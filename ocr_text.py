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
    target_id = getattr(target, "target_id", "")
    if target_id:
        for candidate in candidates:
            if getattr(candidate, "id", "") == target_id:
                text = str(getattr(candidate, "text", "") or "").strip()
                if text:
                    return text
                break
    return str(getattr(target, "matched_text", "") or "").strip()


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

    expected_tokens = _meaningful_tokens(expected)
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
    recognized_tokens = _meaningful_tokens(recognized)
    if not recognized_tokens:
        return OcrTextVerification(
            accepted=True,
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
    tokens = _meaningful_tokens(expected)
    if not tokens:
        return False
    if not any(_token_has_signal(token) for token in tokens):
        return False
    if normalized_type in {"checkbox", "radiobutton"} and rect[2] < 48:
        return False
    return True


def _meaningful_tokens(text: str) -> set[str]:
    return {
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if token not in OCR_GENERIC_WORDS
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


def _is_strong_contradiction(expected: set[str], recognized: set[str]) -> bool:
    expected_signal = {token for token in expected if _token_has_signal(token)}
    recognized_signal = {token for token in recognized if _token_has_signal(token)}
    if not expected_signal or not recognized_signal:
        return False
    return True


def _token_has_signal(token: str) -> bool:
    return len(token) >= 2 and any(char.isalnum() for char in token)


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
