#!/usr/bin/env python3
"""Memory budget guard (PreToolUse on Write and Edit): refuses growing a memory index past its budget.

A project's memory/MEMORY.md is loaded into every session. A Write or Edit that leaves it over MEMORY_BUDGET lines and
longer than it was is refused (exit 2, the reason on stderr goes back to Claude); one that shortens it, keeps its
length or stays within the budget passes. So an index already over budget can still be trimmed, and a new line goes
in only after an old one is retired or merged. The context advisor's budget line (context_advisor.py) warns on every
prompt; this hook is the refusal behind it.

Cost: only a path ending in memory/MEMORY.md is read (one read); any other file passes without touching the disk.
Any error passes: a broken guard must never block an edit.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

MEMORY_BUDGET = 60   # same budget as context_advisor.MEMORY_BUDGET


def lines(text: str) -> int:
    return len(text.splitlines())


def refusal(event: dict) -> str | None:
    """The reason to refuse this Write or Edit, or None when it passes."""
    tool = event.get("tool_name")
    args = event.get("tool_input") or {}
    path = Path(args.get("file_path") or "")
    if tool not in ("Write", "Edit") or path.name != "MEMORY.md" or path.parent.name != "memory":
        return None
    try:
        before = path.read_text()
    except FileNotFoundError:
        before = ""
    if tool == "Write":
        after = args.get("content") or ""
    else:
        old, new = args.get("old_string") or "", args.get("new_string") or ""
        if not old or old not in before:
            return None   # the Edit itself will fail
        after = before.replace(old, new) if args.get("replace_all") else before.replace(old, new, 1)
    n0, n1 = lines(before), lines(after)
    if n1 <= MEMORY_BUDGET or n1 <= n0:
        return None
    return (f"Refused: {path} would go from {n0} to {n1} lines (budget {MEMORY_BUDGET}): "
            "retire or merge index lines first (move detail into the memory file, archive stale entries), "
            "then add; a change that shortens the index or keeps its length passes.")


def main() -> int:
    try:
        reason = refusal(json.load(sys.stdin))
    except Exception:
        return 0
    if reason:
        print(reason, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
