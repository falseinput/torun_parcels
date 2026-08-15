#!/usr/bin/env python3
"""Static file server with HTTP Range support, for previewing dist/ locally.

Python's stdlib SimpleHTTPRequestHandler does not implement Range requests.
PMTiles is built entirely on them, so serving the site with `python -m
http.server` fails in a confusing way: the archive downloads but no tile ever
renders. This handler adds the missing 206 path.

Mirrors what GitHub Pages provides in production (206 + Accept-Ranges + CORS).
"""

from __future__ import annotations

import argparse
import http.server
import os
import re
import socketserver
import sys

RANGE_RE = re.compile(r"^bytes=(\d*)-(\d*)$")


class RangeRequestHandler(http.server.SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def send_head(self):
        header = self.headers.get("Range")
        if not header:
            return super().send_head()

        match = RANGE_RE.match(header.strip())
        if not match:
            self.send_error(400, "malformed Range header")
            return None

        path = self.translate_path(self.path)
        if os.path.isdir(path):
            return super().send_head()

        try:
            handle = open(path, "rb")
        except OSError:
            self.send_error(404, "File not found")
            return None

        size = os.fstat(handle.fileno()).st_size
        start_raw, end_raw = match.group(1), match.group(2)

        if start_raw == "":
            if end_raw == "":
                handle.close()
                self.send_error(400, "malformed Range header")
                return None
            length = int(end_raw)
            start = max(0, size - length)
            end = size - 1
        else:
            start = int(start_raw)
            end = int(end_raw) if end_raw else size - 1

        if start >= size:
            handle.close()
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{size}")
            self.end_headers()
            return None

        end = min(end, size - 1)
        handle.seek(start)

        self.send_response(206)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        return _Bounded(handle, end - start + 1)

    def log_message(self, fmt, *args):  # quieter console
        sys.stderr.write("  %s\n" % (fmt % args))


class _Bounded:
    """File wrapper that stops after `remaining` bytes, for copyfile()."""

    def __init__(self, handle, remaining):
        self.handle = handle
        self.remaining = remaining

    def read(self, size=-1):
        if self.remaining <= 0:
            return b""
        if size < 0 or size > self.remaining:
            size = self.remaining
        chunk = self.handle.read(size)
        self.remaining -= len(chunk)
        return chunk

    def close(self):
        self.handle.close()


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default="dist")
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()

    if not os.path.isdir(args.dir):
        raise SystemExit(f"FAIL: {args.dir} not found -- run `make publish` first")

    os.chdir(args.dir)
    with Server(("127.0.0.1", args.port), RangeRequestHandler) as httpd:
        print(f"serving {args.dir}/ with Range support at "
              f"http://localhost:{args.port}/")
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nstopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
