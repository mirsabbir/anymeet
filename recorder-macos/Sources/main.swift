import AppKit
import AVFoundation

/// Menu-bar app. Left click starts or stops a recording; right click opens the menu.
/// After a recording stops, the transcriber runs on the meeting folder (unless
/// turned off in Settings).
///
/// `--record-for <seconds>` records once without transcribing and quits (for testing).
/// `--show meetings[/<id>]|settings` opens that window on launch.
final class AppDelegate: NSObject, NSApplicationDelegate {
    enum State { case idle, starting, recording(Date), stopping }

    private let statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
    private let recorder = Recorder()
    static let recordingChanged = Notification.Name("RecordingChanged")
    /// Set at launch so the Meetings window can start and stop recordings too.
    private(set) static weak var shared: AppDelegate?

    private var state = State.idle { didSet { render(); NotificationCenter.default.post(name: Self.recordingChanged, object: nil) } }
    private var folder: URL?
    private var lastMessage = "Ready" { didSet { NotificationCenter.default.post(name: Self.recordingChanged, object: nil) } }
    private var ticker: Timer?

    func applicationDidFinishLaunching(_ notification: Notification) {
        Self.shared = self
        Self.resetPrivacyAfterUpdate()
        statusItem.button?.target = self
        statusItem.button?.action = #selector(clicked)
        // On press, not release: the timer redraws every second, and a redraw between
        // press and release made macOS drop the click (Stop did nothing).
        statusItem.button?.sendAction(on: [.leftMouseDown, .rightMouseDown])
        statusItem.button?.font = .monospacedDigitSystemFont(ofSize: NSFont.systemFontSize, weight: .regular)
        recorder.onStreamError = { [weak self] error in
            DispatchQueue.main.async { self?.lastMessage = "⚠️ Recording stopped: \(error.localizedDescription)"; self?.render() }
        }
        NotificationCenter.default.addObserver(forName: TranscriptionRunner.meetingsChanged, object: nil, queue: .main) { [weak self] _ in
            self?.render()
        }
        render()
        let args = CommandLine.arguments
        if let i = args.firstIndex(of: "--show"), i + 1 < args.count {
            let parts = args[i + 1].split(separator: "/", maxSplits: 1).map(String.init)  // meetings/<id>
            let view = parts[0]
            Task { @MainActor in
                view == "settings" ? WebWindows.shared.showSettings() : WebWindows.shared.showMeetings(id: parts.count > 1 ? parts[1] : nil)
                // `--snapshot <png>`: testing aid, saves the window once it has rendered.
                if let j = args.firstIndex(of: "--snapshot"), j + 1 < args.count {
                    try? await Task.sleep(for: .seconds(3))
                    await WebWindows.shared.snapshot(view == "settings" ? "settings" : "meetings", to: args[j + 1])
                }
            }
        }
        // Opened by hand: show a window, since the menu-bar icon can be hidden behind the notch.
        if !args.contains("--show") && !args.contains("--record-for") {
            Task { @MainActor in WebWindows.shared.showMeetings() }
        }
        if let i = args.firstIndex(of: "--record-for"), i + 1 < args.count, let seconds = Double(args[i + 1]) {
            Task { @MainActor in
                await start()
                try? await Task.sleep(for: .seconds(seconds))
                await stop(transcribe: false)
                NSApp.terminate(nil)
            }
        }
    }

    /// Double-clicking the app again while it runs brings the Meetings window back.
    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        MainActor.assumeIsolated { WebWindows.shared.showMeetings() }
        return false
    }

    @objc private func clicked() {
        if NSApp.currentEvent?.type == .rightMouseDown { showMenu(); return }
        switch state {
        case .idle: Task { @MainActor in await start() }
        case .recording: Task { @MainActor in await stop(transcribe: Preferences.autoTranscribe) }
        case .starting, .stopping: break
        }
    }

    // MARK: Recording

    @MainActor func start() async {
        guard case .idle = state else { return }  // the tray and the window can both ask
        guard await permissionsGranted() else { return }
        let name = DateFormatter.folderName.string(from: Date())
        let folder = Preferences.meetingsRoot.appending(path: name)
        state = .starting
        do {
            try await recorder.start(in: folder)
            self.folder = folder
            lastMessage = "Recording"
            state = .recording(Date())
            ticker = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { [weak self] _ in self?.render() }
        } catch {
            lastMessage = "⚠️ Couldn't start: \(error.localizedDescription)"
            state = .idle
        }
    }

    @MainActor func stop(transcribe: Bool) async {
        guard case .recording = state else { return }
        state = .stopping
        ticker?.invalidate()
        let problems = await recorder.stop()
        state = .idle
        guard let folder else { return }
        NotificationCenter.default.post(name: TranscriptionRunner.meetingsChanged, object: nil)
        if !problems.isEmpty {
            lastMessage = "⚠️ " + problems.joined(separator: "; ")
            notify("Recording problem", lastMessage)
        } else {
            lastMessage = "Saved \(folder.lastPathComponent)"
        }
        guard transcribe else { return }
        let started = TranscriptionRunner.shared.start(folder) { [weak self] ok in
            self?.lastMessage = ok ? "Transcript ready: \(folder.lastPathComponent)" : "⚠️ Transcription problem, see transcribe.log"
            self?.notify(ok ? "Transcript ready" : "Transcription problem", folder.lastPathComponent)
            self?.render()
        }
        if started { lastMessage = "Transcribing \(folder.lastPathComponent)" }
        render()
    }

    @MainActor private func permissionsGranted() async -> Bool {
        if !CGPreflightScreenCaptureAccess() {
            Self.resetPrivacy("ScreenCapture")  // drop a stale entry so macOS asks again for this build
            CGRequestScreenCaptureAccess()
            lastMessage = "⚠️ Allow Screen Recording, then restart the app"
            render()
            NSWorkspace.shared.open(URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture")!)
            return false
        }
        if AVCaptureDevice.authorizationStatus(for: .audio) == .denied { Self.resetPrivacy("Microphone") }
        if await AVCaptureDevice.requestAccess(for: .audio) { return true }
        lastMessage = "⚠️ Allow the microphone in System Settings → Privacy & Security"
        render()
        return false
    }

    /// Ad-hoc signed builds get a new code identity on every update, so macOS keeps an
    /// "on" switch that no longer applies to the new build. On the first launch of a new
    /// version, and when a permission is missing, the app clears its own entries
    /// (`tccutil reset <service> <its bundle ID>`) so macOS asks again. A signed build
    /// would not need this.
    private static func resetPrivacy(_ services: String...) {
        guard let id = Bundle.main.bundleIdentifier else { return }
        for service in services {
            let p = Process()
            p.executableURL = URL(filePath: "/usr/bin/tccutil")
            p.arguments = ["reset", service, id]
            p.standardOutput = FileHandle.nullDevice
            p.standardError = FileHandle.nullDevice
            try? p.run()
            p.waitUntilExit()
        }
    }

    private static func resetPrivacyAfterUpdate() {
        let version = Updater.current
        guard !version.contains("dev"), UserDefaults.standard.string(forKey: "lastLaunchedVersion") != version else { return }
        if UserDefaults.standard.string(forKey: "lastLaunchedVersion") != nil { resetPrivacy("ScreenCapture", "Microphone") }
        UserDefaults.standard.set(version, forKey: "lastLaunchedVersion")
    }

    /// The Recording shape in ui/src/types.ts.
    var recording: [String: Any] {
        var info: [String: Any] = ["message": lastMessage]
        switch state {
        case .idle: info["state"] = "idle"
        case .starting: info["state"] = "starting"
        case .stopping: info["state"] = "stopping"
        case .recording(let since):
            info["state"] = "recording"
            info["startedAt"] = ISO8601DateFormatter().string(from: since)
        }
        return info
    }

    // MARK: UI

    private var shownIcon = ""

    private func render() {
        guard let button = statusItem.button else { return }
        let transcribing = !TranscriptionRunner.shared.running.isEmpty
        let (icon, tint, title): (String, NSColor?, String) = switch state {
        case .recording(let since): ("bubble", .systemRed, " " + Self.clock(Date().timeIntervalSince(since)))
        case .starting, .stopping: ("hourglass", nil, "")
        case .idle: (transcribing ? "text.bubble" : "bubble", nil, "")
        }
        // Only swap the image when the state changes; the timer just updates the text.
        if icon != shownIcon {
            button.image = icon == "bubble" ? Self.bubble : NSImage(systemSymbolName: icon, accessibilityDescription: "AnyMeet")
            shownIcon = icon
        }
        button.contentTintColor = tint
        if button.title != title { button.title = title }
    }

    /// The app icon's speech bubble with a small waveform, as a menu-bar template image.
    private static let bubble: NSImage = {
        let image = NSImage(size: NSSize(width: 18, height: 18), flipped: true) { _ in
            let body = NSBezierPath(roundedRect: NSRect(x: 1.5, y: 2.5, width: 15, height: 11), xRadius: 3.6, yRadius: 3.6)
            body.move(to: NSPoint(x: 5.5, y: 13))
            body.line(to: NSPoint(x: 4.5, y: 16.5))
            body.line(to: NSPoint(x: 9, y: 13))
            body.close()
            NSColor.black.setFill()
            body.fill()
            NSGraphicsContext.current?.compositingOperation = .destinationOut
            for (x, y, h) in [(5.6, 6.0, 4.0), (8.3, 4.6, 6.8), (11.0, 6.6, 2.8)] {
                NSBezierPath(roundedRect: NSRect(x: x, y: y, width: 1.6, height: h), xRadius: 0.8, yRadius: 0.8).fill()
            }
            return true
        }
        image.isTemplate = true  // macOS colors it for light and dark menu bars
        image.accessibilityDescription = "AnyMeet"
        return image
    }()

    private func showMenu() {
        let menu = NSMenu()
        menu.addItem(withTitle: lastMessage, action: nil, keyEquivalent: "").isEnabled = false
        menu.addItem(.separator())
        let recording = if case .recording = state { true } else { false }
        menu.addItem(withTitle: recording ? "Stop Recording" : "Start Recording", action: #selector(clicked), keyEquivalent: "").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Open AnyMeet", action: #selector(openMeetings), keyEquivalent: "o").target = self
        menu.addItem(withTitle: "Settings…", action: #selector(openSettings), keyEquivalent: ",").target = self
        menu.addItem(.separator())
        menu.addItem(withTitle: "Quit AnyMeet", action: #selector(NSApplication.terminate(_:)), keyEquivalent: "q")
        statusItem.menu = menu
        statusItem.button?.performClick(nil)
        statusItem.menu = nil  // keep left click as the one-click record button
    }

    @objc private func openMeetings() { MainActor.assumeIsolated { WebWindows.shared.showMeetings(id: folder?.lastPathComponent) } }
    @objc private func openSettings() { MainActor.assumeIsolated { WebWindows.shared.showSettings() } }

    private func notify(_ title: String, _ body: String) {
        let script = "display notification \(Self.quoted(body)) with title \(Self.quoted(title))"
        let p = Process()
        p.executableURL = URL(filePath: "/usr/bin/osascript")
        p.arguments = ["-e", script]
        try? p.run()
    }

    private static func quoted(_ s: String) -> String {
        "\"" + s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "\"", with: "\\\"") + "\""
    }

    private static func clock(_ seconds: TimeInterval) -> String {
        let s = Int(seconds)
        return s >= 3600 ? String(format: "%d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60)
                         : String(format: "%02d:%02d", s / 60, s % 60)
    }
}

extension DateFormatter {
    static let folderName: DateFormatter = {
        let f = DateFormatter()
        f.dateFormat = "yyyy-MM-dd_HHmm"
        return f
    }()
}

let app = NSApplication.shared
let delegate = AppDelegate()
app.delegate = delegate
app.setActivationPolicy(.accessory)
app.run()
