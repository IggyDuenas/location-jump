"""Talks to the iPhone: connection checks, setting/clearing the simulated location, and drift.

pymobiledevice3 is fully async, while Flask is not. So this module runs its own asyncio
event loop on a background thread, and Flask calls into it through small blocking wrappers.

Important detail: on iOS 17+ the simulated location only stays in effect while the
developer (DVT) connection that set it is open. That's why we keep one long-lived session
open after "Set Location" instead of connecting, setting and disconnecting.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import math
import os
import random
import threading
import time
from typing import Any, Optional

from pymobiledevice3 import exceptions as pmd_exc
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.location_simulation import LocationSimulation
from pymobiledevice3.services.mobile_image_mounter import auto_mount
from pymobiledevice3.tunneld.api import get_tunneld_device_by_udid, get_tunneld_tunnels
from pymobiledevice3.usbmux import list_devices

log = logging.getLogger(__name__)

TUNNEL_START_CMD = "sudo .venv/bin/python -m pymobiledevice3 remote tunneld -d"
TUNNEL_SERVICE_PLIST = "/Library/LaunchDaemons/com.github.iggyduenas.locationjump.tunneld.plist"


def tunnel_fix_hint() -> str:
    """How to get the tunnel going again, depending on whether the always-on service is installed."""
    if os.path.exists(TUNNEL_SERVICE_PLIST):
        return ("Restart the tunnel service: open Terminal in the Location Jump folder and run "
                "./tunnel-service.sh restart")
    return ("Open Location Jump again (or run ./start.sh) and enter your Mac password when asked; or in Terminal "
            f"run: {TUNNEL_START_CMD}. Tip: ./tunnel-service.sh install keeps it running so you're never asked again.")


class PhoneError(Exception):
    """An error with a plain-English message that's safe to show in the UI."""

    def __init__(self, message: str, code: str = "error"):
        super().__init__(message)
        self.message = message
        self.code = code


def explain(exc: BaseException) -> PhoneError:
    """Turn any exception from pymobiledevice3 into a plain-English PhoneError."""
    if isinstance(exc, PhoneError):
        return exc
    if isinstance(exc, pmd_exc.TunneldConnectionError):
        return PhoneError(f"The iOS 17 tunnel isn't running. {tunnel_fix_hint()}", "no_tunnel")
    if isinstance(exc, (pmd_exc.NoDeviceConnectedError, pmd_exc.DeviceNotFoundError,
                        pmd_exc.ConnectionFailedToUsbmuxdError)):
        return PhoneError(
            "No iPhone found. Plug it into your Mac with a USB cable and unlock it.",
            "not_connected",
        )
    if isinstance(exc, pmd_exc.PairingDialogResponsePendingError):
        return PhoneError(
            "Your iPhone is asking \"Trust This Computer?\". Unlock it, tap Trust, "
            "and enter your passcode.",
            "not_trusted",
        )
    if isinstance(exc, pmd_exc.UserDeniedPairingError):
        return PhoneError(
            "You tapped \"Don't Trust\" on the iPhone. Unplug and replug the cable, "
            "then tap Trust this time.",
            "not_trusted",
        )
    if isinstance(exc, pmd_exc.PasswordRequiredError):
        return PhoneError("Unlock your iPhone (enter its passcode), then try again.", "locked")
    if isinstance(exc, (pmd_exc.NotTrustedError, pmd_exc.NotPairedError, pmd_exc.PairingError,
                        pmd_exc.InvalidHostIDError, pmd_exc.FatalPairingError)):
        return PhoneError(
            "Your iPhone doesn't trust this Mac yet. Unlock it, unplug and replug the cable, "
            "and tap Trust when asked.",
            "not_trusted",
        )
    if isinstance(exc, (pmd_exc.DeveloperModeIsNotEnabledError, pmd_exc.DeveloperModeError)):
        return PhoneError(
            "Developer Mode is off. On the iPhone: Settings > Privacy & Security > "
            "Developer Mode > turn it on, then restart the phone.",
            "dev_mode_off",
        )
    if isinstance(exc, pmd_exc.DeveloperDiskImageNotFoundError):
        return PhoneError(
            "Couldn't download the Developer Disk Image for your iOS version. Check your "
            "internet connection and try again.",
            "ddi_failed",
        )
    if isinstance(exc, pmd_exc.PasscodeRequiredError):
        return PhoneError("Unlock your iPhone, then try again.", "locked")
    if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
        return PhoneError(
            "The iPhone took too long to answer. Unlock it and try again.", "timeout"
        )
    if isinstance(exc, (pmd_exc.ConnectionTerminatedError, ConnectionError, BrokenPipeError,
                        asyncio.IncompleteReadError, OSError)):
        return PhoneError(
            "Lost the connection to the iPhone. Check the cable, keep the phone unlocked, "
            "and try again.",
            "connection_lost",
        )
    return PhoneError(f"Something unexpected went wrong: {type(exc).__name__}: {exc}", "error")


def same_device(a: Optional[str], b: Optional[str]) -> bool:
    """Device IDs are sometimes written with dashes and sometimes without."""
    return bool(a and b) and a.replace("-", "") == b.replace("-", "")


def offset_meters(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Move a coordinate by a number of meters north and east."""
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon


def validate_coords(lat: Any, lon: Any) -> tuple[float, float]:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise PhoneError("Latitude and longitude must be numbers, e.g. 48.8584 and 2.2945.", "bad_input") from None
    if math.isnan(lat) or math.isnan(lon) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise PhoneError("Latitude must be between -90 and 90, longitude between -180 and 180.", "bad_input")
    return lat, lon


class PhoneController:
    DRIFT_INTERVAL_S = 5.0     # how often the location wiggles
    DRIFT_RADIUS_M = 8.0       # max distance from the chosen point
    DRIFT_STEP_M = 3.0         # max distance per wiggle
    KEEPALIVE_S = 4.0          # how often we re-send the location to notice a dropped connection
    RETRY_S = 3.0              # how often we try to reconnect after a drop
    WALK_TICK_S = 1.0          # how often the location moves while walking
    PHONE_TIMEOUT_S = 8.0      # longest one command to the phone may take (they normally take well under 1 s)
    BUSY_WAIT_S = 5.0          # how long a button press waits for a busy phone before saying so

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="phone-loop", daemon=True)
        self._thread.start()
        self._lock: Optional[asyncio.Lock] = None
        self._stack: Optional[contextlib.AsyncExitStack] = None
        self._sim: Optional[LocationSimulation] = None
        self._udid: Optional[str] = None
        self.selected: Optional[str] = None  # the phone the user picked when several are connected
        self._names: dict[str, dict] = {}     # udid -> {"name", "ios"}, so we don't ask every phone every poll
        self._name_failed: dict[str, float] = {}
        self._mounted_for: Optional[str] = None
        self._maintain_task: Optional[asyncio.Task] = None
        self._wake: Optional[asyncio.Event] = None
        self._waiting = 0  # button presses currently waiting for their turn with the phone

        # State reported to the UI
        self.anchor: Optional[tuple[float, float]] = None   # the point the user chose
        self.current: Optional[tuple[float, float]] = None  # what the phone is showing (incl. drift)
        self.label: Optional[str] = None
        self.drift_enabled = False
        self.reconnecting = False   # lost the phone while simulating; retrying in the background
        self.walk: Optional[dict] = None  # set while moving along a route (walk mode)
        self.last_error: Optional[str] = None

    # ---------- blocking API used by Flask ----------

    def run(self, coro, timeout: float = 90) -> Any:
        return asyncio.run_coroutine_threadsafe(coro, self._loop).result(timeout)

    def status(self) -> dict:
        return self.run(self._status(), timeout=30)

    def set_location(self, lat: float, lon: float, label: Optional[str] = None) -> dict:
        return self.run(self._set_location(lat, lon, label))

    def clear_location(self) -> dict:
        return self.run(self._clear_location())

    def set_drift(self, enabled: bool) -> dict:
        return self.run(self._set_drift(enabled))

    def start_walk(self, path, speed_mps: float, label: Optional[str], mode: str,
                   routed: bool, note: Optional[str] = None) -> dict:
        return self.run(self._start_walk(path, speed_mps, label, mode, routed, note))

    def stop_walk(self) -> dict:
        return self.run(self._stop_walk())

    def set_wifi(self, enabled: bool) -> dict:
        return self.run(self._set_wifi(enabled), timeout=30)

    def select_device(self, udid: Optional[str]) -> dict:
        return self.run(self._select_device(udid), timeout=60)

    def state(self) -> dict:
        return self.run(self._get_state(), timeout=10)

    async def _get_state(self) -> dict:
        return self._state()

    def current_position(self) -> Optional[tuple[float, float]]:
        """Where the phone is being shown right now, or None if nothing is simulated."""
        if self.anchor is None:
            return None
        return self.current or self.anchor

    def shutdown(self) -> None:
        if self.anchor is None:
            return
        print("Putting your iPhone back to its real location…")
        with contextlib.suppress(Exception):
            self.run(self._clear_location(), timeout=10)

    # ---------- internals (run on the background loop) ----------

    def _get_lock(self) -> asyncio.Lock:
        if self._lock is None:
            self._lock = asyncio.Lock()
        return self._lock

    @contextlib.asynccontextmanager
    async def _turn(self):
        """Take a turn talking to the phone, for button presses.

        If the phone is stuck on an earlier command, give up after BUSY_WAIT_S with a clear message
        instead of queueing: queued requests would tie up the web server and freeze the whole page.
        For the same reason only one button press waits at a time; others are told "busy" at once
        (which also stops a frantic double-click from sending the same command twice).
        """
        busy = PhoneError("Your iPhone is still busy with the previous command (it may not be responding). "
                          "Try again in a moment.", "busy")
        lock = self._get_lock()
        if lock.locked() and self._waiting:
            raise busy
        self._waiting += 1
        try:
            acquire = asyncio.ensure_future(lock.acquire())
            done, _ = await asyncio.wait({acquire}, timeout=self.BUSY_WAIT_S)
        finally:
            self._waiting -= 1
        if not done:
            acquire.cancel()
            try:
                await acquire
            except asyncio.CancelledError:
                pass
            else:
                lock.release()  # we got the lock just as we gave up; hand it straight back
            raise busy
        try:
            yield
        finally:
            lock.release()

    def _state(self) -> dict:
        return {
            "simulating": self.anchor is not None,
            "anchor": list(self.anchor) if self.anchor else None,
            "current": list(self.current) if self.current else None,
            "label": self.label,
            "drift": self.drift_enabled,
            "reconnecting": self.reconnecting,
            "last_error": self.last_error,
            "walk": self._walk_state(),
        }

    def _walk_state(self) -> Optional[dict]:
        w = self.walk
        if w is None:
            return None
        path = w["path"]
        left = max(path.total_m - w["done_m"], 0.0)
        return {
            "id": w["id"], "label": w["label"], "mode": w["mode"], "routed": w["routed"], "note": w["note"],
            "total_m": round(path.total_m, 1), "done_m": round(w["done_m"], 1),
            "left_m": round(left, 1), "eta_s": round(left / w["speed"]),
            "path": w["map_path"], "destination": list(path.points[-1]),
        }

    @staticmethod
    async def _list_phones() -> list[dict]:
        """Every phone macOS can see, once each, noting whether it's on the cable, Wi-Fi, or both."""
        phones: dict[str, dict] = {}
        for d in await list_devices():
            p = phones.setdefault(d.serial.replace("-", ""), {"udid": d.serial, "usb": False, "wifi": False})
            if d.connection_type == "USB":
                p["usb"] = True
            elif d.connection_type == "Network":
                p["wifi"] = True
        return list(phones.values())

    def _choose_message(self, count: int) -> str:
        return (f"{count} devices are connected. Choose which one Location Jump should use (click the "
                "status at the top, or pick one below).")

    async def _find_device(self) -> tuple[Optional[str], Optional[str]]:
        """The phone to use: (udid, "USB" or "Network"), preferring the cable. (None, None) if it isn't here.

        With several phones and no choice made yet, we refuse to guess: spoofing the wrong phone
        would be bad. If the chosen phone goes away we wait for it instead of switching to another.
        """
        phones = await self._list_phones()
        if self.selected:
            phone = next((p for p in phones if same_device(p["udid"], self.selected)), None)
        elif len(phones) > 1:
            raise PhoneError(self._choose_message(len(phones)), "choose_device")
        else:
            phone = phones[0] if phones else None
        if phone is None:
            return None, None
        return phone["udid"], "USB" if phone["usb"] else "Network"

    def _tunnel_key(self, udid: Optional[str], tunnels: dict) -> Optional[str]:
        """tunneld's key for this phone (IDs may be written with or without dashes)."""
        if not tunnels:
            return None
        udid = udid or self.selected
        if udid is None:
            # A Wi-Fi tunnel macOS itself doesn't list. Only use it if there's no doubt which phone it is.
            return next(iter(tunnels)) if len(tunnels) == 1 else None
        return next((k for k in tunnels if same_device(k, udid)), None)

    async def _describe(self, phone: dict) -> dict:
        """Name and iOS version for the device list (cached; asking a phone takes a moment)."""
        key = phone["udid"].replace("-", "")
        if key not in self._names and time.monotonic() - self._name_failed.get(key, 0) > 30:
            try:
                lockdown = await create_using_usbmux(serial=phone["udid"],
                                                     connection_type="USB" if phone["usb"] else "Network",
                                                     pair_timeout=1)
                try:
                    self._names[key] = {"name": await lockdown.get_value(key="DeviceName"),
                                        "ios": lockdown.product_version}
                finally:
                    with contextlib.suppress(Exception):
                        await lockdown.close()
            except Exception:
                self._name_failed[key] = time.monotonic()  # e.g. not trusted yet; try again later
        info = self._names.get(key, {"name": "Untrusted device", "ios": None})
        return {**phone, **info, "selected": same_device(phone["udid"], self.selected)}

    async def _status(self) -> dict:
        """Check every link in the chain and report the first thing that's wrong."""
        result = {"connected": False, "ready": False, "code": "ok", "message": "", "device": None,
                  "devices": [], "selected": self.selected}
        result.update(self._state())

        try:
            phones = await self._list_phones()
            if len(phones) > 1 or self.selected:
                result["devices"] = [await self._describe(p) for p in phones]
            udid, kind = await self._find_device()
        except Exception as exc:  # usbmuxd not reachable, or several phones and none chosen yet
            err = explain(exc)
            result.update(code=err.code, message=err.message)
            return result
        if udid is None and self.selected:
            chosen = self._names.get(self.selected.replace("-", ""), {}).get("name", "The iPhone you chose")
            with contextlib.suppress(Exception):
                if self._tunnel_key(None, await get_tunneld_tunnels()):
                    result.update(connected=True, ready=True, message=f"Connected to {chosen} over Wi-Fi. Ready.",
                                  device={"name": chosen, "ios": None, "udid": self.selected, "connection": "wifi",
                                          "wifi_enabled": True})
                    return result
            result.update(code="not_connected",
                          message=f"{chosen} isn't connected. Plug it in and unlock it (or pick another device at "
                                  "the top).")
            return result
        if udid is None:
            # tunneld can also reach a phone over Wi-Fi on its own (Bonjour), without macOS listing it.
            with contextlib.suppress(Exception):
                if self._tunnel_key(None, await get_tunneld_tunnels()):
                    result.update(connected=True, ready=True, message="Connected to your iPhone over Wi-Fi. Ready.",
                                  device={"name": "iPhone", "ios": None, "udid": None, "connection": "wifi",
                                          "wifi_enabled": True})
                    return result
            result.update(code="not_connected",
                          message="No iPhone found. Plug it in with a USB cable and unlock it (or, with Wi-Fi mode "
                                  "on, keep it unlocked on the same Wi-Fi network as this Mac).")
            return result

        try:
            lockdown = await create_using_usbmux(serial=udid, connection_type=kind, pair_timeout=1)
            try:
                name = await lockdown.get_value(key="DeviceName")
                version = lockdown.product_version
                dev_mode = await lockdown.get_developer_mode_status()
                wifi_enabled = await lockdown.get_enable_wifi_connections()
            finally:
                with contextlib.suppress(Exception):
                    await lockdown.close()
        except Exception as exc:
            err = explain(exc)
            result.update(code=err.code, message=err.message)
            return result

        result["connected"] = True
        self._names[udid.replace("-", "")] = {"name": name, "ios": version}
        result["device"] = {"name": name, "ios": version, "udid": udid,
                            "connection": "usb" if kind == "USB" else "wifi", "wifi_enabled": wifi_enabled}

        if int(str(version).split(".")[0]) < 17:
            result.update(code="old_ios", message=f"This tool is built for iOS 17+, but this phone runs iOS {version}.")
            return result
        if not dev_mode:
            err = explain(pmd_exc.DeveloperModeIsNotEnabledError())
            result.update(code=err.code, message=err.message)
            return result

        try:
            tunnels = await get_tunneld_tunnels()
        except Exception as exc:
            err = explain(exc)
            result.update(code=err.code, message=err.message)
            return result
        if self._tunnel_key(udid, tunnels) is None:
            how = "plugged in" if kind == "USB" else "on the same Wi-Fi network"
            result.update(code="tunnel_waiting",
                          message=f"The tunnel is running but hasn't reached your iPhone yet. Keep the phone "
                                  f"unlocked and {how}; this usually takes a few seconds"
                                  f"{'' if kind == 'USB' else ' (up to a minute over Wi-Fi)'}.")
            return result

        via = "" if kind == "USB" else " over Wi-Fi"
        result.update(ready=True, message=f"Connected to {name} (iOS {version}){via}. Ready.")
        return result

    async def _set_wifi(self, enabled: bool) -> dict:
        """Turn "Show this iPhone when on Wi-Fi" on or off. Apple only allows changing it over USB."""
        udid, kind = await self._find_device()
        if kind != "USB":
            raise PhoneError("Plug your iPhone in with the cable to change Wi-Fi mode (it can only be switched "
                             "over USB).", "needs_usb")
        try:
            lockdown = await create_using_usbmux(serial=udid, connection_type="USB", pair_timeout=1)
            try:
                await lockdown.set_enable_wifi_connections(bool(enabled))
            finally:
                with contextlib.suppress(Exception):
                    await lockdown.close()
        except PhoneError:
            raise
        except Exception as exc:
            raise explain(exc) from exc
        return await self._status()

    async def _select_device(self, udid: Optional[str]) -> dict:
        """Switch to another phone. The current phone is put back to its real location first."""
        if not udid:
            raise PhoneError("Choose a device.", "bad_input")
        if not any(same_device(p["udid"], udid) for p in await self._list_phones()):
            raise PhoneError("That device isn't connected anymore.", "not_connected")
        if not same_device(udid, self.selected):
            if self.anchor is not None:
                with contextlib.suppress(Exception):
                    await self._clear_location()
            async with self._turn():
                await self._close_session()
                self._mounted_for = None
                self.selected = udid
        return await self._status()

    async def _close_session(self) -> None:
        stack, self._stack, self._sim = self._stack, None, None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()

    async def _open_session(self) -> LocationSimulation:
        """Open (or reuse) the long-lived developer connection through the tunnel."""
        if self._sim is not None:
            return self._sim

        udid, _kind = await self._find_device()
        key = self._tunnel_key(udid, await get_tunneld_tunnels())
        if udid is None and key is None:
            raise pmd_exc.NoDeviceConnectedError()

        rsd = await get_tunneld_device_by_udid(key) if key else None
        if rsd is None:
            raise PhoneError(
                "The tunnel is running but isn't connected to your iPhone yet. Keep it unlocked (and "
                "plugged in, or on the same Wi-Fi in Wi-Fi mode), wait a few seconds, then try again. "
                "If this keeps happening, restart the "
                "tunnel: ./stop.sh then ./start.sh (or ./tunnel-service.sh restart if you installed the "
                "tunnel service).",
                "tunnel_waiting",
            )

        stack = contextlib.AsyncExitStack()
        try:
            await stack.enter_async_context(rsd)
            if self._mounted_for != key:
                # Location simulation needs the Developer Disk Image mounted. This downloads it the
                # first time (needs internet) and is a no-op if it's already mounted.
                try:
                    await auto_mount(rsd)
                except pmd_exc.AlreadyMountedError:
                    pass
                self._mounted_for = key
            dvt = await stack.enter_async_context(DvtProvider(rsd))
            sim = await stack.enter_async_context(LocationSimulation(dvt))
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._sim, self._udid = stack, sim, key
        if self.selected is None:
            # Remember which phone we're spoofing, so plugging in a second one later changes nothing.
            self.selected = udid or key
        return sim

    async def _push(self, lat: float, lon: float) -> None:
        """Send coordinates to the phone, reconnecting once if the session went stale."""
        for attempt in (1, 2):
            try:
                sim = await self._open_session()
                await asyncio.wait_for(sim.set(lat, lon), timeout=self.PHONE_TIMEOUT_S)
                self.current = (lat, lon)
                self.last_error = None
                return
            except PhoneError:
                raise
            except Exception as exc:
                log.warning("set location failed (attempt %d): %r", attempt, exc)
                await self._close_session()
                if attempt == 2 or isinstance(exc, (pmd_exc.TunneldConnectionError,
                                                    pmd_exc.NoDeviceConnectedError,
                                                    pmd_exc.ConnectionFailedToUsbmuxdError,
                                                    pmd_exc.DeveloperModeIsNotEnabledError)):
                    raise

    async def _set_location(self, lat: float, lon: float, label: Optional[str]) -> dict:
        lat, lon = validate_coords(lat, lon)
        async with self._turn():
            try:
                await self._push(lat, lon)
            except Exception as exc:
                err = explain(exc)
                self.last_error = err.message
                raise err from (exc if err is not exc else None)
            self.anchor, self.label = (lat, lon), label
            self.reconnecting = False
            self.walk = None
        self._ensure_maintainer()
        return self._state()

    async def _clear_location(self) -> dict:
        async with self._turn():
            self.walk = None
            if self.reconnecting:
                # The connection is already gone, so the phone is back on its real GPS.
                # Just stop trying to restore the fake location.
                self.anchor = self.current = self.label = None
                self.reconnecting = False
                self.last_error = None
                await self._close_session()
                return self._state()
            try:
                # Clear even if we don't think we're simulating, in case an old session left one behind.
                sim = await self._open_session()
                await asyncio.wait_for(sim.clear(), timeout=self.PHONE_TIMEOUT_S)
            except Exception as exc:
                await self._close_session()
                err = explain(exc)
                self.last_error = err.message
                raise err from (exc if err is not exc else None)
            finally:
                self.anchor = self.current = self.label = None
                self.reconnecting = False
                if self._wake is not None:
                    self._wake.set()  # let the background loop see it's over and stop now
            await self._close_session()
            self.last_error = None
        return self._state()

    async def _set_drift(self, enabled: bool) -> dict:
        was_enabled, self.drift_enabled = self.drift_enabled, bool(enabled)
        if self._wake is not None:
            self._wake.set()  # start (or stop) drifting now, not after the current wait
        if was_enabled and not self.drift_enabled and self.anchor is not None and not self.reconnecting:
            # Snap back to the exact chosen point (best effort: the next keep-alive does it anyway).
            with contextlib.suppress(Exception):
                async with self._turn():
                    await self._push(*self.anchor)
        return self._state()

    def _next_drift_point(self) -> tuple[float, float]:
        """A small random step from the current point that never wanders far from the anchor."""
        assert self.anchor is not None
        alat, alon = self.anchor
        clat, clon = self.current or self.anchor
        for _ in range(10):
            angle = random.uniform(0, 2 * math.pi)
            step = random.uniform(1.0, self.DRIFT_STEP_M)
            lat, lon = offset_meters(clat, clon, step * math.cos(angle), step * math.sin(angle))
            if distance_m(alat, alon, lat, lon) <= self.DRIFT_RADIUS_M:
                return lat, lon
        # Wandered to the edge: step back toward the anchor.
        return (clat + alat) / 2, (clon + alon) / 2

    async def _start_walk(self, path, speed_mps, label, mode, routed, note) -> dict:
        if self.anchor is None:
            raise PhoneError("Set a starting location first (Set Location or Random), then travel from there.",
                             "bad_input")
        self.walk = {
            "id": random.getrandbits(32), "path": path, "map_path": path.for_map(), "speed": float(speed_mps),
            "label": label, "mode": mode, "routed": routed, "note": note, "done_m": 0.0, "last_tick": None,
        }
        self.label = None
        self._ensure_maintainer()
        self._wake.set()  # start moving now
        return self._state()

    async def _stop_walk(self) -> dict:
        async with self._turn():
            if self.walk is not None:
                # Stay wherever we got to.
                self.walk = None
                if self.current is not None:
                    self.anchor = self.current
        return self._state()

    def _walk_step(self, walk: dict, now: float) -> tuple[float, float]:
        """Advance along the route by the time since the last successful step."""
        if walk["last_tick"] is not None:
            elapsed = min(now - walk["last_tick"], 3.0)  # a slow phone call shouldn't make us jump ahead
            walk["done_m"] = min(walk["path"].total_m, walk["done_m"] + walk["speed"] * elapsed)
        return walk["path"].point_at(walk["done_m"])

    def _ensure_maintainer(self) -> None:
        if self._wake is None:
            self._wake = asyncio.Event()
        if self._maintain_task is None or self._maintain_task.done():
            self._maintain_task = asyncio.ensure_future(self._maintain_loop())

    async def _maintain_loop(self) -> None:
        """Runs while a location is set: drifts it, keeps the connection alive, and auto-reconnects.

        Re-sending the location every few seconds is how we notice the phone went away (unplugged,
        locked during setup, tunnel restarted). When that happens the phone falls back to its real
        GPS, so we keep retrying and put the fake location back as soon as the phone is reachable.
        """
        while self.anchor is not None:
            if self.reconnecting:
                delay = self.RETRY_S
            elif self.walk is not None:
                delay = self.WALK_TICK_S
            elif self.drift_enabled:
                delay = self.DRIFT_INTERVAL_S + random.uniform(-1.0, 1.0)
            else:
                delay = self.KEEPALIVE_S
            self._wake.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), timeout=delay)

            async with self._get_lock():
                if self.anchor is None:
                    break
                walk, now = self.walk, time.monotonic()
                if walk is not None:
                    target = self._walk_step(walk, now)
                elif self.drift_enabled:
                    target = self._next_drift_point()
                else:
                    target = self.anchor
                try:
                    await self._push(*target)
                except Exception as exc:
                    if walk is not None:
                        walk["last_tick"] = None  # pause the walk until we're reconnected
                    if not self.reconnecting:
                        log.warning("lost the iPhone while simulating: %r", exc)
                    self.reconnecting = True
                    # If the phone rebooted, the Developer Disk Image has to be mounted again.
                    self._mounted_for = None
                    place = self.label or "your fake location"
                    self.last_error = (f"Lost connection to your iPhone: {explain(exc).message} "
                                       f"Reconnecting automatically; {place} will come back as soon "
                                       "as the phone is reachable.")
                else:
                    if self.reconnecting:
                        log.info("reconnected to the iPhone; location restored")
                    self.reconnecting = False
                    if walk is not None and walk is self.walk:
                        walk["last_tick"] = now
                        self.anchor = target
                        if walk["done_m"] >= walk["path"].total_m:
                            # Arrived: stay at the destination (drift/keep-alive take over from here).
                            self.walk = None
                            self.anchor, self.label = walk["path"].points[-1], walk["label"]


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
