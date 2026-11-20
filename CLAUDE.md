# Working on KoShelf

This public repository is where KoShelf is developed and released. Work on a branch, open a pull request into `main`, and tag releases from `main`. Everything committed here is public, so keep local paths, personal details, and real library data out of commits and screenshots.

## Roadmap and changelog

Open work lives in `TODO.md`. When an item ships, remove it there and add it under **Unreleased** in `CHANGELOG.md`.

## Checks

Run these before every commit:

```sh
python3 -B -m unittest discover -q
node --check static/app.js
git diff --check
```

For manual or write tests, use a temporary library and state folder. Never use a real library:

```sh
scratch=$(mktemp -d)
mkdir "$scratch/home" "$scratch/books" "$scratch/state"
HOME="$scratch/home" KOSHELF_STATE_DIR="$scratch/state" ./koshelf --browser --library "$scratch/books"
```

Tests that scan a library must point `app.JOURNAL` at a temporary folder. Otherwise they write caches into `state/` in the checkout.

## Layout notes

- **Bundled Python modules.** The app bundle includes only the modules listed in `scripts/build-macos.sh`. When you add a Python module, add it there. `scripts/check-service.py` fails if one is missing.
- **Python version.** The minimum is 3.11. It is enforced in `app.py`, `launcher.py`, and `native/KoShelf.swift`, so change all three together, along with the README.
- **Saved state.** The state folder holds `metadata-cache.json`, which is discarded whenever `app.py` changes, and `sync-digests.json`. Both are only caches and are safe to delete.

## Boundaries to preserve

- **Local service.** The Python service binds only to `127.0.0.1`, on a random port unless `--port` is given. Private APIs require the per-launch session credential. Requests must match `Host`, and mutations also check `Origin` and `Sec-Fetch-Site`. If you change request headers or asset loading, verify the change in the native WebKit window.
- **Untrusted content.** EPUB metadata and filenames are untrusted. The UI sets text with `textContent` and never uses `innerHTML`. Check XML with `epub_safety.check_xml` before parsing it; `parse_xml` does both. ZIP reads stay bounded.
- **Filesystem.** Library paths go through `library_path`, which rejects symbolic links and paths that cross filesystems. Moves use `rename_exclusive` and never overwrite. Replacements keep verified recovery copies.
- **Converter.** The Kindle converter runs under `sandbox-exec` (see `kindle_delivery.sandboxed`). Keep it without network access and with writes limited to its workspace.
- **DRM.** KoShelf handles only DRM-free books. `test_drm.py` guards this.

## Interface

Keep the warm paper palette, subtle grain, fixed-position library slider, and the book cards sliding out and back in when switching libraries. The maintainer approved that transition. Shelf cards are reused between renders (`bookCard` in `static/app.js`). When a card shows a new book field, add that field to `cardKey`.

## Releasing

1. Bump `CFBundleShortVersionString` and `CFBundleVersion` in `native/Info.plist`. Update the README for any changed behavior. In `CHANGELOG.md`, move **Unreleased** into a new version heading.
2. Run `./scripts/package-macos.sh`, then `python3 -B scripts/check-service.py build/portable/KoShelf.app`. Mount the DMG and run the same check against the app inside it.
3. Stage the converter the way `scripts/build-kindle-converter.sh` does. Then run `govulncheck ./...` there for `GOARCH=arm64` and `GOARCH=amd64`.
4. Check that the KoShelf you open for manual testing is the new build. Look at its version in **About KoShelf**.
5. Before pushing, check tracked files and screenshots for local paths and personal details.
6. Run `gh release create vX.Y.Z` with the DMG and its `.sha256` file. In the notes:
   - keep the not-notarized notice;
   - say exactly what was tested;
   - keep the untested-Intel note unless the build was tested on an Intel Mac;
   - include the SHA-256.

Stay on Go 1.26.x while KoShelf supports macOS 12, because Go 1.27 requires macOS 13.
