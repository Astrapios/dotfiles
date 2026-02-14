#!/usr/bin/env python
"""
Telegram bridge for Claude Code hooks.

Usage:
  tg-hook notify "message"        - Send a message, don't wait
  tg-hook ask "question"          - Send a message, wait for reply, print it to stdout
  tg-hook send-photo path [caption] - Send a photo to Telegram
  tg-hook hook                    - Read hook JSON from stdin, write signal for listen
  tg-hook listen                  - Auto-detect Claude sessions, route messages by wN prefix

Environment:
  TELEGRAM_BOT_TOKEN   - Bot token from @BotFather
  TELEGRAM_CHAT_ID     - Your chat ID
  CLAUDE_TG_HOOKS      - Set to "1" to enable hook signals (default: disabled)

Credentials fallback:
  ~/.config/tg_hook.env (KEY=value format, # comments allowed)

Signal-based architecture:
  Hooks write signal files to /tmp/tg_hook_signals/.
  Listen is the only process that talks to Telegram.
  When listen sees a signal, it captures the tmux pane and sends that.
"""
import difflib
import os
import re
import sys
import json
import time
import shlex
import subprocess
import requests


# ── Load credentials from env, falling back to ~/.config/tg_hook.env ─────────

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

_creds = _load_env_file("~/.config/tg_hook.env")

BOT = os.environ.get("TELEGRAM_BOT_TOKEN", "") or _creds.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "") or _creds.get("TELEGRAM_CHAT_ID", "")
TG_HOOKS_ENABLED = os.environ.get("CLAUDE_TG_HOOKS", "") == "1"
TG_MAX = 4096  # Telegram message character limit
SIGNAL_DIR = "/tmp/tg_hook_signals"


# ── Logging ──────────────────────────────────────────────────────────────────

def _log(tag: str, msg: str):
    print(f"[{tag}] {msg}")


# ── Telegram helpers ──────────────────────────────────────────────────────────

def tg_send(text: str, chat_id: str = CHAT_ID, reply_markup: dict | None = None) -> int:
    """Send a message to Telegram. Returns message_id."""
    text = text.strip()[:TG_MAX] or "(empty)"
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    r = requests.post(
        f"https://api.telegram.org/bot{BOT}/sendMessage",
        json=payload,
        timeout=30,
    )
    if r.status_code == 400:
        payload_plain: dict = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload_plain["reply_markup"] = reply_markup
        r = requests.post(
            f"https://api.telegram.org/bot{BOT}/sendMessage",
            json=payload_plain,
            timeout=30,
        )
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def _send_long_message(header: str, body: str, wid: str = "",
                       reply_markup: dict | None = None):
    """Send a header + body as one or more Telegram messages, chunking if needed.

    Body is wrapped in ``` code blocks. If the total exceeds TG_MAX, body is
    split across multiple messages at line boundaries.
    reply_markup is attached to the last chunk only so buttons appear at the bottom.
    """
    # Reserve space for header, code block markers, and safety margin
    overhead = len(header) + len("```\n") + len("\n```") + 50
    chunk_size = TG_MAX - overhead

    if len(body) <= chunk_size:
        msg = f"{header}```\n{body}\n```"
        tg_send(msg, reply_markup=reply_markup)
        _save_last_msg(wid, msg)
        return

    lines = body.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) > chunk_size and current:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))

    total = len(chunks)
    for i, chunk in enumerate(chunks):
        if i == 0:
            label = f"{header}(1/{total})\n"
        else:
            label = f"(cont. {i+1}/{total})\n"
        msg = f"{label}```\n{chunk}\n```"
        # Attach keyboard to last chunk only
        kb = reply_markup if i == total - 1 else None
        tg_send(msg, reply_markup=kb)
    # Save the first chunk as last message for /last
    if chunks:
        _save_last_msg(wid, f"{header}```\n{chunks[0]}\n```")


def tg_send_photo(path: str, caption: str = "", chat_id: str = CHAT_ID) -> int:
    """Send a photo to Telegram. Returns message_id."""
    with open(path, "rb") as f:
        data = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
            data["parse_mode"] = "Markdown"
        r = requests.post(
            f"https://api.telegram.org/bot{BOT}/sendPhoto",
            data=data,
            files={"photo": (os.path.basename(path), f, "image/png")},
            timeout=60,
        )
        if r.status_code == 400 and caption:
            # Markdown parse failure — retry without parse_mode
            f.seek(0)
            data.pop("parse_mode", None)
            r = requests.post(
                f"https://api.telegram.org/bot{BOT}/sendPhoto",
                data=data,
                files={"photo": (os.path.basename(path), f, "image/png")},
                timeout=60,
            )
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def _build_inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """Build InlineKeyboardMarkup from rows of (label, callback_data) tuples."""
    return {"inline_keyboard": [
        [{"text": label, "callback_data": data} for label, data in row]
        for row in rows
    ]}


def _answer_callback_query(callback_query_id: str, text: str = ""):
    """POST answerCallbackQuery to dismiss the button loading spinner."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _remove_inline_keyboard(message_id: int, chat_id: str = CHAT_ID):
    """POST editMessageReplyMarkup with empty keyboard to remove buttons."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id,
                  "reply_markup": {"inline_keyboard": []}},
            timeout=10,
        )
    except Exception:
        pass


def _set_bot_commands():
    """Register bot commands with Telegram so they appear in the / picker."""
    commands = [
        {"command": "status", "description": "Show last response or pane status"},
        {"command": "sessions", "description": "List active Claude sessions"},
        {"command": "help", "description": "Show available commands"},
        {"command": "focus", "description": "Monitor a session in real-time"},
        {"command": "unfocus", "description": "Stop real-time monitoring"},
        {"command": "interrupt", "description": "Interrupt current task (Esc)"},
        {"command": "last", "description": "Re-send last message for a session"},
        {"command": "new", "description": "Start new Claude session"},
        {"command": "stop", "description": "Pause the listener"},
        {"command": "start", "description": "Resume the listener"},
        {"command": "kill", "description": "Exit a Claude session (Ctrl+C)"},
        {"command": "quit", "description": "Shut down the listener"},
    ]
    try:
        requests.post(
            f"https://api.telegram.org/bot{BOT}/setMyCommands",
            json={"commands": commands},
            timeout=10,
        )
    except Exception:
        pass


def tg_wait_reply(after_message_id: int, timeout: int = 300) -> str:
    """Poll for a reply after a given message_id. Returns reply text."""
    send_time = int(time.time()) - 5
    offset = 0
    deadline = time.time() + timeout if timeout > 0 else float("inf")
    while time.time() < deadline:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{BOT}/getUpdates",
                params={"timeout": 10, "offset": offset},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            time.sleep(2)
            continue

        for upd in data.get("result", []):
            offset = max(offset, upd["update_id"] + 1)
            msg = upd.get("message", {})
            cid = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "")
            msg_date = msg.get("date", 0)

            if cid == str(CHAT_ID) and text and msg_date >= send_time:
                return text.strip()

        time.sleep(1)

    return "(no reply - timed out)"


def _poll_updates(offset: int, timeout: int = 1) -> tuple[dict | None, int]:
    """Poll Telegram getUpdates. Returns (response_data, new_offset).
    Returns (None, offset) on error. Lets KeyboardInterrupt propagate."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT}/getUpdates",
            params={"timeout": timeout, "offset": offset},
            timeout=timeout + 10,
        )
        r.raise_for_status()
        data = r.json()
    except KeyboardInterrupt:
        raise
    except Exception:
        return None, offset
    for upd in data.get("result", []):
        offset = max(offset, upd["update_id"] + 1)
    return data, offset


def _download_tg_photo(file_id: str, dest: str) -> str | None:
    """Download a Telegram file by file_id to dest path. Returns path or None."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        r.raise_for_status()
        file_path = r.json().get("result", {}).get("file_path", "")
        if not file_path:
            return None
        r2 = requests.get(
            f"https://api.telegram.org/file/bot{BOT}/{file_path}",
            timeout=60,
        )
        r2.raise_for_status()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(r2.content)
        return dest
    except Exception as e:
        _log("photo", f"Download failed: {e}")
        return None


def _extract_chat_messages(data: dict) -> list[dict]:
    """Extract messages and callback queries from our chat.

    Returns list of dicts with keys:
      - "text": str (message text or caption)
      - "photo": str | None (file_id of largest photo, if present)
      - "callback": dict | None ({"id", "data", "message_id"} for button presses)
    """
    messages = []
    for upd in data.get("result", []):
        # Handle callback queries (inline button presses)
        cb = upd.get("callback_query")
        if cb:
            cb_chat = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            if cb_chat == str(CHAT_ID):
                messages.append({
                    "text": "",
                    "photo": None,
                    "callback": {
                        "id": cb["id"],
                        "data": cb.get("data", ""),
                        "message_id": cb.get("message", {}).get("message_id", 0),
                    },
                })
            continue

        msg = upd.get("message", {})
        cid = str(msg.get("chat", {}).get("id", ""))
        if cid != str(CHAT_ID):
            continue
        text = msg.get("text", "")
        caption = msg.get("caption", "")
        photos = msg.get("photo")
        if photos:
            # Last element is highest resolution
            best = photos[-1]
            messages.append({
                "text": caption.strip(),
                "photo": best.get("file_id"),
                "callback": None,
            })
        elif text:
            messages.append({"text": text.strip(), "photo": None, "callback": None})
    return messages


# ── tmux helpers ──────────────────────────────────────────────────────────────

def get_window_id() -> str | None:
    """Get the tmux window index for the current pane (e.g. 'w0', 'w1')."""
    pane = os.environ.get("TMUX_PANE")
    if not pane:
        return None
    try:
        result = subprocess.run(
            ["tmux", "display-message", "-t", pane, "-p", "#{window_index}"],
            capture_output=True, text=True, timeout=5,
        )
        idx = result.stdout.strip()
        if idx.isdigit():
            return f"w{idx}"
    except Exception:
        pass
    return None


def get_pane_project(pane: str) -> str:
    """Get project name from a tmux pane's working directory."""
    try:
        res = subprocess.run(
            ["tmux", "display-message", "-t", pane, "-p", "#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
        cwd = res.stdout.strip()
        if cwd:
            return cwd.rstrip("/").rsplit("/", 1)[-1]
    except Exception:
        pass
    return "unknown"


def _get_pane_width(pane: str) -> int:
    """Get the character width of a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "display", "-t", pane, "-p", "#{pane_width}"],
            capture_output=True, text=True, timeout=5,
        )
        return int(result.stdout.strip())
    except Exception:
        return 0


def _join_wrapped_lines(lines: list[str], width: int) -> list[str]:
    """Join lines that were soft-wrapped by Claude Code's terminal formatter.

    Detects wraps by checking if the previous line is close to pane width
    and the current line is an indented continuation (not a new bullet/marker).
    """
    if width < 40 or not lines:
        return lines
    result = [lines[0]]
    for line in lines[1:]:
        prev_len = len(result[-1])
        s = line.lstrip()
        indent = len(line) - len(s)
        if (prev_len >= width - 15 and indent >= 2 and s and
                not re.match(r'[●•─━❯✻⏵⏸>*\-\d]', s)):
            result[-1] += " " + s
        else:
            result.append(line)
    return result


def _capture_pane(pane: str, num_lines: int = 20) -> str:
    """Capture the last num_lines from a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane, "-p", "-S", f"-{num_lines}"],
            capture_output=True, text=True, timeout=5,
        )
        lines = result.stdout.splitlines()
        if len(lines) > num_lines:
            return "\n".join(lines[-num_lines:]) + "\n"
        return result.stdout
    except Exception:
        return ""


def scan_claude_sessions() -> dict[str, tuple[str, str]]:
    """Scan tmux for panes running claude. Returns {window_index: (pane_target, project)}."""
    sessions = {}
    try:
        result = subprocess.run(
            ["tmux", "list-panes", "-a", "-F",
             "#{window_index}\t#{session_name}:#{window_index}.#{pane_index}\t#{pane_current_command}\t#{pane_current_path}"],
            capture_output=True, text=True, timeout=5,
        )
        for line in result.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 4:
                win_idx, target, cmd, cwd = parts
                if cmd == "claude":
                    project = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else "?"
                    sessions[win_idx] = (target, project)
    except Exception:
        pass
    return sessions


def format_sessions_message(sessions: dict[str, tuple[str, str]]) -> str:
    """Format a sessions map into a Telegram message."""
    if not sessions:
        return "⚠️ No Claude sessions found in tmux."
    lines = ["📋 *Active Claude sessions:*"]
    for idx in sorted(sessions, key=int):
        target, project = sessions[idx]
        lines.append(f"  `w{idx}` — `{project}` (`{target}`)")
    lines.append("\nPrefix messages with `wN` to route (e.g. `w1 fix the bug`).")
    return "\n".join(lines)


def _sessions_keyboard(sessions: dict) -> dict | None:
    """Build inline keyboard with one button per session."""
    if not sessions:
        return None
    buttons = []
    for idx in sorted(sessions, key=int):
        _, project = sessions[idx]
        buttons.append((f"w{idx} {project}"[:20], f"sess_{idx}"))
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    return _build_inline_keyboard(rows)


def _command_sessions_keyboard(cmd: str, sessions: dict) -> dict | None:
    """Build inline keyboard to pick a session for a command (focus, interrupt, kill)."""
    if not sessions:
        return None
    buttons = []
    for idx in sorted(sessions, key=int):
        _, project = sessions[idx]
        buttons.append((f"w{idx} {project}"[:20], f"cmd_{cmd}_{idx}"))
    rows = [buttons[i:i+3] for i in range(0, len(buttons), 3)]
    return _build_inline_keyboard(rows)


# ── Signal file handling ─────────────────────────────────────────────────────

def write_signal(event: str, data: dict, **extra):
    """Write a signal file for the listen loop to process."""
    os.makedirs(SIGNAL_DIR, exist_ok=True)
    pane = os.environ.get("TMUX_PANE", "")
    wid = get_window_id() or ""
    cwd = data.get("cwd", "")
    project = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else "unknown"
    signal = {
        "event": event,
        "pane": pane,
        "wid": wid,
        "project": project,
        **extra,
    }
    filename = f"{time.time():.6f}_{os.getpid()}.json"
    path = os.path.join(SIGNAL_DIR, filename)
    with open(path, "w") as f:
        json.dump(signal, f)


def _clear_signals(include_state: bool = False):
    """Remove signal files. If include_state, also removes _prefixed state files."""
    if not os.path.isdir(SIGNAL_DIR):
        return
    for f in os.listdir(SIGNAL_DIR):
        if not include_state and f.startswith("_"):
            continue
        try:
            os.remove(os.path.join(SIGNAL_DIR, f))
        except OSError:
            pass


def save_active_prompt(wid: str, pane: str, total: int,
                       shortcuts: dict[str, int] | None = None,
                       free_text_at: int | None = None,
                       remaining_qs: list[dict] | None = None,
                       project: str | None = None):
    """Save active prompt state so listen can route replies with arrow keys.

    Args:
        wid: Window ID (e.g. "w4").
        pane: tmux pane target.
        total: Total navigable options.
        shortcuts: Text aliases mapped to option numbers (e.g. {"y": 1, "n": 3}).
        free_text_at: Option index to navigate to for free-text input (Down N times), or None.
        remaining_qs: Remaining questions for multi-question AskUserQuestion prompts.
        project: Project name for formatting follow-up Telegram messages.
    """
    os.makedirs(SIGNAL_DIR, exist_ok=True)
    path = os.path.join(SIGNAL_DIR, f"_active_prompt_{wid}.json")
    state = {"pane": pane, "total": total, "ts": time.time()}
    if shortcuts:
        state["shortcuts"] = shortcuts
    if free_text_at is not None:
        state["free_text_at"] = free_text_at
    if remaining_qs is not None:
        state["remaining_qs"] = remaining_qs
    if project:
        state["project"] = project
    with open(path, "w") as f:
        json.dump(state, f)


def load_active_prompt(wid: str) -> dict | None:
    """Load and remove active prompt state. Returns None if missing."""
    path = os.path.join(SIGNAL_DIR, f"_active_prompt_{wid}.json")
    try:
        with open(path) as f:
            state = json.load(f)
        os.remove(path)
        return state
    except (OSError, json.JSONDecodeError):
        return None


def _pane_has_prompt(pane: str) -> bool:
    """Check if a tmux pane still shows a permission/question dialog."""
    try:
        raw = _capture_pane(pane, 10)
        for line in raw.splitlines():
            if re.match(r'^\s*[❯>]?\s*\d+\.\s+', line):
                return True
        return False
    except Exception:
        return False


def _cleanup_stale_prompts():
    """Remove active prompt files whose pane no longer shows a dialog."""
    if not os.path.isdir(SIGNAL_DIR):
        return
    for fname in os.listdir(SIGNAL_DIR):
        if not fname.startswith("_active_prompt_"):
            continue
        path = os.path.join(SIGNAL_DIR, fname)
        try:
            with open(path) as f:
                state = json.load(f)
            pane = state.get("pane", "")
            if pane and not _pane_has_prompt(pane):
                os.remove(path)
        except (OSError, json.JSONDecodeError):
            try:
                os.remove(path)
            except OSError:
                pass


def _save_focus_state(wid: str, pane: str, project: str):
    """Save focus target so listen monitors this pane."""
    os.makedirs(SIGNAL_DIR, exist_ok=True)
    path = os.path.join(SIGNAL_DIR, "_focus.json")
    with open(path, "w") as f:
        json.dump({"wid": wid, "pane": pane, "project": project}, f)


def _load_focus_state() -> dict | None:
    """Load focus state. Returns None if missing."""
    path = os.path.join(SIGNAL_DIR, "_focus.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _clear_focus_state():
    """Remove focus state file."""
    path = os.path.join(SIGNAL_DIR, "_focus.json")
    try:
        os.remove(path)
    except OSError:
        pass


def _extract_pane_permission(pane: str) -> tuple[str, str, list[str]]:
    """Extract content and options from a permission dialog in a tmux pane.
    Returns (header, content between last dot and options, list of options)."""
    raw = _capture_pane(pane)
    if not raw:
        return "", "", []
    lines = raw.splitlines()

    # Find options from last 8 lines only
    options = []
    for line in lines[-8:]:
        m = re.match(r'^\s*[❯>]?\s*(\d+\.\s+.+)', line)
        if m:
            options.append(m.group(1).strip())

    # Find the first option line index in full list
    first_opt_idx = len(lines)
    for i in range(len(lines) - 8, len(lines)):
        if i >= 0 and re.match(r'^\s*[❯>]?\s*\d+\.\s+', lines[i]):
            first_opt_idx = i
            break

    # Find last ● above the options
    start = 0
    for i in range(first_opt_idx - 1, -1, -1):
        if lines[i].strip().startswith("●"):
            start = i
            break

    # Extract tool + file from ● header (e.g. "● Update(scripts/tg-hook)")
    header = ""
    hdr_file = ""
    for line in lines[start:first_opt_idx]:
        s = line.strip()
        m_hdr = re.match(r'^● (\w+)\((.+?)\)', s)
        if m_hdr:
            header = f"wants to {m_hdr.group(1).lower()} `{m_hdr.group(2)}`"
            hdr_file = m_hdr.group(2)
            break

    # Clean: skip ● header, separators, chrome; dedent diff
    cleaned = []
    for line in lines[start:first_opt_idx]:
        s = line.strip()
        if s.startswith("●"):
            continue
        if re.match(r'^[─━╌]{3,}$', s):
            continue
        if s.startswith(("⎿", "Do you want", "Claude wants")):
            continue
        if s in ("Edit file", "Write file", "Create file", "Fetch", "Bash command"):
            continue
        if hdr_file and s in (hdr_file, hdr_file.rsplit("/", 1)[-1]):
            continue
        # Strip line numbers, keep -/+ at start for diff format
        m_diff = re.match(r'^\s*\d+\s*([+-])(.*)', line)
        m_ctx = re.match(r'^\s*\d+\s+(.*)', line)
        if m_diff:
            cleaned.append(f"{m_diff.group(1)}{m_diff.group(2)}")
        elif m_ctx:
            cleaned.append(f" {m_ctx.group(1)}")
        elif re.match(r'^\s*\d+\s*$', line):
            cleaned.append("")
        else:
            cleaned.append(line.strip())
    content = "\n".join(cleaned).strip()
    return header, content, options


_last_messages: dict[str, str] = {}  # wid -> last sent message


def _save_last_msg(wid: str, msg: str):
    """Track the last message sent for a window."""
    _last_messages[wid.lstrip("w")] = msg


def _format_question_msg(tag: str, project: str, question: dict) -> str:
    """Format a single AskUserQuestion question for Telegram."""
    parts = [f"❓{tag} Claude Code (`{project}`) asks:\n"]
    parts.append(question.get("question", "?"))
    opts = question.get("options", [])
    for i, opt in enumerate(opts, 1):
        label = opt.get("label", "?")
        desc = opt.get("description", "")
        if desc:
            parts.append(f"  {i}. {label} — {desc}")
        else:
            parts.append(f"  {i}. {label}")
    n = len(opts)
    parts.append(f"  {n+1}. Type your answer")
    parts.append(f"  {n+2}. Chat about this")
    return "\n".join(parts)


def process_signals(focused_wid: str | None = None) -> str | None:
    """Process pending signal files. Returns last window index (e.g. '4') or None.
    If focused_wid is set, stop signals for that window are suppressed."""
    if not os.path.isdir(SIGNAL_DIR):
        return None

    try:
        files = sorted(os.listdir(SIGNAL_DIR))
    except OSError:
        return None

    last_wid = None
    for fname in files:
        if not fname.endswith(".json") or fname.startswith("_"):
            continue
        fpath = os.path.join(SIGNAL_DIR, fname)
        try:
            with open(fpath) as f:
                signal = json.load(f)
        except (json.JSONDecodeError, OSError):
            try:
                os.remove(fpath)
            except OSError:
                pass
            continue

        event = signal.get("event", "")
        pane = signal.get("pane", "")
        wid = signal.get("wid", "")
        project = signal.get("project", "unknown")
        tag = f" {wid}" if wid else ""

        # Resolve project name from tmux pane
        if pane:
            project = get_pane_project(pane) or project

        if event == "stop":
            if focused_wid and wid.lstrip("w") == focused_wid:
                pass  # Focus is monitoring this pane — skip stop notification
            else:
                content = ""
                if pane:
                    time.sleep(4)
                    pw = _get_pane_width(pane)
                    # Progressive capture: expand if starting ● not found
                    for num_lines in (30, 80, 200):
                        content = _capture_pane(pane, num_lines)
                        if _has_response_start(content):
                            break
                else:
                    pw = 0
                cleaned = clean_pane_content(content, "stop", pw) if content else "(could not capture pane)"
                header = f"✅{tag} Claude Code (`{project}`) finished:\n\n"
                stop_kb = _build_inline_keyboard([[
                    ("\U0001f4cb Status", f"cmd_status_{wid}"),
                    ("\U0001f50d Focus", f"cmd_focus_{wid}"),
                ]])
                _send_long_message(header, cleaned, wid, reply_markup=stop_kb)

        elif event == "permission":
            bash_cmd = signal.get("cmd", "")
            header, content, options = _extract_pane_permission(pane)
            if options and not any(o.startswith("1.") for o in options):
                options.insert(0, "1. Yes")
            max_opt = 0
            for o in options:
                m_opt = re.match(r'(\d+)', o)
                if m_opt:
                    max_opt = max(max_opt, int(m_opt.group(1)))
            opts_text = "\n".join(options)
            if bash_cmd:
                msg = f"🔧{tag} Claude Code (`{project}`) needs permission:\n\n```\n{bash_cmd[:2000]}\n```\n{opts_text}"
            else:
                title = header or "needs permission"
                body = f"\n\n```\n{content[:2000]}\n```" if content else ""
                msg = f"🔧{tag} Claude Code (`{project}`) {title}:{body}\n{opts_text}"
            n = max_opt or 3
            perm_kb = _build_inline_keyboard([[
                ("\u2705 Allow", f"perm_{wid}_1"),
                ("\u274c Deny", f"perm_{wid}_{n}"),
                ("\u2705 Always", f"perm_{wid}_2"),
            ]])
            tg_send(msg, reply_markup=perm_kb)
            _save_last_msg(wid, msg)
            save_active_prompt(wid, pane, total=n,
                               shortcuts={"y": 1, "yes": 1, "allow": 1,
                                          "n": n, "no": n, "deny": n})

        elif event == "question":
            questions = signal.get("questions", [])
            if questions:
                # Send only the first question; remaining sent after each answer
                msg = _format_question_msg(tag, project, questions[0])
                opts = questions[0].get("options", [])
                q_buttons = [(opt.get("label", "?")[:20], f"q_{wid}_{i}")
                             for i, opt in enumerate(opts, 1)]
                q_rows = [q_buttons[i:i+3] for i in range(0, len(q_buttons), 3)]
                q_kb = _build_inline_keyboard(q_rows) if q_buttons else None
                tg_send(msg, reply_markup=q_kb)
                _save_last_msg(wid, msg)
                first_opts = len(questions[0].get("options", []))
                remaining = questions[1:] if len(questions) > 1 else None
                save_active_prompt(wid, pane, total=first_opts + 2,
                                   free_text_at=first_opts,
                                   remaining_qs=remaining,
                                   project=project)
            else:
                msg = f"❓{tag} Claude Code (`{project}`) asks:\n\n(check terminal)"
                tg_send(msg)
                _save_last_msg(wid, msg)

        try:
            os.remove(fpath)
        except OSError:
            pass
        if wid:
            last_wid = wid.lstrip("w")  # "w4" → "4"
        _log("signal", f"{event} for {wid} ({project})")

    return last_wid


# ── Pane content cleaning ────────────────────────────────────────────────────

def _filter_noise(raw: str) -> list[str]:
    """Filter common UI noise from captured pane content."""
    lines = raw.splitlines()
    while lines and not lines[-1].strip():
        lines.pop()
    filtered = []
    for line in lines:
        s = line.strip()
        if re.match(r'^[─━]{3,}$', s):
            continue
        if s.startswith(("⏵⏵ ", "⏸ ")):
            continue
        if s.startswith("Context left until auto-compact:"):
            continue
        if s in ("⏳ Working...", "* Working..."):
            continue
        if re.match(r'^✻ \w+ for ', s):
            continue
        # Volatile: spinner/status with timer (e.g. "✢ Coalescing… (6m 8s · …)")
        if re.match(r'^[^\w\s] \w', s) and re.search(r'\d+[hms]', s):
            continue
        # Volatile: overflow line count with timer (e.g. "+12499 more lines (5m 1s …)")
        if re.match(r'^\+\d+ more lines \(', s):
            continue
        # Background hint
        if s.startswith('ctrl+') and 'background' in s:
            continue
        filtered.append(line.rstrip())
    return filtered


def _has_response_start(raw: str) -> bool:
    """Check if captured pane content contains the ● text bullet that starts a response.

    Returns True if a text bullet (not a tool call like ● Bash(...)) is found
    before the last ❯ prompt line.
    """
    lines = raw.splitlines()
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip().startswith("❯"):
            end = i
            break
    for i in range(end - 1, -1, -1):
        s = lines[i].strip()
        if s.startswith("●") and not re.match(r'^● \w+\(', s):
            return True
    return False


def clean_pane_content(raw: str, event: str, pane_width: int = 0) -> str:
    """Clean captured tmux pane content."""
    lines = raw.splitlines()
    if event == "stop":
        # Find last ❯, then find the ● before it — that's the response
        end = len(lines)
        for i in range(len(lines) - 1, -1, -1):
            if lines[i].strip().startswith("❯"):
                end = i
                break
        start = 0
        for i in range(end - 1, -1, -1):
            s = lines[i].strip()
            if s.startswith("●") and not re.match(r'^● \w+\(', s):
                start = i
                break
        lines = lines[start:end]
    filtered = _filter_noise("\n".join(lines))
    if pane_width:
        filtered = _join_wrapped_lines(filtered, pane_width)
    return "\n".join(filtered).strip()


def clean_pane_status(raw: str, pane_width: int = 0) -> str:
    """Clean captured pane content for /status display."""
    filtered = _filter_noise(raw)
    if pane_width:
        filtered = _join_wrapped_lines(filtered, pane_width)
    return "\n".join(filtered).strip()


def _compute_new_lines(old_lines: list[str], new_lines: list[str]) -> list[str]:
    """Find genuinely new (inserted) lines between two captures.

    Uses SequenceMatcher to handle scrolling offsets and in-place changes:
    - 'insert': new lines that didn't exist before → included
    - 'replace': lines changed in place (timers, progress) → skipped
    - 'delete': lines scrolled off the top → skipped
    - 'equal': unchanged lines → skipped
    """
    if not old_lines:
        return new_lines
    sm = difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False)
    opcodes = sm.get_opcodes()
    # Count equal lines to detect meaningful overlap
    equal_count = sum(j2 - j1 for tag, _, _, j1, j2 in opcodes if tag == "equal")
    if equal_count < 3:
        # Content scrolled past capture window — no reliable anchor
        return new_lines
    new = []
    for tag, _i1, _i2, j1, j2 in opcodes:
        if tag == "insert":
            new.extend(new_lines[j1:j2])
    return new


# ── Pane routing ─────────────────────────────────────────────────────────────

def _select_option(pane: str, n: int):
    """Navigate to option n (1-based) and press Enter in a tmux pane."""
    p = shlex.quote(pane)
    parts = []
    if n > 1:
        nav = " ".join(["Down"] * (n - 1))
        parts.append(f"tmux send-keys -t {p} {nav}")
        parts.append("sleep 0.1")
    parts.append(f"tmux send-keys -t {p} Enter")
    subprocess.run(["bash", "-c", " && ".join(parts)], timeout=10)


def route_to_pane(pane: str, win_idx: str, text: str) -> str:
    """Route a message to a tmux pane, handling active prompts.

    If there's an active prompt, translates the reply into arrow-key
    navigation + Enter. Otherwise sends raw text.
    Returns a confirmation message for Telegram.
    """
    wid = f"w{win_idx}"
    prompt = load_active_prompt(wid)

    if prompt:
        total = prompt.get("total", 0)
        shortcuts = prompt.get("shortcuts", {})
        free_text_at = prompt.get("free_text_at")
        remaining_qs = prompt.get("remaining_qs")
        reply = text.strip()
        _log("route", f"prompt found: total={total}, reply={reply!r}, pane={pane}")

        prompt_pane = prompt.get("pane", pane)

        proj = prompt.get("project", "")
        tag = f" {wid}" if wid else ""

        def _advance_question():
            """Handle next question or auto-confirm submission."""
            if remaining_qs:
                next_q = remaining_qs[0]
                rest = remaining_qs[1:]
                n_opts = len(next_q.get("options", []))
                # Send next question to Telegram
                msg = _format_question_msg(tag, proj, next_q)
                tg_send(msg)
                _save_last_msg(wid, msg)
                save_active_prompt(wid, prompt_pane, total=n_opts + 2,
                                   free_text_at=n_opts,
                                   remaining_qs=rest,
                                   project=proj)
            elif remaining_qs is not None:
                # Last question answered — prompt user to confirm submission
                msg = f"❓{tag} Submit answers? (y/n)"
                yn_kb = _build_inline_keyboard([
                    [("\u2705 Yes", f"perm_{wid}_1"), ("\u274c No", f"perm_{wid}_2")],
                ])
                tg_send(msg, reply_markup=yn_kb)
                _save_last_msg(wid, msg)
                save_active_prompt(wid, prompt_pane, total=2,
                                   shortcuts={"y": 1, "yes": 1,
                                              "n": 2, "no": 2})

        # Shortcut match (e.g. "y" → 1, "n" → 3)
        if reply.lower() in shortcuts:
            n = shortcuts[reply.lower()]
            _select_option(prompt_pane, n)
            _advance_question()
            return f"📨 Selected option {n} in `{wid}`"

        # Numbered selection
        if reply.isdigit():
            n = int(reply)
            if 1 <= n <= total:
                _select_option(prompt_pane, n)
                _advance_question()
                return f"📨 Selected option {n} in `{wid}`"

        # Free text → navigate to "Type something.", type directly, Enter to submit
        if free_text_at is not None:
            pp = shlex.quote(prompt_pane)
            nav = " ".join(["Down"] * free_text_at)
            cmd = (f"tmux send-keys -t {pp} {nav} && sleep 0.2 && "
                   f"tmux send-keys -t {pp} -l {shlex.quote(reply)} && sleep 0.1 && "
                   f"tmux send-keys -t {pp} Enter")
            subprocess.run(["bash", "-c", cmd], timeout=10)
            _advance_question()
            return f"📨 Answered in `{wid}`:\n`{reply[:500]}`"

        # Prompt with no free text and no matching shortcut/number — default to option 1
        _select_option(prompt_pane, 1)
        _advance_question()
        return f"📨 Selected option 1 in `{wid}`"

    # Normal message: type text + Enter
    p = shlex.quote(pane)
    cmd = f"tmux send-keys -t {p} -l {shlex.quote(text)} && tmux send-keys -t {p} Enter"
    subprocess.run(["bash", "-c", cmd], timeout=10)
    return f"📨 Sent to `{wid}`:\n`{text[:500]}`"


# ── Commands ─────────────────────────────────────────────────────────────────

def cmd_notify(message: str):
    """Send a notification, no reply expected."""
    tg_send(message)


def cmd_ask(question: str) -> str:
    """Send a question, wait for reply, print to stdout."""
    msg_id = tg_send(f"❓ *Claude Code asks:*\n{question}\n\nReply to respond")
    reply = tg_wait_reply(msg_id)
    print(reply)
    return reply


def cmd_send_photo(path: str, caption: str = ""):
    """Send a photo file to Telegram."""
    if not os.path.isfile(path):
        print(f"File not found: {path}", file=sys.stderr)
        sys.exit(1)
    tg_send_photo(path, caption)
    print(f"Photo sent: {path}")


def cmd_hook():
    """Read hook JSON from stdin, write signal files for listen to process."""
    if not TG_HOOKS_ENABLED:
        sys.stdin.read()
        return
    raw = sys.stdin.read()
    if not raw.strip():
        return

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return

    event = data.get("hook_event_name", "")
    tool = data.get("tool_name", "")

    if event == "Stop":
        write_signal("stop", data)
    elif event == "Notification":
        ntype = data.get("notification_type", "")
        if ntype == "permission_prompt":
            msg = data.get("message", "")
            # AskUserQuestion fires "needs your attention" but has no blocking
            # permission dialog — just suppress, PreToolUse handles the question
            if "needs your attention" in msg:
                return
            wid = get_window_id() or "unknown"
            bash_cmd = ""
            if "bash" in msg.lower():
                cmd_file = os.path.join(SIGNAL_DIR, f"_bash_cmd_{wid}.json")
                try:
                    with open(cmd_file) as f:
                        bash_cmd = json.load(f).get("cmd", "")
                    os.remove(cmd_file)
                except (OSError, json.JSONDecodeError):
                    pass
            write_signal("permission", data, cmd=bash_cmd, message=msg)
    elif event == "PreToolUse":
        if tool == "AskUserQuestion":
            write_signal("question", data, questions=data.get("tool_input", {}).get("questions", []))
        elif tool == "Bash":
            os.makedirs(SIGNAL_DIR, exist_ok=True)
            wid = get_window_id() or "unknown"
            cmd = data.get("tool_input", {}).get("command", "")
            cmd_file = os.path.join(SIGNAL_DIR, f"_bash_cmd_{wid}.json")
            with open(cmd_file, "w") as f:
                json.dump({"cmd": cmd}, f)


_ALIASES: dict[str, str] = {"?": "/help", "uf": "/unfocus"}


def _any_active_prompt() -> bool:
    """Check if any active prompt state files exist."""
    if not os.path.isdir(SIGNAL_DIR):
        return False
    return any(f.startswith("_active_prompt_") for f in os.listdir(SIGNAL_DIR))


def _resolve_alias(text: str, has_active_prompt: bool) -> str:
    """Resolve short aliases. Suppressed when a prompt is active."""
    if has_active_prompt:
        return text
    stripped = text.strip()
    # Exact matches
    if stripped in _ALIASES:
        return _ALIASES[stripped]
    # s, s4, s4 10 → /status ...
    m = re.match(r"^s(\d+)?(?:\s+(\d+))?$", stripped)
    if m:
        parts = ["/status"]
        if m.group(1):
            parts.append(f"w{m.group(1)}")
        if m.group(2):
            parts.append(m.group(2))
        return " ".join(parts)
    # f4 → /focus w4
    m = re.match(r"^f(\d+)$", stripped)
    if m:
        return f"/focus w{m.group(1)}"
    # i4 → /interrupt w4
    m = re.match(r"^i(\d+)$", stripped)
    if m:
        return f"/interrupt w{m.group(1)}"
    return text


def _handle_command(text: str, sessions: dict, last_win_idx: str | None,
                    cmd_help: str) -> tuple[str | None, dict, str | None]:
    """Handle a command in active mode. Returns (action, sessions, last_win_idx).
    action is 'pause', 'quit', or None (continue processing)."""

    if text.lower() == "/stop":
        tg_send("⏸ Paused. Send `/start` to resume or `/quit` to exit.")
        _log("listen", "Paused.")
        return "pause", sessions, last_win_idx

    if text.lower() == "/quit":
        quit_kb = _build_inline_keyboard([
            [("\u2705 Yes", "quit_y"), ("\u274c No", "quit_n")],
        ])
        tg_send("⚠️ Shut down listener? Reply `y` to confirm.", reply_markup=quit_kb)
        return "quit_pending", sessions, last_win_idx

    if text.lower() == "/help":
        sessions = scan_claude_sessions()
        help_lines = [
            "📖 *Commands:*",
            "`/sessions` — list active Claude sessions",
            "`/status [wN] [lines]` — show last response or N filtered lines",
            "`/last [wN]` — re-send last Telegram message for a session",
            "`/focus wN` — monitor a session in real-time",
            "`/unfocus` — stop real-time monitoring",
            "`/new [dir]` — start new Claude session (default: `~/projects/`)",
            "`/interrupt [wN]` — interrupt current task (Esc)",
            "`/kill wN` — exit a Claude session (Ctrl+C x3)",
            "`/stop` — pause the listener",
            "`/quit` — shut down the listener",
            "",
            "*Aliases:*",
            "`s` status | `s4` status w4 | `s4 10` status w4 10",
            "`f4` focus w4 | `uf` unfocus | `i4` interrupt w4",
            "`?` help",
            "",
            "*Routing:* prefix with `wN` (e.g. `w4 fix the bug`) or send without prefix for single/last-used session.",
            "*Photos:* send a photo to have Claude read it. Add `wN` in caption to target.",
        ]
        tg_send("\n".join(help_lines), reply_markup=_sessions_keyboard(sessions))
        return None, sessions, last_win_idx

    if text.lower() == "/sessions":
        sessions = scan_claude_sessions()
        tg_send(format_sessions_message(sessions),
                reply_markup=_sessions_keyboard(sessions))
        return None, sessions, last_win_idx

    # /status [wN] [lines]
    status_m = re.match(r"^/status(?:\s+w?(\d+))?(?:\s+(\d+))?$", text.lower())
    if status_m:
        idx = status_m.group(1)
        num_lines = int(status_m.group(2)) if status_m.group(2) else 20
        targets = []
        if idx and idx in sessions:
            targets = [(idx, sessions[idx])]
        elif idx:
            tg_send(f"⚠️ No session `w{idx}`.\n{format_sessions_message(sessions)}",
                    reply_markup=_sessions_keyboard(sessions))
            return None, sessions, last_win_idx
        elif len(sessions) == 1:
            targets = list(sessions.items())
        elif len(sessions) > 1:
            # Multiple sessions — show session picker
            kb = _command_sessions_keyboard("status", sessions)
            tg_send("📋 Status for which session?", reply_markup=kb)
            return None, sessions, last_win_idx
        else:
            tg_send("⚠️ No Claude sessions found. Send `/sessions` to rescan.")
            return None, sessions, last_win_idx
        explicit_lines = status_m.group(2) is not None
        for win_idx, (pane, project) in targets:
            pw = _get_pane_width(pane)
            if explicit_lines:
                # User asked for N filtered lines — capture extra, filter, take last N
                raw = _capture_pane(pane, num_lines * 3 + 20)
                filtered = clean_pane_status(raw, pw)
                lines = filtered.splitlines()
                content = "\n".join(lines[-num_lines:]) if lines else ""
            else:
                # Smart mode: show last bullet or 30 lines, whichever is bigger
                for n in (30, 80, 200):
                    raw = _capture_pane(pane, n)
                    if _has_response_start(raw):
                        break
                raw_view = clean_pane_status(_capture_pane(pane, 30), pw)
                if _has_response_start(raw):
                    bullet_view = clean_pane_content(raw, "stop", pw)
                    content = bullet_view if len(bullet_view) >= len(raw_view) else raw_view
                else:
                    content = raw_view
            content = content or "(empty)"
            header = f"📋 `w{win_idx}` — `{project}`:\n\n"
            _send_long_message(header, content, win_idx)
        return None, sessions, last_win_idx

    # /focus (bare — show session picker)
    if text.lower().strip() == "/focus":
        sessions = scan_claude_sessions()
        kb = _command_sessions_keyboard("focus", sessions)
        if kb:
            tg_send("🔍 Focus on which session?", reply_markup=kb)
        else:
            tg_send("⚠️ No Claude sessions found.")
        return None, sessions, last_win_idx

    # /focus wN
    focus_m = re.match(r"^/focus\s+w?(\d+)$", text.lower())
    if focus_m:
        idx = focus_m.group(1)
        if idx in sessions:
            pane, project = sessions[idx]
            _save_focus_state(idx, pane, project)
            pw = _get_pane_width(pane)
            content = clean_pane_status(_capture_pane(pane, 20), pw) or "(empty)"
            tg_send(f"🔍 Focusing on `w{idx}` (`{project}`). Send `/unfocus` to stop.\n\n```\n{content[-3000:]}\n```")
            return None, sessions, idx
        else:
            tg_send(f"⚠️ No session `w{idx}`.\n{format_sessions_message(sessions)}",
                    reply_markup=_sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # /unfocus
    if text.lower() == "/unfocus":
        _clear_focus_state()
        tg_send("🔍 Focus stopped.")
        return None, sessions, last_win_idx

    # /new [dir]
    new_m = re.match(r"^/new(?:\s+(.+))?$", text)
    if new_m:
        dir_arg = new_m.group(1).strip() if new_m.group(1) else None
        if dir_arg:
            work_dir = os.path.expanduser(dir_arg)
        else:
            ts = time.strftime("%m%d-%H%M")
            work_dir = os.path.expanduser(f"~/projects/claude-{ts}")
        os.makedirs(work_dir, exist_ok=True)
        try:
            result = subprocess.run(
                ["tmux", "new-window", "-d", "-P", "-F", "#{window_index}",
                 f"bash -c 'cd {shlex.quote(work_dir)} && claude'"],
                capture_output=True, text=True, timeout=10,
            )
            new_idx = result.stdout.strip()
            sessions = scan_claude_sessions()
            proj = work_dir.rstrip("/").rsplit("/", 1)[-1]
            tg_send(f"🚀 Started Claude in `w{new_idx}` (`{proj}`):\n`{work_dir}`")
            return None, sessions, new_idx
        except Exception as e:
            tg_send(f"⚠️ Failed to start session: `{e}`")
            return None, sessions, last_win_idx

    # /interrupt [wN]
    int_m = re.match(r"^/interrupt(?:\s+w?(\d+))?$", text.lower())
    if int_m:
        idx = int_m.group(1)
        if idx and idx in sessions:
            pane, project = sessions[idx]
            p = shlex.quote(pane)
            subprocess.run(["bash", "-c", f"tmux send-keys -t {p} Escape"], timeout=5)
            tg_send(f"⏹ Interrupted `w{idx}` (`{project}`).")
            return None, sessions, idx
        elif idx:
            tg_send(f"⚠️ No session `w{idx}`.\n{format_sessions_message(sessions)}",
                    reply_markup=_sessions_keyboard(sessions))
        elif len(sessions) == 1:
            idx = next(iter(sessions))
            pane, project = sessions[idx]
            p = shlex.quote(pane)
            subprocess.run(["bash", "-c", f"tmux send-keys -t {p} Escape"], timeout=5)
            tg_send(f"⏹ Interrupted `w{idx}` (`{project}`).")
            return None, sessions, idx
        else:
            sessions = scan_claude_sessions()
            kb = _command_sessions_keyboard("interrupt", sessions)
            if kb:
                tg_send("⏹ Interrupt which session?", reply_markup=kb)
            else:
                tg_send("⚠️ No Claude sessions found.")
        return None, sessions, last_win_idx

    # /kill (bare — show session picker)
    if text.lower().strip() == "/kill":
        sessions = scan_claude_sessions()
        kb = _command_sessions_keyboard("kill", sessions)
        if kb:
            tg_send("🛑 Kill which session?", reply_markup=kb)
        else:
            tg_send("⚠️ No Claude sessions found.")
        return None, sessions, last_win_idx

    # /kill wN
    kill_m = re.match(r"^/kill\s+w?(\d+)$", text.lower())
    if kill_m:
        idx = kill_m.group(1)
        if idx in sessions:
            pane, project = sessions[idx]
            p = shlex.quote(pane)
            # Three Ctrl+C with delays — reliably exits Claude Code
            subprocess.run(
                ["bash", "-c",
                 f"tmux send-keys -t {p} C-c && sleep 0.1 && "
                 f"tmux send-keys -t {p} C-c && sleep 0.1 && "
                 f"tmux send-keys -t {p} C-c"],
                timeout=10,
            )
            time.sleep(2)
            sessions = scan_claude_sessions()
            if idx in sessions:
                tg_send(f"⚠️ `w{idx}` (`{project}`) still running after Ctrl+C.")
            else:
                tg_send(f"🛑 Killed `w{idx}` (`{project}`).")
            return None, sessions, last_win_idx
        else:
            tg_send(f"⚠️ No session `w{idx}`.\n{format_sessions_message(sessions)}",
                    reply_markup=_sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # /last [wN]
    last_m = re.match(r"^/last(?:\s+w?(\d+))?$", text.lower())
    if last_m:
        idx = last_m.group(1)
        if idx and idx in _last_messages:
            tg_send(_last_messages[idx])
        elif idx:
            tg_send(f"⚠️ No saved message for `w{idx}`.")
        elif len(_last_messages) == 1:
            tg_send(list(_last_messages.values())[0])
        elif _last_messages:
            # Multiple sessions have saved messages — show picker
            # Only show sessions that have saved messages
            has_msgs = {k: sessions[k] for k in _last_messages if k in sessions}
            kb = _command_sessions_keyboard("last", has_msgs) if has_msgs else None
            if kb:
                tg_send("📋 Last message for which session?", reply_markup=kb)
            else:
                tg_send("⚠️ No saved messages.")
        else:
            tg_send("⚠️ No saved messages yet.")
        return None, sessions, last_win_idx

    # Parse wN prefix
    m = re.match(r"^w(\d+)\s+(.*)", text, re.DOTALL)
    if m:
        win_idx = m.group(1)
        prompt = m.group(2).strip()
        if win_idx in sessions:
            pane, project = sessions[win_idx]
            confirm = route_to_pane(pane, win_idx, prompt)
            tg_send(confirm)
            _log(f"w{win_idx}", confirm[:100])
            return None, sessions, win_idx
        else:
            tg_send(f"⚠️ No Claude session at `w{win_idx}`.\n{format_sessions_message(sessions)}",
                    reply_markup=_sessions_keyboard(sessions))
            return None, sessions, last_win_idx

    # No prefix — route to last used or only session
    target_idx = None
    if len(sessions) == 1:
        target_idx = next(iter(sessions))
    elif last_win_idx and last_win_idx in sessions:
        target_idx = last_win_idx

    if target_idx:
        pane, project = sessions[target_idx]
        confirm = route_to_pane(pane, target_idx, text)
        tg_send(confirm)
        _log(f"w{target_idx}", confirm[:100])
        return None, sessions, target_idx
    elif len(sessions) == 0:
        tg_send("⚠️ No Claude sessions found. Send `/sessions` to rescan.")
    else:
        tg_send(f"⚠️ Multiple sessions — prefix with `wN`.\n{format_sessions_message(sessions)}",
                reply_markup=_sessions_keyboard(sessions))

    return None, sessions, last_win_idx


def _handle_callback(callback: dict, sessions: dict,
                     last_win_idx: str | None) -> tuple[dict, str | None, str | None]:
    """Handle an inline keyboard callback. Returns (sessions, last_win_idx, action).

    action is "quit" when quit_y is pressed, None otherwise.
    """
    cb_id = callback["id"]
    cb_data = callback.get("data", "")
    msg_id = callback.get("message_id", 0)

    # Always dismiss spinner and remove keyboard
    _answer_callback_query(cb_id)
    if msg_id:
        _remove_inline_keyboard(msg_id)

    # Quit callbacks
    if cb_data == "quit_y":
        tg_send("👋 Bye.")
        return sessions, last_win_idx, "quit"
    if cb_data == "quit_n":
        tg_send("Cancelled.")
        return sessions, last_win_idx, None

    # Permission callback: perm_{wid}_{n}
    m = re.match(r"^perm_(w\d+)_(\d+)$", cb_data)
    if m:
        wid, n = m.group(1), int(m.group(2))
        prompt = load_active_prompt(wid)
        if prompt:
            total = prompt.get("total", 3)
            _select_option(prompt.get("pane", ""), n)
            if n == 1:
                label = "\u2705 Allowed"
            elif n == 2:
                label = "\u2705 Always allowed"
            elif n == total:
                label = "\u274c Denied"
            else:
                label = f"Selected option {n}"
            tg_send(f"{label} in `{wid}`")
            _log("callback", f"perm {wid} option {n}")
        else:
            _answer_callback_query(cb_id, "Prompt expired")
        return sessions, last_win_idx, None

    # Question callback: q_{wid}_{n}
    m = re.match(r"^q_(w\d+)_(\d+)$", cb_data)
    if m:
        wid, n_str = m.group(1), m.group(2)
        win_idx = wid.lstrip("w")
        if win_idx in sessions:
            pane = sessions[win_idx][0]
            confirm = route_to_pane(pane, win_idx, n_str)
            tg_send(confirm)
            last_win_idx = win_idx
        return sessions, last_win_idx, None

    # Command callbacks: cmd_{action}_{wid}
    m = re.match(r"^cmd_(status|focus|interrupt|kill|last)_(w?)(\d+)$", cb_data)
    if m:
        cmd, _, idx = m.group(1), m.group(2), m.group(3)
        text = f"/{cmd} w{idx}"
        _, sessions, last_win_idx = _handle_command(
            text, sessions, last_win_idx, "")
        return sessions, last_win_idx, None

    # Session select: sess_{wid}
    m = re.match(r"^sess_(\d+)$", cb_data)
    if m:
        idx = m.group(1)
        last_win_idx = idx
        text = f"/status w{idx}"
        _, sessions, last_win_idx = _handle_command(
            text, sessions, last_win_idx, "")
        return sessions, last_win_idx, None

    _log("callback", f"unknown callback_data: {cb_data}")
    return sessions, last_win_idx, None


def cmd_listen():
    """Poll Telegram and auto-route messages to Claude sessions by wN prefix."""
    _clear_signals()
    # Clear stale prompt state — after restart, no in-memory context to handle them
    if os.path.isdir(SIGNAL_DIR):
        for f in os.listdir(SIGNAL_DIR):
            if f.startswith("_active_prompt_") or f.startswith("_bash_cmd_"):
                try:
                    os.remove(os.path.join(SIGNAL_DIR, f))
                except OSError:
                    pass

    sessions = scan_claude_sessions()
    last_scan = time.time()
    last_win_idx = None
    RESCAN_INTERVAL = 60

    # Prompt cleanup timer
    last_prompt_cleanup: float = 0

    # Focus monitoring state
    focus_target_wid: str | None = None
    focus_pane_width: int = 0
    focus_prev_lines: list[str] = []
    focus_pending: list[str] = []
    focus_last_new_ts: float = 0
    focus_first_new_ts: float = 0

    # Consume existing updates to avoid replaying old messages
    offset = 0
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{BOT}/getUpdates",
            params={"timeout": 0, "offset": -1},
            timeout=10,
        )
        results = r.json().get("result", [])
        if results:
            offset = results[-1]["update_id"] + 1
    except Exception:
        pass

    CMD_HELP = "`/help` | `/sessions` | `/status [wN]` | `/last [wN]` | `/focus wN` | `/unfocus` | `/new [dir]` | `/interrupt [wN]` | `/kill wN` | `/stop` pause | `/quit` exit"

    _set_bot_commands()
    help_msg = format_sessions_message(sessions) + "\n\n" + CMD_HELP
    tg_send(help_msg, reply_markup=_sessions_keyboard(sessions))
    _log("listen", f"Found {len(sessions)} Claude session(s).")
    _log("listen", "Press Ctrl+C to stop")

    paused = False
    quit_pending = False
    script_path = os.path.realpath(__file__)
    script_mtime = os.path.getmtime(script_path)

    while True:
        # Auto-reload on file change
        try:
            if os.path.getmtime(script_path) != script_mtime:
                _log("listen", "Script changed, reloading...")
                tg_send("🔄 Reloading...")
                os.execv(sys.executable, [sys.executable, script_path, "listen"])
        except OSError:
            pass

        # --- Paused mode: only respond to /start, /help, /quit ---
        if paused:
            try:
                data, offset = _poll_updates(offset, timeout=5)
            except KeyboardInterrupt:
                tg_send("👋 Bye.")
                break
            if data is None:
                time.sleep(2)
                continue

            for chat_msg in _extract_chat_messages(data):
                text = chat_msg["text"]
                if text.lower() == "/start":
                    _clear_signals(include_state=True)
                    sessions = scan_claude_sessions()
                    last_scan = time.time()
                    paused = False
                    focus_target_wid = None
                    focus_pane_width = 0
                    focus_prev_lines = []
                    focus_pending = []
                    focus_last_new_ts = 0
                    focus_first_new_ts = 0
                    help_msg = format_sessions_message(sessions) + "\n\n" + CMD_HELP
                    tg_send("▶️ Resumed.\n\n" + help_msg,
                            reply_markup=_sessions_keyboard(sessions))
                    _log("listen", "Resumed listening.")
                elif text.lower() == "/quit":
                    tg_send("👋 Bye.")
                    return
                elif text.lower() == "/help":
                    tg_send("⏸ Paused. Send `/start` to resume or `/quit` to exit.")
                else:
                    tg_send("⏸ Paused. Send `/start` to resume.")
            continue

        # --- Active mode ---
        if time.time() - last_scan > RESCAN_INTERVAL:
            sessions = scan_claude_sessions()
            last_scan = time.time()

        # Periodically clean up stale prompts (pane no longer shows dialog)
        if time.time() - last_prompt_cleanup > 5:
            _cleanup_stale_prompts()
            last_prompt_cleanup = time.time()

        focus_state = _load_focus_state()

        signal_wid = process_signals(focused_wid=focus_state["wid"] if focus_state else None)
        if signal_wid:
            last_win_idx = signal_wid

        # --- Focus monitoring ---
        if focus_state:
            fw = focus_state["wid"]
            if fw != focus_target_wid:
                focus_prev_lines = []
                focus_pending = []
                focus_last_new_ts = 0
                focus_first_new_ts = 0
                focus_target_wid = fw
                focus_pane_width = _get_pane_width(focus_state["pane"])

            fp, fproj = focus_state["pane"], focus_state["project"]

            if fw not in sessions:
                sessions = scan_claude_sessions()
                last_scan = time.time()
                if fw not in sessions:
                    _clear_focus_state()
                    tg_send(f"🔍 Focus on `w{fw}` ended — session gone.")
                    focus_target_wid = None
                    focus_state = None

        if focus_state:
            raw = _capture_pane(fp, 50)
            cur_lines = _filter_noise(raw)
            # Strip prompt line and continuation (user typing)
            for i in range(len(cur_lines) - 1, -1, -1):
                if cur_lines[i].strip().startswith("❯"):
                    cur_lines = cur_lines[:i]
                    break
            if focus_pane_width:
                cur_lines = _join_wrapped_lines(cur_lines, focus_pane_width)

            if focus_prev_lines:
                new = _compute_new_lines(focus_prev_lines, cur_lines)
                if new:
                    focus_pending.extend(new)
                    focus_last_new_ts = time.time()
                    if not focus_first_new_ts:
                        focus_first_new_ts = time.time()

            focus_prev_lines = cur_lines

            now = time.time()
            debounce_ok = focus_pending and focus_last_new_ts and (now - focus_last_new_ts >= 3)
            max_delay_ok = focus_pending and focus_first_new_ts and (now - focus_first_new_ts >= 15)

            if debounce_ok or max_delay_ok:
                chunk = "\n".join(focus_pending).strip()
                if chunk:
                    msg = f"🔍 `w{fw}` (`{fproj}`):\n```\n{chunk[:3500]}\n```"
                    tg_send(msg)
                    _save_last_msg(fw, msg)
                focus_pending = []
                focus_last_new_ts = 0
                focus_first_new_ts = 0
        elif focus_target_wid:
            focus_target_wid = None

        try:
            data, offset = _poll_updates(offset, timeout=1)
        except KeyboardInterrupt:
            tg_send("👋 Bye.")
            break
        if data is None:
            time.sleep(2)
            continue

        for chat_msg in _extract_chat_messages(data):
            # Handle inline keyboard callbacks
            callback = chat_msg.get("callback")
            if callback:
                sessions, last_win_idx, cb_action = _handle_callback(
                    callback, sessions, last_win_idx)
                if cb_action == "quit":
                    return
                # Reset quit_pending when quit buttons are used
                if quit_pending and callback.get("data", "").startswith("quit_"):
                    quit_pending = False
                continue

            text = chat_msg["text"]
            photo_id = chat_msg.get("photo")

            # Photo received — download and route to Claude
            if photo_id:
                dest = f"/tmp/tg_photo_{int(time.time())}.jpg"
                path = _download_tg_photo(photo_id, dest)
                if path:
                    caption = text
                    # Determine target session
                    target_idx = None
                    remaining_text = caption
                    m = re.match(r"^w(\d+)\s*(.*)", caption, re.DOTALL) if caption else None
                    if m and m.group(1) in sessions:
                        target_idx = m.group(1)
                        remaining_text = m.group(2).strip()
                    elif len(sessions) == 1:
                        target_idx = next(iter(sessions))
                    elif last_win_idx and last_win_idx in sessions:
                        target_idx = last_win_idx

                    if target_idx:
                        pane, project = sessions[target_idx]
                        # Build instruction for Claude to read the image
                        instruction = f"Read {path}"
                        if remaining_text:
                            instruction += f" — {remaining_text}"
                        p = shlex.quote(pane)
                        cmd = f"tmux send-keys -t {p} -l {shlex.quote(instruction)} && tmux send-keys -t {p} Enter"
                        subprocess.run(["bash", "-c", cmd], timeout=10)
                        tg_send(f"📷 Photo sent to `w{target_idx}` (`{project}`):\n`{path}`")
                        last_win_idx = target_idx
                    else:
                        tg_send(f"📷 Photo saved to `{path}` — no target session.\n{format_sessions_message(sessions)}",
                                reply_markup=_sessions_keyboard(sessions))
                else:
                    tg_send("⚠️ Failed to download photo.")
                continue

            # Handle quit confirmation
            if quit_pending:
                quit_pending = False
                if text.lower() in ("y", "yes"):
                    tg_send("👋 Bye.")
                    return
                else:
                    tg_send("Cancelled.")
                continue

            text = _resolve_alias(text, _any_active_prompt())
            prev_sessions = sessions
            action, sessions, last_win_idx = _handle_command(
                text, sessions, last_win_idx, CMD_HELP
            )
            if sessions is not prev_sessions:
                last_scan = time.time()
            if action == "pause":
                paused = True
                break
            elif action == "quit_pending":
                quit_pending = True
            elif action == "quit":
                return


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    # hook only writes signal files — no credentials needed
    if command == "hook":
        cmd_hook()
        return

    if not BOT or not CHAT_ID:
        print("Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID (env or ~/.config/tg_hook.env)", file=sys.stderr)
        sys.exit(1)

    if command == "notify":
        msg = sys.argv[2] if len(sys.argv) > 2 else "ping"
        cmd_notify(msg)
    elif command == "ask":
        question = sys.argv[2] if len(sys.argv) > 2 else "Yes or no?"
        cmd_ask(question)
    elif command == "send-photo":
        if len(sys.argv) < 3:
            print("Usage: tg-hook send-photo <path> [caption]", file=sys.stderr)
            sys.exit(1)
        path = sys.argv[2]
        caption = sys.argv[3] if len(sys.argv) > 3 else ""
        cmd_send_photo(path, caption)
    elif command == "listen":
        cmd_listen()
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
