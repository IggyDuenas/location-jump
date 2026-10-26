#!/usr/bin/env bash
# Stops the background iOS tunnel that start.sh launched (asks for your Mac password).
set -euo pipefail
if [[ -f /Library/LaunchDaemons/com.github.iggyduenas.locationjump.tunneld.plist ]]; then
  echo "The tunnel runs as an always-on background service, so there's nothing to stop."
  echo "  Restart it:  ./tunnel-service.sh restart"
  echo "  Remove it:   ./tunnel-service.sh uninstall"
  exit 0
fi
if pgrep -f "pymobiledevice3 remote tunneld" >/dev/null; then
  sudo pkill -f "pymobiledevice3 remote tunneld"
  echo "Tunnel stopped."
else
  echo "The tunnel isn't running."
fi
