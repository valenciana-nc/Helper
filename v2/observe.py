"""Set-of-marks screenshot annotation (v2 perception).

Pure function of the ScreenFrame: draws numbered badges on the top
interactable elements so the VLM can return mark IDs instead of raw
coordinates. Headless — PIL only, no Qt, no display.
"""

from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageDraw, ImageFont

from v2.world import ScreenFrame, normalized_control_type


@dataclass(frozen=True)
class Mark:
    n: int  # 1-based label drawn on the image
    element_id: str


_BADGE_DIAMETER = 28
_BADGE_FILL = (220, 30, 30)  # red badge
_BADGE_TEXT = (255, 255, 255)  # white number
_FONT_SIZE = 16

# Elements smaller than this in either dimension are unusable as targets.
_MIN_DIM_PX = 4

# Normalized (lowercased, alphanumeric-only) control types considered
# interactable: buttons, links, text fields, menu items, checkboxes,
# combo boxes, and close relatives.
_INTERACTABLE_TYPES = frozenset(
    {
        "button",
        "splitbutton",
        "togglebutton",
        "hyperlink",
        "link",
        "edit",
        "textfield",
        "textbox",
        "document",
        "menuitem",
        "checkbox",
        "combobox",
        "radiobutton",
        "tabitem",
        "tab",
        "listitem",
        "treeitem",
        "slider",
        "spinner",
    }
)


def _badge_font() -> ImageFont.ImageFont:
    try:
        return ImageFont.load_default(size=_FONT_SIZE)
    except TypeError:  # very old Pillow without size= support
        return ImageFont.load_default()


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(hi, v))


def _boxes_overlap(a: tuple[int, int, int, int], b: tuple[int, int, int, int]) -> bool:
    ax1, ay1, ax2, ay2 = a
    bx1, by1, bx2, by2 = b
    return ax1 < bx2 and bx1 < ax2 and ay1 < by2 and by1 < ay2


def annotate(frame: ScreenFrame, *, max_marks: int = 40) -> tuple[bytes, list[Mark]]:
    """Draw numbered badges on clickable/typable elements.

    Returns (annotated PNG bytes, marks). Marks are 1-based, in draw order.
    Ordering is deterministic (y, then x, then element id) so marks are
    stable frame-to-frame when nothing changes. Badges that would collide
    with an already-drawn badge are dropped (first wins); only drawn marks
    are returned.
    """
    image = Image.open(io.BytesIO(frame.png)).convert("RGB")
    img_w, img_h = image.size

    # 1. Filter to markable elements.
    candidates = []
    for el in frame.elements.values():
        if not el.enabled or not el.visible:
            continue
        if normalized_control_type(el.control_type) not in _INTERACTABLE_TYPES:
            continue
        _, _, w, h = el.rect
        if w < _MIN_DIM_PX or h < _MIN_DIM_PX:
            continue
        candidates.append(el)

    # 2. Deterministic order, then cap.
    candidates.sort(key=lambda el: (el.rect[1], el.rect[0], el.id))
    candidates = candidates[:max_marks]

    # 3. Draw badges; drop any that would collide with an earlier one.
    draw = ImageDraw.Draw(image)
    font = _badge_font()
    drawn: list[tuple[int, int, int, int]] = []
    marks: list[Mark] = []
    for el in candidates:
        x, y, _, _ = (int(v) for v in el.rect)
        bx = _clamp(x, 0, max(0, img_w - _BADGE_DIAMETER))
        by = _clamp(y, 0, max(0, img_h - _BADGE_DIAMETER))
        box = (bx, by, bx + _BADGE_DIAMETER, by + _BADGE_DIAMETER)
        if any(_boxes_overlap(box, other) for other in drawn):
            continue
        drawn.append(box)

        n = len(marks) + 1
        draw.ellipse(box, fill=_BADGE_FILL)
        label = str(n)
        l, t, r, b = draw.textbbox((0, 0), label, font=font)
        tw, th = r - l, b - t
        draw.text(
            (
                bx + (_BADGE_DIAMETER - tw) / 2 - l,
                by + (_BADGE_DIAMETER - th) / 2 - t,
            ),
            label,
            font=font,
            fill=_BADGE_TEXT,
        )
        marks.append(Mark(n=n, element_id=el.id))

    out = io.BytesIO()
    image.save(out, format="PNG")
    return out.getvalue(), marks


def crop_png(png: bytes, rect_xywh: tuple[int, int, int, int], *, pad: int = 0) -> bytes:
    """Crop a region from a PNG (for the verifier's region crops).

    rect is (x, y, w, h) in image px. pad expands every side, clamped to
    the image bounds. Raises ValueError if the resulting region is empty.
    """
    image = Image.open(io.BytesIO(png)).convert("RGB")
    img_w, img_h = image.size
    x, y, w, h = (int(v) for v in rect_xywh)
    x0 = _clamp(x - pad, 0, img_w)
    y0 = _clamp(y - pad, 0, img_h)
    x1 = _clamp(x + w + pad, 0, img_w)
    y1 = _clamp(y + h + pad, 0, img_h)
    if x1 <= x0 or y1 <= y0:
        raise ValueError(
            f"crop rect {rect_xywh} (pad={pad}) is empty for {img_w}x{img_h} image"
        )
    out = io.BytesIO()
    image.crop((x0, y0, x1, y1)).save(out, format="PNG")
    return out.getvalue()
