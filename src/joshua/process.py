"""Starting, hooking, freezing and thawing a game process."""

import ctypes
import os
import struct
import subprocess
from ctypes import wintypes as w
from pathlib import Path

from . import JoshuaError
from . import win32 as api

BUILD = Path(__file__).resolve().parents[2] / "build"
CLOCK_MAGIC = 0x4B43534A
CLOCK_VERSION = 1
_started = []


class _ClockState(ctypes.Structure):
    """Mirrors JShared in native/jclock.c."""

    _fields_ = [("magic", w.DWORD), ("version", w.DWORD), ("offset_qpc", ctypes.c_int64),
                ("patched", w.LONG), ("ready", w.LONG)]


def architecture(exe):
    """Return 'x86' or 'x64' from the executable's PE header."""
    with open(exe, "rb") as handle:
        header = handle.read(4096)
        if header[:2] != b"MZ" or len(header) < 64:
            raise JoshuaError(f"{exe} is not a Windows executable")
        offset = struct.unpack_from("<I", header, 0x3C)[0]
        handle.seek(offset)
        signature = handle.read(6)
    if signature[:4] != b"PE\0\0":
        raise JoshuaError(f"{exe} is not a 32- or 64-bit Windows program")
    machine = struct.unpack_from("<H", signature, 4)[0]
    try:
        return {0x014C: "x86", 0x8664: "x64"}[machine]
    except KeyError:
        raise JoshuaError(f"{exe} targets an unsupported processor (0x{machine:04x})") from None


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


def _native(arch, name):
    path = BUILD / arch / name
    if not path.is_file():
        raise JoshuaError(f"{path} is missing; run build.ps1 first")
    return str(path)


def _run_injector(arch, *arguments):
    result = subprocess.run(
        [_native(arch, "jinject.exe"), *arguments],
        stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    if result.returncode != 0 or not result.stdout.startswith("pid="):
        raise JoshuaError(result.stderr.strip() or "the injector failed without a message")
    return int(result.stdout.strip()[4:])


def launch(exe, args, cwd, hook_clock):
    """Start a game, with the clock hook in place before its first instruction."""
    exe = str(exe)
    if not hook_clock:
        game = subprocess.Popen([exe, *args], cwd=str(cwd), stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        _started.append(game)  # the game outlives this call; dropping the object would warn
        return game.pid
    arch = architecture(exe)
    command = subprocess.list2cmdline([exe, *args])
    return _run_injector(arch, "launch", _native(arch, "jclock.dll"), str(cwd), exe, command)


def kill(pid):
    handle = api.OpenProcess(api.PROCESS_TERMINATE | api.SYNCHRONIZE, False, pid)
    if handle:
        api.TerminateProcess(handle, 1)
        api.WaitForSingleObject(handle, 5000)
        api.CloseHandle(handle)


class Clock:
    """The offset that the injected hook subtracts from the game's clocks."""

    def __init__(self, mapping, view):
        self._mapping = mapping
        self._view = view
        self._state = _ClockState.from_address(view)

    @classmethod
    def open(cls, pid):
        """Return the clock of an already hooked process, or None."""
        mapping = api.OpenFileMappingW(api.FILE_MAP_ALL_ACCESS, False, f"Local\\JoshuaClock-{pid}")
        if not mapping:
            return None
        view = api.MapViewOfFile(mapping, api.FILE_MAP_ALL_ACCESS, 0, 0, ctypes.sizeof(_ClockState))
        if not view:
            api.CloseHandle(mapping)
            return None
        clock = cls(mapping, view)
        state = clock._state
        if state.magic != CLOCK_MAGIC or state.version != CLOCK_VERSION or not state.ready:
            clock.close()
            raise JoshuaError("the game holds an incompatible or half-loaded clock hook; restart it")
        return clock

    @property
    def patched(self):
        return self._state.patched

    @property
    def offset(self):
        return self._state.offset_qpc

    def hide(self, counts):
        """Remove a span of QPC counts from the game's timeline. Only call while it is suspended."""
        self._state.offset_qpc += counts

    def close(self):
        if self._view:
            self._state = None
            api.UnmapViewOfFile(self._view)
            api.CloseHandle(self._mapping)
            self._view = None


def install_clock(pid, exe):
    clock = Clock.open(pid)
    if clock:
        return clock
    arch = architecture(exe)
    _run_injector(arch, "attach", _native(arch, "jclock.dll"), str(pid))
    clock = Clock.open(pid)
    if not clock:
        raise JoshuaError("the clock hook loaded but never reported in")
    return clock


class GameProcess:
    """A handle on a running game that can be frozen and thawed.

    Suspension uses a Windows 11 process state-change object: if this program
    dies for any reason, closing that object thaws the game automatically.
    With freezable=False the handle carries no right to suspend the game.
    """

    def __init__(self, pid, clock=None, freezable=True):
        self.pid = pid
        self.clock = clock
        self._frozen_at = None
        self._state_change = None
        access = api.PROCESS_TERMINATE | api.PROCESS_QUERY_LIMITED_INFORMATION | api.SYNCHRONIZE
        if freezable:
            access |= api.PROCESS_SUSPEND_RESUME | api.PROCESS_SET_INFORMATION
        self._handle = api.OpenProcess(access, False, pid)
        if not self._handle:
            if clock:
                clock.close()
            raise JoshuaError(f"cannot control process {pid}; is it running as administrator?")
        if not freezable:
            return
        state_change = w.HANDLE()
        status = api.NtCreateProcessStateChange(ctypes.byref(state_change),
                                                api.PROCESS_STATE_ALL_ACCESS, None, self._handle, 0)
        if status < 0:
            api.CloseHandle(self._handle)
            if clock:
                clock.close()
            raise JoshuaError(f"Windows refused to create a freeze handle (0x{status & 0xFFFFFFFF:08x});"
                                   " Windows 11 is required")
        self._state_change = state_change

    @property
    def frozen(self):
        return self._frozen_at is not None

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
        self._frozen_at = api.qpc()

    def thaw(self):
        """Resume the game. Returns the real seconds it spent frozen, in QPC counts."""
        if not self.frozen:
            return 0
        elapsed = api.qpc() - self._frozen_at
        if self.clock:
            self.clock.hide(elapsed)
        self._change(1)
        self._frozen_at = None
        return elapsed

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
            if self.clock:
                self.clock.close()
