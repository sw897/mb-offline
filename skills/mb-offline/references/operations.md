# Operation recipes

Use only the recipe relevant to the user's request. Commands assume the
verified mb-offline checkout is the working directory. Region, zoom, image and
output values below are examples, not a default request or a download budget.
Keep user-supplied paths quoted and preserve existing authorization.

## Preview and image selection

A bbox is `WEST,SOUTH,EAST,NORTH`. Preview needs no Docker or token:

```sh
python3 scripts/download.py \
  --bbox 116.38,39.90,116.42,39.94 \
  --min-zoom 11 --max-zoom 12 \
  --output data/map.db \
  --dry-run
```

These example bounds cover four grid cells across those two zooms. Other
styles/sources can require a different native tile/resource count. If the
output already exists, do not add `--overwrite` just to bypass that conflict
for a later actual run; resolve the output choice first.

Inspect only the selected image's relevant metadata, not its full environment:

```sh
docker image inspect mbgl-offline --format 'architecture={{.Architecture}} limit={{index .Config.Labels "org.mb-offline.offline-tile-count-limit"}} upstream={{index .Config.Labels "org.mb-offline.upstream.revision"}}'
```

For `--image IMAGE`, inspect that exact tag instead. The default `mbgl-offline`
means `mbgl-offline:latest`, not a guaranteed limit. Do not infer a label value
when inspecting a historical image without the label.

## Prepare an image when needed

Confirm that the example build tag is unused or replacement is authorized.
Building under an alternate tag avoids replacing the existing default:

```sh
docker build --tag mbgl-offline:agent-build .
sh scripts/check_image.sh mbgl-offline:agent-build
```

A standard build preserves 6000. Only for an explicitly selected custom limit:

```sh
docker build \
  --build-arg OFFLINE_TILE_COUNT_LIMIT=12000 \
  --tag mbgl-offline:agent-build .
```

The limit must be a decimal integer without leading zeros, in 1-2147483647.
Use `--image mbgl-offline:agent-build` for that build. Building this tag alone
does not make it the wrapper default. Do not retag unless requested; retain
an existing image's ID or a backup tag before replacing its alias.

`APT_MIRROR` must match the architecture: `/ubuntu-ports` for arm64, `/ubuntu`
for amd64 when using the documented Aliyun mirror. Preserve the source pin,
two-stage build and notices; the compatibility base is Focal and its security
maintenance limitations are described in the project README.

## Run an already authorized download

Once the preview and existing authorization cover this actual request, and
credentials are available locally:

```sh
python3 scripts/download.py \
  --bbox 116.38,39.90,116.42,39.94 \
  --min-zoom 11 --max-zoom 12 \
  --output data/map.db \
  --yes
```

Omit `--yes` when the user intentionally wants the wrapper's terminal
confirmation. It does not supply credentials or override the image limit.
Keep an explicitly selected `--image`/`--style`/`--env-file` choice in both
preview and actual invocation. Use an existing ignored credential file; do not
create a file containing a token copied from chat or print its contents.

For a supplied Feature or geometry:

```sh
python3 scripts/download.py \
  --geojson tests/fixtures/beijing.geojson \
  --min-zoom 11 --max-zoom 12 \
  --output data/region.db \
  --dry-run
```

Use the user's GeoJSON path in a real task. Remove `--dry-run` and add `--yes`
only for the already authorized download. Wrapper confirmation, source HTTP
retries and resuming a partial cache are different things: no resume option is
implemented. `--overwrite` deletes the selected output and its SQLite sidecars.

HTTP 401/403 or a tile-limit error can leave the native event loop running.
Send SIGINT through the owned job/session handle, preserve partial output and
report the terminal error. Do not interpret file growth as completion or
silently rebuild with a larger limit. A multi-source XYZ-index failure also
keeps the cache; distinguish it from an empty output or a missing image.

## Inspect a quiescent cache read-only

This optional standard-library recipe emits counts and schema information,
not resource URLs or payloads. Run only after the writer has stopped. It scans
tile records, so avoid repeated full scans of a large cache merely for progress.
The result is a summary, not proof of complete downloads or valid MVT payloads.

```sh
python3 - data/map.db <<'PY'
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import sys

source = Path(sys.argv[1]).expanduser().resolve()
if not source.is_file():
    raise SystemExit("Source cache does not exist")
if any(Path(str(source) + suffix).exists() for suffix in ("-journal", "-wal", "-shm")):
    raise SystemExit("SQLite sidecar present; preserve it and recover safely first")
with closing(sqlite3.connect(source.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version not in (5, 6):
        raise SystemExit("Expected a Mapbox GL Native schema-v5/v6 cache")
    required_columns = {
        "regions": {"id", "definition"},
        "resources": {"id", "kind", "data", "compressed"},
        "tiles": {"z", "x", "y", "data", "compressed"},
    }
    tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if not required_columns.keys() <= tables:
        raise SystemExit("Required native-cache tables are missing")
    for table, required in required_columns.items():
        columns = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        if not required <= columns:
            raise SystemExit(f"Required native-cache columns are missing in {table}")
    count, payloads, low, high = db.execute(
        "SELECT COUNT(*), SUM(data IS NOT NULL), MIN(z), MAX(z) FROM tiles"
    ).fetchone()
    summary = {
        "schema_version": version,
        "regions": db.execute("SELECT COUNT(*) FROM regions").fetchone()[0],
        "resources": db.execute("SELECT COUNT(*) FROM resources").fetchone()[0],
        "tile_records": count,
        "non_null_tiles": payloads or 0,
        "min_record_zoom": low,
        "max_record_zoom": high,
    }
print(json.dumps(summary))
PY
```

Do not run SQLite recovery, checkpoint, vacuum, writes or journal deletion
against a user's source as part of a read-only inspection. If recovery is
needed, establish the writer state and use a preserved copy and a specifically
authorized recovery plan.

## Export and explain compatibility

For a schema-v5/v6 source with compatible tables/columns, no active writer and
no SQLite sidecars:

```sh
python3 scripts/export_mbtiles.py data/map.db data/map.mbtiles
```

The exporter checks the schema version and required columns, reads the source
without modifying it and installs the destination through a unique temporary
directory with atomic replacement. The v6-only `must_revalidate` columns are
not needed for export; do not migrate a v5 source merely to convert its tiles.
Use a new output unless replacing a distinct existing destination is authorized.
Identical paths, symbolic links and hard links to the source are rejected even
with `--overwrite`. Quote paths containing spaces or URI-reserved characters;
the exporter encodes SQLite URI paths, including `#`, `?` and `%`.

Its output uses TMS rows and gzip PBF. Direct native-cache queries use XYZ and
`compressed=1` data is zlib-wrapped, not already HTTP gzip.

Multiple tile rows at a single XYZ coordinate cannot be safely collapsed by
choosing the first row. The exporter has no source-selection flag; report that
limitation rather than inventing one or deleting rows. TileJSON/style-derived
metadata does not bundle sprites, glyphs or a rewritten offline style.

For a cache of unknown history, successful export verifies conversion of the
present records, not completion of the originally requested region. If the user
explicitly wants a partial export, identify it as partial and preserve its source.
