#!/usr/bin/env python3
"""Prepare and run bounded mbgl-offline downloads."""

from __future__ import annotations

import argparse
import getpass
import json
import math
import os
import shutil
import sqlite3
import subprocess
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional


MAX_LATITUDE = 85.0511287798066
MAX_GEOJSON_BYTES = 50 * 1024 * 1024
TOKEN_ENV_NAME = "MAPBOX_ACCESS_TOKEN"
ZXY_INDEX_NAME = "tiles_zxy_unique"


class UserFacingError(Exception):
    """An error that can be shown to the user without a traceback."""


@dataclass(frozen=True)
class TileEstimate:
    total: int
    by_zoom: dict[int, int]
    bounds: tuple[float, float, float, float]
    exact: bool


@dataclass(frozen=True)
class DownloadConfig:
    bbox: Optional[tuple[float, float, float, float]]
    geojson: Optional[Path]
    min_zoom: int
    max_zoom: int
    style: str
    output: Path
    image: str
    pixel_ratio: float
    include_ideographs: bool
    api_base_url: Optional[str]


def finite_float(value: object, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as error:
        raise ValueError(f"{label} must be a number") from error
    if not math.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def parse_bbox(value: str) -> tuple[float, float, float, float]:
    parts = value.strip().split(",")
    if len(parts) != 4:
        raise ValueError("bbox must contain four comma-separated values: WEST,SOUTH,EAST,NORTH")

    west, south, east, north = (
        finite_float(part.strip(), label)
        for part, label in zip(parts, ("west", "south", "east", "north"))
    )

    if not -180 <= west < east <= 180:
        raise ValueError("bbox longitude must satisfy -180 <= WEST < EAST <= 180")
    if not -MAX_LATITUDE <= south < north <= MAX_LATITUDE:
        raise ValueError(
            f"bbox latitude must satisfy {-MAX_LATITUDE} <= SOUTH < NORTH <= {MAX_LATITUDE}"
        )

    return west, south, east, north


def _tile_x(longitude: float, zoom: int) -> int:
    return math.floor((longitude + 180.0) / 360.0 * (1 << zoom))


def _tile_y(latitude: float, zoom: int) -> int:
    latitude = min(max(latitude, -MAX_LATITUDE), MAX_LATITUDE)
    y = (
        1.0
        - math.asinh(math.tan(math.radians(latitude))) / math.pi
    ) / 2.0 * (1 << zoom)
    return math.floor(y)


def _last_index(value: float) -> int:
    return max(math.ceil(value) - 1, 0)


def estimate_bbox_tiles(
    bounds: tuple[float, float, float, float],
    min_zoom: int,
    max_zoom: int,
) -> TileEstimate:
    west, south, east, north = bounds
    by_zoom: dict[int, int] = {}

    for zoom in range(min_zoom, max_zoom + 1):
        size = 1 << zoom
        x_start = min(max(_tile_x(west, zoom), 0), size - 1)
        x_end = min(max(_last_index((east + 180.0) / 360.0 * size), 0), size - 1)
        y_start = min(max(_tile_y(north, zoom), 0), size - 1)
        y_end = min(max(_last_index(_raw_tile_y(south, zoom)), 0), size - 1)
        by_zoom[zoom] = (x_end - x_start + 1) * (y_end - y_start + 1)

    return TileEstimate(
        total=sum(by_zoom.values()),
        by_zoom=by_zoom,
        bounds=bounds,
        exact=True,
    )


def _raw_tile_y(latitude: float, zoom: int) -> float:
    latitude = min(max(latitude, -MAX_LATITUDE), MAX_LATITUDE)
    return (
        1.0
        - math.asinh(math.tan(math.radians(latitude))) / math.pi
    ) / 2.0 * (1 << zoom)


def _coordinates(node: object) -> Iterator[tuple[float, float]]:
    if not isinstance(node, list) or not node:
        return

    if all(not isinstance(item, list) for item in node):
        if len(node) < 2:
            raise ValueError("each GeoJSON coordinate must contain longitude and latitude")
        longitude = finite_float(node[0], "longitude")
        latitude = finite_float(node[1], "latitude")
        if not -180 <= longitude <= 180:
            raise ValueError(f"GeoJSON longitude is outside [-180, 180]: {longitude}")
        if not -MAX_LATITUDE <= latitude <= MAX_LATITUDE:
            raise ValueError(
                f"GeoJSON latitude is outside Web Mercator bounds: {latitude}"
            )
        yield longitude, latitude
        return

    for child in node:
        yield from _coordinates(child)


def load_geojson_bounds(path: Path) -> tuple[float, float, float, float]:
    path = path.expanduser().resolve()
    if not path.is_file():
        raise ValueError(f"GeoJSON file does not exist: {path}")
    if path.stat().st_size > MAX_GEOJSON_BYTES:
        raise ValueError("GeoJSON input is larger than 50 MiB")

    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ValueError(f"cannot read valid GeoJSON from {path}: {error}") from error

    if not isinstance(document, dict):
        raise ValueError("GeoJSON root must be an object")

    geometry: object
    if document.get("type") == "Feature":
        geometry = document.get("geometry")
    elif document.get("type") == "FeatureCollection":
        raise ValueError(
            "GeoJSON FeatureCollection is not supported by mbgl-offline; provide one Feature or geometry"
        )
    else:
        geometry = document

    allowed = {
        "Point",
        "MultiPoint",
        "LineString",
        "MultiLineString",
        "Polygon",
        "MultiPolygon",
        "GeometryCollection",
    }
    if not isinstance(geometry, dict) or geometry.get("type") not in allowed:
        raise ValueError("GeoJSON input must be one geometry or one Feature")

    nodes: list[object] = []
    if geometry.get("type") == "GeometryCollection":
        nested = geometry.get("geometries")
        if not isinstance(nested, list):
            raise ValueError("GeometryCollection.geometries must be an array")
        nodes.extend(nested)
    else:
        nodes.append(geometry.get("coordinates"))

    longitudes: list[float] = []
    latitudes: list[float] = []
    for node in nodes:
        for longitude, latitude in _coordinates(node):
            longitudes.append(longitude)
            latitudes.append(latitude)

    if not longitudes:
        raise ValueError("GeoJSON input contains no coordinates")

    return min(longitudes), min(latitudes), max(longitudes), max(latitudes)


def _strip_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def _validate_token(token: str) -> str:
    if not token:
        raise UserFacingError("Mapbox access token is empty")
    if any(character.isspace() for character in token):
        raise UserFacingError("Mapbox access token contains whitespace")
    return token


def load_token(
    env_file: Optional[Path] = None,
    interactive: bool = False,
    allow_missing: bool = False,
) -> tuple[Optional[str], str]:
    environment_token = os.environ.get(TOKEN_ENV_NAME, "").strip()
    if environment_token:
        return _validate_token(environment_token), "environment"

    selected_file = env_file
    if selected_file is None:
        default_file = Path.cwd() / ".env"
        if default_file.is_file():
            selected_file = default_file

    if selected_file is not None:
        selected_file = selected_file.expanduser().resolve()
        if not selected_file.is_file():
            raise UserFacingError(f"token environment file does not exist: {selected_file}")
        try:
            lines = selected_file.read_text(encoding="utf-8").splitlines()
        except OSError as error:
            raise UserFacingError(
                f"cannot read token environment file: {selected_file}"
            ) from error

        for line in lines:
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if stripped.startswith("export "):
                stripped = stripped[len("export ") :]
            if stripped.startswith(f"{TOKEN_ENV_NAME}="):
                token = _strip_quotes(stripped.split("=", 1)[1])
                return _validate_token(token), str(selected_file)

        raise UserFacingError(
            f"token environment file does not define {TOKEN_ENV_NAME}: {selected_file}"
        )

    if interactive and sys.stdin.isatty() and sys.stdout.isatty():
        token = getpass.getpass("Mapbox access token: ")
        return _validate_token(token), "interactive prompt"

    if allow_missing:
        return None, "not set"

    raise UserFacingError(
        f"MAPBOX_ACCESS_TOKEN is not set; set it, use --env-file, or run interactively"
    )


def build_docker_command(
    config: DownloadConfig,
    container_geojson: Optional[Path] = None,
) -> list[str]:
    output = config.output.expanduser().resolve()
    command = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--env",
        TOKEN_ENV_NAME,
        "--volume",
        f"{output.parent}:/workdir",
        "--workdir",
        "/workdir",
        config.image,
    ]

    command.extend([f"--style={config.style}"])
    command.extend([f"--minZoom={config.min_zoom}", f"--maxZoom={config.max_zoom}"])
    command.extend([f"--pixelRatio={config.pixel_ratio}"])

    if config.bbox is not None:
        west, south, east, north = config.bbox
        command.extend(
            [
                f"--north={north}",
                f"--west={west}",
                f"--south={south}",
                f"--east={east}",
            ]
        )
    elif container_geojson is not None:
        command.extend([f"--geojson={container_geojson}"])
    else:
        raise ValueError("download configuration needs either a bbox or GeoJSON input")

    if config.include_ideographs:
        command.append("--includeIdeographs=true")
    if config.api_base_url:
        command.append(f"--apiBaseURL={config.api_base_url}")

    command.append(f"--output=/workdir/{output.name}")
    return command


def resolve_output(output: Path, overwrite: bool) -> Path:
    output = output.expanduser().resolve()
    if (output.exists() or output.is_symlink()) and not overwrite:
        raise UserFacingError(
            f"output already exists: {output}; use --overwrite to replace it"
        )
    if output.exists() and output.is_dir():
        raise UserFacingError(f"output path is a directory: {output}")

    return output


def remove_output(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    if output.is_dir() and not output.is_symlink():
        raise UserFacingError(f"output path is a directory: {output}")
    if output.exists() or output.is_symlink():
        output.unlink()
    for suffix in ("-wal", "-shm", "-journal"):
        sidecar = output.with_name(output.name + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            sidecar.unlink()


def ensure_unique_zxy_index(output: Path) -> None:
    try:
        connection = sqlite3.connect(output)
    except sqlite3.Error as error:
        raise UserFacingError(f"cannot open downloaded database: {error}") from error

    try:
        try:
            connection.execute(
                f"CREATE UNIQUE INDEX IF NOT EXISTS {ZXY_INDEX_NAME} ON tiles(z,x,y)"
            )
            connection.commit()
        except sqlite3.IntegrityError as error:
            connection.rollback()
            duplicate = connection.execute(
                """SELECT z,x,y,COUNT(*),COUNT(DISTINCT url_template),
                          COUNT(DISTINCT pixel_ratio)
                   FROM tiles
                   GROUP BY z,x,y
                   HAVING COUNT(*) > 1
                   LIMIT 1"""
            ).fetchone()
            detail = ""
            if duplicate is not None:
                z, x, y, rows, templates, ratios = duplicate
                detail = (
                    f"; {z}/{x}/{y} has {rows} rows across "
                    f"{templates} URL template(s) and {ratios} pixel ratio(s)"
                )
            raise UserFacingError(
                "downloaded database cannot provide unique z/x/y tile lookup"
                f"{detail}; the database was kept at {output}"
            ) from error
        except sqlite3.Error as error:
            connection.rollback()
            raise UserFacingError(
                f"cannot create z/x/y tile index in {output}: {error}"
            ) from error
    finally:
        connection.close()


def positive_float(value: str) -> float:
    result = finite_float(value, "pixel ratio")
    if result <= 0:
        raise argparse.ArgumentTypeError("pixel ratio must be greater than zero")
    return result


def create_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Estimate and download Mapbox offline tiles with mbgl-offline."
    )
    region = parser.add_mutually_exclusive_group(required=True)
    region.add_argument(
        "--bbox",
        metavar="WEST,SOUTH,EAST,NORTH",
        help="rectangular Web Mercator bounds",
    )
    region.add_argument(
        "--geojson",
        type=Path,
        metavar="FILE",
        help="GeoJSON Feature or geometry (FeatureCollection is unsupported)",
    )
    parser.add_argument("--min-zoom", type=int, default=0, help="minimum zoom (default: 0)")
    parser.add_argument("--max-zoom", type=int, default=15, help="maximum zoom (default: 15)")
    parser.add_argument(
        "--style",
        default="mapbox://styles/mapbox/streets-v11",
        help="Mapbox style URL",
    )
    parser.add_argument("--output", type=Path, default=Path("offline.db"), help="output database")
    parser.add_argument("--image", default="mbgl-offline:2.0", help="mbgl-offline Docker image")
    parser.add_argument("--pixel-ratio", type=positive_float, default=1.0)
    parser.add_argument(
        "--include-ideographs",
        action="store_true",
        help="include CJK glyph ranges",
    )
    parser.add_argument("--api-base-url", help="alternate Mapbox API base URL")
    parser.add_argument("--env-file", type=Path, help="local file defining MAPBOX_ACCESS_TOKEN")
    parser.add_argument("--yes", action="store_true", help="skip the download confirmation")
    parser.add_argument("--overwrite", action="store_true", help="replace an existing output database")
    parser.add_argument("--dry-run", action="store_true", help="show estimate and command without downloading")
    return parser


def confirm(message: str) -> bool:
    try:
        answer = input(f"{message} [y/N]: ").strip().lower()
    except EOFError:
        return False
    return answer in ("y", "yes")


def run(args: argparse.Namespace) -> int:
    if not 0 <= args.min_zoom <= args.max_zoom <= 22:
        raise UserFacingError("zooms must satisfy 0 <= MIN_ZOOM <= MAX_ZOOM <= 22")

    if args.bbox is not None:
        bounds = parse_bbox(args.bbox)
        estimate = estimate_bbox_tiles(bounds, args.min_zoom, args.max_zoom)
        region_description = f"bbox {bounds[0]}, {bounds[1]}, {bounds[2]}, {bounds[3]}"
        geojson = None
    else:
        geojson = args.geojson.expanduser().resolve()
        bounds = load_geojson_bounds(geojson)
        estimate = estimate_bbox_tiles(bounds, args.min_zoom, args.max_zoom)
        estimate = TileEstimate(
            total=estimate.total,
            by_zoom=estimate.by_zoom,
            bounds=bounds,
            exact=False,
        )
        region_description = f"GeoJSON bounding box {bounds[0]}, {bounds[1]}, {bounds[2]}, {bounds[3]}"

    if args.max_zoom > 16:
        print(
            "Warning: zooms above 16 often overzoom vector sources and may not add source detail.",
            file=sys.stderr,
        )

    output = resolve_output(args.output, args.overwrite)
    if geojson is not None and output == geojson:
        raise UserFacingError("output database must not overwrite the GeoJSON input")

    config = DownloadConfig(
        bbox=bounds if args.bbox is not None else None,
        geojson=geojson,
        min_zoom=args.min_zoom,
        max_zoom=args.max_zoom,
        style=args.style,
        output=output,
        image=args.image,
        pixel_ratio=args.pixel_ratio,
        include_ideographs=args.include_ideographs,
        api_base_url=args.api_base_url,
    )

    container_geojson: Optional[Path] = None
    staged_geojson: Optional[Path] = None
    if geojson is not None:
        container_geojson = Path("/workdir") / f".mb-offline-{uuid.uuid4().hex}.geojson"

    command = build_docker_command(config, container_geojson)

    print(f"Region: {region_description}")
    print(f"Zoom range: {args.min_zoom}-{args.max_zoom}")
    print(
        "Estimated tiles: "
        f"{estimate.total:,} "
        f"({'exact' if estimate.exact else 'GeoJSON bounding-box upper bound'})"
    )
    for zoom in range(args.min_zoom, args.max_zoom + 1):
        print(f"  z{zoom}: {estimate.by_zoom[zoom]:,}")
    print(f"Output: {output}")

    if args.dry_run:
        token, token_source = load_token(
            env_file=args.env_file,
            interactive=False,
            allow_missing=True,
        )
        print(f"Token source: {token_source}")
        print("Docker command (token omitted):")
        print("  " + " ".join(command))
        return 0

    try:
        image_check = subprocess.run(
            ["docker", "image", "inspect", args.image],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError as error:
        raise UserFacingError(f"cannot start Docker: {error}") from error
    if image_check.returncode != 0:
        raise UserFacingError(
            f"Docker image is not available locally: {args.image}; build it with the documented Dockerfile command"
        )

    token, token_source = load_token(
        env_file=args.env_file,
        interactive=True,
        allow_missing=False,
    )
    if token is None:
        raise UserFacingError("Mapbox access token is unavailable")
    print(f"Token source: {token_source}")

    if not args.yes:
        if not sys.stdin.isatty() or not sys.stdout.isatty():
            raise UserFacingError("confirmation requires a TTY; rerun with --yes for non-interactive use")
        if not confirm(f"Download approximately {estimate.total:,} tiles?"):
            print("Cancelled.")
            return 130

    remove_output(output)

    try:
        if geojson is not None:
            if container_geojson is None:
                raise UserFacingError("internal error: GeoJSON staging path is missing")
            staged_geojson = output.parent / container_geojson.name
            shutil.copyfile(geojson, staged_geojson)

        child_environment = os.environ.copy()
        child_environment[TOKEN_ENV_NAME] = token
        result = subprocess.run(command, env=child_environment, check=False)
    except OSError as error:
        raise UserFacingError(f"cannot start Docker: {error}") from error
    except KeyboardInterrupt:
        print("\nDownload interrupted; output may be incomplete.", file=sys.stderr)
        return 130
    finally:
        if staged_geojson is not None:
            staged_geojson.unlink(missing_ok=True)

    if result.returncode != 0:
        return result.returncode
    if not output.is_file():
        raise UserFacingError("mbgl-offline exited successfully but the output database was not created")

    print("Building unique z/x/y tile index...")
    ensure_unique_zxy_index(output)
    print(f"Download complete: {output}")
    return 0


def main() -> int:
    parser = create_parser()
    args = parser.parse_args()
    try:
        return run(args)
    except (UserFacingError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("cancelled", file=sys.stderr)
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
