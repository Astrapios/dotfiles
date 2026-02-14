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


class TestHasResponseStart(unittest.TestCase):
    """Test _has_response_start for progressive capture."""

    def test_found_text_bullet(self):
        raw = "● Here is the answer\n  result\n❯ prompt"
        self.assertTrue(tg._has_response_start(raw))

    def test_only_tool_bullet(self):
        """Tool call bullets don't count as response start."""
        raw = "● Bash(echo hi)\n  ⎿  hi\n❯ prompt"
        self.assertFalse(tg._has_response_start(raw))

    def test_no_bullet_at_all(self):
        """Long response cut off — no bullet visible."""
        raw = "  line 5\n  line 6\n  line 7\n❯ prompt"
        self.assertFalse(tg._has_response_start(raw))

    def test_bullet_before_prompt(self):
        raw = "old stuff\n● The answer is 42.\n  details\n❯ prompt"
        self.assertTrue(tg._has_response_start(raw))


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
        self.signal_dir = "/tmp/tg_hook_test_route"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

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
        """Free text on question prompt — navigate to Type something, type, Enter."""
        prompt = {"pane": "%20", "total": 4, "ts": 0, "free_text_at": 2}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "my custom answer")
        self.assertIn("Answered", result)
        self.assertIn("`my custom answer`", result)
        cmd_str = mock_run.call_args[0][0][2]
        self.assertEqual(cmd_str.count("Down"), 2)
        self.assertIn("my custom answer", cmd_str)
        # Sequence: Down×2 → type text → Enter (submit)
        self.assertEqual(cmd_str.count("Enter"), 1)
        down_pos = cmd_str.rfind("Down")
        text_pos = cmd_str.find("my custom answer")
        enter_pos = cmd_str.find("Enter")
        self.assertGreater(text_pos, down_pos, "Text after Downs")
        self.assertGreater(enter_pos, text_pos, "Enter after text")

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
    def test_unknown_text_navigates_and_types(self, mock_run):
        """Prompt with no free_text: navigate to last option, type text, Enter."""
        prompt = {"pane": "%20", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "n": 3}}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "change step 3")
        self.assertIn("Replied", result)
        self.assertIn("`change step 3`", result)
        cmd_str = mock_run.call_args[0][0][2]
        # Navigate to last option, type text, Enter
        self.assertEqual(cmd_str.count("Down"), 2)  # total=3, so 2 Downs
        self.assertIn("change step 3", cmd_str)
        self.assertEqual(cmd_str.count("Enter"), 1)  # submit only

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
        mock_save.assert_called_once_with("w4", "%20", total=4, free_text_at=2,
                                                 remaining_qs=None, project="proj")

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
        # Line near width 80 (within margin of 15), followed by indented continuation
        lines = ["x" * 68, "  continued text"]
        result = tg._join_wrapped_lines(lines, 80)
        self.assertEqual(result, ["x" * 68 + " continued text"])

    def test_preserves_bullet_after_long_line(self):
        lines = ["x" * 68, "● New bullet point"]
        result = tg._join_wrapped_lines(lines, 80)
        self.assertEqual(result, ["x" * 68, "● New bullet point"])

    def test_preserves_numbered_item(self):
        lines = ["x" * 68, "  2. Second item"]
        result = tg._join_wrapped_lines(lines, 80)
        self.assertEqual(result, ["x" * 68, "  2. Second item"])

    def test_chains_multiple_wraps(self):
        lines = ["x" * 68, "  " + "y" * 66, "  final part"]
        result = tg._join_wrapped_lines(lines, 80)
        self.assertEqual(result, ["x" * 68 + " " + "y" * 66 + " final part"])

    def test_skips_when_width_unknown(self):
        lines = ["x" * 78, "  continued"]
        result = tg._join_wrapped_lines(lines, 0)
        self.assertEqual(result, lines)


class TestExtractChatMessages(unittest.TestCase):
    """Test _extract_chat_messages with text, photo, and caption messages."""

    def _make_update(self, msg_fields):
        return {"result": [{"update_id": 1, "message": {"chat": {"id": int(tg.CHAT_ID)}, **msg_fields}}]}

    def test_text_message(self):
        data = self._make_update({"text": "hello"})
        result = tg._extract_chat_messages(data)
        self.assertEqual(result, [{"text": "hello", "photo": None, "callback": None}])

    def test_photo_message_no_caption(self):
        data = self._make_update({"photo": [
            {"file_id": "small_id", "width": 90, "height": 90},
            {"file_id": "large_id", "width": 800, "height": 800},
        ]})
        result = tg._extract_chat_messages(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["photo"], "large_id")
        self.assertEqual(result[0]["text"], "")
        self.assertIsNone(result[0]["callback"])

    def test_photo_message_with_caption(self):
        data = self._make_update({"photo": [
            {"file_id": "small_id", "width": 90, "height": 90},
            {"file_id": "large_id", "width": 800, "height": 800},
        ], "caption": "w4 describe this"})
        result = tg._extract_chat_messages(data)
        self.assertEqual(result[0]["text"], "w4 describe this")
        self.assertEqual(result[0]["photo"], "large_id")

    def test_ignores_other_chat(self):
        data = {"result": [{"update_id": 1, "message": {
            "chat": {"id": 999999}, "text": "hello"
        }}]}
        result = tg._extract_chat_messages(data)
        self.assertEqual(result, [])

    def test_empty_message_skipped(self):
        data = self._make_update({})
        result = tg._extract_chat_messages(data)
        self.assertEqual(result, [])


class TestDownloadTgPhoto(unittest.TestCase):
    """Test _download_tg_photo helper."""

    @patch("requests.get")
    def test_successful_download(self, mock_get):
        # Mock getFile response
        get_file_resp = MagicMock()
        get_file_resp.json.return_value = {"result": {"file_path": "photos/file_1.jpg"}}
        get_file_resp.raise_for_status = MagicMock()

        # Mock file download response
        download_resp = MagicMock()
        download_resp.content = b"\xff\xd8\xff\xe0fake_jpeg"
        download_resp.raise_for_status = MagicMock()

        mock_get.side_effect = [get_file_resp, download_resp]

        dest = "/tmp/tg_hook_test_photo.jpg"
        result = tg._download_tg_photo("test_file_id", dest)
        self.assertEqual(result, dest)
        self.assertTrue(os.path.exists(dest))
        with open(dest, "rb") as f:
            self.assertEqual(f.read(), b"\xff\xd8\xff\xe0fake_jpeg")
        os.remove(dest)

    @patch("requests.get", side_effect=Exception("network error"))
    def test_download_failure_returns_none(self, mock_get):
        result = tg._download_tg_photo("bad_id", "/tmp/tg_hook_test_fail.jpg")
        self.assertIsNone(result)


class TestTgSendPhoto(unittest.TestCase):
    """Test tg_send_photo function."""

    @patch("requests.post")
    def test_send_photo_success(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"result": {"message_id": 42}}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        # Create a temp file to send
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"fake png data")
            path = f.name

        try:
            msg_id = tg.tg_send_photo(path, "test caption")
            self.assertEqual(msg_id, 42)
            call_kwargs = mock_post.call_args
            self.assertIn("sendPhoto", call_kwargs[0][0])
            self.assertIn("photo", call_kwargs[1]["files"])
            self.assertEqual(call_kwargs[1]["data"]["caption"], "test caption")
            self.assertEqual(call_kwargs[1]["data"]["parse_mode"], "Markdown")
        finally:
            os.remove(path)

    @patch("requests.post")
    def test_send_photo_no_caption(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"result": {"message_id": 43}}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"fake jpg data")
            path = f.name

        try:
            tg.tg_send_photo(path)
            call_kwargs = mock_post.call_args
            self.assertNotIn("caption", call_kwargs[1]["data"])
            self.assertNotIn("parse_mode", call_kwargs[1]["data"])
        finally:
            os.remove(path)

    @patch("requests.post")
    def test_send_photo_markdown_fallback(self, mock_post):
        """On 400, retries without parse_mode."""
        fail_resp = MagicMock()
        fail_resp.status_code = 400

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"result": {"message_id": 44}}
        ok_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [fail_resp, ok_resp]

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"data")
            path = f.name

        try:
            msg_id = tg.tg_send_photo(path, "caption_with_bad_markdown")
            self.assertEqual(msg_id, 44)
            # Second call should not have parse_mode
            second_call = mock_post.call_args_list[1]
            self.assertNotIn("parse_mode", second_call[1]["data"])
        finally:
            os.remove(path)


class TestPaneHasPrompt(unittest.TestCase):
    """Test _pane_has_prompt detects numbered option dialogs."""

    @patch("subprocess.run")
    def test_detects_numbered_options(self, mock_run):
        mock_run.return_value = MagicMock(stdout=(
            "● Bash(echo hi)\n"
            "  ⎿  Bash command\n"
            "❯ 1. Yes\n"
            "  2. Yes, and don't ask again\n"
            "  3. No (esc)\n"
        ))
        self.assertTrue(tg._pane_has_prompt("0:4.0"))

    @patch("subprocess.run")
    def test_detects_indented_options_without_cursor(self, mock_run):
        """Options without ❯ prefix (e.g. non-selected items)."""
        mock_run.return_value = MagicMock(stdout=(
            "● Update(test.py)\n"
            "  ⎿  Edit file\n"
            "  1. Yes\n"
            "  2. No (esc)\n"
        ))
        self.assertTrue(tg._pane_has_prompt("0:4.0"))

    @patch("subprocess.run")
    def test_no_options(self, mock_run):
        mock_run.return_value = MagicMock(stdout=(
            "● Here is the answer\n"
            "  The result is 42.\n"
            "❯ prompt\n"
        ))
        self.assertFalse(tg._pane_has_prompt("0:4.0"))

    @patch("subprocess.run")
    def test_empty_pane(self, mock_run):
        mock_run.return_value = MagicMock(stdout="")
        self.assertFalse(tg._pane_has_prompt("0:4.0"))

    @patch("subprocess.run", side_effect=Exception("tmux error"))
    def test_exception_returns_false(self, mock_run):
        self.assertFalse(tg._pane_has_prompt("0:4.0"))

    @patch("subprocess.run")
    def test_numbered_list_in_response_is_false_positive(self, mock_run):
        """A numbered list in Claude's response will match — known limitation.

        This documents the behavior rather than asserting it 'should' be false.
        The cost of false positives is low (prompt state kept a bit longer).
        """
        mock_run.return_value = MagicMock(stdout=(
            "Here are the steps:\n"
            "  1. Install dependencies\n"
            "  2. Run the tests\n"
            "  3. Deploy\n"
            "❯ prompt\n"
        ))
        # This IS a false positive — numbered content looks like options
        self.assertTrue(tg._pane_has_prompt("0:4.0"))


class TestCleanupStalePrompts(unittest.TestCase):
    """Test _cleanup_stale_prompts removes prompts whose pane no longer shows dialog."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_cleanup"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg, "_pane_has_prompt", return_value=False)
    def test_removes_stale_prompt(self, mock_has):
        path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        with open(path, "w") as f:
            json.dump({"pane": "0:4.0", "total": 3}, f)
        tg._cleanup_stale_prompts()
        self.assertFalse(os.path.exists(path))

    @patch.object(tg, "_pane_has_prompt", return_value=True)
    def test_keeps_active_prompt(self, mock_has):
        path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        with open(path, "w") as f:
            json.dump({"pane": "0:4.0", "total": 3}, f)
        tg._cleanup_stale_prompts()
        self.assertTrue(os.path.exists(path))

    def test_removes_corrupt_file(self):
        path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        with open(path, "w") as f:
            f.write("not json{{{")
        tg._cleanup_stale_prompts()
        self.assertFalse(os.path.exists(path))

    @patch.object(tg, "_pane_has_prompt", return_value=False)
    def test_ignores_non_prompt_state_files(self, mock_has):
        """Should not touch _bash_cmd or _focus files."""
        bash_path = os.path.join(self.signal_dir, "_bash_cmd_w4.json")
        focus_path = os.path.join(self.signal_dir, "_focus.json")
        with open(bash_path, "w") as f:
            json.dump({"cmd": "echo"}, f)
        with open(focus_path, "w") as f:
            json.dump({"wid": "4"}, f)
        tg._cleanup_stale_prompts()
        self.assertTrue(os.path.exists(bash_path))
        self.assertTrue(os.path.exists(focus_path))

    def test_mixed_stale_and_active(self):
        """Multiple prompt files — removes only stale ones."""
        stale = os.path.join(self.signal_dir, "_active_prompt_w1.json")
        active = os.path.join(self.signal_dir, "_active_prompt_w2.json")
        with open(stale, "w") as f:
            json.dump({"pane": "0:1.0", "total": 3}, f)
        with open(active, "w") as f:
            json.dump({"pane": "0:2.0", "total": 3}, f)
        # w1 pane has no prompt, w2 pane still has prompt
        def side_effect(pane):
            return pane == "0:2.0"
        with patch.object(tg, "_pane_has_prompt", side_effect=side_effect):
            tg._cleanup_stale_prompts()
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(active))

    def test_missing_pane_key_keeps_file(self):
        """Prompt file with no pane key is kept (can't verify pane state)."""
        path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        with open(path, "w") as f:
            json.dump({"total": 3}, f)
        tg._cleanup_stale_prompts()
        # Empty pane string short-circuits — file not removed
        self.assertTrue(os.path.exists(path))

    def test_nonexistent_signal_dir(self):
        """No crash when signal dir doesn't exist."""
        tg.SIGNAL_DIR = "/tmp/tg_hook_nonexistent_dir_xyz"
        tg._cleanup_stale_prompts()  # should not raise


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


class TestSendLongMessage(unittest.TestCase):
    """Test _send_long_message chunking logic."""

    @patch.object(tg, "tg_send", return_value=1)
    def test_short_message_single_send(self, mock_send):
        """Body that fits in one message — sent as single message."""
        tg._send_long_message("header:\n", "short body", wid="4")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("header:", msg)
        self.assertIn("```\nshort body\n```", msg)

    @patch.object(tg, "tg_send", return_value=1)
    def test_long_message_chunked(self, mock_send):
        """Body exceeding TG_MAX is split into multiple messages."""
        # Create body that exceeds chunk_size
        line = "x" * 79 + "\n"  # 80 chars per line
        body = line * 100  # 8000 chars total — exceeds TG_MAX minus overhead
        tg._send_long_message("H:\n", body, wid="4")
        self.assertGreater(mock_send.call_count, 1)
        # First chunk has header + (1/N) label
        first_msg = mock_send.call_args_list[0][0][0]
        self.assertIn("H:", first_msg)
        self.assertIn("(1/", first_msg)
        # Subsequent chunks have (cont. N/N) label
        second_msg = mock_send.call_args_list[1][0][0]
        self.assertIn("(cont.", second_msg)
        # All chunks wrapped in code blocks
        for c in mock_send.call_args_list:
            self.assertIn("```", c[0][0])

    @patch.object(tg, "tg_send", return_value=1)
    def test_empty_body(self, mock_send):
        tg._send_long_message("H:\n", "", wid="4")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("```\n\n```", msg)

    @patch.object(tg, "tg_send", return_value=1)
    def test_single_long_line_no_break(self, mock_send):
        """Single line with no newlines — can't split at line boundary."""
        body = "x" * 8000
        tg._send_long_message("H:\n", body, wid="4")
        # The chunking loop puts entire line in one chunk if no newlines
        # Result: single very long message (truncated by tg_send)
        self.assertEqual(mock_send.call_count, 1)

    @patch.object(tg, "tg_send", return_value=1)
    def test_saves_last_msg(self, mock_send):
        """Verifies _last_messages is updated."""
        tg._send_long_message("H:\n", "body", wid="7")
        self.assertIn("7", tg._last_messages)
        self.assertIn("body", tg._last_messages["7"])


class TestTgSendMarkdownFallback(unittest.TestCase):
    """Test tg_send Markdown 400 fallback."""

    @patch("requests.post")
    def test_markdown_400_retries_without_parse_mode(self, mock_post):
        """On 400, retries without Markdown parse_mode."""
        fail_resp = MagicMock()
        fail_resp.status_code = 400

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"result": {"message_id": 1}}
        ok_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [fail_resp, ok_resp]

        tg.tg_send("text with _bad_ markdown")

        self.assertEqual(mock_post.call_count, 2)
        # First call has parse_mode
        first_call = mock_post.call_args_list[0]
        self.assertEqual(first_call[1]["json"]["parse_mode"], "Markdown")
        # Second call has no parse_mode
        second_call = mock_post.call_args_list[1]
        self.assertNotIn("parse_mode", second_call[1]["json"])

    @patch("requests.post")
    def test_success_on_first_try(self, mock_post):
        """200 response — no retry needed."""
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"result": {"message_id": 1}}
        ok_resp.raise_for_status = MagicMock()
        mock_post.return_value = ok_resp

        result = tg.tg_send("clean text")
        self.assertEqual(result, 1)
        mock_post.assert_called_once()


class TestLoadActivePrompt(unittest.TestCase):
    """Test load_active_prompt — no time-based expiry."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_prompt"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_load_and_remove(self):
        """Loading a prompt returns state and removes the file."""
        tg.save_active_prompt("w4", "0:4.0", total=3)
        state = tg.load_active_prompt("w4")
        self.assertIsNotNone(state)
        self.assertEqual(state["pane"], "0:4.0")
        self.assertEqual(state["total"], 3)
        # File should be gone after load
        path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        self.assertFalse(os.path.exists(path))

    def test_missing_returns_none(self):
        self.assertIsNone(tg.load_active_prompt("w99"))

    def test_old_timestamp_still_loads(self):
        """Prompt with ancient timestamp still loads — no time-based expiry."""
        path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        state = {"pane": "0:4.0", "total": 3, "ts": 1000000.0}  # year 1970
        with open(path, "w") as f:
            json.dump(state, f)
        loaded = tg.load_active_prompt("w4")
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded["total"], 3)

    def test_corrupt_file_returns_none(self):
        path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        with open(path, "w") as f:
            f.write("{corrupt")
        self.assertIsNone(tg.load_active_prompt("w4"))

    def test_save_with_all_fields(self):
        """All optional fields are persisted."""
        tg.save_active_prompt("w4", "0:4.0", total=5,
                              shortcuts={"y": 1, "n": 5},
                              free_text_at=3,
                              remaining_qs=[{"question": "Q2?"}],
                              project="myproj")
        state = tg.load_active_prompt("w4")
        self.assertEqual(state["shortcuts"], {"y": 1, "n": 5})
        self.assertEqual(state["free_text_at"], 3)
        self.assertEqual(state["remaining_qs"], [{"question": "Q2?"}])
        self.assertEqual(state["project"], "myproj")


class TestHandleCommand(unittest.TestCase):
    """Test _handle_command for new commands."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.cmd_help = "help text"

    @patch.object(tg, "tg_send", return_value=1)
    def test_help_command(self, mock_send):
        action, sessions, last = tg._handle_command(
            "/help", self.sessions, "4", self.cmd_help)
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Commands", msg)
        self.assertIn("/sessions", msg)
        self.assertIn("/status", msg)
        self.assertIn("/focus", msg)
        self.assertIn("/new", msg)
        self.assertIn("/kill", msg)
        self.assertIn("/interrupt", msg)

    @patch.object(tg, "tg_send", return_value=1)
    def test_stop_command(self, mock_send):
        action, _, _ = tg._handle_command(
            "/stop", self.sessions, "4", self.cmd_help)
        self.assertEqual(action, "pause")

    @patch.object(tg, "tg_send", return_value=1)
    def test_quit_command(self, mock_send):
        action, _, _ = tg._handle_command(
            "/quit", self.sessions, "4", self.cmd_help)
        self.assertEqual(action, "quit_pending")
        msg = mock_send.call_args[0][0]
        self.assertIn("Shut down", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "scan_claude_sessions")
    def test_sessions_command(self, mock_scan, mock_send):
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command(
            "/sessions", self.sessions, "4", self.cmd_help)
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Active Claude sessions", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_interrupt_command(self, mock_run, mock_send):
        action, _, last = tg._handle_command(
            "/interrupt w4", self.sessions, "4", self.cmd_help)
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupted", msg)
        # Check tmux send-keys Escape was sent
        cmd_str = mock_run.call_args[0][0][2]
        self.assertIn("Escape", cmd_str)

    @patch.object(tg, "tg_send", return_value=1)
    def test_interrupt_no_session(self, mock_send):
        action, _, _ = tg._handle_command(
            "/interrupt w99", self.sessions, "4", self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "scan_claude_sessions")
    def test_interrupt_no_window_shows_picker(self, mock_scan, mock_send):
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command(
            "/interrupt", self.sessions, None, self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupt which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "scan_claude_sessions")
    def test_interrupt_no_arg_multi_sessions_shows_picker(self, mock_scan, mock_send):
        """Bare /interrupt with multiple sessions shows picker, ignores last_win."""
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command(
            "/interrupt", self.sessions, "5", self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupt which", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_interrupt_no_arg_single_session_auto_targets(self, mock_run, mock_send):
        """Bare /interrupt with single session auto-interrupts it."""
        single = {"5": ("0:5.0", "other")}
        action, _, last = tg._handle_command(
            "/interrupt", single, None, self.cmd_help)
        self.assertEqual(last, "5")
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupted", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg, "scan_claude_sessions")
    def test_kill_command_success(self, mock_scan, mock_run, mock_send):
        """Kill removes session — success message."""
        mock_scan.return_value = {"4": ("0:4.0", "myproj")}  # w5 gone
        with patch("time.sleep"):
            action, sessions, _ = tg._handle_command(
                "/kill w5", self.sessions, "4", self.cmd_help)
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Killed", msg)
        # Verify three C-c sent
        cmd_str = mock_run.call_args[0][0][2]
        self.assertEqual(cmd_str.count("C-c"), 3)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg, "scan_claude_sessions")
    def test_kill_command_still_running(self, mock_scan, mock_run, mock_send):
        """Kill doesn't remove session — warning message."""
        mock_scan.return_value = self.sessions  # w5 still there
        with patch("time.sleep"):
            action, _, _ = tg._handle_command(
                "/kill w5", self.sessions, "4", self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("still running", msg)

    @patch.object(tg, "tg_send", return_value=1)
    def test_kill_nonexistent_session(self, mock_send):
        action, _, _ = tg._handle_command(
            "/kill w99", self.sessions, "4", self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg, "scan_claude_sessions")
    def test_new_command_default_dir(self, mock_scan, mock_run, mock_send):
        """New session with default directory."""
        mock_run.return_value = MagicMock(stdout="6\n")
        mock_scan.return_value = {**self.sessions, "6": ("0:6.0", "claude-0213-1500")}
        action, sessions, last = tg._handle_command(
            "/new", self.sessions, "4", self.cmd_help)
        self.assertIsNone(action)
        self.assertEqual(last, "6")
        msg = mock_send.call_args[0][0]
        self.assertIn("Started Claude", msg)
        self.assertIn("`w6`", msg)
        # Should create window with claude command
        cmd_arg = mock_run.call_args[0][0]
        self.assertIn("new-window", cmd_arg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg, "scan_claude_sessions")
    def test_new_command_custom_dir(self, mock_scan, mock_run, mock_send):
        """New session with user-specified directory."""
        mock_run.return_value = MagicMock(stdout="7\n")
        mock_scan.return_value = {**self.sessions, "7": ("0:7.0", "mydir")}
        action, _, last = tg._handle_command(
            "/new ~/mydir", self.sessions, "4", self.cmd_help)
        self.assertEqual(last, "7")
        msg = mock_send.call_args[0][0]
        self.assertIn("Started Claude", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run", side_effect=Exception("tmux error"))
    def test_new_command_failure(self, mock_run, mock_send):
        action, _, _ = tg._handle_command(
            "/new", self.sessions, "4", self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("Failed to start", msg)

    @patch.object(tg, "tg_send", return_value=1)
    def test_last_command(self, mock_send):
        tg._last_messages["4"] = "previous message"
        action, _, _ = tg._handle_command(
            "/last w4", self.sessions, "4", self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg, "previous message")

    @patch.object(tg, "tg_send", return_value=1)
    def test_last_command_no_saved(self, mock_send):
        action, _, _ = tg._handle_command(
            "/last w99", self.sessions, "4", self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("No saved message", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "route_to_pane", return_value="📨 Sent to `w4`:\n`hello`")
    def test_wn_prefix_routing(self, mock_route, mock_send):
        action, _, last = tg._handle_command(
            "w4 hello", self.sessions, None, self.cmd_help)
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        mock_route.assert_called_once_with("0:4.0", "4", "hello")

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "route_to_pane", return_value="📨 Sent")
    def test_no_prefix_single_session(self, mock_route, mock_send):
        """Single session — routes without prefix."""
        sessions = {"4": ("0:4.0", "myproj")}
        action, _, last = tg._handle_command(
            "hello", sessions, None, self.cmd_help)
        self.assertEqual(last, "4")
        mock_route.assert_called_once()

    @patch.object(tg, "tg_send", return_value=1)
    def test_no_prefix_multiple_sessions_no_last(self, mock_send):
        """Multiple sessions, no last — asks user to specify."""
        action, _, _ = tg._handle_command(
            "hello", self.sessions, None, self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("Multiple sessions", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "route_to_pane", return_value="📨 Sent")
    def test_no_prefix_uses_last_win(self, mock_route, mock_send):
        """Multiple sessions but last_win_idx set — routes to it."""
        action, _, last = tg._handle_command(
            "hello", self.sessions, "5", self.cmd_help)
        self.assertEqual(last, "5")
        mock_route.assert_called_once_with("0:5.0", "5", "hello")

    @patch.object(tg, "tg_send", return_value=1)
    def test_no_sessions(self, mock_send):
        action, _, _ = tg._handle_command(
            "hello", {}, None, self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("No Claude sessions", msg)

    @patch.object(tg, "tg_send", return_value=1)
    def test_wn_nonexistent_session(self, mock_send):
        action, _, _ = tg._handle_command(
            "w99 hello", self.sessions, "4", self.cmd_help)
        msg = mock_send.call_args[0][0]
        self.assertIn("No Claude session at `w99`", msg)


class TestComputeNewLinesEdgeCases(unittest.TestCase):
    """Additional edge cases for _compute_new_lines."""

    def test_both_empty(self):
        result = tg._compute_new_lines([], [])
        self.assertEqual(result, [])

    def test_new_empty_old_has_content(self):
        result = tg._compute_new_lines(["a", "b"], [])
        self.assertEqual(result, [])

    def test_single_line_identical(self):
        result = tg._compute_new_lines(["a"], ["a"])
        # Single equal line < 3 threshold → returns all new
        self.assertEqual(result, ["a"])

    def test_interleaved_inserts(self):
        """New lines inserted between existing lines."""
        old = ["a", "b", "c", "d", "e"]
        new = ["a", "b", "NEW1", "c", "d", "NEW2", "e"]
        result = tg._compute_new_lines(old, new)
        self.assertIn("NEW1", result)
        self.assertIn("NEW2", result)
        self.assertNotIn("a", result)


class TestCmdHookEdgeCases(unittest.TestCase):
    """Test cmd_hook edge cases."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_hook_edge"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir
        self._orig_enabled = tg.TG_HOOKS_ENABLED

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        tg.TG_HOOKS_ENABLED = self._orig_enabled
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch("sys.stdin")
    def test_hooks_disabled_consumes_stdin(self, mock_stdin):
        """With CLAUDE_TG_HOOKS != '1', stdin is consumed but no signal written."""
        tg.TG_HOOKS_ENABLED = False
        mock_stdin.read.return_value = '{"hook_event_name": "Stop"}'
        tg.cmd_hook()
        mock_stdin.read.assert_called_once()
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(signals, [])

    @patch("sys.stdin")
    def test_empty_stdin(self, mock_stdin):
        """Empty stdin — no crash, no signal."""
        tg.TG_HOOKS_ENABLED = True
        mock_stdin.read.return_value = ""
        tg.cmd_hook()  # should not raise

    @patch("sys.stdin")
    def test_invalid_json(self, mock_stdin):
        """Invalid JSON — no crash, no signal."""
        tg.TG_HOOKS_ENABLED = True
        mock_stdin.read.return_value = "not json{{"
        tg.cmd_hook()  # should not raise
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(signals, [])

    @patch("sys.stdin")
    def test_unknown_event_ignored(self, mock_stdin):
        """Unknown hook_event_name — no signal written."""
        tg.TG_HOOKS_ENABLED = True
        mock_stdin.read.return_value = json.dumps({
            "hook_event_name": "UnknownEvent", "cwd": "/tmp"
        })
        tg.cmd_hook()
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(signals, [])

    @patch("sys.stdin")
    def test_needs_attention_suppressed(self, mock_stdin):
        """AskUserQuestion 'needs your attention' notification is suppressed."""
        tg.TG_HOOKS_ENABLED = True
        mock_stdin.read.return_value = json.dumps({
            "hook_event_name": "Notification",
            "notification_type": "permission_prompt",
            "message": "Claude needs your attention",
            "cwd": "/tmp",
        })
        tg.cmd_hook()
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(signals, [])

    @patch.object(tg, "get_window_id", return_value="w4")
    @patch("sys.stdin")
    def test_question_signal_written(self, mock_stdin, mock_wid):
        """AskUserQuestion PreToolUse creates question signal."""
        tg.TG_HOOKS_ENABLED = True
        questions = [{"question": "Pick?", "options": [{"label": "A"}]}]
        mock_stdin.read.return_value = json.dumps({
            "hook_event_name": "PreToolUse",
            "tool_name": "AskUserQuestion",
            "tool_input": {"questions": questions},
            "cwd": "/tmp/proj",
        })
        os.environ["TMUX_PANE"] = "%20"
        tg.cmd_hook()
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(len(signals), 1)
        with open(os.path.join(self.signal_dir, signals[0])) as f:
            sig = json.load(f)
        self.assertEqual(sig["event"], "question")
        self.assertEqual(sig["questions"], questions)


class TestDownloadTgPhotoEdgeCases(unittest.TestCase):
    """Additional edge cases for _download_tg_photo."""

    @patch("requests.get")
    def test_empty_file_path(self, mock_get):
        """getFile returns empty file_path — returns None."""
        resp = MagicMock()
        resp.json.return_value = {"result": {"file_path": ""}}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        result = tg._download_tg_photo("file_id", "/tmp/test.jpg")
        self.assertIsNone(result)

    @patch("requests.get")
    def test_missing_result_key(self, mock_get):
        """getFile returns no result key — returns None."""
        resp = MagicMock()
        resp.json.return_value = {}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        result = tg._download_tg_photo("file_id", "/tmp/test.jpg")
        self.assertIsNone(result)


class TestMultiQuestionFlow(unittest.TestCase):
    """Test multi-question AskUserQuestion routing through route_to_pane."""

    def setUp(self):
        self.pane = "0:4.0"
        self.win_idx = "4"

    @patch("subprocess.run")
    @patch.object(tg, "tg_send", return_value=1)
    def test_first_question_saves_remaining(self, mock_send, mock_run):
        """Answering first question sends second question to Telegram."""
        remaining = [{"question": "Q2?", "options": [
            {"label": "X", "description": "opt X"},
        ]}]
        prompt = {"pane": "0:4.0", "total": 4, "ts": 0,
                  "free_text_at": 2, "remaining_qs": remaining,
                  "project": "myproj"}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "1")
        self.assertIn("Selected option 1", result)
        # Should have sent the second question
        msg = mock_send.call_args[0][0]
        self.assertIn("Q2?", msg)
        self.assertIn("X", msg)

    @patch("subprocess.run")
    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "save_active_prompt")
    def test_last_question_prompts_submit(self, mock_save, mock_send, mock_run):
        """Answering last question prompts 'Submit answers?'."""
        prompt = {"pane": "0:4.0", "total": 4, "ts": 0,
                  "free_text_at": 2, "remaining_qs": [],
                  "project": "myproj"}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "1")
        msg = mock_send.call_args[0][0]
        self.assertIn("Submit answers?", msg)
        # Should save prompt with y/n shortcuts for confirmation
        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args
        self.assertEqual(call_kwargs[1]["total"], 2)
        self.assertIn("y", call_kwargs[1]["shortcuts"])


class TestBuildInlineKeyboard(unittest.TestCase):
    """Test _build_inline_keyboard helper."""

    def test_single_row(self):
        result = tg._build_inline_keyboard([
            [("Allow", "perm_w4_1"), ("Deny", "perm_w4_3")],
        ])
        self.assertEqual(result, {"inline_keyboard": [
            [{"text": "Allow", "callback_data": "perm_w4_1"},
             {"text": "Deny", "callback_data": "perm_w4_3"}],
        ]})

    def test_multiple_rows(self):
        result = tg._build_inline_keyboard([
            [("A", "a1"), ("B", "a2"), ("C", "a3")],
            [("D", "a4")],
        ])
        self.assertEqual(len(result["inline_keyboard"]), 2)
        self.assertEqual(len(result["inline_keyboard"][0]), 3)
        self.assertEqual(len(result["inline_keyboard"][1]), 1)

    def test_empty(self):
        result = tg._build_inline_keyboard([])
        self.assertEqual(result, {"inline_keyboard": []})


class TestResolveAlias(unittest.TestCase):
    """Test _resolve_alias for short command aliases."""

    def test_status_bare(self):
        self.assertEqual(tg._resolve_alias("s", False), "/status")

    def test_status_with_window(self):
        self.assertEqual(tg._resolve_alias("s4", False), "/status w4")

    def test_status_with_window_and_lines(self):
        self.assertEqual(tg._resolve_alias("s4 10", False), "/status w4 10")

    def test_focus(self):
        self.assertEqual(tg._resolve_alias("f4", False), "/focus w4")

    def test_interrupt(self):
        self.assertEqual(tg._resolve_alias("i4", False), "/interrupt w4")

    def test_help_alias(self):
        self.assertEqual(tg._resolve_alias("?", False), "/help")

    def test_unfocus_alias(self):
        self.assertEqual(tg._resolve_alias("uf", False), "/unfocus")

    def test_passthrough_normal_text(self):
        self.assertEqual(tg._resolve_alias("fix the bug", False), "fix the bug")

    def test_passthrough_slash_command(self):
        self.assertEqual(tg._resolve_alias("/status", False), "/status")

    def test_suppressed_with_active_prompt(self):
        """Aliases suppressed when a prompt is active."""
        self.assertEqual(tg._resolve_alias("s", True), "s")
        self.assertEqual(tg._resolve_alias("f4", True), "f4")
        self.assertEqual(tg._resolve_alias("?", True), "?")

    def test_digits_not_aliased(self):
        """Pure digit replies must not be aliased."""
        self.assertEqual(tg._resolve_alias("1", False), "1")
        self.assertEqual(tg._resolve_alias("3", False), "3")

    def test_y_n_not_aliased(self):
        """y/n replies must not be aliased."""
        self.assertEqual(tg._resolve_alias("y", False), "y")
        self.assertEqual(tg._resolve_alias("n", False), "n")
        self.assertEqual(tg._resolve_alias("yes", False), "yes")
        self.assertEqual(tg._resolve_alias("no", False), "no")


class TestExtractChatMessagesCallbacks(unittest.TestCase):
    """Test _extract_chat_messages with callback_query updates."""

    def test_callback_query(self):
        data = {"result": [{
            "update_id": 100,
            "callback_query": {
                "id": "cb123",
                "data": "perm_w4_1",
                "message": {
                    "message_id": 42,
                    "chat": {"id": int(tg.CHAT_ID)},
                },
            },
        }]}
        result = tg._extract_chat_messages(data)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["text"], "")
        self.assertIsNone(result[0]["photo"])
        self.assertEqual(result[0]["callback"]["id"], "cb123")
        self.assertEqual(result[0]["callback"]["data"], "perm_w4_1")
        self.assertEqual(result[0]["callback"]["message_id"], 42)

    def test_callback_other_chat_ignored(self):
        data = {"result": [{
            "update_id": 100,
            "callback_query": {
                "id": "cb999",
                "data": "perm_w4_1",
                "message": {
                    "message_id": 42,
                    "chat": {"id": 999999},
                },
            },
        }]}
        result = tg._extract_chat_messages(data)
        self.assertEqual(result, [])

    def test_mixed_callbacks_and_messages(self):
        data = {"result": [
            {
                "update_id": 100,
                "callback_query": {
                    "id": "cb1",
                    "data": "perm_w4_1",
                    "message": {"message_id": 10, "chat": {"id": int(tg.CHAT_ID)}},
                },
            },
            {
                "update_id": 101,
                "message": {"chat": {"id": int(tg.CHAT_ID)}, "text": "hello"},
            },
        ]}
        result = tg._extract_chat_messages(data)
        self.assertEqual(len(result), 2)
        self.assertIsNotNone(result[0]["callback"])
        self.assertIsNone(result[1]["callback"])


class TestHandleCallback(unittest.TestCase):
    """Test _handle_callback dispatcher."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_callback"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "_select_option")
    @patch.object(tg, "load_active_prompt")
    def test_perm_allow(self, mock_load, mock_select, mock_send, mock_answer, mock_remove):
        mock_load.return_value = {"pane": "0:4.0", "total": 3}
        callback = {"id": "cb1", "data": "perm_w4_1", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_select.assert_called_once_with("0:4.0", 1)
        mock_answer.assert_called_once_with("cb1")
        mock_remove.assert_called_once_with(42)
        msg = mock_send.call_args[0][0]
        self.assertIn("Allowed", msg)
        self.assertIn("`w4`", msg)
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "_select_option")
    @patch.object(tg, "load_active_prompt")
    def test_perm_deny(self, mock_load, mock_select, mock_send, mock_answer, mock_remove):
        mock_load.return_value = {"pane": "0:4.0", "total": 3}
        callback = {"id": "cb1", "data": "perm_w4_3", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_select.assert_called_once_with("0:4.0", 3)
        msg = mock_send.call_args[0][0]
        self.assertIn("Denied", msg)
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "_select_option")
    @patch.object(tg, "load_active_prompt")
    def test_perm_always(self, mock_load, mock_select, mock_send, mock_answer, mock_remove):
        mock_load.return_value = {"pane": "0:4.0", "total": 3}
        callback = {"id": "cb1", "data": "perm_w4_2", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_select.assert_called_once_with("0:4.0", 2)
        msg = mock_send.call_args[0][0]
        self.assertIn("Always allowed", msg)
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "load_active_prompt")
    def test_perm_expired(self, mock_load, mock_answer, mock_remove):
        mock_load.return_value = None  # prompt file gone
        callback = {"id": "cb1", "data": "perm_w4_1", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        # Should call answer twice: once in main flow, once with "Prompt expired"
        self.assertEqual(mock_answer.call_count, 2)
        mock_answer.assert_any_call("cb1", "Prompt expired")
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "route_to_pane", return_value="📨 Selected option 1")
    def test_question_select(self, mock_route, mock_send, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "q_w4_1", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_route.assert_called_once_with("0:4.0", "4", "1")
        self.assertEqual(last, "4")
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_status(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_status_w4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/status w4", self.sessions, None, "")
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_focus(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_focus_w4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/focus w4", self.sessions, None, "")
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_sess_select(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "sess_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/status w4", self.sessions, "4", "")
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    def test_unknown_callback(self, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "unknown_xyz", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        self.assertEqual(sessions, self.sessions)
        self.assertIsNone(action)


class TestSessionsKeyboard(unittest.TestCase):
    """Test _sessions_keyboard helper."""

    def test_empty_sessions(self):
        self.assertIsNone(tg._sessions_keyboard({}))

    def test_single_session(self):
        result = tg._sessions_keyboard({"4": ("0:4.0", "myproj")})
        self.assertIsNotNone(result)
        buttons = result["inline_keyboard"]
        self.assertEqual(len(buttons), 1)
        self.assertEqual(len(buttons[0]), 1)
        self.assertIn("w4", buttons[0][0]["text"])
        self.assertEqual(buttons[0][0]["callback_data"], "sess_4")

    def test_multiple_sorted(self):
        result = tg._sessions_keyboard({
            "5": ("0:5.0", "beta"),
            "2": ("0:2.0", "alpha"),
            "8": ("0:8.0", "gamma"),
        })
        buttons = result["inline_keyboard"]
        # Should be sorted by window index
        all_buttons = [b for row in buttons for b in row]
        self.assertEqual(all_buttons[0]["callback_data"], "sess_2")
        self.assertEqual(all_buttons[1]["callback_data"], "sess_5")
        self.assertEqual(all_buttons[2]["callback_data"], "sess_8")


class TestTgSendWithKeyboard(unittest.TestCase):
    """Test tg_send with reply_markup parameter."""

    @patch("requests.post")
    def test_keyboard_in_payload(self, mock_post):
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"result": {"message_id": 1}}
        ok_resp.raise_for_status = MagicMock()
        mock_post.return_value = ok_resp

        kb = {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}
        tg.tg_send("test", reply_markup=kb)

        payload = mock_post.call_args[1]["json"]
        self.assertEqual(payload["reply_markup"], kb)

    @patch("requests.post")
    def test_none_keyboard_excluded(self, mock_post):
        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"result": {"message_id": 1}}
        ok_resp.raise_for_status = MagicMock()
        mock_post.return_value = ok_resp

        tg.tg_send("test", reply_markup=None)

        payload = mock_post.call_args[1]["json"]
        self.assertNotIn("reply_markup", payload)

    @patch("requests.post")
    def test_keyboard_survives_markdown_fallback(self, mock_post):
        """On 400 retry, keyboard is still included."""
        fail_resp = MagicMock()
        fail_resp.status_code = 400

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"result": {"message_id": 1}}
        ok_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [fail_resp, ok_resp]

        kb = {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}
        tg.tg_send("bad _markdown_", reply_markup=kb)

        # Second call (fallback) should still have reply_markup
        fallback_payload = mock_post.call_args_list[1][1]["json"]
        self.assertEqual(fallback_payload["reply_markup"], kb)
        self.assertNotIn("parse_mode", fallback_payload)


class TestSendLongMessageWithKeyboard(unittest.TestCase):
    """Test _send_long_message with reply_markup parameter."""

    @patch.object(tg, "tg_send", return_value=1)
    def test_short_message_gets_keyboard(self, mock_send):
        kb = {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}
        tg._send_long_message("H:\n", "short body", wid="4", reply_markup=kb)
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs.get("reply_markup"), kb)

    @patch.object(tg, "tg_send", return_value=1)
    def test_chunked_keyboard_on_last_only(self, mock_send):
        """Multi-chunk: keyboard attached to last chunk only."""
        kb = {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}
        line = "x" * 79 + "\n"
        body = line * 100  # exceeds TG_MAX
        tg._send_long_message("H:\n", body, wid="4", reply_markup=kb)
        self.assertGreater(mock_send.call_count, 1)
        # All calls except last should have reply_markup=None
        for c in mock_send.call_args_list[:-1]:
            self.assertIsNone(c[1].get("reply_markup"))
        # Last call should have the keyboard
        last_call = mock_send.call_args_list[-1]
        self.assertEqual(last_call[1].get("reply_markup"), kb)


class TestProcessSignalsWithKeyboards(unittest.TestCase):
    """Test that process_signals attaches inline keyboards."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_signals_kb"
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

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="proj")
    @patch.object(tg, "_extract_pane_permission",
                  return_value=("wants to update `t.py`", "+new=True", ["1. Yes", "2. No"]))
    @patch.object(tg, "save_active_prompt")
    def test_permission_has_keyboard(self, mock_save, mock_extract, mock_proj, mock_send):
        self._write_signal("permission", cmd="", message="needs permission")
        tg.process_signals()
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("perm_w4_1", buttons)
        self.assertIn("perm_w4_2", buttons)  # Always allow

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_stop_has_keyboard(self, mock_sleep, mock_run, mock_proj, mock_send):
        self._write_signal("stop")
        tg.process_signals()
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_status_w4", buttons)
        self.assertIn("cmd_focus_w4", buttons)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="proj")
    @patch.object(tg, "save_active_prompt")
    def test_question_has_keyboard(self, mock_save, mock_proj, mock_send):
        questions = [{"question": "Pick?", "options": [
            {"label": "Alpha", "description": "a"},
            {"label": "Beta", "description": "b"},
        ]}]
        self._write_signal("question", questions=questions)
        tg.process_signals()
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b for row in kb["inline_keyboard"] for b in row]
        self.assertEqual(buttons[0]["text"], "Alpha")
        self.assertEqual(buttons[0]["callback_data"], "q_w4_1")
        self.assertEqual(buttons[1]["text"], "Beta")
        self.assertEqual(buttons[1]["callback_data"], "q_w4_2")

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="proj")
    @patch.object(tg, "save_active_prompt")
    def test_question_no_options_no_keyboard(self, mock_save, mock_proj, mock_send):
        """Question with no options should not have keyboard."""
        questions = [{"question": "What?", "options": []}]
        self._write_signal("question", questions=questions)
        tg.process_signals()
        _, kwargs = mock_send.call_args
        self.assertIsNone(kwargs.get("reply_markup"))


class TestAnyActivePrompt(unittest.TestCase):
    """Test _any_active_prompt helper."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_any_prompt"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_no_prompts(self):
        self.assertFalse(tg._any_active_prompt())

    def test_has_prompt(self):
        path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        with open(path, "w") as f:
            json.dump({"pane": "0:4.0"}, f)
        self.assertTrue(tg._any_active_prompt())

    def test_other_state_files_not_counted(self):
        path = os.path.join(self.signal_dir, "_bash_cmd_w4.json")
        with open(path, "w") as f:
            json.dump({"cmd": "echo"}, f)
        self.assertFalse(tg._any_active_prompt())


class TestSetBotCommands(unittest.TestCase):
    """Test _set_bot_commands helper."""

    @patch("requests.post")
    def test_registers_commands(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)
        tg._set_bot_commands()
        mock_post.assert_called_once()
        url = mock_post.call_args[0][0]
        self.assertIn("setMyCommands", url)
        commands = mock_post.call_args[1]["json"]["commands"]
        names = [c["command"] for c in commands]
        self.assertIn("status", names)
        self.assertIn("sessions", names)
        self.assertIn("help", names)
        self.assertIn("quit", names)
        self.assertIn("deepfocus", names)
        self.assertIn("name", names)
        self.assertEqual(len(commands), 14)

    @patch("requests.post", side_effect=Exception("network error"))
    def test_survives_exception(self, mock_post):
        """Should not raise on failure."""
        tg._set_bot_commands()  # no exception



class TestSubmitYNButtons(unittest.TestCase):
    """Test that 'Submit answers?' includes Y/N inline keyboard."""

    @patch("subprocess.run")
    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "save_active_prompt")
    def test_submit_prompt_has_yn_keyboard(self, mock_save, mock_send, mock_run):
        """Last question answered — submit prompt includes inline Y/N buttons."""
        prompt = {"pane": "0:4.0", "total": 4, "ts": 0,
                  "free_text_at": 2, "remaining_qs": [],
                  "project": "myproj"}
        with patch.object(tg, "load_active_prompt", return_value=prompt):
            tg.route_to_pane("0:4.0", "4", "1")
        # Find the tg_send call with "Submit answers?"
        submit_call = None
        for c in mock_send.call_args_list:
            if "Submit answers?" in c[0][0]:
                submit_call = c
                break
        self.assertIsNotNone(submit_call, "Submit answers? message not found")
        kb = submit_call[1].get("reply_markup")
        self.assertIsNotNone(kb, "No reply_markup on submit prompt")
        buttons = [b for row in kb["inline_keyboard"] for b in row]
        self.assertEqual(len(buttons), 2)
        self.assertIn("Yes", buttons[0]["text"])
        self.assertIn("No", buttons[1]["text"])
        self.assertEqual(buttons[0]["callback_data"], "perm_w4_1")
        self.assertEqual(buttons[1]["callback_data"], "perm_w4_2")


class TestQuitYNButtons(unittest.TestCase):
    """Test /quit sends Y/N inline keyboard and callbacks dispatch correctly."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}

    @patch.object(tg, "tg_send", return_value=1)
    def test_quit_command_has_yn_keyboard(self, mock_send):
        action, _, _ = tg._handle_command("/quit", self.sessions, "4", "")
        self.assertEqual(action, "quit_pending")
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b for row in kb["inline_keyboard"] for b in row]
        self.assertEqual(len(buttons), 2)
        self.assertEqual(buttons[0]["callback_data"], "quit_y")
        self.assertEqual(buttons[1]["callback_data"], "quit_n")

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "tg_send", return_value=1)
    def test_quit_y_returns_quit_action(self, mock_send, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "quit_y", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, "4")
        self.assertEqual(action, "quit")
        msg = mock_send.call_args[0][0]
        self.assertIn("Bye", msg)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "tg_send", return_value=1)
    def test_quit_n_returns_none_action(self, mock_send, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "quit_n", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, "4")
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Cancelled", msg)


class TestCommandSessionsKeyboard(unittest.TestCase):
    """Test _command_sessions_keyboard helper."""

    def test_empty_sessions(self):
        self.assertIsNone(tg._command_sessions_keyboard("focus", {}))

    def test_builds_buttons_with_cmd_prefix(self):
        sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        kb = tg._command_sessions_keyboard("focus", sessions)
        self.assertIsNotNone(kb)
        buttons = [b for row in kb["inline_keyboard"] for b in row]
        self.assertEqual(buttons[0]["callback_data"], "cmd_focus_4")
        self.assertEqual(buttons[1]["callback_data"], "cmd_focus_5")

    def test_kill_command(self):
        sessions = {"2": ("0:2.0", "proj")}
        kb = tg._command_sessions_keyboard("kill", sessions)
        buttons = [b for row in kb["inline_keyboard"] for b in row]
        self.assertEqual(buttons[0]["callback_data"], "cmd_kill_2")


class TestBareCommandSessionPicker(unittest.TestCase):
    """Test bare /focus, /kill, /interrupt show session picker."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}

    @patch.object(tg, "tg_send", return_value=1)
    def test_bare_status_multiple_no_last_shows_picker(self, mock_send):
        """Bare /status with multiple sessions and no last_win shows picker."""
        action, _, _ = tg._handle_command("/status", self.sessions, None, "")
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Status for which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_status_4", buttons)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "scan_claude_sessions")
    def test_bare_focus_shows_picker(self, mock_scan, mock_send):
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command("/focus", self.sessions, "4", "")
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Focus on which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_focus_4", buttons)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "scan_claude_sessions")
    def test_bare_focus_no_sessions(self, mock_scan, mock_send):
        mock_scan.return_value = {}
        action, _, _ = tg._handle_command("/focus", {}, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("No Claude sessions", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "scan_claude_sessions")
    def test_bare_kill_shows_picker(self, mock_scan, mock_send):
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command("/kill", self.sessions, "4", "")
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Kill which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_kill_4", buttons)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "scan_claude_sessions")
    def test_bare_interrupt_no_last_shows_picker(self, mock_scan, mock_send):
        """Interrupt without args and no last_win shows session picker."""
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command("/interrupt", self.sessions, None, "")
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupt which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_interrupt_4", buttons)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "scan_claude_sessions")
    def test_interrupt_with_last_win_still_shows_picker(self, mock_scan, mock_send):
        """Bare /interrupt with multiple sessions shows picker even with last_win."""
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command(
            "/interrupt", self.sessions, "4", "")
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupt which", msg)


class TestBareLastSessionPicker(unittest.TestCase):
    """Test bare /last shows session picker."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self._orig = dict(tg._last_messages)

    def tearDown(self):
        tg._last_messages.clear()
        tg._last_messages.update(self._orig)

    @patch.object(tg, "tg_send", return_value=1)
    def test_bare_last_multiple_shows_picker(self, mock_send):
        tg._last_messages["4"] = "msg4"
        tg._last_messages["5"] = "msg5"
        action, _, _ = tg._handle_command("/last", self.sessions, "4", "")
        msg = mock_send.call_args[0][0]
        self.assertIn("Last message for which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_last_4", buttons)

    @patch.object(tg, "tg_send", return_value=1)
    def test_bare_last_single_auto_sends(self, mock_send):
        tg._last_messages["4"] = "the message"
        action, _, _ = tg._handle_command("/last", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg, "the message")

    @patch.object(tg, "tg_send", return_value=1)
    def test_bare_last_none_saved(self, mock_send):
        tg._last_messages.clear()
        action, _, _ = tg._handle_command("/last", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("No saved messages", msg)


class TestCallbackCommandExpanded(unittest.TestCase):
    """Test that callback handler dispatches interrupt, kill, and last commands."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_interrupt_callback(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_interrupt_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/interrupt w4", self.sessions, None, "")
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_kill_callback(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_kill_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/kill w4", self.sessions, None, "")
        self.assertIsNone(action)

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_last_callback(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_last_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/last w4", self.sessions, None, "")
        self.assertIsNone(action)


class TestDeepFocusState(unittest.TestCase):
    """Test deepfocus state file operations."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_deepfocus"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        tg._save_deepfocus_state("4", "0:4.0", "myproj")
        state = tg._load_deepfocus_state()
        self.assertEqual(state, {"wid": "4", "pane": "0:4.0", "project": "myproj"})

    def test_load_missing_returns_none(self):
        self.assertIsNone(tg._load_deepfocus_state())

    def test_clear_removes_file(self):
        tg._save_deepfocus_state("4", "0:4.0", "myproj")
        tg._clear_deepfocus_state()
        self.assertIsNone(tg._load_deepfocus_state())

    def test_survives_clear_signals_without_state(self):
        tg._save_deepfocus_state("4", "0:4.0", "myproj")
        tg._clear_signals(include_state=False)
        self.assertIsNotNone(tg._load_deepfocus_state())

    def test_cleared_by_clear_signals_with_state(self):
        tg._save_deepfocus_state("4", "0:4.0", "myproj")
        tg._clear_signals(include_state=True)
        self.assertIsNone(tg._load_deepfocus_state())


class TestSessionNames(unittest.TestCase):
    """Test session name state operations."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_names"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_save_and_load(self):
        tg._save_session_name("4", "auth")
        names = tg._load_session_names()
        self.assertEqual(names, {"4": "auth"})

    def test_multiple_names(self):
        tg._save_session_name("4", "auth")
        tg._save_session_name("5", "refactor")
        names = tg._load_session_names()
        self.assertEqual(names, {"4": "auth", "5": "refactor"})

    def test_clear_name(self):
        tg._save_session_name("4", "auth")
        tg._clear_session_name("4")
        names = tg._load_session_names()
        self.assertEqual(names, {})

    def test_load_empty(self):
        names = tg._load_session_names()
        self.assertEqual(names, {})

    def test_survives_clear_signals_without_state(self):
        tg._save_session_name("4", "auth")
        tg._clear_signals(include_state=False)
        names = tg._load_session_names()
        self.assertEqual(names, {"4": "auth"})

    def test_cleared_by_clear_signals_with_state(self):
        tg._save_session_name("4", "auth")
        tg._clear_signals(include_state=True)
        names = tg._load_session_names()
        self.assertEqual(names, {})


class TestDeepFocusAlias(unittest.TestCase):
    """Test df alias in _resolve_alias."""

    def test_df4_resolves(self):
        self.assertEqual(tg._resolve_alias("df4", False), "/deepfocus w4")

    def test_df10_resolves(self):
        self.assertEqual(tg._resolve_alias("df10", False), "/deepfocus w10")

    def test_suppressed_with_active_prompt(self):
        self.assertEqual(tg._resolve_alias("df4", True), "df4")


class TestDeepFocusCommand(unittest.TestCase):
    """Test /deepfocus command handling."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_dfcmd"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "scan_claude_sessions")
    def test_bare_deepfocus_shows_picker(self, mock_scan, mock_send):
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command("/deepfocus", self.sessions, "4", "")
        msg = mock_send.call_args[0][0]
        self.assertIn("Deep focus on which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_deepfocus_4", buttons)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_deepfocus_wn(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="some content\n")
        action, _, last = tg._handle_command(
            "/deepfocus w4", self.sessions, None, "")
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Deep focus on `w4`", msg)
        # Should have saved deepfocus state
        state = tg._load_deepfocus_state()
        self.assertIsNotNone(state)
        self.assertEqual(state["wid"], "4")
        # Should have cleared regular focus
        self.assertIsNone(tg._load_focus_state())

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_deepfocus_clears_focus(self, mock_run, mock_send):
        """Deepfocus clears any existing focus state (mutual exclusion)."""
        mock_run.return_value = MagicMock(stdout="content\n")
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._handle_command("/deepfocus w4", self.sessions, None, "")
        self.assertIsNone(tg._load_focus_state())
        self.assertIsNotNone(tg._load_deepfocus_state())

    @patch.object(tg, "tg_send", return_value=1)
    def test_deepfocus_no_session(self, mock_send):
        action, _, _ = tg._handle_command(
            "/deepfocus w99", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)


class TestFocusClearsDeepfocus(unittest.TestCase):
    """Test that /focus clears deepfocus state."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_focus_df"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_focus_clears_deepfocus(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="content\n")
        tg._save_deepfocus_state("4", "0:4.0", "myproj")
        tg._handle_command("/focus w4", self.sessions, None, "")
        self.assertIsNone(tg._load_deepfocus_state())
        self.assertIsNotNone(tg._load_focus_state())


class TestUnfocusClearsBoth(unittest.TestCase):
    """Test that /unfocus clears both focus and deepfocus states."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_unfocus"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg, "tg_send", return_value=1)
    def test_unfocus_clears_both(self, mock_send):
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._save_deepfocus_state("5", "0:5.0", "other")
        tg._handle_command("/unfocus", self.sessions, None, "")
        self.assertIsNone(tg._load_focus_state())
        self.assertIsNone(tg._load_deepfocus_state())
        msg = mock_send.call_args[0][0]
        self.assertIn("Focus stopped", msg)


class TestNameCommand(unittest.TestCase):
    """Test /name command handling."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_namecmd"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg, "tg_send", return_value=1)
    def test_name_set(self, mock_send):
        tg._handle_command("/name w4 auth-refactor", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("named `auth-refactor`", msg)
        names = tg._load_session_names()
        self.assertEqual(names["4"], "auth-refactor")

    @patch.object(tg, "tg_send", return_value=1)
    def test_name_clear(self, mock_send):
        tg._save_session_name("4", "old-name")
        tg._handle_command("/name w4", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("name cleared", msg)
        names = tg._load_session_names()
        self.assertNotIn("4", names)


class TestFormatSessionsWithNames(unittest.TestCase):
    """Test format_sessions_message includes session names."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_fmtnames"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_with_name(self):
        tg._save_session_name("4", "auth")
        sessions = {"4": ("0:4.0", "myproj")}
        msg = tg.format_sessions_message(sessions)
        self.assertIn("[`auth`]", msg)
        self.assertIn("`w4`", msg)
        self.assertIn("`myproj`", msg)

    def test_without_name(self):
        sessions = {"4": ("0:4.0", "myproj")}
        msg = tg.format_sessions_message(sessions)
        self.assertNotIn("[", msg)
        self.assertIn("`w4`", msg)

    def test_name_in_backticks_markdown_safe(self):
        """Session names with underscores must be in backticks."""
        tg._save_session_name("4", "my_auth")
        sessions = {"4": ("0:4.0", "proj")}
        msg = tg.format_sessions_message(sessions)
        self.assertIn("[`my_auth`]", msg)
        # Remove code blocks and check no bare underscores
        stripped = re.sub(r'```.*?```', '', msg, flags=re.DOTALL)
        stripped = re.sub(r'`[^`]+`', '', stripped)
        self.assertNotIn('_', stripped)


class TestDeepFocusCallback(unittest.TestCase):
    """Test cmd_deepfocus callback handler."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}

    @patch.object(tg, "_remove_inline_keyboard")
    @patch.object(tg, "_answer_callback_query")
    @patch.object(tg, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_deepfocus_callback(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_deepfocus_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/deepfocus w4", self.sessions, None, "")
        self.assertIsNone(action)


class TestProcessSignalsFocusedWids(unittest.TestCase):
    """Test process_signals with focused_wids set."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_sig_wids"
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

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_stop_suppressed_by_focus_set(self, mock_sleep, mock_run, mock_proj, mock_send):
        """Stop signal suppressed when wid is in focused_wids set."""
        self._write_signal("stop")
        tg.process_signals(focused_wids={"4"})
        mock_send.assert_not_called()

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_stop_not_suppressed_different_wid(self, mock_sleep, mock_run, mock_proj, mock_send):
        """Stop signal NOT suppressed when wid not in focused_wids."""
        self._write_signal("stop")
        tg.process_signals(focused_wids={"5"})
        mock_send.assert_called_once()


class TestProcessSignalsWithNames(unittest.TestCase):
    """Test that process_signals includes session names in tags."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_sig_names"
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

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_stop_includes_name(self, mock_sleep, mock_run, mock_proj, mock_send):
        tg._save_session_name("4", "auth")
        self._write_signal("stop")
        tg.process_signals()
        msg = mock_send.call_args[0][0]
        self.assertIn("`w4 [auth]`", msg)


class TestHelpIncludesNewCommands(unittest.TestCase):
    """Test /help includes deepfocus, name, and df alias."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}

    @patch.object(tg, "tg_send", return_value=1)
    def test_help_has_deepfocus(self, mock_send):
        tg._handle_command("/help", self.sessions, "4", "")
        msg = mock_send.call_args[0][0]
        self.assertIn("/deepfocus", msg)
        self.assertIn("/name", msg)
        self.assertIn("df4", msg)


class TestResolveName(unittest.TestCase):
    """Test _resolve_name helper for name-based session routing."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_resolve"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_numeric_index(self):
        """Direct numeric index returns itself."""
        self.assertEqual(tg._resolve_name("4", self.sessions), "4")

    def test_numeric_not_in_sessions(self):
        """Numeric index not in sessions returns None."""
        self.assertIsNone(tg._resolve_name("99", self.sessions))

    def test_name_lookup(self):
        """Name lookup returns correct index."""
        tg._save_session_name("4", "auth")
        self.assertEqual(tg._resolve_name("auth", self.sessions), "4")

    def test_name_case_insensitive(self):
        """Name lookup is case-insensitive."""
        tg._save_session_name("4", "Auth")
        self.assertEqual(tg._resolve_name("auth", self.sessions), "4")
        self.assertEqual(tg._resolve_name("AUTH", self.sessions), "4")

    def test_unknown_name(self):
        """Unknown name returns None."""
        self.assertIsNone(tg._resolve_name("nonexistent", self.sessions))

    def test_name_for_dead_session(self):
        """Name for a session not in the live sessions dict returns None."""
        tg._save_session_name("99", "dead")
        self.assertIsNone(tg._resolve_name("dead", self.sessions))

    def test_none_target(self):
        """None target returns None."""
        self.assertIsNone(tg._resolve_name(None, self.sessions))


class TestNameBasedCommands(unittest.TestCase):
    """Test commands accept session names as targets."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_namecmds"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir
        tg._save_session_name("4", "auth")

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_focus_by_name(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="content\n")
        action, _, last = tg._handle_command(
            "/focus auth", self.sessions, None, "")
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Focusing on `w4 [auth]`", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_deepfocus_by_name(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="content\n")
        action, _, last = tg._handle_command(
            "/deepfocus auth", self.sessions, None, "")
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Deep focus on `w4 [auth]`", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_interrupt_by_name(self, mock_run, mock_send):
        action, _, last = tg._handle_command(
            "/interrupt auth", self.sessions, None, "")
        self.assertEqual(last, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupted `w4 [auth]`", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg, "scan_claude_sessions")
    def test_kill_by_name(self, mock_scan, mock_run, mock_send):
        mock_scan.return_value = {"5": ("0:5.0", "other")}  # w4 gone
        with patch("time.sleep"):
            action, _, _ = tg._handle_command(
                "/kill auth", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("Killed", msg)

    @patch.object(tg, "tg_send", return_value=1)
    def test_last_by_name(self, mock_send):
        tg._last_messages["4"] = "previous msg"
        action, _, _ = tg._handle_command(
            "/last auth", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg, "previous msg")
        tg._last_messages.pop("4", None)

    @patch.object(tg, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_status_by_name(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="● Answer\n  42\n❯ prompt")
        action, _, _ = tg._handle_command(
            "/status auth", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("`w4 [auth]`", msg)

    @patch.object(tg, "tg_send", return_value=1)
    def test_name_rename_by_name(self, mock_send):
        """Rename a session using its current name."""
        action, _, _ = tg._handle_command(
            "/name auth newname", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("named `newname`", msg)
        names = tg._load_session_names()
        self.assertEqual(names["4"], "newname")

    @patch.object(tg, "tg_send", return_value=1)
    def test_unknown_name_error(self, mock_send):
        action, _, _ = tg._handle_command(
            "/focus nonexistent", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)
        self.assertIn("nonexistent", msg)


class TestNamePrefixRouting(unittest.TestCase):
    """Test name-prefix message routing (e.g. 'auth fix the bug')."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_nameprefix"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.SIGNAL_DIR
        tg.SIGNAL_DIR = self.signal_dir
        tg._save_session_name("4", "auth")

    def tearDown(self):
        tg.SIGNAL_DIR = self._orig_signal_dir
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "route_to_pane", return_value="📨 Sent to `w4`:\n`fix the bug`")
    def test_name_prefix_routes(self, mock_route, mock_send):
        """'auth fix the bug' routes to session named 'auth'."""
        action, _, last = tg._handle_command(
            "auth fix the bug", self.sessions, None, "")
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        mock_route.assert_called_once_with("0:4.0", "4", "fix the bug")

    @patch.object(tg, "tg_send", return_value=1)
    def test_unknown_word_falls_through(self, mock_send):
        """Unknown first word with multiple sessions asks to specify."""
        action, _, _ = tg._handle_command(
            "randomword hello", self.sessions, None, "")
        msg = mock_send.call_args[0][0]
        self.assertIn("Multiple sessions", msg)

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "route_to_pane", return_value="📨 Sent to `w4`:\n`hello`")
    def test_wn_prefix_still_works(self, mock_route, mock_send):
        """w4 hello still works (backward compat)."""
        action, _, last = tg._handle_command(
            "w4 hello", self.sessions, None, "")
        self.assertEqual(last, "4")
        mock_route.assert_called_once_with("0:4.0", "4", "hello")

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "route_to_pane", return_value="📨 Sent")
    def test_name_case_insensitive(self, mock_route, mock_send):
        """Name prefix is case-insensitive."""
        action, _, last = tg._handle_command(
            "Auth fix it", self.sessions, None, "")
        self.assertEqual(last, "4")
        mock_route.assert_called_once_with("0:4.0", "4", "fix it")

    @patch.object(tg, "tg_send", return_value=1)
    @patch.object(tg, "route_to_pane", return_value="📨 Sent")
    def test_single_word_no_prefix(self, mock_route, mock_send):
        """Single word that isn't a name doesn't trigger name routing."""
        sessions = {"4": ("0:4.0", "myproj")}
        action, _, _ = tg._handle_command(
            "hello", sessions, None, "")
        # Should route to single session as no-prefix fallback
        mock_route.assert_called_once_with("0:4.0", "4", "hello")


if __name__ == "__main__":
    unittest.main()
