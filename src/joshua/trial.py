"""Trying a pause key on a game that is not held yet, to find its games.json settings.

The picture is all there is to go on: watch it move, press the key, watch whether
it stops, press again, watch it move. That is evidence, not proof, so the report
hands the agent the pictures as well as the numbers.
"""

import json
import math
import time
from dataclasses import dataclass

from . import capture
from .config import MAX_PAUSE_MS

SAMPLE_MS = 100
BASELINE_MS = 1000
STILL_MS = 1000       # the picture must hold at least this long to count as stopped
RESUME_MS = 1500
TRANSITION_MS = 500   # a pause screen going away is a change, but not the game moving again
MIN_WATCH_MS, DEFAULT_WATCH_MS, MAX_WATCH_MS = 1500, 3000, 10000
# A step with this pause_ms still tolerates this much more settling before it warns.
SETTLE_SLACK_MS = 50


class _Interrupted(Exception):
    """Why the trial could not go on. No more input is sent after this."""


@dataclass
class _Watch:
    first: capture.Frame
    last: capture.Frame
    earlier: capture.Frame | None  # the latest picture before the final change
    changes: list                  # (ms, Change) for each sample that differs from the one before
    ms: int                        # when the last sample was taken

    def moved(self, since=-1):
        """Everything that changed after `since` milliseconds, as one Change, or None."""
        found = [change for at, change in self.changes if at > since]
        if not found:
            return None
        lefts, tops, rights, bottoms = zip(*(change.box for change in found))
        return capture.Change(max(change.pixels for change in found), found[0].total,
                              (min(lefts), min(tops), max(rights), max(bottoms)))

    def settled_ms(self):
        return self.changes[-1][0] if self.changes else 0


@dataclass
class Trial:
    verdict: str | None = None  # "already_still", "stopped", "changing", or None if it never got that far
    pressed: bool = False       # the pause key went down
    resumed: bool = False       # and the resume key was sent in full
    came_back: bool = False     # the picture moved after that
    problem: str | None = None  # why the trial ended early
    before: capture.Frame | None = None
    motion: capture.Change | None = None    # movement before the key
    earlier: capture.Frame | None = None    # after the key: a picture that differs from `paused`
    paused: capture.Frame | None = None     # the last picture after the key
    leftover: capture.Change | None = None  # movement in the last STILL_MS after the key
    settled_ms: int = 0         # when the picture last changed after the key was released
    held_ms: int = 0            # how long it then stayed the same
    after: capture.Frame | None = None      # the last picture after resuming


def _watch(look, guard, ms, clock, sleep):
    start = clock()
    first = previous = earlier = None
    changes = []
    while True:
        reason = guard()
        if reason:
            raise _Interrupted(reason)
        at = round((clock() - start) * 1000)
        frame, problem = look()
        if problem:
            raise _Interrupted(problem)
        if previous is None:
            first = frame
        else:
            change = capture.difference(previous, frame)
            if change:
                changes.append((at, change))
                earlier = previous
        previous = frame
        if at >= ms:
            return _Watch(first, frame, earlier, changes, at)
        sleep(SAMPLE_MS / 1000)


def run(look, press, guard, pause_key, resume_key, watch_ms, clock=time.monotonic, sleep=time.sleep):
    """Try the key on a game that is in front and running.

    look() -> (frame, problem); press(keys) -> (pressed, problem); guard() -> reason to stop, or None.
    """
    trial = Trial()

    def require(sent, problem):
        if problem or not sent:
            raise _Interrupted(problem or "the key was not sent")

    try:
        baseline = _watch(look, guard, BASELINE_MS, clock, sleep)
        trial.before, trial.motion = baseline.last, baseline.moved()
        if not trial.motion:
            trial.verdict = "already_still"
            return trial
        trial.pressed, problem = press(pause_key)
        require(trial.pressed, problem)
        held = _watch(look, guard, watch_ms, clock, sleep)
        trial.paused, trial.earlier = held.last, held.earlier
        trial.settled_ms = held.settled_ms()
        trial.held_ms = held.ms - trial.settled_ms
        trial.leftover = held.moved(since=held.ms - STILL_MS)
        trial.verdict = "changing" if trial.leftover else "stopped"
        require(*press(resume_key))
        trial.resumed = True
        resumed = _watch(look, guard, RESUME_MS, clock, sleep)
        trial.after, trial.came_back = resumed.last, resumed.moved(since=TRANSITION_MS) is not None
    except _Interrupted as stop:
        trial.problem = str(stop)
    return trial


# -- reporting ----------------------------------------------------------------

def suggested_pause_ms(pause_ms, settled_ms):
    """A pause_ms that gives the game time to settle before Joshua photographs it."""
    if settled_ms <= pause_ms + SETTLE_SLACK_MS:
        return pause_ms
    return min(MAX_PAUSE_MS, math.ceil(settled_ms / 50) * 50)


def settings(pause_key, resume_key, pause_ms, check_pause=True):
    """The games.json settings for a key that worked."""
    def named(keys):
        return keys[0] if len(keys) == 1 else list(keys)

    found = {"hold": "pause_key", "pause_key": named(pause_key)}
    if tuple(resume_key) != tuple(pause_key):
        found["resume_key"] = named(resume_key)
    if pause_ms != 100:
        found["pause_ms"] = pause_ms
    if not check_pause:
        found["check_pause"] = False
    return found


def _area(change):
    share = 100 * change.pixels / change.total
    amount = "under 0.1%" if share < 0.1 else f"{share:.1f}%"
    left, top, right, bottom = change.box
    return f"{amount} of the pixels, within x {left}-{right - 1}, y {top}-{bottom - 1}"


def report(name, trial, pause_key, resume_key, pause_ms):
    """What to tell the agent: strings and frames, in the order to show them."""
    chord, back = "+".join(pause_key), "+".join(resume_key)
    parts = []

    def show(caption, frame):
        if frame:
            parts.extend([f"{caption}:", frame])

    def offer(opening, found):
        parts.append(
            f"{opening} {json.dumps(found)}\nJoshua never edits games.json. Show the user these settings for"
            f" \"{name}\", replacing its \"hold\", and let them approve the change. Then end_session and"
            " start_game to use them.")

    if trial.verdict is None:
        if trial.pressed:
            parts.append(f"The trial stopped early: {trial.problem}. {chord} had been pressed and nothing more"
                         " was sent, so the game may be paused: check with step.")
        else:
            parts.append(f"The trial stopped early: {trial.problem}. Nothing was pressed.")
        parts.append("The user may be using the computer: do not retry in a loop.")
        return parts

    if trial.verdict == "already_still":
        parts.append(f"Nothing was pressed: the picture did not change during {BASELINE_MS} ms of watching, so"
                     " a pause could not be told apart. Use step to get into live action with something moving,"
                     " then try again.")
        if trial.before.blank:
            parts.append("The picture is entirely black; the game may be in exclusive fullscreen, which cannot"
                         " be captured. Ask the user to run it windowed.")
        show("The picture", trial.before)
        return parts

    lines = [f"Pressed {chord} for {pause_ms} ms. Before that, the picture was changing:"
             f" {_area(trial.motion)}."]
    if trial.verdict == "stopped":
        lines.append(f"Afterwards it last changed {trial.settled_ms} ms after the key was released, then stayed"
                     f" identical for {trial.held_ms} ms.")
    else:
        lines.append(f"Afterwards it was still changing in the last {STILL_MS} ms watched:"
                     f" {_area(trial.leftover)}.")
    if not trial.resumed:
        lines.append(f"Then the trial stopped early: {trial.problem}. {back} may not have been sent, so the game"
                     " may be paused: check with step.")
    elif trial.problem:
        lines.append(f"Pressed {back}, then the trial stopped early: {trial.problem}.")
    elif trial.came_back:
        lines.append(f"Pressed {back} and the picture moved again." if trial.verdict == "stopped"
                     else f"Pressed {back} afterwards.")
    elif trial.verdict == "stopped":
        lines.append(f"Pressed {back}, but the picture stayed still.")
    else:
        lines.append(f"Pressed {back} afterwards, and only then did the picture stop: the game may be paused"
                     " now, so check with step.")
    parts.append(" ".join(lines))

    show("Before the key", trial.before)
    if trial.verdict == "changing":
        show("After the key, earlier", trial.earlier)
        show("After the key, later", trial.paused)
    else:
        show("After the key", trial.paused)
    if trial.problem:
        show("Last picture", trial.after)
        return parts

    if trial.verdict == "changing":
        show("After pressing again", trial.after)
        parts.append("Compare the pictures. If the game itself kept going, this key does not pause it (or takes"
                     " longer than watch_ms to): try another.")
        if trial.came_back:
            offer("Only if the game held still and just a counter, overlay or animated pause screen moved, these"
                  " settings fit:", settings(pause_key, resume_key, pause_ms, check_pause=False))
    elif not trial.came_back:
        show("After pressing again", trial.after)
        parts.append(f"These settings are not confirmed. Either the game needs another key to resume (try"
                     f" resume_key), or {chord} did something other than pause. Look with step, get back into"
                     " live action and try again.")
    else:
        needed = suggested_pause_ms(pause_ms, trial.settled_ms)
        if needed != pause_ms:
            parts.append(f"The game took longer to settle than pause_ms {pause_ms} allows for. Run try_pause"
                         f" again with pause_ms {needed} to confirm.")
        parts.append("This is evidence, not proof: a map or inventory screen can hide a game that keeps"
                     " running. Look at the picture after the key. If it is dimmed or covered by a menu, also add"
                     " \"capture\": \"before_pause\".")
        offer("Settings that fit:", settings(pause_key, resume_key, needed,
                                             check_pause=trial.settled_ms <= MAX_PAUSE_MS))
    return parts
