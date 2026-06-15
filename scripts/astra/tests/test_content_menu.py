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
