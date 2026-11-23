#!/usr/bin/env python3
"""List MR discussion threads with their code anchors and resolved state.

Usage (run from the worktree so blobs read from local git; elsewhere `projects/:id` is product/site3d):
    mr_discussions.py [IID] [--all] [--json] [--repo PATH]

Default: open threads only (unresolved diff threads plus non-resolvable general comments),
one line each: state, short discussion id, anchor, author, first line of the root note,
reply count. --all includes resolved threads. --json dumps the structured list for scripts.

--json keeps the thread as the reviewer saw it:
    position       the root note's GitLab position verbatim (base/start/head SHA, old and new path
                   and line, line_range); side: new (added line), old (removed line), context, or null
    notes          every non-system note: id, author, created_at, updated_at, body, resolved;
                   last_note_id (an explanation is stale once a newer note exists, at any head)
    posted_head    the MR head when the root note was created (from the versions API)
    anchor_status  as-posted; inferred when GitLab moved the position to a version made after the note
                   (the API keeps no original position); general; unknown (no versions)
    inferred_line  for inferred: the anchored line number read at the position head and at the posted
                   head, the backticked names of the comment each holds, and which reading matches
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from glab_common import paged, raw, resolve_mr  # noqa: E402

POSITION_KEYS = ("base_sha", "start_sha", "head_sha", "old_path", "new_path", "position_type",
                 "old_line", "new_line", "line_range")
_REPO: str | None = None


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


def side(pos: dict | None) -> str | None:
    """new: an added line; old: a removed line; context: an unchanged line; None: no position."""
    if not pos:
        return None
    new, old = pos.get("new_line"), pos.get("old_line")
    if new and old:
        return "context"
    if new:
        return "new"
    if old:
        return "old"
    return None


def _time(s: str) -> dt.datetime:
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def version_at(created_at: str, versions: list[dict]) -> dict | None:
    """The MR version that was current when a note was created (the newest made at or before it)."""
    t = _time(created_at)
    older = [v for v in versions if _time(v["created_at"]) <= t]
    return max(older, key=lambda v: _time(v["created_at"])) if older else None


def anchor_status(pos: dict | None, created_at: str, versions: list[dict]) -> str:
    """as-posted, inferred (moved by GitLab to a later version), general or unknown."""
    if not pos:
        return "general"
    if not versions:
        return "unknown"
    made = {v["head_commit_sha"]: _time(v["created_at"]) for v in versions}
    head_made = made.get(pos.get("head_sha"))
    if head_made is None:
        return "unknown"
    # A comment on an older version's diff keeps that older head: still as posted.
    return "as-posted" if head_made <= _time(created_at) else "inferred"


def backticked(body: str) -> list[str]:
    """Code names the comment quotes in backticks (call parentheses dropped)."""
    out = []
    for m in re.finditer(r"`+([^`]+?)`+", body or ""):
        name = re.split(r"[\s(\[]", m.group(1).strip(), maxsplit=1)[0]
        if name and name not in out:
            out.append(name)
    return out


def read_blob(sha: str, path: str) -> str | None:
    """A file at a commit: local git first (the worktree's repo), else the GitLab files API (GET)."""
    r = subprocess.run(["git", *(["-C", _REPO] if _REPO else []), "show", f"{sha}:{path}"],
                       capture_output=True, text=True)
    if r.returncode == 0:
        return r.stdout
    return raw(f"projects/:id/repository/files/{path.replace('/', '%2F')}/raw?ref={sha}")


def _line(text: str | None, n: int | None) -> str | None:
    if text is None or not n:
        return None
    lines = text.splitlines()
    return lines[n - 1] if 0 < n <= len(lines) else None


def inferred_line(pos: dict, posted: dict | None, body: str, read) -> dict:
    """Read the anchored line number at the position head and at the posted head."""
    old = side(pos) == "old"
    n = pos.get("old_line") if old else pos.get("new_line")
    path = pos.get("old_path") if old else pos.get("new_path")
    names = backticked(body)
    here_sha = pos.get("base_sha") if old else pos.get("head_sha")
    then_sha = (posted or {}).get("base_commit_sha" if old else "head_commit_sha")
    out = {"line": n, "path": path}
    for key, sha in (("at_position", here_sha), ("at_posted", then_sha)):
        txt = _line(read(sha, path), n) if (read and sha) else None
        out[key] = {"sha": sha, "text": txt, "names": [x for x in names if txt and x in txt]}
    a, b = out["at_position"], out["at_posted"]
    if a["text"] is None or b["text"] is None:
        out["reading"] = "unknown"
    elif a["text"] == b["text"]:
        out["reading"] = "same"
    elif b["names"] and not a["names"]:
        out["reading"] = "posted"
    elif a["names"] and not b["names"]:
        out["reading"] = "position"
    else:
        out["reading"] = "unknown"
    return out


def build_threads(discussions: list[dict], versions: list[dict], read_blob=None) -> list[dict]:
    """The structured thread list from the discussions and versions API responses.

    Args:
        discussions: `merge_requests/:iid/discussions`, every page.
        versions: `merge_requests/:iid/versions`.
        read_blob: `(sha, path) -> text or None`, for the inferred line; None skips blob reads.

    Returns:
        One dict per thread with at least one non-system note (keys in the module docstring).
    """
    threads = []
    for d in discussions:
        notes = [n for n in d["notes"] if not n.get("system")]
        if not notes:
            continue
        root = notes[0]
        resolvable = bool(root.get("resolvable"))
        resolved = bool(root.get("resolved")) if resolvable else False
        pos = root.get("position")
        posted = version_at(root["created_at"], versions) if versions else None
        t = {
            "id": d["id"],
            "state": "resolved" if resolved else ("open" if resolvable else "general"),
            "anchor": anchor(pos),
            "author": root["author"]["username"],
            "body": root["body"],
            "replies": len(notes) - 1,
            "last_author": notes[-1]["author"]["username"],
            "updated_at": notes[-1]["updated_at"],
            "position": {k: pos.get(k) for k in POSITION_KEYS} if pos else None,
            "side": side(pos),
            "posted_head": posted["head_commit_sha"] if posted else None,
            "anchor_status": anchor_status(pos, root["created_at"], versions),
            "notes": [{"id": n["id"], "author": n["author"]["username"], "created_at": n["created_at"],
                       "updated_at": n["updated_at"], "body": n["body"], "resolved": bool(n.get("resolved"))}
                      for n in notes],
            "last_note_id": max(n["id"] for n in notes),
        }
        if t["anchor_status"] == "inferred":
            t["inferred_line"] = inferred_line(pos, posted, root["body"], read_blob)
        threads.append(t)
    return threads


def print_threads(mr: dict, threads: list[dict], shown: list[dict]) -> None:
    print(f"MR !{mr['iid']} {mr['title'][:70]}  threads: {len(threads)} total, {len(shown)} shown")
    for t in shown:
        first = t["body"].strip().splitlines()[0][:100] if t["body"].strip() else ""
        where = t["anchor"] + (" (inferred)" if t["anchor_status"] == "inferred" else "")
        print(f"[{t['state']:8}] {t['id'][:8]} {where:45} {t['author']:14} {first}  "
              f"(+{t['replies']} replies, last {t['last_author']})")


def main() -> None:
    global _REPO
    ap = argparse.ArgumentParser()
    ap.add_argument("iid", nargs="?")
    ap.add_argument("--all", action="store_true", help="include resolved threads")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--repo", default=None, help="git repo for blob reads (default: the cwd)")
    a = ap.parse_args()
    _REPO = a.repo
    mr = resolve_mr(a.iid)
    iid = mr["iid"]
    versions = paged(f"projects/:id/merge_requests/{iid}/versions")
    discussions = paged(f"projects/:id/merge_requests/{iid}/discussions")
    threads = build_threads(discussions, versions, read_blob=read_blob)
    shown = threads if a.all else [t for t in threads if t["state"] != "resolved"]
    if a.json:
        json.dump(shown, sys.stdout, indent=1)
        return
    print_threads(mr, threads, shown)


if __name__ == "__main__":
    main()
