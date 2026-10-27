import Foundation

/// Runs `uv run --project <transcriber> transcribe <folder>` through a login shell
/// (so uv is on PATH), one run per folder at a time. Output goes to transcribe.log
/// in the meeting folder. Main thread only.
final class TranscriptionRunner {
    static let shared = TranscriptionRunner()
    static let meetingsChanged = Notification.Name("MeetingsChanged")

    private(set) var running = Set<String>()

    /// Returns false when the transcriber is not configured or this folder is already running.
    @discardableResult
    func start(_ folder: URL, completion: ((Bool) -> Void)? = nil) -> Bool {
        guard let project = Preferences.transcriberProject, !running.contains(folder.path) else { return false }
        let process = Process()
        process.executableURL = URL(filePath: "/bin/zsh")
        process.arguments = ["-lc", "uv run --project \"$1\" transcribe \"$2\"", "zsh", project, folder.path]
        process.environment = ProcessInfo.processInfo.environment.merging(["TRANSCRIBER": Preferences.transcriber]) { $1 }
        let log = folder.appending(path: "transcribe.log")
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
        do { try process.run() } catch { return false }
        running.insert(folder.path)
        NotificationCenter.default.post(name: Self.meetingsChanged, object: nil)
        return true
    }
}
