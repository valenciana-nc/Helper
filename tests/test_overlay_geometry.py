from __future__ import annotations

import unittest

from PyQt6.QtCore import QRect


class OverlayGeometryTests(unittest.TestCase):
    def test_highlight_translates_to_negative_origin_screen(self) -> None:
        from ui_overlay import local_highlight_rect

        local = local_highlight_rect(
            QRect(-1720, 240, 120, 40),
            QRect(-1920, 0, 1920, 1080),
        )

        self.assertIsNotNone(local)
        assert local is not None
        self.assertEqual((local.x(), local.y(), local.width(), local.height()), (200, 240, 120, 40))

    def test_highlight_not_on_non_intersecting_screen(self) -> None:
        from ui_overlay import local_highlight_rect

        local = local_highlight_rect(
            QRect(-1720, 240, 120, 40),
            QRect(0, 0, 1000, 800),
        )

        self.assertIsNone(local)

    def test_high_dpi_highlight_subtracts_native_origin_before_scaling(self) -> None:
        from ui_overlay import local_highlight_rect

        local = local_highlight_rect(
            QRect(-1720, 240, 120, 40),
            QRect(-960, 0, 960, 540),
            device_pixel_ratio=2.0,
        )

        self.assertIsNotNone(local)
        assert local is not None
        self.assertEqual((local.x(), local.y(), local.width(), local.height()), (100, 120, 60, 20))

    def test_high_dpi_highlight_uses_logical_local_rect(self) -> None:
        from ui_overlay import local_highlight_rect

        local = local_highlight_rect(
            QRect(2000, 1000, 200, 80),
            QRect(0, 0, 1920, 1080),
            device_pixel_ratio=2.0,
        )

        self.assertIsNotNone(local)
        assert local is not None
        self.assertEqual((local.x(), local.y(), local.width(), local.height()), (1000, 500, 100, 40))

    def test_high_dpi_highlight_can_use_explicit_native_screen_geometry(self) -> None:
        from ui_overlay import local_highlight_rect

        local = local_highlight_rect(
            QRect(3000, 1000, 200, 80),
            QRect(1920, 0, 1920, 1080),
            device_pixel_ratio=2.0,
            native_screen_geometry=QRect(2560, 0, 3840, 2160),
        )

        self.assertIsNotNone(local)
        assert local is not None
        self.assertEqual((local.x(), local.y(), local.width(), local.height()), (220, 500, 100, 40))

    def test_high_dpi_cursor_point_uses_logical_local_point(self) -> None:
        from ui_overlay import local_screen_point

        point = local_screen_point(
            2000,
            1000,
            QRect(0, 0, 1920, 1080),
            device_pixel_ratio=2.0,
        )

        self.assertIsNotNone(point)
        assert point is not None
        self.assertEqual((point.x(), point.y()), (1000.0, 500.0))

    def test_anchored_point_highlight_stays_on_anchor_screen_at_boundary(self) -> None:
        from ui_overlay import (
            OverlayHighlight,
            highlight_visible_on_screen,
            local_highlight_rect,
        )

        highlight = OverlayHighlight(
            x=1895,
            y=100,
            width=48,
            height=48,
            label="Click",
            anchor_x=1919,
            anchor_y=124,
        )
        primary = QRect(0, 0, 1920, 1080)
        secondary = QRect(1920, 0, 1920, 1080)

        self.assertIsNotNone(local_highlight_rect(highlight.rect, secondary))
        self.assertTrue(highlight_visible_on_screen(highlight, primary))
        self.assertFalse(highlight_visible_on_screen(highlight, secondary))

    def test_label_clamps_inside_top_right_edge(self) -> None:
        from ui_overlay import place_label_rect

        rect = place_label_rect(
            QRect(960, 4, 30, 20),
            label_width=160,
            label_height=34,
            surface_width=1000,
            surface_height=800,
        )

        self.assertEqual(rect.x(), 832)
        self.assertEqual(rect.y(), 33)
        self.assertLessEqual(rect.right(), 992)

    def test_label_clamps_inside_bottom_edge(self) -> None:
        from ui_overlay import place_label_rect

        rect = place_label_rect(
            QRect(20, 4, 30, 780),
            label_width=180,
            label_height=34,
            surface_width=1000,
            surface_height=800,
        )

        self.assertEqual(rect.x(), 20)
        self.assertEqual(rect.y(), 758)
        self.assertLessEqual(rect.bottom(), 792)


if __name__ == "__main__":
    unittest.main()
