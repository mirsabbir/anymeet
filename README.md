# Kothalipi

*Kothalipi* (কথালিপি, "speech written down") is a bot-free meeting recorder for macOS and Windows. One click records every display, your microphone and the system audio (Zoom, Teams, Meet …) as separate files. When you stop, it writes a transcript that handles **Bangla, English and mixed Bangla-English speech**.

- **No meeting bot.** Everything is captured on your own computer; recordings stay in `~/Meetings`.
- **Speakers from tracks.** Your microphone is labelled "Me", system audio "Others".
- **Your words, spelled right.** A personal glossary (names, products, jargon) keeps English terms in English letters.
- **Swappable transcription.** Gemini 3.5 Transcribe by default; providers plug in through a small adapter.

| Folder | What's inside |
|---|---|
| [recorder-macos/](recorder-macos/) | macOS menu-bar app (Swift, ScreenCaptureKit). Build with `./build.sh` |
| [recorder-windows/](recorder-windows/README.md) | Windows tray app (C# .NET 8, ScreenRecorderLib, NAudio, WebView2) |
| [ui/](ui/) | Meetings and Settings windows (React), shared by both apps. `npm run dev` runs it with sample data |
| [transcriber/](transcriber/) | Audio → transcript, with one adapter per provider |
| [benchmark/](benchmark/README.md) | Compare speech-to-text providers on your own audio |
| [model/](model/README.md) | Plan for an open Bangla-English speech model, and how to contribute |
| [DESIGN.md](DESIGN.md) | Architecture and decisions (in Bangla) |

## Install on macOS

Every `v*` tag builds a macOS DMG and a Windows zip with GitHub Actions and publishes them under [Releases](https://github.com/mirsabbir/kothalipi/releases). The app is not signed with an Apple Developer ID yet, so macOS has to be told to trust it (the `xattr` line).

```bash
cd ~/Downloads && curl -LO https://github.com/mirsabbir/kothalipi/releases/latest/download/Kothalipi-macOS.dmg
hdiutil attach Kothalipi-macOS.dmg
cp -R "/Volumes/Kothalipi/Kothalipi.app" /Applications/
hdiutil detach "/Volumes/Kothalipi"
xattr -dr com.apple.quarantine "/Applications/Kothalipi.app"
defaults write com.mirsabbir.kothalipi transcriberProject <repo-path>/transcriber
open "/Applications/Kothalipi.app"
```

Transcription needs a clone of this repository (the `transcriberProject` path above) plus `uv` and `ffmpeg` (`brew install uv ffmpeg`). On the first recording macOS asks for Screen Recording and Microphone access. Because the app is ad-hoc signed, it asks again after each update.

Windows: see [recorder-windows/README.md](recorder-windows/README.md).

## Settings

Nothing personal lives in the code. Each user's settings folder holds:

- `.env`: the API key, e.g. `GEMINI_API_KEY=…`
- `glossary.txt`: one term per line

| OS | Folder |
|---|---|
| macOS | `~/Library/Application Support/Kothalipi/` |
| Windows | `%APPDATA%\Kothalipi\` |
| Linux (CLI only) | `~/.config/kothalipi/` |

Both can be edited from the app: Settings → Transcription and Settings → Glossary.

## License

[MIT](LICENSE)
