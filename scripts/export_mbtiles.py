#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Convert a Mapbox GL Native offline cache to an MBTiles 1.3 file."""

from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sqlite3
import sys
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


MAX_LATITUDE = 85.0511287798066
GZIP_HEADER = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff"


class UserFacingError(Exception):
    """An error that can be displayed without a traceback."""


@dataclass(frozen=True)
class ExportSummary:
    source_tiles: int
    exported_tiles: int
    skipped_null_tiles: int
    repacked_tiles: int
    recompressed_tiles: int
    output: Path


def _read_varint(data: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if offset >= len(data):
            raise ValueError("truncated protobuf varint")
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
        if shift > 70:
            raise ValueError("protobuf varint is too long")


def _protobuf_fields(data: bytes) -> Iterator[tuple[int, int, object]]:
    offset = 0
    while offset < len(data):
        key, offset = _read_varint(data, offset)
        field_number = key >> 3
        wire_type = key & 7
        if wire_type == 0:
            value, offset = _read_varint(data, offset)
        elif wire_type == 1:
            end = offset + 8
            if end > len(data):
                raise ValueError("truncated fixed64 protobuf field")
            value = data[offset:end]
            offset = end
        elif wire_type == 2:
            length, offset = _read_varint(data, offset)
            end = offset + length
            if end > len(data):
                raise ValueError("truncated length-delimited protobuf field")
            value = data[offset:end]
            offset = end
        elif wire_type == 5:
            end = offset + 4
            if end > len(data):
                raise ValueError("truncated fixed32 protobuf field")
            value = data[offset:end]
            offset = end
        else:
            raise ValueError(f"unsupported protobuf wire type: {wire_type}")
        yield field_number, wire_type, value


def _packed_varints(data: bytes) -> list[int]:
    values: list[int] = []
    offset = 0
    while offset < len(data):
        value, offset = _read_varint(data, offset)
        values.append(value)
    return values


def zlib_to_gzip(blob: bytes) -> tuple[bytes, bytes]:
    """Return a gzip stream and its decoded payload without recompressing."""
    if len(blob) < 8:
        raise ValueError("zlib tile is too short")
    if blob[0] & 0x0F != zlib.DEFLATED:
        raise ValueError("zlib tile does not use DEFLATE")
    if blob[1] & 0x20:
        raise ValueError("zlib tile uses a preset dictionary")

    decompressor = zlib.decompressobj()
    try:
        raw = decompressor.decompress(blob) + decompressor.flush()
    except zlib.error as error:
        raise ValueError(f"invalid zlib tile: {error}") from error
    if not decompressor.eof or decompressor.unused_data:
        raise ValueError("zlib tile contains an incomplete or trailing stream")

    deflate = blob[2:-4]
    trailer = (
        (zlib.crc32(raw) & 0xFFFFFFFF).to_bytes(4, "little")
        + (len(raw) & 0xFFFFFFFF).to_bytes(4, "little")
    )
    return GZIP_HEADER + deflate + trailer, raw


def _decode_resource(blob: bytes, compressed: int) -> bytes:
    return zlib.decompress(blob) if compressed else blob


def _open_source(source: Path) -> sqlite3.Connection:
    for suffix in ("-journal", "-wal", "-shm"):
        if source.with_name(source.name + suffix).exists():
            raise UserFacingError(
                f"source has a SQLite sidecar ({source.name}{suffix}); recover or remove it safely first"
            )

    connection = sqlite3.connect(
        f"file:{source.resolve()}?mode=ro&immutable=1",
        uri=True,
    )
    required_tables = {"regions", "resources", "tiles"}
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required_tables - tables)
    if missing:
        connection.close()
        raise UserFacingError(
            "source is not a Mapbox GL Native cache; missing tables: "
            + ", ".join(missing)
        )
    return connection


def _load_json_resource(
    connection: sqlite3.Connection, kind: int
) -> Optional[dict[str, object]]:
    row = connection.execute(
        "SELECT data, compressed FROM resources WHERE kind=? ORDER BY id LIMIT 1",
        (kind,),
    ).fetchone()
    if row is None:
        return None
    try:
        value = json.loads(_decode_resource(row[0], row[1]).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, zlib.error) as error:
        raise UserFacingError(f"cannot decode source resource kind {kind}: {error}") from error
    if not isinstance(value, dict):
        raise UserFacingError(f"source resource kind {kind} is not a JSON object")
    return value


def _value_type(value_wrapper: bytes) -> str:
    for field_number, _, value in _protobuf_fields(value_wrapper):
        if field_number == 1:
            return "String"
        if field_number in (2, 3, 4, 5, 6):
            return "Number"
        if field_number == 7:
            return "Boolean"
    return "String"


def _collect_layer_field_types(
    tile: bytes, field_types: dict[str, dict[str, set[str]]]
) -> None:
    for field_number, wire_type, value in _protobuf_fields(tile):
        if field_number != 3 or wire_type != 2 or not isinstance(value, bytes):
            continue

        layer_name = ""
        keys: list[str] = []
        value_types: list[str] = []
        feature_blobs: list[bytes] = []
        for layer_field, layer_wire, layer_value in _protobuf_fields(value):
            if layer_field == 1 and layer_wire == 2 and isinstance(layer_value, bytes):
                layer_name = layer_value.decode("utf-8", errors="strict")
            elif layer_field == 2 and layer_wire == 2 and isinstance(layer_value, bytes):
                feature_blobs.append(layer_value)
            elif layer_field == 3 and layer_wire == 2 and isinstance(layer_value, bytes):
                keys.append(layer_value.decode("utf-8", errors="strict"))
            elif layer_field == 4 and layer_wire == 2 and isinstance(layer_value, bytes):
                value_types.append(_value_type(layer_value))

        if not layer_name:
            raise ValueError("vector tile layer has no name")
        layer_fields = field_types.setdefault(layer_name, {})
        for feature_blob in feature_blobs:
            tags: Optional[bytes] = None
            for feature_field, feature_wire, feature_value in _protobuf_fields(feature_blob):
                if feature_field == 2 and feature_wire == 2 and isinstance(feature_value, bytes):
                    tags = feature_value
                    break
            if tags is None:
                continue
            indexes = _packed_varints(tags)
            if len(indexes) % 2:
                raise ValueError("vector tile feature tags are not key/value pairs")
            for index in range(0, len(indexes), 2):
                key_index = indexes[index]
                value_index = indexes[index + 1]
                if key_index >= len(keys) or value_index >= len(value_types):
                    raise ValueError("vector tile feature tag index is out of range")
                key = keys[key_index]
                layer_fields.setdefault(key, set()).add(value_types[value_index])


def _normalize_field_type(types: set[str]) -> str:
    if not types:
        return "String"
    if types == {"Number"}:
        return "Number"
    if types == {"Boolean"}:
        return "Boolean"
    return "String"


def _tile_longitude(west_index: int, zoom: int) -> float:
    return west_index / (1 << zoom) * 360.0 - 180.0


def _tile_latitude(south_index: int, zoom: int) -> float:
    y = south_index / (1 << zoom)
    latitude = math.degrees(
        math.atan(math.sinh(math.pi * (1.0 - 2.0 * y)))
    )
    return min(max(latitude, -MAX_LATITUDE), MAX_LATITUDE)


def _tile_bounds(z: int, x: int, y: int) -> tuple[float, float, float, float]:
    west = _tile_longitude(x, z)
    east = _tile_longitude(x + 1, z)
    north = _tile_latitude(y, z)
    south = _tile_latitude(y + 1, z)
    return west, south, east, north


def _union_bounds(
    current: Optional[tuple[float, float, float, float]],
    addition: tuple[float, float, float, float],
) -> tuple[float, float, float, float]:
    if current is None:
        return addition
    return (
        min(current[0], addition[0]),
        min(current[1], addition[1]),
        max(current[2], addition[2]),
        max(current[3], addition[3]),
    )


def _create_target(path: Path, page_size: int) -> sqlite3.Connection:
    connection = sqlite3.connect(path)
    connection.execute(f"PRAGMA page_size={page_size}")
    connection.execute("PRAGMA journal_mode=OFF")
    connection.execute("PRAGMA synchronous=OFF")
    connection.execute("PRAGMA locking_mode=EXCLUSIVE")
    connection.execute("PRAGMA foreign_keys=OFF")
    connection.executescript(
        """
        CREATE TABLE metadata (
            name TEXT,
            value TEXT
        );
        CREATE TABLE tiles (
            zoom_level INTEGER,
            tile_column INTEGER,
            tile_row INTEGER,
            tile_data BLOB
        );
        """
    )
    return connection


def _build_vector_metadata(
    tilejson: Optional[dict[str, object]],
    observed_fields: dict[str, dict[str, set[str]]],
) -> dict[str, object]:
    source_layers: list[dict[str, object]] = []
    if tilejson is not None:
        candidate = tilejson.get("vector_layers")
        if candidate is not None and not isinstance(candidate, list):
            raise UserFacingError("source TileJSON vector_layers is not an array")
        for item in candidate or []:
            if isinstance(item, dict):
                source_layers.append(item)

    result: list[dict[str, object]] = []
    source_by_id = {
        str(layer.get("id")): layer for layer in source_layers if layer.get("id") is not None
    }
    layer_ids: list[str] = []
    for layer in source_layers:
        layer_id = str(layer.get("id"))
        if layer_id not in layer_ids:
            layer_ids.append(layer_id)
    for layer_id in observed_fields:
        if layer_id not in layer_ids:
            layer_ids.append(layer_id)

    for layer_id in layer_ids:
        source = source_by_id.get(layer_id, {})
        fields: dict[str, str] = {}
        source_fields = source.get("fields")
        if isinstance(source_fields, dict):
            for key in source_fields:
                fields[str(key)] = "String"
        for key, types in observed_fields.get(layer_id, {}).items():
            fields[key] = _normalize_field_type(types)

        output_layer: dict[str, object] = {"id": layer_id, "fields": fields}
        if source.get("description") is not None:
            output_layer["description"] = str(source["description"])
        if source.get("minzoom") is not None:
            output_layer["minzoom"] = source["minzoom"]
        if source.get("maxzoom") is not None:
            output_layer["maxzoom"] = source["maxzoom"]
        result.append(output_layer)

    return {"vector_layers": result}


def _write_metadata(
    target: sqlite3.Connection,
    tilejson: Optional[dict[str, object]],
    style: Optional[dict[str, object]],
    observed_fields: dict[str, dict[str, set[str]]],
    zoom_range: tuple[int, int],
    bounds: tuple[float, float, float, float],
    destination: Path,
    name_override: Optional[str],
    description_override: Optional[str],
    attribution_override: Optional[str],
    center_override: Optional[str],
) -> None:
    style_name = None
    if style is not None and style.get("name") is not None:
        style_name = str(style["name"])
    name = name_override or style_name or destination.stem

    tilejson_attribution = None
    if tilejson is not None and tilejson.get("attribution") is not None:
        tilejson_attribution = str(tilejson["attribution"])
    attribution = attribution_override or tilejson_attribution

    if center_override:
        center = center_override
    else:
        center = (
            f"{(bounds[0] + bounds[2]) / 2:.6f},"
            f"{(bounds[1] + bounds[3]) / 2:.6f},"
            f"{(zoom_range[0] + zoom_range[1]) // 2}"
        )

    values: list[tuple[str, str]] = [
        ("name", name),
        ("format", "pbf"),
        ("bounds", ",".join(f"{value:.6f}" for value in bounds)),
        ("center", center),
        ("minzoom", str(zoom_range[0])),
        ("maxzoom", str(zoom_range[1])),
        ("type", "baselayer"),
        ("version", "1"),
    ]
    description = description_override
    if description is None and tilejson is not None and tilejson.get("name") is not None:
        description = f"Converted from {tilejson['name']}"
    if description is not None:
        values.append(("description", description))
    if attribution is not None:
        values.append(("attribution", attribution))
    values.append(
        (
            "json",
            json.dumps(
                _build_vector_metadata(tilejson, observed_fields),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ),
        )
    )
    target.executemany("INSERT INTO metadata(name,value) VALUES (?,?)", values)


def convert(
    source: Path,
    destination: Path,
    overwrite: bool = False,
    batch_size: int = 1000,
    page_size: int = 8192,
    name: Optional[str] = None,
    description: Optional[str] = None,
    attribution: Optional[str] = None,
    center: Optional[str] = None,
    quiet: bool = False,
) -> ExportSummary:
    source = source.expanduser().resolve()
    destination = destination.expanduser().resolve()
    if not source.is_file():
        raise UserFacingError(f"source database does not exist: {source}")
    if destination.exists() and not overwrite:
        raise UserFacingError(f"output already exists: {destination}; use --overwrite")
    if not 1 <= batch_size <= 10000:
        raise UserFacingError("batch size must be between 1 and 10000")
    if page_size not in (512, 1024, 2048, 4096, 8192, 16384, 32768, 65536):
        raise UserFacingError("page size must be a valid SQLite page size")

    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(
        f".{destination.name}.{os.getpid()}.tmp"
    )
    if temporary.exists():
        temporary.unlink()

    source_connection = _open_source(source)
    target_connection: Optional[sqlite3.Connection] = None
    exported = 0
    skipped_null = 0
    repacked = 0
    recompressed = 0
    bounds: Optional[tuple[float, float, float, float]] = None
    observed_fields: dict[str, dict[str, set[str]]] = {}

    try:
        style = _load_json_resource(source_connection, 1)
        tilejson = _load_json_resource(source_connection, 2)
        target_connection = _create_target(temporary, page_size)

        cursor = source_connection.execute(
            "SELECT z,x,y,data,compressed FROM tiles"
        )
        zooms: set[int] = set()
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            batch: list[tuple[int, int, int, bytes]] = []
            for z, x, y, blob, compressed in rows:
                if blob is None:
                    skipped_null += 1
                    continue
                if not 0 <= z <= 24 or not 0 <= x < (1 << z) or not 0 <= y < (1 << z):
                    raise UserFacingError(f"tile coordinate outside valid range: {z}/{x}/{y}")
                bounds = _union_bounds(bounds, _tile_bounds(z, x, y))
                zooms.add(z)
                if compressed:
                    tile_data, raw_tile = zlib_to_gzip(blob)
                    repacked += 1
                else:
                    tile_data = gzip.compress(blob, compresslevel=9, mtime=0)
                    recompressed += 1
                    raw_tile = blob
                _collect_layer_field_types(raw_tile, observed_fields)
                batch.append((z, x, (1 << z) - 1 - y, tile_data))

            target_connection.execute("BEGIN")
            target_connection.executemany(
                "INSERT INTO tiles(zoom_level,tile_column,tile_row,tile_data) VALUES (?,?,?,?)",
                batch,
            )
            target_connection.commit()
            exported += len(batch)
            if not quiet and (exported == len(batch) or exported % 10000 < batch_size):
                print(f"exported {exported} tiles", flush=True)

        if exported == 0:
            raise UserFacingError("source contains no non-null tiles")
        if bounds is None:
            raise UserFacingError("could not determine output bounds")
        min_zoom = min(zooms)
        max_zoom = max(zooms)
        if len(zooms) != max_zoom - min_zoom + 1:
            raise UserFacingError("source zoom range contains gaps")
        zoom_range = (min_zoom, max_zoom)

        target_connection.execute("CREATE UNIQUE INDEX tile_index ON tiles(zoom_level,tile_column,tile_row)")
        _write_metadata(
            target_connection,
            tilejson,
            style,
            observed_fields,
            zoom_range,
            bounds,
            destination,
            name,
            description,
            attribution,
            center,
        )
        target_connection.execute("PRAGMA user_version=4")
        target_connection.commit()
        integrity = target_connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise UserFacingError(f"output integrity check failed: {integrity}")
        target_connection.close()
        target_connection = None
        os.replace(temporary, destination)
    except Exception:
        if target_connection is not None:
            target_connection.close()
        temporary.unlink(missing_ok=True)
        raise
    finally:
        source_connection.close()

    return ExportSummary(
        source_tiles=exported + skipped_null,
        exported_tiles=exported,
        skipped_null_tiles=skipped_null,
        repacked_tiles=repacked,
        recompressed_tiles=recompressed,
        output=destination,
    )


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Convert a Mapbox GL Native offline cache database to MBTiles 1.3."
    )
    parser.add_argument("source", type=Path, help="Mapbox GL Native cache database")
    parser.add_argument("output", type=Path, help="destination .mbtiles file")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output")
    parser.add_argument("--batch-size", type=int, default=1000)
    parser.add_argument("--page-size", type=int, default=8192)
    parser.add_argument("--name", help="override the tileset name")
    parser.add_argument("--description", help="override the tileset description")
    parser.add_argument("--attribution", help="override attribution")
    parser.add_argument("--center", metavar="LON,LAT,ZOOM", help="override default center")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = create_parser()
    args = parser.parse_args(argv)
    try:
        summary = convert(
            args.source,
            args.output,
            overwrite=args.overwrite,
            batch_size=args.batch_size,
            page_size=args.page_size,
            name=args.name,
            description=args.description,
            attribution=args.attribution,
            center=args.center,
            quiet=args.quiet,
        )
    except (UserFacingError, ValueError, OSError, sqlite3.Error, zlib.error) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    if not args.quiet:
        print(
            f"exported {summary.exported_tiles:,} tiles; "
            f"skipped {summary.skipped_null_tiles:,} null tiles; "
            f"output={summary.output}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
