"""games.json: the only programs an agent is allowed to touch."""

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import JoshuaError
from . import timeline

PROJECT = Path(__file__).resolve().parents[2]

HOLDS = ("pause_key", "freeze", "none")
CAPTURES = ("after_pause", "before_pause")
_PAUSE_ONLY = ("pause_key", "resume_key", "pause_ms", "capture", "check_pause")


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
    hook_clock: bool = False     # injects a DLL; antivirus software may quarantine the game
    launch: bool = True          # False: only attach to a copy the user started
    steam_appid: int | None = None  # start through Steam instead of running exe
    window_title: str | None = None
    startup_ms: int = 3000
    max_width: int = 1280
    notes: str = ""


def config_path():
    return Path(os.environ.get("JOSHUA_GAMES") or PROJECT / "games.json")


def _expect(entry, key, kind, default, name):
    value = entry.get(key, default)
    if value is not default and (not isinstance(value, kind) or (kind is int and isinstance(value, bool))):
        raise JoshuaError(f"games.json: {name}.{key} has the wrong type")
    return value


def _chord(entry, key, name):
    """A key name, or a list of names pressed together, checked like agent input."""
    value = entry.get(key)
    if value is None:
        return ()
    names = [value] if isinstance(value, str) else value
    try:
        return timeline.parse([{"ms": 100, "keys": names}])[0].keys
    except JoshuaError as error:
        raise JoshuaError(f"games.json: {name}.{key} is not usable: {error}") from None


def _hold_settings(entry, name):
    hold = entry.get("hold")
    if hold not in HOLDS:
        raise JoshuaError(
            f"games.json: {name} needs \"hold\": how to keep the game still between steps. \"pause_key\":"
            " the game pauses itself with its own key (preferred: nothing is injected or suspended)."
            " \"freeze\": suspend the process (works for any game; antivirus software may object)."
            " \"none\": the game keeps running while the agent thinks (turn-based games).")
    if hold != "pause_key":
        stray = [key for key in _PAUSE_ONLY if key in entry]
        if stray:
            raise JoshuaError(f"games.json: {name}.{stray[0]} only applies with \"hold\": \"pause_key\"")
        return {"hold": hold}
    pause_key = _chord(entry, "pause_key", name)
    if not pause_key:
        raise JoshuaError(f"games.json: {name} uses \"hold\": \"pause_key\", so it needs \"pause_key\"")
    pause_ms = _expect(entry, "pause_ms", int, 100, name)
    if not 20 <= pause_ms <= 2000:
        raise JoshuaError(f"games.json: {name}.pause_ms must be from 20 to 2000")
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
        if not isinstance(entry, dict) or not isinstance(entry.get("exe"), str):
            raise JoshuaError(f"games.json: {name} needs an \"exe\" path")
        unknown = set(entry) - known
        if unknown:
            raise JoshuaError(f"games.json: {name} has unknown settings {sorted(unknown)}")
        holding = _hold_settings(entry, name)
        hook_clock = _expect(entry, "hook_clock", bool, False, name)
        if hook_clock and holding["hold"] != "freeze":
            raise JoshuaError(f"games.json: {name}.hook_clock only applies with \"hold\": \"freeze\"")
        exe = resolve(entry["exe"])
        cwd = _expect(entry, "cwd", str, None, name)
        args = _expect(entry, "args", list, [], name)
        if not all(isinstance(item, str) for item in args):
            raise JoshuaError(f"games.json: {name}.args must be strings")
        launch = _expect(entry, "launch", bool, True, name)
        games[name] = Game(
            name=name, exe=exe, args=tuple(args), cwd=resolve(cwd) if cwd else exe.parent,
            hook_clock=hook_clock, **holding,
            launch=launch, steam_appid=_steam_appid(entry, launch, name),
            window_title=_expect(entry, "window_title", str, None, name),
            startup_ms=_expect(entry, "startup_ms", int, 3000, name),
            max_width=_expect(entry, "max_width", int, 1280, name),
            notes=_expect(entry, "notes", str, "", name),
        )
    return games
