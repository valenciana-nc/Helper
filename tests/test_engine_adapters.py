"""Adapter and Qt checks use fakes and an isolated local test browser only."""
from __future__ import annotations

import os
import json
import queue
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from helper_engine.browser import BrowserDriver
from helper_engine.router import HybridDriver
from helper_engine.types import Action, Effect, Execution, RunReport, RunStatus, StaleObservation, Target
from helper_engine.uia_worker import UiaClient, UiaTimeout
from helper_engine.windows import WindowsDriver
from test_engine import frame


class AdapterTests(unittest.TestCase):
    def test_uia_unknown_never_falls_back_to_click(self):
        class Uia:
            def request(self, *args, **kwargs):
                return {"status": "unknown"}
        driver = WindowsDriver(uia=Uia())
        with patch.object(driver, "_focus", side_effect=AssertionError("must not send synthetic input")):
            result = driver.execute(Action("click", Target("u1")), frame())
        self.assertEqual(result.effect, Effect.UNKNOWN)
        self.assertEqual(result.route, "uia")

    def test_uia_expired_target_is_not_clicked(self):
        class Uia:
            def request(self, *args, **kwargs):
                return {"status": "stale"}
        with self.assertRaises(StaleObservation):
            WindowsDriver(uia=Uia()).execute(Action("click", Target("u1")), frame())

    def test_worker_timeout_terminates_worker(self):
        class Process:
            terminated = False
            def is_alive(self):
                return True
            def terminate(self):
                self.terminated = True
            def join(self, timeout):
                pass
        class Pipe:
            closed = False
            def send(self, payload):
                pass
            def poll(self, timeout):
                return False
            def close(self):
                self.closed = True
        client = UiaClient(0.01)
        process, pipe = Process(), Pipe()
        client._process, client._pipe = process, pipe
        with self.assertRaises(UiaTimeout):
            client.request("act")
        self.assertTrue(process.terminated)
        self.assertTrue(pipe.closed)
        self.assertIsNone(client._process)

    def test_browser_endpoint_must_be_explicit_loopback(self):
        for endpoint in ("http://example.com:9222", "file:///browser", "http://user:secret@localhost:9222"):
            with self.subTest(endpoint=endpoint), self.assertRaises(ValueError):
                BrowserDriver(endpoint=endpoint)

    def test_browser_navigation_timeout_remains_unknown(self):
        class Page:
            def goto(self, *args, **kwargs):
                raise TimeoutError()
        driver = BrowserDriver(page=Page())
        result = driver.execute(Action("open", text="http://127.0.0.1/test"), frame())
        self.assertEqual(result.effect, Effect.UNKNOWN)

    def test_hybrid_does_not_guess_an_unmatched_tab(self):
        class Desktop:
            def observe(self):
                return frame(title="Current - Google Chrome")
            def execute(self, *args):
                return Execution(Effect.APPLIED, "desktop")
            def validate(self, *args):
                pass
        class Browser:
            def matches_window_title(self, title):
                return False
            def observe(self):
                raise AssertionError("Must not inspect the wrong tab")
        driver = HybridDriver(Desktop(), Browser())
        obs = driver.observe()
        self.assertEqual(driver.execute(Action("click", Target("u1")), obs).route, "desktop")

    def test_browser_does_not_close_attached_profile(self):
        closed = []
        driver = BrowserDriver(endpoint="http://localhost:9222")
        driver._browser = SimpleNamespace(close=lambda: closed.append("browser"))
        driver._playwright = SimpleNamespace(stop=lambda: closed.append("connection"))
        driver.close()
        self.assertEqual(closed, ["connection"])


class QtBridgeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_runner_mode_busy_cancel_and_summary(self):
        from engine_bridge import EngineRunner
        runner = EngineRunner()
        release = threading.Event()
        seen = []
        class FakeAgent:
            def run_goal(self, goal, *, mode, on_event, should_abort):
                seen.append(mode)
                release.wait(2)
                return RunReport(goal, RunStatus.COMPLETED, "Saved correctly.")
        runner._agent = FakeAgent()
        messages, finished = [], []
        runner.message.connect(messages.append)
        runner.finished.connect(finished.append)
        self.assertTrue(runner.start_goal("Do it", mode="help"))
        self.assertFalse(runner.start_goal("Another", mode="active"))
        release.set()
        until = time.monotonic() + 3
        while not finished and time.monotonic() < until:
            self.app.processEvents()
            time.sleep(0.005)
        self.assertEqual(seen, ["help"])
        self.assertEqual(messages, ["Saved correctly."])
        self.assertEqual(finished, [True])
        self.assertFalse(runner.is_running())

    def test_app_startup_constructs_runner_before_backend(self):
        import main
        with (patch.object(main, "ensure_windows_shortcuts", return_value=[]),
              patch.object(main.HelplerDesktopApp, "_setup_tray")):
            app = main.HelplerDesktopApp(self.app, "ctrl+shift+space", startup_surface="background")
        try:
            self.assertFalse(app._engine_runner.is_running())
            self.assertFalse(app._engine_abort.is_set())
            self.assertEqual(app._selected_mode, app._widget.HELP)
        finally:
            app.shutdown()
            for widget in (app._widget, app._chat_window, app._dashboard, app._status_pill):
                widget.close()
                widget.deleteLater()
            self.app.processEvents()


@unittest.skipUnless(os.environ.get("HELPER_TEST_BROWSER"), "Set HELPER_TEST_BROWSER to an isolated Chromium executable")
class BrowserIntegrationTests(unittest.TestCase):
    """Real DOM execution against a local in-memory fixture, without a network or account."""

    def test_dom_fill_navigation_unicode_and_detached_target(self):
        executable = Path(os.environ["HELPER_TEST_BROWSER"])
        self.assertTrue(executable.is_file())
        driver = BrowserDriver(headless=True, executable_path=str(executable))
        try:
            driver._connect()
            driver._page.set_content("""<!doctype html><title>Helper fixture</title>
              <label>Name <input id="name"></label><button id="save">Save</button>
              <output id="result"></output>
              <script>save.onclick=()=>{result.textContent='Saved: '+document.querySelector('#name').value}</script>""")
            obs = driver.observe()
            name = next(e for e in obs.elements if e.name == "Name")
            action = Action("fill", Target(name.id), text="¡Hola 世界!")
            driver.validate(action, obs)
            self.assertEqual(driver.execute(action, obs).effect, Effect.APPLIED)
            obs = driver.observe()
            self.assertEqual(next(e for e in obs.elements if e.name == "Name").value, "¡Hola 世界!")
            save = next(e for e in obs.elements if e.name == "Save")
            action = Action("click", Target(save.id))
            driver.validate(action, obs)
            self.assertEqual(driver.execute(action, obs).effect, Effect.APPLIED)
            obs = driver.observe()
            self.assertIn("Saved: ¡Hola 世界!", obs.text)
            # An element reference must not survive DOM replacement even at the same screen position.
            save = next(e for e in obs.elements if e.name == "Save")
            action = Action("click", Target(save.id))
            driver._page.evaluate("() => document.querySelector('#save').remove()")
            with self.assertRaises(StaleObservation):
                driver.validate(action, obs)
            # Coordinates remain CSS pixels even when the screenshot uses a high DPI.
            self.assertEqual(obs.scale, 1)
        finally:
            driver.close()

    def test_entire_runtime_against_live_dom_fixture(self):
        from helper_engine.runtime import Agent, Limits
        from helper_engine.types import Decision, Predicate
        driver = BrowserDriver(headless=True, executable_path=os.environ["HELPER_TEST_BROWSER"])
        driver._connect()
        driver._page.set_content("<title>Form</title><label>Name <input></label><p>Ready</p>")
        class Model:
            def decide(self, goal, obs, context):
                name = next(e for e in obs.elements if e.name == "Name")
                if name.value != "Ada":
                    return Decision("act", obs.id, action=Action("fill", Target(name.id), text="Ada",
                        expect=(Predicate("value_equals", "Name", value="Ada"),)))
                return Decision("finish", obs.id)
            def verify_goal(self, goal, obs, context):
                correct = next(e for e in obs.elements if e.name == "Name").value == "Ada"
                return correct, "Name field contains Ada."
        report = Agent(model=Model(), driver=driver, limits=Limits(max_seconds=10)).run_goal(
            "Enter Ada in Name", mode="active")
        self.assertTrue(report.ok, report.message)
        self.assertEqual(report.metrics.actions, 1)
        self.assertEqual(report.metrics.routes, {"dom": 1})
        self.assertEqual(report.metrics.model_calls, 3)


@unittest.skipUnless(sys.platform == "win32" and os.environ.get("HELPER_TEST_UIA") == "1",
                     "Opt-in native fixture: HELPER_TEST_UIA=1 on Windows")
class WindowsIntegrationTests(unittest.TestCase):
    def test_native_value_and_invoke_in_disposable_fixture(self):
        fixture = Path(__file__).parent / "fixtures" / "native_window.py"
        env = {**os.environ, "QT_QPA_PLATFORM": "windows"}
        process = subprocess.Popen(
            [sys.executable, str(fixture)], env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        client = UiaClient(timeout=5)
        ready = queue.Queue()
        threading.Thread(target=lambda: ready.put(process.stdout.readline()), daemon=True).start()
        try:
            window = json.loads(ready.get(timeout=10))
            snapshot = client.request("snapshot", token="initial", hwnd=window["hwnd"])
            field = next(item for item in snapshot["elements"]
                         if item["name"] == "Fixture Name" and item["role"] == "Edit")
            button = next(item for item in snapshot["elements"] if item["name"] == "Fixture Save")
            self.assertIn("fill", field["capabilities"])
            result = client.request("act", token="initial", element_id=field["id"], kind="fill", text="Hello UIA")
            self.assertEqual(result["status"], "applied")
            # The same worker/cache owns both operations; no physical input fallback is permitted.
            result = client.request("act", token="initial", element_id=button["id"], kind="click", text="")
            self.assertEqual(result["status"], "applied")
            until = time.monotonic() + 3
            while True:
                snapshot = client.request("snapshot", token="after", hwnd=window["hwnd"])
                if (any("Saved Hello UIA" in item["name"] for item in snapshot["elements"])
                        or time.monotonic() >= until):
                    break
                # Invoke dispatch can return before the fixture's Qt event loop updates the label.
                time.sleep(0.03)
            self.assertTrue(any(item["value"] == "Hello UIA" for item in snapshot["elements"]))
            self.assertTrue(any("Saved Hello UIA" in item["name"] for item in snapshot["elements"]))
        finally:
            client.close()
            if process.poll() is None:
                process.terminate()
            process.communicate(timeout=5)


if __name__ == "__main__":
    unittest.main()
