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
    /// Stop the way the system's ⏹ in the menu bar does: no stream-delegate call (self-test only).
    func simulateMenuBarStop() async
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

    /// No recording output here; report what Recorder reports when its output finishes unasked.
    func simulateMenuBarStop() async {
        onStreamError?(RecorderError.stoppedFromMenuBar)
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
///
/// `--download-test <model id>` runs only the model download instead: it picks the model in
/// Settings the way a user does and follows the progress to the end. Start it with HF_HOME
/// set to an empty folder, so it really downloads and leaves the user's model cache alone.
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

        if let i = CommandLine.arguments.firstIndex(of: "--download-test"), i + 1 < CommandLine.arguments.count {
            await downloadTest(CommandLine.arguments[i + 1], check)
            finish(checks, report)
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

            // 4b. The in-app player, the way a user reaches it: click a segment's time in the
            //     transcript; the speaker's track must load and seek inside the window (muted here).
            WebWindows.shared.showMeetings(id: folder.lastPathComponent)
            try? await Task.sleep(for: .seconds(3))
            if let (window, web) = WebWindows.shared.window("meetings") {
                let result = (try? await web.callAsyncJavaScript("""
                    const play = HTMLMediaElement.prototype.play
                    HTMLMediaElement.prototype.play = function () { this.muted = true; return play.call(this) }
                    const at = document.querySelector('.segment .at')
                    if (!at) return 'no segment to click'
                    at.click()
                    for (let i = 0; i < 50; i++) {
                      const a = document.querySelector('.player audio')
                      if (a && a.readyState >= 1 && a.duration > 0) {
                        a.currentTime = Math.min(1, a.duration / 2)
                        await new Promise((ok) => { a.onseeked = ok; setTimeout(ok, 3000) })
                        return `duration ${a.duration.toFixed(1)} s, seeked to ${a.currentTime.toFixed(1)} s, from ${a.currentSrc.split(':')[0]}`
                      }
                      await new Promise((ok) => setTimeout(ok, 200))
                    }
                    const a = document.querySelector('.player audio')
                    return a ? `audio not playable (error ${a.error && a.error.code})` : 'no player opened'
                    """, arguments: [:], in: nil, contentWorld: .page)) as? String ?? "script failed"
                check("player: clicking a segment's time plays its track in the app", result.hasPrefix("duration") && !result.contains("seeked to 0.0"), result)
                window.close()
            } else {
                check("player: Meetings window opened", false)
            }
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

        // 5b. The system's ⏹ in the menu bar stops a display without telling the stream
        //     delegate: AnyMeet must notice, stop fully, and leave no menu-bar item behind.
        let itemsIdle = app.menuBarItemCount
        app.simulateStatusItemClick()
        if await wait(10, until: { app.isRecording }) {
            try? await Task.sleep(for: .seconds(2))
            let menuFolder = app.currentFolder
            let itemsRecording = app.menuBarItemCount
            await app.simulateMenuBarStop()
            let stopped = await wait(30) { app.recording["state"] as? String == "idle" }
            check("menu-bar ⏹: AnyMeet stops too", stopped, app.recording["message"] as? String ?? "")
            try? await Task.sleep(for: .seconds(1))
            check("menu-bar ⏹: no menu-bar item left behind", app.menuBarItemCount == itemsIdle,
                  "idle \(itemsIdle), recording \(itemsRecording), after \(app.menuBarItemCount)")
            if let menuFolder { try? FileManager.default.removeItem(at: menuFolder) }
        } else {
            check("third recording started", false)
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

        finish(checks, report)
    }

    private static func finish(_ checks: [[String: Any]], _ report: URL) -> Never {
        let failed = checks.filter { $0["ok"] as? Bool != true }.count
        let skipped = checks.filter { $0["skipped"] as? Bool == true }.count
        let summary: [String: Any] = ["passed": checks.count - failed - skipped, "failed": failed, "skipped": skipped,
                                      "fakeCapture": fakeCapture, "checks": checks]
        if let data = try? JSONSerialization.data(withJSONObject: summary, options: [.prettyPrinted]) { try? data.write(to: report) }
        print("\(checks.count - failed - skipped) passed, \(failed) failed, \(skipped) skipped")
        exit(failed == 0 ? 0 : 1)
    }

    /// Settings → Transcription: the card of a model that isn't downloaded says so; clicking it
    /// shows progress that goes up to the end, and the transcriber then finds every file.
    private static func downloadTest(_ id: String, _ check: (String, Bool, String) -> Void) async {
        let savedTranscriber = UserDefaults.standard.string(forKey: "transcriber")
        defer {
            if let savedTranscriber { UserDefaults.standard.set(savedTranscriber, forKey: "transcriber") }
            else { UserDefaults.standard.removeObject(forKey: "transcriber") }
        }
        let name = Preferences.transcribers.first { $0["id"] as? String == id }?["name"] as? String ?? id
        WebWindows.shared.showSettings(tab: "transcription")
        try? await Task.sleep(for: .seconds(4))  // the page asks the transcriber which models are downloaded
        guard let (_, web) = WebWindows.shared.window("settings") else { return check("Settings window opened", false, "") }
        let card = "[...document.querySelectorAll('.card')].find((c) => c.innerText.includes(\(String(reflecting: name))))"
        func status() async -> String {
            (try? await web.evaluateJavaScript("(\(card)?.querySelector('.download')?.innerText ?? '').trim()")) as? String ?? ""
        }
        let before = await status()
        check("download: card says the model isn't downloaded", before.contains("Not downloaded"), before)
        _ = try? await web.evaluateJavaScript("\(card).click()")

        var seen: [String] = []
        var percents: [Int] = []
        let started = Date()
        while Date().timeIntervalSince(started) < 30 * 60 {
            let text = await status()
            if text != seen.last { seen.append(text) }
            if let match = text.firstMatch(of: #/Downloading (\d+)%/#), let pct = Int(match.1) { percents.append(pct) }
            if text.contains("Downloaded") || text.contains("failed") { break }
            try? await Task.sleep(for: .milliseconds(500))
        }
        let steps = Set(percents).count
        check("download: progress shown and only goes up", steps >= 5 && percents == percents.sorted() && (percents.last ?? 0) >= 90,
              "\(steps) different percentages, last \(percents.last ?? -1)%, \(Int(Date().timeIntervalSince(started))) s")
        check("download: card ends with 'Downloaded'", seen.last?.contains("Downloaded") == true, seen.suffix(3).joined(separator: " → "))
        let models = (try? await ModelDownloads.shared.list(refresh: true)) ?? []
        let found = models.first { $0["id"] as? String == id }?["downloaded"] as? Bool == true
        check("download: the transcriber finds every file", found, "\(models.map { "\($0["id"] ?? ""): \($0["downloaded"] ?? "")" })")
    }
}
