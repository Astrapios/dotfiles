"""Fake tmux layer for simulation tests.

I/O functions (scan_claude_sessions, _capture_pane, etc.) return
from pre-configured state.  Pure functions (_join_wrapped_lines,
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


class FakeTmux:
    """Stateful replacement for astra.tmux I/O functions."""

    def __init__(self):
        self.panes: dict[str, PaneState] = {}  # window_index -> PaneState
        self._locally_viewed: set[str] = set()

    # --- I/O fakes ---

    def scan_claude_sessions(self):
        return {idx: (ps.pane_target, ps.project) for idx, ps in self.panes.items()}

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
                    idle=False, ansi_content=""):
        """Register a simulated Claude session.

        If *idle* is True, sets content to show an idle prompt (``❯``).
        """
        if idle and not content:
            content = "❯ "
        self.panes[win_idx] = PaneState(
            pane_target=pane_target,
            project=project,
            content=content,
            ansi_content=ansi_content,
            width=width,
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
