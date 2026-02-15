#!/usr/bin/env python
"""Tests for tg-hook — validates formatting, routing, and content cleaning."""
import json
import os
import re
import struct
import sys
import textwrap
import unittest
from unittest.mock import MagicMock, patch, call

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
import tg_hook as tg


class TestMarkdownSafety(unittest.TestCase):
    """Verify messages with underscores don't break Telegram Markdown V1."""

    def _send_and_capture(self, send_fn):
        """Call send_fn, return the text that would be sent to Telegram."""
        with patch.object(tg.telegram, "tg_send") as mock_send:
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
        header, content, options, ctx = tg._extract_pane_permission("test_pane")

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
        header, content, options, ctx = tg._extract_pane_permission("test_pane")

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
        header, content, options, ctx = tg._extract_pane_permission("test_pane")

        self.assertIn("fetch", header)
        self.assertIn("`https://example.com`", header)
        self.assertEqual(len(options), 3)

    @patch("subprocess.run")
    def test_no_options(self, mock_run):
        mock_run.return_value = self._mock_pane("some random content\nno options here")
        header, content, options, ctx = tg._extract_pane_permission("test_pane")
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
        header, content, options, ctx = tg._extract_pane_permission("test_pane")

        self.assertNotIn("Edit file", content)
        self.assertNotIn("hook.py", content)  # standalone filename filtered
        self.assertNotIn("────", content)
        self.assertNotIn("Do you want", content)
        self.assertIn("+new = True", content)

    @patch("subprocess.run")
    def test_progressive_capture_expands(self, mock_run):
        """When ● is near the top of captured window, capture expands for more context."""
        # Short capture (30 lines): ● at line 0, plan content truncated
        short_content = textwrap.dedent("""\
            ● ExitPlanMode()
              ⎿  Plan summary here
              ❯ 1. Yes
                2. No (esc)
        """)
        # Long capture (80+ lines): ● further down, with plan content above
        plan_lines = "\n".join(f"  plan line {i}" for i in range(15))
        long_content = plan_lines + "\n" + textwrap.dedent("""\
            ● ExitPlanMode()
              ⎿  Full plan content here
              more plan details
              ❯ 1. Yes
                2. No (esc)
        """)

        def side_effect(cmd, **kwargs):
            num_lines = int(cmd[6].lstrip("-"))
            if num_lines <= 30:
                return self._mock_pane(short_content)
            return self._mock_pane(long_content)

        mock_run.side_effect = side_effect
        header, body, options, ctx = tg._extract_pane_permission("test_pane")

        # Should have expanded — verify it captured the deeper content
        self.assertIn("more plan details", body)
        self.assertEqual(len(options), 2)
        # Verify subprocess.run was called multiple times (progressive)
        self.assertGreater(mock_run.call_count, 1)

    @patch("subprocess.run")
    def test_context_from_response_bullet(self, mock_run):
        """Response bullet above tool bullet is captured as context."""
        pane_content = textwrap.dedent("""\
            ● I'll update the function to use snake_case.
              Here's the change:
            ● Update(scripts/test_file.py)
              ⎿  Edit file
                 scripts/test_file.py
              1 +new_line = True
              ❯ 1. Yes
                2. Yes, and don't ask again for this file
                3. No, and tell Claude what to do differently (esc)
        """)
        mock_run.return_value = self._mock_pane(pane_content)
        header, content, options, ctx = tg._extract_pane_permission("test_pane")

        self.assertIn("update", header)
        self.assertIn("+new_line = True", content)
        self.assertIn("update the function to use snake_case", ctx)
        self.assertIn("Here's the change:", ctx)

    @patch("subprocess.run")
    def test_no_response_bullet_empty_context(self, mock_run):
        """No response bullet above tool bullet → empty context."""
        pane_content = textwrap.dedent("""\
            ● Update(scripts/test_file.py)
              ⎿  Edit file
                 scripts/test_file.py
              1 +new_line = True
              ❯ 1. Yes
                2. No (esc)
        """)
        mock_run.return_value = self._mock_pane(pane_content)
        header, content, options, ctx = tg._extract_pane_permission("test_pane")

        self.assertIn("update", header)
        self.assertEqual(ctx, "")


class TestRouteToPane(unittest.TestCase):
    """Test route_to_pane logic with mocked tmux."""

    def setUp(self):
        self.pane = "0:4.0"
        self.win_idx = "4"
        self.signal_dir = "/tmp/tg_hook_test_route"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    def test_normal_message(self, mock_idle, mock_run):
        """No active prompt — sends text + Enter."""
        with patch.object(tg.state, "load_active_prompt", return_value=None):
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
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
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
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "n")
        self.assertIn("Selected option 3", result)
        cmd_str = mock_run.call_args[0][0][2]
        self.assertEqual(cmd_str.count("Down"), 2)  # n=3, so 2 Downs

    @patch("subprocess.run")
    def test_numbered_selection(self, mock_run):
        """Digit reply navigates with Down keys."""
        prompt = {"pane": "%20", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "n": 3}}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
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
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
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
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "1")
        self.assertIn("Selected option 1", result)

    @patch("subprocess.run")
    def test_question_extra_options(self, mock_run):
        """Question allows selecting n+1 (Type answer) and n+2 (Chat)."""
        prompt = {"pane": "%20", "total": 4, "ts": 0, "free_text_at": 2}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "4")
        self.assertIn("Selected option 4", result)  # n+2 = 4

    def test_unknown_text_returns_guidance(self):
        """Unrecognized text always returns guidance when prompt is active."""
        prompt = {"pane": "%20", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "n": 3}}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "change step 3")
        self.assertIn("⚠️", result)
        self.assertIn("`n`", result)
        self.assertIn("`y`", result)
        # Prompt was re-saved so user can retry
        saved = tg.state.load_active_prompt(f"w{self.win_idx}")
        self.assertIsNotNone(saved)
        self.assertEqual(saved["total"], 3)

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    def test_message_underscore_safe(self, mock_idle, mock_run):
        """Route confirmation with underscored text is Markdown-safe."""
        with patch.object(tg.state, "load_active_prompt", return_value=None):
            result = tg.route_to_pane(self.pane, self.win_idx, "fix my_var_name")
        # Text should be in backticks
        self.assertIn("`fix my_var_name`", result)

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    def test_newlines_stripped_before_send(self, mock_idle, mock_run):
        """Newlines in message text are replaced with spaces before send-keys."""
        with patch.object(tg.state, "load_active_prompt", return_value=None):
            result = tg.route_to_pane(self.pane, self.win_idx, "line1\nline2\rline3")
        self.assertIn("Sent to", result)
        cmd = mock_run.call_args[0][0][-1]  # bash -c "..."
        self.assertNotIn("\\n", cmd)
        self.assertIn("line1 line2 line3", cmd)


class TestProcessSignals(unittest.TestCase):
    """Test signal processing with mocked filesystem and Telegram."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_signals"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)
        return fname

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="test_project")
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="test_proj")
    @patch.object(tg.content, "_extract_pane_permission", return_value=("wants to update `test.py`", "+new=True", ["1. Yes", "2. No"], ""))
    @patch.object(tg.state, "save_active_prompt")
    def test_permission_signal_non_bash(self, mock_save, mock_extract, mock_proj, mock_send):
        self._write_signal("permission", cmd="", message="Claude needs permission to use Update")

        tg.process_signals()

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("wants to update", msg)
        self.assertIn("```", msg)
        self.assertIn("1. Yes", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="test_proj")
    @patch.object(tg.content, "_extract_pane_permission", return_value=("", "", ["1. Yes", "2. No"], ""))
    @patch.object(tg.state, "save_active_prompt")
    def test_permission_signal_bash(self, mock_save, mock_extract, mock_proj, mock_send):
        self._write_signal("permission", cmd="rm /tmp/test_file.txt", message="Claude needs permission")

        tg.process_signals()

        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("needs permission", msg)
        self.assertIn("```\nrm /tmp/test_file.txt\n```", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.content, "_extract_pane_permission", return_value=("wants to fetch `https://example.com`", "", ["1. Yes", "2. No"], ""))
    @patch.object(tg.state, "save_active_prompt")
    def test_permission_no_content(self, mock_save, mock_extract, mock_proj, mock_send):
        """WebFetch with no content body should not have empty pre block."""
        self._write_signal("permission", cmd="", message="Claude needs permission")

        tg.process_signals()

        msg = mock_send.call_args[0][0]
        self.assertIn("wants to fetch", msg)
        self.assertNotIn("```\n\n```", msg)  # no empty pre block
        self.assertIn("1. Yes", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.state, "save_active_prompt")
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

        with patch.object(tg.telegram, "tg_send"):
            tg.process_signals()

        # State file should still exist (not deleted)
        self.assertTrue(os.path.exists(state_path))

    def test_cleans_processed_signals(self):
        """Processed signal files should be deleted."""
        self._write_signal("stop")
        with patch.object(tg.telegram, "tg_send", return_value=1), \
             patch.object(tg.tmux, "get_pane_project", return_value="p"), \
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
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")
        self._orig_enabled = tg.config.TG_HOOKS_ENABLED
        tg.config.TG_HOOKS_ENABLED = True

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        tg.config.TG_HOOKS_ENABLED = self._orig_enabled
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.tmux, "get_window_id", return_value="w4")
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

    @patch.object(tg.tmux, "get_window_id", return_value="w4")
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


class TestCmdHookPlanMode(unittest.TestCase):
    """Test that EnterPlanMode PreToolUse writes a plan signal."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_plan"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")
        self._orig_enabled = tg.config.TG_HOOKS_ENABLED
        tg.config.TG_HOOKS_ENABLED = True

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        tg.config.TG_HOOKS_ENABLED = self._orig_enabled
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.tmux, "get_window_id", return_value="w4")
    @patch("sys.stdin")
    def test_plan_signal_written(self, mock_stdin, mock_wid):
        data = {"hook_event_name": "PreToolUse", "tool_name": "EnterPlanMode",
                "tool_input": {}, "cwd": "/tmp/test"}
        mock_stdin.read.return_value = json.dumps(data)
        os.environ["TMUX_PANE"] = "%20"

        tg.cmd_hook()

        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(len(signals), 1)
        with open(os.path.join(self.signal_dir, signals[0])) as f:
            signal = json.load(f)
        self.assertEqual(signal["event"], "plan")


class TestPlanSignalBypassesGodMode(unittest.TestCase):
    """Test that plan signals are always sent to Telegram, never auto-accepted."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_plan_god"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        # Clear god mode BEFORE restoring paths (otherwise it deletes the real file)
        tg.state._clear_god_mode()
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.content, "_extract_pane_permission",
                  return_value=("wants to enter plan mode", "", ["1. Yes", "2. No"], ""))
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("time.sleep")
    def test_plan_not_auto_accepted_in_god_mode(self, mock_sleep, mock_send, mock_perm):
        """Even with god mode on, plan signal sends to Telegram instead of auto-accepting."""
        tg.state._set_god_mode("all", True)
        # Write a plan signal
        tg.state.write_signal("plan", {
            "hook_event_name": "PreToolUse",
            "tool_name": "EnterPlanMode",
            "cwd": "/tmp/test",
        })
        tg.signals.process_signals()
        # Should have sent a Telegram message (not auto-accepted)
        mock_send.assert_called()
        msg = mock_send.call_args[0][0]
        self.assertIn("plan mode", msg)


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
        self.assertEqual(result, [{"text": "hello", "photo": None, "callback": None, "reply_wid": None}])

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

    def test_reply_wid_from_reply_to_message(self):
        """reply_to_message with wN text → reply_wid extracted."""
        data = self._make_update({
            "text": "fix the bug",
            "reply_to_message": {"text": "🔔 `w4` (`myproj`): stopped"},
        })
        result = tg._extract_chat_messages(data)
        self.assertEqual(result[0]["reply_wid"], "4")

    def test_reply_wid_none_when_no_wn(self):
        """reply_to_message with no wN pattern → reply_wid is None."""
        data = self._make_update({
            "text": "hello",
            "reply_to_message": {"text": "some message without session id"},
        })
        result = tg._extract_chat_messages(data)
        self.assertIsNone(result[0]["reply_wid"])

    def test_reply_wid_none_when_no_reply(self):
        """No reply_to_message → reply_wid is None."""
        data = self._make_update({"text": "hello"})
        result = tg._extract_chat_messages(data)
        self.assertIsNone(result[0]["reply_wid"])

    def test_reply_wid_from_caption(self):
        """reply_to_message with wN in caption → reply_wid extracted."""
        data = self._make_update({
            "text": "looks good",
            "reply_to_message": {"caption": "📷 Photo from `w7`"},
        })
        result = tg._extract_chat_messages(data)
        self.assertEqual(result[0]["reply_wid"], "7")

    def test_reply_wid_on_photo_message(self):
        """Photo message with reply_to_message → reply_wid extracted."""
        data = self._make_update({
            "photo": [{"file_id": "abc", "width": 800, "height": 800}],
            "caption": "check this",
            "reply_to_message": {"text": "`w3` response"},
        })
        result = tg._extract_chat_messages(data)
        self.assertEqual(result[0]["reply_wid"], "3")


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
    """Test _cleanup_stale_prompts removes prompts whose pane is idle."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_cleanup"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    def test_removes_stale_prompt(self, mock_idle):
        """Pane is idle (❯ visible) — prompt was answered, remove file."""
        path = os.path.join(self.signal_dir, "_active_prompt_w4.json")
        with open(path, "w") as f:
            json.dump({"pane": "0:4.0", "total": 3}, f)
        tg._cleanup_stale_prompts()
        self.assertFalse(os.path.exists(path))

    @patch.object(tg.routing, "_pane_idle_state", return_value=(False, ""))
    def test_keeps_active_prompt(self, mock_idle):
        """Pane is not idle — prompt may still be active, keep file."""
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

    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    def test_ignores_non_prompt_state_files(self, mock_idle):
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
        """Multiple prompt files — removes only idle ones."""
        stale = os.path.join(self.signal_dir, "_active_prompt_w1.json")
        active = os.path.join(self.signal_dir, "_active_prompt_w2.json")
        with open(stale, "w") as f:
            json.dump({"pane": "0:1.0", "total": 3}, f)
        with open(active, "w") as f:
            json.dump({"pane": "0:2.0", "total": 3}, f)
        # w1 pane is idle, w2 pane is not idle (prompt still visible)
        def side_effect(pane):
            return (True, "") if pane == "0:1.0" else (False, "")
        with patch.object(tg.routing, "_pane_idle_state", side_effect=side_effect):
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
        tg.config.SIGNAL_DIR = "/tmp/tg_hook_nonexistent_dir_xyz"
        tg._cleanup_stale_prompts()  # should not raise


class TestFocusState(unittest.TestCase):
    """Test focus state file operations."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_focus"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_short_message_single_send(self, mock_send):
        """Body that fits in one message — sent as single message."""
        tg._send_long_message("header:\n", "short body", wid="4")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("header:", msg)
        self.assertIn("```\nshort body\n```", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_empty_body(self, mock_send):
        tg._send_long_message("H:\n", "", wid="4")
        mock_send.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("```\n\n```", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_single_long_line_no_break(self, mock_send):
        """Single line with no newlines — can't split at line boundary."""
        body = "x" * 8000
        tg._send_long_message("H:\n", body, wid="4")
        # The chunking loop puts entire line in one chunk if no newlines
        # Result: single very long message (truncated by tg_send)
        self.assertEqual(mock_send.call_count, 1)

    @patch.object(tg.telegram, "tg_send", return_value=1)
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
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_help_command(self, mock_send):
        action, sessions, last = tg._handle_command(
            "/help", self.sessions, "4")
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Commands", msg)
        self.assertIn("/status", msg)
        self.assertIn("/focus", msg)
        self.assertIn("/new", msg)
        self.assertIn("/kill", msg)
        self.assertIn("/interrupt", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_stop_command(self, mock_send):
        action, _, _ = tg._handle_command(
            "/stop", self.sessions, "4")
        self.assertEqual(action, "pause")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_quit_command(self, mock_send):
        action, _, _ = tg._handle_command(
            "/quit", self.sessions, "4")
        self.assertEqual(action, "quit_pending")
        msg = mock_send.call_args[0][0]
        self.assertIn("Shut down", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_interrupt_command(self, mock_run, mock_send):
        # Set up busy and prompt state to verify they get cleared
        tg._mark_busy("w4")
        tg.save_active_prompt("w4", "0:4.0", total=3)
        action, _, last = tg._handle_command(
            "/interrupt w4", self.sessions, "4")
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupted", msg)
        # Check Escape + Ctrl+U sent
        cmd_str = mock_run.call_args[0][0][2]
        self.assertIn("Escape", cmd_str)
        self.assertIn("C-u", cmd_str)
        # Busy and prompt state should be cleared
        self.assertFalse(tg._is_busy("w4"))
        self.assertIsNone(tg.load_active_prompt("w4"))

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_interrupt_no_session(self, mock_send):
        action, _, _ = tg._handle_command(
            "/interrupt w99", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_interrupt_no_window_shows_picker(self, mock_scan, mock_send):
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command(
            "/interrupt", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupt which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_interrupt_no_arg_multi_sessions_shows_picker(self, mock_scan, mock_send):
        """Bare /interrupt with multiple sessions shows picker, ignores last_win."""
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command(
            "/interrupt", self.sessions, "5")
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupt which", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_interrupt_no_arg_single_session_auto_targets(self, mock_run, mock_send):
        """Bare /interrupt with single session auto-interrupts it."""
        single = {"5": ("0:5.0", "other")}
        action, _, last = tg._handle_command(
            "/interrupt", single, None)
        self.assertEqual(last, "5")
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupted", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_kill_command_success(self, mock_scan, mock_run, mock_send):
        """Kill removes session — success message."""
        mock_scan.return_value = {"4": ("0:4.0", "myproj")}  # w5 gone
        with patch("time.sleep"):
            action, sessions, _ = tg._handle_command(
                "/kill w5", self.sessions, "4")
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Killed", msg)
        # Verify three C-c sent
        cmd_str = mock_run.call_args[0][0][2]
        self.assertEqual(cmd_str.count("C-c"), 3)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_kill_command_still_running(self, mock_scan, mock_run, mock_send):
        """Kill doesn't remove session — warning message."""
        mock_scan.return_value = self.sessions  # w5 still there
        with patch("time.sleep"):
            action, _, _ = tg._handle_command(
                "/kill w5", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("still running", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_kill_nonexistent_session(self, mock_send):
        action, _, _ = tg._handle_command(
            "/kill w99", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_new_command_default_dir(self, mock_scan, mock_run, mock_send):
        """New session with default directory."""
        mock_run.return_value = MagicMock(stdout="6\n")
        mock_scan.return_value = {**self.sessions, "6": ("0:6.0", "claude-0213-1500")}
        action, sessions, last = tg._handle_command(
            "/new", self.sessions, "4")
        self.assertIsNone(action)
        self.assertEqual(last, "6")
        msg = mock_send.call_args[0][0]
        self.assertIn("Started Claude", msg)
        self.assertIn("`w6`", msg)
        # Should create window with claude command
        cmd_arg = mock_run.call_args[0][0]
        self.assertIn("new-window", cmd_arg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_new_command_custom_dir(self, mock_scan, mock_run, mock_send):
        """New session with user-specified directory."""
        mock_run.return_value = MagicMock(stdout="7\n")
        mock_scan.return_value = {**self.sessions, "7": ("0:7.0", "mydir")}
        action, _, last = tg._handle_command(
            "/new ~/mydir", self.sessions, "4")
        self.assertEqual(last, "7")
        msg = mock_send.call_args[0][0]
        self.assertIn("Started Claude", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run", side_effect=Exception("tmux error"))
    def test_new_command_failure(self, mock_run, mock_send):
        action, _, _ = tg._handle_command(
            "/new", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Failed to start", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_last_command(self, mock_send):
        tg._last_messages["4"] = "previous message"
        action, _, _ = tg._handle_command(
            "/last w4", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg, "previous message")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_last_command_no_saved(self, mock_send):
        action, _, _ = tg._handle_command(
            "/last w99", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("No saved message", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent to `w4`:\n`hello`")
    def test_wn_prefix_routing(self, mock_route, mock_send):
        action, _, last = tg._handle_command(
            "w4 hello", self.sessions, None)
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        mock_route.assert_called_once_with("0:4.0", "4", "hello")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent")
    def test_no_prefix_single_session(self, mock_route, mock_send):
        """Single session — routes without prefix."""
        sessions = {"4": ("0:4.0", "myproj")}
        action, _, last = tg._handle_command(
            "hello", sessions, None)
        self.assertEqual(last, "4")
        mock_route.assert_called_once()

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_no_prefix_multiple_sessions_no_last(self, mock_send):
        """Multiple sessions, no last — asks user to specify."""
        action, _, _ = tg._handle_command(
            "hello", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("Multiple sessions", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent")
    def test_no_prefix_uses_last_win(self, mock_route, mock_send):
        """Multiple sessions but last_win_idx set — routes to it."""
        action, _, last = tg._handle_command(
            "hello", self.sessions, "5")
        self.assertEqual(last, "5")
        mock_route.assert_called_once_with("0:5.0", "5", "hello")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_no_sessions(self, mock_send):
        action, _, _ = tg._handle_command(
            "hello", {}, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No Claude sessions", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_wn_nonexistent_session(self, mock_send):
        action, _, _ = tg._handle_command(
            "w99 hello", self.sessions, "4")
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
        # Identical content returns empty, even if short
        self.assertEqual(result, [])

    def test_two_lines_identical(self):
        result = tg._compute_new_lines(["a", "b"], ["a", "b"])
        self.assertEqual(result, [])

    def test_short_content_with_new_line(self):
        """Short content with a genuine new line returns only the insert."""
        old = ["a"]
        new = ["a", "b"]
        result = tg._compute_new_lines(old, new)
        self.assertEqual(result, ["b"])

    def test_short_content_replace_no_duplicate(self):
        """Short content where one line changes should NOT re-send everything."""
        old = ["Good question — let me check.", "Searching for 1 pattern…"]
        new = ["Good question — let me check.", "Searching for 1 pattern, reading 1 file…"]
        result = tg._compute_new_lines(old, new)
        self.assertEqual(result, [])  # replace only, no inserts

    def test_completely_different_content(self):
        """Zero overlap returns all new content."""
        old = ["Response A", "Details about A"]
        new = ["Response B", "Details about B"]
        result = tg._compute_new_lines(old, new)
        self.assertEqual(result, new)

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
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")
        self._orig_enabled = tg.config.TG_HOOKS_ENABLED

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        tg.config.TG_HOOKS_ENABLED = self._orig_enabled
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch("sys.stdin")
    def test_hooks_disabled_consumes_stdin(self, mock_stdin):
        """With CLAUDE_TG_HOOKS != '1', stdin is consumed but no signal written."""
        tg.config.TG_HOOKS_ENABLED = False
        mock_stdin.read.return_value = '{"hook_event_name": "Stop"}'
        tg.cmd_hook()
        mock_stdin.read.assert_called_once()
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(signals, [])

    @patch("sys.stdin")
    def test_empty_stdin(self, mock_stdin):
        """Empty stdin — no crash, no signal."""
        tg.config.TG_HOOKS_ENABLED = True
        mock_stdin.read.return_value = ""
        tg.cmd_hook()  # should not raise

    @patch("sys.stdin")
    def test_invalid_json(self, mock_stdin):
        """Invalid JSON — no crash, no signal."""
        tg.config.TG_HOOKS_ENABLED = True
        mock_stdin.read.return_value = "not json{{"
        tg.cmd_hook()  # should not raise
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(signals, [])

    @patch("sys.stdin")
    def test_unknown_event_ignored(self, mock_stdin):
        """Unknown hook_event_name — no signal written."""
        tg.config.TG_HOOKS_ENABLED = True
        mock_stdin.read.return_value = json.dumps({
            "hook_event_name": "UnknownEvent", "cwd": "/tmp"
        })
        tg.cmd_hook()
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(signals, [])

    @patch("sys.stdin")
    def test_needs_attention_suppressed(self, mock_stdin):
        """AskUserQuestion 'needs your attention' notification is suppressed."""
        tg.config.TG_HOOKS_ENABLED = True
        mock_stdin.read.return_value = json.dumps({
            "hook_event_name": "Notification",
            "notification_type": "permission_prompt",
            "message": "Claude needs your attention",
            "cwd": "/tmp",
        })
        tg.cmd_hook()
        signals = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(signals, [])

    @patch.object(tg.tmux, "get_window_id", return_value="w4")
    @patch("sys.stdin")
    def test_question_signal_written(self, mock_stdin, mock_wid):
        """AskUserQuestion PreToolUse creates question signal."""
        tg.config.TG_HOOKS_ENABLED = True
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
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_first_question_saves_remaining(self, mock_send, mock_run):
        """Answering first question sends second question to Telegram."""
        remaining = [{"question": "Q2?", "options": [
            {"label": "X", "description": "opt X"},
        ]}]
        prompt = {"pane": "0:4.0", "total": 4, "ts": 0,
                  "free_text_at": 2, "remaining_qs": remaining,
                  "project": "myproj"}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane(self.pane, self.win_idx, "1")
        self.assertIn("Selected option 1", result)
        # Should have sent the second question
        msg = mock_send.call_args[0][0]
        self.assertIn("Q2?", msg)
        self.assertIn("X", msg)

    @patch("subprocess.run")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.state, "save_active_prompt")
    def test_last_question_prompts_submit(self, mock_save, mock_send, mock_run):
        """Answering last question prompts 'Submit answers?'."""
        prompt = {"pane": "0:4.0", "total": 4, "ts": 0,
                  "free_text_at": 2, "remaining_qs": [],
                  "project": "myproj"}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
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

    def test_ambiguous_suppressed_with_active_prompt(self):
        """Only ambiguous aliases (?, uf) suppressed when prompt is active."""
        self.assertEqual(tg._resolve_alias("?", True), "?")
        self.assertEqual(tg._resolve_alias("uf", True), "uf")

    def test_digit_aliases_resolve_with_active_prompt(self):
        """Digit-containing aliases always resolve, even with active prompt."""
        self.assertEqual(tg._resolve_alias("s", True), "/status")
        self.assertEqual(tg._resolve_alias("s4", True), "/status w4")
        self.assertEqual(tg._resolve_alias("s4 10", True), "/status w4 10")
        self.assertEqual(tg._resolve_alias("f4", True), "/focus w4")
        self.assertEqual(tg._resolve_alias("df4", True), "/deepfocus w4")
        self.assertEqual(tg._resolve_alias("i4", True), "/interrupt w4")

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
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.state, "load_active_prompt")
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

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.state, "load_active_prompt")
    def test_perm_deny(self, mock_load, mock_select, mock_send, mock_answer, mock_remove):
        mock_load.return_value = {"pane": "0:4.0", "total": 3}
        callback = {"id": "cb1", "data": "perm_w4_3", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_select.assert_called_once_with("0:4.0", 3)
        msg = mock_send.call_args[0][0]
        self.assertIn("Denied", msg)
        self.assertIsNone(action)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.state, "load_active_prompt")
    def test_perm_always(self, mock_load, mock_select, mock_send, mock_answer, mock_remove):
        mock_load.return_value = {"pane": "0:4.0", "total": 3}
        callback = {"id": "cb1", "data": "perm_w4_2", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_select.assert_called_once_with("0:4.0", 2)
        msg = mock_send.call_args[0][0]
        self.assertIn("Always allowed", msg)
        self.assertIsNone(action)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.state, "load_active_prompt")
    def test_perm_expired(self, mock_load, mock_answer, mock_remove):
        mock_load.return_value = None  # prompt file gone
        callback = {"id": "cb1", "data": "perm_w4_1", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        # Should call answer twice: once in main flow, once with "Prompt expired"
        self.assertEqual(mock_answer.call_count, 2)
        mock_answer.assert_any_call("cb1", "Prompt expired")
        self.assertIsNone(action)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Selected option 1")
    def test_question_select(self, mock_route, mock_send, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "q_w4_1", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_route.assert_called_once_with("0:4.0", "4", "1")
        self.assertEqual(last, "4")
        self.assertIsNone(action)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.commands, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_status(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_status_w4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/status w4", self.sessions, None)
        self.assertIsNone(action)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.commands, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_focus(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_focus_w4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/focus w4", self.sessions, None)
        self.assertIsNone(action)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.commands, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_sess_select(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "sess_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/status w4", self.sessions, "4")
        self.assertIsNone(action)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
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


class TestBuildReplyKeyboard(unittest.TestCase):
    """Test _build_reply_keyboard helper."""

    def test_has_keyboard_key(self):
        result = tg.telegram._build_reply_keyboard()
        self.assertIn("keyboard", result)
        self.assertIsInstance(result["keyboard"], list)

    def test_resize_keyboard_true(self):
        result = tg.telegram._build_reply_keyboard()
        self.assertTrue(result.get("resize_keyboard"))

    def test_is_persistent(self):
        result = tg.telegram._build_reply_keyboard()
        self.assertTrue(result.get("is_persistent"))

    def test_buttons_are_text_dicts(self):
        result = tg.telegram._build_reply_keyboard()
        for row in result["keyboard"]:
            for btn in row:
                self.assertIn("text", btn)
                self.assertTrue(btn["text"].startswith("/"))


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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_short_message_gets_keyboard(self, mock_send):
        kb = {"inline_keyboard": [[{"text": "A", "callback_data": "a"}]]}
        tg._send_long_message("H:\n", "short body", wid="4", reply_markup=kb)
        mock_send.assert_called_once()
        _, kwargs = mock_send.call_args
        self.assertEqual(kwargs.get("reply_markup"), kb)

    @patch.object(tg.telegram, "tg_send", return_value=1)
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
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.content, "_extract_pane_permission",
                  return_value=("wants to update `t.py`", "+new=True", ["1. Yes", "2. No"], ""))
    @patch.object(tg.state, "save_active_prompt")
    def test_permission_has_keyboard(self, mock_save, mock_extract, mock_proj, mock_send):
        self._write_signal("permission", cmd="", message="needs permission")
        tg.process_signals()
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("perm_w4_1", buttons)
        self.assertIn("perm_w4_2", buttons)  # Always allow

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.state, "save_active_prompt")
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.state, "save_active_prompt")
    def test_question_no_options_no_keyboard(self, mock_save, mock_proj, mock_send):
        """Question with no options should not have keyboard."""
        questions = [{"question": "What?", "options": []}]
        self._write_signal("question", questions=questions)
        tg.process_signals()
        _, kwargs = mock_send.call_args
        self.assertIsNone(kwargs.get("reply_markup"))

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.telegram, "_send_long_message")
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.content, "_extract_pane_permission",
                  return_value=("wants to update `t.py`", "big plan\ncontent here", ["1. Yes", "2. No"], ""))
    @patch.object(tg.state, "save_active_prompt")
    def test_permission_non_bash_uses_send_long_message(self, mock_save, mock_extract, mock_proj, mock_long, mock_send):
        """Non-bash permission with body uses _send_long_message for chunking."""
        self._write_signal("permission", cmd="", message="needs permission")
        tg.process_signals()
        mock_long.assert_called_once()
        args, kwargs = mock_long.call_args
        self.assertIn("wants to update", args[0])  # header
        self.assertIn("big plan", args[1])  # body includes content
        self.assertIn("1. Yes", kwargs.get("footer"))  # options in footer
        self.assertIsNotNone(kwargs.get("reply_markup"))
        # tg_send should NOT be called directly for non-bash with body
        mock_send.assert_not_called()


class TestAnyActivePrompt(unittest.TestCase):
    """Test _any_active_prompt helper."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_any_prompt"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
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
        self.assertIn("help", names)
        self.assertIn("quit", names)
        self.assertIn("deepfocus", names)
        self.assertIn("name", names)
        self.assertNotIn("sessions", names)
        self.assertIn("god", names)
        self.assertIn("autofocus", names)
        self.assertIn("saved", names)
        self.assertIn("clear", names)
        self.assertEqual(len(commands), 17)

    @patch("requests.post", side_effect=Exception("network error"))
    def test_survives_exception(self, mock_post):
        """Should not raise on failure."""
        tg._set_bot_commands()  # no exception



class TestSubmitYNButtons(unittest.TestCase):
    """Test that 'Submit answers?' includes Y/N inline keyboard."""

    @patch("subprocess.run")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.state, "save_active_prompt")
    def test_submit_prompt_has_yn_keyboard(self, mock_save, mock_send, mock_run):
        """Last question answered — submit prompt includes inline Y/N buttons."""
        prompt = {"pane": "0:4.0", "total": 4, "ts": 0,
                  "free_text_at": 2, "remaining_qs": [],
                  "project": "myproj"}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_quit_command_has_yn_keyboard(self, mock_send):
        action, _, _ = tg._handle_command("/quit", self.sessions, "4")
        self.assertEqual(action, "quit_pending")
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b for row in kb["inline_keyboard"] for b in row]
        self.assertEqual(len(buttons), 2)
        self.assertEqual(buttons[0]["callback_data"], "quit_y")
        self.assertEqual(buttons[1]["callback_data"], "quit_n")

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_quit_y_returns_quit_action(self, mock_send, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "quit_y", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, "4")
        self.assertEqual(action, "quit")
        msg = mock_send.call_args[0][0]
        self.assertIn("Bye", msg)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_bare_status_shows_sessions(self, mock_scan, mock_send):
        """Bare /status shows session list."""
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command("/status", self.sessions, None)
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Active Claude sessions", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_bare_focus_shows_picker(self, mock_scan, mock_send):
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command("/focus", self.sessions, "4")
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Focus on which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_focus_4", buttons)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_bare_focus_no_sessions(self, mock_scan, mock_send):
        mock_scan.return_value = {}
        action, _, _ = tg._handle_command("/focus", {}, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No Claude sessions", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_bare_kill_shows_picker(self, mock_scan, mock_send):
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command("/kill", self.sessions, "4")
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Kill which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_kill_4", buttons)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_bare_interrupt_no_last_shows_picker(self, mock_scan, mock_send):
        """Interrupt without args and no last_win shows session picker."""
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command("/interrupt", self.sessions, None)
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupt which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_interrupt_4", buttons)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_interrupt_with_last_win_still_shows_picker(self, mock_scan, mock_send):
        """Bare /interrupt with multiple sessions shows picker even with last_win."""
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command(
            "/interrupt", self.sessions, "4")
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_bare_last_multiple_shows_picker(self, mock_send):
        tg._last_messages["4"] = "msg4"
        tg._last_messages["5"] = "msg5"
        action, _, _ = tg._handle_command("/last", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Last message for which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_last_4", buttons)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_bare_last_single_auto_sends(self, mock_send):
        tg._last_messages["4"] = "the message"
        action, _, _ = tg._handle_command("/last", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg, "the message")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_bare_last_none_saved(self, mock_send):
        tg._last_messages.clear()
        action, _, _ = tg._handle_command("/last", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No saved messages", msg)


class TestCallbackCommandExpanded(unittest.TestCase):
    """Test that callback handler dispatches interrupt, kill, and last commands."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.commands, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_interrupt_callback(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_interrupt_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/interrupt w4", self.sessions, None)
        self.assertIsNone(action)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.commands, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_kill_callback(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_kill_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/kill w4", self.sessions, None)
        self.assertIsNone(action)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.commands, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_last_callback(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_last_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/last w4", self.sessions, None)
        self.assertIsNone(action)


class TestDeepFocusState(unittest.TestCase):
    """Test deepfocus state file operations."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_deepfocus"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
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
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
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

    def test_preserved_by_clear_signals_with_state(self):
        tg._save_session_name("4", "auth")
        tg._clear_signals(include_state=True)
        names = tg._load_session_names()
        self.assertEqual(names, {"4": "auth"})


class TestDeepFocusAlias(unittest.TestCase):
    """Test df alias in _resolve_alias."""

    def test_df4_resolves(self):
        self.assertEqual(tg._resolve_alias("df4", False), "/deepfocus w4")

    def test_df10_resolves(self):
        self.assertEqual(tg._resolve_alias("df10", False), "/deepfocus w10")

    def test_resolves_with_active_prompt(self):
        """Digit-containing alias df4 resolves even with active prompt."""
        self.assertEqual(tg._resolve_alias("df4", True), "/deepfocus w4")


class TestDeepFocusCommand(unittest.TestCase):
    """Test /deepfocus command handling."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_dfcmd"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_bare_deepfocus_shows_picker(self, mock_scan, mock_send):
        mock_scan.return_value = self.sessions
        action, _, _ = tg._handle_command("/deepfocus", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Deep focus on which", msg)
        _, kwargs = mock_send.call_args
        kb = kwargs.get("reply_markup")
        self.assertIsNotNone(kb)
        buttons = [b["callback_data"] for row in kb["inline_keyboard"] for b in row]
        self.assertIn("cmd_deepfocus_4", buttons)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_deepfocus_wn(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="some content\n")
        action, _, last = tg._handle_command(
            "/deepfocus w4", self.sessions, None)
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_deepfocus_clears_focus(self, mock_run, mock_send):
        """Deepfocus clears any existing focus state (mutual exclusion)."""
        mock_run.return_value = MagicMock(stdout="content\n")
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._handle_command("/deepfocus w4", self.sessions, None)
        self.assertIsNone(tg._load_focus_state())
        self.assertIsNotNone(tg._load_deepfocus_state())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_deepfocus_no_session(self, mock_send):
        action, _, _ = tg._handle_command(
            "/deepfocus w99", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)


class TestFocusClearsDeepfocus(unittest.TestCase):
    """Test that /focus clears deepfocus state."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_focus_df"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_focus_clears_deepfocus(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="content\n")
        tg._save_deepfocus_state("4", "0:4.0", "myproj")
        tg._handle_command("/focus w4", self.sessions, None)
        self.assertIsNone(tg._load_deepfocus_state())
        self.assertIsNotNone(tg._load_focus_state())


class TestUnfocusClearsBoth(unittest.TestCase):
    """Test that /unfocus clears both focus and deepfocus states."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_unfocus"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_unfocus_clears_both(self, mock_send):
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._save_deepfocus_state("5", "0:5.0", "other")
        tg._handle_command("/unfocus", self.sessions, None)
        self.assertIsNone(tg._load_focus_state())
        self.assertIsNone(tg._load_deepfocus_state())
        msg = mock_send.call_args[0][0]
        self.assertIn("Focus stopped", msg)


class TestClearCommand(unittest.TestCase):
    """Test /clear command handling."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_clear_cmd"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_clear_all(self, mock_send):
        """Test /clear removes all transient state."""
        tg._mark_busy("w4")
        tg._mark_busy("w5")
        tg.save_active_prompt("w4", "0:4.0", total=3)
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._save_smartfocus_state("5", "0:5.0", "other")
        tg._handle_command("/clear", self.sessions, "4")
        # All transient state should be gone
        self.assertFalse(tg._is_busy("w4"))
        self.assertFalse(tg._is_busy("w5"))
        self.assertIsNone(tg.load_active_prompt("w4"))
        self.assertIsNone(tg._load_focus_state())
        self.assertIsNone(tg._load_smartfocus_state())
        msg = mock_send.call_args[0][0]
        self.assertIn("Cleared all", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_clear_specific_window(self, mock_send):
        """Test /clear wN only clears that window's state."""
        tg._mark_busy("w4")
        tg._mark_busy("w5")
        tg.save_active_prompt("w4", "0:4.0", total=3)
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._handle_command("/clear w4", self.sessions, "4")
        # w4 state cleared
        self.assertFalse(tg._is_busy("w4"))
        self.assertIsNone(tg.load_active_prompt("w4"))
        self.assertIsNone(tg._load_focus_state())
        # w5 state untouched
        self.assertTrue(tg._is_busy("w5"))
        msg = mock_send.call_args[0][0]
        self.assertIn("Cleared transient state", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_clear_preserves_queued_and_names(self, mock_send):
        """Test /clear does NOT remove queued messages or session names."""
        tg._save_queued_msg("w4", "hello")
        tg._save_session_name("4", "auth")
        tg._mark_busy("w4")
        tg._handle_command("/clear", self.sessions, "4")
        # Queued and names preserved
        self.assertEqual(len(tg._load_queued_msgs("w4")), 1)
        self.assertEqual(tg._load_session_names()["4"], "auth")
        # Busy cleared
        self.assertFalse(tg._is_busy("w4"))

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_clear_focus_only_if_matching(self, mock_send):
        """Test /clear wN only clears focus if it targets that window."""
        tg._save_focus_state("5", "0:5.0", "other")
        tg._mark_busy("w4")
        tg._handle_command("/clear w4", self.sessions, "4")
        # Focus for w5 should be untouched
        self.assertIsNotNone(tg._load_focus_state())
        # Busy for w4 cleared
        self.assertFalse(tg._is_busy("w4"))

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_clear_unknown_session(self, mock_send):
        tg._handle_command("/clear w99", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_clear_alias_all(self, mock_send):
        """Test 'c' alias resolves to /clear."""
        resolved = tg._resolve_alias("c", has_active_prompt=False)
        self.assertEqual(resolved, "/clear")

    def test_clear_alias_window(self):
        """Test 'c4' alias resolves to /clear w4."""
        resolved = tg._resolve_alias("c4", has_active_prompt=False)
        self.assertEqual(resolved, "/clear w4")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_clear_preserves_god_mode(self, mock_send):
        """Test /clear does NOT remove god mode state."""
        tg._set_god_mode("4", True)
        tg._mark_busy("w4")
        tg._handle_command("/clear", self.sessions, "4")
        self.assertTrue(tg._is_god_mode_for("4"))
        self.assertFalse(tg._is_busy("w4"))


class TestNameCommand(unittest.TestCase):
    """Test /name command handling."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_namecmd"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_name_set(self, mock_send):
        tg._handle_command("/name w4 auth-refactor", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("named `auth-refactor`", msg)
        names = tg._load_session_names()
        self.assertEqual(names["4"], "auth-refactor")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_name_clear(self, mock_send):
        tg._save_session_name("4", "old-name")
        tg._handle_command("/name w4", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("name cleared", msg)
        names = tg._load_session_names()
        self.assertNotIn("4", names)


class TestFormatSessionsWithNames(unittest.TestCase):
    """Test format_sessions_message includes session names."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_fmtnames"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_with_name(self):
        tg._save_session_name("4", "auth")
        sessions = {"4": ("0:4.0", "myproj")}
        msg = tg.format_sessions_message(sessions)
        self.assertIn("`w4 [auth]`", msg)
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
        self.assertIn("`w4 [my_auth]`", msg)
        # Remove code blocks and check no bare underscores
        stripped = re.sub(r'```.*?```', '', msg, flags=re.DOTALL)
        stripped = re.sub(r'`[^`]+`', '', stripped)
        self.assertNotIn('_', stripped)


class TestDeepFocusCallback(unittest.TestCase):
    """Test cmd_deepfocus callback handler."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.commands, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_deepfocus_callback(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_deepfocus_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/deepfocus w4", self.sessions, None)
        self.assertIsNone(action)


class TestProcessSignalsFocusedWids(unittest.TestCase):
    """Test process_signals with focused_wids set."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_sig_wids"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_stop_suppressed_by_focus_set(self, mock_sleep, mock_run, mock_proj, mock_send):
        """Stop signal suppressed when wid is in focused_wids set."""
        self._write_signal("stop")
        tg.process_signals(focused_wids={"4"})
        mock_send.assert_not_called()

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_stop_not_suppressed_different_wid(self, mock_sleep, mock_run, mock_proj, mock_send):
        """Stop signal NOT suppressed when wid not in focused_wids."""
        self._write_signal("stop")
        tg.process_signals(focused_wids={"5"})
        mock_send.assert_called_once()

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.telegram, "_send_long_message")
    @patch.object(tg.telegram, "_build_inline_keyboard", return_value=None)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_smartfocus_stop_no_prev_sends_short(self, mock_sleep, mock_run, mock_proj, mock_kb, mock_long, mock_send):
        """Stop signal with smartfocus but no prev_lines sends short notification."""
        tg.state._save_smartfocus_state("4", "%20", "proj")
        self._write_signal("stop")
        tg.process_signals()  # no smartfocus_prev passed
        # Short notification via tg_send (not _send_long_message with full content)
        mock_send.assert_called()
        calls = [c[0][0] for c in mock_send.call_args_list]
        self.assertTrue(any("finished" in c for c in calls))
        # Smartfocus should be cleared
        self.assertIsNone(tg.state._load_smartfocus_state())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.telegram, "_send_long_message")
    @patch.object(tg.telegram, "_build_inline_keyboard", return_value=None)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  line1\n  line2\n  new stuff\n❯ prompt"))
    @patch("time.sleep")
    def test_smartfocus_stop_with_prev_sends_tail(self, mock_sleep, mock_run, mock_proj, mock_kb, mock_long, mock_send):
        """Stop signal with smartfocus_prev sends only new (tail) content."""
        tg.state._save_smartfocus_state("4", "%20", "proj")
        self._write_signal("stop")
        # prev_lines matches first part of response — only "new stuff" is new
        prev = ["Answer", "  line1", "  line2"]
        tg.process_signals(smartfocus_prev=prev)
        # Tail content via _send_long_message
        mock_long.assert_called_once()
        header = mock_long.call_args[0][0]
        self.assertIn("finished", header)
        body = mock_long.call_args[0][1]
        self.assertIn("new stuff", body)
        self.assertNotIn("line1", body)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.telegram, "_send_long_message")
    @patch.object(tg.telegram, "_build_inline_keyboard", return_value=None)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  line1\n❯ prompt"))
    @patch("time.sleep")
    def test_smartfocus_stop_no_new_lines_sends_short(self, mock_sleep, mock_run, mock_proj, mock_kb, mock_long, mock_send):
        """Stop signal with smartfocus_prev matching all content sends short notification."""
        tg.state._save_smartfocus_state("4", "%20", "proj")
        self._write_signal("stop")
        prev = ["Answer", "  line1"]
        tg.process_signals(smartfocus_prev=prev)
        # No new content — short notification only
        mock_long.assert_not_called()
        calls = [c[0][0] for c in mock_send.call_args_list]
        self.assertTrue(any("finished" in c for c in calls))

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.telegram, "_send_long_message")
    @patch.object(tg.telegram, "_build_inline_keyboard", return_value=None)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_smartfocus_stop_shows_queued_messages(self, mock_sleep, mock_run, mock_proj, mock_kb, mock_long, mock_send):
        """Stop signal with smartfocus still shows queued messages."""
        tg.state._save_smartfocus_state("4", "%20", "proj")
        tg.state._save_queued_msg("w4", "fix the bug")
        self._write_signal("stop")
        tg.process_signals()
        calls = [c[0][0] for c in mock_send.call_args_list]
        self.assertTrue(any("saved message" in c for c in calls))
        # Clean up
        tg.state._pop_queued_msgs("w4")


class TestProcessSignalsWithNames(unittest.TestCase):
    """Test that process_signals includes session names in tags."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_sig_names"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
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

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_help_has_deepfocus(self, mock_send):
        tg._handle_command("/help", self.sessions, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("/deepfocus", msg)
        self.assertIn("/name", msg)
        self.assertIn("df4", msg)


class TestResolveName(unittest.TestCase):
    """Test _resolve_name helper for name-based session routing."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_resolve"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
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
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")
        tg._save_session_name("4", "auth")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_focus_by_name(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="content\n")
        action, _, last = tg._handle_command(
            "/focus auth", self.sessions, None)
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Focusing on `w4 [auth]`", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_deepfocus_by_name(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="content\n")
        action, _, last = tg._handle_command(
            "/deepfocus auth", self.sessions, None)
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Deep focus on `w4 [auth]`", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_interrupt_by_name(self, mock_run, mock_send):
        action, _, last = tg._handle_command(
            "/interrupt auth", self.sessions, None)
        self.assertEqual(last, "4")
        msg = mock_send.call_args[0][0]
        self.assertIn("Interrupted `w4 [auth]`", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    @patch.object(tg.tmux, "scan_claude_sessions")
    def test_kill_by_name(self, mock_scan, mock_run, mock_send):
        mock_scan.return_value = {"5": ("0:5.0", "other")}  # w4 gone
        with patch("time.sleep"):
            action, _, _ = tg._handle_command(
                "/kill auth", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("Killed", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_last_by_name(self, mock_send):
        tg._last_messages["4"] = "previous msg"
        action, _, _ = tg._handle_command(
            "/last auth", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertEqual(msg, "previous msg")
        tg._last_messages.pop("4", None)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_status_by_name(self, mock_run, mock_send):
        mock_run.return_value = MagicMock(stdout="● Answer\n  42\n❯ prompt")
        action, _, _ = tg._handle_command(
            "/status auth", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("`w4 [auth]`", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_name_rename_by_name(self, mock_send):
        """Rename a session using its current name."""
        action, _, _ = tg._handle_command(
            "/name auth newname", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("named `newname`", msg)
        names = tg._load_session_names()
        self.assertEqual(names["4"], "newname")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_unknown_name_error(self, mock_send):
        action, _, _ = tg._handle_command(
            "/focus nonexistent", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)
        self.assertIn("nonexistent", msg)


class TestNamePrefixRouting(unittest.TestCase):
    """Test name-prefix message routing (e.g. 'auth fix the bug')."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_nameprefix"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")
        tg._save_session_name("4", "auth")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent to `w4`:\n`fix the bug`")
    def test_name_prefix_routes(self, mock_route, mock_send):
        """'auth fix the bug' routes to session named 'auth'."""
        action, _, last = tg._handle_command(
            "auth fix the bug", self.sessions, None)
        self.assertIsNone(action)
        self.assertEqual(last, "4")
        mock_route.assert_called_once_with("0:4.0", "4", "fix the bug")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_unknown_word_falls_through(self, mock_send):
        """Unknown first word with multiple sessions asks to specify."""
        action, _, _ = tg._handle_command(
            "randomword hello", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("Multiple sessions", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent to `w4`:\n`hello`")
    def test_wn_prefix_still_works(self, mock_route, mock_send):
        """w4 hello still works (backward compat)."""
        action, _, last = tg._handle_command(
            "w4 hello", self.sessions, None)
        self.assertEqual(last, "4")
        mock_route.assert_called_once_with("0:4.0", "4", "hello")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent")
    def test_name_case_insensitive(self, mock_route, mock_send):
        """Name prefix is case-insensitive."""
        action, _, last = tg._handle_command(
            "Auth fix it", self.sessions, None)
        self.assertEqual(last, "4")
        mock_route.assert_called_once_with("0:4.0", "4", "fix it")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent")
    def test_single_word_no_prefix(self, mock_route, mock_send):
        """Single word that isn't a name doesn't trigger name routing."""
        sessions = {"4": ("0:4.0", "myproj")}
        action, _, _ = tg._handle_command(
            "hello", sessions, None)
        # Should route to single session as no-prefix fallback
        mock_route.assert_called_once_with("0:4.0", "4", "hello")


class TestQueuedMessageState(unittest.TestCase):
    """Test _save_queued_msg, _load_queued_msgs, _pop_queued_msgs."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_queued"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_save_load_cycle(self):
        tg._save_queued_msg("w4", "hello")
        tg._save_queued_msg("w4", "world")
        msgs = tg._load_queued_msgs("w4")
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["text"], "hello")
        self.assertEqual(msgs[1]["text"], "world")
        self.assertIn("ts", msgs[0])

    def test_pop_returns_and_deletes(self):
        tg._save_queued_msg("w4", "msg1")
        msgs = tg._pop_queued_msgs("w4")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "msg1")
        # File should be deleted
        self.assertEqual(tg._load_queued_msgs("w4"), [])

    def test_load_empty(self):
        self.assertEqual(tg._load_queued_msgs("w99"), [])

    def test_pop_empty(self):
        self.assertEqual(tg._pop_queued_msgs("w99"), [])

    def test_separate_sessions(self):
        tg._save_queued_msg("w4", "for w4")
        tg._save_queued_msg("w5", "for w5")
        self.assertEqual(len(tg._load_queued_msgs("w4")), 1)
        self.assertEqual(len(tg._load_queued_msgs("w5")), 1)


class TestSavedPromptTextState(unittest.TestCase):
    """Test _save_prompt_text and _pop_prompt_text."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_prompt_text"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_save_and_pop(self):
        tg._save_prompt_text("w4", "partial input")
        result = tg._pop_prompt_text("w4")
        self.assertEqual(result, "partial input")
        # File should be deleted
        self.assertIsNone(tg._pop_prompt_text("w4"))

    def test_pop_empty(self):
        self.assertIsNone(tg._pop_prompt_text("w99"))


class TestPaneIdleState(unittest.TestCase):
    """Test _pane_idle_state detects idle/busy and typed text."""

    @patch.object(tg.tmux, "_capture_pane")
    def test_idle_no_text(self, mock_capture):
        mock_capture.return_value = "some output\n  ❯ \n"
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)
        self.assertEqual(typed, "")

    @patch.object(tg.tmux, "_capture_pane")
    def test_idle_with_text(self, mock_capture):
        mock_capture.return_value = "some output\n  ❯ partial command\n"
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)
        self.assertEqual(typed, "partial command")

    @patch.object(tg.tmux, "_get_cursor_x", return_value=7)
    @patch.object(tg.tmux, "_capture_pane")
    def test_idle_filters_suggestion(self, mock_capture, mock_cursor):
        """Cursor at col 7 means only 'fix' is typed, rest is suggestion."""
        #                0123456789...
        mock_capture.return_value = "  ❯ fix the bug in auth\n"
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)
        self.assertEqual(typed, "fix")

    @patch.object(tg.tmux, "_get_cursor_x", return_value=4)
    @patch.object(tg.tmux, "_capture_pane")
    def test_idle_cursor_at_prompt_no_text(self, mock_capture, mock_cursor):
        """Cursor right after ❯ means no typed text, even with suggestion."""
        mock_capture.return_value = "  ❯ suggest something\n"
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)
        self.assertEqual(typed, "")

    @patch.object(tg.tmux, "_capture_pane")
    def test_busy(self, mock_capture):
        mock_capture.return_value = "● Working on something\n  Processing files...\n"
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertFalse(is_idle)
        self.assertEqual(typed, "")

    @patch.object(tg.tmux, "_capture_pane")
    def test_old_prompt_in_scrollback_is_busy(self, mock_capture):
        """Old ❯ from submitted command should not count as idle."""
        mock_capture.return_value = "❯ test\n● Working on something\n  Processing files...\n"
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertFalse(is_idle)
        self.assertEqual(typed, "")

    @patch.object(tg.tmux, "_capture_pane")
    def test_prompt_after_output_is_idle(self, mock_capture):
        """New ❯ prompt after output means idle."""
        mock_capture.return_value = "● Done with task\n  Result: 42\n\n❯ \n"
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)
        self.assertEqual(typed, "")

    @patch.object(tg.tmux, "_capture_pane")
    def test_idle_with_ui_chrome_below(self, mock_capture):
        """❯ prompt followed by separator and hint lines should be idle."""
        mock_capture.return_value = (
            "❯ \n"
            "─────────────────────────────────\n"
            "  ⏵⏵ accept edits on (shift+tab to cycle) · esc to interrupt\n"
        )
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)
        self.assertEqual(typed, "")

    @patch.object(tg.tmux, "_capture_pane")
    def test_idle_with_text_and_chrome_below(self, mock_capture):
        """❯ with typed text followed by chrome should be idle with text."""
        mock_capture.return_value = (
            "❯ partial cmd\n"
            "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
            "  ⏵⏵ accept edits\n"
        )
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)
        self.assertEqual(typed, "partial cmd")

    @patch.object(tg.tmux, "_capture_pane")
    def test_idle_with_thinking_indicator_below(self, mock_capture):
        """❯ followed by thinking timing line should be idle."""
        mock_capture.return_value = (
            "❯ \n"
            "─────────────────────────────────\n"
            "* Percolating… (1m 14s · ↓ 1.8k tokens · thought for 71s)\n"
        )
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)
        self.assertEqual(typed, "")

    @patch.object(tg.tmux, "_capture_pane")
    def test_busy_with_working_spinner(self, mock_capture):
        """Working spinner on last line means busy."""
        mock_capture.return_value = "❯ test\n● Doing stuff\n⏳ Working...\n"
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertFalse(is_idle)

    @patch.object(tg.tmux, "_capture_pane")
    def test_all_empty_lines(self, mock_capture):
        mock_capture.return_value = "\n\n\n"
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertFalse(is_idle)

    @patch.object(tg.tmux, "_capture_pane", side_effect=Exception("tmux error"))
    def test_exception_returns_busy(self, mock_capture):
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertFalse(is_idle)
        self.assertEqual(typed, "")


class TestBusyState(unittest.TestCase):
    """Test _mark_busy, _is_busy, _clear_busy state file operations."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_busy_state"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_mark_and_check(self):
        self.assertFalse(tg._is_busy("w4"))
        tg._mark_busy("w4")
        self.assertTrue(tg._is_busy("w4"))

    def test_clear(self):
        tg._mark_busy("w4")
        tg._clear_busy("w4")
        self.assertFalse(tg._is_busy("w4"))

    def test_clear_nonexistent(self):
        tg._clear_busy("w99")  # should not raise

    def test_separate_sessions(self):
        tg._mark_busy("w4")
        self.assertTrue(tg._is_busy("w4"))
        self.assertFalse(tg._is_busy("w5"))

    def test_cleanup_removes_dead_sessions(self):
        """Busy files for sessions not in active_sessions are removed."""
        tg._mark_busy("w4")
        tg._mark_busy("w5")
        active = {"4": ("0:4.0", "proj")}  # w5 is gone
        tg._cleanup_stale_busy(active)
        self.assertTrue(tg._is_busy("w4"))
        self.assertFalse(tg._is_busy("w5"))

    def test_cleanup_empty_sessions(self):
        """All busy files removed when no sessions active."""
        tg._mark_busy("w4")
        tg._cleanup_stale_busy({})
        self.assertFalse(tg._is_busy("w4"))


class TestRouteToPane_BusyDetection(unittest.TestCase):
    """Test route_to_pane busy detection and prompt text save."""

    def setUp(self):
        self.pane = "0:4.0"
        self.win_idx = "4"
        self.signal_dir = "/tmp/tg_hook_test_route_busy"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(False, ""))
    @patch.object(tg.state, "load_active_prompt", return_value=None)
    def test_busy_queues_message(self, mock_prompt, mock_idle, mock_run):
        result = tg.route_to_pane(self.pane, self.win_idx, "hello")
        self.assertIn("Saved", result)
        self.assertIn("busy", result)
        # Message should be queued
        msgs = tg._load_queued_msgs("w4")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "hello")
        # No subprocess call (no send-keys)
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, "existing text"))
    @patch.object(tg.state, "load_active_prompt", return_value=None)
    def test_idle_with_text_saves_and_clears(self, mock_prompt, mock_idle, mock_run):
        result = tg.route_to_pane(self.pane, self.win_idx, "new msg")
        self.assertIn("Sent to", result)
        # Should have saved the existing text to queued messages
        msgs = tg._load_queued_msgs("w4")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "existing text")
        # Should have called Escape + send-keys
        self.assertEqual(mock_run.call_count, 2)  # Escape + send-keys
        esc_cmd = mock_run.call_args_list[0][0][0][2]
        self.assertIn("Escape", esc_cmd)

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    @patch.object(tg.state, "load_active_prompt", return_value=None)
    def test_idle_no_text_sends_normally(self, mock_prompt, mock_idle, mock_run):
        result = tg.route_to_pane(self.pane, self.win_idx, "hello")
        self.assertIn("Sent to", result)
        # Only one subprocess call (send-keys)
        self.assertEqual(mock_run.call_count, 1)

    @patch("subprocess.run")
    @patch.object(tg.state, "load_active_prompt", return_value=None)
    def test_busy_file_queues_subsequent_messages(self, mock_prompt, mock_run):
        """After sending, _busy file prevents subsequent messages when pane is busy."""
        # First call: pane idle → sends. Second call: pane busy → queues.
        with patch.object(tg.routing, "_pane_idle_state", return_value=(True, "")):
            result1 = tg.route_to_pane(self.pane, self.win_idx, "first")
        self.assertIn("Sent to", result1)
        self.assertTrue(tg._is_busy("w4"))
        with patch.object(tg.routing, "_pane_idle_state", return_value=(False, "")):
            result2 = tg.route_to_pane(self.pane, self.win_idx, "second")
        self.assertIn("Saved", result2)
        msgs = tg._load_queued_msgs("w4")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "second")

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    @patch.object(tg.state, "load_active_prompt", return_value=None)
    def test_busy_cleared_allows_send(self, mock_prompt, mock_idle, mock_run):
        """After _clear_busy, messages send normally again."""
        tg._mark_busy("w4")
        tg._clear_busy("w4")
        result = tg.route_to_pane(self.pane, self.win_idx, "hello")
        self.assertIn("Sent to", result)

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    @patch.object(tg.state, "load_active_prompt", return_value=None)
    @patch("time.sleep")
    def test_busy_self_heals_when_pane_idle(self, mock_sleep, mock_prompt, mock_idle, mock_run):
        """If busy file exists but pane is idle and grace period passed, self-heal and send."""
        tg._mark_busy("w4")
        # Pretend busy was set 10s ago (past the 5s grace period)
        with patch.object(tg.state, "_busy_since", return_value=time.time() - 10):
            result = tg.route_to_pane(self.pane, self.win_idx, "hello")
        self.assertIn("Sent to", result)
        # Busy file should be re-set (cleared then re-marked by send)
        self.assertTrue(tg._is_busy("w4"))
        # Double-check delay should have been called
        mock_sleep.assert_called_with(0.5)

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    @patch.object(tg.state, "load_active_prompt", return_value=None)
    def test_busy_grace_period_queues(self, mock_prompt, mock_idle, mock_run):
        """Within 5s grace period, busy file is trusted even if pane looks idle."""
        tg._mark_busy("w4")
        # busy_since is just now — within grace period
        result = tg.route_to_pane(self.pane, self.win_idx, "hello")
        self.assertIn("Saved", result)

    @patch("subprocess.run")
    @patch.object(tg.state, "load_active_prompt", return_value=None)
    @patch("time.sleep")
    def test_busy_self_heal_double_check_catches_transient(self, mock_sleep, mock_prompt, mock_run):
        """If first idle check passes but second fails, queue the message."""
        tg._mark_busy("w4")
        # First check: idle (transient). Second check: busy (real state).
        with patch.object(tg.routing, "_pane_idle_state",
                          side_effect=[(True, ""), (False, "")]):
            with patch.object(tg.state, "_busy_since", return_value=time.time() - 10):
                result = tg.route_to_pane(self.pane, self.win_idx, "hello")
        self.assertIn("Saved", result)
        mock_sleep.assert_called_once_with(0.5)


class TestSavedCommand(unittest.TestCase):
    """Test /saved command."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_saved_cmd"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_saved_empty(self, mock_send):
        action, _, _ = tg._handle_command("/saved", self.sessions, None)
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("No saved messages", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_saved_with_messages(self, mock_send):
        tg._save_queued_msg("w4", "hello there")
        action, _, _ = tg._handle_command("/saved", self.sessions, None)
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("1 saved message", msg)
        self.assertIn("hello there", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_saved_specific_session(self, mock_send):
        tg._save_queued_msg("w4", "msg for w4")
        action, _, _ = tg._handle_command("/saved w4", self.sessions, None)
        self.assertIsNone(action)
        msg = mock_send.call_args[0][0]
        self.assertIn("msg for w4", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_saved_specific_session_empty(self, mock_send):
        action, _, _ = tg._handle_command("/saved w4", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No saved messages", msg)


class TestSavedCallbacks(unittest.TestCase):
    """Test saved_send and saved_discard callbacks."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_saved_cb"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent to `w4`:\n`hello`")
    def test_saved_send(self, mock_route, mock_send, mock_answer, mock_remove):
        tg._save_queued_msg("w4", "hello")
        callback = {"id": "cb1", "data": "saved_send_w4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_route.assert_called_once_with("0:4.0", "4", "hello")
        self.assertEqual(last, "4")
        # Queue should be empty now
        self.assertEqual(tg._load_queued_msgs("w4"), [])

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent to `w4`:\n`a\nb`")
    def test_saved_send_multiple(self, mock_route, mock_send, mock_answer, mock_remove):
        tg._save_queued_msg("w4", "a")
        tg._save_queued_msg("w4", "b")
        callback = {"id": "cb1", "data": "saved_send_w4", "message_id": 42}
        tg._handle_callback(callback, self.sessions, None)
        # Should combine with newlines
        mock_route.assert_called_once_with("0:4.0", "4", "a\nb")

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_saved_discard(self, mock_send, mock_answer, mock_remove):
        tg._save_queued_msg("w4", "hello")
        callback = {"id": "cb1", "data": "saved_discard_w4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("Discarded", msg)
        # Queue should be empty
        self.assertEqual(tg._load_queued_msgs("w4"), [])



class TestSavedAlias(unittest.TestCase):
    """Test sv alias resolves to /saved."""

    def test_sv_alias(self):
        resolved = tg._resolve_alias("sv", has_active_prompt=False)
        self.assertEqual(resolved, "/saved")

    def test_sv_alias_suppressed_during_prompt(self):
        resolved = tg._resolve_alias("sv", has_active_prompt=True)
        self.assertEqual(resolved, "sv")


class TestHelpIncludesSaved(unittest.TestCase):
    """Verify /saved appears in help text."""

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "scan_claude_sessions", return_value={})
    def test_help_has_saved(self, mock_scan, mock_send):
        tg._handle_command("/help", {}, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("/saved", msg)
        self.assertIn("sv", msg)


class TestSmartFocusState(unittest.TestCase):
    """Test smart focus state file operations."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_smartfocus"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_save_and_load_roundtrip(self):
        tg._save_smartfocus_state("4", "0:4.0", "myproj")
        st = tg._load_smartfocus_state()
        self.assertEqual(st, {"wid": "4", "pane": "0:4.0", "project": "myproj"})

    def test_load_missing_returns_none(self):
        self.assertIsNone(tg._load_smartfocus_state())

    def test_clear_removes_file(self):
        tg._save_smartfocus_state("4", "0:4.0", "myproj")
        tg._clear_smartfocus_state()
        self.assertIsNone(tg._load_smartfocus_state())

    def test_survives_clear_signals_without_state(self):
        tg._save_smartfocus_state("4", "0:4.0", "myproj")
        tg._clear_signals(include_state=False)
        self.assertIsNotNone(tg._load_smartfocus_state())

    def test_cleared_by_clear_signals_with_state(self):
        tg._save_smartfocus_state("4", "0:4.0", "myproj")
        tg._clear_signals(include_state=True)
        self.assertIsNone(tg._load_smartfocus_state())


class TestSmartFocusActivation(unittest.TestCase):
    """Test _maybe_activate_smartfocus logic."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_sf_activate"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_normal_send_activates(self):
        """Message sent successfully → smart focus activates."""
        tg._maybe_activate_smartfocus("4", "0:4.0", "myproj",
                                      "📨 Sent to `w4`:\n`fix the bug`")
        st = tg._load_smartfocus_state()
        self.assertIsNotNone(st)
        self.assertEqual(st["wid"], "4")

    def test_queued_does_not_activate(self):
        """Message queued (busy) → no smart focus."""
        tg._maybe_activate_smartfocus("4", "0:4.0", "myproj",
                                      "💾 Saved for `w4` (busy):\n`fix`")
        self.assertIsNone(tg._load_smartfocus_state())

    def test_prompt_reply_does_not_activate(self):
        """Prompt reply → no smart focus."""
        tg._maybe_activate_smartfocus("4", "0:4.0", "myproj",
                                      "📨 Selected option 1 in `w4`")
        self.assertIsNone(tg._load_smartfocus_state())

    def test_skips_when_focus_active_same_wid(self):
        """Manual focus on same wid → skip smart focus."""
        tg._save_focus_state("4", "0:4.0", "myproj")
        tg._maybe_activate_smartfocus("4", "0:4.0", "myproj",
                                      "📨 Sent to `w4`:\n`fix`")
        self.assertIsNone(tg._load_smartfocus_state())

    def test_skips_when_deepfocus_active_same_wid(self):
        """Deep focus on same wid → skip smart focus."""
        tg._save_deepfocus_state("4", "0:4.0", "myproj")
        tg._maybe_activate_smartfocus("4", "0:4.0", "myproj",
                                      "📨 Sent to `w4`:\n`fix`")
        self.assertIsNone(tg._load_smartfocus_state())

    def test_activates_when_focus_on_different_wid(self):
        """Manual focus on w5, sending to w4 → smart focus activates."""
        tg._save_focus_state("5", "0:5.0", "other")
        tg._maybe_activate_smartfocus("4", "0:4.0", "myproj",
                                      "📨 Sent to `w4`:\n`fix`")
        st = tg._load_smartfocus_state()
        self.assertIsNotNone(st)
        self.assertEqual(st["wid"], "4")

    def test_switching_sessions_overwrites(self):
        """Sending to w5 after w4 → smart focus moves to w5."""
        tg._maybe_activate_smartfocus("4", "0:4.0", "projA",
                                      "📨 Sent to `w4`:\n`fix`")
        tg._maybe_activate_smartfocus("5", "0:5.0", "projB",
                                      "📨 Sent to `w5`:\n`test`")
        st = tg._load_smartfocus_state()
        self.assertEqual(st["wid"], "5")


class TestStopSignalClearsSmartFocus(unittest.TestCase):
    """Test that stop signal clears smart focus for matching wid."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_sf_stop"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, wid="w4", **extra):
        signal = {"event": event, "pane": "%20", "wid": wid, "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.telegram, "_send_long_message")
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_matching_wid_cleared(self, mock_sleep, mock_run, mock_proj,
                                   mock_long, mock_send):
        """Stop signal for w4 clears smart focus on w4."""
        tg._save_smartfocus_state("4", "0:4.0", "proj")
        self._write_signal("stop", wid="w4")
        tg.process_signals(focused_wids={"4"})
        self.assertIsNone(tg._load_smartfocus_state())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.telegram, "_send_long_message")
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_non_matching_wid_preserved(self, mock_sleep, mock_run, mock_proj,
                                         mock_long, mock_send):
        """Stop signal for w5 does NOT clear smart focus on w4."""
        tg._save_smartfocus_state("4", "0:4.0", "proj")
        self._write_signal("stop", wid="w5")
        tg.process_signals()
        st = tg._load_smartfocus_state()
        self.assertIsNotNone(st)
        self.assertEqual(st["wid"], "4")


class TestUnfocusClearsSmartFocus(unittest.TestCase):
    """Test that /unfocus clears smart focus."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_sf_unfocus"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_unfocus_clears_smartfocus(self, mock_send):
        tg._save_smartfocus_state("4", "0:4.0", "myproj")
        tg._handle_command("/unfocus", self.sessions, None)
        self.assertIsNone(tg._load_smartfocus_state())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_focus_clears_smartfocus(self, mock_run, mock_send):
        """Manual /focus clears smart focus."""
        mock_run.return_value = MagicMock(stdout="content\n")
        tg._save_smartfocus_state("4", "0:4.0", "myproj")
        tg._handle_command("/focus w4", self.sessions, None)
        self.assertIsNone(tg._load_smartfocus_state())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_deepfocus_clears_smartfocus(self, mock_run, mock_send):
        """Manual /deepfocus clears smart focus."""
        mock_run.return_value = MagicMock(stdout="content\n")
        tg._save_smartfocus_state("4", "0:4.0", "myproj")
        tg._handle_command("/deepfocus w4", self.sessions, None)
        self.assertIsNone(tg._load_smartfocus_state())


class TestSmartFocusIntegration(unittest.TestCase):
    """Test smart focus wires through _handle_command message routing."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_sf_integration"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent to `w4`:\n`fix`")
    def test_wN_prefix_activates_smartfocus(self, mock_route, mock_send):
        """Sending 'w4 fix' activates smart focus on w4."""
        tg._handle_command("w4 fix the bug", self.sessions, None)
        st = tg._load_smartfocus_state()
        self.assertIsNotNone(st)
        self.assertEqual(st["wid"], "4")

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="💾 Saved for `w4` (busy):\n`fix`")
    def test_wN_prefix_queued_no_smartfocus(self, mock_route, mock_send):
        """Queued message does NOT activate smart focus."""
        tg._handle_command("w4 fix the bug", self.sessions, None)
        self.assertIsNone(tg._load_smartfocus_state())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.routing, "route_to_pane", return_value="📨 Sent to `w4`:\n`fix`")
    def test_default_session_activates_smartfocus(self, mock_route, mock_send):
        """Single session message activates smart focus."""
        single = {"4": ("0:4.0", "myproj")}
        tg._handle_command("fix the bug", single, None)
        st = tg._load_smartfocus_state()
        self.assertIsNotNone(st)
        self.assertEqual(st["wid"], "4")


class TestGodModeState(unittest.TestCase):
    """Test god mode state functions."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_god_state"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_default_off(self):
        self.assertFalse(tg._is_god_mode_for("4"))
        self.assertEqual(tg._god_mode_wids(), [])

    def test_enable_per_session(self):
        tg._set_god_mode("4", True)
        self.assertTrue(tg._is_god_mode_for("4"))
        self.assertFalse(tg._is_god_mode_for("5"))
        self.assertEqual(tg._god_mode_wids(), ["4"])

    def test_enable_all(self):
        tg._set_god_mode("all", True)
        self.assertTrue(tg._is_god_mode_for("4"))
        self.assertTrue(tg._is_god_mode_for("99"))
        self.assertIn("all", tg._god_mode_wids())

    def test_disable_per_session(self):
        tg._set_god_mode("4", True)
        tg._set_god_mode("5", True)
        tg._set_god_mode("4", False)
        self.assertFalse(tg._is_god_mode_for("4"))
        self.assertTrue(tg._is_god_mode_for("5"))

    def test_clear_god_mode(self):
        tg._set_god_mode("4", True)
        tg._set_god_mode("5", True)
        tg._clear_god_mode()
        self.assertFalse(tg._is_god_mode_for("4"))
        self.assertFalse(tg._is_god_mode_for("5"))
        self.assertEqual(tg._god_mode_wids(), [])

    def test_disable_last_removes_file(self):
        tg._set_god_mode("4", True)
        tg._set_god_mode("4", False)
        self.assertFalse(os.path.exists(tg.config.GOD_MODE_PATH))

    def test_no_duplicate_wids(self):
        tg._set_god_mode("4", True)
        tg._set_god_mode("4", True)
        self.assertEqual(tg._god_mode_wids(), ["4"])

    def test_survives_clear_signals(self):
        """God mode state survives _clear_signals — stored outside signal dir."""
        # Point GOD_MODE_PATH outside signal dir (like production ~/.config/)
        ext_path = self.signal_dir + "_god_persistent"
        os.makedirs(ext_path, exist_ok=True)
        tg.config.GOD_MODE_PATH = os.path.join(ext_path, "_god_mode.json")
        try:
            tg._set_god_mode("4", True)
            tg._clear_signals(include_state=True)
            self.assertTrue(tg._is_god_mode_for("4"))
        finally:
            import shutil
            shutil.rmtree(ext_path, ignore_errors=True)
            tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")


class TestGodModeAutoAccept(unittest.TestCase):
    """Test permission auto-accept in god mode."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_god_accept"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, wid, pane="0:4.0", project="myproj", **extra):
        import time as t
        signal = {"event": event, "pane": pane, "wid": wid, "project": project, **extra}
        fname = f"{t.time():.6f}_test.json"
        path = os.path.join(self.signal_dir, fname)
        with open(path, "w") as f:
            json.dump(signal, f)

    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.content, "_extract_pane_permission", return_value=("wants to run bash", "ls -la", ["1. Yes", "2. Always", "3. Deny"], ""))
    @patch.object(tg.tmux, "get_pane_project", return_value="myproj")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_mode_auto_accepts(self, mock_send, mock_proj, mock_extract, mock_select):
        """Permission in god-mode session is auto-accepted."""
        tg._set_god_mode("4", True)
        self._write_signal("permission", "w4", cmd="ls -la")
        tg.signals.process_signals()
        mock_select.assert_called_once_with("0:4.0", 1)
        msg = mock_send.call_args[0][0]
        self.assertIn("Auto-allowed", msg)
        self.assertIn("ls -la", msg)
        # No active prompt should be saved
        self.assertIsNone(tg.state.load_active_prompt("w4"))

    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.content, "_extract_pane_permission", return_value=("wants to run bash", "rm -rf /", ["1. Yes", "2. Always", "3. Deny"], ""))
    @patch.object(tg.tmux, "get_pane_project", return_value="myproj")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.state, "save_active_prompt")
    def test_non_god_session_normal_flow(self, mock_save_prompt, mock_send, mock_proj, mock_extract, mock_select):
        """Permission in non-god session follows normal flow."""
        tg._set_god_mode("5", True)  # god mode for w5, not w4
        self._write_signal("permission", "w4", cmd="rm -rf /")
        tg.signals.process_signals()
        mock_select.assert_not_called()
        mock_save_prompt.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertIn("needs permission", msg)

    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.content, "_extract_pane_permission", return_value=("wants to edit", "file.py", ["1. Yes", "2. Always", "3. Deny"], ""))
    @patch.object(tg.tmux, "get_pane_project", return_value="myproj")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_all_accepts_any_session(self, mock_send, mock_proj, mock_extract, mock_select):
        """God mode 'all' auto-accepts for any session."""
        tg._set_god_mode("all", True)
        self._write_signal("permission", "w7", pane="0:7.0")
        tg.signals.process_signals()
        mock_select.assert_called_once_with("0:7.0", 1)
        msg = mock_send.call_args[0][0]
        self.assertIn("Auto-allowed", msg)

    @patch.object(tg.content, "_extract_pane_permission", return_value=("", "", [], ""))
    @patch.object(tg.tmux, "get_pane_project", return_value="myproj")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_mode_receipt_has_header_when_no_cmd(self, mock_send, mock_proj, mock_extract):
        """Receipt uses perm_header when no bash_cmd."""
        tg._set_god_mode("4", True)
        self._write_signal("permission", "w4")
        with patch.object(tg.routing, "_select_option"):
            tg.signals.process_signals()
        msg = mock_send.call_args[0][0]
        self.assertIn("Auto-allowed", msg)
        self.assertIn("permission", msg)

    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.content, "_extract_pane_permission",
                  return_value=("wants to execute plan", "", ["1. Yes", "2. No"], ""))
    @patch.object(tg.tmux, "get_pane_project", return_value="myproj")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.state, "save_active_prompt")
    def test_plan_permission_not_auto_accepted_in_god_mode(self, mock_save_prompt, mock_send, mock_proj, mock_extract, mock_select):
        """ExitPlanMode permission (message contains 'plan') is NOT auto-accepted in god mode."""
        tg._set_god_mode("4", True)
        self._write_signal("permission", "w4",
                           message="Claude has written up a plan and is ready to execute. Would you like to proceed?")
        tg.signals.process_signals()
        mock_select.assert_not_called()
        mock_save_prompt.assert_called_once()
        msg = mock_send.call_args[0][0]
        self.assertNotIn("Auto-allowed", msg)


class TestGodModeCommand(unittest.TestCase):
    """Test /god command handling."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")}
        self.signal_dir = "/tmp/tg_hook_test_god_cmd"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_bare_god_shows_status_off(self, mock_send, mock_scan):
        tg._handle_command("/god", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("off", msg)

    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_bare_god_shows_status_on(self, mock_send, mock_scan):
        tg._set_god_mode("4", True)
        tg._handle_command("/god", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("on", msg)
        self.assertIn("w4", msg)

    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    @patch.object(tg.commands, "_enable_accept_edits")
    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_w4_enables(self, mock_send, mock_scan, mock_accept, mock_idle):
        tg._handle_command("/god w4", self.sessions, None)
        self.assertTrue(tg._is_god_mode_for("4"))
        msg = mock_send.call_args[0][0]
        self.assertIn("on", msg)
        mock_accept.assert_called_once_with("0:4.0")

    @patch.object(tg.routing, "_pane_idle_state", return_value=(False, ""))
    @patch.object(tg.commands, "_enable_accept_edits")
    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_w4_busy_skips_accept_edits(self, mock_send, mock_scan, mock_accept, mock_idle):
        tg._handle_command("/god w4", self.sessions, None)
        self.assertTrue(tg._is_god_mode_for("4"))
        mock_accept.assert_not_called()

    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    @patch.object(tg.commands, "_enable_accept_edits")
    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj"), "5": ("0:5.0", "other")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_all_enables(self, mock_send, mock_scan, mock_accept, mock_idle):
        tg._handle_command("/god all", self.sessions, None)
        self.assertTrue(tg._is_god_mode_for("4"))
        self.assertTrue(tg._is_god_mode_for("5"))
        # Should cycle accept-edits for all idle sessions
        self.assertEqual(mock_accept.call_count, 2)

    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_off_disables_all(self, mock_send, mock_scan):
        tg._set_god_mode("4", True)
        tg._set_god_mode("5", True)
        tg._handle_command("/god off", self.sessions, None)
        self.assertFalse(tg._is_god_mode_for("4"))
        self.assertFalse(tg._is_god_mode_for("5"))
        msg = mock_send.call_args[0][0]
        self.assertIn("off", msg)

    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_off_w4(self, mock_send, mock_scan):
        tg._set_god_mode("4", True)
        tg._set_god_mode("5", True)
        tg._handle_command("/god off w4", self.sessions, None)
        self.assertFalse(tg._is_god_mode_for("4"))
        self.assertTrue(tg._is_god_mode_for("5"))

    def test_alias_g4(self):
        self.assertEqual(tg._resolve_alias("g4", False), "/god w4")

    def test_alias_ga(self):
        self.assertEqual(tg._resolve_alias("ga", False), "/god all")

    def test_alias_goff(self):
        self.assertEqual(tg._resolve_alias("goff", False), "/god off")

    def test_alias_g4_with_active_prompt(self):
        """g4 alias always resolves, even with active prompt."""
        self.assertEqual(tg._resolve_alias("g4", True), "/god w4")

    def test_alias_ga_suppressed_with_active_prompt(self):
        """ga is ambiguous during prompts."""
        self.assertEqual(tg._resolve_alias("ga", True), "ga")


class TestGodModeAcceptEditsOnStop(unittest.TestCase):
    """Test accept-edits cycling on stop signal for god mode sessions."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_god_stop"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, wid, pane="0:4.0", project="myproj"):
        import time as t
        signal = {"event": event, "pane": pane, "wid": wid, "project": project}
        fname = f"{t.time():.6f}_test.json"
        path = os.path.join(self.signal_dir, fname)
        with open(path, "w") as f:
            json.dump(signal, f)

    @patch.object(tg.commands, "_enable_accept_edits")
    @patch.object(tg.content, "_has_response_start", return_value=True)
    @patch.object(tg.content, "clean_pane_content", return_value="done")
    @patch.object(tg.tmux, "_capture_pane", return_value="● done\n❯")
    @patch.object(tg.tmux, "_get_pane_width", return_value=80)
    @patch.object(tg.tmux, "get_pane_project", return_value="myproj")
    @patch.object(tg.telegram, "_send_long_message")
    @patch("time.sleep")
    def test_stop_triggers_accept_edits_for_god_session(self, mock_sleep, mock_long,
                                                         mock_proj, mock_pw, mock_cap,
                                                         mock_clean, mock_has, mock_accept):
        tg._set_god_mode("4", True)
        self._write_signal("stop", "w4")
        tg.signals.process_signals()
        mock_accept.assert_called_once_with("0:4.0")

    @patch.object(tg.commands, "_enable_accept_edits")
    @patch.object(tg.content, "_has_response_start", return_value=True)
    @patch.object(tg.content, "clean_pane_content", return_value="done")
    @patch.object(tg.tmux, "_capture_pane", return_value="● done\n❯")
    @patch.object(tg.tmux, "_get_pane_width", return_value=80)
    @patch.object(tg.tmux, "get_pane_project", return_value="myproj")
    @patch.object(tg.telegram, "_send_long_message")
    @patch("time.sleep")
    def test_stop_no_accept_edits_without_god(self, mock_sleep, mock_long,
                                               mock_proj, mock_pw, mock_cap,
                                               mock_clean, mock_has, mock_accept):
        self._write_signal("stop", "w4")
        tg.signals.process_signals()
        mock_accept.assert_not_called()


class TestEnableAcceptEdits(unittest.TestCase):
    """Test _enable_accept_edits helper."""

    @patch("subprocess.run")
    @patch.object(tg.tmux, "_capture_pane", return_value="some output\n⏵⏵ accept edits on")
    def test_already_on(self, mock_cap, mock_run):
        """No BTab sent if accept edits already on."""
        tg._enable_accept_edits("0:4.0")
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch.object(tg.tmux, "_capture_pane", side_effect=[
        "some output\n⏵⏵ auto-accept",
        "some output\n⏵⏵ accept edits on",
    ])
    def test_cycles_once(self, mock_cap, mock_run):
        """Sends BTab once to cycle to accept edits on."""
        tg._enable_accept_edits("0:4.0")
        self.assertEqual(mock_run.call_count, 1)

    @patch("subprocess.run")
    @patch.object(tg.tmux, "_capture_pane", return_value="some output\n⏵⏵ auto-accept")
    def test_max_cycles(self, mock_cap, mock_run):
        """Stops after 5 cycles even if not found."""
        tg._enable_accept_edits("0:4.0")
        self.assertEqual(mock_run.call_count, 5)


class TestHelpIncludesGod(unittest.TestCase):
    """Test /help includes /god command."""

    @patch.object(tg.tmux, "scan_claude_sessions", return_value={})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_help_has_god(self, mock_send, mock_scan):
        tg._handle_command("/help", {}, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("/god", msg)
        self.assertIn("g4", msg)
        self.assertIn("ga", msg)
        self.assertIn("goff", msg)


class TestIsUiChrome(unittest.TestCase):
    """Test _is_ui_chrome pattern matching."""

    def test_empty_string(self):
        self.assertTrue(tg._is_ui_chrome(""))

    def test_separator_thin(self):
        self.assertTrue(tg._is_ui_chrome("────────────────"))

    def test_separator_thick(self):
        self.assertTrue(tg._is_ui_chrome("━━━━━━━━━━━━━━━━"))

    def test_accept_edits_on(self):
        self.assertTrue(tg._is_ui_chrome("⏵⏵ accept edits on"))

    def test_paused_indicator(self):
        self.assertTrue(tg._is_ui_chrome("⏸ paused"))

    def test_context_left(self):
        self.assertTrue(tg._is_ui_chrome("Context left until auto-compact: 50%"))

    def test_working_emoji(self):
        self.assertTrue(tg._is_ui_chrome("⏳ Working..."))

    def test_working_asterisk(self):
        self.assertTrue(tg._is_ui_chrome("* Working..."))

    def test_shortcut_hint(self):
        self.assertTrue(tg._is_ui_chrome("✻ esc for shortcuts"))

    def test_ctrl_background(self):
        self.assertTrue(tg._is_ui_chrome("ctrl+b to run in background"))

    def test_thinking_with_timing(self):
        self.assertTrue(tg._is_ui_chrome("* Percolating… (1m 14s · ↓ 1.8k tokens)"))

    def test_thinking_spinner(self):
        self.assertTrue(tg._is_ui_chrome("⠐ Thinking…"))

    def test_working_spinner(self):
        self.assertTrue(tg._is_ui_chrome("✶ Working…"))

    def test_more_lines(self):
        self.assertTrue(tg._is_ui_chrome("+12 more lines (ctrl+e to expand)"))

    def test_normal_text(self):
        self.assertFalse(tg._is_ui_chrome("Hello world"))

    def test_prompt(self):
        self.assertFalse(tg._is_ui_chrome("❯ test"))

    def test_bullet(self):
        self.assertFalse(tg._is_ui_chrome("● Response text"))

    def test_status_bar_esc_to_interrupt(self):
        self.assertTrue(tg._is_ui_chrome("1 file +2 -2 · esc to interrupt"))

    def test_status_bar_multiple_files(self):
        self.assertTrue(tg._is_ui_chrome("3 files +50 -10 · esc to interrupt"))

    def test_status_bar_file_count_no_interrupt(self):
        self.assertTrue(tg._is_ui_chrome("1 file +2 -2"))

    def test_short_separator_not_chrome(self):
        """Separators need at least 3 chars."""
        self.assertFalse(tg._is_ui_chrome("──"))


class TestFilterToolCalls(unittest.TestCase):
    """Test _filter_tool_calls removes tool bullets and continuations."""

    def test_removes_tool_bullet(self):
        lines = [
            "● Bash(echo hello)",
            "  ⎿  hello",
            "● The result is 42.",
        ]
        result = tg._filter_tool_calls(lines)
        self.assertEqual(result, ["● The result is 42."])

    def test_preserves_text_bullets(self):
        lines = [
            "● Here is the answer",
            "  The result is 42.",
        ]
        result = tg._filter_tool_calls(lines)
        self.assertEqual(result, lines)

    def test_multiple_tool_calls(self):
        lines = [
            "● Read(file.py)",
            "  content here",
            "● Bash(git status)",
            "  on main branch",
            "● Summary of changes",
        ]
        result = tg._filter_tool_calls(lines)
        self.assertEqual(result, ["● Summary of changes"])

    def test_empty_input(self):
        self.assertEqual(tg._filter_tool_calls([]), [])

    def test_no_tool_bullets(self):
        lines = ["normal line", "another line"]
        result = tg._filter_tool_calls(lines)
        self.assertEqual(result, lines)


class TestGodModeQuestionNotAutoAccepted(unittest.TestCase):
    """Test that god mode does NOT auto-accept AskUserQuestion signals."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_god_question"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.state, "save_active_prompt")
    def test_question_not_auto_accepted(self, mock_save, mock_send, mock_proj, mock_select):
        """AskUserQuestion should always show the question, even in god mode."""
        tg._set_god_mode("4", True)
        questions = [{"question": "Pick one?", "options": [
            {"label": "A", "description": "first"},
        ]}]
        self._write_signal("question", questions=questions)
        tg.process_signals()
        # Should NOT auto-accept
        mock_select.assert_not_called()
        # Should show the question normally
        msg = mock_send.call_args[0][0]
        self.assertIn("asks", msg)
        self.assertIn("Pick one?", msg)
        # Should save active prompt
        mock_save.assert_called_once()


class TestGodModeCallback(unittest.TestCase):
    """Test cmd_god callback handler."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.commands, "_handle_command", return_value=(None, {"4": ("0:4.0", "myproj")}, "4"))
    def test_cmd_god_callback(self, mock_cmd, mock_answer, mock_remove):
        callback = {"id": "cb1", "data": "cmd_god_4", "message_id": 42}
        sessions, last, action = tg._handle_callback(callback, self.sessions, None)
        mock_cmd.assert_called_once_with("/god w4", self.sessions, None)
        self.assertIsNone(action)


class TestGodModeUnknownArg(unittest.TestCase):
    """Test /god with unrecognized argument."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_god_unknown"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_unknown_arg(self, mock_send, mock_scan):
        tg._handle_command("/god xyz123", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)


class TestProcessSignalsClearsBusy(unittest.TestCase):
    """Test that stop signal clears busy state."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_stop_busy"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_stop_clears_busy(self, mock_sleep, mock_run, mock_proj, mock_send):
        tg._mark_busy("w4")
        self.assertTrue(tg._is_busy("w4"))
        self._write_signal("stop")
        tg.process_signals()
        self.assertFalse(tg._is_busy("w4"))


class TestProcessSignalsCorruptedJson(unittest.TestCase):
    """Test signal processing with corrupted signal files."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_corrupt_sig"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_corrupt_json_removed_no_crash(self):
        """Corrupted JSON signal file is removed without crashing."""
        fpath = os.path.join(self.signal_dir, f"{time.time():.6f}_test.json")
        with open(fpath, "w") as f:
            f.write("{corrupt json{{")
        tg.process_signals()  # should not raise
        self.assertFalse(os.path.exists(fpath))

    def test_empty_json_file_removed(self):
        """Empty signal file is removed without crashing."""
        fpath = os.path.join(self.signal_dir, f"{time.time():.6f}_test.json")
        with open(fpath, "w") as f:
            f.write("")
        tg.process_signals()
        self.assertFalse(os.path.exists(fpath))


class TestProcessSignalNoPane(unittest.TestCase):
    """Test stop signal with empty pane."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_no_pane"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("time.sleep")
    def test_stop_without_pane(self, mock_sleep, mock_send):
        """Stop signal with no pane sends fallback message."""
        signal = {"event": "stop", "pane": "", "wid": "w4", "project": "test"}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)
        tg.process_signals()
        msg = mock_send.call_args[0][0]
        self.assertIn("could not capture pane", msg)


class TestProcessSignalsReturnValue(unittest.TestCase):
    """Test process_signals return value."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_sig_return"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_no_signals_returns_none(self):
        result = tg.process_signals()
        self.assertIsNone(result)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● done\n❯"))
    @patch("time.sleep")
    def test_returns_last_wid(self, mock_sleep, mock_run, mock_proj, mock_send):
        signal = {"event": "stop", "pane": "%20", "wid": "w7", "project": "test"}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)
        result = tg.process_signals()
        self.assertEqual(result, "7")

    def test_nonexistent_dir_returns_none(self):
        tg.config.SIGNAL_DIR = "/tmp/tg_hook_nonexistent_xyz_123"
        result = tg.process_signals()
        self.assertIsNone(result)


class TestProcessSignalsMultiple(unittest.TestCase):
    """Test processing multiple signal files in order."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_multi_sig"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● done\n❯"))
    @patch("time.sleep")
    def test_processes_all_signals(self, mock_sleep, mock_run, mock_proj, mock_send):
        """Multiple stop signals are all processed."""
        for i, wid in enumerate(["w4", "w5"]):
            signal = {"event": "stop", "pane": f"%{20+i}", "wid": wid, "project": "test"}
            fname = f"1000000.{i:06d}_test.json"
            with open(os.path.join(self.signal_dir, fname), "w") as f:
                json.dump(signal, f)
        result = tg.process_signals()
        self.assertEqual(result, "5")  # last wid
        # Both signals should be processed (2 tg_send calls)
        self.assertEqual(mock_send.call_count, 2)
        # Signal files should be cleaned up
        remaining = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(remaining, [])


class TestAutofocusState(unittest.TestCase):
    """Test autofocus state management."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_af_state"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_default_enabled(self):
        self.assertTrue(tg._is_autofocus_enabled())

    def test_disable(self):
        tg._set_autofocus(False)
        self.assertFalse(tg._is_autofocus_enabled())

    def test_reenable(self):
        tg._set_autofocus(False)
        tg._set_autofocus(True)
        self.assertTrue(tg._is_autofocus_enabled())

    def test_disabled_blocks_smartfocus(self):
        """When autofocus is off, _maybe_activate_smartfocus does nothing."""
        tg._set_autofocus(False)
        tg._maybe_activate_smartfocus("4", "0:4.0", "proj",
                                      "📨 Sent to `w4`:\n`fix`")
        self.assertIsNone(tg._load_smartfocus_state())


class TestAutofocusCommand(unittest.TestCase):
    """Test /autofocus command handling."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_af_cmd"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_toggle_off(self, mock_send):
        """Toggle from default on to off."""
        tg._handle_command("/autofocus", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("off", msg)
        self.assertFalse(tg._is_autofocus_enabled())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_toggle_on(self, mock_send):
        """Toggle from off to on."""
        tg._set_autofocus(False)
        tg._handle_command("/autofocus", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("on", msg)
        self.assertTrue(tg._is_autofocus_enabled())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_explicit_on(self, mock_send):
        tg._set_autofocus(False)
        tg._handle_command("/autofocus on", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("on", msg)
        self.assertTrue(tg._is_autofocus_enabled())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_explicit_off(self, mock_send):
        tg._handle_command("/autofocus off", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("off", msg)
        self.assertFalse(tg._is_autofocus_enabled())

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_off_clears_smartfocus(self, mock_send):
        """Turning off autofocus clears any active smart focus."""
        tg._save_smartfocus_state("4", "0:4.0", "proj")
        tg._handle_command("/autofocus off", self.sessions, None)
        self.assertIsNone(tg._load_smartfocus_state())

    def test_af_alias(self):
        self.assertEqual(tg._resolve_alias("af", False), "/autofocus")

    def test_af_alias_suppressed_during_prompt(self):
        self.assertEqual(tg._resolve_alias("af", True), "af")


class TestEnableAcceptEditsEdgeCases(unittest.TestCase):
    """Test _enable_accept_edits edge cases."""

    @patch("subprocess.run")
    @patch.object(tg.tmux, "_capture_pane", side_effect=Exception("tmux error"))
    def test_capture_exception_returns(self, mock_cap, mock_run):
        """Exception during capture_pane returns without error."""
        tg._enable_accept_edits("0:4.0")
        mock_run.assert_not_called()

    @patch("subprocess.run")
    @patch.object(tg.tmux, "_capture_pane", return_value="no mode indicator here")
    def test_no_mode_line_sends_btab(self, mock_cap, mock_run):
        """No ⏵⏵ line found → sends BTab."""
        tg._enable_accept_edits("0:4.0")
        self.assertEqual(mock_run.call_count, 5)  # max cycles


class TestPermissionContextInMessage(unittest.TestCase):
    """Test that response context appears in permission messages."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_perm_ctx"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.content, "_extract_pane_permission",
                  return_value=("wants to run bash", "", ["1. Yes", "2. No"], "I'll run git status to check"))
    @patch.object(tg.state, "save_active_prompt")
    def test_context_in_bash_permission(self, mock_save, mock_extract, mock_proj, mock_send):
        """Response context appears in bash permission message."""
        self._write_signal("permission", cmd="git status")
        tg.process_signals()
        msg = mock_send.call_args[0][0]
        self.assertIn("I'll run git status to check", msg)
        self.assertIn("git status", msg)


class TestQueuedMessagesPersistence(unittest.TestCase):
    """Test queued messages survive _clear_signals(include_state=True)."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_queued_persist"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_survives_clear_signals_with_state(self):
        """Queued messages persist through _clear_signals(include_state=True)."""
        tg._save_queued_msg("w4", "important message")
        tg._clear_signals(include_state=True)
        msgs = tg._load_queued_msgs("w4")
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["text"], "important message")


class TestStopSignalQueuedMessages(unittest.TestCase):
    """Test stop signal shows queued message notification."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_stop_queue"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch("subprocess.run", return_value=MagicMock(stdout="● Answer\n  42\n❯ prompt"))
    @patch("time.sleep")
    def test_stop_shows_queued_messages(self, mock_sleep, mock_run, mock_proj, mock_send):
        """Stop signal with queued messages shows notification."""
        tg._save_queued_msg("w4", "pending msg")
        self._write_signal("stop")
        tg.process_signals()
        # Should have 2 tg_send calls: stop message + queued notification
        self.assertEqual(mock_send.call_count, 2)
        queued_msg = mock_send.call_args_list[1][0][0]
        self.assertIn("1 saved message", queued_msg)
        self.assertIn("pending msg", queued_msg)


class TestGodModeFocusedWidsInteraction(unittest.TestCase):
    """Test god mode works correctly with focused_wids suppression."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_god_focus"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.content, "_extract_pane_permission",
                  return_value=("", "", ["1. Yes", "2. No"], ""))
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_permission_auto_accepted_while_focused(self, mock_send, mock_proj, mock_extract, mock_select):
        """Permission auto-accepted even when stop signals would be suppressed by focus."""
        tg._set_god_mode("4", True)
        self._write_signal("permission", cmd="git status")
        tg.process_signals(focused_wids={"4"})
        mock_select.assert_called_once_with("%20", 1)
        msg = mock_send.call_args[0][0]
        self.assertIn("Auto-allowed", msg)


class TestSavedCommandByName(unittest.TestCase):
    """Test /saved command with session name."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_saved_name"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")
        tg._save_session_name("4", "auth")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_saved_by_name(self, mock_send):
        tg._save_queued_msg("w4", "queued msg")
        tg._handle_command("/saved auth", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("queued msg", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_saved_nonexistent_name(self, mock_send):
        tg._handle_command("/saved nonexistent", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)


class TestStatusCommand(unittest.TestCase):
    """Test /status command variants."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch("subprocess.run")
    def test_status_with_explicit_lines(self, mock_run, mock_send):
        """Status with explicit line count uses that count."""
        mock_run.return_value = MagicMock(stdout="line1\nline2\nline3\n❯ prompt")
        tg._handle_command("/status w4 5", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("`myproj`", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_status_nonexistent_session(self, mock_send):
        tg._handle_command("/status w99", self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No session", msg)


class TestSendLongMessageWithFooter(unittest.TestCase):
    """Test _send_long_message footer parameter."""

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_footer_appended(self, mock_send):
        tg._send_long_message("H:\n", "body", wid="4", footer="1. Yes\n2. No")
        msg = mock_send.call_args[0][0]
        self.assertIn("```", msg)
        self.assertIn("1. Yes", msg)
        self.assertIn("2. No", msg)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_no_footer(self, mock_send):
        tg._send_long_message("H:\n", "body", wid="4")
        msg = mock_send.call_args[0][0]
        self.assertTrue(msg.endswith("```"))

    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_chunked_footer_on_last(self, mock_send):
        """Footer only appears after the last chunk."""
        line = "x" * 79 + "\n"
        body = line * 100
        tg._send_long_message("H:\n", body, wid="4", footer="opts")
        last_msg = mock_send.call_args_list[-1][0][0]
        self.assertIn("opts", last_msg)
        # Earlier chunks should not have footer
        for c in mock_send.call_args_list[:-1]:
            self.assertNotIn("opts", c[0][0])


class TestClearSignalsPreservation(unittest.TestCase):
    """Test _clear_signals preserves the right files."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_clear_sig"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_preserves_queued_names(self):
        """_persist prefixes survive include_state=True."""
        for fname, data in [
            ("_queued_w4.json", [{"text": "msg"}]),
            ("_names.json", {"4": "auth"}),
        ]:
            with open(os.path.join(self.signal_dir, fname), "w") as f:
                json.dump(data, f)
        # Add some state files that should be deleted
        for fname in ["_focus.json", "_busy_w4.json", "_active_prompt_w4.json"]:
            with open(os.path.join(self.signal_dir, fname), "w") as f:
                json.dump({}, f)
        tg._clear_signals(include_state=True)
        # Preserved
        self.assertTrue(os.path.exists(os.path.join(self.signal_dir, "_queued_w4.json")))
        self.assertTrue(os.path.exists(os.path.join(self.signal_dir, "_names.json")))
        # Deleted
        self.assertFalse(os.path.exists(os.path.join(self.signal_dir, "_focus.json")))
        self.assertFalse(os.path.exists(os.path.join(self.signal_dir, "_busy_w4.json")))
        self.assertFalse(os.path.exists(os.path.join(self.signal_dir, "_active_prompt_w4.json")))

    def test_without_state_preserves_all_underscore(self):
        """include_state=False preserves all _ files."""
        for fname in ["_focus.json", "_busy_w4.json"]:
            with open(os.path.join(self.signal_dir, fname), "w") as f:
                json.dump({}, f)
        # Regular signal
        with open(os.path.join(self.signal_dir, "123.json"), "w") as f:
            json.dump({}, f)
        tg._clear_signals(include_state=False)
        self.assertTrue(os.path.exists(os.path.join(self.signal_dir, "_focus.json")))
        self.assertTrue(os.path.exists(os.path.join(self.signal_dir, "_busy_w4.json")))
        self.assertFalse(os.path.exists(os.path.join(self.signal_dir, "123.json")))


class TestWidLabel(unittest.TestCase):
    """Test _wid_label formatting."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_wid_label"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_unnamed(self):
        self.assertEqual(tg._wid_label("4"), "`w4`")

    def test_named(self):
        tg._save_session_name("4", "auth")
        self.assertEqual(tg._wid_label("4"), "`w4 [auth]`")


class TestGodModeAutoAcceptNonBash(unittest.TestCase):
    """Test god mode auto-accept for non-bash permissions (edit, write)."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_god_nonbash"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.routing, "_select_option")
    @patch.object(tg.content, "_extract_pane_permission",
                  return_value=("wants to update `file.py`", "+new=True", ["1. Yes", "2. No"], ""))
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_edit_auto_accepted(self, mock_send, mock_proj, mock_extract, mock_select):
        """Non-bash edit permission auto-accepted in god mode."""
        tg._set_god_mode("4", True)
        self._write_signal("permission", cmd="")
        tg.process_signals()
        mock_select.assert_called_once_with("%20", 1)
        msg = mock_send.call_args[0][0]
        self.assertIn("Auto-allowed", msg)
        self.assertIn("wants to update", msg)


class TestPermissionOptionsFirstOptionInjection(unittest.TestCase):
    """Test that '1. Yes' is injected when options don't start with it."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_opt_inject"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def _write_signal(self, event, **extra):
        signal = {"event": event, "pane": "%20", "wid": "w4", "project": "test", **extra}
        fname = f"{time.time():.6f}_test.json"
        with open(os.path.join(self.signal_dir, fname), "w") as f:
            json.dump(signal, f)

    @patch.object(tg.telegram, "tg_send", return_value=1)
    @patch.object(tg.tmux, "get_pane_project", return_value="proj")
    @patch.object(tg.content, "_extract_pane_permission",
                  return_value=("", "", ["2. Yes, always", "3. No (esc)"], ""))
    @patch.object(tg.state, "save_active_prompt")
    def test_injects_yes_option(self, mock_save, mock_extract, mock_proj, mock_send):
        """When options don't start with 1., inject '1. Yes'."""
        self._write_signal("permission", cmd="echo test")
        tg.process_signals()
        msg = mock_send.call_args[0][0]
        self.assertIn("1. Yes", msg)


class TestRouteToPane_PromptPaneOverride(unittest.TestCase):
    """Test that active prompt uses prompt's pane, not routing pane."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_prompt_pane"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch("subprocess.run")
    def test_uses_prompt_pane(self, mock_run):
        """When prompt has a pane, use it for selection, not the routing pane."""
        prompt = {"pane": "%99", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "n": 3}}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
            tg.route_to_pane("0:4.0", "4", "y")
        cmd_str = mock_run.call_args[0][0][2]
        self.assertIn("%99", cmd_str)
        self.assertNotIn("0:4.0", cmd_str)


class TestRouteToPane_StalePromptDiscarded(unittest.TestCase):
    """Test that prompts with stale session:window.pane references are discarded."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_stale_prompt"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch("subprocess.run")
    @patch.object(tg.routing, "_pane_idle_state", return_value=(True, ""))
    def test_stale_session_pane_discarded(self, mock_idle, mock_run):
        """Prompt with old session name (0:4.0) is discarded when current is main:4.0."""
        prompt = {"pane": "0:4.0", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "n": 3}}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane("main:4.0", "4", "hello")
        # Message should be sent normally, not treated as prompt answer
        self.assertIn("Sent to", result)

    @patch("subprocess.run")
    def test_matching_pane_not_discarded(self, mock_run):
        """Prompt with matching session:window.pane is used normally."""
        prompt = {"pane": "main:4.0", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "n": 3}}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane("main:4.0", "4", "y")
        self.assertIn("Selected option", result)

    @patch("subprocess.run")
    def test_pane_id_format_not_discarded(self, mock_run):
        """Prompt with %N pane ID format is always used (no colon = not stale)."""
        prompt = {"pane": "%20", "total": 3, "ts": 0,
                  "shortcuts": {"y": 1, "n": 3}}
        with patch.object(tg.state, "load_active_prompt", return_value=prompt):
            result = tg.route_to_pane("main:4.0", "4", "y")
        self.assertIn("Selected option", result)


class TestBusySince(unittest.TestCase):
    """Test _busy_since timestamp retrieval."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_busy_since"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    def test_returns_none_when_not_busy(self):
        self.assertIsNone(tg._busy_since("w99"))

    def test_returns_timestamp(self):
        before = time.time()
        tg._mark_busy("w4")
        after = time.time()
        ts = tg._busy_since("w4")
        self.assertIsNotNone(ts)
        self.assertGreaterEqual(ts, before)
        self.assertLessEqual(ts, after)


class TestSavedCallbackEdgeCases(unittest.TestCase):
    """Test saved_send/saved_discard callback edge cases."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_saved_edge"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_send_empty_queue(self, mock_send, mock_answer, mock_remove):
        """saved_send with empty queue shows message."""
        callback = {"id": "cb1", "data": "saved_send_w4", "message_id": 42}
        tg._handle_callback(callback, self.sessions, None)
        msg = mock_send.call_args[0][0]
        self.assertIn("No saved messages to send", msg)

    @patch.object(tg.telegram, "_remove_inline_keyboard")
    @patch.object(tg.telegram, "_answer_callback_query")
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_send_dead_session(self, mock_send, mock_answer, mock_remove):
        """saved_send for session no longer in sessions shows warning."""
        tg._save_queued_msg("w5", "msg")
        callback = {"id": "cb1", "data": "saved_send_w5", "message_id": 42}
        tg._handle_callback(callback, self.sessions, None)  # w5 not in sessions
        msg = mock_send.call_args[0][0]
        self.assertIn("no longer active", msg)


class TestFormatQuestionMsg(unittest.TestCase):
    """Test _format_question_msg formatting."""

    def test_basic_question(self):
        q = {"question": "Which one?", "options": [
            {"label": "A", "description": "first"},
            {"label": "B", "description": "second"},
        ]}
        msg = tg._format_question_msg(" `w4`", "myproj", q)
        self.assertIn("Which one?", msg)
        self.assertIn("1. A — first", msg)
        self.assertIn("2. B — second", msg)
        self.assertIn("3. Type your answer", msg)
        self.assertIn("4. Chat about this", msg)

    def test_no_description(self):
        q = {"question": "Pick?", "options": [
            {"label": "X"},
        ]}
        msg = tg._format_question_msg("", "proj", q)
        self.assertIn("1. X", msg)
        self.assertNotIn("—", msg)

    def test_project_in_backticks(self):
        q = {"question": "Q?", "options": []}
        msg = tg._format_question_msg("", "my_proj", q)
        self.assertIn("`my_proj`", msg)


class TestSelectOption(unittest.TestCase):
    """Test _select_option arrow key navigation."""

    @patch("subprocess.run")
    def test_option_1_no_down(self, mock_run):
        tg._select_option("0:4.0", 1)
        cmd = mock_run.call_args[0][0][2]
        self.assertNotIn("Down", cmd)
        self.assertIn("Enter", cmd)

    @patch("subprocess.run")
    def test_option_3_two_downs(self, mock_run):
        tg._select_option("0:4.0", 3)
        cmd = mock_run.call_args[0][0][2]
        self.assertEqual(cmd.count("Down"), 2)
        self.assertIn("sleep 0.1", cmd)
        self.assertIn("Enter", cmd)


class TestCleanPaneStatus(unittest.TestCase):
    """Test clean_pane_status preserves thinking indicators."""

    def test_keeps_working_indicator(self):
        raw = "● Working\n⏳ Working...\n❯ prompt"
        result = tg.clean_pane_status(raw)
        self.assertIn("Working...", result)

    def test_keeps_thinking_timing(self):
        raw = "● Task\n* Percolating… (1m 14s · ↓ 1.8k tokens)\n❯ prompt"
        result = tg.clean_pane_status(raw)
        self.assertIn("Percolating", result)

    def test_still_filters_separator(self):
        raw = "● Task\n────────────\n❯ prompt"
        result = tg.clean_pane_status(raw)
        self.assertNotIn("────", result)


class TestGodModeGodOffByName(unittest.TestCase):
    """Test /god off with session name."""

    def setUp(self):
        self.sessions = {"4": ("0:4.0", "myproj")}
        self.signal_dir = "/tmp/tg_hook_test_god_off_name"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")
        tg._save_session_name("4", "auth")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_off_by_name(self, mock_send, mock_scan):
        tg._set_god_mode("4", True)
        tg._handle_command("/god off auth", self.sessions, None)
        self.assertFalse(tg._is_god_mode_for("4"))
        msg = mock_send.call_args[0][0]
        self.assertIn("off", msg)

    @patch.object(tg.tmux, "scan_claude_sessions", return_value={"4": ("0:4.0", "myproj")})
    @patch.object(tg.telegram, "tg_send", return_value=1)
    def test_god_by_name(self, mock_send, mock_scan):
        """Enable god mode using session name."""
        with patch.object(tg.routing, "_pane_idle_state", return_value=(False, "")):
            tg._handle_command("/god auth", self.sessions, None)
        self.assertTrue(tg._is_god_mode_for("4"))
        msg = mock_send.call_args[0][0]
        self.assertIn("on", msg)


class TestPollUpdates(unittest.TestCase):
    """Test _poll_updates helper."""

    @patch("requests.get")
    def test_returns_data_and_offset(self, mock_get):
        resp = MagicMock()
        resp.json.return_value = {"result": [{"update_id": 100}]}
        resp.raise_for_status = MagicMock()
        mock_get.return_value = resp
        data, offset = tg._poll_updates(0, timeout=1)
        self.assertIsNotNone(data)
        self.assertEqual(offset, 101)

    @patch("requests.get", side_effect=Exception("network error"))
    def test_error_returns_none(self, mock_get):
        data, offset = tg._poll_updates(50, timeout=1)
        self.assertIsNone(data)
        self.assertEqual(offset, 50)

    @patch("requests.get", side_effect=KeyboardInterrupt)
    def test_keyboard_interrupt_propagates(self, mock_get):
        with self.assertRaises(KeyboardInterrupt):
            tg._poll_updates(0, timeout=1)


class TestWriteSignal(unittest.TestCase):
    """Test write_signal creates correct signal files."""

    def setUp(self):
        self.signal_dir = "/tmp/tg_hook_test_write_sig"
        os.makedirs(self.signal_dir, exist_ok=True)
        self._orig_signal_dir = tg.config.SIGNAL_DIR
        self._orig_god_mode_path = tg.config.GOD_MODE_PATH
        tg.config.SIGNAL_DIR = self.signal_dir
        tg.config.GOD_MODE_PATH = os.path.join(self.signal_dir, "_god_mode.json")

    def tearDown(self):
        tg.config.SIGNAL_DIR = self._orig_signal_dir
        tg.config.GOD_MODE_PATH = self._orig_god_mode_path
        import shutil
        shutil.rmtree(self.signal_dir, ignore_errors=True)

    @patch.object(tg.tmux, "get_window_id", return_value="w4")
    def test_creates_signal_file(self, mock_wid):
        os.environ["TMUX_PANE"] = "%20"
        tg.write_signal("stop", {"cwd": "/home/user/myproj"})
        files = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        self.assertEqual(len(files), 1)
        with open(os.path.join(self.signal_dir, files[0])) as f:
            sig = json.load(f)
        self.assertEqual(sig["event"], "stop")
        self.assertEqual(sig["wid"], "w4")
        self.assertEqual(sig["pane"], "%20")
        self.assertEqual(sig["project"], "myproj")

    @patch.object(tg.tmux, "get_window_id", return_value="w4")
    def test_extra_fields(self, mock_wid):
        os.environ["TMUX_PANE"] = "%20"
        tg.write_signal("permission", {"cwd": "/tmp/p"}, cmd="echo hi")
        files = [f for f in os.listdir(self.signal_dir) if not f.startswith("_")]
        with open(os.path.join(self.signal_dir, files[0])) as f:
            sig = json.load(f)
        self.assertEqual(sig["cmd"], "echo hi")


class TestPaneIdleStateChromeOrder(unittest.TestCase):
    """Test _pane_idle_state with various chrome patterns below prompt."""

    @patch.object(tg.tmux, "_capture_pane")
    def test_prompt_above_context_line(self, mock_capture):
        """Prompt above 'Context left until...' line is idle."""
        mock_capture.return_value = (
            "❯ \n"
            "Context left until auto-compact: 33%\n"
        )
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)

    @patch.object(tg.tmux, "_capture_pane")
    def test_prompt_above_more_lines(self, mock_capture):
        """Prompt above '+N more lines' indicator is idle."""
        mock_capture.return_value = (
            "❯ \n"
            "+12 more lines (ctrl+e to expand)\n"
        )
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)

    @patch.object(tg.tmux, "_capture_pane")
    def test_prompt_above_status_bar(self, mock_capture):
        """Prompt above separator + status bar (esc to interrupt) is idle."""
        mock_capture.return_value = (
            "❯ \n"
            "─────────────────────────────────────────\n"
            "  1 file +2 -2 · esc to interrupt\n"
        )
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)

    @patch.object(tg.tmux, "_capture_pane")
    def test_prompt_above_status_bar_no_interrupt(self, mock_capture):
        """Prompt above separator + file count status bar is idle."""
        mock_capture.return_value = (
            "❯ \n"
            "─────────────────────────────────────────\n"
            "  3 files +50 -10\n"
        )
        is_idle, typed = tg._pane_idle_state("0:4.0")
        self.assertTrue(is_idle)


class TestGetImageDimensions(unittest.TestCase):
    """Test _get_image_dimensions parses PNG, JPEG, GIF headers."""

    def test_png_dimensions(self):
        """PNG with known dimensions returns correct (w, h)."""
        import tempfile
        # Minimal valid PNG: 8-byte sig + IHDR chunk (13 bytes data)
        sig = b"\x89PNG\r\n\x1a\n"
        ihdr_data = struct.pack(">II", 1920, 1080) + b"\x08\x02\x00\x00\x00"
        ihdr_length = struct.pack(">I", 13)
        ihdr = ihdr_length + b"IHDR" + ihdr_data
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(sig + ihdr)
            path = f.name
        try:
            w, h = tg._get_image_dimensions(path)
            self.assertEqual((w, h), (1920, 1080))
        finally:
            os.remove(path)

    def test_gif_dimensions(self):
        """GIF with known dimensions returns correct (w, h)."""
        import tempfile
        header = b"GIF89a" + struct.pack("<HH", 800, 600) + b"\x00" * 20
        with tempfile.NamedTemporaryFile(suffix=".gif", delete=False) as f:
            f.write(header)
            path = f.name
        try:
            w, h = tg._get_image_dimensions(path)
            self.assertEqual((w, h), (800, 600))
        finally:
            os.remove(path)

    def test_jpeg_dimensions(self):
        """JPEG with SOF0 marker returns correct (w, h)."""
        import tempfile
        soi = b"\xff\xd8"
        sof0_marker = b"\xff\xc0"
        sof0_length = struct.pack(">H", 11)
        sof0_data = b"\x08" + struct.pack(">HH", 2048, 3072) + b"\x03\x01\x11\x00"
        jpeg = soi + sof0_marker + sof0_length + sof0_data
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(jpeg)
            path = f.name
        try:
            w, h = tg._get_image_dimensions(path)
            self.assertEqual((w, h), (3072, 2048))
        finally:
            os.remove(path)

    def test_non_image_file(self):
        """Non-image file returns (0, 0)."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"just some text")
            path = f.name
        try:
            w, h = tg._get_image_dimensions(path)
            self.assertEqual((w, h), (0, 0))
        finally:
            os.remove(path)

    def test_missing_file(self):
        """Missing file returns (0, 0)."""
        w, h = tg._get_image_dimensions("/nonexistent/file.png")
        self.assertEqual((w, h), (0, 0))


class TestTgSendDocument(unittest.TestCase):
    """Test tg_send_document function."""

    @patch("requests.post")
    def test_send_document_with_caption(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"result": {"message_id": 50}}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"fake pdf data")
            path = f.name

        try:
            msg_id = tg.tg_send_document(path, "test doc")
            self.assertEqual(msg_id, 50)
            call_kwargs = mock_post.call_args
            self.assertIn("sendDocument", call_kwargs[0][0])
            self.assertIn("document", call_kwargs[1]["files"])
            self.assertEqual(call_kwargs[1]["data"]["caption"], "test doc")
            self.assertEqual(call_kwargs[1]["data"]["parse_mode"], "Markdown")
        finally:
            os.remove(path)

    @patch("requests.post")
    def test_send_document_no_caption(self, mock_post):
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"result": {"message_id": 51}}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as f:
            f.write(b"fake zip data")
            path = f.name

        try:
            tg.tg_send_document(path)
            call_kwargs = mock_post.call_args
            self.assertNotIn("caption", call_kwargs[1]["data"])
            self.assertNotIn("parse_mode", call_kwargs[1]["data"])
        finally:
            os.remove(path)

    @patch("requests.post")
    def test_send_document_markdown_fallback(self, mock_post):
        """On 400, retries without parse_mode."""
        fail_resp = MagicMock()
        fail_resp.status_code = 400

        ok_resp = MagicMock()
        ok_resp.status_code = 200
        ok_resp.json.return_value = {"result": {"message_id": 52}}
        ok_resp.raise_for_status = MagicMock()

        mock_post.side_effect = [fail_resp, ok_resp]

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"data")
            path = f.name

        try:
            msg_id = tg.tg_send_document(path, "bad_markdown")
            self.assertEqual(msg_id, 52)
            second_call = mock_post.call_args_list[1]
            self.assertNotIn("parse_mode", second_call[1]["data"])
        finally:
            os.remove(path)


class TestTgSendPhotoAutoDetect(unittest.TestCase):
    """Test tg_send_photo auto-detects large images and routes to sendDocument."""

    @patch.object(tg.telegram, "tg_send_document", return_value=60)
    @patch.object(tg.telegram, "_get_image_dimensions", return_value=(1920, 1080))
    def test_large_image_delegates_to_document(self, mock_dims, mock_doc):
        """Image >1280px delegates to tg_send_document."""
        msg_id = tg.tg_send_photo("/fake/large.png", "hi")
        self.assertEqual(msg_id, 60)
        mock_doc.assert_called_once_with("/fake/large.png", "hi", "")

    @patch("requests.post")
    @patch.object(tg.telegram, "_get_image_dimensions", return_value=(800, 600))
    def test_small_image_uses_send_photo(self, mock_dims, mock_post):
        """Image <=1280px uses sendPhoto as before."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"result": {"message_id": 61}}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"small image data")
            path = f.name

        try:
            msg_id = tg.tg_send_photo(path)
            self.assertEqual(msg_id, 61)
            self.assertIn("sendPhoto", mock_post.call_args[0][0])
        finally:
            os.remove(path)

    @patch("requests.post")
    @patch.object(tg.telegram, "_get_image_dimensions", return_value=(0, 0))
    def test_dimension_failure_uses_send_photo(self, mock_dims, mock_post):
        """Dimension check failure (0, 0) falls through to sendPhoto."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"result": {"message_id": 62}}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"data")
            path = f.name

        try:
            msg_id = tg.tg_send_photo(path)
            self.assertEqual(msg_id, 62)
            self.assertIn("sendPhoto", mock_post.call_args[0][0])
        finally:
            os.remove(path)


class TestCmdSendDoc(unittest.TestCase):
    """Test cmd_send_doc CLI command."""

    @patch.object(tg.telegram, "tg_send_document")
    def test_send_doc_success(self, mock_doc):
        """Existing file sends document."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(b"data")
            path = f.name
        try:
            tg.cmd_send_doc(path, "caption")
            mock_doc.assert_called_once_with(path, "caption")
        finally:
            os.remove(path)

    def test_send_doc_file_not_found(self):
        """Missing file exits with error."""
        with self.assertRaises(SystemExit) as ctx:
            tg.cmd_send_doc("/nonexistent/file.pdf")
        self.assertEqual(ctx.exception.code, 1)


class TestTgSendPhotoMimeType(unittest.TestCase):
    """Test tg_send_photo uses correct MIME type for non-PNG images."""

    @patch("requests.post")
    @patch.object(tg.telegram, "_get_image_dimensions", return_value=(640, 480))
    def test_jpeg_mime_type(self, mock_dims, mock_post):
        """JPEG file gets image/jpeg MIME type."""
        resp = MagicMock()
        resp.status_code = 200
        resp.json.return_value = {"result": {"message_id": 70}}
        resp.raise_for_status = MagicMock()
        mock_post.return_value = resp

        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"fake jpeg data")
            path = f.name

        try:
            tg.tg_send_photo(path)
            files_arg = mock_post.call_args[1]["files"]
            mime = files_arg["photo"][2]
            self.assertEqual(mime, "image/jpeg")
        finally:
            os.remove(path)


class TestDetectInterrupted(unittest.TestCase):
    """Test _detect_interrupted detects Esc-interrupted Claude sessions."""

    def test_interrupted_after_question(self):
        """Detects interrupted state after AskUserQuestion."""
        raw = (
            "❯ ask me question\n"
            "\n"
            "● User answered Claude's questions:\n"
            "  ⎿  · What would you like to work on next? → Code review\n"
            "  ⎿  Interrupted · What should Claude do instead?\n"
            "\n"
            "❯ \n"
        )
        self.assertTrue(tg._detect_interrupted(raw))

    def test_interrupted_mid_response(self):
        """Detects interrupted state during normal response."""
        raw = (
            "❯ write me a very long story about a dog\n"
            "  ⎿  Interrupted · What should Claude do instead?\n"
            "\n"
            "❯ \n"
        )
        self.assertTrue(tg._detect_interrupted(raw))

    def test_normal_completion_not_interrupted(self):
        """Normal completion is not detected as interrupted."""
        raw = (
            "● Here is the response you asked for.\n"
            "\n"
            "❯ \n"
        )
        self.assertFalse(tg._detect_interrupted(raw))

    def test_word_interrupted_in_response(self):
        """The word 'interrupted' in normal text without · is not a match."""
        raw = (
            "● The process was interrupted by a signal.\n"
            "\n"
            "❯ \n"
        )
        self.assertFalse(tg._detect_interrupted(raw))

    def test_busy_pane_not_interrupted(self):
        """Pane without ❯ prompt is not interrupted."""
        raw = (
            "● Working on the task...\n"
            "  ⎿  Interrupted · What should Claude do instead?\n"
            "* Thinking…\n"
        )
        # No ❯ prompt at the end — end stays at len(lines),
        # so the lines-before-end check won't match
        self.assertFalse(tg._detect_interrupted(raw))

    def test_old_interrupt_not_detected(self):
        """Interrupt marker from earlier in history (not near last ❯) is ignored."""
        raw = (
            "  ⎿  Interrupted · What should Claude do instead?\n"
            "\n"
            "❯ do something else\n"
            "\n"
            "● Here is the result of doing something else.\n"
            "  More details about the result here.\n"
            "  And even more details about it.\n"
            "  Final line of the response.\n"
            "\n"
            "❯ \n"
        )
        self.assertFalse(tg._detect_interrupted(raw))


if __name__ == "__main__":
    unittest.main()
