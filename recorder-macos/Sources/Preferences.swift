import Foundation

/// App settings. Plain preferences live in UserDefaults; the glossary and the API
/// key live in ~/Library/Application Support/MeetingRecorder (glossary.txt, .env),
/// the folder the `transcribe` command reads, so the UI and the CLI never disagree.
enum Preferences {
    static let meetingsRoot = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Meetings")
    /// The transcriber project: the `transcriberProject` default if set
    /// (`defaults write com.mirsabbir.meetingrecorder transcriberProject <path>`), else
    /// the path baked in by build.sh. A CI build's baked path doesn't exist here.
    static let transcriberProject: String? = {
        let candidates = [UserDefaults.standard.string(forKey: "transcriberProject"),
                          Bundle.main.object(forInfoDictionaryKey: "TranscriberProject") as? String]
        return candidates.compactMap { $0.map { ($0 as NSString).expandingTildeInPath } }
            .first { FileManager.default.fileExists(atPath: $0 + "/pyproject.toml") }
    }()

    static let transcribers: [[String: Any]] = [
        ["id": "gemini-3.5-transcribe", "name": "Gemini 3.5 Transcribe", "note": "Tested on our Bangla audio · ~$0.35/hour", "available": true],
        ["id": "soniox", "name": "Soniox", "note": "Not added yet", "available": false],
        ["id": "sarvam", "name": "Sarvam", "note": "Not added yet", "available": false],
        ["id": "local-whisper", "name": "Local Whisper", "note": "Not added yet", "available": false],
    ]
    static let keyName = "GEMINI_API_KEY"  // the only available transcriber's key

    static var autoTranscribe: Bool {
        get { UserDefaults.standard.object(forKey: "autoTranscribe") as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: "autoTranscribe") }
    }

    static var transcriber: String {
        get { UserDefaults.standard.string(forKey: "transcriber") ?? "gemini-3.5-transcribe" }
        set { UserDefaults.standard.set(newValue, forKey: "transcriber") }
    }

    static let configFolder = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appending(path: "MeetingRecorder")

    private static func configFile(_ name: String) -> URL? {
        try? FileManager.default.createDirectory(at: configFolder, withIntermediateDirectories: true)
        return configFolder.appending(path: name)
    }

    static var glossary: [String] {
        get {
            guard let url = configFile("glossary.txt"), let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
            return text.split(separator: "\n").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty && !$0.hasPrefix("#") }
        }
        set {
            guard let url = configFile("glossary.txt") else { return }
            let header = "# Terms the transcriber should recognize and write exactly like this.\n# One per line. Edited from the app's Settings → Glossary.\n"
            try? (header + newValue.joined(separator: "\n") + "\n").write(to: url, atomically: true, encoding: .utf8)
        }
    }

    /// True when .env has a non-empty value for the key. The value itself is never returned to the UI.
    static var apiKeySet: Bool {
        envLines().contains { $0.hasPrefix("\(keyName)=") && $0.count > keyName.count + 1 }
    }

    static func setApiKey(_ key: String) throws {
        guard let url = configFile(".env") else { return }
        let clean = key.trimmingCharacters(in: .whitespacesAndNewlines)
        var lines = envLines().filter { !$0.hasPrefix("\(keyName)=") }
        lines.append("\(keyName)=\(clean)")
        try (lines.joined(separator: "\n") + "\n").write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    private static func envLines() -> [String] {
        guard let url = configFile(".env"), let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return text.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
    }
}
