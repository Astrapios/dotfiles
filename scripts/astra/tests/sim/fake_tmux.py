"""Fake tmux layer for simulation tests.

I/O functions (scan_claude_sessions, scan_cli_sessions, _capture_pane, etc.)
return from pre-configured state.  Pure functions (_join_wrapped_lines,
format_sessions_message, _sessions_keyboard) delegate to the real
implementations.

References to real functions are captured at import time so they survive
patching of the module attributes.
"""
from dataclasses import dataclass, field

from astra import tmux as _real_tmux

# Capture real implementations before any patching
_orig_join_wrapped_lines = _real_tmux._join_wrapped_lines
_orig_format_sessions_message = _real_tmux.format_sessions_message
_orig_sessions_keyboard = _real_tmux._sessions_keyboard


@dataclass
class PaneState:
    """Mutable state for a single simulated tmux pane."""
    pane_target: str  # e.g. "%20"
    project: str  # e.g. "myproject"
    content: str = ""  # raw pane content (as returned by _capture_pane)
    ansi_content: str = ""  # ANSI-coded content (for _capture_pane_ansi)
    width: int = 120
    cursor_x: int = 0
    command: str = "node"  # pane_command (default to something Claude-like)
    cli_profile: str = "claude"  # "claude" or "gemini"
    start_command: str = ""  # full start command (for Gemini detection)


class FakeTmux:
    """Stateful replacement for astra.tmux I/O functions."""

    def __init__(self):
        self.panes: dict[str, PaneState] = {}  # window_index -> PaneState
        self._locally_viewed: set[str] = set()

    # --- I/O fakes ---

    def scan_claude_sessions(self):
        return self.scan_cli_sessions()

    def scan_cli_sessions(self):
        """Return {wid: SessionInfo} mirroring the real scan_cli_sessions."""
        # Group by real window index (strip _N suffix for multi-pane keys)
        by_window: dict[str, list[PaneState]] = {}
        for key, ps in self.panes.items():
            win_idx = key.split("_")[0]
            by_window.setdefault(win_idx, []).append(ps)

        sessions = {}
        for win_idx, panes in by_window.items():
            for i, ps in enumerate(panes):
                suffix = chr(ord("a") + i)
                info = _real_tmux.SessionInfo(
                    pane_target=ps.pane_target, project=ps.project,
                    cli=ps.cli_profile, win_idx=win_idx, pane_suffix=suffix,
                )
                sessions[info.wid] = info
        return sessions

    def _capture_pane(self, pane, num_lines=20):
        for ps in self.panes.values():
            if ps.pane_target == pane:
                lines = ps.content.split("\n")
                return "\n".join(lines[-num_lines:])
        return ""

    def _capture_pane_ansi(self, pane, num_lines=20):
        for ps in self.panes.values():
            if ps.pane_target == pane:
                content = ps.ansi_content or ps.content
                lines = content.split("\n")
                return "\n".join(lines[-num_lines:])
        return ""

    def _get_pane_width(self, pane):
        for ps in self.panes.values():
            if ps.pane_target == pane:
                return ps.width
        return 120

    def _get_cursor_x(self, pane):
        for ps in self.panes.values():
            if ps.pane_target == pane:
                return ps.cursor_x
        return 0

    def _get_pane_command(self, pane):
        for ps in self.panes.values():
            if ps.pane_target == pane:
                return ps.command
        return "zsh"

    def _get_locally_viewed_windows(self):
        return set(self._locally_viewed)

    # --- Pure functions (delegate to captured real implementations) ---

    @staticmethod
    def _join_wrapped_lines(lines, width):
        return _orig_join_wrapped_lines(lines, width)

    @staticmethod
    def format_sessions_message(sessions, statuses=None, locally_viewed=None):
        return _orig_format_sessions_message(sessions, statuses=statuses,
                                              locally_viewed=locally_viewed)

    @staticmethod
    def _sessions_keyboard(sessions):
        return _orig_sessions_keyboard(sessions)

    # --- Test helpers ---

    def add_session(self, win_idx, pane_target, project, content="", width=120,
                    idle=False, ansi_content="", cli="claude"):
        """Register a simulated CLI session.

        If *idle* is True, sets content to show an idle prompt (``❯`` for Claude,
        ``>`` for Gemini).
        """
        if idle and not content:
            if cli == "gemini":
                content = "> "
            else:
                content = "❯ "
        self.panes[win_idx] = PaneState(
            pane_target=pane_target,
            project=project,
            content=content,
            ansi_content=ansi_content,
            width=width,
            cli_profile=cli,
            start_command=f"/usr/bin/{cli}" if not content else "",
        )

    def add_multi_pane_session(self, win_idx, pane_target, project, cli="claude",
                               content="", idle=False, ansi_content="", width=120):
        """Add a pane to a window that already has sessions.

        Uses a synthetic unique key (win_idx + suffix) since FakeTmux stores
        panes by key. The scan_cli_sessions() method groups by win_idx.
        """
        # Find next available sub-key for this window
        existing = [k for k in self.panes if k.startswith(win_idx + "_")]
        if win_idx not in self.panes and not existing:
            # First pane — store with bare win_idx, but we need to re-key
            # for multi-pane to work. Move existing pane to win_idx_0.
            self.add_session(win_idx + "_0", pane_target, project, content, width,
                            idle, ansi_content, cli)
            return
        suffix = len(existing) + (1 if win_idx in self.panes else 0)
        key = f"{win_idx}_{suffix}"
        if idle and not content:
            if cli == "gemini":
                content = "> "
            else:
                content = "❯ "
        self.panes[key] = PaneState(
            pane_target=pane_target,
            project=project,
            content=content,
            ansi_content=ansi_content,
            width=width,
            cli_profile=cli,
            start_command=f"/usr/bin/{cli}",
        )

    def set_pane_content(self, win_idx, content):
        """Update the raw content of a pane."""
        self.panes[win_idx].content = content

    def set_pane_idle(self, win_idx):
        """Set pane content to show an idle ``❯`` prompt."""
        self.panes[win_idx].content = "❯ "
        self.panes[win_idx].cursor_x = 2

    def set_pane_ansi_content(self, win_idx, ansi_content):
        """Update the ANSI-coded content of a pane."""
        self.panes[win_idx].ansi_content = ansi_content

    def set_locally_viewed(self, *win_indices):
        """Set which windows are being locally viewed."""
        self._locally_viewed = set(win_indices)
