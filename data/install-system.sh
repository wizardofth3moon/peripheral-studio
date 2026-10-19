#!/bin/sh
# Peripheral Studio's one-time system setup (run with sudo). Safe to run again.
set -e
here=$(dirname "$(readlink -f "$0")")
install -m 644 "$here/udev/70-peripheral-studio.rules" /etc/udev/rules.d/70-peripheral-studio.rules
udevadm control --reload
udevadm trigger
echo "udev rule installed"
