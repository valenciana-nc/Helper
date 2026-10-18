from __future__ import annotations

import sys
import math

from PyQt6.QtCore import QEvent, QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QCloseEvent, QFont, QFontMetrics, QTextOption
from PyQt6.QtWidgets import (
    QApplication, QButtonGroup, QFrame, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QSizePolicy, QTextEdit, QVBoxLayout, QWidget,
)

import theme
from ui_lava import LavaOrb


class MessageText(QTextEdit):
    """Selectable text that grows vertically, including long URLs and paths."""
    def __init__(self, text: str) -> None:
        super().__init__()
        self.setReadOnly(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setFont(theme.font(11))
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setWordWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        self.document().setDocumentMargin(0)
        self.setPlainText(text)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.document().documentLayout().documentSizeChanged.connect(self._fit_height)

    def _fit_height(self, *_args) -> None:
        height = math.ceil(self.document().size().height()) + 2
        if height != self.height():
            self.setFixedHeight(height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self.document().setTextWidth(self.viewport().width())
        self._fit_height()


class MessageBubble(QFrame):
    def __init__(self, text: str, is_user: bool, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._text, self._is_user = text, is_user
        self.setObjectName('MessageBubble')
        self.setMinimumWidth(0)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(5)
        role = QLabel('You' if is_user else 'Helper')
        role.setFont(theme.font(9, QFont.Weight.DemiBold))
        role.setStyleSheet(theme.inline('color: {accent}', 'border: none', 'background: transparent'))
        layout.addWidget(role)
        self._label = MessageText(text)
        fg, bg, border = ('user_fg', 'user_bg', 'user_border') if is_user else ('text_primary', 'bg_card', 'border_light')
        self._label.setStyleSheet(f'color: {theme.value(fg)}; background: transparent; border: none;')
        corner = 'bottom-right' if is_user else 'bottom-left'
        self.setStyleSheet(f'QFrame#MessageBubble {{ background: {theme.value(bg)}; '
                           f'border: 1px solid {theme.value(border)}; border-radius: 16px; '
                           f'border-{corner}-radius: 5px; }}')
        layout.addWidget(self._label)

    def set_available_width(self, width: int) -> None:
        metrics = QFontMetrics(self._label.font())
        content = max((metrics.horizontalAdvance(line) for line in self._text.splitlines()), default=80)
        actual = min(max(120, width), max(140, content + 34))
        self.setFixedWidth(actual)
        self._label.setFixedWidth(actual - 32)
        self._label.document().setTextWidth(actual - 32)
        self._label._fit_height()


class ChatWindow(QWidget):
    submitted = pyqtSignal(str)
    closed = pyqtSignal()
    close_requested = pyqtSignal()
    dashboard_requested = pyqtSignal()
    mode_select_requested = pyqtSignal(str)

    HELP = 'help'
    ACTIVE = 'active'
    HELP_INPUT_PLACEHOLDER = 'Ask helper how to...'
    ACTIVE_INPUT_PLACEHOLDER = 'Tell Helper what to do...'

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._mode = self.HELP
        self._bubbles: list[MessageBubble] = []
        self.setWindowTitle('Helper')
        self.setWindowFlags(Qt.WindowType.Window)
        self.setMinimumSize(420, 480)
        self.resize(620, 700)
        self.setStyleSheet(theme.qss('ChatWindow {{ background: {bg_window}; color: {text_primary}; }}'))
        self._build_ui()

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self._frame = QFrame()
        self._frame.setObjectName('MainFrame')
        self._frame.setStyleSheet(theme.qss(theme.FRAME_QSS))
        outer.addWidget(self._frame)
        main = QVBoxLayout(self._frame)
        main.setContentsMargins(0, 0, 0, 0)
        main.setSpacing(0)

        self._header = QFrame()
        self._header.setObjectName('ChatHeader')
        self._header.setStyleSheet(theme.qss('QFrame#ChatHeader {{ background: {bg_topbar}; border: none; border-bottom: 1px solid {border_light}; }}'))
        header = QHBoxLayout(self._header)
        header.setContentsMargins(18, 10, 20, 10)
        header.setSpacing(10)
        header.addWidget(LavaOrb(48))
        brand = QVBoxLayout()
        brand.setSpacing(0)
        brand.addWidget(self._label('Helper', 14, 'text_primary', bold=True))
        brand.addWidget(self._label('Your desktop companion', 9, 'text_muted'))
        header.addLayout(brand)
        header.addStretch()
        dashboard = QPushButton('Dashboard')
        dashboard.setStyleSheet(theme.qss(theme.GHOST_BUTTON_QSS))
        dashboard.setCursor(Qt.CursorShape.PointingHandCursor)
        dashboard.clicked.connect(self.dashboard_requested.emit)
        header.addWidget(dashboard)
        main.addWidget(self._header)

        self._scroll_area = QScrollArea()
        self._scroll_area.setWidgetResizable(True)
        self._scroll_area.viewport().installEventFilter(self)
        self._scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self._scroll_area.setStyleSheet(theme.qss(theme.SCROLL_QSS))
        self._message_container = QWidget()
        self._message_container.setStyleSheet('background: transparent;')
        self._message_layout = QVBoxLayout(self._message_container)
        self._message_layout.setContentsMargins(22, 24, 22, 24)
        self._message_layout.setSpacing(16)
        self._welcome = self._build_welcome()
        self._message_layout.addWidget(self._welcome, 1)
        self._message_layout.addStretch()
        self._scroll_area.setWidget(self._message_container)
        main.addWidget(self._scroll_area, 1)

        composer = QFrame()
        composer.setObjectName('Composer')
        composer.setStyleSheet(theme.qss('QFrame#Composer {{ background: {bg_topbar}; border: none; border-top: 1px solid {border_light}; }}'))
        compose = QVBoxLayout(composer)
        compose.setContentsMargins(22, 14, 22, 16)
        compose.setSpacing(10)
        modes = QHBoxLayout()
        modes.setSpacing(6)
        group = QButtonGroup(self)
        group.setExclusive(True)
        self._mode_buttons = {}
        for value, text in ((self.HELP, 'Help'), (self.ACTIVE, 'Act')):
            button = QPushButton(text)
            button.setCheckable(True)
            button.setChecked(value == self._mode)
            button.setStyleSheet(theme.qss(theme.SEGMENTED_BUTTON_QSS))
            button.setCursor(Qt.CursorShape.PointingHandCursor)
            button.setToolTip('Guide me step by step' if value == self.HELP else 'Let Helper carry out my task')
            button.clicked.connect(lambda _checked=False, mode=value: self.mode_select_requested.emit(mode))
            group.addButton(button)
            self._mode_buttons[value] = button
            modes.addWidget(button)
        self._mode_description = self._label('Step-by-step guidance', 9, 'text_muted')
        self._mode_description.setWordWrap(True)
        modes.addSpacing(6)
        modes.addWidget(self._mode_description, 1)
        compose.addLayout(modes)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self._input = QLineEdit()
        self._input.setAccessibleName('Message Helper')
        self._input.setPlaceholderText(self._input_placeholder_for_mode())
        self._input.setMinimumHeight(30)
        self._input.setStyleSheet(theme.qss(theme.INPUT_QSS))
        self._input.returnPressed.connect(self._submit)
        input_row.addWidget(self._input, 1)
        self._send = QPushButton('↑')
        self._send.setAccessibleName('Send message')
        self._send.setToolTip('Send message · Enter')
        self._send.setFixedSize(44, 46)
        self._send.setStyleSheet(theme.qss(theme.BUTTON_QSS) + 'QPushButton { padding: 0; font-size: 20pt; border-radius: 12px; }')
        self._send.setCursor(Qt.CursorShape.PointingHandCursor)
        self._send.setEnabled(False)
        self._input.textChanged.connect(lambda text: self._send.setEnabled(bool(text.strip())))
        self._send.clicked.connect(self._submit)
        input_row.addWidget(self._send)
        compose.addLayout(input_row)
        self._status = self._label('Enter to send  ·  Escape to minimize', 9, 'text_muted')
        self._status.setWordWrap(True)
        compose.addWidget(self._status)
        main.addWidget(composer)

    @staticmethod
    def _label(text: str, size: int, token: str, *, bold: bool = False) -> QLabel:
        label = QLabel(text)
        label.setFont(theme.font(size, QFont.Weight.DemiBold if bold else None))
        label.setStyleSheet(f'color: {theme.value(token)}; background: transparent; border: none;')
        return label

    def _build_welcome(self) -> QWidget:
        welcome = QWidget()
        layout = QVBoxLayout(welcome)
        layout.setContentsMargins(4, 10, 4, 10)
        layout.setSpacing(12)
        layout.addStretch()
        layout.addWidget(LavaOrb(116), alignment=Qt.AlignmentFlag.AlignHCenter)
        title = self._label('What can I help with?', 22, 'text_primary', bold=True)
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        title.setWordWrap(True)
        layout.addWidget(title)
        description = self._label('Find your next step, learn something new,\nor get a task done on your computer.', 10, 'text_secondary')
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(description)
        layout.addSpacing(8)
        for text in ('Show me how to…', 'Help me find…'):
            suggestion = QPushButton(text)
            suggestion.setStyleSheet(theme.qss(theme.GHOST_BUTTON_QSS))
            suggestion.setCursor(Qt.CursorShape.PointingHandCursor)
            suggestion.setMaximumWidth(300)
            suggestion.clicked.connect(lambda _checked=False, prompt=text: self._prefill(prompt.replace('…', ' ')))
            layout.addWidget(suggestion, alignment=Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch()
        return welcome

    def _prefill(self, text: str) -> None:
        self._input.setText(text)
        self._input.setFocus()

    def set_mode(self, mode: str) -> None:
        normalized = (mode or '').lower()
        if normalized not in {self.HELP, self.ACTIVE}:
            raise ValueError(f'Unsupported chat mode: {mode}')
        self._mode = normalized
        self._input.setPlaceholderText(self._input_placeholder_for_mode())
        self._mode_buttons[normalized].setChecked(True)
        self._mode_description.setText('Step-by-step guidance' if normalized == self.HELP else 'Helper carries out your task')

    def set_status(self, text: str) -> None:
        self._status.setText(text.strip() if text.strip() else 'Enter to send  ·  Escape to minimize')

    def _input_placeholder_for_mode(self) -> str:
        return self.HELP_INPUT_PLACEHOLDER if self._mode == self.HELP else self.ACTIVE_INPUT_PLACEHOLDER

    def add_message(self, text: str, is_user: bool) -> None:
        text = (text or '').strip()
        if not text:
            return
        bar = self._scroll_area.verticalScrollBar()
        follow = is_user or bar.maximum() - bar.value() < 60
        self._welcome.hide()
        bubble = MessageBubble(text, is_user)
        self._bubbles.append(bubble)
        self._sync_bubble_widths()
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        if is_user:
            row.addStretch(1)
        row.addWidget(bubble)
        if not is_user:
            row.addStretch(1)
        self._message_layout.insertLayout(self._message_layout.count() - 1, row)
        if follow:
            self._scroll_to_bottom()

    def _sync_bubble_widths(self) -> None:
        width = max(160, int((self._scroll_area.viewport().width() - 44) * .9))
        for bubble in self._bubbles:
            bubble.set_available_width(width)

    def show_chat(self) -> None:
        if self.isMinimized():
            self.showNormal()
        self.show()
        self.raise_()
        self.activateWindow()
        self._input.setFocus()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        theme.apply_window_chrome(self)

    def hide_window(self) -> None:
        self.showMinimized()
        self.closed.emit()

    def toggle_visibility(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide_window()
        else:
            self.show_chat()

    def keyPressEvent(self, event) -> None:
        if event.key() == Qt.Key.Key_Escape:
            self.hide_window()
            event.accept()
            return
        super().keyPressEvent(event)

    def closeEvent(self, event: QCloseEvent) -> None:
        event.accept()
        self.close_requested.emit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._sync_bubble_widths()

    def eventFilter(self, obj, event) -> bool:
        if obj is self._scroll_area.viewport() and event.type() == QEvent.Type.Resize:
            self._sync_bubble_widths()
        return super().eventFilter(obj, event)

    def _scroll_to_bottom(self) -> None:
        QTimer.singleShot(0, lambda: self._scroll_area.verticalScrollBar().setValue(self._scroll_area.verticalScrollBar().maximum()))

    def _submit(self) -> None:
        text = self._input.text().strip()
        if text:
            self._input.clear()
            self.submitted.emit(text)


if __name__ == '__main__':
    app = QApplication(sys.argv)
    theme.apply_application_theme(app)
    window = ChatWindow()
    window.show_chat()
    raise SystemExit(app.exec())
