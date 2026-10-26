#!/usr/bin/env bash
# Location Jump: one-step setup + launch.
#   1. Creates a Python virtual environment in .venv and installs the requirements
#   2. Starts the iOS 17+ tunnel (needs your Mac password, because it creates a network interface)
#   3. Launches the web app and opens it in your browser
set -euo pipefail

cd "$(dirname "$0")"

say()  { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\n\033[1;33m[!]\033[0m %s\n' "$*"; }
die()  { printf '\n\033[1;31m[x]\033[0m %s\n' "$*" >&2; exit 1; }

TUNNELD_URL="http://127.0.0.1:49151"

# --- 1. Python ------------------------------------------------------------------------------
if [[ "$(uname)" != "Darwin" ]]; then
  warn "This tool is meant for macOS. Continuing anyway, but the tunnel steps may differ."
fi

PYTHON="${PYTHON:-python3}"
if ! command -v "$PYTHON" >/dev/null 2>&1; then
  die "python3 not found. Install it with:  xcode-select --install   (or from https://www.python.org/downloads/)"
fi
if ! "$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 9) else 1)'; then
  die "Python 3.9 or newer is required (you have $("$PYTHON" --version 2>&1))."
fi

if [[ ! -x .venv/bin/python ]]; then
  say "Creating a virtual environment in .venv (one-time)…"
  "$PYTHON" -m venv .venv
fi

if [[ ! -f .venv/.installed ]] || [[ requirements.txt -nt .venv/.installed ]]; then
  say "Installing pymobiledevice3 and Flask into .venv (one-time, takes a minute)…"
  .venv/bin/python -m pip install --upgrade pip >/dev/null
  .venv/bin/python -m pip install -r requirements.txt
  touch .venv/.installed
fi

# --- 2. iOS 17+ tunnel ----------------------------------------------------------------------
tunnel_running() { curl -fsS --max-time 2 "$TUNNELD_URL" >/dev/null 2>&1; }

if tunnel_running; then
  say "iOS tunnel is already running."
else
  say "Starting the iOS 17+ tunnel (tunneld)."
  echo "    iOS 17 only allows developer connections through an encrypted tunnel, and creating"
  echo "    that tunnel needs administrator rights. macOS will now ask for your Mac login password."
  echo "    (Nothing is shown while you type. That's normal.)"
  sudo .venv/bin/python -m pymobiledevice3 remote tunneld -d
  for _ in $(seq 1 15); do
    tunnel_running && break
    sleep 1
  done
  tunnel_running || die "The tunnel didn't start. Try running it in the foreground to see the error:
    sudo .venv/bin/python -m pymobiledevice3 remote tunneld"
  say "Tunnel started. It keeps running in the background until you restart your Mac or run ./stop.sh."
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
say "Launching Location Jump at $URL  (press Ctrl+C here to stop; your phone goes back to its real location)"
( sleep 2; open "$URL" 2>/dev/null || true ) &
PORT="$PORT" exec .venv/bin/python app.py
