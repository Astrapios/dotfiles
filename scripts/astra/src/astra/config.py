"""Configuration, environment loading, logging, and shared state."""
import datetime
import os




def _load_env_file(path: str) -> dict[str, str]:
    """Load KEY=value pairs from a file. Skips blank lines and # comments."""
    env = {}
    try:
        with open(os.path.expanduser(path)) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, _, value = line.partition("=")
                    env[key.strip()] = value.strip()
    except OSError:
        pass
    return env


_creds = _load_env_file("~/.config/astra.env") or _load_env_file("~/.config/tg_hook.env")

BOT = os.environ.get("TELEGRAM_BOT_TOKEN", "") or _creds.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "") or _creds.get("TELEGRAM_CHAT_ID", "")
TG_HOOKS_ENABLED = os.environ.get("NO_ASTRA", "0") == "0"
TG_MAX = 4096  # Telegram message character limit
SIGNAL_DIR = "/tmp/astra_signals"

_god_new = os.path.expanduser("~/.config/astra_god_mode.json")
_god_old = os.path.expanduser("~/.config/tg_hook_god_mode.json")
GOD_MODE_PATH = _god_new if os.path.exists(_god_new) else (_god_old if os.path.exists(_god_old) else _god_new)


def _log(tag: str, msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] [{tag}] {msg}")


DEBUG_LOG = "/tmp/astra_debug.log"
_DEBUG_MAX = 512 * 1024  # 500 KB auto-truncate


def _is_debug_enabled() -> bool:
    """Check if debug logging is enabled."""
    return os.path.exists(os.path.join(SIGNAL_DIR, "_debug_on.json"))


def _set_debug(enabled: bool):
    """Enable or disable debug logging."""
    os.makedirs(SIGNAL_DIR, exist_ok=True)
    path = os.path.join(SIGNAL_DIR, "_debug_on.json")
    if enabled:
        with open(path, "w") as f:
            f.write("{}")
    else:
        try:
            os.remove(path)
        except OSError:
            pass
        try:
            os.remove(DEBUG_LOG)
        except OSError:
            pass


def _debug_tg(kind: str, detail: str, text: str):
    """Append a debug line if debug logging is enabled. Auto-truncates at _DEBUG_MAX."""
    if not _is_debug_enabled():
        return
    ts = datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    line = f"[{ts}] {kind} {detail} | {text[:500]}\n"
    try:
        with open(DEBUG_LOG, "a") as f:
            f.write(line)
        if os.path.getsize(DEBUG_LOG) > _DEBUG_MAX:
            with open(DEBUG_LOG, "r") as f:
                data = f.read()
            with open(DEBUG_LOG, "w") as f:
                f.write(data[len(data) // 2:])
    except OSError:
        pass


_remote_sessions: dict[str, float] = {}  # bare win_idx -> tg_send_timestamp


def _mark_remote(wid: str):
    """Mark a session as remotely active (TG interaction), disabling local suppress."""
    import re, time as _time
    m = re.match(r'^w?(\d+)', wid)
    if m:
        _remote_sessions[m.group(1)] = _time.time()


_last_messages: dict[str, str] = {}  # wid -> last sent message
_keyboard_messages: dict[str, int] = {}  # wid -> message_id with inline keyboard
_render_bodies: dict[int, str] = {}  # msg_id -> body text for "render as image"


def _save_last_msg(wid: str, msg: str):
    """Track the last message sent for a window."""
    _last_messages[wid.lstrip("w")] = msg


def _save_keyboard_msg(wid: str, msg_id: int):
    """Track a message with inline keyboard buttons for later cleanup."""
    _keyboard_messages[wid.lstrip("w")] = msg_id


def _clear_keyboard_msg(wid: str) -> int | None:
    """Pop and return the tracked keyboard message_id for a window, or None."""
    return _keyboard_messages.pop(wid.lstrip("w"), None)
