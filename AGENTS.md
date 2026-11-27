# Joshua

MCP server that lets agents play Windows games with the game held still between
turns (its own pause key, or process suspension), and web-page games in headless
Chromium held by Playwright's clock (Windows or Linux).
Read [README.md](README.md), then [docs/STATUS.md](docs/STATUS.md).

## Map

- `src/serve.py`: entry point
- `src/joshua/`: `server` (MCP tools, stdio, whose turn it is), `network` (HTTP
  over Tailscale), `lock` (one player per PC), `session` (one game turn), `hold`
  (pause key / freeze / none), `trial` (finding a game's pause key), `process`
  (launch, freeze), `window`, `keyboard`, `capture`, `timeline`, `config`,
  `win32` (all ctypes declarations), `result` (what a turn returns), `browser`
  (browser games), `proctree` (process tree and memory, Windows and Linux)
- `tests/`: `test_unit.py`, `test_network.py`, `test_browser.py` and
  `test_server.py` are safe (the first two skip off Windows); `test_live.py` plays `pausebox.py` with real
  input (opt-in); `test_browser_live.py` plays `browserpage.html` headless,
  whenever Playwright is installed
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how it works and why
- [docs/BROWSER.md](docs/BROWSER.md): browser-game design, measurements and
  what building it found

## Commands

```powershell
python -m unittest discover -s tests -v
```

Live tests take the screen and keyboard for about a minute; only run them when
the user is not at the keyboard or has agreed:

```powershell
$env:JOSHUA_LIVE = "1"; python -m unittest discover -s tests -p test_live.py -v
```

Browser tests need a Python with Playwright (here, the project's `.venv`); they
are headless and safe to run any time:

```powershell
.venv\Scripts\python.exe -m unittest discover -s tests -p "test_browser*.py" -v
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
- Browser games: keep Chromium's sandbox on; never set
  `JOSHUA_BROWSER_NO_SANDBOX` outside a throwaway container. Keep the origin
  lock, the memory watchdog and closing the browser on every exit path.
