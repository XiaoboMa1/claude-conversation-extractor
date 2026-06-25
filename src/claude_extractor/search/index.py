"""
Search index creation for faster subsequent searches.

Pre-processes all conversations and saves metadata.
"""

import json
from datetime import datetime
from pathlib import Path

from .helpers import extract_content


def create_search_index(search_dir: Path, output_file: Path) -> None:
    """Create a search index for faster subsequent searches.

    Pre-processes all conversations and saves metadata to a JSON file.
    """
    index = {"created": datetime.now().isoformat(), "conversations": {}}

    jsonl_files = list(search_dir.rglob("*.jsonl"))

    for jsonl_file in jsonl_files:
        conv_id = jsonl_file.stem

        metadata = {
            "path": str(jsonl_file),
            "modified": datetime.fromtimestamp(jsonl_file.stat().st_mtime).isoformat(),
            "size": jsonl_file.stat().st_size,
            "message_count": 0,
            "speakers": set(),
            "first_message": None,
            "last_message": None,
        }

        try:
            with open(jsonl_file, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())
                        if entry.get("type") in ["user", "assistant"]:
                            metadata["message_count"] += 1
                            speaker = (
                                "human" if entry["type"] == "user" else "assistant"
                            )
                            metadata["speakers"].add(speaker)

                            if metadata["first_message"] is None:
                                metadata["first_message"] = entry.get("timestamp")
                            metadata["last_message"] = entry.get("timestamp")

                    except json.JSONDecodeError:
                        continue
        except Exception:
            continue

        metadata["speakers"] = list(metadata["speakers"])
        index["conversations"][conv_id] = metadata

    with open(output_file, "w") as f:
        json.dump(index, f, indent=2)

    print(f"Created search index with {len(index['conversations'])} conversations")
