from __future__ import annotations

import base64
import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any

import requests

from config import CODEX_MODEL_DEFAULT, codex_request_model
import oauth_codex
import token_store

log = logging.getLogger("helper.openai")

CODEX_RESPONSES_URL = "https://chatgpt.com/backend-api/codex/responses"
API_BASE = "https://api.openai.com/v1"


class AudioUnavailable(RuntimeError):
    pass


class ProviderError(RuntimeError):
    pass


class NotSignedIn(ProviderError):
    pass


class AuthExpired(ProviderError):
    pass


class RateLimited(ProviderError):
    pass


class ProviderUnavailable(ProviderError):
    pass


class BadProviderResponse(ProviderError):
    pass


class UnsupportedModel(ProviderError):
    pass


@dataclass(frozen=True)
class ChatResult:
    text: str


class InvalidChatRequest(ProviderError):
    """A local caller passed messages no provider adapter can serialize."""


_NEUTRAL_PART_KEYS = frozenset({"text", "image_png"})


def _require_neutral_messages(messages: list[dict[str, Any]]) -> None:
    """Reject wrong-shaped history before any network call.

    Every provider adapter reads ``parts``. A message built for another API shape
    (for example ``content`` items) would otherwise serialize to an empty request.
    """
    if not isinstance(messages, list) or not messages:
        raise InvalidChatRequest("Helper built an empty model request.")
    usable = False
    for message in messages:
        if not isinstance(message, dict) or "content" in message:
            raise InvalidChatRequest("Helper built a model request in an unsupported message format.")
        parts = message.get("parts")
        if not isinstance(parts, list):
            raise InvalidChatRequest("Each model request message needs a parts list.")
        for part in parts:
            if not isinstance(part, dict) or len(part) != 1 or not part.keys() <= _NEUTRAL_PART_KEYS:
                raise InvalidChatRequest("Helper built a model request with an unsupported part.")
            value = next(iter(part.values()))
            if (isinstance(value, str) and value.strip()) or (isinstance(value, bytes) and value) or isinstance(
                    value, dict):
                usable = True
    if not usable:
        raise InvalidChatRequest("Helper built a model request without text or an image.")


RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}
CONNECT_TIMEOUT_SEC = 10
READ_TIMEOUT_SEC = 120
MAX_RETRIES = 2


class OpenAIClient:
    def __init__(
        self,
        *,
        optional_api_key: str | None = None,
        codex_provider: "CodexProvider | None" = None,
        custom_provider: Any | None = None,
        custom_model: str | None = None,
    ) -> None:
        self._api_key = optional_api_key or None
        self._session = requests.Session()
        # `_custom` bypasses the Codex OAuth path for configured providers.
        self._custom = custom_provider
        self._custom_model = (custom_model or "").strip() or None
        self._codex = codex_provider or CodexProvider(self._session)

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        response_schema: dict[str, Any] | None = None,
    ) -> ChatResult:
        _require_neutral_messages(messages)
        if self._custom is not None:
            # Compatible endpoints and non-OpenAI providers do not all implement
            # the same structured-output API. Keep their existing wire format.
            if response_schema is not None:
                system_prompt = ((system_prompt or "") + "\nReturn JSON matching this schema:\n"
                                 + json.dumps(response_schema, ensure_ascii=True))
            return self._custom.chat(
                messages,
                model=self._custom_model or model,
                system_prompt=system_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        model = self._codex_model(model)
        body = {
            "model": model,
            "store": False,
            "stream": True,
            "instructions": system_prompt or "",
            "input": _to_responses_input(messages, None),
        }
        if max_tokens is not None:
            body["max_output_tokens"] = int(max_tokens)
        if response_schema is not None:
            body["text"] = {"format": {"type": "json_schema", "name": "helper_response",
                                      "strict": True, "schema": response_schema}}
        payload = self._codex.post_response(body)
        return _parse_response(payload)

    def transcribe(self, wav_bytes: bytes, *, model: str) -> str:
        if not self._api_key:
            raise AudioUnavailable(
                "Voice transcription needs an OpenAI API key. Add OPENAI_API_KEY in the dashboard."
            )
        files = {"file": ("speech.wav", wav_bytes, "audio/wav")}
        data = {"model": model, "response_format": "text"}
        resp = self._session.post(
            f"{API_BASE}/audio/transcriptions",
            headers={"Authorization": f"Bearer {self._api_key}"},
            files=files,
            data=data,
            timeout=60,
        )
        if resp.status_code != 200:
            raise ProviderError(f"Whisper failed: HTTP {resp.status_code}: {resp.text[:300]}")
        text = (resp.text or "").strip()
        if not text:
            raise ProviderError("Whisper returned an empty transcript.")
        return text

    def synthesize(self, text: str, *, model: str, voice: str) -> tuple[bytes, str]:
        if not self._api_key:
            raise AudioUnavailable(
                "Spoken replies need an OpenAI API key. Add OPENAI_API_KEY in the dashboard."
            )
        body = {"model": model, "voice": voice, "input": text, "response_format": "wav"}
        resp = self._session.post(
            f"{API_BASE}/audio/speech",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            data=json.dumps(body),
            timeout=60,
        )
        if resp.status_code != 200:
            raise ProviderError(f"TTS failed: HTTP {resp.status_code}: {resp.text[:300]}")
        return resp.content, "audio/wav"

    @staticmethod
    def _codex_model(model: str) -> str:
        resolved = codex_request_model(model, CODEX_MODEL_DEFAULT)
        if resolved != model:
            log.warning(
                "Model %r is not valid for Codex auth; using %s instead.",
                model,
                resolved,
            )
        return resolved


class CodexProvider:
    def __init__(self, session: requests.Session | None = None) -> None:
        self._session = session or requests.Session()

    def post_response(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = self._headers()
        data = json.dumps(body)
        last_error: ProviderError | None = None

        for attempt in range(MAX_RETRIES + 1):
            client_request_id = uuid.uuid4().hex
            try:
                resp = self._session.post(
                    CODEX_RESPONSES_URL,
                    headers={**headers, "X-Client-Request-Id": client_request_id},
                    data=data,
                    timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC),
                )
            except requests.Timeout as exc:
                last_error = ProviderUnavailable("ChatGPT/Codex request timed out.")
                log.warning("Codex timeout request_id=%s: %s", client_request_id, exc)
            except requests.RequestException as exc:
                last_error = ProviderUnavailable(f"ChatGPT/Codex request failed: {exc}")
                log.warning("Codex request failed request_id=%s: %s", client_request_id, exc)
            else:
                request_id = resp.headers.get("x-request-id") or client_request_id
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        return _parse_stream_response(resp.text, request_id, exc)

                if resp.status_code in {401, 403}:
                    token_store.clear()
                    raise AuthExpired("Your ChatGPT sign-in expired. Please sign in again.")

                message = _provider_error_message(resp, request_id)
                if resp.status_code == 429:
                    last_error = RateLimited(message)
                elif resp.status_code in RETRYABLE_STATUS_CODES:
                    last_error = ProviderUnavailable(message)
                elif resp.status_code == 400 and _is_unsupported_model_response(resp):
                    raise UnsupportedModel(message)
                else:
                    raise ProviderError(message)

                log.warning(
                    "Codex HTTP %s request_id=%s attempt=%s body=%s",
                    resp.status_code,
                    request_id,
                    attempt + 1,
                    _redact(resp.text),
                )

            if attempt < MAX_RETRIES:
                time.sleep(0.6 * (2**attempt))

        if last_error is not None:
            raise last_error
        raise ProviderUnavailable("ChatGPT/Codex request failed.")

    @staticmethod
    def _headers() -> dict[str, str]:
        try:
            access = oauth_codex.get_access_token()
        except oauth_codex.NotSignedIn as exc:
            raise NotSignedIn(str(exc)) from exc
        except oauth_codex.LoginError as exc:
            raise AuthExpired(str(exc)) from exc

        tokens = token_store.load()
        headers = {
            "Authorization": f"Bearer {access}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses=experimental",
            "Originator": "helper",
        }
        if tokens and tokens.account_id:
            headers["chatgpt-account-id"] = tokens.account_id
        return headers


def _to_responses_input(messages: list[dict[str, Any]], system_prompt: str | None) -> list[dict[str, Any]]:
    """Translate neutral text/image messages into the Responses API input shape."""
    out: list[dict[str, Any]] = []
    if system_prompt:
        out.append({"role": "system", "content": [{"type": "input_text", "text": system_prompt}]})

    for msg in messages:
        role = msg.get("role", "user")
        parts = msg.get("parts") or []
        content_items = []
        for part in parts:
            if "text" in part and part["text"]:
                content_type = "output_text" if role == "assistant" else "input_text"
                content_items.append({"type": content_type, "text": part["text"]})
            elif "image_png" in part and part["image_png"]:
                b64 = base64.b64encode(part["image_png"]).decode("ascii")
                content_items.append({
                    "type": "input_image",
                    "image_url": f"data:image/png;base64,{b64}",
                })
        if content_items:
            out.append({"role": role, "content": content_items})
    return out


def _parse_response(payload: dict[str, Any]) -> ChatResult:
    if not isinstance(payload, dict):
        raise BadProviderResponse("ChatGPT/Codex returned a non-object response.")

    output = payload.get("output") or []
    if not isinstance(output, list):
        raise BadProviderResponse("ChatGPT/Codex response output was not a list.")

    text_chunks: list[str] = []

    for item in output:
        if not isinstance(item, dict):
            raise BadProviderResponse("ChatGPT/Codex response contained an invalid output item.")
        item_type = item.get("type")
        if item_type == "message":
            content_items = item.get("content") or []
            if not isinstance(content_items, list):
                raise BadProviderResponse("ChatGPT/Codex message content was not a list.")
            for content in content_items:
                if not isinstance(content, dict):
                    raise BadProviderResponse("ChatGPT/Codex message contained an invalid content item.")
                text = content.get("text")
                if content.get("type") in ("output_text", "text") and isinstance(text, str) and text:
                    text_chunks.append(text)

    return ChatResult(text=" ".join(c.strip() for c in text_chunks if c.strip()))


def _parse_stream_response(text: str, request_id: str, json_error: ValueError | None = None) -> dict[str, Any]:
    output_items: list[dict[str, Any]] = []
    text_deltas: list[str] = []

    for line in (text or "").splitlines():
        if not line.startswith("data:"):
            continue
        raw = line.removeprefix("data:").strip()
        if not raw or raw == "[DONE]":
            continue
        try:
            event = json.loads(raw)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue

        event_type = event.get("type")
        if event_type == "response.output_item.done":
            item = event.get("item")
            if isinstance(item, dict):
                output_items.append(item)
        elif event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str):
                text_deltas.append(delta)

    if output_items:
        return {"output": output_items}
    if text_deltas:
        return {
            "output": [
                {
                    "type": "message",
                    "content": [{"type": "output_text", "text": "".join(text_deltas)}],
                }
            ]
        }
    raise BadProviderResponse(
        f"ChatGPT/Codex returned an unreadable stream. Request id: {request_id}"
    ) from json_error


def _provider_error_message(resp: requests.Response, request_id: str) -> str:
    detail = _response_error_detail(resp)
    suffix = f" Request id: {request_id}."
    if resp.status_code == 400 and _is_unsupported_model_response(resp):
        model = _extract_rejected_model(detail or resp.text)
        if model:
            return (
                f"ChatGPT/Codex does not support model {model!r} with your ChatGPT account. "
                f"Helper uses {CODEX_MODEL_DEFAULT} for Codex requests; update HELPER_AGENT_MODEL "
                f"or HELPER_REASONING_MODEL if you need a different OpenAI model."
            ) + suffix
        return (
            f"ChatGPT/Codex rejected the configured model. Helper uses {CODEX_MODEL_DEFAULT} "
            "for Codex requests; update HELPER_AGENT_MODEL or HELPER_REASONING_MODEL."
        ) + suffix
    if resp.status_code == 429:
        return "ChatGPT/Codex is rate limited. Please wait and try again." + suffix
    if resp.status_code in {500, 502, 503, 504}:
        return f"ChatGPT/Codex is temporarily unavailable: HTTP {resp.status_code}." + suffix
    text = _redact((detail or resp.text)[:400])
    return f"ChatGPT/Codex failed: HTTP {resp.status_code}: {text}" + suffix


def _response_error_detail(resp: requests.Response) -> str:
    try:
        payload = resp.json()
    except ValueError:
        return resp.text or ""
    if isinstance(payload, dict):
        detail = payload.get("detail") or payload.get("error") or payload.get("message")
        if isinstance(detail, dict):
            return json.dumps(detail)
        if detail is not None:
            return str(detail)
    return resp.text or ""


def _is_unsupported_model_response(resp: requests.Response) -> bool:
    text = _response_error_detail(resp).lower()
    return "model" in text and "not supported" in text


def _extract_rejected_model(text: str) -> str | None:
    match = re.search(r"['\"]([^'\"]+)['\"]\s+model\s+is\s+not\s+supported", text, re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"model\s+['\"]([^'\"]+)['\"]", text, re.IGNORECASE)
    if match:
        return match.group(1)
    return None


def _redact(text: str) -> str:
    redacted = text.replace("\r", " ").replace("\n", " ")
    for marker in ("access_token", "refresh_token", "id_token", "Authorization"):
        redacted = redacted.replace(marker, f"{marker[:4]}...")
    return redacted[:400]


class ChatCompletionsProvider:
    """Talks to any OpenAI-compatible /v1/chat/completions endpoint.

    Used when the user configures HELPER_API_BASE_URL + HELPER_API_KEY so Helper
    can be powered by OpenAI, Groq, OpenRouter, Together, LM Studio, vLLM,
    Ollama (with /v1), etc., instead of the ChatGPT/Codex OAuth path.
    """

    def __init__(
        self,
        base_url: str,
        api_key: str,
        session: requests.Session | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._session = session or requests.Session()

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        model: str,
        system_prompt: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResult:
        body: dict[str, Any] = {
            "model": model,
            "messages": _to_chat_completions_messages(messages, system_prompt),
        }
        if temperature is not None:
            body["temperature"] = float(temperature)
        if max_tokens is not None:
            body["max_tokens"] = int(max_tokens)
        payload = self._post(body)
        return _parse_chat_completions(payload)

    def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        url = f"{self._base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }
        last_error: ProviderError | None = None
        retried_without_temperature = False

        for attempt in range(MAX_RETRIES + 1):
            try:
                resp = self._session.post(
                    url,
                    headers=headers,
                    data=json.dumps(body),
                    timeout=(CONNECT_TIMEOUT_SEC, READ_TIMEOUT_SEC),
                )
            except requests.Timeout:
                last_error = ProviderUnavailable("Custom API request timed out.")
            except requests.RequestException as exc:
                last_error = ProviderUnavailable(f"Custom API request failed: {exc}")
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as exc:
                        raise BadProviderResponse(
                            "Custom API returned non-JSON response."
                        ) from exc

                if resp.status_code in {401, 403}:
                    raise AuthExpired(
                        "Custom API rejected the key (HTTP "
                        f"{resp.status_code}). Check HELPER_API_KEY."
                    )

                detail = _redact((resp.text or "")[:400])
                message = f"Custom API failed: HTTP {resp.status_code}: {detail}"
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
                    # Reasoning-style models on custom endpoints reject
                    # non-default temperature. Retry once without it instead
                    # of failing every call.
                    retried_without_temperature = True
                    body = {k: v for k, v in body.items() if k != "temperature"}
                    log.warning(
                        "Custom API rejected temperature for model %s; retrying without it",
                        body.get("model"),
                    )
                    continue
                else:
                    raise ProviderError(message)

                log.warning("Custom API HTTP %s attempt=%s body=%s", resp.status_code, attempt + 1, detail)

            if attempt < MAX_RETRIES:
                time.sleep(0.6 * (2**attempt))

        if last_error is not None:
            raise last_error
        raise ProviderUnavailable("Custom API request failed.")


def _to_chat_completions_messages(
    messages: list[dict[str, Any]],
    system_prompt: str | None,
) -> list[dict[str, Any]]:
    """Translate neutral text/image history into chat-completions messages."""
    out: list[dict[str, Any]] = []
    if system_prompt:
        out.append({"role": "system", "content": system_prompt})

    for msg in messages:
        role = msg.get("role", "user")
        parts = msg.get("parts") or []
        text_chunks = [part["text"] for part in parts if part.get("text")]
        if role == "system":
            if text_chunks:
                out.append({"role": "system", "content": " ".join(text_chunks)})
            continue
        if role == "assistant":
            if text_chunks:
                out.append({"role": "assistant", "content": " ".join(text_chunks)})
            continue

        content_items: list[dict[str, Any]] = [
            {"type": "text", "text": text} for text in text_chunks
        ]
        for part in parts:
            if "image_png" in part and part["image_png"]:
                b64 = base64.b64encode(part["image_png"]).decode("ascii")
                content_items.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{b64}"},
                })

        if content_items:
            if len(content_items) == 1 and content_items[0].get("type") == "text":
                out.append({"role": "user", "content": content_items[0]["text"]})
            else:
                out.append({"role": "user", "content": content_items})
    return out


def _parse_chat_completions(payload: dict[str, Any]) -> ChatResult:
    if not isinstance(payload, dict):
        raise BadProviderResponse("Custom API returned a non-object response.")
    choices = payload.get("choices") or []
    if not isinstance(choices, list) or not choices:
        raise BadProviderResponse("Custom API response had no choices.")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise BadProviderResponse("Custom API response contained an invalid choice.")
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        raise BadProviderResponse("Custom API response contained an invalid message.")

    text = ""
    content = message.get("content")
    if isinstance(content, str):
        text = content.strip()
    elif isinstance(content, list):
        chunks = [
            c.get("text", "")
            for c in content
            if isinstance(c, dict)
            and c.get("type") in ("text", "output_text")
            and isinstance(c.get("text"), str)
        ]
        text = " ".join(chunk.strip() for chunk in chunks if chunk.strip())

    return ChatResult(text=text)


def make_openai_client(
    *,
    optional_api_key: str | None = None,
) -> OpenAIClient:
    """Build a client honoring HELPER_PROVIDER and the legacy custom-API config.

    Dispatch:
      - codex          -> Codex OAuth path via OpenAIClient defaults
      - openai_compat  -> ChatCompletionsProvider on any /v1 endpoint
      - anthropic      -> AnthropicProvider
      - gemini         -> GeminiProvider
      - xai            -> ChatCompletionsProvider on xAI's fixed API endpoint

    For backwards-compat: if HELPER_PROVIDER is unset but HELPER_API_BASE_URL +
    HELPER_API_KEY are both set, behave as if openai_compat were selected.

    optional_api_key is still forwarded so Whisper / TTS (OpenAI-only) keep
    working regardless of which provider serves chat.
    """
    import config

    provider_name = config.effective_provider()

    if provider_name == "xai":
        if not config.XAI_API_KEY:
            raise NotSignedIn("Connect Grok with an xAI API key in Dashboard → Account.")
        return OpenAIClient(
            optional_api_key=optional_api_key,
            custom_provider=ChatCompletionsProvider("https://api.x.ai/v1", config.XAI_API_KEY),
        )

    if provider_name == "openai_compat":
        if not config.custom_api_enabled():
            log.warning(
                "HELPER_PROVIDER=openai_compat but base URL / API key are not set; falling back to Codex."
            )
        else:
            endpoint_error = config.validate_api_base_url(config.API_BASE_URL)
            if endpoint_error:
                raise ProviderError(endpoint_error)
            provider = ChatCompletionsProvider(config.API_BASE_URL, config.API_KEY)
            log.info("Chat provider: openai_compat (custom endpoint configured)")
            return OpenAIClient(
                optional_api_key=optional_api_key,
                custom_provider=provider,
                custom_model=config.API_MODEL,
            )

    if provider_name == "anthropic":
        if not config.ANTHROPIC_API_KEY:
            raise NotSignedIn("Connect Claude with an Anthropic API key in Dashboard → Account.")
        else:
            from anthropic_client import AnthropicProvider

            log.info("Chat provider: anthropic")
            return OpenAIClient(
                optional_api_key=optional_api_key,
                custom_provider=AnthropicProvider(config.ANTHROPIC_API_KEY),
                # NB: API_MODEL belongs to the openai_compat provider only.
                # A stale HELPER_API_MODEL must not override the model the
                # user picked for Anthropic.
                custom_model=None,
            )

    if provider_name == "gemini":
        if not config.GEMINI_API_KEY:
            log.warning(
                "HELPER_PROVIDER=gemini but HELPER_GEMINI_API_KEY is empty; falling back to Codex."
            )
        else:
            from gemini_client import GeminiProvider

            log.info("Chat provider: gemini")
            return OpenAIClient(
                optional_api_key=optional_api_key,
                custom_provider=GeminiProvider(config.GEMINI_API_KEY),
                # NB: API_MODEL belongs to the openai_compat provider only.
                custom_model=None,
            )

    log.info("Chat provider: codex (ChatGPT OAuth)")
    return OpenAIClient(optional_api_key=optional_api_key)
