import AVFoundation

/// Writes one audio source (system or microphone) to an AAC .m4a file.
/// The writer is created on the first buffer, because only then is the
/// source's native format known. All calls must come from one serial queue.
final class AudioFileWriter {
    let url: URL
    private let live: LiveWav?
    private var writer: AVAssetWriter?
    private var input: AVAssetWriterInput?
    private(set) var error: Error?
    private(set) var buffers = 0

    /// `live`: also append the audio to this growing WAV for live transcription.
    init(url: URL, live: URL? = nil) {
        self.url = url
        self.live = live.map(LiveWav.init)
    }

    func append(_ buffer: CMSampleBuffer) {
        guard buffer.isValid, error == nil else { return }
        live?.append(buffer)
        if writer == nil { start(with: buffer) }
        guard let input, input.isReadyForMoreMediaData else { return }
        if input.append(buffer) { buffers += 1 } else { error = writer?.error }
    }

    private func start(with buffer: CMSampleBuffer) {
        guard let format = buffer.formatDescription, let asbd = format.audioStreamBasicDescription else { return }
        do {
            let writer = try AVAssetWriter(outputURL: url, fileType: .m4a)
            let input = AVAssetWriterInput(mediaType: .audio, outputSettings: [
                AVFormatIDKey: kAudioFormatMPEG4AAC,
                AVSampleRateKey: asbd.mSampleRate,
                AVNumberOfChannelsKey: min(Int(asbd.mChannelsPerFrame), 2),
            ], sourceFormatHint: format)
            input.expectsMediaDataInRealTime = true
            writer.add(input)
            guard writer.startWriting() else { throw writer.error ?? CocoaError(.fileWriteUnknown) }
            writer.startSession(atSourceTime: buffer.presentationTimeStamp)
            (self.writer, self.input) = (writer, input)
        } catch {
            self.error = error
        }
    }

    func finish() async {
        live?.close()
        guard let writer, writer.status == .writing else { return }
        input?.markAsFinished()
        await writer.finishWriting()
        if writer.status == .failed { error = writer.error }
    }
}

/// A WAV that grows while recording: the first channel as 32-bit float at the
/// source's rate. The live transcriber reads it as it grows (transcriber/live.py);
/// the header's sizes are filled in on close.
final class LiveWav {
    private let url: URL
    private var handle: FileHandle?
    private var rate: UInt32 = 0
    private var bytes: UInt32 = 0

    init(url: URL) { self.url = url }

    func append(_ buffer: CMSampleBuffer) {
        guard let description = buffer.formatDescription else { return }
        let format = AVAudioFormat(cmAudioFormatDescription: description)
        if handle == nil { open(rate: UInt32(format.sampleRate)) }
        try? buffer.withAudioBufferList { list, _ in
            guard let pcm = AVAudioPCMBuffer(pcmFormat: format, bufferListNoCopy: list.unsafePointer) else { return }
            let frames = Int(pcm.frameLength)
            let stride = format.isInterleaved ? Int(format.channelCount) : 1
            var samples = [Float](repeating: 0, count: frames)
            if let floats = pcm.floatChannelData?[0] {
                for i in 0..<frames { samples[i] = floats[i * stride] }
            } else if let ints = pcm.int16ChannelData?[0] {
                for i in 0..<frames { samples[i] = Float(ints[i * stride]) / 32768 }
            } else if let ints = pcm.int32ChannelData?[0] {
                for i in 0..<frames { samples[i] = Float(ints[i * stride]) / 2_147_483_648 }
            }
            samples.withUnsafeBytes { handle?.write(Data($0)) }
            bytes &+= UInt32(frames * 4)
        }
    }

    private func open(rate: UInt32) {
        self.rate = rate
        try? FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        FileManager.default.createFile(atPath: url.path, contents: Self.header(rate: rate, dataBytes: 0))
        handle = try? FileHandle(forWritingTo: url)
        handle?.seekToEndOfFile()
    }

    func close() {
        guard let handle else { return }
        handle.seek(toFileOffset: 0)
        handle.write(Self.header(rate: rate, dataBytes: bytes))
        try? handle.close()
        self.handle = nil
    }

    /// 44-byte header for mono IEEE float.
    private static func header(rate: UInt32, dataBytes: UInt32) -> Data {
        var d = Data()
        func put(_ v: UInt32) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        func put16(_ v: UInt16) { withUnsafeBytes(of: v.littleEndian) { d.append(contentsOf: $0) } }
        d.append(contentsOf: Array("RIFF".utf8)); put(36 &+ dataBytes); d.append(contentsOf: Array("WAVE".utf8))
        d.append(contentsOf: Array("fmt ".utf8)); put(16); put16(3); put16(1); put(rate); put(rate * 4); put16(4); put16(32)
        d.append(contentsOf: Array("data".utf8)); put(dataBytes)
        return d
    }
}
