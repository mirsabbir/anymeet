import Foundation

/// Local models for the Settings page: whether each one is on this computer
/// (`transcribe --models`) and downloads with progress (`transcribe --download <id>`,
/// which prints one {"done", "total"} JSON line per update). Main thread only.
@MainActor
final class ModelDownloads {
    static let shared = ModelDownloads()
    static let changed = Notification.Name("ModelsChanged")
    static let speakerModel = "speakers-pyannote"

    private var downloaded: [String: Bool] = [:]
    private var progress: [String: (done: Int64, total: Int64)] = [:]
    private var errors: [String: String] = [:]
    private var lines: [String: Data] = [:]  // stdout not yet split into lines, per download
    private var lastError: [String: String] = [:]

    /// The ModelState list in ui/src/types.ts. `refresh` asks the transcriber again (about a
    /// second); otherwise the last answer is reused, since progress events ask often.
    func list(refresh: Bool) async throws -> [[String: Any]] {
        if refresh || downloaded.isEmpty {
            let items = try JSONSerialization.jsonObject(with: try await TranscriptionRunner.output(["transcribe", "--models"])) as? [[String: Any]] ?? []
            for item in items { if let id = item["id"] as? String { downloaded[id] = item["downloaded"] as? Bool ?? false } }
        }
        return downloaded.keys.sorted().map { id in
            var item: [String: Any] = ["id": id, "downloaded": downloaded[id] ?? false]
            if let p = progress[id] { item["progress"] = ["done": p.done, "total": p.total] }
            if let e = errors[id] { item["error"] = e }
            return item
        }
    }

    func start(_ id: String) throws {
        guard progress[id] == nil else { return }
        // The model's optional install comes first (torch with transformers or pyannote), so it is ready to use after this.
        let extras = TranscriptionRunner.extras(local: id != Self.speakerModel, speakers: id == Self.speakerModel)
        let process = try TranscriptionRunner.uvProcess(extras + ["transcribe", "--download", id])
        let out = Pipe(), err = Pipe()
        process.standardOutput = out
        process.standardError = err
        lines[id] = Data()
        lastError[id] = ""
        // Pipes call back on their own threads; all state changes happen on the main thread.
        out.fileHandleForReading.readabilityHandler = { handle in
            let data = handle.availableData
            DispatchQueue.main.async { MainActor.assumeIsolated { self.received(id, data) } }
        }
        err.fileHandleForReading.readabilityHandler = { handle in
            let text = String(decoding: handle.availableData, as: UTF8.self)
            DispatchQueue.main.async { MainActor.assumeIsolated { self.receivedError(id, text) } }
        }
        process.terminationHandler = { p in
            out.fileHandleForReading.readabilityHandler = nil
            err.fileHandleForReading.readabilityHandler = nil
            let status = p.terminationStatus
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    if status == 0 { self.downloaded[id] = true }
                    let reason = self.lastError[id].flatMap { $0.isEmpty ? nil : $0 } ?? "exit \(status)"
                    self.update(id, progress: nil, error: status == 0 ? nil : reason)
                }
            }
        }
        try process.run()
        update(id, progress: (0, 0))  // 0 of 0: getting ready (uv may install packages first)
    }

    /// {"done": bytes, "total": bytes} lines from `transcribe --download`.
    private func received(_ id: String, _ data: Data) {
        var buffer = (lines[id] ?? Data()) + data
        while let end = buffer.firstIndex(of: UInt8(ascii: "\n")) {
            let line = buffer[..<end]
            buffer.removeSubrange(...end)
            guard let json = try? JSONSerialization.jsonObject(with: line) as? [String: Any],
                  let done = (json["done"] as? NSNumber)?.int64Value, let total = (json["total"] as? NSNumber)?.int64Value
            else { continue }
            if progress[id] != nil { update(id, progress: (done, total)) }  // not after it ended
        }
        lines[id] = buffer
    }

    /// Progress bars use \r; the last real line is the reason if the download fails.
    private func receivedError(_ id: String, _ text: String) {
        if let line = text.split(whereSeparator: { $0 == "\n" || $0 == "\r" }).last(where: { !$0.trimmingCharacters(in: .whitespaces).isEmpty }) {
            lastError[id] = String(line)
        }
    }

    private func update(_ id: String, progress: (done: Int64, total: Int64)?, error: String? = nil) {
        self.progress[id] = progress
        errors[id] = error
        NotificationCenter.default.post(name: Self.changed, object: nil)
    }
}
