import AppKit

/// Updates from GitHub Releases. Compares this build's version with the latest
/// release, downloads AnyMeet-macOS.dmg, and after the app quits swaps the
/// bundle and reopens it. The repository comes from Info.plist (UpdateRepository,
/// set by build.sh); development builds never update themselves.
enum Updater {
    static let repository = (Bundle.main.object(forInfoDictionaryKey: "UpdateRepository") as? String).flatMap { $0.isEmpty ? nil : $0 }
    static let current = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0.0.0-dev"
    private static var cached: (at: Date, info: [String: Any])?

    /// The UpdateInfo shape in ui/src/types.ts.
    static func check(force: Bool) async -> [String: Any] {
        if !force, let cached, Date().timeIntervalSince(cached.at) < 3600 { return cached.info }
        var info: [String: Any] = ["current": current, "available": false]
        guard let repository, !current.contains("dev") else {
            info["error"] = "this is a development build"
            return info
        }
        do {
            var request = URLRequest(url: URL(string: "https://api.github.com/repos/\(repository)/releases/latest")!)
            request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let tag = (try JSONSerialization.jsonObject(with: data) as? [String: Any])?["tag_name"] as? String
            else { throw UpdateError("no release found") }
            let latest = tag.hasPrefix("v") ? String(tag.dropFirst()) : tag
            info["latest"] = latest
            info["available"] = isNewer(latest, than: current)
            cached = (Date(), info)
        } catch {
            info["error"] = error.localizedDescription
        }
        return info
    }

    static func isNewer(_ a: String, than b: String) -> Bool {
        let parse = { (v: String) in v.split(separator: ".").map { Int($0.prefix { $0.isNumber }) ?? 0 } }
        let (x, y) = (parse(a), parse(b))
        for i in 0..<max(x.count, y.count) {
            let (p, q) = (i < x.count ? x[i] : 0, i < y.count ? y[i] : 0)
            if p != q { return p > q }
        }
        return false
    }

    /// Downloads and stages the update, then starts a helper that waits for this
    /// process to exit, replaces the app and reopens it. The caller quits the app.
    static func install() async throws {
        let info = await check(force: true)
        guard let repository, let latest = info["latest"] as? String, info["available"] as? Bool == true
        else { throw UpdateError(info["error"] as? String ?? "no newer version available") }
        let target = Bundle.main.bundleURL
        guard FileManager.default.isWritableFile(atPath: target.deletingLastPathComponent().path)
        else { throw UpdateError("can't write to \(target.deletingLastPathComponent().path)") }

        let work = FileManager.default.temporaryDirectory.appending(path: "anymeet-update-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        let url = URL(string: "https://github.com/\(repository)/releases/download/v\(latest)/AnyMeet-macOS.dmg")!
        let (download, response) = try await URLSession.shared.download(from: url)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else { throw UpdateError("download failed") }
        let dmg = work.appending(path: "AnyMeet.dmg")
        try FileManager.default.moveItem(at: download, to: dmg)

        let mount = work.appending(path: "mnt")
        try run("/usr/bin/hdiutil", "attach", "-nobrowse", "-readonly", "-mountpoint", mount.path, dmg.path)
        let staged = work.appending(path: "AnyMeet.app")
        let copied = Result { try run("/usr/bin/ditto", mount.appending(path: "AnyMeet.app").path, staged.path) }
        try? run("/usr/bin/hdiutil", "detach", mount.path, "-quiet")
        try copied.get()
        guard Bundle(url: staged)?.bundleIdentifier == Bundle.main.bundleIdentifier
        else { throw UpdateError("the download is not AnyMeet") }

        let helper = Process()
        helper.executableURL = URL(filePath: "/bin/zsh")
        helper.arguments = ["-c", """
            while kill -0 \(ProcessInfo.processInfo.processIdentifier) 2>/dev/null; do sleep 0.2; done
            rm -rf "$2" && ditto "$1" "$2" && xattr -dr com.apple.quarantine "$2"
            open "$2"; rm -rf "$3"
            """, "zsh", staged.path, target.path, work.path]
        try helper.run()
    }

    private static func run(_ tool: String, _ args: String...) throws {
        let p = Process()
        p.executableURL = URL(filePath: tool)
        p.arguments = args
        p.standardOutput = FileHandle.nullDevice
        p.standardError = FileHandle.nullDevice
        try p.run()
        p.waitUntilExit()
        guard p.terminationStatus == 0 else { throw UpdateError("\((tool as NSString).lastPathComponent) failed") }
    }
}

struct UpdateError: LocalizedError {
    let errorDescription: String?
    init(_ message: String) { errorDescription = message }
}
