"""On-screen popups for profile, DPI and Hypershift changes.

Prefers GNOME's own on-screen display through the bundled shell extension
(data/gnome-shell/peripheral-studio-osd@peripheral-studio); without it, a short desktop
notification that replaces the previous one. Shown from a worker thread: never blocks input.
Settings in ~/.config/peripheral-studio/settings.json: {"osd": {"profile": true, "dpi": true,
"hypershift": true}}.
"""
from __future__ import annotations

import json
import queue
import threading

from . import model

_queue: queue.Queue = queue.Queue()
_started = False


def enabled(kind: str) -> bool:
    try:
        return bool(json.loads((model.ROOT / "settings.json").read_text()).get("osd", {}).get(kind, True))
    except (OSError, ValueError):
        return True


def show(kind: str, icon: str, label: str, level: float = -1) -> None:
    global _started
    if not enabled(kind):
        return
    if not _started:
        _started = True
        threading.Thread(target=_worker, name="osd", daemon=True).start()
    _queue.put((icon, label, level))


def _worker():
    from gi.repository import Gio, GLib

    bus = Gio.bus_get_sync(Gio.BusType.SESSION)
    notification_id = 0
    while True:
        icon, label, level = _queue.get()
        while not _queue.empty():  # only the latest of a burst (e.g. scrolling through DPI stages)
            icon, label, level = _queue.get()
        try:
            bus.call_sync("org.peripheralstudio.Osd", "/org/peripheralstudio/Osd", "org.peripheralstudio.Osd", "Show",
                          GLib.Variant("(ssd)", (icon, label, float(level))), None, Gio.DBusCallFlags.NONE, 500)
            continue
        except GLib.Error:
            pass  # extension not active: fall back to a notification
        try:
            hints = {"transient": GLib.Variant("b", True), "urgency": GLib.Variant("y", 0),
                     "x-gnome-stack": GLib.Variant("s", "peripheral-studio")}
            reply = bus.call_sync("org.freedesktop.Notifications", "/org/freedesktop/Notifications",
                                  "org.freedesktop.Notifications", "Notify",
                                  GLib.Variant("(susssasa{sv}i)", ("Peripheral Studio", notification_id, icon, label, "",
                                                                    [], hints, 1500)),
                                  GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, 1000)
            notification_id = reply.unpack()[0]
        except GLib.Error:
            pass
