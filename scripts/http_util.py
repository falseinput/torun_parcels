"""Tiny stdlib HTTP helper with gzip handling and retries.

The WFS is a single small municipal server; we make at most a handful of
requests per build, so this deliberately stays polite: one connection at a
time, an identifying User-Agent, and backoff on failure.
"""

from __future__ import annotations

import gzip
import time
import urllib.error
import urllib.parse
import urllib.request

USER_AGENT = "torun-parcels-pipeline/1.0 (+https://github.com/; open data ETL)"

DEFAULT_TIMEOUT = 300
MAX_RETRIES = 3
BACKOFF_SECONDS = 5


def build_url(base: str, params: dict) -> str:
    return f"{base}?{urllib.parse.urlencode(params)}"


def get(url: str, timeout: int = DEFAULT_TIMEOUT) -> bytes:
    """GET with gzip negotiation and bounded retries."""
    last_error = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept-Encoding": "gzip",
                },
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
                return payload
        except (urllib.error.URLError, OSError) as exc:
            last_error = exc
            if attempt < MAX_RETRIES:
                wait = BACKOFF_SECONDS * attempt
                print(f"  request failed ({exc}); retrying in {wait}s")
                time.sleep(wait)
    raise RuntimeError(f"GET {url} failed after {MAX_RETRIES} attempts: {last_error}")


def get_text(url: str, timeout: int = DEFAULT_TIMEOUT) -> str:
    return get(url, timeout).decode("utf-8", errors="replace")
