"""Audio settings for the BlackShark V3: EQ, THX Spatial, mic, sidetone, game/chat balance and more."""
from __future__ import annotations

import json

from gi.repository import Adw, GLib, Gtk

from ..engine import client
from ..hardware import blackshark as bs
from ..hardware.devices import STATE_FILE
from ..util import run_bg

STATE_KEY = "1532:057a"


def has_audio(device) -> bool:
    return device.id in ("1532-057a", "1532-0579") and bs.find_hidraw() is not None


def _hz(f: int) -> str:
    return f"{f // 1000}k" if f >= 1000 else str(f)


def _load_eq() -> tuple[int, dict]:
    """The EQ preset last chosen here (the headset can't report it) and any edited bands."""
    try:
        s = json.loads(STATE_FILE.read_text()).get(STATE_KEY, {})
    except (OSError, ValueError):
        s = {}
    return s.get("eq_preset", 0), {int(k): v for k, v in s.get("eq_bands", {}).items()}


def _save_eq(preset: int, bands: dict):
    try:
        data = json.loads(STATE_FILE.read_text())
    except (OSError, ValueError):
        data = {}
    data.setdefault(STATE_KEY, {}).update(eq_preset=preset, eq_bands={str(k): v for k, v in bands.items()})
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(data, indent=1))


class AudioPage(Adw.PreferencesPage):
    def __init__(self, window, device):
        super().__init__()
        self.window = window
        self._loading = True
        self._timers: dict[str, int] = {}
        self.eq_preset, self.eq_custom = _load_eq()

        # ── shown while the headset doesn't answer ──
        self.banner_group = Adw.PreferencesGroup()
        banner = Adw.ActionRow(title="The headset isn't answering",
                               subtitle="Turn it on and make sure it's connected to its dongle, then refresh.")
        banner.add_prefix(Gtk.Image(icon_name="dialog-warning-symbolic"))
        refresh = Gtk.Button(label="Refresh", valign=Gtk.Align.CENTER)
        refresh.connect("clicked", lambda *_: self._reload())
        banner.add_suffix(refresh)
        self.banner_group.add(banner)
        self.banner_group.set_visible(False)
        self.add(self.banner_group)

        # ── Headphones ──
        hp = Adw.PreferencesGroup(title="Headphones")
        self.battery_row = Adw.ActionRow(title="Battery")
        self.battery_bar = Gtk.LevelBar(min_value=0, max_value=100, valign=Gtk.Align.CENTER, width_request=140)
        self.battery_row.add_suffix(self.battery_bar)
        hp.add(self.battery_row)

        self.eq_row = Adw.ComboRow(title="Equalizer", subtitle="Preset", model=Gtk.StringList.new(bs.EQ_PRESETS))
        self.eq_row.connect("notify::selected", self._on_eq_preset)
        hp.add(self.eq_row)

        self.bands_row = Adw.ExpanderRow(title="Adjust bands", subtitle="Changes the selected preset, ±6 dB")
        box = Gtk.Box(spacing=6, margin_top=10, margin_bottom=10, margin_start=10, margin_end=10,
                      homogeneous=True)
        self.band_scales = []
        for f in bs.EQ_BANDS_HZ:
            col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            scale = Gtk.Scale.new_with_range(Gtk.Orientation.VERTICAL, -6, 6, 1)
            scale.set_inverted(True)
            scale.set_draw_value(True)
            scale.set_value_pos(Gtk.PositionType.TOP)
            scale.set_size_request(-1, 150)
            scale.add_mark(0, Gtk.PositionType.LEFT, None)
            scale.connect("value-changed", self._on_band)
            col.append(scale)
            col.append(Gtk.Label(label=_hz(f), css_classes=["caption", "dim-label"]))
            box.append(col)
            self.band_scales.append(scale)
        self.bands_row.add_row(box)
        reset = Gtk.Button(label="Reset to factory", halign=Gtk.Align.END, margin_end=10, margin_bottom=8,
                           css_classes=["flat"])
        reset.connect("clicked", self._on_reset_bands)
        self.bands_row.add_row(reset)
        hp.add(self.bands_row)

        self.thx_row = Adw.SwitchRow(title="THX Spatial Audio", subtitle="Virtual surround sound")
        self.thx_row.connect("notify::active", lambda r, *_: self._write("thx", lambda h: h.set_thx(r.get_active())))
        hp.add(self.thx_row)
        self.ull_row = Adw.SwitchRow(title="Ultra-low latency", subtitle="Less audio delay on 2.4 GHz; the link reconnects briefly")
        self.ull_row.connect("notify::active",
                             lambda r, *_: self._write("ull", lambda h: h.set_low_latency(r.get_active())))
        hp.add(self.ull_row)
        self.add(hp)

        # ── Microphone ──
        mic = Adw.PreferencesGroup(title="Microphone")
        self.mic_row = Adw.ComboRow(title="Mic EQ", model=Gtk.StringList.new(bs.MIC_PRESETS))
        self.mic_row.connect("notify::selected",
                             lambda r, *_: self._write("mic", lambda h: h.set_mic_preset(r.get_selected())))
        mic.add(self.mic_row)
        self.sidetone_row, self.sidetone = self._scale_row("Sidetone", "Hear your own voice (0 = off)", 0, 15,
                                                          lambda h, v: h.set_sidetone(v))
        mic.add(self.sidetone_row)
        self.add(mic)

        # ── Mixing ──
        mix = Adw.PreferencesGroup(title="Mixing")
        self.balance_row, self.balance = self._scale_row("Game / chat balance", "Left: game · right: chat", 0, 20,
                                                        lambda h, v: h.set_balance(v), mark=10)
        mix.add(self.balance_row)
        self.incall_row = Adw.ComboRow(title="During Bluetooth calls", model=Gtk.StringList.new(bs.IN_CALL))
        self.incall_row.connect("notify::selected",
                                lambda r, *_: self._write("incall", lambda h: h.set_in_call(r.get_selected())))
        mix.add(self.incall_row)
        self.add(mix)

        # ── Headset ──
        hs = Adw.PreferencesGroup(title="Headset")
        self.fn_row = Adw.ComboRow(title="Function button", subtitle="What the button on the headset changes",
                                   model=Gtk.StringList.new(bs.FN_BUTTON))
        self.fn_row.connect("notify::selected",
                            lambda r, *_: self._write("fn", lambda h: h.set_fn_button(r.get_selected())))
        hs.add(self.fn_row)
        self.power_row = Adw.ComboRow(title="Turn off when idle", model=Gtk.StringList.new(
            ["Never" if m == 0 else f"After {m} minutes" for m in bs.POWER_OFF]))
        self.power_row.connect("notify::selected", lambda r, *_: self._write(
            "power", lambda h: h.set_power_off(bs.POWER_OFF[r.get_selected()])))
        hs.add(self.power_row)
        self.prompts_row = Adw.SwitchRow(title="Voice prompts", subtitle="Spoken mic mute/unmute announcements")
        self.prompts_row.connect("notify::active",
                                 lambda r, *_: self._write("prompts", lambda h: h.set_prompts(r.get_active())))
        hs.add(self.prompts_row)
        self.led_row = Adw.ComboRow(title="Dongle light", model=Gtk.StringList.new(bs.DONGLE_LED))
        self.led_row.connect("notify::selected",
                             lambda r, *_: self._write("led", lambda h: h.set_led(r.get_selected())))
        hs.add(self.led_row)
        self.add(hs)

        self.groups = [hp, mic, mix, hs]
        self._reload()

    # ── helpers ──

    def _scale_row(self, title, subtitle, lo, hi, apply, mark=None):
        row = Adw.ActionRow(title=title, subtitle=subtitle)
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, lo, hi, 1)
        scale.set_hexpand(True)
        scale.set_size_request(220, -1)
        scale.set_draw_value(True)
        scale.set_value_pos(Gtk.PositionType.RIGHT)
        if mark is not None:
            scale.add_mark(mark, Gtk.PositionType.BOTTOM, None)
        scale.connect("value-changed", lambda s: self._write(title, lambda h: apply(h, int(s.get_value())), delay=250))
        row.add_suffix(scale)
        return row, scale

    def _write(self, key, fn, delay=0):
        """Sends one change to the headset, off the GTK thread (debounced for sliders)."""
        if self._loading:
            return
        if key in self._timers:
            GLib.source_remove(self._timers.pop(key))

        def go():
            self._timers.pop(key, None)

            def work():
                with bs.BlackSharkControl() as h:
                    fn(h)

            run_bg(work, lambda r: isinstance(r, Exception) and self.window.toast(f"Couldn't change the headset: {r}"))
            return False

        if delay:
            self._timers[key] = GLib.timeout_add(delay, go)
        else:
            go()

    def _bands(self, preset: int) -> list[int]:
        return list(self.eq_custom.get(preset, bs.EQ_FACTORY[preset]))

    def _show_bands(self):
        was, self._loading = self._loading, True
        for scale, v in zip(self.band_scales, self._bands(self.eq_preset)):
            scale.set_value(v)
        self._loading = was

    def _send_eq(self, delay=0):
        preset, bands = self.eq_preset, self._bands(self.eq_preset)
        _save_eq(self.eq_preset, self.eq_custom)
        self._write("eq", lambda h: h.set_eq(preset, bands), delay=delay)

    # ── events ──

    def _on_eq_preset(self, row, *_):
        if self._loading:
            return
        self.eq_preset = row.get_selected()
        self._show_bands()
        self._send_eq()

    def _on_band(self, _scale):
        if self._loading:
            return
        self.eq_custom[self.eq_preset] = [int(s.get_value()) for s in self.band_scales]
        self._send_eq(delay=400)

    def _on_reset_bands(self, _button):
        self.eq_custom.pop(self.eq_preset, None)
        self._show_bands()
        self._send_eq()

    # ── loading ──

    def _reload(self):
        def read():
            with bs.BlackSharkControl() as h:
                values = h.read_all()
            try:
                values["battery"] = client.call("status", timeout=1.0).get("batteries", {}).get("1532-057a")
            except Exception:
                values["battery"] = None
            return values

        run_bg(read, self._show)

    def _show(self, v):
        answering = isinstance(v, dict) and v.get("balance") is not None
        self.banner_group.set_visible(not answering)
        for g in self.groups:
            g.set_sensitive(answering)
        if not answering:
            return
        self._loading = True
        try:
            b = v.get("battery")
            if b and b.get("percent") is not None:
                self.battery_row.set_subtitle(f"{round(b['percent'])}%" + (" · charging" if b.get("charging") else ""))
                self.battery_bar.set_value(b["percent"])
            else:
                self.battery_row.set_subtitle("Not reported yet")
            self.eq_row.set_selected(self.eq_preset)
            self._show_bands()
            self.thx_row.set_active(bool(v["thx"]))
            self.ull_row.set_active(bool(v["low_latency"]))
            if v["mic_preset"] is not None and 0 <= v["mic_preset"] < len(bs.MIC_PRESETS):
                self.mic_row.set_selected(v["mic_preset"])
            self.sidetone.set_value(v["sidetone"] or 0)
            self.balance.set_value(v["balance"])
            if v["in_call"] is not None:
                self.incall_row.set_selected(min(v["in_call"], len(bs.IN_CALL) - 1))
            if v["fn_button"] is not None:
                self.fn_row.set_selected(min(v["fn_button"], len(bs.FN_BUTTON) - 1))
            if v["power_off"] is not None:
                self.power_row.set_selected(min(range(len(bs.POWER_OFF)), key=lambda i: abs(bs.POWER_OFF[i] - v["power_off"])))
            self.prompts_row.set_active(bool(v["prompts"]))
            if v["led"] is not None:
                self.led_row.set_selected(min(v["led"], len(bs.DONGLE_LED) - 1))
        finally:
            self._loading = False
