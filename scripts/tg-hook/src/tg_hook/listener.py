"""Main daemon loop."""
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time

import requests

from tg_hook import config, telegram, tmux, state, content, commands, signals


# Track file mtimes for auto-reload
_pkg_dir = pathlib.Path(__file__).parent
_file_mtimes: dict[pathlib.Path, float] = {}
_reload_after: float | None = None


def _init_file_mtimes():
    """Initialize file modification times for auto-reload detection."""
    global _file_mtimes
    _file_mtimes = {p: p.stat().st_mtime for p in _pkg_dir.glob("*.py")}


def _check_file_changes() -> bool:
    """Check if any package file has changed since last check."""
    for p, mtime in _file_mtimes.items():
        try:
            if p.stat().st_mtime != mtime:
                return True
        except OSError:
            pass
    return False


def cmd_listen():
    """Poll Telegram and auto-route messages to Claude sessions by wN prefix."""
    state._clear_signals()
    # Clear stale prompt state — after restart, no in-memory context to handle them
    if os.path.isdir(config.SIGNAL_DIR):
        for f in os.listdir(config.SIGNAL_DIR):
            if f.startswith(("_active_prompt_", "_bash_cmd_")):
                try:
                    os.remove(os.path.join(config.SIGNAL_DIR, f))
                except OSError:
                    pass

    sessions = tmux.scan_claude_sessions()
    last_scan = time.time()
    last_win_idx = None
    RESCAN_INTERVAL = 60

    last_prompt_cleanup: float = 0

    focus_target_wid: str | None = None
    focus_pane_width: int = 0
    focus_last_hash: int = 0

    deepfocus_target_wid: str | None = None
    deepfocus_pane_width: int = 0
    deepfocus_prev_lines: list[str] = []
    deepfocus_pending: list[str] = []
    deepfocus_last_new_ts: float = 0
    deepfocus_first_new_ts: float = 0

    smartfocus_target_wid: str | None = None
    smartfocus_pane_width: int = 0
    smartfocus_prev_lines: list[str] = []

    interrupted_notified: set[str] = set()  # wids already notified as interrupted
    last_interrupt_check: float = 0

    # Consume existing updates to avoid replaying old messages
    offset = 0
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{config.BOT}/getUpdates",
            params={"timeout": 0, "offset": -1},
            timeout=10,
        )
        results = r.json().get("result", [])
        if results:
            offset = results[-1]["update_id"] + 1
    except Exception:
        pass

    telegram._set_bot_commands()
    telegram.tg_send(tmux.format_sessions_message(sessions),
                     reply_markup=telegram._build_reply_keyboard())
    config._log("listen", f"Found {len(sessions)} Claude session(s).")
    config._log("listen", "Press Ctrl+C to stop")

    paused = False
    quit_pending = False
    _init_file_mtimes()

    while True:
        # Auto-reload on file change (debounced — wait for files to stabilize)
        global _reload_after
        try:
            if _check_file_changes():
                _init_file_mtimes()
                _reload_after = time.time() + 2.0
                config._log("listen", "Code change detected, reloading in 2s...")
            elif _reload_after and time.time() >= _reload_after:
                _reload_after = None
                config._log("listen", "Reloading...")
                telegram.tg_send("🔄 Reloading...")
                _restart_cmd = "import sys; sys.argv=['tg-hook','listen']; from tg_hook.cli import main; main()"
                os.execv(sys.executable, [sys.executable, "-c", _restart_cmd])
        except OSError:
            pass

        # --- Paused mode: only respond to /start, /help, /quit ---
        if paused:
            try:
                data, offset = telegram._poll_updates(offset, timeout=5)
            except KeyboardInterrupt:
                telegram.tg_send("👋 Bye.")
                break
            if data is None:
                time.sleep(2)
                continue

            for chat_msg in telegram._extract_chat_messages(data):
                text = chat_msg["text"]
                if text.lower() == "/start":
                    state._clear_signals(include_state=True)
                    sessions = tmux.scan_claude_sessions()
                    last_scan = time.time()
                    paused = False
                    focus_target_wid = None
                    focus_pane_width = 0
                    focus_last_hash = 0
                    deepfocus_target_wid = None
                    deepfocus_pane_width = 0
                    deepfocus_prev_lines = []
                    deepfocus_pending = []
                    deepfocus_last_new_ts = 0
                    deepfocus_first_new_ts = 0
                    smartfocus_target_wid = None
                    smartfocus_pane_width = 0
                    smartfocus_last_hash = 0
                    smartfocus_prev_lines = []
                    telegram.tg_send("▶️ Resumed.\n\n" + tmux.format_sessions_message(sessions),
                                     reply_markup=telegram._build_reply_keyboard())
                    config._log("listen", "Resumed listening.")
                elif text.lower() == "/quit":
                    telegram.tg_send("👋 Bye.")
                    return
                elif text.lower() == "/help":
                    telegram.tg_send("⏸ Paused. Send `/start` to resume or `/quit` to exit.")
                else:
                    telegram.tg_send("⏸ Paused. Send `/start` to resume.")
            continue

        # --- Active mode ---
        if time.time() - last_scan > RESCAN_INTERVAL:
            sessions = tmux.scan_claude_sessions()
            last_scan = time.time()

        if time.time() - last_prompt_cleanup > 5:
            state._cleanup_stale_prompts()
            state._cleanup_stale_busy(sessions)
            last_prompt_cleanup = time.time()

        # --- Interrupt detection (no hook fires on Esc interrupt) ---
        if time.time() - last_interrupt_check > 5:
            last_interrupt_check = time.time()
            from tg_hook import routing
            for idx, (pane, project) in sessions.items():
                wid = f"w{idx}"
                try:
                    raw = tmux._capture_pane(pane, 15)
                except Exception:
                    continue
                idle, _ = routing._pane_idle_state(pane)
                if not idle:
                    interrupted_notified.discard(wid)
                    continue
                # Pane is idle — clear stale busy flag (interrupt doesn't fire Stop hook)
                if state._is_busy(wid):
                    state._clear_busy(wid)
                if wid in interrupted_notified:
                    continue
                if content._detect_interrupted(raw):
                    label = state._wid_label(idx)
                    telegram.tg_send(f"⏹ {label} (`{project}`) was interrupted — waiting for instructions.")
                    interrupted_notified.add(wid)
            # Clear for gone sessions
            interrupted_notified -= interrupted_notified - {f"w{i}" for i in sessions}

        focus_state = state._load_focus_state()
        deepfocus_state = state._load_deepfocus_state()
        smartfocus_state = state._load_smartfocus_state()
        focused_wids: set[str] = set()
        if focus_state:
            focused_wids.add(focus_state["wid"])
        if deepfocus_state:
            focused_wids.add(deepfocus_state["wid"])

        signal_wid = signals.process_signals(focused_wids=focused_wids or None)
        if signal_wid:
            last_win_idx = signal_wid

        # --- Lightweight focus monitoring (completed responses only) ---
        if focus_state:
            fw = focus_state["wid"]
            if fw != focus_target_wid:
                focus_target_wid = fw
                focus_pane_width = tmux._get_pane_width(focus_state["pane"])
                focus_last_hash = 0
            fp, fproj = focus_state["pane"], focus_state["project"]
            if fw not in sessions:
                sessions = tmux.scan_claude_sessions()
                last_scan = time.time()
                if fw not in sessions:
                    state._clear_focus_state()
                    telegram.tg_send(f"🔍 Focus on {state._wid_label(fw)} ended — session gone.")
                    focus_target_wid = None
                    focus_state = None
            if focus_state:
                for n in (50, 150):
                    raw = tmux._capture_pane(fp, n)
                    if content._has_response_start(raw):
                        break
                cleaned = content.clean_pane_content(raw, "stop", focus_pane_width)
                if cleaned:
                    h = hash(cleaned)
                    if h != focus_last_hash and focus_last_hash != 0:
                        header = f"🔍 {state._wid_label(fw)} (`{fproj}`):\n\n"
                        telegram._send_long_message(header, cleaned, fw)
                    focus_last_hash = h
        elif focus_target_wid:
            focus_target_wid = None

        # --- Smart focus monitoring (auto-activated on message send) ---
        if smartfocus_state:
            sfw = smartfocus_state["wid"]
            # Skip if manual focus or deepfocus already covers this wid
            if (focus_state and focus_state["wid"] == sfw) or \
               (deepfocus_state and deepfocus_state["wid"] == sfw):
                pass
            else:
                if sfw != smartfocus_target_wid:
                    smartfocus_target_wid = sfw
                    smartfocus_pane_width = tmux._get_pane_width(smartfocus_state["pane"])
                    smartfocus_prev_lines = []
                sfp, sfproj = smartfocus_state["pane"], smartfocus_state["project"]
                if sfw not in sessions:
                    sessions = tmux.scan_claude_sessions()
                    last_scan = time.time()
                    if sfw not in sessions:
                        state._clear_smartfocus_state()
                        smartfocus_target_wid = None
                        smartfocus_prev_lines = []
                        smartfocus_state = None
                if smartfocus_state:
                    for n in (50, 150):
                        raw = tmux._capture_pane(sfp, n)
                        if content._has_response_start(raw):
                            break
                    cleaned = content.clean_pane_content(raw, "stop", smartfocus_pane_width)
                    if cleaned:
                        cur_lines = cleaned.splitlines()
                        if smartfocus_prev_lines:
                            new = content._compute_new_lines(smartfocus_prev_lines, cur_lines)
                            if new:
                                new_text = "\n".join(new).strip()
                                if new_text:
                                    header = f"👁 {state._wid_label(sfw)} (`{sfproj}`):\n\n"
                                    telegram._send_long_message(header, new_text, sfw)
                        smartfocus_prev_lines = cur_lines
        elif smartfocus_target_wid:
            smartfocus_target_wid = None
            smartfocus_prev_lines = []

        # --- Deep focus monitoring (streams all output) ---
        if deepfocus_state:
            dfw = deepfocus_state["wid"]
            if dfw != deepfocus_target_wid:
                deepfocus_prev_lines = []
                deepfocus_pending = []
                deepfocus_last_new_ts = 0
                deepfocus_first_new_ts = 0
                deepfocus_target_wid = dfw
                deepfocus_pane_width = tmux._get_pane_width(deepfocus_state["pane"])

            dfp, dfproj = deepfocus_state["pane"], deepfocus_state["project"]

            if dfw not in sessions:
                sessions = tmux.scan_claude_sessions()
                last_scan = time.time()
                if dfw not in sessions:
                    state._clear_deepfocus_state()
                    telegram.tg_send(f"🔬 Deep focus on {state._wid_label(dfw)} ended — session gone.")
                    deepfocus_target_wid = None
                    deepfocus_state = None

        if deepfocus_state:
            raw = tmux._capture_pane(dfp, 50)
            cur_lines = content._filter_noise(raw)
            for i in range(len(cur_lines) - 1, -1, -1):
                if cur_lines[i].strip().startswith("❯"):
                    cur_lines = cur_lines[:i]
                    break
            if deepfocus_pane_width:
                cur_lines = tmux._join_wrapped_lines(cur_lines, deepfocus_pane_width)

            if deepfocus_prev_lines:
                new = content._compute_new_lines(deepfocus_prev_lines, cur_lines)
                if new:
                    deepfocus_pending.extend(new)
                    deepfocus_last_new_ts = time.time()
                    if not deepfocus_first_new_ts:
                        deepfocus_first_new_ts = time.time()

            deepfocus_prev_lines = cur_lines

            now = time.time()
            debounce_ok = deepfocus_pending and deepfocus_last_new_ts and (now - deepfocus_last_new_ts >= 3)
            max_delay_ok = deepfocus_pending and deepfocus_first_new_ts and (now - deepfocus_first_new_ts >= 15)

            if debounce_ok or max_delay_ok:
                chunk = "\n".join(deepfocus_pending).strip()
                if chunk:
                    msg = f"🔬 {state._wid_label(dfw)} (`{dfproj}`):\n```\n{chunk[:3500]}\n```"
                    telegram.tg_send(msg)
                    config._save_last_msg(dfw, msg)
                deepfocus_pending = []
                deepfocus_last_new_ts = 0
                deepfocus_first_new_ts = 0
        elif deepfocus_target_wid:
            deepfocus_target_wid = None

        try:
            data, offset = telegram._poll_updates(offset, timeout=1)
        except KeyboardInterrupt:
            telegram.tg_send("👋 Bye.")
            break
        if data is None:
            time.sleep(2)
            continue

        for chat_msg in telegram._extract_chat_messages(data):
            callback = chat_msg.get("callback")
            if callback:
                sessions, last_win_idx, cb_action = commands._handle_callback(
                    callback, sessions, last_win_idx)
                if cb_action == "quit":
                    return
                if quit_pending and callback.get("data", "").startswith("quit_"):
                    quit_pending = False
                continue

            text = chat_msg["text"]
            photo_id = chat_msg.get("photo")

            # Reply-to routing: use wid from the replied-to message
            reply_wid = chat_msg.get("reply_wid")
            if reply_wid and reply_wid in sessions:
                last_win_idx = reply_wid

            # Photo received — download and route to Claude
            if photo_id:
                dest = f"/tmp/tg_photo_{int(time.time())}.jpg"
                path = telegram._download_tg_photo(photo_id, dest)
                if path:
                    caption = text
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
                        instruction = f"Read {path}"
                        if remaining_text:
                            instruction += f" — {remaining_text}"
                        p = shlex.quote(pane)
                        cmd = f"tmux send-keys -t {p} -l {shlex.quote(instruction)} && tmux send-keys -t {p} Enter"
                        subprocess.run(["bash", "-c", cmd], timeout=10)
                        telegram.tg_send(f"📷 Photo sent to `w{target_idx}` (`{project}`):\n`{path}`")
                        last_win_idx = target_idx
                    else:
                        telegram.tg_send(f"📷 Photo saved to `{path}` — no target session.\n{tmux.format_sessions_message(sessions)}",
                                         reply_markup=tmux._sessions_keyboard(sessions))
                else:
                    telegram.tg_send("⚠️ Failed to download photo.")
                continue

            # Handle quit confirmation
            if quit_pending:
                quit_pending = False
                if text.lower() in ("y", "yes"):
                    telegram.tg_send("👋 Bye.")
                    return
                else:
                    telegram.tg_send("Cancelled.")
                continue

            text = commands._resolve_alias(text, commands._any_active_prompt())
            prev_sessions = sessions
            action, sessions, last_win_idx = commands._handle_command(
                text, sessions, last_win_idx
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
