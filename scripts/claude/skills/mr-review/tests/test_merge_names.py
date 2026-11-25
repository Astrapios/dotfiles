"""Tests for scripts/merge_names.py on small throwaway git repos.

Run: uv run --no-project --with pytest --with pytest-mock --with ruff pytest scripts/claude/skills/mr-review/tests
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "merge_names.py"


@pytest.fixture(scope="module")
def mn():
    spec = importlib.util.spec_from_file_location("merge_names", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["merge_names"] = mod  # dataclasses look the module up while the class body runs
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def needs_linter(mn, tmp_path):
    if mn.find_linter(str(tmp_path)) is None:
        pytest.skip("no ruff or pyflakes available")


def git(repo: Path, *args: str) -> str:
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run([*cmd, *args], check=True, capture_output=True, text=True).stdout


def write(repo: Path, files: dict[str, str]) -> None:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)


def make_repo(tmp_path: Path, base: dict[str, str], side_a: dict[str, str], side_b: dict[str, str]) -> Path:
    """A repo with a base commit on main and branches a and b, each one commit on top of it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    write(repo, base)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "--no-verify", "-m", "base")
    for name, files in (("a", side_a), ("b", side_b)):
        git(repo, "checkout", "-q", "-b", name, "main")
        write(repo, files)
        git(repo, "add", "-A")
        git(repo, "commit", "-q", "--no-verify", "-m", name)
    git(repo, "checkout", "-q", "main")
    return repo


def merged_line(repo: Path, tree: str, path: str, text: str) -> int:
    lines = git(repo, "cat-file", "-p", f"{tree}:{path}").splitlines()
    return next(i for i, ln in enumerate(lines, 1) if ln.strip() == text)


# ---------------------------------------------------------------- shape 1: a moved body (!788 x !797)

TRACE_BASE = """def trace(scene):
    a = 1
    b = 2
    c = 3
    d = 4
    e = 5
    f = 6
    return scene + a + b + c + d + e + f
"""
# side A adds a parameter and uses it in the last line of the body
TRACE_A = TRACE_BASE.replace("def trace(scene):", "def trace(scene, reuse=False):").replace(
    "return scene + a", "return (scene if reuse else 0) + a"
)
# side B splits the function: the tail of the old body now belongs to a new function without that parameter
TRACE_B = TRACE_BASE.replace(
    "    c = 3\n", "    c = 3\n    return scene + a + b + c\n\n\ndef trace_tail(scene):\n    a = b = c = 0\n"
)


def test_moved_body_keeps_a_parameter_the_other_side_never_had(mn, tmp_path, needs_linter):
    repo = make_repo(tmp_path, {"mod.py": TRACE_BASE}, {"mod.py": TRACE_A}, {"mod.py": TRACE_B})
    # git itself sees nothing wrong
    plain = subprocess.run(
        ["git", "-C", str(repo), "merge-tree", "--write-tree", "a", "b"], check=False, capture_output=True
    )
    assert plain.returncode == 0

    res = mn.check(str(repo), "a", "b", None)

    assert res["conflicts"] == []
    line = merged_line(repo, res["merged_tree"], "mod.py", "return (scene if reuse else 0) + a + b + c + d + e + f")
    new = [u for u in res["undefined"] if not u["pre_existing"]]
    assert [(u["path"], u["line"], u["code"]) for u in new] == [("mod.py", line, "F821")]
    assert "reuse" in new[0]["message"]
    assert res["findings"] == 1


# ---------------------------------------------------------------- shape 2: a renamed keyword (!789 x !805)

ANTENNA_BASE = """class Antenna:
    def __init__(self, wavelength, aperture_length):
        self.wavelength = wavelength
        self.aperture_length = aperture_length

    @classmethod
    def square(cls, wavelength, aperture_length):
        return cls(wavelength, aperture_length)
"""
ANTENNA_A = """class Antenna:
    def __init__(self, wavelength, az_aperture_length):
        self.wavelength = wavelength
        self.az_aperture_length = az_aperture_length

    @classmethod
    def square(cls, wavelength, aperture_length):
        return cls(wavelength, az_aperture_length=aperture_length)
"""
CORE_BASE = """def gain(antenna):
    return antenna.aperture_length * 2


def other():
    x = 1
    y = 2
    return x + y
"""
# side A follows its own rename
CORE_A = CORE_BASE.replace("antenna.aperture_length * 2", "antenna.az_aperture_length * 2")
# side B edits the same line (a conflict) and adds a function with the old name far below it (no conflict)
CORE_B = CORE_BASE.replace("antenna.aperture_length * 2", "antenna.aperture_length * 3") + (
    "\n\ndef resolution(antenna):\n    return antenna.aperture_length / 2\n"
)
TEST_B = """from pkg.antenna import Antenna
from pkg.core import resolution


def test_resolution():
    assert resolution(Antenna(1.0, aperture_length=2.0)) == 1.0
    assert resolution(Antenna.square(1.0, aperture_length=2.0)) == 1.0
"""


@pytest.fixture
def rename_repo(tmp_path):
    base = {"pkg/antenna.py": ANTENNA_BASE, "pkg/core.py": CORE_BASE}
    return make_repo(
        tmp_path,
        base,
        {"pkg/antenna.py": ANTENNA_A, "pkg/core.py": CORE_A},
        {"pkg/core.py": CORE_B, "tests/test_core.py": TEST_B},
    )


def test_renamed_keyword_while_other_side_adds_calls_with_the_old_name(mn, rename_repo):
    repo = rename_repo
    res = mn.check(str(repo), "a", "b", None)
    tree = res["merged_tree"]

    # (a) one textual conflict, on the gain() line
    assert [c["path"] for c in res["conflicts"]] == ["pkg/core.py"]
    ((start, end),) = res["conflicts"][0]["regions"]
    assert end - start == 4  # <<<, A line, ===, B line, >>>

    groups = {(g["name"], g["what"]): g for g in res["removed_names_still_used"]}
    assert set(groups) == {("aperture_length", "attribute"), ("aperture_length", "keyword argument of Antenna()")}

    # the attribute: B's resolution() outside the hunk and B's side of the conflicted line inside it
    attr = groups[("aperture_length", "attribute")]
    assert attr["side"] == "A" and attr["owners"] == ["Antenna"]
    assert attr["renamed_to"] == ["az_aperture_length"]
    refs = {(r["path"], r["line"], r["in_conflict"]) for r in attr["refs"]}
    outside = merged_line(repo, tree, "pkg/core.py", "return antenna.aperture_length / 2")
    inside = merged_line(repo, tree, "pkg/core.py", "return antenna.aperture_length * 3")
    assert refs == {("pkg/core.py", outside, False), ("pkg/core.py", inside, True)}
    assert start < inside < end < outside

    # the keyword: only the Antenna(...) call, not Antenna.square(...), which keeps the name
    kw = groups[("aperture_length", "keyword argument of Antenna()")]
    call = merged_line(repo, tree, "tests/test_core.py", "assert resolution(Antenna(1.0, aperture_length=2.0)) == 1.0")
    assert [(r["path"], r["line"], r["how"]) for r in kw["refs"]] == [("tests/test_core.py", call, "keyword")]


def test_text_and_json_output_and_exit_status(mn, rename_repo, capsys):
    assert mn.main([str(rename_repo), "a", "b"]) == 1
    text = capsys.readouterr().out
    assert "(a) textual conflicts: 1" in text
    assert "aperture_length: keyword argument of Antenna(), removed by A" in text
    assert "[in conflict hunk]" in text

    assert mn.main([str(rename_repo), "a", "b", "--json"]) == 1
    res = json.loads(capsys.readouterr().out)
    assert res["findings"] == 1 + 2 + len([u for u in res["undefined"] if not u["pre_existing"]])


def test_out_folder_and_exit_zero(mn, rename_repo, tmp_path, capsys):
    out = tmp_path / "out"
    assert mn.main([str(rename_repo), "a", "b", "--out", str(out), "--exit-zero"]) == 0
    printed = capsys.readouterr().out
    assert (out / "merge_names.txt").read_text() == printed
    assert json.loads((out / "merge_names.json").read_text())["findings"] >= 3


def test_base_option_matches_the_merge_base(mn, rename_repo):
    base = git(rename_repo, "rev-parse", "main").strip()
    auto = mn.check(str(rename_repo), "a", "b", None)
    given = mn.check(str(rename_repo), "a", "b", base)
    assert given["base"] == auto["base"] == base
    assert given["removed_names_still_used"] == auto["removed_names_still_used"]


def test_unknown_head_exits_2(mn, rename_repo, capsys):
    assert mn.main([str(rename_repo), "a", "no-such-branch"]) == 2
    assert "no-such-branch" in capsys.readouterr().err


# ---------------------------------------------------------------- clean merges and pre-existing problems


def test_independent_changes_are_clean(mn, tmp_path, needs_linter, capsys):
    base = {"one.py": "def f(x):\n    return x\n", "two.py": "def g(y):\n    return y\n"}
    repo = make_repo(
        tmp_path,
        base,
        {"one.py": "def f(x, scale=1):\n    return x * scale\n"},
        {"two.py": "def g(y):\n    return y + 1\n", "three.py": "from one import f\n\nZ = f(2, scale=3)\n"},
    )
    assert mn.main([str(repo), "a", "b"]) == 0
    assert "result: clean" in capsys.readouterr().out


def test_undefined_name_already_in_a_head_is_pre_existing(mn, tmp_path, needs_linter):
    base = {"m.py": "def f():\n    return missing_name\n\n\ndef g():\n    return 1\n"}
    repo = make_repo(
        tmp_path,
        base,
        {"m.py": base["m.py"].replace("return 1", "return 2")},
        {"n.py": "def h():\n    return 3\n"},
    )
    res = mn.check(str(repo), "a", "b", None)
    assert [(u["code"], u["pre_existing"]) for u in res["undefined"]] == [("F821", True)]
    assert res["findings"] == 0


def test_name_removed_but_still_used_only_by_its_own_side_is_not_a_merge_finding(mn, tmp_path):
    # side A removes a keyword but leaves one stale call itself; that is A's bug, not the merge's
    base = {"m.py": "def f(x, k=0):\n    return x + k\n", "use.py": "from m import f\n\nV = f(1, k=2)\n"}
    repo = make_repo(
        tmp_path,
        base,
        {"m.py": "def f(x):\n    return x\n"},
        {"other.py": "W = 1\n"},
    )
    res = mn.check(str(repo), "a", "b", None)
    assert res["removed_names_still_used"] == []


# ---------------------------------------------------------------- helpers


def test_resolve_maps_each_kept_line_to_the_merged_file(mn):
    text = "x = 0\n<<<<<<< a\ny = 1\n=======\ny = 2\nz = 3\n>>>>>>> b\nw = 4\n"
    assert mn.conflict_regions(text) == [(2, 7)]
    src_a, map_a = mn.resolve(text, "A")
    src_b, map_b = mn.resolve(text, "B")
    assert src_a == "x = 0\ny = 1\nw = 4\n" and map_a == [1, 3, 8]
    assert src_b == "x = 0\ny = 2\nz = 3\nw = 4\n" and map_b == [1, 5, 6, 8]
    diff3 = "<<<<<<< a\ny = 1\n||||||| base\ny = 0\n=======\ny = 2\n>>>>>>> b\n"
    assert mn.resolve(diff3, "A")[0] == "y = 1\n" and mn.resolve(diff3, "B")[0] == "y = 2\n"


# ---------------------------------------------------------------- shape 3: a local moved into a helper (!812 x !788)

EDGES_BASE = """import numpy as np


def visible_edges(edges, normals, look):
    edge_visible = (normals @ look) > 0
    amplitudes = np.where(edge_visible, 1.0, 0.0)
    scale = 2.0
    offset = 0.5
    amplitudes = amplitudes * scale + offset
    return edges, amplitudes
"""
# side A (main's !788) keys a phase by the local mask, at the end of the body
EDGES_A = EDGES_BASE.replace(
    "    return edges, amplitudes\n",
    "    phase_keys = np.flatnonzero(edge_visible)\n    return edges, amplitudes, phase_keys\n",
)
# side B (!812) moves the mask into a helper; the local edge_visible is gone
EDGES_B = EDGES_BASE.replace(
    "def visible_edges(edges, normals, look):\n    edge_visible = (normals @ look) > 0\n"
    "    amplitudes = np.where(edge_visible, 1.0, 0.0)\n",
    "def visible_edge_samples(normals, look):\n    return (normals @ look) > 0\n\n\n"
    "def visible_edges(edges, normals, look):\n    amplitudes = np.where(visible_edge_samples(normals, look), 1.0, 0.0)\n",
)
EDGE_USE = "phase_keys = np.flatnonzero(edge_visible)"


@pytest.fixture
def edge_repo(tmp_path):
    return make_repo(tmp_path, {"raytracer.py": EDGES_BASE}, {"raytracer.py": EDGES_A}, {"raytracer.py": EDGES_B})


def new_findings(res):
    return [(u["path"], u["line"], u["code"], u["message"]) for u in res["undefined"] if not u["pre_existing"]]


def test_local_moved_into_a_helper_while_the_other_side_adds_a_use(mn, edge_repo, needs_linter):
    repo = edge_repo
    res = mn.check(str(repo), "a", "b", None)
    assert res["conflicts"] == []
    assert res["removed_names_still_used"] == []  # a function local is not a public name
    line = merged_line(repo, res["merged_tree"], "raytracer.py", EDGE_USE)
    assert new_findings(res) == [("raytracer.py", line, "F821", "Undefined name `edge_visible`")]
    assert res["findings"] == 1


def test_a_head_rebased_onto_the_target_still_shows_the_break(mn, edge_repo, needs_linter):
    # after a rebase the target is an ancestor of the head: the merged tree is the head itself, so a break the
    # rebase carried in is in head B alone and must still count (only the target's own findings are pre-existing)
    repo = edge_repo
    git(repo, "checkout", "-q", "-b", "b-rebased", "a")
    merged = mn.check(str(repo), "a", "b", None)["merged_tree"]
    write(repo, {"raytracer.py": git(repo, "cat-file", "-p", f"{merged}:raytracer.py")})
    git(repo, "commit", "-q", "--no-verify", "-am", "b rebased onto a")
    git(repo, "checkout", "-q", "main")

    res = mn.check(str(repo), "a", "b-rebased", None)
    assert res["base"] == git(repo, "rev-parse", "a").strip()
    line = merged_line(repo, res["merged_tree"], "raytracer.py", EDGE_USE)
    assert new_findings(res) == [("raytracer.py", line, "F821", "Undefined name `edge_visible`")]
    (u,) = res["undefined"]
    assert u["in_b"] is True  # the head alone has it too: the rebase already carries the break
    assert res["findings"] == 1


def test_merge_that_leaves_an_import_unused_or_a_function_defined_twice(mn, tmp_path, needs_linter):
    base = (
        "import math\n\n\ndef f(x):\n    return math.sqrt(x)\n\n\ndef g(x):\n    return math.floor(x)\n\n\n"
        "def h(x):\n    return x\n"
    )
    helper = "\n\ndef clamp(x):\n    return max(0, x)\n"
    # A stops using math in f and adds clamp below f; B drops g (the other math use) and adds clamp at the end
    side_a = base.replace("return math.sqrt(x)", "return x ** 0.5").replace(
        "return x ** 0.5\n", "return x ** 0.5\n" + helper
    )
    side_b = base.replace("\n\ndef g(x):\n    return math.floor(x)\n", "") + helper
    repo = make_repo(tmp_path, {"m.py": base}, {"m.py": side_a}, {"m.py": side_b})
    res = mn.check(str(repo), "a", "b", None)
    assert res["conflicts"] == []
    tree = res["merged_tree"]
    codes = {(code, msg) for _, _, code, msg in new_findings(res)}
    assert ("F401", "`math` imported but unused") in codes
    assert any(code == "F811" and "clamp" in msg for code, msg in codes)
    assert ("m.py", merged_line(repo, tree, "m.py", "import math"), "F401") in {
        (p, ln, code) for p, ln, code, _ in new_findings(res)
    }


def test_findings_that_hold_whatever_the_hunk_resolution_are_listed_first(mn, capsys, tmp_path, needs_linter):
    # f: a conflict on its first line; B's side defines w and B's return uses it, so only hunks from A break.
    # g: conflict free; A adds a use of the local kept, B deletes it, so every resolution breaks.
    base = (
        "def f():\n    y = 0\n    p = 1\n    q = 2\n    r = 3\n    return y + p + q + r\n\n\n"
        "def g(edges):\n    kept = 1\n    s = 1\n    t = 2\n    u = 3\n    return edges + s + t + u\n"
    )
    side_a = base.replace("    y = 0\n", "    y = 1\n").replace("return edges + s", "return edges + kept + s")
    side_b = (
        base.replace("    y = 0\n", "    w = 5\n").replace("return y + p", "return w + p").replace("    kept = 1\n", "")
    )
    repo = make_repo(tmp_path, {"m.py": base}, {"m.py": side_a}, {"m.py": side_b})
    assert mn.main([str(repo), "a", "b", "--exit-zero"]) == 0
    text = capsys.readouterr().out
    first = text.index("Undefined name `kept`")
    assert first < text.index("Undefined name `w`")
    line_of = lambda i: text[text.rindex("\n", 0, i) : text.index("\n", i)]
    assert "with either hunk resolution" in line_of(first)
    assert "only with hunks taken from A" in line_of(text.index("Undefined name `w`"))


def test_the_repos_own_pixi_ruff_comes_before_a_ruff_on_path(mn, tmp_path, monkeypatch):
    on_path = tmp_path / "bin" / "ruff"
    repo_ruff = tmp_path / "repo" / ".pixi" / "envs" / "lint" / "bin" / "ruff"
    for p in (on_path, repo_ruff):
        p.parent.mkdir(parents=True)
        p.write_text("#!/bin/sh\n")
        p.chmod(0o755)
    monkeypatch.delenv("MERGE_NAMES_RUFF", raising=False)
    monkeypatch.setenv("PATH", f"{on_path.parent}:/usr/bin:/bin")
    assert mn.find_linter(str(tmp_path / "repo")) == [str(repo_ruff)]
    assert mn.find_linter(str(tmp_path / "elsewhere")) == [str(on_path)]
