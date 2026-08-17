import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
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

        command = download.build_docker_command(config, container_geojson=None)

        self.assertEqual(command[:3], ["docker", "run", "--rm"])
        self.assertIn("--env", command)
        self.assertIn("MAPBOX_ACCESS_TOKEN", command)
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
