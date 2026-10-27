import AVFoundation
import ScreenCaptureKit

/// Records every display to its own display-N.mp4, system audio to system.m4a
/// and the default microphone to mic.m4a, all in one meeting folder.
///
/// Video uses SCRecordingOutput (hardware encoding, written by the system).
/// Audio comes from one extra stream that delivers raw buffers, so system and
/// microphone land in separate files: the transcriber labels mic as "আমি".
final class Recorder: NSObject, SCStreamOutput, SCStreamDelegate, SCRecordingOutputDelegate {
    var onStreamError: ((Error) -> Void)?

    private var streams: [SCStream] = []
    private var system: AudioFileWriter?
    private var mic: AudioFileWriter?
    private let audioQueue = DispatchQueue(label: "recorder.audio")
    private var videoDone = DispatchGroup()
    private let lock = NSLock()  // recording-output callbacks arrive on arbitrary threads
    private var videoFinished = Set<ObjectIdentifier>()
    private var videoErrors: [Error] = []

    /// `live`: also write live/mic.wav and live/system.wav as they record, for live transcription.
    func start(in folder: URL, live: Bool) async throws {
        let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
        guard let firstDisplay = content.displays.first else { throw RecorderError.noDisplay }
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        videoDone = DispatchGroup()

        for (n, display) in content.displays.enumerated() {
            let stream = SCStream(filter: SCContentFilter(display: display, excludingWindows: []),
                                  configuration: Self.videoConfig(for: display), delegate: self)
            let rc = SCRecordingOutputConfiguration()
            rc.outputURL = folder.appending(path: "display-\(n + 1).mp4")
            rc.outputFileType = .mp4
            if rc.availableVideoCodecTypes.contains(.hevc) { rc.videoCodecType = .hevc }
            try stream.addRecordingOutput(SCRecordingOutput(configuration: rc, delegate: self))
            videoDone.enter()
            streams.append(stream)
        }

        let liveFolder = folder.appending(path: "live")
        system = AudioFileWriter(url: folder.appending(path: "system.m4a"), live: live ? liveFolder.appending(path: "system.wav") : nil)
        mic = AudioFileWriter(url: folder.appending(path: "mic.m4a"), live: live ? liveFolder.appending(path: "mic.wav") : nil)
        let audioStream = SCStream(filter: SCContentFilter(display: firstDisplay, excludingWindows: []),
                                   configuration: Self.audioConfig(), delegate: self)
        try audioStream.addStreamOutput(self, type: .audio, sampleHandlerQueue: audioQueue)
        try audioStream.addStreamOutput(self, type: .microphone, sampleHandlerQueue: audioQueue)
        streams.append(audioStream)

        do {
            for stream in streams { try await stream.startCapture() }
        } catch {
            _ = await stop()
            throw error
        }
    }

    /// Stops everything and returns problems worth showing (empty when all files are good).
    func stop() async -> [String] {
        for stream in streams { try? await stream.stopCapture() }
        let hadVideo = streams.count > 1
        streams = []
        // Recording outputs finish asynchronously after the stream stops.
        if hadVideo, await waitForVideo(seconds: 15) == .timedOut { videoErrors.append(RecorderError.videoTimeout) }

        var problems = lock.withLock { videoErrors.map { "video: \($0.localizedDescription)" } }
        // Let buffers already queued reach the writers before closing the files.
        await withCheckedContinuation { done in audioQueue.async { done.resume() } }
        for (name, writer) in [("system audio", system), ("microphone", mic)] {
            guard let writer else { continue }
            await writer.finish()
            if let error = writer.error { problems.append("\(name): \(error.localizedDescription)") }
            if writer.buffers == 0 { problems.append("\(name): no audio was received") }
        }
        (system, mic) = (nil, nil)
        lock.withLock { (videoErrors, videoFinished) = ([], []) }
        return problems
    }

    private func waitForVideo(seconds: Double) async -> DispatchTimeoutResult {
        await withCheckedContinuation { done in
            DispatchQueue.global().async { done.resume(returning: self.videoDone.wait(timeout: .now() + seconds)) }
        }
    }

    private static func videoConfig(for display: SCDisplay) -> SCStreamConfiguration {
        let config = SCStreamConfiguration()
        let mode = CGDisplayCopyDisplayMode(display.displayID)
        config.width = mode?.pixelWidth ?? display.width  // full pixels keep small text readable
        config.height = mode?.pixelHeight ?? display.height
        config.minimumFrameInterval = CMTime(value: 1, timescale: 15)
        config.showsCursor = true
        return config
    }

    private static func audioConfig() -> SCStreamConfiguration {
        let config = SCStreamConfiguration()
        config.capturesAudio = true
        config.captureMicrophone = true  // default input device
        config.excludesCurrentProcessAudio = true
        config.sampleRate = 48_000
        config.channelCount = 2
        // This stream exists only for audio; keep its video as cheap as possible.
        config.width = 2
        config.height = 2
        config.minimumFrameInterval = CMTime(value: 1, timescale: 1)
        return config
    }

    // MARK: ScreenCaptureKit callbacks

    func stream(_ stream: SCStream, didOutputSampleBuffer buffer: CMSampleBuffer, of type: SCStreamOutputType) {
        switch type {
        case .audio: system?.append(buffer)
        case .microphone: mic?.append(buffer)
        default: break
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        onStreamError?(error)
    }

    /// Self-test: stops the first display's stream and reports it the way macOS does when
    /// the user clicks Stop on the system's screen-recording indicator.
    func simulateSystemStop() async {
        guard let first = streams.first else { return }
        try? await first.stopCapture()
        stream(first, didStopWithError: SCStreamError(.userStopped))
    }

    func recordingOutputDidFinishRecording(_ recordingOutput: SCRecordingOutput) {
        markVideoDone(recordingOutput, error: nil)
    }

    func recordingOutput(_ recordingOutput: SCRecordingOutput, didFailWithError error: Error) {
        markVideoDone(recordingOutput, error: error)
    }

    /// A failed output may also report finishing; leave the group only once per output.
    private func markVideoDone(_ output: SCRecordingOutput, error: Error?) {
        let first = lock.withLock {
            if let error { videoErrors.append(error) }
            return videoFinished.insert(ObjectIdentifier(output)).inserted
        }
        if first { videoDone.leave() }
    }
}

enum RecorderError: LocalizedError {
    case noDisplay, videoTimeout

    var errorDescription: String? {
        switch self {
        case .noDisplay: "no display found to record"
        case .videoTimeout: "a display recording did not finish within 15 seconds"
        }
    }
}
