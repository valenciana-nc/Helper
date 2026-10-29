"""Unified chat controls and passive computer-use frame; no native input or model calls."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from unittest.mock import Mock, patch

from PyQt6.QtCore import QObject, QPoint, QRect, QRectF, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPainter
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QPushButton, QSystemTrayIcon

from engine_bridge import EngineRunner
from helper_engine.types import RunReport, RunStatus
from ui_computer_use import (
    ANIMATION_INTERVAL_MS, BANNER_TEXT, BANNER_TOP, PULSE_SECONDS,
    ComputerUseManager, ComputerUseWindow, aura_pulse, paint_computer_use_frame,
)
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


def render_frame(seconds=0.0):
    image = QImage(1280, 800, QImage.Format.Format_ARGB32_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    paint_computer_use_frame(painter, QRectF(image.rect()), seconds)
    painter.end()
    return image


class ComputerUseUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    @contextmanager
    def desktop(self, startup_surface="background"):
        import main
        from conversation_store import ConversationStore
        with (TemporaryDirectory() as folder,
              patch.object(main, "ensure_windows_shortcuts", return_value=[]),
              patch.object(main.HelplerDesktopApp, "_setup_tray"),
              patch.object(main.HelplerDesktopApp, "_initialize_backend")):
            app = main.HelplerDesktopApp(self.qt, "ctrl+shift+space", startup_surface=startup_surface)
            app._conversation_store = ConversationStore(Path(folder) / "conversations.json")
            try:
                yield app
            finally:
                app.shutdown()
                for widget in (app._widget, app._dashboard, app._status_pill):
                    widget.close()
                    widget.deleteLater()
                self.qt.processEvents()

    def test_foreground_startup_opens_dashboard_without_a_welcome_chat_window(self):
        with self.desktop("dashboard") as app:
            self.qt.processEvents()
            self.assertTrue(app._dashboard.isVisible())
            self.assertFalse(hasattr(app, "_chat_window"))
            self.assertTrue(app._widget.isVisible())

    def test_tray_opens_dashboard_and_restores_it_from_minimized(self):
        with self.desktop() as app:
            app._handle_tray_activated(QSystemTrayIcon.ActivationReason.Trigger)
            self.assertTrue(app._dashboard.isVisible())
            app._dashboard.showMinimized()
            self.qt.processEvents()
            app._toggle_dashboard()
            self.qt.processEvents()
            self.assertTrue(app._dashboard.isVisible())
            self.assertFalse(app._dashboard.isMinimized())

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
                self.assertFalse(any(b.text() in {"Help", "Act"} for b in app._widget.findChildren(QPushButton)))

    def test_shutdown_releases_ghost_cursor_windows(self):
        # GhostCursorManager used to have no teardown: its windows stayed
        # referenced via the QApplication screen-signal connections, so
        # every desktop construction leaked one top-level window per
        # screen. Shutdown must schedule them for deletion like the
        # computer-use manager already does.
        from PyQt6.QtCore import QEvent
        from ui_ghost_cursor import _GhostWindow

        def ghost_ids():
            return {id(w) for w in QApplication.topLevelWidgets() if isinstance(w, _GhostWindow)}

        before = ghost_ids()
        with self.desktop() as app:
            self.assertTrue(ghost_ids() - before)
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        self.assertEqual(ghost_ids(), before)

    def test_escape_and_late_events_cannot_restore_the_frame(self):
        with self.desktop() as app:
            app._on_computer_use_changed(True)
            self.assertTrue(app._computer_use.active)
            self.assertTrue(app._computer_use._timer.isActive())
            app._on_escape_pressed()
            self.assertFalse(app._computer_use.active)
            self.assertFalse(app._computer_use._timer.isActive())
            app._on_computer_use_changed(True)
            self.assertFalse(app._computer_use.active)
            self.assertFalse(app._computer_use._timer.isActive())

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

    def test_screen_center_remains_fully_transparent_with_soft_purple_edges(self):
        image = QImage(1280, 800, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_computer_use_frame(painter, QRectF(image.rect()))
        painter.end()
        self.assertEqual(image.pixelColor(640, 400).alpha(), 0)
        self.assertEqual(image.pixelColor(400, 400).alpha(), 0)
        for x, y in ((1, 400), (1278, 400), (400, 1), (400, 798)):
            with self.subTest(edge=(x, y)):
                edge = image.pixelColor(x, y)
                self.assertGreater(edge.alpha(), 0)
                self.assertLess(edge.alpha(), 100)
                self.assertGreater(edge.red(), edge.green() + 20)
                self.assertGreater(edge.blue(), edge.green() + 20)
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
        self.assertGreater(banner.red(), banner.green() + 30)
        self.assertGreater(banner.blue(), banner.green() + 60)

    def test_aura_breathes_smoothly_from_rest_to_peak_and_back(self):
        self.assertAlmostEqual(aura_pulse(0), 0)
        self.assertAlmostEqual(aura_pulse(PULSE_SECONDS / 4), .5)
        self.assertAlmostEqual(aura_pulse(PULSE_SECONDS / 2), 1)
        self.assertAlmostEqual(aura_pulse(PULSE_SECONDS), 0)
        rising = [aura_pulse(PULSE_SECONDS * step / 20) for step in range(11)]
        falling = [aura_pulse(PULSE_SECONDS * step / 20) for step in range(10, 21)]
        self.assertEqual(rising, sorted(rising))
        self.assertEqual(falling, sorted(falling, reverse=True))
        self.assertAlmostEqual(aura_pulse(PULSE_SECONDS - .001), aura_pulse(PULSE_SECONDS + .001))
        self.assertAlmostEqual(aura_pulse(-1), 0)

    def test_peak_is_wider_brighter_and_more_purple_without_tinting_center(self):
        rest, peak = render_frame(), render_frame(PULSE_SECONDS / 2)
        for x, y in ((1, 400), (1278, 400), (400, 1), (400, 798)):
            with self.subTest(edge=(x, y)):
                low, high = rest.pixelColor(x, y), peak.pixelColor(x, y)
                self.assertGreater(high.alpha(), low.alpha() * 1.6)
                self.assertGreater(high.blue() - high.green(), low.blue() - low.green())
        for x, y in ((96, 400), (1184, 400), (400, 96), (400, 704)):
            with self.subTest(expansion=(x, y)):
                self.assertEqual(rest.pixelColor(x, y).alpha(), 0)
                self.assertGreater(peak.pixelColor(x, y).alpha(), 0)
        self.assertLess(peak.pixelColor(0, 0).alpha(), 180)
        for seconds in (0, PULSE_SECONDS / 4, PULSE_SECONDS / 2, PULSE_SECONDS * .75, PULSE_SECONDS):
            with self.subTest(seconds=seconds):
                image = render_frame(seconds)
                self.assertEqual(image.pixelColor(640, 400).alpha(), 0)
                self.assertEqual(image.pixelColor(400, 400).alpha(), 0)
        self.assertEqual(rest, render_frame(PULSE_SECONDS))

    def test_banner_is_purple_with_white_text_and_a_breathing_outer_halo(self):
        rest, peak = render_frame(), render_frame(PULSE_SECONDS / 2)
        for image in (rest, peak):
            color = image.pixelColor(640, int(BANNER_TOP + 2))
            self.assertEqual(color.alpha(), 255)
            self.assertGreater(color.red(), color.green() + 30)
            self.assertGreater(color.blue(), color.green() + 60)
            white_pixels = sum(image.pixelColor(x, y) == QColor("white")
                               for y in range(45, 65) for x in range(400, 880))
            self.assertGreater(white_pixels, 20)
            self.assertGreater(image.pixelColor(640, int(BANNER_TOP - 4)).alpha(),
                               image.pixelColor(200, int(BANNER_TOP - 4)).alpha())
        # At the peak the banner halo extends past the resting halo's outer limit.
        y = int(BANNER_TOP - 12)
        self.assertEqual(rest.pixelColor(640, y).alpha(), rest.pixelColor(200, y).alpha())
        self.assertGreater(peak.pixelColor(640, y).alpha(), peak.pixelColor(200, y).alpha())

    def test_animation_timer_runs_only_while_active_and_restarts_at_rest(self):
        manager = ComputerUseManager(FakeApp([Screen(QRect(0, 0, 1280, 800))]))
        try:
            self.assertFalse(manager._timer.isActive())
            self.assertEqual(manager._timer.interval(), ANIMATION_INTERVAL_MS)
            manager.set_active(True)
            self.assertTrue(manager._timer.isActive())
            self.assertLess(manager._windows[0]._animation_seconds, .1)
            QTest.qWait(ANIMATION_INTERVAL_MS * 3)
            self.assertGreater(manager._windows[0]._animation_seconds, 0)
            manager._windows[0].set_animation_time(PULSE_SECONDS / 2)
            manager.set_active(False)
            self.assertFalse(manager._timer.isActive())
            self.assertEqual(manager._windows[0]._animation_seconds, 0)
            manager._advance_animation()  # A queued tick after Escape must remain harmless.
            self.assertEqual(manager._windows[0]._animation_seconds, 0)
            manager.set_active(True)
            self.assertLess(manager._windows[0]._animation_seconds, .1)
        finally:
            manager.close()
        self.assertFalse(manager._timer.isActive())

    def test_one_clock_keeps_monitors_in_phase_without_restarting_on_hotplug(self):
        app = FakeApp([Screen(QRect(0, 0, 1280, 800))])
        manager = ComputerUseManager(app)
        try:
            manager.set_active(True)
            clock = Mock()
            clock.elapsed.return_value = round(PULSE_SECONDS * 500)
            manager._clock = clock
            manager._advance_animation()
            app.monitors.append(Screen(QRect(-1920, -200, 1920, 1080)))
            app.screenAdded.emit(app.monitors[-1])
            self.assertEqual(len(manager._windows), 2)
            self.assertEqual([window._animation_seconds for window in manager._windows], [PULSE_SECONDS / 2] * 2)
            clock.start.assert_not_called()
            self.assertTrue(manager._timer.isActive())
        finally:
            manager.close()

    def test_animation_repaints_edges_and_banner_but_not_desktop_center(self):
        window = ComputerUseWindow(Screen(QRect(-1280, 0, 1280, 800)))
        try:
            self.assertFalse(window._animated_region.contains(QPoint(640, 400)))
            for point in (QPoint(120, 400), QPoint(1160, 400), QPoint(400, 120), QPoint(640, 38)):
                self.assertTrue(window._animated_region.contains(point))
            with patch.object(window, "update") as update:
                window.set_animation_time(PULSE_SECONDS / 2)
            update.assert_called_once_with(window._animated_region)
        finally:
            window.close()
            window.deleteLater()

    def test_small_pointer_has_a_fading_purple_halo_and_dark_fill(self):
        image = QImage(100, 100, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        painter = QPainter(image)
        paint_control_pointer(painter, 40, 40)
        painter.end()
        self.assertEqual(image.pixelColor(0, 0).alpha(), 0)
        halo = image.pixelColor(66, 46)
        self.assertGreater(halo.alpha(), 0)
        self.assertLess(halo.alpha(), 100)
        self.assertGreater(halo.red(), halo.green() + 20)
        self.assertGreater(halo.blue(), halo.green() + 20)
        alphas = [image.pixelColor(x, 46).alpha() for x in (59, 66, 75, 82)]
        self.assertEqual(alphas, sorted(alphas, reverse=True))
        self.assertEqual(alphas[-1], 0)
        body = image.pixelColor(45, 45)
        self.assertEqual(body.alpha(), 255)
        self.assertLess(body.lightness(), 80)
        self.assertEqual(body.red(), body.green())
        self.assertEqual(body.blue(), body.green())

    def test_active_control_uses_only_the_glowing_pointer(self):
        with self.desktop() as app:
            with patch.object(app._ghost_cursor, "animate_to") as pointer:
                app._on_engine_highlight(100, 200, 80, 24, "Click the browser search box")
            pointer.assert_called_once_with(140, 212)


if __name__ == "__main__":
    unittest.main()
