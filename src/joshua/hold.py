"""How a game is held still between steps: paused with its own key, suspended, or not at all.

Each strategy has the same shape. A step calls wake() before bringing the game
forward, resume(tap) once it is in front, and hold(tap) in a `finally` when the
input is done. tap(keys) presses keys in the focused game and returns
(pressed, problem); see Session._tap.
"""

from . import JoshuaError
from . import win32 as api


class _Timed:
    def __init__(self):
        self._since = None
        self._ran = 0

    def _stop_clock(self):
        if self._since is not None:
            self._ran = api.qpc_milliseconds(api.qpc() - self._since)
            self._since = None
        else:
            self._ran = 0

    def ran_ms(self):
        return self._ran

    def check(self, segments):
        """Refuse input that would interfere with holding the game."""


class Pause(_Timed):
    """The game pauses itself with its own key. Nothing is injected or suspended."""

    kind = "pause_key"

    def __init__(self, game):
        super().__init__()
        self.pause_keys = game.pause_key
        self.resume_keys = game.resume_key or game.pause_key
        self.toggle = not game.resume_key
        self.capture_first = game.capture == "before_pause"
        self.check_motion = game.check_pause
        self.ms = game.pause_ms
        # A game that was just started or attached to is assumed to be running.
        self.paused = False

    def check(self, segments):
        for index, segment in enumerate(segments):
            for chord in {self.pause_keys, self.resume_keys}:
                if set(chord) <= set(segment.keys):
                    raise JoshuaError(
                        f"segment {index} presses {'+'.join(chord)}, which Joshua uses to pause and resume"
                        " this game, so it was refused. If the game needs that key for something else, the"
                        " user can choose a different pause key in games.json.")

    def state(self):
        return "paused" if self.paused else "running"

    def wake(self):
        # If the last pause failed, the game has been running all along.
        self._since = None if self.paused else api.qpc()

    def resume(self, tap):
        if not self.paused:
            return None
        self._since = api.qpc()
        pressed, problem = tap(self.resume_keys)
        if pressed:
            self.paused = False
        else:
            self._since = None
        if problem:
            return (f"the resume key may not have registered ({problem})" if pressed
                    else f"the game was not resumed ({problem})")
        return None

    def hold(self, tap):
        pressed, problem = False, None
        if not self.paused:
            pressed, problem = tap(self.pause_keys)
            if pressed:
                self.paused = True
        self._stop_clock()
        if problem and pressed:
            return f"the pause key may not have registered ({problem}); the game may still be running"
        if problem:
            return f"the game could not be paused and is still running ({problem})"
        return None

    def repaused(self):
        """Record that the pause key was pressed outside a step."""
        self.paused = True

    def letting_go(self):
        if self.paused:
            return f"the game was left paused; press {'+'.join(self.resume_keys)} in it to continue"
        return "the game is running"


class Freeze(_Timed):
    """Suspend the whole process. Works for any game, but antivirus software watches this behavior."""

    kind = "freeze"
    capture_first = False
    check_motion = False

    def __init__(self, process):
        super().__init__()
        self._process = process

    def state(self):
        return "frozen"

    def wake(self):
        # Before focusing: a suspended window cannot come forward.
        self._since = api.qpc()
        self._process.thaw()

    def resume(self, tap):
        return None

    def hold(self, tap):
        if self._process.alive():
            self._process.freeze()
        self._stop_clock()
        return None

    def letting_go(self):
        return "the game is running normally again"


class Free(_Timed):
    """Not held at all: for turn-based games and others that wait for input on their own."""

    kind = "none"
    capture_first = True
    check_motion = False

    def state(self):
        return "unheld"

    def wake(self):
        self._since = api.qpc()

    def resume(self, tap):
        return None

    def hold(self, tap):
        self._stop_clock()
        return None

    def letting_go(self):
        return "the game is still running"


def for_game(game, process):
    if game.hold == "pause_key":
        return Pause(game)
    if game.hold == "freeze":
        return Freeze(process)
    return Free()
