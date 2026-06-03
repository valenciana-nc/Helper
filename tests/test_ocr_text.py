from __future__ import annotations

import io
import unittest
from unittest.mock import patch

from PIL import Image

from control_inventory import ControlCandidate, TargetResolution
from ocr_text import (
    OCR_EXTRA_TEXT_REASON,
    OCR_MISSING_TEXT_REASON,
    OCR_PARTIAL_TEXT_REASON,
    OCR_TEXT_MISMATCH_REASON,
    OcrTextResult,
    WindowsOcrTextProvider,
    expected_text_evidence_for_target,
    expected_text_for_target,
    verify_target_text,
    _screen_rect_to_image_box,
)
from screen import Capture


def _capture(
    *,
    width: int = 200,
    height: int = 120,
    monitor_left: int = 0,
    monitor_top: int = 0,
    scale: float = 1.0,
) -> Capture:
    image = Image.new("RGB", (width, height), "white")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return Capture(
        png_bytes=buffer.getvalue(),
        width=width,
        height=height,
        monitor_left=monitor_left,
        monitor_top=monitor_top,
        scale=scale,
    )


class _Provider:
    def __init__(self, result: OcrTextResult) -> None:
        self.result = result
        self.calls: list[tuple[Capture, tuple[int, int, int, int]]] = []

    def recognize_text(
        self,
        capture: Capture,
        rect: tuple[int, int, int, int],
    ) -> OcrTextResult:
        self.calls.append((capture, rect))
        return self.result


class OcrTextTests(unittest.TestCase):
    def test_rejects_strong_visible_text_mismatch(self) -> None:
        provider = _Provider(OcrTextResult(text="Cancel", available=True, elapsed_ms=12.5))

        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 80, 32),
            expected_text="Save",
            control_type="button",
            provider=provider,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_TEXT_MISMATCH_REASON)
        self.assertEqual(result.expected_text, "Save")
        self.assertEqual(result.recognized_text, "Cancel")
        self.assertEqual(result.elapsed_ms, 12.5)

    def test_allows_matching_and_fuzzy_ocr_text(self) -> None:
        provider = _Provider(OcrTextResult(text="Save chanyes", available=True))

        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 120, 32),
            expected_text="Save changes",
            control_type="button",
            provider=provider,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.recognized_text, "Save chanyes")

    def test_rejects_partial_ocr_match_for_multi_word_label(self) -> None:
        provider = _Provider(OcrTextResult(text="Save", available=True))

        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 120, 32),
            expected_text="Save changes",
            control_type="button",
            provider=provider,
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_PARTIAL_TEXT_REASON)
        self.assertEqual(result.recognized_text, "Save")

    def test_rejects_extra_ocr_text_for_short_exact_label(self) -> None:
        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 120, 32),
            expected_text="Save",
            control_type="button",
            provider=_Provider(OcrTextResult(text="Save as", available=True)),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_EXTRA_TEXT_REASON)
        self.assertEqual(result.recognized_text, "Save as")

    def test_rejects_extra_ocr_text_for_multi_word_label(self) -> None:
        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 160, 32),
            expected_text="Save changes",
            control_type="button",
            provider=_Provider(OcrTextResult(text="Save changes now", available=True)),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_EXTRA_TEXT_REASON)

    def test_rejects_extra_ocr_text_for_generic_menu_label(self) -> None:
        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 160, 28),
            expected_text="Open",
            control_type="menuitem",
            provider=_Provider(OcrTextResult(text="Open recent", available=True)),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_EXTRA_TEXT_REASON)

    def test_rejects_extra_ocr_text_for_retained_generic_menu_label(self) -> None:
        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 200, 28),
            expected_text="Open recent",
            control_type="menuitem",
            provider=_Provider(OcrTextResult(text="Open recent files", available=True)),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_EXTRA_TEXT_REASON)

    def test_allows_shortcut_hint_as_extra_ocr_text(self) -> None:
        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 120, 32),
            expected_text="Save",
            control_type="button",
            provider=_Provider(OcrTextResult(text="Save Ctrl S", available=True)),
        )

        self.assertTrue(result.accepted)
        self.assertEqual(result.recognized_text, "Save Ctrl S")

    def test_rejects_numeric_cell_shared_suffix_mismatch(self) -> None:
        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 100, 28),
            expected_text="$1,234.00",
            control_type="cell",
            provider=_Provider(OcrTextResult(text="$9,234.00", available=True)),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_TEXT_MISMATCH_REASON)

    def test_rejects_numeric_cell_partial_crop(self) -> None:
        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 100, 28),
            expected_text="$1,234.00",
            control_type="cell",
            provider=_Provider(OcrTextResult(text="234.00", available=True)),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_PARTIAL_TEXT_REASON)

    def test_checks_single_digit_cell_values(self) -> None:
        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 40, 24),
            expected_text="4",
            control_type="cell",
            provider=_Provider(OcrTextResult(text="5", available=True)),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_TEXT_MISMATCH_REASON)

    def test_menuitem_generic_label_still_verified(self) -> None:
        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 100, 28),
            expected_text="Open",
            control_type="menuitem",
            provider=_Provider(OcrTextResult(text="Delete", available=True)),
        )

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, OCR_TEXT_MISMATCH_REASON)

    def test_dotted_abbreviation_compact_compare(self) -> None:
        matching = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 60, 24),
            expected_text="U.S.",
            control_type="cell",
            provider=_Provider(OcrTextResult(text="US", available=True)),
        )
        mismatching = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 60, 24),
            expected_text="U.S.",
            control_type="cell",
            provider=_Provider(OcrTextResult(text="U.K.", available=True)),
        )

        self.assertTrue(matching.accepted)
        self.assertFalse(mismatching.accepted)
        self.assertEqual(mismatching.reason, OCR_TEXT_MISMATCH_REASON)

    def test_unavailable_ocr_is_inconclusive_but_available_blank_ocr_rejects(self) -> None:
        unavailable = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 120, 32),
            expected_text="Save changes",
            control_type="button",
            provider=_Provider(OcrTextResult(available=False, error="No OCR")),
        )
        blank = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 120, 32),
            expected_text="Save changes",
            control_type="button",
            provider=_Provider(OcrTextResult(text="", available=True)),
        )

        self.assertTrue(unavailable.accepted)
        self.assertFalse(unavailable.available)
        self.assertEqual(unavailable.error, "No OCR")
        self.assertFalse(blank.accepted)
        self.assertEqual(blank.reason, OCR_MISSING_TEXT_REASON)

    def test_symbol_only_button_labels_use_available_blank_ocr_gate(self) -> None:
        for label in ("+", "...", "\u00d7", "X"):
            with self.subTest(label=label):
                blank_provider = _Provider(OcrTextResult(text="", available=True))
                blank = verify_target_text(
                    capture=_capture(),
                    rect=(20, 20, 32, 32),
                    expected_text=label,
                    control_type="button",
                    provider=blank_provider,
                )
                matching_provider = _Provider(OcrTextResult(text=label, available=True))
                matching = verify_target_text(
                    capture=_capture(),
                    rect=(20, 20, 32, 32),
                    expected_text=label,
                    control_type="button",
                    provider=matching_provider,
                )

                self.assertEqual(len(blank_provider.calls), 1)
                self.assertFalse(blank.accepted)
                self.assertEqual(blank.reason, OCR_MISSING_TEXT_REASON)
                self.assertEqual(len(matching_provider.calls), 1)
                self.assertTrue(matching.accepted)

    def test_skips_small_checkbox_external_label_targets(self) -> None:
        provider = _Provider(OcrTextResult(text="Cancel", available=True))

        result = verify_target_text(
            capture=_capture(),
            rect=(20, 20, 18, 18),
            expected_text="Remember me",
            control_type="checkbox",
            provider=provider,
        )

        self.assertTrue(result.accepted)
        self.assertEqual(provider.calls, [])

    def test_expected_text_uses_visible_candidate_text_not_automation_id(self) -> None:
        target = TargetResolution(
            rect=(20, 20, 120, 32),
            confidence=0.9,
            source="target_id",
            matched_text="Save changes saveButton",
            target_id="save",
        )
        candidates = [
            ControlCandidate("save", "Save changes", "button", (20, 20, 120, 32), automation_id="saveButton"),
        ]

        self.assertEqual(expected_text_for_target(target, candidates), "Save changes")

    def test_expected_text_uses_nearby_label_for_state_only_checkbox_text(self) -> None:
        target = TargetResolution(
            rect=(20, 20, 48, 24),
            confidence=0.9,
            source="target_id",
            matched_text="Checked",
            target_id="terms",
        )
        candidates = [
            ControlCandidate("terms", "Checked", "checkbox", (20, 20, 48, 24)),
            ControlCandidate("terms_label", "Terms", "text", (72, 20, 80, 24)),
        ]

        self.assertEqual(expected_text_for_target(target, candidates), "Terms")
        evidence = expected_text_evidence_for_target(target, candidates)
        self.assertEqual(evidence.text, "Terms")
        self.assertEqual(evidence.rect, (20, 20, 132, 24))

    def test_expected_text_uses_nearby_label_for_empty_radio_text(self) -> None:
        target = TargetResolution(
            rect=(20, 20, 48, 24),
            confidence=0.9,
            source="target_id",
            matched_text="",
            target_id="weekly",
        )
        candidates = [
            ControlCandidate("weekly", "", "radiobutton", (20, 20, 48, 24)),
            ControlCandidate("weekly_label", "Weekly", "text", (72, 20, 80, 24)),
        ]

        self.assertEqual(expected_text_for_target(target, candidates), "Weekly")
        evidence = expected_text_evidence_for_target(target, candidates)
        self.assertEqual(evidence.text, "Weekly")
        self.assertEqual(evidence.rect, (20, 20, 132, 24))

    def test_expected_text_uses_nearby_label_for_blank_edit(self) -> None:
        target = TargetResolution(
            rect=(220, 100, 240, 32),
            confidence=0.9,
            source="target_id",
            matched_text="Email",
            target_id="email",
        )
        candidates = [
            ControlCandidate("email_label", "Email", "text", (120, 102, 80, 24)),
            ControlCandidate("email", "", "edit", (220, 100, 240, 32)),
        ]

        evidence = expected_text_evidence_for_target(target, candidates)

        self.assertEqual(evidence.text, "Email")
        self.assertEqual(evidence.rect, (120, 100, 340, 32))

    def test_expected_text_uses_above_label_for_blank_combobox(self) -> None:
        target = TargetResolution(
            rect=(220, 100, 240, 32),
            confidence=0.9,
            source="target_id",
            matched_text="Country",
            target_id="country",
        )
        candidates = [
            ControlCandidate("country_label", "Country", "text", (220, 68, 80, 24)),
            ControlCandidate("country", "", "combobox", (220, 100, 240, 32)),
        ]

        evidence = expected_text_evidence_for_target(target, candidates)

        self.assertEqual(evidence.text, "Country")
        self.assertEqual(evidence.rect, (220, 68, 240, 64))

    def test_expected_text_does_not_invent_label_for_unlabeled_blank_edit(self) -> None:
        target = TargetResolution(
            rect=(220, 100, 240, 32),
            confidence=0.9,
            source="target_id",
            matched_text="Email",
            target_id="email",
        )
        candidates = [
            ControlCandidate("email", "", "edit", (220, 100, 240, 32)),
        ]

        evidence = expected_text_evidence_for_target(target, candidates)

        self.assertEqual(evidence.text, "Email")
        self.assertEqual(evidence.rect, (220, 100, 240, 32))

    def test_expected_text_skips_unlabeled_state_only_checkbox_text(self) -> None:
        target = TargetResolution(
            rect=(20, 20, 64, 24),
            confidence=0.9,
            source="target_id",
            matched_text="Checked",
            target_id="terms",
        )
        candidates = [
            ControlCandidate("terms", "Checked", "checkbox", (20, 20, 64, 24)),
        ]

        evidence = expected_text_evidence_for_target(target, candidates)

        self.assertEqual(evidence.text, "")
        self.assertIsNone(evidence.rect)
        self.assertEqual(expected_text_for_target(target, candidates), "")

    def test_expected_text_skips_unlabeled_state_only_radio_text(self) -> None:
        target = TargetResolution(
            rect=(20, 20, 64, 24),
            confidence=0.9,
            source="target_id",
            matched_text="Selected",
            target_id="weekly",
        )
        candidates = [
            ControlCandidate("weekly", "Selected", "radiobutton", (20, 20, 64, 24)),
        ]

        evidence = expected_text_evidence_for_target(target, candidates)

        self.assertEqual(evidence.text, "")
        self.assertIsNone(evidence.rect)

    def test_expected_text_keeps_meaningful_checkbox_text(self) -> None:
        target = TargetResolution(
            rect=(20, 20, 160, 32),
            confidence=0.9,
            source="target_id",
            matched_text="Remember me",
            target_id="remember",
        )
        candidates = [
            ControlCandidate("remember", "Remember me", "checkbox", (20, 20, 160, 32)),
            ControlCandidate("nearby", "Other", "text", (184, 20, 80, 24)),
        ]

        self.assertEqual(expected_text_for_target(target, candidates), "Remember me")

    def test_crop_mapping_respects_monitor_offset_and_scale(self) -> None:
        capture = _capture(width=100, height=80, monitor_left=-100, monitor_top=50, scale=0.5)

        box = _screen_rect_to_image_box(capture, (-90, 70, 20, 10), padding_px=2)

        self.assertEqual(box, (4, 9, 16, 16))

    def test_windows_provider_import_failure_is_reported_unavailable(self) -> None:
        async def _missing_ocr(_path):
            raise ModuleNotFoundError("No module named 'winrt.windows.media.ocr'")

        provider = WindowsOcrTextProvider(timeout_sec=0.1)
        with patch("ocr_text._recognize_image_path", _missing_ocr):
            first = provider.recognize_text(_capture(), (20, 20, 80, 32))
            second = provider.recognize_text(_capture(), (20, 20, 80, 32))

        self.assertFalse(first.available)
        self.assertIn("winrt.windows.media.ocr", first.error)
        self.assertFalse(second.available)
        self.assertEqual(second.error, first.error)


if __name__ == "__main__":
    unittest.main()
