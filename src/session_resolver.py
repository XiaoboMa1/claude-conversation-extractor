#!/usr/bin/env python3
"""
Session resolution utilities for Claude Code.

Resolves session identifiers (UUID prefix, slug, or customTitle) to jsonl file paths.
Used by both extract and think_realtime commands.

Session naming in Claude Code jsonl:
- slug: auto-generated name, stored as a top-level field in most lines (e.g. "peppy-twirling-wren")
- customTitle: user-set via /rename, stored in dedicated {"type": "custom-title"} lines
- Display priority: customTitle > slug > first 6 chars of UUID
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def get_claude_projects_dir() -> Path:
    """Return the Claude Code projects directory."""
    return Path.home() / ".claude" / "projects"


def get_session_name(jsonl_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """
    Extract the display name of a session from its jsonl file.

    Scans the file for:
    1. customTitle (from type=="custom-title" lines) — user-set via /rename
    2. slug (from any line's top-level "slug" field) — auto-generated

    Returns (custom_title, slug). Either or both may be None.
    The caller should prefer custom_title over slug for display.
    """
    custom_title = None
    slug = None

    try:
        with open(jsonl_path, encoding="utf-8") as f:
            for line in f:
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                # Pick up slug from any line (it's repeated, so first occurrence suffices)
                if not slug and obj.get("slug"):
                    slug = obj["slug"]

                # custom-title lines record /rename results
                if obj.get("type") == "custom-title":
                    custom_title = obj.get("customTitle", custom_title)
                    # Don't break — a later /rename might override

                # Early exit optimization: if we have both, only keep scanning
                # for potential later custom-title overrides (rare)
                # But to keep it simple, scan full file for correctness
    except (OSError, IOError):
        pass

    return (custom_title, slug)


def get_session_display_name(jsonl_path: Path) -> str:
    """
    Return the best display name for a session.
    Priority: customTitle > slug > first 6 chars of UUID.
    """
    custom_title, slug = get_session_name(jsonl_path)
    if custom_title:
        return custom_title
    if slug:
        return slug
    return jsonl_path.stem[:6]


def build_session_index(project_filter: Optional[str] = None) -> List[Dict]:
    """
    Build an index of all sessions with their names.

    Returns a list of dicts:
    {
        "path": Path,
        "session_id": str (full UUID),
        "slug": str or None,
        "custom_title": str or None,
        "display_name": str,
        "mtime": float,
        "project_dir": str (encoded project directory name),
    }

    Sorted by mtime descending (most recent first).
    Skips subagent files.
    """
    projects_dir = get_claude_projects_dir()
    if not projects_dir.exists():
        return []

    sessions = []
    for jsonl_file in projects_dir.rglob("*.jsonl"):
        if "subagents" in jsonl_file.parts:
            continue

        # Determine project directory
        parent = jsonl_file.parent
        if parent.name == jsonl_file.stem:
            project_dir = parent.parent
        else:
            project_dir = parent

        if project_filter and project_filter not in str(project_dir):
            continue

        custom_title, slug = get_session_name(jsonl_file)
        display_name = custom_title or slug or jsonl_file.stem[:6]

        sessions.append({
            "path": jsonl_file,
            "session_id": jsonl_file.stem,
            "slug": slug,
            "custom_title": custom_title,
            "display_name": display_name,
            "mtime": jsonl_file.stat().st_mtime,
            "project_dir": project_dir.name,
        })

    sessions.sort(key=lambda x: x["mtime"], reverse=True)
    return sessions


def resolve_session(identifier: str) -> Optional[Path]:
    """
    Resolve a session identifier to a jsonl file path.

    Matching strategy (in order):
    1. UUID prefix match: if identifier looks like a hex prefix (>= 6 chars, hex-only),
       match against session filenames.
    2. Name match: match against customTitle (exact, case-insensitive),
       then slug (exact, case-insensitive),
       then partial match on either.

    If multiple matches: return the most recently modified one.
    Returns None if no match.
    """
    projects_dir = get_claude_projects_dir()
    if not projects_dir.exists():
        return None

    # Determine if identifier looks like a UUID prefix (all hex chars + dashes)
    clean_id = identifier.replace("-", "")
    is_uuid_like = len(clean_id) >= 6 and all(c in "0123456789abcdefABCDEF" for c in clean_id)

    if is_uuid_like:
        # UUID prefix match
        matches = []
        for jsonl_file in projects_dir.rglob("*.jsonl"):
            if "subagents" in jsonl_file.parts:
                continue
            if jsonl_file.stem.startswith(identifier) or jsonl_file.stem.startswith(clean_id):
                matches.append(jsonl_file)
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)

    # Name-based match: scan all sessions for slug/customTitle
    identifier_lower = identifier.lower()
    exact_matches = []
    partial_matches = []

    for jsonl_file in projects_dir.rglob("*.jsonl"):
        if "subagents" in jsonl_file.parts:
            continue

        custom_title, slug = get_session_name(jsonl_file)

        # Exact match on customTitle or slug
        if custom_title and custom_title.lower() == identifier_lower:
            exact_matches.append(jsonl_file)
            continue
        if slug and slug.lower() == identifier_lower:
            exact_matches.append(jsonl_file)
            continue

        # Partial/substring match
        if custom_title and identifier_lower in custom_title.lower():
            partial_matches.append(jsonl_file)
        elif slug and identifier_lower in slug.lower():
            partial_matches.append(jsonl_file)

    if exact_matches:
        return max(exact_matches, key=lambda p: p.stat().st_mtime)
    if partial_matches:
        return max(partial_matches, key=lambda p: p.stat().st_mtime)

    # Fallback: also try UUID prefix match even for shorter strings
    # (in case user passed 4-5 chars)
    if len(identifier) >= 4:
        matches = []
        for jsonl_file in projects_dir.rglob("*.jsonl"):
            if "subagents" in jsonl_file.parts:
                continue
            if jsonl_file.stem.startswith(identifier):
                matches.append(jsonl_file)
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)

    return None


def format_session_filename(session_path: Path, extension: str = "md") -> str:
    """
    Generate a brief, useful output filename for a session.

    Format: claude-chat-<display_name>.<extension>
    Where display_name is: customTitle > slug > first 6 chars of UUID.

    Sanitizes the name for filesystem safety.
    """
    display_name = get_session_display_name(session_path)

    # Sanitize for filesystem
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "-" for c in display_name)
    safe_name = safe_name.strip("-_ ")
    # Collapse multiple dashes/spaces
    while "--" in safe_name:
        safe_name = safe_name.replace("--", "-")
    safe_name = safe_name.replace(" ", "-")

    if not safe_name:
        safe_name = session_path.stem[:6]

    return f"claude-chat-{safe_name}.{extension}"
