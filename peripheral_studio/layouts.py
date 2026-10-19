"""Button diagrams per device model.

Positions are grid cells (column, row, width, height) in quarter-key units so keys can be
1.25u, 1.5u wide and so on. Each button names the evdev input it sends by default.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from evdev import ecodes as e

KEY, REL = e.EV_KEY, e.EV_REL


@dataclass(frozen=True)
class Button:
    id: str
    label: str
    ev_type: int
    code: int
    col: float
    row: float
    w: float = 4
    h: float = 4
    # For wheels: +1 / -1 picks the direction (input-remapper's analog_threshold).
    threshold: int | None = None
    caption: str = ""  # what it does by default, shown under the label
    short: str = ""  # label on the diagram when the full one doesn't fit
    # Set for buttons the device handles itself (e.g. DPI): shown on the diagram, not bindable yet.
    hardware: str = ""

    @property
    def input_key(self) -> tuple[int, int, int | None]:
        return (self.ev_type, self.code, self.threshold)


@dataclass
class Section:
    title: str
    buttons: list[Button] = field(default_factory=list)
    column: int = 0  # sections in the same column stack; columns sit side by side
    # Drawn behind the buttons: an SVG in data/devices/ sized (width, height) in grid units,
    # or "case" for a plain rounded case around the keys.
    silhouette: str | None = None
    size: tuple[float, float] | None = None
    cell: float | None = None  # overrides Layout.cell for this section


@dataclass
class Layout:
    model: str
    sections: list[Section]
    cell: float = 15  # pixels per grid unit

    def all_buttons(self) -> list[Button]:
        return [b for s in self.sections for b in s.buttons]

    def find(self, ev_type: int, code: int, threshold: int | None, mods: tuple = ()) -> Button | None:
        if mods:
            return None  # chords only come from learned buttons
        for b in self.all_buttons():
            if not b.hardware and b.input_key == (ev_type, code, threshold):
                return b
        return None


def _grid(prefix: str, rows: list[list[tuple[str, int, str]]], start: int = 1, x0: int = 0, y0: int = 0) -> list[Button]:
    out, n = [], start
    for r, row in enumerate(rows):
        for c, (caption, code, _) in enumerate(row):
            out.append(Button(f"{prefix}{n:02d}", f"{n:02d}", KEY, code, x0 + c * 4, y0 + r * 4, caption=caption))
            n += 1
    return out


def _tartarus_v2() -> Layout:
    # Positions in pixels of the reference photo the silhouette was traced from (origin 60, 31).
    centres = [
        (85.5, 65), (108.5, 59), (131.5, 55), (155.5, 54), (180.5, 53),
        (90.5, 86), (110.5, 79), (134.5, 76), (157.5, 74), (180.5, 74),
        (91.5, 108), (113.5, 101), (136.5, 99), (158.5, 97), (181.5, 96),
        (95.5, 130), (116.5, 122), (138.5, 120), (159.5, 119),
    ]
    defaults = [("1", e.KEY_1), ("2", e.KEY_2), ("3", e.KEY_3), ("4", e.KEY_4), ("5", e.KEY_5),
                ("Tab", e.KEY_TAB), ("Q", e.KEY_Q), ("W", e.KEY_W), ("E", e.KEY_E), ("R", e.KEY_R),
                ("Caps", e.KEY_CAPSLOCK), ("A", e.KEY_A), ("S", e.KEY_S), ("D", e.KEY_D), ("F", e.KEY_F),
                ("Shift", e.KEY_LEFTSHIFT), ("Z", e.KEY_Z), ("X", e.KEY_X), ("C", e.KEY_C)]
    ox, oy = 60, 31
    buttons = [Button(f"key{n + 1:02d}", f"{n + 1:02d}", KEY, code, cx - 10 - ox, cy - 9 - oy, 20, 18, caption=caption)
               for n, ((cx, cy), (caption, code)) in enumerate(zip(centres, defaults))]
    buttons += [
        Button("wheel_up", "Scroll up", REL, e.REL_WHEEL, 174 - ox, 114 - oy, 15, 10, threshold=1, short="▲"),
        Button("wheel_click", "Wheel click", KEY, e.BTN_MIDDLE, 174 - ox, 124 - oy, 15, 12, short="●"),
        Button("wheel_down", "Scroll down", REL, e.REL_WHEEL, 174 - ox, 136 - oy, 15, 10, threshold=-1, short="▼"),
        Button("alt", "Upper thumb button", KEY, e.KEY_LEFTALT, 203.5 - ox, 133.5 - oy, 16, 15, short="Alt"),
        Button("dpad_up", "Thumbpad up", KEY, e.KEY_UP, 220 - ox, 146 - oy, 13, 10, short="↑"),
        Button("dpad_left", "Thumbpad left", KEY, e.KEY_LEFT, 212 - ox, 154.5 - oy, 10, 13, short="←"),
        Button("dpad_right", "Thumbpad right", KEY, e.KEY_RIGHT, 231 - ox, 154.5 - oy, 10, 13, short="→"),
        Button("dpad_down", "Thumbpad down", KEY, e.KEY_DOWN, 220 - ox, 166 - oy, 13, 10, short="↓"),
        Button("space", "Key 20 (thumb paddle)", KEY, e.KEY_SPACE, 208 - ox, 186 - oy, 27, 15, short="20",
               caption="Space"),
    ]
    return Layout("Razer Tartarus V2", [Section("Tartarus V2", buttons, silhouette="tartarus-v2.svg", size=(186, 246))],
                  cell=2.4)


def _naga_v2_hyperspeed() -> Layout:
    # Side grid: 4 columns x 3 rows, numbered down each column from the front (1-3 nearest the
    # buttons, 3 on top). Centres are pixels in the side reference photo (origin 130, 296).
    centres = {3: (458, 428), 2: (472, 503), 1: (487, 577), 6: (532, 410), 5: (548, 488), 4: (566, 570),
               9: (602, 405), 8: (618, 483), 7: (636, 563), 12: (672, 403), 11: (692, 477), 10: (710, 553)}
    codes = [e.KEY_1, e.KEY_2, e.KEY_3, e.KEY_4, e.KEY_5, e.KEY_6, e.KEY_7, e.KEY_8, e.KEY_9, e.KEY_0,
             e.KEY_MINUS, e.KEY_EQUAL]
    captions = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="]
    side = [Button(f"side{n:02d}", f"{n}", KEY, codes[n - 1], centres[n][0] - 31 - 130, centres[n][1] - 34 - 296,
                   62, 68, caption=captions[n - 1]) for n in range(1, 13)]
    # Top view, pixels in the top reference photo.
    top = [
        Button("left", "Left click", KEY, e.BTN_LEFT, 50, 22, 56, 150, short="L", caption=""),
        Button("right", "Right click", KEY, e.BTN_RIGHT, 210, 22, 70, 150, short="R", caption=""),
        Button("wheel_up", "Scroll up", REL, e.REL_WHEEL, 140, 74, 34, 36, threshold=1, short="▲"),
        Button("middle", "Wheel click", KEY, e.BTN_MIDDLE, 140, 110, 34, 36, short="●"),
        Button("wheel_down", "Scroll down", REL, e.REL_WHEEL, 140, 146, 34, 36, threshold=-1, short="▼"),
        Button("tilt_left", "Wheel tilt left", REL, e.REL_HWHEEL, 106, 106, 32, 38, threshold=-1, short="‹"),
        Button("tilt_right", "Wheel tilt right", REL, e.REL_HWHEEL, 176, 106, 32, 38, threshold=1, short="›"),
        # Behind the wheel: switches the wheel between notched and free-spinning, in the mouse itself.
        Button("scroll_mode", "Scroll mode", KEY, 0, 140, 214, 32, 40, short="⟳", hardware="scroll_mode"),
        # DPI rocker: Razer keys 32 and 33, sent in Razer's own report 4 (bindable with native HID).
        Button("dpi_up", "DPI up", KEY, 0, 16, 30, 38, 56, short="+", hardware="dpi"),
        Button("dpi_down", "DPI down", KEY, 0, 16, 86, 38, 56, short="−", hardware="dpi"),
    ]
    return Layout("Razer Naga V2 HyperSpeed", [
        Section("Top", top, silhouette="naga-v2-top.svg", size=(349, 553), cell=0.64),
        Section("Side buttons", side, column=1, silhouette="naga-v2-side.svg", size=(860, 356), cell=0.5),
    ])


def _ansi_keyboard(model: str) -> Layout:
    # (caption, code, width in quarter units)
    rows = [
        [("Esc", e.KEY_ESC, 4), ("", 0, 4), ("F1", e.KEY_F1, 4), ("F2", e.KEY_F2, 4), ("F3", e.KEY_F3, 4), ("F4", e.KEY_F4, 4),
         ("", 0, 2), ("F5", e.KEY_F5, 4), ("F6", e.KEY_F6, 4), ("F7", e.KEY_F7, 4), ("F8", e.KEY_F8, 4),
         ("", 0, 2), ("F9", e.KEY_F9, 4), ("F10", e.KEY_F10, 4), ("F11", e.KEY_F11, 4), ("F12", e.KEY_F12, 4)],
        [("`", e.KEY_GRAVE, 4), ("1", e.KEY_1, 4), ("2", e.KEY_2, 4), ("3", e.KEY_3, 4), ("4", e.KEY_4, 4), ("5", e.KEY_5, 4),
         ("6", e.KEY_6, 4), ("7", e.KEY_7, 4), ("8", e.KEY_8, 4), ("9", e.KEY_9, 4), ("0", e.KEY_0, 4), ("-", e.KEY_MINUS, 4),
         ("=", e.KEY_EQUAL, 4), ("Backspace", e.KEY_BACKSPACE, 8)],
        [("Tab", e.KEY_TAB, 6), ("Q", e.KEY_Q, 4), ("W", e.KEY_W, 4), ("E", e.KEY_E, 4), ("R", e.KEY_R, 4), ("T", e.KEY_T, 4),
         ("Y", e.KEY_Y, 4), ("U", e.KEY_U, 4), ("I", e.KEY_I, 4), ("O", e.KEY_O, 4), ("P", e.KEY_P, 4), ("[", e.KEY_LEFTBRACE, 4),
         ("]", e.KEY_RIGHTBRACE, 4), ("\\", e.KEY_BACKSLASH, 6)],
        [("Caps", e.KEY_CAPSLOCK, 7), ("A", e.KEY_A, 4), ("S", e.KEY_S, 4), ("D", e.KEY_D, 4), ("F", e.KEY_F, 4), ("G", e.KEY_G, 4),
         ("H", e.KEY_H, 4), ("J", e.KEY_J, 4), ("K", e.KEY_K, 4), ("L", e.KEY_L, 4), (";", e.KEY_SEMICOLON, 4),
         ("'", e.KEY_APOSTROPHE, 4), ("Enter", e.KEY_ENTER, 9)],
        [("Shift", e.KEY_LEFTSHIFT, 9), ("Z", e.KEY_Z, 4), ("X", e.KEY_X, 4), ("C", e.KEY_C, 4), ("V", e.KEY_V, 4), ("B", e.KEY_B, 4),
         ("N", e.KEY_N, 4), ("M", e.KEY_M, 4), (",", e.KEY_COMMA, 4), (".", e.KEY_DOT, 4), ("/", e.KEY_SLASH, 4),
         ("Shift", e.KEY_RIGHTSHIFT, 11)],
        [("Ctrl", e.KEY_LEFTCTRL, 5), ("Super", e.KEY_LEFTMETA, 5), ("Alt", e.KEY_LEFTALT, 5), ("Space", e.KEY_SPACE, 25),
         ("Alt", e.KEY_RIGHTALT, 5), ("Menu", e.KEY_COMPOSE, 5), ("Ctrl", e.KEY_RIGHTCTRL, 10)],
    ]
    main = []
    for r, row in enumerate(rows):
        x = 0
        for caption, code, w in row:
            if code:
                name = e.KEY[code] if isinstance(e.KEY[code], str) else e.KEY[code][0]
                main.append(Button(name.lower(), caption, KEY, code, x, r * 4 + (1 if r else 0), w, caption=""))
            x += w
    nav_keys = [
        ("Ins", e.KEY_INSERT, 0, 0), ("Home", e.KEY_HOME, 4, 0), ("PgUp", e.KEY_PAGEUP, 8, 0),
        ("Del", e.KEY_DELETE, 0, 4), ("End", e.KEY_END, 4, 4), ("PgDn", e.KEY_PAGEDOWN, 8, 4),
        ("↑", e.KEY_UP, 4, 12), ("←", e.KEY_LEFT, 0, 16), ("↓", e.KEY_DOWN, 4, 16), ("→", e.KEY_RIGHT, 8, 16),
    ]
    nav = [Button(f"nav_{e.KEY[c].lower()}", cap, KEY, c, x, y) for cap, c, x, y in nav_keys]
    return Layout(model, [Section("Keyboard", main, silhouette="case"), Section("Navigation", nav, silhouette="case")], cell=11)


def _blackshark_v3() -> Layout:
    return Layout("Razer BlackShark V3", [Section("Headset controls", [
        Button("mic_mute", "Mic mute", KEY, e.KEY_MICMUTE, 0, 0, 8, caption="Mute mic"),
        Button("vol_up", "Volume up", KEY, e.KEY_VOLUMEUP, 8, 0, 8, caption="Vol +"),
        Button("vol_down", "Volume down", KEY, e.KEY_VOLUMEDOWN, 16, 0, 8, caption="Vol −"),
        Button("play", "Play / pause", KEY, e.KEY_PLAYPAUSE, 0, 4, 8, caption="Play"),
        Button("next", "Next track", KEY, e.KEY_NEXTSONG, 8, 4, 8, caption="Next"),
        Button("prev", "Previous track", KEY, e.KEY_PREVIOUSSONG, 16, 4, 8, caption="Prev"),
    ])])


_MODELS = {
    "tartarus v2": _tartarus_v2,
    "naga v2 hyperspeed": _naga_v2_hyperspeed,
    "ornata v3": lambda: _ansi_keyboard("Razer Ornata V3"),
    "blackshark v3": _blackshark_v3,
}


def for_device(name: str) -> Layout | None:
    low = name.lower()
    for key, build in _MODELS.items():
        if key in low:
            return build()
    return None
