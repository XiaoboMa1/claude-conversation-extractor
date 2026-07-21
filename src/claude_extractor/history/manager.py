#!/usr/bin/env python3
"""
Session deletion and cleanup for Claude Code conversations.

Provides two operations:
- delete: remove a specific session by name or ID prefix
- clean:  interactive 3-stage browser for bulk session deletion

Cross-platform (Linux + Windows) via pathlib and shutil.
"""

import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..cli.listing import get_project_dirs, get_sessions_for_project
from ..session.message import get_last_message_tail
from ..session.resolver import (
    get_session_display_name,
    resolve_session,
)


# ── Delete operations ──────────────────────────────────────────────────


def delete_session_files(jsonl_path: Path) -> Tuple[bool, List[str]]:
    """Delete a session's .jsonl file and its associated folder."""
    deleted: List[str] = []
    session_id = jsonl_path.stem
    session_dir = jsonl_path.parent / session_id

    try:
        if session_dir.exists() and session_dir.is_dir():
            shutil.rmtree(session_dir)
            deleted.append(str(session_dir))

        if jsonl_path.exists():
            jsonl_path.unlink()
            deleted.append(str(jsonl_path))

        return True, deleted
    except Exception as e:
        print(f"  Error deleting session {session_id[:8]}: {e}")
        return False, deleted


def delete_by_identifier(identifier: str) -> bool:
    """Find a session by name or ID prefix, show details, and delete after confirmation."""
    session_path = resolve_session(identifier)
    if not session_path:
        print(f"No session found matching '{identifier}'")
        return False

    display_name = get_session_display_name(session_path)
    session_id = session_path.stem
    session_dir = session_path.parent / session_id

    print(f"\nSession: {display_name} ({session_id[:8]}...)")
    print(f"  File: {session_path}")
    if session_dir.exists():
        size_info = ""
        try:
            total_size = sum(f.stat().st_size for f in session_dir.rglob("*") if f.is_file())
            total_size += session_path.stat().st_size
            size_info = f" ({total_size / 1024:.1f} KB)"
        except OSError:
            pass
        print(f"  Dir:  {session_dir}{size_info}")

    preview = get_last_message_tail(session_path, max_lines=5)
    if preview:
        print("\n  Last message (tail):")
        for ln in preview.split("\n"):
            print(f"    {ln}")

    try:
        confirm = input("\nDelete this session? (y/N): ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled")
        return False

    if confirm != "y":
        print("Cancelled")
        return False

    success, deleted = delete_session_files(session_path)
    if success:
        for path in deleted:
            print(f"  Deleted: {path}")
        print("Done.")
    return success


# ── Interactive clean (3-stage browser) ───────────────────────────────


def _format_session_list(sessions: List[Dict]) -> str:
    """Format numbered session list with last-message preview for pager."""
    lines = [f"{len(sessions)} session(s):", "", "=" * 60]
    for i, s in enumerate(sessions, 1):
        modified = s["modified"].strftime("%Y-%m-%d %H:%M")
        lines.append("")
        lines.append(f"  {i}. {s['session_id'][:8]}...  ({s['display_name']})  [{modified}]")

        preview = get_last_message_tail(s["path"], max_lines=5)
        if preview:
            for ln in preview.split("\n"):
                lines.append(f"       {ln}")
        else:
            lines.append("       (no message content)")
    lines.append("")
    lines.append("=" * 60)
    return "\n".join(lines)


def _parse_session_numbers(text: str, count: int) -> Optional[List[int]]:
    """Parse space-separated session numbers into 0-indexed list."""
    parts = text.strip().split()
    if not parts:
        return None
    indices = []
    for p in parts:
        try:
            n = int(p)
        except ValueError:
            print(f"  Invalid input: {p}")
            return None
        if n < 1 or n > count:
            print(f"  Number {n} out of range (1-{count})")
            return None
        indices.append(n - 1)
    return sorted(set(indices))


def clean_interactive() -> int:
    """Interactive 3-stage session browser for bulk deletion.

    Stage 1: list projects, user picks one.
    Stage 2: list sessions with last-message preview (in pager).
    Stage 3: user enters session numbers to delete (space-separated).
             Loops until user presses Esc to exit.

    Returns total number of deleted sessions.
    """
    from ..cli.browser import _pager, _read_line
    from ..cli.listing import _stage_projects

    # ── Stage 1: pick project ──
    projects = get_project_dirs()
    if not projects:
        print("No Claude sessions found in ~/.claude/projects/")
        return 0

    selected_project = _stage_projects(projects)
    if selected_project is None:
        return 0

    # ── Stage 2 & 3: list sessions, delete loop ──
    total_deleted = 0

    while True:
        print(f"\nLoading sessions for: {selected_project['display_name']} ...")
        sessions = get_sessions_for_project(selected_project["path"])

        if not sessions:
            print("No sessions remaining in this project.")
            break

        _pager(_format_session_list(sessions))

        action = _read_line(
            f"\nSelect session (1-{len(sessions)}, space separated) to delete [Esc=exit]: "
        )
        if action is None:
            break
        if not action.strip():
            continue

        indices = _parse_session_numbers(action, len(sessions))
        if indices is None:
            continue

        for idx in indices:
            target = sessions[idx]
            success, deleted_paths = delete_session_files(target["path"])
            if success:
                total_deleted += 1
                print(f"  Deleted: {target['session_id'][:8]}... ({target['display_name']})")
        # Loop back to show updated list

    if total_deleted:
        print(f"\nDeleted {total_deleted} session(s) total.")
    return total_deleted
