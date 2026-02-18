"""Entry point: main(), cmd_notify/ask/hook/send_photo + local config/debug commands."""
import json
import os
import re
import shlex
import subprocess
import sys
import time

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


def cmd_god():
    """Manage god mode from CLI."""
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if not arg:
        wids = state._god_mode_wids()
        quiet = state._is_god_quiet()
        if not wids:
            print("God mode: off")
        else:
            label = ", ".join(wids)
            print(f"God mode: {label}" + (" (quiet)" if quiet else ""))
        return
    if arg in ("quiet", "q"):
        state._set_god_quiet(True)
        print("God mode receipts suppressed.")
    elif arg in ("loud", "l"):
        state._set_god_quiet(False)
        print("God mode receipts enabled.")
    elif arg == "all":
        state._set_god_mode("all", True)
        print("God mode: all sessions.")
    elif arg == "off":
        state._clear_god_mode()
        print("God mode: off.")
    elif arg.startswith("w"):
        state._set_god_mode(arg, True)
        print(f"God mode: {arg} enabled.")
    else:
        print("Usage: astra god [all|off|wN|quiet|loud]", file=sys.stderr)
        sys.exit(1)


def cmd_local():
    """Manage local suppress from CLI."""
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if not arg:
        enabled = state._is_local_suppress_enabled()
        print(f"Local suppress: {'on' if enabled else 'off'}")
        return
    if arg == "on":
        state._set_local_suppress(True)
        print("Local suppress: on.")
    elif arg == "off":
        state._set_local_suppress(False)
        print("Local suppress: off.")
    else:
        print("Usage: astra local [on|off]", file=sys.stderr)
        sys.exit(1)


def cmd_debug():
    """Manage debug logging from CLI."""
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if arg == "on":
        config._set_debug(True)
        print("Debug logging: on.")
    elif arg == "off":
        config._set_debug(False)
        print("Debug logging: off (log deleted).")
    elif arg == "clear":
        try:
            open(config.DEBUG_LOG, "w").close()
        except OSError:
            pass
        print("Debug log cleared.")
    elif not arg or arg.isdigit():
        n = int(arg) if arg else 20
        enabled = config._is_debug_enabled()
        print(f"Debug logging: {'on' if enabled else 'off'}")
        if enabled or os.path.isfile(config.DEBUG_LOG):
            try:
                with open(config.DEBUG_LOG) as f:
                    lines = f.readlines()
                tail = lines[-n:] if lines else []
                if tail:
                    print(f"Last {len(tail)} log lines:")
                    for line in tail:
                        print(f"  {line.rstrip()}")
                else:
                    print("(log empty)")
            except FileNotFoundError:
                print("(no log file)")
    else:
        print("Usage: astra debug [on|off|clear|N]", file=sys.stderr)
        sys.exit(1)


def cmd_autofocus():
    """Manage autofocus from CLI."""
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if not arg:
        enabled = state._is_autofocus_enabled()
        print(f"Autofocus: {'on' if enabled else 'off'}")
        return
    if arg == "on":
        state._set_autofocus(True)
        print("Autofocus: on.")
    elif arg == "off":
        state._set_autofocus(False)
        print("Autofocus: off.")
    else:
        print("Usage: astra autofocus [on|off]", file=sys.stderr)
        sys.exit(1)


def cmd_notification():
    """Manage notification levels from CLI."""
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    loud = state._load_notification_config()
    if not arg:
        cats = state._NOTIFICATION_CATEGORIES
        lines = []
        for n, (name, emoji) in sorted(cats.items()):
            status = "loud" if n in loud else "silent"
            lines.append(f"  {n}. {emoji} {name}: {status}")
        print("Notifications:\n" + "\n".join(lines))
        return
    if arg == "all":
        loud = set(state._NOTIFICATION_CATEGORIES.keys())
    elif arg == "off":
        loud = set()
    else:
        digits = {int(c) for c in arg if c.isdigit()}
        if not digits:
            print("Usage: astra notification [1..7|all|off]", file=sys.stderr)
            sys.exit(1)
        loud = digits
    state._save_notification_config(loud)
    print(f"Notifications loud: {sorted(loud) if loud else 'none'}.")


def cmd_status():
    """Show sessions or pane output."""
    from astra import content, routing, profiles
    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    args = sys.argv[2:]
    if not args:
        if not sessions:
            print("No CLI sessions found.")
            return
        statuses = routing._get_session_statuses(sessions)
        viewed = tmux._get_locally_viewed_windows() if state._is_local_suppress_enabled() else None
        icons = {"idle": "🟢", "busy": "🟡", "interrupted": "🔴"}
        names = state._load_session_names()
        for idx in tmux._sort_session_keys(sessions):
            info = sessions[idx]
            project = info.project if hasattr(info, "project") else info[1]
            cli_type = info.cli if hasattr(info, "cli") else "claude"
            display = tmux._display_wid(idx, sessions)
            name = names.get(idx, "")
            label = f"{display} [{name}]" if name else display
            icon = icons.get(statuses.get(idx, ""), "")
            god = " ⚡" if state._is_god_mode_for(idx) else ""
            win_idx = info.win_idx if hasattr(info, "win_idx") else re.match(r"^w?(\d+)", idx).group(1)
            local = " 👁" if viewed and win_idx in viewed else ""
            print(f"  {label:12s} {project} ({cli_type}) {icon}{god}{local}")
        return
    raw_target = args[0]
    num_lines = int(args[1]) if len(args) > 1 else 20
    idx = state._resolve_name(raw_target, sessions)
    if not idx:
        print(f"No session '{raw_target}'.", file=sys.stderr)
        sys.exit(1)
    pane, project = sessions[idx]
    info = sessions[idx]
    _prof = profiles.get_profile(info.cli) if hasattr(info, "cli") else None
    pw = tmux._get_pane_width(pane)
    if len(args) > 1:
        raw = tmux._capture_pane(pane, num_lines * 3 + 20)
        filtered = content.clean_pane_status(raw, pw, profile=_prof)
        lines = filtered.splitlines()
        output = "\n".join(lines[-num_lines:]) if lines else ""
    else:
        for n in (30, 80, 200):
            raw = tmux._capture_pane(pane, n)
            if content._has_response_start(raw, profile=_prof):
                break
        raw_view = content.clean_pane_status(tmux._capture_pane(pane, 30), pw, profile=_prof)
        if content._has_response_start(raw, profile=_prof):
            bullet_view = content.clean_pane_content(raw, "stop", pw, profile=_prof)
            output = bullet_view if len(bullet_view) >= len(raw_view) else raw_view
        else:
            output = raw_view
    display = tmux._display_wid(idx, sessions)
    print(f"--- {display} ({project}) ---")
    print(output or "(empty)")


def cmd_focus():
    """Set focus target."""
    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    raw_target = sys.argv[2] if len(sys.argv) > 2 else ""
    if not raw_target:
        current = state._load_focus_state()
        if current:
            print(f"Focus: {current['wid']} ({current.get('project', '?')})")
        else:
            print("Focus: off")
        return
    idx = state._resolve_name(raw_target, sessions)
    if not idx:
        print(f"No session '{raw_target}'.", file=sys.stderr)
        sys.exit(1)
    pane, project = sessions[idx]
    state._save_focus_state(idx, pane, project)
    state._clear_deepfocus_state()
    state._clear_smartfocus_state()
    print(f"Focus: {tmux._display_wid(idx, sessions)} ({project}).")


def cmd_deepfocus():
    """Set deepfocus target."""
    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    raw_target = sys.argv[2] if len(sys.argv) > 2 else ""
    if not raw_target:
        current = state._load_deepfocus_state()
        if current:
            print(f"Deepfocus: {current['wid']} ({current.get('project', '?')})")
        else:
            print("Deepfocus: off")
        return
    idx = state._resolve_name(raw_target, sessions)
    if not idx:
        print(f"No session '{raw_target}'.", file=sys.stderr)
        sys.exit(1)
    pane, project = sessions[idx]
    state._save_deepfocus_state(idx, pane, project)
    state._clear_focus_state()
    state._clear_smartfocus_state()
    print(f"Deepfocus: {tmux._display_wid(idx, sessions)} ({project}).")


def cmd_unfocus():
    """Clear all focus state."""
    state._clear_focus_state()
    state._clear_deepfocus_state()
    state._clear_smartfocus_state()
    print("Focus stopped.")


def cmd_clear():
    """Clear transient state."""
    raw_target = sys.argv[2] if len(sys.argv) > 2 else ""
    if raw_target:
        sessions = tmux.scan_claude_sessions()
        state._current_sessions = sessions
        idx = state._resolve_name(raw_target, sessions)
        if not idx:
            print(f"No session '{raw_target}'.", file=sys.stderr)
            sys.exit(1)
        state._clear_window_state(idx)
        print(f"Cleared state for {tmux._display_wid(idx, sessions)}.")
    else:
        state._clear_all_transient_state()
        print("Cleared all transient state.")


def cmd_interrupt():
    """Interrupt a session (send Escape)."""
    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    raw_target = sys.argv[2] if len(sys.argv) > 2 else ""
    if raw_target:
        idx = state._resolve_name(raw_target, sessions)
    elif len(sessions) == 1:
        idx = next(iter(sessions))
    else:
        if not sessions:
            print("No CLI sessions found.", file=sys.stderr)
        else:
            print("Multiple sessions — specify wN.", file=sys.stderr)
        sys.exit(1)
    if not idx:
        print(f"No session '{raw_target}'.", file=sys.stderr)
        sys.exit(1)
    pane, project = sessions[idx]
    p = shlex.quote(pane)
    subprocess.run(["bash", "-c",
                    f"tmux send-keys -t {p} Escape && sleep 0.1 && "
                    f"tmux send-keys -t {p} C-u"], timeout=5)
    state._clear_busy(idx)
    state.load_active_prompt(idx)
    print(f"Interrupted {tmux._display_wid(idx, sessions)} ({project}).")


def cmd_name():
    """Set or clear a session name."""
    if len(sys.argv) < 3:
        # Show all names
        names = state._load_session_names()
        if not names:
            print("No session names set.")
        else:
            for wid, name in sorted(names.items()):
                print(f"  {wid}: {name}")
        return
    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    raw_target = sys.argv[2]
    label = sys.argv[3] if len(sys.argv) > 3 else None
    idx = state._resolve_name(raw_target, sessions) or raw_target
    if label:
        state._save_session_name(idx, label)
        print(f"Named {idx} → {label}.")
    else:
        state._clear_session_name(idx)
        print(f"Cleared name for {idx}.")


def cmd_saved():
    """Show queued messages."""
    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    raw_target = sys.argv[2] if len(sys.argv) > 2 else ""
    if raw_target:
        idx = state._resolve_name(raw_target, sessions)
        if not idx:
            print(f"No session '{raw_target}'.", file=sys.stderr)
            sys.exit(1)
        queued = state._load_queued_msgs(idx)
        if queued:
            print(f"Saved messages for {tmux._display_wid(idx, sessions)}:")
            for i, m in enumerate(queued, 1):
                print(f"  {i}. {m['text'][:100]}")
        else:
            print(f"No saved messages for {tmux._display_wid(idx, sessions)}.")
    else:
        found = False
        for idx in tmux._sort_session_keys(sessions):
            queued = state._load_queued_msgs(idx)
            if queued:
                found = True
                display = tmux._display_wid(idx, sessions)
                print(f"Saved messages for {display}:")
                for i, m in enumerate(queued, 1):
                    print(f"  {i}. {m['text'][:100]}")
        if not found:
            print("No saved messages.")


def cmd_log():
    """Show listener journal lines."""
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 30
    n = min(n, 200)
    try:
        result = subprocess.run(
            ["journalctl", "--user", "-u", "astra", "-n", str(n), "--no-pager"],
            capture_output=True, text=True, timeout=10,
        )
        output = result.stdout.strip()
        print(output if output else "No journal entries found for astra.")
    except Exception as e:
        print(f"Failed to read journalctl: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_new():
    """Start a new CLI session."""
    from astra import profiles
    args = sys.argv[2:]
    cli_name = "claude"
    dir_arg = None
    if args and profiles.get_profile(args[0].lower()):
        cli_name = args[0].lower()
        dir_arg = args[1] if len(args) > 1 else None
    elif args:
        dir_arg = args[0]
    profile = profiles.get_profile(cli_name) or profiles.CLAUDE
    if dir_arg:
        work_dir = os.path.expanduser(dir_arg)
    else:
        ts = time.strftime("%m%d-%H%M")
        work_dir = os.path.expanduser(f"~/projects/{cli_name}-{ts}")
    os.makedirs(work_dir, exist_ok=True)
    try:
        result = subprocess.run(
            ["tmux", "new-window", "-d", "-P", "-F", "#{window_index}",
             f"bash -c 'cd {shlex.quote(work_dir)} && {profile.launch_cmd}'"],
            capture_output=True, text=True, timeout=10,
        )
        new_idx = result.stdout.strip()
        new_wid = None
        for _ in range(6):
            sessions = tmux.scan_claude_sessions()
            new_wid = tmux.resolve_session_id(f"w{new_idx}", sessions)
            if new_wid:
                break
            time.sleep(1)
        if not new_wid:
            new_wid = f"w{new_idx}a"
        proj = work_dir.rstrip("/").rsplit("/", 1)[-1]
        print(f"Started {profile.display_name} in {new_wid} ({proj}): {work_dir}")
    except Exception as e:
        print(f"Failed to start session: {e}", file=sys.stderr)
        sys.exit(1)


def cmd_restart():
    """Kill and relaunch a CLI session."""
    from astra import profiles
    if len(sys.argv) < 3:
        print("Usage: astra restart <wN>", file=sys.stderr)
        sys.exit(1)
    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    raw_target = sys.argv[2]
    idx = state._resolve_name(raw_target, sessions)
    if not idx:
        print(f"No session '{raw_target}'.", file=sys.stderr)
        sys.exit(1)
    pane, project = sessions[idx]
    info = sessions[idx]
    if isinstance(info, tmux.SessionInfo):
        restart_profile = profiles.get_profile(info.cli) or profiles.CLAUDE
    else:
        restart_profile = profiles.CLAUDE
    cwd = tmux._get_pane_cwd(pane)
    p = shlex.quote(pane)
    subprocess.run(
        ["bash", "-c",
         f"tmux send-keys -t {p} C-c && sleep 0.1 && "
         f"tmux send-keys -t {p} C-c && sleep 0.1 && "
         f"tmux send-keys -t {p} C-c"],
        timeout=10,
    )
    time.sleep(2)
    sessions = tmux.scan_claude_sessions()
    if idx in sessions:
        print(f"{idx} ({project}) still running — restart aborted.", file=sys.stderr)
        sys.exit(1)
    state._clear_busy(idx)
    for suffix in (f"_active_prompt_{idx}.json", f"_bash_cmd_{idx}.json"):
        try:
            os.remove(os.path.join(config.SIGNAL_DIR, suffix))
        except OSError:
            pass
    pane_alive = bool(tmux._get_pane_command(pane))
    restart_cmd = restart_profile.restart_cmd
    if pane_alive:
        shell = tmux._get_pane_command(pane) or ""
        if "zsh" in shell:
            source_cmd = "source ~/.zshrc && "
        elif "bash" in shell:
            source_cmd = "source ~/.bashrc && "
        else:
            source_cmd = ""
        cd_cmd = f"cd {shlex.quote(cwd)} && " if cwd else ""
        subprocess.run(
            ["bash", "-c",
             f"tmux send-keys -t {p} -l {shlex.quote(source_cmd + cd_cmd + restart_cmd)} && "
             f"sleep 0.1 && tmux send-keys -t {p} Enter"],
            timeout=10,
        )
    else:
        work_dir = cwd or os.path.expanduser("~")
        subprocess.run(
            ["tmux", "new-window", "-d", "-P", "-F", "#{window_index}",
             f"bash -c 'cd {shlex.quote(work_dir)} && {restart_cmd}'"],
            capture_output=True, text=True, timeout=10,
        )
    new_wid = None
    for _ in range(6):
        time.sleep(1)
        sessions = tmux.scan_claude_sessions()
        new_wid = tmux.resolve_session_id(idx, sessions)
        if new_wid:
            break
    if new_wid and new_wid in sessions:
        _, new_project = sessions[new_wid]
        print(f"Restarted {new_wid} ({new_project}).")
    else:
        print(f"{idx} did not restart — pane may have closed.", file=sys.stderr)
        sys.exit(1)


def cmd_kill():
    """Kill a CLI session (Ctrl+C x3)."""
    if len(sys.argv) < 3:
        print("Usage: astra kill <wN>", file=sys.stderr)
        sys.exit(1)
    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    raw_target = sys.argv[2]
    idx = state._resolve_name(raw_target, sessions)
    if not idx:
        print(f"No session '{raw_target}'.", file=sys.stderr)
        sys.exit(1)
    pane, project = sessions[idx]
    p = shlex.quote(pane)
    subprocess.run(
        ["bash", "-c",
         f"tmux send-keys -t {p} C-c && sleep 0.1 && "
         f"tmux send-keys -t {p} C-c && sleep 0.1 && "
         f"tmux send-keys -t {p} C-c"],
        timeout=10,
    )
    time.sleep(2)
    sessions = tmux.scan_claude_sessions()
    if idx in sessions:
        print(f"{idx} ({project}) still running after Ctrl+C.", file=sys.stderr)
    else:
        print(f"Killed {idx} ({project}).")


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

Config (no Telegram credentials needed):
  god [all|off|wN|quiet|loud]  Manage god mode
  local [on|off]               Toggle local suppress
  autofocus [on|off]           Toggle autofocus
  notification [1..7|all|off]  Configure notification levels
  debug [on|off|clear|N]       Debug log for outbound Telegram messages

Session management (no Telegram credentials needed):
  status [wN] [lines]          List sessions or show pane output
  focus [wN]                   Set focus target
  deepfocus [wN]               Set deepfocus target
  unfocus                      Stop all monitoring
  interrupt [wN]               Interrupt session (Escape)
  clear [wN]                   Reset transient state
  name [wN] [label]            Set/clear session name
  saved [wN]                   Show queued messages
  log [N]                      Show last N journal lines (default 30)
  new [claude|gemini] [dir]    Start new session
  restart <wN>                 Kill and relaunch session
  kill <wN>                    Kill a session (Ctrl+C x3)

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

    # Config & session commands — no Telegram credentials needed
    _local_commands = {
        "god": cmd_god, "local": cmd_local, "debug": cmd_debug,
        "autofocus": cmd_autofocus,
        "notification": cmd_notification, "status": cmd_status,
        "focus": cmd_focus, "deepfocus": cmd_deepfocus, "unfocus": cmd_unfocus,
        "clear": cmd_clear, "interrupt": cmd_interrupt, "name": cmd_name,
        "saved": cmd_saved, "log": cmd_log, "new": cmd_new,
        "restart": cmd_restart, "kill": cmd_kill,
    }
    if command in _local_commands:
        _local_commands[command]()
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
