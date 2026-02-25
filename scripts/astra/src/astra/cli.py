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
        else:
            # Clean up stale bash_cmd from auto-approved shell commands
            cmd_file = os.path.join(config.SIGNAL_DIR, f"_bash_cmd_{wid}.json")
            try:
                os.remove(cmd_file)
            except OSError:
                pass
        if state._is_god_mode_for(wid):
            tool_input = data.get("tool_input", {})
            if internal_tool == "shell":
                desc = tool_input.get("command", "")[:200]
            elif internal_tool in ("edit", "write", "read", "notebook"):
                desc = tool_input.get("file_path", tool_input.get("notebook_path", ""))[:200]
            elif internal_tool == "fetch":
                desc = tool_input.get("url", "")[:200]
            elif internal_tool == "search":
                desc = tool_input.get("query", "")[:200]
            elif internal_tool in ("glob", "grep"):
                desc = tool_input.get("pattern", "")[:200]
            elif internal_tool == "task":
                desc = tool_input.get("description", tool_input.get("prompt", ""))[:200]
            else:
                desc = internal_tool or tool
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
    """Manage debug logging and debug subcommands from CLI."""
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
    elif arg == "state":
        _debug_state()
    elif arg == "inject":
        _debug_inject()
    elif arg == "tick":
        _debug_tick()
    elif arg == "smartfocus":
        _debug_smartfocus()
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
        print("Usage: astra debug [on|off|clear|N|state|inject|tick|smartfocus]", file=sys.stderr)
        sys.exit(1)


def _debug_state():
    """Dump listener-visible state for debugging."""
    from astra import routing, signals
    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    target_wid = sys.argv[3] if len(sys.argv) > 3 else None

    if target_wid:
        idx = state._resolve_name(target_wid, sessions)
        if not idx:
            print(f"No session '{target_wid}'.", file=sys.stderr)
            sys.exit(1)
        _debug_state_detail(idx, sessions)
    else:
        _debug_state_overview(sessions)


def _debug_state_overview(sessions: dict):
    """Print overview of all sessions and global state."""
    from astra import routing
    if not sessions:
        print("Sessions: 0")
    else:
        statuses = routing._get_session_statuses(sessions)
        print(f"Sessions: {len(sessions)}")
        for idx in tmux._sort_session_keys(sessions):
            info = sessions[idx]
            project = info.project if hasattr(info, "project") else info[1]
            cli_type = info.cli if hasattr(info, "cli") else "claude"
            status = statuses.get(idx, "?")
            busy_ts = state._busy_since(idx)
            busy_note = ""
            if status == "busy" and busy_ts:
                elapsed = int(time.time() - busy_ts)
                busy_note = f" ({elapsed}s)"
            display = tmux._display_wid(idx, sessions)
            print(f"  {display:5s} {project} ({cli_type}) {status}{busy_note}")

    # Global toggles
    focus = state._load_focus_state()
    deepfocus = state._load_deepfocus_state()
    smartfocus = state._load_smartfocus_state()
    god_wids = state._god_mode_wids()
    focus_str = focus["wid"] if focus else "off"
    deepfocus_str = deepfocus["wid"] if deepfocus else "off"
    smartfocus_str = smartfocus["wid"] if smartfocus else "off"
    god_str = ", ".join(god_wids) if god_wids else "off"
    af = "on" if state._is_autofocus_enabled() else "off"
    local = "on" if state._is_local_suppress_enabled() else "off"
    debug = "on" if config._is_debug_enabled() else "off"
    print(f"Focus: {focus_str} | Deepfocus: {deepfocus_str} | Smartfocus: {smartfocus_str}")
    print(f"God: {god_str} | Autofocus: {af} | Local: {local} | Debug: {debug}")

    # Per-session state
    for idx in tmux._sort_session_keys(sessions):
        prompt_info = _read_prompt_file(idx)
        bash_cmd = _read_bash_cmd_file(idx)
        queued = state._load_queued_msgs(idx)
        parts = []
        if prompt_info:
            n = prompt_info.get("total", 0)
            sc = prompt_info.get("shortcuts", {})
            ft = prompt_info.get("free_text_at")
            parts.append(f"prompt (total={n}, shortcuts={{{','.join(f'{k}:{v}' for k, v in sc.items())}}}, free_text_at={ft})")
        else:
            parts.append("no prompt")
        if bash_cmd:
            parts.append(f"bash_cmd")
        parts.append(f"{len(queued)} queued")
        display = tmux._display_wid(idx, sessions)
        print(f"  {display}: {', '.join(parts)}")

    # Pending signals
    pending = 0
    if os.path.isdir(config.SIGNAL_DIR):
        for f in os.listdir(config.SIGNAL_DIR):
            if f.endswith(".json") and not f.startswith("_"):
                pending += 1
    print(f"Pending signals: {pending}")


def _debug_state_detail(idx: str, sessions: dict):
    """Print detailed state for a single session."""
    from astra import routing
    info = sessions[idx]
    project = info.project if hasattr(info, "project") else info[1]
    cli_type = info.cli if hasattr(info, "cli") else "claude"
    pane = info.pane_target if hasattr(info, "pane_target") else info[0]
    pane_id = info.pane_id if hasattr(info, "pane_id") else "?"

    idle, typed = routing._pane_idle_state(pane)
    status = "idle" if idle else "busy"
    busy = state._is_busy(idx)
    busy_ts = state._busy_since(idx)

    print(f"{idx} ({project}, {cli_type}):")
    print(f"  Pane: {pane} (id={pane_id}) | Status: {status}")

    prompt_info = _read_prompt_file(idx)
    if prompt_info:
        total = prompt_info.get("total", 0)
        sc = prompt_info.get("shortcuts", {})
        ft = prompt_info.get("free_text_at")
        print(f"  Prompt: total={total}, shortcuts={{{','.join(f'{k}:{v}' for k, v in sc.items())}}}, free_text_at={ft}")
    else:
        print("  Prompt: (none)")

    bash_cmd = _read_bash_cmd_file(idx)
    queued = state._load_queued_msgs(idx)
    name = state._load_session_names().get(idx, "")
    god = state._is_god_mode_for(idx)
    print(f"  Bash cmd: {bash_cmd[:100] if bash_cmd else '(none)'} | Queued: {len(queued)} | Busy flag: {'yes' if busy else 'no'}")
    if busy_ts:
        elapsed = int(time.time() - busy_ts)
        print(f"  Busy since: {elapsed}s ago")
    print(f"  Name: {name or '(none)'} | God: {'yes' if god else 'no'}")
    if typed:
        print(f"  Typed text: {typed[:100]}")


def _read_prompt_file(wid: str) -> dict | None:
    """Read active prompt file NON-DESTRUCTIVELY (unlike load_active_prompt which deletes)."""
    path = os.path.join(config.SIGNAL_DIR, f"_active_prompt_{wid}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _read_bash_cmd_file(wid: str) -> str:
    """Read bash command file non-destructively."""
    path = os.path.join(config.SIGNAL_DIR, f"_bash_cmd_{wid}.json")
    try:
        with open(path) as f:
            return json.load(f).get("cmd", "")
    except (OSError, json.JSONDecodeError):
        return ""


def _debug_inject():
    """Write a fake signal file for testing."""
    args = sys.argv[3:]
    if len(args) < 2:
        print("Usage: astra debug inject <event> <wid> [args...]", file=sys.stderr)
        print("Events: stop, perm, question", file=sys.stderr)
        sys.exit(1)

    event_type = args[0]
    raw_wid = args[1]
    extra_args = args[2:]

    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    idx = state._resolve_name(raw_wid, sessions)
    if idx and idx in sessions:
        info = sessions[idx]
        pane = info.pane_target if hasattr(info, "pane_target") else info[0]
        project = info.project if hasattr(info, "project") else info[1]
        cli = info.cli if hasattr(info, "cli") else "claude"
    else:
        idx = raw_wid if raw_wid.startswith("w") else f"w{raw_wid}"
        pane = ""
        project = "unknown"
        cli = "claude"

    signal = {"event": "", "pane": pane, "wid": idx, "project": project, "cli": cli}

    if event_type == "stop":
        signal["event"] = "stop"
    elif event_type == "perm":
        signal["event"] = "permission"
        if extra_args and extra_args[0] == "--plan":
            signal["message"] = "plan approval"
        else:
            cmd = extra_args[0] if extra_args else "echo hello"
            signal["cmd"] = cmd
            signal["message"] = "permission"
    elif event_type == "question":
        signal["event"] = "question"
        # --multi: inject 3 questions to test multi-question flow
        if extra_args and extra_args[0] == "--multi":
            signal["questions"] = [
                {"question": "Which approach?", "header": "Approach",
                 "options": [
                     {"label": "Option A", "description": "First approach"},
                     {"label": "Option B", "description": "Second approach"},
                 ], "multiSelect": False},
                {"question": "Which style?", "header": "Style",
                 "options": [
                     {"label": "Minimal", "description": "Keep it simple"},
                     {"label": "Detailed", "description": "Full coverage"},
                     {"label": "Auto", "description": "Let Claude decide"},
                 ], "multiSelect": False},
                {"question": "Run tests?", "header": "Tests",
                 "options": [
                     {"label": "Yes", "description": "Run test suite"},
                     {"label": "No", "description": "Skip tests"},
                 ], "multiSelect": False},
            ]
        else:
            signal["questions"] = [{
                "question": "Test question?",
                "options": [
                    {"label": "Option A", "description": "First choice"},
                    {"label": "Option B", "description": "Second choice"},
                ],
            }]
    else:
        print(f"Unknown event type: {event_type}", file=sys.stderr)
        print("Events: stop, perm, question", file=sys.stderr)
        sys.exit(1)

    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    filename = f"{time.time():.6f}_{os.getpid()}.json"
    path = os.path.join(config.SIGNAL_DIR, filename)
    with open(path, "w") as f:
        json.dump(signal, f)
    print(f"Injected {event_type} signal for {idx}: {filename}")


def _check_bare_underscores(text: str) -> bool:
    """Check if text has bare underscores outside code spans/blocks that would break Markdown V1.

    Returns True if problematic underscores found.
    """
    # Remove code blocks
    cleaned = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
    # Remove inline code spans
    cleaned = re.sub(r'`[^`]+`', '', cleaned)
    # Check for underscores in remaining text
    return '_' in cleaned


def _debug_tick():
    """Dry-run one listener tick, intercepting Telegram I/O."""
    from unittest.mock import patch as mock_patch, MagicMock
    from astra import routing

    sessions = tmux.scan_claude_sessions()
    state._current_sessions = sessions
    statuses = routing._get_session_statuses(sessions)
    interrupted = {idx for idx, st in statuses.items() if st == "interrupted"}

    s = listener._ListenerState(
        sessions=sessions,
        last_scan=time.time(),
        offset=0,
        interrupted_notified=interrupted,
        god_wids=state._god_mode_wids(),
    )

    collected: list[dict] = []

    def fake_tg_send(text, chat_id="", reply_markup=None, silent=False):
        collected.append({"type": "send", "text": text, "reply_markup": reply_markup, "silent": silent})
        return len(collected)

    def fake_send_long(header, body, wid="", reply_markup=None, footer="", silent=False):
        collected.append({"type": "long", "header": header, "body": body, "footer": footer,
                          "reply_markup": reply_markup, "silent": silent})
        return len(collected)

    def fake_fire_and_forget(fn, *args, **kwargs):
        fn(*args, **kwargs)

    with mock_patch.object(telegram, "tg_send", side_effect=fake_tg_send), \
         mock_patch.object(telegram, "_send_long_message", side_effect=fake_send_long), \
         mock_patch.object(telegram, "_poll_updates", return_value=(None, 0)), \
         mock_patch.object(telegram, "_fire_and_forget", side_effect=fake_fire_and_forget), \
         mock_patch.object(telegram, "_answer_callback_query"), \
         mock_patch.object(telegram, "_remove_inline_keyboard"), \
         mock_patch.object(listener, "_check_file_changes", return_value=False):
        result = listener._listen_tick(s)

    if not collected:
        print("(no messages sent)")
    else:
        for i, msg in enumerate(collected, 1):
            kind = msg["type"].upper()
            print(f"[{i}] {kind}:")
            if msg["type"] == "long":
                print(f"  Header: {msg['header'].strip()}")
                body_lines = msg["body"].splitlines()
                print(f"  Body: ({len(body_lines)} lines)")
                for line in body_lines[:5]:
                    print(f"    {line}")
                if len(body_lines) > 5:
                    print(f"    ... ({len(body_lines) - 5} more)")
                if msg["footer"]:
                    print(f"  Footer: {msg['footer'].strip()}")
                # _send_long_message escapes inner ``` to ''' then wraps in code block
                safe_body = msg["body"].replace("```", "'''")
                full_text = msg["header"] + "```\n" + safe_body + "\n```" + msg.get("footer", "")
            else:
                text = msg["text"]
                lines = text.splitlines()
                for line in lines[:8]:
                    print(f"  {line}")
                if len(lines) > 8:
                    print(f"  ... ({len(lines) - 8} more)")
                full_text = text

            # Keyboard layout
            kb = msg.get("reply_markup")
            if kb and "inline_keyboard" in kb:
                parts = []
                cb_values = []
                for row in kb["inline_keyboard"]:
                    for btn in row:
                        label = btn.get("text", "?")
                        cb = btn.get("callback_data", "?")
                        parts.append(f"[{label}:{cb}]")
                        cb_values.append(cb)
                print(f"  Keyboard: {' '.join(parts)}")
                # Duplicate callback warning
                if len(cb_values) != len(set(cb_values)):
                    print(f"  ⚠️ DUPLICATE callback_data detected!")
            elif kb and "keyboard" in kb:
                print("  Keyboard: reply_kb")

            # Markdown V1 check
            if _check_bare_underscores(full_text):
                print(f"  ⚠️ Bare underscores outside code blocks (Markdown V1 risk)")
            else:
                print(f"  ✓ Markdown OK")

    if result:
        print(f"\nTick result: {result}")
    else:
        print(f"\nTick result: continue")


def _debug_smartfocus():
    """Run smartfocus pipeline once on a target session and print diagnostics."""
    from astra import profiles, content
    target = sys.argv[3] if len(sys.argv) > 3 else None
    sessions = tmux.scan_claude_sessions()
    if not sessions:
        print("No active sessions.")
        return

    # Resolve target or use smartfocus state
    sf_state = state._load_smartfocus_state()
    if target:
        idx = state._resolve_name(target.lstrip("w")) or target
        if idx not in sessions:
            print(f"Session '{target}' not found. Available: {list(sessions.keys())}")
            return
    elif sf_state:
        idx = sf_state["wid"]
        print(f"Using active smartfocus target: {idx}")
    else:
        print("Usage: astra debug smartfocus <wN>")
        print(f"Available: {list(sessions.keys())}")
        return

    info = sessions[idx]
    pane, project = info
    profile = profiles.get_profile(info.cli) if hasattr(info, 'cli') else profiles.CLAUDE
    pw = tmux._get_pane_width(pane)
    pc = profile.prompt_char
    bullet = profile.response_bullet
    tool_re = profile.tool_header_re

    print(f"Session: {idx} pane={pane} project={project} width={pw}")
    print(f"Profile: {profile.name} prompt='{pc}' bullet='{bullet}'")
    print()

    # Step 1: Raw capture
    raw = tmux._capture_pane(pane, 200)
    raw_lines = raw.splitlines()
    print(f"[1] Raw capture: {len(raw_lines)} lines")
    print(f"    Last 3 raw:")
    for l in raw_lines[-3:]:
        print(f"      {l[:120]}")

    # Step 2: Idle detection
    idle = any(l.strip().startswith(pc) for l in raw_lines[-5:])
    print(f"\n[2] Idle detection: {idle}")
    print(f"    Last 5 lines checked:")
    for l in raw_lines[-5:]:
        s = l.strip()
        marker = " <-- PROMPT" if s.startswith(pc) else ""
        print(f"      '{s[:80]}'{marker}")

    # Step 3: Filter noise
    filtered = content._filter_noise(raw, profile=profile)
    print(f"\n[3] After _filter_noise: {len(filtered)} lines (removed {len(raw_lines) - len(filtered)})")

    # Step 4: Strip prompt at end
    pre_strip = len(filtered)
    for i in range(len(filtered) - 1, -1, -1):
        if filtered[i].strip().startswith(pc):
            filtered = filtered[:i]
            break
    print(f"[4] After prompt strip: {len(filtered)} lines (removed {pre_strip - len(filtered)})")

    # Step 5: Join wrapped lines
    if pw:
        filtered = tmux._join_wrapped_lines(filtered, pw)
        print(f"[5] After wrap join (width={pw}): {len(filtered)} lines")

    # Step 6: Show content summary
    print(f"\n[6] Final cur_lines ({len(filtered)} lines):")
    bullets = [(i, l) for i, l in enumerate(filtered) if l.strip().startswith(bullet)]
    print(f"    Bullet lines: {len(bullets)}")
    for i, l in bullets:
        is_tool = "TOOL" if re.match(tool_re, l.strip()) else "TEXT"
        print(f"      [{i}] ({is_tool}) {l.strip()[:100]}")

    # Step 7: If prev exists (from state file or second run), show diff
    prev_file = os.path.join(config.SIGNAL_DIR, "_debug_sf_prev.json")
    try:
        import json as _json
        with open(prev_file) as f:
            prev = _json.load(f)
    except (OSError, json.JSONDecodeError):
        prev = None

    if prev:
        new = content._compute_new_lines(prev, filtered)
        print(f"\n[7] Diff vs previous capture: {len(new)} new lines")
        if new:
            for l in new[:10]:
                print(f"      + {l[:120]}")
            if len(new) > 10:
                print(f"      ... ({len(new) - 10} more)")
        else:
            print("      (no changes)")
    else:
        print(f"\n[7] No previous capture (first run). Run again to see diff.")

    # Save current as prev for next run
    with open(prev_file, "w") as f:
        json.dump(filtered, f)

    print(f"\n    Prev saved to {prev_file}")


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


def cmd_smartfocus():
    """Activate or deactivate smartfocus from CLI."""
    arg = sys.argv[2] if len(sys.argv) > 2 else ""
    if arg == "off":
        state._clear_smartfocus_state()
        print("Smartfocus: off.")
        return
    if not arg:
        sf = state._load_smartfocus_state()
        if sf:
            print(f"Smartfocus: on — watching {sf['wid']} ({sf['project']})")
        else:
            print("Smartfocus: off")
        return
    # smartfocus wN — attach to specific session
    sessions = tmux.scan_claude_sessions()
    idx = state._resolve_name(arg.lstrip("w")) or arg
    if not idx.startswith("w"):
        idx = f"w{idx}"
    if idx not in sessions:
        print(f"Session '{arg}' not found. Available: {list(sessions.keys())}")
        sys.exit(1)
    pane, project = sessions[idx]
    state._save_smartfocus_state(idx, pane, project)
    state._clear_focus_state()
    state._clear_deepfocus_state()
    print(f"Smartfocus: on — watching {idx} ({project})")


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


def cmd_keys():
    """Send keys to a session via tmux send-keys."""
    from astra.commands import _resolve_key
    if len(sys.argv) < 4:
        print("Usage: astra keys <wN> <key...>", file=sys.stderr)
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
    key_tokens = sys.argv[3:]
    tmux_keys = [_resolve_key(t) for t in key_tokens]
    keys_arg = " ".join(tmux_keys)
    subprocess.run(["bash", "-c",
                    f"tmux send-keys -t {p} {keys_arg}"], timeout=5)
    print(f"Sent {' '.join(key_tokens)} to {tmux._display_wid(idx, sessions)} ({project}).")


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
        pane_target = None
        for _ in range(6):
            sessions = tmux.scan_claude_sessions()
            new_wid = tmux.resolve_session_id(f"w{new_idx}", sessions)
            if new_wid:
                pane_target = sessions[new_wid].pane_target
                break
            time.sleep(1)
        if not new_wid:
            new_wid = f"w{new_idx}a"
        # Auto-setup: accept trust dialog, switch out of plan mode
        if pane_target:
            time.sleep(2)
            _auto_setup_session(pane_target)
        proj = work_dir.rstrip("/").rsplit("/", 1)[-1]
        print(f"Started {profile.display_name} in {new_wid} ({proj}): {work_dir}")
    except Exception as e:
        print(f"Failed to start session: {e}", file=sys.stderr)
        sys.exit(1)


def _auto_setup_session(pane_target: str):
    """Accept trust dialog and switch out of plan mode for new sessions."""
    try:
        p = shlex.quote(pane_target)
        raw = tmux._capture_pane(pane_target, 30)
        # Accept workspace trust dialog if present
        if "trust" in raw.lower() and ("Yes" in raw or "1." in raw):
            subprocess.run(["bash", "-c",
                            f"tmux send-keys -t {p} Enter"], timeout=5)
            time.sleep(3)
            raw = tmux._capture_pane(pane_target, 30)
        # Switch out of plan mode (Shift+Tab cycles: plan → auto → ...)
        if "plan mode on" in raw.lower():
            subprocess.run(["bash", "-c",
                            f"tmux send-keys -t {p} BTab"], timeout=5)
    except Exception:
        pass


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
  smartfocus [wN|off]          Activate/deactivate smartfocus
  notification [1..7|all|off]  Configure notification levels
  debug [on|off|clear|N]       Debug log for outbound Telegram messages
  debug state [wN]             Dump internal state (sessions, prompts, flags)
  debug inject <event> <wid>   Inject a fake signal (stop, perm, question)
  debug tick                   Dry-run one listener tick (intercepts Telegram)

Session management (no Telegram credentials needed):
  status [wN] [lines]          List sessions or show pane output
  focus [wN]                   Set focus target
  deepfocus [wN]               Set deepfocus target
  unfocus                      Stop all monitoring
  interrupt [wN]               Interrupt session (Escape)
  keys <wN> <key...>           Send keys (e.g. shift+tab, ctrl+c)
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
  /keys wN key...      Send keys (e.g. /keys w4 shift+tab)
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
  k5 shift+tab         /keys w5 shift+tab
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
        "autofocus": cmd_autofocus, "smartfocus": cmd_smartfocus,
        "notification": cmd_notification, "status": cmd_status,
        "focus": cmd_focus, "deepfocus": cmd_deepfocus, "unfocus": cmd_unfocus,
        "clear": cmd_clear, "interrupt": cmd_interrupt, "keys": cmd_keys,
        "name": cmd_name, "saved": cmd_saved, "log": cmd_log, "new": cmd_new,
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
