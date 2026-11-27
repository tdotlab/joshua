"""The browser backend with a real headless Chromium, playing browserpage.html.

Runs only where Playwright and its Chromium are installed (see README.md). It is headless: it
takes neither the screen nor the keyboard.
"""

import functools
import http.server
import os
import threading
import time
import unittest

import support
from joshua import browser, config, proctree, timeline


def playwright_missing():
    """Why the browser cannot be used here, or None."""
    if support.sys.platform != "win32" and os.geteuid() == 0 and os.environ.get(browser.NO_SANDBOX) != "1":
        return f"running as root, where Joshua refuses to start Chromium unless {browser.NO_SANDBOX}=1"
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "Playwright is not installed for this Python"
    try:
        with sync_playwright() as playwright:
            playwright.chromium.launch(chromium_sandbox=os.environ.get(browser.NO_SANDBOX) != "1").close()
    except Exception as error:
        return f"Playwright's Chromium does not start: {str(error).splitlines()[0][:200]}"
    return None


class Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


MISSING = playwright_missing()
SERVER = None


def setUpModule():
    global SERVER
    if MISSING:
        return
    SERVER = http.server.ThreadingHTTPServer(("127.0.0.1", 0), functools.partial(Quiet, directory=str(
        support.PROJECT / "tests")))
    threading.Thread(target=SERVER.serve_forever, daemon=True).start()


def tearDownModule():
    if SERVER:
        SERVER.shutdown()
        SERVER.server_close()


@unittest.skipIf(MISSING, MISSING)
class BrowserLiveTests(unittest.TestCase):
    def start(self, query="", **settings):
        origin = f"http://127.0.0.1:{SERVER.server_address[1]}"
        game = config.BrowserGame(name="test page", url=f"{origin}/browserpage.html{query}", origins=(origin,),
                                  **settings)
        session, result = browser.BrowserSession.start(game)
        self.addCleanup(session.close)
        return session, result

    def page(self, session):
        return session._run(lambda: session._page.evaluate("JSON.parse(JSON.stringify(s))"), 30, "testing")

    def step(self, session, *raw, settle_ms=0):
        return session.step(timeline.parse(list(raw)), settle_ms)

    def test_nothing_runs_between_steps(self):
        session, result = self.start()
        self.assertEqual(result.state, "clock")
        self.assertEqual((result.frame.width, result.frame.height), (1280, 720))
        before = self.page(session)
        time.sleep(0.5)
        after = self.page(session)
        self.assertEqual((before["frames"], before["timers"]), (after["frames"], after["timers"]))

    def test_a_step_runs_exactly_its_game_time(self):
        session, _ = self.start()
        before = self.page(session)
        result = self.step(session, {"ms": 1000, "keys": ["d"]})
        after = self.page(session)
        self.assertEqual((result.played_ms, result.ran_ms), (1000, 1000))
        self.assertAlmostEqual(after["x"], 100, delta=2)  # 100 px a second of game time
        self.assertEqual(after["timers"] - before["timers"], 10)
        self.assertAlmostEqual(after["frames"] - before["frames"], 62, delta=2)
        self.assertEqual((after["downs"], after["ups"]), (["KeyD"], ["KeyD"]))
        self.assertIn('"held":[]', result.reported)

    def test_longer_frames(self):
        session, _ = self.start(frame_ms=50)
        before = self.page(session)
        result = self.step(session, {"ms": 1000, "keys": ["d"]})
        after = self.page(session)
        self.assertEqual(result.ran_ms, 1000)
        self.assertAlmostEqual(after["frames"] - before["frames"], 20, delta=1)
        self.assertAlmostEqual(after["x"], 100, delta=6)

    def test_a_captured_mouse_turns_exactly_and_esc_lets_go(self):
        session, _ = self.start()
        self.step(session, {"ms": 100, "buttons": ["left"], "mouse_to": [400, 300]},
                  {"ms": 500, "mouse_move": [300, -50]})
        state = self.page(session)
        self.assertEqual((state["locked"], state["yaw"], state["pitch"], state["clicks"]), (True, 300, -50, 1))
        self.step(session, {"ms": 100, "keys": ["esc"]}, {"ms": 100, "mouse_move": [40, 40]})
        state = self.page(session)
        self.assertEqual((state["locked"], state["yaw"]), (False, 300))
        self.assertNotIn("Escape", state["downs"])  # a browser keeps the Esc that ends a capture

    def test_misbehaviour_is_contained_and_reported(self):
        session, result = self.start("?blocked=http://127.0.0.2:9/x&popup=1&alert=1&error=1")
        said = result.reported
        self.assertIn("deliberate test error", said)
        self.assertIn("alert: hello from the page", said)
        self.assertIn("http://127.0.0.2:9 (1)", said)
        self.assertIn("ws://127.0.0.2:9 (1)", said)
        self.assertNotIn("ERR_BLOCKED_BY_CLIENT", said)
        pages = session._run(lambda: len(session._context.pages), 30, "testing")
        self.assertEqual(pages, 1)
        again = self.step(session, {"ms": 100})
        self.assertNotIn("deliberate", again.reported or "")  # reported once

    def test_webrtc_is_gone_in_every_frame(self):
        # WebRTC reaches TURN servers over TCP outside the request filter; see docs/BROWSER.md.
        session, _ = self.start()
        found = session._run(lambda: session._page.evaluate("""() => {
            const names = ['RTCPeerConnection', 'webkitRTCPeerConnection', 'RTCIceTransport'];
            const blank = document.createElement('iframe');
            document.body.appendChild(blank);
            const seen = [];
            for (const [where, w] of [['page', window], ['iframe', blank.contentWindow]])
                for (const name of names) if (typeof w[name] === 'function') seen.push(where + ':' + name);
            try { new RTCPeerConnection(); seen.push('constructed'); } catch (error) {}
            return seen;
        }"""), 30, "testing")
        self.assertEqual(found, [])
        frames = session._run(lambda: [frame.evaluate("typeof RTCPeerConnection")
                                       for frame in session._page.frames], 30, "testing")
        self.assertEqual(set(frames), {"undefined"})
        self.assertGreaterEqual(len(frames), 2)

    def test_a_page_that_eats_memory_is_stopped(self):
        session, _ = self.start("?hog=20", memory_mb=512)
        result = self.step(session, {"ms": 5000})
        self.assertEqual(result.state, "exited")
        self.assertIn("more than the 512 MB allowed", result.stopped)
        self.assertFalse(session.alive())
        with self.assertRaisesRegex(Exception, "no longer running"):
            self.step(session, {"ms": 100})

    def test_closing_leaves_no_browser_behind(self):
        session, _ = self.start()
        seen = session._processes()
        self.assertTrue(seen)
        session.close()
        listing = proctree.table()
        self.assertEqual([pid for pid in seen + session._roots if pid in listing
                          and proctree.started(pid, listing) == session._seen.get(pid)], [])


if __name__ == "__main__":
    unittest.main()
