"""Regenerate the README screenshots (docs/screenshots/*.png).

Runs the real web app with a pretend iPhone (nothing is sent to a phone) and takes
pictures with a headless browser. Needs internet for the map tiles and street routes.

    pip install -r requirements.txt playwright && python -m playwright install chromium
    python scripts/screenshots.py            # writes docs/screenshots/

The "Screenshots" GitHub Actions workflow runs this and uploads the images.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from werkzeug.serving import make_server  # noqa: E402

import phone  # noqa: E402
from app import create_app  # noqa: E402
from storage import SavedPlaces  # noqa: E402

PORT = 5099


class PretendPhone(phone.PhoneController):
    """The real controller, minus the USB/tunnel parts."""

    async def _status(self) -> dict:
        s = {"connected": True, "ready": True, "code": "ok", "message": "Connected. Ready.", "devices": [],
             "device": {"name": "Iggy's iPhone", "ios": "18.1", "connection": "usb", "wifi_enabled": False}}
        s.update(self._state())
        return s

    async def _open_session(self):
        class Sim:
            async def set(self, lat, lon):
                pass

            async def clear(self):
                pass

        self._sim = self._sim or Sim()
        return self._sim

    async def _close_session(self) -> None:
        self._sim = None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default=os.path.join(ROOT, "docs", "screenshots"))
    args = parser.parse_args()
    os.makedirs(args.out, exist_ok=True)

    from playwright.sync_api import sync_playwright

    data = tempfile.mkdtemp()
    saved = SavedPlaces(os.path.join(data, "saved.json"))
    for name, lat, lon in [("Shibuya Crossing, Tokyo", 35.6595, 139.7005),
                           ("Golden Gate Bridge, San Francisco", 37.8199, -122.4783),
                           ("Eiffel Tower, Paris", 48.8584, 2.2945)]:
        saved.add_favorite(name, lat, lon)
    for name, lat, lon in [("Colosseum, Rome", 41.8902, 12.4922), ("Sydney Opera House, Sydney", -33.8568, 151.2153)]:
        saved.add_recent(name, lat, lon)

    controller = PretendPhone()
    server = make_server("127.0.0.1", PORT, create_app(controller, saved), threaded=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    url = f"http://127.0.0.1:{PORT}/"

    def settle(page, extra=1.5):
        page.wait_for_load_state("networkidle")
        time.sleep(extra)  # map fly animation + tile fade-in

    with sync_playwright() as p:
        browser = p.chromium.launch(executable_path=os.environ.get("CHROMIUM_PATH") or None)

        def shoot(name, *, scheme="light", size=(1440, 900), setup=None, before=None):
            page = browser.new_page(viewport={"width": size[0], "height": size[1]}, color_scheme=scheme,
                                    device_scale_factor=2 if size[0] < 800 else 1)
            if setup:
                setup()
            page.goto(url)
            page.wait_for_function("document.getElementById('status-text').textContent.includes('iOS')")
            if before:
                before(page)
            settle(page)
            path = os.path.join(args.out, f"{name}.png")
            page.screenshot(path=path)
            print("wrote", path)
            page.close()

        # 1. The main screen: a famous place, drifting, with favorites.
        def eiffel():
            controller.set_location(48.8584, 2.2945, "Eiffel Tower, Paris")
            controller.set_drift(True)
        shoot("main", setup=eiffel)
        controller.set_drift(False)

        # 2. Walk mode along real streets, part of the way there.
        def walking(page):
            controller.set_location(40.7580, -73.9855, "Times Square, New York")
            page.fill("#lat", "40.7794")
            page.fill("#lon", "-73.9632")
            page.click(".seg[data-mode=walk]")  # (blurring the inputs clears the picked name...)
            page.evaluate("pickLabel = 'The Met, New York'")  # (...so name the destination afterwards)
            page.click("#btn-walk")
            page.wait_for_selector("#trip:not([hidden])")
            controller.WALK_TICK_S = 3600  # freeze the trip for the picture
            walk = controller.walk
            walk["done_m"] = walk["path"].total_m * 0.35
            point = walk["path"].point_at(walk["done_m"])
            controller.current = controller.anchor = point
            page.evaluate("refreshStatus()")
        shoot("walk", before=walking)
        controller.WALK_TICK_S = phone.PhoneController.WALK_TICK_S
        controller.stop_walk()

        # 3. Dark mode, with the place search open.
        def search(page):
            page.fill("#search", "sydney opera")
            page.wait_for_selector(".search-results .r-name", timeout=20000)
        shoot("dark", scheme="dark", setup=lambda: controller.set_location(-33.8568, 151.2153,
                                                                          "Sydney Opera House, Sydney"),
              before=search)

        # 4. Phone-sized window.
        shoot("mobile", size=(390, 844), setup=lambda: controller.set_location(35.6595, 139.7005,
                                                                              "Shibuya Crossing, Tokyo"))
        browser.close()
    server.shutdown()


if __name__ == "__main__":
    main()
