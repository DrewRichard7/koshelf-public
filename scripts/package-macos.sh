#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
sh scripts/build-macos.sh --portable
version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' native/Info.plist)
destination="$PWD/artifacts/dist/KoShelf-$version-macOS.dmg"
mkdir -p artifacts/dist
staging=$(mktemp -d "$PWD/artifacts/build/installer.XXXXXX")
trap 'rm -rf "$staging"' EXIT
mkdir "$staging/image"
ditto artifacts/build/portable/KoShelf.app "$staging/image/KoShelf.app"
cp READ-BEFORE-INSTALLING.txt "$staging/image/Read before installing.txt"
ln -s /Applications "$staging/image/Applications"
hdiutil create -volname KoShelf -format UDZO -srcfolder "$staging/image" "$staging/KoShelf.dmg"
hdiutil verify "$staging/KoShelf.dmg"
mv -f "$staging/KoShelf.dmg" "$destination"
# Publish this checksum with the release so people can verify their download.
(cd "$(dirname "$destination")" && shasum -a 256 "$(basename "$destination")" > "$(basename "$destination").sha256")
printf 'Packaged %s\n' "$destination"
cat "$destination.sha256"
