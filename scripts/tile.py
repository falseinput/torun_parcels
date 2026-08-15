#!/usr/bin/env python3
"""Build the PMTiles archive with tippecanoe.

Tippecanoe's tile grid is always EPSG:3857 -- the --projection flag declares
the *input* CRS, and ours is already EPSG:4326 (its default), so it must not
be set. Passing --projection=EPSG:3857 here would make tippecanoe read lon/lat
degrees as Mercator metres and collapse the city to a point near Null Island.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

import config


def build(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "tippecanoe",
        "-o", str(destination),
        "-f",
        "-l", config.LAYER_NAME,
        "-Z", str(config.MIN_ZOOM),
        "-z", str(config.MAX_ZOOM),
        # detail 13 -> extent 8192 -> 4.49 cm quantisation at z16 / lat 53,
        # matching maxzoom 17 precision at a fraction of the size.
        "-d", str(config.DETAIL),
        # Cadastre: never silently discard parcels to meet a size budget.
        "--no-feature-limit",
        "--no-tile-size-limit",
        # Keep adjacent parcels sharing exact borders -- otherwise
        # simplification opens visible slivers between neighbours.
        "--no-simplification-of-shared-nodes",
        "--detect-shared-borders",
        str(source),
    ]
    print("tippecanoe ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"FAIL: tippecanoe exited {result.returncode}")

    noise = result.stderr
    for marker in ("dropped", "Try using", "exceeds"):
        if marker in noise:
            for line in noise.splitlines():
                if marker in line:
                    print(f"  WARNING from tippecanoe: {line.strip()}")

    size_mb = destination.stat().st_size / 1048576
    print(f"  wrote {destination} ({size_mb:.2f} MB)")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="build/parcels.geojsonl", type=Path)
    parser.add_argument("--out", default=f"build/{config.TILESET_NAME}", type=Path)
    args = parser.parse_args()

    if not args.src.exists():
        raise SystemExit(f"FAIL: {args.src} not found -- run transform.py first")

    build(args.src, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
