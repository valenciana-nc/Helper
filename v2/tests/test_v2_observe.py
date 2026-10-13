"""Tests for v2.observe (set-of-marks annotation). Headless: PIL only."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from v2.observe import Mark, _badge_font, annotate, crop_png
from v2.world import Element, ScreenFrame

W, H = 800, 600
BG = (240, 240, 240)
BADGE_FILL = (220, 30, 30)


def _blank_png(w: int = W, h: int = H, color: tuple[int, int, int] = BG) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (w, h), color).save(buf, format="PNG")
    return buf.getvalue()


def _el(
    eid: str,
    ctype: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    enabled: bool = True,
    visible: bool = True,
    name: str = "",
) -> Element:
    return Element(
        id=eid,
        control_type=ctype,
        name=name or eid,
        rect=(x, y, w, h),
        enabled=enabled,
        visible=visible,
        value=None,
    )


def _frame(elements: dict[str, Element]) -> ScreenFrame:
    return ScreenFrame(
        seq=1, png=_blank_png(), width=W, height=H, elements=elements, changed_ids=set()
    )


def _mixed_frame() -> ScreenFrame:
    return _frame(
        {
            "1:menu": _el("1:menu", "MenuItem", 10, 10, 50, 24),
            "1:ok": _el("1:ok", "Button", 100, 50, 80, 30),
            "1:cancel": _el("1:cancel", "Button", 200, 50, 80, 30),
            "1:link": _el("1:link", "Hyperlink", 50, 120, 60, 20),
            "1:edit": _el("1:edit", "Edit", 50, 200, 200, 28),
            "1:check": _el("1:check", "CheckBox", 50, 250, 16, 16),
            "1:combo": _el("1:combo", "ComboBox", 50, 300, 120, 28),
            # Not markable:
            "1:pane": _el("1:pane", "Pane", 0, 0, 800, 600),
            "1:text": _el("1:text", "Text", 50, 400, 100, 20),
            "1:disabled": _el("1:disabled", "Button", 300, 50, 80, 30, enabled=False),
            "1:hidden": _el("1:hidden", "Button", 400, 50, 80, 30, visible=False),
            "1:tiny": _el("1:tiny", "Button", 500, 50, 2, 2),
        }
    )


def _red_pixel_count(png: bytes) -> int:
    img = Image.open(io.BytesIO(png)).convert("RGB")
    raw = img.tobytes()
    return sum(
        1
        for i in range(0, len(raw), 3)
        if abs(raw[i] - BADGE_FILL[0]) < 30
        and abs(raw[i + 1] - BADGE_FILL[1]) < 30
        and abs(raw[i + 2] - BADGE_FILL[2]) < 30
    )


def test_annotate_returns_png_and_marks_in_yx_order():
    png, marks = annotate(_mixed_frame())

    img = Image.open(io.BytesIO(png))
    assert img.size == (W, H)

    # Only the 7 interactable/visible/enabled/non-tiny elements.
    assert [m.element_id for m in marks] == [
        "1:menu",  # y=10
        "1:ok",  # y=50, x=100
        "1:cancel",  # y=50, x=200
        "1:link",  # y=120
        "1:edit",  # y=200
        "1:check",  # y=250
        "1:combo",  # y=300
    ]
    assert [m.n for m in marks] == [1, 2, 3, 4, 5, 6, 7]
    assert all(isinstance(m, Mark) for m in marks)
    # Badges were actually drawn.
    assert _red_pixel_count(png) > 500


def test_annotate_max_marks_caps_and_renumbers():
    png, marks = annotate(_mixed_frame(), max_marks=3)
    assert [m.n for m in marks] == [1, 2, 3]
    assert [m.element_id for m in marks] == ["1:menu", "1:ok", "1:cancel"]


def test_annotate_is_deterministic():
    frame = _mixed_frame()
    png1, marks1 = annotate(frame)
    png2, marks2 = annotate(frame)
    assert png1 == png2
    assert marks1 == marks2


def test_annotate_drops_colliding_badges_first_wins():
    frame = _frame(
        {
            "1:a": _el("1:a", "Button", 50, 50, 80, 30),
            "1:b": _el("1:b", "Button", 50, 50, 80, 30),  # identical badge spot
            "1:c": _el("1:c", "Button", 60, 55, 80, 30),  # overlapping badge spot
            "1:far": _el("1:far", "Button", 400, 400, 80, 30),
        }
    )
    _, marks = annotate(frame)
    assert [m.element_id for m in marks] == ["1:a", "1:far"]
    assert [m.n for m in marks] == [1, 2]


def test_annotate_clamps_badge_inside_image():
    frame = _frame({"1:corner": _el("1:corner", "Button", 795, 595, 20, 20)})
    png, marks = annotate(frame)
    assert [m.element_id for m in marks] == ["1:corner"]
    img = Image.open(io.BytesIO(png)).convert("RGB")
    # Clamped badge top-left is (800-28, 600-28) = (772, 572), center (786, 586);
    # (776, 578) is inside the circle (dist ~12.8 < 14) and left of the glyph.
    px = img.getpixel((776, 578))
    assert abs(px[0] - BADGE_FILL[0]) < 30
    assert abs(px[1] - BADGE_FILL[1]) < 30
    assert abs(px[2] - BADGE_FILL[2]) < 30


def test_annotate_empty_frame():
    png, marks = annotate(_frame({}))
    assert marks == []
    assert Image.open(io.BytesIO(png)).size == (W, H)


def test_crop_png_basic_and_pad():
    png = _blank_png()
    assert Image.open(io.BytesIO(crop_png(png, (100, 50, 80, 30)))).size == (80, 30)
    assert Image.open(io.BytesIO(crop_png(png, (100, 50, 80, 30), pad=10))).size == (
        100,
        50,
    )


def test_crop_png_clamps_to_image_edges():
    png = _blank_png()
    # Pad would extend past the edges; must clamp, not crash.
    assert Image.open(io.BytesIO(crop_png(png, (0, 0, 10, 10), pad=20))).size == (30, 30)
    assert Image.open(io.BytesIO(crop_png(png, (790, 590, 50, 50)))).size == (10, 10)


def test_badge_font_falls_back_without_size_support(monkeypatch):
    # Very old Pillow: load_default() takes no size= kwarg (TypeError) —
    # _badge_font must fall back to the bare call instead of crashing.
    from PIL import ImageFont

    real_load_default = ImageFont.load_default

    def fake_load_default(*args, **kwargs):
        if "size" in kwargs:
            raise TypeError("load_default() got an unexpected keyword 'size'")
        return real_load_default()

    monkeypatch.setattr(ImageFont, "load_default", fake_load_default)
    assert _badge_font() is not None


def test_crop_png_empty_rect_raises():
    png = _blank_png()
    with pytest.raises(ValueError):
        crop_png(png, (900, 900, 10, 10))
    with pytest.raises(ValueError):
        crop_png(png, (100, 100, 0, 0))
