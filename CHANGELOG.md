# Changelog

This file records notable changes to KoShelf, newest first. Planned work lives in [TODO.md](TODO.md). Release downloads are on the [releases page](https://github.com/DrewRichard7/koshelf-public/releases).

## 1.3.0 · 2026-11-23

- **Dark mode.** A sun-and-moon switch above the shelf flips between light and dark, and **Settings → Appearance** also offers System. System follows your computer's light or dark mode. The app and the browser version share the choice, and generated covers and charts follow it too.
- **Shelf actions moved up.** The main action button (such as **Sync libraries**) and the **Shelf actions** menu now sit in the window's top bar, next to the light/dark switch.
- **Color themes.** **Settings → Theme** offers Tokyo Night, Gruvbox, Rosé Pine, Merino, Catppuccin, and Everforest besides KoShelf's own colors, each in light and dark. Previews show both variants. Three themes go further than colors: **Newsprint** has square corners, ink rules, newspaper type, and grayscale covers that turn to color under the pointer; **Matrix** is all monospace with green covers, glowing text and scanlines in dark mode, and green-bar printer paper in light mode. **Everforest** has a misty spruce forest behind the sidebar: daytime in light mode, and a winter night in dark mode, with snow on the trees, soft snow falling, and snow slowly settling on the shelf. Scenery stays in the window margins, and with Reduce motion turned on it stays still.
- **KoShelf now requires macOS 13 or later.** The Kindle converter is built with Go 1.27, which no longer supports macOS 12.

## 1.2.0 · 2026-11-20

- **Settings for what KoShelf may read.** **Shelf actions → Settings…** turns automatic Kindle detection, background comparison with a connected Kindle, and Apple Books progress on or off. Apple Books progress is off until you turn it on; opening Add to Apple Books no longer turns it on.
- **KoShelf asks before creating its library.** On first launch, a welcome screen offers **Create KoShelf Library** or choosing another folder.
- **Security fixes.** On Linux, the browser link is accepted only from the exact connection your browser opened, so another user's socket on the same port can't take over the session. Adding a book to Apple Books no longer follows symbolic links, and its partial copy is never uploaded to iCloud. Send to Kindle on Linux needs **Run the converter without a sandbox** ticked before it converts. KoShelf no longer looks for Kindles under `/mnt` on Linux, which often holds network shares.
- **Builds keep Full Disk Access across updates.** The build signs KoShelf with a stable `KoShelf Signing` certificate when the keychain has one, so macOS keeps the permission that Apple Books features need. Without the certificate, builds are signed ad hoc as before.
- **Apple Books progress.** Turn on **Use Apple Books progress** and give KoShelf Full Disk Access, and KoShelf reads Apple Books' library without changing it. Book cards show Apple Books progress. **Sync libraries** moves the Kindle to Apple Books' position when Apple Books is further along, and marks books finished there as finished on the Kindle. The review shows these changes as Apple Books → Kindle cards, and sync history can undo them. Highlights and notes on the Kindle stay.
- **Add to Apple Books.** A review lists Mac library books that aren't in Apple Books, and flags books whose title matches a different author's. Books you choose are copied into Apple Books' iCloud Drive folder, and Books adds them on all your devices.
- **The browser version shares the app's data.** On macOS both use `~/Library/Application Support/KoShelf`, and on Linux `~/.local/state/koshelf`. Tag mappings, sync history and undo, recovery copies, and the record of books edited on the Mac now carry over between them. Only one can be open at a time, and a second one explains why it can't start. If the app crashes or is force-quit, its service now stops too, so the app reopens normally. Earlier releases kept browser data in `state/` in the checkout.
- **Opening the browser works with Snap browsers on Linux.** The launcher opens a single-use link that expires after a minute, instead of a file in `/tmp` that Snap's Firefox and Chromium can't read. Only a browser running under your account can use the link.
- `koshelf --version` prints the version. Port errors name the port and suggest leaving out `--port`. The browser tab shows the KoShelf icon.
- **Sync never overwrites the Kindle's reading file.** A book finished on the Mac copies only the finished status into the Kindle's file, so the Kindle's reading position, highlights, and notes stay. If the Mac shows more progress for any other reason, the Kindle copy is kept and the review explains why. Reading-progress updates can now be undone from sync history. Their recovery copies are kept for the last 10 syncs.
- **Fixes to the sync review for renamed books.** **Keep both for now** leaves that one book in place instead of blocking the whole sync. Several identical copies under different names sync as ordinary files again. Sync can now be applied when more than 100 books were renamed.
- **Fixes to metadata cleanup.** Choosing only some genre changes can no longer save a genre twice or remove both copies of a repeated tag. The "After your choices" preview matches what is saved, including for letters such as "ß". A tag recognized only in an unsaved preview shows as unsaved. **Recognize** is unavailable while changes are being applied.
- **Metadata cleanup reviews each change on its own.** Every tag change and title change has its own checkbox, and nothing is preselected. An "After your choices" column updates as you choose, and **Select all** is still available. Only books with an actual suggested change are listed.
- **Recognize tags KoShelf doesn't know.** Clean up metadata lists unrecognized tags, such as award names or uncommon genres, with how many books have each one. **Recognize** saves the tag as a genre, so current and future books with that tag, in any capitalization, count as that genre. **Other options…** jumps to the tag in Tag mappings to map it to another genre or remove it. After a save, a confirmation lists the mappings that changed and how many books now have suggested changes from them. Each tag in Tag mappings is marked as saved or unsaved. The dialog widens on large windows, and long file paths wrap instead of pushing the cards past its edge.
- **Reading progress syncs in both directions.** When a book's KOReader reading file differs between the Kindle and the Mac, the copy that is further along wins, and a book marked finished counts as 100%. The review shows a card for each book with both progress bars. It labels which copy sends and which receives, and highlights the progress being added, such as **Kindle → Mac · 27% → 30%**. Earlier releases kept the Mac copy and saved the Kindle copy privately, so newer Kindle progress never reached the Mac. KOReader's `.old` backups follow the Kindle without being listed.
- **Renamed books are recognized.** Sync matches a book by its contents when it has a different name in the same folder, as well as in a different folder. Each matching book is its own choice: use the Mac name, use the Kindle name, or keep both after changing one copy's details. Sync no longer offers to copy a renamed book back as a new file.
- **Old reading-file recovery copies are pruned.** KOReader rewrites a book's reading file on every session, so each sync saved another copy. Sync now keeps the five newest copies of each reading file. EPUB copies and copies that undo needs are always kept.
- **Returning to the window no longer reloads the shelf when nothing changed.** KoShelf still checks the library's files, but only by their sizes and dates, and skips sending and redrawing the shelf when its listing is the same. Reading progress is reread only from KOReader files that changed.
- **The Kindle converter has a memory watchdog.** It is stopped if it uses more than 1 GB, in addition to its sandbox, `GOMEMLIMIT`, and five-minute timeout.
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
