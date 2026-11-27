"""A game in a web page, played in headless Chromium and held still by its own clock.

Playwright's clock stands in for the page's time: between steps no animation frame,
timer or clock reading moves, and a step advances exactly the game time it asks for.
Playwright objects belong to the thread that made them, and the server answers calls on
several threads, so one worker thread owns the browser and every call goes through it.
See docs/BROWSER.md.
"""

import math
import os
import queue
import re
import struct
import sys
import threading
import time

from . import JoshuaError
from . import config, proctree
from .capture import Frame
from .result import Result

SLICE_MS = 50             # input and look are applied between slices of at most this much game time
STARTUP_GAME_MS = 5000    # game time a page gets at most to draw its first frames
STARTUP_FRAMES = 3
STARTUP_SECONDS = 120     # real time allowed to load and start
NETWORK_WAIT_SECONDS = 10  # real time spent waiting, during start, for the page's requests to finish
STEP_SECONDS = 120        # real time a step may take, or STEP_SECONDS_PER_GAME_SECOND if more
STEP_SECONDS_PER_GAME_SECOND = 15
WATCH_SECONDS = 0.1      # how often the browser's memory is measured
CLOSE_SECONDS = 15
MAX_MESSAGES = 10
MAX_MESSAGE = 300
MAX_STATE = 4000

# Chromium's flags. GPU drawing where there is a GPU, software WebGL where there is none, no
# sound, and no address leaks around the request filter (WebRTC, DNS prefetching).
ARGS = ["--enable-gpu", "--enable-unsafe-swiftshader", "--mute-audio", "--dns-prefetch-disable",
        "--force-webrtc-ip-handling-policy=disable_non_proxied_udp"]
NO_SANDBOX = "JOSHUA_BROWSER_NO_SANDBOX"

# Joshua's key names (timeline.KEYS) as Playwright's.
KEYS = {
    **{letter: f"Key{letter.upper()}" for letter in "abcdefghijklmnopqrstuvwxyz"},
    **{digit: f"Digit{digit}" for digit in "0123456789"},
    **{f"f{n}": f"F{n}" for n in range(1, 13)},
    **{f"numpad{n}": f"Numpad{n}" for n in range(10)},
    "esc": "Escape", "minus": "Minus", "equals": "Equal", "backspace": "Backspace", "tab": "Tab",
    "leftbracket": "BracketLeft", "rightbracket": "BracketRight", "enter": "Enter", "ctrl": "ControlLeft",
    "semicolon": "Semicolon", "apostrophe": "Quote", "grave": "Backquote", "shift": "ShiftLeft",
    "backslash": "Backslash", "comma": "Comma", "period": "Period", "slash": "Slash",
    "rightshift": "ShiftRight", "numpad_multiply": "NumpadMultiply", "alt": "AltLeft", "space": "Space",
    "capslock": "CapsLock", "numpad_subtract": "NumpadSubtract", "numpad_add": "NumpadAdd",
    "numpad_decimal": "NumpadDecimal", "numpad_enter": "NumpadEnter", "rightctrl": "ControlRight",
    "numpad_divide": "NumpadDivide", "rightalt": "AltRight", "home": "Home", "up": "ArrowUp",
    "pageup": "PageUp", "left": "ArrowLeft", "right": "ArrowRight", "end": "End", "down": "ArrowDown",
    "pagedown": "PageDown", "insert": "Insert", "delete": "Delete", "pause": "Pause",
}

# Added to every page after the clock. Pointer lock is pretended: headless Chromium's real
# lock undoes every mouse move, and asking for it while the clock is held ran memory away.
# The game is told it has the lock, and look arrives as mousemove events with movementX/Y.
# Esc lets go, as in a real browser, which does not pass that Esc on to the page.
# Animation frames are counted, to know when a page has started drawing.
PAGE_SCRIPT = r"""
(() => {
  if (window.__joshuaLook) return;
  let locked = null;
  const held = () => (locked && locked.isConnected ? locked : null);
  const announce = () => queueMicrotask(() => document.dispatchEvent(new Event('pointerlockchange')));
  Object.defineProperty(Document.prototype, 'pointerLockElement', { configurable: true, get: held });
  Element.prototype.requestPointerLock = function () { locked = this; announce(); return Promise.resolve(); };
  Document.prototype.exitPointerLock = function () { if (locked) { locked = null; announce(); } };
  window.addEventListener('keydown', (event) => {
    if (event.key === 'Escape' && held()) {
      event.stopImmediatePropagation();
      event.preventDefault();
      document.exitPointerLock();
    }
  }, true);
  const look = (dx, dy) => {
    const target = held();
    if (!target) return false;
    const motion = { bubbles: true, cancelable: true, composed: true, movementX: dx, movementY: dy };
    target.dispatchEvent(new PointerEvent('pointermove', { ...motion, pointerType: 'mouse', isPrimary: true }));
    target.dispatchEvent(new MouseEvent('mousemove', motion));
    return true;
  };
  let frames = 0, last = null;
  const raf = window.requestAnimationFrame.bind(window);
  window.requestAnimationFrame = (callback) => raf((at) => {
    if (at !== last) { last = at; frames += 1; }
    return callback(at);
  });
  Object.defineProperty(window, '__joshuaLook', { value: look });
  Object.defineProperty(window, '__joshuaFrames', { value: () => frames });
})();
"""

STATE_SCRIPT = """() => {
  if (typeof window.joshuaState !== 'function') return null;
  try {
    const value = window.joshuaState();
    return JSON.stringify(value === undefined ? null : value);
  } catch (error) {
    return 'joshuaState() failed: ' + error;
  }
}"""


def png_size(png):
    """Width and height from a PNG's header, without decoding it."""
    if png[:8] != b"\x89PNG\r\n\x1a\n" or png[12:16] != b"IHDR":
        raise JoshuaError("the browser returned a screenshot that is not a PNG")
    return struct.unpack(">II", png[16:24])


def request_allowed(url, origins):
    """Whether the page may load url. Only addresses that leave the browser are checked."""
    scheme = url.split(":", 1)[0].lower()
    if scheme in ("data", "blob", "about", "chrome-extension"):
        return True
    if scheme in ("ws", "wss"):
        url = ("http" if scheme == "ws" else "https") + url[len(scheme):]
    try:
        return config.origin_of(url, "address") in origins
    except JoshuaError:
        return False


def _clip(text, limit):
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[:limit - 3] + "..."


class Player:
    """Plays segments into a held page. Only here does the page's clock move.

    page needs .keyboard.down/up, .mouse.move/down/up and .clock.run_for/fast_forward;
    look(dx, dy) sends look to a page that holds the pointer and says whether it did.
    """

    def __init__(self, page, look, viewport, frame_ms, thrown=None):
        self.page = page
        self.look = look
        # Under the clock, an exception from the page's own timers and frames comes out of the clock
        # call, after the whole interval has run. thrown(error) returns True if it was the page's.
        self.thrown = thrown or (lambda error: False)
        self.viewport = viewport
        self.frame_ms = frame_ms
        self.pointer = (0, 0)  # Playwright's mouse starts at the top left corner
        self.played = 0        # input time played in the current step
        self.ran = 0           # game time passed in the current step
        self._owed = 0         # with frames longer than a slice: game time not yet advanced

    def check(self, segments):
        width, height = self.viewport
        for index, segment in enumerate(segments):
            if segment.mouse_to and not (0 <= segment.mouse_to[0] < width and 0 <= segment.mouse_to[1] < height):
                raise JoshuaError(f"segment {index}: mouse_to {list(segment.mouse_to)} is outside the"
                                  f" {width}x{height} screenshot")

    def play(self, segments, settle_ms):
        """Returns the input time played. Every key and button is released, whatever happens."""
        self.played = self.ran = 0
        keys, buttons = [], []
        try:
            for segment in segments:
                for key in [key for key in keys if key not in segment.keys]:
                    keys.remove(key)
                    self.page.keyboard.up(KEYS[key])
                for button in [button for button in buttons if button not in segment.buttons]:
                    buttons.remove(button)
                    self.page.mouse.up(button=button)
                if segment.mouse_to:
                    self.pointer = segment.mouse_to
                    self.page.mouse.move(*self.pointer)
                for key in segment.keys:
                    if key not in keys:
                        keys.append(key)
                        self.page.keyboard.down(KEYS[key])
                for button in segment.buttons:
                    if button not in buttons:
                        buttons.append(button)
                        self.page.mouse.down(button=button)
                self.advance(segment.ms, segment.mouse_move)
                self.played += segment.ms
        finally:
            self.release(keys, buttons)
        self.advance(settle_ms)
        self._catch_up()
        return self.played

    def release(self, keys, buttons):
        for key in reversed(keys):
            try:
                self.page.keyboard.up(KEYS[key])
            except Exception:  # the page is gone; nothing is held any more
                pass
        for button in buttons:
            try:
                self.page.mouse.up(button=button)
            except Exception:
                pass

    def advance(self, ms, look=None):
        """Let ms of game time pass, spreading look evenly over it."""
        if ms <= 0:
            return
        count = math.ceil(ms / max(SLICE_MS, self.frame_ms))
        for index in range(count):
            part = ms * (index + 1) // count - ms * index // count
            if look:
                dx = look[0] * (index + 1) // count - look[0] * index // count
                dy = look[1] * (index + 1) // count - look[1] * index // count
                if dx or dy:
                    self._look(dx, dy)
            self._tick(part)

    def _look(self, dx, dy):
        if self.look(dx, dy):
            return
        # Nothing holds the pointer: move it instead, staying on the page.
        x = min(max(self.pointer[0] + dx, 0), self.viewport[0] - 1)
        y = min(max(self.pointer[1] + dy, 0), self.viewport[1] - 1)
        self.pointer = (x, y)
        self.page.mouse.move(x, y)

    def _tick(self, ms):
        if self.frame_ms <= config.MIN_FRAME_MS:
            self._clock(self.page.clock.run_for, ms)  # every animation frame and timer, 16 ms apart
            self.ran += ms
            return
        # Longer frames: jump frame_ms at a time, so each jump draws one frame.
        self._owed += ms
        while self._owed >= self.frame_ms:
            self._clock(self.page.clock.fast_forward, self.frame_ms)
            self._owed -= self.frame_ms
            self.ran += self.frame_ms

    def _catch_up(self):
        if self._owed:
            self._clock(self.page.clock.fast_forward, self._owed)
            self.ran += self._owed
            self._owed = 0

    def _clock(self, move, ms):
        try:
            move(ms)
        except Exception as error:
            if not self.thrown(error):
                raise


class _Overdue(Exception):
    pass


class _DriverFailed(JoshuaError):
    """Playwright's driver, a Node.js program, stopped while it was starting."""


class _Worker:
    """One thread that owns every Playwright object."""

    def __init__(self):
        self._jobs = queue.Queue()
        self._thread = threading.Thread(target=self._loop, name="joshua-browser", daemon=True)
        self._thread.start()

    def _loop(self):
        while True:
            job = self._jobs.get()
            if job is None:
                return
            work, box, done = job
            try:
                box["value"] = work()
            except BaseException as error:  # handed back to the caller
                box["error"] = error
            done.set()

    def call(self, work, seconds, on_overdue):
        """Run work on the worker. If it takes longer than seconds, on_overdue() is called once and the
        work is waited for a little longer, since stopping the browser makes it fail."""
        box, done = {}, threading.Event()
        self._jobs.put((work, box, done))
        if not done.wait(seconds):
            on_overdue()
            if not done.wait(CLOSE_SECONDS):
                raise _Overdue()
        if "error" in box:
            raise box["error"]
        return box["value"]

    def stop(self):
        self._jobs.put(None)


class BrowserSession:
    """A browser game, with the surface server.Server uses: start, step, close, frame, game and alive."""

    def __init__(self, game):
        self.game = game
        self.frame = None
        self.peak_mb = 0
        self._dead = None       # why the browser stopped, once it has
        self._closed = False
        self._roots = []        # Playwright's driver, under which Chromium runs
        self._seen = {}         # every browser process seen: pid -> when it started
        self._launched = False
        self._worker = _Worker()
        self._watching = threading.Event()
        self._pw = self._browser = self._context = self._page = self._cdp = self._player = None
        self._messages = []     # console errors and the like since the last turn
        self._dropped = 0
        self._blocked = {}      # origin -> how many requests to it were refused
        self._dialogs = []
        self._inflight = 0

    # -- what the server sees ---------------------------------------------------

    @property
    def process(self):
        return self  # the server asks session.process.alive()

    def alive(self):
        return self._dead is None and not self._closed

    @classmethod
    def start(cls, game):
        _check_sandbox()
        try:
            return cls._start_once(game)
        except _DriverFailed:  # seen now and then on Windows: one more try, from scratch
            return cls._start_once(game)

    @classmethod
    def _start_once(cls, game):
        session = cls(game)
        try:
            frame, ran, reported = session._run(session._open, STARTUP_SECONDS, "starting")
        except BaseException:
            session.close()
            raise
        return session, Result(frame, 0, ran, state="clock", reported=reported)

    def step(self, segments, settle_ms=100):
        if not self.alive():
            raise JoshuaError(f"the browser is no longer running ({self._dead or 'closed'}); call start_game"
                              " again")
        self._player.check(segments)
        game_seconds = (sum(segment.ms for segment in segments) + settle_ms) / 1000
        seconds = max(STEP_SECONDS, STEP_SECONDS_PER_GAME_SECOND * game_seconds)
        try:
            frame, reported = self._run(lambda: self._step(segments, settle_ms), seconds, "playing")
        except JoshuaError as error:
            if self.alive():
                raise
            why = str(error)
            return Result(None, self._player.played, self._player.ran, why, why, "exited")
        return Result(frame, self._player.played, self._player.ran, state="clock", reported=reported)

    def repause(self):
        raise JoshuaError("browser games are held by their clock, so there is no pause key to press again")

    def try_pause(self, pause_key, resume_key, pause_ms, watch_ms):
        raise JoshuaError("browser games are always held by their clock; try_pause is for Windows games listed"
                          " with \"hold\": \"none\"")

    def close(self, close_game=False):
        """The browser is Joshua's own, so it is always closed."""
        if self._closed:
            return "the browser was already closed"
        was = self._dead
        self._closed = True
        try:
            self._worker.call(self._shut, CLOSE_SECONDS, self._kill)
        except BaseException:
            self._kill()
        finally:
            self._watching.set()
            self._worker.stop()
            self._sweep()
        return f"the browser had already stopped: {was}" if was else "the browser was closed"

    # -- the worker's side --------------------------------------------------------

    def _run(self, work, seconds, doing):
        """Run work on the worker, within seconds of real time, and explain any failure."""
        from playwright.sync_api import Error as PlaywrightError

        def overdue():
            if self._dead is None:
                self._dead = (f"{doing} took longer than {round(seconds)} s of real time, so the browser was"
                              " stopped. The page may be stuck in a loop, or too slow to draw on this machine:"
                              " try shorter steps, a smaller viewport or a larger frame_ms")
            self._kill()

        try:
            return self._worker.call(work, seconds, overdue)
        except _Overdue:
            raise JoshuaError(self._dead) from None
        except PlaywrightError as error:
            if self._dead:
                raise JoshuaError(self._dead) from None
            if self._page is not None and self._page.is_closed():
                self._dead = "the page closed"
            raise JoshuaError(f"the browser failed while {doing}: {_clip(error.message, 500)}") from None

    def _open(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            raise JoshuaError("browser games need Playwright, which is not installed for this Python: pip"
                              " install playwright, then python -m playwright install chromium --only-shell"
                              " (see README.md)") from None
        game = self.game
        before = set(proctree.children(os.getpid()))
        try:
            self._pw = sync_playwright().start()
        except Exception as error:
            hint = "" if sys.platform == "win32" else " (on Debian or Ubuntu it needs libatomic1)"
            raise _DriverFailed(f"Playwright's driver did not start{hint}: {_clip(str(error), 300)}") from None
        self._roots = [pid for pid in proctree.children(os.getpid()) if pid not in before]
        self._seen.update({pid: proctree.started(pid) for pid in self._roots})
        threading.Thread(target=self._watch, name="joshua-browser-watch", daemon=True).start()
        launching = {"headless": True, "chromium_sandbox": os.environ.get(NO_SANDBOX) != "1", "args": ARGS}
        page_options = {"viewport": {"width": game.viewport[0], "height": game.viewport[1]},
                        "device_scale_factor": 1, "accept_downloads": False, "service_workers": "block"}
        try:
            if game.keep_profile:
                profile = config.profile_dir(game)
                profile.mkdir(parents=True, exist_ok=True)
                self._context = self._pw.chromium.launch_persistent_context(str(profile), **launching,
                                                                            **page_options)
            else:
                self._browser = self._pw.chromium.launch(**launching)
                self._context = self._browser.new_context(**page_options)
        except Exception as error:
            raise JoshuaError(_launch_failure(error)) from None
        self._launched = True
        context = self._context
        context.on("page", self._new_page)
        context.on("request", self._request_started)
        context.on("requestfinished", self._request_ended)
        context.on("requestfailed", self._request_ended)
        context.route(re.compile(r".*"), self._route)
        context.route_web_socket(re.compile(r".*"), self._route_socket)
        context.clock.install()
        context.clock.pause_at(time.time() * 1000 + 1000)
        context.add_init_script(PAGE_SCRIPT)
        self._page = context.pages[0] if context.pages else context.new_page()
        page = self._page
        page.on("console", self._console)
        page.on("pageerror", self._page_error)
        page.on("crash", self._crashed)
        page.on("dialog", self._dialog)
        self._cdp = context.new_cdp_session(page)
        self._cdp.send("Animation.enable")
        self._animations(0)
        self._player = Player(page, self._send_look, game.viewport, game.frame_ms, self._thrown)
        page.goto(game.url, wait_until="load", timeout=60000)
        ran = self._warm_up()
        return self._photograph(), ran, self._gather()

    def _warm_up(self):
        """Give a freshly loaded page game time until it has drawn a few frames.

        Time stands still while its files arrive, as it does between steps.
        """
        player, network_until = self._player, time.monotonic() + NETWORK_WAIT_SECONDS
        ran = 0
        while ran < STARTUP_GAME_MS:
            while self._inflight > 0 and time.monotonic() < network_until:
                self._page.wait_for_timeout(50)  # real time, which the page's clock ignores
            if ran and self._page.evaluate("window.__joshuaFrames ? window.__joshuaFrames() : 0") >= STARTUP_FRAMES:
                break
            player.advance(100)
            player._catch_up()
            ran += 100
        return ran

    def _step(self, segments, settle_ms):
        self._animations(1)
        try:
            self._player.play(segments, settle_ms)
        finally:
            if not self._page.is_closed():
                self._animations(0)
        return self._photograph(), self._gather()

    def _photograph(self):
        png = self._page.screenshot(type="png", timeout=30000)
        width, height = png_size(png)
        self.frame = Frame(png, width, height, width, height, False)
        return self.frame

    def _send_look(self, dx, dy):
        return bool(self._page.evaluate("([dx, dy]) => window.__joshuaLook ? window.__joshuaLook(dx, dy) : false",
                                        [dx, dy]))

    def _animations(self, rate):
        """CSS animations and transitions are not on the page's clock: stop them between steps."""
        try:
            self._cdp.send("Animation.setPlaybackRate", {"playbackRate": rate})
        except Exception:  # an older or odd page; the clock still holds everything else
            pass

    def _gather(self):
        """What the page said since the last turn, for the agent, marked as the page's."""
        lines = []
        if self._messages:
            more = f" (and {self._dropped} more)" if self._dropped else ""
            lines.append(f"Console errors{more}:")
            lines.extend(f"- {message}" for message in self._messages)
        if self._dialogs:
            lines.append("Dialogs it opened, which were dismissed:")
            lines.extend(f"- {text}" for text in self._dialogs)
        if self._blocked:
            listed = ", ".join(f"{origin} ({count})" for origin, count in self._blocked.items())
            lines.append(f"Requests refused, to origins not in games.json: {listed}")
        state = self._page.evaluate(STATE_SCRIPT) if not self._page.is_closed() else None
        if state is not None:
            lines.append(f"joshuaState(): {_clip(state, MAX_STATE)}")
        self._messages, self._dropped, self._dialogs, self._blocked = [], 0, [], {}
        return "\n".join(lines) or None

    def _shut(self):
        try:
            if self._context:
                self._context.close()
            if self._browser:
                self._browser.close()
        finally:
            if self._pw:
                self._pw.stop()

    # -- events, on the worker while it waits on Playwright -----------------------

    def _note(self, text):
        if len(self._messages) < MAX_MESSAGES:
            self._messages.append(_clip(text, MAX_MESSAGE))
        else:
            self._dropped += 1

    def _console(self, message):
        if message.type in ("error", "assert") and "ERR_BLOCKED_BY_CLIENT" not in message.text:
            self._note(message.text)  # refused requests are listed on their own

    def _page_error(self, error):
        self._note(f"uncaught {error.name or 'error'}: {error.message}")

    def _thrown(self, error):
        """An exception the page's code threw while the clock ran it: the page's, not the browser's."""
        text = getattr(error, "message", "")
        if self._dead or self._page.is_closed() or not text.startswith("Clock.") or "closed" in text.split("\n")[0]:
            return False
        text = text.split(": ", 1)[-1]
        self._note("uncaught, in a timer or animation frame: " + text.split(" at ClockController")[0])
        return True

    def _crashed(self, page):
        self._dead = self._dead or "the page crashed (it may have run out of memory)"

    def _dialog(self, dialog):
        if len(self._dialogs) < MAX_MESSAGES:
            self._dialogs.append(_clip(f"{dialog.type}: {dialog.message}", MAX_MESSAGE))
        dialog.dismiss()

    def _new_page(self, page):
        if page is not self._page and self._page is not None:
            page.close()  # popups and new windows: the game has one page

    def _request_started(self, request):
        self._inflight += 1

    def _request_ended(self, request):
        self._inflight = max(0, self._inflight - 1)

    def _route(self, route):
        if request_allowed(route.request.url, self.game.origins):
            route.fallback()
        else:
            self._refused(route.request.url)
            route.abort("blockedbyclient")

    def _route_socket(self, socket):
        if request_allowed(socket.url, self.game.origins):
            socket.connect_to_server()
        else:
            self._refused(socket.url)
            socket.close()

    def _refused(self, url):
        scheme = url.split(":", 1)[0].lower()
        try:
            origin = config.origin_of({"ws": "http", "wss": "https"}.get(scheme, scheme) + url[len(scheme):],
                                      "address")
            origin = scheme + origin[origin.index(":"):]
        except (JoshuaError, ValueError):
            origin = scheme + ":"
        self._blocked[origin] = self._blocked.get(origin, 0) + 1

    # -- the browser's processes, from any thread ---------------------------------

    def _processes(self):
        listing = proctree.table()
        found = [pid for root in self._roots for pid in proctree.descendants(root, listing)]
        for pid in found:
            if pid not in self._seen:
                self._seen[pid] = proctree.started(pid, listing)
        return found

    def _sweep(self):
        """Stop any browser process that outlived its parent, which the tree no longer reaches."""
        deadline = time.monotonic() + 5
        while True:
            listing = proctree.table()
            left = [pid for pid, born in list(self._seen.items())
                    if pid in listing and born is not None and proctree.started(pid, listing) == born]
            if not left or time.monotonic() > deadline:
                break
            time.sleep(0.2)  # give the browser a moment to finish exiting on its own
        for pid in left:
            proctree.kill(pid)

    def _kill(self, roots_too=False):
        for pid in reversed(self._processes()):
            proctree.kill(pid)
        if roots_too:
            for pid in self._roots:
                proctree.kill(pid)

    def _watch(self):
        """Stop a browser that uses more memory than the game is allowed, or notice one that exited."""
        limit = self.game.memory_mb * 2 ** 20
        while not self._watching.wait(WATCH_SECONDS):
            pids = self._processes()
            if not pids:
                if self._launched and not self._closed and self._dead is None:
                    self._dead = "the browser exited"
                continue
            used = sum(proctree.memory(pid) for pid in pids)
            self.peak_mb = max(self.peak_mb, used // 2 ** 20)
            if used > limit and self._dead is None and not self._closed:
                self._dead = (f"the browser used {used // 2 ** 20} MB, more than the {self.game.memory_mb} MB"
                              " allowed (memory_mb in games.json), so it was stopped")
                for pid in reversed(pids):
                    proctree.kill(pid)


def _check_sandbox():
    if sys.platform != "win32" and os.geteuid() == 0 and os.environ.get(NO_SANDBOX) != "1":
        raise JoshuaError("Joshua is running as root, where Chromium's sandbox cannot work. Run it as an"
                          " ordinary user. If this machine is itself a throwaway sandbox (a container or VM), the"
                          f" user can set {NO_SANDBOX}=1 to run Chromium without its sandbox.")


def _launch_failure(error):
    text = getattr(error, "message", None) or str(error)
    if "Executable doesn't exist" in text:
        return ("Playwright's Chromium is not installed: python -m playwright install chromium --only-shell"
                " (on Debian or Ubuntu, install libatomic1 first and add --with-deps)")
    if "sandbox" in text.lower():
        return ("Chromium could not start its sandbox on this machine (often: user namespaces are restricted)."
                f" If this machine is itself a throwaway sandbox, the user can set {NO_SANDBOX}=1. Details: "
                + _clip(text, 300))
    return f"the browser did not start: {_clip(text, 500)}"
