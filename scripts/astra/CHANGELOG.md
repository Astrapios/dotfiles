# Changelog

All notable changes to astra (formerly tg-hook) are documented here.

Versioning: **MINOR** (0.X.0) for new user-facing features (commands, APIs).
**PATCH** (0.0.X) for bug fixes, refactors, and test/docs-only changes.

## 0.29.0

- **Mock layer PR2 — `MockTransport` + JSONL capture (`astra listen --mock`).** New `astra.tg_mock` module with a `MockTransport` class that intercepts all Telegram I/O, forwards to real Telegram by default, and records every call to a JSONL file with bot tokens stripped and chat IDs replaced by `<CHAT_ID>`/`<DOC_CHAT_ID>`. Message text is kept verbatim. Default capture path: `/tmp/astra_capture/<iso8601>.jsonl`. Activate via `astra listen --mock` or `ASTRA_MOCK=1`.
- **New CLI: `astra mock status|recent [N]|dump [path]`.** Inspect captured Telegram traffic — `status` shows the latest capture, `recent` prints a one-line-per-record summary, `dump` outputs the full JSONL (latest or a given path).
- **Retire `_debug_tg`.** The Telegram I/O trace duty (SEND, RECV, CALLBACK, DOC, PHOTO, KB) is now handled by the JSONL capture above. `_debug_log` (internal listener observability — smartfocus diffs, stop-signal capture) is unchanged. `astra debug on/off` keeps working but now only toggles internal traces; for Telegram traffic logging use `astra listen --mock`.
- **Safety tag**: `pre-mock-pr2`.

## 0.28.1

- **Refactor: unified `tmux_send` API.** All `tmux send-keys` invocations (previously scattered across `routing.py`, `listener.py`, `commands.py`, `cli.py`) now route through a single `astra.tmux_send` module. Centralizes sleep schedule constants (`_AFTER_ESCAPE`, `_AFTER_TYPE`, `_AFTER_TYPE_INJECT`, `_BETWEEN_KEYS`) so timing changes no longer require touching multiple files. Behaviour-preserving. Adds 25 new unit tests; total 974 passing.

## 0.28.0

- **`!` prefix for injecting into busy sessions** — `!w0 focus on the API` sends Esc + types the instruction + Enter, adding an "additional instruction" mid-task instead of queuing. Works with `!wN`, `!N` shorthand, session names, and single-session fallback.
- **Fix incomplete stop responses** — stop hook now captures the full response including interleaved text and tool calls. Previously only captured the last text section when a response had multiple `●` text bullets separated by tool calls.
- **Fix response text falsely filtered as spinner** — `●` response bullets containing time references like `(2m)` were being stripped by the timing indicator filter (`\d+[hms]`). Now excludes `●` and `❯` from the spinner character class, consistent with the ellipsis spinner filter. Also adds debug logging to stop signal processing.
- **Fix Gemini busy detection** — Gemini's `>` prompt is always visible (part of the fixed UI layout), so the busy indicator "esc to cancel" appears above it, not below. Now pre-scans all captured lines for the busy indicator instead of only checking lines below the prompt. Also adds `✦` (Gemini response bullet) to content indicators that signal a busy session.

## 0.27.2

- **Fix empty stop messages for long responses** — when tool call outputs push the text `●` bullet beyond the capture range, the stop hook now captures up to 500 lines (was 200) and falls back to showing the last 30 content lines before the prompt.
- **Filter satisfaction survey from stop output** — "How is Claude doing this session?" survey and its rating options are stripped from both stop messages and focus/smartfocus content.
- **Skip trivial smartfocus deltas** — single-emoji or symbol-only deltas (no alphanumeric content) are no longer sent as smartfocus messages.

## 0.27.1

- **Fix smartfocus duplicate sends** — when fast-scrolling output causes zero overlap between captures, the full content was returned as "new" every tick, producing repeated identical messages after tool-call collapse. Now deduplicates: skips sending if the collapsed text matches the previous send. Applies to both focus and smartfocus.
- **Fix false-busy idle detection** — `_pane_idle_state` now tolerates up to 4 unrecognized UI lines below the prompt instead of failing on the first unknown line. Content indicators (`●`, `⎿`) still immediately signal busy (old prompt). Adds chrome patterns for shell hints (`1 shell · ↓ to manage`) and text status bars (`──── branch ──`). Removes fragile text-based `✻` pattern in favor of existing timing/color detection.

## 0.27.0

- **`/local off` auto-attaches smartfocus** — when autofocus is enabled and no focus is active, `/local off` automatically attaches smartfocus to a busy session (prefers last active window).
- **Fix remote detection with ttyd** — ttyd keeps a tmux client always attached, making all windows appear "locally viewed". Now tracks global Telegram activity timestamp; if the most recent interaction is via Telegram, local suppress is disabled for all windows.
- **Reply keyboard persistence** — `/help` and `/unfocus` now re-send the reply keyboard to prevent it from disappearing.
- **Fix incomplete smartfocus output** — tool output lines were dropped when `⎿  Running…` spinners replaced by actual output (1:1 replace missed by diff). Now filters `Running…` as noise so output appears as inserts. Also fixes `(ctrl+o to see all)` not being filtered.
- **Stop hook always sends full response** — smartfocus stop no longer computes a delta against previous lines. Always sends the complete last response for a coherent summary.
- **Noise filter improvements** — filter `⎿  Tip:` lines, bare `Shell` headers, `ctrl+b background` hints, and status bar lines with branch names (e.g. `──── branch-name ──`). Fix `_focus_capture_lines` stripping order to remove trailing chrome after prompt.
- **Fix table data rows stripped** — spinner/timer filter (`[^\w\s] \w` + `\d+[hms]`) matched table data rows starting with `│` that contained meter values like `0m`, `5.0m`. Excluded box-drawing vertical chars (`│┃║`) from spinner patterns.

## 0.26.4

- **rtk rewrite integration** — god mode now rewrites Bash commands via `rtk rewrite` for compact output (when rtk is installed). Hook output migrated to `hookSpecificOutput` format (replaces deprecated `{"decision":"approve"}`).

## 0.26.3

- **`sw` alias for shell commands** — `sw4 git status` sends `!git status` to session w4. Also works with named sessions: `sauth git status` sends `!git status` to the session named "auth".

## 0.26.2

- **Always-on message log** — all outbound Telegram messages (SEND, DOC, PHOTO) are logged as JSON lines to `/tmp/astra_messages.jsonl` with full untruncated text, timestamp, kind, and msg_id. Auto-truncates at 1 MB. Independent of `astra debug on/off`.

## 0.26.1

- **Fix table data stripped from messages** — `_filter_noise` and `_join_wrapped_lines` treated indented table rows starting with `│` as wrapped prompt continuations and dropped them. Added all box-drawing characters to the "keep" regex so table cell values are preserved in stop messages, focus output, and rendered table images.

## 0.26.0

- **Secondary bot token for document/photo sending** — `send-doc` and `send-photo` can route through a separate bot token (`TELEGRAM_DOC_BOT_TOKEN` + `TELEGRAM_DOC_CHAT_ID` in `astra.env`), useful for Obsidian Telegram sync or other plugins polling a dedicated bot. Falls back to the main bot when unset. Use `--main` flag to force the primary bot.

## 0.25.6

- **Fix `/status` stripping in-progress work** — status used stop-mode content filtering which stripped spinners, task lists, and timing indicators, showing only previous completed `●` bullets. Now uses `clean_pane_status` (keep_status=True) with progressive capture so current work is always visible

## 0.25.5

- **Fix false table detection on tool call tree and prose** — `_has_table()` triggered on any box-drawing char (`│`), including tool call tree indentation and `│` in prose text. Now requires 3+ vertical bars on a line (real table row), horizontal rules with corners, or pipe-delimited rows

## 0.25.4

- **Fix Gemini session detection when busy** — `pane_title_pattern` only matched idle Gemini (`◇  Ready`) but not busy (`✦  Working…`) or action-required (`✋  Action Required`) states, causing Gemini sessions to disappear from `/status` while working
- **Fix stale session resolution after pane exit** — when a multi-pane window lost a pane (e.g. Gemini exits from w1), bare `w1` became ambiguous against the cached `w1a`+`w1b` until the next 60s rescan. `_resolve_name` now rescans on miss, fixing all wid-targeted commands (`/status`, `/focus`, `/interrupt`, etc.)

## 0.25.3

- **Fix suggestion capture with ANSI dim detection** — rewrite `_extract_suggestion` to use ANSI escape code detection instead of cursor position. Claude Code renders suggestion (ghost) text with dim attribute (`ESC[2m`); we now capture with ANSI codes and only extract text that is visually dim, avoiding false positives from submitted prompt text
- **Fix stop content after smartfocus** — the stop handler was comparing smartfocus-format lines against stop-format lines (different cleaning pipelines), producing empty deltas. Now uses `_focus_capture_lines` on both sides for apples-to-apples comparison

## 0.25.2

- **Fix suggestion capture** — guard against transient `cursor_x=0` state that captured the prompt char (`❯`) as a false suggestion; fix duplicate label in suggestion message (`w3 [proj] w3 [proj]` → `w3 [proj]`)

## 0.25.1

- **Show suggestion text after stop** — when Claude finishes and shows a grey auto-suggestion in the prompt (e.g. "Fix the imports in utils.py"), forward it to Telegram with a "Send" button. Clicking "Send" routes the suggestion text to the session. Sending your own message clears the suggestion button.

## 0.25.0

- **Unified focus/smartfocus pipeline** — focus and smartfocus now share the same content processing: `_focus_capture_lines` (filter noise → strip prompt → wrap) → diff → strip dialog → collapse → send immediately. Smartfocus is now just automatic activation of focus. Removed pending buffer and bullet-aware batching (no more delayed sends)
- **`astra smartfocus` CLI command** — activate (`smartfocus wN`), deactivate (`smartfocus off`), or query (`smartfocus`) smartfocus directly from the terminal

## 0.24.6

- **Fix smartfocus missing text and false idle** — permission dialog content (`Bash command`, `Do you want to proceed?`, option lines) is now stripped from captures via `_strip_dialog()` before diffing; idle detection checks for busy indicator (`esc to interr`) and dialog footers (`Esc to cancel`) as strong NOT-idle signals; `_compute_new_lines` now includes net-new lines from "replace" operations (not just "insert"); added `config._debug_log()` for verbose debug output and `astra debug smartfocus wN` CLI for step-by-step pipeline diagnostics

## 0.24.5

- **Focus mode icons in `/status`** — sessions show 👁‍🗨 (smartfocus), 🔍 (focus), or 🔬 (deepfocus) when being monitored

## 0.24.4

- **Fix smartfocus/deepfocus missing text content** — increased pane capture window from 50 to 200 lines so Claude's text responses aren't lost when long tool output scrolls them off-screen between ticks

## 0.24.3

- **God mode covers all tools** — added PreToolUse hooks for Read, WebFetch, WebSearch, Glob, Grep, NotebookEdit, and Task so god mode auto-approves them; tool\_map now includes descriptive labels for all tools; god mode log shows URL for fetch, query for search, pattern for glob/grep, description for task

## 0.24.2

- **Fix smartfocus stop repeating content** — stop signal for a smartfocus session now sends only the delta (new content since last smartfocus update + any unflushed pending) instead of repeating the full response that smartfocus already sent; sends short "finished" when there's nothing new
- **Focus mode logging** — focus, smartfocus, and deepfocus sends now log to journal (`config._log`) with line counts and flush reasons (idle/bullet/timeout/debounce/max\_delay) for easier debugging

## 0.24.1

- **CPU and RAM in `/status`** — each session shows CPU% and memory usage of its full process tree; system summary line shows total CPU, system RAM used/total, and aggregate session memory
- **Bare `/autofocus` shows busy session picker** — instead of toggling, bare `/autofocus` now shows an inline keyboard of currently busy sessions to pick which one to watch; also adds `/autofocus wN` to attach directly to a specific session; when no sessions are busy, shows current autofocus status

## 0.24.0

- **Fix focus mode sending full response on every change** — `/focus` now uses diff-based tracking (like smartfocus) to send only new lines instead of re-sending the entire response every time content changes; first tick establishes a baseline without sending
- **Autofocus on auto-attaches to busy session** — toggling `/autofocus on` (or bare `/autofocus` toggle from off→on) now automatically attaches smartfocus to a currently busy session if one exists; prefers `last_win_idx` when multiple sessions are busy
- **Smartfocus bullet-aware batching** — smartfocus accumulates new lines in a pending buffer instead of sending immediately; flushes on bullet boundary (text `●` signals previous bullet is complete), response completion (prompt char detected), or 5-second timeout with no new content; reduces fragmented mid-paragraph updates

## 0.23.1

- **Fix multi-question prompts lost on listener restart** — `cmd_listen` startup cleared `_active_prompt_*` files, so any pending AskUserQuestion (especially multi-question flows in plan mode) was lost when the listener auto-reloaded; prompts now persist across restarts and `_cleanup_stale_prompts` handles expired ones
- **Fix question callback `[0]` indexing on SessionInfo** — the `q_{wid}_{n}` callback handler used `sessions[resolved][0]` which broke after the SessionInfo migration; fixed to use tuple unpacking
- **Add inline keyboard buttons to follow-up questions** — `_advance_question` now sends option buttons for Q2, Q3, etc. (previously only Q1 had buttons)
- **Multi-question `debug inject`** — `astra debug inject question wN --multi` injects a 3-question signal for testing the full multi-question flow

## 0.23.0

- **Debug subcommands** — new diagnostic tools under `astra debug` for inspecting and testing the listener without touching Telegram
  - `astra debug state [wN]` — dump internal state: sessions, prompts, busy flags, focus, god mode, queued messages, pending signals; detail view with `wN` argument
  - `astra debug inject <event> <wid> [args]` — inject fake signals (`stop`, `perm`, `question`) for testing signal processing without real CLI hooks
  - `astra debug tick` — dry-run one listener tick against real tmux state with intercepted Telegram I/O; prints formatted output with keyboard layout, duplicate callback detection, and Markdown V1 safety check
- **Enhanced debug log** — `astra debug on` now also logs inline keyboard button details (`KB [Label:cb_data]`), inbound messages (`RECV text`), and button presses (`CALLBACK cb_data`)

## 0.22.1

- **Fix stale bash command in non-bash permissions** — auto-approved shell commands left `_bash_cmd_{wid}.json` files that polluted the next Write/Edit permission with the old bash command body; non-shell PreToolUse now cleans up stale files
- **Fix 2-option permission keyboard** — permissions with only 2 options (Yes/No) had "Always" and "Deny" mapped to the same callback (`perm_{wid}_2`); now shows only Allow/Deny buttons when `n < 3`
- **Fix Markdown V1 breakage on underscore options** — numbered options containing underscores (e.g. `/tmp/test_perms`) broke Telegram's Markdown parser; `opts_text` is now wrapped in code blocks at all 4 sites
- **Fix stop message showing only tail** — smartfocus stop messages used `_compute_new_lines()` which showed only unseen lines; now always sends full collapsed content as a summary notification

## 0.22.0

- **Deduplicate stop signals** — multiple Stop events for the same session in a single tick are now collapsed into one notification, fixing duplicate "✅ finished:" messages when rapid tool-use turns fire several stops
- **Collapse tool calls in focus output** — smartfocus (👁), focus (🔍), and stop (✅) messages now show compact `🔧 ToolName(args)` headers instead of full tool call bodies, reducing noise while preserving text output
- **Plan permission shows plan file content** — ExitPlanMode permission reads the plan file from `~/.claude/plans/` and sends the full plan text with Approve/Always/Deny buttons and numbered options, instead of extracting (often incomplete) pane content
- **Fix deepfocus profile awareness** — deep focus monitoring now uses the correct CLI profile for `_filter_noise` and prompt character detection instead of hardcoding Claude's `❯`

## 0.21.5

- **Fix false busy detection on tall panes** — `_capture_pane` now strips trailing empty lines before taking the last N, fixing idle detection failure when a pane has few content lines but many blank lines below (e.g. after `/clear`)

## 0.21.4

- **Auto-local override on all TG interactions** — any Telegram interaction targeting a session (keys, interrupt, kill, restart, permission/question responses, quick-pick keys) now disables local suppress for that window, not just text/photo/doc sends

## 0.21.3

- **Fix `/status` local icons with auto-local** — `/status` now applies remote override subtraction so the `👁` icon correctly reflects whether local suppression is active (not shown for windows with a pending TG override)
- **`/keys` always shows session picker** — bare `/keys` with multiple sessions now always prompts "which session?" instead of auto-selecting the last-used session

## 0.21.2

- **Fix reply-to routing** — replying to an astra message now correctly routes to the session even when the displayed wid (`w4`) differs from the session key (`w4a`); uses `resolve_session_id` instead of a direct dict lookup
- **Update persistent keyboard** — replaced `/last`, `/saved`, `/focus`, `/help` with `/keys`, `/god`, `/saved`, `/last` to match most-used commands

## 0.21.1

- **Fix permission prompt formatting** — permission notifications now always read the saved bash command file, fixing cases where `bash_cmd` was empty (e.g. git commit) causing the full command to be shown twice in the Telegram message

## 0.21.0

- **God mode mid-permission** — enabling `/god wN` or `/god all` while a permission dialog is already pending now immediately auto-accepts the pending prompt instead of waiting for the next one
- **Auto-local detection** — when a Telegram message is sent to a locally-viewed session, local suppression is temporarily disabled for that window so you see its notifications; returning to tmux (keyboard activity) re-engages local suppress automatically

## 0.20.0

- **Render table as image** — messages containing ASCII/Unicode tables get a `🖼 As image` inline button; tapping it renders the code block as a crisp PNG via Pillow and sends it as a photo, fixing unreadable wrapped tables on mobile
  - Detects box-drawing characters (`│┌┐└┘├┤┬┴┼─━║╔╗╚╝╠╣╦╩╬`) and pipe-delimited rows (`| col | col |`)
  - Rendering tool at `~/pixi_tools/imgcat/` (DejaVuSansMono 18px on dark background)
- **Fix photo/doc name routing** — photo and document captions now resolve session names (e.g. `myname describe this`) in addition to `wN` prefixes

## 0.19.2

- **Prune god mode for closed sessions** — god mode wids are now automatically cleaned up when sessions disappear, preventing stale god mode state

## 0.19.1

- **Bare `/keys` quick-pick combo buttons** — `/keys` or `/keys wN` without key args shows an inline keyboard with common key combos (Shift+Tab, Ctrl+C, Escape, Ctrl+O, Enter, Up)
  - Single session or last-used auto-selects; multiple sessions shows session picker first
  - `k` alias for bare `/keys`, `k5` for `/keys w5`

## 0.19.0

- **`/keys` command** — send modifier keys and key combinations to sessions from Telegram or CLI
  - `/keys w4 shift+tab` — send Shift+Tab (cycle permission mode)
  - `/keys w4 ctrl+c` — send Ctrl+C
  - `/keys w4 down down enter` — send multiple keys in sequence
  - Supports human-readable names: `shift+tab`, `ctrl+X`, `esc`, `enter`, `space`, arrow keys, `f1`–`f12`, etc.
  - Raw tmux key names (e.g. `BTab`, `C-c`) also work as pass-through
  - CLI: `astra keys <wN> <key...>`
  - Alias: `k5 shift+tab` → `/keys w5 shift+tab`

## 0.18.1

- **Auto-setup for new sessions** — `astra new` now auto-accepts trust dialogs and switches out of plan mode so sessions are immediately usable from Telegram

## 0.18.0

- **Debug log for outbound Telegram messages** — opt-in transient debug mode that logs every `tg_send`, `tg_send_photo`, and `tg_send_document` call to `/tmp/astra_debug.log`
  - `astra debug on` / `astra debug off` — enable/disable (off deletes log)
  - `astra debug [N]` — show status and last N log lines (default 20)
  - `astra debug clear` — clear log file without disabling
  - Log format: `[timestamp] SEND/PHOTO/DOC detail | text`
  - Auto-truncates at 500KB

## 0.17.0

- **Full CLI subcommands** — all Telegram commands now have local CLI equivalents that work without Telegram credentials:
  - **Config:** `astra god`, `astra local`, `astra autofocus`, `astra notification` — manage global settings; no args shows current state
  - **Session:** `astra status [wN] [lines]`, `astra focus [wN]`, `astra deepfocus [wN]`, `astra unfocus`, `astra interrupt [wN]`, `astra clear [wN]`, `astra name [wN] [label]`, `astra saved [wN]` — inspect and manage sessions
  - **Management:** `astra new [claude|gemini] [dir]`, `astra restart <wN>`, `astra kill <wN>` — session lifecycle
  - **Debug:** `astra log [N]` — show listener journal lines

## 0.16.6

- **God mode quiet/loud toggle** — `/god quiet` (alias `gq`) suppresses god mode receipt messages on Telegram; `/god loud` (alias `gl`) re-enables them; bare `/god` status shows "(quiet)" when suppressed

## 0.16.5

- **God mode via PreToolUse hooks** — all PreToolUse hooks (Bash, Read, Edit, Write) output `{"decision": "approve"}` in god mode, bypassing Claude Code's permission dialog; each writes a `god_approve` signal with the tool type so the listener sends descriptive receipts: `⚡ Ran`, `⚡ Read`, `⚡ Edited`, `⚡ Wrote`
- **Read/Edit/Write hooks** — added PreToolUse hooks and profile tool mappings for Read, Edit, and Write in `claude_settings.json`
- **Revert listener sleep optimization** — removed 100ms signal-polling loops and TG poll skip from 0.16.4 (no longer needed)

## 0.16.4

- **Speed up god mode auto-accept** — non-critical Telegram calls run in background daemon threads via `_fire_and_forget()`; listener loop skips the ~500ms TG poll after processing signals; idle sleep (2s) replaced with 100ms signal-checking loop so new permission signals are picked up within ~100ms instead of waiting up to 2s
- **Fix missed god mode auto-accepts for bare wids** — `_is_god_mode_for("w4")` now matches `"w4a"` in the god mode list, so hook signals arriving before session resolution are correctly auto-accepted
- **Rescan sessions on unresolved wid** — when a signal's wid can't be resolved to a known session, `process_signals` rescans tmux to pick up newly appeared panes
- **Migrate test runner to pytest** — switch from `unittest discover` to `python -m pytest`; add `pytest >= 7` dependency; fix pre-existing test isolation issue in `TestBareLastSessionPicker`

## 0.16.3

- **Skip dialog detection for god mode sessions** — god mode auto-accepts permissions via hooks, so the startup dialog scanner no longer scans those sessions; prevents false-positive dialog notifications during rapid god-mode command sequences

## 0.16.2

- **Fix god mode and signal routing in multi-pane windows** — hook signals use TMUX_PANE format (`%2`) but session scan used `session:window.pane` format (`main:1.0`); pane target matching never matched, so bare `wN` wids in multi-pane windows (e.g. Claude + Gemini in w1) couldn't resolve to the correct `wNa`/`wNb` suffix. Added `pane_id` field to `SessionInfo`, captured during scan, matched during signal processing.

## 0.16.1

- **Fix Gemini stop output capture** — stop handler, `/last` command, focus mode, and smartfocus all defaulted to Claude's profile (`●`/`❯`) when extracting response content; Gemini responses (`✦`/`>`) returned empty. Now pass the correct CLI profile throughout signal processing, content extraction, and monitoring paths.

## 0.16.0

- **Startup dialog detection** — periodically scan all CLI sessions for numbered-option dialogs that appear before hooks are active (e.g. Gemini "trust folder" prompt); forward to Telegram with inline buttons and route replies via the existing active prompt mechanism
- CLI-agnostic: detects dialogs in any session that is not idle AND not marked busy (defense-in-depth for hook failures)
- Custom confirmation labels: `perm_` callback uses option text from the dialog (e.g. "Trust this folder") instead of generic "Allowed"/"Denied"
- 10-second debounce prevents false positives: normal permission dialogs handled by hooks within 2–3s are ignored; only dialogs persisting 10s+ trigger a notification
- `has_active_prompt()` non-destructive check in state.py; `_detect_numbered_dialog()` in content.py; `dialog_notified` / `dialog_first_seen` in `_ListenerState`
- **EnterPlanMode is now informational** — send "entered plan mode" notification instead of stale Approve/Deny buttons (Claude Code auto-approves EnterPlanMode; the real plan approval comes via ExitPlanMode as a permission event)
- **Fix `/restart` for pane-less CLIs** — when Ctrl+C kills a CLI that was the pane's initial command (e.g. Gemini started via `/new`), the pane closes; `/restart` now detects the dead pane and creates a new window instead of failing with "pane may have closed"

## 0.15.6

- **Fix plan approval dialog detected as idle** — `_pane_idle_state` no longer treats `❯ 1. Yes, clear context...` (numbered option lines in plan approval / AskUserQuestion dialogs) as an idle prompt; prevents stale prompt cleanup from deleting active prompts mid-dialog
- **Add free text support to permission handler** — ExitPlanMode (plan approval) is handled as a permission signal; detect "Type here/something/your" options and set `free_text_at` so users can type feedback instead of only using buttons; add numeric shortcuts for all options and a hint in the Telegram message

## 0.15.5

- **Fix photo/document routing with bare wids** — `w3` in photo/document captions now resolves to `w3a` via `resolve_session_id` instead of failing direct session lookup
- **Fix `/new` returning bare wid** — `/new` now resolves `w5` → `w5a` after session scan so `last_win_idx` matches the actual session key
- **Fix `/restart` CLI detection** — save CLI profile before killing the session instead of looking it up after (when it's already gone); Gemini sessions now correctly restart with `gemini -r latest`
- **Fix `/new` session detection for Gemini** — retry scan up to 6s after `tmux new-window` to wait for Node.js-based CLIs to start (Gemini takes a few seconds before `pane_current_command` becomes `node`)

## 0.15.4

- **Fix Gemini idle detection** — `_pane_idle_state` now uses the correct CLI profile per pane instead of always defaulting to Claude; Gemini prompt (`>`), busy indicator (`esc to cancel`), and UI chrome (decorative bars, status bar) are properly recognized
- `_profile_for_pane()` looks up the CLI profile from `_current_sessions` by pane target
- Gemini-specific `_is_ui_chrome` patterns: `▀▀▀`/`▄▄▄` bars, status bar, hint line

## 0.15.3

- **Always-suffix session IDs** — solo panes now get `w4a` suffix instead of bare `w4`, ensuring consistent addressing across solo and multi-pane windows
- Bare `wN` in user commands resolves to `wNa` when solo, returns ambiguous (None) when multi-pane
- Bare `3a` (w-prefix stripped by command regexes) resolves to `w3a` — fixes `s3a` alias and `/status w3a`
- Signal wids from hooks (bare `wN`) resolved to actual session key via pane target matching
- God mode normalizes stored wids to always-suffixed format (`w4` → `w4a` on load/write)
- Display shows clean `w3` for solo panes, `w1a`/`w1b` only for multi-pane windows (status, buttons)
- `_wid_label` accepts optional sessions for display-friendly wids in all notifications/headers
- `format_sessions_message` uses pane-count-per-window for multi-pane detection instead of suffix presence

## 0.15.2

- Fix god mode check after wid migration — bidirectional normalization (`w4` ↔ `4`)
- Fix `/status` passing bare window index to god mode check instead of full wid
- Fix god mode status sorting for `wN` format wids
- Normalize bare god mode entries to `wN` format on read and write
- Show per-session detail (wid, project, CLI type) in listener startup log

## 0.15.1

- Always show "Active sessions" header (not "Active Claude sessions")
- Show CLI type (Claude/Gemini) per session when multiple CLIs present
- Fix multi-pane name inheritance — `w1a`/`w1b` no longer inherit bare window-level names
- `scan_claude_sessions()` returns `SessionInfo` objects preserving CLI metadata
- Migrate session dict keys to full wid format (`w4`, `w1a`) throughout codebase

## 0.15.0

- **Multi-CLI support** — add Gemini CLI alongside Claude Code with full hook/routing parity
- Add `CLIProfile` registry (`profiles.py`) with UI patterns, event/tool name mappings per CLI
- Add `SessionInfo` dataclass and `scan_cli_sessions()` for type-aware session scanning
- Add `resolve_session_id()` with bare `w4` → `w4a` fallback for multi-pane windows
- Update all wid regexes to accept optional letter suffix (`w4a`, `w4b`) for multi-pane routing
- Hook normalization: map Gemini events (`AfterAgent`→stop, `BeforeTool`→pre\_tool) and tools (`run_shell_command`→shell) to internal names
- All content/routing parsing functions accept optional `profile` parameter for CLI-specific patterns
- Dynamic display names in Telegram messages (shows "Gemini" instead of "Claude Code" for Gemini signals)
- `/new` command accepts optional CLI type: `/new gemini [dir]`
- `/restart` uses profile-specific restart command (`gemini -r latest` for Gemini)
- Add `gemini_settings.json` hook config and `install.zsh` Gemini setup
- Detect Gemini via `#{pane_title}` (shows `◇  Ready`) since `pane_start_command` is empty
- Migrate session dict keys from bare indices (`"4"`) to full wid format (`"w4"`, `"w1a"`) throughout codebase
- Gemini detection via `pane_title` fallback (pane\_start\_command is empty; uses `◇` diamond or "Gemini" in title)
- Gemini UI patterns discovered from live session: `✦` response bullet, `esc to cancel` busy indicator, braille spinner, box-drawing tool calls
- 39 new tests for profiles, session IDs, hook normalization, display names, and multi-CLI simulation

## 0.14.2

- **Detect active spinner as busy signal** — capture pane with ANSI codes (`tmux capture-pane -e`) and detect non-grey colored spinner symbols (✢, ✶, ⠐, etc.) as a definitive busy indicator; fixes false idle detection when Claude is thinking but `esc to interrupt` hasn't appeared yet

## 0.14.1

- **Fix idle detection on narrow panes** — status line `esc to interr…` (truncated by tmux) was not recognized as busy, causing sessions to show as idle while Claude was actively running

## 0.14.0

- **Simulation test harness** — extract `_ListenerState` dataclass and `_listen_tick()` from `cmd_listen()` to enable integration testing of the listener loop without real Telegram/tmux
- Add `tests/sim/` package with `FakeTelegram`, `FakeTmux`, `FakeClock`, and `SimulationHarness` that replace I/O boundaries with stateful fakes while delegating pure functions to real implementations
- 18 simulation tests covering text routing, stop signals with smartfocus, permission flow, smartfocus content tracking, interrupt detection, and pause/resume

## 0.13.2

- **Fix smartfocus stopping mid-response** — replace `clean_pane_content("stop")` with `_filter_noise()` so smartfocus doesn't require the `●` marker (which scrolls off for long responses); always update prev\_lines to prevent state drift

## 0.13.1

- **Re-source shell config on `/restart`** — runs `source ~/.zshrc` (or `~/.bashrc`) before relaunching Claude, ensuring PATH and env vars are fresh
- **Hooks enabled by default** — remove `CLAUDE_ASTRA=1` opt-in; set `NO_ASTRA=1` to disable hooks for a session instead

## 0.13.0

- **Local view suppression** — auto-detect when a tmux client is viewing a Claude session and skip redundant Telegram notifications for that session
- Add `/local [on|off]` command to toggle local view suppression (default: on)
- Show 👁 indicator in `/status` and startup message for locally viewed sessions
- Alias: `lv` → `/local`
- When locally viewed: permissions, stops, questions, plan approvals, god mode receipts, interrupt/compact notifications are suppressed
- State management (busy, prompts, god mode actions) always runs regardless of local view
- Active prompt is still saved so Telegram fallback works if user switches away
- Log `[local]` tag on suppressed signals, interrupts, and compact notifications
- Show `Local suppress: on/off` at startup in listener log
- **Remove `/tmp/astra.log` file** — `/log` command now reads from `journalctl --user -u astra` instead of a separate log file; `_rotate_log()` removed

## 0.12.0

- **Optimize god mode latency** — send tmux accept keys *before* Telegram notification, skip unnecessary pane capture (~200-500ms faster)
- **Add timestamps and file logging** — `_log()` now prepends `[HH:MM:SS]` timestamps and tees to `/tmp/astra.log` with automatic rotation at 512 KB
- **Add `/log [N]` command** — view last N listener log lines from Telegram (default 30, max 100)
- God mode auto-accepts now appear in listener stdout/journal via `_log()`

## 0.11.0

- Add systemd user service for auto-start and crash recovery (`astra.service`)
- Add lock file (`/tmp/astra_listener.lock`) to prevent duplicate listener instances — uses `fcntl.flock`, inherited across `os.execv` auto-reload, auto-released on exit/crash
- `install.zsh` now installs and enables the astra systemd service

## 0.10.1

- **Fix idle detection failing on unrecognized hint lines** — status lines like `? for shortcuts` below the `❯` prompt weren't recognized as UI chrome, causing `_pane_idle_state` to return False for idle sessions (messages got queued instead of delivered)
- **Fix idle detection returning True for busy sessions** — `esc to interrupt` below the prompt now correctly signals that Claude is actively running
- **Fix stale busy files after listener auto-reload** — `_busy_` files are now cleared at startup alongside prompt files, preventing messages from being queued indefinitely when stop signals are lost during reload

## 0.10.0

- **Rename tg-hook → astra** (after Astrapios, the Lightning-Bringer)
  - CLI command: `tg-hook` → `astra`
  - Python package: `tg_hook` → `astra`
  - Directory: `scripts/tg-hook/` → `scripts/astra/`
  - Env var: `CLAUDE_TG_HOOKS` → `CLAUDE_ASTRA`
  - Config files: `~/.config/tg_hook.env` → `~/.config/astra.env` (old paths still work as fallback)
  - Signal dir: `/tmp/tg_hook_signals/` → `/tmp/astra_signals/`
- Add `/restart wN` command — kills a Claude session and relaunches with `claude -c` (continue last conversation) in the same pane
- Alias: `r4` → `/restart w4`
- Auto-compact detection — listener detects when Claude is auto-compacting context and sends ⏳/✅ notifications to Telegram

## 0.9.0

- Support receiving documents (PDF, text files, etc.) from Telegram — downloads and routes to Claude as `Read /tmp/tg_doc_xxx.ext — caption`
- Prompt for instructions when photo or document is sent without a caption — reply with text, ⏭ Skip, or 🗑 Cancel
- Accumulate multiple caption-less files into one prompt — send photos/documents one by one and they merge before routing
- Rename `_download_tg_photo` → `_download_tg_file` (the function was already generic)

## 0.8.1

- Batch album photos into a single `Read path1 path2 path3 — caption` instruction so Claude sees all images at once
- Photos sharing the same `media_group_id` are merged before processing
- Fix filename collision for simultaneous photos: use microsecond-precision timestamps with index suffix
- Fix album Enter not sent: increase delay before Enter for multi-photo instructions (0.5s vs 0.1s)
- Fix album instruction format: use `Read these images: path1, path2` with comma separators for clarity
- Fix smartfocus noise: filter spinner lines with `...` (three dots), not just `…` (Unicode ellipsis)
- Fix smartfocus noise: filter tool progress lines like `Reading 1 file… (ctrl+o to expand)` regardless of `●` prefix
- Increase send-keys Enter delay from 0.1s to 0.3s for text messages and single photos to prevent stuck prompts
- Fix smartfocus echoing user's prompt: filter `❯` lines in `_filter_noise` so prompt text never leaks into response content
- Fix smartfocus capturing garbage when no response boundary exists: `clean_pane_content("stop")` returns empty instead of including unrelated content
- Fix empty stop message when smartfocus sent noise: detect low-overlap prev vs response and send full content

## 0.8.0

- Add notification control: `/notification` command to configure which message categories buzz your phone
- Default: only permission (🔧) and stop (✅) messages are loud; all others are silent
- Categories: 1=permission, 2=stop, 3=question/plan, 4=error, 5=interrupt, 6=monitor, 7=confirm
- Use `noti 123` to set loud categories, `noti all` / `noti off` for all loud/silent
- Config persists in `~/.config/tg_hook_notifications.json`
- Alias: `noti` → `/notification`
- Fix smartfocus stop message missing content: only update prev\_lines after sending 👁 update, so stop message correctly diffs against last-sent content
- Fix stop message repeating next-task content: discard pane capture when no ❯ boundary found and pane is already busy with next task

## 0.7.5

- Fix broken permission formatting: escape triple backticks in body content to prevent code block breakout
- Merge context and bash command into a single code block for bash permissions

## 0.7.4

- Fix broken permission formatting: merge context and bash command into a single code block

## 0.7.3

- Fix stop message missing content when smartfocus never sent a 👁 update (fast responses)
- Photo handler now checks busy/idle state, saves typed text, marks busy, and activates smartfocus

## 0.7.2

- Show 🔱 god mode indicator on `/status` session list
- Add hook event debug logging to diagnose missing Read/Edit permission notifications

## 0.7.1

- Fix god mode being deleted when running tests (tearDown cleared real persistent file)
- Discard stale active prompts whose tmux pane reference has changed (e.g. session renamed)

## 0.7.0

- Add `/clear [wN]` command to reset transient state (prompts, busy flags, focus)
- Clear all windows with `/clear`, or target a specific window with `/clear wN`
- Short aliases: `c` (clear all), `c4` (clear w4)

## 0.6.1

- Fix duplicate smartfocus messages: stop signal now sends only tail content (new lines since last smartfocus update) instead of full response
- Fix stale smartfocus variable: re-read state after processing signals to prevent extra "👁" message
- Add god mode diagnostic logging for persistence debugging

## 0.6.0

- Send full stop message ("✅ finished") when autofocus session completes, instead of suppressing
- Show queued messages after stop signal regardless of focus mode
- Persistent god mode: stored in `~/.config/` instead of `/tmp` (auto-migrates old state)
- Detect free text option in plan mode dialogs and support text answers
- Fix stale prompt cleanup: use idle detection (❯ visible) instead of unreliable `_pane_has_prompt`
- Return guidance message for unrecognized prompt replies (ExitPlanMode fix from 0.5.3)

## 0.5.2

- Add "approve" shortcut for permission prompts (previously only plan events accepted it)
- Return guidance message when text reply doesn't match prompt options instead of silently saving

## 0.5.1

- Fix god mode auto-approving ExitPlanMode permission (plan approval now always goes to Telegram)

## 0.5.0

- Forward plan mode permission to Telegram (never auto-accepted, even in god mode)
- Add `EnterPlanMode` to PreToolUse hook matchers in `claude_settings.json`

## 0.4.0

- Detect interrupted sessions (Esc pressed mid-response) and notify via Telegram
- Listener scans panes every 5s for the "Interrupted ·" pattern since no hook fires on interrupt
- Clear stale busy state when pane is idle (interrupt leaves _busy file behind)

## 0.3.2

- Fix smart focus duplicate messages when tool status line changes mid-response

## 0.3.1

- Fix idle detection: recognize Claude Code status bar ("esc to interrupt", file change summaries) as UI chrome

## 0.3.0

- Add `tg_send_document` for sending files as documents (preserves original quality)
- Add `send-doc` CLI command
- Auto-detect large images (>1280px) in `send-photo` and route via `sendDocument`
- Fix hardcoded `image/png` MIME type in `tg_send_photo` — now uses `mimetypes.guess_type()`
- Register `/autofocus` and `/saved` in Telegram bot command picker

## 0.2.0

- Message queuing for busy sessions with `/saved` command
- Busy detection with self-healing (5s grace period, double-check idle state)
- Persistent reply keyboard with common commands
- Reply-based routing (reply to a message to target that session)
- Preserve queued messages and session names across `/start` reset
- Save typed prompt text to queued messages
- Fix permission message formatting (code blocks for context and bash commands)

## 0.1.0

- Multi-module pip-installable package (`tg_hook`)
- Signal-based architecture: hooks write JSON signals, listener polls and processes
- Multi-session routing by `wN` prefix
- Permission forwarding with inline keyboard buttons
- Session monitoring: `/focus`, `/deepfocus`, `/unfocus`
- Smart focus: auto-monitor after sending a message
- God mode: auto-accept permissions with compact receipts
- Session naming (`/name wN label`) and name-based routing
- Short aliases (`s4`, `f4`, `df4`, `i4`, `g4`, etc.)
- Photo sending/receiving between Telegram and Claude Code
- `AskUserQuestion` support with multi-question flows
- `/status`, `/interrupt`, `/new`, `/kill`, `/last` commands
- Auto-reload on file changes
