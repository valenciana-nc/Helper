"""Qt presentation adapter. All model and platform work runs off the GUI thread."""
from __future__ import annotations

import logging
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from PyQt6.QtCore import QObject, QTimer, pyqtSignal, pyqtSlot
from PyQt6.QtWidgets import QMessageBox

log = logging.getLogger(__name__)


@dataclass
class PendingApproval:
    text: str
    deadline: float = field(default_factory=lambda: time.monotonic() + 120)
    event: threading.Event = field(default_factory=threading.Event)
    granted: bool = False


class EngineRunner(QObject):
    message = pyqtSignal(str)
    status = pyqtSignal(str)
    state = pyqtSignal(str)
    highlight = pyqtSignal(int, int, int, int, str)
    speak = pyqtSignal(str)
    finished = pyqtSignal(bool)
    busy = pyqtSignal(bool)
    approval_requested = pyqtSignal(object)

    def __init__(self, parent=None, should_abort: Callable[[], bool] | None = None):
        super().__init__(parent)
        self._lock = threading.Lock()
        self._running = False
        self._agent = None
        self._should_abort = should_abort or (lambda: False)
        self.approval_requested.connect(self._display_approval)

    def is_running(self):
        with self._lock:
            return self._running

    def _get_agent(self):
        if self._agent is None:
            from helper_engine.bootstrap import build_agent
            self._agent = build_agent(confirm=self._confirm)
        return self._agent

    def _confirm(self, action, observation, reason):
        target = observation.element(action.target.element_id) if action.target and action.target.element_id else None
        label = target.name if target else action.kind
        pending = PendingApproval(f"{reason}\n\n{action.description or action.kind}\nTarget: {label[:160]}")
        self.approval_requested.emit(pending)
        while not pending.event.wait(0.05):
            if self._should_abort() or time.monotonic() >= pending.deadline:
                pending.event.set()
                return False
        return pending.granted and not self._should_abort()

    @pyqtSlot(object)
    def _display_approval(self, pending):
        if pending.event.is_set() or self._should_abort():
            pending.event.set()
            return
        box = QMessageBox()
        box.setWindowTitle("Allow Helper action")
        box.setText(pending.text)
        box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
        box.setDefaultButton(QMessageBox.StandardButton.No)
        timer = QTimer(box)
        timer.timeout.connect(lambda: box.reject() if (
            pending.event.is_set() or self._should_abort() or time.monotonic() >= pending.deadline) else None)
        timer.start(100)
        result = box.exec()
        if not pending.event.is_set():
            pending.granted = result == QMessageBox.StandardButton.Yes
            pending.event.set()

    def start_goal(self, goal: str, *, mode: str = "help", speak: bool = False):
        if mode not in {"help", "active"}:
            raise ValueError("Unknown control mode")
        with self._lock:
            if self._running:
                return False
            self._running = True
        self.busy.emit(True)
        threading.Thread(target=self._run, args=(goal, mode, speak), daemon=True, name="helper-goal").start()
        return True

    def _run(self, goal, mode, speak):
        from helper_engine.journal import JsonJournal
        ok = False
        journal = None
        try:
            journal = JsonJournal(Path(__file__).parent / "logs" / "computer-use" / f"{uuid.uuid4().hex}.jsonl")
            def event(name, payload):
                journal(name, payload)
                self._on_event(name, payload)
            report = self._get_agent().run_goal(goal, mode=mode, on_event=event, should_abort=self._should_abort)
            ok = report.ok
            self.message.emit(report.message)
            if speak and not self._should_abort():
                self.speak.emit(report.message)
        except Exception as exc:
            log.exception("Helper engine failed")
            self.message.emit(f"Task stopped: {exc}")
        finally:
            if journal:
                journal.close()
            with self._lock:
                self._running = False
            self.status.emit("")
            self.state.emit("idle")
            self.busy.emit(False)
            self.finished.emit(ok)

    def _on_event(self, name, payload):
        if name == "started":
            self.state.emit("thinking")
            self.status.emit("Looking at the current app…")
        elif name == "decision":
            self.status.emit(payload.get("message") or "Checking the next step…")
        elif name == "action_start":
            self.status.emit(payload.get("message") or payload.get("kind", ""))
            rect = payload.get("rect")
            if rect and payload.get("surface") == "desktop":
                self.highlight.emit(*rect, payload.get("message", "")[:120])
        elif name == "guided":
            self.message.emit(payload.get("message") or "Follow the highlighted step.")
        elif name == "action_result":
            self.status.emit("Checking the result…")
        elif name == "goal_verification":
            self.status.emit("Checking the complete goal…")
