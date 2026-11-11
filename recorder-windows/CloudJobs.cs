using System.Diagnostics;

namespace AnyMeet;

/// <summary>
/// Starts `anymeet &lt;command&gt;` (transcriber/commands.py): cloud backups, downloads, freeing
/// space and AI summaries. A job reports in &lt;meeting&gt;\job.json, which MeetingStore passes to
/// the UI, so this only starts it and tells the windows when it ends. Output goes to anymeet.log
/// in %APPDATA%\AnyMeet. Call from the UI thread, as on macOS (recorder-macos/Sources/CloudJobs.swift).
/// </summary>
static class CloudJobs
{
    static readonly string Log = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "AnyMeet", "anymeet.log");

    public static void Run(params string[] args)
    {
        var ui = SynchronizationContext.Current;
        Directory.CreateDirectory(Path.GetDirectoryName(Log)!);
        if (File.Exists(Log) && new FileInfo(Log).Length > 1_000_000) File.Delete(Log);  // only for looking into a problem
        var log = new StreamWriter(Log, append: true) { AutoFlush = true };
        var process = new Process { StartInfo = TranscriptionRunner.UvStartInfo(["anymeet", .. args]) };
        process.OutputDataReceived += (_, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
        process.ErrorDataReceived += (_, e) => { if (e.Data != null) lock (log) log.WriteLine(e.Data); };
        try { process.Start(); }
        catch (Exception e)
        {
            log.Dispose();
            throw new InvalidOperationException($"could not start uv: {e.Message}");
        }
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        _ = Task.Run(async () =>
        {
            await Task.Delay(2000);  // the job file shows up once uv has started Python
            ui?.Post(_ => TranscriptionRunner.RaiseMeetingsChanged(), null);
            await process.WaitForExitAsync();
            lock (log) log.Dispose();
            ui?.Post(_ => TranscriptionRunner.RaiseMeetingsChanged(), null);
        });
    }

    /// <summary>
    /// Once a meeting's transcript is ready (or recording stopped with transcription off):
    /// a summary if it has none and that is on, then a backup if that is on.
    /// </summary>
    public static void AfterTranscript(string folder)
    {
        var backup = Preferences.AutoBackup && Preferences.CloudReady;
        var summarize = Preferences.AutoSummary && Preferences.AnthropicKeySet && !File.Exists(Path.Combine(folder, "summary.json"));
        try
        {
            if (summarize) Run(["summarize", folder, "--language", Preferences.SummaryLanguage, .. backup ? new[] { "--backup" } : []]);
            else if (backup) Run("backup", folder);
        }
        catch (InvalidOperationException) { }  // uv missing: the meeting page says so when asked by hand
    }

    /// <summary>At launch: list the meetings that are only in the cloud, and finish backups that were cut off.</summary>
    public static void AtLaunch()
    {
        try { if (Preferences.CloudReady) Run("sync", Preferences.MeetingsRoot, "--resume"); }
        catch (InvalidOperationException) { }
    }

    /// <summary>The meeting folders for these ids that no recording or transcription is using now.</summary>
    public static string[] Idle(IEnumerable<string> ids)
    {
        var folders = ids.Select(MeetingStore.Folder).OfType<string>()
            .Where(f => !TranscriptionRunner.IsRunning(f) && !string.Equals(f, TrayApp.Current?.RecordingFolder, StringComparison.OrdinalIgnoreCase))
            .ToArray();
        return folders.Length > 0 ? folders : throw new InvalidOperationException("wait until the recording and the transcript are finished");
    }
}
