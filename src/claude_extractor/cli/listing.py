"""
Two-stage interactive project / session listing.

Stage 1 -- list project directories, user picks one.
Stage 2 -- list sessions inside that project with subagent breakdown,
          first user message, last assistant message.
Stage 3 -- user picks a session to extract or view.
"""

import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ..session.message import (
    count_subagents_by_type,
    get_first_meaningful_message,
    get_last_meaningful_message,
)
from ..session.resolver import get_claude_projects_dir, get_session_display_name


# ── Project directory name decoding ──────────────────────────────────


def decode_project_dir_name(encoded: str) -> str:
    """Best-effort decode of Claude Code's encoded project directory name.

    Claude Code replaces every non-alphanumeric character in the absolute
    project path with ``-``.  The mapping is lossy -- we apply a heuristic
    for the common Windows drive-letter case.
    """
    if sys.platform == "win32" and len(encoded) >= 3 and encoded[0].isalpha():
        if encoded[1:3] == "--":
            rest = encoded[3:].replace("-", " ").strip()
            return f"{encoded[0]}:\\{rest}"
    return encoded.replace("-", " ").strip() or encoded


# ── Data collection ──────────────────────────────────────────────────


def get_project_dirs() -> List[Dict]:
    """Return every project directory that contains at least one session.

    Sorted by most-recent modification first.
    """
    projects_dir = get_claude_projects_dir()
    if not projects_dir.exists():
        return []

    project_map: Dict[Path, Dict] = {}

    for jsonl_file in projects_dir.rglob("*.jsonl"):
        if "subagents" in jsonl_file.parts:
            continue

        parent = jsonl_file.parent
        project_dir = parent.parent if parent.name == jsonl_file.stem else parent

        file_mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime)

        if project_dir not in project_map:
            project_map[project_dir] = {
                "path": project_dir,
                "encoded_name": project_dir.name,
                "display_name": decode_project_dir_name(project_dir.name),
                "modified": file_mtime,
                "session_count": 0,
            }

        project_map[project_dir]["session_count"] += 1
        if file_mtime > project_map[project_dir]["modified"]:
            project_map[project_dir]["modified"] = file_mtime

    return sorted(project_map.values(), key=lambda x: x["modified"], reverse=True)


def get_sessions_for_project(project_dir: Path) -> List[Dict]:
    """Return sessions inside *project_dir* with rich metadata.

    Sorted by most-recent modification first.
    """
    sessions: List[Dict] = []

    for jsonl_file in project_dir.glob("*.jsonl"):
        if "subagents" in str(jsonl_file):
            continue

        sessions.append({
            "path": jsonl_file,
            "session_id": jsonl_file.stem,
            "display_name": get_session_display_name(jsonl_file),
            "modified": datetime.fromtimestamp(jsonl_file.stat().st_mtime),
            "subagents": count_subagents_by_type(jsonl_file),
            "first_user_msg": get_first_meaningful_message(jsonl_file, "user", 3),
            "last_assistant_msg": get_last_meaningful_message(jsonl_file, "assistant", 3),
        })

    sessions.sort(key=lambda x: x["modified"], reverse=True)
    return sessions


# ── Formatting helpers ───────────────────────────────────────────────


def _format_subagents(subagents: Dict[str, int]) -> str:
    if not subagents:
        return ""
    return ", ".join(f"{count} {atype}" for atype, count in sorted(subagents.items()))


def _print_indented(label: str, text: str, indent: str = "      ") -> None:
    """Print a labelled multi-line block with consistent indentation."""
    lines = text.split("\n")
    print(f"   {label}: {lines[0]}")
    for ln in lines[1:]:
        print(f"{indent}{ln}")


# ── Interactive flow ─────────────────────────────────────────────────


def _stage_projects(projects: List[Dict]) -> Optional[Dict]:
    """Stage 1: display project list, return selected project or None."""
    print(f"\nFound {len(projects)} project(s):\n")
    print("=" * 60)

    for i, proj in enumerate(projects, 1):
        print(f"\n{i}. {proj['display_name']}")
        print(f"   Modified: {proj['modified'].strftime('%Y-%m-%d %H:%M')}")
        print(f"   Sessions: {proj['session_count']}")

    print("\n" + "=" * 60)

    from .browser import _read_line

    while True:
        choice = _read_line(
            f"\nSelect project (1-{len(projects)}) [Esc=exit]: "
        )
        if choice is None:
            return None
        if not choice.strip():
            continue
        try:
            idx = int(choice.strip()) - 1
            if 0 <= idx < len(projects):
                return projects[idx]
            print("Invalid selection.")
        except ValueError:
            print("Invalid input.")


def _stage_sessions(project: Dict) -> Optional[Path]:
    """Stage 2: display sessions in a project in pager, return selected path or None."""
    from .browser import _pager, _read_line

    print(f"\nLoading sessions for: {project['display_name']} ...")
    sessions = get_sessions_for_project(project["path"])

    if not sessions:
        print("No sessions found in this project.")
        return None

    # Build session list text for pager
    parts = [f"Found {len(sessions)} session(s):", "", "=" * 60]

    for i, s in enumerate(sessions, 1):
        parts.append("")
        parts.append(f"{i}. Session: {s['session_id'][:8]}... ({s['display_name']})")
        parts.append(f"   Modified: {s['modified'].strftime('%Y-%m-%d %H:%M')}")

        sub_str = _format_subagents(s["subagents"])
        if sub_str:
            parts.append(f"   Subagents: {sub_str}")

        if s["first_user_msg"]:
            msg_lines = s["first_user_msg"].split("\n")
            parts.append(f"   First user message: {msg_lines[0]}")
            for ln in msg_lines[1:]:
                parts.append(f"      {ln}")

        if s["last_assistant_msg"]:
            msg_lines = s["last_assistant_msg"].split("\n")
            parts.append(f"   Last claude message: {msg_lines[0]}")
            for ln in msg_lines[1:]:
                parts.append(f"      {ln}")

    parts.append("")
    parts.append("=" * 60)

    _pager("\n".join(parts))

    while True:
        action = _read_line(
            f"\nSelect session (1-{len(sessions)}) to view [Esc=back]: "
        )
        if action is None:
            return None
        if not action.strip():
            continue
        try:
            idx = int(action.strip()) - 1
            if 0 <= idx < len(sessions):
                return sessions[idx]["path"]
            print("Invalid selection.")
        except ValueError:
            print("Invalid input.")


def interactive_list() -> None:
    """Multi-stage interactive listing and browsing.

    Stage 1: project directories  (loops back on Enter from stage 2)
    Stage 2: sessions in a project
    Stage 3-4: message browsing (delegated to browser.browse_session)
    """
    from .browser import browse_session

    projects = get_project_dirs()

    if not projects:
        print("No Claude sessions found in ~/.claude/projects/")
        print("Make sure you've used Claude Code and have conversations saved.")
        return

    while True:
        selected_project = _stage_projects(projects)
        if selected_project is None:
            return

        session_path = _stage_sessions(selected_project)
        if session_path is None:
            # Esc in stage 2 -> go back to project list
            continue

        browse_session(session_path)
        return
