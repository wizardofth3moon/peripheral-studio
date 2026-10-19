# razer-cfg255 (third-party, GPL-2.0)

By Charles D'Aoust: https://git.forked.io/charles/razer-blackshark-v3-quirk (module/, branch master).
Copied unchanged, 2026-10-19. A tiny kretprobe module that makes Linux's first
configuration-descriptor read 255 bytes long for the BlackShark V3 family (as Windows does),
which the headset needs to enable its battery/command channel. Interim until the usbcore
quirk USB_QUIRK_CONFIG_DESC_READ_255 lands upstream; then this can be removed.

Install: sudo cp -r . /usr/src/razer-cfg255-1.0 && sudo dkms install razer-cfg255/1.0 &&
         sudo modprobe razer-cfg255 && echo razer-cfg255 | sudo tee /etc/modules-load.d/razer-cfg255.conf
Then unplug the dongle and plug it back in.
