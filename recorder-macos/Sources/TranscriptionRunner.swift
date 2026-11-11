import Foundation

/// Runs `uv run --project <transcriber> transcribe <folder>`, one run per folder at a
/// time, with output in transcribe.log in the meeting folder. Main thread only.
///
/// Apps opened from Finder don't get the shell's PATH, so uv and ffmpeg (which the
/// transcriber calls) are looked up in the usual install folders and added to PATH.
final class TranscriptionRunner {
    static let shared = TranscriptionRunner()
    static let meetingsChanged = Notification.Name("MeetingsChanged")
    static let toolFolders = ["/opt/homebrew/bin", "/usr/local/bin", "\(NSHomeDirectory())/.local/bin", "\(NSHomeDirectory())/.cargo/bin"]

    private(set) var running = Set<String>()
    /// Why the last start() returned false, for the menu and the UI.
    private(set) var lastError = ""

    static func find(_ tool: String) -> String? {
        let path = ProcessInfo.processInfo.environment["PATH"]?.split(separator: ":").map(String.init) ?? []
        return (toolFolders + path).map { "\($0)/\(tool)" }.first { FileManager.default.isExecutableFile(atPath: $0) }
    }

    /// A `uv run --project <transcriber> <args>` process with the PATH the transcriber needs.
    static func uvProcess(_ args: [String]) throws -> Process {
        guard let project = Preferences.transcriberProject else {
            throw BridgeError("transcriber not found: set it with `defaults write com.mirsabbir.anymeet transcriberProject <repo>/transcriber`")
        }
        guard let uv = find("uv") else { throw BridgeError("uv not found: install it with `brew install uv`") }
        let process = Process()
        process.executableURL = URL(filePath: uv)
        process.arguments = ["run", "--project", project] + args
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = (toolFolders + [env["PATH"] ?? "/usr/bin:/bin"]).joined(separator: ":")
        env["PYTHONUNBUFFERED"] = "1"  // output as it happens, not only at the end
        env["ANYMEET_DEVICE"] = Preferences.processor  // where local models run (device.py)
        process.environment = env
        return process
    }

    /// The optional installs a run needs: `local` for a local Whisper model, `speakers`
    /// for telling speakers apart. uv adds them on first use.
    static func extras(local: Bool, speakers: Bool) -> [String] {
        (local ? ["--extra", "local"] : []) + (speakers ? ["--extra", "speakers"] : [])
    }

    /// Runs `uv run … <args>` to the end and returns its standard output; throws with the
    /// transcriber's last line of errors when it fails.
    static func output(_ args: [String]) async throws -> Data {
        let process = try uvProcess(args)
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        return try await withCheckedThrowingContinuation { done in
            process.terminationHandler = { p in
                let data = out.fileHandleForReading.readDataToEndOfFile()
                if p.terminationStatus == 0 { return done.resume(returning: data) }
                let reason = String(decoding: err.fileHandleForReading.readDataToEndOfFile(), as: UTF8.self)
                done.resume(throwing: BridgeError(reason.split(separator: "\n").last.map(String.init) ?? "transcriber failed"))
            }
            do { try process.run() } catch { done.resume(throwing: error) }
        }
    }

    /// Returns false when the transcriber can't run or this folder is already running.
    /// `live`: start while recording; the transcriber follows live/*.wav until live/done appears.
    /// `onlyFailed`: send again only the parts the transcript marks as not transcribed
    /// (Try again); speakers keep the names they have, so nothing is diarized.
    @discardableResult
    func start(_ folder: URL, live: Bool = false, onlyFailed: Bool = false, completion: ((Bool) -> Void)? = nil) -> Bool {
        let log = folder.appending(path: "transcribe.log")
        func fail(_ reason: String) -> Bool {
            lastError = reason
            try? (reason + "\n").write(to: log, atomically: true, encoding: .utf8)
            NotificationCenter.default.post(name: Self.meetingsChanged, object: nil)
            return false
        }
        guard !running.contains(folder.path) else { lastError = "already transcribing"; return false }
        let speakers = Preferences.speakers && !onlyFailed
        let args = Self.extras(local: Preferences.transcriber.hasPrefix("local-"), speakers: speakers)
            + ["transcribe", folder.path] + (live ? ["--live"] : []) + (speakers ? ["--speakers"] : [])
            + (onlyFailed ? ["--only-failed"] : [])
        let process: Process
        do { process = try Self.uvProcess(args) } catch { return fail(error.localizedDescription) }
        guard Self.find("ffmpeg") != nil else { return fail("ffmpeg not found: install it with `brew install ffmpeg`") }
        process.environment?["TRANSCRIBER"] = Preferences.transcriber
        FileManager.default.createFile(atPath: log.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: log) {
            process.standardOutput = handle
            process.standardError = handle
        }
        process.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                self?.running.remove(folder.path)
                self?.processes[folder.path] = nil
                NotificationCenter.default.post(name: Self.meetingsChanged, object: nil)
                completion?(p.terminationStatus == 0)
                CloudJobs.afterTranscript(folder)  // summary and backup, if they are on
            }
        }
        do { try process.run() } catch { return fail("couldn't start uv: \(error.localizedDescription)") }
        lastError = ""
        running.insert(folder.path)
        processes[folder.path] = process
        NotificationCenter.default.post(name: Self.meetingsChanged, object: nil)
        return true
    }

    private var processes: [String: Process] = [:]

    /// Stop button: asks the run to stop (it sends nothing more and finishes the parts already
    /// sent, see the transcriber's cli.watch_stop), and ends it outright if it hasn't stopped
    /// after `grace` seconds, e.g. while it is still telling speakers apart.
    func stop(_ folder: URL, grace: TimeInterval = 30) {
        // This run's own process, not whatever runs for the folder later: Try again pressed
        // within the grace period starts a new run that must not be ended by this timer.
        guard let process = processes[folder.path] else { return }
        FileManager.default.createFile(atPath: folder.appending(path: "transcribe.stop").path, contents: nil)
        DispatchQueue.main.asyncAfter(deadline: .now() + grace) {
            if process.isRunning { process.terminate() }
        }
    }
}
