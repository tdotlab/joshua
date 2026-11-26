"""One player at a time, and playing over the network. Safe: no game, screen or keyboard."""

import http.client
import io
import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import unittest
import uuid
from contextlib import redirect_stderr
from pathlib import Path
from unittest import mock

import support
from test_unit import FakeSession, request

from joshua import JoshuaError, config, network
from joshua.lock import PlayerLock
from joshua.server import ABANDONED_SECONDS, HANDOVER_SECONDS, Server

TOKEN = "t" * 43


def own_lock_name():
    """A claim of the test's own, so a real game being played is neither disturbed nor in the way."""
    return f"Local\\JoshuaTest-{uuid.uuid4().hex}"


class Clock:
    now = 1000.0

    def __call__(self):
        return self.now


class RecordingSession(FakeSession):
    started = []

    def __init__(self, game):
        super().__init__(game)
        self.steps = 0

    @classmethod
    def start(cls, game):
        cls.started.append(game.name)
        return super().start(game)

    def step(self, segments, settle_ms):
        self.steps += 1
        return super().step(segments, settle_ms)


class LockTests(unittest.TestCase):
    def test_one_holder_at_a_time(self):
        name = own_lock_name()
        first, second = PlayerLock(name), PlayerLock(name)
        self.addCleanup(first.release)
        self.addCleanup(second.release)
        self.assertTrue(first.claim())
        self.assertTrue(first.claim())
        self.assertFalse(second.claim())
        first.release()
        first.release()
        self.assertTrue(second.claim())

    def test_other_processes_are_shut_out_until_the_holder_dies(self):
        name = own_lock_name()
        code = ("import sys, time; sys.path.insert(0, sys.argv[1]); from joshua.lock import PlayerLock;"
                " lock = PlayerLock(sys.argv[2]); print(lock.claim(), flush=True); time.sleep(60)")
        holder = subprocess.Popen([sys.executable, "-c", code, str(support.PROJECT / "src"), name],
                                  stdout=subprocess.PIPE, text=True)
        try:
            self.assertEqual(holder.stdout.readline().strip(), "True")
            mine = PlayerLock(name)
            self.addCleanup(mine.release)
            self.assertFalse(mine.claim())
        finally:
            holder.kill()
            holder.communicate()
        self.assertTrue(mine.claim())


class Players(unittest.TestCase):
    """A server with two listed games, a clock the test moves and a claim of its own."""

    def setUp(self):
        FakeSession.closed = []
        RecordingSession.started = []
        self.clock = Clock()
        self.lock_name = own_lock_name()
        self.server = Server(session_class=RecordingSession, lock=PlayerLock(self.lock_name), clock=self.clock)
        games = {name: config.Game(name, Path(f"{name}.exe"), hold="pause_key", pause_key=("p",))
                 for name in ("toy", "other")}
        patcher = mock.patch.object(config, "load", lambda: games)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.server.shutdown)

    def call(self, client, name, **arguments):
        message = request("tools/call", {"name": name, "arguments": arguments})
        return self.server.handle(message, client=client)["result"]

    def said(self, result):
        return " ".join(part["text"] for part in result["content"] if part["type"] == "text")

    def free(self):
        """Whether another Joshua process could start playing now."""
        other = PlayerLock(self.lock_name)
        claimed = other.claim()
        other.release()
        return claimed


class OnePlayerTests(Players):
    def test_a_second_player_is_refused_while_the_first_is_active(self):
        self.assertFalse(self.call("a", "start_game", game="toy")["isError"])
        for name, arguments in (("start_game", {"game": "toy"}), ("start_game", {"game": "other"}),
                                ("step", {"segments": [{"ms": 100}]}), ("repause", {}), ("look", {}),
                                ("end_session", {"close_game": True})):
            with self.subTest(name=name, arguments=arguments):
                refused = self.call("b", name, **arguments)
                self.assertTrue(refused["isError"])
                self.assertIn("another player", self.said(refused))
                self.assertNotIn("image", [part["type"] for part in refused["content"]])
        self.assertFalse(self.call("b", "list_games")["isError"])
        self.assertFalse(self.call("a", "step", segments=[{"ms": 100}])["isError"])
        self.assertEqual(self.server.session.steps, 1)
        self.assertEqual(FakeSession.closed, [])

    def test_a_quiet_player_can_be_taken_over_without_restarting_the_game(self):
        self.call("a", "start_game", game="toy")
        session = self.server.session
        self.clock.now += HANDOVER_SECONDS
        looked = self.call("b", "look")
        self.assertTrue(looked["isError"])
        self.assertIn("start_game", self.said(looked))
        taken = self.call("b", "start_game", game="toy")
        self.assertFalse(taken["isError"], self.said(taken))
        self.assertIn("image", [part["type"] for part in taken["content"]])
        self.assertIs(self.server.session, session)
        self.assertEqual(RecordingSession.started, ["toy"])
        self.assertEqual(FakeSession.closed, [])
        self.assertTrue(self.call("a", "step", segments=[{"ms": 100}])["isError"])
        self.assertFalse(self.call("b", "step", segments=[{"ms": 100}])["isError"])

    def test_activity_keeps_the_turn(self):
        self.call("a", "start_game", game="toy")
        for _ in range(3):
            self.clock.now += HANDOVER_SECONDS - 1
            self.call("a", "look")
        self.assertTrue(self.call("b", "start_game", game="toy")["isError"])

    def test_taking_over_for_another_game_ends_the_old_one(self):
        self.call("a", "start_game", game="toy")
        self.clock.now += HANDOVER_SECONDS
        self.assertTrue(self.call("b", "start_game", game="nope")["isError"])
        self.assertEqual(FakeSession.closed, [])
        self.assertFalse(self.call("b", "start_game", game="other")["isError"])
        self.assertEqual(FakeSession.closed, [False])
        self.assertEqual(self.server.session.game.name, "other")

    def test_a_player_who_disconnects_can_be_replaced_at_once(self):
        self.call("a", "start_game", game="toy")
        self.server.leave("b")
        self.assertTrue(self.call("b", "start_game", game="toy")["isError"])
        self.server.leave("a")
        self.assertFalse(self.call("b", "start_game", game="toy")["isError"])
        self.assertTrue(self.call("c", "start_game", game="toy")["isError"])

    def test_an_abandoned_network_game_is_let_go(self):
        self.call("a", "start_game", game="toy")
        self.clock.now += ABANDONED_SECONDS - 1
        self.server.tidy()
        self.assertIsNotNone(self.server.session)
        self.assertFalse(self.free())
        self.clock.now += 1
        self.server.tidy()
        self.assertIsNone(self.server.session)
        self.assertEqual(FakeSession.closed, [False])
        self.assertTrue(self.free())

    def test_a_local_game_is_never_let_go_for_being_quiet(self):
        self.call(None, "start_game", game="toy")
        self.clock.now += ABANDONED_SECONDS * 10
        self.server.tidy()
        self.assertIsNotNone(self.server.session)
        self.assertFalse(self.free())

    def test_a_game_that_exited_no_longer_blocks_anyone(self):
        self.call("a", "start_game", game="toy")
        dead = self.server.session
        dead.process.alive.return_value = False
        self.server.tidy()
        self.assertTrue(self.free())
        self.assertFalse(self.call("b", "start_game", game="toy")["isError"])
        self.assertIsNot(self.server.session, dead)
        self.assertFalse(self.free())

    def test_another_process_playing_refuses_a_start(self):
        elsewhere = PlayerLock(self.lock_name)
        self.addCleanup(elsewhere.release)
        elsewhere.claim()
        refused = self.call(None, "start_game", game="toy")
        self.assertTrue(refused["isError"])
        self.assertIn("another player", self.said(refused))
        self.assertEqual(RecordingSession.started, [])

    def test_the_claim_lasts_exactly_as_long_as_the_session(self):
        self.assertTrue(self.free())
        self.call(None, "start_game", game="toy")
        self.assertFalse(self.free())
        self.call(None, "end_session")
        self.assertTrue(self.free())
        self.call(None, "start_game", game="toy")
        self.server.shutdown()
        self.assertTrue(self.free())

    def test_a_failed_start_gives_up_the_claim(self):
        with mock.patch.object(RecordingSession, "start", side_effect=JoshuaError("no window")):
            self.assertTrue(self.call("a", "start_game", game="toy")["isError"])
        self.assertTrue(self.free())
        self.assertFalse(self.call("b", "start_game", game="toy")["isError"])


class AddressTests(unittest.TestCase):
    def test_only_loopback_and_tailscale_addresses(self):
        for address in ("127.0.0.1", "::1", "100.64.0.1", "100.127.255.254", "fd7a:115c:a1e0::1"):
            self.assertTrue(network.allowed_address(address), address)
        for address in ("0.0.0.0", "::", "192.168.0.88", "10.0.0.1", "172.31.208.1", "100.63.255.255",
                        "100.128.0.1", "8.8.8.8", "fd00::1", "fe80::1%12", "localhost", ""):
            self.assertFalse(network.allowed_address(address), address)

    def test_refuses_to_listen_anywhere_else(self):
        for address in ("0.0.0.0", "192.168.0.88", ""):
            with self.assertRaises(JoshuaError):
                network.NetworkServer((address, 0), Server(), TOKEN)

    def test_finds_the_tailscale_address(self):
        def addresses(*found):
            return mock.patch.object(socket, "getaddrinfo",
                                     return_value=[(socket.AF_INET, 0, 0, "", (a, 0)) for a in found])

        with addresses("192.168.0.88", "100.72.0.5", "100.72.0.5"):
            self.assertEqual(network.tailnet_address(), "100.72.0.5")
        for found in ((), ("192.168.0.88",), ("100.72.0.5", "100.80.0.9")):
            with addresses(*found), self.assertRaises(JoshuaError):
                network.tailnet_address()

    def test_the_token_is_made_once(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "made" / "token.txt"
            token = network.load_token(path)
            self.assertGreaterEqual(len(token), 40)
            self.assertEqual(network.load_token(path), token)
            path.write_text("short\n", encoding="ascii")
            with self.assertRaises(JoshuaError):
                network.load_token(path)


class HttpTests(Players):
    def setUp(self):
        super().setUp()
        quiet = redirect_stderr(io.StringIO())
        quiet.__enter__()
        self.addCleanup(quiet.__exit__, None, None, None)
        self.http = network.NetworkServer(("127.0.0.1", 0), self.server, TOKEN)
        thread = threading.Thread(target=self.http.serve_forever, args=(0.01,), daemon=True)
        thread.start()
        self.addCleanup(self.http.server_close)
        self.addCleanup(self.http.shutdown)

    def send(self, method="POST", message=None, client=None, path="/mcp", token=TOKEN, body=None, headers=(),
             connection=None):
        sent = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
        if token:
            sent["Authorization"] = f"Bearer {token}"
        if client:
            sent["Mcp-Session-Id"] = client
        sent.update(dict(headers))
        if body is None and message is not None:
            body = json.dumps(message).encode()
        own = connection is None
        connection = connection or http.client.HTTPConnection(*self.http.server_address[:2], timeout=10)
        try:
            connection.request(method, path, body=body, headers=sent)
            reply = connection.getresponse()
            data = reply.read()
            return reply.status, dict(reply.getheaders()), json.loads(data) if data else None
        finally:
            if own:
                connection.close()

    def join(self):
        status, headers, reply = self.send(message=request("initialize", {"protocolVersion": "2025-06-18"}))
        self.assertEqual((status, reply["result"]["protocolVersion"]), (200, "2025-06-18"))
        client = headers["Mcp-Session-Id"]
        notice = {"jsonrpc": "2.0", "method": "notifications/initialized"}
        self.assertEqual(self.send(message=notice, client=client)[::2], (202, None))
        return client

    def tool(self, client, name, **arguments):
        status, _, reply = self.send(message=request("tools/call", {"name": name, "arguments": arguments}),
                                     client=client)
        self.assertEqual(status, 200)
        return reply["result"]

    def test_nothing_is_answered_without_the_token(self):
        hello = request("initialize")
        for token in (None, "wrong" * 9, TOKEN + "x", TOKEN[:-1]):
            for method in ("POST", "GET", "DELETE"):
                with self.subTest(token=token, method=method):
                    status, headers, _ = self.send(method, hello, token=token)
                    self.assertEqual(status, 401)
                    self.assertNotIn("Mcp-Session-Id", headers)
        status, _, _ = self.send(message=hello, token=None, headers={"Authorization": TOKEN})
        self.assertEqual(status, 401)
        self.assertEqual(self.http.clients, {})

    def test_web_pages_are_refused(self):
        status, _, _ = self.send(message=request("initialize"), headers={"Origin": "http://127.0.0.1"})
        self.assertEqual(status, 403)

    def test_peers_from_elsewhere_are_refused(self):
        self.assertTrue(self.http.verify_request(None, ("100.98.64.90", 5000)))
        self.assertFalse(self.http.verify_request(None, ("192.168.0.58", 5000)))

    def test_one_endpoint_and_no_event_stream(self):
        self.assertEqual(self.send(message=request("ping"), path="/")[0], 404)
        for method in ("GET", "HEAD"):
            status, headers, _ = self.send(method)
            self.assertEqual((status, headers["Allow"]), (405, "POST, DELETE"))

    def test_bad_requests(self):
        client = self.join()
        self.assertEqual(self.send(body=b"not json", client=client)[0], 400)
        self.assertEqual(self.send(body=b"[]", client=client)[0], 400)
        self.assertEqual(self.send(message=request("ping"), client=client,
                                   headers={"Content-Type": "text/plain"})[0], 415)
        self.assertEqual(self.send(headers={"Content-Length": str(network.MAX_BODY + 1)})[0], 413)
        self.assertEqual(self.send(headers={"Content-Length": "-1"})[0], 400)
        self.assertEqual(self.send(headers={"Transfer-Encoding": "chunked"})[0], 411)

    def test_sessions(self):
        self.assertEqual(self.send(message=request("ping"))[0], 400)
        self.assertEqual(self.send(message=request("ping"), client="unknown")[0], 404)
        client = self.join()
        self.assertEqual(self.send(message=request("ping"), client=client)[2]["result"], {})
        self.assertEqual(self.send("DELETE", client=client)[0], 200)
        self.assertEqual(self.send(message=request("ping"), client=client)[0], 404)
        self.assertEqual(self.send("DELETE", client=client)[0], 404)

    def test_one_connection_serves_many_requests(self):
        connection = http.client.HTTPConnection(*self.http.server_address[:2], timeout=10)
        self.addCleanup(connection.close)
        _, headers, _ = self.send(message=request("initialize"), connection=connection)
        for _ in range(3):
            status, _, _ = self.send(message=request("ping"), client=headers["Mcp-Session-Id"],
                                     connection=connection)
            self.assertEqual(status, 200)

    def test_playing_and_handing_over(self):
        first, second = self.join(), self.join()
        started = self.tool(first, "start_game", game="toy")
        self.assertIn("image", [part["type"] for part in started["content"]])
        self.assertTrue(self.tool(second, "start_game", game="toy")["isError"])
        self.assertFalse(self.tool(first, "step", segments=[{"ms": 100}])["isError"])
        self.send("DELETE", client=first)
        self.assertFalse(self.tool(second, "start_game", game="toy")["isError"])
        self.assertEqual(RecordingSession.started, ["toy"])

    def test_old_clients_are_forgotten_but_never_the_player(self):
        player = self.join()
        self.tool(player, "start_game", game="toy")
        idle = self.join()
        for _ in range(network.MAX_CLIENTS):
            self.join()
        self.assertEqual(len(self.http.clients), network.MAX_CLIENTS)
        self.assertEqual(self.send(message=request("ping"), client=idle)[0], 404)
        self.assertEqual(self.send(message=request("ping"), client=player)[0], 200)

    def test_abandoned_games_are_noticed_while_serving(self):
        self.tool(self.join(), "start_game", game="toy")
        self.clock.now += ABANDONED_SECONDS
        support.wait_for(lambda: self.server.session is None, what="the game to be let go")

    def test_the_port_cannot_be_shared(self):
        with self.assertRaises(OSError):
            network.NetworkServer(self.http.server_address[:2], self.server, TOKEN)


class NetworkProcessTests(unittest.TestCase):
    def test_the_real_server_over_the_network(self):
        with tempfile.TemporaryDirectory() as directory:
            games = Path(directory) / "games.json"
            games.write_text(json.dumps({"toy": {"exe": "toy.exe", "hold": "none", "notes": "Hi."}}),
                             encoding="utf-8")
            token_file = Path(directory) / "token.txt"
            server = subprocess.Popen(
                [sys.executable, str(support.PROJECT / "src" / "serve.py"), "--http", "--host", "127.0.0.1",
                 "--port", "0"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                env={**os.environ, "JOSHUA_GAMES": str(games), "JOSHUA_TOKEN_FILE": str(token_file)})
            watchdog = threading.Timer(30, server.kill)
            watchdog.start()
            try:
                banner = []
                while not banner or not banner[-1].startswith("Ctrl+C"):
                    line = server.stdout.readline()
                    self.assertTrue(line, f"the server stopped early: {banner}")
                    banner.append(line)
                shown = "".join(banner)
                token = token_file.read_text(encoding="ascii").strip()
                self.assertNotIn(token, shown)
                self.assertIn(str(token_file), shown)
                port = int(shown.split("http://127.0.0.1:")[1].split("/")[0])
                connection = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
                headers = {"Content-Type": "application/json", "Authorization": f"Bearer {token}"}
                connection.request("POST", "/mcp", json.dumps(request("initialize")), headers)
                reply = connection.getresponse()
                headers["Mcp-Session-Id"] = reply.getheader("Mcp-Session-Id")
                reply.read()
                connection.request("POST", "/mcp",
                                   json.dumps(request("tools/call", {"name": "list_games"})), headers)
                listed = json.loads(connection.getresponse().read())
                connection.close()
                self.assertEqual(listed["result"]["content"][0]["text"], "toy: Hi.")
            finally:
                watchdog.cancel()
                server.kill()
                server.communicate()


if __name__ == "__main__":
    unittest.main()
