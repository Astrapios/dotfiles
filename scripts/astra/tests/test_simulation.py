"""Simulation tests for the astra listener loop.

These tests exercise the actual ``_listen_tick`` code path with
FakeTelegram, FakeTmux, and FakeClock replacing the three external
boundaries (Telegram API, tmux, subprocess).
"""
import os
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

        # Tick again — should detect new content and send update
        self.h.clock.advance(1)
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


if __name__ == "__main__":
    unittest.main()
