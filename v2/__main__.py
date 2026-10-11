"""Helper-E v2 entry point: `python -m v2 "open notepad and type hello"`.

Runs one goal through the v2 engine (world model → plan → closed-loop
execute → verify) using v1's provider stack for the LLM.
"""
from __future__ import annotations

import logging
import sys

log = logging.getLogger("helper.v2")


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(levelname)s %(message)s")
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print('usage: python -m v2 "your goal here"', file=sys.stderr)
        return 2
    goal = " ".join(args)

    from .actuator import V2Actuator
    from .agent import HelperV2
    from .llm import V2LlmAdapter

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
