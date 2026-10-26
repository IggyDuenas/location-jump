#!/usr/bin/env bash
# Runs the iOS 17+ tunnel as an always-on macOS background service, so you're not asked for
# your Mac password every time you start Location Jump.
#
#   ./tunnel-service.sh install     set it up (asks for your password once); re-run to update
#   ./tunnel-service.sh uninstall   remove it (back to starting the tunnel on demand)
#   ./tunnel-service.sh status      is it installed and running?
#   ./tunnel-service.sh restart     restart it (if it gets stuck)
#
# The service runs as root (creating the tunnel needs that), so it uses its own copy of
# pymobiledevice3 in a folder only an administrator can change. It never runs code from
# this (user-writable) project folder.
set -euo pipefail

cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

LABEL="com.github.iggyduenas.locationjump.tunneld"
SERVICE_DIR="${LJ_SERVICE_DIR:-/Library/Application Support/LocationJump}"
DAEMON_DIR="${LJ_DAEMON_DIR:-/Library/LaunchDaemons}"
PLIST="$DAEMON_DIR/$LABEL.plist"
VENV="$SERVICE_DIR/tunnel-venv"
LOG="${LJ_LOG_DIR:-/Library/Logs}/LocationJump-tunnel.log"
TUNNELD_URL="http://127.0.0.1:49151"

say() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\n\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }
tunnel_running() { curl -fsS --max-time 2 "$TUNNELD_URL" >/dev/null 2>&1; }
installed() { [[ -f "$PLIST" ]]; }

xml_escape() { local s="${1//&/&amp;}"; s="${s//</&lt;}"; printf '%s' "${s//>/&gt;}"; }

install() {
  [[ "$(uname)" == "Darwin" ]] || die "This sets up a macOS background service, so it has to run on a Mac."
  local python="${PYTHON:-python3}"
  command -v "$python" >/dev/null 2>&1 || die "Python 3 isn't installed. Run:  xcode-select --install"
  python="$("$python" -c 'import sys; print(sys.executable)')"
  local pin
  pin="$(grep -i '^pymobiledevice3' requirements.txt)"

  say "Installing the tunnel service. macOS will ask for your Mac password (one time)."
  sudo -v || die "Administrator access is needed to install a background service."

  say "Creating a private copy of pymobiledevice3 in $SERVICE_DIR (takes a minute)…"
  sudo mkdir -p "$SERVICE_DIR"
  [[ -x "$VENV/bin/python" ]] || sudo "$python" -m venv "$VENV"
  sudo -H "$VENV/bin/python" -m pip install --quiet --upgrade pip
  sudo -H "$VENV/bin/python" -m pip install --quiet --upgrade "$pin"
  sudo chown -R root:wheel "$SERVICE_DIR"
  sudo chmod -R go-w "$SERVICE_DIR"

  say "Registering the service with macOS…"
  # A tunnel started by start.sh would hold the port the service needs.
  sudo pkill -f "pymobiledevice3 remote tunneld" 2>/dev/null || true
  local tmp
  tmp="$(mktemp)"
  cat > "$tmp" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(xml_escape "$VENV/bin/python")</string>
    <string>-m</string><string>pymobiledevice3</string>
    <string>remote</string><string>tunneld</string>
  </array>
  <key>EnvironmentVariables</key>
  <dict><key>HOME</key><string>/var/root</string></dict>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$(xml_escape "$LOG")</string>
  <key>StandardErrorPath</key><string>$(xml_escape "$LOG")</string>
</dict>
</plist>
EOF
  sudo launchctl bootout system "$PLIST" >/dev/null 2>&1 || true
  sudo install -m 644 -o root -g wheel "$tmp" "$PLIST"
  rm -f "$tmp"
  sudo launchctl bootstrap system "$PLIST"
  sudo launchctl enable "system/$LABEL"

  for _ in $(seq 1 20); do
    tunnel_running && break
    sleep 1
  done
  tunnel_running || die "The service was installed but the tunnel isn't answering yet. Check the log:  $LOG"
  say "Done! The tunnel now starts automatically, even after a restart. You won't be asked for your password again."
}

uninstall() {
  if ! installed; then
    echo "The tunnel service isn't installed."
    return
  fi
  say "Removing the tunnel service (macOS will ask for your password)…"
  sudo launchctl bootout system "$PLIST" >/dev/null 2>&1 || true
  sudo rm -f "$PLIST"
  sudo rm -rf "$SERVICE_DIR"
  say "Removed. ./start.sh (or the app) will start the tunnel on demand again, asking for your password."
}

status() {
  if installed; then
    echo "Tunnel service: installed ($PLIST)"
  else
    echo "Tunnel service: not installed (the tunnel is started on demand by ./start.sh or the app)"
  fi
  if tunnel_running; then
    echo "Tunnel: running"
  else
    echo "Tunnel: not running"
  fi
  [[ -f "$LOG" ]] && echo "Log: $LOG"
  return 0
}

restart() {
  installed || die "The tunnel service isn't installed. Install it with:  ./tunnel-service.sh install"
  sudo launchctl kickstart -k "system/$LABEL"
  for _ in $(seq 1 20); do
    tunnel_running && { echo "Tunnel restarted."; return; }
    sleep 1
  done
  die "The tunnel didn't come back. Check the log:  $LOG"
}

case "${1:-}" in
  install) install ;;
  uninstall) uninstall ;;
  status) status ;;
  restart) restart ;;
  *) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 1 ;;
esac
