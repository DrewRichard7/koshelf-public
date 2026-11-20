# Cross-platform app and updates

This plan covers three linked roadmap items: Linux support, the cross-platform browser version, and in-app updates. They are planned together because the window framework KoShelf uses on each platform decides how updates are installed. The plan stays general on purpose. Revisit the details when this work starts.

## Goals

- KoShelf runs as a standalone app on macOS and Linux, and browser mode keeps working on both.
- An installed app can update itself without a new disk image download and a drag to Applications.
- One release process produces every platform's downloads and update files.

## Principles

- **Manual and opt-in updates.** KoShelf contacts the network only when the user chooses **Check for Updates…**. There are no background checks, and any future automatic option stays off unless the user turns it on.
- **Verified updates.** Every update is signed with the project's own key. The app refuses an update whose signature doesn't verify.
- **Honest privacy.** An update check reveals the user's IP address and app version to the release host. The README says so, and KoShelf sends nothing else.
- **A shared core and a thin shell.** The Python service and web interface stay the same on every platform. Each platform's window shell stays small and only handles windows, file pickers, the service process, and installing updates.
- **Security boundaries stay in place:** a loopback-only service, the per-launch session credential, path containment, the sandboxed converter, and DRM-free-only handling.
- **One update for the whole app.** An update replaces the app as a whole. KoShelf never runs code downloaded into a folder users can write to.

## Phases

The phases are listed in order of dependency. Phase 1 can start at any time, and it is useful even if later plans change.

### Phase 1: Make the core run on Linux

- Keep platform-specific behavior behind a small set of helpers: finding mounted volumes and the Kindle, ejecting (`diskutil` on macOS, `udisksctl` or similar on Linux), and exclusive renames.
- Hide features that depend on macOS, such as iCloud Drive sync, where they don't apply.
- Build the Kindle converter for Linux. Choose a Linux sandbox for it, or document why none is used.
- Run the test suite on Linux.

**Done when:** `./koshelf --browser` works on a Linux machine with a USB-mounted Kindle for browsing, sync, eject, and Send to Kindle.

### Phase 2: Settle the browser version

- Test browser mode in the main browsers on macOS and Linux.
- Decide which platforms and browsers are supported, and say so in the README. Windows is currently out of scope.
- Decide whether browser mode needs a stable address. Installing it as a browser app requires one, but KoShelf uses a random port for security.

**Done when:** the README lists supported platforms and browsers, and those combinations are tested.

### Phase 3: Choose the shell (decision gate)

Build a small prototype before committing.

- **Leading option: Tauri.** One shell for macOS and Linux that uses each system's web view, with a built-in updater that requires signatures. It replaces the `.app` on macOS and the AppImage on Linux.
- **Fallback: keep the Swift shell and add Sparkle on macOS.** On Linux, use browser mode or a separately packaged app.

The prototype should answer these questions:

- Can the new shell start the bundled Python service and receive the session secret the same way the Swift shell does?
- Does a full update work for an app that is not signed with an Apple Developer ID? Test the whole cycle: install an old build, check, install, and relaunch.
- How do app size, startup time, and memory compare with the current app?
- How much of the Swift shell needs rebuilding? That includes the file and folder pickers, the quit prompt, navigation limits, and private logs.
- Does the new shell keep the current security boundaries? Review its permission model.

**Done when:** the shell is chosen, and the reasons are recorded here.

### Phase 4: Move the macOS app to the chosen shell

- Match the Swift shell's behavior. Keep the interface choices recorded in `CLAUDE.md`.
- Review the new shell's security before release.
- Release it as a normal disk image. Users of earlier versions install it manually once. After that, updates arrive in the app.

**Done when:** the new macOS app passes the packaged-service check and manual testing, and has been released.

### Phase 5: Ship manual updates

- **Signing key.** Generate the project's update key. Keep the private key in the Keychain with one offline backup. Protect the GitHub account with two-factor authentication. If the key is lost, installed apps can no longer update. If it leaks, someone could ship a malicious update.
- **Release files.** Extend the packaging script to produce signed update archives and a small file listing the latest version. Attach both to each GitHub release so the app can find them at a fixed "latest release" address.
- **In-app flow.**
  1. The user chooses **Check for Updates…**.
  2. KoShelf shows the new version and its notes.
  3. The user confirms.
  4. KoShelf finishes active file operations, installs the update, and relaunches.

  Errors must leave the installed app untouched.
- **Testing.** Before each release, update an older build from a local test feed.

**Done when:** a user on the previous version updates to a new release from inside the app on macOS.

### Phase 6: Standalone Linux app

- Package the shell, the bundled Python, and the converter as an AppImage. Don't use Flatpak or Snap: their sandboxes get in the way of USB-mounted Kindles.
- Use the same signing key, release files, and in-app update flow as macOS.
- Test on at least one Debian-based and one Fedora-based distribution.

**Done when:** a Linux user can download one file, run KoShelf, and update it from inside the app.

### Later

- Smaller updates that download only what changed.
- Windows, if it becomes a goal.

## Background research (November 2026)

These findings shaped the plan. Recheck them before relying on them.

- **Homebrew isn't an option for KoShelf.** It no longer supports casks that fail Gatekeeper (unsigned or un-notarized apps), and it is removing its `--no-quarantine` option.
- **Sparkle works only on macOS.** It updates ad hoc signed apps when updates carry its EdDSA signature, it handles quarantine, and current versions require macOS 12 or later.
- **Tauri's updater** always requires signatures. It can be triggered only from app code, so checks stay manual. It updates macOS app bundles, Linux AppImages, and Windows installers, and it can read a static file attached to a GitHub release.
