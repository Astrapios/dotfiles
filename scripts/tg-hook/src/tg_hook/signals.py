"""Signal processing and question formatting."""
import json
import os
import re
import shlex
import subprocess
import time

from tg_hook import config, telegram, tmux, content, state


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


def process_signals(focused_wids: set[str] | None = None) -> str | None:
    """Process pending signal files. Returns last window index (e.g. '4') or None.
    If focused_wids is set, stop signals for those windows are suppressed."""
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
            if focused_wids and w_idx in focused_wids:
                pass
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
                stop_kb = telegram._build_inline_keyboard([[
                    ("\U0001f4cb Status", f"cmd_status_{wid}"),
                    ("\U0001f50d Focus", f"cmd_focus_{wid}"),
                ]])
                telegram._send_long_message(header, cleaned, wid, reply_markup=stop_kb)

                # Check for queued messages
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
                else:
                    # Restore saved prompt text if no queued messages
                    saved_text = state._pop_prompt_text(wid)
                    if saved_text and pane:
                        p = shlex.quote(pane)
                        subprocess.run(
                            ["bash", "-c",
                             f"tmux send-keys -t {p} -l {shlex.quote(saved_text)}"],
                            timeout=10,
                        )

        elif event == "permission":
            bash_cmd = signal.get("cmd", "")
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
            perm_kb = telegram._build_inline_keyboard([[
                ("\u2705 Allow", f"perm_{wid}_1"),
                ("\u2705 Always", f"perm_{wid}_2"),
                ("\u274c Deny", f"perm_{wid}_{n}"),
            ]])
            context_str = f"```\n{perm_context}\n```\n\n" if perm_context else ""
            if bash_cmd:
                msg = f"🔧{tag} Claude Code (`{project}`) needs permission:\n\n{context_str}```\n{bash_cmd[:2000]}\n```\n{opts_text}"
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
                                                "n": n, "no": n, "deny": n})

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
