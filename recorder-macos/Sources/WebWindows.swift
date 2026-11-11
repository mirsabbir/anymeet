import AppKit
import AVFoundation
import WebKit

/// The Settings and Meetings windows: the shared React UI (ui/, bundled as
/// Resources/ui/index.html) in a WKWebView. The UI calls the host through
/// window.webkit.messageHandlers.bridge; methods mirror HostApi in ui/src/types.ts.
@MainActor
final class WebWindows: NSObject, WKScriptMessageHandlerWithReply, NSWindowDelegate {
    static let shared = WebWindows()
    private var windows: [String: (window: NSWindow, web: WKWebView)] = [:]

    override init() {
        super.init()
        NotificationCenter.default.addObserver(forName: TranscriptionRunner.meetingsChanged, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { WebWindows.shared.emit("meetingsChanged") }
        }
        NotificationCenter.default.addObserver(forName: AppDelegate.recordingChanged, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { WebWindows.shared.emit("recordingChanged") }
        }
        NotificationCenter.default.addObserver(forName: ModelDownloads.changed, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { WebWindows.shared.emit("modelsChanged") }
        }
    }

    func showMeetings(id: String? = nil) { show("meetings", id: id, title: "AnyMeet", size: NSSize(width: 1180, height: 760)) }
    func showSettings(tab: String? = nil) { show("settings", id: tab, title: "AnyMeet Settings", size: NSSize(width: 860, height: 600)) }

    func window(_ view: String) -> (NSWindow, WKWebView)? { windows[view].map { ($0.window, $0.web) } }

    private func show(_ view: String, id: String?, title: String, size: NSSize) {
        if let existing = windows[view] {
            if id != nil { load(existing.web, view: view, id: id) }
            existing.window.makeKeyAndOrderFront(nil)
            existing.window.orderFrontRegardless()  // in front even when macOS declines to activate the app
            NSApp.activate()
            return
        }
        let config = WKWebViewConfiguration()
        config.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "bridge")
        config.setURLSchemeHandler(MediaSchemeHandler(), forURLScheme: MediaSchemeHandler.scheme)
        let web = WKWebView(frame: .zero, configuration: config)
        let window = NSWindow(contentRect: NSRect(origin: .zero, size: size),
                              styleMask: [.titled, .closable, .miniaturizable, .resizable], backing: .buffered, defer: false)
        window.title = title
        window.contentView = web
        window.minSize = NSSize(width: 560, height: 420)
        window.isReleasedWhenClosed = false
        window.delegate = self
        window.center()
        windows[view] = (window, web)
        load(web, view: view, id: id)
        window.makeKeyAndOrderFront(nil)
        window.orderFrontRegardless()  // in front even when macOS declines to activate the app
        NSApp.activate()
    }

    private func load(_ web: WKWebView, view: String, id: String?) {
        guard let index = Bundle.main.url(forResource: "index", withExtension: "html", subdirectory: "ui"),
              var url = URLComponents(url: index, resolvingAgainstBaseURL: false) else { return }
        url.fragment = "/" + view + (id.map { "/" + $0 } ?? "")
        web.loadFileURL(url.url ?? index, allowingReadAccessTo: index.deletingLastPathComponent())
    }

    func windowWillClose(_ notification: Notification) {
        windows = windows.filter { $0.value.window !== notification.object as? NSWindow }
    }

    /// Testing aid: saves a PNG of an open window's web content.
    func snapshot(_ view: String, to path: String) async {
        guard let web = windows[view]?.web, let image = try? await web.takeSnapshot(configuration: nil),
              let tiff = image.tiffRepresentation, let png = NSBitmapImageRep(data: tiff)?.representation(using: .png, properties: [:])
        else { return }
        try? png.write(to: URL(filePath: path))
    }

    func emit(_ event: String) {
        for (_, entry) in windows {
            entry.web.evaluateJavaScript("window.__hostEvent && window.__hostEvent('\(event)')")
        }
    }

    // MARK: Bridge

    func userContentController(_ controller: WKUserContentController, didReceive message: WKScriptMessage) async -> (Any?, String?) {
        guard let body = message.body as? [String: Any], let method = body["method"] as? String else { return (nil, "bad message") }
        do {
            return (try await handle(method, body["params"] as? [String: Any] ?? [:]), nil)
        } catch {
            return (nil, error.localizedDescription)
        }
    }

    private func handle(_ method: String, _ p: [String: Any]) async throws -> Any? {
        switch method {
        case "getRecording":
            return AppDelegate.shared?.recording ?? ["state": "idle", "message": ""]
        case "startRecording":
            await AppDelegate.shared?.start()
        case "stopRecording":
            await AppDelegate.shared?.stop()
        case "checkForUpdate":
            return await Updater.check(force: p["force"] as? Bool ?? false)
        case "installUpdate":
            guard AppDelegate.shared?.recording["state"] as? String == "idle" else { throw BridgeError("stop the recording first") }
            guard TranscriptionRunner.shared.running.isEmpty else { throw BridgeError("wait until the transcript is finished") }
            try await Updater.install()
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.3) { NSApp.terminate(nil) }
        case "deleteMeeting":
            guard let folder = MeetingStore.folder(p["id"] as? String ?? "") else { throw BridgeError("meeting not found") }
            if TranscriptionRunner.shared.running.contains(folder.path) { throw BridgeError("wait until the transcript is finished") }
            if p["cloudToo"] as? Bool == true {  // the cloud copy first: if that fails, nothing is gone
                _ = try await TranscriptionRunner.output(["anymeet", "forget", folder.path])
            }
            if FileManager.default.fileExists(atPath: folder.path) {  // forget removes a folder that held only cloud records
                try FileManager.default.trashItem(at: folder, resultingItemURL: nil)  // recoverable from the Trash
            }
            NotificationCenter.default.post(name: TranscriptionRunner.meetingsChanged, object: nil)
        case "backup":
            try CloudJobs.run(["backup"] + CloudJobs.idle(p["ids"] as? [String] ?? []).map(\.path))
        case "freeUp":
            try CloudJobs.run(["free"] + CloudJobs.idle(p["ids"] as? [String] ?? []).map(\.path))
        case "restore":
            guard let folder = MeetingStore.folder(p["id"] as? String ?? "") else { throw BridgeError("meeting not found") }
            let files = p["files"] as? [String] ?? []
            try CloudJobs.run(["restore", folder.path] + (files.isEmpty ? [] : ["--files"] + files))
        case "summarize":
            guard let folder = MeetingStore.folder(p["id"] as? String ?? "") else { throw BridgeError("meeting not found") }
            try CloudJobs.run(["summarize", folder.path, "--language", Preferences.summaryLanguage])
        case "stopJob":
            guard let folder = MeetingStore.folder(p["id"] as? String ?? "") else { throw BridgeError("meeting not found") }
            FileManager.default.createFile(atPath: folder.appending(path: "job.stop").path, contents: nil)
        case "checkCloud":
            let out = try await TranscriptionRunner.output(["anymeet", "check"])
            return try JSONSerialization.jsonObject(with: out)
        case "setCloud":
            let url = (p["url"] as? String ?? "").trimmingCharacters(in: .whitespaces)
            guard url.isEmpty || URL(string: url)?.scheme == "https" else { throw BridgeError("the server address must start with https://") }
            try Preferences.setCloud(url: url, token: (p["token"] as? String ?? "").trimmingCharacters(in: .whitespaces))
            CloudJobs.atLaunch()  // list what is already in the cloud
        case "setAutoBackup":
            Preferences.autoBackup = p["on"] as? Bool ?? false
        case "setAnthropicKey":
            try Preferences.setAnthropicKey(p["key"] as? String ?? "")
        case "setAutoSummary":
            Preferences.autoSummary = p["on"] as? Bool ?? false
        case "setSummaryLanguage":
            let language = p["language"] as? String ?? ""
            guard ["auto", "en", "bn"].contains(language) else { throw BridgeError("unknown language \(language)") }
            Preferences.summaryLanguage = language
        case "listMeetings":
            return await MeetingStore.list()
        case "getMeeting":
            return await MeetingStore.get(p["id"] as? String ?? "")
        case "transcribe":
            guard let folder = MeetingStore.folder(p["id"] as? String ?? "") else { throw BridgeError("meeting not found") }
            if !TranscriptionRunner.shared.start(folder, onlyFailed: p["onlyFailed"] as? Bool ?? false) {
                throw BridgeError(TranscriptionRunner.shared.lastError)
            }
        case "stopTranscribing":
            guard let folder = MeetingStore.folder(p["id"] as? String ?? "") else { throw BridgeError("meeting not found") }
            TranscriptionRunner.shared.stop(folder)
        case "openPath":
            try open(p["path"] as? String ?? "")
        case "getSettings":
            return settings()
        case "setTranscribeMode":
            let mode = p["mode"] as? String ?? ""
            guard ["live", "after", "off"].contains(mode) else { throw BridgeError("unknown mode \(mode)") }
            Preferences.transcribeMode = mode
        case "setTranscriber":
            let id = p["id"] as? String ?? ""
            guard Preferences.transcribers.contains(where: { $0["id"] as? String == id && $0["available"] as? Bool == true })
            else { throw BridgeError("transcriber not available") }
            Preferences.transcriber = id
        case "getModels":
            return try await ModelDownloads.shared.list(refresh: p["refresh"] as? Bool ?? false)
        case "downloadModel":
            let id = p["id"] as? String ?? ""
            guard id == ModelDownloads.speakerModel
                    || Preferences.transcribers.contains(where: { $0["id"] as? String == id && $0["needsKey"] as? Bool == false })
            else { throw BridgeError("not a local model: \(id)") }
            try ModelDownloads.shared.start(id)
        case "setApiKey":
            try Preferences.setApiKey(p["key"] as? String ?? "")
        case "setHfToken":
            try Preferences.setHfToken(p["token"] as? String ?? "")
        case "setSpeakers":
            Preferences.speakers = p["on"] as? Bool ?? false
        case "setProcessor":
            let id = p["id"] as? String ?? ""
            guard Preferences.processors.contains(where: { $0["id"] as? String == id && $0["available"] as? Bool == true })
            else { throw BridgeError("can't run models on \(id)") }
            Preferences.processor = id
        case "renameSpeaker":
            guard let folder = MeetingStore.folder(p["id"] as? String ?? "") else { throw BridgeError("meeting not found") }
            if TranscriptionRunner.shared.running.contains(folder.path) { throw BridgeError("wait until the transcript is finished") }
            _ = try await TranscriptionRunner.output(["transcribe", folder.path, "--rename-speaker", p["from"] as? String ?? "", p["to"] as? String ?? ""])
            NotificationCenter.default.post(name: TranscriptionRunner.meetingsChanged, object: nil)
        case "openLink":
            guard let url = URL(string: p["url"] as? String ?? ""), url.scheme == "https", url.host == "huggingface.co"
            else { throw BridgeError("only huggingface.co links open from here") }
            NSWorkspace.shared.open(url)
        case "setGlossary":
            Preferences.glossary = (p["terms"] as? [String] ?? []).map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        default:
            throw BridgeError("unknown method \(method)")
        }
        return nil
    }

    /// Opens a file or folder, but only inside the meetings folder.
    private func open(_ path: String) throws {
        let url = URL(filePath: (path as NSString).expandingTildeInPath).standardizedFileURL
        guard url.path.hasPrefix(Preferences.meetingsRoot.standardizedFileURL.path) else { throw BridgeError("path outside the meetings folder") }
        try? FileManager.default.createDirectory(at: Preferences.meetingsRoot, withIntermediateDirectories: true)
        NSWorkspace.shared.open(url)
    }

    private func settings() -> [String: Any] {
        let displays = NSScreen.screens.map { screen -> String in
            let id = screen.deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? CGDirectDisplayID ?? 0
            let mode = CGDisplayCopyDisplayMode(id)
            return "\(screen.localizedName) · \(mode?.pixelWidth ?? 0)×\(mode?.pixelHeight ?? 0)"
        }
        return [
            "meetingsFolder": "~/" + Preferences.meetingsRoot.lastPathComponent,
            "transcribeMode": Preferences.transcribeMode,
            "transcriber": Preferences.transcriber,
            "transcribers": Preferences.transcribers,
            "apiKeySet": Preferences.apiKeySet,
            "speakers": Preferences.speakers,
            "hfTokenSet": Preferences.hfTokenSet,
            "processor": Preferences.processor,
            "processors": Preferences.processors,
            "glossary": Preferences.glossary,
            "displays": displays,
            "microphone": AVCaptureDevice.default(for: .audio)?.localizedName ?? "",
            "cloudUrl": Preferences.cloudURL,
            "cloudTokenSet": Preferences.cloudTokenSet,
            "autoBackup": Preferences.autoBackup,
            "anthropicKeySet": Preferences.anthropicKeySet,
            "autoSummary": Preferences.autoSummary,
            "summaryLanguage": Preferences.summaryLanguage,
        ]
    }
}

struct BridgeError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}
