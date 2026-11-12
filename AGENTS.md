# Agent Guide

## Source of truth

`AGENTS.md` is the sole project guide for development agents. Keep it current
when commands, defaults, storage contracts or architecture change. README.md
documents the public user workflow in Chinese and English in the same file.
Synchronize English prose from the user's manually edited Chinese text. Keep
both languages aligned and share command examples; do not add language
switching pages. Do not create case-only duplicates such as
`agents.md`, which collide with this file on case-insensitive filesystems.

## Project and scope

mb-offline packages the frozen Mapbox GL Native `mbgl-offline` CLI in Docker.
The host-side Python download wrapper validates regions, estimates a spatial
tile grid, handles credentials and invokes the container. The exporter converts
schema-v5/v6 cache tiles to MBTiles 1.3. Both host tools use only the Python 3.9+
standard library.

Implement the supported bounded download/export workflow. A new renderer,
replacement HTTP downloader, dataset redistribution or different storage
architecture requires an explicit user request. Native HTTP concurrency,
retries and error handling are inherited from the archived dependency; do not
advertise wrapper-managed scheduling, resume or full resource validation.

## Build contract

- Keep the two-stage Dockerfile: compile in `build`, copy the executable and
  vendor license notices into a minimal runtime containing dynamic libraries.
- The Ubuntu Focal manifest is pinned to
  `sha256:8feb4d8ca5354def3d8fce243717141ce31e2c428701f6682bd2fafe15388214`.
  Focal standard support ended in May 2025; the image does not configure ESM.
  Validate legacy C++ and runtime compatibility before changing the base.
- The native source is pinned to
  `f2778251c97ba3403582b9c04290c50f927fd338` (2023-07-27).
- `patches/linux-skip-glfw.patch` skips the unrelated GLFW example.
- `OFFLINE_TILE_COUNT_LIMIT` defaults to `6000`. The Dockerfile validates an
  explicit integer override in the range 1-2147483647 and verifies the source
  constant before and after replacement. Do not silently restore the historical
  large-limit default.
- Optional `APT_MIRROR` overrides must match the target architecture: an
  Ubuntu ports mirror for arm64, an Ubuntu archive mirror for amd64.
- Optional `SOURCE_URL` and `SOURCE_REVISION` describe this project in OCI
  labels. The upstream revision is recorded separately in
  `org.mb-offline.upstream.revision`.
- The runtime user is `65532:65532`. The POSIX wrapper maps the caller's
  UID/GID for writable output mounts. Direct Docker examples must account
  for bind-mount permissions.
- The download wrapper and image check default to `mbgl-offline`
  (`mbgl-offline:latest`). Explicit image overrides remain supported.
- Never put Mapbox tokens in image arguments, layers, labels or metadata.

## Validation

Run the following for relevant changes, and report anything not run:

```sh
python3 -m unittest discover -s tests
python3 scripts/download.py --help
python3 scripts/export_mbtiles.py --help
docker build --tag mbgl-offline:check .
sh scripts/check_image.sh mbgl-offline:check
```

The image check runs with networking disabled. It checks help, dynamic library
resolution, non-root defaults, writable mounts, included notices and absence of
build tools. Run `actionlint` when changing the workflow, if installed.

CI uses pinned action commits, read-only repository permissions, Python 3.9/3.14
and native amd64/arm64 Linux runners. It builds and checks images locally on the
runner; it does not push images, create releases or contact Mapbox. Tests must
use synthetic fixtures and remain token-free and offline. A full Docker build
requires public source and package downloads.

## Credentials and data

- Credentials come from `MAPBOX_ACCESS_TOKEN`, a local ignored `.env`, an
  explicit ignored `--env-file`, or the hidden interactive prompt. The wrapper
  has no `--token` option. Never print or commit a real credential.
- A token disclosed in chat, issues, screenshots, logs or history must be
  rotated. Do not repeat the value when reporting it.
- Honor applicable service terms, quotas and data rights. Raising a native
  client limit does not change a service limit or grant storage/redistribution
  rights. No account-specific Mapbox permission has been established here.
- Always expose the estimated request region and tile grid before downloads.
  Estimates count cells and can differ from native tile/resource counts.
- Treat interrupted output as incomplete. Do not imply that database presence
  or XYZ uniqueness is a complete download audit. The archived CLI can remain
  in its loop after authentication or tile-limit errors; interrupt it rather
  than using a real token in a smoke test.
- Keep databases, SQLite sidecars, MBTiles/PMTiles, tile payloads, caches and
  credentials out of Git. Preserve deterministic tile math and output behavior.
  The historical Beijing z11-z16 grid estimate is 197321 cells.

## Storage contracts

The native output is a Mapbox GL Native schema-v6 SQLite cache, not MBTiles or
a directory of loose tiles. The native full tile key is
`(url_template, pixel_ratio, z, x, y)`. The wrapper adds a unique `(z, x, y)`
index after download, retaining the database and reporting ambiguity if rows
collide across templates/pixel ratios.

Direct native-cache queries use XYZ. A `compressed=1` tile has a zlib-wrapped
DEFLATE stream, not an HTTP gzip stream. The exporter repacks it as gzip and
flips XYZ y to TMS. MBTiles uses a unique three-coordinate index.

The exporter accepts Mapbox GL Native schema versions 5 and 6. Validate
`PRAGMA user_version`, the required `regions`/`resources`/`tiles` tables and
the columns needed for export. The v5-to-v6 migration only adds
`must_revalidate` to `resources` and `tiles`; export does not read that field
or upgrade the source. Test both actual table layouts, not just changed
version numbers. Reject other versions and incompatible structures.

Open the source read-only/immutable using an escaped `Path.as_uri()` URI;
never interpolate a raw path into `file:`. Reject SQLite sidecars and
source/output aliases, including symbolic and hard links, before writing.
`--overwrite` permits replacing a distinct destination, never the source.
Build output in a unique temporary directory beside the destination, clean
up that owned staging directory on success/failure and atomically replace
the destination only after validation. Do not delete a predictable temporary
path that might be an existing source or another user's file.

The exporter validates payloads and skips null records. Exported tiles/metadata
do not include an offline style, sprites or glyphs. Do not weaken these
contracts or claim compatibility with arbitrary old/merged databases.

## Licensing and provenance

The distributable Agent skill lives in `skills/mb-offline/`. Its SKILL.md holds
operational decisions and references/operations.md supplies optional recipes.
Keep it aligned with the actual CLI; do not add credentials, machine-specific
paths or assumptions about local image aliases. Verify examples offline and
preserve authorization and source-database boundaries. Skill installation or
personal asset-library deployment is separate from editing this repository.
Keep the skill package's LICENSE and NOTICE copies aligned with the root files.

Original project contributions use Apache-2.0. Preserve LICENSE, NOTICE and
THIRD_PARTY_NOTICES.md in source distributions and images. The fixed native
upstream's BSD license and aggregate notices are stored verbatim in
`licenses/mapbox-gl-native/`; review/update notices with the source pin.
The Docker build additionally preserves vendor license/notice files.

Confirmed and inferred provenance are separated in `docs/provenance.md`.
Historical alternative research is in `docs/research-2026-08-17.md`; it is
dated background, not a current compatibility or legal guarantee.

## Repository conventions

- Baseline branch: `main`; use the existing configured remote without embedding
  private remote URLs in public documentation.
- Keep commits atomic and explain why a change is needed.
- Preserve user changes and generated local data. Do not rewrite history,
  publish a repository/image or push without authorization.
- Check `git status --short --branch`, `git remote --verbose`,
  `git log --oneline --decorate -5` and `git ls-remote origin main` when
  verifying repository state.
