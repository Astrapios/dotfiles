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
