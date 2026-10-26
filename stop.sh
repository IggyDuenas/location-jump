#!/usr/bin/env bash
# Stops the background iOS tunnel that start.sh launched (asks for your Mac password).
set -euo pipefail
if pgrep -f "pymobiledevice3 remote tunneld" >/dev/null; then
  sudo pkill -f "pymobiledevice3 remote tunneld"
  echo "Tunnel stopped."
else
  echo "The tunnel isn't running."
fi
