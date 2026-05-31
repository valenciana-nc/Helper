from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from screen import Capture


class HelpTargetDiagnosticTests(unittest.TestCase):
    def test_build_target_diagnostic_is_json_serializable(self) -> None:
        from agent import _parse_live_help_decision
        from control_inventory import ControlCandidate, TargetResolution
        from help_session import build_target_diagnostic
        from target_quality import TargetQuality

        capture = Capture(
            png_bytes=b"png",
            width=1000,
            height=800,
            monitor_left=-1920,
            monitor_top=40,
            scale=0.5,
        )
        decision = _parse_live_help_decision(
            json.dumps(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": 100, "y": 200, "width": 60, "height": 40},
                    "expected_change": "The file is saved.",
                }
            )
        )
        target = TargetResolution(
            rect=(-1720, 360, 120, 64),
            confidence=0.92,
            source="target_id",
            matched_text="Save",
            target_id="c001",
        )
        payload = build_target_diagnostic(
            decision=decision,
            capture=capture,
            candidates=[
                ControlCandidate("c001", "Save", "button", (-1720, 360, 120, 64)),
            ],
            target=target,
            quality=TargetQuality(
                accepted=True,
                visible_fraction=1.0,
                visual_activity=0.25,
            ),
            overlay_rect=target.rect,
        )

        encoded = json.dumps(payload, sort_keys=True)

        self.assertIn("Click Save", encoded)
        self.assertEqual(payload["capture"]["monitor_left"], -1920)
        self.assertEqual(payload["model"]["screen_rect"], (-1720, 360, 120, 64))
        self.assertTrue(payload["overlay"]["emitted"])
        self.assertEqual(payload["resolution"]["source"], "target_id")
        self.assertEqual(payload["candidate_count"], 1)

    def test_build_target_diagnostic_records_rejection(self) -> None:
        from agent import _parse_live_help_decision
        from control_inventory import TargetResolution
        from help_session import build_target_diagnostic

        capture = Capture(
            png_bytes=b"png",
            width=500,
            height=500,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )
        decision = _parse_live_help_decision(
            json.dumps(
                {
                    "kind": "step",
                    "instruction": "Click the button.",
                    "target_id": "c999",
                }
            )
        )
        target = TargetResolution(
            rect=(0, 0, 0, 0),
            confidence=0.0,
            source="target_id",
            target_id="c999",
            rejected_reason="unknown target_id",
        )

        payload = build_target_diagnostic(
            decision=decision,
            capture=capture,
            candidates=[],
            target=target,
            rejected_reason=target.rejected_reason,
        )

        self.assertFalse(payload["overlay"]["emitted"])
        self.assertEqual(payload["overlay"]["rejected_reason"], "unknown target_id")
        self.assertEqual(payload["quality"], None)

    def test_diagnostic_sink_writes_jsonl(self) -> None:
        from help_diagnostics import HelpTargetDiagnosticSink

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "help_targets.jsonl"
            sink = HelpTargetDiagnosticSink(path=path, enabled=True)

            sink.write({"event": "target", "resolution": {"source": "target_id"}})

            lines = path.read_text(encoding="utf-8").splitlines()

        self.assertEqual(len(lines), 1)
        payload = json.loads(lines[0])
        self.assertEqual(payload["event"], "target")
        self.assertIn("timestamp", payload)

    def test_diagnostic_sink_respects_disabled_flag(self) -> None:
        from help_diagnostics import HelpTargetDiagnosticSink

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "help_targets.jsonl"
            sink = HelpTargetDiagnosticSink(path=path, enabled=False)

            sink.write({"event": "target"})

            self.assertFalse(path.exists())


if __name__ == "__main__":
    unittest.main()
