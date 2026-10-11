"""v2 LLM adapter: wraps v1's provider stack as a v2 LlmPort.

Lazy imports so `import v2.llm` never touches network, keys, or Qt.
"""
from __future__ import annotations


class V2LlmAdapter:
    """Adapt v1's OpenAIClient.chat() to the v2 LlmPort protocol."""

    def __init__(self, model: str = "gpt-5.5") -> None:
        from openai_client import make_openai_client

        self._client = make_openai_client()
        self._model = model

    def chat(
        self,
        messages: list[dict],
        *,
        system: str | None = None,
        max_tokens: int = 1024,
    ) -> str:
        result = self._client.chat(
            messages,
            model=self._model,
            system_prompt=system,
            max_tokens=max_tokens,
        )
        return result.text or ""
