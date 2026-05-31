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


if __name__ == "__main__":
    unittest.main()

