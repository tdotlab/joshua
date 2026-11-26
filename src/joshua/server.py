"""A small MCP server, no dependencies: the tools, and newline-delimited JSON-RPC over stdio.

network.py serves the same tools to an agent on another machine.
"""

import argparse
import base64
import json
import math
import sys
import threading
import time
import traceback

from . import JoshuaError, __version__
from . import config, timeline
from .lock import PlayerLock
from .session import Session

PROTOCOL_VERSIONS = ("2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05")

INSTRUCTIONS = (
    "Joshua lets you play a Windows game turn by turn. Between steps the game is held still (paused"
    " with its own pause key, or frozen) and runs only during `step`, so there is no time pressure while you"
    " think; start_game says if a game is not held. Look at each screenshot, decide, then send a short step."
    " Start with short steps (200-500 ms) until you know how the game responds. Taps need about 80 ms to"
    " register. If a step reports that it stopped early or has no screenshot, the user may be using the"
    " computer: do not retry in a loop. Only games the user listed can be played."
)

HANDOVER_SECONDS = 300    # a network player quiet this long may be replaced by another
ABANDONED_SECONDS = 1800  # and one quiet this long loses the game altogether
TIDY_SECONDS = 5

ANOTHER_PLAYER = "another player is using Joshua, and only one can play at a time. Do not retry in a loop."

_STATE = {
    "frozen": "is now frozen",
    "paused": "is now paused",
    "running": "is still running",
    "unheld": "keeps running between steps",
}

_SEGMENT = {
    "type": "object",
    "properties": {
        "ms": {"type": "integer", "minimum": 10, "maximum": timeline.MAX_TOTAL_MS,
               "description": "How long this segment lasts."},
        "keys": {"type": "array", "items": {"type": "string"},
                 "description": "Keys held for the whole segment, e.g. [\"up\", \"left\"], [\"w\", \"shift\"],"
                                " [\"enter\"]. Keys not repeated in the next segment are released."},
        "buttons": {"type": "array", "items": {"enum": list(timeline.BUTTONS)},
                    "description": "Mouse buttons held for the segment."},
        "mouse_to": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                     "description": "Move the pointer to [x, y] in screenshot pixels at the segment start."},
        "mouse_move": {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2,
                       "description": "Relative mouse motion [dx, dy] spread over the segment (camera look)."},
    },
    "required": ["ms"],
    "additionalProperties": False,
}

TOOLS = [
    {"name": "list_games", "description": "List the games the user has allowed, with their notes.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "start_game",
     "description": "Start a listed game (or attach to it if already running), then hold it still and return"
                    " a screenshot. Takes over the user's screen and keyboard focus. Only one player can use"
                    " Joshua at a time: call end_session when you are done.",
     "inputSchema": {"type": "object", "properties": {"game": {"type": "string"}}, "required": ["game"],
                     "additionalProperties": False}},
    {"name": "step",
     "description": "Let the game run, play the input segments in order, hold it still again and return a"
                    " screenshot. All keys and buttons are released at the end. To just let time pass, send"
                    " one segment with only ms. The pause key of a game held by pausing cannot be used. Start"
                    " with short steps (200-500 ms) until you know how the game responds; a tap needs about 80"
                    " ms. If input stopped early or there is no screenshot, the user may be using the computer:"
                    " do not retry in a loop.",
     "inputSchema": {"type": "object", "properties": {
         "segments": {"type": "array", "items": _SEGMENT, "minItems": 1, "maxItems": timeline.MAX_SEGMENTS},
         "settle_ms": {"type": "integer", "minimum": 0, "maximum": 3000,
                       "description": "Extra time after input before freezing (default 100)."},
     }, "required": ["segments"], "additionalProperties": False}},
    {"name": "repause",
     "description": "Only for games held with their own pause key. If screenshots show the game still"
                    " running between steps, or a step warns that it may be, the pause toggle is out of step"
                    " with the game: this presses the pause key once more and returns a screenshot.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "look",
     "description": "Return the latest screenshot again without running the game.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "end_session",
     "description": "Stop playing and let go of the game. A frozen game is unfrozen; a paused one is left"
                    " paused. It stays open unless close_game is true (only games this session started can"
                    " be closed).",
     "inputSchema": {"type": "object", "properties": {"close_game": {"type": "boolean"}},
                     "additionalProperties": False}},
]


def _text(message):
    return {"type": "text", "text": message}


def _image(frame):
    return {"type": "image", "data": base64.b64encode(frame.png).decode("ascii"), "mimeType": "image/png"}


class Server:
    """At most one game, played by one client.

    A client is None over stdio, where there is only the one, and a session id over
    the network, where several may be connected.
    """

    def __init__(self, session_class=Session, lock=None, clock=time.monotonic):
        self.session = None
        self.owner = None   # the client playing the current game
        self._caller = None  # the client whose call is being answered
        self._left = False   # the owner disconnected
        self._used = 0.0     # when the owner last called
        self._clock = clock
        self._claim = lock or PlayerLock()
        self._turn = threading.RLock()  # one tool call at a time
        self._session_class = session_class

    # -- whose turn it is ------------------------------------------------------

    def _admit(self, name):
        """Refuse a call that would interfere with another client's game."""
        if name == "list_games" or not self.session or self._caller == self.owner:
            return
        if not self.session.process.alive():
            self._let_go()
            return
        waiting = HANDOVER_SECONDS - (self._clock() - self._used)
        if waiting > 0 and not self._left:
            raise JoshuaError(f"{ANOTHER_PLAYER} If that was you before reconnecting, start_game can take the"
                              f" game over in {math.ceil(waiting / 60)} min.")
        if name not in ("start_game", "end_session"):
            raise JoshuaError(f"the last player left {self.session.game.name} open; call start_game to take it"
                              " over")

    def _let_go(self, close_game=False):
        outcome = self.session.close(close_game=close_game)
        self.session = self.owner = None
        self._claim.release()
        return outcome

    def leave(self, client):
        """A network client disconnected. Its game stays held for whoever takes it over."""
        with self._turn:
            if self.session and client == self.owner:
                self._left = True

    def tidy(self):
        """Between calls: stop claiming a game that exited, and let go of an abandoned one."""
        if not self._turn.acquire(blocking=False):
            return  # a call is in progress
        try:
            if not self.session:
                return
            if not self.session.process.alive():
                self._claim.release()
            elif self.owner is not None and self._clock() - self._used >= ABANDONED_SECONDS:
                self._let_go()
        except Exception:  # report, keep serving
            traceback.print_exc(file=sys.stderr)
            self.session = self.owner = None
            self._claim.release()
        finally:
            self._turn.release()

    # -- tools -----------------------------------------------------------------

    def _require_session(self):
        if not self.session:
            raise JoshuaError("no game is being played; call start_game first")
        return self.session

    def _describe(self, result, opening=None):
        if opening:
            lines = [opening]
        elif result.state == "exited":
            lines = ["The game exited."]
        elif result.state == "unheld":
            lines = [f"The step took {result.ran_ms} ms; the game keeps running between steps."]
        else:
            lines = [f"The game ran for {result.ran_ms} ms and {_STATE[result.state]}."]
        if result.played_ms:
            lines.append(f"Input played for {result.played_ms} ms.")
        if result.stopped:
            lines.append(f"Input stopped early: {result.stopped}.")
        if result.warning:
            lines.append(f"Warning: {result.warning}")
        content = []
        if result.frame:
            lines.append(f"Screenshot is {result.frame.width}x{result.frame.height}.")
            if result.frame.blank:
                lines.append("It is entirely black. If that is unexpected, the game may be in exclusive"
                             " fullscreen, which cannot be captured; ask the user to run it windowed.")
            content.append(_image(result.frame))
        else:
            lines.append(f"No new screenshot: {result.no_frame}.")
        return [_text(" ".join(lines)), *content]

    def tool_list_games(self):
        games = config.load()
        if not games:
            return [_text("No games are listed yet.")]
        return [_text("\n".join(f"{g.name}: {g.notes or '(no notes)'}"
                                + ("" if g.launch else " [user must start it first]")
                                for g in games.values()))]

    def tool_start_game(self, game):
        alive = self.session and self.session.process.alive()
        if alive and self._caller == self.owner:
            raise JoshuaError(f"already playing {self.session.game.name}; call end_session first")
        games = config.load()
        if game not in games:
            raise JoshuaError(f"{game!r} is not listed. Allowed: {', '.join(games) or 'none'}")
        if alive and game == self.session.game.name:
            # Taking over keeps the session, and with it whether the game is paused.
            return self._started(self.session.game, self.session.step([], settle_ms=500))
        if self.session:
            self._let_go()
        if not self._claim.claim():
            raise JoshuaError(f"{ANOTHER_PLAYER} It is connected through another Joshua process on this PC.")
        try:
            self.session, result = self._session_class.start(games[game])
        except BaseException:
            self._claim.release()
            raise
        return self._started(games[game], result)

    def _started(self, game, result):
        self.owner, self._left = self._caller, False
        content = self._describe(result)
        if game.notes:
            content.append(_text(f"Notes for {game.name}: {game.notes}"))
        content.append(_text(self._holding(game)))
        return content

    def _holding(self, game):
        if game.hold == "pause_key":
            return (f"Joshua holds {game.name} still by pressing its own pause key"
                    f" ({'+'.join(game.pause_key)}) between steps, so you cannot use that key. If screenshots"
                    " show it still running between steps, call repause.")
        if game.hold == "none":
            return (f"{game.name} is not held still: it keeps running while you think. That suits games that"
                    " wait for input on their own.")
        if self.session.process.clock is None:
            return "This game is frozen between steps. Its clock is not adjusted, so it may jump forward."
        return "This game is frozen between steps, with its clock adjusted to hide the pauses."

    def tool_step(self, segments, settle_ms=100):
        session = self._require_session()
        parsed = timeline.parse(segments)
        if not isinstance(settle_ms, int) or not 0 <= settle_ms <= 3000:
            raise JoshuaError("settle_ms must be an integer from 0 to 3000")
        return self._describe(session.step(parsed, settle_ms=settle_ms))

    def tool_repause(self):
        pressed, result = self._require_session().repause()
        return self._describe(result, "The pause key was pressed again." if pressed
                              else "The pause key was not pressed.")

    def tool_look(self):
        session = self._require_session()
        if not session.frame:
            raise JoshuaError("there is no screenshot yet")
        return [_text("Latest screenshot; the game has not run since unless a step reported otherwise."),
                _image(session.frame)]

    def tool_end_session(self, close_game=False):
        self._require_session()
        return [_text(f"Session ended: {self._let_go(bool(close_game))}.")]

    def shutdown(self):
        with self._turn:
            if self.session:
                try:
                    self._let_go()
                finally:
                    self.session = self.owner = None
                    self._claim.release()

    # -- protocol --------------------------------------------------------------

    def _call_tool(self, params, client):
        with self._turn:
            self._caller = client
            try:
                return self._run_tool(params)
            finally:
                if self.session and client == self.owner:
                    self._used = self._clock()
                if self.session and not self.session.process.alive():
                    self._claim.release()

    def _run_tool(self, params):
        name = params.get("name")
        arguments = params.get("arguments") or {}
        schema = next((tool["inputSchema"] for tool in TOOLS if tool["name"] == name), None)
        if schema is None:
            raise LookupError(f"unknown tool {name!r}")
        try:
            if not isinstance(arguments, dict):
                raise JoshuaError("arguments must be an object")
            unknown = set(arguments) - set(schema["properties"])
            missing = set(schema.get("required", ())) - set(arguments)
            if unknown or missing:
                raise JoshuaError(f"bad arguments (unknown: {sorted(unknown)}, missing: {sorted(missing)})")
            self._admit(name)
            return {"content": getattr(self, f"tool_{name}")(**arguments), "isError": False}
        except JoshuaError as error:
            return {"content": [_text(str(error))], "isError": True}
        except Exception as error:  # report, keep serving
            traceback.print_exc(file=sys.stderr)
            return {"content": [_text(f"internal error: {type(error).__name__}: {error}")], "isError": True}

    def handle(self, message, client=None):
        """Return the response for one JSON-RPC message, or None for notifications."""
        if not isinstance(message, dict) or not isinstance(message.get("method"), str):
            if isinstance(message, dict) and ("result" in message or "error" in message):
                return None
            return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "invalid request"}}
        if "id" not in message:
            return None
        method = message["method"]
        params = message.get("params") or {}
        reply = {"jsonrpc": "2.0", "id": message["id"]}
        if method == "initialize":
            asked = params.get("protocolVersion")
            reply["result"] = {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "joshua", "version": __version__},
                "instructions": INSTRUCTIONS,
            }
        elif method == "ping":
            reply["result"] = {}
        elif method == "tools/list":
            reply["result"] = {"tools": TOOLS}
        elif method == "tools/call":
            try:
                reply["result"] = self._call_tool(params, client)
            except LookupError as error:
                reply["error"] = {"code": -32602, "message": str(error)}
        else:
            reply["error"] = {"code": -32601, "message": f"method not found: {method}"}
        return reply

    def serve(self, source, sink):
        try:
            for line in source:
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except (UnicodeDecodeError, json.JSONDecodeError):
                    response = {"jsonrpc": "2.0", "id": None,
                                "error": {"code": -32700, "message": "parse error"}}
                else:
                    response = self.handle(message)
                if response is not None:
                    sink.write(json.dumps(response).encode("utf-8") + b"\n")
                    sink.flush()
        finally:
            # The client hung up: give the game back to the user.
            self.shutdown()


def _keep_tidy(server):
    while True:
        time.sleep(TIDY_SECONDS)
        server.tidy()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="serve.py", description="Joshua, an MCP server for playing games."
                                     " With no arguments it speaks MCP over stdio.")
    parser.add_argument("--http", action="store_true",
                        help="serve an agent on another machine over Tailscale, until Ctrl+C")
    parser.add_argument("--host", help="with --http, the address to listen on: this PC's Tailscale address (the"
                                       " default) or loopback")
    parser.add_argument("--port", type=int, help="with --http, the port to listen on")
    options = parser.parse_args(argv)
    if not options.http and (options.host is not None or options.port is not None):
        parser.error("--host and --port need --http")

    from . import win32 as api

    api.use_physical_pixels()
    api.timeBeginPeriod(1)
    server = Server()
    if options.http:
        from . import network

        try:
            network.run(server, options.host, options.port)
        except (JoshuaError, OSError, OverflowError) as error:
            sys.exit(f"joshua: {error}")
        return
    sink = sys.stdout.buffer
    sys.stdout = sys.stderr  # nothing but protocol may reach the real stdout
    threading.Thread(target=_keep_tidy, args=(server,), daemon=True).start()
    server.serve(sys.stdin.buffer, sink)
