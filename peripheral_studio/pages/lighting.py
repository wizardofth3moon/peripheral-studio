"""Lighting: effect, colours, speed, brightness, profile LEDs (native driver or OpenRazer)."""
from __future__ import annotations

from gi.repository import Adw, Gdk, GLib, Gtk

from ..engine import client, model
from ..util import run_bg

# (capability, label, colours used, extra option)
EFFECTS = [
    ("lighting_static", "Static", 1, None),
    ("lighting_spectrum", "Spectrum cycling", 0, None),
    ("lighting_wave", "Wave", 0, "direction"),
    ("lighting_breath_single", "Breathing", 1, None),
    ("lighting_breath_dual", "Breathing, two colours", 2, None),
    ("lighting_breath_random", "Breathing, random", 0, None),
    ("lighting_reactive", "Reactive", 1, "reactive"),
    ("lighting_ripple", "Ripple", 1, None),
    ("lighting_ripple_random", "Ripple, random", 0, None),
    ("lighting_starlight_single", "Starlight", 1, "starlight"),
    ("lighting_starlight_dual", "Starlight, two colours", 2, "starlight"),
    ("lighting_starlight_random", "Starlight, random", 0, "starlight"),
    ("lighting_custom", "Per key", 1, None),
    ("lighting_none", "Off", 0, None),
]
REACTIVE = [("Short (0.5 s)", 1), ("Medium (1 s)", 2), ("Long (1.5 s)", 3), ("Very long (2 s)", 4)]
STARLIGHT = [("Fast", 1), ("Normal", 2), ("Slow", 3)]
DIRECTIONS = [("Right", 1), ("Left", 2)]


def has_lighting(device) -> bool:
    return any(device.has(cap) for cap, *_ in EFFECTS) or device.has("brightness")


def _rgba(r, g, b) -> Gdk.RGBA:
    c = Gdk.RGBA()
    c.red, c.green, c.blue, c.alpha = r / 255, g / 255, b / 255, 1
    return c


def _rgb(c: Gdk.RGBA) -> tuple[int, int, int]:
    return round(c.red * 255), round(c.green * 255), round(c.blue * 255)


class LightingPage(Adw.PreferencesPage):
    def __init__(self, window, device):
        super().__init__()
        self.window = window
        self.razer = device.razer
        self.effects = [e for e in EFFECTS if device.has(e[0])]
        self._loading = True

        if self.effects:
            group = Adw.PreferencesGroup(title="Effect")
            self.effect_row = Adw.ComboRow(title="Effect", model=Gtk.StringList.new([e[1] for e in self.effects]))
            group.add(self.effect_row)

            self.color_rows, self.color_btns = [], []
            for i in range(2):
                btn = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
                row = Adw.ActionRow(title=f"Colour {i + 1}" if i else "Colour")
                row.add_suffix(btn)
                row.set_activatable_widget(btn)
                group.add(row)
                btn.connect("notify::rgba", lambda *_: self._apply_effect())
                self.color_rows.append(row)
                self.color_btns.append(btn)

            self.option_row = Adw.ComboRow(title="Speed")
            self.option_row.connect("notify::selected", lambda *_: self._apply_effect())
            group.add(self.option_row)
            self.effect_row.connect("notify::selected", lambda *_: (self._update_rows(), self._apply_effect()))
            self.add(group)

        if device.has("brightness"):
            group = Adw.PreferencesGroup(title="Brightness")
            self.brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
            self.brightness.set_hexpand(True)
            self.brightness.set_draw_value(True)
            self.brightness.set_value_pos(Gtk.PositionType.RIGHT)
            row = Adw.ActionRow(title="Brightness")
            row.add_suffix(self.brightness)
            group.add(row)
            self._brightness_timer = 0
            self.brightness.connect("value-changed", self._on_brightness)
            self.add(group)
        else:
            self.brightness = None

        leds = [c for c in ("red", "green", "blue") if device.has(f"lighting_profile_led_{c}")]
        # Per-key colours (devices with a lighting matrix, e.g. the Tartarus V2)
        self.key_colours: dict[str, list[int]] = dict(getattr(getattr(self.razer, "fx", None), "keys", {}) or {})
        self.key_buttons: dict[str, Gtk.Button] = {}
        self._key_timer = 0
        self.keys_group = None
        matrix = getattr(self.razer, "MATRIX", None)
        if matrix and device.has("lighting_custom"):
            self._build_key_painter(matrix)

        self.led_rows = {}
        if leds:
            group = Adw.PreferencesGroup(title="Profile indicator",
                                         description="The small LEDs that show which profile is active")
            for c in leds:
                row = Adw.SwitchRow(title=f"{c.title()} LED")
                row.connect("notify::active", lambda r, c=c: self._loading or (
                    setattr(self.razer, f"profile_led_{c}", r.get_active()), self._changed()))
                group.add(row)
                self.led_rows[c] = row
            self.add(group)

        # Lighting saved with the active profile (native driver only: it can snapshot the effect)
        self.profile_name = None
        self._save_timer = 0
        if getattr(self.razer, "backend", None) == "native":
            group = Adw.PreferencesGroup(title="Profile")
            self.profile_row = Adw.SwitchRow(title="Save with the active profile",
                                             subtitle="Switching to that profile brings this lighting back")
            self.profile_row.connect("notify::active", self._on_profile_toggle)
            group.add(self.profile_row)
            self.add(group)
            self.device = device
            run_bg(lambda: client.call("status", timeout=1.0), self._show_profile)
        else:
            self.profile_row = None

        self._load()

    # ── lighting saved per profile ──

    def _show_profile(self, status):
        if isinstance(status, Exception):
            self.profile_row.set_subtitle("Start remapping (Customize tab) to save lighting with a profile")
            self.profile_row.set_sensitive(False)
            return
        entry = next((d for d in status.get("devices", []) if d["id"] == self.device.id), None)
        self.profile_name = entry and entry.get("profile")
        if not self.profile_name:
            self.profile_row.set_sensitive(False)
            return
        self.profile_row.set_title(f"Save with profile “{self.profile_name}”")
        profile = model.Profile.load(self.device.id, self.profile_name)
        was, self._loading = self._loading, True
        self.profile_row.set_active("lighting" in profile.extra)
        self._loading = was

    def _store(self, include: bool):
        """Writes (or removes) the current lighting in the active profile's file."""
        from ..hardware import lighting

        def work():
            profile = model.Profile.load(self.device.id, self.profile_name)
            if include:
                profile.extra["lighting"] = lighting.capture(self.razer)
            else:
                profile.extra.pop("lighting", None)
            profile.save()
            client.call("reload", device=self.device.id)

        run_bg(work, lambda r: isinstance(r, Exception) and self.window.toast(f"Couldn't save lighting: {r}"))

    def _on_profile_toggle(self, row, *_):
        if not self._loading and self.profile_name:
            self._store(row.get_active())

    def _changed(self):
        """After any lighting change: keep the profile's copy current (debounced)."""
        if self._loading or not self.profile_row or not self.profile_row.get_active():
            return
        if self._save_timer:
            GLib.source_remove(self._save_timer)

        def save():
            self._save_timer = 0
            self._store(True)
            return False

        self._save_timer = GLib.timeout_add(600, save)

    def _load(self):
        self._loading = True
        try:
            fx = self.razer.fx
            current = f"lighting_{fx.effect}"
            idx = next((i for i, e in enumerate(self.effects) if e[0] == current), 0)
            if self.effects:
                self.effect_row.set_selected(idx)
                colors = list(fx.colors) + [0] * 6
                for i, btn in enumerate(self.color_btns):
                    btn.set_rgba(_rgba(*colors[i * 3:i * 3 + 3]))
                self._show_key_colours()  # unpainted keys take the (now loaded) "other keys" colour
                self._update_rows()
                self._speed_hint = fx.speed
                self._dir_hint = fx.wave_dir
                self._select_option()
            if self.brightness:
                self.brightness.set_value(self.razer.brightness)
            for c, row in self.led_rows.items():
                row.set_active(bool(getattr(self.razer, f"profile_led_{c}")))
        except Exception as ex:
            self.window.toast(f"Couldn't read lighting: {ex}")
        finally:
            self._loading = False

    def _options(self):
        extra = self.effects[self.effect_row.get_selected()][3]
        return {"reactive": REACTIVE, "starlight": STARLIGHT, "direction": DIRECTIONS}.get(extra, [])

    # ── per-key painter ──

    def _build_key_painter(self, matrix):
        group = Adw.PreferencesGroup(title="Per-key colours",
                                     description="Pick a paint colour, then click keys. Click a painted key again "
                                                 "to give it the “other keys” colour.")
        brush_row = Adw.ActionRow(title="Paint colour")
        self.brush = Gtk.ColorDialogButton(dialog=Gtk.ColorDialog(with_alpha=False), valign=Gtk.Align.CENTER)
        self.brush.set_rgba(_rgba(255, 140, 0))
        brush_row.add_suffix(self.brush)
        clear = Gtk.Button(label="Clear all", valign=Gtk.Align.CENTER, css_classes=["flat"])
        clear.connect("clicked", lambda *_: self._paint_all_clear())
        brush_row.add_suffix(clear)
        group.add(brush_row)

        grid = Gtk.Grid(row_spacing=6, column_spacing=6, halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=10)
        labels = {"wheel_click": "Wheel", "space": "20"}
        for key, (r, c) in sorted(matrix.items(), key=lambda kv: kv[1]):
            b = Gtk.Button(label=labels.get(key, key.removeprefix("key")), css_classes=["pk-key", f"pk-{key}"])
            b.set_size_request(58, 44)
            b.connect("clicked", lambda _b, k=key: self._paint(k))
            grid.attach(b, c, r, 1, 1)
            self.key_buttons[key] = b
        frame = Gtk.Frame(child=grid, css_classes=["view"])
        group.add(frame)
        self.add(group)
        self.keys_group = group
        self._key_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), self._key_css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1)
        self._show_key_colours()

    def _base(self) -> list[int]:
        return list(_rgb(self.color_btns[0].get_rgba())) if self.color_btns else [0, 0, 0]

    def _show_key_colours(self):
        if not self.key_buttons:
            return
        css = []
        base = self._base()
        for key in self.key_buttons:
            r, g, b = self.key_colours.get(key, base)
            fg = "#000" if (r * 299 + g * 587 + b * 114) / 1000 > 140 else "#fff"
            css.append(f"button.pk-{key} {{ background: rgb({r},{g},{b}); color: {fg}; "
                       f"border: 1px solid rgba(255,255,255,0.18); font-weight: 600; }}")
        self._key_css.load_from_string("\n".join(css))

    def _paint(self, key):
        colour = list(_rgb(self.brush.get_rgba()))
        if self.key_colours.get(key) == colour:
            self.key_colours.pop(key)  # second click: back to the "other keys" colour
        else:
            self.key_colours[key] = colour
        self._key_colours_changed()

    def _paint_all_clear(self):
        self.key_colours.clear()
        self._key_colours_changed()

    def _key_colours_changed(self):
        self._show_key_colours()
        if self.effects[self.effect_row.get_selected()][0] != "lighting_custom":
            idx = next(i for i, e in enumerate(self.effects) if e[0] == "lighting_custom")
            self.effect_row.set_selected(idx)  # painting switches to per-key lighting (applies it too)
            return
        if self._key_timer:
            GLib.source_remove(self._key_timer)

        def send():
            self._key_timer = 0
            self._apply_effect()
            return False

        self._key_timer = GLib.timeout_add(120, send)

    def _update_rows(self):
        cap, _label, ncolors, extra = self.effects[self.effect_row.get_selected()]
        if self.keys_group is not None:
            self.keys_group.set_visible(cap == "lighting_custom")
        if self.color_rows:
            self.color_rows[0].set_title("Other keys" if cap == "lighting_custom" else "Colour")
        for i, row in enumerate(self.color_rows):
            row.set_visible(i < ncolors)
        opts = self._options()
        was = self._loading
        self._loading = True
        self.option_row.set_title("Direction" if extra == "direction" else "Duration" if extra == "reactive" else "Speed")
        self.option_row.set_model(Gtk.StringList.new([o[0] for o in opts]))
        self.option_row.set_visible(bool(opts))
        self._select_option()
        self._loading = was

    def _select_option(self):
        opts = self._options()
        if not opts:
            return
        extra = self.effects[self.effect_row.get_selected()][3]
        hint = getattr(self, "_dir_hint" if extra == "direction" else "_speed_hint", None)
        idx = next((i for i, o in enumerate(opts) if o[1] == hint), min(1, len(opts) - 1))
        self.option_row.set_selected(idx)

    def _apply_effect(self):
        if self._loading or not self.effects:
            return
        cap, _label, _n, extra = self.effects[self.effect_row.get_selected()]
        c1 = _rgb(self.color_btns[0].get_rgba())
        c2 = _rgb(self.color_btns[1].get_rgba())
        opts = self._options()
        opt = opts[self.option_row.get_selected()][1] if opts else None
        fx = self.razer.fx
        name = cap.removeprefix("lighting_")
        try:
            if name == "custom":
                keys = dict(self.key_colours)
                self.razer.set_key_colours(keys, c1)
                self._show_key_colours()
                self._changed()
                return
            if name in ("static", "breath_single", "ripple"):
                getattr(fx, name)(*c1)
            elif name == "breath_dual":
                fx.breath_dual(*c1, *c2)
            elif name == "reactive":
                fx.reactive(*c1, opt or 2)
            elif name == "starlight_single":
                fx.starlight_single(*c1, opt or 2)
            elif name == "starlight_dual":
                fx.starlight_dual(*c1, *c2, opt or 2)
            elif name == "starlight_random":
                fx.starlight_random(opt or 2)
            elif name == "wave":
                fx.wave(opt or 1)
            else:  # spectrum, breath_random, ripple_random, none
                getattr(fx, name)()
            self._changed()
        except Exception as ex:
            self.window.toast(f"Couldn't set lighting: {ex}")

    def _on_brightness(self, scale):
        if self._loading:
            return
        # Coalesce drags: one write after the slider settles.
        if self._brightness_timer:
            GLib.source_remove(self._brightness_timer)

        def write():
            self._brightness_timer = 0
            try:
                self.razer.brightness = scale.get_value()
                self._changed()
            except Exception as ex:
                self.window.toast(f"Couldn't set brightness: {ex}")
            return False

        self._brightness_timer = GLib.timeout_add(120, write)
