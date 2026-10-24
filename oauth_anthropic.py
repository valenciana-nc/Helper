"""Native Claude Pro/Max OAuth with PKCE, loopback callback and copy-code login.

Protocol reference: earendil-works/pi, packages/ai/src/auth/oauth/anthropic.ts
(commit 136b87e). Uses no Pi/Claude Code process or external credential files.
"""
from __future__ import annotations

import base64
from dataclasses import asdict, dataclass
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import math
import queue
import secrets
import threading
import time
from urllib.parse import parse_qs, urlencode, urlsplit

import keyring
import requests

log = logging.getLogger("helper.oauth_anthropic")
CLIENT_ID = "9d1c250a-e61b-44d9-88ed-5944d1962f5e"
AUTHORIZE_URL = "https://claude.ai/oauth/authorize"
TOKEN_URL = "https://platform.claude.com/v1/oauth/token"
SCOPES = "org:create_api_key user:profile user:inference user:sessions:claude_code user:mcp_servers user:file_upload"
CALLBACK_HOST = "127.0.0.1"
CALLBACK_PORT = 53692
CALLBACK_PATH = "/callback"
COPY_CODE_REDIRECT_URI = "https://platform.claude.com/oauth/code/callback"
SERVICE = "Helper"
USERNAME = "anthropic-oauth"
REFRESH_LEEWAY_SEC = 300
_lock = threading.RLock()
_generation = 0


class LoginError(RuntimeError):
    pass


class NotSignedIn(LoginError):
    pass


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: float


def _required_string(body: dict, field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value or any(c.isspace() for c in value):
        raise LoginError(f"Invalid Claude OAuth response field: {field}")
    return value


def _positive(value: object) -> bool:
    try:
        return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0
    except OverflowError:
        return False


def _parse_tokens(body: dict, previous_refresh: str | None = None) -> Tokens:
    access = _required_string(body, "access_token")
    refresh = (previous_refresh if "refresh_token" not in body and previous_refresh
               else _required_string(body, "refresh_token"))
    lifetime = body.get("expires_in")
    if not _positive(lifetime):
        raise LoginError("Invalid Claude OAuth response field: expires_in")
    return Tokens(access, refresh, time.time() + lifetime)


def load_tokens() -> Tokens | None:
    try:
        raw = keyring.get_password(SERVICE, USERNAME)
        if not raw:
            return None
        body = json.loads(raw)
        if not isinstance(body, dict) or not _positive(body.get("expires_at")):
            return None
        return Tokens(_required_string(body, "access_token"), _required_string(body, "refresh_token"), body["expires_at"])
    except Exception:
        log.debug("Claude credentials unavailable or unreadable; treating as signed out.")
        return None


def _save_tokens(tokens: Tokens) -> None:
    try:
        keyring.set_password(SERVICE, USERNAME, json.dumps(asdict(tokens)))
    except Exception as exc:
        raise LoginError("Could not save Claude sign-in in the system credential manager.") from exc


def is_signed_in() -> bool:
    return load_tokens() is not None


def sign_out() -> None:
    global _generation
    with _lock:
        _generation += 1
        try:
            keyring.delete_password(SERVICE, USERNAME)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as exc:
            raise LoginError("Could not remove Claude sign-in from the system credential manager.") from exc


def _post_json(fields: dict[str, str]) -> tuple[int, dict]:
    try:
        response = requests.post(TOKEN_URL, json=fields, headers={"Accept": "application/json"},
                                 timeout=(10, 30), allow_redirects=False)
    except requests.RequestException as exc:
        raise LoginError("Could not reach Claude authentication. Please try again.") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise LoginError(f"Claude authentication returned invalid JSON (HTTP {response.status_code}).") from exc
    if not isinstance(body, dict):
        raise LoginError("Claude authentication returned a non-object response.")
    return response.status_code, body


def _parse_authorization_input(value: str, redirect_uri: str) -> tuple[str, str | None]:
    value = value.strip()
    if not value or len(value) > 8192:
        raise LoginError("Paste the authorization code or final redirect URL from Claude.")
    try:
        parsed = urlsplit(value)
    except ValueError as exc:
        raise LoginError("Invalid Claude authorization redirect URL.") from exc
    if parsed.scheme or parsed.netloc:
        expected = urlsplit(redirect_uri)
        if (parsed.scheme, parsed.netloc, parsed.path) != (expected.scheme, expected.netloc, expected.path):
            raise LoginError("Paste only the final Claude authorization redirect URL or code.")
        params = parse_qs(parsed.query, keep_blank_values=True)
        if "error" in params:
            raise LoginError("Claude authorization was denied. Please start sign-in again.")
        codes, states = params.get("code", []), params.get("state", [])
        if len(codes) != 1 or len(states) > 1:
            raise LoginError("Invalid Claude authorization redirect.")
        code, state = codes[0], states[0] if states else None
    elif "#" in value:
        code, state = value.split("#", 1)
    elif value.startswith("code="):
        params = parse_qs(value, keep_blank_values=True)
        codes, states = params.get("code", []), params.get("state", [])
        if len(codes) != 1 or len(states) > 1:
            raise LoginError("Invalid Claude authorization code.")
        code, state = codes[0], states[0] if states else None
    else:
        code, state = value, None
    if not code or any(c.isspace() for c in code):
        raise LoginError("Invalid Claude authorization code.")
    return code, state


class LoginHandle:
    def __init__(self, cancel: threading.Event, method: str) -> None:
        self.cancel = cancel
        self.verifier = secrets.token_urlsafe(32)
        # Pi/Claude Code use the PKCE verifier itself as the OAuth state.
        self.state = self.verifier
        self.redirect_uri = (COPY_CODE_REDIRECT_URI if method == "copy_code"
                             else f"http://localhost:{CALLBACK_PORT}{CALLBACK_PATH}")
        self.server: ThreadingHTTPServer | None = None
        self.server_thread: threading.Thread | None = None
        self._result: queue.Queue[tuple[str, str]] = queue.Queue(maxsize=1)
        self._submission_lock = threading.Lock()
        self._claimed = False
        self._closed = threading.Event()
        with _lock:
            self._generation = _generation

    @property
    def auth_url(self) -> str:
        challenge = base64.urlsafe_b64encode(hashlib.sha256(self.verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
        return AUTHORIZE_URL + "?" + urlencode({
            "code": "true", "client_id": CLIENT_ID, "response_type": "code", "redirect_uri": self.redirect_uri,
            "scope": SCOPES, "code_challenge": challenge, "code_challenge_method": "S256", "state": self.state,
        })

    def submit_authorization(self, value: str) -> None:
        if self.cancel.is_set() or self._closed.is_set():
            raise LoginError("Claude sign-in is no longer waiting for a code.")
        code, state = _parse_authorization_input(value, self.redirect_uri)
        if state is not None and not secrets.compare_digest(state.encode("utf-8"), self.state.encode("ascii")):
            raise LoginError("Claude OAuth state mismatch. Use the code from this sign-in attempt.")
        self._offer_result("code", code)

    def _offer_result(self, kind: str, value: str) -> None:
        with self._submission_lock:
            if self._claimed or self._closed.is_set() or self.cancel.is_set():
                raise LoginError("Claude authorization has already been handled or cancelled.")
            self._result.put_nowait((kind, value))
            self._claimed = True

    def wait(self, timeout: float = 300) -> Tokens:
        deadline = time.monotonic() + timeout
        try:
            while True:
                if self.cancel.is_set() or self._closed.is_set():
                    raise LoginError("Claude sign-in cancelled.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise LoginError("Claude sign-in timed out. Please try again.")
                try:
                    kind, code = self._result.get(timeout=min(0.1, remaining))
                    break
                except queue.Empty:
                    continue
            if kind == "error":
                raise LoginError("Claude authorization was denied. Please try again.")
            status, body = _post_json({
                "grant_type": "authorization_code", "client_id": CLIENT_ID, "code": code,
                "state": self.state, "redirect_uri": self.redirect_uri, "code_verifier": self.verifier,
            })
            if status != 200:
                raise LoginError(f"Claude token exchange failed (HTTP {status}). Please start sign-in again.")
            tokens = _parse_tokens(body)
            with _lock:
                if self.cancel.is_set() or self._generation != _generation:
                    raise LoginError("Claude sign-in cancelled.")
                _save_tokens(tokens)
            return tokens
        finally:
            self.close()

    def close(self) -> None:
        self._closed.set()
        if self.server is not None:
            if self.server_thread is not None and self.server_thread.is_alive():
                self.server.shutdown()
            self.server.server_close()
            self.server = None


def _build_handler(handle: LoginHandle):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            try:
                parsed = urlsplit(self.path)
            except ValueError:
                self._page(400, "Invalid callback URL.")
                return
            params = parse_qs(parsed.query, keep_blank_values=True)
            states = params.get("state", [])
            if parsed.path != CALLBACK_PATH:
                self._page(404, "Callback route not found.")
                return
            if len(states) != 1 or not secrets.compare_digest(states[0].encode("utf-8"), handle.state.encode("ascii")):
                self._page(400, "State mismatch. Return to Helper and try signing in again.")
                return
            if "error" in params:
                try:
                    handle._offer_result("error", "")
                except LoginError:
                    pass
                self._page(400, "Claude authorization failed. Return to Helper.")
                return
            try:
                handle.submit_authorization(handle.redirect_uri + "?" + parsed.query)
            except LoginError:
                self._page(400, "Invalid or already handled authorization callback.")
                return
            self._page(200, "Authorization received. Return to Helper to finish sign-in.")

        def _page(self, status: int, message: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(f"<!doctype html><title>Helper — Claude sign-in</title><p>{message}</p>".encode("utf-8"))

        def log_message(self, *_args):
            pass  # Callback URLs contain authorization codes; never log them.
    return Handler


def start_login(cancel: threading.Event | None = None, *, method: str = "browser") -> LoginHandle:
    if method not in {"browser", "copy_code"}:
        raise LoginError("Unknown Claude sign-in method.")
    event = cancel if cancel is not None else threading.Event()
    if event.is_set():
        raise LoginError("Claude sign-in cancelled.")
    handle = LoginHandle(event, method)
    if method == "copy_code":
        return handle
    for port in (CALLBACK_PORT, 0):
        try:
            server = ThreadingHTTPServer((CALLBACK_HOST, port), _build_handler(handle))
        except OSError:
            continue
        handle.server = server
        handle.redirect_uri = f"http://localhost:{server.server_port}{CALLBACK_PATH}"
        handle.server_thread = threading.Thread(target=lambda: server.serve_forever(poll_interval=0.1),
                                               name="helper-claude-callback", daemon=True)
        handle.server_thread.start()
        break
    # If no loopback listener can be opened, a manually pasted redirect URL still works.
    return handle


def get_access_token(*, force_refresh: bool = False) -> str:
    with _lock:
        tokens = load_tokens()
        if tokens is None:
            raise NotSignedIn("Sign in with Claude in Dashboard → Account.")
        if not force_refresh and tokens.expires_at - time.time() > REFRESH_LEEWAY_SEC:
            return tokens.access_token
        status, body = _post_json({"grant_type": "refresh_token", "client_id": CLIENT_ID,
                                   "refresh_token": tokens.refresh_token})
        if status != 200:
            if body.get("error") in {"invalid_grant", "invalid_token"}:
                sign_out()
                raise NotSignedIn("Your Claude sign-in expired or was revoked. Please sign in again.")
            raise LoginError(f"Claude token refresh failed (HTTP {status}). Please try again.")
        refreshed = _parse_tokens(body, tokens.refresh_token)
        _save_tokens(refreshed)
        return refreshed.access_token
