# Agent Guide

## Source of Truth

This file is the single authoritative project guide for coding and research agents. Keep it current whenever project architecture, tooling, commands, constraints, or conventions change. Do not duplicate authoritative guidance in agent-specific instruction files.

`agents.md` and `claude.md` must contain only a reference to this guide.

## Project Overview

`mb-offline` is a tool for downloading Mapbox-hosted vector tiles so map data can be used offline. The historical implementation adapts the offline-cache mechanism from Mapbox GL Native and packages the resulting `mbgl-offline` CLI for Docker use.

The repository contains the selected Docker-based native implementation, its patches, a Python download wrapper, offline tests, agent documentation, and ignore rules. The Docker build compiles the pinned upstream C++ project directly.

## Product Scope

Core workflows should support:

- Selecting a Mapbox style and the geographic bounds or GeoJSON geometry and zoom range to download.
- Enumerating the exact vector tiles required for the request.
- Downloading tiles with controlled concurrency, retries, backoff, and API-error handling.
- Storing tiles in a deterministic local layout suitable for offline serving or later processing.
- Producing clear progress and failure reports without exposing credentials.
- Keeping downloaded data usable with common offline vector-map tooling.

## Current Build

The selected approach is the patched Mapbox GL Native `mbgl-offline` binary, packaged as a Docker image.

### Inputs

- `Dockerfile` performs a multi-stage build and creates a runtime image with only the executable and its dynamic dependencies.
- The base image is the multiarch Ubuntu Focal manifest digest `sha256:8feb4d8ca5354def3d8fce243717141ce31e2c428701f6682bd2fafe15388214`.
- Mapbox GL Native is pinned to commit `f2778251c97ba3403582b9c04290c50f927fd338` (`Fix macos build`, 2023-07-27).
- `patches/offline-tile-limit.patch` changes `DEFAULT_OFFLINE_TILE_COUNT_LIMIT` from `6000` to `2147483647`.
- `patches/linux-skip-glfw.patch` prevents CMake from configuring the unrelated GLFW sample target. The `mbgl-offline` target does not require GLFW, but upstream Linux configuration unconditionally includes that subdirectory.
- The image does not include a Mapbox token. Pass `MAPBOX_ACCESS_TOKEN` at runtime.

### Build Commands

Official Ubuntu repositories:

```sh
docker build --tag mbgl-offline:2.0 .
```

Aliyun mirror, useful when the official ports repository is slow or intermittent:

```sh
docker build \
  --build-arg APT_MIRROR=http://mirrors.aliyun.com/ubuntu-ports \
  --tag mbgl-offline:2.0 .
```

The mirror value shown above is for non-amd64 Ubuntu ports repositories, including the verified `linux/arm64` build. For an amd64 build using the Aliyun mirror, use its `/ubuntu` repository instead.

### Verification

Verified on 2026-08-17 for `linux/arm64`:

```sh
docker run --rm mbgl-offline:2.0 --help
docker run --rm --entrypoint /bin/sh mbgl-offline:2.0 \
  -c 'ldd /usr/local/bin/mbgl-offline'
docker image inspect mbgl-offline:2.0 \
  --format 'architecture={{.Architecture}} revision={{index .Config.Labels "org.opencontainers.image.revision"}}'
```

`--help` and `ldd` completed successfully, and the image label reported the pinned revision. An unauthenticated zero-area request reached Mapbox and returned `HTTP 401`; the upstream CLI then remained in its run loop until interrupted. Send `SIGINT` (`Ctrl-C` when interactive) to stop it gracefully. Do not use a real token for this smoke test.

The verified unpacked image size is 36,247,441 bytes. Do not treat a locally generated image manifest ID as permanent; BuildKit attestation metadata can change it even when the image config and layers are cached.

## Download Wrapper

`scripts/download.py` is the supported entry point for regional downloads. It validates input, estimates the tile grid before starting, stages GeoJSON input, invokes the Docker image, and keeps Mapbox credentials out of command-line arguments and repository files.

### Credentials

The script intentionally has no `--token` option. Supply a rotated token through one of:

1. `MAPBOX_ACCESS_TOKEN` in the environment.
2. A local ignored `.env` file containing `MAPBOX_ACCESS_TOKEN=...`.
3. An explicit ignored file with `--env-file FILE`.
4. The secure interactive prompt when no file or environment value is present.

The value is passed to Docker as an environment variable and is never printed. A token shared in chat, screenshots, logs, or shell history must be rotated.

### Bounding-Box Download

```sh
./scripts/download.py \
  --bbox 115.4168,39.4420,117.5083,41.059 \
  --min-zoom 11 \
  --max-zoom 16 \
  --output map.db
```

Add `--dry-run` to inspect the estimate and sanitized Docker command without network access. Add `--yes` for non-interactive confirmed runs and `--overwrite` only when replacing an existing database is intended.

### GeoJSON Region

```sh
./scripts/download.py \
  --geojson region.geojson \
  --min-zoom 11 \
  --max-zoom 16 \
  --output map.db
```

GeoJSON input must be one Feature or geometry. The upstream CLI rejects `FeatureCollection`; extract the needed feature first. The script reports a conservative bounding-box tile estimate for GeoJSON; the native downloader applies the actual geometry.

Other useful options are `--style`, `--pixel-ratio`, `--include-ideographs`, `--api-base-url`, and `--image`. Use `--help` for the full contract.

## MBTiles Export

`scripts/export_mbtiles.py` converts a Mapbox GL Native offline-cache schema-v6 database to an MBTiles 1.3 vector-tile archive:

```sh
./scripts/export_mbtiles.py data/map-hk.db data/map-hk.mbtiles
```

The exporter opens the source read-only and immutable, rejects source databases with SQLite sidecars, reverses XYZ `y` coordinates to MBTiles TMS rows, skips null tile records, and writes tiles as gzip-compressed PBF. Compressed GL-cache tiles keep their original DEFLATE body; only the zlib wrapper is replaced. It derives bounds, zooms, and vector-layer field metadata, then creates the destination through a temporary database and atomic replacement.

The output contains tile data and MBTiles metadata, not a complete self-contained Mapbox style. Styles, sprites, and glyph resources remain in the GL cache and require separate handling for offline rendering. Use `--overwrite` only when intentionally replacing an existing MBTiles file. If the source has a `-journal`, `-wal`, or `-shm` sidecar, recover or remove that sidecar safely before conversion.

## Historical Reference

The reference implementation is a Docker build based on [mapbox/mapbox-gl-native](https://github.com/mapbox/mapbox-gl-native). GitHub archived that repository on 2023-08-08, so this project must treat it as a frozen dependency rather than an actively maintained upstream.

Conceptually, the implementation reuses the native mobile offline-cache path: define an offline region, let Mapbox GL Native resolve the style and its resources, and store the downloaded resources in its SQLite cache through the database file source.

### Build Path

The historical Docker image starts from `ubuntu:focal`, installs the Mapbox GL Native C/C++ build dependencies and Node.js 10, clones the upstream repository, initializes its submodules, configures CMake, builds the `mbgl-offline` target, copies the executable to `/mbgl-offline`, and removes most build-only packages afterward. It uses Aliyun package mirrors in the historical recipe.

Before building, it modifies `src/mbgl/util/mapbox.cpp`:

```text
DEFAULT_OFFLINE_TILE_COUNT_LIMIT: 6000 -> 2147483647
```

This removes the client-side offline tile-count guard only. It does not override Mapbox service quotas, pricing, permissions, rate limits, or server-side restrictions. Current Mapbox offline documentation also states that SDK limits must not be circumvented and that offline data may not be preloaded, bundled, or redistributed. A replacement or wrapper must still estimate and expose the requested tile count before starting a potentially very large download.

The historical recipe cloned the upstream default branch rather than pinning a commit. The current `Dockerfile` supersedes it with the exact revision and reviewable patches described above.

The supplied historical Dockerfile was:

```dockerfile
FROM ubuntu:focal

ENV DEBIAN_FRONTEND=noninteractive

RUN sed -i s@/archive.ubuntu.com/@/mirrors.aliyun.com/@g /etc/apt/sources.list
RUN apt-get clean
RUN apt-get update \
    && apt-get install -y git build-essential cmake curl libjpeg-dev libicu-dev libgl1-mesa-dev pkg-config libuv1-dev libglew-dev libsdl2-dev libsdl2-image-dev libglm-dev libfreetype6-dev libglfw3-dev libglfw3 \
    && curl -sL https://deb.nodesource.com/setup_10.x | bash - \
    && apt-get install -y nodejs \
    && apt-get install -y zlib1g-dev libx11-dev libcurl4-openssl-dev xvfb \
    && git clone https://github.com/mapbox/mapbox-gl-native.git \
    && cd mapbox-gl-native \
    && sed -ie 's/const uint64_t DEFAULT_OFFLINE_TILE_COUNT_LIMIT = 6000;/const uint64_t DEFAULT_OFFLINE_TILE_COUNT_LIMIT = 2147483647;/g' src/mbgl/util/mapbox.cpp \
    && git submodule update --init --recursive \
    && cmake . -B build \
    && cmake --build build --target mbgl-offline \
    && cp build/bin/mbgl-offline /mbgl-offline \
    && cd / \
    && rm -Rf /mapbox-gl-native \
    && apt-get remove -y git build-essential cmake curl nodejs zlib1g-dev libx11-dev libcurl4-openssl-dev xvfb \
    && apt autoremove -y \
    && apt-get install -y libgl1-mesa-dev libcurl4 libpng-dev

ENTRYPOINT ["/mbgl-offline"]
```

The original recipe used `make mbgl-offline` inside `build`; the equivalent `cmake --build` form is recorded above. This block is provenance only; the tested current implementation is the committed multi-stage `Dockerfile`.

### CLI Contract

The upstream `mbgl-offline` binary implements the relevant interface:

- Access token: `--token` or `MAPBOX_ACCESS_TOKEN`; prefer the environment variable and never place a token in shell history, documentation, Docker metadata, logs, or tests.
- Style: `--style` / `-s`, defaulting to `mapbox://styles/mapbox/streets-v11`.
- Rectangular region: `--north`, `--west`, `--south`, and `--east`.
- Geometry region: `--geojson <file>`.
- Zoom and display: `--minZoom` (default `0`), `--maxZoom` (default `15`), and `--pixelRatio` (default `1`).
- CJK glyphs: `--includeIdeographs`.
- Output: `--output` / `-o`, default `offline.db`.
- Other legacy options: `--apiBaseURL`, `--merge`, and `--input`.

The historical Docker usage mounts a host directory at `/workdir` and writes a SQLite offline-cache database such as `map.db`. This is Mapbox GL Native's cache format, not a plain `z/x/y` directory of loose vector tiles. Preserve this distinction when documenting compatibility or designing a new export format.

Use a sanitized invocation shape such as:

```sh
docker run --rm \
  --env MAPBOX_ACCESS_TOKEN \
  --volume "$PWD:/workdir" \
  --workdir /workdir \
  mbgl-offline:2.0 \
  --style mapbox://styles/mapbox/streets-v11 \
  --minZoom 11 --maxZoom 16 \
  --north 41.059 --west 115.4168 \
  --south 39.4420 --east 117.5083 \
  --output map.db
```

Never reproduce a real token in examples or repository content. Tokens shared in chat or command history should be considered exposed and rotated.

## Alternative Research

Research date: 2026-08-17.

### Decision Summary

1. **Best compliant Mapbox-native path: Mapbox Atlas v3.** It is the official self-hosted/air-gapped product and provides licensed Mapbox tiles, styles, fonts, sprites, and Mapbox GL JS as deployable assets. It requires an Atlas license and is not a free replacement for this tool.
2. **Best replacement for a custom file-producing tool, only with explicit permission: direct API downloader writing MBTiles or PMTiles.** Parse a Mapbox style into its vector sources, fetch TileJSON metadata, download composite `pbf` tiles for the requested grid, and package a standard archive plus rewritten style assets. This removes the archived native build and proprietary cache schema, but Mapbox terms, account permissions, and any required commercial license are gating requirements. Do not treat this route as generally permitted just because the API is publicly documented.
3. **Best open-data path: OSM extract -> tilemaker -> MBTiles/PMTiles -> MapLibre.** This is the most maintainable and redistributable route when OpenStreetMap-derived data is acceptable. It does not reproduce Mapbox Streets coverage, schema, or cartography.
4. **Keep patched `mbgl-offline` only as a compatibility fallback.** It depends on an archived C++ project, old Ubuntu/Node packages, and a proprietary offline-cache database. Raising its client-side tile limit does not make large downloads advisable or compliant.

### Option Comparison

| Path | Strengths | Weaknesses |
| --- | --- | --- |
| Mapbox Atlas v3 | Official Mapbox data and rendering assets; designed for private, offline, and air-gapped deployment; production support | License/contact required; deploys a data bundle rather than generating arbitrary regional `map.db` files |
| Official mobile SDK offline regions | Fully supported device-side workflow; handles style packs, tile packs, updates, cancellation, and expiration | SDK-managed TileStore; 750 cumulative tile-pack limit; not a portable tile archive generator |
| Direct Mapbox API + MBTiles/PMTiles | Standard output; testable pure logic; resume and packaging under project control; can include style, sprites, and glyph ranges | Must implement resource enumeration and reliability carefully; must verify redistribution rights and Mapbox terms |
| OSM + tilemaker + MapLibre | Open standards; no Mapbox dependency; supports MBTiles and PMTiles; actively maintained ecosystem | Different data quality/schema; requires OSM extracts and OpenMapTiles/Protomaps-compatible styles |
| Existing generic downloaders | Quick prototypes | Generally lack Mapbox style parsing, robust retries/resume, safe credential handling, or production tests |
| Patched `mbgl-offline` | Historical behavior; already resolves style and cache details | Archived upstream; frozen native stack; proprietary SQLite cache; client-side tile-limit patch |

### Official Mapbox Capabilities

- The [Vector Tiles API](https://docs.mapbox.com/api/maps/vector-tiles/) exposes `GET https://api.mapbox.com/v4/{tileset_id}/{z}/{x}/{y}.{format}`, supports `.mvt` and `.vector.pbf`, composites up to 15 tileset IDs, and supports style-optimized tiles through a `style` query parameter.
- The [Styles API](https://docs.mapbox.com/api/maps/styles/) returns style JSON, sprite resources, and licensed style ZIP bundles. A style JSON's `sources` identify the vector tilesets, while `sprite` and `glyphs` identify non-tile resources needed for rendering.
- The [Fonts API](https://docs.mapbox.com/api/maps/fonts/) serves glyph ranges in 256-codepoint PBF chunks. An offline package must either include the required glyph ranges or replace them with compatible self-hosted fonts.
- Current Android/iOS SDK offline storage is built from style packs, tile regions, and tile packs. Official docs state that users must retrieve Mapbox offline data from Mapbox servers, that SDK limits must not be circumvented, and that redistribution, preloading, or bundling is not allowed. The cumulative default is 750 unique tile packs.
- [Mapbox Atlas v3](https://docs.mapbox.com/atlas/guides/) deploys licensed Mapbox maps on private infrastructure, including air-gapped networks. Its Mapbox Streets v8 high-resolution dataset is approximately 292 GB at z16. Atlas v3 uses PMTiles for map tilesets and can also host custom PMTiles.

Style-optimized tiles reduce payload size, but they bake in the referenced style's filters and zoom ranges. Do not use them when users expect to arbitrarily restyle or broaden layer filters offline.

### Existing Tool Review

- [`tilepack`](https://github.com/eknowles/tilepack): TypeScript/Bun CLI that downloads an XYZ template to MBTiles. It exposes concurrency, token substitution, and format metadata, but its current implementation logs failed tiles without failing the run, does not implement its advertised retry option, has no durable resume, does not parse Mapbox styles, and has no declared license. Useful as a design reference, not as the production base.
- [`PBFdownloader`](https://github.com/HaukeDE/PBFdownloader): Python script for low-rate PBF tile downloads into MBTiles, with sequential state and throttling. It does not understand Mapbox styles, has primitive retry behavior, depends on a relatively stale `pymbtiles`, uses a hardcoded browser user agent, and can log request headers on errors. Its CC BY-SA-4.0 license may also impose attribution/share-alike obligations on derivative code.
- [`mapbox/mbutil`](https://github.com/mapbox/mbutil): useful MBTiles import/export reference, but archived and not a downloader.
- [`rclark/get-tile`](https://github.com/rclark/get-tile): historical single-tile Mapbox fetcher, not a regional downloader.
- No reviewed off-the-shelf project provided a complete, maintained, credential-safe Mapbox-style-to-MBTiles workflow.

### Open Standards Stack

- [MBTiles](https://github.com/mapbox/mbtiles-spec) is a SQLite container for raster or vector tiles and is broadly supported.
- [PMTiles](https://github.com/protomaps/PMTiles) is a single-file archive optimized for HTTP range requests and static storage. PMTiles works well with MapLibre through its protocol adapter.
- [MapLibre GL JS](https://github.com/maplibre/maplibre-gl-js) and [MapLibre Native](https://github.com/maplibre/maplibre-native) are actively maintained renderers for self-hosted vector tiles. MapLibre Native supports local MBTiles sources; web usage normally serves tiles through HTTP or a custom protocol.
- [tilemaker](https://github.com/systemed/tilemaker) converts OSM PBF extracts directly to MBTiles or PMTiles without a database stack. It includes OpenMapTiles-compatible configurations and Docker images.
- [Martin](https://github.com/maplibre/martin) and [tileserver-gl](https://github.com/maptiler/tileserver-gl) are actively maintained options for serving MBTiles/PMTiles locally or privately.

For the historical Beijing-area request (`north=41.059`, `west=115.4168`, `south=39.4420`, `east=117.5083`, z11-z16), standard Web Mercator XYZ intersection produces **197,321 tiles**. The earlier `195,808` figure incorrectly subtracted the east and south boundary tile even when the boundary fell inside a tile. Any run must show this estimate, expected storage, request rate, and continuation plan before starting.

Out of scope unless explicitly requested:

- Building a general-purpose map renderer.
- Reproducing the complete Mapbox platform.
- Bulk scraping beyond the tile range explicitly requested by the user.
- Handling raster-only workflows before vector-tile download is established.

## Non-Negotiable Requirements

- Never hard-code Mapbox access tokens or copy credentials into files, logs, examples, tests, or command output.
- Accept credentials through environment variables, a local ignored configuration file, or an explicit user-supplied runtime value.
- Honor Mapbox API terms, rate limits, and service availability. Use bounded concurrency and retry with exponential backoff and jitter.
- Keep tile enumeration and output layout deterministic. The same style, bounds, zoom range, and tileset version should produce the same tile list and storage paths.
- Treat interrupted downloads as incomplete. Do not mark a run successful unless all requested tiles are downloaded and validated.
- Validate tile content where practical, including HTTP status, content type, body size, and gzip integrity where applicable.
- Preserve style, source, layer, and tileset metadata needed to reconstruct or serve the offline map.
- Write clear user-facing errors for invalid bounds, zoom ranges, authentication failures, permission failures, missing tilesets, and network failures.
- Check the estimated resource count before launching a run because the historical client-side tile limit was raised to `2147483647`.
- Treat the Mapbox GL Native offline database schema as version-sensitive; verify compatibility before opening or exporting old or merged databases.

## Initial Design Direction

- Prefer a small CLI-oriented implementation with a library core that can be tested without network access.
- Separate these responsibilities:
  - Tile request parsing and validation
  - Style or tileset metadata retrieval
  - Tile-grid enumeration
  - Download scheduling
  - Offline storage and packaging
  - Progress and result reporting
- Keep pure logic, such as bounds-to-tile-grid enumeration, independent of HTTP clients.
- Use structured data models rather than ad hoc dictionaries or string parsing where the chosen language supports them.
- Add fixture-based tests for metadata parsing, tile enumeration, path layout, retry behavior, and interrupted-run handling.
- Add a small number of recorded or contract-style network tests, kept separate from fast offline tests.

## Repository Conventions

Current implementation facts:

- Implementation language and build system: upstream C++14 with CMake, compiled in Docker on Ubuntu Focal.
- Required host tool: Docker or an equivalent BuildKit-capable builder.
- Download wrapper and MBTiles exporter runtime: Python 3.9 or newer; standard library only.
- Primary build and validation commands are documented under `Current Build`.
- Offline implementation tests: `python3 -m unittest discover -s tests`.
- Git baseline branch: `main`.
- Git remote: use the existing locally configured `origin`; do not embed private remote URLs in public documentation.
- Keep commits atomic and describe why the change is needed, not merely which files changed.
- Keep generated tile data, caches, credentials, `.env` files, and large binary artifacts out of version control.
- Never commit access tokens, downloaded tile databases, Docker build cache, or other machine-local state.
- Update this guide when adding public commands, changing storage formats, altering retry/concurrency behavior, or introducing a new architecture boundary.
- Before claiming a change works, run the documented validation commands and report any that could not be run.

Git verification:

```sh
git status --short --branch
git remote --verbose
git log --oneline --decorate -5
git ls-remote origin main
```

## Current Unknowns

- Whether the project has a Mapbox contract, Atlas license, or other explicit permission for persistent regional storage or redistribution.
- Required export formats beyond the Mapbox GL Native SQLite cache.
- Whether styles must be rewritten or served locally so `map.db` can be used without the original remote resources.
- `linux/amd64` build behavior; the current verified image is `linux/arm64`.
- Image publication and distribution workflow.
