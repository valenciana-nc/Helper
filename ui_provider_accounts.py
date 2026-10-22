"""Official-console links and explicit API-key connections for Claude and Grok."""
from __future__ import annotations

from dataclasses import dataclass
import webbrowser

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

import config
import theme


@dataclass(frozen=True)
class ProviderAccount:
    provider: str
    name: str
    key_setting: str
    console_url: str


PROVIDER_ACCOUNTS = (
    ProviderAccount("anthropic", "Claude", "HELPER_ANTHROPIC_API_KEY", "https://platform.claude.com/settings/keys"),
    ProviderAccount("xai", "Grok", "HELPER_XAI_API_KEY", "https://console.x.ai/team/default/api-keys"),
)


class ProviderAccountCard(QFrame):
    save_requested = pyqtSignal(str, str, str, bool)
    remove_requested = pyqtSignal(str)

    def __init__(self, account: ProviderAccount, saved_key: str, model: str) -> None:
        super().__init__()
        self.account = account
        self.saved_key = saved_key
        self.setObjectName("SurfaceCard")
        self.setProperty("provider", account.provider)
        self.setStyleSheet(theme.qss(theme.CARD_QSS))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 14)
        layout.setSpacing(10)
        heading = QLabel(f"Sign in with {account.name}")
        heading.setFont(theme.font(11, QFont.Weight.DemiBold))
        layout.addWidget(heading)
        description = QLabel(
            f"Sign in on the official {account.name} API console, create an API key, and paste it below. "
            "Helper uses that key—not your chat subscription. API usage is billed separately."
        )
        description.setWordWrap(True)
        description.setFont(theme.font(9))
        description.setStyleSheet(theme.inline("color: {text_muted}", "border: none"))
        layout.addWidget(description)
        sign_in_row = QHBoxLayout()
        sign_in_row.addStretch()
        self.sign_in_button = self._button(f"Sign in with {account.name}", primary=True)
        self.sign_in_button.setToolTip("Opens the official API console. Browser sign-in alone does not connect Helper.")
        self.sign_in_button.clicked.connect(self.open_sign_in)
        sign_in_row.addWidget(self.sign_in_button)
        layout.addLayout(sign_in_row)

        form = QFormLayout()
        form.setRowWrapPolicy(QFormLayout.RowWrapPolicy.WrapLongRows)
        self.key_input = QLineEdit()
        self.key_input.setAccessibleName(f"{account.name} API key")
        self.key_input.setPlaceholderText(f"Paste your {account.name} API key")
        self.key_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.key_input.setStyleSheet(theme.qss(theme.INPUT_QSS))
        self.key_input.setText(saved_key)
        key_row = QHBoxLayout()
        key_row.addWidget(self.key_input, 1)
        self.show_key_button = self._button("Show")
        self.show_key_button.setCheckable(True)
        self.show_key_button.toggled.connect(self._toggle_key_visibility)
        key_row.addWidget(self.show_key_button)
        form.addRow("API key", key_row)
        self.model_input = QComboBox()
        self.model_input.setEditable(True)
        self.model_input.setStyleSheet(theme.qss(theme.INPUT_QSS))
        self.model_input.addItems(config.PROVIDER_MODELS[account.provider])
        self.model_input.setCurrentText(model)
        self.model_input.setAccessibleName(f"{account.name} model")
        form.addRow("Model", self.model_input)
        layout.addLayout(form)

        self.status_label = QLabel("API key saved (not validated)." if saved_key else "Not connected to Helper.")
        self.status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.status_label.setWordWrap(True)
        self.status_label.setFont(theme.font(9))
        self.status_label.setStyleSheet(theme.inline("color: {text_muted}", "border: none"))
        layout.addWidget(self.status_label)
        actions = QHBoxLayout()
        self.remove_button = self._button("Remove key")
        self.remove_button.setEnabled(bool(saved_key))
        self.remove_button.clicked.connect(lambda: self.remove_requested.emit(account.provider))
        actions.addWidget(self.remove_button)
        actions.addStretch()
        self.save_button = self._button("Save")
        self.save_button.clicked.connect(lambda: self._request_save(False))
        actions.addWidget(self.save_button)
        self.restart_button = self._button("Use and restart", primary=True)
        self.restart_button.clicked.connect(lambda: self._request_save(True))
        actions.addWidget(self.restart_button)
        layout.addLayout(actions)

    @staticmethod
    def _button(text: str, *, primary: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setStyleSheet(theme.qss(theme.BUTTON_QSS if primary else theme.GHOST_BUTTON_QSS))
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def refresh_saved_key(self, value: str) -> None:
        """Reflect external settings saves without overwriting an unsaved key draft."""
        changed = value != self.saved_key
        if self.key_input.text() == self.saved_key:
            self.key_input.setText(value)
        self.saved_key = value
        self.remove_button.setEnabled(bool(value))
        if changed:
            self.status_label.setText("API key saved (not validated)." if value else "Not connected to Helper.")

    def _toggle_key_visibility(self, visible: bool) -> None:
        self.key_input.setEchoMode(QLineEdit.EchoMode.Normal if visible else QLineEdit.EchoMode.Password)
        self.show_key_button.setText("Hide" if visible else "Show")

    def open_sign_in(self) -> None:
        try:
            opened = webbrowser.open(self.account.console_url, new=2)
        except Exception:
            opened = False
        self.status_label.setText(
            "After signing in on the provider site, paste an API key here to connect Helper."
            if opened else "Could not open your browser. Open the official provider API console manually."
        )

    def _request_save(self, restart: bool) -> None:
        key = self.key_input.text().strip()
        model = self.model_input.currentText().strip()
        if not key or not model:
            self.status_label.setText("Enter an API key and model first.")
            return
        if any(character.isspace() for character in key) or any(character in model for character in "\r\n"):
            self.status_label.setText("The API key or model contains invalid whitespace.")
            return
        self.save_requested.emit(self.account.provider, key, model, restart)
