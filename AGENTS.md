# Joshua

MCP server that lets agents play Windows games with the game held still between
turns (its own pause key, or process suspension).
Read [README.md](README.md), then [docs/STATUS.md](docs/STATUS.md).

## Map

- `src/serve.py`: entry point
- `src/joshua/`: `server` (MCP), `session` (one game turn), `hold` (pause
  key / freeze / none), `process` (launch, freeze, clock), `window`, `keyboard`,
  `capture`, `timeline`, `config`, `win32` (all ctypes declarations)
- `native/`: `jclock.c` (clock hook), `jinject.c`, `ticker.c` (test game)
- `tests/`: `test_unit.py` is safe; `test_live.py` plays `pausebox.py` with real
  input (opt-in); `test_clock.py` is native and opt-in
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): how it works and why

## Commands

```powershell
python -m unittest discover -s tests -v
```

Live tests take the screen and keyboard for about ten seconds; only run them when
the user is not at the keyboard or has agreed:

```powershell
$env:JOSHUA_LIVE = "1"; python -m unittest discover -s tests -p test_live.py -v
```

```powershell
.\build.ps1
```

## Rules

- Standard library only, in Python and C.
- Ask the user before running anything that injects or suspends a process
  (`JOSHUA_NATIVE=1`, `hook_clock`, a real game). Defender has quarantined
  the test target; see README.md.
- Never change antivirus settings, and never alter code or builds to avoid
  detection.
- Keep the safety properties: allowlisted games only, input only to the focused
  game, everything released after a step, no pixels or titles from other windows.
- Offline single-player games only.
- In `jclock.c`, hooks read the real clock before the offset. See ARCHITECTURE.
