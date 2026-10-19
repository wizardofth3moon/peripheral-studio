"""Battery levels of the wireless devices, published to the desktop.

Runs inside the remap engine (which is always on). Each device with a known level gets a
virtual battery (uhid.VirtualBattery), so it shows in the system power menu with
low-battery warnings; the engine also reports the levels to the app.
"""
from __future__ import annotations

import logging
import threading
import time

from . import blackshark, uhid
from .devices import NagaV2HyperSpeed

log = logging.getLogger("peripheral-studio.batteries")
NAGA_EVERY = 60  # seconds; the receiver answers cheaply, and asleep it simply doesn't


class Batteries:
    def __init__(self, on_change=None):
        self.on_change = on_change or (lambda levels: None)
        self.levels: dict[str, dict] = {}  # device id -> {"name", "kind", "percent", "charging"}
        self._virtual: dict[str, uhid.VirtualBattery] = {}
        self._lock = threading.Lock()
        self._headset: blackshark.BlackSharkV3 | None = None
        self._uhid_ok = True
        threading.Thread(target=self._run, name="batteries", daemon=True).start()

    def snapshot(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self.levels.items()}

    def _publish(self, did: str, name: str, kind: str, percent, charging, product: int):
        with self._lock:
            entry = {"name": name, "kind": kind, "percent": percent, "charging": bool(charging)}
            if self.levels.get(did) == entry:
                return
            self.levels[did] = entry
        if percent is not None and self._uhid_ok:
            try:
                vb = self._virtual.get(did)
                if vb is None:
                    vb = self._virtual[did] = uhid.VirtualBattery(kind, name, f"peripheral-studio-{did}", 0x1532, product)
                vb.update(int(round(percent)), bool(charging))
            except OSError as ex:
                # no access to /dev/uhid (udev rule not installed): the app still shows the level
                self._uhid_ok = False
                log.warning("desktop battery unavailable: %s", ex)
        self.on_change(self.snapshot())

    def _naga(self):
        dev = NagaV2HyperSpeed.open()
        if dev is None:
            return
        level = dev.battery_level  # 0 while the mouse sleeps
        if level > 0:
            self._publish("1532-00b4", dev.NAME, "mouse", level, dev.is_charging, dev.PID)

    def _headset_changed(self):
        h = self._headset
        if h and h.percent is not None:
            self._publish(f"1532-{h.product:04x}", h.NAME, "headset", h.percent, h.charging, h.product)

    def _run(self):
        while True:
            try:
                self._naga()
            except Exception as ex:  # keep the thread alive across unplugs
                log.debug("naga battery: %s", ex)
            if self._headset is None or self._headset._stop.is_set():
                self._headset = blackshark.BlackSharkV3.open(on_change=self._headset_changed)
            time.sleep(NAGA_EVERY)
