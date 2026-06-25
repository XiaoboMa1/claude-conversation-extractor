"""
ConversationSearcher - main search engine for Claude conversations.

Orchestrates multi-mode search (smart, exact, regex, semantic) and
line-context grouped search.  Search mode implementations live in
modes.py and semantic.py.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .helpers import SearchResult, clean_for_search, extract_content
from .modes import search_exact, search_regex, search_smart
from .semantic import get_conversation_topics, load_nlp, search_semantic


class ConversationSearcher:
    """Main search engine for Claude conversations.

    Provides multiple search modes and intelligent ranking.
    """

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or Path.home() / ".claude" / ".search_cache"
        self.cache_dir.mkdir(parents=True, exist_ok=True)

        self.nlp = load_nlp()

        # Common words to ignore in relevance scoring
        self.stop_words = {
            "the", "a", "an", "and", "or", "but", "in", "on", "at", "to",
            "for", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would",
            "could", "should", "may", "might", "i", "you", "we", "they",
            "it", "this", "that", "these", "those",
        }

    def search(
        self,
        query: str,
        search_dir: Optional[Path] = None,
        mode: str = "smart",
        date_from: Optional[datetime] = None,
        date_to: Optional[datetime] = None,
        speaker_filter: Optional[str] = None,
        max_results: int = 20,
        case_sensitive: bool = False,
    ) -> List[SearchResult]:
        """Search conversations with various filters.

        Args:
            query: Search query (text or regex pattern).
            search_dir: Directory to search in (default: ~/.claude/projects).
            mode: Search mode - "smart", "exact", "regex", "semantic".
            date_from: Filter results from this date.
            date_to: Filter results until this date.
            speaker_filter: Filter by speaker - "human", "assistant", or None.
            max_results: Maximum number of results to return.
            case_sensitive: Whether search should be case-sensitive.

        Returns:
            List of SearchResult objects sorted by relevance.
        """
        if search_dir is None:
            search_dir = Path.home() / ".claude" / "projects"
        if not search_dir.exists():
            raise ValueError(f"Search directory does not exist: {search_dir}")
        if not query or not query.strip():
            return []

        jsonl_files = [
            f for f in search_dir.rglob("*.jsonl")
            if "subagents" not in f.parts
        ]
        if not jsonl_files:
            return []

        if date_from or date_to:
            jsonl_files = self._filter_files_by_date(jsonl_files, date_from, date_to)

        all_results = []

        for jsonl_file in jsonl_files:
            if mode == "regex":
                results = search_regex(
                    jsonl_file, query, speaker_filter, case_sensitive
                )
            elif mode == "exact":
                results = search_exact(
                    jsonl_file, query, speaker_filter, case_sensitive
                )
            elif mode == "semantic" and self.nlp:
                results = search_semantic(
                    jsonl_file, query, speaker_filter, self.nlp
                )
            else:
                results = search_smart(
                    jsonl_file, query, speaker_filter, case_sensitive, self.stop_words
                )

            all_results.extend(results)

        all_results.sort(key=lambda x: x.relevance_score, reverse=True)
        return all_results[:max_results]

    def search_grouped(
        self,
        query: str,
        search_dir: Optional[Path] = None,
        case_sensitive: bool = False,
        context_lines: int = 3,
    ) -> Dict[Path, List[Dict]]:
        """Search all sessions and return results grouped by JSONL file.

        Each match entry: ``{"speaker": str, "context": str, "line_number": int}``

        *context* is ``context_lines`` lines above + match line + ``context_lines``
        lines below.  Overlapping context windows are merged.
        Excludes subagent files.
        """
        if search_dir is None:
            search_dir = Path.home() / ".claude" / "projects"
        if not search_dir.exists() or not query or not query.strip():
            return {}

        jsonl_files = [
            f for f in search_dir.rglob("*.jsonl")
            if "subagents" not in f.parts
        ]

        grouped: Dict[Path, List[Dict]] = {}

        for jsonl_file in jsonl_files:
            matches = self._search_file_line_context(
                jsonl_file, query, case_sensitive, context_lines
            )
            if matches:
                grouped[jsonl_file] = matches

        return grouped

    def _search_file_line_context(
        self,
        jsonl_file: Path,
        query: str,
        case_sensitive: bool,
        context_lines: int,
    ) -> List[Dict]:
        """Search a single JSONL file and return per-match entries with
        line-based context around each match position."""
        results: List[Dict] = []
        search_q = query if case_sensitive else query.lower()

        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                    except json.JSONDecodeError:
                        continue

                    if entry.get("type") not in ("user", "assistant"):
                        continue

                    speaker = "user" if entry["type"] == "user" else "assistant"
                    content = extract_content(entry)
                    if not content:
                        continue

                    # Strip XML noise before searching
                    content = clean_for_search(content)

                    compare = content if case_sensitive else content.lower()
                    if search_q not in compare:
                        continue

                    content_lines = content.split("\n")
                    compare_lines = compare.split("\n")

                    match_indices = [
                        idx for idx, cline in enumerate(compare_lines)
                        if search_q in cline
                    ]
                    if not match_indices:
                        continue

                    # Merge nearby indices whose context windows overlap
                    merged_ranges = []
                    cur_start = match_indices[0]
                    cur_end = match_indices[0]
                    for idx in match_indices[1:]:
                        if idx - cur_end <= 2 * context_lines:
                            cur_end = idx
                        else:
                            merged_ranges.append((cur_start, cur_end))
                            cur_start = cur_end = idx
                    merged_ranges.append((cur_start, cur_end))

                    for first_idx, last_idx in merged_ranges:
                        ctx_start = max(0, first_idx - context_lines)
                        ctx_end = min(len(content_lines), last_idx + context_lines + 1)
                        snippet = "\n".join(content_lines[ctx_start:ctx_end])
                        results.append({
                            "speaker": speaker,
                            "context": snippet,
                            "line_number": first_idx,
                        })

        except Exception:
            pass

        return results

    def search_by_date_range(
        self,
        date_from: datetime,
        date_to: datetime,
        search_dir: Optional[Path] = None,
    ) -> List[Path]:
        """Find all conversation files within a date range."""
        if search_dir is None:
            search_dir = Path.home() / ".claude" / "projects"
        jsonl_files = list(search_dir.rglob("*.jsonl"))
        return self._filter_files_by_date(jsonl_files, date_from, date_to)

    def get_conversation_topics(
        self, jsonl_file: Path, max_topics: int = 5
    ) -> List[str]:
        """Extract main topics from a conversation using NLP."""
        return get_conversation_topics(jsonl_file, self.nlp, max_topics)

    @staticmethod
    def _filter_files_by_date(
        files: List[Path],
        date_from: Optional[datetime],
        date_to: Optional[datetime],
    ) -> List[Path]:
        """Filter files by modification date."""
        filtered = []
        for file in files:
            file_mtime = datetime.fromtimestamp(file.stat().st_mtime)
            if date_from and file_mtime < date_from:
                continue
            if date_to and file_mtime > date_to:
                continue
            filtered.append(file)
        return filtered

    # Keep backward-compatible static method reference
    @staticmethod
    def _clean_for_search(text: str) -> str:
        return clean_for_search(text)

    # Keep backward-compatible instance method reference
    def _extract_content(self, entry: Dict) -> str:
        return extract_content(entry)
