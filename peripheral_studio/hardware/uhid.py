"""A virtual HID device that only reports a battery level, so the desktop shows it.

The kernel's hid-input turns its Battery Strength usage into a power_supply
(hid-<uniq>-battery), and UPower lists it next to other wireless devices, with
low-battery warnings. UPower takes the device kind from the input node next to it:
the mouse variant carries mouse usages, and a udev rule marks the headset variant
(data/udev/70-peripheral-studio.rules).
"""
from __future__ import annotations

import os
import struct
import threading

UHID_DESTROY, UHID_GET_REPORT, UHID_GET_REPORT_REPLY = 1, 9, 10
UHID_CREATE2, UHID_INPUT2, UHID_SET_REPORT, UHID_SET_REPORT_REPLY = 11, 12, 13, 14
EVENT_SIZE = 4 + 128 + 64 + 64 + 2 + 2 + 4 * 4 + 4096  # sizeof(struct uhid_event)
BUS_VIRTUAL = 0x06
REPORT_ID = 0x01
EIO = 5

_BATTERY = bytes([
    0x85, REPORT_ID,        # Report ID
    # Battery Strength first: older kernels read a GET_REPORT reply's level from buf[1]
    0x05, 0x06, 0x09, 0x20,  # Usage Page (Generic Device Controls), Usage (Battery Strength)
    0x15, 0x00, 0x25, 0x64,  # Logical 0..100
    0x75, 0x08, 0x95, 0x01, 0x81, 0x02,  # 1 x 8 bits, Input
    0x05, 0x85, 0x09, 0x44,  # Usage Page (Battery System), Usage (Charging)
    0x25, 0x01, 0x75, 0x01, 0x81, 0x02,  # 1 bit, Input
])

# hid-input only keeps a battery on a device that also gets an input node, so each kind
# carries a few (never used) inputs of its own.
DESCRIPTORS = {
    "mouse": bytes([0x05, 0x01, 0x09, 0x02, 0xA1, 0x01]) + _BATTERY + bytes([  # Generic Desktop, Mouse
        0x05, 0x09, 0x19, 0x01, 0x29, 0x03, 0x25, 0x01, 0x75, 0x01, 0x95, 0x03, 0x81, 0x02,  # 3 buttons
        0x95, 0x04, 0x81, 0x03,  # padding to the byte
        0x05, 0x01, 0x09, 0x30, 0x09, 0x31, 0x15, 0x81, 0x25, 0x7F, 0x75, 0x08, 0x95, 0x02, 0x81, 0x06,  # X, Y
        0xC0]),
    "headset": bytes([0x05, 0x0C, 0x09, 0x01, 0xA1, 0x01]) + _BATTERY + bytes([  # Consumer Control
        0x05, 0x0C, 0x09, 0xE2, 0x95, 0x01, 0x75, 0x01, 0x81, 0x02,  # Mute
        0x75, 0x05, 0x81, 0x03,  # padding to the byte
        0xC0]),
}
_REPORT_LEN = {"mouse": 1 + 1 + 1 + 2, "headset": 1 + 1 + 1}  # id, level, charging bit (+ buttons, X, Y)


def _cstr(s: str, n: int) -> bytes:
    b = s.encode()[: n - 1]
    return b + b"\0" * (n - len(b))


class VirtualBattery:
    def __init__(self, kind: str, name: str, uniq: str, vendor: int, product: int):
        self.kind = kind
        self.report = bytearray(_REPORT_LEN[kind])
        self.report[0] = REPORT_ID
        self.lock = threading.Lock()
        self.fd = os.open("/dev/uhid", os.O_RDWR | os.O_CLOEXEC)
        desc = DESCRIPTORS[kind]
        ev = bytearray(EVENT_SIZE)
        struct.pack_into("=I", ev, 0, UHID_CREATE2)
        ev[4:132] = _cstr(name, 128)
        ev[132:196] = _cstr("peripheral-studio", 64)
        ev[196:260] = _cstr(uniq, 64)
        struct.pack_into("=HHIIII", ev, 260, len(desc), BUS_VIRTUAL, vendor, product, 0, 0)
        ev[280:280 + len(desc)] = desc
        os.write(self.fd, ev)
        self.closed = False
        threading.Thread(target=self._events, name=f"uhid-{uniq}", daemon=True).start()

    def update(self, percent: int, charging: bool = False) -> None:
        with self.lock:
            self.report[1] = max(0, min(100, int(percent)))
            self.report[2] = 1 if charging else 0
            ev = struct.pack("=IH", UHID_INPUT2, len(self.report)) + bytes(self.report)
            os.write(self.fd, ev)

    def _events(self) -> None:
        """Answers the kernel's GET_REPORT (asked when something reads the battery early)."""
        while not self.closed:
            try:
                ev = os.read(self.fd, EVENT_SIZE)
            except OSError:
                return
            if len(ev) < 4:
                continue
            kind = struct.unpack_from("=I", ev)[0]
            with self.lock:
                if kind == UHID_GET_REPORT and len(ev) >= 10:
                    req_id, rnum = struct.unpack_from("=IB", ev, 4)
                    if rnum == REPORT_ID:
                        reply = struct.pack("=IIHH", UHID_GET_REPORT_REPLY, req_id, 0, len(self.report)) + bytes(self.report)
                    else:
                        reply = struct.pack("=IIHH", UHID_GET_REPORT_REPLY, req_id, EIO, 0)
                    os.write(self.fd, reply)
                elif kind == UHID_SET_REPORT and len(ev) >= 8:
                    req_id = struct.unpack_from("=I", ev, 4)[0]
                    os.write(self.fd, struct.pack("=IIH", UHID_SET_REPORT_REPLY, req_id, EIO))

    def close(self) -> None:
        if not self.closed:
            self.closed = True
            try:
                os.write(self.fd, struct.pack("=I", UHID_DESTROY))
            finally:
                os.close(self.fd)
