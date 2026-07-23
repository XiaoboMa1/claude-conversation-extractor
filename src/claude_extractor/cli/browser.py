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
import unicodedata
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ..session.loader import load_messages
from ..session.resolver import get_session_display_name


# ── ANSI color constants ──────────────────────────────────────────

_C_RESET = "\033[0m"
_C_BOLD = "\033[1m"
_C_DIM = "\033[2m"
_C_CYAN = "\033[36m"
_C_YELLOW = "\033[33m"
_C_GREEN = "\033[32m"
_C_MAGENTA = "\033[35m"


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

        if ch in ("\x00", "\xe0"):  # Arrow / function key prefix
            msvcrt.getwch()  # consume scan code byte
            continue

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


# ── VT/ANSI support ──────────────────────────────────────────────


def _enable_vt_processing() -> bool:
    """Enable VT/ANSI escape processing on Windows console.

    Required for cursor positioning and alternate screen buffer.
    Always returns True on non-Windows (ANSI natively supported).
    """
    if sys.platform != "win32":
        return True
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
        mode = ctypes.c_ulong()
        if not kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            return False
        mode.value |= 0x0004  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
        return bool(kernel32.SetConsoleMode(handle, mode))
    except Exception:
        return False


# ── Display width / line wrapping ─────────────────────────────────


def _display_width(text: str) -> int:
    """Terminal display width accounting for wide (CJK) characters.

    East Asian Fullwidth/Wide characters occupy 2 columns;
    all others occupy 1.  ANSI escape sequences are zero-width.
    """
    w = 0
    i = 0
    n = len(text)
    while i < n:
        # Skip ANSI escape sequences (\033[...m)
        if text[i] == "\033" and i + 1 < n and text[i + 1] == "[":
            i += 2
            while i < n and text[i] != "m":
                i += 1
            i += 1  # skip 'm'
            continue
        if unicodedata.east_asian_width(text[i]) in ("F", "W"):
            w += 2
        else:
            w += 1
        i += 1
    return w


def _wrap_lines_for_display(lines: list, width: int) -> list:
    """Wrap logical lines into display rows that fit *width* columns.

    Accounts for CJK double-width characters and ANSI escape
    sequences (zero-width, state carried across line breaks).
    Tabs are expanded to 4 spaces before wrapping.
    """
    if width <= 0:
        return list(lines)
    wrapped: list = []
    for line in lines:
        line = line.expandtabs(4)
        if not line:
            wrapped.append("")
            continue
        dw = _display_width(line)
        if dw <= width:
            wrapped.append(line)
            continue
        # ANSI-aware character-by-character wrap
        row: list = []
        row_w = 0
        active_codes: list = []  # ANSI codes in effect for continuation
        i = 0
        n = len(line)
        while i < n:
            # ANSI escape sequence? pass through at zero width
            if line[i] == "\033" and i + 1 < n and line[i + 1] == "[":
                j = i + 2
                while j < n and line[j] != "m":
                    j += 1
                if j < n:
                    j += 1
                code = line[i:j]
                row.append(code)
                if code == "\033[0m":
                    active_codes.clear()
                else:
                    active_codes.append(code)
                i = j
                continue
            ch = line[i]
            ch_w = 2 if unicodedata.east_asian_width(ch) in ("F", "W") else 1
            if row_w + ch_w > width:
                row.append("\033[0m")
                wrapped.append("".join(row))
                row = list(active_codes) + [ch]
                row_w = ch_w
            else:
                row.append(ch)
                row_w += ch_w
            i += 1
        if row:
            wrapped.append("".join(row))
    return wrapped


# ── Pager ──────────────────────────────────────────────────────────


def _pager(text: str) -> None:
    """Display text in a scrollable pager.

    Tries ``less -R`` first (supports arrow keys, search, etc.).
    On non-Windows, also tries ``more``.
    Falls back to a built-in scrollable pager with arrow key support.
    """
    # Try less first (best option on all platforms)
    try:
        proc = subprocess.Popen(
            ["less", "-R"],
            stdin=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
        )
        proc.communicate(input=text)
        if proc.returncode == 0:
            return
        # less exited with error — fall through to built-in pager
    except (FileNotFoundError, OSError):
        pass

    # On non-Windows, try more (Windows more lacks arrow key support)
    if sys.platform != "win32":
        try:
            proc = subprocess.Popen(
                ["more"],
                stdin=subprocess.PIPE,
                encoding="utf-8",
                errors="replace",
            )
            proc.communicate(input=text)
            if proc.returncode == 0:
                return
        except (FileNotFoundError, OSError):
            pass

    _builtin_pager(text)


def _builtin_pager(text: str) -> None:
    """Built-in pager with arrow key scrolling on Windows.

    Uses ANSI escape codes and alternate screen buffer for
    full-screen rendering.  Falls back to a simple page-by-page
    display when ANSI support is unavailable.
    """
    lines = text.split("\n")
    total = len(lines)
    height = shutil.get_terminal_size().lines - 1  # -1 for status bar

    if total <= height:
        sys.stdout.write(text + "\n")
        return

    if sys.platform == "win32" and _enable_vt_processing():
        _scrollable_pager_win(lines)
    else:
        _simple_pager(lines)


def _scrollable_pager_win(lines: list) -> None:
    """Windows full-screen scrollable pager.

    Uses alternate screen buffer and msvcrt for raw key input.
    Long lines are wrapped to terminal width (CJK-aware).
    Controls: Up/Down arrows, PgUp/PgDn, Home/End, Space, Enter,
    b (page up), q or Esc to quit.
    """
    import msvcrt

    raw_lines = lines          # keep originals for re-wrapping on resize
    display_lines: list = []
    last_width = 0
    offset = 0

    # Enter alternate screen buffer, hide cursor
    sys.stdout.write("\033[?1049h\033[?25l")
    sys.stdout.flush()

    try:
        while True:
            # Re-read terminal size each frame (handles resize)
            term = shutil.get_terminal_size()
            height = max(1, term.lines - 1)
            width = term.columns

            # Re-wrap when terminal width changes
            if width != last_width:
                display_lines = _wrap_lines_for_display(raw_lines, width)
                last_width = width

            total = len(display_lines)
            max_offset = max(0, total - height)
            offset = min(offset, max_offset)

            _draw_pager_frame(display_lines, offset, height, width, total)

            ch = msvcrt.getwch()

            if ch in ("\x00", "\xe0"):
                scan = msvcrt.getwch()
                if scan == "H":        # Up arrow
                    offset = max(0, offset - 1)
                elif scan == "P":      # Down arrow
                    offset = min(max_offset, offset + 1)
                elif scan == "I":      # Page Up
                    offset = max(0, offset - height)
                elif scan == "Q":      # Page Down
                    offset = min(max_offset, offset + height)
                elif scan == "G":      # Home
                    offset = 0
                elif scan == "O":      # End
                    offset = max_offset
            elif ch.lower() == "q" or ch == "\x1b":
                break
            elif ch == " ":            # Space = page down
                offset = min(max_offset, offset + height)
            elif ch == "\r":           # Enter = line down
                offset = min(max_offset, offset + 1)
            elif ch == "b":            # b = page up (less-style)
                offset = max(0, offset - height)
            elif ch == "\x03":         # Ctrl-C
                break
    finally:
        # Restore: show cursor, leave alternate screen
        sys.stdout.write("\033[?25h\033[?1049l")
        sys.stdout.flush()


def _draw_pager_frame(
    lines: list, offset: int, height: int, width: int, total: int,
) -> None:
    """Render one frame of the scrollable pager.

    Uses absolute cursor positioning (``ESC[row;1H``) to avoid
    newline/wrap edge cases with full-width lines.
    Lines are pre-wrapped by the caller — no truncation here.
    """
    end = min(offset + height, total)
    for row, i in enumerate(range(offset, end)):
        sys.stdout.write(f"\033[{row + 1};1H")  # 1-based row
        sys.stdout.write(lines[i])
        sys.stdout.write("\033[K")  # clear to end of line

    # Fill remaining rows with ~
    for row in range(end - offset, height):
        sys.stdout.write(f"\033[{row + 1};1H~\033[K")

    # Status bar in reverse video
    if offset + height >= total:
        pos = "(END)"
    else:
        pct = (offset + height) * 100 // total
        pos = f"{pct}%"

    bar = f" {pos} \u2014 \u2191\u2193 scroll  PgUp/PgDn page  q quit "
    sys.stdout.write(
        f"\033[{height + 1};1H\033[7m{bar[:width].ljust(width)}\033[0m"
    )
    sys.stdout.flush()


def _simple_pager(lines: list) -> None:
    """Fallback page-by-page pager when ANSI support is unavailable."""
    height = shutil.get_terminal_size().lines - 2

    for i in range(0, len(lines), height):
        chunk = lines[i : i + height]
        sys.stdout.write("\n".join(chunk) + "\n")
        if i + height < len(lines):
            sys.stdout.write(":")
            sys.stdout.flush()
            if sys.platform == "win32":
                import msvcrt
                while True:
                    ch = msvcrt.getwch()
                    if ch in ("\x00", "\xe0"):
                        msvcrt.getwch()
                        continue
                    break
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
    """Show previews of all messages in pager, return selected IDs."""
    max_id = messages[-1]["id"]

    # Build preview text for pager (with colored ID / role labels)
    parts = [f"{len(messages)} messages:\n", _separator()]
    for msg in messages:
        label = _role_label(msg["role"])
        role_color = _C_GREEN if msg["role"] == "user" else _C_MAGENTA
        parts.append(
            f"\n{_C_BOLD}ID {msg['id']}{_C_RESET}: "
            f"{role_color}{label}{_C_RESET}"
        )
        for line in msg["preview"].split("\n"):
            parts.append(f"   {line}")
    parts.append("\n" + _separator())

    _pager("\n".join(parts))

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


def browse_session(session_paths: List[Path]) -> None:
    """Interactive message browser for one or more sessions.

    Called after listing.py or search.py selects sessions.
    Loads and merges messages from all paths with sequential IDs.
    Stage 3: preview list -> Stage 4: full view + copy/save.
    Esc at any prompt goes back one level.
    """
    if len(session_paths) == 1:
        display_name = get_session_display_name(session_paths[0])
        print(f"\nLoading messages for: {display_name} ...")
    else:
        print(f"\nLoading messages from {len(session_paths)} session(s) ...")

    all_messages: List[Dict] = []
    next_id = 1
    for path in session_paths:
        msgs = load_messages(path)
        for msg in msgs:
            msg["id"] = next_id
            next_id += 1
            all_messages.append(msg)

    if not all_messages:
        print("No messages found in selected session(s).")
        return

    # Stage 3 -> Stage 4 loop
    # Esc in stage 3 → back to caller (stage 2 session list)
    # Esc or completion in stage 4 → back to stage 3
    while True:
        selected_ids = _stage_message_list(all_messages)
        if selected_ids is None:
            return

        _stage_message_view(all_messages, selected_ids, session_paths[0])
