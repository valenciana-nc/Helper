from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import requests
from PIL import Image
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

import config
import main
import token_store
from agent import HelplerAgent
from computer_control import ComputerController
from fast_actions import parse_fast_action
from ui_chat import ChatWindow
from ui_dashboard import DashboardWindow
from ui_widget import FloatingCircle
from openai_client import (
    BadProviderResponse,
    ChatResult,
    CodexProvider,
    OpenAIClient,
    RateLimited,
    ToolCall,
    UnsupportedModel,
    _parse_response,
    _parse_stream_response,
)
from screen import Capture


def _png_bytes(width: int = 10, height: int = 10) -> bytes:
    buf = BytesIO()
    Image.new("RGB", (width, height), color=(20, 40, 60)).save(buf, format="PNG")
    return buf.getvalue()


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


class EnvAliasTests(unittest.TestCase):
    def test_helper_wins_over_legacy_aliases(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HELPER_AGENT_MODEL": "helper-model",
                "HELPLER_AGENT_MODEL": "helpler-model",
                "HARVIS_AGENT_MODEL": "harvis-model",
            },
            clear=True,
        ):
            self.assertEqual(config.env_value("HELPER_AGENT_MODEL", "default"), "helper-model")

    def test_legacy_alias_order(self) -> None:
        with patch.dict(
            os.environ,
            {"HELPLER_AGENT_MODEL": "helpler-model", "HARVIS_AGENT_MODEL": "harvis-model"},
            clear=True,
        ):
            self.assertEqual(config.env_value("HELPER_AGENT_MODEL", "default"), "helpler-model")

        with patch.dict(os.environ, {"HARVIS_AGENT_MODEL": "harvis-model"}, clear=True):
            self.assertEqual(config.env_value("HELPER_AGENT_MODEL", "default"), "harvis-model")

    def test_codex_model_uses_helper_when_valid(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HELPER_REASONING_MODEL": "gpt-5.5",
                "HARVIS_REASONING_MODEL": "gemini-2.5-flash",
            },
            clear=True,
        ):
            self.assertEqual(config.resolve_codex_model("HELPER_REASONING_MODEL"), "gpt-5.5")

    def test_legacy_gemini_model_falls_back_for_codex(self) -> None:
        with patch.dict(os.environ, {"HARVIS_REASONING_MODEL": "gemini-2.5-flash"}, clear=True):
            self.assertEqual(config.resolve_codex_model("HELPER_REASONING_MODEL"), "gpt-5.5")
            self.assertIn("HARVIS_REASONING_MODEL=gemini-2.5-flash", config.model_compatibility_warnings()[0])

    def test_legacy_non_model_settings_still_resolve(self) -> None:
        with patch.dict(os.environ, {"HARVIS_HOTKEY": "alt+space"}, clear=True):
            self.assertEqual(config.env_value("HELPER_HOTKEY", "ctrl+shift+space"), "alt+space")

    def test_legacy_gemini_voice_models_use_openai_defaults(self) -> None:
        with patch.dict(
            os.environ,
            {
                "HARVIS_STT_MODEL": "gemini-2.5-flash",
                "HARVIS_TTS_MODEL": "gemini-2.5-flash-preview-tts",
            },
            clear=True,
        ):
            self.assertEqual(config.resolve_openai_voice_model("HELPER_STT_MODEL", "whisper-1"), "whisper-1")
            self.assertEqual(
                config.resolve_openai_voice_model("HELPER_TTS_MODEL", "gpt-4o-mini-tts"),
                "gpt-4o-mini-tts",
            )


class FakeKeyring:
    class errors:
        class PasswordDeleteError(Exception):
            pass

    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service: str, username: str) -> str | None:
        return self.values.get((service, username))

    def set_password(self, service: str, username: str, value: str) -> None:
        self.values[(service, username)] = value

    def delete_password(self, service: str, username: str) -> None:
        key = (service, username)
        if key not in self.values:
            raise self.errors.PasswordDeleteError()
        del self.values[key]


class TokenStoreTests(unittest.TestCase):
    def test_load_migrates_legacy_token(self) -> None:
        fake = FakeKeyring()
        token = token_store.TokenSet(
            access_token="access",
            refresh_token="refresh",
            id_token="id",
            expires_at=time.time() + 3600,
            account_id="acct",
        )
        fake.set_password("Harvis", token_store.USERNAME, json.dumps(token.__dict__))

        with patch.object(token_store, "keyring", fake):
            loaded = token_store.load()

        self.assertEqual(loaded, token)
        self.assertIsNotNone(fake.get_password("Helper", token_store.USERNAME))


class FastActionTests(unittest.TestCase):
    def test_parse_search_as_navigation(self) -> None:
        action = parse_fast_action("search for helper app")
        self.assertIsNotNone(action)
        self.assertEqual(action.name, "navigate")
        self.assertEqual(action.raw_args["url"], "https://www.google.com/search?q=helper+app")

    def test_parse_url_navigation(self) -> None:
        action = parse_fast_action("go to example.com")
        self.assertIsNotNone(action)
        self.assertEqual(action.name, "navigate")
        self.assertEqual(action.raw_args["url"], "https://example.com")

    def test_parse_hotkey_alias(self) -> None:
        action = parse_fast_action("close tab")
        self.assertIsNotNone(action)
        self.assertEqual(action.name, "key_combination")
        self.assertEqual(action.keys, "ctrl+w")

    def test_parse_safe_control_click(self) -> None:
        action = parse_fast_action("click save")
        self.assertIsNotNone(action)
        self.assertEqual(action.name, "click_control")
        self.assertEqual(action.raw_args["label"], "save")


class ProviderTests(unittest.TestCase):
    def test_parse_response_reads_text_and_tool_calls(self) -> None:
        result = _parse_response(
            {
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
                    {
                        "type": "function_call",
                        "name": "click_at",
                        "arguments": '{"x":1,"y":2}',
                        "call_id": "call_1",
                    },
                ]
            }
        )
        self.assertEqual(result.text, "hello")
        self.assertEqual(result.tool_calls[0].name, "click_at")
        self.assertEqual(result.tool_calls[0].call_id, "call_1")

    def test_parse_response_rejects_non_object(self) -> None:
        with self.assertRaises(BadProviderResponse):
            _parse_response([])  # type: ignore[arg-type]

    def test_parse_response_keeps_malformed_tool_call_with_empty_args(self) -> None:
        result = _parse_response(
            {
                "output": [
                    {
                        "type": "function_call",
                        "name": "click_at",
                        "arguments": "{bad json",
                        "call_id": "call_bad",
                    }
                ]
            }
        )
        self.assertEqual(result.tool_calls[0].arguments, {})
        self.assertEqual(result.tool_calls[0].call_id, "call_bad")

    def test_parse_stream_response_reads_completed_output_item(self) -> None:
        payload = _parse_stream_response(
            "\n".join(
                [
                    "event: response.output_text.delta",
                    'data: {"type":"response.output_text.delta","delta":"ignored when item is present"}',
                    "event: response.output_item.done",
                    'data: {"type":"response.output_item.done","item":{"type":"message","content":[{"type":"output_text","text":"hello"}]}}',
                ]
            ),
            "req_stream",
        )

        self.assertEqual(_parse_response(payload).text, "hello")

    def test_provider_rate_limit_retries_then_raises(self) -> None:
        response = requests.Response()
        response.status_code = 429
        response._content = b'{"error":"slow down"}'
        response.headers["x-request-id"] = "req_123"

        class Session:
            def post(self, *args, **kwargs):  # noqa: ANN001
                return response

        provider = CodexProvider(Session())  # type: ignore[arg-type]
        with (
            patch("oauth_codex.get_access_token", return_value="access"),
            patch("token_store.load", return_value=None),
            patch("time.sleep", return_value=None),
        ):
            with self.assertRaises(RateLimited):
                provider.post_response({"model": "gpt-5.5", "input": "hi"})

    def test_provider_unsupported_model_error_is_actionable(self) -> None:
        response = requests.Response()
        response.status_code = 400
        response._content = (
            b"{\"detail\":\"The 'gemini-2.5-flash' model is not supported when using Codex with a ChatGPT account.\"}"
        )
        response.headers["x-request-id"] = "req_bad_model"

        class Session:
            def post(self, *args, **kwargs):  # noqa: ANN001
                return response

        provider = CodexProvider(Session())  # type: ignore[arg-type]
        with (
            patch("oauth_codex.get_access_token", return_value="access"),
            patch("token_store.load", return_value=None),
        ):
            with self.assertRaisesRegex(UnsupportedModel, "gemini-2.5-flash.*gpt-5.5"):
                provider.post_response({"model": "gemini-2.5-flash", "input": "hi"})

    def test_openai_client_sends_codex_safe_chat_model(self) -> None:
        class Provider:
            def __init__(self) -> None:
                self.body = None

            def post_response(self, body):  # noqa: ANN001
                self.body = body
                return {"output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]}

        provider = Provider()
        client = OpenAIClient(codex_provider=provider)  # type: ignore[arg-type]
        result = client.chat([], model="gemini-2.5-flash", system_prompt="Be helpful.")

        self.assertEqual(result.text, "hello")
        self.assertEqual(provider.body["model"], "gpt-5.5")
        self.assertFalse(provider.body["store"])
        self.assertTrue(provider.body["stream"])
        self.assertEqual(provider.body["instructions"], "Be helpful.")


class AgentTests(unittest.TestCase):
    def test_route_keeps_instructional_click_question_as_chat(self) -> None:
        self.assertEqual(HelplerAgent._route("how do I click a button?"), "chat")
        self.assertEqual(HelplerAgent._route("click the start button"), "computer_use")

    def test_chat_route_does_not_capture_screenshot(self) -> None:
        class Client:
            def chat(self, messages, *, model, system_prompt=None, **_kwargs):  # noqa: ANN001
                self.messages = messages
                return ChatResult(text="hello", tool_calls=[])

        def fail_capture() -> Capture:
            raise AssertionError("plain chat should not capture the screen")

        client = Client()
        agent = HelplerAgent(capture_provider=fail_capture, client=client)  # type: ignore[arg-type]
        prompt = "how do I install python"
        session, turn = agent.start_guide(prompt)

        self.assertEqual(turn.message, "hello")
        self.assertIsNone(turn.capture)
        self.assertEqual(session.history.conversation_messages()[0].text, prompt)

    def test_open_chrome_uses_fast_local_action(self) -> None:
        class Client:
            def chat(self, *args, **kwargs):  # noqa: ANN001
                raise AssertionError("fast app launch should not call chat")

            def computer_use_step(self, *args, **kwargs):  # noqa: ANN001
                raise AssertionError("fast app launch should not call computer use")

        class Dispatcher:
            def __init__(self) -> None:
                self.actions = []

            def dispatch(self, action):  # noqa: ANN001
                self.actions.append(action)
                return {"status": "executed", "action": action.name}

        def fail_capture() -> Capture:
            raise AssertionError("fast app launch should not capture the screen")

        dispatcher = Dispatcher()
        agent = HelplerAgent(
            capture_provider=fail_capture,
            client=Client(),  # type: ignore[arg-type]
            dispatcher=dispatcher,  # type: ignore[arg-type]
        )
        _session, turn = agent.start_guide("open chrome")

        self.assertTrue(turn.done)
        self.assertIsNone(turn.capture)
        self.assertEqual(turn.actions[0].name, "launch_app")
        self.assertEqual(turn.actions[0].raw_args["command"], "chrome")
        self.assertTrue(turn.actions[0].raw_args["fast_local"])
        self.assertEqual(dispatcher.actions[0].name, "launch_app")

    def test_web_search_uses_fast_local_action(self) -> None:
        class Client:
            def chat(self, *args, **kwargs):  # noqa: ANN001
                raise AssertionError("fast web search should not call chat")

            def computer_use_step(self, *args, **kwargs):  # noqa: ANN001
                raise AssertionError("fast web search should not call computer use")

        class Dispatcher:
            def __init__(self) -> None:
                self.actions = []

            def dispatch(self, action):  # noqa: ANN001
                self.actions.append(action)
                return {"status": "executed", "action": action.name}

        def fail_capture() -> Capture:
            raise AssertionError("fast web search should not capture the screen")

        dispatcher = Dispatcher()
        agent = HelplerAgent(
            capture_provider=fail_capture,
            client=Client(),  # type: ignore[arg-type]
            dispatcher=dispatcher,  # type: ignore[arg-type]
        )
        _session, turn = agent.start_guide("search for helper app")

        self.assertTrue(turn.done)
        self.assertEqual(turn.actions[0].name, "navigate")
        self.assertEqual(
            turn.actions[0].raw_args["url"],
            "https://www.google.com/search?q=helper+app",
        )
        self.assertEqual(dispatcher.actions[0].name, "navigate")

    def test_action_outputs_are_recorded_with_call_ids(self) -> None:
        capture = Capture(
            png_bytes=_png_bytes(),
            width=100,
            height=100,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )

        class Client:
            def computer_use_step(self, messages, *, model, system_prompt=None, **_kwargs):  # noqa: ANN001
                return ChatResult(
                    text="",
                    tool_calls=[
                        ToolCall("click_at", {"x": 50, "y": 50}, call_id="call_1"),
                    ],
                )

        class Dispatcher:
            def dispatch(self, action):  # noqa: ANN001
                return {"status": "guided", "action": action.name}

        agent = HelplerAgent(
            capture_provider=lambda: capture,
            client=Client(),  # type: ignore[arg-type]
            dispatcher=Dispatcher(),  # type: ignore[arg-type]
        )
        session, _ = agent.start_guide("click the button")
        messages = session.history.build_messages()

        self.assertTrue(
            any(
                part.get("function_response", {}).get("call_id") == "call_1"
                for message in messages
                for part in message.get("parts", [])
            )
        )

    def test_coordinates_are_clamped(self) -> None:
        capture = Capture(
            png_bytes=_png_bytes(),
            width=100,
            height=50,
            monitor_left=10,
            monitor_top=20,
            scale=1.0,
        )
        agent = HelplerAgent(client=object())  # type: ignore[arg-type]
        self.assertEqual(agent._screen_point(capture, 5000, -10), (109, 20))

    def test_plan_next_step_includes_control_candidates(self) -> None:
        from control_inventory import ControlCandidate
        from history import HistoryManager

        capture = Capture(
            png_bytes=_png_bytes(),
            width=1000,
            height=1000,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )

        class Client:
            def chat(self, messages, *, model, system_prompt=None, **_kwargs):  # noqa: ANN001
                self.messages = messages
                return ChatResult(
                    text=json.dumps(
                        {
                            "kind": "step",
                            "instruction": "Click Save.",
                            "target_id": "c001",
                        }
                    ),
                    tool_calls=[],
                )

        client = Client()
        history = HistoryManager()
        history.add_user_turn("Click save", screenshot=capture)
        agent = HelplerAgent(client=client)  # type: ignore[arg-type]

        decision = agent.plan_next_step(
            history,
            control_candidates=[
                ControlCandidate("c001", "Save", "button", (100, 100, 60, 30))
            ],
            capture=capture,
        )

        self.assertEqual(decision.target_id, "c001")
        latest_parts = client.messages[-1]["parts"]
        prompt_text = latest_parts[-1]["text"]
        self.assertTrue(any("image_png" in part for part in latest_parts))
        self.assertIn("Visible clickable controls", prompt_text)
        self.assertIn("c001", prompt_text)
        self.assertIn("valid only for this screenshot", prompt_text)

    def test_live_help_history_text_does_not_persist_target_ids(self) -> None:
        from agent import LiveHelpDecision

        decision = LiveHelpDecision(
            kind="step",
            instruction="Click Save.",
            target_id="c001",
            target_norm_x=100,
            target_norm_y=100,
            target_norm_width=50,
            target_norm_height=30,
        )

        self.assertEqual(decision.history_text, "Suggested step: Click Save.")
        self.assertNotIn("target_id", decision.history_text)


class ComputerControlTests(unittest.TestCase):
    def test_enter_detection_and_dangerous_text(self) -> None:
        controller = ComputerController()
        self.assertTrue(controller._contains_enter_key(["ctrl", "enter"]))
        self.assertEqual(
            controller._dangerous_text("type_text", {"text": "please delete the file"}),
            "please delete the file",
        )

    def test_horizontal_scroll_is_not_mapped_to_vertical(self) -> None:
        from main import DesktopActionDispatcher

        self.assertIsNone(DesktopActionDispatcher._scroll_delta("left"))
        self.assertIsNone(DesktopActionDispatcher._scroll_delta("right"))


class HelpSessionMessageTests(unittest.TestCase):
    def test_downgrade_note_includes_specific_rejection_reason(self) -> None:
        from agent import LiveHelpDecision
        from help_session import HelpSession

        note = HelpSession._outcome_after_downgrade(
            LiveHelpDecision(kind="step", instruction="Click Save."),
            "unknown target_id",
        )

        self.assertIn("unknown target_id", note)
        self.assertNotIn("panel-sized", note)


class DesktopWindowTests(unittest.TestCase):
    def test_helper_logo_icon_is_loadable(self) -> None:
        app = _qt_app()
        self.assertTrue(main.APP_ICON_PATH.exists())
        self.assertFalse(main.helper_app_icon(app).isNull())

    def test_chat_window_is_normal_taskbar_window_and_close_requests_quit(self) -> None:
        app = _qt_app()
        window = ChatWindow()

        flags = window.windowFlags()
        self.assertTrue(flags & Qt.WindowType.Window)
        self.assertEqual(
            flags & Qt.WindowType.WindowType_Mask,
            Qt.WindowType.Window,
        )

        close_requested: list[bool] = []
        window.close_requested.connect(lambda: close_requested.append(True))
        window.close()
        app.processEvents()

        self.assertTrue(close_requested)

    def test_chat_show_restores_minimized_window(self) -> None:
        app = _qt_app()
        window = ChatWindow()

        window.show()
        app.processEvents()
        window.showMinimized()
        app.processEvents()

        window.show_chat()
        app.processEvents()

        self.assertTrue(window.isVisible())
        self.assertFalse(window.isMinimized())
        window.hide()
        window.deleteLater()

    def test_chat_window_placeholder_tracks_mode(self) -> None:
        app = _qt_app()
        window = ChatWindow()

        self.assertEqual(window._input.placeholderText(), "Ask helper how to...")
        window.set_mode(FloatingCircle.ACTIVE)
        self.assertEqual(window._input.placeholderText(), "Tell Helper what to do...")
        window.set_mode(FloatingCircle.HELP)
        self.assertEqual(window._input.placeholderText(), "Ask helper how to...")
        app.processEvents()
        window.deleteLater()

    def test_floating_chat_placeholder_tracks_mode(self) -> None:
        app = _qt_app()
        widget = FloatingCircle()

        self.assertEqual(widget._chat_input.placeholderText(), "Ask helper how to...")
        widget.set_mode(FloatingCircle.ACTIVE)
        self.assertEqual(widget._chat_input.placeholderText(), "Tell Helper what to do...")
        widget.set_mode(FloatingCircle.HELP)
        self.assertEqual(widget._chat_input.placeholderText(), "Ask helper how to...")
        app.processEvents()
        widget.deleteLater()

    def test_dashboard_is_normal_secondary_window_and_close_does_not_quit(self) -> None:
        app = _qt_app()
        quit_requested: list[bool] = []
        app.aboutToQuit.connect(lambda: quit_requested.append(True))

        with tempfile.TemporaryDirectory() as tmp:
            window = DashboardWindow(
                env_path=Path(tmp) / ".env",
                status_provider=lambda: {},
                restart_callback=lambda: None,
            )
            self.addCleanup(window.deleteLater)

            flags = window.windowFlags()
            self.assertTrue(flags & Qt.WindowType.Window)
            self.assertEqual(
                flags & Qt.WindowType.WindowType_Mask,
                Qt.WindowType.Window,
            )

            closed: list[bool] = []
            window.closed.connect(lambda: closed.append(True))
            window.close()
            app.processEvents()

        self.assertTrue(closed)
        self.assertFalse(quit_requested)


class ConversationStoreTests(unittest.TestCase):
    def _store(self, tmpdir: str):
        from conversation_store import ConversationStore

        return ConversationStore(Path(tmpdir) / "conversations.json")

    def test_load_returns_empty_when_missing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            self.assertEqual(store.load_all(), [])

    def test_save_round_trip(self) -> None:
        from conversation_store import StoredConversation

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            convo = StoredConversation.new(started_at=1_700_000_000.0)
            convo.add_message("user", "hello there", when=1_700_000_001.0)
            convo.add_message("assistant", "hi back", when=1_700_000_002.0)
            convo.title = convo.derive_title()
            store.save(convo)

            loaded = store.load_all()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(loaded[0].id, convo.id)
            self.assertEqual(loaded[0].title, "hello there")
            self.assertEqual(len(loaded[0].messages), 2)
            self.assertEqual(loaded[0].messages[0].role, "user")
            self.assertEqual(loaded[0].messages[1].text, "hi back")

    def test_save_sorts_newest_first(self) -> None:
        from conversation_store import StoredConversation

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            older = StoredConversation.new(started_at=1_700_000_000.0)
            older.add_message("user", "old", when=1_700_000_000.0)
            newer = StoredConversation.new(started_at=1_700_001_000.0)
            newer.add_message("user", "new", when=1_700_001_000.0)
            store.save(older)
            store.save(newer)

            loaded = store.load_all()
            self.assertEqual(len(loaded), 2)
            self.assertEqual(loaded[0].id, newer.id)
            self.assertEqual(loaded[1].id, older.id)

    def test_save_replaces_existing_by_id(self) -> None:
        from conversation_store import StoredConversation

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            convo = StoredConversation.new(started_at=1_700_000_000.0)
            convo.add_message("user", "v1")
            convo.title = "v1"
            store.save(convo)

            convo.add_message("user", "v2")
            convo.title = "v1"
            store.save(convo)

            loaded = store.load_all()
            self.assertEqual(len(loaded), 1)
            self.assertEqual(len(loaded[0].messages), 2)

    def test_load_tolerates_corrupt_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            store.path.parent.mkdir(parents=True, exist_ok=True)
            store.path.write_text("not json {{{", encoding="utf-8")
            self.assertEqual(store.load_all(), [])

    def test_atomic_write_no_partial_on_failure(self) -> None:
        from conversation_store import StoredConversation

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            convo = StoredConversation.new()
            convo.add_message("user", "first")
            convo.title = "first"
            store.save(convo)

            original_bytes = store.path.read_bytes()

            with patch("conversation_store.json.dump", side_effect=RuntimeError("boom")):
                with self.assertRaises(RuntimeError):
                    convo.add_message("user", "second")
                    store.save(convo)

            self.assertEqual(store.path.read_bytes(), original_bytes)
            leftover = list(store.path.parent.glob(".conversations-*"))
            self.assertEqual(leftover, [])

    def test_derive_title_truncates_long_text(self) -> None:
        from conversation_store import StoredConversation, TITLE_MAX_LEN

        convo = StoredConversation.new()
        long_text = "a" * (TITLE_MAX_LEN + 20)
        convo.add_message("user", long_text)
        title = convo.derive_title()
        self.assertLessEqual(len(title), TITLE_MAX_LEN)
        self.assertTrue(title.endswith("…"))

    def test_delete_removes_entry(self) -> None:
        from conversation_store import StoredConversation

        with tempfile.TemporaryDirectory() as tmp:
            store = self._store(tmp)
            convo = StoredConversation.new()
            convo.add_message("user", "hi")
            convo.title = "hi"
            store.save(convo)

            self.assertTrue(store.delete(convo.id))
            self.assertEqual(store.load_all(), [])
            self.assertFalse(store.delete(convo.id))


class HistoryScreenshotTests(unittest.TestCase):
    def test_capture_within_budget_is_not_reencoded(self) -> None:
        from history import HistoryManager

        png = _png_bytes(80, 60)
        capture = Capture(
            png_bytes=png,
            width=80,
            height=60,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )
        manager = HistoryManager(screenshot_max_edge=128)
        manager.add_user_turn(text="look", screenshot=capture)
        messages = manager.build_messages()
        image_parts = [
            part for msg in messages for part in msg.get("parts", []) if "image_png" in part
        ]
        self.assertEqual(len(image_parts), 1)
        self.assertIs(image_parts[0]["image_png"], png)

    def test_capture_above_budget_is_resized(self) -> None:
        from history import HistoryManager

        png = _png_bytes(400, 300)
        capture = Capture(
            png_bytes=png,
            width=400,
            height=300,
            monitor_left=0,
            monitor_top=0,
            scale=1.0,
        )
        manager = HistoryManager(screenshot_max_edge=128)
        manager.add_user_turn(text="look", screenshot=capture)
        messages = manager.build_messages()
        image_parts = [
            part for msg in messages for part in msg.get("parts", []) if "image_png" in part
        ]
        self.assertEqual(len(image_parts), 1)
        self.assertIsNot(image_parts[0]["image_png"], png)


class ParseLiveHelpDecisionTests(unittest.TestCase):
    def test_step_decision(self) -> None:
        from agent import _parse_live_help_decision

        payload = json.dumps(
            {
                "kind": "step",
                "instruction": "Click the Start button.",
                "target": {"x": 10, "y": 980, "width": 40, "height": 40},
                "expected_change": "Start menu opens.",
            }
        )
        decision = _parse_live_help_decision(payload)
        self.assertEqual(decision.kind, "step")
        self.assertEqual(decision.instruction, "Click the Start button.")
        self.assertEqual(decision.target_norm_x, 10)
        self.assertEqual(decision.target_norm_y, 980)
        self.assertEqual(decision.target_norm_width, 40)
        self.assertEqual(decision.target_norm_height, 40)
        self.assertEqual(decision.expected_change, "Start menu opens.")

    def test_step_decision_accepts_target_id_without_rect(self) -> None:
        from agent import _parse_live_help_decision

        payload = json.dumps(
            {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "expected_change": "The file is saved.",
            }
        )
        decision = _parse_live_help_decision(payload)
        self.assertEqual(decision.kind, "step")
        self.assertEqual(decision.target_id, "c001")
        self.assertFalse(decision.has_target_rect)

    def test_step_preserves_tiny_target_geometry(self) -> None:
        from agent import _parse_live_help_decision

        payload = json.dumps(
            {
                "kind": "step",
                "instruction": "Tiny target.",
                "target": {"x": 0, "y": 0, "width": 2, "height": 3},
            }
        )
        decision = _parse_live_help_decision(payload)
        self.assertEqual(decision.kind, "step")
        self.assertEqual(decision.target_norm_width, 2)
        self.assertEqual(decision.target_norm_height, 3)

    def test_done_decision(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps({"kind": "done", "message": "All set!"})
        )
        self.assertEqual(decision.kind, "done")
        self.assertEqual(decision.message, "All set!")

    def test_narrate_decision(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps({"kind": "narrate", "message": "Open Settings first."})
        )
        self.assertEqual(decision.kind, "narrate")
        self.assertEqual(decision.message, "Open Settings first.")

    def test_step_missing_target_falls_back_to_narrate(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps({"kind": "step", "instruction": "Click something."})
        )
        self.assertEqual(decision.kind, "narrate")
        self.assertEqual(decision.message, "Click something.")

    def test_step_with_invalid_coords_falls_back_to_narrate(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps(
                {
                    "kind": "step",
                    "instruction": "Click here.",
                    "target": {"x": "bad", "y": "bad", "width": "bad", "height": "bad"},
                }
            )
        )
        self.assertEqual(decision.kind, "narrate")
        self.assertEqual(decision.message, "Click here.")

    def test_step_with_out_of_range_coords_falls_back_to_narrate(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps(
                {
                    "kind": "step",
                    "instruction": "Click here.",
                    "target": {"x": 1200, "y": 40, "width": 80, "height": 40},
                }
            )
        )
        self.assertEqual(decision.kind, "narrate")
        self.assertEqual(decision.message, "Click here.")

    def test_step_with_target_id_ignores_out_of_range_rect(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps(
                {
                    "kind": "step",
                    "instruction": "Click Save.",
                    "target_id": "c001",
                    "target": {"x": -50, "y": 40, "width": 80, "height": 40},
                }
            )
        )
        self.assertEqual(decision.kind, "step")
        self.assertEqual(decision.target_id, "c001")
        self.assertFalse(decision.has_target_rect)

    def test_garbage_input_falls_back_to_narrate(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision("not json at all")
        self.assertEqual(decision.kind, "narrate")
        self.assertTrue(decision.message)

    def test_helper_action_is_sanitized(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps(
                {
                    "kind": "narrate",
                    "message": "Opening Settings for you.",
                    "helper_action": {
                        "name": "launch_app",
                        "command": "ms-settings:",
                        "evil": ["not", "a", "primitive"],
                    },
                }
            )
        )
        self.assertEqual(decision.kind, "narrate")
        assert decision.helper_action is not None
        self.assertEqual(decision.helper_action["name"], "launch_app")
        self.assertEqual(decision.helper_action["command"], "ms-settings:")
        self.assertNotIn("evil", decision.helper_action)

    def test_unknown_helper_action_dropped(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps(
                {
                    "kind": "narrate",
                    "message": "Test.",
                    "helper_action": {"name": "click_at", "x": 100, "y": 100},
                }
            )
        )
        self.assertIsNone(decision.helper_action)

    def test_screen_rect_translation(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps(
                {
                    "kind": "step",
                    "instruction": "Click here.",
                    "target": {"x": 100, "y": 100, "width": 100, "height": 100},
                }
            )
        )
        capture = Capture(
            png_bytes=_png_bytes(),
            width=1000,
            height=1000,
            monitor_left=50,
            monitor_top=20,
            scale=1.0,
        )
        rect = decision.screen_rect(capture)
        self.assertEqual(rect, (150, 120, 100, 100))

    def test_screen_rect_clamps_right_bottom_to_capture_bounds(self) -> None:
        from agent import _parse_live_help_decision

        decision = _parse_live_help_decision(
            json.dumps(
                {
                    "kind": "step",
                    "instruction": "Click the edge.",
                    "target": {"x": 980, "y": 990, "width": 80, "height": 50},
                }
            )
        )
        capture = Capture(
            png_bytes=_png_bytes(),
            width=1000,
            height=1000,
            monitor_left=50,
            monitor_top=20,
            scale=2.0,
        )

        rect = decision.screen_rect(capture)

        self.assertEqual(rect, (540, 515, 10, 8))


@unittest.skipUnless(
    __import__("sys").platform.startswith("win"),
    "ClickSensor depends on Windows user32.",
)
class ClickSensorDispatchTests(unittest.TestCase):
    def _make_lparam(self, x: int, y: int):
        import ctypes
        from ctypes import wintypes

        from click_sensor import _MSLLHOOKSTRUCT

        struct = _MSLLHOOKSTRUCT()
        struct.pt = wintypes.POINT(x, y)
        struct.mouseData = 0
        struct.flags = 0
        struct.time = 0
        struct.dwExtraInfo = None
        # Keep a reference on self so the struct is not garbage-collected during the call.
        self._struct_ref = struct
        return ctypes.cast(ctypes.pointer(struct), ctypes.c_void_p).value

    def test_click_inside_target_fires_with_in_target_true(self) -> None:
        from click_sensor import ClickSensor, HC_ACTION, WM_LBUTTONDOWN

        received: list[tuple[int, int, bool]] = []
        sensor = ClickSensor(lambda x, y, in_target: received.append((x, y, in_target)))
        sensor.set_target(100, 100, 50, 30)
        sensor._low_level_proc(HC_ACTION, WM_LBUTTONDOWN, self._make_lparam(120, 115))
        self.assertEqual(received, [(120, 115, True)])

    def test_click_outside_target_fires_with_in_target_false(self) -> None:
        from click_sensor import ClickSensor, HC_ACTION, WM_LBUTTONDOWN

        received: list[tuple[int, int, bool]] = []
        sensor = ClickSensor(lambda x, y, in_target: received.append((x, y, in_target)))
        sensor.set_target(100, 100, 50, 30)
        sensor._low_level_proc(HC_ACTION, WM_LBUTTONDOWN, self._make_lparam(10, 10))
        self.assertEqual(received, [(10, 10, False)])

    def test_no_target_means_no_callback(self) -> None:
        from click_sensor import ClickSensor, HC_ACTION, WM_LBUTTONDOWN

        received: list[tuple[int, int, bool]] = []
        sensor = ClickSensor(lambda x, y, in_target: received.append((x, y, in_target)))
        sensor.clear_target()
        sensor._low_level_proc(HC_ACTION, WM_LBUTTONDOWN, self._make_lparam(120, 115))
        self.assertEqual(received, [])


if __name__ == "__main__":
    unittest.main()
