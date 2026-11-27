"""Another program's processes, on Windows and Linux: who started whom, how much memory, and stopping them.

A browser game's Chromium is started by Playwright's driver, which this program starts.
These find everything under the driver, so a runaway browser can be measured and stopped.
"""

import os
import signal
import sys

WINDOWS = sys.platform == "win32"


def _table_linux():
    """pid -> (parent pid, start time)."""
    table = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as file:
                stat = file.read().decode("ascii", "replace")
        except OSError:
            continue  # exited while being listed
        fields = stat[stat.rindex(")") + 2:].split()  # the name may hold spaces and brackets
        table[int(name)] = (int(fields[1]), int(fields[19]))
    return table


def _created_windows(pid):
    import ctypes
    from ctypes import wintypes as w

    from . import win32 as api

    handle = api.OpenProcess(api.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        times = [w.FILETIME() for _ in range(4)]
        if not api.GetProcessTimes(handle, *(ctypes.byref(t) for t in times)):
            return None
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    finally:
        api.CloseHandle(handle)


def _table_windows():
    import ctypes

    from . import win32 as api

    snapshot = api.CreateToolhelp32Snapshot(api.TH32CS_SNAPPROCESS, 0)
    if not snapshot or snapshot == api.INVALID_HANDLE_VALUE:
        return {}
    table = {}
    try:
        entry = api.PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(entry)
        more = api.Process32FirstW(snapshot, ctypes.byref(entry))
        while more:
            table[entry.th32ProcessID] = (entry.th32ParentProcessID, None)  # start time looked up when needed
            more = api.Process32NextW(snapshot, ctypes.byref(entry))
    finally:
        api.CloseHandle(snapshot)
    return table


def table():
    return _table_windows() if WINDOWS else _table_linux()


def children(pid, listing=None):
    listing = table() if listing is None else listing
    return [child for child, (parent, _) in listing.items() if parent == pid and child != pid]


def started(pid, listing=None):
    """When a process started, as a number only comparable with others from here; None if it is gone."""
    if WINDOWS:
        return _created_windows(pid)
    listing = table() if listing is None else listing
    return listing.get(pid, (None, None))[1]


def descendants(root, listing=None):
    """Every process under root, nearest first.

    On Windows a parent id can be stale: the parent exited and its number went to a
    newer process. A child counts only if it is no older than its parent.
    """
    listing = table() if listing is None else listing
    created = {}

    def born(pid):
        if pid not in created:
            created[pid] = started(pid, listing)
        return created[pid]

    found, queue = [], [root]
    while queue:
        parent = queue.pop(0)
        for child in children(parent, listing):
            if child in found:
                continue
            if WINDOWS:
                child_born, parent_born = born(child), born(parent)
                if child_born is None or parent_born is None or child_born < parent_born:
                    continue
            found.append(child)
            queue.append(child)
    return found


def memory(pid):
    """Bytes a process uses that are its own (proportional set on Linux, private commit on Windows), or 0."""
    if WINDOWS:
        import ctypes

        from . import win32 as api

        handle = api.OpenProcess(api.PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return 0
        try:
            counters = api.PROCESS_MEMORY_COUNTERS_EX()
            counters.cb = ctypes.sizeof(counters)
            if not api.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return 0
            return counters.PrivateUsage
        finally:
            api.CloseHandle(handle)
    try:
        with open(f"/proc/{pid}/smaps_rollup", encoding="ascii") as file:
            for line in file:
                if line.startswith("Pss:"):
                    return int(line.split()[1]) * 1024
    except (OSError, ValueError):
        pass
    try:
        with open(f"/proc/{pid}/statm", encoding="ascii") as file:
            return int(file.read().split()[1]) * os.sysconf("SC_PAGE_SIZE")
    except (OSError, ValueError, IndexError):
        return 0


def kill(pid):
    if WINDOWS:
        from . import win32 as api

        handle = api.OpenProcess(api.PROCESS_TERMINATE, False, pid)
        if handle:
            api.TerminateProcess(handle, 1)
            api.CloseHandle(handle)
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass  # already gone
