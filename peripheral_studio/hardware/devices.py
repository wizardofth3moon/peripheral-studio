"""Native drivers for the Razer devices Peripheral Studio supports without OpenRazer.

Each driver offers the same calls the Lighting and Performance pages already use with an
openrazer.client device (has(), fx.static(...), brightness, dpi, poll_rate, battery_level, ...),
so a device can come from either backend. Command values follow the OpenRazer driver
(GPL-2.0), see docs/razer-protocol.md.
"""
from __future__ import annotations

import json
import math
import os
from pathlib import Path

from .transport import NoAnswer, RazerError, Transport

VENDOR = 0x1532
STATE_FILE = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "peripheral-studio" / "hardware-state.json"

NOSTORE, VARSTORE = 0x00, 0x01
ZERO_LED, BACKLIGHT_LED, MACRO_LED, GAME_LED = 0x00, 0x05, 0x07, 0x08
PROFILE_LEDS = {"red": 0x0C, "green": 0x0D, "blue": 0x0E}


def find_hidraw(vid: int, pid: int, interface: int) -> str | None:
    """/dev/hidrawN of one USB interface of a device."""
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        hid = (node / "device").resolve()  # .../1-3:1.0/0003:1532:00B4.000A
        try:
            _bus, v, p = hid.name.split(".")[0].split(":")
            if (int(v, 16), int(p, 16)) != (vid, pid):
                continue
            if int((hid.parent / "bInterfaceNumber").read_text(), 16) == interface:
                return f"/dev/{node.name}"
        except (ValueError, OSError):
            continue
    return None


class _State:
    """The last lighting a device was given: the hardware can't report its current effect."""

    def __init__(self, key: str):
        self.key = key

    def load(self) -> dict:
        try:
            return json.loads(STATE_FILE.read_text()).get(self.key, {})
        except (OSError, ValueError):
            return {}

    def save(self, **values):
        try:
            data = json.loads(STATE_FILE.read_text())
        except (OSError, ValueError):
            data = {}
        data.setdefault(self.key, {}).update(values)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        STATE_FILE.write_text(json.dumps(data, indent=1))


class NativeDevice:
    """Common parts: identity, capability list, the command interface."""

    PID = 0
    NAME = ""
    INTERFACE = 0
    SETTLE = 0.0008
    TID = 0x1F
    CAPS: frozenset[str] = frozenset()

    def __init__(self, path: str):
        self.transport = Transport(path, self.TID, self.SETTLE)
        self.name = self.NAME
        self.backend = "native"

    @classmethod
    def open(cls) -> "NativeDevice | None":
        path = find_hidraw(VENDOR, cls.PID, cls.INTERFACE)
        if not path:
            return None
        try:
            return cls(path)
        except OSError:
            return None

    def has(self, cap: str) -> bool:
        return cap in self.CAPS

    def cmd(self, cls: int, cid: int, size: int, args: bytes = b"", tid: int | None = None):
        if tid is None or tid == self.transport.transaction_id:
            return self.transport.request(cls, cid, size, args)
        old, self.transport.transaction_id = self.transport.transaction_id, tid
        try:
            return self.transport.request(cls, cid, size, args)
        finally:
            self.transport.transaction_id = old

    @property
    def firmware_version(self) -> str:
        a = self.cmd(0x00, 0x81, 0x02, tid=self.INFO_TID).args
        return f"v{a[0]}.{a[1]}"

    @property
    def serial(self) -> str:
        return self.cmd(0x00, 0x82, 0x16, tid=self.INFO_TID).args[:22].split(b"\0")[0].decode("ascii", "replace").strip()

    INFO_TID = 0xFF


# ── Lighting (extended matrix effects, class 0x0F) ─────────────────────────────

class ExtendedFx:
    """openrazer-style fx object for keyboards/keypads with the extended effect commands."""

    def __init__(self, dev: "LitDevice"):
        self.dev = dev
        self.state = _State(dev.state_key)
        s = self.state.load()
        self.effect = s.get("effect", "static")
        self.colors = bytes(s.get("colors", [0, 255, 0, 0, 0, 255]))
        self.speed = s.get("speed", 2)
        self.wave_dir = s.get("wave_dir", 1)
        self.keys = s.get("keys", {})  # per-key colours, for the "custom" effect

    def _send(self, name: str, size: int, effect: int, extra: bytes = b"", colors: bytes = b"", tid: int | None = None,
              speed: int | None = None, wave_dir: int | None = None):
        args = bytes([VARSTORE, BACKLIGHT_LED, effect]) + extra
        self.dev.cmd(0x0F, 0x02, size, args, tid=tid)
        self.effect = name
        if colors:
            self.colors = (colors + self.colors[len(colors):])[:6]
        if speed is not None:
            self.speed = speed
        if wave_dir is not None:
            self.wave_dir = wave_dir
        self.state.save(effect=self.effect, colors=list(self.colors), speed=self.speed, wave_dir=self.wave_dir)
        return True

    def none(self):
        return self._send("none", 0x06, 0x00)

    def static(self, r, g, b):
        rgb = bytes([r, g, b])
        return self._send("static", 0x09, 0x01, bytes([0, 0, 1]) + rgb, rgb)

    def spectrum(self):
        return self._send("spectrum", 0x06, 0x03)

    def wave(self, direction):
        d = max(0, min(2, int(direction)))
        return self._send("wave", 0x06, 0x04, bytes([d, 0x28]), wave_dir=d)

    def breath_random(self):
        return self._send("breath_random", 0x06, 0x02, tid=self.dev.BREATH_TID)

    def breath_single(self, r, g, b):
        rgb = bytes([r, g, b])
        return self._send("breath_single", 0x09, 0x02, bytes([1, 0, 1]) + rgb, rgb, tid=self.dev.BREATH_TID)

    def breath_dual(self, r, g, b, r2, g2, b2):
        c = bytes([r, g, b, r2, g2, b2])
        return self._send("breath_dual", 0x0C, 0x02, bytes([2, 0, 2]) + c, c, tid=self.dev.BREATH_TID)

    def reactive(self, r, g, b, speed):
        s = max(1, min(4, int(speed)))
        rgb = bytes([r, g, b])
        return self._send("reactive", 0x09, 0x05, bytes([0, s, 1]) + rgb, rgb, speed=s)

    def starlight_random(self, speed):
        s = max(1, min(3, int(speed)))
        return self._send("starlight_random", 0x06, 0x07, bytes([0, s]), speed=s)

    def starlight_single(self, r, g, b, speed):
        s = max(1, min(3, int(speed)))
        rgb = bytes([r, g, b])
        return self._send("starlight_single", 0x09, 0x07, bytes([0, s, 1]) + rgb, rgb, speed=s)

    def starlight_dual(self, r, g, b, r2, g2, b2, speed):
        s = max(1, min(3, int(speed)))
        c = bytes([r, g, b, r2, g2, b2])
        return self._send("starlight_dual", 0x0C, 0x07, bytes([0, s, 2]) + c, c, speed=s)


class LitDevice(NativeDevice):
    INTERFACE = 1
    BRIGHTNESS_LED = BACKLIGHT_LED
    BREATH_TID = None

    def __init__(self, path: str):
        super().__init__(path)
        self.state_key = f"{VENDOR:04x}:{self.PID:04x}"
        self.fx = ExtendedFx(self)

    @property
    def brightness(self) -> float:
        raw = self.cmd(0x0F, 0x84, 0x03, bytes([VARSTORE, self.BRIGHTNESS_LED])).args[2]
        return round(raw * 100 / 255, 1)

    @brightness.setter
    def brightness(self, percent: float):
        raw = max(0, min(255, round(float(percent) * 255 / 100)))
        self.cmd(0x0F, 0x04, 0x03, bytes([VARSTORE, self.BRIGHTNESS_LED, raw]))


# Tartarus V2 lighting matrix (4 rows x 6 columns, from OpenRGB's RazerDevices.cpp): which light is which key.
TARTARUS_V2_MATRIX = {**{f"key{n:02d}": ((n - 1) // 5, (n - 1) % 5) for n in range(1, 20)},
                      "wheel_click": (3, 4), "space": (3, 5)}


class TartarusV2(LitDevice):
    PID = 0x022B
    NAME = "Razer Tartarus V2"
    type = "keypad"
    MATRIX = TARTARUS_V2_MATRIX
    ROWS, COLS = 4, 6

    def set_key_colours(self, keys: dict[str, tuple[int, int, int]], base=(0, 0, 0)):
        """Per-key lighting: one colour per lit key (by layout button id), base colour for the rest."""
        grid = [[tuple(base)] * self.COLS for _ in range(self.ROWS)]
        for key, rgb in keys.items():
            if key in self.MATRIX:
                r, c = self.MATRIX[key]
                grid[r][c] = tuple(int(v) for v in rgb[:3])
        for row, colours in enumerate(grid):
            rgb = bytes(v for colour in colours for v in colour)
            self.cmd(0x0F, 0x03, 0x47, bytes([0, 0, row, 0, self.COLS - 1]) + rgb)
        self.cmd(0x0F, 0x02, 0x0C, bytes([NOSTORE, ZERO_LED, 0x08]))  # show the custom frame
        self.fx.effect = "custom"
        self.fx.keys = {k: list(v)[:3] for k, v in keys.items()}
        self.fx.colors = (bytes(base) + self.fx.colors[3:])[:6]
        self.fx.state.save(effect="custom", keys=self.fx.keys, colors=list(self.fx.colors))
    BRIGHTNESS_LED = ZERO_LED
    BREATH_TID = 0x3F  # the only Tartarus V2 command sent with 0x3F (OpenRazer razerkbd_driver.c)
    CAPS = frozenset({
        "lighting_static", "lighting_spectrum", "lighting_wave", "lighting_breath_single", "lighting_breath_dual",
        "lighting_breath_random", "lighting_reactive", "lighting_starlight_single", "lighting_starlight_dual",
        "lighting_starlight_random", "lighting_none", "brightness", "lighting_custom",
        "lighting_profile_led_red", "lighting_profile_led_green", "lighting_profile_led_blue",
    })

    def _profile_led(self, colour: str) -> bool:
        return bool(self.cmd(0x03, 0x80, 0x03, bytes([VARSTORE, PROFILE_LEDS[colour]]), tid=0xFF).args[2])

    def _set_profile_led(self, colour: str, on: bool):
        self.cmd(0x03, 0x00, 0x03, bytes([VARSTORE, PROFILE_LEDS[colour], 1 if on else 0]), tid=0xFF)

    profile_led_red = property(lambda s: s._profile_led("red"), lambda s, v: s._set_profile_led("red", v))
    profile_led_green = property(lambda s: s._profile_led("green"), lambda s, v: s._set_profile_led("green", v))
    profile_led_blue = property(lambda s: s._profile_led("blue"), lambda s, v: s._set_profile_led("blue", v))


class OrnataV3X(LitDevice):
    PID = 0x02A2  # ORNATA_V3_X_ALT; same commands as 0x0294
    NAME = "Razer Ornata V3 X"
    type = "keyboard"
    CAPS = frozenset({
        "lighting_static", "lighting_spectrum", "lighting_breath_single", "lighting_breath_dual",
        "lighting_breath_random", "lighting_none", "brightness",
    })


class OrnataV3XAlt(OrnataV3X):
    PID = 0x0294


# ── Mouse: DPI, polling rate, battery (Naga V2 HyperSpeed has no lighting) ──────

POLL_CODES = {1000: 0x01, 500: 0x02, 125: 0x08}


class NagaV2HyperSpeed(NativeDevice):
    PID = 0x00B4
    NAME = "Razer Naga V2 HyperSpeed"
    type = "mouse"
    INTERFACE = 0
    SETTLE = 0.031  # the receiver relays to the mouse: OpenRazer waits ~31 ms before reading the reply
    INFO_TID = 0x1F
    CAPS = frozenset({"dpi", "poll_rate", "battery", "set_idle_time", "set_low_battery_threshold"})
    max_dpi = 30000
    supported_poll_rates = [125, 500, 1000]

    # While the mouse sleeps only the receiver answers: reads then report 0, as OpenRazer does,
    # which the Performance page shows as "the mouse is asleep".
    def _read(self, cls: int, cid: int, size: int, args: bytes = b"") -> bytes | None:
        try:
            return self.cmd(cls, cid, size, args).args
        except NoAnswer:
            return None

    @property
    def dpi(self) -> tuple[int, int]:
        a = self._read(0x04, 0x85, 0x07, bytes([NOSTORE]))
        if a is None:
            return 0, 0
        return a[1] << 8 | a[2], a[3] << 8 | a[4]

    @dpi.setter
    def dpi(self, value: tuple[int, int]):
        x, y = (max(100, min(self.max_dpi, int(v))) for v in value)
        self.cmd(0x04, 0x05, 0x07, bytes([VARSTORE, x >> 8, x & 0xFF, y >> 8, y & 0xFF, 0, 0]))

    @property
    def dpi_stages(self) -> tuple[int, list[tuple[int, int]]]:
        a = self._read(0x04, 0x86, 0x26, bytes([VARSTORE]))
        if a is None:
            return 0, []
        active, count = a[1], min(a[2], 5)
        stages = []
        for i in range(count):
            o = 3 + i * 7  # [stage number, Xhi, Xlo, Yhi, Ylo, 0, 0]
            stages.append((a[o + 1] << 8 | a[o + 2], a[o + 3] << 8 | a[o + 4]))
        return active, stages

    def set_dpi_stages(self, stages: list[tuple[int, int]], active: int = 1):
        """Up to 5 stages, (x, y) each; `active` is 1-based. Entries carry 1-based stage numbers, as the
        mouse reports them."""
        stages = [(max(100, min(self.max_dpi, int(x))), max(100, min(self.max_dpi, int(y)))) for x, y in stages[:5]]
        if not stages:
            raise ValueError("at least one DPI stage is needed")
        args = bytearray([VARSTORE, max(1, min(len(stages), int(active))), len(stages)])
        for i, (x, y) in enumerate(stages, 1):
            args += bytes([i, x >> 8, x & 0xFF, y >> 8, y & 0xFF, 0, 0])
        self.cmd(0x04, 0x06, 0x26, bytes(args))

    @property
    def poll_rate(self) -> int:
        a = self._read(0x00, 0x85, 0x01)
        if a is None:
            return 0
        code = a[0]
        return next((hz for hz, c in POLL_CODES.items() if c == code), 0)

    @poll_rate.setter
    def poll_rate(self, hz: int):
        self.cmd(0x00, 0x05, 0x01, bytes([POLL_CODES.get(int(hz), 0x02)]))

    @property
    def battery_level(self) -> float:
        a = self._read(0x07, 0x80, 0x02)
        return 0.0 if a is None else round(a[1] * 100 / 255, 1)

    @property
    def is_charging(self) -> bool:
        return False  # this receiver doesn't report it (OpenRazer returns 0 as well)

    def get_idle_time(self) -> int:
        a = self._read(0x07, 0x83, 0x02)
        return 0 if a is None else a[0] << 8 | a[1]

    def set_idle_time(self, seconds: int):
        s = max(60, min(900, int(seconds)))
        self.cmd(0x07, 0x03, 0x02, bytes([s >> 8, s & 0xFF]))

    def get_low_battery_threshold(self) -> int:
        a = self._read(0x07, 0x81, 0x01)
        return 0 if a is None else round(a[0] / 255 * 100)

    def set_low_battery_threshold(self, percent: int):
        raw = max(0x0C, min(0x3F, math.floor(int(percent) / 100 * 255)))
        self.cmd(0x07, 0x01, 0x01, bytes([raw]), tid=0xFF)


DRIVERS = [NagaV2HyperSpeed, TartarusV2, OrnataV3X, OrnataV3XAlt]


def native_devices() -> dict[int, NativeDevice]:
    """Connected devices with a native driver, by USB product id."""
    found = {}
    for cls in DRIVERS:
        dev = cls.open()
        if dev:
            found[cls.PID] = dev
    return found


__all__ = ["native_devices", "find_hidraw", "RazerError", "DRIVERS"]
