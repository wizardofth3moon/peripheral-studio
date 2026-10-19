# Razer settings protocol (as used by `peripheral_studio/hardware/`)

Peripheral Studio talks to Razer devices itself, with no driver or daemon. The command values
below come from reading the OpenRazer driver (GPL-2.0, https://github.com/openrazer/openrazer),
version 3.12.4. Thanks to its authors for working them out.

## Transport

- Every command is one 90-byte report, sent as a HID **feature report** (report id 0) with
  `HIDIOCSFEATURE` on the device's hidraw node. After a short wait, the answer is read back with
  `HIDIOCGFEATURE`.
- The hidraw node is chosen by **USB interface**: OpenRazer sends to `wIndex` = interface 0
  (Naga V2 HyperSpeed receiver) or 1 (Tartarus V2, Ornata V3 X).
- Wait between send and read: about 31 ms for the mouse receiver, which relays to the mouse;
  about 0.8 ms for the keyboards.

| Offset | Field |
| --- | --- |
| 0 | status: 0 new; replies 1 busy, 2 ok, 3 failed, 4 no answer (e.g. mouse asleep), 5 not supported |
| 1 | transaction id: 0x1F for most commands; 0xFF for some info/LED commands; 0x3F for Tartarus V2 breathing |
| 2–3 | remaining packets (0) |
| 4 | protocol type (0) |
| 5 | data size |
| 6 / 7 | command class / command id (bit 7 of the id set = read) |
| 8–87 | arguments |
| 88 | CRC: XOR of bytes 2–87 |
| 89 | reserved |

## Commands

| What | Class / id / size | Arguments |
| --- | --- | --- |
| Firmware | 00 / 81 / 02 | reply `v{a0}.{a1}` |
| Serial | 00 / 82 / 16 | reply ASCII |
| Lighting effect | 0F / 02 / 06–0C | `[store 01, led 05, effect, …]`: none 00, static 01 `[.., 0, 0, 1, R, G, B]`, breathing 02 (random / `[1,0,1,RGB]` / `[2,0,2,RGB,RGB]`), spectrum 03, wave 04 `[dir, 0x28]`, reactive 05 `[0, speed 1–4, 1, RGB]`, starlight 07 `[0, speed 1–3, n, RGB…]` |
| Brightness | 0F / 04 (set), 84 (get) / 03 | `[01, led, 0–255]`; led 00 on the Tartarus V2, 05 on the Ornata V3 X |
| Profile LEDs (Tartarus V2) | 03 / 00 (set), 80 (get) / 03 | `[01, 0C red \| 0D green \| 0E blue, on]` |
| DPI | 04 / 05 (set), 85 (get) / 07 | `[01, Xhi, Xlo, Yhi, Ylo, 0, 0]` |
| DPI stages | 04 / 86 / 26 | reply `[01, active, count, (n, Xhi, Xlo, Yhi, Ylo, 0, 0)×count]` |
| Polling rate | 00 / 05 (set), 85 (get) / 01 | 01 = 1000 Hz, 02 = 500 Hz, 08 = 125 Hz |
| Battery | 07 / 80 / 02 | reply a1 = 0–255 |
| Sleep timer | 07 / 03 (set), 83 (get) / 02 | seconds, big-endian, 60–900 |
| Low-battery warning | 07 / 01 (set, tid FF), 81 (get) / 01 | 0x0C–0x3F of 255 |
