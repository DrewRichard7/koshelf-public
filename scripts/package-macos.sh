#!/bin/sh
set -eu
cd "$(dirname "$0")/.."
sh scripts/build-macos.sh --portable
version=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' native/Info.plist)
destination="$PWD/dist/KoShelf-$version-macOS.dmg"
mkdir -p dist
staging=$(mktemp -d "$PWD/build/installer.XXXXXX")
trap 'rm -rf "$staging"' EXIT
mkdir "$staging/image"
ditto build/portable/KoShelf.app "$staging/image/KoShelf.app"
cp READ-BEFORE-INSTALLING.txt "$staging/image/Read before installing.txt"
ln -s /Applications "$staging/image/Applications"
hdiutil create -volname KoShelf -format UDZO -srcfolder "$staging/image" "$staging/KoShelf.dmg"
hdiutil verify "$staging/KoShelf.dmg"
mv -f "$staging/KoShelf.dmg" "$destination"
printf 'Packaged %s\n' "$destination"
