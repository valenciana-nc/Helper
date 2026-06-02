from __future__ import annotations

import io
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from PIL import Image, ImageFilter, ImageStat

from screen import Capture

MIN_VISIBLE_FRACTION = 0.35
MODEL_EMPTY_VISUAL_FLOOR = 0.035
MODEL_BOUNDARY_ACTIVITY_FLOOR = 0.10
MODEL_NOISY_VISUAL_CEILING = 0.40
MODEL_NOISY_BOUNDARY_FLOOR = 0.25
MODEL_COMPOUND_SEPARATOR_FLOOR = 0.65
MODEL_COMPOUND_MIN_SEPARATOR_GROUPS = 2
MODEL_BOUNDARY_ALIGNMENT_FLOOR = 0.08
CANDIDATE_EMPTY_VISUAL_FLOOR = 0.012
CANDIDATE_COMPOUND_MIN_AREA = 3000
CANDIDATE_COMPOUND_MIN_WIDTH = 120
CANDIDATE_STRICT_QUALITY_CONTROL_TYPES = frozenset(
    {
        "dataitem",
        "datagrid",
        "grid",
        "group",
        "list",
        "listitem",
        "pane",
        "table",
        "toolbar",
        "treeitem",
        "window",
    }
)
CANDIDATE_BOUNDARY_ALIGNMENT_CONTROL_TYPES = frozenset(
    {
        "button",
        "checkbox",
        "combobox",
        "edit",
        "menuitem",
        "radiobutton",
        "slider",
        "spinner",
        "splitbutton",
        "tabitem",
    }
)
MAX_TARGET_AREA_FRACTION = 0.25
CANDIDATE_COMPOUND_ACTION_WORDS = frozenset(
    {
        "accept",
        "activate",
        "add",
        "apply",
        "approve",
        "archive",
        "attach",
        "cancel",
        "check",
        "clear",
        "close",
        "complete",
        "confirm",
        "copy",
        "create",
        "deactivate",
        "decline",
        "delete",
        "disable",
        "dismiss",
        "download",
        "edit",
        "enable",
        "export",
        "filter",
        "finish",
        "invite",
        "lock",
        "pay",
        "publish",
        "refund",
        "reject",
        "remove",
        "reset",
        "resolve",
        "restore",
        "revoke",
        "save",
        "send",
        "share",
        "sort",
        "start",
        "stop",
        "submit",
        "sync",
        "toggle",
        "trash",
        "uncheck",
        "unlock",
        "update",
        "upload",
    }
)


@dataclass(frozen=True)
class TargetQuality:
    accepted: bool
    reason: str = ""
    visible_fraction: float = 1.0
    visual_activity: float = 0.0
    boundary_activity: float = 0.0
    target_area_fraction: float = 0.0


def evaluate_target_quality(
    *,
    capture: Capture,
    rect: tuple[int, int, int, int],
    source: str,
    confidence: float,
    instruction: str = "",
    target_control_type: str = "",
) -> TargetQuality:
    image_rect = _screen_to_image_rect(capture, rect)
    clipped = _clip_rect(image_rect, (0, 0, capture.width, capture.height))
    if clipped is None:
        return TargetQuality(
            accepted=False,
            reason="target outside capture",
            visible_fraction=0.0,
        )

    visible_area = clipped[2] * clipped[3]
    rect_area = max(1, image_rect[2] * image_rect[3])
    visible_fraction = visible_area / rect_area
    target_area_fraction = rect_area / max(1, capture.width * capture.height)
    if visible_fraction < MIN_VISIBLE_FRACTION:
        return TargetQuality(
            accepted=False,
            reason="target mostly outside capture",
            visible_fraction=visible_fraction,
            target_area_fraction=target_area_fraction,
        )

    if target_area_fraction > MAX_TARGET_AREA_FRACTION:
        return TargetQuality(
            accepted=False,
            reason="target too large",
            visible_fraction=visible_fraction,
            target_area_fraction=target_area_fraction,
        )

    visual_activity, boundary_activity = _visual_activity(capture.png_bytes, clipped)
    if (
        source != "model"
        and visual_activity < CANDIDATE_EMPTY_VISUAL_FLOOR
        and boundary_activity < CANDIDATE_EMPTY_VISUAL_FLOOR
    ):
        return TargetQuality(
            accepted=False,
            reason="target appears visually empty",
            visible_fraction=visible_fraction,
            visual_activity=visual_activity,
            boundary_activity=boundary_activity,
            target_area_fraction=target_area_fraction,
        )
    if (
        source != "model"
        and _candidate_compound_action_request(instruction)
        and _candidate_compound_rect_large_enough(image_rect)
        and _has_compound_control_separators(capture.png_bytes, clipped)
    ):
        return TargetQuality(
            accepted=False,
            reason="target appears to contain multiple controls",
            visible_fraction=visible_fraction,
            visual_activity=visual_activity,
            boundary_activity=boundary_activity,
            target_area_fraction=target_area_fraction,
        )
    if (
        _strict_candidate_quality_target(source, target_control_type, image_rect)
        and visual_activity > MODEL_NOISY_VISUAL_CEILING
        and boundary_activity > MODEL_NOISY_BOUNDARY_FLOOR
    ):
        return TargetQuality(
            accepted=False,
            reason="target appears visually noisy",
            visible_fraction=visible_fraction,
            visual_activity=visual_activity,
            boundary_activity=boundary_activity,
            target_area_fraction=target_area_fraction,
        )
    if (
        _candidate_boundary_alignment_target(source, target_control_type)
        and boundary_activity >= MODEL_BOUNDARY_ACTIVITY_FLOOR
        and not _model_boundary_aligned(
            capture.png_bytes,
            clipped,
            require_all_sides=not _candidate_allows_edge_flush_boundary(
                source,
                target_control_type,
                instruction,
            ),
        )
    ):
        return TargetQuality(
            accepted=False,
            reason="target boundary misaligned",
            visible_fraction=visible_fraction,
            visual_activity=visual_activity,
            boundary_activity=boundary_activity,
            target_area_fraction=target_area_fraction,
        )
    if source == "model":
        if visual_activity < MODEL_EMPTY_VISUAL_FLOOR:
            return TargetQuality(
                accepted=False,
                reason="target appears visually empty",
                visible_fraction=visible_fraction,
                visual_activity=visual_activity,
                boundary_activity=boundary_activity,
                target_area_fraction=target_area_fraction,
            )
        if boundary_activity < MODEL_BOUNDARY_ACTIVITY_FLOOR:
            return TargetQuality(
                accepted=False,
                reason="target lacks visible control boundary",
                visible_fraction=visible_fraction,
                visual_activity=visual_activity,
                boundary_activity=boundary_activity,
                target_area_fraction=target_area_fraction,
            )
        if (
            visual_activity > MODEL_NOISY_VISUAL_CEILING
            and boundary_activity > MODEL_NOISY_BOUNDARY_FLOOR
        ):
            return TargetQuality(
                accepted=False,
                reason="target appears visually noisy",
                visible_fraction=visible_fraction,
                visual_activity=visual_activity,
                boundary_activity=boundary_activity,
                target_area_fraction=target_area_fraction,
            )
        if not _model_boundary_aligned(capture.png_bytes, clipped, require_all_sides=True):
            return TargetQuality(
                accepted=False,
                reason="target boundary misaligned",
                visible_fraction=visible_fraction,
                visual_activity=visual_activity,
                boundary_activity=boundary_activity,
                target_area_fraction=target_area_fraction,
            )
        if _has_compound_control_separators(capture.png_bytes, clipped, min_groups=1):
            return TargetQuality(
                accepted=False,
                reason="target appears to contain multiple controls",
                visible_fraction=visible_fraction,
                visual_activity=visual_activity,
                boundary_activity=boundary_activity,
                target_area_fraction=target_area_fraction,
            )

    return TargetQuality(
        accepted=True,
        visible_fraction=visible_fraction,
        visual_activity=visual_activity,
        boundary_activity=boundary_activity,
        target_area_fraction=target_area_fraction,
    )


def _candidate_compound_action_request(instruction: str) -> bool:
    words = set(re.findall(r"[a-z0-9]+", (instruction or "").lower()))
    return bool(words & CANDIDATE_COMPOUND_ACTION_WORDS)


def _candidate_compound_rect_large_enough(rect: tuple[int, int, int, int]) -> bool:
    _x, _y, width, height = rect
    return width >= CANDIDATE_COMPOUND_MIN_WIDTH and width * height >= CANDIDATE_COMPOUND_MIN_AREA


def _strict_candidate_quality_target(
    source: str,
    target_control_type: str,
    rect: tuple[int, int, int, int],
) -> bool:
    if source == "model":
        return False
    if target_control_type.lower() not in CANDIDATE_STRICT_QUALITY_CONTROL_TYPES:
        return False
    return _candidate_compound_rect_large_enough(rect)


def _candidate_boundary_alignment_target(source: str, target_control_type: str) -> bool:
    if source == "model":
        return False
    return target_control_type.lower() in CANDIDATE_BOUNDARY_ALIGNMENT_CONTROL_TYPES


def _candidate_allows_edge_flush_boundary(
    source: str,
    target_control_type: str,
    instruction: str,
) -> bool:
    if source == "model":
        return False
    if target_control_type.lower() != "button":
        return False
    words = set(re.findall(r"[a-z0-9]+", (instruction or "").lower()))
    return bool(words & {"clock", "date", "time"})


def _screen_to_image_rect(
    capture: Capture,
    rect: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    left = int((x - capture.monitor_left) * capture.scale)
    top = int((y - capture.monitor_top) * capture.scale)
    scaled_width = max(1, int(width * capture.scale))
    scaled_height = max(1, int(height * capture.scale))
    return (left, top, scaled_width, scaled_height)


def _clip_rect(
    rect: tuple[int, int, int, int],
    bounds: tuple[int, int, int, int],
) -> tuple[int, int, int, int] | None:
    ax1, ay1, aw, ah = rect
    bx1, by1, bw, bh = bounds
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    if ix2 <= ix1 or iy2 <= iy1:
        return None
    return (ix1, iy1, ix2 - ix1, iy2 - iy1)


def _visual_activity(png_bytes: bytes, rect: tuple[int, int, int, int]) -> tuple[float, float]:
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            x, y, width, height = rect
            crop = img.convert("L").crop((x, y, x + width, y + height))
            if crop.width <= 0 or crop.height <= 0:
                return 0.0, 0.0
            stats = ImageStat.Stat(crop)
            contrast = (stats.stddev[0] if stats.stddev else 0.0) / 255.0
            edges = crop.filter(ImageFilter.FIND_EDGES)
            if edges.width > 2 and edges.height > 2:
                edges = edges.crop((1, 1, edges.width - 1, edges.height - 1))
                edge_mean = ImageStat.Stat(edges).mean[0] / 255.0
            else:
                edge_mean = 0.0
            boundary_activity = _boundary_activity(crop)
            return min(1.0, 0.65 * contrast + 0.35 * edge_mean), boundary_activity
    except Exception:
        return 0.0, 0.0


def _boundary_activity(crop: Image.Image) -> float:
    width, height = crop.size
    if width <= 0 or height <= 0:
        return 0.0
    band = max(1, min(3, width // 6, height // 6))
    pixels = crop.load()
    values: list[int] = []
    for y in range(height):
        for x in range(width):
            if x < band or y < band or x >= width - band or y >= height - band:
                values.append(int(pixels[x, y]))
    if not values:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return min(1.0, (variance ** 0.5) / 255.0)


def _model_boundary_aligned(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
    *,
    require_all_sides: bool = False,
) -> bool:
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            image = img.convert("L")
            scores = _boundary_crossing_scores(image, rect)
            if len(scores) < 4:
                return not require_all_sides
            return all(score >= MODEL_BOUNDARY_ALIGNMENT_FLOOR for score in scores)
    except Exception:
        return True


def _boundary_crossing_scores(
    image: Image.Image,
    rect: tuple[int, int, int, int],
) -> list[float]:
    x, y, width, height = rect
    if width < 8 or height < 8:
        return []
    x2 = x + width
    y2 = y + height
    image_width, image_height = image.size
    band = max(1, min(3, width // 12, height // 12))
    margin = max(2, min(8, width // 8, height // 4))
    pixels = image.load()
    scores: list[float] = []

    if x - band >= 0 and y + margin < y2 - margin:
        scores.append(
            _average_boundary_difference(
                pixels,
                (
                    (x - 1 - offset, row, x + offset, row)
                    for row in range(y + margin, y2 - margin)
                    for offset in range(band)
                ),
            )
        )
    if x2 + band <= image_width and y + margin < y2 - margin:
        scores.append(
            _average_boundary_difference(
                pixels,
                (
                    (x2 + offset, row, x2 - 1 - offset, row)
                    for row in range(y + margin, y2 - margin)
                    for offset in range(band)
                ),
            )
        )
    if y - band >= 0 and x + margin < x2 - margin:
        scores.append(
            _average_boundary_difference(
                pixels,
                (
                    (col, y - 1 - offset, col, y + offset)
                    for col in range(x + margin, x2 - margin)
                    for offset in range(band)
                ),
            )
        )
    if y2 + band <= image_height and x + margin < x2 - margin:
        scores.append(
            _average_boundary_difference(
                pixels,
                (
                    (col, y2 + offset, col, y2 - 1 - offset)
                    for col in range(x + margin, x2 - margin)
                    for offset in range(band)
                ),
            )
        )
    return scores


def _average_boundary_difference(
    pixels: Any,
    pairs: Iterable[tuple[int, int, int, int]],
) -> float:
    total = 0
    count = 0
    for x1, y1, x2, y2 in pairs:
        total += abs(int(pixels[x1, y1]) - int(pixels[x2, y2]))
        count += 1
    if count == 0:
        return 0.0
    return total / (count * 255)


def _has_compound_control_separators(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
    *,
    min_groups: int = MODEL_COMPOUND_MIN_SEPARATOR_GROUPS,
) -> bool:
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            x, y, width, height = rect
            crop = img.convert("L").crop((x, y, x + width, y + height))
            if crop.width < 24 or crop.height < 16:
                return False
            edges = crop.filter(ImageFilter.FIND_EDGES)
            return (
                _strong_internal_separator_groups(edges, vertical=True)
                >= min_groups
                or _strong_internal_separator_groups(edges, vertical=False)
                >= min_groups
            )
    except Exception:
        return False


def _strong_internal_separator_groups(edges: Image.Image, *, vertical: bool) -> int:
    width, height = edges.size
    band = max(2, min(5, width // 12, height // 12))
    if width <= band * 2 or height <= band * 2:
        return 0
    pixels = edges.load()
    positions = range(band, width - band) if vertical else range(band, height - band)
    line_length = (height - band * 2) if vertical else (width - band * 2)
    groups = 0
    last_strong: int | None = None
    for position in positions:
        strong = 0
        for cross in range(band, (height if vertical else width) - band):
            value = int(pixels[position, cross] if vertical else pixels[cross, position])
            if value > 32:
                strong += 1
        if strong / max(1, line_length) < MODEL_COMPOUND_SEPARATOR_FLOOR:
            continue
        if last_strong is None or position - last_strong > 2:
            groups += 1
        last_strong = position
    return groups
