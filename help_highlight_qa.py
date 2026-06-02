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
            "name": "stale_save_as_target_id_recovers_to_exact_save",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [10, 10, 100, 32], "label": "Save as"},
                {"rect": [10, 60, 100, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "stale",
                "target": {"x": 10, "y": 10, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "stale", "text": "Save as", "control_type": "button", "rect": [10, 10, 100, 32]},
                {"id": "exact", "text": "Save", "control_type": "button", "rect": [10, 60, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "exact",
                "rect": [10, 60, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "stale_search_filters_target_id_recovers_to_exact_search",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [10, 10, 140, 32], "label": "Search filters"},
                {"rect": [10, 60, 100, 32], "label": "Search"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Search.",
                "target_id": "stale",
                "target": {"x": 10, "y": 10, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "stale", "text": "Search filters", "control_type": "button", "rect": [10, 10, 140, 32]},
                {"id": "exact", "text": "Search", "control_type": "button", "rect": [10, 60, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "exact",
                "rect": [10, 60, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "stale_open_file_target_id_recovers_to_exact_open",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [10, 10, 140, 32], "label": "Open file"},
                {"rect": [10, 60, 100, 32], "label": "Open"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Open.",
                "target_id": "stale",
                "target": {"x": 10, "y": 10, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "stale", "text": "Open file", "control_type": "button", "rect": [10, 10, 140, 32]},
                {"id": "exact", "text": "Open", "control_type": "button", "rect": [10, 60, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "exact",
                "rect": [10, 60, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "stale_read_later_target_id_recovers_to_exact_read",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [10, 10, 140, 32], "label": "Read later"},
                {"rect": [10, 60, 100, 32], "label": "Read"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Read.",
                "target_id": "stale",
                "target": {"x": 10, "y": 10, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "stale", "text": "Read later", "control_type": "button", "rect": [10, 10, 140, 32]},
                {"id": "exact", "text": "Read", "control_type": "button", "rect": [10, 60, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "exact",
                "rect": [10, 60, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "close_notification_recovers_from_open_notification_target_id",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [100, 100, 160, 32], "label": "Open notification"},
                {"rect": [320, 100, 160, 32], "label": "Close notification"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close notification.",
                "target_id": "open_notification",
                "target": {"x": 100, "y": 100, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "open_notification", "text": "Open notification", "control_type": "button", "rect": [100, 100, 160, 32]},
                {"id": "close_notification", "text": "Close notification", "control_type": "button", "rect": [320, 100, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "close_notification",
                "rect": [320, 100, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "follow_project_recovers_from_unfollow_project_target_id",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 80, 200, 32], "label": "Unfollow project"},
                {"rect": [20, 140, 200, 32], "label": "Follow project"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Follow project",
                "target_id": "wrong",
                "target": {"x": 20, "y": 80, "width": 200, "height": 32},
            },
            "candidates": [
                {"id": "wrong", "text": "Unfollow project", "control_type": "button", "rect": [20, 80, 200, 32]},
                {"id": "exact", "text": "Follow project", "control_type": "button", "rect": [20, 140, 200, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "exact",
                "rect": [20, 140, 200, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "restore_file_recovers_from_delete_file_target_id",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 80, 200, 32], "label": "Delete file"},
                {"rect": [20, 140, 200, 32], "label": "Restore file"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Restore file",
                "target_id": "wrong",
                "target": {"x": 20, "y": 80, "width": 200, "height": 32},
            },
            "candidates": [
                {"id": "wrong", "text": "Delete file", "control_type": "button", "rect": [20, 80, 200, 32]},
                {"id": "exact", "text": "Restore file", "control_type": "button", "rect": [20, 140, 200, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "exact",
                "rect": [20, 140, 200, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "open_wrong_target_id_recovers_from_publish_action",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 20, 120, 32], "label": "Publish project"},
                {"rect": [180, 20, 120, 32], "label": "Open project"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open project.",
                "target_id": "publish",
                "target": {"x": 20, "y": 20, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "publish", "text": "Publish project", "control_type": "button", "rect": [20, 20, 120, 32]},
                {"id": "open", "text": "Open project", "control_type": "button", "rect": [180, 20, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "open",
                "rect": [180, 20, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "open_wrong_target_id_recovers_from_deploy_action",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 20, 140, 32], "label": "Deploy report"},
                {"rect": [180, 20, 120, 32], "label": "Open report"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open report.",
                "target_id": "deploy",
                "target": {"x": 20, "y": 20, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "deploy", "text": "Deploy report", "control_type": "button", "rect": [20, 20, 140, 32]},
                {"id": "open", "text": "Open report", "control_type": "button", "rect": [180, 20, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "open",
                "rect": [180, 20, 120, 32],
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
            "name": "unlabeled_target_id_rejects_cross_role_visible_alternative",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [80, 80, 32, 32], "label": ""},
                {"rect": [180, 80, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Save button.",
                "target_id": "c001",
                "target": {"x": 160, "y": 250, "width": 64, "height": 100},
            },
            "candidates": [
                {"id": "c001", "text": "", "control_type": "button", "rect": [80, 80, 32, 32]},
                {"id": "c002", "text": "Save", "control_type": "hyperlink", "rect": [180, 80, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
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
            "name": "destructive_action_rejects_neutral_destination_button",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [100, 100, 120, 32], "label": "Project settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Delete project.",
                "target": {"x": 100, "y": 100, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "settings", "text": "Project settings", "control_type": "button", "rect": [100, 100, 120, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "destructive_action_rejects_neutral_options_button",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [100, 100, 140, 32], "label": "Project options"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Delete project.",
                "target": {"x": 100, "y": 100, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "options", "text": "Project options", "control_type": "button", "rect": [100, 100, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "destructive_action_rejects_object_destination_button",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [100, 100, 140, 32], "label": "Project billing"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Delete project.",
                "target": {"x": 100, "y": 100, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "billing", "text": "Project billing", "control_type": "button", "rect": [100, 100, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "open_destination_rejects_different_destination_button",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [100, 100, 160, 32], "label": "Project dashboard"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open project settings.",
                "target": {"x": 100, "y": 100, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "dashboard", "text": "Project dashboard", "control_type": "button", "rect": [100, 100, 160, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "open_report_rejects_side_effect_action_button",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [100, 100, 140, 32], "label": "Deploy report"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open report.",
                "target": {"x": 100, "y": 100, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "deploy", "text": "Deploy report", "control_type": "button", "rect": [100, 100, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "context_text_exact_geometry_rejects_wrong_button",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 20, 120, 32], "label": "Advanced"},
                {"rect": [20, 90, 100, 32], "label": "Filters"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Filters button in Advanced search.",
                "target": {"x": 20, "y": 20, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "context", "text": "Advanced", "control_type": "button", "rect": [20, 20, 120, 32]},
                {"id": "target", "text": "Filters", "control_type": "button", "rect": [20, 90, 100, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "action_label_with_context_suffix_overrides_exact_context_geometry",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 20, 170, 32], "label": "Team settings"},
                {"rect": [20, 90, 100, 32], "label": "Invite"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Invite - Team settings.",
                "target": {"x": 20, "y": 20, "width": 170, "height": 32},
            },
            "candidates": [
                {"id": "context", "text": "Team settings", "control_type": "button", "rect": [20, 20, 170, 32]},
                {"id": "target", "text": "Invite", "control_type": "button", "rect": [20, 90, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "target",
                "rect": [20, 90, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "action_label_with_prepositional_context_resolves_duplicate_heading",
            "capture": {"width": 520, "height": 240},
            "draw": [
                {"rect": [20, 20, 170, 32], "label": "Team settings"},
                {"rect": [20, 90, 100, 32], "label": "Invite"},
                {"rect": [250, 20, 190, 32], "label": "Account settings"},
                {"rect": [250, 90, 100, 32], "label": "Invite"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Invite in Team settings.",
                "target_id": "team_settings",
                "target": {"x": 20, "y": 20, "width": 170, "height": 32},
            },
            "candidates": [
                {"id": "team_settings", "text": "Team settings", "control_type": "button", "rect": [20, 20, 170, 32]},
                {"id": "team_invite", "text": "Invite", "control_type": "button", "rect": [20, 90, 100, 32]},
                {"id": "acct_settings", "text": "Account settings", "control_type": "button", "rect": [250, 20, 190, 32]},
                {"id": "acct_invite", "text": "Invite", "control_type": "button", "rect": [250, 90, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "team_invite",
                "rect": [20, 90, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "action_label_for_clickable_row_label_resolves_duplicate_action",
            "capture": {"width": 520, "height": 240},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Alice"},
                {"rect": [240, 80, 80, 32], "label": "Open"},
                {"rect": [20, 130, 120, 32], "label": "Bob"},
                {"rect": [240, 130, 80, 32], "label": "Open"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Open for Bob.",
                "target_id": "bob_label",
                "target": {"x": 20, "y": 130, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "alice_label", "text": "Alice", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "alice_open", "text": "Open", "control_type": "button", "rect": [240, 80, 80, 32]},
                {"id": "bob_label", "text": "Bob", "control_type": "button", "rect": [20, 130, 120, 32]},
                {"id": "bob_open", "text": "Open", "control_type": "button", "rect": [240, 130, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "bob_open",
                "rect": [240, 130, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_tab_role_recovers_from_same_label_listitem",
            "capture": {"width": 500, "height": 240},
            "draw": [
                {"rect": [20, 20, 160, 32], "label": "Billing"},
                {"rect": [20, 70, 160, 32], "label": "Billing"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select the Billing tab.",
                "target_id": "wrong",
                "target": {"x": 20, "y": 20, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "wrong", "text": "Billing", "control_type": "listitem", "rect": [20, 20, 160, 32]},
                {"id": "expected", "text": "Billing", "control_type": "tabitem", "rect": [20, 70, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "expected",
                "rect": [20, 70, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dropdown_option_rejects_combobox_launcher_only",
            "capture": {"width": 500, "height": 240},
            "draw": [
                {"rect": [20, 20, 160, 32], "label": "Canada"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select Canada dropdown option.",
                "target_id": "wrong",
                "target": {"x": 20, "y": 20, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "wrong", "text": "Canada", "control_type": "combobox", "rect": [20, 20, 160, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "wrong",
                "rejected_reason": "target_id control type mismatch",
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
            "name": "broad_same_type_button_recovers_to_tight_child_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 600, 80], "label": "Settings"},
                {"rect": [540, 104, 70, 28], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings.",
                "target_id": "c001",
                "target": {"x": 20, "y": 80, "width": 600, "height": 80},
            },
            "candidates": [
                {"id": "c001", "text": "Settings", "control_type": "button", "rect": [20, 80, 600, 80]},
                {"id": "c002", "text": "Settings", "control_type": "button", "rect": [540, 104, 70, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [540, 104, 70, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "broad_toolbar_recovers_to_tight_save_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 300, 60], "label": "Save"},
                {"rect": [250, 34, 60, 28], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save.",
                "target_id": "c001",
                "target": {"x": 20, "y": 20, "width": 300, "height": 60},
            },
            "candidates": [
                {"id": "c001", "text": "Save", "control_type": "toolbar", "rect": [20, 20, 300, 60]},
                {"id": "c002", "text": "Save", "control_type": "button", "rect": [250, 34, 60, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c002",
                "rect": [250, 34, 60, 28],
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
            "name": "turn_on_action_recovers_from_opposite_stale_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 190, 32], "label": "Turn off notifications"},
                {"rect": [20, 124, 190, 32], "label": "Turn on notifications"},
                {"rect": [260, 80, 180, 32], "label": "Notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Turn on notifications.",
                "target_id": "off",
                "target": {"x": 20, "y": 80, "width": 190, "height": 32},
            },
            "candidates": [
                {"id": "off", "text": "Turn off notifications", "control_type": "button", "rect": [20, 80, 190, 32]},
                {"id": "on", "text": "Turn on notifications", "control_type": "button", "rect": [20, 124, 190, 32]},
                {"id": "checkbox", "text": "Notifications", "control_type": "checkbox", "rect": [260, 80, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "on",
                "rect": [20, 124, 190, 32],
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
            "name": "spinner_increment_target_id_accepts_adjacent_stepper_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 120, 32], "label": "Quantity"},
                {"rect": [224, 100, 24, 16], "label": "+"},
                {"rect": [224, 116, 24, 16], "label": "-"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Increase the Quantity spinner.",
                "target_id": "up",
                "target": {"x": 224, "y": 100, "width": 24, "height": 16},
            },
            "candidates": [
                {"id": "spin", "text": "Quantity", "control_type": "spinner", "rect": [100, 100, 120, 32]},
                {"id": "up", "text": "Increase", "control_type": "button", "rect": [224, 100, 24, 16]},
                {"id": "down", "text": "Decrease", "control_type": "button", "rect": [224, 116, 24, 16]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "up",
                "rect": [224, 100, 24, 16],
                "overlay_emitted": True,
            },
        },
        {
            "name": "spinner_increment_without_target_id_recovers_adjacent_stepper_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 120, 32], "label": "Quantity"},
                {"rect": [224, 100, 24, 16], "label": "+"},
                {"rect": [224, 116, 24, 16], "label": "-"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Increase the Quantity spinner.",
                "target": {"x": 100, "y": 100, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "spin", "text": "Quantity", "control_type": "spinner", "rect": [100, 100, 120, 32]},
                {"id": "up", "text": "Increase", "control_type": "button", "rect": [224, 100, 24, 16]},
                {"id": "down", "text": "Decrease", "control_type": "button", "rect": [224, 116, 24, 16]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "up",
                "rect": [224, 100, 24, 16],
                "overlay_emitted": True,
            },
        },
        {
            "name": "text_field_wording_rejects_spinner_target",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Name"},
                {"rect": [20, 120, 160, 32], "label": "Retries"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Type in this text field.",
                "target_id": "spin",
                "target": {"x": 20, "y": 120, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "edit", "text": "Name", "control_type": "edit", "rect": [20, 80, 160, 32]},
                {"id": "spin", "text": "Retries", "control_type": "spinner", "rect": [20, 120, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "edit",
                "rect": [20, 80, 160, 32],
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
            "name": "page_address_wording_recovers_from_browser_address_bar",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [88, 8, 520, 34], "label": "Address and search bar"},
                {"rect": [120, 260, 300, 36], "label": "Address"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the address field on the page.",
                "target_id": "browser_address",
                "target": {"x": 88, "y": 8, "width": 520, "height": 34},
            },
            "candidates": [
                {
                    "id": "browser_address",
                    "text": "Address and search bar",
                    "control_type": "edit",
                    "rect": [88, 8, 520, 34],
                    "automation_id": "address and search bar",
                    "window_title": "Checkout - Google Chrome",
                },
                {
                    "id": "page_address",
                    "text": "Address",
                    "control_type": "edit",
                    "rect": [120, 260, 300, 36],
                    "window_title": "Checkout - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "page_address",
                "rect": [120, 260, 300, 36],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_address_wording_recovers_from_page_address_field",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [88, 8, 520, 34], "label": "Address and search bar"},
                {"rect": [120, 260, 300, 36], "label": "Address"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Focus the address bar in Chrome.",
                "target_id": "page_address",
                "target": {"x": 120, "y": 260, "width": 300, "height": 36},
            },
            "candidates": [
                {
                    "id": "browser_address",
                    "text": "Address and search bar",
                    "control_type": "edit",
                    "rect": [88, 8, 520, 34],
                    "automation_id": "address and search bar",
                    "window_title": "Checkout - Google Chrome",
                },
                {
                    "id": "page_address",
                    "text": "Address",
                    "control_type": "edit",
                    "rect": [120, 260, 300, 36],
                    "window_title": "Checkout - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "browser_address",
                "rect": [88, 8, 520, 34],
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
            "name": "fill_text_entry_wrong_target_id_recovers_to_edit",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 220, 32], "label": "Email"},
                {"rect": [300, 80, 90, 32], "label": "Email"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Fill email address.",
                "target_id": "c002",
                "target": {"x": 300, "y": 80, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Email", "control_type": "edit", "rect": [20, 80, 220, 32]},
                {"id": "c002", "text": "Email", "control_type": "button", "rect": [300, 80, 90, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c001",
                "rect": [20, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "text_entry_label_target_recovers_to_adjacent_empty_edit",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [80, 100, 80, 24], "label": "Username"},
                {"rect": [180, 94, 280, 36], "label": ""},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Type into Username.",
                "target_id": "label",
                "target": {"x": 80, "y": 100, "width": 80, "height": 24},
            },
            "candidates": [
                {"id": "label", "text": "Username", "control_type": "text", "rect": [80, 100, 80, 24]},
                {"id": "field", "text": "", "control_type": "edit", "rect": [180, 94, 280, 36]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "field",
                "rect": [180, 94, 280, 36],
                "overlay_emitted": True,
            },
        },
        {
            "name": "paste_into_search_field_recovers_from_toolbar_paste_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [120, 160, 500, 40], "label": "Search"},
                {"rect": [20, 20, 90, 32], "label": "Paste"},
                {"rect": [586, 166, 28, 28], "label": "Clear"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Paste into the search field.",
                "target_id": "paste",
                "target": {"x": 20, "y": 20, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "search", "text": "Search", "control_type": "edit", "rect": [120, 160, 500, 40]},
                {"id": "paste", "text": "Paste", "control_type": "button", "rect": [20, 20, 90, 32]},
                {"id": "clear", "text": "Clear", "control_type": "button", "rect": [586, 166, 28, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "search",
                "rect": [120, 160, 500, 40],
                "overlay_emitted": True,
            },
        },
        {
            "name": "paste_into_message_recovers_from_toolbar_paste_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [120, 160, 500, 40], "label": "Message"},
                {"rect": [20, 20, 90, 32], "label": "Paste"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Paste into message.",
                "target_id": "paste",
                "target": {"x": 20, "y": 20, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "message", "text": "Message", "control_type": "edit", "rect": [120, 160, 500, 40]},
                {"id": "paste", "text": "Paste", "control_type": "button", "rect": [20, 20, 90, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "message",
                "rect": [120, 160, 500, 40],
                "overlay_emitted": True,
            },
        },
        {
            "name": "paste_into_chat_recovers_from_toolbar_paste_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [120, 160, 500, 40], "label": "Chat"},
                {"rect": [20, 20, 90, 32], "label": "Paste"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Paste into chat.",
                "target_id": "paste",
                "target": {"x": 20, "y": 20, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "chat", "text": "Chat", "control_type": "edit", "rect": [120, 160, 500, 40]},
                {"id": "paste", "text": "Paste", "control_type": "button", "rect": [20, 20, 90, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "chat",
                "rect": [120, 160, 500, 40],
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
            "name": "uncheck_option_rejects_same_label_radiobutton",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 160, 32], "label": "Weekly"},
                {"rect": [100, 150, 160, 32], "label": "Weekly"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Uncheck the Weekly option.",
                "target_id": "radio",
                "target": {"x": 100, "y": 150, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "check", "text": "Weekly", "control_type": "checkbox", "rect": [100, 100, 160, 32]},
                {"id": "radio", "text": "Weekly", "control_type": "radiobutton", "rect": [100, 150, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "check",
                "rect": [100, 100, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_radio_rejects_same_label_combobox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 160, 32], "label": "Weekly"},
                {"rect": [100, 150, 160, 32], "label": "Weekly"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select Weekly radio.",
                "target_id": "combo",
                "target": {"x": 100, "y": 150, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "radio", "text": "Weekly", "control_type": "radiobutton", "rect": [100, 100, 160, 32]},
                {"id": "combo", "text": "Weekly", "control_type": "combobox", "rect": [100, 150, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "radio",
                "rect": [100, 100, 160, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_option_rejects_same_label_combobox_launcher",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 160, 32], "label": "State dropdown"},
                {"rect": [100, 150, 160, 32], "label": "State option"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select the State option.",
                "target_id": "combo",
                "target": {"x": 100, "y": 100, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "combo", "text": "State", "control_type": "combobox", "rect": [100, 100, 160, 32]},
                {"id": "radio", "text": "State", "control_type": "radiobutton", "rect": [100, 150, 160, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "radio",
                "rect": [100, 150, 160, 32],
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
            "name": "explicit_checkbox_wording_recovers_from_state_action_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 180, 32], "label": "Notifications"},
                {"rect": [320, 100, 190, 32], "label": "Enable notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Enable notifications checkbox.",
                "target_id": "button",
                "target": {"x": 320, "y": 100, "width": 190, "height": 32},
            },
            "candidates": [
                {"id": "checkbox", "text": "Notifications", "control_type": "checkbox", "rect": [100, 100, 180, 32]},
                {"id": "button", "text": "Enable notifications", "control_type": "button", "rect": [320, 100, 190, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "checkbox",
                "rect": [100, 100, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_toggle_wording_recovers_from_state_action_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 180, 32], "label": "Notifications"},
                {"rect": [320, 100, 190, 32], "label": "Enable notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Enable notifications toggle.",
                "target_id": "button",
                "target": {"x": 320, "y": 100, "width": 190, "height": 32},
            },
            "candidates": [
                {"id": "checkbox", "text": "Notifications", "control_type": "checkbox", "rect": [100, 100, 180, 32]},
                {"id": "button", "text": "Enable notifications", "control_type": "button", "rect": [320, 100, 190, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "checkbox",
                "rect": [100, 100, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_toggle_in_toolbar_recovers_from_same_label_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 180, 32], "label": "Notifications toggle"},
                {"rect": [100, 150, 180, 32], "label": "Notifications button"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Notifications toggle in the toolbar.",
                "target_id": "button",
                "target": {"x": 100, "y": 150, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "toggle", "text": "Notifications", "control_type": "checkbox", "rect": [100, 100, 180, 32]},
                {"id": "button", "text": "Notifications", "control_type": "button", "rect": [100, 150, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "toggle",
                "rect": [100, 100, 180, 32],
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
            "name": "enable_notifications_target_id_rejects_turn_off_notifications_checkbox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 210, 32], "label": "Turn off notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Enable notifications.",
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
            "name": "activate_notifications_recovers_from_deactivate_notifications_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 170, 32], "label": "Enable notifications"},
                {"rect": [20, 130, 190, 32], "label": "Deactivate notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Activate notifications.",
                "target_id": "deactivate_notifications",
                "target": {"x": 20, "y": 130, "width": 190, "height": 32},
            },
            "candidates": [
                {"id": "enable_notifications", "text": "Enable notifications", "control_type": "button", "rect": [20, 80, 170, 32]},
                {"id": "deactivate_notifications", "text": "Deactivate notifications", "control_type": "button", "rect": [20, 130, 190, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "enable_notifications",
                "rect": [20, 80, 170, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "check_in_guest_recovers_from_check_out_guest_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Check in guest"},
                {"rect": [20, 130, 190, 32], "label": "Check out guest"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Check in guest.",
                "target_id": "check_out_guest",
                "target": {"x": 20, "y": 130, "width": 190, "height": 32},
            },
            "candidates": [
                {"id": "check_in_guest", "text": "Check in guest", "control_type": "button", "rect": [20, 80, 180, 32]},
                {"id": "check_out_guest", "text": "Check out guest", "control_type": "button", "rect": [20, 130, 190, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "check_in_guest",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "activate_account_recovers_from_active_account_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 140, 32], "label": "Active account"},
                {"rect": [100, 150, 32, 32], "label": "Activate"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Activate account.",
                "target_id": "status",
                "target": {"x": 100, "y": 100, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "status", "text": "Active account", "control_type": "button", "rect": [100, 100, 140, 32]},
                {"id": "activate", "text": "", "control_type": "button", "rect": [100, 150, 32, 32], "automation_id": "ActivateButton"},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "activate",
                "rect": [100, 150, 32, 32],
                "overlay_emitted": True,
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
            "name": "filter_orders_model_rect_rejects_filtered_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Orders filtered"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Filter orders.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Orders filtered", "control_type": "button", "rect": [20, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "c001",
                "rejected_reason": "candidate semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "sort_orders_model_rect_rejects_sorted_status_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Orders sorted"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Sort orders.",
                "target": {"x": 20, "y": 80, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "c001", "text": "Orders sorted", "control_type": "button", "rect": [20, 80, 180, 32]},
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
            "name": "create_exact_neighbor_recovers_from_add_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Add"},
                {"rect": [180, 80, 140, 32], "label": "Create"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Create item.",
                "target_id": "add",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "add", "text": "Add", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "create", "text": "Create", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "create",
                "rect": [180, 80, 140, 32],
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
            "name": "sign_out_exact_neighbor_recovers_from_logout_all_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Logout all sessions"},
                {"rect": [220, 80, 120, 32], "label": "Sign out"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Sign out.",
                "target_id": "logout_all",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {
                    "id": "logout_all",
                    "text": "Logout all sessions",
                    "control_type": "button",
                    "rect": [20, 80, 160, 32],
                },
                {"id": "signout", "text": "Sign out", "control_type": "button", "rect": [220, 80, 120, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "signout",
                "rect": [220, 80, 120, 32],
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
            "name": "combobox_labeled_dropdown_arrow_recovers_from_full_combo_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 220, 32], "label": "Country"},
                {"rect": [292, 100, 28, 32], "label": "v"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Country dropdown arrow.",
                "target_id": "combo1",
                "target": {"x": 100, "y": 100, "width": 220, "height": 32},
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
            "name": "generic_dropdown_broad_rect_with_multiple_comboboxes_stays_ambiguous",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 200, 32], "label": "Country"},
                {"rect": [10, 50, 200, 32], "label": "State"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open this dropdown.",
                "target_id": "state",
                "target": {"x": 10, "y": 10, "width": 200, "height": 72},
            },
            "candidates": [
                {"id": "country", "text": "Country", "control_type": "combobox", "rect": [10, 10, 200, 32]},
                {"id": "state", "text": "State", "control_type": "combobox", "rect": [10, 50, 200, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "country",
                "rejected_reason": "ambiguous candidate snap",
                "overlay_emitted": False,
            },
        },
        {
            "name": "named_dropdown_recovers_from_wrong_combobox_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 200, 32], "label": "Status"},
                {"rect": [10, 50, 200, 32], "label": "Priority"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Priority dropdown.",
                "target_id": "status",
                "target": {"x": 10, "y": 10, "width": 200, "height": 32},
            },
            "candidates": [
                {"id": "status", "text": "Status", "control_type": "combobox", "rect": [10, 10, 200, 32]},
                {"id": "priority", "text": "Priority", "control_type": "combobox", "rect": [10, 50, 200, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "priority",
                "rect": [10, 50, 200, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "textbox_wording_rejects_same_label_combobox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 220, 32], "label": "Email"},
                {"rect": [100, 150, 220, 32], "label": "Email"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Type in the Email textbox.",
                "target_id": "combo",
                "target": {"x": 100, "y": 150, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "edit", "text": "Email", "control_type": "edit", "rect": [100, 100, 220, 32]},
                {"id": "combo", "text": "Email", "control_type": "combobox", "rect": [100, 150, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "edit",
                "rect": [100, 100, 220, 32],
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
            "name": "chrome_settings_text_match_prefers_button_over_tab_title",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [80, 20, 220, 40], "label": "Settings - Google Chrome"},
                {"rect": [500, 120, 100, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Chrome settings.",
                "target": {"x": 500, "y": 120, "width": 100, "height": 32},
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
                    "id": "settings",
                    "text": "Settings",
                    "control_type": "button",
                    "rect": [500, 120, 100, 32],
                    "window_title": "Settings - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "settings",
                "rect": [500, 120, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_history_rejects_browser_tab_title_for_history_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [80, 20, 220, 40], "label": "History - Google Chrome"},
                {"rect": [500, 120, 100, 32], "label": "History"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open history.",
                "target_id": "tab",
                "target": {"x": 80, "y": 20, "width": 220, "height": 40},
            },
            "candidates": [
                {
                    "id": "tab",
                    "text": "History - Google Chrome",
                    "control_type": "tabitem",
                    "rect": [80, 20, 220, 40],
                    "window_title": "History - Google Chrome",
                },
                {
                    "id": "button",
                    "text": "History",
                    "control_type": "button",
                    "rect": [500, 120, 100, 32],
                    "automation_id": "history",
                    "window_title": "History - Google Chrome",
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
            "name": "generic_extensions_rejects_browser_tab_title_for_extensions_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [80, 20, 240, 40], "label": "Extensions - Google Chrome"},
                {"rect": [500, 120, 120, 32], "label": "Extensions"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open extensions.",
                "target_id": "tab",
                "target": {"x": 80, "y": 20, "width": 240, "height": 40},
            },
            "candidates": [
                {
                    "id": "tab",
                    "text": "Extensions - Google Chrome",
                    "control_type": "tabitem",
                    "rect": [80, 20, 240, 40],
                    "window_title": "Extensions - Google Chrome",
                },
                {
                    "id": "button",
                    "text": "Extensions",
                    "control_type": "button",
                    "rect": [500, 120, 120, 32],
                    "automation_id": "extensions",
                    "window_title": "Extensions - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "button",
                "rect": [500, 120, 120, 32],
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
            "name": "share_selected_text_recovers_from_browser_share_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [120, 160, 80, 32], "label": "Share"},
                {"rect": [760, 8, 42, 34], "label": "Share page"},
                {"rect": [220, 210, 460, 260], "label": "Body text"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Share selected text.",
                "target_id": "browser_share",
                "target": {"x": 760, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {"id": "editor_share", "text": "Share", "control_type": "button", "rect": [120, 160, 80, 32]},
                {
                    "id": "browser_share",
                    "text": "Share this page",
                    "control_type": "button",
                    "rect": [760, 8, 42, 34],
                    "automation_id": "share",
                    "window_title": "Chrome",
                },
                {"id": "body", "text": "Body text", "control_type": "edit", "rect": [220, 210, 460, 260]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "editor_share",
                "rect": [120, 160, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "share_selected_text_recovers_from_generic_chrome_share_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [120, 160, 80, 32], "label": "Share"},
                {"rect": [760, 8, 42, 34], "label": "Share"},
                {"rect": [220, 210, 460, 260], "label": "Body text"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Share selected text.",
                "target_id": "chrome_share",
                "target": {"x": 760, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {"id": "editor_share", "text": "Share", "control_type": "button", "rect": [120, 160, 80, 32]},
                {
                    "id": "chrome_share",
                    "text": "Share",
                    "control_type": "button",
                    "rect": [760, 8, 42, 34],
                    "automation_id": "share",
                    "window_title": "Chrome",
                },
                {"id": "body", "text": "Body text", "control_type": "edit", "rect": [220, 210, 460, 260]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "editor_share",
                "rect": [120, 160, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_page_share_recovers_from_in_page_share_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [420, 180, 100, 32], "label": "Share"},
                {"rect": [900, 20, 90, 32], "label": "Share page"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Share browser page.",
                "target_id": "page_share",
                "target": {"x": 420, "y": 180, "width": 100, "height": 32},
            },
            "candidates": [
                {
                    "id": "page_share",
                    "text": "Share",
                    "control_type": "button",
                    "rect": [420, 180, 100, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "chrome_share",
                    "text": "Share this page",
                    "control_type": "button",
                    "rect": [900, 20, 90, 32],
                    "automation_id": "share",
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "chrome_share",
                "rect": [900, 20, 90, 32],
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
            "name": "search_exact_neighbor_recovers_from_find_alias_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Find users"},
                {"rect": [220, 80, 180, 32], "label": "Search users"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Search users.",
                "target_id": "find",
                "target": {"x": 20, "y": 80, "width": 160, "height": 32},
            },
            "candidates": [
                {"id": "find", "text": "Find users", "control_type": "button", "rect": [20, 80, 160, 32]},
                {"id": "search", "text": "Search users", "control_type": "button", "rect": [220, 80, 180, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "search",
                "rect": [220, 80, 180, 32],
                "overlay_emitted": True,
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
            "name": "download_exact_neighbor_rejects_export_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Export"},
                {"rect": [180, 80, 140, 32], "label": "Download"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Download report.",
                "target_id": "export",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "export", "text": "Export", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "download", "text": "Download", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "export",
                "rejected_reason": "candidate semantic mismatch",
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
            "name": "audio_action_recovers_from_neutral_speaker_alias",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Speaker"},
                {"rect": [200, 80, 100, 32], "label": "Unmute"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Unmute audio.",
                "target_id": "speaker",
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "speaker", "text": "Speaker", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "unmute", "text": "Unmute", "control_type": "button", "rect": [200, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "unmute",
                "rect": [200, 80, 100, 32],
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
            "name": "bare_menu_rejects_taskbar_start_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [0, 940, 55, 40], "label": "Start"},
                {"rect": [500, 120, 80, 32], "label": "Menu"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open menu.",
                "target_id": "start",
                "target": {"x": 0, "y": 940, "width": 55, "height": 40},
            },
            "candidates": [
                {
                    "id": "start",
                    "text": "Start",
                    "control_type": "button",
                    "rect": [0, 940, 55, 40],
                    "automation_id": "StartButton",
                    "window_title": "Taskbar",
                },
                {"id": "app_menu", "text": "Menu", "control_type": "button", "rect": [500, 120, 80, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "start",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
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
                "source": "text_match",
                "target_id": "p",
                "rect": [560, 60, 60, 32],
                "overlay_emitted": True,
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
            "name": "taskbar_wording_recovers_from_page_volume_control",
            "capture": {"width": 1200, "height": 1000},
            "draw": [
                {"rect": [780, 960, 200, 36], "label": "Volume 24%"},
                {"rect": [120, 260, 140, 36], "label": "Volume"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click taskbar volume.",
                "target_id": "page_volume",
                "target": {"x": 120, "y": 260, "width": 140, "height": 36},
            },
            "candidates": [
                {
                    "id": "taskbar_volume",
                    "text": "Volume Speakers (Realtek(R) Audio): 24%",
                    "control_type": "button",
                    "rect": [780, 960, 200, 36],
                    "automation_id": "SystemTrayIcon",
                    "window_title": "Taskbar",
                },
                {
                    "id": "page_volume",
                    "text": "Volume",
                    "control_type": "button",
                    "rect": [120, 260, 140, 36],
                    "window_title": "Player - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "taskbar_volume",
                "rect": [780, 960, 200, 36],
                "overlay_emitted": True,
            },
        },
        {
            "name": "page_wording_recovers_from_taskbar_volume_control",
            "capture": {"width": 1200, "height": 1000},
            "draw": [
                {"rect": [780, 960, 200, 36], "label": "Volume 24%"},
                {"rect": [120, 260, 140, 36], "label": "Volume"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click volume on the page.",
                "target_id": "taskbar_volume",
                "target": {"x": 780, "y": 960, "width": 200, "height": 36},
            },
            "candidates": [
                {
                    "id": "taskbar_volume",
                    "text": "Volume Speakers (Realtek(R) Audio): 24%",
                    "control_type": "button",
                    "rect": [780, 960, 200, 36],
                    "automation_id": "SystemTrayIcon",
                    "window_title": "Taskbar",
                },
                {
                    "id": "page_volume",
                    "text": "Volume",
                    "control_type": "button",
                    "rect": [120, 260, 140, 36],
                    "window_title": "Player - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "page_volume",
                "rect": [120, 260, 140, 36],
                "overlay_emitted": True,
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
            "name": "lock_exact_neighbor_rejects_unlock_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Unlock"},
                {"rect": [180, 80, 100, 32], "label": "Lock"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Lock account.",
                "target_id": "unlock",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "unlock", "text": "Unlock", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "lock", "text": "Lock", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "unlock",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
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
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
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
            "name": "text_entry_recovers_from_wrong_blank_field_when_labeled_field_matches",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 94, 260, 36], "label": ""},
                {"rect": [420, 100, 90, 24], "label": "Password"},
                {"rect": [520, 94, 260, 36], "label": ""},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Type into Password.",
                "target_id": "wrong",
                "target": {"x": 100, "y": 94, "width": 260, "height": 36},
            },
            "candidates": [
                {"id": "wrong", "text": "", "control_type": "edit", "rect": [100, 94, 260, 36]},
                {"id": "label", "text": "Password", "control_type": "text", "rect": [420, 100, 90, 24]},
                {"id": "field", "text": "", "control_type": "edit", "rect": [520, 94, 260, 36]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "field",
                "rect": [520, 94, 260, 36],
                "overlay_emitted": True,
            },
        },
        {
            "name": "text_entry_recovers_from_password_visibility_button_target",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [80, 100, 90, 24], "label": "Password"},
                {"rect": [180, 94, 280, 36], "label": ""},
                {"rect": [430, 98, 28, 28], "label": "Show"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Enter the password.",
                "target_id": "show",
                "target": {"x": 430, "y": 98, "width": 28, "height": 28},
            },
            "candidates": [
                {"id": "label", "text": "Password", "control_type": "text", "rect": [80, 100, 90, 24]},
                {"id": "field", "text": "", "control_type": "edit", "rect": [180, 94, 280, 36]},
                {"id": "show", "text": "Show password", "control_type": "button", "rect": [430, 98, 28, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "field",
                "rect": [180, 94, 280, 36],
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
            "name": "calendar_exact_label_overrides_date_modified_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 130, 32], "label": "Date modified"},
                {"rect": [260, 100, 130, 32], "label": "Calendar"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open calendar.",
                "target_id": "date_modified",
                "target": {"x": 100, "y": 100, "width": 130, "height": 32},
            },
            "candidates": [
                {"id": "date_modified", "text": "Date modified", "control_type": "button", "rect": [100, 100, 130, 32]},
                {"id": "calendar", "text": "Calendar", "control_type": "button", "rect": [260, 100, 130, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "calendar",
                "rect": [260, 100, 130, 32],
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
            "name": "numeric_taskbar_clock_status_accepts_clock_request",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [900, 960, 90, 40], "label": "11:32 AM"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open clock.",
                "target_id": "clock",
                "target": {"x": 900, "y": 960, "width": 90, "height": 40},
            },
            "candidates": [
                {
                    "id": "clock",
                    "text": "11:32 AM\n6/1/2026",
                    "control_type": "button",
                    "rect": [900, 960, 90, 40],
                    "automation_id": "SystemTrayIcon",
                    "window_title": "Taskbar",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "clock",
                "rect": [900, 960, 90, 40],
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
            "name": "downloads_folder_rejects_browser_toolbar_downloads_button",
            "capture": {"width": 1200, "height": 800},
            "draw": [
                {"rect": [900, 8, 42, 34], "label": "Downloads"},
                {"rect": [100, 200, 200, 32], "label": "Downloads"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open downloads folder.",
                "target_id": "browser_downloads",
                "target": {"x": 900, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "browser_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [900, 8, 42, 34],
                    "automation_id": "downloads",
                    "window_title": "Report - Google Chrome",
                },
                {
                    "id": "downloads_folder",
                    "text": "Downloads",
                    "control_type": "listitem",
                    "rect": [100, 200, 200, 32],
                    "window_title": "File Explorer",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "browser_downloads",
                "rejected_reason": "target_id semantic mismatch",
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
            "name": "generic_settings_prefers_visible_settings_over_edge_menu",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [930, 8, 42, 34], "label": "Settings and more"},
                {"rect": [100, 200, 100, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open settings.",
                "target_id": "edge_menu",
                "target": {"x": 930, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "edge_menu",
                    "text": "Settings and more",
                    "control_type": "button",
                    "rect": [930, 8, 42, 34],
                    "window_title": "Dashboard - Microsoft Edge",
                },
                {
                    "id": "page_settings",
                    "text": "Settings",
                    "control_type": "button",
                    "rect": [100, 200, 100, 32],
                    "window_title": "Dashboard - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "page_settings",
                "rect": [100, 200, 100, 32],
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
            "name": "taskbar_app_button_rejects_in_app_report_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [80, 952, 96, 40], "label": "Taskbar Report"},
                {"rect": [120, 180, 180, 36], "label": "Reports"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Report in the app.",
                "target_id": "taskbar_report",
                "target": {"x": 80, "y": 952, "width": 96, "height": 40},
            },
            "candidates": [
                {
                    "id": "taskbar_report",
                    "text": "Report",
                    "control_type": "button",
                    "rect": [80, 952, 96, 40],
                    "window_title": "Taskbar",
                    "window_rank": 1,
                },
                {
                    "id": "app_reports",
                    "text": "Reports",
                    "control_type": "listitem",
                    "rect": [120, 180, 180, 36],
                    "window_title": "Dashboard",
                    "window_rank": 0,
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "taskbar_report",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "browser_print_button_rejects_in_app_print_report_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [760, 8, 42, 34], "label": "Browser Print"},
                {"rect": [420, 180, 110, 32], "label": "Print report"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Print report in the app.",
                "target_id": "browser_print",
                "target": {"x": 760, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "browser_print",
                    "text": "Print",
                    "control_type": "button",
                    "rect": [760, 8, 42, 34],
                    "window_title": "Report - Google Chrome",
                },
                {
                    "id": "app_print",
                    "text": "Print report",
                    "control_type": "button",
                    "rect": [420, 180, 110, 32],
                    "window_title": "Report - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_print",
                "rect": [420, 180, 110, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_downloads_rejects_workspace_downloads_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [900, 8, 80, 34], "label": "Browser Downloads"},
                {"rect": [300, 120, 500, 500], "label": "Workspace"},
                {"rect": [420, 180, 120, 32], "label": "Downloads"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Downloads in the workspace.",
                "target_id": "chrome_downloads",
                "target": {"x": 900, "y": 8, "width": 80, "height": 34},
            },
            "candidates": [
                {
                    "id": "chrome_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [900, 8, 80, 34],
                    "automation_id": "downloads",
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "workspace",
                    "text": "Workspace",
                    "control_type": "group",
                    "rect": [300, 120, 500, 500],
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "app_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [420, 180, 120, 32],
                    "window_title": "Project - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_downloads",
                "rect": [420, 180, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_downloads_rejects_plural_sidebars_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [900, 8, 80, 34], "label": "Browser Downloads"},
                {"rect": [300, 120, 500, 500], "label": "Sidebars"},
                {"rect": [420, 180, 120, 32], "label": "Downloads"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Downloads in sidebars.",
                "target_id": "chrome_downloads",
                "target": {"x": 900, "y": 8, "width": 80, "height": 34},
            },
            "candidates": [
                {
                    "id": "chrome_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [900, 8, 80, 34],
                    "automation_id": "downloads",
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "sidebars",
                    "text": "Sidebars",
                    "control_type": "group",
                    "rect": [300, 120, 500, 500],
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "app_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [420, 180, 120, 32],
                    "window_title": "Project - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_downloads",
                "rect": [420, 180, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_downloads_rejects_plural_drawers_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [900, 8, 80, 34], "label": "Browser Downloads"},
                {"rect": [300, 120, 500, 500], "label": "Drawers"},
                {"rect": [420, 180, 120, 32], "label": "Downloads"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Downloads in the drawers.",
                "target_id": "chrome_downloads",
                "target": {"x": 900, "y": 8, "width": 80, "height": 34},
            },
            "candidates": [
                {
                    "id": "chrome_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [900, 8, 80, 34],
                    "automation_id": "downloads",
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "drawers",
                    "text": "Drawers",
                    "control_type": "group",
                    "rect": [300, 120, 500, 500],
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "app_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [420, 180, 120, 32],
                    "window_title": "Project - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_downloads",
                "rect": [420, 180, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_downloads_rejects_plural_notifications_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [900, 8, 80, 34], "label": "Browser Downloads"},
                {"rect": [300, 120, 500, 500], "label": "Notifications"},
                {"rect": [420, 180, 120, 32], "label": "Downloads"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Downloads in the notifications.",
                "target_id": "chrome_downloads",
                "target": {"x": 900, "y": 8, "width": 80, "height": 34},
            },
            "candidates": [
                {
                    "id": "chrome_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [900, 8, 80, 34],
                    "automation_id": "downloads",
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "notifications",
                    "text": "Notifications",
                    "control_type": "group",
                    "rect": [300, 120, 500, 500],
                    "window_title": "Project - Google Chrome",
                },
                {
                    "id": "app_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [420, 180, 120, 32],
                    "window_title": "Project - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_downloads",
                "rect": [420, 180, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "edge_favorites_chrome_rejects_app_favorite_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [910, 8, 42, 34], "label": "Edge favorite"},
                {"rect": [420, 180, 130, 32], "label": "Add favorite"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Add favorite to item in the app.",
                "target_id": "edge_fav",
                "target": {"x": 910, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "edge_fav",
                    "text": "Add to favorites",
                    "control_type": "button",
                    "rect": [910, 8, 42, 34],
                    "window_title": "Catalog - Microsoft Edge",
                },
                {
                    "id": "app_fav",
                    "text": "Add favorite",
                    "control_type": "button",
                    "rect": [420, 180, 130, 32],
                    "window_title": "Catalog - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_fav",
                "rect": [420, 180, 130, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "edge_collections_chrome_rejects_app_collections_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [904, 8, 42, 34], "label": "Edge Collections"},
                {"rect": [120, 160, 180, 32], "label": "Collections"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Collections in the app.",
                "target_id": "edge_collections",
                "target": {"x": 904, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "edge_collections",
                    "text": "Collections",
                    "control_type": "button",
                    "rect": [904, 8, 42, 34],
                    "automation_id": "Collections",
                    "window_title": "CRM - Microsoft Edge",
                },
                {
                    "id": "app_collections",
                    "text": "Collections",
                    "control_type": "listitem",
                    "rect": [120, 160, 180, 32],
                    "window_title": "CRM - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_collections",
                "rect": [120, 160, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "edge_reading_list_chrome_rejects_app_reading_list_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [904, 8, 42, 34], "label": "Edge Reading list"},
                {"rect": [120, 160, 180, 32], "label": "Reading list"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Reading list in the app.",
                "target_id": "edge_reading_list",
                "target": {"x": 904, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "edge_reading_list",
                    "text": "Reading list",
                    "control_type": "button",
                    "rect": [904, 8, 42, 34],
                    "automation_id": "ReadingList",
                    "window_title": "CRM - Microsoft Edge",
                },
                {
                    "id": "app_reading_list",
                    "text": "Reading list",
                    "control_type": "listitem",
                    "rect": [120, 160, 180, 32],
                    "window_title": "CRM - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_reading_list",
                "rect": [120, 160, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "edge_copilot_chrome_rejects_app_copilot_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [904, 8, 42, 34], "label": "Edge Copilot"},
                {"rect": [120, 160, 180, 32], "label": "Copilot"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Copilot in the app.",
                "target_id": "edge_copilot",
                "target": {"x": 904, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "edge_copilot",
                    "text": "Copilot",
                    "control_type": "button",
                    "rect": [904, 8, 42, 34],
                    "automation_id": "Copilot",
                    "window_title": "CRM - Microsoft Edge",
                },
                {
                    "id": "app_copilot",
                    "text": "Copilot",
                    "control_type": "listitem",
                    "rect": [120, 160, 180, 32],
                    "window_title": "CRM - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_copilot",
                "rect": [120, 160, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "edge_passwords_chrome_rejects_app_passwords_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [904, 8, 42, 34], "label": "Edge Passwords"},
                {"rect": [120, 160, 180, 32], "label": "Passwords"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Passwords in the app.",
                "target_id": "edge_passwords",
                "target": {"x": 904, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "edge_passwords",
                    "text": "Passwords",
                    "control_type": "button",
                    "rect": [904, 8, 42, 34],
                    "automation_id": "Passwords",
                    "window_title": "CRM - Microsoft Edge",
                },
                {
                    "id": "app_passwords",
                    "text": "Passwords",
                    "control_type": "listitem",
                    "rect": [120, 160, 180, 32],
                    "window_title": "CRM - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_passwords",
                "rect": [120, 160, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_essentials_chrome_rejects_app_sidebar_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [900, 8, 80, 34], "label": "Browser essentials"},
                {"rect": [120, 180, 180, 36], "label": "Browser essentials"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Browser essentials in the app sidebar.",
                "target_id": "browser_essentials",
                "target": {"x": 900, "y": 8, "width": 80, "height": 34},
            },
            "candidates": [
                {
                    "id": "browser_essentials",
                    "text": "Browser essentials",
                    "control_type": "button",
                    "rect": [900, 8, 80, 34],
                    "automation_id": "browseressentials",
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "app_essentials",
                    "text": "Browser essentials",
                    "control_type": "listitem",
                    "rect": [120, 180, 180, 36],
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_essentials",
                "rect": [120, 180, 180, 36],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_new_tab_chrome_rejects_app_new_tab_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [904, 8, 42, 34], "label": "Browser New tab"},
                {"rect": [120, 160, 180, 32], "label": "New tab"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open New tab in the app.",
                "target_id": "browser_new_tab",
                "target": {"x": 904, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "browser_new_tab",
                    "text": "New tab",
                    "control_type": "button",
                    "rect": [904, 8, 42, 34],
                    "window_title": "CRM - Microsoft Edge",
                },
                {
                    "id": "app_new_tab",
                    "text": "New tab",
                    "control_type": "listitem",
                    "rect": [120, 160, 180, 32],
                    "window_title": "CRM - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_new_tab",
                "rect": [120, 160, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_search_tabs_chrome_rejects_app_search_tabs_instruction",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [904, 8, 82, 34], "label": "Browser Search tabs"},
                {"rect": [120, 160, 180, 32], "label": "Search tabs"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Search tabs in the app.",
                "target_id": "browser_search_tabs",
                "target": {"x": 904, "y": 8, "width": 82, "height": 34},
            },
            "candidates": [
                {
                    "id": "browser_search_tabs",
                    "text": "Search tabs",
                    "control_type": "button",
                    "rect": [904, 8, 82, 34],
                    "window_title": "CRM - Microsoft Edge",
                },
                {
                    "id": "app_search_tabs",
                    "text": "Search tabs",
                    "control_type": "listitem",
                    "rect": [120, 160, 180, 32],
                    "window_title": "CRM - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_search_tabs",
                "rect": [120, 160, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "literal_drop_app_target_recovers_from_browser_downloads_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [904, 8, 42, 34], "label": "Browser Downloads"},
                {"rect": [120, 160, 180, 32], "label": "Drop"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Drop in the app.",
                "target_id": "browser_downloads",
                "target": {"x": 904, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "browser_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [904, 8, 42, 34],
                    "automation_id": "downloads",
                    "window_title": "CRM - Microsoft Edge",
                },
                {
                    "id": "app_drop",
                    "text": "Drop",
                    "control_type": "listitem",
                    "rect": [120, 160, 180, 32],
                    "window_title": "CRM - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_drop",
                "rect": [120, 160, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "literal_text_app_target_recovers_from_browser_downloads_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [904, 8, 42, 34], "label": "Browser Downloads"},
                {"rect": [120, 160, 180, 32], "label": "Text"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Text in the app.",
                "target_id": "browser_downloads",
                "target": {"x": 904, "y": 8, "width": 42, "height": 34},
            },
            "candidates": [
                {
                    "id": "browser_downloads",
                    "text": "Downloads",
                    "control_type": "button",
                    "rect": [904, 8, 42, 34],
                    "automation_id": "downloads",
                    "window_title": "CRM - Microsoft Edge",
                },
                {
                    "id": "app_text",
                    "text": "Text",
                    "control_type": "listitem",
                    "rect": [120, 160, 180, 32],
                    "window_title": "CRM - Microsoft Edge",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "app_text",
                "rect": [120, 160, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "singleton_literal_item_recovers_from_stale_archive_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [320, 100, 90, 32], "label": "Archive"},
                {"rect": [100, 100, 90, 32], "label": "Item"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Item.",
                "target_id": "stale",
                "target": {"x": 320, "y": 100, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "stale", "text": "Archive", "control_type": "button", "rect": [320, 100, 90, 32]},
                {"id": "item", "text": "Item", "control_type": "button", "rect": [100, 100, 90, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "item",
                "rect": [100, 100, 90, 32],
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
            "name": "favorite_action_alias_with_exact_neighbor_rejects_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Star"},
                {"rect": [180, 80, 140, 32], "label": "Favorite"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Favorite this item.",
                "target_id": "star",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "star", "text": "Star", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "favorite", "text": "Favorite", "control_type": "button", "rect": [180, 80, 140, 32]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "favorite",
                "rect": [180, 80, 140, 32],
                "overlay_emitted": True,
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
            "name": "notification_settings_recovers_from_taskbar_notification_status",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [900, 940, 120, 32], "label": "Notifications"},
                {"rect": [420, 180, 180, 32], "label": "Manage notifications"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open notification settings.",
                "target_id": "notif",
                "target": {"x": 900, "y": 940, "width": 120, "height": 32},
            },
            "candidates": [
                {
                    "id": "notif",
                    "text": "Notifications",
                    "control_type": "button",
                    "rect": [900, 940, 120, 32],
                    "automation_id": "SystemTrayIcon",
                    "window_title": "Taskbar",
                },
                {
                    "id": "manage",
                    "text": "Manage notifications",
                    "control_type": "button",
                    "rect": [420, 180, 180, 32],
                    "window_title": "Notifications",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "manage",
                "rect": [420, 180, 180, 32],
                "overlay_emitted": True,
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
            "name": "show_hidden_files_accepts_exact_visibility_object_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [120, 160, 180, 32], "label": "Show hidden files"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Show hidden files.",
                "target_id": "c001",
                "target": {"x": 120, "y": 160, "width": 180, "height": 32},
            },
            "candidates": [
                {
                    "id": "c001",
                    "text": "Show hidden files",
                    "control_type": "checkbox",
                    "rect": [120, 160, 180, 32],
                    "window_title": "File Explorer Options",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "c001",
                "rect": [120, 160, 180, 32],
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
            "name": "select_field_rejects_same_label_menuitem_when_combobox_exists",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 220, 32], "label": "State field"},
                {"rect": [100, 150, 220, 32], "label": "State menu item"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select the State field.",
                "target_id": "stale",
                "target": {"x": 100, "y": 150, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "combo", "text": "State", "control_type": "combobox", "rect": [100, 100, 220, 32]},
                {"id": "stale", "text": "State", "control_type": "menuitem", "rect": [100, 150, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "combo",
                "rect": [100, 100, 220, 32],
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
            "name": "named_page_route_rejects_browser_tabitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 220, 32], "label": "Customers - MyApp - Google Chrome"},
                {"rect": [20, 120, 180, 32], "label": "Customers"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Customers page.",
                "target_id": "tab",
                "target": {"x": 20, "y": 20, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "tab",
                    "text": "Customers - MyApp - Google Chrome",
                    "control_type": "tabitem",
                    "rect": [20, 20, 220, 32],
                    "window_title": "MyApp - Google Chrome",
                },
                {
                    "id": "customers",
                    "text": "Customers",
                    "control_type": "listitem",
                    "rect": [20, 120, 180, 32],
                    "window_title": "MyApp - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "customers",
                "rect": [20, 120, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "named_app_route_rejects_browser_tabitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 220, 32], "label": "Customers - MyApp - Google Chrome"},
                {"rect": [20, 120, 180, 32], "label": "Customers"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Customers route.",
                "target_id": "tab",
                "target": {"x": 20, "y": 20, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "tab",
                    "text": "Customers - MyApp - Google Chrome",
                    "control_type": "tabitem",
                    "rect": [20, 20, 220, 32],
                    "window_title": "MyApp - Google Chrome",
                },
                {
                    "id": "customers",
                    "text": "Customers",
                    "control_type": "listitem",
                    "rect": [20, 120, 180, 32],
                    "window_title": "MyApp - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "customers",
                "rect": [20, 120, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "left_rail_item_rejects_browser_tabitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 220, 32], "label": "Settings - MyApp - Google Chrome"},
                {"rect": [20, 120, 180, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings left rail item.",
                "target_id": "tab",
                "target": {"x": 20, "y": 20, "width": 220, "height": 32},
            },
            "candidates": [
                {
                    "id": "tab",
                    "text": "Settings - MyApp - Google Chrome",
                    "control_type": "tabitem",
                    "rect": [20, 20, 220, 32],
                    "window_title": "MyApp - Google Chrome",
                },
                {
                    "id": "item",
                    "text": "Settings",
                    "control_type": "listitem",
                    "rect": [20, 120, 180, 32],
                    "window_title": "MyApp - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "item",
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
            "name": "grid_item_wording_recovers_from_same_label_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 120, 32], "label": "Settings"},
                {"rect": [20, 60, 180, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings grid item.",
                "target_id": "button",
                "target": {"x": 20, "y": 20, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "button", "text": "Settings", "control_type": "button", "rect": [20, 20, 120, 32]},
                {"id": "item", "text": "Settings", "control_type": "dataitem", "rect": [20, 60, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "item",
                "rect": [20, 60, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_table_cell_model_rect_snaps_to_cell",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 100, 620, 42], "label": "Acme row"},
                {"rect": [260, 112, 120, 30], "label": "Active"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this table cell.",
                "target": {"x": 260, "y": 112, "width": 120, "height": 30},
            },
            "candidates": [
                {"id": "row1", "text": "Acme row", "control_type": "dataitem", "rect": [20, 100, 620, 42]},
                {"id": "status1", "text": "Active", "control_type": "cell", "rect": [260, 112, 120, 30]},
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "status1",
                "rect": [260, 112, 120, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_table_cell_broad_row_rejects_multiple_cells",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [260, 112, 120, 30], "label": "Active"},
                {"rect": [420, 112, 120, 30], "label": "Morgan"},
                {"rect": [580, 112, 120, 30], "label": "Enterprise"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this table cell.",
                "target": {"x": 260, "y": 112, "width": 360, "height": 30},
            },
            "candidates": [
                {"id": "status1", "text": "Active", "control_type": "cell", "rect": [260, 112, 120, 30]},
                {"id": "owner1", "text": "Morgan", "control_type": "cell", "rect": [420, 112, 120, 30]},
                {"id": "plan1", "text": "Enterprise", "control_type": "cell", "rect": [580, 112, 120, 30]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
                "overlay_emitted": False,
            },
        },
        {
            "name": "copied_field_automation_id_uses_nearby_label_context",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 28], "label": "Shipping"},
                {"rect": [140, 80, 220, 32], "label": "Address"},
                {"rect": [20, 130, 100, 28], "label": "Billing"},
                {"rect": [140, 130, 220, 32], "label": "Address"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Fill the Billing Address field.",
                "target_id": "ship_addr",
                "target": {"x": 140, "y": 80, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "ship_lbl", "text": "Shipping", "control_type": "text", "rect": [20, 80, 100, 28]},
                {
                    "id": "ship_addr",
                    "text": "Address",
                    "control_type": "edit",
                    "rect": [140, 80, 220, 32],
                    "automation_id": "address",
                },
                {"id": "bill_lbl", "text": "Billing", "control_type": "text", "rect": [20, 130, 100, 28]},
                {
                    "id": "bill_addr",
                    "text": "Address",
                    "control_type": "edit",
                    "rect": [140, 130, 220, 32],
                    "automation_id": "address",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "bill_addr",
                "rect": [140, 130, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "tab_context_action_recovers_action_not_tab",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 90, 32], "label": "Alpha"},
                {"rect": [110, 20, 90, 32], "label": "Beta"},
                {"rect": [240, 100, 70, 30], "label": "Run"},
                {"rect": [240, 160, 70, 30], "label": "Run"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Run in the Beta tab.",
                "target_id": "alpha_run",
                "target": {"x": 240, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {"id": "alpha_tab", "text": "Alpha", "control_type": "tabitem", "rect": [20, 20, 90, 32]},
                {"id": "beta_tab", "text": "Beta", "control_type": "tabitem", "rect": [110, 20, 90, 32]},
                {
                    "id": "alpha_run",
                    "text": "Run",
                    "control_type": "button",
                    "rect": [240, 100, 70, 30],
                    "window_title": "Alpha",
                },
                {
                    "id": "beta_run",
                    "text": "Run",
                    "control_type": "button",
                    "rect": [240, 160, 70, 30],
                    "window_title": "Beta",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "beta_run",
                "rect": [240, 160, 70, 30],
                "overlay_emitted": True,
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
            "name": "chrome_profile_menu_rejects_page_profile_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [420, 180, 110, 32], "label": "Profile"},
                {"rect": [936, 20, 32, 32], "label": "A"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Chrome profile menu.",
                "target_id": "page_profile",
                "target": {"x": 420, "y": 180, "width": 110, "height": 32},
            },
            "candidates": [
                {
                    "id": "page_profile",
                    "text": "Profile",
                    "control_type": "button",
                    "rect": [420, 180, 110, 32],
                    "window_title": "Dashboard - Google Chrome",
                },
                {
                    "id": "chrome_profile",
                    "text": "Abel (All)",
                    "control_type": "button",
                    "rect": [936, 20, 32, 32],
                    "automation_id": "view_1018",
                    "window_title": "Dashboard - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "chrome_profile",
                "rect": [936, 20, 32, 32],
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
            "name": "pin_same_action_object_mismatch_recovers_to_exact_label",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 32], "label": "Pin Beta"},
                {"rect": [180, 80, 120, 32], "label": "Pin Alpha"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Pin Alpha.",
                "target_id": "wrong",
                "target": {"x": 20, "y": 80, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "wrong", "text": "Pin Beta", "control_type": "button", "rect": [20, 80, 120, 32]},
                {"id": "correct", "text": "Pin Alpha", "control_type": "button", "rect": [180, 80, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "correct",
                "rect": [180, 80, 120, 32],
                "overlay_emitted": True,
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
            "name": "next_page_rejects_media_window_next_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 32, 32], "label": "Next"},
                {"rect": [200, 100, 100, 32], "label": "Next page"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Go to the next page.",
                "target_id": "media_next",
                "target": {"x": 100, "y": 100, "width": 32, "height": 32},
            },
            "candidates": [
                {
                    "id": "media_next",
                    "text": "Next",
                    "control_type": "button",
                    "rect": [100, 100, 32, 32],
                    "window_title": "Music Player",
                },
                {
                    "id": "page_next",
                    "text": "Next page",
                    "control_type": "button",
                    "rect": [200, 100, 100, 32],
                    "window_title": "Docs",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "page_next",
                "rect": [200, 100, 100, 32],
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
            "name": "cardinal_direction_text_match_overrides_opposite_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Up"},
                {"rect": [180, 80, 100, 32], "label": "Down"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Move down.",
                "target_id": "up",
                "target": {"x": 20, "y": 80, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "up", "text": "Up", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "down", "text": "Down", "control_type": "button", "rect": [180, 80, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "down",
                "rect": [180, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "browser_back_wording_recovers_from_page_local_back_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [16, 16, 32, 32], "label": "Back"},
                {"rect": [80, 160, 96, 36], "label": "Back"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the browser Back button.",
                "target_id": "page_back",
                "target": {"x": 80, "y": 160, "width": 96, "height": 36},
            },
            "candidates": [
                {
                    "id": "browser_back",
                    "text": "Back",
                    "control_type": "button",
                    "rect": [16, 16, 32, 32],
                    "automation_id": "view_back",
                    "window_title": "Docs - Google Chrome",
                },
                {
                    "id": "page_back",
                    "text": "Back",
                    "control_type": "button",
                    "rect": [80, 160, 96, 36],
                    "window_title": "Docs - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "browser_back",
                "rect": [16, 16, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "page_back_wording_recovers_from_browser_toolbar_back_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [16, 50, 36, 32], "label": "Back"},
                {"rect": [80, 180, 80, 32], "label": "Back"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Back button on the page.",
                "target_id": "browser_back",
                "target": {"x": 16, "y": 50, "width": 36, "height": 32},
            },
            "candidates": [
                {
                    "id": "browser_back",
                    "text": "Back",
                    "control_type": "button",
                    "rect": [16, 50, 36, 32],
                    "window_title": "Docs - Google Chrome",
                },
                {
                    "id": "page_back",
                    "text": "Back",
                    "control_type": "button",
                    "rect": [80, 180, 80, 32],
                    "window_title": "Docs - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "page_back",
                "rect": [80, 180, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "page_find_wording_recovers_from_browser_toolbar_find",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [760, 8, 80, 34], "label": "Find"},
                {"rect": [80, 220, 100, 36], "label": "Find"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Find on the page.",
                "target_id": "browser_find",
                "target": {"x": 760, "y": 8, "width": 80, "height": 34},
            },
            "candidates": [
                {
                    "id": "browser_find",
                    "text": "Find",
                    "control_type": "button",
                    "rect": [760, 8, 80, 34],
                    "window_title": "Docs - Google Chrome",
                },
                {
                    "id": "page_find",
                    "text": "Find",
                    "control_type": "button",
                    "rect": [80, 220, 100, 36],
                    "window_title": "Docs - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "page_find",
                "rect": [80, 220, 100, 36],
                "overlay_emitted": True,
            },
        },
        {
            "name": "chrome_toolbar_reload_wording_recovers_from_page_reload",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [16, 8, 34, 34], "label": "Reload"},
                {"rect": [80, 220, 120, 36], "label": "Reload"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Chrome toolbar Reload button.",
                "target_id": "page_reload",
                "target": {"x": 80, "y": 220, "width": 120, "height": 36},
            },
            "candidates": [
                {
                    "id": "browser_reload",
                    "text": "Reload",
                    "control_type": "button",
                    "rect": [16, 8, 34, 34],
                    "automation_id": "reload",
                    "window_title": "Docs - Google Chrome",
                },
                {
                    "id": "page_reload",
                    "text": "Reload",
                    "control_type": "button",
                    "rect": [80, 220, 120, 36],
                    "window_title": "Docs - Google Chrome",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "browser_reload",
                "rect": [16, 8, 34, 34],
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
            "name": "state_word_in_exact_object_label_accepts_target",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 150, 36], "label": "Closed tickets"},
                {"rect": [260, 100, 140, 36], "label": "Open tickets"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open Closed tickets.",
                "target_id": "closed_tab",
                "target": {"x": 100, "y": 100, "width": 150, "height": 36},
            },
            "candidates": [
                {
                    "id": "closed_tab",
                    "text": "Closed tickets",
                    "control_type": "tabitem",
                    "rect": [100, 100, 150, 36],
                    "window_title": "Helpdesk",
                },
                {
                    "id": "open_tab",
                    "text": "Open tickets",
                    "control_type": "tabitem",
                    "rect": [260, 100, 140, 36],
                    "window_title": "Helpdesk",
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "closed_tab",
                "rect": [100, 100, 150, 36],
                "overlay_emitted": True,
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
                "source": "text_match",
                "target_id": "pay2",
                "rect": [730, 144, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_pay_action_promotes_adjacent_action_column",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 600, 40], "label": "Alpha"},
                {"rect": [620, 14, 60, 30], "label": "Pay"},
                {"rect": [10, 60, 600, 40], "label": "Beta"},
                {"rect": [620, 64, 60, 30], "label": "Pay"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Pay for Beta row.",
                "target": {"x": 10, "y": 60, "width": 600, "height": 40},
            },
            "candidates": [
                {"id": "r1", "text": "Alpha", "control_type": "listitem", "rect": [10, 10, 600, 40]},
                {"id": "pay1", "text": "Pay", "control_type": "button", "rect": [620, 14, 60, 30]},
                {"id": "r2", "text": "Beta", "control_type": "listitem", "rect": [10, 60, 600, 40]},
                {"id": "pay2", "text": "Pay", "control_type": "button", "rect": [620, 64, 60, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "pay2",
                "rect": [620, 64, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_next_to_label_recovers_adjacent_action_column",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 120, 30], "label": "Alice"},
                {"rect": [220, 84, 70, 32], "label": "Edit"},
                {"rect": [20, 140, 120, 30], "label": "Bob"},
                {"rect": [220, 134, 70, 32], "label": "Edit"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Edit next to Alice.",
                "target_id": "edit_bob",
                "target": {"x": 220, "y": 134, "width": 70, "height": 32},
            },
            "candidates": [
                {"id": "label_alice", "text": "Alice", "control_type": "text", "rect": [20, 80, 120, 30]},
                {"id": "edit_alice", "text": "Edit", "control_type": "button", "rect": [220, 84, 70, 32]},
                {"id": "label_bob", "text": "Bob", "control_type": "text", "rect": [20, 140, 120, 30]},
                {"id": "edit_bob", "text": "Edit", "control_type": "button", "rect": [220, 134, 70, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "edit_alice",
                "rect": [220, 84, 70, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_pay_action_promotes_separated_action_column",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 600, 32], "label": "Alpha"},
                {"rect": [730, 10, 60, 30], "label": "Pay"},
                {"rect": [10, 60, 600, 32], "label": "Beta"},
                {"rect": [730, 60, 60, 30], "label": "Pay"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Pay for Beta row.",
                "target_id": "pay2",
                "target": {"x": 730, "y": 60, "width": 60, "height": 30},
            },
            "candidates": [
                {"id": "r1", "text": "Alpha", "control_type": "listitem", "rect": [10, 10, 600, 32]},
                {"id": "pay1", "text": "Pay", "control_type": "button", "rect": [730, 10, 60, 30]},
                {"id": "r2", "text": "Beta", "control_type": "listitem", "rect": [10, 60, 600, 32]},
                {"id": "pay2", "text": "Pay", "control_type": "button", "rect": [730, 60, 60, 30]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "pay2",
                "rect": [730, 60, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_pay_action_promotes_automation_only_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 800, 40], "label": "Alpha"},
                {"rect": [720, 14, 32, 30], "label": ""},
                {"rect": [10, 60, 800, 40], "label": "Beta"},
                {"rect": [720, 64, 32, 30], "label": ""},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Pay for Beta row.",
                "target": {"x": 10, "y": 60, "width": 800, "height": 40},
            },
            "candidates": [
                {"id": "r1", "text": "Alpha", "control_type": "listitem", "rect": [10, 10, 800, 40]},
                {
                    "id": "pay1",
                    "text": "",
                    "automation_id": "payButton",
                    "control_type": "button",
                    "rect": [720, 14, 32, 30],
                },
                {"id": "r2", "text": "Beta", "control_type": "listitem", "rect": [10, 60, 800, 40]},
                {
                    "id": "pay2",
                    "text": "",
                    "automation_id": "payButton",
                    "control_type": "button",
                    "rect": [720, 64, 32, 30],
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "pay2",
                "rect": [720, 64, 32, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_refund_action_uses_singular_item_context",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 90, 560, 48], "label": "Acme item"},
                {"rect": [610, 99, 80, 30], "label": "Refund"},
                {"rect": [20, 150, 560, 48], "label": "Globex item"},
                {"rect": [610, 159, 80, 30], "label": "Refund"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Refund for Globex item.",
                "target_id": "refund_acme",
                "target": {"x": 610, "y": 99, "width": 80, "height": 30},
            },
            "candidates": [
                {"id": "row_acme", "text": "Acme item", "control_type": "listitem", "rect": [20, 90, 560, 48]},
                {"id": "refund_acme", "text": "Refund", "control_type": "button", "rect": [610, 99, 80, 30]},
                {"id": "row_globex", "text": "Globex item", "control_type": "listitem", "rect": [20, 150, 560, 48]},
                {"id": "refund_globex", "text": "Refund", "control_type": "button", "rect": [610, 159, 80, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "refund_globex",
                "rect": [610, 159, 80, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_refund_action_uses_record_context",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 90, 560, 48], "label": "Acme record"},
                {"rect": [610, 99, 80, 30], "label": "Refund"},
                {"rect": [20, 150, 560, 48], "label": "Globex record"},
                {"rect": [610, 159, 80, 30], "label": "Refund"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Refund for Globex record.",
                "target_id": "refund_acme",
                "target": {"x": 610, "y": 99, "width": 80, "height": 30},
            },
            "candidates": [
                {"id": "row_acme", "text": "Acme record", "control_type": "listitem", "rect": [20, 90, 560, 48]},
                {"id": "refund_acme", "text": "Refund", "control_type": "button", "rect": [610, 99, 80, 30]},
                {"id": "row_globex", "text": "Globex record", "control_type": "listitem", "rect": [20, 150, 560, 48]},
                {"id": "refund_globex", "text": "Refund", "control_type": "button", "rect": [610, 159, 80, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "refund_globex",
                "rect": [610, 159, 80, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_action_without_row_evidence_rejects_duplicate_save",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [220, 80, 70, 30], "label": "Save"},
                {"rect": [220, 130, 70, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save for Phone record.",
                "target_id": "email_save",
                "target": {"x": 220, "y": 80, "width": 70, "height": 30},
            },
            "candidates": [
                {"id": "email_save", "text": "Save", "control_type": "button", "rect": [220, 80, 70, 30]},
                {"id": "phone_save", "text": "Save", "control_type": "button", "rect": [220, 130, 70, 30]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "email_save",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "row_scoped_refund_action_uses_named_row_label_without_row_noun",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 90, 560, 48], "label": "Acme"},
                {"rect": [610, 99, 80, 30], "label": "Refund"},
                {"rect": [20, 150, 560, 48], "label": "Globex"},
                {"rect": [610, 159, 80, 30], "label": "Refund"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Refund for Globex.",
                "target_id": "refund_acme",
                "target": {"x": 610, "y": 99, "width": 80, "height": 30},
            },
            "candidates": [
                {"id": "row_acme", "text": "Acme", "control_type": "listitem", "rect": [20, 90, 560, 48]},
                {"id": "refund_acme", "text": "Refund", "control_type": "button", "rect": [610, 99, 80, 30]},
                {"id": "row_globex", "text": "Globex", "control_type": "listitem", "rect": [20, 150, 560, 48]},
                {"id": "refund_globex", "text": "Refund", "control_type": "button", "rect": [610, 159, 80, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "refund_globex",
                "rect": [610, 159, 80, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "static_text_row_label_rejects_wrong_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 90, 120, 30], "label": "Acme"},
                {"rect": [180, 90, 90, 30], "label": "Approve"},
                {"rect": [20, 140, 120, 30], "label": "Globex"},
                {"rect": [180, 140, 90, 30], "label": "Approve"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Approve Acme.",
                "target_id": "approve_globex",
                "target": {"x": 180, "y": 140, "width": 90, "height": 30},
            },
            "candidates": [
                {"id": "label_acme", "text": "Acme", "control_type": "text", "rect": [20, 90, 120, 30]},
                {"id": "approve_acme", "text": "Approve", "control_type": "button", "rect": [180, 90, 90, 30]},
                {"id": "label_globex", "text": "Globex", "control_type": "text", "rect": [20, 140, 120, 30]},
                {"id": "approve_globex", "text": "Approve", "control_type": "button", "rect": [180, 140, 90, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "approve_acme",
                "rect": [180, 90, 90, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "action_first_rowheader_label_recovers_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 90, 90, 30], "label": "Approve"},
                {"rect": [140, 90, 120, 30], "label": "Acme"},
                {"rect": [20, 140, 90, 30], "label": "Approve"},
                {"rect": [140, 140, 120, 30], "label": "Globex"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Approve Acme.",
                "target_id": "approve_globex",
                "target": {"x": 20, "y": 140, "width": 90, "height": 30},
            },
            "candidates": [
                {"id": "approve_acme", "text": "Approve", "control_type": "button", "rect": [20, 90, 90, 30]},
                {"id": "label_acme", "text": "Acme", "control_type": "rowheader", "rect": [140, 90, 120, 30]},
                {"id": "approve_globex", "text": "Approve", "control_type": "button", "rect": [20, 140, 90, 30]},
                {"id": "label_globex", "text": "Globex", "control_type": "rowheader", "rect": [140, 140, 120, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "approve_acme",
                "rect": [20, 90, 90, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "static_text_row_label_with_request_noun_recovers_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 90, 120, 30], "label": "Acme"},
                {"rect": [180, 90, 90, 30], "label": "Approve"},
                {"rect": [20, 140, 120, 30], "label": "Globex"},
                {"rect": [180, 140, 90, 30], "label": "Approve"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Approve Acme request.",
                "target_id": "approve_globex",
                "target": {"x": 180, "y": 140, "width": 90, "height": 30},
            },
            "candidates": [
                {"id": "label_acme", "text": "Acme", "control_type": "text", "rect": [20, 90, 120, 30]},
                {"id": "approve_acme", "text": "Approve", "control_type": "button", "rect": [180, 90, 90, 30]},
                {"id": "label_globex", "text": "Globex", "control_type": "text", "rect": [20, 140, 120, 30]},
                {"id": "approve_globex", "text": "Approve", "control_type": "button", "rect": [180, 140, 90, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "approve_acme",
                "rect": [180, 90, 90, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "grid_cell_row_label_rejects_wrong_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 90, 140, 30], "label": "Nimbus"},
                {"rect": [200, 90, 90, 30], "label": "Approve"},
                {"rect": [20, 140, 140, 30], "label": "Orion"},
                {"rect": [200, 140, 90, 30], "label": "Approve"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Approve Nimbus.",
                "target_id": "approve_orion",
                "target": {"x": 200, "y": 140, "width": 90, "height": 30},
            },
            "candidates": [
                {"id": "cell_nimbus", "text": "Nimbus", "control_type": "cell", "rect": [20, 90, 140, 30]},
                {"id": "approve_nimbus", "text": "Approve", "control_type": "button", "rect": [200, 90, 90, 30]},
                {"id": "cell_orion", "text": "Orion", "control_type": "cell", "rect": [20, 140, 140, 30]},
                {"id": "approve_orion", "text": "Approve", "control_type": "button", "rect": [200, 140, 90, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "approve_nimbus",
                "rect": [200, 90, 90, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "rowheader_label_rejects_wrong_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 90, 140, 30], "label": "Vega"},
                {"rect": [200, 90, 90, 30], "label": "Approve"},
                {"rect": [20, 140, 140, 30], "label": "Lyra"},
                {"rect": [200, 140, 90, 30], "label": "Approve"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Approve Vega.",
                "target_id": "approve_lyra",
                "target": {"x": 200, "y": 140, "width": 90, "height": 30},
            },
            "candidates": [
                {"id": "label_vega", "text": "Vega", "control_type": "rowheader", "rect": [20, 90, 140, 30]},
                {"id": "approve_vega", "text": "Approve", "control_type": "button", "rect": [200, 90, 90, 30]},
                {"id": "label_lyra", "text": "Lyra", "control_type": "rowheader", "rect": [20, 140, 140, 30]},
                {"id": "approve_lyra", "text": "Approve", "control_type": "button", "rect": [200, 140, 90, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "approve_vega",
                "rect": [200, 90, 90, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "group_scoped_billing_email_recovers_from_shipping_email_geometry",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 60, 420, 70], "label": "Billing"},
                {"rect": [150, 80, 220, 32], "label": "Email"},
                {"rect": [20, 120, 420, 70], "label": "Shipping"},
                {"rect": [150, 130, 220, 32], "label": "Email"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Enter Billing email.",
                "target_id": "shipping_email",
                "target": {"x": 150, "y": 130, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "billing_group", "text": "Billing", "control_type": "group", "rect": [20, 60, 420, 70]},
                {"id": "billing_email", "text": "Email", "control_type": "edit", "rect": [150, 80, 220, 32]},
                {"id": "shipping_group", "text": "Shipping", "control_type": "group", "rect": [20, 120, 420, 70]},
                {"id": "shipping_email", "text": "Email", "control_type": "edit", "rect": [150, 130, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "billing_email",
                "rect": [150, 80, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "window_scoped_duplicate_action_recovers_requested_window",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 100], "label": "Alpha window"},
                {"rect": [240, 140, 80, 32], "label": "Duplicate"},
                {"rect": [420, 80, 300, 100], "label": "Beta window"},
                {"rect": [640, 140, 80, 32], "label": "Duplicate"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Use Duplicate on the Beta window.",
                "target_id": "alpha_duplicate",
                "target": {"x": 240, "y": 140, "width": 80, "height": 32},
            },
            "candidates": [
                {"id": "alpha_window", "text": "Alpha window", "control_type": "window", "rect": [20, 80, 300, 100]},
                {"id": "alpha_duplicate", "text": "Duplicate", "control_type": "button", "rect": [240, 140, 80, 32]},
                {"id": "beta_window", "text": "Beta window", "control_type": "window", "rect": [420, 80, 300, 100]},
                {"id": "beta_duplicate", "text": "Duplicate", "control_type": "button", "rect": [640, 140, 80, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "beta_duplicate",
                "rect": [640, 140, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "active_window_duplicate_action_recovers_foreground_window",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 360, 280], "label": "Main window"},
                {"rect": [70, 130, 80, 32], "label": "Save"},
                {"rect": [420, 80, 360, 280], "label": "Settings window"},
                {"rect": [470, 130, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the active window.",
                "target_id": "main_save",
                "target": {"x": 70, "y": 130, "width": 80, "height": 32},
            },
            "candidates": [
                {
                    "id": "main_window",
                    "text": "Main window",
                    "control_type": "window",
                    "rect": [20, 80, 360, 280],
                    "window_title": "Main",
                    "window_rank": 1,
                },
                {
                    "id": "main_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [70, 130, 80, 32],
                    "window_title": "Main",
                    "window_rank": 1,
                },
                {
                    "id": "settings_window",
                    "text": "Settings window",
                    "control_type": "window",
                    "rect": [420, 80, 360, 280],
                    "window_title": "Settings",
                    "window_rank": 0,
                },
                {
                    "id": "settings_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [470, 130, 80, 32],
                    "window_title": "Settings",
                    "window_rank": 0,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "settings_save",
                "rect": [470, 130, 80, 32],
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
                "source": "text_match",
                "target_id": "bob_clear",
                "rect": [390, 170, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_scoped_open_action_uses_action_word_filtered_from_tokens",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 600, 80], "label": "Alice"},
                {"rect": [520, 34, 80, 30], "label": "Open"},
                {"rect": [10, 100, 600, 80], "label": "Bob"},
                {"rect": [520, 124, 80, 30], "label": "Open"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Open in Bob row.",
                "target_id": "a1",
                "target": {"x": 520, "y": 34, "width": 80, "height": 30},
            },
            "candidates": [
                {"id": "r1", "text": "Alice", "control_type": "listitem", "rect": [10, 10, 600, 80]},
                {"id": "a1", "text": "Open", "control_type": "button", "rect": [520, 34, 80, 30]},
                {"id": "r2", "text": "Bob", "control_type": "listitem", "rect": [10, 100, 600, 80]},
                {"id": "a2", "text": "Open", "control_type": "button", "rect": [520, 124, 80, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "a2",
                "rect": [520, 124, 80, 30],
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
                "source": "text_match",
                "target_id": "bob_clear",
                "rect": [390, 170, 28, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "matrix_scoped_duplicate_action_uses_row_and_column_context",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [220, 40, 160, 36], "label": "Staging"},
                {"rect": [400, 40, 160, 36], "label": "Production"},
                {"rect": [20, 90, 560, 48], "label": "Acme Corp"},
                {"rect": [270, 100, 80, 28], "label": "Approve"},
                {"rect": [450, 100, 80, 28], "label": "Approve"},
                {"rect": [20, 150, 560, 48], "label": "Globex Corp"},
                {"rect": [270, 160, 80, 28], "label": "Approve"},
                {"rect": [450, 160, 80, 28], "label": "Approve"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Approve for Globex in the Production column.",
                "target_id": "globex_stage",
                "target": {"x": 270, "y": 160, "width": 80, "height": 28},
            },
            "candidates": [
                {"id": "stage_col", "text": "Staging", "control_type": "headeritem", "rect": [220, 40, 160, 36]},
                {"id": "prod_col", "text": "Production", "control_type": "headeritem", "rect": [400, 40, 160, 36]},
                {"id": "acme_row", "text": "Acme Corp", "control_type": "listitem", "rect": [20, 90, 560, 48]},
                {"id": "acme_stage", "text": "Approve", "control_type": "button", "rect": [270, 100, 80, 28]},
                {"id": "acme_prod", "text": "Approve", "control_type": "button", "rect": [450, 100, 80, 28]},
                {"id": "globex_row", "text": "Globex Corp", "control_type": "listitem", "rect": [20, 150, 560, 48]},
                {"id": "globex_stage", "text": "Approve", "control_type": "button", "rect": [270, 160, 80, 28]},
                {"id": "globex_prod", "text": "Approve", "control_type": "button", "rect": [450, 160, 80, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "globex_prod",
                "rect": [450, 160, 80, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "shorthand_header_context_recovers_requested_column_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 40, 160, 36], "label": "Name"},
                {"rect": [200, 40, 160, 36], "label": "Status"},
                {"rect": [140, 86, 70, 28], "label": "Filter"},
                {"rect": [320, 86, 70, 28], "label": "Filter"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Filter Status.",
                "target_id": "name_filter",
                "target": {"x": 140, "y": 86, "width": 70, "height": 28},
            },
            "candidates": [
                {"id": "name_col", "text": "Name", "control_type": "headeritem", "rect": [20, 40, 160, 36]},
                {"id": "status_col", "text": "Status", "control_type": "headeritem", "rect": [200, 40, 160, 36]},
                {"id": "name_filter", "text": "Filter", "control_type": "button", "rect": [140, 86, 70, 28]},
                {"id": "status_filter", "text": "Filter", "control_type": "button", "rect": [320, 86, 70, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "status_filter",
                "rect": [320, 86, 70, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "column_context_action_recovers_same_label_action_not_header",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 50, 150, 28], "label": "Name"},
                {"rect": [190, 50, 150, 28], "label": "Status"},
                {"rect": [75, 90, 70, 28], "label": "Edit"},
                {"rect": [245, 90, 70, 28], "label": "Edit"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Edit in the Status column.",
                "target_id": "edit_name",
                "target": {"x": 75, "y": 90, "width": 70, "height": 28},
            },
            "candidates": [
                {"id": "h_name", "text": "Name", "control_type": "headeritem", "rect": [20, 50, 150, 28]},
                {"id": "h_status", "text": "Status", "control_type": "headeritem", "rect": [190, 50, 150, 28]},
                {"id": "edit_name", "text": "Edit", "control_type": "button", "rect": [75, 90, 70, 28]},
                {"id": "edit_status", "text": "Edit", "control_type": "button", "rect": [245, 90, 70, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "edit_status",
                "rect": [245, 90, 70, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "same_rect_page_context_recovers_copied_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 120, 32], "label": "Customers"},
                {"rect": [140, 20, 120, 32], "label": "Settings"},
                {"rect": [600, 120, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save on the Settings page.",
                "target_id": "cust_save",
                "target": {"x": 600, "y": 120, "width": 80, "height": 32},
            },
            "candidates": [
                {
                    "id": "customers_tab",
                    "text": "Customers",
                    "control_type": "tabitem",
                    "rect": [20, 20, 120, 32],
                    "window_title": "CRM",
                },
                {
                    "id": "settings_tab",
                    "text": "Settings",
                    "control_type": "tabitem",
                    "rect": [140, 20, 120, 32],
                    "window_title": "CRM",
                },
                {
                    "id": "cust_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [600, 120, 80, 32],
                    "automation_id": "primary-action",
                    "window_title": "Customers",
                },
                {
                    "id": "settings_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [600, 120, 80, 32],
                    "automation_id": "primary-action",
                    "window_title": "Settings",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "settings_save",
                "rect": [600, 120, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_sidebar_target_recovers_container_not_child",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 180], "label": "Settings sidebar"},
                {"rect": [40, 100, 100, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings sidebar.",
                "target_id": "child",
                "target": {"x": 40, "y": 100, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "container", "text": "Settings sidebar", "control_type": "pane", "rect": [20, 80, 300, 180]},
                {"id": "child", "text": "Settings", "control_type": "button", "rect": [40, 100, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "container",
                "rect": [20, 80, 300, 180],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_panel_target_recovers_group_surface_not_child",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 180], "label": "Details panel"},
                {"rect": [40, 100, 100, 32], "label": "Details"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Details panel.",
                "target_id": "child",
                "target": {"x": 40, "y": 100, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "container", "text": "Details panel", "control_type": "group", "rect": [20, 80, 300, 180]},
                {"id": "child", "text": "Details", "control_type": "button", "rect": [40, 100, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "container",
                "rect": [20, 80, 300, 180],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_dialog_target_recovers_window_not_child",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [420, 80, 300, 180], "label": "Details dialog"},
                {"rect": [450, 110, 90, 32], "label": "Details"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Details dialog.",
                "target_id": "child",
                "target": {"x": 450, "y": 110, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "container", "text": "Details dialog", "control_type": "window", "rect": [420, 80, 300, 180]},
                {"id": "child", "text": "Details", "control_type": "button", "rect": [450, 110, 90, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "container",
                "rect": [420, 80, 300, 180],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_toast_target_recovers_window_not_child",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [420, 80, 300, 180], "label": "Details toast"},
                {"rect": [450, 110, 90, 32], "label": "Details"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Details toast.",
                "target_id": "child",
                "target": {"x": 450, "y": 110, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "container", "text": "Details toast", "control_type": "window", "rect": [420, 80, 300, 180]},
                {"id": "child", "text": "Details", "control_type": "button", "rect": [450, 110, 90, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "container",
                "rect": [420, 80, 300, 180],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_form_target_recovers_pane_not_child",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 180], "label": "Details form"},
                {"rect": [40, 100, 100, 32], "label": "Details"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Details form.",
                "target_id": "child",
                "target": {"x": 40, "y": 100, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "container", "text": "Details form", "control_type": "pane", "rect": [20, 80, 300, 180]},
                {"id": "child", "text": "Details", "control_type": "button", "rect": [40, 100, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "container",
                "rect": [20, 80, 300, 180],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_navigation_target_recovers_pane_not_child",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 180], "label": "Details navigation"},
                {"rect": [40, 100, 100, 32], "label": "Details"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Details navigation.",
                "target_id": "child",
                "target": {"x": 40, "y": 100, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "container", "text": "Details navigation", "control_type": "pane", "rect": [20, 80, 300, 180]},
                {"id": "child", "text": "Details", "control_type": "button", "rect": [40, 100, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "container",
                "rect": [20, 80, 300, 180],
                "overlay_emitted": True,
            },
        },
        {
            "name": "named_column_target_recovers_header_not_same_label_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [200, 40, 160, 36], "label": "Status"},
                {"rect": [320, 86, 70, 28], "label": "Status"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Status column.",
                "target_id": "status_filter",
                "target": {"x": 320, "y": 86, "width": 70, "height": 28},
            },
            "candidates": [
                {"id": "status_filter", "text": "Status", "control_type": "button", "rect": [320, 86, 70, 28]},
                {"id": "status_header", "text": "Status", "control_type": "headeritem", "rect": [200, 40, 160, 36]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "status_header",
                "rect": [200, 40, 160, 36],
                "overlay_emitted": True,
            },
        },
        {
            "name": "short_result_wording_recovers_listitem_not_stale_menuitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 180, 28], "label": "Settings"},
                {"rect": [20, 60, 180, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings result.",
                "target_id": "stale",
                "target": {"x": 20, "y": 20, "width": 180, "height": 28},
            },
            "candidates": [
                {"id": "stale", "text": "Settings", "control_type": "menuitem", "rect": [20, 20, 180, 28]},
                {"id": "item", "text": "Settings", "control_type": "listitem", "rect": [20, 60, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "item",
                "rect": [20, 60, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "short_node_wording_recovers_treeitem_not_launcher",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 120, 32], "label": "Settings"},
                {"rect": [20, 60, 180, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings node.",
                "target_id": "launcher",
                "target": {"x": 20, "y": 20, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "launcher", "text": "Settings", "control_type": "button", "rect": [20, 20, 120, 32]},
                {"id": "node", "text": "Settings", "control_type": "treeitem", "rect": [20, 60, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "node",
                "rect": [20, 60, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "tab_context_action_recovers_button_not_tab",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 120, 32], "label": "Settings"},
                {"rect": [300, 100, 80, 30], "label": "Run"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Run in the Settings tab.",
                "target_id": "settings_tab",
                "target": {"x": 20, "y": 20, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "run", "text": "Run", "control_type": "button", "rect": [300, 100, 80, 30], "window_title": "Settings"},
                {"id": "settings_tab", "text": "Settings", "control_type": "tabitem", "rect": [20, 20, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "run",
                "rect": [300, 100, 80, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_item_without_item_candidate_rejects_surface_overlay",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 120, 32], "label": "Settings"},
                {"rect": [300, 10, 300, 300], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings item.",
                "target_id": "pane",
                "target": {"x": 300, "y": 10, "width": 300, "height": 300},
            },
            "candidates": [
                {"id": "button", "text": "Settings", "control_type": "button", "rect": [20, 20, 120, 32]},
                {"id": "pane", "text": "Settings", "control_type": "pane", "rect": [300, 10, 300, 300]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "pane",
                "rejected_reason": "target_id control type mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "shorthand_row_context_recovers_requested_review_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 500, 48], "label": "Alice"},
                {"rect": [540, 88, 90, 32], "label": "Review"},
                {"rect": [20, 140, 500, 48], "label": "Bob"},
                {"rect": [540, 148, 90, 32], "label": "Review"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Review Bob.",
                "target_id": "review_alice",
                "target": {"x": 540, "y": 88, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "row_alice", "text": "Alice", "control_type": "listitem", "rect": [20, 80, 500, 48]},
                {"id": "review_alice", "text": "Review", "control_type": "button", "rect": [540, 88, 90, 32]},
                {"id": "row_bob", "text": "Bob", "control_type": "listitem", "rect": [20, 140, 500, 48]},
                {"id": "review_bob", "text": "Review", "control_type": "button", "rect": [540, 148, 90, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "review_bob",
                "rect": [540, 148, 90, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "shorthand_dataitem_row_context_recovers_requested_delete_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 760, 48], "label": "Alice"},
                {"rect": [540, 88, 90, 32], "label": "Delete"},
                {"rect": [20, 140, 760, 48], "label": "Bob"},
                {"rect": [540, 148, 90, 32], "label": "Delete"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Delete Bob.",
                "target_id": "delete_alice",
                "target": {"x": 540, "y": 88, "width": 90, "height": 32},
            },
            "candidates": [
                {"id": "row_alice", "text": "Alice", "control_type": "dataitem", "rect": [20, 80, 760, 48]},
                {"id": "delete_alice", "text": "Delete", "control_type": "button", "rect": [540, 88, 90, 32]},
                {"id": "row_bob", "text": "Bob", "control_type": "dataitem", "rect": [20, 140, 760, 48]},
                {"id": "delete_bob", "text": "Delete", "control_type": "button", "rect": [540, 148, 90, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "delete_bob",
                "rect": [540, 148, 90, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "billing_card_context_recovers_from_adjacent_profile_card_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 100], "label": "Profile card"},
                {"rect": [240, 140, 70, 30], "label": "Archive"},
                {"rect": [360, 80, 300, 100], "label": "Billing card"},
                {"rect": [580, 140, 70, 30], "label": "Archive"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Archive in the Billing card.",
                "target_id": "profile_archive",
                "target": {"x": 240, "y": 140, "width": 70, "height": 30},
            },
            "candidates": [
                {"id": "profile_card", "text": "Profile card", "control_type": "listitem", "rect": [20, 80, 300, 100]},
                {"id": "profile_archive", "text": "Archive", "control_type": "button", "rect": [240, 140, 70, 30]},
                {"id": "billing_card", "text": "Billing card", "control_type": "listitem", "rect": [360, 80, 300, 100]},
                {"id": "billing_archive", "text": "Archive", "control_type": "button", "rect": [580, 140, 70, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "billing_archive",
                "rect": [580, 140, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "shorthand_adjacent_card_context_recovers_requested_save_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 500, 48], "label": "Alpha card"},
                {"rect": [540, 88, 70, 32], "label": "Save"},
                {"rect": [20, 140, 500, 48], "label": "Beta card"},
                {"rect": [540, 148, 70, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Beta Save.",
                "target_id": "alpha_save",
                "target": {"x": 540, "y": 88, "width": 70, "height": 32},
            },
            "candidates": [
                {"id": "alpha_card", "text": "Alpha card", "control_type": "listitem", "rect": [20, 80, 500, 48]},
                {"id": "alpha_save", "text": "Save", "control_type": "button", "rect": [540, 88, 70, 32]},
                {"id": "beta_card", "text": "Beta card", "control_type": "listitem", "rect": [20, 140, 500, 48]},
                {"id": "beta_save", "text": "Save", "control_type": "button", "rect": [540, 148, 70, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "beta_save",
                "rect": [540, 148, 70, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "shorthand_pane_context_recovers_requested_save_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 100], "label": "Profile"},
                {"rect": [240, 140, 70, 30], "label": "Save"},
                {"rect": [360, 80, 300, 100], "label": "Billing"},
                {"rect": [580, 140, 70, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Save Billing.",
                "target_id": "profile_save",
                "target": {"x": 240, "y": 140, "width": 70, "height": 30},
            },
            "candidates": [
                {"id": "profile_card", "text": "Profile", "control_type": "pane", "rect": [20, 80, 300, 100]},
                {"id": "profile_save", "text": "Save", "control_type": "button", "rect": [240, 140, 70, 30]},
                {"id": "billing_card", "text": "Billing", "control_type": "pane", "rect": [360, 80, 300, 100]},
                {"id": "billing_save", "text": "Save", "control_type": "button", "rect": [580, 140, 70, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "billing_save",
                "rect": [580, 140, 70, 30],
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
            "name": "dialog_close_exact_rect_picks_dialog_duplicate",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 80, 32], "label": "Close"},
                {"rect": [500, 300, 80, 32], "label": "Close"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close the dialog.",
                "target": {"x": 500, "y": 300, "width": 80, "height": 32},
            },
            "candidates": [
                {
                    "id": "page_close",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [100, 100, 80, 32],
                    "window_title": "Editor",
                    "window_rank": 0,
                },
                {
                    "id": "dialog_close",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [500, 300, 80, 32],
                    "window_title": "Preferences dialog",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "dialog_close",
                "rect": [500, 300, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "modal_context_recovers_from_page_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 70, 30], "label": "Save"},
                {"rect": [500, 300, 70, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the modal.",
                "target_id": "page_save",
                "target": {"x": 100, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 70, 30],
                    "window_title": "Main page",
                    "window_rank": 0,
                },
                {
                    "id": "modal_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [500, 300, 70, 30],
                    "window_title": "Save changes",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "modal_save",
                "rect": [500, 300, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "modal_context_uses_explicit_surface_with_foreground_rank_zero",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [420, 80, 300, 120], "label": "Confirm changes modal"},
                {"rect": [630, 160, 60, 30], "label": "Save"},
                {"rect": [230, 160, 60, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the modal.",
                "target_id": "page_save",
                "target": {"x": 230, "y": 160, "width": 60, "height": 30},
            },
            "candidates": [
                {
                    "id": "modal_window",
                    "text": "Confirm changes modal",
                    "control_type": "window",
                    "rect": [420, 80, 300, 120],
                    "window_rank": 0,
                },
                {
                    "id": "modal_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [630, 160, 60, 30],
                    "window_title": "Confirm changes",
                    "window_rank": 0,
                },
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [230, 160, 60, 30],
                    "window_title": "Editor",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "modal_save",
                "rect": [630, 160, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dialog_context_uses_foreground_modal_evidence",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 70, 30], "label": "Save"},
                {"rect": [500, 300, 70, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the dialog.",
                "target_id": "page_save",
                "target": {"x": 100, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 70, 30],
                    "window_title": "Editor",
                    "window_rank": 0,
                },
                {
                    "id": "dialog_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [500, 300, 70, 30],
                    "window_title": "Preferences",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "dialog_save",
                "rect": [500, 300, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dialog_context_uses_unnamed_foreground_window_surface",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [360, 200, 300, 180], "label": ""},
                {"rect": [500, 310, 70, 30], "label": "Save"},
                {"rect": [100, 100, 70, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the dialog.",
                "target_id": "page_save",
                "target": {"x": 100, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {
                    "id": "dialog_window",
                    "text": "",
                    "control_type": "window",
                    "rect": [360, 200, 300, 180],
                    "window_rank": 0,
                },
                {
                    "id": "dialog_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [500, 310, 70, 30],
                    "window_rank": 0,
                },
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 70, 30],
                    "window_title": "Editor",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "dialog_save",
                "rect": [500, 310, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "notification_context_uses_unnamed_foreground_window_surface",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [360, 200, 300, 180], "label": ""},
                {"rect": [500, 310, 70, 30], "label": "Save"},
                {"rect": [100, 100, 70, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the notification.",
                "target_id": "page_save",
                "target": {"x": 100, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {
                    "id": "notification_window",
                    "text": "",
                    "control_type": "window",
                    "rect": [360, 200, 300, 180],
                    "window_rank": 0,
                },
                {
                    "id": "notification_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [500, 310, 70, 30],
                    "window_rank": 0,
                },
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 70, 30],
                    "window_title": "Editor",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "notification_save",
                "rect": [500, 310, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "alert_context_uses_unnamed_foreground_window_surface",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [360, 200, 300, 180], "label": ""},
                {"rect": [500, 310, 70, 30], "label": "Save"},
                {"rect": [100, 100, 70, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the alert.",
                "target_id": "page_save",
                "target": {"x": 100, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {
                    "id": "alert_window",
                    "text": "",
                    "control_type": "window",
                    "rect": [360, 200, 300, 180],
                    "window_rank": 0,
                },
                {
                    "id": "alert_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [500, 310, 70, 30],
                    "window_rank": 0,
                },
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 70, 30],
                    "window_title": "Editor",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "alert_save",
                "rect": [500, 310, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "popups_context_uses_unnamed_foreground_window_surface",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [360, 200, 300, 180], "label": ""},
                {"rect": [500, 310, 70, 30], "label": "Save"},
                {"rect": [100, 100, 70, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the popups.",
                "target_id": "page_save",
                "target": {"x": 100, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {
                    "id": "popups_window",
                    "text": "",
                    "control_type": "window",
                    "rect": [360, 200, 300, 180],
                    "window_rank": 0,
                },
                {
                    "id": "popups_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [500, 310, 70, 30],
                    "window_rank": 0,
                },
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 70, 30],
                    "window_title": "Editor",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "popups_save",
                "rect": [500, 310, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "snackbar_context_uses_unnamed_foreground_window_surface",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [360, 200, 300, 180], "label": ""},
                {"rect": [500, 310, 70, 30], "label": "Save"},
                {"rect": [100, 100, 70, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the snackbar.",
                "target_id": "page_save",
                "target": {"x": 100, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {
                    "id": "snackbar_window",
                    "text": "",
                    "control_type": "window",
                    "rect": [360, 200, 300, 180],
                    "window_rank": 0,
                },
                {
                    "id": "snackbar_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [500, 310, 70, 30],
                    "window_rank": 0,
                },
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 70, 30],
                    "window_title": "Editor",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "snackbar_save",
                "rect": [500, 310, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dialog_close_exact_target_id_returns_clean_text_resolution",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 80, 32], "label": "Close"},
                {"rect": [500, 300, 80, 32], "label": "Close"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Close the dialog.",
                "target_id": "dialog_close",
                "target": {"x": 500, "y": 300, "width": 80, "height": 32},
            },
            "candidates": [
                {
                    "id": "page_close",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [100, 100, 80, 32],
                    "window_title": "Editor",
                    "window_rank": 0,
                },
                {
                    "id": "dialog_close",
                    "text": "Close",
                    "control_type": "button",
                    "rect": [500, 300, 80, 32],
                    "window_title": "Preferences dialog",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "dialog_close",
                "rect": [500, 300, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "settings_popup_recovers_from_settings_panel_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 120], "label": "Settings panel"},
                {"rect": [230, 160, 60, 30], "label": "Save"},
                {"rect": [420, 80, 300, 120], "label": "Settings popup"},
                {"rect": [630, 160, 60, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the Settings popup.",
                "target_id": "panel_save",
                "target": {"x": 630, "y": 160, "width": 60, "height": 30},
            },
            "candidates": [
                {"id": "settings_panel", "text": "Settings panel", "control_type": "pane", "rect": [20, 80, 300, 120]},
                {"id": "panel_save", "text": "Save", "control_type": "button", "rect": [230, 160, 60, 30]},
                {"id": "settings_popup", "text": "Settings popup", "control_type": "window", "rect": [420, 80, 300, 120]},
                {"id": "popup_save", "text": "Save", "control_type": "button", "rect": [630, 160, 60, 30]},
            ],
            "expected": {
                "target_id": "popup_save",
                "rect": [630, 160, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "notification_dismiss_recovers_from_page_dismiss_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [230, 160, 70, 30], "label": "Dismiss"},
                {"rect": [420, 80, 300, 120], "label": "Updates notification"},
                {"rect": [630, 160, 70, 30], "label": "Dismiss"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Dismiss in the Updates notification.",
                "target_id": "main_dismiss",
                "target": {"x": 630, "y": 160, "width": 70, "height": 30},
            },
            "candidates": [
                {"id": "main_dismiss", "text": "Dismiss", "control_type": "button", "rect": [230, 160, 70, 30]},
                {
                    "id": "updates_notification",
                    "text": "Updates notification",
                    "control_type": "pane",
                    "rect": [420, 80, 300, 120],
                },
                {"id": "notification_dismiss", "text": "Dismiss", "control_type": "button", "rect": [630, 160, 70, 30]},
            ],
            "expected": {
                "target_id": "notification_dismiss",
                "rect": [630, 160, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_popup_dismiss_recovers_from_page_dismiss_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 120], "label": "Main content"},
                {"rect": [230, 160, 70, 30], "label": "Dismiss"},
                {"rect": [420, 80, 300, 120], "label": "Settings popup"},
                {"rect": [630, 160, 70, 30], "label": "Dismiss"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Dismiss in the popup.",
                "target_id": "main_dismiss",
                "target": {"x": 230, "y": 160, "width": 70, "height": 30},
            },
            "candidates": [
                {"id": "main", "text": "Main content", "control_type": "pane", "rect": [20, 80, 300, 120]},
                {"id": "main_dismiss", "text": "Dismiss", "control_type": "button", "rect": [230, 160, 70, 30]},
                {"id": "settings_popup", "text": "Settings popup", "control_type": "window", "rect": [420, 80, 300, 120]},
                {"id": "popup_dismiss", "text": "Dismiss", "control_type": "button", "rect": [630, 160, 70, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "popup_dismiss",
                "rect": [630, 160, 70, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "popup_context_recovers_to_automation_only_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 70, 30], "label": "Save"},
                {"rect": [420, 80, 300, 120], "label": "Settings popup"},
                {"rect": [630, 160, 32, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the popup.",
                "target_id": "main_save",
                "target": {"x": 100, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {"id": "main_save", "text": "Save", "control_type": "button", "rect": [100, 100, 70, 30]},
                {"id": "popup", "text": "Settings popup", "control_type": "window", "rect": [420, 80, 300, 120]},
                {
                    "id": "popup_save",
                    "text": "",
                    "automation_id": "save_button",
                    "control_type": "button",
                    "rect": [630, 160, 32, 32],
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "popup_save",
                "rect": [630, 160, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "positional_duplicate_action_recovers_requested_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 32, 32], "label": "edit icon"},
                {"rect": [150, 100, 32, 32], "label": "edit icon"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the second edit button.",
                "target_id": "edit1",
                "target": {"x": 100, "y": 100, "width": 32, "height": 32},
            },
            "candidates": [
                {
                    "id": "edit1",
                    "text": "",
                    "automation_id": "editButton",
                    "control_type": "button",
                    "rect": [100, 100, 32, 32],
                },
                {
                    "id": "edit2",
                    "text": "",
                    "automation_id": "editButton",
                    "control_type": "button",
                    "rect": [150, 100, 32, 32],
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "edit2",
                "rect": [150, 100, 32, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "shorthand_positional_row_context_recovers_requested_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [80, 90, 240, 50], "label": "Top"},
                {"rect": [240, 100, 80, 30], "label": "Archive"},
                {"rect": [80, 140, 240, 50], "label": "Bottom"},
                {"rect": [240, 150, 80, 30], "label": "Archive"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Archive Bottom.",
                "target_id": "archive_top",
                "target": {"x": 240, "y": 100, "width": 80, "height": 30},
            },
            "candidates": [
                {"id": "row_top", "text": "Top", "control_type": "listitem", "rect": [80, 90, 240, 50]},
                {"id": "archive_top", "text": "Archive", "control_type": "button", "rect": [240, 100, 80, 30]},
                {"id": "row_bottom", "text": "Bottom", "control_type": "listitem", "rect": [80, 140, 240, 50]},
                {"id": "archive_bottom", "text": "Archive", "control_type": "button", "rect": [240, 150, 80, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "archive_bottom",
                "rect": [240, 150, 80, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "positional_duplicate_field_recovers_requested_input",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 220, 32], "label": "Email"},
                {"rect": [100, 150, 220, 32], "label": "Phone"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Type in the second field.",
                "target_id": "email",
                "target": {"x": 100, "y": 100, "width": 220, "height": 32},
            },
            "candidates": [
                {"id": "email", "text": "Email", "control_type": "edit", "rect": [100, 100, 220, 32]},
                {"id": "phone", "text": "Phone", "control_type": "edit", "rect": [100, 150, 220, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "phone",
                "rect": [100, 150, 220, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "spelled_out_higher_ordinal_recovers_requested_checkbox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 120, 32], "label": "Option"},
                {"rect": [100, 140, 120, 32], "label": "Option"},
                {"rect": [100, 180, 120, 32], "label": "Option"},
                {"rect": [100, 220, 120, 32], "label": "Option"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Check the fourth checkbox.",
                "target_id": "c1",
                "target": {"x": 100, "y": 100, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "c1", "text": "Option", "control_type": "checkbox", "rect": [100, 100, 120, 32]},
                {"id": "c2", "text": "Option", "control_type": "checkbox", "rect": [100, 140, 120, 32]},
                {"id": "c3", "text": "Option", "control_type": "checkbox", "rect": [100, 180, 120, 32]},
                {"id": "c4", "text": "Option", "control_type": "checkbox", "rect": [100, 220, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "c4",
                "rect": [100, 220, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "card_context_recovers_from_modal_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 100], "label": "Profile card"},
                {"rect": [230, 140, 60, 30], "label": "Save"},
                {"rect": [360, 200, 280, 160], "label": "Save changes modal"},
                {"rect": [480, 310, 60, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the card.",
                "target_id": "modal_save",
                "target": {"x": 480, "y": 310, "width": 60, "height": 30},
            },
            "candidates": [
                {"id": "card", "text": "Profile card", "control_type": "listitem", "rect": [20, 80, 300, 100]},
                {"id": "card_save", "text": "Save", "control_type": "button", "rect": [230, 140, 60, 30]},
                {"id": "modal", "text": "Save changes modal", "control_type": "window", "rect": [360, 200, 280, 160]},
                {"id": "modal_save", "text": "Save", "control_type": "button", "rect": [480, 310, 60, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "card_save",
                "rect": [230, 140, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "sidebar_context_recovers_from_main_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 120], "label": "Main content"},
                {"rect": [230, 160, 60, 30], "label": "Save"},
                {"rect": [420, 80, 300, 120], "label": "Settings sidebar"},
                {"rect": [630, 160, 60, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the sidebar.",
                "target_id": "main_save",
                "target": {"x": 230, "y": 160, "width": 60, "height": 30},
            },
            "candidates": [
                {"id": "main", "text": "Main content", "control_type": "pane", "rect": [20, 80, 300, 120]},
                {"id": "main_save", "text": "Save", "control_type": "button", "rect": [230, 160, 60, 30]},
                {"id": "sidebar", "text": "Settings sidebar", "control_type": "pane", "rect": [420, 80, 300, 120]},
                {"id": "sidebar_save", "text": "Save", "control_type": "button", "rect": [630, 160, 60, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "sidebar_save",
                "rect": [630, 160, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "panel_context_recovers_from_main_pane_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 120], "label": "Main content"},
                {"rect": [230, 160, 60, 30], "label": "Save"},
                {"rect": [420, 80, 300, 120], "label": "Settings panel"},
                {"rect": [630, 160, 60, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the panel.",
                "target_id": "main_save",
                "target": {"x": 230, "y": 160, "width": 60, "height": 30},
            },
            "candidates": [
                {"id": "main", "text": "Main content", "control_type": "pane", "rect": [20, 80, 300, 120]},
                {"id": "main_save", "text": "Save", "control_type": "button", "rect": [230, 160, 60, 30]},
                {"id": "settings_panel", "text": "Settings panel", "control_type": "pane", "rect": [420, 80, 300, 120]},
                {"id": "panel_save", "text": "Save", "control_type": "button", "rect": [630, 160, 60, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "panel_save",
                "rect": [630, 160, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_pane_context_with_duplicate_actions_stays_ambiguous",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 120], "label": "Main content"},
                {"rect": [230, 160, 60, 30], "label": "Save"},
                {"rect": [420, 80, 300, 120], "label": "Settings pane"},
                {"rect": [630, 160, 60, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the pane.",
                "target_id": "main_save",
                "target": {"x": 230, "y": 160, "width": 60, "height": 30},
            },
            "candidates": [
                {"id": "main", "text": "Main content", "control_type": "pane", "rect": [20, 80, 300, 120]},
                {"id": "main_save", "text": "Save", "control_type": "button", "rect": [230, 160, 60, 30]},
                {"id": "settings_pane", "text": "Settings pane", "control_type": "pane", "rect": [420, 80, 300, 120]},
                {"id": "pane_save", "text": "Save", "control_type": "button", "rect": [630, 160, 60, 30]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "main_save",
                "rejected_reason": "target_id ambiguous",
                "overlay_emitted": False,
            },
        },
        {
            "name": "popover_context_recovers_from_main_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 120], "label": "Main content"},
                {"rect": [230, 160, 60, 30], "label": "Save"},
                {"rect": [420, 80, 300, 120], "label": "Settings popover"},
                {"rect": [630, 160, 60, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the popover.",
                "target_id": "main_save",
                "target": {"x": 230, "y": 160, "width": 60, "height": 30},
            },
            "candidates": [
                {"id": "main", "text": "Main content", "control_type": "pane", "rect": [20, 80, 300, 120]},
                {"id": "main_save", "text": "Save", "control_type": "button", "rect": [230, 160, 60, 30]},
                {"id": "popover", "text": "Settings popover", "control_type": "pane", "rect": [420, 80, 300, 120]},
                {"id": "popover_save", "text": "Save", "control_type": "button", "rect": [630, 160, 60, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "popover_save",
                "rect": [630, 160, 60, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "notification_context_recovers_from_main_duplicate_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 300, 120], "label": "Main content"},
                {"rect": [230, 160, 60, 30], "label": "Save"},
                {"rect": [420, 80, 300, 120], "label": "Settings notification"},
                {"rect": [630, 160, 60, 30], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save in the notification.",
                "target_id": "main_save",
                "target": {"x": 230, "y": 160, "width": 60, "height": 30},
            },
            "candidates": [
                {"id": "main", "text": "Main content", "control_type": "pane", "rect": [20, 80, 300, 120]},
                {"id": "main_save", "text": "Save", "control_type": "button", "rect": [230, 160, 60, 30]},
                {"id": "notification", "text": "Settings notification", "control_type": "pane", "rect": [420, 80, 300, 120]},
                {"id": "notification_save", "text": "Save", "control_type": "button", "rect": [630, 160, 60, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "notification_save",
                "rect": [630, 160, 60, 30],
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
            "name": "explicit_combo_box_rejects_same_label_edit_field",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 180, 32], "label": "Settings edit"},
                {"rect": [100, 150, 180, 32], "label": "Settings combo"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings combo box.",
                "target_id": "settings_edit",
                "target": {"x": 100, "y": 100, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "settings_edit", "text": "Settings", "control_type": "edit", "rect": [100, 100, 180, 32]},
                {"id": "settings_combo", "text": "Settings", "control_type": "combobox", "rect": [100, 150, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "settings_combo",
                "rect": [100, 150, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_dropdown_rejects_same_label_button_for_combobox",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 180, 32], "label": "Settings button"},
                {"rect": [100, 150, 180, 32], "label": "Settings combo"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings dropdown.",
                "target_id": "settings_button",
                "target": {"x": 100, "y": 100, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "settings_button", "text": "Settings", "control_type": "button", "rect": [100, 100, 180, 32]},
                {"id": "settings_combo", "text": "Settings", "control_type": "combobox", "rect": [100, 150, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "settings_combo",
                "rect": [100, 150, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_spin_box_rejects_same_label_edit_field",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 180, 32], "label": "Quantity edit"},
                {"rect": [100, 150, 180, 32], "label": "Quantity spinner"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Use Quantity spin box.",
                "target_id": "quantity_edit",
                "target": {"x": 100, "y": 100, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "quantity_edit", "text": "Quantity", "control_type": "edit", "rect": [100, 100, 180, 32]},
                {"id": "quantity_spinner", "text": "Quantity", "control_type": "spinner", "rect": [100, 150, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "quantity_spinner",
                "rect": [100, 150, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_slider_rejects_same_label_list_item",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 120, 32], "label": "Settings item"},
                {"rect": [10, 60, 120, 32], "label": "Settings slider"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select Settings slider.",
                "target_id": "settings_item",
                "target": {"x": 10, "y": 10, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "settings_item", "text": "Settings", "control_type": "listitem", "rect": [10, 10, 120, 32]},
                {"id": "settings_slider", "text": "Settings", "control_type": "slider", "rect": [10, 60, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "settings_slider",
                "rect": [10, 60, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "explicit_panel_rejects_same_label_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 120, 32], "label": "Settings button"},
                {"rect": [10, 60, 220, 120], "label": "Settings panel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Settings panel.",
                "target_id": "settings_button",
                "target": {"x": 10, "y": 10, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "settings_button", "text": "Settings", "control_type": "button", "rect": [10, 10, 120, 32]},
                {"id": "settings_pane", "text": "Settings panel", "control_type": "pane", "rect": [10, 60, 220, 120]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "settings_pane",
                "rect": [10, 60, 220, 120],
                "overlay_emitted": True,
            },
        },
        {
            "name": "select_button_rejects_same_label_radio_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 100, 30], "label": "Settings radio"},
                {"rect": [10, 60, 100, 30], "label": "Settings button"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Select the Settings button.",
                "target_id": "settings_radio",
                "target": {"x": 10, "y": 10, "width": 100, "height": 30},
            },
            "candidates": [
                {"id": "settings_radio", "text": "Settings", "control_type": "radiobutton", "rect": [10, 10, 100, 30]},
                {"id": "settings_button", "text": "Settings", "control_type": "button", "rect": [10, 60, 100, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "settings_button",
                "rect": [10, 60, 100, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dropdown_launcher_rejects_same_label_menuitem_option",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Country"},
                {"rect": [20, 140, 180, 28], "label": "Country"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the Country dropdown.",
                "target_id": "option",
                "target": {"x": 20, "y": 140, "width": 180, "height": 28},
            },
            "candidates": [
                {"id": "combo", "text": "Country", "control_type": "combobox", "rect": [20, 80, 180, 32]},
                {"id": "option", "text": "Country", "control_type": "menuitem", "rect": [20, 140, 180, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "combo",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dropdown_item_request_recovers_from_launcher_target_id",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 180, 32], "label": "Status"},
                {"rect": [10, 46, 180, 28], "label": "Active"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Active in the dropdown.",
                "target_id": "combo",
                "target": {"x": 10, "y": 46, "width": 180, "height": 28},
            },
            "candidates": [
                {"id": "combo", "text": "Status", "control_type": "combobox", "rect": [10, 10, 180, 32]},
                {"id": "active", "text": "Active", "control_type": "menuitem", "rect": [10, 46, 180, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "active",
                "rect": [10, 46, 180, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dropdown_item_request_uses_named_launcher_context",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 180, 32], "label": "Status"},
                {"rect": [10, 46, 180, 28], "label": "Active"},
                {"rect": [250, 10, 180, 32], "label": "Priority"},
                {"rect": [250, 46, 180, 28], "label": "Active"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Active in the Status dropdown.",
                "target_id": "priority_active",
                "target": {"x": 250, "y": 46, "width": 180, "height": 28},
            },
            "candidates": [
                {"id": "status_combo", "text": "Status", "control_type": "combobox", "rect": [10, 10, 180, 32]},
                {"id": "status_active", "text": "Active", "control_type": "menuitem", "rect": [10, 46, 180, 28]},
                {"id": "priority_combo", "text": "Priority", "control_type": "combobox", "rect": [250, 10, 180, 32]},
                {"id": "priority_active", "text": "Active", "control_type": "menuitem", "rect": [250, 46, 180, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "status_active",
                "rect": [10, 46, 180, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dropdown_item_from_dropdown_uses_named_launcher_context",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 180, 32], "label": "Status"},
                {"rect": [10, 46, 180, 28], "label": "Active"},
                {"rect": [250, 10, 180, 32], "label": "Priority"},
                {"rect": [250, 46, 180, 28], "label": "Active"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Active from the Status dropdown.",
                "target_id": "priority_active",
                "target": {"x": 250, "y": 46, "width": 180, "height": 28},
            },
            "candidates": [
                {"id": "status_combo", "text": "Status", "control_type": "combobox", "rect": [10, 10, 180, 32]},
                {"id": "status_active", "text": "Active", "control_type": "menuitem", "rect": [10, 46, 180, 28]},
                {"id": "priority_combo", "text": "Priority", "control_type": "combobox", "rect": [250, 10, 180, 32]},
                {"id": "priority_active", "text": "Active", "control_type": "menuitem", "rect": [250, 46, 180, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "status_active",
                "rect": [10, 46, 180, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dropdown_item_from_menu_uses_named_launcher_context",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [10, 10, 180, 32], "label": "Status"},
                {"rect": [10, 46, 180, 28], "label": "Active"},
                {"rect": [250, 10, 180, 32], "label": "Priority"},
                {"rect": [250, 46, 180, 28], "label": "Active"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Choose Active from the Status menu.",
                "target_id": "priority_active",
                "target": {"x": 250, "y": 46, "width": 180, "height": 28},
            },
            "candidates": [
                {"id": "status_combo", "text": "Status", "control_type": "combobox", "rect": [10, 10, 180, 32]},
                {"id": "status_active", "text": "Active", "control_type": "menuitem", "rect": [10, 46, 180, 28]},
                {"id": "priority_combo", "text": "Priority", "control_type": "combobox", "rect": [250, 10, 180, 32]},
                {"id": "priority_active", "text": "Active", "control_type": "menuitem", "rect": [250, 46, 180, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "status_active",
                "rect": [10, 46, 180, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "dropdown_launcher_rejects_same_label_button_menuitem",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 20, 120, 32], "label": "Account"},
                {"rect": [200, 80, 160, 28], "label": "Account"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open the account dropdown.",
                "target_id": "stale",
                "target": {"x": 200, "y": 80, "width": 160, "height": 28},
            },
            "candidates": [
                {"id": "launcher", "text": "Account", "control_type": "button", "rect": [20, 20, 120, 32]},
                {"id": "stale", "text": "Account", "control_type": "menuitem", "rect": [200, 80, 160, 28], "window_title": "Account menu"},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "launcher",
                "rect": [20, 20, 120, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_dropdown_launcher_recovers_from_open_option",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 160, 32], "label": "Country"},
                {"rect": [20, 114, 160, 28], "label": "Canada"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Open this dropdown.",
                "target_id": "option",
                "target": {"x": 20, "y": 114, "width": 160, "height": 28},
            },
            "candidates": [
                {"id": "combo", "text": "Country", "control_type": "combobox", "rect": [20, 80, 160, 32]},
                {"id": "option", "text": "Canada", "control_type": "menuitem", "rect": [20, 114, 160, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "combo",
                "rect": [20, 80, 160, 32],
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
            "name": "plain_button_wording_recovers_from_splitbutton",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 80, 100, 32], "label": "Settings"},
                {"rect": [20, 130, 140, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings button.",
                "target_id": "split",
                "target": {"x": 20, "y": 130, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "button", "text": "Settings", "control_type": "button", "rect": [20, 80, 100, 32]},
                {"id": "split", "text": "Settings", "control_type": "splitbutton", "rect": [20, 130, 140, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "button",
                "rect": [20, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bare_option_recovers_checkbox_from_button_target",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Settings"},
                {"rect": [20, 130, 140, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings option.",
                "target_id": "button",
                "target": {"x": 20, "y": 130, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "check", "text": "Settings", "control_type": "checkbox", "rect": [20, 80, 140, 32]},
                {"id": "button", "text": "Settings", "control_type": "button", "rect": [20, 130, 140, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "check",
                "rect": [20, 80, 140, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "bare_option_mixed_roles_reject_overlay",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Settings"},
                {"rect": [20, 130, 140, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings option.",
                "target_id": "menu",
                "target": {"x": 20, "y": 130, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "radio", "text": "Settings", "control_type": "radiobutton", "rect": [20, 80, 140, 32]},
                {"id": "menu", "text": "Settings", "control_type": "menuitem", "rect": [20, 130, 140, 32]},
            ],
            "expected": {
                "source": "target_id",
                "target_id": "menu",
                "rejected_reason": "target_id control type mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "plain_field_wording_recovers_from_combobox",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 80, 180, 32], "label": "Settings"},
                {"rect": [20, 130, 180, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings input field.",
                "target_id": "combo",
                "target": {"x": 20, "y": 130, "width": 180, "height": 32},
            },
            "candidates": [
                {"id": "edit", "text": "Settings", "control_type": "edit", "rect": [20, 80, 180, 32]},
                {"id": "combo", "text": "Settings", "control_type": "combobox", "rect": [20, 130, 180, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "edit",
                "rect": [20, 80, 180, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "grid_cell_wording_recovers_from_plain_cell",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 80, 120, 30], "label": "Settings"},
                {"rect": [20, 130, 120, 30], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings grid cell.",
                "target_id": "cell",
                "target": {"x": 20, "y": 130, "width": 120, "height": 30},
            },
            "candidates": [
                {"id": "grid", "text": "Settings", "control_type": "gridcell", "rect": [20, 80, 120, 30]},
                {"id": "cell", "text": "Settings", "control_type": "cell", "rect": [20, 130, 120, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "grid",
                "rect": [20, 80, 120, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "context_action_recovers_from_context_label_button",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 40, 400, 140], "label": "Settings dialog", "fill": "#eef2ff"},
                {"rect": [40, 80, 100, 32], "label": "Cancel"},
                {"rect": [40, 130, 100, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Cancel in the Settings dialog.",
                "target_id": "settings_button",
                "target": {"x": 40, "y": 130, "width": 100, "height": 32},
            },
            "candidates": [
                {"id": "dialog", "text": "Settings dialog", "control_type": "window", "rect": [20, 40, 400, 140]},
                {"id": "cancel", "text": "Cancel", "control_type": "button", "rect": [40, 80, 100, 32]},
                {"id": "settings_button", "text": "Settings", "control_type": "button", "rect": [40, 130, 100, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "cancel",
                "rect": [40, 80, 100, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "generic_icon_recovers_from_same_label_checkbox",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 80, 140, 32], "label": "Settings"},
                {"rect": [20, 130, 40, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Settings icon.",
                "target_id": "check",
                "target": {"x": 20, "y": 80, "width": 140, "height": 32},
            },
            "candidates": [
                {"id": "check", "text": "Settings", "control_type": "checkbox", "rect": [20, 80, 140, 32]},
                {"id": "icon", "text": "Settings", "control_type": "button", "rect": [20, 130, 40, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "icon",
                "rect": [20, 130, 40, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "context_field_recovers_from_context_label_button",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 40, 400, 160], "label": "Settings form", "fill": "#f1f5f9"},
                {"rect": [40, 80, 220, 32], "label": "Email"},
                {"rect": [40, 130, 120, 32], "label": "Settings"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Type Email in the Settings form.",
                "target_id": "settings_button",
                "target": {"x": 40, "y": 130, "width": 120, "height": 32},
            },
            "candidates": [
                {"id": "form", "text": "Settings form", "control_type": "pane", "rect": [20, 40, 400, 160]},
                {"id": "email", "text": "Email", "control_type": "edit", "rect": [40, 80, 220, 32]},
                {"id": "settings_button", "text": "Settings", "control_type": "button", "rect": [40, 130, 120, 32]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "email",
                "rect": [40, 80, 220, 32],
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
            "name": "shared_window_title_context_does_not_steal_duplicate_action",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [20, 70, 100, 20], "label": "Profile"},
                {"rect": [20, 100, 70, 24], "label": "Delete"},
                {"rect": [20, 170, 100, 20], "label": "Billing"},
                {"rect": [20, 200, 70, 24], "label": "Delete"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Delete in Billing.",
                "target_id": "delete_profile",
                "target": {"x": 20, "y": 100, "width": 70, "height": 24},
            },
            "candidates": [
                {
                    "id": "delete_profile",
                    "text": "Delete",
                    "control_type": "button",
                    "rect": [20, 100, 70, 24],
                    "window_title": "Billing - Admin",
                },
                {
                    "id": "delete_billing",
                    "text": "Delete",
                    "control_type": "button",
                    "rect": [20, 200, 70, 24],
                    "window_title": "Billing - Admin",
                },
                {
                    "id": "profile_label",
                    "text": "Profile",
                    "control_type": "text",
                    "rect": [20, 70, 100, 20],
                    "window_title": "Billing - Admin",
                },
                {
                    "id": "billing_label",
                    "text": "Billing",
                    "control_type": "text",
                    "rect": [20, 170, 100, 20],
                    "window_title": "Billing - Admin",
                },
            ],
            "expected": {
                "source": "text_match",
                "target_id": "delete_billing",
                "rect": [20, 200, 70, 24],
                "overlay_emitted": True,
            },
        },
        {
            "name": "page_behind_dialog_target_id_rejects_overlay",
            "capture": {"width": 800, "height": 520},
            "draw": [
                {"rect": [100, 100, 70, 30], "label": "Save"},
                {"rect": [360, 200, 300, 180], "label": "Confirm changes"},
                {"rect": [420, 320, 80, 32], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Save on the page behind the dialog.",
                "target_id": "page_save",
                "target": {"x": 100, "y": 100, "width": 70, "height": 30},
            },
            "candidates": [
                {
                    "id": "dialog",
                    "text": "Confirm changes dialog",
                    "control_type": "window",
                    "rect": [360, 200, 300, 180],
                    "window_rank": 0,
                },
                {
                    "id": "dialog_cancel",
                    "text": "Cancel",
                    "control_type": "button",
                    "rect": [420, 320, 80, 32],
                    "window_rank": 0,
                },
                {
                    "id": "page_save",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 70, 30],
                    "window_title": "Editor",
                    "window_rank": 1,
                },
            ],
            "expected": {
                "source": "target_id",
                "target_id": "page_save",
                "rejected_reason": "target_id semantic mismatch",
                "overlay_emitted": False,
            },
        },
        {
            "name": "same_rect_foreground_snap_prefers_active_window",
            "capture": {"width": 500, "height": 320},
            "draw": [
                {"rect": [100, 100, 80, 32], "label": "Save"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click this button.",
                "target": {"x": 200, "y": 313, "width": 160, "height": 100},
            },
            "candidates": [
                {
                    "id": "background",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 80, 32],
                    "window_title": "Background Editor",
                    "window_rank": 1,
                },
                {
                    "id": "foreground",
                    "text": "Save",
                    "control_type": "button",
                    "rect": [100, 100, 80, 32],
                    "window_title": "Foreground Editor",
                    "window_rank": 0,
                },
            ],
            "expected": {
                "source": "candidate_snap",
                "target_id": "foreground",
                "rect": [100, 100, 80, 32],
                "overlay_emitted": True,
            },
        },
        {
            "name": "row_action_recovers_from_wrong_target_id_in_same_row",
            "capture": {"width": 760, "height": 360},
            "draw": [
                {"rect": [20, 100, 620, 48], "label": "Bob invoice"},
                {"rect": [520, 112, 70, 30], "label": "Delete"},
                {"rect": [600, 112, 80, 30], "label": "Archive"},
                {"rect": [20, 160, 620, 48], "label": "Ada invoice"},
                {"rect": [600, 172, 80, 30], "label": "Archive"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Archive for Bob invoice.",
                "target_id": "delete_bob",
                "target": {"x": 520, "y": 112, "width": 70, "height": 30},
            },
            "candidates": [
                {"id": "row_bob", "text": "Bob invoice", "control_type": "dataitem", "rect": [20, 100, 620, 48]},
                {"id": "delete_bob", "text": "Delete", "control_type": "button", "rect": [520, 112, 70, 30]},
                {"id": "archive_bob", "text": "Archive", "control_type": "button", "rect": [600, 112, 80, 30]},
                {"id": "row_ada", "text": "Ada invoice", "control_type": "dataitem", "rect": [20, 160, 620, 48]},
                {"id": "archive_ada", "text": "Archive", "control_type": "button", "rect": [600, 172, 80, 30]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "archive_bob",
                "rect": [600, 112, 80, 30],
                "overlay_emitted": True,
            },
        },
        {
            "name": "nested_row_label_recovers_to_requested_subrow_action",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [20, 80, 760, 120], "label": "Acme order"},
                {"rect": [40, 110, 120, 20], "label": "Billing"},
                {"rect": [650, 106, 70, 28], "label": "Delete"},
                {"rect": [40, 160, 120, 20], "label": "Shipping"},
                {"rect": [650, 156, 70, 28], "label": "Delete"},
                {"rect": [20, 230, 760, 120], "label": "Globex order"},
                {"rect": [40, 260, 120, 20], "label": "Shipping"},
                {"rect": [650, 256, 70, 28], "label": "Delete"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click Delete in Shipping for Acme order.",
                "target_id": "del_bill",
                "target": {"x": 650, "y": 106, "width": 70, "height": 28},
            },
            "candidates": [
                {"id": "row_acme", "text": "Acme order", "control_type": "dataitem", "rect": [20, 80, 760, 120]},
                {"id": "bill_label", "text": "Billing", "control_type": "text", "rect": [40, 110, 120, 20]},
                {"id": "ship_label", "text": "Shipping", "control_type": "text", "rect": [40, 160, 120, 20]},
                {"id": "del_bill", "text": "Delete", "control_type": "button", "rect": [650, 106, 70, 28]},
                {"id": "del_ship", "text": "Delete", "control_type": "button", "rect": [650, 156, 70, 28]},
                {"id": "row_globex", "text": "Globex order", "control_type": "dataitem", "rect": [20, 230, 760, 120]},
                {"id": "ship_label_g", "text": "Shipping", "control_type": "text", "rect": [40, 260, 120, 20]},
                {"id": "del_ship_g", "text": "Delete", "control_type": "button", "rect": [650, 256, 70, 28]},
            ],
            "expected": {
                "source": "text_match",
                "target_id": "del_ship",
                "rect": [650, 156, 70, 28],
                "overlay_emitted": True,
            },
        },
        {
            "name": "container_text_does_not_promote_contradictory_contained_button",
            "capture": {"width": 1000, "height": 1000},
            "draw": [
                {"rect": [100, 100, 300, 100], "label": "Save settings"},
                {"rect": [120, 130, 70, 28], "label": "Cancel"},
            ],
            "decision": {
                "kind": "step",
                "instruction": "Click the Save button.",
                "target": {"x": 100, "y": 100, "width": 300, "height": 100},
            },
            "candidates": [
                {"id": "panel", "text": "Save settings", "control_type": "pane", "rect": [100, 100, 300, 100]},
                {"id": "cancel", "text": "Cancel", "control_type": "button", "rect": [120, 130, 70, 28]},
            ],
            "expected": {
                "source": "candidate_snap",
                "rejected_reason": "candidate snapshot no match",
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
