from __future__ import annotations

import io
import json
import math
from dataclasses import dataclass, field
from typing import Any, Iterable

from PIL import Image

from config import HISTORY_MAX_TOKENS, HISTORY_MAX_TURNS, SCREENSHOT_MAX_EDGE
from screen import Capture

IMAGE_TILE_SIZE = 384
IMAGE_TILE_TOKENS = 256
TEXT_CHARS_PER_TOKEN = 4
CONTENT_OVERHEAD_TOKENS = 6
PART_OVERHEAD_TOKENS = 2


@dataclass(frozen=True)
class HistorySnapshot:
    messages: list[dict[str, Any]]
    estimated_tokens: int
    dropped_images: int
    dropped_turns: int


@dataclass(frozen=True)
class ConversationMessage:
    role: str
    text: str


@dataclass(frozen=True)
class _StoredPart:
    text: str | None = None
    image_png: bytes | None = None
    function_call_name: str | None = None
    function_call_args: dict[str, Any] | None = None
    function_call_id: str | None = None
    function_response_name: str | None = None
    function_response_data: dict[str, Any] | None = None
    function_response_call_id: str | None = None
    estimated_tokens: int = 0

    @property
    def is_image(self) -> bool:
        return self.image_png is not None

    def to_message_part(self) -> dict[str, Any]:
        if self.text is not None:
            return {"text": self.text}
        if self.image_png is not None:
            return {"image_png": self.image_png}
        if self.function_call_name is not None:
            return {"function_call": {
                "name": self.function_call_name,
                "arguments": self.function_call_args or {},
                "call_id": self.function_call_id,
            }}
        if self.function_response_name is not None:
            return {"function_response": {
                "name": self.function_response_name,
                "response": self.function_response_data or {},
                "call_id": self.function_response_call_id,
            }}
        return {}


@dataclass(frozen=True)
class _StoredContent:
    role: str
    parts: tuple[_StoredPart, ...] = field(default_factory=tuple)

    @property
    def estimated_tokens(self) -> int:
        if not self.parts:
            return 0
        return CONTENT_OVERHEAD_TOKENS + sum(part.estimated_tokens for part in self.parts)

    def without_images(self, remaining_budget: int) -> tuple["_StoredContent", int]:
        kept_parts: list[_StoredPart] = []
        dropped_images = 0
        current_total = self.estimated_tokens

        for part in self.parts:
            if current_total <= remaining_budget or not part.is_image:
                kept_parts.append(part)
                continue
            current_total -= part.estimated_tokens
            dropped_images += 1

        return _StoredContent(role=self.role, parts=tuple(kept_parts)), dropped_images

    def to_message(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "parts": [part.to_message_part() for part in self.parts if part.to_message_part()],
        }


class HistoryManager:
    """Conversation history with image-aware pruning.

    Token counts are estimates. Text is sized heuristically, and image parts are
    estimated by tile count after downscaling to `SCREENSHOT_MAX_EDGE`.
    """

    def __init__(
        self,
        *,
        max_turns: int = HISTORY_MAX_TURNS,
        max_tokens: int = HISTORY_MAX_TOKENS,
        screenshot_max_edge: int = SCREENSHOT_MAX_EDGE,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be at least 1.")
        if max_tokens < 1:
            raise ValueError("max_tokens must be at least 1.")
        if screenshot_max_edge < 1:
            raise ValueError("screenshot_max_edge must be at least 1.")

        self.max_turns = max_turns
        self.max_tokens = max_tokens
        self.screenshot_max_edge = screenshot_max_edge
        self._entries: list[_StoredContent] = []

    def clear(self) -> None:
        self._entries.clear()

    def add_user_turn(self, text: str, screenshot: Capture | bytes | None = None) -> None:
        self.add_turn("user", text=text, screenshot=screenshot)

    def add_assistant_turn(self, text: str) -> None:
        self.add_turn("assistant", text=text)

    def add_turn(
        self,
        role: str,
        *,
        text: str | None = None,
        screenshot: Capture | bytes | None = None,
        extra_parts: Iterable[_StoredPart] | None = None,
    ) -> None:
        built: list[_StoredPart] = []
        if text:
            built.append(self._make_text_part(text))
        if screenshot is not None:
            built.append(self._make_screenshot_part(screenshot))
        if extra_parts is not None:
            built.extend(extra_parts)
        if not built:
            raise ValueError("A history turn requires text, screenshot, or extra_parts.")
        self._entries.append(_StoredContent(role=role, parts=tuple(built)))

    def add_function_call(self, name: str, args: dict[str, Any] | None = None, call_id: str | None = None) -> None:
        part = _StoredPart(
            function_call_name=name,
            function_call_args=args or {},
            function_call_id=call_id,
            estimated_tokens=PART_OVERHEAD_TOKENS
            + self._estimate_text_tokens(name)
            + self._estimate_json_tokens(args or {}),
        )
        self._entries.append(_StoredContent(role="assistant", parts=(part,)))

    def add_function_response(self, name: str, response: dict[str, Any] | None = None, call_id: str | None = None) -> None:
        part = _StoredPart(
            function_response_name=name,
            function_response_data=response or {},
            function_response_call_id=call_id,
            estimated_tokens=PART_OVERHEAD_TOKENS
            + self._estimate_text_tokens(name)
            + self._estimate_json_tokens(response or {}),
        )
        self._entries.append(_StoredContent(role="tool", parts=(part,)))

    def snapshot(self) -> HistorySnapshot:
        selected_entries = list(self._entries[-self.max_turns :])
        dropped_turns = max(0, len(self._entries) - len(selected_entries))

        total_tokens = sum(entry.estimated_tokens for entry in selected_entries)
        dropped_images = 0

        if total_tokens > self.max_tokens:
            for index, entry in enumerate(selected_entries):
                if total_tokens <= self.max_tokens:
                    break
                pruned_entry, removed = entry.without_images(self.max_tokens - (total_tokens - entry.estimated_tokens))
                if removed == 0:
                    continue
                total_tokens -= entry.estimated_tokens - pruned_entry.estimated_tokens
                dropped_images += removed
                selected_entries[index] = pruned_entry

        selected_entries = [entry for entry in selected_entries if entry.parts]

        while total_tokens > self.max_tokens and len(selected_entries) > 1:
            removed_entry = selected_entries.pop(0)
            total_tokens -= removed_entry.estimated_tokens
            dropped_turns += 1

        messages = [entry.to_message() for entry in selected_entries]
        return HistorySnapshot(
            messages=messages,
            estimated_tokens=max(total_tokens, 0),
            dropped_images=dropped_images,
            dropped_turns=dropped_turns,
        )

    def build_messages(self) -> list[dict[str, Any]]:
        return self.snapshot().messages

    def conversation_messages(self) -> list[ConversationMessage]:
        out: list[ConversationMessage] = []
        for entry in self._entries:
            chunks = [
                part.text.strip()
                for part in entry.parts
                if part.text and part.text.strip()
            ]
            if not chunks:
                continue
            out.append(ConversationMessage(role=entry.role, text=" ".join(chunks)))
        return out

    def estimated_tokens(self) -> int:
        return self.snapshot().estimated_tokens

    def last_executed_actions(self) -> list[dict[str, Any]]:
        """Return the most recent contiguous block of executed actions.

        Each item is `{"name": str, "args": dict, "response": dict}`. Ordered
        oldest-to-newest. Empty list if the tail of history has no tool turns.
        """
        responses: list[_StoredPart] = []
        index = len(self._entries) - 1
        while index >= 0:
            entry = self._entries[index]
            if entry.role != "tool":
                break
            for part in entry.parts:
                if part.function_response_name is not None:
                    responses.append(part)
            index -= 1
        if not responses:
            return []
        responses.reverse()

        calls_by_id: dict[str, _StoredPart] = {}
        while index >= 0:
            entry = self._entries[index]
            if entry.role != "assistant":
                break
            had_call = False
            for part in entry.parts:
                if part.function_call_name is not None:
                    had_call = True
                    key = part.function_call_id or part.function_call_name
                    calls_by_id.setdefault(key, part)
            if not had_call:
                break
            index -= 1

        out: list[dict[str, Any]] = []
        for response in responses:
            key = response.function_response_call_id or response.function_response_name or ""
            call = calls_by_id.get(key)
            out.append(
                {
                    "name": response.function_response_name or (call.function_call_name if call else ""),
                    "args": dict(call.function_call_args or {}) if call else {},
                    "response": dict(response.function_response_data or {}),
                }
            )
        return out

    def _make_text_part(self, text: str) -> _StoredPart:
        return _StoredPart(
            text=text,
            estimated_tokens=PART_OVERHEAD_TOKENS + self._estimate_text_tokens(text),
        )

    def _make_screenshot_part(self, screenshot: Capture | bytes) -> _StoredPart:
        if isinstance(screenshot, Capture):
            long_edge = max(screenshot.width, screenshot.height)
            if long_edge <= self.screenshot_max_edge:
                return _StoredPart(
                    image_png=screenshot.png_bytes,
                    estimated_tokens=self._estimate_image_tokens(screenshot.width, screenshot.height),
                )
            raw = screenshot.png_bytes
        else:
            raw = screenshot
        if not isinstance(raw, bytes):
            raise TypeError(f"Unsupported screenshot type: {type(screenshot)!r}")
        normalized, width, height = self._normalize_image_bytes(raw)
        return _StoredPart(
            image_png=normalized,
            estimated_tokens=self._estimate_image_tokens(width, height),
        )

    def _normalize_image_bytes(self, data: bytes) -> tuple[bytes, int, int]:
        with Image.open(io.BytesIO(data)) as img:
            normalized = img.convert("RGB")
            normalized.thumbnail((self.screenshot_max_edge, self.screenshot_max_edge), Image.LANCZOS)
            width, height = normalized.size
            buf = io.BytesIO()
            normalized.save(buf, format="PNG", optimize=True)
        return buf.getvalue(), width, height

    @staticmethod
    def _estimate_image_tokens(width: int, height: int) -> int:
        tiles_x = max(1, math.ceil(width / IMAGE_TILE_SIZE))
        tiles_y = max(1, math.ceil(height / IMAGE_TILE_SIZE))
        return tiles_x * tiles_y * IMAGE_TILE_TOKENS

    @staticmethod
    def _estimate_json_tokens(value: Any) -> int:
        try:
            serialized = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        except TypeError:
            serialized = repr(value)
        return HistoryManager._estimate_text_tokens(serialized)

    @staticmethod
    def _estimate_text_tokens(text: str) -> int:
        if not text:
            return 0
        return max(1, math.ceil(len(text) / TEXT_CHARS_PER_TOKEN))


if __name__ == "__main__":
    demo = HistoryManager(max_turns=6, max_tokens=3000)
    image = Image.new("RGB", (1920, 1080), color=(20, 40, 60))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    screenshot = buf.getvalue()

    demo.add_user_turn("Open Notepad.", screenshot=screenshot)
    demo.add_assistant_turn("I'll highlight the Start button first.")
    demo.add_user_turn("Now search for Notepad.", screenshot=screenshot)
    demo.add_assistant_turn("The older screenshot should be dropped before text turns.")

    snapshot = demo.snapshot()
    print(f"Messages kept: {len(snapshot.messages)}")
    print(f"Estimated tokens: {snapshot.estimated_tokens}")
    print(f"Dropped images: {snapshot.dropped_images}")
    print(f"Dropped turns: {snapshot.dropped_turns}")
