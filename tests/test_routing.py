from __future__ import annotations

import unittest
from unittest.mock import patch

from agent import HelplerAgent
from openai_client import ChatResult


class FakeClient:
    def __init__(self, classify_word: str | None = None) -> None:
        self._word = classify_word
        self.calls: list[str] = []

    def chat(self, *args, **kwargs):  # noqa: ANN001
        raise AssertionError("chat must not be called during routing tests")

    def computer_use_step(self, *args, **kwargs):  # noqa: ANN001
        raise AssertionError("computer_use_step must not be called during routing tests")

    def classify_route(self, text: str, *, model: str) -> str:
        self.calls.append(text)
        return self._word or ""


class RouteRuleTests(unittest.TestCase):
    def test_chat_prefix_routes_to_chat(self) -> None:
        cases = [
            "how do I open Gmail",
            "what is a thread pool",
            "explain virtual memory",
            "tell me about Windows DPI",
            "why doesn't this build",
            "what's the difference between X and Y",
        ]
        for text in cases:
            with self.subTest(text=text):
                self.assertEqual(HelplerAgent._route_rules(text), "chat")

    def test_short_question_without_action_keyword_routes_to_chat(self) -> None:
        self.assertEqual(HelplerAgent._route_rules("is this a good idea?"), "chat")
        self.assertEqual(HelplerAgent._route_rules("are you sure?"), "chat")

    def test_short_question_with_action_keyword_routes_to_computer_use(self) -> None:
        self.assertEqual(HelplerAgent._route_rules("click that thing?"), "computer_use")

    def test_computer_use_keywords_route_to_computer_use(self) -> None:
        self.assertEqual(HelplerAgent._route_rules("scroll down a bit"), "computer_use")
        self.assertEqual(HelplerAgent._route_rules("type my name here"), "computer_use")

    def test_fast_action_phrase_routes_to_computer_use(self) -> None:
        self.assertEqual(HelplerAgent._route_rules("new tab"), "computer_use")
        self.assertEqual(HelplerAgent._route_rules("go to example.com"), "computer_use")

    def test_site_alias_routes_to_computer_use(self) -> None:
        self.assertEqual(HelplerAgent._route_rules("bring me to gmail"), "computer_use")

    def test_app_alias_routes_to_computer_use(self) -> None:
        self.assertEqual(HelplerAgent._route_rules("fire up notepad"), "computer_use")

    def test_ambiguous_message_is_ambiguous(self) -> None:
        self.assertEqual(HelplerAgent._route_rules("find me a flight to Tokyo"), "ambiguous")
        self.assertEqual(HelplerAgent._route_rules("hello"), "ambiguous")

    def test_empty_message_routes_to_chat(self) -> None:
        self.assertEqual(HelplerAgent._route_rules(""), "chat")
        self.assertEqual(HelplerAgent._route_rules("    "), "chat")


class RouteResolutionTests(unittest.TestCase):
    def _agent(self, client: FakeClient) -> HelplerAgent:
        return HelplerAgent(client=client)  # type: ignore[arg-type]

    def test_deterministic_rule_skips_classifier(self) -> None:
        client = FakeClient(classify_word="act")
        agent = self._agent(client)
        self.assertEqual(agent._resolve_route("how do I install python"), "chat")
        self.assertEqual(client.calls, [])

    def test_ambiguous_uses_classifier_when_enabled(self) -> None:
        client = FakeClient(classify_word="act")
        agent = self._agent(client)
        with patch("agent.USE_ROUTE_CLASSIFIER", True):
            self.assertEqual(agent._resolve_route("find me a flight to Tokyo"), "computer_use")
        self.assertEqual(len(client.calls), 1)

    def test_classifier_says_chat(self) -> None:
        client = FakeClient(classify_word="chat")
        agent = self._agent(client)
        with patch("agent.USE_ROUTE_CLASSIFIER", True):
            self.assertEqual(agent._resolve_route("just thinking out loud"), "chat")

    def test_classifier_disabled_defaults_to_computer_use(self) -> None:
        client = FakeClient(classify_word="chat")  # would route to chat IF called
        agent = self._agent(client)
        with patch("agent.USE_ROUTE_CLASSIFIER", False):
            self.assertEqual(agent._resolve_route("find me a flight to Tokyo"), "computer_use")
        self.assertEqual(client.calls, [])

    def test_classifier_error_defaults_to_computer_use(self) -> None:
        class BoomClient(FakeClient):
            def classify_route(self, text: str, *, model: str) -> str:  # noqa: D401
                raise RuntimeError("nope")

        agent = self._agent(BoomClient())
        with patch("agent.USE_ROUTE_CLASSIFIER", True):
            self.assertEqual(agent._resolve_route("find me a flight to Tokyo"), "computer_use")

    def test_classifier_garbage_word_defaults_to_computer_use(self) -> None:
        client = FakeClient(classify_word="banana")
        agent = self._agent(client)
        with patch("agent.USE_ROUTE_CLASSIFIER", True):
            self.assertEqual(agent._resolve_route("find me a flight to Tokyo"), "computer_use")

    def test_classifier_results_are_cached(self) -> None:
        client = FakeClient(classify_word="act")
        agent = self._agent(client)
        with patch("agent.USE_ROUTE_CLASSIFIER", True):
            agent._resolve_route("find me a flight to Tokyo")
            agent._resolve_route("Find me a flight to Tokyo")
            agent._resolve_route("FIND ME A FLIGHT TO TOKYO")
        self.assertEqual(len(client.calls), 1)

    def test_client_without_classifier_method_defaults_to_computer_use(self) -> None:
        class BareClient:
            def chat(self, *args, **kwargs):  # noqa: ANN001
                raise AssertionError

            def computer_use_step(self, *args, **kwargs):  # noqa: ANN001
                raise AssertionError

        agent = HelplerAgent(client=BareClient())  # type: ignore[arg-type]
        with patch("agent.USE_ROUTE_CLASSIFIER", True):
            self.assertEqual(agent._resolve_route("find me a flight to Tokyo"), "computer_use")


class ClassifyRouteParsingTests(unittest.TestCase):
    def test_classify_route_parses_first_word(self) -> None:
        from openai_client import OpenAIClient

        class Provider:
            def post_response(self, body):  # noqa: ANN001
                return {
                    "output": [
                        {"type": "message", "content": [{"type": "output_text", "text": "Act."}]}
                    ]
                }

        client = OpenAIClient(codex_provider=Provider())  # type: ignore[arg-type]
        self.assertEqual(client.classify_route("open gmail", model="gpt-5.5"), "act")

    def test_classify_route_returns_empty_on_provider_error(self) -> None:
        from openai_client import OpenAIClient, ProviderError

        class Provider:
            def post_response(self, body):  # noqa: ANN001
                raise ProviderError("boom")

        client = OpenAIClient(codex_provider=Provider())  # type: ignore[arg-type]
        self.assertEqual(client.classify_route("anything", model="gpt-5.5"), "")

    def test_classify_route_empty_input_short_circuits(self) -> None:
        from openai_client import OpenAIClient

        class Provider:
            def post_response(self, body):  # noqa: ANN001
                raise AssertionError("should not call provider")

        client = OpenAIClient(codex_provider=Provider())  # type: ignore[arg-type]
        self.assertEqual(client.classify_route("   ", model="gpt-5.5"), "")


if __name__ == "__main__":
    unittest.main()
