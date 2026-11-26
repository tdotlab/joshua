"""The core claim: a frozen game cannot tell that it was frozen.

Starts real processes with hidden windows. Sends no input and takes no focus.

Opt-in (JOSHUA_NATIVE=1): these tests inject a DLL into a test program, and
Microsoft Defender has quarantined that program as a threat. See README.md.
"""

import os
import subprocess
import sys
import tempfile
import time
import unittest

import support
from joshua import process

PAUSE = 1.5            # seconds each game spends frozen
LEAK_LIMIT_MS = 200    # largest time step a hooked game may see
CONTROL_MIN_MS = 1300  # smallest time step an unhooked game must see


@unittest.skipUnless(os.environ.get("JOSHUA_NATIVE") == "1",
                     "native tests trigger antivirus detections; see README.md")
@unittest.skipUnless(support.built(), "run build.ps1 first")
class ClockTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(temporary.cleanup)
        self.directory = temporary.name

    def start(self, **options):
        ticker = support.Ticker(self.directory, **options)
        self.addCleanup(ticker.kill)
        return ticker

    def control(self, pid, clock):
        game = process.GameProcess(pid, clock)
        self.addCleanup(game.close)
        return game

    def pause(self, ticker, game, seconds=PAUSE):
        game.freeze()
        time.sleep(0.1)
        frozen = ticker.state()
        time.sleep(seconds - 0.1)
        self.assertEqual(ticker.state()["frames"], frozen["frames"], "the game ran while frozen")
        game.thaw()
        support.wait_for(lambda: ticker.state()["frames"] > frozen["frames"] + 20, what="the game to resume")
        return frozen, ticker.state()

    def test_without_the_hook_the_pause_leaks(self):
        """Control: proves the test game would notice."""
        for arch in support.ARCHITECTURES:
            for kind in ("tgt", "tick", "tick64", "qpc"):
                with self.subTest(arch=arch, clock=kind):
                    ticker = self.start(arch=arch, hook=False, extra=("--hidden", "--clock", kind))
                    _, after = self.pause(ticker, self.control(ticker.pid, None))
                    self.assertGreater(after["max_dt"], CONTROL_MIN_MS)
                    ticker.kill()

    def test_hook_hides_the_pause_from_every_clock(self):
        for arch in support.ARCHITECTURES:
            for kind in ("tgt", "tick", "tick64", "qpc"):
                for lookup in ((), ("--dynamic",)):
                    with self.subTest(arch=arch, clock=kind, lookup=lookup or "imported"):
                        ticker = self.start(arch=arch, extra=("--hidden", "--clock", kind, *lookup))
                        clock = process.Clock.open(ticker.pid)
                        self.assertIsNotNone(clock)
                        self.assertGreater(clock.patched, 0)
                        before, after = self.pause(ticker, self.control(ticker.pid, clock))
                        self.assertLess(after["max_dt"], LEAK_LIMIT_MS)
                        # Game time moved by the time it ran, not by the time that passed.
                        passed = after["wall_ms"] - before["wall_ms"]
                        lived = after["game_ms"] - before["game_ms"]
                        self.assertLess(lived, passed - PAUSE * 1000 * 0.8)
                        ticker.kill()

    def test_hook_can_be_added_to_a_running_game(self):
        for arch in support.ARCHITECTURES:
            with self.subTest(arch=arch):
                ticker = self.start(arch=arch, hook=False)
                clock = process.install_clock(ticker.pid, support.ticker_exe(arch))
                _, after = self.pause(ticker, self.control(ticker.pid, clock))
                self.assertLess(after["max_dt"], LEAK_LIMIT_MS)
                # Asking again reuses the hook that is already there.
                again = process.install_clock(ticker.pid, support.ticker_exe(arch))
                self.assertEqual(again.offset, clock.offset)
                again.close()
                ticker.kill()

    def test_many_quick_freezes_neither_leak_nor_stall(self):
        """Hunts for the race between a freeze and a hook reading the clock."""
        for kind in ("tgt", "tick64", "qpc"):
            with self.subTest(clock=kind):
                ticker = self.start(extra=("--hidden", "--clock", kind))
                game = self.control(ticker.pid, process.Clock.open(ticker.pid))
                before = ticker.state()
                frozen_seconds = 0.0
                for _ in range(150):
                    game.freeze()
                    began = time.perf_counter()
                    time.sleep(0.02)
                    frozen_seconds += time.perf_counter() - began
                    game.thaw()
                    time.sleep(0.004)
                time.sleep(0.2)
                after = ticker.state()
                self.assertLess(after["max_dt"], LEAK_LIMIT_MS)
                passed = after["wall_ms"] - before["wall_ms"]
                lived = after["game_ms"] - before["game_ms"]
                running = passed - frozen_seconds * 1000
                self.assertGreater(lived, running * 0.7)
                self.assertLess(lived, running * 1.3)
                ticker.kill()

    def test_game_thaws_if_the_controller_dies(self):
        ticker = self.start()
        holder = subprocess.Popen(
            [sys.executable, "-c",
             "import sys, time; sys.path.insert(0, sys.argv[1]);"
             "from joshua.process import GameProcess;"
             "game = GameProcess(int(sys.argv[2])); game.freeze(); print('frozen', flush=True); time.sleep(60)",
             str(support.PROJECT / "src"), str(ticker.pid)],
            stdout=subprocess.PIPE, text=True)
        self.addCleanup(holder.stdout.close)
        self.addCleanup(holder.wait)
        self.addCleanup(holder.kill)
        self.assertEqual(holder.stdout.readline().strip(), "frozen")
        time.sleep(0.1)
        frames = ticker.state()["frames"]
        time.sleep(0.5)
        self.assertEqual(ticker.state()["frames"], frames)
        holder.kill()  # no cleanup code runs in the holder
        holder.wait()
        support.wait_for(lambda: ticker.state()["frames"] > frames + 20, what="the orphaned game to thaw")


if __name__ == "__main__":
    unittest.main()
