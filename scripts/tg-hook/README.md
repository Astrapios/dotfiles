# tg-hook

Telegram bridge for [Claude Code](https://docs.anthropic.com/en/docs/claude-code). Control and monitor Claude Code sessions from your phone via a Telegram bot.

## What it does

- **Permission forwarding** — Claude needs to run a command or edit a file? Get the prompt on Telegram with Allow/Deny buttons
- **Session monitoring** — Watch responses stream in real-time, or get notified when Claude finishes
- **Multi-session routing** — Run multiple Claude sessions in tmux and route messages by `w4`, `w5` prefix
- **Message queuing** — Send messages to busy sessions; they're delivered when Claude becomes idle
- **God mode** — Auto-accept all permissions for trusted sessions, with compact receipts

## Architecture

```
Claude Code hooks ──► tg-hook hook ──► signal files ──► tg-hook listen ──► Telegram Bot
                         (stdin)        (/tmp/tg_hook_signals/)              (polling)
                                                                                │
                                                                          Your phone
                                                                          Telegram app
```

- **`tg-hook hook`** — Called by Claude Code hooks (Stop, Notification, PreToolUse). Reads JSON from stdin, writes signal files
- **`tg-hook listen`** — Single daemon that polls Telegram for your messages and processes signal files. Routes messages to the right tmux pane, handles permissions, monitors output
- Signal files decouple the hook calls (which run inside Claude's process) from the Telegram communication

## Setup

### 1. Create a Telegram bot

1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Send `/newbot`, follow the prompts
3. Save the bot token

### 2. Get your chat ID

1. Send any message to your new bot
2. Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates`
3. Find your `chat.id` in the response

### 3. Save credentials

Create `~/.config/tg_hook.env`:

```
TELEGRAM_BOT_TOKEN=123456:ABC-DEF...
TELEGRAM_CHAT_ID=your-chat-id
```

### 4. Install

Requires [pixi](https://pixi.sh) (conda-based Python environment manager).

```bash
cd scripts/tg-hook
pixi install
```

This installs `tg-hook` as an editable package with its dependencies (Python 3.11+, requests).

### 5. Make `tg-hook` available in PATH

Create a wrapper script (e.g. `~/bin/tg-hook`):

```sh
#!/bin/sh
exec pixi run -m /path/to/scripts/tg-hook/pixi.toml tg-hook "$@"
```

Make it executable: `chmod +x ~/bin/tg-hook`

### 6. Configure Claude Code hooks

Copy or symlink `claude_settings.json` to `~/.claude/settings.json` (or merge with your existing settings):

```json
{
  "env": {
    "CLAUDE_TG_HOOKS": "1"
  },
  "hooks": {
    "Notification": [
      { "matcher": "", "hooks": [{ "type": "command", "command": "tg-hook hook" }] }
    ],
    "Stop": [
      { "matcher": "", "hooks": [{ "type": "command", "command": "tg-hook hook" }] }
    ],
    "PreToolUse": [
      { "matcher": "Bash", "hooks": [{ "type": "command", "command": "tg-hook hook" }] },
      { "matcher": "AskUserQuestion", "hooks": [{ "type": "command", "command": "tg-hook hook" }] }
    ]
  }
}
```

### 7. Start the listener

```bash
tg-hook listen
```

Run Claude Code in a tmux session. The listener auto-detects Claude panes and starts routing.

## CLI commands

```
tg-hook listen              Start the Telegram listener daemon
tg-hook hook                Read Claude hook JSON from stdin (called by hooks)
tg-hook notify <message>    Send a one-shot notification
tg-hook ask <question>      Send a question, wait for reply, print to stdout
tg-hook send-photo <path> [caption]  Send a photo to Telegram
tg-hook send-doc <path> [caption]    Send a file as a document to Telegram
tg-hook help                Show help
```

## Telegram commands

Once the listener is running, send these from Telegram:

| Command | Description |
|---------|-------------|
| `/status [wN]` | List sessions, or show last response for wN |
| `/focus wN` | Watch completed responses from a session |
| `/deepfocus wN` | Stream all output in real-time |
| `/unfocus` | Stop monitoring |
| `/clear [wN]` | Reset transient state (prompts, busy, focus) |
| `/god [wN\|all\|off]` | Auto-accept permissions (god mode) |
| `/autofocus` | Toggle auto-monitor on message send |
| `/name wN label` | Name a session for easier routing |
| `/interrupt wN` | Interrupt current task (Esc) |
| `/new [dir]` | Start a new Claude session |
| `/saved [wN]` | Review queued messages |
| `/last [wN]` | Re-send last Telegram message |
| `/kill wN` | Exit a Claude session (Ctrl+C) |
| `/notification [digits\|all\|off]` | Control which alerts buzz your phone |
| `/stop` / `/start` | Pause / resume the listener |
| `/quit` | Shut down the listener |

### Short aliases

| Alias | Expands to |
|-------|-----------|
| `s` / `s4` / `s4 10` | `/status` / `/status w4` / `/status w4 10` |
| `f4` | `/focus w4` |
| `df4` | `/deepfocus w4` |
| `i4` | `/interrupt w4` |
| `c` / `c4` | `/clear` / `/clear w4` |
| `g4` | `/god w4` |
| `ga` | `/god all` |
| `goff` | `/god off` |
| `uf` | `/unfocus` |
| `af` | `/autofocus` |
| `noti` / `noti 123` | `/notification` / `/notification 123` |
| `sv` | `/saved` |
| `?` | `/help` |

### Message routing

- **`w4 fix the bug`** — sends "fix the bug" to session w4
- **`fix the bug`** — sends to the only session, or the last-used one
- **Named sessions** — after `/name w4 auth`, send `auth fix the bug`
- **Photos** — send a photo with optional `w4` caption to have Claude read it

## God mode

Auto-accept all permission prompts for trusted sessions:

```
/god w4          Enable for session w4
/god all         Enable for all sessions
/god off         Disable entirely
/god off w4      Disable for w4 only
/god             Show current status
```

When active, permissions are auto-accepted and you see compact receipts instead of interactive buttons:

```
🔱 w4 Auto-allowed (myproject): git status
```

God mode also enables "accept edits on" mode (Shift+Tab cycling) to reduce edit permission prompts.

## Notification control

By default, only permission (🔧) and stop (✅) messages buzz your phone. All other messages (monitoring updates, confirmations, errors) arrive silently.

```
/notification           Show current config and categories
/notification 12        Set loud categories (1=permission, 2=stop)
/notification 1234      Add question and error alerts
/notification all       All categories loud
/notification off       Everything silent
```

Categories:

| # | Type | Default |
|---|------|---------|
| 1 | 🔧 Permission requests | loud |
| 2 | ✅ Task completion | loud |
| 3 | ❓ Questions / plan mode | silent |
| 4 | ⚠️ Errors | silent |
| 5 | ⏹ Interrupted sessions | silent |
| 6 | 🔍 Focus/monitoring updates | silent |
| 7 | 📨 Confirmations (sent, saved, reload) | silent |

Config persists in `~/.config/tg_hook_notifications.json`.

## Tests

```bash
cd scripts/tg-hook
pixi run test
```

## Project structure

```
scripts/tg-hook/
├── pixi.toml              # Pixi project config
├── pyproject.toml          # Python package config
├── src/tg_hook/
│   ├── __init__.py         # Re-exports for backward compat
│   ├── cli.py              # CLI entry point (main, notify, ask, hook)
│   ├── commands.py         # Telegram command handling (/status, /god, etc.)
│   ├── config.py           # Environment loading, constants
│   ├── content.py          # Pane content extraction and cleaning
│   ├── listener.py         # Main daemon loop
│   ├── routing.py          # Message routing to tmux panes
│   ├── signals.py          # Signal file processing
│   ├── state.py            # Persistent state (prompts, focus, god mode, etc.)
│   ├── telegram.py         # Telegram Bot API wrapper
│   └── tmux.py             # tmux session scanning and pane interaction
└── tests/
    └── test_tg_hook.py     # Unit tests
```
