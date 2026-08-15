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

`scripts/gate.py` compares a SHA-256 hash of the parcel content against the
published manifest. The hash covers the identifier, registry group, class,
usufruct flag, parcel number, obręb, and geometry of every parcel, in sorted
order. When the hash matches, the workflow skips tiling, validation, and
deployment.

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
index.html
latest.json                                     Mutable pointer
stats.json
torun/<date>-<hash>/parcels-evidence.pmtiles    Immutable
```

Each build writes the tileset to a new content-addressed path and rewrites
`latest.json` to point at it. The viewer reads `latest.json` with
`cache: "no-store"`.

GitHub Pages sets `cache-control: max-age=600` on every response and does not
allow per-path headers, so `latest.json` can be up to 10 minutes stale.

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
`scripts/publish.py` writes both to the `attribution` object in `latest.json`.
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
