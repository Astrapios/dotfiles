"""Tests for the context advisor hook's memory index budget line.

Run: uv run --no-project --with pytest pytest scripts/claude/hooks/tests
"""
from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path

import pytest

HOOK = Path(__file__).resolve().parent.parent / "context_advisor.py"


@pytest.fixture
def advisor():
    spec = importlib.util.spec_from_file_location("context_advisor", HOOK)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def run_hook(advisor, monkeypatch, capsys, tmp_path: Path, memory_lines: int) -> str:
    """Run the hook for a session of a project whose MEMORY.md has the given number of lines; return its stdout."""
    project = tmp_path / "projects" / "-home-ubuntu-src-demo"
    (project / "memory").mkdir(parents=True)
    (project / "memory" / "MEMORY.md").write_text("".join(f"- line {i}\n" for i in range(memory_lines)))
    event = {"session_id": "s1", "transcript_path": str(project / "s1.jsonl")}   # no transcript yet: no context note
    monkeypatch.delenv("CONTEXT_ADVISOR_OFF", raising=False)
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(event)))
    assert advisor.main() == 0
    return capsys.readouterr().out


def test_memory_over_budget_prints_the_line(advisor, monkeypatch, capsys, tmp_path):
    out = run_hook(advisor, monkeypatch, capsys, tmp_path, 61)
    context = json.loads(out)["hookSpecificOutput"]["additionalContext"]
    assert "MEMORY.md is 61 lines (budget 60): retire or merge before adding" in context


def test_memory_at_budget_prints_nothing(advisor, monkeypatch, capsys, tmp_path):
    assert run_hook(advisor, monkeypatch, capsys, tmp_path, 60) == ""
