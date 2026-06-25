"""
Search mode implementations: smart, exact, and regex.

Each function searches a single JSONL file and returns a list of SearchResult.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Set

from .helpers import (
    SearchResult,
    calculate_relevance,
    extract_content,
    extract_context,
)


def search_smart(
    jsonl_file: Path,
    query: str,
    speaker_filter: Optional[str],
    case_sensitive: bool,
    stop_words: Set[str],
) -> List[SearchResult]:
    """Smart search combining exact matching, token overlap, and proximity."""
    results = []
    conversation_id = jsonl_file.stem

    if not case_sensitive:
        query_lower = query.lower()
        query_tokens = set(query_lower.split()) - stop_words
    else:
        query_tokens = set(query.split()) - stop_words

    try:
        with open(jsonl_file, "r", encoding="utf-8") as f:
            line_num = 0
            for line in f:
                line_num += 1
                try:
                    entry = json.loads(line.strip())

                    if entry.get("type") in ["user", "assistant"]:
                        speaker = (
                            "human" if entry["type"] == "user" else "assistant"
                        )

                        if speaker_filter and speaker != speaker_filter:
                            continue

                        content = extract_content(entry)
                        if not content:
                            continue

                        relevance = calculate_relevance(
                            content, query, query_tokens, case_sensitive, stop_words
                        )

                        if relevance > 0.1:
                            context = extract_context(
                                content, query, case_sensitive
                            )

                            timestamp = _parse_timestamp(entry)

                            result = SearchResult(
                                file_path=jsonl_file,
                                conversation_id=conversation_id,
                                matched_content=content[:200],
                                context=context,
                                speaker=speaker,
                                timestamp=timestamp,
                                relevance_score=relevance,
                                line_number=line_num,
                            )
                            results.append(result)

                except json.JSONDecodeError:
                    continue

    except Exception as e:
        print(f"Error searching {jsonl_file}: {e}")

    return results


def search_exact(
    jsonl_file: Path,
    query: str,
    speaker_filter: Optional[str],
    case_sensitive: bool,
) -> List[SearchResult]:
    """Exact string matching search."""
    results = []
    conversation_id = jsonl_file.stem

    search_query = query if case_sensitive else query.lower()

    try:
        with open(jsonl_file, "r", encoding="utf-8") as f:
            line_num = 0
            for line in f:
                line_num += 1
                try:
                    entry = json.loads(line.strip())

                    if entry.get("type") in ["user", "assistant"]:
                        speaker = (
                            "human" if entry["type"] == "user" else "assistant"
                        )

                        if speaker_filter and speaker != speaker_filter:
                            continue

                        content = extract_content(entry)
                        if not content:
                            continue

                        search_content = (
                            content if case_sensitive else content.lower()
                        )

                        if search_query in search_content:
                            match_count = search_content.count(search_query)
                            relevance = min(1.0, match_count * 0.2)

                            context = extract_context(
                                content, query, case_sensitive
                            )

                            timestamp = _parse_timestamp(entry)

                            result = SearchResult(
                                file_path=jsonl_file,
                                conversation_id=conversation_id,
                                matched_content=content[:200],
                                context=context,
                                speaker=speaker,
                                timestamp=timestamp,
                                relevance_score=relevance,
                                line_number=line_num,
                            )
                            results.append(result)

                except json.JSONDecodeError:
                    continue

    except Exception as e:
        print(f"Error searching {jsonl_file}: {e}")

    return results


def search_regex(
    jsonl_file: Path,
    pattern: str,
    speaker_filter: Optional[str],
    case_sensitive: bool,
) -> List[SearchResult]:
    """Regex pattern matching search."""
    results = []
    conversation_id = jsonl_file.stem

    try:
        flags = 0 if case_sensitive else re.IGNORECASE
        regex = re.compile(pattern, flags)
    except re.error as e:
        print(f"Invalid regex pattern: {e}")
        return []

    try:
        with open(jsonl_file, "r", encoding="utf-8") as f:
            line_num = 0
            for line in f:
                line_num += 1
                try:
                    entry = json.loads(line.strip())

                    if entry.get("type") in ["user", "assistant"]:
                        speaker = (
                            "human" if entry["type"] == "user" else "assistant"
                        )

                        if speaker_filter and speaker != speaker_filter:
                            continue

                        content = extract_content(entry)
                        if not content:
                            continue

                        matches = list(regex.finditer(content))

                        if matches:
                            relevance = min(1.0, len(matches) * 0.2)

                            first_match = matches[0]
                            start = max(0, first_match.start() - 100)
                            end = min(len(content), first_match.end() + 100)
                            context = "..." + content[start:end] + "..."

                            timestamp = _parse_timestamp(entry)

                            result = SearchResult(
                                file_path=jsonl_file,
                                conversation_id=conversation_id,
                                matched_content=first_match.group(),
                                context=context,
                                speaker=speaker,
                                timestamp=timestamp,
                                relevance_score=relevance,
                                line_number=line_num,
                            )
                            results.append(result)

                except json.JSONDecodeError:
                    continue

    except Exception as e:
        print(f"Error searching {jsonl_file}: {e}")

    return results


def _parse_timestamp(entry: dict) -> Optional[datetime]:
    """Parse ISO timestamp from a JSONL entry, returning None on failure."""
    timestamp_str = entry.get("timestamp")
    if timestamp_str:
        try:
            return datetime.fromisoformat(timestamp_str.replace("Z", "+00:00"))
        except ValueError:
            pass
    return None
