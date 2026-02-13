#!/usr/bin/env python
"""Tests for tg-hook — validates formatting, routing, and content cleaning."""
import json
import os
import re
import sys
import textwrap
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import tg_hook as tg


class TestMarkdownSafety(unittest.TestCase):
    """Verify messages with underscores don't break Telegram Markdown V1."""

    def _send_and_capture(self, send_fn):
        """Call send_fn, return the text that would be sent to Telegram."""
        with patch.object(tg, "tg_send") as mock_send:
            mock_send.return_value = 1
            send_fn(mock_send)
            return mock_send.call_args[0][0]

    def test_sessions_message_underscore_project(self):
        sessions = {"1": ("0:1.0", "my_project"), "2": ("0:2.0", "another_test_proj")}
        msg = tg.format_sessions_message(sessions)
        # Project names must be inside backticks
        self.assertIn("`my_project`", msg)
        self.assertIn("`another_test_proj`", msg)
        # No bare underscores outside backticks
        self._assert_no_bare_underscores(msg)

    def test_stop_message_underscore_project(self):
        """Stop message wraps project in backticks and content in pre block."""
        msg = f"✅ w1 Claude Code (`my_project`) finished:\n\n```\nsome output with var_name = 1\n```"
        self._assert_no_bare_underscores(msg)

    def test_permission_header_underscore_file(self):
        """Permission header wraps filename in backticks."""
        # Simulate _extract_pane_permission header output
        lines = ["● Update(scripts/test_hook.py)", "  ⎿  some content"]
        m = re.match(r'^● (\w+)\((.+?)\)', lines[0].strip())
        header = f"wants to {m.group(1).lower()} `{m.group(2)}`"
        self.assertIn("`scripts/test_hook.py`", header)
        self._assert_no_bare_underscores(header)

    def test_permission_bash_message(self):
        msg = f"🔧 w1 Claude Code (`my_proj`) needs permission:\n\n```\nrm /tmp/test_file.txt\n```\n1. Yes"
        self._assert_no_bare_underscores(msg)

    def test_permission_edit_message(self):
        msg = f"🔧 w1 Claude Code (`my_proj`) wants to update `scripts/my_file.py`:\n\n```\n+new_line = True\n```\n1. Yes"
        self._assert_no_bare_underscores(msg)

    def test_permission_no_content(self):
        """WebFetch-style permission with no content body."""
        msg = f"🔧 w1 Claude Code (`proj`) wants to fetch `https://example.com`:\n1. Yes"
        self._assert_no_bare_underscores(msg)

    def test_route_confirm_messages(self):
        msgs = [
            f"📨 Selected option 1 in `w4`",
            f"📨 Answered in `w4`:\n`hello world`",
            f"📨 Allowed in `w4`",
            f"📨 Denied in `w4`",
            f"📨 Sent to `w4`:\n`some text with under_scores`",
        ]
        for msg in msgs:
            self._assert_no_bare_underscores(msg)

    def test_status_message_underscore_project(self):
        msg = f"📋 `w1` — `my_project`:\n\n```\nsome_var = 1\n```"
        self._assert_no_bare_underscores(msg)

    def test_question_message(self):
        msg = f"❓ w1 Claude Code (`my_project`) asks:\nWhat to do?"
        self._assert_no_bare_underscores(msg)

    def test_error_messages(self):
        msgs = [
            f"⚠️ No session `w1`.",
            f"⚠️ No Claude session at `w3`.",
            f"⚠️ No Claude sessions found. Send `/sessions` to rescan.",
            f"⚠️ Multiple sessions — prefix with `wN`.",
        ]
        for msg in msgs:
            self._assert_no_bare_underscores(msg)

    def test_pause_messages(self):
        msgs = [
            f"⏸ Paused. Send `/start` to resume or `/quit` to exit.",
            f"⏸ Paused. Send `/start` to resume.",
        ]
        for msg in msgs:
            self._assert_no_bare_underscores(msg)

    def _assert_no_bare_underscores(self, text):
        """Assert no underscores appear outside backtick-protected regions.

        Strips content inside `...` and ```...``` blocks, then checks
        remaining text has no underscores.
        """
        # Remove pre blocks
        stripped = re.sub(r'```.*?```', '', text, flags=re.DOTALL)
        # Remove inline code
        stripped = re.sub(r'`[^`]+`', '', stripped)
        if '_' in stripped:
            self.fail(f"Bare underscore outside backticks in:\n{text}\n\nRemaining after stripping code: {stripped}")


class TestFilterNoise(unittest.TestCase):
    """Test _filter_noise removes UI chrome."""

    def test_removes_separators(self):
        raw = "hello\n────────────\nworld"
        result = tg._filter_noise(raw)
        self.assertEqual(result, ["hello", "world"])

    def test_removes_working_indicator(self):
        raw = "hello\n⏳ Working...\nworld"
        result = tg._filter_noise(raw)
        self.assertEqual(result, ["hello", "world"])

    def test_removes_accept_edits_line(self):
        raw = "hello\n⏵⏵ accept edits on\nworld"
        result = tg._filter_noise(raw)
        self.assertEqual(result, ["hello", "world"])

    def test_removes_context_line(self):
        raw = "hello\nContext left until auto-compact: 50%\nworld"
        result = tg._filter_noise(raw)
        self.assertEqual(result, ["hello", "world"])

    def test_removes_shortcut_hint(self):
        raw = "hello\n✻ esc for shortcuts\nworld"
        result = tg._filter_noise(raw)
        self.assertEqual(result, ["hello", "world"])

    def test_strips_trailing_blanks(self):
        raw = "hello\n\n\n"
        result = tg._filter_noise(raw)
        self.assertEqual(result, ["hello"])

    def test_keeps_normal_content(self):
        raw = "line one\nline two\nline three"
        result = tg._filter_noise(raw)
        self.assertEqual(result, ["line one", "line two", "line three"])


class TestCleanPaneContent(unittest.TestCase):
    """Test clean_pane_content for stop events."""

    def test_stop_extracts_between_bullet_and_prompt(self):
        raw = textwrap.dedent("""\
            ● Some previous tool call
              old stuff
            ● Here is the response
              This is the actual reply.
              It has multiple lines.
            ❯ next prompt here
        """)
        result = tg.clean_pane_content(raw, "stop")
        self.assertIn("Here is the response", result)
        self.assertIn("actual reply", result)
        self.assertNotIn("next prompt", result)
        self.assertNotIn("previous tool call", result)

    def test_stop_skips_tool_bullets(self):
        """● Bash(...) should not be treated as a text bullet."""
        raw = textwrap.dedent("""\
            ● Bash(echo hello)
              ⎿  hello
            ● The answer is 42.
            ❯ prompt
        """)
        result = tg.clean_pane_content(raw, "stop")
        self.assertIn("The answer is 42", result)
        self.assertNotIn("Bash(echo", result)

    def test_non_stop_event_returns_all(self):
        raw = "line 1\nline 2\nline 3"
        result = tg.clean_pane_content(raw, "notification")
        self.assertIn("line 1", result)
        self.assertIn("line 3", result)


class TestExtractPanePermission(unittest.TestCase):
    """Test _extract_pane_permission with mocked tmux."""

    def _mock_pane(self, content):
        """Create a mock that returns content from capture-pane."""
        mock_result = MagicMock()
        mock_result.stdout = content
        return mock_result

    @patch("subprocess.run")
    def test_edit_permission(self, mock_run):
        pane_content = textwrap.dedent("""\
            ● Update(scripts/test_file.py)
              ⎿  Edit file
                 scripts/test_file.py
              1 +new_line = True
              2  old_line = False
              ❯ 1. Yes
                2. Yes, and don't ask again for this file
                3. No, and tell Claude what to do differently (esc)
        """)
        mock_run.return_value = self._mock_pane(pane_content)
        header, content, options = tg._extract_pane_permission("test_pane")

        self.assertIn("update", header)
        self.assertIn("`scripts/test_file.py`", header)
        self.assertIn("+new_line = True", content)
        self.assertEqual(len(options), 3)
        self.assertTrue(options[0].startswith("1."))

    @patch("subprocess.run")
    def test_bash_permission(self, mock_run):
        pane_content = textwrap.dedent("""\
            ● Bash(rm /tmp/test_file.txt)
              ⎿  Bash command
                 rm /tmp/test_file.txt
              ❯ 1. Yes
                2. Yes, and don't ask again for this command
                3. No (esc)
        """)
        mock_run.return_value = self._mock_pane(pane_content)
        header, content, options = tg._extract_pane_permission("test_pane")

        self.assertEqual(len(options), 3)

    @patch("subprocess.run")
    def test_webfetch_permission(self, mock_run):
        pane_content = textwrap.dedent("""\
            ● Fetch(https://example.com)
              ⎿  Fetch
                 https://example.com
              ❯ 1. Yes
                2. Yes, and don't ask again for example.com
                3. No (esc)
        """)
        mock_run.return_value = self._mock_pane(pane_content)
        header, content, options = tg._extract_pane_permission("test_pane")

        self.assertIn("fetch", header)
        self.assertIn("`https://example.com`", header)
        self.assertEqual(len(options), 3)

    @patch("subprocess.run")
    def test_no_options(self, mock_run):
        mock_run.return_value = self._mock_pane("some random content\nno options here")
        header, content, options = tg._extract_pane_permission("test_pane")
        self.assertEqual(options, [])

    @patch("subprocess.run")
    def test_chrome_filtered(self, mock_run):
        pane_content = textwrap.dedent("""\
            ● Update(scripts/hook.py)
              ⎿  Edit file
                 scripts/hook.py
                 hook.py
              ────────────
              1 +new = True
              Do you want to proceed?
              ❯ 1. Yes
                2. No (esc)
        """)
        mock_run.return_value = self._mock_pane(pane_content)
        header, content, options = tg._extract_pane_permission("test_pane")

        self.assertNotIn("Edit file", content)
        self.assertNotIn("hook.py", content)  # standalone filename filtered
        self.assertNotIn("────", content)
        self.assertNotIn("Do you want", content)
        self.assertIn("+new = True", content)


class TestRouteToPane(unittest.TestCase):
    """Test route_to_pane logic with mocked tmux."""

    def setUp(self):
        self.pane = "0:4.0"
        self.win_idx = "4"

    @patch("subprocess.run")
    def test_normal_message(self, mock_run):
        """No active prompt — sends text + Enter."""
        with patch.object(tg, "load_active_prompt", return_value=None):
            result = tg.route_to_pane(self.pane, self.win_idx, "hello")
        self.assertIn("Sent to", result)
        self.assertIn("`w4`", result)
        # Should call bash -c with send-keys
        mock_run.assert_called_once()
        cmd = mock_run.call_args[0][0]
        self.assertEqual(cmd[0], "bash")

    @patch("subprocess.run")
    def test_permission_allow(self, mock_run):
        """Permission prompt — 'y' sends Enter (option 1)."""
        prompt = {"pane": "%20", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "yes": 1, "allow": 1, "n": 3, "no": 3, "deny": 3}}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "y")
        self.assertIn("Selected option 1", result)
        cmd_str = mock_run.call_args[0][0][2]  # bash -c "..."
        self.assertIn("Enter", cmd_str)
        self.assertNotIn("Down", cmd_str)  # option 1, no Down needed

    @patch("subprocess.run")
    def test_permission_deny(self, mock_run):
        """Permission prompt — 'n' navigates to last option."""
        prompt = {"pane": "%20", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "yes": 1, "allow": 1, "n": 3, "no": 3, "deny": 3}}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "n")
        self.assertIn("Selected option 3", result)
        cmd_str = mock_run.call_args[0][0][2]
        self.assertEqual(cmd_str.count("Down"), 2)  # n=3, so 2 Downs

    @patch("subprocess.run")
    def test_numbered_selection(self, mock_run):
        """Digit reply navigates with Down keys."""
        prompt = {"pane": "%20", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "n": 3}}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "2")
        self.assertIn("Selected option 2", result)
        cmd_str = mock_run.call_args[0][0][2]
        self.assertEqual(cmd_str.count("Down"), 1)  # 1 Down for option 2
        self.assertIn("sleep 0.1", cmd_str)
        self.assertIn("Enter", cmd_str)

    @patch("subprocess.run")
    def test_question_free_text(self, mock_run):
        """Free text on question prompt — navigates to Other, types, Enter."""
        prompt = {"pane": "%20", "total": 4, "ts": 0, "free_text_at": 2}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "my custom answer")
        self.assertIn("Answered", result)
        self.assertIn("`my custom answer`", result)
        cmd_str = mock_run.call_args[0][0][2]
        self.assertEqual(cmd_str.count("Down"), 2)  # 2 Downs to reach Other
        self.assertIn("sleep 0.1", cmd_str)
        self.assertIn("my custom answer", cmd_str)
        self.assertIn("Enter", cmd_str)
        # No Enter between Down and text (the bug we fixed)
        down_pos = cmd_str.rfind("Down")
        enter_pos = cmd_str.find("Enter")
        text_pos = cmd_str.find("my custom answer")
        self.assertGreater(text_pos, down_pos, "Text should come after Downs")
        # First Enter should be AFTER text, not between Down and text
        self.assertGreater(enter_pos, text_pos, "Enter should come after text, not between Down and text")

    @patch("subprocess.run")
    def test_question_numbered(self, mock_run):
        """Digit reply on question selects that option."""
        prompt = {"pane": "%20", "total": 4, "ts": 0, "free_text_at": 2}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "1")
        self.assertIn("Selected option 1", result)

    @patch("subprocess.run")
    def test_question_extra_options(self, mock_run):
        """Question allows selecting n+1 (Type answer) and n+2 (Chat)."""
        prompt = {"pane": "%20", "total": 4, "ts": 0, "free_text_at": 2}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "4")
        self.assertIn("Selected option 4", result)  # n+2 = 4

    @patch("subprocess.run")
    def test_unknown_text_defaults_to_option_1(self, mock_run):
        """Prompt with no free_text and no matching shortcut defaults to option 1."""
        prompt = {"pane": "%20", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "n": 3}}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "whatever")
        self.assertIn("Selected option 1", result)

    @patch("subprocess.run")
    def test_message_underscore_safe(self, mock_run):
        """Route confirmation with underscored text is Markdown-safe."""
        with patch.object(tg, "load_active_prompt", return_value=None):
            result = tg.route_to_pane(self.pane, self.win_idx, "fix my_var_name")
        # Text should be in backticks
        self.assertIn("`fix my_var_name`", result)


class TestProcessSignals(unittest.TestCase):
    """Test signal processing with mocked filesystem and Telegram."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_signals"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)
        return fname

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="test_project")
    @patch("subprocess.run")
    @patch("time.sleep")
    def test_stop_signal(self, mock_sleep, mock_run, mock_proj, mock_send):
        self._write_signal("stop")
        mock_result = MagicMock()
        mock_result.stdout = "● Here is the answer\n  The result is 42.\n❯ prompt"
        mock_run.return_value = mock_result

        tg.process_signals()

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("finished", msg)
        self.assertIn("`test_project`", msg)
        self.assertIn("```", msg)  # content in pre block

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="test_proj")
    @patch.object(tg, "_extract_pane_permission", return_value=("wants to update `test.py`", "+new=True", ["1. Yes", "2. No"]))
    @patch.object(tg, "save_active_prompt")
    def test_permission_signal_non_bash(self, mock_save, mock_extract, mock_proj, mock_send):
        self._write_signal("permission", cmd="", message="Claude needs permission to use Update")

        tg.process_signals()

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("wants to update", msg)
        self.assertIn("```", msg)
        self.assertIn("1. Yes", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="test_proj")
    @patch.object(tg, "_extract_pane_permission", return_value=("", "", ["1. Yes", "2. No"]))
    @patch.object(tg, "save_active_prompt")
    def test_permission_signal_bash(self, mock_save, mock_extract, mock_proj, mock_send):
        self._write_signal("permission", cmd="rm /tmp/test_file.txt", message="Claude needs permission")

        tg.process_signals()

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("needs permission", msg)
        self.assertIn("rm /tmp/test_file.txt", msg)
        self.assertIn("```", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="proj")
    @patch.object(tg, "_extract_pane_permission", return_value=("wants to fetch `https://example.com`", "", ["1. Yes", "2. No"]))
    @patch.object(tg, "save_active_prompt")
    def test_permission_no_content(self, mock_save, mock_extract, mock_proj, mock_send):
        """WebFetch with no content body should not have empty pre block."""
        self._write_signal("permission", cmd="", message="Claude needs permission")

        tg.process_signals()

        msg = mock_send.call_args[0][0]
        self.assertIn("wants to fetch", msg)
        self.assertNotIn("```\n\n```", msg)  # no empty pre block
        self.assertIn("1. Yes", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="proj")
    @patch.object(tg, "save_active_prompt")
    def test_question_signal(self, mock_save, mock_proj, mock_send):
        questions = [{"question": "Pick one?", "options": [
            {"label": "A", "description": "first"},
            {"label": "B", "description": "second"},
        ]}]
        self._write_signal("question", questions=questions)

        tg.process_signals()

        msg = mock_send.call_args[0][0]
        self.assertIn("asks", msg)
        self.assertIn("Pick one?", msg)
        self.assertIn("1. A", msg)
        self.assertIn("2. B", msg)
        self.assertIn("3. Type your answer", msg)
        self.assertIn("4. Chat about this", msg)
        mock_save.assert_called_once_with("w4", "%20", total=4, free_text_at=2)

    def test_skips_underscore_files(self):
        """Signal processing should skip _prefixed state files."""
        state_path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        with open(state_path, "w") as f:
            json.dump({"type": "test"}, f)

        with patch.object(tg, "tg_send"):
            tg.process_signals()

        # State file should still exist (not deleted)
        self.assertTrue(os.path.exists(state_path))

    def test_cleans_processed_signals(self):
        """Processed signal files should be deleted."""
        self._write_signal("stop")
        with patch.object(tg, "tg_send", return_value=1), \
             patch.object(tg, "get_pane_project", return_value="p"), \
             patch("subprocess.run", return_value=MagicMock(stdout="")), \
             patch("time.sleep"):
            tg.process_signals()
        # Only state files should remain
        remaining = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(remaining, [])


class TestCmdHook(unittest.TestCase):
    """Test hook command signal writing."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_signals_hook"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir
        self._orig_enabled = tg.TG_HOOKS_ENABLED
        tg.TG_HOOKS_ENABLED = True

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        tg.TG_HOOKS_ENABLED = self._orig_enabled
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg, "get_window_id", return_value="w4")
    @patch("sys.stdin")
    def test_bash_pretooluse_saves_cmd(self, mock_stdin, mock_wid):
        data = {"hook_event_name": "PreToolUse", "tool_name": "Bash",
                "tool_input": {"command": "echo hello"}, "cwd": "/tmp/test"}
        mock_stdin.read.return_value = json.dumps(data)
        os.environ["TMUX_PANE"] = "%20"

        tg.cmd_hook()

        cmd_file = os.path.join(self.signal_dir, "_bash_cmd_w4.json")
        self.assertTrue(os.path.exists(cmd_file))
        with open(cmd_file) as f:
            self.assertEqual(json.load(f)["cmd"], "echo hello")

    @patch.object(tg, "get_window_id", return_value="w4")
    @patch("sys.stdin")
    def test_permission_reads_bash_cmd_only_for_bash(self, mock_stdin, mock_wid):
        """Permission notification only reads _bash_cmd if message mentions bash."""
        # Pre-create a bash cmd file
        cmd_file = os.path.join(self.signal_dir, "_bash_cmd_w4.json")
        with open(cmd_file, "w") as f:
            json.dump({"cmd": "echo hello"}, f)

        # Non-bash permission should NOT consume it
        data = {"hook_event_name": "Notification", "notification_type": "permission_prompt",
                "message": "Claude needs permission to use Update", "cwd": "/tmp/test"}
        mock_stdin.read.return_value = json.dumps(data)
        os.environ["TMUX_PANE"] = "%20"

        tg.cmd_hook()

        # Bash cmd file should still exist
        self.assertTrue(os.path.exists(cmd_file))

        # Verify signal was written without cmd
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(len(signals), 1)
        with open(os.path.join(self.signal_dir, signals[0])) as f:
            signal = json.load(f)
        self.assertEqual(signal["cmd"], "")


import time  # needed for _write_signal


class TestComputeNewLines(unittest.TestCase):
    """Test _compute_new_lines diff algorithm."""

    def test_empty_old_returns_all_new(self):
        result = tg._compute_new_lines([], ["a", "b", "c"])
        self.assertEqual(result, ["a", "b", "c"])

    def test_identical_returns_empty(self):
        lines = ["a", "b", "c"]
        result = tg._compute_new_lines(lines, lines[:])
        self.assertEqual(result, [])

    def test_scroll_down_overlap(self):
        old = ["a", "b", "c", "d", "e"]
        new = ["c", "d", "e", "f", "g"]
        result = tg._compute_new_lines(old, new)
        self.assertEqual(result, ["f", "g"])

    def test_single_line_scroll(self):
        old = ["a", "b", "c", "d", "e"]
        new = ["b", "c", "d", "e", "f"]
        result = tg._compute_new_lines(old, new)
        self.assertEqual(result, ["f"])

    def test_in_place_change_skipped(self):
        """Lines that changed in place (e.g. timers) are not reported as new."""
        old = ["a", "b", "progress 62%", "c", "d"]
        new = ["a", "b", "progress 88%", "c", "d"]
        result = tg._compute_new_lines(old, new)
        self.assertEqual(result, [])

    def test_scroll_with_in_place_change(self):
        """Scrolling + in-place change: only inserted lines returned."""
        old = ["a", "b", "progress 62%", "c", "d"]
        new = ["b", "progress 88%", "c", "d", "e"]
        result = tg._compute_new_lines(old, new)
        self.assertEqual(result, ["e"])

    def test_complete_change_returns_all(self):
        """No overlap (content scrolled past window) returns all new lines."""
        old = ["a", "b"]
        new = ["x", "y", "z"]
        result = tg._compute_new_lines(old, new)
        self.assertEqual(result, ["x", "y", "z"])


class TestJoinWrappedLines(unittest.TestCase):
    """Test _join_wrapped_lines for Claude Code terminal wrapping."""

    def test_no_wrapping(self):
        lines = ["short line", "another short"]
        result = tg._join_wrapped_lines(lines, 80)
        self.assertEqual(result, ["short line", "another short"])

    def test_joins_continuation(self):
        # Line at width 80, followed by indented continuation
        lines = ["x" * 78, "  continued text"]
        result = tg._join_wrapped_lines(lines, 80)
        self.assertEqual(result, ["x" * 78 + " continued text"])

    def test_preserves_bullet_after_long_line(self):
        lines = ["x" * 78, "● New bullet point"]
        result = tg._join_wrapped_lines(lines, 80)
        self.assertEqual(result, ["x" * 78, "● New bullet point"])

    def test_preserves_numbered_item(self):
        lines = ["x" * 78, "  2. Second item"]
        result = tg._join_wrapped_lines(lines, 80)
        self.assertEqual(result, ["x" * 78, "  2. Second item"])

    def test_chains_multiple_wraps(self):
        lines = ["x" * 78, "  " + "y" * 76, "  final part"]
        result = tg._join_wrapped_lines(lines, 80)
        self.assertEqual(result, ["x" * 78 + " " + "y" * 76 + " final part"])

    def test_skips_when_width_unknown(self):
        lines = ["x" * 78, "  continued"]
        result = tg._join_wrapped_lines(lines, 0)
        self.assertEqual(result, lines)


class TestFocusState(unittest.TestCase):
    """Test focus state file operations."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_focus"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        tg._save_focus_state("4", "0:4.0", "myproj")
        state = tg._load_focus_state()
        self.assertEqual(state, {"wid": "4", "pane": "0:4.0", "project": "myproj"})

    def test_load_missing_returns_none(self):
        self.assertIsNone(tg._load_focus_state())

    def test_clear_removes_file(self):
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._clear_focus_state()
        self.assertIsNone(tg._load_focus_state())

    def test_survives_clear_signals_without_state(self):
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._clear_signals(include_state=False)
        self.assertIsNotNone(tg._load_focus_state())

    def test_cleared_by_clear_signals_with_state(self):
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._clear_signals(include_state=True)
        self.assertIsNone(tg._load_focus_state())


if __name__ == "__main__":
    unittest.main()
