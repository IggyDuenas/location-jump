"""Tests that run without an iPhone: the phone connection is replaced by a fake."""
import asyncio
import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import phone  # noqa: E402
from app import create_app  # noqa: E402
from places import PLACES  # noqa: E402
from storage import SavedPlaces  # noqa: E402
from pymobiledevice3 import exceptions as pmd_exc  # noqa: E402


class FakeSim:
    def __init__(self):
        self.calls = []

    async def set(self, lat, lon):
        self.calls.append(("set", lat, lon))

    async def clear(self):
        self.calls.append(("clear",))


@pytest.fixture
def controller(monkeypatch):
    c = phone.PhoneController()
    sim = FakeSim()
    opens = {"n": 0}

    async def fake_open():
        if c.unplugged:
            raise pmd_exc.NoDeviceConnectedError()
        opens["n"] += 1
        c._sim = sim
        return sim

    async def fake_close():
        c._sim = None

    async def fake_status():
        s = {"connected": True, "ready": True, "code": "ok", "message": "Connected to Test (iOS 18.0). Ready.",
             "device": {"name": "Test", "ios": "18.0", "udid": "x"}}
        s.update(c._state())
        return s

    monkeypatch.setattr(c, "_open_session", fake_open)
    monkeypatch.setattr(c, "_close_session", fake_close)
    monkeypatch.setattr(c, "_status", fake_status)
    c.fake_sim, c.opens, c.unplugged = sim, opens, False
    return c


class FakeSearch:
    def __init__(self):
        self.queries = []

    def search(self, q):
        self.queries.append(q)
        if q == "boom":
            raise phone.PhoneError("Couldn't reach the place search.", "search_failed")
        return [{"name": "Eiffel Tower", "detail": "Paris, France", "lat": 48.8584, "lon": 2.2945}]


@pytest.fixture
def saved(tmp_path):
    return SavedPlaces(str(tmp_path / "saved.json"))


@pytest.fixture
def searcher():
    return FakeSearch()


class FakeRouter:
    def __init__(self):
        self.calls = []

    def plan(self, start, end, mode, follow_streets=True):
        from routing import Path
        self.calls.append((start, end, mode, follow_streets))
        mid = ((start[0] + end[0]) / 2 + 0.0005, (start[1] + end[1]) / 2)
        return Path([start, mid, end]), follow_streets, None if follow_streets else "straight"


@pytest.fixture
def router():
    return FakeRouter()


@pytest.fixture
def client(controller, saved, searcher, router):
    return create_app(controller, saved, searcher, router).test_client()


def test_places_list_is_big_and_valid():
    assert len(PLACES) >= 40
    assert len({p[0] for p in PLACES}) == len(PLACES)
    for name, lat, lon in PLACES:
        phone.validate_coords(lat, lon)


def test_index_page(client):
    html = client.get("/").get_data(as_text=True)
    assert "leaflet" in html and "Random Location" in html and "Reset to Real Location" in html


def test_set_location(client, controller):
    r = client.post("/api/location", json={"lat": 48.8584, "lon": 2.2945})
    assert r.status_code == 200
    body = r.get_json()
    assert body["simulating"] and body["anchor"] == [48.8584, 2.2945]
    assert controller.fake_sim.calls == [("set", 48.8584, 2.2945)]


@pytest.mark.parametrize("payload", [{}, {"lat": "abc", "lon": 1}, {"lat": 91, "lon": 0}, {"lat": 0, "lon": -181}])
def test_set_location_rejects_bad_input(client, payload):
    r = client.post("/api/location", json=payload)
    assert r.status_code == 400
    assert r.get_json()["code"] == "bad_input"


def test_random_location_uses_a_known_place(client, controller):
    body = client.post("/api/random", json={}).get_json()
    assert (body["label"], *body["anchor"]) in PLACES
    assert controller.fake_sim.calls[-1][0] == "set"


def test_reset_clears_and_disconnects(client, controller):
    client.post("/api/location", json={"lat": 1, "lon": 2})
    body = client.post("/api/reset", json={}).get_json()
    assert not body["simulating"] and body["current"] is None
    assert controller.fake_sim.calls[-1] == ("clear",)
    assert controller._sim is None


def test_status_reports_simulated_coordinates(client):
    client.post("/api/location", json={"lat": 35.6595, "lon": 139.7005, "label": "Shibuya"})
    body = client.get("/api/status").get_json()
    assert body["ready"] and body["current"] == [35.6595, 139.7005] and body["label"] == "Shibuya"


def test_drift_stays_close_to_anchor(controller):
    controller.set_location(51.5007, -0.1246)
    for _ in range(500):
        controller.current = controller._next_drift_point()
        assert phone.distance_m(51.5007, -0.1246, *controller.current) <= controller.DRIFT_RADIUS_M + 0.01
    assert controller.current != (51.5007, -0.1246)


def test_drift_loop_moves_location_and_snaps_back(controller, monkeypatch):
    monkeypatch.setattr(controller, "DRIFT_INTERVAL_S", 1.05)  # sleep is interval +/- 1s
    controller.set_location(-33.8568, 151.2153)
    controller.set_drift(True)
    controller.run(asyncio.sleep(2.5))
    drift_sets = [c for c in controller.fake_sim.calls[1:] if c[0] == "set"]
    assert drift_sets, "drift should have moved the location"
    for _, lat, lon in drift_sets:
        assert 0 < phone.distance_m(-33.8568, 151.2153, lat, lon) <= controller.DRIFT_RADIUS_M + 0.01
    state = controller.set_drift(False)
    assert state["current"] == [-33.8568, 151.2153]
    assert not state["drift"]


def test_set_reconnects_once_after_dropped_connection(controller):
    class FlakySim(FakeSim):
        fails = 1

        async def set(self, lat, lon):
            if self.fails:
                self.fails -= 1
                raise pmd_exc.ConnectionTerminatedError()
            await super().set(lat, lon)

    controller.fake_sim.__class__ = FlakySim
    controller.fake_sim.fails = 1
    controller.set_location(10, 20)
    assert controller.opens["n"] == 2
    assert controller.current == (10, 20)


def test_phone_errors_become_friendly_api_errors(client, controller, monkeypatch):
    async def no_tunnel():
        raise pmd_exc.TunneldConnectionError()

    monkeypatch.setattr(controller, "_open_session", no_tunnel)
    r = client.post("/api/location", json={"lat": 1, "lon": 2})
    assert r.status_code == 503
    body = r.get_json()
    assert body["code"] == "no_tunnel" and "sudo" in body["error"]


@pytest.mark.parametrize("exc, code", [
    (pmd_exc.TunneldConnectionError(), "no_tunnel"),
    (pmd_exc.NoDeviceConnectedError(), "not_connected"),
    (pmd_exc.ConnectionFailedToUsbmuxdError(), "not_connected"),
    (pmd_exc.PairingDialogResponsePendingError(), "not_trusted"),
    (pmd_exc.UserDeniedPairingError(), "not_trusted"),
    (pmd_exc.NotPairedError(), "not_trusted"),
    (pmd_exc.PasswordRequiredError(), "locked"),
    (pmd_exc.DeveloperModeIsNotEnabledError(), "dev_mode_off"),
    (pmd_exc.ConnectionTerminatedError(), "connection_lost"),
    (asyncio.TimeoutError(), "timeout"),
    (ValueError("weird"), "error"),
])
def test_explain(exc, code):
    err = phone.explain(exc)
    assert err.code == code
    assert err.message


def test_offset_meters_is_accurate():
    lat, lon = phone.offset_meters(40.0, -74.0, 3.0, 4.0)
    assert abs(phone.distance_m(40.0, -74.0, lat, lon) - 5.0) < 0.05


# ---------- auto-reconnect ----------

def wait_until(controller, predicate, timeout=6.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        controller.run(asyncio.sleep(0.05))
    return False


@pytest.fixture
def fast(controller, monkeypatch):
    monkeypatch.setattr(controller, "KEEPALIVE_S", 0.1)
    monkeypatch.setattr(controller, "RETRY_S", 0.1)
    return controller


def test_keepalive_resends_location(fast):
    fast.set_location(1.0, 2.0)
    assert wait_until(fast, lambda: len(fast.fake_sim.calls) >= 3)
    assert all(call == ("set", 1.0, 2.0) for call in fast.fake_sim.calls)


def test_reconnects_and_restores_location_after_unplug(fast):
    fast.set_location(48.8584, 2.2945, "Eiffel Tower, Paris")
    fast.unplugged = True
    fast._sim = None  # the old connection died with the cable
    assert wait_until(fast, lambda: fast.reconnecting)
    state = fast._state()
    assert state["simulating"] and state["anchor"] == [48.8584, 2.2945]
    assert "Reconnecting automatically" in state["last_error"] and "Eiffel Tower" in state["last_error"]
    assert fast._mounted_for is None  # will remount the Developer Disk Image if the phone rebooted

    calls_before = len(fast.fake_sim.calls)
    fast.unplugged = False
    assert wait_until(fast, lambda: not fast.reconnecting)
    assert fast.fake_sim.calls[calls_before] == ("set", 48.8584, 2.2945)
    assert fast._state()["last_error"] is None


def test_reset_while_reconnecting_just_stops(fast):
    fast.set_location(1.0, 2.0)
    fast.unplugged = True
    fast._sim = None
    assert wait_until(fast, lambda: fast.reconnecting)
    state = fast.clear_location()  # must not fail: the phone is already on its real GPS
    assert not state["simulating"] and not state["reconnecting"]
    calls = len(fast.fake_sim.calls)
    fast.unplugged = False
    fast.run(asyncio.sleep(0.4))
    assert len(fast.fake_sim.calls) == calls  # nothing re-applied after reset


def test_status_reports_reconnecting(client, fast):
    client.post("/api/location", json={"lat": 1, "lon": 2})
    fast.unplugged = True
    fast._sim = None
    assert wait_until(fast, lambda: fast.reconnecting)
    assert client.get("/api/status").get_json()["reconnecting"] is True


# ---------- favorites & recents ----------

def test_setting_locations_adds_recents(client, saved):
    client.post("/api/location", json={"lat": 1, "lon": 2, "label": "  Somewhere   nice "})
    client.post("/api/random", json={})
    recents = client.get("/api/saved").get_json()["recents"]
    assert len(recents) == 2
    assert (recents[0]["name"], recents[0]["lat"], recents[0]["lon"]) in PLACES
    assert recents[1]["name"] == "Somewhere nice"


def test_failed_set_is_not_added_to_recents(client, controller):
    controller.unplugged = True
    assert client.post("/api/location", json={"lat": 1, "lon": 2}).status_code == 503
    assert client.get("/api/saved").get_json()["recents"] == []


def test_recents_dedupe_and_cap(saved):
    for i in range(20):
        saved.add_recent(f"P{i}", float(i), 0.0)
    assert [r["name"] for r in saved.all()["recents"]][:2] == ["P19", "P18"]  # newest first
    saved.add_recent(None, 10.00001, 0.0)  # same spot as P10, within a few meters
    recents = saved.all()["recents"]
    assert len(recents) == 12
    assert recents[0]["name"] == "P10"  # moved to the top, kept its name
    assert [r["name"] for r in recents].count("P10") == 1


def test_favorites_api(client):
    r = client.post("/api/favorites", json={"name": "Office", "lat": 40.7, "lon": -74.0})
    fav = r.get_json()["favorite"]
    assert r.status_code == 200 and fav["name"] == "Office"
    # Saving the same spot again doesn't duplicate it.
    client.post("/api/favorites", json={"name": None, "lat": 40.70001, "lon": -74.0})
    assert len(client.get("/api/saved").get_json()["favorites"]) == 1
    assert client.post("/api/favorites", json={"lat": 999, "lon": 0}).status_code == 400
    assert client.delete(f"/api/favorites/{fav['id']}", json={}).get_json()["favorites"] == []
    assert client.delete(f"/api/favorites/{fav['id']}", json={}).status_code == 404


def test_clear_recents(client):
    client.post("/api/location", json={"lat": 1, "lon": 2})
    assert client.delete("/api/recents", json={}).get_json()["recents"] == []


def test_saved_places_persist_and_survive_a_broken_file(tmp_path):
    path = str(tmp_path / "saved.json")
    SavedPlaces(path).add_favorite("Home", 1.0, 2.0)
    assert SavedPlaces(path).all()["favorites"][0]["name"] == "Home"
    with open(path, "w") as f:
        f.write("{not json")
    assert SavedPlaces(path).all() == {"favorites": [], "recents": []}
    assert os.path.exists(path + ".broken")


# ---------- place search ----------

def test_search_endpoint(client, searcher):
    body = client.get("/api/search?q=eiffel").get_json()
    assert body["results"][0]["name"] == "Eiffel Tower"
    assert searcher.queries == ["eiffel"]
    r = client.get("/api/search?q=boom")
    assert r.status_code == 502 and "place search" in r.get_json()["error"]


class FakeResponse:
    def __init__(self, status, payload):
        self.status_code, self._payload = status, payload
        self.ok = 200 <= status < 300

    def json(self):
        return self._payload


class FakeSession:
    def __init__(self, responses):
        self.headers, self.calls, self.responses = {}, [], list(responses)

    def get(self, url, params=None, timeout=None):
        self.calls.append((time.monotonic(), params))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


NOMINATIM_ROW = {"name": "Eiffel Tower", "display_name": "Eiffel Tower, Avenue Anatole France, Paris, France",
                 "lat": "48.8582599", "lon": "2.2945006"}


def test_place_search_formats_caches_and_rate_limits():
    from search import PlaceSearch, USER_AGENT
    session = FakeSession([FakeResponse(200, [NOMINATIM_ROW]), FakeResponse(200, [])])
    s = PlaceSearch(session)
    assert session.headers["User-Agent"] == USER_AGENT
    first = s.search("  Eiffel   tower ")
    assert first == [{"name": "Eiffel Tower", "detail": "Avenue Anatole France, Paris, France",
                      "lat": 48.8582599, "lon": 2.2945006}]
    assert s.search("eiffel tower") == first  # cached: no second request
    assert len(session.calls) == 1
    assert s.search("nowhere at all") == []
    assert session.calls[1][0] - session.calls[0][0] >= 0.95  # at most one request per second
    assert s.search("x") == []  # too short, no request
    assert len(session.calls) == 2


@pytest.mark.parametrize("response, text", [
    (FakeResponse(429, None), "busy"),
    (FakeResponse(500, None), "error 500"),
    (FakeResponse(200, {"oops": 1}), "unexpected"),
])
def test_place_search_errors(response, text):
    from search import PlaceSearch
    with pytest.raises(phone.PhoneError) as info:
        PlaceSearch(FakeSession([response])).search("paris")
    assert text in info.value.message


def test_place_search_offline():
    import requests
    from search import PlaceSearch
    with pytest.raises(phone.PhoneError) as info:
        PlaceSearch(FakeSession([requests.ConnectionError()])).search("paris")
    assert "internet" in info.value.message


# ---------- walk mode ----------

from routing import MODES, Path, Router  # noqa: E402


def test_path_interpolation():
    p = Path([(0.0, 0.0), (0.0, 0.001), (0.001, 0.001)])
    assert 220 < p.total_m < 225  # two ~111 m legs
    assert p.point_at(-5) == (0.0, 0.0) and p.point_at(10_000) == (0.001, 0.001)
    lat, lon = p.point_at(p.cumulative[1] / 2)
    assert abs(lat) < 1e-9 and abs(lon - 0.0005) < 1e-9
    lat, lon = p.point_at(p.cumulative[1] + 55.66)
    assert abs(lon - 0.001) < 1e-9 and 0.00049 < lat < 0.00051


def test_path_for_map_is_capped_and_keeps_the_end():
    pts = [(0.0, i * 0.0001) for i in range(2001)]
    m = Path(pts).for_map()
    assert len(m) <= 402 and m[0] == [0.0, 0.0] and m[-1] == [0.0, 0.2]


ROUTE_OK = {"code": "Ok", "routes": [{"geometry": {"coordinates": [[2.2950, 48.8580], [2.2960, 48.8590], [2.3000, 48.8600]]}}]}


def test_router_follows_streets():
    session = FakeSession([FakeResponse(200, ROUTE_OK)])
    path, routed, note = Router(session).plan((48.8584, 2.2945), (48.8606, 2.3006), "walk")
    assert routed and note is None
    assert path.points[0] == (48.8584, 2.2945) and path.points[-1] == (48.8606, 2.3006)
    assert path.points[1] == (48.8580, 2.2950)  # lon/lat swapped into lat/lon
    assert len(session.calls) == 1


@pytest.mark.parametrize("response, note", [
    (FakeResponse(200, {"code": "NoRoute", "routes": []}), "No street route"),
    (FakeResponse(502, "<html>"), "Couldn't reach"),
])
def test_router_falls_back_to_a_straight_line(response, note):
    path, routed, msg = Router(FakeSession([response])).plan((48.0, 2.0), (48.01, 2.01), "bike")
    assert not routed and note in msg and len(path.points) == 2


def test_router_offline_and_skips():
    import requests
    path, routed, msg = Router(FakeSession([requests.ConnectionError()])).plan((48.0, 2.0), (48.01, 2.01), "drive")
    assert not routed and "Couldn't reach" in msg
    session = FakeSession([])
    assert Router(session).plan((48.0, 2.0), (48.01, 2.01), "walk", follow_streets=False)[2] is None
    assert "too far" in Router(session).plan((48.8, 2.3), (35.6, 139.7), "walk")[2]
    assert session.calls == []


@pytest.fixture
def walker(fast, monkeypatch):
    monkeypatch.setattr(fast, "WALK_TICK_S", 0.05)
    return fast


def test_walk_moves_along_the_path_and_arrives(walker):
    walker.set_location(0.0, 0.0, "Start")
    path = Path([(0.0, 0.0), (0.0, 0.001)])  # ~111 m
    state = walker.start_walk(path, 100.0, "Finish", "walk", True)
    assert state["walk"]["total_m"] > 100 and state["label"] is None
    assert wait_until(walker, lambda: walker.walk is not None and 0 < walker.walk["done_m"] < path.total_m)
    assert 0 < walker.current[1] < 0.001
    assert wait_until(walker, lambda: walker.walk is None)
    assert walker.anchor == (0.0, 0.001) and walker.label == "Finish"
    sent = [c for c in walker.fake_sim.calls if c[0] == "set"]
    lons = [c[2] for c in sent]
    assert lons == sorted(lons)  # never moves backwards


def test_walk_pauses_while_disconnected(walker):
    walker.set_location(0.0, 0.0)
    walker.start_walk(Path([(0.0, 0.0), (0.0, 0.01)]), 50.0, None, "drive", True)
    assert wait_until(walker, lambda: walker.walk["done_m"] > 0)
    walker.unplugged = True
    walker._sim = None
    assert wait_until(walker, lambda: walker.reconnecting)
    paused_at = walker.walk["done_m"]
    walker.run(asyncio.sleep(0.4))
    assert walker.walk["done_m"] <= paused_at + 50 * 3.0 * 0.06  # at most one capped step, not 0.4 s of travel per tick
    walker.unplugged = False
    assert wait_until(walker, lambda: not walker.reconnecting and walker.walk["done_m"] > paused_at)


def test_stop_walk_stays_put_and_set_location_cancels(walker):
    walker.set_location(0.0, 0.0)
    walker.start_walk(Path([(0.0, 0.0), (0.0, 0.01)]), 20.0, "Far", "walk", True)
    assert wait_until(walker, lambda: walker.walk["done_m"] > 0)
    state = walker.stop_walk()
    assert state["walk"] is None and state["anchor"] == list(walker.current) and 0 < state["anchor"][1] < 0.01
    walker.start_walk(Path([(0.0, 0.0), (0.0, 0.01)]), 20.0, "Far", "walk", True)
    assert walker.set_location(5.0, 5.0)["walk"] is None


def test_walk_api(client, router):
    r = client.post("/api/walk", json={"lat": 1, "lon": 1})
    assert r.status_code == 400 and "starting location" in r.get_json()["error"]
    client.post("/api/location", json={"lat": 0, "lon": 0})
    body = client.post("/api/walk", json={"lat": 0.01, "lon": 0.01, "label": "Cafe", "mode": "bike"}).get_json()
    assert body["walk"]["mode"] == "bike" and body["walk"]["label"] == "Cafe"
    assert body["walk"]["eta_s"] == round(body["walk"]["left_m"] / MODES["bike"][1])
    assert router.calls[-1] == ((0.0, 0.0), (0.01, 0.01), "bike", True)
    assert client.get("/api/saved").get_json()["recents"][0]["name"] == "Cafe"
    assert client.get("/api/state").get_json()["walk"]["id"] == body["walk"]["id"]
    body = client.post("/api/walk", json={"lat": 0.02, "lon": 0.02, "mode": "teleport", "follow_streets": False}).get_json()
    assert body["walk"]["mode"] == "walk" and body["walk"]["note"] == "straight"
    assert client.post("/api/walk/stop", json={}).get_json()["walk"] is None


# ---------- app shell: ping, quit, and the localhost guard ----------

def test_ping(client):
    assert client.get("/api/ping").get_json()["app"] == "location-jump"


def test_quit_resets_phone_then_stops(controller, saved, searcher, router):
    quits = []
    client = create_app(controller, saved, searcher, router, on_quit=lambda: quits.append(1)).test_client()
    client.post("/api/location", json={"lat": 1, "lon": 2})
    assert client.post("/api/quit", json={}).get_json()["ok"]
    assert quits == [1]
    assert controller.fake_sim.calls[-1] == ("clear",) and controller.anchor is None


@pytest.mark.parametrize("method, path", [
    ("post", "/api/random"), ("post", "/api/reset"), ("post", "/api/quit"), ("delete", "/api/recents"),
])
def test_changes_require_json_so_other_websites_cant_trigger_them(client, method, path):
    # A plain HTML form on some other website could send exactly this.
    r = getattr(client, method)(path, data="x=1", content_type="application/x-www-form-urlencoded")
    assert r.status_code == 415
    assert client.get("/api/status").get_json()["simulating"] is False


@pytest.mark.parametrize("host, ok", [
    ("localhost:5000", True), ("127.0.0.1:5001", True), ("[::1]:5000", True), ("localhost", True),
    ("evil.example:5000", False), ("192.168.1.20:5000", False),
])
def test_only_localhost_host_header(client, host, ok):
    r = client.get("/api/ping", headers={"Host": host})
    assert (r.status_code == 200) is ok
