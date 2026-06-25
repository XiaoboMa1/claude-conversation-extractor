"""
Search data structures and shared utilities.

SearchResult dataclass, content extraction from JSONL entries,
relevance scoring, and context extraction.
"""

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Set


@dataclass
class SearchResult:
    """Represents a search result with context."""

    file_path: Path
    conversation_id: str
    matched_content: str
    context: str  # Surrounding text for context
    speaker: str  # 'human' or 'assistant'
    timestamp: Optional[datetime] = None
    relevance_score: float = 0.0
    line_number: int = 0

    def __str__(self) -> str:
        return (
            f"\n{'=' * 60}\n"
            f"File: {self.file_path.name}\n"
            f"Speaker: {self.speaker.title()}\n"
            f"Relevance: {self.relevance_score:.0%}\n"
            f"{'=' * 60}\n"
            f"{self.context}\n"
        )


def extract_content(entry: Dict) -> str:
    """Extract text content from a JSONL entry.

    Handles both the test format (``type: user/assistant, content: string``)
    and actual Claude log format (``message.content`` as string or list of
    typed blocks).
    """
    # Test format (type: user/assistant, content: string)
    if entry.get("type") in ["user", "assistant"] and "content" in entry:
        content = entry["content"]
        if isinstance(content, str):
            return content

    # Actual Claude log format (message.content)
    if "message" in entry:
        msg = entry["message"]
        if isinstance(msg, dict):
            content = msg.get("content", "")
            if isinstance(content, list):
                text_parts = []
                for item in content:
                    if isinstance(item, dict) and item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif isinstance(item, str):
                        text_parts.append(item)
                return " ".join(text_parts)
            elif isinstance(content, str):
                return content

    return ""


def calculate_relevance(
    content: str,
    query: str,
    query_tokens: Set[str],
    case_sensitive: bool,
    stop_words: Set[str],
) -> float:
    """Calculate relevance score for content against query.

    Factors: exact match bonus, token overlap, proximity of terms, match density.
    """
    relevance = 0.0

    if not case_sensitive:
        content_lower = content.lower()
        query_lower = query.lower()
    else:
        content_lower = content
        query_lower = query

    # Exact match bonus
    if query_lower in content_lower:
        relevance += 0.5
        count = content_lower.count(query_lower)
        relevance += min(0.3, count * 0.1)

    # Token overlap
    content_tokens = set(content_lower.split()) - stop_words
    if query_tokens and content_tokens:
        overlap = len(query_tokens & content_tokens)
        relevance += min(0.4, overlap / len(query_tokens) * 0.4)

    # Proximity bonus - are query terms near each other?
    if len(query_tokens) > 1:
        words = content_lower.split()
        for i in range(len(words) - len(query_tokens)):
            window = set(words[i : i + len(query_tokens) * 2])
            if query_tokens.issubset(window):
                relevance += 0.1
                break

    return min(1.0, relevance)


def extract_context(
    content: str, query: str, case_sensitive: bool, context_size: int = 150
) -> str:
    """Extract context around the match for display."""
    if not case_sensitive:
        pos = content.lower().find(query.lower())
    else:
        pos = content.find(query)

    if pos == -1:
        return content[: context_size * 2] + (
            "..." if len(content) > context_size * 2 else ""
        )

    start = max(0, pos - context_size)
    end = min(len(content), pos + len(query) + context_size)

    context = content[start:end]

    if start > 0:
        context = "..." + context
    if end < len(content):
        context = context + "..."

    # Highlight the match
    if not case_sensitive:
        pattern = re.compile(re.escape(query), re.IGNORECASE)
        context = pattern.sub(f"**{query.upper()}**", context)
    else:
        context = context.replace(query, f"**{query}**")

    return context


def clean_for_search(text: str) -> str:
    """Strip XML noise tags so keywords inside them don't produce matches."""
    return re.sub(r"<[^>]+>.*?</[^>]+>", "", text, flags=re.DOTALL)
