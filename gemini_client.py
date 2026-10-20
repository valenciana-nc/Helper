"""Gemini provider adapter for the Google Generative Language API.

Used when HELPER_PROVIDER=gemini. Reuses ChatResult/ProviderError types and
retry helpers from openai_client.
"""

from __future__ import annotations

import base64
import json
import logging
import time
from typing import Any

import requests

from openai_client import (
    BadProviderResponse,
    CONNECT_TIMEOUT_SEC,
    READ_TIMEOUT_SEC,
    MAX_RETRIES,
    RETRYABLE_STATUS_CODES,
    AuthExpired,
    ChatResult,
    ProviderError,
    ProviderUnavailable,
    RateLimited,
    _redact,
)

log = logging.getLogger("helper.gemini")

GEMINI_BASE = "https://generativelanguage.googleapis.com/v1beta/models"


class GeminiProvider:
    def __init__(
        self,
        api_key: str,
        session: requests.Session | None = None,
    ) -> None:
        self._api_key = api_key
        self._session = session or requests.Session()

    # ------------------------------------------------------------------

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
            system_prompt=system_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return _parse_generate(self._post(model, body))

    # ------------------------------------------------------------------

    def _body(
        self,
        messages: list[dict[str, Any]],
        *,
        system_prompt: str | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> dict[str, Any]:
        contents, system_text = _to_gemini_contents(messages)
        system_text = system_prompt or system_text
        body: dict[str, Any] = {"contents": contents}
        if system_text:
            body["system_instruction"] = {"parts": [{"text": system_text}]}
        gen_config: dict[str, Any] = {}
        if temperature is not None:
            gen_config["temperature"] = float(temperature)
        if max_tokens is not None:
            gen_config["maxOutputTokens"] = int(max_tokens)
        if gen_config:
            body["generationConfig"] = gen_config
        return body

    def _post(self, model: str, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{GEMINI_BASE}/{model}:generateContent"
        headers = {
            "x-goog-api-key": self._api_key,
            "content-type": "application/json",
        }
        last_error: ProviderError | None = None

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._session.post(
                    url,
                    headers=headers,
                    data=json.dumps(body),
                    timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC),
                )
            except requests.Timeout:
                last_error = ProviderUnavailable("Gemini request timed out.")
            except requests.RequestException as exc:
                last_error = ProviderUnavailable(f"Gemini request failed: {exc}")
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise BadProviderResponse(
                            "Gemini returned non-JSON response."
                        ) from exc

                if resp.status_code in {401, 403}:
                    raise AuthExpired(
                        "Gemini rejected the key (HTTP "
                        f"{resp.status_code}). Check HELPER_GEMINI_API_KEY."
                    )

                detail = _redact((resp.text or "")[:400])
                message = f"Gemini failed: HTTP {resp.status_code}: {detail}"
                if resp.status_code == 429:
                    last_error = RateLimited(message)
                elif resp.status_code in RETRYABLE_STATUS_CODES:
                    last_error = ProviderUnavailable(message)
                else:
                    raise ProviderError(message)

                log.warning("Gemini HTTP %s attempt=%s body=%s", resp.status_code, attempt + 1, detail)

            if attempt < MAX_RETRIES:
                time.sleep(0.6 * (2 ** attempt))

        if last_error is not None:
            raise last_error
        raise ProviderUnavailable("Gemini request failed.")


# ---------------------------------------------------------------------------

def _to_gemini_contents(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
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

        gem_role = "model" if role == "assistant" else "user"
        gem_parts: list[dict[str, Any]] = []
        for part in parts:
            if "text" in part and part["text"]:
                gem_parts.append({"text": part["text"]})
            elif "image_png" in part and part["image_png"]:
                gem_parts.append({
                    "inline_data": {
                        "mime_type": "image/png",
                        "data": base64.b64encode(part["image_png"]).decode("ascii"),
                    },
                })
        if gem_parts:
            out.append({"role": gem_role, "parts": gem_parts})

    system_text = "\n\n".join(s for s in system_chunks if s) or None
    # Gemini requires strict user/model alternation; merge consecutive same-role
    # messages (same as the Anthropic translator does).
    merged: list[dict[str, Any]] = []
    for msg in out:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["parts"].extend(msg["parts"])
        else:
            merged.append(msg)
    return merged, system_text


def _parse_generate(payload: dict[str, Any]) -> ChatResult:
    if not isinstance(payload, dict):
        raise BadProviderResponse("Gemini returned a non-object response.")
    candidates = payload.get("candidates") or []
    if not isinstance(candidates, list) or not candidates:
        # Possible promptFeedback block from safety filters.
        feedback = payload.get("promptFeedback") or {}
        if isinstance(feedback, dict) and feedback.get("blockReason"):
            raise BadProviderResponse(
                f"Gemini blocked the prompt: {feedback.get('blockReason')}"
            )
        raise BadProviderResponse("Gemini response had no candidates.")
    candidate = candidates[0]
    if not isinstance(candidate, dict):
        raise BadProviderResponse("Gemini response contained an invalid candidate.")
    content = candidate.get("content") or {}
    if not isinstance(content, dict):
        raise BadProviderResponse("Gemini candidate content was not an object.")
    parts = content.get("parts") or []
    if not isinstance(parts, list):
        raise BadProviderResponse("Gemini candidate parts was not a list.")
    text_chunks: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = part.get("text")
        if isinstance(text, str) and text:
            text_chunks.append(text.strip())
    return ChatResult(text=" ".join(text_chunks).strip())
