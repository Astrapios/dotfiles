"""Command handling and callback dispatch."""
import os
import re
import shlex
import subprocess
import time

from astra import config, telegram, tmux, state, content, routing, profiles

# Notification category constants (see state._NOTIFICATION_CATEGORIES)
_CAT_ERROR = 4
_CAT_CONFIRM = 7

_ALIASES: dict[str, str] = {"?": "/help", "uf": "/unfocus", "sv": "/saved", "af": "/autofocus",
                            "lv": "/local", "ga": "/god all", "goff": "/god off",
                            "gq": "/god quiet", "gl": "/god loud",
                            "c": "/clear", "noti": "/notification", "k": "/keys"}

# Human-readable key names → tmux send-keys names (looked up case-insensitively)
_KEYS_MAP: dict[str, str] = {
    "shift+tab": "BTab", "s-tab": "BTab",
    "esc": "Escape", "escape": "Escape",
    "enter": "Enter", "return": "Enter", "cr": "Enter",
    "tab": "Tab", "space": "Space",
    "up": "Up", "down": "Down", "left": "Left", "right": "Right",
    "bs": "BSpace", "backspace": "BSpace",
    "del": "DC", "delete": "DC",
    "home": "Home", "end": "End",
    "pgup": "PPage", "pageup": "PPage",
    "pgdn": "NPage", "pagedown": "NPage",
}
# Add F1..F12
for _i in range(1, 13):
    _KEYS_MAP[f"f{_i}"] = f"F{_i}"

# Quick-pick combos for bare /keys (label, short_id, tmux_key)
_QUICK_KEYS: list[tuple[str, str, str]] = [
    ("Shift+Tab", "btab", "BTab"),
    ("Ctrl+C", "ctrlc", "C-c"),
    ("Escape", "esc", "Escape"),
    ("Ctrl+O", "ctrlo", "C-o"),
    ("Enter", "enter", "Enter"),
    ("\u2191 Up", "up", "Up"),
]


def _keys_combo_keyboard(wid: str) -> dict:
    """Build 2x3 inline keyboard of quick-pick key combos for a session."""
    rows = []
    for i in range(0, len(_QUICK_KEYS), 3):
        row = []
        for label, short, _tmux_key in _QUICK_KEYS[i:i + 3]:
            row.append((label, f"keys_{wid}_{short}"))
        rows.append(row)
    return telegram._build_inline_keyboard(rows)


def _resolve_key(name: str) -> str:
    """Map a human-readable key name to a tmux send-keys name.

    Handles ctrl+X / c-X patterns dynamically. Anything unrecognized
    passes through as-is (raw tmux key names like BTab, C-c work directly).
    """
    lower = name.lower()
    # Exact match in map
    if lower in _KEYS_MAP:
        return _KEYS_MAP[lower]
    # ctrl+X or c-X → C-x
    m = re.match(r"^(?:ctrl\+|c-)(.+)$", lower)
    if m:
        return f"C-{m.group(1)}"
    # Pass through as-is (raw tmux key name)
    return name


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
    # sw alias: sw4 cmd → w4 !cmd, s<name> cmd → <name> !cmd (shell in CLI)
    m = re.match(r"^sw(\d+[a-z]?)\s+(.+)$", stripped, re.DOTALL)
    if m:
        return f"w{m.group(1)} !{m.group(2)}"
    m = re.match(r"^s([a-z]\w[\w-]*)\s+(.+)$", stripped, re.DOTALL)
    if m and state._resolve_name(m.group(1)) is not None:
        return f"{m.group(1)} !{m.group(2)}"
    # Digit-containing aliases always resolve (unambiguous)
    m = re.match(r"^s(\d+[a-z]?)?(?:\s+(\d+))?$", stripped)
    if m:
        parts = ["/status"]
        if m.group(1):
            parts.append(f"w{m.group(1)}")
        if m.group(2):
            parts.append(m.group(2))
        return " ".join(parts)
    m = re.match(r"^f(\d+[a-z]?)$", stripped)
    if m:
        return f"/focus w{m.group(1)}"
    m = re.match(r"^df(\d+[a-z]?)$", stripped)
    if m:
        return f"/deepfocus w{m.group(1)}"
    m = re.match(r"^i(\d+[a-z]?)$", stripped)
    if m:
        return f"/interrupt w{m.group(1)}"
    m = re.match(r"^g(\d+[a-z]?)$", stripped)
    if m:
        return f"/god w{m.group(1)}"
    m = re.match(r"^c(\d+[a-z]?)$", stripped)
    if m:
        return f"/clear w{m.group(1)}"
    m = re.match(r"^r(\d+[a-z]?)$", stripped)
    if m:
        return f"/restart w{m.group(1)}"
    m = re.match(r"^k(\d+[a-z]?)\s+(.+)$", stripped)
    if m:
        return f"/keys w{m.group(1)} {m.group(2)}"
    m = re.match(r"^k(\d+[a-z]?)$", stripped)
    if m:
        return f"/keys w{m.group(1)}"
    # noti <args> → /notification <args>
    m = re.match(r"^noti\s+(.+)$", stripped)
    if m:
        return f"/notification {m.group(1)}"
    return text


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


def _interrupt_session(idx: str, sessions: dict):
    """Interrupt a CLI session: Escape, clear prompt, clear busy/prompt state."""
    pane, project = sessions[idx]
    p = shlex.quote(pane)
    config._mark_remote(idx)
    # Escape interrupts current operation, Ctrl+U clears the prompt line
    subprocess.run(["bash", "-c",
                    f"tmux send-keys -t {p} Escape && sleep 0.1 && "
                    f"tmux send-keys -t {p} C-u"], timeout=5)
    state._clear_busy(idx)
    state.load_active_prompt(idx)  # load = consume and delete
    telegram.tg_send(f"⏹ Interrupted {state._wid_label(idx)} (`{project}`).")


def _enable_accept_edits(pane: str):
    """Cycle Shift+Tab until 'accept edits on' mode is active."""
    p = shlex.quote(pane)
    for _ in range(5):
        try:
            raw = tmux._capture_pane(pane, 5)
        except Exception:
            return
        for line in raw.splitlines():
            s = line.strip()
            if s.startswith("\u23f5\u23f5"):
                if "accept edits on" in s.lower():
                    return
                break
        subprocess.run(["bash", "-c", f"tmux send-keys -t {p} BTab"], timeout=5)
        time.sleep(0.3)


def _god_accept_pending_prompts(wids: list[str]):
    """Auto-accept pending permission prompts for the given wids."""
    for wid in wids:
        prompt = state.load_active_prompt(wid)
        if not prompt:
            continue
        pane = prompt.get("pane", "")
        if pane:
            routing._select_option(pane, 1)
        # Clear tracked keyboard
        old_kb = config._clear_keyboard_msg(wid)
        if old_kb:
            telegram._remove_inline_keyboard(old_kb)
        label = state._wid_label(wid)
        telegram.tg_send(f"⚡ {label} Auto-accepted pending prompt.")


def _clear_suggestion_keyboard(wid: str):
    """Clear any pending suggestion keyboard and stored text for a session."""
    config._pop_suggestion(wid)
    old_kb = config._clear_keyboard_msg(wid)
    if old_kb:
        telegram._fire_and_forget(telegram._remove_inline_keyboard, old_kb)


def _maybe_activate_smartfocus(win_idx: str, pane: str, project: str, confirm: str):
    """Activate smart focus after a message is sent (not queued/prompt reply)."""
    if not (confirm.startswith("📨 Sent to") or confirm.startswith("📷 Photo sent to") or confirm.startswith("📎 Document sent to")):
        return
    config._mark_remote(win_idx)
    if not state._is_autofocus_enabled():
        return
    # Skip if manual focus or deepfocus already covers this wid
    focus = state._load_focus_state()
    if focus and focus["wid"] == win_idx:
        return
    deepfocus = state._load_deepfocus_state()
    if deepfocus and deepfocus["wid"] == win_idx:
        return
    state._save_smartfocus_state(win_idx, pane, project)


def _handle_command(text: str, sessions: dict, last_win_idx: str | None) -> tuple[str | None, dict, str | None]:
    """Handle a command in active mode. Returns (action, sessions, last_win_idx).
    action is 'pause', 'quit', or None (continue processing)."""
    state._current_sessions = sessions

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
            "`/status [wN] [lines]` — list sessions or show output",
            "`/interrupt [wN]` — interrupt current task (Esc)",
            "`/god [wN|all|off]` — auto-accept permissions",
            "`/god quiet|loud` — suppress/enable receipts",
            "`/focus wN` — watch completed responses",
            "`/deepfocus wN` — stream all output in real-time",
            "`/unfocus` — stop monitoring",
            "`/saved [wN]` — review saved messages",
            "`/last [wN]` — re-send last Telegram message",
            "",
            "*Settings:*",
            "`/autofocus [on|off]` — auto-monitor on send (default: on)",
            "`/local [on|off]` — suppress Telegram when viewing locally",
            "`/notification [1..7|all|off]` — control which alerts buzz",
            "`/name wN [label]` — name a session",
            "",
            "*Session management:*",
            "`/new [claude|gemini] [dir]` — start new session",
            "`/restart wN` — kill and relaunch with `claude -c`",
            "`/keys [wN] [key...]` — send keys or pick from combos",
            "`/kill wN` — exit a session (Ctrl+C x3)",
            "`/clear [wN]` — reset transient state",
            "`/log [N]` — show last N journal lines (default 30)",
            "`/stop` / `/quit` — pause / shut down listener",
            "",
            "*Aliases:*",
            "`s` status | `s4` status w4 | `s4 10` status w4 10",
            "`f4` focus w4 | `df4` deepfocus w4 | `uf` unfocus",
            "`i4` interrupt w4 | `sv` saved | `?` help",
            "`g4` god w4 | `ga` god all | `goff` god off",
            "`af` autofocus | `lv` local | `noti` notification",
            "`k` keys | `k5` keys w5 | `k5 shift+tab` keys w5 shift+tab",
            "`c` clear | `c4` clear w4 | `r4` restart w4",
            "",
            "*Routing:* prefix with `wN` (e.g. `w4 fix the bug`) or send without prefix for single/last-used session.",
            "*Photos:* send a photo to have the CLI read it. Add `wN` in caption to target.",
        ]
        telegram.tg_send("\n".join(help_lines), reply_markup=telegram._build_reply_keyboard())
        return None, sessions, last_win_idx

    # /status [wN|name] [lines]
    if text.lower() == "/status":
        sessions = tmux.scan_claude_sessions()
        statuses = routing._get_session_statuses(sessions)
        resources = tmux._get_session_resources(sessions)
        viewed = tmux._get_locally_viewed_windows() if state._is_local_suppress_enabled() else None
        # Apply auto-local overrides so icons reflect effective state
        if viewed and config._remote_sessions:
            client_activity = tmux._get_client_last_activity()
            expired = [k for k, ts in config._remote_sessions.items()
                       if client_activity > ts]
            for k in expired:
                del config._remote_sessions[k]
            if config._remote_sessions:
                viewed = viewed - set(config._remote_sessions.keys())
        telegram.tg_send(tmux.format_sessions_message(sessions, statuses=statuses,
                                                       locally_viewed=viewed,
                                                       resources=resources),
                         reply_markup=tmux._sessions_keyboard(sessions))
        return None, sessions, last_win_idx

    status_m = re.match(r"^/status\s+w?(\w[\w-]*)(?:\s+(\d+))?$", text.lower())
    if status_m:
        raw_target = status_m.group(1)
        num_lines = int(status_m.group(2)) if status_m.group(2) else 20
        idx = state._resolve_name(raw_target) if raw_target else None
        targets = []
        if idx:
            targets = [(idx, sessions[idx])]
        else:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx
        explicit_lines = status_m.group(2) is not None
        for win_idx, info in targets:
            pane, project = info
            _prof = profiles.get_profile(info.cli) if hasattr(info, 'cli') else None
            pw = tmux._get_pane_width(pane)
            if explicit_lines:
                raw = tmux._capture_pane(pane, num_lines * 3 + 20)
                filtered = content.clean_pane_status(raw, pw, profile=_prof)
                lines = filtered.splitlines()
                status_content = "\n".join(lines[-num_lines:]) if lines else ""
            else:
                # Use clean_pane_status (keep_status=True) to preserve
                # in-progress indicators (spinners, task lists, timing).
                # The old bullet_view approach used stop-mode filtering which
                # stripped these, making status show only completed bullets.
                for n in (30, 80, 200):
                    raw = tmux._capture_pane(pane, n)
                    if content._has_response_start(raw, profile=_prof):
                        break
                status_content = content.clean_pane_status(raw, pw, profile=_prof)
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
            telegram.tg_send("⚠️ No CLI sessions found.")
        return None, sessions, last_win_idx

    # /deepfocus wN|name
    dfocus_m = re.match(r"^/deepfocus\s+w?(\w[\w-]*)$", text.lower())
    if dfocus_m:
        raw_target = dfocus_m.group(1)
        idx = state._resolve_name(raw_target)
        if idx:
            pane, project = sessions[idx]
            state._save_deepfocus_state(idx, pane, project)
            state._clear_focus_state()
            state._clear_smartfocus_state()
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
            telegram.tg_send("⚠️ No CLI sessions found.")
        return None, sessions, last_win_idx

    # /focus wN|name
    focus_m = re.match(r"^/focus\s+w?(\w[\w-]*)$", text.lower())
    if focus_m:
        raw_target = focus_m.group(1)
        idx = state._resolve_name(raw_target)
        if idx:
            pane, project = sessions[idx]
            state._save_focus_state(idx, pane, project)
            state._clear_deepfocus_state()
            state._clear_smartfocus_state()
            pw = tmux._get_pane_width(pane)
            fc_content = content.clean_pane_status(tmux._capture_pane(pane, 20), pw) or "(empty)"
            telegram.tg_send(f"🔍 Focusing on {state._wid_label(idx)} (`{project}`). Send `/unfocus` to stop.\n\n```\n{fc_content[-3000:]}\n```")
            return None, sessions, idx
        else:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # /clear [wN|name]
    clear_m = re.match(r"^/clear(?:\s+w?(\w[\w-]*))?$", text.lower())
    if clear_m:
        raw_target = clear_m.group(1)
        if raw_target:
            idx = state._resolve_name(raw_target)
            if idx:
                state._clear_window_state(idx)
                telegram.tg_send(f"🧹 Cleared transient state for {state._wid_label(idx)}.")
                return None, sessions, last_win_idx
            else:
                telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                                 reply_markup=tmux._sessions_keyboard(sessions))
                return None, sessions, last_win_idx
        else:
            state._clear_all_transient_state()
            telegram.tg_send("🧹 Cleared all transient state.")
            return None, sessions, last_win_idx

    # /unfocus
    if text.lower() == "/unfocus":
        state._clear_focus_state()
        state._clear_deepfocus_state()
        state._clear_smartfocus_state()
        telegram.tg_send("🔍 Focus stopped.", reply_markup=telegram._build_reply_keyboard())
        return None, sessions, last_win_idx

    # /autofocus [on|off|wN]
    af_m = re.match(r"^/autofocus(?:\s+(.+))?$", text.lower())
    if af_m:
        arg = af_m.group(1).strip() if af_m.group(1) else None
        if arg == "off":
            state._set_autofocus(False)
            state._clear_smartfocus_state()
            telegram.tg_send("👁 Autofocus *off*.")
        elif arg == "on":
            state._set_autofocus(True)
            # Auto-attach to a busy session if one exists
            busy_target = None
            if last_win_idx and last_win_idx in sessions and state._is_busy(last_win_idx):
                busy_target = last_win_idx
            else:
                for wid in sessions:
                    if state._is_busy(wid):
                        busy_target = wid
                        break
            if busy_target:
                pane, project = sessions[busy_target]
                state._save_smartfocus_state(busy_target, pane, project)
                telegram.tg_send(f"👁 Autofocus *on* — watching {state._wid_label(busy_target)} (`{project}`).")
            else:
                telegram.tg_send("👁 Autofocus *on*.")
        elif arg:
            # /autofocus wN — attach to specific session
            target_m = re.match(r"^w?(\w[\w-]*)$", arg)
            if target_m:
                raw_target = target_m.group(1)
                idx = state._resolve_name(raw_target)
                if idx:
                    state._set_autofocus(True)
                    pane, project = sessions[idx]
                    state._save_smartfocus_state(idx, pane, project)
                    telegram.tg_send(f"👁 Autofocus *on* — watching {state._wid_label(idx)} (`{project}`).")
                    return None, sessions, idx
                else:
                    telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                                     reply_markup=tmux._sessions_keyboard(sessions))
            else:
                telegram.tg_send(f"⚠️ Unknown `/autofocus` argument: `{arg}`")
        else:
            # Bare /autofocus — show busy sessions to pick from
            sessions = tmux.scan_claude_sessions()
            busy = {wid: sessions[wid] for wid in sessions if state._is_busy(wid)}
            if busy:
                kb = tmux._command_sessions_keyboard("autofocus", busy)
                telegram.tg_send("👁 Watch which session?", reply_markup=kb)
            else:
                currently_on = state._is_autofocus_enabled()
                status = "*on*" if currently_on else "*off*"
                sf = state._load_smartfocus_state()
                if sf:
                    telegram.tg_send(f"👁 Autofocus is {status}, watching {state._wid_label(sf['wid'])} (`{sf['project']}`).")
                else:
                    telegram.tg_send(f"👁 Autofocus is {status}. No busy sessions.")
        return None, sessions, last_win_idx

    # /local [on|off]
    local_m = re.match(r"^/local(?:\s+(on|off))?$", text.lower())
    if local_m:
        arg = local_m.group(1)
        if arg == "on":
            state._set_local_suppress(True)
            telegram.tg_send("📍 Local suppression *on* — Telegram muted for locally viewed sessions.")
        elif arg == "off":
            state._set_local_suppress(False)
            # Auto-attach smartfocus to a busy session if autofocus is on
            if state._is_autofocus_enabled() and not state._load_smartfocus_state() \
               and not state._load_focus_state() and not state._load_deepfocus_state():
                busy_target = None
                if last_win_idx and last_win_idx in sessions and state._is_busy(last_win_idx):
                    busy_target = last_win_idx
                else:
                    for wid in sessions:
                        if state._is_busy(wid):
                            busy_target = wid
                            break
                if busy_target:
                    pane, project = sessions[busy_target]
                    state._save_smartfocus_state(busy_target, pane, project)
                    telegram.tg_send(f"📍 Local suppression *off* — watching {state._wid_label(busy_target)} (`{project}`).")
                    return None, sessions, last_win_idx
            telegram.tg_send("📍 Local suppression *off* — always notify via Telegram.")
        else:
            enabled = state._is_local_suppress_enabled()
            viewed = tmux._get_locally_viewed_windows()
            status = "*on*" if enabled else "*off*"
            lines = [f"📍 Local view suppression is {status}."]
            if viewed:
                labels = ", ".join(f"`w{v}`" for v in sorted(viewed, key=int))
                lines.append(f"Currently viewed: {labels}")
            else:
                lines.append("No tmux client attached or viewing a session window.")
            lines.append("\n`/local on` | `/local off`")
            telegram.tg_send("\n".join(lines))
        return None, sessions, last_win_idx

    # /notification [digits|all|off]
    noti_m = re.match(r"^/notification(?:\s+(.+))?$", text, re.IGNORECASE)
    if noti_m:
        arg = noti_m.group(1).strip() if noti_m.group(1) else None
        loud = state._load_notification_config()
        if arg is None:
            # Show current config
            lines = ["🔔 *Notification categories:*\n"]
            for num, (label, emoji) in sorted(state._NOTIFICATION_CATEGORIES.items()):
                marker = "🔊" if num in loud else "🔇"
                lines.append(f"  {num}. {emoji} {label} {marker}")
            lines.append(f"\nLoud: `{''.join(str(n) for n in sorted(loud))}`" if loud else "\nLoud: _(none)_")
            lines.append("\n`/notification 12` set loud | `all` | `off`")
            telegram.tg_send("\n".join(lines))
        elif arg.lower() == "all":
            loud = set(state._NOTIFICATION_CATEGORIES.keys())
            state._save_notification_config(loud)
            telegram.tg_send(f"🔔 All notifications *loud*.")
        elif arg.lower() == "off":
            state._save_notification_config(set())
            telegram.tg_send(f"🔔 All notifications *silent*.")
        elif re.match(r"^\d+$", arg):
            loud = {int(c) for c in arg if c.isdigit() and int(c) in state._NOTIFICATION_CATEGORIES}
            state._save_notification_config(loud)
            labels = ", ".join(state._NOTIFICATION_CATEGORIES[n][0] for n in sorted(loud) if n in state._NOTIFICATION_CATEGORIES)
            telegram.tg_send(f"🔔 Loud: {labels or '_(none)_'}")
        else:
            telegram.tg_send("⚠️ Usage: `/notification [digits|all|off]`")
        return None, sessions, last_win_idx

    # /log [N]
    log_m = re.match(r"^/log(?:\s+(\d+))?$", text.lower())
    if log_m:
        n = int(log_m.group(1)) if log_m.group(1) else 30
        n = min(n, 100)
        try:
            result = subprocess.run(
                ["journalctl", "--user", "-u", "astra", "-n", str(n), "--no-pager"],
                capture_output=True, text=True, timeout=10,
            )
            output = result.stdout.strip()
            if output:
                telegram.tg_send(f"📋 Last {n} log lines:\n```\n{output[-3500:]}\n```")
            else:
                telegram.tg_send("⚠️ No journal entries found for astra.")
        except Exception:
            telegram.tg_send("⚠️ Failed to read journalctl.")
        return None, sessions, last_win_idx

    # /god [w4|all|off|off w4]
    god_m = re.match(r"^/god(?:\s+(.+))?$", text, re.IGNORECASE)
    if god_m:
        arg = god_m.group(1).strip() if god_m.group(1) else None
        sessions = tmux.scan_claude_sessions()
        if arg is None:
            # Bare /god — show status
            wids = state._god_mode_wids()
            if not wids:
                status_msg = "\u26a1 God mode is *off*."
            elif "all" in wids:
                status_msg = "\u26a1 God mode is *on* for all sessions."
            else:
                def _sort_wid(x):
                    m = re.match(r'^w?(\d+)', x)
                    return int(m.group(1)) if m else 0
                labels = ", ".join(state._wid_label(w) for w in sorted(wids, key=_sort_wid))
                status_msg = f"\u26a1 God mode is *on* for {labels}."
            if state._is_god_quiet():
                status_msg += " (quiet)"
            kb = tmux._command_sessions_keyboard("god", sessions)
            telegram.tg_send(status_msg, reply_markup=kb)
            return None, sessions, last_win_idx

        # /god off [wN]
        off_m = re.match(r"^off(?:\s+w?(\w[\w-]*))?$", arg, re.IGNORECASE)
        if off_m:
            off_target = off_m.group(1)
            if off_target:
                idx = state._resolve_name(off_target) or off_target
                state._set_god_mode(idx, False)
                telegram.tg_send(f"\u26a1 God mode *off* for {state._wid_label(idx)}.")
            else:
                state._clear_god_mode()
                telegram.tg_send("\u26a1 God mode *off*.")
            return None, sessions, last_win_idx

        # /god quiet|loud
        if arg.lower() in ("quiet", "q"):
            state._set_god_quiet(True)
            telegram.tg_send("\u26a1 God mode receipts *suppressed*.")
            return None, sessions, last_win_idx
        if arg.lower() in ("loud", "l"):
            state._set_god_quiet(False)
            telegram.tg_send("\u26a1 God mode receipts *enabled*.")
            return None, sessions, last_win_idx

        # /god all
        if arg.lower() == "all":
            state._set_god_mode("all", True)
            telegram.tg_send("\u26a1 God mode *on* for all sessions.")
            _god_accept_pending_prompts(list(sessions.keys()))
            # Cycle accept-edits for all idle sessions
            for idx, (p, proj) in sessions.items():
                idle, _ = routing._pane_idle_state(p)
                if idle:
                    _enable_accept_edits(p)
            return None, sessions, last_win_idx

        # /god wN|name
        target_m = re.match(r"^w?(\w[\w-]*)$", arg)
        if target_m:
            raw_target = target_m.group(1)
            idx = state._resolve_name(raw_target)
            if idx:
                state._set_god_mode(idx, True)
                telegram.tg_send(f"\u26a1 God mode *on* for {state._wid_label(idx)}.")
                _god_accept_pending_prompts([idx])
                pane_t, _ = sessions[idx]
                idle, _ = routing._pane_idle_state(pane_t)
                if idle:
                    _enable_accept_edits(pane_t)
                return None, sessions, idx
            else:
                telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                                 reply_markup=tmux._sessions_keyboard(sessions))
                return None, sessions, last_win_idx

        telegram.tg_send(f"⚠️ Unknown `/god` argument: `{arg}`")
        return None, sessions, last_win_idx

    # /name wN|name [label]
    name_m = re.match(r"^/name\s+w?(\w[\w-]*)(?:\s+(.+))?$", text)
    if name_m:
        raw_target = name_m.group(1)
        idx = state._resolve_name(raw_target) or raw_target
        label = name_m.group(2).strip() if name_m.group(2) else None
        if label:
            state._save_session_name(idx, label)
            telegram.tg_send(f"✏️ Session {state._wid_label(idx)} named `{label}`.")
        else:
            state._clear_session_name(idx)
            telegram.tg_send(f"✏️ Session {state._wid_label(idx)} name cleared.")
        return None, sessions, last_win_idx

    # /new [claude|gemini] [dir]
    new_m = re.match(r"^/new(?:\s+(.+))?$", text)
    if new_m:
        args = new_m.group(1).strip().split(None, 1) if new_m.group(1) else []
        cli_name = "claude"
        dir_arg = None
        if args and profiles.get_profile(args[0].lower()):
            cli_name = args[0].lower()
            dir_arg = args[1].strip() if len(args) > 1 else None
        elif args:
            dir_arg = new_m.group(1).strip()
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
            # Wait for CLI to start — Gemini (Node.js) takes a few seconds
            new_wid = None
            pane_target = None
            for _ in range(6):
                sessions = tmux.scan_claude_sessions()
                new_wid = tmux.resolve_session_id(f"w{new_idx}", sessions)
                if new_wid:
                    info = sessions[new_wid]
                    pane_target = info.pane_target if hasattr(info, "pane_target") else info[0]
                    break
                time.sleep(1)
            if not new_wid:
                new_wid = f"w{new_idx}a"
            # Auto-setup: accept trust dialog, switch out of plan mode
            if pane_target:
                time.sleep(2)
                _auto_setup_session(pane_target)
            proj = work_dir.rstrip("/").rsplit("/", 1)[-1]
            label = state._wid_label(new_wid)
            telegram.tg_send(f"🚀 Started {profile.display_name} in {label} (`{proj}`):\n`{work_dir}`")
            return None, sessions, new_wid
        except Exception as e:
            telegram.tg_send(f"⚠️ Failed to start session: `{e}`")
            return None, sessions, last_win_idx

    # /interrupt [wN|name]
    int_m = re.match(r"^/interrupt(?:\s+w?(\w[\w-]*))?$", text.lower())
    if int_m:
        raw_target = int_m.group(1)
        idx = state._resolve_name(raw_target) if raw_target else None
        if idx:
            _interrupt_session(idx, sessions)
            return None, sessions, idx
        elif raw_target:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
        elif len(sessions) == 1:
            idx = next(iter(sessions))
            _interrupt_session(idx, sessions)
            return None, sessions, idx
        else:
            sessions = tmux.scan_claude_sessions()
            kb = tmux._command_sessions_keyboard("interrupt", sessions)
            if kb:
                telegram.tg_send("⏹ Interrupt which session?", reply_markup=kb)
            else:
                telegram.tg_send("⚠️ No CLI sessions found.")
        return None, sessions, last_win_idx

    # /keys (bare — combo picker, always ask which session for multiple)
    if re.match(r"^/keys?$", text.strip(), re.IGNORECASE):
        sessions = tmux.scan_claude_sessions()
        if len(sessions) == 1:
            target = next(iter(sessions))
            _, project = sessions[target]
            kb = _keys_combo_keyboard(target)
            telegram.tg_send(f"⌨️ Send key to {state._wid_label(target)} (`{project}`):", reply_markup=kb)
            return None, sessions, target
        elif len(sessions) == 0:
            telegram.tg_send("⚠️ No CLI sessions found.")
        else:
            kb = tmux._command_sessions_keyboard("keys", sessions)
            telegram.tg_send("⌨️ Send keys to which session?", reply_markup=kb)
        return None, sessions, last_win_idx

    # /keys wN|name (no key args — combo picker for that session)
    keys_bare_m = re.match(r"^/keys?\s+w?(\w[\w-]*)$", text.strip(), re.IGNORECASE)
    if keys_bare_m:
        raw_target = keys_bare_m.group(1)
        idx = state._resolve_name(raw_target)
        if idx:
            _, project = sessions[idx]
            kb = _keys_combo_keyboard(idx)
            telegram.tg_send(f"⌨️ Send key to {state._wid_label(idx)} (`{project}`):", reply_markup=kb)
            return None, sessions, idx
        else:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # /keys wN|name key1 [key2 ...]
    keys_m = re.match(r"^/keys?\s+w?(\w[\w-]*)\s+(.+)$", text, re.IGNORECASE)
    if keys_m:
        raw_target = keys_m.group(1)
        key_str = keys_m.group(2).strip()
        idx = state._resolve_name(raw_target)
        if idx:
            pane, project = sessions[idx]
            p = shlex.quote(pane)
            config._mark_remote(idx)
            tokens = key_str.split()
            tmux_keys = [_resolve_key(t) for t in tokens]
            keys_arg = " ".join(tmux_keys)
            subprocess.run(["bash", "-c",
                            f"tmux send-keys -t {p} {keys_arg}"], timeout=5)
            telegram.tg_send(f"⌨️ Sent `{key_str}` to {state._wid_label(idx)} (`{project}`).")
            return None, sessions, idx
        else:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # /kill (bare — show session picker)
    if text.lower().strip() == "/kill":
        sessions = tmux.scan_claude_sessions()
        kb = tmux._command_sessions_keyboard("kill", sessions)
        if kb:
            telegram.tg_send("🛑 Kill which session?", reply_markup=kb)
        else:
            telegram.tg_send("⚠️ No CLI sessions found.")
        return None, sessions, last_win_idx

    # /kill wN|name
    kill_m = re.match(r"^/kill\s+w?(\w[\w-]*)$", text.lower())
    if kill_m:
        raw_target = kill_m.group(1)
        idx = state._resolve_name(raw_target)
        if idx:
            pane, project = sessions[idx]
            p = shlex.quote(pane)
            config._mark_remote(idx)
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

    # /restart (bare — show session picker)
    if text.lower().strip() == "/restart":
        sessions = tmux.scan_claude_sessions()
        kb = tmux._command_sessions_keyboard("restart", sessions)
        if kb:
            telegram.tg_send("🔄 Restart which session?", reply_markup=kb)
        else:
            telegram.tg_send("⚠️ No CLI sessions found.")
        return None, sessions, last_win_idx

    # /restart wN|name
    restart_m = re.match(r"^/restart\s+w?(\w[\w-]*)$", text.lower())
    if restart_m:
        raw_target = restart_m.group(1)
        idx = state._resolve_name(raw_target)
        if idx:
            pane, project = sessions[idx]
            config._mark_remote(idx)
            # Remember CLI type before killing (SessionInfo carries it)
            orig_info = sessions[idx]
            if isinstance(orig_info, tmux.SessionInfo):
                restart_profile = profiles.get_profile(orig_info.cli) or profiles.CLAUDE
            else:
                restart_profile = profiles.CLAUDE
            # Save working directory before killing
            cwd = tmux._get_pane_cwd(pane)
            p = shlex.quote(pane)
            # Kill with 3x Ctrl+C
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
                telegram.tg_send(f"⚠️ {state._wid_label(idx)} (`{project}`) still running — restart aborted.")
                return None, sessions, last_win_idx
            # Clear stale state
            state._clear_busy(idx)
            for suffix in (f"_active_prompt_{idx}.json", f"_bash_cmd_{idx}.json"):
                try:
                    os.remove(os.path.join(config.SIGNAL_DIR, suffix))
                except OSError:
                    pass
            # Check if pane still exists (it closes if CLI was the initial command)
            pane_alive = bool(tmux._get_pane_command(pane))
            restart_cmd = restart_profile.restart_cmd
            if pane_alive:
                # Re-source shell config so PATH is fresh, then relaunch
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
                # Pane closed — create a new window (same as /new)
                work_dir = cwd or os.path.expanduser("~")
                subprocess.run(
                    ["tmux", "new-window", "-d", "-P", "-F", "#{window_index}",
                     f"bash -c 'cd {shlex.quote(work_dir)} && {restart_cmd}'"],
                    capture_output=True, text=True, timeout=10,
                )
            # Wait for CLI to start (Gemini/Node takes a few seconds)
            new_wid = None
            for _ in range(6):
                time.sleep(1)
                sessions = tmux.scan_claude_sessions()
                if pane_alive:
                    new_wid = tmux.resolve_session_id(idx, sessions)
                else:
                    # New window — find the new session by matching cwd
                    for sid, info in sessions.items():
                        if sid not in {idx} and isinstance(info, tmux.SessionInfo):
                            if info.cli == restart_profile.name:
                                new_wid = sid
                                break
                    if not new_wid:
                        new_wid = tmux.resolve_session_id(idx, sessions)
                if new_wid:
                    break
            if new_wid and new_wid in sessions:
                _, new_project = sessions[new_wid]
                telegram.tg_send(f"🔄 Restarted {state._wid_label(new_wid)} (`{new_project}`).")
                return None, sessions, new_wid
            else:
                telegram.tg_send(f"⚠️ {state._wid_label(idx)} did not restart — pane may have closed.")
                return None, sessions, last_win_idx
        else:
            telegram.tg_send(f"⚠️ No session `{raw_target}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # /last [wN|name]
    last_m = re.match(r"^/last(?:\s+w?(\w[\w-]*))?$", text.lower())
    if last_m:
        raw_target = last_m.group(1)
        idx = state._resolve_name(raw_target) if raw_target else None
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

    # /saved [wN|name]
    saved_m = re.match(r"^/saved(?:\s+w?(\w[\w-]*))?$", text.lower())
    if saved_m:
        raw_target = saved_m.group(1)
        if raw_target:
            idx = state._resolve_name(raw_target)
            if not idx:
                telegram.tg_send(f"⚠️ No session `{raw_target}`.")
                return None, sessions, last_win_idx
            queued = state._load_queued_msgs(idx)
            if queued:
                preview_lines = []
                for i, m_q in enumerate(queued, 1):
                    preview_lines.append(f"{i}. `{m_q['text'][:100]}`")
                saved_kb = telegram._build_inline_keyboard([[
                    ("\u2709\ufe0f Send", f"saved_send_{idx}"),
                    ("\U0001f5d1 Discard", f"saved_discard_{idx}"),
                ]])
                telegram.tg_send(
                    f"💾 {len(queued)} saved message(s) for {state._wid_label(idx)}:\n" + "\n".join(preview_lines),
                    reply_markup=saved_kb,
                )
            else:
                telegram.tg_send(f"No saved messages for {state._wid_label(idx)}.")
        else:
            # Scan all sessions for queued messages
            found_any = False
            for idx in tmux._sort_session_keys(sessions):
                queued = state._load_queued_msgs(idx)
                if queued:
                    found_any = True
                    preview_lines = []
                    for i, m_q in enumerate(queued, 1):
                        preview_lines.append(f"{i}. `{m_q['text'][:100]}`")
                    saved_kb = telegram._build_inline_keyboard([[
                        ("\u2709\ufe0f Send", f"saved_send_{idx}"),
                        ("\U0001f5d1 Discard", f"saved_discard_{idx}"),
                    ]])
                    telegram.tg_send(
                        f"💾 {len(queued)} saved message(s) for {state._wid_label(idx)}:\n" + "\n".join(preview_lines),
                        reply_markup=saved_kb,
                    )
            if not found_any:
                telegram.tg_send("No saved messages.")
        return None, sessions, last_win_idx

    # Parse wN prefix
    m = re.match(r"^w(\d+[a-z]?)\s+(.*)", text, re.DOTALL)
    if m:
        wid = f"w{m.group(1)}"
        prompt = m.group(2).strip()
        resolved = tmux.resolve_session_id(wid, sessions)
        if resolved:
            pane, project = sessions[resolved]
            _clear_suggestion_keyboard(resolved)
            confirm = routing.route_to_pane(pane, resolved, prompt)
            telegram.tg_send(confirm, silent=state._is_silent(_CAT_CONFIRM))
            config._log(resolved, confirm[:100])
            _maybe_activate_smartfocus(resolved, pane, project, confirm)
            return None, sessions, resolved
        else:
            telegram.tg_send(f"⚠️ No session at `{wid}`.\n{tmux.format_sessions_message(sessions)}",
                             reply_markup=tmux._sessions_keyboard(sessions),
                             silent=state._is_silent(_CAT_ERROR))
            return None, sessions, last_win_idx

    # Name prefix: first word matches a known session name
    words = text.split(None, 1)
    if len(words) == 2:
        name_idx = state._resolve_name(words[0])
        if name_idx is not None:
            pane, project = sessions[name_idx]
            _clear_suggestion_keyboard(name_idx)
            confirm = routing.route_to_pane(pane, name_idx, words[1].strip())
            telegram.tg_send(confirm, silent=state._is_silent(_CAT_CONFIRM))
            config._log(name_idx, confirm[:100])
            _maybe_activate_smartfocus(name_idx, pane, project, confirm)
            return None, sessions, name_idx

    # No prefix — route to last used or only session
    target_idx = None
    if len(sessions) == 1:
        target_idx = next(iter(sessions))
    elif last_win_idx and last_win_idx in sessions:
        target_idx = last_win_idx

    if target_idx:
        pane, project = sessions[target_idx]
        _clear_suggestion_keyboard(target_idx)
        confirm = routing.route_to_pane(pane, target_idx, text)
        telegram.tg_send(confirm, silent=state._is_silent(_CAT_CONFIRM))
        config._log(target_idx, confirm[:100])
        _maybe_activate_smartfocus(target_idx, pane, project, confirm)
        return None, sessions, target_idx
    elif len(sessions) == 0:
        telegram.tg_send("⚠️ No CLI sessions found. Send `/sessions` to rescan.",
                         silent=state._is_silent(_CAT_ERROR))
    else:
        telegram.tg_send(f"⚠️ Multiple sessions — prefix with `wN`.\n{tmux.format_sessions_message(sessions)}",
                         reply_markup=tmux._sessions_keyboard(sessions),
                         silent=state._is_silent(_CAT_ERROR))

    return None, sessions, last_win_idx


def _handle_callback(callback: dict, sessions: dict,
                     last_win_idx: str | None) -> tuple[dict, str | None, str | None]:
    """Handle an inline keyboard callback. Returns (sessions, last_win_idx, action)."""
    state._current_sessions = sessions
    cb_id = callback["id"]
    cb_data = callback.get("data", "")
    msg_id = callback.get("message_id", 0)

    telegram._answer_callback_query(cb_id)
    if msg_id:
        telegram._remove_inline_keyboard(msg_id)

    # Clear keyboard tracker for any wid-based callback
    wid_m = re.search(r"(w\d+[a-z]?)", cb_data)
    if wid_m:
        config._clear_keyboard_msg(wid_m.group(1))

    if cb_data == "quit_y":
        telegram.tg_send("👋 Bye.")
        return sessions, last_win_idx, "quit"
    if cb_data == "quit_n":
        telegram.tg_send("Cancelled.")
        return sessions, last_win_idx, None

    # Permission callback: perm_{wid}_{n}
    m = re.match(r"^perm_(w\d+[a-z]?)_(\d+)$", cb_data)
    if m:
        wid, n = m.group(1), int(m.group(2))
        config._mark_remote(wid)
        prompt = state.load_active_prompt(wid)
        if prompt:
            total = prompt.get("total", 3)
            routing._select_option(prompt.get("pane", ""), n)
            # Custom labels from prompt (e.g. startup dialog options)
            prompt_labels = prompt.get("labels", {})
            if str(n) in prompt_labels:
                label = f"✅ {prompt_labels[str(n)]}"
            elif n == 1:
                label = "\u2705 Allowed"
            elif n == 2:
                label = "\u2705 Always allowed"
            elif n == total:
                label = "\u274c Denied"
            else:
                label = f"Selected option {n}"
            telegram.tg_send(f"{label} in {state._wid_label(wid)}",
                             silent=state._is_silent(_CAT_CONFIRM))
            config._log("callback", f"perm {wid} option {n}")
        else:
            telegram._answer_callback_query(cb_id, "Prompt expired")
        return sessions, last_win_idx, None

    # Question callback: q_{wid}_{n}
    m = re.match(r"^q_(w\d+[a-z]?)_(\d+)$", cb_data)
    if m:
        wid, n_str = m.group(1), m.group(2)
        config._mark_remote(wid)
        resolved = tmux.resolve_session_id(wid, sessions)
        if resolved:
            pane, project = sessions[resolved]
            confirm = routing.route_to_pane(pane, resolved, n_str)
            telegram.tg_send(confirm, silent=state._is_silent(_CAT_CONFIRM))
            last_win_idx = resolved
        return sessions, last_win_idx, None

    # Quick-pick keys callback: keys_{wid}_{short}
    m = re.match(r"^keys_(w\d+[a-z]?)_(\w+)$", cb_data)
    if m:
        wid, short = m.group(1), m.group(2)
        # Look up short id in _QUICK_KEYS
        tmux_key = None
        label = short
        for qlabel, qshort, qkey in _QUICK_KEYS:
            if qshort == short:
                tmux_key = qkey
                label = qlabel
                break
        if tmux_key:
            resolved = tmux.resolve_session_id(wid, sessions)
            if resolved:
                config._mark_remote(resolved)
                pane, project = sessions[resolved]
                p = shlex.quote(pane)
                subprocess.run(["bash", "-c",
                                f"tmux send-keys -t {p} {tmux_key}"], timeout=5)
                telegram.tg_send(f"⌨️ Sent `{label}` to {state._wid_label(resolved)} (`{project}`).")
                last_win_idx = resolved
            else:
                telegram.tg_send(f"⚠️ Session `{wid}` no longer active.")
        return sessions, last_win_idx, None

    # Command callbacks: cmd_{action}_{wid}
    m = re.match(r"^cmd_(status|focus|deepfocus|autofocus|interrupt|kill|restart|last|god|keys)_(w?\d+[a-z]?)$", cb_data)
    if m:
        cmd, wid_part = m.group(1), m.group(2)
        wid_str = wid_part if wid_part.startswith("w") else f"w{wid_part}"
        cmd_text = f"/{cmd} {wid_str}"
        _, sessions, last_win_idx = _handle_command(
            cmd_text, sessions, last_win_idx)
        return sessions, last_win_idx, None

    # Session select: sess_{wid}
    m = re.match(r"^sess_(w?\d+[a-z]?)$", cb_data)
    if m:
        wid = m.group(1)
        if not wid.startswith("w"):
            wid = f"w{wid}"
        last_win_idx = wid
        cmd_text = f"/status {wid}"
        _, sessions, last_win_idx = _handle_command(
            cmd_text, sessions, last_win_idx)
        return sessions, last_win_idx, None

    # Saved message callbacks: saved_send_{wid}, saved_discard_{wid}
    m = re.match(r"^saved_(send|discard)_(w\d+[a-z]?)$", cb_data)
    if m:
        action_type, wid = m.group(1), m.group(2)
        if action_type == "send":
            msgs = state._pop_queued_msgs(wid)
            resolved = tmux.resolve_session_id(wid, sessions)
            if msgs and resolved:
                combined = "\n".join(m_q["text"] for m_q in msgs)
                pane, project = sessions[resolved]
                confirm = routing.route_to_pane(pane, resolved, combined)
                telegram.tg_send(confirm, silent=state._is_silent(_CAT_CONFIRM))
                _maybe_activate_smartfocus(resolved, pane, project, confirm)
                last_win_idx = resolved
            elif msgs:
                telegram.tg_send(f"⚠️ Session `{wid}` no longer active.",
                                 silent=state._is_silent(_CAT_ERROR))
            else:
                telegram.tg_send("No saved messages to send.")
        else:  # discard
            state._pop_queued_msgs(wid)
            telegram.tg_send(f"🗑 Discarded saved messages for {state._wid_label(wid)}.",
                             silent=state._is_silent(_CAT_CONFIRM))
        return sessions, last_win_idx, None

    # Suggestion callback: suggest_{wid}
    m = re.match(r"^suggest_(w\d+[a-z]?)$", cb_data)
    if m:
        wid = m.group(1)
        suggestion = config._pop_suggestion(wid)
        if suggestion:
            config._mark_remote(wid)
            resolved = tmux.resolve_session_id(wid, sessions)
            if resolved:
                pane, project = sessions[resolved]
                confirm = routing.route_to_pane(pane, resolved, suggestion)
                telegram.tg_send(confirm, silent=state._is_silent(_CAT_CONFIRM))
                _maybe_activate_smartfocus(resolved, pane, project, confirm)
                last_win_idx = resolved
            else:
                telegram.tg_send(f"⚠️ Session `{wid}` no longer active.",
                                 silent=state._is_silent(_CAT_ERROR))
        else:
            telegram.tg_send("⚠️ Suggestion expired.")
        return sessions, last_win_idx, None

    # Render-as-image callback
    if cb_data == "render":
        body = config._render_bodies.get(msg_id)
        if body:
            _render_and_send_image(body)
        else:
            telegram.tg_send("⚠️ Render data expired — resend the message and try again.")
        return sessions, last_win_idx, None

    config._log("callback", f"unknown callback_data: {cb_data}")
    return sessions, last_win_idx, None


def _render_and_send_image(body: str):
    """Render text body as a PNG image and send via Telegram."""
    import tempfile
    tmp_in = tempfile.mktemp(suffix=".txt")
    tmp_out = tempfile.mktemp(suffix=".png")
    try:
        with open(tmp_in, "w") as f:
            f.write(body)
        _pixi = os.path.expanduser("~/.pixi/bin/pixi")
        result = subprocess.run(
            [_pixi, "run", "--manifest-path",
             os.path.expanduser("~/pixi_tools/imgcat/pixi.toml"),
             "python", os.path.expanduser("~/pixi_tools/imgcat/render.py"),
             tmp_in, tmp_out],
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode != 0:
            config._log("render", f"render.py failed: {result.stderr[:200]}")
            telegram.tg_send(f"⚠️ Render failed: `{result.stderr[:200]}`")
            return
        telegram.tg_send_photo(tmp_out, caption="")
    except Exception as e:
        config._log("render", f"render error: {e}")
        telegram.tg_send(f"⚠️ Render failed: `{e}`")
    finally:
        for p in (tmp_in, tmp_out):
            try:
                os.remove(p)
            except OSError:
                pass
