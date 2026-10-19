# Peripheral Studio

A Synapse-style app for Razer peripherals on Linux (GTK4 + libadwaita, Python). It handles keybinds, lighting, DPI, polling rate and power settings in one window.

It is self-contained: no OpenRazer and no input-remapper needed.

- **Keybinds:** a background remap engine (`peripheral-studio-engine`, a systemd user service) reads the Razer devices, grabs the ones with an active profile, and sends their binds through a virtual keyboard and mouse. Unbound buttons pass through unchanged.
  - **Hypershift:** a second layer, used while the Hypershift button is held.
  - **Per-app profiles:** a profile can list apps and becomes active while one of them is focused.
  - **Macros:** recorded or edited steps (key down/up/tap, waits, text, scrolling).
  - **Synapse 4 import:** brings over profiles from a Windows Synapse install.
- **Lighting, DPI, polling rate and power:** Peripheral Studio talks to the devices directly, with Razer's settings commands over hidraw (see [docs/razer-protocol.md](docs/razer-protocol.md)). A device it has no driver for falls back to OpenRazer, if that happens to be installed.

## Install

```sh
packaging/build-deb.sh                      # builds dist/peripheral-studio_<version>_all.deb (+ razer-cfg255-dkms)
sudo apt install ./dist/peripheral-studio_0.1.0_all.deb
sudo apt install ./dist/razer-cfg255-dkms_1.0_all.deb   # only for a BlackShark V3; replug its dongle after
gnome-extensions enable peripheral-studio-osd@peripheral-studio   # popups; takes effect after logging in again
```

The package installs the app (`/usr/lib/peripheral-studio`), the remap engine as a user service (enabled for all users),
the udev rule, uhid/uinput loading at boot and the GNOME popup extension. It conflicts with input-remapper's daemon:
both would try to own the same devices.

## Run

```sh
peripheral-studio            # or "Peripheral Studio" in the app menu
```

**Device access:** the app and the engine need read/write access to the Razer devices. On first start the app offers **Grant access**, which runs the setup below behind the desktop's password prompt. By hand:

```sh
sudo cp data/udev/70-peripheral-studio.rules /etc/udev/rules.d/
sudo udevadm control --reload && sudo udevadm trigger
```

## Devices

| Device | Diagram | Lighting | Performance and power |
| --- | --- | --- | --- |
| Naga V2 HyperSpeed | Top buttons, wheel, 12 side buttons | No (it has no RGB) | DPI, polling rate, battery, sleep timer |
| Tartarus V2 | Keys 01–19, thumb buttons, D-pad, wheel | Effects, brightness, profile LEDs | — |
| Ornata V3 X | Full keyboard and navigation keys | Effects, brightness | — |
| BlackShark V3 | Mic mute, volume, media keys | — | Audio page: EQ presets and bands, THX Spatial, ultra-low latency, mic EQ, sidetone, game/chat balance, call mixing, function button, auto power-off, voice prompts, dongle light; battery |

A device without a diagram can still be bound with **Find a button**.

## Binding a button

1. Click a button on the diagram, or click **Find a button** and press it on the device.
2. Pick what the button should do:

   | Choice | What it does |
   | --- | --- |
   | Default | The button does what it does out of the box |
   | Keyboard | A key or shortcut: record it, or type it like `Control_L + Shift_L + s` |
   | Mouse | A mouse button, or a scroll |
   | Media | Play/pause, volume, mute and similar keys |
   | Text | Types a string |
   | Macro | A sequence of steps, recorded or edited |
   | Turbo | Repeats a key while the button is held |
   | Hypershift | Holds the Hypershift layer |
   | Profile | Switches to another profile |
   | DPI | Next / previous / cycle through the mouse's DPI stages |
   | Disable | The button does nothing |

3. Click **Apply**. The profile is saved and the engine applies it immediately.

**Profiles:** use **+** to add a profile, and **⋮** to rename, duplicate or delete one. Switching profiles applies the new one immediately while remapping is on.

## Profiles, lighting and popups

- **Export / import** (profile menu ⋮): a profile as a `.json` file, to back up or share. Imports only into the same device model.
- **Per-key colours** (Tartarus V2): Lighting → Per key, then paint keys; saved with the profile like other lighting.
- **DPI stages** (Naga): edited on the Performance page; buttons bound to "DPI: next / previous stage" step through them.

- **Lighting with a profile:** on the Lighting page, turn on "Save with profile …". Switching to that profile (by hand, from a button, or by app) then brings its lighting back. Profiles without saved lighting leave the lighting alone.
- **On-screen popups** for profile switches, DPI changes and Hypershift use GNOME's own on-screen display through the bundled extension `data/gnome-shell/peripheral-studio-osd@peripheral-studio` (copy it to `~/.local/share/gnome-shell/extensions/` and enable it; on Wayland it appears after logging in again). Without it they show as short notifications. Turn kinds off in `~/.config/peripheral-studio/settings.json`: `{"osd": {"profile": false, "dpi": true, "hypershift": false}}`.

## Limits

- **Left click can't be remapped** on the base layer, so you can't lose your main click.
- **Lighting is per device, not per profile** yet, and custom per-key colours aren't supported.
- **The BlackShark V3 needs the `razer-cfg255` kernel module** (in `third_party/`) until the matching usbcore quirk is in the kernel: without it the headset never enables its settings channel. Its active EQ preset can't be read back, so the app remembers the last one chosen.
- **The Naga V2 HyperSpeed has no lighting** (it has no RGB), and its settings are unavailable while the mouse sleeps.

## Debugging

To render the window to a PNG and quit, run:

```sh
PS_SCREENSHOT=/tmp/shot.png PS_DEVICE=1 PS_TAB=lighting peripheral-studio
```

`PS_SELECT=<button id>` and `PS_KIND=<kind>` also pre-select a button and an action type.
