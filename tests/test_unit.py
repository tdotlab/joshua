"""Tests that launch no game, inject nothing and send no real input."""

import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import unittest
import zlib
from pathlib import Path
from unittest import mock

import support
from freezeframe import FreezeframeError, capture, config, hold, keyboard, process, timeline, window
from freezeframe import session as session_module
from freezeframe.server import Server
from freezeframe.session import Result, Session


class TimelineTests(unittest.TestCase):
    def test_accepts_a_normal_step(self):
        segments = timeline.parse([{"ms": 200, "keys": ["Up", "up", "left"]},
                                   {"ms": 100, "buttons": ["left"], "mouse_to": [10.4, 20.6]}])
        self.assertEqual(segments[0].keys, ("up", "left"))
        self.assertEqual(segments[1].mouse_to, (10, 21))

    def test_rejects_bad_steps(self):
        bad = {
            "empty": [],
            "not a list": {"ms": 100},
            "too short": [{"ms": 1}],
            "boolean duration": [{"ms": True}],
            "total too long": [{"ms": 10000}, {"ms": 10000}],
            "too many": [{"ms": 10}] * (timeline.MAX_SEGMENTS + 1),
            "unknown key": [{"ms": 100, "keys": ["windows"]}],
            "unknown button": [{"ms": 100, "buttons": ["fourth"]}],
            "unknown field": [{"ms": 100, "hold": True}],
            "bad point": [{"ms": 100, "mouse_to": [1]}],
            "alt tab": [{"ms": 100, "keys": ["alt", "tab"]}],
            "ctrl esc": [{"ms": 100, "keys": ["rightctrl", "esc"]}],
            "abort chord": [{"ms": 100, "keys": ["ctrl", "alt", "f12"]}],
        }
        for name, raw in bad.items():
            with self.subTest(name), self.assertRaises(FreezeframeError):
                timeline.parse(raw)


class PlayerTests(unittest.TestCase):
    def setUp(self):
        self.sent = []
        patcher = mock.patch.object(keyboard, "_send", lambda *events: self.sent.extend(events))
        patcher.start()
        self.addCleanup(patcher.stop)

    def player(self, guard=lambda: None, over_game=lambda: True):
        return keyboard.Player(keyboard.UserActivity(), guard, lambda point: point, over_game)

    def releases(self):
        return [(e.ki.wScan, bool(e.ki.dwFlags & 0x0002)) for e in self.sent if e.type == 1]

    def test_sends_nothing_when_the_game_is_not_ready(self):
        _, reason = self.player(guard=lambda: "no focus").play(timeline.parse([{"ms": 50, "keys": ["w"]}]))
        self.assertEqual(reason, "no focus")
        self.assertEqual(self.sent, [])

    def test_releases_keys_when_interrupted(self):
        calls = iter([None, None])
        _, reason = self.player(guard=lambda: next(calls, "lost focus")).play(
            timeline.parse([{"ms": 500, "keys": ["w", "shift"]}]))
        self.assertEqual(reason, "lost focus")
        self.assertEqual([up for _, up in self.releases()], [False, False, True, True])

    def test_releases_keys_after_an_error(self):
        with self.assertRaises(FreezeframeError):
            self.player(over_game=lambda: False).play(
                timeline.parse([{"ms": 20, "keys": ["w"], "buttons": ["left"]}]))
        self.assertEqual([up for _, up in self.releases()], [False, True])

    def test_keeps_a_key_held_across_segments(self):
        self.player().play(timeline.parse([{"ms": 10, "keys": ["w"]}, {"ms": 10, "keys": ["w", "a"]}]))
        scans = self.releases()
        w_scan = timeline.KEYS["w"][0]
        self.assertEqual([up for scan, up in scans if scan == w_scan], [False, True])


class CaptureTests(unittest.TestCase):
    def test_png_holds_the_pixels(self):
        data = capture.encode_png(2, 1, bytes([3, 2, 1, 0, 6, 5, 4, 0]))
        self.assertEqual(data[:8], b"\x89PNG\r\n\x1a\n")
        position, chunks = 8, {}
        while position < len(data):
            length = struct.unpack_from(">I", data, position)[0]
            kind = data[position + 4:position + 8]
            body = data[position + 8:position + 8 + length]
            self.assertEqual(struct.unpack_from(">I", data, position + 8 + length)[0],
                             zlib.crc32(kind + body))
            chunks[kind] = body
            position += 12 + length
        self.assertEqual(struct.unpack(">II", chunks[b"IHDR"][:8]), (2, 1))
        self.assertEqual(zlib.decompress(chunks[b"IDAT"]), bytes([0, 1, 2, 3, 4, 5, 6]))

    def test_scaling_keeps_the_shape(self):
        self.assertEqual(capture.scaled_size(1920, 1080, 1280), (1280, 720))
        self.assertEqual(capture.scaled_size(640, 480, 1280), (640, 480))


class SessionTests(unittest.TestCase):
    def test_screenshot_points_map_to_screen_pixels(self):
        session = Session.__new__(Session)
        session.hwnd = 1
        session.frame = capture.Frame(b"", 1280, 720, 2560, 1440, False)
        with mock.patch("freezeframe.window.client_area", lambda _: (100, 50, 2560, 1440)):
            self.assertEqual(session._to_screen((0, 0)), (100, 50))
            self.assertEqual(session._to_screen((640, 360)), (1380, 770))
            with self.assertRaises(FreezeframeError):
                session._to_screen((1280, 0))


def frame(tag):
    return capture.Frame(tag, 4, 3, 4, 3, False)


def game(**settings):
    return config.Game("toy", Path("toy.exe"), **settings)


class PauseHoldTests(unittest.TestCase):
    """The pause strategy's bookkeeping, with a fake key press."""

    def setUp(self):
        self.pressed = []
        self.outcome = (True, None)

    def tap(self, keys):
        self.pressed.append(keys)
        return self.outcome

    def test_toggles_with_one_key(self):
        pause = hold.Pause(game(hold="pause_key", pause_key=("p",)))
        self.assertIsNone(pause.resume(self.tap))   # a fresh game is running: nothing to resume
        self.assertIsNone(pause.hold(self.tap))
        self.assertEqual(pause.state(), "paused")
        self.assertIsNone(pause.hold(self.tap))     # already paused: no second press
        pause.resume(self.tap)
        self.assertEqual(self.pressed, [("p",), ("p",)])

    def test_uses_a_separate_resume_key(self):
        pause = hold.Pause(game(hold="pause_key", pause_key=("f5",), resume_key=("f6",)))
        pause.hold(self.tap)
        pause.resume(self.tap)
        self.assertEqual(self.pressed, [("f5",), ("f6",)])

    def test_reports_a_pause_that_did_not_happen(self):
        pause = hold.Pause(game(hold="pause_key", pause_key=("p",)))
        self.outcome = (False, "the game lost focus")
        self.assertIn("still running", pause.hold(self.tap))
        self.assertEqual(pause.state(), "running")
        self.outcome = (True, "the game lost focus")
        self.assertIn("may not have registered", pause.hold(self.tap))
        self.assertEqual(pause.state(), "paused")

    def test_a_resume_that_did_not_happen_keeps_it_paused(self):
        pause = hold.Pause(game(hold="pause_key", pause_key=("p",)))
        pause.hold(self.tap)
        self.outcome = (False, "no focus")
        self.assertIn("not resumed", pause.resume(self.tap))
        self.assertEqual(pause.state(), "paused")

    def test_refuses_input_that_holds_the_pause_key(self):
        pause = hold.Pause(game(hold="pause_key", pause_key=("shift", "p")))
        pause.check(timeline.parse([{"ms": 100, "keys": ["p"]}]))  # p alone is fine
        with self.assertRaises(FreezeframeError):
            pause.check(timeline.parse([{"ms": 100, "keys": ["p", "shift", "up"]}]))


class SessionStepTests(unittest.TestCase):
    """A whole step with every Windows call replaced: what is pressed, in what order."""

    def make(self, **settings):
        self.events = []
        self.frames = []
        self.lost_focus_after = None
        proc = mock.Mock(pid=1)
        proc.alive.return_value = True
        proc.thaw.side_effect = lambda: self.events.append("thaw")
        proc.freeze.side_effect = lambda: self.events.append("freeze")
        session = Session(game(**settings), proc, launched=True)
        session.hwnd = 1

        def send(*inputs):
            for event in inputs:
                name = next(n for n, (scan, _) in timeline.KEYS.items() if scan == event.ki.wScan)
                self.events.append(f"{name} {'up' if event.ki.dwFlags & 0x0002 else 'down'}")

        def guard():
            if self.lost_focus_after and self.lost_focus_after in self.events:
                return "the game lost focus"
            return None

        def photograph(keep=True):
            self.events.append("photo")
            return (self.frames.pop(0) if self.frames else frame(b"same")), None

        for target, name, value in (
                (keyboard, "_send", send),
                (window, "find", lambda *_: 1),
                (window, "is_foreground", lambda _: True),
                (window, "window_at_points", lambda *_: None),
                (session_module, "COMPOSE_SECONDS", 0),
                (session, "_guard", guard),
                (session, "_photograph", photograph),
                (session, "_wait_until_idle", lambda: self.events.append("idle")),
                (session, "_focus", lambda: self.events.append("focus"))):
            patcher = mock.patch.object(target, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return session

    def test_pauses_after_input_and_resumes_before_the_next(self):
        session = self.make(hold="pause_key", pause_key=("p",), pause_ms=20)
        result = session.step(timeline.parse([{"ms": 20, "keys": ["w"]}]), settle_ms=0)
        self.assertEqual(result.state, "paused")
        self.assertIsNone(result.warning)
        self.assertEqual(self.events, ["idle", "focus", "w down", "w up", "p down", "p up", "photo", "photo"])
        self.events.clear()
        session.step(timeline.parse([{"ms": 20, "keys": ["w"]}]), settle_ms=0)
        self.assertEqual(self.events, ["idle", "focus", "p down", "p up", "w down", "w up", "p down", "p up",
                                       "photo", "photo"])
        self.assertNotIn("thaw", self.events)

    def test_warns_when_the_paused_picture_keeps_changing(self):
        session = self.make(hold="pause_key", pause_key=("p",), pause_ms=20)
        self.frames = [frame(b"one"), frame(b"two")]
        result = session.step([], settle_ms=0)
        self.assertEqual(result.warning, session_module.STILL_RUNNING)
        self.assertEqual(result.frame.png, b"two")

    def test_can_photograph_before_pausing(self):
        session = self.make(hold="pause_key", pause_key=("p",), pause_ms=20, capture="before_pause")
        self.frames = [frame(b"shown"), frame(b"a"), frame(b"a")]
        result = session.step([], settle_ms=0)
        self.assertEqual(self.events, ["idle", "focus", "photo", "p down", "p up", "photo", "photo"])
        self.assertEqual(result.frame.png, b"shown")
        self.assertIsNone(result.warning)

    def test_a_pause_that_cannot_be_sent_is_reported(self):
        session = self.make(hold="pause_key", pause_key=("p",), pause_ms=20)
        self.lost_focus_after = "w down"
        result = session.step(timeline.parse([{"ms": 500, "keys": ["w"]}]), settle_ms=0)
        self.assertEqual(result.stopped, "the game lost focus")
        self.assertEqual(result.state, "running")
        self.assertIn("could not be paused", result.warning)
        self.assertNotIn("p down", self.events)

    def test_the_pause_key_is_refused_before_anything_happens(self):
        session = self.make(hold="pause_key", pause_key=("esc",), pause_ms=20)
        with self.assertRaises(FreezeframeError):
            session.step(timeline.parse([{"ms": 20, "keys": ["esc"]}]))
        self.assertEqual(self.events, [])

    def test_freezing_waits_for_the_user_before_thawing(self):
        session = self.make(hold="freeze")
        result = session.step(timeline.parse([{"ms": 20, "keys": ["w"]}]), settle_ms=0)
        self.assertEqual(self.events, ["idle", "thaw", "focus", "w down", "w up", "freeze", "photo"])
        self.assertEqual(result.state, "frozen")

    def test_a_failed_step_still_freezes(self):
        session = self.make(hold="freeze")

        def refuse():
            self.events.append("focus")
            raise FreezeframeError("no")
        with mock.patch.object(session, "_focus", refuse), self.assertRaises(FreezeframeError):
            session.step([], settle_ms=0)
        self.assertEqual(self.events, ["idle", "thaw", "focus", "freeze"])

    def test_an_unheld_game_is_photographed_running(self):
        session = self.make(hold="none")
        result = session.step(timeline.parse([{"ms": 20, "keys": ["w"]}]), settle_ms=0)
        self.assertEqual(self.events, ["idle", "focus", "w down", "w up", "photo"])
        self.assertEqual(result.state, "unheld")

    def test_repause_presses_the_pause_key_once(self):
        session = self.make(hold="pause_key", pause_key=("p",), pause_ms=20)
        session.step([], settle_ms=0)
        self.events.clear()
        pressed, result = session.repause()
        self.assertTrue(pressed)
        self.assertEqual(self.events[:4], ["idle", "focus", "p down", "p up"])
        self.assertEqual(result.state, "paused")
        with self.assertRaises(FreezeframeError):
            self.make(hold="freeze").repause()


class ProcessTests(unittest.TestCase):
    def test_a_pausable_game_is_opened_without_the_right_to_suspend_it(self):
        own = process.GameProcess(os.getpid(), freezable=False)
        try:
            self.assertTrue(own.alive())
            with self.assertRaisesRegex(FreezeframeError, "not held by freezing"):
                own.freeze()
        finally:
            own.close()

    def test_reads_the_architecture_of_a_program(self):
        self.assertEqual(process.architecture(sys.executable),
                         "x64" if sys.maxsize > 2**32 else "x86")
        with self.assertRaises(FreezeframeError):
            process.architecture(__file__)

    def test_starts_a_game_without_injecting_anything(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(process, "_run_injector") as injector:
                process.launch(sys.executable, ["-c", "open('ran', 'w').close()"], directory, False)
                process._started[-1].wait(30)
            self.assertTrue((Path(directory) / "ran").exists())
        injector.assert_not_called()


class PrivacyTests(unittest.TestCase):
    def test_a_covering_window_is_not_named(self):
        other, mine = 7, 1
        with mock.patch.multiple(window, client_area=lambda _: (0, 0, 100, 100),
                                 owner=lambda _: other, title=lambda _: "private title"), \
             mock.patch.object(window.api, "WindowFromPoint", lambda _: 5), \
             mock.patch.object(window.api, "GetAncestor", lambda hwnd, _: hwnd):
            self.assertEqual(window.window_at_points(2, mine), window.COVERED)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "games.json"

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return config.load(self.path)

    def test_defaults_are_the_cautious_ones(self):
        game = self.load({"a": {"exe": "bin/a.exe", "hold": "freeze"}})["a"]
        self.assertFalse(game.hook_clock)
        self.assertEqual(game.exe, (self.path.parent / "bin/a.exe").resolve())
        self.assertEqual(game.cwd, game.exe.parent)

    def test_reads_pause_keys(self):
        games = self.load({"a": {"exe": "a.exe", "hold": "pause_key", "pause_key": "P"},
                           "b": {"exe": "b.exe", "hold": "pause_key", "pause_key": ["ctrl", "p"],
                                 "resume_key": "f6", "pause_ms": 50, "capture": "before_pause"}})
        self.assertEqual(games["a"].pause_key, ("p",))
        self.assertEqual((games["a"].pause_ms, games["a"].capture), (100, "after_pause"))
        self.assertEqual(games["b"].pause_key, ("ctrl", "p"))
        self.assertEqual(games["b"].resume_key, ("f6",))

    def test_rejects_mistakes(self):
        pausing = {"exe": "a.exe", "hold": "pause_key"}
        for entry in ({}, {"exe": 1}, {"exe": "a.exe"}, {"exe": "a.exe", "hold": "suspend"},
                      {"exe": "a.exe", "hold": "freeze", "hok_clock": True},
                      {"exe": "a.exe", "hold": "freeze", "startup_ms": "10"},
                      {"exe": "a.exe", "hold": "freeze", "args": [1]},
                      pausing, {**pausing, "pause_key": "windows"}, {**pausing, "pause_key": ["alt", "tab"]},
                      {**pausing, "pause_key": ["ctrl", "alt", "f12"]}, {**pausing, "pause_key": 5},
                      {**pausing, "pause_key": "p", "pause_ms": 5},
                      {**pausing, "pause_key": "p", "capture": "never"},
                      {**pausing, "pause_key": "p", "hook_clock": True},
                      {"exe": "a.exe", "hold": "freeze", "pause_key": "p"},
                      {"exe": "a.exe", "hold": "none", "check_pause": False}):
            with self.subTest(entry=entry), self.assertRaises(FreezeframeError):
                self.load({"a": entry})

    def test_a_missing_hold_explains_the_choices(self):
        with self.assertRaisesRegex(FreezeframeError, "pause_key.*freeze.*none"):
            self.load({"a": {"exe": "a.exe"}})

    def test_explains_a_missing_file(self):
        with self.assertRaisesRegex(FreezeframeError, "does not exist"):
            config.load(self.path)

    def test_the_example_file_loads(self):
        self.assertTrue(config.load(support.PROJECT / "games.example.json"))


class FakeSession:
    closed = []

    def __init__(self, game):
        self.game = game
        self.frame = capture.Frame(b"png", 4, 3, 4, 3, False)
        self.process = mock.Mock(clock=None)
        self.process.alive.return_value = True

    @classmethod
    def start(cls, game):
        session = cls(game)
        return session, Result(session.frame, 0, 500)

    def step(self, segments, settle_ms):
        return Result(self.frame, sum(s.ms for s in segments), 400, state="paused",
                      warning="the picture kept changing")

    def repause(self):
        return True, Result(self.frame, 0, 0, state="paused")

    def close(self, close_game=False):
        self.closed.append(close_game)
        return "done"


def request(method, params=None, number=1):
    return {"jsonrpc": "2.0", "id": number, "method": method, "params": params or {}}


class ServerTests(unittest.TestCase):
    def setUp(self):
        FakeSession.closed = []
        self.server = Server(session_class=FakeSession)
        game = config.Game("toy", Path("toy.exe"), hold="pause_key", pause_key=("p",), notes="Arrows move.")
        patcher = mock.patch.object(config, "load", lambda: {"toy": game})
        patcher.start()
        self.addCleanup(patcher.stop)

    def call(self, name, **arguments):
        return self.server.handle(request("tools/call", {"name": name, "arguments": arguments}))["result"]

    def test_handshake(self):
        result = self.server.handle(request("initialize", {"protocolVersion": "2025-06-18"}))["result"]
        self.assertEqual(result["protocolVersion"], "2025-06-18")
        self.assertIsNone(self.server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}))
        names = [t["name"] for t in self.server.handle(request("tools/list"))["result"]["tools"]]
        self.assertEqual(names, ["list_games", "start_game", "step", "repause", "look", "end_session"])

    def test_protocol_errors(self):
        self.assertEqual(self.server.handle(request("nope"))["error"]["code"], -32601)
        reply = self.server.handle(request("tools/call", {"name": "nope"}))
        self.assertEqual(reply["error"]["code"], -32602)

    def test_tool_errors_are_reported_to_the_agent(self):
        for name, arguments in (("step", {"segments": [{"ms": 100}]}), ("start_game", {"game": "other"}),
                                ("start_game", {}), ("look", {"extra": 1})):
            with self.subTest(name=name, arguments=arguments):
                self.assertTrue(self.call(name, **arguments)["isError"])

    def test_a_whole_session(self):
        started = self.call("start_game", game="toy")
        self.assertFalse(started["isError"])
        self.assertIn("image", [part["type"] for part in started["content"]])
        self.assertIn("own pause key (p)", started["content"][-1]["text"])
        self.assertTrue(self.call("start_game", game="toy")["isError"])
        stepped = self.call("step", segments=[{"ms": 300, "keys": ["up"]}])
        summary = stepped["content"][0]["text"]
        self.assertIn("is now paused", summary)
        self.assertIn("Input played for 300 ms", summary)
        self.assertIn("Warning: the picture kept changing", summary)
        repaused = self.call("repause")
        self.assertIn("pressed again", repaused["content"][0]["text"])
        self.assertFalse(self.call("look")["isError"])
        self.assertFalse(self.call("end_session", close_game=True)["isError"])
        self.assertEqual(FakeSession.closed, [True])

    def test_hanging_up_releases_the_game(self):
        lines = [json.dumps(request("tools/call", {"name": "start_game", "arguments": {"game": "toy"}})),
                 "not json"]
        sink = io.BytesIO()
        self.server.serve(io.BytesIO(("\n".join(lines) + "\n").encode()), sink)
        self.assertEqual(FakeSession.closed, [False])
        self.assertEqual(json.loads(sink.getvalue().splitlines()[1])["error"]["code"], -32700)


class StdioTests(unittest.TestCase):
    def test_the_real_server_speaks_clean_json(self):
        with tempfile.TemporaryDirectory() as directory:
            games = Path(directory) / "games.json"
            games.write_text(json.dumps({"toy": {"exe": "toy.exe", "hold": "none", "notes": "Hi."}}),
                             encoding="utf-8")
            messages = [request("initialize", {"protocolVersion": "2025-11-25"}, 1),
                        request("tools/call", {"name": "list_games"}, 2)]
            result = subprocess.run(
                [sys.executable, str(support.PROJECT / "src" / "serve.py")],
                input="".join(json.dumps(m) + "\n" for m in messages), capture_output=True, text=True,
                timeout=30, env={**os.environ, "FREEZEFRAME_GAMES": str(games)})
        self.assertEqual(result.returncode, 0, result.stderr)
        replies = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([r["id"] for r in replies], [1, 2])
        self.assertEqual(replies[1]["result"]["content"][0]["text"], "toy: Hi.")


if __name__ == "__main__":
    unittest.main()
