"""Finds the Razer peripherals and joins their two halves.

Each physical device shows up twice: as one or more evdev nodes (what input-remapper grabs
and remaps) and, for lighting, DPI and battery, as a settings device: Peripheral Studio's own driver
(hardware/) where it has one, otherwise OpenRazer if that is installed.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections import defaultdict
from dataclasses import dataclass, field

import evdev
from evdev import ecodes

from . import layouts


@dataclass
class Node:
    """One /dev/input/eventN of a device."""

    path: str
    name: str
    hash: str  # input-remapper's origin_hash for this node
    keys: frozenset[int]
    rels: frozenset[int]


@dataclass
class Device:
    id: str  # "<vid>-<pid>", the engine's and the profiles' name for the device
    key: str  # evdev name of its main node
    name: str  # display name
    kind: str  # mouse / keypad / keyboard / headset / other
    nodes: list[Node] = field(default_factory=list)
    razer: object | None = None  # openrazer.client device
    layout: layouts.Layout | None = None

    def has(self, cap: str) -> bool:
        try:
            return bool(self.razer is not None and self.razer.has(cap))
        except Exception:
            return False

    def nodes_with(self, ev_type: int, code: int) -> list[Node]:
        pool = (lambda n: n.keys) if ev_type == ecodes.EV_KEY else (lambda n: n.rels)
        return [n for n in self.nodes if code in pool(n)]


def device_hash(dev: evdev.InputDevice) -> str:
    """input-remapper's origin_hash (inputremapper.utils.get_device_hash)."""
    s = str(dev.capabilities(absinfo=False)) + dev.name
    return hashlib.md5(s.encode()).hexdigest().lower()


def _norm(name: str) -> str:
    name = re.sub(r"\(.*?\)", "", name)
    name = re.sub(r"\b(razer|inc|consumer control)\b", "", name, flags=re.I)
    return re.sub(r"\s+", " ", name).strip().lower()


def _native_devices() -> dict:
    """Devices Peripheral Studio drives itself (hardware/), by USB product id."""
    if os.environ.get("PERIPHERAL_STUDIO_BACKEND") == "openrazer":
        return {}
    try:
        from .hardware.devices import native_devices
        return native_devices()
    except Exception:
        return {}


def _openrazer_devices() -> list:
    """Fallback for devices without a native driver, when OpenRazer happens to be installed."""
    try:
        from openrazer.client import DeviceManager

        manager = DeviceManager()
        manager.sync_effects = False
        return list(manager.devices)
    except Exception:
        return []


def discover() -> list[Device]:
    # Group the evdev nodes per USB interface set, the way input-remapper does (by phys).
    groups: dict[str, list[evdev.InputDevice]] = defaultdict(list)
    for path in evdev.list_devices():
        try:
            dev = evdev.InputDevice(path)
        except OSError:
            continue
        if (dev.info.vendor != 0x1532 or dev.info.product in (0x5053, 0x5054)
                or dev.name.startswith(("input-remapper", "Peripheral Studio")) or "(Peripheral Studio)" in dev.name):
            continue
        phys = (dev.phys or dev.name).split("/input")[0]
        groups[phys].append(dev)

    native = _native_devices()
    razers = None  # OpenRazer is only asked when a device has no native driver
    devices: list[Device] = []
    for phys, devs in groups.items():
        # The group key is the name most of its nodes share; extra nodes like
        # "... Consumer Control" carry a suffix.
        key = min((d.name for d in devs), key=len)
        nodes = []
        for d in sorted(devs, key=lambda d: d.path):
            caps = d.capabilities(absinfo=False)
            nodes.append(Node(d.path, d.name, device_hash(d),
                              frozenset(caps.get(ecodes.EV_KEY, [])),
                              frozenset(caps.get(ecodes.EV_REL, []))))
            d.close()

        razer = native.get(devs[0].info.product)
        if razer is None:
            if razers is None:
                razers = _openrazer_devices()
            razer = next((r for r in razers if _norm(r.name) and _norm(r.name) in _norm(key)), None)
        name = razer.name.replace(" (Receiver)", "") if razer else re.sub(r"^Razer (Razer |Inc )?", "Razer ", key)
        kind = getattr(razer, "type", None) or ("headset" if "blackshark" in key.lower() else "other")
        did = f"{devs[0].info.vendor:04x}-{devs[0].info.product:04x}"
        devices.append(Device(id=did, key=key, name=name, kind=kind, nodes=nodes, razer=razer,
                              layout=layouts.for_device(name)))

    order = {"mouse": 0, "keypad": 1, "keyboard": 2, "headset": 3}
    return sorted(devices, key=lambda d: (order.get(d.kind, 9), d.name))
