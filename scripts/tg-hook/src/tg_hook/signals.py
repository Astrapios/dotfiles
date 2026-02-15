"""Signal processing and question formatting."""
import json
import os
import re
import time

from tg_hook import config, telegram, tmux, content, state, routing


def _format_question_msg(tag: str, project: str, question: dict) -> str:
    """Format a single AskUserQuestion question for Telegram."""
    parts = [f"❓{tag} Claude Code (`{project}`) asks:\n"]
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
                     smartfocus_has_sent: bool = False) -> str | None:
    """Process pending signal files. Returns last window index (e.g. '4') or None.
    If focused_wids is set, stop signals for those windows are suppressed.
    smartfocus_prev: previous lines from smartfocus monitoring, used to send
    only the tail (new content) when a smartfocus session stops.
    smartfocus_has_sent: whether any 👁 update was sent during this smartfocus session."""
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
        w_idx = wid.lstrip("w") if wid else ""
        tag = f" {state._wid_label(w_idx)}" if w_idx else ""

        if pane:
            project = tmux.get_pane_project(pane) or project

        if event == "stop":
            state._clear_busy(wid)
            sf = state._load_smartfocus_state()
            was_smartfocus = sf and sf["wid"] == w_idx
            if was_smartfocus:
                state._clear_smartfocus_state()

            stop_kb = telegram._build_inline_keyboard([[
                ("\U0001f4cb Status", f"cmd_status_{wid}"),
                ("\U0001f50d Focus", f"cmd_focus_{wid}"),
            ]])

            if focused_wids and w_idx in focused_wids:
                pass
            elif was_smartfocus and pane:
                # Smartfocus session ended — send tail or full content
                time.sleep(1)
                pw = tmux._get_pane_width(pane)
                for num_lines in (30, 80, 200):
                    raw = tmux._capture_pane(pane, num_lines)
                    if content._has_response_start(raw):
                        break
                cleaned = content.clean_pane_content(raw, "stop", pw) if raw else ""
                if cleaned and smartfocus_prev:
                    cur_lines = cleaned.splitlines()
                    new = content._compute_new_lines(smartfocus_prev, cur_lines)
                    if new:
                        # New lines since last 👁 update — send tail
                        tail_text = "\n".join(new).strip()
                        if tail_text:
                            header = f"✅{tag} (`{project}`) finished:\n\n"
                            telegram._send_long_message(header, tail_text, wid, reply_markup=stop_kb)
                        else:
                            telegram.tg_send(f"✅{tag} (`{project}`) finished.", reply_markup=stop_kb)
                    elif smartfocus_has_sent:
                        # No new lines but 👁 already delivered content
                        telegram.tg_send(f"✅{tag} (`{project}`) finished.", reply_markup=stop_kb)
                    else:
                        # No new lines AND never sent any 👁 — send full response
                        header = f"✅{tag} (`{project}`) finished:\n\n"
                        telegram._send_long_message(header, cleaned, wid, reply_markup=stop_kb)
                elif cleaned:
                    # No prev_lines (very fast response) — send full content
                    header = f"✅{tag} (`{project}`) finished:\n\n"
                    telegram._send_long_message(header, cleaned, wid, reply_markup=stop_kb)
                else:
                    telegram.tg_send(f"✅{tag} (`{project}`) finished.", reply_markup=stop_kb)
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
                header = f"✅{tag} Claude Code (`{project}`) finished:\n\n"
                telegram._send_long_message(header, cleaned, wid, reply_markup=stop_kb)

            # Check for queued messages (always, regardless of focus)
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
                    f"💾 {len(queued)} saved message(s) for {state._wid_label(w_idx)}:\n{preview}",
                    reply_markup=saved_kb,
                )

            # God mode: ensure accept-edits is on when session becomes idle
            if pane and w_idx and state._is_god_mode_for(w_idx):
                from tg_hook import commands  # deferred to avoid circular
                commands._enable_accept_edits(pane)

        elif event == "permission":
            bash_cmd = signal.get("cmd", "")
            perm_header, perm_body, options, perm_context = content._extract_pane_permission(pane)

            # God mode: auto-accept and send compact receipt (skip plan approvals)
            is_plan_perm = "plan" in signal.get("message", "").lower()
            if w_idx and state._is_god_mode_for(w_idx) and not is_plan_perm:
                desc = bash_cmd[:200] if bash_cmd else (perm_header or "permission")
                telegram.tg_send(f"\U0001f531{tag} Auto-allowed (`{project}`): `{desc}`")
                routing._select_option(pane, 1)
            else:
                if options and not any(o.startswith("1.") for o in options):
                    options.insert(0, "1. Yes")
                max_opt = 0
                for o in options:
                    m_opt = re.match(r'(\d+)', o)
                    if m_opt:
                        max_opt = max(max_opt, int(m_opt.group(1)))
                opts_text = "\n".join(options)
                n = max_opt or 3
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
                    msg = f"🔧{tag} Claude Code (`{project}`) needs permission:\n\n{code_block}\n{opts_text}"
                    telegram.tg_send(msg, reply_markup=perm_kb)
                    config._save_last_msg(wid, msg)
                else:
                    title = perm_header or "needs permission"
                    header_str = f"🔧{tag} Claude Code (`{project}`) {title}:\n\n{context_str}"
                    if perm_body:
                        telegram._send_long_message(header_str, perm_body, wid, reply_markup=perm_kb, footer=opts_text)
                    else:
                        msg = f"{header_str}{opts_text}"
                        telegram.tg_send(msg, reply_markup=perm_kb)
                        config._save_last_msg(wid, msg)
                state.save_active_prompt(wid, pane, total=n,
                                         shortcuts={"y": 1, "yes": 1, "allow": 1,
                                                    "approve": 1,
                                                    "n": n, "no": n, "deny": n})

        elif event == "plan":
            # PreToolUse fires before the dialog — wait for it to appear
            time.sleep(2)
            perm_header, perm_body, options, perm_context = content._extract_pane_permission(pane)
            if options and not any(o.startswith("1.") for o in options):
                options.insert(0, "1. Yes")
            max_opt = 0
            for o in options:
                m_opt = re.match(r'(\d+)', o)
                if m_opt:
                    max_opt = max(max_opt, int(m_opt.group(1)))
            opts_text = "\n".join(options)
            deny_at = max_opt or 2

            # Check if dialog has a free text option ("Type something.")
            free_text_at = None
            total = deny_at
            try:
                raw = tmux._capture_pane(pane, 10)
                for line in raw.splitlines():
                    if re.match(r'^\s*Type (something|your)', line.strip()):
                        free_text_at = deny_at
                        total = deny_at + 2  # + "Type something" + "Chat about this"
                        break
            except Exception:
                pass

            plan_kb = telegram._build_inline_keyboard([
                [("\u2705 Approve", f"perm_{wid}_1"),
                 ("\u274c Deny", f"perm_{wid}_{deny_at}")],
            ])
            free_text_hint = "\n\n_Or type a message to give feedback._" if free_text_at is not None else ""
            msg = f"🗺{tag} Claude Code (`{project}`) wants to enter plan mode:\n{opts_text}{free_text_hint}"
            telegram.tg_send(msg, reply_markup=plan_kb)
            config._save_last_msg(wid, msg)
            state.save_active_prompt(wid, pane, total=total,
                                     free_text_at=free_text_at,
                                     shortcuts={"y": 1, "yes": 1, "approve": 1,
                                                "n": deny_at, "no": deny_at,
                                                "deny": deny_at})

        elif event == "question":
            questions = signal.get("questions", [])
            if questions:
                msg = _format_question_msg(tag, project, questions[0])
                opts = questions[0].get("options", [])
                q_buttons = [(opt.get("label", "?")[:20], f"q_{wid}_{i}")
                             for i, opt in enumerate(opts, 1)]
                q_rows = [q_buttons[i:i+3] for i in range(0, len(q_buttons), 3)]
                q_kb = telegram._build_inline_keyboard(q_rows) if q_buttons else None
                telegram.tg_send(msg, reply_markup=q_kb)
                config._save_last_msg(wid, msg)
                first_opts = len(questions[0].get("options", []))
                remaining = questions[1:] if len(questions) > 1 else None
                state.save_active_prompt(wid, pane, total=first_opts + 2,
                                         free_text_at=first_opts,
                                         remaining_qs=remaining,
                                         project=project)
            else:
                msg = f"❓{tag} Claude Code (`{project}`) asks:\n\n(check terminal)"
                telegram.tg_send(msg)
                config._save_last_msg(wid, msg)

        try:
            os.remove(fpath)
        except OSError:
            pass
        if wid:
            last_wid = wid.lstrip("w")
        config._log("signal", f"{event} for {wid} ({project})")

    return last_wid
