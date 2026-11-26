# Architecture

```
agent --MCP over stdio--> server.py --> session.py --> hold / process / window / keyboard / capture
                                                                  |
                                                     game <-- jclock.dll (optional, freeze only)
```

The server speaks newline-delimited JSON-RPC itself and has no dependencies. It
holds one game session at a time. `config.py` is the allowlist.

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

## Freezing

`NtCreateProcessStateChange` / `NtChangeProcessState` (Windows 11, undocumented)
suspend the process through a handle. When the handle closes, including when the
server crashes or is killed, Windows resumes the game.

## The clock hook (optional, freeze only)

Suspension leaves `QueryPerformanceCounter`, `GetTickCount`, `GetTickCount64` and
`timeGetTime` running. `jclock.dll` rewrites the import tables of loaded modules,
including later ones and `GetProcAddress` lookups, so those functions return the
real time minus the total time spent suspended. The total lives in a shared
mapping, `Local\JoshuaClock-<pid>` (`struct JShared`, mirrored by
`_ClockState` in `process.py`). The server adds to it only while the game is
suspended.

**Invariant:** a hook reads the real clock first and the offset second. A freeze
can land between the two. In this order the stale reading minus the new offset is
slightly early, and the monotonic clamp holds it. The other order returns a time
far in the future, after which the clamp stalls the game's clock. C does not fix
the evaluation order of operands, so each read is its own statement.

`jinject launch` creates the game suspended and loads the DLL before its first
instruction; `jinject attach` loads it into a running game. Both need the
matching 32- or 64-bit build.

Not covered: `rdtsc`, wall-clock and system-time functions, multimedia timer
callbacks, waits and sleeps, audio device clocks, and statically linked or
directly resolved calls that bypass the import tables.
