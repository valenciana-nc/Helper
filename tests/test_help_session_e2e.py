from __future__ import annotations

import io
import os
import threading
import time
import unittest
from typing import Any

from PIL import Image, ImageDraw
from PyQt6.QtWidgets import QApplication

from agent import LiveHelpDecision
from control_inventory import ControlCandidate, TargetResolution
from help_session import HelpSession
from ocr_text import OcrTextResult
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


def _checkbox_capture(
    checkbox_rect: tuple[int, int, int, int] = (40, 50, 24, 24),
    label: str = "Terms",
) -> Capture:
    image = Image.new("RGB", (240, 160), color=(244, 246, 249))
    draw = ImageDraw.Draw(image)
    x, y, width, height = checkbox_rect
    box_size = min(20, height)
    box_y = y + (height - box_size) // 2
    draw.rectangle((x, box_y, x + box_size, box_y + box_size), fill=(255, 255, 255), outline=(18, 48, 130), width=2)
    draw.line((x + 4, box_y + 10, x + 8, box_y + 15), fill=(45, 100, 190), width=2)
    draw.line((x + 8, box_y + 15, x + 16, box_y + 5), fill=(45, 100, 190), width=2)
    draw.text((x + width + 4, y + 4), label, fill=(18, 24, 38))
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


def _dialog_ok_capture(
    title: str,
    body: str,
    button_rect: tuple[int, int, int, int] = (120, 110, 60, 28),
) -> Capture:
    image = Image.new("RGB", (240, 160), color=(238, 240, 244))
    draw = ImageDraw.Draw(image)
    draw.rectangle((16, 18, 224, 148), fill=(255, 255, 255), outline=(130, 136, 148), width=2)
    draw.text((28, 34), title, fill=(18, 24, 38))
    draw.text((28, 58), body, fill=(56, 63, 77))
    x, y, width, height = button_rect
    draw.rectangle((x, y, x + width, y + height), fill=(52, 103, 190), outline=(26, 60, 130), width=2)
    draw.text((x + 21, y + 8), "OK", fill=(255, 255, 255))
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


class _ModelRectAgent:
    def __init__(self) -> None:
        self.calls = 0

    def plan_next_step(
        self,
        history: Any,
        *,
        control_candidates: list[ControlCandidate] | None = None,
        capture: Capture | None = None,
    ) -> LiveHelpDecision:
        self.calls += 1
        if self.calls == 1:
            return LiveHelpDecision(
                kind="step",
                instruction="Click this button.",
                expected_change="A saved confirmation appears.",
                target_norm_x=167,
                target_norm_y=313,
                target_norm_width=333,
                target_norm_height=200,
            )
        return LiveHelpDecision(kind="done", message="Done.")


class _OkAgent:
    def __init__(self) -> None:
        self.calls = 0

    def plan_next_step(
        self,
        history: Any,
        *,
        control_candidates: list[ControlCandidate] | None = None,
        capture: Capture | None = None,
    ) -> LiveHelpDecision:
        self.calls += 1
        if self.calls == 1:
            return LiveHelpDecision(
                kind="step",
                instruction="Click OK.",
                expected_change="The dialog closes.",
                target_id="ok",
            )
        return LiveHelpDecision(kind="done", message="Done.")


class _FakeOcrProvider:
    def __init__(self, result: OcrTextResult) -> None:
        self.result = result
        self.calls: list[tuple[Capture, tuple[int, int, int, int]]] = []

    def recognize_text(
        self,
        capture: Capture,
        rect: tuple[int, int, int, int],
    ) -> OcrTextResult:
        self.calls.append((capture, rect))
        return self.result


class _MutatingOcrProvider(_FakeOcrProvider):
    def __init__(self, result: OcrTextResult, on_call: Any) -> None:
        super().__init__(result)
        self._on_call = on_call

    def recognize_text(
        self,
        capture: Capture,
        rect: tuple[int, int, int, int],
    ) -> OcrTextResult:
        result = super().recognize_text(capture, rect)
        self._on_call()
        return result


class HelpSessionEndToEndTests(unittest.TestCase):
    def setUp(self) -> None:
        self._previous_ocr_env = os.environ.get("HELP_OCR_TEXT_VERIFY")
        os.environ["HELP_OCR_TEXT_VERIFY"] = "0"

    def tearDown(self) -> None:
        if self._previous_ocr_env is None:
            os.environ.pop("HELP_OCR_TEXT_VERIFY", None)
        else:
            os.environ["HELP_OCR_TEXT_VERIFY"] = self._previous_ocr_env

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

    def test_help_session_ocr_unavailable_still_emits_candidate_highlight(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        candidate = ControlCandidate(
            id="c001",
            text="Save changes",
            control_type="button",
            rect=(40, 50, 120, 32),
            automation_id="saveButton",
        )
        ocr_provider = _FakeOcrProvider(OcrTextResult(available=False, error="OCR unavailable"))
        agent = _ScriptedAgent()
        session = HelpSession(
            agent=agent,  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [candidate],
            ocr_text_provider=ocr_provider,
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
        self.assertEqual(highlights, [(40, 50, 120, 32, "Click Save changes.")])
        self.assertEqual(len(ocr_provider.calls), 1)
        self.assertEqual(ocr_provider.calls[0][1], (40, 50, 120, 32))
        self.assertFalse(diagnostics[0]["ocr"]["available"])
        self.assertTrue(diagnostics[0]["overlay"]["emitted"])

    def test_help_session_ocr_disagreement_downgrades_before_highlight(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        candidate = ControlCandidate(
            id="c001",
            text="Save changes",
            control_type="button",
            rect=(40, 50, 120, 32),
            automation_id="saveButton",
        )
        ocr_provider = _FakeOcrProvider(OcrTextResult(text="Cancel", available=True))
        agent = _ScriptedAgent()
        session = HelpSession(
            agent=agent,  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [candidate],
            ocr_text_provider=ocr_provider,
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

        advanced = False
        try:
            session.start("Help me save this.")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and not finished and not failed:
                app.processEvents()
                if diagnostics and not advanced:
                    session.notify_user_click(5, 5)
                    advanced = True
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
        self.assertEqual(highlights, [])
        self.assertEqual(len(ocr_provider.calls), 1)
        self.assertFalse(diagnostics[0]["overlay"]["emitted"])
        self.assertEqual(diagnostics[0]["overlay"]["rejected_reason"], "ocr text mismatch")
        self.assertEqual(diagnostics[0]["ocr"]["expected_text"], "Save changes")
        self.assertEqual(diagnostics[0]["ocr"]["recognized_text"], "Cancel")

    def test_help_session_ocr_uses_state_label_evidence_rect_without_moving_overlay(self) -> None:
        app = _qt_app()
        capture = _checkbox_capture()
        checkbox = ControlCandidate(
            id="terms",
            text="Checked",
            control_type="checkbox",
            rect=(40, 52, 21, 21),
        )
        label = ControlCandidate(
            id="terms_label",
            text="Terms",
            control_type="text",
            rect=(64, 52, 80, 21),
        )
        ocr_provider = _FakeOcrProvider(OcrTextResult(text="Terms", available=True))

        class _TermsAgent(_ScriptedAgent):
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
                        instruction="Click the Terms checkbox.",
                        expected_change="Terms is toggled.",
                        target_id="terms",
                        target_norm_x=167,
                        target_norm_y=313,
                        target_norm_width=200,
                        target_norm_height=150,
                    )
                return LiveHelpDecision(kind="done", message="Done.")

        session = HelpSession(
            agent=_TermsAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [checkbox, label],
            ocr_text_provider=ocr_provider,
        )
        highlights: list[tuple[int, int, int, int, str]] = []
        diagnostics: list[dict[str, Any]] = []
        finished: list[str] = []
        failed: list[str] = []

        session.highlight_show.connect(
            lambda x, y, w, h, label_text: highlights.append((x, y, w, h, label_text))
        )
        session.target_diagnostic.connect(lambda payload: diagnostics.append(payload))
        session.finished.connect(lambda message: finished.append(message))
        session.failed.connect(lambda message: failed.append(message))

        click_sent = False
        try:
            session.start("Help me accept the terms.")
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
        self.assertEqual(finished, ["Done."])
        self.assertEqual(highlights, [(40, 52, 21, 21, "Click the Terms checkbox.")])
        self.assertEqual(len(ocr_provider.calls), 1)
        self.assertEqual(ocr_provider.calls[0][1], (40, 52, 104, 21))
        self.assertTrue(diagnostics[0]["overlay"]["emitted"])
        self.assertEqual(diagnostics[0]["overlay"]["rect"], (40, 52, 21, 21))
        self.assertEqual(diagnostics[0]["ocr"]["expected_text"], "Terms")

    def test_final_pre_overlay_recheck_rejects_target_covered_after_ocr(self) -> None:
        app = _qt_app()
        clean_capture = _button_capture()
        covered_capture = _dialog_ok_capture(
            "Confirm changes",
            "This dialog covers the stale page button.",
            button_rect=(120, 110, 60, 28),
        )
        covered = False

        def mark_covered() -> None:
            nonlocal covered
            covered = True

        def capture_provider() -> Capture:
            return covered_capture if covered else clean_capture

        def candidate_provider(_capture: Capture) -> list[ControlCandidate]:
            save = ControlCandidate(
                id="c001",
                text="Save changes",
                control_type="button",
                rect=(40, 50, 120, 32),
                automation_id="saveButton",
                window_rank=1,
            )
            if not covered:
                return [save]
            return [
                ControlCandidate("dialog", "Confirm changes dialog", "window", (16, 18, 208, 130), window_rank=0),
                ControlCandidate("ok", "OK", "button", (120, 110, 60, 28), window_rank=0),
                save,
            ]

        ocr_provider = _MutatingOcrProvider(
            OcrTextResult(text="Save changes", available=True),
            mark_covered,
        )
        session = HelpSession(
            agent=_ScriptedAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=capture_provider,
            candidate_provider=candidate_provider,
            ocr_text_provider=ocr_provider,
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

        advanced = False
        try:
            session.start("Help me save this.")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and not finished and not failed:
                app.processEvents()
                if diagnostics and not advanced:
                    session.notify_user_click(5, 5)
                    advanced = True
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
        self.assertEqual(highlights, [])
        self.assertEqual(len(ocr_provider.calls), 1)
        self.assertFalse(diagnostics[0]["overlay"]["emitted"])
        self.assertEqual(diagnostics[0]["overlay"]["rejected_reason"], "target covered before overlay")
        self.assertEqual(diagnostics[0]["ocr"]["recognized_text"], "Save changes")

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

    def test_current_screen_recheck_rejects_reused_row_action_context_change(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        current_candidates = [
            ControlCandidate("row_0", "Globex account", "dataitem", (20, 100, 700, 42)),
            ControlCandidate("approve", "Approve", "button", (600, 106, 80, 30)),
        ]
        previous_candidates = [
            ControlCandidate("row_0", "Acme account", "dataitem", (20, 100, 700, 42)),
            ControlCandidate("approve", "Approve", "button", (600, 106, 80, 30)),
        ]
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: current_candidates,
        )
        previous_target = TargetResolution(
            rect=(600, 106, 80, 30),
            confidence=0.9,
            source="target_id",
            matched_text="Approve",
            target_id="approve",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Approve.",
            target_id="approve",
            target_norm_x=600,
            target_norm_y=106,
            target_norm_width=80,
            target_norm_height=30,
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
                previous_candidates=previous_candidates,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "current screen recheck target changed")

    def test_current_screen_recheck_allows_reused_row_action_same_context(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        candidates = [
            ControlCandidate("row_0", "Acme account", "dataitem", (20, 100, 700, 42)),
            ControlCandidate("approve", "Approve", "button", (600, 106, 80, 30)),
        ]
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: candidates,
        )
        previous_target = TargetResolution(
            rect=(600, 106, 80, 30),
            confidence=0.9,
            source="target_id",
            matched_text="Approve",
            target_id="approve",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Approve.",
            target_id="approve",
            target_norm_x=600,
            target_norm_y=106,
            target_norm_width=80,
            target_norm_height=30,
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
                previous_candidates=candidates,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "target_id")
        self.assertFalse(target.rejected_reason)

    def test_current_screen_recheck_rejects_rebound_row_action_id_context_change(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        current_candidates = [
            ControlCandidate("row_1", "Globex account", "dataitem", (20, 100, 700, 42)),
            ControlCandidate("approve_new", "Approve", "button", (600, 106, 80, 30)),
        ]
        previous_candidates = [
            ControlCandidate("row_0", "Acme account", "dataitem", (20, 100, 700, 42)),
            ControlCandidate("approve_old", "Approve", "button", (600, 106, 80, 30)),
        ]
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: current_candidates,
        )
        previous_target = TargetResolution(
            rect=(600, 106, 80, 30),
            confidence=0.9,
            source="target_id",
            matched_text="Approve",
            target_id="approve_old",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Approve.",
            target_id="approve_old",
            target_norm_x=600,
            target_norm_y=106,
            target_norm_width=80,
            target_norm_height=30,
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
                previous_candidates=previous_candidates,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "approve_new")
        self.assertEqual(target.rejected_reason, "current screen recheck target changed")

    def test_current_screen_recheck_rejects_reused_action_identity_disappears(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        rect = (40, 50, 120, 32)
        current_candidates = [
            ControlCandidate("c001", "", "button", rect),
        ]
        previous_candidates = [
            ControlCandidate("c001", "Save changes", "button", rect),
        ]
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: current_candidates,
        )
        previous_target = TargetResolution(
            rect=rect,
            confidence=0.9,
            source="target_id",
            matched_text="Save changes",
            target_id="c001",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Save changes.",
            target_id="c001",
            target_norm_x=167,
            target_norm_y=313,
            target_norm_width=500,
            target_norm_height=200,
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
                previous_candidates=previous_candidates,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "current screen recheck target changed")

    def test_current_screen_recheck_allows_reused_action_same_identity(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        rect = (40, 50, 120, 32)
        current_candidates = [
            ControlCandidate("c001", "Save", "button", rect),
        ]
        previous_candidates = [
            ControlCandidate("c001", "Save changes", "button", rect),
        ]
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: current_candidates,
        )
        previous_target = TargetResolution(
            rect=rect,
            confidence=0.9,
            source="target_id",
            matched_text="Save changes",
            target_id="c001",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Save changes.",
            target_id="c001",
            target_norm_x=167,
            target_norm_y=313,
            target_norm_width=500,
            target_norm_height=200,
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
                previous_candidates=previous_candidates,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "target_id")
        self.assertFalse(target.rejected_reason)

    def test_current_screen_recheck_allows_generic_action_when_visual_context_stable(self) -> None:
        app = _qt_app()
        rect = (120, 110, 60, 28)
        capture = _dialog_ok_capture(
            "Delete customer?",
            "This will remove Acme from the workspace.",
            button_rect=rect,
        )
        candidates = [
            ControlCandidate("ok", "OK", "button", rect),
        ]
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: candidates,
        )
        previous_target = TargetResolution(
            rect=rect,
            confidence=0.9,
            source="target_id",
            matched_text="OK",
            target_id="ok",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click OK.",
            target_id="ok",
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
                previous_capture=capture,
                previous_candidates=candidates,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "target_id")
        self.assertFalse(target.rejected_reason)

    def test_help_session_downgrades_generic_action_when_visual_context_changes(self) -> None:
        app = _qt_app()
        rect = (120, 110, 60, 28)
        first_capture = _dialog_ok_capture(
            "Delete customer?",
            "This will remove Acme from the workspace.",
            button_rect=rect,
        )
        current_capture = _dialog_ok_capture(
            "Archive project?",
            "This will hide Project Orion from active lists.",
            button_rect=rect,
        )
        capture_calls: list[Capture] = []

        def capture_provider() -> Capture:
            capture = first_capture if not capture_calls else current_capture
            capture_calls.append(capture)
            return capture

        candidates = [
            ControlCandidate("ok", "OK", "button", rect),
        ]
        session = HelpSession(
            agent=_OkAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=capture_provider,
            candidate_provider=lambda _capture: candidates,
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

        advanced = False
        try:
            session.start("Help me confirm this.")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and not finished and not failed:
                app.processEvents()
                if diagnostics and not advanced:
                    session.notify_user_click(5, 5)
                    advanced = True
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
        self.assertGreaterEqual(len(capture_calls), 2)
        self.assertEqual(highlights, [])
        self.assertGreaterEqual(len(diagnostics), 1)
        self.assertFalse(diagnostics[0]["overlay"]["emitted"])
        self.assertEqual(diagnostics[0]["resolution"]["source"], "target_id")
        self.assertEqual(
            diagnostics[0]["resolution"]["rejected_reason"],
            "current screen recheck target changed",
        )

    def test_current_screen_recheck_rejects_reused_control_identity_change(self) -> None:
        app = _qt_app()
        capture = _button_capture(button_rect=(40, 50, 80, 32))
        rect = (40, 50, 80, 32)
        cases = [
            ("Click this checkbox.", "checkbox", "Notifications", "Dark mode"),
            ("Click this combobox.", "combobox", "Country", "Language"),
            ("Click this edit control.", "edit", "Email", "Phone"),
            ("Select this radio option.", "radiobutton", "Weekly", "Daily"),
            ("Adjust this slider.", "slider", "Volume", "Brightness"),
            ("Adjust this spinner.", "spinner", "Retries", "Timeout"),
        ]

        for instruction, control_type, previous_text, current_text in cases:
            with self.subTest(control_type=control_type):
                current_candidates = [
                    ControlCandidate("setting", current_text, control_type, rect),
                ]
                previous_candidates = [
                    ControlCandidate("setting", previous_text, control_type, rect),
                ]
                session = HelpSession(
                    agent=_DoneAgent(),  # type: ignore[arg-type]
                    controller=_Controller(),  # type: ignore[arg-type]
                    capture_provider=lambda: capture,
                    candidate_provider=lambda _capture, items=current_candidates: items,
                )
                previous_target = TargetResolution(
                    rect=rect,
                    confidence=0.9,
                    source="target_id",
                    matched_text=previous_text,
                    target_id="setting",
                )
                decision = LiveHelpDecision(
                    kind="step",
                    instruction=instruction,
                    target_id="setting",
                    target_norm_x=167,
                    target_norm_y=313,
                    target_norm_width=333,
                    target_norm_height=200,
                )

                try:
                    _capture, _candidates, target = session._revalidate_target_on_current_screen(
                        decision,
                        previous_target=previous_target,
                        previous_candidates=previous_candidates,
                    )
                finally:
                    session.deleteLater()
                    app.processEvents()

                self.assertEqual(target.source, "target_id")
                self.assertEqual(target.rejected_reason, "current screen recheck target changed")

    def test_current_screen_recheck_allows_reused_control_same_identity(self) -> None:
        app = _qt_app()
        capture = _button_capture(button_rect=(40, 50, 80, 32))
        rect = (40, 50, 80, 32)
        current_candidates = [
            ControlCandidate("setting", "Notifications", "checkbox", rect),
        ]
        previous_candidates = [
            ControlCandidate("setting", "Notifications", "checkbox", rect),
        ]
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: current_candidates,
        )
        previous_target = TargetResolution(
            rect=rect,
            confidence=0.9,
            source="target_id",
            matched_text="Notifications",
            target_id="setting",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click this checkbox.",
            target_id="setting",
            target_norm_x=167,
            target_norm_y=313,
            target_norm_width=333,
            target_norm_height=200,
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
                previous_candidates=previous_candidates,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "target_id")
        self.assertFalse(target.rejected_reason)

    def test_current_screen_recheck_rejects_tiny_target_id_zero_overlap_drift(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [
                ControlCandidate("c001", "Info", "button", (48, 50, 8, 8)),
            ],
        )
        previous_target = TargetResolution(
            rect=(40, 50, 8, 8),
            confidence=0.9,
            source="target_id",
            matched_text="Info",
            target_id="c001",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Info.",
            target_id="c001",
        )

        try:
            _capture, _candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
                previous_candidates=[
                    ControlCandidate("c001", "Info", "button", (40, 50, 8, 8)),
                ],
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "current screen recheck target changed")

    def test_current_screen_recheck_rejects_nearby_nonoverlapping_target_id_rebind(self) -> None:
        app = _qt_app()
        capture = _button_capture()
        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [
                ControlCandidate("c001", "Save changes", "button", (40, 95, 120, 32)),
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

    def test_current_screen_recheck_rejects_empty_candidates_for_candidate_backed_target(self) -> None:
        app = _qt_app()
        capture = _button_capture()

        def snapper(_rect: tuple[int, int, int, int], _instruction: str):
            raise AssertionError("model fallback should not run when recheck candidates vanish")

        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [],
            snapper=snapper,
        )
        previous_target = TargetResolution(
            rect=(40, 50, 120, 32),
            confidence=0.9,
            source="candidate_snap",
            matched_text="Save changes",
            target_id="c001",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click this button.",
            target_norm_x=166,
            target_norm_y=312,
            target_norm_width=500,
            target_norm_height=200,
        )

        try:
            _capture, candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(candidates, [])
        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(
            target.rejected_reason,
            "current screen recheck candidates unavailable",
        )

    def test_current_screen_recheck_rejects_model_only_target_without_fresh_evidence(self) -> None:
        from rect_snap import SnapResult

        app = _qt_app()
        rect = (40, 50, 80, 32)
        capture = _button_capture(button_rect=rect)

        def snapper(_rect: tuple[int, int, int, int], _instruction: str):
            return SnapResult(rect=rect, confidence=0.0, source="model")

        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [],
            snapper=snapper,
        )
        previous_target = TargetResolution(
            rect=rect,
            confidence=0.0,
            source="model",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click this button.",
            target_norm_x=167,
            target_norm_y=313,
            target_norm_width=333,
            target_norm_height=200,
        )

        try:
            _capture, candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(candidates, [])
        self.assertEqual(target.source, "model")
        self.assertEqual(target.rejected_reason, "current screen recheck target changed")

    def test_help_session_downgrades_model_only_recheck_without_fresh_evidence(self) -> None:
        from rect_snap import SnapResult

        app = _qt_app()
        rect = (40, 50, 80, 32)
        capture = _button_capture(button_rect=rect)
        agent = _ModelRectAgent()

        def snapper(_rect: tuple[int, int, int, int], _instruction: str):
            return SnapResult(rect=rect, confidence=0.0, source="model")

        session = HelpSession(
            agent=agent,  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [],
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

        advanced = False
        try:
            session.start("Help me save this.")
            deadline = time.monotonic() + 4.0
            while time.monotonic() < deadline and not finished and not failed:
                app.processEvents()
                if diagnostics and not advanced:
                    session.notify_user_click(5, 5)
                    advanced = True
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
        self.assertEqual(highlights, [])
        self.assertGreaterEqual(len(diagnostics), 1)
        self.assertFalse(diagnostics[0]["overlay"]["emitted"])
        self.assertEqual(diagnostics[0]["resolution"]["source"], "model")
        self.assertEqual(
            diagnostics[0]["resolution"]["rejected_reason"],
            "current screen recheck target changed",
        )

    def test_current_screen_recheck_allows_model_origin_when_fresh_uia_snap_confirms(self) -> None:
        from rect_snap import SnapResult

        app = _qt_app()
        rect = (40, 50, 80, 32)
        capture = _button_capture(button_rect=rect)

        def snapper(_rect: tuple[int, int, int, int], _instruction: str):
            return SnapResult(
                rect=rect,
                confidence=0.9,
                source="uia",
                matched_text="Save changes",
            )

        session = HelpSession(
            agent=_DoneAgent(),  # type: ignore[arg-type]
            controller=_Controller(),  # type: ignore[arg-type]
            capture_provider=lambda: capture,
            candidate_provider=lambda _capture: [],
            snapper=snapper,
        )
        previous_target = TargetResolution(
            rect=rect,
            confidence=0.0,
            source="model",
        )
        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Save changes.",
            target_norm_x=167,
            target_norm_y=313,
            target_norm_width=333,
            target_norm_height=200,
        )

        try:
            _capture, candidates, target = session._revalidate_target_on_current_screen(
                decision,
                previous_target=previous_target,
            )
        finally:
            session.deleteLater()
            app.processEvents()

        self.assertEqual(candidates, [])
        self.assertEqual(target.source, "snap")
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
