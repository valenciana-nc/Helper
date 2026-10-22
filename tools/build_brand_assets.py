"""Regenerate Helper's violet logo, web-safe README thumbnail, and Windows icon.

Run from the repository root: python tools/build_brand_assets.py
"""
from __future__ import annotations

import os
from pathlib import Path
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from PIL import Image  # noqa: E402
from PyQt6.QtCore import QRectF, Qt  # noqa: E402
from PyQt6.QtGui import QImage, QPainter  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402

from ui_lava import paint_lava_orb  # noqa: E402


def build_readme_logo(logo: Image.Image) -> None:
    """Use a separate, 8-bit web asset so README browsers do not reuse the old blue logo URL."""
    output = ROOT / "assets" / "readme" / "helper-orb-purple.png"
    output.parent.mkdir(parents=True, exist_ok=True)
    thumbnail = logo.convert("RGBA").resize((360, 360), Image.Resampling.LANCZOS)
    Image.frombytes("RGBA", thumbnail.size, thumbnail.tobytes()).save(output, optimize=True)


def build_assets() -> None:
    app = QApplication.instance() or QApplication([])
    size = 1024
    image = QImage(size, size, QImage.Format.Format_RGBA64_Premultiplied)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    inset = size * .08
    paint_lava_orb(painter, QRectF(inset, inset, size - inset * 2, size - inset * 2), 2.4)
    painter.end()
    output = ROOT / "assets"
    output.mkdir(exist_ok=True)
    png = output / "helper_logo.png"
    if not image.save(str(png)):
        raise RuntimeError(f"Could not save {png}")
    sizes = [(size, size) for size in (16, 20, 24, 32, 40, 48, 64, 96, 128, 192, 256)]
    with Image.open(png) as logo:
        logo.save(output / "helper_logo.ico", format="ICO", sizes=sizes)
        build_readme_logo(logo)
    # Retain the QApplication until all painter/image work has finished.
    assert app is not None


if __name__ == "__main__":
    build_assets()
