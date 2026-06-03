from __future__ import annotations

import asyncio
import io
import os
import re
import threading
import tempfile
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Protocol

from PIL import Image

from screen import Capture

OCR_TEXT_MISMATCH_REASON = "ocr text mismatch"
OCR_PARTIAL_TEXT_REASON = "ocr partial text match"
OCR_EXTRA_TEXT_REASON = "ocr extra text mismatch"
OCR_GENERIC_WORDS = frozenset(
    {
        "a",
        "an",
        "area",
        "button",
        "cell",
        "checkbox",
        "choose",
        "click",
        "column",
        "combobox",
        "control",
        "data",
        "dropdown",
        "field",
        "find",
        "focus",
        "grid",
        "header",
        "here",
        "highlighted",
        "hit",
        "icon",
        "input",
        "item",
        "list",
        "menu",
        "navigate",
        "open",
        "option",
        "press",
        "radio",
        "radiobutton",
        "row",
        "select",
        "tab",
        "table",
        "tap",
        "text",
        "the",
        "this",
        "visit",
    }
)
OCR_GENERIC_LABEL_EXCEPTIONS = frozenset({"cell", "open", "option"})
OCR_NUMERIC_STRICT_CONTROL_TYPES = frozenset(
    {"cell", "dataitem", "datagridcell", "edit", "gridcell", "row", "rowheader"}
)
OCR_STATE_CONTROL_TYPES = frozenset({"checkbox", "radiobutton"})
OCR_NEARBY_LABEL_CONTROL_TYPES = frozenset(
    {"combobox", "edit", "slider", "spinner"}
)
OCR_STATE_VALUE_WORDS = frozenset(
    {
        "checked",
        "disabled",
        "enabled",
        "false",
        "mixed",
        "off",
        "on",
        "selected",
        "true",
        "unchecked",
        "unselected",
    }
)
OCR_ALLOWED_EXTRA_TEXT_TOKENS = frozenset(
    {
        "alt",
        "backspace",
        "cmd",
        "command",
        "control",
        "ctrl",
        "del",
        "enter",
        "esc",
        "escape",
        "f1",
        "f2",
        "f3",
        "f4",
        "f5",
        "f6",
        "f7",
        "f8",
        "f9",
        "f10",
        "f11",
        "f12",
        "fn",
        "key",
        "keys",
        "meta",
        "return",
        "shift",
        "shortcut",
        "shortcuts",
        "space",
        "win",
        "windows",
    }
)
OCR_LABEL_CONTROL_TYPES = frozenset(
    {"header", "headeritem", "label", "static", "text"}
)
OCR_VERIFICATION_CONTROL_TYPES = frozenset(
    {
        "button",
        "cell",
        "checkbox",
        "combobox",
        "dataitem",
        "datagridcell",
        "edit",
        "gridcell",
        "headeritem",
        "hyperlink",
        "listitem",
        "menuitem",
        "radiobutton",
        "row",
        "rowheader",
        "splitbutton",
        "tabitem",
        "treeitem",
    }
)


@dataclass(frozen=True)
class OcrTextResult:
    text: str = ""
    available: bool = True
    error: str = ""
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class OcrTextVerification:
    accepted: bool
    reason: str = ""
    expected_text: str = ""
    recognized_text: str = ""
    available: bool = False
    error: str = ""
    elapsed_ms: float = 0.0


@dataclass(frozen=True)
class OcrTextEvidence:
    text: str = ""
    rect: tuple[int, int, int, int] | None = None


class OcrTextProvider(Protocol):
    def recognize_text(
        self,
        capture: Capture,
        rect: tuple[int, int, int, int],
    ) -> OcrTextResult:
        ...


class WindowsOcrTextProvider:
    def __init__(self, *, timeout_sec: float = 0.75) -> None:
        self._timeout_sec = timeout_sec
        self._disabled_reason = ""

    def recognize_text(
        self,
        capture: Capture,
        rect: tuple[int, int, int, int],
    ) -> OcrTextResult:
        started = time.monotonic()
        if self._disabled_reason:
            return OcrTextResult(
                available=False,
                error=self._disabled_reason,
                elapsed_ms=_elapsed_ms(started),
            )
        image_path: Path | None = None
        try:
            image_path = _write_target_crop(capture, rect)
            if image_path is None:
                return OcrTextResult(
                    available=False,
                    error="target crop unavailable",
                    elapsed_ms=_elapsed_ms(started),
                )
            text = _run_async(
                asyncio.wait_for(_recognize_image_path(image_path), timeout=self._timeout_sec)
            )
            return OcrTextResult(
                text=text.strip(),
                available=True,
                elapsed_ms=_elapsed_ms(started),
            )
        except Exception as exc:
            reason = str(exc) or exc.__class__.__name__
            if _should_disable_provider(reason):
                self._disabled_reason = reason
            return OcrTextResult(
                available=False,
                error=reason,
                elapsed_ms=_elapsed_ms(started),
            )
        finally:
            if image_path is not None:
                try:
                    image_path.unlink(missing_ok=True)
                except Exception:
                    pass


def default_ocr_text_provider() -> OcrTextProvider | None:
    value = os.environ.get("HELP_OCR_TEXT_VERIFY", "1").strip().lower()
    if value in {"0", "false", "no", "off"}:
        return None
    return WindowsOcrTextProvider()


def expected_text_for_target(target: object, candidates: list[object]) -> str:
    return expected_text_evidence_for_target(target, candidates).text


def expected_text_evidence_for_target(
    target: object,
    candidates: list[object],
) -> OcrTextEvidence:
    target_id = getattr(target, "target_id", "")
    if target_id:
        for candidate in candidates:
            if getattr(candidate, "id", "") == target_id:
                label = _state_control_visible_label_evidence(candidate, candidates)
                if label.text:
                    return label
                text = str(getattr(candidate, "text", "") or "").strip()
                if _candidate_has_state_only_text_without_label(candidate, text):
                    return OcrTextEvidence()
                if text:
                    return OcrTextEvidence(text=text, rect=_object_rect(candidate))
                label = _nearby_control_label_evidence(candidate, candidates)
                if label.text:
                    return label
                break
    return OcrTextEvidence(
        text=str(getattr(target, "matched_text", "") or "").strip(),
        rect=_object_rect(target),
    )


def _state_control_visible_label(candidate: object, candidates: list[object]) -> str:
    return _state_control_visible_label_evidence(candidate, candidates).text


def _candidate_has_state_only_text_without_label(candidate: object, text: str) -> bool:
    control_type = str(getattr(candidate, "control_type", "") or "").strip().lower()
    return control_type in OCR_STATE_CONTROL_TYPES and _state_control_text_is_state_only(text)


def _state_control_visible_label_evidence(
    candidate: object,
    candidates: list[object],
) -> OcrTextEvidence:
    control_type = str(getattr(candidate, "control_type", "") or "").strip().lower()
    if control_type not in OCR_STATE_CONTROL_TYPES:
        return OcrTextEvidence()
    text = str(getattr(candidate, "text", "") or "").strip()
    if text and not _state_control_text_is_state_only(text):
        return OcrTextEvidence()
    rect = _object_rect(candidate)
    if rect is None:
        return OcrTextEvidence()
    best_score = 0.0
    best_text = ""
    best_rect: tuple[int, int, int, int] | None = None
    for label in candidates:
        if getattr(label, "id", "") == getattr(candidate, "id", ""):
            continue
        label_type = str(getattr(label, "control_type", "") or "").strip().lower()
        if label_type not in OCR_LABEL_CONTROL_TYPES:
            continue
        label_text = str(getattr(label, "text", "") or "").strip()
        if not label_text or len(label_text.split()) > 8:
            continue
        label_rect = _object_rect(label)
        if label_rect is None:
            continue
        score = _state_label_score(rect, label_rect)
        if score > best_score:
            best_score = score
            best_text = label_text
            best_rect = _union_rect(rect, label_rect)
    if best_score < 0.5:
        return OcrTextEvidence()
    return OcrTextEvidence(text=best_text, rect=best_rect)


def _nearby_control_label_evidence(
    candidate: object,
    candidates: list[object],
) -> OcrTextEvidence:
    control_type = str(getattr(candidate, "control_type", "") or "").strip().lower()
    if control_type not in OCR_NEARBY_LABEL_CONTROL_TYPES:
        return OcrTextEvidence()
    rect = _object_rect(candidate)
    if rect is None:
        return OcrTextEvidence()
    best_score = 0.0
    best_text = ""
    best_rect: tuple[int, int, int, int] | None = None
    for label in candidates:
        if getattr(label, "id", "") == getattr(candidate, "id", ""):
            continue
        label_type = str(getattr(label, "control_type", "") or "").strip().lower()
        if label_type not in OCR_LABEL_CONTROL_TYPES:
            continue
        label_text = str(getattr(label, "text", "") or "").strip()
        if not label_text or len(label_text.split()) > 8:
            continue
        label_rect = _object_rect(label)
        if label_rect is None:
            continue
        score = _nearby_control_label_score(rect, label_rect)
        if score > best_score:
            best_score = score
            best_text = label_text
            best_rect = _union_rect(rect, label_rect)
    if best_score < 0.5:
        return OcrTextEvidence()
    return OcrTextEvidence(text=best_text, rect=best_rect)


def _state_control_text_is_state_only(text: str) -> bool:
    tokens = set(re.findall(r"[a-z0-9]+", (text or "").lower()))
    return bool(tokens) and tokens <= OCR_STATE_VALUE_WORDS


def _object_rect(item: object) -> tuple[int, int, int, int] | None:
    raw = getattr(item, "rect", None)
    try:
        x, y, width, height = raw
        rect = (int(x), int(y), int(width), int(height))
    except Exception:
        return None
    if rect[2] <= 0 or rect[3] <= 0:
        return None
    return rect


def _state_label_score(
    option_rect: tuple[int, int, int, int],
    label_rect: tuple[int, int, int, int],
) -> float:
    option_left, option_top, option_width, option_height = option_rect
    option_right = option_left + option_width
    option_bottom = option_top + option_height
    label_left, label_top, label_width, label_height = label_rect
    label_right = label_left + label_width
    label_bottom = label_top + label_height

    y_overlap = max(0, min(option_bottom, label_bottom) - max(option_top, label_top))
    y_ratio = y_overlap / max(1, min(option_height, label_height))
    right_gap = label_left - option_right
    if y_ratio >= 0.45 and -4 <= right_gap <= 240 and label_right >= option_right:
        return 0.75 + 0.25 * (1.0 - min(1.0, max(0, right_gap) / 240.0))
    return 0.0


def _nearby_control_label_score(
    control_rect: tuple[int, int, int, int],
    label_rect: tuple[int, int, int, int],
) -> float:
    control_left, control_top, control_width, control_height = control_rect
    control_right = control_left + control_width
    control_bottom = control_top + control_height
    control_center_y = control_top + control_height / 2.0
    label_left, label_top, label_width, label_height = label_rect
    label_right = label_left + label_width
    label_bottom = label_top + label_height
    label_center_y = label_top + label_height / 2.0

    y_overlap = max(0, min(control_bottom, label_bottom) - max(control_top, label_top))
    y_ratio = y_overlap / max(1, min(control_height, label_height))
    if y_ratio >= 0.45:
        left_gap = control_left - label_right
        if -4 <= left_gap <= 260 and label_left <= control_left:
            y_penalty = abs(control_center_y - label_center_y) / max(1.0, control_height)
            return 0.85 + 0.15 * (1.0 - min(1.0, y_penalty))
        right_gap = label_left - control_right
        if 0 <= right_gap <= 180 and label_right >= control_right:
            y_penalty = abs(control_center_y - label_center_y) / max(1.0, control_height)
            return 0.65 + 0.15 * (1.0 - min(1.0, y_penalty))

    if label_bottom <= control_top:
        vertical_gap = control_top - label_bottom
        horizontal_overlap = max(0, min(control_right, label_right) - max(control_left, label_left))
        left_aligned = abs(label_left - control_left) <= max(16, min(control_width, label_width) * 0.30)
        center_aligned = abs(
            (label_left + label_right) / 2.0 - (control_left + control_right) / 2.0
        ) <= max(32, min(control_width, label_width) * 0.45)
        if vertical_gap <= max(36, control_height * 1.2) and (
            horizontal_overlap > 0 or left_aligned or center_aligned
        ):
            return 0.75 - min(0.25, vertical_gap / max(1.0, control_height * 3.0))
    return 0.0


def _union_rect(
    first: tuple[int, int, int, int],
    second: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    left = min(first[0], second[0])
    top = min(first[1], second[1])
    right = max(first[0] + first[2], second[0] + second[2])
    bottom = max(first[1] + first[3], second[1] + second[3])
    return (left, top, right - left, bottom - top)


def verify_target_text(
    *,
    capture: Capture,
    rect: tuple[int, int, int, int],
    expected_text: str,
    control_type: str = "",
    provider: OcrTextProvider | None = None,
) -> OcrTextVerification:
    expected = (expected_text or "").strip()
    if not expected:
        return OcrTextVerification(accepted=True)
    if not _should_check_ocr(expected, control_type, rect):
        return OcrTextVerification(accepted=True, expected_text=expected)

    keep_generic_label = _keep_generic_label_tokens(control_type)
    expected_tokens = _meaningful_tokens(
        expected,
        keep_generic_label=keep_generic_label,
    )
    if not expected_tokens:
        return OcrTextVerification(accepted=True, expected_text=expected)
    provider = provider if provider is not None else default_ocr_text_provider()
    if provider is None:
        return OcrTextVerification(accepted=True, expected_text=expected)

    result = provider.recognize_text(capture, rect)
    recognized = (result.text or "").strip()
    if not result.available:
        return OcrTextVerification(
            accepted=True,
            expected_text=expected,
            recognized_text=recognized,
            available=False,
            error=result.error,
            elapsed_ms=result.elapsed_ms,
        )
    numeric_reason = _numeric_text_rejection_reason(expected, recognized, control_type)
    if numeric_reason:
        return OcrTextVerification(
            accepted=False,
            reason=numeric_reason,
            expected_text=expected,
            recognized_text=recognized,
            available=True,
            elapsed_ms=result.elapsed_ms,
        )
    if _numeric_text_matches(expected, recognized, control_type):
        return OcrTextVerification(
            accepted=True,
            expected_text=expected,
            recognized_text=recognized,
            available=True,
            elapsed_ms=result.elapsed_ms,
        )
    compact_reason = _compact_abbreviation_rejection_reason(expected, recognized)
    if compact_reason:
        return OcrTextVerification(
            accepted=False,
            reason=compact_reason,
            expected_text=expected,
            recognized_text=recognized,
            available=True,
            elapsed_ms=result.elapsed_ms,
        )
    if _compact_abbreviation_matches(expected, recognized):
        return OcrTextVerification(
            accepted=True,
            expected_text=expected,
            recognized_text=recognized,
            available=True,
            elapsed_ms=result.elapsed_ms,
        )

    recognized_tokens = _meaningful_tokens(
        recognized,
        keep_generic_label=keep_generic_label,
    )
    if not recognized_tokens:
        return OcrTextVerification(
            accepted=True,
            expected_text=expected,
            recognized_text=recognized,
            available=True,
            elapsed_ms=result.elapsed_ms,
        )
    if _is_partial_text_match(expected_tokens, recognized_tokens):
        return OcrTextVerification(
            accepted=False,
            reason=OCR_PARTIAL_TEXT_REASON,
            expected_text=expected,
            recognized_text=recognized,
            available=True,
            elapsed_ms=result.elapsed_ms,
        )
    if _has_extra_identity_text(expected_tokens, recognized_tokens):
        return OcrTextVerification(
            accepted=False,
            reason=OCR_EXTRA_TEXT_REASON,
            expected_text=expected,
            recognized_text=recognized,
            available=True,
            elapsed_ms=result.elapsed_ms,
        )
    if _tokens_match(expected_tokens, recognized_tokens):
        return OcrTextVerification(
            accepted=True,
            expected_text=expected,
            recognized_text=recognized,
            available=True,
            elapsed_ms=result.elapsed_ms,
        )
    if not _is_strong_contradiction(expected_tokens, recognized_tokens):
        return OcrTextVerification(
            accepted=True,
            expected_text=expected,
            recognized_text=recognized,
            available=True,
            elapsed_ms=result.elapsed_ms,
        )
    return OcrTextVerification(
        accepted=False,
        reason=OCR_TEXT_MISMATCH_REASON,
        expected_text=expected,
        recognized_text=recognized,
        available=True,
        elapsed_ms=result.elapsed_ms,
    )


def _should_check_ocr(
    expected: str,
    control_type: str,
    rect: tuple[int, int, int, int],
) -> bool:
    normalized_type = (control_type or "").strip().lower()
    if normalized_type and normalized_type not in OCR_VERIFICATION_CONTROL_TYPES:
        return False
    if (
        normalized_type in OCR_NUMERIC_STRICT_CONTROL_TYPES
        and _compact_digits(expected)
    ):
        return True
    if _looks_like_compact_abbreviation(expected):
        return True
    tokens = _meaningful_tokens(
        expected,
        keep_generic_label=_keep_generic_label_tokens(control_type),
    )
    if not tokens:
        return False
    if not any(_token_has_signal(token) for token in tokens):
        return False
    if normalized_type in {"checkbox", "radiobutton"} and rect[2] < 48:
        return False
    return True


def _meaningful_tokens(text: str, *, keep_generic_label: bool = False) -> set[str]:
    tokens = {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
    }
    filtered = {token for token in tokens if token not in OCR_GENERIC_WORDS}
    if keep_generic_label:
        filtered |= {token for token in tokens if token in OCR_GENERIC_LABEL_EXCEPTIONS}
    if filtered:
        return filtered
    if keep_generic_label and tokens and tokens <= OCR_GENERIC_LABEL_EXCEPTIONS:
        return tokens
    return set()


def _keep_generic_label_tokens(control_type: str) -> bool:
    return (control_type or "").strip().lower() in {
        "button",
        "cell",
        "datagridcell",
        "gridcell",
        "menuitem",
    }


def _tokens_match(expected: set[str], recognized: set[str]) -> bool:
    if expected & recognized:
        return True
    return any(
        _similar_token(expected_token, recognized_token)
        for expected_token in expected
        for recognized_token in recognized
    )


def _similar_token(first: str, second: str) -> bool:
    if min(len(first), len(second)) < 4:
        return False
    return SequenceMatcher(None, first, second).ratio() >= 0.74


def _is_partial_text_match(expected: set[str], recognized: set[str]) -> bool:
    expected_signal = {token for token in expected if _token_has_signal(token)}
    recognized_signal = {token for token in recognized if _token_has_signal(token)}
    if len(expected_signal) < 2 or not recognized_signal:
        return False
    covered = {
        expected_token
        for expected_token in expected_signal
        if any(
            expected_token == recognized_token
            or _similar_token(expected_token, recognized_token)
            for recognized_token in recognized_signal
        )
    }
    return bool(covered) and len(covered) < len(expected_signal)


def _has_extra_identity_text(expected: set[str], recognized: set[str]) -> bool:
    expected_signal = {token for token in expected if _token_has_signal(token)}
    recognized_signal = {token for token in recognized if _token_has_signal(token)}
    if not expected_signal or not recognized_signal:
        return False
    if not _all_signal_tokens_covered(expected_signal, recognized_signal):
        return False
    extra = {
        recognized_token
        for recognized_token in recognized_signal
        if not _signal_token_matches_any(recognized_token, expected_signal)
    }
    return any(not _is_allowed_extra_text_token(token) for token in extra)


def _all_signal_tokens_covered(expected_signal: set[str], recognized_signal: set[str]) -> bool:
    return all(
        _signal_token_matches_any(expected_token, recognized_signal)
        for expected_token in expected_signal
    )


def _signal_token_matches_any(token: str, candidates: set[str]) -> bool:
    return any(token == candidate or _similar_token(token, candidate) for candidate in candidates)


def _is_allowed_extra_text_token(token: str) -> bool:
    if token in OCR_ALLOWED_EXTRA_TEXT_TOKENS:
        return True
    return bool(re.fullmatch(r"f(?:1[0-9]|2[0-4]|[1-9])", token))


def _is_strong_contradiction(expected: set[str], recognized: set[str]) -> bool:
    expected_signal = {token for token in expected if _token_has_signal(token)}
    recognized_signal = {token for token in recognized if _token_has_signal(token)}
    if not expected_signal or not recognized_signal:
        return False
    return True


def _token_has_signal(token: str) -> bool:
    return len(token) >= 2 and any(char.isalnum() for char in token)


def _numeric_text_rejection_reason(
    expected: str,
    recognized: str,
    control_type: str,
) -> str:
    if (control_type or "").strip().lower() not in OCR_NUMERIC_STRICT_CONTROL_TYPES:
        return ""
    expected_digits = _compact_digits(expected)
    recognized_digits = _compact_digits(recognized)
    if not expected_digits or not recognized_digits or expected_digits == recognized_digits:
        return ""
    if expected_digits in recognized_digits or recognized_digits in expected_digits:
        return OCR_PARTIAL_TEXT_REASON
    return OCR_TEXT_MISMATCH_REASON


def _numeric_text_matches(expected: str, recognized: str, control_type: str) -> bool:
    if (control_type or "").strip().lower() not in OCR_NUMERIC_STRICT_CONTROL_TYPES:
        return False
    expected_digits = _compact_digits(expected)
    recognized_digits = _compact_digits(recognized)
    return bool(expected_digits and recognized_digits and expected_digits == recognized_digits)


def _compact_digits(text: str) -> str:
    return "".join(re.findall(r"\d+", text or ""))


def _compact_abbreviation_rejection_reason(expected: str, recognized: str) -> str:
    if not _looks_like_compact_abbreviation(expected):
        return ""
    expected_compact = _compact_alnum(expected)
    recognized_compact = _compact_alnum(recognized)
    if not expected_compact or not recognized_compact or expected_compact == recognized_compact:
        return ""
    return OCR_TEXT_MISMATCH_REASON


def _compact_abbreviation_matches(expected: str, recognized: str) -> bool:
    if not _looks_like_compact_abbreviation(expected):
        return False
    expected_compact = _compact_alnum(expected)
    recognized_compact = _compact_alnum(recognized)
    return bool(expected_compact and recognized_compact and expected_compact == recognized_compact)


def _looks_like_compact_abbreviation(text: str) -> bool:
    compact = _compact_alnum(text)
    return "." in (text or "") and 2 <= len(compact) <= 4


def _compact_alnum(text: str) -> str:
    return "".join(re.findall(r"[a-z0-9]+", (text or "").lower()))


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000, 2)


def _should_disable_provider(reason: str) -> bool:
    lowered = reason.lower()
    return "no module named" in lowered or "class not registered" in lowered


def _write_target_crop(
    capture: Capture,
    rect: tuple[int, int, int, int],
) -> Path | None:
    try:
        image = Image.open(io.BytesIO(capture.png_bytes)).convert("RGB")
        image.load()
    except Exception:
        return None
    box = _screen_rect_to_image_box(capture, rect, padding_px=6)
    clipped = _clip_box(box, image.size)
    if clipped is None:
        return None
    crop = image.crop(clipped)
    if crop.width <= 0 or crop.height <= 0:
        return None
    if crop.width < 480 and crop.height < 180:
        crop = crop.resize((crop.width * 2, crop.height * 2), Image.Resampling.LANCZOS)
    handle, path = tempfile.mkstemp(prefix="helper_ocr_", suffix=".png")
    os.close(handle)
    output = Path(path)
    crop.save(output, format="PNG")
    return output


def _screen_rect_to_image_box(
    capture: Capture,
    rect: tuple[int, int, int, int],
    *,
    padding_px: int = 0,
) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    scale = capture.scale
    left = int((x - capture.monitor_left - padding_px) * scale)
    top = int((y - capture.monitor_top - padding_px) * scale)
    right = int((x - capture.monitor_left + width + padding_px) * scale)
    bottom = int((y - capture.monitor_top + height + padding_px) * scale)
    return (left, top, max(left + 1, right), max(top + 1, bottom))


def _clip_box(
    box: tuple[int, int, int, int],
    size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    width, height = size
    left, top, right, bottom = box
    clipped = (max(0, left), max(0, top), min(width, right), min(height, bottom))
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        return None
    return clipped


async def _recognize_image_path(path: Path) -> str:
    import winrt.windows.foundation.collections as _foundation_collections  # noqa: F401
    import winrt.windows.globalization as _globalization  # noqa: F401
    from winrt.windows.graphics.imaging import BitmapDecoder
    from winrt.windows.media.ocr import OcrEngine
    from winrt.windows.storage import FileAccessMode, StorageFile

    file = await StorageFile.get_file_from_path_async(str(path))
    stream = await file.open_async(FileAccessMode.READ)
    decoder = await BitmapDecoder.create_async(stream)
    bitmap = await decoder.get_software_bitmap_async()
    engine = OcrEngine.try_create_from_user_profile_languages()
    if engine is None:
        return ""
    result = await engine.recognize_async(bitmap)
    return result.text or ""


def _run_async(coro):
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: list[object] = []
    failure: list[BaseException] = []

    def _runner() -> None:
        try:
            result.append(asyncio.run(coro))
        except BaseException as exc:
            failure.append(exc)

    thread = threading.Thread(target=_runner, daemon=True)
    thread.start()
    thread.join()
    if failure:
        raise failure[0]
    return result[0] if result else None
