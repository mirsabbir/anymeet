import AppKit
import ScreenCaptureKit
import WebKit

/// What the app records with: ScreenCaptureKit normally, a fake for the self-test.
protocol Capture: AnyObject {
    var onStreamError: ((Error) -> Void)? { get set }
    func start(in folder: URL) async throws
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

    func start(in folder: URL) async throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        self.folder = folder
    }

    func simulateSystemStop() async {
        onStreamError?(NSError(domain: SCStreamErrorDomain, code: SCStreamError.Code.userStopped.rawValue))
    }

    func stop() async -> [String] {
        guard let folder else { return ["no folder"] }
        let say = Process()
        say.executableURL = URL(filePath: "/usr/bin/say")
        say.arguments = ["--file-format=WAVE", "--data-format=LEI16@16000", "-o", folder.appending(path: "mic.wav").path,
                         "Hello, this is an AnyMeet self test. We deploy the API next quarter."]
        try? say.run()
        say.waitUntilExit()
        return say.terminationStatus == 0 ? [] : ["say failed"]
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
        let autoTranscribe = Preferences.autoTranscribe
        Preferences.autoTranscribe = false
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
        Preferences.autoTranscribe = autoTranscribe

        // 6. Paste into the API key field with Cmd+V, and copy from the page.
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
