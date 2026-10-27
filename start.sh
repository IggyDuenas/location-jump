#!/usr/bin/env bash
# Location Jump: one-step setup + launch.
#   1. Creates a Python virtual environment in .venv and installs the requirements
#   2. Launches the web app and opens it in your browser (the app makes the iOS 17 tunnel
#      itself, so no Mac password is needed)
#
# ./start.sh               run in this Terminal window (Ctrl+C to stop)
# ./start.sh --background  what "Location Jump.app" runs: no Terminal needed, uses macOS
#                          dialogs and notifications, and keeps the app running in the background
set -euo pipefail

# On Apple silicon the app can end up running in Intel mode (Rosetta), where Apple's Python
# can't start ("missing compatible architecture"). Switch back to native mode first.
if [[ "$(uname)" == "Darwin" && -z "${LJ_NATIVE:-}" ]] \
   && [[ "$(sysctl -in sysctl.proc_translated 2>/dev/null)" == "1" ]]; then
  export LJ_NATIVE=1
  exec arch -arm64 /bin/bash "$0" "$@"
fi

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

# Is Location Jump already running? (Port 5000 may belong to AirPlay instead, so ask for our name.)
running_port() {
  for p in 5000 5001; do
    if curl -fsS --max-time 1 "http://127.0.0.1:$p/api/ping" 2>/dev/null | grep -q '"location-jump"'; then
      echo "$p"; return 0
    fi
  done
  return 1
}

# --- 1. Python ------------------------------------------------------------------------------
# Location Jump is built for Macs with Apple silicon (M1 or later, or the A-series MacBook Neo).
[[ "$(uname)" == "Darwin" ]] || die "Location Jump only runs on a Mac."
[[ "$(uname -m)" == "arm64" ]] || die "Location Jump needs a Mac with Apple silicon (M1 or later, or MacBook Neo). This Mac has an Intel processor."

# Apps opened from Launchpad don't get Terminal's PATH, and a leftover Homebrew or python.org
# link can be broken, so try every usual place and use the first Python 3.9+ that runs.
find_python() {
  local c
  for c in ${PYTHON:+"$PYTHON"} python3 \
           /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
           /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    command -v "$c" >/dev/null 2>&1 || continue
    if "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)' >/dev/null 2>&1; then
      command -v "$c"; return 0
    fi
  done
  return 1
}

if ! PYTHON="$(find_python)"; then
  # Say why Apple's python3 didn't run, so the message points at the real fix.
  why="$(/usr/bin/python3 -c 'import sys' 2>&1 || true)"
  echo "No working Python 3.9+ found. /usr/bin/python3 said: ${why:-nothing}"
  if [[ "$why" == *icense* ]]; then
    die "Python needs the Xcode license accepted. Open Terminal and run:  sudo xcodebuild -license accept   then try again."
  elif [[ "$why" == *"developer path"* || "$why" == *xcode-select* || -z "$(xcode-select -p 2>/dev/null)" ]]; then
    die "Python 3 isn't installed. Open Terminal and run:  xcode-select --install   then try again."
  else
    die "Python 3 didn't start. Install it from python.org/downloads (the macOS installer), then try again."
  fi
fi
echo "Using $PYTHON ($("$PYTHON" --version 2>&1))"

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

# --- 2. Web app -----------------------------------------------------------------------------
if (( BACKGROUND )) && PORT_RUNNING="$(running_port)"; then
  echo "Already running on port $PORT_RUNNING; opening it."
  open "http://localhost:$PORT_RUNNING"
  exit 0
fi

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
