"""The Windows API surface Freezeframe uses, declared once."""

import ctypes
from ctypes import wintypes as w

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
user32 = ctypes.WinDLL("user32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
ntdll = ctypes.WinDLL("ntdll")
winmm = ctypes.WinDLL("winmm")
dwmapi = ctypes.WinDLL("dwmapi")
shcore = ctypes.WinDLL("shcore")

ULONG_PTR = ctypes.c_size_t

PROCESS_TERMINATE = 0x0001
PROCESS_SET_INFORMATION = 0x0200
PROCESS_SUSPEND_RESUME = 0x0800
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
SYNCHRONIZE = 0x00100000
PROCESS_STATE_ALL_ACCESS = 0x001F0001
FILE_MAP_ALL_ACCESS = 0x000F001F
STILL_ACTIVE = 259

GA_ROOT = 2
GW_HWNDPREV = 3
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
LWA_ALPHA = 0x0002
DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14
SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN, SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 76, 77, 78, 79
MONITOR_DEFAULTTONEAREST = 2
MDT_EFFECTIVE_DPI = 0
SW_RESTORE = 9
WM_CLOSE = 0x0010
INPUT_MOUSE = 0
INPUT_KEYBOARD = 1
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MOUSEEVENTF_MOVE = 0x0001
MOUSE_BUTTON_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_F12 = 0x7B
SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000
HALFTONE = 4


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", w.LONG), ("dy", w.LONG), ("mouseData", w.DWORD), ("dwFlags", w.DWORD),
                ("time", w.DWORD), ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", w.WORD), ("wScan", w.WORD), ("dwFlags", w.DWORD), ("time", w.DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", w.DWORD), ("u", _INPUTUNION)]


class LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", w.UINT), ("dwTime", w.DWORD)]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", w.DWORD), ("biWidth", w.LONG), ("biHeight", w.LONG), ("biPlanes", w.WORD),
                ("biBitCount", w.WORD), ("biCompression", w.DWORD), ("biSizeImage", w.DWORD),
                ("biXPelsPerMeter", w.LONG), ("biYPelsPerMeter", w.LONG), ("biClrUsed", w.DWORD),
                ("biClrImportant", w.DWORD)]


EnumWindowsProc = ctypes.WINFUNCTYPE(w.BOOL, w.HWND, w.LPARAM)


def _declare(library, name, restype, *argtypes):
    function = getattr(library, name)
    function.restype = restype
    function.argtypes = list(argtypes)
    return function


_P = ctypes.POINTER

OpenProcess = _declare(kernel32, "OpenProcess", w.HANDLE, w.DWORD, w.BOOL, w.DWORD)
CloseHandle = _declare(kernel32, "CloseHandle", w.BOOL, w.HANDLE)
TerminateProcess = _declare(kernel32, "TerminateProcess", w.BOOL, w.HANDLE, w.UINT)
GetExitCodeProcess = _declare(kernel32, "GetExitCodeProcess", w.BOOL, w.HANDLE, _P(w.DWORD))
WaitForSingleObject = _declare(kernel32, "WaitForSingleObject", w.DWORD, w.HANDLE, w.DWORD)
QueryFullProcessImageNameW = _declare(kernel32, "QueryFullProcessImageNameW", w.BOOL, w.HANDLE,
                                      w.DWORD, w.LPWSTR, _P(w.DWORD))
EnumProcesses = _declare(kernel32, "K32EnumProcesses", w.BOOL, _P(w.DWORD), w.DWORD, _P(w.DWORD))
QueryPerformanceCounter = _declare(kernel32, "QueryPerformanceCounter", w.BOOL, _P(ctypes.c_int64))
OpenFileMappingW = _declare(kernel32, "OpenFileMappingW", w.HANDLE, w.DWORD, w.BOOL, w.LPCWSTR)
MapViewOfFile = _declare(kernel32, "MapViewOfFile", ctypes.c_void_p, w.HANDLE, w.DWORD, w.DWORD,
                         w.DWORD, ctypes.c_size_t)
UnmapViewOfFile = _declare(kernel32, "UnmapViewOfFile", w.BOOL, ctypes.c_void_p)
GetTickCount = _declare(kernel32, "GetTickCount", w.DWORD)
GetCurrentThreadId = _declare(kernel32, "GetCurrentThreadId", w.DWORD)

NtCreateProcessStateChange = _declare(ntdll, "NtCreateProcessStateChange", w.LONG, _P(w.HANDLE),
                                      w.DWORD, ctypes.c_void_p, w.HANDLE, w.ULONG)
NtChangeProcessState = _declare(ntdll, "NtChangeProcessState", w.LONG, w.HANDLE, w.HANDLE, ctypes.c_int,
                                ctypes.c_void_p, ctypes.c_size_t, w.ULONG)

timeBeginPeriod = _declare(winmm, "timeBeginPeriod", w.UINT, w.UINT)

EnumWindows = _declare(user32, "EnumWindows", w.BOOL, EnumWindowsProc, w.LPARAM)
GetWindowThreadProcessId = _declare(user32, "GetWindowThreadProcessId", w.DWORD, w.HWND, _P(w.DWORD))
IsWindow = _declare(user32, "IsWindow", w.BOOL, w.HWND)
IsWindowVisible = _declare(user32, "IsWindowVisible", w.BOOL, w.HWND)
IsIconic = _declare(user32, "IsIconic", w.BOOL, w.HWND)
ShowWindow = _declare(user32, "ShowWindow", w.BOOL, w.HWND, ctypes.c_int)
GetClientRect = _declare(user32, "GetClientRect", w.BOOL, w.HWND, _P(w.RECT))
ClientToScreen = _declare(user32, "ClientToScreen", w.BOOL, w.HWND, _P(w.POINT))
GetWindowTextW = _declare(user32, "GetWindowTextW", ctypes.c_int, w.HWND, w.LPWSTR, ctypes.c_int)
SetForegroundWindow = _declare(user32, "SetForegroundWindow", w.BOOL, w.HWND)
BringWindowToTop = _declare(user32, "BringWindowToTop", w.BOOL, w.HWND)
GetForegroundWindow = _declare(user32, "GetForegroundWindow", w.HWND)
AttachThreadInput = _declare(user32, "AttachThreadInput", w.BOOL, w.DWORD, w.DWORD, w.BOOL)
GetAncestor = _declare(user32, "GetAncestor", w.HWND, w.HWND, w.UINT)
WindowFromPoint = _declare(user32, "WindowFromPoint", w.HWND, w.POINT)
SendInput = _declare(user32, "SendInput", w.UINT, w.UINT, _P(INPUT), ctypes.c_int)
SetCursorPos = _declare(user32, "SetCursorPos", w.BOOL, ctypes.c_int, ctypes.c_int)
GetCursorPos = _declare(user32, "GetCursorPos", w.BOOL, _P(w.POINT))
GetAsyncKeyState = _declare(user32, "GetAsyncKeyState", ctypes.c_short, ctypes.c_int)
GetLastInputInfo = _declare(user32, "GetLastInputInfo", w.BOOL, _P(LASTINPUTINFO))
PostMessageW = _declare(user32, "PostMessageW", w.BOOL, w.HWND, w.UINT, w.WPARAM, w.LPARAM)
SetProcessDpiAwarenessContext = _declare(user32, "SetProcessDpiAwarenessContext", w.BOOL,
                                         ctypes.c_void_p)
GetDC = _declare(user32, "GetDC", w.HDC, w.HWND)
ReleaseDC = _declare(user32, "ReleaseDC", ctypes.c_int, w.HWND, w.HDC)
GetWindow = _declare(user32, "GetWindow", w.HWND, w.HWND, w.UINT)
GetWindowRect = _declare(user32, "GetWindowRect", w.BOOL, w.HWND, _P(w.RECT))
GetWindowLongW = _declare(user32, "GetWindowLongW", w.LONG, w.HWND, ctypes.c_int)
GetLayeredWindowAttributes = _declare(user32, "GetLayeredWindowAttributes", w.BOOL, w.HWND,
                                      _P(w.DWORD), _P(w.BYTE), _P(w.DWORD))
GetSystemMetrics = _declare(user32, "GetSystemMetrics", ctypes.c_int, ctypes.c_int)
DwmGetWindowAttribute = _declare(dwmapi, "DwmGetWindowAttribute", w.LONG, w.HWND, w.DWORD,
                                 ctypes.c_void_p, w.DWORD)
MonitorFromWindow = _declare(user32, "MonitorFromWindow", w.HMONITOR, w.HWND, w.DWORD)
GetDpiForMonitor = _declare(shcore, "GetDpiForMonitor", w.LONG, w.HMONITOR, ctypes.c_int, _P(w.UINT),
                            _P(w.UINT))
QueryPerformanceFrequency = _declare(kernel32, "QueryPerformanceFrequency", w.BOOL, _P(ctypes.c_int64))

CreateCompatibleDC = _declare(gdi32, "CreateCompatibleDC", w.HDC, w.HDC)
CreateDIBSection = _declare(gdi32, "CreateDIBSection", w.HBITMAP, w.HDC, _P(BITMAPINFOHEADER), w.UINT,
                            _P(ctypes.c_void_p), w.HANDLE, w.DWORD)
SelectObject = _declare(gdi32, "SelectObject", w.HGDIOBJ, w.HDC, w.HGDIOBJ)
SetStretchBltMode = _declare(gdi32, "SetStretchBltMode", ctypes.c_int, w.HDC, ctypes.c_int)
SetBrushOrgEx = _declare(gdi32, "SetBrushOrgEx", w.BOOL, w.HDC, ctypes.c_int, ctypes.c_int,
                         ctypes.c_void_p)
StretchBlt = _declare(gdi32, "StretchBlt", w.BOOL, w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      ctypes.c_int, w.HDC, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                      w.DWORD)
GdiFlush = _declare(gdi32, "GdiFlush", w.BOOL)
DeleteObject = _declare(gdi32, "DeleteObject", w.BOOL, w.HGDIOBJ)
DeleteDC = _declare(gdi32, "DeleteDC", w.BOOL, w.HDC)


def qpc():
    value = ctypes.c_int64()
    QueryPerformanceCounter(ctypes.byref(value))
    return value.value


def qpc_milliseconds(counts):
    frequency = ctypes.c_int64()
    QueryPerformanceFrequency(ctypes.byref(frequency))
    return round(counts * 1000 / frequency.value)


def use_physical_pixels():
    """Make window coordinates and screen captures agree on scaled displays."""
    SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # per-monitor aware, version 2
