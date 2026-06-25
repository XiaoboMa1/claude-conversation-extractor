#!/usr/bin/env python3
"""
CLI entry point for Claude Conversation Extractor.

The core extraction class lives in core/extractor.py; this module provides
the argument parser (main) and the interactive launcher (launch_interactive).
"""

import argparse
import sys

from ..core.extractor import ClaudeConversationExtractor


def main():
    parser = argparse.ArgumentParser(
        description="Extract Claude Code conversations to clean markdown files",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""\
Examples:
  %(prog)s --list                    # Interactive: browse projects -> sessions -> extract
  %(prog)s --extract 1               # Extract the most recent session (by list number)
  %(prog)s --extract 1,3,5           # Extract specific sessions by number
  %(prog)s --extract 724a8e2f        # Extract session by ID prefix (+ subagents)
  %(prog)s -e peppy-twirling-wren    # Extract by session slug (auto-name)
  %(prog)s -e "gft resume wording"   # Extract by custom title (/rename)
  %(prog)s --recent 5                # Extract 5 most recent sessions
  %(prog)s --all                     # Extract all sessions
  %(prog)s --output ~/my-logs        # Specify output directory
  %(prog)s --format json --all       # Export all as JSON
  %(prog)s --format html --extract 1 # Export session 1 as HTML
  %(prog)s --detailed --extract 1    # Include tool use & system messages
  %(prog)s --diff --extract 1        # Include file changes (edits & new files)
  %(prog)s --inspect 7c3e9f           # Real-time monitor thinking blocks
  %(prog)s --think -e 1               # Include thinking blocks in exported log
  %(prog)s -D cb15eb3d                # Delete session by ID prefix
  %(prog)s --delete peppy-twirling    # Delete session by name
  %(prog)s --delete                   # Browse and delete sessions interactively

  claude-search "keyword"            # Search conversations (separate command)
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

    parser.add_argument(
        "--think",
        action="store_true",
        help="Include thinking blocks in exported conversation (wrapped in markdown comments)"
    )

    parser.add_argument(
        "-D",
        "--delete",
        nargs="?",
        const="",
        metavar="SESSION",
        help="Delete a session by name or ID prefix. Omit SESSION to browse interactively.",
    )

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
        from ..monitor.think import main as think_main
        sys.argv = [sys.argv[0], args.inspect]
        think_main()
        return

    # Handle --delete mode
    if args.delete is not None:
        if args.delete:
            from ..history.manager import delete_by_identifier
            delete_by_identifier(args.delete)
        else:
            from ..history.manager import clean_interactive
            clean_interactive()
        return

    # Handle interactive mode
    if args.interactive or (args.export and args.export.lower() == "logs"):
        from .interactive import main as interactive_main
        interactive_main()
        return

    # Handle --list: two-stage interactive listing
    if args.list or (
        not args.extract
        and not args.all
        and not args.recent
    ):
        from .listing import interactive_list

        selected_session = interactive_list()

        if selected_session:
            extractor = ClaudeConversationExtractor(args.output)
            print(f"\nExtracting session {selected_session.stem[:8]}... as {args.format.upper()} ...")
            saved = extractor.extract_session_with_subagents(
                selected_session,
                format=args.format,
                detailed=args.detailed,
                diff=args.diff,
                think=args.think,
            )
            print(f"\nSuccessfully saved {saved} file(s)")
        return

    # Initialize extractor with optional output directory
    extractor = ClaudeConversationExtractor(args.output)

    if args.extract:
        extract_arg = args.extract.strip()

        is_comma_list = "," in extract_arg
        session_path = None

        if not is_comma_list:
            is_small_number = extract_arg.isdigit() and int(extract_arg) < 1000

            if not is_small_number:
                try:
                    from ..session.resolver import resolve_session
                    session_path = resolve_session(extract_arg)
                except ImportError:
                    pass

                if not session_path and len(extract_arg) >= 7:
                    session_path = extractor.find_session_by_id(extract_arg, quiet=True)

        if session_path:
            try:
                from ..session.resolver import get_session_display_name
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
            sessions = extractor.find_sessions()

            indices = []
            for num in extract_arg.split(","):
                try:
                    idx = int(num.strip()) - 1
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

    if len(sys.argv) == 1:
        from .interactive import main as interactive_main
        interactive_main()
    elif len(sys.argv) > 1 and sys.argv[1] == 'search':
        from ..monitor.realtime_search import RealTimeSearch, create_smart_searcher
        from ..search.searcher import ConversationSearcher

        extractor = ClaudeConversationExtractor()
        searcher = ConversationSearcher()
        smart_searcher = create_smart_searcher(searcher)

        rts = RealTimeSearch(smart_searcher, extractor)
        selected_file = rts.run()

        if selected_file:
            extractor.display_conversation(selected_file)

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
        main()


if __name__ == "__main__":
    main()
