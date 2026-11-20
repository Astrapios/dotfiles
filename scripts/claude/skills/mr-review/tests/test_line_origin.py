"""Tests for scripts/line_origin.py on a throwaway git repo, transcripts folder and fact log.

The repo copies the shape of an MR split: the choice is made on a dev branch in September, and the MR
branch gets one port commit with author time 12:00:00 that moves the code to a new path with new
imports, so blame on the MR stops at the port.
"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "line_origin.py"

DEV_V1 = '''from radar import LookDirection


def ref_point(pos, look_direction=LookDirection.RIGHT):
    """Scene reference point."""
    return pos
'''
DEV_V2 = DEV_V1.replace("return pos", "return pos * 1.0")
PORT = '''"""Ephemeris helpers."""

from sar_common.geometry import LookDirection

LIMIT = 30.0


def ref_point(pos, look_direction=LookDirection.RIGHT):
    """Scene reference point."""
    return pos * 1.0


def other(x):
    return x
'''


def git(repo: Path, *args: str, date: str | None = None) -> str:
    env = {**os.environ}
    if date:
        env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
    cmd = ["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false"]
    return subprocess.run([*cmd, *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def commit(repo: Path, files: dict[str, str], msg: str, date: str) -> str:
    for rel, text in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", msg, date=date)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture(scope="module")
def lo():
    spec = importlib.util.spec_from_file_location("line_origin", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    repo = root / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    base = commit(repo, {"README.md": "x\n"}, "base", "2026-09-01T10:00:00-07:00")
    git(repo, "checkout", "-q", "-b", "dev")
    demo = commit(repo, {"demo/run_demo.py": "look = LookDirection.RIGHT  # hard-coded for the demo\n"},
                  "Promote the raytraced demo", "2026-09-01T11:00:00-07:00")
    a = commit(repo, {"demo/geo.py": DEV_V1}, "Promote the demo pipeline", "2026-09-08T15:01:02-07:00")
    b = commit(repo, {"demo/geo.py": DEV_V2}, "Scale the reference point", "2026-09-23T09:10:11-07:00")
    git(repo, "checkout", "-q", "main")
    git(repo, "checkout", "-q", "-b", "mr")
    port = commit(repo, {"sar_common/eph.py": PORT}, "Add ephemeris helpers", "2026-11-07T12:00:00-08:00")
    fix = commit(repo, {"sar_common/eph.py": PORT.replace("return x", "return x + 1")}, "Fix other",
                 "2026-11-18T09:00:00-08:00")
    tr = root / "projects"
    (tr / "-home-u-astra-areas-1a1c-runs-T-ab").mkdir(parents=True)
    (tr / "-home-u-astra-areas-1a1c-runs-T-ab" / "s1.jsonl").write_text(
        json.dumps({"message": {"content": "Until now the radar look in ref_point was a hidden constant, "
                                           "right-looking."}}) + "\n")
    (tr / "-home-u-src-site3d-sim").mkdir()
    (tr / "-home-u-src-site3d-sim" / "s2.jsonl").write_text(
        json.dumps({"message": {"content": f"Committed {a[:9]} Promote the demo pipeline"}}) + "\n")
    (tr / "-home-u-src-site3d-sim" / "s3.jsonl").write_text("unrelated\n")
    facts = root / "facts.md"
    facts.write_text("Format: - YYYY-MM-DD | symbols and topic words | \"Jisu's words\" | where said\n"
                     "- 2026-11-19 | ref_point, look direction | \"we will pin srp\" | desk chat N-GA\n"
                     "- 2026-11-19 | noise floor | \"use the known noise\" | card N-X\n")
    pm = root / "port_maps"
    pm.mkdir()
    (pm / "2026-11-07-split.json").write_text(json.dumps({
        "schema": 1, "made": "2026-11-07T12:00:00-08:00", "task": "T-zz",
        "source": {"branch": "dev", "tip": b},
        "commits": [{"port": port, "mr": 7, "branch": "mr", "sources": [a, b],
                     "paths": {"sar_common/eph.py": "demo/geo.py"}}]}))
    return {"repo": repo, "base": base, "demo": demo, "a": a, "b": b, "port": port, "fix": fix, "tr": tr, "facts": facts,
            "pm": pm, "root": root}


def trace(lo, w, line, **kw):
    args = dict(repo=str(w["repo"]), rev="mr", path="sar_common/eph.py", line=line, dev="main..dev",
                transcripts=[str(w["tr"])], facts=str(w["facts"]), port_maps=[], mrs={})
    args.update(kw)
    return lo.trace(**args)


RIGHT_LINE = PORT.splitlines().index("def ref_point(pos, look_direction=LookDirection.RIGHT):") + 1


def test_port_commit_by_author_time(lo, world):
    r = trace(lo, world, RIGHT_LINE)
    assert r["blame"]["sha"] == world["port"]
    assert r["port"] == {"is_port": True, "why": "author time 12:00:00", "map": None}


def test_follows_port_to_dev_line_by_function_name(lo, world):
    r = trace(lo, world, RIGHT_LINE)
    assert r["symbol"] == "ref_point"
    assert r["dev"]["introduced"]["sha"] == world["a"]
    assert [c["sha"] for c in r["dev"]["function_commits"]] == [world["b"], world["a"]]
    added = {t["token"]: t for t in r["dev"]["token_commits"] if t["first"]}
    assert added["LookDirection.RIGHT"]["first"]["sha"] == world["a"]
    # the demo hard-coded it first, in another file of the dev line
    assert added["LookDirection.RIGHT"]["first_anywhere"]["sha"] == world["demo"]
    assert "earlier elsewhere on the dev line" in lo.render(r)


def test_transcripts_by_symbol_and_commit(lo, world):
    r = trace(lo, world, RIGHT_LINE)
    hits = r["transcripts"]["ref_point"]
    assert hits["count"] == 1 and hits["files"][0]["task"] == "T-ab"
    assert "hidden constant" in hits["files"][0]["snippet"]
    sess = r["commit_sessions"]["Promote the demo pipeline"]
    assert sess["sha"] == world["a"]
    assert sess["count"] == 1 and sess["files"][0]["project"] == "-home-u-src-site3d-sim"
    assert "LookDirection.RIGHT" in r["transcripts"]  # the line's most distinctive name is searched too


def test_fact_log_read_first(lo, world):
    r = trace(lo, world, RIGHT_LINE)
    assert r["facts"] == ['- 2026-11-19 | ref_point, look direction | "we will pin srp" | desk chat N-GA']
    text = lo.render(r)
    assert text.index("Jisu said") < text.index("Blame")


def test_port_map_names_sources_and_dev_path(lo, world):
    r = trace(lo, world, RIGHT_LINE, port_maps=[str(world["pm"] / "*.json")])
    assert r["port"]["is_port"] and r["port"]["why"].startswith("port map")
    assert r["port"]["map"]["sources"] == [world["a"], world["b"]]
    assert r["dev"]["paths"] == ["demo/geo.py"]


def test_stack_map_names_the_mr(lo, world):
    r = trace(lo, world, RIGHT_LINE, mrs={7: f"{world['base']}..{world['fix']}", 9: f"{world['fix']}..{world['fix']}"})
    assert r["mr"] == 7


def test_non_port_line(lo, world):
    line = PORT.replace("return x", "return x + 1").splitlines().index("    return x + 1") + 1
    r = trace(lo, world, line)
    assert r["blame"]["sha"] == world["fix"] and r["port"]["is_port"] is False
    assert r["symbol"] == "other"


def test_module_level_line_has_no_symbol(lo, world):
    line = PORT.splitlines().index("LIMIT = 30.0") + 1
    r = trace(lo, world, line)
    assert r["symbol"] is None and "LIMIT" in [t["token"] for t in r["dev"]["token_commits"]]


def test_blank_line_after_a_function_takes_that_function(lo, world):
    line = PORT.splitlines().index("    return pos * 1.0") + 2  # the blank line after ref_point
    r = trace(lo, world, line)
    assert r["symbol"] == "ref_point" and r["symbol_how"] == "def just above a blank line"
    assert r["dev"]["introduced"]["sha"] == world["a"]


def test_module_docstring_falls_back_to_the_file_added(lo, world):
    r = trace(lo, world, 1, port_maps=[str(world["pm"] / "*.json")])  # '"""Ephemeris helpers."""'
    assert r["symbol"] is None and r["dev"]["introduced"] is None
    assert r["dev"]["file_added"]["sha"] == world["a"]
    assert "file first added" in lo.render(r)
    # without a port map the MR path has no twin on the dev line, so nothing is claimed
    assert trace(lo, world, 1)["dev"]["file_added"] is None


def test_comment_names_pick_the_function(lo, world):
    line = PORT.splitlines().index("LIMIT = 30.0") + 1
    r = trace(lo, world, line, comment="Could `ref_point()` share this with `missing_helper`?")
    assert r["symbol"] == "ref_point" and r["symbol_how"] == "named in the comment"


def test_subject_term(lo):
    assert lo.subject_term('Say "why" in the docs') == "Say"
    assert lo.subject_term("x" * 80) == "x" * 60


def test_distinctive(lo):
    assert [t for t in ["LookDirection.RIGHT", "np.linalg.norm", "config", "ray_batch", "LookDirection",
                        "threshold", "random_seed"] if lo.distinctive(t)] == [
        "LookDirection.RIGHT", "ray_batch", "LookDirection", "random_seed"]


def test_tokens(lo):
    toks = lo.line_tokens("    look_direction: LookDirection = LookDirection.RIGHT,  # default")
    assert toks[0] == "look_direction: LookDirection = LookDirection.RIGHT,"
    assert "LookDirection.RIGHT" in toks


def test_symbol_override(lo, world):
    r = trace(lo, world, 1, symbol="ref_point")
    assert r["symbol"] == "ref_point" and r["dev"]["introduced"]["sha"] == world["a"]


def test_batch_cli(world, tmp_path):
    lines = [{"label": "right", "repo": str(world["repo"]), "rev": "mr", "file": "sar_common/eph.py",
              "line": RIGHT_LINE}]
    lf = tmp_path / "lines.json"
    lf.write_text(json.dumps(lines))
    out = tmp_path / "out"
    r = subprocess.run([sys.executable, str(SCRIPT), "--batch", str(lf), "--out", str(out), "--dev", "main..dev",
                        "--transcripts", str(world["tr"]), "--facts", str(world["facts"]), "--port-map", "none"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    res = json.loads((out / "results.json").read_text())
    assert res[0]["label"] == "right" and res[0]["port"]["is_port"] and res[0]["seconds"] < 60
    assert (out / "01-right.txt").read_text().startswith("right:")
