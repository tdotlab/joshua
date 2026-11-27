"""MCP over streamable HTTP, for an agent on another machine of the user's tailnet.

Every POST is answered with one JSON reply, and the server never sends anything of
its own accord, so there is no event stream. Whoever can reach this can take
over the screen and keyboard within the listed games, hence: loopback or
Tailscale addresses only, a secret token, and no requests from web pages.
"""

import hmac
import ipaddress
import json
import os
import secrets
import socket
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import JoshuaError
from . import config

DEFAULT_PORT = 8737
ENDPOINT = "/mcp"
MAX_BODY = 1 << 20
MAX_CLIENTS = 16
TAILNET = (ipaddress.ip_network("100.64.0.0/10"), ipaddress.ip_network("fd7a:115c:a1e0::/48"))


def allowed_address(address):
    """Loopback or Tailscale: never the local network or the internet."""
    try:
        ip = ipaddress.ip_address(address)
    except ValueError:
        return False
    return ip.is_loopback or any(ip in network for network in TAILNET)


def tailnet_address():
    """This PC's Tailscale IPv4 address."""
    own = socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET)
    found = sorted({entry[4][0] for entry in own if ipaddress.ip_address(entry[4][0]) in TAILNET[0]})
    if not found:
        raise JoshuaError("this PC has no Tailscale address; start Tailscale, or choose an address with --host")
    if len(found) > 1:
        raise JoshuaError(f"several addresses look like Tailscale's ({', '.join(found)}); choose one with --host")
    return found[0]


def token_path():
    chosen = os.environ.get("JOSHUA_TOKEN_FILE")
    return Path(chosen) if chosen else config.user_dir() / "token.txt"


def load_token(path):
    """The secret a client must send, made on first use."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        # Readable by this user only where the mode applies (not on Windows, where the folder is private).
        with open(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "w", encoding="ascii") as file:
            file.write(secrets.token_urlsafe(32) + "\n")
    except FileExistsError:
        pass
    token = path.read_text(encoding="utf-8-sig").strip()
    if len(token) < 32 or not token.isascii():
        raise JoshuaError(f"{path} should hold at least 32 plain characters; delete it to have a new token made")
    return token


class NetworkServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False  # on Windows this would let a second program share the port

    def __init__(self, address, player, token):
        if not allowed_address(address[0]):
            raise JoshuaError(f"refusing to listen on {address[0]!r}: only this PC's Tailscale address or"
                              " loopback")
        self.address_family = socket.AF_INET6 if ":" in address[0] else socket.AF_INET
        self.player = player
        self.token = token.encode("ascii")
        self.clients = {}  # session ids, least recently heard from first
        self._clients_lock = threading.Lock()
        super().__init__(address, _Handler)

    def server_bind(self):
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def verify_request(self, request, client_address):
        return allowed_address(client_address[0])

    def service_actions(self):
        self.player.tidy()

    def handle_error(self, request, client_address):
        if not isinstance(sys.exc_info()[1], (ConnectionError, TimeoutError)):
            super().handle_error(request, client_address)

    def open_client(self):
        client = secrets.token_hex(16)
        with self._clients_lock:
            self.clients[client] = None
            while len(self.clients) > MAX_CLIENTS:
                del self.clients[next(old for old in self.clients if old != self.player.owner)]
        return client

    def knows(self, client):
        with self._clients_lock:
            if client not in self.clients:
                return False
            self.clients[client] = self.clients.pop(client)
        return True

    def close_client(self, client):
        with self._clients_lock:
            self.clients.pop(client, None)
        self.player.leave(client)


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "joshua"
    sys_version = ""
    timeout = 30

    def do_POST(self):
        self._answer()

    def do_GET(self):
        self._answer()

    def do_HEAD(self):
        self._answer()

    def do_DELETE(self):
        self._answer()

    def _reply(self, status, payload=None, headers=()):
        body = b"" if payload is None else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in headers:
            self.send_header(name, value)
        if status >= 400:
            self.send_header("Connection", "close")  # the request may not have been read to its end
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _refuse(self, status, message, headers=()):
        self._reply(status, {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": message}},
                    headers)

    def _answer(self):
        # The body is read first: closing a connection with unread data can lose the reply on Windows.
        if self.headers.get("Transfer-Encoding"):
            return self._refuse(411, "send a Content-Length")
        length = self.headers.get("Content-Length", "0")
        if not (length.isascii() and length.isdigit()):
            return self._refuse(400, "bad Content-Length")
        if int(length) > MAX_BODY:
            return self._refuse(413, "request too large")
        body = self.rfile.read(int(length))
        if self.headers.get("Origin") is not None:
            return self._refuse(403, "requests from web pages are not accepted")
        scheme, _, token = self.headers.get("Authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not hmac.compare_digest(token.strip().encode("utf-8"), self.server.token):
            print(f"joshua: refused {self.client_address[0]}: missing or wrong token", file=sys.stderr)
            return self._refuse(401, "missing or wrong token", [("WWW-Authenticate", "Bearer")])
        if self.path != ENDPOINT:
            return self._refuse(404, f"the endpoint is {ENDPOINT}")
        if self.command == "POST":
            return self._post(body)
        if self.command == "DELETE":
            return self._delete()
        return self._refuse(405, "no event stream is offered", [("Allow", "POST, DELETE")])

    def _post(self, body):
        if self.headers.get_content_type() != "application/json":
            return self._refuse(415, "send application/json")
        try:
            message = json.loads(body)
        except (UnicodeDecodeError, json.JSONDecodeError):
            return self._refuse(400, "parse error")
        if not isinstance(message, dict):
            return self._refuse(400, "send one JSON-RPC message")
        # The MCP-Protocol-Version header is not enforced: some clients send the newest version they
        # know, whatever was agreed.
        headers = []
        if message.get("method") == "initialize":
            client = self.server.open_client()
            headers.append(("Mcp-Session-Id", client))
        else:
            client = self.headers.get("Mcp-Session-Id")
            if not client:
                return self._refuse(400, "initialize first: no Mcp-Session-Id")
            if not self.server.knows(client):
                return self._refuse(404, "session not found")
        if message.get("method") == "tools/call":
            params = message.get("params")
            name = params.get("name") if isinstance(params, dict) else None
            print(f"joshua: {self.client_address[0]} calls {str(name)[:40]!r}", file=sys.stderr)
        response = self.server.player.respond(message, client=client)
        if response is None:
            return self._reply(202)
        return self._reply(200, response, headers)

    def _delete(self):
        client = self.headers.get("Mcp-Session-Id")
        if not client or not self.server.knows(client):
            return self._refuse(404, "session not found")
        self.server.close_client(client)
        return self._reply(200)

    def log_message(self, format, *args):
        pass  # one line per tool call and refused token is printed instead


def run(player, host=None, port=None):
    """Serve until Ctrl+C, then let go of any game."""
    path = token_path()
    token = load_token(path)
    server = NetworkServer((host or tailnet_address(), DEFAULT_PORT if port is None else port), player, token)
    with server:
        address, bound = server.server_address[:2]
        shown = f"[{address}]" if ":" in address else address
        print(f"Joshua is listening at http://{shown}:{bound}{ENDPOINT}\n"
              f"Clients send the token in {path} as a bearer token.\n"
              "Ctrl+C stops it and lets go of the game.", flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            player.shutdown()
