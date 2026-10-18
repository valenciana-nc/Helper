"""CLI for the same runtime used by the desktop application."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import asdict
from pathlib import Path
from threading import Event


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Helper computer-use engine")
    parser.add_argument("goal", nargs="+")
    parser.add_argument("--mode", choices=("help", "active"), default="help")
    parser.add_argument("--backend", choices=("desktop", "browser", "hybrid"), default=None)
    parser.add_argument("--browser-endpoint", default=None)
    parser.add_argument("--page-index", type=int)
    parser.add_argument("--headless", action="store_true")
    parser.add_argument("--model")
    parser.add_argument("--trace", type=Path)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    from .bootstrap import build_agent
    from .journal import JsonJournal
    aborted = Event()
    journal = JsonJournal(args.trace) if args.trace else None

    def confirm(action, observation, reason):
        if not sys.stdin.isatty():
            return False
        answer = input(f"{reason}\n{action.description or action.kind} in {observation.title}\nAllow? [y/N] ")
        return answer.strip().casefold() in {"y", "yes"}

    def event(name, payload):
        if journal:
            journal(name, payload)
        if name in {"decision", "guided", "goal_verification"} and payload.get("message"):
            print(payload["message"], flush=True)

    try:
        agent = build_agent(confirm=confirm, model_name=args.model, backend=args.backend,
                            endpoint=args.browser_endpoint, page_index=args.page_index, headless=args.headless)
        report = agent.run_goal(" ".join(args.goal), mode=args.mode, on_event=event, should_abort=aborted.is_set)
        print(json.dumps({"status": report.status, "message": report.message,
                          "metrics": asdict(report.metrics)}, ensure_ascii=True))
        return 0 if report.ok else 1
    except KeyboardInterrupt:
        aborted.set()
        return 130
    finally:
        if journal:
            journal.close()


if __name__ == "__main__":
    raise SystemExit(main())
