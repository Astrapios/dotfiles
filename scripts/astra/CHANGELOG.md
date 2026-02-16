# Changelog

All notable changes to astra (formerly tg-hook) are documented here.

Versioning: **MINOR** (0.X.0) for new user-facing features (commands, APIs).
**PATCH** (0.0.X) for bug fixes, refactors, and test/docs-only changes.

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
