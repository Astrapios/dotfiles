"""Signal processing and question formatting."""
import difflib
import json
import os
import re
import time

from astra import config, telegram, tmux, content, state, routing

# Notification category constants (see state._NOTIFICATION_CATEGORIES)
_CAT_PERMISSION = 1
_CAT_STOP = 2
_CAT_QUESTION = 3
_CAT_CONFIRM = 7


def _display_name_for(cli: str = "") -> str:
    """Get display name for a CLI type."""
    from astra import profiles
    if cli:
        p = profiles.get_profile(cli)
        if p:
            return p.display_name
    return "Claude Code"


def _format_question_msg(tag: str, project: str, question: dict, cli: str = "") -> str:
    """Format a single AskUserQuestion question for Telegram."""
    dn = _display_name_for(cli)
    parts = [f"❓{tag} {dn} (`{project}`) asks:\n"]
    parts.append(question.get("question", "?"))
    opts = question.get("options", [])
    for i, opt in enumerate(opts, 1):
        label = opt.get("label", "?")
        desc = opt.get("description", "")
        if desc:
            parts.append(f"  {i}. {label} — {desc}")
        else:
            parts.append(f"  {i}. {label}")
    n = len(opts)
    parts.append(f"  {n+1}. Type your answer")
    parts.append(f"  {n+2}. Chat about this")
    return "\n".join(parts)


def process_signals(focused_wids: set[str] | None = None,
                     smartfocus_prev: list[str] | None = None,
                     smartfocus_has_sent: bool = False,
                     locally_viewed: set[str] | None = None,
                     sessions: dict | None = None) -> str | None:
    """Process pending signal files. Returns last window index (e.g. '4') or None.
    If focused_wids is set, stop signals for those windows are suppressed.
    smartfocus_prev: previous lines from smartfocus monitoring, used to send
    only the tail (new content) when a smartfocus session stops.
    smartfocus_has_sent: whether any 👁 update was sent during this smartfocus session.
    locally_viewed: set of window indices currently viewed in tmux — suppresses Telegram sends."""
    if not os.path.isdir(config.SIGNAL_DIR):
        return None

    try:
        files = sorted(os.listdir(config.SIGNAL_DIR))
    except OSError:
        return None

    last_wid = None
    for fname in files:
        if not fname.endswith(".json") or fname.startswith("_"):
            continue
        fpath = os.path.join(config.SIGNAL_DIR, fname)
        try:
            with open(fpath) as f:
                signal = json.load(f)
        except (json.JSONDecodeError, OSError):
            try:
                os.remove(fpath)
            except OSError:
                pass
            continue

        event = signal.get("event", "")
        pane = signal.get("pane", "")
        wid = signal.get("wid", "")
        project = signal.get("project", "unknown")
        cli = signal.get("cli", "claude")

        # Resolve bare wid (e.g. "w4") to actual session key (e.g. "w4a")
        if sessions and wid and wid not in sessions:
            # Match by pane target (most accurate)
            for sid, info in sessions.items():
                if isinstance(info, tmux.SessionInfo) and info.pane_target == pane:
                    wid = sid
                    break
            else:
                # Fallback: resolve_session_id handles wN → wNa alias
                resolved = tmux.resolve_session_id(wid, sessions)
                if resolved:
                    wid = resolved

        dn = _display_name_for(cli)
        tag = f" {state._wid_label(wid)}" if wid else ""
        # locally_viewed contains bare window indices; extract from wid
        win_idx = re.match(r'^w?(\d+)', wid).group(1) if wid else ""
        is_local = bool(locally_viewed and win_idx in locally_viewed)

        if pane:
            project = tmux.get_pane_project(pane) or project

        # Remove stale inline keyboard from previous message for this session
        if wid and not is_local:
            old_kb = config._clear_keyboard_msg(wid)
            if old_kb:
                telegram._remove_inline_keyboard(old_kb)

        if event == "stop":
            state._clear_busy(wid)
            sf = state._load_smartfocus_state()
            was_smartfocus = sf and sf["wid"] == wid
            if was_smartfocus:
                state._clear_smartfocus_state()

            stop_kb = telegram._build_inline_keyboard([[
                ("\U0001f4cb Status", f"cmd_status_{wid}"),
                ("\U0001f50d Focus", f"cmd_focus_{wid}"),
            ]])

            if focused_wids and wid in focused_wids:
                pass
            elif is_local:
                pass  # Locally viewed — skip Telegram notification
            elif was_smartfocus and pane:
                # Smartfocus session ended — send tail or full content
                time.sleep(1)
                pw = tmux._get_pane_width(pane)
                for num_lines in (30, 80, 200):
                    raw = tmux._capture_pane(pane, num_lines)
                    if content._has_response_start(raw):
                        break
                cleaned = content.clean_pane_content(raw, "stop", pw) if raw else ""
                # Guard: if no ❯ boundary in capture and pane is busy,
                # the capture contains next-task content — discard it
                if cleaned and raw:
                    has_boundary = any(l.strip().startswith("❯") for l in raw.splitlines())
                    if not has_boundary:
                        idle, _ = routing._pane_idle_state(pane)
                        if not idle:
                            cleaned = ""
                if cleaned and smartfocus_prev:
                    cur_lines = cleaned.splitlines()
                    new = content._compute_new_lines(smartfocus_prev, cur_lines)
                    _stop_silent = state._is_silent(_CAT_STOP)
                    if new:
                        # New lines since last 👁 update — send tail
                        tail_text = "\n".join(new).strip()
                        if tail_text:
                            header = f"✅{tag} (`{project}`) finished:\n\n"
                            telegram._send_long_message(header, tail_text, wid, reply_markup=stop_kb, silent=_stop_silent)
                        else:
                            telegram.tg_send(f"✅{tag} (`{project}`) finished.", reply_markup=stop_kb, silent=_stop_silent)
                    elif smartfocus_has_sent:
                        # No new lines — check if 👁 delivered the real content
                        # or just noise (e.g. instruction echoes, tool progress)
                        sm = difflib.SequenceMatcher(None, smartfocus_prev, cur_lines)
                        if sm.ratio() < 0.3:
                            # Content is very different from what smartfocus sent — send full response
                            header = f"✅{tag} (`{project}`) finished:\n\n"
                            telegram._send_long_message(header, cleaned, wid, reply_markup=stop_kb, silent=_stop_silent)
                        else:
                            telegram.tg_send(f"✅{tag} (`{project}`) finished.", reply_markup=stop_kb, silent=_stop_silent)
                    else:
                        # No new lines AND never sent any 👁 — send full response
                        header = f"✅{tag} (`{project}`) finished:\n\n"
                        telegram._send_long_message(header, cleaned, wid, reply_markup=stop_kb, silent=_stop_silent)
                elif cleaned:
                    # No prev_lines (very fast response) — send full content
                    header = f"✅{tag} (`{project}`) finished:\n\n"
                    telegram._send_long_message(header, cleaned, wid, reply_markup=stop_kb, silent=state._is_silent(_CAT_STOP))
                else:
                    telegram.tg_send(f"✅{tag} (`{project}`) finished.", reply_markup=stop_kb, silent=state._is_silent(_CAT_STOP))
            else:
                raw = ""
                if pane:
                    time.sleep(4)
                    pw = tmux._get_pane_width(pane)
                    for num_lines in (30, 80, 200):
                        raw = tmux._capture_pane(pane, num_lines)
                        if content._has_response_start(raw):
                            break
                else:
                    pw = 0
                cleaned = content.clean_pane_content(raw, "stop", pw) if raw else "(could not capture pane)"
                header = f"✅{tag} {dn} (`{project}`) finished:\n\n"
                telegram._send_long_message(header, cleaned, wid, reply_markup=stop_kb, silent=state._is_silent(_CAT_STOP))

            # Check for queued messages (always, regardless of focus)
            if not is_local:
                queued = state._load_queued_msgs(wid)
                if queued:
                    preview_lines = []
                    for i, m in enumerate(queued, 1):
                        preview_lines.append(f"{i}. `{m['text'][:100]}`")
                    preview = "\n".join(preview_lines)
                    saved_kb = telegram._build_inline_keyboard([[
                        ("\u2709\ufe0f Send", f"saved_send_{wid}"),
                        ("\U0001f5d1 Discard", f"saved_discard_{wid}"),
                    ]])
                    telegram.tg_send(
                        f"💾 {len(queued)} saved message(s) for {state._wid_label(wid)}:\n{preview}",
                        reply_markup=saved_kb,
                        silent=state._is_silent(_CAT_CONFIRM),
                    )

            # God mode: ensure accept-edits is on when session becomes idle
            if pane and wid and state._is_god_mode_for(wid):
                from astra import commands  # deferred to avoid circular
                commands._enable_accept_edits(pane)

        elif event == "permission":
            bash_cmd = signal.get("cmd", "")

            # God mode: auto-accept and send compact receipt (skip plan approvals)
            is_plan_perm = "plan" in signal.get("message", "").lower()
            if wid and state._is_god_mode_for(wid) and not is_plan_perm:
                routing._select_option(pane, 1)  # Accept IMMEDIATELY — always runs
                desc = bash_cmd[:200] if bash_cmd else (signal.get("message", "") or "permission")
                config._log("god", f"Auto-allowed {wid} ({project}): {desc}")
                if not is_local:
                    telegram.tg_send(f"\u26a1{tag} Auto-allowed (`{project}`): `{desc}`",
                                     silent=state._is_silent(_CAT_CONFIRM))
            else:
                perm_header, perm_body, options, perm_context = content._extract_pane_permission(pane)
                if options and not any(o.startswith("1.") for o in options):
                    options.insert(0, "1. Yes")
                max_opt = 0
                for o in options:
                    m_opt = re.match(r'(\d+)', o)
                    if m_opt:
                        max_opt = max(max_opt, int(m_opt.group(1)))
                opts_text = "\n".join(options)
                n = max_opt or 3

                # Detect free-text option (e.g. "4. Type here to tell Claude...")
                free_text_at = None
                for o in options:
                    if re.search(r'\btype\b.*\b(here|something|your)\b', o, re.IGNORECASE):
                        m_ft = re.match(r'(\d+)', o)
                        if m_ft:
                            free_text_at = int(m_ft.group(1)) - 1
                        break
                free_text_hint = "\n\n_Or type a message to give feedback._" if free_text_at is not None else ""

                if not is_local:
                    perm_kb = telegram._build_inline_keyboard([[
                        ("\u2705 Allow", f"perm_{wid}_1"),
                        ("\u2705 Always", f"perm_{wid}_2"),
                        ("\u274c Deny", f"perm_{wid}_{n}"),
                    ]])
                    context_str = f"```\n{perm_context}\n```\n\n" if perm_context else ""
                    if bash_cmd:
                        if perm_context:
                            code_block = f"```\n{perm_context}\n\n{bash_cmd[:2000]}\n```"
                        else:
                            code_block = f"```\n{bash_cmd[:2000]}\n```"
                        msg = f"🔧{tag} {dn} (`{project}`) needs permission:\n\n{code_block}\n{opts_text}{free_text_hint}"
                        kb_id = telegram.tg_send(msg, reply_markup=perm_kb, silent=state._is_silent(_CAT_PERMISSION))
                        config._save_last_msg(wid, msg)
                        config._save_keyboard_msg(wid, kb_id)
                    else:
                        title = perm_header or "needs permission"
                        header_str = f"🔧{tag} {dn} (`{project}`) {title}:\n\n{context_str}"
                        if perm_body:
                            kb_id = telegram._send_long_message(header_str, perm_body, wid, reply_markup=perm_kb, footer=opts_text + free_text_hint, silent=state._is_silent(_CAT_PERMISSION))
                        else:
                            msg = f"{header_str}{opts_text}{free_text_hint}"
                            kb_id = telegram.tg_send(msg, reply_markup=perm_kb, silent=state._is_silent(_CAT_PERMISSION))
                            config._save_last_msg(wid, msg)
                        config._save_keyboard_msg(wid, kb_id)
                # Always save prompt so Telegram fallback works if user switches away
                shortcuts = {"y": 1, "yes": 1, "allow": 1,
                             "approve": 1,
                             "n": n, "no": n, "deny": n}
                # Add numeric shortcuts for all options
                for i in range(1, n + 1):
                    shortcuts[str(i)] = i
                state.save_active_prompt(wid, pane, total=n,
                                         shortcuts=shortcuts,
                                         free_text_at=free_text_at)

        elif event == "plan":
            # EnterPlanMode is auto-approved by Claude Code — no blocking dialog.
            # Send an informational notification (no buttons, no active prompt).
            # If a blocking dialog somehow appears, startup dialog detection handles it.
            if not is_local:
                msg = f"🗺{tag} {dn} (`{project}`) entered plan mode."
                telegram.tg_send(msg, silent=state._is_silent(_CAT_QUESTION))
                config._save_last_msg(wid, msg)

        elif event == "question":
            questions = signal.get("questions", [])
            if questions:
                first_opts = len(questions[0].get("options", []))
                remaining = questions[1:] if len(questions) > 1 else None
                if not is_local:
                    msg = _format_question_msg(tag, project, questions[0], cli=cli)
                    opts = questions[0].get("options", [])
                    q_buttons = [(opt.get("label", "?")[:20], f"q_{wid}_{i}")
                                 for i, opt in enumerate(opts, 1)]
                    q_rows = [q_buttons[i:i+3] for i in range(0, len(q_buttons), 3)]
                    q_kb = telegram._build_inline_keyboard(q_rows) if q_buttons else None
                    kb_id = telegram.tg_send(msg, reply_markup=q_kb, silent=state._is_silent(_CAT_QUESTION))
                    config._save_last_msg(wid, msg)
                    if q_kb:
                        config._save_keyboard_msg(wid, kb_id)
                state.save_active_prompt(wid, pane, total=first_opts + 2,
                                         free_text_at=first_opts,
                                         remaining_qs=remaining,
                                         project=project)
            elif not is_local:
                msg = f"❓{tag} {dn} (`{project}`) asks:\n\n(check terminal)"
                telegram.tg_send(msg, silent=state._is_silent(_CAT_QUESTION))
                config._save_last_msg(wid, msg)

        try:
            os.remove(fpath)
        except OSError:
            pass
        if wid:
            last_wid = wid
        local_tag = " [local]" if is_local else ""
        config._log("signal", f"{event} for {wid} ({project}){local_tag}")

    return last_wid
