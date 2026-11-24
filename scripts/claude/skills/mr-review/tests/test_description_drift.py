"""Tests for scripts/description_drift.py (no network, no site3d checkout).

Two real cases where reviewers spent comments on a stale MR description:
  - !782 at f0f483ae9 named OMP, IAA, reweighted L1, LASSO debiasing and `keep_peaks` after they were removed,
    and said "28 tests" where the MR adds 36 (30 in test_tomography.py, 3 in test_gpu.py, 3 in test_geometry.py).
  - !786 at 25c113d6f named `_local_pointing_from_angles` (renamed to `local_pointing_from_angles`) and said
    "6 tests" where the MR adds 16 (12 in test_ephemeris.py, 4 in test_geometry.py).
The descriptions are the saved text reviewers read; the head facts were saved from the real heads by the
script's own collector (`--save-facts`). A tiny git repo built in tmp_path tests the collector itself.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SKILL = Path(__file__).resolve().parent.parent
SCRIPT = SKILL / "scripts" / "description_drift.py"
FIX = Path(__file__).resolve().parent / "fixtures" / "description_drift"


@pytest.fixture(scope="module")
def dd():
    sys.path.insert(0, str(SCRIPT.parent))
    spec = importlib.util.spec_from_file_location("description_drift", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _names(items):
    return {i["name"] for i in items}


def test_catches_782_saved_description(dd):
    desc = (FIX / "782_description.md").read_text()
    facts = dd.load_facts(FIX / "782_facts.json.gz")
    assert facts["head"].startswith("f0f483ae9")
    r = dd.check(desc, facts)
    assert "keep_peaks" in _names(r["code_names"])
    assert {"OMP", "IAA"} <= _names(r["acronyms"])
    assert {"reweighted", "debiasing"} <= _names(r["rare_words"])
    # Names the head still has are not flagged.
    flagged = _names(r["code_names"]) | _names(r["acronyms"]) | _names(r["rare_words"])
    for kept in ("tomo_fista", "select_extractor", "build_dictionary", "FISTA", "SBL", "column_window_mask"):
        assert kept not in flagged
    claims = {c["claimed"]: c for c in r["test_counts"]}
    assert claims[28]["ok"] is False
    assert claims[28]["added"] == 36
    assert facts["tests"]["added"]["image_formation/tests/test_tomography.py"].__len__() == 30
    assert any("3 GPU-only tests" in s["text"] for s in r["subset_counts"])


def test_catches_786_saved_description(dd):
    desc = (FIX / "786_description.md").read_text()
    facts = dd.load_facts(FIX / "786_facts.json.gz")
    assert facts["head"].startswith("25c113d6f")
    r = dd.check(desc, facts)
    assert "_local_pointing_from_angles" in _names(r["code_names"])
    flagged = _names(r["code_names"])
    for kept in ("compute_ray_to_earth_intersection", "relative_rtn", "save_npz", "margin_s",
                 "sar_common/orbit/formation.py", "sar_common.orbit.ephemeris"):
        assert kept not in flagged
    claims = {c["claimed"]: c for c in r["test_counts"]}
    assert claims[6]["ok"] is False
    assert claims[6]["added"] == 16
    assert len(facts["tests"]["added"]["sar_common/tests/test_ephemeris.py"]) == 12


def test_extract_kinds(dd):
    text = (
        "Adds `foo_bar()` and `pkg.mod.Thing` in `pkg/mod.py`, a `--dry-run` flag, BazQux and the "
        "`jisu/mr-topic` branch; see https://x.io/a_b/c.py. 4 tests, 2 new tests and 3 GPU-only tests."
    )
    ex = dd.extract(text)
    names = {(e["kind"], e["name"]) for e in ex["names"]}
    assert ("backtick", "foo_bar") in names
    assert ("backtick", "pkg.mod.Thing") in names
    assert ("backtick", "pkg/mod.py") in names
    assert ("backtick", "--dry-run") in names
    assert ("camel", "BazQux") in names
    assert not any("x.io" in n for _, n in names), "URLs are not names"
    assert [c["claimed"] for c in ex["test_counts"]] == [4, 2]
    assert [s["text"] for s in ex["subset_counts"]] == ["3 GPU-only tests"]


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True)


@pytest.fixture
def tiny_repo(tmp_path):
    repo = tmp_path / "repo"
    (repo / "pkg" / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    (repo / "pkg" / "core.py").write_text("def old_name(x):\n    return x\n")
    (repo / "pkg" / "config.py").write_text("OMP_NUM_THREADS = 4\n")
    (repo / "pkg" / "tests" / "test_core.py").write_text(
        "from pkg.core import old_name\n\n\ndef test_one():\n    assert old_name(1) == 1\n\n\n"
        "def test_untouched():\n    assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")
    _git(repo, "checkout", "-q", "-b", "topic")
    (repo / "pkg" / "core.py").write_text("def new_name(x):\n    return x\n")
    (repo / "pkg" / "solver.py").write_text("def tomo_sbl(a):\n    \"\"\"Sparse Bayesian learning.\"\"\"\n")
    (repo / "pkg" / "tests" / "test_core.py").write_text(
        "from pkg.core import new_name\n\n\ndef test_one():\n    assert new_name(2) == 2\n\n\n"
        "def test_untouched():\n    assert True\n\n\nclass TestSolver:\n    def test_two(self):\n"
        "        assert True\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "topic")
    return repo


def test_collector_on_tiny_repo(dd, tiny_repo):
    facts = dd.collect_facts(str(tiny_repo), "main", "topic")
    assert facts["tests"]["added"] == {"pkg/tests/test_core.py": ["TestSolver::test_two"]}
    assert facts["tests"]["touched"] == {"pkg/tests/test_core.py": ["test_one"]}
    desc = ("Related to #1\n\n- Renames `old_name` to `new_name` in `pkg/core.py`; adds OMP and SBL solvers "
            "with debiasing.\n- 2 tests. Also 5 tests.\n")
    r = dd.check(desc, facts)
    assert _names(r["code_names"]) == {"old_name"}
    # OMP is in the repo (OMP_NUM_THREADS) but not in the MR's own code; SBL is (tomo_sbl).
    assert "OMP" in _names(r["acronyms"]) and "SBL" not in _names(r["acronyms"])
    assert "debiasing" in _names(r["rare_words"])
    claims = {c["claimed"]: c["ok"] for c in r["test_counts"]}
    assert claims == {2: True, 5: False}


def test_cli_description_file_out_folder(dd, tiny_repo, tmp_path):
    desc = tmp_path / "d.md"
    desc.write_text("Uses `old_name`. 3 tests.\n")
    out = tmp_path / "out"
    rc = dd.main(["--description", str(desc), "--head", "topic", "--repo", str(tiny_repo), "--out", str(out)])
    assert rc == 0
    rep = json.loads((out / "report.json").read_text())
    assert _names(rep["code_names"]) == {"old_name"}
    assert "old_name" in (out / "report.txt").read_text()
    assert dd.main(["--description", str(desc), "--head", "topic", "--repo", str(tiny_repo), "--strict"]) == 1


def test_mr_mode_reads_gitlab_only(dd, tiny_repo, monkeypatch, tmp_path):
    head = subprocess.run(["git", "-C", str(tiny_repo), "rev-parse", "topic"], capture_output=True,
                          text=True, check=True).stdout.strip()
    _git(tiny_repo, "update-ref", "refs/remotes/origin/main", "main")
    calls = []

    def fake_api(path, *args, method=None):
        calls.append((path, args, method))
        return {"iid": 7, "sha": head, "target_branch": "main", "description": "Adds `new_name`. 3 tests.",
                "web_url": "https://gitlab.example/p/-/merge_requests/7"}

    monkeypatch.setattr(dd, "api", fake_api)
    out = tmp_path / "o"
    assert dd.main(["--mr", "7", "--project", "group/proj", "--repo", str(tiny_repo), "--out", str(out)]) == 0
    assert calls == [("projects/group%2Fproj/merge_requests/7", (), None)]
    rep = json.loads((out / "report.json").read_text())
    assert rep["code_names"] == []
    assert rep["test_counts"][0]["ok"] is False  # 3 claimed; 1 added, 1 changed


def test_skill_names_the_submit_step():
    skill = (SKILL / "SKILL.md").read_text()
    for phrase in ("submit the MR", "write the MR description", "gs branch submit", "references/mr-submit.md"):
        assert phrase in skill
    ref = (SKILL / "references" / "mr-submit.md").read_text()
    assert "description_drift.py" in ref and "Design choices and known limits" in ref


def test_merge_order_section_is_skipped(dd, tiny_repo):
    facts = dd.collect_facts(str(tiny_repo), "main", "topic")
    desc = "Adds `new_name`.\n\n## Merge order\n- !12 IPR explorer `other_branch_func`, 40 tests\n\n## Known limits\n- `gone_func`\n"
    r = dd.check(desc, facts)
    assert _names(r["code_names"]) == {"gone_func"}
    assert r["acronyms"] == [] and r["test_counts"] == []
