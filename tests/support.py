"""Shared helpers: the import path, and waiting on a test program."""

import json
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "src"))


def read_state(path):
    """The test program's latest report. It is replaced atomically, but reads can still collide."""
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
