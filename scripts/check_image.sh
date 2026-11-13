#!/bin/sh
# SPDX-License-Identifier: Apache-2.0
set -eu

image_name=${1:-mbgl-offline}

docker run --rm --network none "$image_name" --help > /dev/null
# Put the value before --help so the native parser must accept it first.
docker run --rm --network none "$image_name" --includeIdeographs=1 --help > /dev/null
docker run --rm --network none --entrypoint /bin/sh "$image_name" -ec '
    test "$(id -u)" -ne 0
    test -w /workdir
    test -s /usr/local/share/mb-offline/LICENSE
    test -s /usr/local/share/mb-offline/NOTICE
    test -s /usr/local/share/mb-offline/THIRD_PARTY_NOTICES.md
    test -s /usr/local/share/mb-offline/licenses/mapbox-gl-native/LICENSE.md
    test -s /usr/local/share/mb-offline/licenses/mapbox-gl-native/LICENSE.mbgl-core.md
    library_report=$(ldd /usr/local/bin/mbgl-offline)
    printf "%s\n" "$library_report"
    if printf "%s\n" "$library_report" | grep -F "not found"; then
        exit 1
    fi
    for build_tool in git cmake gcc g++ node; do
        if command -v "$build_tool" > /dev/null 2>&1; then
            printf "Unexpected build tool in runtime image: %s\n" "$build_tool" >&2
            exit 1
        fi
    done
'

smoke_directory=$(mktemp -d)
trap 'rm -rf "$smoke_directory"' EXIT
docker run --rm --network none \
    --user "$(id -u):$(id -g)" \
    --volume "$smoke_directory:/workdir" \
    --entrypoint /bin/sh "$image_name" -ec 'touch /workdir/write-check'
test -f "$smoke_directory/write-check"
printf 'Image checks passed: %s\n' "$image_name"
