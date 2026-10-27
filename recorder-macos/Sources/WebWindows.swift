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
    }

    func showMeetings(id: String? = nil) { show("meetings", id: id, title: "Meetings", size: NSSize(width: 1180, height: 760)) }
    func showSettings() { show("settings", id: nil, title: "Settings", size: NSSize(width: 860, height: 600)) }

    private func show(_ view: String, id: String?, title: String, size: NSSize) {
        if let existing = windows[view] {
            if id != nil { load(existing.web, view: view, id: id) }
            existing.window.makeKeyAndOrderFront(nil)
            NSApp.activate()
            return
        }
        let config = WKWebViewConfiguration()
        config.userContentController.addScriptMessageHandler(self, contentWorld: .page, name: "bridge")
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
            await AppDelegate.shared?.stop(transcribe: Preferences.autoTranscribe)
        case "listMeetings":
            return await MeetingStore.list()
        case "getMeeting":
            return await MeetingStore.get(p["id"] as? String ?? "")
        case "transcribe":
            guard let folder = MeetingStore.folder(p["id"] as? String ?? "") else { throw BridgeError("meeting not found") }
            if !TranscriptionRunner.shared.start(folder) { throw BridgeError("transcription is already running or not set up") }
        case "openPath":
            try open(p["path"] as? String ?? "")
        case "getSettings":
            return settings()
        case "setAutoTranscribe":
            Preferences.autoTranscribe = p["value"] as? Bool ?? true
        case "setTranscriber":
            let id = p["id"] as? String ?? ""
            guard Preferences.transcribers.contains(where: { $0["id"] as? String == id && $0["available"] as? Bool == true })
            else { throw BridgeError("transcriber not available") }
            Preferences.transcriber = id
        case "setApiKey":
            try Preferences.setApiKey(p["key"] as? String ?? "")
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
            "autoTranscribe": Preferences.autoTranscribe,
            "transcriber": Preferences.transcriber,
            "transcribers": Preferences.transcribers,
            "apiKeySet": Preferences.apiKeySet,
            "glossary": Preferences.glossary,
            "displays": displays,
            "microphone": AVCaptureDevice.default(for: .audio)?.localizedName ?? "",
        ]
    }
}

struct BridgeError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}
