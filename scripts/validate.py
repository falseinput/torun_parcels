#!/usr/bin/env python3
"""CI guards for the Toruń parcels tileset.

Scope is deliberately narrow. A check earns a place in the default run only if
it tests something that can actually *vary between runs* -- the source data, or
the source service's advertised contract. Checks that only re-test our own
pinned tooling (that tippecanoe emits gzipped MVT, that the SQL we generate
matches the mapping we generated it from) are one-off acceptance tests, not
scheduled-build guards; they live behind --deep.

Expectations come from the service's own GetCapabilities where possible
(captured by fetch.py to build/source_contract.json), so they follow the source
if it legitimately changes instead of drifting against frozen constants.

Exit code is non-zero if any check fails.
"""

from __future__ import annotations

import argparse
import json
import struct
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path

import config
from http_util import build_url, get

FAILURES: list[str] = []
PASSES: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> bool:
    if ok:
        PASSES.append(name)
        print(f"  PASS  {name}" + (f" -- {detail}" if detail else ""))
    else:
        FAILURES.append(f"{name}: {detail}")
        print(f"  FAIL  {name} -- {detail}")
    return ok


def load_features(path: Path) -> list:
    features = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                features.append(json.loads(line))
    return features


# --- 1. source data ---------------------------------------------------------


def check_source_data(features: list) -> None:
    """Varies every run: the source can gain, lose or corrupt parcels."""
    print("\n[1] Source data")
    total = len(features)

    low = config.EXPECTED_FEATURES * (1 - config.COUNT_TOLERANCE)
    high = config.EXPECTED_FEATURES * (1 + config.COUNT_TOLERANCE)
    check(
        "feature count within tolerance",
        low <= total <= high,
        f"{total} (expected {config.EXPECTED_FEATURES} "
        f"+/-{config.COUNT_TOLERANCE:.0%}; a truncated GML fails low)",
    )

    ids = [f["properties"]["id"] for f in features]
    check("parcel ids unique", len(set(ids)) == total, f"{len(set(ids))}/{total}")

    null_geom = sum(1 for f in features if not f.get("geometry"))
    check("no null geometries", null_geom == 0, f"{null_geom} null")


# --- 2. classification ------------------------------------------------------


def check_classification(features: list) -> None:
    """Varies with the source: a new registry group would appear here first."""
    print("\n[2] Ownership classification")
    known = config.all_mapped_groups()
    observed = Counter(f["properties"]["grupa"] for f in features)

    unexpected = sorted(set(observed) - known)
    check(
        "every registry group is mapped",
        not unexpected,
        f"unmapped groups {unexpected} fell silently into "
        f"'{config.FALLBACK_CLASS}'"
        if unexpected
        else f"groups {sorted(observed)} all in 1-16",
    )

    class_counts = Counter(f["properties"]["klasa"] for f in features)
    print("        distribution:")
    for klasa in config.CLASS_MAP:
        n = class_counts.get(klasa, 0)
        print(f"          {klasa:20s} {n:6d}  {n / len(features) * 100:5.2f}%")


# --- 3. axis order, cross-checked against the server ------------------------


def check_axis_order() -> None:
    """The single highest-value guard.

    A silent axis flip yields a valid, correctly sized archive that renders
    ~900 km away. GDAL could change its axis-mapping defaults, or the service
    could flip convention on an upgrade; either shows up here as deltas beyond
    pure rounding.
    """
    print("\n[3] Axis order vs. server-side EPSG:4326")
    params = {
        "service": "WFS",
        "version": config.WFS_VERSION,
        "request": "GetFeature",
        "typeName": config.TYPENAME,
        "maxFeatures": str(config.AXIS_CHECK_FEATURES),
    }

    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        native = tmp / "native.gml"
        server = tmp / "server.gml"
        native.write_bytes(get(build_url(config.WFS_URL, params), timeout=120))
        server.write_bytes(
            get(
                build_url(config.WFS_URL, {**params, "srsName": config.CHECK_CRS}),
                timeout=120,
            )
        )

        local_out = tmp / "local.geojsonl"
        server_out = tmp / "server.geojsonl"
        subprocess.run(
            ["ogr2ogr", "-f", "GeoJSONSeq", str(local_out), str(native),
             "-oo", "SWAP_COORDINATES=YES", "-t_srs", "EPSG:4326",
             "-lco", "RS=NO", "-lco", "COORDINATE_PRECISION=9", "-nlt", "POLYGON"],
            capture_output=True, check=True,
        )
        subprocess.run(
            ["ogr2ogr", "-f", "GeoJSONSeq", str(server_out), str(server),
             "-oo", "SWAP_COORDINATES=YES",
             "-lco", "RS=NO", "-lco", "COORDINATE_PRECISION=9", "-nlt", "POLYGON"],
            capture_output=True, check=True,
        )

        def load(path):
            out = {}
            with path.open(encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        feature = json.loads(line)
                        out[feature["properties"]["ID_DZIALKI"]] = feature["geometry"]
            return out

        ours, theirs = load(local_out), load(server_out)

    shared = set(ours) & set(theirs)
    if not check("axis check fetched comparable features", len(shared) > 50,
                 f"{len(shared)} shared ids"):
        return

    max_lon_cm = max_lat_cm = 0.0
    for parcel_id in shared:
        for ring_a, ring_b in zip(
            ours[parcel_id]["coordinates"], theirs[parcel_id]["coordinates"]
        ):
            for (x1, y1), (x2, y2) in zip(ring_a, ring_b):
                max_lon_cm = max(max_lon_cm, abs(x1 - x2) * 66990 * 100)
                max_lat_cm = max(max_lat_cm, abs(y1 - y2) * 111320 * 100)

    worst = max(max_lon_cm, max_lat_cm)
    check(
        "reprojection agrees with server EPSG:4326",
        worst < config.AXIS_CHECK_MAX_DELTA_CM,
        f"max delta {worst:.2f} cm (lon {max_lon_cm:.2f}, lat {max_lat_cm:.2f}); "
        f"limit {config.AXIS_CHECK_MAX_DELTA_CM} cm -- ~5.6 cm is pure rounding",
    )


# --- 4. PMTiles header ------------------------------------------------------
#
# 127 bytes and a struct.unpack. This stays in the default run because the
# bounds are derived from the data, so they move if the geometry moves.


def read_pmtiles_header(path: Path) -> dict:
    with path.open("rb") as fh:
        raw = fh.read(127)
    if raw[:7] != b"PMTiles":
        raise SystemExit(f"FAIL: {path} is not a PMTiles archive")
    bbox = struct.unpack("<4i", raw[102:118])
    return {
        "spec_version": raw[7],
        "tile_compression": raw[98],
        "tile_type": raw[99],
        "min_zoom": raw[100],
        "max_zoom": raw[101],
        "bounds": tuple(v / 1e7 for v in bbox),
    }


def check_header(path: Path, contract: dict | None) -> None:
    print("\n[4] PMTiles header")
    header = read_pmtiles_header(path)

    check(
        "archive is MVT/gzip, spec v3",
        header["spec_version"] == 3
        and header["tile_type"] == 1
        and header["tile_compression"] == 2,
        f"v{header['spec_version']}, type={header['tile_type']}, "
        f"compression={header['tile_compression']}",
    )
    check(
        "zoom range as configured",
        header["min_zoom"] == config.MIN_ZOOM and header["max_zoom"] == config.MAX_ZOOM,
        f"z{header['min_zoom']}-{header['max_zoom']}",
    )

    min_lon, min_lat, max_lon, max_lat = header["bounds"]
    if contract and contract.get("wgs84_bbox"):
        bx0, by0, bx1, by1 = contract["wgs84_bbox"]
        source = "advertised WGS84BoundingBox"
        pad = config.BBOX_PAD
    else:
        bx0, by0, bx1, by1 = config.SANITY_BBOX
        source = "sanity envelope (no contract captured)"
        pad = 0.0

    inside = (
        bx0 - pad <= min_lon <= max_lon <= bx1 + pad
        and by0 - pad <= min_lat <= max_lat <= by1 + pad
    )
    check(
        "tileset bounds match the source's advertised extent",
        inside,
        f"[{min_lon:.5f},{min_lat:.5f},{max_lon:.5f},{max_lat:.5f}] vs {source} "
        f"[{bx0:.5f},{by0:.5f},{bx1:.5f},{by1:.5f}] +/-{pad}",
    )


# --- 5. deep tile inspection (opt-in) ---------------------------------------


def check_deep(pmtiles: Path, features: list, contract: dict | None) -> None:
    """Decode every tile and inspect its contents.

    Not in the default run: with --no-feature-limit / --no-tile-size-limit and
    a pinned tippecanoe, these outcomes are fixed by our own flags rather than
    by the data. Worth running when tippecanoe, GDAL or the tiling config
    changes -- which is exactly when they can start being wrong.
    """
    print("\n[5] Deep tile inspection")
    from pmtiles_reader import (
        PMTiles,
        decode_mvt,
        tile_coord_to_lonlat,
        tile_id_to_zxy,
    )

    # The generated CASE expression vs. the Python mapping it was generated
    # from -- guards an edit to config.CLASS_MAP, not the data.
    mismatches = [
        f["properties"]["id"]
        for f in features
        if f["properties"]["klasa"] != config.group_to_class(f["properties"]["grupa"])
    ]
    check(
        "generated SQL matches config.CLASS_MAP",
        not mismatches,
        f"{len(mismatches)} mismatched, e.g. {mismatches[:3]}"
        if mismatches
        else f"all {len(features)} features agree",
    )

    if contract and contract.get("wgs84_bbox"):
        bx0, by0, bx1, by1 = contract["wgs84_bbox"]
    else:
        bx0, by0, bx1, by1 = config.SANITY_BBOX
    pad = config.BBOX_PAD

    archive = PMTiles(pmtiles)
    entries = list(archive.entries())

    out_of_bounds = 0
    bad_extent = set()
    bad_layer = set()
    bad_types = 0
    missing_props = 0
    ids_at_maxzoom: set = set()
    features_seen = 0
    required = ("id", "nr", "obreb", "grupa", "klasa", "uw")

    for entry in entries:
        z, x, y = tile_id_to_zxy(entry["tile_id"])
        for layer in decode_mvt(archive.tile_bytes(entry)):
            if layer["name"] != config.LAYER_NAME:
                bad_layer.add(layer["name"])
            expected_extent = (
                config.TILE_EXTENT if z == config.MAX_ZOOM else config.LOW_TILE_EXTENT
            )
            if layer["extent"] != expected_extent:
                bad_extent.add((z, layer["extent"], expected_extent))

            for feature in layer["features"]:
                features_seen += 1
                props = feature["properties"]
                if any(key not in props for key in required):
                    missing_props += 1
                elif not all(
                    isinstance(props[k], int) and not isinstance(props[k], bool)
                    for k in ("grupa", "uw")
                ):
                    bad_types += 1

                if z == config.MAX_ZOOM and "id" in props:
                    ids_at_maxzoom.add(props["id"])

                for ring in feature["rings"]:
                    for px, py in ring:
                        lon, lat = tile_coord_to_lonlat(
                            px, py, z, x, y, layer["extent"]
                        )
                        if not (
                            bx0 - pad <= lon <= bx1 + pad
                            and by0 - pad <= lat <= by1 + pad
                        ):
                            out_of_bounds += 1

    check("layer name correct in every tile", not bad_layer,
          f"unexpected {sorted(bad_layer)}" if bad_layer else config.LAYER_NAME)
    check("tile extent per zoom as configured", not bad_extent,
          f"(zoom, got, want) {sorted(bad_extent)}" if bad_extent
          else f"z{config.MAX_ZOOM}={config.TILE_EXTENT}, "
               f"below={config.LOW_TILE_EXTENT}")
    check("all required attributes present", missing_props == 0,
          f"{missing_props} features missing attributes")
    check("grupa/uw are integers in tiles", bad_types == 0,
          f"{bad_types} wrong -- a MapLibre match on a string never fires")
    check("every decoded vertex inside the source extent", out_of_bounds == 0,
          f"{out_of_bounds} vertices outside")

    source_ids = {f["properties"]["id"] for f in features}
    missing = source_ids - ids_at_maxzoom
    check("no parcels dropped at max zoom", not missing,
          f"{len(missing)} missing, e.g. {sorted(missing)[:3]}" if missing
          else f"all {len(source_ids)} present at z{config.MAX_ZOOM}")

    print(f"        decoded {len(entries)} tiles, {features_seen} feature instances")


# --- entry point ------------------------------------------------------------


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geojsonl", default="build/parcels.geojsonl", type=Path)
    parser.add_argument("--pmtiles", default=f"build/{config.TILESET_NAME}", type=Path)
    parser.add_argument("--contract", default="build/source_contract.json", type=Path)
    parser.add_argument(
        "--skip-network", action="store_true",
        help="skip the server axis cross-check (offline runs)",
    )
    parser.add_argument(
        "--deep", action="store_true",
        help="also decode every tile (run when tooling or tiling config changes)",
    )
    args = parser.parse_args()

    for path in (args.geojsonl, args.pmtiles):
        if not path.exists():
            raise SystemExit(f"FAIL: {path} not found -- run the build first")

    contract = None
    if args.contract.exists():
        contract = json.loads(args.contract.read_text(encoding="utf-8"))

    print("=" * 68)
    print("Validating Toruń parcels tileset")
    print("=" * 68)

    features = load_features(args.geojsonl)
    check_source_data(features)
    check_classification(features)

    if args.skip_network:
        print("\n[3] Axis order vs. server-side EPSG:4326 -- SKIPPED")
    else:
        check_axis_order()

    check_header(args.pmtiles, contract)

    if args.deep:
        check_deep(args.pmtiles, features, contract)

    print("\n" + "=" * 68)
    if FAILURES:
        print(f"FAILED -- {len(FAILURES)} of {len(FAILURES) + len(PASSES)} checks")
        for failure in FAILURES:
            print(f"  * {failure}")
        return 1
    print(f"OK -- all {len(PASSES)} checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
