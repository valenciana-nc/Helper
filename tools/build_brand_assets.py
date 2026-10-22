"""Regenerate the README logo and Windows icon from Helper's violet orb painter.

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
    # Retain the QApplication until all painter/image work has finished.
    assert app is not None


if __name__ == "__main__":
    build_assets()
