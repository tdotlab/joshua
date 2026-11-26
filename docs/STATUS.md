# Status

As of 2026-11-26. Experimental. Registered with Claude Code (user scope) as
`joshua`, with a local `games.json` listing Daggerfall and Redguard. The server
connects and lists them; no agent has played through that registration yet.
Network mode and the one-player lock are built; no other machine has connected.

## Verified

- Safe tests: 84 pass, 9 opt-in tests skipped. They cover step parsing, key
  release logic, the three hold strategies and the order of a whole step (Windows
  calls replaced), PNG encoding and corner masking, config, the MCP protocol
  including the real server over stdio and HTTP, whose turn it is (takeover,
  expiry, a second process), the network server's refusals, coordinate mapping,
  covering-window wording, starting a program without injection, and the Steam
  start (the request and the waiting, with Windows calls replaced).
- Live tests (`JOSHUA_LIVE=1`): the real server, over stdio and over HTTP on
  loopback, played `tests/pausebox.py` with `"hold": "pause_key"`. Window
  finding, focus, scan-code input, the pause key, screen capture and closing all
  worked. The game did not advance between steps, and a toggle started out of
  step was detected by the picture comparison and fixed by `repause`.
- Network mode with the official MCP Python SDK 2.0.0 as client, on loopback:
  handshake, ping, tool list, calls from two clients at once and a wrong token,
  with no game launched. Ctrl+C stops the server cleanly. Started with no
  `--host`, it bound to this PC's Tailscale address and answered from this PC.
- Real games, `"hold": "pause_key"` with Alt+Pause (Microsoft Defender recorded no
  detection during them):
  - Daggerfall, DOSBox 0.74: intro movie and menus held (over 5 s paused the
    picture was identical and the process nearly idle), keyboard input worked,
    and the mouse-driven menu was operated with a click to capture the mouse and
    then `mouse_move`.
  - Redguard, DOSBox 0.73: loaded a game into real-time 3D combat. Over 8 s
    paused the picture, animated water included, was identical. Walking forward
    in steps brought the player up to the opponent.
  - Both closed cleanly from the paused state.
- GTA IV: The Complete Edition, started with `steam_appid` through the Rockstar
  launcher (already signed in), windowed at 1280x720 by `commandline.txt`, over
  the HTTP transport on loopback:
  - Joshua asked Steam, waited about 170 s and attached, with Steam already
    running. With Steam closed it took about 190 s, longer than the 180 s limit
    then in force; a second call attached and the limit is now 240 s.
  - Menus were captured and driven with `"hold": "none"`.
  - The Lost and Damned loaded a new game. With Esc as the pause key, the in-game
    clock did not move for over a minute while paused, and two driving steps
    steered the motorcycle. The rider was not drawn in any picture; the cause is
    unknown.
  - The base game crashed twice while loading from Start, with its own dialog
    "Invalid resource detected". Existing saves were not modified. Not known:
    whether a save, the installed files or `commandline.txt` is responsible, or
    whether it also happens without Joshua. The same `commandline.txt` was in
    place when the episode loaded.
- Factorio 2.1.24 (Steam copy, started directly with a `steam_appid.txt`, since
  removed), with a separate config and a new map, over HTTP on loopback:
  - Without that file the game handed itself to Steam, Joshua reported it exited,
    and Steam waited for the user to approve the arguments.
  - Pause held it: the picture, fire animation included, was identical over 20 s
    (the frame counter's strip and the right edge left out).
  - Walked to the crash wreck, opened it with a click and moved its ammunition to
    the character with Ctrl+click, using `mouse_to`.
  - A pause pressed during loading was ignored; `repause` after attaching again
    put the toggle back in step.
  - The game was killed at the end. The user's Factorio folder and its Steam
    Cloud files were unchanged.
- Native run, interrupted by Defender: a hooked 32-bit test program thawed after
  its controller was killed, and attaching the hook to a running program passed
  for 32-bit and 64-bit. That was before the rename: the renamed C sources
  compile (compile only, 32-bit and 64-bit), but `build.ps1` has not been run
  since.

## Not verified

- A connection from another machine: Windows Firewall, tailnet access rules,
  Hermes itself, and whether its model receives the screenshots as pictures.
- A real game played from another machine.
- The live tests have not been rerun since the Steam start was added.
- Camera look in GTA IV, anything on foot, and a mission to its end.
- GTA 1 (the 2002 Windows version from Steam, launched directly, before
  `steam_appid` existed) showed its menus, which were captured and driven, then crashed with
  an access violation in its own program while loading the first mission: three
  times at the same place where Windows logged it, whether Joshua or Windows
  started it, with no injection or suspension. Not known: whether it also crashes
  without Joshua's input and capture, or when started from Steam. Its pause
  key is therefore still unknown.
- `"hold": "freeze"` through the full session path, and whether antivirus
  software reacts to suspension without injection.
- The clock hook's main claim. The 32-bit tests failed once the test program was
  quarantined, 64-bit results were not fully captured, and the repeated
  freeze/thaw race test has never completed.
- Camera look with `mouse_move` in a 3D game.

## Known gaps

- Once, Windows refused to bring a freshly launched DOSBox forward and the start
  failed; the retry and later launches worked. The cause was not found.
- A captured mouse (DOSBox) has no absolute positioning: the agent steers by
  looking and correcting.
- Ctrl+Alt+F12 stops the current step only; the agent can send another.
- Keyboard and mouse only: no gamepad, no audio.
- In pause mode the game runs briefly outside the agent's input (about
  `pause_ms` before and after).

## Next

Connect Hermes from the laptop: list the games, then start one and check what
the model says it sees.

Let an agent play Daggerfall or Redguard from a fresh Claude Code session, and
see what the notes and tools leave it guessing about.

Find out whether GTA 1 loads a mission when started with `steam_appid`. If it
does, find its pause key.

If GTA IV is wanted: add it to the local `games.json` with the settings in
README.md, and see whether the base game still crashes without `commandline.txt`.
