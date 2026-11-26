"""Finding, focusing and inspecting the game's window."""

import ctypes
import time
from ctypes import wintypes as w

from . import win32 as api

# Deliberately vague: another window's title can be private, and this goes to the model.
COVERED = "another window is over it"


def client_area(hwnd):
    """The window's drawable area as (left, top, width, height) in screen pixels."""
    rect = w.RECT()
    origin = w.POINT(0, 0)
    if not api.GetClientRect(hwnd, ctypes.byref(rect)) or not api.ClientToScreen(hwnd, ctypes.byref(origin)):
        return None
    return (origin.x, origin.y, rect.right - rect.left, rect.bottom - rect.top)


def dpi(hwnd):
    """The DPI of the display showing most of a window.

    Asked of the display, since a window unaware of scaling reports 96 whatever it is.
    """
    across, down = w.UINT(), w.UINT()
    monitor = api.MonitorFromWindow(hwnd, api.MONITOR_DEFAULTTONEAREST)
    if api.GetDpiForMonitor(monitor, api.MDT_EFFECTIVE_DPI, ctypes.byref(across), ctypes.byref(down)) != 0:
        return 96
    return across.value


def owner(hwnd):
    pid = w.DWORD()
    api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def title(hwnd):
    buffer = ctypes.create_unicode_buffer(512)
    api.GetWindowTextW(hwnd, buffer, len(buffer))
    return buffer.value


def find(pid, title_contains=None):
    """The largest visible top-level window of a process, or None."""
    candidates = []

    def visit(hwnd, _):
        if owner(hwnd) == pid and api.IsWindowVisible(hwnd):
            if not title_contains or title_contains.lower() in title(hwnd).lower():
                area = client_area(hwnd)
                size = area[2] * area[3] if area else 0
                if size or api.IsIconic(hwnd):
                    candidates.append((size, hwnd))
        return True

    api.EnumWindows(api.EnumWindowsProc(visit), 0)
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def is_foreground(pid):
    front = api.GetForegroundWindow()
    return bool(front) and owner(front) == pid


def _wait_foreground(pid, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if is_foreground(pid):
            return True
        time.sleep(0.02)
    return is_foreground(pid)


def focus(hwnd, pid, nudge):
    """Bring the game forward. Windows may refuse; the result says what happened.

    nudge() must send an input event that does nothing, which makes this process
    eligible to change the foreground window.
    """
    if api.IsIconic(hwnd):
        api.ShowWindow(hwnd, api.SW_RESTORE)
    if is_foreground(pid):
        return True
    api.SetForegroundWindow(hwnd)
    if _wait_foreground(pid, 0.3):
        return True
    # Share the current foreground thread's input state, which lifts the restriction.
    front = api.GetForegroundWindow()
    ours = api.GetCurrentThreadId()
    theirs = api.GetWindowThreadProcessId(front, None) if front else 0
    if theirs and api.AttachThreadInput(ours, theirs, True):
        try:
            api.BringWindowToTop(hwnd)
            api.SetForegroundWindow(hwnd)
        finally:
            api.AttachThreadInput(ours, theirs, False)
    if _wait_foreground(pid, 0.3):
        return True
    nudge()
    api.SetForegroundWindow(hwnd)
    return _wait_foreground(pid, 0.5)


def _attribute(hwnd, attribute, value):
    return api.DwmGetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0


def _shows_pixels(hwnd):
    """Whether a window can put anything visible on the screen."""
    if not api.IsWindowVisible(hwnd) or api.IsIconic(hwnd):
        return False
    cloaked = w.DWORD()
    if _attribute(hwnd, api.DWMWA_CLOAKED, cloaked) and cloaked.value:
        return False
    style = api.GetWindowLongW(hwnd, api.GWL_EXSTYLE)
    if style & api.WS_EX_LAYERED:
        if style & api.WS_EX_TRANSPARENT:
            return False  # click-through overlays (graphics drivers and the like)
        alpha, flags = w.BYTE(), w.DWORD()
        if (api.GetLayeredWindowAttributes(hwnd, None, ctypes.byref(alpha), ctypes.byref(flags))
                and flags.value & api.LWA_ALPHA and not alpha.value & 0xFF):
            return False
    return True


def _overlaps(hwnd, area):
    rect = w.RECT()
    # The DWM bounds exclude the invisible resize border around modern windows.
    if not _attribute(hwnd, api.DWMWA_EXTENDED_FRAME_BOUNDS, rect):
        if not api.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
    left, top, width, height = area
    return rect.left < left + width and rect.right > left and rect.top < top + height and rect.bottom > top


def _on_screen(area):
    left, top, width, height = area
    x, y, wide, high = (api.GetSystemMetrics(index) for index in
                        (api.SM_XVIRTUALSCREEN, api.SM_YVIRTUALSCREEN,
                         api.SM_CXVIRTUALSCREEN, api.SM_CYVIRTUALSCREEN))
    return left >= x and top >= y and left + width <= x + wide and top + height <= y + high


def window_above(hwnd, pid):
    """What lies over the game according to the window order, or None.

    Sends no messages, so it is safe to call while the game is frozen.
    """
    area = client_area(hwnd)
    if not area or area[2] <= 0 or area[3] <= 0:
        return "the game window has no visible area"
    if not _on_screen(area):
        return "part of it is off the screen"
    above = api.GetWindow(hwnd, api.GW_HWNDPREV)
    for _ in range(10000):  # the order can change under us; never loop forever
        if not above:
            break
        if owner(above) != pid and _shows_pixels(above) and _overlaps(above, area):
            return COVERED
        above = api.GetWindow(above, api.GW_HWNDPREV)
    return None


def window_at_points(hwnd, pid, points=9):
    """What a grid of hit tests finds over the game, or None. Needs the game running.

    Catches system surfaces such as notifications that the window order omits.
    """
    area = client_area(hwnd)
    if not area:
        return "the game window has no visible area"
    left, top, width, height = area
    for row in range(points):
        for column in range(points):
            point = w.POINT(left + (width - 1) * column // (points - 1),
                            top + (height - 1) * row // (points - 1))
            hit = api.WindowFromPoint(point)
            if not hit:
                return "part of it is off the screen"
            root = api.GetAncestor(hit, api.GA_ROOT)
            if owner(root) != pid:
                return COVERED
    return None
