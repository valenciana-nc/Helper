import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QImage, QPainter, QColor
from PyQt6.QtWidgets import QApplication

from ui_widget import FloatingCircle


def render_states():
    app = QApplication(sys.argv)
    out_dir = Path(__file__).parent
    out_dir.mkdir(exist_ok=True)

    cases = [
        ("help_idle", FloatingCircle.HELP, FloatingCircle.IDLE, False),
        ("help_listening", FloatingCircle.HELP, FloatingCircle.LISTENING, False),
        ("help_muted", FloatingCircle.HELP, FloatingCircle.IDLE, True),
        ("active_idle", FloatingCircle.ACTIVE, FloatingCircle.IDLE, False),
    ]

    for name, mode, state, muted in cases:
        w = FloatingCircle()
        w.set_mode(mode)
        w.set_state(state)
        w.set_muted(muted)
        # Force a couple of paint ticks so the orb animation settles
        for _ in range(5):
            w.repaint()
            app.processEvents()

        img = QImage(QSize(w.WIDGET_W * 4, w.WIDGET_H * 4), QImage.Format.Format_ARGB32_Premultiplied)
        img.fill(QColor(40, 50, 70))  # darkish backdrop to see the pill clearly
        p = QPainter(img)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        p.scale(4, 4)
        w.render(p)
        p.end()
        path = out_dir / f"preview_{name}.png"
        img.save(str(path))
        print("Wrote", path)


if __name__ == "__main__":
    render_states()
