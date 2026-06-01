from __future__ import annotations

import io
import unittest

from PIL import Image, ImageDraw

from control_inventory import ControlCandidate
from screen import Capture


def _capture_with_image(img: Image.Image) -> Capture:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Capture(
        png_bytes=buf.getvalue(),
        width=img.width,
        height=img.height,
        monitor_left=0,
        monitor_top=0,
        scale=1.0,
    )


class HelpPrecisionSelfTestUnitTests(unittest.TestCase):
    def test_evaluate_selftest_result_passes_matching_overlay(self) -> None:
        from help_precision_selftest import evaluate_selftest_result

        passed, failures = evaluate_selftest_result(
            target_candidate=ControlCandidate("c001", "Save changes", "button", (10, 10, 100, 30)),
            overlay_rect=(10, 10, 100, 30),
            rejected_reason="",
        )

        self.assertTrue(passed)
        self.assertEqual(failures, [])

    def test_evaluate_selftest_result_rejects_missing_overlay(self) -> None:
        from help_precision_selftest import evaluate_selftest_result

        passed, failures = evaluate_selftest_result(
            target_candidate=ControlCandidate("c001", "Save changes", "button", (10, 10, 100, 30)),
            overlay_rect=None,
            rejected_reason="",
        )

        self.assertFalse(passed)
        self.assertIn("overlay rect was not emitted", failures)

    def test_evaluate_selftest_result_rejects_low_iou(self) -> None:
        from help_precision_selftest import evaluate_selftest_result

        passed, failures = evaluate_selftest_result(
            target_candidate=ControlCandidate("c001", "Save changes", "button", (10, 10, 100, 30)),
            overlay_rect=(200, 200, 100, 30),
            rejected_reason="",
        )

        self.assertFalse(passed)
        self.assertTrue(any("IoU too low" in failure for failure in failures))

    def test_find_target_candidate_filters_by_window_title(self) -> None:
        from help_precision_selftest import TARGET_TEXT, _find_target_candidate

        candidates = [
            ControlCandidate("c001", "Save changes", "button", (1, 1, 10, 10), window_title="Other"),
            ControlCandidate("c002", TARGET_TEXT, "button", (2, 2, 10, 10), window_title="Helper Precision Self Test abc"),
        ]

        result = _find_target_candidate(
            candidates,
            title="Helper Precision Self Test abc",
        )

        self.assertIsNotNone(result)
        assert result is not None
        self.assertEqual(result.id, "c002")

    def test_manifest_marks_save_control_required(self) -> None:
        from help_precision_selftest import _manifest

        manifest = _manifest("Helper Precision Self Test abc")

        required = [item for item in manifest["expected_controls"] if item["required"]]
        self.assertEqual(required[0]["automation_id"], "helperPrecisionSave")
        self.assertEqual(manifest["window_title"], "Helper Precision Self Test abc")

    def test_evaluate_case_result_allows_expected_rejection(self) -> None:
        from help_precision_selftest import evaluate_case_result

        passed, failures = evaluate_case_result(
            expected_candidate=None,
            overlay_rect=None,
            rejected_reason="unknown target_id",
            expect_rejected_reason="unknown target_id",
            expect_overlay=False,
        )

        self.assertTrue(passed)
        self.assertEqual(failures, [])

    def test_evaluate_case_result_rejects_unexpected_overlay(self) -> None:
        from help_precision_selftest import evaluate_case_result

        passed, failures = evaluate_case_result(
            expected_candidate=None,
            overlay_rect=(1, 2, 3, 4),
            rejected_reason="",
            expect_overlay=False,
        )

        self.assertFalse(passed)
        self.assertTrue(any("unexpectedly" in failure for failure in failures))

    def test_resolution_cases_cover_wrong_target_id_paths(self) -> None:
        from help_precision_selftest import TARGET_TEXT, _run_resolution_cases

        img = Image.new("RGB", (260, 100), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 20, 140, 52), outline="black", fill="#f3f4f6")
        draw.text((34, 29), TARGET_TEXT, fill="black")
        draw.rectangle((160, 20, 235, 52), outline="black", fill="#f3f4f6")
        draw.text((174, 29), "Cancel", fill="black")
        capture = _capture_with_image(img)
        title = "Helper Precision Self Test unit"
        save = ControlCandidate(
            "c001",
            TARGET_TEXT,
            "button",
            (20, 20, 120, 32),
            window_title=title,
        )
        cancel = ControlCandidate(
            "c002",
            "Cancel",
            "button",
            (160, 20, 75, 32),
            window_title=title,
        )

        cases = _run_resolution_cases(
            capture=capture,
            candidates=[save, cancel],
            target_candidate=save,
            title=title,
        )
        by_name = {case["name"]: case for case in cases}

        recovered = by_name["wrong_target_id_recovers_by_text_match"]
        self.assertTrue(recovered["passed"], recovered["failures"])
        self.assertEqual(recovered["overlay_rect"], (20, 20, 120, 32))

        rejected = by_name["copied_wrong_target_id_without_semantic_alternative_rejects"]
        self.assertTrue(rejected["passed"], rejected["failures"])
        self.assertEqual(rejected["rejected_reason"], "target_id semantic mismatch")


if __name__ == "__main__":
    unittest.main()
