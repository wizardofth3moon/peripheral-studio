"""The virtual keyboard and mouse the engine types and clicks with."""
from __future__ import annotations

import asyncio

import evdev
from evdev import ecodes as e

# US layout: character -> (key, shift)
_US_SHIFTED = {'!': '1', '@': '2', '#': '3', '$': '4', '%': '5', '^': '6', '&': '7', '*': '8', '(': '9',
               ')': '0', '_': 'MINUS', '+': 'EQUAL', '{': 'LEFTBRACE', '}': 'RIGHTBRACE', '|': 'BACKSLASH',
               ':': 'SEMICOLON', '"': 'APOSTROPHE', '<': 'COMMA', '>': 'DOT', '?': 'SLASH', '~': 'GRAVE'}
_US_PLAIN = {' ': 'SPACE', '-': 'MINUS', '=': 'EQUAL', '[': 'LEFTBRACE', ']': 'RIGHTBRACE', '\\': 'BACKSLASH',
             ';': 'SEMICOLON', "'": 'APOSTROPHE', ',': 'COMMA', '.': 'DOT', '/': 'SLASH', '`': 'GRAVE',
             '\n': 'ENTER', '\t': 'TAB'}


def char_to_key(ch: str) -> tuple[int, bool] | None:
    if ch in _US_PLAIN:
        return e.ecodes[f"KEY_{_US_PLAIN[ch]}"], False
    if ch in _US_SHIFTED:
        return e.ecodes[f"KEY_{_US_SHIFTED[ch]}"], True
    if ch.isascii() and ch.isalnum():
        return e.ecodes[f"KEY_{ch.upper()}"], ch.isupper()
    return None


class Output:
    """Two uinput devices so libinput classifies them properly: one keyboard, one mouse.

    Tracks what it holds down, so a stopped macro or a vanished device never leaves a key stuck.
    """

    MOUSE_BUTTONS = [e.BTN_LEFT, e.BTN_RIGHT, e.BTN_MIDDLE, e.BTN_SIDE, e.BTN_EXTRA, e.BTN_FORWARD,
                     e.BTN_BACK, e.BTN_TASK]

    def __init__(self):
        keys = [c for c in range(e.KEY_ESC, e.KEY_MAX) if c not in range(e.BTN_MISC, e.KEY_OK)]
        self.keyboard = evdev.UInput({e.EV_KEY: keys}, name="Peripheral Studio keyboard", vendor=0x1532,
                                     product=0x5053)
        self.mouse = evdev.UInput({e.EV_KEY: self.MOUSE_BUTTONS,
                                   e.EV_REL: [e.REL_X, e.REL_Y, e.REL_WHEEL, e.REL_HWHEEL,
                                              e.REL_WHEEL_HI_RES, e.REL_HWHEEL_HI_RES]},
                                  name="Peripheral Studio mouse", vendor=0x1532, product=0x5054)
        self.held: dict[int, int] = {}  # code -> number of holders

    def _dev(self, code: int):
        return self.mouse if code in self.MOUSE_BUTTONS else self.keyboard

    def down(self, code: int) -> None:
        n = self.held.get(code, 0)
        self.held[code] = n + 1
        if n == 0:
            dev = self._dev(code)
            dev.write(e.EV_KEY, code, 1)
            dev.syn()

    def up(self, code: int) -> None:
        n = self.held.get(code, 0)
        if n <= 1:
            self.held.pop(code, None)
            if n:
                dev = self._dev(code)
                dev.write(e.EV_KEY, code, 0)
                dev.syn()
        else:
            self.held[code] = n - 1

    def tap(self, code: int) -> None:
        self.down(code)
        self.up(code)

    def scroll(self, axis: str, amount: int) -> None:
        rel, hi = (e.REL_WHEEL, e.REL_WHEEL_HI_RES) if axis == "vertical" else (e.REL_HWHEEL, e.REL_HWHEEL_HI_RES)
        self.mouse.write(e.EV_REL, rel, amount)
        self.mouse.write(e.EV_REL, hi, amount * 120)
        self.mouse.syn()

    async def type_text(self, text: str, delay: float = 0.004) -> None:
        for ch in text:
            mapped = char_to_key(ch)
            if not mapped:
                continue
            code, shift = mapped
            if shift:
                self.down(e.KEY_LEFTSHIFT)
            self.tap(code)
            if shift:
                self.up(e.KEY_LEFTSHIFT)
            await asyncio.sleep(delay)

    def release_all(self) -> None:
        for code in list(self.held):
            self.held[code] = 1
            self.up(code)

    def close(self) -> None:
        self.release_all()
        self.keyboard.close()
        self.mouse.close()
