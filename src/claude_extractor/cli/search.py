#!/usr/bin/env python3
"""
Two-stage interactive search for Claude conversations.

Flow:
  1. Accept search keyword (CLI arg or interactive prompt).
  2. Search all main sessions (exclude subagents).
  3. Group matching files by project directory -- user picks a project.
  4. Show per-session matches with line-based context around the keyword.

Entry point: ``claude-search [keyword]``
"""

import re
import sys
from collections import OrderedDict
from pathlib import Path
from typing import Dict, List

from ..search.searcher import ConversationSearcher
from ..cli.listing import decode_project_dir_name
from ..session.resolver import get_session_display_name


_XML_TAG_RE = re.compile(r"<[^>]+>.*?</[^>]+>", re.DOTALL)


def _strip_xml_tags(text: str) -> str:
    """Remove XML tag pairs and collapse resulting blank lines."""
    cleaned = _XML_TAG_RE.sub("", text)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _get_search_keyword() -> str:
    """Get search keyword from CLI args or interactive prompt."""
    if len(sys.argv) > 1:
        return " ".join(sys.argv[1:])

    try:
        keyword = input("Search keyword: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\nCancelled")
        return ""
    return keyword


def _group_by_project(
    grouped: Dict[Path, List[Dict]],
) -> Dict[Path, Dict[Path, List[Dict]]]:
    """Re-group ``{jsonl_path: [matches]}`` into
    ``{project_dir: {jsonl_path: [matches]}}``.
    """
    by_project: Dict[Path, Dict[Path, List[Dict]]] = OrderedDict()

    for jsonl_path, matches in grouped.items():
        parent = jsonl_path.parent
        project_dir = parent.parent if parent.name == jsonl_path.stem else parent

        if project_dir not in by_project:
            by_project[project_dir] = OrderedDict()
        by_project[project_dir][jsonl_path] = matches

    return by_project


def _display_project_list(by_project: Dict[Path, Dict[Path, List[Dict]]]) -> List[Path]:
    """Print project directory list and return ordered list of project paths."""
    project_list = list(by_project.keys())
    total_matches = sum(
        sum(len(m) for m in sessions.values())
        for sessions in by_project.values()
    )
    print(f"\nFound {total_matches} match(es) across {len(project_list)} project(s):\n")
    print("Select project directory (type number):")
    for i, proj_dir in enumerate(project_list, 1):
        session_count = len(by_project[proj_dir])
        match_count = sum(len(m) for m in by_project[proj_dir].values())
        display = decode_project_dir_name(proj_dir.name)
        print(f"  {i}. {display}  ({session_count} session(s), {match_count} match(es))")
    return project_list


def _display_session_matches(
    sessions: Dict[Path, List[Dict]], keyword: str
) -> None:
    """Print per-session match details with context."""
    print()
    print("=" * 60)

    for i, (jsonl_path, matches) in enumerate(sessions.items(), 1):
        session_id = jsonl_path.stem
        try:
            display_name = get_session_display_name(jsonl_path)
        except Exception:
            display_name = session_id[:8]

        print(f"\n{i}. Session {session_id[:8]}... ({display_name})  "
              f"({len(matches)} match(es))")

        for j, m in enumerate(matches, 1):
            speaker = m["speaker"]
            context = m["context"]
            context = _strip_xml_tags(context)
            context_lines = context.split("\n")
            truncated = []
            for ln in context_lines:
                if len(ln) > 120:
                    ln = ln[:117] + "..."
                truncated.append(ln)
            preview = "\n      ".join(truncated)
            print(f"   match {j}: {speaker}: {preview}")

    print("\n" + "=" * 60)


def main():
    """Entry point for ``claude-search``."""
    keyword = _get_search_keyword()
    if not keyword:
        return

    print(f"\nSearching for: '{keyword}' ...")

    searcher = ConversationSearcher()
    grouped = searcher.search_grouped(keyword, case_sensitive=False, context_lines=3)

    if not grouped:
        print(f"\nNo matches found for '{keyword}'")
        return

    # Stage 1: group by project, user picks one
    by_project = _group_by_project(grouped)

    if len(by_project) == 1:
        selected_project = next(iter(by_project.keys()))
    else:
        project_list = _display_project_list(by_project)

        try:
            choice = input(
                f"\nSelect project (1-{len(project_list)}): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            print("\nCancelled")
            return

        try:
            idx = int(choice) - 1
            if idx < 0 or idx >= len(project_list):
                print("Invalid selection.")
                return
        except ValueError:
            print("Invalid input.")
            return

        selected_project = project_list[idx]

    # Stage 2: show per-session matches
    sessions = by_project[selected_project]
    display = decode_project_dir_name(selected_project.name)
    print(f"\nResults in: {display}")
    _display_session_matches(sessions, keyword)


if __name__ == "__main__":
    main()
