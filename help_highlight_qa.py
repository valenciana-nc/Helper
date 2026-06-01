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
