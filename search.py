"""Place search ("Eiffel Tower", "10 Downing St") using OpenStreetMap's free Nominatim service.

Nominatim's usage policy asks for an identifying User-Agent, at most one request per second,
and caching repeated queries, so all three are handled here rather than in the browser.
"""
from __future__ import annotations

import threading
import time
from collections import OrderedDict

import requests

from phone import PhoneError
from version import __version__

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = f"LocationJump/{__version__} (personal hobby app; https://github.com/iggyduenas/location-jump)"
MIN_INTERVAL_S = 1.0
CACHE_SIZE = 200


class PlaceSearch:
    def __init__(self, session: requests.Session | None = None):
        self._http = session or requests.Session()
        self._http.headers.update({"User-Agent": USER_AGENT, "Accept-Language": "en"})
        self._lock = threading.Lock()
        self._last_request = 0.0
        self._cache: OrderedDict[str, list] = OrderedDict()

    def search(self, query: str, limit: int = 6) -> list[dict]:
        query = " ".join((query or "").split())
        if len(query) < 2:
            return []
        key = query.lower()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            wait = self._last_request + MIN_INTERVAL_S - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            try:
                resp = self._http.get(
                    NOMINATIM_URL,
                    params={"q": query, "format": "jsonv2", "limit": limit, "addressdetails": 0},
                    timeout=8,
                )
            except requests.RequestException as exc:
                raise PhoneError("Couldn't reach the place search. Check your Mac's internet connection.",
                                 "search_failed") from exc
            finally:
                self._last_request = time.monotonic()
            if resp.status_code == 429:
                raise PhoneError("Place search is busy. Wait a few seconds and try again.", "search_failed")
            if not resp.ok:
                raise PhoneError(f"Place search failed (error {resp.status_code}). Try again shortly.",
                                 "search_failed")
            try:
                rows = resp.json()
                if not isinstance(rows, list):
                    raise ValueError("expected a list")
                results = [self._format(r) for r in rows]
            except (ValueError, KeyError, TypeError, AttributeError) as exc:
                raise PhoneError("Place search sent back something unexpected. Try again.", "search_failed") from exc
            self._cache[key] = results
            if len(self._cache) > CACHE_SIZE:
                self._cache.popitem(last=False)
            return results

    @staticmethod
    def _format(r: dict) -> dict:
        full = r.get("display_name") or ""
        name = r.get("name") or full.split(",")[0]
        detail = full[len(name):].lstrip(", ") if full.startswith(name) else full
        return {"name": name, "detail": detail, "lat": float(r["lat"]), "lon": float(r["lon"])}
