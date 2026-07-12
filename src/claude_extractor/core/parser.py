"""
JSONL content extraction and file change handling.

Extracts text from Claude Code JSONL entries (various content formats),
collects file change information (Edit/Write tool use), and formats
change blocks for output.
"""

import json
from typing import Dict, List, Optional


def extract_text_content(
    content, detailed: bool = False, think: bool = False, cmd: bool = False
) -> str:
    """Extract text from various content formats Claude uses.

    Args:
        content: String or list of content blocks from JSONL entry.
        detailed: Include tool_use blocks in output.
        think: Include thinking blocks (wrapped in markdown comments).
        cmd: Include Bash tool_use blocks formatted as description + inline code.
    """
    if isinstance(content, str):
        return content
    elif isinstance(content, list):
        text_parts = []
        for item in content:
            if isinstance(item, dict):
                if item.get("type") == "thinking" and think:
                    thinking_text = item.get("thinking", "")
                    if thinking_text:
                        text_parts.append(f"<!-- {thinking_text} -->")
                elif item.get("type") == "text":
                    text_parts.append(item.get("text", ""))
                elif item.get("type") == "tool_use":
                    if cmd and item.get("name") == "Bash":
                        text_parts.append(_format_bash_command(item))
                    elif detailed:
                        tool_name = item.get("name", "unknown")
                        tool_input = item.get("input", {})
                        text_parts.append(f"\nUsing tool: {tool_name}")
                        text_parts.append(
                            f"Input: {json.dumps(tool_input, indent=2, ensure_ascii=False)}\n"
                        )
        return "\n".join(text_parts)
    else:
        return str(content)


def _format_bash_command(item: dict) -> str:
    """Format a Bash tool_use block as description + code.

    Short commands (<=30 chars): inline backticks.
    Long commands (>30 chars): fenced code block on next line.
    """
    inp = item.get("input", {})
    command = inp.get("command", "")
    description = inp.get("description", "")

    if len(command) <= 30:
        code = f"`{command}`"
    else:
        code = f"```\n{command}\n```"

    if description:
        return f'Command to "{description}":\n{code}'
    else:
        return code


def extract_detailed_entry(
    entry: dict, conversation: List[Dict[str, str]]
) -> None:
    """Extract tool use, tool result, or system entries (detailed mode).

    Appends the extracted entry to *conversation* in-place.
    """
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


# ── File change collection ───────────────────────────────────────────


def collect_file_change_from_tool_use(
    item: dict, pending: Dict[str, list]
) -> None:
    """Accumulate an Edit file change from an assistant tool_use block.

    Edit tool input has: file_path, old_string, new_string.
    Write operations are handled separately via collect_write_change.
    """
    if item.get("name") != "Edit":
        return
    inp = item.get("input", {})
    fp = inp.get("file_path", "")
    if fp and "old_string" in inp and "new_string" in inp:
        pending.setdefault(fp, []).append((inp["old_string"], inp["new_string"]))


def collect_write_change(tool_result: dict, pending: Dict[str, list]) -> None:
    """Accumulate a Write file change from a toolUseResult entry.

    toolUseResult for Write has:
      type="create" + filePath + content          -> new file
      type="update" + filePath + structuredPatch   -> overwrite (has diff)
    Edit toolUseResults (have oldString/newString) are skipped here
    because they are already collected from tool_use blocks.
    """
    if "oldString" in tool_result:
        return

    result_type = tool_result.get("type", "")
    fp = tool_result.get("filePath", "")
    if not fp or result_type not in ("create", "update"):
        return

    if result_type == "create":
        # Only record the path — content is intentionally omitted.
        # When this file is later edited, the Edit tool's old_string
        # will naturally show the relevant "before" content in the diff.
        pending.setdefault(fp, []).append(None)
    elif result_type == "update":
        patch = tool_result.get("structuredPatch", [])
        if not isinstance(patch, list) or not patch:
            return
        for hunk in patch:
            before = []
            after = []
            for line in hunk.get("lines", []):
                if line.startswith("-"):
                    before.append(line[1:])
                elif line.startswith("+"):
                    after.append(line[1:])
            if before or after:
                pending.setdefault(fp, []).append(
                    ("\n".join(before), "\n".join(after))
                )


def attach_file_changes(
    conversation: List[Dict[str, str]],
    assistant_idx: int,
    changes: Dict[str, list],
) -> None:
    """Append a formatted file-changes block to an assistant message."""
    if assistant_idx < 0 or assistant_idx >= len(conversation):
        return
    block = format_file_changes(changes)
    if block:
        conversation[assistant_idx]["content"] += "\n\n" + block


def _indent(text: str, prefix: str = "    ") -> str:
    """Add *prefix* to every line of *text*."""
    return "\n".join(prefix + line for line in text.split("\n"))


def format_file_changes(changes: Dict[str, list]) -> str:
    """Render collected file changes into a markdown block.

    Output format (designed for readable markdown rendering):
    - ``#### File Changes:`` heading separates diff from message text.
    - Code blocks are 4-space indented to visually distinguish from messages.
    - **Before** / **After** labels are bold.
    """
    if not changes:
        return ""

    parts = ["#### File Changes:"]
    file_num = 0
    for fp, edits in changes.items():
        file_num += 1
        is_all_new = all(e is None for e in edits)
        if is_all_new:
            parts.append(f"{file_num}. New file: {fp}")
        else:
            parts.append(f"{file_num}. {fp}")
            real_edits = [e for e in edits if e is not None]
            if real_edits:
                old_blocks = [old for old, _ in real_edits]
                new_blocks = [new for _, new in real_edits]
                divider = "\n----------\n"
                parts.append("**Before**:")
                parts.append(_indent("```"))
                parts.append(_indent(divider.join(old_blocks)))
                parts.append(_indent("```"))
                parts.append("**After**:")
                parts.append(_indent("```"))
                parts.append(_indent(divider.join(new_blocks)))
                parts.append(_indent("```"))
    return "\n".join(parts)
