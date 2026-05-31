from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw
from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QApplication,
    QCheckBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from agent import _parse_live_help_decision
from control_inventory import ControlCandidate, collect_control_candidates
from help_live_probe import draw_candidate_overlay, screen_rect_to_image_box
from help_session import build_target_diagnostic, clip_resolution_to_capture, resolve_help_target
from rect_snap import SnapResult
from screen import Capture, capture_active_monitor
from target_quality import evaluate_target_quality

WINDOW_TITLE_PREFIX = "Helper Precision Self Test"
TARGET_TEXT = "Save changes"
CHILD_READY_SETTLE_SEC = 0.25
TARGET_WAIT_TIMEOUT_SEC = 5.0


def run_selftest(
    *,
    artifacts_dir: Path,
    clean: bool = True,
    settle_sec: float = 0.8,
) -> dict[str, Any]:
    if clean and artifacts_dir.exists():
        shutil.rmtree(artifacts_dir)
    artifacts_dir.mkdir(parents=True, exist_ok=True)

    title = f"{WINDOW_TITLE_PREFIX} {os.getpid()} {uuid.uuid4().hex[:6]}"
    child = _start_child_window(title)
    try:
        capture, candidates, target_candidate = _wait_for_target_candidate(
            title=title,
            timeout_sec=max(TARGET_WAIT_TIMEOUT_SEC, settle_sec),
        )
        if target_candidate is None:
            summary = _failure_summary("target candidate not found", capture, candidates)
            _write_artifacts(
                artifacts_dir,
                capture,
                candidates,
                summary=summary,
                manifest=_manifest(title),
            )
            return summary

        decision = _decision_for_candidate(capture, target_candidate)
        target = resolve_help_target(
            decision,
            capture,
            candidates,
            snapper=lambda rect, _instruction: SnapResult(rect=rect, confidence=0.0, source="model"),
            clip_to_capture=False,
        )
        quality = None
        overlay_rect = None
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
        passed, failures = evaluate_selftest_result(
            target_candidate=target_candidate,
            overlay_rect=overlay_rect,
            rejected_reason=rejected_reason,
        )
        summary = {
            "passed": passed,
            "failures": failures,
            "target_candidate": _candidate_payload(target_candidate),
            "diagnostic": diagnostic,
        }
        _write_artifacts(
            artifacts_dir,
            capture,
            candidates,
            summary=summary,
            diagnostic=diagnostic,
            target_candidate=target_candidate,
            overlay_rect=overlay_rect,
            manifest=_manifest(title),
        )
        return summary
    finally:
        _stop_child_window(child)


def evaluate_selftest_result(
    *,
    target_candidate: ControlCandidate,
    overlay_rect: tuple[int, int, int, int] | None,
    rejected_reason: str,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if rejected_reason:
        failures.append(f"target rejected: {rejected_reason}")
    if overlay_rect is None:
        failures.append("overlay rect was not emitted")
    else:
        iou = _iou(target_candidate.rect, overlay_rect)
        if iou < 0.85:
            failures.append(f"overlay/candidate IoU too low: {iou:.3f}")
    return not failures, failures


def _start_child_window(title: str) -> subprocess.Popen:
    return subprocess.Popen(
        [sys.executable, "-m", "help_precision_selftest", "--child-window", title],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        cwd=str(Path(__file__).parent),
    )


def _stop_child_window(child: subprocess.Popen) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait(timeout=2.0)


def run_child_window(title: str) -> int:
    app = QApplication.instance() or QApplication(["help_precision_selftest_child"])
    window = _build_window(title)
    window.show()
    window.raise_()
    window.activateWindow()
    _process_events(app, CHILD_READY_SETTLE_SEC)
    return app.exec()


def _build_window(title: str) -> QWidget:
    window = QWidget()
    window.setWindowTitle(title)
    window.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, True)
    window.setGeometry(120, 120, 520, 260)
    layout = QVBoxLayout(window)
    label = QLabel("Helper precision self-test")
    layout.addWidget(label)

    edit = QLineEdit()
    edit.setObjectName("helperPrecisionName")
    edit.setAccessibleName("Project name")
    edit.setText("Precision sample")
    layout.addWidget(edit)

    checkbox = QCheckBox("Enable precision mode")
    checkbox.setObjectName("helperPrecisionCheckbox")
    checkbox.setAccessibleName("Enable precision mode")
    layout.addWidget(checkbox)

    row = QHBoxLayout()
    save = QPushButton(TARGET_TEXT)
    save.setObjectName("helperPrecisionSave")
    save.setAccessibleName(TARGET_TEXT)
    cancel = QPushButton("Cancel")
    cancel.setObjectName("helperPrecisionCancel")
    cancel.setAccessibleName("Cancel")
    row.addWidget(save)
    row.addWidget(cancel)
    layout.addLayout(row)

    duplicate_row = QHBoxLayout()
    first_duplicate = QPushButton("Duplicate")
    first_duplicate.setObjectName("helperPrecisionDuplicateA")
    first_duplicate.setAccessibleName("Duplicate")
    second_duplicate = QPushButton("Duplicate")
    second_duplicate.setObjectName("helperPrecisionDuplicateB")
    second_duplicate.setAccessibleName("Duplicate")
    duplicate_row.addWidget(first_duplicate)
    duplicate_row.addWidget(second_duplicate)
    layout.addLayout(duplicate_row)

    icon_row = QHBoxLayout()
    icon_a = QPushButton("")
    icon_a.setObjectName("helperPrecisionIconA")
    icon_b = QPushButton("")
    icon_b.setObjectName("helperPrecisionIconB")
    icon_row.addWidget(icon_a)
    icon_row.addWidget(icon_b)
    layout.addLayout(icon_row)
    return window


def _wait_for_target_candidate(
    *,
    title: str,
    timeout_sec: float,
) -> tuple[Capture, list[ControlCandidate], ControlCandidate | None]:
    deadline = time.monotonic() + timeout_sec
    last: tuple[Capture, list[ControlCandidate], ControlCandidate | None] | None = None
    stable_rect: tuple[int, int, int, int] | None = None
    while True:
        capture = capture_active_monitor()
        candidates = collect_control_candidates(capture, timeout_ms=1500, limit=160)
        target = _find_target_candidate(candidates, title=title)
        last = (capture, candidates, target)
        if target is not None:
            if stable_rect == target.rect:
                return capture, candidates, target
            stable_rect = target.rect
        if time.monotonic() >= deadline:
            return last
        time.sleep(0.15)


def _decision_for_candidate(capture: Capture, candidate: ControlCandidate):
    norm = _norm_rect(candidate.rect, capture)
    # Deliberately drift the model rectangle a little; the resolver should trust
    # the UIA candidate rect over approximate vision geometry.
    payload = {
        "kind": "step",
        "instruction": f"Click {TARGET_TEXT}.",
        "target_id": candidate.id,
        "target": {
            "x": max(0, norm[0] - 8),
            "y": max(0, norm[1] - 8),
            "width": min(1000, norm[2] + 16),
            "height": min(1000, norm[3] + 16),
        },
        "expected_change": "The self-test accepts the known Save button.",
    }
    return _parse_live_help_decision(json.dumps(payload))


def _norm_rect(
    rect: tuple[int, int, int, int],
    capture: Capture,
) -> tuple[int, int, int, int]:
    x, y, width, height = rect
    left = int((x - capture.monitor_left) * capture.scale / max(1, capture.width) * 1000)
    top = int((y - capture.monitor_top) * capture.scale / max(1, capture.height) * 1000)
    norm_width = int(width * capture.scale / max(1, capture.width) * 1000)
    norm_height = int(height * capture.scale / max(1, capture.height) * 1000)
    return (
        max(0, min(1000, left)),
        max(0, min(1000, top)),
        max(1, min(1000, norm_width)),
        max(1, min(1000, norm_height)),
    )


def _find_target_candidate(
    candidates: list[ControlCandidate],
    *,
    title: str = "",
) -> ControlCandidate | None:
    for candidate in candidates:
        if title and title not in candidate.window_title:
            continue
        if TARGET_TEXT.lower() in candidate.descriptor.lower():
            return candidate
    return None


def _write_artifacts(
    artifacts_dir: Path,
    capture: Capture,
    candidates: list[ControlCandidate],
    *,
    summary: dict[str, Any],
    diagnostic: dict[str, Any] | None = None,
    target_candidate: ControlCandidate | None = None,
    overlay_rect: tuple[int, int, int, int] | None = None,
    manifest: dict[str, Any] | None = None,
) -> None:
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    image = Image.open(io.BytesIO(capture.png_bytes)).convert("RGB")
    image.save(artifacts_dir / "screen.png")
    draw_candidate_overlay(capture, candidates, base=image).save(artifacts_dir / "controls_overlay.png")
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    if target_candidate is not None:
        draw.rectangle(screen_rect_to_image_box(capture, target_candidate.rect), outline="#22c55e", width=3)
    if overlay_rect is not None:
        draw.rectangle(screen_rect_to_image_box(capture, overlay_rect), outline="#ef4444", width=2)
    overlay.save(artifacts_dir / "selftest_overlay.png")
    (artifacts_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    (artifacts_dir / "candidates.json").write_text(
        json.dumps(
            {
                "candidate_count": len(candidates),
                "candidates": [_candidate_payload(candidate) for candidate in candidates],
            },
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    if manifest is not None:
        (artifacts_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    if diagnostic is not None:
        (artifacts_dir / "diagnostic.json").write_text(
            json.dumps(diagnostic, indent=2, sort_keys=True),
            encoding="utf-8",
        )
    crop_rect = overlay_rect or (target_candidate.rect if target_candidate else None)
    if crop_rect is not None:
        box = _clip_box(screen_rect_to_image_box(capture, crop_rect), image.size)
        if box is not None:
            image.crop(box).save(artifacts_dir / "target_crop.png")


def _failure_summary(
    reason: str,
    capture: Capture,
    candidates: list[ControlCandidate],
) -> dict[str, Any]:
    return {
        "passed": False,
        "failures": [reason],
        "capture": {
            "width": capture.width,
            "height": capture.height,
            "monitor_left": capture.monitor_left,
            "monitor_top": capture.monitor_top,
            "scale": capture.scale,
        },
        "candidate_count": len(candidates),
        "candidates": [_candidate_payload(candidate) for candidate in candidates[:20]],
    }


def _candidate_payload(candidate: ControlCandidate) -> dict[str, Any]:
    return {
        "id": candidate.id,
        "text": candidate.text,
        "control_type": candidate.control_type,
        "rect": candidate.rect,
        "automation_id": candidate.automation_id,
        "window_title": candidate.window_title,
    }


def _manifest(title: str) -> dict[str, Any]:
    return {
        "window_title": title,
        "expected_controls": [
            {"text": TARGET_TEXT, "automation_id": "helperPrecisionSave", "role": "button", "required": True},
            {"text": "Cancel", "automation_id": "helperPrecisionCancel", "role": "button", "required": False},
            {"text": "Project name", "automation_id": "helperPrecisionName", "role": "edit", "required": False},
            {"text": "Enable precision mode", "automation_id": "helperPrecisionCheckbox", "role": "checkbox", "required": False},
            {"text": "Duplicate", "automation_id": "helperPrecisionDuplicateA", "role": "button", "required": False},
            {"text": "Duplicate", "automation_id": "helperPrecisionDuplicateB", "role": "button", "required": False},
            {"text": "", "automation_id": "helperPrecisionIconA", "role": "button", "required": False},
            {"text": "", "automation_id": "helperPrecisionIconB", "role": "button", "required": False},
        ],
    }


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


def _process_events(app: QApplication, seconds: float) -> None:
    deadline = time.monotonic() + max(0.0, seconds)
    while True:
        app.processEvents()
        if time.monotonic() >= deadline:
            return
        time.sleep(0.02)


def _iou(
    a: tuple[int, int, int, int],
    b: tuple[int, int, int, int],
) -> float:
    ax1, ay1, aw, ah = a
    bx1, by1, bw, bh = b
    ax2, ay2 = ax1 + aw, ay1 + ah
    bx2, by2 = bx1 + bw, by1 + bh
    ix1, iy1 = max(ax1, bx1), max(ay1, by1)
    ix2, iy2 = min(ax2, bx2), min(ay2, by2)
    iw = max(0, ix2 - ix1)
    ih = max(0, iy2 - iy1)
    inter = iw * ih
    union = aw * ah + bw * bh - inter
    if union <= 0:
        return 0.0
    return inter / union


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a live Help-mode precision self-test.")
    parser.add_argument("--artifacts", type=Path, default=Path("logs/help_precision_selftest/latest"))
    parser.add_argument("--child-window", default="")
    args = parser.parse_args(argv)
    if args.child_window:
        return run_child_window(args.child_window)
    summary = run_selftest(artifacts_dir=args.artifacts)
    print(
        "Help precision self-test: "
        f"{'passed' if summary.get('passed') else 'failed'}; "
        f"artifacts={args.artifacts}"
    )
    for failure in summary.get("failures", []):
        print(f"- {failure}")
    return 0 if summary.get("passed") else 1


if __name__ == "__main__":
    raise SystemExit(main())
