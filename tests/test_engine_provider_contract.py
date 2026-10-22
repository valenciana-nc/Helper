"""The real provider serialization path that scripted runtime tests replace."""
from __future__ import annotations

import base64
import io
import json
import time
import unittest
from unittest.mock import patch

from PIL import Image

from anthropic_client import _to_anthropic_messages
from gemini_client import _to_gemini_contents
from helper_engine.model import ProviderModel
from helper_engine.types import Element, Observation, Rect
from openai_client import InvalidChatRequest, OpenAIClient, _to_chat_completions_messages


def observation() -> Observation:
    out = io.BytesIO()
    Image.new("RGB", (80, 60), "white").save(out, "PNG")
    return Observation("obs-1", "desktop", "123", "Browser", out.getvalue(), 80, 60,
                       (Element("u1", "Search", "Edit", Rect(5, 5, 40, 12)),))


class RecordingCodex:
    def __init__(self, reply: str):
        self.reply = reply
        self.bodies = []

    def post_response(self, body):
        self.bodies.append(body)
        return {"output": [{"type": "message", "content": [{"type": "output_text", "text": self.reply}]}]}


class RecordingClient:
    def __init__(self, reply: str):
        self.reply = reply
        self.messages = []

    def chat(self, messages, **_kwargs):
        self.messages.append(messages)
        return type("Result", (), {"text": self.reply})()


class ProviderContractTests(unittest.TestCase):
    def test_codex_request_contains_goal_and_current_screenshot(self):
        codex = RecordingCodex('{"kind":"answer","observation_id":"obs-1","message":"Hello"}')
        model = ProviderModel(OpenAIClient(codex_provider=codex), "gpt-6-luna")

        decision = model.decide("Open a song", observation(), {"mode": "active"})

        self.assertEqual(decision.message, "Hello")
        body = codex.bodies[0]
        self.assertTrue(body["instructions"])
        content = body["input"][0]["content"]
        self.assertEqual([item["type"] for item in content], ["input_text", "input_image"])
        self.assertEqual(json.loads(content[0]["text"])["goal"], "Open a song")
        self.assertTrue(content[1]["image_url"].startswith("data:image/png;base64,"))
        Image.open(io.BytesIO(base64.b64decode(content[1]["image_url"].split(",", 1)[1]))).verify()

    def test_every_provider_adapter_receives_text_and_image(self):
        client = RecordingClient('{"kind":"answer","observation_id":"obs-1","message":"Ready"}')
        ProviderModel(client, "test").decide("Play music", observation(), {})
        messages = client.messages[0]

        chat = _to_chat_completions_messages(messages, "system")[1]["content"]
        self.assertEqual([item["type"] for item in chat], ["text", "image_url"])
        anthropic = _to_anthropic_messages(messages)[0][0]["content"]
        self.assertEqual([item["type"] for item in anthropic], ["text", "image"])
        gemini = _to_gemini_contents(messages)[0][0]["parts"]
        self.assertIn("text", gemini[0])
        self.assertIn("inline_data", gemini[1])

    def test_wrong_message_shape_fails_before_network(self):
        codex = RecordingCodex("unused")
        client = OpenAIClient(codex_provider=codex)
        with self.assertRaises(InvalidChatRequest):
            client.chat([{"role": "user", "content": [{"type": "text", "text": "hello"}]}], model="test")
        with self.assertRaises(InvalidChatRequest):
            client.chat([{"role": "user", "parts": []}], model="test")
        self.assertEqual(codex.bodies, [])

    def test_anthropic_merges_consecutive_same_role_messages(self):
        from anthropic_client import _to_anthropic_messages
        messages = [
            {"role": "assistant", "parts": [{"text": "first"}]},
            {"role": "assistant", "parts": [{"text": "second"}]},
            {"role": "user", "parts": [{"text": "reply"}]},
        ]
        translated, _ = _to_anthropic_messages(messages)
        roles = [m["role"] for m in translated]
        self.assertEqual(roles, ["assistant", "user"])
        self.assertEqual(len(translated[0]["content"]), 2)

    def test_gemini_merges_consecutive_same_role_messages(self):
        messages = [
            {"role": "assistant", "parts": [{"text": "first"}]},
            {"role": "assistant", "parts": [{"text": "second"}]},
            {"role": "user", "parts": [{"text": "reply"}]},
        ]
        translated, _ = _to_gemini_contents(messages)
        roles = [m["role"] for m in translated]
        self.assertEqual(roles, ["model", "user"])
        self.assertEqual(len(translated[0]["parts"]), 2)


class FinalReplyVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PyQt6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def test_result_stays_visible_after_the_run_finishes(self):
        import main
        with (patch.object(main, "ensure_windows_shortcuts", return_value=[]),
              patch.object(main.HelplerDesktopApp, "_setup_tray")):
            app = main.HelplerDesktopApp(self.app, "ctrl+shift+space", startup_surface="background")
        try:
            reply = "Task stopped: ChatGPT/Codex could not use the selected model."
            app._handle_agent_message(reply)
            app._on_chat_status_changed("")  # emitted by the worker as cleanup
            app._on_engine_finished(False)
            self.app.processEvents()

            self.assertTrue(app._status_pill.isVisible())
            self.assertEqual(app._status_pill.text(), reply)
            self.assertTrue(app._reply_hide_timer.isActive())

            app._on_chat_status_changed("Thinking…")
            self.assertFalse(app._reply_hide_timer.isActive())
            self.assertEqual(app._status_pill.text(), "Thinking…")
        finally:
            app.shutdown()
            for widget in (app._widget, app._dashboard, app._status_pill):
                widget.close()
                widget.deleteLater()
            self.app.processEvents()

    def test_long_status_wraps_instead_of_becoming_one_short_line(self):
        from ui_widget import ChatStatusPill
        pill = ChatStatusPill()
        try:
            pill.set_text("I opened your browser and searched for the song. " * 8)
            self.assertGreater(pill.height(), 24)
            self.assertLessEqual(pill.width(), 420)
            self.assertTrue(pill._display.endswith("…"))
        finally:
            pill.close()
            pill.deleteLater()
            deadline = time.monotonic() + 0.1
            while time.monotonic() < deadline:
                self.app.processEvents()


if __name__ == "__main__":
    unittest.main()
