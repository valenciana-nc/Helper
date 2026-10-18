"""Temporary, credential-free Windows window for the opt-in UIA integration test."""
from __future__ import annotations

import json
import sys

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import QApplication, QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget


def main():
    app = QApplication(sys.argv)
    widget = QWidget()
    widget.setWindowTitle("Helper isolated UIA fixture")
    layout = QVBoxLayout(widget)
    label = QLabel("Fixture Name")
    field = QLineEdit()
    field.setAccessibleName("Fixture Name")
    label.setBuddy(field)
    button = QPushButton("Fixture Save")
    button.setAccessibleName("Fixture Save")
    result = QLabel("Not saved")
    button.clicked.connect(lambda: result.setText("Saved " + field.text()))
    for control in (label, field, button, result):
        layout.addWidget(control)
    widget.resize(400, 200)
    widget.show()
    print(json.dumps({"hwnd": int(widget.winId())}), flush=True)
    QTimer.singleShot(25000, app.quit)
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
