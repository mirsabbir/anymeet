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
    readonly Stopwatch clock = new();
    readonly object gate = new();
    long written;
    public Exception? Error { get; private set; }
    public long Bytes => written;

    public AudioTrack(bool loopback, string folder, string name)
    {
        capture = loopback ? new WasapiLoopbackCapture() : new WasapiCapture();
        wavPath = Path.Combine(folder, name + ".wav");
        m4aPath = Path.Combine(folder, name + ".m4a");
        writer = new WaveFileWriter(wavPath, capture.WaveFormat);
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
            writer.Write(buffer, 0, count);
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
            writer.Write(zeros, 0, (int)Math.Min(left, zeros.Length));
        }
        written += missing;
    }

    /// <summary>Stops, pads to the end of the recording and encodes to .m4a. Returns the final file.</summary>
    public string Stop()
    {
        capture.StopRecording();
        lock (gate)
        {
            PadSilenceTo(clock.Elapsed);
            writer.Dispose();
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
    }
}
