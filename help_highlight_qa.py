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
            "name": "deictic_target_id_accepts_exact_labeled_control",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click here.",
                "target_id": "c001",
                "target": {"x": 160, "y": 250, "width": 160, "height": 100},
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
            "name": "wrong_target_id_recovers_by_text_match",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 80, 32], "label": "Save"},
                {"rect": [180, 80, 80, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c002",
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "button", "rect": [80, 80, 80, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [80, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "wrong_target_id_recovers_by_geometry_when_text_ambiguous",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [40, 80, 80, 32], "label": "Cancel"},
                {"rect": [160, 80, 80, 32], "label": "Save"},
                {"rect": [300, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "target": {"x": 320, "y": 250, "width": 160, "height": 100},
            },
            "candidates": [
                {"id": "c001", "text": "Cancel", "control_type": "button", "rect": [40, 80, 80, 32]},
                {"id": "c002", "text": "Save", "control_type": "button", "rect": [160, 80, 80, 32]},
                {"id": "c003", "text": "Save", "control_type": "button", "rect": [300, 80, 80, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c002",
                "rect": [160, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "ambiguous_text_without_target_id_blocks_geometry_snap",
            "capture": {"width": 700, "height": 320},
            "draw": [
                {"rect": [160, 80, 80, 32], "label": "Save"},
                {"rect": [520, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target": {"x": 160, "y": 80, "width": 80, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "button", "rect": [160, 80, 80, 32]},
                {"id": "c002", "text": "Save", "control_type": "button", "rect": [520, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rejected_reason": "ambiguous text match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "unlabeled_target_id_recovers_to_visible_text_match",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 32, 32], "label": ""},
                {"rect": [180, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "target": {"x": 160, "y": 250, "width": 64, "height": 100},
            },
            "candidates": [
                {"id": "c001", "text": "", "control_type": "button", "rect": [80, 80, 32, 32]},
                {"id": "c002", "text": "Save", "control_type": "button", "rect": [180, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [180, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "automation_only_model_rect_recovers_to_visible_text_match",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 32, 32], "label": ""},
                {"rect": [180, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target": {"x": 160, "y": 250, "width": 64, "height": 100},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "",
                    "automation_id": "saveButton",
                    "control_type": "button",
                    "rect": [80, 80, 32, 32],
                },
                {"id": "c002", "text": "Save", "control_type": "button", "rect": [180, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [180, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "automation_only_target_id_recovers_to_visible_text_match",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 32, 32], "label": ""},
                {"rect": [180, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "target": {"x": 160, "y": 250, "width": 64, "height": 100},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "",
                    "automation_id": "saveButton",
                    "control_type": "button",
                    "rect": [80, 80, 32, 32],
                },
                {"id": "c002", "text": "Save", "control_type": "button", "rect": [180, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [180, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "background_duplicate_target_id_recovers_to_foreground",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 80, 32], "label": "Save"},
                {"rect": [280, 210, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [80, 80, 80, 32],
                    "window_title": "Background Editor",
                    "window_rank": 2,
                },
                {
                    "id": "c002",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [280, 210, 80, 32],
                    "window_title": "Active Editor",
                    "window_rank": 0,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [280, 210, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "background_duplicate_target_id_with_geometry_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 80, 32], "label": "Save"},
                {"rect": [280, 210, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "target": {"x": 160, "y": 250, "width": 160, "height": 100},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [80, 80, 80, 32],
                    "window_title": "Background Editor",
                    "window_rank": 2,
                },
                {
                    "id": "c002",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [280, 210, 80, 32],
                    "window_title": "Active Editor",
                    "window_rank": 0,
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "ambiguous_candidate_snap_rejects_overlay",
            "capture": {"width": 420, "height": 220},
            "draw": [
                {"rect": [100, 80, 90, 32], "label": "Duplicate"},
                {"rect": [110, 80, 90, 32], "label": "Duplicate"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this button.",
                "target": {"x": 250, "y": 345, "width": 214, "height": 181},
            },
            "candidates": [
                {"id": "c001", "text": "Duplicate", "control_type": "button", "rect": [100, 80, 90, 32]},
                {"id": "c002", "text": "Duplicate", "control_type": "button", "rect": [110, 80, 90, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "ambiguous candidate snap",
                "overlay_emitted": False,
            },
        },
        {
            "name": "foreground_candidate_snap_prefers_active_window",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 120, 80, 32], "label": "Save"},
                {"rect": [170, 120, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this button.",
                "target": {"x": 260, "y": 375, "width": 160, "height": 100},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [80, 120, 80, 32],
                    "window_title": "Background Editor",
                    "window_rank": 2,
                },
                {
                    "id": "c002",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [170, 120, 80, 32],
                    "window_title": "Active Editor",
                    "window_rank": 0,
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c002",
                "rect": [170, 120, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "background_candidate_snap_conflict_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [120, 100, 80, 32], "label": "Save"},
                {"rect": [120, 145, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this button.",
                "target": {"x": 240, "y": 425, "width": 160, "height": 100},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [120, 100, 80, 32],
                    "window_title": "Active Editor",
                    "window_rank": 0,
                },
                {
                    "id": "c002",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [120, 145, 80, 32],
                    "window_title": "Background Editor",
                    "window_rank": 2,
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c002",
                "rejected_reason": "ambiguous candidate snap",
                "overlay_emitted": False,
            },
        },
        {
            "name": "background_candidate_snap_exact_duplicate_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [120, 100, 80, 32], "label": "Save"},
                {"rect": [120, 145, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this button.",
                "target": {"x": 240, "y": 453, "width": 160, "height": 100},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [120, 100, 80, 32],
                    "window_title": "Active Editor",
                    "window_rank": 0,
                },
                {
                    "id": "c002",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [120, 145, 80, 32],
                    "window_title": "Background Editor",
                    "window_rank": 2,
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c002",
                "rejected_reason": "ambiguous candidate snap",
                "overlay_emitted": False,
            },
        },
        {
            "name": "semantic_mismatch_candidate_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [120, 140, 90, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target": {"x": 240, "y": 437, "width": 180, "height": 100},
            },
            "candidates": [
                {"id": "c001", "text": "Cancel", "control_type": "button", "rect": [120, 140, 90, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "loose_row_model_rect_snaps_to_tight_child_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 600, 80], "label": "Settings"},
                {"rect": [40, 100, 80, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings.",
                "target": {"x": 20, "y": 80, "width": 600, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Settings", "control_type": "listitem", "rect": [20, 80, 600, 80]},
                {"id": "c002", "text": "Settings", "control_type": "button", "rect": [40, 100, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [40, 100, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_target_id_recovers_to_tight_child_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 600, 80], "label": "Settings"},
                {"rect": [40, 100, 80, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 600, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Settings", "control_type": "listitem", "rect": [20, 80, 600, 80]},
                {"id": "c002", "text": "Settings", "control_type": "button", "rect": [40, 100, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [40, 100, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_row_model_rect_with_actions_rejects_overlay",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 600, 80], "label": "Account row"},
                {"rect": [470, 100, 60, 32], "label": "Edit"},
                {"rect": [540, 100, 80, 32], "label": "Delete"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this button.",
                "target": {"x": 20, "y": 80, "width": 600, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Account row", "control_type": "listitem", "rect": [20, 80, 600, 80]},
                {"id": "c002", "text": "Edit", "control_type": "button", "rect": [470, 100, 60, 32]},
                {"id": "c003", "text": "Delete", "control_type": "button", "rect": [540, 100, 80, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_field_model_rect_with_clear_action_highlights_field",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 600, 40], "label": "Search"},
                {"rect": [586, 86, 28, 28], "label": "Clear"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this field.",
                "target": {"x": 20, "y": 80, "width": 600, "height": 40},
            },
            "candidates": [
                {"id": "c001", "text": "Search", "control_type": "edit", "rect": [20, 80, 600, 40]},
                {"id": "c002", "text": "Clear", "control_type": "button", "rect": [586, 86, 28, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 600, 40],
                "overlay_emitted": True,
            },
        },
        {
            "name": "search_field_clear_action_snaps_to_clear_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 600, 40], "label": "Search"},
                {"rect": [586, 86, 28, 28], "label": "X"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Clear.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 600, "height": 40},
            },
            "candidates": [
                {"id": "c001", "text": "Search", "control_type": "edit", "rect": [20, 80, 600, 40]},
                {"id": "c002", "text": "Clear", "control_type": "button", "rect": [586, 86, 28, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [586, 86, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "clear_filter_rejects_plain_filter_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [120, 160, 140, 32], "label": "Filter"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clear filter.",
                "target_id": "c001",
                "target": {"x": 120, "y": 160, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Filter", "control_type": "button", "rect": [120, 160, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "delete_filter_rejects_apply_filter_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [120, 160, 160, 32], "label": "Apply filter"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Delete filter.",
                "target_id": "c001",
                "target": {"x": 120, "y": 160, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Apply filter", "control_type": "button", "rect": [120, 160, 160, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_checkbox_model_rect_rejects_button_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 32, 32], "label": ""},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this checkbox.",
                "target": {"x": 160, "y": 250, "width": 64, "height": 100},
            },
            "candidates": [
                {"id": "c001", "text": "", "control_type": "button", "rect": [80, 80, 32, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_checkbox_row_model_rect_snaps_to_single_checkbox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 600, 80], "label": "Task row"},
                {"rect": [34, 110, 20, 20], "label": ""},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this checkbox.",
                "target": {"x": 20, "y": 80, "width": 600, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Task row", "control_type": "listitem", "rect": [20, 80, 600, 80]},
                {"id": "c002", "text": "Done", "control_type": "checkbox", "rect": [34, 110, 20, 20]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c002",
                "rect": [34, 110, 20, 20],
                "overlay_emitted": True,
            },
        },
        {
            "name": "contained_checkbox_requires_child_identity_evidence",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 600, 80], "label": "Billing row"},
                {"rect": [34, 110, 20, 20], "label": ""},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Archive checkbox in Billing row.",
                "target": {"x": 20, "y": 80, "width": 600, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Billing row", "control_type": "listitem", "rect": [20, 80, 600, 80]},
                {"id": "c002", "text": "Done", "control_type": "checkbox", "rect": [34, 110, 20, 20]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_toggle_model_rect_snaps_to_checkbox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Dark mode"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this toggle.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Dark mode", "control_type": "checkbox", "rect": [20, 80, 220, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_switch_model_rect_snaps_to_checkbox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Dark mode"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this switch.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Dark mode", "control_type": "checkbox", "rect": [20, 80, 220, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "toggle_sidebar_model_rect_highlights_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Toggle sidebar"},
                {"rect": [20, 130, 160, 32], "label": "Dark mode"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Toggle sidebar.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Toggle sidebar", "control_type": "button", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "Dark mode", "control_type": "checkbox", "rect": [20, 130, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_option_model_rect_snaps_to_radio",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Weekly"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select this option.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Weekly", "control_type": "radiobutton", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_option_broad_group_rejects_multiple_radios",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Daily"},
                {"rect": [20, 112, 180, 32], "label": "Weekly"},
                {"rect": [20, 144, 180, 32], "label": "Monthly"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select this option.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 96},
            },
            "candidates": [
                {"id": "c001", "text": "Daily", "control_type": "radiobutton", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "Weekly", "control_type": "radiobutton", "rect": [20, 112, 180, 32]},
                {"id": "c003", "text": "Monthly", "control_type": "radiobutton", "rect": [20, 144, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_slider_model_rect_snaps_to_slider",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Volume"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Adjust this slider.",
                "target": {"x": 20, "y": 80, "width": 240, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Volume", "control_type": "slider", "rect": [20, 80, 240, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 240, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_slider_broad_group_rejects_multiple_sliders",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Volume"},
                {"rect": [20, 120, 240, 32], "label": "Brightness"},
                {"rect": [20, 160, 240, 32], "label": "Contrast"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Adjust this slider.",
                "target": {"x": 20, "y": 80, "width": 240, "height": 112},
            },
            "candidates": [
                {"id": "c001", "text": "Volume", "control_type": "slider", "rect": [20, 80, 240, 32]},
                {"id": "c002", "text": "Brightness", "control_type": "slider", "rect": [20, 120, 240, 32]},
                {"id": "c003", "text": "Contrast", "control_type": "slider", "rect": [20, 160, 240, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_spinner_model_rect_snaps_to_spinner",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "History max tokens"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Adjust this spinner.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "History max tokens", "control_type": "spinner", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_spinner_broad_group_rejects_multiple_spinners",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Temperature"},
                {"rect": [20, 120, 180, 32], "label": "Retries"},
                {"rect": [20, 160, 180, 32], "label": "Delay"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Adjust this spinner.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 112},
            },
            "candidates": [
                {"id": "c001", "text": "Temperature", "control_type": "spinner", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "Retries", "control_type": "spinner", "rect": [20, 120, 180, 32]},
                {"id": "c003", "text": "Delay", "control_type": "spinner", "rect": [20, 160, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_hyperlink_model_rect_snaps_to_hyperlink",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 28], "label": "Documentation"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this hyperlink.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 28},
            },
            "candidates": [
                {"id": "c001", "text": "Documentation", "control_type": "hyperlink", "rect": [20, 80, 180, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 180, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_hyperlink_broad_group_rejects_multiple_hyperlinks",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 28], "label": "Docs"},
                {"rect": [20, 116, 180, 28], "label": "Support"},
                {"rect": [20, 152, 180, 28], "label": "Pricing"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this hyperlink.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 100},
            },
            "candidates": [
                {"id": "c001", "text": "Docs", "control_type": "hyperlink", "rect": [20, 80, 180, 28]},
                {"id": "c002", "text": "Support", "control_type": "hyperlink", "rect": [20, 116, 180, 28]},
                {"id": "c003", "text": "Pricing", "control_type": "hyperlink", "rect": [20, 152, 180, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_list_item_model_rect_snaps_to_listitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this list item.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Settings", "control_type": "listitem", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_list_item_broad_group_rejects_multiple_listitems",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "General"},
                {"rect": [20, 120, 180, 32], "label": "Privacy"},
                {"rect": [20, 160, 180, 32], "label": "Billing"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this list item.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 112},
            },
            "candidates": [
                {"id": "c001", "text": "General", "control_type": "listitem", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "Privacy", "control_type": "listitem", "rect": [20, 120, 180, 32]},
                {"id": "c003", "text": "Billing", "control_type": "listitem", "rect": [20, 160, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_tree_item_model_rect_snaps_to_treeitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this tree item.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Settings", "control_type": "treeitem", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_tree_item_broad_group_rejects_multiple_treeitems",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "src"},
                {"rect": [20, 120, 180, 32], "label": "tests"},
                {"rect": [20, 160, 180, 32], "label": "docs"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this tree item.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 112},
            },
            "candidates": [
                {"id": "c001", "text": "src", "control_type": "treeitem", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "tests", "control_type": "treeitem", "rect": [20, 120, 180, 32]},
                {"id": "c003", "text": "docs", "control_type": "treeitem", "rect": [20, 160, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "compact_menuitem_model_rect_snaps_to_menuitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 28], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this menuitem.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 28},
            },
            "candidates": [
                {"id": "c001", "text": "Settings", "control_type": "menuitem", "rect": [20, 80, 180, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 180, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "compact_menuitem_broad_group_rejects_multiple_menuitems",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 28], "label": "Open"},
                {"rect": [20, 116, 180, 28], "label": "Save"},
                {"rect": [20, 152, 180, 28], "label": "Close"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this menuitem.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 100},
            },
            "candidates": [
                {"id": "c001", "text": "Open", "control_type": "menuitem", "rect": [20, 80, 180, 28]},
                {"id": "c002", "text": "Save", "control_type": "menuitem", "rect": [20, 116, 180, 28]},
                {"id": "c003", "text": "Close", "control_type": "menuitem", "rect": [20, 152, 180, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "compact_tabitem_model_rect_snaps_to_tabitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this tabitem.",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Settings", "control_type": "tabitem", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "compact_tabitem_broad_group_rejects_multiple_tabitems",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "General"},
                {"rect": [180, 80, 160, 32], "label": "Privacy"},
                {"rect": [340, 80, 160, 32], "label": "Billing"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this tabitem.",
                "target": {"x": 20, "y": 80, "width": 480, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "General", "control_type": "tabitem", "rect": [20, 80, 160, 32]},
                {"id": "c002", "text": "Privacy", "control_type": "tabitem", "rect": [180, 80, 160, 32]},
                {"id": "c003", "text": "Billing", "control_type": "tabitem", "rect": [340, 80, 160, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "compact_headeritem_model_rect_snaps_to_headeritem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 28], "label": "Status"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this headeritem.",
                "target": {"x": 20, "y": 80, "width": 160, "height": 28},
            },
            "candidates": [
                {"id": "c001", "text": "Status", "control_type": "headeritem", "rect": [20, 80, 160, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 160, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "compact_headeritem_broad_group_rejects_multiple_headeritems",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 28], "label": "Name"},
                {"rect": [180, 80, 160, 28], "label": "Status"},
                {"rect": [340, 80, 160, 28], "label": "Owner"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this headeritem.",
                "target": {"x": 20, "y": 80, "width": 480, "height": 28},
            },
            "candidates": [
                {"id": "c001", "text": "Name", "control_type": "headeritem", "rect": [20, 80, 160, 28]},
                {"id": "c002", "text": "Status", "control_type": "headeritem", "rect": [180, 80, 160, 28]},
                {"id": "c003", "text": "Owner", "control_type": "headeritem", "rect": [340, 80, 160, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_split_button_model_rect_snaps_to_splitbutton",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Export"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this split button.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Export", "control_type": "splitbutton", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_split_button_broad_group_rejects_multiple_splitbuttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Export"},
                {"rect": [20, 120, 180, 32], "label": "Share"},
                {"rect": [20, 160, 180, 32], "label": "Archive"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this split button.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 112},
            },
            "candidates": [
                {"id": "c001", "text": "Export", "control_type": "splitbutton", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "Share", "control_type": "splitbutton", "rect": [20, 120, 180, 32]},
                {"id": "c003", "text": "Archive", "control_type": "splitbutton", "rect": [20, 160, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "browser_url_bar_model_rect_snaps_to_address_edit",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 360, 32], "label": "Address"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Focus the URL bar.",
                "target": {"x": 20, "y": 80, "width": 360, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Address", "control_type": "edit", "rect": [20, 80, 360, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 360, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_url_bar_broad_group_prefers_address_edit",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 360, 32], "label": "Address"},
                {"rect": [20, 120, 360, 32], "label": "Search"},
                {"rect": [20, 160, 360, 32], "label": "Filter"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Focus the URL bar.",
                "target": {"x": 20, "y": 80, "width": 360, "height": 112},
            },
            "candidates": [
                {"id": "c001", "text": "Address", "control_type": "edit", "rect": [20, 80, 360, 32]},
                {"id": "c002", "text": "Search", "control_type": "edit", "rect": [20, 120, 360, 32]},
                {"id": "c003", "text": "Filter", "control_type": "edit", "rect": [20, 160, 360, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 360, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "info_wording_rejects_browser_address_bar_url_content",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 360, 32], "label": "about:blank"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show info.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "about:blank | Address and search bar",
                    "control_type": "edit",
                    "rect": [20, 80, 360, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "site_information_recovers_from_address_bar_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 360, 32], "label": "about:blank"},
                {"rect": [420, 80, 160, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open site information.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "about:blank | Address and search bar",
                    "control_type": "edit",
                    "rect": [20, 80, 360, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [420, 80, 160, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [420, 80, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "info_wording_rejects_about_blank_tab_title",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "about:blank tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show info.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "about:blank",
                    "control_type": "tabitem",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "site_information_recovers_from_about_blank_tab_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "about:blank tab"},
                {"rect": [240, 80, 160, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show site info.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "about:blank",
                    "control_type": "tabitem",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [240, 80, 160, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [240, 80, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "about_blank_tab_title_accepts_explicit_tab_wording",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "about:blank tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open about:blank tab.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "about:blank",
                    "control_type": "tabitem",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_browser_menu_recovers_from_hidden_bookmarks_overflow",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 60, 32], "label": "Chrome"},
                {"rect": [120, 80, 220, 32], "label": "Hidden bookmarks"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open more options menu.",
                "target_id": "c002",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Chrome",
                    "control_type": "button",
                    "rect": [20, 80, 60, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Menu containing hidden bookmarks",
                    "control_type": "button",
                    "rect": [120, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 60, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_browser_menu_rejects_hidden_bookmarks_overflow",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Hidden bookmarks"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open options menu.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Menu containing hidden bookmarks",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_menu_button_rejects_browser_back_button_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 34, 34], "label": "Back"},
                {"rect": [120, 80, 40, 34], "label": "Chrome"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click menu button.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Back",
                    "control_type": "button",
                    "rect": [20, 80, 34, 34],
                    "automation_id": "view_1001",
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Chrome",
                    "control_type": "button",
                    "rect": [120, 80, 40, 34],
                    "automation_id": "view_1007",
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_menu_button_model_rect_rejects_browser_back_button_snap",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 34, 34], "label": "Back"},
                {"rect": [120, 80, 40, 34], "label": "Chrome"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click menu button.",
                "target": {"x": 20, "y": 80, "width": 34, "height": 34},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Back",
                    "control_type": "button",
                    "rect": [20, 80, 34, 34],
                    "automation_id": "view_1001",
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Chrome",
                    "control_type": "button",
                    "rect": [120, 80, 40, 34],
                    "automation_id": "view_1007",
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_hidden_rejects_hidden_bookmarks_overflow",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Hidden bookmarks"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open hidden.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Menu containing hidden bookmarks",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "chrome_menu_button_accepts_more_options_wording",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 60, 32], "label": "Chrome"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open more options menu.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Chrome",
                    "control_type": "button",
                    "rect": [20, 80, 60, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 60, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_window_page_more_options_beats_chrome_menu_fallback",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [400, 400, 120, 32], "label": "More options"},
                {"rect": [930, 8, 40, 34], "label": "Chrome"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the more options menu.",
                "target_id": "c001",
                "target": {"x": 400, "y": 400, "width": 120, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "More options",
                    "control_type": "button",
                    "rect": [400, 400, 120, 32],
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Chrome",
                    "control_type": "button",
                    "rect": [930, 8, 40, 34],
                    "automation_id": "view_1007",
                    "window_title": "Project - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [400, 400, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "hidden_bookmarks_overflow_accepts_hidden_bookmarks_wording",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Hidden bookmarks"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open hidden bookmarks.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Menu containing hidden bookmarks",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bare_all_rejects_all_bookmarks_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "All Bookmarks"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open all.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "All Bookmarks",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "all_bookmarks_wording_accepts_all_bookmarks_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "All Bookmarks"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open all bookmarks.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "All Bookmarks",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "live_address_bar_label_accepts_explicit_address_wording",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 360, 32], "label": "Address"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click address bar.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "about:blank | Address and search bar",
                    "control_type": "edit",
                    "rect": [20, 80, 360, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 360, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "text_entry_action_wrong_target_id_recovers_to_edit",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 260, 32], "label": "Email"},
                {"rect": [300, 80, 90, 32], "label": "Email"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Type your email.",
                "target_id": "c002",
                "target": {"x": 300, "y": 80, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Email", "control_type": "edit", "rect": [20, 80, 260, 32]},
                {"id": "c002", "text": "Email", "control_type": "button", "rect": [300, 80, 90, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 260, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "search_bar_plain_button_rejects_overlay",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [300, 80, 90, 32], "label": "Search"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the search bar.",
                "target": {"x": 300, "y": 80, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Search", "control_type": "button", "rect": [300, 80, 90, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "state_action_wrong_target_id_recovers_to_checkbox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Remember me"},
                {"rect": [240, 80, 120, 32], "label": "Remember me"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Check Remember me.",
                "target_id": "c002",
                "target": {"x": 240, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Remember me", "control_type": "checkbox", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "Remember me", "control_type": "button", "rect": [240, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "state_action_model_rect_prefers_matching_button_over_noun_checkbox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 190, 32], "label": "Enable notifications"},
                {"rect": [240, 80, 180, 32], "label": "Notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Enable notifications.",
                "target": {"x": 20, "y": 80, "width": 190, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Enable notifications", "control_type": "button", "rect": [20, 80, 190, 32]},
                {"id": "c002", "text": "Notifications", "control_type": "checkbox", "rect": [240, 80, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 190, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "state_action_target_id_rejects_opposite_checkbox_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 190, 32], "label": "Disable notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Enable notifications.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Disable notifications", "control_type": "checkbox", "rect": [20, 80, 190, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "enable_notifications_target_id_rejects_enabled_notifications_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 210, 32], "label": "Enabled notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Enable notifications.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Enabled notifications", "control_type": "checkbox", "rect": [20, 80, 210, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "enable_notifications_target_id_rejects_disabled_notifications_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 210, 32], "label": "Disabled notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Enable notifications.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Disabled notifications", "control_type": "checkbox", "rect": [20, 80, 210, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "turn_on_notifications_target_id_rejects_turn_off_notifications_checkbox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 210, 32], "label": "Turn off notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Turn on notifications.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Turn off notifications", "control_type": "checkbox", "rect": [20, 80, 210, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "approve_request_target_id_rejects_approved_request_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Approved request"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Approve request.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Approved request", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "accept_invite_target_id_rejects_decline_invite_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Decline invite"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Accept invite.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Decline invite", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "mark_as_read_model_rect_rejects_read_message_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Read message"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mark as read.",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Read message", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "choice_wording_wrong_target_id_recovers_to_radio",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Daily"},
                {"rect": [240, 80, 120, 32], "label": "Daily"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pick Daily choice.",
                "target_id": "c002",
                "target": {"x": 240, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Daily", "control_type": "radiobutton", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "Daily", "control_type": "button", "rect": [240, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "select_yes_wrong_target_id_recovers_to_radio",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Yes"},
                {"rect": [140, 80, 80, 32], "label": "Yes"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select Yes.",
                "target_id": "c002",
                "target": {"x": 140, "y": 80, "width": 80, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Yes", "control_type": "radiobutton", "rect": [20, 80, 80, 32]},
                {"id": "c002", "text": "Yes", "control_type": "button", "rect": [140, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "country_select_wrong_target_id_recovers_to_combobox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Country"},
                {"rect": [240, 80, 100, 32], "label": "Country"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Country select.",
                "target_id": "c002",
                "target": {"x": 240, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Country", "control_type": "combobox", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "Country", "control_type": "button", "rect": [240, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "save_file_wrong_target_id_recovers_from_object_to_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "File"},
                {"rect": [220, 80, 120, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Save file.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "File", "control_type": "button", "rect": [20, 80, 150, 32]},
                {"id": "c002", "text": "Save", "control_type": "button", "rect": [220, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [220, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "download_file_wrong_target_id_recovers_from_object_to_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "File"},
                {"rect": [220, 80, 140, 32], "label": "Download"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Download file.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "File", "control_type": "button", "rect": [20, 80, 150, 32]},
                {"id": "c002", "text": "Download", "control_type": "button", "rect": [220, 80, 140, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [220, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "attach_selected_file_prefers_exact_action_over_upload",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Upload"},
                {"rect": [220, 80, 120, 32], "label": "Attach"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Attach selected file.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Upload", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Attach", "control_type": "button", "rect": [220, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [220, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "check_for_updates_button_remains_clickable",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Check for updates"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Check for updates.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Check for updates", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "copy_action_target_id_accepts_duplicate_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Duplicate"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Copy this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Duplicate", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "clone_action_target_id_accepts_duplicate_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Duplicate"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clone this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Duplicate", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "copy_action_text_match_overrides_wrong_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Duplicate"},
                {"rect": [180, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Copy this item.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Duplicate", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "copy_action_alias_rejects_ambiguous_copy_and_duplicate_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Duplicate"},
                {"rect": [180, 80, 100, 32], "label": "Copy"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Copy this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Duplicate", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Copy", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "copy_coupon_model_rect_rejects_copy_address_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Copy address"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Copy coupon.",
                "target": {"x": 20, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Copy address", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "create_action_target_id_accepts_add_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Add"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Create item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Add", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "finish_action_target_id_accepts_done_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Done"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Finish setup.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Done", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "confirm_action_target_id_accepts_checkmark_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "OK"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Confirm selection.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2713", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "complete_action_target_id_accepts_check_mark_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Check mark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Complete task.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Check mark", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "apply_checkmark_text_match_overrides_cancel_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "OK"},
                {"rect": [180, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Apply changes.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u2713", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "apply_changes_target_id_rejects_cancel_changes_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Cancel changes"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Apply changes.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Cancel changes", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "apply_changes_target_id_rejects_applied_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Changes applied"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Apply changes.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Changes applied", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "save_document_model_rect_rejects_saved_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Document saved"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Save document.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Document saved", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "send_message_target_id_rejects_sent_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Message sent"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Send message.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Message sent", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "resolve_alert_target_id_rejects_resolved_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Alert resolved"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Resolve alert.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Alert resolved", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "apply_filter_model_rect_rejects_apply_coupon_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Apply coupon"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Apply filter.",
                "target": {"x": 20, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Apply coupon", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "checkbox_intent_rejects_checkmark_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "OK"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Check this box.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2713", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id control type mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "create_action_text_match_overrides_cancel_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Add"},
                {"rect": [180, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Create item.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Add", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "finish_action_text_match_overrides_back_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Done"},
                {"rect": [180, 80, 100, 32], "label": "Back"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Finish setup.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Done", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Back", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "sign_out_target_id_accepts_logout_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Logout"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Sign out.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Logout", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "sign_out_text_match_overrides_profile_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Logout"},
                {"rect": [180, 80, 100, 32], "label": "Profile"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Sign out.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Logout", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Profile", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "sign_out_text_match_overrides_sign_in_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Logout"},
                {"rect": [180, 80, 100, 32], "label": "Sign in"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Sign out.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Logout", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Sign in", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "close_dialog_target_id_accepts_cancel_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close the dialog.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Cancel", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "close_dialog_text_match_overrides_details_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Cancel"},
                {"rect": [180, 80, 100, 32], "label": "Details"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close the dialog.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Cancel", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Details", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "cancel_subscription_rejects_close_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Close"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Cancel subscription.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Close", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "clear_search_target_id_accepts_x_symbol_inside_search_field",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 500, 40], "label": "Search"},
                {"rect": [486, 86, 28, 28], "label": "X"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clear search.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u00d7", "control_type": "button", "rect": [486, 86, 28, 28]},
                {"id": "c002", "text": "Search", "control_type": "edit", "rect": [20, 80, 500, 40]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [486, 86, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "clear_search_target_id_rejects_window_close_x_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [700, 20, 32, 32], "label": "X"},
                {"rect": [20, 80, 500, 40], "label": "Search"},
                {"rect": [486, 86, 28, 28], "label": "Clear"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clear search.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "\u00d7",
                    "automation_id": "Close",
                    "control_type": "button",
                    "rect": [700, 20, 32, 32],
                    "window_title": "Dialog",
                },
                {"id": "c002", "text": "Search", "control_type": "edit", "rect": [20, 80, 500, 40]},
                {"id": "c003", "text": "Clear", "control_type": "button", "rect": [486, 86, 28, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c003",
                "rect": [486, 86, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "clear_search_model_rect_rejects_window_close_x_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [700, 20, 32, 32], "label": "X"},
                {"rect": [20, 80, 500, 40], "label": "Search"},
                {"rect": [486, 86, 28, 28], "label": "Clear"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clear search.",
                "target": {"x": 700, "y": 20, "width": 32, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "\u00d7",
                    "automation_id": "Close",
                    "control_type": "button",
                    "rect": [700, 20, 32, 32],
                    "window_title": "Dialog",
                },
                {"id": "c002", "text": "Search", "control_type": "edit", "rect": [20, 80, 500, 40]},
                {"id": "c003", "text": "Clear", "control_type": "button", "rect": [486, 86, 28, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c003",
                "rect": [486, 86, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "weather_widget_target_id_accepts_open_weather",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Widgets weather"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open weather.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Widgets 64\u00b0F Clear",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "weather_widget_target_id_accepts_open_widgets",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Widgets weather"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open widgets.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Widgets 64\u00b0F Clear",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "clear_search_rejects_weather_widget_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Widgets weather"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clear search.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Widgets 64\u00b0F Clear",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "clear_text_rejects_weather_clear_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Weather"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clear text.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Weather 64\u00b0F Clear",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "tab_search_target_id_accepts_search_tabs_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Search tabs"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open tab search.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Search tabs",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_tabs_rejects_chrome_tab_search_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Search tabs"},
                {"rect": [180, 80, 220, 32], "label": "about:blank"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Highlight tabs.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Search tabs",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "about:blank",
                    "control_type": "tabitem",
                    "rect": [180, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id control type mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "tab_search_rejects_windows_search_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Windows search"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Search tabs.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Search - World Reef Awareness Day",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "windows_search_target_id_accepts_taskbar_search",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Windows search"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Windows search.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Search - World Reef Awareness Day",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "windows_search_rejects_chrome_tab_search_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Search tabs"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Search Windows.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Search tabs",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_search_recovers_from_tab_search_to_windows_search",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Search tabs"},
                {"rect": [180, 80, 180, 32], "label": "Windows search"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open search.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Search tabs",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Search - World Reef Awareness Day",
                    "control_type": "button",
                    "rect": [180, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [180, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_search_rejects_chrome_tab_search_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Search tabs"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open search.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Search tabs",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_search_rejects_search_results_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Search results"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open search.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Search results", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "go_back_rejects_backup_sync_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Back up and sync"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go back.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Back up and sync", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "search_field_button_prefers_tight_button_over_field",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Search"},
                {"rect": [230, 82, 28, 28], "label": ""},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Search field button.",
                "target": {"x": 230, "y": 82, "width": 28, "height": 28},
            },
            "candidates": [
                {"id": "field", "text": "Search", "control_type": "edit", "rect": [20, 80, 240, 32]},
                {
                    "id": "btn",
                    "text": "",
                    "control_type": "button",
                    "rect": [230, 82, 28, 28],
                    "automation_id": "SearchButton",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "btn",
                "rect": [230, 82, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_table_row_not_demoted_to_same_label_child_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 320, 56], "label": "Settings"},
                {"rect": [36, 92, 88, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings table row.",
                "target_id": "row",
                "target": {"x": 20, "y": 80, "width": 320, "height": 56},
            },
            "candidates": [
                {"id": "row", "text": "Settings", "control_type": "listitem", "rect": [20, 80, 320, 56]},
                {"id": "btn", "text": "Settings", "control_type": "button", "rect": [36, 92, 88, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "row",
                "rect": [20, 80, 320, 56],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_card_not_demoted_to_same_label_child_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 320, 56], "label": "Settings"},
                {"rect": [36, 92, 88, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings card.",
                "target_id": "card",
                "target": {"x": 20, "y": 80, "width": 320, "height": 56},
            },
            "candidates": [
                {"id": "card", "text": "Settings", "control_type": "listitem", "rect": [20, 80, 320, 56]},
                {"id": "btn", "text": "Settings", "control_type": "button", "rect": [36, 92, 88, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "card",
                "rect": [20, 80, 320, 56],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_menu_accepts_menu_launcher_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 500, 48], "label": "Order 123"},
                {"rect": [560, 108, 32, 32], "label": "..."},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open menu in row.",
                "target": {"x": 560, "y": 108, "width": 32, "height": 32},
            },
            "candidates": [
                {"id": "row1", "text": "Order 123", "control_type": "listitem", "rect": [100, 100, 500, 48]},
                {"id": "menu1", "text": "More options", "control_type": "button", "rect": [560, 108, 32, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "menu1",
                "rect": [560, 108, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "combobox_down_arrow_accepts_adjacent_open_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 220, 32], "label": "Country"},
                {"rect": [292, 100, 28, 32], "label": "v"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the down arrow.",
                "target": {"x": 292, "y": 100, "width": 28, "height": 32},
            },
            "candidates": [
                {"id": "combo1", "text": "Country", "control_type": "combobox", "rect": [100, 100, 220, 32]},
                {"id": "arrow1", "text": "Open", "control_type": "button", "rect": [292, 100, 28, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "arrow1",
                "rect": [292, 100, 28, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_settings_rejects_browser_tab_title_for_settings_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [80, 20, 220, 40], "label": "Settings - Google Chrome"},
                {"rect": [500, 120, 100, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open settings.",
                "target_id": "tab",
                "target": {"x": 80, "y": 20, "width": 220, "height": 40},
            },
            "candidates": [
                {
                    "id": "tab",
                    "text": "Settings - Google Chrome",
                    "control_type": "tabitem",
                    "rect": [80, 20, 220, 40],
                    "window_title": "Settings - Google Chrome",
                },
                {
                    "id": "button",
                    "text": "Settings",
                    "control_type": "button",
                    "rect": [500, 120, 100, 32],
                    "window_title": "Settings - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "button",
                "rect": [500, 120, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "clear_text_match_overrides_field_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [180, 80, 220, 32], "label": "Body text"},
                {"rect": [372, 86, 28, 20], "label": "X"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clear text.",
                "target": {"x": 180, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "X", "control_type": "button", "rect": [372, 86, 28, 20]},
                {"id": "c002", "text": "Body text", "control_type": "edit", "rect": [180, 80, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [372, 86, 28, 20],
                "overlay_emitted": True,
            },
        },
        {
            "name": "delete_symbol_target_id_accepts_wastebasket_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Delete"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Delete item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f5d1", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "delete_symbol_text_match_overrides_cancel_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Delete"},
                {"rect": [180, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Delete item.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f5d1", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "delete_account_target_id_rejects_delete_message_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Delete message"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Delete account.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Delete message", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "delete_account_target_id_rejects_delete_button_in_messages_window",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Delete"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Delete account.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Delete",
                    "control_type": "button",
                    "rect": [20, 80, 100, 32],
                    "window_title": "Messages",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "zoom_in_target_id_accepts_plus_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "+"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Zoom in.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "+", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "zoom_out_target_id_accepts_minus_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "-"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Zoom out.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "-", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "zoom_in_text_match_overrides_fit_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "+"},
                {"rect": [180, 80, 100, 32], "label": "Fit"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Zoom in.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "+", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Fit", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "zoom_out_text_match_overrides_fit_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "-"},
                {"rect": [180, 80, 100, 32], "label": "Fit"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Zoom out.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "-", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Fit", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "zoom_in_rejects_add_button_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Add"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Zoom in.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Add", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "minimize_window_target_id_accepts_minus_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "-"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Minimize window.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "-", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "minimize_all_windows_target_id_accepts_show_desktop",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Desktop"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Minimize all windows.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Desktop",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "hide_all_windows_target_id_accepts_show_desktop",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Desktop"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Hide all windows.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Desktop",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "minimize_all_windows_rejects_window_minimize_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Minimize"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Minimize all windows.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Minimize",
                    "control_type": "button",
                    "rect": [20, 80, 100, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "minimize_window_rejects_show_desktop_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Desktop"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Minimize window.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Desktop",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_desktop_rejects_show_desktop_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 12, 32], "label": "Desktop"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open desktop.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Desktop",
                    "control_type": "button",
                    "rect": [20, 80, 12, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_desktop_model_rect_rejects_show_desktop_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 12, 32], "label": "Desktop"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click desktop.",
                "target": {"x": 20, "y": 80, "width": 12, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Desktop",
                    "control_type": "button",
                    "rect": [20, 80, 12, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_desktop_rejects_docker_desktop_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "Docker Desktop"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open desktop.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Docker Desktop",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_desktop_model_rect_rejects_docker_desktop_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "Docker Desktop"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open desktop.",
                "target": {"x": 20, "y": 80, "width": 76, "height": 54},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Docker Desktop",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "docker_desktop_wording_accepts_docker_desktop_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "Docker Desktop"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Docker Desktop.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Docker Desktop",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 76, 54],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bare_about_rejects_spotlight_picture_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "Picture"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open about.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Learn about this picture",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "picture_wording_accepts_spotlight_picture_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "Picture"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open this picture.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Learn about this picture",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 76, 54],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bare_new_rejects_new_pandora_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "Pandora"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open new.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "New Pandora (1)",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "pandora_wording_accepts_new_pandora_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "Pandora"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Pandora.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "New Pandora (1)",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 76, 54],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bare_app_rejects_socialapp_desktop_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "SocialApp"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open app.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "SocialApp",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_ai_model_rect_rejects_atlas_desktop_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "Atlas.ai"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open ai.",
                "target": {"x": 20, "y": 80, "width": 76, "height": 54},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Atlas.ai",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "socialapp_wording_accepts_socialapp_desktop_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 76, 54], "label": "SocialApp"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open SocialApp.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "SocialApp",
                    "control_type": "listitem",
                    "rect": [20, 80, 76, 54],
                    "window_title": "Program Manager",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 76, 54],
                "overlay_emitted": True,
            },
        },
        {
            "name": "maximize_window_target_id_accepts_square_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Square"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Maximize window.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u25a1", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "restore_window_target_id_accepts_overlap_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Restore"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Restore window.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f5d7", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "close_window_target_id_accepts_x_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "X"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close window.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u00d7", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "close_window_wrong_target_id_recovers_to_foreground_close",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [900, 20, 32, 32], "label": "X"},
                {"rect": [900, 140, 32, 32], "label": "X"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close window.",
                "target_id": "c002",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [900, 20, 32, 32],
                    "window_title": "Active Editor",
                    "window_rank": 0,
                },
                {
                    "id": "c002",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [900, 140, 32, 32],
                    "window_title": "Background Editor",
                    "window_rank": 2,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [900, 20, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "close_tab_recovers_to_tab_close_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 20, 220, 40], "label": "Docs - Project Plan"},
                {"rect": [286, 28, 24, 24], "label": "X"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close tab.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Docs - Project Plan",
                    "control_type": "tabitem",
                    "rect": [100, 20, 220, 40],
                },
                {
                    "id": "c002",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [286, 28, 24, 24],
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [286, 28, 24, 24],
                "overlay_emitted": True,
            },
        },
        {
            "name": "close_tab_rejects_window_close_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 20, 220, 40], "label": "Docs - Project Plan"},
                {"rect": [900, 20, 46, 40], "label": "X"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close tab.",
                "target_id": "c002",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Docs - Project Plan",
                    "control_type": "tabitem",
                    "rect": [100, 20, 220, 40],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [900, 20, 46, 40],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c002",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "close_page_recovers_to_browser_tab_close_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 20, 220, 40], "label": "Project plan"},
                {"rect": [286, 28, 24, 24], "label": "X"},
                {"rect": [940, 20, 46, 40], "label": "X"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close page.",
                "target_id": "winclose",
                "target": {"x": 940, "y": 20, "width": 46, "height": 40},
            },
            "candidates": [
                {
                    "id": "tab",
                    "text": "Project plan",
                    "control_type": "tabitem",
                    "rect": [100, 20, 220, 40],
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "tabclose",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [286, 28, 24, 24],
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "winclose",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [940, 20, 46, 40],
                    "window_title": "Project - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "tabclose",
                "rect": [286, 28, 24, 24],
                "overlay_emitted": True,
            },
        },
        {
            "name": "close_toolbar_target_id_recovers_from_window_close_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [940, 10, 46, 40], "label": "Close"},
                {"rect": [280, 80, 120, 32], "label": "Close toolbar"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close toolbar.",
                "target_id": "c001",
                "target": {"x": 940, "y": 10, "width": 46, "height": 40},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [940, 10, 46, 40],
                    "window_title": "MyApp - Google Chrome",
                },
                {"id": "c002", "text": "Close toolbar", "control_type": "button", "rect": [280, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [280, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "close_dialog_duplicate_buttons_stay_ambiguous",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [900, 20, 32, 32], "label": "X"},
                {"rect": [900, 140, 32, 32], "label": "X"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close the dialog.",
                "target_id": "c002",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [900, 20, 32, 32],
                    "window_title": "Active Editor",
                    "window_rank": 0,
                },
                {
                    "id": "c002",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [900, 140, 32, 32],
                    "window_title": "Background Editor",
                    "window_rank": 2,
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c002",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "minimize_window_text_match_overrides_zoom_out_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "-"},
                {"rect": [180, 80, 100, 32], "label": "Zoom out"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Minimize window.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "-", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Zoom out", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "maximize_window_text_match_overrides_full_screen_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Square"},
                {"rect": [180, 80, 120, 32], "label": "Full screen"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Maximize window.",
                "target": {"x": 180, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u25a1", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Full screen", "control_type": "button", "rect": [180, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "restore_window_text_match_overrides_maximize_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Restore"},
                {"rect": [180, 80, 100, 32], "label": "Maximize"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Restore window.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f5d7", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Maximize", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "zoom_out_rejects_minimize_button_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Minimize"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Zoom out.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Minimize", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "minimize_window_rejects_zoom_out_button_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Zoom out"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Minimize window.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Zoom out", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "paste_action_target_id_accepts_clipboard_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 110, 32], "label": "Clipboard"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Paste into the note.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Clipboard", "control_type": "button", "rect": [20, 80, 110, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "paste_action_target_id_accepts_clipboard_symbol",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "P"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Paste.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4cb", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "copy_selected_text_accepts_copy_toolbar_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Copy"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Copy selected text.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Copy", "control_type": "button", "rect": [20, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "paste_selected_text_accepts_paste_toolbar_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Paste"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Paste selected text.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Paste", "control_type": "button", "rect": [20, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "cut_action_target_id_accepts_scissors_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Scissors"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Cut selection.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Scissors", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "cut_action_target_id_accepts_scissors_symbol",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "C"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Cut selection.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2702", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "paste_text_match_overrides_export_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 110, 32], "label": "Clipboard"},
                {"rect": [180, 80, 100, 32], "label": "Export"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Paste.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Clipboard", "control_type": "button", "rect": [20, 80, 110, 32]},
                {"id": "c002", "text": "Export", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "cut_text_match_overrides_copy_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Scissors"},
                {"rect": [180, 80, 100, 32], "label": "Copy"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Cut selection.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Scissors", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Copy", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "filter_action_target_id_accepts_funnel_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Funnel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Filter results.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Funnel", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "funnel_action_target_id_accepts_filter_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Filter"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click funnel.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Filter", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "sort_ascending_target_id_accepts_a_to_z_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "A to Z"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Sort ascending.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "A to Z", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "sort_descending_target_id_accepts_z_to_a_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Z to A"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Sort descending.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Z to A", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "filter_text_match_overrides_search_field_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Funnel"},
                {"rect": [180, 80, 220, 32], "label": "Search"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Filter results.",
                "target": {"x": 180, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Funnel", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Search", "control_type": "edit", "rect": [180, 80, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "sort_text_match_overrides_filter_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "A to Z"},
                {"rect": [180, 80, 100, 32], "label": "Filter"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Sort ascending.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "A to Z", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Filter", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "sort_direction_rejects_opposite_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Z to A"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Sort A to Z.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Z to A", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "search_instruction_rejects_filter_action_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Filter users"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Search users.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Filter users", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "remove_instruction_rejects_add_action_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Add member"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Remove member.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Add member", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "second_card_duplicate_action_rejects_first_card_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 300, 100], "label": "Billing"},
                {"rect": [230, 70, 60, 30], "label": "Save"},
                {"rect": [10, 130, 300, 100], "label": "Billing"},
                {"rect": [230, 190, 60, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the second Billing card.",
                "target_id": "save1",
                "target": {"x": 230, "y": 70, "width": 60, "height": 30},
            },
            "candidates": [
                {"id": "card1", "text": "Billing", "control_type": "listitem", "rect": [10, 10, 300, 100]},
                {"id": "save1", "text": "Save", "control_type": "button", "rect": [230, 70, 60, 30]},
                {"id": "card2", "text": "Billing", "control_type": "listitem", "rect": [10, 130, 300, 100]},
                {"id": "save2", "text": "Save", "control_type": "button", "rect": [230, 190, 60, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "save2",
                "rect": [230, 190, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bold_action_target_id_accepts_b_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "B"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Bold text.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "B", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "italic_action_target_id_accepts_i_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "I"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Italic text.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "I", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "italicize_selected_text_accepts_italic_toolbar_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "I"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Italicize selected text.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "I", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "underline_action_target_id_accepts_u_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "U"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Underline text.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "U", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "remove_formatting_target_id_accepts_clear_formatting_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 170, 32], "label": "Clear formatting"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Remove formatting.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Clear formatting", "control_type": "button", "rect": [20, 80, 170, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 170, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "remove_formatting_model_rect_rejects_trash_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Trash"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Remove formatting.",
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Trash", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bold_text_match_overrides_text_field_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "B"},
                {"rect": [180, 80, 220, 32], "label": "Body text"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Bold text.",
                "target": {"x": 180, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "B", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Body text", "control_type": "edit", "rect": [180, 80, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "undo_action_target_id_accepts_left_curved_arrow",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Undo"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Undo change.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u21b6", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "redo_action_target_id_accepts_right_curved_arrow",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Redo"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Redo change.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u21b7", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "undo_text_match_overrides_back_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Undo"},
                {"rect": [180, 80, 100, 32], "label": "Back"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Undo change.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u21b6", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Back", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "redo_text_match_overrides_next_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Redo"},
                {"rect": [180, 80, 100, 32], "label": "Next"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Redo change.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u21b7", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Next", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "download_action_target_id_accepts_export_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Export"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Download the report.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Export", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "import_action_target_id_accepts_upload_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Upload"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Import data.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Upload", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "refresh_action_target_id_accepts_reload_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Reload"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Refresh the page.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Reload", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "refresh_icon_target_id_accepts_clockwise_arrow_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Refresh"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Refresh the page.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u27f3", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "reload_icon_target_id_accepts_clockwise_arrows_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Reload"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Reload this view.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f504", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "refresh_icon_text_match_overrides_back_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Refresh"},
                {"rect": [180, 80, 100, 32], "label": "Back"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Refresh the page.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u27f3", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Back", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "refresh_action_rejects_redo_arrow_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Redo"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Refresh the page.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u21bb", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "download_action_text_match_overrides_wrong_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Export"},
                {"rect": [180, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Download the report.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Export", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "download_action_alias_rejects_ambiguous_download_and_export_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Export"},
                {"rect": [180, 80, 140, 32], "label": "Download"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Download the report.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Export", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Download", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "download_action_ambiguous_alias_survives_stale_cancel_target",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Export"},
                {"rect": [180, 80, 140, 32], "label": "Download"},
                {"rect": [360, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Download the report.",
                "target_id": "c003",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Export", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Download", "control_type": "button", "rect": [180, 80, 140, 32]},
                {"id": "c003", "text": "Cancel", "control_type": "button", "rect": [360, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rejected_reason": "ambiguous text match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "download_report_target_id_rejects_download_invoice_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Download invoice"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Download report.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Download invoice", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_download_file_rejects_export_alias_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Export"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Download file.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Export", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_import_file_rejects_upload_alias_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Upload"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Import file.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Upload", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "copy_link_rejects_duplicate_link_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Duplicate link"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Copy link.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Duplicate link", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "share_action_target_id_accepts_chain_link_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "S"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Share this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f517", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "external_link_target_id_accepts_external_link_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "External"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open external link.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "External link", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "open_new_tab_target_id_accepts_arrow_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "N"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open in new tab.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2197", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "open_new_tab_target_id_accepts_new_tab_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "New tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open in new tab.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "New tab", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "external_link_target_id_rejects_new_tab_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "New tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open external link.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "New tab",
                    "control_type": "button",
                    "rect": [20, 80, 100, 32],
                    "window_title": "GitHub - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "new_tab_model_rect_rejects_new_window_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "New window"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open in new tab.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "New window",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "GitHub - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "new_window_model_rect_rejects_new_tab_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "New tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open in new window.",
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "New tab",
                    "control_type": "button",
                    "rect": [20, 80, 100, 32],
                    "window_title": "GitHub - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_new_target_id_rejects_new_tab_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "New tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open new.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "New tab",
                    "control_type": "button",
                    "rect": [20, 80, 100, 32],
                    "window_title": "GitHub - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_new_model_rect_rejects_new_tab_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "New tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Create new.",
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "New tab",
                    "control_type": "button",
                    "rect": [20, 80, 100, 32],
                    "window_title": "GitHub - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_new_target_id_rejects_brave_new_tab_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "New tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open new.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "New tab",
                    "control_type": "button",
                    "rect": [20, 80, 100, 32],
                    "window_title": "Vidbox - Brave",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "external_link_action_rejects_chain_share_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "S"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open external link.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f517", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "archive_action_target_id_accepts_file_cabinet_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "A"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Archive item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f5c4", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "archive_action_target_id_accepts_file_cabinet_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "File cabinet"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Archive item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "File cabinet", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "archive_email_model_rect_rejects_unarchive_email_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 170, 32], "label": "Unarchive email"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Archive email.",
                "target": {"x": 20, "y": 80, "width": 170, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Unarchive email", "control_type": "button", "rect": [20, 80, 170, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "share_text_match_overrides_export_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "S"},
                {"rect": [180, 80, 100, 32], "label": "Export"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Share this item.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f517", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Export", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "archive_text_match_overrides_export_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "A"},
                {"rect": [180, 80, 100, 32], "label": "Export"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Archive item.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f5c4", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Export", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "send_action_target_id_accepts_submit_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Submit"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Send the message.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Submit", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "submit_action_target_id_accepts_send_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Send"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Submit the form.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Send", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "send_action_text_match_overrides_wrong_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Submit"},
                {"rect": [180, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Send the message.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Submit", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "send_action_alias_prefers_exact_send_over_submit_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Submit"},
                {"rect": [180, 80, 100, 32], "label": "Send"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Send the message.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Submit", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Send", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [180, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "send_action_target_id_accepts_paper_plane_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Paper plane"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Send message.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Paper plane", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "send_message_text_match_overrides_message_field_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Paper plane"},
                {"rect": [180, 80, 220, 32], "label": "Message"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Send message.",
                "target": {"x": 180, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Paper plane", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Message", "control_type": "edit", "rect": [180, 80, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "send_message_model_rect_rejects_delete_message_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Delete message"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Send message.",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Delete message", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "save_document_target_id_rejects_delete_document_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 170, 32], "label": "Delete document"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Save document.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Delete document", "control_type": "button", "rect": [20, 80, 170, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "save_card_target_id_rejects_save_profile_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Save profile"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Save card.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Save profile", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "save_document_model_rect_rejects_save_profile_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Save profile"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Save document.",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Save profile", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "archive_card_model_rect_rejects_archive_email_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Archive email"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Archive card.",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Archive email", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "save_action_target_id_accepts_floppy_disk_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Floppy disk"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Save document.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Floppy disk", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "floppy_disk_action_target_id_accepts_save_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the floppy disk.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "save_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "S"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Save document.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4be", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "save_text_match_overrides_cancel_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Floppy disk"},
                {"rect": [180, 80, 140, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Save document.",
                "target": {"x": 180, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Floppy disk", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "microphone_action_target_id_accepts_mic_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Mic"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute microphone.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Mic", "control_type": "button", "rect": [20, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "mute_speaker_target_id_rejects_muted_speaker_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Muted speaker"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute speaker.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Muted speaker", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "mute_speaker_target_id_rejects_unmuted_speaker_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Unmuted speaker"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute speaker.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Unmuted speaker", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "mute_microphone_target_id_rejects_unmute_microphone_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Unmute microphone"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute microphone.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Unmute microphone", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "start_recording_target_id_rejects_stop_recording_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Stop recording"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Start recording.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Stop recording", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "connect_account_target_id_rejects_disconnect_account_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 190, 32], "label": "Disconnect account"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Connect account.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Disconnect account", "control_type": "button", "rect": [20, 80, 190, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "mic_action_target_id_accepts_microphone_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Microphone"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute mic.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Microphone", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "microphone_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "M"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute microphone.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f3a4", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "audio_action_target_id_accepts_speaker_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Speaker"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute audio.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Speaker", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "speaker_action_target_id_accepts_sound_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 90, 32], "label": "Sound"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute speaker.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Sound", "control_type": "button", "rect": [20, 80, 90, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 90, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "volume_action_target_id_accepts_speaker_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Speaker"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open volume.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Speaker", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "increase_volume_target_id_accepts_volume_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Volume"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Increase volume.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Volume", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "increase_volume_target_id_rejects_decrease_volume_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Decrease volume"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Increase volume.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Decrease volume", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "volume_down_model_rect_rejects_volume_up_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Volume up"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Volume down.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Volume up", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "speaker_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "S"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute audio.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f50a", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "video_action_target_id_accepts_camera_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Camera"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Start video.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Camera", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "camera_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "C"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Start video.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4f7", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "start_video_rejects_taskbar_start_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 55, 40], "label": "Start"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Start video.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Start",
                    "control_type": "button",
                    "rect": [20, 80, 55, 40],
                    "automation_id": "StartButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "start_menu_accepts_taskbar_start_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 55, 40], "label": "Start"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Start menu.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Start",
                    "control_type": "button",
                    "rect": [20, 80, 55, 40],
                    "automation_id": "StartButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 55, 40],
                "overlay_emitted": True,
            },
        },
        {
            "name": "play_video_target_id_accepts_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "P"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Play video.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u25b6", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "pause_video_target_id_accepts_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "P"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pause video.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u23f8", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "stop_playback_target_id_accepts_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "S"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Stop playback.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u23f9", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "record_clip_target_id_accepts_symbol_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "R"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Record clip.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u23fa", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "resume_playback_target_id_accepts_play_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Play"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Resume playback.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Play", "control_type": "button", "rect": [20, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "play_video_text_match_overrides_video_settings_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "P"},
                {"rect": [180, 80, 160, 32], "label": "Video settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Play video.",
                "target": {"x": 180, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u25b6", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Video settings", "control_type": "button", "rect": [180, 80, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "pause_video_text_match_overrides_camera_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 90, 32], "label": "Pause"},
                {"rect": [180, 80, 100, 32], "label": "Camera"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pause video.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Pause", "control_type": "button", "rect": [20, 80, 90, 32]},
                {"id": "c002", "text": "Camera", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 90, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "edit_row_target_id_accepts_edit_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Edit"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Edit this row.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Edit", "control_type": "button", "rect": [20, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "edit_row_target_id_accepts_pencil_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 90, 32], "label": "Pencil"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Edit this row.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Pencil", "control_type": "button", "rect": [20, 80, 90, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 90, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "edit_row_target_id_accepts_pencil_symbol",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "E"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Edit this row.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u270f", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "edit_profile_target_id_rejects_view_profile_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "View profile"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Edit profile.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "View profile", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "edit_profile_model_rect_rejects_edit_button_in_message_row",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 40, 520, 72], "label": "Profile"},
                {"rect": [560, 60, 60, 32], "label": "Edit"},
                {"rect": [100, 120, 520, 72], "label": "Message from Alice"},
                {"rect": [560, 140, 60, 32], "label": "Edit"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Edit profile.",
                "target": {"x": 560, "y": 140, "width": 60, "height": 32},
            },
            "candidates": [
                {"id": "r1", "text": "Profile", "control_type": "listitem", "rect": [100, 40, 520, 72]},
                {"id": "p", "text": "Edit", "control_type": "button", "rect": [560, 60, 60, 32]},
                {"id": "r2", "text": "Message from Alice", "control_type": "listitem", "rect": [100, 120, 520, 72]},
                {"id": "m", "text": "Edit", "control_type": "button", "rect": [560, 140, 60, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "save_in_billing_card_rejects_uncontextualized_duplicate_save",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 0, 220, 80], "label": "Profile"},
                {"rect": [150, 24, 72, 32], "label": "Save"},
                {"rect": [100, 100, 220, 80], "label": "Billing"},
                {"rect": [150, 124, 72, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the Billing card.",
                "target": {"x": 150, "y": 24, "width": 72, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "button", "rect": [150, 24, 72, 32]},
                {"id": "c002", "text": "Save", "control_type": "button", "rect": [150, 124, 72, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "edit_action_text_match_overrides_name_field_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 90, 32], "label": "Pencil"},
                {"rect": [180, 80, 220, 32], "label": "Name"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Edit this row.",
                "target": {"x": 180, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Pencil", "control_type": "button", "rect": [20, 80, 90, 32]},
                {"id": "c002", "text": "Name", "control_type": "edit", "rect": [180, 80, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 90, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "literal_edit_control_target_id_stays_edit_field",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Edit"},
                {"rect": [180, 80, 220, 32], "label": "Name"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this edit control.",
                "target_id": "c002",
                "target": {"x": 180, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Edit", "control_type": "button", "rect": [20, 80, 80, 32]},
                {"id": "c002", "text": "Name", "control_type": "edit", "rect": [180, 80, 220, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c002",
                "rect": [180, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "microphone_text_match_overrides_audio_settings_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Mic"},
                {"rect": [180, 80, 140, 32], "label": "Audio settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute microphone.",
                "target": {"x": 180, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Mic", "control_type": "button", "rect": [20, 80, 80, 32]},
                {"id": "c002", "text": "Audio settings", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "audio_text_match_overrides_audio_settings_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Speaker"},
                {"rect": [180, 80, 140, 32], "label": "Audio settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute audio.",
                "target": {"x": 180, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Speaker", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Audio settings", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "audio_settings_exact_target_id_stays_settings",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Speaker"},
                {"rect": [180, 80, 140, 32], "label": "Audio settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open audio settings.",
                "target_id": "c002",
            },
            "candidates": [
                {"id": "c001", "text": "Speaker", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Audio settings", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c002",
                "rect": [180, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "audio_settings_rejects_taskbar_volume_status_snap",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Volume 24%"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open audio settings.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 240, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Volume Speakers (Realtek(R) Audio): 24%",
                    "control_type": "button",
                    "rect": [20, 80, 240, 32],
                    "automation_id": "SystemTrayIcon",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "lock_action_target_id_accepts_padlock_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 110, 32], "label": "Padlock"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Lock screen.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Padlock", "control_type": "button", "rect": [20, 80, 110, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "unlock_action_target_id_accepts_lock_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Lock"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Unlock account.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Lock", "control_type": "button", "rect": [20, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "security_action_target_id_accepts_shield_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 90, 32], "label": "Shield"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open security.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Shield", "control_type": "button", "rect": [20, 80, 90, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 90, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "lock_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "L"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Lock screen.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f512", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "lock_text_match_overrides_security_settings_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 110, 32], "label": "Padlock"},
                {"rect": [180, 80, 160, 32], "label": "Security settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Lock screen.",
                "target": {"x": 180, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Padlock", "control_type": "button", "rect": [20, 80, 110, 32]},
                {"id": "c002", "text": "Security settings", "control_type": "button", "rect": [180, 80, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "security_settings_exact_target_id_stays_settings",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 90, 32], "label": "Shield"},
                {"rect": [180, 80, 160, 32], "label": "Security settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open security settings.",
                "target_id": "c002",
            },
            "candidates": [
                {"id": "c001", "text": "Shield", "control_type": "button", "rect": [20, 80, 90, 32]},
                {"id": "c002", "text": "Security settings", "control_type": "button", "rect": [180, 80, 160, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c002",
                "rect": [180, 80, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "lock_icon_target_id_accepts_site_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the lock icon.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "padlock_icon_target_id_accepts_site_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the padlock icon.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "lock_screen_rejects_site_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Lock screen.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "security_action_rejects_site_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open security.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "site_settings_rejects_site_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open site settings.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "video_text_match_overrides_av_settings_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Camera"},
                {"rect": [180, 80, 150, 32], "label": "AV settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Start video.",
                "target": {"x": 180, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Camera", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "AV settings", "control_type": "button", "rect": [180, 80, 150, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "meeting_alias_rejects_ambiguous_mic_and_microphone_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "Mic"},
                {"rect": [180, 80, 140, 32], "label": "Microphone"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Mute microphone.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Mic", "control_type": "button", "rect": [20, 80, 80, 32]},
                {"id": "c002", "text": "Microphone", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "cart_action_target_id_accepts_basket_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Basket"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open cart.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Basket", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "basket_action_target_id_accepts_cart_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Cart"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open basket.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Cart", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "cart_action_target_id_accepts_shopping_bag_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 130, 32], "label": "Shopping bag"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open cart.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Shopping bag", "control_type": "button", "rect": [20, 80, 130, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 130, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "cart_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "C"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open cart.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f6d2", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "cart_action_text_match_overrides_shopping_options_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Basket"},
                {"rect": [180, 80, 160, 32], "label": "Shopping options"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open cart.",
                "target": {"x": 180, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Basket", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Shopping options", "control_type": "button", "rect": [180, 80, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "cart_alias_rejects_ambiguous_cart_and_basket_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Basket"},
                {"rect": [180, 80, 100, 32], "label": "Cart"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open cart.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Basket", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Cart", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "password_visibility_target_id_accepts_eye_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Password"},
                {"rect": [260, 80, 32, 32], "label": "Eye"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show password.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Eye", "control_type": "button", "rect": [260, 80, 32, 32]},
                {"id": "c002", "text": "Password", "control_type": "edit", "rect": [20, 80, 220, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [260, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "password_visibility_target_id_accepts_visibility_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Password"},
                {"rect": [260, 80, 90, 32], "label": "Visibility"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Hide password.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Visibility", "control_type": "button", "rect": [260, 80, 90, 32]},
                {"id": "c002", "text": "Password", "control_type": "edit", "rect": [20, 80, 220, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [260, 80, 90, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "password_eye_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Password"},
                {"rect": [260, 80, 32, 32], "label": "E"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show password.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f441", "control_type": "button", "rect": [260, 80, 32, 32]},
                {"id": "c002", "text": "Password", "control_type": "edit", "rect": [20, 80, 220, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [260, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "hide_password_target_id_rejects_show_password_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Password"},
                {"rect": [260, 80, 130, 32], "label": "Show password"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Hide password.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Show password", "control_type": "button", "rect": [260, 80, 130, 32]},
                {"id": "c002", "text": "Password", "control_type": "edit", "rect": [20, 80, 220, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "show_password_model_rect_rejects_hide_password_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Password"},
                {"rect": [260, 80, 130, 32], "label": "Hide password"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show password.",
                "target": {"x": 260, "y": 80, "width": 130, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Hide password", "control_type": "button", "rect": [260, 80, 130, 32]},
                {"id": "c002", "text": "Password", "control_type": "edit", "rect": [20, 80, 220, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "password_visibility_text_match_overrides_password_field_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Password"},
                {"rect": [260, 80, 32, 32], "label": "Eye"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show password.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Eye", "control_type": "button", "rect": [260, 80, 32, 32]},
                {"id": "c002", "text": "Password", "control_type": "edit", "rect": [20, 80, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [260, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "show_sidebar_does_not_match_eye_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Show sidebar"},
                {"rect": [260, 80, 32, 32], "label": "Eye"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show sidebar.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Eye", "control_type": "button", "rect": [260, 80, 32, 32]},
                {"id": "c002", "text": "Show sidebar", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [20, 80, 150, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "show_sidebar_model_rect_rejects_hide_sidebar_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Hide sidebar"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show sidebar.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Hide sidebar", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "show_sidebar_model_rect_rejects_visible_sidebar_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Visible sidebar"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show sidebar.",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Visible sidebar", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "show_sidebar_model_rect_rejects_hidden_sidebar_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Hidden sidebar"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show sidebar.",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Hidden sidebar", "control_type": "button", "rect": [20, 80, 160, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "hide_sidebar_model_rect_rejects_show_sidebar_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Show sidebar"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Hide sidebar.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Show sidebar", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "open_details_model_rect_rejects_close_details_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Close details"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open details.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Close details", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "open_details_model_rect_rejects_edit_details_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Edit details"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open details.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Edit details", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "open_account_model_rect_rejects_delete_account_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Delete account"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open account.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Delete account", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "click_account_model_rect_rejects_delete_account_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Delete account"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click account.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Delete account", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "search_account_model_rect_rejects_delete_account_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Delete account"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Search account.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Delete account", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "inspect_report_model_rect_rejects_download_report_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Download report"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Inspect report.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Download report", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "calendar_action_target_id_accepts_date_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Date"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open calendar.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Date", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "date_picker_target_id_accepts_calendar_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Calendar"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open date picker.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Calendar", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "calendar_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "D"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open calendar.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4c5", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "calendar_text_match_overrides_cancel_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Date"},
                {"rect": [180, 80, 140, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open calendar.",
                "target": {"x": 180, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Date", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "clock_action_target_id_accepts_time_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Time"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open clock.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Time", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "home_action_target_id_accepts_house_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "House"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go home.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "House", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "house_action_target_id_accepts_home_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Home"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the house.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Home", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "home_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "H"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go home.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f3e0", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "home_alias_rejects_ambiguous_home_and_house_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "House"},
                {"rect": [180, 80, 100, 32], "label": "Home"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go home.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "House", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Home", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "print_action_target_id_accepts_printer_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Printer"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Print document.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Printer", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "printer_action_target_id_accepts_print_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Print"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open printer.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Print", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "print_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "P"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Print document.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f5a8", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "print_action_text_match_overrides_cancel_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Printer"},
                {"rect": [180, 80, 140, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Print document.",
                "target": {"x": 180, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Printer", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "print_alias_rejects_ambiguous_print_and_printer_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Printer"},
                {"rect": [180, 80, 100, 32], "label": "Print"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Print document.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Printer", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Print", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "folder_action_target_id_accepts_directory_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Directory"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open folder.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Directory", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "directory_action_target_id_accepts_folder_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Folder"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open directory.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Folder", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "folder_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "F"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open folder.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4c1", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "folder_action_text_match_overrides_cancel_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Directory"},
                {"rect": [180, 80, 140, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open folder.",
                "target": {"x": 180, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Directory", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "folder_alias_rejects_ambiguous_folder_and_directory_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Directory"},
                {"rect": [180, 80, 100, 32], "label": "Folder"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open folder.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Directory", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Folder", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "favorite_action_target_id_accepts_star_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Star"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Favorite this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Star", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bookmark_action_target_id_accepts_star_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Star"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Bookmark this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Star", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "favorite_item_target_id_rejects_browser_bookmark_tab_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Bookmark this tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Favorite this item.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Bookmark this tab",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "GitHub - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bookmark_tab_target_id_accepts_browser_bookmark_tab_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Bookmark this tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Bookmark this tab.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Bookmark this tab",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "GitHub - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_settings_target_id_rejects_unnamed_openai_settings_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open settings.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://platform.openai.com/settings/organization/billing/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "specific_openai_settings_target_id_accepts_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open OpenAI settings.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://platform.openai.com/settings/organization/billing/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bare_unnamed_target_id_rejects_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open unnamed.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://github.com",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_unnamed_model_rect_rejects_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open unnamed.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://github.com",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "specific_openai_organization_settings_accepts_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open OpenAI organization settings.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://platform.openai.com/settings/organization/billing/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_settings_text_match_prefers_visible_settings_over_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
                {"rect": [300, 80, 100, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open settings.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://platform.openai.com/settings/organization/billing/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {"id": "c002", "text": "Settings", "control_type": "button", "rect": [300, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [300, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_settings_prefers_visible_settings_over_chrome_menu",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Chrome"},
                {"rect": [160, 80, 100, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open settings.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 32, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Chrome",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "Settings - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Settings",
                    "control_type": "button",
                    "rect": [160, 80, 100, 32],
                    "window_title": "Settings - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [160, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_tab_strip_rejects_in_app_tab_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 220, 40], "label": "Reports browser tab"},
                {"rect": [260, 110, 120, 32], "label": "Reports app tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the Reports tab in the app.",
                "target_id": "c001",
                "target": {"x": 20, "y": 20, "width": 220, "height": 40},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Reports - MyApp",
                    "control_type": "tabitem",
                    "rect": [20, 20, 220, 40],
                    "window_title": "MyApp - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Reports",
                    "control_type": "tabitem",
                    "rect": [260, 110, 120, 32],
                    "window_title": "MyApp - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [260, 110, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_home_rejects_sidebar_home_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [96, 20, 34, 34], "label": "Browser home"},
                {"rect": [260, 160, 180, 32], "label": "Sidebar home"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Home in the sidebar.",
                "target_id": "c001",
                "target": {"x": 96, "y": 20, "width": 34, "height": 34},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Home",
                    "control_type": "button",
                    "rect": [96, 20, 34, 34],
                    "automation_id": "home",
                    "window_title": "Reports - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Home",
                    "control_type": "listitem",
                    "rect": [260, 160, 180, 32],
                    "window_title": "Reports - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [260, 160, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_reload_rejects_dashboard_widget_refresh_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [96, 20, 34, 34], "label": "Browser reload"},
                {"rect": [420, 220, 100, 32], "label": "Widget refresh"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Refresh the dashboard widget.",
                "target_id": "c001",
                "target": {"x": 96, "y": 20, "width": 34, "height": 34},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Reload",
                    "control_type": "button",
                    "rect": [96, 20, 34, 34],
                    "automation_id": "reload",
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Refresh",
                    "control_type": "button",
                    "rect": [420, 220, 100, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [420, 220, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "moved_browser_reload_rejects_dashboard_widget_refresh_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [96, 108, 34, 34], "label": "Browser reload"},
                {"rect": [420, 320, 100, 32], "label": "Widget refresh"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Refresh the dashboard widget.",
                "target_id": "c001",
                "target": {"x": 96, "y": 108, "width": 34, "height": 34},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Reload",
                    "control_type": "button",
                    "rect": [96, 108, 34, 34],
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Refresh",
                    "control_type": "button",
                    "rect": [420, 320, 100, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [420, 320, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_reload_rejects_chart_refresh_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [96, 20, 34, 34], "label": "Browser reload"},
                {"rect": [420, 220, 100, 32], "label": "Chart refresh"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Refresh the chart.",
                "target_id": "c001",
                "target": {"x": 96, "y": 20, "width": 34, "height": 34},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Reload",
                    "control_type": "button",
                    "rect": [96, 20, 34, 34],
                    "automation_id": "reload",
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Refresh",
                    "control_type": "button",
                    "rect": [420, 220, 100, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [420, 220, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_profile_rejects_app_profile_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [936, 20, 32, 32], "label": "Browser profile"},
                {"rect": [420, 180, 110, 32], "label": "App profile"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Profile in the app.",
                "target_id": "c001",
                "target": {"x": 936, "y": 20, "width": 32, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "All",
                    "control_type": "button",
                    "rect": [936, 20, 32, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Profile",
                    "control_type": "button",
                    "rect": [420, 180, 110, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [420, 180, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_site_info_rejects_app_site_info_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [90, 20, 28, 34], "label": "Browser site info"},
                {"rect": [420, 180, 110, 32], "label": "App site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open site info in the app.",
                "target_id": "c001",
                "target": {"x": 90, "y": 20, "width": 28, "height": 34},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "site_info_lock",
                    "control_type": "button",
                    "rect": [90, 20, 28, 34],
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Site info",
                    "control_type": "button",
                    "rect": [420, 180, 110, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [420, 180, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "untitled_browser_site_info_rejects_app_site_info_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [90, 20, 28, 34], "label": "Browser site info"},
                {"rect": [420, 180, 110, 32], "label": "App site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open site info in the app.",
                "target_id": "c001",
                "target": {"x": 90, "y": 20, "width": 28, "height": 34},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "site_info_lock",
                    "control_type": "button",
                    "rect": [90, 20, 28, 34],
                },
                {
                    "id": "c002",
                    "text": "Site info",
                    "control_type": "button",
                    "rect": [420, 180, 110, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [420, 180, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "titlebar_minimize_rejects_app_panel_minimize_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [910, 0, 30, 30], "label": "Window minimize"},
                {"rect": [620, 140, 110, 32], "label": "Panel minimize"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Minimize the panel in the app.",
                "target_id": "c001",
                "target": {"x": 910, "y": 0, "width": 30, "height": 30},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Minimize",
                    "control_type": "button",
                    "rect": [910, 0, 30, 30],
                    "automation_id": "Minimize",
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Minimize panel",
                    "control_type": "button",
                    "rect": [620, 140, 110, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [620, 140, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "moved_titlebar_minimize_rejects_app_panel_minimize_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [910, 100, 30, 30], "label": "Window minimize"},
                {"rect": [620, 240, 110, 32], "label": "Panel minimize"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Minimize the panel in the app.",
                "target_id": "c001",
                "target": {"x": 910, "y": 100, "width": 30, "height": 30},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Minimize",
                    "control_type": "button",
                    "rect": [910, 100, 30, 30],
                    "automation_id": "Minimize",
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Minimize panel",
                    "control_type": "button",
                    "rect": [620, 240, 110, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [620, 240, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "taskbar_search_rejects_app_search_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [80, 955, 160, 40], "label": "Taskbar search"},
                {"rect": [300, 160, 240, 32], "label": "App search"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Search in the app.",
                "target_id": "c001",
                "target": {"x": 80, "y": 955, "width": 160, "height": 40},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Search",
                    "control_type": "button",
                    "rect": [80, 955, 160, 40],
                    "automation_id": "SearchGleamButton",
                    "window_title": "Taskbar",
                },
                {
                    "id": "c002",
                    "text": "Search",
                    "control_type": "edit",
                    "rect": [300, 160, 240, 32],
                    "window_title": "Dashboard",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [300, 160, 240, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_forward_rejects_wizard_navigation_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 34, 34], "label": "Browser forward"},
                {"rect": [420, 540, 110, 32], "label": "Wizard forward"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go forward in the wizard.",
                "target_id": "c001",
                "target": {"x": 20, "y": 20, "width": 34, "height": 34},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Forward",
                    "control_type": "button",
                    "rect": [20, 20, 34, 34],
                    "automation_id": "view_1002",
                    "window_title": "Onboarding - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Forward",
                    "control_type": "button",
                    "rect": [420, 540, 110, 32],
                    "window_title": "Onboarding - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [420, 540, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_settings_model_rect_rejects_unnamed_bookmark_snap",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open settings.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://platform.openai.com/settings/organization/billing/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_dashboard_target_id_rejects_unnamed_stripe_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open dashboard.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_page_target_id_rejects_unnamed_business_facebook_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open page.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://business.facebook.com/latest/?asset_id=1136461419546617&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_query_word_target_id_rejects_unnamed_business_facebook_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open asset.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://business.facebook.com/latest/?asset_id=1136461419546617&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_numeric_id_target_id_rejects_unnamed_business_facebook_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open 1136461419546617.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://business.facebook.com/latest/?asset_id=1136461419546617&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_page_model_rect_rejects_unnamed_business_facebook_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open page.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://business.facebook.com/latest/?asset_id=1136461419546617&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_folder_target_id_rejects_unnamed_privateemail_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open folder.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://privateemail.com/appsuite/#!!&app=io.ox/mail&folder=default0/INBOX",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_folder_model_rect_rejects_unnamed_privateemail_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open folder.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://privateemail.com/appsuite/#!!&app=io.ox/mail&folder=default0/INBOX",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "specific_facebook_page_accepts_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Facebook page"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Facebook page.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://business.facebook.com/latest/?asset_id=1136461419546617&nav_ref=manage_page_ap_plus_left_nav_mbs_button",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "specific_stripe_dashboard_accepts_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Stripe dashboard.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_platform_target_id_rejects_unnamed_openai_platform_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open platform.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://platform.openai.com/settings/organization/billing/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_organization_target_id_rejects_unnamed_openai_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open organization.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://platform.openai.com/settings/organization/billing/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "claude_platform_target_id_rejects_openai_platform_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Claude platform.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://platform.openai.com/settings/organization/billing/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "specific_claude_platform_accepts_matching_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Claude platform.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://platform.claude.com/workspaces/default/cost",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_cloud_target_id_rejects_unnamed_google_cloud_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open cloud.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://console.cloud.google.com/apis/credentials?project=gen-lang-client-0559993646",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_project_target_id_rejects_unnamed_google_cloud_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open project.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://console.cloud.google.com/apis/credentials?project=gen-lang-client-0559993646",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "specific_google_cloud_accepts_matching_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Google Cloud.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://console.cloud.google.com/apis/credentials?project=gen-lang-client-0559993646",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "specific_supabase_dashboard_rejects_stripe_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Supabase dashboard.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_bookmark_action_rejects_unnamed_url_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Bookmark this item.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://github.com",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "specific_github_bookmark_accepts_matching_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "GitHub bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open GitHub bookmark.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://github.com",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "specific_github_bookmark_rejects_stripe_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Stripe bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open GitHub bookmark.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_bookmark_text_match_prefers_star_over_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
                {"rect": [300, 80, 80, 32], "label": "Star"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Bookmark this item.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://github.com",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {"id": "c002", "text": "Star", "control_type": "button", "rect": [300, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [300, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "add_bookmark_recovers_from_new_tab_to_bookmark_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "New"},
                {"rect": [80, 80, 32, 32], "label": "Star"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Add bookmark.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "New Tab",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Bookmark this tab",
                    "control_type": "button",
                    "rect": [80, 80, 32, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [80, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_bookmark_model_rect_rejects_unnamed_bookmark_snap",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Bookmark this item.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://github.com",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_new_action_rejects_unnamed_new_url_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Claude new bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open new.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://claude.ai/new",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_app_action_rejects_unnamed_app_url_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Gemini app bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open app.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://gemini.google.com/app?utm_source=app_launcher&utm_medium=owned&utm_campaign=base_all",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "specific_gemini_app_accepts_matching_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Gemini app bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Gemini app.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://gemini.google.com/app?utm_source=app_launcher&utm_medium=owned&utm_campaign=base_all",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_new_rejects_new_tab_and_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Claude new bookmark"},
                {"rect": [300, 80, 100, 32], "label": "New Tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open new.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://claude.ai/new",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "New Tab",
                    "control_type": "button",
                    "rect": [300, 80, 100, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_app_model_rect_rejects_unnamed_app_url_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Gemini app bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open app.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://gemini.google.com/app?utm_source=app_launcher",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_closed_rejects_browser_group_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Limitless group"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open closed.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Limitless group - Closed",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "wrong_named_group_rejects_browser_group_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "AgenticField group"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Limitless group.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "AgenticField group - Closed",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "named_agenticfield_group_accepts_browser_group_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "AgenticField group"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open AgenticField group.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "AgenticField group - Closed",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "tab_groups_button_accepts_tab_groups_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Tab groups"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open tab groups.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Tab groups",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_closed_group_model_rect_rejects_browser_group_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Limitless group"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open closed group.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Limitless group - Closed",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bold_action_rejects_b2b_browser_group",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "B2B group"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open bold.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "B2B group - Closed",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bold_action_recovers_from_b2b_group_to_bold_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "B2B group"},
                {"rect": [240, 80, 80, 32], "label": "Bold"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open bold.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "B2B group - Closed",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {"id": "c002", "text": "Bold", "control_type": "button", "rect": [240, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [240, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_account_target_id_rejects_unnamed_account_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open account.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://www.name.com/account/domain/details/s2client.dev/dns",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_dashboard_text_match_prefers_visible_dashboard_over_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Bookmark"},
                {"rect": [300, 80, 180, 32], "label": "GitHub Dashboard"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open dashboard.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://dashboard.stripe.com/acct_1TQxqVCdMQikXj6B/balance/overview",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "GitHub Dashboard",
                    "control_type": "tabitem",
                    "rect": [300, 80, 180, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [300, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_access_rejects_extension_access_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Claude"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open access.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Open Claude\nWants access to this site",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "extension_status_has_rejects_extension_access_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Codex"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open has.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Codex\nHas access to this site",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "extension_status_has_model_rect_rejects_extension_access_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Codex"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click has.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Codex\nHas access to this site",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_access_button_model_rect_rejects_extension_access_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Codex"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click access button.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Codex\nHas access to this site",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "GitHub - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "specific_claude_access_accepts_extension_access_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Claude"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Grant Claude access.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Open Claude\nWants access to this site",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "claude_access_rejects_codex_extension_access_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Codex"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Grant Claude access.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Codex\nHas access to this site",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_site_rejects_site_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open site.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_view_rejects_site_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open view.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "automation_id": "view_1011",
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_view_model_rect_rejects_site_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open view.",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "automation_id": "view_1011",
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_view_rejects_brave_site_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open view.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                    "automation_id": "view_1011",
                    "window_title": "Vidbox - Brave",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "site_information_recovers_from_extension_access_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Claude"},
                {"rect": [300, 80, 160, 32], "label": "Site info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open site information.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Open Claude\nWants access to this site",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "View site information",
                    "control_type": "button",
                    "rect": [300, 80, 160, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [300, 80, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "favorite_symbol_target_id_accepts_star_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "*"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Favorite this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2606", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "favorite_action_text_match_overrides_wrong_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Star"},
                {"rect": [180, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Favorite this item.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Star", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "favorite_action_alias_rejects_ambiguous_favorite_and_star_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Star"},
                {"rect": [180, 80, 140, 32], "label": "Favorite"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Favorite this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Star", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Favorite", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bell_action_target_id_accepts_notifications_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the bell.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Notifications", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "notifications_action_target_id_accepts_bell_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Bell"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open notifications.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Bell", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "notification_bell_symbol_target_id_accepts_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "B"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open notifications.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f514", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bell_action_text_match_overrides_wrong_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Notifications"},
                {"rect": [180, 80, 100, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the bell.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Notifications", "control_type": "button", "rect": [20, 80, 140, 32]},
                {"id": "c002", "text": "Cancel", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "notification_alias_rejects_ambiguous_bell_and_notifications_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Bell"},
                {"rect": [180, 80, 140, 32], "label": "Notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open notifications.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Bell", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Notifications", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "system_tray_target_id_accepts_show_hidden_icons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Tray"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open system tray.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Hidden Icons",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "notification_area_target_id_accepts_show_hidden_icons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Tray"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open notification area.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Hidden Icons",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bare_hidden_rejects_show_hidden_icons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Tray"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open hidden.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Hidden Icons",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_icons_rejects_show_hidden_icons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Tray"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open icons.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Hidden Icons",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_hidden_model_rect_rejects_show_hidden_icons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Tray"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open hidden.",
                "target": {"x": 20, "y": 80, "width": 32, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Hidden Icons",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_icons_model_rect_rejects_show_hidden_icons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Tray"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open icons.",
                "target": {"x": 20, "y": 80, "width": 32, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Hidden Icons",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "notification_area_rejects_bell_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Bell"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open notification area.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Bell", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "notifications_rejects_show_hidden_icons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Tray"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open notifications.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Hidden Icons",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "show_history_rejects_show_hidden_icons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Tray"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show history.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show Hidden Icons",
                    "control_type": "button",
                    "rect": [20, 80, 32, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "wifi_target_id_accepts_taskbar_network_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Network"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Wi-Fi.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Network StarLink\nInternet access",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "wireless_target_id_accepts_taskbar_network_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Network"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open wireless.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Network StarLink\nInternet access",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "starlink_target_id_accepts_taskbar_network_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Network"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open StarLink.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Network StarLink\nInternet access",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "favorite_action_rejects_starlink_network_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Network"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Favorite this item.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Network StarLink\nInternet access",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bookmark_action_rejects_starlink_network_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Network"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Bookmark this.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Network StarLink\nInternet access",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "wifi_action_rejects_airplane_mode",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Airplane"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Wi-Fi.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Airplane mode",
                    "control_type": "button",
                    "rect": [20, 80, 140, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "button_control_suffix_model_rect_snaps_to_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Submit"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this button control.",
                "target": {"x": 20, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Submit", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "literal_edit_model_rect_snaps_to_edit",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 360, 32], "label": "Search"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this edit control.",
                "target": {"x": 20, "y": 80, "width": 360, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Search", "control_type": "edit", "rect": [20, 80, 360, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 360, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "toolbar_button_model_rect_snaps_to_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this toolbar button.",
                "target": {"x": 20, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "form_field_model_rect_snaps_to_edit",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 360, 32], "label": "Name"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this form field.",
                "target": {"x": 20, "y": 80, "width": 360, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Name", "control_type": "edit", "rect": [20, 80, 360, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 360, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "popup_menu_item_model_rect_snaps_to_menuitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 28], "label": "Open"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this popup menu item.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 28},
            },
            "candidates": [
                {"id": "c001", "text": "Open", "control_type": "menuitem", "rect": [20, 80, 180, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 180, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "sidebar_item_broad_group_rejects_multiple_listitems",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "General"},
                {"rect": [20, 120, 180, 32], "label": "Privacy"},
                {"rect": [20, 160, 180, 32], "label": "Billing"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this sidebar item.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 112},
            },
            "candidates": [
                {"id": "c001", "text": "General", "control_type": "listitem", "rect": [20, 80, 180, 32]},
                {"id": "c002", "text": "Privacy", "control_type": "listitem", "rect": [20, 120, 180, 32]},
                {"id": "c003", "text": "Billing", "control_type": "listitem", "rect": [20, 160, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "sidebar_item_target_id_rejects_browser_tabitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 220, 32], "label": "Settings - MyApp - Google Chrome"},
                {"rect": [20, 120, 180, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings sidebar item.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Settings - MyApp - Google Chrome",
                    "control_type": "tabitem",
                    "rect": [20, 20, 220, 32],
                    "window_title": "MyApp - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Settings",
                    "control_type": "listitem",
                    "rect": [20, 120, 180, 32],
                    "window_title": "MyApp - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [20, 120, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "modal_button_model_rect_snaps_to_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "OK"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this modal button.",
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "OK", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "table_row_model_rect_snaps_to_listitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 32], "label": "Order 123"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this table row.",
                "target": {"x": 20, "y": 80, "width": 300, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Order 123", "control_type": "listitem", "rect": [20, 80, 300, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rect": [20, 80, 300, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "table_row_broad_group_rejects_multiple_listitems",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 32], "label": "Order 1"},
                {"rect": [20, 120, 300, 32], "label": "Order 2"},
                {"rect": [20, 160, 300, 32], "label": "Order 3"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this table row.",
                "target": {"x": 20, "y": 80, "width": 300, "height": 112},
            },
            "candidates": [
                {"id": "c001", "text": "Order 1", "control_type": "listitem", "rect": [20, 80, 300, 32]},
                {"id": "c002", "text": "Order 2", "control_type": "listitem", "rect": [20, 120, 300, 32]},
                {"id": "c003", "text": "Order 3", "control_type": "listitem", "rect": [20, 160, 300, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "contextual_checkbox_row_model_rect_snaps_to_single_checkbox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 600, 80], "label": "Task row"},
                {"rect": [34, 110, 20, 20], "label": ""},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the checkbox in Task row.",
                "target": {"x": 20, "y": 80, "width": 600, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Task row", "control_type": "listitem", "rect": [20, 80, 600, 80]},
                {"id": "c002", "text": "Done", "control_type": "checkbox", "rect": [34, 110, 20, 20]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [34, 110, 20, 20],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_column_header_model_rect_snaps_to_header",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 50, 120, 28], "label": "Name"},
                {"rect": [140, 50, 120, 28], "label": "Status"},
                {"rect": [260, 50, 120, 28], "label": "Owner"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this column header.",
                "target": {"x": 140, "y": 50, "width": 120, "height": 28},
            },
            "candidates": [
                {"id": "c001", "text": "Name", "control_type": "headeritem", "rect": [20, 50, 120, 28]},
                {"id": "c002", "text": "Status", "control_type": "headeritem", "rect": [140, 50, 120, 28]},
                {"id": "c003", "text": "Owner", "control_type": "headeritem", "rect": [260, 50, 120, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c002",
                "rect": [140, 50, 120, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_column_header_broad_row_rejects_multiple_headers",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 50, 120, 28], "label": "Name"},
                {"rect": [140, 50, 120, 28], "label": "Status"},
                {"rect": [260, 50, 120, 28], "label": "Owner"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this column header.",
                "target": {"x": 20, "y": 50, "width": 360, "height": 28},
            },
            "candidates": [
                {"id": "c001", "text": "Name", "control_type": "headeritem", "rect": [20, 50, 120, 28]},
                {"id": "c002", "text": "Status", "control_type": "headeritem", "rect": [140, 50, 120, 28]},
                {"id": "c003", "text": "Owner", "control_type": "headeritem", "rect": [260, 50, 120, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "target_id_copied_wrong_geometry_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [120, 140, 90, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "target": {"x": 240, "y": 437, "width": 180, "height": 100},
            },
            "candidates": [
                {"id": "c001", "text": "Cancel", "control_type": "button", "rect": [120, 140, 90, 32]},
            ],
            "expected": {
                "source": "target_id",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "visible_text_conflict_automation_id_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [120, 140, 90, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "target": {"x": 240, "y": 437, "width": 180, "height": 100},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Cancel",
                    "automation_id": "saveButton",
                    "control_type": "button",
                    "rect": [120, 140, 90, 32],
                },
            ],
            "expected": {
                "source": "target_id",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "scaled_negative_origin_snaps_to_candidate",
            "capture": {"width": 500, "height": 320, "monitor_left": -1000, "monitor_top": 200, "scale": 0.5},
            "draw": [
                {"rect": [-800, 360, 120, 40], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this button.",
                "target": {"x": 190, "y": 240, "width": 140, "height": 82},
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "button", "rect": [-800, 360, 120, 40]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rect": [-800, 360, 120, 40],
                "overlay_emitted": True,
            },
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
        {
            "name": "noisy_model_rect_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"kind": "checker", "rect": [60, 80, 100, 50]},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this button.",
                "target": {"x": 120, "y": 250, "width": 200, "height": 156},
            },
            "candidates": [],
            "expected": {"source": "model", "quality_reason": "target appears visually noisy", "overlay_emitted": False},
        },
        {
            "name": "text_only_model_rect_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"kind": "text", "rect": [60, 80, 160, 30], "label": "Save changes now"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save changes.",
                "target": {"x": 120, "y": 250, "width": 320, "height": 93},
            },
            "candidates": [],
            "expected": {
                "source": "model",
                "quality_reason": "target lacks visible control boundary",
                "overlay_emitted": False,
            },
        },
        {
            "name": "compound_model_rect_over_multiple_buttons_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [60, 74, 80, 32], "label": "Save"},
                {"rect": [160, 74, 80, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target": {"x": 120, "y": 231, "width": 360, "height": 106},
            },
            "candidates": [],
            "expected": {
                "source": "model",
                "quality_reason": "target appears to contain multiple controls",
                "overlay_emitted": False,
            },
        },
        {
            "name": "splitbutton_menu_model_rect_snaps_to_menu_segment",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 180, 32], "label": "Export"},
                {"rect": [240, 100, 40, 32], "label": "v"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the Export menu.",
                "target": {"x": 100, "y": 100, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Export", "control_type": "splitbutton", "rect": [100, 100, 180, 32]},
                {"id": "c002", "text": "Export", "control_type": "button", "rect": [100, 100, 140, 32]},
                {"id": "c003", "text": "Export menu", "control_type": "menuitem", "rect": [240, 100, 40, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c003",
                "rect": [240, 100, 40, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "menu_launcher_target_id_highlights_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "More options"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the overflow menu.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "More options", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "menu_launcher_model_rect_highlights_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "More options"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the three dots menu.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "More options", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "contextual_profile_menu_model_rect_highlights_launcher_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Profile"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the profile menu.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Profile", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "contextual_account_dropdown_model_rect_highlights_launcher_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Account"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the account dropdown.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Account", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "profile_menu_model_rect_highlights_person_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Person"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the profile menu.",
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Person", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "profile_target_id_accepts_person_icon_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Person"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open profile.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f464", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "chrome_profile_name_target_id_accepts_profile_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 34, 34], "label": "A"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open profile menu.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Abel (All)",
                    "control_type": "button",
                    "rect": [20, 80, 34, 34],
                    "automation_id": "view_1018",
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 34, 34],
                "overlay_emitted": True,
            },
        },
        {
            "name": "profile_page_prefers_page_link_over_chrome_profile_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [700, 80, 40, 36], "label": "Profile 1"},
                {"rect": [100, 250, 120, 28], "label": "Profile page"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open profile page.",
                "target": {"x": 700, "y": 80, "width": 40, "height": 36},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Profile 1",
                    "control_type": "button",
                    "rect": [700, 80, 40, 36],
                    "window_title": "about:blank - Google Chrome",
                },
                {"id": "c002", "text": "Profile page", "control_type": "hyperlink", "rect": [100, 250, 120, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [100, 250, 120, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "profile_page_model_rect_rejects_chrome_profile_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [700, 80, 40, 36], "label": "Profile 1"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open profile page.",
                "target": {"x": 700, "y": 80, "width": 40, "height": 36},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Profile 1",
                    "control_type": "button",
                    "rect": [700, 80, 40, 36],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "profile_page_button_model_rect_rejects_chrome_profile_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [700, 80, 40, 36], "label": "Profile 1"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the profile page button.",
                "target": {"x": 700, "y": 80, "width": 40, "height": 36},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Profile 1",
                    "control_type": "button",
                    "rect": [700, 80, 40, 36],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "profile_name_inference_rejects_non_browser_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 34, 34], "label": "A"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open profile menu.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Abel (All)",
                    "control_type": "button",
                    "rect": [20, 80, 34, 34],
                    "window_title": "Contacts",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "profile_name_inference_rejects_all_token_in_unnamed_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 28, 28], "label": "Bookmark"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open account.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://gemini.google.com/app?utm_source=app_launcher&utm_medium=owned&utm_campaign=base_all",
                    "control_type": "button",
                    "rect": [20, 80, 28, 28],
                    "automation_id": "view_1028",
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "bare_all_rejects_browser_profile_all_hint",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 34, 34], "label": "A"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open all.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Abel (All)",
                    "control_type": "button",
                    "rect": [20, 80, 34, 34],
                    "automation_id": "view_1018",
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "chrome_profile_rejects_plain_chrome_toolbar_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 48, 32], "label": "Chrome"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Chrome profile.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Chrome",
                    "control_type": "button",
                    "rect": [20, 80, 48, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "profile_request_rejects_taskbar_chrome_app_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Chrome app"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open account.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Google Chrome - 5 running windows",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "chrome_profile_recovers_from_chrome_button_to_profile_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 48, 32], "label": "Chrome"},
                {"rect": [180, 80, 34, 34], "label": "A"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Chrome profile.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 48, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Chrome",
                    "control_type": "button",
                    "rect": [20, 80, 48, 32],
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Abel (All)",
                    "control_type": "button",
                    "rect": [180, 80, 34, 34],
                    "automation_id": "view_1018",
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [180, 80, 34, 34],
                "overlay_emitted": True,
            },
        },
        {
            "name": "user_menu_target_id_accepts_people_icon_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "People"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open user menu.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f465", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "chrome_profile_name_text_match_overrides_extensions_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 34, 34], "label": "A"},
                {"rect": [180, 80, 90, 32], "label": "Extensions"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open profile menu.",
                "target": {"x": 180, "y": 80, "width": 90, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Abel (All)",
                    "control_type": "button",
                    "rect": [20, 80, 34, 34],
                    "automation_id": "view_1018",
                    "window_title": "about:blank - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Extensions",
                    "control_type": "button",
                    "rect": [180, 80, 90, 32],
                    "window_title": "about:blank - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 34, 34],
                "overlay_emitted": True,
            },
        },
        {
            "name": "profile_person_icon_text_match_overrides_settings_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Person"},
                {"rect": [180, 80, 120, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open profile.",
                "target": {"x": 180, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f464", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Settings", "control_type": "button", "rect": [180, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "contextual_profile_menu_item_still_highlights_menuitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Profile"},
                {"rect": [20, 130, 200, 32], "label": "Profile"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the profile menu item.",
                "target": {"x": 20, "y": 130, "width": 200, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Profile", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "c002", "text": "Profile", "control_type": "menuitem", "rect": [20, 130, 200, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [20, 130, 200, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "confirm_alias_model_rect_highlights_ok_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 80, 32], "label": "OK"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Confirm.",
                "target": {"x": 20, "y": 80, "width": 80, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "OK", "control_type": "button", "rect": [20, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "symbol_question_mark_target_id_highlights_help_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "?"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the question mark.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "?", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "symbol_question_mark_text_match_overrides_wrong_help_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "?"},
                {"rect": [100, 80, 80, 32], "label": "Help"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the question mark.",
                "target": {"x": 100, "y": 80, "width": 80, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "?", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Help", "control_type": "button", "rect": [100, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "info_icon_target_id_highlights_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show info.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2139", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "about_icon_target_id_highlights_circled_information_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "About"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open about.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f6c8", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "info_icon_text_match_overrides_help_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Info"},
                {"rect": [100, 80, 80, 32], "label": "Help"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show info.",
                "target": {"x": 100, "y": 80, "width": 80, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u2139", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Help", "control_type": "button", "rect": [100, 80, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "info_action_rejects_question_mark_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "?"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show info.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "?", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "help_action_rejects_info_icon_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Info"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open help.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2139", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "pin_item_target_id_accepts_pushpin_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Pushpin"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pin this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Pushpin", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "pin_item_target_id_accepts_pushpin_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Pushpin"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pin this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4cc", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "unpin_item_target_id_accepts_pinned_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Pinned"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Unpin this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Pinned", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "unpin_action_target_id_rejects_pin_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Pin"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Unpin this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Pin", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "pin_action_model_rect_rejects_unpin_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Unpin"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pin this item.",
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Unpin", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "pin_icon_text_match_overrides_archive_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Pushpin"},
                {"rect": [180, 80, 100, 32], "label": "Archive"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pin this item.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4cc", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Archive", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "pin_action_rejects_ambiguous_pin_and_pushpin_buttons",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Pushpin"},
                {"rect": [180, 80, 80, 32], "label": "Pin"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pin this item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Pushpin", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "c002", "text": "Pin", "control_type": "button", "rect": [180, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "pin_action_rejects_taskbar_pinned_app_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Chrome pinned"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pin this item.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Google Chrome pinned",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "pin_named_app_rejects_taskbar_pinned_app_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Chrome pinned"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pin Google Chrome.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Google Chrome pinned",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_running_windows_rejects_taskbar_app_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Chrome running"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open running windows.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Google Chrome - 5 running windows",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "named_running_taskbar_app_still_highlights_app",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Chrome running"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Google Chrome.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Google Chrome - 5 running windows",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_onedrive_status_rejects_taskbar_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "OneDrive status"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open backed up.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "OneDrive - Personal\r\nBacked up and synced",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_onedrive_fragment_rejects_taskbar_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "OneDrive status"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open one.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "OneDrive - Personal\r\nBacked up and synced",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_taskbar_state_model_rect_rejects_app_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Chrome running"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open running windows.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Google Chrome - 5 running windows",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_onedrive_status_model_rect_rejects_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "OneDrive status"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open backed up.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "OneDrive - Personal\r\nBacked up and synced",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "named_onedrive_status_still_highlights_service",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "OneDrive status"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open OneDrive.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "OneDrive - Personal\r\nBacked up and synced",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_widget_temperature_rejects_taskbar_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Widgets 64F"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open 64.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Widgets 64\u00b0F Clear",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                    "automation_id": "WidgetsButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_power_remaining_rejects_taskbar_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Battery status"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open remaining.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Power Battery status: 80% remaining\r\nFully smart charged",
                    "control_type": "button",
                    "rect": [20, 80, 240, 32],
                    "automation_id": "SystemTrayIcon",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_volume_percent_model_rect_rejects_taskbar_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Volume 24%"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open 24%.",
                "target": {"x": 20, "y": 80, "width": 240, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Volume Speakers (Realtek(R) Audio): 24%",
                    "control_type": "button",
                    "rect": [20, 80, 240, 32],
                    "automation_id": "SystemTrayIcon",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_search_gleam_word_rejects_taskbar_search_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Search gleam"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open reef.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Search - World Reef Awareness Day",
                    "control_type": "button",
                    "rect": [20, 80, 240, 32],
                    "automation_id": "SearchGleamButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "localized_search_gleam_separator_rejects_zoom_out_alias",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Search gleam"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Zoom out.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "\u641c\u7d22 - \u4e16\u754c\u73ca\u745a\u7901\u65e5",
                    "control_type": "button",
                    "rect": [20, 80, 240, 32],
                    "automation_id": "SearchGleamButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "localized_label_separator_rejects_zoom_out_alias",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Localized label"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Zoom out.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "\u641c\u7d22 - \u4e16\u754c\u73ca\u745a\u7901\u65e5",
                    "control_type": "button",
                    "rect": [20, 80, 240, 32],
                    "window_title": "Browser",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "named_battery_status_still_highlights_power_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 240, 32], "label": "Battery status"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open battery.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Power Battery status: 80% remaining\r\nFully smart charged",
                    "control_type": "button",
                    "rect": [20, 80, 240, 32],
                    "automation_id": "SystemTrayIcon",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 240, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "email_target_id_accepts_envelope_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Envelope"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open email.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Envelope", "control_type": "button", "rect": [20, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "mail_target_id_accepts_envelope_icon",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Mail"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open mail.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2709", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "mail_target_id_accepts_gmail_tab",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 260, 32], "label": "Gmail tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open mail.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - Memory usage - 270 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 260, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 260, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "email_target_id_accepts_recibidos_gmail_tab",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 260, 32], "label": "Recibidos Gmail"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open email.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - Memory usage - 270 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 260, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 260, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "gmail_instruction_rejects_generic_mail_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Mail"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Gmail.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Mail",
                    "control_type": "button",
                    "rect": [20, 80, 100, 32],
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "gmail_instruction_rejects_email_address_account_tab",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 184, 32], "label": "Cloudflare account"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Gmail.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "DNS | Records | limitles.dev | Abelnavarrocarreon@gmail.com's Account | Cloudflare - Memory usage - 580 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 184, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "gmail_text_match_recovers_from_email_address_account_tab",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 184, 32], "label": "Cloudflare account"},
                {"rect": [240, 80, 184, 32], "label": "Gmail tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Gmail.",
                "target": {"x": 20, "y": 80, "width": 184, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "DNS | Records | limitles.dev | Abelnavarrocarreon@gmail.com's Account | Cloudflare - Memory usage - 580 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 184, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
                {
                    "id": "c002",
                    "text": "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - Memory usage - 270 MB",
                    "control_type": "tabitem",
                    "rect": [240, 80, 184, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [240, 80, 184, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "tab_owner_account_segment_rejects_generic_account_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Cloudflare tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Account tab.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "DNS | Records | limitles.dev | Abelnavarrocarreon@gmail.com's Account | Cloudflare - Memory usage - 580 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "tab_owner_account_segment_rejects_generic_account_snap",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Cloudflare tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open account.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "DNS | Records | limitles.dev | Abelnavarrocarreon@gmail.com's Account | Cloudflare - Memory usage - 580 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "tab_owner_account_segment_keeps_cloudflare_title_words",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Cloudflare tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Cloudflare tab.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "DNS | Records | limitles.dev | Abelnavarrocarreon@gmail.com's Account | Cloudflare - Memory usage - 580 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_tab_login_title_rejects_generic_login_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Mercury login tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Log in.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Log In | Mercury - Memory usage - 372 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "browser_tab_login_title_rejects_generic_login_snap",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Mercury login tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open login.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Log In | Mercury - Memory usage - 372 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "browser_tab_login_title_keeps_mercury_tab_wording",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Mercury login tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Mercury tab.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Log In | Mercury - Memory usage - 372 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_tab_home_title_rejects_generic_home_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Stripe home tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open home.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Home - Limitless - Stripe - Memory usage - 687 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "browser_tab_overview_title_rejects_generic_overview_snap",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "OpenAI overview tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open overview.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Billing overview - OpenAI API - Memory usage - 195 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "browser_tab_overview_title_keeps_openai_api_tab_wording",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "OpenAI overview tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open OpenAI API tab.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Billing overview - OpenAI API - Memory usage - 195 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "tab_memory_usage_suffix_rejects_generic_memory_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Stripe tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open memory.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Home - Limitless - Stripe - Memory usage - 687 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "tab_memory_usage_suffix_rejects_generic_memory_snap",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Stripe tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open memory.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Home - Limitless - Stripe - Memory usage - 687 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "decimal_tab_memory_usage_suffix_rejects_generic_usage_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "OpenAI API tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open usage.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Billing overview - OpenAI API - Memory usage - 99.2 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "decimal_tab_memory_usage_suffix_rejects_generic_mb_snap",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "OpenAI API tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open MB.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Billing overview - OpenAI API - Memory usage - 99.2 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "tab_memory_usage_suffix_keeps_real_tab_title_words",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Stripe tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Stripe.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Home - Limitless - Stripe - Memory usage - 687 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 220, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "gmail_tab_target_id_wins_over_mail_decoys",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 184, 32], "label": "Cloudflare account"},
                {"rect": [240, 80, 184, 32], "label": "Gmail tab"},
                {"rect": [460, 80, 28, 28], "label": "Private mail"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Gmail.",
                "target_id": "c010",
            },
            "candidates": [
                {
                    "id": "c006",
                    "text": "DNS | Records | limitles.dev | Abelnavarrocarreon@gmail.com's Account | Cloudflare - Memory usage - 580 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 184, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
                {
                    "id": "c010",
                    "text": "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - Memory usage - 270 MB",
                    "control_type": "tabitem",
                    "rect": [240, 80, 184, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
                {
                    "id": "c042",
                    "text": "Unnamed bookmark for https://privateemail.com/appsuite/#!!&app=io.ox/mail&folder=default0/INBOX",
                    "control_type": "button",
                    "rect": [460, 80, 28, 28],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c010",
                "rect": [240, 80, 184, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "mail_target_id_gmail_tab_wins_over_privateemail_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 184, 32], "label": "Gmail tab"},
                {"rect": [240, 80, 28, 28], "label": "Private mail"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open mail.",
                "target_id": "c010",
            },
            "candidates": [
                {
                    "id": "c010",
                    "text": "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - Memory usage - 270 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 184, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
                {
                    "id": "c042",
                    "text": "Unnamed bookmark for https://privateemail.com/appsuite/#!!&app=io.ox/mail&folder=default0/INBOX",
                    "control_type": "button",
                    "rect": [240, 80, 28, 28],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c010",
                "rect": [20, 80, 184, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "gmail_instruction_rejects_privateemail_bookmark",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 28, 28], "label": "Private mail"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Gmail.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Unnamed bookmark for https://privateemail.com/appsuite/#!!&app=io.ox/mail&folder=default0/INBOX",
                    "control_type": "button",
                    "rect": [20, 80, 28, 28],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "type_email_rejects_gmail_tab",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 260, 32], "label": "Gmail tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Type your email.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Recibidos (3.921) - abelvalencianacarreon@gmail.com - Gmail - Memory usage - 270 MB",
                    "control_type": "tabitem",
                    "rect": [20, 80, 260, 32],
                    "window_title": "GitHub Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id control type mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "email_icon_text_match_overrides_settings_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Email"},
                {"rect": [180, 80, 120, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open email.",
                "target": {"x": 180, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u2709", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Settings", "control_type": "button", "rect": [180, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "paste_action_rejects_envelope_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Mail"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Paste into the note.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2709", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "type_email_rejects_envelope_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Mail"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Type your email.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2709", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id control type mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "symbol_plus_target_id_highlights_add_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "+"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Add a new item.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "+", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "navigation_back_target_id_accepts_left_arrow_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Back"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go back.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2190", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "navigation_forward_target_id_accepts_right_arrow_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Forward"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go forward.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2192", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "navigation_forward_target_id_rejects_next_track_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Next track"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go forward.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Next track", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "navigation_back_model_rect_rejects_previous_song_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 150, 32], "label": "Previous song"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go back.",
                "target": {"x": 20, "y": 80, "width": 150, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Previous song", "control_type": "button", "rect": [20, 80, 150, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "media_next_track_target_id_accepts_next_track_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Next track"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Next track.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Next track", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "navigation_back_text_match_overrides_undo_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Back"},
                {"rect": [180, 80, 100, 32], "label": "Undo"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go back.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u2190", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Undo", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "navigation_forward_text_match_overrides_redo_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Forward"},
                {"rect": [180, 80, 100, 32], "label": "Redo"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go forward.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\u2192", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Redo", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "undo_action_rejects_back_arrow_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Back"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Undo last change.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u2190", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "navigation_back_rejects_undo_arrow_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Undo"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go back.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u21b6", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "symbol_ellipsis_target_id_highlights_more_options_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "..."},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the more options menu.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "...", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "disclosure_target_id_accepts_right_triangle_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Expand"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Expand Advanced settings.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u25b8", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "disclosure_target_id_accepts_down_triangle_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Collapse"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Collapse Advanced settings.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\u25be", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "disclosure_expand_target_id_rejects_collapse_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Collapse Advanced settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Expand Advanced settings.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Collapse Advanced settings",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "disclosure_expand_target_id_rejects_expanded_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Expanded Advanced settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Expand Advanced settings.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Expanded Advanced settings",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "disclosure_collapse_target_id_rejects_expand_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Expand Advanced settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Collapse Advanced settings.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Expand Advanced settings",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "disclosure_expand_candidate_snap_rejects_collapse_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Collapse Advanced settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Expand Advanced settings.",
                "target": {"x": 20, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Collapse Advanced settings",
                    "control_type": "button",
                    "rect": [20, 80, 220, 32],
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "disclosure_icon_text_match_overrides_row_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 500, 80], "label": "Advanced settings"},
                {"rect": [478, 106, 28, 28], "label": "Expand"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Expand Advanced settings.",
                "target": {"x": 20, "y": 80, "width": 500, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Advanced settings", "control_type": "listitem", "rect": [20, 80, 500, 80]},
                {"id": "c002", "text": "\u25b8", "control_type": "button", "rect": [478, 106, 28, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [478, 106, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "disclosure_broad_row_highlights_chevron_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 500, 80], "label": "Advanced settings"},
                {"rect": [478, 106, 28, 28], "label": ">"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the chevron.",
                "target": {"x": 20, "y": 80, "width": 500, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Advanced settings", "control_type": "listitem", "rect": [20, 80, 500, 80]},
                {"id": "c002", "text": "Expand", "control_type": "button", "rect": [478, 106, 28, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [478, 106, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_pay_action_promotes_contained_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 800, 40], "label": "INV-001 Acme Pending"},
                {"rect": [730, 84, 60, 30], "label": "Pay"},
                {"rect": [20, 140, 800, 40], "label": "INV-002 Beta Pending"},
                {"rect": [730, 144, 60, 30], "label": "Pay"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Pay for Beta invoice row.",
                "target": {"x": 20, "y": 140, "width": 800, "height": 40},
            },
            "candidates": [
                {"id": "r1", "text": "INV-001 Acme Pending", "control_type": "listitem", "rect": [20, 80, 800, 40]},
                {"id": "pay1", "text": "Pay", "control_type": "button", "rect": [730, 84, 60, 30]},
                {"id": "r2", "text": "INV-002 Beta Pending", "control_type": "listitem", "rect": [20, 140, 800, 40]},
                {"id": "pay2", "text": "Pay", "control_type": "button", "rect": [730, 144, 60, 30]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "pay2",
                "rect": [730, 144, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_clear_email_uses_requested_row_over_wrong_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 760, 56], "label": "Alice"},
                {"rect": [120, 92, 300, 32], "label": "Email"},
                {"rect": [390, 94, 28, 28], "label": "Clear"},
                {"rect": [20, 156, 760, 56], "label": "Bob"},
                {"rect": [120, 168, 300, 32], "label": "Email"},
                {"rect": [390, 170, 28, 28], "label": "Clear"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clear the Email field in Bob row.",
                "target_id": "alice_clear",
                "target": {"x": 390, "y": 94, "width": 28, "height": 28},
            },
            "candidates": [
                {"id": "alice_row", "text": "Alice", "control_type": "listitem", "rect": [20, 80, 760, 56]},
                {"id": "alice_email", "text": "Email", "control_type": "edit", "rect": [120, 92, 300, 32]},
                {"id": "alice_clear", "text": "Clear", "control_type": "button", "rect": [390, 94, 28, 28]},
                {"id": "bob_row", "text": "Bob", "control_type": "listitem", "rect": [20, 156, 760, 56]},
                {"id": "bob_email", "text": "Email", "control_type": "edit", "rect": [120, 168, 300, 32]},
                {"id": "bob_clear", "text": "Clear", "control_type": "button", "rect": [390, 170, 28, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "bob_clear",
                "rect": [390, 170, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_clear_email_accepts_row_name_after_container",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 760, 56], "label": "Alice"},
                {"rect": [120, 92, 300, 32], "label": "Email"},
                {"rect": [390, 94, 28, 28], "label": "Clear"},
                {"rect": [20, 156, 760, 56], "label": "Bob"},
                {"rect": [120, 168, 300, 32], "label": "Email"},
                {"rect": [390, 170, 28, 28], "label": "Clear"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Clear the Email field in row Bob.",
                "target_id": "alice_clear",
                "target": {"x": 390, "y": 94, "width": 28, "height": 28},
            },
            "candidates": [
                {"id": "alice_row", "text": "Alice", "control_type": "listitem", "rect": [20, 80, 760, 56]},
                {"id": "alice_email", "text": "Email", "control_type": "edit", "rect": [120, 92, 300, 32]},
                {"id": "alice_clear", "text": "Clear", "control_type": "button", "rect": [390, 94, 28, 28]},
                {"id": "bob_row", "text": "Bob", "control_type": "listitem", "rect": [20, 156, 760, 56]},
                {"id": "bob_email", "text": "Email", "control_type": "edit", "rect": [120, 168, 300, 32]},
                {"id": "bob_clear", "text": "Clear", "control_type": "button", "rect": [390, 170, 28, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "bob_clear",
                "rect": [390, 170, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_numeric_action_target_id_uses_context_over_wrong_model_rect",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 800, 40], "label": "Order 1"},
                {"rect": [730, 84, 80, 30], "label": "Archive"},
                {"rect": [20, 140, 800, 40], "label": "Order 2"},
                {"rect": [730, 144, 80, 30], "label": "Archive"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Archive in Order 2 row.",
                "target_id": "a2",
                "target": {"x": 20, "y": 80, "width": 800, "height": 40},
            },
            "candidates": [
                {"id": "r1", "text": "Order 1", "control_type": "listitem", "rect": [20, 80, 800, 40]},
                {"id": "a1", "text": "Archive", "control_type": "button", "rect": [730, 84, 80, 30]},
                {"id": "r2", "text": "Order 2", "control_type": "listitem", "rect": [20, 140, 800, 40]},
                {"id": "a2", "text": "Archive", "control_type": "button", "rect": [730, 144, 80, 30]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "a2",
                "rect": [730, 144, 80, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_letter_action_target_id_uses_context_over_wrong_model_rect",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 120], "label": "Project A"},
                {"rect": [230, 160, 80, 30], "label": "Archive"},
                {"rect": [360, 80, 300, 120], "label": "Project B"},
                {"rect": [570, 160, 80, 30], "label": "Archive"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Archive on Project B list item.",
                "target_id": "a2",
                "target": {"x": 20, "y": 80, "width": 300, "height": 120},
            },
            "candidates": [
                {"id": "r1", "text": "Project A", "control_type": "listitem", "rect": [20, 80, 300, 120]},
                {"id": "a1", "text": "Archive", "control_type": "button", "rect": [230, 160, 80, 30]},
                {"id": "r2", "text": "Project B", "control_type": "listitem", "rect": [360, 80, 300, 120]},
                {"id": "a2", "text": "Archive", "control_type": "button", "rect": [570, 160, 80, 30]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "a2",
                "rect": [570, 160, 80, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "same_label_modal_button_uses_geometry_over_foreground_rank",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 80, 32], "label": "Save"},
                {"rect": [400, 240, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Save button in the modal.",
                "target": {"x": 360, "y": 200, "width": 260, "height": 120},
            },
            "candidates": [
                {
                    "id": "bg",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 80, 32],
                    "window_title": "Editor",
                    "window_rank": 0,
                },
                {
                    "id": "modal",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [400, 240, 80, 32],
                    "window_title": "Save changes",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "modal",
                "rect": [400, 240, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "same_label_dialog_button_uses_context_over_wrong_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 80, 32], "label": "OK"},
                {"rect": [400, 240, 80, 32], "label": "OK"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click OK in the dialog.",
                "target_id": "dialog",
                "target": {"x": 100, "y": 100, "width": 80, "height": 32},
            },
            "candidates": [
                {
                    "id": "bg",
                    "text": "OK",
                    "control_type": "button",
                    "rect": [100, 100, 80, 32],
                    "window_title": "Settings",
                    "window_rank": 0,
                },
                {
                    "id": "dialog",
                    "text": "OK",
                    "control_type": "button",
                    "rect": [400, 240, 80, 32],
                    "window_title": "Preferences dialog",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "dialog",
                "rect": [400, 240, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "launcher_wording_rejects_same_label_menuitem_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 100, 32], "label": "Settings launcher"},
                {"rect": [200, 80, 160, 28], "label": "Settings menu item"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings launcher.",
                "target_id": "launcher",
                "target": {"x": 200, "y": 80, "width": 160, "height": 28},
            },
            "candidates": [
                {"id": "launcher", "text": "Settings", "control_type": "button", "rect": [20, 20, 100, 32], "window_title": "Start"},
                {"id": "item", "text": "Settings", "control_type": "menuitem", "rect": [200, 80, 160, 28], "window_title": "Settings menu"},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "launcher",
                "rect": [20, 20, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "selector_wrong_target_id_recovers_to_combobox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Country"},
                {"rect": [280, 80, 100, 32], "label": "Country"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the Country selector.",
                "target_id": "c002",
                "target": {"x": 280, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Country", "control_type": "combobox", "rect": [20, 80, 220, 32]},
                {"id": "c002", "text": "Country", "control_type": "button", "rect": [280, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "selector_plain_button_rejects_overlay",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [280, 80, 100, 32], "label": "Country"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the Country selector.",
                "target": {"x": 280, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Country", "control_type": "button", "rect": [280, 80, 100, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "date_picker_model_rect_highlights_launcher_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Date"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the Date picker.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Date", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "file_picker_target_id_highlights_launcher_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Choose file"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the File picker.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Choose file", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "upload_file_model_rect_highlights_browse_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Browse"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Upload a file.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Browse", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "open_file_model_rect_rejects_save_file_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Save file"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open file.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Save file", "control_type": "button", "rect": [20, 80, 120, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "attach_document_target_id_highlights_choose_file_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Choose file"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Attach a document.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Choose file", "control_type": "button", "rect": [20, 80, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "attach_file_target_id_highlights_paperclip_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Paperclip"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Attach file.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4ce", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "add_attachment_target_id_highlights_linked_paperclips_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Attachment"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Add attachment.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f587", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "attach_file_text_match_overrides_upload_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Paperclip"},
                {"rect": [180, 80, 100, 32], "label": "Upload"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Attach file.",
                "target": {"x": 180, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4ce", "control_type": "button", "rect": [20, 80, 32, 32]},
                {"id": "c002", "text": "Upload", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "paste_action_rejects_paperclip_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Paperclip"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Paste into the note.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4ce", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "attach_file_rejects_clipboard_alias_collision",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 32, 32], "label": "Clipboard"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Attach file.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "\U0001f4cb", "control_type": "button", "rect": [20, 80, 32, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "attach_file_rejects_taskbar_file_explorer_pinned",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "File Explorer"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Attach file.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "File Explorer pinned",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "attach_file_rejects_taskbar_file_explorer_app",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "File Explorer"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Attach file.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "File Explorer",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "attach_file_model_rect_rejects_taskbar_file_explorer_pinned",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "File Explorer"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Attach file.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "File Explorer pinned",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "named_taskbar_app_still_highlights_file_explorer",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "File Explorer"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click File Explorer.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "File Explorer pinned",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_view_rejects_tradingview_taskbar_app",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "TradingView"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open view.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "TradingView pinned",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_task_rejects_task_view_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Task View"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open task.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Task View",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "automation_id": "TaskViewButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_view_does_not_recover_to_task_view_over_tradingview",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "TradingView"},
                {"rect": [240, 80, 120, 32], "label": "Task View"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open view.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "TradingView pinned",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
                {
                    "id": "c002",
                    "text": "Task View",
                    "control_type": "button",
                    "rect": [240, 80, 120, 32],
                    "automation_id": "TaskViewButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_task_model_rect_rejects_task_view",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Task View"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open task.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Task View",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "automation_id": "TaskViewButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "generic_view_button_model_rect_rejects_task_view_contained_fallback",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Task View"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click view button.",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Task View",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "automation_id": "TaskViewButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "named_task_view_still_highlights_taskbar_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Task View"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Task View.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Task View",
                    "control_type": "button",
                    "rect": [20, 80, 120, 32],
                    "automation_id": "TaskViewButton",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "named_tradingview_still_highlights_taskbar_app",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "TradingView"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open TradingView.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "TradingView pinned",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "phone_link_phrase_accepts_taskbar_app",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Phone Link"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open phone link.",
                "target_id": "c001",
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Phone Link pinned",
                    "control_type": "button",
                    "rect": [20, 80, 180, 32],
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "splitbutton_dropdown_model_rect_snaps_to_menu_segment",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 180, 32], "label": "Export"},
                {"rect": [240, 100, 40, 32], "label": "v"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the Export drop down.",
                "target": {"x": 100, "y": 100, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Export", "control_type": "splitbutton", "rect": [100, 100, 180, 32]},
                {"id": "c002", "text": "Export", "control_type": "button", "rect": [100, 100, 140, 32]},
                {"id": "c003", "text": "Export menu", "control_type": "menuitem", "rect": [240, 100, 40, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c003",
                "rect": [240, 100, 40, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "blank_candidate_rect_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "button", "rect": [80, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "quality_reason": "target appears visually empty",
                "overlay_emitted": False,
            },
        },
        {
            "name": "out_of_range_model_rect_does_not_draw_edge_overlay",
            "capture": {"width": 500, "height": 320},
            "decision": {
                "kind": "step",
                "instruction": "Click here.",
                "target": {"x": 1200, "y": 40, "width": 80, "height": 40},
            },
            "candidates": [],
            "expected": {
                "source": "none",
                "rejected_reason": "no resolvable target",
                "overlay_emitted": False,
            },
        },
        {
            "name": "overflowing_model_rect_does_not_draw_edge_overlay",
            "capture": {"width": 500, "height": 320},
            "decision": {
                "kind": "step",
                "instruction": "Click the edge.",
                "target": {"x": 980, "y": 990, "width": 80, "height": 50},
            },
            "candidates": [],
            "expected": {
                "source": "none",
                "rejected_reason": "no resolvable target",
                "overlay_emitted": False,
            },
        },
        {
            "name": "panel_sized_candidate_rejects_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 20, 450, 120], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings.",
                "target_id": "c001",
            },
            "candidates": [
                {"id": "c001", "text": "Settings", "control_type": "listitem", "rect": [20, 20, 450, 120]},
            ],
            "expected": {
                "source": "target_id",
                "quality_reason": "target too large",
                "overlay_emitted": False,
            },
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
        label = str(item.get("label") or "")
        if item.get("kind") == "text":
            if label:
                draw.text((box[0], box[1]), label, fill=item.get("fill", "black"))
            continue
        if item.get("kind") == "checker":
            tile = max(2, int(item.get("tile", 4)))
            for y in range(box[1], box[3], tile):
                for x in range(box[0], box[2], tile):
                    fill = "black" if ((x + y) // tile) % 2 else "white"
                    draw.rectangle(
                        (x, y, min(x + tile - 1, box[2] - 1), min(y + tile - 1, box[3] - 1)),
                        fill=fill,
                    )
            continue
        draw.rectangle(box, outline="black", fill=item.get("fill", "#f8fafc"), width=1)
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
        window_rank=int(item.get("window_rank") or 0),
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
