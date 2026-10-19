"""Performance and power: DPI, polling rate, battery, sleep timer (native driver or OpenRazer)."""
from __future__ import annotations

from gi.repository import Adw, GLib, Gtk

POLL_RATES = [125, 250, 500, 1000, 2000, 4000, 8000]


def _try(fn, default=None):
    try:
        return fn()
    except Exception:
        return default


def has_performance(device) -> bool:
    return device.has("dpi") or device.has("poll_rate")


def has_power(device) -> bool:
    return device.has("battery") or device.has("set_idle_time")


class PerformancePage(Adw.PreferencesPage):
    def __init__(self, window, device):
        super().__init__()
        self.window = window
        self.device = device
        self.razer = device.razer
        self._loading = True
        self._dpi_timer = 0

        self.banner_group = Adw.PreferencesGroup()
        self.asleep = Adw.ActionRow(title="The mouse isn't answering",
                                    subtitle="It's probably asleep or switched off. Move it, then refresh.")
        self.asleep.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        refresh = Gtk.Button(label="Refresh", valign=Gtk.Align.CENTER)
        refresh.connect("clicked", lambda *_: self._load())
        self.asleep.add_suffix(refresh)
        self.banner_group.add(self.asleep)
        self.add(self.banner_group)

        if device.has("dpi"):
            group = Adw.PreferencesGroup(title="Sensitivity")
            max_dpi = _try(lambda: self.razer.max_dpi, 30000) or 30000
            self.dpi_row = Adw.SpinRow.new_with_range(100, max_dpi, 50)
            self.dpi_row.set_title("DPI")
            self.dpi_row.set_subtitle("Same on both axes")
            self.dpi_row.connect("notify::value", self._on_dpi)
            group.add(self.dpi_row)
            self.stages_row = Adw.ActionRow(title="DPI stages", subtitle="Set on the mouse with its DPI buttons")
            group.add(self.stages_row)
            self.add(group)
        else:
            self.dpi_row = None

        if device.has("poll_rate"):
            group = Adw.PreferencesGroup(title="Polling rate")
            supported = _try(lambda: list(self.razer.supported_poll_rates)) or [125, 500, 1000]
            self.rates = [r for r in POLL_RATES if r in supported] or supported
            self.poll_row = Adw.ComboRow(title="Polling rate", subtitle="Reports per second",
                                         model=Gtk.StringList.new([f"{r} Hz" for r in self.rates]))
            self.poll_row.connect("notify::selected", self._on_poll)
            group.add(self.poll_row)
            self.add(group)
        else:
            self.poll_row = None

        if has_power(device):
            group = Adw.PreferencesGroup(title="Power")
            self.battery_row = Adw.ActionRow(title="Battery")
            self.battery_bar = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER, width_request=140)
            self.battery_row.add_suffix(self.battery_bar)
            group.add(self.battery_row)
            self.battery_row.set_visible(device.has("battery"))

            self.idle_row = Adw.SpinRow.new_with_range(1, 15, 1)
            self.idle_row.set_title("Sleep after")
            self.idle_row.set_subtitle("Minutes without use")
            self.idle_row.connect("notify::value", self._on_idle)
            group.add(self.idle_row)
            self.idle_row.set_visible(device.has("set_idle_time"))

            self.low_row = Adw.SpinRow.new_with_range(5, 100, 5)
            self.low_row.set_title("Low battery warning")
            self.low_row.set_subtitle("Percent")
            self.low_row.connect("notify::value", self._on_low)
            group.add(self.low_row)
            self.low_row.set_visible(device.has("set_low_battery_threshold"))
            self.add(group)
        else:
            self.battery_row = None

        self._load()
        # Battery and wake state change on their own.
        GLib.timeout_add_seconds(30, self._tick)

    def _tick(self):
        if self.get_root() is None:
            return False
        if not self._dpi_timer:
            self._load()
        return True

    def _load(self):
        self._loading = True
        r = self.razer
        dpi = _try(lambda: r.dpi, (0, 0)) if self.dpi_row else None
        asleep = bool(self.dpi_row) and (not dpi or dpi[0] == 0)
        self.banner_group.set_visible(asleep)

        if self.dpi_row:
            self.dpi_row.set_sensitive(not asleep)
            if not asleep:
                self.dpi_row.set_value(dpi[0])
            active, stages = _try(lambda: r.dpi_stages, (0, [])) or (0, [])
            if stages:
                text = "  ".join((f"[{x}]" if i + 1 == active else str(x)) for i, (x, _y) in enumerate(stages))
                self.stages_row.set_subtitle(text)
            self.stages_row.set_visible(bool(stages))

        if self.poll_row:
            rate = _try(lambda: r.poll_rate)
            self.poll_row.set_sensitive(not asleep)
            if rate in self.rates:
                self.poll_row.set_selected(self.rates.index(rate))

        if self.battery_row:
            level = _try(lambda: r.battery_level, 0)
            charging = _try(lambda: r.is_charging, False)
            if asleep or not level:
                self.battery_row.set_subtitle("Unavailable while the mouse sleeps")
                self.battery_bar.set_value(0)
            else:
                self.battery_row.set_subtitle(f"{level:.0f}%" + (" · charging" if charging else ""))
                self.battery_bar.set_value(level)
            self.idle_row.set_sensitive(not asleep)
            self.low_row.set_sensitive(not asleep)
            idle = _try(lambda: r.get_idle_time())
            if idle:
                self.idle_row.set_value(max(1, round(idle / 60)))
            low = _try(lambda: r.get_low_battery_threshold())
            if low:
                self.low_row.set_value(low)
        self._loading = False

    def _on_dpi(self, row, *_):
        if self._loading:
            return
        if self._dpi_timer:
            GLib.source_remove(self._dpi_timer)

        def write():
            self._dpi_timer = 0
            value = int(row.get_value())
            try:
                self.razer.dpi = (value, value)
            except Exception as ex:
                self.window.toast(f"Couldn't set DPI: {ex}")
            return False

        self._dpi_timer = GLib.timeout_add(300, write)

    def _on_poll(self, row, *_):
        if self._loading:
            return
        try:
            self.razer.poll_rate = self.rates[row.get_selected()]
        except Exception as ex:
            self.window.toast(f"Couldn't set polling rate: {ex}")

    def _on_idle(self, row, *_):
        if not self._loading:
            try:
                self.razer.set_idle_time(int(row.get_value()) * 60)
            except Exception as ex:
                self.window.toast(f"Couldn't set sleep timer: {ex}")

    def _on_low(self, row, *_):
        if not self._loading:
            try:
                self.razer.set_low_battery_threshold(int(row.get_value()))
            except Exception as ex:
                self.window.toast(f"Couldn't set warning level: {ex}")
