using System.Diagnostics;
using System.Text.Json;

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
        Ready();
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

    /// <summary>Throws when the transcriber checkout predates `anymeet`, which uv would only report as "Failed to spawn".</summary>
    public static void Ready()
    {
        var project = Preferences.TranscriberProject;
        if (project != null && !File.Exists(Path.Combine(project, "transcriber", "commands.py")))
            throw new InvalidOperationException($"The transcriber at {project} is older than this app: update it (git pull) to use cloud backup and AI summaries.");
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

    /// <summary>At launch: list the meetings that are only in the cloud, and finish backups and downloads that were cut off.</summary>
    public static void AtLaunch()
    {
        try { if (Preferences.CloudReady) Run("sync", Preferences.MeetingsRoot, "--resume"); }
        catch (InvalidOperationException) { }
    }

    /// <summary>Jobs that move bytes and are running now: a job.json without an error, written in the last 30 s (transcriber/job.py).</summary>
    public static List<(string Folder, JsonElement Job)> Running()
    {
        var result = new List<(string, JsonElement)>();
        if (!Directory.Exists(Preferences.MeetingsRoot)) return result;
        foreach (var folder in Directory.GetDirectories(Preferences.MeetingsRoot))
        {
            if (MeetingStore.ReadJson(folder, "job.json") is not JsonElement job || job.TryGetProperty("error", out _)) continue;
            var kind = job.TryGetProperty("kind", out var k) ? k.GetString() : null;
            var fresh = job.TryGetProperty("updatedAt", out var u) && DateTime.TryParse(u.GetString(), null,
                System.Globalization.DateTimeStyles.AdjustToUniversal | System.Globalization.DateTimeStyles.AssumeUniversal, out var at)
                && DateTime.UtcNow - at < TimeSpan.FromSeconds(30);
            if (fresh && kind is "backup" or "restore" or "free") result.Add((folder, job));
        }
        return result;
    }

    /// <summary>
    /// Quit from the tray menu while a backup or download runs: asks first. Quitting stops the
    /// jobs; each keeps what it sent and `sync --resume` goes on from there at the next launch.
    /// </summary>
    public static bool ConfirmQuit()
    {
        var jobs = Running();
        if (jobs.Count == 0) return true;
        var job = jobs[0].Job;
        var verb = job.GetProperty("kind").GetString() switch { "backup" => "backing up", "restore" => "downloading", _ => "freeing up space for" };
        var progress = job.TryGetProperty("total", out var total) && total.GetDouble() > 0
            ? $"{job.GetProperty("done").GetDouble() / 1e9:0.00} of {total.GetDouble() / 1e9:0.00} GB{(job.TryGetProperty("file", out var f) ? " · now " + f.GetString() : "")}.\n\n" : "";
        var answer = MessageBox.Show(
            $"{progress}If you quit now, it stops and goes on from where it stopped the next time you open AnyMeet. Nothing already transferred is lost.\n\nQuit anyway?",
            $"AnyMeet is still {verb} {(jobs.Count == 1 ? "a meeting" : $"{jobs.Count} meetings")}",
            MessageBoxButtons.YesNo, MessageBoxIcon.Warning, MessageBoxDefaultButton.Button2);
        if (answer != DialogResult.Yes) return false;
        foreach (var (folder, _) in jobs) File.WriteAllText(Path.Combine(folder, "job.stop"), "");
        return true;
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
