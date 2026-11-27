"""The MCP protocol layer, on any platform: what a client can send and what comes back."""

import io
import json
import unittest
from unittest import mock

import support  # noqa: F401  (the import path)
from joshua import capture, config
from joshua.result import Result
from joshua.server import Server


class QuietSession:
    """A browser-kind session that does nothing, so no claim is taken and nothing is started."""

    closed = []

    def __init__(self, game):
        self.game = game
        self.frame = capture.Frame(b"png", 4, 3, 4, 3, False)
        self.process = self
        self.dead = False

    def alive(self):
        return not self.dead

    @classmethod
    def start(cls, game):
        session = cls(game)
        return session, Result(session.frame, 0, 100, state="clock")

    def step(self, segments, settle_ms):
        return Result(self.frame, 0, 100, state="clock")

    def close(self, close_game=False):
        type(self).closed.append(self)
        return "closed"


def request(method, params, number=1):
    message = {"jsonrpc": "2.0", "id": number, "method": method}
    if params is not ...:
        message["params"] = params
    return message


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        QuietSession.closed = []
        self.server = Server(session_class=QuietSession, lock=mock.Mock())
        self.addCleanup(self.server.shutdown)
        game = config.BrowserGame(name="web", url="http://127.0.0.1:8000/", origins=("http://127.0.0.1:8000",))
        patcher = mock.patch.object(config, "load", lambda: {"web": game})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_params_that_are_not_an_object_are_refused(self):
        for method in ("initialize", "tools/call", "tools/list", "ping"):
            for params in ("x", [1], 5, True):
                with self.subTest(method=method, params=params):
                    reply = self.server.handle(request(method, params))
                    self.assertEqual(reply["error"]["code"], -32602)
                    self.assertEqual(reply["id"], 1)

    def test_missing_or_null_params_are_fine(self):
        for params in (..., None, {}):
            with self.subTest(params=params):
                self.assertIn("result", self.server.handle(request("ping", params)))
                self.assertIn("result", self.server.handle(request("initialize", params)))

    def test_a_tool_name_that_is_not_a_string_is_refused(self):
        for name in (None, 5, ["step"], {"a": 1}):
            with self.subTest(name=name):
                reply = self.server.handle(request("tools/call", {"name": name}))
                self.assertEqual(reply["error"]["code"], -32602)

    def test_serving_survives_bad_messages(self):
        lines = [json.dumps(request("tools/call", "x", 1)), json.dumps(request("ping", [1], 2)),
                 json.dumps([request("ping", {}, 3)]), json.dumps(request("ping", {}, 4))]
        sink = io.BytesIO()
        self.server.serve(io.BytesIO(("\n".join(lines) + "\n").encode()), sink)
        replies = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual([reply.get("id") for reply in replies], [1, 2, None, 4])
        self.assertEqual(replies[-1]["result"], {})

    def test_an_unexpected_failure_is_answered_and_serving_goes_on(self):
        real = self.server.handle
        calls = []

        def flaky(message, client=None):
            calls.append(message)
            if len(calls) == 1:
                raise RuntimeError("surprise")
            return real(message, client)

        sink = io.BytesIO()
        with mock.patch.object(self.server, "handle", flaky):
            self.server.serve(io.BytesIO((json.dumps(request("ping", {}, 7)) + "\n"
                                          + json.dumps(request("ping", {}, 8)) + "\n").encode()), sink)
        replies = [json.loads(line) for line in sink.getvalue().splitlines()]
        self.assertEqual(replies[0]["id"], 7)
        self.assertEqual(replies[0]["error"]["code"], -32603)
        self.assertNotIn("surprise", replies[0]["error"]["message"])
        self.assertEqual(replies[1], {"jsonrpc": "2.0", "id": 8, "result": {}})


class TidyTests(unittest.TestCase):
    def setUp(self):
        QuietSession.closed = []
        self.server = Server(session_class=QuietSession, lock=mock.Mock())
        self.addCleanup(self.server.shutdown)
        game = config.BrowserGame(name="web", url="http://127.0.0.1:8000/", origins=("http://127.0.0.1:8000",))
        patcher = mock.patch.object(config, "load", lambda: {"web": game})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.server.handle(request("tools/call", {"name": "start_game", "arguments": {"game": "web"}}))

    def test_a_session_that_fails_while_being_tidied_is_still_closed(self):
        session = self.server.session
        session.process = mock.Mock()
        session.process.alive.side_effect = RuntimeError("broken")
        with mock.patch("sys.stderr", io.StringIO()):
            self.server.tidy()
        self.assertIsNone(self.server.session)
        self.assertEqual(QuietSession.closed, [session])


if __name__ == "__main__":
    unittest.main()
