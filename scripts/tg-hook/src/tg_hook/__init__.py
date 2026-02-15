"""
Telegram bridge for Claude Code hooks.

Usage:
  tg-hook notify "message"        - Send a message, don't wait
  tg-hook ask "question"          - Send a message, wait for reply, print it to stdout
  tg-hook send-photo path [caption] - Send a photo to Telegram
  tg-hook send-doc path [caption]   - Send a file as a document to Telegram
  tg-hook hook                    - Read hook JSON from stdin, write signal for listen
  tg-hook listen                  - Auto-detect Claude sessions, route messages by wN prefix
"""

# Import submodules in dependency order (no circular imports at module level)
from . import config
from . import telegram
from . import state
from . import tmux
from . import content
from . import signals
from . import routing
from . import commands
from . import listener
from . import cli

# Re-export everything for backward compatibility with tests
# (allows `import tg_hook as tg; tg.tg_send(...)`)

# config
from .config import (
    _load_env_file, BOT, CHAT_ID, TG_HOOKS_ENABLED, TG_MAX, SIGNAL_DIR,
    _log, _last_messages, _save_last_msg,
)

# telegram
from .telegram import (
    tg_send, _send_long_message, _get_image_dimensions, tg_send_document,
    tg_send_photo, _build_inline_keyboard,
    _answer_callback_query, _remove_inline_keyboard, _set_bot_commands,
    tg_wait_reply, _poll_updates, _download_tg_photo, _extract_chat_messages,
)

# tmux
from .tmux import (
    get_window_id, get_pane_project, _get_pane_width, _get_cursor_x,
    _join_wrapped_lines, _capture_pane, scan_claude_sessions,
    format_sessions_message, _sessions_keyboard, _command_sessions_keyboard,
)

# state
from .state import (
    write_signal, _clear_signals, save_active_prompt, load_active_prompt,
    _pane_has_prompt, _cleanup_stale_prompts,
    _save_focus_state, _load_focus_state, _clear_focus_state,
    _save_deepfocus_state, _load_deepfocus_state, _clear_deepfocus_state,
    _save_smartfocus_state, _load_smartfocus_state, _clear_smartfocus_state,
    _is_autofocus_enabled, _set_autofocus,
    _save_session_name, _clear_session_name, _load_session_names,
    _resolve_name, _wid_label,
    _save_queued_msg, _load_queued_msgs, _pop_queued_msgs,
    _save_prompt_text, _pop_prompt_text,
    _mark_busy, _is_busy, _busy_since, _clear_busy, _cleanup_stale_busy,
    _is_god_mode_for, _god_mode_wids, _set_god_mode, _clear_god_mode,
)

# content
from .content import (
    _extract_pane_permission, _filter_noise, _filter_tool_calls,
    _has_response_start, _detect_interrupted, clean_pane_content,
    clean_pane_status, _compute_new_lines,
)

# routing
from .routing import (
    _select_option, route_to_pane, _pane_idle_state,
    _is_ui_chrome,
)

# signals
from .signals import _format_question_msg, process_signals

# commands
from .commands import (
    _ALIASES, _any_active_prompt, _resolve_alias,
    _enable_accept_edits, _maybe_activate_smartfocus,
    _handle_command, _handle_callback,
)

# listener
from .listener import cmd_listen

# cli
from .cli import cmd_notify, cmd_ask, cmd_send_photo, cmd_send_doc, cmd_hook, cmd_help, main
