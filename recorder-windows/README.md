# Meeting Recorder: Windows

Mac অ্যাপের মতোই কাজ করে। টাস্কবারের notification area-তে একটা আইকন থাকে।

- **বাম ক্লিক:** রেকর্ড শুরু বা বন্ধ।
- **ডান ক্লিক:** মেনু, যেখানে Meetings… আর Settings… আছে।
- **কী রেকর্ড হয়:** প্রতিটি ডিসপ্লে আলাদা `display-N.mp4`-এ, মাইক `mic.m4a`-এ, সিস্টেম অডিও `system.m4a`-এ।
- **কোথায় যায়:** `%USERPROFILE%\Meetings\<তারিখ_সময়>\`-এ।
- **UI:** Mac-এর একই React UI ([../ui](../ui)), এখানে WebView2-তে চলে।

| অংশ | লাইব্রেরি |
|---|---|
| ডিসপ্লে | [ScreenRecorderLib](https://github.com/sskodje/ScreenRecorderLib) 6.6.0: প্রতিটি ডিসপ্লের জন্য আলাদা recorder, H.264, ১৫ fps |
| মাইক আর সিস্টেম অডিও | NAudio 2.2.1 WASAPI (সিস্টেম অডিওর জন্য loopback), শেষে AAC-তে রূপান্তর |
| UI | WebView2, `window.chrome.webview` bridge |

সিস্টেম অডিও শুধু তখনই ডেটা দেয় যখন কোনো অ্যাপ শব্দ বাজায়। তাই ফাঁকা সময়গুলো নীরবতা দিয়ে ভরাট করা হয়। এতে mic আর system দুই ট্র্যাক একই ঘড়ি ধরে চলে, আর ট্রান্সক্রিপ্টে "Me" ও "Others"-এর কথা সঠিক ক্রমে সাজানো যায়।

## যা লাগবে (একবার)

```powershell
winget install Microsoft.DotNet.DesktopRuntime.8 Microsoft.VCRedist.2015+.x64 astral-sh.uv Gyan.FFmpeg
```

WebView2 Windows 11-এ আগে থেকেই থাকে।

## চালানো

- **নিজে build করে:** repo clone করো, তারপর:
  ```powershell
  cd ui; npm ci; npm run build; cd ..\recorder-windows; dotnet run -c Release
  ```
- **build না করে:** GitHub-এর **Actions → Windows app** থেকে সর্বশেষ সফল run-এর `MeetingRecorder-windows-x64` zip নামাও।

transcriber কোথায় আছে, অ্যাপ তা এই ক্রমে খোঁজে:

1. `MEETING_RECORDER_TRANSCRIBER` environment variable
2. build-এর সময় যে path ঢোকানো হয়েছিল
3. exe-র উপরের কোনো ফোল্ডারে থাকা `transcriber\`

zip থেকে চালালে প্রথমটা সেট করে দাও:

```powershell
setx MEETING_RECORDER_TRANSCRIBER "C:\path\to\bangla-meeting-recorder\transcriber"
```

## অবস্থা

এই অ্যাপ macOS-এ compile হয় আর GitHub Actions-এ Windows-এ build হয়। কিন্তু **এখনো কোনো Windows কম্পিউটারে চালিয়ে পরীক্ষা করা হয়নি।** প্রথমবার চালানোর সময় এগুলো দেখতে হবে:

- সব ডিসপ্লের ভিডিও তৈরি হচ্ছে কিনা
- দুই অডিও ট্র্যাকে শব্দ আছে কিনা
- Meetings আর Settings উইন্ডো খুলছে কিনা
