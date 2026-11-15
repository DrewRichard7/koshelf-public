#!/bin/sh
# Install the pinned, relocatable Python runtime for a portable Mac build.
set -eu
architecture=$1
destination=$2
cache=$3
case "$architecture" in
    arm64) platform=aarch64; checksum=7a2384641584ab9984221fcb0fc9265df465922af71be71410529e2a9c3f0540 ;;
    x86_64) platform=x86_64; checksum=d27d59a722db9f69926eb6d9564071247221f45a04da920ea7f3001c97e32f9b ;;
    *) printf 'Unsupported architecture: %s\n' "$architecture" >&2; exit 1 ;;
esac
release=20261110
name="cpython-3.13.18+${release}-${platform}-apple-darwin-install_only_stripped.tar.gz"
archive="$cache/$name"
mkdir -p "$cache" "$destination"
if [ ! -f "$archive" ]; then
    download=$(mktemp "$cache/python.XXXXXX")
    trap 'rm -f "$download"' EXIT
    curl --fail --location --silent --show-error --retry 2 --max-time 180 \
        "https://github.com/astral-sh/python-build-standalone/releases/download/$release/$name" -o "$download"
    printf '%s  %s\n' "$checksum" "$download" | shasum -a 256 -c -
    mv "$download" "$archive"
fi
printf '%s  %s\n' "$checksum" "$archive" | shasum -a 256 -c -
tar -xzf "$archive" --strip-components 1 -C "$destination"
# Python's optional development tools and pip are unused by the service.
rm -rf "$destination/include" "$destination/lib/python3.13/ensurepip" \
    "$destination/lib/python3.13/site-packages"
find "$destination" -type f \( -name '*.dylib' -o -name '*.so' -o -path '*/bin/python3.13' \) \
    -exec codesign --force --sign - {} +
