using ScreenRecorderLib;

namespace AnyMeet;

/// <summary>
/// Records every display to its own display-N.mp4, system audio to system.m4a
/// and the default microphone to mic.m4a, in one meeting folder — the same
/// layout as the macOS app, so the transcriber and the UI treat both alike.
/// </summary>
sealed class MeetingCapture
{
    readonly List<(Recorder recorder, TaskCompletionSource<string?> done)> videos = new();
    AudioTrack? mic, system;

    public void Start(string folder)
    {
        Directory.CreateDirectory(folder);
        var displays = Recorder.GetDisplays();
        if (displays.Count == 0) throw new InvalidOperationException("no display found to record");

        for (int n = 0; n < displays.Count; n++)
        {
            var options = new RecorderOptions
            {
                SourceOptions = new SourceOptions
                {
                    RecordingSources = { new DisplayRecordingSource(displays[n].DeviceName) { IsCursorCaptureEnabled = true, IsBorderRequired = false } },
                },
                AudioOptions = new AudioOptions { IsAudioEnabled = false },  // audio goes to its own tracks
                VideoEncoderOptions = new VideoEncoderOptions
                {
                    Encoder = new H264VideoEncoder { BitrateMode = H264BitrateControlMode.Quality },
                    Quality = 70,
                    Framerate = 15,
                    IsHardwareEncodingEnabled = true,
                },
            };
            var recorder = Recorder.CreateRecorder(options);
            var done = new TaskCompletionSource<string?>();
            recorder.OnRecordingComplete += (_, _) => done.TrySetResult(null);
            recorder.OnRecordingFailed += (_, e) => done.TrySetResult(e.Error);
            recorder.Record(Path.Combine(folder, $"display-{n + 1}.mp4"));
            videos.Add((recorder, done));
        }

        mic = new AudioTrack(loopback: false, folder, "mic");
        system = new AudioTrack(loopback: true, folder, "system");
        mic.Start();
        system.Start();
    }

    /// <summary>Stops everything; returns problems worth showing (empty when all files are good).</summary>
    public async Task<List<string>> StopAsync()
    {
        var problems = new List<string>();
        foreach (var (recorder, _) in videos) recorder.Stop();

        foreach (var (name, track) in new[] { ("microphone", mic), ("system audio", system) })
        {
            if (track == null) continue;
            await Task.Run(track.Stop);  // AAC encoding takes a few seconds for a long meeting
            if (track.Error != null) problems.Add($"{name}: {track.Error.Message}");
            if (track.Bytes == 0) problems.Add($"{name}: no audio was received");
            track.Dispose();
        }

        for (int n = 0; n < videos.Count; n++)
        {
            var finished = await Task.WhenAny(videos[n].done.Task, Task.Delay(TimeSpan.FromSeconds(15)));
            if (finished != videos[n].done.Task) problems.Add($"display {n + 1}: recording did not finish within 15 seconds");
            else if (videos[n].done.Task.Result is string error) problems.Add($"display {n + 1}: {error}");
            videos[n].recorder.Dispose();
        }
        videos.Clear();
        (mic, system) = (null, null);
        return problems;
    }
}
