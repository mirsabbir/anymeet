import AVFoundation
import AppKit

/// Reads meeting folders under ~/Meetings into the shapes the UI expects
/// (ui/src/types.ts: MeetingSummary, Meeting, MeetingFile, Segment).
enum MeetingStore {
    private static let audioExtensions: Set<String> = ["m4a", "mp3", "wav", "flac", "aac"]

    static func folder(_ id: String) -> URL? {
        let url = Preferences.meetingsRoot.appending(path: id).standardizedFileURL
        // Ids come from the UI: only direct children of the meetings folder are valid.
        guard url.deletingLastPathComponent().path == Preferences.meetingsRoot.standardizedFileURL.path,
              (try? url.resourceValues(forKeys: [.isDirectoryKey]).isDirectory) == true else { return nil }
        return url
    }

    static func list() async -> [[String: Any]] {
        let ids = (try? FileManager.default.contentsOfDirectory(atPath: Preferences.meetingsRoot.path)) ?? []
        var items: [[String: Any]] = []
        for id in ids where !id.hasPrefix(".") {
            if let folder = folder(id) { items.append(await summary(id: id, folder: folder)) }
        }
        return items.sorted { ($0["startedAt"] as? String ?? "") > ($1["startedAt"] as? String ?? "") }
    }

    static func get(_ id: String) async -> [String: Any]? {
        guard let folder = folder(id) else { return nil }
        var meeting = await summary(id: id, folder: folder)
        let transcript = readTranscript(folder)
        let segments = transcript?["segments"] as? [[String: Any]] ?? []
        meeting["folder"] = folder.path
        meeting["model"] = transcript?["model"]
        meeting["segments"] = segments
        meeting["transcriptText"] = segments.compactMap { s -> String? in
            guard let text = s["text"] as? String, !text.isEmpty else { return nil }
            return "[\(clock(s["start"] as? Double ?? 0))] \(s["speaker"] as? String ?? ""): \(text)"
        }.joined(separator: "\n\n")
        meeting["files"] = await files(folder)
        if meeting["status"] as? String == "failed", let log = try? String(contentsOf: folder.appending(path: "transcribe.log"), encoding: .utf8) {
            meeting["error"] = log.split(separator: "\n").last.map(String.init) ?? ""  // the transcriber's own last words
        }
        return meeting
    }

    // MARK: Pieces

    private static func summary(id: String, folder: URL) async -> [String: Any] {
        let segments = readTranscript(folder)?["segments"] as? [[String: Any]]
        let status: String
        if TranscriptionRunner.shared.running.contains(folder.path) {
            status = "transcribing"
        } else if let segments {
            status = segments.contains { !($0["failed"] as? String ?? "").isEmpty } ? "failed" : "done"
        } else {
            status = FileManager.default.fileExists(atPath: folder.appending(path: "transcribe.log").path) ? "failed" : "none"
        }
        let preview = segments?.lazy.compactMap { $0["text"] as? String }.first { !$0.isEmpty } ?? ""
        return [
            "id": id,
            "startedAt": startedAt(id: id, folder: folder),
            "durationSec": await duration(folder),
            "status": status,
            "preview": String(preview.prefix(140)),
        ]
    }

    private static func readTranscript(_ folder: URL) -> [String: Any]? {
        guard let data = try? Data(contentsOf: folder.appending(path: "transcript.json")) else { return nil }
        return try? JSONSerialization.jsonObject(with: data) as? [String: Any]
    }

    private static func startedAt(id: String, folder: URL) -> String {
        let parse = DateFormatter()
        parse.dateFormat = "yyyy-MM-dd_HHmm"
        let created = (try? folder.resourceValues(forKeys: [.creationDateKey]).creationDate) ?? Date()
        let date = parse.date(from: id) ?? created
        let iso = DateFormatter()
        iso.dateFormat = "yyyy-MM-dd'T'HH:mm:ss"  // local time, as the folder name is
        return iso.string(from: date)
    }

    private static func mediaFiles(_ folder: URL) -> [URL] {
        let names = (try? FileManager.default.contentsOfDirectory(atPath: folder.path)) ?? []
        return names.sorted().map { folder.appending(path: $0) }.filter {
            $0.lastPathComponent.hasPrefix("display-") || audioExtensions.contains($0.pathExtension.lowercased())
        }
    }

    private static func duration(_ folder: URL) async -> Double {
        var longest = 0.0
        for url in mediaFiles(folder) where audioExtensions.contains(url.pathExtension.lowercased()) {
            if let d = try? await AVURLAsset(url: url).load(.duration).seconds, d.isFinite { longest = max(longest, d) }
        }
        return longest
    }

    private static func files(_ folder: URL) async -> [[String: Any]] {
        var result: [[String: Any]] = []
        for url in mediaFiles(folder) {
            let asset = AVURLAsset(url: url)
            if url.lastPathComponent.hasPrefix("display-") {
                var file: [String: Any] = ["name": url.lastPathComponent, "path": url.path, "kind": "display", "info": "Video"]
                if let track = try? await asset.loadTracks(withMediaType: .video).first,
                   let size = try? await track.load(.naturalSize) {
                    file["info"] = "\(Int(size.width))×\(Int(size.height))"
                }
                file["thumbnail"] = await thumbnail(asset)
                result.append(file)
            } else {
                let who = url.deletingPathExtension().lastPathComponent == "mic" ? "Me · " : url.deletingPathExtension().lastPathComponent == "system" ? "Others · " : ""
                var info = who + "Audio"
                if let track = try? await asset.loadTracks(withMediaType: .audio).first,
                   let format = try? await track.load(.formatDescriptions).first,
                   let asbd = format.audioStreamBasicDescription {
                    let channels = asbd.mChannelsPerFrame == 1 ? "Mono" : asbd.mChannelsPerFrame == 2 ? "Stereo" : "\(asbd.mChannelsPerFrame) channels"
                    info = who + "\(channels) · \(Int(asbd.mSampleRate / 1000)) kHz"
                }
                result.append(["name": url.lastPathComponent, "path": url.path, "kind": "audio", "info": info])
            }
        }
        return result
    }

    /// A small JPEG of the frame one second in, as a data: URL.
    private static func thumbnail(_ asset: AVURLAsset) async -> String? {
        let generator = AVAssetImageGenerator(asset: asset)
        generator.maximumSize = CGSize(width: 640, height: 640)
        generator.appliesPreferredTrackTransform = true
        guard let (image, _) = try? await generator.image(at: CMTime(seconds: 1, preferredTimescale: 600)),
              let jpeg = NSBitmapImageRep(cgImage: image).representation(using: .jpeg, properties: [.compressionFactor: 0.7])
        else { return nil }
        return "data:image/jpeg;base64," + jpeg.base64EncodedString()
    }

    private static func clock(_ seconds: Double) -> String {
        let s = Int(seconds)
        return String(format: "%02d:%02d:%02d", s / 3600, s % 3600 / 60, s % 60)
    }
}
