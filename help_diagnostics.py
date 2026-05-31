from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from config import LOGS_DIR, bool_env

log = logging.getLogger("helper.help_diagnostics")

HELP_TARGET_DIAGNOSTICS = bool_env("HELPER_HELP_TARGET_DIAGNOSTICS", True)
HELP_TARGET_LOG = LOGS_DIR / "help_targets.jsonl"


class HelpTargetDiagnosticSink:
    def __init__(
        self,
        *,
        path: Path = HELP_TARGET_LOG,
        enabled: bool = HELP_TARGET_DIAGNOSTICS,
    ) -> None:
        self.path = path
        self.enabled = enabled

    def write(self, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        enriched = {
            "timestamp": time.time(),
            **payload,
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(enriched, sort_keys=True, ensure_ascii=True))
                fh.write("\n")
        except Exception:
            log.exception("Could not write Help target diagnostic.")

