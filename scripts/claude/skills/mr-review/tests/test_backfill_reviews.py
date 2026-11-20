"""Tests for scripts/backfill_reviews.py on a fake GitLab snapshot (no GitLab call)."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
HEAD = "c7ba454b9" + "0" * 31
NOW = "830b35262" + "1" * 31
DISC = "aac10b66" + "2" * 32
D782 = "1dbf2d36" + "3" * 32
SLOT = "[intent: research tool for formation and antenna studies for now / aimed at flight data / let's talk on a call]"
JISU = "[JISU: intent: research tool / flight data / call]"
BLOCK = ('<html><body><img src="data:image/png;base64,AAAA" alt="f"><div class="draft">Thanks. I\'ll fix the docstring. '
         f"{SLOT}</div></body></html>")
COMMON = {"head_sha": HEAD, "tier": "standard", "deep_page": None, "cold_read": "pass", "cold_read_note": "",
          "requested_by": "review", "made_at": "2026-11-20T03:00:00-08:00"}


def note(i, at, who="cassie"):
    return {"id": i, "author": {"username": who}, "created_at": at, "updated_at": at, "body": f"note {i}",
            "system": False, "resolvable": True, "resolved": False}


@pytest.fixture
def bf(monkeypatch):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location("backfill_reviews", SCRIPTS / "backfill_reviews.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def world(tmp_path, bf, monkeypatch):
    snap, ctx, run = tmp_path / "snap", tmp_path / "ctx", tmp_path / "run"
    snap.mkdir()
    run.mkdir()
    (run / "page.html").write_text("<html><body>review</body></html>")
    (run / "cone_reply.md").write_text("The default is too wide.\n")
    (ctx / "808").mkdir(parents=True)
    (ctx / "808" / "finding-1.html").write_text('<img src="data:x">')
    (ctx / "808" / "index.json").write_text(json.dumps({"schema": 1, "project": "product/site3d", "mr_iid": 808,
        "entries": [], "findings": [{"finding": "finding-1", "draft_note_id": 3939, "title": "SIDD reader crashes",
                                     "block": "finding-1.html", **COMMON}]}))
    # !782: one schema 1 thread entry whose draft promises a fix and keeps the intent slot
    (ctx / "782").mkdir()
    (ctx / "782" / f"{D782}.html").write_text(BLOCK)
    (ctx / "782" / "index.json").write_text(json.dumps({"schema": 1, "project": "product/site3d", "mr_iid": 782,
        "entries": [{"discussion_id": D782, "title": "Who the tool is for", "block": f"{D782}.html", **COMMON}]}))
    w = lambda iid, k, v: (snap / f"{iid}.{k}.json").write_text(json.dumps(v))  # noqa: E731
    # !808: one pending finding draft at the head; !805: moved since, its reply draft deleted or published
    w(808, "mr", {"sha": HEAD})
    w(808, "versions", [{"id": 1, "head_commit_sha": HEAD, "created_at": "2026-11-19T20:00:00.000Z"}])
    w(808, "drafts", [{"id": 3939, "note": "Body as posted", "discussion_id": None,
                       "position": {"new_path": "r.py", "new_line": 83, "line_range": {"start": {"new_line": 81}}}}])
    w(808, "discussions", [])
    w(808, "changes", [{"new_path": "r.py", "diff": "@@ -1 +1 @@\n-a\n+b\n"}])
    w(805, "mr", {"sha": NOW})
    w(805, "versions", [{"id": 2, "head_commit_sha": NOW, "created_at": "2026-11-20T18:00:00.000Z"},
                        {"id": 1, "head_commit_sha": HEAD, "created_at": "2026-11-19T20:00:00.000Z"}])
    # !782: Cassie's root note (101) and a reply after the explanation was written (105, 04:00 PST)
    w(782, "mr", {"sha": HEAD})
    w(782, "versions", [{"id": 1, "head_commit_sha": HEAD, "created_at": "2026-11-19T20:00:00.000Z"}])
    w(782, "drafts", [{"id": 3951, "note": "Reply body", "discussion_id": D782, "position": None}])
    w(782, "discussions", [{"id": D782, "notes": [
        {**note(101, "2026-11-20T09:00:00.000Z"), "position": {"head_sha": HEAD, "new_path": "t.py", "new_line": 4}},
        note(105, "2026-11-20T12:00:00.000Z")]}])
    w(782, "changes", [{"new_path": "t.py", "diff": "@@ y"}])
    w(805, "drafts", [])
    w(805, "discussions", [{"id": DISC, "notes": []}])
    w(805, "changes", [{"new_path": "m.py", "diff": "@@ x"}])
    plans = {
        782: {"task": "T-v0", "kind": "received-reply", "tier": "deep", "head": HEAD, "made_at": "2026-11-20T03:19:00-08:00",
              "state": "comments-only", "verdict": "Nothing blocks.", "page": run / "page.html", "record": "t.md",
              "drafts": [3951], "slots": {"1dbf2d36": ("[intent:", JISU)}},
        808: {"task": "T-uZ", "kind": "new", "tier": "standard", "head": HEAD, "made_at": "2026-11-20T02:56:00-08:00",
              "state": "request-changes", "verdict": "Request changes.", "page": run / "page.html", "record": "r.md",
              "drafts": [3939], "findings": {"finding-1": ("significant", "r.py", 81, 83)}},
        805: {"task": "T-tO", "kind": "new", "tier": "standard", "head": None, "head_short": "c7ba454b9",
              "made_at": "2026-11-19T14:00:00-08:00", "state": "comments-only", "verdict": "Sound.",
              "page": run / "page.html", "record": "g.md", "drafts": [3918],
              "gone": [(3918, "cone_reply.md", "aac10b6", None, None, "Reply: the cone default")], "sources": run},
    }
    monkeypatch.setattr(bf, "PLANS", plans)
    return {"snap": snap, "ctx": ctx}


def test_backfill_writes_each_review_with_its_drafts(bf, world):
    said = bf.backfill(world["snap"], world["ctx"])
    said = [x for x in said if not x.startswith("!782")]
    assert said[0].startswith("!808: review T-uZ at c7ba454b9 (MR now c7ba454b9)") and "1 pending" in said[0]
    assert "moved" in said[1] and "0 pending" in said[1]
    idx = json.loads((world["ctx"] / "808" / "index.json").read_text())
    (r,) = idx["reviews"]
    (d,) = r["drafts"]
    assert d == {"id": 3939, "kind": "finding", "finding": "finding-1", "discussion_id": None, "path": "r.py",
                 "line_from": 81, "line": 83, "title": "SIDD reader crashes",
                 "posted_sha256": hashlib.sha256(b"Body as posted").hexdigest()}
    assert idx["findings"][0]["severity"] == "significant" and idx["findings"][0]["line_from"] == 81
    assert r["file_sigs"] == {"r.py": hashlib.sha1(b"@@ -1 +1 @@\n-a\n+b\n").hexdigest()[:16]}
    assert (world["ctx"] / "808" / r["page"]).exists()
    idx5 = json.loads((world["ctx"] / "805" / "index.json").read_text())
    (r5,) = idx5["reviews"]
    assert r5["head_sha"] == HEAD and idx5["mr_iid"] == 805 and idx5["entries"] == []
    (g,) = r5["drafts"]
    assert g["posted_sha256"] is None and g["posted_text"] == "The default is too wide.\n" and g["discussion_id"] == DISC


def test_backfill_is_idempotent(bf, world):
    bf.backfill(world["snap"], world["ctx"])
    first = (world["ctx"] / "808" / "index.json").read_text()
    bf.backfill(world["snap"], world["ctx"])
    assert (world["ctx"] / "808" / "index.json").read_text() == first


def test_a_pending_draft_with_no_title_stops_the_backfill(bf, world):
    idx = json.loads((world["ctx"] / "808" / "index.json").read_text())
    idx["findings"][0]["title"] = ""
    (world["ctx"] / "808" / "index.json").write_text(json.dumps(idx))
    with pytest.raises(SystemExit, match="no title"):
        bf.backfill(world["snap"], world["ctx"], [808])


def test_backfill_moves_entries_to_schema2_and_queues_their_promises(bf, world):
    """Each schema 1 entry goes through `mr_context.py add`: last_note_id is the thread's last note when the entry was
    written (a later reply leaves it stale), anchor_status from mr_discussions, its draft's promises queued."""
    said = bf.backfill(world["snap"], world["ctx"])
    assert any(x.startswith("!782:") and "1 entries moved to schema 2" in x for x in said)
    idx = json.loads((world["ctx"] / "782" / "index.json").read_text())
    (e,) = idx["entries"]
    assert idx["schema"] == 2 and e["last_note_id"] == 101 and e["anchor_status"] == "as-posted"
    assert e["promises"] == [{"text": "I'll fix the docstring.", "status": "queued"}]
    assert e["made_at"] == COMMON["made_at"] and e["evidence"] == [{"mr": 782, "sha": HEAD}]
    f = json.loads((world["ctx"] / "808" / "index.json").read_text())["findings"][0]
    assert f["draft_note_id"] == 3939 and f["evidence"] == [{"mr": 808, "sha": HEAD}]
    assert (f["severity"], f["path"], f["line_from"], f["line"]) == ("significant", "r.py", 81, 83)   # (kept by the backfill)


def test_the_782_intent_slot_becomes_a_jisu_slot_so_check_warns(bf, world):
    bf.backfill(world["snap"], world["ctx"])
    blk = (world["ctx"] / "782" / f"{D782}.html").read_text()
    assert JISU in blk and SLOT not in blk
    mc = sys.modules.get("mr_context") or bf.MC
    probs, warn = mc.audit(world["ctx"] / "782")
    assert probs == [] and any("slot for Jisu" in w for w in warn)


def test_a_draft_published_since_keeps_its_hash_from_a_prior_snapshot(bf, world, tmp_path):
    """!808's drafts were published after the first snapshot: the prior snapshot's body gives posted_sha256."""
    prior = tmp_path / "prior"
    prior.mkdir()
    for f in world["snap"].glob("808.*.json"):
        (prior / f.name).write_text(f.read_text())
    (world["snap"] / "808.drafts.json").write_text("[]")
    with pytest.raises(SystemExit, match="draft 3939"):
        bf.backfill(world["snap"], world["ctx"], [808])
    said = bf.backfill(world["snap"], world["ctx"], [808], prior=prior)
    assert "0 pending" in said[0]
    (d,) = json.loads((world["ctx"] / "808" / "index.json").read_text())["reviews"][0]["drafts"]
    assert d["posted_sha256"] == hashlib.sha256(b"Body as posted").hexdigest() and d["path"] == "r.py" and d["line"] == 83
