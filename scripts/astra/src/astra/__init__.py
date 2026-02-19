"""
Astra — Telegram bridge for Claude Code & Gemini CLI hooks.

Usage:
  astra notify "message"        - Send a message, don't wait
  astra ask "question"          - Send a message, wait for reply, print it to stdout
  astra send-photo path [caption] - Send a photo to Telegram
  astra send-doc path [caption]   - Send a file as a document to Telegram
  astra hook                    - Read hook JSON from stdin, write signal for listen
  astra listen                  - Auto-detect CLI sessions, route messages by wN prefix
"""

# Import submodules in dependency order (no circular imports at module level)
from . import config
from . import telegram
from . import state
from . import profiles
from . import tmux
from . import content
from . import signals
from . import routing
from . import commands
from . import listener
from . import cli

# Re-export everything for convenience
# (allows `import astra; astra.tg_send(...)`)

# config
from .config import (
    _load_env_file, BOT, CHAT_ID, TG_HOOKS_ENABLED, TG_MAX, SIGNAL_DIR,
    DEBUG_LOG, _is_debug_enabled, _set_debug, _debug_tg,
    _log, _last_messages, _save_last_msg,
)

# telegram
from .telegram import (
    tg_send, _send_long_message, _get_image_dimensions, tg_send_document,
    tg_send_photo, _build_inline_keyboard,
    _answer_callback_query, _remove_inline_keyboard, _set_bot_commands,
    tg_wait_reply, _poll_updates, _download_tg_file, _extract_chat_messages,
)

# profiles
from .profiles import (
    CLIProfile, register_profile, get_profile, all_profiles, identify_cli,
    CLAUDE, GEMINI,
)

# tmux
from .tmux import (
    get_window_id, get_pane_project, _get_pane_command, _get_pane_cwd, _get_pane_width, _get_cursor_x,
    _join_wrapped_lines, _capture_pane, _get_locally_viewed_windows,
    scan_claude_sessions, scan_cli_sessions, SessionInfo, resolve_session_id,
    format_sessions_message, _sessions_keyboard, _command_sessions_keyboard,
)

# state
from .state import (
    write_signal, _clear_signals, save_active_prompt, load_active_prompt,
    _pane_has_prompt, _cleanup_stale_prompts,
    _save_focus_state, _load_focus_state, _clear_focus_state,
    _save_deepfocus_state, _load_deepfocus_state, _clear_deepfocus_state,
    _save_smartfocus_state, _load_smartfocus_state, _clear_smartfocus_state,
    _is_local_suppress_enabled, _set_local_suppress,
    _is_autofocus_enabled, _set_autofocus,
    _save_session_name, _clear_session_name, _load_session_names,
    _resolve_name, _wid_label,
    _save_queued_msg, _load_queued_msgs, _pop_queued_msgs,
    _save_prompt_text, _pop_prompt_text,
    _mark_busy, _is_busy, _busy_since, _clear_busy, _cleanup_stale_busy,
    _clear_window_state, _clear_all_transient_state,
    _is_god_mode_for, _god_mode_wids, _set_god_mode, _clear_god_mode,
    _is_god_quiet, _set_god_quiet,
    _load_notification_config, _save_notification_config, _is_silent,
    NOTIFICATION_CONFIG_PATH, _NOTIFICATION_CATEGORIES, _DEFAULT_LOUD,
)

# content
from .content import (
    _extract_pane_permission, _filter_noise, _filter_tool_calls,
    _has_response_start, _detect_interrupted, _detect_compacting,
    clean_pane_content, clean_pane_status, _compute_new_lines,
)

# routing
from .routing import (
    _select_option, route_to_pane, _pane_idle_state,
    _get_session_statuses, _is_ui_chrome, _has_colored_spinner,
)

# signals
from .signals import _format_question_msg, process_signals

# commands
from .commands import (
    _ALIASES, _KEYS_MAP, _QUICK_KEYS, _resolve_key, _keys_combo_keyboard,
    _any_active_prompt, _resolve_alias,
    _enable_accept_edits, _maybe_activate_smartfocus,
    _handle_command, _handle_callback,
)

# listener
from .listener import _merge_album_photos, cmd_listen

# cli
from .cli import (
    cmd_notify, cmd_ask, cmd_send_photo, cmd_send_doc, cmd_hook, cmd_help,
    cmd_god, cmd_local, cmd_debug, cmd_autofocus, cmd_notification,
    cmd_status, cmd_focus, cmd_deepfocus, cmd_unfocus, cmd_clear,
    cmd_interrupt, cmd_keys, cmd_name, cmd_saved, cmd_log, cmd_new,
    cmd_restart, cmd_kill, main,
)
