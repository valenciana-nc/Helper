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
