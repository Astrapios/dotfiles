"""Pane capture, session scanning, formatting."""
import os
import re
import subprocess

from tg_hook import config, telegram, state


def get_window_id() -> str | None:
    """Get the tmux window index for the current pane (e.g. 'w0', 'w1')."""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", pane, "-p", "#{window_index}"],
            capture_output=True, text=True, timeout=5,
        )
        idx = result.stdout.strip()
        if idx.isdigit():
            return f"w{idx}"
    except Exception:
        pass
    return None


def get_pane_project(pane: str) -> str:
    """Get project name from a tmux pane's working directory."""
    try:
        res = subprocess.run(
            ["tmux", "display-message", "-t", pane, "-p", "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
        cwd = res.stdout.strip()
        if cwd:
            return cwd.rstrip("/").rsplit("/", 1)[-1]
    except Exception:
        pass
    return "unknown"


def _get_pane_width(pane: str) -> int:
    """Get the character width of a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "display", "-t", pane, "-p", "#{pane_width}"],
            capture_output=True, text=True, timeout=5,
        )
        return int(result.stdout.strip())
    except Exception:
        return 0


def _join_wrapped_lines(lines: list[str], width: int) -> list[str]:
    """Join lines that were soft-wrapped by Claude Code's terminal formatter."""
    if width < 40 or not lines:
        return lines
    result = [lines[0]]
    for line in lines[1:]:
        prev_len = len(result[-1])
        s = line.lstrip()
        indent = len(line) - len(s)
        if (prev_len >= width - 15 and indent >= 2 and s and
                not re.match(r'[●•─━❯✻⏵⏸>*\-\d]', s)):
            result[-1] += " " + s
        else:
            result.append(line)
    return result


def _capture_pane(pane: str, num_lines: int = 20) -> str:
    """Capture the last num_lines from a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane, "-p", "-S", f"-{num_lines}"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.splitlines()
        if len(lines) > num_lines:
            return "\n".join(lines[-num_lines:]) + "\n"
        return result.stdout
    except Exception:
        return ""


def scan_claude_sessions() -> dict[str, tuple[str, str]]:
    """Scan tmux for panes running claude. Returns {window_index: (pane_target, project)}."""
    sessions = {}
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{window_index}\t#{session_name}:#{window_index}.#{pane_index}\t#{pane_current_command}\t#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                win_idx, target, cmd, cwd = parts
                if cmd == "claude":
                    project = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else "?"
                    sessions[win_idx] = (target, project)
    except Exception:
        pass
    return sessions


def format_sessions_message(sessions: dict[str, tuple[str, str]]) -> str:
    """Format a sessions map into a Telegram message."""
    if not sessions:
        return "⚠️ No Claude sessions found in tmux."
    names = state._load_session_names()
    lines = ["📋 *Active Claude sessions:*"]
    for idx in sorted(sessions, key=int):
        target, project = sessions[idx]
        name = names.get(idx, "")
        label = f"`w{idx} [{name}]`" if name else f"`w{idx}`"
        lines.append(f"  {label} — `{project}` (`{target}`)")
    lines.append("\nPrefix messages with `wN` to route (e.g. `w1 fix the bug`).")
    return "\n".join(lines)


def _sessions_keyboard(sessions: dict) -> dict | None:
    """Build inline keyboard with one button per session."""
    if not sessions:
        return None
    names = state._load_session_names()
    buttons = []
    for idx in sorted(sessions, key=int):
        _, project = sessions[idx]
        name = names.get(idx, "")
        label = f"w{idx} [{name}]" if name else f"w{idx} {project}"
        buttons.append((label[:20], f"sess_{idx}"))
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    return telegram._build_inline_keyboard(rows)


def _command_sessions_keyboard(cmd: str, sessions: dict) -> dict | None:
    """Build inline keyboard to pick a session for a command (focus, interrupt, kill)."""
    if not sessions:
        return None
    names = state._load_session_names()
    buttons = []
    for idx in sorted(sessions, key=int):
        _, project = sessions[idx]
        name = names.get(idx, "")
        label = f"w{idx} [{name}]" if name else f"w{idx} {project}"
        buttons.append((label[:20], f"cmd_{cmd}_{idx}"))
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    return telegram._build_inline_keyboard(rows)
