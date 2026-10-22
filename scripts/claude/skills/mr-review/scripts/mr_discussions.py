#!/usr/bin/env python3
"""List MR discussion threads with their code anchors and resolved state.

Usage (run from the worktree so `projects/:id` resolves):
    mr_discussions.py [IID] [--all] [--json]

Default: open threads only (unresolved diff threads plus non-resolvable general comments),
one line each: state, short discussion id, anchor, author, first line of the root note,
reply count. --all includes resolved threads. --json dumps the structured list for scripts.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from glab_common import paged, resolve_mr  # noqa: E402


def anchor(pos: dict | None) -> str:
    if not pos:
        return "(general)"
    path = pos.get("new_path") or pos.get("old_path")
    rng = pos.get("line_range")
    if rng and rng.get("start") and rng.get("end"):
        s = rng["start"].get("new_line") or rng["start"].get("old_line")
        e = rng["end"].get("new_line") or rng["end"].get("old_line")
        return f"{path}:{s}-{e}" if s != e else f"{path}:{s}"
    line = pos.get("new_line") or pos.get("old_line")
    return f"{path}:{line}"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("iid", nargs="?")
    ap.add_argument("--all", action="store_true", help="include resolved threads")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    mr = resolve_mr(a.iid)
    iid = mr["iid"]
    threads = []
    for d in paged(f"projects/:id/merge_requests/{iid}/discussions"):
        notes = [n for n in d["notes"] if not n.get("system")]
        if not notes:
            continue
        root = notes[0]
        resolvable = bool(root.get("resolvable"))
        resolved = bool(root.get("resolved")) if resolvable else False
        threads.append({
            "id": d["id"],
            "state": "resolved" if resolved else ("open" if resolvable else "general"),
            "anchor": anchor(root.get("position")),
            "author": root["author"]["username"],
            "body": root["body"],
            "replies": len(notes) - 1,
            "last_author": notes[-1]["author"]["username"],
            "updated_at": notes[-1]["updated_at"],
        })
    shown = threads if a.all else [t for t in threads if t["state"] != "resolved"]
    if a.json:
        json.dump(shown, sys.stdout, indent=1)
        return
    print(f"MR !{iid} {mr['title'][:70]}  threads: {len(threads)} total, {len(shown)} shown")
    for t in shown:
        first = t["body"].strip().splitlines()[0][:100] if t["body"].strip() else ""
        print(f"[{t['state']:8}] {t['id'][:8]} {t['anchor']:45} {t['author']:14} {first}  (+{t['replies']} replies, last {t['last_author']})")


if __name__ == "__main__":
    main()
