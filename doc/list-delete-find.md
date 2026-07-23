# Interactive Commands: `--list`, `--delete`, `--find`

Three interactive multi-stage commands for browsing, deleting, and searching Claude Code conversations stored in `~/.claude/projects/`.

All three share a common data source (JSONL session files), common terminal primitives (`_read_line`, `_pager`), and common noise filtering. This document specifies each command's stage flow, terminal output format, input handling, and edge cases.

---

## 1. Shared Behavior

### 1.1 Input Controls

All interactive prompts use `_read_line` (browser.py):

| Key | Behavior |
|-----|----------|
| Enter | Confirm typed input |
| Esc | Go back to previous stage, or exit if at stage 1. Exception: `--delete` stage 2 Esc exits (no back to project selection). |
| Backspace | Delete last character |
| Ctrl-C | Same as Esc |
| Arrow keys | ignored (Windows: `\x00`/`\xe0` prefix + scan code consumed) |

On non-Windows platforms, `_read_line` falls back to `input()` with `KeyboardInterrupt` mapped to Esc.

### 1.2 Pager

Session lists and full messages are displayed through `_pager` (browser.py). Tries, in order:

1. `less -R` (available in Git Bash on Windows)
2. `more` (non-Windows only — Windows `more` lacks arrow key support)
3. Built-in scrollable pager (Windows with VT/ANSI support):

| Key | Behavior |
|-----|----------|
| Up / Down arrow | Scroll one line |
| Page Up / Page Down | Scroll one screenful |
| Home / End | Jump to top / bottom |
| Space | Page down |
| Enter | Line down |
| b | Page up (less-style) |
| q / Esc | Quit pager |

Uses alternate screen buffer: pager content renders on a separate screen and the original terminal content is restored on quit.

4. Simple fallback pager (no ANSI support): one screenful at a time, `:` prompt, `q` to quit.

### 1.3 Noise Filtering

Messages whose text starts with any of these prefixes are treated as system noise and excluded from previews, tail displays, and search results:

```
Caveat: The messages below were generated
<local-command-caveat>
<system-reminder>
<task-notification>
This session is being continued from a previous conversation
You've hit your limit
Prompt is too long
No response requested
No response needed
No response required
[Request interrupted
Tool use interrupted
```

Noise filtering is applied in all display contexts: `--list` session previews, `--delete` tail previews, `--find` search results, and `--list`/`--find` stage 3-4 message browsing.

Additional noise detection: if stripping all XML tag pairs (`<tag>...</tag>`) from a message leaves fewer than 5 characters, the message is noise.

Skill messages (starting with `Base directory for this skill`) are not noise but receive special handling — previews show the last N lines instead of the first N.

### 1.4 Message Merging

When loading messages for stage 3/4 browsing (`load_messages`), consecutive JSONL entries from the same speaker are merged into a single logical message. This matches the extract pipeline's output. Each merged message gets a stable 1-based ID.

### 1.5 Project Directory Name Decoding

Claude Code encodes absolute project paths by replacing all non-alphanumeric characters with `-`. On Windows, `D--dev-foo` is decoded to `D:\dev foo`. On other platforms, hyphens are replaced with spaces.

### 1.6 Subagent Exclusion

All commands exclude subagent JSONL files (`subagents/` in path) from project/session enumeration and search. Only main session files are shown.

### 1.7 Sort Order

Projects and sessions are sorted by most-recent modification time first (newest at top).

---

## 2. `--list` — Browse and Extract

**CLI**: `extract --list` or `extract` (no arguments)

Four stages: project selection → session selection → message preview → full view + extract.

### Stage 1: Project Selection

Not paginated. Printed directly to terminal.

```
Found 3 project(s):

============================================================

1. D:\dev foo
   Modified: 2026-07-18 18:53
   Sessions: 7

2. D:\work bar
   Modified: 2026-07-15 14:12
   Sessions: 3

============================================================

Select project (1-3) [Esc=exit]:
```

**Input**: single number. Empty Enter re-prompts. Esc exits.

**Data per project**: decoded directory name, last-modified timestamp (most recent session file mtime), session count.

### Stage 2: Session Selection

Shown in pager. Uses the same colored structural markers as `--find` stage 2 — bold cyan `━━` session headers, colored speaker labels (green = user, magenta = assistant), and dim `│` gutter for message content.

```
Found 7 session(s):

━━ 1. 4518d44a... (aggre) — 2026-07-18 18:53 ━━━━━━━━━━
   Subagents: 3 explore, 2 compact
  user:
  │  How do I aggregate the results from
  │  multiple API calls into a single response?
  │  I need to combine data from three endpoints.
  assistant:
  │  Here's the approach using Promise.all
  │  to run the three fetches concurrently...

━━ 2. bea128cc... (l4jconfig) — 2026-07-18 13:23 ━━━━━━━
  user:
  │  Can you help me configure log4j2...
```

After pager exits:

```
Select session (1-7, space separated) to view [Esc=back]:
```

**Input**: space-separated session numbers (e.g., `1 3`). Selecting multiple sessions merges their messages into a single browsable list with sequential IDs (same behavior as `--find` stage 3). Esc returns to stage 1.

**Data per session**: 8-char ID prefix, display name (custom title > slug > ID prefix), modified timestamp in header, subagent counts (if any), first 3 lines of first non-noise user message, first 3 lines of last non-noise assistant message.

### Stage 3: Message Preview

Shown in pager (arrow key scrolling). ID labels are bold, role labels are colored (green = User, magenta = Claude).

```
16 messages:

============================================================

ID 1: User                    ← bold ID, green role
   How do I aggregate the results from
   multiple API calls into a single response?
   I need to combine data from three endpoints.

ID 2: Claude                  ← bold ID, magenta role
   Here's the approach using Promise.all
   to run the three fetches concurrently...
   First, define your endpoint URLs:

...

ID 16: Claude
   Done. The refactored version handles errors
   per-endpoint and falls back to cached data.

============================================================
```

After pager exits:

```
Select message IDs to view (1-16, space separated) [Esc=back]:
```

**Input**: space-separated message IDs (e.g., `1 5 16`). Esc returns to stage 2 session list.

**Preview content**: first 3 non-blank lines per message (last 3 for skill-invoked messages).

### Stage 4: Full Message View + Extract

Selected messages rendered in pager as markdown:

```
## ID 1: User

How do I aggregate the results from multiple API calls into a single response?
I need to combine data from three endpoints.

---

## ID 5: Claude

Here's the refactored aggregation function:
...

---
```

After pager exits:

```
Copy message IDs (1-16, space separated) to clipboard, or -o <dir> to file [Esc=back]:
```

**Input formats**:

| Input | Action |
|-------|--------|
| `1 5 16` | Copy messages 1, 5, 16 to system clipboard |
| `1 5 -o ./out` | Save messages 1, 5 to `./out/<session-name>.md` |

Esc returns to stage 3 (message preview). After successful extraction, the flow also returns to stage 3 for further selection.

**Clipboard**: Windows `clip`, macOS `pbcopy`, Linux `xclip`/`xsel`.

**File output**: filename derived from session display name (non-alphanumeric chars replaced with `-`, consecutive dashes collapsed). Directory created if absent.

### Navigation Summary

```
Stage 1 ←Esc── Stage 2 ←Esc── Stage 3 ←Esc── Stage 4
  │               │               │               │
  Esc=exit        Esc=back        Esc=back        Esc=back
                  select→3        select→4        extract→back to 3
```

Esc always goes back one stage. Only Esc at stage 1 exits the program. After extraction (stage 4), the flow returns to stage 3 (message preview) for further selection.

---

## 3. `--delete` — Delete Sessions

**CLI**: `extract --delete` / `extract -D` (interactive) or `extract -D <name-or-id>` (direct)

### Direct Delete (`extract -D cb15eb3d`)

Non-interactive. Resolves the identifier (UUID prefix, slug, or custom title), shows session details, and prompts `y/N`:

```
Session: my-session (cb15eb3d...)
  File: C:\Users\...\.claude\projects\D--foo\cb15eb3d-...-4006.jsonl
  Dir:  C:\Users\...\.claude\projects\D--foo\cb15eb3d-...-4006 (142.3 KB)

  Last message (tail):
    Done. The refactored version handles errors
    per-endpoint and falls back to cached data.

Delete this session? (y/N):
```

Deletes both the `.jsonl` file and the session directory (containing subagent files, etc.).

### Interactive Delete (`extract --delete`)

Three stages: project selection → session list → delete loop.

#### Stage 1: Project Selection

Reuses the same `_stage_projects` function as `--list`. Same format, same controls. Esc exits.

#### Stage 2: Session List

Shown in pager. Each session shows its last 5 non-blank lines from the last non-noise message (tail, not head — shows what the session ended with).

```
7 session(s):

============================================================

  1. 4518d44a...  (aggre)  [2026-07-18 18:53]
       Done. The refactored version handles errors
       per-endpoint and falls back to cached data.
       Let me know if you want me to add retry logic.

  2. bea128cc...  (l4jconfig)  [2026-07-18 13:23]
       The log4j2.xml configuration is now updated.
       Restart the application to pick up changes.

  3. 2b94ba4f...  (unit-test)  [2026-07-15 14:12]
       (no message content)

============================================================
```

After pager exits:

```
Select session (1-7, space separated) to delete [Esc=exit]:
```

**Input**: space-separated session numbers (e.g., `1 3 5`). Duplicates are deduplicated. Numbers are sorted before processing.

**Deletion is immediate** — no extra confirmation prompt.

```
  Deleted: 4518d44a... (aggre)
  Deleted: 2b94ba4f... (unit-test)
```

After deletion, the session list is refreshed and shown in pager again (the deleted sessions are gone). The loop continues until Esc or all sessions are deleted.

```
Deleted 2 session(s) total.
```

#### Stage 2 Preview: `get_last_message_tail`

Unlike `--list` (which shows first 3 lines of first user message and first 3 lines of last assistant message), `--delete` shows the **last 5 non-blank lines** of the last non-noise message of any role. This shows what the session ended with, helping identify stale/abandoned sessions.

### Navigation Summary

```
Direct: show details → y/N → delete or cancel
Interactive: Stage 1 ←(reuse)── _stage_projects ──Esc→ exit
                 │
                 └─select─→ Stage 2 ──Esc→ exit
                               │
                               └─select numbers─→ delete → refresh → Stage 2
```

---

## 4. `--find` — Search and Extract

**CLI**: `extract --find "keyword"` / `extract -f "keyword"` or `extract -f` (prompts for keyword)

**Regex mode**: `extract -f "pattern" -r` / `extract --find "pattern" --regex`

**Backward-compatible entry point**: `claude-search keyword`

Three stages: project selection → session match list → message browse + extract.

### Keyword Input

If no keyword provided on CLI, prompts interactively:

```
Search keyword:       (default)
Search regex:         (with -r)
```

Esc exits. After entering keyword:

```
Searching for keyword: 'keyword' ...
Searching for regex: 'pattern' ...
```

Search is case-insensitive. Scans all JSONL files under `~/.claude/projects/`, excluding subagent files. Matches are found at the line level within each message's text content (after XML tag stripping).

### Regex Mode (`-r`)

Without `-r`, the search term is a literal substring match — characters like `.` `*` `(` have no special meaning.

With `-r`, the search term is a Python regex pattern (the `re` module). Matching scope is per-line: each line of a message is tested independently. A pattern cannot span across line breaks.

Common patterns:

| Pattern | Matches |
|---------|---------|
| `error\|warn` | lines containing "error" or "warn" |
| `def\s+test_` | function definitions starting with `test_` |
| `import\s+(os\|sys\|re)` | imports of os, sys, or re |
| `TODO.*fix` | "TODO" followed by "fix" on the same line (any distance) |
| `\d{3,}` | sequences of 3+ digits |
| `"[^"]*"` | double-quoted strings |

Invalid patterns are rejected before search starts with the `re` module's error message.

### Stage 1: Project Selection

Skipped if matches exist in only one project (auto-selected). Otherwise:

```
Found 12 match(es) across 2 project(s):

  1. D:\dev foo  (3 session(s), 8 match(es))
  2. D:\work bar  (1 session(s), 4 match(es))

Select project (1-2) [Esc=exit]:
```

Not paginated. Esc exits.

### Stage 2: Session Match List

Shown in pager. Sessions grouped under the selected project. Each session uses colored structural markers to distinguish session headers, match labels, and message content:

- **Session header**: bold cyan `━━` rule line
- **Match label**: yellow `match N` + colored speaker (green = user, magenta = assistant), on its own line
- **Content lines**: prefixed with dim `│` gutter — anything right of `│` is raw message text

```
━━ 1. bf0d3317... (resume-review) — 3 match(es) ━━━━━━━━━━
  match 1  user:
  │  I think the third paragraph is still not right.
  │  The claim about "building the entire session manager"
  │  is misleading because we only modified the timeout
  │  handling, not the whole component. Can you check
  │  the source code and rewrite this accurately?
  │  Compare with the PR description in Linear.
  │  The actual scope was much smaller.
  match 2  user:
  │  Also the second bullet point needs fixing
  match 3  assistant:
  │  You're right. Looking at the PR, the scope was limited to

━━ 2. ca7750bd... (cleanup) — 1 match(es) ━━━━━━━━━━━━━━━━
  match 1  assistant:
  │  The configuration has been updated.
  │  I removed the deprecated settings and
  │  added the new format as specified in
  │  the migration guide.
```

After pager exits:

```
Select session (1-2, space separated) to view [Esc=back]:
```

#### Match Context Rules

Each match entry corresponds to one JSONL message that contains the keyword. Within a single message:

- **First match**: 3 lines before the match line + the match line + 3 lines after (7 lines total, fewer at message boundaries). Lines truncated to 120 characters.
- **Subsequent matches in the same message**: just the line containing the keyword (truncated to 120 chars). This avoids repeating large context blocks when a keyword appears many times in one message.

Match label and content are always on **separate lines** (the label never shares a line with content text). This prevents content that starts with numbers or dashes from being confused with structural elements.

The `match N` counter is sequential across all messages within a session. The session header shows the total match count.

XML tag pairs are stripped from context before display. Blank-line runs are collapsed.

**Input**: space-separated session numbers (e.g., `1 2`). Duplicates are deduplicated. Esc goes back to stage 1 (or exits if single-project).

### Stage 3: Message Browse + Extract

After selecting sessions, all messages from the selected sessions are loaded, merged (consecutive same-speaker entries combined), and re-numbered sequentially starting from 1. Then the same two-phase flow as `--list` stages 3-4 runs:

1. **Message preview**: all messages shown with ID and 3-line preview. User selects message IDs to view.
2. **Full message view**: selected messages rendered in pager. User selects message IDs to copy to clipboard or save to file.

If multiple sessions were selected, their messages appear in sequence under a single numbering scheme (e.g., session A has messages 1-10, session B has messages 11-16).

Esc in message preview returns to stage 2 (session match list). Esc in full view returns to message preview. After extraction, the flow returns to message preview for further selection.

### Navigation Summary

```
keyword input → search
  │
  └─→ Stage 1 (auto-skip if single project)
        │
        └─select─→ Stage 2 ←Esc── Stage 3 (preview) ←Esc── Stage 4 (full view)
                      │               │                         │
                      Esc=Stage 1     Esc=back to 2             Esc=back to 3
                      (or exit if     select IDs→4              extract→back to 3
                       single project)
```

Esc always goes back one stage. Only Esc at stage 1 (or stage 2 with a single project) exits. After extraction (stage 4), the flow returns to stage 3 for further selection. If no messages are found, exits immediately.

---

## 5. Edge Cases

### 5.1 Empty States

| Condition | Behavior |
|-----------|----------|
| No `~/.claude/projects/` directory | Print message and exit |
| No JSONL files found | Print "No Claude sessions found" and exit |
| Project has 0 sessions (all deleted) | `--delete`: print "No sessions remaining" and exit loop |
| Session has 0 non-noise messages | `--list`/`--find` stage 3: print "No messages found" and return |
| Search keyword has 0 matches | Print "No matches found for '...'" and exit |

### 5.2 Noise Messages in Previews

`get_last_message_tail` (used by `--delete`) and `get_first_meaningful_message` / `get_last_meaningful_message` (used by `--list`) skip noise messages. If the last JSONL entry in a session is `[Request interrupted by user for tool use]`, the preview shows the previous real message instead. If no non-noise messages exist, the preview shows `(no message content)`.

### 5.3 Multiple Matches in One Message (`--find`)

When a keyword appears on multiple lines within a single JSONL message:

- All match lines are found by scanning each line of the message text (after XML stripping)
- The first match line gets 3-line context window
- Remaining match lines are listed individually without context
- The total `match_count` reflects all hits across all lines

When two match lines are within 6 lines of each other (overlapping context windows), they are NOT merged in the display — each still gets its own `match N` entry. The first match always gets context; the rest get bare lines regardless of proximity.

### 5.4 Skill Messages

Messages from skill invocations (starting with `Base directory for this skill`) have their auto-injected skill template stripped. Only the user's actual input after `ARGUMENTS:` is kept. If no user input exists (pure skill invocation with no arguments), the message is dropped entirely.

For preview display, skill messages show the last 3 lines instead of the first 3.

### 5.5 Session Display Name Priority

Session names are resolved in this order:

1. Custom title (set via `/rename` in Claude Code)
2. Slug (auto-generated name like `peppy-twirling-wren`)
3. First 8 characters of the UUID

### 5.6 Batch Selection

All three commands' stage 2 prompts accept space-separated numbers for batch operations. Duplicates are silently deduplicated. Numbers are validated against the displayed range. Invalid or out-of-range numbers print an error and re-prompt (no partial processing). For `--list`, selecting multiple sessions merges their messages into a single browsable list (same as `--find` stage 3).

### 5.7 Large Sessions / Long Lines

Pager handles large session lists and long messages. `less -R` supports scrolling, search (`/`), and navigation. The built-in scrollable pager wraps long lines to terminal width (CJK-aware, ANSI-aware) and re-wraps on terminal resize. Lines are never truncated — content that exceeds terminal width flows to the next visual row.

### 5.8 Cross-Platform

| Feature | Windows | macOS | Linux |
|---------|---------|-------|-------|
| Esc detection | msvcrt char-by-char | Ctrl-C fallback | Ctrl-C fallback |
| Arrow key handling | `\x00`/`\xe0` prefix consumed | N/A (raw mode not used) | N/A |
| Pager | less > scrollable builtin (ANSI) > simple builtin | less > more > builtin | less > more > builtin |
| Clipboard | `clip` (UTF-16LE) | `pbcopy` | `xclip` > `xsel` |
| Path display | `D:\path` decoded from `D--path` | `/path` decoded | `/path` decoded |

---

## 6. Module Map

```
extract --list    → listing.interactive_list()
                    ├─ Stage 1: listing._stage_projects()
                    ├─ Stage 2: listing._stage_sessions()    [pager, colored, batch]
                    └─ Stage 3-4: browser.browse_session()   [shared]
                        ├─ browser._stage_message_list()     [pager]
                        └─ browser._stage_message_view()     [pager]

extract --delete  → manager.delete_by_identifier()           (direct)
                  → manager.clean_interactive()               (interactive)
                    ├─ Stage 1: listing._stage_projects()     [shared]
                    └─ Stage 2-3: manager loop
                        ├─ manager._format_session_list()     [pager]
                        └─ manager._parse_session_numbers()

extract --find    → search.find_interactive()
                    ├─ Stage 1: search._stage_find_projects()
                    ├─ Stage 2: search._stage_find_sessions() [pager, colored, batch]
                    └─ Stage 3-4: browser.browse_session()    [shared]
                        ├─ browser._stage_message_list()      [pager]
                        └─ browser._stage_message_view()      [pager]

Shared primitives (browser.py):
  _read_line / _read_line_win    — Esc-aware terminal input
  _pager                         — scrollable text display (less > builtin)
  _scrollable_pager_win          — Windows full-screen pager (arrow keys, ANSI)
  _simple_pager                  — fallback page-by-page pager
  _parse_ids                     — space-separated number parsing
  _parse_extract_input           — number parsing with -o flag
  _copy_to_clipboard             — cross-platform clipboard
  _save_messages_to_file         — markdown file output

Noise filtering (message.py):
  is_noise_message               — prefix-based + XML residue check
  NOISE_PREFIXES                 — maintained list of system noise patterns

Data loading:
  listing.get_project_dirs()     — enumerate projects with metadata
  listing.get_sessions_for_project() — enumerate sessions with previews
  loader.load_messages()         — parse JSONL into merged messages
  searcher.search_for_display()  — search with per-message match context
```
