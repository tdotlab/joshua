# Status

As of 2026-11-26. Experimental, not registered with any MCP client.

## Verified

- Safe tests: 44 pass, 7 opt-in tests skipped. They cover step parsing, key
  release logic, the three hold strategies and the order of a whole step (Windows
  calls replaced), PNG encoding, config, the MCP protocol including the real
  server over stdio, coordinate mapping, covering-window wording, and starting a
  program without injection.
- Live tests (`FREEZEFRAME_LIVE=1`), passed twice: the real server over stdio
  played `tests/pausebox.py` with `"hold": "pause_key"`. Window finding, focus,
  scan-code input, the pause key, screen capture and closing all worked. The game
  did not advance between steps, and a toggle started out of step was detected
  by the picture comparison and fixed by `repause`. Microsoft Defender raised
  nothing.
- Native run, interrupted by Defender: a hooked 32-bit test program thawed after
  its controller was killed, and attaching the hook to a running program passed
  for 32-bit and 64-bit.

## Not verified

- Any real game, in any hold mode.
- `"hold": "freeze"` through the full session path, and whether antivirus
  software reacts to suspension without injection.
- The clock hook's main claim. The 32-bit tests failed once the test program was
  quarantined, 64-bit results were not fully captured, and the repeated
  freeze/thaw race test has never completed.
- Mouse input into a real window.

## Known gaps

- Ctrl+Alt+F12 stops the current step only; the agent can send another.
- Keyboard and mouse only: no gamepad, no audio.
- In pause mode the game runs briefly outside the agent's input (about
  `pause_ms` before and after).

## Next

Play one offline game with `"hold": "pause_key"`. An emulator with a pause hotkey
is the easiest real target.
