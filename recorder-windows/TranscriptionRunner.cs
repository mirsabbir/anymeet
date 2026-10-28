using System.Diagnostics;

namespace AnyMeet;

/// <summary>
/// Runs `uv run --project &lt;transcriber&gt; transcribe &lt;folder&gt;`, one run per folder
/// at a time, with output in transcribe.log in the meeting folder. Needs uv and
/// ffmpeg on PATH. Call from the UI thread; events are raised on it too.
/// </summary>
static class TranscriptionRunner
{
    static readonly HashSet<string> running = new(StringComparer.OrdinalIgnoreCase);
    static SynchronizationContext? ui;

    /// <summary>Raised when a meeting starts or finishes transcribing.</summary>
    public static event Action? MeetingsChanged;

    public static bool IsRunning(string folder) => running.Contains(folder);
    public static bool AnyRunning => running.Count > 0;

    public static void RaiseMeetingsChanged() => MeetingsChanged?.Invoke();

    /// <summary>`uv run --project &lt;transcriber&gt; &lt;args&gt;` with output redirected as UTF-8.</summary>
    public static ProcessStartInfo UvStartInfo(params string[] args)
    {
        var info = new ProcessStartInfo("uv")
        {
            ArgumentList = { "run", "--project", Preferences.TranscriberProject ?? throw new InvalidOperationException("transcriber not found: set ANYMEET_TRANSCRIBER") },
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8,
            CreateNoWindow = true,
            UseShellExecute = false,
        };
        foreach (var arg in args) info.ArgumentList.Add(arg);
        info.Environment["PYTHONIOENCODING"] = "utf-8";  // the log has Bangla text
        info.Environment["PYTHONUNBUFFERED"] = "1";  // output as it happens
        info.Environment["ANYMEET_DEVICE"] = Preferences.Processor;  // where local models run (device.py)
        return info;
    }

    /// <summary>
    /// The optional installs a run needs: `local` for a local Whisper model, `speakers` for
    /// telling speakers apart, and with the GPU chosen `cuda`, so torch is the NVIDIA build.
    /// Every run that uses torch must pass the same `cuda`, or uv swaps the 2.6 GB build out.
    /// </summary>
    public static string[] Extras(bool local, bool speakers)
    {
        var extras = new List<string>();
        if (local) extras.AddRange(["--extra", "local"]);
        if (speakers) extras.AddRange(["--extra", "speakers"]);
        if ((local || speakers) && Preferences.Processor == "gpu") extras.AddRange(["--extra", "cuda"]);
        return [.. extras];
    }

    /// <summary>Runs `uv run … &lt;args&gt;` to the end and returns its standard output.</summary>
    public static async Task<string> Output(params string[] args)
    {
        using var process = Process.Start(UvStartInfo(args))!;
        var output = process.StandardOutput.ReadToEndAsync();
        var error = process.StandardError.ReadToEndAsync();
        await process.WaitForExitAsync();
        if (process.ExitCode != 0)
            throw new InvalidOperationException((await error).Trim().Split('\n').LastOrDefault() ?? "transcriber failed");
        return await output;
    }

    /// <summary>Returns false when the transcriber is not set up or this folder is already running.</summary>
    /// <summary>`live`: start while recording; the transcriber follows live/*.wav until live/done appears.</summary>
    public static bool Start(string folder, bool live = false, Action<bool>? completion = null)
    {
        ui ??= SynchronizationContext.Current;
        if (Preferences.TranscriberProject == null || running.Contains(folder)) return false;
        var speakers = Preferences.Speakers;
        var info = UvStartInfo([.. Extras(Preferences.Transcriber.StartsWith("local-"), speakers), "transcribe", folder,
                                .. live ? new[] { "--live" } : [], .. speakers ? new[] { "--speakers" } : []]);
        info.Environment["TRANSCRIBER"] = Preferences.Transcriber;

        var log = new StreamWriter(Path.Combine(folder, "transcribe.log")) { AutoFlush = true };
        var process = new Process { StartInfo = info };
        process.OutputDataReceived += (_, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
        process.ErrorDataReceived += (_, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
        try { process.Start(); }
        catch (Exception e)  // uv not installed or not on PATH
        {
            log.WriteLine($"could not start uv: {e.Message}");
            log.Dispose();
            return false;
        }
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();

        running.Add(folder);
        RaiseMeetingsChanged();
        _ = Task.Run(async () =>
        {
            await process.WaitForExitAsync();
            bool ok = process.ExitCode == 0;
            lock (log) log.Dispose();
            ui?.Post(_ =>
            {
                running.Remove(folder);
                RaiseMeetingsChanged();
                completion?.Invoke(ok);
            }, null);
        });
        return true;
    }
}
