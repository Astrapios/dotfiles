"""Signal files, prompts, focus, session names."""
import json
import os
import re
import time

from tg_hook import config


def write_signal(event: str, data: dict, **extra):
    """Write a signal file for the listen loop to process."""
    from tg_hook import tmux  # deferred to avoid circular import

    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    pane = os.environ.get("TMUX_PANE", "")
    wid = tmux.get_window_id() or ""
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
    path = os.path.join(config.SIGNAL_DIR, filename)
    with open(path, "w") as f:
        json.dump(signal, f)


def _clear_signals(include_state: bool = False):
    """Remove signal files. If include_state, also removes _prefixed state files.
    Queued messages (_queued_) and session names (_names) are always preserved."""
    if not os.path.isdir(config.SIGNAL_DIR):
        return
    _persist = ("_queued_", "_names")
    for f in os.listdir(config.SIGNAL_DIR):
        if f.startswith(_persist):
            continue
        if not include_state and f.startswith("_"):
            continue
        try:
            os.remove(os.path.join(config.SIGNAL_DIR, f))
        except OSError:
            pass


def save_active_prompt(wid: str, pane: str, total: int,
                       shortcuts: dict[str, int] | None = None,
                       free_text_at: int | None = None,
                       remaining_qs: list[dict] | None = None,
                       project: str | None = None):
    """Save active prompt state so listen can route replies with arrow keys."""
    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    path = os.path.join(config.SIGNAL_DIR, f"_active_prompt_{wid}.json")
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
    path = os.path.join(config.SIGNAL_DIR, f"_active_prompt_{wid}.json")
    try:
        with open(path) as f:
            state = json.load(f)
        os.remove(path)
        return state
    except (OSError, json.JSONDecodeError):
        return None


def _pane_has_prompt(pane: str) -> bool:
    """Check if a tmux pane still shows a permission/question dialog."""
    from tg_hook import tmux  # deferred to avoid circular import

    try:
        raw = tmux._capture_pane(pane, 10)
        for line in raw.splitlines():
            if re.match(r'^\s*[❯>]?\s*\d+\.\s+', line):
                return True
        return False
    except Exception:
        return False


def _cleanup_stale_prompts():
    """Remove active prompt files whose pane is idle (prompt was answered).

    Uses idle detection (❯ prompt visible) instead of _pane_has_prompt,
    which fails for ExitPlanMode and other non-numbered dialogs.
    """
    from tg_hook import routing  # deferred to avoid circular import

    if not os.path.isdir(config.SIGNAL_DIR):
        return
    for fname in os.listdir(config.SIGNAL_DIR):
        if not fname.startswith("_active_prompt_"):
            continue
        path = os.path.join(config.SIGNAL_DIR, fname)
        try:
            with open(path) as f:
                st = json.load(f)
            pane = st.get("pane", "")
            if pane:
                idle, _ = routing._pane_idle_state(pane)
                if idle:
                    os.remove(path)
        except (OSError, json.JSONDecodeError):
            try:
                os.remove(path)
            except OSError:
                pass


def _save_focus_state(wid: str, pane: str, project: str):
    """Save focus target so listen monitors this pane."""
    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    path = os.path.join(config.SIGNAL_DIR, "_focus.json")
    with open(path, "w") as f:
        json.dump({"wid": wid, "pane": pane, "project": project}, f)


def _load_focus_state() -> dict | None:
    """Load focus state. Returns None if missing."""
    path = os.path.join(config.SIGNAL_DIR, "_focus.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _clear_focus_state():
    """Remove focus state file."""
    path = os.path.join(config.SIGNAL_DIR, "_focus.json")
    try:
        os.remove(path)
    except OSError:
        pass


def _save_deepfocus_state(wid: str, pane: str, project: str):
    """Save deepfocus target so listen streams all output from this pane."""
    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    path = os.path.join(config.SIGNAL_DIR, "_deepfocus.json")
    with open(path, "w") as f:
        json.dump({"wid": wid, "pane": pane, "project": project}, f)


def _load_deepfocus_state() -> dict | None:
    """Load deepfocus state. Returns None if missing."""
    path = os.path.join(config.SIGNAL_DIR, "_deepfocus.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _clear_deepfocus_state():
    """Remove deepfocus state file."""
    path = os.path.join(config.SIGNAL_DIR, "_deepfocus.json")
    try:
        os.remove(path)
    except OSError:
        pass


def _save_smartfocus_state(wid: str, pane: str, project: str):
    """Save smart focus target (auto-activated on message send)."""
    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    path = os.path.join(config.SIGNAL_DIR, "_smartfocus.json")
    with open(path, "w") as f:
        json.dump({"wid": wid, "pane": pane, "project": project}, f)


def _load_smartfocus_state() -> dict | None:
    """Load smart focus state. Returns None if missing."""
    path = os.path.join(config.SIGNAL_DIR, "_smartfocus.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _clear_smartfocus_state():
    """Remove smart focus state file."""
    path = os.path.join(config.SIGNAL_DIR, "_smartfocus.json")
    try:
        os.remove(path)
    except OSError:
        pass


def _is_autofocus_enabled() -> bool:
    """Check if autofocus is enabled (on by default)."""
    return not os.path.exists(os.path.join(config.SIGNAL_DIR, "_autofocus_off.json"))


def _set_autofocus(enabled: bool):
    """Enable or disable autofocus."""
    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    path = os.path.join(config.SIGNAL_DIR, "_autofocus_off.json")
    if enabled:
        try:
            os.remove(path)
        except OSError:
            pass
    else:
        with open(path, "w") as f:
            json.dump({"ts": time.time()}, f)


def _save_session_name(wid: str, name: str):
    """Save a friendly name for a session."""
    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    path = os.path.join(config.SIGNAL_DIR, "_names.json")
    names = _load_session_names()
    names[wid] = name
    with open(path, "w") as f:
        json.dump(names, f)


def _clear_session_name(wid: str):
    """Remove a session's friendly name."""
    path = os.path.join(config.SIGNAL_DIR, "_names.json")
    names = _load_session_names()
    names.pop(wid, None)
    try:
        with open(path, "w") as f:
            json.dump(names, f)
    except OSError:
        pass


def _load_session_names() -> dict[str, str]:
    """Load session names. Returns empty dict on failure."""
    path = os.path.join(config.SIGNAL_DIR, "_names.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _resolve_name(target: str, sessions: dict) -> str | None:
    """Resolve a session name or numeric index to a window index.
    Returns the index string (e.g. '4') or None."""
    if target in sessions:
        return target
    names = _load_session_names()
    for idx, name in names.items():
        if name.lower() == target.lower() and idx in sessions:
            return idx
    return None


def _wid_label(idx: str) -> str:
    """Format a window index with its name for display.
    Returns '`w4 [auth]`' or just '`w4`' if unnamed."""
    names = _load_session_names()
    name = names.get(idx, "")
    if name:
        return f"`w{idx} [{name}]`"
    return f"`w{idx}`"


def _save_queued_msg(wid: str, text: str):
    """Append a message to the queue for a session."""
    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    path = os.path.join(config.SIGNAL_DIR, f"_queued_{wid}.json")
    msgs = _load_queued_msgs(wid)
    msgs.append({"text": text, "ts": time.time()})
    with open(path, "w") as f:
        json.dump(msgs, f)


def _load_queued_msgs(wid: str) -> list[dict]:
    """Load queued messages (non-destructive)."""
    path = os.path.join(config.SIGNAL_DIR, f"_queued_{wid}.json")
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return []


def _pop_queued_msgs(wid: str) -> list[dict]:
    """Load and delete queued messages."""
    path = os.path.join(config.SIGNAL_DIR, f"_queued_{wid}.json")
    try:
        with open(path) as f:
            msgs = json.load(f)
        os.remove(path)
        return msgs
    except (OSError, json.JSONDecodeError):
        return []


def _save_prompt_text(wid: str, text: str):
    """Save locally typed prompt text that was cleared."""
    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    path = os.path.join(config.SIGNAL_DIR, f"_saved_prompt_{wid}.json")
    with open(path, "w") as f:
        json.dump({"text": text}, f)


def _pop_prompt_text(wid: str) -> str | None:
    """Load and delete saved prompt text. Returns text or None."""
    path = os.path.join(config.SIGNAL_DIR, f"_saved_prompt_{wid}.json")
    try:
        with open(path) as f:
            data = json.load(f)
        os.remove(path)
        return data.get("text")
    except (OSError, json.JSONDecodeError):
        return None


def _mark_busy(wid: str):
    """Mark a session as busy (message was just sent). Persists to file."""
    os.makedirs(config.SIGNAL_DIR, exist_ok=True)
    path = os.path.join(config.SIGNAL_DIR, f"_busy_{wid}.json")
    with open(path, "w") as f:
        json.dump({"ts": time.time()}, f)


def _is_busy(wid: str) -> bool:
    """Check if a session is marked busy."""
    return os.path.exists(os.path.join(config.SIGNAL_DIR, f"_busy_{wid}.json"))


def _busy_since(wid: str) -> float | None:
    """Return the timestamp when session was marked busy, or None."""
    path = os.path.join(config.SIGNAL_DIR, f"_busy_{wid}.json")
    try:
        with open(path) as f:
            return json.load(f).get("ts")
    except (OSError, json.JSONDecodeError):
        return None


def _clear_busy(wid: str):
    """Clear busy mark for a session (called on stop signal)."""
    path = os.path.join(config.SIGNAL_DIR, f"_busy_{wid}.json")
    try:
        os.remove(path)
    except OSError:
        pass


def _cleanup_stale_busy(active_sessions: dict):
    """Remove busy files for sessions that no longer exist in tmux."""
    if not os.path.isdir(config.SIGNAL_DIR):
        return
    for fname in os.listdir(config.SIGNAL_DIR):
        m = re.match(r'^_busy_w(\d+)\.json$', fname)
        if not m:
            continue
        idx = m.group(1)
        if idx not in active_sessions:
            try:
                os.remove(os.path.join(config.SIGNAL_DIR, fname))
            except OSError:
                pass


def _is_god_mode_for(w_idx: str) -> bool:
    """Check if god mode is enabled for a specific window index."""
    wids = _god_mode_wids()
    return "all" in wids or w_idx in wids


def _god_mode_wids() -> list[str]:
    """Return list of god-mode wids (may contain 'all')."""
    # Check persistent location first, fall back to legacy /tmp path
    for path in (config.GOD_MODE_PATH,
                 os.path.join(config.SIGNAL_DIR, "_god_mode.json")):
        try:
            with open(path) as f:
                wids = json.load(f).get("wids", [])
            # Migrate legacy file to persistent location
            if path != config.GOD_MODE_PATH and wids:
                _set_god_mode(wids[0], True)  # triggers write to persistent path
                for w in wids[1:]:
                    _set_god_mode(w, True)
                try:
                    os.remove(path)
                except OSError:
                    pass
                config._log("god", f"Migrated god mode from {path}: {wids}")
            return wids
        except (OSError, json.JSONDecodeError):
            continue
    return []


def _set_god_mode(w_idx: str, enabled: bool):
    """Enable/disable god mode for a specific wid or 'all'."""
    path = config.GOD_MODE_PATH
    os.makedirs(os.path.dirname(path), exist_ok=True)
    wids = []
    try:
        with open(path) as f:
            wids = json.load(f).get("wids", [])
    except (OSError, json.JSONDecodeError):
        pass
    if enabled:
        if w_idx not in wids:
            wids.append(w_idx)
    else:
        wids = [w for w in wids if w != w_idx]
    if wids:
        with open(path, "w") as f:
            json.dump({"wids": wids}, f)
        config._log("god", f"Set god mode: {wids} -> {path}")
    else:
        try:
            os.remove(path)
        except OSError:
            pass
        config._log("god", f"Cleared god mode (removed {path})")


def _clear_god_mode():
    """Disable god mode entirely."""
    for path in (config.GOD_MODE_PATH,
                 os.path.join(config.SIGNAL_DIR, "_god_mode.json")):
        try:
            os.remove(path)
            config._log("god", f"Cleared god mode file: {path}")
        except OSError:
            pass
