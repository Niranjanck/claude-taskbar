#!/bin/bash
# Builds "Claude Taskbar.app" and a shareable .dmg in ./dist
#
#   bash build-app.sh
#
# For a build that opens on other Macs without a security warning, sign and
# notarize it with an Apple Developer ID (needs an Apple Developer account):
#
#   DEVELOPER_ID="Developer ID Application: Your Company (TEAMID)" \
#   NOTARY_PROFILE="claude-taskbar" bash build-app.sh
#
# Create the notary profile once with:
#   xcrun notarytool store-credentials claude-taskbar --apple-id you@example.com --team-id TEAMID
set -euo pipefail

SRC="$(cd "$(dirname "$0")" && pwd)"
VERSION="${VERSION:-1.0.0}"
OUT="$SRC/dist"
APP="$OUT/Claude Taskbar.app"
DMG="$OUT/Claude-Taskbar-$VERSION.dmg"

command -v swiftc >/dev/null || { echo "Install the Xcode Command Line Tools first: xcode-select --install"; exit 1; }

rm -rf "$OUT"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

echo "Building for Apple silicon and Intel..."
swiftc -O -target arm64-apple-macos11 "$SRC/ClaudeTaskbar.swift" -o "$OUT/arm64"
swiftc -O -target x86_64-apple-macos11 "$SRC/ClaudeTaskbar.swift" -o "$OUT/x86_64"
lipo -create -output "$APP/Contents/MacOS/ClaudeTaskbar" "$OUT/arm64" "$OUT/x86_64"
rm "$OUT/arm64" "$OUT/x86_64"

cp "$SRC/AppIcon.icns" "$SRC/claude_hook.py" "$SRC/web_bridge.py" "$SRC/install_hooks.py" "$SRC/channel_server.py" "$APP/Contents/Resources/"
cp -R "$SRC/chrome-extension" "$APP/Contents/Resources/chrome-extension"
find "$APP" -name '__pycache__' -prune -exec rm -rf {} +

cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Claude Taskbar</string>
  <key>CFBundleDisplayName</key><string>Claude Taskbar</string>
  <key>CFBundleIdentifier</key><string>com.claudetaskbar.app</string>
  <key>CFBundleExecutable</key><string>ClaudeTaskbar</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>$VERSION</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  <key>NSHumanReadableCopyright</key><string>Shows your Claude tasks, always on top.</string>
  <key>NSAppleEventsUsageDescription</key>
  <string>Claude Taskbar switches to a claude.ai chat's Chrome tab when you click it.</string>
</dict>
</plist>
PLIST

if [ -n "${DEVELOPER_ID:-}" ]; then
  echo "Signing with $DEVELOPER_ID..."
  codesign --force --options runtime --timestamp \
    --entitlements "$SRC/ClaudeTaskbar.entitlements" -s "$DEVELOPER_ID" "$APP"
else
  echo "Signing ad hoc (no Developer ID set)..."
  codesign --force -s - "$APP"
fi

echo "Making the disk image..."
STAGE="$OUT/stage"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
cp "$SRC/INSTALL.txt" "$STAGE/Read Me First.txt"
hdiutil create -quiet -volname "Claude Taskbar" -srcfolder "$STAGE" -ov -format UDZO "$DMG"
rm -rf "$STAGE"

if [ -n "${DEVELOPER_ID:-}" ]; then
  codesign --force --timestamp -s "$DEVELOPER_ID" "$DMG"
  if [ -n "${NOTARY_PROFILE:-}" ]; then
    echo "Notarizing (this can take a few minutes)..."
    xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait
    xcrun stapler staple "$DMG"
  fi
fi

echo
echo "Done: $DMG"
