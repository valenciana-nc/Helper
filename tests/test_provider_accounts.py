"""Account cards and provider routing without browser launches, secrets, or model inference."""
from __future__ import annotations

from contextlib import contextmanager
import io
import json
from pathlib import Path
import time
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from PIL import Image
from PyQt6.QtWidgets import QApplication, QLineEdit
import requests

import config
from env_io import read_env, write_env
from helper_engine.model import ProviderModel
from helper_engine.types import Observation
from openai_client import NotSignedIn, make_openai_client
from ui_dashboard import DashboardWindow


class ProviderAccountTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.qt = QApplication.instance() or QApplication([])

    @contextmanager
    def dashboard(self, values=None, snapshot=None):
        with (TemporaryDirectory() as folder,
              patch("oauth_codex.is_signed_in", return_value=False),
              patch("oauth_codex.account_email", return_value=None),
              patch("oauth_xai.is_signed_in", return_value=False),
              patch("oauth_anthropic.is_signed_in", return_value=False)):
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

    def test_cards_are_directly_under_chatgpt_and_status_page_is_removed(self):
        with self.dashboard() as (window, _, _):
            page = window._stack.widget(window._page_index[window.NAV_ACCOUNT]).widget()
            cards = window._provider_account_cards
            self.assertEqual(page.layout().indexOf(cards["anthropic"]), 2)
            self.assertEqual(page.layout().indexOf(cards["xai"]), 3)
            self.assertEqual(window._stack.count(), 3)
            self.assertEqual(set(window._nav_buttons), {"conversations", "settings", "account"})
            self.assertFalse(hasattr(window, "_log_view"))
            for card in cards.values():
                self.assertEqual(card.key_input.echoMode(), QLineEdit.EchoMode.Password)
                self.assertEqual(card.sign_in_button.text(), f"Sign in with {card.account.name}")

    def test_sign_in_opens_only_official_console_and_does_not_claim_connected(self):
        with self.dashboard() as (window, path, _):
            for card in window._provider_account_cards.values():
                card.key_input.setText("fixture-private-key")
                with patch("ui_provider_accounts.webbrowser.open", return_value=True) as browser:
                    button = getattr(card, "api_console_button", card.sign_in_button)
                    button.click()
                browser.assert_called_once_with(card.account.console_url, new=2)
                self.assertNotIn("fixture-private-key", browser.call_args.args[0])
                self.assertIn("paste an API key", card.status_label.text())
                self.assertFalse(path.exists())

    def test_browser_failure_does_not_save_or_restart(self):
        with self.dashboard() as (window, path, restart):
            card = window._provider_account_cards["xai"]
            with patch("ui_provider_accounts.webbrowser.open", side_effect=OSError("browser unavailable")):
                card.api_console_button.click()
            self.assertIn("Could not open", card.status_label.text())
            self.assertFalse(path.exists())
            restart.assert_not_called()

    def test_save_and_use_preserves_other_keys_and_unsaved_settings(self):
        values = {"HELPER_PROVIDER": "codex", "OPENAI_API_KEY": "fixture-voice", "UNRELATED": "keep"}
        with self.dashboard(values) as (window, path, restart):
            window._field_widgets["HELPER_HOTKEY"].setText("ctrl+alt+p")
            card = window._provider_account_cards["xai"]
            card.key_input.setText("fixture-xai")
            card.model_input.setCurrentText("grok-custom")
            card.save_button.click()
            saved = read_env(path)
            self.assertEqual(saved["HELPER_PROVIDER"], "xai")
            self.assertEqual(saved["HELPER_XAI_API_KEY"], "fixture-xai")
            self.assertEqual(saved["HELPER_AGENT_MODEL"], "grok-custom")
            self.assertEqual(saved["HELPER_REASONING_MODEL"], "grok-custom")
            self.assertEqual(saved["OPENAI_API_KEY"], "fixture-voice")
            self.assertEqual(saved["UNRELATED"], "keep")
            self.assertNotIn("HELPER_HOTKEY", saved)
            self.assertEqual(window._field_widgets["HELPER_HOTKEY"].text(), "ctrl+alt+p")
            self.assertEqual(window._current_provider_key(), "xai")
            self.assertIn("not validated", card.status_label.text().lower())
            restart.assert_not_called()

    def test_claude_save_restarts_only_after_success(self):
        with self.dashboard() as (window, path, restart):
            card = window._provider_account_cards["anthropic"]
            card.key_input.setText("fixture-anthropic")
            card.restart_button.click()
            self.assertEqual(read_env(path)["HELPER_PROVIDER"], "anthropic")
            restart.assert_called_once()

    def test_invalid_or_failed_save_never_switches_provider_or_restarts(self):
        with self.dashboard({"HELPER_PROVIDER": "codex"}) as (window, path, restart):
            card = window._provider_account_cards["xai"]
            for key in ("", "bad key", "bad\nkey"):
                card.key_input.setText(key)
                card.restart_button.click()
                self.assertEqual(read_env(path)["HELPER_PROVIDER"], "codex")
            card.key_input.setText("fixture-xai")
            with patch("ui_dashboard.write_env", side_effect=OSError("disk full")):
                card.restart_button.click()
            self.assertEqual(read_env(path)["HELPER_PROVIDER"], "codex")
            self.assertEqual(window._current_provider_key(), "codex")
            restart.assert_not_called()

    def test_remove_key_masks_and_clears_only_selected_credentials(self):
        with self.dashboard({"HELPER_XAI_API_KEY": "fixture-xai", "HELPER_ANTHROPIC_API_KEY": "fixture-claude"}) as (
            window, path, restart
        ):
            card = window._provider_account_cards["xai"]
            card.show_key_button.click()
            card.remove_button.click()
            self.assertEqual(read_env(path)["HELPER_XAI_API_KEY"], "")
            self.assertEqual(read_env(path)["HELPER_ANTHROPIC_API_KEY"], "fixture-claude")
            self.assertEqual(card.key_input.text(), "")
            self.assertEqual(card.key_input.echoMode(), QLineEdit.EchoMode.Password)
            self.assertFalse(card.remove_button.isEnabled())
            restart.assert_not_called()

    def test_external_key_save_refreshes_account_without_overwriting_a_draft(self):
        with self.dashboard() as (window, path, _):
            card = window._provider_account_cards["xai"]
            write_env(path, {"HELPER_XAI_API_KEY": "fixture-first"})
            window._refresh_account_card()
            self.assertEqual(card.key_input.text(), "fixture-first")
            card.key_input.setText("fixture-unsaved-draft")
            write_env(path, {"HELPER_XAI_API_KEY": "fixture-second"})
            window._refresh_account_card()
            self.assertEqual(card.key_input.text(), "fixture-unsaved-draft")
            self.assertTrue(card.remove_button.isEnabled())

    def test_grok_is_selectable_in_settings_and_requires_its_own_key(self):
        with self.dashboard() as (window, _, _):
            combo = window._field_widgets["HELPER_PROVIDER"]
            combo.setCurrentIndex(combo.findData("xai"))
            self.assertEqual(window._current_provider_key(), "xai")
            self.assertIn("xAI API key", window._validate_settings({}))
            self.assertEqual(window._field_widgets["HELPER_XAI_API_KEY"].echoMode(), QLineEdit.EchoMode.Password)

    def test_failed_remove_preserves_saved_credentials(self):
        with self.dashboard({"HELPER_XAI_API_KEY": "fixture-xai"}) as (window, path, _):
            card = window._provider_account_cards["xai"]
            with patch("ui_dashboard.write_env", side_effect=OSError("disk full")):
                card.remove_button.click()
            self.assertEqual(read_env(path)["HELPER_XAI_API_KEY"], "fixture-xai")
            self.assertEqual(card.key_input.text(), "fixture-xai")

    def test_grok_native_login_shows_code_and_browser_fallback_without_saving_settings(self):
        with self.dashboard() as (window, path, restart):
            card = window._provider_account_cards["xai"]
            with patch("ui_provider_accounts.threading.Thread") as thread:
                card.sign_in_button.click()
                card.sign_in_button.click()  # Duplicate admission is blocked.
            self.assertEqual(thread.call_count, 1)
            self.assertFalse(card.sign_in_button.isEnabled())
            worker = card._login_worker
            with patch("ui_provider_accounts.webbrowser.open", return_value=False) as browser:
                card._on_device_ready("ABCD-1234", "https://accounts.x.ai/oauth2/device")
            browser.assert_called_once_with("https://accounts.x.ai/oauth2/device", new=2)
            self.assertIn("ABCD-1234", card.device_code_label.text())
            self.assertIn("Could not open", card.oauth_status_label.text())
            self.assertFalse(path.exists())
            card.cancel_login_button.click()
            self.assertTrue(worker.cancel.is_set())
            card._on_subscription_login_finished(False, "Grok sign-in cancelled.")
            self.assertTrue(card.sign_in_button.isEnabled())
            self.assertTrue(card.device_code_label.isHidden())
            self.assertIn("cancelled", card.oauth_status_label.text())
            restart.assert_not_called()

    def test_native_login_worker_delivers_browser_and_completion_events_to_qt(self):
        from oauth_xai import DeviceCode
        def login(on_device, cancel):
            on_device(DeviceCode("private", "ABCD-1234", "https://accounts.x.ai/oauth2/device", 900, 5))
        with self.dashboard() as (window, path, restart):
            card = window._provider_account_cards["xai"]
            with (patch("oauth_xai.login", side_effect=login) as auth,
                  patch("oauth_xai.is_signed_in", return_value=True),
                  patch("ui_provider_accounts.webbrowser.open", return_value=True) as browser):
                card.sign_in_button.click()
                deadline = time.monotonic() + 2
                while card._login_worker is not None and time.monotonic() < deadline:
                    self.qt.processEvents()
                    time.sleep(0.01)
                self.qt.processEvents()
                self.assertIsNone(card._login_worker)
                self.assertTrue(card.use_subscription_button.isEnabled())
            auth.assert_called_once()
            browser.assert_called_once_with("https://accounts.x.ai/oauth2/device", new=2)
            self.assertFalse(path.exists())
            restart.assert_not_called()

    def test_grok_subscription_selection_preserves_api_key_other_settings_and_drafts(self):
        values = {"HELPER_XAI_API_KEY": "fixture-api", "UNRELATED": "keep", "HELPER_PROVIDER": "codex"}
        with self.dashboard(values) as (window, path, restart):
            card = window._provider_account_cards["xai"]
            window._field_widgets["HELPER_HOTKEY"].setText("ctrl+alt+p")
            card.model_input.setCurrentText("grok-subscription-model")
            with patch("oauth_xai.is_signed_in", return_value=True):
                card._on_subscription_login_finished(True, "")
                card.use_subscription_button.click()
                self.assertIsNone(window._validate_settings({}))
            saved = read_env(path)
            self.assertEqual(saved["HELPER_XAI_AUTH_MODE"], "oauth")
            self.assertEqual(saved["HELPER_PROVIDER"], "xai")
            self.assertEqual(saved["HELPER_AGENT_MODEL"], "grok-subscription-model")
            self.assertEqual(saved["HELPER_REASONING_MODEL"], "grok-subscription-model")
            self.assertEqual(saved["HELPER_XAI_API_KEY"], "fixture-api")
            self.assertEqual(saved["UNRELATED"], "keep")
            self.assertNotIn("HELPER_HOTKEY", saved)
            self.assertEqual(window._field_widgets["HELPER_HOTKEY"].text(), "ctrl+alt+p")
            restart.assert_called_once()

    def test_subscription_save_failure_and_missing_login_do_not_restart(self):
        with self.dashboard({"HELPER_PROVIDER": "codex"}) as (window, path, restart):
            self.assertFalse(window._use_grok_subscription("grok-4.7"))
            with (patch("oauth_xai.is_signed_in", return_value=True),
                  patch("ui_dashboard.write_env", side_effect=OSError("disk full"))):
                self.assertFalse(window._use_grok_subscription("grok-4.7"))
            self.assertEqual(read_env(path)["HELPER_PROVIDER"], "codex")
            restart.assert_not_called()

    def test_api_key_save_explicitly_selects_api_billing_not_existing_subscription(self):
        with self.dashboard({"HELPER_XAI_AUTH_MODE": "oauth"}) as (window, path, _):
            card = window._provider_account_cards["xai"]
            card.key_input.setText("fixture-api")
            card.save_button.click()
            self.assertEqual(read_env(path)["HELPER_XAI_AUTH_MODE"], "api_key")
            self.assertFalse(window._grok_uses_subscription())

    def test_grok_sign_out_is_separate_from_chatgpt_and_api_key(self):
        with self.dashboard({"HELPER_XAI_API_KEY": "fixture-api"}) as (window, path, _):
            card = window._provider_account_cards["xai"]
            with patch("oauth_xai.sign_out") as sign_out, patch("oauth_codex.sign_out") as codex_out:
                card._sign_out_subscription()
            sign_out.assert_called_once()
            codex_out.assert_not_called()
            self.assertEqual(read_env(path)["HELPER_XAI_API_KEY"], "fixture-api")

    def test_closing_dashboard_cancels_pending_grok_login(self):
        with self.dashboard() as (window, _, _):
            card = window._provider_account_cards["xai"]
            with patch("ui_provider_accounts.threading.Thread"):
                card.sign_in_button.click()
            worker = card._login_worker
            window.close()
            self.assertTrue(worker.cancel.is_set())
            card._on_subscription_login_finished(False, "Cancelled")

    def test_sidebar_reports_subscription_instead_of_an_api_key(self):
        with self.dashboard(snapshot={"provider": "xai", "provider_connected": True,
                                      "provider_auth_mode": "oauth"}) as (window, _, _):
            self.assertIn("Subscription", window._sidebar_foot_pill.text())
            self.assertIn("configured", window._sidebar_foot_pill.text())
            self.assertNotIn("API key", window._sidebar_foot_pill.text())

    def test_sidebar_reports_grok_key_instead_of_missing_chatgpt_sign_in(self):
        with self.dashboard(snapshot={"provider": "xai", "provider_connected": True}) as (window, _, _):
            self.assertIn("Grok", window._sidebar_foot_pill.text())
            self.assertIn("configured", window._sidebar_foot_pill.text())
            self.assertNotIn("Not signed in", window._sidebar_foot_pill.text())


class GrokRoutingTests(unittest.TestCase):
    def test_missing_claude_or_grok_key_does_not_fall_back_to_codex(self):
        for provider in ("anthropic", "xai"):
            with (self.subTest(provider=provider), patch.object(config, "PROVIDER", provider),
                  patch.object(config, "ANTHROPIC_API_KEY", ""), patch.object(config, "XAI_API_KEY", ""),
                  patch.object(config, "XAI_AUTH_MODE", "auto"), patch("oauth_xai.is_signed_in", return_value=False),
                  patch.object(config, "ANTHROPIC_AUTH_MODE", "auto"),
                  patch("oauth_anthropic.is_signed_in", return_value=False)):
                with self.assertRaises(NotSignedIn):
                    make_openai_client()

    def test_grok_default_and_custom_model_resolution(self):
        self.assertEqual(config.resolve_provider_model("HELPER_AGENT_MODEL", {"HELPER_PROVIDER": "xai"}),
                         config.GROK_MODEL_DEFAULT)
        self.assertEqual(config.resolve_provider_model("HELPER_AGENT_MODEL", {
            "HELPER_PROVIDER": "xai", "HELPER_AGENT_MODEL": "grok-custom"
        }), "grok-custom")

    def test_engine_sends_image_and_schema_to_xai_without_computer_use_tools(self):
        calls = []
        decision = '{"kind":"answer","observation_id":"fixture","message":"Ready"}'
        class Session:
            def post(self, url, **kwargs):
                calls.append((url, kwargs))
                result = requests.Response()
                result.status_code = 200
                result._content = json.dumps({"choices": [{"message": {"content": decision}}]}).encode()
                return result
        with (patch.object(config, "PROVIDER", "xai"), patch.object(config, "XAI_API_KEY", "fixture-xai"),
              patch.object(config, "XAI_AUTH_MODE", "api_key"),
              patch.object(config, "API_MODEL", "unrelated-custom-model"),
              patch("openai_client.requests.Session", return_value=Session())):
            client = make_openai_client()
            output = io.BytesIO()
            Image.new("RGB", (80, 60), "white").save(output, "PNG")
            obs = Observation("fixture", "desktop", "scope", "Test", output.getvalue(), 80, 60)
            result = ProviderModel(client, "grok-custom").decide("Describe the screen", obs, {"mode": "active"})
        self.assertEqual(result.message, "Ready")
        self.assertEqual(len(calls), 1)
        url, request = calls[0]
        self.assertEqual(url, "https://api.x.ai/v1/chat/completions")
        self.assertEqual(request["headers"]["Authorization"], "Bearer fixture-xai")
        body = json.loads(request["data"])
        self.assertEqual(body["model"], "grok-custom")
        self.assertNotIn("tools", body)
        self.assertIn("observation_id", body["messages"][0]["content"])
        self.assertEqual([p["type"] for p in body["messages"][1]["content"]], ["text", "image_url"])


if __name__ == "__main__":
    unittest.main()
