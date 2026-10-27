# বাংলা মিটিং রেকর্ডার

বট ছাড়া মিটিং রেকর্ডার। এক ক্লিকে সব ডিসপ্লে, মাইক আর সিস্টেম অডিও রেকর্ড হয়। রেকর্ড শেষে বাংলা, ইংরেজি আর মেশানো কথার ট্রান্সক্রিপ্ট তৈরি হয়।

| ফোল্ডার | কী আছে |
|---|---|
| [DESIGN.md](DESIGN.md) | পুরো ডিজাইন আর সিদ্ধান্তগুলো |
| [recorder-macos/](recorder-macos/) | macOS মেনু বার অ্যাপ (Swift, ScreenCaptureKit)। build করতে `./build.sh` |
| [ui/](ui/) | Meetings আর Settings উইন্ডো (React)। Mac আর Windows দুটোতেই একই UI। `npm run dev` চালালে নমুনা ডেটা দিয়ে চলে |
| [transcriber/](transcriber/) | অডিও থেকে ট্রান্সক্রিপ্ট। মডেল বদলানো যায় অ্যাডাপ্টার দিয়ে, ডিফল্ট Gemini 3.5 Transcribe |
| [model/](model/README.md) | নিজেদের ওপেন বাংলা-ইংরেজি মডেলের পরিকল্পনা, আর মানুষ কীভাবে অবদান রাখবে |
| [benchmark/](benchmark/README.md) | নিজের অডিওতে বিভিন্ন STT সার্ভিসের তুলনা |

## Release থেকে install (macOS)

`v*` tag push করলে GitHub Actions Mac-এর DMG আর Windows-এর zip বানিয়ে [Releases](https://github.com/mirsabbir/bangla-meeting-recorder/releases)-এ তোলে। Windows-এর নিয়ম: [recorder-windows/README.md](recorder-windows/README.md)।

```bash
cd ~/Downloads && curl -LO https://github.com/mirsabbir/bangla-meeting-recorder/releases/latest/download/MeetingRecorder-macOS.dmg
hdiutil attach MeetingRecorder-macOS.dmg
cp -R "/Volumes/Meeting Recorder/Meeting Recorder.app" /Applications/
hdiutil detach "/Volumes/Meeting Recorder"
xattr -dr com.apple.quarantine "/Applications/Meeting Recorder.app"   # Apple Developer ID নেই, তাই Gatekeeper-কে জানানো
defaults write com.mirsabbir.meetingrecorder transcriberProject <repo-path>/transcriber
open "/Applications/Meeting Recorder.app"
```

transcriber চালাতে `uv` আর `ffmpeg` লাগে (`brew install uv ffmpeg`)। প্রথমবার রেকর্ড করার সময় Screen Recording আর মাইকের অনুমতি দিতে হয়।
