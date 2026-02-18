"""Tests for CLI subcommands (config, session, management)."""
import json
import os
import sys
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import astra
from astra import config, state, tmux


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    """Redirect all state files to tmp_path so tests don't affect real config."""
    sig_dir = str(tmp_path / "signals")
    os.makedirs(sig_dir, exist_ok=True)
    monkeypatch.setattr(config, "SIGNAL_DIR", sig_dir)
    monkeypatch.setattr(config, "GOD_MODE_PATH", str(tmp_path / "god_mode.json"))
    monkeypatch.setattr(state, "NOTIFICATION_CONFIG_PATH", str(tmp_path / "notifications.json"))


def _fake_sessions():
    """Return a fake sessions dict for tmux-dependent tests."""
    return {
        "w3a": tmux.SessionInfo(pane_target="%5", project="my-proj", cli="claude",
                                win_idx="3", pane_suffix="a", pane_id="%5"),
        "w5a": tmux.SessionInfo(pane_target="%7", project="other", cli="gemini",
                                win_idx="5", pane_suffix="a", pane_id="%7"),
    }


# --- god ---

class TestCmdGod:
    def test_status_off(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "god"])
        astra.cmd_god()
        assert "God mode: off" in capsys.readouterr().out

    def test_enable_all(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "god", "all"])
        astra.cmd_god()
        assert "all sessions" in capsys.readouterr().out
        assert state._is_god_mode_for("w1a")

    def test_disable(self, capsys, monkeypatch):
        state._set_god_mode("all", True)
        monkeypatch.setattr(sys, "argv", ["astra", "god", "off"])
        astra.cmd_god()
        assert "off" in capsys.readouterr().out
        assert not state._god_mode_wids()

    def test_enable_specific_wid(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "god", "w3"])
        astra.cmd_god()
        out = capsys.readouterr().out
        assert "w3" in out
        assert state._is_god_mode_for("w3")

    def test_quiet(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "god", "quiet"])
        astra.cmd_god()
        assert "suppressed" in capsys.readouterr().out
        assert state._is_god_quiet()

    def test_loud(self, capsys, monkeypatch):
        state._set_god_quiet(True)
        monkeypatch.setattr(sys, "argv", ["astra", "god", "loud"])
        astra.cmd_god()
        assert "enabled" in capsys.readouterr().out
        assert not state._is_god_quiet()

    def test_status_shows_quiet(self, capsys, monkeypatch):
        state._set_god_mode("all", True)
        state._set_god_quiet(True)
        monkeypatch.setattr(sys, "argv", ["astra", "god"])
        astra.cmd_god()
        out = capsys.readouterr().out
        assert "(quiet)" in out

    def test_invalid_arg(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "god", "bogus"])
        with pytest.raises(SystemExit):
            astra.cmd_god()

    def test_q_alias(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "god", "q"])
        astra.cmd_god()
        assert "suppressed" in capsys.readouterr().out

    def test_l_alias(self, capsys, monkeypatch):
        state._set_god_quiet(True)
        monkeypatch.setattr(sys, "argv", ["astra", "god", "l"])
        astra.cmd_god()
        assert "enabled" in capsys.readouterr().out


# --- local ---

class TestCmdLocal:
    def test_status_default_on(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "local"])
        astra.cmd_local()
        assert "on" in capsys.readouterr().out

    def test_off(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "local", "off"])
        astra.cmd_local()
        assert "off" in capsys.readouterr().out
        assert not state._is_local_suppress_enabled()

    def test_on(self, capsys, monkeypatch):
        state._set_local_suppress(False)
        monkeypatch.setattr(sys, "argv", ["astra", "local", "on"])
        astra.cmd_local()
        assert "on" in capsys.readouterr().out
        assert state._is_local_suppress_enabled()

    def test_invalid_arg(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "local", "bogus"])
        with pytest.raises(SystemExit):
            astra.cmd_local()


# --- autofocus ---

class TestCmdAutofocus:
    def test_status_default_on(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "autofocus"])
        astra.cmd_autofocus()
        assert "on" in capsys.readouterr().out

    def test_off(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "autofocus", "off"])
        astra.cmd_autofocus()
        assert "off" in capsys.readouterr().out
        assert not state._is_autofocus_enabled()

    def test_on(self, capsys, monkeypatch):
        state._set_autofocus(False)
        monkeypatch.setattr(sys, "argv", ["astra", "autofocus", "on"])
        astra.cmd_autofocus()
        assert "on" in capsys.readouterr().out
        assert state._is_autofocus_enabled()

    def test_invalid_arg(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "autofocus", "bogus"])
        with pytest.raises(SystemExit):
            astra.cmd_autofocus()


# --- notification ---

class TestCmdNotification:
    def test_status_shows_categories(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "notification"])
        astra.cmd_notification()
        out = capsys.readouterr().out
        assert "permission" in out
        assert "stop" in out
        assert "1." in out and "loud" in out

    def test_set_all(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "notification", "all"])
        astra.cmd_notification()
        out = capsys.readouterr().out
        assert "loud" in out
        loud = state._load_notification_config()
        assert loud == set(state._NOTIFICATION_CATEGORIES.keys())

    def test_set_off(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "notification", "off"])
        astra.cmd_notification()
        assert "none" in capsys.readouterr().out
        assert state._load_notification_config() == set()

    def test_set_digits(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "notification", "135"])
        astra.cmd_notification()
        out = capsys.readouterr().out
        assert "[1, 3, 5]" in out
        assert state._load_notification_config() == {1, 3, 5}

    def test_invalid_arg(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "notification", "abc"])
        with pytest.raises(SystemExit):
            astra.cmd_notification()


# --- status ---

class TestCmdStatus:
    def test_no_sessions(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "status"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value={}):
            astra.cmd_status()
        assert "No CLI sessions found" in capsys.readouterr().out

    def test_list_sessions(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "status"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions), \
             patch.object(astra.routing, "_get_session_statuses", return_value={"w3a": "idle", "w5a": "busy"}), \
             patch.object(astra.tmux, "_get_locally_viewed_windows", return_value=set()):
            astra.cmd_status()
        out = capsys.readouterr().out
        assert "my-proj" in out
        assert "other" in out

    def test_specific_session(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "status", "w3"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions), \
             patch.object(astra.tmux, "_get_pane_width", return_value=120), \
             patch.object(astra.tmux, "_capture_pane", return_value="● Hello world\n❯ "), \
             patch.object(astra.content, "_has_response_start", return_value=True), \
             patch.object(astra.content, "clean_pane_status", return_value="Hello world"), \
             patch.object(astra.content, "clean_pane_content", return_value="Hello world"):
            astra.cmd_status()
        out = capsys.readouterr().out
        assert "w3" in out
        assert "my-proj" in out

    def test_unknown_session(self, monkeypatch):
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "status", "w99"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            with pytest.raises(SystemExit):
                astra.cmd_status()


# --- focus / deepfocus / unfocus ---

class TestCmdFocus:
    def test_show_status_off(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "focus"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=_fake_sessions()):
            astra.cmd_focus()
        assert "off" in capsys.readouterr().out

    def test_set_focus(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "focus", "w3"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            astra.cmd_focus()
        out = capsys.readouterr().out
        assert "w3" in out
        assert "my-proj" in out
        fs = state._load_focus_state()
        assert fs["wid"] == "w3a"

    def test_focus_clears_deepfocus(self, monkeypatch):
        state._save_deepfocus_state("w5a", "%7", "other")
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "focus", "w3"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            astra.cmd_focus()
        assert state._load_deepfocus_state() is None

    def test_unknown_session(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "focus", "w99"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=_fake_sessions()):
            with pytest.raises(SystemExit):
                astra.cmd_focus()


class TestCmdDeepfocus:
    def test_show_status_off(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "deepfocus"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=_fake_sessions()):
            astra.cmd_deepfocus()
        assert "off" in capsys.readouterr().out

    def test_set_deepfocus(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "deepfocus", "w5"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            astra.cmd_deepfocus()
        out = capsys.readouterr().out
        assert "w5" in out
        ds = state._load_deepfocus_state()
        assert ds["wid"] == "w5a"

    def test_deepfocus_clears_focus(self, monkeypatch):
        state._save_focus_state("w3a", "%5", "my-proj")
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "deepfocus", "w5"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            astra.cmd_deepfocus()
        assert state._load_focus_state() is None


class TestCmdUnfocus:
    def test_clears_all(self, capsys, monkeypatch):
        state._save_focus_state("w3a", "%5", "proj")
        state._save_deepfocus_state("w5a", "%7", "proj2")
        state._save_smartfocus_state("w3a", "%5", "proj")
        monkeypatch.setattr(sys, "argv", ["astra", "unfocus"])
        astra.cmd_unfocus()
        assert "stopped" in capsys.readouterr().out
        assert state._load_focus_state() is None
        assert state._load_deepfocus_state() is None
        assert state._load_smartfocus_state() is None


# --- clear ---

class TestCmdClear:
    def test_clear_all(self, capsys, monkeypatch):
        state._save_focus_state("w3a", "%5", "proj")
        monkeypatch.setattr(sys, "argv", ["astra", "clear"])
        astra.cmd_clear()
        assert "all" in capsys.readouterr().out
        assert state._load_focus_state() is None

    def test_clear_specific(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        state._mark_busy("w3a")
        monkeypatch.setattr(sys, "argv", ["astra", "clear", "w3"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            astra.cmd_clear()
        assert "w3" in capsys.readouterr().out
        assert not state._is_busy("w3a")

    def test_clear_unknown(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "clear", "w99"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=_fake_sessions()):
            with pytest.raises(SystemExit):
                astra.cmd_clear()


# --- interrupt ---

class TestCmdInterrupt:
    def test_interrupt_session(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        state._mark_busy("w3a")
        monkeypatch.setattr(sys, "argv", ["astra", "interrupt", "w3"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions), \
             patch("subprocess.run") as mock_run:
            astra.cmd_interrupt()
        out = capsys.readouterr().out
        assert "Interrupted" in out
        assert "w3" in out
        assert not state._is_busy("w3a")
        mock_run.assert_called_once()

    def test_interrupt_single_session(self, capsys, monkeypatch):
        sessions = {"w3a": _fake_sessions()["w3a"]}
        monkeypatch.setattr(sys, "argv", ["astra", "interrupt"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions), \
             patch("subprocess.run"):
            astra.cmd_interrupt()
        assert "Interrupted" in capsys.readouterr().out

    def test_interrupt_ambiguous(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "interrupt"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=_fake_sessions()):
            with pytest.raises(SystemExit):
                astra.cmd_interrupt()


# --- name ---

class TestCmdName:
    def test_show_no_names(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "name"])
        astra.cmd_name()
        assert "No session names" in capsys.readouterr().out

    def test_set_name(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "name", "w3", "auth"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            astra.cmd_name()
        assert "auth" in capsys.readouterr().out
        names = state._load_session_names()
        assert "w3a" in names

    def test_clear_name(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        state._save_session_name("w3a", "auth")
        monkeypatch.setattr(sys, "argv", ["astra", "name", "w3"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            astra.cmd_name()
        assert "Cleared" in capsys.readouterr().out

    def test_show_names(self, capsys, monkeypatch):
        state._save_session_name("w3a", "auth")
        state._save_session_name("w5a", "api")
        monkeypatch.setattr(sys, "argv", ["astra", "name"])
        astra.cmd_name()
        out = capsys.readouterr().out
        assert "auth" in out
        assert "api" in out


# --- saved ---

class TestCmdSaved:
    def test_no_saved(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "saved"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=_fake_sessions()):
            astra.cmd_saved()
        assert "No saved messages" in capsys.readouterr().out

    def test_show_saved(self, capsys, monkeypatch):
        state._save_queued_msg("w3a", "fix the bug")
        state._save_queued_msg("w3a", "also add tests")
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "saved"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            astra.cmd_saved()
        out = capsys.readouterr().out
        assert "fix the bug" in out
        assert "also add tests" in out

    def test_show_saved_specific(self, capsys, monkeypatch):
        state._save_queued_msg("w3a", "hello")
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "saved", "w3"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions):
            astra.cmd_saved()
        assert "hello" in capsys.readouterr().out

    def test_saved_unknown_session(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "saved", "w99"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=_fake_sessions()):
            with pytest.raises(SystemExit):
                astra.cmd_saved()


# --- log ---

class TestCmdLog:
    def test_log_default(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "log"])
        mock_result = MagicMock()
        mock_result.stdout = "some log output"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            astra.cmd_log()
        assert "some log output" in capsys.readouterr().out
        call_args = mock_run.call_args[0][0]
        assert "-n" in call_args
        assert "30" in call_args

    def test_log_custom_lines(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "log", "50"])
        mock_result = MagicMock()
        mock_result.stdout = "log lines"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            astra.cmd_log()
        call_args = mock_run.call_args[0][0]
        assert "50" in call_args

    def test_log_caps_at_200(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "log", "999"])
        mock_result = MagicMock()
        mock_result.stdout = "log lines"
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            astra.cmd_log()
        call_args = mock_run.call_args[0][0]
        assert "200" in call_args


# --- kill ---

class TestCmdKill:
    def test_kill_needs_arg(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "kill"])
        with pytest.raises(SystemExit):
            astra.cmd_kill()

    def test_kill_unknown(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "kill", "w99"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=_fake_sessions()):
            with pytest.raises(SystemExit):
                astra.cmd_kill()

    def test_kill_success(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "kill", "w3"])
        with patch.object(astra.tmux, "scan_claude_sessions", side_effect=[sessions, {}]), \
             patch("subprocess.run"), \
             patch("time.sleep"):
            astra.cmd_kill()
        assert "Killed" in capsys.readouterr().out

    def test_kill_still_running(self, capsys, monkeypatch):
        sessions = _fake_sessions()
        monkeypatch.setattr(sys, "argv", ["astra", "kill", "w3"])
        with patch.object(astra.tmux, "scan_claude_sessions", side_effect=[sessions, sessions]), \
             patch("subprocess.run"), \
             patch("time.sleep"):
            astra.cmd_kill()
        assert "still running" in capsys.readouterr().err


# --- new ---

class TestCmdNew:
    def test_new_default(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "new"])
        sessions_after = {"w8a": tmux.SessionInfo(
            pane_target="%10", project="claude-0218-1500", cli="claude",
            win_idx="8", pane_suffix="a", pane_id="%10")}
        mock_result = MagicMock()
        mock_result.stdout = "8\n"
        with patch("subprocess.run", return_value=mock_result), \
             patch.object(astra.tmux, "scan_claude_sessions", return_value=sessions_after), \
             patch.object(astra.tmux, "resolve_session_id", return_value="w8a"), \
             patch("os.makedirs"):
            astra.cmd_new()
        assert "Started" in capsys.readouterr().out

    def test_new_with_dir(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "new", "/tmp/test-proj"])
        mock_result = MagicMock()
        mock_result.stdout = "9\n"
        with patch("subprocess.run", return_value=mock_result), \
             patch.object(astra.tmux, "scan_claude_sessions", return_value={}), \
             patch.object(astra.tmux, "resolve_session_id", return_value=None), \
             patch("os.makedirs"):
            astra.cmd_new()
        out = capsys.readouterr().out
        assert "test-proj" in out


# --- restart ---

class TestCmdRestart:
    def test_restart_needs_arg(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "restart"])
        with pytest.raises(SystemExit):
            astra.cmd_restart()

    def test_restart_unknown(self, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "restart", "w99"])
        with patch.object(astra.tmux, "scan_claude_sessions", return_value=_fake_sessions()):
            with pytest.raises(SystemExit):
                astra.cmd_restart()

    def test_restart_success(self, capsys, monkeypatch):
        sessions_before = _fake_sessions()
        sessions_after = _fake_sessions()  # same sessions after restart
        monkeypatch.setattr(sys, "argv", ["astra", "restart", "w3"])
        # First scan returns sessions, second (after kill) returns empty, third+ returns sessions_after
        scan_returns = [sessions_before, {}, sessions_after]
        with patch.object(astra.tmux, "scan_claude_sessions", side_effect=scan_returns), \
             patch.object(astra.tmux, "_get_pane_cwd", return_value="/home/user/proj"), \
             patch.object(astra.tmux, "_get_pane_command", return_value="zsh"), \
             patch.object(astra.tmux, "resolve_session_id", return_value="w3a"), \
             patch("subprocess.run"), \
             patch("time.sleep"):
            astra.cmd_restart()
        assert "Restarted" in capsys.readouterr().out


# --- main() dispatcher ---

class TestMainDispatcher:
    def test_god_dispatched(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "god"])
        astra.main()
        assert "God mode" in capsys.readouterr().out

    def test_local_dispatched(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "local"])
        astra.main()
        assert "Local suppress" in capsys.readouterr().out

    def test_autofocus_dispatched(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "autofocus"])
        astra.main()
        assert "Autofocus" in capsys.readouterr().out

    def test_notification_dispatched(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "notification"])
        astra.main()
        assert "Notifications" in capsys.readouterr().out

    def test_unfocus_dispatched(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "unfocus"])
        astra.main()
        assert "stopped" in capsys.readouterr().out

    def test_clear_dispatched(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "clear"])
        astra.main()
        assert "Cleared" in capsys.readouterr().out

    def test_name_dispatched(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "name"])
        astra.main()
        assert "No session names" in capsys.readouterr().out

    def test_no_telegram_creds_needed(self, capsys, monkeypatch):
        """All local commands work even without Telegram credentials."""
        monkeypatch.setattr(config, "BOT", "")
        monkeypatch.setattr(config, "CHAT_ID", "")
        for cmd in ("god", "local", "autofocus", "notification", "unfocus", "clear", "name"):
            monkeypatch.setattr(sys, "argv", ["astra", cmd])
            astra.main()
        # All should succeed (no SystemExit for missing creds)

    def test_status_dispatched(self, capsys, monkeypatch):
        monkeypatch.setattr(sys, "argv", ["astra", "status"])
        monkeypatch.setattr(config, "BOT", "")
        monkeypatch.setattr(config, "CHAT_ID", "")
        with patch.object(astra.tmux, "scan_claude_sessions", return_value={}):
            astra.main()
        assert "No CLI sessions" in capsys.readouterr().out
