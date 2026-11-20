#!/usr/bin/env python3
"""Backfill the review half of index.json (schema 2) for the five MRs open with a review on Nov 20, 2026.

Each review's facts (task, kind, tier, head, time, state, verdict, page, record, draft ids) come from its record in
docs/branches/ and its run; everything else from GitLab, read only (GETs), saved first as a snapshot folder:
the drafts still pending (their body hash as posted), the threads (which draft replies where), the diff at the review
head (file signatures). Published or deleted drafts (!805) carry the text the run posted (its draft source file) so the
app can tell "published" from "rewritten". Writes through `mr_context.py review add`'s functions; one run is idempotent.

Before the review, every schema 1 thread entry and finding in the folder moves to schema 2 through `mr_context.py add`
(its own fields kept; last_note_id is the thread's last note at the entry's made_at, so a reply since leaves it stale;
anchor_status from mr_discussions.py; the draft's promises queued). A plan's `slots` rewrites one slot in a block first
(!782's intent slot becomes a [JISU: ...] slot, which check warns about instead of failing).

    backfill_reviews.py --fetch SNAPSHOT_DIR                    GitLab GETs into SNAPSHOT_DIR (mr, versions, drafts,
                                                                discussions, changes or version diffs per MR)
    backfill_reviews.py --snapshot SNAPSHOT_DIR --ctx CTX_DIR [--prior OLDER_SNAPSHOT_DIR]
                                                                write CTX_DIR/<iid>/index.json and reviews/ from it;
                                                                a draft published or deleted since OLDER was taken keeps
                                                                its body hash and place from OLDER

CTX_DIR is outputs/mr_context (the desk runs it there once, after audit); prove it into a copy first and diff.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import mr_context as MC  # noqa: E402
import mr_discussions as MD  # noqa: E402
import mr_review_index as RI  # noqa: E402

HOST = "gitlab.gc.arraylabs.net"
PROJECT = "product/site3d"
ROOT = Path("/home/ubuntu/src/site3d_project_root")
RUNS = Path("/home/ubuntu/.config/astra/areas/1a1c25b6/runs")

# One review per MR, as its record says (docs/branches/<branch>-review.md, latest round).
PLANS = {
    782: {"task": "T-v0", "kind": "received-reply", "tier": "deep", "head": "f0f483ae90746f2dc83fa8b7b4ca9e8312b08a6c",
          "made_at": "2026-11-20T03:19:00-08:00", "state": "comments-only",
          "verdict": "Cassie is right on all nine comments; six small changes accepted, nothing blocks.",
          "page": RUNS / "T-v0/result/mr782_threads_replies.html", "record": "docs/branches/jisu-mr-tomography-review.md",
          "drafts": [3943, 3944, 3945, 3946, 3947, 3948, 3949, 3950, 3951],
          # the draft keeps Jisu's call on who the tool is for: a slot check warns about, not an unfilled one
          "slots": {"1dbf2d36": ("[intent:", "[JISU: intent: research tool / flight data / call]")}},
    788: {"task": "T-v1", "kind": "received-reply", "tier": "standard", "head": "adcfc4a31ca8a6499a22bcedc1e5cb2ec5d9fd7f",
          "made_at": "2026-11-20T03:35:00-08:00", "state": "ready",
          "verdict": "Ready to merge after you publish the three replies; merge before !797.",
          "page": RUNS / "T-v1/result/mr788_threads_replies.html",
          "record": "docs/branches/jisu-mr-raytrace-batches-review.md", "drafts": [3952, 3953, 3954]},
    789: {"task": "T-v2", "kind": "received-reply", "tier": "standard", "head": "db2ec0675beadfc72ee3c126eba80465aae0ff0f",
          "made_at": "2026-11-20T04:03:00-08:00", "state": "comments-only",
          "verdict": "Cassie is right on all four threads; four replies drafted, nothing blocks the merge.",
          "page": RUNS / "T-v2/result/mr789_threads_replies.html",
          "record": "docs/branches/jisu-mr-sim-noise-sigma0-review.md", "drafts": [3955, 3956, 3957, 3958]},
    808: {"task": "T-uZ", "kind": "new", "tier": "standard", "head": "4cc5d0cbb18e97d8eb10ec13b79e45fae2730a4f",
          "made_at": "2026-11-20T02:56:00-08:00", "state": "request-changes",
          "verdict": "Request changes for finding 1 only: the SIDD reader now crashes on in-house SIDDs.",
          "page": RUNS / "T-u_z/page/index.html",
          "record": "docs/branches/yuwei-20261105_plane_sweep_v3_views-review.md", "drafts": [3939, 3940, 3941, 3942],
          # the record's findings ledger: severity and lines per finding
          "findings": {"finding-1": ("significant", "reconstruction/sar_image_reader.py", 81, 83),
                       "finding-2": ("nit", "reconstruction/tests/test_sar_image_reader.py", 160, 176),
                       "finding-3": ("minor", "reconstruction/sar_reconstruction/v3/views.py", 292, 293),
                       "finding-4": ("duplicate", "reconstruction/sar_reconstruction/v3/views.py", 294, 298)}},
    805: {"task": "T-tO", "kind": "new", "tier": "standard", "head": None,  # (c7ba454b9..., full SHA from versions)
          "head_short": "c7ba454b9", "made_at": "2026-11-19T14:00:00-08:00", "state": "comments-only",
          "verdict": "Sound for the square antennas used today; before approval reconsider the last-leg cone default.",
          "page": RUNS / "T-t_o/mr805_review.html", "record": "docs/branches/geoff-rect-antenna-dims-review.md",
          "drafts": [3915, 3916, 3917, 3918, 3919],
          # published or deleted since: (id, source file in the run, reply-to discussion prefix, file, line, title)
          "gone": [(3915, "sim_gate.md", None, "sar_simulation/phase_history/phase_history.py", 201,
                    "The simulator's beamwidth check runs after the mesh raytrace"),
                   (3916, "bp_gate.md", None, "image_formation/backprojection/backprojection.py", 853,
                    "No test covers the backprojection default-beamwidth refusal"),
                   (3917, "bench.md", None, "experimental/jisu/benchmark_az_window.py", 78,
                    "Antenna.square without efficiency raises (pre-existing)"),
                   (3918, "cone_reply.md", "aac10b6", None, None, "Reply: the last-leg cone default is too wide"),
                   (3919, "kwonly_reply.md", "7682368", None, None, "Reply: keyword-only arguments for Antenna")],
          "sources": RUNS / "T-t_o/drafts"},
}


def get(path: str):
    out = subprocess.run(["glab", "api", "--hostname", HOST, path], check=True, capture_output=True, text=True).stdout
    return json.loads(out)


def pages(path: str) -> list:
    out, page = [], 1
    while True:
        chunk = get(f"{path}{'&' if '?' in path else '?'}per_page=100&page={page}")
        out += chunk
        if len(chunk) < 100:
            return out
        page += 1


def fetch(snap: Path) -> None:
    """GitLab GETs only: each planned MR's record, versions, drafts, threads, and its diff at the review head."""
    snap.mkdir(parents=True, exist_ok=True)
    for iid, plan in PLANS.items():
        p = f"projects/{PROJECT.replace('/', '%2F')}/merge_requests/{iid}"
        mr, versions = get(p), get(f"{p}/versions")
        files = {"mr": mr, "versions": versions, "drafts": get(f"{p}/draft_notes"), "discussions": pages(f"{p}/discussions")}
        head = plan["head"] or next(v["head_commit_sha"] for v in versions if v["head_commit_sha"].startswith(plan["head_short"]))
        if mr["sha"] == head:
            files["changes"] = get(f"{p}/changes?access_raw_diffs=true").get("changes") or []
        else:
            v = next(v for v in versions if v["head_commit_sha"] == head)
            files["changes"] = get(f"{p}/versions/{v['id']}").get("diffs") or []
        for k, v in files.items():
            (snap / f"{iid}.{k}.json").write_text(json.dumps(v))
        print(f"!{iid}: head now {mr['sha'][:9]}, review head {head[:9]}, {len(files['drafts'])} drafts pending")


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def drafts_of(iid: int, plan: dict, snap: Path, idx: dict, prior: Path | None = None) -> list[dict]:
    """The review's drafts: pending ones from GitLab (body hash, where), or from the prior snapshot when published or
    deleted since it; gone ones from the plan (posted text)."""
    live = {d["id"]: d for d in json.loads((snap / f"{iid}.drafts.json").read_text())}
    if prior and (prior / f"{iid}.drafts.json").exists():
        live = {**{d["id"]: d for d in json.loads((prior / f"{iid}.drafts.json").read_text())}, **live}
    disc = json.loads((snap / f"{iid}.discussions.json").read_text())
    titles = {e["discussion_id"]: e["title"] for e in idx.get("entries", [])}
    by_note = {f.get("draft_note_id"): f for f in idx.get("findings", [])}
    out = []
    for did in plan["drafts"]:
        d = live.get(did)
        if d is None:
            gone = next((g for g in plan.get("gone", []) if g[0] == did), None)
            if gone is None:
                raise SystemExit(f"!{iid} draft {did}: not on GitLab and not planned as gone (pass --prior)")
            _, src, reply, path, line, title = gone
            full = next((x["id"] for x in disc if reply and x["id"].startswith(reply)), None)
            out.append({"id": did, "kind": "reply" if reply else "finding", "finding": None, "discussion_id": full,
                        "path": path, "line_from": line if path else None, "line": line, "title": title,
                        "posted_sha256": None, "posted_text": (plan["sources"] / src).read_text()})
            continue
        pos = d.get("position") or {}
        start = ((pos.get("line_range") or {}).get("start") or {}).get("new_line")
        f = by_note.get(did)
        reply = d.get("discussion_id")
        title = titles.get(reply) or (f or {}).get("title")
        if not title:
            raise SystemExit(f"!{iid} draft {did}: no title in index.json (entry or finding)")
        out.append({"id": did, "kind": "reply" if reply else "finding", "finding": (f or {}).get("finding"),
                    "discussion_id": reply, "path": pos.get("new_path"),
                    "line_from": start if start is not None else pos.get("new_line"), "line": pos.get("new_line"),
                    "title": title, "posted_sha256": sha(d.get("note") or "")})
    return out


def rewrite_slots(d: Path, plan: dict) -> None:
    """Each planned slot rewrite: the one `[start...]` slot in the block of the entry with this id prefix."""
    for prefix, (start, new) in plan.get("slots", {}).items():
        (blk,) = [p for p in d.glob(f"{prefix}*.html")]
        txt = blk.read_text()
        if new in txt:
            continue
        hits = [i for i in range(len(txt)) if txt.startswith(start, i)]
        if len(hits) != 1:
            raise SystemExit(f"{blk.name}: expected one slot starting {start!r}, found {len(hits)}")
        end = txt.index("]", hits[0]) + 1
        blk.write_text(txt[: hits[0]] + new + txt[end:])


def last_note_at(t: dict, made_at: str) -> int:
    """The thread's last note when an entry was written at `made_at` (its root note if none is older)."""
    older = [n["id"] for n in t["notes"] if MD._time(n["created_at"]) <= MD._time(made_at)]
    return max(older) if older else t["notes"][0]["id"]


def migrate(iid: int, snap: Path, d: Path) -> int:
    """Move each schema 1 entry and finding in d/index.json to schema 2 with `mr_context.py add`; the count moved."""
    p = d / "index.json"
    if not p.exists():
        return 0
    idx = json.loads(p.read_text())
    versions = json.loads((snap / f"{iid}.versions.json").read_text())
    threads = {t["id"]: t for t in MD.build_threads(json.loads((snap / f"{iid}.discussions.json").read_text()), versions)}
    n = 0
    for e in list(idx.get("entries", [])) + list(idx.get("findings", [])):
        if "last_note_id" in e or "evidence" in e:
            continue   # (already schema 2)
        args = ["add", str(d), "--head", e["head_sha"], "--tier", e["tier"], "--title", e["title"],
                "--cold-read", e["cold_read"], "--cold-read-note", e["cold_read_note"], "--requested-by", e["requested_by"],
                "--project", idx["project"], "--iid", str(iid), "--made-at", e["made_at"]]
        args += ["--deep", e["deep_page"]] if e.get("deep_page") else []
        if "discussion_id" in e:
            t = threads.get(e["discussion_id"])
            if t is None or t["anchor_status"] not in MC.ANCHOR_STATUSES:
                why = "not in GitLab's threads" if t is None else f"anchor {t['anchor_status']}"
                raise SystemExit(f"!{iid} entry {e['discussion_id'][:8]}: {why}")
            args += ["--discussion", e["discussion_id"], "--last-note", str(last_note_at(t, e["made_at"])),
                     "--anchor-status", t["anchor_status"]]
        else:
            args += ["--finding", e["finding"]]
            args += ["--draft-note", str(e["draft_note_id"])] if e.get("draft_note_id") is not None else []
        r = subprocess.run([sys.executable, str(Path(MC.__file__)), *args], capture_output=True, text=True, check=False)
        if r.returncode:
            raise SystemExit(f"!{iid} mr_context.py add: {r.stdout}{r.stderr}")
        n += 1
    return n


def backfill(snap: Path, ctx: Path, only: list[int] | None = None, prior: Path | None = None) -> list[str]:
    """Write each planned MR's review into ctx/<iid>/index.json (and its page into reviews/); lines said."""
    said = []
    for iid, plan in PLANS.items():
        if only and iid not in only:
            continue
        d = ctx / str(iid)
        d.mkdir(parents=True, exist_ok=True)
        rewrite_slots(d, plan)
        moved = migrate(iid, snap, d)
        p = d / "index.json"
        idx = json.loads(p.read_text()) if p.exists() else {"schema": RI.SCHEMA, "project": PROJECT, "mr_iid": iid,
                                                             "entries": []}
        versions = json.loads((snap / f"{iid}.versions.json").read_text())
        head = plan["head"] or next(v["head_commit_sha"] for v in versions if v["head_commit_sha"].startswith(plan["head_short"]))
        for f in idx.get("findings", []):
            loc = plan.get("findings", {}).get(f["finding"])
            if loc:
                f.update(dict(zip(("severity", "path", "line_from", "line"), loc)))
        drafts = drafts_of(iid, plan, snap, idx, prior)
        changes = json.loads((snap / f"{iid}.changes.json").read_text())
        RI.add_review(d, idx, task=plan["task"], kind=plan["kind"], tier=plan["tier"], head=head,
                      verdict=plan["verdict"], state=plan["state"], page=plan["page"], record=plan["record"],
                      drafts=drafts, file_sigs=RI.sigs_of(changes), made_at=plan["made_at"])
        probs = RI.problems(d, idx, plan["task"])
        if probs:
            raise SystemExit(f"!{iid}: " + "; ".join(probs))
        RI.save_atomic(p, idx)
        probs = MC.audit(d, review=plan["task"])[0]
        if probs:
            raise SystemExit(f"!{iid}: check fails: " + "; ".join(probs))
        now = json.loads((snap / f"{iid}.mr.json").read_text())["sha"]
        on_gitlab = {x["id"] for x in json.loads((snap / f"{iid}.drafts.json").read_text())}
        pending = sum(x["id"] in on_gitlab for x in drafts)
        said.append(f"!{iid}: review {plan['task']} at {head[:9]} (MR now {now[:9]}{', moved' if now != head else ''}), "
                    f"{len(drafts)} drafts ({pending} pending on GitLab), page {idx['reviews'][-1]['page']}"
                    + (f"; {moved} entries moved to schema 2" if moved else ""))
    return said


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", help="GitLab GETs into this snapshot folder, then stop")
    ap.add_argument("--snapshot", help="the snapshot folder to backfill from")
    ap.add_argument("--ctx", help="the mr_context folder to write (outputs/mr_context, or a copy of it)")
    ap.add_argument("--only", type=int, nargs="*", help="these MR iids only")
    ap.add_argument("--prior", help="an older snapshot: drafts published or deleted since keep their hash from it")
    a = ap.parse_args()
    if a.fetch:
        fetch(Path(a.fetch))
        return 0
    if not (a.snapshot and a.ctx):
        ap.error("--snapshot and --ctx, or --fetch")
    for line in backfill(Path(a.snapshot), Path(a.ctx), a.only, Path(a.prior) if a.prior else None):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
