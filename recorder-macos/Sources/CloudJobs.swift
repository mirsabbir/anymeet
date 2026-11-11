import Foundation

/// Starts `anymeet <command>` (transcriber/commands.py): cloud backups, downloads, freeing space
/// and AI summaries. A job reports in <meeting>/job.json, which MeetingStore passes to the UI, so
/// this only starts it and tells the windows when it ends. Output goes to anymeet.log in the
/// settings folder. Main thread only.
enum CloudJobs {
    static func run(_ args: [String]) throws {
        let process = try TranscriptionRunner.uvProcess(["anymeet"] + args)
        let log = Preferences.configFolder.appending(path: "anymeet.log")
        if ((try? FileManager.default.attributesOfItem(atPath: log.path)[.size] as? Int) ?? 0) > 1_000_000 {
            try? FileManager.default.removeItem(at: log)  // keep it small: it's only for looking into a problem
        }
        if !FileManager.default.fileExists(atPath: log.path) { FileManager.default.createFile(atPath: log.path, contents: nil) }
        if let handle = try? FileHandle(forWritingTo: log) {
            handle.seekToEndOfFile()
            process.standardOutput = handle
            process.standardError = handle
        }
        process.terminationHandler = { _ in
            DispatchQueue.main.async { NotificationCenter.default.post(name: TranscriptionRunner.meetingsChanged, object: nil) }
        }
        try process.run()
        // The job file shows up once uv has started Python.
        DispatchQueue.main.asyncAfter(deadline: .now() + 2) {
            NotificationCenter.default.post(name: TranscriptionRunner.meetingsChanged, object: nil)
        }
    }

    /// Once a meeting's transcript is ready (or recording stopped with transcription off):
    /// a summary if it has none and that is on, then a backup if that is on.
    static func afterTranscript(_ folder: URL) {
        let backup = Preferences.autoBackup && Preferences.cloudReady
        let summarize = Preferences.autoSummary && Preferences.anthropicKeySet
            && !FileManager.default.fileExists(atPath: folder.appending(path: "summary.json").path)
        if summarize {
            try? run(["summarize", folder.path, "--language", Preferences.summaryLanguage] + (backup ? ["--backup"] : []))
        } else if backup {
            try? run(["backup", folder.path])
        }
    }

    /// At launch: list the meetings that are only in the cloud, and finish backups that were cut off.
    static func atLaunch() {
        if Preferences.cloudReady { try? run(["sync", Preferences.meetingsRoot.path, "--resume"]) }
    }

    /// The meeting folders for these ids that no recording or transcription is using now.
    static func idle(_ ids: [String]) throws -> [URL] {
        let recording = AppDelegate.shared?.isRecording == true ? AppDelegate.shared?.currentFolder?.path : nil
        let busy = TranscriptionRunner.shared.running.union([recording].compactMap { $0 })
        let folders = ids.compactMap(MeetingStore.folder).filter { !busy.contains($0.path) }
        if folders.isEmpty { throw BridgeError("wait until the recording and the transcript are finished") }
        return folders
    }
}
