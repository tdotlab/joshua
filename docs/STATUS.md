# Status

As of 2026-11-26. Experimental, not registered with any MCP client.

## Verified

- Safe tests: 45 pass, 7 opt-in tests skipped. They cover step parsing, key
  release logic, the three hold strategies and the order of a whole step (Windows
  calls replaced), PNG encoding and corner masking, config, the MCP protocol
  including the real server over stdio, coordinate mapping, covering-window
  wording, and starting a program without injection.
- Live tests (`FREEZEFRAME_LIVE=1`), passing with the current code: the real
  server over stdio played `tests/pausebox.py` with `"hold": "pause_key"`. Window
  finding, focus, scan-code input, the pause key, screen capture and closing all
  worked. The game did not advance between steps, and a toggle started out of
  step was detected by the picture comparison and fixed by `repause`.
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
- Native run, interrupted by Defender: a hooked 32-bit test program thawed after
  its controller was killed, and attaching the hook to a running program passed
  for 32-bit and 64-bit.

## Not verified

- A native Windows game. GTA 1 (the 2002 Windows version from Steam, launched
  directly) showed its menus, which were captured and driven, then crashed with
  an access violation in its own program while loading the first mission: three
  times at the same place where Windows logged it, whether Freezeframe or Windows
  started it, with no injection or suspension. Not known: whether it also crashes
  without Freezeframe's input and capture, or when started from Steam. Its pause
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

Find out whether GTA 1 loads a mission when the user starts it from Steam. If it
does, attach to it (`"launch": false`) and find its pause key. Then register the
server with an MCP client and let an agent play.
