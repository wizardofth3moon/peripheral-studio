"""Profiles, bindings and actions: the on-disk format shared by the app and the engine.

Layout under ~/.config/peripheral-studio/:

    devices/<vid>-<pid>/profiles/<name>.json   one file per profile
    devices/<vid>-<pid>/device.json            {"active": "<profile>", "enabled": true}

A profile file:

    {"version": 1, "name": "WoW", "apps": ["wow.exe", "WowClassic"],
     "bindings": [{"input": {"type": 1, "code": 2}, "layer": "base", "action": {...}}, ...]}

`input.dir` is +1/-1 for wheels. `layer` is "base" or "hypershift". Action shapes are
documented on each Action subclass's `kind`.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from evdev import ecodes

ROOT = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "peripheral-studio"
DEVICES_DIR = ROOT / "devices"
FORMAT_VERSION = 1
LAYERS = ("base", "hypershift")


def device_id(vendor: int, product: int) -> str:
    return f"{vendor:04x}-{product:04x}"


def code_of(name: str) -> int:
    """evdev code for a name like KEY_A or BTN_SIDE."""
    value = ecodes.ecodes.get(name)
    if value is None:
        raise ValueError(f"unknown key {name!r}")
    return value


def name_of(code: int) -> str:
    n = ecodes.KEY.get(code) or ecodes.BTN.get(code)
    if n is None:
        return f"KEY_{code}"
    return n[0] if isinstance(n, (list, tuple)) else n


# ── Inputs ───────────────────────────────────────────────────────────────────

MODIFIER_CODES = frozenset(ecodes.ecodes[n] for n in (
    "KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT",
    "KEY_LEFTALT", "KEY_RIGHTALT", "KEY_LEFTMETA", "KEY_RIGHTMETA"))


@dataclass(frozen=True)
class Input:
    type: int
    code: int
    dir: int | None = None  # wheels: +1 up/right, -1 down/left
    # Modifiers the device itself sends with the key, e.g. a mouse whose onboard memory
    # (programmed by Synapse) makes a button send Shift+Keypad 3. Sorted key names.
    mods: tuple[str, ...] = ()

    @property
    def key(self) -> tuple:
        return (self.type, self.code, self.dir, self.mods)

    def to_json(self) -> dict:
        d = {"type": self.type, "code": self.code}
        if self.dir is not None:
            d["dir"] = self.dir
        if self.mods:
            d["mods"] = list(self.mods)
        return d

    @classmethod
    def from_json(cls, d: dict) -> "Input":
        return cls(int(d["type"]), int(d["code"]), d.get("dir"), tuple(sorted(d.get("mods", []))))

    def describe(self) -> str:
        if self.type == ecodes.EV_REL:
            return {(ecodes.REL_WHEEL, 1): "Scroll up", (ecodes.REL_WHEEL, -1): "Scroll down",
                    (ecodes.REL_HWHEEL, 1): "Scroll right", (ecodes.REL_HWHEEL, -1): "Scroll left"}.get(
                (self.code, self.dir), "Wheel")
        return " + ".join([pretty_key(m) for m in self.mods] + [pretty_key(name_of(self.code))])


# ── Actions ──────────────────────────────────────────────────────────────────

_PRETTY = {
    "LEFTCTRL": "Ctrl", "RIGHTCTRL": "Right Ctrl", "LEFTSHIFT": "Shift", "RIGHTSHIFT": "Right Shift",
    "LEFTALT": "Alt", "RIGHTALT": "AltGr", "LEFTMETA": "Super", "RIGHTMETA": "Right Super",
    "ENTER": "Enter", "SPACE": "Space", "ESC": "Esc", "BACKSPACE": "Backspace", "PAGEUP": "Page Up",
    "PAGEDOWN": "Page Down", "MINUS": "-", "EQUAL": "=", "COMMA": ",", "DOT": ".", "SLASH": "/",
    "BACKSLASH": "\\", "SEMICOLON": ";", "APOSTROPHE": "'", "GRAVE": "`", "LEFTBRACE": "[",
    "RIGHTBRACE": "]", "CAPSLOCK": "Caps Lock", "PLAYPAUSE": "Play / pause", "NEXTSONG": "Next track",
    "PREVIOUSSONG": "Previous track", "VOLUMEUP": "Volume up", "VOLUMEDOWN": "Volume down", "MUTE": "Mute",
    "MICMUTE": "Mic mute", "STOPCD": "Stop", "SYSRQ": "Print screen", "CALC": "Calculator",
    "HOMEPAGE": "Browser", "MAIL": "Mail", "FILE": "Files", "BRIGHTNESSUP": "Brightness up",
    "BRIGHTNESSDOWN": "Brightness down", "NUMLOCK": "Num Lock", "KPENTER": "Keypad Enter",
    "KPPLUS": "Keypad +", "KPMINUS": "Keypad −", "KPASTERISK": "Keypad *", "KPSLASH": "Keypad /",
    "KPDOT": "Keypad .", **{f"KP{n}": f"Keypad {n}" for n in range(10)},
}
MOUSE_NAMES = {"BTN_LEFT": "Left click", "BTN_RIGHT": "Right click", "BTN_MIDDLE": "Middle click",
               "BTN_SIDE": "Back (Mouse 4)", "BTN_EXTRA": "Forward (Mouse 5)"}


def pretty_key(name: str) -> str:
    if name in MOUSE_NAMES:
        return MOUSE_NAMES[name]
    bare = name.removeprefix("KEY_").removeprefix("BTN_")
    if bare in _PRETTY:
        return _PRETTY[bare]
    if len(bare) == 1:
        return bare.upper()
    return bare if re.fullmatch(r"F\d+", bare) else bare.capitalize()


@dataclass
class Action:
    """Base: every action serialises to {"kind": ..., ...}."""

    kind: str = "none"

    def to_json(self) -> dict:
        return {"kind": self.kind}

    def describe(self) -> str:
        return self.kind

    @staticmethod
    def from_json(d: dict) -> "Action":
        kind = d.get("kind")
        cls = _ACTIONS.get(kind)
        if cls is None:
            raise ValueError(f"unknown action kind {kind!r}")
        return cls.load(d)


@dataclass
class KeysAction(Action):
    """{"kind": "keys", "keys": ["KEY_LEFTCTRL", "KEY_C"]}: held while the button is held."""

    kind: str = "keys"
    keys: list[str] = field(default_factory=list)

    def to_json(self):
        return {"kind": self.kind, "keys": self.keys}

    @classmethod
    def load(cls, d):
        return cls(keys=[str(k) for k in d.get("keys", [])])

    def describe(self):
        return " + ".join(pretty_key(k) for k in self.keys) or "Nothing"


@dataclass
class ScrollAction(Action):
    """{"kind": "scroll", "axis": "vertical"|"horizontal", "amount": 1}: positive is up/right."""

    kind: str = "scroll"
    axis: str = "vertical"
    amount: int = 1

    def to_json(self):
        return {"kind": self.kind, "axis": self.axis, "amount": self.amount}

    @classmethod
    def load(cls, d):
        return cls(axis=d.get("axis", "vertical"), amount=int(d.get("amount", 1)))

    def describe(self):
        if self.axis == "vertical":
            return "Scroll up" if self.amount > 0 else "Scroll down"
        return "Scroll right" if self.amount > 0 else "Scroll left"


@dataclass
class TurboAction(Action):
    """{"kind": "turbo", "keys": [...], "interval_ms": 50}: taps repeatedly while held."""

    kind: str = "turbo"
    keys: list[str] = field(default_factory=list)
    interval_ms: int = 50

    def to_json(self):
        return {"kind": self.kind, "keys": self.keys, "interval_ms": self.interval_ms}

    @classmethod
    def load(cls, d):
        return cls(keys=[str(k) for k in d.get("keys", [])], interval_ms=max(10, int(d.get("interval_ms", 50))))

    def describe(self):
        rate = round(1000 / self.interval_ms)
        return f"Turbo {' + '.join(pretty_key(k) for k in self.keys)} ({rate}/s)"


MACRO_MODES = ("once", "while_held", "toggle", "repeat")


@dataclass
class MacroStep:
    """{"op": "down"|"up"|"tap", "key": "KEY_A"} | {"op": "wait", "ms": 100} |
    {"op": "text", "text": "gg"} | {"op": "scroll", "axis": "vertical", "amount": -1}"""

    op: str
    key: str = ""
    ms: int = 0
    text: str = ""
    axis: str = "vertical"
    amount: int = 0

    def to_json(self):
        d = {"op": self.op}
        if self.op in ("down", "up", "tap"):
            d["key"] = self.key
        elif self.op == "wait":
            d["ms"] = self.ms
        elif self.op == "text":
            d["text"] = self.text
        elif self.op == "scroll":
            d.update(axis=self.axis, amount=self.amount)
        return d

    @classmethod
    def load(cls, d):
        return cls(op=d["op"], key=d.get("key", ""), ms=int(d.get("ms", 0)), text=d.get("text", ""),
                   axis=d.get("axis", "vertical"), amount=int(d.get("amount", 0)))

    def describe(self):
        if self.op == "wait":
            return f"Wait {self.ms} ms"
        if self.op == "text":
            return f"Type “{self.text}”"
        if self.op == "scroll":
            return ScrollAction(axis=self.axis, amount=self.amount).describe()
        verb = {"down": "Press", "up": "Release", "tap": "Tap"}[self.op]
        return f"{verb} {pretty_key(self.key)}"


@dataclass
class MacroAction(Action):
    """{"kind": "macro", "name": "...", "mode": "once"|"while_held"|"toggle"|"repeat", "count": 3,
    "steps": [...]}. Keys a macro left pressed are released when it ends or is stopped."""

    kind: str = "macro"
    name: str = ""
    mode: str = "once"
    count: int = 1
    steps: list[MacroStep] = field(default_factory=list)

    def to_json(self):
        return {"kind": self.kind, "name": self.name, "mode": self.mode, "count": self.count,
                "steps": [s.to_json() for s in self.steps]}

    @classmethod
    def load(cls, d):
        mode = d.get("mode", "once")
        return cls(name=d.get("name", ""), mode=mode if mode in MACRO_MODES else "once",
                   count=max(1, int(d.get("count", 1))), steps=[MacroStep.load(s) for s in d.get("steps", [])])

    def describe(self):
        label = self.name or f"Macro ({len(self.steps)} steps)"
        suffix = {"while_held": ", while held", "toggle": ", toggle", "repeat": f", ×{self.count}"}.get(self.mode, "")
        return label + suffix


@dataclass
class DisableAction(Action):
    """{"kind": "disable"}"""

    kind: str = "disable"

    @classmethod
    def load(cls, d):
        return cls()

    def describe(self):
        return "Disabled"


@dataclass
class HypershiftAction(Action):
    """{"kind": "hypershift"}: while held, every device uses its Hypershift layer."""

    kind: str = "hypershift"

    @classmethod
    def load(cls, d):
        return cls()

    def describe(self):
        return "Hypershift"


@dataclass
class ProfileAction(Action):
    """{"kind": "profile", "target": "next"|"previous"|"<profile name>", "device": "<vid-pid>"?,
    "group": true?}. `device` switches another device (Synapse's cross-device keymap cycling);
    `group` cycles only through profiles of the active one's group (one Synapse profile's keymaps)."""

    kind: str = "profile"
    target: str = "next"
    device: str | None = None
    group: bool = False

    def to_json(self):
        d = {"kind": self.kind, "target": self.target}
        if self.device:
            d["device"] = self.device
        if self.group:
            d["group"] = True
        return d

    @classmethod
    def load(cls, d):
        return cls(target=d.get("target", "next"), device=d.get("device"), group=bool(d.get("group", False)))

    def describe(self):
        text = {"next": "Next profile", "previous": "Previous profile"}.get(self.target, f"Profile: {self.target}")
        if self.group:
            text = text.replace("profile", "keymap")
        return text + (" (other device)" if self.device else "")


_ACTIONS = {c.kind: c for c in (KeysAction, ScrollAction, TurboAction, MacroAction, DisableAction,
                                HypershiftAction, ProfileAction)}


# ── Profiles ─────────────────────────────────────────────────────────────────

@dataclass
class Binding:
    input: Input
    action: Action
    layer: str = "base"

    def to_json(self):
        return {"input": self.input.to_json(), "layer": self.layer, "action": self.action.to_json()}

    @classmethod
    def from_json(cls, d):
        layer = d.get("layer", "base")
        return cls(Input.from_json(d["input"]), Action.from_json(d["action"]), layer if layer in LAYERS else "base")


def _safe(name: str) -> str:
    return re.sub(r"[/\x00]", "-", name).strip() or "Profile"


@dataclass
class Profile:
    device: str
    name: str
    bindings: dict[tuple[str, tuple], Binding] = field(default_factory=dict)
    apps: list[str] = field(default_factory=list)
    extra: dict = field(default_factory=dict)  # unknown top-level keys, kept on save

    @property
    def path(self) -> Path:
        return DEVICES_DIR / self.device / "profiles" / f"{_safe(self.name)}.json"

    def get(self, layer: str, inp: Input) -> Binding | None:
        return self.bindings.get((layer, inp.key))

    def set(self, binding: Binding) -> None:
        self.bindings[(binding.layer, binding.input.key)] = binding

    def remove(self, layer: str, inp: Input) -> None:
        self.bindings.pop((layer, inp.key), None)

    def layer(self, layer: str) -> list[Binding]:
        return [b for (l, _), b in self.bindings.items() if l == layer]

    def to_json(self):
        return {**self.extra, "version": FORMAT_VERSION, "name": self.name, "apps": self.apps,
                "bindings": [b.to_json() for b in self.bindings.values()]}

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.to_json(), indent=2))
        tmp.replace(self.path)

    @classmethod
    def load(cls, device: str, name: str) -> "Profile":
        p = cls(device, name)
        try:
            data = json.loads(p.path.read_text())
        except FileNotFoundError:
            return p
        p.apps = [str(a) for a in data.get("apps", [])]
        p.extra = {k: v for k, v in data.items() if k not in ("version", "name", "apps", "bindings")}
        for b in data.get("bindings", []):
            try:
                p.set(Binding.from_json(b))
            except (KeyError, ValueError, TypeError):
                continue  # a binding this version can't read is skipped, not fatal
        return p


def list_profiles(device: str) -> list[str]:
    folder = DEVICES_DIR / device / "profiles"
    if not folder.is_dir():
        return []
    names = []
    for f in sorted(folder.glob("*.json")):
        try:
            names.append(json.loads(f.read_text()).get("name") or f.stem)
        except (json.JSONDecodeError, OSError):
            continue
    return names


def delete_profile(device: str, name: str) -> None:
    Profile(device, name).path.unlink(missing_ok=True)
    state = DeviceState.load(device)
    if state.active == name:
        state.active = None
        state.save()


def rename_profile(device: str, old: str, new: str) -> None:
    p = Profile.load(device, old)
    old_path = p.path
    p.name = new
    p.save()
    if old_path != p.path:
        old_path.unlink(missing_ok=True)
    state = DeviceState.load(device)
    if state.active == old:
        state.active = new
        state.save()


@dataclass
class DeviceState:
    device: str
    active: str | None = None
    enabled: bool = True
    # What a diagram button really sends, learned from a press: {button id: Input JSON}.
    buttons: dict = field(default_factory=dict)

    @property
    def path(self) -> Path:
        return DEVICES_DIR / self.device / "device.json"

    @classmethod
    def load(cls, device: str) -> "DeviceState":
        s = cls(device)
        try:
            d = json.loads(s.path.read_text())
            s.active, s.enabled = d.get("active"), bool(d.get("enabled", True))
            s.buttons = dict(d.get("buttons", {}))
        except (FileNotFoundError, json.JSONDecodeError):
            pass
        return s

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps({"active": self.active, "enabled": self.enabled,
                                         "buttons": self.buttons}, indent=2))


def ensure_profile(device: str) -> str:
    """The active profile's name, creating an empty "Default" one the first time."""
    state = DeviceState.load(device)
    names = list_profiles(device)
    if state.active in names:
        return state.active
    if not names:
        Profile(device, "Default").save()
        names = ["Default"]
    state.active = names[0]
    state.save()
    return state.active


# ── Typed shortcuts ──────────────────────────────────────────────────────────

_ALIASES = {
    "ctrl": "LEFTCTRL", "control": "LEFTCTRL", "rctrl": "RIGHTCTRL", "shift": "LEFTSHIFT", "rshift": "RIGHTSHIFT",
    "alt": "LEFTALT", "altgr": "RIGHTALT", "super": "LEFTMETA", "win": "LEFTMETA", "meta": "LEFTMETA",
    "cmd": "LEFTMETA", "esc": "ESC", "escape": "ESC", "return": "ENTER", "enter": "ENTER", "del": "DELETE",
    "ins": "INSERT", "pgup": "PAGEUP", "pageup": "PAGEUP", "pgdn": "PAGEDOWN", "pagedown": "PAGEDOWN",
    "caps": "CAPSLOCK", "capslock": "CAPSLOCK", "print": "SYSRQ", "prtsc": "SYSRQ", "menu": "COMPOSE",
    "-": "MINUS", "=": "EQUAL", "[": "LEFTBRACE", "]": "RIGHTBRACE", "\\": "BACKSLASH", ";": "SEMICOLON",
    "'": "APOSTROPHE", ",": "COMMA", ".": "DOT", "/": "SLASH", "`": "GRAVE",
    "plus": "EQUAL", "play": "PLAYPAUSE", "next": "NEXTSONG", "prev": "PREVIOUSSONG",
}


def parse_shortcut(text: str) -> list[str]:
    """"Ctrl+Shift+S" -> ["KEY_LEFTCTRL", "KEY_LEFTSHIFT", "KEY_S"]. Raises ValueError."""
    keys = []
    parts = [p.strip() for p in text.replace(" + ", "+").split("+")]
    if text.strip().endswith("++"):
        parts = parts[:-2] + ["EQUAL"]
    for part in parts:
        if not part:
            continue
        low = part.lower()
        if part.upper().startswith(("KEY_", "BTN_")):
            name = part.upper()
        elif low in _ALIASES:
            name = "KEY_" + _ALIASES[low]
        else:
            name = "KEY_" + part.upper()
        code_of(name)  # validates
        keys.append(name)
    if not keys:
        raise ValueError("empty shortcut")
    return keys
