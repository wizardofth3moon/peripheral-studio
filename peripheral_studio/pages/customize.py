"""Customize: the device diagram, profiles, layers and the binding editor."""
from __future__ import annotations

from evdev import ecodes
from gi.repository import Adw, GLib, Gtk, Pango

from ..engine import client, model
from ..layouts import Button
from ..util import run_bg
from .macro_editor import MacroEditor, evdev_name
from pathlib import Path

DEVICES_ART = Path(__file__).resolve().parent.parent.parent / "data" / "devices"

KINDS = [
    ("default", "Default"), ("keys", "Keyboard"), ("mouse", "Mouse"), ("media", "Media"), ("text", "Text"),
    ("macro", "Macro"), ("turbo", "Turbo"), ("hypershift", "Hypershift"), ("profile", "Profile"),
    ("dpi", "DPI"), ("disable", "Disable"),
]
DPI_STEPS = [("next", "Next DPI stage"), ("previous", "Previous DPI stage"), ("cycle", "Cycle DPI stages")]
MOUSE = [("BTN_LEFT", "Left click"), ("BTN_RIGHT", "Right click"), ("BTN_MIDDLE", "Middle click"),
         ("BTN_SIDE", "Back (Mouse 4)"), ("BTN_EXTRA", "Forward (Mouse 5)"),
         ("scroll:vertical:1", "Scroll up"), ("scroll:vertical:-1", "Scroll down"),
         ("scroll:horizontal:-1", "Scroll left"), ("scroll:horizontal:1", "Scroll right")]
MEDIA = [("KEY_PLAYPAUSE", "Play / pause"), ("KEY_NEXTSONG", "Next track"), ("KEY_PREVIOUSSONG", "Previous track"),
         ("KEY_STOPCD", "Stop"), ("KEY_VOLUMEUP", "Volume up"), ("KEY_VOLUMEDOWN", "Volume down"),
         ("KEY_MUTE", "Mute"), ("KEY_MICMUTE", "Mic mute"), ("KEY_SYSRQ", "Print screen"),
         ("KEY_CALC", "Calculator"), ("KEY_HOMEPAGE", "Browser"), ("KEY_MAIL", "Mail"), ("KEY_FILE", "Files"),
         ("KEY_BRIGHTNESSUP", "Brightness up"), ("KEY_BRIGHTNESSDOWN", "Brightness down")]
HARDWARE_NOTES = {
    "dpi": "Right now the mouse changes DPI with this button by itself. It reports the press over Razer's own "
           "USB channel rather than as a normal button, so binding it needs Peripheral Studio's native Razer "
           "support, which is next on the list. Until then, set the DPI steps it cycles through under Performance.",
    "scroll_mode": "Switches the wheel between notched and free-spinning. The mouse handles this itself and "
                   "doesn't report it, so it can't be rebound (Synapse can't either).",
}
MODIFIERS = {"KEY_LEFTCTRL", "KEY_RIGHTCTRL", "KEY_LEFTSHIFT", "KEY_RIGHTSHIFT", "KEY_LEFTALT", "KEY_RIGHTALT",
             "KEY_LEFTMETA", "KEY_RIGHTMETA"}


class Silhouette(Gtk.DrawingArea):
    """Draws device art at exactly the diagram's size, sharp at any display scale."""

    def __init__(self, path, width, height):
        super().__init__(content_width=width, content_height=height, halign=Gtk.Align.START,
                         valign=Gtk.Align.START, can_target=False)
        import gi
        gi.require_version("Rsvg", "2.0")
        from gi.repository import Rsvg
        self.handle = Rsvg.Handle.new_from_file(str(path))
        self.set_draw_func(self._draw)

    def _draw(self, _area, cr, width, height):
        from gi.repository import Rsvg
        rect = Rsvg.Rectangle()
        rect.x, rect.y, rect.width, rect.height = 0, 0, width, height
        self.handle.render_document(cr, rect)


class CustomizePage(Gtk.Box):
    def __init__(self, window, device):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL)
        self.window = window
        self.device = device
        self.profile = model.Profile.load(device.id, model.ensure_profile(device.id))
        self.learned: dict = model.DeviceState.load(device.id).buttons
        self.learning: Button | None = None  # button waiting for a press to learn what it sends
        self.layer = "base"
        self.selected: Button | None = None
        self.selected_input: model.Input | None = None
        self.key_widgets: dict[str, Gtk.Button] = {}
        self.finding = False
        self.recording = False
        self.recorded: list[str] = []

        main = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.banner = Adw.Banner(title="The remap engine isn't running, so binds aren't active",
                                 button_label="Start engine")
        self.banner.connect("button-clicked", lambda *_: self.window.start_engine())
        main.append(self.banner)
        main.append(self._build_profile_bar())
        main.append(self._build_layer_bar())
        scroller = Gtk.ScrolledWindow(vexpand=True, hexpand=True)
        self.diagram_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18,
                                   margin_top=12, margin_bottom=24, margin_start=24, margin_end=24)
        scroller.set_child(self.diagram_box)
        main.append(scroller)
        self.append(main)

        self.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        side = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, width_request=380)
        self.panel = self._build_panel()
        side.set_child(self.panel)
        self.append(side)

        self._build_diagram()
        self._refresh_panel()
        self.engine_changed(window.engine_up)

    # ── Button identity ──────────────────────────────────────────────────────

    def _input_of(self, b: Button) -> model.Input | None:
        """What the button sends: learned from a press, else its default (None for an unlearned
        hardware button)."""
        if b.id in self.learned:
            return model.Input.from_json(self.learned[b.id])
        return None if b.hardware else model.Input(b.ev_type, b.code, b.threshold)

    def _button_for(self, inp: model.Input) -> Button | None:
        layout = self.device.layout
        if not layout:
            return None
        for bid, j in self.learned.items():
            if model.Input.from_json(j).key == inp.key:
                return next((b for b in layout.all_buttons() if b.id == bid), None)
        b = layout.find(*inp.key)
        return b if b and b.id not in self.learned else None

    def _learn(self, b: Button, inp: model.Input):
        state = model.DeviceState.load(self.device.id)
        default = None if b.hardware else model.Input(b.ev_type, b.code, b.threshold)
        if default is not None and inp.key == default.key:
            state.buttons.pop(b.id, None)
        else:
            # One input can belong to one button only.
            for bid in [k for k, j in state.buttons.items() if model.Input.from_json(j).key == inp.key]:
                state.buttons.pop(bid)
            state.buttons[b.id] = inp.to_json()
        state.save()
        self.learned = state.buttons
        self.window.toast(f"{b.label} sends {inp.describe()}")
        self._rebuild()

    # ── Engine link ──────────────────────────────────────────────────────────

    def engine_changed(self, up: bool):
        self.banner.set_revealed(not up)

    def on_engine_event(self, msg):
        if msg.get("device") not in (None, self.device.id):
            return
        ev = msg.get("event")
        if ev == "input":
            inp = model.Input.from_json(msg["input"])
            if self.learning:
                b, self.learning = self.learning, None
                self.detect_status.set_visible(False)
                self._learn(b, inp)
                self._select(b)
                return
            self._flash(inp)
            if self.finding:
                self.finding = False
                self.detect_status.set_visible(False)
                self._select_input(inp)
        elif ev == "device" and msg.get("profile") and msg["profile"] != self.profile.name:
            self.profile = model.Profile.load(self.device.id, msg["profile"])
            self._fill_profiles()
            self._rebuild()
        elif ev == "hypershift":
            self.hs_indicator.set_visible(msg.get("active", False))

    def _notify_engine(self, message="Saved"):
        """Tells the engine to re-read this device's profile."""

        def done(result):
            if isinstance(result, client.EngineUnavailable):
                self.window.toast(f"{message}. Start the engine to use it")
            elif isinstance(result, Exception):
                self.window.toast(f"Engine: {result}")
            else:
                self.window.toast(message)

        run_bg(lambda: client.call("reload", device=self.device.id), done)

    # ── Profile bar ──────────────────────────────────────────────────────────

    def _build_profile_bar(self):
        bar = Gtk.Box(spacing=8, margin_top=10, margin_bottom=4, margin_start=24, margin_end=24)
        bar.append(Gtk.Label(label="Profile", css_classes=["dim-label"]))
        self.profile_model = Gtk.StringList()
        self.profile_dd = Gtk.DropDown(model=self.profile_model)
        self.profile_dd.set_size_request(220, -1)
        self._profile_handler = self.profile_dd.connect("notify::selected", self._on_profile_selected)
        bar.append(self.profile_dd)

        add = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New profile")
        add.connect("clicked", lambda *_: self._ask_name("New profile", "", self._create_profile))
        bar.append(add)

        menu = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="Profile options")
        pop = Gtk.Popover()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_top=4, margin_bottom=4)
        for label, cb in (("Linked apps…", self._edit_apps), ("Rename…", self._rename_profile),
                          ("Duplicate…", self._duplicate_profile), ("Delete", self._delete_profile)):
            b = Gtk.Button(label=label, css_classes=["flat"])
            b.get_child().set_xalign(0)
            b.connect("clicked", lambda _b, cb=cb: (pop.popdown(), cb()))
            box.append(b)
        pop.set_child(box)
        menu.set_popover(pop)
        bar.append(menu)

        self.apps_label = Gtk.Label(css_classes=["dim-label", "caption"], ellipsize=Pango.EllipsizeMode.END)
        bar.append(self.apps_label)
        bar.append(Gtk.Box(hexpand=True))

        find = Gtk.Button(tooltip_text="Press a button on the device to select it")
        find.set_child(Adw.ButtonContent(icon_name="find-location-symbolic", label="Find a button"))
        find.connect("clicked", lambda *_: self._find_button())
        bar.append(find)

        bar.append(Gtk.Label(label="Remapping", css_classes=["dim-label"], margin_start=12))
        self.enable_switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=model.DeviceState.load(self.device.id).enabled)
        self.enable_switch.connect("notify::active", self._on_enable_toggled)
        bar.append(self.enable_switch)
        self._fill_profiles()
        return bar

    def _fill_profiles(self):
        names = model.list_profiles(self.device.id) or [self.profile.name]
        self.profile_dd.handler_block(self._profile_handler)
        self.profile_model.splice(0, self.profile_model.get_n_items(), names)
        self.profile_dd.set_selected(names.index(self.profile.name) if self.profile.name in names else 0)
        self.profile_dd.handler_unblock(self._profile_handler)
        apps = ", ".join(self.profile.apps)
        self.apps_label.set_label(f"Auto: {apps}" if apps else "")

    def _on_profile_selected(self, *_):
        item = self.profile_dd.get_selected_item()
        if item and item.get_string() != self.profile.name:
            self._switch_to(item.get_string())

    def _switch_to(self, name):
        self.profile = model.Profile.load(self.device.id, name)
        state = model.DeviceState.load(self.device.id)
        state.active = name
        state.save()
        self._fill_profiles()
        self._rebuild()
        run_bg(lambda: client.call("activate", device=self.device.id, profile=name),
               lambda r: self.window.toast(f"Switched to “{name}”" if not isinstance(r, Exception) else
                                           f"Switched to “{name}” (engine not running)"))

    def _ask_name(self, heading, initial, done, body=None):
        dialog = Adw.AlertDialog(heading=heading, body=body or "")
        entry = Gtk.Entry(text=initial, activates_default=True)
        dialog.set_extra_child(entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("ok", "OK")
        dialog.set_response_appearance("ok", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("ok")

        def on_response(_d, response):
            if response == "ok":
                done(entry.get_text().strip())

        dialog.connect("response", on_response)
        dialog.present(self.window)

    def _valid_new_name(self, name, allow=None) -> bool:
        if not name:
            return False
        if name in model.list_profiles(self.device.id) and name != allow:
            self.window.toast(f"A profile named “{name}” already exists")
            return False
        return True

    def _create_profile(self, name):
        if self._valid_new_name(name):
            model.Profile(self.device.id, name).save()
            self._switch_to(name)

    def _duplicate_profile(self):
        def done(name):
            if not self._valid_new_name(name):
                return
            copy = model.Profile.load(self.device.id, self.profile.name)
            copy.name, copy.apps = name, []
            copy.save()
            self._switch_to(name)
        self._ask_name("Duplicate profile", f"{self.profile.name} copy", done)

    def _rename_profile(self):
        def done(name):
            if name != self.profile.name and self._valid_new_name(name):
                model.rename_profile(self.device.id, self.profile.name, name)
                self.profile = model.Profile.load(self.device.id, name)
                self._fill_profiles()
                self._notify_engine("Renamed")
        self._ask_name("Rename profile", self.profile.name, done)

    def _edit_apps(self):
        def done(text):
            self.profile.apps = [a.strip() for a in text.split(",") if a.strip()]
            self.profile.save()
            self._fill_profiles()
            self._notify_engine("Linked apps saved")
        self._ask_name("Linked apps", ", ".join(self.profile.apps), done,
                       body="This profile turns on by itself while one of these apps is focused. "
                            "Use the app's window class or program name, separated by commas, e.g. "
                            "“wow.exe, steam_app_1086940”.")

    def _delete_profile(self):
        if len(model.list_profiles(self.device.id)) <= 1:
            self.window.toast("A device needs at least one profile")
            return
        dialog = Adw.AlertDialog(heading=f"Delete “{self.profile.name}”?", body="Its keybinds are removed for good.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response == "delete":
                model.delete_profile(self.device.id, self.profile.name)
                self._switch_to(model.list_profiles(self.device.id)[0])

        dialog.connect("response", on_response)
        dialog.present(self.window)

    def _on_enable_toggled(self, *_):
        on = self.enable_switch.get_active()
        state = model.DeviceState.load(self.device.id)
        state.enabled = on
        state.save()
        run_bg(lambda: client.call("enable", device=self.device.id, enabled=on),
               lambda r: self.window.toast("Remapping on" if on else "Remapping off: buttons do their defaults"))

    # ── Layer bar ────────────────────────────────────────────────────────────

    def _build_layer_bar(self):
        bar = Gtk.Box(spacing=10, margin_start=24, margin_end=24, margin_top=4)
        seg = Gtk.Box(css_classes=["linked"])
        first = None
        for layer, label in (("base", "Standard"), ("hypershift", "Hypershift")):
            t = Gtk.ToggleButton(label=label, active=layer == "base")
            if first:
                t.set_group(first)
            first = first or t
            t.connect("toggled", lambda t, l=layer: t.get_active() and self._set_layer(l))
            seg.append(t)
        bar.append(seg)
        self.layer_hint = Gtk.Label(css_classes=["dim-label", "caption"], xalign=0, wrap=True, hexpand=True)
        bar.append(self.layer_hint)
        self.hs_indicator = Gtk.Label(label="HYPERSHIFT HELD", css_classes=["hs-live"], visible=False)
        bar.append(self.hs_indicator)
        self._update_layer_hint()
        return bar

    def _set_layer(self, layer):
        self.layer = layer
        self._rebuild()

    def _update_layer_hint(self):
        if self.layer == "hypershift":
            self.layer_hint.set_label("These binds apply while a Hypershift key is held (on any device). "
                                      "Buttons left empty here keep their Standard bind.")
        else:
            has_hs = any(isinstance(b.action, model.HypershiftAction) for b in self.profile.layer("base"))
            self.layer_hint.set_label("" if has_hs else "Tip: bind a button to “Hypershift” to unlock a second layer.")

    # ── Diagram ──────────────────────────────────────────────────────────────

    def _rebuild(self):
        while child := self.diagram_box.get_first_child():
            self.diagram_box.remove(child)
        self.key_widgets.clear()
        self._build_diagram()
        self._update_layer_hint()
        self._refresh_panel()

    def _build_diagram(self):
        layout = self.device.layout
        if layout:
            cell = layout.cell
            columns = Gtk.Box(spacing=40, halign=Gtk.Align.START)
            col_boxes: dict[int, Gtk.Box] = {}
            for section in layout.sections:
                if section.column not in col_boxes:
                    col_boxes[section.column] = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22,
                                                        valign=Gtk.Align.START)
                    columns.append(col_boxes[section.column])
                cell = section.cell or layout.cell
                box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
                box.append(Gtk.Label(label=section.title, xalign=0, css_classes=["heading"]))
                on_device = section.silhouette not in (None, "case")
                fixed = Gtk.Fixed(halign=Gtk.Align.START, valign=Gtk.Align.START)
                width, height = section.size or (max(b.col + b.w for b in section.buttons),
                                                 max(b.row + b.h for b in section.buttons))
                fixed.set_size_request(int(width * cell), int(height * cell))
                for b in section.buttons:
                    w = self._key_widget(b, compact=cell < 14 and not on_device, on_device=on_device, cell=cell)
                    w.set_size_request(int(b.w * cell) - 4, int(b.h * cell) - 4)
                    fixed.put(w, b.col * cell + 2, b.row * cell + 2)
                if on_device:
                    overlay = Gtk.Overlay(halign=Gtk.Align.START)
                    overlay.set_child(Silhouette(DEVICES_ART / section.silhouette, int(width * cell), int(height * cell)))
                    overlay.add_overlay(fixed)
                    box.append(overlay)
                elif section.silhouette == "case":
                    case = Gtk.Box(css_classes=["device-case"], halign=Gtk.Align.START)
                    case.append(fixed)
                    box.append(case)
                else:
                    box.append(fixed)
                col_boxes[section.column].append(box)
            self.diagram_box.append(columns)
        else:
            self.diagram_box.append(Gtk.Label(
                label="No diagram for this device yet: use “Find a button” to bind any of its buttons.",
                wrap=True, xalign=0, css_classes=["dim-label"]))

        others = [b for b in self.profile.layer(self.layer) if not self._button_for(b.input)]
        if others:
            group = Adw.PreferencesGroup(title="Other buttons")
            for b in others:
                row = Adw.ActionRow(title=b.input.describe(), subtitle=b.action.describe(), activatable=True)
                row.connect("activated", lambda _r, b=b: self._select_input(b.input))
                group.add(row)
            self.diagram_box.append(group)

    def _key_widget(self, b: Button, compact=False, on_device=False, cell=15.0) -> Gtk.Button:
        inp = self._input_of(b)
        binding = self.profile.get(self.layer, inp) if inp else None
        inherited = inp and self.layer == "hypershift" and not binding and self.profile.get("base", inp)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, spacing=1)
        box.append(Gtk.Label(label=b.short or b.label, css_classes=["key-label"], ellipsize=Pango.EllipsizeMode.END))
        caption = binding.action.describe() if binding else (inherited.action.describe() if inherited else b.caption)
        small = b.w * cell < 70
        if caption and (binding or not compact) and not (small and b.short and not binding):
            box.append(Gtk.Label(label=caption, css_classes=["key-caption"], ellipsize=Pango.EllipsizeMode.END))
        w = Gtk.Button(child=box, css_classes=["key"] + (["compact"] if compact else []) + (["on-device"] if on_device else []),
                       tooltip_text=f"{b.label}: {binding.action.describe() if binding else 'default'}")
        if b.hardware and inp is None:
            w.add_css_class("hardware")
            w.set_tooltip_text(f"{b.label}: handled by the device itself")
        elif binding:
            w.add_css_class("hs-bound" if self.layer == "hypershift" else "bound")
            if isinstance(binding.action, model.HypershiftAction):
                w.add_css_class("hs-key")
        elif inherited:
            w.add_css_class("inherited")
        if self.selected and self.selected.id == b.id:
            w.add_css_class("selected")
        w.connect("clicked", lambda *_: self._select(b))
        self.key_widgets[b.id] = w
        return w

    def _flash(self, inp: model.Input):
        b = self._button_for(inp)
        if b and (w := self.key_widgets.get(b.id)):
            w.add_css_class("pressed")
            GLib.timeout_add(220, lambda: (w.remove_css_class("pressed"), False)[1])

    def _select(self, b: Button | None):
        self.finding = False
        self.detect_status.set_visible(False)
        if self.selected and self.selected.id in self.key_widgets:
            self.key_widgets[self.selected.id].remove_css_class("selected")
        self.selected = b
        self.selected_input = self._input_of(b) if b else None
        if b and b.id in self.key_widgets:
            self.key_widgets[b.id].add_css_class("selected")
        self._refresh_panel()

    def _select_input(self, inp: model.Input):
        if b := self._button_for(inp):
            self._select(b)
            return
        self._select(None)
        self.selected_input = inp
        self._refresh_panel()

    def _learn_selected(self):
        if not self.selected:
            return
        if not self.window.engine_up:
            self.window.toast("Start the engine first: it reports which button you press")
            return
        self.learning = self.selected
        self.detect_status.set_title(f"Press {self.selected.label} on the device")
        self.detect_status.set_description("Peripheral Studio will remember what it sends.")
        for w in (self.placeholder, self.editor, self.panel_title, self.panel_sub, self.detect_link, self.hardware_note):
            w.set_visible(False)
        self.detect_status.set_visible(True)

    def _find_button(self):
        if not self.window.engine_up:
            self.window.toast("Start the engine first: it reports which button you press")
            return
        self.finding = True
        self.detect_status.set_title("Press a button on the device")
        self.detect_status.set_description("It will be selected here so you can bind it.")
        self.detect_link.set_visible(False)
        self.hardware_note.set_visible(False)
        self.placeholder.set_visible(False)
        self.editor.set_visible(False)
        self.panel_title.set_visible(False)
        self.panel_sub.set_visible(False)
        self.detect_status.set_visible(True)

    # ── Binding panel ────────────────────────────────────────────────────────

    def _build_panel(self):
        panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12,
                        margin_top=18, margin_bottom=18, margin_start=18, margin_end=18)
        panel.add_css_class("bind-panel")
        self.panel_title = Gtk.Label(xalign=0, css_classes=["title-2"], wrap=True)
        self.panel_sub = Gtk.Label(xalign=0, css_classes=["dim-label"], wrap=True)
        panel.append(self.panel_title)
        panel.append(self.panel_sub)
        self.detect_link = Gtk.Button(label="Detect what this button sends", css_classes=["flat", "detect-link"],
                                      halign=Gtk.Align.START,
                                      tooltip_text="Press the button once: useful when the device's onboard memory "
                                                   "remaps it (for example from Synapse)")
        self.detect_link.connect("clicked", lambda *_: self._learn_selected())
        panel.append(self.detect_link)

        self.editor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        panel.append(self.editor)

        self.kind_buttons: dict[str, Gtk.ToggleButton] = {}
        kinds = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, max_children_per_line=4, min_children_per_line=4,
                            column_spacing=4, row_spacing=4, homogeneous=True)
        first = None
        for kind, label in KINDS:
            t = Gtk.ToggleButton(label=label, css_classes=["kind"])
            if first:
                t.set_group(first)
            first = first or t
            t.connect("toggled", lambda t, k=kind: t.get_active() and self._show_kind(k))
            self.kind_buttons[kind] = t
            kinds.append(t)
        self.editor.append(kinds)

        self.kind_stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, vhomogeneous=False)
        self.editor.append(self.kind_stack)

        def note(text):
            return Gtk.Label(label=text, wrap=True, xalign=0, css_classes=["dim-label"])

        self.kind_stack.add_named(note("The button does what it does out of the box."), "default")
        self.kind_stack.add_named(self._build_keys_editor(), "keys")

        self.mouse_dd = Gtk.DropDown(model=Gtk.StringList.new([l for _, l in MOUSE]))
        self.kind_stack.add_named(self.mouse_dd, "mouse")
        self.media_dd = Gtk.DropDown(model=Gtk.StringList.new([l for _, l in MEDIA]))
        self.kind_stack.add_named(self.media_dd, "media")

        tx = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.text_entry = Gtk.Entry(placeholder_text="Text to type")
        tx.append(self.text_entry)
        tx.append(Gtk.Label(label="Typed with a US layout.", xalign=0, css_classes=["dim-label", "caption"]))
        self.kind_stack.add_named(tx, "text")

        self.macro_editor = MacroEditor(self.window)
        self.kind_stack.add_named(self.macro_editor, "macro")

        tb = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.turbo_entry = Gtk.Entry(placeholder_text="Key to repeat, e.g. F or Ctrl+C")
        tb.append(self.turbo_entry)
        rate = Gtk.Box(spacing=8)
        rate.append(Gtk.Label(label="Presses per second", hexpand=True, xalign=0))
        self.turbo_rate = Gtk.SpinButton.new_with_range(1, 50, 1)
        self.turbo_rate.set_value(20)
        rate.append(self.turbo_rate)
        tb.append(rate)
        tb.append(Gtk.Label(label="Taps the key over and over while you hold the button.", xalign=0, wrap=True,
                            css_classes=["dim-label", "caption"]))
        self.kind_stack.add_named(tb, "turbo")

        self.kind_stack.add_named(note("Hold this button to switch every device to its Hypershift layer. "
                                       "Edit that layer with the Hypershift tab above the diagram."), "hypershift")

        self.profile_target_dd = Gtk.DropDown()
        self.kind_stack.add_named(self.profile_target_dd, "profile")
        dpi = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.dpi_step_dd = Gtk.DropDown(model=Gtk.StringList.new([label for _s, label in DPI_STEPS]))
        dpi.append(self.dpi_step_dd)
        dpi.append(Gtk.Label(label="Steps through the Naga's DPI stages, with an on-screen popup.", xalign=0, wrap=True,
                             css_classes=["dim-label", "caption"]))
        self.kind_stack.add_named(dpi, "dpi")
        self.kind_stack.add_named(note("The button does nothing."), "disable")

        apply = Gtk.Button(label="Apply", css_classes=["suggested-action"], margin_top=6)
        apply.connect("clicked", lambda *_: self._apply_binding())
        self.editor.append(apply)

        self.detect_status = Adw.StatusPage(icon_name="find-location-symbolic", title="Press a button on the device",
                                            description="It will be selected here so you can bind it.", vexpand=True)
        self.detect_status.add_css_class("compact")
        cancel = Gtk.Button(label="Cancel", halign=Gtk.Align.CENTER, css_classes=["pill"])
        cancel.connect("clicked", lambda *_: (setattr(self, "finding", False), setattr(self, "learning", None),
                                              self.detect_status.set_visible(False), self._refresh_panel()))
        self.detect_status.set_child(cancel)
        self.detect_status.set_visible(False)
        panel.append(self.detect_status)

        self.hardware_note = Adw.StatusPage(icon_name="speedometer-symbolic", vexpand=True, visible=False)
        learn = Gtk.Button(label="Detect what it sends", halign=Gtk.Align.CENTER, css_classes=["pill"])
        learn.connect("clicked", lambda *_: self._learn_selected())
        self.hardware_note.set_child(learn)
        self.hardware_note.add_css_class("compact")
        panel.append(self.hardware_note)

        self.placeholder = Adw.StatusPage(icon_name="input-mouse-symbolic", title="Select a button",
                                          description="Click a button on the diagram, or use “Find a button” and press it on the device.",
                                          vexpand=True)
        self.placeholder.add_css_class("compact")
        panel.append(self.placeholder)
        return panel

    def _build_keys_editor(self):
        sc = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.record_btn = Gtk.Button(css_classes=["record"], height_request=56)
        self.record_label = Gtk.Label(label="Click to record a shortcut", wrap=True)
        self.record_btn.set_child(self.record_label)
        self.record_btn.connect("clicked", lambda *_: self._start_recording())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key_pressed)
        keys.connect("key-released", self._on_key_released)
        self.record_btn.add_controller(keys)
        sc.append(self.record_btn)
        sc.append(Gtk.Label(label="Or type it, e.g. Ctrl+Shift+S, Alt+F4, F13", xalign=0,
                            css_classes=["dim-label", "caption"]))
        self.shortcut_entry = Gtk.Entry(placeholder_text="Ctrl+C")
        sc.append(self.shortcut_entry)
        return sc

    def _current(self) -> model.Input | None:
        return self._input_of(self.selected) if self.selected else self.selected_input

    def _refresh_panel(self):
        self.detect_link.set_visible(False)
        inp = self._current()
        hw = self.selected.hardware if self.selected and self._input_of(self.selected) is None else ""
        self.hardware_note.set_visible(bool(hw) and not self.finding)
        if hw:
            self.hardware_note.set_title(self.selected.label)
            self.hardware_note.set_description(HARDWARE_NOTES.get(hw, ""))
            inp = None
        showing = inp is not None and not self.finding
        self.placeholder.set_visible(inp is None and not self.finding and not hw)
        self.editor.set_visible(showing)
        self.panel_title.set_visible(showing)
        self.panel_sub.set_visible(showing)
        if not showing:
            return
        binding = self.profile.get(self.layer, inp)
        sel = self.selected
        title = (f"Button {sel.label}" if sel.label.isdigit() else sel.label) if sel else inp.describe()
        default = sel.caption if sel and sel.caption else inp.describe()
        self.panel_title.set_label(title + (" (Hypershift)" if self.layer == "hypershift" else ""))
        sends = ""
        if sel and sel.id in self.learned:
            sends = f"Sends {inp.describe()}" + (" (from the device's onboard memory)" if inp.mods or sel.hardware else "")
        self.panel_sub.set_label((sends or f"Default: {default}") +
                                 (f"  ·  Now: {binding.action.describe()}" if binding else ""))
        self.detect_link.set_visible(sel is not None)

        names = ["Next profile", "Previous profile"] + model.list_profiles(self.device.id)
        self.profile_target_dd.set_model(Gtk.StringList.new(names))

        a = binding.action if binding else None
        kind = "default"
        self.shortcut_entry.set_text("")
        self.record_label.set_label("Click to record a shortcut")
        self.text_entry.set_text("")
        self.turbo_entry.set_text("")
        self.macro_editor.load(None)
        if isinstance(a, model.KeysAction):
            if len(a.keys) == 1 and a.keys[0].startswith("BTN_"):
                kind = "mouse"
                self.mouse_dd.set_selected(next((i for i, (k, _) in enumerate(MOUSE) if k == a.keys[0]), 0))
            elif len(a.keys) == 1 and any(k == a.keys[0] for k, _ in MEDIA):
                kind = "media"
                self.media_dd.set_selected(next(i for i, (k, _) in enumerate(MEDIA) if k == a.keys[0]))
            else:
                kind = "keys"
                self.shortcut_entry.set_text("+".join(model.pretty_key(k) for k in a.keys))
                self.record_label.set_label(a.describe())
        elif isinstance(a, model.ScrollAction):
            kind = "mouse"
            key = f"scroll:{a.axis}:{1 if a.amount > 0 else -1}"
            self.mouse_dd.set_selected(next((i for i, (k, _) in enumerate(MOUSE) if k == key), 0))
        elif isinstance(a, model.MacroAction):
            if len(a.steps) == 1 and a.steps[0].op == "text" and a.mode == "once" and not a.name:
                kind = "text"
                self.text_entry.set_text(a.steps[0].text)
            else:
                kind = "macro"
                self.macro_editor.load(a)
        elif isinstance(a, model.TurboAction):
            kind = "turbo"
            self.turbo_entry.set_text("+".join(model.pretty_key(k) for k in a.keys))
            self.turbo_rate.set_value(round(1000 / a.interval_ms))
        elif isinstance(a, model.HypershiftAction):
            kind = "hypershift"
        elif isinstance(a, model.ProfileAction):
            kind = "profile"
            self.profile_target_dd.set_selected({"next": 0, "previous": 1}.get(
                a.target, names.index(a.target) if a.target in names else 0))
        elif isinstance(a, model.DpiAction):
            kind = "dpi"
            self.dpi_step_dd.set_selected(next((i for i, (st, _l) in enumerate(DPI_STEPS) if st == a.step), 0))
        elif isinstance(a, model.DisableAction):
            kind = "disable"
        # A Hypershift key on the Hypershift layer could never be released into.
        self.kind_buttons["hypershift"].set_sensitive(self.layer == "base")
        self.kind_buttons[kind].set_active(True)
        self._show_kind(kind)

    def _show_kind(self, kind):
        self.kind_stack.set_visible_child_name(kind)
        self.recording = False

    # ── Shortcut recording ───────────────────────────────────────────────────

    def _start_recording(self):
        self.recording = True
        self.recorded = []
        self.record_label.set_label("Press the keys…")
        self.record_btn.add_css_class("recording")
        self.record_btn.grab_focus()

    def _finish_recording(self):
        self.recording = False
        self.record_btn.remove_css_class("recording")
        self.record_label.set_label(model.KeysAction(keys=self.recorded).describe())
        self.shortcut_entry.set_text("+".join(model.pretty_key(k) for k in self.recorded))

    def _on_key_pressed(self, _ctl, keyval, keycode, state):
        if not self.recording:
            return False
        name = evdev_name(keycode)
        if name and name not in self.recorded:
            self.recorded.append(name)
            self.record_label.set_label(model.KeysAction(keys=self.recorded).describe() + " …")
            if name not in MODIFIERS:
                self._finish_recording()
        return True

    def _on_key_released(self, _ctl, keyval, keycode, state):
        if self.recording and self.recorded and evdev_name(keycode) in MODIFIERS:
            self._finish_recording()  # a lone modifier, e.g. bind a button to Shift

    # ── Apply ────────────────────────────────────────────────────────────────

    def _parse(self, text, what):
        try:
            return model.parse_shortcut(text)
        except ValueError as ex:
            self.window.toast(f"{what}: {ex}")
            return None

    def _action_from_editor(self):
        """The edited action, None for Default, or False when what was entered is invalid."""
        kind = self.kind_stack.get_visible_child_name()
        if kind == "default":
            return None
        if kind == "keys":
            keys = self._parse(self.shortcut_entry.get_text(), "Shortcut")
            return model.KeysAction(keys=keys) if keys else False
        if kind == "mouse":
            key = MOUSE[self.mouse_dd.get_selected()][0]
            if key.startswith("scroll:"):
                _, axis, amount = key.split(":")
                return model.ScrollAction(axis=axis, amount=int(amount))
            return model.KeysAction(keys=[key])
        if kind == "media":
            return model.KeysAction(keys=[MEDIA[self.media_dd.get_selected()][0]])
        if kind == "text":
            text = self.text_entry.get_text()
            if not text:
                self.window.toast("Type some text first")
                return False
            return model.MacroAction(steps=[model.MacroStep("text", text=text)])
        if kind == "macro":
            macro = self.macro_editor.action()
            if not macro:
                self.window.toast("Add some steps first")
                return False
            return macro
        if kind == "turbo":
            keys = self._parse(self.turbo_entry.get_text(), "Turbo key")
            return model.TurboAction(keys=keys, interval_ms=round(1000 / self.turbo_rate.get_value())) if keys else False
        if kind == "hypershift":
            return model.HypershiftAction()
        if kind == "profile":
            i = self.profile_target_dd.get_selected()
            item = self.profile_target_dd.get_selected_item()
            return model.ProfileAction(target="next" if i == 0 else "previous" if i == 1 else item.get_string())
        if kind == "dpi":
            return model.DpiAction(step=DPI_STEPS[self.dpi_step_dd.get_selected()][0])
        return model.DisableAction()

    def _apply_binding(self):
        inp = self._current()
        if inp is None:
            return
        action = self._action_from_editor()
        if action is False:
            return
        if action is not None and self.layer == "base" and inp.key == (ecodes.EV_KEY, ecodes.BTN_LEFT, None):
            self.window.toast("Left click can only be changed on the Hypershift layer, so it can't be lost")
            return
        if action is None:
            self.profile.remove(self.layer, inp)
            message = "Reset to default"
        else:
            self.profile.set(model.Binding(inp, action, self.layer))
            message = "Saved"
        self.profile.save()
        self._rebuild()
        self._notify_engine(message)
