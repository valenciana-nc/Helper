from __future__ import annotations

import json
import logging
import os
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger("helper.conversation_store")

TITLE_MAX_LEN = 60


@dataclass(frozen=True)
class StoredMessage:
    role: str
    text: str
    timestamp: float


@dataclass
class StoredConversation:
    id: str
    started_at: float
    ended_at: float
    title: str
    messages: list[StoredMessage] = field(default_factory=list)

    @classmethod
    def new(cls, started_at: float | None = None) -> "StoredConversation":
        now = started_at if started_at is not None else time.time()
        return cls(
            id=uuid.uuid4().hex,
            started_at=now,
            ended_at=now,
            title="",
            messages=[],
        )

    def add_message(self, role: str, text: str, when: float | None = None) -> None:
        ts = when if when is not None else time.time()
        self.messages.append(StoredMessage(role=role, text=text, timestamp=ts))
        self.ended_at = ts

    def has_user_message(self) -> bool:
        return any(m.role == "user" and m.text.strip() for m in self.messages)

    def derive_title(self) -> str:
        for message in self.messages:
            if message.role == "user":
                text = message.text.strip().replace("\n", " ")
                if not text:
                    continue
                if len(text) <= TITLE_MAX_LEN:
                    return text
                return text[: TITLE_MAX_LEN - 1].rstrip() + "…"
        return "Conversation"

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "title": self.title or self.derive_title(),
            "messages": [asdict(m) for m in self.messages],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "StoredConversation":
        raw_messages = data.get("messages") or []
        messages: list[StoredMessage] = []
        for item in raw_messages:
            if not isinstance(item, dict):
                continue
            role = str(item.get("role", "")).strip()
            text = str(item.get("text", ""))
            try:
                ts = float(item.get("timestamp", 0.0))
            except (TypeError, ValueError):
                ts = 0.0
            if role and text:
                messages.append(StoredMessage(role=role, text=text, timestamp=ts))
        return cls(
            id=str(data.get("id") or uuid.uuid4().hex),
            started_at=float(data.get("started_at") or 0.0),
            ended_at=float(data.get("ended_at") or 0.0),
            title=str(data.get("title") or ""),
            messages=messages,
        )


class ConversationStore:
    def __init__(self, path: Path) -> None:
        self._path = Path(path)

    @property
    def path(self) -> Path:
        return self._path

    def load_all(self) -> list[StoredConversation]:
        if not self._path.exists():
            return []
        try:
            with self._path.open("r", encoding="utf-8") as handle:
                payload = json.load(handle)
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("Could not read conversation store %s: %s", self._path, exc)
            return []

        items = payload.get("conversations") if isinstance(payload, dict) else payload
        if not isinstance(items, list):
            return []

        conversations: list[StoredConversation] = []
        for entry in items:
            if not isinstance(entry, dict):
                continue
            try:
                conversations.append(StoredConversation.from_dict(entry))
            except Exception:
                log.exception("Skipping malformed conversation entry")
        conversations.sort(key=lambda c: c.started_at, reverse=True)
        return conversations

    def save(self, conversation: StoredConversation) -> None:
        existing = self.load_all()
        existing = [c for c in existing if c.id != conversation.id]
        existing.append(conversation)
        existing.sort(key=lambda c: c.started_at, reverse=True)
        self._write_all(existing)

    def delete(self, conversation_id: str) -> bool:
        existing = self.load_all()
        kept = [c for c in existing if c.id != conversation_id]
        if len(kept) == len(existing):
            return False
        self._write_all(kept)
        return True

    def _write_all(self, conversations: list[StoredConversation]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"conversations": [c.to_dict() for c in conversations]}
        fd, tmp_name = tempfile.mkstemp(
            prefix=".conversations-", suffix=".json", dir=str(self._path.parent)
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self._path)
        except Exception:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
