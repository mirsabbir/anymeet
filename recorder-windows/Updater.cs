using System.Diagnostics;
using System.IO.Compression;
using System.Reflection;
using System.Text.Json;

namespace AnyMeet;

/// <summary>
/// Updates from GitHub Releases, like the macOS app: compares this build's version
/// with the latest release, downloads AnyMeet-Windows-x64.zip, and after the app
/// exits a helper copies the new files over the app folder and starts it again.
/// The repository is baked in at build time (UpdateRepository); development builds
/// never update themselves.
/// </summary>
static class Updater
{
    static readonly string? Repository = typeof(Updater).Assembly.GetCustomAttributes<AssemblyMetadataAttribute>()
        .FirstOrDefault(a => a.Key == "UpdateRepository")?.Value is { Length: > 0 } r ? r : null;
    public static readonly string Current = (Application.ProductVersion ?? "0.0.0-dev").Split('+')[0];
    static readonly HttpClient http = new() { DefaultRequestHeaders = { { "User-Agent", "AnyMeet" } } };
    static (DateTime At, Dictionary<string, object?> Info)? cached;

    /// <summary>The UpdateInfo shape in ui/src/types.ts.</summary>
    public static async Task<Dictionary<string, object?>> Check(bool force)
    {
        if (!force && cached is { } c && DateTime.Now - c.At < TimeSpan.FromHours(1)) return c.Info;
        var info = new Dictionary<string, object?> { ["current"] = Current, ["available"] = false };
        if (Repository == null || Current.Contains("dev"))
        {
            info["error"] = "this is a development build";
            return info;
        }
        try
        {
            var json = await http.GetStringAsync($"https://api.github.com/repos/{Repository}/releases/latest");
            var tag = JsonDocument.Parse(json).RootElement.GetProperty("tag_name").GetString() ?? "";
            var latest = tag.TrimStart('v');
            info["latest"] = latest;
            info["available"] = IsNewer(latest, Current);
            cached = (DateTime.Now, info);
        }
        catch (Exception e)
        {
            info["error"] = e.Message;
        }
        return info;
    }

    static bool IsNewer(string a, string b)
    {
        static int[] Parse(string v) => v.Split('.').Select(p => int.TryParse(new string(p.TakeWhile(char.IsDigit).ToArray()), out var n) ? n : 0).ToArray();
        var (x, y) = (Parse(a), Parse(b));
        for (int i = 0; i < Math.Max(x.Length, y.Length); i++)
        {
            int p = i < x.Length ? x[i] : 0, q = i < y.Length ? y[i] : 0;
            if (p != q) return p > q;
        }
        return false;
    }

    /// <summary>
    /// Downloads and unpacks the update, then starts a hidden PowerShell helper that
    /// waits for this process to exit, copies the files over and restarts the app.
    /// The caller exits the app.
    /// </summary>
    public static async Task Install()
    {
        var info = await Check(force: true);
        if (Repository == null || info["latest"] is not string latest || info["available"] is not true)
            throw new InvalidOperationException(info.GetValueOrDefault("error") as string ?? "no newer version available");
        var appDir = AppContext.BaseDirectory.TrimEnd('\\');
        try { File.WriteAllText(Path.Combine(appDir, ".write-test"), ""); File.Delete(Path.Combine(appDir, ".write-test")); }
        catch { throw new InvalidOperationException($"can't write to {appDir}"); }

        var work = Path.Combine(Path.GetTempPath(), "anymeet-update-" + Guid.NewGuid().ToString("N"));
        Directory.CreateDirectory(work);
        var zip = Path.Combine(work, "AnyMeet.zip");
        await using (var stream = await http.GetStreamAsync($"https://github.com/{Repository}/releases/download/v{latest}/AnyMeet-Windows-x64.zip"))
        await using (var file = File.Create(zip))
            await stream.CopyToAsync(file);
        var staged = Path.Combine(work, "app");
        ZipFile.ExtractToDirectory(zip, staged);
        if (!File.Exists(Path.Combine(staged, "AnyMeet.exe"))) throw new InvalidOperationException("the download is not AnyMeet");

        var script = Path.Combine(work, "update.ps1");
        File.WriteAllText(script, $$"""
            Wait-Process -Id {{Environment.ProcessId}} -ErrorAction SilentlyContinue
            Copy-Item -Path '{{staged}}\*' -Destination '{{appDir}}' -Recurse -Force
            Start-Process '{{Path.Combine(appDir, "AnyMeet.exe")}}'
            Remove-Item -Path '{{work}}' -Recurse -Force -ErrorAction SilentlyContinue
            """);
        Process.Start(new ProcessStartInfo("powershell.exe", $"-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File \"{script}\"")
        {
            CreateNoWindow = true,
            UseShellExecute = false,
        });
    }
}
