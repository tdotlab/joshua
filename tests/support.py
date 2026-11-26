"""Shared helpers: paths, and a handle on the ticker test game."""

import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))

from freezeframe import process  # noqa: E402

BUILD = PROJECT / "build"
ARCHITECTURES = ("x86", "x64")


def built():
    return all((BUILD / arch / name).is_file()
               for arch in ARCHITECTURES for name in ("ffclock.dll", "ffinject.exe", "ticker.exe"))


def ticker_exe(arch):
    return BUILD / arch / "ticker.exe"


def read_state(path):
    """The ticker's latest report. It is replaced atomically, but reads can still collide."""
    for _ in range(200):
        try:
            return json.loads(Path(path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            time.sleep(0.005)
    raise AssertionError(f"no readable state in {path}")


def wait_for(condition, seconds=5, what="the condition"):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        value = condition()
        if value:
            return value
        time.sleep(0.01)
    raise AssertionError(f"timed out waiting for {what}")


class Ticker:
    """A ticker process started for one test and always killed afterwards."""

    def __init__(self, directory, arch="x86", hook=True, extra=("--hidden",), life=60):
        self.state_path = Path(directory) / f"ticker-{time.monotonic_ns()}.json"
        args = ["--state", str(self.state_path), "--life", str(life), *extra]
        self.pid = process.launch(ticker_exe(arch), args, directory, hook)
        wait_for(self.state_path.exists, what="the ticker to start")
        wait_for(lambda: self.state()["frames"] > 20, what="the ticker to run")

    def state(self):
        return read_state(self.state_path)

    def kill(self):
        process.kill(self.pid)
