from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat

from screen import Capture

MIN_VISIBLE_FRACTION = 0.35
MODEL_EMPTY_VISUAL_FLOOR = 0.035
MODEL_LOW_CONFIDENCE_FLOOR = 0.20


@dataclass(frozen=True)
class TargetQuality:
    accepted: bool
    reason: str = ""
    visible_fraction: float = 1.0
    visual_activity: float = 0.0


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
    if visible_fraction < MIN_VISIBLE_FRACTION:
        return TargetQuality(
            accepted=False,
            reason="target mostly outside capture",
            visible_fraction=visible_fraction,
        )

    visual_activity = _visual_activity(capture.png_bytes, clipped)
    if (
        source == "model"
        and confidence <= MODEL_LOW_CONFIDENCE_FLOOR
        and visual_activity < MODEL_EMPTY_VISUAL_FLOOR
    ):
        return TargetQuality(
            accepted=False,
            reason="target appears visually empty",
            visible_fraction=visible_fraction,
            visual_activity=visual_activity,
        )

    return TargetQuality(
        accepted=True,
        visible_fraction=visible_fraction,
        visual_activity=visual_activity,
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


def _visual_activity(png_bytes: bytes, rect: tuple[int, int, int, int]) -> float:
    try:
        with Image.open(io.BytesIO(png_bytes)) as img:
            x, y, width, height = rect
            crop = img.convert("L").crop((x, y, x + width, y + height))
            if crop.width <= 0 or crop.height <= 0:
                return 0.0
            stats = ImageStat.Stat(crop)
            contrast = (stats.stddev[0] if stats.stddev else 0.0) / 255.0
            edges = crop.filter(ImageFilter.FIND_EDGES)
            edge_mean = (ImageStat.Stat(edges).mean[0] if edges else 0.0) / 255.0
            return min(1.0, 0.65 * contrast + 0.35 * edge_mean)
    except Exception:
        return 0.0

