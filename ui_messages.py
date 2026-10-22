"""Selectable, wrapping message bubbles for dashboard conversation transcripts."""
from __future__ import annotations

import math

from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QFontMetrics, QTextOption
from PyQt6.QtWidgets import QFrame, QLabel, QSizePolicy, QTextEdit, QVBoxLayout, QWidget

import theme


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
