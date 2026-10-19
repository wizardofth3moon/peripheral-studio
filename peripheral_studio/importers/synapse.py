"""Imports Razer Synapse 4 profiles from a Windows user folder.

Synapse 4 keeps a local copy of every profile in Chromium Local Storage under
%LOCALAPPDATA%\\Razer\\RazerAppEngine\\User Data\\Default\\Local Storage\\leveldb, one JSON value per
device: key "synapse_<product id in decimal>". Each profile has keymaps (Tartarus-style sets of
binds), plain "mappings", and on the Naga a separate "sidePanelMappings" for the side grid.

    python3 -m peripheral_studio.importers.synapse <folder>     # dry run: prints what would import
"""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

from evdev import ecodes

from ..engine import model
from .leveldb import chromium_string, read_database

LOCAL_STORAGE = Path("RazerAppEngine") / "User Data" / "Default" / "Local Storage" / "leveldb"


# ── Finding the data ─────────────────────────────────────────────────────────

def locate(root: Path) -> Path | None:
    """The Local Storage folder under a Windows drive, user folder, AppData or Razer folder."""
    root = Path(root)
    for base in (root, root / "Razer", root / "Local" / "Razer", root / "AppData" / "Local" / "Razer"):
        candidate = base / LOCAL_STORAGE
        if candidate.is_dir():
            return candidate
    if root.name == "leveldb" and root.is_dir():
        return root
    # A drive or Users folder: look one or two levels down for user profiles.
    for users in (root / "Users", root):
        if users.is_dir():
            for user in sorted(users.iterdir()):
                candidate = user / "AppData" / "Local" / "Razer" / LOCAL_STORAGE
                if candidate.is_dir():
                    return candidate
    return None


def candidates() -> list[Path]:
    """Places a Windows install or backup commonly shows up on Linux."""
    found = []
    roots = [Path("/mnt"), Path("/media"), Path("/run/media"), Path.home()]
    for r in roots:
        if not r.is_dir():
            continue
        for d in [r] + [p for p in r.iterdir() if p.is_dir()] + \
                 [q for p in r.iterdir() if p.is_dir() for q in _safe_dirs(p)]:
            if (hit := locate(d)) and hit not in found:
                found.append(hit)
    return found


def _safe_dirs(p: Path):
    try:
        return [q for q in p.iterdir() if q.is_dir()]
    except OSError:
        return []


# ── Synapse data ─────────────────────────────────────────────────────────────

@dataclass
class SynapseProfile:
    guid: str
    name: str
    keymaps: list[tuple[str, list[dict]]]  # (keymap name, mappings)


@dataclass
class SynapseDevice:
    product_ids: set[int]
    name: str
    category: str
    profiles: list[SynapseProfile] = field(default_factory=list)
    active: str | None = None


def read(local_storage: Path) -> list[SynapseDevice]:
    db = read_database(local_storage)
    out = []
    for key, value in db.items():
        name = key.split(b"\x00\x01")[-1].decode("latin-1", "replace")
        if not re.fullmatch(r"synapse_\d+", name):
            continue
        try:
            data = json.loads(chromium_string(value))
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or not data.get("profiles"):
            continue
        pids = {int(data[k]) for k in ("productId", "bleId", "dongleId") if isinstance(data.get(k), int)}
        pids.add(int(name.split("_")[1]))
        dev = SynapseDevice(pids, data.get("name") or "", data.get("category", ""), active=data.get("activeProfile"))
        for p in data["profiles"]:
            keymaps = [(k.get("name") or f"Keymap {i + 1}", list(k.get("mappings", [])))
                       for i, k in enumerate(p.get("keymaps", []))]
            loose = list(p.get("mappings", []))
            for group in (p.get("sidePanelMappings") or {}).values():
                loose += list(group)
            if loose:
                if keymaps:
                    keymaps[0] = (keymaps[0][0], keymaps[0][1] + loose)
                else:
                    keymaps = [("", loose)]
            dev.profiles.append(SynapseProfile(p.get("guid", ""), p.get("name", "Profile"), keymaps or [("", [])]))
        out.append(dev)
    # A device can appear under its wired and its dongle id with the same profiles; keep one.
    unique: dict[frozenset, SynapseDevice] = {}
    for d in out:
        k = frozenset(d.product_ids)
        if k not in unique or len(d.profiles) > len(unique[k].profiles):
            unique[k] = d
    return list(unique.values())


# ── Conversion ───────────────────────────────────────────────────────────────

_KEY_ALIASES = {
    "SPACEBAR": "SPACE", "ESCAPE": "ESC", "PRINT_SCREEN": "SYSRQ", "TILDE": "GRAVE", "GRAVE_ACCENT": "GRAVE",
    "EQUALS": "EQUAL", "LEFT_BRACKET": "LEFTBRACE", "RIGHT_BRACKET": "RIGHTBRACE", "QUOTE": "APOSTROPHE",
    "SINGLE_QUOTE": "APOSTROPHE", "PERIOD": "DOT", "FORWARD_SLASH": "SLASH", "LEFT_WINDOWS": "LEFTMETA",
    "RIGHT_WINDOWS": "RIGHTMETA", "LEFT_WIN": "LEFTMETA", "RIGHT_WIN": "RIGHTMETA", "APPLICATION": "COMPOSE",
    "MENU": "COMPOSE", "RETURN": "ENTER", "LEFT_CONTROL": "LEFTCTRL", "RIGHT_CONTROL": "RIGHTCTRL",
    "NUMPAD_NUM_LOCK": "NUMLOCK", "NUM_LOCK": "NUMLOCK", "NUMPAD_DECIMAL": "KPDOT", "NUMPAD_PERIOD": "KPDOT",
    "NUMPAD_ENTER": "KPENTER", "NUMPAD_DIVIDE": "KPSLASH", "NUMPAD_SLASH": "KPSLASH",
    "NUMPAD_MULTIPLY": "KPASTERISK", "NUMPAD_ASTERISK": "KPASTERISK", "NUMPAD_MINUS": "KPMINUS",
    "NUMPAD_SUBTRACT": "KPMINUS", "NUMPAD_PLUS": "KPPLUS", "NUMPAD_ADD": "KPPLUS",
    "UP_ARROW": "UP", "DOWN_ARROW": "DOWN", "LEFT_ARROW": "LEFT", "RIGHT_ARROW": "RIGHT",
    "PLAY_PAUSE": "PLAYPAUSE", "MEDIA_PLAY_PAUSE": "PLAYPAUSE", "NEXT_TRACK": "NEXTSONG",
    "PREVIOUS_TRACK": "PREVIOUSSONG", "VOLUME_UP": "VOLUMEUP", "VOLUME_DOWN": "VOLUMEDOWN", "VOLUME_MUTE": "MUTE",
    "HYPEN": "MINUS", "HYPHEN": "MINUS", "DASH": "MINUS", "OPEN_SQUARE_BRACKET": "LEFTBRACE",
    "CLOSE_SQUARE_BRACKET": "RIGHTBRACE", "NUMPAD_DASH": "KPMINUS", "NUMPAD_HYPEN": "KPMINUS",
    "BACK_SLASH": "BACKSLASH", "BACKQUOTE": "GRAVE", "CAPSLOCK": "CAPSLOCK", "PAGEUP": "PAGEUP",
}


def evdev_key(synapse_name: str) -> str | None:
    """KEY_LEFT_SHIFT -> KEY_LEFTSHIFT, KEY_NUMPAD_3 -> KEY_KP3, KEY_SPACEBAR -> KEY_SPACE."""
    bare = synapse_name.removeprefix("KEY_")
    if bare in _KEY_ALIASES:
        bare = _KEY_ALIASES[bare]
    elif m := re.fullmatch(r"NUMPAD_(\d)", bare):
        bare = f"KP{m.group(1)}"
    else:
        bare = bare.replace("_", "")
    name = f"KEY_{bare}"
    return name if name in ecodes.ecodes else None


# Synapse's names for non-key inputs -> our diagram button ids (first existing one wins).
_MOUSE_INPUTS = {
    "ScrollButton": ("middle", "wheel_click"), "ScrollUp": ("wheel_up",), "ScrollDown": ("wheel_down",),
    "RepeatScrollLeft": ("tilt_left",), "ScrollLeft": ("tilt_left",),
    "RepeatScrollRight": ("tilt_right",), "ScrollRight": ("tilt_right",),
    "LeftClick": ("left",), "RightClick": ("right",), "LeftButton": ("left",), "RightButton": ("right",),
    "DKM_M_01": ("dpi_up",), "DKM_M_02": ("dpi_down",),
}


@dataclass
class Imported:
    name: str
    group: str
    profile: model.Profile
    skipped: list[str]  # human-readable reasons, one per mapping that didn't convert
    selected: bool = True
    source: str = ""  # Synapse profile name


def _clean(name: str) -> str:
    return re.sub(r"\s+-\s+S3\b", "", name).strip() or "Profile"


def convert(sdev: SynapseDevice, device, all_devices=(), existing=()) -> list[Imported]:
    """Converts one Synapse device's profiles for one of our devices (devices.Device)."""
    layout = device.layout
    learned = model.DeviceState.load(device.id).buttons
    by_pid = {int(d.id.split("-")[1], 16): d for d in all_devices}

    def button_input(button_ids) -> tuple[model.Input | None, str | None]:
        if not layout:
            return None, "no diagram for this device"
        for bid in button_ids:
            b = next((x for x in layout.all_buttons() if x.id == bid), None)
            if b is None:
                continue
            if bid in learned:
                return model.Input.from_json(learned[bid]), None
            if b.hardware:
                return None, f"{b.label}: press “Detect what this button sends” first"
            return model.Input(b.ev_type, b.code, b.threshold), None
        return None, "this device has no such button"

    def to_input(m: dict) -> tuple[model.Input | None, str | None]:
        kind, iid = m.get("inputType"), m.get("inputID", "")
        if kind == "KeyInput":
            name = evdev_key(iid)
            if not name:
                return None, f"unknown key {iid}"
            code = model.code_of(name)
            if layout:  # a learned button whose default is this key (onboard memory moved it)
                b = layout.find(ecodes.EV_KEY, code, None)
                if b and b.id in learned:
                    return model.Input.from_json(learned[b.id]), None
            return model.Input(ecodes.EV_KEY, code), None
        if iid in _MOUSE_INPUTS:
            return button_input(_MOUSE_INPUTS[iid])
        return None, f"input {kind}/{iid} isn't supported yet"

    def to_action(m: dict) -> tuple[model.Action | None, str | None]:
        out = m.get("outputType")
        if out == "keyboardGroup":
            g = m.get("keyboardGroup", {})
            keys = []
            for k in list(g.get("modifiers", [])) + ([g["key"]] if g.get("key") else []):
                name = evdev_key(k)
                if not name:
                    return None, f"unknown key {k}"
                keys.append(name)
            if not keys:
                return None, "empty key assignment"
            return model.KeysAction(keys=keys), None
        if out == "disableGroup":
            return model.DisableAction(), None
        if out == "macroGroup":
            g = m.get("macroGroup", {})
            mode = {"Once": "once", "Repeat": "repeat", "RepeatWhileHeld": "while_held", "Toggle": "toggle",
                    "WhileHeld": "while_held"}.get(g.get("macroPlaybackOption", "Once"), "once")
            count = int(g.get("repeatCount") or 1)
            if mode == "once" and count > 1:
                mode = "repeat"
            return model.MacroAction(name=f"{_clean(g.get('name', 'Macro'))} (re-record)", mode=mode,
                                     count=max(1, count)), \
                "macro steps live in Synapse's cloud library; imported as an empty, named macro"
        if out == "interDeviceGroup":
            g = m.get("interDeviceGroup", {})
            target = by_pid.get(int(g.get("interPID", 0)))
            kind = g.get("keymapGroup", {}).get("type", "")
            if g.get("interDeviceType") == "keymapGroup" and kind in ("cycleUpKeymap", "cycleDownKeymap") and target:
                return model.ProfileAction(target="next" if kind == "cycleUpKeymap" else "previous",
                                           device=target.id, group=True), None
            return None, f"cross-device action {kind or g.get('interDeviceType')} isn't supported"
        if out == "hypershiftGroup":
            return model.HypershiftAction(), None
        return None, f"output {out} isn't supported yet"

    # Duplicates ("WoW - S3 (1)", "WoW - S3 (2)"): the copy with the most binds is preselected
    # and takes the clean name; the others keep their number and start unticked.
    def base(n: str) -> str:
        return re.sub(r"\s*\(\d+\)$", "", _clean(n))

    def size(sp: SynapseProfile) -> int:
        return sum(len(m) for _, m in sp.keymaps)

    best: dict[str, SynapseProfile] = {}
    for sp in sdev.profiles:
        b = base(sp.name)
        if size(sp) and (b not in best or size(sp) > size(best[b])):
            best[b] = sp

    results: list[Imported] = []
    taken = set(existing)
    order = sorted(sdev.profiles, key=lambda sp: best.get(base(sp.name)) is not sp)  # chosen copies name first
    for sp in order:
        chosen = best.get(base(sp.name)) is sp
        group = base(sp.name) if chosen else _clean(sp.name)
        for i, (km_name, mappings) in enumerate(sp.keymaps):
            name = group if i == 0 or not km_name else f"{group} · {km_name}"
            final, n = name, 2
            while final in taken:
                final, n = f"{name} ({n})", n + 1
            taken.add(final)
            prof = model.Profile(device.id, final)
            prof.extra["group"] = group
            prof.extra["imported_from"] = f"Synapse: {sp.name}" + (f" / {km_name}" if km_name else "")
            skipped = []
            for m in mappings:
                inp, why = to_input(m)
                if inp is None:
                    skipped.append(f"{m.get('inputID')}: {why}")
                    continue
                act, note = to_action(m)
                if act is None:
                    skipped.append(f"{m.get('inputID')}: {note}")
                    continue
                if note:
                    skipped.append(f"{m.get('inputID')}: {note}")
                if (isinstance(act, model.KeysAction) and inp.type == ecodes.EV_KEY
                        and sorted(act.keys) == sorted(list(inp.mods) + [model.name_of(inp.code)])):
                    continue  # the button already sends this (onboard memory): nothing to remap
                prof.set(model.Binding(inp, act, "hypershift" if m.get("isHyperShift") else "base"))
            results.append(Imported(final, group, prof, skipped, selected=chosen, source=sp.name))
    return results


def match(sdevs: list[SynapseDevice], devices) -> list[tuple[SynapseDevice, object]]:
    pairs = []
    for sd in sdevs:
        for d in devices:
            if int(d.id.split("-")[1], 16) in sd.product_ids:
                pairs.append((sd, d))
                break
    return pairs


def main(argv):
    from .. import devices as devmod
    folder = locate(Path(argv[1])) if len(argv) > 1 else (candidates() or [None])[0]
    if not folder:
        print("No Synapse data found")
        return 1
    print("Reading", folder)
    devs = devmod.discover()
    for sd, d in match(read(folder), devs):
        print(f"\n== {d.name} ({d.id}) ← Synapse {sd.name or sd.category}: {len(sd.profiles)} profiles")
        for r in convert(sd, d, devs, model.list_profiles(d.id)):
            mark = "x" if r.selected else " "
            print(f"  [{mark}] {r.name}: {len(r.profile.bindings)} binds" +
                  (f", {len(r.skipped)} notes" if r.skipped else ""))
            for s in r.skipped[:4]:
                print("        -", s)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
