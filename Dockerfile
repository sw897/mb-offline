# syntax=docker/dockerfile:1

ARG UBUNTU_IMAGE=ubuntu:focal@sha256:8feb4d8ca5354def3d8fce243717141ce31e2c428701f6682bd2fafe15388214
ARG MBGL_SOURCE_COMMIT=f2778251c97ba3403582b9c04290c50f927fd338
ARG APT_MIRROR=

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
RUN patch --strip=1 --input=/tmp/patches/offline-tile-limit.patch \
    && patch --strip=1 --input=/tmp/patches/linux-skip-glfw.patch \
    && grep -Fxq 'const uint64_t DEFAULT_OFFLINE_TILE_COUNT_LIMIT = 2147483647;' src/mbgl/util/mapbox.cpp \
    && cmake -S . -B build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build build --target mbgl-offline --parallel "$(nproc)" \
    && cp build/bin/mbgl-offline /mbgl-offline

FROM ${UBUNTU_IMAGE}

ARG MBGL_SOURCE_COMMIT
ARG APT_MIRROR

COPY --from=build /mbgl-offline /usr/local/bin/mbgl-offline

ENV DEBIAN_FRONTEND=noninteractive

LABEL org.opencontainers.image.title="mbgl-offline" \
      org.opencontainers.image.description="Mapbox GL Native offline tile downloader" \
      org.opencontainers.image.source="https://github.com/mapbox/mapbox-gl-native" \
      org.opencontainers.image.revision="${MBGL_SOURCE_COMMIT}"

RUN if [ -n "${APT_MIRROR}" ]; then \
        sed -i "s|http://ports.ubuntu.com/ubuntu-ports|${APT_MIRROR}|g; s|http://archive.ubuntu.com/ubuntu|${APT_MIRROR}|g" /etc/apt/sources.list; \
    fi \
    && apt-get -o Acquire::Retries=5 update \
    && apt-get install -y --no-install-recommends \
       ca-certificates libcurl4 libjpeg8 libopengl0 libpng16-16 \
       libstdc++6 libuv1 zlib1g \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workdir
ENTRYPOINT ["/usr/local/bin/mbgl-offline"]
