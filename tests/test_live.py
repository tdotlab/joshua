"""The real server, playing a real window with real input. Opt-in: it takes the screen and keyboard.

Run with JOSHUA_LIVE=1 while nobody is using the computer. The game is
pausebox.py, a harmless Tk window that pauses with P. Nothing is injected or
suspended: these tests use "hold": "pause_key" only.
"""

import base64
import json
import os
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import support
from joshua import process

HERE = Path(__file__).resolve().parent


@unittest.skipUnless(os.environ.get("JOSHUA_LIVE") == "1", "takes the screen and keyboard; see README.md")
class LiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.state_path = self.directory / "pausebox.json"
        self.pythonw = Path(sys.executable).with_name("pythonw.exe")
        self.before = set(process.find_running(self.pythonw))
        self.addCleanup(self._kill_strays)
        self.server = None
        self.number = 0

    def _kill_strays(self):
        if self.server:
            self.server.stdin.close()
            self.server.wait(15)
            self.server.stdout.close()
        for pid in set(process.find_running(self.pythonw)) - self.before:
            process.kill(pid)

    def serve(self, *extra):
        games = self.directory / "games.json"
        games.write_text(json.dumps({"pausebox": {
            "exe": str(self.pythonw), "args": [str(HERE / "pausebox.py"), str(self.state_path), *extra],
            "hold": "pause_key", "pause_key": "p", "startup_ms": 1500, "window_title": "pausebox",
            "notes": "Arrows move the box.",
        }}), encoding="utf-8")
        log = open(self.directory / "server.log", "w", encoding="utf-8")
        self.addCleanup(log.close)
        self.server = subprocess.Popen(
            [sys.executable, str(support.PROJECT / "src" / "serve.py")], stdin=subprocess.PIPE,
            stdout=subprocess.PIPE, stderr=log, env={**os.environ, "JOSHUA_GAMES": str(games)})
        self.rpc("initialize", {"protocolVersion": "2025-11-25"})

    def rpc(self, method, params):
        self.number += 1
        message = {"jsonrpc": "2.0", "id": self.number, "method": method, "params": params}
        self.server.stdin.write(json.dumps(message).encode() + b"\n")
        self.server.stdin.flush()
        reply = json.loads(self.server.stdout.readline())
        self.assertEqual(reply.get("id"), self.number, reply)
        return reply["result"]

    def call(self, name, **arguments):
        result = self.rpc("tools/call", {"name": name, "arguments": arguments})
        text = " ".join(part["text"] for part in result["content"] if part["type"] == "text")
        images = [base64.b64decode(part["data"]) for part in result["content"] if part["type"] == "image"]
        return result["isError"], text, images

    def state(self):
        return support.read_state(self.state_path)

    def assert_held(self, seconds=0.8):
        before = self.state()
        time.sleep(seconds)
        after = self.state()
        self.assertTrue(after["paused"], after)
        self.assertEqual(before["ticks"], after["ticks"], "the game ran between steps")

    def assert_picture(self, images):
        self.assertEqual(len(images), 1)
        png = images[0]
        self.assertEqual(png[:8], b"\x89PNG\r\n\x1a\n")
        width, height = struct.unpack(">II", png[16:24])
        self.assertGreater(width * height, 0)

    def test_pausing_holds_the_game_still(self):
        self.serve()
        error, text, images = self.call("start_game", game="pausebox")
        self.assertFalse(error, text)
        self.assertIn("is now paused", text)
        self.assertNotIn("Warning", text)
        self.assert_picture(images)
        self.assertEqual(self.state()["toggles"], 1)
        self.assert_held()

        before = self.state()
        error, text, images = self.call("step", segments=[{"ms": 400, "keys": ["right"]}])
        self.assertFalse(error, text)
        self.assertIn("is now paused", text)
        self.assertNotIn("Warning", text)
        self.assert_picture(images)
        after = self.state()
        self.assertGreater(after["x"], before["x"], "the arrow key did not reach the game")
        self.assertGreater(after["ticks"], before["ticks"])
        self.assertEqual(after["toggles"], 3)
        self.assert_held()

        error, text, _ = self.call("step", segments=[{"ms": 100, "keys": ["p"]}])
        self.assertTrue(error)
        self.assertIn("pause", text)
        self.assertEqual(self.state()["toggles"], 3)

        error, text, _ = self.call("end_session", close_game=True)
        self.assertFalse(error, text)
        self.assertIn("the game closed", text)

    def test_a_toggle_out_of_step_is_caught_and_fixed(self):
        # The game starts paused, so the first press of P resumes it instead.
        self.serve("--paused")
        error, text, _ = self.call("start_game", game="pausebox")
        self.assertFalse(error, text)
        self.assertIn("Warning: the picture kept changing", text)
        self.assertFalse(self.state()["paused"])

        error, text, images = self.call("repause")
        self.assertFalse(error, text)
        self.assertIn("pressed again", text)
        self.assertNotIn("Warning", text)
        self.assert_picture(images)
        self.assert_held()

        error, text, _ = self.call("end_session")
        self.assertFalse(error, text)
        self.assertIn("left paused", text)


if __name__ == "__main__":
    unittest.main()
