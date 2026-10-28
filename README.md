# AnyMeet

AnyMeet is a bot-free meeting recorder for macOS and Windows. One click records every display, your microphone and the system audio (Zoom, Teams, Meet …) as separate files. The transcript grows while you talk, or is written when you stop, and handles **Bangla, English and mixed Bangla-English speech**.

- **No meeting bot.** Everything is captured on your own computer; recordings stay in `~/Meetings`.
- **Who said what.** Your microphone is "Me". Everyone else, in the system audio, can be told apart by voice as Speaker 1, Speaker 2… on your own computer, and renamed on the meeting page.
- **Your words, spelled right.** A personal glossary (names, products, jargon) keeps English terms in English letters.
- **Swappable transcription.** Gemini 3.5 Transcribe by default, or free Whisper models that run on your own computer (one for Bangla, one for English). Providers plug in through a small adapter.
- **Play it back in the app.** Click a time in the transcript to hear that moment from the speaker's own track; recordings play inside the app with the system's own decoders.

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
| Local Whisper (Bangla) | Your computer, offline | Free, 1.5 GB. Bangla only: English words come out in Bangla letters. On an M3, 1 minute of audio takes about 1 minute |
| Local Whisper (English) | Your computer, offline | Free, 1.6 GB (Whisper large-v3-turbo). English-only meetings; Bangla speech comes out translated or garbled |

Local models are downloaded from Hugging Face to `~/.cache/huggingface`. Picking one in Settings that isn't on the computer yet starts the download there, with a progress bar; the app also installs the extra Python packages it needs. From the command line: `uv run transcribe --models` lists what is downloaded, and `uv run --extra local transcribe --download local-whisper-english` fetches one.

No local model handles Bangla and English in the same sentence yet; use Gemini for mixed meetings.

**When to transcribe**

- **Live, while recording** (default): the apps also write `live/mic.wav` and `live/system.wav` as they record. The transcriber cuts them at pauses and sends each piece as soon as it ends, so `transcript.md` grows during the meeting and is finished a few seconds after you stop (longer with the local model if it falls behind).
- **After the meeting**: the whole recording is transcribed when you stop.
- **Off**: transcribe later from the meeting page.

Both modes work with every model. From the command line: `uv run transcribe <meeting-folder>` (add `--live` to follow a recording in progress).

**Telling speakers apart**

Settings → Transcription → Speakers. Turned on, the system audio ("Others") is split by voice into Speaker 1, Speaker 2… when the meeting ends, and every part of the transcript gets one speaker. It runs on your computer with [pyannote community-1](https://huggingface.co/pyannote/speaker-diarization-community-1) and works with every model above, since it only listens to voices. Click a name on the meeting page to rename it everywhere; giving two speakers the same name merges them.

- **One-time setup:** the model is free but gated. Sign in at Hugging Face, accept its terms, create a token of type "Read" and paste it into Settings. The model (33 MB) then downloads, and after that it works offline. pyannote's usage telemetry is switched off.
- **Live mode:** the transcript says "Others" during the meeting, because telling voices apart needs the whole recording. After Stop the system audio is split once; a part with one speaker is relabelled, and only parts where several people spoke are transcribed again.
- **If it can't run** (no model, an error), the transcript is still written, with "Others".
- **Command line:** `uv run --extra speakers transcribe <meeting-folder> --speakers`, and `uv run transcribe <meeting-folder> --rename-speaker "Speaker 1" "Rahim"`.

**GPU or processor**

Settings → Transcription → Run local models on: the graphics card or the processor. It applies to the local Whisper models and to telling speakers apart. Measured on a MacBook Air M3 (8 cores, 16 GB) with a 10-minute, four-voice English test clip, per hour of meeting:

| Job | On the GPU | On the processor |
|---|---|---|
| Telling speakers apart | ~6 min, ~2 min of processor time, 0.8 GB | ~40 min with ~3.5 cores busy, 3.8 GB |
| Local Whisper (English) | ~21 min, ~3 min of processor time | slower; not measured |

On the GPU your processor stays mostly free for other work. The test clip's 4 voices were all found, with a diarization error rate of 7.9%; real meetings with similar voices or crosstalk will do worse.

- **macOS:** the Apple GPU (through Metal), the default.
- **Windows:** NVIDIA cards only; the app finds them with `nvidia-smi`. Choosing the GPU installs PyTorch built for CUDA once (2.6 GB, needs a recent NVIDIA driver) and checks that the card works (`transcribe --device`). Other cards, and PCs without one, use the processor, which is also the default.

## Settings

Nothing personal lives in the code. Each user's settings folder holds:

- `.env`: the API key, e.g. `GEMINI_API_KEY=…`, and the Hugging Face token for the speaker model, `HF_TOKEN=…`
- `glossary.txt`: one term per line

| OS | Folder |
|---|---|
| macOS | `~/Library/Application Support/AnyMeet/` |
| Windows | `%APPDATA%\AnyMeet\` |
| Linux (CLI only) | `~/.config/anymeet/` |

Both can be edited from the app: Settings → Transcription and Settings → Glossary.

## Tests

```bash
cd transcriber && uv run pytest                       # transcriber: audio splitting, pipeline, CLI, live mode, speakers
recorder-macos/build.sh && recorder-macos/build/AnyMeet.app/Contents/MacOS/AnyMeet --self-test /tmp/anymeet-test.json --fake-capture
```

The macOS self-test drives the real app: it records (with `--fake-capture`, a spoken test clip instead of the screen), stops from the menu-bar item, runs the transcriber after the meeting and live (the transcript must appear while still recording), checks the menu-bar icon at every step, pastes into the API key field and copies to the clipboard, then writes a pass/fail report. Without `--fake-capture`, start it with `open -a AnyMeet --args --self-test <report.json>` so the real screen capture runs under AnyMeet's own permission. It also plays a track from the transcript inside the app, and stops the way the system's ⏹ in the menu bar does (that button stops only the screen video, so AnyMeet has to notice and stop the rest). Last, it checks the GPU/processor choice and the speakers switch in Settings, and renames a speaker on a meeting page by clicking it and typing, which must rewrite the transcript files.

`--speakers-test` records a WAV of several voices as the system audio (`--fake-system`, 16 kHz mono) with "Tell speakers apart" on, transcribes it after the meeting and live, and checks that "Others" comes out as Speaker 1, Speaker 2…. It needs the speaker model downloaded:

```bash
recorder-macos/build/AnyMeet.app/Contents/MacOS/AnyMeet --self-test /tmp/anymeet-speakers.json --fake-capture --speakers-test --fake-system voices.wav
```

`--download-test <model>` checks a local model download through Settings instead: the card says it isn't downloaded, clicking it shows progress up to the end, and the transcriber finds every file. Give it an empty `HF_HOME` so it really downloads and leaves your model cache alone:

```bash
HF_HOME=$(mktemp -d) recorder-macos/build/AnyMeet.app/Contents/MacOS/AnyMeet --self-test /tmp/anymeet-download.json --download-test local-whisper-english
```

GitHub Actions runs the transcriber tests and builds on every push.

## License

[MIT](LICENSE)
