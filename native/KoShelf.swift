import AppKit
import Darwin
import WebKit
import UniformTypeIdentifiers

final class AppDelegate: NSObject, NSApplicationDelegate, WKNavigationDelegate, WKUIDelegate, WKScriptMessageHandler {
    var window: NSWindow!
    var webView: WKWebView!
    var service: Process?
    var output = ""
    var loaded = false
    var quitting = false
    var serviceURL: URL?
    let pipe = Pipe()

    func applicationDidFinishLaunching(_ notification: Notification) {
        buildMenu()
        let config = WKWebViewConfiguration()
        config.userContentController.add(self, name: "chooseICloudFolder")
        config.userContentController.addUserScript(WKUserScript(
            source: "document.documentElement.classList.add('native-window')",
            injectionTime: .atDocumentEnd, forMainFrameOnly: true
        ))
        webView = WKWebView(frame: .zero, configuration: config)
        webView.navigationDelegate = self
        webView.uiDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1180, height: 800), styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView], backing: .buffered, defer: false)
        // The delegate owns the window, including while AppKit finishes quitting.
        window.isReleasedWhenClosed = false
        window.title = "KoShelf"
        window.titleVisibility = .hidden
        window.titlebarAppearsTransparent = true
        window.titlebarSeparatorStyle = .none
        window.isMovableByWindowBackground = true
        window.minSize = NSSize(width: 650, height: 500)
        window.backgroundColor = NSColor(calibratedRed: 0.933, green: 0.910, blue: 0.863, alpha: 1)
        window.contentView = webView
        window.center()
        window.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
        webView.loadHTMLString("<html><body style='background:#f1f3eb;color:#345849;display:grid;place-items:center;height:90vh;font:30px Georgia'>KoShelf is waking up…</body></html>", baseURL: nil)
        startService()
    }

    func buildMenu() {
        let menu = NSMenu()
        let appItem = NSMenuItem(); menu.addItem(appItem)
        let appMenu = NSMenu(); appItem.submenu = appMenu
        appMenu.addItem(withTitle: "About KoShelf", action: #selector(NSApplication.orderFrontStandardAboutPanel(_:)), keyEquivalent: "")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Hide KoShelf", action: #selector(NSApplication.hide(_:)), keyEquivalent: "h")
        appMenu.addItem(.separator())
        appMenu.addItem(withTitle: "Quit KoShelf", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        let editItem = NSMenuItem(); menu.addItem(editItem)
        let edit = NSMenu(title: "Edit"); editItem.submenu = edit
        for (title, action, key) in [("Undo", "undo:", "z"), ("Cut", "cut:", "x"), ("Copy", "copy:", "c"), ("Paste", "paste:", "v"), ("Select All", "selectAll:", "a")] {
            edit.addItem(withTitle: title, action: Selector(action), keyEquivalent: key)
        }
        let windowItem = NSMenuItem(); menu.addItem(windowItem)
        let windows = NSMenu(title: "Window"); windowItem.submenu = windows
        windows.addItem(withTitle: "Minimize", action: #selector(NSWindow.performMiniaturize(_:)), keyEquivalent: "m")
        windows.addItem(withTitle: "Close", action: #selector(NSWindow.performClose(_:)), keyEquivalent: "w")
        NSApp.windowsMenu = windows
        NSApp.mainMenu = menu
    }

    func startService() {
        guard let resources = Bundle.main.resourceURL else { fail("The app’s resources are missing."); return }
        #if arch(arm64)
        let architecture = "arm64"
        #else
        let architecture = "x86_64"
        #endif
        let bundledPython = resources.appendingPathComponent("Python/\(architecture)/bin/python3").path
        let python: String?
        if FileManager.default.isExecutableFile(atPath: bundledPython) {
            python = bundledPython
        } else if Bundle.main.object(forInfoDictionaryKey: "KoShelfRequiresBundledPython") as? Bool == true {
            // Release builds run only the interpreter they were built and checked with.
            fail("This copy of KoShelf is missing its bundled Python. Download KoShelf again."); return
        } else {
            python = ["/opt/homebrew/bin/python3", "/usr/local/bin/python3", "/usr/bin/python3"]
                .first { FileManager.default.isExecutableFile(atPath: $0) && supportedPython($0) }
        }
        guard let python = python else { fail("Python 3.11 or later is required on this Mac."); return }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: python)
        process.arguments = ["-E", "-S", "-B", resources.appendingPathComponent("app.py").path,
                             "--port", "0", "--session-pipe"]
        // Never use a mounted volume as the process working directory: it must be ejectable.
        let support = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0].appendingPathComponent("KoShelf", isDirectory: true)
        do {
            try FileManager.default.createDirectory(at: support, withIntermediateDirectories: true,
                                                    attributes: [.posixPermissions: 0o700])
            process.standardError = try privateLog(in: support)
        }
        catch { fail("Could not create KoShelf’s local data folder: \(error.localizedDescription)"); return }
        process.currentDirectoryURL = support
        var environment = [
            "HOME": FileManager.default.homeDirectoryForCurrentUser.path,
            "PATH": "/usr/bin:/bin:/usr/sbin:/sbin",
            "KOSHELF_STATE_DIR": support.path
        ]
        environment["TMPDIR"] = ProcessInfo.processInfo.environment["TMPDIR"]
        process.environment = environment
        process.standardOutput = pipe
        pipe.fileHandleForReading.readabilityHandler = { [weak self] handle in
            let data = handle.availableData
            guard !data.isEmpty else { handle.readabilityHandler = nil; return }
            let text = String(decoding: data, as: UTF8.self)
            DispatchQueue.main.async { self?.received(text) }
        }
        process.terminationHandler = { [weak self] process in
            DispatchQueue.main.async {
                guard let self = self else { return }
                self.pipe.fileHandleForReading.readabilityHandler = nil
                if self.quitting { NSApp.reply(toApplicationShouldTerminate: true) }
                else if process.terminationStatus == 75 {
                    // app.py exits with 75 when another KoShelf service already uses the data folder.
                    self.fail("KoShelf is already open, either in a browser or in another copy of the app. Close it (Control-C in the terminal for a browser service), then reopen KoShelf.")
                }
                else if process.terminationStatus == 78 {
                    self.fail("KoShelf can't use its data folder. Details: ~/Library/Application Support/KoShelf/service.log")
                }
                else { self.fail("The local library service stopped. Quit and reopen KoShelf. Details: ~/Library/Application Support/KoShelf/service.log") }
            }
        }
        service = process
        do { try process.run() }
        catch { service = nil; fail("Could not start Python: \(error.localizedDescription)"); return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 20) { [weak self] in
            guard let self = self, !self.loaded, !self.quitting else { return }
            self.fail("The library service did not start in time. Quit and reopen KoShelf.")
        }
    }

    func supportedPython(_ path: String) -> Bool {
        let check = Process()
        check.executableURL = URL(fileURLWithPath: path)
        check.arguments = ["-I", "-S", "-c", "import sys; sys.exit(sys.version_info < (3, 11))"]
        check.standardOutput = FileHandle.nullDevice
        check.standardError = FileHandle.nullDevice
        do { try check.run() } catch { return false }
        check.waitUntilExit()
        return check.terminationStatus == 0
    }

    func privateLog(in support: URL) throws -> FileHandle {
        let directory = Darwin.open(support.path, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC)
        guard directory >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        defer { Darwin.close(directory) }
        var info = stat()
        guard fstat(directory, &info) == 0, info.st_uid == getuid(), fchmod(directory, 0o700) == 0 else {
            throw CocoaError(.fileWriteNoPermission)
        }
        let fd = openat(directory, "service.log", O_WRONLY | O_APPEND | O_CREAT | O_NOFOLLOW | O_NONBLOCK | O_CLOEXEC, 0o600)
        guard fd >= 0 else { throw CocoaError(.fileWriteNoPermission) }
        guard fstat(fd, &info) == 0, info.st_mode & S_IFMT == S_IFREG,
              info.st_uid == getuid(), info.st_nlink == 1, fchmod(fd, 0o600) == 0 else {
            Darwin.close(fd)
            throw CocoaError(.fileWriteNoPermission)
        }
        return FileHandle(fileDescriptor: fd, closeOnDealloc: true)
    }

    func received(_ text: String) {
        guard !loaded else { return }
        output += text
        guard output.utf8.count <= 4096 else { fail("KoShelf returned an invalid service address."); return }
        let prefix = "KoShelf session: "
        while let newline = output.firstIndex(of: "\n") {
            let line = output[..<newline]
            output = String(output[output.index(after: newline)...])
            guard line.hasPrefix(prefix),
                  let url = URL(string: String(line.dropFirst(prefix.count))),
                  url.scheme == "http", url.host == "127.0.0.1", url.path == "/",
                  let port = url.port, (1...65535).contains(port),
                  url.user == nil, url.password == nil, url.query == nil,
                  let fragment = url.fragment,
                  fragment.range(of: "^session=[0-9a-f]{64}$", options: .regularExpression) != nil,
                  let origin = URL(string: "http://127.0.0.1:\(port)/") else { continue }
            loaded = true
            serviceURL = origin
            output = ""
            webView.load(URLRequest(url: url))
            return
        }
    }

    func fail(_ message: String) {
        let alert = NSAlert(); alert.messageText = "KoShelf couldn’t start"; alert.informativeText = message
        alert.addButton(withTitle: "Quit")
        alert.runModal(); NSApp.terminate(nil)
    }
    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction, decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
        guard let url = navigationAction.request.url else { decisionHandler(.cancel); return }
        if url.absoluteString == "about:blank" || (serviceURL != nil && url.host == "127.0.0.1" &&
               url.port == serviceURL?.port && url.scheme == "http" && url.path == "/" &&
               url.user == nil && url.password == nil) { decisionHandler(.allow) }
        else { decisionHandler(.cancel) }
    }
    func webView(_ webView: WKWebView, runOpenPanelWith parameters: WKOpenPanelParameters,
                 initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping ([URL]?) -> Void) {
        let origin = frame.securityOrigin
        guard frame.isMainFrame, origin.protocol == "http", origin.host == "127.0.0.1",
              origin.port == serviceURL?.port else {
            completionHandler(nil)
            return
        }

        // WebKit does not pass the input's accepted extensions to this delegate.
        webView.evaluateJavaScript("document.getElementById('font-dialog')?.open === true") { [weak self] result, _ in
            guard let self, self.window.attachedSheet == nil else { completionHandler(nil); return }
            let choosingFonts = result as? Bool == true
            let panel = NSOpenPanel()
            panel.prompt = "Choose"
            panel.allowsMultipleSelection = parameters.allowsMultipleSelection
            panel.canChooseFiles = true
            panel.canChooseDirectories = false

            if choosingFonts {
                panel.title = "Choose fonts"
                panel.allowedContentTypes = ["ttf", "otf", "ttc"].compactMap { UTType(filenameExtension: $0) }
            } else {
                panel.title = parameters.allowsMultipleSelection ? "Import EPUBs" : "Choose cover image"
                panel.allowedContentTypes = parameters.allowsMultipleSelection ? [.epub] : [.jpeg, .png]
            }
            panel.beginSheetModal(for: self.window) { response in
                completionHandler(response == .OK ? panel.urls : nil)
            }
        }
    }

    func userContentController(_ userContentController: WKUserContentController, didReceive message: WKScriptMessage) {
        let origin = message.frameInfo.securityOrigin
        guard message.name == "chooseICloudFolder", message.frameInfo.isMainFrame,
              origin.protocol == "http", origin.host == "127.0.0.1", origin.port == serviceURL?.port,
              window.attachedSheet == nil else { return }
        let panel = NSOpenPanel()
        panel.title = "Choose a KoShelf folder in iCloud Drive"
        panel.prompt = "Choose"
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.beginSheetModal(for: window) { [weak self] response in
            let path = response == .OK ? panel.url?.path : nil
            guard let data = try? JSONEncoder().encode(path),
                  let argument = String(data: data, encoding: .utf8) else { return }
            self?.webView.evaluateJavaScript("window.onICloudFolderChosen?.(\(argument))")
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        if let service = service, service.isRunning {
            if !quitting {
                quitting = true
                service.terminate()
                // The service finishes the current file operation before exiting.
                // Explain the wait if it takes more than a moment.
                DispatchQueue.main.asyncAfter(deadline: .now() + 1.5) { [weak self] in
                    guard let self, service.isRunning, self.window.attachedSheet == nil else { return }
                    let alert = NSAlert()
                    alert.messageText = "Finishing the current operation…"
                    alert.informativeText = "KoShelf will close as soon as it finishes writing files. Quitting now could leave a partial copy on your Mac or Kindle."
                    alert.addButton(withTitle: "Keep Waiting")
                    alert.addButton(withTitle: "Quit Now")
                    alert.beginSheetModal(for: self.window) { response in
                        if response == .alertSecondButtonReturn, service.isRunning {
                            kill(service.processIdentifier, SIGKILL)
                        }
                    }
                }
            }
            return .terminateLater
        }
        return .terminateNow
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { true }
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool { window.makeKeyAndOrderFront(nil); return true }
}
let application = NSApplication.shared
let delegate = AppDelegate()
application.delegate = delegate
application.setActivationPolicy(.regular)
application.run()
