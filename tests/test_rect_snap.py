"""Tests for rect_snap.snap_to_control and help_session.looks_oversized."""
from __future__ import annotations

import unittest
from unittest.mock import patch


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
        handle: int | None = None,
        enabled: bool = True,
        visible: bool = True,
    ) -> None:
        self.control_type = control_type
        self.name = name
        self.automation_id = automation_id
        self.rectangle = rectangle
        self.handle = handle
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
        handle: int | None = None,
        enabled: bool = True,
        visible: bool = True,
        children: list["_FakeControl"] | None = None,
    ) -> None:
        self._text = text
        self.handle = handle
        self._children = list(children or [])
        self.element_info = _FakeElementInfo(
            control_type=control_type,
            name=text,
            automation_id=automation_id,
            rectangle=rect,
            handle=handle,
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


class _RecordingControl(_FakeControl):
    def __init__(self, *, visits: list[str], **kwargs: object) -> None:
        super().__init__(**kwargs)
        self._visits = visits

    def children(self) -> list[_FakeControl]:
        self._visits.append(self.window_text())
        return super().children()


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
    *,
    handle: int | None = None,
) -> _FakeControl:
    return _FakeControl(
        text=name,
        control_type="Window",
        rect=_FakeRect(x, y, x + w, y + h),
        handle=handle,
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

    def test_skips_disabled_or_hidden_controls(self) -> None:
        from rect_snap import snap_to_control

        disabled = _make_button("Submit", 100, 200, 60, 30)
        disabled.element_info.enabled = False
        hidden = _make_button("Submit", 110, 200, 60, 30)
        hidden.element_info.visible = False
        window = _make_window("App", 0, 0, 800, 600, [disabled, hidden])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 60, 30),
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "model")

    def test_semantic_mismatch_does_not_snap_wrong_labeled_control(self) -> None:
        from rect_snap import snap_to_control

        cancel = _make_button("Cancel", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [cancel])
        desktop = _FakeDesktop([window])
        model_rect = (100, 200, 60, 30)

        result = snap_to_control(
            model_rect,
            "Click Submit",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )
        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_semantic_mismatch_rejects_loose_model_rect_centered_on_wrong_control(self) -> None:
        from rect_snap import snap_to_control

        cancel = _make_button("Cancel", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [cancel])
        desktop = _FakeDesktop([window])
        model_rect = (80, 185, 120, 70)

        result = snap_to_control(
            model_rect,
            "Click Save",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 60, 30))
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")

    def test_snap_rejects_visible_text_conflict_despite_matching_automation_id(self) -> None:
        from rect_snap import snap_to_control

        cancel = _make_button("Cancel", 100, 200, 60, 30, automation_id="saveButton")
        window = _make_window("App", 0, 0, 800, 600, [cancel])
        desktop = _FakeDesktop([window])
        model_rect = (100, 200, 60, 30)

        result = snap_to_control(
            model_rect,
            "Click Save",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")
        self.assertIn("saveButton", result.matched_text)

    def test_snap_prefers_visible_text_over_automation_only_match(self) -> None:
        from rect_snap import snap_to_control

        icon = _make_button("", 100, 200, 32, 32, automation_id="save_button")
        save = _make_button("Save", 145, 200, 80, 32)
        window = _make_window("App", 0, 0, 800, 600, [icon, save])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (145, 200, 80, 32))
        self.assertEqual(result.matched_text, "Save")
        self.assertFalse(result.rejected_reason)

    def test_snap_rejects_automation_only_match_when_visible_alternative_is_weak(self) -> None:
        from rect_snap import snap_to_control

        icon = _make_button("", 100, 200, 32, 32, automation_id="save_button")
        save = _make_button("Save", 190, 260, 80, 32, control_type="Spinner")
        window = _make_window("App", 0, 0, 800, 600, [icon, save])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 32, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 32, 32))
        self.assertEqual(result.rejected_reason, "automation-only target ambiguous")

    def test_snap_rejects_background_duplicate_when_foreground_is_plausible(self) -> None:
        from rect_snap import snap_to_control

        background_save = _make_button("Save", 100, 200, 80, 32)
        foreground_save = _make_button("Save", 100, 250, 80, 32)
        background = _make_window(
            "Background Editor",
            0,
            0,
            400,
            320,
            [background_save],
            handle=101,
        )
        foreground = _make_window(
            "Active Editor",
            0,
            40,
            400,
            320,
            [foreground_save],
            handle=202,
        )
        desktop = _FakeDesktop([background, foreground])

        result = snap_to_control(
            (100, 200, 80, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            foreground_handle_provider=lambda: 202,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 80, 32))
        self.assertEqual(result.rejected_reason, "foreground target ambiguous")

    def test_snap_rejects_occluded_background_target(self) -> None:
        from rect_snap import snap_to_control

        save = _make_button("Save", 100, 200, 80, 32)
        background = _make_window("Background Editor", 0, 0, 400, 320, [save], handle=101)
        blocking_dialog = _make_window("Blocking Dialog", 70, 170, 180, 100, [], handle=202)
        desktop = _FakeDesktop([background, blocking_dialog])

        def topmost_at(x: int, y: int) -> int:
            if 70 <= x < 250 and 170 <= y < 270:
                return 202
            return 101

        result = snap_to_control(
            (100, 200, 80, 32),
            "Click Save.",
            desktop_factory=lambda: desktop,
            topmost_handle_provider=topmost_at,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 80, 32))
        self.assertEqual(result.matched_text, "Save")
        self.assertEqual(result.rejected_reason, "occluded target")

    def test_snap_rejects_own_process_target_instead_of_raw_fallback(self) -> None:
        from rect_snap import snap_to_control

        helper_button = _make_button("Save", 100, 200, 60, 30)
        helper_window = _make_window("Helper", 0, 0, 800, 600, [helper_button], handle=101)
        desktop = _FakeDesktop([helper_window])
        model_rect = (100, 200, 60, 30)

        with patch("rect_snap._is_own_process_window", side_effect=lambda hwnd: hwnd == 101):
            result = snap_to_control(
                model_rect,
                "Click Save",
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, model_rect)
        self.assertEqual(result.matched_text, "Save")
        self.assertEqual(result.rejected_reason, "own process target")

    def test_snap_uses_common_ui_label_synonyms(self) -> None:
        from rect_snap import snap_to_control

        options = _make_button("Options", 100, 200, 60, 30)
        window = _make_window("App", 0, 0, 800, 600, [options])
        desktop = _FakeDesktop([window])

        result = snap_to_control(
            (100, 200, 60, 30),
            "Click the settings gear.",
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(result.source, "uia")
        self.assertEqual(result.rect, (100, 200, 60, 30))
        self.assertIn("Options", result.matched_text)

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

    def test_collect_dedupes_same_visible_control_with_different_automation_ids(self) -> None:
        from control_inventory import collect_control_candidates

        save_a = _make_button("Save", 100, 200, 60, 30, automation_id="save-a")
        save_b = _make_button("Save", 100, 200, 60, 30, automation_id="save-b")
        window = _make_window("Editor", 0, 0, 800, 600, [save_a, save_b])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].text, "Save")
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

    def test_candidate_prompt_separates_visible_text_from_automation_id(self) -> None:
        from control_inventory import ControlCandidate, format_candidates_for_prompt

        prompt = format_candidates_for_prompt(
            [
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (10, 10, 60, 30),
                    automation_id="saveButton",
                )
            ],
            self._capture(),
        )

        self.assertIn('visible_text="Cancel"', prompt)
        self.assertIn('automation_id="saveButton"', prompt)
        self.assertNotIn('"Cancel saveButton"', prompt)
        self.assertIn("do not treat automation_id as visible screen text", prompt)

    def test_resolve_exact_target_id_wins_when_semantically_compatible(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        candidates = [
            ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
            ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
        ]

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Submit.",
            candidates=candidates,
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rect, (100, 10, 60, 30))
        self.assertFalse(result.rejected_reason)

    def test_target_id_semantic_mismatch_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Cancel.",
            candidates=[
                ControlCandidate("c001", "Cancel", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Submit", "button", (100, 10, 60, 30)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id semantic mismatch")

    def test_visible_text_conflict_rejects_target_id_despite_matching_automation_id(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (10, 10, 60, 30),
                    automation_id="saveButton",
                )
            ],
            model_rect=(10, 10, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id semantic mismatch")

    def test_visible_text_conflict_does_not_resolve_by_automation_id_text_match(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (10, 10, 60, 30),
                    automation_id="saveButton",
                )
            ],
        )

        self.assertIsNone(result)

    def test_text_match_prefers_visible_label_over_automation_only_geometry(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")
        self.assertFalse(result.rejected_reason)

    def test_target_id_accepts_common_ui_synonym_with_exact_geometry(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click the settings gear.",
            candidates=[
                ControlCandidate("c001", "Options", "button", (100, 10, 32, 32)),
            ],
            model_rect=(100, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (100, 10, 32, 32))

    def test_text_match_uses_common_ui_synonyms(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click the settings gear.",
            candidates=[
                ControlCandidate("c001", "Options", "button", (100, 10, 32, 32)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c001")

    def test_unlabeled_target_id_can_pass_with_exact_geometry_when_unambiguous(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this icon.",
            candidates=[
                ControlCandidate("c001", "", "button", (100, 10, 32, 32)),
            ],
            model_rect=(100, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "target_id")

    def test_unlabeled_target_id_rejects_exact_geometry_when_visible_alternative_matches(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
                ControlCandidate("c002", "Save", "button", (100, 10, 60, 30)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_automation_only_target_id_rejects_when_visible_alternative_matches(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_target_id_duplicate_label_without_geometry_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_target_id_duplicate_label_with_geometry_is_accepted(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30)),
            ],
            model_rect=(298, 8, 64, 34),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (300, 10, 60, 30))

    def test_target_id_rejects_matching_row_when_tight_child_action_exists(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Settings.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_generic_target_id_rejects_row_containing_tight_actions(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "Account row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Edit", "button", (450, 20, 60, 30)),
                ControlCandidate("c003", "Delete", "button", (520, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_generic_field_target_id_accepts_edit_containing_clear_action(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this field.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(10, 10, 600, 40),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 600, 40))

    def test_generic_field_target_id_rejects_wrong_button_type(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click this field.",
            candidates=[
                ControlCandidate("c001", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(570, 14, 28, 28),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id control type mismatch")

    def test_target_id_foreground_duplicate_without_geometry_is_accepted(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.target_id, "c002")

    def test_target_id_background_duplicate_without_geometry_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_target_id_background_duplicate_with_geometry_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c001",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
            model_rect=(10, 10, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "target_id")
        self.assertEqual(result.rejected_reason, "target_id ambiguous")

    def test_text_match_prefers_foreground_duplicate_across_windows(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "text_match")
        self.assertEqual(result.target_id, "c002")

    def test_unlabeled_target_id_with_nearby_unlabeled_competitor_is_rejected(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "", "button", (100, 10, 32, 32)),
                ControlCandidate("c002", "", "button", (140, 10, 32, 32)),
            ],
            model_rect=(140, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id ambiguous unlabeled control")

    def test_icon_only_target_id_with_nearby_icon_ignores_automation_ids_for_ambiguity(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="c002",
            instruction="Click this icon.",
            candidates=[
                ControlCandidate("c001", "", "button", (100, 10, 32, 32), automation_id="helperPrecisionIconA"),
                ControlCandidate("c002", "", "button", (140, 10, 32, 32), automation_id="helperPrecisionIconB"),
            ],
            model_rect=(140, 10, 32, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "target_id ambiguous unlabeled control")

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

    def test_ambiguous_text_match_returns_rejected_resolution(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30)),
                ControlCandidate("c002", "Save", "button", (100, 10, 60, 30)),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.rejected_reason, "ambiguous text match")

    def test_text_match_ignores_same_visual_duplicate(self) -> None:
        from control_inventory import ControlCandidate, resolve_candidate_target

        result = resolve_candidate_target(
            target_id="",
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), automation_id="save-a"),
                ControlCandidate("c002", "Save", "button", (10, 10, 60, 30), automation_id="save-b"),
            ],
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.target_id, "c001")

    def test_norm_rect_clips_partially_offscreen_candidate(self) -> None:
        from control_inventory import collect_control_candidates, format_candidates_for_prompt

        button = _make_button("Edge", -20, 120, 60, 30)
        window = _make_window("App", -40, 0, 200, 600, [button])
        desktop = _FakeDesktop([window])
        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        prompt = format_candidates_for_prompt(candidates, self._capture())

        self.assertIn("Edge", prompt)
        self.assertIn("norm=(0,200,50,50)", prompt)

    def test_collect_prefers_tighter_child_over_matching_container(self) -> None:
        from control_inventory import collect_control_candidates

        child = _make_button("Save", 120, 120, 50, 24)
        parent = _FakeControl(
            text="Save",
            control_type="Button",
            rect=_FakeRect(100, 100, 240, 180),
            children=[child],
        )
        window = _make_window("App", 0, 0, 800, 600, [parent])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertEqual(len(candidates), 1)
        self.assertEqual(candidates[0].rect, (120, 120, 50, 24))

    def test_labeled_parent_is_not_pruned_by_unlabeled_child_glyph(self) -> None:
        from control_inventory import collect_control_candidates

        glyph = _FakeControl(
            text="",
            control_type="Button",
            rect=_FakeRect(104, 104, 124, 124),
        )
        parent = _FakeControl(
            text="Enable sync",
            control_type="CheckBox",
            rect=_FakeRect(100, 100, 220, 132),
            children=[glyph],
        )
        window = _make_window("Settings", 0, 0, 800, 600, [parent])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertTrue(any(candidate.text == "Enable sync" for candidate in candidates))

    def test_visible_parent_is_not_pruned_by_automation_only_child_glyph(self) -> None:
        from control_inventory import collect_control_candidates

        glyph = _FakeControl(
            text="",
            control_type="Button",
            rect=_FakeRect(104, 104, 124, 124),
            automation_id="saveChangesIcon",
        )
        parent = _FakeControl(
            text="Save changes",
            control_type="Button",
            rect=_FakeRect(100, 100, 240, 132),
            children=[glyph],
        )
        window = _make_window("Editor", 0, 0, 800, 600, [parent])
        desktop = _FakeDesktop([window])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            timeout_ms=2000,
        )

        self.assertGreaterEqual(len(candidates), 2)
        self.assertEqual(candidates[0].text, "Save changes")
        self.assertEqual(candidates[0].rect, (100, 100, 140, 32))
        self.assertTrue(any(candidate.automation_id == "saveChangesIcon" for candidate in candidates))

    def test_collect_prioritizes_foreground_window_before_screen_position(self) -> None:
        from control_inventory import collect_control_candidates

        background_buttons = [
            _make_button(f"Browser {index}", 10 + index * 12, 10, 10, 24)
            for index in range(12)
        ]
        background = _make_window("Browser", 0, 0, 800, 120, background_buttons, handle=101)
        save = _make_button("Save changes", 120, 500, 90, 32)
        foreground = _make_window("Editor", 0, 420, 800, 180, [save], handle=202)
        desktop = _FakeDesktop([background, foreground])

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            foreground_handle_provider=lambda: 202,
            timeout_ms=2000,
            limit=3,
        )

        self.assertTrue(candidates)
        self.assertEqual(candidates[0].text, "Save changes")
        self.assertEqual(candidates[0].id, "c001")

    def test_collect_visits_foreground_window_before_background_windows(self) -> None:
        from control_inventory import collect_control_candidates

        visits: list[str] = []
        background = _RecordingControl(
            text="Background",
            control_type="Window",
            rect=_FakeRect(0, 0, 800, 300),
            handle=101,
            children=[_make_button("Background button", 20, 20, 120, 30)],
            visits=visits,
        )
        foreground = _RecordingControl(
            text="Foreground",
            control_type="Window",
            rect=_FakeRect(0, 320, 800, 620),
            handle=202,
            children=[_make_button("Foreground button", 20, 340, 120, 30)],
            visits=visits,
        )
        desktop = _FakeDesktop([background, foreground])

        collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            foreground_handle_provider=lambda: 202,
            timeout_ms=2000,
        )

        self.assertGreaterEqual(len(visits), 2)
        self.assertEqual(visits[:2], ["Foreground", "Background"])

    def test_collect_skips_own_process_top_level_windows(self) -> None:
        from control_inventory import collect_control_candidates

        helper_button = _make_button("Helper settings", 20, 20, 120, 30)
        helper_window = _make_window("Helper", 0, 0, 200, 120, [helper_button], handle=101)
        app_button = _make_button("Save changes", 220, 20, 120, 30)
        app_window = _make_window("Editor", 200, 0, 300, 120, [app_button], handle=202)
        desktop = _FakeDesktop([helper_window, app_window])

        with patch("control_inventory._is_own_process_window", side_effect=lambda hwnd: hwnd == 101):
            candidates = collect_control_candidates(
                self._capture(),
                desktop_factory=lambda: desktop,
                timeout_ms=2000,
            )

        self.assertEqual([candidate.text for candidate in candidates], ["Save changes"])
        self.assertEqual(candidates[0].window_title, "Editor")

    def test_collect_skips_occluded_background_window_candidates(self) -> None:
        from control_inventory import collect_control_candidates

        save = _make_button("Save changes", 40, 40, 120, 30)
        background = _make_window("Background Editor", 0, 0, 240, 140, [save], handle=101)
        dismiss = _make_button("Dismiss", 50, 45, 100, 30)
        foreground = _make_window("Blocking Dialog", 20, 20, 220, 120, [dismiss], handle=202)
        desktop = _FakeDesktop([background, foreground])

        def topmost_at(x: int, y: int) -> int:
            if 20 <= x < 240 and 20 <= y < 140:
                return 202
            return 101

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            topmost_handle_provider=topmost_at,
            timeout_ms=2000,
        )

        labels = [candidate.text for candidate in candidates]
        self.assertIn("Dismiss", labels)
        self.assertNotIn("Save changes", labels)

    def test_collect_skips_candidate_when_click_center_is_occluded(self) -> None:
        from control_inventory import collect_control_candidates

        save = _make_button("Save changes", 40, 40, 120, 30)
        background = _make_window("Background Editor", 0, 0, 240, 140, [save], handle=101)
        dismiss = _make_button("Dismiss", 90, 45, 80, 30)
        foreground = _make_window("Blocking Dialog", 80, 35, 100, 50, [dismiss], handle=202)
        desktop = _FakeDesktop([background, foreground])

        def topmost_at(x: int, y: int) -> int:
            if 80 <= x < 180 and 35 <= y < 85:
                return 202
            return 101

        candidates = collect_control_candidates(
            self._capture(),
            desktop_factory=lambda: desktop,
            topmost_handle_provider=topmost_at,
            timeout_ms=2000,
        )

        labels = [candidate.text for candidate in candidates]
        self.assertIn("Dismiss", labels)
        self.assertNotIn("Save changes", labels)

    def test_snap_candidate_target_reuses_collected_candidate_snapshot(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[ControlCandidate("c001", "Save", "button", (100, 100, 50, 24))],
            model_rect=(96, 96, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.rect, (100, 100, 50, 24))

    def test_snap_candidate_target_prefers_tight_action_inside_matching_row(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Settings.",
            candidates=[
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertEqual(result.rect, (20, 20, 70, 30))

    def test_snap_candidate_target_rejects_generic_row_containing_tight_actions(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "Account row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Edit", "button", (450, 20, 60, 30)),
                ControlCandidate("c003", "Delete", "button", (520, 20, 70, 30)),
            ],
            model_rect=(10, 10, 600, 80),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_accepts_generic_field_containing_clear_action(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this field.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(10, 10, 600, 40),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 600, 40))

    def test_snap_candidate_target_accepts_generic_text_box_without_placeholder_match(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this text box.",
            candidates=[
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
            model_rect=(10, 10, 600, 40),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.rect, (10, 10, 600, 40))

    def test_snap_candidate_target_ignores_same_visual_duplicate(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (100, 100, 50, 24), automation_id="save-a"),
                ControlCandidate("c002", "Save", "button", (100, 100, 50, 24), automation_id="save-b"),
            ],
            model_rect=(96, 96, 60, 30),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c001")

    def test_snap_candidate_target_prefers_foreground_duplicate_when_geometry_is_close(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (80, 100, 80, 32), window_rank=2),
                ControlCandidate("c002", "Save", "button", (170, 100, 80, 32), window_rank=0),
            ],
            model_rect=(130, 100, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertFalse(result.rejected_reason)
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")

    def test_snap_candidate_target_rejects_exact_background_duplicate(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click this button.",
            candidates=[
                ControlCandidate("c001", "Save", "button", (120, 100, 80, 32), window_rank=0),
                ControlCandidate("c002", "Save", "button", (120, 145, 80, 32), window_rank=2),
            ],
            model_rect=(120, 145, 80, 32),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.target_id, "c002")
        self.assertEqual(result.rejected_reason, "ambiguous candidate snap")

    def test_snap_candidate_target_rejects_automation_only_when_visible_alternative_exists(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (160, 10, 80, 32)),
            ],
            model_rect=(10, 10, 32, 32),
        )

        self.assertIsNone(result)

    def test_snap_candidate_target_rejects_visible_text_conflict_despite_matching_automation_id(self) -> None:
        from control_inventory import ControlCandidate, snap_candidate_target

        result = snap_candidate_target(
            instruction="Click Save.",
            candidates=[
                ControlCandidate(
                    "c001",
                    "Cancel",
                    "button",
                    (100, 100, 50, 24),
                    automation_id="saveButton",
                )
            ],
            model_rect=(100, 100, 50, 24),
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.source, "candidate_snap")
        self.assertEqual(result.rejected_reason, "candidate semantic mismatch")


class HelpTargetHarnessTests(unittest.TestCase):
    def _capture(self):
        from screen import Capture

        return Capture(
            png_bytes=b"png",
            width=1000,
            height=1000,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )

    def _decision(self, payload: dict):
        from agent import _parse_live_help_decision
        import json

        return _parse_live_help_decision(json.dumps(payload))

    def test_target_id_uses_candidate_rect_not_model_rect(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 100, "y": 150, "width": 120, "height": 60},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 80, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_wrong_target_id_recovers_by_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c002",
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (120, 160, 80, 32)),
                ControlCandidate("c002", "Cancel", "button", (260, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c001")

    def test_unlabeled_target_id_with_geometry_recovers_to_visible_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "", "button", (10, 10, 32, 32)),
                ControlCandidate("c002", "Save", "button", (100, 10, 60, 30)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (100, 10, 60, 30))
        self.assertFalse(target.rejected_reason)

    def test_model_rect_on_automation_only_candidate_recovers_to_visible_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (120, 10, 80, 32))
        self.assertFalse(target.rejected_reason)

    def test_automation_only_target_id_recovers_to_visible_text_match(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 32, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "", "button", (10, 10, 32, 32), automation_id="saveButton"),
                ControlCandidate("c002", "Save", "button", (120, 10, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "text_match")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (120, 10, 80, 32))
        self.assertFalse(target.rejected_reason)

    def test_background_target_id_with_geometry_does_not_resnap_same_rejected_target(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 60, "height": 30},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (10, 10, 60, 30), window_rank=2),
                ControlCandidate("c002", "Save", "button", (300, 10, 60, 30), window_rank=0),
            ],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.target_id, "c001")
        self.assertEqual(target.rejected_reason, "target_id ambiguous")

    def test_wrong_target_id_recovers_by_geometry_when_text_is_ambiguous(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Cancel", "button", (20, 160, 80, 32)),
                ControlCandidate("c002", "Save", "button", (120, 160, 80, 32)),
                ControlCandidate("c003", "Save", "button", (260, 160, 80, 32)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_unknown_target_id_without_rect_downgrades_no_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c999",
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 80, 32))],
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "unknown target_id")

    def test_unknown_target_id_with_rect_does_not_fall_back_to_raw_model_rect(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c999",
                    "target": {"x": 400, "y": 400, "width": 70, "height": 30},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Cancel", "button", (120, 160, 80, 32))],
            snapper=lambda rect, _instruction: SnapResult(rect=rect, confidence=0.0, source="model"),
        )

        self.assertEqual(target.source, "target_id")
        self.assertEqual(target.rejected_reason, "unknown target_id")

    def test_model_rect_snaps_to_candidate_snapshot_without_fresh_uia(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        calls: list[bool] = []

        def snapper(_rect, _instruction):
            calls.append(True)
            raise AssertionError("fresh UIA snapper should not be called")

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button.",
                    "target": {"x": 105, "y": 155, "width": 105, "height": 50},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Save", "button", (120, 160, 80, 32))],
            snapper=snapper,
        )

        self.assertFalse(calls)
        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rect, (120, 160, 80, 32))

    def test_loose_row_model_rect_snaps_to_tight_child_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Settings.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (20, 20, 70, 30))
        self.assertFalse(target.rejected_reason)

    def test_row_target_id_recovers_to_tight_child_action(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Settings.",
                    "target_id": "c001",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Settings", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Settings", "button", (20, 20, 70, 30)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rect, (20, 20, 70, 30))
        self.assertFalse(target.rejected_reason)

    def test_generic_row_model_rect_with_actions_downgrades_no_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 80},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Account row", "listitem", (10, 10, 600, 80)),
                ControlCandidate("c002", "Edit", "button", (450, 20, 60, 30)),
                ControlCandidate("c003", "Delete", "button", (520, 20, 70, 30)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_generic_field_model_rect_with_clear_action_highlights_field(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this field.",
                    "target": {"x": 10, "y": 10, "width": 600, "height": 40},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Search", "edit", (10, 10, 600, 40)),
                ControlCandidate("c002", "Clear", "button", (570, 14, 28, 28)),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c001")
        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (10, 10, 600, 40))

    def test_generic_model_rect_rejects_background_snap_when_foreground_is_plausible(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click this button.",
                    "target": {"x": 120, "y": 136, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [
                ControlCandidate("c001", "Save", "button", (120, 100, 80, 32), window_rank=0),
                ControlCandidate("c002", "Save", "button", (120, 145, 80, 32), window_rank=2),
            ],
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.target_id, "c002")
        self.assertEqual(target.rejected_reason, "ambiguous candidate snap")

    def test_model_rect_on_mismatched_candidate_rejects_instead_of_raw_overlay(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        model_rect = (120, 160, 80, 32)
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Cancel", "button", model_rect)],
            snapper=lambda rect, _instruction: SnapResult(
                rect=rect,
                confidence=0.41,
                source="model",
                matched_text="Cancel",
            ),
        )

        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.matched_text, "Cancel")
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_candidate_snapshot_miss_does_not_call_fresh_snapper(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import resolve_help_target

        calls: list[bool] = []

        def snapper(_rect, _instruction):
            calls.append(True)
            raise AssertionError("fresh snapper should not run after candidate snapshot no-match")

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 420, "y": 420, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Cancel", "button", (120, 160, 80, 32))],
            snapper=snapper,
        )

        self.assertFalse(calls)
        self.assertEqual(target.source, "candidate_snap")
        self.assertEqual(target.rejected_reason, "candidate snapshot no match")

    def test_rejected_fresh_snap_does_not_fall_back_to_raw_model_rect(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        model_rect = (120, 160, 80, 32)
        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target": {"x": 120, "y": 160, "width": 80, "height": 32},
                }
            ),
            self._capture(),
            [],
            snapper=lambda _rect, _instruction: SnapResult(
                rect=model_rect,
                confidence=0.41,
                source="uia",
                matched_text="Cancel saveButton",
                rejected_reason="candidate semantic mismatch",
            ),
        )

        self.assertEqual(target.source, "snap")
        self.assertEqual(target.rect, model_rect)
        self.assertEqual(target.rejected_reason, "candidate semantic mismatch")

    def test_oversized_model_rect_is_rejected(self) -> None:
        from help_session import resolve_help_target
        from rect_snap import SnapResult

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click the button.",
                    "target": {"x": 100, "y": 100, "width": 600, "height": 300},
                }
            ),
            self._capture(),
            [],
            snapper=lambda rect, _instruction: SnapResult(
                rect=rect,
                confidence=0.0,
                source="model",
            ),
        )

        self.assertEqual(target.source, "model")
        self.assertEqual(target.rejected_reason, "oversized target")

    def test_partially_offscreen_candidate_is_clipped_before_display(self) -> None:
        from control_inventory import ControlCandidate
        from help_session import clip_resolution_to_capture, resolve_help_target

        target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Edge.",
                    "target_id": "c001",
                    "target": {"x": 0, "y": 120, "width": 40, "height": 30},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Edge", "button", (-20, 120, 60, 30))],
        )

        self.assertFalse(target.rejected_reason)
        self.assertEqual(target.rect, (0, 120, 40, 30))

        raw_target = resolve_help_target(
            self._decision(
                {
                    "kind": "step",
                    "instruction": "Click Edge.",
                    "target_id": "c001",
                    "target": {"x": 0, "y": 120, "width": 40, "height": 30},
                }
            ),
            self._capture(),
            [ControlCandidate("c001", "Edge", "button", (-20, 120, 60, 30))],
            clip_to_capture=False,
        )
        clipped = clip_resolution_to_capture(raw_target, self._capture())
        self.assertEqual(raw_target.rect, (-20, 120, 60, 30))
        self.assertEqual(clipped.rect, (0, 120, 40, 30))


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
