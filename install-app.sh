#!/usr/bin/env bash
# Builds "Location Jump.app" in your Applications folder so you can start Location Jump
# from Launchpad, Spotlight or the Dock instead of Terminal.
#
#   ./install-app.sh               install (or update) the app
#   ./install-app.sh --uninstall   remove it
#
# The app is a small launcher that runs ./start.sh --background from this folder, so keep
# this folder where it is. If you move it, run ./install-app.sh again from the new place.
set -euo pipefail

cd "$(dirname "$0")"
REPO="$PWD"
APP_DIR="$HOME/Applications"
APP="$APP_DIR/Location Jump.app"

if [[ "${1:-}" == "--uninstall" ]]; then
  rm -rf "$APP"
  echo "Removed $APP"
  exit 0
fi

[[ "$(uname)" == "Darwin" ]] || { echo "This builds a macOS app, so it has to run on a Mac."; exit 1; }
[[ "$(uname -m)" == "arm64" || "$(sysctl -in sysctl.proc_translated 2>/dev/null)" == "1" ]] \
  || { echo "Location Jump needs a Mac with Apple silicon (M1 or later, or MacBook Neo)."; exit 1; }

echo "==> Building $APP"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"

# The launcher: find this folder and hand over to start.sh.
cat > "$APP/Contents/MacOS/LocationJump" <<EOF
#!/bin/bash
REPO=$(printf '%q' "$REPO")
if [[ ! -x "\$REPO/start.sh" ]]; then
  osascript -e 'on run argv' -e 'display alert "Location Jump can’t find its folder" message (item 1 of argv) as critical' -e 'end run' \
    "It was at: \$REPO

If you moved or renamed the folder, open Terminal in its new location and run ./install-app.sh again."
  exit 1
fi
exec "\$REPO/start.sh" --background
EOF
chmod +x "$APP/Contents/MacOS/LocationJump"

VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' version.py)"
cat > "$APP/Contents/Info.plist" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>CFBundleName</key><string>Location Jump</string>
  <key>CFBundleDisplayName</key><string>Location Jump</string>
  <key>CFBundleIdentifier</key><string>com.github.iggyduenas.locationjump</string>
  <key>CFBundleExecutable</key><string>LocationJump</string>
  <key>CFBundleIconFile</key><string>AppIcon</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>$VERSION</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <!-- Apple silicon only. The launcher is a script, so macOS can't tell which chip it's for
       and may pick Intel mode (Rosetta), where Apple's Python can't start. -->
  <key>LSArchitecturePriority</key><array><string>arm64</string></array>
  <key>LSRequiresNativeExecution</key><true/>
  <!-- The launcher only starts the background server and opens your browser, then exits,
       so it shouldn't sit in the Dock. -->
  <key>LSUIElement</key><true/>
  <key>NSHighResolutionCapable</key><true/>
</dict>
</plist>
EOF

# App icon: turn the 1024px PNG into the .icns file macOS wants.
ICONSET="$(mktemp -d)/AppIcon.iconset"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z $size $size assets/AppIcon.png --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  sips -z $((size * 2)) $((size * 2)) assets/AppIcon.png --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$APP/Contents/Resources/AppIcon.icns"
rm -rf "$(dirname "$ICONSET")"

# Tell Finder/Launchpad about the new app (and its icon) right away.
touch "$APP"
LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
[[ -x "$LSREGISTER" ]] && "$LSREGISTER" -f "$APP" >/dev/null 2>&1 || true

echo
echo "Done! Open Location Jump from Launchpad or Spotlight (Cmd+Space, type \"Location Jump\")."
echo "Tip: drag it from your Applications folder ($APP_DIR) into the Dock for one-click access."
open -R "$APP" 2>/dev/null || true
