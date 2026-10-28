import Foundation
import Metal

/// App settings. Plain preferences live in UserDefaults; the glossary and the API
/// key live in ~/Library/Application Support/AnyMeet (glossary.txt, .env),
/// the folder the `transcribe` command reads, so the UI and the CLI never disagree.
enum Preferences {
    static let meetingsRoot = FileManager.default.homeDirectoryForCurrentUser.appending(path: "Meetings")
    /// The transcriber project: the `transcriberProject` default if set
    /// (`defaults write com.mirsabbir.anymeet transcriberProject <path>`), else
    /// the path baked in by build.sh. A CI build's baked path doesn't exist here.
    static let transcriberProject: String? = {
        let candidates = [UserDefaults.standard.string(forKey: "transcriberProject"),
                          Bundle.main.object(forInfoDictionaryKey: "TranscriberProject") as? String]
        return candidates.compactMap { $0.map { ($0 as NSString).expandingTildeInPath } }
            .first { FileManager.default.fileExists(atPath: $0 + "/pyproject.toml") }
    }()

    static let transcribers: [[String: Any]] = [
        ["id": "gemini-3.5-transcribe", "name": "Gemini 3.5 Transcribe", "note": "Tested on our Bangla audio · ~$0.35/hour", "available": true, "needsKey": true],
        ["id": "local-whisper-bangla", "name": "Local Whisper (Bangla)", "note": "On this Mac, free, offline · Bangla only: English words come out in Bangla letters · 1.5 GB", "available": true, "needsKey": false],
        ["id": "local-whisper-english", "name": "Local Whisper (English)", "note": "On this Mac, free, offline · English-only meetings · 1.6 GB", "available": true, "needsKey": false],
        ["id": "soniox", "name": "Soniox", "note": "Not added yet", "available": false, "needsKey": true],
    ]
    static let keyName = "GEMINI_API_KEY"  // the only available transcriber's key
    static let hfTokenName = "HF_TOKEN"  // Hugging Face, read by the transcriber to download the speaker model

    /// When to transcribe: "live" (while recording), "after" (when recording stops) or "off".
    static var transcribeMode: String {
        get {
            if let mode = UserDefaults.standard.string(forKey: "transcribeMode") { return mode }
            return UserDefaults.standard.object(forKey: "autoTranscribe") as? Bool == false ? "off" : "live"
        }
        set { UserDefaults.standard.set(newValue, forKey: "transcribeMode") }
    }

    static var transcriber: String {
        get { UserDefaults.standard.string(forKey: "transcriber") ?? "gemini-3.5-transcribe" }
        set { UserDefaults.standard.set(newValue, forKey: "transcriber") }
    }

    /// Split "Others" into Speaker 1, Speaker 2… (`transcribe --speakers`).
    static var speakers: Bool {
        get { UserDefaults.standard.bool(forKey: "identifySpeakers") }
        set { UserDefaults.standard.set(newValue, forKey: "identifySpeakers") }
    }

    /// Where local models run, "gpu" or "cpu"; the transcriber reads it as ANYMEET_DEVICE.
    static var processor: String {
        get { UserDefaults.standard.string(forKey: "processor") ?? (gpuName == nil ? "cpu" : "gpu") }
        set { UserDefaults.standard.set(newValue, forKey: "processor") }
    }

    static let gpuName: String? = MTLCreateSystemDefaultDevice()?.name  // e.g. "Apple M3"

    /// The Processor list in ui/src/types.ts.
    static var processors: [[String: Any]] {
        var size = 0
        sysctlbyname("machdep.cpu.brand_string", nil, &size, nil, 0)
        var brand = [CChar](repeating: 0, count: max(size, 1))
        sysctlbyname("machdep.cpu.brand_string", &brand, &size, nil, 0)
        let cpu = String(cString: brand).trimmingCharacters(in: .whitespaces)
        return [
            ["id": "gpu", "name": gpuName ?? "None found", "available": gpuName != nil,
             "note": gpuName == nil ? "No graphics card the models can use" : "Faster, and leaves the processor free for your other work"],
            ["id": "cpu", "name": "\(cpu.isEmpty ? "This Mac" : cpu) · \(ProcessInfo.processInfo.activeProcessorCount) cores", "available": true,
             "note": "Slower, and keeps several processor cores busy"],
        ]
    }

    static let configFolder = FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
        .appending(path: "AnyMeet")

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

    static var apiKeySet: Bool { isSet(keyName) }
    static var hfTokenSet: Bool { isSet(hfTokenName) }

    /// True when .env has a non-empty value for the name. The value itself is never returned to the UI.
    private static func isSet(_ name: String) -> Bool {
        envLines().contains { $0.hasPrefix("\(name)=") && $0.count > name.count + 1 }
    }

    static func setApiKey(_ key: String) throws { try setSecret(key, as: keyName) }
    static func setHfToken(_ token: String) throws { try setSecret(token, as: hfTokenName) }

    private static func setSecret(_ value: String, as name: String) throws {
        guard let url = configFile(".env") else { return }
        let clean = value.trimmingCharacters(in: .whitespacesAndNewlines)
        var lines = envLines().filter { !$0.hasPrefix("\(name)=") }
        lines.append("\(name)=\(clean)")
        try (lines.joined(separator: "\n") + "\n").write(to: url, atomically: true, encoding: .utf8)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }

    private static func envLines() -> [String] {
        guard let url = configFile(".env"), let text = try? String(contentsOf: url, encoding: .utf8) else { return [] }
        return text.split(separator: "\n", omittingEmptySubsequences: true).map(String.init)
    }
}
