---
name: mb-offline
description: Operate an mb-offline checkout to estimate bounded Mapbox vector-tile downloads, inspect GL Native caches, and export MBTiles. Use for this project's CLI workflows, rather than general GIS processing or map rendering.
license: Apache-2.0
---

# mb-offline

Use the existing Python CLIs and Docker image. This skill supplies operational
decisions; the checkout's AGENTS.md, README.md and current CLI help define the
implementation contract.

## Locate the checkout and choose the operation

- When loaded from this repository, the checkout is two levels above this
  skill directory. Verify `scripts/download.py`, `scripts/export_mbtiles.py`
  and `Dockerfile` before running commands there.
- An installed copy of the skill does not contain the downloader. Locate the
  checkout from the user's workspace or supplied path; do not assume that the
  installed skill's parent directories are the project, or scan the whole home
  directory. Ask for the checkout only if the available context cannot locate it.
- Distinguish preview, image preparation, actual download, cache inspection
  and export. A preview request does not authorize a hosted download. Exporting
  an existing cache does not require a token or Docker.
- Read [operations.md](references/operations.md) only for the command recipe
  or cache inspection relevant to the selected operation. Run from the verified
  checkout so credential-file resolution is predictable; use absolute paths
  when the user's inputs or outputs are elsewhere.

## Prepare a bounded download

1. Resolve the requested bbox or supplied GeoJSON, zoom range, output, style
   and any image choice. Do not invent administrative boundaries for a named
   place or replace the user's region with an example. Ask only for missing
   information that materially changes coverage or request volume. An unused
   path under `data/` is a reasonable output choice when none is specified.
2. Run the wrapper with `--dry-run` before the actual download. Report its
   spatial grid estimate and whether it is a bbox count or a GeoJSON bbox upper
   bound. This is not an enumeration of every style resource or API request.
   Do not infer precise storage, charges or throughput from this number.
3. Inspect the selected local image's ID, architecture and
   `org.mb-offline.offline-tile-count-limit` label. The CLI defaults to
   `mbgl-offline` (`:latest`), but a local alias may select a custom large-limit
   build. A missing label means the limit is unknown, not necessarily 6000.
4. If the user's existing authorization covers this bounded operation, proceed
   without another confirmation; `--yes` supports that non-interactive run.
   Surface a materially large estimate before launching. Clarify scope or
   budget only when the existing request does not cover it. Do not add
   `--overwrite` unless replacement is already authorized; preserve existing
   outputs or choose an unused destination instead.

The wrapper takes `--min-zoom`/`--max-zoom`, not the native CLI's camel-case
flags. It accepts one GeoJSON Feature or geometry and rejects FeatureCollection.
Do not silently select the first feature or discard the rest. Its bbox does
not cross the antimeridian and its integer zoom range is 0-22. Source detail
can stop before the requested maximum zoom.

## Use credentials and control the run

- Use `MAPBOX_ACCESS_TOKEN`, an ignored local `.env`, an explicitly selected
  ignored `--env-file`, or the wrapper's hidden terminal prompt. There is no
  wrapper `--token` option. Do not ask for a token in chat, read credential files
  into tool output, put secrets in URLs/arguments, or bake them into an image.
  If a non-interactive run lacks credentials, ask the user to configure them
  through one of those local mechanisms.
- Use a controllable process/session for actual downloads and retain its job
  handle. On an authentication, permission or tile-limit failure, interrupt
  that owned run with SIGINT and report the cause. The archived native CLI can
  remain in its event loop after errors. Do not kill unrelated containers,
  restart the Docker daemon or repeatedly relaunch a failing request.
- Treat interrupted output as incomplete. The wrapper has no durable resume,
  configurable concurrency/rate/retry flags or complete resource audit; do
  not invent these options or call a fresh overwriting run a resume.
- Keep native output and credential-bearing URLs out of user-facing reports
  unless sanitized. Avoid full Docker environment dumps. Report paths,
  aggregate counts and sanitized error reasons rather than cached URL tables.

## Preserve image choices

Reuse an existing selected image for ordinary downloads. Building or retagging
is a separate operation when requested or needed to prepare a missing image.
Prefer an unused build tag and pass `--image` for that run rather than replacing
the caller's working default. If replacement is requested, preserve the old
image ID/tag and the user's selected configuration.

A standard build uses a 6000-tile limit. An explicitly selected
`OFFLINE_TILE_COUNT_LIMIT` can be 1-2147483647. Do not silently raise it to fix a
failed request, assume `large` exists elsewhere, or overwrite a large-limit
local alias with a standard build. Changing this client constant does not
grant service quotas, change billing or establish data storage/redistribution
rights. Preserve the user's bounded scope and applicable account agreement.

## Inspect or export a cache

- Current downloads create schema-v6 SQLite caches, not MBTiles. The exporter
  supports schema-v5 and schema-v6 without upgrading or modifying the source.
  Stop writers before inspection/export. Use read-only, immutable inspection
  and reject `-journal`, `-wal` or `-shm` sidecars; do not delete them to force
  an export.
- Verify `PRAGMA user_version` is 5 or 6, required tables and export columns.
  The v6-only `must_revalidate` field is not needed for export; do not reject
  v5 for lacking it. Other versions and incompatible structures are rejected.
  A `.db` suffix or familiar table names alone do not establish compatibility.
- Export through `scripts/export_mbtiles.py` to a distinct file, including
  when paths involve symbolic or hard links. `--overwrite` never authorizes
  replacing the source. Keep shell paths quoted; read-only inspection must
  use `Path.as_uri()` rather than interpolating raw paths into a SQLite URI.
  Let the exporter perform XYZ-to-TMS conversion and zlib-to-gzip repacking.
  Renaming a native cache does not convert it to MBTiles.
- Duplicate XYZ coordinates across source templates/pixel ratios are ambiguous
  for this exporter. Preserve the cache and explain the source conflict; do not
  drop rows or arbitrarily choose a source to make conversion pass.
- MBTiles contains tiles and metadata; styles, sprites and glyphs require
  separate offline handling. Tile counts and a valid coordinate index do not
  establish that an interrupted or previously supplied cache is complete.

## Report the observed outcome

For the selected operation, report the image/limit when relevant, requested
coverage and zooms, estimate, output path/format and observed completion or
failure. A successful wrapper exit and completion message establish the
wrapper's result, not an independent audit of every remote resource. For an
existing cache of unknown history, distinguish successful conversion from
unknown download completeness. Include only what the task needs; never expose
credentials or claim an API download was tested when only offline checks ran.
