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


if __name__ == "__main__":
    unittest.main()

