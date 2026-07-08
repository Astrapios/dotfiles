"""Tests for transcript.py — the JSONL-transcript focus source."""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
from astra import transcript


def _rec(**kw):
    return json.dumps(kw)


def _assistant(blocks, **extra):
    return {"type": "assistant", "message": {"content": blocks}, **extra}


class TestRenderRecord:
    def test_assistant_text(self):
        r = transcript.render_record(_assistant([{"type": "text", "text": "line one\nline two"}]))
        assert r == ["line one", "line two"]

    def test_assistant_tool_use(self):
        r = transcript.render_record(_assistant(
            [{"type": "tool_use", "name": "Bash", "input": {"command": "ls -la", "description": "x"}}]))
        assert r == ["🔧 Bash(ls -la)"]

    def test_tool_use_prefers_file_path(self):
        r = transcript.render_record(_assistant(
            [{"type": "tool_use", "name": "Read", "input": {"file_path": "/a/b.py"}}]))
        assert r == ["🔧 Read(/a/b.py)"]

    def test_multiline_command_single_lined(self):
        # a multi-line Bash command must collapse to one line so the
        # "🔧 Name(...)" line doesn't split and break tool-block rendering
        r = transcript.render_record(_assistant(
            [{"type": "tool_use", "name": "Bash",
              "input": {"command": "cd /foo &&\n  pytest -q &&\n  echo done"}}]))
        assert r == ["🔧 Bash(cd /foo && pytest -q && echo done)"]
        assert "\n" not in r[0]

    def test_long_summary_truncated(self):
        r = transcript.render_record(_assistant(
            [{"type": "tool_use", "name": "Bash", "input": {"command": "x" * 500}}]))
        assert len(r[0]) < 220 and r[0].endswith("…)")

    def test_thinking_skipped(self):
        r = transcript.render_record(_assistant([{"type": "thinking", "thinking": "hmm"}]))
        assert r == []

    def test_mixed_blocks(self):
        r = transcript.render_record(_assistant([
            {"type": "thinking", "thinking": "..."},
            {"type": "text", "text": "Doing it."},
            {"type": "tool_use", "name": "Edit", "input": {"file_path": "x.py"}},
        ]))
        assert r == ["Doing it.", "🔧 Edit(x.py)"]

    def test_sidechain_skipped(self):
        r = transcript.render_record(_assistant([{"type": "text", "text": "sub"}], isSidechain=True))
        assert r == []

    def test_meta_skipped(self):
        r = transcript.render_record(_assistant([{"type": "text", "text": "m"}], isMeta=True))
        assert r == []

    def test_user_prompt_skipped(self):
        assert transcript.render_record({"type": "user", "message": {"content": "hi there"}}) == []

    def test_noise_types_skipped(self):
        for t in ("mode", "attachment", "ai-title", "system", "file-history-snapshot"):
            assert transcript.render_record({"type": t, "foo": 1}) == []

    def test_tool_result_only_with_flag(self):
        rec = {"type": "user", "message": {"content": [
            {"type": "tool_result", "content": "out-1\nout-2"}]}}
        assert transcript.render_record(rec) == []
        assert transcript.render_record(rec, include_tool_results=True) == ["out-1", "out-2"]


class TestTranscriptTail:
    def _write(self, path, records):
        with open(path, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")

    def test_seed_skips_existing(self, tmp_path):
        p = tmp_path / "t.jsonl"
        self._write(p, [_assistant([{"type": "text", "text": "old"}])])
        tail = transcript.TranscriptTail(str(p))
        tail.seed()
        assert tail.poll() == []  # existing content skipped

    def test_poll_returns_appended(self, tmp_path):
        p = tmp_path / "t.jsonl"
        self._write(p, [_assistant([{"type": "text", "text": "old"}])])
        tail = transcript.TranscriptTail(str(p))
        tail.seed()
        with open(p, "a") as f:
            f.write(json.dumps(_assistant([{"type": "text", "text": "new one"}])) + "\n")
        assert tail.poll() == ["new one"]
        assert tail.poll() == []  # nothing new second time — no repeat

    def test_incremental_no_repeats(self, tmp_path):
        p = tmp_path / "t.jsonl"
        self._write(p, [])
        tail = transcript.TranscriptTail(str(p))
        tail.seed()
        for i in range(3):
            with open(p, "a") as f:
                f.write(json.dumps(_assistant([{"type": "text", "text": f"msg{i}"}])) + "\n")
            assert tail.poll() == [f"msg{i}"]

    def test_partial_line_buffered(self, tmp_path):
        p = tmp_path / "t.jsonl"
        self._write(p, [])
        tail = transcript.TranscriptTail(str(p))
        tail.seed()
        rec = json.dumps(_assistant([{"type": "text", "text": "whole"}]))
        with open(p, "a") as f:
            f.write(rec[:10])  # partial, no newline
        assert tail.poll() == []  # buffered, not parsed yet
        with open(p, "a") as f:
            f.write(rec[10:] + "\n")
        assert tail.poll() == ["whole"]

    def test_truncation_resets(self, tmp_path):
        p = tmp_path / "t.jsonl"
        self._write(p, [_assistant([{"type": "text", "text": "a"}]),
                        _assistant([{"type": "text", "text": "b"}])])
        tail = transcript.TranscriptTail(str(p))
        tail.seed()
        # file rotated/truncated to something shorter
        self._write(p, [_assistant([{"type": "text", "text": "fresh"}])])
        assert tail.poll() == ["fresh"]

    def test_missing_file(self, tmp_path):
        tail = transcript.TranscriptTail(str(tmp_path / "nope.jsonl"))
        tail.seed()
        assert tail.poll() == []


class TestResolveTranscript:
    def test_none_when_unknown(self, tmp_path, monkeypatch):
        from astra import config
        monkeypatch.setattr(config, "SIGNAL_DIR", str(tmp_path))
        assert transcript.resolve_transcript("w9z") is None

    def test_resolves_cached_path(self, tmp_path, monkeypatch):
        from astra import config, state
        monkeypatch.setattr(config, "SIGNAL_DIR", str(tmp_path))
        real = tmp_path / "sess.jsonl"
        real.write_text("{}\n")
        state._save_transcript_path("w4a", str(real))
        assert transcript.resolve_transcript("w4a") == str(real)

    def test_none_when_file_gone(self, tmp_path, monkeypatch):
        from astra import config, state
        monkeypatch.setattr(config, "SIGNAL_DIR", str(tmp_path))
        state._save_transcript_path("w4a", str(tmp_path / "gone.jsonl"))
        assert transcript.resolve_transcript("w4a") is None

    def test_resolves_bare_window_id(self, tmp_path, monkeypatch):
        # hook writes under bare "w3" (get_window_id), focus looks up full "w3a"
        from astra import config, state
        monkeypatch.setattr(config, "SIGNAL_DIR", str(tmp_path))
        real = tmp_path / "s.jsonl"
        real.write_text("{}\n")
        state._save_transcript_path("w3", str(real))
        assert transcript.resolve_transcript("w3a") == str(real)
