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

A service started without `KOSHELF_STATE_DIR` uses the real app data folder (`~/Library/Application Support/KoShelf` on macOS), so always set it for manual tests. On macOS a browser service also detects any Kindle mounted under `/Volumes`, even with a temporary `HOME`. If the maintainer's Kindle is connected, don't sync, eject, or send to it during a manual test.

## Layout notes

- **Bundled Python modules.** The app bundle includes only the modules listed in `scripts/build-macos.sh`. When you add a Python module, add it there. `scripts/check-service.py` fails if one is missing.
- **Python version.** The minimum is 3.11. It is enforced in `app.py`, `launcher.py`, and `native/KoShelf.swift`, so change all three together, along with the README.
- **Saved state.** The app and the browser service share one state folder: `~/Library/Application Support/KoShelf` on macOS, and `$XDG_STATE_HOME/koshelf` or `~/.local/state/koshelf` on Linux. `app.main` resolves it at startup, so importing `app` in tests never touches it. `service.lock` keeps a second service from using the same folder. The state folder holds `metadata-cache.json`, which is discarded whenever `app.py` changes, and `sync-digests.json`. Both are only caches and are safe to delete. `sync-versions/` holds recovery copies of Kindle files that differed during a sync. Undo depends on it, so it is not a cache.

## Boundaries to preserve

- **Local service.** The Python service binds only to `127.0.0.1`, on a random port unless `--port` is given. Private APIs require the per-launch session credential. Requests must match `Host`, and mutations also check `Origin` and `Sec-Fetch-Site`. If you change request headers or asset loading, verify the change in the native WebKit window.
- **Untrusted content.** EPUB metadata and filenames are untrusted. The UI sets text with `textContent` and never uses `innerHTML`. Check XML with `epub_safety.check_xml` before parsing it; `parse_xml` does both. ZIP reads stay bounded.
- **Filesystem.** Library paths go through `library_path`, which rejects symbolic links and paths that cross filesystems. Moves use `rename_exclusive` and never overwrite. Replacements keep verified recovery copies.
- **Converter.** The Kindle converter runs under `sandbox-exec` (see `kindle_delivery.sandboxed`). Keep it without network access and with writes limited to its workspace. Linux has no sandbox yet. There it runs with a warning in the Send to Kindle dialog until Linux sandboxing (see `TODO.md`) is added.
- **DRM.** KoShelf handles only DRM-free books. `test_drm.py` guards this.
- **Opt-in access.** Reading or writing anything outside the library the user opened must be the user's choice: an action they start, or a switch in **Settings** (`SETTINGS_DEFAULTS` in `app.py`). Never add background access to new locations without a setting. Apple Books progress is off by default. KoShelf asks before creating `~/KoShelf Library`.

## Interface

Keep the warm paper palette (and its dark counterpart), subtle grain, fixed-position library slider, and the book cards sliding out and back in when switching libraries. The maintainer approved that transition. Shelf cards are reused between renders (`bookCard` in `static/app.js`). When a card shows a new book field, add that field to `cardKey`.

**Colors come from theme tokens.** `style.css` starts with the light tokens on `:root` and the dark ones on `[data-theme="dark"]`. Rules and `app.js` use `var(--token)`, never literal colors, and both themes define every token. `test_appearance.py` checks this. `static/theme.js` sets `data-theme` before the page draws, from the `appearance` setting that the server writes into `index.html`. The other color themes live in `static/themes.css`, keyed by `data-palette`. Don't edit that file by hand: change the palettes in `scripts/build-themes.py` and run it. A theme there can also set fonts (typefaces are the `--serif`, `--sans`, and `--mono` tokens) and extra CSS for shape and effects, as Newsprint and Matrix do. Scenery art (Everforest) lives in `scripts/theme-art/<theme>/`; the forest and snow are drawn by `scripts/theme-art/make-scenery.py`, so change and rerun that instead of editing them. Art used in only one mode goes in a `light/` or `dark/` subfolder. Its `{{token}}` placeholders take each mode's colors, and the generator inlines it as `--art-<name>` data URLs. Credit every file in `scripts/theme-art/CREDITS.md`, and keep scenery in the window margins with `pointer-events:none`, transform-only motion, and a reduced-motion rule. To add a theme, add its palette there, its name to `SETTINGS_CHOICES` in `app.py`, and a preview in the Settings dialog in `index.html`.

## Releasing

1. Bump `CFBundleShortVersionString` and `CFBundleVersion` in `native/Info.plist`. Update the README for any changed behavior. In `CHANGELOG.md`, move **Unreleased** into a new version heading.
2. Run `./scripts/package-macos.sh`, then `python3 -B scripts/check-service.py build/portable/KoShelf.app`. Mount the DMG and run the same check against the app inside it. Sign releases with the maintainer's `KoShelf Signing` certificate (the build prints `Signing with: KoShelf Signing`). Users then keep Full Disk Access across updates, and a release signed with another certificate makes them grant it again. For dev builds, set `KOSHELF_VERSION` instead of editing `Info.plist` afterward, because re-signing ad hoc discards the certificate signature.
3. Stage the converter the way `scripts/build-kindle-converter.sh` does. Then run `govulncheck ./...` there for `GOARCH=arm64` and `GOARCH=amd64`.
4. Check that the KoShelf you open for manual testing is the new build. Look at its version in **About KoShelf**.
5. Before pushing, check tracked files and screenshots for local paths and personal details.
6. Run `gh release create vX.Y.Z` with the DMG and its `.sha256` file. In the notes:
   - keep the not-notarized notice;
   - say exactly what was tested;
   - keep the untested-Intel note unless the build was tested on an Intel Mac;
   - include the SHA-256.

KoShelf requires macOS 13 or later. Keep the Go toolchain in `scripts/build-kindle-converter.sh` and `native/converter/go.mod` on a supported Go release, and check the macOS minimum in its release notes before raising the version.
