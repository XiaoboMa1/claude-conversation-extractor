"""
Load and merge JSONL session messages into browsable units.

A "message" here is the same unit used by the extract pipeline:
all consecutive JSONL entries from the same speaker are merged into
one logical message.  Noise (system prompts, task-notifications,
XML wrappers, "No response requested", etc.) is stripped.

This module is the shared data layer for both the interactive browser
and the extract command -- it reads raw JSONL once, returns a list of
``BrowsableMessage`` dicts ready for display or export.
"""

import json
from pathlib import Path
from typing import Dict, List

from .message import (
    extract_text_from_content,
    is_noise_message,
    is_skill_message,
    strip_skill_prompt,
    _clean_for_display,
)


def _preview_lines(text: str, n: int = 3) -> str:
    """Return the first *n* non-blank lines of *text*."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines[:n])


def _skill_preview_lines(text: str, n: int = 3) -> str:
    """Return the last *n* non-blank lines of *text* (for skill messages)."""
    lines = [ln for ln in text.split("\n") if ln.strip()]
    return "\n".join(lines[-n:])


def load_messages(jsonl_path: Path) -> List[Dict]:
    """Parse a JSONL session file into merged, cleaned messages.

    Returns a list of dicts, each with:
        id       : 1-based int (stable display ID)
        role     : "user" or "assistant"
        content  : full cleaned text
        preview  : first 3 lines (or last 3 for skill messages)
        is_skill : bool
        timestamp: ISO string from the first JSONL entry in this message
    """
    raw_messages = _extract_raw_messages(jsonl_path)
    merged = _merge_consecutive(raw_messages)
    # Assign stable 1-based IDs
    for i, msg in enumerate(merged, 1):
        msg["id"] = i
    return merged


def _extract_raw_messages(jsonl_path: Path) -> List[Dict]:
    """Read JSONL and extract individual user/assistant messages.

    Filters out:
    - task-notifications (content starts with <task-notification>)
    - system noise (detected by is_noise_message)
    - empty messages after XML tag stripping

    Does NOT merge consecutive same-speaker entries yet.
    """
    messages: List[Dict] = []

    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type")
                if entry_type not in ("user", "assistant"):
                    continue

                msg = entry.get("message", {})
                if not isinstance(msg, dict):
                    continue
                role = msg.get("role", "")
                if role not in ("user", "assistant"):
                    continue

                content = msg.get("content", "")
                text = extract_text_from_content(content)
                if not text or not text.strip():
                    continue

                # Check noise on raw text first (catches <task-notification>
                # whose trailing text survives XML stripping)
                if is_noise_message(text):
                    continue

                clean = _clean_for_display(text)
                if not clean or len(clean.strip()) < 3:
                    continue

                if is_noise_message(clean):
                    continue

                # Strip skill template — keep only user args after ARGUMENTS:
                skill = is_skill_message(clean)
                if skill:
                    clean = strip_skill_prompt(clean)
                    if not clean:
                        # Pure skill invocation with no user input — drop
                        continue

                messages.append({
                    "role": role,
                    "content": clean,
                    "is_skill": skill,
                    "timestamp": entry.get("timestamp", ""),
                })
    except (OSError, IOError):
        pass

    return messages


def _merge_consecutive(messages: List[Dict]) -> List[Dict]:
    """Merge consecutive messages from the same role into one unit.

    The merged message keeps the timestamp of the first entry.
    ``is_skill`` is True if any constituent was a skill message.
    """
    merged: List[Dict] = []

    for msg in messages:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n\n" + msg["content"]
            if msg["is_skill"]:
                merged[-1]["is_skill"] = True
        else:
            merged.append(dict(msg))

    # Build previews after merging
    for msg in merged:
        if msg["is_skill"]:
            msg["preview"] = _skill_preview_lines(msg["content"])
        else:
            msg["preview"] = _preview_lines(msg["content"])

    return merged
