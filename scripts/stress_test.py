"""Stress test: hammer the real server with many clients while the iPhone misbehaves.

Runs the real app on the real web server (waitress) and the real phone-handling code. Only the
lowest layer, the iPhone itself, is pretend, and it's made deliberately unreliable: slow commands,
dropped connections, commands that hang longer than the app's timeout, and the phone vanishing
(unplugged) every so often. Place search and street routing are faked too, so we never flood
OpenStreetMap's free services.

    python scripts/stress_test.py                 # 40 clients for 60 s
    python scripts/stress_test.py --clients 80 --seconds 120

Exits non-zero if any check fails.
"""
from __future__ import annotations

import argparse
import asyncio
import collections
import json
import os
import random
import resource
import sys
import tempfile
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import requests  # noqa: E402
from pymobiledevice3 import exceptions as pmd_exc  # noqa: E402
from waitress import create_server  # noqa: E402

import phone  # noqa: E402
from app import create_app  # noqa: E402
from routing import Path  # noqa: E402
from storage import MAX_RECENTS, SavedPlaces  # noqa: E402

PORT = 5098
BASE = f"http://127.0.0.1:{PORT}"


# ---------------------------------------------------------------- the unreliable pretend iPhone

class World:
    """Shared knobs and counters for the pretend phone."""

    def __init__(self, drop_rate: float, hang_rate: float, unplug_every: float, unplug_for: float):
        self.drop_rate, self.hang_rate = drop_rate, hang_rate
        self.unplug_every, self.unplug_for = unplug_every, unplug_for
        self.start = time.monotonic()
        self.in_flight = 0
        self.max_in_flight = 0
        self.counts = collections.Counter()

    def unplugged(self) -> bool:
        t = (time.monotonic() - self.start) % self.unplug_every
        return t > self.unplug_every - self.unplug_for


def install_pretend_phone(world: World) -> None:
    """Swap the pymobiledevice3 functions phone.py calls for misbehaving fakes."""

    class Mux:
        serial, connection_type = "STRESS-0001", "USB"

    class Lockdown:
        product_version = "18.1"

        async def get_value(self, domain=None, key=None):
            return "Stress iPhone"

        async def get_developer_mode_status(self):
            return True

        async def get_enable_wifi_connections(self):
            return False

        async def close(self):
            pass

    class Rsd:
        async def __aenter__(self):
            if world.unplugged():
                raise pmd_exc.ConnectionTerminatedError()
            return self

        async def __aexit__(self, *exc):
            pass

    class Dvt(Rsd):
        def __init__(self, rsd):
            pass

    class Sim(Dvt):
        async def _command(self, name):
            world.in_flight += 1
            world.max_in_flight = max(world.max_in_flight, world.in_flight)
            try:
                world.counts[name] += 1
                if world.unplugged():
                    world.counts["unplugged"] += 1
                    raise pmd_exc.ConnectionTerminatedError()
                roll = random.random()
                if roll < world.hang_rate:
                    world.counts["hang"] += 1
                    await asyncio.sleep(25)  # longer than the app's 20 s timeout
                elif roll < world.hang_rate + world.drop_rate:
                    world.counts["drop"] += 1
                    raise pmd_exc.ConnectionTerminatedError()
                await asyncio.sleep(random.uniform(0, 0.03))
            finally:
                world.in_flight -= 1

        async def set(self, lat, lon):
            await self._command("set")

        async def clear(self):
            await self._command("clear")

    async def list_devices():
        return [] if world.unplugged() else [Mux()]

    async def create_using_usbmux(**kw):
        if world.unplugged():
            raise pmd_exc.NoDeviceConnectedError()
        return Lockdown()

    async def tunnels():
        return {} if world.unplugged() else {"STRESS-0001": [{}]}

    async def rsd_by_udid(udid):
        return None if world.unplugged() else Rsd()

    async def auto_mount(rsd):
        pass

    phone.list_devices = list_devices
    phone.create_using_usbmux = create_using_usbmux
    phone.get_tunneld_tunnels = tunnels
    phone.get_tunneld_device_by_udid = rsd_by_udid
    phone.auto_mount = auto_mount
    phone.DvtProvider = Dvt
    phone.LocationSimulation = Sim


class FakeSearch:
    def search(self, q, limit=6):
        time.sleep(random.uniform(0.01, 0.05))
        return [{"name": f"Place {q}", "detail": "Somewhere", "lat": random.uniform(-60, 60),
                 "lon": random.uniform(-170, 170)}]


class FakeRouter:
    def plan(self, start, end, mode, follow_streets=True):
        time.sleep(random.uniform(0.01, 0.05))
        pts = [start] + [((start[0] * (100 - i) + end[0] * i) / 100 + random.uniform(-1e-4, 1e-4),
                          (start[1] * (100 - i) + end[1] * i) / 100) for i in range(1, 100)] + [end]
        return Path(pts), True, None


# ---------------------------------------------------------------- the clients

def rand_coords():
    return {"lat": round(random.uniform(-60, 70), 5), "lon": round(random.uniform(-179, 179), 5)}


ACTIONS = [  # (weight, name, function(session) -> response)
    (30, "GET /api/state", lambda s: s.get(f"{BASE}/api/state")),
    (12, "GET /api/status", lambda s: s.get(f"{BASE}/api/status")),
    (10, "POST /api/location", lambda s: s.post(f"{BASE}/api/location", json=rand_coords())),
    (5, "POST /api/random", lambda s: s.post(f"{BASE}/api/random", json={})),
    (5, "POST /api/drift", lambda s: s.post(f"{BASE}/api/drift", json={"enabled": random.random() < 0.5})),
    (5, "POST /api/walk", lambda s: s.post(f"{BASE}/api/walk", json={**rand_coords(), "mode": random.choice(
        ["walk", "jog", "bike", "drive"])})),
    (3, "POST /api/walk/stop", lambda s: s.post(f"{BASE}/api/walk/stop", json={})),
    (8, "GET /api/saved", lambda s: s.get(f"{BASE}/api/saved")),
    (5, "POST /api/favorites", lambda s: s.post(f"{BASE}/api/favorites", json={"name": "Stress", **rand_coords()})),
    (3, "DELETE /api/favorites", None),  # needs an id: handled in the loop
    (4, "GET /api/search", lambda s: s.get(f"{BASE}/api/search", params={"q": f"cafe {random.randint(1, 500)}"})),
    (3, "POST /api/reset", lambda s: s.post(f"{BASE}/api/reset", json={})),
    (2, "GET /", lambda s: s.get(f"{BASE}/")),
    (1, "DELETE /api/recents", lambda s: s.delete(f"{BASE}/api/recents", json={})),
]
WEIGHTS = [a[0] for a in ACTIONS]

# A phone error the app explained in plain English is fine (503/400 with a message);
# anything else (500, no JSON, connection error) is a bug.
OK_CODES = {200, 400, 404, 503}


def client(stop: threading.Event, results: list, lock: threading.Lock):
    s = requests.Session()
    while not stop.is_set():
        _, name, fn = random.choices(ACTIONS, WEIGHTS)[0]
        t0 = time.monotonic()
        try:
            if name == "DELETE /api/favorites":
                favs = s.get(f"{BASE}/api/saved", timeout=60).json().get("favorites", [])
                if not favs:
                    continue
                r = s.delete(f"{BASE}/api/favorites/{random.choice(favs)['id']}", json={}, timeout=60)
            else:
                r = fn(s)
            code = r.status_code
            body_ok = code == 200 or (r.headers.get("content-type", "").startswith("application/json")
                                      and bool(r.json().get("error")))
            if name == "GET /":
                body_ok = code == 200 and "Location Jump" in r.text
            outcome = "ok" if code == 200 else ("handled" if code in OK_CODES and body_ok else f"BAD {code}")
            detail = "" if code == 200 else (r.json().get("error", "")[:80] if body_ok else r.text[:200])
        except Exception as exc:  # connection refused/reset, timeout
            outcome, detail = "BAD exception", f"{type(exc).__name__}: {exc}"[:200]
        with lock:
            results.append((name, outcome, time.monotonic() - t0, detail))


# ---------------------------------------------------------------- run + report

def rss_mb() -> float:
    usage = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return usage / (1024 * 1024) if sys.platform == "darwin" else usage / 1024  # bytes on macOS, KiB on Linux


def current_rss_mb() -> float:
    try:
        with open("/proc/self/statm") as f:
            return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 1e6
    except OSError:
        return rss_mb()


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(len(values) * p / 100))] * 1000 if values else 0.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--clients", type=int, default=40)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--drop-rate", type=float, default=0.03)
    parser.add_argument("--hang-rate", type=float, default=0.005)
    parser.add_argument("--json", help="also write the results here")
    args = parser.parse_args()

    random.seed(1)
    world = World(args.drop_rate, args.hang_rate, unplug_every=20, unplug_for=3)
    install_pretend_phone(world)
    data_file = os.path.join(tempfile.mkdtemp(), "saved.json")
    controller = phone.PhoneController()
    controller.KEEPALIVE_S = 1.0  # keep the background loop busy too
    controller.WALK_TICK_S = 0.2
    app = create_app(controller, SavedPlaces(data_file), FakeSearch(), FakeRouter(), on_quit=lambda: None)
    server = create_server(app, host="127.0.0.1", port=PORT, threads=16)  # same as app.py
    threading.Thread(target=server.run, daemon=True).start()
    time.sleep(0.5)

    threads_before, rss_before = threading.active_count(), current_rss_mb()
    results: list = []
    lock = threading.Lock()
    stop = threading.Event()
    samples = []  # (t, threads, rss, ping ms) every 2 s
    print(f"Stress test: {args.clients} clients for {args.seconds:.0f} s against {BASE} "
          f"(drops {args.drop_rate:.0%}, hangs {args.hang_rate:.1%}, unplugged 3 s of every 20 s)", flush=True)
    workers = [threading.Thread(target=client, args=(stop, results, lock), daemon=True) for _ in range(args.clients)]
    t_start = time.monotonic()
    for w in workers:
        w.start()
    while time.monotonic() - t_start < args.seconds:
        time.sleep(2)
        t0 = time.monotonic()
        try:
            requests.get(f"{BASE}/api/ping", timeout=30)
            ping = (time.monotonic() - t0) * 1000
        except Exception:
            ping = float("inf")
        samples.append((time.monotonic() - t_start, threading.active_count() - len(workers), current_rss_mb(), ping))
        print(f"  t={samples[-1][0]:5.0f}s  requests={len(results):6d}  ping={ping:7.1f} ms  "
              f"rss={samples[-1][2]:6.1f} MB  unplugged={world.unplugged()}", flush=True)
    stop.set()
    for w in workers:
        w.join(timeout=90)
    elapsed = time.monotonic() - t_start

    # Let the background loop settle, then make sure everything recovers cleanly.
    time.sleep(max(0.0, world.unplug_every - (time.monotonic() - world.start) % world.unplug_every))  # plugged in
    time.sleep(2)
    final_reset = requests.post(f"{BASE}/api/reset", json={}, timeout=60)
    for _ in range(20):  # the background loop should notice within a moment
        if controller._maintain_task is None or controller._maintain_task.done():
            break
        time.sleep(0.25)
    state = requests.get(f"{BASE}/api/state", timeout=10).json()
    saved_ok, recents_ok = True, True
    try:
        saved = json.load(open(data_file))
        recents_ok = len(saved["recents"]) <= MAX_RECENTS
    except Exception:
        saved_ok = False
    maintain_done = controller._maintain_task is None or controller._maintain_task.done()
    threads_after = threading.active_count() - sum(w.is_alive() for w in workers)
    server.close()

    # ---- report
    by_name = collections.defaultdict(list)
    outcomes = collections.Counter()
    bad = collections.Counter()
    for name, outcome, dt, detail in results:
        by_name[name].append(dt)
        outcomes[outcome.split()[0]] += 1
        if outcome.startswith("BAD"):
            bad[(name, outcome, detail)] += 1
    handled = collections.Counter(d for _, o, _, d in results if o == "handled")
    lat_state = [dt for n, o, dt, _ in results if n == "GET /api/state"]

    print(f"\n{len(results)} requests in {elapsed:.0f} s ({len(results) / elapsed:.0f}/s)  "
          f"ok={outcomes['ok']}  handled phone errors={outcomes['handled']}  BAD={outcomes['BAD']}")
    print(f"\n{'endpoint':24s} {'count':>6s} {'p50 ms':>8s} {'p95 ms':>8s} {'p99 ms':>8s} {'max ms':>9s}")
    for name in sorted(by_name, key=lambda n: -len(by_name[n])):
        v = by_name[name]
        print(f"{name:24s} {len(v):6d} {pct(v, 50):8.1f} {pct(v, 95):8.1f} {pct(v, 99):8.1f} {max(v) * 1000:9.1f}")
    print("\nPretend phone:", dict(world.counts), f"max simultaneous commands={world.max_in_flight}")
    print("Most common explained errors:")
    for msg, n in handled.most_common(5):
        print(f"  {n:5d}  {msg}")
    if bad:
        print("UNEXPECTED responses:")
        for (name, outcome, detail), n in bad.most_common(10):
            print(f"  {n:5d}  {name}  {outcome}  {detail}")

    pings = [s[3] for s in samples]
    rss = [s[2] for s in samples]
    checks = {
        "no 500s / crashes / dropped connections": outcomes["BAD"] == 0,
        "never two commands to the phone at once": world.max_in_flight <= 1,
        "server stayed responsive (ping p95 < 1 s)": pct([p / 1000 for p in pings], 95) < 1000,
        "status polling stayed fast (p95 < 1 s)": pct(lat_state, 95) < 1000,
        "final reset worked": final_reset.status_code == 200 and not state["simulating"],
        "background loop stopped after reset": maintain_done,
        "saved_places.json is valid": saved_ok,
        f"recents capped at {MAX_RECENTS}": recents_ok,
        "no thread leak": threads_after <= threads_before + 2,
        "memory stable (growth < 50 MB)": (max(rss) - rss_before) < 50 if rss else True,
    }
    print(f"\nThreads before/after: {threads_before}/{threads_after}   "
          f"memory: start {rss_before:.0f} MB, peak {max(rss or [0]):.0f} MB, end {rss[-1] if rss else 0:.0f} MB   "
          f"ping p50/p95/max: {pct([p / 1000 for p in pings], 50):.0f}/{pct([p / 1000 for p in pings], 95):.0f}/"
          f"{max(pings or [0]):.0f} ms")
    print("\nChecks:")
    for name, ok in checks.items():
        print(f"  {'PASS' if ok else 'FAIL'}  {name}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump({"requests": len(results), "elapsed_s": elapsed, "outcomes": dict(outcomes),
                       "latency_ms": {n: {"p50": pct(v, 50), "p95": pct(v, 95), "p99": pct(v, 99),
                                          "max": max(v) * 1000, "count": len(v)} for n, v in by_name.items()},
                       "phone": dict(world.counts), "max_in_flight": world.max_in_flight,
                       "samples": samples, "checks": checks}, f, indent=2)
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
