# KoShelf roadmap

This file lists open work. When an item ships, move it to [CHANGELOG.md](CHANGELOG.md).

## Features

- [ ] **Cross-platform app and in-app updates.** A standalone app on macOS and Linux that updates itself when the user asks, without a new disk image download each time. Updates stay manual and opt-in. See [the plan](docs/cross-platform-and-updates.md).
    - [ ] Phase 1: make the core run on Linux (Kindle detection, eject, converter).
    - [ ] Phase 2: settle the browser version and the supported platforms.
    - [ ] Phase 3: prototype and choose the shell (Tauri, or Sparkle as the fallback).
    - [ ] Phase 4: move the macOS app to the chosen shell.
    - [ ] Phase 5: ship manual, signed updates.
    - [ ] Phase 6: ship the standalone Linux app.
- [ ] **Sync with the Records project.** Identify which book and reading records to share, and decide how updates should flow between the projects.
- [ ] **Author and award-tag cleanup.** Suggest author aliases and cleanup of award labels.
- [ ] **Three-way library review.** Include the connected Kindle in the Mac and iCloud Drive review, so all three libraries are reviewed in one run. Verify the iPhone-to-Kindle workflow.
- [ ] **MTP Kindle delivery.** Explore USB delivery on macOS to Kindle models that use MTP instead of mounting as a drive.
- [ ] **Selective sync.** Let a sync review skip individual changes or whole categories (copies to Mac, copies to Kindle, replacements, reading statuses, folder removals), so applying a review isn't all or nothing.
- [ ] **Wireless transfer (Wi-Fi or Bluetooth).** Sync with KoShelf without a cable when the Kindle is awake and nearby. This will likely be a KOReader plugin built in its own repository, with KoShelf providing the Mac side. When that repository exists, link it here.

## Bugs

- [ ] Investigate problems with a sync backup copy named `… (backup <hash>).epub`. Sync creates these names when a Kindle book conflicts with a different Mac version.

## Testing

- [ ] Test 1.1.0's Kindle sync, eject, and Send to Kindle on a physical Kindle.
- [ ] Test the Intel build on an Intel Mac, if one becomes available.

## Maintenance

- [ ] Skip the full library rescan when the window regains focus and nothing has changed.
- [ ] Add a memory watchdog for the Kindle converter, in addition to its sandbox, `GOMEMLIMIT`, and timeout.
- [ ] Before Go 1.28 ships (expected around February 2027), decide whether to raise the minimum to macOS 13. Go 1.27 and later require macOS 13, and Go 1.26 stops receiving fixes then.
