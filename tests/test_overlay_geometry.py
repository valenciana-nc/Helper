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
