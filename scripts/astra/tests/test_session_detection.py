"""Session detection tests for scan_cli_sessions.

Covers the process-tree fallback added when Claude Code >=2.1.x began setting
its process title to the version string, so tmux's #{pane_current_command}
reads e.g. "2.1.170" instead of "claude" and the pane title is the task
summary rather than "Claude Code".
"""
from types import SimpleNamespace

import pytest

from astra import tmux


def _make_run(tmux_lines, ps_lines):
    """Build a subprocess.run side_effect dispatching on argv."""
    calls = {"ps": 0, "tmux": 0}

    def fake_run(args, **kwargs):
        if args[:2] == ["tmux", "list-panes"]:
            calls["tmux"] += 1
            return SimpleNamespace(stdout="\n".join(tmux_lines) + "\n", returncode=0)
        if args and args[0] == "ps":
            calls["ps"] += 1
            return SimpleNamespace(stdout="\n".join(ps_lines) + "\n", returncode=0)
        return SimpleNamespace(stdout="", returncode=0)

    return fake_run, calls


def test_version_titled_claude_detected_via_process_tree(monkeypatch):
    """A pane whose current command is the version string is still detected
    by walking the process subtree to the real `claude` binary."""
    # pane_pid 76016 is the pane's shell; claude (37178) is its child.
    tmux_lines = [
        "1\tmain:1.0\t2.1.170\t/Users/jisu/proj\t\t"
        "✳ Compare proof document to source\t%1\t76016",
    ]
    ps_lines = [
        "76016 1 /bin/zsh",
        "37178 76016 /opt/homebrew/bin/claude",
    ]
    fake_run, calls = _make_run(tmux_lines, ps_lines)
    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    sessions = tmux.scan_cli_sessions()

    assert "w1a" in sessions
    assert sessions["w1a"].cli == "claude"
    assert sessions["w1a"].pane_id == "%1"
    assert sessions["w1a"].win_idx == "1"
    assert calls["ps"] == 1  # process tree built exactly once


def test_classic_claude_command_needs_no_process_tree(monkeypatch):
    """When #{pane_current_command} is plainly `claude`, detection happens
    directly and the `ps` fallback is never invoked."""
    tmux_lines = [
        "2\tmain:2.0\tclaude\t/Users/jisu/proj\t\tClaude Code\t%5\t12345",
    ]
    fake_run, calls = _make_run(tmux_lines, [])
    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    sessions = tmux.scan_cli_sessions()

    assert sessions["w2a"].cli == "claude"
    assert calls["ps"] == 0


def test_plain_shell_pane_skips_process_tree(monkeypatch):
    """A plain shell pane is not a CLI and must not trigger a `ps` call."""
    tmux_lines = [
        "0\tmain:0.0\tzsh\t/Users/jisu\t\t~/.dotfiles\t%0\t87953",
    ]
    fake_run, calls = _make_run(tmux_lines, [])
    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    sessions = tmux.scan_cli_sessions()

    assert sessions == {}
    assert calls["ps"] == 0


def test_version_titled_pane_with_no_claude_descendant_not_detected(monkeypatch):
    """A version-titled pane whose subtree has no CLI binary stays undetected."""
    tmux_lines = [
        "3\tmain:3.0\t2.1.170\t/Users/jisu\t\tsomething\t%9\t5000",
    ]
    ps_lines = [
        "5000 1 /bin/zsh",
        "5001 5000 vim",
    ]
    fake_run, calls = _make_run(tmux_lines, ps_lines)
    monkeypatch.setattr(tmux.subprocess, "run", fake_run)

    sessions = tmux.scan_cli_sessions()

    assert sessions == {}
    assert calls["ps"] == 1
