"""Razer BlackShark V3 headset: battery level and charging state over its vendor HID channel.

Frames are 64-byte output/input reports with id 0x02 on the dongle's HID interface (5):

    request  [0]=0x02 [2]=0x60 [6]=4+len(args) [9]=0x80 [10]=class [12]=argc [13..]=args
             [62]=XOR of bytes 0..61
    reply    [10]=class [11]=0x01 (answer) or 0x02 (the headset pushing a change)
             [12]=count [13..]=values

Battery is class 0x21 (percent 0..100), charging 0x2A (0/1); 0x20 with value 1 means the
radio link (re)connected. Command values from OpenRazer PR #2794 (GPL-2.0), which decoded them
from Synapse captures.

Two firmware quirks shape this:
- The dongle only opens this channel if the host asked for 255 bytes in its very first
  configuration-descriptor read after power-on (Windows does, Linux asks for 9). Without the
  razer-cfg255 kernel module (or the upcoming usbcore quirk) the headset never answers.
- Querying the battery often makes the headset drop its link and restart, so it is asked once
  per connection; after that the headset's own pushes keep the value current.
"""
from __future__ import annotations

import os
import select
import threading
import time
from pathlib import Path

VENDOR, PRODUCTS = 0x1532, (0x057A, 0x0579)  # 2.4 GHz dongle, USB cable
REPORT_ID, REPORT_LEN, INTERFACE = 0x02, 64, 5
BATTERY, CHARGING, LINK, INIT = 0x21, 0x2A, 0x20, 0x02
MIN_QUERY_GAP = 600  # seconds between battery questions, beyond the first per connection


def find_hidraw() -> tuple[str, int] | None:
    for node in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
        hid = (node / "device").resolve()
        try:
            _bus, v, p = hid.name.split(".")[0].split(":")
            pid = int(p, 16)
            if int(v, 16) != VENDOR or pid not in PRODUCTS:
                continue
            if int((hid.parent / "bInterfaceNumber").read_text(), 16) != INTERFACE:
                continue
            if b"\x06\x14\xff" in (hid / "report_descriptor").read_bytes():  # usage page 0xFF14 carries id 0x02
                return f"/dev/{node.name}", pid
        except (ValueError, OSError):
            continue
    return None


def frame(cls: int, direction: int = 0x80, args: bytes = b"", count: int | None = None) -> bytes:
    """count overrides byte 12 (the argument count): a few commands carry a selector there instead."""
    b = bytearray(REPORT_LEN)
    b[0], b[2], b[6], b[9], b[10] = REPORT_ID, 0x60, 4 + len(args), direction, cls
    b[12] = len(args) if count is None else count
    b[13:13 + len(args)] = args
    x = 0
    for v in b[:62]:
        x ^= v
    b[62] = x
    return bytes(b)


class BlackSharkV3:
    """Keeps the headset's battery state up to date in a background thread."""

    NAME = "Razer BlackShark V3"
    type = "headset"

    def __init__(self, path: str, product: int, on_change=None):
        self.path, self.product = path, product
        self.on_change = on_change or (lambda: None)
        self.percent: int | None = None
        self.charging: bool | None = None
        self.answered = False  # whether the vendor channel works at all (see the module docstring)
        self._asked_at = 0.0
        self._stop = threading.Event()
        self._fd = os.open(path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        threading.Thread(target=self._run, name="blackshark", daemon=True).start()

    @classmethod
    def open(cls, on_change=None) -> "BlackSharkV3 | None":
        found = find_hidraw()
        if not found:
            return None
        try:
            return cls(*found, on_change=on_change)
        except OSError:
            return None

    def close(self):
        self._stop.set()

    # -- protocol ------------------------------------------------------------

    def _send(self, data: bytes):
        try:
            os.write(self._fd, data)
        except OSError:
            pass

    def _handshake(self):
        """Synapse's wake sequence; without it the dongle ignores the questions that follow."""
        for cls, direction in ((INIT, 0x00), (CHARGING, 0x00), (CHARGING, 0x80)):
            self._send(frame(cls, direction))
            time.sleep(0.003)

    def _ask(self):
        self._asked_at = time.monotonic()
        self._handshake()
        self._send(frame(BATTERY))
        time.sleep(0.12)
        self._send(frame(CHARGING))

    def _handle(self, d: bytes):
        if len(d) < 14 or d[0] != REPORT_ID or d[11] not in (0x01, 0x02) or d[12] < 1:
            return
        cls, value = d[10], d[13]
        changed = False
        if cls == BATTERY and value <= 100:
            changed = value != self.percent
            self.percent, self.answered = value, True
        elif cls == CHARGING and value <= 1:
            changed = bool(value) != self.charging
            self.charging, self.answered = bool(value), True
        elif cls == LINK and value == 0x01:
            # link came back: the old values may be stale; ask again shortly
            self.percent = self.charging = None
            changed = True
            self._asked_at = 0.0
        if changed:
            self.on_change()

    def _run(self):
        time.sleep(0.5)
        self._ask()
        while not self._stop.is_set():
            r, _, _ = select.select([self._fd], [], [], 5)
            if r:
                try:
                    self._handle(os.read(self._fd, REPORT_LEN))
                except BlockingIOError:
                    pass
                except OSError:
                    self._stop.set()  # unplugged: the battery service opens it again when it's back
                    return
            # ask again only after a link change (asked_at reset) or a long quiet spell
            since = time.monotonic() - self._asked_at
            if (self._asked_at == 0.0 and since > 1) or (self.percent is None and since > MIN_QUERY_GAP):
                self._ask()


# ── Settings (EQ, mic, sidetone, ...) ───────────────────────────────────────────
# Each setting has a setter class and a getter class (setter - 0x80), verified on a V3
# dongle by changing every value and reading it back (2026-10-19).

EQ_PRESETS = ["Default", "Game", "Movie", "Music", "Esports"]
EQ_BANDS_HZ = [31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000]
# factory band gains (dB) per preset, from Synapse captures (OpenRazer PR #2794)
EQ_FACTORY = [
    [0, 0, 0, 0, 0, 0, 0, 0, 0, 0],
    [2, 2, 5, 5, 1, -1, 2, 3, 3, 3],
    [3, 3, 3, -1, -4, -4, 2, 3, 3, 3],
    [2, 2, 0, 0, 1, -1, -1, 3, 3, 3],
    [1, 1, -1, 0, 2, 0, 4, 4, 4, -3],
]
_EQ_APPLY = [(0x00, 0x00, 0x01), (0x01, 0x01, 0x01), (0x03, 0x03, 0x01), (0x02, 0x02, 0x01), (0x04, 0x0A, 0x00)]
MIC_PRESETS = ["Default", "Mic boost", "Broadcast", "Esports"]  # 0x20 + index
POWER_OFF = [0, 15, 30, 45, 60]  # minutes, 0 = never
IN_CALL = ["Mix 2.4 GHz and Bluetooth", "Lower 2.4 GHz volume", "Mute 2.4 GHz"]
FN_BUTTON = ["Game/chat balance", "Sidetone", "Footstep boost", "Bluetooth volume"]
DONGLE_LED = ["Connection status", "Battery level", "Battery warnings only"]


def _gain(db: int) -> int:
    db = max(-6, min(6, int(db)))
    return 0x80 | -db if db < 0 else db


class BlackSharkControl:
    """Reads and changes the headset's settings. Short-lived: open, use, close."""

    def __init__(self):
        found = find_hidraw()
        if not found:
            raise OSError("BlackShark V3 not connected")
        self.path, self.product = found
        self.direction = 0x80 if self.product == 0x057A else 0x00  # through the dongle / over the cable
        self.fd = os.open(self.path, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        for cls, d in ((INIT, 0x00), (CHARGING, 0x00), (CHARGING, 0x80)):
            self._write(frame(cls, d))
            time.sleep(0.003)
        self._replies(0.2)

    def close(self):
        os.close(self.fd)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

    def _write(self, data: bytes):
        os.write(self.fd, data)

    def _replies(self, wait: float, cls: int | None = None) -> list[bytes]:
        end, out = time.monotonic() + wait, []
        while time.monotonic() < end:
            r, _, _ = select.select([self.fd], [], [], max(0.0, end - time.monotonic()))
            if not r:
                break
            try:
                d = os.read(self.fd, REPORT_LEN)
            except BlockingIOError:
                continue
            if len(d) > 13 and d[0] == REPORT_ID and (cls is None or d[10] == cls):
                out.append(d)
                if cls is not None:
                    break
        return out

    def get(self, cls: int) -> bytes | None:
        """The values a getter class answers with, or None when the headset doesn't answer."""
        self._write(frame(cls, self.direction))
        got = self._replies(0.3, cls)
        if not got or got[0][13] == 0xFF and got[0][12] == 1:  # 0xff: "not ready" (link still coming up)
            return None
        n = max(1, got[0][12])
        return bytes(got[0][13:13 + n])

    def _set(self, cls: int, args: bytes, count: int | None = None):
        self._write(frame(cls, self.direction, args, count))
        time.sleep(0.03)

    def read_all(self) -> dict:
        def one(cls, i=0):
            v = self.get(cls)
            return v[i] if v and len(v) > i else None
        mic = one(0x16)
        power = one(0x2C)
        return {
            "sidetone": one(0x19), "mic_preset": None if mic is None else mic - 0x20,
            "power_off": power, "thx": one(0x1E), "balance": one(0x5C), "in_call": one(0x5D),
            "prompts": one(0x65, 1), "fn_button": one(0x6A), "low_latency": one(0x5F), "led": one(0x66),
        }

    def set_sidetone(self, level: int):
        self._set(0x98, b"\x01")
        self._set(0x99, bytes([max(0, min(15, int(level)))]))

    def set_mic_preset(self, index: int):
        self._set(0x96, bytes([0x20 + max(0, min(3, int(index)))]))

    def set_power_off(self, minutes: int):
        self._set(0xAC, bytes([min(POWER_OFF, key=lambda m: abs(m - int(minutes)))]))

    def set_thx(self, on: bool):
        self._set(0x9E, b"\x01" if on else b"\x00")

    def set_balance(self, value: int):
        """0 = all game, 10 = centre, 20 = all chat."""
        self._set(0xDC, bytes([max(0, min(20, int(value))), 0]), count=1)

    def set_in_call(self, mode: int):
        self._set(0xDD, bytes([max(0, min(2, int(mode))), 0]), count=1)

    def set_prompts(self, on: bool):
        self._set(0xE5, bytes([0, 1 if on else 0, 0]), count=2)

    def set_fn_button(self, mode: int):
        self._set(0xEA, bytes([max(0, min(3, int(mode)))]))

    def set_low_latency(self, on: bool):
        self._set(0xDF, b"\x01" if on else b"\x00")

    def set_led(self, mode: int):
        self._set(0xE6, bytes([max(0, min(2, int(mode)))]))

    def set_eq(self, preset: int, bands: list[int] | None = None):
        """Writes a preset's 10 bands (its factory ones unless given) and makes it the active EQ."""
        p = max(0, min(4, int(preset)))
        gains = bytes(_gain(b) for b in (bands if bands is not None else EQ_FACTORY[p]))
        a14, a17, a18 = _EQ_APPLY[p]
        self._set(0xE1, b"\x01")  # begin
        self._set(0x95, bytes([p]) + gains)  # bands
        self._set(0xE0, bytes([p, a14, 1, 1, a17, a18]))  # apply
        self._set(0xE1, b"\x02")  # end
        self._set(0xEB, bytes([p, 0, 0, 1, 1]) + bytes(6), count=0x0B)  # commit
