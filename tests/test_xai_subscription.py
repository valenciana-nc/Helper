"""Native subscription routing and real engine serialization, without real credentials or API calls."""
from __future__ import annotations

import io
import json
import unittest
from unittest.mock import Mock, call, patch

from PIL import Image
import requests

import config
import oauth_xai
from helper_engine.model import ProviderModel
from helper_engine.types import Observation
from openai_client import AuthExpired, BadProviderResponse, NotSignedIn, ProviderError, RateLimited, make_openai_client
from xai_client import RESPONSES_URL, XaiSubscriptionProvider


def reply(text="Ready", status=200, response_status="completed", **fields):
    response = requests.Response()
    response.status_code = status
    response._content = json.dumps({"status": response_status, "output": [
        {"type": "message", "content": [{"type": "output_text", "text": text}]}], **fields}).encode()
    return response


class XaiSubscriptionTests(unittest.TestCase):
    def test_explicit_mode_and_existing_key_precedence(self):
        for mode, key, expected in (("auto", "key", "api_key"), ("auto", "", "oauth"),
                                    ("oauth", "key", "oauth"), ("api_key", "", "api_key")):
            with (self.subTest(mode=mode, key=key), patch.object(config, "XAI_AUTH_MODE", mode),
                  patch.object(config, "XAI_API_KEY", key)):
                self.assertEqual(config.xai_auth_mode(), expected)

    def test_missing_subscription_never_falls_back_to_codex_or_a_saved_api_key(self):
        with (patch.object(config, "PROVIDER", "xai"), patch.object(config, "XAI_AUTH_MODE", "oauth"),
              patch.object(config, "XAI_API_KEY", "other-key"), patch.object(oauth_xai, "is_signed_in", return_value=False),
              patch("openai_client.CodexProvider") as codex, self.assertRaises(NotSignedIn)):
            make_openai_client()
        codex.assert_not_called()

    def test_engine_uses_subscription_responses_with_image_and_schema_not_codex_model(self):
        decision = '{"kind":"answer","observation_id":"fixture","message":"Ready"}'
        session = Mock()
        session.post.return_value = reply(decision)
        output = io.BytesIO()
        Image.new("RGB", (80, 60), "white").save(output, "PNG")
        observation = Observation("fixture", "desktop", "scope", "Test", output.getvalue(), 80, 60)
        with (patch.object(config, "PROVIDER", "xai"), patch.object(config, "XAI_AUTH_MODE", "oauth"),
              patch.object(config, "XAI_API_KEY", "unused-key"), patch.object(config, "API_MODEL", "unused-model"),
              patch.object(oauth_xai, "is_signed_in", return_value=True),
              patch.object(oauth_xai, "get_access_token", return_value="subscription-token"),
              patch("xai_client.requests.Session", return_value=session)):
            client = make_openai_client()
            result = ProviderModel(client, "grok-4.7").decide("Describe the screen", observation, {"mode": "active"})
        self.assertEqual(result.message, "Ready")
        self.assertEqual(session.post.call_count, 1)
        self.assertEqual(session.post.call_args.args[0], RESPONSES_URL)
        request = session.post.call_args.kwargs
        self.assertEqual(request["headers"]["Authorization"], "Bearer subscription-token")
        self.assertNotIn("chatgpt-account-id", request["headers"])
        self.assertFalse(request["allow_redirects"])
        body = json.loads(request["data"])
        self.assertEqual(body["model"], "grok-4.7")
        self.assertFalse(body["store"])
        self.assertNotIn("tools", body)
        self.assertNotIn("temperature", body)
        self.assertEqual(body["input"][0]["role"], "developer")
        self.assertIn("observation_id", body["input"][0]["content"][0]["text"])
        self.assertEqual([part["type"] for part in body["input"][1]["content"]], ["input_text", "input_image"])

    def test_401_refreshes_once_and_later_retries_do_not_keep_refreshing(self):
        session = Mock()
        session.post.side_effect = [reply(status=401), reply(status=503), reply()]
        provider = XaiSubscriptionProvider(session)
        with (patch.object(oauth_xai, "get_access_token", side_effect=["old", "new", "new"]) as token,
              patch("xai_client.time.sleep")):
            self.assertEqual(provider.chat([{"role": "user", "parts": [{"text": "Hi"}]}], model="grok").text, "Ready")
        self.assertEqual(token.call_args_list, [call(force_refresh=False), call(force_refresh=True), call(force_refresh=False)])
        self.assertEqual(session.post.call_args_list[1].kwargs["headers"]["Authorization"], "Bearer new")

    def test_auth_limits_forbidden_and_malformed_responses_do_not_clear_other_credentials(self):
        for response, expected in ((reply(status=401), AuthExpired), (reply(status=403), ProviderError),
                                   (reply(status=429), RateLimited), (reply(status=400), ProviderError),
                                   (reply(response_status="incomplete"), BadProviderResponse)):
            session = Mock()
            session.post.return_value = response
            with (self.subTest(expected=expected), patch.object(oauth_xai, "get_access_token", return_value="token"),
                  patch("token_store.clear") as clear, patch("xai_client.time.sleep"), self.assertRaises(expected)):
                XaiSubscriptionProvider(session).chat([{"role": "user", "parts": [{"text": "Hi"}]}], model="grok")
            clear.assert_not_called()

    def test_blank_output_and_invalid_json_are_not_treated_as_answers(self):
        responses = [reply(""), reply(output=[]), reply()]
        responses[-1]._content = b"not JSON"
        for response in responses:
            session = Mock()
            session.post.return_value = response
            with (patch.object(oauth_xai, "get_access_token", return_value="token"), self.assertRaises(BadProviderResponse)):
                XaiSubscriptionProvider(session).chat([{"role": "user", "parts": [{"text": "Hi"}]}], model="grok")

    def test_status_uses_oauth_credential_without_needing_api_key(self):
        with (patch.object(config, "XAI_AUTH_MODE", "oauth"), patch.object(config, "XAI_API_KEY", ""),
              patch.object(oauth_xai, "is_signed_in", return_value=True)):
            self.assertTrue(config.provider_key_configured("xai"))


if __name__ == "__main__":
    unittest.main()
