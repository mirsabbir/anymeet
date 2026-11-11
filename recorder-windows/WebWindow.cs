using System.Diagnostics;
using System.Text.Json;
using System.Text.Json.Nodes;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using NAudio.CoreAudioApi;

namespace AnyMeet;

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

    public static void ShowMeetings(string? id = null) => Show("meetings", id, "AnyMeet", new Size(1180, 760));
    public static void ShowSettings() => Show("settings", null, "AnyMeet Settings", new Size(860, 600));

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
        Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath);
        Controls.Add(web);
    }

    async Task LoadAsync(string? id)
    {
        if (web.CoreWebView2 == null)
        {
            var data = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "AnyMeet", "WebView2");
            await web.EnsureCoreWebView2Async(await CoreWebView2Environment.CreateAsync(null, data));
            web.CoreWebView2!.WebMessageReceived += OnMessage;
            // Recordings play inside the app at https://media.anymeet/<meeting id>/<file>, decoded by Windows.
            web.CoreWebView2.SetVirtualHostNameToFolderMapping(MeetingStore.MediaHost, Preferences.MeetingsRoot, CoreWebView2HostResourceAccessKind.Allow);
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
        IEnumerable<string> Ids() => (p["ids"] as JsonArray ?? new()).Select(i => i?.GetValue<string>() ?? "");
        switch (method)
        {
            case "getRecording":
                return TrayApp.Current?.Recording;
            case "startRecording":
                if (TrayApp.Current != null) await TrayApp.Current.StartAsync();
                return null;
            case "stopRecording":
                if (TrayApp.Current != null) await TrayApp.Current.StopAsync();
                return null;
            case "checkForUpdate":
                return await Updater.Check(p["force"]?.GetValue<bool>() ?? false);
            case "installUpdate":
                if ((TrayApp.Current?.IsIdle ?? true) == false) throw new InvalidOperationException("stop the recording first");
                if (TranscriptionRunner.AnyRunning) throw new InvalidOperationException("wait until the transcript is finished");
                await Updater.Install();
                _ = Task.Delay(300).ContinueWith(_ => TrayApp.Current?.ExitThread(), TaskScheduler.FromCurrentSynchronizationContext());
                return null;
            case "deleteMeeting":
                var meeting = MeetingStore.Folder(Str("id")) ?? throw new InvalidOperationException("meeting not found");
                if (TranscriptionRunner.IsRunning(meeting)) throw new InvalidOperationException("wait until the transcript is finished");
                if (p["cloudToo"]?.GetValue<bool>() == true)  // the cloud copy first: if that fails, nothing is gone
                    await TranscriptionRunner.Output("anymeet", "forget", meeting);
                if (Directory.Exists(meeting))  // forget removes a folder that held only cloud records
                    // Recoverable from the Recycle Bin.
                    Microsoft.VisualBasic.FileIO.FileSystem.DeleteDirectory(meeting,
                        Microsoft.VisualBasic.FileIO.UIOption.OnlyErrorDialogs, Microsoft.VisualBasic.FileIO.RecycleOption.SendToRecycleBin);
                TranscriptionRunner.RaiseMeetingsChanged();
                return null;
            case "backup":
                CloudJobs.Run(["backup", .. CloudJobs.Idle(Ids())]);
                return null;
            case "freeUp":
                CloudJobs.Run(["free", .. CloudJobs.Idle(Ids())]);
                return null;
            case "restore":
                var restored = MeetingStore.Folder(Str("id")) ?? throw new InvalidOperationException("meeting not found");
                var files = (p["files"] as JsonArray ?? new()).Select(f => f?.GetValue<string>() ?? "").Where(f => f.Length > 0).ToArray();
                CloudJobs.Run(["restore", restored, .. files.Length > 0 ? ["--files", .. files] : Array.Empty<string>()]);
                return null;
            case "summarize":
                CloudJobs.Run("summarize", MeetingStore.Folder(Str("id")) ?? throw new InvalidOperationException("meeting not found"),
                              "--language", Preferences.SummaryLanguage);
                return null;
            case "stopJob":
                File.WriteAllText(Path.Combine(MeetingStore.Folder(Str("id")) ?? throw new InvalidOperationException("meeting not found"), "job.stop"), "");
                return null;
            case "checkCloud":
                return JsonDocument.Parse(await TranscriptionRunner.Output("anymeet", "check")).RootElement.Clone();
            case "setCloud":
                if (Str("url").Trim().Length > 0 && !(Uri.TryCreate(Str("url").Trim(), UriKind.Absolute, out var server) && server.Scheme == "https"))
                    throw new InvalidOperationException("the server address must start with https://");
                Preferences.SetCloud(Str("url").Trim(), Str("token").Trim());
                CloudJobs.AtLaunch();  // list what is already in the cloud
                return null;
            case "setAutoBackup":
                Preferences.AutoBackup = p["on"]?.GetValue<bool>() ?? false;
                return null;
            case "setAnthropicKey":
                Preferences.SetAnthropicKey(Str("key"));
                return null;
            case "setAutoSummary":
                Preferences.AutoSummary = p["on"]?.GetValue<bool>() ?? false;
                return null;
            case "setSummaryLanguage":
                Preferences.SummaryLanguage = Str("language") is "auto" or "en" or "bn" ? Str("language") : throw new InvalidOperationException("unknown language");
                return null;
            case "listMeetings":
                return await Task.Run(MeetingStore.List);
            case "getMeeting":
                return await MeetingStore.Get(Str("id"));
            case "transcribe":
                var folder = MeetingStore.Folder(Str("id")) ?? throw new InvalidOperationException("meeting not found");
                if (!TranscriptionRunner.Start(folder, onlyFailed: p["onlyFailed"]?.GetValue<bool>() ?? false))
                    throw new InvalidOperationException("transcription is already running, or uv is not installed");
                return null;
            case "stopTranscribing":
                TranscriptionRunner.Stop(MeetingStore.Folder(Str("id")) ?? throw new InvalidOperationException("meeting not found"));
                return null;
            case "openPath":
                Open(Str("path"));
                return null;
            case "getSettings":
                return Settings();
            case "setTranscribeMode":
                Preferences.TranscribeMode = Str("mode") is "live" or "after" or "off" ? Str("mode") : throw new InvalidOperationException("unknown mode");
                return null;
            case "setTranscriber":
                Preferences.Transcriber = Preferences.AvailableTranscribers.Contains(Str("id")) ? Str("id") : throw new InvalidOperationException("transcriber not available");
                return null;
            case "getModels":
                return await ModelDownloads.List(p["refresh"]?.GetValue<bool>() ?? false);
            case "downloadModel":
                var local = Str("id").StartsWith("local-") && Preferences.AvailableTranscribers.Contains(Str("id"));
                if (!local && Str("id") is not (ModelDownloads.SpeakerModel or ModelDownloads.GpuSupport)) throw new InvalidOperationException("not a local model");
                if (Str("id") == ModelDownloads.GpuSupport && Preferences.Processor != "gpu") throw new InvalidOperationException("choose the graphics card first");
                ModelDownloads.Start(Str("id"));
                return null;
            case "setApiKey":
                Preferences.SetApiKey(Str("key"));
                return null;
            case "setHfToken":
                Preferences.SetHfToken(Str("token"));
                return null;
            case "setSpeakers":
                Preferences.Speakers = p["on"]?.GetValue<bool>() ?? false;
                return null;
            case "setProcessor":
                if (Str("id") is not ("gpu" or "cpu") || (Str("id") == "gpu" && Preferences.NvidiaGpu == null)) throw new InvalidOperationException($"can't run models on {Str("id")}");
                Preferences.Processor = Str("id");
                return null;
            case "renameSpeaker":
                var renamed = MeetingStore.Folder(Str("id")) ?? throw new InvalidOperationException("meeting not found");
                if (TranscriptionRunner.IsRunning(renamed)) throw new InvalidOperationException("wait until the transcript is finished");
                await TranscriptionRunner.Output("transcribe", renamed, "--rename-speaker", Str("from"), Str("to"));
                TranscriptionRunner.RaiseMeetingsChanged();
                return null;
            case "openLink":
                if (!Uri.TryCreate(Str("url"), UriKind.Absolute, out var link) || link.Scheme != "https" || link.Host != "huggingface.co")
                    throw new InvalidOperationException("only huggingface.co links open from here");
                Process.Start(new ProcessStartInfo(link.AbsoluteUri) { UseShellExecute = true });
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
            transcribeMode = Preferences.TranscribeMode,
            transcriber = Preferences.Transcriber,
            transcribers = Preferences.Transcribers,
            apiKeySet = Preferences.ApiKeySet,
            speakers = Preferences.Speakers,
            hfTokenSet = Preferences.HfTokenSet,
            processor = Preferences.Processor,
            processors = Preferences.Processors,
            glossary = Preferences.Glossary,
            displays = Screen.AllScreens.Select(s => $"{s.DeviceName.TrimStart('\\', '.')} · {s.Bounds.Width}×{s.Bounds.Height}").ToList(),
            microphone,
            cloudUrl = Preferences.CloudUrl,
            cloudTokenSet = Preferences.CloudTokenSet,
            autoBackup = Preferences.AutoBackup,
            anthropicKeySet = Preferences.AnthropicKeySet,
            autoSummary = Preferences.AutoSummary,
            summaryLanguage = Preferences.SummaryLanguage,
        };
    }
}
