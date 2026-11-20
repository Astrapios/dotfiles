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
                                     "head_sha": HEAD}]}))
    w = lambda iid, k, v: (snap / f"{iid}.{k}.json").write_text(json.dumps(v))  # noqa: E731
    # !808: one pending finding draft at the head; !805: moved since, its reply draft deleted or published
    w(808, "mr", {"sha": HEAD})
    w(808, "versions", [{"id": 1, "head_commit_sha": HEAD}])
    w(808, "drafts", [{"id": 3939, "note": "Body as posted", "discussion_id": None,
                       "position": {"new_path": "r.py", "new_line": 83, "line_range": {"start": {"new_line": 81}}}}])
    w(808, "discussions", [])
    w(808, "changes", [{"new_path": "r.py", "diff": "@@ -1 +1 @@\n-a\n+b\n"}])
    w(805, "mr", {"sha": NOW})
    w(805, "versions", [{"id": 2, "head_commit_sha": NOW}, {"id": 1, "head_commit_sha": HEAD}])
    w(805, "drafts", [])
    w(805, "discussions", [{"id": DISC, "notes": []}])
    w(805, "changes", [{"new_path": "m.py", "diff": "@@ x"}])
    plans = {
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
