"""Simulation tests for the astra listener loop.

These tests exercise the actual ``_listen_tick`` code path with
FakeTelegram, FakeTmux, and FakeClock replacing the three external
boundaries (Telegram API, tmux, subprocess).
"""
import os
import pathlib
import unittest

from astra import state, config, listener
from tests.sim import SimulationHarness


class SimTestBase(unittest.TestCase):
    """Base class that sets up / tears down the simulation harness."""

    def setUp(self):
        self.h = SimulationHarness()
        self.h.setup()

    def tearDown(self):
        self.h.teardown()


class TestHarnessBasics(SimTestBase):
    """Verify the harness itself works before testing real scenarios."""

    def test_empty_tick_no_crash(self):
        """A tick with no sessions and no messages should return None."""
        s = self.h.make_listener_state()
        result = self.h.tick(s)
        self.assertIsNone(result)

    def test_tick_with_idle_session(self):
        """A tick with an idle session and no messages should return None."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()
        result = self.h.tick(s)
        self.assertIsNone(result)

    def test_poll_returns_none(self):
        """When _poll_updates returns None, the tick sleeps and returns."""
        s = self.h.make_listener_state()
        self.h.tick(s)
        # Clock should have advanced by 2s (the sleep on empty poll)
        self.assertGreater(self.h.clock.total_slept, 0)


class TestTextMessageRouting(SimTestBase):
    """Scenario 1: Text message routing.

    Inject a Telegram message with wN prefix → verify routed to correct
    pane → verify busy flag set → verify smartfocus activated.
    """

    def test_message_routed_to_session(self):
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Inject a message targeting w4a
        self.h.tg.inject_text_message("w4a fix the bug")
        self.h.tick(s)

        # Verify confirmation sent to Telegram
        self.h.assert_sent("Sent to.*w4")

        # Verify tmux send-keys was called for the pane
        self.h.assert_keys_sent_to("%20")

        # Verify busy flag set
        self.assertTrue(state._is_busy("w4a"))

    def test_smartfocus_activated_after_send(self):
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h.tg.inject_text_message("w4a fix the bug")
        self.h.tick(s)

        # Smartfocus should be activated
        sf = state._load_smartfocus_state()
        self.assertIsNotNone(sf)
        self.assertEqual(sf["wid"], "w4a")
        self.assertEqual(sf["pane"], "%20")

    def test_single_session_no_prefix_needed(self):
        """With one session, messages route without wN prefix."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h.tg.inject_text_message("fix the bug")
        self.h.tick(s)

        self.h.assert_sent("Sent to.*w4")

    def test_last_win_idx_remembered(self):
        """After routing to w4a, next message without prefix goes to w4a."""
        self.h.tmux.add_session("4", "%20", "projA", idle=True)
        self.h.tmux.add_session("5", "%21", "projB", idle=True)
        s = self.h.make_listener_state()

        # First message to w4a
        self.h.tg.inject_text_message("w4a first task")
        self.h.tick(s)
        state._clear_busy("w4a")
        self.h.tmux.set_pane_idle("4")

        # Second message without prefix — should go to w4a
        self.h.tg.inject_text_message("continue working")
        self.h.clock.advance(1)
        self.h.tick(s)

        sent = self.h.tg.find_sent("Sent to.*w4")
        self.assertEqual(len(sent), 2)


class TestStopSignalWithSmartfocus(SimTestBase):
    """Scenario 2: Stop signal with smartfocus tail.

    Set up smartfocus state → inject stop signal → verify tail (not full
    response) sent to Telegram.
    """

    def test_stop_with_smartfocus_sends_tail(self):
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Simulate smartfocus having been active with some prev_lines
        state._save_smartfocus_state("w4a", "%20", "myproject")
        s.smartfocus_target_wid = "w4a"
        s.smartfocus_pane_width = 120
        s.smartfocus_prev_lines = ["line1", "line2", "line3"]
        s.smartfocus_has_sent = True

        # Set pane content to show a completed response with new content
        self.h.tmux.set_pane_content("4",
            "● Here is my response\n"
            "line1\nline2\nline3\n"
            "new_line4\nnew_line5\n"
            "❯ "
        )

        # Inject stop signal
        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        # Should have sent a "finished" message
        self.h.assert_sent("finished")

    def test_stop_clears_busy_flag(self):
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        state._mark_busy("w4a")
        self.assertTrue(state._is_busy("w4a"))

        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        self.assertFalse(state._is_busy("w4a"))


class TestPermissionFlow(SimTestBase):
    """Scenario 3: Permission flow.

    Inject permission signal → verify keyboard sent → inject callback
    → verify tmux send-keys called.
    """

    def test_permission_sends_keyboard(self):
        # Set up pane with a permission dialog
        perm_content = (
            "  Claude wants to run:\n"
            "  bash: ls -la\n"
            "───────────────────────────────\n"
            "  1. Yes\n"
            "  2. Yes, and don't ask again for this tool\n"
            "  3. No\n"
            "  Enter to select · ↑/↓ to navigate\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=perm_content)
        s = self.h.make_listener_state()

        # Inject permission signal
        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject", cmd="ls -la")
        self.h.tick(s)

        # Should have sent a permission message with keyboard
        perm_msgs = self.h.tg.find_sent("permission")
        self.assertTrue(len(perm_msgs) > 0)
        # The permission message should have reply_markup
        self.assertIsNotNone(perm_msgs[0].get("reply_markup"))

    def test_permission_callback_sends_keys(self):
        perm_content = (
            "  Claude wants to run:\n"
            "  bash: ls -la\n"
            "───────────────────────────────\n"
            "  1. Yes\n"
            "  2. Yes, and don't ask again for this tool\n"
            "  3. No\n"
            "  Enter to select · ↑/↓ to navigate\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=perm_content)
        s = self.h.make_listener_state()

        # First tick: process permission signal (creates active prompt)
        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject", cmd="ls -la")
        self.h.tick(s)

        # Get the message_id of the permission message
        perm_msgs = self.h.tg.find_sent("permission")
        msg_id = perm_msgs[0]["msg_id"] if perm_msgs else None

        # Second tick: inject callback for "Allow" (option 1 = perm_w4a_1)
        self.h.tg.inject_callback("perm_w4a_1", message_id=msg_id,
                                  callback_id="cb_1")
        self.h.clock.advance(1)
        self.h.tick(s)

        # Should have called tmux send-keys to navigate and select
        self.assertTrue(len(self.h.subprocess_calls) > 0)


class TestMultiPanePermission(SimTestBase):
    """Scenario: Permission signal in multi-pane window resolves via pane_id."""

    def test_god_mode_resolves_multi_pane(self):
        """God mode auto-accepts permission even when window has multiple CLIs."""
        from unittest.mock import patch as _patch
        # Window 1 has Claude (w1a) and Gemini (w1b)
        self.h.tmux.add_session("1", "%2", "myproject")
        self.h.tmux.add_multi_pane_session("1", "%27", "myproject", cli="gemini")
        s = self.h.make_listener_state()

        perm_content = (
            "  Claude wants to run:\n"
            "  bash: ls -la\n"
            "───────────────────────────────\n"
            "  1. Yes\n"
            "  2. Yes, and don't ask again for this tool\n"
            "  3. No\n"
            "  Enter to select · ↑/↓ to navigate\n"
        )
        self.h.tmux.set_pane_content("1", perm_content)

        # Signal has bare "w1" wid — must resolve to w1a via pane_id match
        self.h.inject_signal("permission", "w1", pane="%2",
                             project="myproject", cmd="ls -la")
        # Override the harness god mode mock for this test
        with _patch.object(state, "_is_god_mode_for", side_effect=lambda w: w == "w1a"):
            self.h.tick(s)

        # God mode should have auto-accepted (send-keys called)
        self.h.assert_sent("Auto-allowed")


class TestSmartfocusAcrossTicks(SimTestBase):
    """Scenario 4: Smartfocus across multiple ticks.

    Activate smartfocus → change pane content across ticks → verify
    eye updates sent for each content change.
    """

    def test_smartfocus_tracks_content_changes(self):
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Send message to activate smartfocus
        self.h.tg.inject_text_message("w4a do something")
        self.h.tick(s)
        initial_sent = len(self.h.tg.sent_messages)

        # Now simulate Claude working — pane shows response content
        self.h.tmux.set_pane_content("4",
            "● Working on it...\n"
            "First I'll check the files\n"
        )
        state._clear_busy("w4a")  # Clear so the route doesn't interfere

        # Tick to establish prev_lines
        self.h.clock.advance(1)
        self.h.tick(s)
        after_first = len(self.h.tg.sent_messages)

        # Change content — add new lines
        self.h.tmux.set_pane_content("4",
            "● Working on it...\n"
            "First I'll check the files\n"
            "Now reading main.py\n"
            "Found the bug in line 42\n"
        )

        # Tick to accumulate new lines into pending buffer
        self.h.clock.advance(1)
        self.h.tick(s)

        # Advance past 5s timeout and tick again to flush pending
        self.h.clock.advance(5)
        self.h.tick(s)

        # Should have sent an eye update for the new content
        eye_msgs = self.h.tg.find_sent("👁")
        self.assertTrue(len(eye_msgs) > 0, f"Expected 👁 message. All sent: {self.h.dump_timeline()}")

    def test_smartfocus_clears_on_stop(self):
        """Smartfocus state is cleared when stop signal is processed."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Activate smartfocus
        state._save_smartfocus_state("w4a", "%20", "myproject")
        s.smartfocus_target_wid = "w4a"
        s.smartfocus_prev_lines = ["some content"]

        # Set pane to show completed response
        self.h.tmux.set_pane_content("4",
            "● Done\nsome content\n❯ "
        )

        # Inject stop signal
        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        # Smartfocus should be cleared
        sf = state._load_smartfocus_state()
        self.assertIsNone(sf)


class TestInterruptDetection(SimTestBase):
    """Scenario 5: Interrupt detection.

    Set pane content with "Interrupted" pattern → advance clock past
    5s check interval → verify notification sent.
    """

    def test_interrupt_detected_and_notified(self):
        self.h.tmux.add_session("4", "%20", "myproject")
        s = self.h.make_listener_state()

        # Set pane content showing an interrupted state
        self.h.tmux.set_pane_content("4",
            "Interrupted · 3 tool uses · 1.2K tokens remaining\n"
            "❯ "
        )
        self.h.tmux.panes["4"].cursor_x = 2  # cursor on empty prompt

        # Advance clock past the 5s interrupt check interval
        self.h.clock.advance(6)
        s.last_interrupt_check = 0

        self.h.tick(s)

        # Should have sent an interrupt notification
        self.h.assert_sent("interrupted")

    def test_interrupt_not_re_sent(self):
        """After notifying about interrupt, don't send again."""
        self.h.tmux.add_session("4", "%20", "myproject")
        s = self.h.make_listener_state()

        self.h.tmux.set_pane_content("4",
            "Interrupted · 3 tool uses · 1.2K tokens remaining\n"
            "❯ "
        )
        self.h.tmux.panes["4"].cursor_x = 2

        # First detection
        self.h.clock.advance(6)
        s.last_interrupt_check = 0
        self.h.tick(s)
        first_count = len(self.h.tg.find_sent("interrupted"))

        # Second detection — same content, should not re-notify
        self.h.clock.advance(6)
        self.h.tick(s)
        second_count = len(self.h.tg.find_sent("interrupted"))

        self.assertEqual(first_count, second_count,
                         "Interrupt notification sent twice for same state")

    def test_interrupt_re_sent_after_busy(self):
        """After session goes busy and comes back interrupted, re-notify."""
        self.h.tmux.add_session("4", "%20", "myproject")
        s = self.h.make_listener_state()

        # First: detect interrupt
        self.h.tmux.set_pane_content("4",
            "Interrupted · 3 tool uses\n❯ "
        )
        self.h.tmux.panes["4"].cursor_x = 2
        self.h.clock.advance(6)
        s.last_interrupt_check = 0
        self.h.tick(s)
        self.assertEqual(len(self.h.tg.find_sent("interrupted")), 1)

        # Simulate session going busy (not idle anymore)
        self.h.tmux.set_pane_content("4",
            "● Working on something...\n"
            "  esc to interrupt\n"
        )
        self.h.clock.advance(6)
        self.h.tick(s)

        # Now interrupt again
        self.h.tmux.set_pane_content("4",
            "Interrupted · 5 tool uses\n❯ "
        )
        self.h.tmux.panes["4"].cursor_x = 2
        self.h.clock.advance(6)
        self.h.tick(s)

        # Should have sent two interrupt notifications total
        self.assertEqual(len(self.h.tg.find_sent("interrupted")), 2)


class TestPausedMode(SimTestBase):
    """Verify paused mode transitions work correctly."""

    def test_pause_and_resume(self):
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Inject /stop command (which triggers pause)
        self.h.tg.inject_text_message("/stop")
        result = self.h.tick(s)

        self.assertEqual(result, "pause_break")
        self.assertTrue(s.paused)

        # In paused mode, /start resumes
        self.h.tg.inject_text_message("/start")
        self.h.clock.advance(1)
        result = self.h.tick(s)

        self.assertFalse(s.paused)
        self.h.assert_sent("Resumed")

    def test_pause_quit(self):
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Pause first
        s.paused = True

        # /quit in paused mode should return "quit"
        self.h.tg.inject_text_message("/quit")
        result = self.h.tick(s)

        self.assertEqual(result, "quit")
        self.h.assert_sent("Bye")


class TestFakeTmuxMultiCLI(SimTestBase):
    """Verify FakeTmux scan_cli_sessions() with multi-CLI support."""

    def test_scan_cli_sessions_single_claude(self):
        """Single Claude session returns suffixed wid."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True, cli="claude")
        sessions = self.h.tmux.scan_cli_sessions()
        self.assertIn("w4a", sessions)
        self.assertEqual(sessions["w4a"].cli, "claude")
        self.assertEqual(sessions["w4a"].pane_target, "%20")

    def test_scan_cli_sessions_single_gemini(self):
        """Single Gemini session returns suffixed wid."""
        self.h.tmux.add_session("3", "%15", "gemproj", idle=True, cli="gemini")
        sessions = self.h.tmux.scan_cli_sessions()
        self.assertIn("w3a", sessions)
        self.assertEqual(sessions["w3a"].cli, "gemini")

    def test_scan_cli_sessions_mixed(self):
        """Claude and Gemini in different windows."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True, cli="claude")
        self.h.tmux.add_session("5", "%21", "gemproj", idle=True, cli="gemini")
        sessions = self.h.tmux.scan_cli_sessions()
        self.assertEqual(len(sessions), 2)
        self.assertEqual(sessions["w4a"].cli, "claude")
        self.assertEqual(sessions["w5a"].cli, "gemini")

    def test_session_info_unpacking(self):
        """SessionInfo can be unpacked as (pane_target, project)."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        sessions = self.h.tmux.scan_cli_sessions()
        pane, project = sessions["w4a"]
        self.assertEqual(pane, "%20")
        self.assertEqual(project, "myproject")


class TestStopSignalCliField(SimTestBase):
    """Verify stop signals carry cli field through the listener."""

    def test_stop_signal_with_cli_claude(self):
        """Stop signal with cli='claude' generates proper display name."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h.tmux.set_pane_content("4",
            "● Here is my response\n"
            "Some content here\n"
            "❯ "
        )

        self.h.inject_signal("stop", "w4", pane="%20", project="myproject", cli="claude")
        self.h.tick(s)

        # Should have sent a "finished" message mentioning Claude Code
        finished_msgs = self.h.tg.find_sent("finished")
        self.assertTrue(len(finished_msgs) > 0)

    def test_stop_signal_with_cli_gemini(self):
        """Stop signal with cli='gemini' generates proper display name."""
        self.h.tmux.add_session("4", "%20", "myproject", cli="gemini", idle=True)
        s = self.h.make_listener_state()

        self.h.tmux.set_pane_content("4",
            "✦ Here is my response from Gemini\n"
            "Some content here\n"
            " > "
        )

        self.h.inject_signal("stop", "w4", pane="%20", project="myproject", cli="gemini")
        self.h.tick(s)

        # Should have sent a "finished" message mentioning Gemini
        finished_msgs = self.h.tg.find_sent("finished")
        self.assertTrue(len(finished_msgs) > 0)
        # The message should use "Gemini" display name
        self.h.assert_sent("Gemini")

    def test_gemini_stop_captures_response_content(self):
        """Gemini stop captures content between ✦ bullet and > prompt."""
        self.h.tmux.add_session("5", "%30", "myproject", cli="gemini", idle=True)
        s = self.h.make_listener_state()

        self.h.tmux.set_pane_content("5",
            "✦ The analysis is complete.\n"
            "Here are the key findings.\n"
            " > "
        )

        self.h.inject_signal("stop", "w5", pane="%30", project="myproject", cli="gemini")
        self.h.tick(s)

        # Should capture actual response content, not empty
        self.h.assert_sent("analysis is complete")


class TestStartupDialogDetection(SimTestBase):
    """Scenario: Startup dialog detection (e.g. Gemini trust folder).

    When a session is not idle and not marked busy (no hooks fired),
    the listener scans for numbered-option dialogs and forwards them.
    """

    def _trigger_dialog(self, s):
        """Run enough ticks to pass the 10s debounce for dialog detection."""
        # First tick: dialog first seen (starts debounce timer)
        self.h.clock.advance(6)
        s.last_interrupt_check = 0
        self.h.tick(s)
        # Second tick: past 10s debounce (need >10s gap) → notification fires
        self.h.clock.advance(11)
        self.h.tick(s)

    def test_gemini_trust_dialog_forwarded(self):
        """Gemini trust dialog → Telegram notification with buttons."""
        self.h.tmux.add_session("5", "%30", "newproject", cli="gemini",
                                content=(
                                    " > 1. Trust this folder\n"
                                    "   2. Don't trust\n"
                                ))
        s = self.h.make_listener_state()
        self._trigger_dialog(s)

        # Should have sent a dialog notification
        self.h.assert_sent("dialog")
        self.h.assert_sent("Trust this folder")

    def test_dialog_not_re_sent(self):
        """Same dialog should not be sent twice."""
        self.h.tmux.add_session("5", "%30", "newproject", cli="gemini",
                                content=(
                                    " > 1. Trust this folder\n"
                                    "   2. Don't trust\n"
                                ))
        s = self.h.make_listener_state()
        self._trigger_dialog(s)
        first_count = len(self.h.tg.find_sent("dialog"))

        self.h.clock.advance(6)
        self.h.tick(s)
        second_count = len(self.h.tg.find_sent("dialog"))

        self.assertEqual(first_count, second_count,
                         "Dialog notification sent twice for same state")

    def test_dialog_cleared_on_idle(self):
        """After session goes idle, dialog_notified resets for future dialogs."""
        self.h.tmux.add_session("5", "%30", "newproject", cli="gemini",
                                content=(
                                    " > 1. Trust this folder\n"
                                    "   2. Don't trust\n"
                                ))
        s = self.h.make_listener_state()
        self._trigger_dialog(s)

        # Now session goes idle (dialog answered)
        self.h.tmux.set_pane_content("5", " >   \n")
        self.h.tmux.panes["5"].cursor_x = 1
        self.h.clock.advance(6)
        self.h.tick(s)

        # dialog_notified should be cleared
        self.assertNotIn("w5a", s.dialog_notified)

    def test_busy_session_skipped(self):
        """Session marked busy via hooks is not scanned for dialog."""
        self.h.tmux.add_session("5", "%30", "newproject", cli="gemini",
                                content=(
                                    "⠋ Working on something (esc to cancel, 5s)\n"
                                ))
        s = self.h.make_listener_state()
        state._mark_busy("w5a")
        self.h.clock.advance(6)
        s.last_interrupt_check = 0
        self.h.tick(s)

        self.h.assert_not_sent("dialog")
        state._clear_busy("w5a")

    def test_idle_session_skipped(self):
        """Idle session is not scanned for dialog."""
        self.h.tmux.add_session("5", "%30", "newproject", cli="gemini", idle=True)
        s = self.h.make_listener_state()
        self.h.clock.advance(6)
        s.last_interrupt_check = 0
        self.h.tick(s)

        self.h.assert_not_sent("dialog")

    def test_active_prompt_skipped(self):
        """Session with active prompt file is not scanned for dialog."""
        self.h.tmux.add_session("5", "%30", "newproject", cli="gemini",
                                content=(
                                    " > 1. Trust this folder\n"
                                    "   2. Don't trust\n"
                                ))
        s = self.h.make_listener_state()
        # Pre-create active prompt (simulating hook-based prompt)
        state.save_active_prompt("w5a", "%30", total=2)
        self.h.clock.advance(6)
        s.last_interrupt_check = 0
        self.h.tick(s)

        self.h.assert_not_sent("dialog")

    def test_claude_hookless_dialog_detected(self):
        """Claude session with dialog but no hooks fired → detected."""
        self.h.tmux.add_session("4", "%20", "myproject",
                                content=(
                                    "  ❯ 1. Yes, clear context\n"
                                    "    2. No\n"
                                    "    3. Edit the plan\n"
                                    "    4. Type something.\n"
                                ))
        s = self.h.make_listener_state()
        self._trigger_dialog(s)

        # Should detect the dialog even for Claude
        self.h.assert_sent("dialog")

    def test_transient_dialog_not_sent(self):
        """Dialog that disappears within debounce window → no notification."""
        self.h.tmux.add_session("5", "%30", "newproject", cli="gemini",
                                content=(
                                    " > 1. Trust this folder\n"
                                    "   2. Don't trust\n"
                                ))
        s = self.h.make_listener_state()
        # First tick: dialog first seen
        self.h.clock.advance(6)
        s.last_interrupt_check = 0
        self.h.tick(s)

        # Dialog handled by hooks before debounce expires → goes idle
        self.h.tmux.set_pane_content("5", " >   \n")
        self.h.tmux.panes["5"].cursor_x = 1
        self.h.clock.advance(6)
        self.h.tick(s)

        # No dialog notification should have been sent
        self.h.assert_not_sent("dialog")


class TestGodModeMidPermission(SimTestBase):
    """Scenario: Enabling god mode auto-accepts already-pending prompts."""

    def test_god_wn_accepts_pending_prompt(self):
        """Enabling /god wN while a permission dialog is pending auto-accepts it."""
        from unittest.mock import patch as _patch
        perm_content = (
            "  Claude wants to run:\n"
            "  bash: rm -rf /tmp/test\n"
            "───────────────────────────────\n"
            "  1. Yes\n"
            "  2. Yes, and don't ask again for this tool\n"
            "  3. No\n"
            "  Enter to select · ↑/↓ to navigate\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=perm_content)
        s = self.h.make_listener_state()

        # Inject permission signal → creates pending prompt
        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject", cmd="rm -rf /tmp/test")
        self.h.tick(s)

        # Verify prompt was saved
        self.assertTrue(state.has_active_prompt("w4a"))

        # Now enable god mode via /god w4a command
        self.h.tg.inject_text_message("/god w4a")
        self.h.clock.advance(1)
        with _patch.object(state, "_is_god_mode_for", side_effect=lambda w: w == "w4a"), \
             _patch.object(state, "_set_god_mode"):
            self.h.tick(s)

        # The pending prompt should have been consumed and accepted
        self.assertFalse(state.has_active_prompt("w4a"))
        self.h.assert_sent("Auto-accepted pending prompt")

    def test_god_all_accepts_multiple_pending_prompts(self):
        """Enabling /god all auto-accepts pending prompts across all sessions."""
        from unittest.mock import patch as _patch
        perm_content = (
            "  Claude wants to run:\n"
            "  bash: ls\n"
            "───────────────────────────────\n"
            "  1. Yes\n  2. No\n"
            "  Enter to select\n"
        )
        self.h.tmux.add_session("4", "%20", "projA", content=perm_content)
        self.h.tmux.add_session("5", "%21", "projB", content=perm_content)
        s = self.h.make_listener_state()

        # Create pending prompts for both sessions
        self.h.inject_signal("permission", "w4", pane="%20",
                             project="projA", cmd="ls")
        self.h.inject_signal("permission", "w5", pane="%21",
                             project="projB", cmd="ls")
        self.h.tick(s)

        self.assertTrue(state.has_active_prompt("w4a"))
        self.assertTrue(state.has_active_prompt("w5a"))

        # Enable /god all
        self.h.tg.inject_text_message("/god all")
        self.h.clock.advance(1)
        with _patch.object(state, "_is_god_mode_for", return_value=True), \
             _patch.object(state, "_set_god_mode"), \
             _patch.object(state, "_god_mode_wids", return_value=["all"]):
            self.h.tick(s)

        # Both pending prompts should have been consumed
        self.assertFalse(state.has_active_prompt("w4a"))
        self.assertFalse(state.has_active_prompt("w5a"))
        accepted_msgs = self.h.tg.find_sent("Auto-accepted pending prompt")
        self.assertEqual(len(accepted_msgs), 2)

    def test_god_wn_no_pending_prompt_is_noop(self):
        """Enabling /god wN with no pending prompt just enables god mode."""
        from unittest.mock import patch as _patch
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h.tg.inject_text_message("/god w4a")
        self.h.clock.advance(1)
        with _patch.object(state, "_is_god_mode_for", return_value=True), \
             _patch.object(state, "_set_god_mode"):
            self.h.tick(s)

        # No auto-accept message should be sent
        self.h.assert_not_sent("Auto-accepted pending prompt")
        # God mode on message should still be sent
        self.h.assert_sent("God mode.*on")


class TestAutoLocalDetection(SimTestBase):
    """Scenario: Auto-local detection via remote override."""

    def test_tg_send_overrides_local_suppress(self):
        """Sending a TG message to a locally-viewed session overrides local suppress."""
        from unittest.mock import patch as _patch
        from astra import config

        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        self.h.tmux.set_locally_viewed("4")
        s = self.h.make_listener_state()

        # Enable local suppress
        with _patch.object(state, "_is_local_suppress_enabled", return_value=True):
            # Send message to session — this should add remote override
            self.h.tg.inject_text_message("w4a do something")
            self.h.tick(s)

        # Remote session override should be set
        self.assertIn("4", config._remote_sessions)

    def test_remote_override_expires_on_keyboard_activity(self):
        """Remote override is cleared when tmux client_activity exceeds send time."""
        from astra import config

        # Manually set a remote override in the past
        config._remote_sessions["4"] = 1000.0

        # Client activity after the send
        self.h.tmux.set_client_activity(1001.0)

        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        self.h.tmux.set_locally_viewed("4")

        from unittest.mock import patch as _patch
        with _patch.object(state, "_is_local_suppress_enabled", return_value=True):
            s = self.h.make_listener_state()

            # Tick to trigger reconciliation
            self.h.clock.advance(6)
            s.last_interrupt_check = 0
            self.h.tick(s)

        # Remote override should have been cleared
        self.assertNotIn("4", config._remote_sessions)

    def test_remote_override_prevents_local_suppress(self):
        """With remote override active, interrupt notifications are NOT suppressed."""
        from astra import config
        from unittest.mock import patch as _patch

        self.h.tmux.add_session("4", "%20", "myproject")
        self.h.tmux.set_locally_viewed("4")

        # Remote override is active (sent to via TG recently)
        config._remote_sessions["4"] = self.h.clock.time()
        # Client activity is BEFORE the send (user hasn't typed)
        self.h.tmux.set_client_activity(self.h.clock.time() - 10)

        self.h.tmux.set_pane_content("4",
            "Interrupted · 3 tool uses · 1.2K tokens remaining\n"
            "❯ "
        )
        self.h.tmux.panes["4"].cursor_x = 2

        with _patch.object(state, "_is_local_suppress_enabled", return_value=True):
            s = self.h.make_listener_state()
            self.h.clock.advance(6)
            s.last_interrupt_check = 0
            self.h.tick(s)

        # Interrupt notification should NOT be suppressed
        self.h.assert_sent("interrupted")

    def test_local_suppress_restored_after_keyboard(self):
        """After keyboard activity, local suppress is re-engaged."""
        from astra import config
        from unittest.mock import patch as _patch

        self.h.tmux.add_session("4", "%20", "myproject")
        self.h.tmux.set_locally_viewed("4")

        # Remote override set, then keyboard activity clears it
        config._remote_sessions["4"] = 1000.0
        self.h.tmux.set_client_activity(1001.0)

        self.h.tmux.set_pane_content("4",
            "Interrupted · 3 tool uses · 1.2K tokens remaining\n"
            "❯ "
        )
        self.h.tmux.panes["4"].cursor_x = 2

        with _patch.object(state, "_is_local_suppress_enabled", return_value=True):
            s = self.h.make_listener_state()
            self.h.clock.advance(6)
            s.last_interrupt_check = 0
            self.h.tick(s)

        # Keyboard activity expired the override → local suppress is back
        # So interrupt notification should be suppressed
        self.h.assert_not_sent("interrupted")


class TestReplyRouting(SimTestBase):
    """Reply-to-message routing resolves wid correctly."""

    def test_reply_routes_bare_wid_to_suffixed_session(self):
        """Reply to a message with 'w4' should route to session 'w4a'."""
        self.h.tmux.add_session("4", "%40", "/tmp/proj")
        self.h.tmux.set_pane_content("4",
            "Some output\n"
            "❯ "
        )
        self.h.tmux.panes["4"].cursor_x = 2

        s = self.h.make_listener_state()
        # Reply to a message that contains "w4" (displayed as w4, session key is w4a)
        self.h.tg.inject_reply_message("fix the bug", "🔔 w4 (proj): stopped")
        self.h.tick(s)

        self.h.assert_sent("Sent to.*w4")

    def test_reply_sets_last_win_idx_for_subsequent_messages(self):
        """Reply routing sets last_win_idx so next message without prefix routes there too."""
        self.h.tmux.add_session("4", "%40", "/tmp/proj1")
        self.h.tmux.add_session("5", "%50", "/tmp/proj2")
        self.h.tmux.set_pane_content("4",
            "Some output\n"
            "❯ "
        )
        self.h.tmux.panes["4"].cursor_x = 2
        self.h.tmux.set_pane_content("5",
            "Other output\n"
            "❯ "
        )
        self.h.tmux.panes["5"].cursor_x = 2

        s = self.h.make_listener_state()
        # Reply to w4 message — routes first msg to w4
        self.h.tg.inject_reply_message("first msg", "● w4 completed")
        self.h.tick(s)
        self.h.assert_sent("Sent to.*w4")
        # Second message without prefix should also go to w4 (saved as busy)
        self.h.tg.clear_sent()
        self.h.tg.inject_text_message("second msg")
        self.h.clock.advance(0.5)
        self.h.tick(s)

        self.h.assert_sent("w4")


class TestStopSignalDedup(SimTestBase):
    """Stop signal deduplication — multiple stops for same wid produce one message."""

    def test_multiple_stop_signals_deduped(self):
        """3 stop signals for same wid → only 1 '✅' message."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Set pane content with a completed response
        self.h.tmux.set_pane_content("4",
            "● Done with the task\n"
            "All changes applied.\n"
            "❯ "
        )

        # Inject 3 stop signals for the same wid
        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        # Should have exactly 1 "finished" message
        finished_msgs = self.h.tg.find_sent("finished")
        assert len(finished_msgs) == 1, \
            f"Expected 1 finished msg, got {len(finished_msgs)}: {self.h.dump_timeline()}"

    def test_stop_signals_different_wids_not_deduped(self):
        """2 stop signals for different wids → 2 '✅' messages."""
        self.h.tmux.add_session("4", "%20", "projA", idle=True)
        self.h.tmux.add_session("5", "%21", "projB", idle=True)
        s = self.h.make_listener_state()

        self.h.tmux.set_pane_content("4",
            "● Done A\n❯ "
        )
        self.h.tmux.set_pane_content("5",
            "● Done B\n❯ "
        )

        self.h.inject_signal("stop", "w4", pane="%20", project="projA")
        self.h.inject_signal("stop", "w5", pane="%21", project="projB")
        self.h.tick(s)

        finished_msgs = self.h.tg.find_sent("finished")
        assert len(finished_msgs) == 2, \
            f"Expected 2 finished msgs, got {len(finished_msgs)}: {self.h.dump_timeline()}"


class TestPlanPermission(SimTestBase):
    """Plan permission reads plan file and sends full content."""

    def _write_plan_file(self, content):
        """Write a fake plan file to ~/.claude/plans/ for testing."""
        import tempfile
        plans_dir = pathlib.Path.home() / ".claude" / "plans"
        plans_dir.mkdir(parents=True, exist_ok=True)
        self._plan_file = plans_dir / "_test_plan.md"
        self._plan_file.write_text(content)
        # Touch to ensure it's the most recent
        self._plan_file.touch()

    def _cleanup_plan_file(self):
        if hasattr(self, '_plan_file') and self._plan_file.exists():
            self._plan_file.unlink()

    def setUp(self):
        super().setUp()
        self._write_plan_file(
            "# Plan: Add cowsay integration\n\n"
            "## Changes\n\n"
            "1. Add /moo command handler\n"
            "2. Wire into CLI\n"
        )

    def tearDown(self):
        self._cleanup_plan_file()
        super().tearDown()

    def test_plan_permission_shows_plan_file_content(self):
        """Plan permission reads plan file and shows it with buttons."""
        pane_content = (
            "● ExitPlanMode\n"
            "───────────────────────────────\n"
            "  1. Yes, execute this plan\n"
            "  2. Yes, and don't ask again\n"
            "  3. No\n"
            "  4. Type something to tell Claude...\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=pane_content)
        s = self.h.make_listener_state()

        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject",
                             message="Claude has a plan ready to execute.")
        self.h.tick(s)

        # Should send plan message with plan file content
        plan_msgs = self.h.tg.find_sent("plan for review")
        assert len(plan_msgs) > 0, \
            f"Expected plan msg. All sent: {self.h.dump_timeline()}"
        # Should contain plan file content
        assert "Add cowsay integration" in plan_msgs[0]["text"]
        assert "Add /moo command" in plan_msgs[0]["text"]
        # Should have inline keyboard
        assert plan_msgs[0].get("reply_markup") is not None

    def test_plan_permission_has_options(self):
        """Plan permission includes numbered options from pane."""
        pane_content = (
            "● ExitPlanMode\n"
            "───────────────────────────────\n"
            "  1. Yes, execute this plan\n"
            "  2. Yes, and don't ask again\n"
            "  3. No\n"
            "  4. Type something to tell Claude...\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=pane_content)
        s = self.h.make_listener_state()

        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject",
                             message="plan approval needed")
        self.h.tick(s)

        plan_msgs = self.h.tg.find_sent("plan for review")
        # Options should be in the message text
        assert "Yes, execute" in plan_msgs[0]["text"] or \
               "1." in plan_msgs[0]["text"]

    def test_plan_permission_not_auto_accepted_god_mode(self):
        """Plan permission is NOT auto-accepted in god mode."""
        from unittest.mock import patch as _patch
        pane_content = (
            "● ExitPlanMode\n"
            "───────────────────────────────\n"
            "  1. Yes\n"
            "  2. No\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=pane_content)
        s = self.h.make_listener_state()

        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject",
                             message="plan ready for execution")
        with _patch.object(state, "_is_god_mode_for", return_value=True):
            self.h.tick(s)

        # Should NOT have auto-accepted (no "Auto-allowed" message)
        self.h.assert_not_sent("Auto-allowed")

    def test_normal_permission_not_affected(self):
        """Non-plan permission still goes through normal path."""
        perm_content = (
            "  Claude wants to run:\n"
            "  bash: ls -la\n"
            "───────────────────────────────\n"
            "  1. Yes\n"
            "  2. Yes, and don't ask again\n"
            "  3. No\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=perm_content)
        s = self.h.make_listener_state()

        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject", cmd="ls -la",
                             message="Claude wants to run a bash command")
        self.h.tick(s)

        # Should send regular permission (not plan)
        self.h.assert_not_sent("plan for review")
        perm_msgs = self.h.tg.find_sent("permission")
        assert len(perm_msgs) > 0


class TestStaleBashCmdCleanup(SimTestBase):
    """Bug fix: non-shell PreToolUse cleans up stale _bash_cmd file."""

    def test_stale_bash_cmd_not_shown_for_write_permission(self):
        """Write permission should NOT show stale bash command from auto-approved shell."""
        import json as _json
        self.h.tmux.add_session("4", "%20", "myproject")
        s = self.h.make_listener_state()

        # Simulate stale _bash_cmd file from auto-approved shell command
        cmd_file = os.path.join(config.SIGNAL_DIR, "_bash_cmd_w4.json")
        with open(cmd_file, "w") as f:
            _json.dump({"cmd": "echo hello"}, f)

        # Inject a Write permission (non-bash) — the stale file should NOT be consumed
        perm_content = (
            "● Write(app.py)\n"
            "  ⎿  from flask import Flask\n"
            "───────────────────────────────\n"
            "  1. Yes\n"
            "  2. Yes, and don't ask again\n"
            "  3. No\n"
        )
        self.h.tmux.set_pane_content("4", perm_content)
        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject", cmd="",
                             message="Claude wants to write app.py")
        self.h.tick(s)

        # Permission message should NOT contain the stale bash command
        perm_msgs = self.h.tg.find_sent("permission|Write|app.py")
        assert len(perm_msgs) > 0, f"Expected perm msg. All: {self.h.dump_timeline()}"
        for msg in perm_msgs:
            assert "echo hello" not in msg["text"], \
                f"Stale bash cmd leaked into Write permission: {msg['text'][:200]}"


class TestTwoOptionKeyboard(SimTestBase):
    """Bug fix: 2-option permissions don't have duplicate callbacks."""

    def test_two_option_perm_no_always_button(self):
        """Permission with only 2 options should show Allow/Deny, not Always."""
        perm_content = (
            "  Claude wants to run:\n"
            "  bash: ls\n"
            "───────────────────────────────\n"
            "  1. Yes\n"
            "  2. No\n"
            "  Enter to select\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=perm_content)
        s = self.h.make_listener_state()

        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject", cmd="ls")
        self.h.tick(s)

        perm_msgs = self.h.tg.find_sent("permission")
        assert len(perm_msgs) > 0
        kb = perm_msgs[0].get("reply_markup")
        assert kb is not None, "Expected keyboard on permission msg"

        # Extract all callback data from the keyboard
        callbacks = []
        for row in kb.get("inline_keyboard", []):
            for btn in row:
                callbacks.append(btn.get("callback_data", ""))

        # Should have exactly 2 buttons: Allow (perm_w4a_1) and Deny (perm_w4a_2)
        assert len(callbacks) == 2, f"Expected 2 buttons, got {len(callbacks)}: {callbacks}"
        assert callbacks[0] == "perm_w4a_1", f"First button should be Allow: {callbacks[0]}"
        assert callbacks[1] == "perm_w4a_2", f"Second button should be Deny: {callbacks[1]}"
        # No duplicate callbacks
        assert len(set(callbacks)) == len(callbacks), f"Duplicate callbacks: {callbacks}"

    def test_three_option_perm_has_always_button(self):
        """Permission with 3 options should show Allow/Always/Deny."""
        perm_content = (
            "  Claude wants to run:\n"
            "  bash: ls -la\n"
            "───────────────────────────────\n"
            "  1. Yes\n"
            "  2. Yes, and don't ask again\n"
            "  3. No\n"
            "  Enter to select\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=perm_content)
        s = self.h.make_listener_state()

        self.h.inject_signal("permission", "w4", pane="%20",
                             project="myproject", cmd="ls -la")
        self.h.tick(s)

        perm_msgs = self.h.tg.find_sent("permission")
        assert len(perm_msgs) > 0
        kb = perm_msgs[0].get("reply_markup")
        assert kb is not None

        callbacks = []
        for row in kb.get("inline_keyboard", []):
            for btn in row:
                callbacks.append(btn.get("callback_data", ""))

        assert len(callbacks) == 3, f"Expected 3 buttons, got {len(callbacks)}: {callbacks}"
        assert callbacks[0] == "perm_w4a_1"
        assert callbacks[1] == "perm_w4a_2"
        assert callbacks[2] == "perm_w4a_3"


class TestStopFullContent(SimTestBase):
    """Bug fix: stop message always shows full content, not just tail."""

    def test_smartfocus_stop_shows_full_content(self):
        """Stop with smartfocus tracking should show full collapsed content."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Simulate smartfocus with prev_lines that overlap with response
        state._save_smartfocus_state("w4a", "%20", "myproject")
        s.smartfocus_target_wid = "w4a"
        s.smartfocus_pane_width = 120
        # prev_lines already "saw" the tool call
        s.smartfocus_prev_lines = [
            "🔧 Read(app.py)",
            "  Contents of file...",
        ]
        s.smartfocus_has_sent = True

        # Pane shows full response including tool call + final text
        self.h.tmux.set_pane_content("4",
            "● Here is the analysis\n"
            "🔧 Read(app.py)\n"
            "  Contents of file...\n"
            "The code looks good. All tests pass.\n"
            "❯ "
        )

        self.h.inject_signal("stop", "w4", pane="%20", project="myproject")
        self.h.tick(s)

        # Should show full content, not just the tail
        self.h.assert_sent("finished")
        finished = self.h.tg.find_sent("finished")
        # Should include the analysis text (which is in the full response)
        assert any("analysis" in m["text"] or "code looks good" in m["text"]
                    for m in finished), \
            f"Stop msg missing full content: {[m['text'][:200] for m in finished]}"


class TestMultiQuestion(SimTestBase):
    """Multi-question AskUserQuestion flow."""

    def _setup(self):
        self.h.tmux.add_session("4", "%20", "myproject",
                                content="  1. Option A\n  2. Option B\n❯ ")
        return self.h.make_listener_state()

    def test_multi_question_sends_all_questions_sequentially(self):
        """Answering Q1 should send Q2 with buttons."""
        s = self._setup()
        questions = [
            {"question": "Which approach?", "options": [
                {"label": "A", "description": "First"},
                {"label": "B", "description": "Second"},
            ]},
            {"question": "Which style?", "options": [
                {"label": "Min", "description": "Minimal"},
                {"label": "Max", "description": "Maximal"},
            ]},
        ]
        self.h.inject_signal("question", "w4", pane="%20", project="myproject",
                             questions=questions)
        self.h.tick(s)

        # Q1 sent with buttons
        self.h.assert_sent("Which approach?")
        q1_msgs = self.h.tg.find_sent("Which approach?")
        assert q1_msgs, "Q1 not sent"
        assert q1_msgs[0].get("reply_markup"), "Q1 missing keyboard buttons"

        # Answer Q1 via button callback
        self.h.tg.inject_callback("q_w4a_1", message_id=1)
        self.h.tick(s)

        # Q2 sent with buttons
        self.h.assert_sent("Which style?")
        q2_msgs = self.h.tg.find_sent("Which style?")
        assert q2_msgs, "Q2 not sent"
        assert q2_msgs[0].get("reply_markup"), "Q2 missing keyboard buttons"

    def test_multi_question_submit_confirmation(self):
        """After last question in a multi-question set, should ask to submit."""
        s = self._setup()
        questions = [
            {"question": "Q1?", "options": [
                {"label": "Yes", "description": "ok"},
            ]},
            {"question": "Q2?", "options": [
                {"label": "No", "description": "nope"},
            ]},
        ]
        self.h.inject_signal("question", "w4", pane="%20", project="myproject",
                             questions=questions)
        self.h.tick(s)
        self.h.assert_sent("Q1?")

        # Answer Q1
        self.h.tg.inject_callback("q_w4a_1", message_id=1)
        self.h.tick(s)
        self.h.assert_sent("Q2?")

        # Answer Q2 — remaining_qs is now [], should trigger submit
        self.h.tg.inject_callback("q_w4a_1", message_id=1)
        self.h.tick(s)
        self.h.assert_sent("Submit answers")

    def test_multi_question_text_answer(self):
        """Typing a number to answer should work the same as button."""
        s = self._setup()
        questions = [
            {"question": "Pick?", "options": [
                {"label": "A", "description": "First"},
                {"label": "B", "description": "Second"},
            ]},
            {"question": "Style?", "options": [
                {"label": "X", "description": "Style X"},
            ]},
        ]
        self.h.inject_signal("question", "w4", pane="%20", project="myproject",
                             questions=questions)
        self.h.tick(s)
        self.h.assert_sent("Pick?")

        # Answer via text (no prefix — single session)
        self.h.tg.inject_text_message("1")
        self.h.tick(s)
        self.h.assert_sent("Style?")

    def test_prompt_survives_restart_cleanup(self):
        """Active prompt files should survive listener startup cleanup."""
        s = self._setup()
        questions = [
            {"question": "Q1?", "options": [
                {"label": "A", "description": "ok"},
            ]},
            {"question": "Q2?", "options": [
                {"label": "B", "description": "ok"},
            ]},
        ]
        self.h.inject_signal("question", "w4", pane="%20", project="myproject",
                             questions=questions)
        self.h.tick(s)
        self.h.assert_sent("Q1?")

        # Verify prompt file exists
        assert state.has_active_prompt("w4a"), "Prompt should exist after Q1"

        # Simulate startup cleanup (what cmd_listen does on restart)
        sig_dir = config.SIGNAL_DIR
        for f in os.listdir(sig_dir):
            if f.startswith(("_bash_cmd_", "_busy_")):
                try:
                    os.remove(os.path.join(sig_dir, f))
                except OSError:
                    pass

        # Prompt should still exist
        assert state.has_active_prompt("w4a"), "Prompt should survive restart cleanup"

        # And answering should still work
        self.h.tg.inject_callback("q_w4a_1", message_id=1)
        self.h.tick(s)
        self.h.assert_sent("Q2?")


class TestFocusDiffOnly(SimTestBase):
    """Bug fix: Focus mode sends only new lines (diff), not full response."""

    def test_focus_sends_diff_not_full(self):
        """After baseline, only new lines are sent."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        # Activate focus
        state._save_focus_state("w4a", "%20", "myproject")

        # Tick 1: establish baseline
        self.h.tmux.set_pane_content("4",
            "● First paragraph\n"
            "Line A\nLine B\n"
            "❯ "
        )
        self.h.clock.advance(1)
        self.h.tick(s)
        assert len(s.focus_prev_lines) > 0, "Baseline should be set"
        self.h.assert_not_sent("🔍.*myproject")

        # Tick 2: add new content → only new lines sent
        self.h.tmux.set_pane_content("4",
            "● First paragraph\n"
            "Line A\nLine B\n"
            "New line C\nNew line D\n"
            "❯ "
        )
        self.h.clock.advance(1)
        self.h.tick(s)

        focus_msgs = self.h.tg.find_sent("🔍")
        assert len(focus_msgs) > 0, f"Expected focus msg. All: {self.h.dump_timeline()}"
        # Should contain only new lines, not the full response
        msg_text = focus_msgs[0]["text"]
        assert "New line C" in msg_text
        assert "New line D" in msg_text

    def test_focus_no_send_on_first_tick(self):
        """First tick after activation sets baseline, doesn't send."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        state._save_focus_state("w4a", "%20", "myproject")
        self.h.tmux.set_pane_content("4",
            "● Some response\nContent here\n❯ "
        )
        self.h.clock.advance(1)
        self.h.tick(s)

        # Should NOT send anything (first tick = baseline)
        self.h.assert_not_sent("🔍.*myproject")
        assert len(s.focus_prev_lines) > 0

    def test_focus_no_send_when_unchanged(self):
        """No send when content hasn't changed between ticks."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        state._save_focus_state("w4a", "%20", "myproject")
        self.h.tmux.set_pane_content("4",
            "● Response\nLine 1\n❯ "
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Baseline

        # Same content, tick again
        self.h.clock.advance(1)
        self.h.tick(s)

        self.h.assert_not_sent("🔍.*myproject")

    def test_focus_resets_on_target_change(self):
        """Switching focus target resets prev_lines."""
        self.h.tmux.add_session("4", "%20", "projA", idle=True)
        self.h.tmux.add_session("5", "%21", "projB", idle=True)
        s = self.h.make_listener_state()

        # Focus on w4a
        state._save_focus_state("w4a", "%20", "projA")
        self.h.tmux.set_pane_content("4", "● A\nContent\n❯ ")
        self.h.clock.advance(1)
        self.h.tick(s)
        assert s.focus_target_wid == "w4a"
        assert len(s.focus_prev_lines) > 0

        # Switch to w5a
        state._save_focus_state("w5a", "%21", "projB")
        self.h.tmux.set_pane_content("5", "● B\nOther\n❯ ")
        self.h.clock.advance(1)
        self.h.tick(s)
        assert s.focus_target_wid == "w5a"
        # First tick on new target = baseline, no send
        self.h.assert_not_sent("🔍.*projB")


class TestAutofocusOnBusyAttach(SimTestBase):
    """Feature: /autofocus on auto-attaches to a busy session."""

    def test_autofocus_on_attaches_to_busy_session(self):
        """Toggling autofocus on while a session is busy auto-attaches smartfocus."""
        self.h.tmux.add_session("4", "%20", "myproject",
                                content="● Working on something...\n  esc to interrupt\n")
        s = self.h.make_listener_state()

        state._mark_busy("w4a")

        self.h.tg.inject_text_message("/autofocus on")
        self.h.tick(s)

        # Should have attached smartfocus to the busy session
        sf = state._load_smartfocus_state()
        assert sf is not None, "Smartfocus should be activated"
        assert sf["wid"] == "w4a"

        # Confirmation should mention the session
        self.h.assert_sent("watching.*w4")
        state._clear_busy("w4a")

    def test_autofocus_on_prefers_last_win_idx(self):
        """When multiple sessions are busy, prefer last_win_idx."""
        self.h.tmux.add_session("4", "%20", "projA",
                                content="● Working...\n  esc to interrupt\n")
        self.h.tmux.add_session("5", "%21", "projB",
                                content="● Working...\n  esc to interrupt\n")
        s = self.h.make_listener_state()
        s.last_win_idx = "w5a"

        state._mark_busy("w4a")
        state._mark_busy("w5a")

        self.h.tg.inject_text_message("/autofocus on")
        self.h.tick(s)

        sf = state._load_smartfocus_state()
        assert sf is not None
        assert sf["wid"] == "w5a", f"Should prefer last_win_idx w5a, got {sf['wid']}"
        state._clear_busy("w4a")
        state._clear_busy("w5a")

    def test_autofocus_on_no_busy_session(self):
        """When no sessions are busy, just confirm without attaching."""
        from unittest.mock import patch as _patch
        import astra.state as state_mod

        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h._patches.append(
            _patch.object(state_mod, "_is_autofocus_enabled", return_value=True)
        )
        self.h._patches[-1].start()

        self.h.tg.inject_text_message("/autofocus on")
        self.h.tick(s)

        sf = state._load_smartfocus_state()
        assert sf is None, "No smartfocus without busy session"
        self.h.assert_sent("Autofocus.*on")
        self.h.assert_not_sent("watching")

    def test_bare_autofocus_shows_busy_picker(self):
        """Bare /autofocus shows busy session picker."""
        self.h.tmux.add_session("4", "%20", "myproject",
                                content="● Working...\n  esc to interrupt\n")
        s = self.h.make_listener_state()

        state._mark_busy("w4a")

        self.h.tg.inject_text_message("/autofocus")
        self.h.tick(s)

        # Should show picker, not toggle
        self.h.assert_sent("Watch which session")
        # Smartfocus should NOT be auto-activated (user must pick)
        sf = state._load_smartfocus_state()
        assert sf is None, "Bare /autofocus should show picker, not attach"
        state._clear_busy("w4a")

    def test_autofocus_wN_attaches(self):
        """'/autofocus wN' turns on autofocus and attaches to that session."""
        self.h.tmux.add_session("4", "%20", "myproject",
                                content="● Working...\n  esc to interrupt\n")
        s = self.h.make_listener_state()

        self.h.tg.inject_text_message("/autofocus w4")
        self.h.tick(s)

        sf = state._load_smartfocus_state()
        assert sf is not None, "Should attach to specified session"
        assert sf["wid"] == "w4a"
        self.h.assert_sent("watching.*w4")


class TestSmartfocusBulletBatching(SimTestBase):
    """Feature: Smartfocus accumulates lines and flushes on bullet boundaries."""

    def _setup_smartfocus(self):
        """Helper: send message, establish baseline."""
        self.h.tmux.add_session("4", "%20", "myproject", idle=True)
        s = self.h.make_listener_state()

        self.h.tg.inject_text_message("w4a do work")
        self.h.tick(s)
        state._clear_busy("w4a")

        # Establish baseline
        self.h.tmux.set_pane_content("4",
            "● Starting response\n"
            "First line of content\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)
        return s

    def test_pending_accumulates_no_immediate_send(self):
        """New lines accumulate in pending buffer, not sent immediately."""
        s = self._setup_smartfocus()
        self.h.tg.clear_sent()

        self.h.tmux.set_pane_content("4",
            "● Starting response\n"
            "First line of content\n"
            "Second line added\n"
            "Third line added\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)

        # Should NOT have sent yet (pending, no flush condition)
        eye_msgs = self.h.tg.find_sent("👁")
        assert len(eye_msgs) == 0, f"Should not send immediately: {self.h.dump_timeline()}"
        assert len(s.smartfocus_pending) > 0, "Pending should have accumulated lines"

    def test_timeout_flushes_pending(self):
        """5s with no new content triggers flush."""
        s = self._setup_smartfocus()
        self.h.tg.clear_sent()

        self.h.tmux.set_pane_content("4",
            "● Starting response\n"
            "First line of content\n"
            "New content here\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Accumulates

        # Advance past 5s timeout
        self.h.clock.advance(5)
        self.h.tick(s)  # Should flush

        eye_msgs = self.h.tg.find_sent("👁")
        assert len(eye_msgs) > 0, f"Timeout should flush. Sent: {self.h.dump_timeline()}"

    def test_idle_flushes_immediately(self):
        """Prompt char appearing (session idle) flushes all pending."""
        s = self._setup_smartfocus()
        self.h.tg.clear_sent()

        # Content with prompt char at end (session went idle)
        self.h.tmux.set_pane_content("4",
            "● Starting response\n"
            "First line of content\n"
            "All done with this task\n"
            "❯ "
        )
        self.h.clock.advance(1)
        self.h.tick(s)  # Idle detected → immediate flush

        eye_msgs = self.h.tg.find_sent("👁")
        assert len(eye_msgs) > 0, f"Idle should flush immediately. Sent: {self.h.dump_timeline()}"

    def test_bullet_boundary_flushes_before_bullet(self):
        """A new text bullet flushes everything before it."""
        s = self._setup_smartfocus()
        self.h.tg.clear_sent()

        # Add a tool call followed by a new text bullet
        self.h.tmux.set_pane_content("4",
            "● Starting response\n"
            "First line of content\n"
            "\n"
            "● Read(file.py)\n"
            "  file contents here\n"
            "\n"
            "● Here is what I found:\n"
            "\n"
            "- Bug on line 42\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)

        # The bullet boundary should flush the tool call before "● Here is what I found:"
        # The tool call content should be sent (collapsed), the text bullet stays in pending
        assert len(s.smartfocus_pending) > 0, "Text bullet and content should be in pending"

    def test_pending_cleared_on_smartfocus_deactivate(self):
        """When smartfocus is cleared, pending buffer is also cleared."""
        s = self._setup_smartfocus()

        # Accumulate some pending
        self.h.tmux.set_pane_content("4",
            "● Starting response\n"
            "First line of content\n"
            "More content\n"
        )
        self.h.clock.advance(1)
        self.h.tick(s)
        assert len(s.smartfocus_pending) > 0

        # Clear smartfocus
        state._clear_smartfocus_state()
        self.h.clock.advance(1)
        self.h.tick(s)

        assert s.smartfocus_target_wid is None
        assert len(s.smartfocus_pending) == 0
        assert s.smartfocus_last_new_ts == 0


if __name__ == "__main__":
    unittest.main()
