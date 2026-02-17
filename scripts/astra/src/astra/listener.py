"""Main daemon loop."""
import fcntl
import os
import pathlib
import re
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field

import requests

from astra import config, telegram, tmux, state, content, commands, signals, routing

# Notification category constants (see state._NOTIFICATION_CATEGORIES)
_CAT_ERROR = 4
_CAT_INTERRUPT = 5
_CAT_MONITOR = 6
_CAT_CONFIRM = 7

# Track file mtimes for auto-reload
_pkg_dir = pathlib.Path(__file__).parent
_file_mtimes: dict[pathlib.Path, float] = {}
_reload_after: float | None = None

_RESCAN_INTERVAL = 60


def _merge_album_photos(messages: list[dict]) -> list[dict]:
    """Merge photo messages sharing a media_group_id into a single entry.

    Album photos arrive as separate messages with the same media_group_id.
    This merges them so the first message accumulates all file_ids in a
    'photos' list, and non-album messages pass through unchanged.
    """
    groups: dict[str, int] = {}  # media_group_id -> index in result
    result: list[dict] = []
    for msg in messages:
        mgid = msg.get("media_group_id")
        if mgid and msg.get("photo"):
            if mgid in groups:
                # Append photo to existing album entry
                result[groups[mgid]]["photos"].append(msg["photo"])
                # Keep caption from whichever message has one
                if msg.get("text") and not result[groups[mgid]]["text"]:
                    result[groups[mgid]]["text"] = msg["text"]
            else:
                # First photo in this album
                groups[mgid] = len(result)
                merged = dict(msg)
                merged["photos"] = [msg["photo"]]
                result.append(merged)
        else:
            result.append(msg)
    return result


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


def _build_pending_prompt(files: list[dict]) -> tuple[str, dict]:
    """Build prompt message and inline keyboard for pending file(s)."""
    kb = telegram._build_inline_keyboard([
        [("⏭ Skip", "file_skip"), ("🗑 Cancel", "file_cancel")],
    ])
    if len(files) == 1:
        f = files[0]
        if f["type"] == "photo":
            msg = f"📷 Photo received\n`{f['path']}`"
        else:
            display = f.get("display", os.path.basename(f["path"]))
            msg = f"📎 Document received: `{display}`"
    else:
        lines = []
        for f in files:
            name = f.get("display", os.path.basename(f["path"]))
            icon = "📷" if f["type"] == "photo" else "📎"
            lines.append(f"  {icon} `{name}`")
        msg = f"📎 {len(files)} files received:\n" + "\n".join(lines)
    msg += "\n\nReply with instructions:"
    return msg, kb


def _build_file_instruction(files: list[dict], user_text: str = "") -> str:
    """Build a Read instruction from accumulated pending files."""
    paths = [f["path"] for f in files]
    if len(paths) == 1:
        instruction = f"Read {paths[0]}"
    else:
        all_photos = all(f["type"] == "photo" for f in files)
        label = "images" if all_photos else "files"
        instruction = f"Read these {label}: " + ", ".join(paths)
    if user_text and user_text not in ("-", "skip"):
        instruction += f" — {user_text}"
    return instruction


@dataclass
class _ListenerState:
    """All mutable state for the listener loop."""
    sessions: dict = field(default_factory=dict)
    last_scan: float = 0
    last_win_idx: str | None = None
    offset: int = 0
    paused: bool = False
    quit_pending: bool = False
    pending_file: dict | None = None
    last_prompt_cleanup: float = 0
    focus_target_wid: str | None = None
    focus_pane_width: int = 0
    focus_last_hash: int = 0
    deepfocus_target_wid: str | None = None
    deepfocus_pane_width: int = 0
    deepfocus_prev_lines: list = field(default_factory=list)
    deepfocus_pending: list = field(default_factory=list)
    deepfocus_last_new_ts: float = 0
    deepfocus_first_new_ts: float = 0
    smartfocus_target_wid: str | None = None
    smartfocus_pane_width: int = 0
    smartfocus_prev_lines: list = field(default_factory=list)
    smartfocus_has_sent: bool = False
    compact_notified: set = field(default_factory=set)
    last_interrupt_check: float = 0
    interrupted_notified: set = field(default_factory=set)
    god_wids: list = field(default_factory=list)


def _listen_tick(s):
    """Execute one iteration of the listener loop.

    Mutates *s* in place.  Returns ``None`` to continue looping,
    ``"quit"`` to exit the listener, or ``"pause_break"`` when
    transitioning to paused mode.  ``KeyboardInterrupt`` from
    ``_poll_updates`` propagates to the caller.
    """
    global _reload_after

    # Auto-reload on file change (debounced — wait for files to stabilize)
    try:
        if _check_file_changes():
            _init_file_mtimes()
            _reload_after = time.time() + 2.0
            config._log("listen", "Code change detected, reloading in 2s...")
        elif _reload_after and time.time() >= _reload_after:
            _reload_after = None
            config._log("listen", "Reloading...")
            telegram.tg_send("🔄 Reloading...", silent=state._is_silent(_CAT_CONFIRM))
            _restart_cmd = "import sys; sys.argv=['astra','listen']; from astra.cli import main; main()"
            os.execv(sys.executable, [sys.executable, "-c", _restart_cmd])
    except OSError:
        pass

    # --- Paused mode: only respond to /start, /help, /quit ---
    if s.paused:
        data, s.offset = telegram._poll_updates(s.offset, timeout=5)
        if data is None:
            time.sleep(2)
            return None

        for chat_msg in telegram._extract_chat_messages(data):
            text = chat_msg["text"]
            if text.lower() == "/start":
                state._clear_signals(include_state=True)
                s.sessions = tmux.scan_claude_sessions()
                s.last_scan = time.time()
                s.paused = False
                s.focus_target_wid = None
                s.focus_pane_width = 0
                s.focus_last_hash = 0
                s.deepfocus_target_wid = None
                s.deepfocus_pane_width = 0
                s.deepfocus_prev_lines = []
                s.deepfocus_pending = []
                s.deepfocus_last_new_ts = 0
                s.deepfocus_first_new_ts = 0
                s.smartfocus_target_wid = None
                s.smartfocus_pane_width = 0
                s.smartfocus_prev_lines = []
                s.smartfocus_has_sent = False
                statuses = routing._get_session_statuses(s.sessions)
                s.interrupted_notified = {idx for idx, st in statuses.items() if st == "interrupted"}
                resume_viewed = tmux._get_locally_viewed_windows() if state._is_local_suppress_enabled() else set()
                telegram.tg_send("▶️ Resumed.\n\n" + tmux.format_sessions_message(s.sessions, statuses=statuses,
                                                                                   locally_viewed=resume_viewed or None),
                                 reply_markup=telegram._build_reply_keyboard())
                config._log("listen", "Resumed listening.")
            elif text.lower() == "/quit":
                telegram.tg_send("👋 Bye.")
                return "quit"
            elif text.lower() == "/help":
                telegram.tg_send("⏸ Paused. Send `/start` to resume or `/quit` to exit.")
            else:
                telegram.tg_send("⏸ Paused. Send `/start` to resume.")
        return None

    # --- Active mode ---
    if time.time() - s.last_scan > _RESCAN_INTERVAL:
        s.sessions = tmux.scan_claude_sessions()
        s.last_scan = time.time()

    if time.time() - s.last_prompt_cleanup > 5:
        state._cleanup_stale_prompts()
        state._cleanup_stale_busy(s.sessions)
        s.last_prompt_cleanup = time.time()

    locally_viewed = tmux._get_locally_viewed_windows() if state._is_local_suppress_enabled() else set()

    # --- Interrupt detection (no hook fires on Esc interrupt) ---
    if time.time() - s.last_interrupt_check > 5:
        s.last_interrupt_check = time.time()
        for wid, (pane, project) in s.sessions.items():
            win_idx = re.match(r'^w?(\d+)', wid).group(1)
            try:
                raw = tmux._capture_pane(pane, 15)
            except Exception:
                continue
            idle, _ = routing._pane_idle_state(pane)
            if not idle:
                s.interrupted_notified.discard(wid)
                continue
            # Pane is idle — clear stale busy flag (interrupt doesn't fire Stop hook)
            if state._is_busy(wid):
                state._clear_busy(wid)
            if wid in s.interrupted_notified:
                continue
            if content._detect_interrupted(raw):
                if win_idx not in locally_viewed:
                    label = state._wid_label(wid)
                    telegram.tg_send(f"⏹ {label} (`{project}`) was interrupted — waiting for instructions.",
                                     silent=state._is_silent(_CAT_INTERRUPT))
                else:
                    config._log("local", f"suppressed interrupt for {wid} ({project})")
                s.interrupted_notified.add(wid)
        # Clear for gone sessions
        s.interrupted_notified -= s.interrupted_notified - set(s.sessions)

        # --- Auto-compact detection ---
        for wid, (pane, project) in s.sessions.items():
            win_idx = re.match(r'^w?(\d+)', wid).group(1)
            try:
                raw = tmux._capture_pane(pane, 15)
            except Exception:
                continue
            if content._detect_compacting(raw):
                if wid not in s.compact_notified:
                    if win_idx not in locally_viewed:
                        label = state._wid_label(wid)
                        telegram.tg_send(f"⏳ {label} (`{project}`) is auto-compacting context\u2026",
                                         silent=state._is_silent(_CAT_MONITOR))
                    else:
                        config._log("local", f"suppressed compact for {wid} ({project})")
                    s.compact_notified.add(wid)
            else:
                if wid in s.compact_notified:
                    if win_idx not in locally_viewed:
                        label = state._wid_label(wid)
                        telegram.tg_send(f"✅ {label} finished compacting.",
                                         silent=state._is_silent(_CAT_MONITOR))
                    s.compact_notified.discard(wid)
        # Clear for gone sessions
        s.compact_notified -= s.compact_notified - set(s.sessions)

    focus_state = state._load_focus_state()
    deepfocus_state = state._load_deepfocus_state()
    smartfocus_state = state._load_smartfocus_state()
    focused_wids: set[str] = set()
    if focus_state:
        focused_wids.add(focus_state["wid"])
    if deepfocus_state:
        focused_wids.add(deepfocus_state["wid"])

    signal_wid = signals.process_signals(
        focused_wids=focused_wids or None,
        smartfocus_prev=s.smartfocus_prev_lines if smartfocus_state else None,
        smartfocus_has_sent=s.smartfocus_has_sent if smartfocus_state else False,
        locally_viewed=locally_viewed or None,
    )
    # Re-read smartfocus state — process_signals may have cleared it
    smartfocus_state = state._load_smartfocus_state()
    if signal_wid:
        s.last_win_idx = signal_wid

    # --- Lightweight focus monitoring (completed responses only) ---
    if focus_state:
        fw = focus_state["wid"]
        if fw != s.focus_target_wid:
            s.focus_target_wid = fw
            s.focus_pane_width = tmux._get_pane_width(focus_state["pane"])
            s.focus_last_hash = 0
        fp, fproj = focus_state["pane"], focus_state["project"]
        if fw not in s.sessions:
            s.sessions = tmux.scan_claude_sessions()
            s.last_scan = time.time()
            if fw not in s.sessions:
                state._clear_focus_state()
                telegram.tg_send(f"🔍 Focus on {state._wid_label(fw)} ended — session gone.")
                s.focus_target_wid = None
                focus_state = None
        if focus_state:
            for n in (50, 150):
                raw = tmux._capture_pane(fp, n)
                if content._has_response_start(raw):
                    break
            cleaned = content.clean_pane_content(raw, "stop", s.focus_pane_width)
            if cleaned:
                h = hash(cleaned)
                if h != s.focus_last_hash and s.focus_last_hash != 0:
                    header = f"🔍 {state._wid_label(fw)} (`{fproj}`):\n\n"
                    telegram._send_long_message(header, cleaned, fw, silent=state._is_silent(_CAT_MONITOR))
                s.focus_last_hash = h
    elif s.focus_target_wid:
        s.focus_target_wid = None

    # --- Smart focus monitoring (auto-activated on message send) ---
    if smartfocus_state:
        sfw = smartfocus_state["wid"]
        # Skip if manual focus or deepfocus already covers this wid
        if (focus_state and focus_state["wid"] == sfw) or \
           (deepfocus_state and deepfocus_state["wid"] == sfw):
            pass
        else:
            if sfw != s.smartfocus_target_wid:
                s.smartfocus_target_wid = sfw
                s.smartfocus_pane_width = tmux._get_pane_width(smartfocus_state["pane"])
                s.smartfocus_prev_lines = []
                s.smartfocus_has_sent = False
            sfp, sfproj = smartfocus_state["pane"], smartfocus_state["project"]
            if sfw not in s.sessions:
                s.sessions = tmux.scan_claude_sessions()
                s.last_scan = time.time()
                if sfw not in s.sessions:
                    state._clear_smartfocus_state()
                    s.smartfocus_target_wid = None
                    s.smartfocus_prev_lines = []
                    smartfocus_state = None
            if smartfocus_state:
                raw = tmux._capture_pane(sfp, 50)
                cur_lines = content._filter_noise(raw)
                for i in range(len(cur_lines) - 1, -1, -1):
                    if cur_lines[i].strip().startswith("❯"):
                        cur_lines = cur_lines[:i]
                        break
                if s.smartfocus_pane_width:
                    cur_lines = tmux._join_wrapped_lines(cur_lines, s.smartfocus_pane_width)
                if s.smartfocus_prev_lines:
                    new = content._compute_new_lines(s.smartfocus_prev_lines, cur_lines)
                    if new:
                        new_text = "\n".join(new).strip()
                        if new_text:
                            header = f"👁 {state._wid_label(sfw)} (`{sfproj}`):\n\n"
                            telegram._send_long_message(header, new_text, sfw, silent=state._is_silent(_CAT_MONITOR))
                            s.smartfocus_has_sent = True
                s.smartfocus_prev_lines = cur_lines
    elif s.smartfocus_target_wid:
        s.smartfocus_target_wid = None
        s.smartfocus_prev_lines = []
        s.smartfocus_has_sent = False

    # --- Deep focus monitoring (streams all output) ---
    if deepfocus_state:
        dfw = deepfocus_state["wid"]
        if dfw != s.deepfocus_target_wid:
            s.deepfocus_prev_lines = []
            s.deepfocus_pending = []
            s.deepfocus_last_new_ts = 0
            s.deepfocus_first_new_ts = 0
            s.deepfocus_target_wid = dfw
            s.deepfocus_pane_width = tmux._get_pane_width(deepfocus_state["pane"])

        dfp, dfproj = deepfocus_state["pane"], deepfocus_state["project"]

        if dfw not in s.sessions:
            s.sessions = tmux.scan_claude_sessions()
            s.last_scan = time.time()
            if dfw not in s.sessions:
                state._clear_deepfocus_state()
                telegram.tg_send(f"🔬 Deep focus on {state._wid_label(dfw)} ended — session gone.")
                s.deepfocus_target_wid = None
                deepfocus_state = None

    if deepfocus_state:
        raw = tmux._capture_pane(dfp, 50)
        cur_lines = content._filter_noise(raw)
        for i in range(len(cur_lines) - 1, -1, -1):
            if cur_lines[i].strip().startswith("❯"):
                cur_lines = cur_lines[:i]
                break
        if s.deepfocus_pane_width:
            cur_lines = tmux._join_wrapped_lines(cur_lines, s.deepfocus_pane_width)

        if s.deepfocus_prev_lines:
            new = content._compute_new_lines(s.deepfocus_prev_lines, cur_lines)
            if new:
                s.deepfocus_pending.extend(new)
                s.deepfocus_last_new_ts = time.time()
                if not s.deepfocus_first_new_ts:
                    s.deepfocus_first_new_ts = time.time()

        s.deepfocus_prev_lines = cur_lines

        now = time.time()
        debounce_ok = s.deepfocus_pending and s.deepfocus_last_new_ts and (now - s.deepfocus_last_new_ts >= 3)
        max_delay_ok = s.deepfocus_pending and s.deepfocus_first_new_ts and (now - s.deepfocus_first_new_ts >= 15)

        if debounce_ok or max_delay_ok:
            chunk = "\n".join(s.deepfocus_pending).strip()
            if chunk:
                msg = f"🔬 {state._wid_label(dfw)} (`{dfproj}`):\n```\n{chunk[:3500]}\n```"
                telegram.tg_send(msg, silent=state._is_silent(_CAT_MONITOR))
                config._save_last_msg(dfw, msg)
            s.deepfocus_pending = []
            s.deepfocus_last_new_ts = 0
            s.deepfocus_first_new_ts = 0
    elif s.deepfocus_target_wid:
        s.deepfocus_target_wid = None

    data, s.offset = telegram._poll_updates(s.offset, timeout=0)
    if data is None:
        time.sleep(2)
        return None
    if not data.get("result"):
        time.sleep(0.15)
        return None

    for chat_msg in _merge_album_photos(telegram._extract_chat_messages(data)):
        callback = chat_msg.get("callback")
        if callback:
            cb_data = callback.get("data", "")

            # Handle pending file Skip / Cancel buttons
            if cb_data in ("file_skip", "file_cancel"):
                telegram._answer_callback_query(callback["id"])
                if callback.get("message_id"):
                    telegram._remove_inline_keyboard(callback["message_id"])
                if s.pending_file and cb_data == "file_skip":
                    instruction = _build_file_instruction(s.pending_file["files"])
                    s.pending_file = None
                    _, s.sessions, s.last_win_idx = commands._handle_command(
                        instruction, s.sessions, s.last_win_idx)
                elif s.pending_file and cb_data == "file_cancel":
                    s.pending_file = None
                    telegram.tg_send("🗑 File discarded.",
                                     silent=state._is_silent(_CAT_CONFIRM))
                continue

            s.sessions, s.last_win_idx, cb_action = commands._handle_callback(
                callback, s.sessions, s.last_win_idx)
            if cb_action == "quit":
                return "quit"
            if s.quit_pending and cb_data.startswith("quit_"):
                s.quit_pending = False
            continue

        text = chat_msg["text"]
        photo_id = chat_msg.get("photo")

        # Reply-to routing: use wid from the replied-to message
        reply_wid = chat_msg.get("reply_wid")
        if reply_wid and reply_wid in s.sessions:
            s.last_win_idx = reply_wid

        # Photo received — download and route to Claude
        photo_ids = chat_msg.get("photos") or ([photo_id] if photo_id else [])
        if photo_ids:
            ts = f"{time.time():.6f}"
            paths: list[str] = []
            for i, fid in enumerate(photo_ids):
                suffix = f"_{i}" if len(photo_ids) > 1 else ""
                dest = f"/tmp/tg_photo_{ts}{suffix}.jpg"
                path = telegram._download_tg_file(fid, dest)
                if path:
                    paths.append(path)
            if paths:
                caption = text
                if not caption:
                    # No caption — accumulate and prompt for instructions
                    new_files = [{"type": "photo", "path": p} for p in paths]
                    if s.pending_file:
                        if s.pending_file.get("prompt_msg_id"):
                            telegram._remove_inline_keyboard(s.pending_file["prompt_msg_id"])
                        s.pending_file["files"].extend(new_files)
                    else:
                        s.pending_file = {"files": new_files}
                    msg, kb = _build_pending_prompt(s.pending_file["files"])
                    s.pending_file["prompt_msg_id"] = telegram.tg_send(
                        msg, reply_markup=kb,
                        silent=state._is_silent(_CAT_CONFIRM))
                    continue
                target_wid = None
                remaining_text = caption
                m = re.match(r"^w(\d+[a-z]?)\s*(.*)", caption, re.DOTALL) if caption else None
                if m and f"w{m.group(1)}" in s.sessions:
                    target_wid = f"w{m.group(1)}"
                    remaining_text = m.group(2).strip()
                elif len(s.sessions) == 1:
                    target_wid = next(iter(s.sessions))
                elif s.last_win_idx and s.last_win_idx in s.sessions:
                    target_wid = s.last_win_idx

                if target_wid:
                    pane, project = s.sessions[target_wid]
                    if len(paths) == 1:
                        instruction = f"Read {paths[0]}"
                    else:
                        instruction = "Read these images: " + ", ".join(paths)
                    if remaining_text:
                        instruction += f" — {remaining_text}"

                    paths_display = "`, `".join(paths)

                    # Busy check — queue if session is working
                    if state._is_busy(target_wid):
                        state._save_queued_msg(target_wid, instruction)
                        telegram.tg_send(f"💾 Photo saved for `{target_wid}` (busy):\n`{paths_display}`",
                                         silent=state._is_silent(_CAT_CONFIRM))
                        s.last_win_idx = target_wid
                        continue

                    is_idle, typed_text = routing._pane_idle_state(pane)
                    if not is_idle:
                        state._save_queued_msg(target_wid, instruction)
                        telegram.tg_send(f"💾 Photo saved for `{target_wid}` (busy):\n`{paths_display}`",
                                         silent=state._is_silent(_CAT_CONFIRM))
                        s.last_win_idx = target_wid
                        continue

                    p = shlex.quote(pane)

                    # Save locally typed text before clearing
                    if typed_text:
                        state._save_queued_msg(target_wid, typed_text)
                        subprocess.run(["bash", "-c", f"tmux send-keys -t {p} Escape"], timeout=5)
                        time.sleep(0.2)

                    # Delay before Enter — Claude Code needs time to
                    # process image path previews (longer for albums)
                    delay = "0.5" if len(paths) > 1 else "0.3"
                    cmd = f"tmux send-keys -t {p} -l {shlex.quote(instruction)} && sleep {delay} && tmux send-keys -t {p} Enter"
                    subprocess.run(["bash", "-c", cmd], timeout=10)
                    state._mark_busy(target_wid)
                    confirm = f"📷 Photo sent to `{target_wid}` (`{project}`):\n`{paths_display}`"
                    telegram.tg_send(confirm, silent=state._is_silent(_CAT_CONFIRM))
                    commands._maybe_activate_smartfocus(target_wid, pane, project, confirm)
                    s.last_win_idx = target_wid
                else:
                    paths_display = "`, `".join(paths)
                    telegram.tg_send(f"📷 Photo saved to `{paths_display}` — no target session.\n{tmux.format_sessions_message(s.sessions)}",
                                     reply_markup=tmux._sessions_keyboard(s.sessions))
            else:
                telegram.tg_send("⚠️ Failed to download photo.", silent=state._is_silent(_CAT_ERROR))
            continue

        # Document received — download and route to Claude
        doc_info = chat_msg.get("document")
        if doc_info:
            ts = f"{time.time():.6f}"
            file_name = doc_info.get("file_name", "")
            ext = os.path.splitext(file_name)[1] if file_name else ".bin"
            if not ext:
                ext = ".bin"
            dest = f"/tmp/tg_doc_{ts}{ext}"
            path = telegram._download_tg_file(doc_info["file_id"], dest)
            if path:
                caption = text
                if not caption:
                    # No caption — accumulate and prompt for instructions
                    display = file_name or os.path.basename(path)
                    new_file = {"type": "document", "path": path, "display": display}
                    if s.pending_file:
                        if s.pending_file.get("prompt_msg_id"):
                            telegram._remove_inline_keyboard(s.pending_file["prompt_msg_id"])
                        s.pending_file["files"].append(new_file)
                    else:
                        s.pending_file = {"files": [new_file]}
                    msg, kb = _build_pending_prompt(s.pending_file["files"])
                    s.pending_file["prompt_msg_id"] = telegram.tg_send(
                        msg, reply_markup=kb,
                        silent=state._is_silent(_CAT_CONFIRM))
                    continue
                target_wid = None
                remaining_text = caption
                m = re.match(r"^w(\d+[a-z]?)\s*(.*)", caption, re.DOTALL) if caption else None
                if m and f"w{m.group(1)}" in s.sessions:
                    target_wid = f"w{m.group(1)}"
                    remaining_text = m.group(2).strip()
                elif len(s.sessions) == 1:
                    target_wid = next(iter(s.sessions))
                elif s.last_win_idx and s.last_win_idx in s.sessions:
                    target_wid = s.last_win_idx

                if target_wid:
                    pane, project = s.sessions[target_wid]
                    instruction = f"Read {path}"
                    if remaining_text:
                        instruction += f" — {remaining_text}"

                    # Busy check — queue if session is working
                    if state._is_busy(target_wid):
                        state._save_queued_msg(target_wid, instruction)
                        telegram.tg_send(f"💾 Document saved for `{target_wid}` (busy):\n`{file_name}`",
                                         silent=state._is_silent(_CAT_CONFIRM))
                        s.last_win_idx = target_wid
                        continue

                    is_idle, typed_text = routing._pane_idle_state(pane)
                    if not is_idle:
                        state._save_queued_msg(target_wid, instruction)
                        telegram.tg_send(f"💾 Document saved for `{target_wid}` (busy):\n`{file_name}`",
                                         silent=state._is_silent(_CAT_CONFIRM))
                        s.last_win_idx = target_wid
                        continue

                    p = shlex.quote(pane)

                    # Save locally typed text before clearing
                    if typed_text:
                        state._save_queued_msg(target_wid, typed_text)
                        subprocess.run(["bash", "-c", f"tmux send-keys -t {p} Escape"], timeout=5)
                        time.sleep(0.2)

                    cmd = f"tmux send-keys -t {p} -l {shlex.quote(instruction)} && sleep 0.3 && tmux send-keys -t {p} Enter"
                    subprocess.run(["bash", "-c", cmd], timeout=10)
                    state._mark_busy(target_wid)
                    confirm = f"📎 Document sent to `{target_wid}` (`{project}`):\n`{file_name}`"
                    telegram.tg_send(confirm, silent=state._is_silent(_CAT_CONFIRM))
                    commands._maybe_activate_smartfocus(target_wid, pane, project, confirm)
                    s.last_win_idx = target_wid
                else:
                    telegram.tg_send(f"📎 Document saved to `{path}` — no target session.\n{tmux.format_sessions_message(s.sessions)}",
                                     reply_markup=tmux._sessions_keyboard(s.sessions))
            else:
                telegram.tg_send("⚠️ Failed to download document.", silent=state._is_silent(_CAT_ERROR))
            continue

        # Handle quit confirmation
        if s.quit_pending:
            s.quit_pending = False
            if text.lower() in ("y", "yes"):
                telegram.tg_send("👋 Bye.")
                return "quit"
            else:
                telegram.tg_send("Cancelled.")
            continue

        text = commands._resolve_alias(text, commands._any_active_prompt())

        # Pending file: user is providing instructions for a previously sent photo/doc
        if s.pending_file and not text.startswith("/"):
            if s.pending_file.get("prompt_msg_id"):
                telegram._remove_inline_keyboard(s.pending_file["prompt_msg_id"])
            wn_m = re.match(r"^(w\d+[a-z]?)\s+(.*)", text, re.DOTALL)
            prefix = ""
            user_text = text.strip()
            if wn_m:
                prefix = wn_m.group(1) + " "
                user_text = wn_m.group(2).strip()
            instruction = _build_file_instruction(s.pending_file["files"], user_text)
            text = f"{prefix}{instruction}"
            s.pending_file = None

        prev_sessions = s.sessions
        action, s.sessions, s.last_win_idx = commands._handle_command(
            text, s.sessions, s.last_win_idx
        )
        if s.sessions is not prev_sessions:
            s.last_scan = time.time()
        if action == "pause":
            s.paused = True
            return "pause_break"
        elif action == "quit_pending":
            s.quit_pending = True
        elif action == "quit":
            return "quit"

    return None


def cmd_listen():
    """Poll Telegram and auto-route messages to Claude sessions by wN prefix."""
    # Acquire exclusive lock to prevent duplicate listeners.
    # fcntl.flock is inherited across os.execv (auto-reload) and auto-released on exit/crash.
    lock_fd = open("/tmp/astra_listener.lock", "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("Another astra listener is already running. Stop it first.")
        sys.exit(1)
    lock_fd.write(str(os.getpid()))
    lock_fd.flush()

    state._clear_signals()
    # Clear stale state — after restart, no in-memory context to handle prompts
    # and stop signals that would clear busy files are lost during reload
    if os.path.isdir(config.SIGNAL_DIR):
        for f in os.listdir(config.SIGNAL_DIR):
            if f.startswith(("_active_prompt_", "_bash_cmd_", "_busy_")):
                try:
                    os.remove(os.path.join(config.SIGNAL_DIR, f))
                except OSError:
                    pass

    sessions = tmux.scan_claude_sessions()
    last_scan = time.time()

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
    statuses = routing._get_session_statuses(sessions)
    startup_viewed = tmux._get_locally_viewed_windows() if state._is_local_suppress_enabled() else set()
    telegram.tg_send(tmux.format_sessions_message(sessions, statuses=statuses,
                                                   locally_viewed=startup_viewed or None),
                     reply_markup=telegram._build_reply_keyboard())
    # Pre-seed interrupted set so we don't send redundant notifications
    # for sessions already shown as 🔴 in the startup message
    interrupted_notified = {idx for idx, st in statuses.items() if st == "interrupted"}
    god_wids = state._god_mode_wids()
    config._log("listen", f"Found {len(sessions)} CLI session(s).")
    config._log("listen", f"God mode: {god_wids or 'off'}")
    config._log("listen", f"Local suppress: {'on' if state._is_local_suppress_enabled() else 'off'}")
    config._log("listen", "Press Ctrl+C to stop")

    s = _ListenerState(
        sessions=sessions,
        last_scan=last_scan,
        offset=offset,
        interrupted_notified=interrupted_notified,
        god_wids=god_wids,
    )
    _init_file_mtimes()

    while True:
        try:
            result = _listen_tick(s)
        except KeyboardInterrupt:
            telegram.tg_send("👋 Bye.")
            break
        if result == "quit":
            return
