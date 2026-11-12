import gzip
import importlib.util
import io
import json
import os
import sqlite3
import struct
import sys
import tempfile
import unittest
import zlib
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "export_mbtiles.py"
SPEC = importlib.util.spec_from_file_location("mb_offline_export_mbtiles", SCRIPT)
export_mbtiles = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = export_mbtiles
SPEC.loader.exec_module(export_mbtiles)


def varint(value: int) -> bytes:
    if value < 0:
        raise ValueError("test encoder only handles non-negative varints")
    output = bytearray()
    while value > 0x7F:
        output.append((value & 0x7F) | 0x80)
        value >>= 7
    output.append(value)
    return bytes(output)


def protobuf_field(number: int, wire_type: int, payload: bytes) -> bytes:
    return varint((number << 3) | wire_type) + payload


def length_field(number: int, payload: bytes) -> bytes:
    return protobuf_field(number, 2, varint(len(payload)) + payload)


def varint_field(number: int, value: int) -> bytes:
    return protobuf_field(number, 0, varint(value))


def value_string(value: str) -> bytes:
    return length_field(1, value.encode("utf-8"))


def value_double(value: float) -> bytes:
    return protobuf_field(3, 1, struct.pack("<d", value))


def value_bool(value: bool) -> bytes:
    return varint_field(7, int(value))


def feature(tags: list[int]) -> bytes:
    packed_tags = b"".join(varint(value) for value in tags)
    return (
        length_field(2, packed_tags)
        + varint_field(3, 1)
        + length_field(4, varint(9))
    )


def vector_tile() -> bytes:
    water = (
        length_field(1, b"water")
        + length_field(2, feature([0, 0, 1, 1, 2, 2]))
        + length_field(3, b"name")
        + length_field(3, b"height")
        + length_field(3, b"enabled")
        + length_field(4, value_string("Reservoir"))
        + length_field(4, value_double(12.5))
        + length_field(4, value_bool(True))
        + varint_field(5, 4096)
        + varint_field(15, 2)
    )
    road = (
        length_field(1, b"road")
        + length_field(2, feature([0, 0]))
        + length_field(3, b"name")
        + length_field(4, value_string("Route 1"))
        + varint_field(5, 4096)
        + varint_field(15, 2)
    )
    return length_field(3, water) + length_field(3, road)


def create_cache(path: Path, schema_version: int = 6) -> None:
    if schema_version not in (5, 6):
        raise ValueError("fixture schema must be 5 or 6")
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE regions (
            id INTEGER PRIMARY KEY,
            definition TEXT NOT NULL,
            description BLOB
        );
        CREATE TABLE resources (
            id INTEGER PRIMARY KEY,
            url TEXT NOT NULL,
            kind INTEGER NOT NULL,
            expires INTEGER,
            modified INTEGER,
            etag TEXT,
            data BLOB,
            compressed INTEGER NOT NULL DEFAULT 0,
            accessed INTEGER NOT NULL,
            UNIQUE (url)
        );
        CREATE TABLE tiles (
            id INTEGER PRIMARY KEY,
            url_template TEXT NOT NULL,
            pixel_ratio INTEGER NOT NULL,
            z INTEGER NOT NULL,
            x INTEGER NOT NULL,
            y INTEGER NOT NULL,
            expires INTEGER,
            modified INTEGER,
            etag TEXT,
            data BLOB,
            compressed INTEGER NOT NULL DEFAULT 0,
            accessed INTEGER NOT NULL,
            UNIQUE (url_template, pixel_ratio, z, x, y)
        );
        """
    )
    # Start with v5 tables; v6 adds these columns through the upstream migration.
    if schema_version == 6:
        for table in ("resources", "tiles"):
            connection.execute(
                f"ALTER TABLE {table} ADD COLUMN must_revalidate INTEGER NOT NULL DEFAULT 0"
            )
    connection.execute(f"PRAGMA user_version={schema_version}")

    style = {
        "version": 8,
        "name": "Synthetic Offline Style",
        "sources": {
            "composite": {
                "type": "vector",
                "url": "mapbox://synthetic.source",
            }
        },
        "layers": [],
    }
    tilejson = {
        "tilejson": "3.0.0",
        "name": "Synthetic Source",
        "attribution": "Synthetic attribution",
        "bounds": [-180, -85, 180, 85],
        "center": [0, 0, 0],
        "minzoom": 0,
        "maxzoom": 2,
        "vector_layers": [
            {
                "id": "water",
                "description": "Water features",
                "minzoom": 0,
                "maxzoom": 2,
                "fields": {
                    "name": "Feature name",
                    "height": "Height in meters",
                    "enabled": "Whether enabled",
                },
            },
            {
                "id": "road",
                "description": "Road features",
                "minzoom": 0,
                "maxzoom": 2,
                "fields": {"name": "Feature name"},
            },
        ],
    }
    connection.execute(
        "INSERT INTO regions(definition) VALUES (?)",
        (
            json.dumps(
                {
                    "style_url": "mapbox://styles/synthetic/style",
                    "bounds": [-85.0511287798066, -180, 85.0511287798066, 180],
                    "min_zoom": 0,
                    "max_zoom": 2,
                }
            ),
        ),
    )
    connection.executemany(
        "INSERT INTO resources(url,kind,data,compressed,accessed) VALUES (?,?,?,?,1)",
        [
            ("mapbox://styles/synthetic/style", 1, json.dumps(style).encode(), 0),
            ("mapbox://synthetic.source", 2, json.dumps(tilejson).encode(), 0),
        ],
    )

    tile = vector_tile()
    zlib_tile = zlib.compress(tile)
    rows = [
        ("template", 1, 0, 0, 0, zlib_tile, 1),
        ("template", 1, 1, 1, 1, tile, 0),
        ("template", 1, 2, 1, 0, zlib_tile, 1),
        ("template", 1, 2, 1, 1, None, 0),
    ]
    connection.executemany(
        """INSERT INTO tiles(
               url_template,pixel_ratio,z,x,y,data,compressed,accessed
           ) VALUES (?,?,?,?,?,?,?,1)""",
        rows,
    )
    connection.commit()
    connection.close()


class ExportMbtilesTests(unittest.TestCase):
    SCHEMA_VERSION = 6

    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.source = self.root / "source.db"
        self.destination = self.root / "output.mbtiles"
        create_cache(self.source, schema_version=self.SCHEMA_VERSION)

    def tearDown(self):
        self.directory.cleanup()

    def test_exports_valid_mbtiles_and_reverses_y_axis(self):
        source_bytes = self.source.read_bytes()
        with closing(sqlite3.connect(self.source)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA user_version").fetchone()[0],
                self.SCHEMA_VERSION,
            )
            for table in ("resources", "tiles"):
                columns = {
                    row[1] for row in connection.execute(f"PRAGMA table_info({table})")
                }
                self.assertEqual("must_revalidate" in columns, self.SCHEMA_VERSION == 6)

        summary = export_mbtiles.convert(
            self.source,
            self.destination,
            overwrite=False,
            batch_size=2,
            quiet=True,
        )

        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(summary.source_tiles, 4)
        self.assertEqual(summary.exported_tiles, 3)
        self.assertEqual(summary.skipped_null_tiles, 1)
        self.assertEqual(summary.recompressed_tiles, 1)
        self.assertEqual(summary.repacked_tiles, 2)

        with closing(sqlite3.connect(self.destination)) as connection:
            self.assertEqual(
                connection.execute("PRAGMA integrity_check").fetchone()[0], "ok"
            )
            self.assertEqual(connection.execute("PRAGMA user_version").fetchone()[0], 4)
            rows = connection.execute(
                "SELECT zoom_level,tile_column,tile_row,tile_data FROM tiles ORDER BY zoom_level"
            ).fetchall()
            self.assertEqual(len(rows), 3)
            self.assertEqual(
                [(row[0], row[1], row[2]) for row in rows],
                [(0, 0, 0), (1, 1, 0), (2, 1, 3)],
            )
            for row in rows:
                self.assertEqual(row[3][:2], b"\x1f\x8b")
                self.assertEqual(gzip.decompress(row[3]), vector_tile())

            metadata = dict(
                connection.execute("SELECT name,value FROM metadata").fetchall()
            )
            self.assertEqual(metadata["format"], "pbf")
            self.assertEqual(metadata["name"], "Synthetic Offline Style")
            self.assertEqual(metadata["minzoom"], "0")
            self.assertEqual(metadata["maxzoom"], "2")
            self.assertEqual(metadata["type"], "baselayer")
            self.assertEqual(metadata["attribution"], "Synthetic attribution")
            vector_metadata = json.loads(metadata["json"])
            self.assertEqual(
                vector_metadata["vector_layers"][0]["fields"],
                {"name": "String", "height": "Number", "enabled": "Boolean"},
            )
            self.assertEqual(
                vector_metadata["vector_layers"][1]["fields"],
                {"name": "String"},
            )

    def test_source_cache_point_lookup_uses_unique_tile_key_index(self):
        with closing(sqlite3.connect(self.source)) as connection:
            tile_indexes = connection.execute("PRAGMA index_list('tiles')").fetchall()
            unique_index = next(row for row in tile_indexes if row[2] == 1)
            columns = [
                row[2]
                for row in connection.execute(
                    f"PRAGMA index_info('{unique_index[1]}')"
                )
            ]
            self.assertEqual(
                columns,
                ["url_template", "pixel_ratio", "z", "x", "y"],
            )

            plan = connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT data FROM tiles
                   WHERE url_template=? AND pixel_ratio=? AND z=? AND x=? AND y=?""",
                ("template", 1, 1, 1, 1),
            ).fetchall()
            self.assertTrue(
                any(unique_index[1] in row[3] for row in plan),
                plan,
            )

    def test_mbtiles_point_lookup_uses_coordinate_index(self):
        export_mbtiles.convert(self.source, self.destination, quiet=True)

        with closing(sqlite3.connect(self.destination)) as connection:
            tile_index = next(
                row
                for row in connection.execute("PRAGMA index_list('tiles')")
                if row[1] == "tile_index"
            )
            self.assertEqual(tile_index[2], 1)
            columns = [
                row[2]
                for row in connection.execute("PRAGMA index_info('tile_index')")
            ]
            self.assertEqual(
                columns,
                ["zoom_level", "tile_column", "tile_row"],
            )

            plan = connection.execute(
                """EXPLAIN QUERY PLAN
                   SELECT tile_data FROM tiles
                   WHERE zoom_level=? AND tile_column=? AND tile_row=?""",
                (1, 1, 0),
            ).fetchall()
            self.assertTrue(
                any("tile_index" in row[3] for row in plan),
                plan,
            )

    def test_reuses_deflate_body_when_repacking_zlib(self):
        source_blob = zlib.compress(vector_tile())
        gzip_blob, raw_tile = export_mbtiles.zlib_to_gzip(source_blob)

        self.assertEqual(raw_tile, vector_tile())
        self.assertEqual(gzip_blob[:2], b"\x1f\x8b")
        self.assertEqual(gzip.decompress(gzip_blob), vector_tile())
        self.assertEqual(gzip_blob[10:-8], source_blob[2:-4])

    def test_rejects_existing_output_without_overwrite(self):
        self.destination.write_bytes(b"existing")

        with self.assertRaisesRegex(
            export_mbtiles.UserFacingError, "already exists"
        ):
            export_mbtiles.convert(self.source, self.destination, overwrite=False)

        self.assertEqual(self.destination.read_bytes(), b"existing")

    def test_replaces_distinct_output_when_overwrite_is_requested(self):
        self.destination.write_bytes(b"existing output")
        source_bytes = self.source.read_bytes()

        summary = export_mbtiles.convert(
            self.source, self.destination, overwrite=True, quiet=True
        )

        self.assertEqual(summary.exported_tiles, 3)
        self.assertEqual(self.source.read_bytes(), source_bytes)
        with closing(sqlite3.connect(self.destination)) as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM tiles").fetchone()[0], 3
            )
        self.assertEqual(set(self.root.iterdir()), {self.source, self.destination})

    def test_missing_source_does_not_create_files(self):
        source = self.root / "missing #?%.db"

        with self.assertRaisesRegex(
            export_mbtiles.UserFacingError, "source database does not exist"
        ):
            export_mbtiles.convert(source, self.destination)

        self.assertEqual(set(self.root.iterdir()), {self.source})

    def test_cli_rejects_source_as_output_even_with_overwrite(self):
        source_bytes = self.source.read_bytes()
        for overwrite in (False, True):
            with self.subTest(overwrite=overwrite):
                arguments = [str(self.source), str(self.source)]
                if overwrite:
                    arguments.append("--overwrite")
                with redirect_stderr(io.StringIO()) as stderr:
                    result = export_mbtiles.main(arguments)
                self.assertEqual(result, 2)
                self.assertIn("must refer to different files", stderr.getvalue())
                self.assertEqual(self.source.read_bytes(), source_bytes)
                self.assertEqual(set(self.root.iterdir()), {self.source})

    def test_rejects_source_path_aliases(self):
        source_bytes = self.source.read_bytes()
        nested = self.root / "nested"
        nested.mkdir()
        symbolic = self.root / "source-symlink.db"
        symbolic.symlink_to(self.source)
        hard = self.root / "source-hardlink.db"
        os.link(self.source, hard)
        for output in (nested / ".." / self.source.name, symbolic, hard):
            for overwrite in (False, True):
                with self.subTest(output=output, overwrite=overwrite):
                    with self.assertRaisesRegex(
                        export_mbtiles.UserFacingError, "must refer to different files"
                    ):
                        export_mbtiles.convert(self.source, output, overwrite=overwrite)
                    self.assertEqual(self.source.read_bytes(), source_bytes)
                    self.assertEqual(output.read_bytes(), source_bytes)
        self.assertTrue(symbolic.is_symlink())
        self.assertTrue(self.source.samefile(hard))

    def test_preserves_source_named_like_old_temporary_output(self):
        source = self.source.rename(
            self.root / f".{self.destination.name}.{os.getpid()}.tmp"
        )
        source_bytes = source.read_bytes()

        summary = export_mbtiles.convert(source, self.destination, quiet=True)

        self.assertEqual(summary.exported_tiles, 3)
        self.assertEqual(source.read_bytes(), source_bytes)
        self.assertEqual(set(self.root.iterdir()), {source, self.destination})

    def test_exports_paths_with_uri_reserved_and_unicode_characters(self):
        source_bytes = self.source.read_bytes()
        names = (
            "region#1.db",
            "region?mode=rw&immutable=0.db",
            "region%23.db",
            "地图 区域.db",
        )
        for index, name in enumerate(names):
            with self.subTest(name=name):
                directory = self.root / f"区域 #?% {index}"
                directory.mkdir()
                source = directory / name
                source.write_bytes(source_bytes)
                output = directory / "地图 #?%.mbtiles"

                summary = export_mbtiles.convert(source, output, quiet=True)

                self.assertEqual(summary.exported_tiles, 3)
                self.assertEqual(source.read_bytes(), source_bytes)
                self.assertEqual(set(directory.iterdir()), {source, output})
                with closing(sqlite3.connect(output)) as connection:
                    blob = connection.execute(
                        "SELECT tile_data FROM tiles WHERE zoom_level=0"
                    ).fetchone()[0]
                    self.assertEqual(gzip.decompress(blob), vector_tile())

    def test_source_connection_rejects_writes(self):
        source = self.source.rename(self.root / "地图 #?mode=rw%.db")
        source_bytes = source.read_bytes()
        with closing(export_mbtiles._open_source(source)) as connection:
            with self.assertRaisesRegex(sqlite3.OperationalError, "readonly"):
                connection.execute("DELETE FROM tiles")
        self.assertEqual(source.read_bytes(), source_bytes)

    def test_rejects_unsupported_versions_without_replacing_output(self):
        self.destination.write_bytes(b"existing output")
        for version in (0, 4, 7, 999):
            with self.subTest(version=version):
                with closing(sqlite3.connect(self.source)) as connection:
                    connection.execute(f"PRAGMA user_version={version}")
                source_bytes = self.source.read_bytes()

                with self.assertRaisesRegex(
                    export_mbtiles.UserFacingError, f"unsupported .* schema version: {version}"
                ):
                    export_mbtiles.convert(self.source, self.destination, overwrite=True)

                self.assertEqual(self.source.read_bytes(), source_bytes)
                self.assertEqual(self.destination.read_bytes(), b"existing output")
                self.assertEqual(set(self.root.iterdir()), {self.source, self.destination})

    def test_rejects_missing_tables_without_replacing_output(self):
        self.destination.write_bytes(b"existing output")
        original = self.source.read_bytes()
        for table in ("regions", "resources", "tiles"):
            with self.subTest(table=table):
                self.source.write_bytes(original)
                with closing(sqlite3.connect(self.source)) as connection:
                    connection.execute(f"DROP TABLE {table}")
                source_bytes = self.source.read_bytes()

                with self.assertRaisesRegex(
                    export_mbtiles.UserFacingError, f"missing tables: {table}"
                ):
                    export_mbtiles.convert(self.source, self.destination, overwrite=True)

                self.assertEqual(self.source.read_bytes(), source_bytes)
                self.assertEqual(self.destination.read_bytes(), b"existing output")
                self.assertEqual(set(self.root.iterdir()), {self.source, self.destination})

    def test_rejects_missing_columns_without_replacing_output(self):
        self.destination.write_bytes(b"existing output")
        original = self.source.read_bytes()
        for table, column in (
            ("regions", "definition"),
            ("resources", "compressed"),
            ("tiles", "z"),
            ("tiles", "data"),
        ):
            with self.subTest(table=table, column=column):
                self.source.write_bytes(original)
                with closing(sqlite3.connect(self.source)) as connection:
                    connection.execute(
                        f"ALTER TABLE {table} RENAME COLUMN {column} TO unexpected_column"
                    )
                source_bytes = self.source.read_bytes()

                with self.assertRaisesRegex(
                    export_mbtiles.UserFacingError,
                    f"source table {table} is missing required columns: {column}",
                ):
                    export_mbtiles.convert(self.source, self.destination, overwrite=True)

                self.assertEqual(self.source.read_bytes(), source_bytes)
                self.assertEqual(self.destination.read_bytes(), b"existing output")
                self.assertEqual(set(self.root.iterdir()), {self.source, self.destination})

    def test_cleans_temporary_output_after_conversion_failure(self):
        self.destination.write_bytes(b"existing output")
        with closing(sqlite3.connect(self.source)) as connection:
            connection.execute(
                """INSERT INTO tiles(url_template,pixel_ratio,z,x,y,data,compressed,accessed)
                   SELECT 'second-source',pixel_ratio,z,x,y,data,compressed,accessed
                   FROM tiles WHERE z=0"""
            )
            connection.commit()
        source_bytes = self.source.read_bytes()

        with self.assertRaises(sqlite3.IntegrityError):
            export_mbtiles.convert(self.source, self.destination, overwrite=True, quiet=True)

        self.assertEqual(self.source.read_bytes(), source_bytes)
        self.assertEqual(self.destination.read_bytes(), b"existing output")
        self.assertEqual(set(self.root.iterdir()), {self.source, self.destination})

    def test_rejects_dirty_source_sidecar(self):
        source_bytes = self.source.read_bytes()
        for suffix in ("-journal", "-wal", "-shm"):
            with self.subTest(suffix=suffix):
                sidecar = self.source.with_name(self.source.name + suffix)
                sidecar.write_bytes(b"pending")

                with self.assertRaisesRegex(
                    export_mbtiles.UserFacingError, "source has a SQLite sidecar"
                ):
                    export_mbtiles.convert(self.source, self.destination)

                self.assertFalse(self.destination.exists())
                self.assertEqual(self.source.read_bytes(), source_bytes)
                self.assertEqual(sidecar.read_bytes(), b"pending")
                sidecar.unlink()

    def test_cli_help_does_not_require_database(self):
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as raised:
                export_mbtiles.main(["--help"])

        self.assertEqual(raised.exception.code, 0)


class ExportMbtilesV5Tests(ExportMbtilesTests):
    SCHEMA_VERSION = 5


if __name__ == "__main__":
    unittest.main()
