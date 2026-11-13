import importlib.util
import io
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "download.py"
SPEC = importlib.util.spec_from_file_location("mb_offline_download", SCRIPT)
download = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = download
SPEC.loader.exec_module(download)


class DownloadScriptTests(unittest.TestCase):
    def load_geometry_bounds(self, document):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "region.geojson"
            path.write_text(json.dumps(document), encoding="utf-8")
            return download.load_geojson_bounds(path)

    def test_estimates_beijing_bbox_tile_count(self):
        bbox = (115.4168, 39.4420, 117.5083, 41.059)
        estimate = download.estimate_bbox_tiles(bbox, 11, 16)

        self.assertEqual(estimate.total, 197_321)
        self.assertEqual(estimate.by_zoom[11], 169)
        self.assertEqual(estimate.by_zoom[16], 147_834)

    def test_bbox_keeps_half_open_maximum_edges(self):
        bounds = (-180, 0, 0, download.MAX_LATITUDE)
        self.assertEqual(download.estimate_bbox_tiles(bounds, 1, 1).total, 1)
        self.assertEqual(
            download.estimate_bbox_tiles(
                bounds, 1, 1, include_max_edges=True
            ).total,
            4,
        )

    def test_geojson_point_at_tile_corner_counts_one_cell_per_zoom(self):
        estimate = download.estimate_bbox_tiles(
            (0, 0, 0, 0), 11, 12, include_max_edges=True
        )
        self.assertEqual(estimate.by_zoom, {11: 1, 12: 1})
        self.assertEqual(estimate.total, 2)

    def test_geojson_lines_on_tile_edges_have_nonzero_estimates(self):
        for coordinates in (
            [[0, -1], [0, 1]],
            [[-1, 0], [1, 0]],
            [[0, 0], [0, 1]],
            [[-1, 0], [0, 0]],
        ):
            with self.subTest(coordinates=coordinates):
                bounds = self.load_geometry_bounds(
                    {"type": "LineString", "coordinates": coordinates}
                )
                estimate = download.estimate_bbox_tiles(
                    bounds, 1, 2, include_max_edges=True
                )
                self.assertEqual(estimate.by_zoom, {1: 2, 2: 2})
                self.assertEqual(estimate.total, 4)

    def test_geojson_estimate_includes_maximum_endpoint_cells(self):
        bounds = self.load_geometry_bounds(
            {"type": "LineString", "coordinates": [[-1, 1], [0, 0]]}
        )
        estimate = download.estimate_bbox_tiles(
            bounds, 1, 2, include_max_edges=True
        )
        self.assertEqual(estimate.by_zoom, {1: 4, 2: 4})

    def test_geojson_points_at_world_edges_stay_in_tile_grid(self):
        for longitude in (-180, 180):
            for latitude in (-download.MAX_LATITUDE, download.MAX_LATITUDE):
                with self.subTest(longitude=longitude, latitude=latitude):
                    estimate = download.estimate_bbox_tiles(
                        (longitude, latitude, longitude, latitude),
                        0,
                        2,
                        include_max_edges=True,
                    )
                    self.assertEqual(estimate.by_zoom, {0: 1, 1: 1, 2: 1})
        world = (-180, -download.MAX_LATITUDE, 180, download.MAX_LATITUDE)
        self.assertEqual(
            download.estimate_bbox_tiles(world, 0, 2, include_max_edges=True).total,
            21,
        )

    def test_rejects_invalid_bbox(self):
        with self.assertRaises(ValueError):
            download.parse_bbox("117.5083,39.4420,115.4168,41.059")

    def test_reads_geojson_feature_bounds(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "region.geojson"
            path.write_text(
                json.dumps(
                    {
                        "type": "Feature",
                        "properties": {},
                        "geometry": {
                            "type": "Polygon",
                            "coordinates": [
                                [
                                    [115.0, 39.0],
                                    [117.0, 39.0],
                                    [117.0, 41.0],
                                    [115.0, 41.0],
                                    [115.0, 39.0],
                                ]
                            ],
                        },
                    }
                ),
                encoding="utf-8",
            )

            self.assertEqual(
                download.load_geojson_bounds(path),
                (115.0, 39.0, 117.0, 41.0),
            )

    def test_rejects_geojson_feature_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "collection.geojson"
            path.write_text(
                json.dumps({"type": "FeatureCollection", "features": []}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "FeatureCollection"):
                download.load_geojson_bounds(path)

    def test_reads_nested_geometry_collections_and_all_members(self):
        geometry = {
            "type": "GeometryCollection",
            "geometries": [
                {"type": "Point", "coordinates": [-5, 2]},
                {
                    "type": "Polygon",
                    "coordinates": [[[-6, -8], [-4, -8], [-4, -6], [-6, -8]]],
                },
                {
                    "type": "GeometryCollection",
                    "geometries": [
                        {"type": "LineString", "coordinates": [[1, -7], [3, 4]]},
                        {
                            "type": "GeometryCollection",
                            "geometries": [
                                {"type": "MultiPoint", "coordinates": [[8, 12], [9, 10]]},
                                {
                                    "type": "MultiPolygon",
                                    "coordinates": [[[[10, 20], [11, 20], [11, 21], [10, 20]]]],
                                },
                            ],
                        },
                    ],
                },
            ],
        }
        for document in (
            geometry,
            {"type": "Feature", "properties": {}, "geometry": geometry},
        ):
            with self.subTest(root=document["type"]):
                self.assertEqual(self.load_geometry_bounds(document), (-6, -8, 11, 21))

    def test_rejects_malformed_geometry_collection_arrays(self):
        for children in (None, {}, "invalid"):
            with self.subTest(children=children):
                with self.assertRaisesRegex(ValueError, "geometries must be an array"):
                    self.load_geometry_bounds(
                        {"type": "GeometryCollection", "geometries": children}
                    )

    def test_rejects_invalid_collection_members_instead_of_skipping_them(self):
        valid = {"type": "Point", "coordinates": [1, 2]}
        for invalid in (
            None,
            42,
            {},
            {"type": []},
            {"type": "FeatureCollection", "features": []},
            {"type": "Feature", "geometry": valid},
            {"type": "Point"},
            {"type": "Point", "coordinates": {}},
            {"type": "Point", "coordinates": [181, 0]},
            {"type": "Point", "coordinates": [0, 86]},
            {"type": "LineString", "coordinates": [[0, 0], 7]},
        ):
            with self.subTest(invalid=invalid):
                with self.assertRaises(ValueError):
                    self.load_geometry_bounds(
                        {"type": "GeometryCollection", "geometries": [valid, invalid]}
                    )

    def test_empty_collection_needs_at_least_one_coordinate(self):
        empty = {"type": "GeometryCollection", "geometries": []}
        with self.assertRaisesRegex(ValueError, "contains no coordinates"):
            self.load_geometry_bounds(empty)
        self.assertEqual(
            self.load_geometry_bounds(
                {
                    "type": "GeometryCollection",
                    "geometries": [empty, {"type": "Point", "coordinates": [0, 0]}],
                }
            ),
            (0, 0, 0, 0),
        )

    def test_cli_geojson_corner_and_collection_previews_with_cjk(self):
        point = {"type": "Point", "coordinates": [0, 0]}
        for geometry in (
            point,
            {"type": "LineString", "coordinates": [[0, 0], [0, 1]]},
            {"type": "GeometryCollection", "geometries": [point]},
        ):
            with self.subTest(geometry=geometry["type"]):
                with tempfile.TemporaryDirectory() as directory:
                    source = Path(directory) / "region.geojson"
                    source.write_text(json.dumps(geometry), encoding="utf-8")
                    original = source.read_bytes()
                    output = Path(directory) / "preview.db"
                    result = subprocess.run(
                        [
                            sys.executable,
                            str(SCRIPT),
                            "--geojson", str(source),
                            "--min-zoom", "11",
                            "--max-zoom", "12",
                            "--include-ideographs",
                            "--output", str(output),
                            "--dry-run",
                        ],
                        cwd=directory,
                        env={**os.environ, "MAPBOX_ACCESS_TOKEN": ""},
                        text=True,
                        capture_output=True,
                        check=True,
                    )
                    if geometry["type"] in ("Point", "GeometryCollection"):
                        self.assertIn(
                            "Estimated tiles: 2 (GeoJSON bounding-box upper bound)",
                            result.stdout,
                        )
                    self.assertNotIn("Estimated tiles: 0 ", result.stdout)
                    self.assertIn("GeoJSON bounding-box upper bound", result.stdout)
                    self.assertIn("--includeIdeographs=1", result.stdout)
                    self.assertNotIn("--includeIdeographs=true", result.stdout)
                    self.assertEqual(source.read_bytes(), original)
                    self.assertFalse(output.exists())

    def test_docker_command_uses_environment_without_token_value(self):
        config = download.DownloadConfig(
            bbox=(115.0, 39.0, 117.0, 41.0),
            geojson=None,
            min_zoom=11,
            max_zoom=12,
            style="mapbox://styles/mapbox/streets-v11",
            output=Path("map.db"),
            image="mbgl-offline",
            pixel_ratio=1.0,
            include_ideographs=False,
            api_base_url=None,
        )

        with mock.patch.multiple(
            download.os, getuid=lambda: 1234, getgid=lambda: 5678, create=True
        ):
            command = download.build_docker_command(config, container_geojson=None)

        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertIn("--env", command)
        self.assertIn("MAPBOX_ACCESS_TOKEN", command)
        self.assertEqual(command[command.index("--user") + 1], "1234:5678")
        self.assertNotIn("--token", command)
        self.assertIn("--volume", command)
        self.assertIn("--north=41.0", command)
        self.assertIn("--west=115.0", command)

    def test_cjk_uses_native_numeric_boolean_only_when_requested(self):
        for include in (False, True):
            with self.subTest(include=include):
                config = download.DownloadConfig(
                    bbox=(115.0, 39.0, 117.0, 41.0),
                    geojson=None,
                    min_zoom=11,
                    max_zoom=12,
                    style="mapbox://styles/mapbox/streets-v11",
                    output=Path("map.db"),
                    image="mbgl-offline",
                    pixel_ratio=1.0,
                    include_ideographs=include,
                    api_base_url=None,
                )
                command = download.build_docker_command(config)
                flags = [flag for flag in command if flag.startswith("--includeIdeographs")]
                self.assertEqual(flags, ["--includeIdeographs=1"] if include else [])

    def test_run_preserves_nested_geojson_and_forwards_numeric_cjk(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "region.geojson"
            source.write_text(json.dumps({
                "type": "Feature",
                "properties": {"name": "keep this metadata"},
                "geometry": {
                    "type": "GeometryCollection",
                    "geometries": [{
                        "type": "GeometryCollection",
                        "geometries": [{"type": "Point", "coordinates": [0, 0]}],
                    }],
                },
            }), encoding="utf-8")
            original = source.read_bytes()
            output = root / "map.db"
            args = download.create_parser().parse_args([
                "--geojson", str(source),
                "--min-zoom", "11", "--max-zoom", "12",
                "--include-ideographs", "--output", str(output), "--yes",
            ])

            def fake_docker(command, **kwargs):
                if command[:3] == ["docker", "image", "inspect"]:
                    return subprocess.CompletedProcess(command, 0)
                self.assertEqual(command[:3], ["docker", "run", "--rm"])
                self.assertIn("--includeIdeographs=1", command)
                self.assertNotIn("--token", command)
                self.assertNotIn("fixture-credential", " ".join(command))
                self.assertEqual(
                    kwargs["env"]["MAPBOX_ACCESS_TOKEN"], "fixture-credential"
                )
                staged = next(
                    flag.split("=", 1)[1]
                    for flag in command
                    if flag.startswith("--geojson=")
                )
                self.assertEqual((root / Path(staged).name).read_bytes(), original)
                with closing(sqlite3.connect(output)) as db:
                    db.execute("CREATE TABLE tiles(z INTEGER,x INTEGER,y INTEGER,data BLOB)")
                    db.executemany(
                        "INSERT INTO tiles VALUES (?,?,?,?)",
                        [(11, 1024, 1024, b"fixture"), (12, 2048, 2048, b"fixture")],
                    )
                    db.commit()
                return subprocess.CompletedProcess(command, 0)

            with mock.patch.dict(os.environ, {"MAPBOX_ACCESS_TOKEN": "fixture-credential"}):
                with mock.patch.object(download.subprocess, "run", side_effect=fake_docker) as run:
                    with redirect_stdout(io.StringIO()) as stdout:
                        result = download.run(args)
            self.assertEqual(result, 0)
            self.assertEqual(run.call_count, 2)
            self.assertIn(
                "Estimated tiles: 2 (GeoJSON bounding-box upper bound)", stdout.getvalue()
            )
            self.assertNotIn("fixture-credential", stdout.getvalue())
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse(list(root.glob(".mb-offline-*.geojson")))
            with closing(sqlite3.connect(output)) as db:
                self.assertEqual(db.execute("SELECT COUNT(*) FROM tiles").fetchone()[0], 2)
                index = next(
                    row for row in db.execute("PRAGMA index_list('tiles')")
                    if row[1] == download.ZXY_INDEX_NAME
                )
                self.assertEqual(index[2], 1)

    def test_loads_token_from_env_file_without_leaking_value(self):
        with tempfile.TemporaryDirectory() as directory:
            env_file = Path(directory) / ".env"
            env_file.write_text(
                "# local credentials\nMAPBOX_ACCESS_TOKEN='test-token-value'\n",
                encoding="utf-8",
            )

            with mock.patch.dict(os.environ, {}, clear=True):
                token, source = download.load_token(
                    env_file=env_file,
                    interactive=False,
                )

            self.assertEqual(token, "test-token-value")
            self.assertEqual(source, str(env_file.resolve()))

    def test_output_validation_does_not_remove_existing_database(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.db"
            output.write_bytes(b"existing database")

            resolved = download.resolve_output(output, overwrite=True)

            self.assertEqual(resolved, output.resolve())
            self.assertTrue(output.is_file())
            self.assertEqual(output.read_bytes(), b"existing database")

            download.remove_output(resolved)

            self.assertFalse(output.exists())

    def test_builds_unique_zxy_index_for_direct_tile_lookup(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.db"
            with closing(sqlite3.connect(output)) as connection:
                connection.execute(
                    """CREATE TABLE tiles (
                           url_template TEXT NOT NULL,
                           pixel_ratio INTEGER NOT NULL,
                           z INTEGER NOT NULL,
                           x INTEGER NOT NULL,
                           y INTEGER NOT NULL,
                           data BLOB
                       )"""
                )
                connection.executemany(
                    "INSERT INTO tiles VALUES (?,?,?,?,?,?)",
                    [
                        ("template", 1, 10, 843, 388, b"first"),
                        ("template", 1, 10, 844, 388, b"second"),
                    ],
                )
                connection.commit()

            download.ensure_unique_zxy_index(output)

            with closing(sqlite3.connect(output)) as connection:
                index = next(
                    row
                    for row in connection.execute("PRAGMA index_list('tiles')")
                    if row[1] == download.ZXY_INDEX_NAME
                )
                self.assertEqual(index[2], 1)
                columns = [
                    row[2]
                    for row in connection.execute(
                        f"PRAGMA index_info('{download.ZXY_INDEX_NAME}')"
                    )
                ]
                self.assertEqual(columns, ["z", "x", "y"])
                plan = connection.execute(
                    """EXPLAIN QUERY PLAN
                       SELECT data FROM tiles WHERE z=? AND x=? AND y=?""",
                    (10, 843, 388),
                ).fetchall()
                self.assertTrue(
                    any(download.ZXY_INDEX_NAME in row[3] for row in plan),
                    plan,
                )

    def test_rejects_ambiguous_zxy_tiles_and_keeps_database(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "map.db"
            with closing(sqlite3.connect(output)) as connection:
                connection.execute(
                    """CREATE TABLE tiles (
                           url_template TEXT NOT NULL,
                           pixel_ratio INTEGER NOT NULL,
                           z INTEGER NOT NULL,
                           x INTEGER NOT NULL,
                           y INTEGER NOT NULL
                       )"""
                )
                connection.executemany(
                    "INSERT INTO tiles VALUES (?,?,?,?,?)",
                    [
                        ("vector", 1, 10, 843, 388),
                        ("raster", 2, 10, 843, 388),
                    ],
                )
                connection.commit()

            with self.assertRaisesRegex(
                download.UserFacingError,
                r"10/843/388 has 2 rows across 2 URL template\(s\) and 2 pixel ratio\(s\)",
            ):
                download.ensure_unique_zxy_index(output)

            self.assertTrue(output.is_file())
            with closing(sqlite3.connect(output)) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM tiles").fetchone()[0],
                    2,
                )
                index_names = {
                    row[1] for row in connection.execute("PRAGMA index_list('tiles')")
                }
                self.assertNotIn(download.ZXY_INDEX_NAME, index_names)

    def test_cli_help_and_bbox_dry_run_do_not_require_a_token(self):
        for arguments in (
            ["--help"],
            [
                "--bbox",
                "115.4168,39.4420,117.5083,41.059",
                "--min-zoom",
                "11",
                "--max-zoom",
                "12",
                "--output",
                "dry-run.db",
                "--dry-run",
            ],
        ):
            with self.subTest(arguments=arguments):
                result = subprocess.run(
                    [sys.executable, str(SCRIPT), *arguments],
                    check=True,
                    capture_output=True,
                    text=True,
                    env={**os.environ, "MAPBOX_ACCESS_TOKEN": ""},
                )

                self.assertNotIn("test-token-value", result.stdout)
                self.assertNotIn("pk.", result.stdout)

                if "--dry-run" in arguments:
                    self.assertIn("Estimated tiles", result.stdout)


if __name__ == "__main__":
    unittest.main()
