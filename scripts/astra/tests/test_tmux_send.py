"""Tests for the unified tmux_send API."""
from __future__ import annotations

import shlex
from unittest.mock import patch

import astra
from astra import tmux_send


def _captured_bash_cmd(mock_run) -> str:
    """Extract the bash -c command string from the most recent _run call."""
    args, kwargs = mock_run.call_args
    cmd_list = args[0]
    assert cmd_list[0] == "bash"
    assert cmd_list[1] == "-c"
    return cmd_list[2]


@patch.object(tmux_send.subprocess, "run")
class TestTypeText:
    def test_basic(self, mock_run):
        tmux_send.type_text("%30", "hello world")
        cmd = _captured_bash_cmd(mock_run)
        assert "tmux send-keys -t %30" in cmd
        # shlex.quote uses single quotes for strings with spaces
        assert "-l 'hello world'" in cmd
        assert "Enter" not in cmd  # no Enter on raw type

    def test_strips_newlines(self, mock_run):
        tmux_send.type_text("%30", "line1\nline2\rline3")
        cmd = _captured_bash_cmd(mock_run)
        assert "\\n" not in cmd
        assert "line1 line2 line3" in cmd

    def test_quotes_special_chars(self, mock_run):
        tmux_send.type_text("%30", "fix my_var; rm -rf /")
        cmd = _captured_bash_cmd(mock_run)
        # shlex.quote should wrap the text containing ; and spaces
        assert "'fix my_var; rm -rf /'" in cmd

    def test_pane_target_passed_through(self, mock_run):
        """Pane target is shlex.quoted — `0:1.0` is shell-safe so no quotes."""
        tmux_send.type_text("0:1.0", "hi")
        cmd = _captured_bash_cmd(mock_run)
        assert "-t 0:1.0" in cmd


@patch.object(tmux_send.subprocess, "run")
class TestPressKey:
    def test_enter(self, mock_run):
        tmux_send.press_key("%30", "Enter")
        cmd = _captured_bash_cmd(mock_run)
        assert cmd == "tmux send-keys -t %30 Enter"

    def test_escape(self, mock_run):
        tmux_send.press_key("%30", "Escape")
        cmd = _captured_bash_cmd(mock_run)
        assert cmd == "tmux send-keys -t %30 Escape"

    def test_btab(self, mock_run):
        tmux_send.press_key("%30", "BTab")
        cmd = _captured_bash_cmd(mock_run)
        assert "BTab" in cmd

    def test_ctrl_c(self, mock_run):
        tmux_send.press_key("%30", "C-c")
        cmd = _captured_bash_cmd(mock_run)
        assert "C-c" in cmd


@patch.object(tmux_send.subprocess, "run")
class TestPressKeys:
    def test_single_call_for_multiple_keys(self, mock_run):
        """Multiple keys must be sent in ONE tmux send-keys invocation."""
        tmux_send.press_keys("%30", "Down", "Down", "Enter")
        assert mock_run.call_count == 1
        cmd = _captured_bash_cmd(mock_run)
        assert cmd == "tmux send-keys -t %30 Down Down Enter"

    def test_empty_keys_no_op(self, mock_run):
        tmux_send.press_keys("%30")
        assert mock_run.call_count == 0


@patch.object(tmux_send.subprocess, "run")
class TestSelectOption:
    def test_option_1_is_just_enter(self, mock_run):
        tmux_send.select_option("%30", 1)
        cmd = _captured_bash_cmd(mock_run)
        assert cmd == "tmux send-keys -t %30 Enter"

    def test_option_2_downs_then_enter(self, mock_run):
        tmux_send.select_option("%30", 2)
        cmd = _captured_bash_cmd(mock_run)
        assert "tmux send-keys -t %30 Down" in cmd
        assert "sleep 0.1" in cmd
        assert cmd.endswith("Enter")

    def test_option_3_two_downs(self, mock_run):
        tmux_send.select_option("%30", 3)
        cmd = _captured_bash_cmd(mock_run)
        assert "Down Down" in cmd
        assert cmd.endswith("Enter")


@patch.object(tmux_send.subprocess, "run")
class TestSubmitText:
    def test_type_settle_enter_sequence(self, mock_run):
        tmux_send.submit_text("%30", "hello")
        cmd = _captured_bash_cmd(mock_run)
        # All three pieces in one bash invocation
        assert "-l hello" in cmd  # simple word — no quoting needed
        assert "sleep 0.3" in cmd
        assert cmd.endswith("Enter")
        # In correct order: type → sleep → Enter
        type_idx = cmd.index("-l hello")
        sleep_idx = cmd.index("sleep 0.3")
        enter_idx = cmd.rindex("Enter")
        assert type_idx < sleep_idx < enter_idx

    def test_custom_settle(self, mock_run):
        """settle=0.5 should appear in the sleep (used for image albums)."""
        tmux_send.submit_text("%30", "hi", settle=0.5)
        cmd = _captured_bash_cmd(mock_run)
        assert "sleep 0.5" in cmd

    def test_newlines_replaced(self, mock_run):
        tmux_send.submit_text("%30", "a\nb")
        cmd = _captured_bash_cmd(mock_run)
        # 'a b' has a space so shlex.quote will wrap it in single quotes
        assert "'a b'" in cmd


@patch.object(tmux_send.subprocess, "run")
class TestInjectBusy:
    def test_full_sequence(self, mock_run):
        """Escape → 0.3 → type → 0.1 → Enter, in a single bash invocation."""
        tmux_send.inject_busy("%30", "focus on X")
        cmd = _captured_bash_cmd(mock_run)
        assert "Escape" in cmd
        assert "sleep 0.3" in cmd
        assert "-l 'focus on X'" in cmd
        assert "sleep 0.1" in cmd
        assert cmd.endswith("Enter")
        # Order: Escape → sleep 0.3 → type → sleep 0.1 → Enter
        esc_idx = cmd.index("Escape")
        sleep_long = cmd.index("sleep 0.3")
        type_idx = cmd.index("-l 'focus on X'")
        sleep_short = cmd.index("sleep 0.1")
        enter_idx = cmd.rindex("Enter")
        assert esc_idx < sleep_long < type_idx < sleep_short < enter_idx

    def test_one_subprocess_call(self, mock_run):
        tmux_send.inject_busy("%30", "msg")
        assert mock_run.call_count == 1


@patch.object(tmux_send.subprocess, "run")
class TestNavigateThenSubmit:
    def test_basic(self, mock_run):
        tmux_send.navigate_then_submit("%30", 2, "answer text")
        cmd = _captured_bash_cmd(mock_run)
        # Down Down → 0.2s settle → type → 0.1s → Enter, all in one call
        assert "Down Down" in cmd
        assert "sleep 0.2" in cmd
        assert "-l 'answer text'" in cmd
        assert "sleep 0.1" in cmd
        assert cmd.rstrip().endswith("Enter")
        assert mock_run.call_count == 1

    def test_zero_downs_just_types(self, mock_run):
        """down_count=0 means: just type + Enter (no navigation)."""
        tmux_send.navigate_then_submit("%30", 0, "answer")
        cmd = _captured_bash_cmd(mock_run)
        assert "Down" not in cmd
        assert "-l answer" in cmd
        assert cmd.rstrip().endswith("Enter")

    def test_newlines_stripped(self, mock_run):
        tmux_send.navigate_then_submit("%30", 1, "a\nb")
        cmd = _captured_bash_cmd(mock_run)
        assert "'a b'" in cmd


@patch.object(tmux_send.subprocess, "run")
class TestClearTyped:
    def test_sends_escape(self, mock_run):
        tmux_send.clear_typed("%30")
        cmd = _captured_bash_cmd(mock_run)
        assert cmd == "tmux send-keys -t %30 Escape"


@patch.object(tmux_send.subprocess, "run")
class TestInterrupt:
    def test_escape_then_ctrl_u(self, mock_run):
        tmux_send.interrupt("%30")
        cmd = _captured_bash_cmd(mock_run)
        assert "Escape" in cmd
        assert "sleep 0.1" in cmd
        assert "C-u" in cmd
        # Escape first, then C-u
        assert cmd.index("Escape") < cmd.index("C-u")


def test_module_exposes_expected_api():
    """Sanity check that all planned API functions exist."""
    for name in ("type_text", "press_key", "press_keys", "select_option",
                 "submit_text", "inject_busy", "navigate_then_submit",
                 "clear_typed", "interrupt"):
        assert hasattr(tmux_send, name), f"tmux_send missing {name}"
