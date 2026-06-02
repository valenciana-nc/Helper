from __future__ import annotations

import io
import unittest

from PIL import Image, ImageDraw

from screen import Capture


def _capture_with_image(img: Image.Image, *, scale: float = 1.0) -> Capture:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Capture(
        png_bytes=buf.getvalue(),
        width=img.width,
        height=img.height,
        monitor_left=0,
        monitor_top=0,
        scale=scale,
    )


class TargetQualityTests(unittest.TestCase):
    def test_rejects_low_confidence_model_rect_on_blank_space(self) -> None:
        from target_quality import evaluate_target_quality

        capture = _capture_with_image(Image.new("RGB", (200, 120), "white"))

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 30, 60, 30),
            source="model",
            confidence=0.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears visually empty")

    def test_rejects_small_blank_model_rects(self) -> None:
        from target_quality import evaluate_target_quality

        capture = _capture_with_image(Image.new("RGB", (80, 80), "white"))

        for rect in ((10, 10, 20, 30), (40, 40, 10, 10)):
            with self.subTest(rect=rect):
                quality = evaluate_target_quality(
                    capture=capture,
                    rect=rect,
                    source="model",
                    confidence=0.0,
                )
                self.assertFalse(quality.accepted)
                self.assertEqual(quality.reason, "target appears visually empty")

    def test_rejects_blank_model_rect_even_with_snap_fallback_score(self) -> None:
        from target_quality import evaluate_target_quality

        capture = _capture_with_image(Image.new("RGB", (200, 120), "white"))

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 30, 60, 30),
            source="model",
            confidence=0.41,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears visually empty")

    def test_accepts_structured_model_rect(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (200, 120), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((40, 30, 100, 60), outline="black", fill="#f3f4f6")
        draw.text((52, 38), "Save", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 30, 60, 30),
            source="model",
            confidence=0.0,
        )

        self.assertTrue(quality.accepted)
        self.assertGreaterEqual(quality.visual_activity, 0.035)

    def test_rejects_shifted_model_rect_over_single_button(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (220, 140), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((60, 80, 150, 112), outline="black", fill="#f3f4f6")
        draw.text((86, 90), "Save", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(70, 80, 90, 32),
            source="model",
            confidence=0.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target boundary misaligned")
        self.assertGreaterEqual(quality.boundary_activity, 0.10)

    def test_rejects_clipped_model_rect_over_single_button(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (220, 140), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((60, 80, 150, 112), outline="black", fill="#f3f4f6")
        draw.text((86, 90), "Save", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(60, 80, 80, 32),
            source="model",
            confidence=0.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target boundary misaligned")
        self.assertGreaterEqual(quality.boundary_activity, 0.10)

    def test_rejects_edge_flush_model_rect_with_unproven_boundary(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (240, 140), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((24, 80, 184, 112), outline="black", fill="#f3f4f6")
        draw.text((44, 90), "Search", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(0, 80, 184, 32),
            source="model",
            confidence=0.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target boundary misaligned")

    def test_rejects_edge_flush_candidate_rect_with_unproven_boundary(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (240, 140), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((24, 80, 184, 112), outline="black", fill="#f3f4f6")
        draw.text((44, 90), "Search", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(0, 80, 184, 32),
            source="target_id",
            confidence=1.0,
            target_control_type="button",
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target boundary misaligned")

    def test_accepts_edge_flush_taskbar_clock_candidate(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (1000, 1000), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((900, 960, 990, 999), outline="black", fill="#f3f4f6")
        draw.text((912, 972), "11:32 AM", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(900, 960, 90, 40),
            source="target_id",
            confidence=1.0,
            instruction="Open clock.",
            target_control_type="button",
        )

        self.assertTrue(quality.accepted)

    def test_rejects_model_rect_containing_multiple_button_boundaries(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (320, 160), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((60, 74, 140, 106), outline="black", fill="#f3f4f6")
        draw.text((80, 83), "Save", fill="black")
        draw.rectangle((160, 74, 240, 106), outline="black", fill="#f3f4f6")
        draw.text((177, 83), "Cancel", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(60, 74, 180, 32),
            source="model",
            confidence=0.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears to contain multiple controls")

    def test_rejects_model_rect_spanning_two_segmented_buttons(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (320, 160), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((60, 74, 240, 106), outline="black", fill="#f3f4f6")
        draw.line((150, 74, 150, 106), fill="black")
        draw.text((92, 84), "Back", fill="black")
        draw.text((178, 84), "Next", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(60, 74, 180, 32),
            source="model",
            confidence=0.0,
            instruction="Click Next.",
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears to contain multiple controls")

    def test_rejects_candidate_rect_spanning_two_segmented_buttons(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (320, 160), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((60, 74, 240, 106), outline="black", fill="#f3f4f6")
        draw.line((150, 74, 150, 106), fill="black")
        draw.text((92, 84), "Back", fill="black")
        draw.text((178, 84), "Next", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(60, 74, 180, 32),
            source="target_id",
            confidence=1.0,
            instruction="Click Next.",
            target_control_type="button",
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears to contain multiple controls")

    def test_accepts_candidate_icon_label_button_with_internal_edges(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (240, 160), color=(244, 246, 249))
        draw = ImageDraw.Draw(img)
        draw.rectangle((40, 50, 160, 82), fill=(45, 100, 190), outline=(18, 48, 130), width=2)
        draw.rectangle((48, 58, 72, 74), fill=(245, 248, 255))
        draw.line((82, 60, 150, 60), fill=(245, 248, 255), width=3)
        draw.line((82, 70, 132, 70), fill=(245, 248, 255), width=3)
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 50, 120, 32),
            source="target_id",
            confidence=1.0,
            instruction="Click Save changes.",
            target_control_type="button",
        )

        self.assertTrue(quality.accepted)

    def test_rejects_candidate_action_rect_containing_multiple_button_boundaries(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (420, 180), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 80, 380, 128), outline="black", fill="#f8fafc")
        draw.text((34, 96), "Request 42", fill="black")
        draw.rectangle((240, 88, 310, 120), outline="black", fill="#f3f4f6")
        draw.text((252, 97), "Approve", fill="black")
        draw.rectangle((322, 88, 372, 120), outline="black", fill="#f3f4f6")
        draw.text((330, 97), "Reject", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(20, 80, 360, 48),
            source="target_id",
            confidence=0.95,
            instruction="Approve request 42.",
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears to contain multiple controls")

    def test_accepts_candidate_row_rect_for_row_request_even_with_child_boundaries(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (420, 180), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 80, 380, 128), outline="black", fill="#f8fafc")
        draw.text((34, 96), "Request 42", fill="black")
        draw.rectangle((240, 88, 310, 120), outline="black", fill="#f3f4f6")
        draw.text((252, 97), "Approve", fill="black")
        draw.rectangle((322, 88, 372, 120), outline="black", fill="#f3f4f6")
        draw.text((330, 97), "Reject", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(20, 80, 360, 48),
            source="target_id",
            confidence=0.95,
            instruction="Click this row.",
        )

        self.assertTrue(quality.accepted)

    def test_rejects_candidate_row_rect_with_single_embedded_action_button(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (500, 260), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((40, 80, 420, 160), outline="black", fill="#f8fafc")
        draw.text((56, 112), "Invoice 42", fill="black")
        draw.rectangle((300, 100, 360, 132), outline="black", fill="#f3f4f6")
        draw.text((316, 110), "Pay", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 80, 380, 80),
            source="target_id",
            confidence=1.0,
            instruction="Pay invoice 42.",
            target_control_type="listitem",
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears to contain multiple controls")

    def test_accepts_candidate_row_rect_for_row_request_without_embedded_action(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (500, 260), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((40, 80, 420, 160), outline="black", fill="#f8fafc")
        draw.text((56, 112), "Invoice 42", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 80, 380, 80),
            source="target_id",
            confidence=1.0,
            instruction="Click invoice 42 row.",
            target_control_type="listitem",
        )

        self.assertTrue(quality.accepted)

    def test_rejects_partial_candidate_row_rect(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (500, 240), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 80, 420, 128), outline="black", fill="#f8fafc")
        draw.text((36, 96), "Invoice 42", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(20, 80, 120, 48),
            source="target_id",
            confidence=1.0,
            instruction="Click invoice 42 row.",
            target_control_type="listitem",
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target boundary misaligned")

    def test_accepts_text_heavy_candidate_button_without_internal_controls(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (260, 100), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 20, 140, 52), outline="black", fill="#f3f4f6")
        draw.text((34, 29), "Save helper precision", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(20, 20, 120, 32),
            source="text_match",
            confidence=0.76,
            instruction="Click Save helper precision.",
            target_control_type="button",
        )

        self.assertTrue(quality.accepted)

    def test_accepts_compact_candidate_action_icon_with_internal_edges(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (120, 100), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((20, 30, 52, 62), outline="black", fill="#f8fafc")
        draw.line((28, 38, 44, 54), fill="black", width=2)
        draw.line((44, 38, 28, 54), fill="black", width=2)
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(20, 30, 32, 32),
            source="target_id",
            confidence=1.0,
            instruction="Clear search.",
        )

        self.assertTrue(quality.accepted)

    def test_rejects_shifted_candidate_button_rect(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (220, 140), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((60, 80, 150, 112), outline="black", fill="#f3f4f6")
        draw.text((86, 90), "Save", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(70, 80, 90, 32),
            source="target_id",
            confidence=1.0,
            target_control_type="button",
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target boundary misaligned")

    def test_accepts_aligned_candidate_button_rect(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (220, 140), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((60, 80, 150, 112), outline="black", fill="#f3f4f6")
        draw.text((86, 90), "Save", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(60, 80, 90, 32),
            source="target_id",
            confidence=1.0,
            target_control_type="button",
        )

        self.assertTrue(quality.accepted)

    def test_rejects_candidate_button_rect_over_inner_label_only(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (500, 240), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((40, 80, 260, 132), outline="black", fill="#f3f4f6")
        draw.text((104, 98), "Submit order", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(100, 94, 120, 24),
            source="target_id",
            confidence=1.0,
            instruction="Click Submit order.",
            target_control_type="button",
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target boundary misaligned")

    def test_rejects_noisy_model_rect_without_candidate_evidence(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (200, 120), "white")
        draw = ImageDraw.Draw(img)
        for y in range(30, 60, 4):
            for x in range(40, 100, 4):
                color = "black" if ((x + y) // 4) % 2 else "white"
                draw.rectangle((x, y, x + 3, y + 3), fill=color)
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 30, 60, 30),
            source="model",
            confidence=0.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears visually noisy")
        self.assertGreater(quality.visual_activity, 0.40)

    def test_accepts_noisy_candidate_rect_with_uia_evidence(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (200, 120), "white")
        draw = ImageDraw.Draw(img)
        for y in range(30, 60, 4):
            for x in range(40, 100, 4):
                color = "black" if ((x + y) // 4) % 2 else "white"
                draw.rectangle((x, y, x + 3, y + 3), fill=color)
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 30, 60, 30),
            source="target_id",
            confidence=1.0,
        )

        self.assertTrue(quality.accepted)

    def test_rejects_noisy_broad_candidate_container_rect(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (1000, 1000), "white")
        draw = ImageDraw.Draw(img)
        for y in range(80, 200, 4):
            for x in range(20, 380, 4):
                color = "black" if ((x + y) // 4) % 2 else "white"
                draw.rectangle((x, y, x + 3, y + 3), fill=color)
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(20, 80, 360, 120),
            source="candidate_snap",
            confidence=0.95,
            target_control_type="listitem",
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears visually noisy")

    def test_rejects_candidate_rect_on_blank_space(self) -> None:
        from target_quality import evaluate_target_quality

        capture = _capture_with_image(Image.new("RGB", (220, 120), "white"))

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 30, 130, 30),
            source="target_id",
            confidence=1.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target appears visually empty")

    def test_accepts_empty_bordered_candidate_rect(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (220, 120), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((40, 30, 170, 60), outline="#94a3b8", fill="white")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 30, 130, 30),
            source="target_id",
            confidence=1.0,
        )

        self.assertTrue(quality.accepted)
        self.assertGreaterEqual(quality.visual_activity, 0.012)

    def test_rejects_text_only_model_rect_without_control_boundary(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (220, 120), "white")
        draw = ImageDraw.Draw(img)
        draw.text((44, 38), "Save changes now", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 30, 130, 30),
            source="model",
            confidence=0.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target lacks visible control boundary")
        self.assertGreaterEqual(quality.visual_activity, 0.035)

    def test_rejects_text_only_model_rect_even_with_snap_fallback_score(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (220, 120), "white")
        draw = ImageDraw.Draw(img)
        draw.text((44, 38), "Save changes now", fill="black")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(40, 30, 130, 30),
            source="model",
            confidence=0.41,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target lacks visible control boundary")

    def test_rejects_mostly_outside_capture(self) -> None:
        from target_quality import evaluate_target_quality

        capture = _capture_with_image(Image.new("RGB", (200, 120), "white"))

        quality = evaluate_target_quality(
            capture=capture,
            rect=(180, 20, 100, 30),
            source="target_id",
            confidence=1.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target mostly outside capture")

    def test_rejects_raw_rect_with_only_small_visible_sliver(self) -> None:
        from target_quality import evaluate_target_quality

        capture = _capture_with_image(Image.new("RGB", (200, 120), "white"))

        quality = evaluate_target_quality(
            capture=capture,
            rect=(-45, 20, 60, 30),
            source="target_id",
            confidence=1.0,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target mostly outside capture")

    def test_rejects_panel_sized_candidate_rect(self) -> None:
        from target_quality import evaluate_target_quality

        img = Image.new("RGB", (200, 120), "white")
        draw = ImageDraw.Draw(img)
        draw.rectangle((5, 5, 195, 95), outline="black", fill="#f3f4f6")
        capture = _capture_with_image(img)

        quality = evaluate_target_quality(
            capture=capture,
            rect=(5, 5, 190, 90),
            source="target_id",
            confidence=0.95,
        )

        self.assertFalse(quality.accepted)
        self.assertEqual(quality.reason, "target too large")
        self.assertGreater(quality.target_area_fraction, 0.25)


if __name__ == "__main__":
    unittest.main()
