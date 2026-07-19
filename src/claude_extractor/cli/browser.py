"""
Interactive message browser for a single Claude session.

Stages (continuing from listing.py's project/session selection):

Stage 3 -- Show each message's preview (first 3 lines), prompt user
           to select message IDs to view in full.
Stage 4 -- Show selected messages in full, prompt user to select
           IDs to copy to clipboard (or write to file with -o).

Controls:
- Enter: confirm typed input
- Esc: go back to previous stage
"""

import os
import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..session.loader import load_messages
from ..session.resolver import get_session_display_name


# ── Terminal input (Esc-aware) ─────────────────────────────────────


def _read_line(prompt: str) -> Optional[str]:
    """Read a line with Esc support.

    Returns the typed string on Enter, or None on Esc.
    On Windows uses msvcrt for character-by-character reading.
    On other platforms falls back to input() (Ctrl-C = back).
    """
    if sys.platform == "win32":
        return _read_line_win(prompt)
    # Fallback: regular input, KeyboardInterrupt = go back
    try:
        result = input(prompt)
        return result
    except (EOFError, KeyboardInterrupt):
        print()
        return None


def _read_line_win(prompt: str) -> Optional[str]:
    """Windows: read line char-by-char via msvcrt, Esc returns None."""
    import msvcrt

    sys.stdout.write(prompt)
    sys.stdout.flush()
    buf = []

    while True:
        ch = msvcrt.getwch()

        if ch == "\x1b":  # Esc
            # Clear the line visually
            sys.stdout.write("\r" + " " * (len(prompt) + len(buf)) + "\r")
            sys.stdout.flush()
            return None

        if ch == "\r":  # Enter
            sys.stdout.write("\n")
            sys.stdout.flush()
            return "".join(buf)

        if ch == "\x08":  # Backspace
            if buf:
                buf.pop()
                sys.stdout.write("\b \b")
                sys.stdout.flush()
            continue

        if ch == "\x03":  # Ctrl-C
            sys.stdout.write("\n")
            sys.stdout.flush()
            return None

        # Normal printable character
        if ch.isprintable():
            buf.append(ch)
            sys.stdout.write(ch)
            sys.stdout.flush()


# ── Pager ──────────────────────────────────────────────────────────


def _pager(text: str) -> None:
    """Display text in system pager (less/more). Scroll with Enter, quit with q.

    Tries ``less -R`` first (available in Git Bash on Windows), then
    falls back to ``more``, then to a simple built-in pager.
    """
    # Try less, then more
    for cmd in (["less", "-R"], ["more"]):
        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
            )
            proc.communicate(input=text)
            if proc.returncode is not None:
                return
        except (FileNotFoundError, OSError):
            continue

    # Built-in fallback: page line by line
    _builtin_pager(text)


def _builtin_pager(text: str) -> None:
    """Simple built-in pager: shows one screenful at a time."""
    lines = text.split("\n")
    height = shutil.get_terminal_size().lines - 2

    for i in range(0, len(lines), height):
        chunk = lines[i:i + height]
        sys.stdout.write("\n".join(chunk) + "\n")
        if i + height < len(lines):
            sys.stdout.write(":")
            sys.stdout.flush()
            # Wait for key
            if sys.platform == "win32":
                import msvcrt
                ch = msvcrt.getwch()
                sys.stdout.write("\r \r")
                if ch.lower() == "q":
                    return
            else:
                try:
                    ch = input()
                    if ch.lower() == "q":
                        return
                except (EOFError, KeyboardInterrupt):
                    return


# ── Clipboard ──────────────────────────────────────────────────────


def _copy_to_clipboard(text: str) -> bool:
    """Copy text to system clipboard.  Returns True on success."""
    try:
        if platform.system() == "Windows":
            proc = subprocess.run(
                ["clip"], input=text.encode("utf-16-le"),
                creationflags=subprocess.CREATE_NO_WINDOW,
            )
            return proc.returncode == 0
        elif platform.system() == "Darwin":
            proc = subprocess.run(
                ["pbcopy"], input=text.encode("utf-8"),
            )
            return proc.returncode == 0
        else:
            # Try xclip, then xsel
            for cmd in [["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]:
                try:
                    proc = subprocess.run(cmd, input=text.encode("utf-8"))
                    if proc.returncode == 0:
                        return True
                except FileNotFoundError:
                    continue
            return False
    except Exception:
        return False


# ── Formatting helpers ──────────────────────────────────────────────


def _role_label(role: str) -> str:
    return "User" if role == "user" else "Claude"


def _separator(width: int = 60) -> str:
    return "=" * min(width, shutil.get_terminal_size().columns)


def _print_preview(msg: Dict) -> None:
    """Print a single message preview block (ID + first/last 3 lines)."""
    label = _role_label(msg["role"])
    print(f"\nID {msg['id']}: {label}")
    for line in msg["preview"].split("\n"):
        print(f"   {line}")


# ── Input parsing ───────────────────────────────────────────────────


def _parse_ids(text: str, max_id: int) -> Optional[List[int]]:
    """Parse space-separated message IDs.  Returns None on invalid input."""
    parts = text.strip().split()
    if not parts:
        return None
    ids = []
    for p in parts:
        try:
            n = int(p)
        except ValueError:
            return None
        if n < 1 or n > max_id:
            print(f"  ID {n} out of range (1-{max_id})")
            return None
        ids.append(n)
    return ids


def _parse_extract_input(text: str, max_id: int) -> Tuple[Optional[List[int]], Optional[str]]:
    """Parse extract input: ``<id> [<id> ...] [-o <dir>]``.

    Returns (ids, output_dir).  output_dir is None when -o is absent
    (meaning: copy to clipboard).
    """
    parts = text.strip().split()
    if not parts:
        return None, None

    output_dir = None
    id_parts = []

    i = 0
    while i < len(parts):
        if parts[i] == "-o" and i + 1 < len(parts):
            output_dir = parts[i + 1]
            i += 2
        else:
            id_parts.append(parts[i])
            i += 1

    if not id_parts:
        return None, output_dir

    ids = []
    for p in id_parts:
        try:
            n = int(p)
        except ValueError:
            print(f"  Invalid ID: {p}")
            return None, output_dir
        if n < 1 or n > max_id:
            print(f"  ID {n} out of range (1-{max_id})")
            return None, output_dir
        ids.append(n)

    return ids, output_dir


# ── Message rendering (full) ───────────────────────────────────────


def _render_full_message(msg: Dict) -> str:
    """Render one message in the same format as extract markdown output."""
    label = _role_label(msg["role"])
    header = f"## {label}"
    return f"{header}\n\n{msg['content']}\n\n---\n"


def _render_full_message_with_id(msg: Dict) -> str:
    """Render one message with its ID prefixed to the header."""
    label = _role_label(msg["role"])
    header = f"## ID {msg['id']}: {label}"
    return f"{header}\n\n{msg['content']}\n\n---\n"


# ── Stage 3: message list ──────────────────────────────────────────


def _stage_message_list(messages: List[Dict]) -> Optional[List[int]]:
    """Show previews of all messages, return selected IDs to view."""
    print(f"\n{len(messages)} messages:\n")
    print(_separator())

    for msg in messages:
        _print_preview(msg)

    max_id = messages[-1]["id"]

    print("\n" + _separator())
    while True:
        choice = _read_line(
            f"\nSelect message IDs to view (1-{max_id}, space separated) [Esc=back]: "
        )
        if choice is None:
            return None
        if not choice.strip():
            continue

        ids = _parse_ids(choice, max_id)
        if ids is not None:
            return ids


# ── Stage 4: full message view + extract ───────────────────────────


def _stage_message_view(
    messages: List[Dict],
    selected_ids: List[int],
    session_path: Path,
) -> None:
    """Show selected messages in pager, then offer extraction to clipboard or file."""
    msg_by_id = {m["id"]: m for m in messages}
    max_id = messages[-1]["id"]

    # Render selected messages and show in pager
    parts = []
    for mid in selected_ids:
        msg = msg_by_id.get(mid)
        if msg:
            parts.append(_render_full_message_with_id(msg))
    _pager("\n".join(parts))

    # Extract prompt
    while True:
        choice = _read_line(
            f"\nCopy message IDs (1-{max_id}, space separated) "
            "to clipboard, or -o <dir> to file [Esc=back]: "
        )
        if choice is None:
            return
        if not choice.strip():
            continue

        ids, output_dir = _parse_extract_input(choice, max_id)
        if ids is None:
            continue

        selected_msgs = [msg_by_id[i] for i in ids if i in msg_by_id]
        if output_dir:
            _save_messages_to_file(selected_msgs, session_path, output_dir)
        else:
            _copy_messages_to_clipboard(selected_msgs)
        return


def _copy_messages_to_clipboard(messages: List[Dict]) -> None:
    """Render and copy selected messages to system clipboard."""
    if not messages:
        print("  No messages to copy.")
        return

    parts = []
    for msg in messages:
        parts.append(_render_full_message(msg))

    text = "\n".join(parts)
    if _copy_to_clipboard(text):
        print(f"  Copied {len(messages)} message(s) to clipboard.")
    else:
        print("  Failed to copy to clipboard.")


def _save_messages_to_file(
    messages: List[Dict],
    session_path: Path,
    output_dir: str,
) -> None:
    """Write selected messages to a markdown file."""
    if not messages:
        print("  No messages to extract.")
        return

    display_name = get_session_display_name(session_path)
    safe_name = "".join(c if c.isalnum() or c in "-_ " else "-" for c in display_name)
    safe_name = safe_name.strip("-_ ").replace(" ", "-")
    while "--" in safe_name:
        safe_name = safe_name.replace("--", "-")
    if not safe_name:
        safe_name = session_path.stem[:6]
    filename = f"{safe_name}.md"

    out_path = Path(output_dir)
    out_path.mkdir(parents=True, exist_ok=True)
    filepath = out_path / filename

    with open(filepath, "w", encoding="utf-8") as f:
        for msg in messages:
            f.write(_render_full_message(msg))
            f.write("\n")

    print(f"  Saved {len(messages)} message(s) to: {filepath}")


# ── Public entry point ──────────────────────────────────────────────


def browse_session(session_path: Path) -> None:
    """Interactive 2-stage message browser for a single session.

    Called after listing.py selects a session (stages 1-2).
    Stage 3: preview list -> Stage 4: full view + copy/save.
    After extraction completes, exits back to session list.
    Esc at any prompt goes back one level.
    """
    display_name = get_session_display_name(session_path)
    print(f"\nLoading messages for: {display_name} ...")

    messages = load_messages(session_path)
    if not messages:
        print("No messages found in this session.")
        return

    # Stage 3 -> Stage 4 loop (Esc in stage 3 exits to session list)
    while True:
        selected_ids = _stage_message_list(messages)
        if selected_ids is None:
            return

        _stage_message_view(messages, selected_ids, session_path)
        # After stage 4 (extract done or Esc), exit entirely
        return
