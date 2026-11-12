# Provenance

This document records historical evidence. Current commands and defaults are
in [README.md](../README.md); development rules are in [AGENTS.md](../AGENTS.md).

## Confirmed native upstream

The Dockerfile clones [mapbox/mapbox-gl-native](https://github.com/mapbox/mapbox-gl-native)
and builds its `mbgl-offline` target at revision
`f2778251c97ba3403582b9c04290c50f927fd338` (2023-07-27, "Fix macos build").
The command implementation is
[`bin/offline.cpp`](https://github.com/mapbox/mapbox-gl-native/blob/f2778251c97ba3403582b9c04290c50f927fd338/bin/offline.cpp).
The upstream repository was archived on 2023-08-08.

This project's existing Git history starts with a new baseline on 2026-08-17.
It does not preserve an upstream fork/merge chain. The Python download wrapper,
MBTiles exporter and packaging changes are maintained in this repository.

## Likely historical Docker references

A comparison on 2026-11-12 found the strongest similarities with the
[snodnipper Dockerfile](https://github.com/snodnipper/whalebrew-mbgl-offline/blob/e683bce4248debb74b5b98588678b5dfb816c927/Dockerfile)
(2017-04-04) and
[OrdnanceSurvey's initial Dockerfile](https://github.com/OrdnanceSurvey/whalebrew-mbgl-offline/blob/7b4d625e912d1dbdaf1936e90f25e40b3cfd0a34/Dockerfile)
(2017-04-20). Those two files differ only in trailing whitespace.

The supplied recipe retains the same NodeSource installation method, the same
separate four-package development dependency group, and the same sequence of
nine packages removed after compilation. It also preserves the ordering of
source removal, automatic package cleanup and runtime package installation.
Upgrading Xenial to Focal, Node.js 6 to 10, and the native build/output paths
accounts for many of its differences. This is evidence of a shared recipe
lineage; it does not establish which address was copied or whether a tutorial
or other intermediate source was used.

[marcosfons's Gist](https://gist.github.com/marcosfons/d8257c5c073811f547fbf73ad7af4d4f)
was created on 2021-12-19 and last revised on 2021-12-20. All five recorded
versions use multiple build stages and omit the NodeSource/removal sequence.
Its Focal base and tile-limit edit resemble parts of the supplied recipe,
but the structural evidence for direct inheritance is weaker. No code from
that Gist was added during the open-source preparation.

## Supplied historical recipe

The following recipe was recorded in the project's first baseline. It is
retained as evidence, not as the current build or a recommended download
configuration. In particular, its large tile-limit override is no longer the
default. The original input used `make mbgl-offline` inside `build`; the
baseline documentation normalized that invocation to `cmake --build`.

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

The maintained Dockerfile keeps the two-stage approach, validates any explicit
tile-limit override, retains the upstream default otherwise, and ships license
notices. This project's software license does not extend to hosted map data.
