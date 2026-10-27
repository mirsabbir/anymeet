using System.Diagnostics;
using NAudio.CoreAudioApi;
using NAudio.MediaFoundation;
using NAudio.Wave;

namespace AnyMeet;

/// <summary>
/// Records one audio source (the default microphone, or system audio through
/// WASAPI loopback) to a WAV file, then encodes it to AAC (.m4a) on stop.
/// Loopback delivers nothing while no app plays sound, so gaps are filled with
/// silence: every track stays aligned to the recording clock, which the
/// transcriber needs to put "Me" and "Others" in the right order.
/// </summary>
sealed class AudioTrack : IDisposable
{
    readonly IWaveIn capture;
    readonly string wavPath;
    readonly string m4aPath;
    readonly WaveFileWriter writer;
    readonly WaveFileWriter? live;  // live/<name>.wav, read while it grows by the live transcriber
    readonly Stopwatch clock = new();
    readonly object gate = new();
    long written;
    public Exception? Error { get; private set; }
    public long Bytes => written;

    public AudioTrack(bool loopback, string folder, string name, bool liveCopy)
    {
        capture = loopback ? new WasapiLoopbackCapture() : new WasapiCapture();
        wavPath = Path.Combine(folder, name + ".wav");
        m4aPath = Path.Combine(folder, name + ".m4a");
        writer = new WaveFileWriter(wavPath, capture.WaveFormat);
        if (liveCopy)
        {
            Directory.CreateDirectory(Path.Combine(folder, "live"));
            live = new WaveFileWriter(Path.Combine(folder, "live", name + ".wav"), capture.WaveFormat);
        }
        capture.DataAvailable += (_, e) => Write(e.Buffer, e.BytesRecorded);
        capture.RecordingStopped += (_, e) => { if (e.Exception != null) Error = e.Exception; };
    }

    public void Start()
    {
        clock.Start();
        capture.StartRecording();
    }

    void Write(byte[] buffer, int count)
    {
        lock (gate)
        {
            // This buffer ends now, so it started one buffer-length ago.
            PadSilenceTo(clock.Elapsed - TimeSpan.FromSeconds((double)count / writer.WaveFormat.AverageBytesPerSecond));
            Emit(buffer, count);
            written += count;
        }
    }

    /// <summary>Writes zeros until the file is as long as <paramref name="until"/>, if it is behind by more than 100 ms.</summary>
    void PadSilenceTo(TimeSpan until)
    {
        var format = writer.WaveFormat;
        long expected = (long)(until.TotalSeconds * format.AverageBytesPerSecond);
        long missing = expected - written;
        if (missing < format.AverageBytesPerSecond / 10) return;
        missing -= missing % format.BlockAlign;
        var zeros = new byte[Math.Min(missing, format.AverageBytesPerSecond)];
        for (long left = missing; left > 0; left -= zeros.Length)
        {
            Emit(zeros, (int)Math.Min(left, zeros.Length));
        }
        written += missing;
    }

    void Emit(byte[] data, int count)
    {
        writer.Write(data, 0, count);
        if (live == null) return;
        live.Write(data, 0, count);
        live.Flush();  // so the live transcriber sees it now, not when a buffer fills
    }

    /// <summary>Stops, pads to the end of the recording and encodes to .m4a. Returns the final file.</summary>
    public string Stop()
    {
        capture.StopRecording();
        lock (gate)
        {
            PadSilenceTo(clock.Elapsed);
            writer.Dispose();
            live?.Dispose();
        }
        try
        {
            using var reader = new WaveFileReader(wavPath);
            if (reader.WaveFormat.Channels > 2) return wavPath;  // AAC here takes mono or stereo; keep the WAV
            MediaFoundationApi.Startup();
            MediaFoundationEncoder.EncodeToAac(reader.ToSampleProvider().ToWaveProvider16(), m4aPath, 128_000);
        }
        catch (Exception e)
        {
            Error ??= e;
            return wavPath;  // the WAV is still a complete recording
        }
        File.Delete(wavPath);
        return m4aPath;
    }

    public void Dispose()
    {
        capture.Dispose();
        writer.Dispose();
        live?.Dispose();
    }
}
