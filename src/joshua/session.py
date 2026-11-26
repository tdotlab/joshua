"""One game being played: held still between steps, running only while input plays."""

import time
from dataclasses import dataclass

from . import JoshuaError
from . import capture, hold, keyboard, process, timeline, window
from . import win32 as api

IDLE_BEFORE_TAKING_FOCUS_MS = 2000
IDLE_WAIT_SECONDS = 15
COMPOSE_SECONDS = 0.08
STEAM_WAIT_SECONDS = 240  # Steam, then perhaps a publisher's launcher, then the game
STEAM_POLL_SECONDS = 1

STILL_RUNNING = ("the picture kept changing after the game was paused, so it may still be running (or its"
                 " pause screen is animated). If it is running, call repause.")


@dataclass
class Result:
    frame: capture.Frame | None
    played_ms: int
    ran_ms: int
    stopped: str | None = None   # why input ended early
    no_frame: str | None = None  # why there is no new screenshot
    state: str = "frozen"        # frozen, paused, running, unheld or exited
    warning: str | None = None


class Session:
    def __init__(self, game, proc, launched):
        self.game = game
        self.process = proc
        self.launched = launched
        self.frame = None
        self.hwnd = None
        self.activity = keyboard.UserActivity()
        self.hold = hold.for_game(game, proc)

    # -- starting and stopping -------------------------------------------------

    @staticmethod
    def _start_through_steam(game):
        """Ask Steam for the game, and return its process id once it shows a window."""
        process.open_steam(game.steam_appid)
        deadline = time.monotonic() + STEAM_WAIT_SECONDS
        while time.monotonic() < deadline:
            # A launcher may start the program more than once; the one with a window is the game.
            showing = [pid for pid in process.find_running(game.exe) if window.find(pid, game.window_title)]
            if len(showing) == 1:
                return showing[0]
            time.sleep(STEAM_POLL_SECONDS)
        raise JoshuaError(f"Steam was asked to start {game.name}, but no window from {game.exe.name} appeared"
                          f" within {STEAM_WAIT_SECONDS} s. It may still be loading, or Steam or the game's"
                          " launcher may be waiting for the user (sign-in, update, terms). Ask them to check,"
                          " then call start_game again. Do not retry in a loop.")

    @classmethod
    def start(cls, game, settle_ms=500):
        if not game.exe.is_file():
            raise JoshuaError(f"{game.exe} does not exist; fix games.json")
        running = process.find_running(game.exe)
        if len(running) > 1:
            raise JoshuaError(f"{len(running)} copies of {game.name} are running; close all but one")
        launched = not running
        through_steam = launched and game.steam_appid is not None
        if through_steam:
            pid = cls._start_through_steam(game)
        elif launched:
            if not game.launch:
                raise JoshuaError(f"{game.name} is attach-only: ask the user to start it, then try again")
            pid = process.launch(game.exe, game.args, game.cwd, game.hook_clock)
        else:
            pid = running[0]
        proc = None
        try:
            clock = process.install_clock(pid, game.exe) if game.hook_clock else None
            proc = process.GameProcess(pid, clock, freezable=game.hold == "freeze")
            session = cls(game, proc, launched)
            session._wait_for_window()
            if launched:
                time.sleep(game.startup_ms / 1000)
            result = session.step([], settle_ms=settle_ms)
        except BaseException as error:
            # A launch that failed should not leave an orphaned game behind. A game that
            # came through Steam was slow to start and can be attached to, so it stays.
            if launched and not through_steam:
                process.kill(pid)
            if proc:
                proc.close()
            if through_steam and isinstance(error, JoshuaError):
                raise JoshuaError(f"{error} {game.name} was left running; call start_game again to attach to"
                                  " it.") from None
            raise
        return session, result

    def _wait_for_window(self, patience=20):
        deadline = time.monotonic() + patience
        while time.monotonic() < deadline:
            if not self.process.alive():
                raise JoshuaError("the game exited while starting. Games that a launcher starts need"
                                  " \"steam_appid\" or \"launch\": false in games.json.")
            self.hwnd = window.find(self.process.pid, self.game.window_title)
            if self.hwnd:
                return
            time.sleep(0.1)
        raise JoshuaError("the game opened no visible window")

    def close(self, close_game=False):
        """Let go of the game, leaving it running (or paused) unless asked to close it."""
        if not self.process.alive():
            self.process.close()
            return "the game had already exited"
        if close_game and not self.launched:
            self.process.close()
            return ("the game was already running when this session began, so it was left open:"
                    f" {self.hold.letting_go()}")
        if close_game:
            self.process.thaw()
            if self.hwnd:
                api.PostMessageW(self.hwnd, api.WM_CLOSE, 0, 0)
            deadline = time.monotonic() + 3
            while self.process.alive() and time.monotonic() < deadline:
                time.sleep(0.05)
            forced = self.process.alive()
            if forced:
                self.process.terminate()
            self.process.close()
            return "the game was forced to close" if forced else "the game closed"
        self.process.close()
        return self.hold.letting_go()

    # -- playing ---------------------------------------------------------------

    def _guard(self):
        if keyboard.abort_requested():
            return "the user pressed Ctrl+Alt+F12"
        if not self.process.alive():
            return "the game exited"
        if not window.is_foreground(self.process.pid):
            return "the game lost focus (the user or another window took over)"
        return None

    def _to_screen(self, point):
        area = window.client_area(self.hwnd)
        if not self.frame or not area:
            raise JoshuaError("take a screenshot before using mouse_to")
        x, y = point
        if not (0 <= x < self.frame.width and 0 <= y < self.frame.height):
            raise JoshuaError(f"mouse_to {list(point)} is outside the {self.frame.width}x"
                                   f"{self.frame.height} screenshot")
        left, top, width, height = area
        return (left + round((x + 0.5) * width / self.frame.width - 0.5),
                top + round((y + 0.5) * height / self.frame.height - 0.5))

    def _over_game(self):
        hit = api.WindowFromPoint(keyboard.pointer())
        return bool(hit) and window.owner(hit) == self.process.pid

    def _player(self):
        return keyboard.Player(self.activity, self._guard, self._to_screen, self._over_game)

    def _wait_until_idle(self):
        """Do not take the screen from someone using it. The game is still held while this waits."""
        if window.is_foreground(self.process.pid):
            return
        deadline = time.monotonic() + IDLE_WAIT_SECONDS
        while True:
            idle = self.activity.idle_ms()
            if idle is None or idle >= IDLE_BEFORE_TAKING_FOCUS_MS:
                return
            if time.monotonic() > deadline:
                raise JoshuaError("the user is using the computer, so the game was not brought forward."
                                       " Try again later, or ask them to click the game window.")
            time.sleep(0.1)

    def _focus(self):
        if window.is_foreground(self.process.pid):
            return
        if not window.focus(self.hwnd, self.process.pid, lambda: keyboard.nudge(self.activity)):
            raise JoshuaError("Windows would not bring the game forward. Ask the user to click the game"
                                   " window, then try again.")

    def _tap(self, keys):
        """Press keys together in the focused game, then give it time to react.

        Returns (whether anything was pressed, why it stopped early or None).
        """
        player = self._player()
        _, problem = player.play([timeline.Segment(ms=self.game.pause_ms, keys=tuple(keys))])
        if player.sent_any:
            time.sleep(self.game.pause_ms / 1000)
        return player.sent_any, problem

    def step(self, segments, settle_ms=100):
        """Let the game run, play the segments, hold it still again and photograph it."""
        if not self.process.alive():
            raise JoshuaError("the game is no longer running")
        self.hold.check(segments)
        self.hwnd = window.find(self.process.pid, self.game.window_title) or self.hwnd
        self._wait_until_idle()
        played, stopped, hidden, frame = 0, None, None, None
        self.hold.wake()
        try:
            self._focus()
            stopped = self.hold.resume(self._tap)
            if segments and not stopped:
                played, stopped = self._player().play(segments)
            # Let the game notice the releases and draw the result.
            time.sleep(settle_ms / 1000)
            if self.process.alive() and window.is_foreground(self.process.pid):
                # Hit-testing needs the game running, so it happens before holding.
                cover = window.window_at_points(self.hwnd, self.process.pid)
                if cover:
                    hidden = f"the game is not fully visible: {cover}"
                elif self.hold.capture_first:
                    frame, hidden = self._photograph()
            elif self.hold.capture_first:
                hidden = "the game is not in front"
        finally:
            # Held on every path, so a failed step never leaves the game running if that can be helped.
            warning = self.hold.hold(self._tap)
        ran = self.hold.ran_ms()
        if not self.process.alive():
            # A dying game loses its window first, so any earlier reason for stopping is misleading.
            return Result(None, played, ran, "the game exited", "the game exited", "exited")
        if not hidden and not self.hold.capture_first:
            time.sleep(COMPOSE_SECONDS)
            frame, hidden = self._photograph()
        if not hidden and not warning:
            warning, newer = self._check_still(frame)
            if newer:
                frame = newer
        return Result(frame, played, ran, stopped, hidden, self.hold.state(), warning)

    def repause(self):
        """Press the pause key once more, for a toggle that got out of step with the game.

        Returns (whether it was pressed, Result).
        """
        if self.hold.kind != "pause_key":
            raise JoshuaError("repause only applies to games held with their own pause key")
        if not self.process.alive():
            raise JoshuaError("the game is no longer running")
        self.hwnd = window.find(self.process.pid, self.game.window_title) or self.hwnd
        self._wait_until_idle()
        self._focus()
        pressed, problem = self._tap(self.hold.pause_keys)
        if not pressed:
            return False, Result(None, 0, 0, None, problem, self.hold.state())
        self.hold.repaused()
        time.sleep(COMPOSE_SECONDS)
        frame, hidden = self._photograph()
        warning = f"the pause key may not have registered ({problem})" if problem else None
        if not hidden and not warning:
            warning, newer = self._check_still(frame)
            frame = newer or frame
        return True, Result(frame, 0, 0, None, hidden, self.hold.state(), warning)

    def _check_still(self, shown):
        """For a paused game, compare two pictures a moment apart. Returns (warning, newer frame)."""
        if not (self.hold.check_motion and self.hold.state() == "paused"):
            return None, None
        if self.hold.capture_first:
            # The frame shown was taken before pausing, so it cannot be one of the pair.
            shown, problem = self._photograph(keep=False)
            if problem:
                return None, None
        time.sleep(self.hold.ms / 1000)
        later, problem = self._photograph(keep=not self.hold.capture_first)
        if problem or shown is None or later.png == shown.png:
            return None, None
        return STILL_RUNNING, (None if self.hold.capture_first else later)

    def _obstruction(self):
        if not window.is_foreground(self.process.pid):
            return "the game is not in front"
        cover = window.window_above(self.hwnd, self.process.pid)
        return f"the game is not fully visible: {cover}" if cover else None

    def _photograph(self, keep=True):
        """Capture the game's area, only if nothing else can be in the picture."""
        problem = self._obstruction()
        if problem:
            return None, problem
        frame = capture.grab(*window.client_area(self.hwnd), max_width=self.game.max_width,
                             dpi=window.dpi(self.hwnd))
        # Something may have popped up while the picture was being taken.
        problem = self._obstruction()
        if problem:
            return None, problem
        if keep:
            self.frame = frame
        return frame, None
