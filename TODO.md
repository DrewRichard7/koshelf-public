# KoShelf roadmap

This file lists open work. When an item ships, move it to [CHANGELOG.md](CHANGELOG.md).

## Features

- [ ] **Sync with the Records project.** Identify which book and reading records to share, and decide how updates should flow between the projects.
- [ ] **Author and award-tag cleanup.** Suggest author aliases and cleanup of award labels.
- [ ] **Three-way library review.** Include the connected Kindle in the Mac and iCloud Drive review, so all three libraries are reviewed in one run. Verify the iPhone-to-Kindle workflow.
- [ ] **MTP Kindle delivery.** Explore USB delivery on macOS to Kindle models that use MTP instead of mounting as a drive.
- [ ] **Selective sync.** Let a sync review skip individual changes or whole categories (copies to Mac, copies to Kindle, replacements, reading statuses, folder removals), so applying a review isn't all or nothing.
- [ ] **In-app updates.** Check for new releases and install them without downloading a new disk image each time. If a full download is unavoidable, make updating simpler than Calibre's.
- [ ] **Linux support.** Make KoShelf work fully on Linux, including Kindle detection, eject, and the Kindle converter.
- [ ] **Cross-platform browser version.** Check that browser mode works across platforms and browsers, and decide which platforms to support.
- [ ] **KOReader plugin for wireless sync.** Build a KOReader plugin that syncs with KoShelf over the local network when the Kindle is awake and nearby.

## Bugs

- [ ] Investigate problems with a sync backup copy named `… (backup <hash>).epub`. Sync creates these names when a Kindle book conflicts with a different Mac version.

## Testing

- [ ] Test 1.1.0's Kindle sync, eject, and Send to Kindle on a physical Kindle.
- [ ] Test the Intel build on an Intel Mac, if one becomes available.

## Maintenance

- [ ] Skip the full library rescan when the window regains focus and nothing has changed.
- [ ] Add a memory watchdog for the Kindle converter, in addition to its sandbox, `GOMEMLIMIT`, and timeout.
- [ ] Before Go 1.28 ships (expected around February 2027), decide whether to raise the minimum to macOS 13. Go 1.27 and later require macOS 13, and Go 1.26 stops receiving fixes then.
