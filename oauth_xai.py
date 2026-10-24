"""Native xAI subscription sign-in using the RFC 8628 device grant.

Protocol reference: earendil-works/pi, packages/ai/src/auth/oauth/xai.ts
(commit 136b87e). No Pi process or Pi credential files are used.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import asdict, dataclass
import json
import logging
import math
import threading
import time
from urllib.parse import urlsplit

import keyring
import requests

log = logging.getLogger("helper.oauth_xai")
CLIENT_ID = "b1a00492-073a-47ea-816f-4c329264a828"
SCOPES = "openid profile email offline_access grok-cli:access api:access"
DEVICE_CODE_URL = "https://auth.x.ai/oauth2/device/code"
TOKEN_URL = "https://auth.x.ai/oauth2/token"
SERVICE = "Helper"
USERNAME = "xai-oauth"
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


@dataclass(frozen=True)
class DeviceCode:
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: float
    interval: float


def _required_string(body: dict, field: str) -> str:
    value = body.get(field)
    if not isinstance(value, str) or not value.strip() or any(c.isspace() for c in value):
        raise LoginError(f"Invalid xAI OAuth response field: {field}")
    return value


def _positive(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def _verification_uri(value: str) -> str:
    try:
        url = urlsplit(value)
        # Only open official xAI authorization pages, never arbitrary schemes/hosts.
        if (url.scheme == "https" and url.hostname in {"accounts.x.ai", "auth.x.ai", "x.ai"}
                and not url.username and not url.password and url.port in {None, 443}):
            return value
    except ValueError:
        pass
    raise LoginError("Untrusted verification URI in xAI OAuth response.")


def _post_form(url: str, fields: dict[str, str]) -> tuple[int, dict]:
    try:
        response = requests.post(url, data=fields, headers={"Accept": "application/json"},
                                 timeout=(10, 30), allow_redirects=False)
    except requests.RequestException as exc:
        # Do not include request bodies or response text: they may contain credentials.
        raise LoginError("Could not reach xAI authentication. Please try again.") from exc
    try:
        body = response.json()
    except ValueError as exc:
        raise LoginError(f"xAI authentication returned invalid JSON (HTTP {response.status_code}).") from exc
    if not isinstance(body, dict):
        raise LoginError("xAI authentication returned a non-object response.")
    return response.status_code, body


def _request_error(action: str, status: int) -> LoginError:
    return LoginError(f"xAI {action} failed (HTTP {status}). Please try signing in again.")


def _parse_tokens(body: dict, previous_refresh: str | None = None) -> Tokens:
    access = _required_string(body, "access_token")
    refresh = (previous_refresh if "refresh_token" not in body and previous_refresh
               else _required_string(body, "refresh_token"))
    lifetime = body.get("expires_in", 3600)
    if not _positive(lifetime):
        raise LoginError("Invalid xAI OAuth response field: expires_in")
    return Tokens(access, refresh, time.time() + lifetime)


def load_tokens() -> Tokens | None:
    try:
        raw = keyring.get_password(SERVICE, USERNAME)
        if not raw:
            return None
        body = json.loads(raw)
        if not isinstance(body, dict):
            return None
        access = _required_string(body, "access_token")
        refresh = _required_string(body, "refresh_token")
        expiry = body.get("expires_at")
        if not _positive(expiry):
            return None
        return Tokens(access, refresh, expiry)
    except Exception:
        log.debug("xAI credentials unavailable or unreadable; treating as signed out.")
        return None


def _save_tokens(tokens: Tokens) -> None:
    try:
        keyring.set_password(SERVICE, USERNAME, json.dumps(asdict(tokens)))
    except Exception as exc:
        raise LoginError("Could not save Grok sign-in in the system credential manager.") from exc


def is_signed_in() -> bool:
    return load_tokens() is not None


def sign_out() -> None:
    global _generation
    with _lock:
        _generation += 1  # Invalidate any login that started before sign-out.
        try:
            keyring.delete_password(SERVICE, USERNAME)
        except keyring.errors.PasswordDeleteError:
            pass
        except Exception as exc:
            raise LoginError("Could not remove Grok sign-in from the system credential manager.") from exc


def login(on_device_code: Callable[[DeviceCode], None], cancel: threading.Event) -> Tokens:
    with _lock:
        generation = _generation
    if cancel.is_set():
        raise LoginError("Grok sign-in cancelled.")
    status, body = _post_form(DEVICE_CODE_URL, {"client_id": CLIENT_ID, "scope": SCOPES, "referrer": "helper"})
    if status != 200:
        raise _request_error("device authorization", status)
    interval = body.get("interval")
    lifetime = body.get("expires_in")
    if not _positive(lifetime):
        raise LoginError("Invalid xAI OAuth response field: expires_in")
    uri = _verification_uri(_required_string(body, "verification_uri"))
    if body.get("verification_uri_complete"):
        uri = _verification_uri(_required_string(body, "verification_uri_complete"))
    device = DeviceCode(_required_string(body, "device_code"), _required_string(body, "user_code"),
                        uri, lifetime, max(1.0, interval) if _positive(interval) else 5.0)
    deadline = time.monotonic() + device.expires_in
    if cancel.is_set():
        raise LoginError("Grok sign-in cancelled.")
    on_device_code(device)
    delay = device.interval
    # Wait before the first poll and honor pending/slow_down throughout the flow.
    while time.monotonic() < deadline:
        if cancel.wait(min(delay, max(0, deadline - time.monotonic()))):
            raise LoginError("Grok sign-in cancelled.")
        if time.monotonic() >= deadline:
            break
        status, body = _post_form(TOKEN_URL, {
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            "client_id": CLIENT_ID, "device_code": device.device_code,
        })
        if cancel.is_set():
            raise LoginError("Grok sign-in cancelled.")
        if status == 200:
            tokens = _parse_tokens(body)
            with _lock:
                if cancel.is_set() or generation != _generation:
                    raise LoginError("Grok sign-in cancelled.")
                _save_tokens(tokens)
            return tokens
        error = body.get("error")
        if error == "authorization_pending":
            continue
        if error == "slow_down":
            reported = body.get("interval")
            delay = max(delay + 5, reported) if _positive(reported) else delay + 5
            continue
        if error in {"access_denied", "authorization_denied"}:
            raise LoginError("Grok device authorization was denied.")
        if error == "expired_token":
            break
        raise _request_error("device authorization", status)
    raise LoginError("Grok device code expired. Please sign in again.")


def get_access_token(*, force_refresh: bool = False) -> str:
    # Serialize refreshes and sign-out; never resurrect a removed credential.
    with _lock:
        tokens = load_tokens()
        if tokens is None:
            raise NotSignedIn("Sign in with Grok in Dashboard → Account.")
        if not force_refresh and tokens.expires_at - time.time() > REFRESH_LEEWAY_SEC:
            return tokens.access_token
        status, body = _post_form(TOKEN_URL, {
            "grant_type": "refresh_token", "client_id": CLIENT_ID, "refresh_token": tokens.refresh_token,
        })
        if status != 200:
            if body.get("error") in {"invalid_grant", "invalid_token"}:
                sign_out()
                raise NotSignedIn("Your Grok sign-in expired or was revoked. Please sign in again.")
            raise _request_error("token refresh", status)
        refreshed = _parse_tokens(body, tokens.refresh_token)
        _save_tokens(refreshed)
        return refreshed.access_token
