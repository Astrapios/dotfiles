"""Entry point: main(), cmd_notify/ask/hook/send_photo."""
import json
import os
import sys

from astra import config, telegram, state, tmux, listener


def cmd_notify(message: str):
    """Send a notification, no reply expected."""
    telegram.tg_send(message)


def cmd_ask(question: str) -> str:
    """Send a question, wait for reply, print to stdout."""
    msg_id = telegram.tg_send(f"❓ *Claude Code asks:*\n{question}\n\nReply to respond")
    reply = telegram.tg_wait_reply(msg_id)
    print(reply)
    return reply


def cmd_send_photo(path: str, caption: str = ""):
    """Send a photo file to Telegram."""
    if not os.path.isfile(path):
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    telegram.tg_send_photo(path, caption)
    print(f"Photo sent: {path}")


def cmd_send_doc(path: str, caption: str = ""):
    """Send a file as a document to Telegram."""
    if not os.path.isfile(path):
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    telegram.tg_send_document(path, caption)
    print(f"Document sent: {path}")


def _detect_cli_from_event(event: str) -> str:
    """Detect which CLI fired the hook based on event name."""
    from astra import profiles
    for profile in profiles.all_profiles():
        if event in profile.event_map:
            return profile.name
    return "claude"  # default fallback


def cmd_hook():
    """Read hook JSON from stdin, write signal files for listen to process.

    Normalizes event and tool names from different CLIs (Claude, Gemini)
    into internal names via profile event_map/tool_map.
    """
    if not config.TG_HOOKS_ENABLED:
        sys.stdin.read()
        return
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    event = data.get("hook_event_name", "")
    tool = data.get("tool_name", "")

    # Detect CLI and normalize event/tool names
    cli_name = _detect_cli_from_event(event)
    from astra import profiles
    profile = profiles.get_profile(cli_name) or profiles.CLAUDE
    internal_event = profile.event_map.get(event, "")
    internal_tool = profile.tool_map.get(tool, "")

    # PreToolUse: save bash cmd + god mode auto-approve for all tools.
    # Handled early to keep stdout clean (approve decision must be only output).
    if internal_event == "pre_tool" and internal_tool not in ("plan", "question"):
        wid = tmux.get_window_id() or "unknown"
        if internal_tool == "shell":
            os.makedirs(config.SIGNAL_DIR, exist_ok=True)
            cmd = data.get("tool_input", {}).get("command", "")
            cmd_file = os.path.join(config.SIGNAL_DIR, f"_bash_cmd_{wid}.json")
            with open(cmd_file, "w") as f:
                json.dump({"cmd": cmd}, f)
        if state._is_god_mode_for(wid):
            tool_input = data.get("tool_input", {})
            if internal_tool == "shell":
                desc = tool_input.get("command", "")[:200]
            elif internal_tool in ("edit", "write", "read"):
                desc = tool_input.get("file_path", "")[:200]
            else:
                desc = internal_tool
            print(json.dumps({"decision": "approve"}))
            state.write_signal("god_approve", data, cmd=desc, tool=internal_tool, cli=cli_name)
        return

    config._log("hook", f"cli={cli_name} event={event}→{internal_event} tool={tool}→{internal_tool} keys={list(data.keys())}")

    if internal_event == "stop":
        state.write_signal("stop", data, cli=cli_name)
    elif internal_event == "notification":
        ntype = data.get("notification_type", "")
        msg = data.get("message", "")
        config._log("hook", f"notification type={ntype} msg={msg[:200]}")
        if ntype == "permission_prompt":
            if "needs your attention" in msg:
                return
            wid = tmux.get_window_id() or "unknown"
            bash_cmd = ""
            if "bash" in msg.lower() or internal_tool == "shell":
                cmd_file = os.path.join(config.SIGNAL_DIR, f"_bash_cmd_{wid}.json")
                try:
                    with open(cmd_file) as f:
                        bash_cmd = json.load(f).get("cmd", "")
                    os.remove(cmd_file)
                except (OSError, json.JSONDecodeError):
                    pass
            state.write_signal("permission", data, cmd=bash_cmd, message=msg, cli=cli_name)
    elif internal_event == "pre_tool":
        if internal_tool == "plan":
            state.write_signal("plan", data, cli=cli_name)
        elif internal_tool == "question":
            state.write_signal("question", data, questions=data.get("tool_input", {}).get("questions", []), cli=cli_name)


def cmd_help():
    """Print CLI usage information."""
    print("""astra — Telegram bridge for Claude Code & Gemini CLI

Usage: astra <command> [args...]

Commands:
  listen              Start the Telegram listener daemon
  hook                Read hook JSON from stdin (called by hooks)
  notify <message>    Send a one-shot notification to Telegram
  ask <question>      Send a question, wait for reply, print to stdout
  send-photo <path> [caption]  Send a photo to Telegram
  send-doc <path> [caption]    Send a file as a document to Telegram
  help                Show this help message

Setup:
  1. Create a Telegram bot via @BotFather, get the token
  2. Get your chat ID (send a message to the bot, check getUpdates)
  3. Save credentials to ~/.config/astra.env:
       TELEGRAM_BOT_TOKEN=your-bot-token
       TELEGRAM_CHAT_ID=your-chat-id
  4. Configure hooks (see claude_settings.json / gemini_settings.json)
  5. Run: astra listen

Telegram commands (inside listener):
  /status [wN] [lines] List sessions or show output
  /interrupt [wN]      Interrupt current task (Esc)
  /god [wN|all|off]    Auto-accept permissions (god mode)
  /god quiet|loud      Suppress/enable god mode receipts
  /focus wN            Watch completed responses
  /deepfocus wN        Stream all output in real-time
  /unfocus             Stop monitoring
  /saved [wN]          Review saved messages
  /last [wN]           Re-send last Telegram message
  /autofocus [on|off]  Auto-monitor on send (default: on)
  /local [on|off]      Suppress Telegram when viewing locally
  /notification [1..7|all|off]  Control which alerts buzz
  /name wN [label]     Name a session (omit label to clear)
  /new [claude|gemini] [dir]  Start new session
  /restart wN          Kill and relaunch session
  /kill wN             Exit a session
  /clear [wN]          Reset transient state
  /log [N]             Show last N journal lines (default 30)
  /stop / /start       Pause / resume listener
  /quit                Shut down listener

Aliases:
  s / s4 / s4 10       /status / /status w4 / /status w4 10
  f4 / df4 / uf        /focus w4 / /deepfocus w4 / /unfocus
  i4 / sv / ?          /interrupt w4 / /saved / /help
  g4 / ga / goff       /god w4 / /god all / /god off
  gq / gl              /god quiet / /god loud
  af / lv / noti       /autofocus / /local / /notification
  c / c4 / r4          /clear / /clear w4 / /restart w4

Routing: prefix with wN (e.g. 'w4 fix the bug').
  Solo panes: w4 or w4a. Multi-pane: w1a, w1b.""")


def main():
    """CLI entry point."""
    if len(sys.argv) < 2:
        cmd_help()
        sys.exit(1)

    command = sys.argv[1]

    if command == "help" or command == "--help" or command == "-h":
        cmd_help()
        return

    if command == "hook":
        cmd_hook()
        return

    if not config.BOT or not config.CHAT_ID:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (env or ~/.config/astra.env)", file=sys.stderr)
        sys.exit(1)

    if command == "notify":
        msg = sys.argv[2] if len(sys.argv) > 2 else "ping"
        cmd_notify(msg)
    elif command == "ask":
        question = sys.argv[2] if len(sys.argv) > 2 else "Yes or no?"
        cmd_ask(question)
    elif command == "send-photo":
        if len(sys.argv) < 3:
            print("Usage: astra send-photo <path> [caption]", file=sys.stderr)
            sys.exit(1)
        path = sys.argv[2]
        caption = sys.argv[3] if len(sys.argv) > 3 else ""
        cmd_send_photo(path, caption)
    elif command == "send-doc":
        if len(sys.argv) < 3:
            print("Usage: astra send-doc <path> [caption]", file=sys.stderr)
            sys.exit(1)
        path = sys.argv[2]
        caption = sys.argv[3] if len(sys.argv) > 3 else ""
        cmd_send_doc(path, caption)
    elif command == "listen":
        listener.cmd_listen()
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print("Run 'astra help' for usage information.")
        sys.exit(1)
