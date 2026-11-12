import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock


SCRIPT = Path(__file__).parents[1] / "scripts" / "download.py"
SPEC = importlib.util.spec_from_file_location("mb_offline_download", SCRIPT)
download = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = download
SPEC.loader.exec_module(download)


class DownloadScriptTests(unittest.TestCase):
    def test_estimates_beijing_bbox_tile_count(self):
        bbox = (115.4168, 39.4420, 117.5083, 41.059)
        estimate = download.estimate_bbox_tiles(bbox, 11, 16)

        self.assertEqual(estimate.total, 197_321)
        self.assertEqual(estimate.by_zoom[11], 169)
        self.assertEqual(estimate.by_zoom[16], 147_834)

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

    def test_docker_command_uses_environment_without_token_value(self):
        config = download.DownloadConfig(
            bbox=(115.0, 39.0, 117.0, 41.0),
            geojson=None,
            min_zoom=11,
            max_zoom=12,
            style="mapbox://styles/mapbox/streets-v11",
            output=Path("map.db"),
            image="mbgl-offline:2.0",
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
