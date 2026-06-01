from __future__ import annotations

import io
import threading
import time
import unittest
from typing import Any

from PIL import Image, ImageDraw
from PyQt6.QtWidgets import QApplication

from agent import LiveHelpDecision
from control_inventory import ControlCandidate, TargetResolution
from help_session import HelpSession
from screen import Capture


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _button_capture(button_rect: tuple[int, int, int, int] = (40, 50, 120, 32)) -> Capture:
    image = Image.new("RGB", (240, 160), color=(244, 246, 249))
    draw = ImageDraw.Draw(image)
    x, y, width, height = button_rect
    draw.rectangle((x, y, x + width, y + height), fill=(45, 100, 190), outline=(18, 48, 130), width=2)
    draw.rectangle((x + 8, y + 8, x + 32, y + height - 8), fill=(245, 248, 255))
    draw.line((x + 42, y + 10, x + width - 10, y + 10), fill=(245, 248, 255), width=3)
    draw.line((x + 42, y + 20, x + width - 28, y + 20), fill=(245, 248, 255), width=3)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Capture(
        png_bytes=buffer.getvalue(),
        width=image.width,
        height=image.height,
        monitor_left=0,
        monitor_top=0,
        scale=1.0,
    )


class _AbortController:
    def is_aborted(self) -> bool:
        return False


class _Controller:
    abort_controller = _AbortController()


class _ScriptedAgent:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def plan_next_step(
        self,
        history: Any,
        *,
        control_candidates: list[ControlCandidate] | None = None,
        capture: Capture | None = None,
    ) -> LiveHelpDecision:
        self.calls.append(
            {
                "messages": [(msg.role, msg.text) for msg in history.conversation_messages()],
                "control_candidates": list(control_candidates or []),
                "capture": capture,
            }
        )
        if len(self.calls) == 1:
            return LiveHelpDecision(
                kind="step",
                instruction="Click Save changes.",
                expected_change="A saved confirmation appears.",
                target_id="c001",
                target_norm_x=780,
                target_norm_y=760,
                target_norm_width=80,
                target_norm_height=80,
            )
        return LiveHelpDecision(kind="done", message="Saved.")


class _WrongTargetIdAgent(_ScriptedAgent):
    def plan_next_step(
        self,
        history: Any,
        *,
        control_candidates: list[ControlCandidate] | None = None,
        capture: Capture | None = None,
    ) -> LiveHelpDecision:
        self.calls.append(
            {
                "messages": [(msg.role, msg.text) for msg in history.conversation_messages()],
                "control_candidates": list(control_candidates or []),
                "capture": capture,
            }
        )
        if len(self.calls) == 1:
            return LiveHelpDecision(
                kind="step",
                instruction="Click Save changes.",
                expected_change="A saved confirmation appears.",
                target_id="c002",
            )
        return LiveHelpDecision(kind="done", message="Saved.")


class _DoneAgent:
    def plan_next_step(
        self,
        history: Any,
        *,
        control_candidates: list[ControlCandidate] | None = None,
        capture: Capture | None = None,
    ) -> LiveHelpDecision:
        return LiveHelpDecision(kind="done", message="Done.")


class HelpSessionEndToEndTests(unittest.TestCase):
    def test_click_hit_margin_scales_with_target_size(self) -> None:
        from help_session import click_hit_margin

        self.assertEqual(click_hit_margin((100, 100, 32, 32)), 10)
        self.assertEqual(click_hit_margin((100, 100, 120, 32)), 10)
        self.assertEqual(click_hit_margin((100, 100, 300, 300)), 24)

    def test_small_target_click_just_outside_adaptive_margin_counts_elsewhere(self) -> None:
        _qt_app()
        session = HelpSession(
            _ScriptedAgent(),
            _Controller(),
            capture_provider=lambda: _button_capture(),
            candidate_provider=lambda _capture: [],
        )

        session._set_active_rect((100, 100, 32, 32))
        session.notify_user_click(143, 116)

        self.assertFalse(session._click_inside_event.is_set())
        self.assertTrue(session._check_now_event.is_set())

    def test_small_target_click_inside_adaptive_margin_counts_inside(self) -> None:
        _qt_app()
        session = HelpSession(
            _ScriptedAgent(),
            _Controller(),
            capture_provider=lambda: _button_capture(),
            candidate_provider=lambda _capture: [],
        )

        session._set_active_rect((100, 100, 32, 32))
        session.notify_user_click(141, 116)

        self.assertTrue(session._click_inside_event.is_set())
        self.assertFalse(session._check_now_event.is_set())

    def test_help_session_waits_for_overlay_clear_barrier_before_capture(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        barrier_entered = threading.Event()
        barrier_release = threading.Event()
        capture_called = threading.Event()
        capture_saw_released: list[bool] = []

        def overlay_clear_barrier() -> None:
            barrier_entered.set()
            self.assertTrue(barrier_release.wait(timeout=2.0))

        def capture_provider() -> Capture:
            capture_saw_released.append(barrier_release.is_set())
            capture_called.set()
            return capture

        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=capture_provider,
            candidate_provider=lambda _capture: [],
            overlay_clear_barrier=overlay_clear_barrier,
        )
        finished: list[str] = []
        failed: list[str] = []
        session.finished.connect(lambda message: finished.append(message))
        session.failed.connect(lambda message: failed.append(message))

        try:
            session.start("Help me.")
            self.assertTrue(barrier_entered.wait(timeout=1.0))
            time.sleep(0.08)
            app.processEvents()
            self.assertFalse(capture_called.is_set())
            barrier_release.set()
            deadline = time.monotonic() + 3.0
            while time.monotonic() < deadline and not finished and not failed:
                app.processEvents()
                time.sleep(0.01)
            app.processEvents()
        finally:
            if not finished:
                session.cancel()
            thread = session._thread
            if thread is not None:
                thread.join(timeout=1.0)
            session.deleteLater()
            app.processEvents()

        self.assertFalse(failed)
        self.assertEqual(finished, ["Done."])
        self.assertEqual(capture_saw_released, [True])

    def test_help_session_emits_candidate_highlight_and_click_outcome(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        candidate = ControlCandidate(
            id="c001",
            text="Save changes",
            control_type="button",
            rect=(40, 50, 120, 32),
            automation_id="saveButton",
        )
        agent = _ScriptedAgent()
        candidate_captures: list[Capture] = []

        def candidate_provider(seen_capture: Capture) -> list[ControlCandidate]:
            candidate_captures.append(seen_capture)
            return [candidate]

        def snapper(_rect: tuple[int, int, int, int], _instruction: str):
            raise AssertionError("target_id candidate evidence should resolve before snapper")

        session = HelpSession(
            agent=agent,  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=candidate_provider,
            snapper=snapper,
        )
        highlights: list[tuple[int, int, int, int, str]] = []
        diagnostics: list[dict[str, Any]] = []
        finished: list[str] = []
        failed: list[str] = []
        clears: list[bool] = []

        session.highlight_show.connect(
            lambda x, y, w, h, label: highlights.append((x, y, w, h, label))
        )
        session.target_diagnostic.connect(lambda payload: diagnostics.append(payload))
        session.finished.connect(lambda message: finished.append(message))
        session.failed.connect(lambda message: failed.append(message))
        session.highlight_clear.connect(lambda: clears.append(True))

        click_sent = False
        try:
            session.start("Help me save this.")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and not finished and not failed:
                app.processEvents()
                if highlights and not click_sent:
                    x, y, width, height, _label = highlights[0]
                    time.sleep(0.05)
                    session.notify_user_click(x + width // 2, y + height // 2)
                    click_sent = True
                time.sleep(0.01)
            app.processEvents()
        finally:
            if not finished:
                session.cancel()
            thread = session._thread
            if thread is not None:
                thread.join(timeout=1.0)
            session.deleteLater()
            app.processEvents()

        self.assertFalse(failed)
        self.assertEqual(finished, ["Saved."])
        self.assertEqual(highlights, [(40, 50, 120, 32, "Click Save changes.")])
        self.assertGreaterEqual(len(clears), 2)

        self.assertGreaterEqual(len(diagnostics), 1)
        diagnostic = diagnostics[0]
        self.assertTrue(diagnostic["overlay"]["emitted"])
        self.assertEqual(diagnostic["overlay"]["rect"], (40, 50, 120, 32))
        self.assertEqual(diagnostic["resolution"]["source"], "target_id")
        self.assertEqual(diagnostic["resolution"]["target_id"], "c001")
        self.assertEqual(diagnostic["resolution"]["matched_text"], "Save changes saveButton")

        self.assertGreaterEqual(len(agent.calls), 2)
        self.assertEqual(agent.calls[0]["control_candidates"], [candidate])
        self.assertIs(agent.calls[0]["capture"], capture)
        self.assertTrue(all(seen is capture for seen in candidate_captures))

        followup_text = agent.calls[1]["messages"][-1][1]
        self.assertIn("The user clicked the highlighted target.", followup_text)
        self.assertIn('Expected visible change: "A saved confirmation appears".', followup_text)
        assistant_history = [
            text for role, text in agent.calls[1]["messages"] if role == "assistant"
        ]
        self.assertTrue(assistant_history)
        self.assertFalse(any("target_id=" in text for text in assistant_history))

    def test_help_session_retries_transient_empty_candidate_snapshot(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        candidate = ControlCandidate(
            id="c001",
            text="Save changes",
            control_type="button",
            rect=(40, 50, 120, 32),
            automation_id="saveButton",
        )
        agent = _ScriptedAgent()
        candidate_calls = 0

        def candidate_provider(_capture: Capture) -> list[ControlCandidate]:
            nonlocal candidate_calls
            candidate_calls += 1
            if candidate_calls == 1:
                return []
            return [candidate]

        session = HelpSession(
            agent=agent,  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=candidate_provider,
        )
        highlights: list[tuple[int, int, int, int, str]] = []
        diagnostics: list[dict[str, Any]] = []
        finished: list[str] = []
        failed: list[str] = []
        session.highlight_show.connect(
            lambda x, y, w, h, label: highlights.append((x, y, w, h, label))
        )
        session.target_diagnostic.connect(lambda payload: diagnostics.append(payload))
        session.finished.connect(lambda message: finished.append(message))
        session.failed.connect(lambda message: failed.append(message))

        click_sent = False
        try:
            session.start("Help me save this.")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and not finished and not failed:
                app.processEvents()
                if highlights and not click_sent:
                    x, y, width, height, _label = highlights[0]
                    session.notify_user_click(x + width // 2, y + height // 2)
                    click_sent = True
                time.sleep(0.01)
            app.processEvents()
        finally:
            if not finished:
                session.cancel()
            thread = session._thread
            if thread is not None:
                thread.join(timeout=1.0)
            session.deleteLater()
            app.processEvents()

        self.assertFalse(failed)
        self.assertEqual(finished, ["Saved."])
        self.assertGreaterEqual(candidate_calls, 2)
        self.assertEqual(agent.calls[0]["control_candidates"], [candidate])
        self.assertEqual(highlights, [(40, 50, 120, 32, "Click Save changes.")])
        self.assertTrue(diagnostics[0]["overlay"]["emitted"])

    def test_help_session_revalidates_current_screen_before_emitting_highlight(self) -> None:
        app = _qt_app()
        first_capture = _button_capture((40, 50, 120, 32))
        current_capture = _button_capture((82, 64, 120, 32))
        capture_calls: list[Capture] = []
        agent = _ScriptedAgent()

        def capture_provider() -> Capture:
            capture = first_capture if not capture_calls else current_capture
            capture_calls.append(capture)
            return capture

        def candidate_provider(seen_capture: Capture) -> list[ControlCandidate]:
            rect = (40, 50, 120, 32) if seen_capture is first_capture else (82, 64, 120, 32)
            return [
                ControlCandidate(
                    id="c001",
                    text="Save changes",
                    control_type="button",
                    rect=rect,
                    automation_id="saveButton",
                )
            ]

        session = HelpSession(
            agent=agent,  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=capture_provider,
            candidate_provider=candidate_provider,
        )
        highlights: list[tuple[int, int, int, int, str]] = []
        diagnostics: list[dict[str, Any]] = []
        finished: list[str] = []
        failed: list[str] = []
        session.highlight_show.connect(
            lambda x, y, w, h, label: highlights.append((x, y, w, h, label))
        )
        session.target_diagnostic.connect(lambda payload: diagnostics.append(payload))
        session.finished.connect(lambda message: finished.append(message))
        session.failed.connect(lambda message: failed.append(message))

        click_sent = False
        try:
            session.start("Help me save this.")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and not finished and not failed:
                app.processEvents()
                if highlights and not click_sent:
                    x, y, width, height, _label = highlights[0]
                    time.sleep(0.05)
                    session.notify_user_click(x + width // 2, y + height // 2)
                    click_sent = True
                time.sleep(0.01)
            app.processEvents()
        finally:
            if not finished:
                session.cancel()
            thread = session._thread
            if thread is not None:
                thread.join(timeout=1.0)
            session.deleteLater()
            app.processEvents()

        self.assertFalse(failed)
        self.assertEqual(finished, ["Saved."])
        self.assertGreaterEqual(len(capture_calls), 2)
        self.assertEqual(highlights, [(82, 64, 120, 32, "Click Save changes.")])
        self.assertGreaterEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["resolution"]["rect"], (82, 64, 120, 32))

    def test_current_screen_recheck_rejects_stale_target_id_that_jumps_far(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [
                ControlCandidate("c001", "Save changes", "button", (170, 50, 120, 32)),
            ],
        )
        previous_target = TargetResolution(
            rect=(40, 50, 120, 32),
            confidence=0.9,
            source="target_id",
            matched_text="Save changes",
            target_id="c001",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Save changes.",
            target_id="c001",
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "current screen recheck target changed")

    def test_current_screen_recheck_allows_moderate_target_id_drift_when_text_confirms(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        rect = (82, 64, 120, 32)
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [
                ControlCandidate("c001", "Save changes", "button", rect),
            ],
        )
        previous_target = TargetResolution(
            rect=(40, 50, 120, 32),
            confidence=0.9,
            source="target_id",
            matched_text="Save changes",
            target_id="c001",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Save changes.",
            target_id="c001",
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "target_id")
        self.assertFalse(target.rejected_reason)

    def test_current_screen_recheck_rejects_text_match_that_jumps_far(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [
                ControlCandidate("c002", "Save changes", "button", (220, 50, 120, 32)),
            ],
        )
        previous_target = TargetResolution(
            rect=(40, 50, 120, 32),
            confidence=0.9,
            source="text_match",
            matched_text="Save changes",
            target_id="c001",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Save changes.",
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.rejected_reason, "current screen recheck target changed")

    def test_current_screen_recheck_allows_moderate_text_match_drift(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        rect = (82, 64, 120, 32)
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [
                ControlCandidate("c002", "Save changes", "button", rect),
            ],
        )
        previous_target = TargetResolution(
            rect=(40, 50, 120, 32),
            confidence=0.9,
            source="text_match",
            matched_text="Save changes",
            target_id="c001",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Save changes.",
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "text_match")
        self.assertFalse(target.rejected_reason)

    def test_help_session_recovers_wrong_target_id_before_emitting_highlight(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        save = ControlCandidate(
            id="c001",
            text="Save changes",
            control_type="button",
            rect=(40, 50, 120, 32),
            automation_id="saveButton",
        )
        cancel = ControlCandidate(
            id="c002",
            text="Cancel",
            control_type="button",
            rect=(170, 50, 60, 32),
            automation_id="cancelButton",
        )
        agent = _WrongTargetIdAgent()

        def snapper(_rect: tuple[int, int, int, int], _instruction: str):
            raise AssertionError("wrong target_id should recover from current candidates")

        session = HelpSession(
            agent=agent,  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [save, cancel],
            snapper=snapper,
        )
        highlights: list[tuple[int, int, int, int, str]] = []
        diagnostics: list[dict[str, Any]] = []
        finished: list[str] = []
        failed: list[str] = []

        session.highlight_show.connect(
            lambda x, y, w, h, label: highlights.append((x, y, w, h, label))
        )
        session.target_diagnostic.connect(lambda payload: diagnostics.append(payload))
        session.finished.connect(lambda message: finished.append(message))
        session.failed.connect(lambda message: failed.append(message))

        click_sent = False
        try:
            session.start("Help me save this.")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and not finished and not failed:
                app.processEvents()
                if highlights and not click_sent:
                    x, y, width, height, _label = highlights[0]
                    time.sleep(0.05)
                    session.notify_user_click(x + width // 2, y + height // 2)
                    click_sent = True
                time.sleep(0.01)
            app.processEvents()
        finally:
            if not finished:
                session.cancel()
            thread = session._thread
            if thread is not None:
                thread.join(timeout=1.0)
            session.deleteLater()
            app.processEvents()

        self.assertFalse(failed)
        self.assertEqual(finished, ["Saved."])
        self.assertEqual(highlights, [(40, 50, 120, 32, "Click Save changes.")])
        self.assertGreaterEqual(len(diagnostics), 1)
        self.assertEqual(diagnostics[0]["resolution"]["source"], "text_match")
        self.assertEqual(diagnostics[0]["resolution"]["target_id"], "c001")
        self.assertEqual(diagnostics[0]["resolution"]["rect"], (40, 50, 120, 32))


if __name__ == "__main__":
    unittest.main()
