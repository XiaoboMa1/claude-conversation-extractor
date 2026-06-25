"""
Core extraction logic for Claude Code conversations.

Parses the undocumented JSONL format used by Claude Code in ~/.claude/projects/
and converts conversations to markdown, JSON, or HTML.

ClaudeConversationExtractor is the public API.  JSONL content parsing
lives in parser.py, output formatting in formatters.py, session
discovery in session/.
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import formatters as fmt
from .parser import (
    attach_file_changes,
    collect_file_change_from_tool_use,
    collect_write_change,
    extract_detailed_entry,
    extract_text_content,
)
from ..session import discovery as sd

# Re-export noise constants so tests/external code referencing
# extractor._NOISE_PATTERNS / extractor._NOISE_STRINGS still work.
_NOISE_PATTERNS = fmt.NOISE_PATTERNS
_NOISE_STRINGS = fmt.NOISE_STRINGS


class ClaudeConversationExtractor:
    """Extract and convert Claude Code conversations from JSONL to markdown."""

    def __init__(self, output_dir: Optional[Path] = None):
        """Initialize the extractor with Claude's directory and output location."""
        self.claude_dir = Path.home() / ".claude" / "projects"

        if output_dir:
            self.output_dir = Path(output_dir)
            self.output_dir.mkdir(parents=True, exist_ok=True)
        else:
            possible_dirs = [
                Path.home() / "Documents" / "Claude logs",
                Path.home() / "Claude logs",
                Path.cwd() / "claude-logs",
            ]

            for dir_path in possible_dirs:
                try:
                    dir_path.mkdir(parents=True, exist_ok=True)
                    test_file = dir_path / ".test"
                    test_file.touch()
                    test_file.unlink()
                    self.output_dir = dir_path
                    break
                except Exception:
                    continue
            else:
                self.output_dir = Path.cwd() / "claude-logs"
                self.output_dir.mkdir(exist_ok=True)

        print(f"Saving logs to: {self.output_dir}")

    # ── Noise filtering (delegates to formatters) ───────────────────────

    @staticmethod
    def _clean_content(text: str) -> str:
        return fmt.clean_content(text)

    @staticmethod
    def _is_noise(text: str) -> bool:
        return fmt.is_noise(text)

    def _merge_and_clean(
        self, conversation: List[Dict[str, str]]
    ) -> List[Dict[str, str]]:
        return fmt.merge_and_clean(conversation)

    # ── Session discovery (delegates to session.discovery) ──────────────

    def find_sessions(
        self, project_path: Optional[str] = None, include_subagents: bool = False
    ) -> List[Path]:
        """Find all JSONL session files, sorted by most recent first."""
        return sd.find_sessions(self.claude_dir, project_path, include_subagents)

    def find_subagents(self, session_path: Path) -> List[Tuple[Path, Dict]]:
        """Find all subagent JSONL files belonging to a session."""
        return sd.find_subagents(session_path)

    @staticmethod
    def _get_subagent_header(jsonl_path: Path) -> Optional[str]:
        return sd.get_subagent_header(jsonl_path)

    def find_session_by_id(
        self, session_id_prefix: str, quiet: bool = False
    ) -> Optional[Path]:
        return sd.find_session_by_id(self.claude_dir, session_id_prefix, quiet)

    # ── Preview / listing ──────────────────────────────────────────────

    def get_conversation_preview(self, session_path: Path) -> Tuple[str, int]:
        return sd.get_conversation_preview(session_path)

    def list_recent_sessions(self, limit: int = None) -> List[Path]:
        """List recent sessions with details."""
        sessions = self.find_sessions()

        if not sessions:
            print("No Claude sessions found in ~/.claude/projects/")
            print("Make sure you've used Claude Code and have conversations saved.")
            return []

        print(f"\nFound {len(sessions)} Claude sessions:\n")
        print("=" * 80)

        sessions_to_show = sessions[:limit] if limit else sessions
        for i, session in enumerate(sessions_to_show, 1):
            project = session.parent.name.replace("-", " ").strip()
            if project.startswith("Users"):
                project = (
                    "~/" + "/".join(project.split()[2:])
                    if len(project.split()) > 2
                    else "Home"
                )

            session_id = session.stem
            modified = datetime.fromtimestamp(session.stat().st_mtime)

            size_kb = session.stat().st_size / 1024
            preview, msg_count = self.get_conversation_preview(session)

            subagents = self.find_subagents(session)
            subagent_info = f"   Subagents: {len(subagents)}" if subagents else ""

            session_label = fmt.get_session_label(session_id)

            print(f"\n{i}. {project}")
            print(f"   Session: {session_id[:8]}... ({session_label})")
            print(f"   Modified: {modified.strftime('%Y-%m-%d %H:%M')}")
            print(f"   Messages: {msg_count}")
            print(f"   Size: {size_kb:.1f} KB")
            if subagent_info:
                print(subagent_info)
            print(f'   Preview: "{preview}..."')

        print("\n" + "=" * 80)
        return sessions[:limit]

    # ── Conversation extraction ────────────────────────────────────────

    def extract_conversation(
        self,
        jsonl_path: Path,
        detailed: bool = False,
        diff: bool = False,
        think: bool = False,
    ) -> List[Dict[str, str]]:
        """Extract conversation messages from a JSONL file."""
        conversation: List[Dict[str, str]] = []
        _last_assistant_idx = -1
        _pending_changes: Dict[str, list] = {}

        try:
            with open(jsonl_path, "r", encoding="utf-8") as f:
                for line in f:
                    try:
                        entry = json.loads(line.strip())

                        # Collect Write file changes from toolUseResult
                        if diff and "toolUseResult" in entry:
                            tur = entry["toolUseResult"]
                            if isinstance(tur, dict):
                                collect_write_change(tur, _pending_changes)

                        # User messages
                        if entry.get("type") == "user" and "message" in entry:
                            msg = entry["message"]
                            if isinstance(msg, dict) and msg.get("role") == "user":
                                text = extract_text_content(msg.get("content", ""))
                                if text and text.strip():
                                    if diff and _pending_changes and _last_assistant_idx >= 0:
                                        attach_file_changes(
                                            conversation, _last_assistant_idx, _pending_changes
                                        )
                                        _pending_changes = {}
                                    conversation.append(
                                        {
                                            "role": "user",
                                            "content": text,
                                            "timestamp": entry.get("timestamp", ""),
                                        }
                                    )

                        # Assistant messages
                        elif entry.get("type") == "assistant" and "message" in entry:
                            msg = entry["message"]
                            if isinstance(msg, dict) and msg.get("role") == "assistant":
                                # Collect file changes from tool_use blocks
                                if diff:
                                    for item in msg.get("content", []):
                                        if isinstance(item, dict) and item.get("type") == "tool_use":
                                            collect_file_change_from_tool_use(item, _pending_changes)

                                text = extract_text_content(
                                    msg.get("content", []), detailed=detailed, think=think
                                )
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
                                elif diff and _pending_changes:
                                    conversation.append(
                                        {
                                            "role": "assistant",
                                            "content": "",
                                            "timestamp": entry.get("timestamp", ""),
                                        }
                                    )
                                    _last_assistant_idx = len(conversation) - 1

                        # Detailed: tool use, tool results, system messages
                        elif detailed:
                            extract_detailed_entry(entry, conversation)

                    except json.JSONDecodeError:
                        continue
                    except Exception:
                        continue

            # Flush remaining file changes
            if diff and _pending_changes and _last_assistant_idx >= 0:
                attach_file_changes(
                    conversation, _last_assistant_idx, _pending_changes
                )

        except Exception as e:
            print(f"Error reading file {jsonl_path}: {e}")

        return conversation

    # ── Terminal display ───────────────────────────────────────────────

    def display_conversation(
        self, jsonl_path: Path, detailed: bool = False
    ) -> None:
        """Display a conversation in the terminal with pagination."""
        try:
            messages = self.extract_conversation(jsonl_path, detailed=detailed)
            if not messages:
                print("No messages found in conversation")
                return

            session_id = jsonl_path.stem

            print("\033[2J\033[H", end="")
            print("=" * 60)
            print(f"Viewing: {jsonl_path.parent.name}")
            print(f"Session: {session_id[:8]}...")

            first_timestamp = messages[0].get("timestamp", "")
            if first_timestamp:
                try:
                    dt = datetime.fromisoformat(
                        first_timestamp.replace("Z", "+00:00")
                    )
                    print(f"Date: {dt.strftime('%Y-%m-%d %H:%M:%S')}")
                except Exception:
                    pass

            print("=" * 60)
            print("Up/Down to scroll, Q to quit, Enter to continue\n")

            lines_shown = 8
            lines_per_page = 30

            role_labels = {
                "user": "HUMAN",
                "human": "HUMAN",
                "assistant": "CLAUDE",
                "tool_use": "TOOL USE",
                "tool_result": "TOOL RESULT",
                "system": "SYSTEM",
            }

            for msg in messages:
                role = msg["role"]
                content = msg["content"]
                label = role_labels.get(role, role.upper())

                if role in ("user", "human", "assistant"):
                    print(f"\n{'─' * 40}")
                    print(f"{label}:")
                    print(f"{'─' * 40}")
                else:
                    print(f"\n{label}:")

                lines = content.split("\n")
                max_lines = 50

                for line in lines[:max_lines]:
                    if len(line) > 100:
                        line = line[:97] + "..."
                    print(line)
                    lines_shown += 1

                    if lines_shown >= lines_per_page:
                        response = (
                            input("\n[Enter] Continue | [Q] Quit: ").strip().upper()
                        )
                        if response == "Q":
                            print("\nStopped viewing")
                            return
                        print("\033[2J\033[H", end="")
                        lines_shown = 0

                if len(lines) > max_lines:
                    print(f"... [{len(lines) - max_lines} more lines truncated]")
                    lines_shown += 1

            print("\n" + "=" * 60)
            print("End of conversation")
            print("=" * 60)
            input("\nPress Enter to continue...")

        except Exception as e:
            print(f"Error displaying conversation: {e}")
            input("\nPress Enter to continue...")

    # ── Save (delegates to formatters) ─────────────────────────────────

    def save_as_markdown(
        self,
        conversation: List[Dict[str, str]],
        session_id: str,
        filename_label: Optional[str] = None,
    ) -> Optional[Path]:
        return fmt.save_as_markdown(
            conversation, session_id, self.output_dir, filename_label
        )

    def save_as_json(
        self,
        conversation: List[Dict[str, str]],
        session_id: str,
        filename_label: Optional[str] = None,
    ) -> Optional[Path]:
        return fmt.save_as_json(
            conversation, session_id, self.output_dir, filename_label
        )

    def save_as_html(
        self,
        conversation: List[Dict[str, str]],
        session_id: str,
        filename_label: Optional[str] = None,
    ) -> Optional[Path]:
        return fmt.save_as_html(
            conversation, session_id, self.output_dir, filename_label
        )

    def save_conversation(
        self,
        conversation: List[Dict[str, str]],
        session_id: str,
        format: str = "markdown",
        filename_label: Optional[str] = None,
    ) -> Optional[Path]:
        return fmt.save_conversation(
            conversation, session_id, self.output_dir, format, filename_label
        )

    # ── Batch extraction ───────────────────────────────────────────────

    def extract_multiple(
        self,
        sessions: List[Path],
        indices: List[int],
        format: str = "markdown",
        detailed: bool = False,
        diff: bool = False,
        think: bool = False,
    ) -> Tuple[int, int]:
        """Extract multiple sessions by index."""
        success = 0
        total = len(indices)

        for idx in indices:
            if 0 <= idx < len(sessions):
                session_path = sessions[idx]
                conversation = self.extract_conversation(
                    session_path, detailed=detailed, diff=diff, think=think
                )
                if conversation:
                    output_path = self.save_conversation(
                        conversation, session_path.stem, format=format
                    )
                    success += 1
                    print(
                        f"{success}/{total}: {output_path.name} "
                        f"({len(conversation)} messages)"
                    )
                else:
                    print(f"Skipped session {idx + 1} (no conversation)")
            else:
                print(f"Invalid session number: {idx + 1}")

        return success, total

    def extract_session_with_subagents(
        self,
        session_path: Path,
        format: str = "markdown",
        detailed: bool = False,
        diff: bool = False,
        think: bool = False,
    ) -> int:
        """Extract a session and all its subagent conversations."""
        session_id = session_path.stem
        saved = 0

        # Main session
        print(f"\nMain session: {session_id}")
        conversation = self.extract_conversation(
            session_path, detailed=detailed, diff=diff, think=think
        )
        if conversation:
            output_path = self.save_conversation(
                conversation, session_id, format=format
            )
            if output_path:
                print(
                    f"   Saved {output_path.name} ({len(conversation)} messages)"
                )
                saved += 1
        else:
            print("   No conversation content")

        # Subagents
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

                agent_conv = self.extract_conversation(
                    agent_path, detailed=detailed, diff=diff, think=think
                )
                if agent_conv:
                    header = self._get_subagent_header(agent_path)
                    label = (
                        f"{session_id[:8]}-sub-{header}"
                        if header
                        else f"{session_id[:8]}-sub-{agent_id}"
                    )
                    agent_output = self.save_conversation(
                        agent_conv, agent_id, format=format, filename_label=label
                    )
                    if agent_output:
                        print(
                            f"      Saved {agent_output.name} ({len(agent_conv)} messages)"
                        )
                        saved += 1
                else:
                    print("      No conversation content")
        else:
            print("\n   (No subagents found for this session)")

        return saved

    # ── Backward-compatible static method references ────────────────────

    @staticmethod
    def _collect_file_change_from_tool_use(item: dict, pending: Dict[str, list]) -> None:
        collect_file_change_from_tool_use(item, pending)

    @staticmethod
    def _collect_write_change(tool_result: dict, pending: Dict[str, list]) -> None:
        collect_write_change(tool_result, pending)

    @staticmethod
    def _extract_detailed_entry(entry: dict, conversation: List[Dict[str, str]]) -> None:
        extract_detailed_entry(entry, conversation)

    @staticmethod
    def _attach_file_changes(conversation: List[Dict[str, str]], assistant_idx: int, changes: Dict[str, list]) -> None:
        attach_file_changes(conversation, assistant_idx, changes)

    @staticmethod
    def _format_file_changes(changes: Dict[str, list]) -> str:
        from .parser import format_file_changes
        return format_file_changes(changes)

    def _extract_text_content(self, content, detailed: bool = False, think: bool = False) -> str:
        return extract_text_content(content, detailed, think)
