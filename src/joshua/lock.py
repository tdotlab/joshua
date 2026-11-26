"""Only one player at a time, across every Joshua process in this Windows sign-in."""

import ctypes

from . import win32 as api

NAME = "Local\\JoshuaPlayer"


class PlayerLock:
    """A claim on the screen and keyboard.

    It is a named mutex that is never locked: whoever created it holds the claim
    until closing it. Windows closes it if the process dies, and unlike a locked
    mutex it does not care which thread lets go.
    """

    def __init__(self, name=NAME):
        self._name = name
        self._handle = None

    def claim(self):
        """Take the claim, or keep it. False if another process has it."""
        if self._handle:
            return True
        ctypes.set_last_error(0)
        handle = api.CreateMutexW(None, False, self._name)
        error = ctypes.get_last_error()
        if handle and error != api.ERROR_ALREADY_EXISTS:
            self._handle = handle
            return True
        if handle:
            api.CloseHandle(handle)
        elif error != api.ERROR_ACCESS_DENIED:  # denied: held by an elevated process
            raise ctypes.WinError(error)
        return False

    def release(self):
        if self._handle:
            api.CloseHandle(self._handle)
            self._handle = None
