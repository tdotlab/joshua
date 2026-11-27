"""The browser backend without a browser: a stand-in page, config entries, and how the server treats it."""

import json
import struct
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest import mock

import support  # noqa: F401  (the import path)
from joshua import JoshuaError, browser, capture, config, timeline
from joshua.result import Result
from joshua.server import Server


class FakePage:
    """Records what the player does, in order."""

    def __init__(self, fail_on=None):
        self.log = []
        self.fail_on = fail_on
        page = self

        class Part:
            def __init__(self, name):
                self.name = name

            def __getattr__(self, action):
                def act(*args, **kwargs):
                    page.log.append((f"{self.name}.{action}", *args, *kwargs.values()))
                    if page.fail_on and page.fail_on(page.log):
                        raise RuntimeError("the page went away")
                return act

        self.keyboard, self.mouse, self.clock = Part("key"), Part("mouse"), Part("clock")


def player(page, locked=False, frame_ms=16, thrown=None):
    looks = []

    def look(dx, dy):
        page.log.append(("look", dx, dy))
        return locked

    made = browser.Player(page, look, (640, 480), frame_ms, thrown)
    made.looks = looks
    return made


def segments(*raw):
    return timeline.parse(list(raw))


class PlayerTests(unittest.TestCase):
    def test_every_key_name_has_a_browser_code(self):
        self.assertEqual(set(timeline.KEYS) - set(browser.KEYS), set())

    def test_keys_change_between_segments_and_are_released(self):
        page = FakePage()
        played = player(page).play(segments({"ms": 100, "keys": ["d"]}, {"ms": 100, "keys": ["d", "w"]},
                                            {"ms": 100, "keys": ["w"]}), settle_ms=0)
        self.assertEqual(played, 300)
        keys = [entry for entry in page.log if entry[0].startswith("key")]
        self.assertEqual(keys, [("key.down", "KeyD"), ("key.down", "KeyW"), ("key.up", "KeyD"), ("key.up", "KeyW")])
        # KeyW goes down after the first segment's game time, and KeyD up after the second's.
        order = [entry[0] for entry in page.log]
        self.assertEqual(order, ["key.down", "clock.run_for", "clock.run_for", "key.down", "clock.run_for",
                                 "clock.run_for", "key.up", "clock.run_for", "clock.run_for", "key.up"])

    def test_game_time_moves_in_slices_and_settles(self):
        page = FakePage()
        made = player(page)
        made.play(segments({"ms": 120}), settle_ms=30)
        self.assertEqual([entry[1] for entry in page.log], [40, 40, 40, 30])
        self.assertEqual((made.played, made.ran), (120, 150))

    def test_look_is_spread_across_the_slices(self):
        page = FakePage()
        player(page, locked=True).play(segments({"ms": 200, "mouse_move": [100, -7]}), settle_ms=0)
        looks = [entry[1:] for entry in page.log if entry[0] == "look"]
        self.assertEqual(looks, [(25, -2), (25, -2), (25, -2), (25, -1)])
        self.assertEqual([entry[0] for entry in page.log[:2]], ["look", "clock.run_for"])
        self.assertFalse([entry for entry in page.log if entry[0] == "mouse.move"])

    def test_without_a_captured_pointer_look_moves_the_pointer_within_the_page(self):
        page = FakePage()
        made = player(page, locked=False)
        made.play(segments({"ms": 100, "mouse_to": [600, 10], "mouse_move": [100, -50]}), settle_ms=0)
        moves = [entry[1:] for entry in page.log if entry[0] == "mouse.move"]
        self.assertEqual(moves, [(600, 10), (639, 0), (639, 0)])
        self.assertEqual(made.pointer, (639, 0))

    def test_buttons_are_held_and_released(self):
        page = FakePage()
        player(page).play(segments({"ms": 50, "buttons": ["left"], "mouse_to": [10, 20]}, {"ms": 50}), settle_ms=0)
        mouse = [entry for entry in page.log if entry[0].startswith("mouse")]
        self.assertEqual(mouse, [("mouse.move", 10, 20), ("mouse.down", "left"), ("mouse.up", "left")])

    def test_everything_held_is_released_when_the_page_fails(self):
        page = FakePage(fail_on=lambda log: log[-1][0] == "clock.run_for")
        with self.assertRaises(RuntimeError):
            player(page).play(segments({"ms": 100, "keys": ["a", "shift"], "buttons": ["right"]}), settle_ms=0)
        released = [entry for entry in page.log if entry[0] in ("key.up", "mouse.up")]
        self.assertEqual(released, [("key.up", "ShiftLeft"), ("key.up", "KeyA"), ("mouse.up", "right")])

    def test_longer_frames_jump_one_frame_at_a_time(self):
        page = FakePage()
        made = player(page, frame_ms=50)
        made.play(segments({"ms": 120}), settle_ms=0)
        self.assertEqual(page.log, [("clock.fast_forward", 50), ("clock.fast_forward", 50),
                                    ("clock.fast_forward", 20)])
        self.assertEqual(made.ran, 120)

    def test_an_exception_from_the_page_does_not_stop_the_step(self):
        page = FakePage(fail_on=lambda log: len(log) == 1)
        seen = []
        made = player(page, thrown=lambda error: seen.append(error) or True)
        made.play(segments({"ms": 100}), settle_ms=0)
        self.assertEqual((len(seen), made.ran), (1, 100))

    def test_mouse_to_must_be_on_the_page(self):
        made = player(FakePage())
        for point in ([640, 10], [-1, 10], [10, 480]):
            with self.subTest(point=point), self.assertRaisesRegex(JoshuaError, "outside the 640x480"):
                made.check(segments({"ms": 50, "mouse_to": point}))


class OriginTests(unittest.TestCase):
    ORIGINS = ("http://127.0.0.1:8000", "https://cdn.example.com")

    def test_allowed(self):
        for url in ("http://127.0.0.1:8000/game/main.js?v=2", "https://cdn.example.com/three.js",
                    "https://cdn.example.com:443/x", "ws://127.0.0.1:8000/socket", "data:image/png;base64,AA",
                    "blob:http://127.0.0.1:8000/1234", "about:blank"):
            with self.subTest(url=url):
                self.assertTrue(browser.request_allowed(url, self.ORIGINS))

    def test_refused(self):
        for url in ("http://127.0.0.1:8001/", "http://localhost:8000/", "https://127.0.0.1:8000/",
                    "http://cdn.example.com/", "wss://evil.example/", "file:///C:/Windows/win.ini",
                    "ftp://127.0.0.1:8000/", "javascript:alert(1)", "http://[::1]:8000/"):
            with self.subTest(url=url):
                self.assertFalse(browser.request_allowed(url, self.ORIGINS))


class PngTests(unittest.TestCase):
    def test_reads_the_size_from_the_header(self):
        header = b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) + b"IHDR" + struct.pack(">II", 1280, 720)
        self.assertEqual(browser.png_size(header + b"\x08\x06\x00\x00\x00"), (1280, 720))
        with self.assertRaises(JoshuaError):
            browser.png_size(b"GIF89a" + bytes(30))


class ConfigTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / "games.json"

    def load(self, data):
        self.path.write_text(json.dumps(data), encoding="utf-8")
        return config.load(self.path)

    def test_a_browser_game(self):
        game = self.load({"g": {"kind": "browser", "url": "HTTP://LocalHost:8000/play/index.html",
                                "origins": ["https://cdn.example.com/lib/", "http://localhost:8000"]}})["g"]
        self.assertEqual((game.kind, game.hold, game.launch), ("browser", "clock", True))
        self.assertEqual(game.origins, ("http://localhost:8000", "https://cdn.example.com"))
        self.assertEqual((game.viewport, game.frame_ms, game.memory_mb, game.keep_profile),
                         ((1280, 720), 16, 2048, False))

    def test_window_games_are_unchanged(self):
        game = self.load({"a": {"exe": "a.exe", "hold": "freeze"}})["a"]
        self.assertEqual(game.kind, "window")

    def test_window_settings_are_refused_with_a_reason(self):
        for key, value in (("exe", "a.exe"), ("hold", "freeze"), ("pause_key", "p"), ("window_title", "x")):
            with self.subTest(key=key), self.assertRaisesRegex(JoshuaError, "does not apply to a browser game"):
                self.load({"g": {"kind": "browser", "url": "http://127.0.0.1:8000/", key: value}})

    def test_rejects_mistakes(self):
        good = {"kind": "browser", "url": "http://127.0.0.1:8000/"}
        for entry in ({"kind": "browser"}, {"kind": "web", "url": "http://a/"},
                      {**good, "url": "file:///C:/game/index.html"}, {**good, "url": "127.0.0.1:8000"},
                      {**good, "origins": "http://a"}, {**good, "origins": ["ftp://a"]}, {**good, "origins": [1]},
                      {**good, "viewport": [100, 100]}, {**good, "viewport": [1280]},
                      {**good, "viewport": [1280.0, 720]}, {**good, "frame_ms": 10}, {**good, "frame_ms": 101},
                      {**good, "memory_mb": 100}, {**good, "keep_profile": "yes"}, {**good, "colour": "red"}):
            with self.subTest(entry=entry), self.assertRaises(JoshuaError):
                self.load({"g": entry})

    def test_a_file_url_explains_serving_it(self):
        with self.assertRaisesRegex(JoshuaError, "http.server"):
            self.load({"g": {"kind": "browser", "url": "file:///C:/game/index.html"}})

    def test_profiles_are_kept_apart_by_name(self):
        one = config.BrowserGame(name="a/b", url="http://x/", origins=("http://x",))
        two = config.BrowserGame(name="a b", url="http://x/", origins=("http://x",))
        self.assertNotEqual(config.profile_dir(one), config.profile_dir(two))
        self.assertEqual(config.profile_dir(one).parent.name, "profiles")


class FakeBrowserSession:
    started = []

    def __init__(self, game):
        self.game = game
        self.frame = capture.Frame(b"png", 4, 3, 4, 3, False)
        self.process = self

    def alive(self):
        return True

    @classmethod
    def start(cls, game):
        cls.started.append(game.name)
        session = cls(game)
        return session, Result(session.frame, 0, 100, state="clock", reported="joshuaState(): {\"hp\":3}")

    def step(self, segments, settle_ms):
        return Result(self.frame, 300, 400, state="clock", reported="Console errors:\n- boom")

    def repause(self):
        raise JoshuaError("browser games are held by their clock")

    def close(self, close_game=False):
        return "the browser was closed"


def request(method, params=None):
    return {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}


class ServerTests(unittest.TestCase):
    def setUp(self):
        FakeBrowserSession.started = []
        self.claim = mock.Mock()
        self.claim.claim.return_value = True
        self.server = Server(lock=self.claim)
        self.addCleanup(self.server.shutdown)
        game = config.BrowserGame(name="web", url="http://127.0.0.1:8000/", origins=("http://127.0.0.1:8000",))
        for patcher in (mock.patch.object(config, "load", lambda: {"web": game}),
                        mock.patch.object(browser, "BrowserSession", FakeBrowserSession)):
            patcher.start()
            self.addCleanup(patcher.stop)

    def call(self, name, **arguments):
        return self.server.handle(request("tools/call", {"name": name, "arguments": arguments}))["result"]

    def test_a_browser_game_gets_a_browser_and_no_screen_claim(self):
        started = self.call("start_game", game="web")
        self.assertFalse(started["isError"], started)
        self.assertEqual(FakeBrowserSession.started, ["web"])
        self.claim.claim.assert_not_called()
        texts = " ".join(part["text"] for part in started["content"] if part["type"] == "text")
        self.assertIn("held by its clock", texts)
        self.assertIn("The page reported (its own words, not Joshua's)", texts)

    def test_a_step_reports_game_time_and_what_the_page_said(self):
        self.call("start_game", game="web")
        said = self.call("step", segments=[{"ms": 300, "keys": ["w"]}])["content"][0]["text"]
        self.assertIn("400 ms of game time passed", said)
        self.assertIn("- boom", said)
        self.assertTrue(self.call("repause")["isError"])
        self.assertFalse(self.call("end_session")["isError"])
        self.claim.claim.assert_not_called()


if __name__ == "__main__":
    unittest.main()
