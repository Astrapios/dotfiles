"""Unified tmux send-keys API.

Centralizes all `tmux send-keys` invocations behind named helpers with a
shared sleep schedule.  Callers pass raw text/key names; quoting and the
literal-mode flag (-l) for text are handled here.

All helpers wrap a single bash invocation so that key sequences within
one helper call do not get dropped (the `tmux send-keys K1 K2` pattern
matters — separate subprocess calls have been observed to lose keys).

The sleep constants are the canonical, behaviour-preserving values
previously scattered across routing.py / listener.py / commands.py.  Do
not change them without explicit reason — they encode timing the CLI
front-ends actually need.
"""
from __future__ import annotations

import shlex
import subprocess


# Settle delays between key events. Names describe the gap they bridge.
_AFTER_ESCAPE = 0.3    # Esc → next key (e.g. inject-while-busy)
_AFTER_TYPE = 0.3      # typed text → Enter (normal send)
_AFTER_TYPE_INJECT = 0.1  # typed text → Enter (inject-busy: shorter)
_BETWEEN_KEYS = 0.1    # generic gap between named keys


def _run(cmd: str, timeout: int = 10) -> None:
    """Run a bash command (used so multiple send-keys in one invocation
    cannot be interleaved or dropped between subprocess calls)."""
    subprocess.run(["bash", "-c", cmd], timeout=timeout)


def _strip_newlines(text: str) -> str:
    """Replace newlines with spaces — send-keys -l would emit LF which
    Claude Code doesn't treat as Enter, leaving messages unsubmitted."""
    return text.replace("\n", " ").replace("\r", " ")


def type_text(pane: str, text: str) -> None:
    """Type text literally into the pane (send-keys -l). Strips newlines."""
    p = shlex.quote(pane)
    clean = _strip_newlines(text)
    _run(f"tmux send-keys -t {p} -l {shlex.quote(clean)}")


def press_key(pane: str, key: str, timeout: int = 5) -> None:
    """Send a single named key (Enter, Escape, BTab, Down, C-c, …)."""
    p = shlex.quote(pane)
    _run(f"tmux send-keys -t {p} {key}", timeout=timeout)


def press_keys(pane: str, *keys: str) -> None:
    """Send multiple named keys in a single tmux send-keys call.

    Use this when the CLI must see the keys arrive as a group (e.g.
    Down Down Enter for option navigation). Individual subprocess calls
    have been observed to drop keys, so we batch.
    """
    if not keys:
        return
    p = shlex.quote(pane)
    _run(f"tmux send-keys -t {p} {' '.join(keys)}")


def select_option(pane: str, n: int) -> None:
    """Navigate to option n (1-based) and press Enter.

    For n > 1, sends `Down*(n-1)` then sleeps `_BETWEEN_KEYS` then Enter.
    For n == 1, just Enter. The sleep prevents Enter racing the Downs.
    """
    p = shlex.quote(pane)
    if n > 1:
        nav = " ".join(["Down"] * (n - 1))
        cmd = (f"tmux send-keys -t {p} {nav} && sleep {_BETWEEN_KEYS} && "
               f"tmux send-keys -t {p} Enter")
    else:
        cmd = f"tmux send-keys -t {p} Enter"
    _run(cmd)


def submit_text(pane: str, text: str, settle: float = _AFTER_TYPE) -> None:
    """Type text + sleep(settle) + Enter. Strips newlines.

    `settle` is the gap between typed input and the Enter keypress; it
    gives the CLI time to process the input (especially image-path
    previews, which need ~0.5s for albums).
    """
    p = shlex.quote(pane)
    clean = _strip_newlines(text)
    cmd = (f"tmux send-keys -t {p} -l {shlex.quote(clean)} && "
           f"sleep {settle} && tmux send-keys -t {p} Enter")
    _run(cmd)


def inject_busy(pane: str, text: str) -> None:
    """Inject additional instruction into a busy session.

    Sequence: Escape → sleep → type → sleep → Enter. Opens Claude Code's
    additional-instruction input mid-task instead of queuing the message.
    """
    p = shlex.quote(pane)
    clean = _strip_newlines(text)
    cmd = (f"tmux send-keys -t {p} Escape && sleep {_AFTER_ESCAPE} && "
           f"tmux send-keys -t {p} -l {shlex.quote(clean)} && "
           f"sleep {_AFTER_TYPE_INJECT} && tmux send-keys -t {p} Enter")
    _run(cmd)


def navigate_then_submit(pane: str, down_count: int, text: str,
                         nav_settle: float = 0.2) -> None:
    """Down*N → sleep → type → sleep → Enter, in one bash call.

    Used for the free-text reply path: navigate down to a "Type something"
    option in a Claude AskUserQuestion dialog, then submit text directly.
    """
    p = shlex.quote(pane)
    clean = _strip_newlines(text)
    if down_count > 0:
        nav = " ".join(["Down"] * down_count)
        cmd = (f"tmux send-keys -t {p} {nav} && sleep {nav_settle} && "
               f"tmux send-keys -t {p} -l {shlex.quote(clean)} && "
               f"sleep {_AFTER_TYPE_INJECT} && tmux send-keys -t {p} Enter")
    else:
        cmd = (f"tmux send-keys -t {p} -l {shlex.quote(clean)} && "
               f"sleep {_AFTER_TYPE_INJECT} && tmux send-keys -t {p} Enter")
    _run(cmd)


def triple_ctrl_c(pane: str) -> None:
    """Send C-c three times with 0.1s gaps — used to forcibly kill a session."""
    p = shlex.quote(pane)
    cmd = (f"tmux send-keys -t {p} C-c && sleep {_BETWEEN_KEYS} && "
           f"tmux send-keys -t {p} C-c && sleep {_BETWEEN_KEYS} && "
           f"tmux send-keys -t {p} C-c")
    _run(cmd)


def clear_typed(pane: str) -> None:
    """Send a single Escape — used to clear locally-typed text before
    routing a different message into the same pane."""
    press_key(pane, "Escape")


def interrupt(pane: str) -> None:
    """Interrupt the session: Escape, then C-u to clear the prompt line."""
    p = shlex.quote(pane)
    cmd = (f"tmux send-keys -t {p} Escape && sleep {_BETWEEN_KEYS} && "
           f"tmux send-keys -t {p} C-u")
    _run(cmd, timeout=5)
