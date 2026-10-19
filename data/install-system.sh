#!/bin/sh
# Peripheral Studio's one-time system setup (run with sudo). Safe to run again.
set -e
here=$(dirname "$(readlink -f "$0")")
install -m 644 "$here/udev/70-peripheral-studio.rules" /etc/udev/rules.d/70-peripheral-studio.rules
# uhid (virtual battery devices for the desktop's power menu) and uinput (the remap engine's
# virtual keyboard/mouse): loaded at boot, so udev can give the user access to them
printf 'uhid\nuinput\n' > /etc/modules-load.d/peripheral-studio.conf
modprobe uhid
modprobe uinput
udevadm control --reload
udevadm trigger
udevadm trigger --action=add /sys/class/misc/uhid /sys/class/misc/uinput 2>/dev/null || true
echo "udev rule installed"
