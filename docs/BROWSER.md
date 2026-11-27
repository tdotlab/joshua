# Browser games

Status: designed and built 2026-11-27 (`src/joshua/browser.py`; set-up in
README.md, code walk-through in ARCHITECTURE.md). Steps 1 to 4 of the order of
work below are done; `joshua-call` is not built. What building it found is under
[Built](#built).

A second kind of game for Joshua: one that runs in a web page. It is played
through headless Chromium driven by Playwright, so it works on Linux and on
machines with no display or GPU, like the VM where Meta's Muse agent writes a
Three.js game. The tools and the step format stay the same; only what happens
inside a turn changes.

## What was measured

Measured 2026-11-27 with Playwright 1.65.2 (headless shell, build 1255) in a
Linux container with no GPU, so WebGL ran on SwiftShader. The probes and their
results are kept outside the repo.

Playwright's clock (`page.clock`) holds a page completely: while paused, no
animation frames, no timers, and `Date` and `performance.now` stand still.
`run_for(ms)` then advances exactly that much game time in fixed 16 ms frames.
On a Three.js scene of the kind a small survival game draws (terrain, 300
trees, shadows, fog, about 200 draw calls):

| Viewport, shadows | Live, unheld | 1 s of game time, 16 ms frames | Same, about 25 ms jumps |
| --- | --- | --- | --- |
| 1280x720, on | 10.7 fps | 63 frames, 5.4 s | 40 frames, 3.1 s |
| 1280x720, off | 21.0 fps | 62 frames, 2.2 s | 40 frames, 1.7 s |
| 640x360, on | 23.0 fps | 63 frames, 2.4 s | 40 frames, 1.7 s |

So unheld, this game runs at 10 to 23 fps
on such a machine. Held, it runs at a smooth 60 fps of game time, but a second
of play costs 2 to 5 s of wall time. A screenshot takes 30 to 220 ms. The
container peaked at about 1.2 GiB.

The same step gave the same result each time: holding W for 1000 ms moved the
player 4.80 to 4.89 units at 5 units/s, and a 300 px look turned it exactly
0.900 rad every time.

What did not work:

- Chromium's real pointer lock in headless mode turns each mouse move into a
  move and its exact reverse, so mouse look nets zero. Advancing the held clock
  after a page asked for real pointer lock hung and ate memory: about 8.8 GB
  without a limit, and the 2 GiB limit on a repeat.
- Chromium's own virtual time (CDP `Emulation.setVirtualTimePolicy`) held
  timers, `performance.now` and CSS animation, but animation frames kept coming
  (51 in 2 s while "paused"), and the first advance ran memory up to the 2 GiB
  limit until it was killed. It is not used.
- Holdfast, Muse's version, gated animation frames only: timers and CSS kept
  running while held, and the first frame afterwards saw the whole pause as
  elapsed time.

## Listing a browser game

```json
"survival": {
  "kind": "browser",
  "url": "http://127.0.0.1:5173/",
  "viewport": [1280, 720],
  "frame_ms": 16,
  "notes": "WASD to move, click to capture the mouse, E to use."
}
```

`kind` defaults to `"window"`, which is everything Joshua does today. A browser
game takes no `hold`: it is always held by its clock. Window-only settings
(`exe`, `steam_appid`, `pause_key` and the rest) are refused on it.

- `url`: where the game is, `http` or `https`. A game on disk is served
  locally first (`python -m http.server`), since `file:` pages have no origin
  to lock to.
- `origins`: the only origins the page may load anything from. Defaults to the
  origin of `url`. A game that loads a library from a CDN needs that origin
  listed, which is the user's decision, as with every entry in `games.json`.
- `viewport`: the page size in CSS pixels, which is also the screenshot size.
  Smaller is faster to render (see the table).
- `frame_ms`: game time between frames, 16 by default. Larger values render
  fewer frames per game second (below).
- `keep_profile`: keep the browser profile, and so the game's saves in local
  storage, between sessions. Default false: every session starts clean, which
  suits testing a game under development.
- `memory_mb`: the browser's memory limit, 2048 by default.

## A turn

1. Check the segments with `timeline.parse`, as now. Joshua's key names map to
   Playwright's (`w` to `KeyW`, `up` to `ArrowUp`, `ctrl` to `Control`).
2. For each segment: release the keys and buttons it no longer holds, press the
   new ones, move to `mouse_to` (screenshot pixels are CSS pixels here), then
   advance the clock by the segment's `ms` in slices of at most 50 ms. A
   `mouse_move` is spread over those slices.
3. Advance by `settle_ms`.
4. In a `finally`: release every key and mouse button.
5. Take the screenshot. Its size comes from the PNG header, so nothing is
   decoded.
6. Gather what the page reported since the last turn: console errors and
   uncaught exceptions (capped), and, if the page defines
   `window.joshuaState()`, its return value as JSON (capped at a few KB). A game
   author can expose position, health or the current level this way. The page
   writes this text; Joshua only passes it on, labelled as coming from the
   game.

The agent is told the game time that passed, which is exact, and is not told
the wall time, which only reflects how fast the machine renders.

## Frame interval

With `frame_ms` 16, the clock is advanced with `run_for`, which fires every
16 ms frame. A larger `frame_ms` advances with `fast_forward` in jumps of that
size instead, so each jump renders one frame. In the measurement, jumps of 33
and 17 ms (40 frames per game second) cut wall time by 30 to 45 percent; jumps
of 33 ms alone should come close to half. The catch: a timer due more often
than `frame_ms` fires only once per jump, so a game that runs its logic on a
fast `setInterval` would slow down. Games that take a frame delta, which is
how Three.js games are usually written, only see larger steps.

## Mouse look

The real pointer lock is never used (see above). A script added before the
page loads replaces `requestPointerLock`, `exitPointerLock` and
`pointerLockElement`, so the game believes it got the lock, and `mouse_move`
sends `mousemove` events with `movementX`/`movementY` to the locked element.
Measured on a test page and on the Three.js scene: the exact delta arrives
every time. Not covered: a game that checks `event.isTrusted`, or that reads
`pointerrawupdate` instead of `mousemove`.

## What is not held

- CSS animations and transitions: paused between steps through CDP
  (`Animation.setPlaybackRate` 0, measured), but during a step they run on wall
  time, which is longer than game time. Seeking them by the step's game time
  (`Animation.seekAnimations`) could fix this; untested.
- Audio: Chromium runs muted. `AudioContext.currentTime` follows the real
  clock, so a game that times itself by audio would drift. Expected, untested.
- Web Workers: Playwright's clock patches the page, so timers in a worker
  probably keep real time. Expected, untested.

## Safety

The properties in `AGENTS.md` carry over, some by construction.

- Allowlisted games only: the `url` in `games.json`. Top-level navigation away
  from the game's origin is blocked, every request to an origin not in
  `origins` is aborted, new windows are closed as they open, downloads are
  refused and no browser permissions are granted. (Holdfast's selector click
  could follow a link off its allowed origin.) WebRTC is removed from every
  frame: it connects outside the request filter, and the Chromium flag meant to
  stop it blocks only UDP (see the audit below).
- Input only to the game, and no pixels from other windows: the page is
  headless, in its own browser and profile, so there is nothing else to send
  input to or photograph.
- Everything released after a step: in a `finally`, as above. (A bad key name
  in Holdfast left earlier keys held.)
- Bounded resources: a watchdog samples the memory of Chromium's process tree
  and kills it past `memory_mb`; every step also has a wall-time limit
  (120 s). Either ends the session with a plain reason. Both runaways above
  would have been stopped.
- Nothing left behind: Chromium is closed on `end_session`, when the client
  hangs up and when the server exits. Nothing is suspended, so there are no
  stopped processes to orphan (Holdfast left 12 behind). Killed outright, the
  server takes Chromium with it on Windows (checked; see below).
- Chromium's sandbox stays on. Running as root, as in most containers, needs
  it off; Joshua refuses unless the user's environment says to allow that.
- One player: the per-PC lock exists because window games share the screen and
  keyboard. Headless browser games share neither, so they do not take it. One
  game per server process still holds.

## Tools

`list_games`, `start_game`, `step`, `look` and `end_session` work as now.
`start_game` launches Chromium, installs the clock and the pointer-lock script,
loads the page, lets it run until it has drawn a few frames, then holds it and
returns a screenshot. `end_session` always closes the browser; it is Joshua's
own. `repause` and `try_pause` answer that browser games are held by their
clock and need neither.

## Code

- `config.py`: `kind`, and a separate check for browser entries.
- `browser.py` (new): `BrowserSession`, with the same surface the server
  already uses: `start(game)`, `step(segments, settle_ms)`, `frame`, `game`,
  `close()`, and an `alive()` check.
- `server.py`: choose the session class by `kind`, where it now takes one
  `session_class`. Import the Windows modules only for window games, and call
  the Windows set-up in `main()` only on Windows, so the server starts on
  Linux. Only window games take the lock.
- `network.py`: the token path falls back to `~/.config/joshua` where there is
  no `LOCALAPPDATA`.
- Dependency: `playwright`, needed only for browser games, plus
  `playwright install chromium` (on Debian, also `libatomic1`).

## If an agent cannot load MCP

For a harness that cannot load MCP servers, a small `joshua-call`
command could talk to a server started with `--http --host 127.0.0.1`, print the
text answer and save the screenshot to a file for the agent to open. It reuses
the network transport, so it adds a client and no new server. Build it only if
needed; no agent has needed it so far (Muse can load Joshua).

## Order of work

1. The platform split, `kind` in `config.py`, and choosing the session class.
   The existing tests still pass, and the safe tests also run on Linux.
2. `BrowserSession`, with unit tests against a stand-in page: key and button
   changes per segment, clock slices, look spread across slices, release on
   every failure path, origin blocking.
3. Opt-in live tests with a test page in `tests/`, as `pausebox.py` is for
   window games: the hold, an exact look, key release, a blocked origin, a
   blocked popup, a reported console error, and the memory limit killing a page
   that allocates without end.
4. A real game: another agent's, and the Three.js probe scene.
5. `joshua-call`, if needed.

## Built

Done 2026-11-27, steps 1 to 4 (the probe scene as the real game).

- On the Windows desktop (GTX 1070), `--enable-gpu` gave headless Chromium
  hardware WebGL through ANGLE on D3D11. The probe scene, driven through the
  real server's tool calls, started in 4.2 s; 1 s of game time holding W moved
  the player 4.96 units at 5 units/s and took 0.20 s of real time; a click,
  a 400 px look and 1.5 s of W and Shift took 0.35 s. Compare 2 to 5 s per game
  second in software above. Without a GPU, `--enable-unsafe-swiftshader` keeps
  software WebGL working.
- The clock rethrows exceptions from the page's own timers and frame callbacks
  out of `run_for`, after finishing the interval. Left alone, one bad callback
  aborted `start_game`. They are now reported as the page's and play goes on.
- The memory watchdog overshoots: a page allocating 20 MB a frame reached about
  800 MB against a 512 MB limit with slower sampling. At 0.1 s it is a backstop
  that stops runaways, not an exact cap.
- Killing the server outright (Windows) took Chromium down within 3 s. On
  Windows, browser processes whose parent died drop out of the process tree, so
  `close` also kills anything it saw that is still running.
- In Docker (python:3.12-slim), Chromium's sandbox fails for an ordinary user
  under the default seccomp profile, which blocks user namespaces. Joshua says
  so; as root it refuses unless `JOSHUA_BROWSER_NO_SANDBOX=1`. With the opt-out
  the live tests pass there. A sandbox-on launch on Linux has not been seen
  working, since that needs a relaxed seccomp profile, which was not tried.
- On the test page: no frame or timer moves in a second of real time between
  steps; 1000 ms of D gives 100 px, 10 timer ticks and about 62 frames; a
  300 px look arrives exactly; Esc releases the lock without reaching the page;
  a blocked fetch and WebSocket, a popup, an alert and an uncaught error are all
  contained and reported once. Peak memory was about 160 MB in 4 processes.
- Playwright's driver (Node 26.13.1 with Playwright 1.65.2, Windows) sometimes
  dies while starting, with a V8 fatal error, `Check failed: index < size()`.
  It was seen in 2 of about 14 runs of the whole test suite, and in none of 450
  bare driver starts or 118 runs of the browser tests alone. The cause is not
  known. `start` tries once more, then reports it.

## Audit

2026-11-27, a review of the whole project.

- The origin lock leaked through WebRTC. With
  `--force-webrtc-ip-handling-policy=disable_non_proxied_udp` set, a page on
  127.0.0.1 pointed `RTCPeerConnection` at a TURN server on this PC's LAN
  address, and that address received the TCP connection, while a `fetch` to the
  same address was refused. The flag stops UDP only. The init script now deletes
  `RTCPeerConnection`, `webkitRTCPeerConnection` and `RTCIceTransport`. A probe
  then tried the page itself, an `about:blank` iframe, a `srcdoc` iframe (before
  and after it loaded), a `data:` iframe running its own script, and a popup:
  none found a constructor, and the listener got nothing. A live test checks
  every frame.
- `joshuaState()` is cut to size in the page, so a huge state is not carried
  across the driver only to be clipped.
- A key whose release failed in one segment change is tried again at the end of
  the step, and time owed by a failed step is not charged to the next.

Not measured yet: a game whose logic runs in a Web Worker; anything with sound;
a game written by someone else.
