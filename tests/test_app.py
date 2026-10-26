"""Tests that run without an iPhone: the phone connection is replaced by a fake."""
import asyncio
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import phone  # noqa: E402
from app import create_app  # noqa: E402
from places import PLACES  # noqa: E402
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
    c.fake_sim, c.opens = sim, opens
    return c


@pytest.fixture
def client(controller):
    return create_app(controller).test_client()


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
    body = client.post("/api/random").get_json()
    assert (body["label"], *body["anchor"]) in PLACES
    assert controller.fake_sim.calls[-1][0] == "set"


def test_reset_clears_and_disconnects(client, controller):
    client.post("/api/location", json={"lat": 1, "lon": 2})
    body = client.post("/api/reset").get_json()
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
