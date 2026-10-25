"""Screenshot geometry and cheap visual comparisons, independent of the platform."""
from __future__ import annotations

import io
from functools import lru_cache

from PIL import Image, ImageChops, ImageDraw, ImageStat

from .types import Action, Observation, ProtocolError, Rect, StaleObservation


__all__ = ["target_rect", "target_rect_or_stale", "image_difference", "annotated_png"]


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


def target_rect_or_stale(action: Action, observation: Observation) -> Rect | None:
    """Resolve the action's target rect for a driver about to dispatch input.

    An invalid target means the scene moved since the decision, not an
    ambiguous side effect, so it is a stale observation rather than a
    protocol error: the runtime re-observes instead of recording an
    UNKNOWN effect. Both drivers share this rule, so it lives here rather
    than as a repeated try/except at each dispatch site.
    """
    try:
        return target_rect(action, observation)
    except ProtocolError as exc:
        raise StaleObservation(f"Target no longer valid: {exc}") from exc


@lru_cache(maxsize=4)
def _decoded_rgb(png: bytes):
    """Decode PNG bytes to a fully-loaded RGB image, memoizing recent results.

    The runtime compares the same screenshot bytes repeatedly (help-mode wait
    loops poll every 0.35s against one reference frame; each step also runs a
    no-progress and a post-action comparison). Re-decoding a full screenshot
    costs tens of milliseconds, so the last few decodes are retained. The
    cache is bounded (4 entries) so long runs cannot grow memory, and corrupt
    input raises instead of caching -- lru_cache never stores exceptions, so
    invalid images keep the caller's existing "treat as fully different"
    behavior. Callers must treat the returned image as read-only; crop/resize
    already return new images.
    """
    with Image.open(io.BytesIO(png)) as source:
        return source.convert("RGB")


def image_difference(first: bytes, second: bytes, *, box=None) -> float:
    """Normalized mean pixel difference; invalid images cannot prove freshness."""
    try:
        a, b = _decoded_rgb(first), _decoded_rgb(second)
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
