"""
Output formatters for Claude Code conversations.

Converts extracted conversation data to markdown, JSON, or HTML files.
Also contains the noise-filtering / turn-merging pipeline that runs
before any format is written.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ── Noise filtering ───────────────────────────────────────────────────

# Patterns to strip from message content (XML command wrappers, etc.)
NOISE_PATTERNS = [
    re.compile(r"<local-command-caveat>.*?</local-command-caveat>", re.DOTALL),
    re.compile(r"<command-name>.*?</command-name>", re.DOTALL),
    re.compile(r"<command-args>.*?</command-args>", re.DOTALL),
    re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.DOTALL),
    re.compile(r"<command-message>.*?</command-message>", re.DOTALL),
]

# Messages that start with any of these strings are dropped entirely
NOISE_STRINGS = [
    "You've hit your limit",
    "Prompt is too long",
]


def clean_content(text: str) -> str:
    """Strip XML noise tags from a message's content."""
    for pattern in NOISE_PATTERNS:
        text = pattern.sub("", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text


def is_noise(text: str) -> bool:
    """Return True if the entire message is noise (should be dropped)."""
    stripped = text.strip()
    if not stripped:
        return True
    for noise in NOISE_STRINGS:
        if stripped.startswith(noise):
            return True
    return False


def merge_and_clean(conversation: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """Merge consecutive same-speaker messages and filter noise.

    Steps:
    1. Strip XML noise tags from each message content.
    2. Drop messages that are pure noise.
    3. Strip skill prompt templates (keep only user args after ARGUMENTS:).
    4. Merge neighboring messages from the same role into one turn.
    """
    from ..session.message import is_skill_message, strip_skill_prompt

    # Phase 1: clean + filter
    cleaned = []
    for msg in conversation:
        content = clean_content(msg["content"])
        if is_noise(content):
            # Preserve File Changes even when the message text is noise
            fc_idx = content.find("File Changes:")
            if fc_idx >= 0:
                content = content[fc_idx:]
            else:
                continue
        # Strip skill template — keep only user args
        if is_skill_message(content):
            content = strip_skill_prompt(content)
            if not content:
                continue
        cleaned.append({**msg, "content": content})

    # Phase 2: merge consecutive same-role messages
    merged: List[Dict[str, str]] = []
    for msg in cleaned:
        if merged and merged[-1]["role"] == msg["role"]:
            merged[-1]["content"] += "\n\n" + msg["content"]
        else:
            merged.append(dict(msg))

    return merged


# ── Filename helpers ──────────────────────────────────────────────────


def get_date_info(conversation: List[Dict[str, str]]) -> Tuple[str, str]:
    """Extract date and time strings from conversation's first message."""
    first_timestamp = conversation[0].get("timestamp", "") if conversation else ""
    if first_timestamp:
        try:
            dt = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
            return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
        except Exception:
            pass
    return datetime.now().strftime("%Y-%m-%d"), ""


def get_session_label(session_id: str) -> str:
    """Resolve session_id to a display label.

    Priority: customTitle > slug > first 6 UUID chars.
    """
    try:
        from ..session.resolver import get_claude_projects_dir, get_session_name

        projects_dir = get_claude_projects_dir()
        for jsonl_file in projects_dir.rglob(f"{session_id}*.jsonl"):
            if "subagents" not in str(jsonl_file):
                custom_title, slug = get_session_name(jsonl_file)
                if custom_title:
                    return custom_title
                if slug:
                    return slug
                break
    except (ImportError, Exception):
        pass
    return session_id[:6]


def make_filename(
    date_str: str,
    session_id: str,
    ext: str,
    filename_label: Optional[str] = None,
) -> str:
    """Build output filename.

    Format: ``claude-<name>-<date>.<ext>``
    Name priority: filename_label > session display name > first 6 UUID chars.
    """
    label = filename_label if filename_label else get_session_label(session_id)

    safe_label = "".join(c if c.isalnum() or c in "-_ " else "-" for c in label)
    safe_label = safe_label.strip("-_ ").replace(" ", "-")
    while "--" in safe_label:
        safe_label = safe_label.replace("--", "-")
    if not safe_label:
        safe_label = session_id[:6]
    return f"claude-{safe_label}-{date_str}.{ext}"


# ── Format writers ────────────────────────────────────────────────────


def save_as_markdown(
    conversation: List[Dict[str, str]],
    session_id: str,
    output_dir: Path,
    filename_label: Optional[str] = None,
) -> Optional[Path]:
    """Save conversation as a clean markdown file."""
    if not conversation:
        return None

    date_str, time_str = get_date_info(conversation)
    filename = make_filename(date_str, session_id, "md", filename_label)
    output_path = output_dir / filename

    cleaned = merge_and_clean(conversation)

    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# Claude Conversation Log\n\n")
        f.write(f"Session ID: {session_id}\n")
        f.write(f"Date: {date_str}")
        if time_str:
            f.write(f" {time_str}")
        f.write("\n\n---\n\n")

        for msg in cleaned:
            role = msg["role"]
            content = msg["content"]

            role_headers = {
                "user": "## User",
                "assistant": "## Claude",
                "tool_use": "### Tool Use",
                "tool_result": "### Tool Result",
                "system": "### System",
            }
            header = role_headers.get(role, f"## {role}")
            f.write(f"{header}\n\n{content}\n\n---\n\n")

    return output_path


def save_as_json(
    conversation: List[Dict[str, str]],
    session_id: str,
    output_dir: Path,
    filename_label: Optional[str] = None,
) -> Optional[Path]:
    """Save conversation as a JSON file."""
    if not conversation:
        return None

    date_str, _ = get_date_info(conversation)
    filename = make_filename(date_str, session_id, "json", filename_label)
    output_path = output_dir / filename

    cleaned = merge_and_clean(conversation)

    output = {
        "session_id": session_id,
        "date": date_str,
        "message_count": len(cleaned),
        "messages": cleaned,
    }

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)

    return output_path


def save_as_html(
    conversation: List[Dict[str, str]],
    session_id: str,
    output_dir: Path,
    filename_label: Optional[str] = None,
) -> Optional[Path]:
    """Save conversation as an HTML file with styling."""
    if not conversation:
        return None

    date_str, time_str = get_date_info(conversation)
    filename = make_filename(date_str, session_id, "html", filename_label)
    output_path = output_dir / filename

    html_head = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Claude Conversation - {session_id[:8]}</title>
    <style>
        body {{
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            line-height: 1.6;
            color: #333;
            max-width: 900px;
            margin: 0 auto;
            padding: 20px;
            background: #f5f5f5;
        }}
        .header {{
            background: white;
            padding: 20px;
            border-radius: 8px;
            margin-bottom: 20px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        h1 {{
            color: #2c3e50;
            margin: 0 0 10px 0;
        }}
        .metadata {{
            color: #666;
            font-size: 0.9em;
        }}
        .message {{
            background: white;
            padding: 15px 20px;
            margin-bottom: 15px;
            border-radius: 8px;
            box-shadow: 0 2px 4px rgba(0,0,0,0.1);
        }}
        .user {{ border-left: 4px solid #3498db; }}
        .assistant {{ border-left: 4px solid #2ecc71; }}
        .tool_use {{ border-left: 4px solid #f39c12; background: #fffbf0; }}
        .tool_result {{ border-left: 4px solid #e74c3c; background: #fff5f5; }}
        .system {{ border-left: 4px solid #95a5a6; background: #f8f9fa; }}
        .role {{
            font-weight: bold;
            margin-bottom: 10px;
            display: flex;
            align-items: center;
        }}
        .content {{
            white-space: pre-wrap;
            word-wrap: break-word;
        }}
        pre {{
            background: #f4f4f4;
            padding: 10px;
            border-radius: 4px;
            overflow-x: auto;
        }}
        code {{
            background: #f4f4f4;
            padding: 2px 4px;
            border-radius: 3px;
            font-family: 'Courier New', monospace;
        }}
    </style>
</head>
<body>
    <div class="header">
        <h1>Claude Conversation Log</h1>
        <div class="metadata">
            <p>Session ID: {session_id}</p>
            <p>Date: {date_str} {time_str}</p>
            <p>Messages: {len(conversation)}</p>
        </div>
    </div>
"""

    cleaned = merge_and_clean(conversation)

    role_display_map = {
        "user": "User",
        "assistant": "Claude",
        "tool_use": "Tool Use",
        "tool_result": "Tool Result",
        "system": "System",
    }

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_head)

        for msg in cleaned:
            role = msg["role"]
            content = msg["content"]

            content = content.replace("&", "&amp;")
            content = content.replace("<", "&lt;")
            content = content.replace(">", "&gt;")

            role_display = role_display_map.get(role, role)

            f.write(f'    <div class="message {role}">\n')
            f.write(f'        <div class="role">{role_display}</div>\n')
            f.write(f'        <div class="content">{content}</div>\n')
            f.write(f"    </div>\n")

        f.write("\n</body>\n</html>")

    return output_path


def save_conversation(
    conversation: List[Dict[str, str]],
    session_id: str,
    output_dir: Path,
    format: str = "markdown",
    filename_label: Optional[str] = None,
) -> Optional[Path]:
    """Save conversation in the specified format.

    Args:
        format: ``'markdown'``, ``'json'``, or ``'html'``.
    """
    writers = {
        "markdown": save_as_markdown,
        "json": save_as_json,
        "html": save_as_html,
    }
    writer = writers.get(format)
    if writer is None:
        print(f"Unsupported format: {format}")
        return None
    return writer(conversation, session_id, output_dir, filename_label)
