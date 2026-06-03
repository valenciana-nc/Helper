from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path


class HelpHighlightQATests(unittest.TestCase):
    def test_builtin_scenarios_pass(self) -> None:
        from help_highlight_qa import builtin_scenarios, run_scenarios

        with tempfile.TemporaryDirectory() as tmp:
            results = run_scenarios(builtin_scenarios(), artifacts_dir=Path(tmp))

        self.assertTrue(results)
        self.assertTrue(all(result.passed for result in results))

    def test_failure_writes_diagnostic_artifacts(self) -> None:
        from help_highlight_qa import run_scenarios

        scenario = {
            "name": "wrong_expectation",
            "capture": {"width": 240, "height": 160},
            "draw": [{"rect": [30, 40, 80, 32], "label": "Save"}],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "target": {"x": 100, "y": 100, "width": 100, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "button", "rect": [30, 40, 80, 32]},
            ],
            "expected": {"rect": [1, 2, 3, 4], "overlay_emitted": True},
        }

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            results = run_scenarios([scenario], artifacts_dir=root)
            failure_dir = root / "wrong_expectation"

            self.assertFalse(results[0].passed)
            self.assertTrue((failure_dir / "diagnostic.json").exists())
            self.assertTrue((failure_dir / "screen.png").exists())
            self.assertTrue((failure_dir / "overlay.png").exists())
            self.assertTrue((failure_dir / "crop.png").exists())
            self.assertIn("rect:", (failure_dir / "summary.txt").read_text(encoding="utf-8"))
            summary = json.loads((root / "summary.json").read_text(encoding="utf-8"))

        self.assertEqual(summary["failed"], 1)

    def test_runner_applies_final_coverage_gate(self) -> None:
        from help_highlight_qa import run_scenarios

        scenario = {
            "name": "covered_target",
            "capture": {"width": 300, "height": 220},
            "draw": [
                {"rect": [80, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "save",
                "target": {"x": 80, "y": 80, "width": 80, "height": 32},
            },
            "candidates": [
                {"id": "save", "text": "Save", "control_type": "button", "rect": [80, 80, 80, 32]},
                {
                    "id": "popup",
                    "text": "Suggestions popup",
                    "control_type": "window",
                    "rect": [70, 70, 160, 100],
                },
            ],
            "coverage_previous_candidates": [
                {"id": "save", "text": "Save", "control_type": "button", "rect": [80, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "save",
                "rejected_reason": "target covered before overlay",
                "overlay_emitted": False,
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            results = run_scenarios([scenario], artifacts_dir=Path(tmp))

        self.assertTrue(results[0].passed)

    def test_runner_rejects_same_rect_foreground_coverer(self) -> None:
        from help_highlight_qa import run_scenarios

        scenario = {
            "name": "same_rect_foreground_coverer",
            "capture": {"width": 300, "height": 220},
            "draw": [
                {"rect": [80, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "background_save",
                "target": {"x": 80, "y": 80, "width": 80, "height": 32},
            },
            "candidates": [
                {
                    "id": "foreground_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [80, 80, 80, 32],
                    "window_title": "Dialog",
                    "window_rank": 0,
                },
                {
                    "id": "background_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [80, 80, 80, 32],
                    "window_title": "Page",
                    "window_rank": 1,
                },
            ],
            "coverage_previous_candidates": [
                {
                    "id": "background_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [80, 80, 80, 32],
                    "window_title": "Page",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "background_save",
                "rejected_reason": "target covered before overlay",
                "overlay_emitted": False,
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            results = run_scenarios([scenario], artifacts_dir=Path(tmp))

        self.assertTrue(results[0].passed)

    def test_runner_rejects_new_same_rank_same_rect_coverer(self) -> None:
        from help_highlight_qa import run_scenarios

        scenario = {
            "name": "same_rank_same_rect_coverer",
            "capture": {"width": 300, "height": 220},
            "draw": [
                {"rect": [80, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "page_save",
                "target": {"x": 80, "y": 80, "width": 80, "height": 32},
            },
            "candidates": [
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [80, 80, 80, 32],
                    "window_title": "Page",
                    "window_rank": 0,
                },
                {
                    "id": "dialog_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [80, 80, 80, 32],
                    "window_title": "Dialog",
                    "window_rank": 0,
                },
            ],
            "coverage_previous_candidates": [
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [80, 80, 80, 32],
                    "window_title": "Page",
                    "window_rank": 0,
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "page_save",
                "rejected_reason": "target covered before overlay",
                "overlay_emitted": False,
            },
        }

        with tempfile.TemporaryDirectory() as tmp:
            results = run_scenarios([scenario], artifacts_dir=Path(tmp))

        self.assertTrue(results[0].passed)


if __name__ == "__main__":
    unittest.main()
