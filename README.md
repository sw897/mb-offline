# mb-offline

Build the archived Mapbox GL Native offline CLI in Docker, download a bounded
region into its SQLite cache, and optionally export vector tiles to MBTiles.
The host-side tools use Python's standard library and require no pip packages.

This is an independent compatibility tool built on a frozen upstream. It is
not an official Mapbox product. Access to hosted map resources, offline storage
and redistribution depend on the applicable service terms and your account
agreement. The software license does not grant rights to Mapbox data. Review
the [Mapbox product terms](https://www.mapbox.com/legal/product-terms) before
using hosted resources; no map data or credentials are included here.

## Requirements and status

- Python 3.9 or newer for the download wrapper and exporter.
- Docker with BuildKit for building and running the native downloader.
- A suitable Mapbox access token for requests to Mapbox-hosted resources.
- The native source is pinned to
  [`f2778251c97ba3403582b9c04290c50f927fd338`](https://github.com/mapbox/mapbox-gl-native/commit/f2778251c97ba3403582b9c04290c50f927fd338).
  Mapbox archived the repository on 2023-08-08.
- CI is configured to build on native Linux amd64 and arm64 runners. The
  Docker image is Linux-only; the Python scripts run on the host.
- The compatibility image still uses Ubuntu 20.04 Focal. Its standard security
  maintenance [ended in May 2025](https://ubuntu.com/20-04). This project does
  not configure Ubuntu Pro/ESM or promise current security coverage. Migration
  to a maintained base requires testing the legacy C++ code and runtime ABI.

## Build

```sh
docker build --tag mbgl-offline:2.0 .
```

The Dockerfile has two stages. The final image contains the executable,
runtime libraries and license notices; it runs as UID/GID `65532:65532`.
The base image digest and native source revision are pinned. Apt repositories
are not snapshotted, so this is not a promise of byte-identical builds.

For a local arm64 build where the official Ubuntu ports mirror is slow:

```sh
docker build \
  --build-arg APT_MIRROR=http://mirrors.aliyun.com/ubuntu-ports \
  --tag mbgl-offline:2.0 .
```

For amd64, an Aliyun mirror override must use its `/ubuntu` repository
instead of `/ubuntu-ports`. The default uses the official Ubuntu repositories.

### Tile count limit

The default build preserves the upstream **6000-tile** offline limit.
This is the native cache's cumulative Mapbox tile limit, not a guarantee that
every estimate below 6000 will fit: styles may have multiple sources, and
native source zooms can differ from the requested grid.

Only if the applicable account terms permit your intended use, an explicit
build argument can select a different positive integer, up to `2147483647`:

```sh
docker build \
  --build-arg OFFLINE_TILE_COUNT_LIMIT=12000 \
  --tag mbgl-offline:custom .
```

Use `--image mbgl-offline:custom` with the wrapper for that image. Changing this
client-side constant does not change service quotas, rate limits, billing or
data rights. Old locally built images keep their old limit until rebuilt.
The configured value is recorded in the image label
`org.mb-offline.offline-tile-count-limit`.

## Preview a download without network access

```sh
python3 scripts/download.py \
  --bbox 116.38,39.90,116.42,39.94 \
  --min-zoom 11 --max-zoom 12 \
  --output data/map.db \
  --dry-run
```

`--dry-run` prints the tile-grid estimate and a command without a token value.
It does not invoke Docker, download resources or create the output database.
The estimate counts spatial grid cells; it does not enumerate every style
resource, predict storage precisely or determine the final request count.

## Credentials and download

Run the following in a terminal to use the hidden token prompt:

```sh
python3 scripts/download.py \
  --bbox 116.38,39.90,116.42,39.94 \
  --min-zoom 11 --max-zoom 12 \
  --output data/map.db
```

The wrapper checks for credentials in this order:

1. `MAPBOX_ACCESS_TOKEN` in the environment.
2. The file selected with `--env-file`, or the local `.env` when no explicit
   file is selected.
3. A hidden interactive prompt.

For file-based configuration, copy `.env.example` to `.env` and fill in your
token locally. `.env` is ignored by Git and excluded from the Docker build
context. Do not put real tokens in command arguments, screenshots, issues,
commits or examples. Rotate any exposed token.

The wrapper passes the token to Docker through an environment variable. On
POSIX hosts it maps the calling user's UID/GID so the mounted output directory
remains writable and output files belong to that user.

`--yes` skips the interactive confirmation; it does not supply credentials.
`--overwrite` replaces the selected output database and its SQLite sidecars.
Without that flag, an existing output is preserved. Downloads are not resumed
automatically.

### GeoJSON input

```sh
python3 scripts/download.py \
  --geojson tests/fixtures/beijing.geojson \
  --min-zoom 11 --max-zoom 12 \
  --output data/region.db \
  --dry-run
```

The wrapper accepts a single Feature or geometry, rejects FeatureCollection,
and estimates its bounding box. The native downloader applies the geometry.
Remove `--dry-run` to perform a confirmed download. Other options include
`--style`, `--pixel-ratio`, `--include-ideographs`, `--image` and
`--api-base-url`; run `python3 scripts/download.py --help` for details.

The wrapper rejects bounding boxes that cross the antimeridian, out-of-range
Web Mercator latitudes and zoom ranges outside 0-22. Zooms above 16 can overzoom
a source without adding source detail.

## Export MBTiles

```sh
python3 scripts/export_mbtiles.py data/map.db data/map.mbtiles
```

The source must be a Mapbox GL Native schema-v6 offline cache without a
`-journal`, `-wal` or `-shm` sidecar. Safely recover an interrupted SQLite
database before exporting; do not delete a sidecar to force conversion.
The source is opened read-only and immutable. The destination is built in a
temporary database and atomically installed; use `--overwrite` only when
replacing an existing destination is intended.

The exporter converts XYZ `y` coordinates to MBTiles TMS rows, skips null tile
records, writes gzip-compressed PBF, and derives bounds, zooms and vector-layer
metadata. It rejects duplicate coordinates across sources/pixel ratios and
invalid tile payloads. Standard MBTiles coordinate lookups use a unique index.

The archive contains tiles and metadata. Styles, sprites and glyphs remain
in the GL cache and require separate handling for offline rendering.
This project does not provide a map renderer or tile server.

### Direct cache queries

After a successful download, the wrapper creates a unique `(z, x, y)` index.
It reports an error and keeps the database if multiple source templates or
pixel ratios make XYZ lookup ambiguous. Direct cache coordinates are XYZ:

```sql
SELECT data, compressed FROM tiles WHERE z=? AND x=? AND y=?;
```

A cache record with `compressed=1` contains a zlib-wrapped DEFLATE stream,
not an HTTP gzip stream. Decompress it or rewrap it correctly before serving
it as gzip. The MBTiles exporter already performs the required repacking.

## Known limitations

- The frozen native downloader can remain in its event loop after an HTTP
  authentication error or tile-limit error. Interrupt it with Ctrl-C; do not
  interpret an existing database or partial progress as completion.
- Concurrency, retries and HTTP behavior are inherited from the native
  downloader. The wrapper does not implement configurable scheduling,
  durable resume, a download timeout or a complete post-download resource
  audit. The native exit status, output presence and XYZ uniqueness are
  checked; users should validate downloaded data for their application.
- Native caches are version-sensitive. Arbitrary old or merged caches and
  styles with several tile rows at one coordinate are not interchangeable
  with a single-source MBTiles archive.
- Tile data, glyphs, styles and credentials must not be committed to this
  repository or bundled in its public releases.

## Validation and CI

```sh
python3 -m unittest discover -s tests
python3 scripts/download.py --help
python3 scripts/export_mbtiles.py --help
sh scripts/check_image.sh mbgl-offline:2.0
```

The Python tests use synthetic fixtures and require no network or token.
The image check runs with Docker networking disabled and verifies CLI help,
dynamic dependencies, non-root defaults, bundled notices, absence of build
tools and writing to a bind mount with the caller's UID/GID.

GitHub Actions runs Python 3.9/3.14 checks and native amd64/arm64 image builds.
The workflow has read-only repository permissions, uses pinned action commits
and does not push images, create releases or call Mapbox APIs. Builds still need
network access to upstream source repositories and Ubuntu package mirrors.
See [CONTRIBUTING.md](CONTRIBUTING.md) for contribution and verification rules.

## License and provenance

Original project contributions are licensed under [Apache-2.0](LICENSE).
The native executable and bundled dependencies retain their licenses; see
[NOTICE](NOTICE), [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) and
[licenses/](licenses/README.md). Notices are included in the runtime image at
`/usr/local/share/mb-offline/`.

[docs/provenance.md](docs/provenance.md) records the confirmed native upstream
and the evidence for likely historical Docker references. Source attribution
does not imply affiliation or endorsement.
