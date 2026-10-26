"""Location Jump: a tiny local web UI for setting your USB-connected iPhone's GPS location.

Run it with ./start.sh, then open http://localhost:5000.
"""
from __future__ import annotations

import atexit
import logging
import os
import random
import signal
import sys
import threading

from flask import Flask, jsonify, render_template, request

from places import PLACES
from phone import PhoneController, PhoneError, validate_coords
from routing import MODES, Router
from search import PlaceSearch
from storage import SavedPlaces

DEFAULT_DATA_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "saved_places.json")


LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _stop_server() -> None:
    # Same as pressing Ctrl+C: the server stops and atexit puts the phone back to its real location.
    threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT)).start()


def create_app(controller=None, saved=None, searcher=None, router=None, on_quit=_stop_server) -> Flask:
    app = Flask(__name__)
    phone = controller or PhoneController()
    saved = saved or SavedPlaces(os.environ.get("LOCATION_JUMP_DATA", DEFAULT_DATA_FILE))
    searcher = searcher or PlaceSearch()
    router = router or Router()
    app.config["PHONE"] = phone

    def fail(err: PhoneError, status: int = 400):
        return jsonify(ok=False, code=err.code, error=err.message), status

    @app.before_request
    def only_this_mac():
        """Refuse requests that other websites could make to localhost behind your back.

        The Host check blocks DNS-rebinding tricks. Requiring JSON on every change means a
        foreign page can't submit a plain form to us: browsers won't send a cross-site JSON
        request without our permission, and we never give it.
        """
        host = request.host or ""
        host = host[1:].split("]")[0] if host.startswith("[") else host.rsplit(":", 1)[0]  # drop the port
        if host not in LOCAL_HOSTS:
            return fail(PhoneError("Location Jump only accepts requests from this Mac.", "forbidden"), 403)
        if request.method not in ("GET", "HEAD", "OPTIONS") and not request.is_json:
            return fail(PhoneError("Requests must be sent from the Location Jump page.", "forbidden"), 415)
        return None

    @app.get("/api/ping")
    def ping():
        return jsonify(ok=True, app="location-jump")

    @app.post("/api/quit")
    def quit_app():
        if phone.current_position() is not None:
            try:
                phone.clear_location()
            except PhoneError:
                pass  # disconnected: the phone is already on its real GPS
        on_quit()
        return jsonify(ok=True)

    def call(fn, *args, then=None):
        try:
            result = fn(*args)
            if then:
                then(result)
            return jsonify(ok=True, **result)
        except PhoneError as err:
            return fail(err, 400 if err.code == "bad_input" else 503)
        except Exception as exc:  # e.g. a timeout waiting for the phone
            logging.exception("request failed")
            return fail(PhoneError(f"Something went wrong: {type(exc).__name__}: {exc}"), 500)

    @app.get("/")
    def index():
        return render_template("index.html", place_count=len(PLACES))

    @app.get("/api/status")
    def status():
        return call(phone.status)

    @app.get("/api/state")
    def state():
        # Just the simulation state (no device checks), for smooth updates while walking.
        return call(phone.state)

    def remember(state):
        # The phone is already set at this point, so a failed save must not turn into an error.
        try:
            saved.add_recent(state.get("label"), *state["anchor"])
        except Exception:
            logging.exception("couldn't save recent place")

    @app.post("/api/location")
    def set_location():
        data = request.get_json(silent=True) or {}
        try:
            lat, lon = validate_coords(data.get("lat"), data.get("lon"))
        except PhoneError as err:
            return fail(err)
        label = " ".join(str(data.get("label") or "").split())[:80] or None
        return call(phone.set_location, lat, lon, label, then=remember)

    @app.post("/api/random")
    def random_location():
        name, lat, lon = random.choice(PLACES)
        return call(phone.set_location, lat, lon, name, then=remember)

    @app.post("/api/walk")
    def walk():
        data = request.get_json(silent=True) or {}
        try:
            lat, lon = validate_coords(data.get("lat"), data.get("lon"))
        except PhoneError as err:
            return fail(err)
        mode = data.get("mode") if data.get("mode") in MODES else "walk"
        current = phone.current_position()
        if current is None:
            return fail(PhoneError("Set a starting location first (Set Location or Random), then travel from there.",
                                   "bad_input"))
        label = " ".join(str(data.get("label") or "").split())[:80] or None
        path, routed, note = router.plan(current, (lat, lon), mode, bool(data.get("follow_streets", True)))
        _, speed, _ = MODES[mode]
        return call(phone.start_walk, path, speed, label, mode, routed, note,
                    then=lambda _state: remember({"label": label, "anchor": [lat, lon]}))

    @app.post("/api/walk/stop")
    def stop_walk():
        return call(phone.stop_walk)

    @app.get("/api/search")
    def search():
        try:
            return jsonify(ok=True, results=searcher.search(request.args.get("q", "")))
        except PhoneError as err:
            return fail(err, 502)

    @app.get("/api/saved")
    def saved_places():
        return jsonify(ok=True, **saved.all())

    @app.post("/api/favorites")
    def add_favorite():
        data = request.get_json(silent=True) or {}
        try:
            lat, lon = validate_coords(data.get("lat"), data.get("lon"))
        except PhoneError as err:
            return fail(err)
        favorite = saved.add_favorite(data.get("name"), lat, lon)
        return jsonify(ok=True, favorite=favorite, **saved.all())

    @app.delete("/api/favorites/<fav_id>")
    def remove_favorite(fav_id):
        if not saved.remove_favorite(fav_id):
            return fail(PhoneError("That favorite doesn't exist anymore.", "not_found"), 404)
        return jsonify(ok=True, **saved.all())

    @app.delete("/api/recents")
    def clear_recents():
        saved.clear_recents()
        return jsonify(ok=True, **saved.all())

    @app.post("/api/reset")
    def reset():
        return call(phone.clear_location)

    @app.post("/api/wifi")
    def wifi():
        data = request.get_json(silent=True) or {}
        return call(phone.set_wifi, bool(data.get("enabled")))

    @app.post("/api/drift")
    def drift():
        data = request.get_json(silent=True) or {}
        return call(phone.set_drift, bool(data.get("enabled")))

    return app


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    app = create_app()
    # Put the phone back to its real location when the app is stopped with Ctrl+C.
    atexit.register(app.config["PHONE"].shutdown)
    # Also when macOS asks the app to stop (logging out, shutting down).
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    # Programs started in the background (as Location Jump.app does) begin with Ctrl+C ignored,
    # which would make the Quit button do nothing. Turn normal Ctrl+C handling back on.
    signal.signal(signal.SIGINT, signal.default_int_handler)
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n  Location Jump is running: open http://localhost:{port}\n  Press Ctrl+C to stop.\n")
    # threaded=True so a slow phone call doesn't freeze status polling; 127.0.0.1 keeps it on your Mac only.
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
