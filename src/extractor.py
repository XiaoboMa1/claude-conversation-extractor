"""
Core extraction logic for Claude Code conversations.

Parses the undocumented JSONL format used by Claude Code in ~/.claude/projects/
and converts conversations to markdown, JSON, or HTML.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Noise patterns to filter out entirely (messages matching these are dropped)
_NOISE_PATTERNS = [
    re.compile(r"<local-command-caveat>.*?</local-command-caveat>", re.DOTALL),
    re.compile(r"<command-name>.*?</command-name>", re.DOTALL),
    re.compile(r"<command-args>.*?</command-args>", re.DOTALL),
    re.compile(r"<local-command-stdout>.*?</local-command-stdout>", re.DOTALL),
    re.compile(r"<command-message>.*?</command-message>", re.DOTALL),
]

# Messages that consist entirely of these strings are dropped
_NOISE_STRINGS = [
    "You've hit your limit",
    "Prompt is too long",
]


class ClaudeConversationExtractor:
    """Extract and convert Claude Code conversations from JSONL to markdown."""

    def __init__(self, output_dir: Optional[Path] = None):
        """Initialize the extractor with Claude's directory and output location."""
        self.claude_dir = Path.home() / ".claude" / "projects"

        if output_dir:
            self.output_dir = Path(output_dir)
            self.output_dir.mkdir(parents=True, exist_ok=True)
        else:
            # Try multiple possible output directories
            possible_dirs = [
                Path.home() / "Documents" / "Claude logs",
                Path.home() / "Claude logs",
                Path.cwd() / "claude-logs",
            ]

            # Use the first directory we can create
            for dir_path in possible_dirs:
                try:
                    dir_path.mkdir(parents=True, exist_ok=True)
                    # Test if we can write to it
                    test_file = dir_path / ".test"
                    test_file.touch()
                    test_file.unlink()
                    self.output_dir = dir_path
                    break
                except Exception:
                    continue
            else:
                # Fallback to current directory
                self.output_dir = Path.cwd() / "claude-logs"
                self.output_dir.mkdir(exist_ok=True)

        print(f"Saving logs to: {self.output_dir}")

    # ── Session discovery ──────────────────────────────────────────────

    def find_sessions(self, project_path: Optional[str] = None, include_subagents: bool = False) -> List[Path]:
        """Find all JSONL session files, sorted by most recent first.

        Only returns top-level session files by default (excludes subagent files
        inside <session-id>/subagents/ directories).

        Args:
            project_path: Optional project subdirectory to search
            include_subagents: If True, also include subagent JSONL files
        """
        if project_path:
            search_dir = self.claude_dir / project_path
        else:
            search_dir = self.claude_dir

        sessions = []
        if search_dir.exists():
            for jsonl_file in search_dir.rglob("*.jsonl"):
                # Skip subagent files unless explicitly requested
                if not include_subagents and "subagents" in jsonl_file.parts:
                    continue
                sessions.append(jsonl_file)
        # Use (mtime, path) as sort key for stable ordering across calls
        return sorted(sessions, key=lambda x: (x.stat().st_mtime, str(x)), reverse=True)

    def find_subagents(self, session_path: Path) -> List[Tuple[Path, Dict]]:
        """Find all subagent JSONL files belonging to a session.

        Skips auto-compaction subagents (agent-acompact-*) which are compressed
        duplicates of the main session conversation. Only returns subagents with
        independent content (e.g. Explore agents for code search).

        Args:
            session_path: Path to the main session JSONL file

        Returns:
            List of (jsonl_path, meta_dict) tuples, sorted by modification time
        """
        session_id = session_path.stem
        session_dir = session_path.parent / session_id / "subagents"

        subagents = []
        if session_dir.exists():
            for jsonl_file in session_dir.glob("*.jsonl"):
                # Skip acompact (auto-compaction) files — these are compressed
                # sidechain duplicates of the main session, not independent content
                if jsonl_file.stem.startswith("agent-acompact-"):
                    continue

                meta = {}
                meta_file = jsonl_file.with_suffix(".meta.json")
                if meta_file.exists():
                    try:
                        with open(meta_file, "r", encoding="utf-8") as f:
                            meta = json.load(f)
                    except Exception:
                        pass
                subagents.append((jsonl_file, meta))

        return sorted(subagents, key=lambda x: x[0].stat().st_mtime)

    @staticmethod
    def _get_subagent_header(jsonl_path: Path) -> Optional[str]:
        """Extract the first markdown header from the last assistant response.

        Used to give Explore subagents a meaningful label instead of their
        opaque agent ID (e.g. "agent-a434e98d83baaba4c").

        Returns:
            The header text (without leading # marks), or None if not found.
        """
        last_text = ""
        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if entry.get("type") != "assistant":
                        continue
                    msg = entry.get("message", {})
                    content = msg.get("content", [])
                    if isinstance(content, list):
                        for item in content:
                            if isinstance(item, dict) and item.get("type") == "text":
                                last_text = item.get("text", "")
                    elif isinstance(content, str):
                        last_text = content
        except Exception:
            return None

        if not last_text:
            return None

        # Find first markdown header (# or ## or ###)
        for ln in last_text.split("\n"):
            m = re.match(r"^#{1,3}\s+(.+)", ln.strip())
            if m:
                return m.group(1).strip()
        return None

    def find_session_by_id(self, session_id_prefix: str, quiet: bool = False) -> Optional[Path]:
        """Find a session JSONL file by session ID prefix.

        Args:
            session_id_prefix: First 7+ characters of the session ID
            quiet: If True, suppress error messages (used when falling back to numeric mode)

        Returns:
            Path to the matching session JSONL, or None
        """
        all_sessions = self.find_sessions()
        matches = [s for s in all_sessions if s.stem.startswith(session_id_prefix)]

        if len(matches) == 1:
            return matches[0]
        elif len(matches) > 1:
            print(f"Prefix '{session_id_prefix}' matches {len(matches)} sessions:")
            for m in matches:
                print(f"   {m.stem}")
            print("   Please use a longer prefix to disambiguate.")
            return None
        else:
            if not quiet:
                print(f"No session found matching prefix '{session_id_prefix}'")
            return None

    # ── Conversation extraction ────────────────────────────────────────

    def extract_conversation(self, jsonl_path: Path, detailed: bool = False, diff: bool = False, think: bool = False) -> List[Dict[str, str]]:
        """Extract conversation messages from a JSONL file.

        Args:
            jsonl_path: Path to the JSONL file
            detailed: If True, include tool use, MCP responses, and system messages
            diff: If True, collect file changes from toolUseResult and attach to assistant turns
            think: If True, include thinking blocks wrapped in markdown comments
        """
        conversation = []
        # When diff=True, track file changes per assistant turn.
        # Each item: index into conversation[] of the last assistant message,
        # mapped to an ordered dict of filePath -> list of (oldString, newString) or None for writes.
        # We use _pending_changes to accumulate changes, then flush when a real user message appears.
        _last_assistant_idx = -1
        # filePath -> list of edits.  Each edit: (old, new) for Edit, None for Write.
        _pending_changes: Dict[str, list] = {}

        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())

                        # -- Collect file changes from toolUseResult --
                        if diff and "toolUseResult" in entry:
                            tr = entry["toolUseResult"]
                            fp = tr.get("filePath")
                            if fp:
                                if fp not in _pending_changes:
                                    _pending_changes[fp] = []
                                if "oldString" in tr and "newString" in tr:
                                    _pending_changes[fp].append((tr["oldString"], tr["newString"]))
                                elif "content" in tr:
                                    # Write (new file creation) — mark with None
                                    _pending_changes[fp].append(None)

                        # Extract user messages
                        if entry.get("type") == "user" and "message" in entry:
                            msg = entry["message"]
                            if isinstance(msg, dict) and msg.get("role") == "user":
                                content = msg.get("content", "")
                                text = self._extract_text_content(content)

                                if text and text.strip():
                                    # Before adding a real user message, flush pending
                                    # file changes into the last assistant message.
                                    if diff and _pending_changes and _last_assistant_idx >= 0:
                                        self._attach_file_changes(conversation, _last_assistant_idx, _pending_changes)
                                        _pending_changes = {}

                                    conversation.append(
                                        {
                                            "role": "user",
                                            "content": text,
                                            "timestamp": entry.get("timestamp", ""),
                                        }
                                    )

                        # Extract assistant messages
                        elif entry.get("type") == "assistant" and "message" in entry:
                            msg = entry["message"]
                            if isinstance(msg, dict) and msg.get("role") == "assistant":
                                content = msg.get("content", [])
                                text = self._extract_text_content(content, detailed=detailed, think=think)

                                if text and text.strip():
                                    conversation.append(
                                        {
                                            "role": "assistant",
                                            "content": text,
                                            "timestamp": entry.get("timestamp", ""),
                                        }
                                    )
                                    if diff:
                                        _last_assistant_idx = len(conversation) - 1

                        # Include tool use and system messages if detailed mode
                        elif detailed:
                            # Extract tool use events
                            if entry.get("type") == "tool_use":
                                tool_data = entry.get("tool", {})
                                tool_name = tool_data.get("name", "unknown")
                                tool_input = tool_data.get("input", {})
                                conversation.append(
                                    {
                                        "role": "tool_use",
                                        "content": f"Tool: {tool_name}\nInput: {json.dumps(tool_input, indent=2, ensure_ascii=False)}",
                                        "timestamp": entry.get("timestamp", ""),
                                    }
                                )

                            # Extract tool results
                            elif entry.get("type") == "tool_result":
                                result = entry.get("result", {})
                                output = result.get("output", "") or result.get("error", "")
                                conversation.append(
                                    {
                                        "role": "tool_result",
                                        "content": f"Result:\n{output}",
                                        "timestamp": entry.get("timestamp", ""),
                                    }
                                )

                            # Extract system messages
                            elif entry.get("type") == "system" and "message" in entry:
                                msg = entry.get("message", "")
                                if msg:
                                    conversation.append(
                                        {
                                            "role": "system",
                                            "content": f"System: {msg}",
                                            "timestamp": entry.get("timestamp", ""),
                                        }
                                    )

                    except json.JSONDecodeError:
                        continue
                    except Exception:
                        # Silently skip problematic entries
                        continue

            # Flush any remaining file changes at end of file
            if diff and _pending_changes and _last_assistant_idx >= 0:
                self._attach_file_changes(conversation, _last_assistant_idx, _pending_changes)

        except Exception as e:
            print(f"Error reading file {jsonl_path}: {e}")

        return conversation

    @staticmethod
    def _attach_file_changes(
        conversation: List[Dict[str, str]],
        assistant_idx: int,
        changes: Dict[str, list],
    ) -> None:
        """Append a formatted file-changes block to an assistant message in-place.

        Args:
            conversation: The conversation list being built.
            assistant_idx: Index of the assistant message to append to.
            changes: Ordered mapping of filePath -> list of edits.
                     Each edit is (oldString, newString) for Edit, or None for Write.
        """
        if assistant_idx < 0 or assistant_idx >= len(conversation):
            return
        block = ClaudeConversationExtractor._format_file_changes(changes)
        if block:
            conversation[assistant_idx]["content"] += "\n\n" + block

    @staticmethod
    def _format_file_changes(changes: Dict[str, list]) -> str:
        """Render collected file changes into a markdown block.

        Format per the spec:
        - Ordered list, one entry per file
        - Write (new file): just show the path
        - Edit: aggregate all edits in one code block, separated by a divider
        """
        if not changes:
            return ""

        parts = ["File Changes:"]
        file_num = 0
        for fp, edits in changes.items():
            file_num += 1
            # Check if all edits are writes (None)
            is_all_write = all(e is None for e in edits)
            if is_all_write:
                parts.append(f"{file_num}. New file: {fp}")
            else:
                parts.append(f"{file_num}. {fp}")
                # Collect non-None edits
                real_edits = [e for e in edits if e is not None]
                if real_edits:
                    old_blocks = []
                    new_blocks = []
                    for old, new in real_edits:
                        old_blocks.append(old)
                        new_blocks.append(new)
                    divider = "\n----------\n"
                    parts.append("- Before:")
                    parts.append("```")
                    parts.append(divider.join(old_blocks))
                    parts.append("```")
                    parts.append("- After:")
                    parts.append("```")
                    parts.append(divider.join(new_blocks))
                    parts.append("```")
        return "\n".join(parts)

    def _extract_text_content(self, content, detailed: bool = False, think: bool = False) -> str:
        """Extract text from various content formats Claude uses.

        Args:
            content: The content to extract from
            detailed: If True, include tool use blocks and other metadata
            think: If True, include thinking blocks wrapped in markdown comments
        """
        if isinstance(content, str):
            return content
        elif isinstance(content, list):
            # Extract text from content array
            text_parts = []
            for item in content:
                if isinstance(item, dict):
                    if item.get("type") == "thinking" and think:
                        thinking_text = item.get("thinking", "")
                        if thinking_text:
                            text_parts.append(f"<!-- {thinking_text} -->")
                    elif item.get("type") == "text":
                        text_parts.append(item.get("text", ""))
                    elif detailed and item.get("type") == "tool_use":
                        # Include tool use details in detailed mode
                        tool_name = item.get("name", "unknown")
                        tool_input = item.get("input", {})
                        text_parts.append(f"\nUsing tool: {tool_name}")
                        text_parts.append(f"Input: {json.dumps(tool_input, indent=2, ensure_ascii=False)}\n")
            return "\n".join(text_parts)
        else:
            return str(content)

    # ── Noise filtering & turn merging ─────────────────────────────────

    @staticmethod
    def _clean_content(text: str) -> str:
        """Strip XML noise tags from a message's content."""
        for pattern in _NOISE_PATTERNS:
            text = pattern.sub("", text)
        # Collapse runs of blank lines left after tag removal
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        return text

    @staticmethod
    def _is_noise(text: str) -> bool:
        """Return True if the entire message is noise (should be dropped)."""
        stripped = text.strip()
        if not stripped:
            return True
        for noise in _NOISE_STRINGS:
            if stripped.startswith(noise):
                return True
        return False

    def _merge_and_clean(self, conversation: List[Dict[str, str]]) -> List[Dict[str, str]]:
        """Merge consecutive same-speaker messages and filter noise.

        Steps:
        1. Strip XML noise tags from each message content.
        2. Drop messages that are pure noise (empty after cleaning, or matching noise strings).
        3. Merge neighboring messages from the same role into one turn.
        """
        # Phase 1: clean + filter
        cleaned = []
        for msg in conversation:
            content = self._clean_content(msg["content"])
            if self._is_noise(content):
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

    # ── Terminal display ───────────────────────────────────────────────

    def display_conversation(self, jsonl_path: Path, detailed: bool = False) -> None:
        """Display a conversation in the terminal with pagination.

        Args:
            jsonl_path: Path to the JSONL file
            detailed: If True, include tool use and system messages
        """
        try:
            # Extract conversation
            messages = self.extract_conversation(jsonl_path, detailed=detailed)

            if not messages:
                print("No messages found in conversation")
                return

            # Get session info
            session_id = jsonl_path.stem

            # Clear screen and show header
            print("\033[2J\033[H", end="")  # Clear screen
            print("=" * 60)
            print(f"Viewing: {jsonl_path.parent.name}")
            print(f"Session: {session_id[:8]}...")

            # Get timestamp from first message
            first_timestamp = messages[0].get("timestamp", "")
            if first_timestamp:
                try:
                    dt = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
                    print(f"Date: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                except Exception:
                    pass

            print("=" * 60)
            print("Up/Down to scroll, Q to quit, Enter to continue\n")

            # Display messages with pagination
            lines_shown = 8  # Header lines
            lines_per_page = 30

            for i, msg in enumerate(messages):
                role = msg["role"]
                content = msg["content"]

                # Format role display
                if role == "user" or role == "human":
                    print(f"\n{'─' * 40}")
                    print("HUMAN:")
                    print(f"{'─' * 40}")
                elif role == "assistant":
                    print(f"\n{'─' * 40}")
                    print("CLAUDE:")
                    print(f"{'─' * 40}")
                elif role == "tool_use":
                    print("\nTOOL USE:")
                elif role == "tool_result":
                    print("\nTOOL RESULT:")
                elif role == "system":
                    print("\nSYSTEM:")
                else:
                    print(f"\n{role.upper()}:")

                # Display content (limit very long messages)
                lines = content.split('\n')
                max_lines_per_msg = 50

                for line_idx, line in enumerate(lines[:max_lines_per_msg]):
                    # Wrap very long lines
                    if len(line) > 100:
                        line = line[:97] + "..."
                    print(line)
                    lines_shown += 1

                    # Check if we need to paginate
                    if lines_shown >= lines_per_page:
                        response = input("\n[Enter] Continue | [Q] Quit: ").strip().upper()
                        if response == "Q":
                            print("\nStopped viewing")
                            return
                        # Clear screen for next page
                        print("\033[2J\033[H", end="")
                        lines_shown = 0

                if len(lines) > max_lines_per_msg:
                    print(f"... [{len(lines) - max_lines_per_msg} more lines truncated]")
                    lines_shown += 1

            print("\n" + "=" * 60)
            print("End of conversation")
            print("=" * 60)
            input("\nPress Enter to continue...")

        except Exception as e:
            print(f"Error displaying conversation: {e}")
            input("\nPress Enter to continue...")

    # ── Filename / metadata helpers ────────────────────────────────────

    def _get_date_info(self, conversation: List[Dict[str, str]]) -> Tuple[str, str]:
        """Extract date and time strings from conversation's first message."""
        first_timestamp = conversation[0].get("timestamp", "") if conversation else ""
        if first_timestamp:
            try:
                dt = datetime.fromisoformat(first_timestamp.replace("Z", "+00:00"))
                return dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S")
            except Exception:
                pass
        return datetime.now().strftime("%Y-%m-%d"), ""

    def _make_filename(self, date_str: str, session_id: str, ext: str, filename_label: Optional[str] = None) -> str:
        """Build output filename.

        Format: claude-chat-<name>-<date>.<ext>
        Name priority: filename_label > session display name > first 6 chars of UUID.
        """
        if filename_label:
            label = filename_label
        else:
            # Try to get session display name (customTitle or slug)
            label = self._get_session_label(session_id)
        # Sanitize label for filesystem
        safe_label = "".join(c if c.isalnum() or c in "-_ " else "-" for c in label)
        safe_label = safe_label.strip("-_ ").replace(" ", "-")
        while "--" in safe_label:
            safe_label = safe_label.replace("--", "-")
        if not safe_label:
            safe_label = session_id[:6]
        return f"claude-chat-{safe_label}-{date_str}.{ext}"

    def _get_session_label(self, session_id: str) -> str:
        """Resolve session_id to a display label (customTitle > slug > first 6 UUID chars)."""
        try:
            from session_resolver import get_claude_projects_dir, get_session_name
            projects_dir = get_claude_projects_dir()
            # Find the jsonl file for this session_id
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

    # ── Save methods ───────────────────────────────────────────────────

    def save_as_markdown(
        self, conversation: List[Dict[str, str]], session_id: str,
        filename_label: Optional[str] = None
    ) -> Optional[Path]:
        """Save conversation as clean markdown file."""
        if not conversation:
            return None

        date_str, time_str = self._get_date_info(conversation)
        filename = self._make_filename(date_str, session_id, "md", filename_label)
        output_path = self.output_dir / filename

        cleaned = self._merge_and_clean(conversation)

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

                if role == "user":
                    f.write("## User\n\n")
                    f.write(f"{content}\n\n")
                elif role == "assistant":
                    f.write("## Claude\n\n")
                    f.write(f"{content}\n\n")
                elif role == "tool_use":
                    f.write("### Tool Use\n\n")
                    f.write(f"{content}\n\n")
                elif role == "tool_result":
                    f.write("### Tool Result\n\n")
                    f.write(f"{content}\n\n")
                elif role == "system":
                    f.write("### System\n\n")
                    f.write(f"{content}\n\n")
                else:
                    f.write(f"## {role}\n\n")
                    f.write(f"{content}\n\n")
                f.write("---\n\n")

        return output_path

    def save_as_json(
        self, conversation: List[Dict[str, str]], session_id: str,
        filename_label: Optional[str] = None
    ) -> Optional[Path]:
        """Save conversation as JSON file."""
        if not conversation:
            return None

        date_str, _ = self._get_date_info(conversation)
        filename = self._make_filename(date_str, session_id, "json", filename_label)
        output_path = self.output_dir / filename

        cleaned = self._merge_and_clean(conversation)

        # Create JSON structure
        output = {
            "session_id": session_id,
            "date": date_str,
            "message_count": len(cleaned),
            "messages": cleaned
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(output, f, indent=2, ensure_ascii=False)

        return output_path

    def save_as_html(
        self, conversation: List[Dict[str, str]], session_id: str,
        filename_label: Optional[str] = None
    ) -> Optional[Path]:
        """Save conversation as HTML file with syntax highlighting."""
        if not conversation:
            return None

        date_str, time_str = self._get_date_info(conversation)
        filename = self._make_filename(date_str, session_id, "html", filename_label)
        output_path = self.output_dir / filename

        # HTML template with modern styling
        html_content = f"""<!DOCTYPE html>
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
        .user {{
            border-left: 4px solid #3498db;
        }}
        .assistant {{
            border-left: 4px solid #2ecc71;
        }}
        .tool_use {{
            border-left: 4px solid #f39c12;
            background: #fffbf0;
        }}
        .tool_result {{
            border-left: 4px solid #e74c3c;
            background: #fff5f5;
        }}
        .system {{
            border-left: 4px solid #95a5a6;
            background: #f8f9fa;
        }}
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

        cleaned = self._merge_and_clean(conversation)

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html_content)

            for msg in cleaned:
                role = msg["role"]
                content = msg["content"]

                # Escape HTML
                content = content.replace("&", "&amp;")
                content = content.replace("<", "&lt;")
                content = content.replace(">", "&gt;")

                role_display = {
                    "user": "User",
                    "assistant": "Claude",
                    "tool_use": "Tool Use",
                    "tool_result": "Tool Result",
                    "system": "System"
                }.get(role, role)

                f.write(f'    <div class="message {role}">\n')
                f.write(f'        <div class="role">{role_display}</div>\n')
                f.write(f'        <div class="content">{content}</div>\n')
                f.write(f'    </div>\n')

            f.write("\n</body>\n</html>")

        return output_path

    def save_conversation(
        self, conversation: List[Dict[str, str]], session_id: str,
        format: str = "markdown", filename_label: Optional[str] = None
    ) -> Optional[Path]:
        """Save conversation in the specified format.

        Args:
            conversation: The conversation data
            session_id: Session identifier
            format: Output format ('markdown', 'json', 'html')
            filename_label: Optional label for filename (overrides default session_id[:8] truncation)
        """
        if format == "markdown":
            return self.save_as_markdown(conversation, session_id, filename_label=filename_label)
        elif format == "json":
            return self.save_as_json(conversation, session_id, filename_label=filename_label)
        elif format == "html":
            return self.save_as_html(conversation, session_id, filename_label=filename_label)
        else:
            print(f"Unsupported format: {format}")
            return None

    # ── Session listing & preview ──────────────────────────────────────

    def get_conversation_preview(self, session_path: Path) -> Tuple[str, int]:
        """Get a preview of the conversation's first real user message and message count."""
        try:
            first_user_msg = ""
            msg_count = 0

            with open(session_path, 'r', encoding='utf-8') as f:
                for line in f:
                    msg_count += 1
                    if not first_user_msg:
                        try:
                            data = json.loads(line)
                            # Check for user message
                            if data.get("type") == "user" and "message" in data:
                                msg = data["message"]
                                if msg.get("role") == "user":
                                    content = msg.get("content", "")

                                    # Handle list content (common format in Claude JSONL)
                                    if isinstance(content, list):
                                        for item in content:
                                            if isinstance(item, dict) and item.get("type") == "text":
                                                text = item.get("text", "").strip()

                                                # Skip tool results
                                                if text.startswith("tool_use_id"):
                                                    continue

                                                # Skip interruption messages
                                                if "[Request interrupted" in text:
                                                    continue

                                                # Skip Claude's session continuation messages
                                                if "session is being continued" in text.lower():
                                                    continue

                                                # Remove XML-like tags (command messages, etc)
                                                text = re.sub(r'<[^>]+>', '', text).strip()

                                                # Skip command outputs
                                                if "is running" in text and "\u2026" in text:
                                                    continue

                                                # Handle image references - extract text after them
                                                if text.startswith("[Image #"):
                                                    parts = text.split("]", 1)
                                                    if len(parts) > 1:
                                                        text = parts[1].strip()

                                                # If we have real user text, use it
                                                if text and len(text) > 3:
                                                    first_user_msg = text[:100].replace('\n', ' ')
                                                    break

                                    # Handle string content (less common but possible)
                                    elif isinstance(content, str):
                                        content = content.strip()

                                        # Remove XML-like tags
                                        content = re.sub(r'<[^>]+>', '', content).strip()

                                        # Skip command outputs
                                        if "is running" in content and "\u2026" in content:
                                            continue

                                        # Skip Claude's session continuation messages
                                        if "session is being continued" in content.lower():
                                            continue

                                        # Skip tool results and interruptions
                                        if not content.startswith("tool_use_id") and "[Request interrupted" not in content:
                                            if content and len(content) > 3:
                                                first_user_msg = content[:100].replace('\n', ' ')
                        except json.JSONDecodeError:
                            continue

            return first_user_msg or "No preview available", msg_count
        except Exception as e:
            return f"Error: {str(e)[:30]}", 0

    def list_recent_sessions(self, limit: int = None) -> List[Path]:
        """List recent sessions with details."""
        sessions = self.find_sessions()

        if not sessions:
            print("No Claude sessions found in ~/.claude/projects/")
            print("Make sure you've used Claude Code and have conversations saved.")
            return []

        print(f"\nFound {len(sessions)} Claude sessions:\n")
        print("=" * 80)

        # Show all sessions if no limit specified
        sessions_to_show = sessions[:limit] if limit else sessions
        for i, session in enumerate(sessions_to_show, 1):
            # Clean up project name (remove hyphens, make readable)
            project = session.parent.name.replace('-', ' ').strip()
            if project.startswith("Users"):
                project = "~/" + "/".join(project.split()[2:]) if len(project.split()) > 2 else "Home"

            session_id = session.stem
            modified = datetime.fromtimestamp(session.stat().st_mtime)

            # Get file size
            size = session.stat().st_size
            size_kb = size / 1024

            # Get preview and message count
            preview, msg_count = self.get_conversation_preview(session)

            # Check for subagents
            subagents = self.find_subagents(session)
            subagent_info = f"   Subagents: {len(subagents)}" if subagents else ""

            # Get session display name
            session_label = self._get_session_label(session_id)

            # Print formatted info
            print(f"\n{i}. {project}")
            print(f"   Session: {session_id[:8]}... ({session_label})")
            print(f"   Modified: {modified.strftime('%Y-%m-%d %H:%M')}")
            print(f"   Messages: {msg_count}")
            print(f"   Size: {size_kb:.1f} KB")
            if subagent_info:
                print(subagent_info)
            print(f"   Preview: \"{preview}...\"")

        print("\n" + "=" * 80)
        return sessions[:limit]

    # ── Batch extraction ───────────────────────────────────────────────

    def extract_multiple(
        self, sessions: List[Path], indices: List[int],
        format: str = "markdown", detailed: bool = False, diff: bool = False, think: bool = False
    ) -> Tuple[int, int]:
        """Extract multiple sessions by index.

        Args:
            sessions: List of session paths
            indices: Indices to extract
            format: Output format ('markdown', 'json', 'html')
            detailed: If True, include tool use and system messages
            diff: If True, include file changes in output
            think: If True, include thinking blocks in output
        """
        success = 0
        total = len(indices)

        for idx in indices:
            if 0 <= idx < len(sessions):
                session_path = sessions[idx]
                conversation = self.extract_conversation(session_path, detailed=detailed, diff=diff, think=think)
                if conversation:
                    output_path = self.save_conversation(conversation, session_path.stem, format=format)
                    success += 1
                    msg_count = len(conversation)
                    print(
                        f"{success}/{total}: {output_path.name} "
                        f"({msg_count} messages)"
                    )
                else:
                    print(f"Skipped session {idx + 1} (no conversation)")
            else:
                print(f"Invalid session number: {idx + 1}")

        return success, total

    def extract_session_with_subagents(
        self, session_path: Path, format: str = "markdown", detailed: bool = False, diff: bool = False, think: bool = False
    ) -> int:
        """Extract a session and all its subagent conversations.

        Produces one file for the main session and one file per subagent.

        Args:
            session_path: Path to the main session JSONL
            format: Output format ('markdown', 'json', 'html')
            detailed: If True, include tool use and system messages
            diff: If True, include file changes in output
            think: If True, include thinking blocks in output

        Returns:
            Number of files successfully saved
        """
        session_id = session_path.stem
        saved = 0

        # 1. Extract main session
        print(f"\nMain session: {session_id}")
        conversation = self.extract_conversation(session_path, detailed=detailed, diff=diff, think=think)
        if conversation:
            output_path = self.save_conversation(conversation, session_id, format=format)
            if output_path:
                print(f"   Saved {output_path.name} ({len(conversation)} messages)")
                saved += 1
        else:
            print("   No conversation content")

        # 2. Find and extract subagents
        subagents = self.find_subagents(session_path)
        if subagents:
            print(f"\nFound {len(subagents)} subagent(s):")
            for agent_path, meta in subagents:
                agent_id = agent_path.stem
                agent_type = meta.get("agentType", "unknown")
                agent_desc = meta.get("description", "")
                print(f"\n   {agent_id}")
                print(f"      Type: {agent_type}")
                if agent_desc:
                    print(f"      Desc: {agent_desc}")

                agent_conv = self.extract_conversation(agent_path, detailed=detailed, diff=diff, think=think)
                if agent_conv:
                    # Build a descriptive filename label.
                    # Try to use the first markdown header from the agent's
                    # Claude response (e.g. "GFT Implementation Files").
                    # Falls back to the raw agent ID if no header found.
                    header = self._get_subagent_header(agent_path)
                    if header:
                        label = f"{session_id[:8]}-sub-{header}"
                    else:
                        label = f"{session_id[:8]}-sub-{agent_id}"
                    agent_output = self.save_conversation(
                        agent_conv,
                        agent_id,  # session_id stored inside the file
                        format=format,
                        filename_label=label,
                    )
                    if agent_output:
                        print(f"      Saved {agent_output.name} ({len(agent_conv)} messages)")
                        saved += 1
                else:
                    print("      No conversation content")
        else:
            print("\n   (No subagents found for this session)")

        return saved
