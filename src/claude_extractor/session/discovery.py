"""
Session discovery and preview for Claude Code conversations.

Pure functions for finding JSONL session files under ~/.claude/projects/
and extracting preview metadata.  No output-directory or formatting concerns.
"""

import json
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple


# ── Find sessions ─────────────────────────────────────────────────────


def find_sessions(
    claude_dir: Path,
    project_path: Optional[str] = None,
    include_subagents: bool = False,
) -> List[Path]:
    """Find all JSONL session files, sorted by most recent first.

    Only returns top-level session files by default (excludes subagent
    files inside ``<session-id>/subagents/`` directories).
    """
    search_dir = claude_dir / project_path if project_path else claude_dir

    sessions: List[Path] = []
    if search_dir.exists():
        for jsonl_file in search_dir.rglob("*.jsonl"):
            if not include_subagents and "subagents" in jsonl_file.parts:
                continue
            sessions.append(jsonl_file)

    return sorted(
        sessions, key=lambda x: (x.stat().st_mtime, str(x)), reverse=True
    )


def find_subagents(session_path: Path) -> List[Tuple[Path, Dict]]:
    """Find all subagent JSONL files belonging to a session.

    Skips auto-compaction subagents (``agent-acompact-*``) which are
    compressed duplicates of the main session conversation.
    """
    session_id = session_path.stem
    session_dir = session_path.parent / session_id / "subagents"

    subagents: List[Tuple[Path, Dict]] = []
    if session_dir.exists():
        for jsonl_file in session_dir.glob("*.jsonl"):
            if jsonl_file.stem.startswith("agent-acompact-"):
                continue

            meta: Dict = {}
            meta_file = jsonl_file.with_suffix(".meta.json")
            if meta_file.exists():
                try:
                    with open(meta_file, "r", encoding="utf-8") as f:
                        meta = json.load(f)
                except Exception:
                    pass
            subagents.append((jsonl_file, meta))

    return sorted(subagents, key=lambda x: x[0].stat().st_mtime)


def get_subagent_header(jsonl_path: Path) -> Optional[str]:
    """Extract the first markdown header from the last assistant response.

    Used to give Explore subagents a meaningful label instead of their
    opaque agent ID.
    """
    last_text = ""
    try:
        with open(jsonl_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "assistant":
                    continue
                msg = entry.get("message", {})
                content = msg.get("content", [])
                if isinstance(content, list):
                    for item in content:
                        if isinstance(item, dict) and item.get("type") == "text":
                            last_text = item.get("text", "")
                elif isinstance(content, str):
                    last_text = content
    except Exception:
        return None

    if not last_text:
        return None

    for ln in last_text.split("\n"):
        m = re.match(r"^#{1,3}\s+(.+)", ln.strip())
        if m:
            return m.group(1).strip()
    return None


def find_session_by_id(
    claude_dir: Path, session_id_prefix: str, quiet: bool = False
) -> Optional[Path]:
    """Find a session JSONL file by session ID prefix."""
    all_sessions = find_sessions(claude_dir)
    matches = [s for s in all_sessions if s.stem.startswith(session_id_prefix)]

    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        print(f"Prefix '{session_id_prefix}' matches {len(matches)} sessions:")
        for m in matches:
            print(f"   {m.stem}")
        print("   Please use a longer prefix to disambiguate.")
        return None
    else:
        if not quiet:
            print(f"No session found matching prefix '{session_id_prefix}'")
        return None


# ── Preview / metadata ────────────────────────────────────────────────


def get_conversation_preview(session_path: Path) -> Tuple[str, int]:
    """Get a preview of the conversation's first real user message and message count."""
    try:
        first_user_msg = ""
        msg_count = 0

        with open(session_path, "r", encoding="utf-8") as f:
            for line in f:
                msg_count += 1
                if not first_user_msg:
                    try:
                        data = json.loads(line)
                        if data.get("type") == "user" and "message" in data:
                            msg = data["message"]
                            if msg.get("role") == "user":
                                content = msg.get("content", "")
                                first_user_msg = _extract_first_user_text(content)
                    except json.JSONDecodeError:
                        continue

        return first_user_msg or "No preview available", msg_count
    except Exception as e:
        return f"Error: {str(e)[:30]}", 0


def _extract_first_user_text(content) -> str:
    """Extract the first meaningful user text from content for preview."""
    if isinstance(content, list):
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text", "").strip()

                if text.startswith("tool_use_id"):
                    continue
                if "[Request interrupted" in text:
                    continue
                if "session is being continued" in text.lower():
                    continue

                text = re.sub(r"<[^>]+>", "", text).strip()

                if "is running" in text and "\u2026" in text:
                    continue

                if text.startswith("[Image #"):
                    parts = text.split("]", 1)
                    if len(parts) > 1:
                        text = parts[1].strip()

                if text and len(text) > 3:
                    return text[:100].replace("\n", " ")

    elif isinstance(content, str):
        text = content.strip()
        text = re.sub(r"<[^>]+>", "", text).strip()

        if "is running" in text and "\u2026" in text:
            return ""
        if "session is being continued" in text.lower():
            return ""
        if text.startswith("tool_use_id") or "[Request interrupted" in text:
            return ""

        if text and len(text) > 3:
            return text[:100].replace("\n", " ")

    return ""
