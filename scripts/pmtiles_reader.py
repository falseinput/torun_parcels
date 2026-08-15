"""Minimal, dependency-free PMTiles v3 + Mapbox Vector Tile reader.

This exists so validation decodes the produced bytes independently instead of
asking tippecanoe to confirm its own output. Only the read paths the checks
need are implemented -- this is not a general-purpose library.

References:
  PMTiles v3 spec  https://github.com/protomaps/PMTiles/blob/main/spec/v3/spec.md
  MVT 2.1 spec     https://github.com/mapbox/vector-tile-spec/tree/master/2.1
"""

from __future__ import annotations

import gzip
import math
import struct
import zlib

HEADER_LEN = 127
MAGIC = b"PMTiles"

COMPRESSION = {0: "unknown", 1: "none", 2: "gzip", 3: "brotli", 4: "zstd"}
TILE_TYPE = {0: "unknown", 1: "mvt", 2: "png", 3: "jpeg", 4: "webp", 5: "avif"}

GEOM_TYPE = {0: "UNKNOWN", 1: "POINT", 2: "LINESTRING", 3: "POLYGON"}


# --- varint / protobuf primitives -------------------------------------------


def read_varint(buf: bytes, pos: int):
    result = 0
    shift = 0
    while True:
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def zigzag(n: int) -> int:
    return (n >> 1) ^ (-(n & 1))


def iter_fields(buf: bytes):
    """Yield (field_number, wire_type, value) for one protobuf message."""
    pos = 0
    end = len(buf)
    while pos < end:
        key, pos = read_varint(buf, pos)
        field, wire = key >> 3, key & 7
        if wire == 0:
            val, pos = read_varint(buf, pos)
            yield field, wire, val
        elif wire == 2:
            length, pos = read_varint(buf, pos)
            yield field, wire, buf[pos : pos + length]
            pos += length
        elif wire == 5:
            yield field, wire, buf[pos : pos + 4]
            pos += 4
        elif wire == 1:
            yield field, wire, buf[pos : pos + 8]
            pos += 8
        else:
            raise ValueError(f"unsupported wire type {wire}")


def unpack_varints(buf: bytes) -> list:
    out = []
    pos = 0
    while pos < len(buf):
        val, pos = read_varint(buf, pos)
        out.append(val)
    return out


# --- decompression ----------------------------------------------------------


def decompress(data: bytes, compression: int) -> bytes:
    if compression in (0, 1):
        return data
    if compression == 2:
        return gzip.decompress(data)
    if compression == 4:
        try:
            import zstandard
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("zstd-compressed PMTiles needs `zstandard`") from exc
        return zstandard.ZstdDecompressor().decompress(data)
    if compression == 3:
        try:
            import brotli
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("brotli-compressed PMTiles needs `brotli`") from exc
        return brotli.decompress(data)
    raise ValueError(f"unknown compression {compression}")


# --- Hilbert curve ----------------------------------------------------------


def _rotate(n: int, x: int, y: int, rx: int, ry: int):
    if ry == 0:
        if rx == 1:
            x = n - 1 - x
            y = n - 1 - y
        x, y = y, x
    return x, y


def tile_id_to_zxy(tile_id: int):
    """Inverse of the PMTiles Hilbert tile addressing."""
    acc = 0
    z = 0
    while True:
        num_tiles = (1 << z) * (1 << z)
        if acc + num_tiles > tile_id:
            break
        acc += num_tiles
        z += 1
        if z > 32:
            raise ValueError(f"tile id {tile_id} out of range")

    t = tile_id - acc
    n = 1 << z
    x = y = 0
    s = 1
    while s < n:
        rx = 1 & (t >> 1)
        ry = 1 & (t ^ rx)
        x, y = _rotate(s, x, y, rx, ry)
        x += s * rx
        y += s * ry
        t >>= 2
        s <<= 1
    return z, x, y


# --- Web Mercator -----------------------------------------------------------


def tile_coord_to_lonlat(px, py, z, tx, ty, extent):
    """Tile-local MVT coordinate -> WGS84 lon/lat."""
    n = 1 << z
    lon = (tx + px / extent) / n * 360.0 - 180.0
    ratio = 1 - 2 * (ty + py / extent) / n
    lat = math.degrees(math.atan(math.sinh(math.pi * ratio)))
    return lon, lat


# --- PMTiles ----------------------------------------------------------------


class PMTiles:
    def __init__(self, path):
        self.path = path
        with open(path, "rb") as fh:
            self.raw_header = fh.read(HEADER_LEN)
        if self.raw_header[:7] != MAGIC:
            raise ValueError(f"{path}: not a PMTiles file")
        self.spec_version = self.raw_header[7]
        if self.spec_version != 3:
            raise ValueError(f"{path}: expected PMTiles v3, got v{self.spec_version}")

        vals = struct.unpack("<11Q", self.raw_header[8:96])
        (
            self.root_offset,
            self.root_length,
            self.metadata_offset,
            self.metadata_length,
            self.leaf_offset,
            self.leaf_length,
            self.tile_data_offset,
            self.tile_data_length,
            self.addressed_tiles,
            self.tile_entries,
            self.tile_contents,
        ) = vals

        self.clustered = self.raw_header[96]
        self.internal_compression = self.raw_header[97]
        self.tile_compression = self.raw_header[98]
        self.tile_type = self.raw_header[99]
        self.min_zoom = self.raw_header[100]
        self.max_zoom = self.raw_header[101]

        bbox = struct.unpack("<4i", self.raw_header[102:118])
        self.bounds = tuple(v / 1e7 for v in bbox)  # min_lon, min_lat, max_lon, max_lat

        self.center_zoom = self.raw_header[118]
        clon, clat = struct.unpack("<2i", self.raw_header[119:127])
        self.center = (clon / 1e7, clat / 1e7)

    # -- directories --

    def _read(self, offset, length):
        with open(self.path, "rb") as fh:
            fh.seek(offset)
            return fh.read(length)

    def _parse_directory(self, blob: bytes):
        buf = decompress(blob, self.internal_compression)
        pos = 0
        count, pos = read_varint(buf, pos)

        tile_ids = []
        last = 0
        for _ in range(count):
            delta, pos = read_varint(buf, pos)
            last += delta
            tile_ids.append(last)

        run_lengths = []
        for _ in range(count):
            val, pos = read_varint(buf, pos)
            run_lengths.append(val)

        lengths = []
        for _ in range(count):
            val, pos = read_varint(buf, pos)
            lengths.append(val)

        offsets = []
        for i in range(count):
            val, pos = read_varint(buf, pos)
            if val == 0 and i > 0:
                offsets.append(offsets[i - 1] + lengths[i - 1])
            else:
                offsets.append(val - 1)

        return [
            {
                "tile_id": tile_ids[i],
                "offset": offsets[i],
                "length": lengths[i],
                "run_length": run_lengths[i],
            }
            for i in range(count)
        ]

    def entries(self):
        """Yield every leaf entry, following leaf directories where present."""
        root = self._parse_directory(self._read(self.root_offset, self.root_length))
        for entry in root:
            if entry["run_length"] == 0:
                blob = self._read(
                    self.leaf_offset + entry["offset"], entry["length"]
                )
                yield from self._parse_directory(blob)
            else:
                yield entry

    def metadata(self):
        import json

        blob = self._read(self.metadata_offset, self.metadata_length)
        return json.loads(decompress(blob, self.internal_compression))

    def tile_bytes(self, entry):
        blob = self._read(self.tile_data_offset + entry["offset"], entry["length"])
        return decompress(blob, self.tile_compression)


# --- MVT --------------------------------------------------------------------


def _parse_value(buf: bytes):
    for field, wire, val in iter_fields(buf):
        if field == 1:
            return val.decode("utf-8")
        if field == 2:
            return struct.unpack("<f", val)[0]
        if field == 3:
            return struct.unpack("<d", val)[0]
        if field == 4:
            return val
        if field == 5:
            return val
        if field == 6:
            return zigzag(val)
        if field == 7:
            return bool(val)
    return None


def _decode_geometry(ints):
    """MVT command stream -> list of rings in tile-local coordinates."""
    rings = []
    current = []
    x = y = 0
    i = 0
    while i < len(ints):
        command = ints[i]
        i += 1
        cmd_id = command & 0x7
        count = command >> 3
        if cmd_id == 1:  # MoveTo
            for _ in range(count):
                x += zigzag(ints[i])
                y += zigzag(ints[i + 1])
                i += 2
                if current:
                    rings.append(current)
                current = [(x, y)]
        elif cmd_id == 2:  # LineTo
            for _ in range(count):
                x += zigzag(ints[i])
                y += zigzag(ints[i + 1])
                i += 2
                current.append((x, y))
        elif cmd_id == 7:  # ClosePath
            if current:
                current.append(current[0])
        else:
            raise ValueError(f"unknown MVT command {cmd_id}")
    if current:
        rings.append(current)
    return rings


def decode_mvt(data: bytes):
    """Decode a vector tile into [{name, extent, version, features:[...]}]."""
    layers = []
    for field, _wire, val in iter_fields(data):
        if field != 3:
            continue
        name = None
        extent = 4096
        version = 1
        keys = []
        values = []
        raw_features = []
        for lf, _lw, lv in iter_fields(val):
            if lf == 1:
                name = lv.decode("utf-8")
            elif lf == 2:
                raw_features.append(lv)
            elif lf == 3:
                keys.append(lv.decode("utf-8"))
            elif lf == 4:
                values.append(_parse_value(lv))
            elif lf == 5:
                extent = lv
            elif lf == 15:
                version = lv

        features = []
        for raw in raw_features:
            fid = None
            tags = []
            gtype = 0
            geom_ints = []
            for ff, _fw, fv in iter_fields(raw):
                if ff == 1:
                    fid = fv
                elif ff == 2:
                    tags = unpack_varints(fv)
                elif ff == 3:
                    gtype = fv
                elif ff == 4:
                    geom_ints = unpack_varints(fv)
            props = {}
            for j in range(0, len(tags) - 1, 2):
                props[keys[tags[j]]] = values[tags[j + 1]]
            features.append(
                {
                    "id": fid,
                    "type": GEOM_TYPE.get(gtype, "UNKNOWN"),
                    "properties": props,
                    "rings": _decode_geometry(geom_ints),
                }
            )

        layers.append(
            {"name": name, "extent": extent, "version": version, "features": features}
        )
    return layers
