"""Comprehensive autofocus/smartfocus tests.

Tests the full lifecycle:
  1. Message sent → smartfocus activates
  2. Bulleted response content captured incrementally
  3. Tool calls interleaved with bullets — filtered correctly
  4. Stop signal sends only tail (new content since last update)
  5. Edge cases: empty responses, rapid content changes, no prev_lines
"""
import os
import unittest

from astra import state, config, content, listener
from tests.sim import SimulationHarness


class SimTestBase(unittest.TestCase):
    def setUp(self):
        self.h = SimulationHarness()
        self.h.setup()

    def tearDown(self):
        self.h.teardown()


# ---------------------------------------------------------------------------
# Scenario 1: Basic activation and initial capture
# ---------------------------------------------------------------------------

class TestSmartfocusActivation(SimTestBase):
    """Verify smartfocus activates on send and starts tracking content."""

    def test_activated_on_message_send(self):
        """Sending a message activates smartfocus for that session."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h.tg.inject_text_message("w4a fix the bug")
        self.h.tick(s)

        sf = state._load_smartfocus_state()
        self.assertIsNotNone(sf)
        self.assertEqual(sf["wid"], "w4a")
        self.assertEqual(sf["pane"], "%20")

    def test_not_activated_when_autofocus_off(self):
        """With autofocus disabled, smartfocus should not activate."""
        import astra.state as state_mod
        self.h._patches.append(
            unittest.mock.patch.object(state_mod, "_is_autofocus_enabled", return_value=False)
        )
        self.h._patches[-1].start()

        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h.tg.inject_text_message("w4a fix the bug")
        self.h.tick(s)

        sf = state._load_smartfocus_state()
        self.assertIsNone(sf)

    def test_initial_tick_establishes_baseline(self):
        """First tick after activation sets prev_lines but doesn't send."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Send message to activate
        self.h.tg.inject_text_message("w4a fix the bug")
        self.h.tick(s)
        sent_count = len(self.h.tg.sent_messages)

        # Now Claude starts responding
        self.h.tmux.set_pane_content("4",
            "● Here is my analysis\n"
            "First point\n"
        )
        state._clear_busy("w4a")
        self.h.clock.advance(1)
        self.h.tick(s)

        # Should establish prev_lines but NOT send an 👁 message
        # (nothing to diff against yet)
        self.assertEqual(len(s.smartfocus_prev_lines), 2)
        eye_msgs = self.h.tg.find_sent("👁")
        self.assertEqual(len(eye_msgs), 0)


# ---------------------------------------------------------------------------
# Scenario 2: Incremental bulleted response capture
# ---------------------------------------------------------------------------

class TestBulletedResponseCapture(SimTestBase):
    """Verify incremental capture of bulleted responses."""

    def _setup_active_smartfocus(self):
        """Helper: send a message, establish baseline content."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Send and activate smartfocus
        self.h.tg.inject_text_message("w4a analyze the code")
        self.h.tick(s)
        state._clear_busy("w4a")

        # Establish baseline
        self.h.tmux.set_pane_content("4",
            "● Here is the analysis:\n"
            "\n"
            "- First, the auth module\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)
        return s

    def test_new_bullets_captured(self):
        """New bullet points added to the response are sent as an update."""
        s = self._setup_active_smartfocus()
        self.h.tg.clear_sent()

        # Add more bullets
        self.h.tmux.set_pane_content("4",
            "● Here is the analysis:\n"
            "\n"
            "- First, the auth module\n"
            "- Second, the database layer\n"
            "- Third, the API endpoints\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Accumulates to pending

        # Advance past 5s timeout to flush pending
        self.h.clock.advance(5)
        self.h.tick(s)

        eye_msgs = self.h.tg.find_sent("👁")
        self.assertTrue(len(eye_msgs) > 0, f"Expected eye update. Sent: {self.h.dump_timeline()}")

    def test_multiple_incremental_updates(self):
        """Multiple rounds of new content each trigger an update."""
        s = self._setup_active_smartfocus()

        # Round 1: add two more bullets
        self.h.tmux.set_pane_content("4",
            "● Here is the analysis:\n"
            "\n"
            "- First, the auth module\n"
            "- Second, the database layer\n"
            "- Third, the API endpoints\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Accumulates
        self.h.clock.advance(5)
        self.h.tick(s)  # Timeout flush
        round1 = len(self.h.tg.find_sent("👁"))

        # Round 2: add more
        self.h.tmux.set_pane_content("4",
            "● Here is the analysis:\n"
            "\n"
            "- First, the auth module\n"
            "- Second, the database layer\n"
            "- Third, the API endpoints\n"
            "\n"
            "Overall the code is well-structured.\n"
            "Here are my recommendations:\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Accumulates
        self.h.clock.advance(5)
        self.h.tick(s)  # Timeout flush
        round2 = len(self.h.tg.find_sent("👁"))

        self.assertGreater(round2, round1, "Second update should generate another 👁")

    def test_no_update_when_content_unchanged(self):
        """No 👁 update if pane content hasn't changed between ticks."""
        s = self._setup_active_smartfocus()
        self.h.tg.clear_sent()

        # Tick without changing content
        self.h.clock.advance(1)
        self.h.tick(s)

        eye_msgs = self.h.tg.find_sent("👁")
        self.assertEqual(len(eye_msgs), 0)


# ---------------------------------------------------------------------------
# Scenario 3: Tool calls interleaved with bullets
# ---------------------------------------------------------------------------

class TestToolCallInterleaved(SimTestBase):
    """Verify tool calls in pane content are handled correctly during smartfocus."""

    def _setup_with_baseline(self, initial_content):
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()
        self.h.tg.inject_text_message("w4a refactor the code")
        self.h.tick(s)
        state._clear_busy("w4a")

        self.h.tmux.set_pane_content("4", initial_content)
        self.h.clock.advance(1)
        self.h.tick(s)
        return s

    def test_tool_call_then_more_bullets(self):
        """Response with tool call in the middle, then more bullet text."""
        initial = (
            "● Let me check the code first\n"
            "\n"
            "I'll start by reading the file.\n"
        )
        s = self._setup_with_baseline(initial)
        self.h.tg.clear_sent()

        # Add a tool call block followed by more response text
        self.h.tmux.set_pane_content("4",
            "● Let me check the code first\n"
            "\n"
            "I'll start by reading the file.\n"
            "\n"
            "● Read(src/main.py)\n"
            "  ... file content shown ...\n"
            "\n"
            "● Now let me explain what I found:\n"
            "\n"
            "- The function `parse_config` has a bug on line 42\n"
            "- It doesn't handle empty strings correctly\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)

        eye_msgs = self.h.tg.find_sent("👁")
        self.assertTrue(len(eye_msgs) > 0,
                        f"Expected eye update with tool+bullets. Sent: {self.h.dump_timeline()}")

    def test_multiple_tool_calls_between_bullets(self):
        """Multiple tool calls interleaved: Read, then Edit, then explanation."""
        initial = (
            "● I'll fix the bug step by step\n"
        )
        s = self._setup_with_baseline(initial)
        self.h.tg.clear_sent()

        # Simulate response with multiple tool calls and text between
        self.h.tmux.set_pane_content("4",
            "● I'll fix the bug step by step\n"
            "\n"
            "● Read(src/auth.py)\n"
            "  def login(user, pw):\n"
            "      ...\n"
            "\n"
            "● Edit(src/auth.py)\n"
            "  Applied change to line 15\n"
            "\n"
            "● The fix addresses two issues:\n"
            "\n"
            "- Input validation was missing\n"
            "- Error messages were too generic\n"
            "\n"
            "● Bash(pytest tests/)\n"
            "  ✓ 15 passed in 2.1s\n"
            "\n"
            "● All tests pass. Summary:\n"
            "\n"
            "- Added null check in `login()`\n"
            "- Improved error message specificity\n"
            "- All 15 existing tests still pass\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)

        eye_msgs = self.h.tg.find_sent("👁")
        self.assertTrue(len(eye_msgs) > 0,
                        f"Expected update. Sent: {self.h.dump_timeline()}")

    def test_spinner_lines_filtered(self):
        """Spinner and status lines don't appear in captured content."""
        initial = "● Working on it\n"
        s = self._setup_with_baseline(initial)
        self.h.tg.clear_sent()

        # Content with spinners and tool progress
        self.h.tmux.set_pane_content("4",
            "● Working on it\n"
            "\n"
            "⠋ Reading files… (3s)\n"
            "Reading 3 files… (ctrl+o to expand)\n"
            "\n"
            "● Here are the results:\n"
            "\n"
            "- File A is clean\n"
            "- File B has issues\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Accumulates to pending

        # Advance past 5s timeout to flush pending
        self.h.clock.advance(5)
        self.h.tick(s)

        eye_msgs = self.h.tg.find_sent("👁")
        self.assertTrue(len(eye_msgs) > 0)
        # Verify spinner text NOT in the sent message
        for m in eye_msgs:
            self.assertNotIn("⠋", m["text"])
            self.assertNotIn("ctrl+o", m["text"])


# ---------------------------------------------------------------------------
# Scenario 4: Stop signal with smartfocus tail
# ---------------------------------------------------------------------------

class TestStopWithSmartfocusTail(SimTestBase):
    """Verify stop signal sends only the tail when smartfocus was active."""

    def test_stop_sends_tail_only(self):
        """Stop after smartfocus sends only new lines since last update."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Set up smartfocus with known prev_lines
        state._save_smartfocus_state("w4a", "%20", "myproject")
        s.smartfocus_target_wid = "w4a"
        s.smartfocus_pane_width = 120
        s.smartfocus_prev_lines = [
            "● Here is the fix:",
            "",
            "- Changed auth validation",
            "- Updated error handling",
        ]
        s.smartfocus_has_sent = True

        # Final pane content has the old lines plus new tail
        self.h.tmux.set_pane_content("4",
            "● Here is the fix:\n"
            "\n"
            "- Changed auth validation\n"
            "- Updated error handling\n"
            "- Added unit tests for edge cases\n"
            "- Updated documentation\n"
            "\n"
            "All changes are backwards compatible.\n"
            "❯ "
        )

        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        # Should send "finished" with tail content
        finish_msgs = self.h.tg.find_sent("finished")
        self.assertTrue(len(finish_msgs) > 0)

        # Tail should contain the new lines, not the old ones
        combined_text = " ".join(m["text"] for m in finish_msgs)
        self.assertIn("unit tests", combined_text)
        self.assertIn("documentation", combined_text)

    def test_stop_full_response_when_no_prev(self):
        """Stop without prev_lines sends full response."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Smartfocus active but no prev_lines (never got to track)
        state._save_smartfocus_state("w4a", "%20", "myproject")
        s.smartfocus_target_wid = "w4a"
        s.smartfocus_pane_width = 120
        s.smartfocus_prev_lines = []
        s.smartfocus_has_sent = False

        self.h.tmux.set_pane_content("4",
            "● Complete response here:\n"
            "\n"
            "- Point one\n"
            "- Point two\n"
            "- Point three\n"
            "❯ "
        )

        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        finish_msgs = self.h.tg.find_sent("finished")
        self.assertTrue(len(finish_msgs) > 0)
        combined = " ".join(m["text"] for m in finish_msgs)
        self.assertIn("Point one", combined)

    def test_stop_always_sends_full_content(self):
        """Stop always sends full response content regardless of smartfocus state."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        state._save_smartfocus_state("w4a", "%20", "myproject")
        s.smartfocus_target_wid = "w4a"
        s.smartfocus_pane_width = 120
        s.smartfocus_prev_lines = [
            "● Done!",
            "",
            "- Fixed the bug",
            "- Ran all tests",
        ]
        s.smartfocus_has_sent = True

        # Pane shows same content
        self.h.tmux.set_pane_content("4",
            "● Done!\n"
            "\n"
            "- Fixed the bug\n"
            "- Ran all tests\n"
            "❯ "
        )

        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        finish_msgs = self.h.tg.find_sent("finished")
        self.assertTrue(len(finish_msgs) > 0)
        # Stop always sends full content for a complete summary
        msg_text = finish_msgs[0]["text"]
        assert "Fixed the bug" in msg_text, "Stop should always include full response"

    def test_stop_clears_smartfocus(self):
        """Stop signal clears smartfocus state."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        state._save_smartfocus_state("w4a", "%20", "myproject")
        s.smartfocus_target_wid = "w4a"
        s.smartfocus_prev_lines = ["some line"]

        self.h.tmux.set_pane_content("4", "● Done\n❯ ")
        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        sf = state._load_smartfocus_state()
        self.assertIsNone(sf)


# ---------------------------------------------------------------------------
# Scenario 5: Full lifecycle — send → bullets → tools → more bullets → stop
# ---------------------------------------------------------------------------

class TestFullLifecycle(SimTestBase):
    """End-to-end: message → response with bullets+tools → stop."""

    def test_send_response_with_tools_then_stop(self):
        """Full lifecycle: send msg → Claude responds with bullets and tool
        calls → smartfocus tracks content → stop signal sends tail."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # --- Step 1: Send message ---
        self.h.tg.inject_text_message("w4a analyze auth.py and fix any bugs")
        self.h.tick(s)
        self.h.assert_sent("Sent to")
        sf = state._load_smartfocus_state()
        self.assertIsNotNone(sf)
        state._clear_busy("w4a")

        # --- Step 2: Claude starts responding ---
        self.h.tmux.set_pane_content("4",
            "● I'll analyze the auth module and fix any issues.\n"
            "\n"
            "Let me start by reading the file.\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Establishes baseline

        # --- Step 3: Tool call (Read) + initial findings ---
        self.h.tmux.set_pane_content("4",
            "● I'll analyze the auth module and fix any issues.\n"
            "\n"
            "Let me start by reading the file.\n"
            "\n"
            "● Read(src/auth.py)\n"
            "  def login(username, password):\n"
            "      if not username: return None  # BUG\n"
            "\n"
            "● I found two issues:\n"
            "\n"
            "1. Missing password validation\n"
            "2. No rate limiting on login attempts\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Sends new lines immediately
        eye1 = len(self.h.tg.find_sent("👁"))
        self.assertGreater(eye1, 0, "First update after tool call")

        # --- Step 4: Tool call (Edit) + fix explanation ---
        self.h.tmux.set_pane_content("4",
            "● I'll analyze the auth module and fix any issues.\n"
            "\n"
            "Let me start by reading the file.\n"
            "\n"
            "● Read(src/auth.py)\n"
            "  def login(username, password):\n"
            "      if not username: return None  # BUG\n"
            "\n"
            "● I found two issues:\n"
            "\n"
            "1. Missing password validation\n"
            "2. No rate limiting on login attempts\n"
            "\n"
            "● Edit(src/auth.py)\n"
            "  Updated lines 5-12\n"
            "\n"
            "● Here's what I changed:\n"
            "\n"
            "- Added password length check (min 8 chars)\n"
            "- Added rate limiter (max 5 attempts/minute)\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Sends new lines immediately
        eye2 = len(self.h.tg.find_sent("👁"))
        self.assertGreater(eye2, eye1, "Second update after edit")

        # --- Step 5: Tool call (Bash) + final summary (session goes idle) ---
        self.h.tmux.set_pane_content("4",
            "● I'll analyze the auth module and fix any issues.\n"
            "\n"
            "Let me start by reading the file.\n"
            "\n"
            "● Read(src/auth.py)\n"
            "  def login(username, password):\n"
            "      if not username: return None  # BUG\n"
            "\n"
            "● I found two issues:\n"
            "\n"
            "1. Missing password validation\n"
            "2. No rate limiting on login attempts\n"
            "\n"
            "● Edit(src/auth.py)\n"
            "  Updated lines 5-12\n"
            "\n"
            "● Here's what I changed:\n"
            "\n"
            "- Added password length check (min 8 chars)\n"
            "- Added rate limiter (max 5 attempts/minute)\n"
            "\n"
            "● Bash(pytest tests/test_auth.py)\n"
            "  PASSED: 12 tests in 1.3s\n"
            "\n"
            "● All done! Summary of changes:\n"
            "\n"
            "- **auth.py**: Fixed login validation, added rate limiting\n"
            "- **Tests**: All 12 tests pass\n"
            "- No breaking changes to the public API\n"
            "❯ "
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Sends new lines immediately

        # --- Step 6: Stop signal arrives ---
        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.clock.advance(0.5)
        self.h.tick(s)

        # Should have a finish message
        finish = self.h.tg.find_sent("finished")
        self.assertTrue(len(finish) > 0,
                        f"Expected finish. All: {self.h.dump_timeline()}")

        # Smartfocus should be cleared
        self.assertIsNone(state._load_smartfocus_state())


# ---------------------------------------------------------------------------
# Scenario 6: Edge cases
# ---------------------------------------------------------------------------

class TestSmartfocusEdgeCases(SimTestBase):
    """Edge cases for smartfocus content capture."""

    def test_prompt_lines_stripped_from_capture(self):
        """Lines below the prompt (❯) are not included in smartfocus content."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Activate smartfocus
        self.h.tg.inject_text_message("w4a do task")
        self.h.tick(s)
        state._clear_busy("w4a")

        # Content with prompt at the bottom — the "❯ next prompt" should be stripped
        self.h.tmux.set_pane_content("4",
            "● Response text\n"
            "- Point one\n"
            "❯ some typed text\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)

        # prev_lines should NOT contain the prompt line
        for line in s.smartfocus_prev_lines:
            self.assertFalse(line.strip().startswith("❯"),
                             f"Prompt line leaked into prev_lines: {line}")

    def test_separator_lines_filtered(self):
        """Horizontal separator lines are filtered out."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h.tg.inject_text_message("w4a task")
        self.h.tick(s)
        state._clear_busy("w4a")

        self.h.tmux.set_pane_content("4",
            "● Response\n"
            "─────────────────────\n"
            "- Real content\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)

        for line in s.smartfocus_prev_lines:
            self.assertNotRegex(line.strip(), r'^[─━]{3,}$')

    def test_session_gone_clears_smartfocus(self):
        """If the session disappears, smartfocus is cleared."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h.tg.inject_text_message("w4a do work")
        self.h.tick(s)

        # Remove the session
        del self.h.tmux.panes["4"]
        s.sessions = self.h.tmux.scan_claude_sessions()
        self.h.clock.advance(1)
        self.h.tick(s)

        # Smartfocus should be cleared
        sf = state._load_smartfocus_state()
        self.assertIsNone(sf)

    def test_stop_with_tool_calls_in_final_content(self):
        """Stop signal correctly handles pane with tool calls in final content."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        state._save_smartfocus_state("w4a", "%20", "myproject")
        s.smartfocus_target_wid = "w4a"
        s.smartfocus_pane_width = 120
        s.smartfocus_prev_lines = ["● Starting analysis"]
        s.smartfocus_has_sent = False

        # Final content includes tool calls between bullets
        self.h.tmux.set_pane_content("4",
            "● Starting analysis\n"
            "\n"
            "● Read(src/main.py)\n"
            "  contents here...\n"
            "\n"
            "● Found the issue:\n"
            "\n"
            "- Line 42 has a null dereference\n"
            "- Easy fix: add a guard clause\n"
            "❯ "
        )

        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        finish = self.h.tg.find_sent("finished")
        self.assertTrue(len(finish) > 0)


# ---------------------------------------------------------------------------
# Scenario 7: Pure function tests for _filter_tool_calls
# ---------------------------------------------------------------------------

class TestFilterToolCalls(unittest.TestCase):
    """Unit tests for _filter_tool_calls — the function that strips tool
    call blocks from response content."""

    def test_text_only_passes_through(self):
        lines = [
            "● Here is my analysis:",
            "",
            "- Point one",
            "- Point two",
        ]
        result = content._filter_tool_calls(lines)
        self.assertEqual(result, lines)

    def test_tool_call_removed(self):
        lines = [
            "● Let me check",
            "",
            "● Read(src/main.py)",
            "  def main():",
            "      pass",
            "",
            "● Here's what I found:",
            "",
            "- Bug on line 5",
        ]
        result = content._filter_tool_calls(lines)
        # Tool block (Read + continuation) should be removed
        self.assertNotIn("● Read(src/main.py)", [l.strip() for l in result])
        self.assertNotIn("  def main():", result)
        # Text bullets should remain
        texts = [l.strip() for l in result]
        self.assertIn("● Let me check", texts)
        self.assertIn("● Here's what I found:", texts)
        self.assertIn("- Bug on line 5", texts)

    def test_multiple_tool_calls_removed(self):
        lines = [
            "● Analysis:",
            "",
            "● Read(file_a.py)",
            "  content_a",
            "",
            "● Found issue A",
            "",
            "● Edit(file_a.py)",
            "  applied fix",
            "",
            "● Bash(pytest)",
            "  12 passed",
            "",
            "● All done:",
            "- Fixed A",
            "- Tests pass",
        ]
        result = content._filter_tool_calls(lines)
        texts = [l.strip() for l in result]
        # No tool calls
        for t in texts:
            self.assertFalse(t.startswith("● Read("))
            self.assertFalse(t.startswith("● Edit("))
            self.assertFalse(t.startswith("● Bash("))
        # Text bullets remain
        self.assertIn("● Analysis:", texts)
        self.assertIn("● Found issue A", texts)
        self.assertIn("● All done:", texts)
        self.assertIn("- Fixed A", texts)

    def test_consecutive_tool_calls(self):
        """Back-to-back tool calls with no text between them."""
        lines = [
            "● Read(a.py)",
            "  aaa",
            "● Edit(a.py)",
            "  bbb",
            "● Bash(test)",
            "  ok",
            "● Summary:",
            "- Done",
        ]
        result = content._filter_tool_calls(lines)
        texts = [l.strip() for l in result]
        self.assertEqual(texts, ["● Summary:", "- Done"])


# ---------------------------------------------------------------------------
# Scenario 8: _compute_new_lines diff accuracy
# ---------------------------------------------------------------------------

class TestComputeNewLines(unittest.TestCase):
    """Unit tests for _compute_new_lines — verifying diff detection."""

    def test_appended_lines_detected(self):
        old = ["line1", "line2"]
        new = ["line1", "line2", "line3", "line4"]
        result = content._compute_new_lines(old, new)
        self.assertEqual(result, ["line3", "line4"])

    def test_identical_returns_empty(self):
        lines = ["a", "b", "c"]
        result = content._compute_new_lines(lines, lines[:])
        self.assertEqual(result, [])

    def test_inserted_in_middle(self):
        old = ["a", "c"]
        new = ["a", "b", "c"]
        result = content._compute_new_lines(old, new)
        self.assertEqual(result, ["b"])

    def test_empty_old_returns_all_new(self):
        result = content._compute_new_lines([], ["x", "y"])
        self.assertEqual(result, ["x", "y"])

    def test_completely_different_returns_all(self):
        old = ["a", "b"]
        new = ["x", "y", "z"]
        result = content._compute_new_lines(old, new)
        self.assertEqual(result, ["x", "y", "z"])

    def test_bullets_with_tool_blocks_appended(self):
        """Simulate tool output appearing then more text."""
        old = [
            "● Analysis:",
            "- Point one",
        ]
        new = [
            "● Analysis:",
            "- Point one",
            "● Read(file.py)",
            "  content",
            "● Found bug:",
            "- On line 42",
        ]
        result = content._compute_new_lines(old, new)
        self.assertIn("● Read(file.py)", result)
        self.assertIn("● Found bug:", result)
        self.assertIn("- On line 42", result)


# ---------------------------------------------------------------------------
# Scenario 9: clean_pane_content stop mode with tool calls
# ---------------------------------------------------------------------------

class TestCleanPaneContentWithTools(unittest.TestCase):
    """Verify clean_pane_content correctly extracts response from pane
    that contains tool calls."""

    def test_extracts_last_response_bullet_block(self):
        """clean_pane_content('stop') finds the LAST text bullet, not tool bullets."""
        raw = (
            "● Read(src/auth.py)\n"
            "  def login():\n"
            "      pass\n"
            "\n"
            "● Here are the issues:\n"
            "\n"
            "- Missing validation\n"
            "- No rate limiting\n"
            "❯ "
        )
        result = content.clean_pane_content(raw, "stop")
        self.assertIn("issues", result)
        self.assertIn("Missing validation", result)
        self.assertNotIn("def login", result)

    def test_multiple_tool_calls_skipped(self):
        """Last response bullet is found even with multiple tool calls before it."""
        raw = (
            "● Read(a.py)\n"
            "  aaa\n"
            "● Edit(a.py)\n"
            "  bbb\n"
            "● Bash(pytest)\n"
            "  passed\n"
            "● Summary of changes:\n"
            "\n"
            "- Fixed authentication\n"
            "- Added tests\n"
            "- All pass\n"
            "❯ "
        )
        result = content.clean_pane_content(raw, "stop")
        self.assertIn("Summary", result)
        self.assertIn("Fixed authentication", result)
        # Tool content should not leak through
        self.assertNotIn("aaa", result)
        self.assertNotIn("bbb", result)

    def test_response_with_no_tools(self):
        """Pure text response extracted correctly."""
        raw = (
            "● Here is my complete analysis:\n"
            "\n"
            "1. The code is well-structured\n"
            "2. Performance could be improved\n"
            "3. Consider adding caching\n"
            "❯ "
        )
        result = content.clean_pane_content(raw, "stop")
        self.assertIn("complete analysis", result)
        self.assertIn("well-structured", result)
        self.assertIn("caching", result)

    def test_empty_when_no_response_boundary(self):
        """Returns empty when no response bullet found."""
        raw = "Some random text\n❯ "
        result = content.clean_pane_content(raw, "stop")
        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
