#!/usr/bin/env python3
"""
Fixed real-time search interface for Claude Conversation Extractor.
Properly handles arrow keys without printing escape sequences.
"""

import os
import sys
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

# Platform-specific imports for keyboard handling
if sys.platform == "win32":
    import msvcrt
else:
    import select
    import termios
    import tty


@dataclass
class SearchState:
    """Maintains the current state of the search interface"""

    query: str = ""
    cursor_pos: int = 0
    results: List = None
    selected_index: int = 0
    last_update: float = 0
    is_searching: bool = False

    def __post_init__(self):
        if self.results is None:
            self.results = []


class KeyboardHandler:
    """Cross-platform keyboard input handler with fixed arrow key support"""

    def __init__(self):
        self.old_settings = None
        if sys.platform != "win32":
            self.stdin_fd = sys.stdin.fileno()

    def __enter__(self):
        if sys.platform != "win32":
            self.old_settings = termios.tcgetattr(self.stdin_fd)
            tty.setraw(self.stdin_fd)
        return self

    def __exit__(self, *args):
        if sys.platform != "win32" and self.old_settings:
            termios.tcsetattr(self.stdin_fd, termios.TCSADRAIN, self.old_settings)

    def get_key(self, timeout: float = 0.1) -> Optional[str]:
        if sys.platform == "win32":
            start_time = time.time()
            while time.time() - start_time < timeout:
                if msvcrt.kbhit():
                    key = msvcrt.getch()
                    if key in (b"\x00", b"\xe0"):
                        key = msvcrt.getch()
                        if key == b"H":
                            return "UP"
                        elif key == b"P":
                            return "DOWN"
                        elif key == b"K":
                            return "LEFT"
                        elif key == b"M":
                            return "RIGHT"
                    elif key == b"\x1b":
                        return "ESC"
                    elif key == b"\r":
                        return "ENTER"
                    elif key == b"\x08":
                        return "BACKSPACE"
                    else:
                        try:
                            return key.decode("utf-8")
                        except UnicodeDecodeError:
                            return None
                time.sleep(0.01)
            return None
        else:
            if select.select([sys.stdin], [], [], timeout)[0]:
                char = sys.stdin.read(1)

                if char == '\x1b':
                    if select.select([sys.stdin], [], [], 0.0)[0]:
                        seq = []
                        seq.append(sys.stdin.read(1))

                        if select.select([sys.stdin], [], [], 0.0)[0]:
                            seq.append(sys.stdin.read(1))

                            if seq == ['[', 'A']:
                                return "UP"
                            elif seq == ['[', 'B']:
                                return "DOWN"
                            elif seq == ['[', 'C']:
                                return "RIGHT"
                            elif seq == ['[', 'D']:
                                return "LEFT"
                            else:
                                while select.select([sys.stdin], [], [], 0.0)[0]:
                                    sys.stdin.read(1)
                                return None
                        return None
                    else:
                        return "ESC"

                elif char == '\r' or char == '\n':
                    return "ENTER"
                elif char == '\x7f' or char == '\x08':
                    return "BACKSPACE"
                elif char == '\x03':
                    raise KeyboardInterrupt
                elif ord(char) >= 32 and ord(char) < 127:
                    return char
                else:
                    return None
            return None


class TerminalDisplay:
    """Manages terminal display for real-time search"""

    def __init__(self):
        self.last_result_count = 0
        self.header_lines = 4

    def clear_screen(self):
        if sys.platform == "win32":
            os.system("cls")
        else:
            print("\033[2J\033[H", end="")

    def move_cursor(self, row: int, col: int):
        print(f"\033[{row};{col}H", end="", flush=True)

    def clear_line(self):
        print("\033[2K", end="", flush=True)

    def save_cursor(self):
        print("\033[s", end="", flush=True)

    def restore_cursor(self):
        print("\033[u", end="", flush=True)

    def draw_header(self):
        self.move_cursor(1, 1)
        print("REAL-TIME SEARCH")
        print("=" * 60)
        print("Type to search | Up/Down to select | Enter to open | ESC to exit")
        print("-" * 60)

    def draw_results(self, results: List, selected_index: int, query: str):
        for i in range(self.last_result_count + 1):
            self.move_cursor(self.header_lines + i + 1, 1)
            self.clear_line()

        if not results:
            self.move_cursor(self.header_lines + 1, 1)
            if query:
                print(f"No results found for '{query}'")
            else:
                print("Start typing to search...")
        else:
            for i, result in enumerate(results[:10]):
                self.move_cursor(self.header_lines + i + 1, 1)

                if i == selected_index:
                    print("> ", end="")
                else:
                    print("  ", end="")

                date_str = result.timestamp.strftime("%Y-%m-%d")
                project = Path(result.file_path).parent.name[:20]

                preview = result.context[:60].replace("\n", " ")
                if query.lower() in preview.lower():
                    idx = preview.lower().find(query.lower())
                    preview = (
                        preview[:idx]
                        + f"\033[93m{preview[idx:idx + len(query)]}\033[0m"
                        + preview[idx + len(query) :]
                    )

                print(f"  {date_str} | {project} | {preview}...")

        self.last_result_count = len(results[:10])

    def draw_search_box(self, query: str, cursor_pos: int):
        row = self.header_lines + self.last_result_count + 3
        self.move_cursor(row, 1)
        self.clear_line()
        print("-" * 60)

        self.move_cursor(row + 1, 1)
        self.clear_line()
        print(f"Search: {query}", end="")

        self.move_cursor(row + 1, 9 + cursor_pos)
        sys.stdout.flush()


class RealTimeSearch:
    """Main real-time search interface with fixed arrow key handling"""

    def __init__(self, searcher, extractor):
        self.searcher = searcher
        self.extractor = extractor
        self.display = TerminalDisplay()
        self.state = SearchState()
        self.search_thread = None
        self.search_lock = threading.Lock()
        self.results_cache = {}
        self.debounce_delay = 0.3
        self.stop_event = threading.Event()

    def _process_search_request(self):
        with self.search_lock:
            if not self.state.is_searching:
                return False

            if time.time() - self.state.last_update < self.debounce_delay:
                return False

            query = self.state.query
            self.state.is_searching = False

        if not query:
            with self.search_lock:
                self.state.results = []
            return True

        if query in self.results_cache:
            with self.search_lock:
                self.state.results = self.results_cache[query]
            return True

        try:
            search_kwargs = {
                "query": query,
                "mode": "smart",
                "max_results": 20,
                "case_sensitive": False,
            }
            if hasattr(self, "search_dir") and self.search_dir:
                search_kwargs["search_dir"] = self.search_dir

            results = self.searcher.search(**search_kwargs)

            self.results_cache[query] = results

            with self.search_lock:
                self.state.results = results
                self.state.selected_index = 0
        except Exception:
            with self.search_lock:
                self.state.results = []

        return True

    def search_worker(self):
        while not self.stop_event.is_set():
            time.sleep(0.05)
            self._process_search_request()

        self.stop_event.clear()

    def handle_input(self, key: str) -> Optional[str]:
        if not key:
            return None

        if key == "ESC":
            return "exit"

        elif key == "ENTER":
            if self.state.results and 0 <= self.state.selected_index < len(
                self.state.results
            ):
                return "select"

        elif key == "UP":
            if self.state.results:
                self.state.selected_index = max(0, self.state.selected_index - 1)
                return "redraw"

        elif key == "DOWN":
            if self.state.results:
                self.state.selected_index = min(
                    len(self.state.results[:10]) - 1, self.state.selected_index + 1
                )
                return "redraw"

        elif key == "LEFT":
            self.state.cursor_pos = max(0, self.state.cursor_pos - 1)
            return "redraw"

        elif key == "RIGHT":
            self.state.cursor_pos = min(
                len(self.state.query), self.state.cursor_pos + 1
            )
            return "redraw"

        elif key == "BACKSPACE":
            if self.state.cursor_pos > 0:
                self.state.query = (
                    self.state.query[: self.state.cursor_pos - 1]
                    + self.state.query[self.state.cursor_pos :]
                )
                self.state.cursor_pos -= 1
                self.trigger_search()
                return "redraw"

        elif key and len(key) == 1 and ord(key) >= 32 and ord(key) < 127:
            self.state.query = (
                self.state.query[: self.state.cursor_pos]
                + key
                + self.state.query[self.state.cursor_pos :]
            )
            self.state.cursor_pos += 1
            self.trigger_search()
            return "redraw"

        return None

    def trigger_search(self):
        with self.search_lock:
            self.state.last_update = time.time()
            self.state.is_searching = True
            keys_to_remove = [
                k
                for k in self.results_cache.keys()
                if not k.startswith(self.state.query)
            ]
            for k in keys_to_remove:
                del self.results_cache[k]

    def stop(self):
        if self.search_thread and self.search_thread.is_alive():
            self.stop_event.set()
            self.search_thread.join(timeout=0.5)

    def run(self) -> Optional[Path]:
        self.search_thread = threading.Thread(target=self.search_worker, daemon=True)
        self.search_thread.start()

        try:
            self.display.clear_screen()
            self.display.draw_header()

            with KeyboardHandler() as keyboard:
                self.display.draw_results(
                    self.state.results[:10],
                    self.state.selected_index,
                    self.state.query,
                )
                self.display.draw_search_box(
                    self.state.query, self.state.cursor_pos
                )

                while True:
                    key = keyboard.get_key(timeout=0.1)

                    if key:
                        action = self.handle_input(key)

                        if action == "exit":
                            return None
                        elif action == "select":
                            selected_result = self.state.results[
                                self.state.selected_index
                            ]
                            return selected_result.file_path
                        elif action == "redraw" or action is None:
                            self.display.draw_results(
                                self.state.results[:10],
                                self.state.selected_index,
                                self.state.query,
                            )
                            self.display.draw_search_box(
                                self.state.query, self.state.cursor_pos
                            )

        except KeyboardInterrupt:
            return None
        finally:
            self.stop()
            self.display.clear_screen()


def create_smart_searcher(searcher):
    """Enhance the searcher with smart search capabilities"""
    original_search = searcher.search

    def smart_search(query: str, **kwargs):
        kwargs.pop("mode", None)

        results = []

        exact_results = original_search(query, mode="exact", **kwargs)
        results.extend(exact_results)

        if any(c in query for c in r".*+?[]{}()^$|\\"):
            try:
                regex_results = original_search(query, mode="regex", **kwargs)
                existing_paths = {r.file_path for r in results}
                for r in regex_results:
                    if r.file_path not in existing_paths:
                        results.append(r)
            except Exception:
                pass

        smart_results = original_search(query, mode="smart", **kwargs)
        existing_paths = {r.file_path for r in results}
        for r in smart_results:
            if r.file_path not in existing_paths:
                results.append(r)

        if hasattr(searcher, "nlp") and searcher.nlp:
            try:
                semantic_results = original_search(query, mode="semantic", **kwargs)
                existing_paths = {r.file_path for r in results}
                for r in semantic_results:
                    if r.file_path not in existing_paths:
                        results.append(r)
            except Exception:
                pass

        try:
            results.sort(
                key=lambda x: x.timestamp if x.timestamp else datetime.min, reverse=True
            )
        except (AttributeError, TypeError):
            try:
                results.sort(
                    key=lambda x: getattr(x, "relevance_score", 0), reverse=True
                )
            except Exception:
                pass

        max_results = kwargs.get("max_results", 20)
        return results[:max_results]

    searcher.search = smart_search
    return searcher


def main():
    """Main entry point for running real-time search directly."""
    from ..core.extractor import ClaudeConversationExtractor
    from ..search.searcher import ConversationSearcher

    extractor = ClaudeConversationExtractor()
    searcher = ConversationSearcher()
    smart_searcher = create_smart_searcher(searcher)

    rts = RealTimeSearch(smart_searcher, extractor)
    selected_file = rts.run()

    if selected_file:
        print(f"\nSelected: {selected_file}")
    else:
        print("\nSearch cancelled")


if __name__ == "__main__":
    main()
