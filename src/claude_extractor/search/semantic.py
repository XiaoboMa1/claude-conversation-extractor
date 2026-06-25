"""
Semantic search using spaCy NLP.

Optional dependency: install spacy for enhanced search capabilities.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from .helpers import SearchResult, extract_content, extract_context

# Optional NLP imports
try:
    import spacy

    SPACY_AVAILABLE = True
except ImportError:
    SPACY_AVAILABLE = False


def load_nlp():
    """Load spaCy model if available."""
    if not SPACY_AVAILABLE:
        return None
    try:
        nlp = spacy.load("en_core_web_sm")
        nlp.select_pipes(disable=["ner", "lemmatizer"])
        return nlp
    except Exception:
        return None


def search_semantic(
    jsonl_file: Path,
    query: str,
    speaker_filter: Optional[str],
    nlp,
) -> List[SearchResult]:
    """Semantic search: finds conceptually similar content using spaCy."""
    if not nlp:
        return []

    results = []
    conversation_id = jsonl_file.stem

    query_doc = nlp(query.lower())
    query_tokens = [
        token for token in query_doc if not token.is_stop and token.is_alpha
    ]

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

                        content_doc = nlp(content.lower())

                        similarity = calculate_semantic_similarity(
                            query_doc, query_tokens, content_doc
                        )

                        if similarity > 0.3:
                            context = extract_context(content, query, False)

                            timestamp = None
                            timestamp_str = entry.get("timestamp")
                            if timestamp_str:
                                try:
                                    timestamp = datetime.fromisoformat(
                                        timestamp_str.replace("Z", "+00:00")
                                    )
                                except ValueError:
                                    pass

                            result = SearchResult(
                                file_path=jsonl_file,
                                conversation_id=conversation_id,
                                matched_content=content[:200],
                                context=context,
                                speaker=speaker,
                                timestamp=timestamp,
                                relevance_score=similarity,
                                line_number=line_num,
                            )
                            results.append(result)

                except json.JSONDecodeError:
                    continue

    except Exception as e:
        print(f"Error searching {jsonl_file}: {e}")

    return results


def calculate_semantic_similarity(query_doc, query_tokens, content_doc) -> float:
    """Calculate semantic similarity using spaCy token matching."""
    if not query_tokens:
        return 0.0

    similar_count = 0
    for query_token in query_tokens:
        for content_token in content_doc:
            if content_token.is_alpha and not content_token.is_stop:
                if (
                    query_token.lemma_ == content_token.lemma_
                    or query_token.text == content_token.text
                ):
                    similar_count += 1
                    break

    if query_tokens:
        base_similarity = similar_count / len(query_tokens)
    else:
        base_similarity = 0.0

    if query_doc.text.lower() in content_doc.text.lower():
        base_similarity = min(1.0, base_similarity + 0.3)

    return base_similarity


def get_conversation_topics(
    jsonl_file: Path, nlp, max_topics: int = 5
) -> List[str]:
    """Extract main topics from a conversation using NLP noun phrases."""
    if not nlp:
        return []

    all_content = []
    try:
        with open(jsonl_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    content = extract_content(entry)
                    if content:
                        all_content.append(content)
                except json.JSONDecodeError:
                    continue
    except Exception:
        return []

    if not all_content:
        return []

    full_text = " ".join(all_content[:10])
    doc = nlp(full_text)

    noun_phrases = []
    for chunk in doc.noun_chunks:
        if len(chunk.text.split()) <= 3:
            noun_phrases.append(chunk.text.lower())

    phrase_counts: Dict[str, int] = {}
    for phrase in noun_phrases:
        phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1

    sorted_phrases = sorted(phrase_counts.items(), key=lambda x: x[1], reverse=True)
    return [phrase for phrase, count in sorted_phrases[:max_topics] if count > 1]
