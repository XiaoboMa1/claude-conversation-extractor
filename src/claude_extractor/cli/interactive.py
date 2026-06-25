#!/usr/bin/env python3
"""Interactive terminal UI for Claude Conversation Extractor"""

import os
import platform
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from ..core.extractor import ClaudeConversationExtractor
from ..monitor.realtime_search import RealTimeSearch, create_smart_searcher
from ..search.searcher import ConversationSearcher


class InteractiveUI:
    """Interactive terminal UI for easier conversation extraction"""

    def __init__(self, output_dir: Optional[str] = None):
        self.output_dir = output_dir
        self.extractor = ClaudeConversationExtractor(output_dir)
        self.searcher = ConversationSearcher()
        self.sessions: List[Path] = []
        self.terminal_width = shutil.get_terminal_size().columns

    def clear_screen(self):
        print("\033[2J\033[H", end="")

    def print_banner(self):
        MAGENTA = "\033[95m"
        RESET = "\033[0m"
        BOLD = "\033[1m"

        banner = f"""{MAGENTA}{BOLD}

 ██████╗██╗      █████╗ ██╗   ██╗██████╗ ███████╗
██╔════╝██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝
██║     ██║     ███████║██║   ██║██║  ██║█████╗
██║     ██║     ██╔══██║██║   ██║██║  ██║██╔══╝
╚██████╗███████╗██║  ██║╚██████╔╝██████╔╝███████╗
 ╚═════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝
███████╗██╗  ██╗████████╗██████╗  █████╗  ██████╗████████╗
██╔════╝╚██╗██╔╝╚══██╔══╝██╔══██╗██╔══██╗██╔════╝╚══██╔══╝
█████╗   ╚███╔╝    ██║   ██████╔╝███████║██║        ██║
██╔══╝   ██╔██╗    ██║   ██╔══██╗██╔══██║██║        ██║
███████╗██╔╝ ██╗   ██║   ██║  ██║██║  ██║╚██████╗   ██║
╚══════╝╚═╝  ╚═╝   ╚═╝   ╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝   ╚═╝

{RESET}"""
        print(banner)

    def print_centered(self, text: str, char: str = "="):
        padding = (self.terminal_width - len(text) - 2) // 2
        print(f"{char * padding} {text} {char * padding}")

    def get_folder_selection(self) -> Optional[Path]:
        self.clear_screen()
        self.print_banner()
        print("\n  Where would you like to save your conversations?\n")

        home = Path.home()
        suggestions = [
            home / "Desktop" / "Claude Conversations",
            home / "Documents" / "Claude Conversations",
            home / "Downloads" / "Claude Conversations",
            Path.cwd() / "Claude Conversations",
        ]

        print("Suggested locations:")
        for i, path in enumerate(suggestions, 1):
            print(f"  {i}. {path}")

        print("\n  C. Custom location")
        print("  Q. Quit")

        while True:
            choice = input("\nSelect an option (1-4, C, or Q): ").strip().upper()

            if choice == "Q":
                return None
            elif choice == "C":
                custom_path = input("\nEnter custom path: ").strip()
                if custom_path:
                    return Path(custom_path).expanduser()
            elif choice.isdigit() and 1 <= int(choice) <= len(suggestions):
                return suggestions[int(choice) - 1]
            else:
                print("Invalid choice. Please try again.")

    def show_sessions_menu(self) -> List[int]:
        self.clear_screen()
        self.print_banner()

        print("\n  Finding your Claude conversations...")
        self.sessions = self.extractor.find_sessions()

        if not self.sessions:
            print("\n  No Claude conversations found!")
            print("Make sure you've used Claude Code at least once.")
            input("\nPress Enter to exit...")
            return []

        print(f"\n  Found {len(self.sessions)} conversations!\n")

        for i, session_path in enumerate(self.sessions[:20], 1):
            project = session_path.parent.name
            modified = datetime.fromtimestamp(session_path.stat().st_mtime)
            size_kb = session_path.stat().st_size / 1024

            date_str = modified.strftime("%Y-%m-%d %H:%M")
            print(f"  {i:2d}. [{date_str}] {project[:30]:<30} ({size_kb:.1f} KB)")

        if len(self.sessions) > 20:
            print(f"\n  ... and {len(self.sessions) - 20} more conversations")

        print("\n" + "=" * 60)
        print("\nOptions:")
        print("  A. Extract ALL conversations")
        print("  R. Extract 5 most RECENT")
        print("  S. SELECT specific conversations (e.g., 1,3,5)")
        print("  F. SEARCH conversations (real-time search)")
        print("  Q. QUIT")

        while True:
            choice = input("\nYour choice: ").strip().upper()

            if choice == "Q":
                return []
            elif choice == "A":
                return list(range(len(self.sessions)))
            elif choice == "R":
                return list(range(min(5, len(self.sessions))))
            elif choice == "S":
                selection = input("Enter conversation numbers (e.g., 1,3,5): ").strip()
                try:
                    indices = [int(x.strip()) - 1 for x in selection.split(",")]
                    if all(0 <= i < len(self.sessions) for i in indices):
                        return indices
                    else:
                        print("Invalid selection. Please use valid numbers.")
                except ValueError:
                    print("Invalid format. Use comma-separated numbers.")
            elif choice == "F":
                search_results = self.search_conversations()
                if search_results:
                    return search_results
            else:
                print("Invalid choice. Please try again.")

    def show_progress(self, current: int, total: int, message: str = ""):
        bar_width = 40
        progress = current / total if total > 0 else 0
        filled = int(bar_width * progress)
        bar = "=" * filled + "-" * (bar_width - filled)

        print(f"\r[{bar}] {current}/{total} {message}", end="", flush=True)

    def search_conversations(self) -> List[int]:
        smart_searcher = create_smart_searcher(self.searcher)

        rts = RealTimeSearch(smart_searcher, self.extractor)
        selected_file = rts.run()

        if selected_file:
            self.extractor.display_conversation(Path(selected_file))

            extract_choice = input("\nExtract this conversation? (y/N): ").strip().lower()
            if extract_choice == 'y':
                try:
                    index = self.sessions.index(Path(selected_file))
                    return [index]
                except ValueError:
                    print("\nError: Selected file not found in sessions list")
                    input("\nPress Enter to continue...")

            return []

        return []

    def extract_conversations(self, indices: List[int], output_dir: Path) -> int:
        print(f"\nExtracting {len(indices)} conversations...\n")

        self.extractor.output_dir = output_dir

        success_count, total_count = self.extractor.extract_multiple(
            self.sessions, indices
        )

        print(
            f"\n\nSuccessfully extracted {success_count}/{total_count} conversations!"
        )
        return success_count

    def open_folder(self, path: Path):
        try:
            if platform.system() == "Windows":
                os.startfile(str(path))
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(path)])
            else:
                subprocess.run(["xdg-open", str(path)])
        except Exception:
            pass

    def run(self):
        try:
            output_dir = self.get_folder_selection()
            if not output_dir:
                print("\nGoodbye!")
                return

            selected_indices = self.show_sessions_menu()
            if not selected_indices:
                print("\nGoodbye!")
                return

            output_dir.mkdir(parents=True, exist_ok=True)

            success_count = self.extract_conversations(selected_indices, output_dir)

            if success_count > 0:
                print(f"\nFiles saved to: {output_dir}")

                open_choice = input("\nOpen output folder? (Y/n): ").strip().lower()
                if open_choice != "n":
                    self.open_folder(output_dir)
            else:
                print("\nNo conversations were extracted.")

            input("\nPress Enter to exit...")

        except KeyboardInterrupt:
            print("\n\nGoodbye!")
        except Exception as e:
            print(f"\nError: {e}")
            input("\nPress Enter to exit...")


def main():
    ui = InteractiveUI()
    ui.run()


if __name__ == "__main__":
    main()
