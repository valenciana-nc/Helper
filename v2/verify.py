"""Verifier: did the subgoal actually happen?

Two stages, cheapest first:
1. Pixel-diff the target region between before/after crops. No change → False.
2. Ask the VLM on the CROPPED before/after pair + success criteria → yes/no.

Never a full screenshot; crops only.
"""
from __future__ import annotations

import base64
import io

from PIL import Image, ImageChops, ImageStat

from .plan import LlmPort, Subgoal

# Mean per-channel difference below this counts as "nothing changed".
DIFF_FLOOR = 4.0


def regions_differ(before_png: bytes, after_png: bytes) -> bool:
    """True if the two crops differ beyond the noise floor."""
    try:
        a = Image.open(io.BytesIO(before_png)).convert("RGB")
        b = Image.open(io.BytesIO(after_png)).convert("RGB")
    except Exception:
        return True  # undecodable → don't claim "no change"
    if a.size != b.size:
        b = b.resize(a.size, Image.Resampling.BILINEAR)
    diff = ImageChops.difference(a, b)
    stat = ImageStat.Stat(diff)
    mean = sum(stat.mean) / max(1, len(stat.mean))
    return mean > DIFF_FLOOR


VERIFIER_SYSTEM = """You are a strict verifier for a desktop computer-use agent. You see two cropped screenshots: BEFORE and AFTER an action. Answer whether the success criterion is now true.

Reply with ONLY "yes" or "no". Be strict: only say yes if the criterion is clearly satisfied in the AFTER image."""


def verify_subgoal(
    subgoal: Subgoal,
    before_crop_png: bytes,
    after_crop_png: bytes,
    llm: LlmPort,
    *,
    max_tokens: int = 64,
) -> bool:
    """Return True iff the subgoal's success criteria holds after the action."""
    if not regions_differ(before_crop_png, after_crop_png):
        return False
    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"Action taken: {subgoal.description}\n"
                        f"Success criterion: {subgoal.success_criteria}\n"
                        "Is the criterion satisfied in the AFTER image? Reply yes or no."
                    ),
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(before_crop_png).decode("ascii")
                    },
                },
                {
                    "type": "image_url",
                    "image_url": {
                        "url": "data:image/png;base64,"
                        + base64.b64encode(after_crop_png).decode("ascii")
                    },
                },
            ],
        }
    ]
    raw = llm.chat(messages, system=VERIFIER_SYSTEM, max_tokens=max_tokens)
    return raw.strip().lower().startswith("yes")
