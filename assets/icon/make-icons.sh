#!/bin/zsh
# Rebuilds the app icons from the SVGs (run on macOS after editing them):
#   AppIcon.icns  macOS app bundle, from anymeet-macos.svg (Apple's 824/1024 layout)
#   anymeet.ico   Windows exe and windows, from anymeet.svg (full bleed)
#   png/          the rendered sizes, for anything else
set -euo pipefail
cd "${0:A:h}"
rm -rf png AppIcon.iconset && mkdir -p png AppIcon.iconset

for s in 16 32 64 128 256 512 1024; do
    swift render.swift anymeet-macos.svg $s png/macos-$s.png
    swift render.swift anymeet.svg $s png/anymeet-$s.png
done
for s in 16 32 128 256 512; do
    cp png/macos-$s.png AppIcon.iconset/icon_${s}x${s}.png
    cp png/macos-$((s * 2)).png AppIcon.iconset/icon_${s}x${s}@2x.png
done
iconutil -c icns AppIcon.iconset -o AppIcon.icns
rm -rf AppIcon.iconset
swift render.swift anymeet.svg 24 png/anymeet-24.png
swift render.swift anymeet.svg 48 png/anymeet-48.png

# ICO with PNG-compressed entries (Windows Vista and later).
python3 - <<'PY'
import struct
sizes = [16, 24, 32, 48, 64, 128, 256]
images = [open(f"png/anymeet-{s}.png", "rb").read() for s in sizes]
offset = 6 + 16 * len(sizes)
header = struct.pack("<HHH", 0, 1, len(sizes))
entries = b""
for s, data in zip(sizes, images):
    entries += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
    offset += len(data)
open("anymeet.ico", "wb").write(header + entries + b"".join(images))
PY
echo "AppIcon.icns, anymeet.ico and png/ rebuilt"
