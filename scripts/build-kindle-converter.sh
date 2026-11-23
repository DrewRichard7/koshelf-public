#!/bin/sh
# Build the pinned converter used for stock Kindle delivery.
set -eu
output=$1
notices=$2
wrapper="$(cd "$(dirname "$0")/../native/converter" && pwd)"
module=github.com/seapy/kindle-cli@v0.0.3-0.20260704180633-3a8bdee7f938
export GOWORK=off GOTOOLCHAIN=go1.27.4 CGO_ENABLED=0
metadata=$(go mod download -json "$module")
work=$(mktemp -d "$(dirname "$output")/kindle-converter.XXXXXX")
trap 'rm -rf "$work"' EXIT
printf '%s' "$metadata" | python3 -E -S "$(dirname "$0")/stage-kindle-converter.py" "$module" "$work"
cp "$wrapper/azw3/image_budget.go" "$work/internal/azw3/"
cp "$wrapper/main.go" "$wrapper/go.mod" "$wrapper/go.sum" "$work/"
cd "$work"
go mod download
go mod verify
go build -mod=readonly -trimpath -buildvcs=false -ldflags="-s -w" -o "$output" .
dependencies=$(go list -mod=readonly -deps -f '{{with .Module}}{{.Path}} {{.Dir}}{{end}}' .)
{
    printf 'KoShelf Kindle converter\n%s\n' "$module"
    cat LICENSE
    printf '\n\nGo standard library\n'
    goroot=$(go env GOROOT)
    go_license="$goroot/LICENSE"
    if [ ! -f "$go_license" ]; then go_license="$goroot/../LICENSE"; fi
    cat "$go_license" "$goroot/PATENTS"
    printf '%s\n' "$dependencies" | sort -u | while IFS=' ' read -r name directory; do
        [ -n "$name" ] && [ "$directory" != "$work" ] || continue
        printf '\n\n%s\n' "$name"
        cat "$directory/LICENSE"
        if [ -f "$directory/PATENTS" ]; then cat "$directory/PATENTS"; fi
    done
} > "$notices"
