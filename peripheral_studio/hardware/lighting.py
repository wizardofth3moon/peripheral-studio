"""Lighting as data: what a profile saves, and how the engine puts it back on the device.

    {"effect": "static", "colors": [r, g, b, r2, g2, b2], "speed": 2, "wave_dir": 1,
     "brightness": 75, "profile_leds": {"red": false, "green": false, "blue": true}}
"""
from __future__ import annotations

from .devices import DRIVERS, NativeDevice

ONE_COLOUR = ("static", "breath_single", "ripple")


def driver_for(device_id: str) -> NativeDevice | None:
    """The native driver of a device id like "1532-022b", if it has lighting."""
    try:
        pid = int(device_id.split("-")[1], 16)
    except (IndexError, ValueError):
        return None
    for cls in DRIVERS:
        if cls.PID == pid and "brightness" in cls.CAPS:
            return cls.open()
    return None


def capture(dev) -> dict:
    fx = dev.fx
    d = {"effect": fx.effect, "colors": list(fx.colors), "speed": fx.speed, "wave_dir": fx.wave_dir}
    if getattr(fx, "keys", None):
        d["keys"] = fx.keys
    if dev.has("brightness"):
        d["brightness"] = round(dev.brightness)
    leds = {c: bool(getattr(dev, f"profile_led_{c}")) for c in ("red", "green", "blue")
            if dev.has(f"lighting_profile_led_{c}")}
    if leds:
        d["profile_leds"] = leds
    return d


def apply(dev, d: dict) -> None:
    fx = dev.fx
    effect = d.get("effect", "static")
    c = (list(d.get("colors", [])) + [0] * 6)[:6]
    speed = d.get("speed", 2)
    if effect == "custom" and hasattr(dev, "set_key_colours"):
        dev.set_key_colours(d.get("keys", {}), c[:3])
    elif dev.has(f"lighting_{effect}"):
        if effect in ONE_COLOUR:
            getattr(fx, effect)(*c[:3])
        elif effect == "breath_dual":
            fx.breath_dual(*c)
        elif effect == "reactive":
            fx.reactive(*c[:3], speed)
        elif effect == "starlight_single":
            fx.starlight_single(*c[:3], speed)
        elif effect == "starlight_dual":
            fx.starlight_dual(*c, speed)
        elif effect == "starlight_random":
            fx.starlight_random(speed)
        elif effect == "wave":
            fx.wave(d.get("wave_dir", 1))
        else:  # spectrum, breath_random, none
            getattr(fx, effect)()
    if "brightness" in d and dev.has("brightness"):
        dev.brightness = d["brightness"]
    for colour, on in d.get("profile_leds", {}).items():
        if dev.has(f"lighting_profile_led_{colour}"):
            setattr(dev, f"profile_led_{colour}", bool(on))
