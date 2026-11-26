# Joshua

An MCP server that lets an agent play a Windows game turn by turn. The game is
held still while the model thinks, runs only while the model's input plays, then
is held again and returns a screenshot. Slow reasoning stops mattering; being bad
at the game still does.

**Experimental.** Pause-key mode has played real games: two DOS games in
DOSBox, including a 3D sword fight held still between steps. Freeze mode has never
run against a real game. See [docs/STATUS.md](docs/STATUS.md) for what is and is
not verified.

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
claude mcp add --scope user joshua -- python C:\path\to\Joshua\src\serve.py
```

Codex:

```powershell
codex mcp add joshua -- python C:\path\to\Joshua\src\serve.py
```

Any other client that runs stdio MCP servers on the same machine uses the same
command. For an agent on another machine, see below.

## Playing from another machine

Joshua can serve an agent elsewhere on your Tailscale network. The game, screen
and keyboard are still this PC's, so it has to be logged in and unlocked.

On the PC with the games:

```powershell
python C:\path\to\Joshua\src\serve.py --http
```

It listens on this PC's Tailscale address, port 8737, until you press Ctrl+C
(`--host` and `--port` change that; only loopback and Tailscale addresses are
accepted). It prints the URL, and logs each tool call and each refused token.

Clients must send a token, which is made on the first run. This copies it:

```powershell
Get-Content "$env:LOCALAPPDATA\Joshua\token.txt" | Set-Clipboard
```

`JOSHUA_TOKEN_FILE` keeps it elsewhere. To replace it, delete the file and
restart the server.

Any MCP client that speaks streamable HTTP can use the URL with the header
`Authorization: Bearer <token>`. The connection is plain HTTP; Tailscale does
the encryption. With Hermes Agent, as the user Hermes runs as:

```bash
hermes mcp add joshua --url http://100.x.y.z:8737/mcp --auth header
```

It asks for the token without showing it and keeps it in its `.env`. Then start
a new chat or send `/reload-mcp`.

Whoever has the token and can reach the port can see and control the listed
programs. A listed program is trusted with that: if its window offers a command
line, as DOSBox does, the agent can type into it. If another machine cannot
connect, check that Windows Firewall lets Python accept connections on private
networks and that your tailnet's access rules allow it.

## One player at a time

There is one screen and one keyboard, so one game is played by one client.

- While a game is being played, other clients are refused. `list_games` still
  answers.
- This holds across processes: a second Claude Code or Codex session cannot
  start a game until the first calls `end_session` or exits, or its game closes.
- Over the network, a player who disconnects can be replaced at once, and one
  who has made no call for 5 minutes can be replaced then. The newcomer calls
  `start_game`; naming the same game carries on without restarting it.
- After 30 minutes without a call, the network server lets go of the game by
  itself. A frozen game is thawed; a paused one stays paused.
- Joshua tells clients apart by connection. A single Hermes gateway shares one
  connection between its chats, so two of its chats look like one player.

## games.json

Only programs listed here can be started, attached to or controlled. The agent
names an entry; it cannot supply paths or arguments. The file is re-read on every
use. Set `JOSHUA_GAMES` to keep it elsewhere.

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

**`pause_key` (preferred).** Joshua presses the game's own pause key: resume,
play the input, pause, take the screenshot. Nothing is injected or suspended, and
the game's clock stays sane because the game paused itself. The catches:

- The game must have a pause that really stops the action. Emulators and most PC
  games do; some cutscenes and menus ignore it.
- The agent cannot use that key for anything else. Pick a key, or an emulator
  hotkey, that the game does not otherwise need.
- The game runs for a moment the agent did not ask for: about `pause_ms` before
  the input and a little more after it. The reported run time includes it.
- A toggle can get out of step, for example if you pause the game yourself before
  the agent attaches. After pausing, Joshua takes a second picture
  `pause_ms` later; if the two differ, it warns the agent, which can call
  `repause`.
- The pause key is only sent while the game is in front. If you take focus
  during a step, the game is left running and the agent is told.

### DOSBox

DOSBox has its own pause hotkey, which stops the whole emulated machine, so any
game in it can be held without help from the game. Tried with DOSBox 0.73 and
0.74:

```json
"daggerfall": {
  "exe": "C:/Games/Daggerfall/DOSBox-0.74/DOSBox.exe",
  "args": ["-noconsole", "-conf", "daggerfall.conf"],
  "hold": "pause_key",
  "pause_key": ["alt", "pause"],
  "window_title": "DOSBox",
  "startup_ms": 9000
}
```

With `autolock=true` in the DOSBox config (set in both tested copies), the first
click in the window captures the mouse. After that `mouse_to` does not apply:
move with `mouse_move`, which is not one to one with screenshot pixels, so look
where the cursor landed and correct. Tell the agent this in `notes`.

**`freeze`.** Suspend the whole process. Works for any game, but suspending a
process does not stop Windows' clocks: a thawed game sees all the thinking time as
having passed and may skip ahead, drop its timers or jump its physics.
`hook_clock: true` hides the pauses from the common Windows timing functions by
injecting `jclock.dll` (build it with `.\build.ps1` first).

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
pixels, `mouse_move` is relative motion for camera control or a captured mouse.
Key names include letters, digits, `up`, `enter`, `esc`, `space`, `shift`,
`ctrl`, `alt`, `f1` to `f12` and `pause`; the full list is in
`src/joshua/timeline.py`. One step is at most 15 seconds and 60 segments.

## Behavior to expect

- Input is sent only while the game is in front. Switching away stops the step and
  releases everything held.
- Hold Ctrl+Alt+F12 during a step to stop its input. It does not end the session;
  close the MCP client or tell the agent to stop. While you hold it, the pause key
  is not sent either, so a paused game is left running.
- The server waits for you to be idle for two seconds before taking focus. The
  game stays held while it waits.
- A screenshot covers the game's drawing area only, and is skipped if the game is
  not in front or another window is over it. The corners are blacked out, because
  Windows 11 rounds them and the desktop shows through.
- Windows occasionally refuses to bring a newly launched game forward. The start
  fails, the game is closed again, and trying once more has worked.
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

- `JOSHUA_LIVE=1`: the real server, over stdio and over HTTP on loopback, plays
  `tests/pausebox.py`, a small Tk window that pauses with P, using real input and
  screenshots. It takes the screen and keyboard for about twenty seconds, so
  leave the computer alone. Nothing is injected or suspended.
- `JOSHUA_NATIVE=1`: the clock hook tests. They inject and suspend, and they
  are what set off Defender.

Design: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
