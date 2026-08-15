#!/usr/bin/env python3
"""Validate the Toruń WFS and download the full parcel layer.

The whole city is 46k parcels and comes back in one ~5 s request, so there is
no BBOX grid and no paging here -- a grid would only add a dedupe stage for
parcels straddling cell boundaries.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import config
from http_util import build_url, get, get_text


def check_capabilities(contract_path: Path) -> dict:
    """Assert the advertised contract and capture it for downstream checks.

    Everything here comes from the service's own GetCapabilities, so the
    expectations follow the source if it legitimately changes, instead of
    drifting against constants frozen when this was first written.
    """
    url = build_url(
        config.WFS_URL,
        {
            "service": "WFS",
            "version": config.WFS_VERSION,
            "request": "GetCapabilities",
        },
    )
    print("GetCapabilities ...")
    xml = get_text(url, timeout=60)

    if "<wfs:WFS_Capabilities" not in xml and "<WFS_Capabilities" not in xml:
        raise SystemExit("FAIL: GetCapabilities did not return a WFS capabilities doc")

    names = re.findall(r"<Name>(.*?)</Name>", xml)
    if config.TYPENAME not in names:
        raise SystemExit(
            f"FAIL: layer {config.TYPENAME} missing from capabilities. Found: {names}"
        )

    block = re.search(
        r"<FeatureType>(?:(?!</FeatureType>).)*?<Name>"
        + re.escape(config.TYPENAME)
        + r"</Name>.*?</FeatureType>",
        xml,
        re.S,
    )
    if not block:
        raise SystemExit(f"FAIL: no FeatureType block for {config.TYPENAME}")
    body = block.group(0)

    default_srs = re.search(r"<DefaultSRS>(.*?)</DefaultSRS>", body)
    default_srs = default_srs.group(1) if default_srs else ""
    if default_srs != config.EXPECTED_DEFAULT_SRS:
        raise SystemExit(
            f"FAIL: DefaultSRS changed: {default_srs!r} "
            f"(expected {config.EXPECTED_DEFAULT_SRS!r}). The reprojection chain "
            "assumes this CRS and its Northing,Easting axis order."
        )

    formats = re.findall(r"<Format>(.*?)</Format>", body)
    if config.EXPECTED_OUTPUT_FORMAT not in formats:
        raise SystemExit(
            f"FAIL: output format {config.EXPECTED_OUTPUT_FORMAT!r} no longer "
            f"offered. Advertised: {formats}"
        )

    lower = re.search(r"<ows:LowerCorner>(.*?)</ows:LowerCorner>", body)
    upper = re.search(r"<ows:UpperCorner>(.*?)</ows:UpperCorner>", body)
    if not (lower and upper):
        raise SystemExit("FAIL: no WGS84BoundingBox advertised for the layer")
    min_lon, min_lat = (float(v) for v in lower.group(1).split())
    max_lon, max_lat = (float(v) for v in upper.group(1).split())
    bbox = [min_lon, min_lat, max_lon, max_lat]

    sx0, sy0, sx1, sy1 = config.SANITY_BBOX
    if not (sx0 <= min_lon <= max_lon <= sx1 and sy0 <= min_lat <= max_lat <= sy1):
        raise SystemExit(
            f"FAIL: advertised WGS84 bbox {bbox} is outside the sanity envelope "
            f"{list(config.SANITY_BBOX)}"
        )

    contract = {
        "typename": config.TYPENAME,
        "default_srs": default_srs,
        "other_srs": re.findall(r"<OtherSRS>(.*?)</OtherSRS>", body),
        "formats": formats,
        "wgs84_bbox": bbox,
    }
    contract_path.parent.mkdir(parents=True, exist_ok=True)
    contract_path.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")

    print(f"  ok: {config.TYPENAME} advertised")
    print(f"  ok: DefaultSRS {default_srs}")
    print(f"  ok: format {config.EXPECTED_OUTPUT_FORMAT}")
    print(f"  ok: advertised bbox {bbox}")
    return contract


def check_schema() -> None:
    url = build_url(
        config.WFS_URL,
        {
            "service": "WFS",
            "version": config.WFS_VERSION,
            "request": "DescribeFeatureType",
            "typeName": config.TYPENAME,
        },
    )
    print("DescribeFeatureType ...")
    xml = get_text(url, timeout=60)

    fields = re.findall(r'<element name="(\w+)"', xml)
    required = ["ID_DZIALKI", "NUMER_DZIALKI", "NUMER_OBREBU", config.GROUP_FIELD]
    missing = [f for f in required if f not in fields]
    if missing:
        raise SystemExit(f"FAIL: schema is missing required fields: {missing}")
    print(f"  ok: {', '.join(required)} present")


def feature_count() -> int:
    url = build_url(
        config.WFS_URL,
        {
            "service": "WFS",
            "version": config.WFS_VERSION,
            "request": "GetFeature",
            "typeName": config.TYPENAME,
            "resultType": "hits",
        },
    )
    print("GetFeature resultType=hits ...")
    xml = get_text(url, timeout=60)
    match = re.search(r'numberOfFeatures="(\d+)"', xml)
    if not match:
        raise SystemExit("FAIL: could not read numberOfFeatures from hits response")
    count = int(match.group(1))
    print(f"  source reports {count} features")
    return count


def download(destination: Path) -> None:
    # No outputFormat: the only permitted value is "text/xml; subtype=gml/3.1.1"
    # and it is already the default. No bbox: the full extent is one request.
    url = build_url(
        config.WFS_URL,
        {
            "service": "WFS",
            "version": config.WFS_VERSION,
            "request": "GetFeature",
            "typeName": config.TYPENAME,
        },
    )
    print("GetFeature (full extent) ...")
    payload = get(url)

    if b"ExceptionReport" in payload[:4000]:
        snippet = payload[:400].decode("utf-8", errors="replace")
        raise SystemExit(f"FAIL: server returned an exception:\n{snippet}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(payload)
    got = payload.count(b"<gml:featureMember>")
    print(f"  wrote {destination} ({len(payload) / 1e6:.1f} MB, {got} featureMembers)")

    if got == 0:
        raise SystemExit("FAIL: downloaded GML contains no features")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="build/dzialki.gml", type=Path)
    parser.add_argument("--contract", default="build/source_contract.json", type=Path)
    parser.add_argument(
        "--skip-checks",
        action="store_true",
        help="download without re-validating capabilities/schema",
    )
    args = parser.parse_args()

    if not args.skip_checks:
        check_capabilities(args.contract)
        check_schema()
        expected = feature_count()
        low = config.EXPECTED_FEATURES * (1 - config.COUNT_TOLERANCE)
        high = config.EXPECTED_FEATURES * (1 + config.COUNT_TOLERANCE)
        if not low <= expected <= high:
            raise SystemExit(
                f"FAIL: source count {expected} outside expected "
                f"{config.EXPECTED_FEATURES} +/-{config.COUNT_TOLERANCE:.0%} "
                f"({low:.0f}..{high:.0f}). If this is a real change, update "
                "EXPECTED_FEATURES in scripts/config.py."
            )

    download(args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
