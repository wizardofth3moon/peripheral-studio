"""Razer's 90-byte command reports over Linux hidraw feature reports.

Every setting (lighting, DPI, polling rate, battery, ...) is one request report sent with
HIDIOCSFEATURE, answered by a report of the same shape read back with HIDIOCGFEATURE.
"""
from __future__ import annotations

import fcntl
import os
import threading
import time
from dataclasses import dataclass

REPORT_LEN = 90

# report status byte
STATUS_NEW, STATUS_BUSY, STATUS_OK, STATUS_FAIL, STATUS_TIMEOUT, STATUS_UNSUPPORTED = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05


def _ioc(direction: int, kind: int, nr: int, size: int) -> int:
    return direction << 30 | size << 16 | kind << 8 | nr


_IOC_WRITE, _IOC_READ = 1, 2


def HIDIOCSFEATURE(n: int) -> int:
    return _ioc(_IOC_WRITE | _IOC_READ, ord("H"), 0x06, n)


def HIDIOCGFEATURE(n: int) -> int:
    return _ioc(_IOC_WRITE | _IOC_READ, ord("H"), 0x07, n)


class RazerError(IOError):
    pass


class NoAnswer(RazerError):
    """The device didn't answer in time - for a wireless mouse: it's asleep or out of range."""


@dataclass
class Reply:
    status: int
    command_class: int
    command_id: int
    args: bytes


def build(transaction_id: int, command_class: int, command_id: int, data_size: int, args: bytes = b"") -> bytes:
    r = bytearray(REPORT_LEN)
    r[0] = STATUS_NEW
    r[1] = transaction_id
    # r[2:4] remaining packets, r[4] protocol type: 0
    r[5] = data_size
    r[6] = command_class
    r[7] = command_id
    r[8:8 + len(args)] = args
    crc = 0
    for b in r[2:88]:
        crc ^= b
    r[88] = crc
    return bytes(r)


class Transport:
    """One device's command interface (a hidraw node). Thread-safe; one request at a time."""

    def __init__(self, path: str, transaction_id: int, settle: float = 0.0006):
        self.path = path
        self.transaction_id = transaction_id
        self.settle = settle
        self.lock = threading.Lock()
        os.close(os.open(path, os.O_RDWR | os.O_CLOEXEC))  # fail early without access

    def close(self):
        pass

    def request(self, command_class: int, command_id: int, data_size: int, args: bytes = b"",
                retries: int = 3) -> Reply:
        report = build(self.transaction_id, command_class, command_id, data_size, args)
        # opened per request: device lists are rebuilt often, and a held node would leak
        with self.lock:
            fd = os.open(self.path, os.O_RDWR | os.O_CLOEXEC)
            try:
                return self._request(fd, report, command_class, command_id, retries)
            finally:
                os.close(fd)

    def _request(self, fd: int, report: bytes, command_class: int, command_id: int, retries: int) -> Reply:
        for attempt in range(retries + 1):
            buf = bytearray(b"\x00" + report)  # report id 0
            fcntl.ioctl(fd, HIDIOCSFEATURE(len(buf)), buf)
            time.sleep(self.settle)
            reply = bytearray(REPORT_LEN + 1)
            fcntl.ioctl(fd, HIDIOCGFEATURE(len(reply)), reply)
            r = reply[1:]
            status = r[0]
            if status == STATUS_BUSY and attempt < retries:
                time.sleep(0.01 * (attempt + 1))
                continue
            if status == STATUS_UNSUPPORTED:
                raise RazerError(f"command {command_class:#04x}/{command_id:#04x} not supported")
            if status in (STATUS_FAIL, STATUS_TIMEOUT):
                if attempt < retries:
                    time.sleep(0.02)
                    continue
                if status == STATUS_TIMEOUT:
                    raise NoAnswer(f"command {command_class:#04x}/{command_id:#04x}: no answer from the device")
                raise RazerError(f"command {command_class:#04x}/{command_id:#04x} failed (status {status})")
            return Reply(status, r[6], r[7], bytes(r[8:8 + 80]))
        raise RazerError("device busy")
