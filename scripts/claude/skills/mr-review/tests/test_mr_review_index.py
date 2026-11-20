"""Tests for the review half of index.json schema 2: `mr_context.py review add`, `check --review`, the atomic save
(scripts/mr_review_index.py, wired into scripts/mr_context.py), and `draft_note.py --json`.

Run: pixi run --manifest-path /home/ubuntu/src/site3d_project_root/site3d_mr_review/pyproject.toml -e test \
     python -m pytest scripts/claude/skills/mr-review/tests -q
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
CTX = SCRIPTS / "mr_context.py"
HEAD = "4cc5d0cbb18e97d8eb10ec13b79e45fae2730a4f"
DISC = "ee87777b21cab18568796cffd5e15b1bdef0859c"
BLOCK = '<html><body><p>Words</p><img src="data:image/png;base64,AAAA" alt="fig"></body></html>'


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(SCRIPTS))
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ri():
    return load("mr_review_index")


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(CTX), *args], capture_output=True, text=True)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


@pytest.fixture
def mr(tmp_path):
    """An MR folder with one schema 1 thread entry and a review page in a run folder."""
    d = tmp_path / "mr_context" / "808"
    d.mkdir(parents=True)
    (d / f"{DISC}.html").write_text(BLOCK)
    idx = {"schema": 1, "project": "product/site3d", "mr_iid": 808, "entries": [{
        "discussion_id": DISC, "head_sha": HEAD, "tier": "short", "title": "A thread", "block": f"{DISC}.html",
        "deep_page": None, "cold_read": "pass", "cold_read_note": "", "requested_by": "review",
        "made_at": "2026-11-20T03:00:00-08:00"}]}
    (d / "index.json").write_text(json.dumps(idx))
    run_dir = tmp_path / "runs" / "T-uZ" / "page"
    run_dir.mkdir(parents=True)
    (run_dir / "index.html").write_text('<html><body><h1>Review</h1><img src="fig1.png"><a href="detail.md">d</a>'
                                        '<a href="https://x/y">x</a></body></html>')
    (run_dir / "fig1.png").write_bytes(b"\x89PNG")
    (run_dir / "detail.md").write_text("# detail")
    (run_dir / "unrelated.log").write_text("not linked")
    drafts = [{"id": 3939, "kind": "finding", "finding": "finding-1", "discussion_id": None,
               "path": "reconstruction/sar_image_reader.py", "line_from": 81, "line": 83,
               "title": "Reading a SIDD without the Geometry block now crashes", "posted_sha256": sha("body 1")},
              {"id": 3940, "kind": "reply", "finding": None, "discussion_id": DISC, "path": None, "line": None,
               "title": "Reply on the thread", "posted_sha256": sha("body 2")}]
    (tmp_path / "drafts.json").write_text(json.dumps(drafts))
    changes = [{"new_path": "reconstruction/sar_image_reader.py", "old_path": "reconstruction/sar_image_reader.py",
                "diff": "@@ -1 +1 @@\n-a\n+b\n"}]
    (tmp_path / "changes.json").write_text(json.dumps({"changes": changes}))
    return {"dir": d, "page": run_dir / "index.html", "tmp": tmp_path}


def add(mr, *extra: str) -> subprocess.CompletedProcess:
    t = mr["tmp"]
    return run("review", "add", str(mr["dir"]), "--task", "T-uZ", "--kind", "new", "--tier", "standard",
               "--head", HEAD, "--state", "request-changes",
               "--verdict", "Request changes for finding 1 only: the SIDD reader now crashes on in-house SIDDs.",
               "--page", str(mr["page"]), "--record", "docs/branches/x-review.md", "--drafts", str(t / "drafts.json"),
               "--changes", str(t / "changes.json"), "--made-at", "2026-11-20T02:56:00-08:00", *extra)


def test_review_add_copies_the_page_and_writes_a_schema2_entry(mr):
    p = add(mr)
    assert p.returncode == 0, p.stdout + p.stderr
    idx = json.loads((mr["dir"] / "index.json").read_text())
    assert idx["schema"] == 2
    assert len(idx["entries"]) == 1 and idx["entries"][0]["discussion_id"] == DISC   # (kept as it was)
    (r,) = idx["reviews"]
    folder = f"reviews/{HEAD[:12]}-T-uZ"
    assert r["page"] == f"{folder}/index.html" and r["task"] == "T-uZ" and r["head_sha"] == HEAD
    assert (mr["dir"] / folder / "index.html").read_text().startswith("<html>")
    assert (mr["dir"] / folder / "fig1.png").exists() and (mr["dir"] / folder / "detail.md").exists()
    assert not (mr["dir"] / folder / "unrelated.log").exists()   # (only what the page links)
    assert r["page_bytes"] == (mr["dir"] / folder / "index.html").stat().st_size
    assert [d["id"] for d in r["drafts"]] == [3939, 3940]
    assert r["file_sigs"] == {"reconstruction/sar_image_reader.py": hashlib.sha1(b"@@ -1 +1 @@\n-a\n+b\n").hexdigest()[:16]}
    assert run("check", str(mr["dir"]), "--review", "T-uZ").returncode == 0


def test_review_add_again_replaces_the_same_task_and_head_and_keeps_others_in_time_order(mr):
    assert add(mr).returncode == 0
    assert add(mr, "--made-at", "2026-11-20T02:57:00-08:00").returncode == 0
    p = run("review", "add", str(mr["dir"]), "--task", "T-aa", "--kind", "rereview", "--tier", "quick", "--head", HEAD,
            "--state", "ready", "--verdict", "Ready.", "--page", str(mr["page"]), "--record", "r.md",
            "--drafts", str(mr["tmp"] / "drafts.json"), "--no-sigs", "--made-at", "2026-11-19T01:00:00-08:00")
    assert p.returncode == 0, p.stderr
    idx = json.loads((mr["dir"] / "index.json").read_text())
    assert [r["task"] for r in idx["reviews"]] == ["T-aa", "T-uZ"]
    assert idx["reviews"][1]["made_at"] == "2026-11-20T02:57:00-08:00"


def test_check_review_fails_on_missing_page_script_and_untitled_draft(mr):
    assert add(mr).returncode == 0
    d = mr["dir"]
    p = run("check", str(d), "--review", "T-zz")
    assert p.returncode == 1 and "no review by T-zz" in p.stdout
    page = d / f"reviews/{HEAD[:12]}-T-uZ/index.html"
    page.write_text("<html><script>alert(1)</script></html>")
    p = run("check", str(d), "--review", "T-uZ")
    assert p.returncode == 1 and "page has a script" in p.stdout
    page.unlink()
    p = run("check", str(d), "--review", "T-uZ")
    assert p.returncode == 1 and "page" in p.stdout and "missing" in p.stdout
    add(mr)
    idx = json.loads((d / "index.json").read_text())
    idx["reviews"][0]["drafts"][1]["title"] = ""
    (d / "index.json").write_text(json.dumps(idx))
    p = run("check", str(d), "--review", "T-uZ")
    assert p.returncode == 1 and "draft 3940 has no title" in p.stdout


def test_check_refuses_bad_review_fields(mr, ri):
    assert add(mr).returncode == 0
    idx = json.loads((mr["dir"] / "index.json").read_text())
    r = idx["reviews"][0]
    r.update(kind="nice", state="maybe", head_sha="abc")
    r["drafts"][0]["posted_sha256"] = "zz"
    probs = ri.problems(mr["dir"], idx)
    text = "\n".join(probs)
    assert "kind 'nice'" in text and "state 'maybe'" in text and "head_sha is not 40 hex" in text
    assert "posted_sha256" in text


def test_check_refuses_a_page_outside_the_reviews_folder(mr, ri):
    assert add(mr).returncode == 0
    idx = json.loads((mr["dir"] / "index.json").read_text())
    idx["reviews"][0]["page"] = "../../etc/passwd"
    assert any("page must be under reviews/" in p for p in ri.problems(mr["dir"], idx))


def test_finding_location_keys_are_checked(mr, ri):
    idx = json.loads((mr["dir"] / "index.json").read_text())
    idx["findings"] = [{"finding": "finding-1", "severity": 3, "path": "a.py", "line_from": "81", "line": 83}]
    text = "\n".join(ri.problems(mr["dir"], idx))
    assert "severity has type int" in text and "line_from has type str" in text


def test_schema1_folder_without_reviews_still_checks(mr):
    p = run("check", str(mr["dir"]))
    assert p.returncode == 0, p.stdout


def test_thread_add_keeps_schema2(mr):
    assert add(mr).returncode == 0
    p = run("add", str(mr["dir"]), "--discussion", DISC, "--head", HEAD, "--tier", "short", "--title", "Again",
            "--last-note", "101", "--anchor-status", "general")
    assert p.returncode == 0, p.stderr
    idx = json.loads((mr["dir"] / "index.json").read_text())
    assert idx["schema"] == 2 and len(idx["reviews"]) == 1


def test_save_is_atomic(tmp_path, ri, monkeypatch):
    p = tmp_path / "index.json"
    p.write_text('{"old": true}')

    def killed(*a, **k):
        raise RuntimeError("killed mid write")

    monkeypatch.setattr(ri.json, "dumps", killed)
    with pytest.raises(RuntimeError):
        ri.save_atomic(p, {"new": True})
    assert json.loads(p.read_text()) == {"old": True}
    assert [x.name for x in tmp_path.iterdir()] == ["index.json"]   # (no temp file left)


def test_save_replaces_through_os_replace(tmp_path, ri, monkeypatch):
    calls, real = [], ri.os.replace
    monkeypatch.setattr(ri.os, "replace", lambda a, b: (calls.append((a, b)), real(a, b)))
    ri.save_atomic(tmp_path / "index.json", {"a": 1})
    assert len(calls) == 1 and json.loads((tmp_path / "index.json").read_text()) == {"a": 1}


def test_draft_note_json_prints_id_and_body_hash():
    dn = load("draft_note")
    res = {"id": 3939, "note": "Body as GitLab keeps it\n", "discussion_id": None,
           "position": {"new_path": "a.py", "new_line": 83, "line_range": {"start": {"new_line": 81}}}}
    out = dn.result_json(res, 808)
    assert out == {"id": 3939, "mr": 808, "path": "a.py", "line_from": 81, "line": 83, "reply_to": None,
                   "posted_sha256": sha("Body as GitLab keeps it\n")}
