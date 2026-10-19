"""The app's side of the engine's control socket."""
from __future__ import annotations

import json
import socket
import threading

from gi.repository import GLib

from .daemon import SOCKET


class EngineUnavailable(Exception):
    pass


def call(cmd: str, timeout: float = 2.0, **args) -> dict:
    """One request, one reply. Raises EngineUnavailable when the service isn't running."""
    try:
        with socket.socket(socket.AF_UNIX) as s:
            s.settimeout(timeout)
            s.connect(str(SOCKET))
            s.sendall((json.dumps({"cmd": cmd, **args}) + "\n").encode())
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
    except (FileNotFoundError, ConnectionRefusedError, socket.timeout, OSError) as ex:
        raise EngineUnavailable(str(ex)) from ex
    reply = json.loads(data or b"{}")
    if not reply.get("ok", False):
        raise RuntimeError(reply.get("error", "engine error"))
    return reply


def available() -> bool:
    try:
        call("ping", timeout=0.5)
        return True
    except (EngineUnavailable, RuntimeError):
        return False


class Watcher:
    """Streams engine events (button presses, profile and Hypershift changes) to a callback
    on the GTK thread. Reconnects when the engine restarts."""

    def __init__(self, on_event):
        self.on_event = on_event
        self._stop = threading.Event()
        threading.Thread(target=self._run, daemon=True).start()

    def stop(self):
        self._stop.set()

    def _run(self):
        while not self._stop.is_set():
            try:
                with socket.socket(socket.AF_UNIX) as s:
                    s.connect(str(SOCKET))
                    s.settimeout(1.0)
                    s.sendall(b'{"cmd": "watch"}\n')
                    buf = b""
                    while not self._stop.is_set():
                        try:
                            chunk = s.recv(65536)
                        except socket.timeout:
                            continue
                        if not chunk:
                            break
                        buf += chunk
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            msg = json.loads(line)
                            if "event" in msg:
                                GLib.idle_add(lambda m=msg: (self.on_event(m), False)[1])
            except OSError:
                pass
            self._stop.wait(2.0)
