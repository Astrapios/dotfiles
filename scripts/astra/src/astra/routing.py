"""Pane routing and option selection."""
from __future__ import annotations

import re
import shlex
import subprocess
import time

from astra import config, telegram, state, signals, tmux


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


_ANSI_STRIP_RE = re.compile(r'\033\[[0-9;]*m')
_ANSI_256_COLOR_RE = re.compile(r'\033\[38;5;(\d+)m')


def _has_colored_spinner(ansi_raw: str) -> bool:
    """Detect active Claude Code spinner via non-grey ANSI color codes.

    Active spinners (✢ Channeling…, ✶ Working…, etc.) use colored
    escape codes (e.g. 38;5;174 salmon), while completed summaries and
    chrome use grey (232-255) or dim.
    """
    for line in ansi_raw.splitlines():
        stripped = _ANSI_STRIP_RE.sub('', line).strip()
        if not stripped:
            continue
        # Match spinner pattern: non-word symbol + space + word
        if not re.match(r'^[^\w\s●❯─━⏵⏸] \w', stripped):
            continue
        # Check for non-grey 256-color codes on this line
        for m in _ANSI_256_COLOR_RE.finditer(line):
            n = int(m.group(1))
            # 232-255 greyscale ramp, 0/7/8/15 neutral black/white/grey
            if not (232 <= n <= 255 or n in (0, 7, 8, 15)):
                return True
    return False


def _is_ui_chrome(s: str, profile=None) -> bool:
    """Check if a stripped line is CLI UI chrome (not real content).
    profile: CLIProfile for CLI-specific busy indicator (defaults to Claude)."""
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
    # Thinking/spinner without timing (e.g. "⠐ Thinking…", "✶ Working…")
    if re.match(r'^[^\w\s●❯] \w+.*(…|\.\.\.)', s):
        return True
    # Tool progress lines (e.g. "Reading 1 file… (ctrl+o to expand)")
    if re.search(r'\(ctrl\+\w to \w+\)', s):
        return True
    if re.match(r'^\+\d+ more lines \(', s):
        return True
    # Status bar below prompt (CLI-specific busy indicator)
    busy_ind = (profile.busy_indicator if profile else "esc to interr")
    if busy_ind and busy_ind in s:
        return True
    if re.match(r'^\d+ files? [+-]', s):
        return True
    # Hint lines below prompt: "? for shortcuts", "↵ to send", etc.
    if s in ("? for shortcuts", "\u21b5 to send"):
        return True
    if re.match(r'^(\?|↵|⏎)\s', s):
        return True
    # Gemini decorative bars and status bar
    if re.match(r'^[▀▄]{3,}$', s):
        return True
    if re.search(r'no sandbox|Auto \(Gemini', s):
        return True
    # Gemini hint line: "Enter to submit · ↑/↓ for history · ..."
    if 'Enter to submit' in s:
        return True
    return False


def _profile_for_pane(pane: str):
    """Look up the CLIProfile for a pane via _current_sessions, default CLAUDE."""
    from astra import profiles
    for info in state._current_sessions.values():
        if isinstance(info, tmux.SessionInfo) and info.pane_target == pane:
            p = profiles.get_profile(info.cli)
            if p:
                return p
    return profiles.CLAUDE


def _pane_idle_state(pane: str, profile=None) -> tuple[bool, str]:
    """Check if a pane is idle (has prompt). Returns (is_idle, typed_text).

    Finds the last non-chrome line (skipping separators, hints, spinners)
    and checks if it's a prompt. Old prompt lines from submitted commands
    in earlier lines are correctly ignored.
    typed_text is any text on the same line after the prompt char (locally typed input).
    Uses cursor position to exclude grayed-out auto-suggestions.
    Also checks for busy indicator below the prompt — if present,
    CLI is actively running and the pane is NOT idle.
    Checks for colored (non-grey) spinner symbols via ANSI capture
    as an additional busy signal.
    """
    if profile is None:
        profile = _profile_for_pane(pane)
    prompt_re = profile.prompt_re
    busy_ind = profile.busy_indicator
    try:
        raw = tmux._capture_pane(pane, 15)
    except Exception:
        return False, ""
    saw_busy_indicator = False
    saw_potential_spinner = False
    for line in reversed(raw.splitlines()):
        s = line.strip()
        if busy_ind and busy_ind in s:
            saw_busy_indicator = True
        # Track potential active spinner (non-word symbol + word, no timing)
        if re.match(r'^[^\w\s●❯─━⏵⏸] \w', s) and not re.search(r'\d+[hms]', s):
            saw_potential_spinner = True
        if _is_ui_chrome(s, profile=profile):
            continue
        m = re.match(rf'^(\s*{re.escape(profile.prompt_char)}\s*)(.*)', line)
        if m:
            # Dialog option lines (e.g. "❯ 1. Yes, clear context...") are
            # selection indicators, not idle prompts.  Don't treat as idle.
            after = m.group(2).strip()
            if re.match(r'^\d+\.\s+', after):
                return False, ""
            if saw_busy_indicator:
                return False, ""
            # Colored spinner below prompt = active thinking/working
            if saw_potential_spinner:
                try:
                    ansi_raw = tmux._capture_pane_ansi(pane, 15)
                    if _has_colored_spinner(ansi_raw):
                        return False, ""
                except Exception:
                    pass
            # Use cursor position to exclude auto-suggestions
            cursor_x = tmux._get_cursor_x(pane)
            if cursor_x is not None:
                typed = line[:cursor_x][len(m.group(1)):].strip()
            else:
                typed = m.group(2).strip()
            return True, typed
        return False, ""
    return False, ""


def _get_session_statuses(sessions: dict[str, tuple[str, str]]) -> dict[str, str]:
    """Return {idx: "idle"|"busy"|"interrupted"} for each session."""
    from astra import content
    statuses: dict[str, str] = {}
    for idx, (pane, _project) in sessions.items():
        idle, _ = _pane_idle_state(pane)
        if idle:
            try:
                raw = tmux._capture_pane(pane, 15)
                if content._detect_interrupted(raw):
                    statuses[idx] = "interrupted"
                else:
                    statuses[idx] = "idle"
            except Exception:
                statuses[idx] = "idle"
        else:
            statuses[idx] = "busy"
    return statuses


def route_to_pane(pane: str, win_idx: str, text: str) -> str:
    """Route a message to a tmux pane, handling active prompts.

    If there's an active prompt, translates the reply into arrow-key
    navigation + Enter. Otherwise sends raw text.
    Returns a confirmation message for Telegram.
    """
    wid = f"w{win_idx}" if not win_idx.startswith("w") else win_idx
    label = state._wid_label(wid)
    prompt = state.load_active_prompt(wid)

    if prompt:
        prompt_pane = prompt.get("pane", pane)
        # Discard stale prompts whose pane uses session:window.pane format
        # but doesn't match the current pane (e.g. session renamed 0→main).
        # Pane IDs like %20 are stable across renames and always valid.
        if ":" in prompt_pane and prompt_pane != pane:
            config._log("route", f"discarding stale prompt: stored pane={prompt_pane!r}, current={pane!r}")
            prompt = None
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
                telegram.tg_send(msg, silent=state._is_silent(3))
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
                telegram.tg_send(msg, reply_markup=yn_kb, silent=state._is_silent(3))
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

        # Prompt with no free text and no matching shortcut/number —
        # re-save the prompt so the user can try again.
        state.save_active_prompt(wid, prompt_pane, total=total,
                                 shortcuts=shortcuts,
                                 free_text_at=free_text_at,
                                 remaining_qs=remaining_qs,
                                 project=proj)

        # Active prompt but unrecognized reply — guide the user.
        valid = ", ".join(f"`{k}`" for k in sorted(shortcuts))
        return f"⚠️ Use buttons above or type: {valid}"

    # Check pane idle state (always authoritative)
    is_idle, typed_text = _pane_idle_state(pane)
    busy = state._is_busy(wid)
    config._log("route", f"idle={is_idle}, busy={busy}, pane={pane!r}, wid={wid}")

    # Busy guard: file-based, but pane overrides if session is genuinely idle
    # Grace period: don't self-heal within 5s of marking busy (race between
    # sending Enter and Claude starting to process)
    if busy:
        busy_ts = state._busy_since(wid)
        recently_sent = busy_ts and (time.time() - busy_ts < 5)
        if is_idle and not recently_sent:
            # Double-check: transient states (auto-reload, brief ❯ flash)
            # can cause a false positive. Wait and re-check.
            time.sleep(0.5)
            is_idle2, typed_text = _pane_idle_state(pane)
            if is_idle2:
                # Stop signal was missed (crash, newline issue, etc.) — self-heal
                state._clear_busy(wid)
            else:
                state._save_queued_msg(wid, text)
                return f"💾 Saved for {label} (busy):\n`{text[:500]}`"
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
    cmd = f"tmux send-keys -t {p} -l {shlex.quote(clean_text)} && sleep 0.3 && tmux send-keys -t {p} Enter"
    subprocess.run(["bash", "-c", cmd], timeout=10)
    state._mark_busy(wid)
    return f"📨 Sent to {label}:\n`{text[:500]}`"
