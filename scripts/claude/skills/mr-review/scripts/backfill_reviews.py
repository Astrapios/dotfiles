#!/usr/bin/env python3
"""Backfill the review half of index.json (schema 2) for the five MRs open with a review on Nov 20, 2026.

Each review's facts (task, kind, tier, head, time, state, verdict, page, record, draft ids) come from its record in
docs/branches/ and its run; everything else from GitLab, read only (GETs), saved first as a snapshot folder:
the drafts still pending (their body hash as posted), the threads (which draft replies where), the diff at the review
head (file signatures). Published or deleted drafts (!805) carry the text the run posted (its draft source file) so the
app can tell "published" from "rewritten". Writes through `mr_context.py review add`'s functions; one run is idempotent.

    backfill_reviews.py --fetch SNAPSHOT_DIR                    GitLab GETs into SNAPSHOT_DIR (mr, versions, drafts,
                                                                discussions, changes or version diffs per MR)
    backfill_reviews.py --snapshot SNAPSHOT_DIR --ctx CTX_DIR   write CTX_DIR/<iid>/index.json and reviews/ from it

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
          "drafts": [3943, 3944, 3945, 3946, 3947, 3948, 3949, 3950, 3951]},
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


def drafts_of(iid: int, plan: dict, snap: Path, idx: dict) -> list[dict]:
    """The review's drafts: pending ones from GitLab (body hash, where), gone ones from the plan (posted text)."""
    live = {d["id"]: d for d in json.loads((snap / f"{iid}.drafts.json").read_text())}
    disc = json.loads((snap / f"{iid}.discussions.json").read_text())
    titles = {e["discussion_id"]: e["title"] for e in idx.get("entries", [])}
    by_note = {f.get("draft_note_id"): f for f in idx.get("findings", [])}
    out = []
    for did in plan["drafts"]:
        d = live.get(did)
        if d is None:
            gone = next(g for g in plan.get("gone", []) if g[0] == did)
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


def backfill(snap: Path, ctx: Path, only: list[int] | None = None) -> list[str]:
    """Write each planned MR's review into ctx/<iid>/index.json (and its page into reviews/); lines said."""
    said = []
    for iid, plan in PLANS.items():
        if only and iid not in only:
            continue
        d = ctx / str(iid)
        d.mkdir(parents=True, exist_ok=True)
        p = d / "index.json"
        idx = json.loads(p.read_text()) if p.exists() else {"schema": RI.SCHEMA, "project": PROJECT, "mr_iid": iid,
                                                             "entries": []}
        versions = json.loads((snap / f"{iid}.versions.json").read_text())
        head = plan["head"] or next(v["head_commit_sha"] for v in versions if v["head_commit_sha"].startswith(plan["head_short"]))
        for f in idx.get("findings", []):
            loc = plan.get("findings", {}).get(f["finding"])
            if loc:
                f.update(dict(zip(("severity", "path", "line_from", "line"), loc)))
        drafts = drafts_of(iid, plan, snap, idx)
        changes = json.loads((snap / f"{iid}.changes.json").read_text())
        RI.add_review(d, idx, task=plan["task"], kind=plan["kind"], tier=plan["tier"], head=head,
                      verdict=plan["verdict"], state=plan["state"], page=plan["page"], record=plan["record"],
                      drafts=drafts, file_sigs=RI.sigs_of(changes), made_at=plan["made_at"])
        probs = RI.problems(d, idx, plan["task"])
        if probs:
            raise SystemExit(f"!{iid}: " + "; ".join(probs))
        RI.save_atomic(p, idx)
        now = json.loads((snap / f"{iid}.mr.json").read_text())["sha"]
        pending = sum(x["posted_sha256"] is not None for x in drafts)
        said.append(f"!{iid}: review {plan['task']} at {head[:9]} (MR now {now[:9]}{', moved' if now != head else ''}), "
                    f"{len(drafts)} drafts ({pending} pending on GitLab), page {idx['reviews'][-1]['page']}")
    return said


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--fetch", help="GitLab GETs into this snapshot folder, then stop")
    ap.add_argument("--snapshot", help="the snapshot folder to backfill from")
    ap.add_argument("--ctx", help="the mr_context folder to write (outputs/mr_context, or a copy of it)")
    ap.add_argument("--only", type=int, nargs="*", help="these MR iids only")
    a = ap.parse_args()
    if a.fetch:
        fetch(Path(a.fetch))
        return 0
    if not (a.snapshot and a.ctx):
        ap.error("--snapshot and --ctx, or --fetch")
    for line in backfill(Path(a.snapshot), Path(a.ctx), a.only):
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
