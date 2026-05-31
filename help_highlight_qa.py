from __future__ import annotations

import argparse
import io
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from agent import _parse_live_help_decision
from control_inventory import ControlCandidate, TargetResolution
from help_session import (
    build_target_diagnostic,
    clip_resolution_to_capture,
    resolve_help_target,
)
from rect_snap import SnapResult
from screen import Capture
from target_quality import TargetQuality, evaluate_target_quality


@dataclass(frozen=True)
class ScenarioResult:
    name: str
    passed: bool
    failures: tuple[str, ...]
    diagnostic: dict[str, Any]


def builtin_scenarios() -> list[dict[str, Any]]:
    return [
        {
            "name": "target_id_uses_candidate_rect",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "target": {"x": 120, "y": 220, "width": 220, "height": 120},
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "button", "rect": [80, 80, 80, 32]},
            ],
            "expected": {"source": "target_id", "rect": [80, 80, 80, 32], "overlay_emitted": True},
        },
        {
            "name": "unknown_id_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c999",
                "target": {"x": 300, "y": 200, "width": 80, "height": 40},
            },
            "candidates": [
                {"id": "c001", "text": "Cancel", "control_type": "button", "rect": [80, 80, 80, 32]},
            ],
            "expected": {"source": "target_id", "rejected_reason": "unknown target_id", "overlay_emitted": False},
        },
        {
            "name": "blank_model_rect_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "decision": {
                "kind": "step",
                "instruction": "Click this button.",
                "target": {"x": 100, "y": 100, "width": 80, "height": 50},
            },
            "candidates": [],
            "expected": {"source": "model", "quality_reason": "target appears visually empty", "overlay_emitted": False},
        },
    ]


def load_scenarios(fixtures: Path | None) -> list[dict[str, Any]]:
    if fixtures is None:
        return builtin_scenarios()
    scenarios: list[dict[str, Any]] = []
    for path in sorted(fixtures.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        data.setdefault("name", path.stem)
        scenarios.append(data)
    return scenarios


def run_scenarios(
    scenarios: list[dict[str, Any]],
    *,
    artifacts_dir: Path,
    clean: bool = True,
) -> list[ScenarioResult]:
    if clean and artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    results = [_run_one(scenario, artifacts_dir) for scenario in scenarios]
    summary = {
        "passed": sum(1 for result in results if result.passed),
        "failed": sum(1 for result in results if not result.passed),
        "results": [
            {
                "name": result.name,
                "passed": result.passed,
                "failures": list(result.failures),
            }
            for result in results
        ],
    }
    (artifacts_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return results


def _run_one(scenario: dict[str, Any], artifacts_dir: Path) -> ScenarioResult:
    name = str(scenario.get("name") or "scenario")
    capture = _make_capture(scenario)
    candidates = [_candidate(item) for item in scenario.get("candidates", [])]
    decision = _parse_live_help_decision(json.dumps(scenario.get("decision") or {}))
    target = resolve_help_target(
        decision,
        capture,
        candidates,
        snapper=lambda rect, _instruction: SnapResult(rect=rect, confidence=0.0, source="model"),
        clip_to_capture=False,
    )
    quality: TargetQuality | None = None
    overlay_rect: tuple[int, int, int, int] | None = None
    rejected_reason = target.rejected_reason
    display_target = target
    if not rejected_reason:
        quality = evaluate_target_quality(
            capture=capture,
            rect=target.rect,
            source=target.source,
            confidence=target.confidence,
        )
        if not quality.accepted:
            rejected_reason = quality.reason
        else:
            display_target = clip_resolution_to_capture(target, capture)
            rejected_reason = display_target.rejected_reason
            if not rejected_reason:
                overlay_rect = display_target.rect

    diagnostic = build_target_diagnostic(
        decision=decision,
        capture=capture,
        candidates=candidates,
        target=display_target,
        quality=quality,
        overlay_rect=overlay_rect,
        rejected_reason=rejected_reason,
    )
    failures = tuple(_check_expectations(scenario.get("expected") or {}, diagnostic))
    if failures:
        _write_failure_artifacts(
            artifacts_dir / name,
            capture=capture,
            candidates=candidates,
            diagnostic=diagnostic,
            failures=failures,
        )
    return ScenarioResult(
        name=name,
        passed=not failures,
        failures=failures,
        diagnostic=diagnostic,
    )


def _check_expectations(expected: dict[str, Any], diagnostic: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    resolution = diagnostic["resolution"]
    overlay = diagnostic["overlay"]
    quality = diagnostic.get("quality") or {}
    checks = {
        "source": resolution.get("source"),
        "target_id": resolution.get("target_id"),
        "rejected_reason": overlay.get("rejected_reason"),
        "quality_reason": quality.get("reason"),
        "overlay_emitted": overlay.get("emitted"),
    }
    for key, actual in checks.items():
        if key in expected and actual != expected[key]:
            failures.append(f"{key}: expected {expected[key]!r}, got {actual!r}")
    if "rect" in expected:
        actual_rect = list(overlay.get("rect") or resolution.get("rect") or [])
        if actual_rect != list(expected["rect"]):
            failures.append(f"rect: expected {expected['rect']!r}, got {actual_rect!r}")
    return failures


def _write_failure_artifacts(
    out_dir: Path,
    *,
    capture: Capture,
    candidates: list[ControlCandidate],
    diagnostic: dict[str, Any],
    failures: tuple[str, ...],
) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    image = Image.open(io.BytesIO(capture.png_bytes)).convert("RGB")
    image.save(out_dir / "screen.png")
    (out_dir / "diagnostic.json").write_text(
        json.dumps(diagnostic, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (out_dir / "summary.txt").write_text("\n".join(failures), encoding="utf-8")

    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    for candidate in candidates:
        draw.rectangle(_screen_to_image_box(capture, candidate.rect), outline="#64748b", width=1)
    model_rect = diagnostic["model"].get("screen_rect")
    if model_rect:
        draw.rectangle(_screen_to_image_box(capture, tuple(model_rect)), outline="#ef4444", width=2)
    resolved_rect = diagnostic["resolution"].get("rect")
    if resolved_rect:
        draw.rectangle(_screen_to_image_box(capture, tuple(resolved_rect)), outline="#f59e0b", width=2)
    overlay_rect = diagnostic["overlay"].get("rect")
    if overlay_rect:
        draw.rectangle(_screen_to_image_box(capture, tuple(overlay_rect)), outline="#22c55e", width=3)
    overlay.save(out_dir / "overlay.png")

    crop_rect = overlay_rect or resolved_rect or model_rect
    if crop_rect:
        box = _clip_box(_screen_to_image_box(capture, tuple(crop_rect)), image.size)
        if box is not None:
            image.crop(box).save(out_dir / "crop.png")


def _make_capture(scenario: dict[str, Any]) -> Capture:
    spec = scenario.get("capture") or {}
    width = int(spec.get("width", 500))
    height = int(spec.get("height", 320))
    monitor_left = int(spec.get("monitor_left", 0))
    monitor_top = int(spec.get("monitor_top", 0))
    scale = float(spec.get("scale", 1.0))
    img = Image.new("RGB", (width, height), spec.get("background", "white"))
    draw = ImageDraw.Draw(img)
    for item in scenario.get("draw", []):
        rect = tuple(int(v) for v in item.get("rect", (0, 0, 0, 0)))
        box = _screen_to_image_box(
            Capture(b"", width, height, monitor_left, monitor_top, scale),
            rect,
        )
        draw.rectangle(box, outline="black", fill=item.get("fill", "#f8fafc"), width=1)
        label = str(item.get("label") or "")
        if label:
            draw.text((box[0] + 6, box[1] + 8), label, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Capture(
        png_bytes=buf.getvalue(),
        width=width,
        height=height,
        monitor_left=monitor_left,
        monitor_top=monitor_top,
        scale=scale,
    )


def _candidate(item: dict[str, Any]) -> ControlCandidate:
    return ControlCandidate(
        id=str(item.get("id") or ""),
        text=str(item.get("text") or ""),
        control_type=str(item.get("control_type") or "button"),
        rect=tuple(int(v) for v in item.get("rect", (0, 0, 0, 0))),
        automation_id=str(item.get("automation_id") or ""),
        window_title=str(item.get("window_title") or ""),
    )


def _screen_to_image_box(
    capture: Capture,
    rect: tuple[int, int, int, int],
) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    left = int((x - capture.monitor_left) * capture.scale)
    top = int((y - capture.monitor_top) * capture.scale)
    right = left + max(1, int(width * capture.scale))
    bottom = top + max(1, int(height * capture.scale))
    return (left, top, right, bottom)


def _clip_box(
    box: tuple[int, int, int, int],
    size: tuple[int, int],
) -> tuple[int, int, int, int] | None:
    left, top, right, bottom = box
    width, height = size
    clipped = (max(0, left), max(0, top), min(width, right), min(height, bottom))
    if clipped[2] <= clipped[0] or clipped[3] <= clipped[1]:
        return None
    return clipped


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run model-free Help highlight QA scenarios.")
    parser.add_argument("--fixtures", type=Path, default=None)
    parser.add_argument("--artifacts", type=Path, default=Path("logs/help_qa/latest"))
    args = parser.parse_args(argv)

    results = run_scenarios(load_scenarios(args.fixtures), artifacts_dir=args.artifacts)
    failed = [result for result in results if not result.passed]
    print(f"Help highlight QA: {len(results) - len(failed)} passed, {len(failed)} failed")
    for result in failed:
        print(f"- {result.name}: {'; '.join(result.failures)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())

