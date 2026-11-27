"""Let an agent play a game one held step at a time: a Windows game, or a web page in headless Chromium."""

__version__ = "0.1.0"


class JoshuaError(Exception):
    """A failure whose message is meant to be read by the agent or its user."""
