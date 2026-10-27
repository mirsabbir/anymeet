using System.Diagnostics;

namespace MeetingRecorder;

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

    /// <summary>Returns false when the transcriber is not set up or this folder is already running.</summary>
    public static bool Start(string folder, Action<bool>? completion = null)
    {
        ui ??= SynchronizationContext.Current;
        if (Preferences.TranscriberProject == null || running.Contains(folder)) return false;
        var info = new ProcessStartInfo("uv")
        {
            ArgumentList = { "run", "--project", Preferences.TranscriberProject, "transcribe", folder },
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = System.Text.Encoding.UTF8,
            StandardErrorEncoding = System.Text.Encoding.UTF8,
            CreateNoWindow = true,
            UseShellExecute = false,
        };
        info.Environment["TRANSCRIBER"] = Preferences.Transcriber;
        info.Environment["PYTHONIOENCODING"] = "utf-8";  // the log has Bangla text

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
