# Changelog

All notable changes to Location Jump. Versions follow [semantic versioning](https://semver.org).

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
