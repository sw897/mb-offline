# Changelog

## Unreleased

- Pass the numeric CJK glyph flag expected by the native CLI, with a
  network-disabled parser smoke check.
- Traverse nested GeoJSON GeometryCollections and reject invalid members.
  Include geometry endpoint cells in conservative estimates so boundary
  points/lines are not reported as zero; preserve existing bbox counts.
- Preserve source caches when export paths alias the same file, and encode
  special characters in read-only SQLite URIs. Use unique temporary staging
  directories to avoid deleting existing files.
- Support schema-v5/v6 exports with explicit version/column checks and offline
  regression tests for both layouts, source preservation and output cleanup.
- Add a distributable Agent skill for bounded downloads, read-only cache
  inspection and MBTiles export, with offline-verified operation recipes.
- Default the download wrapper and runtime checks to `mbgl-offline` (`:latest`),
  matching the documented build image name.
- Prepare the local project for open-source publication under Apache-2.0,
  with usage documentation and preserved upstream/dependency notices.
- Keep the two-stage Docker build and restore the native default tile count
  limit of 6000; allow an explicit validated build-time override.
- Run the image as a non-root user and map the host UID/GID in the POSIX
  download wrapper for writable output mounts.
- Record project and upstream revisions separately in image labels.
- Add offline Python checks and native amd64/arm64 Docker checks in CI.

Existing locally built images keep their previous limits and metadata until
rebuilt. The native upstream remains pinned and the base remains Focal.
