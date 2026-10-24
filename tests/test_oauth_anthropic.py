"""Claude OAuth protocol tests: isolated credentials, fake provider HTTP, real loopback callback."""
from __future__ import annotations

import base64
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
import hashlib
import json
import threading
import unittest
from unittest.mock import Mock, patch
from urllib.parse import parse_qs, urlencode, urlsplit

import keyring
import requests

import oauth_anthropic as auth


def token_body(**updates):
    return {"access_token": "sk-ant-oat-fixture-access", "refresh_token": "fixture-refresh", "expires_in": 3600, **updates}


class AnthropicOAuthTests(unittest.TestCase):
    def setUp(self):
        self.store = {}
        patches = [
            patch.object(auth.keyring, "get_password", side_effect=lambda service, user: self.store.get((service, user))),
            patch.object(auth.keyring, "set_password", side_effect=lambda service, user, raw: self.store.__setitem__((service, user), raw)),
            patch.object(auth.keyring, "delete_password", side_effect=lambda service, user: self.store.pop((service, user), None)),
            patch.object(auth.time, "time", return_value=1000),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def handle(self, method="copy_code"):
        # Do not bind the user's preferred login port during tests.
        with patch.object(auth, "CALLBACK_PORT", 0):
            handle = auth.start_login(method=method)
        self.addCleanup(handle.close)
        return handle

    def saved_tokens(self, **updates):
        tokens = auth.Tokens("old-access", "old-refresh", 4600)
        self.store[(auth.SERVICE, auth.USERNAME)] = json.dumps({**asdict(tokens), **updates})

    def test_pkce_public_client_scopes_and_copy_redirect_match_pi(self):
        handle = self.handle()
        params = parse_qs(urlsplit(handle.auth_url).query)
        self.assertTrue(handle.auth_url.startswith("https://claude.ai/oauth/authorize?"))
        self.assertEqual(params["client_id"], ["9d1c250a-e61b-44d9-88ed-5944d1962f5e"])
        self.assertEqual(params["state"], [handle.verifier])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        challenge = base64.urlsafe_b64encode(hashlib.sha256(handle.verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(params["code_challenge"], [challenge])
        self.assertEqual(params["redirect_uri"], [auth.COPY_CODE_REDIRECT_URI])
        self.assertEqual(params["scope"], [auth.SCOPES])
        self.assertIsNone(handle.server)
        self.assertNotEqual(handle.verifier, self.handle().verifier)

    def test_copy_code_exchange_and_separate_credential_store(self):
        handle = self.handle()
        handle.submit_authorization(f"copied-code#{handle.state}")
        with patch.object(auth, "_post_json", return_value=(200, token_body())) as post:
            tokens = handle.wait()
        self.assertEqual(post.call_args.args[0], {
            "grant_type": "authorization_code", "client_id": auth.CLIENT_ID, "code": "copied-code",
            "state": handle.state, "redirect_uri": auth.COPY_CODE_REDIRECT_URI, "code_verifier": handle.verifier,
        })
        self.assertEqual(auth.load_tokens(), tokens)
        self.assertEqual(tokens.expires_at, 4600)
        self.assertEqual(set(self.store), {("Helper", "anthropic-oauth")})

    def test_manual_input_formats_and_mismatched_or_malformed_state(self):
        for value in ("bare-code", "code#STATE", "code=code&state=STATE", "URL?code=code&state=STATE"):
            with self.subTest(value=value):
                handle = self.handle()
                value = value.replace("STATE", handle.state).replace("URL", handle.redirect_uri)
                handle.submit_authorization(value)
                with patch.object(auth, "_post_json", return_value=(200, token_body())):
                    handle.wait()
        for value in ("code#wrong", "code#é", "", "bad code", "https://evil.test/?code=x",
                      "http://[bad", "code=a&code=b", "code=x&state=a&state=b"):
            with self.subTest(value=value), self.assertRaises(auth.LoginError):
                self.handle().submit_authorization(value)

    def test_browser_callback_checks_state_ignores_invalid_requests_and_exchanges_once(self):
        handle = self.handle("browser")
        uri = handle.redirect_uri.replace("localhost", "127.0.0.1")
        self.assertEqual(urlsplit(handle.redirect_uri).hostname, "localhost")
        self.assertEqual(handle.server.server_address[0], "127.0.0.1")
        self.assertEqual(requests.get(uri + "?code=x&state=wrong", timeout=2).status_code, 400)
        self.assertEqual(requests.get(uri.replace("/callback", "/other"), timeout=2).status_code, 404)
        self.assertEqual(requests.get(uri + "?" + urlencode({"state": handle.state}), timeout=2).status_code, 400)
        url = uri + "?" + urlencode({"code": "browser-code", "state": handle.state})
        page = requests.get(url, timeout=2)
        self.assertEqual(page.status_code, 200)
        self.assertEqual(page.headers["Cache-Control"], "no-store")
        self.assertNotIn("browser-code", page.text)
        self.assertIn("Return to Helper", page.text)
        self.assertEqual(requests.get(url, timeout=2).status_code, 400)
        with patch.object(auth, "_post_json", return_value=(200, token_body())) as post:
            handle.wait()
        self.assertEqual(post.call_count, 1)
        self.assertEqual(post.call_args.args[0]["redirect_uri"], handle.redirect_uri)
        self.assertEqual(post.call_args.args[0]["code"], "browser-code")
        self.assertIsNone(handle.server)

    def test_preferred_callback_port_falls_back_to_ephemeral_port(self):
        factory = auth.ThreadingHTTPServer
        calls = []
        def create(address, handler):
            calls.append(address)
            if len(calls) == 1:
                raise OSError("preferred port busy")
            return factory(address, handler)
        with patch.object(auth, "ThreadingHTTPServer", side_effect=create):
            handle = auth.start_login()
        self.addCleanup(handle.close)
        self.assertEqual(calls, [("127.0.0.1", 53692), ("127.0.0.1", 0)])
        self.assertGreater(urlsplit(handle.redirect_uri).port, 0)

    def test_no_callback_listener_still_allows_manual_redirect_input(self):
        with patch.object(auth, "ThreadingHTTPServer", side_effect=OSError("unavailable")):
            handle = auth.start_login()
        self.addCleanup(handle.close)
        self.assertIsNone(handle.server)
        handle.submit_authorization(handle.redirect_uri + "?" + urlencode({"code": "manual", "state": handle.state}))
        with patch.object(auth, "_post_json", return_value=(200, token_body())):
            handle.wait()

    def test_callback_denial_does_not_echo_untrusted_html_or_store_tokens(self):
        handle = self.handle("browser")
        uri = handle.redirect_uri.replace("localhost", "127.0.0.1")
        page = requests.get(uri + "?" + urlencode({"state": handle.state, "error": "access_denied",
                                                   "error_description": "<script>private</script>"}), timeout=2)
        self.assertEqual(page.status_code, 400)
        self.assertNotIn("<script>", page.text)
        self.assertNotIn("private", page.text)
        with patch.object(auth, "_post_json") as post, self.assertRaisesRegex(auth.LoginError, "denied"):
            handle.wait()
        post.assert_not_called()
        self.assertFalse(self.store)

    def test_cancel_timeout_and_closed_handle_cannot_save_or_accept_code(self):
        cancel = threading.Event()
        cancel.set()
        with self.assertRaisesRegex(auth.LoginError, "cancelled"):
            auth.start_login(cancel)
        handle = self.handle()
        handle.cancel.set()
        with self.assertRaisesRegex(auth.LoginError, "cancelled"):
            handle.wait()
        handle = self.handle("browser")
        with self.assertRaisesRegex(auth.LoginError, "timed out"):
            handle.wait(timeout=0)
        self.assertIsNone(handle.server)
        with self.assertRaises(auth.LoginError):
            handle.submit_authorization("code")
        self.assertFalse(self.store)

    def test_cancel_or_sign_out_during_exchange_cannot_resurrect_grant(self):
        for action in ("cancel", "sign_out"):
            with self.subTest(action=action):
                handle = self.handle()
                handle.submit_authorization("code")
                def respond(_fields):
                    handle.cancel.set() if action == "cancel" else auth.sign_out()
                    return 200, token_body()
                with patch.object(auth, "_post_json", side_effect=respond), self.assertRaisesRegex(auth.LoginError, "cancelled"):
                    handle.wait()
        self.assertFalse(self.store)

    def test_exchange_failure_does_not_include_response_secrets(self):
        handle = self.handle()
        handle.submit_authorization("private-code")
        with (patch.object(auth, "_post_json", return_value=(400, {"error_description": "private-code"})),
              self.assertRaises(auth.LoginError) as error):
            handle.wait()
        self.assertNotIn("private-code", str(error.exception))
        self.assertFalse(self.store)

    def test_fresh_token_needs_no_network_and_refresh_keeps_or_rotates_refresh_token(self):
        self.saved_tokens()
        with patch.object(auth, "_post_json") as post:
            self.assertEqual(auth.get_access_token(), "old-access")
        post.assert_not_called()
        for rotate in (True, False):
            body = token_body()
            if not rotate:
                del body["refresh_token"]
            previous = auth.load_tokens().refresh_token
            with patch.object(auth, "_post_json", return_value=(200, body)) as post:
                auth.get_access_token(force_refresh=True)
            self.assertEqual(post.call_args.args[0], {"grant_type": "refresh_token", "client_id": auth.CLIENT_ID,
                                                     "refresh_token": previous})
            self.assertEqual(auth.load_tokens().refresh_token, "fixture-refresh" if rotate else previous)

    def test_near_expiry_and_concurrent_callers_refresh_only_once(self):
        self.saved_tokens(expires_at=1200)
        with patch.object(auth, "_post_json", return_value=(200, token_body())) as post:
            with ThreadPoolExecutor(max_workers=4) as pool:
                results = list(pool.map(lambda _: auth.get_access_token(), range(4)))
        self.assertEqual(results, ["sk-ant-oat-fixture-access"] * 4)
        self.assertEqual(post.call_count, 1)

    def test_transient_refresh_failure_preserves_grant_revocation_clears_only_claude(self):
        self.saved_tokens(expires_at=1)
        self.store[("Helper", "xai-oauth")] = "grok-unchanged"
        self.store[("Helper", "openai-codex")] = "codex-unchanged"
        with patch.object(auth, "_post_json", return_value=(503, {})), self.assertRaises(auth.LoginError):
            auth.get_access_token()
        self.assertTrue(auth.is_signed_in())
        with (patch.object(auth, "_post_json", return_value=(400, {"error": "invalid_grant"})),
              self.assertRaises(auth.NotSignedIn)):
            auth.get_access_token()
        self.assertFalse(auth.is_signed_in())
        self.assertEqual(self.store, {("Helper", "xai-oauth"): "grok-unchanged", ("Helper", "openai-codex"): "codex-unchanged"})

    def test_missing_corrupt_and_unavailable_storage_is_signed_out(self):
        for raw in (None, "bad-json", "[]", '{"access_token":"x"}', json.dumps({
                "access_token": "x", "refresh_token": "y", "expires_at": float("inf")})):
            with self.subTest(raw=raw), patch.object(auth.keyring, "get_password", return_value=raw):
                self.assertFalse(auth.is_signed_in())
                with self.assertRaises(auth.NotSignedIn):
                    auth.get_access_token()
        with patch.object(auth.keyring, "get_password", side_effect=RuntimeError("unavailable")):
            self.assertFalse(auth.is_signed_in())

    def test_invalid_tokens_and_storage_errors_fail_closed(self):
        for updates in ({"access_token": None}, {"refresh_token": "bad value"}, {"expires_in": None},
                        {"expires_in": -1}, {"expires_in": True}, {"expires_in": "3600"}, {"expires_in": float("nan")}):
            with self.subTest(updates=updates), self.assertRaises(auth.LoginError):
                auth._parse_tokens(token_body(**updates))
        handle = self.handle()
        handle.submit_authorization("code")
        with (patch.object(auth, "_post_json", return_value=(200, token_body())),
              patch.object(auth.keyring, "set_password", side_effect=RuntimeError("secret")),
              self.assertRaisesRegex(auth.LoginError, "credential manager")):
            handle.wait()
        with patch.object(auth.keyring, "delete_password", side_effect=keyring.errors.PasswordDeleteError()):
            auth.sign_out()

    def test_http_uses_json_timeout_no_redirects_and_sanitized_errors(self):
        response = Mock(status_code=200)
        response.json.return_value = token_body()
        with patch.object(auth.requests, "post", return_value=response) as post:
            auth._post_json({"code": "private"})
        self.assertEqual(post.call_args.args[0], "https://platform.claude.com/v1/oauth/token")
        self.assertEqual(post.call_args.kwargs["json"], {"code": "private"})
        self.assertEqual(post.call_args.kwargs["timeout"], (10, 30))
        self.assertFalse(post.call_args.kwargs["allow_redirects"])
        with (patch.object(auth.requests, "post", side_effect=requests.ConnectionError("private")),
              self.assertRaises(auth.LoginError) as error):
            auth._post_json({})
        self.assertNotIn("private", str(error.exception))
        response.json.side_effect = ValueError("private")
        with patch.object(auth.requests, "post", return_value=response), self.assertRaises(auth.LoginError):
            auth._post_json({})


if __name__ == "__main__":
    unittest.main()
