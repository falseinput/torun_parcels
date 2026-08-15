#!/usr/bin/env python3
"""Reproject GML to WGS84 GeoJSONSeq and classify ownership.

The projection chain is the highest-risk part of this pipeline:

    EPSG:2177 (Northing,Easting)   <- what the WFS emits
      -oo SWAP_COORDINATES=YES     <- MUST be explicit; AUTO and NO are wrong
    EPSG:2177 (Easting,Northing)
      -t_srs EPSG:4326
    WGS84 lon/lat                  <- what tippecanoe expects (its default)

Getting the swap wrong produces a perfectly valid file that renders in the
North Sea off Norway, ~900 km from Toruń, so validate.py checks the result
against the server's own EPSG:4326 output rather than trusting this step.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import config


def run_ogr2ogr(source: Path, destination: Path) -> None:
    sql = config.build_classification_sql()
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        destination.unlink()

    cmd = [
        "ogr2ogr",
        "-f", "GeoJSONSeq",
        str(destination),
        str(source),
        # The GML declares EPSG:2177, whose authority axis order is
        # (Northing, Easting). GDAL's AUTO heuristic does not detect this.
        "-oo", "SWAP_COORDINATES=YES",
        "-t_srs", "EPSG:4326",
        "-lco", "RS=NO",
        "-nlt", "POLYGON",
        # CASE requires the SQLite dialect; the default OGR dialect lacks it.
        "-dialect", "SQLITE",
        "-sql", sql,
    ]
    print("ogr2ogr (reproject + classify) ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
        raise SystemExit(f"FAIL: ogr2ogr exited {result.returncode}")
    if result.stderr.strip():
        print(f"  ogr2ogr: {result.stderr.strip()}")


def content_hash(path: Path) -> tuple[str, int]:
    """Stable hash of parcel content, order-independent.

    Deliberately excludes the source DATA attribute: it is a single export-run
    timestamp identical across all records, so it changes every night whether
    or not any parcel actually changed. Hashing it would defeat the gate.
    """
    rows = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            feature = json.loads(line)
            props = feature["properties"]
            geometry = json.dumps(
                feature["geometry"], sort_keys=True, separators=(",", ":")
            )
            rows.append(
                f"{props['id']}|{props['grupa']}|{props['klasa']}|"
                f"{props['uw']}|{props['nr']}|{props['obreb']}|{geometry}"
            )
    rows.sort()
    digest = hashlib.sha256("\n".join(rows).encode("utf-8")).hexdigest()
    return digest, len(rows)


def source_timestamp(gml: Path) -> str | None:
    """Read the export timestamp the source stamps on every record.

    The value is identical across all features, so it records when the source
    generated the export, not when any parcel changed. That makes it useless
    for change detection but exactly right for the "time of creation" that the
    Open Data Act lets the provider require in attribution.
    """
    pattern = re.compile(rb"<ms:DATA>([^<]+)</ms:DATA>")
    with gml.open("rb") as fh:
        for _ in range(200_000):
            line = fh.readline()
            if not line:
                break
            match = pattern.search(line)
            if match:
                return match.group(1).decode("utf-8").strip()
    return None


def summarise(path: Path) -> dict:
    from collections import Counter

    classes = Counter()
    groups = Counter()
    usufruct = 0
    total = 0
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            props = json.loads(line)["properties"]
            classes[props["klasa"]] += 1
            groups[props["grupa"]] += 1
            usufruct += props["uw"]
            total += 1
    return {
        "total": total,
        "classes": dict(classes),
        "groups": {str(k): v for k, v in sorted(groups.items())},
        "usufruct": usufruct,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", default="build/dzialki.gml", type=Path)
    parser.add_argument("--out", default="build/parcels.geojsonl", type=Path)
    parser.add_argument("--stats", default="build/stats.json", type=Path)
    args = parser.parse_args()

    if not args.src.exists():
        raise SystemExit(f"FAIL: {args.src} not found -- run fetch.py first")

    run_ogr2ogr(args.src, args.out)

    digest, count = content_hash(args.out)
    stats = summarise(args.out)
    stats["content_sha256"] = digest
    # Attribution metadata required by art. 40c ust. 3 Pgik and the Open Data Act.
    stats["source_created"] = source_timestamp(args.src)
    stats["retrieved"] = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    args.stats.write_text(json.dumps(stats, indent=2, ensure_ascii=False) + "\n")

    print(f"  {count} features -> {args.out}")
    print(f"  content sha256: {digest}")
    for klasa in config.CLASS_MAP:
        n = stats["classes"].get(klasa, 0)
        print(f"    {klasa:20s} {n:6d}  {n / count * 100:5.2f}%")
    print(f"    {'uw=1':20s} {stats['usufruct']:6d}  "
          f"{stats['usufruct'] / count * 100:5.2f}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
