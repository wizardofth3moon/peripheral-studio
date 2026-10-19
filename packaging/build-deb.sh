#!/bin/sh
# Builds dist/peripheral-studio_<version>_all.deb and dist/razer-cfg255-dkms_1.0_all.deb.
# No build tools needed beyond dpkg-deb. Usage: packaging/build-deb.sh
set -eu
cd "$(dirname "$0")/.."
VERSION=$(python3 -c 'import peripheral_studio; print(peripheral_studio.__version__)')
OUT=dist
STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$OUT"

# ── peripheral-studio ────────────────────────────────────────────────────────
R=$STAGE/peripheral-studio
LIB=$R/usr/lib/peripheral-studio
mkdir -p "$LIB" "$R/usr/bin" "$R/DEBIAN"
# the app keeps its layout: package next to data/ (DATA = <package>/../data)
rsync -a --exclude __pycache__ --exclude '*.pyc' peripheral_studio "$LIB/"
rsync -a --exclude systemd --exclude gnome-shell --exclude 'peripheral-studio.svg' data "$LIB/"

cat > "$R/usr/bin/peripheral-studio" <<'SH'
#!/bin/sh
export PYTHONPATH=/usr/lib/peripheral-studio${PYTHONPATH:+:$PYTHONPATH}
exec python3 -W ignore -m peripheral_studio "$@"
SH
chmod 755 "$R/usr/bin/peripheral-studio"

install -Dm644 /dev/stdin "$R/usr/lib/systemd/user/peripheral-studio-engine.service" <<'UNIT'
[Unit]
Description=Peripheral Studio remap engine
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
Environment=PYTHONPATH=/usr/lib/peripheral-studio
ExecStart=/usr/bin/python3 -W ignore -m peripheral_studio.engine
Restart=on-failure
RestartSec=2

[Install]
WantedBy=graphical-session.target
UNIT

install -Dm644 data/udev/70-peripheral-studio.rules "$R/usr/lib/udev/rules.d/70-peripheral-studio.rules"
printf 'uhid\nuinput\n' | install -Dm644 /dev/stdin "$R/usr/lib/modules-load.d/peripheral-studio.conf"
EXT=peripheral-studio-osd@peripheral-studio
mkdir -p "$R/usr/share/gnome-shell/extensions"
cp -r "data/gnome-shell/$EXT" "$R/usr/share/gnome-shell/extensions/"
install -Dm644 data/peripheral-studio.svg "$R/usr/share/icons/hicolor/scalable/apps/com.herd.PeripheralStudio.svg"
install -Dm644 /dev/stdin "$R/usr/share/applications/com.herd.PeripheralStudio.desktop" <<'DESK'
[Desktop Entry]
Type=Application
Name=Peripheral Studio
Comment=Keybinds, lighting and DPI for Razer peripherals
Exec=peripheral-studio
Icon=com.herd.PeripheralStudio
Categories=Settings;HardwareSettings;
Keywords=razer;keybind;remap;macro;lighting;dpi;mouse;keyboard;headset;
StartupWMClass=com.herd.PeripheralStudio
DESK
install -Dm644 README.md "$R/usr/share/doc/peripheral-studio/README.md"
install -Dm644 docs/razer-protocol.md "$R/usr/share/doc/peripheral-studio/razer-protocol.md"
install -Dm644 /dev/stdin "$R/usr/share/doc/peripheral-studio/copyright" <<'COPY'
Format: https://www.debian.org/doc/packaging-manuals/copyright-format/1.0/
Upstream-Name: peripheral-studio

Files: *
Copyright: 2026 Donnie Herd
License: GPL-3.0-or-later
 On Debian systems, the full text is in /usr/share/common-licenses/GPL-3.

Files: peripheral_studio/hardware/*
Comment: Razer command values were taken from reading the OpenRazer driver (GPL-2.0)
 and OpenRazer pull request #2794; the Tartarus V2 light map from OpenRGB (GPL-2.0).
 See razer-protocol.md.
License: GPL-3.0-or-later
COPY

SIZE=$(du -sk "$R/usr" | cut -f1)
cat > "$R/DEBIAN/control" <<CTRL
Package: peripheral-studio
Version: $VERSION
Section: utils
Priority: optional
Architecture: all
Maintainer: Donnie Herd <75598735+wizardofth3moon@users.noreply.github.com>
Installed-Size: $SIZE
Depends: python3 (>= 3.10), python3-gi, gir1.2-gtk-4.0, gir1.2-adw-1, gir1.2-rsvg-2.0, python3-evdev, python3-pyudev, policykit-1 | pkexec
Suggests: razer-cfg255-dkms, python3-openrazer
Conflicts: input-remapper-daemon
Description: Keybinds, lighting and settings for Razer peripherals
 A Synapse-style GTK4 app with its own remap engine: per-button binds, macros,
 Hypershift layer, per-app profiles, lighting (incl. per-key on the Tartarus V2),
 DPI stages, battery levels in the desktop power menu, BlackShark V3 audio
 settings and Synapse 4 profile import. Talks to the devices directly over
 hidraw; no OpenRazer needed for the supported models.
CTRL

cat > "$R/DEBIAN/postinst" <<'SH'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    modprobe uhid 2>/dev/null || true
    modprobe uinput 2>/dev/null || true
    if command -v udevadm >/dev/null; then
        udevadm control --reload || true
        udevadm trigger --subsystem-match=hidraw --subsystem-match=input --subsystem-match=misc --subsystem-match=usb || true
    fi
    systemctl --global enable peripheral-studio-engine.service >/dev/null 2>&1 || true
    gtk-update-icon-cache -q -t /usr/share/icons/hicolor 2>/dev/null || true
fi
SH
cat > "$R/DEBIAN/prerm" <<'SH'
#!/bin/sh
set -e
if [ "$1" = remove ] || [ "$1" = purge ]; then
    systemctl --global disable peripheral-studio-engine.service >/dev/null 2>&1 || true
fi
SH
chmod 755 "$R/DEBIAN/postinst" "$R/DEBIAN/prerm"
dpkg-deb --root-owner-group --build "$R" "$OUT/peripheral-studio_${VERSION}_all.deb" >/dev/null

# ── razer-cfg255-dkms (optional: BlackShark V3 battery/settings channel) ────────
K=$STAGE/razer-cfg255-dkms
mkdir -p "$K/usr/src/razer-cfg255-1.0" "$K/DEBIAN"
cp third_party/razer-cfg255/Makefile third_party/razer-cfg255/dkms.conf third_party/razer-cfg255/razer-cfg255.c "$K/usr/src/razer-cfg255-1.0/"
install -Dm644 third_party/razer-cfg255/SOURCE.md "$K/usr/share/doc/razer-cfg255-dkms/SOURCE.md"
printf 'razer-cfg255\n' | install -Dm644 /dev/stdin "$K/usr/lib/modules-load.d/razer-cfg255.conf"
cat > "$K/DEBIAN/control" <<CTRL
Package: razer-cfg255-dkms
Version: 1.0
Section: kernel
Priority: optional
Architecture: all
Maintainer: Donnie Herd <75598735+wizardofth3moon@users.noreply.github.com>
Depends: dkms
Description: Enables the Razer BlackShark V3 command channel on Linux (DKMS)
 A kretprobe module by Charles D'Aoust that makes Linux's first configuration-
 descriptor read 255 bytes long for the BlackShark V3 family, as Windows does;
 without it the headset never answers battery or settings commands. Interim
 until the USB_QUIRK_CONFIG_DESC_READ_255 quirk is in the kernel. Replug the
 dongle after installing.
CTRL
cat > "$K/DEBIAN/postinst" <<'SH'
#!/bin/sh
set -e
if [ "$1" = configure ]; then
    dkms add -m razer-cfg255 -v 1.0 >/dev/null 2>&1 || true
    dkms install -m razer-cfg255 -v 1.0 || true
    modprobe razer-cfg255 2>/dev/null || true
fi
SH
cat > "$K/DEBIAN/prerm" <<'SH'
#!/bin/sh
set -e
modprobe -r razer-cfg255 2>/dev/null || true
dkms remove -m razer-cfg255 -v 1.0 --all >/dev/null 2>&1 || true
SH
chmod 755 "$K/DEBIAN/postinst" "$K/DEBIAN/prerm"
dpkg-deb --root-owner-group --build "$K" "$OUT/razer-cfg255-dkms_1.0_all.deb" >/dev/null
ls -la "$OUT"
