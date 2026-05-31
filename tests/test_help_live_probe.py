from __future__ import annotations

import io
import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from control_inventory import ControlCandidate
from screen import Capture


def _capture() -> Capture:
    image = Image.new("RGB", (200, 120), "white")
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return Capture(
        png_bytes=buf.getvalue(),
        width=200,
        height=120,
        monitor_left=-100,
        monitor_top=20,
        scale=0.5,
    )


class HelpLiveProbeTests(unittest.TestCase):
    def test_screen_rect_to_image_box_handles_negative_origin_and_scale(self) -> None:
        from help_live_probe import screen_rect_to_image_box

        box = screen_rect_to_image_box(_capture(), (-80, 60, 40, 20))

        self.assertEqual(box, (10, 20, 30, 30))

    def test_build_probe_summary_includes_candidate_image_boxes(self) -> None:
        from help_live_probe import build_probe_summary

        summary = build_probe_summary(
            _capture(),
            [ControlCandidate("c001", "Save", "button", (-80, 60, 40, 20))],
        )

        self.assertEqual(summary["candidate_count"], 1)
        self.assertEqual(summary["capture"]["monitor_left"], -100)
        self.assertEqual(summary["candidates"][0]["image_box"], (10, 20, 30, 30))

    def test_run_probe_writes_artifacts(self) -> None:
        from help_live_probe import run_probe

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            summary = run_probe(
                artifacts_dir=root,
                capture_provider=_capture,
                candidates=[
                    ControlCandidate("c001", "Save", "button", (-80, 60, 40, 20))
                ],
            )
            payload = json.loads((root / "candidates.json").read_text(encoding="utf-8"))

            self.assertTrue((root / "screen.png").exists())
            self.assertTrue((root / "controls_overlay.png").exists())

        self.assertEqual(summary["candidate_count"], 1)
        self.assertEqual(payload["candidates"][0]["id"], "c001")


if __name__ == "__main__":
    unittest.main()

