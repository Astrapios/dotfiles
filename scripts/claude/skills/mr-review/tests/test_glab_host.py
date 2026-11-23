"""Every glab call in the mr-review scripts names the GitLab host, from any folder.

Outside a git checkout glab falls back to gitlab.com and answers 401 (seen twice, Nov 20 and Nov 23), and
it cannot expand `projects/:id`. These tests run each script from a folder that is not a checkout, with
subprocess.run faked for glab only (git still runs for real), and check every glab argv and env.

Run: uv run --no-project --with pytest --with pytest-mock pytest scripts/claude/skills/mr-review/tests/test_glab_host.py
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"
HOST = "gitlab.gc.arraylabs.net"
HEAD = "h" * 40
ENCODED = "projects/product%2Fsite3d/"


def load(name: str):
    sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def answer(endpoint: str) -> object:
    """Canned GitLab JSON, keyed on the endpoint path."""
    path = endpoint.split("?")[0]
    if path.endswith("/merge_requests/808"):
        return {"iid": 808, "sha": HEAD, "diff_refs": {"head_sha": HEAD, "base_sha": "b" * 40, "start_sha": "b" * 40}}
    if path.endswith("/changes"):
        return {"changes": []}
    return []  # versions, discussions, draft_notes


@pytest.fixture
def glab_calls(tmp_path, monkeypatch, mocker):
    """Run from a non-checkout folder; record (argv, env) of every glab call and answer it from `answer`."""
    monkeypatch.chdir(tmp_path)
    for var in ("GITLAB_HOST", "GIT_DIR", "GIT_WORK_TREE"):
        monkeypatch.delenv(var, raising=False)
    real_run = subprocess.run
    calls: list[tuple[list[str], dict]] = []

    def fake_run(argv, *args, **kw):
        if argv[0] != "glab":
            return real_run(argv, *args, **kw)
        env = kw.get("env")
        calls.append((list(argv), env))
        endpoint = next(a for a in argv if a.startswith("projects/"))
        return subprocess.CompletedProcess(argv, 0, stdout=json.dumps(answer(endpoint)), stderr="")

    mocker.patch("subprocess.run", side_effect=fake_run)
    return calls


def assert_named(calls, host: str = HOST) -> None:
    assert calls, "no glab call was made"
    for argv, env in calls:
        assert argv[:4] == ["glab", "api", "--hostname", host], argv
        assert env is not None and env["GITLAB_HOST"] == host, argv
        assert not any(":id" in a for a in argv), argv
        assert any(a.startswith(ENCODED) for a in argv), argv


def test_glab_common(glab_calls):
    gc = load("glab_common")
    assert gc.resolve_mr("808")["iid"] == 808
    gc.raw("projects/:id/repository/files/a.py/raw?ref=x")
    gc.paged("projects/:id/merge_requests/808/discussions")
    assert_named(glab_calls)


def test_draft_note(glab_calls, monkeypatch):
    dn = load("draft_note")
    monkeypatch.setattr(sys, "argv", ["draft_note.py", "--mr", "808", "--list"])
    dn.main()
    assert_named(glab_calls)
    assert any(a.endswith("/merge_requests/808/draft_notes") for argv, _ in glab_calls for a in argv)


def test_mr_discussions(glab_calls, monkeypatch, capsys):
    md = load("mr_discussions")
    monkeypatch.setattr(sys, "argv", ["mr_discussions.py", "808", "--json"])
    md.main()
    assert json.loads(capsys.readouterr().out) == []
    assert_named(glab_calls)


def test_mr_context(glab_calls):
    mc = load("mr_context")
    a = argparse.Namespace(no_sigs=False, changes=None, head=HEAD)
    assert mc._sigs(a, "product/site3d", 808) == {}
    assert_named(glab_calls)


def test_backfill_reviews(glab_calls):
    bf = load("backfill_reviews")
    assert bf.get("projects/product%2Fsite3d/merge_requests/808")["iid"] == 808
    assert bf.pages("projects/product%2Fsite3d/merge_requests/808/versions?x=1") == []
    assert_named(glab_calls)


def test_caller_host_wins(glab_calls, monkeypatch):
    monkeypatch.setenv("GITLAB_HOST", "gitlab.example.org")
    load("glab_common").api("projects/:id/merge_requests/808")
    assert_named(glab_calls, host="gitlab.example.org")


def test_checkout_keeps_id_for_glab(tmp_path, glab_calls):
    """Inside a checkout glab expands `:id` from the remote (another project's checkout stays itself)."""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    gc = load("glab_common")
    argv, env = gc.glab_argv("api", "projects/:id/merge_requests/808")
    assert argv == ["glab", "api", "--hostname", HOST, "projects/:id/merge_requests/808"]
    assert env["GITLAB_HOST"] == HOST
