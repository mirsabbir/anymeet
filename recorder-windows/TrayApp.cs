using System.Drawing.Drawing2D;

namespace MeetingRecorder;

/// <summary>
/// Notification-area (system tray) app. Left click starts or stops a recording;
/// right click opens the menu. After a recording stops, the transcriber runs on
/// the meeting folder unless turned off in Settings. Same behavior as macOS.
/// </summary>
sealed class TrayApp : ApplicationContext
{
    enum State { Idle, Starting, Recording, Stopping }

    readonly NotifyIcon tray = new() { Visible = true };
    readonly MeetingCapture capture = new();
    readonly System.Windows.Forms.Timer ticker = new() { Interval = 1000 };
    readonly Icon idleIcon = Dot(Color.FromArgb(0x16, 0x18, 0x1D), ring: true);
    readonly Icon recordingIcon = Dot(Color.FromArgb(0xE5, 0x48, 0x4D), ring: false);
    readonly Icon busyIcon = Dot(Color.FromArgb(0x43, 0x38, 0xCA), ring: true);
    State state_ = State.Idle;
    DateTime startedAt;
    string? folder;
    string lastMessage_ = "Ready";

    /// <summary>Set at startup so the Meetings window can start and stop recordings too.</summary>
    public static TrayApp? Current { get; private set; }

    // Every change is pushed to open windows, which show the record bar.
    State state { get => state_; set { state_ = value; WebWindow.Emit("recordingChanged"); } }
    string lastMessage { get => lastMessage_; set { lastMessage_ = value; WebWindow.Emit("recordingChanged"); } }

    /// <summary>The Recording shape in ui/src/types.ts.</summary>
    public object Recording => new
    {
        state = state.ToString().ToLowerInvariant(),
        startedAt = state == State.Recording ? startedAt.ToUniversalTime().ToString("o") : null,
        message = lastMessage,
    };

    public TrayApp()
    {
        Current = this;
        // Created first so SynchronizationContext.Current is the UI thread's for the runner.
        tray.MouseClick += (_, e) => { if (e.Button == MouseButtons.Left) Toggle(); };
        tray.ContextMenuStrip = new ContextMenuStrip();
        tray.ContextMenuStrip.Opening += (_, _) => BuildMenu();
        ticker.Tick += (_, _) => Render();
        TranscriptionRunner.MeetingsChanged += () => { Render(); WebWindow.Emit("meetingsChanged"); };
        Render();
    }

    async void Toggle()
    {
        if (state == State.Idle) await StartAsync();
        else if (state == State.Recording) await StopAsync(Preferences.AutoTranscribe);
    }

    public async Task StartAsync()
    {
        if (state != State.Idle) return;  // the tray and the window can both ask
        folder = Path.Combine(Preferences.MeetingsRoot, DateTime.Now.ToString("yyyy-MM-dd_HHmm"));
        state = State.Starting;
        Render();
        try
        {
            await Task.Run(() => capture.Start(folder));
            startedAt = DateTime.Now;
            state = State.Recording;
            lastMessage = "Recording";
            ticker.Start();
        }
        catch (Exception e)
        {
            await capture.StopAsync();
            state = State.Idle;
            lastMessage = $"⚠️ Couldn't start: {e.Message}";
            tray.ShowBalloonTip(5000, "Couldn't start recording", e.Message, ToolTipIcon.Error);
        }
        Render();
    }

    public async Task StopAsync(bool transcribe)
    {
        if (state != State.Recording) return;
        state = State.Stopping;
        ticker.Stop();
        Render();
        var problems = await capture.StopAsync();
        state = State.Idle;
        var name = Path.GetFileName(folder!);
        TranscriptionRunner.RaiseMeetingsChanged();
        if (problems.Count > 0)
        {
            lastMessage = "⚠️ " + string.Join("; ", problems);
            tray.ShowBalloonTip(8000, "Recording problem", lastMessage, ToolTipIcon.Warning);
        }
        else
        {
            lastMessage = $"Saved {name}";
        }
        if (transcribe)
        {
            var started = TranscriptionRunner.Start(folder!, ok =>
            {
                lastMessage = ok ? $"Transcript ready: {name}" : "⚠️ Transcription problem, see transcribe.log";
                tray.ShowBalloonTip(5000, ok ? "Transcript ready" : "Transcription problem", name, ok ? ToolTipIcon.Info : ToolTipIcon.Warning);
                Render();
            });
            lastMessage = started ? $"Transcribing {name}" : "⚠️ Couldn't start the transcriber (is uv installed?)";
        }
        Render();
    }

    void Render()
    {
        tray.Icon = state switch
        {
            State.Recording => recordingIcon,
            State.Idle when !TranscriptionRunner.AnyRunning => idleIcon,
            _ => busyIcon,
        };
        var text = state == State.Recording ? $"Recording {Clock(DateTime.Now - startedAt)} · click to stop" : $"Meeting Recorder · {lastMessage}";
        tray.Text = text.Length > 127 ? text[..127] : text;  // Windows limit
    }

    void BuildMenu()
    {
        var menu = tray.ContextMenuStrip!;
        menu.Items.Clear();
        menu.Items.Add(new ToolStripMenuItem(lastMessage) { Enabled = false });
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add(state == State.Recording ? "Stop Recording" : "Start Recording", null, (_, _) => Toggle());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Meetings…", null, (_, _) => WebWindow.ShowMeetings(folder != null ? Path.GetFileName(folder) : null));
        menu.Items.Add("Settings…", null, (_, _) => WebWindow.ShowSettings());
        menu.Items.Add(new ToolStripSeparator());
        menu.Items.Add("Quit Meeting Recorder", null, async (_, _) =>
        {
            if (state == State.Recording) await StopAsync(transcribe: false);
            tray.Visible = false;
            ExitThread();
        });
    }

    /// <summary>A 32×32 tray icon: a filled dot, or a ring with a dot (idle).</summary>
    static Icon Dot(Color color, bool ring)
    {
        using var bitmap = new Bitmap(32, 32);
        using (var g = Graphics.FromImage(bitmap))
        {
            g.SmoothingMode = SmoothingMode.AntiAlias;
            using var brush = new SolidBrush(color);
            if (ring)
            {
                using var pen = new Pen(color, 3);
                g.DrawEllipse(pen, 3, 3, 26, 26);
                g.FillEllipse(brush, 10, 10, 12, 12);
            }
            else
            {
                g.FillEllipse(brush, 2, 2, 28, 28);
                g.FillRectangle(Brushes.White, 11, 11, 10, 10);  // stop square
            }
        }
        return Icon.FromHandle(bitmap.GetHicon());
    }

    static string Clock(TimeSpan t) => t.TotalHours >= 1 ? $"{(int)t.TotalHours}:{t.Minutes:00}:{t.Seconds:00}" : $"{t.Minutes:00}:{t.Seconds:00}";

    protected override void Dispose(bool disposing)
    {
        if (disposing) { tray.Dispose(); ticker.Dispose(); }
        base.Dispose(disposing);
    }
}
