"""Investigation tests for two AskUserQuestion bugs reported by the user.

Bug 1: When user is asked multiple questions (multi-question AskUserQuestion),
       the FIRST question appears to get auto-selected without user input.
       Subsequent questions work correctly.

Bug 2: When a question has multi-choice options, typing a TEXT answer (not a
       number) doesn't pass correctly to the pane.

These tests inspect the actual tmux send-keys commands issued by the listener
to verify whether/when keys are sent to the pane.
"""
from __future__ import annotations

import pytest

from tests.sim.harness import SimulationHarness
from tests.test_simulation import SimTestBase


def _send_keys_calls(harness) -> list[str]:
    """Extract just the bash -c send-keys commands from the harness."""
    out = []
    for call in harness.subprocess_calls:
        args = call.get("args", [])
        if isinstance(args, list) and len(args) >= 3 and args[0] == "bash" and args[1] == "-c":
            cmd = args[2]
            if "send-keys" in cmd:
                out.append(cmd)
    return out


# -----------------------------------------------------------------------------
# Bug 1: First question auto-selected
# -----------------------------------------------------------------------------


class TestBug1FirstQuestionAutoSelect(SimTestBase):
    """Verify that receiving a multi-question signal does NOT send any keys
    to the pane before the user actually answers Q1."""

    def _setup(self):
        # Pane content shows the AskUserQuestion dialog
        pane_content = (
            "● Which approach do you prefer?\n"
            "❯ 1. Option A\n"
            "    First approach\n"
            "  2. Option B\n"
            "    Second approach\n"
            "  3. Type something.\n"
            "  4. Chat about this\n"
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=pane_content)
        return self.h.make_listener_state()

    def test_no_keys_sent_to_pane_before_user_answers(self):
        """Bug 1: After Q1 arrives, no Enter / Down should be sent before
        the user replies. The first question should NOT auto-select."""
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
            {"question": "How fast?", "options": [
                {"label": "Slow", "description": "Slow"},
                {"label": "Fast", "description": "Fast"},
            ]},
        ]
        self.h.inject_signal("question", "w4", pane="%20", project="myproject",
                             questions=questions)
        self.h.tick(s)

        # Q1 must be sent to Telegram
        assert self.h.tg.find_sent("Which approach"), \
            "Q1 was not sent to Telegram"

        # Bug 1 check: NO tmux send-keys calls should target the pane before
        # the user answers. Any Enter / Down at this point is auto-select.
        keys = _send_keys_calls(self.h)
        pane_keys = [c for c in keys if "%20" in c]
        assert not pane_keys, (
            f"BUG 1 CONFIRMED: pane received keys before user answered Q1. "
            f"Calls: {pane_keys}"
        )

    def test_q1_button_click_sends_one_option_then_q2(self):
        """When user clicks Q1's option 2 button, exactly one selection
        sequence (Down + Enter) goes to the pane, then Q2 is sent."""
        s = self._setup()
        questions = [
            {"question": "Pick A or B?", "options": [
                {"label": "A", "description": "First"},
                {"label": "B", "description": "Second"},
            ]},
            {"question": "Pick X or Y?", "options": [
                {"label": "X", "description": "x"},
                {"label": "Y", "description": "y"},
            ]},
        ]
        self.h.inject_signal("question", "w4", pane="%20", project="myproject",
                             questions=questions)
        self.h.tick(s)
        # Clear any pre-Q1 subprocess calls (there shouldn't be any per Bug 1)
        prior_calls = list(self.h.subprocess_calls)
        self.h.subprocess_calls.clear()

        # User clicks option 2 button for Q1
        self.h.tg.inject_callback("q_w4a_2", message_id=1)
        self.h.tick(s)

        # Exactly one send-keys sequence should have hit the pane
        keys = _send_keys_calls(self.h)
        pane_keys = [c for c in keys if "%20" in c]
        assert len(pane_keys) == 1, (
            f"Expected 1 send-keys for Q1 selection, got {len(pane_keys)}: {pane_keys}"
        )
        # And it should be Down + Enter (for option 2)
        assert "Down" in pane_keys[0] and "Enter" in pane_keys[0], \
            f"Q1 option 2 should send Down + Enter, got: {pane_keys[0]}"

        # Q2 should now be sent to Telegram
        assert self.h.tg.find_sent("Pick X or Y"), \
            "Q2 was not sent after Q1 selection"


# -----------------------------------------------------------------------------
# Bug 2: Free-text answer doesn't pass correctly in multi-choice
# -----------------------------------------------------------------------------


class TestBug2FreeTextInMultiChoice(SimTestBase):
    """Verify that typing a free-text answer to a multi-choice question
    correctly navigates to 'Type something.' and types the text."""

    def _setup(self, extra_pane=""):
        pane_content = (
            "● Question?\n"
            "❯ 1. Option A\n"
            "  2. Option B\n"
            "  3. Type something.\n"
            "  4. Chat about this\n"
            + extra_pane
        )
        self.h.tmux.add_session("4", "%20", "myproject", content=pane_content)
        return self.h.make_listener_state()

    def test_free_text_navigates_to_type_something_then_types(self):
        """Typing 'my custom answer' should send Down*N + 'my custom answer' + Enter.
        For 2 options, N should be 2 (cursor on option 1 → option 2 → 'Type something.').
        """
        s = self._setup()
        questions = [
            {"question": "Q?", "options": [
                {"label": "A", "description": "First"},
                {"label": "B", "description": "Second"},
            ]},
        ]
        self.h.inject_signal("question", "w4", pane="%20", project="myproject",
                             questions=questions)
        self.h.tick(s)
        assert self.h.tg.find_sent("Q?")
        self.h.subprocess_calls.clear()

        # User types a free-text answer
        self.h.tg.inject_text_message("my custom answer")
        self.h.tick(s)

        keys = _send_keys_calls(self.h)
        pane_keys = [c for c in keys if "%20" in c]
        assert pane_keys, "No keys sent to pane for free-text answer"

        combined = " ".join(pane_keys)
        # Must contain Down navigation (free_text_at = 2 for 2 options)
        assert combined.count("Down") >= 2, (
            f"BUG 2 SUSPECT: free-text should navigate Down*2 to reach "
            f"'Type something.', got: {pane_keys}"
        )
        # Must contain the typed text literally
        assert "my custom answer" in combined, (
            f"BUG 2 CONFIRMED: typed text 'my custom answer' missing from "
            f"send-keys commands. Got: {pane_keys}"
        )
        # Must end with Enter to submit
        assert combined.rstrip().endswith("Enter") or " Enter" in combined, (
            f"BUG 2 SUSPECT: free-text should submit with Enter, got: {pane_keys}"
        )

    def test_free_text_with_multi_question_chain(self):
        """In a multi-question scenario, typing free text on Q1 should still
        work correctly (Bug 2 might only manifest in multi-question mode)."""
        s = self._setup()
        questions = [
            {"question": "First?", "options": [
                {"label": "A", "description": "x"},
                {"label": "B", "description": "y"},
            ]},
            {"question": "Second?", "options": [
                {"label": "C", "description": "z"},
            ]},
        ]
        self.h.inject_signal("question", "w4", pane="%20", project="myproject",
                             questions=questions)
        self.h.tick(s)
        self.h.subprocess_calls.clear()

        # User types free text for Q1
        self.h.tg.inject_text_message("custom for q1")
        self.h.tick(s)

        keys = _send_keys_calls(self.h)
        pane_keys = [c for c in keys if "%20" in c]
        combined = " ".join(pane_keys)
        assert "custom for q1" in combined, (
            f"BUG 2 CONFIRMED (multi-q): typed text missing. Got: {pane_keys}"
        )

        # Q2 should now be sent
        assert self.h.tg.find_sent("Second?"), \
            "Q2 not sent after free-text answer to Q1"

    def test_text_starting_with_digit_treated_as_text_not_number(self):
        """If user types '1 some explanation', it has a space so it's NOT
        treated as a numbered option — should go through free-text path."""
        s = self._setup()
        questions = [
            {"question": "Q?", "options": [
                {"label": "A", "description": "x"},
                {"label": "B", "description": "y"},
            ]},
        ]
        self.h.inject_signal("question", "w4", pane="%20", project="myproject",
                             questions=questions)
        self.h.tick(s)
        self.h.subprocess_calls.clear()

        self.h.tg.inject_text_message("1 because of X")
        self.h.tick(s)

        keys = _send_keys_calls(self.h)
        pane_keys = [c for c in keys if "%20" in c]
        combined = " ".join(pane_keys)
        # Should NOT be treated as just option 1 — full text must appear
        assert "1 because of X" in combined, (
            f"Free text starting with digit lost. Got: {pane_keys}"
        )
