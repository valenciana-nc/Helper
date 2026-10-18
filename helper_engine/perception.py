"""Screenshot geometry and cheap visual comparisons, independent of the platform."""
from __future__ import annotations

import io

from PIL import Image, ImageChops, ImageDraw, ImageStat

from .types import Action, Observation, ProtocolError, Rect


__all__ = ["target_rect", "image_difference", "annotated_png"]


def target_rect(action: Action, observation: Observation) -> Rect | None:
    target = action.target
    if target is None:
        return None
    if target.element_id:
        element = observation.element(target.element_id)
        if element is None or not element.visible or not element.enabled:
            raise ProtocolError("Target is absent, hidden, or disabled in this observation")
        return element.rect
    if target.box is None:
        raise ProtocolError("Missing target geometry")
    return observation.screen_box(target.box)


def image_difference(first: bytes, second: bytes, *, box=None) -> float:
    """Normalized mean pixel difference; invalid images cannot prove freshness."""
    try:
        with Image.open(io.BytesIO(first)) as a, Image.open(io.BytesIO(second)) as b:
            a, b = a.convert("RGB"), b.convert("RGB")
            if a.size != b.size:
                return 1.0
            if box is not None:
                left, top, right, bottom = box
                if right <= left or bottom <= top:
                    return 1.0
                a, b = a.crop(box), b.crop(box)
            a = a.resize((96, 64))
            b = b.resize((96, 64))
            return sum(ImageStat.Stat(ImageChops.difference(a, b)).mean) / (3 * 255)
    except Exception:
        # A corrupt screenshot must never crash the run; treat as fully different
        # so the runtime re-observes instead of trusting stale pixels.
        return 1.0


def annotated_png(observation: Observation, *, limit: int = 100) -> bytes:
    if not observation.elements:
        return observation.png
    with Image.open(io.BytesIO(observation.png)) as source:
        image = source.convert("RGB")
    draw = ImageDraw.Draw(image)
    for element in observation.elements[:limit]:
        if not element.visible or not element.enabled:
            continue
        x, y, right, bottom = observation.image_box(element.rect)
        if right <= x or bottom <= y:
            continue
        draw.rectangle((x, y, right, bottom), outline="#32c5ff", width=1)
        label = element.id
        # IDs are short ASCII marks; Unicode names stay in the text observation.
        text_box = draw.textbbox((0, 0), label)
        w, h = text_box[2] + 5, text_box[3] + 5
        label_y = max(0, y - h)
        draw.rectangle((x, label_y, x + w, label_y + h), fill="#123b54")
        draw.text((x + 2, label_y + 1), label, fill="white")
    out = io.BytesIO()
    image.save(out, format="PNG", compress_level=2)
    return out.getvalue()
