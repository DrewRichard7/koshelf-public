# KoShelf roadmap

This file lists open work. When an item ships, move it to [CHANGELOG.md](CHANGELOG.md).

## Features

- [ ] **Sync with the Records project.** Identify which book and reading records to share, and decide how updates should flow between the projects.
- [ ] **Author and award-tag cleanup.** Suggest author aliases and cleanup of award labels.
- [ ] **Three-way library review.** Include the connected Kindle in the Mac and iCloud Drive review, so all three libraries are reviewed in one run. Verify the iPhone-to-Kindle workflow.
- [ ] **MTP Kindle delivery.** Explore USB delivery on macOS to Kindle models that use MTP instead of mounting as a drive.
- [ ] **Set reading progress manually.** Let the book details set a reading percentage, not only a status, so progress from reading elsewhere can win the next sync.
- [ ] **Apple Books reading progress.** Find out whether Apple Books exposes reading positions, and if it does, sync them with KOReader progress.
- [ ] **KOReader progress on Mac and iPhone.** Look into syncing progress with KOReader on other devices, for example through KOReader's progress sync server.
- [ ] **Selective sync.** Let a sync review skip individual changes or whole categories (copies to Mac, copies to Kindle, replacements, reading statuses, folder removals), so applying a review isn't all or nothing.
- [ ] **In-app updates.** Check for new releases and install them without downloading a new disk image each time. If a full download is unavoidable, make updating simpler than Calibre's.
- [ ] **Linux support.** Make KoShelf work fully on Linux, including Kindle detection, eject, and the Kindle converter.
- [ ] **Cross-platform browser version.** Check that browser mode works across platforms and browsers, and decide which platforms to support.
- [ ] **Wireless transfer (Wi-Fi or Bluetooth).** Sync with KoShelf without a cable when the Kindle is awake and nearby. This will likely be a KOReader plugin built in its own repository, with KoShelf providing the Mac side. When that repository exists, link it here.

## Testing

- [ ] Test 1.1.0's Kindle sync, eject, and Send to Kindle on a physical Kindle.
- [ ] Test the Intel build on an Intel Mac, if one becomes available.

## Maintenance

- [ ] Before Go 1.28 ships (expected around February 2027), decide whether to raise the minimum to macOS 13. Go 1.27 and later require macOS 13, and Go 1.26 stops receiving fixes then.
- [ ] Clean up repository structure: main code in src/ directory, test files in tests/ directory, artifacts in artifacts/ directory, etc.
