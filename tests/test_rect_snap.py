"""Tests for rect_snap.snap_to_control and help_session.looks_oversized."""
from __future__ import annotations

import unittest


class _FakeRect:
    __slots__ = ("left", "top", "right", "bottom")

    def __init__(self, left: int, top: int, right: int, bottom: int) -> None:
        self.left = left
        self.top = top
        self.right = right
        self.bottom = bottom


class _FakeElementInfo:
    def __init__(
        self,
        *,
        control_type: str = "",
        name: str = "",
        automation_id: str = "",
        rectangle: _FakeRect | None = None,
        enabled: bool = True,
        visible: bool = True,
    ) -> None:
        self.control_type = control_type
        self.name = name
        self.automation_id = automation_id
        self.rectangle = rectangle
        self.enabled = enabled
        self.visible = visible


class _FakeControl:
    def __init__(
        self,
        *,
        text: str = "",
        control_type: str = "",
        rect: _FakeRect | None = None,
        automation_id: str = "",
        enabled: bool = True,
        visible: bool = True,
        children: list["_FakeControl"] | None = None,
    ) -> None:
        self._text = text
        self._children = list(children or [])
        self.element_info = _FakeElementInfo(
            control_type=control_type,
            name=text,
            automation_id=automation_id,
            rectangle=rect,
            enabled=enabled,
            visible=visible,
        )

    def window_text(self) -> str:
        return self._text

    def children(self) -> list["_FakeControl"]:
        return list(self._children)

    def is_enabled(self) -> bool:
        return bool(self.element_info.enabled)

    def is_visible(self) -> bool:
        return bool(self.element_info.visible)


class _FakeDesktop:
    def __init__(self, toplevels: list[_FakeControl]) -> None:
        self._toplevels = list(toplevels)

    def windows(self, **_kwargs: object) -> list[_FakeControl]:
        return list(self._toplevels)


def _make_button(
    name: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    control_type: str = "Button",
    automation_id: str = "",
) -> _FakeControl:
    return _FakeControl(
        text=name,
        control_type=control_type,
        rect=_FakeRect(x, y, x + w, y + h),
        automation_id=automation_id,
    )


def _make_window(
    name: str,
    x: int,
    y: int,
    w: int,
    h: int,
    children: list[_FakeControl],
) -> _FakeControl:
    return _FakeControl(
        text=name,
        control_type="Window",
        rect=_FakeRect(x, y, x + w, y + h),
        children=children,
    )


class SnapToControlTests(unittest.TestCase):
    def test_snaps_to_nearby_button_with_matching_text(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Submit", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (75, 195, 70, 35),
            "Click the Submit button",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 60, 30))
        self.assertIn("Submit", result.matched_text)
        self.assertGreaterEqual(result.confidence, 0.42)

    def test_returns_model_rect_when_no_overlap(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Other", 800, 800, 80, 30)
        window = _make_window("App", 700, 700, 200, 200, [button])
        desktop = _FakeDesktop([window])

        model_rect = (50, 50, 80, 30)
        result = snap_to_control(
            model_rect,
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")
        self.assertEqual(result.rect, model_rect)

    def test_text_match_breaks_ties(self) -> None:
        from rect_snap import snap_to_control

        wrong = _make_button("Cancel", 100, 200, 60, 30)
        right = _make_button("Submit", 110, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [wrong, right])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (105, 195, 60, 35),
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertIn("Submit", result.matched_text)

    def test_skips_non_clickable_control_types(self) -> None:
        from rect_snap import snap_to_control

        text_label = _FakeControl(
            text="Submit",
            control_type="Text",
            rect=_FakeRect(100, 200, 160, 230),
        )
        window = _make_window("App", 0, 0, 800, 600, [text_label])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 60, 30),
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")

    def test_factory_failure_falls_back_cleanly(self) -> None:
        from rect_snap import snap_to_control

        def boom() -> _FakeDesktop:
            raise RuntimeError("UIA unavailable")

        result = snap_to_control(
            (10, 10, 50, 50),
            "Click X",
            desktop_factory=boom,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")
        self.assertEqual(result.rect, (10, 10, 50, 50))

    def test_no_visible_windows_falls_back(self) -> None:
        from rect_snap import snap_to_control

        desktop = _FakeDesktop([])
        result = snap_to_control(
            (10, 10, 50, 50),
            "Click X",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")

    def test_descends_into_window_to_find_button(self) -> None:
        from rect_snap import snap_to_control

        button = _make_button("Save", 410, 510, 50, 24)
        panel = _FakeControl(
            text="Toolbar",
            control_type="Pane",
            rect=_FakeRect(400, 500, 600, 540),
            children=[button],
        )
        window = _make_window("Editor", 0, 0, 1000, 800, [panel])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (405, 505, 60, 30),
            "Save the file",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (410, 510, 50, 24))


class ControlInventoryTests(unittest.TestCase):
    def _capture(self):
        from screen import Capture

        return Capture(
            png_bytes=b"png",
            width=800,
            height=600,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )

    def test_collects_clickable_visible_controls_with_stable_ids(self) -> None:
        from control_inventory import collect_control_candidates

        save = _make_button("Save", 100, 200, 60, 30, automation_id="save-btn")
        label = _FakeControl(
            text="Save",
            control_type="Text",
            rect=_FakeRect(100, 250, 160, 280),
        )
        disabled = _make_button("Disabled", 200, 200, 80, 30)
        disabled.element_info.enabled = False
        offscreen = _make_button("Offscreen", 900, 200, 80, 30)
        window = _make_window("Editor", 0, 0, 800, 600, [save, label, disabled, offscreen])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].id, "c001")
        self.assertEqual(candidates[0].text, "Save")
        self.assertEqual(candidates[0].automation_id, "save-btn")
        self.assertEqual(candidates[0].rect, (100, 200, 60, 30))

    def test_candidate_prompt_includes_target_ids_and_normalized_rects(self) -> None:
        from control_inventory import collect_control_candidates, format_candidates_for_prompt

        button = _make_button("Submit", 80, 120, 40, 30)
        window = _make_window("App", 0, 0, 800, 600, [button])
        desktop = _FakeDesktop([window])
        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        prompt = format_candidates_for_prompt(candidates, self._capture())

        self.assertIn("c001", prompt)
        self.assertIn("Submit", prompt)
        self.assertIn("norm=(100,200,50,50)", prompt)

    def test_resolve_exact_target_id_wins(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [
            ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
            ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
        ]

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Cancel.",
            candidates=candidates,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rect, (100, 10, 60, 30))

    def test_resolve_text_match_beats_nearby_wrong_button(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [
            ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
            ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
        ]

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the Submit button.",
            candidates=candidates,
            model_rect=(10, 10, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")

    def test_resolve_low_confidence_returns_none(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30))]

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Continue.",
            candidates=candidates,
        )

        self.assertIsNone(result)

    def test_resolve_unknown_target_id_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c999",
            instruction="Click Continue.",
            candidates=[ControlCandidate("c001", "Continue", "button", (10, 10, 60, 30))],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "unknown target_id")


class LooksOversizedTests(unittest.TestCase):
    def _make_decision(self, w: int, h: int):
        from agent import LiveHelpDecision

        return LiveHelpDecision(
            kind="step",
            instruction="placeholder",
            target_norm_x=100,
            target_norm_y=200,
            target_norm_width=w,
            target_norm_height=h,
        )

    def test_normal_button_not_oversized(self) -> None:
        from help_session import looks_oversized

        self.assertFalse(looks_oversized(self._make_decision(80, 30)))

    def test_wide_input_not_oversized(self) -> None:
        from help_session import looks_oversized

        self.assertFalse(looks_oversized(self._make_decision(300, 40)))

    def test_panel_sized_box_is_oversized_by_area(self) -> None:
        from help_session import looks_oversized

        self.assertTrue(looks_oversized(self._make_decision(400, 300)))

    def test_very_wide_strip_is_oversized_by_edge(self) -> None:
        from help_session import looks_oversized

        self.assertTrue(looks_oversized(self._make_decision(450, 30)))

    def test_very_tall_column_is_oversized_by_edge(self) -> None:
        from help_session import looks_oversized

        self.assertTrue(looks_oversized(self._make_decision(40, 450)))


if __name__ == "__main__":
    unittest.main()
