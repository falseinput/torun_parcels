#!/usr/bin/env python3
"""Decide whether anything actually changed since the published build.

The source re-exports nightly -- every record carries an identical DATA
timestamp that moves whether or not a single parcel changed -- so DATA is
useless as a change signal. We compare a content hash instead (see
transform.content_hash), which ignores DATA entirely.

Prints "changed" or "unchanged" and, under GitHub Actions, writes
`changed=true|false` to $GITHUB_OUTPUT.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
from pathlib import Path

from http_util import get_text


def load_published(source: str) -> dict | None:
    if not source:
        return None
    try:
        if source.startswith(("http://", "https://")):
            return json.loads(get_text(source, timeout=60))
        path = Path(source)
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except urllib.error.HTTPError as exc:
        # The expected state on the very first build: Pages has published
        # nothing yet, so there is no manifest to compare against.
        if exc.code == 404:
            print("  no manifest published yet (HTTP 404)")
        else:
            print(f"  could not read published manifest (HTTP {exc.code})")
    # http_util.get re-raises exhausted retries as RuntimeError, so catch that
    # too or a transient network fault aborts the build instead of rebuilding.
    except (urllib.error.URLError, OSError, RuntimeError) as exc:
        print(f"  could not read published manifest ({exc})")
    except json.JSONDecodeError as exc:
        print(f"  published manifest is not valid JSON ({exc})")
    return None


def emit(changed: bool, reason: str) -> int:
    print(f"{'changed' if changed else 'unchanged'}: {reason}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as fh:
            fh.write(f"changed={'true' if changed else 'false'}\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stats", default="build/stats.json", type=Path)
    parser.add_argument(
        "--published",
        default="",
        help="URL or path of the currently published latest.json",
    )
    args = parser.parse_args()

    if not args.stats.exists():
        raise SystemExit(f"FAIL: {args.stats} not found -- run transform.py first")

    current = json.loads(args.stats.read_text(encoding="utf-8"))
    digest = current.get("content_sha256")
    if not digest:
        raise SystemExit("FAIL: stats.json has no content_sha256")

    published = load_published(args.published)
    if published is None:
        return emit(True, "no published manifest to compare against")

    previous = published.get("content_sha256")
    if previous != digest:
        return emit(True, f"content hash {previous} -> {digest}")
    return emit(False, f"content hash unchanged ({digest})")


if __name__ == "__main__":
    sys.exit(main())
