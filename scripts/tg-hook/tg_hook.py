#!/usr/bin/env python
"""
Telegram bridge for Claude Code hooks.

Usage:
  tg-hook notify "message"        - Send a message, don't wait
  tg-hook ask "question"          - Send a message, wait for reply, print it to stdout
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
PROMPT_EXPIRY = 300  # seconds before active prompt state expires


# ── Logging ──────────────────────────────────────────────────────────────────

def _log(tag: str, msg: str):
    print(f"[{tag}] {msg}")


# ── Telegram helpers ──────────────────────────────────────────────────────────

def tg_send(text: str, chat_id: str = CHAT_ID) -> int:
    """Send a message to Telegram. Returns message_id."""
    text = text.strip()[:TG_MAX] or "(empty)"
    r = requests.post(
        f"https://api.telegram.org/bot{BOT}/sendMessage",
        json={"chat_id": chat_id, "text": text, "parse_mode": "Markdown"},
        timeout=30,
    )
    if r.status_code == 400:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT}/sendMessage",
            json={"chat_id": chat_id, "text": text},
            timeout=30,
        )
    r.raise_for_status()
    return r.json()["result"]["message_id"]


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


def _extract_chat_messages(data: dict) -> list[str]:
    """Extract message texts from our chat. Returns list of stripped texts."""
    messages = []
    for upd in data.get("result", []):
        msg = upd.get("message", {})
        cid = str(msg.get("chat", {}).get("id", ""))
        text = msg.get("text", "")
        if cid == str(CHAT_ID) and text:
            messages.append(text.strip())
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


def _capture_pane(pane: str, num_lines: int = 20) -> str:
    """Capture recent lines from a tmux pane."""
    try:
        result = subprocess.run(
            ["tmux", "capture-pane", "-t", pane, "-p", "-S", f"-{num_lines}"],
            capture_output=True, text=True, timeout=5,
        )
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


def save_active_prompt(wid: str, prompt_type: str, pane: str, num_options: int):
    """Save active prompt state so listen can route replies with arrow keys."""
    os.makedirs(SIGNAL_DIR, exist_ok=True)
    path = os.path.join(SIGNAL_DIR, f"_active_prompt_{wid}.json")
    with open(path, "w") as f:
        json.dump({"type": prompt_type, "pane": pane, "num_options": num_options, "ts": time.time()}, f)


def load_active_prompt(wid: str) -> dict | None:
    """Load and remove active prompt state. Returns None if expired or missing."""
    path = os.path.join(SIGNAL_DIR, f"_active_prompt_{wid}.json")
    try:
        with open(path) as f:
            state = json.load(f)
        os.remove(path)
        if time.time() - state.get("ts", 0) > PROMPT_EXPIRY:
            return None
        return state
    except (OSError, json.JSONDecodeError):
        return None


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


def process_signals() -> str | None:
    """Process pending signal files. Returns last window index (e.g. '4') or None."""
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
            content = ""
            if pane:
                time.sleep(4)
                content = _capture_pane(pane, 30)
            cleaned = clean_pane_content(content, "stop") if content else "(could not capture pane)"
            tg_send(f"✅{tag} Claude Code (`{project}`) finished:\n\n```\n{cleaned[:3000]}\n```")

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
                tg_send(f"🔧{tag} Claude Code (`{project}`) needs permission:\n\n```\n{bash_cmd[:2000]}\n```\n{opts_text}")
            else:
                title = header or "needs permission"
                body = f"\n\n```\n{content[:2000]}\n```" if content else ""
                tg_send(f"🔧{tag} Claude Code (`{project}`) {title}:{body}\n{opts_text}")
            save_active_prompt(wid, "permission", pane, max_opt or 3)

        elif event == "question":
            questions = signal.get("questions", [])
            if questions:
                parts = [f"❓{tag} Claude Code (`{project}`) asks:\n"]
                total_opts = 0
                for q in questions:
                    parts.append(q.get("question", "?"))
                    opts = q.get("options", [])
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
                    total_opts = n
                tg_send("\n".join(parts))
                save_active_prompt(wid, "question", pane, total_opts)
            else:
                tg_send(f"❓{tag} Claude Code (`{project}`) asks:\n\n(check terminal)")

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
        filtered.append(line)
    return filtered


def clean_pane_content(raw: str, event: str) -> str:
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
    return "\n".join(filtered).strip()


def clean_pane_status(raw: str) -> str:
    """Clean captured pane content for /status display."""
    filtered = _filter_noise(raw)
    return "\n".join(filtered).strip()


# ── Pane routing ─────────────────────────────────────────────────────────────

def route_to_pane(pane: str, win_idx: str, text: str) -> str:
    """Route a message to a tmux pane, handling active prompts.

    If there's an active question/permission prompt, translates the reply
    into arrow-key navigation + Enter. Otherwise sends raw text.
    Returns a confirmation message for Telegram.
    """
    wid = f"w{win_idx}"
    prompt = load_active_prompt(wid)

    if prompt:
        num_opts = prompt.get("num_options", 0)
        prompt_type = prompt.get("type", "")
        reply = text.strip()
        _log("route", f"prompt found: type={prompt_type}, num_opts={num_opts}, reply={reply!r}, pane={pane}")

        p = shlex.quote(pane)

        # Numbered selection
        if reply.isdigit():
            n = int(reply)
            total = num_opts + 2 if prompt_type == "question" else num_opts
            if 1 <= n <= total:
                nav = " ".join(["Down"] * (n - 1))
                parts = [f"tmux send-keys -t {p} {nav}"] if n > 1 else []
                if n > 1:
                    parts.append("sleep 0.1")
                parts.append(f"tmux send-keys -t {p} Enter")
                subprocess.run(["bash", "-c", " && ".join(parts)], timeout=10)
                return f"📨 Selected option {n} in `{wid}`"

        # Free text for questions → navigate to "Other", type, submit
        if prompt_type == "question":
            prompt_pane = prompt.get("pane", pane)
            pp = shlex.quote(prompt_pane)
            nav = " ".join(["Down"] * num_opts)
            cmd = f"tmux send-keys -t {pp} {nav} && sleep 0.1 && tmux send-keys -t {pp} -l {shlex.quote(reply)} && tmux send-keys -t {pp} Enter"
            subprocess.run(["bash", "-c", cmd], timeout=10)
            return f"📨 Answered in `{wid}`:\n`{reply[:500]}`"

        # y/n shortcuts for permission
        if prompt_type == "permission":
            if reply.lower() in ("y", "yes", "allow"):
                n = 1
            elif reply.lower() in ("n", "no", "deny"):
                n = num_opts
            else:
                n = 1  # default to allow
            parts = []
            if n > 1:
                nav = " ".join(["Down"] * (n - 1))
                parts.append(f"tmux send-keys -t {p} {nav}")
                parts.append("sleep 0.1")
            parts.append(f"tmux send-keys -t {p} Enter")
            subprocess.run(["bash", "-c", " && ".join(parts)], timeout=10)
            label = "Allowed" if n == 1 else "Denied"
            return f"📨 {label} in `{wid}`"

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
            wid = get_window_id() or "unknown"
            msg = data.get("message", "")
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


def _handle_command(text: str, sessions: dict, last_win_idx: str | None,
                    cmd_help: str) -> tuple[str | None, dict, str | None]:
    """Handle a command in active mode. Returns (action, sessions, last_win_idx).
    action is 'pause', 'quit', or None (continue processing)."""

    if text.lower() == "/stop":
        tg_send("⏸ Paused. Send `/start` to resume or `/quit` to exit.")
        _log("listen", "Paused.")
        return "pause", sessions, last_win_idx

    if text.lower() == "/quit":
        tg_send("👋 Bye.")
        return "quit", sessions, last_win_idx

    if text.lower() == "/help":
        sessions = scan_claude_sessions()
        help_msg = format_sessions_message(sessions) + "\n\n" + cmd_help
        tg_send(help_msg)
        return None, sessions, last_win_idx

    if text.lower() == "/sessions":
        sessions = scan_claude_sessions()
        tg_send(format_sessions_message(sessions))
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
            tg_send(f"⚠️ No session `w{idx}`.\n{format_sessions_message(sessions)}")
            return None, sessions, last_win_idx
        elif len(sessions) == 1:
            targets = list(sessions.items())
        elif last_win_idx and last_win_idx in sessions:
            targets = [(last_win_idx, sessions[last_win_idx])]
        else:
            targets = list(sessions.items())
        for win_idx, (pane, project) in targets:
            content = clean_pane_status(_capture_pane(pane, num_lines)) or "(empty)"
            tg_send(f"📋 `w{win_idx}` — `{project}`:\n\n```\n{content[-3000:]}\n```")
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
            tg_send(f"⚠️ No Claude session at `w{win_idx}`.\n{format_sessions_message(sessions)}")
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
        tg_send(f"⚠️ Multiple sessions — prefix with `wN`.\n{format_sessions_message(sessions)}")

    return None, sessions, last_win_idx


def cmd_listen():
    """Poll Telegram and auto-route messages to Claude sessions by wN prefix."""
    _clear_signals()

    sessions = scan_claude_sessions()
    last_scan = time.time()
    last_win_idx = None
    RESCAN_INTERVAL = 60

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

    CMD_HELP = "`/help` | `/sessions` | `/status [wN]` | `/stop` pause | `/quit` exit"

    help_msg = format_sessions_message(sessions) + "\n\n" + CMD_HELP
    tg_send(help_msg)
    _log("listen", f"Found {len(sessions)} Claude session(s).")
    _log("listen", "Press Ctrl+C to stop")

    paused = False
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

            for text in _extract_chat_messages(data):
                if text.lower() == "/start":
                    _clear_signals(include_state=True)
                    sessions = scan_claude_sessions()
                    last_scan = time.time()
                    paused = False
                    help_msg = format_sessions_message(sessions) + "\n\n" + CMD_HELP
                    tg_send("▶️ Resumed.\n\n" + help_msg)
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

        signal_wid = process_signals()
        if signal_wid:
            last_win_idx = signal_wid

        try:
            data, offset = _poll_updates(offset, timeout=1)
        except KeyboardInterrupt:
            tg_send("👋 Bye.")
            break
        if data is None:
            time.sleep(2)
            continue

        for text in _extract_chat_messages(data):
            prev_sessions = sessions
            action, sessions, last_win_idx = _handle_command(
                text, sessions, last_win_idx, CMD_HELP
            )
            if sessions is not prev_sessions:
                last_scan = time.time()
            if action == "pause":
                paused = True
                break
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
    elif command == "listen":
        cmd_listen()
    else:
        print(f"Unknown command: {command}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
