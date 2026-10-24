"""Provider API-key cards and native Claude/Grok subscription sign-in."""
from __future__ import annotations

from dataclasses import dataclass
import threading
from types import ModuleType
import webbrowser

from PyQt6.QtCore import QObject, Qt, pyqtSignal
from PyQt6.QtGui import QFont
from PyQt6.QtWidgets import QComboBox, QFormLayout, QFrame, QHBoxLayout, QLabel, QLineEdit, QPushButton, QVBoxLayout

import config
from model_catalogue import ModelCatalogue, load_provider_catalogue
import oauth_anthropic
import oauth_xai
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
    refresh_models_requested = pyqtSignal(str)
    credentials_changed = pyqtSignal(str)

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
        self.description = description
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
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_input, 1)
        self.refresh_models_button = self._button("Refresh models")
        self.refresh_models_button.clicked.connect(lambda: self.refresh_models_requested.emit(account.provider))
        model_row.addWidget(self.refresh_models_button)
        form.addRow("Model", model_row)
        layout.addLayout(form)
        self.model_catalogue_hint = QLabel()
        self.model_catalogue_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.model_catalogue_hint.setWordWrap(True)
        self.model_catalogue_hint.setFont(theme.font(9))
        self.model_catalogue_hint.setStyleSheet(theme.inline('color: {text_muted}', 'border: none'))
        layout.addWidget(self.model_catalogue_hint)
        self.set_model_catalogue(load_provider_catalogue(account.provider))

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

    def set_model_catalogue(self, catalogue: ModelCatalogue) -> None:
        """The card selects a computer-use model: list vision models, preserve custom drafts."""
        draft = self.model_input.currentText()
        blocked = self.model_input.blockSignals(True)
        try:
            self.model_input.clear()
            for model in catalogue.models:
                if not model.image_input:
                    continue
                self.model_input.addItem(model.id)
                self.model_input.setItemData(self.model_input.count() - 1, model.label, Qt.ItemDataRole.ToolTipRole)
            self.model_input.setCurrentText(draft)
        finally:
            self.model_input.blockSignals(blocked)
        text = f'{catalogue.source}. {self.model_input.count()} image-capable models. Access depends on your account.'
        if catalogue.warning:
            text += ' ' + catalogue.warning
        self.model_catalogue_hint.setText(text)

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


class _GrokLoginWorker(QObject):
    device_ready = pyqtSignal(str, str)
    finished = pyqtSignal(bool, str)

    def __init__(self) -> None:
        super().__init__()
        self.cancel = threading.Event()

    def run(self) -> None:
        try:
            oauth_xai.login(lambda device: self.device_ready.emit(device.user_code, device.verification_uri), self.cancel)
        except oauth_xai.LoginError as exc:
            self.finished.emit(False, str(exc))
        except Exception:
            self.finished.emit(False, "Grok sign-in failed. Please try again.")
        else:
            self.finished.emit(True, "")


class _ClaudeLoginWorker(QObject):
    auth_ready = pyqtSignal(str)
    finished = pyqtSignal(bool, str)

    def __init__(self, method: str) -> None:
        super().__init__()
        self.method = method
        self.cancel = threading.Event()
        self.handle: oauth_anthropic.LoginHandle | None = None

    def run(self) -> None:
        ok, message = False, ""
        try:
            self.handle = oauth_anthropic.start_login(self.cancel, method=self.method)
            self.auth_ready.emit(self.handle.auth_url)
            self.handle.wait()
            ok = True
        except oauth_anthropic.LoginError as exc:
            message = str(exc)
        except Exception:
            message = "Claude sign-in failed. Please try again."
        finally:
            if self.handle is not None:
                self.handle.close()
            self.handle = None
        self.finished.emit(ok, message)


class SubscriptionAccountCard(ProviderAccountCard):
    subscription_use_requested = pyqtSignal(str)
    auth: ModuleType
    login_description: str
    console_label: str
    sign_in_hint: str

    def __init__(self, account: ProviderAccount, saved_key: str, model: str) -> None:
        super().__init__(account, saved_key, model)
        self._login_worker: _GrokLoginWorker | _ClaudeLoginWorker | None = None
        self.description.setText(self.login_description)
        self.api_console_button = self.sign_in_button
        self.api_console_button.setText(self.console_label)
        section = QFrame()
        section.setStyleSheet("border: none; background: transparent;")
        layout = QVBoxLayout(section)
        self._oauth_layout = layout
        layout.setContentsMargins(0, 0, 0, 0)
        self.oauth_status_label = QLabel("")
        self.oauth_status_label.setTextFormat(Qt.TextFormat.PlainText)
        self.oauth_status_label.setWordWrap(True)
        layout.addWidget(self.oauth_status_label)
        self.device_code_label = QLabel("")
        self.device_code_label.setTextFormat(Qt.TextFormat.PlainText)
        self.device_code_label.setWordWrap(True)
        self.device_code_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.device_code_label.hide()
        layout.addWidget(self.device_code_label)
        actions = QHBoxLayout()
        self.sign_in_button = self._button(f"Sign in with {account.name}", primary=True)
        self.sign_in_button.setToolTip(self.sign_in_hint)
        self.sign_in_button.clicked.connect(self._start_subscription_login)
        actions.addWidget(self.sign_in_button)
        self.cancel_login_button = self._button("Cancel sign-in")
        self.cancel_login_button.clicked.connect(self.cancel_login)
        self.cancel_login_button.hide()
        actions.addWidget(self.cancel_login_button)
        self.oauth_sign_out_button = self._button(f"Sign out of {account.name}")
        self.oauth_sign_out_button.clicked.connect(self._sign_out_subscription)
        actions.addWidget(self.oauth_sign_out_button)
        layout.addLayout(actions)
        self.use_subscription_button = self._button("Use subscription and restart", primary=True)
        self.use_subscription_button.clicked.connect(self._use_subscription)
        layout.addWidget(self.use_subscription_button)
        self.layout().insertWidget(2, section)
        self.refresh_subscription()

    def refresh_subscription(self) -> None:
        connected = self.auth.is_signed_in()
        self.use_subscription_button.setEnabled(connected and self._login_worker is None)
        self.oauth_sign_out_button.setVisible(connected)
        if self._login_worker is None:
            name = self.account.name
            self.sign_in_button.setText(f"Re-authenticate {name}" if connected else f"Sign in with {name}")
            self.oauth_status_label.setText(
                f"{name} subscription sign-in saved. Use subscription and restart to apply."
                if connected else f"Not signed in to {name}."
            )

    def _start_subscription_login(self) -> None:
        if self._login_worker is not None:
            return
        worker = self._create_login_worker()
        self._login_worker = worker
        self._connect_login_worker(worker)
        worker.finished.connect(self._on_subscription_login_finished)
        self.sign_in_button.setEnabled(False)
        self.use_subscription_button.setEnabled(False)
        self.cancel_login_button.setEnabled(True)
        self.cancel_login_button.show()
        self.device_code_label.clear()
        self.device_code_label.hide()
        self.oauth_status_label.setText(f"Starting {self.account.name} sign-in…")
        self._set_login_active(True)
        # A daemon worker keeps Qt responsive and cannot hold app shutdown open.
        threading.Thread(target=worker.run, name=f"helper-{self.account.provider}-login", daemon=True).start()

    def _create_login_worker(self) -> _GrokLoginWorker | _ClaudeLoginWorker:
        raise NotImplementedError

    def _connect_login_worker(self, worker) -> None:
        raise NotImplementedError

    def _set_login_active(self, active: bool) -> None:
        pass

    def cancel_login(self) -> None:
        if self._login_worker is not None:
            self._login_worker.cancel.set()
            self.cancel_login_button.setEnabled(False)
            self.oauth_status_label.setText(f"Cancelling {self.account.name} sign-in…")

    def _on_subscription_login_finished(self, ok: bool, message: str) -> None:
        self._login_worker = None
        self.cancel_login_button.hide()
        self.sign_in_button.setEnabled(True)
        self.device_code_label.clear()
        self.device_code_label.hide()
        self._set_login_active(False)
        self.refresh_subscription()
        if not ok:
            self.oauth_status_label.setText(message)
        else:
            self.credentials_changed.emit(self.account.provider)

    def _sign_out_subscription(self) -> None:
        self.cancel_login()
        try:
            self.auth.sign_out()
        except self.auth.LoginError as exc:
            self.oauth_status_label.setText(str(exc))
            return
        self.refresh_subscription()
        self.oauth_status_label.setText(f"Signed out of {self.account.name}. Your API key and other sign-ins are unchanged.")
        self.credentials_changed.emit(self.account.provider)

    def _use_subscription(self) -> None:
        if not self.auth.is_signed_in():
            self.oauth_status_label.setText(f"Sign in with {self.account.name} first.")
            return
        model = self.model_input.currentText().strip()
        if not model or any(c in model for c in "\r\n"):
            self.oauth_status_label.setText(f"Enter a valid {self.account.name} model first.")
            return
        self.subscription_use_requested.emit(model)


class GrokAccountCard(SubscriptionAccountCard):
    auth = oauth_xai
    login_description = (
        "Connect an eligible SuperGrok or X Premium subscription with a browser approval code. "
        "Or use an xAI API key below (separate API billing). Model access and limits depend on your account."
    )
    console_label = "Open xAI API console"
    sign_in_hint = "Approve a device code on xAI's site using your Grok/X subscription."

    def _create_login_worker(self) -> _GrokLoginWorker:
        return _GrokLoginWorker()

    def _connect_login_worker(self, worker: _GrokLoginWorker) -> None:
        worker.device_ready.connect(self._on_device_ready)

    def _on_device_ready(self, code: str, uri: str) -> None:
        if self._login_worker is None or self._login_worker.cancel.is_set():
            return
        self.device_code_label.setText(f"Approval code: {code}\n{uri}")
        self.device_code_label.show()
        try:
            opened = webbrowser.open(uri, new=2)
        except Exception:
            opened = False
        self.oauth_status_label.setText(
            "Approve this code in your browser. Waiting for xAI…" if opened
            else "Could not open your browser. Open the address below and enter the approval code."
        )


class ClaudeAccountCard(SubscriptionAccountCard):
    auth = oauth_anthropic
    login_description = (
        "Sign in with an eligible Claude Pro/Max account using browser approval or a copied code. "
        "Uses Pi's Claude Code OAuth compatibility flow; Anthropic may restrict third-party access. "
        "An Anthropic API key below remains a separate, billed alternative."
    )
    console_label = "Open Anthropic API console"
    sign_in_hint = "Sign in on claude.ai using the Claude Pro/Max OAuth flow."

    def __init__(self, account: ProviderAccount, saved_key: str, model: str) -> None:
        super().__init__(account, saved_key, model)
        method_row = QHBoxLayout()
        method_row.addWidget(QLabel("Login method"))
        self.login_method_input = QComboBox()
        self.login_method_input.setStyleSheet(theme.qss(theme.INPUT_QSS))
        self.login_method_input.addItem("Browser callback", "browser")
        self.login_method_input.addItem("Copy code (manual)", "copy_code")
        method_row.addWidget(self.login_method_input, 1)
        self._oauth_layout.insertLayout(0, method_row)
        self.manual_frame = QFrame()
        manual_layout = QVBoxLayout(self.manual_frame)
        manual_layout.setContentsMargins(0, 0, 0, 0)
        hint = QLabel("If needed, paste the final redirect URL or the code Claude shows (code#state).")
        hint.setWordWrap(True)
        manual_layout.addWidget(hint)
        entry = QHBoxLayout()
        self.authorization_input = QLineEdit()
        self.authorization_input.setStyleSheet(theme.qss(theme.INPUT_QSS))
        self.authorization_input.setEchoMode(QLineEdit.EchoMode.Password)
        self.authorization_input.setPlaceholderText("Paste authorization code / final redirect URL")
        self.authorization_input.setAccessibleName("Claude authorization code or redirect URL")
        self.authorization_input.returnPressed.connect(self._submit_authorization)
        entry.addWidget(self.authorization_input, 1)
        self.submit_code_button = self._button("Submit code")
        self.submit_code_button.clicked.connect(self._submit_authorization)
        entry.addWidget(self.submit_code_button)
        manual_layout.addLayout(entry)
        self.manual_frame.hide()
        self._oauth_layout.insertWidget(self._oauth_layout.count() - 1, self.manual_frame)

    def _create_login_worker(self) -> _ClaudeLoginWorker:
        return _ClaudeLoginWorker(self.login_method_input.currentData())

    def _connect_login_worker(self, worker: _ClaudeLoginWorker) -> None:
        worker.auth_ready.connect(self._on_auth_url)

    def _set_login_active(self, active: bool) -> None:
        self.login_method_input.setEnabled(not active)
        self.manual_frame.setVisible(active)
        self.submit_code_button.setEnabled(False)
        self.authorization_input.clear()

    def _on_auth_url(self, url: str) -> None:
        if self._login_worker is None or self._login_worker.cancel.is_set():
            return
        self.device_code_label.setText(url)
        self.device_code_label.show()
        self.submit_code_button.setEnabled(True)
        try:
            opened = webbrowser.open(url, new=2)
        except Exception:
            opened = False
        self.oauth_status_label.setText(
            "Complete Claude sign-in in your browser. Waiting for callback or pasted code…" if opened
            else "Could not open your browser. Open the address below, sign in, then paste the final URL/code."
        )

    def _submit_authorization(self) -> None:
        worker = self._login_worker
        handle = worker.handle if isinstance(worker, _ClaudeLoginWorker) else None
        if handle is None:
            self.oauth_status_label.setText("Start Claude sign-in first, then wait for the browser address.")
            return
        try:
            handle.submit_authorization(self.authorization_input.text())
        except oauth_anthropic.LoginError as exc:
            self.oauth_status_label.setText(str(exc))
            return
        self.authorization_input.clear()
        self.submit_code_button.setEnabled(False)
        self.oauth_status_label.setText("Authorization received. Finishing Claude sign-in…")
