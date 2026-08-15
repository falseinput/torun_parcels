#!/usr/bin/env python3
"""Assemble the publishable site directory.

Tilesets are written to an immutable, content-addressed path and the manifest
is rewritten to point at it. Nothing is ever overwritten in place, so a client
mid-download of the previous build cannot receive a half-written archive.

    dist/
      index.html
      latest.json                              <- small, refetched, mutable
      stats.json
      torun/<version>-<hash8>/parcels-evidence.pmtiles   <- immutable

GitHub Pages pins cache-control to max-age=600 and offers no per-path header
control, so the manifest can be up to 10 minutes stale. That is the accepted
trade for zero-infrastructure hosting; the immutable tileset paths mean a
stale manifest only ever points at a complete, valid older build.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

import config


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pmtiles", default=f"build/{config.TILESET_NAME}", type=Path)
    parser.add_argument("--stats", default="build/stats.json", type=Path)
    parser.add_argument("--site", default="site", type=Path)
    parser.add_argument("--out", default="dist", type=Path)
    parser.add_argument("--version", default=date.today().isoformat())
    args = parser.parse_args()

    for path in (args.pmtiles, args.stats):
        if not path.exists():
            raise SystemExit(f"FAIL: {path} not found -- run the build first")

    stats = json.loads(args.stats.read_text(encoding="utf-8"))
    digest = stats["content_sha256"]
    version_dir = f"{args.version}-{digest[:8]}"

    out = args.out
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    for item in args.site.iterdir():
        if item.is_file():
            shutil.copy2(item, out / item.name)

    tiles_dir = out / "torun" / version_dir
    tiles_dir.mkdir(parents=True)
    shutil.copy2(args.pmtiles, tiles_dir / config.TILESET_NAME)
    relative = f"torun/{version_dir}/{config.TILESET_NAME}"

    shutil.copy2(args.stats, out / "stats.json")

    manifest = {
        "version": args.version,
        "url": relative,
        "features": stats["total"],
        "content_sha256": digest,
        "layer": config.LAYER_NAME,
        "minzoom": config.MIN_ZOOM,
        "maxzoom": config.MAX_ZOOM,
        "source": config.WFS_URL,
        "attribution": {
            "source": config.ATTRIBUTION_SOURCE,
            "dataset": config.ATTRIBUTION_DATASET,
            "created": stats.get("source_created"),
            "retrieved": stats.get("retrieved"),
        },
    }
    (out / "latest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )

    # Pages would otherwise hand the whole tree to Jekyll, which drops paths
    # beginning with an underscore and needlessly slows publication.
    (out / ".nojekyll").write_text("", encoding="utf-8")

    size_mb = (tiles_dir / config.TILESET_NAME).stat().st_size / 1048576
    print(f"  {out}/{relative}  ({size_mb:.2f} MB)")
    print(f"  {out}/latest.json -> {relative}")
    print(f"  version {args.version}, content {digest[:8]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
