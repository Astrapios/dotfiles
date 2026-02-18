# Changelog

All notable changes to astra (formerly tg-hook) are documented here.

Versioning: **MINOR** (0.X.0) for new user-facing features (commands, APIs).
**PATCH** (0.0.X) for bug fixes, refactors, and test/docs-only changes.

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
