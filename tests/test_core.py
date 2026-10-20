from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import requests
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QApplication

import config
import main
import token_store
from ui_chat import ChatWindow
from ui_dashboard import DashboardWindow
from ui_widget import FloatingCircle
from openai_client import (
    BadProviderResponse,
    CodexProvider,
    OpenAIClient,
    RateLimited,
    UnsupportedModel,
    _parse_chat_completions,
    _parse_response,
    _parse_stream_response,
)


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
            self.assertEqual(config.resolve_codex_model("HELPER_REASONING_MODEL"), config.CODEX_MODEL_DEFAULT)
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


class ConfigurationHardeningTests(unittest.TestCase):
    def test_invalid_numeric_settings_fall_back(self) -> None:
        with patch.object(config, "env_value", return_value="not-a-number"):
            self.assertEqual(config._int_env("HELPER_TEST", 12, lo=1, hi=20), 12)
            self.assertEqual(config._float_env("HELPER_TEST", 0.5, lo=0.0, hi=1.0), 0.5)

        with patch.object(config, "env_value", return_value="nan"):
            self.assertEqual(config._float_env("HELPER_TEST", 0.5, lo=0.0, hi=1.0), 0.5)

    def test_numeric_settings_are_clamped(self) -> None:
        with patch.object(config, "env_value", return_value="999"):
            self.assertEqual(config._int_env("HELPER_TEST", 12, lo=1, hi=20), 20)
        with patch.object(config, "env_value", return_value="-1"):
            self.assertEqual(config._float_env("HELPER_TEST", 0.5, lo=0.0, hi=1.0), 0.0)

    def test_custom_api_url_requires_tls_except_loopback(self) -> None:
        self.assertIsNone(config.validate_api_base_url("https://router.example/v1"))
        self.assertIsNone(config.validate_api_base_url("http://127.0.0.1:8080/v1"))
        self.assertIsNotNone(config.validate_api_base_url("http://router.example/v1"))
        self.assertIsNotNone(config.validate_api_base_url("https://user:secret@router.example/v1"))


class EnvIoTests(unittest.TestCase):
    def test_write_and_read_round_trip_special_characters(self) -> None:
        from env_io import read_env, write_env

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            value = 'api"key\\with # spaces'
            write_env(path, {"HELPER_API_KEY": value})
            self.assertEqual(read_env(path)["HELPER_API_KEY"], value)


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


class AnthropicProviderTests(unittest.TestCase):
    def _body(self, model: str) -> dict:
        from anthropic_client import AnthropicProvider

        provider = AnthropicProvider(api_key="test-key")
        return provider._body(
            [{"role": "user", "parts": [{"text": "hi"}]}],
            model=model,
            system_prompt=None,
            temperature=0.7,
            max_tokens=256,
        )

    def test_temperature_sent_to_models_that_accept_it(self) -> None:
        self.assertEqual(self._body("claude-sonnet-4-6").get("temperature"), 0.7)
        self.assertEqual(self._body("claude-haiku-4-5").get("temperature"), 0.7)

    def test_temperature_omitted_for_sampling_removed_models(self) -> None:
        # Opus 4.7+ and the Fable/Mythos families reject sampling params with
        # HTTP 400, so the body must not include temperature for them.
        for model in (
            "claude-opus-4-7",
            "claude-opus-4-8",
            "claude-fable-5",
            "claude-mythos-5",
        ):
            with self.subTest(model=model):
                self.assertNotIn("temperature", self._body(model))

    def test_curated_anthropic_menu_models_build_valid_bodies(self) -> None:
        import config
        from anthropic_client import model_accepts_temperature

        for model in config.PROVIDER_MODELS["anthropic"]:
            with self.subTest(model=model):
                body = self._body(model)
                self.assertEqual(body["model"], model)
                self.assertEqual(
                    "temperature" in body, model_accepts_temperature(model)
                )


class ProviderTests(unittest.TestCase):
    def test_parse_response_reads_text(self) -> None:
        result = _parse_response(
            {"output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]}
        )
        self.assertEqual(result.text, "hello")

    def test_parse_response_rejects_non_object(self) -> None:
        with self.assertRaises(BadProviderResponse):
            _parse_response([])  # type: ignore[arg-type]

    def test_parse_response_rejects_malformed_nested_items(self) -> None:
        with self.assertRaises(BadProviderResponse):
            _parse_response({"output": [None]})  # type: ignore[list-item]

    def test_chat_completions_parser_rejects_malformed_choice(self) -> None:
        with self.assertRaises(BadProviderResponse):
            _parse_chat_completions({"choices": [None]})  # type: ignore[list-item]

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
            with self.assertRaisesRegex(UnsupportedModel, f"gemini-2.5-flash.*{config.CODEX_MODEL_DEFAULT}"):
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
        result = client.chat([{"role": "user", "parts": [{"text": "hi"}]}],
                             model="gemini-2.5-flash", system_prompt="Be helpful.")

        self.assertEqual(result.text, "hello")
        self.assertEqual(provider.body["model"], config.CODEX_MODEL_DEFAULT)
        self.assertFalse(provider.body["store"])
        self.assertTrue(provider.body["stream"])
        self.assertEqual(provider.body["instructions"], "Be helpful.")

    def test_gemini_parser_rejects_malformed_candidate(self) -> None:
        from gemini_client import _parse_generate

        with self.assertRaises(BadProviderResponse):
            _parse_generate({"candidates": [None]})  # type: ignore[list-item]

    def test_oauth_parser_normalizes_bad_expiry_to_login_error(self) -> None:
        from oauth_codex import LoginError, _token_set_from_response

        with self.assertRaises(LoginError):
            _token_set_from_response(
                {"access_token": "access", "expires_in": "not-a-number"},
                prev_refresh=None,
            )

    def test_oauth_callback_escapes_error_description_html(self) -> None:
        # error_description comes from the authorization server (untrusted):
        # the localhost error page must not reflect it into HTML raw.
        from oauth_codex import _build_handler

        import io as _io

        class FakeCompletion:
            def __init__(self):
                self.set_calls = 0

            def set(self):
                self.set_calls += 1

        result: dict[str, str] = {}
        completion = FakeCompletion()
        handler_cls = _build_handler(result, completion)
        handler = handler_cls.__new__(handler_cls)
        handler.path = (
            "/auth/callback?error=access_denied"
            "&error_description=%3Cscript%3Ealert(1)%3C%2Fscript%3E"
        )
        handler.wfile = _io.BytesIO()
        handler.send_response = lambda code: None  # type: ignore[method-assign]
        handler.send_header = lambda k, v: None  # type: ignore[method-assign]
        handler.end_headers = lambda: None  # type: ignore[method-assign]
        handler.do_GET()

        body = handler.wfile.getvalue().decode("utf-8")
        self.assertNotIn("<script>", body)
        self.assertIn("&lt;script&gt;", body)
        self.assertEqual(completion.set_calls, 1)


class LaunchSafetyTests(unittest.TestCase):
    def test_shell_metacharacters_are_rejected(self) -> None:
        from launch_safety import LaunchError, launch_target

        with self.assertRaises(LaunchError):
            launch_target("notepad & whoami")

    def test_command_launch_uses_shell_free_process(self) -> None:
        from launch_safety import launch_target

        with patch("launch_safety.subprocess.Popen") as popen:
            launch_target("notepad")
        popen.assert_called_once()
        self.assertFalse(popen.call_args.kwargs["shell"])

    def test_automatic_helper_launches_are_allowlisted(self) -> None:
        from launch_safety import HELP_LAUNCH_COMMANDS, LaunchError, launch_target

        with self.assertRaises(LaunchError):
            launch_target("powershell -Command whoami", allowed_commands=HELP_LAUNCH_COMMANDS)

    def test_url_labels_do_not_include_query_secrets(self) -> None:
        from launch_safety import safe_target_label

        label = safe_target_label("https://user:secret@example.com/path?token=secret")
        self.assertEqual(label, "https://example.com/path")
        self.assertNotIn("secret", label)

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

    def test_chat_window_accepts_questions_and_tasks_without_mode_switches(self) -> None:
        app = _qt_app()
        window = ChatWindow()
        self.assertEqual(window._input.placeholderText(), "Ask anything or give me a task…")
        self.assertFalse(hasattr(window, "_mode_buttons"))
        app.processEvents()
        window.deleteLater()

    def test_floating_chat_accepts_questions_and_tasks_without_mode_switches(self) -> None:
        app = _qt_app()
        widget = FloatingCircle()
        self.assertEqual(widget._chat_input.placeholderText(), "Ask anything or give me a task…")
        self.assertFalse(hasattr(widget, "mode_toggle_requested"))
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

            convo.add_message("user", "legacy")
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


class ConversationSnapshotTests(unittest.TestCase):
    def test_active_autosaved_conversation_is_rendered_only_as_live(self) -> None:
        from conversation_store import ConversationStore, StoredConversation

        with tempfile.TemporaryDirectory() as tmp:
            store = ConversationStore(Path(tmp) / "conversations.json")
            active = StoredConversation.new(started_at=1_700_001_000.0)
            active.add_message("user", "active", when=1_700_001_001.0)
            other = StoredConversation.new(started_at=1_700_000_000.0)
            other.add_message("user", "older", when=1_700_000_001.0)
            store.save(active)
            store.save(other)

            # The dashboard method only needs these two attributes; using a
            # lightweight harness keeps this regression test headless.
            app = SimpleNamespace(
                _conversation_store=store,
                _active_conversation=active,
            )
            snapshot = main.HelplerDesktopApp._conversations_snapshot(app)  # type: ignore[arg-type]

            self.assertEqual([conversation.id for conversation in snapshot["stored"]], [other.id])
            self.assertIsNotNone(snapshot["live"])
            self.assertEqual(snapshot["live"].id, active.id)


