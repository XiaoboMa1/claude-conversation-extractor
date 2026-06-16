#!/usr/bin/env python3
"""
CLI entry point for Claude Conversation Extractor.

The core extraction class lives in extractor.py; this module provides
the argument parser (main) and the interactive launcher (launch_interactive).
"""

import argparse
import sys
from datetime import datetime

# Re-export so existing imports keep working:
#   from extract_claude_logs import ClaudeConversationExtractor
from extractor import ClaudeConversationExtractor  # noqa: F401


def main():
    parser = argparse.ArgumentParser(
        description="Extract Claude Code conversations to clean markdown files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list                    # List all available sessions
  %(prog)s --extract 1               # Extract the most recent session (by list number)
  %(prog)s --extract 1,3,5           # Extract specific sessions by number
  %(prog)s --extract 724a8e2f        # Extract session by ID prefix (+ subagents)
  %(prog)s -e peppy-twirling-wren    # Extract by session slug (auto-name)
  %(prog)s -e "gft resume wording"   # Extract by custom title (/rename)
  %(prog)s --recent 5                # Extract 5 most recent sessions
  %(prog)s --all                     # Extract all sessions
  %(prog)s --output ~/my-logs        # Specify output directory
  %(prog)s --search "python error"   # Search conversations
  %(prog)s --search-regex "import.*" # Search with regex
  %(prog)s --format json --all       # Export all as JSON
  %(prog)s --format html --extract 1 # Export session 1 as HTML
  %(prog)s --detailed --extract 1    # Include tool use & system messages
  %(prog)s --diff --extract 1        # Include file changes (edits & new files)
  %(prog)s --inspect 7c3e9f           # Real-time monitor thinking blocks
  %(prog)s --think -e 1               # Include thinking blocks in exported log
        """,
    )
    parser.add_argument("--list", action="store_true", help="List recent sessions")

    parser.add_argument(
        "-e",
        "--extract",
        type=str,
        help="Extract session(s): by list number (e.g. 1,3,5), session name (slug or /rename title), "
        "or session ID prefix (e.g. 724a8e2f). "
        "When using name or ID prefix, subagent conversations are automatically included.",
    )
    parser.add_argument(
        "--all", "--logs", action="store_true", help="Extract all sessions"
    )
    parser.add_argument(
        "--recent", type=int, help="Extract N most recent sessions", default=0
    )

    parser.add_argument(
        "-o",
        "--output",
        type=str,
        help="Output directory for markdown files"
    )

    parser.add_argument(
        "--limit", type=int, help="Limit for --list command (default: show all)", default=None
    )
    parser.add_argument(
        "--interactive",
        "-i",
        "--start",
        "-s",
        action="store_true",
        help="Launch interactive UI for easy extraction",
    )
    parser.add_argument(
        "--export",
        type=str,
        help="Export mode: 'logs' for interactive UI",
    )

    # Search arguments
    parser.add_argument(
        "--search", type=str, help="Search conversations for text (smart search)"
    )
    parser.add_argument(
        "--search-regex", type=str, help="Search conversations using regex pattern"
    )
    parser.add_argument(
        "--search-date-from", type=str, help="Filter search from date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--search-date-to", type=str, help="Filter search to date (YYYY-MM-DD)"
    )
    parser.add_argument(
        "--search-speaker",
        choices=["human", "assistant", "both"],
        default="both",
        help="Filter search by speaker",
    )
    parser.add_argument(
        "--case-sensitive", action="store_true", help="Make search case-sensitive"
    )

    # Export format arguments
    parser.add_argument(
        "--format",
        choices=["markdown", "json", "html"],
        default="markdown",
        help="Output format for exported conversations (default: markdown)"
    )

    parser.add_argument(
        "-d",
        "--detailed",
        action="store_true",
        help="Include tool use, MCP responses, and system messages in export"
    )

    parser.add_argument(
        "--diff",
        action="store_true",
        help="Include file changes (edits and new files) in the exported conversation"
    )

    # Include thinking blocks in extracted logs
    parser.add_argument(
        "--think",
        action="store_true",
        help="Include thinking blocks in exported conversation (wrapped in markdown comments)"
    )

    # Real-time thinking monitor
    parser.add_argument(
        "--inspect",
        type=str,
        metavar="SESSION_PREFIX",
        help="Monitor a session in real-time and print thinking blocks. "
        "Pass at least 6 characters of the session UUID prefix.",
    )

    args = parser.parse_args()

    # Handle --inspect mode (real-time thinking monitor)
    if args.inspect:
        from think_realtime import main as think_main
        # Pass the identifier through to think_realtime
        sys.argv = [sys.argv[0], args.inspect]
        think_main()
        return

    # Handle interactive mode
    if args.interactive or (args.export and args.export.lower() == "logs"):
        from interactive_ui import main as interactive_main

        interactive_main()
        return

    # Initialize extractor with optional output directory
    extractor = ClaudeConversationExtractor(args.output)

    # Handle search mode
    if args.search or args.search_regex:
        from search_conversations import ConversationSearcher

        searcher = ConversationSearcher()

        # Determine search mode and query
        if args.search_regex:
            query = args.search_regex
            mode = "regex"
        else:
            query = args.search
            mode = "smart"

        # Parse date filters
        date_from = None
        date_to = None
        if args.search_date_from:
            try:
                date_from = datetime.strptime(args.search_date_from, "%Y-%m-%d")
            except ValueError:
                print(f"Invalid date format: {args.search_date_from}")
                return

        if args.search_date_to:
            try:
                date_to = datetime.strptime(args.search_date_to, "%Y-%m-%d")
            except ValueError:
                print(f"Invalid date format: {args.search_date_to}")
                return

        # Speaker filter
        speaker_filter = None if args.search_speaker == "both" else args.search_speaker

        # Perform search
        print(f"Searching for: {query}")
        results = searcher.search(
            query=query,
            mode=mode,
            date_from=date_from,
            date_to=date_to,
            speaker_filter=speaker_filter,
            case_sensitive=args.case_sensitive,
            max_results=30,
        )

        if not results:
            print("No matches found.")
            return

        print(f"\nFound {len(results)} matches across conversations:")

        # Group and display results
        results_by_file = {}
        for result in results:
            if result.file_path not in results_by_file:
                results_by_file[result.file_path] = []
            results_by_file[result.file_path].append(result)

        # Store file paths for potential viewing
        file_paths_list = []
        for file_path, file_results in results_by_file.items():
            file_paths_list.append(file_path)
            print(f"\n{len(file_paths_list)}. {file_path.parent.name} ({len(file_results)} matches)")
            # Show first match preview
            first = file_results[0]
            print(f"   {first.speaker}: {first.matched_content[:100]}...")

        # Offer to view conversations
        if file_paths_list:
            print("\n" + "=" * 60)
            try:
                view_choice = input("\nView a conversation? Enter number (1-{}) or press Enter to skip: ".format(
                    len(file_paths_list))).strip()

                if view_choice.isdigit():
                    view_num = int(view_choice)
                    if 1 <= view_num <= len(file_paths_list):
                        selected_path = file_paths_list[view_num - 1]
                        extractor.display_conversation(selected_path, detailed=args.detailed)

                        # Offer to extract after viewing
                        extract_choice = input("\nExtract this conversation? (y/N): ").strip().lower()
                        if extract_choice == 'y':
                            conversation = extractor.extract_conversation(selected_path, detailed=args.detailed, diff=args.diff, think=args.think)
                            if conversation:
                                session_id = selected_path.stem
                                if args.format == "json":
                                    output = extractor.save_as_json(conversation, session_id)
                                elif args.format == "html":
                                    output = extractor.save_as_html(conversation, session_id)
                                else:
                                    output = extractor.save_as_markdown(conversation, session_id)
                                print(f"Saved: {output.name}")
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled")

        return

    # Default action is to list sessions
    if args.list or (
        not args.extract
        and not args.all
        and not args.recent
        and not args.search
        and not args.search_regex
    ):
        sessions = extractor.list_recent_sessions(args.limit)

        if sessions and not args.list:
            print("\nTo extract conversations:")
            print("  claude-extract -e <number>             # Extract by list number")
            print("  claude-extract -e <session-name>       # Extract by slug or /rename title")
            print("  claude-extract -e <session_id>         # Extract by session ID prefix")
            print("  claude-extract --recent 5              # Extract 5 most recent")
            print("  claude-extract --all                   # Extract all sessions")

    elif args.extract:
        extract_arg = args.extract.strip()

        # Resolution strategy (in order):
        # 1. Comma-separated numbers (e.g. "1,3,5") -> numeric list indices
        # 2. Pure small number (1-999) -> numeric list index
        # 3. Session name match (slug or customTitle) via session_resolver
        # 4. UUID prefix match (>= 7 hex chars) via existing find_session_by_id
        # 5. Fallback: try parsing as number

        is_comma_list = "," in extract_arg
        session_path = None

        if not is_comma_list:
            # Check if it's a small pure number (list index)
            is_small_number = extract_arg.isdigit() and int(extract_arg) < 1000

            if not is_small_number:
                # Try session name resolution first (slug, customTitle, or UUID prefix)
                try:
                    from session_resolver import resolve_session
                    session_path = resolve_session(extract_arg)
                except ImportError:
                    pass

                # Fallback to legacy UUID prefix match
                if not session_path and len(extract_arg) >= 7:
                    session_path = extractor.find_session_by_id(extract_arg, quiet=True)

        if session_path:
            # Session identified: extract session + all subagents
            try:
                from session_resolver import get_session_display_name
                display = get_session_display_name(session_path)
            except ImportError:
                display = session_path.stem[:8]
            print(f"\nExtracting: {display} ({session_path.stem[:8]}...) as {args.format.upper()}...")
            if args.detailed:
                print("Including detailed tool use and system messages")
            saved = extractor.extract_session_with_subagents(
                session_path, format=args.format, detailed=args.detailed, diff=args.diff, think=args.think
            )
            print(f"\nSuccessfully saved {saved} file(s)")
        else:
            # Numeric index mode
            sessions = extractor.find_sessions()

            indices = []
            for num in extract_arg.split(","):
                try:
                    idx = int(num.strip()) - 1  # Convert to 0-based index
                    indices.append(idx)
                except ValueError:
                    print(f"Invalid argument: {num} (not a list number, session name, or ID prefix)")
                    continue

            if indices:
                print(f"\nExtracting {len(indices)} session(s) as {args.format.upper()}...")
                if args.detailed:
                    print("Including detailed tool use and system messages")
                success, total = extractor.extract_multiple(
                    sessions, indices, format=args.format, detailed=args.detailed, diff=args.diff, think=args.think
                )
                print(f"\nSuccessfully extracted {success}/{total} sessions")

    elif args.recent:
        sessions = extractor.find_sessions()
        limit = min(args.recent, len(sessions))
        print(f"\nExtracting {limit} most recent sessions as {args.format.upper()}...")
        if args.detailed:
            print("Including detailed tool use and system messages")

        indices = list(range(limit))
        success, total = extractor.extract_multiple(
            sessions, indices, format=args.format, detailed=args.detailed, diff=args.diff, think=args.think
        )
        print(f"\nSuccessfully extracted {success}/{total} sessions")

    elif args.all:
        sessions = extractor.find_sessions()
        print(f"\nExtracting all {len(sessions)} sessions as {args.format.upper()}...")
        if args.detailed:
            print("Including detailed tool use and system messages")

        indices = list(range(len(sessions)))
        success, total = extractor.extract_multiple(
            sessions, indices, format=args.format, detailed=args.detailed, diff=args.diff, think=args.think
        )
        print(f"\nSuccessfully extracted {success}/{total} sessions")


def launch_interactive():
    """Launch the interactive UI directly, or handle search if specified."""
    import sys

    # If no arguments provided, launch interactive UI
    if len(sys.argv) == 1:
        try:
            from .interactive_ui import main as interactive_main
        except ImportError:
            from interactive_ui import main as interactive_main
        interactive_main()
    # Check if 'search' was passed as an argument
    elif len(sys.argv) > 1 and sys.argv[1] == 'search':
        # Launch real-time search with viewing capability
        try:
            from .realtime_search import RealTimeSearch, create_smart_searcher
            from .search_conversations import ConversationSearcher
        except ImportError:
            from realtime_search import RealTimeSearch, create_smart_searcher
            from search_conversations import ConversationSearcher

        # Initialize components
        extractor = ClaudeConversationExtractor()
        searcher = ConversationSearcher()
        smart_searcher = create_smart_searcher(searcher)

        # Run search
        rts = RealTimeSearch(smart_searcher, extractor)
        selected_file = rts.run()

        if selected_file:
            # View the selected conversation
            extractor.display_conversation(selected_file)

            # Offer to extract
            try:
                extract_choice = input("\nExtract this conversation? (y/N): ").strip().lower()
                if extract_choice == 'y':
                    conversation = extractor.extract_conversation(selected_file)
                    if conversation:
                        session_id = selected_file.stem
                        output = extractor.save_as_markdown(conversation, session_id)
                        print(f"Saved: {output.name}")
            except (EOFError, KeyboardInterrupt):
                print("\nCancelled")
    else:
        # If other arguments are provided, run the normal CLI
        main()


if __name__ == "__main__":
    main()
