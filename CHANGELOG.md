# Changelog

This file records notable changes to KoShelf, newest first. Planned work lives in [TODO.md](TODO.md). Release downloads are on the [releases page](https://github.com/DrewRichard7/koshelf-public/releases).

## Unreleased

- **Clean up sync leftovers.** A reviewed action in the Mac library finds `._` files copied from the Kindle and `(backup …)` copies left by earlier releases in the Mac library and the iCloud Drive folder. It moves them to the private recovery folder.
- **Sync no longer clutters libraries with backup copies.** When a Kindle file differs from the Mac file at the same path, sync keeps the Kindle version in KoShelf's private recovery folder. Earlier releases saved it in the Mac library as `Title (backup …)`, where it appeared on the shelf and synced to iCloud Drive.
- **Books edited on the Mac update the Kindle.** Sync recognizes a Kindle copy that predates an edit made in KoShelf and updates it, instead of reporting a conflict.
- **Sync and backup skip macOS metadata files.** Hidden files that macOS writes on the Kindle's drive, such as `._` files and `.DS_Store`, are no longer copied.
- Contributor notes in `CLAUDE.md`.
- The README mentions the **Quit Now** prompt.

## 1.1.0 · 2026-11-16

### Performance

- Kindle sync checks remember checksums, so books that haven't changed are not reread. A check of 3,700 books dropped from about 2 seconds with full reads to about 0.2 seconds. Eject cancels a running check.
- Shelf cards are reused across renders. Searching and filtering 1,500 books re-renders in about 23 ms instead of about 500 ms.
- Covers load without waiting for other operations, are cached, and are shrunk in the browser.
- Parsed book metadata is saved between launches. The first scan after a relaunch dropped from 0.54 s to 0.06 s.
- Storage totals load after the shelf appears. Descriptions load when a book is opened, and large responses are compressed.
- Kindle sync, backup, and iCloud Drive sync show progress counts. Quitting during a transfer explains the wait and offers **Quit Now**.

### Security

- The Kindle converter runs in a macOS sandbox with no network access and no access to the home folder.
- Browser mode listens on a random local port.
- The browser handoff file is removed after a minute.
- Release builds run only their bundled Python. Running from source requires Python 3.11 or later.
- Tests guard the DRM-free-only boundary.
- `golang.org/x/image` was updated to 0.48.0 and `golang.org/x/text` to 0.44.0. A `govulncheck` scan found no vulnerabilities.

### Licensing and packaging

- KoShelf is MIT licensed. The app includes `LICENSE.txt` and `PythonNotices.txt`.
- The bundled Python is smaller (55 MB to 44 MB per architecture). Tk, IDLE, pip, and the test suite were removed.
- The packaging script writes the DMG's SHA-256 next to the DMG.
- `koshelf` finds the app in `/Applications` as well as `~/Applications`.
- The Intel build is included but untested.

## 1.0.1 · 2026-11-15

- Hardened the local service with a per-launch session credential, protected browser handoff, `Host` and `Origin` checks, and request limits.
- Added bounded EPUB parsing in `epub_safety.py`, which covers XML entities, archive size and entry limits, and image budgets.
- Added shared filesystem containment and private state helpers in `library_fs.py`.
- Pinned the offline Kindle converter and its Go modules, with verified inputs and license notices.
- Packaged a portable universal build for Apple silicon and Intel with a bundled Python, verified by pinned checksums.
- Added the not-notarized notice and **Read before installing**.

## 1.0.0 · 2026-11-14

First full release. In addition to the 0.x features below, 1.0.0 includes:

- **Bulk metadata editing:** apply shared author or genre changes to selected books, with a verified backup of each EPUB. Move a selection into an existing or new folder.
- **One sync review:** see transfers in both directions, moved books, and conflicts before syncing Mac and Kindle. Choose which version wins, and see metadata differences.
- **KOReader reading status:** read progress and status from KOReader, filter by status, and edit a status and queue it for Kindle sync.
- **Duplicate finder:** find exact copies and possible matches, keep one copy, and archive the others. Sync applies archive choices to the Kindle.
- **Drag-and-drop imports:** preview the destination and check for duplicates and name collisions before copying.
- **Series and reading order:** read and edit series names and numbers, filter by series, and sort by reading order.
- **Cover art editing:** replace an EPUB's embedded cover with a JPEG or PNG.
- **Genre and title cleanup:** preview offline genre suggestions with editable mappings, then apply approved changes with verified backups. Get suggestions for shorter titles that repeat the series.
- **Sync history and undo:** review recorded EPUB actions and undo individual changes.
- **Library health check:** find unreadable metadata, missing covers or authors, and inconsistent tags and series fields.
- **iCloud Drive sync:** review and sync a Mac library folder with iCloud Drive, with recovery copies and history.
- **Omnibus splitting:** export separate volumes from EPUB 2 and EPUB 3 omnibuses.
- **Kindle font utility:** preview and import TTF, OTF, and TTC fonts for the Kindle reader or KOReader, with recovery copies when removing a font.
- **Stock Kindle delivery:** convert Mac EPUBs to AZW3 with a bundled offline converter, and detect whether a Kindle runs KOReader.
- Warm paper palette, window grain, and new sidebar and app icons.

## 0.x · 2026-11-13

- **0.7:** opens the Mac library first and offers Kindle sync. Shows sync progress. Books slide off and onto the shelf when switching libraries, with Kindle metadata fetched ahead of time. Adds editable EPUB genres with an interactive genre chart, and sends new Mac EPUBs to matching Kindle folders.
- **0.6:** reviews moved EPUBs during library backup.
- **0.5:** collapsible storage overview and local library backups.
- **0.4:** folder storage breakdown and sidebar size labels.
- **0.3:** EPUB title and author editor that keeps original backups.
- **0.2:** first native macOS window and app packaging. Removing a folder moves its contents to the parent.
- **0.1:** browser-based KoShelf with Kindle detection and safe eject.
