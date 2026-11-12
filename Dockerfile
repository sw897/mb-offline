# syntax=docker/dockerfile:1
# SPDX-License-Identifier: Apache-2.0
# Modified from the supplied historical Docker recipe; see docs/provenance.md.

ARG UBUNTU_IMAGE=ubuntu:focal@sha256:8feb4d8ca5354def3d8fce243717141ce31e2c428701f6682bd2fafe15388214
ARG MBGL_SOURCE_COMMIT=f2778251c97ba3403582b9c04290c50f927fd338
ARG APT_MIRROR=
ARG OFFLINE_TILE_COUNT_LIMIT=6000

FROM ${UBUNTU_IMAGE} AS build

ARG MBGL_SOURCE_COMMIT
ARG APT_MIRROR
ENV DEBIAN_FRONTEND=noninteractive

RUN if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|http://ports.ubuntu.com/ubuntu-ports|${APT_MIRROR}|g; s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get install -y --no-install-recommends \
       build-essential ca-certificates cmake git nodejs patch pkg-config \
       libcurl4-openssl-dev libgl1-mesa-dev libjpeg-dev libpng-dev \
       libuv1-dev libx11-dev zlib1g-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src/mapbox-gl-native
RUN git clone https://github.com/mapbox/mapbox-gl-native.git . \
    && git checkout --detach "${MBGL_SOURCE_COMMIT}" \
    && git submodule update --init --recursive

COPY patches /tmp/patches
ARG OFFLINE_TILE_COUNT_LIMIT
RUN case "${OFFLINE_TILE_COUNT_LIMIT}" in \
        ''|0*|*[!0-9]*) echo 'OFFLINE_TILE_COUNT_LIMIT must be an integer between 1 and 2147483647' >&2; exit 1 ;; \
    esac \
    && test "${OFFLINE_TILE_COUNT_LIMIT}" -le 2147483647 \
    && patch --strip=1 --input=/tmp/patches/linux-skip-glfw.patch \
    && grep -Fxq 'const uint64_t DEFAULT_OFFLINE_TILE_COUNT_LIMIT = 6000;' src/mbgl/util/mapbox.cpp \
    && sed -i "s/DEFAULT_OFFLINE_TILE_COUNT_LIMIT = 6000;/DEFAULT_OFFLINE_TILE_COUNT_LIMIT = ${OFFLINE_TILE_COUNT_LIMIT};/" src/mbgl/util/mapbox.cpp \
    && grep -Fxq "const uint64_t DEFAULT_OFFLINE_TILE_COUNT_LIMIT = ${OFFLINE_TILE_COUNT_LIMIT};" src/mbgl/util/mapbox.cpp \
    && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target mbgl-offline --parallel "$(nproc)" \
    && cp build/bin/mbgl-offline /mbgl-offline

# Preserve notices for the vendored dependencies, including CLI-only libraries.
RUN find vendor -type f \( \
        -iname 'LICENSE*' -o -iname 'LICENCE*' -o \
        -iname 'COPYING*' -o -iname 'COPYRIGHT*' -o -iname 'NOTICE*' \
    \) -exec sh -c 'for license_path do \
        install -D -m 644 "$license_path" "/tmp/vendor-licenses/$license_path"; \
    done' sh {} +

FROM ${UBUNTU_IMAGE}

ARG MBGL_SOURCE_COMMIT
ARG APT_MIRROR
ARG OFFLINE_TILE_COUNT_LIMIT
ARG SOURCE_URL=
ARG SOURCE_REVISION=unknown

COPY --from=build /mbgl-offline /usr/local/bin/mbgl-offline
COPY --from=build /tmp/vendor-licenses/ /usr/local/share/mb-offline/licenses/mapbox-gl-native/
COPY LICENSE NOTICE THIRD_PARTY_NOTICES.md /usr/local/share/mb-offline/
COPY licenses/ /usr/local/share/mb-offline/licenses/

ENV DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.title="mbgl-offline" \
      org.opencontainers.image.description="Mapbox GL Native offline tile downloader" \
      org.opencontainers.image.source="${SOURCE_URL}" \
      org.opencontainers.image.revision="${SOURCE_REVISION}" \
      org.opencontainers.image.licenses="Apache-2.0" \
      org.mb-offline.upstream.source="https://github.com/mapbox/mapbox-gl-native" \
      org.mb-offline.upstream.revision="${MBGL_SOURCE_COMMIT}" \
      org.mb-offline.offline-tile-count-limit="${OFFLINE_TILE_COUNT_LIMIT}"

RUN if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|http://ports.ubuntu.com/ubuntu-ports|${APT_MIRROR}|g; s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get install -y --no-install-recommends \
       ca-certificates libcurl4 libjpeg8 libopengl0 libpng16-16 \
       libstdc++6 libuv1 zlib1g \
    && rm -rf /var/lib/apt/lists/* \
    && install -d -m 755 -o 65532 -g 65532 /workdir

USER 65532:65532
WORKDIR /workdir
ENTRYPOINT ["/usr/local/bin/mbgl-offline"]
