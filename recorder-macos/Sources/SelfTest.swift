import AppKit
import ScreenCaptureKit
import WebKit

/// What the app records with: ScreenCaptureKit normally, a fake for the self-test.
protocol Capture: AnyObject {
    var onStreamError: ((Error) -> Void)? { get set }
    func start(in folder: URL, live: Bool) async throws
    func stop() async -> [String]
    /// Report a stop that came from macOS rather than from AnyMeet (self-test only).
    func simulateSystemStop() async
}

extension Recorder: Capture {}

/// Writes a short spoken WAV as mic.wav, so the self-test exercises everything except
/// screen capture (which needs a permission a test run can't grant itself).
final class FakeCapture: Capture {
    var onStreamError: ((Error) -> Void)?
    private var folder: URL?
    private var feeder: Task<Void, Never>?
    private let speech = FileManager.default.temporaryDirectory.appending(path: "anymeet-selftest-speech.wav")

    func start(in folder: URL, live: Bool) async throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        self.folder = folder
        let say = Process()
        say.executableURL = URL(filePath: "/usr/bin/say")
        say.arguments = ["--file-format=WAVE", "--data-format=LEI16@16000", "-o", speech.path,
                         "Hello, this is an AnyMeet self test. We deploy the API next quarter."]
        try say.run()
        say.waitUntilExit()
        guard live else { return }
        // Feed live/mic.wav at real-time pace (16 kHz × 2 bytes), then keep adding
        // silence, like a microphone in a quiet room, until the recording stops.
        let target = folder.appending(path: "live/mic.wav")
        let audio = (try? Data(contentsOf: speech)) ?? Data()
        feeder = Task.detached {
            try? FileManager.default.createDirectory(at: target.deletingLastPathComponent(), withIntermediateDirectories: true)
            FileManager.default.createFile(atPath: target.path, contents: nil)
            guard let out = try? FileHandle(forWritingTo: target) else { return }
            defer { try? out.close() }
            let step = 6_400  // 0.2 s
            var offset = 0
            while !Task.isCancelled {
                let piece = offset < audio.count ? audio[offset..<min(offset + step, audio.count)] : Data(count: step)
                out.write(piece)
                offset += step
                try? await Task.sleep(for: .milliseconds(200))
            }
        }
    }

    func simulateSystemStop() async {
        onStreamError?(NSError(domain: SCStreamErrorDomain, code: SCStreamError.Code.userStopped.rawValue))
    }

    func stop() async -> [String] {
        guard let folder else { return ["no folder"] }
        feeder?.cancel()
        await feeder?.value
        do { try FileManager.default.copyItem(at: speech, to: folder.appending(path: "mic.wav")) } catch { return ["\(error)"] }
        return []
    }
}

/// `AnyMeet --self-test <report.json> [--fake-capture]`: drives the real app through a
/// recording, the transcriber, the menu-bar icon and the Settings window, checks each
/// step, writes a JSON report and exits 0 (all passed) or 1. Uses the clipboard and
/// restores it afterwards. Removes the meeting folder it creates.
@MainActor
enum SelfTest {
    nonisolated static let enabled = CommandLine.arguments.contains("--self-test")
    nonisolated static let fakeCapture = CommandLine.arguments.contains("--fake-capture")

    static func run(_ app: AppDelegate, report: URL) async {
        var checks: [[String: Any]] = []
        func check(_ name: String, _ ok: Bool, _ detail: String = "") {
            checks.append(["name": name, "ok": ok, "detail": detail])
            print("\(ok ? "PASS" : "FAIL")  \(name)\(detail.isEmpty ? "" : "  [\(detail)]")")
        }
        func skip(_ name: String, _ reason: String) {
            checks.append(["name": name, "ok": true, "skipped": true, "detail": reason])
            print("SKIP  \(name)  [\(reason)]")
        }
        func wait(_ seconds: Double, until done: () -> Bool) async -> Bool {
            let deadline = Date().addingTimeInterval(seconds)
            while !done() && Date() < deadline { try? await Task.sleep(for: .milliseconds(200)) }
            return done()
        }

        let savedMode = UserDefaults.standard.string(forKey: "transcribeMode")  // nil = never set; restore exactly
        Preferences.transcribeMode = "after"  // steps 2-4 cover transcribing after the meeting

        // 1. Menu bar while idle.
        var look = app.menuBarLook()
        check("idle: bubble icon, no tint, no timer", look.icon == "bubble" && !look.red && look.title.isEmpty, "\(look)")

        // 2. Start from the menu-bar icon (same action a click sends).
        app.simulateStatusItemClick()
        let started = await wait(10) { app.isRecording }
        look = app.menuBarLook()
        check("recording started from the menu-bar icon", started, app.recording["message"] as? String ?? "")
        check("recording: red bubble with a timer", look.icon == "bubble" && look.red && look.title.contains(":"), "\(look)")
        try? await Task.sleep(for: .seconds(2))
        let folder = app.currentFolder

        // 3. Stop from the menu-bar icon; the icon must leave the recording look.
        app.simulateStatusItemClick()
        let stopped = await wait(30) { app.recording["state"] as? String == "idle" }
        look = app.menuBarLook()
        check("stopped from the menu-bar icon", stopped, app.recording["message"] as? String ?? "")
        check("after stop: not red, no timer", !look.red && look.title.isEmpty, "\(look)")

        // 4. The transcriber starts (uv and ffmpeg found) and the icon returns to idle when it ends.
        let message = app.recording["message"] as? String ?? ""
        check("transcriber started", message.hasPrefix("Transcribing"), message)
        let finished = await wait(180) { TranscriptionRunner.shared.running.isEmpty }
        look = app.menuBarLook()
        check("transcription finished within 3 minutes", finished)
        check("after transcription: idle bubble, no tint, no timer", look.icon == "bubble" && !look.red && look.title.isEmpty, "\(look)")
        if let folder {
            let log = (try? String(contentsOf: folder.appending(path: "transcribe.log"), encoding: .utf8)) ?? ""
            check("transcriber ran cleanly (no 'not found', no Python error)",
                  !log.isEmpty && !log.contains("not found") && !log.contains("Traceback"),
                  String(log.split(separator: "\n").last ?? ""))
            check("transcript.json written", FileManager.default.fileExists(atPath: folder.appending(path: "transcript.json").path))
            try? FileManager.default.removeItem(at: folder)
        }

        // 5. macOS stops the capture (the system indicator's Stop): AnyMeet must stop fully too.
        //    Transcription is off here: step 4 already covered it, and it saves a Gemini call.
        Preferences.transcribeMode = "off"
        app.simulateStatusItemClick()
        if await wait(10, until: { app.isRecording }) {
            try? await Task.sleep(for: .seconds(2))
            let systemFolder = app.currentFolder
            await app.simulateSystemStop()
            let stoppedBySystem = await wait(30) { app.recording["state"] as? String == "idle" }
            look = app.menuBarLook()
            check("macOS stop: AnyMeet stops too", stoppedBySystem, app.recording["message"] as? String ?? "")
            check("macOS stop: no red icon, no timer", !look.red && look.title.isEmpty, "\(look)")
            if let systemFolder {
                let names = (try? FileManager.default.contentsOfDirectory(atPath: systemFolder.path)) ?? []
                check("macOS stop: the recording is saved", names.contains { $0.hasPrefix("mic.") }, names.joined(separator: ", "))
                try? FileManager.default.removeItem(at: systemFolder)
            }
        } else {
            check("second recording started", false)
        }

        // 6. Live transcription: text must appear while still recording, and the run
        //    must finish cleanly after Stop.
        Preferences.transcribeMode = "live"
        app.simulateStatusItemClick()
        if await wait(10, until: { app.isRecording }), let liveFolder = app.currentFolder {
            let transcript = liveFolder.appending(path: "transcript.json")
            check("live: transcriber starts with the recording", !TranscriptionRunner.shared.running.isEmpty,
                  app.recording["message"] as? String ?? "")
            let early = await wait(45) { FileManager.default.fileExists(atPath: transcript.path) }
            check("live: transcript appears while still recording", early && app.isRecording,
                  early ? "recording: \(app.isRecording)" : "no transcript.json after 45 s")
            app.simulateStatusItemClick()
            _ = await wait(30) { app.recording["state"] as? String == "idle" }
            let finished = await wait(180) { TranscriptionRunner.shared.running.isEmpty }
            look = app.menuBarLook()
            let log = (try? String(contentsOf: liveFolder.appending(path: "transcribe.log"), encoding: .utf8)) ?? ""
            check("live: finishes after Stop", finished && !log.contains("Traceback"), String(log.split(separator: "\n").last ?? ""))
            check("live: live/ folder cleaned up", !FileManager.default.fileExists(atPath: liveFolder.appending(path: "live").path))
            check("live: icon back to idle", look.icon == "bubble" && !look.red && look.title.isEmpty, "\(look)")
            try? FileManager.default.removeItem(at: liveFolder)
        } else {
            check("live: recording started", false)
        }
        if let savedMode { Preferences.transcribeMode = savedMode } else { UserDefaults.standard.removeObject(forKey: "transcribeMode") }
        try? FileManager.default.removeItem(at: FileManager.default.temporaryDirectory.appending(path: "anymeet-selftest-speech.wav"))

        // 7. Paste into the API key field with Cmd+V, and copy from the page.
        let pasteboard = NSPasteboard.general
        let saved = pasteboard.string(forType: .string)
        WebWindows.shared.showSettings(tab: "transcription")
        try? await Task.sleep(for: .seconds(2))
        if let (window, web) = WebWindows.shared.window("settings") {
            window.makeKeyAndOrderFront(nil)
            NSApp.activate()
            window.makeFirstResponder(web)
            _ = try? await web.evaluateJavaScript("document.getElementById('key').focus(); document.getElementById('key').value = ''; 1")
            pasteboard.clearContents()
            pasteboard.setString("anymeet-selftest-key", forType: .string)
            // (a) Cmd+V must map to Paste in the Edit menu: without it macOS never delivers
            //     paste to the page. (b) The field must accept that Paste. Sending the menu's
            //     action directly keeps this independent of which app is in front.
            let pasteItem = NSApp.mainMenu?.items.compactMap(\.submenu).flatMap(\.items)
                .first { $0.keyEquivalent == "v" && $0.keyEquivalentModifierMask == .command }
            check("Edit menu maps Cmd+V to Paste", pasteItem?.action == #selector(NSText.paste(_:)),
                  pasteItem.map { "\($0.title) → \($0.action.map(NSStringFromSelector) ?? "nil")" } ?? "no Cmd+V item")
            if NSApp.isActive {  // WebKit only pastes into the focused field of the active window
                if let action = pasteItem?.action { NSApp.sendAction(action, to: web, from: pasteItem) }
                try? await Task.sleep(for: .milliseconds(500))
                let value = (try? await web.evaluateJavaScript("document.getElementById('key').value")) as? String ?? ""
                check("Paste puts the clipboard into the API key field", value == "anymeet-selftest-key", "field: '\(value)'")
            } else {
                skip("Paste puts the clipboard into the API key field", "AnyMeet is not the frontmost app; macOS won't let it take focus")
            }

            pasteboard.clearContents()
            _ = try? await web.callAsyncJavaScript("await navigator.clipboard.writeText('anymeet-copy-test'); return 1",
                                                   arguments: [:], in: nil, contentWorld: .page)
            let copied = pasteboard.string(forType: .string) ?? ""
            check("Copy transcript can write to the clipboard", copied == "anymeet-copy-test", "clipboard: '\(copied)'")
            window.close()
        } else {
            check("Settings window opened", false)
        }
        pasteboard.clearContents()
        if let saved { pasteboard.setString(saved, forType: .string) }

        let failed = checks.filter { $0["ok"] as? Bool != true }.count
        let skipped = checks.filter { $0["skipped"] as? Bool == true }.count
        let summary: [String: Any] = ["passed": checks.count - failed - skipped, "failed": failed, "skipped": skipped,
                                      "fakeCapture": fakeCapture, "checks": checks]
        if let data = try? JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted]) { try? data.write(to: report) }
        print("\(checks.count - failed - skipped) passed, \(failed) failed, \(skipped) skipped")
        exit(failed == 0 ? 0 : 1)
    }
}
