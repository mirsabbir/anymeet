using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;

namespace AnyMeet;

/// <summary>
/// Local models for the Settings page, as on macOS: whether each one is on this PC
/// (`transcribe --models`) and downloads with progress (`transcribe --download &lt;id&gt;`,
/// which prints one {"done", "total"} JSON line per update). Call from the UI thread;
/// updates are posted back to it.
/// </summary>
static class ModelDownloads
{
    public const string SpeakerModel = "speakers-pyannote";
    public const string GpuSupport = "gpu-support";  // PyTorch for NVIDIA cards: installed by uv, then checked with `transcribe --device`

    static readonly Dictionary<string, bool> downloaded = new();
    static readonly Dictionary<string, (long Done, long Total)> progress = new();
    static readonly Dictionary<string, string> errors = new();
    static SynchronizationContext? ui;

    /// <summary>The ModelState list in ui/src/types.ts. `refresh` asks the transcriber again (about a second).</summary>
    public static async Task<List<Dictionary<string, object>>> List(bool refresh)
    {
        if (refresh || downloaded.Count == 0)
        {
            foreach (var item in JsonNode.Parse(await TranscriptionRunner.Output("transcribe", "--models"))?.AsArray() ?? new())
                if (item?["id"]?.GetValue<string>() is { } id) downloaded[id] = item["downloaded"]?.GetValue<bool>() ?? false;
        }
        return downloaded.Keys.Order().Select(id =>
        {
            var item = new Dictionary<string, object> { ["id"] = id, ["downloaded"] = downloaded[id] };
            if (progress.TryGetValue(id, out var p)) item["progress"] = new { done = p.Done, total = p.Total };
            if (errors.TryGetValue(id, out var e)) item["error"] = e;
            return item;
        }).ToList();
    }

    public static void Start(string id)
    {
        if (progress.ContainsKey(id)) return;
        ui ??= SynchronizationContext.Current;
        // The model's optional install comes first (torch with transformers or pyannote), so it is ready to use after this.
        string[] args = id == GpuSupport ? [.. TranscriptionRunner.Extras(local: true, speakers: false), "transcribe", "--device"]
            : [.. TranscriptionRunner.Extras(local: id != SpeakerModel, speakers: id == SpeakerModel), "transcribe", "--download", id];
        var process = new Process { StartInfo = TranscriptionRunner.UvStartInfo(args) };
        var lastError = "";
        process.OutputDataReceived += (_, e) =>
        {
            if (e.Data?.StartsWith('{') != true) return;
            try
            {
                var line = JsonNode.Parse(e.Data);
                if (line?["done"]?.GetValue<long>() is not { } done || line["total"]?.GetValue<long>() is not { } total) return;
                ui?.Post(_ => { if (progress.ContainsKey(id)) Update(id, (done, total)); }, null);  // not after it ended
            }
            catch (JsonException) { }
        };
        // Progress bars use \r; the last real line is the reason if the download fails.
        process.ErrorDataReceived += (_, e) =>
        {
            if (e.Data?.Split('\r').LastOrDefault(s => s.Trim().Length > 0) is { } line) lastError = line;
        };
        process.Start();  // throws when uv is missing; the bridge shows the message
        process.BeginOutputReadLine();
        process.BeginErrorReadLine();
        Update(id, (0, 0));  // 0 of 0: getting ready (uv may install packages first)
        _ = Task.Run(async () =>
        {
            await process.WaitForExitAsync();
            var code = process.ExitCode;
            ui?.Post(_ =>
            {
                if (code == 0) downloaded[id] = true;
                Update(id, null, code == 0 ? null : lastError.Length > 0 ? lastError : $"exit {code}");
            }, null);
        });
    }

    static void Update(string id, (long, long)? now, string? error = null)
    {
        if (now is { } p) progress[id] = p; else progress.Remove(id);
        if (error != null) errors[id] = error; else errors.Remove(id);
        WebWindow.Emit("modelsChanged");
    }
}
