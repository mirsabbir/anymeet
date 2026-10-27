import Foundation
import WebKit

/// Serves recordings to the web view as anymeet-media://meetings/<meeting id>/<file>, with
/// byte ranges, so <audio> and <video> play and seek them inside the app. Decoding is the
/// system's (WebKit uses AVFoundation), so the app ships no codecs of its own.
final class MediaSchemeHandler: NSObject, WKURLSchemeHandler {
    nonisolated static let scheme = "anymeet-media"
    /// Largest piece sent per request; the player asks for the next piece itself.
    private static let maxChunk: UInt64 = 8 << 20

    nonisolated static func url(for file: URL) -> String {
        "\(scheme)://meetings/\(file.deletingLastPathComponent().lastPathComponent)/\(file.lastPathComponent)"
    }

    func webView(_ webView: WKWebView, start task: any WKURLSchemeTask) {
        guard let url = task.request.url, let file = Self.file(url),
              let size = (try? FileManager.default.attributesOfItem(atPath: file.path)[.size] as? NSNumber)?.uint64Value, size > 0,
              let handle = try? FileHandle(forReadingFrom: file)
        else { return task.didFailWithError(URLError(.fileDoesNotExist)) }
        defer { try? handle.close() }

        let requested = Self.range(task.request.value(forHTTPHeaderField: "Range"), size: size)
        let start = requested?.start ?? 0
        let end = min(requested?.end ?? size - 1, start + Self.maxChunk - 1)
        try? handle.seek(toOffset: start)
        let data = (try? handle.read(upToCount: Int(end - start + 1))) ?? Data()
        let headers = [
            "Content-Type": Self.mimeTypes[file.pathExtension.lowercased()] ?? "application/octet-stream",
            "Content-Length": "\(data.count)",
            "Accept-Ranges": "bytes",
            "Content-Range": "bytes \(start)-\(start + UInt64(data.count) - 1)/\(size)",
        ]
        task.didReceive(HTTPURLResponse(url: url, statusCode: requested == nil ? 200 : 206, httpVersion: "HTTP/1.1", headerFields: headers)!)
        task.didReceive(data)
        task.didFinish()
    }

    func webView(_ webView: WKWebView, stop task: any WKURLSchemeTask) {}

    private static let mimeTypes = ["mp4": "video/mp4", "mov": "video/quicktime", "m4a": "audio/mp4", "wav": "audio/wav", "mp3": "audio/mpeg"]

    /// The file a URL names, only if it is inside one meeting folder.
    private static func file(_ url: URL) -> URL? {
        let parts = url.path.split(separator: "/").map(String.init)
        guard url.host == "meetings", parts.count == 2, let folder = MeetingStore.folder(parts[0]),
              !parts[1].hasPrefix("."), !parts[1].contains("..") else { return nil }
        let file = folder.appending(path: parts[1])
        return FileManager.default.fileExists(atPath: file.path) ? file : nil
    }

    /// "bytes=a-b", "bytes=a-" or "bytes=-n" (the last n bytes).
    private static func range(_ header: String?, size: UInt64) -> (start: UInt64, end: UInt64)? {
        guard let spec = header?.split(separator: "=", maxSplits: 1).last, header?.hasPrefix("bytes=") == true else { return nil }
        let ends = spec.split(separator: "-", maxSplits: 1, omittingEmptySubsequences: false).map { UInt64($0.trimmingCharacters(in: .whitespaces)) }
        guard ends.count == 2 else { return nil }
        switch (ends[0], ends[1]) {
        case let (a?, b?) where a <= b && a < size: return (a, min(b, size - 1))
        case let (a?, nil) where a < size: return (a, size - 1)
        case let (nil, n?) where n > 0: return (size - min(n, size), size - 1)
        default: return nil
        }
    }
}
