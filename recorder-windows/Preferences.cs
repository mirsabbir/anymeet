using System.Reflection;
using System.Text.Json;

namespace AnyMeet;

/// <summary>
/// App settings, all in %APPDATA%\AnyMeet: settings.json for preferences,
/// glossary.txt and .env (API key), the folder the `transcribe` command reads, as on macOS.
/// </summary>
static class Preferences
{
    public static readonly string MeetingsRoot = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), "Meetings");
    public static readonly string? TranscriberProject = FindTranscriber();

    /// <summary>
    /// The transcriber project: ANYMEET_TRANSCRIBER if set, else the path
    /// baked in at build time, else a transcriber/ folder above the exe (a build
    /// inside the repo). A CI build's baked path doesn't exist here, so it falls through.
    /// </summary>
    static string? FindTranscriber()
    {
        static bool IsProject(string? dir) => dir != null && File.Exists(Path.Combine(dir, "pyproject.toml"));
        var fromEnv = Environment.GetEnvironmentVariable("ANYMEET_TRANSCRIBER");
        if (IsProject(fromEnv)) return fromEnv;
        var baked = typeof(Preferences).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
            .FirstOrDefault(a => a.Key == "TranscriberProject")?.Value;
        if (IsProject(baked)) return baked;
        for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir != null; dir = dir.Parent)
        {
            var candidate = Path.Combine(dir.FullName, "transcriber");
            if (IsProject(candidate)) return candidate;
        }
        return null;
    }
    const string KeyName = "GEMINI_API_KEY";  // the only available transcriber's key

    public static readonly object[] Transcribers =
    {
        new { id = "gemini-3.5-transcribe", name = "Gemini 3.5 Transcribe", note = "Tested on our Bangla audio · ~$0.35/hour", available = true },
        new { id = "soniox", name = "Soniox", note = "Not added yet", available = false },
        new { id = "sarvam", name = "Sarvam", note = "Not added yet", available = false },
        new { id = "local-whisper", name = "Local Whisper", note = "Not added yet", available = false },
    };

    record Stored(bool AutoTranscribe = true, string Transcriber = "gemini-3.5-transcribe");

    static readonly string SettingsFile = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ApplicationData), "AnyMeet", "settings.json");

    static Stored Load()
    {
        try { return JsonSerializer.Deserialize<Stored>(File.ReadAllText(SettingsFile)) ?? new(); }
        catch { return new(); }
    }

    static void Save(Stored s)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(SettingsFile)!);
        File.WriteAllText(SettingsFile, JsonSerializer.Serialize(s));
    }

    public static bool AutoTranscribe
    {
        get => Load().AutoTranscribe;
        set => Save(Load() with { AutoTranscribe = value });
    }

    public static string Transcriber
    {
        get => Load().Transcriber;
        set => Save(Load() with { Transcriber = value });
    }

    static string? ConfigFile(string name)
    {
        Directory.CreateDirectory(Path.GetDirectoryName(SettingsFile)!);
        return Path.Combine(Path.GetDirectoryName(SettingsFile)!, name);
    }

    public static List<string> Glossary
    {
        get
        {
            var path = ConfigFile("glossary.txt");
            if (path == null || !File.Exists(path)) return new();
            return File.ReadAllLines(path).Select(l => l.Trim()).Where(l => l.Length > 0 && !l.StartsWith('#')).ToList();
        }
        set
        {
            var path = ConfigFile("glossary.txt");
            if (path == null) return;
            var header = "# Terms the transcriber should recognize and write exactly like this.\n# One per line. Edited from the app's Settings → Glossary.\n";
            File.WriteAllText(path, header + string.Join("\n", value) + "\n");
        }
    }

    static List<string> EnvLines()
    {
        var path = ConfigFile(".env");
        return path != null && File.Exists(path) ? File.ReadAllLines(path).Where(l => l.Length > 0).ToList() : new();
    }

    /// <summary>True when .env has a non-empty key. The value itself is never returned to the UI.</summary>
    public static bool ApiKeySet => EnvLines().Any(l => l.StartsWith(KeyName + "=") && l.Length > KeyName.Length + 1);

    public static void SetApiKey(string key)
    {
        var path = ConfigFile(".env") ?? throw new InvalidOperationException("transcriber project not found");
        var lines = EnvLines().Where(l => !l.StartsWith(KeyName + "=")).ToList();
        lines.Add($"{KeyName}={key.Trim()}");
        File.WriteAllText(path, string.Join("\n", lines) + "\n");
    }
}
