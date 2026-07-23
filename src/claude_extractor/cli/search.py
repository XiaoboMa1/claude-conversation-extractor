#!/usr/bin/env python3
"""
Three-stage interactive search for Claude conversations.

Usage:
  extract --find [keyword]
  claude-search [keyword]

Stage 1: search all sessions -> group by project -> user picks project
Stage 2: sessions with match context (pager) -> user picks sessions
Stage 3: load messages from selected sessions -> preview -> view -> extract
"""

import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..search.searcher import ConversationSearcher
from ..cli.listing import decode_project_dir_name
from ..session.resolver import get_session_display_name


_XML_TAG_RE = re.compile(r"<[^>]+>.*?</[^>]+>", re.DOTALL)


def _strip_xml_tags(text: str) -> str:
    """Remove XML tag pairs and collapse resulting blank lines."""
    cleaned = _XML_TAG_RE.sub("", text)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def _truncate_line(text: str, max_len: int = 120) -> str:
    """Truncate a single line to *max_len* characters."""
    if len(text) > max_len:
        return text[: max_len - 3] + "..."
    return text


# ── Grouping ──────────────────────────────────────────────────────


def _group_by_project(
    grouped: Dict[Path, List[Dict]],
) -> Dict[Path, Dict[Path, List[Dict]]]:
    """Re-group {jsonl_path: [matches]} into {project_dir: {jsonl_path: [matches]}}."""
    by_project: Dict[Path, Dict[Path, List[Dict]]] = OrderedDict()
    for jsonl_path, matches in grouped.items():
        parent = jsonl_path.parent
        project_dir = parent.parent if parent.name == jsonl_path.stem else parent
        if project_dir not in by_project:
            by_project[project_dir] = OrderedDict()
        by_project[project_dir][jsonl_path] = matches
    return by_project


# ── Formatting ────────────────────────────────────────────────────


def _format_session_matches(
    sessions: Dict[Path, List[Dict]],
) -> Tuple[str, List[Path]]:
    """Format session match list for pager display.

    Uses colored session headers (bold cyan ``━━``), yellow match
    labels, colored speaker roles, and ``│`` gutter to visually
    separate message content from structural elements.

    Returns (formatted_text, ordered_session_paths).
    """
    from .browser import (
        _C_BOLD, _C_CYAN, _C_DIM, _C_GREEN, _C_MAGENTA,
        _C_RESET, _C_YELLOW, _display_width,
    )

    out: List[str] = []
    session_paths: List[Path] = []

    for i, (jsonl_path, matches) in enumerate(sessions.items(), 1):
        session_paths.append(jsonl_path)
        session_id = jsonl_path.stem
        try:
            display_name = get_session_display_name(jsonl_path)
        except Exception:
            display_name = session_id[:8]

        total_matches = sum(m["match_count"] for m in matches)

        # ── Session header (bold cyan with ━━ rule) ──
        header_text = (
            f" {i}. {session_id[:8]}... ({display_name})"
            f" \u2014 {total_matches} match(es) "
        )
        rule_pad = max(2, 60 - _display_width(header_text) - 2)
        pad = "\u2501" * rule_pad
        out.append(
            f"{_C_BOLD}{_C_CYAN}\u2501\u2501{header_text}{pad}{_C_RESET}"
        )

        match_num = 0
        for m in matches:
            speaker = m["speaker"]
            sp_color = _C_GREEN if speaker == "user" else _C_MAGENTA
            sp_label = f"{sp_color}{speaker}{_C_RESET}"

            # First match: label line + gutter content lines
            match_num += 1
            context = _strip_xml_tags(m["first_context"])
            ctx_lines = context.split("\n")
            truncated = [_truncate_line(ln) for ln in ctx_lines]

            out.append(
                f"  {_C_YELLOW}match {match_num}{_C_RESET}  {sp_label}:"
            )
            for ln in truncated:
                out.append(f"  {_C_DIM}\u2502{_C_RESET}  {ln}")

            # Subsequent matches: label + single gutter line
            for other_line in m.get("other_lines", []):
                match_num += 1
                clean = _strip_xml_tags(other_line).strip()
                out.append(
                    f"  {_C_YELLOW}match {match_num}{_C_RESET}  {sp_label}:"
                )
                out.append(
                    f"  {_C_DIM}\u2502{_C_RESET}  {_truncate_line(clean)}"
                )

        out.append("")  # blank line between sessions

    return "\n".join(out), session_paths


# ── Stages ────────────────────────────────────────────────────────


def _stage_find_projects(
    by_project: Dict[Path, Dict[Path, List[Dict]]],
) -> Optional[Path]:
    """Stage 1: show projects with matches, return selected project path."""
    from .browser import _read_line

    project_list = list(by_project.keys())
    total_matches = sum(
        sum(m["match_count"] for ms in sessions.values() for m in ms)
        for sessions in by_project.values()
    )

    print(
        f"\nFound {total_matches} match(es) across "
        f"{len(project_list)} project(s):\n"
    )
    for i, proj_dir in enumerate(project_list, 1):
        session_count = len(by_project[proj_dir])
        match_count = sum(
            m["match_count"]
            for ms in by_project[proj_dir].values()
            for m in ms
        )
        display = decode_project_dir_name(proj_dir.name)
        print(
            f"  {i}. {display}  "
            f"({session_count} session(s), {match_count} match(es))"
        )

    while True:
        choice = _read_line(
            f"\nSelect project (1-{len(project_list)}) [Esc=exit]: "
        )
        if choice is None:
            return None
        if not choice.strip():
            continue
        try:
            idx = int(choice.strip()) - 1
            if 0 <= idx < len(project_list):
                return project_list[idx]
            print("Invalid selection.")
        except ValueError:
            print("Invalid input.")


def _stage_find_sessions(
    sessions: Dict[Path, List[Dict]],
) -> Optional[List[Path]]:
    """Stage 2: show sessions with match context in pager, return selected paths."""
    from .browser import _pager, _parse_ids, _read_line

    text, session_paths = _format_session_matches(sessions)
    _pager(text)

    while True:
        action = _read_line(
            f"\nSelect session (1-{len(session_paths)}, space separated) "
            f"to view [Esc=back]: "
        )
        if action is None:
            return None
        if not action.strip():
            continue

        ids = _parse_ids(action, len(session_paths))
        if ids is not None:
            unique_ids = sorted(set(ids))
            return [session_paths[i - 1] for i in unique_ids]


def _stage_find_messages(selected_paths: List[Path]) -> None:
    """Stage 3: load messages from selected sessions, preview and extract.

    Delegates to ``browse_session`` which handles message loading, merging,
    preview, full view, and extraction.
    """
    from .browser import browse_session

    browse_session(selected_paths)


# ── Entry points ──────────────────────────────────────────────────


def find_interactive(
    keyword: Optional[str] = None, *, use_regex: bool = False
) -> None:
    """Three-stage interactive search.

    Called by ``extract --find [keyword]`` and ``claude-search [keyword]``.
    When *use_regex* is True, *keyword* is treated as a regex pattern.
    """
    import re as _re
    from .browser import _read_line

    if not keyword:
        prompt = "Search regex: " if use_regex else "Search keyword: "
        kw = _read_line(prompt)
        if kw is None or not kw.strip():
            return
        keyword = kw.strip()

    # Validate regex early so the user gets a clear error
    if use_regex:
        try:
            _re.compile(keyword)
        except _re.error as e:
            print(f"\nInvalid regex pattern: {e}")
            return

    mode_label = "regex" if use_regex else "keyword"
    print(f"\nSearching for {mode_label}: '{keyword}' ...")

    searcher = ConversationSearcher()
    grouped = searcher.search_for_display(
        keyword, case_sensitive=False, context_lines=3, use_regex=use_regex,
    )

    if not grouped:
        print(f"\nNo matches found for '{keyword}'")
        return

    by_project = _group_by_project(grouped)

    while True:
        # Stage 1: project selection (auto-select if only one)
        if len(by_project) == 1:
            selected_project = next(iter(by_project.keys()))
        else:
            selected_project = _stage_find_projects(by_project)
            if selected_project is None:
                return

        # Stage 2 loop: Esc in stage 3 → back here (session match list)
        while True:
            sessions = by_project[selected_project]
            selected_paths = _stage_find_sessions(sessions)
            if selected_paths is None:
                if len(by_project) == 1:
                    return  # single project, nowhere to go back
                break  # Esc in stage 2 → back to stage 1

            # Stage 3: message view + extract
            _stage_find_messages(selected_paths)
            # _stage_find_messages returned (Esc or completion) → back to stage 2


def main():
    """Entry point for ``claude-search`` (backward-compatible)."""
    keyword = ""
    if len(sys.argv) > 1:
        keyword = " ".join(sys.argv[1:])
    find_interactive(keyword or None)


if __name__ == "__main__":
    main()
