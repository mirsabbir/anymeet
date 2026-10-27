#!/bin/zsh
# Builds "AnyMeet.app" into build/. VERSION sets the version (default 0.0.0-dev).
set -euo pipefail
cd "${0:A:h}"

(cd ../ui && npm ci --silent && npm run build)
swift build -c release
APP="build/AnyMeet.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/ui"
cp .build/release/AnyMeet "$APP/Contents/MacOS/AnyMeet"
cp ../ui/dist/index.html "$APP/Contents/Resources/ui/index.html"

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleIdentifier</key><string>com.mirsabbir.anymeet</string>
    <key>CFBundleName</key><string>AnyMeet</string>
    <key>CFBundleExecutable</key><string>AnyMeet</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleShortVersionString</key><string>${VERSION:-0.0.0-dev}</string>
    <key>CFBundleVersion</key><string>${VERSION:-0.0.0-dev}</string>
    <key>LSMinimumSystemVersion</key><string>15.0</string>
    <key>LSUIElement</key><true/>
    <key>NSMicrophoneUsageDescription</key><string>Records your voice in meetings as its own track.</string>
    <key>TranscriberProject</key><string>${PWD:h}/transcriber</string>
</dict>
</plist>
PLIST

# SIGN_IDENTITY (+ SIGN_KEYCHAIN) set: sign with the project's self-signed certificate,
# so macOS keeps Screen Recording/Microphone permissions across updates.
# Unset: ad-hoc, and macOS asks for the permissions again after every build.
codesign --force --sign "${SIGN_IDENTITY:--}" ${SIGN_KEYCHAIN:+--keychain "$SIGN_KEYCHAIN"} \
    --identifier com.mirsabbir.anymeet --timestamp=none "$APP"
echo "Built $APP"
