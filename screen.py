import ctypes
import io
import sys
from dataclasses import dataclass

import mss
from PIL import Image

from config import SCREENSHOT_MAX_EDGE


def set_dpi_aware() -> None:
    # DPI awareness is a Windows-only concept — ctypes.windll doesn't exist on
    # other platforms. (The old try/except fallback also touched windll and
    # would crash if the first call raised.)
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


@dataclass(frozen=True)
class Capture:
    png_bytes: bytes
    width: int
    height: int
    monitor_left: int
    monitor_top: int
    scale: float

    def to_screen_coords(self, x: int, y: int) -> tuple[int, int]:
        sx = int(x / self.scale) + self.monitor_left
        sy = int(y / self.scale) + self.monitor_top
        return sx, sy


def _downscale(img: Image.Image, max_edge: int) -> tuple[Image.Image, float]:
    long_edge = max(img.width, img.height)
    if long_edge <= max_edge:
        return img, 1.0
    scale = max_edge / long_edge
    new_size = (int(img.width * scale), int(img.height * scale))
    return img.resize(new_size, Image.LANCZOS), scale


def _capture_monitor(monitor: dict[str, int], max_edge: int) -> Capture:
    with mss.mss() as sct:
        raw = sct.grab(monitor)
        img = Image.frombytes("RGB", raw.size, raw.rgb)
    scaled, ratio = _downscale(img, max_edge)
    buf = io.BytesIO()
    # No optimize=True: the world loop encodes a full frame every poll
    # cycle for change detection, and optimize's extra zlib pass costs
    # ~20% encode time for negligible size gain on screenshots. The
    # stored frame is decoded + re-encoded downstream anyway (annotate,
    # crop_png), so wire size is unaffected.
    scaled.save(buf, format="PNG")
    return Capture(
        png_bytes=buf.getvalue(),
        width=scaled.width,
        height=scaled.height,
        monitor_left=monitor["left"],
        monitor_top=monitor["top"],
        scale=ratio,
    )


def capture_virtual_desktop(max_edge: int = SCREENSHOT_MAX_EDGE) -> Capture:
    with mss.mss() as sct:
        monitor = dict(sct.monitors[0])
    return _capture_monitor(monitor, max_edge)


def capture_primary(max_edge: int = SCREENSHOT_MAX_EDGE) -> Capture:
    with mss.mss() as sct:
        monitor = dict(sct.monitors[1])
    return _capture_monitor(monitor, max_edge)


if __name__ == "__main__":
    from pathlib import Path

    set_dpi_aware()
    cap = capture_primary()
    out = Path(__file__).parent / "logs" / "screen_test.png"
    out.write_bytes(cap.png_bytes)
    print(f"Saved {out}")
    print(f"Captured {cap.width}x{cap.height} at scale {cap.scale:.3f}")
    print(f"Monitor origin: ({cap.monitor_left}, {cap.monitor_top})")
    cx, cy = cap.to_screen_coords(cap.width // 2, cap.height // 2)
    print(f"Center of image -> screen coords ({cx}, {cy})")
