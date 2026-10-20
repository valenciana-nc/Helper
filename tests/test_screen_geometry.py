from __future__ import annotations

import unittest
from unittest.mock import patch

from PyQt6.QtCore import QRect

from ui_screen_geometry import local_screen_point, screen_native_geometry


class ScreenGeometryTests(unittest.TestCase):
    def test_negative_origin_monitor_maps_physical_point_to_local(self) -> None:
        point = local_screen_point(-1720, 240, QRect(-1920, 0, 1920, 1080))
        self.assertIsNotNone(point)
        assert point is not None
        self.assertEqual((point.x(), point.y()), (200.0, 240.0))

    def test_point_outside_monitor_is_rejected(self) -> None:
        self.assertIsNone(local_screen_point(100, 100, QRect(-1920, 0, 1920, 1080)))

    def test_high_dpi_monitor_scales_physical_point_to_logical_point(self) -> None:
        point = local_screen_point(2000, 1000, QRect(0, 0, 1920, 1080), device_pixel_ratio=2.0)
        self.assertIsNotNone(point)
        assert point is not None
        self.assertEqual((point.x(), point.y()), (1000.0, 500.0))

    def test_native_geometry_falls_back_to_scaled_qt_geometry(self) -> None:
        class Screen:
            def geometry(self):
                return QRect(-960, 0, 960, 540)

            def devicePixelRatio(self):
                return 2.0

        with patch("ui_screen_geometry._native_monitor_geometries", return_value=[]):
            geometry = screen_native_geometry(Screen())
        self.assertEqual(geometry, QRect(-1920, 0, 1920, 1080))


if __name__ == "__main__":
    unittest.main()
