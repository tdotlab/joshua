"""games.json: the only programs an agent is allowed to touch."""

import hashlib
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

from . import JoshuaError
from . import timeline

PROJECT = Path(__file__).resolve().parents[2]

KINDS = ("window", "browser")
HOLDS = ("pause_key", "freeze", "none")
CAPTURES = ("after_pause", "before_pause")
_PAUSE_ONLY = ("pause_key", "resume_key", "pause_ms", "capture", "check_pause")
MIN_PAUSE_MS, MAX_PAUSE_MS = 20, 2000


@dataclass(frozen=True)
class Game:
    name: str
    exe: Path
    hold: str = "none"           # how the game is held still between steps; see HOLDS
    args: tuple = ()
    cwd: Path | None = None
    pause_key: tuple = ()        # keys pressed together to pause (and, if no resume_key, to resume)
    resume_key: tuple = ()
    pause_ms: int = 100          # how long the key is held, and how long the game gets to react
    capture: str = "after_pause"
    check_pause: bool = True     # warn if the picture keeps changing after pausing
    launch: bool = True          # False: only attach to a copy the user started
    steam_appid: int | None = None  # start through Steam instead of running exe
    window_title: str | None = None
    startup_ms: int = 3000
    max_width: int = 1280
    notes: str = ""
    kind: str = "window"


@dataclass(frozen=True)
class BrowserGame:
    """A game in a web page, played in headless Chromium and held by its clock."""

    name: str
    url: str
    origins: tuple = ()          # the only origins the page may load from; the url's own is always one
    viewport: tuple = (1280, 720)  # page size in CSS pixels, which is also the screenshot size
    frame_ms: int = 16           # game time between frames
    keep_profile: bool = False   # keep local storage (saves) between sessions
    memory_mb: int = 2048        # the browser is stopped if it uses more
    notes: str = ""
    kind: str = "browser"
    hold: str = "clock"          # always: nothing in the page runs between steps
    launch: bool = True


BROWSER_SETTINGS = ("kind", "url", "origins", "viewport", "frame_ms", "keep_profile", "memory_mb", "notes")
MIN_FRAME_MS, MAX_FRAME_MS = 16, 100
MIN_MEMORY_MB, MAX_MEMORY_MB = 256, 65536
MIN_VIEWPORT, MAX_VIEWPORT = (160, 120), (3840, 2160)


def user_dir():
    """Where Joshua keeps its own files for this user: the network token and kept browser profiles."""
    if sys.platform == "win32" and os.environ.get("LOCALAPPDATA"):
        return Path(os.environ["LOCALAPPDATA"]) / "Joshua"
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "joshua"


def profile_dir(game):
    """A kept browser profile, one per game name."""
    slug = re.sub(r"[^a-z0-9]+", "-", game.name.lower()).strip("-")[:40] or "game"
    digest = hashlib.sha256(game.name.encode("utf-8")).hexdigest()[:8]
    return user_dir() / "profiles" / f"{slug}-{digest}"


def origin_of(url, field):
    """scheme://host[:port] of an http or https address, lowercased, without a default port."""
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:
        raise JoshuaError(f"{field} is not a valid address: {url!r}") from None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise JoshuaError(f"{field} must be an http or https address, not {url!r}. A game on disk must be"
                          " served first, e.g. with python -m http.server, since a file: page has no origin to"
                          " lock to.")
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    default = {"http": 80, "https": 443}[parts.scheme]
    return f"{parts.scheme}://{host}" + ("" if port in (None, default) else f":{port}")


def config_path():
    return Path(os.environ.get("JOSHUA_GAMES") or PROJECT / "games.json")


def _expect(entry, key, kind, default, name):
    value = entry.get(key, default)
    if value is not default and (not isinstance(value, kind) or (kind is int and isinstance(value, bool))):
        raise JoshuaError(f"games.json: {name}.{key} has the wrong type")
    return value


def _chord(entry, key, name):
    """The key or keys a setting names, or none if it is absent."""
    value = entry.get(key)
    if value is None:
        return ()
    return timeline.chord(value, f"games.json: {name}.{key}")


def _hold_settings(entry, name):
    hold = entry.get("hold")
    if hold not in HOLDS:
        raise JoshuaError(
            f"games.json: {name} needs \"hold\": how to keep the game still between steps. \"pause_key\":"
            " the game pauses itself with its own key (preferred: nothing is injected or suspended)."
            " \"freeze\": suspend the process (works for any game; antivirus software may object)."
            " \"none\": the game keeps running while the agent thinks (turn-based games). To find a game's"
            " pause key, list it with \"none\" and have the agent use try_pause.")
    if hold != "pause_key":
        stray = [key for key in _PAUSE_ONLY if key in entry]
        if stray:
            raise JoshuaError(f"games.json: {name}.{stray[0]} only applies with \"hold\": \"pause_key\"")
        return {"hold": hold}
    pause_key = _chord(entry, "pause_key", name)
    if not pause_key:
        raise JoshuaError(f"games.json: {name} uses \"hold\": \"pause_key\", so it needs \"pause_key\"")
    pause_ms = _expect(entry, "pause_ms", int, 100, name)
    if not MIN_PAUSE_MS <= pause_ms <= MAX_PAUSE_MS:
        raise JoshuaError(f"games.json: {name}.pause_ms must be from {MIN_PAUSE_MS} to {MAX_PAUSE_MS}")
    capture = entry.get("capture", "after_pause")
    if capture not in CAPTURES:
        raise JoshuaError(f"games.json: {name}.capture must be \"after_pause\" or \"before_pause\"")
    return {"hold": hold, "pause_key": pause_key, "resume_key": _chord(entry, "resume_key", name),
            "pause_ms": pause_ms, "capture": capture,
            "check_pause": _expect(entry, "check_pause", bool, True, name)}


def _steam_appid(entry, launch, name):
    appid = _expect(entry, "steam_appid", int, None, name)
    if appid is None:
        return None
    if appid <= 0:
        raise JoshuaError(f"games.json: {name}.steam_appid must be a positive number")
    if not launch:
        raise JoshuaError(f"games.json: {name}.steam_appid starts the game, so it cannot be combined with"
                          " \"launch\": false")
    for key in ("args", "cwd"):
        if key in entry:
            raise JoshuaError(f"games.json: {name}.{key} does not apply with steam_appid: Steam starts the game,"
                              " so set launch options in Steam")
    return appid


def _browser_game(entry, name):
    unknown = set(entry) - set(BROWSER_SETTINGS)
    if unknown:
        window_only = sorted(unknown & set(Game.__dataclass_fields__))
        if window_only:
            raise JoshuaError(f"games.json: {name}.{window_only[0]} does not apply to a browser game: it is"
                              " held by its clock, in a browser Joshua starts")
        raise JoshuaError(f"games.json: {name} has unknown settings {sorted(unknown)}")
    url = entry.get("url")
    if not isinstance(url, str):
        raise JoshuaError(f"games.json: {name} is a browser game, so it needs a \"url\"")
    own = origin_of(url, f"games.json: {name}.url")
    origins = _expect(entry, "origins", list, [], name)
    if not all(isinstance(item, str) for item in origins):
        raise JoshuaError(f"games.json: {name}.origins must be a list of addresses")
    allowed = [own]
    for item in origins:
        origin = origin_of(item, f"games.json: {name}.origins")
        if origin not in allowed:
            allowed.append(origin)
    viewport = _expect(entry, "viewport", list, [1280, 720], name)
    if (len(viewport) != 2 or not all(isinstance(n, int) and not isinstance(n, bool) for n in viewport)
            or not all(low <= n <= high for n, low, high in zip(viewport, MIN_VIEWPORT, MAX_VIEWPORT))):
        raise JoshuaError(f"games.json: {name}.viewport must be [width, height], from {list(MIN_VIEWPORT)} to"
                          f" {list(MAX_VIEWPORT)}")
    frame_ms = _expect(entry, "frame_ms", int, 16, name)
    if not MIN_FRAME_MS <= frame_ms <= MAX_FRAME_MS:
        raise JoshuaError(f"games.json: {name}.frame_ms must be from {MIN_FRAME_MS} to {MAX_FRAME_MS}")
    memory_mb = _expect(entry, "memory_mb", int, 2048, name)
    if not MIN_MEMORY_MB <= memory_mb <= MAX_MEMORY_MB:
        raise JoshuaError(f"games.json: {name}.memory_mb must be from {MIN_MEMORY_MB} to {MAX_MEMORY_MB}")
    return BrowserGame(name=name, url=url, origins=tuple(allowed), viewport=tuple(viewport), frame_ms=frame_ms,
                       keep_profile=_expect(entry, "keep_profile", bool, False, name), memory_mb=memory_mb,
                       notes=_expect(entry, "notes", str, "", name))


def load(path=None):
    """Read the allowlist. It is re-read on every use, so edits apply immediately."""
    path = Path(path) if path else config_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise JoshuaError(f"{path} does not exist. The user must create it (see games.example.json);"
                               " only games listed there can be played.") from None
    except (OSError, json.JSONDecodeError) as error:
        raise JoshuaError(f"cannot read {path}: {error}") from None
    if not isinstance(data, dict):
        raise JoshuaError("games.json must be an object mapping names to games")

    def resolve(value):
        expanded = Path(os.path.expandvars(value))
        return expanded if expanded.is_absolute() else (path.parent / expanded).resolve()

    games = {}
    known = set(Game.__dataclass_fields__) - {"name"}
    for name, entry in data.items():
        if not isinstance(entry, dict):
            raise JoshuaError(f"games.json: {name} must be an object")
        kind = entry.get("kind", "window")
        if kind not in KINDS:
            raise JoshuaError(f"games.json: {name}.kind must be \"window\" (a program) or \"browser\" (a web"
                              " page)")
        if kind == "browser":
            games[name] = _browser_game(entry, name)
            continue
        if not isinstance(entry.get("exe"), str):
            raise JoshuaError(f"games.json: {name} needs an \"exe\" path")
        unknown = set(entry) - known
        if unknown:
            raise JoshuaError(f"games.json: {name} has unknown settings {sorted(unknown)}")
        holding = _hold_settings(entry, name)
        exe = resolve(entry["exe"])
        cwd = _expect(entry, "cwd", str, None, name)
        args = _expect(entry, "args", list, [], name)
        if not all(isinstance(item, str) for item in args):
            raise JoshuaError(f"games.json: {name}.args must be strings")
        launch = _expect(entry, "launch", bool, True, name)
        games[name] = Game(
            name=name, exe=exe, args=tuple(args), cwd=resolve(cwd) if cwd else exe.parent,
            **holding, launch=launch, steam_appid=_steam_appid(entry, launch, name),
            window_title=_expect(entry, "window_title", str, None, name),
            startup_ms=_expect(entry, "startup_ms", int, 3000, name),
            max_width=_expect(entry, "max_width", int, 1280, name),
            notes=_expect(entry, "notes", str, "", name),
        )
    return games
