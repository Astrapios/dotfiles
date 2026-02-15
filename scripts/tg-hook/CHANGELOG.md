# Changelog

All notable changes to tg-hook are documented here.

Versioning: **MINOR** (0.X.0) for new user-facing features (commands, APIs).
**PATCH** (0.0.X) for bug fixes, refactors, and test/docs-only changes.

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
