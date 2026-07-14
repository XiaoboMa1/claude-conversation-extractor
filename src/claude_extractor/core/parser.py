"""
JSONL content extraction and file change handling.

Extracts text from Claude Code JSONL entries (various content formats),
collects file change information (Edit/Write tool use), and formats
change blocks for output.
"""

import json
import re
from typing import Dict, List, Optional


def extract_text_content(
    content, detailed: bool = False, think: bool = False,
    cmdin: bool = False, cmdout: bool = False,
) -> str:
    """Extract text from various content formats Claude uses.

    Args:
        content: String or list of content blocks from JSONL entry.
        detailed: Include tool_use blocks in output.
        think: Include thinking blocks (wrapped in markdown comments).
        cmdin: Include Bash tool_use blocks formatted as description + inline code.
        cmdout: Like cmdin, but also insert a placeholder for command output
            (filled later by attach_bash_output).
    """
    if isinstance(content, str):
        if content.lstrip().startswith("<task-notification>"):
            return ""
        return content
    elif isinstance(content, list):
        show_cmd = cmdin or cmdout
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
                    if show_cmd and item.get("name") == "Bash":
                        text_parts.append(
                            _format_bash_command(item, output_placeholder=cmdout)
                        )
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


def _format_bash_command(item: dict, output_placeholder: bool = False) -> str:
    """Format a Bash tool_use block as an indented fenced code block.

    Layout (indented 4 spaces to distinguish from conversation text)::

        Command to "description":
            ```
            command text
            ------ output ------   ← only when output is filled later
            output lines …
            ```

    When *output_placeholder* is True a ``<!--CMDOUT:id-->`` marker is
    inserted inside the fence so that ``attach_bash_output`` can replace
    it with the real output later.
    """
    inp = item.get("input", {})
    command = inp.get("command", "")
    description = inp.get("description", "")

    parts = [_indent("```")]
    parts.append(_indent(command))
    if output_placeholder:
        tool_id = item.get("id", "")
        if tool_id:
            parts.append(f"<!--CMDOUT:{tool_id}-->")
    parts.append(_indent("```"))

    block = "\n".join(parts)
    if description:
        return f'Command to "{description}":\n{block}'
    return block


# ── Bash command output (--cmdout) ───────────────────────────────────


def _format_output_text(stdout: str, stderr: str = "") -> str:
    """Merge stdout/stderr into cleaned text.  Returns "" when empty."""
    output = stdout or ""
    if stderr:
        output = (output + "\n" + stderr) if output else stderr
    if not output.strip():
        return ""
    return output.replace("\r\n", "\n").rstrip()


def attach_bash_output(
    conversation: List[Dict[str, str]],
    assistant_idx: int,
    tool_use_id: str,
    stdout: str,
    stderr: str = "",
) -> None:
    """Replace a ``<!--CMDOUT:id-->`` placeholder with the actual output.

    The output is rendered as an indented ``------ output ------`` block
    inside the same fenced region as the command.
    """
    if assistant_idx < 0 or assistant_idx >= len(conversation):
        return
    placeholder = f"<!--CMDOUT:{tool_use_id}-->"
    msg = conversation[assistant_idx]
    if placeholder not in msg["content"]:
        return

    output = _format_output_text(stdout, stderr)
    if output:
        replacement = _indent("------ output ------") + "\n" + _indent(output)
        msg["content"] = msg["content"].replace(placeholder, replacement)
    else:
        # No output — remove placeholder line cleanly
        msg["content"] = msg["content"].replace(placeholder + "\n", "")
        msg["content"] = msg["content"].replace(placeholder, "")


def cleanup_cmdout_placeholders(conversation: List[Dict[str, str]]) -> None:
    """Remove any remaining unfilled ``<!--CMDOUT:...-->`` placeholders."""
    pattern = re.compile(r"<!--CMDOUT:.*?-->\n?")
    for msg in conversation:
        msg["content"] = pattern.sub("", msg["content"])


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
        pending.setdefault(fp, []).append({"old": inp["old_string"], "new": inp["new_string"]})


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
                edit = {"old": "\n".join(before), "new": "\n".join(after)}
                old_start = hunk.get("oldStart")
                old_lines = hunk.get("oldLines")
                new_start = hunk.get("newStart")
                new_lines = hunk.get("newLines")
                if old_start is not None:
                    edit["old_start"] = old_start
                    if old_lines is not None:
                        edit["old_end"] = old_start + old_lines - 1
                if new_start is not None:
                    edit["new_start"] = new_start
                    if new_lines is not None:
                        edit["new_end"] = new_start + new_lines - 1
                pending.setdefault(fp, []).append(edit)


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


def _line_range_label(edit: dict, key_prefix: str) -> str:
    """Build a line-range suffix like ' (line 5 to 12)' if info is available."""
    start = edit.get(f"{key_prefix}_start")
    end = edit.get(f"{key_prefix}_end")
    if start is not None and end is not None:
        return f" (line {start} to {end})"
    elif start is not None:
        return f" (line {start})"
    return ""


def format_file_changes(changes: Dict[str, list]) -> str:
    """Render collected file changes into a markdown block.

    Each edited section within a file is shown as a paired Before/After block.
    Sections are separated by ``----------``.
    Line numbers are included when available (Write-update via structuredPatch).
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
            for i, edit in enumerate(real_edits):
                old_text = edit["old"]
                new_text = edit["new"]
                before_label = f"**Before{_line_range_label(edit, 'old')}**:"
                after_label = f"**After{_line_range_label(edit, 'new')}**:"
                parts.append(before_label)
                parts.append(_indent("```"))
                parts.append(_indent(old_text))
                parts.append(_indent("```"))
                parts.append(after_label)
                parts.append(_indent("```"))
                parts.append(_indent(new_text))
                parts.append(_indent("```"))
                if i < len(real_edits) - 1:
                    parts.append("----------")
    return "\n".join(parts)
