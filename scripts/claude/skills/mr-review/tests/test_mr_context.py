"""Tests for scripts/mr_context.py: schema 1 and 2 entries, draft checks, freshness by last note.

Run: pixi run --manifest-path <site3d>/pyproject.toml -e test python -m pytest scripts/claude/skills/mr-review/tests
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mr_context.py"
DISC = "a" * 40
DISC2 = "b" * 40
HEAD = "c" * 40
OTHER = "d" * 40


@pytest.fixture(scope="module")
def mc():
    spec = importlib.util.spec_from_file_location("mr_context", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def block(draft: str, tag: str = 'div class="draft"') -> str:
    end = tag.split()[0]
    return (
        "<html><body><h1>t</h1><svg></svg><p>Checked. Right (tested: 2 pairs, 25 dB).</p>"
        f"<{tag}>{draft}</{end}></body></html>"
    )


def v1_entry(disc: str = DISC, **kw) -> dict:
    e = {
        "discussion_id": disc,
        "head_sha": HEAD,
        "tier": "standard",
        "title": "t",
        "block": f"{disc}.html",
        "deep_page": None,
        "cold_read": "pass",
        "cold_read_note": "",
        "requested_by": "review",
        "made_at": "2026-11-20T00:00:00-08:00",
    }
    e.update(kw)
    return e


def v2_entry(disc: str = DISC, **kw) -> dict:
    e = v1_entry(disc)
    e.update(last_note_id=100, anchor_status="as-posted", evidence=[{"mr": 7, "sha": HEAD}], promises=[])
    e.update(kw)
    return e


def folder(tmp_path: Path, entries: list, blocks: dict, schema: int = 2, **extra) -> Path:
    d = tmp_path / "7"
    d.mkdir()
    for name, html in blocks.items():
        (d / name).write_text(html)
    idx = {"schema": schema, "project": "product/site3d", "mr_iid": 7, "entries": entries, **extra}
    (d / "index.json").write_text(json.dumps(idx))
    return d


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True)


# --- draft checks (change 4) ---


def test_unfilled_slot_fails(tmp_path):
    d = folder(tmp_path, [v1_entry()], {f"{DISC}.html": block("On intent: [intent: a tool / flight data]. Thanks.")}, schema=1)
    r = run("check", str(d))
    assert r.returncode == 1
    assert "unfilled slot [intent: a tool / flight data]" in r.stdout


def test_jisu_slot_only_warns(tmp_path):
    d = folder(tmp_path, [v2_entry()], {f"{DISC}.html": block("On intent: [JISU: research tool or flight data].")})
    r = run("check", str(d))
    assert r.returncode == 0, r.stdout
    assert "warn:" in r.stdout and "[JISU: research tool or flight data]" in r.stdout


@pytest.mark.parametrize(
    "text",
    [
        "Index `a[0]` and arr[1] and f(x)[key] stay.",
        "A [link](https://example.com) and a [ ] box and [x] done.",
        "Code <code>A=[[1],[1]]</code> is masked.",
        "Fenced:\n```suggestion:-1+0\n    return [config.random_seed, int(config.ray_batch[0])]\n```\nDone.",
        "Numbers [1] and [2, 3] are not slots.",
    ],
)
def test_not_a_slot(mc, text):
    assert mc.find_slots(mc.mask_code(text)) == []


def test_slot_kinds(mc):
    text = mc.mask_code("Pick [intent: a / b], or [TODO], or [JISU: say which].")
    assert mc.find_slots(text) == ["[intent: a / b]", "[TODO]", "[JISU: say which]"]


def test_draft_found_in_pre_reply(mc):
    html = block("Thanks, I'll open an issue for it.", tag='pre class="reply"')
    assert mc.extract_drafts(html) == ["Thanks, I'll open an issue for it."]


def test_promises_split_on_sentences_not_code_dots(mc):
    text = (
        "Right. I'll switch both to skip on `get_cuda_device_count() == 0` like the `test_gpu.py` ones. "
        "Nothing else changes.\n\nI will add a test. We checked it."
    )
    assert mc.find_promises(text) == [
        "I'll switch both to skip on `get_cuda_device_count() == 0` like the `test_gpu.py` ones.",
        "I will add a test.",
    ]


def test_promise_before_a_fence_ends_at_the_fence(mc):
    text = "So I'll take your one-liner:\n```suggestion:-2+0\n    x = 1. y\n```\nThanks."
    assert mc.find_promises(text) == ["So I'll take your one-liner:"]


def test_v2_promise_not_queued_fails(tmp_path):
    d = folder(tmp_path, [v2_entry()], {f"{DISC}.html": block("Yes. I'll add a docstring sentence.")})
    r = run("check", str(d))
    assert r.returncode == 1
    assert "promise not queued" in r.stdout and "I'll add a docstring sentence." in r.stdout


def test_v2_promise_queued_passes(tmp_path):
    p = [{"text": "I'll add a docstring sentence.", "status": "queued"}]
    d = folder(tmp_path, [v2_entry(promises=p)], {f"{DISC}.html": block("Yes. I'll add a docstring sentence.")})
    r = run("check", str(d))
    assert r.returncode == 0, r.stdout


def test_v1_promise_warns_only(tmp_path):
    d = folder(tmp_path, [v1_entry()], {f"{DISC}.html": block("Yes. I'll add a docstring sentence.")}, schema=1)
    r = run("check", str(d))
    assert r.returncode == 0, r.stdout
    assert "warn:" in r.stdout and "I'll add a docstring sentence." in r.stdout


def test_add_queues_promises_from_the_block(tmp_path):
    d = tmp_path / "7"
    d.mkdir()
    (d / f"{DISC}.html").write_text(block("Agreed. I'll rename it. I will add a test too."))
    r = run(
        "add", str(d), "--discussion", DISC, "--head", HEAD, "--tier", "short", "--title", "t",
        "--last-note", "101", "--anchor-status", "inferred", "--evidence", f"809={OTHER}", "--cold-read", "pass",
    )
    assert r.returncode == 0, r.stderr
    e = json.loads((d / "index.json").read_text())["entries"][0]
    assert e["promises"] == [
        {"text": "I'll rename it.", "status": "queued"},
        {"text": "I will add a test too.", "status": "queued"},
    ]
    assert e["last_note_id"] == 101 and e["anchor_status"] == "inferred"
    assert {"mr": 7, "sha": HEAD} in e["evidence"] and {"mr": 809, "sha": OTHER} in e["evidence"]
    assert json.loads((d / "index.json").read_text())["schema"] == 2
    assert run("check", str(d)).returncode == 0
    out = run("promises", str(d), "--md").stdout
    assert f"{DISC[:8]}" in out and "I'll rename it." in out and "queued" in out


def test_add_keeps_a_promise_status(tmp_path):
    d = tmp_path / "7"
    d.mkdir()
    (d / f"{DISC}.html").write_text(block("I'll rename it."))
    args = ["add", str(d), "--discussion", DISC, "--head", HEAD, "--tier", "short", "--title", "t",
            "--last-note", "101", "--anchor-status", "as-posted"]
    run(*args)
    idx = json.loads((d / "index.json").read_text())
    idx["entries"][0]["promises"][0]["status"] = "done"
    (d / "index.json").write_text(json.dumps(idx))
    run(*args)
    assert json.loads((d / "index.json").read_text())["entries"][0]["promises"][0]["status"] == "done"


def test_bad_promise_status_fails(tmp_path):
    p = [{"text": "I'll rename it.", "status": "maybe"}]
    d = folder(tmp_path, [v2_entry(promises=p)], {f"{DISC}.html": block("I'll rename it.")})
    assert "status 'maybe'" in run("check", str(d)).stdout


# --- schema 2 and freshness (change 2) ---


def test_partial_v2_entry_fails(tmp_path):
    e = v1_entry(last_note_id=5)
    d = folder(tmp_path, [e], {f"{DISC}.html": block("Thanks.")})
    r = run("check", str(d))
    assert r.returncode == 1 and "no anchor_status" in r.stdout


def test_bad_anchor_status_fails(tmp_path):
    d = folder(tmp_path, [v2_entry(anchor_status="moved")], {f"{DISC}.html": block("Thanks.")})
    assert "anchor_status 'moved'" in run("check", str(d)).stdout


def test_v1_entry_warns_head_only_freshness(tmp_path):
    d = folder(tmp_path, [v1_entry()], {f"{DISC}.html": block("Thanks.")}, schema=1)
    r = run("check", str(d))
    assert r.returncode == 0 and "written before schema 2" in r.stdout


def threads_file(tmp_path: Path, note_ids: list[int], disc: str = DISC) -> Path:
    t = [{"id": disc, "last_note_id": note_ids[-1], "notes": [{"id": i} for i in note_ids]}]
    p = tmp_path / "threads.json"
    p.write_text(json.dumps(t))
    return p


def test_newer_note_at_same_head_is_stale(tmp_path):
    # A reviewer withdraws the comment six minutes later, no push: the head is unchanged.
    d = folder(tmp_path, [v2_entry(last_note_id=30129)], {f"{DISC}.html": block("Thanks.")})
    th = threads_file(tmp_path, [30129, 30134])
    r = run("check", str(d), "--head", HEAD, "--threads", str(th))
    assert r.returncode == 1
    assert "stale: 1 newer note (30134) since 30129" in r.stdout


def test_same_last_note_is_fresh(tmp_path):
    d = folder(tmp_path, [v2_entry(last_note_id=30134)], {f"{DISC}.html": block("Thanks.")})
    th = threads_file(tmp_path, [30129, 30134])
    assert run("check", str(d), "--head", HEAD, "--threads", str(th)).returncode == 0


def test_thread_missing_warns(tmp_path):
    d = folder(tmp_path, [v2_entry()], {f"{DISC}.html": block("Thanks.")})
    th = threads_file(tmp_path, [1], disc=DISC2)
    r = run("check", str(d), "--threads", str(th))
    assert r.returncode == 0 and "not in the thread list" in r.stdout


def test_evidence_from_other_mr_at_old_head_is_stale(tmp_path):
    ev = [{"mr": 7, "sha": HEAD}, {"mr": 809, "sha": OTHER}]
    d = folder(tmp_path, [v2_entry(evidence=ev)], {f"{DISC}.html": block("Thanks.")})
    r = run("check", str(d), "--mr-head", f"809={'e' * 40}")
    assert r.returncode == 1 and "evidence from !809 at ddddddddd" in r.stdout
    assert run("check", str(d), "--mr-head", f"809={OTHER}").returncode == 0


def test_bad_evidence_fails(tmp_path):
    d = folder(tmp_path, [v2_entry(evidence=[{"mr": "x", "sha": "123"}])], {f"{DISC}.html": block("Thanks.")})
    assert "evidence item 0" in run("check", str(d)).stdout


# --- findings list (the !808 layout) ---


def finding(**kw) -> dict:
    f = {k: v for k, v in v1_entry().items() if k != "discussion_id"}
    f.update(finding="finding-1", block="finding-1.html", draft_note_id=3939)
    f.update(kw)
    return f


def test_findings_only_folder_passes(tmp_path):
    d = folder(tmp_path, [], {"finding-1.html": block("Could this raise here?")}, schema=1, findings=[finding()])
    r = run("check", str(d))
    assert r.returncode == 0, r.stdout


def test_findings_slot_fails(tmp_path):
    d = folder(tmp_path, [], {"finding-1.html": block("Could [NAME] raise?")}, schema=1, findings=[finding()])
    r = run("check", str(d))
    assert r.returncode == 1 and "finding-1" in r.stdout


def test_bad_finding_name_fails(tmp_path):
    d = folder(tmp_path, [], {"x.html": block("Ok.")}, schema=1, findings=[finding(finding="x", block="x.html")])
    assert "finding name" in run("check", str(d)).stdout


def test_empty_folder_still_fails(tmp_path):
    d = folder(tmp_path, [], {}, schema=1)
    assert run("check", str(d)).returncode == 1


def test_no_draft_element_warns(tmp_path):
    d = folder(tmp_path, [v2_entry()], {f"{DISC}.html": "<html><svg></svg><p>No reply drafted.</p></html>"})
    r = run("check", str(d))
    assert r.returncode == 0 and "no draft element" in r.stdout
