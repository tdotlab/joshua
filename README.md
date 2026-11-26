# Freezeframe

An MCP server that lets an agent play a Windows game turn by turn. The game is
held still while the model thinks, runs only while the model's input plays, then
is held again and returns a screenshot. Slow reasoning stops mattering; being bad
at the game still does.

**Experimental.** The full path (window, focus, input, pause, capture) works
against a test window of its own, but no real game has been played yet. See
[docs/STATUS.md](docs/STATUS.md) for what is and is not verified.

## Requirements

- Windows 11, in a logged-in desktop session (the game takes screen and focus).
  Only `"hold": "freeze"` uses Windows 11 features; Windows 10 is untested.
- Python 3.10 or newer (only 3.13 has been tried); no packages
- Visual Studio Build Tools with C++, only for the optional clock hook

## Setup

Copy `games.example.json` to `games.json` and list the games the agent may play.
Then register the server with your MCP client, using the real path.

Claude Code:

```powershell
claude mcp add --scope user freezeframe -- python C:\path\to\Freezeframe\src\serve.py
```

Codex:

```powershell
codex mcp add freezeframe -- python C:\path\to\Freezeframe\src\serve.py
```

Any other client that runs stdio MCP servers on the same machine uses the same
command.

## games.json

Only programs listed here can be started, attached to or controlled. The agent
names an entry; it cannot supply paths or arguments. The file is re-read on every
use. Set `FREEZEFRAME_GAMES` to keep it elsewhere.

| Setting | Default | Meaning |
| --- | --- | --- |
| `exe` | required | Path to the game program. Relative paths start at the file's folder. |
| `hold` | required | How the game is held still between steps: `pause_key`, `freeze` or `none`. See below. |
| `args` | `[]` | Command-line arguments. |
| `cwd` | the exe's folder | Working directory. |
| `launch` | `true` | `false`: only attach to a copy you started yourself. Use this for games started through a launcher. |
| `window_title` | none | Text the window title must contain. |
| `startup_ms` | `3000` | Time the game runs after its window appears. |
| `max_width` | `1280` | Screenshots are scaled down to this width. |
| `notes` | `""` | Controls and goals, shown to the agent. |

With `"hold": "pause_key"` only:

| Setting | Default | Meaning |
| --- | --- | --- |
| `pause_key` | required | The game's pause key, or keys pressed together: `"p"`, `"esc"`, `["shift", "p"]`. Names as in step input. |
| `resume_key` | the pause key | For games with separate pause and resume keys. Without it, the pause key is a toggle. |
| `pause_ms` | `100` | How long the key is held, and how long the game then gets to react (20 to 2000). |
| `capture` | `after_pause` | `before_pause` takes the screenshot just before pausing, for games whose pause screen hides the action. |
| `check_pause` | `true` | Warn the agent if the picture keeps changing after pausing. Turn off for games with an animated pause screen. |

With `"hold": "freeze"` only: `hook_clock` (default `false`), described below.

## Holding the game still

**`pause_key` (preferred).** Freezeframe presses the game's own pause key: resume,
play the input, pause, take the screenshot. Nothing is injected or suspended, and
the game's clock stays sane because the game paused itself. The catches:

- The game must have a pause that really stops the action. Emulators and most PC
  games do; some cutscenes and menus ignore it.
- The agent cannot use that key for anything else. Pick a key, or an emulator
  hotkey, that the game does not otherwise need.
- The game runs for a moment the agent did not ask for: about `pause_ms` before
  the input and a little more after it. The reported run time includes it.
- A toggle can get out of step, for example if you pause the game yourself before
  the agent attaches. After pausing, Freezeframe takes a second picture
  `pause_ms` later; if the two differ, it warns the agent, which can call
  `repause`.
- The pause key is only sent while the game is in front. If you take focus
  during a step, the game is left running and the agent is told.

**`freeze`.** Suspend the whole process. Works for any game, but suspending a
process does not stop Windows' clocks: a thawed game sees all the thinking time as
having passed and may skip ahead, drop its timers or jump its physics.
`hook_clock: true` hides the pauses from the common Windows timing functions by
injecting `ffclock.dll` (build it with `.\build.ps1` first).

**Microsoft Defender has treated the hook as a threat.** On 2026-11-26, during the
clock tests, it raised `Behavior:Win32/DefenseEvasion.A!ml` (severity Severe) and
quarantined the *target*: the freshly built test program being injected and
suspended. The likely cause is a behavioral heuristic reacting to injection into
an unsigned program; that has not been confirmed. Assume it could quarantine a
real game's executable the same way. Whether suspending without injection also
triggers it is untested. `pause_key` exists to avoid both.

**`none`.** The game keeps running while the agent thinks. Fine for turn-based
games, puzzles and anything else that waits for input.

This project will not disguise any technique to get past antivirus software, and
you should not turn protection off for it. Never use it on online games or games
with anti-cheat.

## Tools

| Tool | What it does |
| --- | --- |
| `list_games` | The allowed games and their notes. |
| `start_game` | Start or attach, hold still, return a screenshot. |
| `step` | Let it run, play a list of timed segments, hold still, return a screenshot. |
| `repause` | Press the pause key once more, when a toggle is out of step. |
| `look` | The latest screenshot again. |
| `end_session` | Let go (a frozen game is thawed, a paused one stays paused); optionally close a game this session started. |

A segment is `{"ms": 300, "keys": ["up"], "buttons": ["left"], "mouse_to": [x, y],
"mouse_move": [dx, dy]}`; only `ms` is required. `mouse_to` uses screenshot
pixels, `mouse_move` is relative motion for camera control. One step is at most
15 seconds and 60 segments.

## Behavior to expect

- Input is sent only while the game is in front. Switching away stops the step and
  releases everything held.
- Hold Ctrl+Alt+F12 during a step to stop its input. It does not end the session;
  close the MCP client or tell the agent to stop. While you hold it, the pause key
  is not sent either, so a paused game is left running.
- The server waits for you to be idle for two seconds before taking focus. The
  game stays held while it waits.
- A screenshot covers the game's drawing area only, and is skipped if the game is
  not in front or another window is over it.
- With `freeze`: typing into the frozen window may be delivered when it resumes,
  Windows may mark it "Not Responding" if you click it, and if the server dies,
  Windows thaws the game.
- Exclusive fullscreen captures as black: run windowed or borderless. Games
  running as administrator cannot be controlled. Keyboard and mouse only.

## Development

```powershell
python -m unittest discover -s tests -v
```

By default the tests inject nothing, start no game and send no real input. Two
groups are opt-in:

- `FREEZEFRAME_LIVE=1`: the real server plays `tests/pausebox.py`, a small Tk
  window that pauses with P, using real input and screenshots. It takes the
  screen and keyboard for about ten seconds, so leave the computer alone. Nothing
  is injected or suspended.
- `FREEZEFRAME_NATIVE=1`: the clock hook tests. They inject and suspend, and they
  are what set off Defender.

Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
