# Toruń land ownership tiles

This project converts the Toruń cadastral WFS into a PMTiles vector tileset and
publishes it as a web map. Each parcel carries its ownership category, its
parcel number, and its geometry.

Live map: <https://falseinput.github.io/torun_parcels>

The pipeline downloads 46,059 parcels, reprojects them to WGS84, classifies
their ownership, builds tiles, validates the result, and assembles a site. A
full run takes about 16 seconds.

## Prerequisites

Install the following tools:

| Tool | Purpose | Minimum version |
|---|---|---|
| Python | Runs the pipeline scripts | 3.10 |
| GDAL (`ogr2ogr`) | Reprojects and classifies the data | 3.6 |
| tippecanoe | Builds the tileset | 2.0 |

The scripts import only the Python standard library. You do not need to install
Python packages.

To confirm your environment, run:

```bash
make check-tools
```

## Quick start

1. Build and validate the tileset:

   ```bash
   make all
   ```

2. Serve the result locally:

   ```bash
   make serve
   ```

3. Open <http://localhost:8099>.

Use `make serve` rather than `python -m http.server`. PMTiles reads tiles with
HTTP range requests, and the standard library server does not implement them.
A server without range support returns the whole archive and renders no tiles.

## Make targets

| Target | Action |
|---|---|
| `make all` | Runs fetch, transform, tile, validate, and publish |
| `make fetch` | Checks the service contract and downloads the GML |
| `make transform` | Reprojects to WGS84 and classifies ownership |
| `make tile` | Builds the PMTiles archive |
| `make validate` | Runs the per-build checks |
| `make validate-offline` | Runs the per-build checks without network access |
| `make validate-deep` | Also decodes every tile |
| `make publish` | Assembles `dist/` |
| `make serve` | Serves `dist/` with range support on port 8099 |
| `make clean` | Deletes `build/` and `dist/` |

## Data source

The pipeline reads layer `ms:dzialki` from
`https://mtorun-wms.webewid.pl/iip/ows`.

| Property | Value |
|---|---|
| Parcels | 46,059 |
| Full download | One request, about 5 seconds, 4.5 MB gzipped |
| `DefaultMaxFeatures` | 1,000,000 |
| Output format | `text/xml; subtype=gml/3.1.1` |
| Coordinate reference systems | EPSG:2177 (default), EPSG:2180, EPSG:4326 |

The service returns the whole city in one request, so the pipeline sends no
bounding box and performs no paging.

### Available attributes

The service publishes `ID_DZIALKI`, `NUMER_DZIALKI`, `NAZWA_OBREBU`,
`NUMER_OBREBU`, `NUMER_JEDNOSTKI`, `NAZWA_GMINY`, `GRUPA_REJESTROWA`, and
`DATA`.

The service also declares `POLE_EWIDENCYJNE` (area) and `KLASOUZYTKI_EGIB`
(land use), but both fields are empty on every record. Article 40a ust. 2 pkt 1
of *Prawo geodezyjne i kartograficzne* limits what the service publishes without
a fee. To obtain parcel area, calculate it from the geometry in EPSG:2177.

The service does not publish owner names. `GRUPA_REJESTROWA` identifies an
ownership *category* only.

## API

The build publishes two static endpoints. Both allow cross-origin requests
(`access-control-allow-origin: *`).

Base URL: `https://falseinput.github.io/torun_parcels`

| Method | Path | Returns |
|---|---|---|
| `GET` | `/api/v1/parcels.json` | TileJSON 3.0.0 metadata |
| `GET` | `/api/v1/parcels.pmtiles` | PMTiles v3 archive, ~10 MB |

The `v1` prefix versions the contract. A future change to the response shape
ships as `v2` and leaves `v1` working.

### `GET /api/v1/parcels.json`

Returns a TileJSON 3.0.0 document. Two extension keys carry information the
specification does not define: `provenance` and `statistics`. Clients that do
not recognise them ignore them, as the specification requires.

```bash
curl https://falseinput.github.io/torun_parcels/api/v1/parcels.json
```

```json
{
  "tilejson": "3.0.0",
  "name": "torun-parcels",
  "version": "2026-08-15",
  "tiles": ["pmtiles://https://falseinput.github.io/torun_parcels/api/v1/parcels.pmtiles/{z}/{x}/{y}"],
  "minzoom": 11,
  "maxzoom": 16,
  "bounds": [18.460113, 52.962039, 18.742612, 53.066788],
  "center": [18.60136, 53.01441, 13],
  "attribution": "Źródło: Państwowy zasób geodezyjny i kartograficzny — Prezydent Miasta Torunia. Ewidencja gruntów i budynków (EGiB). Stan na 2026-08-14 23:44:23. Pobrano 2026-08-15.",
  "vector_layers": [{ "id": "parcels", "fields": { "id": "…", "nr": "…" } }],
  "provenance": {
    "source": "Państwowy zasób geodezyjny i kartograficzny — Prezydent Miasta Torunia",
    "dataset": "Ewidencja gruntów i budynków (EGiB)",
    "source_url": "https://mtorun-wms.webewid.pl/iip/ows",
    "source_created": "2026-08-14 23:44:23",
    "retrieved": "2026-08-15",
    "legal_basis": "art. 40c ust. 3 Prawo geodezyjne i kartograficzne",
    "content_sha256": "f9cfafa731f16eba…"
  },
  "statistics": {
    "features": 46059,
    "usufruct": 3830,
    "classes": { "osoba_fizyczna": 20780, "gmina": 14930 },
    "groups": { "1": 2929, "2": 2498 }
  }
}
```

| Field | Meaning |
|---|---|
| `version` | Build date |
| `attribution` | Ready-to-display credit line. Display it — see [Attribution](#attribution) |
| `provenance.source_created` | When the source generated its export |
| `provenance.retrieved` | When this build downloaded the data |
| `provenance.content_sha256` | Hash of the parcel content. Compare it to detect a new build |
| `statistics.classes` | Parcel count per ownership class |
| `statistics.groups` | Parcel count per EGiB registry group |

### `GET /api/v1/parcels.pmtiles`

Returns the tileset: PMTiles v3, MVT tiles, gzip, zoom 11 to 16. The archive is
read with HTTP range requests, so the client needs a server that supports them.

The URL is stable and always serves the latest build.

### Using the API with MapLibre

Register the PMTiles protocol, then point the source at the **metadata**
endpoint. MapLibre reads `tiles` from the document and fetches each tile
through the protocol, so attribution, bounds and zoom range come from the API
rather than being hardcoded in your client.

```js
const protocol = new pmtiles.Protocol();
maplibregl.addProtocol("pmtiles", protocol.tile);

map.on("load", () => {
  map.addSource("parcels", {
    type: "vector",
    url: "https://falseinput.github.io/torun_parcels/api/v1/parcels.json",
  });
  map.addLayer({
    id: "parcels-fill",
    type: "fill",
    source: "parcels",
    "source-layer": "parcels",
    paint: {
      "fill-color": [
        "match", ["get", "klasa"],
        ["skarb_panstwa", "gmina", "wojewodztwo_powiat"], "#2a78d6",
        ["osoba_fizyczna"], "#eb6834",
        "#1baf7a",
      ],
      "fill-opacity": 0.7,
    },
  });
});
```

To read the archive directly instead, pass a `pmtiles://` URL:

```js
map.addSource("parcels", {
  type: "vector",
  url: "pmtiles://https://falseinput.github.io/torun_parcels/api/v1/parcels.pmtiles",
});
```

### Caching

GitHub Pages sets `cache-control: max-age=600` on every response and offers no
per-path control, so a response may be up to 10 minutes stale.

The tileset URL is stable, which means its content changes when a build
publishes. PMTiles reads an archive incrementally and does not detect the file
being replaced underneath a cached client
([protomaps/PMTiles#326](https://github.com/protomaps/PMTiles/issues/326)). A
client holding a partially cached archive across a deploy may therefore need a
hard reload. Builds are weekly and skipped when nothing changed, so the window
is small.

To detect a new build, poll `/api/v1/parcels.json` and compare
`provenance.content_sha256`.

## Coordinate reference systems

The service returns EPSG:2177 with **northing, easting** axis order. MapLibre
renders only the Web Mercator tile grid. The pipeline therefore reprojects the
data as follows:

```
EPSG:2177 (northing, easting)   Source GML
  -oo SWAP_COORDINATES=YES
EPSG:2177 (easting, northing)
  -t_srs EPSG:4326
WGS84 lon/lat                   tippecanoe input
  tippecanoe
EPSG:3857 tile grid             PMTiles output
```

Observe two constraints when you modify the pipeline:

* **Pass `SWAP_COORDINATES=YES` explicitly.** The values `AUTO` and `NO` both
  produce a valid archive that renders about 900 km from Toruń.
* **Do not pass `--projection` to tippecanoe.** That flag declares the *input*
  projection. The input is EPSG:4326, which is already the default.

Do not request `srsName=EPSG:4326` from the service to skip the reprojection.
The service returns latitude first, so you still need the swap, and it rounds
coordinates to six decimal places. That rounding introduces up to 5.55 cm of
latitude error, which exceeds the 4.49 cm tile quantisation.

## Ownership classification

*Rozporządzenie Ministra Rozwoju, Pracy i Technologii* of 27 July 2021
(Dz.U. 2021 poz. 1390), § 14 and Załącznik nr 2, defines 16 registry groups.
`scripts/config.py` maps those groups to seven classes and generates the
`ogr2ogr` `CASE` expression from that map.

Edit `CLASS_MAP` in `scripts/config.py` to change the classification. Do not
edit the SQL, because the pipeline generates it.

| Class | Registry groups | Parcels | Share |
|---|---|---|---|
| `osoba_fizyczna` | 7 | 20,780 | 45.12% |
| `gmina` | 4, 5, 6 | 14,930 | 32.41% |
| `skarb_panstwa` | 1, 2, 3 | 5,441 | 11.81% |
| `spolka_handlowa` | 15 | 1,813 | 3.94% |
| `pozostale` | 9, 10, 16 | 1,100 | 2.39% |
| `spoldzielnia` | 8 | 1,070 | 2.32% |
| `wojewodztwo_powiat` | 11, 12, 13, 14 | 925 | 2.01% |

Groups 2, 5, 12, and 14 identify parcels that a public body owns and a
*użytkownik wieczysty* controls. The tileset records these parcels in the `uw`
field rather than in a separate class. Toruń contains 3,830 such parcels
(8.32%).

Groups 10 and 12 do not occur in Toruń. The map still covers them.

## Tile schema

Layer `parcels`, zoom 11 to 16, 10.21 MB.

```json
{"id":"046301_1.0063.277/2","nr":"277/2","obreb":"0063",
 "grupa":4,"klasa":"gmina","uw":0}
```

| Field | Type | Description |
|---|---|---|
| `id` | string | Full parcel identifier |
| `nr` | string | Parcel number within the obręb |
| `obreb` | string | Obręb (cadastral district) number |
| `grupa` | integer | Registry group, 1 to 16 |
| `klasa` | string | Ownership class from the table above |
| `uw` | integer | `1` if a *użytkownik wieczysty* holds the parcel |

`grupa` and `uw` are integers. A MapLibre `match` expression that compares them
against numbers fails silently if the tileset stores them as strings.

At zoom 16 the tiles use extent 8192, which quantises coordinates to 4.49 cm.
Lower zoom levels use extent 4096.

## Map colours

The map colours parcels by ownership. All classes appear on screen at once, so
the palette must separate every pair of colours, not just adjacent ones. Seven
simultaneous hues cannot meet that requirement, so the map works in two modes:

* **Default view.** Three colours group the classes into public ownership,
  natural persons, and other entities.
* **Isolated view.** Selecting a class in the legend draws that class in one
  colour against a neutral background.

The popup names the registry group and the ownership class in text, so the map
never conveys identity through colour alone.

## Validation

`make validate` runs nine checks in about four seconds. Each check tests
something that can change between runs: the source data, or the contract the
service advertises.

| Group | Checks |
|---|---|
| Source data | Parcel count within ±2%, unique identifiers, no null geometries |
| Ownership | Every registry group present in the data appears in `CLASS_MAP` |
| Axis order | Refetches 400 features as EPSG:4326 and compares them to the local reprojection |
| Header | Archive format, zoom range, and bounds against the advertised extent |

`scripts/fetch.py` reads `DefaultSRS`, the output format, and
`WGS84BoundingBox` from `GetCapabilities`, then writes them to
`build/source_contract.json`. The header check compares the tileset bounds
against that advertised extent.

The axis check is the most important one. The service and GDAL can each change
axis convention, and such a change produces a valid archive that renders in the
wrong place. Rounding alone accounts for about 5.55 cm of difference, so the
check fails above 6 cm.

### Deep validation

`make validate-deep` adds seven checks that decode all 1,351 tiles, which takes
about six seconds. These checks verify the tile extent per zoom level, the layer
name, attribute presence, attribute types, vertex bounds, that no parcel
disappears at maximum zoom, and that the generated SQL matches `CLASS_MAP`.

Run deep validation after you upgrade tippecanoe or GDAL, or after you change
the tiling configuration in `scripts/config.py`. The per-build checks do not
depend on `scripts/pmtiles_reader.py`.

## Change detection

`scripts/gate.py` compares a SHA-256 hash of the parcel content against
`provenance.content_sha256` in the published `/api/v1/parcels.json`. The hash
covers the identifier, registry group, class, usufruct flag, parcel number,
obręb, and geometry of every parcel, in sorted order. When the hash matches,
the workflow skips tiling, validation, and deployment.

The hash excludes the `DATA` field. The service writes an identical `DATA`
timestamp to every record and updates it nightly, whether or not any parcel
changed, so `DATA` cannot indicate a content change.

## Deployment

The `build` workflow runs weekly and on manual dispatch. It publishes to GitHub
Pages when the content hash changes.

To enable deployment, set the Pages source to **GitHub Actions** in the
repository settings.

The workflow publishes this structure:

```
index.html                  Viewer
api/v1/parcels.json         TileJSON metadata
api/v1/parcels.pmtiles      Tileset
```

Each build overwrites both endpoints in place, so their URLs never change. See
[Caching](#caching) for what that means for clients.

The viewer reads `api/v1/parcels.json` with `cache: "no-store"` and resolves
the tileset against its own origin, so `make serve` previews the local build
rather than following the absolute production URL in `tiles`.

## Attribution

Article 40c ust. 3 of *Prawo geodezyjne i kartograficzne* requires anyone who
uses material from the *państwowy zasób geodezyjny i kartograficzny* to state
the source of that material in published work. The *Ustawa o otwartych danych i
ponownym wykorzystywaniu informacji sektora publicznego* of 11 August 2021
additionally permits the provider to require the time of creation and the time
of acquisition.

If you republish this data, include the following:

> Źródło: Państwowy zasób geodezyjny i kartograficzny — Prezydent Miasta
> Torunia. Ewidencja gruntów i budynków (EGiB). Stan na *<data>*, pobrano
> *<data>*.

The pipeline records these values automatically. `scripts/transform.py` reads
the source timestamp from the GML and the retrieval date from the build, and
`scripts/publish.py` writes both to `/api/v1/parcels.json`, as the TileJSON
`attribution` string and as `provenance.source_created` / `provenance.retrieved`.
The viewer displays them in the legend panel and in the map attribution control.

Article 40a ust. 3 states that the authority issues no licence for material
covered by article 40a ust. 2 pkt 1, so this data carries an attribution
requirement rather than a licence document.

## Licence

The source code in this repository is available under the MIT Licence. See
[LICENSE](LICENSE).

The licence covers the code only. It does not cover the parcel data or the
tilesets built from it. See [Attribution](#attribution).

## Repository layout

```
scripts/
  config.py           Classification, thresholds, and tiling parameters
  fetch.py            Contract checks and download
  transform.py        Reprojection, classification, and content hash
  tile.py             tippecanoe wrapper
  validate.py         Per-build checks, plus --deep
  gate.py             Change detection
  publish.py          Site assembly
  serve.py            Local server with range support
  pmtiles_reader.py   PMTiles and MVT decoder, used only by --deep
  http_util.py        HTTP helper with gzip and retries
site/index.html       MapLibre viewer
```

## Troubleshooting

**The map renders no tiles, and the browser downloads the whole archive.**
Your server does not support HTTP range requests. Use `make serve`.

**Parcels appear in the North Sea.**
The reprojection lost the axis swap. Confirm that `scripts/transform.py` passes
`-oo SWAP_COORDINATES=YES`.

**Validation reports an unmapped registry group.**
The source introduced a group that `CLASS_MAP` does not list. Add it to
`scripts/config.py`. Until you do, the pipeline assigns it to `pozostale`.

**Validation reports a parcel count outside tolerance.**
Compare the reported count against the source. If the change is genuine, update
`EXPECTED_FEATURES` in `scripts/config.py`.
