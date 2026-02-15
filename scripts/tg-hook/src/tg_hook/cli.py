"""Entry point: main(), cmd_notify/ask/hook/send_photo."""
import json
import os
import sys

from tg_hook import config, telegram, state, tmux, listener


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


def cmd_hook():
    """Read hook JSON from stdin, write signal files for listen to process."""
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

    config._log("hook", f"event={event} tool={tool} keys={list(data.keys())}")

    if event == "Stop":
        state.write_signal("stop", data)
    elif event == "Notification":
        ntype = data.get("notification_type", "")
        msg = data.get("message", "")
        config._log("hook", f"notification type={ntype} msg={msg[:200]}")
        if ntype == "permission_prompt":
            if "needs your attention" in msg:
                return
            wid = tmux.get_window_id() or "unknown"
            bash_cmd = ""
            if "bash" in msg.lower():
                cmd_file = os.path.join(config.SIGNAL_DIR, f"_bash_cmd_{wid}.json")
                try:
                    with open(cmd_file) as f:
                        bash_cmd = json.load(f).get("cmd", "")
                    os.remove(cmd_file)
                except (OSError, json.JSONDecodeError):
                    pass
            state.write_signal("permission", data, cmd=bash_cmd, message=msg)
    elif event == "PreToolUse":
        if tool == "EnterPlanMode":
            state.write_signal("plan", data)
        elif tool == "AskUserQuestion":
            state.write_signal("question", data, questions=data.get("tool_input", {}).get("questions", []))
        elif tool == "Bash":
            os.makedirs(config.SIGNAL_DIR, exist_ok=True)
            wid = tmux.get_window_id() or "unknown"
            cmd = data.get("tool_input", {}).get("command", "")
            cmd_file = os.path.join(config.SIGNAL_DIR, f"_bash_cmd_{wid}.json")
            with open(cmd_file, "w") as f:
                json.dump({"cmd": cmd}, f)


def cmd_help():
    """Print CLI usage information."""
    print("""tg-hook — Telegram bridge for Claude Code

Usage: tg-hook <command> [args...]

Commands:
  listen              Start the Telegram listener daemon
  hook                Read Claude hook JSON from stdin (called by hooks)
  notify <message>    Send a one-shot notification to Telegram
  ask <question>      Send a question, wait for reply, print to stdout
  send-photo <path> [caption]  Send a photo to Telegram
  send-doc <path> [caption]    Send a file as a document to Telegram
  help                Show this help message

Setup:
  1. Create a Telegram bot via @BotFather, get the token
  2. Get your chat ID (send a message to the bot, check getUpdates)
  3. Save credentials to ~/.config/tg_hook.env:
       TELEGRAM_BOT_TOKEN=your-bot-token
       TELEGRAM_CHAT_ID=your-chat-id
  4. Configure Claude Code hooks (see claude_settings.json)
  5. Run: tg-hook listen

Telegram commands (inside listener):
  /status [wN]        List sessions or show session output
  /focus wN           Watch completed responses
  /deepfocus wN       Stream all output in real-time
  /unfocus            Stop monitoring
  /clear [wN]         Reset transient state (prompts, busy, focus)
  /god [wN|all|off]   Auto-accept permissions (god mode)
  /interrupt wN       Interrupt current task
  /name wN label      Name a session
  /new [dir]          Start new Claude session
  /saved [wN]         Review queued messages
  /last [wN]          Re-send last Telegram message
  /autofocus          Toggle auto-monitor on send
  /kill wN            Exit a Claude session
  /stop / /start      Pause/resume listener
  /quit               Shut down listener""")


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
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (env or ~/.config/tg_hook.env)", file=sys.stderr)
        sys.exit(1)

    if command == "notify":
        msg = sys.argv[2] if len(sys.argv) > 2 else "ping"
        cmd_notify(msg)
    elif command == "ask":
        question = sys.argv[2] if len(sys.argv) > 2 else "Yes or no?"
        cmd_ask(question)
    elif command == "send-photo":
        if len(sys.argv) < 3:
            print("Usage: tg-hook send-photo <path> [caption]", file=sys.stderr)
            sys.exit(1)
        path = sys.argv[2]
        caption = sys.argv[3] if len(sys.argv) > 3 else ""
        cmd_send_photo(path, caption)
    elif command == "send-doc":
        if len(sys.argv) < 3:
            print("Usage: tg-hook send-doc <path> [caption]", file=sys.stderr)
            sys.exit(1)
        path = sys.argv[2]
        caption = sys.argv[3] if len(sys.argv) > 3 else ""
        cmd_send_doc(path, caption)
    elif command == "listen":
        listener.cmd_listen()
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        print("Run 'tg-hook help' for usage information.")
        sys.exit(1)
