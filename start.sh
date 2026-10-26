#!/usr/bin/env bash
# Location Jump: one-step setup + launch.
#   1. Creates a Python virtual environment in .venv and installs the requirements
#   2. Starts the iOS 17+ tunnel (needs your Mac password, because it creates a network interface)
#   3. Launches the web app and opens it in your browser
#
# ./start.sh               run in this Terminal window (Ctrl+C to stop)
# ./start.sh --background  what "Location Jump.app" runs: no Terminal needed, uses macOS
#                          dialogs and notifications, and keeps the app running in the background
set -euo pipefail

cd "$(dirname "$0")"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:$PATH"

BACKGROUND=0
[[ "${1:-}" == "--background" ]] && BACKGROUND=1

LOG_DIR="$HOME/Library/Logs"
LOG="$LOG_DIR/LocationJump.log"
if (( BACKGROUND )); then
  mkdir -p "$LOG_DIR"
  exec >>"$LOG" 2>&1
  echo; echo "=== $(date) ==="
fi

# AppleScript strings can't contain raw double quotes or backslashes.
as_text() { local s="${1//\\/\\\\}"; printf '%s' "${s//\"/\\\"}"; }
notify() { (( BACKGROUND )) && osascript -e "display notification \"$(as_text "$1")\" with title \"Location Jump\"" >/dev/null 2>&1 || true; }

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[!]\033[0m %s\n' "$*"; }
die() {
  printf '\n\033[1;31m[x]\033[0m %s\n' "$*" >&2
  if (( BACKGROUND )); then
    osascript -e "display alert \"Location Jump couldn't start\" message \"$(as_text "$*")

Details are in ~/Library/Logs/LocationJump.log\" as critical" >/dev/null 2>&1 || true
  fi
  exit 1
}

TUNNELD_URL="http://127.0.0.1:49151"

# Is Location Jump already running? (Port 5000 may belong to AirPlay instead, so ask for our name.)
running_port() {
  for p in 5000 5001; do
    if curl -fsS --max-time 1 "http://127.0.0.1:$p/api/ping" 2>/dev/null | grep -q '"location-jump"'; then
      echo "$p"; return 0
    fi
  done
  return 1
}

if (( BACKGROUND )) && PORT_RUNNING="$(running_port)"; then
  echo "Already running on port $PORT_RUNNING; opening it."
  open "http://localhost:$PORT_RUNNING"
  exit 0
fi

# --- 1. Python ------------------------------------------------------------------------------
if [[ "$(uname)" != "Darwin" ]]; then
  warn "This tool is meant for macOS. Continuing anyway, but the tunnel steps may differ."
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1 || ! "$PYTHON" -c 'import sys' >/dev/null 2>&1; then
  die "Python 3 isn't installed. Open Terminal and run:  xcode-select --install   then try again."
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  die "Python 3.9 or newer is required (you have $("$PYTHON" --version 2>&1))."
fi

if [[ ! -x .venv/bin/python ]]; then
  say "Creating a virtual environment in .venv (one-time)…"
  notify "Setting up for the first time. This takes a minute or two."
  "$PYTHON" -m venv .venv
fi

if [[ ! -f .venv/.installed ]] || [[ requirements.txt -nt .venv/.installed ]]; then
  say "Installing pymobiledevice3 and Flask into .venv (one-time, takes a minute)…"
  notify "Installing updates. This takes a minute."
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt || die "Installing the Python packages failed. Check your internet connection and try again."
  touch .venv/.installed
fi

# --- 2. iOS 17+ tunnel ----------------------------------------------------------------------
tunnel_running() { curl -fsS --max-time 2 "$TUNNELD_URL" >/dev/null 2>&1; }

if tunnel_running; then
  say "iOS tunnel is already running."
else
  say "Starting the iOS 17+ tunnel (tunneld)."
  if (( BACKGROUND )); then
    # Native macOS password dialog instead of a Terminal prompt.
    osascript -e 'on run argv' \
      -e 'do shell script (quoted form of (item 1 of argv)) & " -m pymobiledevice3 remote tunneld -d" with administrator privileges with prompt "Location Jump needs your Mac password to start the secure connection to your iPhone (required by iOS 17)."' \
      -e 'end run' "$PWD/.venv/bin/python" \
      || warn "The tunnel wasn't started (password dialog cancelled?). The app will explain how to start it."
  else
    echo "    iOS 17 only allows developer connections through an encrypted tunnel, and creating"
    echo "    that tunnel needs administrator rights. macOS will now ask for your Mac login password."
    echo "    (Nothing is shown while you type. That's normal.)"
    sudo .venv/bin/python -m pymobiledevice3 remote tunneld -d
  fi
  for _ in $(seq 1 15); do
    tunnel_running && break
    sleep 1
  done
  if tunnel_running; then
    say "Tunnel started. It keeps running in the background until you restart your Mac or run ./stop.sh."
  elif (( ! BACKGROUND )); then
    die "The tunnel didn't start. Try running it in the foreground to see the error:
    sudo .venv/bin/python -m pymobiledevice3 remote tunneld"
  fi
fi

# --- 3. Web app -----------------------------------------------------------------------------
PORT="${PORT:-5000}"
if lsof -nP -iTCP:"$PORT" -sTCP:LISTEN >/dev/null 2>&1; then
  warn "Port $PORT is already in use. On macOS this is usually the AirPlay Receiver."
  echo "    To use port 5000, turn it off: System Settings > General > AirDrop & Handoff > AirPlay Receiver."
  PORT=5001
  echo "    Using port $PORT for now."
fi

URL="http://localhost:$PORT"

if (( BACKGROUND )); then
  say "Launching Location Jump in the background at $URL"
  PORT="$PORT" nohup .venv/bin/python app.py >>"$LOG" 2>&1 &
  disown
  for _ in $(seq 1 30); do
    running_port >/dev/null && break
    sleep 0.5
  done
  running_port >/dev/null || die "The app didn't start."
  open "$URL"
  exit 0
fi

say "Launching Location Jump at $URL  (press Ctrl+C here to stop; your phone goes back to its real location)"
( sleep 2; open "$URL" 2>/dev/null || true ) &
PORT="$PORT" exec .venv/bin/python app.py
