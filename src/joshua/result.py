"""What a turn reports, whichever kind of game played it."""

from dataclasses import dataclass

from .capture import Frame


@dataclass
class Result:
    frame: Frame | None
    played_ms: int
    ran_ms: int
    stopped: str | None = None   # why input ended early
    no_frame: str | None = None  # why there is no new screenshot
    state: str = "frozen"        # frozen, paused, running, unheld, clock or exited
    warning: str | None = None
    window_age: int | None = None  # for an unheld game started here: seconds since its window appeared
    reported: str | None = None  # what a browser game's page said: errors and its own state
