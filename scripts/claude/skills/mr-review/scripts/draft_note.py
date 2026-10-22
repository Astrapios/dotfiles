#!/usr/bin/env python3
"""Create or update a GitLab DRAFT note on an MR, anchored on code, with correct line codes.

The user publishes drafts from the web UI; this script never publishes, never posts a plain
note, never resolves a thread (the global guard hook blocks those anyway).

Usage (run from the worktree that has the MR's base and head commits):
  # new thread anchored on lines 40-44 of a file (range end = position.new_line)
  draft_note.py --path image_formation/x.py --line 40 --line-end 44 --note-file note.md
  # same, with a GitLab suggestion replacing exactly the anchored lines
  draft_note.py --path ... --line 40 --line-end 44 --note-file note.md --suggestion-file fix.py
  # reply in an existing thread (no anchor)
  draft_note.py --reply-to <discussion_id> --note-file reply.md
  # edit an existing draft (position is recomputed and resent; a bare note PUT would wipe it)
  draft_note.py --update <draft_id> --path ... --line 40 --line-end 44 --note-file note.md
  # list / delete drafts
  draft_note.py --list            draft_note.py --delete <draft_id>
Add --mr IID when not on the MR's branch, --dry-run to print the payload only.

Line codes follow GitLab's diff parser: `sha1(new_path)_<old_pos>_<new_pos>`, where for an
added line old_pos is the next unconsumed old line number; unchanged lines carry old_line in
the position; added lines omit it and set type "new".
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from glab_common import api, paged, resolve_mr  # noqa: E402

HUNK = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def line_map(iid: int, path: str) -> dict[int, tuple[int | None, int, str | None]]:
    """new_line -> (old_line or None, old_pos, type) from GitLab's OWN diff of the file.

    Line codes must match GitLab's own diff parser (threads re-traced after a push keep stale
    codes, so existing threads are not an oracle; GitLab rejects a code it cannot find), so the
    diff text comes from GitLab, not from local git. Outside hunks the line
    is unchanged and old = new - offset; after the last hunk the mapping is extended by the
    file length at head (via git show when available).
    """
    # `diffs` returns big files collapsed (empty diff); `changes?access_raw_diffs=true` does not.
    text = None
    for f in api(f"projects/:id/merge_requests/{iid}/changes?access_raw_diffs=true")["changes"]:
        if f.get("new_path") == path:
            text = f.get("diff", "")
            break
    if text is None:
        raise SystemExit(f"{path} is not in the MR diff")
    if not text.strip():
        raise SystemExit(f"GitLab returned an empty (collapsed) diff for {path}")
    head = api(f"projects/:id/merge_requests/{iid}")["diff_refs"]["head_sha"]
    shown = subprocess.run(["git", "show", f"{head}:{path}"], capture_output=True, text=True)
    n_new = shown.stdout.count("\n") + (0 if shown.stdout.endswith("\n") else 1) if shown.returncode == 0 else 10**6
    m: dict[int, tuple[int | None, int, str | None]] = {}
    old = new = 1
    in_hunk = False
    for raw in text.splitlines():
        h = HUNK.match(raw)
        if h:
            ho, hn = int(h.group(1)), int(h.group(3))
            while new < hn:  # unchanged lines between hunks
                m[new] = (old, old, None)
                old += 1
                new += 1
            old, new, in_hunk = ho, hn, True
            continue
        if not in_hunk or raw.startswith("\\"):
            continue
        if raw.startswith("+"):
            m[new] = (None, old, "new")
            new += 1
        elif raw.startswith("-"):
            old += 1
        else:
            m[new] = (old, old, None)
            old += 1
            new += 1
    while new <= n_new:
        m[new] = (old, old, None)
        old += 1
        new += 1
    return m


def position_dict(refs: dict, path: str, start: int, end: int, lmap: dict) -> dict:
    """Nested `position` object for the draft-notes API (sent as JSON, never as -f fields)."""
    sha = hashlib.sha1(path.encode()).hexdigest()
    pos: dict = {
        "position_type": "text",
        "base_sha": refs["base_sha"],
        "head_sha": refs["head_sha"],
        "start_sha": refs["start_sha"],
        "new_path": path,
        "old_path": path,
        "new_line": end,
        "old_line": lmap[end][0],
        "line_range": {},
    }
    for key, ln in (("start", start), ("end", end)):
        old_line, old_pos, typ = lmap[ln]
        pos["line_range"][key] = {
            "line_code": f"{sha}_{old_pos}_{ln}",
            "type": typ,
            "old_line": old_line,
            "new_line": ln,
        }
    return pos


def send_json(path: str, body: dict, method: str) -> dict:
    """glab api with a JSON body: --input + Content-Type header (auth comes from glab)."""
    import tempfile

    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as tmp:
        json.dump(body, tmp)
        name = tmp.name
    return api(path, "--input", name, "-H", "Content-Type: application/json", method=method)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mr")
    ap.add_argument("--path")
    ap.add_argument("--line", type=int)
    ap.add_argument("--line-end", type=int)
    ap.add_argument("--note")
    ap.add_argument("--note-file")
    ap.add_argument("--suggestion-file", help="replacement for exactly the anchored lines; appended as a suggestion block")
    ap.add_argument("--reply-to", help="discussion id to reply in (no anchor)")
    ap.add_argument("--update", help="draft note id to edit (PUT)")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--delete")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()

    mr = resolve_mr(a.mr)
    iid = mr["iid"]
    base = f"projects/:id/merge_requests/{iid}/draft_notes"

    if a.list:
        for d in api(base) or []:
            pos = d.get("position") or {}
            if pos.get("new_path"):
                where = f"{pos['new_path']}:{pos.get('new_line')}"
            elif d.get("discussion_id"):
                where = "reply " + str(d["discussion_id"])[:8]
            else:
                where = "general"
            print(f"{d['id']:6} {where:50} {d['note'].strip().splitlines()[0][:80]}")
        return
    if a.delete:
        if a.dry_run:
            print(f"DELETE {base}/{a.delete}")
            return
        api(f"{base}/{a.delete}", method="DELETE")
        print(f"deleted draft {a.delete}")
        return

    note = a.note if a.note is not None else (Path(a.note_file).read_text() if a.note_file else None)
    if note is None:
        raise SystemExit("--note or --note-file required")
    body: dict = {}
    if a.reply_to:
        body["in_reply_to_discussion_id"] = a.reply_to
    elif a.path and a.line:
        end = a.line_end or a.line
        lmap = line_map(iid, a.path)
        for ln in (a.line, end):
            if ln not in lmap:
                raise SystemExit(f"{a.path}:{ln} is outside the head file ({len(lmap)} lines)")
        if a.suggestion_file:
            sug = Path(a.suggestion_file).read_text().rstrip("\n")
            note = note.rstrip("\n") + f"\n\n```suggestion:-{end - a.line}+0\n{sug}\n```\n"
        body["position"] = position_dict(mr["diff_refs"], a.path, a.line, end, lmap)
    body["note"] = note
    if a.update:
        path, method = f"{base}/{a.update}", "PUT"
    else:
        path, method = base, "POST"
    if a.dry_run:
        print(method, path)
        print(json.dumps(body, indent=1)[:1500])
        return
    res = send_json(path, body, method)
    pos = res.get("position") or {}
    where = f" at {pos.get('new_path')}:{pos.get('new_line')}" if pos.get("new_path") else ""
    if a.path and not pos.get("new_path"):
        raise SystemExit(f"draft {res['id']} was created WITHOUT its anchor; delete it and investigate")
    print(f"{method} ok: draft {res['id']} on !{iid}{where}")


if __name__ == "__main__":
    main()
