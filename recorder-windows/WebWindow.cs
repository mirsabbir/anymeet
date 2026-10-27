using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using NAudio.CoreAudioApi;

namespace Kothalipi;

/// <summary>
/// The Settings and Meetings windows: the shared React UI (ui/index.html next to
/// the exe) in WebView2. The UI sends {id, method, params} through
/// window.chrome.webview.postMessage and gets {id, result} or {id, error} back;
/// methods mirror HostApi in ui/src/types.ts and the macOS host.
/// </summary>
sealed class WebWindow : Form
{
    static readonly Dictionary<string, WebWindow> open = new();
    static readonly JsonSerializerOptions json = new() { PropertyNamingPolicy = JsonNamingPolicy.CamelCase };
    readonly WebView2 web = new() { Dock = DockStyle.Fill };
    readonly string view;

    public static void ShowMeetings(string? id = null) => Show("meetings", id, "Meetings", new Size(1180, 760));
    public static void ShowSettings() => Show("settings", null, "Settings", new Size(860, 600));

    static void Show(string view, string? id, string title, Size size)
    {
        if (!open.TryGetValue(view, out var window))
        {
            window = new WebWindow(view, title, size);
            open[view] = window;
            window.FormClosed += (_, _) => open.Remove(view);
            window.Show();
        }
        if (id != null || window.web.CoreWebView2 == null) _ = window.LoadAsync(id);
        window.Activate();
    }

    /// <summary>Tells every open window that something changed outside the UI.</summary>
    public static void Emit(string hostEvent)
    {
        foreach (var w in open.Values)
            w.web.CoreWebView2?.PostWebMessageAsJson(JsonSerializer.Serialize(new { @event = hostEvent }));
    }

    WebWindow(string view, string title, Size size)
    {
        this.view = view;
        Text = title;
        ClientSize = size;
        MinimumSize = new Size(560, 420);
        StartPosition = FormStartPosition.CenterScreen;
        Controls.Add(web);
    }

    async Task LoadAsync(string? id)
    {
        if (web.CoreWebView2 == null)
        {
            var data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Kothalipi", "WebView2");
            await web.EnsureCoreWebView2Async(await CoreWebView2Environment.CreateAsync(null, data));
            web.CoreWebView2!.WebMessageReceived += OnMessage;
        }
        var index = Path.Combine(AppContext.BaseDirectory, "ui", "index.html");
        var fragment = "/" + view + (id != null ? "/" + Uri.EscapeDataString(id) : "");
        web.CoreWebView2.Navigate(new Uri(index).AbsoluteUri + "#" + fragment);
    }

    async void OnMessage(object? sender, CoreWebView2WebMessageReceivedEventArgs e)
    {
        var message = JsonNode.Parse(e.WebMessageAsJson);
        var id = message?["id"]?.GetValue<int>();
        object reply;
        try
        {
            var result = await Dispatch(message?["method"]?.GetValue<string>() ?? "", message?["params"] as JsonObject ?? new JsonObject());
            reply = new { id, result };
        }
        catch (Exception error)
        {
            reply = new { id, error = error.Message };
        }
        web.CoreWebView2?.PostWebMessageAsJson(JsonSerializer.Serialize(reply, json));
    }

    static async Task<object?> Dispatch(string method, JsonObject p)
    {
        string Str(string key) => p[key]?.GetValue<string>() ?? "";
        switch (method)
        {
            case "getRecording":
                return TrayApp.Current?.Recording;
            case "startRecording":
                if (TrayApp.Current != null) await TrayApp.Current.StartAsync();
                return null;
            case "stopRecording":
                if (TrayApp.Current != null) await TrayApp.Current.StopAsync(Preferences.AutoTranscribe);
                return null;
            case "listMeetings":
                return await Task.Run(MeetingStore.List);
            case "getMeeting":
                return await MeetingStore.Get(Str("id"));
            case "transcribe":
                var folder = MeetingStore.Folder(Str("id")) ?? throw new InvalidOperationException("meeting not found");
                if (!TranscriptionRunner.Start(folder)) throw new InvalidOperationException("transcription is already running, or uv is not installed");
                return null;
            case "openPath":
                Open(Str("path"));
                return null;
            case "getSettings":
                return Settings();
            case "setAutoTranscribe":
                Preferences.AutoTranscribe = p["value"]?.GetValue<bool>() ?? true;
                return null;
            case "setTranscriber":
                Preferences.Transcriber = Str("id") == "gemini-3.5-transcribe" ? Str("id") : throw new InvalidOperationException("transcriber not available");
                return null;
            case "setApiKey":
                Preferences.SetApiKey(Str("key"));
                return null;
            case "setGlossary":
                Preferences.Glossary = (p["terms"] as JsonArray ?? new()).Select(t => t?.GetValue<string>().Trim() ?? "").Where(t => t.Length > 0).ToList();
                return null;
            default:
                throw new InvalidOperationException($"unknown method {method}");
        }
    }

    /// <summary>Opens a file or folder, but only inside the meetings folder.</summary>
    static void Open(string path)
    {
        var full = Path.GetFullPath(path);
        var root = Path.GetFullPath(Preferences.MeetingsRoot);
        if (!full.StartsWith(root, StringComparison.OrdinalIgnoreCase)) throw new InvalidOperationException("path outside the meetings folder");
        Directory.CreateDirectory(root);
        Process.Start(new ProcessStartInfo(full) { UseShellExecute = true });
    }

    static object Settings()
    {
        string microphone = "";
        try { microphone = new MMDeviceEnumerator().GetDefaultAudioEndpoint(DataFlow.Capture, Role.Multimedia).FriendlyName; }
        catch { /* no microphone */ }
        return new
        {
            meetingsFolder = Preferences.MeetingsRoot,
            autoTranscribe = Preferences.AutoTranscribe,
            transcriber = Preferences.Transcriber,
            transcribers = Preferences.Transcribers,
            apiKeySet = Preferences.ApiKeySet,
            glossary = Preferences.Glossary,
            displays = Screen.AllScreens.Select(s => $"{s.DeviceName.TrimStart('\\', '.')} · {s.Bounds.Width}×{s.Bounds.Height}").ToList(),
            microphone,
        };
    }
}
