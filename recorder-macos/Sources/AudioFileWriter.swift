import AVFoundation

/// Writes one audio source (system or microphone) to an AAC .m4a file.
/// The writer is created on the first buffer, because only then is the
/// source's native format known. All calls must come from one serial queue.
final class AudioFileWriter {
    let url: URL
    private var writer: AVAssetWriter?
    private var input: AVAssetWriterInput?
    private(set) var error: Error?
    private(set) var buffers = 0

    init(url: URL) { self.url = url }

    func append(_ buffer: CMSampleBuffer) {
        guard buffer.isValid, error == nil else { return }
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
        guard let writer, writer.status == .writing else { return }
        input?.markAsFinished()
        await writer.finishWriting()
        if writer.status == .failed { error = writer.error }
    }
}
