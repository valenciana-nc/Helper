"""Claude subscription routing, Messages compatibility, and native account UI with no real credentials."""
from __future__ import annotations

from contextlib import contextmanager
import io
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image
from PyQt6.QtWidgets import QApplication, QLineEdit
import requests

from anthropic_client import AnthropicProvider, OAUTH_BETAS, OAUTH_SYSTEM_PREFIX
import config
from env_io import read_env, write_env
from helper_engine.model import ProviderModel
from helper_engine.types import Observation
import oauth_anthropic as auth
from openai_client import AuthExpired, BadProviderResponse, NotSignedIn, ProviderError, RateLimited, make_openai_client
from ui_dashboard import DashboardWindow


def reply(text="Ready", status=200, **fields):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"stop_reason": "end_turn", "content": [{"type": "text", "text": text}], **fields}).encode()
    return response


class AnthropicSubscriptionTests(unittest.TestCase):
    def test_explicit_auth_modes_and_existing_api_key_precedence(self):
        for mode, key, expected in (("auto", "key", "api_key"), ("auto", "", "oauth"),
                                    ("oauth", "key", "oauth"), ("api_key", "", "api_key")):
            with (self.subTest(mode=mode, key=key), patch.object(config, "ANTHROPIC_AUTH_MODE", mode),
                  patch.object(config, "ANTHROPIC_API_KEY", key)):
                self.assertEqual(config.anthropic_auth_mode(), expected)

    def test_missing_subscription_never_uses_codex_or_existing_billed_api_key(self):
        with (patch.object(config, "PROVIDER", "anthropic"), patch.object(config, "ANTHROPIC_AUTH_MODE", "oauth"),
              patch.object(config, "ANTHROPIC_API_KEY", "unused-key"), patch.object(auth, "is_signed_in", return_value=False),
              patch("openai_client.CodexProvider") as codex, self.assertRaises(NotSignedIn)):
            make_openai_client()
        codex.assert_not_called()

    def test_engine_sends_image_schema_and_helper_prompt_with_oauth_compatibility(self):
        decision = '{"kind":"answer","observation_id":"fixture","message":"Ready"}'
        session = Mock()
        session.post.return_value = reply(decision)
        output = io.BytesIO()
        Image.new("RGB", (80, 60), "white").save(output, "PNG")
        observation = Observation("fixture", "desktop", "scope", "Test", output.getvalue(), 80, 60)
        with (patch.object(config, "PROVIDER", "anthropic"), patch.object(config, "ANTHROPIC_AUTH_MODE", "oauth"),
              patch.object(config, "ANTHROPIC_API_KEY", "unused-key"), patch.object(config, "API_MODEL", "unused-model"),
              patch.object(auth, "is_signed_in", return_value=True),
              patch.object(auth, "get_access_token", return_value="sk-ant-oat-fixture"),
              patch("anthropic_client.requests.Session", return_value=session),
              patch("oauth_codex.get_access_token") as codex_token):
            client = make_openai_client(optional_api_key="fixture-voice")
            result = ProviderModel(client, "claude-sonnet-4-6").decide("Describe the screen", observation, {"mode": "active"})
        self.assertEqual(result.message, "Ready")
        codex_token.assert_not_called()
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(session.post.call_args.args[0], "https://api.anthropic.com/v1/messages")
        request = session.post.call_args.kwargs
        self.assertFalse(request["allow_redirects"])
        self.assertEqual(request["headers"]["Authorization"], "Bearer sk-ant-oat-fixture")
        self.assertEqual(request["headers"]["anthropic-beta"], OAUTH_BETAS)
        self.assertEqual(request["headers"]["user-agent"], "claude-cli/2.1.280")
        self.assertEqual(request["headers"]["x-app"], "cli")
        self.assertNotIn("x-api-key", request["headers"])
        body = json.loads(request["data"])
        self.assertEqual(body["model"], "claude-sonnet-4-6")
        self.assertEqual(body["system"][0]["text"], OAUTH_SYSTEM_PREFIX)
        self.assertIn("You are Helper", body["system"][1]["text"])
        self.assertIn("observation_id", body["system"][1]["text"])
        self.assertNotIn("tools", body)
        self.assertEqual([part["type"] for part in body["messages"][0]["content"]], ["text", "image"])
        self.assertEqual(body["messages"][0]["content"][1]["source"]["media_type"], "image/png")
        self.assertEqual(client._api_key, "fixture-voice")

    def test_api_key_path_does_not_get_oauth_headers_or_system_prefix(self):
        session = Mock()
        session.post.return_value = reply()
        with patch.object(auth, "get_access_token") as token:
            provider = AnthropicProvider("fixture-api", session)
            self.assertEqual(provider.chat([{"role": "user", "parts": [{"text": "Hi"}]}],
                                           model="claude-sonnet-4-6", system_prompt="Helper").text, "Ready")
        token.assert_not_called()
        request = session.post.call_args.kwargs
        self.assertEqual(request["headers"]["x-api-key"], "fixture-api")
        self.assertNotIn("Authorization", request["headers"])
        self.assertNotIn("anthropic-beta", request["headers"])
        self.assertEqual(json.loads(request["data"])["system"], "Helper")

    def test_401_refreshes_once_and_later_retries_do_not_repeat_refresh(self):
        session = Mock()
        session.post.side_effect = [reply(status=401), reply(status=503), reply()]
        with (patch.object(auth, "get_access_token", side_effect=["old", "new", "new"]) as token,
              patch("anthropic_client.time.sleep")):
            result = AnthropicProvider("", session, subscription=True).chat(
                [{"role": "user", "parts": [{"text": "Hi"}]}], model="claude")
        self.assertEqual(result.text, "Ready")
        self.assertEqual(token.call_args_list, [call(force_refresh=False), call(force_refresh=True), call(force_refresh=False)])
        self.assertEqual(session.post.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer new")

    def test_auth_quota_and_incomplete_responses_do_not_erase_other_accounts(self):
        for response, expected in ((reply(status=401), AuthExpired), (reply(status=403), ProviderError),
                                   (reply(status=429), RateLimited), (reply(status=400), ProviderError),
                                   (reply(stop_reason="max_tokens"), BadProviderResponse),
                                   (reply(stop_reason="refusal"), BadProviderResponse), (reply(""), BadProviderResponse)):
            session = Mock()
            session.post.return_value = response
            with (self.subTest(expected=expected), patch.object(auth, "get_access_token", return_value="token"),
                  patch.object(auth, "sign_out") as logout, patch("token_store.clear") as clear,
                  patch("anthropic_client.time.sleep"), self.assertRaises(expected)):
                AnthropicProvider("", session, subscription=True).chat(
                    [{"role": "user", "parts": [{"text": "Hi"}]}], model="claude")
            logout.assert_not_called()
            clear.assert_not_called()

    def test_status_uses_subscription_without_api_key(self):
        with (patch.object(config, "ANTHROPIC_AUTH_MODE", "oauth"), patch.object(config, "ANTHROPIC_API_KEY", ""),
              patch.object(auth, "is_signed_in", return_value=True)):
            self.assertTrue(config.provider_key_configured("anthropic"))


class ClaudeAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    @contextmanager
    def dashboard(self, values=None, snapshot=None):
        with (TemporaryDirectory() as folder, patch("oauth_codex.is_signed_in", return_value=False),
              patch("oauth_codex.account_email", return_value=None), patch("oauth_xai.is_signed_in", return_value=False),
              patch.object(auth, "is_signed_in", return_value=False)):
            path = Path(folder) / ".env"
            if values:
                write_env(path, values)
            restart = Mock()
            window = DashboardWindow(path, lambda: snapshot or {}, restart)
            try:
                yield window, path, restart
            finally:
                window.close()
                window.deleteLater()
                self.qt.processEvents()

    def test_native_browser_fallback_manual_input_validation_and_cancel(self):
        with self.dashboard() as (window, path, restart):
            card = window._provider_account_cards["anthropic"]
            self.assertEqual(card.authorization_input.echoMode(), QLineEdit.EchoMode.Password)
            card.login_method_input.setCurrentIndex(card.login_method_input.findData("copy_code"))
            with patch("ui_provider_accounts.threading.Thread") as thread:
                card.sign_in_button.click()
                card.sign_in_button.click()
            self.assertEqual(thread.call_count, 1)
            worker = card._login_worker
            self.assertEqual(worker.method, "copy_code")
            handle = auth.start_login(worker.cancel, method="copy_code")
            self.addCleanup(handle.close)
            worker.handle = handle
            with patch("ui_provider_accounts.webbrowser.open", return_value=False) as browser:
                card._on_auth_url(handle.auth_url)
            browser.assert_called_once_with(handle.auth_url, new=2)
            self.assertIn("Could not open", card.oauth_status_label.text())
            card.authorization_input.setText("code#wrong-state")
            card.submit_code_button.click()
            self.assertIn("state mismatch", card.oauth_status_label.text())
            self.assertFalse(handle._claimed)
            card.authorization_input.setText(f"code#{handle.state}")
            card.submit_code_button.click()
            self.assertTrue(handle._claimed)
            self.assertEqual(card.authorization_input.text(), "")
            self.assertFalse(card.submit_code_button.isEnabled())
            card.cancel_login_button.click()
            self.assertTrue(worker.cancel.is_set())
            card._on_subscription_login_finished(False, "Claude sign-in cancelled.")
            self.assertTrue(card.manual_frame.isHidden())
            self.assertTrue(card.sign_in_button.isEnabled())
            self.assertFalse(path.exists())
            restart.assert_not_called()

    def test_native_worker_browser_event_and_success_arrive_on_qt(self):
        with self.dashboard() as (window, path, restart):
            card = window._provider_account_cards["anthropic"]
            handle = Mock(auth_url="https://claude.ai/oauth/authorize?fixture=1")
            with (patch.object(auth, "start_login", return_value=handle) as start,
                  patch.object(auth, "is_signed_in", return_value=True),
                  patch("ui_provider_accounts.webbrowser.open", return_value=True) as browser):
                card.sign_in_button.click()
                deadline = time.monotonic() + 2
                while card._login_worker is not None and time.monotonic() < deadline:
                    self.qt.processEvents()
                    time.sleep(0.01)
                self.qt.processEvents()
                self.assertIsNone(card._login_worker)
                self.assertTrue(card.use_subscription_button.isEnabled())
            self.assertEqual(start.call_args.kwargs["method"], "browser")
            handle.wait.assert_called_once()
            handle.close.assert_called_once()
            browser.assert_called_once_with(handle.auth_url, new=2)
            self.assertEqual(card.authorization_input.text(), "")
            self.assertFalse(path.exists())
            restart.assert_not_called()

    def test_subscription_selection_preserves_api_keys_other_provider_and_drafts(self):
        values = {"HELPER_PROVIDER": "xai", "HELPER_XAI_AUTH_MODE": "oauth", "HELPER_ANTHROPIC_API_KEY": "api-fixture",
                  "OPENAI_API_KEY": "voice-fixture", "UNRELATED": "keep"}
        with self.dashboard(values) as (window, path, restart):
            card = window._provider_account_cards["anthropic"]
            window._field_widgets["HELPER_HOTKEY"].setText("ctrl+alt+p")
            card.model_input.setCurrentText("claude-custom")
            with patch.object(auth, "is_signed_in", return_value=True):
                card._on_subscription_login_finished(True, "")
                card.use_subscription_button.click()
                self.assertIsNone(window._validate_settings({}))
            saved = read_env(path)
            self.assertEqual(saved["HELPER_PROVIDER"], "anthropic")
            self.assertEqual(saved["HELPER_ANTHROPIC_AUTH_MODE"], "oauth")
            self.assertEqual(saved["HELPER_AGENT_MODEL"], "claude-custom")
            self.assertEqual(saved["HELPER_REASONING_MODEL"], "claude-custom")
            for key in ("HELPER_XAI_AUTH_MODE", "HELPER_ANTHROPIC_API_KEY", "OPENAI_API_KEY", "UNRELATED"):
                self.assertEqual(saved[key], values[key])
            self.assertNotIn("HELPER_HOTKEY", saved)
            self.assertEqual(window._field_widgets["HELPER_HOTKEY"].text(), "ctrl+alt+p")
            restart.assert_called_once()

    def test_missing_login_and_failed_save_never_switch_or_restart(self):
        with self.dashboard({"HELPER_PROVIDER": "codex"}) as (window, path, restart):
            self.assertFalse(window._use_provider_subscription("anthropic", "claude"))
            with (patch.object(auth, "is_signed_in", return_value=True),
                  patch("ui_dashboard.write_env", side_effect=OSError("disk full"))):
                self.assertFalse(window._use_provider_subscription("anthropic", "claude"))
            self.assertEqual(read_env(path)["HELPER_PROVIDER"], "codex")
            restart.assert_not_called()

    def test_api_key_save_selects_api_key_mode_without_erasing_subscription(self):
        with self.dashboard({"HELPER_ANTHROPIC_AUTH_MODE": "oauth"}) as (window, path, restart):
            card = window._provider_account_cards["anthropic"]
            card.key_input.setText("api-fixture")
            with patch.object(auth, "sign_out") as logout:
                card.save_button.click()
            self.assertEqual(read_env(path)["HELPER_ANTHROPIC_AUTH_MODE"], "api_key")
            self.assertFalse(window._provider_uses_subscription("anthropic"))
            logout.assert_not_called()
            restart.assert_not_called()

    def test_sign_out_and_dashboard_close_are_isolated_and_cancel_pending_logins(self):
        with self.dashboard({"HELPER_ANTHROPIC_API_KEY": "api-fixture"}) as (window, path, _):
            card = window._provider_account_cards["anthropic"]
            with (patch.object(auth, "sign_out") as logout, patch("oauth_codex.sign_out") as codex,
                  patch("oauth_xai.sign_out") as grok):
                card._sign_out_subscription()
            logout.assert_called_once()
            codex.assert_not_called()
            grok.assert_not_called()
            self.assertEqual(read_env(path)["HELPER_ANTHROPIC_API_KEY"], "api-fixture")
            with patch("ui_provider_accounts.threading.Thread"):
                for item in window._provider_account_cards.values():
                    item.sign_in_button.click()
            workers = [item._login_worker for item in window._provider_account_cards.values()]
            window.close()
            self.assertTrue(all(worker.cancel.is_set() for worker in workers))
            for item in window._provider_account_cards.values():
                item._on_subscription_login_finished(False, "Cancelled")

    def test_sidebar_distinguishes_claude_subscription_from_api_billing(self):
        with self.dashboard(snapshot={"provider": "anthropic", "provider_connected": True,
                                      "provider_auth_mode": "oauth"}) as (window, _, _):
            self.assertIn("Claude", window._sidebar_foot_pill.text())
            self.assertIn("Subscription", window._sidebar_foot_pill.text())
            self.assertNotIn("API key", window._sidebar_foot_pill.text())


if __name__ == "__main__":
    unittest.main()
