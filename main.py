from __future__ import annotations

import ctypes
import logging
import subprocess
import sys
import time
import traceback
from pathlib import Path
from threading import Event, Lock, Thread
from typing import TYPE_CHECKING

# DPI awareness is a Windows-only concept — ctypes.windll doesn't exist on
# other platforms, so guard the whole block instead of relying on try/except
# (the old fallback also touched ctypes.windll and crashed at import time).
if sys.platform == "win32":
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

from PyQt6.QtCore import QObject, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QPainter, QPixmap
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from config import (
    DEFAULT_MODE,
    ENV_PATH,
    HOTKEY,
    ROOT,
    STARTUP_LOG,
    SPEAK_TYPED_CHAT,
    setup_logging,
)
from conversation_store import ConversationStore, StoredConversation
import oauth_codex
import theme
from ui_chat import ChatWindow
from ui_dashboard import DashboardWindow
from ui_ghost_cursor import GhostCursorManager
from ui_overlay import OverlayManager
from ui_widget import ChatStatusPill, FloatingCircle
from windows_integration import configure_windows_app_identity, ensure_windows_shortcuts

log = logging.getLogger("helper")

if TYPE_CHECKING:
    from audio_handler import AudioHandler


ALIASES = {
    "control": "ctrl",
    "ctrl_l": "ctrl",
    "ctrl_r": "ctrl",
    "shift_l": "shift",
    "shift_r": "shift",
    "alt_l": "alt",
    "alt_r": "alt",
    "cmd": "meta",
    "cmd_l": "meta",
    "cmd_r": "meta",
    " ": "space",
}

CHAT_TAP_THRESHOLD_SEC = 0.3
CONFIRMATION_TIMEOUT_SEC = 120.0
APP_ICON_PATH = ROOT / "assets" / "helper_logo.ico"
CONVERSATIONS_PATH = ROOT / "data" / "conversations.json"

def helper_app_icon(app: QApplication) -> QIcon:
    from ui_lava import paint_lava_orb

    icon = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        pixmap = QPixmap(size, size)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        inset = size * .08
        paint_lava_orb(painter, QRectF(inset, inset, size - inset * 2, size - inset * 2), 2.4)
        painter.end()
        icon.addPixmap(pixmap)
    return icon


def normalize_key_name(name: str) -> str:
    return ALIASES.get(name.lower(), name.lower())


def key_name(key: object) -> str | None:
    char = getattr(key, "char", None)
    if isinstance(char, str) and char:
        return normalize_key_name(char)

    name = getattr(key, "name", None)
    if isinstance(name, str) and name:
        return normalize_key_name(name)

    return None


class HelplerDesktopApp(QObject):
    widget_state_requested = pyqtSignal(str)
    caption_changed = pyqtSignal(str)
    overlay_highlight_requested = pyqtSignal(
        int, int, int, int, str, int, int, int
    )
    overlay_clear_requested = pyqtSignal()
    ghost_clear_requested = pyqtSignal()
    agent_message_received = pyqtSignal(str)
    user_message_received = pyqtSignal(str)
    chat_toggle_requested = pyqtSignal()
    chat_window_toggle_requested = pyqtSignal()
    chat_show_requested = pyqtSignal()
    chat_status_changed = pyqtSignal(str)

    def __init__(self, app: QApplication, hotkey: str, startup_surface: str = "background") -> None:
        super().__init__()
        if not hotkey.strip():
            raise ValueError("HELPER_HOTKEY cannot be empty.")

        theme.apply_application_theme(app)
        self._app = app
        self._startup_surface = startup_surface
        self._hotkey_label = hotkey
        self._hotkey = {normalize_key_name(part) for part in hotkey.split("+") if part}
        self._widget = FloatingCircle()
        self._widget.set_state(FloatingCircle.IDLE)
        self._app_icon = helper_app_icon(self._app)
        self._app.setWindowIcon(self._app_icon)
        shortcuts = ensure_windows_shortcuts()
        if shortcuts:
            log.info("Helper shortcuts refreshed: %s", ", ".join(str(path) for path in shortcuts))
        self._backend_ready = False
        self._backend_initializing = False
        self._backend_error: str | None = None
        # Cache the keyring-backed sign-in state: _status_snapshot() runs on
        # the dashboard's 2s timer, and each is_signed_in() call is a
        # credential-manager round-trip. Refreshed on auth transitions via
        # _handle_auth_changed().
        self._cached_signed_in: bool | None = None
        # The same typed runtime serves chat, voice, teaching, and active control.
        from engine_bridge import EngineRunner

        self._engine_abort = Event()
        self._engine_runner = EngineRunner(self,
                                   should_abort=self._engine_abort.is_set)
        self._engine_runner.message.connect(self._handle_agent_message)
        self._engine_runner.status.connect(self._on_chat_status_changed)
        self._engine_runner.state.connect(self.widget_state_requested.emit)
        self._engine_runner.highlight.connect(self._on_engine_highlight)
        self._engine_runner.speak.connect(self._speak_reply)
        self._engine_runner.finished.connect(self._on_engine_finished)
        # Each run snapshots its mode; Escape cancels it before the next input.
        if DEFAULT_MODE == FloatingCircle.ACTIVE:
            self._selected_mode = FloatingCircle.ACTIVE
        else:
            self._selected_mode = FloatingCircle.HELP
        self._chat_window = ChatWindow()
        self._chat_window.set_mode(self._selected_mode)
        self._chat_window.setWindowIcon(self._app_icon)
        self._status_pill = ChatStatusPill()
        self._conversation_store = ConversationStore(CONVERSATIONS_PATH)
        self._active_conversation: StoredConversation | None = None
        self._dashboard = DashboardWindow(
            env_path=ENV_PATH,
            status_provider=self._status_snapshot,
            restart_callback=self._handle_restart_requested,
            auth_changed_callback=self._handle_auth_changed,
            conversations_provider=self._conversations_snapshot,
        )
        self._dashboard.setWindowIcon(self._app_icon)
        self._overlay = OverlayManager(app)
        self._ghost_cursor = GhostCursorManager(app)
        self._audio: AudioHandler | None = None
        self._listener = None
        self._mouse_listener = None
        self._lock = Lock()
        self._pressed: set[str] = set()
        self._recording_active = False
        self._recording_started_at = 0.0
        self._busy = False
        self._shutting_down = False
        # The orb's pill is the only visible reply surface while the chat window is closed.
        self._last_run_reply = ""
        self._reply_hide_timer = QTimer(self)
        self._reply_hide_timer.setSingleShot(True)
        self._reply_hide_timer.timeout.connect(self._hide_final_reply)

        self.widget_state_requested.connect(self._widget.set_state)
        self.caption_changed.connect(self._widget.set_caption)
        self.overlay_highlight_requested.connect(self._overlay.show_highlight)
        self.overlay_clear_requested.connect(self._overlay.clear)
        self.ghost_clear_requested.connect(self._ghost_cursor.clear)
        self.agent_message_received.connect(self._handle_agent_message)
        self.user_message_received.connect(self._handle_user_message)
        self.chat_toggle_requested.connect(self._toggle_widget_chat)
        self.chat_window_toggle_requested.connect(self._toggle_chat_window_if_visible)
        self.chat_show_requested.connect(self._show_chat_window)
        self.chat_status_changed.connect(self._on_chat_status_changed)

        self._widget.mode_toggle_requested.connect(self._handle_mode_toggle_requested)
        self._widget.mode_select_requested.connect(self._handle_mode_select_requested)
        self._widget.quit_requested.connect(self._handle_quit_requested)
        self._widget.restart_requested.connect(self._handle_restart_requested)
        self._widget.chat_requested.connect(self._toggle_widget_chat)
        self._widget.chat_submitted.connect(self._handle_chat_submitted)
        self._widget.voice_hold_started.connect(self._start_voice_recording)
        self._widget.voice_hold_finished.connect(self._stop_voice_recording)
        self._widget.chat_open_changed.connect(self._on_widget_chat_open_changed)
        self._widget.dashboard_requested.connect(self._toggle_dashboard)
        self._widget.moved.connect(self._on_widget_moved)

        self._chat_window.dashboard_requested.connect(self._toggle_dashboard)
        self._chat_window.mode_select_requested.connect(self._handle_mode_select_requested)
        self._chat_window.submitted.connect(self._handle_chat_submitted)
        self._chat_window.closed.connect(self._on_chat_closed)
        self._chat_window.close_requested.connect(self._handle_main_window_close_requested)
        self._app.aboutToQuit.connect(self.shutdown)

        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_toggle_action = None
        self._setup_tray()

        self._widget.move(*self._initial_widget_position())
        self._widget.set_mode(self._selected_mode)
        self._widget.show()
        if self._startup_surface == "chat":
            QTimer.singleShot(0, self._show_chat_window)
        elif self._startup_surface == "dashboard":
            QTimer.singleShot(0, self._show_dashboard_window)
        self._sync_tray_actions()
        self._widget.set_state(FloatingCircle.LISTENING)
        QTimer.singleShot(2000, lambda: self._widget.set_state(FloatingCircle.IDLE))
        QTimer.singleShot(800, self._show_first_launch_balloon)
        QTimer.singleShot(50, self._initialize_backend)

    def start(self) -> None:
        log.info("Starting Helper. Loading backend for %s...", self._hotkey_label)
        log.info("Right-click the widget or tray icon for controls.")

    def _initial_widget_position(self) -> tuple[int, int]:
        screen = self._app.primaryScreen()
        if screen is None:
            screens = self._app.screens()
            screen = screens[0] if screens else None
        if screen is None:
            return (80, 80)
        geom = screen.availableGeometry()
        width, height = self._widget.width(), self._widget.height()
        x = geom.left() + (geom.width() - width) // 2
        y = geom.bottom() - height
        x = max(geom.left(), min(x, geom.right() - width))
        y = max(geom.top(), min(y, geom.bottom() - height))
        return (x, y)

    def _show_first_launch_balloon(self) -> None:
        if self._tray_icon is None:
            return
        self._tray_icon.showMessage(
            "Helper is running",
            "Look at the bottom-center of your screen. Right-click the circle for controls.",
            QSystemTrayIcon.MessageIcon.Information,
            5000,
        )

    def _initialize_backend(self) -> None:
        if self._backend_ready or self._backend_initializing or self._shutting_down:
            return

        self._backend_initializing = True
        try:
            from pynput import keyboard, mouse

            from audio_handler import AudioHandler

            try:
                audio = AudioHandler()
            except Exception:
                audio = None
                log.exception("Voice unavailable; keyboard and typed chat remain available")
            listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )
            mouse_listener = mouse.Listener(on_click=self._on_mouse_click)

            self._audio = audio
            self._listener = listener
            self._mouse_listener = mouse_listener

            if self._shutting_down:
                return

            self._listener.start()
            self._mouse_listener.start()
            self._backend_ready = True
            self._backend_error = None
            self.widget_state_requested.emit(FloatingCircle.IDLE)
            log.info(
                "Helper ready (observation-driven engine). Tap %s to toggle chat or hold it to record.",
                self._hotkey_label,
            )
        except Exception as exc:
            log.exception("Backend init failed")
            self._audio = None
            self._listener = None
            self._mouse_listener = None
            self._backend_ready = False
            self._backend_error = str(exc)
            self.caption_changed.emit(f"Startup failed: {str(exc)[:60]}")
            self.widget_state_requested.emit(FloatingCircle.IDLE)
            if self._tray_icon is not None:
                self._tray_icon.showMessage(
                    "Helper startup failed",
                    str(exc)[:200],
                    QSystemTrayIcon.MessageIcon.Critical,
                    8000,
                )
        finally:
            self._backend_initializing = False

    def shutdown(self) -> None:
        if self._shutting_down:
            return
        self._shutting_down = True
        self._engine_abort.set()
        self._persist_active_conversation()
        if self._listener is not None:
            self._listener.stop()
        if self._mouse_listener is not None:
            self._mouse_listener.stop()
        if self._audio is not None:
            self._audio.cancel_recording()
            self._audio.stop_playback()
        self._chat_window.hide()
        self._status_pill.hide()
        self._dashboard.hide()
        self._overlay.clear()
        self._ghost_cursor.clear()
        self._widget.hide()
        self._status_pill.hide()
        if self._tray_icon is not None:
            self._tray_icon.hide()

    def _persist_active_conversation(self) -> None:
        conversation = self._active_conversation
        if conversation is None or not conversation.has_user_message():
            return
        conversation.title = conversation.derive_title()
        if conversation.ended_at < conversation.started_at:
            conversation.ended_at = time.time()
        try:
            self._conversation_store.save(conversation)
            log.info("Saved conversation %s (%d messages)", conversation.id, len(conversation.messages))
        except Exception:
            log.exception("Saving conversation failed")
        self._active_conversation = None

    def _setup_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            log.warning("System tray unavailable")
            QMessageBox.warning(
                None,
                "Helper",
                "System tray is not available. The floating circle is still on screen - "
                "right-click it for controls.",
            )
            return

        tray_icon = QSystemTrayIcon(self._app)
        tray_icon.setToolTip("Helper")
        tray_icon.setIcon(self._app_icon)

        tray_menu = QMenu()
        self._tray_toggle_action = tray_menu.addAction("Show Helper")
        self._tray_toggle_action.triggered.connect(self._toggle_chat_window)

        dashboard_action = tray_menu.addAction("Dashboard")
        dashboard_action.triggered.connect(self._toggle_dashboard)

        tray_menu.addSeparator()

        restart_action = tray_menu.addAction("Restart")
        restart_action.triggered.connect(self._handle_restart_requested)

        quit_action = tray_menu.addAction("Quit")
        quit_action.triggered.connect(self._handle_quit_requested)

        tray_icon.setContextMenu(tray_menu)
        tray_icon.activated.connect(self._handle_tray_activated)
        tray_icon.show()

        self._tray_icon = tray_icon
        self._sync_tray_actions()

    def _sync_tray_actions(self) -> None:
        if self._tray_toggle_action is None:
            return
        self._tray_toggle_action.setText(
            "Minimize Helper"
            if self._chat_window.isVisible() and not self._chat_window.isMinimized()
            else "Show Helper"
        )

    def _toggle_chat_window(self) -> None:
        self._chat_window.toggle_visibility()
        self._sync_tray_actions()

    def _toggle_chat_window_if_visible(self) -> None:
        # GUI-thread slot for the Esc hotkey: never touch QWidget from the
        # pynput listener thread.
        self._engine_abort.set()
        if self._audio is not None:
            self._audio.stop_playback()
        if self._chat_window.isVisible():
            self._toggle_chat_window()
        self.overlay_clear_requested.emit()
        self._ghost_cursor.clear()
        log.info("Goal aborted by user.")

    def _show_chat_window(self) -> None:
        self._chat_window.show_chat()
        self._sync_tray_actions()

    def _show_dashboard_window(self) -> None:
        self._dashboard.show_dashboard()
        self._sync_tray_actions()

    def _toggle_widget_chat(self) -> None:
        self._widget.toggle_chat_open()
        self._sync_tray_actions()

    def _on_chat_closed(self) -> None:
        self._sync_tray_actions()

    def _on_widget_chat_open_changed(self, opened: bool) -> None:
        if opened:
            self._status_pill.anchor_above(self._widget)
        else:
            self._status_pill.set_text("")
            self._status_pill.hide()
        self._sync_tray_actions()

    def _on_widget_moved(self) -> None:
        if self._status_pill.isVisible():
            self._status_pill.anchor_above(self._widget)

    def _on_chat_status_changed(self, text: str) -> None:
        text = (text or "").strip()
        self._chat_window.set_status(text)
        if text:
            self._reply_hide_timer.stop()
            self._status_pill.set_text(text)
            self._status_pill.anchor_above(self._widget)
            self._status_pill.show()
            self._status_pill.raise_()
        elif not self._reply_hide_timer.isActive():
            self._status_pill.set_text("")
            self._status_pill.hide()

    def _show_final_reply(self, text: str) -> None:
        self._chat_window.set_status("")
        text = (text or "").strip()
        if not text:
            self._hide_final_reply()
            return
        self._status_pill.set_text(text)
        self._status_pill.anchor_above(self._widget)
        self._status_pill.show()
        self._status_pill.raise_()
        self._reply_hide_timer.start(max(6000, min(20000, 2500 + 60 * len(text))))

    def _hide_final_reply(self) -> None:
        self._reply_hide_timer.stop()
        self._status_pill.set_text("")
        self._status_pill.hide()

    def _handle_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self._toggle_chat_window()

    def _toggle_dashboard(self) -> None:
        self._dashboard.toggle_visibility()

    def _status_snapshot(self) -> dict[str, object]:
        if self._cached_signed_in is None:
            self._cached_signed_in = oauth_codex.is_signed_in()
        return {
            "backend_ready": self._backend_ready,
            "backend_initializing": self._backend_initializing,
            "backend_error": self._backend_error,
            "signed_in": self._cached_signed_in,
            "agent_ready": True,  # engine creation is lazy
            "agent_error": None,
            "mode": self._selected_mode,
            "hotkey": self._hotkey_label,
            "log_path": STARTUP_LOG,
            "engine_running": self._engine_runner.is_running(),
        }

    def _handle_auth_changed(self, signed_in: bool) -> None:
        self._cached_signed_in = signed_in
        if signed_in:
            self.agent_message_received.emit("Signed in. Helper is ready.")
        else:
            self.agent_message_received.emit(
                "Signed out. Chat and computer-use are disabled until you sign in again."
            )

    def _handle_quit_requested(self) -> None:
        self.shutdown()
        self._app.quit()

    def _handle_main_window_close_requested(self) -> None:
        # X button minimizes the chat window instead of quitting the entire
        # app (orb, tray icon, hotkeys). Quitting is via the tray/widget
        # "Quit Helper" actions.
        self._chat_window.hide_window()

    def _handle_restart_requested(self) -> None:
        log.info("Restart requested. Launching a fresh instance...")
        try:
            subprocess.Popen(
                [sys.executable, *sys.argv],
                close_fds=True,
            )
        except Exception:
            log.exception("Restart failed")
            return
        self._handle_quit_requested()

    def _handle_mode_toggle_requested(self) -> None:
        order = FloatingCircle.MODE_ORDER
        current = self._widget.mode
        idx = order.index(current) if current in order else 0
        target = order[(idx + 1) % len(order)]
        self._request_mode(target)

    def _handle_mode_select_requested(self, mode: str) -> None:
        self._request_mode(mode)

    def _request_mode(self, mode: str) -> None:
        if mode == self._widget.mode:
            return
        self._set_mode(mode)

    def _set_mode(self, mode: str) -> None:
        if mode not in {FloatingCircle.HELP, FloatingCircle.ACTIVE}:
            raise ValueError("Unknown control mode")
        if self._engine_runner.is_running():
            self._engine_abort.set()
        self._selected_mode = mode
        self._widget.set_mode(mode)
        self._chat_window.set_mode(mode)
        self.overlay_clear_requested.emit()
        self._ghost_cursor.clear()
        log.info("Mode set to %s.", mode.upper())

    def _backend_status_message(self) -> str:
        if self._backend_error:
            return f"Helper startup failed: {self._backend_error}"
        if self._backend_initializing:
            return "Helper is still starting up. Try again in a moment."
        return "Helper backend is not ready yet."

    def _handle_user_message(self, message: str) -> None:
        self._chat_window.add_message(message, True)
        text = (message or "").strip()
        if text:
            self._append_to_active_conversation("user", text)

    def _handle_agent_message(self, message: str) -> None:
        self._chat_window.add_message(message, False)
        text = (message or "").strip()
        if text:
            self._last_run_reply = text
            if self._active_conversation is not None:
                self._append_to_active_conversation("assistant", text)
            self.chat_status_changed.emit(text)

    def _append_to_active_conversation(self, role: str, text: str) -> None:
        if self._active_conversation is None:
            self._active_conversation = StoredConversation.new()
        self._active_conversation.add_message(role, text)
        self._autosave_active_conversation()

    def _autosave_active_conversation(self) -> None:
        conversation = self._active_conversation
        if conversation is None or not conversation.has_user_message():
            return
        conversation.title = conversation.derive_title()
        try:
            self._conversation_store.save(conversation)
        except Exception:
            log.exception("Autosaving conversation failed")

    def _conversations_snapshot(self) -> dict[str, object]:
        try:
            stored = self._conversation_store.load_all()
        except Exception:
            log.exception("Loading conversations failed")
            stored = []

        # Active conversations are autosaved after each message so they survive
        # a crash.  Keep the in-memory copy as the single live row in the
        # dashboard, rather than rendering that same id once from disk and
        # once as LIVE while the session is still open.
        active = self._active_conversation
        if active is not None:
            stored = [conversation for conversation in stored if conversation.id != active.id]

        live = None
        if active is not None and active.has_user_message():
            live = StoredConversation(
                id=active.id,
                started_at=active.started_at,
                ended_at=active.ended_at,
                title=active.derive_title(),
                messages=list(active.messages),
            )
        return {"stored": stored, "live": live}

    def _on_press(self, key: object) -> bool | None:
        if not self._backend_ready:
            return None

        name = key_name(key)
        if name is None:
            return None

        if name == "esc":
            self._engine_abort.set()
            with self._lock:
                recording_active = self._recording_active
                if recording_active:
                    self._recording_active = False
                    self._recording_started_at = 0.0
            if recording_active:
                self._audio.cancel_recording()
                self.caption_changed.emit("")
                self.widget_state_requested.emit(FloatingCircle.IDLE)
                log.info("Recording canceled.")
            else:
                # Never touch QWidget from the pynput thread; the signal
                # queues the toggle/abort to the GUI thread.
                self.chat_window_toggle_requested.emit()
                log.info("Abort requested.")
            return None

        if self._audio is None:
            # No audio: track the press for tap-to-toggle-chat, but no recording.
            with self._lock:
                self._pressed.add(name)
                if not self._busy and self._hotkey.issubset(self._pressed):
                    self._recording_started_at = time.perf_counter()
            return None
        should_start = False
        with self._lock:
            self._pressed.add(name)
            if (
                not self._busy
                and not self._recording_active
                and self._hotkey.issubset(self._pressed)
            ):
                self._recording_active = True
                self._recording_started_at = time.perf_counter()
                should_start = True

        if should_start:
            self._engine_abort.clear()
            try:
                self._audio.start_recording()
            except Exception:
                log.exception("Failed to start recording")
                with self._lock:
                    self._recording_active = False
                    self._recording_started_at = 0.0
                self.widget_state_requested.emit(FloatingCircle.IDLE)
                return None
            self.widget_state_requested.emit(FloatingCircle.LISTENING)
            log.info("Listening...")
        return None

    def _on_release(self, key: object) -> None:
        if not self._backend_ready:
            return

        name = key_name(key)
        if name is None:
            return

        should_finish = False
        should_toggle_chat = False
        with self._lock:
            self._pressed.discard(name)
            if self._recording_active and not self._hotkey.issubset(self._pressed):
                self._recording_active = False
                elapsed = time.perf_counter() - self._recording_started_at
                self._recording_started_at = 0.0
                if elapsed < CHAT_TAP_THRESHOLD_SEC:
                    should_toggle_chat = True
                else:
                    self._busy = True
                    should_finish = True
            elif self._audio is None and not self._hotkey.issubset(self._pressed):
                # No-audio tap: quick press+release toggles chat.
                elapsed = time.perf_counter() - self._recording_started_at
                self._recording_started_at = 0.0
                if elapsed < CHAT_TAP_THRESHOLD_SEC:
                    should_toggle_chat = True

        if should_toggle_chat:
            if self._audio is not None:
                self._audio.cancel_recording()
            self.caption_changed.emit("")
            self.widget_state_requested.emit(FloatingCircle.IDLE)
            log.info("Hotkey tapped. Toggling chat window.")
            self.chat_toggle_requested.emit()
            return

        if should_finish:
            self.widget_state_requested.emit(FloatingCircle.THINKING)
            Thread(target=self._finish_recording, daemon=True).start()

    def _on_mouse_click(self, x: int, y: int, button, pressed: bool) -> None:
        # Reserved for future v2 use (e.g. user-demonstration capture).
        del x, y, button, pressed

    def _start_voice_recording(self) -> None:
        if not self._backend_ready or self._audio is None:
            return
        should_start = False
        with self._lock:
            if not self._busy and not self._recording_active:
                self._recording_active = True
                self._recording_started_at = time.perf_counter()
                should_start = True
        if should_start:
            self._engine_abort.clear()
            try:
                self._audio.start_recording()
            except Exception:
                log.exception("Failed to start recording (orb hold)")
                with self._lock:
                    self._recording_active = False
                    self._recording_started_at = 0.0
                self.widget_state_requested.emit(FloatingCircle.IDLE)
                return
            self.widget_state_requested.emit(FloatingCircle.LISTENING)
            log.info("Listening (orb hold)...")

    def _stop_voice_recording(self) -> None:
        if not self._backend_ready or self._audio is None:
            return
        should_finish = False
        with self._lock:
            if self._recording_active:
                self._recording_active = False
                self._recording_started_at = 0.0
                self._busy = True
                should_finish = True
        if should_finish:
            self.widget_state_requested.emit(FloatingCircle.THINKING)
            Thread(target=self._finish_recording, daemon=True).start()

    def _finish_recording(self) -> None:
        if self._audio is None:
            self._reset_busy_state(clear_caption=True)
            return

        try:
            recording = self._audio.stop_recording()
            log.info("Captured %.2fs. Transcribing...", recording.duration_sec)
            from v2_bridge import transcribe_v2

            transcript = transcribe_v2(recording.wav_bytes).strip()
            log.info("Transcript: %s", transcript)
            if transcript:
                self._start_goal(transcript, speak=True)
            else:
                self._reset_busy_state(clear_caption=True)
        except Exception as exc:
            log.exception("voice pipeline failed")
            self.agent_message_received.emit(f"Voice error: {exc}")
            self._reset_busy_state(clear_caption=True)

    def _handle_chat_submitted(self, text: str) -> None:
        message = (text or "").strip()
        if not message:
            return
        if not self._backend_ready:
            status = self._backend_status_message()
            self._chat_window.add_message(status, False)
            log.info(status)
            return

        with self._lock:
            if self._busy:
                return
            self._busy = True

        self._start_goal(message, speak=SPEAK_TYPED_CHAT)

    def _reset_busy_state(self, *, clear_caption: bool = False) -> None:
        with self._lock:
            self._busy = False
        if clear_caption:
            self.caption_changed.emit("")
        self.widget_state_requested.emit(FloatingCircle.IDLE)

    # -- computer-use engine ------------------------------------------------------

    def _on_engine_highlight(
        self, x: int, y: int, width: int, height: int, label: str
    ) -> None:
        anchor_x = x + width // 2
        anchor_y = y + height // 2
        self._ghost_cursor.animate_to(anchor_x, anchor_y, label)
        self._overlay.show_highlight(
            x, y, width, height, label,
            duration_ms=0 if self._selected_mode == FloatingCircle.HELP else 1200,
            anchor_x=anchor_x, anchor_y=anchor_y,
        )

    def _on_engine_finished(self, ok: bool) -> None:
        self.overlay_clear_requested.emit()
        self._ghost_cursor.clear()
        self._reset_busy_state()
        self.widget_state_requested.emit(FloatingCircle.IDLE)
        # Do not erase the result: keep the answer or failure visible near the orb.
        self._show_final_reply(self._last_run_reply)
        log.info("Helper goal finished ok=%s", ok)

    def _speak_reply(self, text: str) -> None:
        """Speak via v2's streaming TTS (sentence-chunked, barge-in capable)."""
        message = (text or "").strip()
        if not message or self._audio is None:
            return
        if self._engine_abort.is_set():
            return
        self.widget_state_requested.emit(FloatingCircle.SPEAKING)
        try:
            from v2.voice import TtsStreamer

            tts = TtsStreamer()
            # Stream one chunk; the worker synthesizes sentence-by-sentence.
            tts.speak([message])
        except Exception as exc:
            log.exception("v2 TTS failed: %s", exc)

    def _start_goal(self, goal: str, *, speak: bool = False) -> None:
        if self._engine_runner is None:
            return
        self._engine_abort.clear()
        self._last_run_reply = ""
        self._reply_hide_timer.stop()
        self.user_message_received.emit(goal)
        if not self._engine_runner.start_goal(goal, mode=self._selected_mode, speak=speak):
            self.agent_message_received.emit(
                "Helper is already working on a goal — wait for it to finish.")
            self._reset_busy_state()

def _startup_surface(argv: list[str]) -> str:
    args = {arg.strip().lower() for arg in argv[1:]}
    if "--background" in args:
        return "background"
    if "--chat" in args or "--app" in args:
        return "chat"
    return "dashboard"


def _show_fatal_dialog(log_path: Path, exc_text: str) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    box = QMessageBox()
    box.setWindowIcon(helper_app_icon(app))
    box.setIcon(QMessageBox.Icon.Critical)
    box.setWindowTitle("Helper failed to start")
    stripped = exc_text.strip()
    first_line = stripped.splitlines()[-1] if stripped else "Unknown error"
    box.setText(first_line)
    box.setDetailedText(f"{exc_text}\n\nLog: {log_path}")
    box.setStandardButtons(QMessageBox.StandardButton.Ok)
    box.exec()


def main() -> int:
    log_path = setup_logging()
    startup_log = logging.getLogger("helper.main")
    startup_log.info("Helper starting; log=%s", log_path)
    try:
        configure_windows_app_identity()
        app = QApplication(sys.argv)
        app.setApplicationName("Helper")
        app.setApplicationDisplayName("Helper")
        app.setDesktopFileName("Helper")
        app.setQuitOnLastWindowClosed(False)
        helpler = HelplerDesktopApp(app, HOTKEY, startup_surface=_startup_surface(sys.argv))
        helpler.start()
        return app.exec()
    except Exception:
        tb = traceback.format_exc()
        startup_log.error("Fatal startup error\n%s", tb)
        _show_fatal_dialog(log_path, tb)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
