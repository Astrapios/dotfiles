"""Pane routing and option selection."""
import re
import shlex
import subprocess
import time

from tg_hook import config, telegram, state, signals, tmux


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


def _is_ui_chrome(s: str) -> bool:
    """Check if a stripped line is Claude Code UI chrome (not real content)."""
    if not s:
        return True
    if re.match(r'^[─━]{3,}$', s):
        return True
    if s.startswith(("⏵⏵ ", "⏸ ")):
        return True
    if s.startswith("Context left until auto-compact:"):
        return True
    if s in ("⏳ Working...", "* Working..."):
        return True
    if re.match(r'^✻ \w+ for ', s):
        return True
    if s.startswith('ctrl+') and 'background' in s:
        return True
    # Thinking/timing indicators: "* Percolating… (1m 14s …)"
    if re.match(r'^[^\w\s] \w', s) and re.search(r'\d+[hms]', s):
        return True
    if re.match(r'^\+\d+ more lines \(', s):
        return True
    return False


def _pane_idle_state(pane: str) -> tuple[bool, str]:
    """Check if a pane is idle (has ❯ prompt). Returns (is_idle, typed_text).

    Finds the last non-chrome line (skipping separators, hints, spinners)
    and checks if it's a ❯ prompt. Old ❯ lines from submitted commands
    in earlier lines are correctly ignored.
    typed_text is any text on the same line after ❯ (locally typed input).
    Uses cursor position to exclude grayed-out auto-suggestions.
    """
    try:
        raw = tmux._capture_pane(pane, 15)
    except Exception:
        return False, ""
    for line in reversed(raw.splitlines()):
        s = line.strip()
        if _is_ui_chrome(s):
            continue
        m = re.match(r'^(\s*❯\s*)(.*)', line)
        if m:
            # Use cursor position to exclude auto-suggestions
            cursor_x = tmux._get_cursor_x(pane)
            if cursor_x is not None:
                typed = line[:cursor_x][len(m.group(1)):].strip()
            else:
                typed = m.group(2).strip()
            return True, typed
        return False, ""
    return False, ""


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

    # Check pane idle state (always authoritative)
    is_idle, typed_text = _pane_idle_state(pane)

    # Busy guard: file-based, but pane overrides if session is genuinely idle
    # Grace period: don't self-heal within 5s of marking busy (race between
    # sending Enter and Claude starting to process)
    if state._is_busy(wid):
        busy_ts = state._busy_since(wid)
        recently_sent = busy_ts and (time.time() - busy_ts < 5)
        if is_idle and not recently_sent:
            # Stop signal was missed (crash, newline issue, etc.) — self-heal
            state._clear_busy(wid)
        else:
            state._save_queued_msg(wid, text)
            return f"💾 Saved for {label} (busy):\n`{text[:500]}`"

    if not is_idle:
        # Claude is busy — queue the message
        state._save_queued_msg(wid, text)
        return f"💾 Saved for {label} (busy):\n`{text[:500]}`"

    p = shlex.quote(pane)

    if typed_text:
        # Save locally typed text to queue and clear it before sending
        state._save_queued_msg(wid, typed_text)
        subprocess.run(["bash", "-c", f"tmux send-keys -t {p} Escape"], timeout=5)
        time.sleep(0.2)

    # Strip newlines — send-keys -l sends \n as LF which Claude Code
    # doesn't treat as Enter (CR), causing the message to never submit.
    clean_text = text.replace("\n", " ").replace("\r", " ")

    # Normal message: type text + Enter (sleep lets Claude Code accept the input)
    cmd = f"tmux send-keys -t {p} -l {shlex.quote(clean_text)} && sleep 0.1 && tmux send-keys -t {p} Enter"
    subprocess.run(["bash", "-c", cmd], timeout=10)
    state._mark_busy(wid)
    return f"📨 Sent to {label}:\n`{text[:500]}`"
