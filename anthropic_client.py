"""Anthropic provider adapter for the Messages API.

Used when HELPER_PROVIDER=anthropic. Reuses ChatResult/ProviderError types and
retry helpers from openai_client so the rest of the codebase doesn't need to
know which provider it's talking to.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import requests

import oauth_anthropic
from openai_client import (
    BadProviderResponse,
    CONNECT_TIMEOUT_SEC,
    READ_TIMEOUT_SEC,
    MAX_RETRIES,
    NotSignedIn,
    RETRYABLE_STATUS_CODES,
    AuthExpired,
    ChatResult,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    _redact,
)

log = logging.getLogger("helper.anthropic")

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# Pi's subscription transport requires Claude Code compatibility (not API-key auth).
CLAUDE_CODE_VERSION = "2.1.280"
OAUTH_BETAS = "claude-code-20250219,oauth-2025-04-20"
OAUTH_SYSTEM_PREFIX = "You are Claude Code, Anthropic's official CLI for Claude."

# Sampling parameters were removed from these model families; sending
# `temperature` returns HTTP 400. Matched by prefix so dated and future
# point-release IDs are covered. Extend when Anthropic ships new families.
SAMPLING_UNSUPPORTED_MODEL_PREFIXES = (
    "claude-opus-4-7",
    "claude-opus-4-8",
    "claude-opus-5",
    "claude-sonnet-5",
    "claude-haiku-5",
    "claude-fable",
    "claude-mythos",
)


def model_accepts_temperature(model: str) -> bool:
    normalized = (model or "").strip().lower()
    return not normalized.startswith(SAMPLING_UNSUPPORTED_MODEL_PREFIXES)


class AnthropicProvider:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
        *,
        subscription: bool = False,
    ) -> None:
        self._subscription = subscription
        self._api_key = api_key
        self._session = session or requests.Session()

    # ------------------------------------------------------------------
    # Public API matching ChatCompletionsProvider / CodexProvider surface.

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        body = self._body(
            messages,
            model=model,
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        payload = self._post(body)
        if self._subscription and (not isinstance(payload, dict) or payload.get("stop_reason") in {
                "max_tokens", "refusal", "tool_use", "pause_turn"}):
            raise BadProviderResponse("Claude did not return a complete answer. Please try again.")
        result = _parse_messages(payload)
        if self._subscription and not result.text:
            raise BadProviderResponse("Claude returned no answer (the request may have been refused).")
        return result

    # ------------------------------------------------------------------
    # Internals.

    def _body(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        system_prompt: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        translated, extracted_system = _to_anthropic_messages(messages)
        system_text = system_prompt or extracted_system or None
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": max(1, int(max_tokens or 4096)),
            "messages": translated,
        }
        if self._subscription:
            body["system"] = [{"type": "text", "text": OAUTH_SYSTEM_PREFIX}]
            if system_text:
                body["system"].append({"type": "text", "text": system_text})
        elif system_text:
            body["system"] = system_text
        if temperature is not None and model_accepts_temperature(model):
            # Anthropic's valid range is 0-1 (the shared HELPER_TEMPERATURE
            # setting allows up to 2.0 for other providers).
            body["temperature"] = min(1.0, max(0.0, float(temperature)))
        return body

    def _headers(self, *, force_refresh: bool = False) -> dict[str, str]:
        headers = {"anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"}
        if not self._subscription:
            return {**headers, "x-api-key": self._api_key}
        try:
            access = oauth_anthropic.get_access_token(force_refresh=force_refresh)
        except oauth_anthropic.NotSignedIn as exc:
            raise NotSignedIn(str(exc)) from exc
        except oauth_anthropic.LoginError as exc:
            raise AuthExpired(str(exc)) from exc
        return {**headers, "Authorization": f"Bearer {access}", "anthropic-beta": OAUTH_BETAS,
                "accept": "application/json", "anthropic-dangerous-direct-browser-access": "true",
                "user-agent": f"claude-cli/{CLAUDE_CODE_VERSION}", "x-app": "cli"}

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        last_error: ProviderError | None = None
        retried_without_temperature = False
        refreshed_after_401 = False
        refresh_requested = False

        for attempt in range(MAX_RETRIES + 1):
            headers = self._headers(force_refresh=refresh_requested)
            refresh_requested = False
            try:
                resp = self._session.post(
                    ANTHROPIC_URL,
                    headers=headers,
                    data=json.dumps(body),
                    timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC),
                    allow_redirects=False,
                )
            except requests.Timeout:
                last_error = ProviderUnavailable("Anthropic request timed out.")
            except requests.RequestException as exc:
                last_error = ProviderUnavailable(f"Anthropic request failed: {exc}")
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise BadProviderResponse(
                            "Anthropic returned non-JSON response."
                        ) from exc

                if self._subscription and resp.status_code == 401:
                    if not refreshed_after_401 and attempt < MAX_RETRIES:
                        refreshed_after_401 = True
                        refresh_requested = True
                        continue
                    raise AuthExpired("Claude rejected your sign-in. Please sign in again in Account.")
                if self._subscription and resp.status_code == 403:
                    raise ProviderError("Claude subscription access denied. Anthropic may restrict this client or model.")
                if resp.status_code in {401, 403}:
                    raise AuthExpired(
                        "Anthropic rejected the key (HTTP "
                        f"{resp.status_code}). Check HELPER_ANTHROPIC_API_KEY."
                    )

                detail = "" if self._subscription else _redact((resp.text or "")[:400])
                message = (f"Claude subscription request failed (HTTP {resp.status_code}). Check account/model access."
                           if self._subscription else f"Anthropic failed: HTTP {resp.status_code}: {detail}")
                if resp.status_code == 429:
                    last_error = RateLimited(message)
                elif resp.status_code in RETRYABLE_STATUS_CODES:
                    last_error = ProviderUnavailable(message)
                elif (
                    resp.status_code == 400
                    and not retried_without_temperature
                    and "temperature" in body
                    and "temperature" in (resp.text or "").lower()
                ):
                    # A model family newer than the prefix list above can
                    # still reject sampling params. Retry once without
                    # temperature instead of failing every call.
                    retried_without_temperature = True
                    body = {k: v for k, v in body.items() if k != "temperature"}
                    log.warning(
                        "Anthropic rejected temperature for model %s; retrying without it",
                        body.get("model"),
                    )
                    continue
                else:
                    raise ProviderError(message)

                log.warning("Anthropic HTTP %s attempt=%s body=%s", resp.status_code, attempt + 1, detail)

            if attempt < MAX_RETRIES:
                time.sleep(0.6 * (2 ** attempt))

        if last_error is not None:
            raise last_error
        raise ProviderUnavailable("Anthropic request failed.")


# ---------------------------------------------------------------------------
# Translation helpers.

def _to_anthropic_messages(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    """Translate Helper's internal message shape to Anthropic's.

    Returns (messages, system_text). system_text is the concatenation of any
    role=system messages in the history — callers can override with their own.
    """
    out: list[dict[str, Any]] = []
    system_chunks: list[str] = []

    for msg in messages:
        role = msg.get("role", "user")
        parts = msg.get("parts") or []

        if role == "system":
            for part in parts:
                if "text" in part and part["text"]:
                    system_chunks.append(part["text"])
            continue

        if role == "assistant":
            blocks: list[dict[str, Any]] = []
            for part in parts:
                if "text" in part and part["text"]:
                    blocks.append({"type": "text", "text": part["text"]})
            if blocks:
                out.append({"role": "assistant", "content": blocks})
            continue

        blocks = []
        for part in parts:
            if "text" in part and part["text"]:
                blocks.append({"type": "text", "text": part["text"]})
            elif "image_png" in part and part["image_png"]:
                blocks.append({
                    "type": "image",
                    "source": {
                        "type": "base64",
                        "media_type": "image/png",
                        "data": base64.b64encode(part["image_png"]).decode("ascii"),
                    },
                })
        if blocks:
            out.append({"role": "user", "content": blocks})

    system_text = "\n\n".join(s for s in system_chunks if s) or None

    # Anthropic requires strict user/assistant alternation and rejects
    # consecutive same-role messages with a 400.
    merged: list[dict[str, Any]] = []
    for msg in out:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"].extend(msg["content"])
        else:
            merged.append(msg)
    return merged, system_text


def _parse_messages(payload: dict[str, Any]) -> ChatResult:
    if not isinstance(payload, dict):
        raise BadProviderResponse("Anthropic returned a non-object response.")
    content = payload.get("content")
    if not isinstance(content, list):
        raise BadProviderResponse("Anthropic response has no content blocks.")

    text_chunks: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        btype = block.get("type")
        if btype == "text":
            t = block.get("text") or ""
            if isinstance(t, str) and t.strip():
                text_chunks.append(t.strip())

    return ChatResult(text=" ".join(text_chunks).strip())
