"""Tests for scripts/mr_discussions.py on fake GitLab JSON (no network).

The fixtures copy the shape of two real cases with made-up text: a diff note GitLab moved to a later
version after a restack (the line number kept, now a docstring), four controls on the same version that
were not moved, and a reply that withdraws the comment six minutes later at the same head.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "mr_discussions.py"
V1, V2, V3 = "1" * 40, "2" * 40, "3" * 40
BASE, BASE2 = "b" * 40, "e" * 40

VERSIONS = [  # newest first, as GitLab returns them
    {"id": 3, "head_commit_sha": V3, "base_commit_sha": BASE2, "start_commit_sha": BASE2,
     "created_at": "2026-11-20T00:44:37.898Z"},
    {"id": 2, "head_commit_sha": V2, "base_commit_sha": BASE, "start_commit_sha": BASE,
     "created_at": "2026-11-16T19:24:56.833Z"},
    {"id": 1, "head_commit_sha": V1, "base_commit_sha": BASE, "start_commit_sha": BASE,
     "created_at": "2026-11-09T21:24:36.679Z"},
]

FILES = {
    (V2, "pkg/test_x.py"): "import x\n\n\ndef _circular_setup(n=2):\n    \"\"\"Two satellites on a circle.\"\"\"\n",
    (V3, "pkg/test_x.py"): "import x\n\ndef _circular_setup(n=2):\n    \"\"\"Two satellites on a circle.\"\"\"\n",
    (V2, "pkg/eph.py"): "a\nb\nlook = RIGHT\n",
}


def pos(head, path, new_line, old_line=None, base=BASE, rng=None):
    p = {"base_sha": base, "start_sha": base, "head_sha": head, "old_path": path, "new_path": path,
         "position_type": "text", "old_line": old_line, "new_line": new_line}
    if rng:
        p["line_range"] = {"start": {"old_line": None, "new_line": rng[0], "type": "new"},
                           "end": {"old_line": None, "new_line": rng[1], "type": "new"}}
    return p


def note(nid, body, created, position=None, author="reviewer", resolved=False, system=False):
    n = {"id": nid, "body": body, "author": {"username": author}, "created_at": created, "updated_at": created,
         "system": system, "resolvable": position is not None, "resolved": resolved}
    if position:
        n["position"] = position
    return n


DISCUSSIONS = [
    {"id": "c" * 40, "notes": [
        note(101, "Is the RIGHT default intentional?", "2026-11-18T22:39:00.683Z", pos(V2, "pkg/eph.py", 3)),
        note(102, "Not intentional, changed.", "2026-11-19T10:00:00.000Z", pos(V2, "pkg/eph.py", 3), author="jisu"),
        note(103, "changed this line", "2026-11-19T10:00:01.000Z", system=True),
    ]},
    {"id": "d" * 40, "notes": [
        note(105, "The data from `_circular_setup` ignore Earth's rotation.", "2026-11-18T22:39:01.544Z",
             pos(V3, "pkg/test_x.py", 4, base=BASE2, rng=(5, 5))),
    ]},
    {"id": "f" * 40, "notes": [
        note(201, "When would I set `area_factor`?", "2026-11-19T23:32:02.338Z", pos(V2, "pkg/eph.py", 2),
             resolved=True),
        note(202, "Oops, already in your known limits. Resolving.", "2026-11-19T23:37:46.450Z",
             pos(V2, "pkg/eph.py", 2), resolved=True),
    ]},
    {"id": "9" * 40, "notes": [note(301, "Overall looks good.", "2026-11-19T01:00:00.000Z")]},
    {"id": "8" * 40, "notes": [
        note(401, "Removed line?", "2026-11-12T01:00:00.000Z", pos(V1, "pkg/eph.py", None, old_line=7)),
    ]},
]


@pytest.fixture(scope="module")
def md():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("mr_discussions", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def threads(md):
    return md.build_threads(DISCUSSIONS, VERSIONS, read_blob=lambda sha, path: FILES.get((sha, path)))


def by_id(threads, c):
    return next(t for t in threads if t["id"] == c * 40)


def test_full_position_and_every_note_kept(threads):
    t = by_id(threads, "c")
    assert t["position"]["head_sha"] == V2 and t["position"]["base_sha"] == BASE
    assert t["position"]["new_path"] == "pkg/eph.py" and t["position"]["new_line"] == 3
    assert [n["id"] for n in t["notes"]] == [101, 102]  # the system note is dropped
    assert t["notes"][1] == {"id": 102, "author": "jisu", "created_at": "2026-11-19T10:00:00.000Z",
                             "updated_at": "2026-11-19T10:00:00.000Z", "body": "Not intentional, changed.",
                             "resolved": False}
    assert t["last_note_id"] == 102 and t["replies"] == 1 and t["last_author"] == "jisu"
    assert t["side"] == "new"


def test_control_on_its_own_version_is_as_posted(threads):
    t = by_id(threads, "c")
    assert t["posted_head"] == V2 and t["anchor_status"] == "as-posted"
    assert "inferred_line" not in t


def test_moved_position_is_inferred_and_the_named_line_found(threads):
    t = by_id(threads, "d")
    # posted while V2 was the head; GitLab now says V3, a version made after the note
    assert t["posted_head"] == V2 and t["position"]["head_sha"] == V3
    assert t["anchor_status"] == "inferred"
    il = t["inferred_line"]
    assert il["line"] == 4
    assert il["at_position"]["text"].strip().startswith('"""Two satellites')
    assert il["at_posted"]["text"].startswith("def _circular_setup")
    assert il["at_posted"]["names"] == ["_circular_setup"] and il["at_position"]["names"] == []
    assert il["reading"] == "posted"
    assert t["anchor"] == "pkg/test_x.py:4-5" or t["anchor"].startswith("pkg/test_x.py:")


def test_withdrawal_at_same_head_is_kept(threads):
    t = by_id(threads, "f")
    assert [n["body"][:4] for n in t["notes"]] == ["When", "Oops"]
    assert t["last_note_id"] == 202 and t["state"] == "resolved"
    assert t["anchor_status"] == "as-posted"


def test_general_and_old_side(threads):
    assert by_id(threads, "9")["anchor_status"] == "general" and by_id(threads, "9")["side"] is None
    t = by_id(threads, "8")
    assert t["side"] == "old" and t["anchor_status"] == "as-posted" and t["posted_head"] == V1


def test_no_versions_gives_unknown(md):
    t = md.build_threads(DISCUSSIONS[:1], [], read_blob=None)[0]
    assert t["anchor_status"] == "unknown" and t["posted_head"] is None


def test_inferred_without_blobs_says_unknown(md):
    t = md.build_threads(DISCUSSIONS[1:2], VERSIONS, read_blob=lambda sha, path: None)[0]
    assert t["anchor_status"] == "inferred" and t["inferred_line"]["reading"] == "unknown"


def test_backticked_names(md):
    assert md.backticked("use `a.b` and `_x(y)` or ``z``") == ["a.b", "_x", "z"]


def test_text_mode_marks_inferred(md, threads, capsys):
    md.print_threads({"iid": 7, "title": "t"}, threads, threads)
    out = capsys.readouterr().out
    line = next(ln for ln in out.splitlines() if "dddddddd" in ln)
    assert "(inferred)" in line


def test_cli_json_end_to_end(md, monkeypatch, capsys):
    calls = []

    def fake_paged(path):
        calls.append(path)
        return VERSIONS if path.endswith("/versions") else DISCUSSIONS

    monkeypatch.setattr(md, "resolve_mr", lambda iid: {"iid": 7, "title": "t"})
    monkeypatch.setattr(md, "paged", fake_paged)
    monkeypatch.setattr(md, "read_blob", lambda sha, path: FILES.get((sha, path)))
    monkeypatch.setattr(sys, "argv", ["mr_discussions.py", "7", "--all", "--json"])
    md.main()
    data = json.loads(capsys.readouterr().out)
    assert len(data) == 5
    assert {t["id"][:1]: t["anchor_status"] for t in data} == {
        "c": "as-posted", "d": "inferred", "f": "as-posted", "9": "general", "8": "as-posted"}
    assert calls == ["projects/:id/merge_requests/7/versions", "projects/:id/merge_requests/7/discussions"]
