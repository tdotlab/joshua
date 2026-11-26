"""Playing a timeline of keyboard and mouse input into the focused game."""

import ctypes
import time
from ctypes import wintypes as w

from . import JoshuaError
from . import win32 as api
from .timeline import KEYS, VIRTUAL_KEYS

_SLICE = 0.01


class UserActivity:
    """Tells the user's own input apart from ours, using the system's last-input time."""

    def __init__(self):
        self._ours = None

    def mark(self):
        self._ours = api.GetTickCount()

    def idle_ms(self):
        """Milliseconds since input we did not send, or None if the latest input was ours."""
        info = api.LASTINPUTINFO(ctypes.sizeof(api.LASTINPUTINFO), 0)
        api.GetLastInputInfo(ctypes.byref(info))
        if self._ours is not None:
            newer = (info.dwTime - self._ours) & 0xFFFFFFFF
            if newer <= 50 or newer >= 0x80000000:
                return None
        return (api.GetTickCount() - info.dwTime) & 0xFFFFFFFF


def _send(*events):
    array = (api.INPUT * len(events))(*events)
    if api.SendInput(len(events), array, ctypes.sizeof(api.INPUT)) != len(events):
        raise JoshuaError("Windows blocked the input; is the game running as administrator?")


def key_event(name, down):
    scan, extended = KEYS[name]
    virtual = VIRTUAL_KEYS.get(name, 0)
    flags = 0 if virtual else api.KEYEVENTF_SCANCODE
    if extended:
        flags |= api.KEYEVENTF_EXTENDEDKEY
    if not down:
        flags |= api.KEYEVENTF_KEYUP
    event = api.INPUT(type=api.INPUT_KEYBOARD)
    event.ki = api.KEYBDINPUT(virtual, scan, flags, 0, 0)
    return event


def mouse_event(flags, dx=0, dy=0):
    event = api.INPUT(type=api.INPUT_MOUSE)
    event.mi = api.MOUSEINPUT(dx, dy, 0, flags, 0, 0)
    return event


def nudge(activity):
    """Send an empty mouse event: no effect anywhere, but it counts as our input."""
    _send(mouse_event(0))
    activity.mark()


def abort_requested():
    return all(api.GetAsyncKeyState(key) & 0x8000
               for key in (api.VK_CONTROL, api.VK_MENU, api.VK_F12))


class Player:
    """Sends input while a guard confirms that the game still has the floor."""

    def __init__(self, activity, guard, to_screen, over_game):
        self._activity = activity
        self._guard = guard          # () -> reason to stop, or None
        self._to_screen = to_screen  # (x, y) in screenshot pixels -> screen pixels
        self._over_game = over_game  # () -> whether the pointer is over the game
        self._keys = []
        self._buttons = []
        self.sent_any = False

    def _emit(self, *events):
        _send(*events)
        self.sent_any = True
        self._activity.mark()

    def _release(self, keys, buttons):
        for button in list(buttons):
            self._emit(mouse_event(api.MOUSE_BUTTON_FLAGS[button][1]))
            self._buttons.remove(button)
        for name in list(keys):
            self._emit(key_event(name, False))
            self._keys.remove(name)

    def release_all(self):
        self._release(list(reversed(self._keys)), list(self._buttons))

    def _begin(self, segment):
        self._release([k for k in self._keys if k not in segment.keys],
                      [b for b in self._buttons if b not in segment.buttons])
        if segment.mouse_to is not None:
            point = self._to_screen(segment.mouse_to)
            api.SetCursorPos(*point)
            # A real movement event, for games that ignore cursor warps.
            self._emit(mouse_event(api.MOUSEEVENTF_MOVE, 0, 0))
        for name in segment.keys:
            if name not in self._keys:
                self._emit(key_event(name, True))
                self._keys.append(name)
        for button in segment.buttons:
            if button not in self._buttons:
                if not self._over_game():
                    raise JoshuaError("the mouse pointer is not over the game, so no button was pressed;"
                                           " set mouse_to first")
                self._emit(mouse_event(api.MOUSE_BUTTON_FLAGS[button][0]))
                self._buttons.append(button)

    def play(self, segments):
        """Play the segments. Returns (milliseconds played, reason it stopped early or None).

        Everything held is released before returning, on every path.
        """
        started = time.perf_counter()
        reason = None
        try:
            for segment in segments:
                reason = self._guard()
                if reason:
                    break
                self._begin(segment)
                reason = self._hold(segment)
                if reason:
                    break
        finally:
            played = round((time.perf_counter() - started) * 1000)
            self.release_all()
        return played, reason

    def _hold(self, segment):
        begin = time.perf_counter()
        length = segment.ms / 1000
        sent_x = sent_y = 0
        while True:
            reason = self._guard()
            if reason:
                return reason
            progress = min(1.0, (time.perf_counter() - begin) / length)
            if segment.mouse_move:
                # Spread relative motion over the segment, as a hand would.
                want_x = round(segment.mouse_move[0] * progress)
                want_y = round(segment.mouse_move[1] * progress)
                if want_x != sent_x or want_y != sent_y:
                    self._emit(mouse_event(api.MOUSEEVENTF_MOVE, want_x - sent_x, want_y - sent_y))
                    sent_x, sent_y = want_x, want_y
            if progress >= 1.0:
                return None
            time.sleep(min(_SLICE, max(0.0, length - (time.perf_counter() - begin))))


def pointer():
    point = w.POINT()
    api.GetCursorPos(ctypes.byref(point))
    return point
