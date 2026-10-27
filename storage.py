"""Favorites and recent places, saved as a small JSON file on your Mac."""
from __future__ import annotations

import json
import logging
import os
import threading
import time
import uuid
from typing import Optional

from phone import distance_m

log = logging.getLogger(__name__)

MAX_RECENTS = 12
MAX_FAVORITES = 200  # every save rewrites the file, so keep it a sensible size
SAME_PLACE_M = 30.0   # closer than this counts as the same place
MAX_NAME_LEN = 80


def _clean_name(name: Optional[str]) -> Optional[str]:
    name = " ".join(str(name or "").split())[:MAX_NAME_LEN]
    return name or None


class FavoritesFullError(Exception):
    pass


class SavedPlaces:
    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()
        self._data = self._load()

    def _load(self) -> dict:
        try:
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except FileNotFoundError:
            data = {}
        except (OSError, ValueError) as exc:
            # Don't lose a damaged file silently: keep a copy, then start fresh.
            log.warning("couldn't read %s (%s); starting with an empty list", self.path, exc)
            try:
                os.replace(self.path, self.path + ".broken")
            except OSError:
                pass
            data = {}
        return {
            "favorites": [p for p in data.get("favorites", []) if isinstance(p, dict)],
            "recents": [p for p in data.get("recents", []) if isinstance(p, dict)],
        }

    def _save(self) -> None:
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, self.path)  # atomic, so a crash never leaves a half-written file

    @staticmethod
    def _entry(name: Optional[str], lat: float, lon: float) -> dict:
        return {"id": uuid.uuid4().hex[:12], "name": _clean_name(name), "lat": lat, "lon": lon,
                "saved_at": int(time.time())}

    @staticmethod
    def _find(items: list, lat: float, lon: float) -> Optional[dict]:
        for item in items:
            if distance_m(item["lat"], item["lon"], lat, lon) < SAME_PLACE_M:
                return item
        return None

    def all(self) -> dict:
        with self._lock:
            return json.loads(json.dumps(self._data))

    def add_favorite(self, name: Optional[str], lat: float, lon: float) -> dict:
        with self._lock:
            existing = self._find(self._data["favorites"], lat, lon)
            if existing:
                if _clean_name(name):
                    existing["name"] = _clean_name(name)
                    self._save()
                return dict(existing)
            if len(self._data["favorites"]) >= MAX_FAVORITES:
                raise FavoritesFullError(f"You have {MAX_FAVORITES} favorites already. Remove some to add more.")
            entry = self._entry(name, lat, lon)
            self._data["favorites"].insert(0, entry)
            self._save()
            return dict(entry)

    def remove_favorite(self, fav_id: str) -> bool:
        with self._lock:
            before = len(self._data["favorites"])
            self._data["favorites"] = [p for p in self._data["favorites"] if p.get("id") != fav_id]
            if len(self._data["favorites"]) == before:
                return False
            self._save()
            return True

    def add_recent(self, name: Optional[str], lat: float, lon: float) -> None:
        with self._lock:
            recents = self._data["recents"]
            existing = self._find(recents, lat, lon)
            if existing:
                recents.remove(existing)
                name = name or existing.get("name")
            recents.insert(0, self._entry(name, lat, lon))
            del recents[MAX_RECENTS:]
            self._save()

    def clear_recents(self) -> None:
        with self._lock:
            self._data["recents"] = []
            self._save()
