# Contributing

Keep changes focused and explain the user-visible behavior and validation.
Use Python 3.9-compatible standard-library code for the host tools. The native
upstream is frozen; keep source pins and any patches explicit and reviewable.

Before submitting a change:

```sh
python3 -m unittest discover -s tests
python3 scripts/download.py --help
python3 scripts/export_mbtiles.py --help
```

For Dockerfile or runtime changes, rebuild an image and run:

```sh
docker build --tag mbgl-offline:check .
sh scripts/check_image.sh mbgl-offline:check
```

If available, validate the workflow with `actionlint` and scan Git history
with `gitleaks git --log-opts=--all --redact=100 --no-banner .`.
CI builds both Linux architectures and never performs authenticated downloads.

Use synthetic, small fixtures for tests. Do not add access tokens, downloaded
map data, private service URLs or large binary files. Report credential leaks
without repeating the secret and rotate the credential immediately. Do not
post tokens, customer databases or private resource URLs in public issues.

Update README.md and AGENTS.md when changing public commands, defaults,
storage contracts or build behavior. A new source revision requires review
of the patches, dependency notices and runtime compatibility.
Preserve upstream license snapshots verbatim; do not reformat or normalize them.

By submitting a contribution, you agree to license your original contribution
under Apache-2.0. Third-party code must retain its applicable license,
copyright and attribution notices.
