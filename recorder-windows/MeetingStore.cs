using System.Globalization;
using System.Text.Json;
using NAudio.Wave;
using Windows.Storage;
using Windows.Storage.FileProperties;

namespace AnyMeet;

/// <summary>
/// Reads meeting folders under %USERPROFILE%\Meetings into the shapes the UI expects
/// (ui/src/types.ts: MeetingSummary, Meeting, MeetingFile, Segment).
/// </summary>
static class MeetingStore
{
    static readonly HashSet<string> AudioExtensions = new(StringComparer.OrdinalIgnoreCase) { ".m4a", ".mp3", ".wav", ".flac", ".aac" };

    /// <summary>Ids come from the UI: only direct children of the meetings folder are valid.</summary>
    public static string? Folder(string id)
    {
        if (string.IsNullOrEmpty(id) || id.IndexOfAny(Path.GetInvalidFileNameChars()) >= 0 || id is "." or "..") return null;
        var path = Path.Combine(Preferences.MeetingsRoot, id);
        return Directory.Exists(path) ? path : null;
    }

    public static List<Dictionary<string, object?>> List()
    {
        if (!Directory.Exists(Preferences.MeetingsRoot)) return new();
        return Directory.GetDirectories(Preferences.MeetingsRoot)
            .Where(d => !Path.GetFileName(d).StartsWith('.'))
            .Select(d => Summary(Path.GetFileName(d), d))
            .OrderByDescending(m => (string)m["startedAt"]!)
            .ToList();
    }

    public static async Task<Dictionary<string, object?>?> Get(string id)
    {
        if (Folder(id) is not string folder) return null;
        var meeting = Summary(id, folder);
        var transcript = ReadTranscript(folder);
        var segments = transcript is JsonElement t && t.TryGetProperty("segments", out var s) ? s.EnumerateArray().ToList() : new();
        meeting["folder"] = folder;
        meeting["model"] = transcript is JsonElement t2 && t2.TryGetProperty("model", out var m) ? m.GetString() : null;
        meeting["segments"] = segments;
        meeting["transcriptText"] = string.Join("\n\n", segments
            .Where(x => !string.IsNullOrEmpty(x.GetProperty("text").GetString()))
            .Select(x => $"[{Clock(x.GetProperty("start").GetDouble())}] {x.GetProperty("speaker").GetString()}: {x.GetProperty("text").GetString()}"));
        meeting["files"] = await Files(folder);
        return meeting;
    }

    static Dictionary<string, object?> Summary(string id, string folder)
    {
        var segments = ReadTranscript(folder) is JsonElement t && t.TryGetProperty("segments", out var s) ? s.EnumerateArray().ToList() : null;
        string status =
            TranscriptionRunner.IsRunning(folder) ? "transcribing" :
            segments != null ? (segments.Any(x => !string.IsNullOrEmpty(x.GetProperty("failed").GetString())) ? "failed" : "done") :
            File.Exists(Path.Combine(folder, "transcribe.log")) ? "failed" : "none";
        var preview = segments?.Select(x => x.GetProperty("text").GetString()).FirstOrDefault(x => !string.IsNullOrEmpty(x)) ?? "";
        var started = DateTime.TryParseExact(id, "yyyy-MM-dd_HHmm", CultureInfo.InvariantCulture, DateTimeStyles.None, out var parsed)
            ? parsed : Directory.GetCreationTime(folder);
        return new()
        {
            ["id"] = id,
            ["startedAt"] = started.ToString("yyyy-MM-dd'T'HH:mm:ss", CultureInfo.InvariantCulture),  // local time, as the folder name is
            ["durationSec"] = MediaFiles(folder).Where(IsAudio).Select(f => Probe(f)?.TotalTime.TotalSeconds ?? 0).DefaultIfEmpty(0).Max(),
            ["status"] = status,
            ["preview"] = preview.Length > 140 ? preview[..140] : preview,
        };
    }

    static JsonElement? ReadTranscript(string folder)
    {
        var path = Path.Combine(folder, "transcript.json");
        if (!File.Exists(path)) return null;
        try { return JsonDocument.Parse(File.ReadAllText(path)).RootElement.Clone(); }
        catch (JsonException) { return null; }
    }

    static bool IsAudio(string path) => AudioExtensions.Contains(Path.GetExtension(path));

    static IEnumerable<string> MediaFiles(string folder) => Directory.GetFiles(folder).OrderBy(f => f)
        .Where(f => Path.GetFileName(f).StartsWith("display-") || IsAudio(f));

    /// <summary>Duration and format of an audio file, or null when Media Foundation can't read it.</summary>
    static (TimeSpan TotalTime, WaveFormat Format)? Probe(string path)
    {
        try
        {
            using var reader = new MediaFoundationReader(path);
            return (reader.TotalTime, reader.WaveFormat);
        }
        catch { return null; }
    }

    static async Task<List<Dictionary<string, object?>>> Files(string folder)
    {
        var result = new List<Dictionary<string, object?>>();
        foreach (var path in MediaFiles(folder))
        {
            var name = Path.GetFileName(path);
            if (name.StartsWith("display-"))
            {
                result.Add(new() { ["name"] = name, ["path"] = path, ["kind"] = "display", ["info"] = "Video", ["thumbnail"] = await Thumbnail(path) });
                continue;
            }
            var stem = Path.GetFileNameWithoutExtension(path);
            var who = stem == "mic" ? "Me · " : stem == "system" ? "Others · " : "";
            var info = who + "Audio";
            if (Probe(path) is var (_, format))
            {
                var channels = format.Channels switch { 1 => "Mono", 2 => "Stereo", var c => $"{c} channels" };
                info = $"{who}{channels} · {format.SampleRate / 1000} kHz";
            }
            result.Add(new() { ["name"] = name, ["path"] = path, ["kind"] = "audio", ["info"] = info });
        }
        return result;
    }

    /// <summary>The thumbnail Windows Explorer would show, as a data: URL.</summary>
    static async Task<string?> Thumbnail(string path)
    {
        try
        {
            var file = await StorageFile.GetFileFromPathAsync(path);
            using var thumb = await file.GetThumbnailAsync(ThumbnailMode.SingleItem, 640);
            using var memory = new MemoryStream();
            await thumb.AsStreamForRead().CopyToAsync(memory);
            return $"data:{thumb.ContentType};base64,{Convert.ToBase64String(memory.ToArray())}";
        }
        catch { return null; }
    }

    static string Clock(double seconds)
    {
        var t = TimeSpan.FromSeconds(Math.Floor(seconds));
        return $"{(int)t.TotalHours:00}:{t.Minutes:00}:{t.Seconds:00}";
    }
}
