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

    /// Returns false when the transcriber can't run or this folder is already running.
    @discardableResult
    /// `live`: start while recording; the transcriber follows live/*.wav until live/done appears.
    func start(_ folder: URL, live: Bool = false, completion: ((Bool) -> Void)? = nil) -> Bool {
        let log = folder.appending(path: "transcribe.log")
        func fail(_ reason: String) -> Bool {
            lastError = reason
            try? (reason + "\n").write(to: log, atomically: true, encoding: .utf8)
            NotificationCenter.default.post(name: Self.meetingsChanged, object: nil)
            return false
        }
        guard !running.contains(folder.path) else { lastError = "already transcribing"; return false }
        guard let project = Preferences.transcriberProject else {
            return fail("transcriber not found: set it with `defaults write com.mirsabbir.anymeet transcriberProject <repo>/transcriber`")
        }
        guard let uv = Self.find("uv") else { return fail("uv not found: install it with `brew install uv`") }
        guard Self.find("ffmpeg") != nil else { return fail("ffmpeg not found: install it with `brew install ffmpeg`") }

        let process = Process()
        process.executableURL = URL(filePath: uv)
        // Local models need the optional torch/transformers install; uv adds it on first use.
        let extra = Preferences.transcriber.hasPrefix("local-") ? ["--extra", "local"] : []
        process.arguments = ["run", "--project", project] + extra + ["transcribe", folder.path] + (live ? ["--live"] : [])
        var env = ProcessInfo.processInfo.environment
        env["PATH"] = (Self.toolFolders + [env["PATH"] ?? "/usr/bin:/bin"]).joined(separator: ":")
        env["TRANSCRIBER"] = Preferences.transcriber
        env["PYTHONUNBUFFERED"] = "1"  // write transcribe.log as it goes, not only at the end
        process.environment = env
        FileManager.default.createFile(atPath: log.path, contents: nil)
        if let handle = try? FileHandle(forWritingTo: log) {
            process.standardOutput = handle
            process.standardError = handle
        }
        process.terminationHandler = { [weak self] p in
            DispatchQueue.main.async {
                self?.running.remove(folder.path)
                NotificationCenter.default.post(name: Self.meetingsChanged, object: nil)
                completion?(p.terminationStatus == 0)
            }
        }
        do { try process.run() } catch { return fail("couldn't start uv: \(error.localizedDescription)") }
        lastError = ""
        running.insert(folder.path)
        NotificationCenter.default.post(name: Self.meetingsChanged, object: nil)
        return true
    }
}
