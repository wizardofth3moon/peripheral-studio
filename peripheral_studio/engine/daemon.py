"""The remap engine: a background service that owns the Razer input devices.

Every Razer evdev node is read. A device with an active, non-empty profile is grabbed
(exclusive access); its unbound events are passed through a per-node clone so the rest of
the system sees them unchanged, and its bound buttons run actions on the virtual keyboard
and mouse. Ungrabbed devices are still read, so the app can show which button was pressed.

Control socket: $XDG_RUNTIME_DIR/peripheral-studio/engine.sock, one JSON object per line.
"""
from __future__ import annotations

import asyncio
import collections
import json
import time
import logging
import os
import signal
from dataclasses import dataclass, field
from pathlib import Path

import evdev
from evdev import ecodes as e

from . import model
from .actions import Running, Runner
from .output import Output

log = logging.getLogger("engine")
RAZER_VENDOR = 0x1532
OWN_PRODUCTS = {0x5053, 0x5054}  # our own virtual keyboard and mouse
SOCKET = Path(os.environ.get("XDG_RUNTIME_DIR", f"/tmp/peripheral-studio-{os.getuid()}")) / "peripheral-studio" / "engine.sock"
WHEELS = {e.REL_WHEEL: e.REL_WHEEL, e.REL_WHEEL_HI_RES: e.REL_WHEEL,
          e.REL_HWHEEL: e.REL_HWHEEL, e.REL_HWHEEL_HI_RES: e.REL_HWHEEL}


@dataclass
class Node:
    dev: evdev.InputDevice
    forward: evdev.UInput | None = None
    task: asyncio.Task | None = None
    pending: bool = False  # forwarded events waiting for SYN
    frame: list = field(default_factory=list)  # events since the last SYN
    held_mods: set = field(default_factory=set)  # modifier codes this node holds down
    swallowed: set = field(default_factory=set)  # modifiers eaten as part of a bound chord


@dataclass
class Device:
    id: str
    name: str
    phys: str
    nodes: dict[str, Node] = field(default_factory=dict)
    state: model.DeviceState | None = None
    profile: model.Profile | None = None
    override: str | None = None  # profile picked by an app rule
    grabbed: bool = False
    pressed: dict[tuple, Running] = field(default_factory=dict)
    pressed_codes: dict[int, tuple] = field(default_factory=dict)  # key code -> its slot in pressed


class Hypershift(Running):
    def __init__(self, engine):
        self.engine = engine
        engine.hypershift += 1
        engine.broadcast({"event": "hypershift", "active": True})

    def release(self):
        self.engine.hypershift = max(0, self.engine.hypershift - 1)
        if not self.engine.hypershift:
            self.engine.broadcast({"event": "hypershift", "active": False})


class Engine:
    def __init__(self):
        self.out = Output()
        self.runner = Runner(self.out)
        self.devices: dict[str, Device] = {}
        self.hypershift = 0
        self.focus: str | None = None
        self.watchers: set[asyncio.StreamWriter] = set()
        # Last inputs seen, for diagnostics ({"cmd": "recent"}): which code a button really sends.
        self.recent: collections.deque = collections.deque(maxlen=50)
        self._rescan_handle = None

    # ── Devices ──────────────────────────────────────────────────────────────

    def scan(self) -> None:
        seen: dict[str, tuple[str, list[evdev.InputDevice]]] = {}
        known_paths = {p for d in self.devices.values() for p in d.nodes}
        for path in evdev.list_devices():
            if path in known_paths:
                seen.setdefault(self._device_of(path).id, ("", []))
                continue
            try:
                dev = evdev.InputDevice(path)
            except OSError:
                continue
            if (dev.info.vendor != RAZER_VENDOR or dev.info.product in OWN_PRODUCTS
                    or dev.name.startswith(("input-remapper", "Peripheral Studio")) or "(Peripheral Studio)" in dev.name):
                dev.close()
                continue
            did = model.device_id(dev.info.vendor, dev.info.product)
            device = self.devices.get(did)
            if device is None:
                device = Device(did, dev.name, (dev.phys or "").split("/input")[0])
                self.devices[did] = device
                log.info("found %s (%s)", device.name, did)
            if len(dev.name) < len(device.name):
                device.name = dev.name
            device.nodes[path] = Node(dev)
            seen.setdefault(did, ("", []))
            self.load_profile(device, quiet=True)
            self._start_reading(device, path)

        for did in list(self.devices):
            device = self.devices[did]
            for path in list(device.nodes):
                if not os.path.exists(path):
                    self._drop_node(device, path)
            if not device.nodes:
                log.info("lost %s", device.name)
                self._release_presses(device)
                del self.devices[did]
        for device in self.devices.values():
            self._apply_grab(device)

    def _device_of(self, path: str) -> Device:
        return next(d for d in self.devices.values() if path in d.nodes)

    def _start_reading(self, device: Device, path: str) -> None:
        node = device.nodes[path]
        node.task = asyncio.get_running_loop().create_task(self._read(device, path, node))

    def _drop_node(self, device: Device, path: str) -> None:
        node = device.nodes.pop(path, None)
        if not node:
            return
        if node.task:
            node.task.cancel()
        if node.forward:
            node.forward.close()
        try:
            node.dev.close()
        except OSError:
            pass

    def schedule_rescan(self) -> None:
        loop = asyncio.get_running_loop()
        if self._rescan_handle:
            self._rescan_handle.cancel()
        self._rescan_handle = loop.call_later(0.6, self.scan)

    # ── Profiles and grabbing ────────────────────────────────────────────────

    def active_name(self, device: Device) -> str | None:
        return device.override or (device.state.active if device.state else None)

    def load_profile(self, device: Device, quiet: bool = False) -> None:
        device.state = model.DeviceState.load(device.id)
        name = self.active_name(device)
        device.profile = model.Profile.load(device.id, name) if name else None
        if not quiet:
            log.info("%s: profile %r (%d bindings)", device.name, name,
                     len(device.profile.bindings) if device.profile else 0)

    def _apply_grab(self, device: Device) -> None:
        want = bool(device.state and device.state.enabled and device.profile and device.profile.bindings)
        if want == device.grabbed and all((n.forward is not None) == want for n in device.nodes.values()):
            return
        self._release_presses(device)
        for path, node in device.nodes.items():
            if want and node.forward is None:
                try:
                    node.forward = evdev.UInput.from_device(node.dev, name=f"{node.dev.name} (Peripheral Studio)")
                    node.dev.grab()
                except (OSError, evdev.UInputError) as ex:
                    log.error("%s: can't take over %s: %s", device.name, path, ex)
                    if node.forward:
                        node.forward.close()
                        node.forward = None
            elif not want and node.forward is not None:
                try:
                    node.dev.ungrab()
                except OSError:
                    pass
                node.forward.close()
                node.forward = None
        device.grabbed = want
        log.info("%s: %s", device.name, "remapping" if want else "passthrough")
        self.broadcast({"event": "device", "device": device.id, "grabbed": want,
                        "profile": self.active_name(device)})

    def _release_presses(self, device: Device) -> None:
        for running in device.pressed.values():
            running.release()
        device.pressed.clear()
        device.pressed_codes.clear()

    def reload(self, device_id: str | None = None) -> None:
        for device in self.devices.values():
            if device_id in (None, device.id):
                self.load_profile(device)
                self._apply_grab(device)

    def activate(self, device_id: str, profile: str) -> None:
        state = model.DeviceState.load(device_id)
        state.active = profile
        state.save()
        if device := self.devices.get(device_id):
            device.override = None
            self.reload(device_id)

    def switch_profile_from(self, device_id: str, target: str, group: bool = False) -> None:
        names = model.list_profiles(device_id)
        device = self.devices.get(device_id)
        if not names or not device:
            return
        current = self.active_name(device)
        if group and current:
            mine = model.Profile.load(device_id, current).extra.get("group")
            names = [n for n in names if model.Profile.load(device_id, n).extra.get("group") == mine] or names
        if target in ("next", "previous"):
            i = names.index(current) if current in names else -1
            target = names[(i + (1 if target == "next" else -1)) % len(names)]
        if target in names:
            # Switch after this event is processed: the press that asked for it is still held.
            asyncio.get_running_loop().call_soon(self.activate, device_id, target)

    def hold_hypershift(self) -> Running:
        return Hypershift(self)

    def set_focus(self, app: str | None) -> None:
        """Applies app rules: a profile listing the focused app wins until focus moves on."""
        self.focus = app
        low = (app or "").lower()
        for device in self.devices.values():
            match = None
            if low:
                for name in model.list_profiles(device.id):
                    apps = [a.lower() for a in model.Profile.load(device.id, name).apps]
                    if any(a and (a == low or a in low) for a in apps):
                        match = name
                        break
            if match != device.override:
                device.override = match
                self.load_profile(device)
                self._apply_grab(device)
                self.broadcast({"event": "device", "device": device.id, "grabbed": device.grabbed,
                                "profile": self.active_name(device), "app": app})

    # ── Events ───────────────────────────────────────────────────────────────

    async def _read(self, device: Device, path: str, node: Node) -> None:
        try:
            async for ev in node.dev.async_read_loop():
                try:
                    self._handle(device, node, ev)
                except Exception:
                    log.exception("event handling failed; passing it through")
                    if node.forward and ev.type != e.EV_SYN:
                        node.forward.write_event(ev)
                        node.forward.syn()
        except (OSError, asyncio.CancelledError):
            pass
        if path in device.nodes and device.nodes[path] is node:
            self.schedule_rescan()

    def _binding(self, device: Device, inp: model.Input) -> model.Binding | None:
        p = device.profile
        if not p:
            return None
        if self.hypershift and (b := p.get("hypershift", inp)):
            return b
        return p.get("base", inp)

    def _handle(self, device: Device, node: Node, ev) -> None:
        """Collects one report's events (up to SYN) and handles them together, so a chord the
        device sends in one report (Shift + Keypad 3 from onboard memory) is seen as one input."""
        if ev.type != e.EV_SYN:
            node.frame.append(ev)
            return
        frame, node.frame = node.frame, []
        self._frame(device, node, frame)
        if node.forward and node.pending:
            node.forward.syn()
            node.pending = False

    def _forward(self, node: Node, ev) -> None:
        if node.forward:
            node.forward.write_event(ev)
            node.pending = True

    def _frame(self, device: Device, node: Node, frame: list) -> None:
        fwd = node.forward
        mods_down = {ev.code for ev in frame if ev.type == e.EV_KEY and ev.code in model.MODIFIER_CODES and ev.value == 1}
        context = node.held_mods | mods_down
        chord = tuple(sorted(model.name_of(c) for c in context))

        # Pass 1: does a key in this report complete a bound chord? Then its modifiers are eaten.
        swallow = set()
        if fwd and chord:
            for ev in frame:
                if ev.type == e.EV_KEY and ev.value == 1 and ev.code not in model.MODIFIER_CODES:
                    if self._binding(device, model.Input(e.EV_KEY, ev.code, None, chord)):
                        swallow |= mods_down

        for ev in frame:
            if ev.type == e.EV_KEY and ev.code in model.MODIFIER_CODES:
                if ev.value == 1:
                    node.held_mods.add(ev.code)
                    if ev.code in swallow:
                        node.swallowed.add(ev.code)
                        continue
                elif ev.value == 0:
                    node.held_mods.discard(ev.code)
                    if ev.code in node.swallowed:
                        node.swallowed.discard(ev.code)
                        continue
                elif ev.code in node.swallowed:
                    continue
                lone = not any(x.type == e.EV_KEY and x.value == 1 and x.code not in model.MODIFIER_CODES for x in frame)
                if ev.value == 1 and lone:  # a modifier pressed on its own is an input too
                    self._input_event(device, model.Input(e.EV_KEY, ev.code))
                if fwd and ev.value == 1 and (b := self._binding(device, model.Input(e.EV_KEY, ev.code))):
                    slot = (device.id, model.Input(e.EV_KEY, ev.code).key)
                    device.pressed[slot] = self.runner.press(slot, b.action, self)
                    device.pressed_codes[ev.code] = slot
                    continue
                if ev.value == 0 and ev.code in device.pressed_codes:
                    device.pressed.pop(device.pressed_codes.pop(ev.code)).release()
                    continue
                self._forward(node, ev)

            elif ev.type == e.EV_KEY:
                if ev.value == 1:
                    with_mods = model.Input(e.EV_KEY, ev.code, None, chord)
                    plain = model.Input(e.EV_KEY, ev.code)
                    self._input_event(device, with_mods if chord else plain)
                    if fwd:
                        b, inp = (self._binding(device, with_mods), with_mods) if chord else (None, plain)
                        if b is None:
                            b, inp = self._binding(device, plain), plain
                        if b:
                            slot = (device.id, inp.key)
                            device.pressed[slot] = self.runner.press(slot, b.action, self)
                            device.pressed_codes[ev.code] = slot
                            continue
                    self._forward(node, ev)
                elif ev.code in device.pressed_codes:
                    if ev.value == 0:
                        device.pressed.pop(device.pressed_codes.pop(ev.code)).release()
                    # autorepeat of a bound key is dropped
                else:
                    self._forward(node, ev)

            elif ev.type == e.EV_REL and ev.code in WHEELS and ev.value:
                base = WHEELS[ev.code]
                inp = model.Input(e.EV_REL, base, 1 if ev.value > 0 else -1)
                lo_res = ev.code == base
                if lo_res:
                    self._input_event(device, inp)
                if fwd and (b := self._binding(device, inp)):
                    if lo_res:  # one action per detent; the hi-res stream of the same turn is dropped
                        slot = (device.id, inp.key)
                        for _ in range(abs(ev.value)):
                            self.runner.press(slot, b.action, self).release()
                    continue
                self._forward(node, ev)
            else:
                self._forward(node, ev)

    def _input_event(self, device: Device, inp: model.Input) -> None:
        self.broadcast({"event": "input", "device": device.id, "input": inp.to_json()})

    # ── Control socket ───────────────────────────────────────────────────────

    def status(self) -> dict:
        return {"ok": True, "hypershift": bool(self.hypershift), "focus": self.focus,
                "devices": [{"id": d.id, "name": d.name, "nodes": len(d.nodes), "grabbed": d.grabbed,
                             "profile": self.active_name(d), "override": d.override is not None,
                             "enabled": bool(d.state and d.state.enabled)}
                            for d in self.devices.values()]}

    def broadcast(self, msg: dict) -> None:
        if msg.get("event") == "input":
            self.recent.append({"t": round(time.time(), 2), **msg})
        if not self.watchers:
            return
        line = (json.dumps(msg) + "\n").encode()
        for w in list(self.watchers):
            if w.is_closing():
                self.watchers.discard(w)
                continue
            w.write(line)

    async def serve_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            while line := await reader.readline():
                try:
                    req = json.loads(line)
                    reply = self.command(req, writer)
                except Exception as ex:
                    reply = {"ok": False, "error": str(ex)}
                writer.write((json.dumps(reply) + "\n").encode())
                await writer.drain()
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        finally:
            self.watchers.discard(writer)
            writer.close()

    def command(self, req: dict, writer) -> dict:
        cmd = req.get("cmd")
        if cmd == "ping":
            return {"ok": True}
        if cmd == "status":
            return self.status()
        if cmd == "reload":
            self.reload(req.get("device"))
            return self.status()
        if cmd == "activate":
            self.activate(req["device"], req["profile"])
            return self.status()
        if cmd == "enable":
            state = model.DeviceState.load(req["device"])
            state.enabled = bool(req["enabled"])
            state.save()
            self.reload(req["device"])
            return self.status()
        if cmd == "focus":
            self.set_focus(req.get("app"))
            return {"ok": True}
        if cmd == "recent":
            return {"ok": True, "recent": list(self.recent)}
        if cmd == "watch":
            self.watchers.add(writer)
            return {"ok": True, "watching": True}
        return {"ok": False, "error": f"unknown command {cmd!r}"}

    # ── Lifecycle ────────────────────────────────────────────────────────────

    def shutdown(self) -> None:
        self.runner.stop_all()
        for device in self.devices.values():
            self._release_presses(device)
            for path in list(device.nodes):
                node = device.nodes[path]
                if node.forward:
                    try:
                        node.dev.ungrab()
                    except OSError:
                        pass
                self._drop_node(device, path)
        self.out.close()


async def run() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    engine = Engine()
    loop = asyncio.get_running_loop()

    SOCKET.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(SOCKET.parent, 0o700)
    SOCKET.unlink(missing_ok=True)
    server = await asyncio.start_unix_server(engine.serve_client, path=str(SOCKET))
    os.chmod(SOCKET, 0o600)

    engine.scan()

    # Hotplug: the Naga's receiver drops and re-adds nodes when the mouse sleeps and wakes.
    try:
        import pyudev
        ctx = pyudev.Context()
        mon = pyudev.Monitor.from_netlink(ctx)
        mon.filter_by("input")
        mon.start()

        def on_udev():
            while (dev := mon.poll(timeout=0)) is not None:
                if (dev.device_node or "").startswith("/dev/input/event"):
                    engine.schedule_rescan()

        loop.add_reader(mon.fileno(), on_udev)
    except Exception as ex:
        log.warning("no hotplug (%s); polling instead", ex)

        async def poll():
            while True:
                await asyncio.sleep(3)
                engine.scan()

        loop.create_task(poll())

    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    log.info("engine running, socket %s", SOCKET)
    try:
        await stop.wait()
    finally:
        server.close()
        engine.shutdown()
        SOCKET.unlink(missing_ok=True)
        log.info("engine stopped")


def main() -> None:
    asyncio.run(run())
