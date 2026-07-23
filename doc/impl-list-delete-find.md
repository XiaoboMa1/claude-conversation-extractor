# Implementation Notes: Pager & Display Fixes

Four changes to the interactive commands' terminal display and navigation, in `browser.py`, `search.py`, and `listing.py`.

---

## 1. Plan B Display Formatting (`--find` Stage 2)

**Problem**: Session numbers, match labels, and raw message content all used plain text with indentation. When messages contained numbered lists or markdown headings, structural elements were indistinguishable from content.

**Solution**: Three visual layers, each with a distinct marker.

### Layer mapping

| Layer | Marker | ANSI codes |
|-------|--------|------------|
| Session header | `━━` rule line | Bold + cyan (`\033[1m\033[36m`) |
| Match label | `match N  speaker:` on its own line | Yellow (`\033[33m`) + green/magenta speaker |
| Content | `│` gutter prefix | Dim (`\033[2m`) gutter, plain text |

### ANSI constants (browser.py)

```
_C_RESET   = \033[0m
_C_BOLD    = \033[1m
_C_DIM     = \033[2m
_C_CYAN    = \033[36m
_C_YELLOW  = \033[33m
_C_GREEN   = \033[32m   (user)
_C_MAGENTA = \033[35m   (assistant/claude)
```

### Format structure

```
{BOLD+CYAN}━━ N. session_id (name) — X match(es) ━━━━{RESET}
  {YELLOW}match 1{RESET}  {GREEN}user{RESET}:
  {DIM}│{RESET}  context line 1
  {DIM}│{RESET}  context line 2
  {YELLOW}match 2{RESET}  {MAGENTA}assistant{RESET}:
  {DIM}│{RESET}  bare match line
                                          ← blank line between sessions
{BOLD+CYAN}━━ N+1. next_session ━━━━━━━━━━━━━━━━━━━{RESET}
```

### Key design choices

- Match label and content are on **separate lines** (previously on the same line). This prevents `match 1: user: 1. some content` where the content's `1.` looked like a structural number.
- The `│` gutter character never appears naturally in message content. Anything right of `│` is unambiguously content.
- Session header uses `━` (box-drawing heavy horizontal) instead of `=` or `-`, which can appear in messages.
- Colors degrade gracefully: without ANSI support, the `│` gutter alone provides sufficient boundary distinction.

### Changed function

`search.py:_format_session_matches()` — complete rewrite of formatting logic. Imports color constants and `_display_width` from `browser.py`.

---

## 2. Stage 3 Message Preview → Pager (Issue 2)

**Problem**: `_stage_message_list()` used `print()` to output message previews directly to stdout. No pager — arrow keys had no effect. Stage 2 used `_pager()` and worked fine.

**Solution**: Collect preview output into a string, pass through `_pager()`.

### Changed function

`browser.py:_stage_message_list()`:
- Before: `print()` loop for each message preview
- After: builds formatted string with colored `ID N: Role` labels, passes to `_pager()`

### Color scheme (message preview)

- `ID N` in bold (`\033[1m`)
- `User` in green (`\033[32m`), `Claude` in magenta (`\033[35m`)
- Preview content lines: plain (no color)

---

## 3. Pager Line Wrapping (Issue 3)

**Problem**: `_draw_pager_frame()` truncated lines using `len(line)` (Python character count), which doesn't account for CJK characters (2 display columns each) or ANSI escape sequences (0 display columns). Lines were cut too early on narrow terminals and CJK content was misaligned.

**Solution**: Pre-wrap lines to terminal display width before rendering.

### New functions (browser.py)

**`_display_width(text)`**: Counts terminal columns. Iterates character-by-character, skipping `\033[...m` sequences (zero width), counting CJK `F`/`W` characters as 2, everything else as 1.

**`_wrap_lines_for_display(lines, width)`**: Splits logical lines into display rows that each fit in `width` columns. ANSI-aware:
- ANSI escape sequences pass through at zero width
- When a row breaks, `\033[0m` (reset) is appended to the end
- Active ANSI codes are re-applied at the start of the next row
- Tabs expanded to 4 spaces before wrapping

### Changed functions

**`_scrollable_pager_win(lines)`**:
- Stores original lines as `raw_lines`
- Wraps to `display_lines` on first frame and on terminal width change
- Uses `display_lines` for offset/height calculations

**`_draw_pager_frame()`**:
- Removed line truncation (`line[:width-1]`)
- Uses absolute cursor positioning (`\033[row;1H`) instead of `\n` to avoid double-line issues with full-width rows

### `_pager()` changes

- Skips `more` on Windows (lacks arrow key support)
- Falls back to `_scrollable_pager_win` (alternate screen buffer, msvcrt input) or `_simple_pager` (old page-by-page behavior)
- Checks `returncode == 0` (not `is not None`) so a failed `less`/`more` falls through to the built-in pager

### Removed dead code

`browser.py:_print_preview()` — leftover from the pre-pager version (used `print()` directly). No callers remained after `_stage_message_list` was rewritten to build a string for `_pager()`.

---

## 4. Esc Navigation — Back Instead of Exit (Issue 4)

**Problem**: Pressing Esc at stage 3 (message preview) or stage 4 (full view / extract) exited the program entirely. The `while True` loops in `browse_session()` and `_stage_find_messages()` had a `return` after `_stage_message_view()`, and the callers (`interactive_list()`, `find_interactive()`) also returned immediately after the stage 3 function completed.

**Expected**: Esc goes back one stage — stage 3 → stage 2, stage 2 → stage 1, stage 1 → exit.

**Solution**: Two structural changes to the control flow.

### Change 1: Remove early `return` after stage 4

`browse_session()` and `_stage_find_messages()` both had:

```python
while True:
    ids = _stage_message_list(messages)
    if ids is None:
        return          # Esc → back to caller ✓
    _stage_message_view(messages, ids, path)
    return              # ← exits after view, never loops back
```

Removed the final `return`. Now Esc or completion in stage 4 loops back to stage 3.

### Change 2: Add inner loop for stage 2

`interactive_list()` and `find_interactive()` had stage 2 in the same loop as stage 1. After `browse_session()` / `_stage_find_messages()` returned, the outer loop's `return` exited the program.

Added an inner `while True` loop around stage 2. Stage 3 returning now re-enters stage 2 (session list), not stage 1.

### Changed functions

| Function | File | Change |
|----------|------|--------|
| `browse_session()` | `browser.py` | Removed `return` after `_stage_message_view()` |
| `_stage_find_messages()` | `search.py` | Removed `return` after `_stage_message_view()` |
| `interactive_list()` | `listing.py` | Added inner `while True` loop for stage 2 |
| `find_interactive()` | `search.py` | Added inner `while True` loop for stage 2 |

### Resulting navigation

```
--list:
  Stage 1 ──select──→ Stage 2 ──select──→ Stage 3 ──select──→ Stage 4
     ↑ Esc exits        ↑ Esc=back          ↑ Esc=back          │ Esc=back
                         └───── browse_session returns ──────────┘

--find:
  Stage 1 ──select──→ Stage 2 ──select──→ Stage 3 ──select──→ Stage 4
     ↑ Esc exits        ↑ Esc=back          ↑ Esc=back          │ Esc=back
                         └── _stage_find_messages returns ───────┘
```
