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


def frame(cls: int, direction: int = 0x80, args: bytes = b"") -> bytes:
    b = bytearray(REPORT_LEN)
    b[0], b[2], b[6], b[9], b[10], b[12] = REPORT_ID, 0x60, 4 + len(args), direction, cls, len(args)
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
