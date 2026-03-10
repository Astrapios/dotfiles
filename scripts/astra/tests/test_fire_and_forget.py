"""Tests for _fire_and_forget, bare-wid god mode, and PreToolUse auto-approve."""
import io
import json
import os
import sys
import threading

import pytest

import astra
from astra import telegram


# -- _fire_and_forget --------------------------------------------------------

def test_fire_and_forget_runs_target():
    event = threading.Event()
    telegram._fire_and_forget(lambda: event.set())
    assert event.wait(timeout=2)


@pytest.mark.filterwarnings("ignore::pytest.PytestUnhandledThreadExceptionWarning")
def test_fire_and_forget_exceptions_dont_propagate():
    event = threading.Event()

    def _bad():
        event.set()
        raise RuntimeError("boom")

    telegram._fire_and_forget(_bad)
    assert event.wait(timeout=2)
    # Let the thread fully terminate so the exception is caught by this test's
    # filterwarnings marker, not leaked into the next test.
    import time
    time.sleep(0.05)


# -- bare-wid god mode matching ----------------------------------------------

@pytest.fixture()
def god_mode_dir(tmp_path):
    sig_dir = str(tmp_path / "signals")
    os.makedirs(sig_dir, exist_ok=True)
    orig_sig = astra.config.SIGNAL_DIR
    orig_god = astra.config.GOD_MODE_PATH
    astra.config.SIGNAL_DIR = sig_dir
    astra.config.GOD_MODE_PATH = os.path.join(sig_dir, "_god_mode.json")
    yield
    astra.config.SIGNAL_DIR = orig_sig
    astra.config.GOD_MODE_PATH = orig_god


def test_bare_wid_matches_suffixed(god_mode_dir):
    """Bare 'w4' should match when 'w4a' is in god mode."""
    astra._set_god_mode("4", True)
    assert astra._is_god_mode_for("w4")
    assert not astra._is_god_mode_for("w5")


def test_bare_wid_no_false_prefix_match(god_mode_dir):
    """'w4' should not match 'w40a' — only 'w4' + single-letter suffix."""
    astra._set_god_mode("40", True)
    assert not astra._is_god_mode_for("w4")
    assert astra._is_god_mode_for("w40")


# -- PreToolUse auto-approve (0.16.5) ----------------------------------------

def _run_hook(monkeypatch, hook_data: dict):
    """Feed hook_data as JSON stdin and run cmd_hook(), return captured stdout."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(hook_data)))
    astra.cmd_hook()


def _god_mode_hook_data(tool_name, tool_input, cwd="/home/user/project"):
    return {
        "hook_event_name": "PreToolUse",
        "tool_name": tool_name,
        "tool_input": tool_input,
        "cwd": cwd,
    }


def _signal_files(sig_dir):
    """Return non-state signal files in sig_dir."""
    return [f for f in os.listdir(sig_dir)
            if f.endswith(".json") and not f.startswith("_")]


def test_pre_tool_bash_god_mode_approves(god_mode_dir, monkeypatch, capsys):
    """PreToolUse/Bash in god mode outputs approve and writes signal with tool type."""
    astra._set_god_mode("5", True)
    monkeypatch.setattr(astra.tmux, "get_window_id", lambda: "w5")
    monkeypatch.setattr(astra.config, "TG_HOOKS_ENABLED", True)
    capsys.readouterr()

    _run_hook(monkeypatch, _god_mode_hook_data(
        "Bash", {"command": "rm -rf /tmp/test"}))

    captured = capsys.readouterr()
    result = json.loads(captured.out.strip())
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    sigs = _signal_files(astra.config.SIGNAL_DIR)
    assert len(sigs) == 1
    with open(os.path.join(astra.config.SIGNAL_DIR, sigs[0])) as f:
        sig = json.load(f)
    assert sig["event"] == "god_approve"
    assert sig["tool"] == "shell"
    assert sig["cmd"] == "rm -rf /tmp/test"


def test_pre_tool_bash_no_god_mode_no_output(god_mode_dir, monkeypatch, capsys):
    """PreToolUse/Bash without god mode produces no stdout decision."""
    monkeypatch.setattr(astra.tmux, "get_window_id", lambda: "w5")
    monkeypatch.setattr(astra.config, "TG_HOOKS_ENABLED", True)

    _run_hook(monkeypatch, _god_mode_hook_data(
        "Bash", {"command": "ls"}))

    captured = capsys.readouterr()
    assert captured.out.strip() == ""
    assert _signal_files(astra.config.SIGNAL_DIR) == []


def test_pre_tool_bash_saves_cmd_file(god_mode_dir, monkeypatch):
    """PreToolUse/Bash always saves _bash_cmd file (god mode or not)."""
    monkeypatch.setattr(astra.tmux, "get_window_id", lambda: "w5")
    monkeypatch.setattr(astra.config, "TG_HOOKS_ENABLED", True)

    _run_hook(monkeypatch, _god_mode_hook_data(
        "Bash", {"command": "echo hello"}))

    cmd_file = os.path.join(astra.config.SIGNAL_DIR, "_bash_cmd_w5.json")
    assert os.path.exists(cmd_file)
    with open(cmd_file) as f:
        assert json.load(f)["cmd"] == "echo hello"


def test_pre_tool_edit_god_mode_approves(god_mode_dir, monkeypatch, capsys):
    """PreToolUse/Edit in god mode outputs approve and writes signal."""
    astra._set_god_mode("5", True)
    monkeypatch.setattr(astra.tmux, "get_window_id", lambda: "w5")
    monkeypatch.setattr(astra.config, "TG_HOOKS_ENABLED", True)
    capsys.readouterr()

    _run_hook(monkeypatch, _god_mode_hook_data(
        "Edit", {"file_path": "/home/user/project/main.py",
                 "old_string": "foo", "new_string": "bar"}))

    captured = capsys.readouterr()
    result = json.loads(captured.out.strip())
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
    sigs = _signal_files(astra.config.SIGNAL_DIR)
    assert len(sigs) == 1
    with open(os.path.join(astra.config.SIGNAL_DIR, sigs[0])) as f:
        sig = json.load(f)
    assert sig["event"] == "god_approve"
    assert sig["tool"] == "edit"
    assert "main.py" in sig["cmd"]


def test_pre_tool_write_god_mode_approves(god_mode_dir, monkeypatch, capsys):
    """PreToolUse/Write in god mode outputs approve and writes signal."""
    astra._set_god_mode("5", True)
    monkeypatch.setattr(astra.tmux, "get_window_id", lambda: "w5")
    monkeypatch.setattr(astra.config, "TG_HOOKS_ENABLED", True)
    capsys.readouterr()

    _run_hook(monkeypatch, _god_mode_hook_data(
        "Write", {"file_path": "/tmp/new_file.txt", "content": "hello"}))

    captured = capsys.readouterr()
    result = json.loads(captured.out.strip())
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"


def test_pre_tool_plan_not_auto_approved(god_mode_dir, monkeypatch, capsys):
    """PreToolUse/EnterPlanMode is NOT auto-approved by god mode."""
    astra._set_god_mode("5", True)
    monkeypatch.setattr(astra.tmux, "get_window_id", lambda: "w5")
    monkeypatch.setattr(astra.config, "TG_HOOKS_ENABLED", True)
    capsys.readouterr()

    _run_hook(monkeypatch, _god_mode_hook_data(
        "EnterPlanMode", {}))

    captured = capsys.readouterr()
    # Should NOT contain approve decision
    assert "approve" not in captured.out


def test_pre_tool_god_mode_bare_wid(god_mode_dir, monkeypatch, capsys):
    """God mode approve works with bare wid (w5 matching w5a in god mode)."""
    astra._set_god_mode("5", True)  # stores as w5a
    monkeypatch.setattr(astra.tmux, "get_window_id", lambda: "w5")
    monkeypatch.setattr(astra.config, "TG_HOOKS_ENABLED", True)
    capsys.readouterr()

    _run_hook(monkeypatch, _god_mode_hook_data(
        "Bash", {"command": "apt install something"}))

    captured = capsys.readouterr()
    result = json.loads(captured.out.strip())
    assert result["hookSpecificOutput"]["permissionDecision"] == "allow"
