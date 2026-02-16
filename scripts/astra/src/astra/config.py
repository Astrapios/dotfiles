"""Configuration, environment loading, logging, and shared state."""
import datetime
import os


LOG_FILE = "/tmp/astra.log"
_MAX_LOG_BYTES = 512 * 1024  # 512 KB


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
TG_HOOKS_ENABLED = os.environ.get("CLAUDE_ASTRA", "") == "1"
TG_MAX = 4096  # Telegram message character limit
SIGNAL_DIR = "/tmp/astra_signals"

_god_new = os.path.expanduser("~/.config/astra_god_mode.json")
_god_old = os.path.expanduser("~/.config/tg_hook_god_mode.json")
GOD_MODE_PATH = _god_new if os.path.exists(_god_new) else (_god_old if os.path.exists(_god_old) else _god_new)


def _log(tag: str, msg: str):
    ts = datetime.datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] [{tag}] {msg}"
    print(line)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _rotate_log():
    """Truncate log file if it exceeds max size (keep tail half)."""
    try:
        if os.path.getsize(LOG_FILE) > _MAX_LOG_BYTES:
            with open(LOG_FILE, "rb") as f:
                f.seek(-_MAX_LOG_BYTES // 2, 2)
                f.readline()  # skip partial line
                tail = f.read()
            with open(LOG_FILE, "wb") as f:
                f.write(tail)
    except OSError:
        pass


_last_messages: dict[str, str] = {}  # wid -> last sent message


def _save_last_msg(wid: str, msg: str):
    """Track the last message sent for a window."""
    _last_messages[wid.lstrip("w")] = msg
