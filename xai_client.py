"""xAI Responses adapter for native Grok subscription authentication."""
from __future__ import annotations

import json
import time
from typing import Any

import requests

import oauth_xai
from openai_client import (
    AuthExpired, BadProviderResponse, ChatResult, CONNECT_TIMEOUT_SEC, MAX_RETRIES,
    NotSignedIn, ProviderError, ProviderUnavailable, RateLimited, READ_TIMEOUT_SEC,
    RETRYABLE_STATUS_CODES, _parse_response, _to_responses_input,
)

RESPONSES_URL = "https://api.x.ai/v1/responses"


class XaiSubscriptionProvider:
    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def chat(
        self, messages: list[dict[str, Any]], *, model: str, system_prompt: str | None = None,
        temperature: float | None = None, max_tokens: int | None = None,
    ) -> ChatResult:
        inputs = _to_responses_input(messages, system_prompt)
        if system_prompt:
            inputs[0]["role"] = "developer"
        body = {"model": model, "store": False, "stream": False, "input": inputs}
        if max_tokens is not None:
            body["max_output_tokens"] = int(max_tokens)
        # Match Pi's reasoning-model defaults; do not send an unsupported temperature.
        payload = self._post(body)
        if not isinstance(payload, dict) or payload.get("status") in {"incomplete", "failed", "cancelled"}:
            raise BadProviderResponse("Grok did not return a complete response. Please try again.")
        result = _parse_response(payload, provider_name="Grok")
        if not result.text:
            raise BadProviderResponse("Grok returned no answer (the request may have been refused).")
        return result

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        refreshed_after_401 = False
        refresh_requested = False
        last_error: ProviderError | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                token = oauth_xai.get_access_token(force_refresh=refresh_requested)
                refresh_requested = False
            except oauth_xai.NotSignedIn as exc:
                raise NotSignedIn(str(exc)) from exc
            except oauth_xai.LoginError as exc:
                raise AuthExpired(str(exc)) from exc
            try:
                response = self._session.post(
                    RESPONSES_URL,
                    headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json",
                             "User-Agent": "Helper"},
                    data=json.dumps(body), timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC), allow_redirects=False,
                )
            except requests.RequestException as exc:
                last_error = ProviderUnavailable("Could not reach Grok. Please try again.")
                last_error.__cause__ = exc
            else:
                status = response.status_code
                if status == 200:
                    try:
                        return response.json()
                    except ValueError as exc:
                        raise BadProviderResponse("Grok returned invalid JSON.") from exc
                if status == 401:
                    if not refreshed_after_401 and attempt < MAX_RETRIES:
                        refreshed_after_401 = True
                        refresh_requested = True
                        continue
                    raise AuthExpired("Grok rejected your sign-in. Please sign in again in Account.")
                if status == 403:
                    raise ProviderError("Grok access denied. Check your subscription and model access in your xAI account.")
                if status == 429:
                    last_error = RateLimited("Grok usage limit reached. Please wait and try again.")
                elif status in RETRYABLE_STATUS_CODES:
                    last_error = ProviderUnavailable(f"Grok is temporarily unavailable (HTTP {status}).")
                else:
                    # Never surface/log raw response bodies containing private request data.
                    raise ProviderError(f"Grok request failed (HTTP {status}). Check the selected model and account.")
            if attempt < MAX_RETRIES:
                time.sleep(0.6 * (2 ** attempt))
        raise last_error or ProviderUnavailable("Grok request failed.")
