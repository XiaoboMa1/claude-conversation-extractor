"""
Shared message extraction and noise filtering utilities.

Used by project listing and search CLI to extract meaningful
first/last messages from sessions, filter system noise, and
count subagent types.
"""

import json
import re
from pathlib import Path
from typing import Dict

# ── Noise detection ──────────────────────────────────────────────────

# Messages starting with any of these are system noise, not real content.
NOISE_PREFIXES = [
    "Caveat: The messages below were generated",
    "<local-command-caveat>",
    "<system-reminder>",
    "<task-notification>",
    "This session is being continued from a previous conversation",
    "You've hit your limit",
    "Prompt is too long",
    "No response requested",
    "No response needed",
    "No response required",
    "[Request interrupted",
    "Tool use interrupted",
]

# Skills messages start with this prefix.  They are not noise per se
# (the session was invoked via a skill), but listing/browsing should
# treat them specially -- show the *last* N lines instead of the first.
SKILL_PREFIX = "Base directory for this skill"

# XML tag pairs used by Claude Code for slash commands and local-command output.
_XML_TAG_PAIR_RE = re.compile(r"<[^>]+>.*?</[^>]+>", re.DOTALL)


def is_noise_message(text: str) -> bool:
    """Return True if *text* is system noise rather than real user/assistant content."""
    stripped = text.strip()
    if not stripped:
        return True
    for prefix in NOISE_PREFIXES:
        if stripped.startswith(prefix):
            return True
    # If removing all XML tag pairs leaves < 5 meaningful characters, it's noise.
    cleaned = _XML_TAG_PAIR_RE.sub("", stripped).strip()
    if len(cleaned) < 5:
        return True
    return False


def is_skill_message(text: str) -> bool:
    """Return True if *text* is a skill invocation (starts with skill prefix)."""
    return text.strip().startswith(SKILL_PREFIX)


def strip_skill_prompt(text: str) -> str:
    """Strip the auto-injected skill template from a skill message.

    If ``ARGUMENTS:`` is present, returns everything after it (the
    user's actual input).  If absent, the message is a pure skill
    invocation with no user content — returns empty string.

    Non-skill messages are returned unchanged.
    """
    if not is_skill_message(text):
        return text
    marker = "ARGUMENTS:"
    pos = text.find(marker)
    if pos < 0:
        return ""
    return text[pos + len(marker):].strip()


# ── Content extraction ───────────────────────────────────────────────


def extract_text_from_content(content) -> str:
    """Extract plain text from a JSONL message ``content`` field.

    Content can be a plain string or a list of typed blocks.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "\n".join(parts)
    return str(content) if content else ""


def _clean_for_display(text: str) -> str:
    """Strip XML noise tags and collapse blank lines for display."""
    cleaned = _XML_TAG_PAIR_RE.sub("", text).strip()
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned


# ── First / last meaningful message ──────────────────────────────────


def get_first_meaningful_message(
    jsonl_path: Path, role: str = "user", max_lines: int = 3
) -> str:
    """Return the first non-noise message of *role*.

    Returns the first ``max_lines`` lines of the first qualifying message.
    """
    target_type = role
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != target_type:
                    continue
                msg = entry.get("message", {})
                if not isinstance(msg, dict) or msg.get("role") != role:
                    continue

                text = extract_text_from_content(msg.get("content", ""))
                if not text or is_noise_message(text):
                    continue

                clean = _clean_for_display(text)
                if not clean or len(clean) <= 3:
                    continue

                # Strip skill template — only keep user args
                if is_skill_message(clean):
                    clean = strip_skill_prompt(clean)
                    if not clean:
                        continue  # pure invocation, no user input
                    lines = [ln for ln in clean.split("\n") if ln.strip()]
                    return "\n".join(lines[-max_lines:])

                lines = [ln for ln in clean.split("\n") if ln.strip()]
                return "\n".join(lines[:max_lines])
    except (OSError, IOError):
        pass
    return ""


def get_last_meaningful_message(
    jsonl_path: Path, role: str = "assistant", max_lines: int = 3
) -> str:
    """Return the last non-noise message of *role*.

    Returns the first ``max_lines`` lines of the *last* qualifying message.
    """
    target_type = role
    last_clean = ""
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") != target_type:
                    continue
                msg = entry.get("message", {})
                if not isinstance(msg, dict) or msg.get("role") != role:
                    continue

                text = extract_text_from_content(msg.get("content", ""))
                if not text or is_noise_message(text):
                    continue

                clean = _clean_for_display(text)
                if not clean or len(clean) <= 3:
                    continue

                # Strip skill template — only keep user args
                if is_skill_message(clean):
                    clean = strip_skill_prompt(clean)
                    if not clean:
                        continue

                last_clean = clean
    except (OSError, IOError):
        pass

    if last_clean:
        lines = [ln for ln in last_clean.split("\n") if ln.strip()]
        return "\n".join(lines[:max_lines])
    return ""


def get_last_message_tail(
    jsonl_path: Path, max_lines: int = 5
) -> str:
    """Return the last ``max_lines`` lines of the last non-noise user or assistant message.

    Unlike ``get_last_meaningful_message`` which takes lines from the top,
    this returns lines from the *bottom* of the message — useful for
    previewing what a session ended with.
    """
    last_clean = ""
    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("type") not in ("user", "assistant"):
                    continue
                msg = entry.get("message", {})
                if not isinstance(msg, dict) or msg.get("role") not in ("user", "assistant"):
                    continue

                text = extract_text_from_content(msg.get("content", ""))
                if not text or is_noise_message(text):
                    continue

                clean = _clean_for_display(text)
                if not clean or len(clean) <= 3:
                    continue

                # Strip skill template — only keep user args
                if is_skill_message(clean):
                    clean = strip_skill_prompt(clean)
                    if not clean:
                        continue

                last_clean = clean
    except (OSError, IOError):
        pass

    if last_clean:
        lines = [ln for ln in last_clean.split("\n") if ln.strip()]
        return "\n".join(lines[-max_lines:])
    return ""


# ── Subagent counting ────────────────────────────────────────────────


def count_subagents_by_type(session_path: Path) -> Dict[str, int]:
    """Count subagents grouped by type.

    Returns a dict like ``{"explore": 3, "compact": 2}``.

    Detection: filename ``agent-acompact-*`` -> "compact",
    otherwise read ``agentType`` from ``.meta.json`` sidecar.
    """
    session_id = session_path.stem
    subagents_dir = session_path.parent / session_id / "subagents"

    counts: Dict[str, int] = {}
    if not subagents_dir.exists():
        return counts

    for jsonl_file in subagents_dir.glob("*.jsonl"):
        if jsonl_file.stem.startswith("agent-acompact-"):
            agent_type = "compact"
        else:
            agent_type = "explore"  # default
            meta_file = jsonl_file.with_suffix(".meta.json")
            if meta_file.exists():
                try:
                    with open(meta_file, encoding="utf-8") as f:
                        meta = json.load(f)
                        agent_type = meta.get("agentType", "explore")
                except Exception:
                    pass
        counts[agent_type] = counts.get(agent_type, 0) + 1

    return counts
