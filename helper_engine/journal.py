"""Optional metadata-only run journal. Screenshots, goals, tokens and typed text stay out."""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path


class JsonJournal:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._file = path.open("a", encoding="utf-8")
        self._lock = threading.Lock()

    def __call__(self, name: str, payload: dict) -> None:
        allowed = {"run_id", "step", "status", "route", "effect", "outcome", "seconds",
                   "observations", "model_calls", "actions", "verified_actions", "stale_decisions",
                   "rejected_actions", "screenshot_bytes", "model_seconds", "elapsed_seconds", "routes"}
        record = {"event": name, "time": time.time(),
                  **{key: value for key, value in payload.items() if key in allowed}}
        with self._lock:
            self._file.write(json.dumps(record, ensure_ascii=True) + "\n")
            self._file.flush()

    def close(self):
        with self._lock:
            if not self._file.closed:
                self._file.close()
