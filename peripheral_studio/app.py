"""Peripheral Studio: keybinds, lighting and performance for Razer peripherals on Linux."""
from __future__ import annotations

import os
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from . import devices as devmod  # noqa: E402
from .engine import client  # noqa: E402
from .pages.customize import CustomizePage  # noqa: E402
from .pages.lighting import LightingPage, has_lighting  # noqa: E402
from .pages.performance import PerformancePage, has_performance, has_power  # noqa: E402
from .util import run_bg  # noqa: E402

APP_ID = "com.herd.PeripheralStudio"
DATA = Path(__file__).resolve().parent.parent / "data"
ICONS = {"mouse": "input-mouse-symbolic", "keypad": "input-gaming-symbolic",
         "keyboard": "input-keyboard-symbolic", "headset": "audio-headset-symbolic"}
KIND_LABEL = {"mouse": "Mouse", "keypad": "Keypad", "keyboard": "Keyboard", "headset": "Headset"}


class Window(Adw.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Peripheral Studio", default_width=1440, default_height=820)
        self.set_size_request(900, 560)
        self.devices: list[devmod.Device] = []
        self.engine_up = client.available()
        self.customize = None
        self.watcher = client.Watcher(self._on_engine_event)
        GLib.timeout_add_seconds(3, self._check_engine)

        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=240, max_sidebar_width=300)
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)

        # Sidebar
        side = Adw.ToolbarView()
        head = Adw.HeaderBar(show_title=True)
        head.set_title_widget(Adw.WindowTitle(title="Peripheral Studio"))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Look for devices again")
        refresh.connect("clicked", lambda *_: self.load_devices())
        head.pack_start(refresh)
        imp = Gtk.Button(icon_name="document-open-symbolic", tooltip_text="Import profiles from Razer Synapse")
        imp.connect("clicked", lambda *_: self.open_import())
        head.pack_end(imp)
        side.add_top_bar(head)
        self.device_list = Gtk.ListBox(css_classes=["navigation-sidebar"])
        self.device_list.connect("row-selected", self._on_device_selected)
        scroller = Gtk.ScrolledWindow(vexpand=True, child=self.device_list)
        side.set_content(scroller)
        self.split.set_sidebar(Adw.NavigationPage(title="Devices", child=side))

        # Content
        self.content = Adw.ToolbarView()
        self.content_head = Adw.HeaderBar()
        self.switcher = Adw.ViewSwitcher(policy=Adw.ViewSwitcherPolicy.WIDE)
        self.content_head.set_title_widget(self.switcher)
        self.content.add_top_bar(self.content_head)
        self.content.set_content(Adw.StatusPage(icon_name="input-gaming-symbolic", title="Looking for devices…"))
        self.split.set_content(Adw.NavigationPage(title="Device", child=self.content))

        self.load_devices()

    def _check_engine(self):
        up = client.available()
        if up != self.engine_up:
            self.engine_up = up
            if self.customize:
                self.customize.engine_changed(up)
        return True

    def _show_batteries(self, levels):
        """Battery badges in the sidebar, from the engine's battery service."""
        if isinstance(levels, Exception) or not isinstance(levels, dict):
            return
        for did, badge in getattr(self, "battery_badges", {}).items():
            info = levels.get(did)
            if not info or info.get("percent") is None:
                continue
            pct = round(info["percent"])
            charging = info.get("charging")
            level = "full" if pct >= 90 else "good" if pct >= 50 else "low" if pct >= 20 else "caution"
            badge.icon.set_from_icon_name(f"battery-{level}{'-charging' if charging else ''}-symbolic")
            badge.label.set_text(f"{pct}%")
            badge.set_tooltip_text(f"Battery {pct}%" + (", charging" if charging else ""))
            badge.set_visible(True)

    def _on_engine_event(self, msg):
        if msg.get("event") == "battery":
            self._show_batteries(msg.get("levels", {}))
            return
        if not self.engine_up:
            self.engine_up = True
            if self.customize:
                self.customize.engine_changed(True)
        if self.customize:
            self.customize.on_engine_event(msg)

    def start_engine(self):
        """Starts the engine's systemd user service, falling back to a plain background process."""
        import subprocess

        def start():
            r = subprocess.run(["systemctl", "--user", "start", "peripheral-studio-engine.service"],
                               capture_output=True, text=True)
            if r.returncode != 0:
                subprocess.Popen([sys.executable, "-W", "ignore", "-m", "peripheral_studio.engine"],
                                 cwd=str(DATA.parent), start_new_session=True,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            import time
            for _ in range(30):
                if client.available():
                    return True
                time.sleep(0.1)
            return False

        run_bg(start, lambda ok: (self._check_engine(), self.toast("Engine started" if ok is True else "Couldn't start the engine")))

    def open_import(self):
        from .pages.import_dialog import ImportDialog
        self.import_dialog = ImportDialog(self)
        self.import_dialog.present(self)

    def reload_current_device(self):
        row = self.device_list.get_selected_row()
        if row is not None:
            self._on_device_selected(self.device_list, row)

    def toast(self, text):
        self.toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=3))

    def load_devices(self):
        run_bg(devmod.discover, self._show_devices)

    def _show_devices(self, result):
        if isinstance(result, Exception):
            self.content.set_content(Adw.StatusPage(icon_name="dialog-error-symbolic", title="Couldn't read devices",
                                                    description=str(result)))
            return
        self.devices = result
        while row := self.device_list.get_row_at_index(0):
            self.device_list.remove(row)
        self.battery_badges = {}
        for d in self.devices:
            row = Adw.ActionRow(title=d.name, subtitle=KIND_LABEL.get(d.kind, "Device"))
            row.add_prefix(Gtk.Image(icon_name=ICONS.get(d.kind, "input-gaming-symbolic"), pixel_size=24))
            badge = Gtk.Box(spacing=3, valign=Gtk.Align.CENTER, visible=False, css_classes=["dim-label"])
            badge.icon = Gtk.Image(icon_name="battery-good-symbolic")
            badge.label = Gtk.Label(css_classes=["caption"])
            badge.append(badge.icon)
            badge.append(badge.label)
            row.add_suffix(badge)
            self.battery_badges[d.id] = badge
            self.device_list.append(row)
        run_bg(lambda: client.call("status", timeout=1.0).get("batteries", {}), self._show_batteries)
        if not self.devices:
            self.content.set_content(Adw.StatusPage(icon_name="input-gaming-symbolic", title="No Razer devices found",
                                                    description="Plug one in, then refresh."))
            return
        want = int(os.environ.get("PS_DEVICE", "0"))
        self.device_list.select_row(self.device_list.get_row_at_index(min(want, len(self.devices) - 1)))

    def _on_device_selected(self, _list, row):
        if row is None:
            return
        device = self.devices[row.get_index()]
        stack = Adw.ViewStack()
        self.customize = CustomizePage(self, device)
        stack.add_titled_with_icon(self.customize, "customize", "Customize", "input-keyboard-symbolic")
        if device.razer is not None and has_lighting(device):
            stack.add_titled_with_icon(LightingPage(self, device), "lighting", "Lighting", "weather-clear-symbolic")
        if device.razer is not None and (has_performance(device) or has_power(device)):
            stack.add_titled_with_icon(PerformancePage(self, device), "performance", "Performance",
                                       "power-profile-performance-symbolic")
        self.switcher.set_stack(stack)
        self.content.set_content(stack)
        self.split.set_show_content(True)
        if tab := os.environ.get("PS_TAB"):
            stack.set_visible_child_name(tab)
        self.set_title(f"{device.name} · Peripheral Studio")


class App(Adw.Application):
    def __init__(self):
        flags = Gio.ApplicationFlags.DEFAULT_FLAGS
        if os.environ.get("PS_SCREENSHOT"):  # debug runs must not hand off to an open window
            flags |= Gio.ApplicationFlags.NON_UNIQUE
        super().__init__(application_id=APP_ID, flags=flags)
        self.window = None

    def do_startup(self):
        Adw.Application.do_startup(self)
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.FORCE_DARK)
        css = Gtk.CssProvider()
        css.load_from_path(str(DATA / "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        quit_action = Gio.SimpleAction.new("quit", None)
        quit_action.connect("activate", lambda *_: self.quit())
        self.add_action(quit_action)
        self.set_accels_for_action("app.quit", ["<Control>q"])

    def do_activate(self):
        if not self.window:
            self.window = Window(self)
        self.window.present()
        if shot := os.environ.get("PS_SCREENSHOT"):
            GLib.timeout_add(int(os.environ.get("PS_SCREENSHOT_DELAY", "2500")), self._screenshot, shot)

    def _screenshot(self, path):
        """Debug aid: renders the window to a PNG and quits (PS_SCREENSHOT=path)."""
        win = self.window
        if os.environ.get("PS_IMPORT"):
            win.open_import()
            GLib.timeout_add(int(os.environ.get("PS_FIND_WAIT", "6000")), self._screenshot_now, path)
            return False
        if os.environ.get("PS_FIND") and win.customize:
            win.customize._find_button()
            GLib.timeout_add(int(os.environ.get("PS_FIND_WAIT", "3000")), self._screenshot_now, path)
            return False
        if sel := os.environ.get("PS_SELECT"):
            page = win.content.get_content()
            cust = page.get_child_by_name("customize") if isinstance(page, Adw.ViewStack) else None
            if cust and cust.device.layout:
                button = next((b for b in cust.device.layout.all_buttons() if b.id == sel), None)
                if button:
                    cust._select(button)
                    if kind := os.environ.get("PS_KIND"):
                        cust.kind_buttons[kind].set_active(True)
            GLib.timeout_add(400, self._screenshot_now, path)
            return False
        return self._screenshot_now(path)

    def _screenshot_now(self, path):
        win = self.window
        paintable = Gtk.WidgetPaintable.new(win)
        w, h = win.get_width(), win.get_height()
        snap = Gtk.Snapshot()
        paintable.snapshot(snap, w, h)
        node = snap.to_node()
        if node:
            tex = win.get_native().get_renderer().render_texture(node, None)
            tex.save_to_png(path)
        self.quit()
        return False


def main():
    return App().run(sys.argv)
