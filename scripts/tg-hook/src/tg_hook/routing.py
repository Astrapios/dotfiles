"""Pane routing and option selection."""
import shlex
import subprocess

from tg_hook import config, telegram, state, signals


def _select_option(pane: str, n: int):
    """Navigate to option n (1-based) and press Enter in a tmux pane."""
    p = shlex.quote(pane)
    parts = []
    if n > 1:
        nav = " ".join(["Down"] * (n - 1))
        parts.append(f"tmux send-keys -t {p} {nav}")
        parts.append("sleep 0.1")
    parts.append(f"tmux send-keys -t {p} Enter")
    subprocess.run(["bash", "-c", " && ".join(parts)], timeout=10)


def route_to_pane(pane: str, win_idx: str, text: str) -> str:
    """Route a message to a tmux pane, handling active prompts.

    If there's an active prompt, translates the reply into arrow-key
    navigation + Enter. Otherwise sends raw text.
    Returns a confirmation message for Telegram.
    """
    wid = f"w{win_idx}"
    label = state._wid_label(win_idx)
    prompt = state.load_active_prompt(wid)

    if prompt:
        total = prompt.get("total", 0)
        shortcuts = prompt.get("shortcuts", {})
        free_text_at = prompt.get("free_text_at")
        remaining_qs = prompt.get("remaining_qs")
        reply = text.strip()
        config._log("route", f"prompt found: total={total}, reply={reply!r}, pane={pane}")

        prompt_pane = prompt.get("pane", pane)

        proj = prompt.get("project", "")
        tag = f" {wid}" if wid else ""

        def _advance_question():
            """Handle next question or auto-confirm submission."""
            if remaining_qs:
                next_q = remaining_qs[0]
                rest = remaining_qs[1:]
                n_opts = len(next_q.get("options", []))
                msg = signals._format_question_msg(tag, proj, next_q)
                telegram.tg_send(msg)
                config._save_last_msg(wid, msg)
                state.save_active_prompt(wid, prompt_pane, total=n_opts + 2,
                                         free_text_at=n_opts,
                                         remaining_qs=rest,
                                         project=proj)
            elif remaining_qs is not None:
                msg = f"❓{tag} Submit answers? (y/n)"
                yn_kb = telegram._build_inline_keyboard([
                    [("\u2705 Yes", f"perm_{wid}_1"), ("\u274c No", f"perm_{wid}_2")],
                ])
                telegram.tg_send(msg, reply_markup=yn_kb)
                config._save_last_msg(wid, msg)
                state.save_active_prompt(wid, prompt_pane, total=2,
                                         shortcuts={"y": 1, "yes": 1,
                                                    "n": 2, "no": 2})

        # Shortcut match (e.g. "y" → 1, "n" → 3)
        if reply.lower() in shortcuts:
            n = shortcuts[reply.lower()]
            _select_option(prompt_pane, n)
            _advance_question()
            return f"📨 Selected option {n} in {label}"

        # Numbered selection
        if reply.isdigit():
            n = int(reply)
            if 1 <= n <= total:
                _select_option(prompt_pane, n)
                _advance_question()
                return f"📨 Selected option {n} in {label}"

        # Free text → navigate to "Type something.", type directly, Enter to submit
        if free_text_at is not None:
            pp = shlex.quote(prompt_pane)
            nav = " ".join(["Down"] * free_text_at)
            cmd = (f"tmux send-keys -t {pp} {nav} && sleep 0.2 && "
                   f"tmux send-keys -t {pp} -l {shlex.quote(reply)} && sleep 0.1 && "
                   f"tmux send-keys -t {pp} Enter")
            subprocess.run(["bash", "-c", cmd], timeout=10)
            _advance_question()
            return f"📨 Answered in {label}:\n`{reply[:500]}`"

        # Prompt with no free text and no matching shortcut/number
        pp = shlex.quote(prompt_pane)
        nav = " ".join(["Down"] * (total - 1)) if total > 1 else ""
        cmd = (f"tmux send-keys -t {pp} {nav} && sleep 0.2 && "
               f"tmux send-keys -t {pp} -l {shlex.quote(reply)} && sleep 0.1 && "
               f"tmux send-keys -t {pp} Enter")
        subprocess.run(["bash", "-c", cmd], timeout=10)
        _advance_question()
        return f"📨 Replied in {label}:\n`{reply[:500]}`"

    # Normal message: type text + Enter
    p = shlex.quote(pane)
    cmd = f"tmux send-keys -t {p} -l {shlex.quote(text)} && tmux send-keys -t {p} Enter"
    subprocess.run(["bash", "-c", cmd], timeout=10)
    return f"📨 Sent to {label}:\n`{text[:500]}`"
