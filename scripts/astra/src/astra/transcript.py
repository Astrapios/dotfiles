"""Stream focus content from Claude's structured session transcript (JSONL).

Claude Code appends one JSON record per event to
``~/.claude/projects/<cwd-slug>/<session-id>.jsonl``. That file is the ground
truth the TUI renders *from* — no spinners, timers, wrapping, bullet animation,
or torn frames. Tailing it by byte offset gives a strictly monotonic,
append-only event stream, so focus becomes repeat-free and omit-free by
construction (the whole pane-diff failure class disappears).

This module is Claude-only; Gemini has no equivalent transcript and keeps the
pane-scraping path. `resolve_transcript` returns None when no transcript is
known, so the caller falls back to pane-diff.
"""
from __future__ import annotations

import json
import os
import re

from astra import state


# Assistant tool_use input → a compact one-line summary, matching the pane's
# "🔧 Name(arg)" style. Keyed by tool name (external Claude names).
def _tool_summary(name: str, tool_input: dict) -> str:
    if not isinstance(tool_input, dict):
        return ""
    for key in ("command", "file_path", "notebook_path", "path", "url",
                "query", "pattern", "description", "prompt"):
        val = tool_input.get(key)
        if val:
            return str(val)
    # Fall back to the first scalar value
    for val in tool_input.values():
        if isinstance(val, (str, int, float)):
            return str(val)
    return ""


def render_record(rec: dict, include_tool_results: bool = False) -> list[str]:
    """Render one transcript record to display lines (may be empty).

    Focus shows Claude's *output*: assistant text and tool calls. Skips
    thinking, the user's own prompts, meta/sidechain records, and (unless
    ``include_tool_results``) tool result bodies. Unknown record types → [].
    """
    if not isinstance(rec, dict):
        return []
    if rec.get("isMeta") or rec.get("isSidechain"):
        return []
    rtype = rec.get("type")
    if rtype == "assistant":
        out: list[str] = []
        content = rec.get("message", {}).get("content")
        if not isinstance(content, list):
            return []
        for block in content:
            if not isinstance(block, dict):
                continue
            bt = block.get("type")
            if bt == "text":
                text = block.get("text", "")
                if text.strip():
                    out.extend(text.rstrip("\n").split("\n"))
            elif bt == "tool_use":
                summary = _tool_summary(block.get("name", ""), block.get("input", {}))
                out.append(f"🔧 {block.get('name', '?')}({summary})")
            # thinking / redacted_thinking → skipped
        return out
    if rtype == "user" and include_tool_results:
        content = rec.get("message", {}).get("content")
        if isinstance(content, list):
            out = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    c = block.get("content")
                    if isinstance(c, str):
                        out.extend(c.rstrip("\n").split("\n"))
                    elif isinstance(c, list):
                        for b in c:
                            if isinstance(b, dict) and b.get("type") == "text":
                                out.extend(b.get("text", "").rstrip("\n").split("\n"))
            return out
    return []


def resolve_transcript(wid: str, sessions: dict | None = None) -> str | None:
    """Return the transcript JSONL path for a wid, or None.

    Uses the path cached from the session's Claude hooks
    (`state._load_transcript_path`). Returns None if unknown or the file is
    gone → caller falls back to pane-diff.

    The hook records the path under the bare window id (``w3`` from
    ``get_window_id``) while focus targets the full wid (``w3a``); try both."""
    candidates = [wid]
    m = re.match(r"(w\d+)", wid or "")
    if m and m.group(1) != wid:
        candidates.append(m.group(1))
    for key in candidates:
        path = state._load_transcript_path(key)
        if path and os.path.isfile(path):
            return path
    return None


class TranscriptTail:
    """Incremental byte-offset tail of one transcript JSONL file.

    ``poll()`` returns the display lines for records appended since the last
    call. Append-only ⇒ each record is rendered exactly once ⇒ no repeats, no
    omits. Handles partial trailing lines and file truncation/rotation."""

    def __init__(self, path: str, include_tool_results: bool = False):
        self.path = path
        self.include_tool_results = include_tool_results
        self.offset = 0
        self._buf = b""

    def seed(self) -> None:
        """Skip existing content — start streaming only records appended after
        now (so activating focus doesn't dump the whole prior transcript)."""
        try:
            self.offset = os.path.getsize(self.path)
        except OSError:
            self.offset = 0
        self._buf = b""

    def poll(self) -> list[str]:
        try:
            size = os.path.getsize(self.path)
        except OSError:
            return []
        if size < self.offset:  # truncated / rotated (e.g. /clear, compaction)
            self.offset = 0
            self._buf = b""
        if size == self.offset and not self._buf:
            return []
        try:
            with open(self.path, "rb") as f:
                f.seek(self.offset)
                chunk = f.read()
        except OSError:
            return []
        self.offset += len(chunk)
        data = self._buf + chunk
        nl = data.rfind(b"\n")
        if nl == -1:
            self._buf = data
            return []
        complete, self._buf = data[:nl + 1], data[nl + 1:]
        lines: list[str] = []
        for raw in complete.decode("utf-8", "replace").splitlines():
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            lines.extend(render_record(rec, self.include_tool_results))
        return lines
