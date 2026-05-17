from __future__ import annotations

import sys

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QFont
from PyQt6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)


class MessageBubble(QFrame):
    def __init__(self, text: str, is_user: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text = text
        self._is_user = is_user
        self._build_ui()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)

        label = QLabel(self._text)
        label.setWordWrap(True)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setFont(QFont("Segoe UI", 10))

        if self._is_user:
            label.setStyleSheet("color: white;")
            self.setStyleSheet(
                """
                MessageBubble {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 255, 255, 30), stop:1 rgba(255, 255, 255, 10));
                    border-radius: 14px;
                    border-bottom-right-radius: 4px;
                    border-top: 1px solid rgba(255, 255, 255, 60);
                    border-left: 1px solid rgba(255, 255, 255, 40);
                    border-right: 1px solid rgba(255, 255, 255, 10);
                    border-bottom: 1px solid rgba(255, 255, 255, 10);
                }
                """
            )
        else:
            label.setStyleSheet("color: #E6EDF5;")
            self.setStyleSheet(
                """
                MessageBubble {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 255, 255, 15), stop:1 rgba(255, 255, 255, 5));
                    border-radius: 14px;
                    border-bottom-left-radius: 4px;
                    border-top: 1px solid rgba(255, 255, 255, 40);
                    border-left: 1px solid rgba(255, 255, 255, 25);
                    border-right: 1px solid rgba(255, 255, 255, 5);
                    border-bottom: 1px solid rgba(255, 255, 255, 5);
                }
                """
            )

        layout.addWidget(label)


class ChatWindow(QWidget):
    submitted = pyqtSignal(str)
    closed = pyqtSignal()
    close_requested = pyqtSignal()

    HELP = "help"
    ACTIVE = "active"
    HELP_INPUT_PLACEHOLDER = "Ask helper how to..."
    ACTIVE_INPUT_PLACEHOLDER = "Tell Helper what to do..."

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._drag_offset = None
        self._mode = self.HELP
        self._build_window()
        self._build_ui()

    def _build_window(self) -> None:
        self.setWindowTitle("Helper")
        self.setWindowFlags(Qt.WindowType.Window)
        self.setMinimumSize(420, 360)
        self.resize(560, 560)

    def _build_ui(self) -> None:
        self._frame = QFrame(self)
        self._frame.setObjectName("MainFrame")
        self._frame.setStyleSheet(
            """
            #MainFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 255, 255, 25), stop:1 rgba(255, 255, 255, 5));
                border-radius: 18px;
                border-top: 1px solid rgba(255, 255, 255, 60);
                border-left: 1px solid rgba(255, 255, 255, 40);
                border-right: 1px solid rgba(255, 255, 255, 10);
                border-bottom: 1px solid rgba(255, 255, 255, 10);
            }
            """
        )

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self._frame)

        main_layout = QVBoxLayout(self._frame)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self._header = QFrame()
        self._header.setFixedHeight(58)
        self._header.setStyleSheet(
            "background-color: transparent; border-bottom: 1px solid rgba(255, 255, 255, 18); border-radius: 0px;"
        )
        header_layout = QHBoxLayout(self._header)
        header_layout.setContentsMargins(18, 0, 12, 0)

        title_wrap = QVBoxLayout()
        title_wrap.setContentsMargins(0, 0, 0, 0)
        title_wrap.setSpacing(2)

        title = QLabel("Helper")
        title.setFont(QFont("Segoe UI", 13, QFont.Weight.Bold))
        title.setStyleSheet("color: white; border: none;")

        subtitle = QLabel("Desktop assistant")
        subtitle.setFont(QFont("Segoe UI", 9))
        subtitle.setStyleSheet("color: rgba(230, 237, 245, 150); border: none;")

        title_wrap.addWidget(title)
        title_wrap.addWidget(subtitle)

        close_button = QPushButton("x")
        close_button.setFixedSize(28, 28)
        close_button.setStyleSheet(
            """
            QPushButton {
                background-color: transparent;
                color: #9BA9B8;
                border: none;
                font-family: 'Segoe UI';
                font-size: 13pt;
            }
            QPushButton:hover {
                color: white;
                background-color: rgba(255, 255, 255, 18);
                border-radius: 14px;
            }
            """
        )
        close_button.clicked.connect(self.close)

        header_layout.addLayout(title_wrap)
        header_layout.addStretch()
        header_layout.addWidget(close_button)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.setStyleSheet(
            """
            QScrollArea {
                border: none;
                background-color: transparent;
            }
            QScrollBar:vertical {
                border: none;
                background: transparent;
                width: 8px;
                margin: 8px 4px 8px 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 36);
                min-height: 20px;
                border-radius: 4px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 58);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                border: none;
                background: none;
            }
            """
        )

        self._message_container = QWidget()
        self._message_container.setStyleSheet("background-color: transparent;")
        self._message_layout = QVBoxLayout(self._message_container)
        self._message_layout.setContentsMargins(18, 18, 18, 18)
        self._message_layout.setSpacing(12)
        self._message_layout.addStretch()
        self._scroll_area.setWidget(self._message_container)

        input_frame = QFrame()
        input_frame.setStyleSheet(
            "background-color: transparent; border-top: 1px solid rgba(255, 255, 255, 18); border-radius: 0px;"
        )
        input_layout = QVBoxLayout(input_frame)
        input_layout.setContentsMargins(18, 14, 18, 18)

        self._input = QLineEdit()
        self._input.setPlaceholderText(self._input_placeholder_for_mode())
        self._input.setStyleSheet(
            """
            QLineEdit {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 255, 255, 15), stop:1 rgba(255, 255, 255, 5));
                border-top: 1px solid rgba(255, 255, 255, 40);
                border-left: 1px solid rgba(255, 255, 255, 40);
                border-right: 1px solid rgba(255, 255, 255, 10);
                border-bottom: 1px solid rgba(255, 255, 255, 10);
                border-radius: 13px;
                padding: 11px 14px;
                color: white;
                font-family: 'Segoe UI';
                font-size: 11pt;
            }
            QLineEdit:focus {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:1, stop:0 rgba(255, 255, 255, 30), stop:1 rgba(255, 255, 255, 10));
                border: 1px solid rgba(255, 255, 255, 80);
                border-bottom: 1px solid rgba(255, 255, 255, 40);
                border-right: 1px solid rgba(255, 255, 255, 40);
            }
            """
        )
        self._input.returnPressed.connect(self._submit)
        input_layout.addWidget(self._input)

        main_layout.addWidget(self._header)
        main_layout.addWidget(self._scroll_area)
        main_layout.addWidget(input_frame)

    def set_mode(self, mode: str) -> None:
        normalized = (mode or "").lower()
        if normalized not in {self.HELP, self.ACTIVE}:
            raise ValueError(f"Unsupported chat mode: {mode}")
        if normalized == self._mode:
            return
        self._mode = normalized
        self._input.setPlaceholderText(self._input_placeholder_for_mode())

    def _input_placeholder_for_mode(self) -> str:
        if self._mode == self.HELP:
            return self.HELP_INPUT_PLACEHOLDER
        return self.ACTIVE_INPUT_PLACEHOLDER

    def add_message(self, text: str, is_user: bool) -> None:
        text = (text or "").strip()
        if not text:
            return

        bubble = MessageBubble(text, is_user)
        row = QHBoxLayout()
        if is_user:
            row.addStretch()
            row.addWidget(bubble)
        else:
            row.addWidget(bubble)
            row.addStretch()

        self._message_layout.insertLayout(self._message_layout.count() - 1, row)
        self._scroll_to_bottom()
        QTimer.singleShot(0, self._adjust_size)

    def _adjust_size(self) -> None:
        self._frame.resize(self.size())

    def show_chat(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

    def hide_window(self) -> None:
        self.showMinimized()
        self.closed.emit()

    def toggle_visibility(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide_window()
            return
        self.show_chat()

    def keyPressEvent(self, event) -> None:  # type: ignore[override]
        if event.key() == Qt.Key.Key_Escape:
            self.hide_window()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:  # type: ignore[override]
        event.accept()
        self.close_requested.emit()

    def resizeEvent(self, event) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        self._frame.resize(self.size())

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = None
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(
            0,
            lambda: self._scroll_area.verticalScrollBar().setValue(
                self._scroll_area.verticalScrollBar().maximum()
            ),
        )

    def _submit(self) -> None:
        text = self._input.text().strip()
        if not text:
            return
        self._input.clear()
        self.submitted.emit(text)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = ChatWindow()
    window.add_message("Hello. I'm Helper.", False)
    window.add_message("Show me my schedule for today.", True)
    window.add_message("I can help with that.", False)
    window.show_chat()
    raise SystemExit(app.exec())
