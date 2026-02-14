"""Command handling and callback dispatch."""
import os
import re
import shlex
import subprocess
import time

from tg_hook import config, telegram, tmux, state, content, routing


_ALIASES: dict[str, str] = {"?": "/help", "uf": "/unfocus"}


def _any_active_prompt() -> bool:
    """Check if any active prompt state files exist."""
    if not os.path.isdir(config.SIGNAL_DIR):
        return False
    return any(f.startswith("_active_prompt_") for f in os.listdir(config.SIGNAL_DIR))


def _resolve_alias(text: str, has_active_prompt: bool) -> str:
    """Resolve short aliases. Only ambiguous ones (?, uf) suppressed during prompts.
    Digit-containing aliases (s4, f4, df4, i4) always resolve — they're unambiguous."""
    stripped = text.strip()
    # Simple aliases — ambiguous during prompts (could be prompt responses)
    if stripped in _ALIASES:
        if has_active_prompt:
            return text
        return _ALIASES[stripped]
    # Digit-containing aliases always resolve (unambiguous)
    m = re.match(r"^s(\d+)?(?:\s+(\d+))?$", stripped)
    if m:
        parts = ["/status"]
        if m.group(1):
            parts.append(f"w{m.group(1)}")
        if m.group(2):
            parts.append(m.group(2))
        return " ".join(parts)
    m = re.match(r"^f(\d+)$", stripped)
    if m:
        return f"/focus w{m.group(1)}"
    m = re.match(r"^df(\d+)$", stripped)
    if m:
        return f"/deepfocus w{m.group(1)}"
    m = re.match(r"^i(\d+)$", stripped)
    if m:
        return f"/interrupt w{m.group(1)}"
    return text


def _handle_command(text: str, sessions: dict, last_win_idx: str | None) -> tuple[str | None, dict, str | None]:
    """Handle a command in active mode. Returns (action, sessions, last_win_idx).
    action is 'pause', 'quit', or None (continue processing)."""

    if text.lower() == "/stop":
        telegram.tg_send("⏸ Paused. Send `/start` to resume or `/quit` to exit.")
        config._log("listen", "Paused.")
        return "pause", sessions, last_win_idx

    if text.lower() == "/quit":
        quit_kb = telegram._build_inline_keyboard([
            [("\u2705 Yes", "quit_y"), ("\u274c No", "quit_n")],
        ])
        telegram.tg_send("⚠️ Shut down listener? Reply `y` to confirm.", reply_markup=quit_kb)
        return "quit_pending", sessions, last_win_idx

    if text.lower() == "/help":
        sessions = tmux.scan_claude_sessions()
        help_lines = [
            "📖 *Commands:*",
            "`/sessions` — list active Claude sessions",
            "`/status [wN] [lines]` — show last response or N filtered lines",
            "`/last [wN]` — re-send last Telegram message for a session",
            "`/focus wN` — watch completed responses",
            "`/deepfocus wN` — stream all output in real-time",
            "`/unfocus` — stop monitoring",
            "`/name wN [label]` — name a session",
            "`/new [dir]` — start new Claude session (default: `~/projects/`)",
            "`/interrupt [wN]` — interrupt current task (Esc)",
            "`/kill wN` — exit a Claude session (Ctrl+C x3)",
            "`/stop` — pause the listener",
            "`/quit` — shut down the listener",
            "",
            "*Aliases:*",
            "`s` status | `s4` status w4 | `s4 10` status w4 10",
            "`f4` focus w4 | `df4` deepfocus w4 | `uf` unfocus | `i4` interrupt w4",
            "`?` help",
            "",
            "*Routing:* prefix with `wN` (e.g. `w4 fix the bug`) or send without prefix for single/last-used session.",
            "*Photos:* send a photo to have Claude read it. Add `wN` in caption to target.",
        ]
        telegram.tg_send("\n".join(help_lines), reply_markup=tmux._sessions_keyboard(sessions))
        return None, sessions, last_win_idx

    if text.lower() == "/sessions":
        sessions = tmux.scan_claude_sessions()
        telegram.tg_send(tmux.format_sessions_message(sessions),
                         reply_markup=tmux._sessions_keyboard(sessions))
        return None, sessions, last_win_idx

    # /status [wN|name] [lines]
    status_m = re.match(r"^/status(?:\s+w?(\w[\w-]*))?(?:\s+(\d+))?$", text.lower())
    if status_m:
        raw_target = status_m.group(1)
        num_lines = int(status_m.group(2)) if status_m.group(2) else 20
        idx = state._resolve_name(raw_target, sessions) if raw_target else None
        targets = []
        if raw_target and idx:
            targets = [(idx, sessions[idx])]
        elif raw_target:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx
        elif len(sessions) == 1:
            targets = list(sessions.items())
        elif len(sessions) > 1:
            kb = tmux._command_sessions_keyboard("status", sessions)
            telegram.tg_send("📋 Status for which session?", reply_markup=kb)
            return None, sessions, last_win_idx
        else:
            telegram.tg_send("⚠️ No Claude sessions found. Send `/sessions` to rescan.")
            return None, sessions, last_win_idx
        explicit_lines = status_m.group(2) is not None
        for win_idx, (pane, project) in targets:
            pw = tmux._get_pane_width(pane)
            if explicit_lines:
                raw = tmux._capture_pane(pane, num_lines * 3 + 20)
                filtered = content.clean_pane_status(raw, pw)
                lines = filtered.splitlines()
                status_content = "\n".join(lines[-num_lines:]) if lines else ""
            else:
                for n in (30, 80, 200):
                    raw = tmux._capture_pane(pane, n)
                    if content._has_response_start(raw):
                        break
                raw_view = content.clean_pane_status(tmux._capture_pane(pane, 30), pw)
                if content._has_response_start(raw):
                    bullet_view = content.clean_pane_content(raw, "stop", pw)
                    status_content = bullet_view if len(bullet_view) >= len(raw_view) else raw_view
                else:
                    status_content = raw_view
            status_content = status_content or "(empty)"
            header = f"📋 {state._wid_label(win_idx)} — `{project}`:\n\n"
            telegram._send_long_message(header, status_content, win_idx)
        return None, sessions, last_win_idx

    # /deepfocus (bare — show session picker)
    if text.lower().strip() == "/deepfocus":
        sessions = tmux.scan_claude_sessions()
        kb = tmux._command_sessions_keyboard("deepfocus", sessions)
        if kb:
            telegram.tg_send("🔬 Deep focus on which session?", reply_markup=kb)
        else:
            telegram.tg_send("⚠️ No Claude sessions found.")
        return None, sessions, last_win_idx

    # /deepfocus wN|name
    dfocus_m = re.match(r"^/deepfocus\s+w?(\w[\w-]*)$", text.lower())
    if dfocus_m:
        raw_target = dfocus_m.group(1)
        idx = state._resolve_name(raw_target, sessions)
        if idx:
            pane, project = sessions[idx]
            state._save_deepfocus_state(idx, pane, project)
            state._clear_focus_state()
            pw = tmux._get_pane_width(pane)
            df_content = content.clean_pane_status(tmux._capture_pane(pane, 20), pw) or "(empty)"
            telegram.tg_send(f"🔬 Deep focus on {state._wid_label(idx)} (`{project}`). Send `/unfocus` to stop.\n\n```\n{df_content[-3000:]}\n```")
            return None, sessions, idx
        else:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # /focus (bare — show session picker)
    if text.lower().strip() == "/focus":
        sessions = tmux.scan_claude_sessions()
        kb = tmux._command_sessions_keyboard("focus", sessions)
        if kb:
            telegram.tg_send("🔍 Focus on which session?", reply_markup=kb)
        else:
            telegram.tg_send("⚠️ No Claude sessions found.")
        return None, sessions, last_win_idx

    # /focus wN|name
    focus_m = re.match(r"^/focus\s+w?(\w[\w-]*)$", text.lower())
    if focus_m:
        raw_target = focus_m.group(1)
        idx = state._resolve_name(raw_target, sessions)
        if idx:
            pane, project = sessions[idx]
            state._save_focus_state(idx, pane, project)
            state._clear_deepfocus_state()
            pw = tmux._get_pane_width(pane)
            fc_content = content.clean_pane_status(tmux._capture_pane(pane, 20), pw) or "(empty)"
            telegram.tg_send(f"🔍 Focusing on {state._wid_label(idx)} (`{project}`). Send `/unfocus` to stop.\n\n```\n{fc_content[-3000:]}\n```")
            return None, sessions, idx
        else:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # /unfocus
    if text.lower() == "/unfocus":
        state._clear_focus_state()
        state._clear_deepfocus_state()
        telegram.tg_send("🔍 Focus stopped.")
        return None, sessions, last_win_idx

    # /name wN|name [label]
    name_m = re.match(r"^/name\s+w?(\w[\w-]*)(?:\s+(.+))?$", text)
    if name_m:
        raw_target = name_m.group(1)
        idx = state._resolve_name(raw_target, sessions) or raw_target
        label = name_m.group(2).strip() if name_m.group(2) else None
        if label:
            state._save_session_name(idx, label)
            telegram.tg_send(f"✏️ Session `w{idx}` named `{label}`.")
        else:
            state._clear_session_name(idx)
            telegram.tg_send(f"✏️ Session `w{idx}` name cleared.")
        return None, sessions, last_win_idx

    # /new [dir]
    new_m = re.match(r"^/new(?:\s+(.+))?$", text)
    if new_m:
        dir_arg = new_m.group(1).strip() if new_m.group(1) else None
        if dir_arg:
            work_dir = os.path.expanduser(dir_arg)
        else:
            ts = time.strftime("%m%d-%H%M")
            work_dir = os.path.expanduser(f"~/projects/claude-{ts}")
        os.makedirs(work_dir, exist_ok=True)
        try:
            result = subprocess.run(
                ["tmux", "new-window", "-d", "-P", "-F", "#{window_index}",
                 f"bash -c 'cd {shlex.quote(work_dir)} && claude'"],
                capture_output=True, text=True, timeout=10,
            )
            new_idx = result.stdout.strip()
            sessions = tmux.scan_claude_sessions()
            proj = work_dir.rstrip("/").rsplit("/", 1)[-1]
            telegram.tg_send(f"🚀 Started Claude in `w{new_idx}` (`{proj}`):\n`{work_dir}`")
            return None, sessions, new_idx
        except Exception as e:
            telegram.tg_send(f"⚠️ Failed to start session: `{e}`")
            return None, sessions, last_win_idx

    # /interrupt [wN|name]
    int_m = re.match(r"^/interrupt(?:\s+w?(\w[\w-]*))?$", text.lower())
    if int_m:
        raw_target = int_m.group(1)
        idx = state._resolve_name(raw_target, sessions) if raw_target else None
        if idx:
            pane, project = sessions[idx]
            p = shlex.quote(pane)
            subprocess.run(["bash", "-c", f"tmux send-keys -t {p} Escape"], timeout=5)
            telegram.tg_send(f"⏹ Interrupted {state._wid_label(idx)} (`{project}`).")
            return None, sessions, idx
        elif raw_target:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
        elif len(sessions) == 1:
            idx = next(iter(sessions))
            pane, project = sessions[idx]
            p = shlex.quote(pane)
            subprocess.run(["bash", "-c", f"tmux send-keys -t {p} Escape"], timeout=5)
            telegram.tg_send(f"⏹ Interrupted {state._wid_label(idx)} (`{project}`).")
            return None, sessions, idx
        else:
            sessions = tmux.scan_claude_sessions()
            kb = tmux._command_sessions_keyboard("interrupt", sessions)
            if kb:
                telegram.tg_send("⏹ Interrupt which session?", reply_markup=kb)
            else:
                telegram.tg_send("⚠️ No Claude sessions found.")
        return None, sessions, last_win_idx

    # /kill (bare — show session picker)
    if text.lower().strip() == "/kill":
        sessions = tmux.scan_claude_sessions()
        kb = tmux._command_sessions_keyboard("kill", sessions)
        if kb:
            telegram.tg_send("🛑 Kill which session?", reply_markup=kb)
        else:
            telegram.tg_send("⚠️ No Claude sessions found.")
        return None, sessions, last_win_idx

    # /kill wN|name
    kill_m = re.match(r"^/kill\s+w?(\w[\w-]*)$", text.lower())
    if kill_m:
        raw_target = kill_m.group(1)
        idx = state._resolve_name(raw_target, sessions)
        if idx:
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
                telegram.tg_send(f"⚠️ {state._wid_label(idx)} (`{project}`) still running after Ctrl+C.")
            else:
                telegram.tg_send(f"🛑 Killed {state._wid_label(idx)} (`{project}`).")
            return None, sessions, last_win_idx
        else:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # /last [wN|name]
    last_m = re.match(r"^/last(?:\s+w?(\w[\w-]*))?$", text.lower())
    if last_m:
        raw_target = last_m.group(1)
        idx = state._resolve_name(raw_target, sessions) if raw_target else None
        if idx and idx in config._last_messages:
            telegram.tg_send(config._last_messages[idx])
        elif raw_target:
            telegram.tg_send(f"⚠️ No saved message for `{raw_target}`.")
        elif len(config._last_messages) == 1:
            telegram.tg_send(list(config._last_messages.values())[0])
        elif config._last_messages:
            has_msgs = {k: sessions[k] for k in config._last_messages if k in sessions}
            kb = tmux._command_sessions_keyboard("last", has_msgs) if has_msgs else None
            if kb:
                telegram.tg_send("📋 Last message for which session?", reply_markup=kb)
            else:
                telegram.tg_send("⚠️ No saved messages.")
        else:
            telegram.tg_send("⚠️ No saved messages yet.")
        return None, sessions, last_win_idx

    # Parse wN prefix
    m = re.match(r"^w(\d+)\s+(.*)", text, re.DOTALL)
    if m:
        win_idx = m.group(1)
        prompt = m.group(2).strip()
        if win_idx in sessions:
            pane, project = sessions[win_idx]
            confirm = routing.route_to_pane(pane, win_idx, prompt)
            telegram.tg_send(confirm)
            config._log(f"w{win_idx}", confirm[:100])
            return None, sessions, win_idx
        else:
            telegram.tg_send(f"⚠️ No Claude session at `w{win_idx}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # Name prefix: first word matches a known session name
    words = text.split(None, 1)
    if len(words) == 2:
        name_idx = state._resolve_name(words[0], sessions)
        if name_idx is not None:
            pane, project = sessions[name_idx]
            confirm = routing.route_to_pane(pane, name_idx, words[1].strip())
            telegram.tg_send(confirm)
            config._log(f"w{name_idx}", confirm[:100])
            return None, sessions, name_idx

    # No prefix — route to last used or only session
    target_idx = None
    if len(sessions) == 1:
        target_idx = next(iter(sessions))
    elif last_win_idx and last_win_idx in sessions:
        target_idx = last_win_idx

    if target_idx:
        pane, project = sessions[target_idx]
        confirm = routing.route_to_pane(pane, target_idx, text)
        telegram.tg_send(confirm)
        config._log(f"w{target_idx}", confirm[:100])
        return None, sessions, target_idx
    elif len(sessions) == 0:
        telegram.tg_send("⚠️ No Claude sessions found. Send `/sessions` to rescan.")
    else:
        telegram.tg_send(f"⚠️ Multiple sessions — prefix with `wN`.\n{tmux.format_sessions_message(sessions)}",
                         reply_markup=tmux._sessions_keyboard(sessions))

    return None, sessions, last_win_idx


def _handle_callback(callback: dict, sessions: dict,
                     last_win_idx: str | None) -> tuple[dict, str | None, str | None]:
    """Handle an inline keyboard callback. Returns (sessions, last_win_idx, action)."""
    cb_id = callback["id"]
    cb_data = callback.get("data", "")
    msg_id = callback.get("message_id", 0)

    telegram._answer_callback_query(cb_id)
    if msg_id:
        telegram._remove_inline_keyboard(msg_id)

    if cb_data == "quit_y":
        telegram.tg_send("👋 Bye.")
        return sessions, last_win_idx, "quit"
    if cb_data == "quit_n":
        telegram.tg_send("Cancelled.")
        return sessions, last_win_idx, None

    # Permission callback: perm_{wid}_{n}
    m = re.match(r"^perm_(w\d+)_(\d+)$", cb_data)
    if m:
        wid, n = m.group(1), int(m.group(2))
        prompt = state.load_active_prompt(wid)
        if prompt:
            total = prompt.get("total", 3)
            routing._select_option(prompt.get("pane", ""), n)
            if n == 1:
                label = "\u2705 Allowed"
            elif n == 2:
                label = "\u2705 Always allowed"
            elif n == total:
                label = "\u274c Denied"
            else:
                label = f"Selected option {n}"
            w_idx = wid.lstrip("w")
            telegram.tg_send(f"{label} in {state._wid_label(w_idx)}")
            config._log("callback", f"perm {wid} option {n}")
        else:
            telegram._answer_callback_query(cb_id, "Prompt expired")
        return sessions, last_win_idx, None

    # Question callback: q_{wid}_{n}
    m = re.match(r"^q_(w\d+)_(\d+)$", cb_data)
    if m:
        wid, n_str = m.group(1), m.group(2)
        win_idx = wid.lstrip("w")
        if win_idx in sessions:
            pane = sessions[win_idx][0]
            confirm = routing.route_to_pane(pane, win_idx, n_str)
            telegram.tg_send(confirm)
            last_win_idx = win_idx
        return sessions, last_win_idx, None

    # Command callbacks: cmd_{action}_{wid}
    m = re.match(r"^cmd_(status|focus|deepfocus|interrupt|kill|last)_(w?)(\d+)$", cb_data)
    if m:
        cmd, _, idx = m.group(1), m.group(2), m.group(3)
        cmd_text = f"/{cmd} w{idx}"
        _, sessions, last_win_idx = _handle_command(
            cmd_text, sessions, last_win_idx)
        return sessions, last_win_idx, None

    # Session select: sess_{wid}
    m = re.match(r"^sess_(\d+)$", cb_data)
    if m:
        idx = m.group(1)
        last_win_idx = idx
        cmd_text = f"/status w{idx}"
        _, sessions, last_win_idx = _handle_command(
            cmd_text, sessions, last_win_idx)
        return sessions, last_win_idx, None

    config._log("callback", f"unknown callback_data: {cb_data}")
    return sessions, last_win_idx, None
