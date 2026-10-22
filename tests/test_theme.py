"""Application chrome is monochrome; orb and computer-use presentation stay purple."""
from __future__ import annotations

from pathlib import Path
import re
import unittest
from unittest.mock import patch

from PIL import Image
from PyQt6.QtGui import QColor, QImage, QPainter, QPalette
from PyQt6.QtWidgets import QApplication

import config
import theme
from ui_computer_use import BANNER_COLOR, BANNER_END_COLOR
from ui_widget import FloatingCircle


def contrast(first: str, second: str) -> float:
    def luminance(spec: str) -> float:
        color = QColor(spec)
        channels = (color.redF(), color.greenF(), color.blueF())
        linear = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4 for value in channels]
        return sum(value * weight for value, weight in zip(linear, (.2126, .7152, .0722)))
    first_value, second_value = sorted((luminance(first), luminance(second)))
    return (second_value + .05) / (first_value + .05)


class MonochromeThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_every_chrome_token_is_grayscale_in_both_themes(self):
        self.assertEqual(theme.PALETTE_LIGHT.keys(), theme.PALETTE_DARK.keys())
        for palette in (theme.PALETTE_LIGHT, theme.PALETTE_DARK):
            for name, spec in palette.items():
                with self.subTest(name=name, background=palette["bg_window"]):
                    color = QColor(spec)
                    self.assertTrue(color.isValid())
                    self.assertEqual(color.red(), color.green())
                    self.assertEqual(color.blue(), color.green())

    def test_light_canvas_sidebar_cards_and_native_windows_are_white(self):
        try:
            with patch.object(config, "THEME", "light"):
                theme.apply_application_theme(self.app)
                for token in ("bg_window", "bg_sidebar", "bg_topbar", "bg_card"):
                    self.assertEqual(theme.PALETTE_LIGHT[token], "#ffffff")
                self.assertEqual(self.app.palette().color(QPalette.ColorRole.Window), QColor("white"))
                self.assertEqual(self.app.palette().color(QPalette.ColorRole.Base), QColor("white"))
        finally:
            theme.apply_application_theme(self.app)

    def test_text_primary_buttons_and_selected_text_keep_contrast(self):
        for palette in (theme.PALETTE_LIGHT, theme.PALETTE_DARK):
            self.assertGreaterEqual(contrast(palette["text_primary"], palette["bg_window"]), 4.5)
            self.assertGreaterEqual(contrast(palette["text_secondary"], palette["bg_card"]), 4.5)
            self.assertGreaterEqual(contrast(palette["btn_primary_fg"], palette["btn_primary_bg"]), 4.5)
            self.assertGreaterEqual(contrast(palette["selection_fg"], palette["accent"]), 4.5)
            self.assertIn(f'selection-color: {palette["selection_fg"]}', theme.qss(theme.INPUT_QSS, palette))
            self.assertIn(palette["btn_primary_disabled_fg"], theme.qss(theme.BUTTON_QSS, palette))

    def test_purple_computer_use_banner_keeps_white_text_contrast(self):
        for spec in (BANNER_COLOR, BANNER_END_COLOR):
            self.assertGreaterEqual(contrast(spec, "#ffffff"), 4.5)

    def test_floating_input_and_side_controls_have_no_purple_pixels(self):
        for mode in ("light", "dark"):
            with patch.object(config, "THEME", mode):
                widget = FloatingCircle()
                try:
                    image = QImage(124, 40, QImage.Format.Format_ARGB32_Premultiplied)
                    image.fill(QColor(0, 0, 0, 0))
                    painter = QPainter(image)
                    widget._paint_pill_background(painter, 1)
                    widget._paint_left_icon(painter, 1)
                    widget._paint_right_icon(painter, 1)
                    painter.end()
                    for y in range(image.height()):
                        for x in range(image.width()):
                            color = image.pixelColor(x, y)
                            if color.alpha():
                                self.assertEqual(color.red(), color.green())
                                self.assertEqual(color.blue(), color.green())
                finally:
                    widget.close()
                    widget.deleteLater()
        self.app.processEvents()

    def test_control_svg_icons_are_monochrome_but_logo_stays_purple(self):
        root = Path(__file__).resolve().parents[1]
        for path in (root / "assets" / "ui").glob("*.svg"):
            for spec in re.findall(r'#[0-9a-fA-F]{6}', path.read_text(encoding="utf-8")):
                color = QColor(spec)
                self.assertEqual(color.red(), color.green())
                self.assertEqual(color.blue(), color.green())
        with Image.open(root / "assets" / "helper_logo.png") as logo:
            red, green, blue, _ = logo.convert("RGBA").getpixel((512, 780))
            self.assertGreater(red, green + 10)
            self.assertGreater(blue, green + 30)


if __name__ == "__main__":
    unittest.main()
