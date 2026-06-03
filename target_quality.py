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
CANDIDATE_SEGMENTED_SEPARATOR_FLOOR = 0.85
CANDIDATE_INNER_CONTROL_EDGE_FLOOR = 0.40
CANDIDATE_INNER_ROW_EDGE_FLOOR = 0.70
CANDIDATE_ENCLOSING_EDGE_FLOOR = 0.35
CANDIDATE_COMPOUND_MIN_AREA = 3000
CANDIDATE_COMPOUND_MIN_WIDTH = 120
CANDIDATE_ACTION_CONTAINER_CONTROL_TYPES = frozenset(
    {
        "dataitem",
        "group",
        "listitem",
        "pane",
        "row",
        "tableitem",
        "treeitem",
    }
)
CANDIDATE_ROW_CONTAINER_CONTROL_TYPES = frozenset(
    {"dataitem", "listitem", "row", "tableitem", "treeitem"}
)
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
        "option",
        "radiobutton",
        "slider",
        "spinner",
        "splitbutton",
        "tabitem",
    }
)
CANDIDATE_LEAF_ACTION_CONTROL_TYPES = frozenset(
    {"button", "hyperlink", "menuitem", "option", "splitbutton", "tabitem"}
)
SELECTION_ROW_CONTROL_TYPES = frozenset({"checkbox", "option", "radiobutton"})
TABULAR_CELL_CONTROL_TYPES = frozenset(
    {"cell", "datagridcell", "gridcell", "headeritem", "rowheader"}
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
        "back",
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
        "forward",
        "invite",
        "lock",
        "next",
        "pay",
        "previous",
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
        and _candidate_multiple_selection_indicators(
            capture.png_bytes,
            clipped,
            target_control_type,
        )
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
        source != "model"
        and _candidate_tabular_cell_target(target_control_type)
        and boundary_activity >= MODEL_BOUNDARY_ACTIVITY_FLOOR
        and not _model_boundary_aligned(capture.png_bytes, clipped, require_all_sides=True)
    ):
        return TargetQuality(
            accepted=False,
            reason="target boundary misaligned",
            visible_fraction=visible_fraction,
            visual_activity=visual_activity,
            boundary_activity=boundary_activity,
            target_area_fraction=target_area_fraction,
        )
    if (
        source != "model"
        and _candidate_tabular_cell_target(target_control_type)
        and boundary_activity < MODEL_BOUNDARY_ACTIVITY_FLOOR
        and visual_activity >= CANDIDATE_EMPTY_VISUAL_FLOOR
        and not _model_boundary_aligned(capture.png_bytes, clipped, require_all_sides=True)
        and _has_visible_enclosing_boundary_outside(capture.png_bytes, clipped)
    ):
        return TargetQuality(
            accepted=False,
            reason="target boundary misaligned",
            visible_fraction=visible_fraction,
            visual_activity=visual_activity,
            boundary_activity=boundary_activity,
            target_area_fraction=target_area_fraction,
        )
    if (
        source != "model"
        and _candidate_tabular_cell_target(target_control_type)
        and _has_tabular_cell_internal_separators(capture.png_bytes, clipped)
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
        source != "model"
        and _candidate_compound_action_request(instruction)
        and _candidate_compound_rect_large_enough(image_rect)
        and (
            _has_compound_control_vertical_separators(capture.png_bytes, clipped)
            or _has_segmented_control_separator(capture.png_bytes, clipped)
        )
        and not _candidate_single_selection_row(
            capture.png_bytes,
            clipped,
            target_control_type,
        )
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
        source != "model"
        and _candidate_action_container_target(target_control_type, image_rect)
        and _candidate_compound_action_request(instruction)
        and _has_internal_control_vertical_edges(capture.png_bytes, clipped)
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
        source != "model"
        and _candidate_container_boundary_alignment_target(target_control_type)
        and boundary_activity >= MODEL_BOUNDARY_ACTIVITY_FLOOR
        and not _model_boundary_aligned(capture.png_bytes, clipped, require_all_sides=True)
    ):
        return TargetQuality(
            accepted=False,
            reason="target boundary misaligned",
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
        source != "model"
        and _candidate_row_container_target(target_control_type, image_rect)
        and _has_internal_control_horizontal_edges(capture.png_bytes, clipped)
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
    if (
        _candidate_boundary_alignment_target(source, target_control_type)
        and boundary_activity < MODEL_BOUNDARY_ACTIVITY_FLOOR
        and visual_activity >= CANDIDATE_EMPTY_VISUAL_FLOOR
        and not _model_boundary_aligned(capture.png_bytes, clipped, require_all_sides=True)
        and _has_adjacent_selection_indicator(capture.png_bytes, clipped, target_control_type)
    ):
        return TargetQuality(
            accepted=False,
            reason="target boundary misaligned",
            visible_fraction=visible_fraction,
            visual_activity=visual_activity,
            boundary_activity=boundary_activity,
            target_area_fraction=target_area_fraction,
        )
    if (
        _candidate_boundary_alignment_target(source, target_control_type)
        and boundary_activity < MODEL_BOUNDARY_ACTIVITY_FLOOR
        and visual_activity >= CANDIDATE_EMPTY_VISUAL_FLOOR
        and not _model_boundary_aligned(capture.png_bytes, clipped, require_all_sides=True)
        and _has_visible_enclosing_boundary_outside(capture.png_bytes, clipped)
    ):
        return TargetQuality(
            accepted=False,
            reason="target boundary misaligned",
            visible_fraction=visible_fraction,
            visual_activity=visual_activity,
            boundary_activity=boundary_activity,
            target_area_fraction=target_area_fraction,
        )
    if (
        source != "model"
        and _candidate_leaf_action_target(target_control_type, image_rect)
        and (
            _has_compound_control_vertical_separators(capture.png_bytes, clipped)
            or _has_segmented_control_separator(capture.png_bytes, clipped)
        )
        and not _candidate_single_selection_row(
            capture.png_bytes,
            clipped,
            target_control_type,
        )
    ):
        return TargetQuality(
            accepted=False,
            reason="target appears to contain multiple controls",
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


def _candidate_action_container_target(
    target_control_type: str,
    rect: tuple[int, int, int, int],
) -> bool:
    if target_control_type.lower() not in CANDIDATE_ACTION_CONTAINER_CONTROL_TYPES:
        return False
    return _candidate_compound_rect_large_enough(rect)


def _candidate_row_container_target(
    target_control_type: str,
    rect: tuple[int, int, int, int],
) -> bool:
    if target_control_type.lower() not in CANDIDATE_ROW_CONTAINER_CONTROL_TYPES:
        return False
    return _candidate_compound_rect_large_enough(rect)


def _candidate_leaf_action_target(
    target_control_type: str,
    rect: tuple[int, int, int, int],
) -> bool:
    if target_control_type.lower() not in CANDIDATE_LEAF_ACTION_CONTROL_TYPES:
        return False
    return _candidate_compound_rect_large_enough(rect)


def _candidate_selection_row_target(target_control_type: str) -> bool:
    return target_control_type.lower() in SELECTION_ROW_CONTROL_TYPES


def _candidate_tabular_cell_target(target_control_type: str) -> bool:
    return target_control_type.lower() in TABULAR_CELL_CONTROL_TYPES


def _has_tabular_cell_internal_separators(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
) -> bool:
    return _has_compound_control_separators(png_bytes, rect, min_groups=1)


def _candidate_single_selection_row(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
    target_control_type: str,
) -> bool:
    if not _candidate_selection_row_target(target_control_type):
        return False
    matches = _selection_indicator_matches_inside(png_bytes, rect, target_control_type)
    if len(matches) != 1:
        return False
    return not _has_selection_row_extra_separators(png_bytes, rect, matches[0])


def _candidate_multiple_selection_indicators(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
    target_control_type: str,
) -> bool:
    if not _candidate_selection_row_target(target_control_type):
        return False
    matches = _selection_indicator_matches_inside(png_bytes, rect, target_control_type)
    for index, first in enumerate(matches):
        for second in matches[index + 1 :]:
            first_x, first_y, first_size = first
            second_x, second_y, second_size = second
            lane_tolerance = max(first_size, second_size) * 2
            min_vertical_gap = max(20, min(first_size, second_size))
            if (
                abs(first_x - second_x) <= lane_tolerance
                and abs(first_y - second_y) >= min_vertical_gap
            ):
                return True
    return False


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


def _candidate_container_boundary_alignment_target(target_control_type: str) -> bool:
    return target_control_type.lower() in CANDIDATE_ACTION_CONTAINER_CONTROL_TYPES


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


def _has_visible_enclosing_boundary_outside(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
) -> bool:
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            image = img.convert("L").filter(ImageFilter.FIND_EDGES)
            x, y, width, height = rect
            if width < 16 or height < 8:
                return False
            image_width, image_height = image.size
            x2 = x + width
            y2 = y + height
            max_x_margin = max(32, min(160, width * 2))
            max_y_margin = max(16, min(96, height * 3))
            left = _strongest_vertical_edge_line(
                image,
                range(max(4, x - max_x_margin), max(4, x - 4)),
                y,
                y2,
            )
            right = _strongest_vertical_edge_line(
                image,
                range(
                    min(image_width - 4, x2 + 4),
                    min(image_width - 4, x2 + max_x_margin),
                ),
                y,
                y2,
            )
            top = _strongest_horizontal_edge_line(
                image,
                range(max(4, y - max_y_margin), max(4, y - 4)),
                x,
                x2,
            )
            bottom = _strongest_horizontal_edge_line(
                image,
                range(
                    min(image_height - 4, y2 + 4),
                    min(image_height - 4, y2 + max_y_margin),
                ),
                x,
                x2,
            )
            if min(left[0], right[0], top[0], bottom[0]) < CANDIDATE_ENCLOSING_EDGE_FLOOR:
                return False
            if (
                _vertical_edge_groups_between(image, left[1] + 4, x - 4, y, y2) >= 2
                or _vertical_edge_groups_between(image, x2 + 4, right[1] - 4, y, y2) >= 2
            ):
                return False
            return True
    except Exception:
        return False


def _has_adjacent_selection_indicator(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
    target_control_type: str,
) -> bool:
    if not _candidate_selection_row_target(target_control_type):
        return False
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            image = img.convert("L")
            x, y, width, height = rect
            if width < 16 or height < 8:
                return False
            center_y = y + height // 2
            max_size = max(12, min(32, height + 8))
            min_size = max(10, min(18, height))
            for size in range(min_size, max_size + 1, 2):
                top_start = center_y - size // 2 - 6
                top_stop = center_y - size // 2 + 7
                for top in range(top_start, top_stop):
                    for gap in range(2, 25):
                        left_rect = (x - gap - size, top, size, size)
                        right_rect = (x + width + gap, top, size, size)
                        if _selection_indicator_rect_matches(image, left_rect):
                            return True
                        if _selection_indicator_rect_matches(image, right_rect):
                            return True
            return False
    except Exception:
        return False


def _selection_indicator_matches_inside(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
    target_control_type: str,
) -> list[tuple[int, int, int]]:
    if not _candidate_selection_row_target(target_control_type):
        return []
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            image = img.convert("L")
            x, y, width, height = rect
            if width < 16 or height < 8:
                return []
            x2 = x + width
            y2 = y + height
            max_size = max(12, min(32, height + 8))
            min_size = max(10, min(18, height))
            matches: list[tuple[int, int, int]] = []
            for size in range(min_size, max_size + 1, 2):
                scan_width = min(width, max(48, size * 4))
                left_ranges = [(x, min(x2 - size + 1, x + scan_width))]
                right_start = max(x, x2 - scan_width)
                right_stop = x2 - size + 1
                if right_start < right_stop:
                    left_ranges.append((right_start, right_stop))
                for top in range(y, y2 - size + 1):
                    for left_start, left_stop in left_ranges:
                        if left_start >= left_stop:
                            continue
                        for left in range(left_start, left_stop):
                            candidate = (left, top, size, size)
                            if not _selection_indicator_rect_matches(image, candidate):
                                continue
                            center = (left + size // 2, top + size // 2)
                            merge_x_radius = max(12, size * 2)
                            merge_y_radius = max(8, size)
                            if any(
                                abs(center[0] - seen_x) <= merge_x_radius
                                and abs(center[1] - seen_y) <= merge_y_radius
                                for seen_x, seen_y, _seen_size in matches
                            ):
                                continue
                            matches.append((center[0], center[1], size))
                            if len(matches) > 1:
                                return matches
            return matches
    except Exception:
        return []


def _has_selection_row_extra_separators(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
    match: tuple[int, int, int],
) -> bool:
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            x, y, width, height = rect
            crop = img.convert("L").crop((x, y, x + width, y + height))
            if crop.width < 24 or crop.height < 16:
                return False
            center_x, center_y, size = match
            left = max(0, center_x - size // 2 - x - 3)
            top = max(0, center_y - size // 2 - y - 3)
            right = min(crop.width, center_x + size // 2 - x + 3)
            bottom = min(crop.height, center_y + size // 2 - y + 3)
            pixels = crop.load()
            for erase_y in range(top, bottom):
                for erase_x in range(left, right):
                    pixels[erase_x, erase_y] = 255
            edges = crop.filter(ImageFilter.FIND_EDGES)
            return (
                _strong_internal_separator_groups(edges, vertical=True)
                >= MODEL_COMPOUND_MIN_SEPARATOR_GROUPS
                or _strong_center_vertical_separator_groups(edges) >= 1
            )
    except Exception:
        return True


def _selection_indicator_rect_matches(
    image: Image.Image,
    rect: tuple[int, int, int, int],
) -> bool:
    x, y, width, height = rect
    if x < 0 or y < 0 or x + width > image.width or y + height > image.height:
        return False
    if abs(width - height) > max(2, min(width, height) // 5):
        return False
    crop = image.crop((x, y, x + width, y + height))
    if _boundary_activity(crop) < MODEL_BOUNDARY_ACTIVITY_FLOOR:
        return False
    scores = _boundary_crossing_scores(image, rect)
    return len(scores) >= 4 and all(score >= MODEL_BOUNDARY_ALIGNMENT_FLOOR for score in scores)


def _strongest_vertical_edge_line(
    image: Image.Image,
    positions: Iterable[int],
    y1: int,
    y2: int,
) -> tuple[float, int]:
    pixels = image.load()
    height = image.height
    top = max(0, min(height, y1))
    bottom = max(0, min(height, y2))
    if bottom <= top:
        return 0.0, 0
    best = (0.0, 0)
    for x in positions:
        if x < 0 or x >= image.width:
            continue
        total = sum(int(pixels[x, y]) for y in range(top, bottom))
        score = total / ((bottom - top) * 255)
        if score > best[0]:
            best = (score, x)
    return best


def _strongest_horizontal_edge_line(
    image: Image.Image,
    positions: Iterable[int],
    x1: int,
    x2: int,
) -> tuple[float, int]:
    pixels = image.load()
    width = image.width
    left = max(0, min(width, x1))
    right = max(0, min(width, x2))
    if right <= left:
        return 0.0, 0
    best = (0.0, 0)
    for y in positions:
        if y < 0 or y >= image.height:
            continue
        total = sum(int(pixels[x, y]) for x in range(left, right))
        score = total / ((right - left) * 255)
        if score > best[0]:
            best = (score, y)
    return best


def _vertical_edge_groups_between(
    image: Image.Image,
    x1: int,
    x2: int,
    y1: int,
    y2: int,
) -> int:
    pixels = image.load()
    left = max(0, min(image.width, x1))
    right = max(0, min(image.width, x2))
    top = max(0, min(image.height, y1))
    bottom = max(0, min(image.height, y2))
    if right <= left or bottom <= top:
        return 0
    groups = 0
    last_strong: int | None = None
    for x in range(left, right):
        total = sum(int(pixels[x, y]) for y in range(top, bottom))
        score = total / ((bottom - top) * 255)
        if score < CANDIDATE_ENCLOSING_EDGE_FLOOR:
            continue
        if last_strong is None or x - last_strong > 2:
            groups += 1
        last_strong = x
    return groups


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


def _has_compound_control_vertical_separators(
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
            return _strong_internal_separator_groups(edges, vertical=True) >= min_groups
    except Exception:
        return False


def _has_segmented_control_separator(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
) -> bool:
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            x, y, width, height = rect
            crop = img.convert("L").crop((x, y, x + width, y + height))
            if crop.width < 48 or crop.height < 20:
                return False
            edges = crop.filter(ImageFilter.FIND_EDGES)
            return _strong_center_vertical_separator_groups(edges) >= 1
    except Exception:
        return False


def _has_internal_control_vertical_edges(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
) -> bool:
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            x, y, width, height = rect
            crop = img.convert("L").crop((x, y, x + width, y + height))
            if crop.width < 80 or crop.height < 36:
                return False
            edges = crop.filter(ImageFilter.FIND_EDGES)
            return _internal_vertical_edge_groups(edges) >= 2
    except Exception:
        return False


def _has_internal_control_horizontal_edges(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
) -> bool:
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            x, y, width, height = rect
            crop = img.convert("L").crop((x, y, x + width, y + height))
            if crop.width < 80 or crop.height < 36:
                return False
            edges = crop.filter(ImageFilter.FIND_EDGES)
            return _internal_horizontal_edge_groups(edges) >= 1
    except Exception:
        return False


def _internal_vertical_edge_groups(edges: Image.Image) -> int:
    width, height = edges.size
    band = max(2, min(5, width // 12, height // 12))
    margin_x = max(band * 3, int(width * 0.08))
    if width <= margin_x * 2 or height <= band * 2:
        return 0
    pixels = edges.load()
    line_length = height - band * 2
    groups = 0
    last_strong: int | None = None
    for position in range(margin_x, width - margin_x):
        strong = 0
        for y in range(band, height - band):
            if int(pixels[position, y]) > 32:
                strong += 1
        if strong / max(1, line_length) < CANDIDATE_INNER_CONTROL_EDGE_FLOOR:
            continue
        if last_strong is None or position - last_strong > 2:
            groups += 1
        last_strong = position
    return groups


def _internal_horizontal_edge_groups(edges: Image.Image) -> int:
    width, height = edges.size
    band = max(2, min(5, width // 12, height // 12))
    margin_y = max(band * 3, int(height * 0.18))
    if width <= band * 2 or height <= margin_y * 2:
        return 0
    pixels = edges.load()
    line_length = width - band * 2
    groups = 0
    last_strong: int | None = None
    for position in range(margin_y, height - margin_y):
        strong = 0
        for x in range(band, width - band):
            if int(pixels[x, position]) > 32:
                strong += 1
        if strong / max(1, line_length) < CANDIDATE_INNER_ROW_EDGE_FLOOR:
            continue
        if last_strong is None or position - last_strong > 2:
            groups += 1
        last_strong = position
    return groups


def _strong_center_vertical_separator_groups(edges: Image.Image) -> int:
    width, height = edges.size
    band = max(2, min(5, width // 12, height // 12))
    margin_x = max(band * 3, int(width * 0.12))
    if width <= margin_x * 2 or height <= band * 2:
        return 0
    pixels = edges.load()
    line_length = height - band * 2
    groups = 0
    last_strong: int | None = None
    for position in range(margin_x, width - margin_x):
        strong = 0
        for y in range(band, height - band):
            if int(pixels[position, y]) > 32:
                strong += 1
        if strong / max(1, line_length) < CANDIDATE_SEGMENTED_SEPARATOR_FLOOR:
            continue
        if last_strong is None or position - last_strong > 2:
            groups += 1
        last_strong = position
    return groups


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
