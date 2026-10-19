// Peripheral Studio OSD: GNOME's own on-screen display (the volume/brightness popup) for
// Peripheral Studio. Apps may not call the shell's ShowOSD, so this exposes one small D-Bus
// method the remap engine calls: Show(icon, label, level) with level 0..1, or -1 for no bar.
import Gio from 'gi://Gio';
import * as Main from 'resource:///org/gnome/shell/ui/main.js';
import {Extension} from 'resource:///org/gnome/shell/extensions/extension.js';

const IFACE = `
<node>
  <interface name="org.peripheralstudio.Osd">
    <method name="Show">
      <arg type="s" direction="in" name="icon"/>
      <arg type="s" direction="in" name="label"/>
      <arg type="d" direction="in" name="level"/>
    </method>
  </interface>
</node>`;

export default class PeripheralStudioOsd extends Extension {
    enable() {
        this._dbus = Gio.DBusExportedObject.wrapJSObject(IFACE, this);
        this._dbus.export(Gio.DBus.session, '/org/peripheralstudio/Osd');
        this._owner = Gio.bus_own_name_on_connection(Gio.DBus.session, 'org.peripheralstudio.Osd',
            Gio.BusNameOwnerFlags.REPLACE, null, null);
    }

    disable() {
        if (this._owner)
            Gio.bus_unown_name(this._owner);
        this._dbus?.unexport();
        this._dbus = null;
        this._owner = 0;
    }

    Show(icon, label, level) {
        const gicon = Gio.ThemedIcon.new_with_default_fallbacks(icon || 'input-gaming-symbolic');
        Main.osdWindowManager.showAll(gicon, label, level >= 0 ? level : null, level >= 0 ? 1 : -1);
    }
}
