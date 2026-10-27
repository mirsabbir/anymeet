# AnyMeet: Windows

Works like the macOS app, from an icon in the taskbar's notification area.

- **Left click:** start or stop recording.
- **Right click:** menu with Meetings… and Settings….
- **What it records:** each display to its own `display-N.mp4`, the microphone to `mic.m4a`, system audio to `system.m4a`.
- **Where:** `%USERPROFILE%\Meetings\<date_time>\`.
- **UI:** the same React UI as macOS ([../ui](../ui)), here in WebView2.

| Part | Library |
|---|---|
| Displays | [ScreenRecorderLib](https://github.com/sskodje/ScreenRecorderLib) 6.6.0: one recorder per display, H.264, 15 fps |
| Microphone and system audio | NAudio 2.2.1 WASAPI (loopback for system audio), encoded to AAC at the end |
| UI | WebView2 with a `window.chrome.webview` bridge |

System audio only delivers data while some app is playing sound, so silent stretches are filled with silence. That keeps the mic and system tracks on the same clock, so "Me" and "Others" come out in the right order in the transcript.

## Requirements (once)

```powershell
winget install Microsoft.DotNet.DesktopRuntime.8 Microsoft.VCRedist.2015+.x64 astral-sh.uv Gyan.FFmpeg
```

WebView2 ships with Windows 11.

## Run

- **From a release:** download [AnyMeet-Windows-x64.zip](https://github.com/mirsabbir/anymeet/releases/latest/download/AnyMeet-Windows-x64.zip), unzip, run `AnyMeet.exe`. The app isn't code-signed yet, so SmartScreen asks: choose "More info → Run anyway".
- **From source:** clone the repo, then
  ```powershell
  cd ui; npm ci; npm run build; cd ..\recorder-windows; dotnet run -c Release
  ```

The app looks for the transcriber (a clone of this repo's `transcriber\` folder) in this order:

1. the `ANYMEET_TRANSCRIBER` environment variable
2. the path baked in at build time
3. a `transcriber\` folder above the exe

When running from the zip, set the first one:

```powershell
setx ANYMEET_TRANSCRIBER "C:\path\to\anymeet\transcriber"
```

## Status

The app compiles on macOS and builds on Windows in GitHub Actions, but **it has not been run on a Windows computer yet.** On a first run, check that:

- every display produces a video
- both audio tracks contain sound
- the Meetings and Settings windows open
