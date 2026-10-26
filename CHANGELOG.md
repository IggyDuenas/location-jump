# Changelog

All notable changes to Location Jump. Versions follow [semantic versioning](https://semver.org).

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
