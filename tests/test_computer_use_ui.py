"""Unified chat controls and passive computer-use frame; no native input or model calls."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QObject, QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton

from engine_bridge import EngineRunner
from helper_engine.types import RunReport, RunStatus
from ui_computer_use import BANNER_TEXT, BANNER_TOP, ComputerUseManager, ComputerUseWindow, paint_computer_use_frame
from ui_ghost_cursor import paint_control_pointer


class Screen(QObject):
    geometryChanged = pyqtSignal(QRect)

    def __init__(self, rect):
        super().__init__()
        self.rect = rect

    def geometry(self):
        return QRect(self.rect)


class FakeApp(QObject):
    screenAdded = pyqtSignal(object)
    screenRemoved = pyqtSignal(object)

    def __init__(self, screens):
        super().__init__()
        self.monitors = screens

    def screens(self):
        return list(self.monitors)


class ComputerUseUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    @contextmanager
    def desktop(self):
        import main
        from conversation_store import ConversationStore
        with (TemporaryDirectory() as folder,
              patch.object(main, "ensure_windows_shortcuts", return_value=[]),
              patch.object(main.HelplerDesktopApp, "_setup_tray"),
              patch.object(main.HelplerDesktopApp, "_initialize_backend")):
            app = main.HelplerDesktopApp(self.qt, "ctrl+shift+space", startup_surface="background")
            app._conversation_store = ConversationStore(Path(folder) / "conversations.json")
            try:
                yield app
            finally:
                app.shutdown()
                for widget in (app._widget, app._chat_window, app._dashboard, app._status_pill):
                    widget.close()
                    widget.deleteLater()
                self.qt.processEvents()

    def test_chat_icon_and_lava_sphere_open_the_same_input(self):
        with self.desktop() as app:
            widget = app._widget
            # The side controls are visible in the expanded hover state.
            widget._hover_amt = 1
            QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
            self.assertTrue(widget.is_chat_open())
            self.assertTrue(widget._chat_input.isVisible())
            widget.set_chat_open(False)
            widget._open_amt = 0
            QTest.mouseClick(widget, Qt.MouseButton.LeftButton, pos=QPoint(62, 20))
            self.assertTrue(widget.is_chat_open())

    def test_existing_help_default_cannot_disable_requested_desktop_actions(self):
        with patch.dict("os.environ", {"HARVIS_DEFAULT_MODE": "help", "HELPER_DEFAULT_MODE": "help"}):
            with self.desktop() as app:
                with patch.object(app._engine_runner, "start_goal", return_value=True) as start:
                    app._start_goal("Open a song in my browser")
                start.assert_called_once_with("Open a song in my browser", mode="active", speak=False)
                self.assertFalse(app._computer_use.active)
                self.assertFalse(any(b.text() in {"Help", "Act"} for b in app._chat_window.findChildren(QPushButton)))

    def test_escape_and_late_events_cannot_restore_the_frame(self):
        with self.desktop() as app:
            app._on_computer_use_changed(True)
            self.assertTrue(app._computer_use.active)
            app._toggle_chat_window_if_visible()
            self.assertFalse(app._computer_use.active)
            app._on_computer_use_changed(True)
            self.assertFalse(app._computer_use.active)

    def test_frame_is_never_shown_for_an_answer_or_guided_help(self):
        runner = EngineRunner()
        changes = []
        runner.computer_use.connect(changes.append)
        runner._on_event("started", {"mode": "active"})
        runner._on_event("decision", {"kind": "answer", "message": "Hello"})
        runner._on_event("finished", {})
        runner._on_event("started", {"mode": "help"})
        runner._on_event("action_start", {"surface": "desktop", "rect": [1, 2, 3, 4]})
        runner._on_event("finished", {})
        self.assertEqual(changes, [])

    def test_active_frame_lasts_until_finish_and_clears_after_worker_failure(self):
        runner = EngineRunner()
        changes, finished = [], []
        runner.computer_use.connect(changes.append)
        runner.finished.connect(finished.append)
        class Agent:
            def run_goal(self, goal, *, mode, on_event, should_abort):
                self.mode = mode
                on_event("started", {"mode": mode})
                on_event("action_start", {"kind": "open", "surface": "desktop"})
                on_event("action_result", {})
                raise RuntimeError("transport disconnected")
        agent = Agent()
        runner._agent = agent
        self.assertTrue(runner.start_goal("Open browser"))
        deadline = time.monotonic() + 3
        while not finished and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(.005)
        self.assertEqual(agent.mode, "active")
        self.assertEqual(changes, [True, False])
        self.assertEqual(finished, [False])

    def test_worker_answer_does_not_flash_computer_use_frame(self):
        runner = EngineRunner()
        finished, changes = [], []
        runner.computer_use.connect(changes.append)
        runner.finished.connect(finished.append)
        class Agent:
            def run_goal(self, goal, *, mode, on_event, should_abort):
                on_event("started", {"mode": mode})
                on_event("decision", {"kind": "answer", "message": "Hello"})
                return RunReport(goal, RunStatus.ANSWERED, "Hello")
        runner._agent = Agent()
        runner.start_goal("Hello")
        deadline = time.monotonic() + 3
        while not finished and time.monotonic() < deadline:
            self.qt.processEvents()
            time.sleep(.005)
        self.assertEqual(changes, [])
        self.assertEqual(finished, [True])

    def test_cancelled_worker_cannot_show_the_frame(self):
        abort = threading.Event()
        runner = EngineRunner(should_abort=abort.is_set)
        changes = []
        runner.computer_use.connect(changes.append)
        abort.set()
        runner._on_event("started", {"mode": "active"})
        runner._on_event("action_start", {"surface": "desktop"})
        self.assertEqual(changes, [])

    def test_frame_is_passive_excluded_from_capture_and_tracks_screen_geometry(self):
        screen = Screen(QRect(-1280, 0, 1280, 800))
        with patch("ui_computer_use.exclude_from_capture") as exclude:
            window = ComputerUseWindow(screen)
        try:
            exclude.assert_called_once_with(window)
            self.assertEqual(window.geometry(), screen.geometry())
            self.assertTrue(window.windowFlags() & Qt.WindowType.WindowTransparentForInput)
            self.assertTrue(window.windowFlags() & Qt.WindowType.WindowDoesNotAcceptFocus)
            self.assertTrue(window.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating))
            self.assertEqual(window.focusPolicy(), Qt.FocusPolicy.NoFocus)
            screen.rect = QRect(-1920, -200, 1920, 1080)
            screen.geometryChanged.emit(screen.rect)
            self.assertEqual(window.geometry(), screen.rect)
        finally:
            window.close()
            window.deleteLater()

    def test_manager_hides_all_monitors_and_preserves_active_state_on_hotplug(self):
        app = FakeApp([Screen(QRect(0, 0, 800, 600))])
        manager = ComputerUseManager(app)
        try:
            self.assertFalse(manager.active)
            self.assertTrue(all(w.isHidden() for w in manager._windows))
            manager.set_active(True)
            app.monitors.append(Screen(QRect(-1000, 0, 1000, 700)))
            app.screenAdded.emit(app.monitors[-1])
            self.assertEqual(len(manager._windows), 2)
            self.assertTrue(all(w.isVisible() for w in manager._windows))
            manager.set_active(False)
            self.assertTrue(all(w.isHidden() for w in manager._windows))
        finally:
            manager.close()
        self.assertEqual(manager._windows, [])

    def test_screen_center_remains_fully_transparent_with_soft_lavender_edges(self):
        image = QImage(1280, 800, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_computer_use_frame(painter, QRectF(image.rect()))
        painter.end()
        self.assertEqual(image.pixelColor(640, 400).alpha(), 0)
        self.assertEqual(image.pixelColor(400, 400).alpha(), 0)
        edge = image.pixelColor(1, 400)
        self.assertGreater(edge.alpha(), 0)
        self.assertLess(edge.alpha(), 100)
        self.assertGreater(edge.blue(), edge.green() + 20)
        self.assertGreater(edge.red(), edge.green() + 20)
        self.assertNotEqual(image.pixelColor(640, 20), QColor(Qt.GlobalColor.transparent))

    def test_aura_fades_inward_without_a_hard_perimeter(self):
        image = QImage(1280, 800, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_computer_use_frame(painter, QRectF(image.rect()))
        painter.end()
        alphas = [image.pixelColor(x, 400).alpha() for x in (0, 12, 30, 52, 80)]
        self.assertEqual(alphas, sorted(alphas, reverse=True))
        self.assertEqual(alphas[-1], 0)
        self.assertLess(max(alphas), 100)
        self.assertLess(image.pixelColor(0, 0).alpha(), 140)
        self.assertEqual(BANNER_TEXT, "Helper is using your computer  ·  Esc to cancel")
        banner = image.pixelColor(640, int(BANNER_TOP + 2))
        self.assertEqual(banner.alpha(), 255)
        self.assertGreater(banner.blue(), banner.green() + 30)
        self.assertGreater(banner.red(), banner.green() + 30)

    def test_small_pointer_has_a_fading_halo_and_dark_fill(self):
        image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_control_pointer(painter, 40, 40)
        painter.end()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        halo = image.pixelColor(66, 46)
        self.assertGreater(halo.alpha(), 0)
        self.assertLess(halo.alpha(), 100)
        self.assertGreater(halo.blue(), halo.green() + 20)
        self.assertGreater(halo.red(), halo.green() + 20)
        body = image.pixelColor(45, 45)
        self.assertEqual(body.alpha(), 255)
        self.assertLess(body.lightness(), 80)

    def test_active_control_uses_only_the_glowing_pointer(self):
        with self.desktop() as app:
            with patch.object(app._ghost_cursor, "animate_to") as pointer:
                app._on_engine_highlight(100, 200, 80, 24, "Click the browser search box")
            pointer.assert_called_once_with(140, 212)


if __name__ == "__main__":
    unittest.main()
