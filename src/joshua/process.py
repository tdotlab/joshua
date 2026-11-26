"""Starting, freezing and thawing a game process."""

import ctypes
import os
import subprocess
import winreg
from ctypes import wintypes as w

from . import JoshuaError
from . import win32 as api

_started = []


def image_path(pid):
    handle = api.OpenProcess(api.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        buffer = ctypes.create_unicode_buffer(32768)
        size = w.DWORD(len(buffer))
        if not api.QueryFullProcessImageNameW(handle, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        api.CloseHandle(handle)


def find_running(exe):
    """Process ids whose executable is exactly this file."""
    wanted = os.path.normcase(os.path.realpath(exe))
    pids = (w.DWORD * 8192)()
    used = w.DWORD()
    if not api.EnumProcesses(pids, ctypes.sizeof(pids), ctypes.byref(used)):
        return []
    found = []
    for pid in pids[: used.value // ctypes.sizeof(w.DWORD)]:
        path = image_path(pid)
        if path and os.path.normcase(os.path.realpath(path)) == wanted:
            found.append(pid)
    return found


def launch(exe, args, cwd):
    """Start a game directly and return its process id."""
    game = subprocess.Popen([str(exe), *args], cwd=str(cwd), stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    _started.append(game)  # the game outlives this call; dropping the object would warn
    return game.pid


def _steam_registered():
    try:
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, r"steam\shell\open\command"):
            return True
    except OSError:
        return False


def open_steam(appid):
    """Ask Steam to start a game.

    The request goes through the Windows shell, so that Steam and the game are not
    children of this program and do not end with it.
    """
    if not _steam_registered():
        raise JoshuaError("Steam is not installed for this user: nothing handles steam: links")
    explorer = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "explorer.exe")
    try:
        _started.append(subprocess.Popen([explorer, f"steam://rungameid/{int(appid)}"], stdin=subprocess.DEVNULL,
                                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL))
    except OSError as error:
        raise JoshuaError(f"could not ask Steam to start the game: {error}") from None


def kill(pid):
    handle = api.OpenProcess(api.PROCESS_TERMINATE | api.SYNCHRONIZE, False, pid)
    if handle:
        api.TerminateProcess(handle, 1)
        api.WaitForSingleObject(handle, 5000)
        api.CloseHandle(handle)


class GameProcess:
    """A handle on a running game that can be frozen and thawed.

    Suspension uses a Windows 11 process state-change object: if this program
    dies for any reason, closing that object thaws the game automatically.
    With freezable=False the handle carries no right to suspend the game.
    """

    def __init__(self, pid, freezable=True):
        self.pid = pid
        self.frozen = False
        self._state_change = None
        access = api.PROCESS_TERMINATE | api.PROCESS_QUERY_LIMITED_INFORMATION | api.SYNCHRONIZE
        if freezable:
            access |= api.PROCESS_SUSPEND_RESUME | api.PROCESS_SET_INFORMATION
        self._handle = api.OpenProcess(access, False, pid)
        if not self._handle:
            raise JoshuaError(f"cannot control process {pid}; is it running as administrator?")
        if not freezable:
            return
        state_change = w.HANDLE()
        status = api.NtCreateProcessStateChange(ctypes.byref(state_change),
                                                api.PROCESS_STATE_ALL_ACCESS, None, self._handle, 0)
        if status < 0:
            api.CloseHandle(self._handle)
            raise JoshuaError(f"Windows refused to create a freeze handle (0x{status & 0xFFFFFFFF:08x});"
                                   " Windows 11 is required")
        self._state_change = state_change

    def alive(self):
        if not self._handle:
            return False
        return api.WaitForSingleObject(self._handle, 0) != 0

    def _change(self, kind):
        if not self._state_change:
            raise JoshuaError("this game is not held by freezing")
        status = api.NtChangeProcessState(self._state_change, self._handle, kind, None, 0, 0)
        if status < 0:
            verb = "freeze" if kind == 0 else "thaw"
            raise JoshuaError(f"could not {verb} the game (0x{status & 0xFFFFFFFF:08x})")

    def freeze(self):
        if self.frozen:
            return
        self._change(0)
        self.frozen = True

    def thaw(self):
        if not self.frozen:
            return
        self._change(1)
        self.frozen = False

    def terminate(self):
        api.TerminateProcess(self._handle, 1)
        api.WaitForSingleObject(self._handle, 5000)

    def close(self):
        """Thaw if needed and let go of the process."""
        if not self._handle:
            return
        try:
            if self.frozen and self.alive():
                self.thaw()
        finally:
            if self._state_change:
                api.CloseHandle(self._state_change)
                self._state_change = None
            api.CloseHandle(self._handle)
            self._handle = None
