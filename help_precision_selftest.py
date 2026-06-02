from __future__ import annotations

import argparse
import ctypes
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
from control_inventory import ControlCandidate, MAX_CANDIDATES, collect_control_candidates
from help_live_probe import draw_candidate_overlay, screen_rect_to_image_box
from help_session import (
    build_target_diagnostic,
    clip_resolution_to_capture,
    resolve_help_target,
    target_control_type_for_resolution,
)
from rect_snap import SnapResult
from screen import Capture, capture_active_monitor
from target_quality import evaluate_target_quality

WINDOW_TITLE_PREFIX = "Helper Precision Self Test"
TARGET_TEXT = "Save helper precision"
CHILD_READY_SETTLE_SEC = 0.25
TARGET_WAIT_TIMEOUT_SEC = 5.0
SELFTEST_CANDIDATE_LIMIT = max(MAX_CANDIDATES, 200)


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

        cases = _run_resolution_cases(
            capture=capture,
            candidates=candidates,
            target_candidate=target_candidate,
            title=title,
        )
        primary = cases[0]
        passed = all(case["passed"] for case in cases)
        failures = [
            f"{case['name']}: {failure}"
            for case in cases
            for failure in case["failures"]
        ]
        summary = {
            "passed": passed,
            "failures": failures,
            "cases": cases,
            "target_candidate": _candidate_payload(target_candidate),
        }
        _write_artifacts(
            artifacts_dir,
            capture,
            candidates,
            summary=summary,
            diagnostic=primary.get("diagnostic"),
            target_candidate=target_candidate,
            overlay_rect=primary.get("overlay_rect"),
            manifest=_manifest(title),
        )
        _write_case_artifacts(artifacts_dir, capture, candidates, cases)
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


def evaluate_case_result(
    *,
    expected_candidate: ControlCandidate | None,
    overlay_rect: tuple[int, int, int, int] | None,
    rejected_reason: str,
    expect_rejected_reason: str = "",
    allow_any_rejection: bool = False,
    expect_overlay: bool = True,
    min_iou: float = 0.85,
) -> tuple[bool, list[str]]:
    failures: list[str] = []
    if expect_rejected_reason:
        if rejected_reason != expect_rejected_reason:
            failures.append(
                f"expected rejection {expect_rejected_reason!r}, got {rejected_reason!r}"
            )
    elif allow_any_rejection:
        if not rejected_reason:
            failures.append("expected target rejection")
    elif rejected_reason:
        failures.append(f"target rejected: {rejected_reason}")
    if expect_overlay and overlay_rect is None:
        failures.append("overlay rect was not emitted")
    if not expect_overlay and overlay_rect is not None:
        failures.append(f"overlay rect was emitted unexpectedly: {overlay_rect}")
    if expected_candidate is not None and overlay_rect is not None:
        iou = _iou(expected_candidate.rect, overlay_rect)
        if iou < min_iou:
            failures.append(f"overlay/candidate IoU too low: {iou:.3f}")
    return not failures, failures


def _run_resolution_cases(
    *,
    capture: Capture,
    candidates: list[ControlCandidate],
    target_candidate: ControlCandidate,
    title: str,
) -> list[dict[str, Any]]:
    duplicate_candidates = _find_candidates_by_text(candidates, "Duplicate", title=title)
    cancel_candidates = _find_candidates_by_text(candidates, "Cancel", title=title)
    icon_candidates = _find_candidates_by_automation_ids(
        candidates,
        {"helperPrecisionIconA", "helperPrecisionIconB"},
        title=title,
    )
    cases = [
        _run_resolution_case(
            name="save_target_id_uses_candidate_rect",
            decision=_decision_for_candidate(capture, target_candidate),
            capture=capture,
            candidates=candidates,
            expected_candidate=target_candidate,
        ),
        _run_resolution_case(
            name="unknown_target_id_rejects",
            decision=_decision_unknown_id(capture, target_candidate),
            capture=capture,
            candidates=candidates,
            expected_candidate=None,
            expect_overlay=False,
            expect_rejected_reason="unknown target_id",
        ),
        _run_resolution_case(
            name="model_rect_snaps_to_candidate_snapshot",
            decision=_decision_model_rect_only(capture, target_candidate),
            capture=capture,
            candidates=candidates,
            expected_candidate=target_candidate,
        ),
    ]
    if cancel_candidates:
        cancel_candidate = cancel_candidates[0]
        cases.append(
            _run_resolution_case(
                name="wrong_target_id_recovers_by_text_match",
                decision=_decision_wrong_target_id(cancel_candidate),
                capture=capture,
                candidates=candidates,
                expected_candidate=target_candidate,
            )
        )
        cases.append(
            _run_resolution_case(
                name="copied_wrong_target_id_without_semantic_alternative_rejects",
                decision=_decision_copied_wrong_target_id(capture, cancel_candidate),
                capture=capture,
                candidates=[cancel_candidate],
                expected_candidate=None,
                expect_overlay=False,
                expect_rejected_reason="target_id semantic mismatch",
            )
        )
        cases.append(
            _run_resolution_case(
                name="no_candidate_compound_model_rect_rejects",
                decision=_decision_compound_model_rect(capture, [target_candidate, cancel_candidate]),
                capture=capture,
                candidates=[],
                expected_candidate=None,
                expect_overlay=False,
                allow_any_rejection=True,
            )
        )
    else:
        cases.append(
            _missing_case(
                "wrong_target_id_recovers_by_text_match",
                "cancel control not found",
            )
        )
        cases.append(
            _missing_case(
                "copied_wrong_target_id_without_semantic_alternative_rejects",
                "cancel control not found",
            )
        )
        cases.append(
            _missing_case(
                "no_candidate_compound_model_rect_rejects",
                "cancel control not found",
            )
        )
    if len(duplicate_candidates) >= 2:
        cases.append(
            _run_resolution_case(
                name="duplicate_label_without_geometry_rejects",
                decision=_decision_duplicate_without_rect(duplicate_candidates[0]),
                capture=capture,
                candidates=candidates,
                expected_candidate=None,
                expect_overlay=False,
                expect_rejected_reason="target_id ambiguous",
            )
        )
        cases.append(
            _run_resolution_case(
                name="duplicate_model_rect_ambiguous_candidate_snap_rejects",
                decision=_decision_duplicate_ambiguous_snap(capture, duplicate_candidates[:2]),
                capture=capture,
                candidates=candidates,
                expected_candidate=None,
                expect_overlay=False,
                expect_rejected_reason="ambiguous candidate snap",
            )
        )
    else:
        cases.append(_missing_case("duplicate_label_without_geometry_rejects", "duplicate controls not found"))
        cases.append(_missing_case("duplicate_model_rect_ambiguous_candidate_snap_rejects", "duplicate controls not found"))

    if len(icon_candidates) >= 2:
        cases.append(
            _run_resolution_case(
                name="icon_only_target_id_ambiguous_unlabeled_rejects",
                decision=_decision_icon_target_id(icon_candidates[0]),
                capture=capture,
                candidates=candidates,
                expected_candidate=None,
                expect_overlay=False,
                expect_rejected_reason="target_id ambiguous unlabeled control",
            )
        )
    else:
        cases.append(_missing_case("icon_only_target_id_ambiguous_unlabeled_rejects", "icon controls not found"))
    return cases


def _run_resolution_case(
    *,
    name: str,
    decision,
    capture: Capture,
    candidates: list[ControlCandidate],
    expected_candidate: ControlCandidate | None,
    expect_overlay: bool = True,
    expect_rejected_reason: str = "",
    allow_any_rejection: bool = False,
) -> dict[str, Any]:
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
            instruction=decision.instruction,
            target_control_type=target_control_type_for_resolution(target, candidates),
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
    passed, failures = evaluate_case_result(
        expected_candidate=expected_candidate,
        overlay_rect=overlay_rect,
        rejected_reason=rejected_reason,
        expect_rejected_reason=expect_rejected_reason,
        allow_any_rejection=allow_any_rejection,
        expect_overlay=expect_overlay,
    )
    return {
        "name": name,
        "passed": passed,
        "failures": failures,
        "overlay_rect": overlay_rect,
        "rejected_reason": rejected_reason,
        "diagnostic": diagnostic,
    }


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
    _force_window_to_front(window)
    _process_events(app, CHILD_READY_SETTLE_SEC)
    _force_window_to_front(window)
    return app.exec()


def _force_window_to_front(window: QWidget) -> None:
    if sys.platform != "win32":
        return
    try:
        hwnd = int(window.winId())
        user32 = ctypes.windll.user32
        user32.ShowWindow(hwnd, 1)
        user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
        user32.SetForegroundWindow(hwnd)
    except Exception:
        return


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
        candidates = collect_control_candidates(
            capture,
            timeout_ms=1500,
            limit=SELFTEST_CANDIDATE_LIMIT,
            foreground_handle_provider=_ignore_foreground_window,
        )
        target = _find_target_candidate(candidates, title=title)
        last = (capture, candidates, target)
        if target is not None:
            if stable_rect == target.rect:
                return capture, candidates, target
            stable_rect = target.rect
        if time.monotonic() >= deadline:
            return last
        time.sleep(0.15)


def _ignore_foreground_window() -> int | None:
    return None


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


def _decision_unknown_id(capture: Capture, candidate: ControlCandidate):
    payload = {
        "kind": "step",
        "instruction": f"Click {TARGET_TEXT}.",
        "target_id": "c999",
    }
    return _parse_live_help_decision(json.dumps(payload))


def _decision_model_rect_only(capture: Capture, candidate: ControlCandidate):
    norm = _norm_rect(candidate.rect, capture)
    payload = {
        "kind": "step",
        "instruction": "Click this button.",
        "target": {
            "x": max(0, norm[0] - 5),
            "y": max(0, norm[1] - 5),
            "width": min(1000, norm[2] + 10),
            "height": min(1000, norm[3] + 10),
        },
    }
    return _parse_live_help_decision(json.dumps(payload))


def _decision_wrong_target_id(wrong_candidate: ControlCandidate):
    payload = {
        "kind": "step",
        "instruction": f"Click {TARGET_TEXT}.",
        "target_id": wrong_candidate.id,
    }
    return _parse_live_help_decision(json.dumps(payload))


def _decision_copied_wrong_target_id(
    capture: Capture,
    wrong_candidate: ControlCandidate,
):
    norm = _norm_rect(wrong_candidate.rect, capture)
    payload = {
        "kind": "step",
        "instruction": f"Click {TARGET_TEXT}.",
        "target_id": wrong_candidate.id,
        "target": {
            "x": norm[0],
            "y": norm[1],
            "width": norm[2],
            "height": norm[3],
        },
    }
    return _parse_live_help_decision(json.dumps(payload))


def _decision_duplicate_without_rect(candidate: ControlCandidate):
    payload = {
        "kind": "step",
        "instruction": "Click Duplicate.",
        "target_id": candidate.id,
    }
    return _parse_live_help_decision(json.dumps(payload))


def _decision_duplicate_ambiguous_snap(
    capture: Capture,
    candidates: list[ControlCandidate],
):
    union = _union_rect([candidate.rect for candidate in candidates])
    norm = _norm_rect(union, capture)
    payload = {
        "kind": "step",
        "instruction": "Click this button.",
        "target": {
            "x": norm[0],
            "y": norm[1],
            "width": norm[2],
            "height": norm[3],
        },
    }
    return _parse_live_help_decision(json.dumps(payload))


def _decision_compound_model_rect(
    capture: Capture,
    candidates: list[ControlCandidate],
):
    union = _union_rect([candidate.rect for candidate in candidates])
    norm = _norm_rect(union, capture)
    payload = {
        "kind": "step",
        "instruction": f"Click {TARGET_TEXT}.",
        "target": {
            "x": norm[0],
            "y": norm[1],
            "width": norm[2],
            "height": norm[3],
        },
    }
    return _parse_live_help_decision(json.dumps(payload))


def _decision_icon_target_id(candidate: ControlCandidate):
    payload = {
        "kind": "step",
        "instruction": "Click this icon.",
        "target_id": candidate.id,
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


def _union_rect(rects: list[tuple[int, int, int, int]]) -> tuple[int, int, int, int]:
    left = min(rect[0] for rect in rects)
    top = min(rect[1] for rect in rects)
    right = max(rect[0] + rect[2] for rect in rects)
    bottom = max(rect[1] + rect[3] for rect in rects)
    return (left, top, right - left, bottom - top)


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


def _find_candidates_by_text(
    candidates: list[ControlCandidate],
    text: str,
    *,
    title: str = "",
) -> list[ControlCandidate]:
    needle = text.lower()
    out: list[ControlCandidate] = []
    for candidate in candidates:
        if title and title not in candidate.window_title:
            continue
        if needle in candidate.descriptor.lower():
            out.append(candidate)
    return out


def _find_candidates_by_automation_ids(
    candidates: list[ControlCandidate],
    automation_ids: set[str],
    *,
    title: str = "",
) -> list[ControlCandidate]:
    out: list[ControlCandidate] = []
    for candidate in candidates:
        if title and title not in candidate.window_title:
            continue
        if candidate.automation_id in automation_ids:
            out.append(candidate)
    return out


def _missing_case(name: str, failure: str) -> dict[str, Any]:
    return {
        "name": name,
        "passed": False,
        "failures": [failure],
        "overlay_rect": None,
        "diagnostic": None,
    }


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


def _write_case_artifacts(
    artifacts_dir: Path,
    capture: Capture,
    candidates: list[ControlCandidate],
    cases: list[dict[str, Any]],
) -> None:
    root = artifacts_dir / "cases"
    root.mkdir(parents=True, exist_ok=True)
    image = Image.open(io.BytesIO(capture.png_bytes)).convert("RGB")
    for case in cases:
        case_dir = root / str(case["name"])
        case_dir.mkdir(parents=True, exist_ok=True)
        diagnostic = case.get("diagnostic")
        (case_dir / "summary.json").write_text(
            json.dumps(
                {
                    "name": case["name"],
                    "passed": case["passed"],
                    "failures": case["failures"],
                    "rejected_reason": case.get("rejected_reason", ""),
                    "overlay_rect": case.get("overlay_rect"),
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        if diagnostic is None:
            continue
        (case_dir / "diagnostic.json").write_text(
            json.dumps(diagnostic, indent=2, sort_keys=True),
            encoding="utf-8",
        )
        overlay = image.copy()
        draw = ImageDraw.Draw(overlay)
        for candidate in candidates:
            draw.rectangle(screen_rect_to_image_box(capture, candidate.rect), outline="#64748b", width=1)
        model_rect = diagnostic["model"].get("screen_rect")
        if model_rect:
            draw.rectangle(screen_rect_to_image_box(capture, tuple(model_rect)), outline="#ef4444", width=2)
        resolved_rect = diagnostic["resolution"].get("rect")
        if resolved_rect:
            draw.rectangle(screen_rect_to_image_box(capture, tuple(resolved_rect)), outline="#f59e0b", width=2)
        overlay_rect = diagnostic["overlay"].get("rect")
        if overlay_rect:
            draw.rectangle(screen_rect_to_image_box(capture, tuple(overlay_rect)), outline="#22c55e", width=3)
        overlay.save(case_dir / "overlay.png")


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
        "window_rank": candidate.window_rank,
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
