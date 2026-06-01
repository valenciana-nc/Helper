from __future__ import annotations

import argparse
import io
import json
import shutil
import time
from pathlib import Path
from typing import Any, Callable

from PIL import Image, ImageDraw

from control_inventory import ControlCandidate, collect_control_candidates
from screen import Capture, capture_active_monitor, capture_primary, capture_virtual_desktop

CaptureFn = Callable[[], Capture]


def run_probe(
    *,
    artifacts_dir: Path,
    capture_provider: CaptureFn = capture_active_monitor,
    candidates: list[ControlCandidate] | None = None,
    clean: bool = True,
    max_candidates: int = 80,
    min_candidates: int = 1,
    min_actionable_candidates: int = 1,
    candidate_retries: int = 4,
    retry_delay_sec: float = 0.3,
    candidate_timeout_ms: int = 1500,
) -> dict[str, Any]:
    if clean and artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    collection_attempts = 1
    if candidates is not None:
        capture = capture_provider()
        resolved_candidates = list(candidates)[:max_candidates]
    else:
        attempts = max(0, candidate_retries) + 1
        resolved_candidates = []
        for attempt in range(attempts):
            collection_attempts = attempt + 1
            capture = capture_provider()
            resolved_candidates = collect_control_candidates(
                capture,
                limit=max_candidates,
                timeout_ms=max(0, candidate_timeout_ms),
            )[:max_candidates]
            failures, _actionable_count = _probe_failures(
                capture,
                resolved_candidates,
                min_candidates=min_candidates,
                min_actionable_candidates=min_actionable_candidates,
            )
            if not failures or attempt == attempts - 1:
                break
            if retry_delay_sec > 0:
                time.sleep(retry_delay_sec)

    screen = Image.open(io.BytesIO(capture.png_bytes)).convert("RGB")
    screen.save(artifacts_dir / "screen.png")
    overlay = draw_candidate_overlay(capture, resolved_candidates, base=screen)
    overlay.save(artifacts_dir / "controls_overlay.png")

    summary = build_probe_summary(
        capture,
        resolved_candidates,
        min_candidates=min_candidates,
        min_actionable_candidates=min_actionable_candidates,
        collection_attempts=collection_attempts,
    )
    (artifacts_dir / "candidates.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def build_probe_summary(
    capture: Capture,
    candidates: list[ControlCandidate],
    *,
    min_candidates: int = 0,
    min_actionable_candidates: int = 0,
    collection_attempts: int = 1,
) -> dict[str, Any]:
    failures, actionable_count = _probe_failures(
        capture,
        candidates,
        min_candidates=min_candidates,
        min_actionable_candidates=min_actionable_candidates,
    )
    return {
        "capture": {
            "width": capture.width,
            "height": capture.height,
            "monitor_left": capture.monitor_left,
            "monitor_top": capture.monitor_top,
            "scale": capture.scale,
        },
        "candidate_count": len(candidates),
        "actionable_candidate_count": actionable_count,
        "min_candidates": max(0, min_candidates),
        "min_actionable_candidates": max(0, min_actionable_candidates),
        "collection_attempts": max(1, collection_attempts),
        "passed": not failures,
        "failures": failures,
        "candidates": [
            {
                "id": candidate.id,
                "text": candidate.text,
                "control_type": candidate.control_type,
                "rect": candidate.rect,
                "automation_id": candidate.automation_id,
                "window_title": candidate.window_title,
                "image_box": screen_rect_to_image_box(capture, candidate.rect),
            }
            for candidate in candidates
        ],
    }


def _probe_failures(
    capture: Capture,
    candidates: list[ControlCandidate],
    *,
    min_candidates: int,
    min_actionable_candidates: int,
) -> tuple[list[str], int]:
    failures: list[str] = []
    required_candidates = max(0, min_candidates)
    required_actionable = max(0, min_actionable_candidates)
    if len(candidates) < required_candidates:
        failures.append(
            f"candidate count {len(candidates)} below required minimum {required_candidates}"
        )
    actionable_count = sum(1 for candidate in candidates if _is_actionable_candidate(candidate, capture))
    if actionable_count < required_actionable:
        failures.append(
            "actionable candidate count "
            f"{actionable_count} below required minimum {required_actionable}"
        )
    return failures, actionable_count


def _is_actionable_candidate(candidate: ControlCandidate, capture: Capture) -> bool:
    if _looks_like_window_chrome(candidate, capture):
        return False
    return True


def _looks_like_window_chrome(candidate: ControlCandidate, capture: Capture) -> bool:
    text = " ".join((candidate.text or "").lower().split())
    automation_id = (candidate.automation_id or "").lower()
    titlebar_labels = {"close", "maximize", "minimize", "restore"}
    if text not in titlebar_labels:
        return False
    if automation_id in {"view_1", "view_2", "view_3", "view_4"}:
        return True
    _x, y, _width, height = candidate.rect
    return y <= capture.monitor_top + 48 and height <= 64


def draw_candidate_overlay(
    capture: Capture,
    candidates: list[ControlCandidate],
    *,
    base: Image.Image | None = None,
) -> Image.Image:
    image = (base.copy() if base is not None else Image.open(io.BytesIO(capture.png_bytes)).convert("RGB"))
    draw = ImageDraw.Draw(image)
    palette = ["#22c55e", "#06b6d4", "#f59e0b", "#a855f7", "#ef4444"]
    for index, candidate in enumerate(candidates):
        box = clip_box(screen_rect_to_image_box(capture, candidate.rect), image.size)
        if box is None:
            continue
        color = palette[index % len(palette)]
        draw.rectangle(box, outline=color, width=2)
        label = candidate.id
        if candidate.text:
            label = f"{label} {candidate.text[:28]}"
        draw.text((box[0] + 3, max(0, box[1] - 12)), label, fill=color)
    return image


def screen_rect_to_image_box(
    capture: Capture,
    rect: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    left = int((x - capture.monitor_left) * capture.scale)
    top = int((y - capture.monitor_top) * capture.scale)
    right = left + max(1, int(width * capture.scale))
    bottom = top + max(1, int(height * capture.scale))
    return (left, top, right, bottom)


def clip_box(
    box: tuple[int, int, int, int],
    size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = box
    width, height = size
    clipped = (max(0, left), max(0, top), min(width, right), min(height, bottom))
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        return None
    return clipped


def _capture_provider(kind: str) -> CaptureFn:
    if kind == "primary":
        return capture_primary
    if kind == "virtual":
        return capture_virtual_desktop
    return capture_active_monitor


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture the desktop and draw UIA Help-target candidates for live QA."
    )
    parser.add_argument(
        "--capture",
        choices=("active", "primary", "virtual"),
        default="active",
    )
    parser.add_argument("--artifacts", type=Path, default=Path("logs/help_live_probe/latest"))
    parser.add_argument("--max-candidates", type=int, default=80)
    parser.add_argument("--min-candidates", type=int, default=1)
    parser.add_argument("--min-actionable-candidates", type=int, default=1)
    parser.add_argument("--candidate-retries", type=int, default=4)
    parser.add_argument("--retry-delay-sec", type=float, default=0.3)
    parser.add_argument("--candidate-timeout-ms", type=int, default=1500)
    args = parser.parse_args(argv)

    summary = run_probe(
        artifacts_dir=args.artifacts,
        capture_provider=_capture_provider(args.capture),
        max_candidates=args.max_candidates,
        min_candidates=args.min_candidates,
        min_actionable_candidates=args.min_actionable_candidates,
        candidate_retries=args.candidate_retries,
        retry_delay_sec=args.retry_delay_sec,
        candidate_timeout_ms=args.candidate_timeout_ms,
    )
    print(
        "Help live probe: "
        f"{summary['candidate_count']} candidates "
        f"({summary['actionable_candidate_count']} actionable), "
        f"{summary['collection_attempts']} collection attempt(s), "
        f"{summary['capture']['width']}x{summary['capture']['height']} "
        f"scale={summary['capture']['scale']:.3f}; "
        f"artifacts={args.artifacts}"
    )
    for failure in summary.get("failures", []):
        print(f"- {failure}")
    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
