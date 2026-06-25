"""Tests for content._detect_interactive_menu (slash-command menu detection).

Fixtures under tests/fixtures/menus/ are real `tmux capture-pane -p`
snapshots from Claude Code v2.1.x.
"""
from __future__ import annotations

import os

from astra import content

_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "menus")


def _load(name: str) -> str:
    with open(os.path.join(_FIX, name)) as f:
        return f.read()


class TestDetectInteractiveMenu:
    def test_model_menu_parsed(self):
        """Real /model menu → title + all 5 options (incl. option 1, which
        sits ~13 lines above the footer — beyond the old 10-line window)."""
        result = content._detect_interactive_menu(_load("model_menu.txt"))
        assert result is not None
        title, options, free_text = result
        assert title == "Select model"
        assert len(options) == 5, f"expected 5 options, got {options}"
        # Option 1 must be present (the bug that motivated a wider scan)
        assert options[0].startswith("Default (recommended)")
        assert any(o.startswith("Sonnet") for o in options)
        assert any(o.startswith("Haiku") for o in options)
        assert free_text is None  # /model has no text-input affordance

    def test_model_confirm_footerless_parsed(self):
        """Real /model 'Switch model?' confirmation has NO footer — detect it
        via the ❯ selection cursor on option 1. (This was the stuck step.)"""
        result = content._detect_interactive_menu(_load("model_confirm.txt"))
        assert result is not None, "footer-less confirmation must be detected"
        title, options, free_text = result
        assert title == "Switch model?"
        assert len(options) == 2
        assert options[0].startswith("Yes")
        assert options[1].startswith("No")

    def test_idle_frame_returns_none(self):
        """Idle pane ('? for shortcuts · ← for agents') is not a menu."""
        assert content._detect_interactive_menu(_load("idle.txt")) is None

    def test_agents_tabbed_returns_none(self):
        """/agents is a tabbed panel with no numbered options — footer
        matches but there's nothing to tap-select, so None (manual /keys)."""
        assert content._detect_interactive_menu(_load("agents_tabbed.txt")) is None

    # --- synthetic edge cases ---

    def test_working_spinner_returns_none(self):
        """A 'Claude is working' frame has no menu footer → None."""
        raw = (
            "● Doing the thing\n"
            "\n"
            "✶ Working… (12s · esc to interrupt)\n"
        )
        assert content._detect_interactive_menu(raw) is None

    def test_text_affordance_sets_free_text_index(self):
        raw = (
            "────────────────────────────────────────\n"
            "  Pick one\n"
            "  ❯ 1. Alpha\n"
            "    2. Beta\n"
            "    3. Type something to search\n"
            "  Enter to select · Esc to cancel\n"
        )
        result = content._detect_interactive_menu(raw)
        assert result is not None
        _title, options, free_text = result
        assert free_text == 3
        assert len(options) == 3

    def test_navigate_footer_variant(self):
        raw = (
            "────────────────────────────────────────\n"
            "  Choose\n"
            "  ❯ 1. One\n"
            "    2. Two\n"
            "  ↑/↓ to navigate · Enter to confirm · Esc to cancel\n"
        )
        result = content._detect_interactive_menu(raw)
        assert result is not None
        assert result[0] == "Choose"
        assert result[1] == ["One", "Two"]

    def test_single_option_not_a_menu(self):
        raw = (
            "────────────────────────────────────────\n"
            "  ❯ 1. Only one\n"
            "  Enter to select · Esc to cancel\n"
        )
        assert content._detect_interactive_menu(raw) is None

    def test_no_footer_not_a_menu(self):
        raw = (
            "  1. Alpha\n"
            "  2. Beta\n"
            "❯ \n"
        )
        assert content._detect_interactive_menu(raw) is None

    def test_empty_input(self):
        assert content._detect_interactive_menu("") is None
        assert content._detect_interactive_menu("\n\n\n") is None


class TestDetectPermissionDialog:
    """god mode auto-accept relies on classifying a prompt as a tool
    permission — and NOT misclassifying menus/questions as permissions."""

    def test_settings_edit_is_permission(self):
        """Real settings.json self-edit dialog → approve option 1."""
        result = content._detect_permission_dialog(_load("permission_edit.txt"))
        assert result is not None
        approve_n, desc = result
        assert approve_n == 1

    def test_bash_permission_is_permission(self):
        raw = (
            "● Bash(rm -rf build/)\n"
            "────────────────────────────────────────\n"
            " Bash command\n"
            " rm -rf build/\n"
            " Do you want to proceed?\n"
            " ❯ 1. Yes\n"
            "   2. Yes, and don't ask again\n"
            "   3. No\n"
            " Esc to cancel\n"
        )
        result = content._detect_permission_dialog(raw)
        assert result is not None
        assert result[0] == 1

    def test_model_menu_is_not_permission(self):
        """/model list is a user choice, never auto-accept."""
        assert content._detect_permission_dialog(_load("model_menu.txt")) is None

    def test_model_confirm_is_not_permission(self):
        """CRITICAL: '/model Switch model?' option 1 starts with 'Yes' but
        is NOT a permission — god mode must not force a model switch."""
        assert content._detect_permission_dialog(_load("model_confirm.txt")) is None

    def test_idle_is_not_permission(self):
        assert content._detect_permission_dialog(_load("idle.txt")) is None

    def test_askuserquestion_is_not_permission(self):
        """A genuine question (no permission marker, non-Yes options)."""
        raw = (
            "────────────────────────────────────────\n"
            "  What is your favorite color?\n"
            "  ❯ 1. Red\n"
            "    2. Blue\n"
            "    3. Type something.\n"
            "  Enter to select · Esc to cancel\n"
        )
        assert content._detect_permission_dialog(raw) is None
