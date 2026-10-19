"""Macro editor: a list of steps, with real-time recording from the keyboard."""
from __future__ import annotations

import time

from gi.repository import Gdk, GLib, Gtk, Pango

from ..engine import model


def evdev_name(keycode: int) -> str | None:
    """GDK hardware keycodes are evdev codes + 8 on Linux, whatever the keyboard layout."""
    from evdev import ecodes
    code = keycode - 8
    return model.name_of(code) if code in ecodes.KEY else None


class MacroEditor(Gtk.Box):
    MODES = [("once", "Play once"), ("while_held", "Repeat while held"), ("toggle", "Toggle on/off"),
             ("repeat", "Repeat a number of times")]

    def __init__(self, window):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.window = window
        self.steps: list[model.MacroStep] = []
        self.recording = False
        self._last = 0.0

        self.name_entry = Gtk.Entry(placeholder_text="Macro name (optional)")
        self.append(self.name_entry)

        mode_box = Gtk.Box(spacing=8)
        self.mode_dd = Gtk.DropDown(model=Gtk.StringList.new([m[1] for m in self.MODES]), hexpand=True)
        self.mode_dd.connect("notify::selected", lambda *_: self.count.set_visible(self._mode() == "repeat"))
        mode_box.append(self.mode_dd)
        self.count = Gtk.SpinButton.new_with_range(2, 999, 1)
        self.count.set_visible(False)
        mode_box.append(self.count)
        self.append(mode_box)

        frame = Gtk.ScrolledWindow(min_content_height=150, max_content_height=260, propagate_natural_height=True)
        frame.add_css_class("macro-steps")
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["boxed-list"])
        self.list.set_placeholder(Gtk.Label(label="No steps yet: record or add some", margin_top=18,
                                            margin_bottom=18, css_classes=["dim-label"]))
        frame.set_child(self.list)
        self.append(frame)

        tools = Gtk.Box(spacing=6)
        self.record_btn = Gtk.ToggleButton(child=Gtk.Label(label="● Record"), css_classes=["record-macro"])
        self.record_btn.connect("toggled", self._on_record_toggled)
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key, 1)
        keys.connect("key-released", self._on_key_up)
        self.record_btn.add_controller(keys)
        tools.append(self.record_btn)

        add = Gtk.MenuButton(label="Add step", hexpand=True)
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=8, margin_bottom=8,
                      margin_start=8, margin_end=8)
        self.key_entry = Gtk.Entry(placeholder_text="Key or shortcut, e.g. Ctrl+C")
        self.key_entry.connect("activate", lambda *_: (self._add_keys(), pop.popdown()))
        box.append(self.key_entry)
        add_key = Gtk.Button(label="Add key press")
        add_key.connect("clicked", lambda *_: (self._add_keys(), pop.popdown()))
        box.append(add_key)
        box.append(Gtk.Separator())
        delay_row = Gtk.Box(spacing=6)
        self.delay_spin = Gtk.SpinButton.new_with_range(1, 60000, 10)
        self.delay_spin.set_value(50)
        delay_row.append(self.delay_spin)
        add_delay = Gtk.Button(label="Add delay (ms)", hexpand=True)
        add_delay.connect("clicked", lambda *_: (self._append(model.MacroStep("wait", ms=int(self.delay_spin.get_value()))), pop.popdown()))
        delay_row.append(add_delay)
        box.append(delay_row)
        box.append(Gtk.Separator())
        self.text_entry = Gtk.Entry(placeholder_text="Text to type")
        box.append(self.text_entry)
        add_text = Gtk.Button(label="Add text")
        add_text.connect("clicked", lambda *_: (self.text_entry.get_text() and self._append(
            model.MacroStep("text", text=self.text_entry.get_text())), pop.popdown()))
        box.append(add_text)
        box.append(Gtk.Separator())
        for label, amount in (("Scroll up", 1), ("Scroll down", -1)):
            b = Gtk.Button(label=f"Add {label.lower()}")
            b.connect("clicked", lambda _b, a=amount: (self._append(model.MacroStep("scroll", amount=a)), pop.popdown()))
            box.append(b)
        pop.set_child(box)
        add.set_popover(pop)
        tools.append(add)

        clear = Gtk.Button(icon_name="edit-clear-all-symbolic", tooltip_text="Remove all steps")
        clear.connect("clicked", lambda *_: self.set_steps([]))
        tools.append(clear)
        self.append(tools)

        self.hint = Gtk.Label(xalign=0, wrap=True, css_classes=["dim-label", "caption"],
                              label="Record captures key presses with their real timing.")
        self.append(self.hint)

    # ── Model ────────────────────────────────────────────────────────────────

    def _mode(self) -> str:
        return self.MODES[self.mode_dd.get_selected()][0]

    def load(self, macro: model.MacroAction | None):
        macro = macro or model.MacroAction()
        self.name_entry.set_text(macro.name)
        self.mode_dd.set_selected(next((i for i, m in enumerate(self.MODES) if m[0] == macro.mode), 0))
        self.count.set_value(max(2, macro.count))
        self.set_steps([model.MacroStep.load(s.to_json()) for s in macro.steps])

    def action(self) -> model.MacroAction | None:
        if not self.steps:
            return None
        return model.MacroAction(name=self.name_entry.get_text().strip(), mode=self._mode(),
                                 count=int(self.count.get_value()) if self._mode() == "repeat" else 1,
                                 steps=list(self.steps))

    def set_steps(self, steps):
        self.steps = steps
        self._render()

    def _append(self, step):
        self.steps.append(step)
        self._render()

    def _render(self):
        while row := self.list.get_row_at_index(0):
            self.list.remove(row)
        for i, step in enumerate(self.steps):
            row = Gtk.Box(spacing=4, margin_start=10, margin_end=4, margin_top=2, margin_bottom=2)
            icon = {"wait": "alarm-symbolic", "text": "insert-text-symbolic", "scroll": "input-mouse-symbolic"}.get(
                step.op, "input-keyboard-symbolic")
            row.append(Gtk.Image(icon_name=icon))
            row.append(Gtk.Label(label=step.describe(), xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            for icon, delta, tip in (("go-up-symbolic", -1, "Move up"), ("go-down-symbolic", 1, "Move down")):
                b = Gtk.Button(icon_name=icon, css_classes=["flat", "circular"], tooltip_text=tip,
                               sensitive=0 <= i + delta < len(self.steps))
                b.connect("clicked", lambda _b, i=i, d=delta: self._move(i, d))
                row.append(b)
            rm = Gtk.Button(icon_name="user-trash-symbolic", css_classes=["flat", "circular"], tooltip_text="Remove")
            rm.connect("clicked", lambda _b, i=i: (self.steps.pop(i), self._render()))
            row.append(rm)
            self.list.append(row)

    def _move(self, i, d):
        self.steps[i], self.steps[i + d] = self.steps[i + d], self.steps[i]
        self._render()

    def _add_keys(self):
        try:
            keys = model.parse_shortcut(self.key_entry.get_text())
        except ValueError as ex:
            self.window.toast(str(ex))
            return
        if len(keys) == 1:
            self._append(model.MacroStep("tap", key=keys[0]))
        else:
            for k in keys:
                self.steps.append(model.MacroStep("down", key=k))
            for k in reversed(keys):
                self.steps.append(model.MacroStep("up", key=k))
            self._render()
        self.key_entry.set_text("")

    # ── Recording ────────────────────────────────────────────────────────────

    def _on_record_toggled(self, btn):
        self.recording = btn.get_active()
        btn.get_child().set_label("■ Stop" if self.recording else "● Record")
        if self.recording:
            self._last = 0.0
            btn.add_css_class("recording")
            self.hint.set_label("Recording: press keys now. Click Stop when done.")
            GLib.idle_add(btn.grab_focus)
        else:
            btn.remove_css_class("recording")
            self._collapse_taps()
            self.hint.set_label("Record captures key presses with their real timing.")

    def _gap(self):
        now = time.monotonic()
        if self._last:
            ms = int((now - self._last) * 1000)
            if ms >= 10:
                self.steps.append(model.MacroStep("wait", ms=ms))
        self._last = now

    def _on_key(self, _ctl, keyval, keycode, state, value):
        if not self.recording:
            return False
        name = evdev_name(keycode)
        if not name:
            return True
        if self.steps and self.steps[-1].op == "down" and self.steps[-1].key == name:
            return True  # autorepeat
        self._gap()
        self.steps.append(model.MacroStep("down", key=name))
        self._render()
        return True

    def _on_key_up(self, _ctl, keyval, keycode, state):
        if not self.recording:
            return
        name = evdev_name(keycode)
        if name:
            self._gap()
            self.steps.append(model.MacroStep("up", key=name))
            self._render()

    def _collapse_taps(self):
        """down X, (short wait), up X  ->  tap X, keeping the list readable."""
        out = []
        for step in self.steps:
            if step.op == "up" and out and out[-1].op == "down" and out[-1].key == step.key:
                out[-1] = model.MacroStep("tap", key=step.key)
                continue
            if (step.op == "up" and len(out) >= 2 and out[-1].op == "wait" and out[-1].ms < 60
                    and out[-2].op == "down" and out[-2].key == step.key):
                out.pop()
                out[-1] = model.MacroStep("tap", key=step.key)
                continue
            out.append(step)
        self.steps = out
        self._render()
