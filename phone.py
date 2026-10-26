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
        return PhoneError(
            "The iOS 17 tunnel isn't running. Open Terminal in this folder and run:  "
            f"{TUNNEL_START_CMD}  (it will ask for your Mac password), then try again.",
            "no_tunnel",
        )
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


def offset_meters(lat: float, lon: float, north_m: float, east_m: float) -> tuple[float, float]:
    """Move a coordinate by a number of meters north and east."""
    dlat = north_m / 111_320.0
    dlon = east_m / (111_320.0 * max(math.cos(math.radians(lat)), 1e-6))
    return lat + dlat, lon + dlon


def validate_coords(lat: Any, lon: Any) -> tuple[float, float]:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        raise PhoneError("Latitude and longitude must be numbers, e.g. 48.8584 and 2.2945.", "bad_input")
    if math.isnan(lat) or math.isnan(lon) or not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        raise PhoneError("Latitude must be between -90 and 90, longitude between -180 and 180.", "bad_input")
    return lat, lon


class PhoneController:
    DRIFT_INTERVAL_S = 5.0     # how often the location wiggles
    DRIFT_RADIUS_M = 8.0       # max distance from the chosen point
    DRIFT_STEP_M = 3.0         # max distance per wiggle
    KEEPALIVE_S = 4.0          # how often we re-send the location to notice a dropped connection
    RETRY_S = 3.0              # how often we try to reconnect after a drop

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="phone-loop", daemon=True)
        self._thread.start()
        self._lock: Optional[asyncio.Lock] = None
        self._stack: Optional[contextlib.AsyncExitStack] = None
        self._sim: Optional[LocationSimulation] = None
        self._udid: Optional[str] = None
        self._mounted_for: Optional[str] = None
        self._maintain_task: Optional[asyncio.Task] = None
        self._wake: Optional[asyncio.Event] = None

        # State reported to the UI
        self.anchor: Optional[tuple[float, float]] = None   # the point the user chose
        self.current: Optional[tuple[float, float]] = None  # what the phone is showing (incl. drift)
        self.label: Optional[str] = None
        self.drift_enabled = False
        self.reconnecting = False   # lost the phone while simulating; retrying in the background
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

    def _state(self) -> dict:
        return {
            "simulating": self.anchor is not None,
            "anchor": list(self.anchor) if self.anchor else None,
            "current": list(self.current) if self.current else None,
            "label": self.label,
            "drift": self.drift_enabled,
            "reconnecting": self.reconnecting,
            "last_error": self.last_error,
        }

    async def _status(self) -> dict:
        """Check every link in the chain and report the first thing that's wrong."""
        result = {"connected": False, "ready": False, "code": "ok", "message": "", "device": None}
        result.update(self._state())

        try:
            devices = [d for d in await list_devices() if d.is_usb]
        except Exception as exc:  # usbmuxd not reachable
            err = explain(exc)
            result.update(code=err.code, message=err.message)
            return result
        if not devices:
            result.update(code="not_connected",
                          message="No iPhone found. Plug it in with a USB cable and unlock it.")
            return result

        udid = devices[0].serial
        try:
            lockdown = await create_using_usbmux(serial=udid, pair_timeout=1)
            try:
                name = await lockdown.get_value(key="DeviceName")
                version = lockdown.product_version
                dev_mode = await lockdown.get_developer_mode_status()
            finally:
                with contextlib.suppress(Exception):
                    await lockdown.close()
        except Exception as exc:
            err = explain(exc)
            result.update(code=err.code, message=err.message)
            return result

        result["connected"] = True
        result["device"] = {"name": name, "ios": version, "udid": udid}

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
        if udid not in tunnels:
            result.update(code="tunnel_waiting",
                          message="The tunnel is running but hasn't reached your iPhone yet. Keep the "
                                  "phone unlocked and plugged in; this usually takes a few seconds.")
            return result

        result.update(ready=True, message=f"Connected to {name} (iOS {version}). Ready.")
        return result

    async def _close_session(self) -> None:
        stack, self._stack, self._sim = self._stack, None, None
        if stack is not None:
            with contextlib.suppress(Exception):
                await stack.aclose()

    async def _open_session(self) -> LocationSimulation:
        """Open (or reuse) the long-lived developer connection through the tunnel."""
        if self._sim is not None:
            return self._sim

        devices = [d for d in await list_devices() if d.is_usb]
        if not devices:
            raise pmd_exc.NoDeviceConnectedError()
        udid = devices[0].serial

        rsd = await get_tunneld_device_by_udid(udid)
        if rsd is None:
            raise PhoneError(
                "The tunnel is running but isn't connected to your iPhone yet. Keep it unlocked and "
                "plugged in, wait a few seconds, then try again. If this keeps happening, restart the "
                f"tunnel: sudo pkill -f 'pymobiledevice3 remote tunneld'; {TUNNEL_START_CMD}",
                "tunnel_waiting",
            )

        stack = contextlib.AsyncExitStack()
        try:
            await stack.enter_async_context(rsd)
            if self._mounted_for != udid:
                # Location simulation needs the Developer Disk Image mounted. This downloads it the
                # first time (needs internet) and is a no-op if it's already mounted.
                try:
                    await auto_mount(rsd)
                except pmd_exc.AlreadyMountedError:
                    pass
                self._mounted_for = udid
            dvt = await stack.enter_async_context(DvtProvider(rsd))
            sim = await stack.enter_async_context(LocationSimulation(dvt))
        except BaseException:
            await stack.aclose()
            raise
        self._stack, self._sim, self._udid = stack, sim, udid
        return sim

    async def _push(self, lat: float, lon: float) -> None:
        """Send coordinates to the phone, reconnecting once if the session went stale."""
        for attempt in (1, 2):
            try:
                sim = await self._open_session()
                await asyncio.wait_for(sim.set(lat, lon), timeout=20)
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
        async with self._get_lock():
            try:
                await self._push(lat, lon)
            except Exception as exc:
                err = explain(exc)
                self.last_error = err.message
                raise err
            self.anchor, self.label = (lat, lon), label
            self.reconnecting = False
        self._ensure_maintainer()
        return self._state()

    async def _clear_location(self) -> dict:
        async with self._get_lock():
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
                await asyncio.wait_for(sim.clear(), timeout=20)
            except Exception as exc:
                await self._close_session()
                err = explain(exc)
                self.last_error = err.message
                raise err
            finally:
                self.anchor = self.current = self.label = None
                self.reconnecting = False
            await self._close_session()
            self.last_error = None
        return self._state()

    async def _set_drift(self, enabled: bool) -> dict:
        was_enabled, self.drift_enabled = self.drift_enabled, bool(enabled)
        if self._wake is not None:
            self._wake.set()  # start (or stop) drifting now, not after the current wait
        if was_enabled and not self.drift_enabled and self.anchor is not None and not self.reconnecting:
            # Snap back to the exact chosen point.
            async with self._get_lock():
                with contextlib.suppress(Exception):
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
                target = self._next_drift_point() if self.drift_enabled else self.anchor
                try:
                    await self._push(*target)
                except Exception as exc:
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


def distance_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance in meters."""
    r = 6_371_000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))
