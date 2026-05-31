from __future__ import annotations

import unittest

from control_inventory import ControlCandidate


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
        from help_precision_selftest import _find_target_candidate

        candidates = [
            ControlCandidate("c001", "Save changes", "button", (1, 1, 10, 10), window_title="Other"),
            ControlCandidate("c002", "Save changes", "button", (2, 2, 10, 10), window_title="Helper Precision Self Test abc"),
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


if __name__ == "__main__":
    unittest.main()
