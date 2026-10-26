"""Location Jump: a tiny local web UI for setting your USB-connected iPhone's GPS location.

Run it with ./start.sh, then open http://localhost:5000.
"""
from __future__ import annotations

import atexit
import logging
import os
import random

from flask import Flask, jsonify, render_template, request

from places import PLACES
from phone import PhoneController, PhoneError, validate_coords


def create_app(controller=None) -> Flask:
    app = Flask(__name__)
    phone = controller or PhoneController()
    app.config["PHONE"] = phone

    def fail(err: PhoneError, status: int = 400):
        return jsonify(ok=False, code=err.code, error=err.message), status

    def call(fn, *args):
        try:
            return jsonify(ok=True, **fn(*args))
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

    @app.post("/api/location")
    def set_location():
        data = request.get_json(silent=True) or {}
        try:
            lat, lon = validate_coords(data.get("lat"), data.get("lon"))
        except PhoneError as err:
            return fail(err)
        return call(phone.set_location, lat, lon, data.get("label"))

    @app.post("/api/random")
    def random_location():
        name, lat, lon = random.choice(PLACES)
        return call(phone.set_location, lat, lon, name)

    @app.post("/api/reset")
    def reset():
        return call(phone.clear_location)

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
    port = int(os.environ.get("PORT", "5000"))
    print(f"\n  Location Jump is running: open http://localhost:{port}\n  Press Ctrl+C to stop.\n")
    # threaded=True so a slow phone call doesn't freeze status polling; 127.0.0.1 keeps it on your Mac only.
    app.run(host="127.0.0.1", port=port, threaded=True, use_reloader=False)
