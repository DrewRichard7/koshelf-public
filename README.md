# KoShelf

KoShelf organizes EPUBs on your Mac and in the `books` folder of a USB-mounted Kindle running KOReader. The desktop app is for macOS. The browser interface runs locally on macOS and Linux with Python 3.11 or later and uses only the Python standard library.

## Install from a disk image

> **KoShelf has not been notarized by Apple. Download and use it at your own risk.**
>
> I built KoShelf for fun with AI coding tools and guided the work myself. It's a personal project I'm sharing with friends. Testing and review have been limited. KoShelf can move and modify files on your Mac and Kindle, so keep a separate backup of your library before using it.

Download the latest [KoShelf disk image](https://github.com/DrewRichard7/koshelf-public/releases/latest).

Open the KoShelf disk image and drag **KoShelf** into **Applications**. Launch that copy from Finder or Raycast. The portable package includes Python and the Kindle converter for Apple silicon and Intel Macs running macOS 12 or later. It has been tested on Apple silicon only. The Intel build is included but untested, because no Intel Mac was available. Transfer support depends on the Kindle appearing as a mounted USB drive.

The disk image includes [Read before installing](READ-BEFORE-INSTALLING.txt). The app uses an ad hoc signature, so macOS may show a Gatekeeper warning the first time you open it. Control-click the app and choose **Open** instead of turning off Gatekeeper. Each release lists the disk image’s SHA-256 checksum. Compare it with `shasum -a 256 KoShelf-<version>-macOS.dmg` before opening a copy you received from someone else.

## Open KoShelf

On a Mac with the desktop app installed, search for **KoShelf** in Raycast or open it from `/Applications` or `~/Applications`. The app starts its own local service on an available port. **Command-Q** closes the app and waits for active file requests. If a transfer is still running, KoShelf explains the wait and offers **Quit Now**.

From a checkout, use the following commands:

```sh
./koshelf                       # Desktop app when installed, otherwise browser
./koshelf --browser             # Default browser
./koshelf --browser --port 8766 # Fixed local port instead of a random one
./koshelf --library /path/to/books
./koshelf --version
```

The wrapper works from bash, zsh, or any POSIX shell on macOS and Linux. To use `koshelf` from any directory, put a symlink to it in a directory on your PATH, such as `~/.local/bin`. On Linux there is no desktop app, so `koshelf` always opens the browser. `Launch.command` also works from Finder.

Browser mode listens on a random local port. Running `./koshelf --browser` again reopens the service you already started. Keep the terminal open while using the browser service. Press Control-C to stop it.

The app and the browser share their saved data, so only one can be open at a time. If the app is open, `koshelf --browser` explains that and exits. Quit the app first to use the browser.

## Browse and organize

KoShelf opens or creates `~/KoShelf Library`. Use the **Mac library** and **Kindle library** switch to browse each shelf. KoShelf detects a mounted Kindle with a `books` folder and leaves the default Kindle library outside its scope. If automatic selection cannot tell which Kindle to use, choose its books folder explicitly.

The shelf supports search, genre and reading-status filters, and series reading order. The storage panel counts all files inside the selected folder, including hidden files and KOReader companions. The genre chart uses EPUB counts and splits a book's share across its genres. Click a genre to filter the shelf. The storage and genre panels expand and collapse together.

Create folders in the sidebar, drag books to them, or select several books and move them together. **Remove folder…** promotes the folder's direct contents to its parent and stops on a name collision. Import EPUBs by dropping files into the app or using **Import EPUBs** from the shelf menu.

Open **Shelf actions → Manage fonts…** with your Kindle connected. Choose the Kindle reader, KOReader, or both, then choose or drop TTF, OTF, or TTC files. Browse fonts by reader, preview individual styles, and remove custom fonts with a recovery copy saved in KoShelf’s state directory. After ejecting, choose a font from the Kindle reader’s **Aa** menu or KOReader’s **Font** menu. Restart KOReader if it was already open.

Use **Eject Kindle** before unplugging the Kindle. On macOS, KoShelf requests a normal unmount and reports when it is safe to unplug. Reconnect by unplugging and plugging it back in. Ejection is available on macOS.

## Send EPUBs to the Kindle reader

Connect one Kindle whose USB storage appears as a disk with a `documents` folder. KoShelf checks for KOReader on that disk. When KOReader is present, KoShelf offers **Sync libraries**, or **Set up KOReader shelf** if the top-level `books` folder is missing. Otherwise it offers **Send to Kindle…** for the built-in reader.

From Mac Library, open a book or select several and choose **Send to Kindle…**. The desktop app includes a local converter based on [kindle-cli](https://github.com/seapy/kindle-cli) and [leotaku/mobi](https://github.com/leotaku/mobi). It sends reflowable, DRM-free EPUBs as AZW3 personal documents. Original EPUBs stay in your Mac library. Existing destination filenames are skipped. The converter omits embedded fonts and has limited support for fixed-layout books.

Kindle models that need an MTP transfer app on Mac are outside this USB workflow.

## Edit metadata

Open a book and choose **Edit metadata…** to edit its title, authors, genres, series position, or embedded cover. Use a JPEG or PNG for replacement covers. The shelf can display embedded JPEG, PNG, and GIF covers.

In **Mac library → Shelf actions → Clean up metadata…**, scan the full library, a folder, or your selection. Save custom tag mappings locally, then approve individual title and genre changes before applying. Title suggestions require a simple title with a suffix that matches the embedded series and book number. Use **Library health check** to find missing or inconsistent metadata.

KoShelf verifies an original EPUB backup before replacing a book and checks the replacement archive before saving. Metadata edits preserve archive entries outside the package metadata, except when you replace the cover. The editor refuses encrypted, digitally signed, malformed, and oversized EPUBs.

Same-stem `.sdr` companions move with their books. Metadata edits preserve those companions and the EPUB filename. KOReader may show cached metadata until you refresh its book information. KOReader History, Favorites, collections, custom overrides, and centralized companion stores are outside the scope of these edits.

Use **Split omnibus…** in the Mac library to export separate volumes from an EPUB 2 or EPUB 3 omnibus with a readable table of contents. Review the suggested starts, set a series name, and skip any volumes you already have. KoShelf creates new EPUBs in a folder beside the original.

## Sync, duplicates, and recovery

On macOS, open **Shelf actions → iCloud Drive sync…** and choose a dedicated folder in iCloud Drive, such as `KoShelf Library`. You can create it in the folder picker. Choose **Keep Downloaded** in Finder so KoShelf can compare the local contents. The same folder appears under **Files → iCloud Drive** on your iPhone. Add an EPUB at the top level to sync it to **Books in root** on the Mac.

Review changes before syncing Mac and iCloud. The review shows copies, replacements, removals, and conflicts. KoShelf keeps recovery copies on the Mac before replacing or removing EPUBs. Check Finder for upload progress after KoShelf finishes writing to the local iCloud Drive folder. Use **Sync libraries** separately to bring your Kindle up to date.

With both libraries available, **Sync libraries** reviews proposed changes in both directions. Review the transfer lists, choose whether matching books should follow Kindle folders, Mac folders, or both, and inspect differences between versions. When a Kindle file differs from the Mac file at the same path, KoShelf keeps the Kindle version in its private recovery folder (`sync-versions` in the state folder), not in your library. For EPUB conflicts you can choose to use the Mac version on the Kindle. Books you edited in KoShelf on the Mac are recognized and updated on the Kindle without asking. Sync skips the hidden files macOS writes on the Kindle's drive, such as `._` files and `.DS_Store`. If an earlier version left `._` files or `(backup …)` copies in your Mac library or iCloud Drive folder, use **Shelf actions → Clean up sync leftovers…**. It lists what it found and, after you confirm, moves those files to KoShelf's recovery folder. Backup copies whose original is missing stay where they are.

**Find duplicates** helps you choose the copy to keep. KoShelf stores archived copies in `~/KoShelf Duplicate Archive`, outside the synced shelves. The next sync can apply an archive decision to the matching Kindle copy after verifying that the keeper is present. **Sync history** lists recorded EPUB actions and offers undo where applicable. Transfer totals can include non-EPUB files, such as companion data, while sync history lists EPUB actions.

When you open an explicit folder with `--library`, **Back up library** copies into `~/KoShelf Library`. It preserves relative paths and empty folders, skips identical files, and saves changed same-path files under a content-based suffix. Keep the source connected through the result summary.

KoShelf stores its data under `~/Library/Application Support/KoShelf/` on macOS, for both the app and the browser, and under `~/.local/state/koshelf/` on Linux, or `$XDG_STATE_HOME/koshelf/` when that is set. This data includes tag mappings, sync history and undo, recovery copies, and logs. Set `KOSHELF_STATE_DIR` to use another folder. Earlier releases kept browser data in `state/` in the checkout; move its files into the new folder to keep them. The state folder also holds `metadata-cache.json` and `sync-digests.json`, which let KoShelf skip rereading unchanged books. Deleting them is safe. `moves.jsonl` records metadata backup paths, and `metadata-backups/` holds original EPUBs. `sync-versions/` holds Kindle versions that differed from the Mac during a sync, including older KOReader reading files, arranged by library path. To restore a metadata backup, locate the original path in the journal, quit KoShelf, and copy the saved EPUB over that path using the original filename. Backups accumulate until you manage them.

## Privacy and security

KoShelf serves its interface on this computer’s loopback address, on a random port. Library operations require a session created by the desktop or terminal launcher. If you close a browser tab, open a fresh session with `./koshelf --browser`. KoShelf has no telemetry. The bundled Kindle converter runs offline in a macOS sandbox that blocks network access and limits it to its own working folder. Linux has no sandbox for it yet. If you build the converter on Linux, **Send to Kindle** warns that it runs with your normal permissions, so convert only EPUBs you trust.

On macOS, the launcher hands your session to the browser through a private file. On Linux, Snap browsers can't read that file, so the launcher opens a single-use link instead. The link stops working after one minute or once it has been used. KoShelf accepts it only from a browser running under your user account. iCloud Drive sync is optional and uses the folder you select.

Use a folder dedicated to your books. The original backup workflow can copy companion files from the selected library, including KOReader reading data. KoShelf checks EPUB structure and limits resource usage when processing files. EPUBs and fonts still come from outside KoShelf and may also be parsed by your reader. KoShelf works only with DRM-free books and refuses encrypted or signed EPUBs.

## Build and check

On macOS, install the Apple Command Line Tools and make Python 3.11 or later and Go available. The build uses Go 1.26.11 for the pinned offline converter and includes its license notices. The default developer build uses the host architecture, signs locally, and uses a Python 3.11 or later already installed on the Mac. `./scripts/build-macos.sh --portable` builds the universal app with its own Python in `build/portable/`.

```sh
./scripts/build-macos.sh
python3 scripts/check-service.py
```

For a browser checkout without a macOS build, build the converter with `mkdir -p build && sh scripts/build-kindle-converter.sh "$PWD/build/koshelf-kindle-converter" "$PWD/build/KindleConverterNotices.txt"`.

Quit the installed app before copying `build/KoShelf.app` to `~/Applications/`. The smoke test starts the packaged service on a local port using a temporary library.

Run `./scripts/package-macos.sh` to build the self-contained installer in `dist/` and write its SHA-256 checksum beside it. It checks the two standalone Python archives against pinned SHA-256 digests, removes parts of Python that KoShelf doesn’t use, and includes KoShelf’s license, `PythonNotices.txt`, and `KindleConverterNotices.txt`. A portable build runs only its bundled Python. To smoke-test the portable build with its own interpreter, run `python3 scripts/check-service.py build/portable/KoShelf.app`. Before a release, stage the converter the way `scripts/build-kindle-converter.sh` does, then run `govulncheck ./...` in that folder for both `GOARCH=arm64` and `GOARCH=amd64`.

```sh
python3 -B -m unittest discover -q
node --check static/app.js
```

`app.py` serves the local API and handles EPUB and library operations. `genre_cleanup.py` implements offline genre suggestions. The shared interface lives in `static/`. `launcher.py` starts the desktop app or browser service, and `native/KoShelf.swift` hosts the interface and manages the desktop service. See [TODO.md](TODO.md) for the roadmap and [CHANGELOG.md](CHANGELOG.md) for released changes.

## License

KoShelf is released under the [MIT License](LICENSE). The Kindle converter and the bundled Python include third-party software under their own licenses. Their notices ship in the app as `KindleConverterNotices.txt` and [`PythonNotices.txt`](notices/PythonNotices.txt).

KoShelf is an independent project. It is not affiliated with or endorsed by Amazon or the KOReader project. Kindle is a trademark of Amazon.com, Inc. or its affiliates.
