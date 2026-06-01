from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat

from screen import Capture

MIN_VISIBLE_FRACTION = 0.35
MODEL_EMPTY_VISUAL_FLOOR = 0.035
MODEL_BOUNDARY_ACTIVITY_FLOOR = 0.10
MODEL_NOISY_VISUAL_CEILING = 0.40
MODEL_NOISY_BOUNDARY_FLOOR = 0.25
MODEL_COMPOUND_SEPARATOR_FLOOR = 0.65
MODEL_COMPOUND_MIN_SEPARATOR_GROUPS = 2
CANDIDATE_EMPTY_VISUAL_FLOOR = 0.012
MAX_TARGET_AREA_FRACTION = 0.25


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
        if _has_compound_control_separators(capture.png_bytes, clipped):
            return TargetQuality(
                accepted=False,
                reason="target appears to contain multiple controls",
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

    return TargetQuality(
        accepted=True,
        visible_fraction=visible_fraction,
        visual_activity=visual_activity,
        boundary_activity=boundary_activity,
        target_area_fraction=target_area_fraction,
    )


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


def _has_compound_control_separators(
    png_bytes: bytes,
    rect: tuple[int, int, int, int],
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
                >= MODEL_COMPOUND_MIN_SEPARATOR_GROUPS
                or _strong_internal_separator_groups(edges, vertical=False)
                >= MODEL_COMPOUND_MIN_SEPARATOR_GROUPS
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
