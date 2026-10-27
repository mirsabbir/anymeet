// swift-tools-version:5.10
import PackageDescription

let package = Package(
    name: "MeetingRecorder",
    platforms: [.macOS("15.0")],  // SCRecordingOutput and microphone capture in ScreenCaptureKit
    targets: [.executableTarget(name: "MeetingRecorder", path: "Sources")]
)
