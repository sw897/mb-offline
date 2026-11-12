# Changelog

## Unreleased

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
