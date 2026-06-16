#!/usr/bin/env python3
"""
Real-time thinking block renderer for Claude Code sessions.

Monitors a Claude Code session's jsonl file and prints thinking blocks
as they appear. Automatically follows to new session files when /clear
creates a new session in the same project directory.

Usage:
    extract --inspect 7c3e9f              # by UUID prefix
    extract --inspect peppy-twirling-wren  # by slug (auto-generated name)
    extract --inspect "gft resume wording" # by custom title (user-set via /rename)
    python think_realtime.py <identifier>
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from session_resolver import (
    get_claude_projects_dir,
    get_session_display_name,
    resolve_session,
)


# --- Configuration ---
POLL_INTERVAL = 0.3  # seconds between file reads
STALE_THRESHOLD = 10.0  # seconds without new data before checking for newer files
WAIT_MESSAGE_INTERVAL = 30.0  # seconds between "still waiting" messages


def get_project_dir_for_session(session_path: Path) -> Path:
    """
    Given a session jsonl path, return its project directory.

    Handles both layouts:
      ~/.claude/projects/<encoded-project>/<uuid>.jsonl
      ~/.claude/projects/<encoded-project>/<uuid>/<uuid>.jsonl
    """
    parent = session_path.parent
    if parent.name == session_path.stem:
        return parent.parent
    return parent


def find_newest_session_in_project(project_dir: Path, exclude: Optional[Path] = None) -> Optional[Path]:
    """
    Find the most recently modified jsonl in a project directory.
    Optionally exclude a specific file (the one we're already tailing).
    """
    newest = None
    newest_mtime = 0.0

    for jsonl_file in project_dir.iterdir():
        if not jsonl_file.suffix == ".jsonl":
            continue
        if exclude and jsonl_file == exclude:
            continue
        try:
            mtime = jsonl_file.stat().st_mtime
        except OSError:
            continue
        if mtime > newest_mtime:
            newest_mtime = mtime
            newest = jsonl_file

    return newest


def extract_thinking_from_line(line: str) -> list:
    """
    Parse a jsonl line and extract all thinking block texts.
    Returns a list of (thinking_text, timestamp) tuples.
    """
    try:
        obj = json.loads(line)
    except json.JSONDecodeError:
        return []

    # Only process assistant messages
    if obj.get("type") != "assistant":
        return []

    results = []
    timestamp = obj.get("timestamp", "")

    # Navigate to content blocks — tolerant of structure variations
    message = obj.get("message", {})
    content = message.get("content", [])

    if not isinstance(content, list):
        return []

    for block in content:
        if isinstance(block, dict) and block.get("type") == "thinking":
            thinking_text = block.get("thinking", "")
            if thinking_text:
                results.append((thinking_text, timestamp))

    return results


def format_timestamp(iso_timestamp: str) -> str:
    """Convert ISO timestamp to HH:MM:SS local time."""
    if not iso_timestamp:
        return datetime.now().strftime("%H:%M:%S")
    try:
        dt = datetime.fromisoformat(iso_timestamp.replace("Z", "+00:00"))
        local_dt = dt.astimezone()
        return local_dt.strftime("%H:%M:%S")
    except (ValueError, OSError):
        return datetime.now().strftime("%H:%M:%S")


def print_thinking(thinking_text: str, timestamp: str, display_name: str):
    """Print a thinking block with a header."""
    time_display = format_timestamp(timestamp)
    print(f"\n── thinking [{display_name}] {time_display} ──")
    print(thinking_text)
    print(f"── end ──")
    sys.stdout.flush()


def tail_session(session_path: Path) -> Optional[Path]:
    """
    Tail a session jsonl file, printing thinking blocks as they appear.

    Returns:
        None if user interrupted (Ctrl+C)
        Path to a new session file if a newer one was detected (e.g. after /clear)
    """
    session_id = session_path.stem
    project_dir = get_project_dir_for_session(session_path)
    display_name = get_session_display_name(session_path)

    print(f"Tailing: {display_name} ({session_id[:8]}...)")
    print(f"Project: {project_dir.name}")
    print(f"Poll: {POLL_INTERVAL}s | Ctrl+C to stop")
    print("─" * 60)
    sys.stdout.flush()

    # Seek to end of file
    file_size = session_path.stat().st_size if session_path.exists() else 0
    offset = file_size
    buffer = ""
    last_data_time = time.time()
    last_wait_message = time.time()

    while True:
        try:
            if not session_path.exists():
                time.sleep(POLL_INTERVAL)
                continue

            current_size = session_path.stat().st_size

            if current_size > offset:
                # New data available
                with open(session_path, "r", encoding="utf-8") as f:
                    f.seek(offset)
                    new_data = f.read()
                    offset = f.tell()

                last_data_time = time.time()
                buffer += new_data

                # Process complete lines (handle half-line buffering)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue

                    results = extract_thinking_from_line(line)
                    for thinking_text, timestamp in results:
                        print_thinking(thinking_text, timestamp, display_name)

            elif current_size < offset:
                # File was truncated — reset
                offset = 0
                buffer = ""

            # Check for newer session file if stale
            elapsed_since_data = time.time() - last_data_time
            if elapsed_since_data > STALE_THRESHOLD:
                newer = find_newest_session_in_project(project_dir, exclude=session_path)
                if newer and newer.stat().st_mtime > session_path.stat().st_mtime:
                    new_name = get_session_display_name(newer)
                    print(f"\n── session switch ──")
                    print(f"New session: {new_name} ({newer.stem[:8]}...)")
                    print(f"── following ──")
                    sys.stdout.flush()
                    return newer

                # Periodic heartbeat
                # if time.time() - last_wait_message > WAIT_MESSAGE_INTERVAL:
                #     last_wait_message = time.time()
                #     now = datetime.now().strftime("%H:%M:%S")
                #     print(f"[{now}] waiting...", file=sys.stderr)
                #     sys.stderr.flush()

            time.sleep(POLL_INTERVAL)

        except KeyboardInterrupt:
            return None


def wait_for_session(identifier: str) -> Optional[Path]:
    """
    Wait for a session matching the identifier to appear.
    Polls every second.
    """
    projects_dir = get_claude_projects_dir()
    print(f"No session matching '{identifier}' found.")
    print(f"Watching {projects_dir} ...")
    print("Press Ctrl+C to stop.")
    sys.stdout.flush()

    while True:
        try:
            match = resolve_session(identifier)
            if match:
                name = get_session_display_name(match)
                print(f"\nFound: {name} ({match.stem[:8]}...)")
                return match
            time.sleep(1.0)
        except KeyboardInterrupt:
            return None


def main():
    """Entry point for --think mode."""
    if len(sys.argv) < 2:
        print("Usage: think_realtime.py <identifier>")
        print("       extract --inspect <identifier>")
        print()
        print("  identifier: UUID prefix (>= 6 hex chars), session slug, or custom title")
        print()
        print("Examples:")
        print("  extract -t 7c3e9f                  # UUID prefix")
        print("  extract -t peppy-twirling-wren      # slug (auto-generated)")
        print('  extract -t "gft resume wording"     # custom title (/rename)')
        sys.exit(1)

    identifier = sys.argv[1]

    if len(identifier) < 3:
        print(f"Error: identifier too short (got {len(identifier)}: '{identifier}')")
        sys.exit(1)

    # Resolve session
    session_path = resolve_session(identifier)

    if session_path is None:
        session_path = wait_for_session(identifier)
        if session_path is None:
            print("\nAborted.")
            sys.exit(0)

    # Main loop: tail session, follow to new sessions on /clear
    while session_path is not None:
        session_path = tail_session(session_path)

    print("\nDone.")


if __name__ == "__main__":
    main()
