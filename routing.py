"""Walk mode: plan a path between two points and work out where along it the phone should be.

Street routes come from the free OpenStreetMap routing service run by FOSSGIS
(routing.openstreetmap.de). If it can't find a route (e.g. across an ocean) or isn't
reachable, we fall back to a straight line so walking still works.
"""
from __future__ import annotations

import bisect
import threading
import time
from typing import Optional

import requests

from phone import distance_m
from search import USER_AGENT

ROUTER_URL = "https://routing.openstreetmap.de/routed-{profile}/route/v1/driving/{lon1},{lat1};{lon2},{lat2}"

# mode -> (routing profile, speed in meters per second, verb for messages)
MODES = {
    "walk": ("foot", 1.4, "Walking"),    # ~5 km/h
    "jog": ("foot", 2.8, "Jogging"),     # ~10 km/h
    "bike": ("bike", 5.0, "Biking"),     # ~18 km/h
    "drive": ("car", 13.4, "Driving"),   # ~48 km/h
}
MAX_ROUTED_M = 2_000_000   # don't ask the free router for continent-crossing routes
MAX_UI_POINTS = 400        # the map line doesn't need every point


class Path:
    """A list of (lat, lon) points with distances, so we can ask "where are we after N meters?"."""

    def __init__(self, points: list[tuple[float, float]]):
        if len(points) < 2:
            points = [points[0], points[0]]
        self.points = points
        self.cumulative = [0.0]
        for (a_lat, a_lon), (b_lat, b_lon) in zip(points, points[1:]):
            self.cumulative.append(self.cumulative[-1] + distance_m(a_lat, a_lon, b_lat, b_lon))
        self.total_m = self.cumulative[-1]

    def point_at(self, meters: float) -> tuple[float, float]:
        if meters <= 0:
            return self.points[0]
        if meters >= self.total_m:
            return self.points[-1]
        i = bisect.bisect_right(self.cumulative, meters) - 1
        seg = self.cumulative[i + 1] - self.cumulative[i]
        t = 0.0 if seg == 0 else (meters - self.cumulative[i]) / seg
        (a_lat, a_lon), (b_lat, b_lon) = self.points[i], self.points[i + 1]
        return a_lat + (b_lat - a_lat) * t, a_lon + (b_lon - a_lon) * t

    def for_map(self) -> list[list[float]]:
        step = max(1, len(self.points) // MAX_UI_POINTS)
        pts = self.points[::step]
        if pts[-1] != self.points[-1]:
            pts.append(self.points[-1])
        return [[round(lat, 6), round(lon, 6)] for lat, lon in pts]


class Router:
    def __init__(self, session: Optional[requests.Session] = None):
        self._http = session or requests.Session()
        self._http.headers.update({"User-Agent": USER_AGENT})
        self._lock = threading.Lock()
        self._last_request = 0.0

    def plan(self, start: tuple[float, float], end: tuple[float, float], mode: str,
             follow_streets: bool = True) -> tuple[Path, bool, Optional[str]]:
        """Returns (path, followed_streets, note). The note explains a straight-line fallback."""
        straight = Path([start, end])
        if not follow_streets:
            return straight, False, None
        if straight.total_m < 20:
            return straight, False, None
        if straight.total_m > MAX_ROUTED_M:
            return straight, False, "That's too far for a street route, so it's going in a straight line."
        profile = MODES[mode][0]
        try:
            with self._lock:
                wait = self._last_request + 1.0 - time.monotonic()
                if wait > 0:
                    time.sleep(wait)
                try:
                    resp = self._http.get(
                        ROUTER_URL.format(profile=profile, lat1=start[0], lon1=start[1], lat2=end[0], lon2=end[1]),
                        params={"overview": "full", "geometries": "geojson"},
                        timeout=15,
                    )
                finally:
                    self._last_request = time.monotonic()
            data = resp.json()
            if data.get("code") != "Ok" or not data.get("routes"):
                return straight, False, "No street route found between those points, so it's going in a straight line."
            coords = data["routes"][0]["geometry"]["coordinates"]
            points = [(float(lat), float(lon)) for lon, lat in coords]
        except (requests.RequestException, ValueError, KeyError, TypeError, AttributeError):
            return straight, False, "Couldn't reach the street routing service, so it's going in a straight line."
        # The route starts/ends at the nearest road; join it up to the exact points.
        return Path([start, *points, end]), True, None
