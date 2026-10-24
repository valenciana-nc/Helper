"""Device-grant/refresh tests with fake time, HTTP and an isolated credential store."""
from __future__ import annotations

from dataclasses import asdict
import json
import threading
import unittest
from unittest.mock import Mock, patch

import keyring
import requests

import oauth_xai as auth


def device_body(**updates):
    return {"device_code": "device-secret", "user_code": "ABCD-1234",
            "verification_uri": "https://accounts.x.ai/oauth2/device", "expires_in": 900, "interval": 5,
            **updates}


def token_body(**updates):
    return {"access_token": "fixture-access", "refresh_token": "fixture-refresh", "expires_in": 21600, **updates}


class FakeEvent:
    def __init__(self):
        self.now = 0.0
        self.waits = []
        self.cancelled = False

    def is_set(self):
        return self.cancelled

    def set(self):
        self.cancelled = True

    def wait(self, delay):
        self.waits.append(delay)
        self.now += delay
        return self.cancelled


class XaiOAuthTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        def delete(service, user):
            self.store.pop((service, user), None)
        patches = [
            patch.object(auth.keyring, "get_password", side_effect=lambda service, user: self.store.get((service, user))),
            patch.object(auth.keyring, "set_password", side_effect=lambda service, user, raw: self.store.__setitem__((service, user), raw)),
            patch.object(auth.keyring, "delete_password", side_effect=delete),
            patch.object(auth.time, "time", return_value=1000),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def saved_tokens(self, **updates):
        tokens = auth.Tokens("old-access", "old-refresh", 1000 + 3600)
        self.store[(auth.SERVICE, auth.USERNAME)] = json.dumps({**asdict(tokens), **updates})

    def login(self, replies, *, device_callback=None):
        clock = FakeEvent()
        notify = device_callback or Mock()
        with (patch.object(auth, "_post_form", side_effect=replies) as post,
              patch.object(auth.time, "monotonic", side_effect=lambda: clock.now)):
            result = auth.login(notify, clock)
        return result, clock, notify, post

    def test_device_flow_waits_handles_pending_and_slow_down_and_stores_only_in_keyring(self):
        tokens, clock, notify, post = self.login([
            (200, device_body()), (400, {"error": "authorization_pending"}),
            (400, {"error": "slow_down", "interval": 10}), (200, token_body()),
        ])
        self.assertEqual(clock.waits, [5, 5, 10])
        self.assertEqual(tokens.expires_at, 22600)
        self.assertEqual(auth.load_tokens(), tokens)
        self.assertEqual(set(self.store), {("Helper", "xai-oauth")})
        self.assertEqual(post.call_args_list[0].args, (auth.DEVICE_CODE_URL, {
            "client_id": auth.CLIENT_ID, "scope": auth.SCOPES, "referrer": "helper",
        }))
        self.assertEqual(post.call_args_list[1].args, (auth.TOKEN_URL, {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code", "client_id": auth.CLIENT_ID,
            "device_code": "device-secret",
        }))
        self.assertEqual(notify.call_args.args[0].user_code, "ABCD-1234")

    def test_interval_default_complete_uri_and_minimum_slow_down(self):
        for interval in (None, 0, -1, "bad", float("nan")):
            with self.subTest(interval=interval):
                _, clock, notify, _ = self.login([
                    (200, device_body(interval=interval,
                                      verification_uri_complete="https://accounts.x.ai/oauth2/device?user_code=ABCD-1234")),
                    (400, {"error": "slow_down", "interval": 1}), (200, token_body()),
                ])
                self.assertEqual(clock.waits, [5, 10])
                self.assertIn("user_code=", notify.call_args.args[0].verification_uri)

    def test_untrusted_urls_are_rejected_before_browser_callback(self):
        for uri in ("http://accounts.x.ai/device", "file:///tmp/token", "https://evil.test/device",
                    "https://accounts.x.ai.evil.test", "https://user@accounts.x.ai/device", "not a url"):
            for field in ("verification_uri", "verification_uri_complete"):
                with self.subTest(uri=uri, field=field):
                    notify = Mock()
                    with self.assertRaises(auth.LoginError):
                        self.login([(200, device_body(**{field: uri}))], device_callback=notify)
                    notify.assert_not_called()
        self.assertFalse(self.store)

    def test_denial_and_expiry_do_not_store_tokens(self):
        for error in ("access_denied", "authorization_denied", "expired_token"):
            with self.subTest(error=error), self.assertRaises(auth.LoginError):
                self.login([(200, device_body()), (400, {"error": error})])
        with self.assertRaisesRegex(auth.LoginError, "expired"):
            self.login([(200, device_body(expires_in=1))])
        self.assertFalse(self.store)

    def test_cancellation_before_request_and_during_wait(self):
        event = threading.Event()
        event.set()
        with patch.object(auth, "_post_form") as post, self.assertRaisesRegex(auth.LoginError, "cancelled"):
            auth.login(Mock(), event)
        post.assert_not_called()
        event = FakeEvent()
        with (patch.object(auth, "_post_form", return_value=(200, device_body())) as post,
              patch.object(auth.time, "monotonic", side_effect=lambda: event.now),
              self.assertRaisesRegex(auth.LoginError, "cancelled")):
            auth.login(lambda _: event.set(), event)
        self.assertEqual(post.call_count, 1)
        self.assertFalse(self.store)

    def test_sign_out_during_poll_cannot_resurrect_credentials(self):
        replies = [(200, device_body()), (200, token_body())]
        def respond(*_args):
            reply = replies.pop(0)
            if not replies:
                auth.sign_out()
            return reply
        clock = FakeEvent()
        with (patch.object(auth, "_post_form", side_effect=respond),
              patch.object(auth.time, "monotonic", side_effect=lambda: clock.now),
              self.assertRaisesRegex(auth.LoginError, "cancelled")):
            auth.login(Mock(), clock)
        self.assertFalse(self.store)

    def test_fresh_tokens_are_used_without_network(self):
        self.saved_tokens()
        with patch.object(auth, "_post_form") as post:
            self.assertEqual(auth.get_access_token(), "old-access")
        post.assert_not_called()

    def test_refresh_rotates_or_preserves_refresh_token_and_uses_expiry_skew(self):
        self.saved_tokens(expires_at=1200)
        for refresh in ("rotated-refresh", None):
            body = token_body()
            if refresh:
                body["refresh_token"] = refresh
            else:
                del body["refresh_token"]
            previous = auth.load_tokens().refresh_token
            with patch.object(auth, "_post_form", return_value=(200, body)) as post:
                self.assertEqual(auth.get_access_token(force_refresh=True), "fixture-access")
            self.assertEqual(auth.load_tokens().refresh_token, refresh or previous)
            self.assertEqual(post.call_args.args[1]["refresh_token"], previous)
            self.assertEqual(post.call_args.args[1]["grant_type"], "refresh_token")

    def test_revoked_refresh_clears_only_grok_and_transient_error_keeps_it(self):
        self.store[("Helper", "openai-codex")] = "untouched"
        self.saved_tokens(expires_at=1)
        with (patch.object(auth, "_post_form", return_value=(503, {})), self.assertRaises(auth.LoginError)):
            auth.get_access_token()
        self.assertTrue(auth.is_signed_in())
        with (patch.object(auth, "_post_form", return_value=(400, {"error": "invalid_grant"})),
              self.assertRaises(auth.NotSignedIn)):
            auth.get_access_token()
        self.assertFalse(auth.is_signed_in())
        self.assertEqual(self.store, {("Helper", "openai-codex"): "untouched"})

    def test_missing_corrupt_or_unavailable_keyring_is_signed_out(self):
        for raw in (None, "not-json", "[]", '{"access_token": 10}',
                    json.dumps({"access_token": "x", "refresh_token": "y", "expires_at": float("nan")})):
            with self.subTest(raw=raw), patch.object(auth.keyring, "get_password", return_value=raw):
                self.assertFalse(auth.is_signed_in())
                with self.assertRaises(auth.NotSignedIn):
                    auth.get_access_token()
        with patch.object(auth.keyring, "get_password", side_effect=RuntimeError("unavailable")):
            self.assertFalse(auth.is_signed_in())

    def test_malformed_tokens_are_rejected_and_missing_expiry_defaults_to_one_hour(self):
        for overrides in ({"access_token": ""}, {"refresh_token": None}, {"expires_in": 0},
                          {"expires_in": float("inf")}, {"expires_in": True}, {"expires_in": "3600"}):
            with self.subTest(overrides=overrides), self.assertRaises(auth.LoginError):
                auth._parse_tokens(token_body(**overrides))
        body = token_body()
        del body["expires_in"]
        self.assertEqual(auth._parse_tokens(body).expires_at, 4600)

    def test_storage_failure_fails_login_and_missing_delete_is_safe(self):
        with (patch.object(auth.keyring, "set_password", side_effect=RuntimeError("secret-error")),
              self.assertRaisesRegex(auth.LoginError, "credential manager")):
            self.login([(200, device_body()), (200, token_body())])
        with patch.object(auth.keyring, "delete_password", side_effect=keyring.errors.PasswordDeleteError()):
            auth.sign_out()

    def test_http_uses_form_timeout_tls_and_no_redirects_without_leaking_bodies(self):
        response = Mock(status_code=200)
        response.json.return_value = {"ok": True}
        with patch.object(auth.requests, "post", return_value=response) as post:
            self.assertEqual(auth._post_form(auth.TOKEN_URL, {"refresh_token": "private"}), (200, {"ok": True}))
        self.assertEqual(post.call_args.kwargs["data"], {"refresh_token": "private"})
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 30))
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        with (patch.object(auth.requests, "post", side_effect=requests.ConnectionError("private")),
              self.assertRaises(auth.LoginError) as error):
            auth._post_form(auth.TOKEN_URL, {})
        self.assertNotIn("private", str(error.exception))
        response.json.side_effect = ValueError("private")
        with patch.object(auth.requests, "post", return_value=response), self.assertRaises(auth.LoginError):
            auth._post_form(auth.TOKEN_URL, {})


if __name__ == "__main__":
    unittest.main()
