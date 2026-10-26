# 📍 Location Jump

A small Mac tool that sets the GPS location of your USB-connected iPhone (iOS 17 or later) from a web page on your Mac. Pick a spot on a map, or hit **Random Location** to jump to one of 55 famous places. Your phone then reports that location to every app, including Snap Map.

> **For personal fun and testing only.** While it's on, your *whole phone* thinks it's somewhere else: Snapchat, Find My, Maps, weather, and so on. Snapchat's terms don't allow location spoofing, and accounts *can* be flagged for it, so keep it to harmless jokes with people who are in on it. Press **Reset to Real Location** when you're done.

---

## What you need

- A Mac (any recent macOS) and a USB cable for your iPhone
- An iPhone running **iOS 17 or later**
- About 10 minutes for the first setup

---

## Step 1: Prepare your iPhone (one time)

1. **Set a passcode** if you don't have one. Developer Mode requires it.
2. **Turn on Developer Mode:** *Settings → Privacy & Security → Developer Mode* (at the very bottom) → turn it **on** → tap **Restart**. After the restart, unlock the phone and tap **Turn On** when asked.
   - *Don't see "Developer Mode"?* It only appears after the phone has been connected to developer tools once. Do Step 2, plug the phone in, run
     `.venv/bin/python -m pymobiledevice3 amfi reveal-developer-mode`
     in the project folder, and the option will appear.
3. **Plug the iPhone into your Mac** with the cable and **unlock it**.
4. When the phone asks **"Trust This Computer?"**, tap **Trust** and enter your passcode.
5. For Snap Map: in *Settings → Snapchat → Location*, choose **While Using the App** and turn **Precise Location** on. In Snapchat, make sure **Ghost Mode is off** (Snap Map → ⚙️), otherwise nobody sees your location anyway.

## Step 2: Get the tool onto your Mac (one time)

Open the **Terminal** app (press ⌘+Space, type *Terminal*, press Enter) and run:

```bash
xcode-select --install          # installs Python 3 and git; if it says "already installed", that's fine
git clone https://github.com/iggyduenas/location-jump.git
cd location-jump
```

## Step 3: Start it (every time)

In Terminal, inside the `location-jump` folder:

```bash
./start.sh
```

The script does everything for you, explaining each step as it goes:

1. **Creates a Python "virtual environment"** in a hidden `.venv` folder and installs `pymobiledevice3` (the library that talks to iPhones) and `Flask` (the tiny web server). This happens only the first time and takes a minute or two.
2. **Starts the iOS tunnel.** Since iOS 17, Apple only allows developer tools to talk to an iPhone through an encrypted network tunnel, and creating that tunnel needs administrator rights. That's why **it asks for your Mac login password**. Nothing appears while you type it; that's normal. The tunnel then keeps running in the background, so you won't be asked again until you restart your Mac.
3. **Opens the web page** at **http://localhost:5000** in your browser.

> **Port 5000 note:** On newer Macs, the AirPlay Receiver already uses port 5000. If so, `start.sh` tells you and uses **http://localhost:5001** instead. To get 5000 back, turn off *System Settings → General → AirDrop & Handoff → AirPlay Receiver*.

## Step 4: Use it

- The **status light** in the top-right should turn **green** ("Connected to *your iPhone*. Ready."). If it's yellow or red, it tells you in plain English what to fix (see Troubleshooting below).
- **Click anywhere on the map** (or type a latitude and longitude), then press **Set Location**.
  - The **very first time**, this takes up to a minute: the tool downloads Apple's "Developer Disk Image" and installs it on your phone. After that it's instant.
- **🎲 Random Location** jumps to a random famous place (Eiffel Tower, Shibuya Crossing, Sydney Opera House, the Pyramids, and 51 more).
- **Natural drift** wiggles your position by a few meters every ~5 seconds, so on Snap Map you look like a real person standing around rather than a frozen pin. It never wanders more than about 8 meters from the spot you chose. Turning it off snaps you back to the exact spot.
- **Phone is showing** shows the exact coordinates your phone is reporting right now. The red dot on the map is the same thing.
- **Reset to Real Location** puts your phone back to its real GPS.

**Check it worked:** open Apple Maps or Snapchat → Snap Map on the phone. Snap Map updates your Bitmoji when you open the app, so open (or re-open) Snapchat after changing location.

**Keep the phone plugged in and the app running** while you want the fake location. The fake location stays only while this tool is connected. If you press **Ctrl+C** in Terminal, the tool resets your phone to its real location before quitting.

## Step 5: When you're done

1. Press **Reset to Real Location** (or Ctrl+C in Terminal).
2. Optionally stop the background tunnel with `./stop.sh` (it asks for your password). Restarting your Mac stops it too.

---

## Troubleshooting

| What the status says | What to do |
|---|---|
| **No iPhone found** | Plug the phone in with a cable that carries data (some cheap cables only charge), unlock it, and wait a few seconds. |
| **Your iPhone is asking "Trust This Computer?"** | Unlock the phone, tap **Trust**, and enter your passcode. |
| **Your iPhone doesn't trust this Mac yet** / **You tapped "Don't Trust"** | Unplug and replug the cable, then tap **Trust**. If the prompt never appears: *Settings → General → Transfer or Reset iPhone → Reset → Reset Location & Privacy*, then replug. |
| **Unlock your iPhone** | Unlock it with your passcode and try again. |
| **Developer Mode is off** | See Step 1.2 above. The phone has to restart after turning it on. |
| **The iOS 17 tunnel isn't running** | Run `./start.sh` again, or in the project folder run `sudo .venv/bin/python -m pymobiledevice3 remote tunneld -d`. |
| **The tunnel is running but hasn't reached your iPhone yet** | Keep the phone unlocked and plugged in for ~10 seconds. Still stuck? Run `./stop.sh`, then `./start.sh`. |
| **Couldn't download the Developer Disk Image** | The first Set Location needs internet on the Mac. Check your connection and try again. |
| **Lost the connection to the iPhone** | Usually the cable wiggled or the phone locked during setup. Just press the button again; the tool reconnects automatically. |
| Browser shows **"403 Forbidden"** at localhost:5000 | That's the Mac's AirPlay Receiver, not this app. Use the address `start.sh` printed (probably http://localhost:5001). |
| Phone seems stuck on a fake location | Press **Reset to Real Location**. If the tool isn't running anymore, simply **restart the iPhone**; that always restores the real GPS. |

To see the tunnel's own log while debugging, stop it (`./stop.sh`) and run it in the foreground:
`sudo .venv/bin/python -m pymobiledevice3 remote tunneld`

---

## How it works (for the curious)

```
Browser (map UI) ──HTTP──► Flask app (app.py) ──► phone.py ──► tunneld (sudo, port 49151) ══encrypted tunnel══► iPhone
```

- **`start.sh`**: setup and launch.
- **`app.py`**: the Flask web server and its small JSON API (`/api/status`, `/api/location`, `/api/random`, `/api/reset`, `/api/drift`).
- **`phone.py`**: all the iPhone logic. It checks USB, trust, Developer Mode, and the tunnel in that order, so the status tells you the *first* thing that's wrong. It keeps one long-lived developer connection open, because iOS drops the simulated location when that connection closes. It also runs the drift loop.
- **`places.py`**: the 55 famous places for Random Location.
- **`templates/` + `static/`**: the web page (Leaflet map with OpenStreetMap tiles).
- **`tests/`**: automated tests that use a pretend iPhone. Run them with `.venv/bin/pip install pytest && .venv/bin/python -m pytest`.

The web app only listens on `127.0.0.1`, so nobody else on your Wi-Fi can open it.
