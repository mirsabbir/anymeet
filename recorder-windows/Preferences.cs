using System.Diagnostics;
using System.Reflection;
using System.Text.Json;
using Microsoft.Win32;

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
    const string HfTokenName = "HF_TOKEN";  // Hugging Face, read by the transcriber to download the speaker model

    public static readonly string[] AvailableTranscribers = { "gemini-3.5-transcribe", "local-whisper-bangla", "local-whisper-english" };

    public static readonly object[] Transcribers =
    {
        new { id = "gemini-3.5-transcribe", name = "Gemini 3.5 Transcribe", note = "Tested on our Bangla audio · ~$0.35/hour", available = true, needsKey = true },
        new { id = "local-whisper-bangla", name = "Local Whisper (Bangla)", note = "On this PC, free, offline · Bangla only: English words come out in Bangla letters · 1.5 GB", available = true, needsKey = false },
        new { id = "local-whisper-english", name = "Local Whisper (English)", note = "On this PC, free, offline · English-only meetings · 1.6 GB", available = true, needsKey = false },
        new { id = "soniox", name = "Soniox", note = "Not added yet", available = false, needsKey = true },
    };

    record Stored(bool AutoTranscribe = true, string Transcriber = "gemini-3.5-transcribe", string? TranscribeMode = null,
                  bool Speakers = false, string Processor = "cpu");

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

    /// <summary>When to transcribe: "live" (while recording), "after" (when recording stops) or "off".</summary>
    public static string TranscribeMode
    {
        get { var s = Load(); return s.TranscribeMode ?? (s.AutoTranscribe ? "live" : "off"); }
        set => Save(Load() with { TranscribeMode = value });
    }

    public static string Transcriber
    {
        get => Load().Transcriber;
        set => Save(Load() with { Transcriber = value });
    }

    /// <summary>Split "Others" into Speaker 1, Speaker 2… (`transcribe --speakers`).</summary>
    public static bool Speakers
    {
        get => Load().Speakers;
        set => Save(Load() with { Speakers = value });
    }

    /// <summary>
    /// Where local models run, "gpu" or "cpu"; the transcriber reads it as ANYMEET_DEVICE.
    /// The processor by default: the GPU needs a 2.6 GB PyTorch for NVIDIA cards first.
    /// </summary>
    public static string Processor
    {
        get => Load().Processor == "gpu" && NvidiaGpu != null ? "gpu" : "cpu";
        set => Save(Load() with { Processor = value });
    }

    /// <summary>The NVIDIA card's name and memory from nvidia-smi (installed with the driver), or null.</summary>
    public static readonly string? NvidiaGpu = FindNvidiaGpu();

    static string? FindNvidiaGpu()
    {
        string[] candidates = [Path.Combine(Environment.SystemDirectory, "nvidia-smi.exe"),
                               Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), "NVIDIA Corporation", "NVSMI", "nvidia-smi.exe")];
        var smi = candidates.FirstOrDefault(File.Exists);
        if (smi == null) return null;
        try
        {
            using var process = Process.Start(new ProcessStartInfo(smi, "--query-gpu=name,memory.total --format=csv,noheader")
            { RedirectStandardOutput = true, CreateNoWindow = true, UseShellExecute = false })!;
            var line = process.StandardOutput.ReadLine()?.Split(',', StringSplitOptions.TrimEntries);
            process.WaitForExit(5000);
            return line is [var name, var memory] ? $"{name} · {memory.Replace(" MiB", "")} MB" : line?.FirstOrDefault();
        }
        catch { return null; }
    }

    /// <summary>The Processor list in ui/src/types.ts.</summary>
    public static object[] Processors
    {
        get
        {
            var cpu = Registry.GetValue(@"HKEY_LOCAL_MACHINE\HARDWARE\DESCRIPTION\System\CentralProcessor\0", "ProcessorNameString", null) as string;
            return
            [
                new { id = "gpu", name = NvidiaGpu ?? "No NVIDIA graphics card found", available = NvidiaGpu != null,
                      note = NvidiaGpu == null ? "Other graphics cards aren't supported yet" : "Faster, and leaves the processor free · needs a one-time 2.6 GB download" },
                new { id = "cpu", name = $"{cpu?.Trim() ?? "This PC"} · {Environment.ProcessorCount} threads", available = true,
                      note = "Slower, and keeps several processor cores busy" },
            ];
        }
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

    /// <summary>True when .env has a non-empty value for the name. The value itself is never returned to the UI.</summary>
    static bool IsSet(string name) => EnvLines().Any(l => l.StartsWith(name + "=") && l.Length > name.Length + 1);

    public static bool ApiKeySet => IsSet(KeyName);
    public static bool HfTokenSet => IsSet(HfTokenName);
    public static void SetApiKey(string key) => SetSecret(KeyName, key);
    public static void SetHfToken(string token) => SetSecret(HfTokenName, token);

    static void SetSecret(string name, string value)
    {
        var path = ConfigFile(".env") ?? throw new InvalidOperationException("transcriber project not found");
        var lines = EnvLines().Where(l => !l.StartsWith(name + "=")).ToList();
        lines.Add($"{name}={value.Trim()}");
        File.WriteAllText(path, string.Join("\n", lines) + "\n");
    }
}
