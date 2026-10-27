# AnyMeet

AnyMeet is a bot-free meeting recorder for macOS and Windows. One click records every display, your microphone and the system audio (Zoom, Teams, Meet …) as separate files. The transcript grows while you talk, or is written when you stop, and handles **Bangla, English and mixed Bangla-English speech**.

- **No meeting bot.** Everything is captured on your own computer; recordings stay in `~/Meetings`.
- **Speakers from tracks.** Your microphone is labelled "Me", system audio "Others".
- **Your words, spelled right.** A personal glossary (names, products, jargon) keeps English terms in English letters.
- **Swappable transcription.** Gemini 3.5 Transcribe by default, or a free Bangla-tuned Whisper model that runs on your own Mac. Providers plug in through a small adapter.

| Folder | What's inside |
|---|---|
| [recorder-macos/](recorder-macos/) | macOS menu-bar app (Swift, ScreenCaptureKit). Build with `./build.sh` |
| [recorder-windows/](recorder-windows/README.md) | Windows tray app (C# .NET 8, ScreenRecorderLib, NAudio, WebView2) |
| [ui/](ui/) | Meetings and Settings windows (React), shared by both apps. `npm run dev` runs it with sample data |
| [transcriber/](transcriber/) | Audio → transcript, with one adapter per provider |
| [benchmark/](benchmark/README.md) | Compare speech-to-text providers on your own audio |
| [model/](model/README.md) | Plan for an open Bangla-English speech model, and how to contribute |
| [assets/icon/](assets/icon/) | App icon: SVG masters, `make-icons.sh` builds the macOS `.icns` and Windows `.ico` |
| [DESIGN.md](DESIGN.md) | Architecture and decisions (in Bangla) |

## Install on macOS

Every `v*` tag builds a macOS DMG and a Windows zip with GitHub Actions and publishes them under [Releases](https://github.com/mirsabbir/anymeet/releases). The app is not signed with an Apple Developer ID yet, so macOS has to be told to trust it (the `xattr` line).

```bash
cd ~/Downloads && curl -LO https://github.com/mirsabbir/anymeet/releases/latest/download/AnyMeet-macOS.dmg
hdiutil attach AnyMeet-macOS.dmg
cp -R "/Volumes/AnyMeet/AnyMeet.app" /Applications/
hdiutil detach "/Volumes/AnyMeet"
xattr -dr com.apple.quarantine "/Applications/AnyMeet.app"
defaults write com.mirsabbir.anymeet transcriberProject <repo-path>/transcriber
open "/Applications/AnyMeet.app"
```

Transcription needs a clone of this repository (the `transcriberProject` path above) plus `uv` and `ffmpeg` (`brew install uv ffmpeg`). On the first recording macOS asks for Screen Recording and Microphone access. Because the app is ad-hoc signed, it asks again after each update.

Windows: see [recorder-windows/README.md](recorder-windows/README.md).

## Updates

From v0.3.2 on, AnyMeet checks GitHub Releases when it opens and every six hours. When a new version is out, the Meetings window shows **Update & restart**: it downloads the release, replaces the app and reopens it. Settings → About & updates shows your version and has **Check now**. Updating is blocked while a recording or a transcription is running.

## "The app won't open"

AnyMeet is not signed with a paid Apple Developer ID or a Windows code-signing certificate yet, so both systems warn about it the first time. The app is safe to open; the source is all here.

**macOS: "AnyMeet is damaged and can't be opened" or "Apple could not verify AnyMeet"**

Remove the download quarantine flag, then open it again:

```bash
xattr -dr com.apple.quarantine /Applications/AnyMeet.app
```

Or without Terminal: try to open the app once, then go to **System Settings → Privacy & Security**, scroll down to the message about AnyMeet and click **Open Anyway**.

**macOS: it asks for Screen Recording / Microphone again after an update**

Expected for now: an unsigned app looks like a new app to macOS after every update. Allow it again, then quit and reopen AnyMeet. Old entries for AnyMeet in the Screen Recording list can be removed with **−**.

**Windows: "Windows protected your PC" (SmartScreen)**

Click **More info → Run anyway**. If Windows blocks the zip's files, right-click the downloaded zip → **Properties** → tick **Unblock** → OK, then unzip again.

## Transcription

Settings → Transcription picks the model and when it runs.

| Model | Where it runs | Notes |
|---|---|---|
| Gemini 3.5 Transcribe | Google's API | Needs a Gemini API key (prepaid credits); about $0.35 per hour of audio |
| Local Whisper (Bangla) | This Mac, offline | Free. The first use downloads about 1.5 GB to `~/.cache/huggingface`. On an M3, 1 minute of audio takes about 1 minute. English words come out in Bangla letters |

To install the local model's packages once: `cd transcriber && uv sync --extra local`. The app passes `--extra local` itself when this model is selected.

**When to transcribe**

- **Live, while recording** (default): the apps also write `live/mic.wav` and `live/system.wav` as they record. The transcriber cuts them at pauses and sends each piece as soon as it ends, so `transcript.md` grows during the meeting and is finished a few seconds after you stop (longer with the local model if it falls behind).
- **After the meeting**: the whole recording is transcribed when you stop.
- **Off**: transcribe later from the meeting page.

Both modes work with every model. From the command line: `uv run transcribe <meeting-folder>` (add `--live` to follow a recording in progress).

## Settings

Nothing personal lives in the code. Each user's settings folder holds:

- `.env`: the API key, e.g. `GEMINI_API_KEY=…`
- `glossary.txt`: one term per line

| OS | Folder |
|---|---|
| macOS | `~/Library/Application Support/AnyMeet/` |
| Windows | `%APPDATA%\AnyMeet\` |
| Linux (CLI only) | `~/.config/anymeet/` |

Both can be edited from the app: Settings → Transcription and Settings → Glossary.

## Tests

```bash
cd transcriber && uv run pytest                       # transcriber: audio splitting, pipeline, CLI, live mode
recorder-macos/build.sh && recorder-macos/build/AnyMeet.app/Contents/MacOS/AnyMeet --self-test /tmp/anymeet-test.json --fake-capture
```

The macOS self-test drives the real app: it records (with `--fake-capture`, a spoken test clip instead of the screen), stops from the menu-bar item, runs the transcriber after the meeting and live (the transcript must appear while still recording), checks the menu-bar icon at every step, pastes into the API key field and copies to the clipboard, then writes a pass/fail report. Without `--fake-capture`, start it with `open -a AnyMeet --args --self-test <report.json>` so the real screen capture runs under AnyMeet's own permission. GitHub Actions runs the transcriber tests and builds on every push.

## License

[MIT](LICENSE)
