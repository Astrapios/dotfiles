"""Tests for content._detect_interactive_menu (slash-command menu detection).

Fixtures under tests/fixtures/menus/ are real `tmux capture-pane -p`
snapshots from Claude Code v2.1.x.
"""
from __future__ import annotations

import os
from unittest.mock import Mock

from astra import content, routing

_FIX = os.path.join(os.path.dirname(__file__), "fixtures", "menus")

# A real /model capture where the active model is option 2 (Opus) — the ❯
# cursor opens on the current selection, NOT on option 1. This is the layout
# that broke tap-to-select (every tap landed at current+n-1).
_MODEL_MENU_CURSOR_2 = (
    "  Select model\n"
    "  Switch between Claude models. Your pick becomes the default.\n"
    "    1. Default (recommended)  Opus 4.8 with 1M context\n"
    "  ❯ 2. Opus ✔                 Opus 4.8 with 1M context\n"
    "    3. Fable                  Fable 5\n"
    "    4. Sonnet                 Sonnet 5\n"
    "    5. Haiku                  Haiku 4.5\n"
    "  ● High effort (default) ←/→ to adjust\n"
    "  Enter to set as default · s to use this session only · Esc to cancel\n"
)


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


class TestMenuCursorOption:
    """The ❯ cursor marks the menu's *current* selection; navigation must be
    relative to it, so we need to read which option number it points at."""

    def test_cursor_on_current_model(self):
        assert content._menu_cursor_option(_MODEL_MENU_CURSOR_2) == 2

    def test_cursor_on_option_1_fixture(self):
        # In this older fixture the active model IS option 1.
        assert content._menu_cursor_option(_load("model_menu.txt")) == 1

    def test_no_cursor_returns_none(self):
        assert content._menu_cursor_option("  1. Alpha\n  2. Beta\n") is None

    def test_bottom_most_cursor_wins(self):
        """A stale menu higher in scrollback must not beat the live one."""
        raw = "  ❯ 1. Old\n────\n  Select model\n  ❯ 3. Live\n"
        assert content._menu_cursor_option(raw) == 3


class TestSelectOptionCursorRelative:
    """routing._select_option navigates relative to the ❯ cursor, fixing the
    /model bug where taps landed at (current_position + n - 1)."""

    def test_tap_below_cursor_moves_down(self, monkeypatch):
        # cursor on option 2; tap "4. Sonnet" → Down exactly 2 (4-2), not 3.
        monkeypatch.setattr(routing.tmux, "_capture_pane",
                            lambda *a, **k: _MODEL_MENU_CURSOR_2)
        rel = Mock()
        monkeypatch.setattr(routing.tmux_send, "select_relative", rel)
        routing._select_option("%2", 4)
        rel.assert_called_once_with("%2", 2)

    def test_tap_on_cursor_is_zero_delta(self, monkeypatch):
        # cursor on option 2; tap "2. Opus" → Enter only (delta 0).
        monkeypatch.setattr(routing.tmux, "_capture_pane",
                            lambda *a, **k: _MODEL_MENU_CURSOR_2)
        rel = Mock()
        monkeypatch.setattr(routing.tmux_send, "select_relative", rel)
        routing._select_option("%2", 2)
        rel.assert_called_once_with("%2", 0)

    def test_tap_above_cursor_moves_up(self, monkeypatch):
        # cursor on option 2; tap "1. Default" → Up 1 (delta -1).
        monkeypatch.setattr(routing.tmux, "_capture_pane",
                            lambda *a, **k: _MODEL_MENU_CURSOR_2)
        rel = Mock()
        monkeypatch.setattr(routing.tmux_send, "select_relative", rel)
        routing._select_option("%2", 1)
        rel.assert_called_once_with("%2", -1)

    def test_no_cursor_falls_back_to_option1_origin(self, monkeypatch):
        # Permission dialogs reliably open at option 1; without a detectable
        # cursor we preserve the old Down*(n-1) behaviour.
        monkeypatch.setattr(routing.tmux, "_capture_pane",
                            lambda *a, **k: "no menu on screen")
        rel = Mock()
        monkeypatch.setattr(routing.tmux_send, "select_relative", rel)
        routing._select_option("%2", 3)
        rel.assert_called_once_with("%2", 2)

    def test_capture_failure_falls_back(self, monkeypatch):
        def _boom(*a, **k):
            raise OSError("no pane")
        monkeypatch.setattr(routing.tmux, "_capture_pane", _boom)
        rel = Mock()
        monkeypatch.setattr(routing.tmux_send, "select_relative", rel)
        routing._select_option("%2", 2)
        rel.assert_called_once_with("%2", 1)


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
