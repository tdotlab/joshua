# Architecture

```
agent --MCP over stdio-----------------> server.py --> session.py --> hold / process / window / keyboard / capture
agent --MCP over HTTP--> network.py ----^    |    \                                |
                                         lock.py   browser.py --> Playwright --> headless Chromium (game page)
```

The server speaks JSON-RPC itself. It holds one game session at a time.
`config.py` is the allowlist, and an entry's `kind` picks the session class:
`session.Session` for a window game, `browser.BrowserSession` for a web page.
Both return a `result.Result`. Window games need no packages; browser games need
Playwright, imported only when one starts. The Windows modules are imported only
for window games, so the server and the browser backend also run on Linux.

## Browser games (`browser.py`)

Design and measurements are in [BROWSER.md](BROWSER.md); this is how the code
does it.

Playwright's sync API belongs to the thread that created it, and the server
answers calls on several threads, so a `_Worker` thread owns every Playwright
object and each call is handed to it with a real-time limit. If the limit
passes, the browser's processes are killed, which makes the stuck call fail.

`start` launches Chromium with its sandbox on, installs Playwright's clock
paused, adds `PAGE_SCRIPT` and loads the page. The script pretends pointer lock
(headless Chromium's real lock reverses every move), turns look into
`pointermove` and `mousemove` events, and counts animation frames. The page
then gets game time in 100 ms pieces until it has drawn three frames, at most
5 s of game time; time stands still while its files are still arriving.

`Player` plays a step: per segment it releases what is no longer held, presses
what is new, moves to `mouse_to`, then advances the clock in slices of at most
50 ms, spreading `mouse_move` over them. With `frame_ms` 16 it uses `run_for`;
above that `fast_forward`, one frame at a time, catching up the remainder at the
end. Keys and buttons are released in a `finally`. CSS animations are not on
the clock, so CDP's `Animation.setPlaybackRate` sets them to 1 during a step and
0 between steps.

An exception thrown by the page's own timer or frame callback comes out of the
clock call, after the whole interval has run. `_thrown` recognises it as the
page's and reports it instead of failing the step.

The origin lock is `context.route` and `route_web_socket` for every URL:
anything not in `origins` is aborted and counted. Service workers and downloads
are refused, new pages closed as they open, dialogs dismissed. WebRTC and DNS
prefetching are turned off by flag, since they would get around the route.

`proctree.py` lists processes on both systems. The Playwright driver is found
as the new child of this process, and the watchdog sums the memory of its
descendants every 0.1 s, killing them past `memory_mb`. Chromium can allocate a
few hundred MB within one interval, so it is a backstop, not an exact cap. On
Windows a child whose parent has died is no longer reachable from the tree, so
every process seen is remembered with its start time, and anything still running
after `close` is killed. If the server itself is killed, the driver takes
Chromium with it.

## Starting through Steam

With `steam_appid`, `process.open_steam` hands `steam://rungameid/<id>` to
`explorer.exe`, so Steam and the game are not children of this program and do not
end with the agent's session. `session.py` then waits for exactly one process of
the allowlisted `exe` to show a window, which skips Steam, publisher launchers
and their helpers. Joshua never kills a game that arrived this way: it took
minutes to start, and attaching to it is cheaper than starting again.

## One player

`lock.py` is a claim shared by every Joshua process in the Windows sign-in: a
named mutex, `Local\JoshuaPlayer`, that exists while a session's game is alive.
It is never waited on, so which thread releases it does not matter, and Windows
frees it if its holder dies. Processes in other sign-ins are not seen, and they
have other screens.

Inside one process, `Server.owner` is the client that started or took over the
game: `None` over stdio, a session id over HTTP. Tool calls run one at a time.
A takeover keeps the `Session` object when the game is the same. A fresh one
would assume the game is running and press the pause key on a paused game,
inverting the state it is meant to track.

`tidy` runs between calls, releasing the claim of a game that has exited and
ending a network session nobody has used for 30 minutes.

## The network transport (`network.py`)

MCP's streamable HTTP, reduced to what a server that never speaks first needs:
each POST gets one JSON reply, and there is no event stream (GET answers 405).
`Mcp-Session-Id` tells clients apart. The `MCP-Protocol-Version` header is
ignored, because Hermes sends the newest version it knows whatever was agreed.

It binds only to loopback or a Tailscale address, drops connections from other
addresses, requires the bearer token on every request, and refuses any request
with an `Origin` header, which is how a web page would arrive. The port is bound
exclusively, since Windows would otherwise let a second program share it.

## A turn

1. Check the input: the pause key of a paused game is refused.
2. If the game is not in front, wait for the user to be idle. The game is still
   held during this wait.
3. `wake`: thaw a frozen game. This comes before focusing because a suspended
   window cannot come forward.
4. Bring the game to the front.
5. `resume`: press the resume key of a paused game.
6. Play the segments with scan-code keyboard and mouse input. Before and during
   each segment, check the abort chord, that the game is alive and that it is in
   front. Release everything held on every exit path.
7. Let it run for `settle_ms`, then hit-test a grid over the window. This needs
   the game running and catches surfaces the window order misses. With
   `capture: before_pause`, or a game that is not held, the screenshot is taken
   now.
8. `hold`, in a `finally`, so a failed step never leaves the game running if that
   can be helped: freeze, or press the pause key. The pause key goes through the
   same guard as the agent's input, so it is never sent to another window; if it
   cannot be sent, the agent is told the game is still running.
9. Check the window order, copy the game's drawing area from the screen, check
   again. If anything could have been in the picture, discard it. The corners are
   blacked out: Windows 11 rounds them (8 pixels at 96 DPI), so the copy would
   otherwise include whatever is behind, which both leaks and defeats the
   all-black check for failed captures.
10. For a paused game, take a second picture `pause_ms` later. If it differs, the
    game probably did not pause (or its pause screen moves), and the agent is
    warned. The pictures are compared as PNG bytes, which are identical exactly
    when the pixels are.

Screen copying is simple and works for windowed and borderless games. It cannot
see exclusive fullscreen, which is why covered or hidden games yield no picture
instead of someone else's pixels. Reasons given to the agent never name another
window.

## Holding (`hold.py`)

Three strategies with the same `wake` / `resume` / `hold` shape.

`Pause` tracks whether it believes the game is paused and only presses a key to
change that belief, so its own presses can never invert the state. What it cannot
know is the game's real state: a game paused by the user before attaching is
resumed by the first press. The picture comparison in step 10 catches that, and
`repause` presses the key once more without changing the belief. A key press that
was interrupted after the key went down is counted as having happened, and the
agent is told it may not have registered. The game process is opened without the
right to suspend it.

Input is sent as scan codes, which DirectInput-era games read. `pause` is the
exception: its hardware sequence has no single scan code, so it goes as a virtual
key.

`Freeze` suspends the process (below). `Free` does nothing; the game keeps
running.

## Finding a pause key (`trial.py`)

`try_pause` runs only on a game listed with `"hold": "none"`, so it grants no
input the agent did not already have through `step`, and Joshua only reports
settings: writing `games.json` stays with the user.

It samples the picture about every 100 ms, with both covering checks on each
sample, because the game is running throughout. One second of baseline must show
movement, or nothing is pressed: a still scene would make any key look like a
pause. After the key, the game counts as stopped only if the last second watched
did not change, and the settings are offered unqualified only if movement then
came back after the second press (ignoring the first half second, when a pause
screen going away is itself a change). The time of the last change after the key
suggests `pause_ms`. A changing picture is reported with the area that changed,
since only the agent can tell an overlay from a running game. Once anything
interrupts a trial, no further input is sent, even to resume.

An unheld game that this program started also reports how long ago its window
appeared, which is what `startup_ms` is measured from.

## Freezing

`NtCreateProcessStateChange` / `NtChangeProcessState` (Windows 11, undocumented)
suspend the process through a handle. When the handle closes, including when the
server crashes or is killed, Windows resumes the game.

Nothing adjusts the game's clocks. `QueryPerformanceCounter`, `GetTickCount` and
the other timers run on during a freeze, so a thawed game sees the whole pause as
elapsed time.
