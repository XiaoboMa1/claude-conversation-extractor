#!/usr/bin/env python3
"""
Session resolution utilities for Claude Code.

Resolves session identifiers (UUID prefix, slug, or customTitle) to jsonl file paths.

Session naming in Claude Code jsonl:
- slug: auto-generated name, stored as a top-level field in most lines
- customTitle: user-set via /rename, stored in {"type": "custom-title"} lines
- Display priority: customTitle > slug > first 6 chars of UUID
"""

import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple


def get_claude_projects_dir() -> Path:
    """Return the Claude Code projects directory."""
    return Path.home() / ".claude" / "projects"


def get_session_name(jsonl_path: Path) -> Tuple[Optional[str], Optional[str]]:
    """Extract the display name of a session from its jsonl file.

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

                if not slug and obj.get("slug"):
                    slug = obj["slug"]

                if obj.get("type") == "custom-title":
                    custom_title = obj.get("customTitle", custom_title)

    except (OSError, IOError):
        pass

    return (custom_title, slug)


def get_session_display_name(jsonl_path: Path) -> str:
    """Return the best display name for a session.

    Priority: customTitle > slug > first 6 chars of UUID.
    """
    custom_title, slug = get_session_name(jsonl_path)
    if custom_title:
        return custom_title
    if slug:
        return slug
    return jsonl_path.stem[:6]


def build_session_index(project_filter: Optional[str] = None) -> List[Dict]:
    """Build an index of all sessions with their names.

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
    """Resolve a session identifier to a jsonl file path.

    Matching strategy (in order):
    1. UUID prefix match (>= 6 hex chars)
    2. Name match: customTitle or slug (exact then partial)
    3. Fallback: UUID prefix match for shorter strings (>= 4 chars)
    """
    projects_dir = get_claude_projects_dir()
    if not projects_dir.exists():
        return None

    clean_id = identifier.replace("-", "")
    is_uuid_like = len(clean_id) >= 6 and all(
        c in "0123456789abcdefABCDEF" for c in clean_id
    )

    if is_uuid_like:
        matches = []
        for jsonl_file in projects_dir.rglob("*.jsonl"):
            if "subagents" in jsonl_file.parts:
                continue
            if jsonl_file.stem.startswith(identifier) or jsonl_file.stem.startswith(clean_id):
                matches.append(jsonl_file)
        if matches:
            return max(matches, key=lambda p: p.stat().st_mtime)

    identifier_lower = identifier.lower()
    exact_matches = []
    partial_matches = []

    for jsonl_file in projects_dir.rglob("*.jsonl"):
        if "subagents" in jsonl_file.parts:
            continue

        custom_title, slug = get_session_name(jsonl_file)

        if custom_title and custom_title.lower() == identifier_lower:
            exact_matches.append(jsonl_file)
            continue
        if slug and slug.lower() == identifier_lower:
            exact_matches.append(jsonl_file)
            continue

        if custom_title and identifier_lower in custom_title.lower():
            partial_matches.append(jsonl_file)
        elif slug and identifier_lower in slug.lower():
            partial_matches.append(jsonl_file)

    if exact_matches:
        return max(exact_matches, key=lambda p: p.stat().st_mtime)
    if partial_matches:
        return max(partial_matches, key=lambda p: p.stat().st_mtime)

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
    """Generate a brief, useful output filename for a session.

    Format: claude-<display_name>.<extension>
    """
    display_name = get_session_display_name(session_path)

    safe_name = "".join(c if c.isalnum() or c in "-_ " else "-" for c in display_name)
    safe_name = safe_name.strip("-_ ")
    while "--" in safe_name:
        safe_name = safe_name.replace("--", "-")
    safe_name = safe_name.replace(" ", "-")

    if not safe_name:
        safe_name = session_path.stem[:6]

    return f"claude-{safe_name}.{extension}"
