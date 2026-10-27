# Changelog

All notable changes to Location Jump. Versions follow [semantic versioning](https://semver.org).

## [Unreleased]

### Fixed
- **"Python 3 isn't installed" on Apple silicon Macs when the app ran in Intel mode (Rosetta)**, where Apple's Python fails with "missing compatible architecture". `start.sh` now switches back to native mode, and the app bundle asks macOS to prefer Apple silicon (run `./install-app.sh` again to get that part).
- **"Python 3 isn't installed" when opening the app**, even though Python worked in Terminal. Apps opened from Launchpad don't get Terminal's PATH, and a broken leftover Homebrew or python.org link could be picked first. `start.sh` now tries every usual Python location, uses the first 3.9+ that runs, and logs why Apple's `python3` failed with a message that points at the real fix (install the tools, accept the Xcode license, or install from python.org).

### Removed
- **Intel Macs and other systems.** Location Jump now targets Macs with Apple silicon only (M1 or later, including the A-series MacBook Neo). `start.sh` and `install-app.sh` stop with a clear message elsewhere, and the app always runs natively, never in Rosetta.
- **The tunnel service (`tunnel-service.sh`)** is gone, to keep setup simple. The app and `start.sh` start the iOS 17 tunnel when needed and ask for your Mac password once per restart. If you installed it, remove it with: `sudo launchctl bootout system /Library/LaunchDaemons/com.github.iggyduenas.locationjump.tunneld.plist; sudo rm -f /Library/LaunchDaemons/com.github.iggyduenas.locationjump.tunneld.plist; sudo rm -rf "/Library/Application Support/LocationJump"`

### Changed
- **New identity.** The pixel-pin icon replaces the old app icon (Mac app, web app and website), with a pixel favicon, an Apple touch icon and a 1200×630 link-preview image.
- **The app is Matrix green too.** The purple accent in the Mac app's web interface (buttons, switches, the route line, the phone dot) is now the design system's green, with black text on green in dark mode; the top bar shows the pixel-pin mark. README screenshots retaken.
- **Website follows the Location Jump design system**: dot-grid hero, Geist Mono kickers with a `>` prefix, numbered feature cards, a tagline that lights up in green with a block cursor, a green ring on screenshot windows, hairline section rules, and warning, danger and status colours that keep 4.5:1 contrast in both themes.
- Website and README install steps now include checking `python3 --version`, with python.org as the fallback, plus FAQ and troubleshooting entries for missing Python and non-administrator accounts.

## [1.1.0] - 2026-10-26

### Added
- **Project website** (`site/`), published to GitHub Pages: features, a live "jump around the world" demo, install steps with copy buttons, FAQ and responsible-use notes.
- **Stress test** (`scripts/stress_test.py`): many clients against an unreliable pretend iPhone, with pass/fail checks.

### Fixed
- A hung iPhone command could freeze the whole page for about 20 seconds, because every request waiting for the phone tied up the web server (found by the stress test). Button presses now wait at most 5 s and only one at a time, getting a clear "busy" message otherwise; phone commands time out after 8 s instead of 20 s; the server has 16 threads. Under 40 simultaneous clients: worst ping 20 s → 0.25 s, throughput 31 → 279 requests/s.
- Reset now stops the background loop immediately instead of on its next tick.
- Favorites are capped at 200 (each save rewrites the file; the stress test showed thousands would slow things down). No memory leaks were found: server memory stays flat over 20,000+ requests.

## [1.0.0] - 2026-10-26

### Added
- **Several devices:** when more than one iPhone/iPad is connected, the app asks which to use (it never guesses), lets you switch from the status pill, and resets the previous device first.
- Version number in the page footer, `/api/ping`, and the app bundle.
- Developer tooling: `make check`, ruff and shellcheck linting, GitHub Actions CI (tests on Python 3.9, 3.11 and 3.13; app bundle, tunnel service and `start.sh` on a real macOS machine), and a script plus workflow that regenerate the README screenshots.
- MIT license.

### Changed
- The map library (Leaflet 1.9.4) is bundled, so the app loads and works without internet; only map tiles, place search and street routes need it.
- Served by waitress, a production web server, instead of Flask's development server.
- Errors now keep their original cause, which makes the log more useful when something goes wrong.

### Fixed
- The map showed "API KEY REQUIRED" tiles: CARTO's tile service now requires an account. The map now uses OpenStreetMap's own tiles (no key needed), darkened with a CSS filter in dark mode.
- README screenshots added (generated with a pretend iPhone by the Screenshots workflow).

## [0.7.0] - 2026-10-26
### Added
- **Wi-Fi mode:** use the phone without the cable on the same Wi-Fi network; auto-reconnect picks the phone up over Wi-Fi after unplugging.

## [0.6.0] - 2026-10-26
### Added
- **Tunnel service** (`./tunnel-service.sh install`): the iOS 17 tunnel starts at boot, so the Mac password is asked only once. Uses its own root-owned copy of pymobiledevice3.

## [0.5.0] - 2026-10-26
### Added
- **Location Jump.app** (`./install-app.sh`): start from Launchpad/Spotlight/Dock without Terminal; macOS password dialog; Quit button in the page.
### Security
- Requests that other websites could send to the app are refused (non-localhost Host header, or changes not sent as JSON).
### Fixed
- Quit did nothing when the app was started in the background.

## [0.4.0] - 2026-10-26
### Added
- **Walk mode:** travel to a destination at walk, jog, bike or drive speed along real streets, with progress, ETA and Stop.

## [0.3.0] - 2026-10-26
### Added
- Place and address search, favorites and recent places, and automatic reconnect that restores the fake location after a dropped connection.

## [0.2.0] - 2026-10-26
### Changed
- Redesigned interface: full-screen map, floating panel, dark mode, custom markers, toasts.

## [0.1.0] - 2026-10-26
### Added
- First version: set, randomize, drift and reset the location of a USB-connected iOS 17+ iPhone from a local web page.
