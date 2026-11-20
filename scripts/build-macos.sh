#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
case "${1:-}" in
    "") architectures=$(uname -m); portable=false; destination="$PWD/build/KoShelf.app" ;;
    --portable) architectures="arm64 x86_64"; portable=true; destination="$PWD/build/portable/KoShelf.app" ;;
    *) printf 'Usage: %s [--portable]\n' "$0" >&2; exit 1 ;;
esac
mkdir -p build/module-cache build/go-cache "$(dirname "$destination")"
export GOCACHE="$PWD/build/go-cache"
scratch=$(mktemp -d "$PWD/build/macos.XXXXXX")
trap 'rm -rf "$scratch"' EXIT
APP="$scratch/KoShelf.app"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/static"
for architecture in $architectures; do
    case "$architecture" in
        arm64) go_arch=arm64 ;;
        x86_64) go_arch=amd64 ;;
    esac
    # The current SDK ships Swift compatibility archives for ARM only.
    swiftc -O -runtime-compatibility-version none -target "$architecture-apple-macos12.0" -module-cache-path "$PWD/build/module-cache" \
        native/KoShelf.swift -o "$scratch/KoShelf-$architecture" -framework AppKit -framework WebKit
    GOOS=darwin GOARCH="$go_arch" sh scripts/build-kindle-converter.sh \
        "$scratch/converter-$architecture" "$APP/Contents/Resources/KindleConverterNotices.txt"
    if "$portable"; then
        runtime="$APP/Contents/Resources/Python/$architecture"
        sh scripts/bundle-python.sh "$architecture" "$runtime" "$PWD/build/downloads"
    fi
done
if "$portable"; then
    lipo -create "$scratch/KoShelf-arm64" "$scratch/KoShelf-x86_64" -output "$APP/Contents/MacOS/KoShelf"
    lipo -create "$scratch/converter-arm64" "$scratch/converter-x86_64" -output "$APP/Contents/MacOS/koshelf-kindle-converter"
else
    cp "$scratch/KoShelf-$architecture" "$APP/Contents/MacOS/KoShelf"
    cp "$scratch/converter-$architecture" "$APP/Contents/MacOS/koshelf-kindle-converter"
fi
cp native/Info.plist "$APP/Contents/Info.plist"
if "$portable"; then
    /usr/libexec/PlistBuddy -c 'Add :KoShelfRequiresBundledPython bool true' "$APP/Contents/Info.plist"
    # Ship the license texts for everything the bundled runtime includes.
    test -s notices/PythonNotices.txt
    cp notices/PythonNotices.txt "$APP/Contents/Resources/PythonNotices.txt"
fi
cp LICENSE "$APP/Contents/Resources/LICENSE.txt"
cp app.py apple_books.py genre_cleanup.py cloud_sync.py digest_index.py epub_split.py kindle_fonts.py kindle_delivery.py library_fs.py "$APP/Contents/Resources/"
cp epub_safety.py local_session.py "$APP/Contents/Resources/"
cp static/index.html static/app.js static/style.css static/koshelf-icon.png "$APP/Contents/Resources/static/"
if [ -f native/KoShelf.icns ]; then cp native/KoShelf.icns "$APP/Contents/Resources/KoShelf.icns"; fi
if [ -n "${KOSHELF_VERSION:-}" ]; then
    # Label a development build, for example 1.1.2-dev, before it is signed.
    /usr/libexec/PlistBuddy -c "Set :CFBundleShortVersionString $KOSHELF_VERSION" "$APP/Contents/Info.plist"
fi
# macOS ties privacy permissions such as Full Disk Access to the signature. A stable
# certificate keeps them across builds; ad hoc signing gives every build a new identity.
identity=${KOSHELF_SIGNING_IDENTITY:-}
if [ -z "$identity" ] && security find-identity -p codesigning 2>/dev/null | grep -q '"KoShelf Signing"'; then
    identity="KoShelf Signing"
fi
if [ -z "$identity" ]; then
    identity=-
    echo "Signing ad hoc. Full Disk Access must be granted again after each build." >&2
else
    echo "Signing with: $identity" >&2
fi
codesign --force --sign "$identity" "$APP/Contents/MacOS/koshelf-kindle-converter"
codesign --force --sign "$identity" "$APP"
codesign --verify --deep --strict "$APP"
rm -rf "$destination"
mv "$APP" "$destination"
printf 'Built %s\n' "$destination"
