import threading

from gi.repository import GLib


def run_bg(fn, done=None):
    """Runs fn() on a worker thread and done(result) back on the GTK thread."""

    def work():
        try:
            result = fn()
        except Exception as ex:  # surfaced to the caller instead of dying silently
            result = ex
        if done:
            GLib.idle_add(lambda: (done(result), False)[1])

    threading.Thread(target=work, daemon=True).start()
