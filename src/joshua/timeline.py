"""The input language an agent speaks: a list of timed segments."""

from dataclasses import dataclass

from . import JoshuaError

MAX_SEGMENTS = 60
MAX_TOTAL_MS = 15000

# name -> (scan code, needs the extended-key flag)
KEYS = {
    "esc": (0x01, False), "1": (0x02, False), "2": (0x03, False), "3": (0x04, False),
    "4": (0x05, False), "5": (0x06, False), "6": (0x07, False), "7": (0x08, False),
    "8": (0x09, False), "9": (0x0A, False), "0": (0x0B, False), "minus": (0x0C, False),
    "equals": (0x0D, False), "backspace": (0x0E, False), "tab": (0x0F, False),
    **{letter: (code, False) for letter, code in zip("qwertyuiop", range(0x10, 0x1A))},
    "leftbracket": (0x1A, False), "rightbracket": (0x1B, False), "enter": (0x1C, False),
    "ctrl": (0x1D, False),
    **{letter: (code, False) for letter, code in zip("asdfghjkl", range(0x1E, 0x27))},
    "semicolon": (0x27, False), "apostrophe": (0x28, False), "grave": (0x29, False),
    "shift": (0x2A, False), "backslash": (0x2B, False),
    **{letter: (code, False) for letter, code in zip("zxcvbnm", range(0x2C, 0x33))},
    "comma": (0x33, False), "period": (0x34, False), "slash": (0x35, False),
    "rightshift": (0x36, False), "numpad_multiply": (0x37, False), "alt": (0x38, False),
    "space": (0x39, False), "capslock": (0x3A, False),
    **{f"f{n}": (0x3A + n, False) for n in range(1, 11)},
    "numpad7": (0x47, False), "numpad8": (0x48, False), "numpad9": (0x49, False),
    "numpad_subtract": (0x4A, False), "numpad4": (0x4B, False), "numpad5": (0x4C, False),
    "numpad6": (0x4D, False), "numpad_add": (0x4E, False), "numpad1": (0x4F, False),
    "numpad2": (0x50, False), "numpad3": (0x51, False), "numpad0": (0x52, False),
    "numpad_decimal": (0x53, False), "f11": (0x57, False), "f12": (0x58, False),
    "numpad_enter": (0x1C, True), "rightctrl": (0x1D, True), "numpad_divide": (0x35, True),
    "rightalt": (0x38, True), "home": (0x47, True), "up": (0x48, True), "pageup": (0x49, True),
    "left": (0x4B, True), "right": (0x4D, True), "end": (0x4F, True), "down": (0x50, True),
    "pagedown": (0x51, True), "insert": (0x52, True), "delete": (0x53, True),
    "pause": (0x45, False),
}

# Keys Windows only understands by virtual-key code: Pause's hardware sequence has
# no single scan code, and 0x45 on its own reads as Num Lock.
VIRTUAL_KEYS = {"pause": 0x13}

# Combinations that would leave the game for the desktop, or trip the abort chord.
_ALT = {"alt", "rightalt"}
_CTRL = {"ctrl", "rightctrl"}
_FORBIDDEN = ((_ALT, {"tab"}), (_ALT, {"esc"}), (_CTRL, {"esc"}), (_ALT, {"space"}))
BUTTONS = ("left", "right", "middle")


@dataclass(frozen=True)
class Segment:
    ms: int
    keys: tuple = ()
    buttons: tuple = ()
    mouse_to: tuple | None = None
    mouse_move: tuple | None = None


def _names(value, allowed, field, index):
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise JoshuaError(f"segment {index}: {field} must be a list of names")
    names = []
    for item in value:
        name = item.strip().lower()
        if name not in allowed:
            hint = ", ".join(sorted(allowed))
            raise JoshuaError(f"segment {index}: unknown name {item!r} in {field}. Known: {hint}")
        if name not in names:
            names.append(name)
    return tuple(names)


def _pair(value, field, index):
    if value is None:
        return None
    if (not isinstance(value, list) or len(value) != 2
            or not all(isinstance(n, (int, float)) and not isinstance(n, bool) for n in value)):
        raise JoshuaError(f"segment {index}: {field} must be [x, y]")
    if not all(abs(n) <= 100000 for n in value):
        raise JoshuaError(f"segment {index}: {field} is out of range")
    return (round(value[0]), round(value[1]))


def parse(raw):
    """Validate the agent's segments before anything touches the keyboard."""
    if not isinstance(raw, list) or not raw:
        raise JoshuaError("segments must be a non-empty list")
    if len(raw) > MAX_SEGMENTS:
        raise JoshuaError(f"at most {MAX_SEGMENTS} segments per step")
    segments = []
    total = 0
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise JoshuaError(f"segment {index} must be an object")
        extra = set(item) - {"ms", "keys", "buttons", "mouse_to", "mouse_move"}
        if extra:
            raise JoshuaError(f"segment {index}: unknown fields {sorted(extra)}")
        ms = item.get("ms")
        if not isinstance(ms, int) or isinstance(ms, bool) or not 10 <= ms <= MAX_TOTAL_MS:
            raise JoshuaError(f"segment {index}: ms must be an integer from 10 to {MAX_TOTAL_MS}")
        keys = _names(item.get("keys"), KEYS, "keys", index)
        for modifiers, others in _FORBIDDEN:
            if modifiers & set(keys) and others & set(keys):
                raise JoshuaError(f"segment {index}: that key combination would leave the game")
        if _ALT & set(keys) and _CTRL & set(keys) and "f12" in keys:
            raise JoshuaError(f"segment {index}: Ctrl+Alt+F12 is reserved for the user's abort")
        segments.append(Segment(
            ms=ms, keys=keys,
            buttons=_names(item.get("buttons"), BUTTONS, "buttons", index),
            mouse_to=_pair(item.get("mouse_to"), "mouse_to", index),
            mouse_move=_pair(item.get("mouse_move"), "mouse_move", index),
        ))
        total += ms
    if total > MAX_TOTAL_MS:
        raise JoshuaError(f"a step may last at most {MAX_TOTAL_MS} ms; this one is {total} ms")
    return segments
