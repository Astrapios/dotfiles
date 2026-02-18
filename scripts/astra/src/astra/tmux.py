"""Pane capture, session scanning, formatting."""
from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass

from astra import config, telegram, state


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


def _get_pane_command(pane: str) -> str:
    """Get the current command running in a tmux pane (e.g. 'zsh', 'bash')."""
    try:
        result = subprocess.run(
            ["tmux", "display", "-t", pane, "-p", "#{pane_current_command}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return ""


def _get_pane_cwd(pane: str) -> str:
    """Get the current working directory of a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "display", "-t", pane, "-p", "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip()
    except Exception:
        return ""


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


def _capture_pane_ansi(pane: str, num_lines: int = 20) -> str:
    """Capture pane with ANSI escape codes preserved (``-e`` flag)."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane, "-e", "-p", "-S", f"-{num_lines}"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.splitlines()
        if len(lines) > num_lines:
            return "\n".join(lines[-num_lines:]) + "\n"
        return result.stdout
    except Exception:
        return ""


def _get_cursor_x(pane: str) -> int | None:
    """Get the cursor column position (0-based) for a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", pane, "-p", "#{cursor_x}"],
            capture_output=True, text=True, timeout=5,
        )
        return int(result.stdout.strip())
    except Exception:
        return None


def _get_locally_viewed_windows() -> set[str]:
    """Return window indices currently being viewed by attached tmux clients."""
    try:
        result = subprocess.run(
            ["tmux", "list-clients", "-F", "#{client_session}"],
            capture_output=True, text=True, timeout=5,
        )
        attached = set(result.stdout.strip().splitlines())
        if not attached:
            return set()
        viewed = set()
        for sess in attached:
            result = subprocess.run(
                ["tmux", "list-windows", "-t", sess, "-F",
                 "#{window_index}\t#{window_active}"],
                capture_output=True, text=True, timeout=5,
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split("\t")
                if len(parts) == 2 and parts[1] == "1":
                    viewed.add(parts[0])
        return viewed
    except Exception:
        return set()


def scan_claude_sessions() -> dict[str, SessionInfo]:
    """Scan tmux for panes running any registered CLI.

    Returns {wid: SessionInfo} — SessionInfo supports ``pane, project = info``
    unpacking for backward compat.  Also updates ``state._current_sessions``
    so display helpers like ``_wid_label`` stay current.
    """
    result = scan_cli_sessions()
    state._current_sessions = result
    return result


@dataclass
class SessionInfo:
    """Information about a detected CLI session."""
    pane_target: str    # "main:1.0"
    project: str        # "myproject"
    cli: str            # "claude" or "gemini"
    win_idx: str        # "4"
    pane_suffix: str    # "a" minimum, "b"/"c" for additional panes
    pane_id: str = ""   # "%2" — TMUX_PANE format for signal matching

    @property
    def wid(self) -> str:
        """Full session ID, e.g. 'w4' or 'w4a'."""
        return f"w{self.win_idx}{self.pane_suffix}"

    @property
    def display_name(self) -> str:
        """Human-readable CLI name from profile."""
        from astra import profiles
        p = profiles.get_profile(self.cli)
        return p.display_name if p else self.cli.title()

    def __iter__(self):
        """Allow ``pane, project = session_info`` unpacking."""
        return iter((self.pane_target, self.project))


def scan_cli_sessions() -> dict[str, SessionInfo]:
    """Scan tmux for panes running any registered CLI.

    Returns {wid: SessionInfo} where wid always has a suffix (e.g. 'w4a',
    'w1a'/'w1b').

    Uses #{pane_start_command} and #{pane_title} to identify CLIs like Gemini
    whose pane_current_command is 'node' rather than 'gemini'.
    """
    from astra import profiles

    raw_panes: list[tuple[str, str, str, str, str, str, str]] = []
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{window_index}\t#{session_name}:#{window_index}.#{pane_index}"
             "\t#{pane_current_command}\t#{pane_current_path}"
             "\t#{pane_start_command}\t#{pane_title}\t#{pane_id}"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) >= 7:
                raw_panes.append(tuple(parts[:7]))
            elif len(parts) >= 6:
                raw_panes.append((*parts[:6], ""))
            elif len(parts) >= 4:
                raw_panes.append((parts[0], parts[1], parts[2], parts[3],
                                  parts[4] if len(parts) > 4 else "",
                                  parts[5] if len(parts) > 5 else "", ""))
    except Exception:
        pass

    # Group matched panes by window index
    by_window: dict[str, list[tuple[str, str, str, str]]] = {}  # win_idx → [(target, project, cli, pane_id)]
    for win_idx, target, cmd, cwd, start_cmd, title, pane_id in raw_panes:
        profile = profiles.identify_cli(cmd, start_cmd, title)
        if profile:
            project = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else "?"
            by_window.setdefault(win_idx, []).append((target, project, profile.name, pane_id))

    # Always assign a/b/c suffix — even solo panes get "a"
    sessions: dict[str, SessionInfo] = {}
    for win_idx, panes in by_window.items():
        for i, (target, project, cli, pane_id) in enumerate(panes):
            suffix = chr(ord("a") + i)
            info = SessionInfo(pane_target=target, project=project, cli=cli,
                               win_idx=win_idx, pane_suffix=suffix, pane_id=pane_id)
            sessions[info.wid] = info
    return sessions


def resolve_session_id(raw_wid: str, sessions: dict) -> str | None:
    """Resolve a wid string to a key in sessions dict.

    Handles: direct match, bare 'w4' → 'w4a' fallback, and name resolution.
    Works with both old-style {idx: (pane, project)} and new {wid: SessionInfo} dicts.
    """
    if raw_wid in sessions:
        return raw_wid
    # Bare w4 → try w4a (only when unambiguous — no w4b sibling)
    if re.match(r'^w\d+$', raw_wid):
        suffixed = raw_wid + "a"
        if suffixed in sessions:
            if raw_wid + "b" in sessions:
                return None  # Ambiguous — multiple panes
            return suffixed
    # Numeric-only → try with w prefix
    if raw_wid.isdigit():
        wid = f"w{raw_wid}"
        if wid in sessions:
            return wid
        suffixed = f"w{raw_wid}a"
        if suffixed in sessions:
            if f"w{raw_wid}b" in sessions:
                return None  # Ambiguous — multiple panes
            return suffixed
    # Bare "3a" → try "w3a" (w-prefix stripped by command regexes)
    m = re.match(r'^(\d+[a-z])$', raw_wid)
    if m:
        wid = f"w{raw_wid}"
        if wid in sessions:
            return wid
    return None


def _sort_session_keys(keys):
    """Sort session keys numerically, handling both '4' and 'w4a' formats."""
    def key_func(k):
        m = re.match(r'^w?(\d+)([a-z]?)$', str(k))
        if m:
            return (int(m.group(1)), m.group(2))
        return (9999, str(k))
    return sorted(keys, key=key_func)


def _display_wid(wid: str, sessions: dict) -> str:
    """Return display-friendly wid — 'w3' for solo panes, 'w1a' for multi-pane."""
    info = sessions.get(wid)
    if isinstance(info, SessionInfo) and info.pane_suffix == "a":
        if f"w{info.win_idx}b" not in sessions:
            return f"w{info.win_idx}"
    return wid


def format_sessions_message(sessions: dict[str, tuple[str, str]],
                            statuses: dict[str, str] | None = None,
                            locally_viewed: set[str] | None = None) -> str:
    """Format a sessions map into a Telegram message.

    statuses: optional dict of {idx: "idle"|"busy"|"interrupted"} for each session.
    locally_viewed: optional set of window indices currently viewed in tmux.
    """
    if not sessions:
        return "⚠️ No active sessions found in tmux."
    _status_icons = {"idle": "🟢", "busy": "🟡", "interrupted": "🔴"}
    names = state._load_session_names()
    # Detect if multiple CLI types are present
    cli_types = set()
    for v in sessions.values():
        if isinstance(v, SessionInfo):
            cli_types.add(v.cli)
    has_multi_cli = len(cli_types) > 1
    # Pre-compute panes per window to detect multi-pane windows
    _panes_per_window: dict[str, int] = {}
    for idx in sessions:
        val = sessions[idx]
        if isinstance(val, SessionInfo):
            _panes_per_window[val.win_idx] = _panes_per_window.get(val.win_idx, 0) + 1
        else:
            wi = re.match(r'^w?(\d+)', idx).group(1) if idx else idx
            _panes_per_window[wi] = _panes_per_window.get(wi, 0) + 1

    lines = ["📋 *Active sessions:*"]
    for idx in _sort_session_keys(sessions):
        val = sessions[idx]
        if isinstance(val, SessionInfo):
            project = val.project
            cli_tag = f" · {val.cli.title()}" if has_multi_cli else ""
            display_idx = _display_wid(idx, sessions)
            win_idx = val.win_idx
            is_multi_pane = _panes_per_window.get(win_idx, 1) > 1
        else:
            target, project = val
            cli_tag = ""
            display_idx = _display_wid(idx, sessions)
            if display_idx == idx and not idx.startswith("w"):
                display_idx = f"w{idx}"
            win_idx = re.match(r'^w?(\d+)', idx).group(1) if idx else idx
            is_multi_pane = _panes_per_window.get(win_idx, 1) > 1
        # Name lookup: exact wid first, then bare window index (only for solo panes)
        name = names.get(idx, "")
        if not name and not is_multi_pane:
            name = names.get(win_idx, "") or names.get(f"w{win_idx}", "")
        label = f"`{display_idx} [{name}]`" if name else f"`{display_idx}`"
        god = " ⚡" if state._is_god_mode_for(idx) else ""
        status_icon = ""
        if statuses and idx in statuses:
            status_icon = f" {_status_icons.get(statuses[idx], '')}"
        local_icon = " 👁" if locally_viewed and (idx in locally_viewed or win_idx in locally_viewed) else ""
        lines.append(f"  {label} — `{project}`{status_icon}{cli_tag}{god}{local_icon}")
    lines.append("\nPrefix messages with `wN` to route (e.g. `w1 fix the bug`).")
    return "\n".join(lines)


def _sessions_keyboard(sessions: dict) -> dict | None:
    """Build inline keyboard with one button per session."""
    if not sessions:
        return None
    names = state._load_session_names()
    buttons = []
    for idx in _sort_session_keys(sessions):
        val = sessions[idx]
        if isinstance(val, SessionInfo):
            project = val.project
        else:
            _, project = val
        display_idx = _display_wid(idx, sessions)
        name = names.get(idx, "")
        label = f"{display_idx} [{name}]" if name else f"{display_idx} {project}"
        buttons.append((label[:20], f"sess_{idx}"))
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    return telegram._build_inline_keyboard(rows)


def _command_sessions_keyboard(cmd: str, sessions: dict) -> dict | None:
    """Build inline keyboard to pick a session for a command (focus, interrupt, kill)."""
    if not sessions:
        return None
    names = state._load_session_names()
    buttons = []
    for idx in _sort_session_keys(sessions):
        val = sessions[idx]
        if isinstance(val, SessionInfo):
            project = val.project
        else:
            _, project = val
        display_idx = _display_wid(idx, sessions)
        name = names.get(idx, "")
        label = f"{display_idx} [{name}]" if name else f"{display_idx} {project}"
        buttons.append((label[:20], f"cmd_{cmd}_{display_idx}"))
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    return telegram._build_inline_keyboard(rows)
