from __future__ import annotations

import ctypes
import logging
import os
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

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtGui import QIcon
from PyQt6.QtWidgets import QApplication, QMenu, QMessageBox, QSystemTrayIcon

from config import (
    ENV_PATH,
    HOTKEY,
    ROOT,
    STARTUP_LOG,
    SPEAK_TYPED_CHAT,
    TTS_MODEL,
    TTS_VOICE,
    setup_logging,
)
from conversation_store import ConversationStore, StoredConversation
from env_io import read_env
import config
import oauth_codex
import theme
from ui_computer_use import ComputerUseManager
from ui_dashboard import DashboardWindow
from ui_ghost_cursor import GhostCursorManager
from ui_widget import ChatStatusPill, FloatingCircle
from windows_integration import configure_windows_app_identity, ensure_windows_shortcuts

log = logging.getLogger("helper")

if TYPE_CHECKING:
    from audio_recorder import AudioRecorder


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
APP_ICON_PATH = ROOT / "assets" / "helper_logo.ico"
CONVERSATIONS_PATH = ROOT / "data" / "conversations.json"

def helper_app_icon() -> QIcon:
    """Use the same violet artwork as the desktop shortcut and README."""
    return QIcon(str(APP_ICON_PATH))


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
    ghost_clear_requested = pyqtSignal()
    agent_message_received = pyqtSignal(str)
    user_message_received = pyqtSignal(str)
    chat_toggle_requested = pyqtSignal()
    escape_requested = pyqtSignal()
    chat_status_changed = pyqtSignal(str)
    voice_transcribed = pyqtSignal(str)

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
        self._app_icon = helper_app_icon()
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
        # The same typed runtime serves chat, voice, and active control.
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
        self._engine_runner.computer_use.connect(self._on_computer_use_changed)
        # One desktop experience: the model answers or acts according to the request.
        # Legacy HELPER/HARVIS_DEFAULT_MODE must not silently disable requested actions.
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
        self._computer_use = ComputerUseManager(app, self)
        self._ghost_cursor = GhostCursorManager(app)
        self._audio: AudioRecorder | None = None
        self._tts = None
        self._listener = None
        self._lock = Lock()
        self._pressed: set[str] = set()
        self._recording_active = False
        self._recording_started_at = 0.0
        self._busy = False
        self._shutting_down = False
        # Live replies appear beside the orb; saved transcripts live in the dashboard.
        self._last_run_reply = ""
        self._reply_hide_timer = QTimer(self)
        self._reply_hide_timer.setSingleShot(True)
        self._reply_hide_timer.timeout.connect(self._hide_final_reply)
        self._startup_timer = QTimer(self)
        self._startup_timer.setSingleShot(True)
        self._startup_timer.timeout.connect(self._finish_startup_animation)

        self.widget_state_requested.connect(self._widget.set_state)
        self.caption_changed.connect(self._widget.set_caption)
        self.ghost_clear_requested.connect(self._ghost_cursor.clear)
        self.agent_message_received.connect(self._handle_agent_message)
        self.user_message_received.connect(self._handle_user_message)
        self.chat_toggle_requested.connect(self._toggle_widget_chat)
        self.escape_requested.connect(self._on_escape_pressed)
        self.chat_status_changed.connect(self._on_chat_status_changed)
        self.voice_transcribed.connect(self._on_voice_transcribed)

        self._widget.quit_requested.connect(self._handle_quit_requested)
        self._widget.restart_requested.connect(self._handle_restart_requested)
        self._widget.chat_requested.connect(self._toggle_widget_chat)
        self._widget.chat_submitted.connect(self._handle_chat_submitted)
        self._widget.voice_hold_started.connect(self._start_voice_recording)
        self._widget.voice_hold_finished.connect(self._stop_voice_recording)
        self._widget.muted_changed.connect(self._on_muted_changed)
        self._widget.chat_open_changed.connect(self._on_widget_chat_open_changed)
        self._widget.dashboard_requested.connect(self._toggle_dashboard)
        self._widget.moved.connect(self._on_widget_moved)

        self._dashboard.closed.connect(self._sync_tray_actions)
        self._app.aboutToQuit.connect(self.shutdown)

        self._tray_icon: QSystemTrayIcon | None = None
        self._tray_toggle_action = None
        self._setup_tray()

        self._widget.move(*self._initial_widget_position())
        self._widget.show()
        if self._startup_surface != "background":
            QTimer.singleShot(0, self._show_dashboard_window)
        self._sync_tray_actions()
        self._widget.set_state(FloatingCircle.LISTENING)
        self._startup_timer.start(2000)
        QTimer.singleShot(800, self._show_first_launch_balloon)
        QTimer.singleShot(50, self._initialize_backend)

    def start(self) -> None:
        log.info("Starting Helper. Loading backend for %s...", self._hotkey_label)
        log.info("Right-click the widget or tray icon for controls.")

    def _finish_startup_animation(self) -> None:
        if not self._shutting_down and not self._busy and not self._recording_active:
            self.widget_state_requested.emit(FloatingCircle.IDLE)

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
            from pynput import keyboard

            try:
                from audio_recorder import AudioRecorder
                audio = AudioRecorder()
            except Exception:
                audio = None
                log.exception("Voice unavailable; keyboard and typed chat remain available")
            listener = keyboard.Listener(
                on_press=self._on_press,
                on_release=self._on_release,
            )

            self._audio = audio
            self._listener = listener

            if self._shutting_down:
                return

            self._listener.start()
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
        self._dashboard.cancel_pending_sign_ins()
        self._engine_abort.set()
        self._stop_speech()
        self._reply_hide_timer.stop()
        self._startup_timer.stop()
        self._computer_use.close()
        self._persist_active_conversation()
        if self._listener is not None:
            self._listener.stop()
        if self._audio is not None:
            self._audio.cancel_recording()
        self._status_pill.hide()
        self._dashboard.hide()
        self._ghost_cursor.close()
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
        self._tray_toggle_action.triggered.connect(self._toggle_dashboard)

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
            if self._dashboard.isVisible() and not self._dashboard.isMinimized()
            else "Show Helper"
        )

    def _on_escape_pressed(self) -> None:
        # GUI-thread slot for the Esc hotkey: never touch QWidget from the
        # pynput listener thread.
        self._engine_abort.set()
        self._computer_use.set_active(False)
        self._stop_speech()
        self._ghost_cursor.clear()
        log.info("Goal aborted by user.")

    def _show_dashboard_window(self) -> None:
        self._dashboard.show_dashboard()
        self._sync_tray_actions()

    def _toggle_widget_chat(self) -> None:
        self._widget.toggle_chat_open()
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
            self._toggle_dashboard()

    def _toggle_dashboard(self) -> None:
        self._dashboard.toggle_visibility()
        self._sync_tray_actions()

    def _status_snapshot(self) -> dict[str, object]:
        if self._cached_signed_in is None:
            self._cached_signed_in = oauth_codex.is_signed_in()
        provider = config.effective_provider()
        connected = bool(self._cached_signed_in) if provider == "codex" else config.provider_key_configured(provider)
        return {
            "provider": provider,
            "provider_connected": connected,
            "provider_auth_mode": (config.xai_auth_mode() if provider == "xai" else
                                   config.anthropic_auth_mode() if provider == "anthropic" else None),
            "backend_ready": self._backend_ready,
            "backend_initializing": self._backend_initializing,
            "backend_error": self._backend_error,
            "signed_in": self._cached_signed_in,
            "agent_ready": True,  # engine creation is lazy
            "agent_error": None,
            "mode": "automatic",
            "hotkey": self._hotkey_label,
            "log_path": STARTUP_LOG,
            "engine_running": self._engine_runner.is_running(),
        }

    def _handle_auth_changed(self, signed_in: bool) -> None:
        self._cached_signed_in = signed_in
        if signed_in:
            self.agent_message_received.emit("Signed in. Helper is ready.")
        elif config.effective_provider() == "codex":
            self.agent_message_received.emit("Signed out of ChatGPT. Sign in again to use this provider.")
        else:
            self.agent_message_received.emit("Signed out of ChatGPT. Your selected API provider is unchanged.")

    def _handle_quit_requested(self) -> None:
        self.shutdown()
        self._app.quit()

    def _handle_restart_requested(self) -> None:
        log.info("Restart requested. Launching a fresh instance...")
        try:
            # dotenv-loaded values are inherited by children. Reload the saved file
            # explicitly so Save and restart cannot resurrect the old model/provider.
            environment = os.environ.copy()
            environment.update(read_env(ENV_PATH))
            subprocess.Popen(
                [sys.executable, *sys.argv],
                close_fds=True,
                env=environment,
            )
        except Exception:
            log.exception("Restart failed")
            return
        self._handle_quit_requested()

    def _backend_status_message(self) -> str:
        if self._backend_error:
            return f"Helper startup failed: {self._backend_error}"
        if self._backend_initializing:
            return "Helper is still starting up. Try again in a moment."
        return "Helper backend is not ready yet."

    def _handle_user_message(self, message: str) -> None:
        text = (message or "").strip()
        if text:
            self._append_to_active_conversation("user", text)

    def _handle_agent_message(self, message: str) -> None:
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
                # queues cancellation to the GUI thread.
                self.escape_requested.emit()
                log.info("Abort requested.")
            return None

        if self._audio is None:
            # No audio: track the press for tap-to-toggle-chat, but no recording.
            with self._lock:
                self._pressed.add(name)
                if not self._busy and self._hotkey.issubset(self._pressed):
                    self._recording_started_at = time.perf_counter()
            return None
        if self._widget.muted:
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
            log.info("Hotkey tapped. Toggling floating chat input.")
            self.chat_toggle_requested.emit()
            return

        if should_finish:
            self.widget_state_requested.emit(FloatingCircle.THINKING)
            Thread(target=self._finish_recording, daemon=True).start()

    def _start_voice_recording(self) -> None:
        if not self._backend_ready or self._audio is None or self._widget.muted:
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
            from voice_bridge import transcribe_audio

            transcript = transcribe_audio(recording.wav_bytes).strip()
            log.info("Voice transcription completed (characters=%d).", len(transcript))
            if transcript:
                # The recorder runs off-thread. Timers, widgets, and task admission belong to Qt's thread.
                self.voice_transcribed.emit(transcript)
            else:
                self._reset_busy_state(clear_caption=True)
        except Exception as exc:
            log.exception("voice pipeline failed")
            self.agent_message_received.emit(f"Voice error: {exc}")
            self._reset_busy_state(clear_caption=True)

    @pyqtSlot(str)
    def _on_voice_transcribed(self, transcript: str) -> None:
        if self._shutting_down:
            return
        if self._engine_abort.is_set() or self._widget.muted:
            self._reset_busy_state(clear_caption=True)
            return
        self._start_goal(transcript, speak=True)

    def _on_muted_changed(self, muted: bool) -> None:
        if not muted:
            return
        self._stop_speech()
        with self._lock:
            recording = self._recording_active
            self._recording_active = False
            self._recording_started_at = 0.0
        if recording and self._audio is not None:
            self._audio.cancel_recording()
            self.widget_state_requested.emit(FloatingCircle.IDLE)

    def _stop_speech(self) -> None:
        if self._tts is not None:
            self._tts.stop()

    def _handle_chat_submitted(self, text: str) -> None:
        message = (text or "").strip()
        if not message:
            return
        if not self._backend_ready:
            status = self._backend_status_message()
            self._show_final_reply(status)
            log.info(status)
            return

        with self._lock:
            busy = self._busy
            if not busy:
                self._busy = True
        if busy:
            self._on_chat_status_changed("Helper is already working. Press Escape to stop it first.")
            return
        self._start_goal(message, speak=SPEAK_TYPED_CHAT)

    def _reset_busy_state(self, *, clear_caption: bool = False) -> None:
        with self._lock:
            self._busy = False
        if clear_caption:
            self.caption_changed.emit("")
        self.widget_state_requested.emit(FloatingCircle.IDLE)

    # -- computer-use engine ------------------------------------------------------

    def _on_computer_use_changed(self, active: bool) -> None:
        # Queued worker events must not restore a frame after Escape or shutdown.
        self._computer_use.set_active(active and not self._engine_abort.is_set() and not self._shutting_down)

    def _on_engine_highlight(
        self, x: int, y: int, width: int, height: int, label: str
    ) -> None:
        if self._engine_abort.is_set() or self._shutting_down:
            return
        anchor_x = x + width // 2
        anchor_y = y + height // 2
        # Active control uses the small aura pointer. Action details remain in chat/status;
        # a target rectangle and floating speech bubble would obscure the app being used.
        self._ghost_cursor.animate_to(anchor_x, anchor_y)

    def _on_engine_finished(self, ok: bool) -> None:
        self._computer_use.set_active(False)
        self._ghost_cursor.clear()
        self._reset_busy_state()
        self.widget_state_requested.emit(FloatingCircle.IDLE)
        # Do not erase the result: keep the answer or failure visible near the orb.
        self._show_final_reply(self._last_run_reply)
        log.info("Helper goal finished ok=%s", ok)

    def _speak_reply(self, text: str) -> None:
        """Speak via the streaming TTS runtime (sentence-chunked, barge-in capable)."""
        message = (text or "").strip()
        if not message or self._audio is None or self._widget.muted:
            return
        if self._engine_abort.is_set():
            return
        self.widget_state_requested.emit(FloatingCircle.SPEAKING)
        try:
            from helper_voice import TtsStreamer

            self._stop_speech()
            # A fresh cancellation token avoids joining an old HTTP synthesis call on Qt's thread.
            self._tts = TtsStreamer(model=TTS_MODEL, voice=TTS_VOICE)
            self._tts.speak([message])
        except Exception as exc:
            log.exception("TTS failed: %s", exc)

    def _start_goal(self, goal: str, *, speak: bool = False) -> None:
        if self._engine_runner is None or self._shutting_down:
            return
        if self._engine_runner.is_running():
            # A second submission must never clear cancellation on the in-flight run.
            self._on_chat_status_changed("Helper is already working. Press Escape to stop it first.")
            return
        with self._lock:
            self._busy = True
        self._stop_speech()
        self._engine_abort.clear()
        self._last_run_reply = ""
        self._reply_hide_timer.stop()
        self.user_message_received.emit(goal)
        try:
            started = self._engine_runner.start_goal(goal, mode="active", speak=speak)
        except Exception:
            log.exception("Could not start task worker")
            self.agent_message_received.emit("Could not start the task worker. Please try again.")
            self._reset_busy_state()
            return
        if not started:
            self._on_chat_status_changed("Helper is already working on a goal — wait for it to finish.")
            if not self._engine_runner.is_running():
                self._reset_busy_state()

def _startup_surface(argv: list[str]) -> str:
    args = {arg.strip().lower() for arg in argv[1:]}
    if "--background" in args:
        return "background"
    return "dashboard"


def _show_fatal_dialog(log_path: Path, exc_text: str) -> None:
    app = QApplication.instance() or QApplication(sys.argv)
    box = QMessageBox()
    box.setWindowIcon(helper_app_icon())
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
