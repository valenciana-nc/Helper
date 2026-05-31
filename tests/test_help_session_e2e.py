from __future__ import annotations

import io
import time
import unittest
from typing import Any

from PIL import Image, ImageDraw
from PyQt6.QtWidgets import QApplication

from agent import LiveHelpDecision
from control_inventory import ControlCandidate
from help_session import HelpSession
from screen import Capture


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _button_capture() -> Capture:
    image = Image.new("RGB", (240, 160), color=(244, 246, 249))
    draw = ImageDraw.Draw(image)
    draw.rectangle((40, 50, 160, 82), fill=(45, 100, 190), outline=(18, 48, 130), width=2)
    draw.rectangle((48, 58, 72, 74), fill=(245, 248, 255))
    draw.line((82, 60, 150, 60), fill=(245, 248, 255), width=3)
    draw.line((82, 70, 132, 70), fill=(245, 248, 255), width=3)
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


class HelpSessionEndToEndTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
