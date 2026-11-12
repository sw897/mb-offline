# Third-party notices

The Apache-2.0 license in the repository root applies to this project's
original contributions. Bundled dependencies retain their own licenses.
This file describes software provenance; it does not license map data.

## Mapbox GL Native

- Repository: <https://github.com/mapbox/mapbox-gl-native>
- Pinned revision: `f2778251c97ba3403582b9c04290c50f927fd338` (2023-07-27).
- Copyright: Mapbox, 2014-2020.
- Main license: BSD-2-Clause. The complete, unmodified text is preserved in
  [licenses/mapbox-gl-native/LICENSE.md](licenses/mapbox-gl-native/LICENSE.md).
- The upstream core's aggregate dependency notices are preserved verbatim in
  [licenses/mapbox-gl-native/LICENSE.mbgl-core.md](licenses/mapbox-gl-native/LICENSE.mbgl-core.md).

The build skips the unrelated GLFW example. It retains the upstream 6000-tile
default; an explicit `OFFLINE_TILE_COUNT_LIMIT` build argument can change that
constant. See [README.md](README.md) for the contract and limitations.

The upstream aggregate notice includes the core's Mapbox libraries, Boost,
RapidJSON, expected-lite, optional, unique_resource and other dependencies.
It is not an exhaustive inventory of the final executable: the offline CLI
also uses `args`, and platform libraries have additional notices. During the
Docker build, license, copying, copyright and notice files from the checked-out
`vendor/` tree and its initialized submodules are copied, with their relative
paths preserved, into the runtime image. This includes dependencies outside
the core's aggregate notice. This is a superset of vendor notice files, not a
claim that every vendored component is linked into the offline CLI.

The image stores the project license, this notice, `NOTICE`, the upstream
license snapshots and collected vendor notices under:

```text
/usr/local/share/mb-offline/
```

Ubuntu runtime packages retain their package copyright and license records
under `/usr/share/doc/<package>/copyright`; shared license texts are under
`/usr/share/common-licenses/`. Packaging an image does not relicense these
packages under Apache-2.0. Image release tooling should also inspect the
resolved package inventory when distributing a new image.

The checked-in license snapshots describe the default pinned upstream. A
custom `MBGL_SOURCE_COMMIT` must be reviewed for source compatibility and
license changes before distribution; update these snapshots as needed.

## Historical Docker recipes

The supplied historical recipe has strong structural similarities to
[snodnipper/whalebrew-mbgl-offline](https://github.com/snodnipper/whalebrew-mbgl-offline)
and [OrdnanceSurvey/whalebrew-mbgl-offline](https://github.com/OrdnanceSurvey/whalebrew-mbgl-offline).
The latter carries an [Apache-2.0 license](https://github.com/OrdnanceSurvey/whalebrew-mbgl-offline/blob/master/LICENSE),
whose complete license text is also provided in this repository's `LICENSE`.
The final Dockerfile has been modified from the supplied recipe; its header
records that modification. The direct provenance remains unconfirmed.

[marcosfons's Dockerfile Gist](https://gist.github.com/marcosfons/d8257c5c073811f547fbf73ad7af4d4f)
was examined as a comparison. No code from that Gist was added during the
open-source preparation. See [docs/provenance.md](docs/provenance.md).

## Data and trademarks

Mapbox-hosted tiles, styles, sprites, glyphs and any downloaded archives are
not distributed with this repository and are not covered by its software
license. Their use is governed by the applicable service terms and account
agreement. Mapbox and Ordnance Survey names identify upstream projects and
do not imply affiliation, endorsement or a trademark license.
