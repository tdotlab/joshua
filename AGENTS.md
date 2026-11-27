# Joshua

MCP server that lets agents play Windows games with the game held still between
turns (its own pause key, or process suspension).
Read [README.md](README.md), then [docs/STATUS.md](docs/STATUS.md).

## Map

- `src/serve.py`: entry point
- `src/joshua/`: `server` (MCP tools, stdio, whose turn it is), `network` (HTTP
  over Tailscale), `lock` (one player per PC), `session` (one game turn), `hold`
  (pause key / freeze / none), `trial` (finding a game's pause key), `process`
  (launch, freeze), `window`, `keyboard`, `capture`, `timeline`, `config`,
  `win32` (all ctypes declarations)
- `tests/`: `test_unit.py` and `test_network.py` are safe; `test_live.py` plays
  `pausebox.py` with real input (opt-in)
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how it works and why
- [docs/BROWSER.md](docs/BROWSER.md): proposed browser-game backend (not built)

## Commands

```powershell
python -m unittest discover -s tests -v
```

Live tests take the screen and keyboard for about a minute; only run them when
the user is not at the keyboard or has agreed:

```powershell
$env:JOSHUA_LIVE = "1"; python -m unittest discover -s tests -p test_live.py -v
```

## Rules

- Ask the user before running anything that suspends a process (a game with
  `"hold": "freeze"`) or plays a real game. Antivirus software may object; see
  README.md.
- Never change antivirus settings, and never alter code or builds to avoid
  detection.
- Keep the safety properties: allowlisted games only, input only to the focused
  game, everything released after a step, no pixels or titles from other windows,
  one player at a time. The network server listens only on loopback or Tailscale
  and requires its token; never print or log the token.
- Tests use their own claim name, never the real `PlayerLock()` of an in-process
  `Server`.
- Offline single-player games only.
