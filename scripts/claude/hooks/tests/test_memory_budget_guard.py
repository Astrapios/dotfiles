"""Tests for the memory index budget guard (PreToolUse on Write and Edit).

Run: uv run --no-project --with pytest --with pytest-mock pytest scripts/claude/hooks/tests
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "memory_budget_guard.py"


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location("memory_budget_guard", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def index_with(tmp_path: Path, n: int) -> Path:
    """A project memory index of n lines, `- line 0` to `- line n-1`."""
    path = tmp_path / "projects" / "-home-ubuntu-src-demo" / "memory" / "MEMORY.md"
    path.parent.mkdir(parents=True)
    path.write_text("".join(f"- line {i}\n" for i in range(n)))
    return path


def run_hook(guard, monkeypatch, capsys, event: dict) -> tuple[int, str]:
    """Run the hook on one PreToolUse event; return its exit code and stderr."""
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    code = guard.main()
    return code, capsys.readouterr().err


def edit(path: Path, old: str, new: str) -> dict:
    return {"tool_name": "Edit", "tool_input": {"file_path": str(path), "old_string": old, "new_string": new}}


def write(path: Path, n: int) -> dict:
    return {"tool_name": "Write", "tool_input": {"file_path": str(path), "content": "".join(f"- w {i}\n" for i in range(n))}}


def test_over_budget_index_refuses_a_one_line_add(guard, monkeypatch, capsys, tmp_path):
    index = index_with(tmp_path, 126)
    code, err = run_hook(guard, monkeypatch, capsys, edit(index, "- line 125\n", "- line 125\n- line new\n"))
    assert code == 2
    assert "126 to 127 lines" in err
    assert "budget 60" in err
    assert "retire or merge" in err


def test_over_budget_index_refuses_a_longer_write(guard, monkeypatch, capsys, tmp_path):
    index = index_with(tmp_path, 126)
    code, _ = run_hook(guard, monkeypatch, capsys, write(index, 127))
    assert code == 2


def test_over_budget_index_accepts_a_one_line_removal(guard, monkeypatch, capsys, tmp_path):
    index = index_with(tmp_path, 126)
    assert run_hook(guard, monkeypatch, capsys, edit(index, "- line 5\n", ""))[0] == 0
    assert run_hook(guard, monkeypatch, capsys, write(index, 125))[0] == 0


def test_under_budget_index_accepts_an_add(guard, monkeypatch, capsys, tmp_path):
    index = index_with(tmp_path, 59)
    assert run_hook(guard, monkeypatch, capsys, edit(index, "- line 58\n", "- line 58\n- line new\n"))[0] == 0


def test_add_that_crosses_the_budget_is_refused(guard, monkeypatch, capsys, tmp_path):
    index = index_with(tmp_path, 60)
    assert run_hook(guard, monkeypatch, capsys, write(index, 61))[0] == 2


def test_edit_that_keeps_the_count_passes(guard, monkeypatch, capsys, tmp_path):
    index = index_with(tmp_path, 126)
    assert run_hook(guard, monkeypatch, capsys, edit(index, "- line 7\n", "- line seven, reworded\n"))[0] == 0


def test_replace_all_counts_every_occurrence(guard, monkeypatch, capsys, tmp_path):
    index = index_with(tmp_path, 100)
    event = edit(index, "- line 1", "- line 1\n- extra")
    event["tool_input"]["replace_all"] = True   # "- line 1" also starts lines 10 to 19: 11 lines added
    _, err = run_hook(guard, monkeypatch, capsys, event)
    assert "100 to 111 lines" in err


def test_other_files_are_never_read(guard, mocker, tmp_path):
    read = mocker.patch.object(Path, "read_text", side_effect=AssertionError("read a file that is not an index"))
    for name in ("notes.md", "memory/other.md", "MEMORY.md", "memory/MEMORY.md.bak"):
        event = write(tmp_path / name, 500)
        assert guard.refusal(event) is None
    read.assert_not_called()


def test_a_new_index_over_budget_is_refused(guard, monkeypatch, capsys, tmp_path):
    index = tmp_path / "memory" / "MEMORY.md"   # does not exist yet: 0 lines before
    assert run_hook(guard, monkeypatch, capsys, write(index, 61))[0] == 2


def test_bad_input_passes(guard, monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("not json"))
    assert guard.main() == 0
