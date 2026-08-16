#!/usr/bin/env python3
"""Assemble the publishable site directory.

The output is a small, versioned API of two static files:

    dist/
      .nojekyll
      index.html
      api/v1/parcels.pmtiles   the tileset, always the latest build
      api/v1/parcels.json      TileJSON 3.0.0 + provenance and statistics

parcels.json follows TileJSON 3.0.0 rather than a bespoke shape, so MapLibre
and other tooling can consume it directly. Provenance and statistics ride along
as extension keys; the spec requires implementations to ignore unknown keys.

The tileset lives at one stable, mutable URL. PMTiles reads an archive
incrementally and does not notice the file being replaced underneath a cached
client (protomaps/PMTiles#326), so consumers that need to detect a new build
should compare provenance.content_sha256.
"""

from __future__ import annotations

import argparse
import json
import shutil
import struct
import sys
from datetime import date
from pathlib import Path

import config


def header_bounds(pmtiles: Path) -> list:
    """Fall back to the bounds recorded in the PMTiles header."""
    with pmtiles.open("rb") as fh:
        raw = fh.read(127)
    if raw[:7] != b"PMTiles":
        raise SystemExit(f"FAIL: {pmtiles} is not a PMTiles archive")
    return [v / 1e7 for v in struct.unpack("<4i", raw[102:118])]


def build_tilejson(stats: dict, contract: dict | None, pmtiles: Path,
                   base_url: str, version: str) -> dict:
    # Prefer the extent the service advertises over the extent we derived, so
    # the document describes the source rather than our rendering of it.
    if contract and contract.get("wgs84_bbox"):
        bounds = list(contract["wgs84_bbox"])
    else:
        bounds = header_bounds(pmtiles)

    min_lon, min_lat, max_lon, max_lat = bounds
    center = [
        round((min_lon + max_lon) / 2, 5),
        round((min_lat + max_lat) / 2, 5),
        config.MIN_ZOOM + 2,
    ]

    tiles_url = (
        f"pmtiles://{base_url}/{config.API_DIR}/{config.API_TILESET_NAME}"
        "/{z}/{x}/{y}"
    )

    return {
        "tilejson": "3.0.0",
        "name": config.TILEJSON_NAME,
        "description": config.TILEJSON_DESCRIPTION,
        "version": version,
        "scheme": "xyz",
        "tiles": [tiles_url],
        "minzoom": config.MIN_ZOOM,
        "maxzoom": config.MAX_ZOOM,
        "bounds": [round(v, 6) for v in bounds],
        "center": center,
        "attribution": config.attribution_text(
            stats.get("source_created"), stats.get("retrieved")
        ),
        "vector_layers": [
            {
                "id": config.LAYER_NAME,
                "description": config.TILEJSON_DESCRIPTION,
                "minzoom": config.MIN_ZOOM,
                "maxzoom": config.MAX_ZOOM,
                "fields": dict(config.FIELD_DESCRIPTIONS),
            }
        ],
        # --- extension keys ---
        "provenance": {
            "source": config.ATTRIBUTION_SOURCE,
            "dataset": config.ATTRIBUTION_DATASET,
            "source_url": config.WFS_URL,
            "source_layer": config.TYPENAME,
            "source_created": stats.get("source_created"),
            "retrieved": stats.get("retrieved"),
            "legal_basis": config.LEGAL_BASIS,
            "content_sha256": stats["content_sha256"],
        },
        "statistics": {
            "features": stats["total"],
            "usufruct": stats["usufruct"],
            "classes": stats["classes"],
            "groups": stats["groups"],
        },
    }


def assert_valid(document: dict) -> None:
    """Fail the build rather than publish a malformed document.

    tilejson and tiles are the only fields TileJSON 3.0.0 requires; the rest of
    these are things this API promises and consumers rely on.
    """
    problems = []
    if document.get("tilejson") != "3.0.0":
        problems.append("tilejson must be '3.0.0'")
    if not document.get("tiles"):
        problems.append("tiles must be a non-empty array")
    layers = document.get("vector_layers") or []
    if not layers:
        problems.append("vector_layers must be a non-empty array")
    elif not layers[0].get("fields"):
        problems.append("vector_layers[0].fields must be present")
    if not document.get("attribution"):
        problems.append("attribution must be present (art. 40c ust. 3 Pgik)")
    if not document.get("provenance", {}).get("content_sha256"):
        problems.append("provenance.content_sha256 must be present")

    statistics = document.get("statistics", {})
    classes = statistics.get("classes", {})
    if classes and sum(classes.values()) != statistics.get("features"):
        problems.append(
            f"statistics.classes sums to {sum(classes.values())}, "
            f"expected {statistics.get('features')}"
        )

    if problems:
        raise SystemExit("FAIL: invalid TileJSON:\n  - " + "\n  - ".join(problems))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmtiles", default=f"build/{config.TILESET_NAME}", type=Path)
    parser.add_argument("--stats", default="build/stats.json", type=Path)
    parser.add_argument("--contract", default="build/source_contract.json", type=Path)
    parser.add_argument("--site", default="site", type=Path)
    parser.add_argument("--out", default="dist", type=Path)
    parser.add_argument("--version", default=date.today().isoformat())
    parser.add_argument(
        "--base-url",
        default=config.SITE_BASE_URL,
        help="origin the TileJSON tiles[] entry points at",
    )
    args = parser.parse_args()

    for path in (args.pmtiles, args.stats):
        if not path.exists():
            raise SystemExit(f"FAIL: {path} not found -- run the build first")

    stats = json.loads(args.stats.read_text(encoding="utf-8"))
    contract = None
    if args.contract.exists():
        contract = json.loads(args.contract.read_text(encoding="utf-8"))

    out = args.out
    # Clear the contents rather than the directory itself. `make serve` holds
    # dist/ as its working directory, and removing the inode leaves the server
    # alive but unable to serve anything -- a confusing failure when you
    # republish while a preview is running.
    out.mkdir(parents=True, exist_ok=True)
    for item in out.iterdir():
        if item.is_dir():
            shutil.rmtree(item)
        else:
            item.unlink()

    # Directories too, not just files: the glyph PBFs the label layer needs
    # live in site/glyphs/<font stack>/. A file-only copy publishes a style
    # that references them and a site that does not carry them, and the only
    # symptom is parcel numbers silently failing to render.
    for item in args.site.iterdir():
        if item.is_dir():
            shutil.copytree(item, out / item.name)
        else:
            shutil.copy2(item, out / item.name)

    api_dir = out / config.API_DIR
    api_dir.mkdir(parents=True)
    shutil.copy2(args.pmtiles, api_dir / config.API_TILESET_NAME)

    document = build_tilejson(
        stats, contract, args.pmtiles, args.base_url.rstrip("/"), args.version
    )
    assert_valid(document)
    (api_dir / config.API_METADATA_NAME).write_text(
        json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Pages would otherwise hand the whole tree to Jekyll, which drops paths
    # beginning with an underscore and needlessly slows publication.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    size_mb = (api_dir / config.API_TILESET_NAME).stat().st_size / 1048576
    print(f"  {config.API_DIR}/{config.API_TILESET_NAME}  ({size_mb:.2f} MB)")
    print(f"  {config.API_DIR}/{config.API_METADATA_NAME}  "
          f"(TileJSON 3.0.0, {stats['total']} features)")
    print(f"  version {args.version}, content {stats['content_sha256'][:8]}")
    print(f"  tiles: {document['tiles'][0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
