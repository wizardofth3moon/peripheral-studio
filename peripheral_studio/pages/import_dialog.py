"""Import from Synapse: pick a Windows folder, review the converted profiles, import."""
from __future__ import annotations

from pathlib import Path

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from ..engine import client, model
from ..importers import synapse
from ..util import run_bg


class ImportDialog(Adw.Dialog):
    def __init__(self, window):
        super().__init__(title="Import from Synapse", content_width=640, content_height=720)
        self.window = window
        self.items: list[tuple[synapse.Imported, Gtk.CheckButton]] = []

        view = Adw.ToolbarView()
        view.add_top_bar(Adw.HeaderBar())
        self.toasts = Adw.ToastOverlay()
        view.set_content(self.toasts)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.toasts.set_child(box)

        source = Adw.PreferencesGroup(margin_top=12, margin_start=12, margin_end=12,
                                      description="Synapse 4 keeps a copy of your profiles on the Windows "
                                                  "machine. Point this at a Windows drive, a user folder or a "
                                                  "backup of one.")
        self.folder_row = Adw.ActionRow(title="Windows folder", subtitle="Looking…")
        self.folder_row.set_subtitle_lines(2)
        choose = Gtk.Button(label="Choose…", valign=Gtk.Align.CENTER)
        choose.connect("clicked", lambda *_: self._choose())
        self.folder_row.add_suffix(choose)
        source.add(self.folder_row)
        box.append(source)

        self.stack = Gtk.Stack(vexpand=True)
        self.status = Adw.StatusPage(icon_name="folder-download-symbolic", title="Looking for Synapse data…")
        self.stack.add_named(self.status, "status")
        self.page = Adw.PreferencesPage()
        self.stack.add_named(self.page, "list")
        box.append(self.stack)

        bottom = Gtk.Box(spacing=8, margin_top=8, margin_bottom=12, margin_start=12, margin_end=12)
        self.summary = Gtk.Label(xalign=0, hexpand=True, css_classes=["dim-label"])
        bottom.append(self.summary)
        self.import_btn = Gtk.Button(label="Import", css_classes=["suggested-action", "pill"], sensitive=False)
        self.import_btn.connect("clicked", lambda *_: self._import())
        bottom.append(self.import_btn)
        box.append(bottom)
        self.set_child(view)

        run_bg(synapse.candidates, self._on_candidates)

    # ── Source ───────────────────────────────────────────────────────────────

    def _on_candidates(self, found):
        if isinstance(found, Exception) or not found:
            self.folder_row.set_subtitle("Nothing found automatically: choose the folder")
            self.status.set_title("Choose your Windows folder")
            self.status.set_description("For example the Windows drive, C:\\Users\\<you>, or a backup of it.")
            return
        self._load(found[0])

    def _choose(self):
        dialog = Gtk.FileDialog(title="Windows drive, user folder or backup")
        dialog.set_initial_folder(Gio.File.new_for_path("/mnt" if Path("/mnt").is_dir() else str(Path.home())))
        dialog.select_folder(self.window, None, self._on_chosen)

    def _on_chosen(self, dialog, result):
        try:
            folder = dialog.select_folder_finish(result)
        except GLib.Error:
            return
        hit = synapse.locate(Path(folder.get_path()))
        if not hit:
            self.toasts.add_toast(Adw.Toast(title="No Synapse 4 data in that folder"))
            return
        self._load(hit)

    def _load(self, local_storage: Path):
        self.folder_row.set_subtitle(GLib.markup_escape_text(str(local_storage)))
        self.status.set_title("Reading Synapse profiles…")
        self.stack.set_visible_child_name("status")
        devices = self.window.devices

        def work():
            pairs = synapse.match(synapse.read(local_storage), devices)
            return [(d, synapse.convert(sd, d, devices, model.list_profiles(d.id))) for sd, d in pairs]

        run_bg(work, self._show)

    # ── Review ───────────────────────────────────────────────────────────────

    def _show(self, result):
        if isinstance(result, Exception):
            self.status.set_title("Couldn't read the Synapse data")
            self.status.set_description(GLib.markup_escape_text(str(result)))
            return
        self.page = self._rebuild_page()
        self.items.clear()
        for device, imported in result:
            useful = [r for r in imported if r.profile.bindings]
            if not useful:
                continue
            group = Adw.PreferencesGroup(title=device.name,
                                         description=f"{len(useful)} profiles with binds")
            for r in useful:
                check = Gtk.CheckButton(active=r.selected, valign=Gtk.Align.CENTER)
                check.connect("toggled", lambda *_: self._update_summary())
                n = len(r.profile.bindings)
                subtitle = f"{n} bind{'s' if n != 1 else ''} · from “{r.source}”"
                if r.skipped:
                    row = Adw.ExpanderRow(title=GLib.markup_escape_text(r.name), subtitle=GLib.markup_escape_text(
                        f"{subtitle} · {len(r.skipped)} note{'s' if len(r.skipped) != 1 else ''}"))
                    for note in r.skipped:
                        nr = Adw.ActionRow(title=GLib.markup_escape_text(note), css_classes=["dim-label"])
                        nr.set_title_lines(3)
                        row.add_row(nr)
                else:
                    row = Adw.ActionRow(title=GLib.markup_escape_text(r.name), subtitle=GLib.markup_escape_text(subtitle),
                                        activatable_widget=check)
                row.add_prefix(check)
                group.add(row)
                self.items.append((r, check))
            self.page.add(group)
        if not self.items:
            self.status.set_title("No profiles with binds for the devices connected here")
            self.stack.set_visible_child_name("status")
            return
        self.stack.set_visible_child_name("list")
        self._update_summary()

    def _rebuild_page(self):
        old = self.stack.get_child_by_name("list")
        self.stack.remove(old)
        page = Adw.PreferencesPage()
        self.stack.add_named(page, "list")
        return page

    def _update_summary(self):
        chosen = [r for r, c in self.items if c.get_active()]
        binds = sum(len(r.profile.bindings) for r in chosen)
        self.summary.set_label(f"{len(chosen)} profiles, {binds} binds selected")
        self.import_btn.set_sensitive(bool(chosen))
        self.import_btn.set_label(f"Import {len(chosen)}" if chosen else "Import")

    def _import(self):
        chosen = [r for r, c in self.items if c.get_active()]
        devices = set()
        for r in chosen:
            name, n = r.name, 2
            while name in model.list_profiles(r.profile.device):
                name, n = f"{r.name} ({n})", n + 1
            r.profile.name = name
            r.profile.save()
            devices.add(r.profile.device)
        for device in devices:
            run_bg(lambda d=device: client.call("reload", device=d))
        self.window.toast(f"Imported {len(chosen)} profiles from Synapse. Pick one from the Profile list.")
        self.window.reload_current_device()
        self.close()
