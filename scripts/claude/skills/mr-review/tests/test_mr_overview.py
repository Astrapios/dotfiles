"""Tests for scripts/mr_overview.py (facts, build, check, make, embed) and mr_context.py overview add / check --overview.

A small git repo made in tmp_path holds one base commit and one MR commit with every case of the plan's 9 A list:
a new module, a changed function, a docstring-only change, a class changed only in a method, a renamed function,
plus a renamed file and a deleted file; threads: a line thread inside a method, a general thread naming a function.

Run: pixi run --manifest-path <site3d>/pyproject.toml -e test python -m pytest scripts/claude/skills/mr-review/tests
The CLI cases run the scripts under the system python3 (they are standard library only).
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
OVERVIEW = SCRIPTS / "mr_overview.py"
CONTEXT = SCRIPTS / "mr_context.py"
PY = shutil.which("python3", path="/usr/bin") or sys.executable

CORE_BASE = '''"""Core helpers."""


def helper(x):
    """Return x plus one."""
    return x + 1


def documented(x):
    """Old words."""
    return x * 2


def old_name(x):
    y = x - 1
    return y


class Engine:
    """An engine."""

    speed: int = 1

    def run(self, n):
        out = n * self.speed
        return out

    def stop(self):
        return 0
'''

CORE_HEAD = '''"""Core helpers."""


def helper(x):
    """Return x plus one."""
    return x + 2


def documented(x):
    """New words, same code."""
    return x * 2


def new_name(x):
    y = x - 1
    return y


class Engine:
    """An engine."""

    speed: int = 1

    def run(self, n):
        out = n * self.speed * 2
        return out

    def stop(self):
        return 0
'''

MOVED_BASE = "\n\n".join(
    [f"def keep{i}(a):\n    b = a + {i}\n    return b\n" for i in range(8)] + ["def edit(a):\n    return a + 1\n"]
)
MOVED_HEAD = MOVED_BASE.replace("return a + 1", "return a + 100")

USER_BASE = '''from pkg.core import helper


def use(items):
    return helper(items)
'''

USER_HEAD = '''from pkg.core import helper
from pkg.views import select


def use(items):
    """Uses select(items) to pick, then helper."""
    # select(items) is called below
    return helper(select(items))


def use_long(items):
    return select(
        items,
    )
'''

VIEWS = '''"""Which views go together."""

from dataclasses import dataclass


@dataclass
class Params:
    """Settings of the selection.

    Attributes:
        limit: How many views to keep at most,
            counted per group.
        name: A label.
    """

    limit: int = 3
    name: str = "x"


def select(items: list, params: Params | None = None) -> list:
    """Pick the views.

    Args:
        items: The candidate views.
        params: The settings.
    """
    p = params or Params()
    return items[: p.limit]
'''

TEST_VIEWS = '''from pkg.views import select


def test_select():
    assert select([1, 2, 3, 4]) == [1, 2, 3]
'''


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture(scope="module")
def repo(tmp_path_factory) -> dict:
    r = tmp_path_factory.mktemp("repo")
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@example.com")
    _git(r, "config", "user.name", "t")
    (r / "pkg").mkdir()
    (r / "pkg/core.py").write_text(CORE_BASE)
    (r / "pkg/moved_src.py").write_text(MOVED_BASE)
    (r / "pkg/gone.py").write_text("def bye():\n    return 1\n")
    (r / "pkg/user.py").write_text(USER_BASE)
    (r / "other").mkdir()
    (r / "other/x.py").write_text("X = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "base")
    base = _git(r, "rev-parse", "HEAD")
    _git(r, "checkout", "-q", "-b", "mr")
    (r / "pkg/core.py").write_text(CORE_HEAD)
    _git(r, "mv", "pkg/moved_src.py", "pkg/moved_dst.py")
    (r / "pkg/moved_dst.py").write_text(MOVED_HEAD)
    _git(r, "rm", "-q", "pkg/gone.py")
    (r / "pkg/user.py").write_text(USER_HEAD)
    (r / "pkg/views.py").write_text(VIEWS)
    (r / "tests").mkdir()
    (r / "tests/test_views.py").write_text(TEST_VIEWS)
    _git(r, "add", "-A")
    _git(r, "commit", "-q", "-m", "mr")
    head = _git(r, "rev-parse", "HEAD")
    return {"path": str(r), "base": base, "head": head}


@pytest.fixture(scope="module")
def mo():
    spec = importlib.util.spec_from_file_location("mr_overview", OVERVIEW)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["mr_overview"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def fx(mo, repo) -> dict:
    return mo.facts(repo["path"], repo["base"], repo["head"])


@pytest.fixture(scope="module")
def boxes(mo, fx) -> dict:
    return {b["id"]: b for b in mo.boxes_of(fx)}


def sym(fx: dict, path: str, q: str) -> dict:
    f = next(f for f in fx["files"] if f["path"] == path)
    return next(s for s in f["symbols"] if s["qualname"] == q)


def line_of(text: str, needle: str) -> int:
    return next(i for i, ln in enumerate(text.splitlines(), 1) if needle in ln)


def good_notes() -> dict:
    """Notes that describe every box the fixture MR has."""
    d = {
        "pkg/core.py": "Core helpers.",
        "pkg/core.py::helper": "Adds a step to x.",
        "pkg/core.py::new_name": "Subtracts one (renamed from old_name).",
        "pkg/core.py::Engine.run": "Runs the engine n steps.",
        "pkg/gone.py": "A module the MR deletes.",
        "pkg/moved_dst.py": "Moved from moved_src.py.",
        "pkg/moved_dst.py::edit": "Adds to a.",
        "pkg/user.py": "Uses the helpers.",
        "pkg/user.py::use": "Picks then helps.",
        "pkg/user.py::use_long": "Picks over several lines.",
        "pkg/views.py": "Which views go together.",
        "pkg/views.py::Params": "Settings of the selection.",
        "pkg/views.py::select": "Keeps the first views.",
        "tests/test_views.py": "A test of select.",
    }
    return {
        "what": "Adds a view selection and changes the helper step.",
        "pipeline": {"stage": None, "note": "Not on the site3d pipeline."},
        "boxes": d,
        "callers_pass": {"pkg/views.py::select": "use passes its items."},
        "dataflow": {
            "layers": [["items"], ["sel"], ["out"]],
            "nodes": {
                "items": {"label": "Views", "sub": "list", "kind": "data"},
                "sel": {"label": "select", "kind": "added", "box": "pkg/views.py::select"},
                "out": {"label": "Kept views", "kind": "data"},
            },
            "edges": [["items", "sel", "list", {"arg": "items", "type": "list"}], ["sel", "out", "list"]],
        },
        "before_after": [
            {"box": "pkg/core.py::helper", "before": "x + 1", "after": "x + 2"},
            {"box": "pkg/core.py::Engine.run", "before": "n * speed", "after": "n * speed * 2"},
            {"box": "pkg/moved_dst.py::edit", "before": "a + 1", "after": "a + 100"},
            {"box": "pkg/user.py::use", "before": "helper only", "after": "select then helper"},
        ],
        "terms": {"view": "one image of the scene."},
    }


def threads(repo: dict) -> list[dict]:
    run_line = line_of(CORE_HEAD, "out = n * self.speed * 2")
    stop_line = line_of(CORE_HEAD, "return 0")
    old_line = line_of(CORE_BASE, "y = x - 1")

    def pos(**kw):
        p = {"base_sha": repo["base"], "head_sha": repo["head"], "start_sha": repo["base"],
             "old_path": "pkg/core.py", "new_path": "pkg/core.py", "new_line": None, "old_line": None}
        p.update(kw)
        return p

    def t(i, position, body):
        return {"id": f"{i:x}" * 40 if i < 16 else f"{i:040x}", "position": position,
                "notes": [{"id": i, "author": "r", "body": body, "created_at": "2026-11-23T00:00:00Z"}]}

    return [
        t(1, pos(new_line=run_line), "Why times two?"),
        t(2, pos(new_line=stop_line), "Should stop do more?"),
        t(3, None, "Is `helper` still right after this?"),
        t(4, None, "Looks fine overall."),
        t(5, pos(new_line=run_line, head_sha="f" * 40), "At a head the repo lacks."),
        t(6, pos(old_line=old_line), "Why was this renamed?"),
    ]


# --- facts: statuses, box ids, call sites ---


def test_statuses(fx):
    st = {(f["path"], s["qualname"]): s["status"] for f in fx["files"] for s in f["symbols"]}
    assert st[("pkg/views.py", "select")] == "added"  # new module
    assert st[("pkg/views.py", "Params")] == "added"
    assert st[("pkg/core.py", "helper")] == "changed"  # changed function
    assert st[("pkg/core.py", "documented")] == "doc-only"  # docstring-only change
    assert st[("pkg/core.py", "Engine")] == "via-methods"  # class changed only in a method
    assert st[("pkg/core.py", "Engine.run")] == "changed"
    assert st[("pkg/core.py", "Engine.stop")] == "unchanged"
    assert st[("pkg/user.py", "use")] == "changed"
    assert st[("pkg/user.py", "use_long")] == "added"


def test_renamed_function(fx, boxes):
    assert sym(fx, "pkg/core.py", "new_name")["status"] == "added"
    assert sym(fx, "pkg/core.py", "old_name")["status"] == "removed"
    assert "pkg/core.py::new_name" in boxes
    assert "pkg/core.py::old_name" not in boxes


def test_renamed_file_reads_old_path(fx, boxes):
    f = next(f for f in fx["files"] if f["path"] == "pkg/moved_dst.py")
    assert f["status"] == "changed"
    assert f["old_path"] == "pkg/moved_src.py"
    assert (f["plus"], f["minus"]) == (1, 1)
    assert sym(fx, "pkg/moved_dst.py", "keep3")["status"] == "unchanged"
    assert sym(fx, "pkg/moved_dst.py", "edit")["status"] == "changed"
    assert "pkg/moved_dst.py::keep3" not in boxes


def test_deleted_file_is_a_removed_box(fx, boxes):
    f = next(f for f in fx["files"] if f["path"] == "pkg/gone.py")
    assert f["status"] == "removed"
    assert boxes["pkg/gone.py"]["status"] == "removed"
    assert [s["status"] for s in f["symbols"]] == ["removed"]


def test_box_ids(boxes):
    assert sorted(boxes) == sorted(
        [
            "pkg/core.py", "pkg/core.py::helper", "pkg/core.py::documented", "pkg/core.py::new_name",
            "pkg/core.py::Engine", "pkg/core.py::Engine.run",
            "pkg/gone.py",
            "pkg/moved_dst.py", "pkg/moved_dst.py::edit",
            "pkg/user.py", "pkg/user.py::use", "pkg/user.py::use_long",
            "pkg/views.py", "pkg/views.py::Params", "pkg/views.py::select",
            "tests/test_views.py",
        ]
    )
    assert boxes["pkg/views.py"]["status"] == "added"
    assert boxes["tests/test_views.py"]["tests_added"] == 1
    assert boxes["pkg/core.py::Engine.run"]["kind"] == "method"
    assert boxes["pkg/core.py::Engine.run"]["parent"] == "pkg/core.py"


def test_call_sites(fx):
    sites = sym(fx, "pkg/views.py", "select")["call_sites"]
    got = sorted((c["path"], c["line"]) for c in sites)
    user_lines = [i for i, ln in enumerate(USER_HEAD.splitlines(), 1) if "select(" in ln and "import" not in ln]
    # the docstring line and the comment line mention select( but are not calls
    doc_line = line_of(USER_HEAD, "Uses select(items)")
    comment_line = line_of(USER_HEAD, "# select(items)")
    want_user = [n for n in user_lines if n not in (doc_line, comment_line)]
    assert len(want_user) == 2  # the nested call and the multi-line call
    assert got == sorted([("pkg/user.py", n) for n in want_user] + [("tests/test_views.py", line_of(TEST_VIEWS, "assert select"))])
    assert {c["test"] for c in sites if c["path"].startswith("tests/")} == {True}
    assert all(c["in_mr"] for c in sites)


def test_doc_args_and_fields(fx):
    p = sym(fx, "pkg/views.py", "Params")
    assert [f["name"] for f in p["fields"]] == ["limit", "name"]
    assert p["doc_args"]["limit"] == "How many views to keep at most, counted per group."
    s = sym(fx, "pkg/views.py", "select")
    assert s["signature"] == "def select(items: list, params: Params | None = None) -> list"
    assert s["doc_args"]["items"] == "The candidate views."


def test_missing_sha_fails_loudly(mo, repo):
    with pytest.raises(SystemExit, match="not in"):
        mo.facts(repo["path"], repo["base"], "e" * 40)


# --- anchors ---


def test_anchors(mo, repo, boxes):
    bl = list(boxes.values())
    got = {t["id"][:2]: mo.anchor(repo["path"], t, bl) for t in threads(repo)}
    assert got["11"]["box"] == "pkg/core.py::Engine.run"  # line thread inside a method
    assert got["22"]["box"] == "pkg/core.py::Engine"  # an unchanged method: its class box
    assert got["33"]["box"] == "pkg/core.py::helper"  # general thread naming a function
    assert got["44"]["box"] is None  # names nothing: the MR itself
    assert got["55"]["box"] == "pkg/core.py"  # head the repo lacks: the file box, with the reason
    assert "not in the repo" in got["55"]["how"]
    assert got["66"]["box"] == "pkg/core.py"  # a removed line, read at the base: old_name is no box
    assert "old_name" in got["66"]["how"]


# --- check ---


def test_check_passes_good_notes(mo, boxes, fx):
    probs, warn = mo.check_notes(good_notes(), list(boxes.values()), fx)
    assert probs == []
    assert not any("names no arg" in w for w in warn)  # select's only input edge names its arg


def test_check_refuses_unknown_box(mo, boxes, fx):
    n = good_notes()
    n["boxes"]["pkg/core.py::nothing"] = "x"
    n["before_after"].append({"box": "pkg/nope.py::f", "before": "a", "after": "b"})
    probs, _ = mo.check_notes(n, list(boxes.values()), fx)
    assert any("pkg/core.py::nothing" in p for p in probs)
    assert any("pkg/nope.py::f" in p for p in probs)


def test_check_refuses_undescribed_changed_symbol(mo, boxes, fx):
    n = good_notes()
    del n["boxes"]["pkg/core.py::helper"]
    probs, _ = mo.check_notes(n, list(boxes.values()), fx)
    assert any("no description" in p and "pkg/core.py::helper" in p for p in probs)


def test_check_refuses_changed_without_before_after(mo, boxes, fx):
    n = good_notes()
    n["before_after"] = [x for x in n["before_after"] if x["box"] != "pkg/core.py::Engine.run"]
    probs, _ = mo.check_notes(n, list(boxes.values()), fx)
    assert any("before vs after" in p and "Engine.run" in p for p in probs)


def test_check_types_against_signatures(mo, boxes, fx):
    n = good_notes()
    n["dataflow"]["edges"][0][3] = {"arg": "views", "type": "list"}
    probs, _ = mo.check_notes(n, list(boxes.values()), fx)
    assert any("views" in p and "select" in p for p in probs)
    n["dataflow"]["edges"][0][3] = {"arg": "items", "type": "dict"}
    probs, _ = mo.check_notes(n, list(boxes.values()), fx)
    assert any("dict" in p for p in probs)
    n["dataflow"]["edges"][0] = ["items", "sel", "list"]
    probs, warn = mo.check_notes(n, list(boxes.values()), fx)
    assert probs == []
    assert any("names no arg" in w for w in warn)


def test_check_refuses_bad_stage_and_dangling_edge(mo, boxes, fx):
    n = good_notes()
    n["pipeline"]["stage"] = "nowhere"
    n["dataflow"]["edges"].append(["sel", "ghost", ""])
    probs, _ = mo.check_notes(n, list(boxes.values()), fx)
    assert any("nowhere" in p for p in probs)
    assert any("ghost" in p for p in probs)


# --- the data-flow drawing: labels never clip, edge labels always show ---

# Widths the test allows per character, in units of the font size: wider than the renderer's own estimate, so a
# label that passes here fits on a real screen (DejaVu Sans Bold draws collection_resolution_area at 0.57).
SANS_BOLD, MONO_W = 0.6, 0.62
LONG3 = ["ground_resolution_area", "NoiseConfigurationForAllPulses", "collection_resolution_area"]


def flow_items(svg: str) -> tuple[float, list[dict], list[dict]]:
    """The drawing width, its node rectangles and its texts (with a width from the allowances above)."""
    import xml.etree.ElementTree as ET

    root = ET.fromstring(svg)
    width = float(root.get("viewBox").split()[2])
    rects, texts = [], []
    for el in root.iter():
        tagname = el.tag.split("}")[-1]
        if tagname == "rect":
            rects.append({k: float(el.get(k)) for k in ("x", "y", "width", "height")})
        elif tagname == "text":
            size = float(el.get("font-size"))
            mono = "monospace" in el.get("font-family")
            w = len(el.text or "") * size * (MONO_W if mono else SANS_BOLD)
            x = float(el.get("x"))
            lo = {"start": x, "middle": x - w / 2, "end": x - w}[el.get("text-anchor")]
            texts.append({"s": el.text or "", "x0": lo, "x1": lo + w, "y0": float(el.get("y")) - size,
                          "y1": float(el.get("y")) + 2, "size": size})
    return width, rects, texts


def three_long_flow() -> dict:
    return {
        "layers": [["src"], ["a", "b", "c"], ["out"]],
        "nodes": {
            "src": {"label": "Inputs", "kind": "data"},
            "a": {"label": LONG3[0], "sub": "A_cell [m^2]", "kind": "added"},
            "b": {"label": LONG3[1], "sub": "NESZ, reference", "kind": "changed"},
            "c": {"label": LONG3[2], "sub": "same A_cell, no echoes", "kind": "added"},
            "out": {"label": "Noisy phase history", "kind": "data"},
        },
        "edges": [["src", "a", "x"], ["src", "b", "y"], ["src", "c", "z"], ["a", "out", "A_cell"],
                  ["b", "out", "NESZ"], ["c", "out", "A_cell"]],
    }


def inside(t: dict, rects: list[dict]) -> dict | None:
    """The node rectangle holding the middle of text `t`."""
    cx, cy = (t["x0"] + t["x1"]) / 2, t["y1"] - 4
    return next((r for r in rects if r["x"] <= cx <= r["x"] + r["width"] and r["y"] <= cy <= r["y"] + r["height"]),
                None)


def test_three_long_names_in_one_row_do_not_clip(mo):
    df = three_long_flow()
    width, rects, texts = flow_items(mo.dataflow_svg(df))
    drawn = {}
    for t in texts:
        r = inside(t, rects)
        if r is None or t["size"] not in (11.5, 10):  # an edge label, or not a node line
            continue
        assert r["x"] + 2 <= t["x0"] and t["x1"] <= r["x"] + r["width"] - 2, (t["s"], r)
        if t["size"] == 11.5:
            drawn.setdefault((r["x"], r["y"]), []).append(t["s"])
    assert sorted("".join(v) for v in drawn.values()) == sorted(n["label"] for n in df["nodes"].values())
    for t in texts:
        assert 0 <= t["x0"] and t["x1"] <= width, t


def test_long_names_break_after_a_separator(mo):
    _, rects, texts = flow_items(mo.dataflow_svg(three_long_flow()))
    lines = {}
    for t in texts:
        r = inside(t, rects)
        if r is not None and t["size"] == 11.5:
            lines.setdefault((r["x"], r["y"]), []).append(t["s"])
    wrapped = [v for v in lines.values() if "".join(v) in LONG3]
    assert len(wrapped) == 3 and all(len(v) > 1 for v in wrapped), lines
    for v in wrapped:
        name = "".join(v)
        cut = 0
        for piece in v[:-1]:  # every break is after _ or between a lowercase letter and a capital
            cut += len(piece)
            assert name[cut - 1] == "_" or (name[cut - 1].islower() and name[cut].isupper()), (name, piece)


def right_side_flow() -> dict:
    """The !789 shape: collection_resolution_area (right, layer 0) feeds image_noise_power (right, layer 2) past
    NoiseConfig (right, layer 1), so the edge runs down the right side."""
    return {
        "layers": [["geom", "coll"], ["cell", "cfg"], ["add", "pow"]],
        "nodes": {
            "geom": {"label": "_local_pulse_geometry", "kind": "added"},
            "coll": {"label": "collection_resolution_area", "sub": "same A_cell", "kind": "added"},
            "cell": {"label": "ground_resolution_area", "kind": "added"},
            "cfg": {"label": "NoiseConfig", "sub": "NESZ, reference", "kind": "changed"},
            "add": {"label": "add_noise", "kind": "changed"},
            "pow": {"label": "image_noise_power", "kind": "added"},
        },
        "edges": [["geom", "cell", "paths"], ["cell", "add", "area"], ["cfg", "add", "NESZ"],
                  ["cfg", "pow", "NESZ"], ["coll", "pow", "A_cell"]],
    }


def test_side_edge_label_shows(mo):
    width, rects, texts = flow_items(mo.dataflow_svg(right_side_flow()))
    label = [t for t in texts if t["s"] == "A_cell"]
    assert len(label) == 1
    t = label[0]
    assert 0 <= t["x0"] and t["x1"] <= width
    for r in rects:  # no box covers it
        assert (t["x1"] <= r["x"] or t["x0"] >= r["x"] + r["width"]
                or t["y1"] <= r["y"] or t["y0"] >= r["y"] + r["height"]), (t, r)


def test_left_side_edge_label_shows(mo):
    df = right_side_flow()
    for layer in df["layers"]:
        layer.reverse()
    width, rects, texts = flow_items(mo.dataflow_svg(df))
    t = next(t for t in texts if t["s"] == "A_cell")
    assert 0 <= t["x0"] and t["x1"] <= width
    for r in rects:
        assert (t["x1"] <= r["x"] or t["x0"] >= r["x"] + r["width"]
                or t["y1"] <= r["y"] or t["y0"] >= r["y"] + r["height"]), (t, r)


def test_check_warns_when_a_label_would_still_clip(mo, boxes, fx):
    n = good_notes()
    probs, warn = mo.check_notes(n, list(boxes.values()), fx)
    assert not any("clip" in w for w in warn)
    df = three_long_flow()
    df["nodes"]["b"]["label"] = "abcdefghijklmnopqrstuvwxyzabcdefgh"  # one word, no place to break it
    n["dataflow"] = df
    probs, warn = mo.check_notes(n, list(boxes.values()), fx)
    assert probs == []
    clip = [w for w in warn if "clip" in w]
    assert len(clip) == 1 and "'b'" in clip[0] and "abcdefgh" in clip[0], warn
    n["dataflow"] = three_long_flow()
    _, warn = mo.check_notes(n, list(boxes.values()), fx)
    assert not any("clip" in w for w in warn)


# --- build and the stored files (CLI, system python3) ---


def run(script: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([PY, str(script), *args], capture_output=True, text=True, check=False)


@pytest.fixture(scope="module")
def built(repo, tmp_path_factory) -> dict:
    d = tmp_path_factory.mktemp("ov") / "7"
    inp = d.parent
    (inp / "notes.json").write_text(json.dumps(good_notes()))
    (inp / "threads.json").write_text(json.dumps(threads(repo)))
    r = run(OVERVIEW, "make", repo["path"], "--base", repo["base"], "--head", repo["head"],
            "--notes", str(inp / "notes.json"), "--threads", str(inp / "threads.json"), "--out", str(d))
    assert r.returncode == 0, r.stdout + r.stderr
    return {"dir": d, "h12": repo["head"][:12], "head": repo["head"], "out": r.stdout}


def test_make_writes_the_stored_files(built):
    d, h = built["dir"], built["h12"]
    for name in (f"overview-{h}.html", f"overview-{h}.json", f"overview-{h}.txt",
                 f"overview-{h}-map.svg", f"overview-{h}-flow.svg", f"overview-{h}-pipe.svg"):
        assert (d / name).exists(), name
    minis = sorted(p.name for p in d.glob(f"overview-{h}-mini-*.svg"))
    # threads 1, 2, 3, 5, 6 land on a box; thread 4 names nothing
    assert minis == sorted(f"overview-{h}-mini-{c * 8}.svg" for c in "12356")
    data = json.loads((d / f"overview-{h}.json").read_text())
    assert set(data) >= {"facts", "notes", "anchors", "boxes"}
    assert data["anchors"]["1" * 40]["box"] == "pkg/core.py::Engine.run"


def test_page_safe(mo, built):
    d, h = built["dir"], built["h12"]
    page = (d / f"overview-{h}.html").read_text()
    assert "<script" not in page.lower()
    assert mo.page_problems(page) == []
    for svg in d.glob("*.svg"):
        txt = svg.read_text()
        assert mo.svg_problems(txt) == [], svg.name
        assert "href=" in txt or svg.name.endswith("-pipe.svg")
    assert 'id="card-pkg-views-py-select"' in page
    assert mo.page_problems(page.replace("</section>", "<script>x()</script></section>", 1))
    assert mo.page_problems(page.replace("<section", '<section onclick="x()"', 1))
    assert mo.svg_problems('<svg><a href="https://example.com"><g/></a></svg>')
    assert mo.svg_problems('<svg><g onload="x()"/></svg>')
    assert mo.svg_problems('<svg><foreignObject/></svg>')


def test_page_explains_the_tags(built):
    page = (built["dir"] / f"overview-{built['h12']}.html").read_text()
    first = page.index('class="src ')
    legend = page.index('class="legend tags"')
    assert legend < first < page.index("</p>", legend), "the legend is not where the tags first appear"
    line = page[legend: page.index("</p>", legend)]
    assert "the reviewer&#x27;s words" in line or "the reviewer's words" in line
    assert "read from the diff" in line


def test_check_cli(built):
    r = run(OVERVIEW, "check", str(built["dir"]), "--head", built["head"])
    assert r.returncode == 0, r.stdout + r.stderr
    assert "ok" in r.stdout


def test_embed_under_title(built, tmp_path):
    page = tmp_path / "review.html"
    page.write_text("<html><body><h1>Review of !7</h1><p>Verdict</p></body></html>")
    for _ in range(2):  # a second embed replaces the first
        r = run(OVERVIEW, "embed", str(built["dir"]), "--head", built["head"], "--into", str(page))
        assert r.returncode == 0, r.stderr
    txt = page.read_text()
    assert txt.count("<!-- mr-overview:start") == 1
    assert txt.index("</h1>") < txt.index("mr-overview:start") < txt.index("<p>Verdict")


# --- mr_context.py overview add / check --overview ---


@pytest.fixture()
def ctx(built, tmp_path) -> Path:
    d = tmp_path / "7"
    shutil.copytree(built["dir"], d)
    r = run(CONTEXT, "overview", "add", str(d), "--head", built["head"], "--task", "T-xx", "--cold-read", "rewritten",
            "--cold-read-note", "terms were undefined", "--requested-by", "review", "--project", "product/site3d")
    assert r.returncode == 0, r.stdout + r.stderr
    return d


def test_overview_add_writes_entry(ctx, built):
    idx = json.loads((ctx / "index.json").read_text())
    ov = idx["overview"]
    h = built["h12"]
    assert ov["head_sha"] == built["head"]
    assert ov["page"] == f"overview-{h}.html"
    assert ov["svgs"] == {"map": f"overview-{h}-map.svg", "flow": f"overview-{h}-flow.svg",
                          "pipe": f"overview-{h}-pipe.svg"}
    assert ov["task"] == "T-xx" and ov["requested_by"] == "review" and ov["cold_read"] == "rewritten"
    assert {"id": "pkg/core.py::Engine.run", "kind": "method", "status": "changed",
            "line": line_of(CORE_HEAD, "def run")} in ov["boxes"]
    assert ov["threads"]["1" * 40] == "pkg/core.py::Engine.run"
    assert ov["threads"]["4" * 40] is None
    assert idx["mr_iid"] == 7 and idx["entries"] == []


def test_check_overview_passes(ctx, built):
    r = run(CONTEXT, "check", str(ctx), "--overview", built["head"])
    assert r.returncode == 0, r.stdout + r.stderr


def test_check_overview_fails_on_missing_svg(ctx, built):
    (ctx / f"overview-{built['h12']}-flow.svg").unlink()
    r = run(CONTEXT, "check", str(ctx), "--overview", built["head"])
    assert r.returncode == 1 and "flow.svg" in r.stdout


def test_check_overview_fails_on_missing_mini(ctx, built):
    (ctx / f"overview-{built['h12']}-mini-{'1' * 8}.svg").unlink()
    r = run(CONTEXT, "check", str(ctx), "--overview", built["head"])
    assert r.returncode == 1 and "mini" in r.stdout


def test_check_overview_fails_on_script(ctx, built):
    p = ctx / f"overview-{built['h12']}.html"
    p.write_text(p.read_text().replace("</body>", "<script>alert(1)</script></body>"))
    r = run(CONTEXT, "check", str(ctx), "--overview", built["head"])
    assert r.returncode == 1 and "script" in r.stdout


def test_check_overview_fails_on_unlisted_anchor(ctx, built):
    idx = json.loads((ctx / "index.json").read_text())
    idx["overview"]["threads"]["1" * 40] = "pkg/core.py::nowhere"
    (ctx / "index.json").write_text(json.dumps(idx))
    r = run(CONTEXT, "check", str(ctx), "--overview", built["head"])
    assert r.returncode == 1 and "nowhere" in r.stdout


def test_check_overview_fails_on_other_head(ctx):
    r = run(CONTEXT, "check", str(ctx), "--overview", "a" * 40)
    assert r.returncode == 1 and "aaaaaaaaa" in r.stdout


def test_check_overview_fails_without_entry(tmp_path):
    d = tmp_path / "7"
    d.mkdir()
    (d / "index.json").write_text(json.dumps({"schema": 2, "project": "p/x", "mr_iid": 7, "entries": [
        {"discussion_id": "a" * 40}]}))
    r = run(CONTEXT, "check", str(d), "--overview", "b" * 40)
    assert r.returncode == 1 and "no overview" in r.stdout


def test_overview_add_refuses_failing_notes(built, tmp_path):
    d = tmp_path / "7"
    shutil.copytree(built["dir"], d)
    p = d / f"overview-{built['h12']}.json"
    data = json.loads(p.read_text())
    del data["notes"]["boxes"]["pkg/core.py::helper"]
    p.write_text(json.dumps(data))
    r = run(CONTEXT, "overview", "add", str(d), "--head", built["head"], "--task", "T-xx", "--cold-read", "pass",
            "--requested-by", "button")
    assert r.returncode == 1 and "helper" in r.stdout
    assert not (d / "index.json").exists()
