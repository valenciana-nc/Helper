"""Helper-E v2 entry point: `python -m v2 "open notepad and type hello"`.

Builds a real LlmPort from v1's provider stack and a real Actuator
(UIA patterns on Windows, synthetic fallback elsewhere), then runs one goal.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("helper.v2")


class V2LlmAdapter:
    """Adapt v1's OpenAIClient.chat() to the v2 LlmPort protocol."""

    def __init__(self, model: str = "gpt-5.5") -> None:
        from openai_client import make_openai_client

        self._client = make_openai_client()
        self._model = model

    def chat(self, messages: list[dict], *, system: str | None = None,
             max_tokens: int = 1024) -> str:
        result = self._client.chat(
            messages,
            model=self._model,
            system_prompt=system,
            max_tokens=max_tokens,
        )
        return result.text or ""


class V2Actuator:
    """Actuator: UIA element patterns first, synthetic input as fallback."""

    def __init__(self) -> None:
        self._use_uia = False
        try:
            import comtypes.client  # noqa: F401
            import sys as _sys
            self._use_uia = _sys.platform == "win32"
        except Exception:
            self._use_uia = False

    # -- element patterns (no cursor movement, DPI-proof) --

    def click_element(self, el) -> bool:
        if self._use_uia:
            try:
                return self._uia_invoke(el)
            except Exception as exc:
                log.debug("UIA invoke failed: %r", exc)
                return False
        return False

    def set_value(self, el, text: str) -> bool:
        if self._use_uia:
            try:
                return self._uia_set_value(el, text)
            except Exception as exc:
                log.debug("UIA set_value failed: %r", exc)
                return False
        return False

    # -- synthetic fallback --

    def click_at(self, x: int, y: int) -> None:
        import pyautogui
        pyautogui.click(x, y)

    def type_text(self, text: str) -> None:
        import pyautogui
        pyautogui.write(text)

    def press_key(self, key: str) -> None:
        import pyautogui
        pyautogui.press(key)

    # -- UIA internals (Windows only) --

    def _uia_invoke(self, el) -> bool:
        # Best-effort: locate via pywinauto and use InvokePattern.
        from pywinauto import Desktop
        pid, runtime = el.id.split(":", 1)
        for win in Desktop(backend="uia").windows():
            try:
                if str(win.process_id()) != pid:
                    continue
                for desc in win.descendants():
                    try:
                        if str(desc.element_info.runtime_id) == runtime:
                            desc.invoke()
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False

    def _uia_set_value(self, el, text: str) -> bool:
        from pywinauto import Desktop
        pid, runtime = el.id.split(":", 1)
        for win in Desktop(backend="uia").windows():
            try:
                if str(win.process_id()) != pid:
                    continue
                for desc in win.descendants():
                    try:
                        if str(desc.element_info.runtime_id) == runtime:
                            desc.set_edit_text(text)
                            return True
                    except Exception:
                        continue
            except Exception:
                continue
        return False


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print('usage: python -m v2 "your goal here"', file=sys.stderr)
        return 2
    goal = " ".join(args)

    from .agent import HelperV2

    agent = HelperV2(llm=V2LlmAdapter(), actuator=V2Actuator())

    def on_event(name: str, payload: dict) -> None:
        if name == "plan":
            print(f"[plan] {len(payload['subgoals'])} subgoals")
            for s in payload["subgoals"]:
                print(f"  - {s}")
        elif name == "subgoal_start":
            print(f"[step {payload['id']}] {payload['description']}")
        elif name == "subgoal_done":
            print(f"  ✓ {payload['id']} verified")
        elif name == "subgoal_failed":
            print(f"  ✗ {payload['id']}: {payload['detail']}")
        elif name == "replan":
            print(f"[replan] after {payload['after']}")
        elif name == "finished":
            r = payload["report"]
            print(f"[done] ok={r['ok']} done={r['done']} failed={r['failed']}")

    report = agent.run_goal(goal, on_event=on_event)
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
