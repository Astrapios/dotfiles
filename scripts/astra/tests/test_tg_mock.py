"""Tests for tg_mock — MockTransport, redaction, JSONL capture (PR2)."""
from __future__ import annotations

import json
import os
import re
import tempfile
from unittest.mock import MagicMock, patch

import pytest

import astra
from astra import telegram, tg_mock
from astra.tg_mock import (
    FakeResponse, MockConfig, MockTransport,
    _build_record, _extract_endpoint, _direction_for,
    _redact_payload, _redact_url, _redact_value, _summarise_files,
    attach, detach, activate_from_env, read_records, find_latest_capture,
)


# --- pure helpers ---

class TestExtractEndpoint:
    def test_send_message(self):
        url = "https://api.telegram.org/bot12345:ABC/sendMessage"
        assert _extract_endpoint(url) == "sendMessage"

    def test_get_updates_with_params(self):
        url = "https://api.telegram.org/bot12345:ABC/getUpdates"
        assert _extract_endpoint(url) == "getUpdates"

    def test_file_download(self):
        url = "https://api.telegram.org/file/bot12345:ABC/photos/file_1.jpg"
        assert _extract_endpoint(url) == "file:photos/file_1.jpg"


class TestRedactUrl:
    def test_strips_token(self):
        url = "https://api.telegram.org/bot12345:supersecret/sendMessage"
        redacted = _redact_url(url)
        assert "12345:supersecret" not in redacted
        assert "<REDACTED>" in redacted


class TestRedactValue:
    def test_main_chat_id(self):
        with patch.object(astra.config, "CHAT_ID", "999"):
            assert _redact_value("999") == "<CHAT_ID>"
            assert _redact_value(999) == "<CHAT_ID>"

    def test_doc_chat_id(self):
        with patch.object(astra.config, "CHAT_ID", "999"), \
             patch.object(astra.config, "DOC_CHAT_ID", "888"):
            assert _redact_value("888") == "<DOC_CHAT_ID>"

    def test_other_value_unchanged(self):
        with patch.object(astra.config, "CHAT_ID", "999"):
            assert _redact_value("777") == "777"
            assert _redact_value("hello") == "hello"

    def test_empty_unchanged(self):
        assert _redact_value("") == ""
        assert _redact_value(None) is None


class TestRedactPayload:
    def test_chat_id_in_payload_redacted(self):
        with patch.object(astra.config, "CHAT_ID", "999"):
            out = _redact_payload({"chat_id": "999", "text": "secret"})
        assert out["chat_id"] == "<CHAT_ID>"
        assert out["text"] == "secret"  # text NOT redacted per user decision

    def test_nested_dict_recursed(self):
        with patch.object(astra.config, "CHAT_ID", "999"):
            out = _redact_payload({
                "outer": {"chat_id": "999", "inner": {"chat_id": "999"}}
            })
        assert out["outer"]["chat_id"] == "<CHAT_ID>"
        assert out["outer"]["inner"]["chat_id"] == "<CHAT_ID>"

    def test_list_recursed(self):
        with patch.object(astra.config, "CHAT_ID", "999"):
            out = _redact_payload([{"chat_id": "999"}, {"chat_id": "999"}])
        assert out[0]["chat_id"] == "<CHAT_ID>"
        assert out[1]["chat_id"] == "<CHAT_ID>"

    def test_other_keys_unchanged(self):
        out = _redact_payload({"text": "hello world", "parse_mode": "Markdown"})
        assert out["text"] == "hello world"
        assert out["parse_mode"] == "Markdown"


class TestDirectionFor:
    def test_outbound(self):
        assert _direction_for("sendMessage") == "out"
        assert _direction_for("sendPhoto") == "out"
        assert _direction_for("answerCallbackQuery") == "out"
        assert _direction_for("editMessageReplyMarkup") == "out"

    def test_inbound(self):
        assert _direction_for("getUpdates") == "in"
        assert _direction_for("getFile") == "in"
        assert _direction_for("file:photos/x.jpg") == "in"

    def test_setMyCommands_is_inbound(self):
        # Registration is system traffic; classified as "in" so it doesn't
        # clutter outbound message counts.
        assert _direction_for("setMyCommands") == "in"


class TestSummariseFiles:
    def test_basic(self):
        files = {"photo": ("x.jpg", b"<bytes>", "image/jpeg")}
        out = _summarise_files(files)
        assert out == [{"field": "photo", "name": "x.jpg", "mime": "image/jpeg"}]

    def test_none_returns_none(self):
        assert _summarise_files(None) is None

    def test_no_bytes_leak(self):
        """Verify file bytes are never in the summary."""
        files = {"photo": ("x.jpg", b"VERY-SECRET-IMAGE-BYTES", "image/jpeg")}
        out = _summarise_files(files)
        assert "VERY-SECRET-IMAGE-BYTES" not in str(out)


# --- FakeResponse ---

class TestFakeResponse:
    def test_default_ok(self):
        r = FakeResponse()
        assert r.status_code == 200
        assert r.json()["ok"] is True
        r.raise_for_status()  # no error

    def test_custom_json(self):
        r = FakeResponse(json_data={"result": [{"update_id": 1}]})
        assert r.json()["result"][0]["update_id"] == 1

    def test_400_raises(self):
        r = FakeResponse(status_code=400)
        with pytest.raises(Exception):
            r.raise_for_status()


# --- record building ---

class TestBuildRecord:
    def test_basic_send_message(self):
        with patch.object(astra.config, "CHAT_ID", "999"):
            response = FakeResponse(
                json_data={"ok": True, "result": {"message_id": 42}})
            record = _build_record(
                seq=1, method="POST", endpoint="sendMessage",
                kwargs={"json": {"chat_id": "999", "text": "hi"}, "timeout": 30},
                response=response, elapsed_ms=87.3)
        assert record["seq"] == 1
        assert record["method"] == "POST"
        assert record["endpoint"] == "sendMessage"
        assert record["dir"] == "out"
        assert record["request"]["chat_id"] == "<CHAT_ID>"
        assert record["request"]["text"] == "hi"
        assert record["status"] == 200
        assert record["response"]["result"]["message_id"] == 42
        assert record["elapsed_ms"] == 87.3

    def test_get_with_params(self):
        record = _build_record(
            seq=1, method="GET", endpoint="getUpdates",
            kwargs={"params": {"timeout": 1, "offset": 5}},
            response=FakeResponse(json_data={"ok": True, "result": []}),
            elapsed_ms=12.0)
        assert record["request"] == {"timeout": 1, "offset": 5}
        assert record["dir"] == "in"

    def test_files_summarised(self):
        record = _build_record(
            seq=1, method="POST", endpoint="sendPhoto",
            kwargs={
                "data": {"chat_id": "999"},
                "files": {"photo": ("x.jpg", b"FAKE", "image/jpeg")},
            },
            response=FakeResponse(),
            elapsed_ms=50.0)
        assert "files" in record
        assert record["files"][0]["name"] == "x.jpg"


# --- redaction safety (the grep test mentioned in the plan) ---

class TestNoTokenLeakage:
    def test_jsonl_contains_no_bot_token(self, tmp_path):
        """A fully-captured call must not contain the bot token anywhere
        in the serialized JSON record."""
        FAKE_TOKEN = "12345:ABCDEFsuperSECRETtokenXYZ_abc"
        capture_path = str(tmp_path / "capture.jsonl")
        cfg = MockConfig(capture_path=capture_path)
        # Use a transport that returns a fake response without making real calls
        fake_transport = MagicMock()
        fake_transport.post.return_value = FakeResponse(
            json_data={"ok": True, "result": {"message_id": 1}})
        mt = MockTransport(cfg, real_transport=fake_transport)
        with patch.object(astra.config, "BOT", FAKE_TOKEN), \
             patch.object(astra.config, "CHAT_ID", "999"):
            mt.post(f"https://api.telegram.org/bot{FAKE_TOKEN}/sendMessage",
                    json={"chat_id": "999", "text": "hello"}, timeout=30)
        mt.close()
        # Read the file as raw bytes and assert the token is nowhere in it
        with open(capture_path, "rb") as f:
            raw = f.read()
        assert FAKE_TOKEN.encode() not in raw, \
            "bot token leaked into JSONL capture"
        # Also verify the structured record is sane
        records = read_records(capture_path)
        assert len(records) == 1
        assert records[0]["endpoint"] == "sendMessage"


# --- MockTransport behavior ---

class TestMockTransport:
    def test_forwards_to_real_transport_by_default(self):
        real = MagicMock()
        real.post.return_value = FakeResponse(json_data={"ok": True, "result": {"message_id": 7}})
        mt = MockTransport(MockConfig(), real_transport=real)
        r = mt.post("https://api.telegram.org/bot<TOK>/sendMessage",
                    json={"text": "hi"})
        assert r.status_code == 200
        real.post.assert_called_once()

    def test_void_sink_returns_synthetic(self):
        real = MagicMock()
        mt = MockTransport(MockConfig(sink="void"), real_transport=real)
        r = mt.post("https://api.telegram.org/bot<TOK>/sendMessage", json={})
        assert r.status_code == 200
        real.post.assert_not_called()  # no real call made

    def test_get_routes_to_transport_get(self):
        real = MagicMock()
        real.get.return_value = FakeResponse()
        mt = MockTransport(MockConfig(), real_transport=real)
        mt.get("https://api.telegram.org/bot<TOK>/getUpdates",
               params={"timeout": 1})
        real.get.assert_called_once()
        real.post.assert_not_called()

    def test_request_method_routes_correctly(self):
        real = MagicMock()
        real.get.return_value = FakeResponse()
        mt = MockTransport(MockConfig(), real_transport=real)
        mt.request("GET", "https://api.telegram.org/bot<TOK>/getUpdates")
        real.get.assert_called_once()

    def test_queue_source_drains_pending_updates(self):
        mt = MockTransport(MockConfig(source="queue"))
        mt.inject_update({"update_id": 1, "message": {"text": "hi", "chat": {"id": "999"}}})
        mt.inject_update({"update_id": 2, "message": {"text": "bye", "chat": {"id": "999"}}})
        r = mt.get("https://api.telegram.org/bot<TOK>/getUpdates")
        result = r.json()["result"]
        assert len(result) == 2
        assert result[0]["update_id"] == 1

    def test_queue_drained_after_read(self):
        mt = MockTransport(MockConfig(source="queue"))
        mt.inject_update({"update_id": 1})
        mt.get("https://api.telegram.org/bot<TOK>/getUpdates")  # drains
        r = mt.get("https://api.telegram.org/bot<TOK>/getUpdates")
        assert r.json()["result"] == []

    def test_ring_buffer_records_calls(self):
        real = MagicMock()
        real.post.return_value = FakeResponse()
        mt = MockTransport(MockConfig(), real_transport=real)
        mt.post("https://api.telegram.org/bot<TOK>/sendMessage", json={"text": "a"})
        mt.post("https://api.telegram.org/bot<TOK>/sendMessage", json={"text": "b"})
        assert len(mt.ring) == 2
        assert mt.ring[0]["endpoint"] == "sendMessage"
        assert mt.ring[1]["seq"] == 2

    def test_ring_size_bounded(self):
        real = MagicMock()
        real.post.return_value = FakeResponse()
        mt = MockTransport(MockConfig(ring_size=3), real_transport=real)
        for i in range(5):
            mt.post("https://api.telegram.org/bot<TOK>/sendMessage", json={"i": i})
        assert len(mt.ring) == 3
        # Should be the most recent 3
        assert mt.ring[0]["request"]["i"] == 2
        assert mt.ring[2]["request"]["i"] == 4

    def test_capture_to_jsonl(self, tmp_path):
        capture_path = str(tmp_path / "cap.jsonl")
        real = MagicMock()
        real.post.return_value = FakeResponse(
            json_data={"ok": True, "result": {"message_id": 1}})
        mt = MockTransport(MockConfig(capture_path=capture_path),
                           real_transport=real)
        mt.post("https://api.telegram.org/bot<TOK>/sendMessage",
                json={"text": "hi"})
        mt.close()
        records = read_records(capture_path)
        assert len(records) == 1
        assert records[0]["request"]["text"] == "hi"

    def test_capture_unaffected_by_response_error(self):
        """Even if response parsing fails, the call should succeed."""
        real = MagicMock()
        # Response has no .json() method
        broken_resp = MagicMock()
        broken_resp.status_code = 200
        broken_resp.json.side_effect = ValueError("not json")
        real.post.return_value = broken_resp
        mt = MockTransport(MockConfig(), real_transport=real)
        # Should not raise
        mt.post("https://api.telegram.org/bot<TOK>/sendMessage", json={})
        # Record exists but response field is None
        assert len(mt.ring) == 1


class TestAttachDetach:
    def test_attach_swaps_transport(self):
        client = telegram.TelegramClient()
        original = client.transport
        mt = attach(client)
        assert client.transport is mt
        assert mt.real_transport is original

    def test_detach_restores(self):
        client = telegram.TelegramClient()
        original = client.transport
        attach(client)
        detach(client)
        assert client.transport is original

    def test_detach_noop_when_no_mock(self):
        client = telegram.TelegramClient()
        original = client.transport
        detach(client)  # should not raise
        assert client.transport is original


class TestActivateFromEnv:
    def test_no_env_var_returns_none(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ASTRA_MOCK", None)
            assert activate_from_env() is None

    def test_env_var_attaches_mock(self, tmp_path):
        client = telegram._default_client
        original_transport = client.transport
        try:
            with patch.dict(os.environ, {"ASTRA_MOCK": "1"}), \
                 patch.object(tg_mock, "_CAPTURE_DIR_DEFAULT", str(tmp_path)):
                result = activate_from_env()
            assert result is not None
            assert isinstance(client.transport, MockTransport)
        finally:
            detach(client)
            client.transport = original_transport

    def test_idempotent_when_already_mocked(self, tmp_path):
        client = telegram._default_client
        original_transport = client.transport
        try:
            with patch.dict(os.environ, {"ASTRA_MOCK": "1"}), \
                 patch.object(tg_mock, "_CAPTURE_DIR_DEFAULT", str(tmp_path)):
                first = activate_from_env()
                second = activate_from_env()
            # Second call returns the same already-attached transport
            assert second is first
        finally:
            detach(client)
            client.transport = original_transport


class TestReadRecords:
    def test_reads_all(self, tmp_path):
        path = tmp_path / "x.jsonl"
        path.write_text(
            json.dumps({"seq": 1, "endpoint": "a"}) + "\n" +
            json.dumps({"seq": 2, "endpoint": "b"}) + "\n")
        records = read_records(str(path))
        assert len(records) == 2
        assert records[0]["seq"] == 1

    def test_limit_returns_tail(self, tmp_path):
        path = tmp_path / "x.jsonl"
        lines = [json.dumps({"seq": i}) for i in range(5)]
        path.write_text("\n".join(lines) + "\n")
        records = read_records(str(path), limit=2)
        assert len(records) == 2
        assert records[0]["seq"] == 3
        assert records[1]["seq"] == 4

    def test_skips_malformed(self, tmp_path):
        path = tmp_path / "x.jsonl"
        path.write_text(
            json.dumps({"seq": 1}) + "\n" +
            "not-json\n" +
            json.dumps({"seq": 2}) + "\n")
        records = read_records(str(path))
        assert len(records) == 2

    def test_missing_file_returns_empty(self):
        assert read_records("/nonexistent/path.jsonl") == []


class TestFindLatestCapture:
    def test_returns_most_recent(self, tmp_path):
        # Create files with different mtimes
        import time as _time
        (tmp_path / "old.jsonl").write_text("")
        _time.sleep(0.01)
        (tmp_path / "new.jsonl").write_text("")
        result = find_latest_capture(str(tmp_path))
        assert result.endswith("new.jsonl")

    def test_no_files_returns_none(self, tmp_path):
        assert find_latest_capture(str(tmp_path)) is None

    def test_nonexistent_dir_returns_none(self):
        assert find_latest_capture("/nonexistent") is None


# --- PR3: live toggle via signal file ---


class TestMockStateFile:
    def test_write_creates_signal_file(self, tmp_path):
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            state = tg_mock.write_mock_state(capture_path=str(tmp_path / "cap.jsonl"))
        assert state["capture_path"].endswith("cap.jsonl")
        assert state["sink"] == "real"
        assert state["source"] == "real"
        assert "started_at" in state
        assert os.path.exists(str(tmp_path / "_mock_on.json"))

    def test_write_uses_default_capture_path(self, tmp_path):
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)), \
             patch.object(tg_mock, "_CAPTURE_DIR_DEFAULT", str(tmp_path / "captures")):
            state = tg_mock.write_mock_state()
        assert state["capture_path"].startswith(str(tmp_path / "captures"))
        assert state["capture_path"].endswith(".jsonl")

    def test_read_after_write(self, tmp_path):
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            tg_mock.write_mock_state(capture_path="/tmp/x.jsonl")
            loaded = tg_mock.read_mock_state()
        assert loaded is not None
        assert loaded["capture_path"] == "/tmp/x.jsonl"

    def test_read_missing_returns_none(self, tmp_path):
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            assert tg_mock.read_mock_state() is None

    def test_read_corrupt_returns_none(self, tmp_path):
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            (tmp_path / "_mock_on.json").write_text("not-json{{{")
            assert tg_mock.read_mock_state() is None

    def test_clear_removes_file(self, tmp_path):
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            tg_mock.write_mock_state(capture_path="/tmp/x.jsonl")
            assert tg_mock.clear_mock_state() is True
            assert tg_mock.read_mock_state() is None

    def test_clear_returns_false_when_absent(self, tmp_path):
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            assert tg_mock.clear_mock_state() is False

    def test_write_is_atomic(self, tmp_path):
        """A partial write should never leave the .json file in a broken state."""
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            tg_mock.write_mock_state(capture_path="/tmp/x.jsonl")
            # The .tmp file should not linger after a successful write
            assert not os.path.exists(str(tmp_path / "_mock_on.json.tmp"))
            assert os.path.exists(str(tmp_path / "_mock_on.json"))


class TestApplyMockState:
    def _new_client(self):
        return telegram.TelegramClient()

    def test_attaches_when_state_present(self, tmp_path):
        client = self._new_client()
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            tg_mock.write_mock_state(capture_path=str(tmp_path / "cap.jsonl"))
            result = tg_mock.apply_mock_state(client)
        assert isinstance(client.transport, tg_mock.MockTransport)
        assert result is client.transport

    def test_no_state_returns_none_no_attach(self, tmp_path):
        client = self._new_client()
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            result = tg_mock.apply_mock_state(client)
        assert result is None
        assert not isinstance(client.transport, tg_mock.MockTransport)

    def test_detaches_when_state_removed(self, tmp_path):
        client = self._new_client()
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            tg_mock.write_mock_state(capture_path=str(tmp_path / "cap.jsonl"))
            tg_mock.apply_mock_state(client)
            assert isinstance(client.transport, tg_mock.MockTransport)
            tg_mock.clear_mock_state()
            result = tg_mock.apply_mock_state(client)
        assert result is None
        assert not isinstance(client.transport, tg_mock.MockTransport)

    def test_no_op_when_unchanged(self, tmp_path):
        """Repeated apply with same state should not re-attach a new transport."""
        client = self._new_client()
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            tg_mock.write_mock_state(capture_path=str(tmp_path / "cap.jsonl"))
            mt1 = tg_mock.apply_mock_state(client)
            mt2 = tg_mock.apply_mock_state(client)
        assert mt1 is mt2  # same instance — no re-attach

    def test_re_attaches_on_started_at_change(self, tmp_path):
        """If started_at changes (user toggled off + on), re-attach with fresh transport."""
        import time as _time
        client = self._new_client()
        with patch.object(astra.config, "SIGNAL_DIR", str(tmp_path)):
            tg_mock.write_mock_state(capture_path=str(tmp_path / "a.jsonl"))
            mt1 = tg_mock.apply_mock_state(client)
            # Simulate user toggling off + on. Sleep enough to guarantee a
            # distinct millisecond-resolution `started_at` — real CLI
            # toggles are seconds apart in practice.
            tg_mock.clear_mock_state()
            _time.sleep(0.01)
            tg_mock.write_mock_state(capture_path=str(tmp_path / "b.jsonl"))
            mt2 = tg_mock.apply_mock_state(client)
        assert mt1 is not mt2
        assert mt2.config.capture_path.endswith("b.jsonl")


# --- PR5: replay transcript ---


class TestReplayTranscript:
    """Tests for the human-readable transcript produced by `astra mock replay`."""

    def _run_replay(self, jsonl_path: str, capsys) -> str:
        """Invoke cmd_mock with sys.argv = ['astra', 'mock', 'replay', path]."""
        import astra.cli as cli
        import sys as _sys
        old_argv = _sys.argv
        _sys.argv = ["astra", "mock", "replay", jsonl_path]
        try:
            cli.cmd_mock()
        finally:
            _sys.argv = old_argv
        return capsys.readouterr().out

    def test_replay_basic_message(self, tmp_path, capsys):
        path = tmp_path / "cap.jsonl"
        path.write_text(json.dumps({
            "ts": "2026-05-14T22:00:00.000",
            "seq": 1,
            "dir": "out",
            "endpoint": "sendMessage",
            "method": "POST",
            "request": {"chat_id": "<CHAT_ID>", "text": "hello world"},
            "status": 200,
            "elapsed_ms": 100.0,
        }) + "\n")
        out = self._run_replay(str(path), capsys)
        assert "sendMessage" in out
        assert "hello world" in out
        assert "→" in out  # outbound arrow

    def test_replay_get_updates_with_text(self, tmp_path, capsys):
        path = tmp_path / "cap.jsonl"
        path.write_text(json.dumps({
            "ts": "2026-05-14T22:00:00.000",
            "seq": 1,
            "dir": "in",
            "endpoint": "getUpdates",
            "method": "GET",
            "request": {"timeout": 1, "offset": 0},
            "response": {"ok": True, "result": [
                {"update_id": 1, "message": {"text": "fix the bug", "chat": {"id": "<CHAT_ID>"}}}
            ]},
            "status": 200,
        }) + "\n")
        out = self._run_replay(str(path), capsys)
        assert "getUpdates" in out
        assert "fix the bug" in out
        assert "←" in out  # inbound arrow

    def test_replay_callback_update(self, tmp_path, capsys):
        path = tmp_path / "cap.jsonl"
        path.write_text(json.dumps({
            "ts": "2026-05-14T22:00:00.000",
            "seq": 1,
            "dir": "in",
            "endpoint": "getUpdates",
            "method": "GET",
            "request": {},
            "response": {"ok": True, "result": [
                {"update_id": 1, "callback_query": {"id": "cb1", "data": "perm_w0_1"}}
            ]},
            "status": 200,
        }) + "\n")
        out = self._run_replay(str(path), capsys)
        assert "callback: perm_w0_1" in out

    def test_replay_empty_getupdates(self, tmp_path, capsys):
        path = tmp_path / "cap.jsonl"
        path.write_text(json.dumps({
            "ts": "2026-05-14T22:00:00.000",
            "seq": 1,
            "dir": "in",
            "endpoint": "getUpdates",
            "method": "GET",
            "request": {"timeout": 1, "offset": 0},
            "response": {"ok": True, "result": []},
            "status": 200,
        }) + "\n")
        out = self._run_replay(str(path), capsys)
        assert "(empty)" in out

    def test_replay_send_photo(self, tmp_path, capsys):
        path = tmp_path / "cap.jsonl"
        path.write_text(json.dumps({
            "ts": "2026-05-14T22:00:00.000",
            "seq": 1,
            "dir": "out",
            "endpoint": "sendPhoto",
            "method": "POST",
            "request": {"chat_id": "<CHAT_ID>", "caption": "screenshot"},
            "files": [{"field": "photo", "name": "x.png", "mime": "image/png"}],
            "status": 200,
        }) + "\n")
        out = self._run_replay(str(path), capsys)
        assert "sendPhoto" in out
        assert "screenshot" in out
        assert "x.png" in out

    def test_replay_inline_keyboard_summary(self, tmp_path, capsys):
        path = tmp_path / "cap.jsonl"
        path.write_text(json.dumps({
            "ts": "2026-05-14T22:00:00.000",
            "seq": 1,
            "dir": "out",
            "endpoint": "sendMessage",
            "method": "POST",
            "request": {
                "chat_id": "<CHAT_ID>",
                "text": "Allow?",
                "reply_markup": {"inline_keyboard": [[
                    {"text": "✅ Allow", "callback_data": "perm_w0_1"},
                    {"text": "❌ Deny", "callback_data": "perm_w0_3"}
                ]]},
            },
            "status": 200,
        }) + "\n")
        out = self._run_replay(str(path), capsys)
        assert "Allow?" in out
        assert "kb:" in out
        assert "Allow" in out
        assert "Deny" in out

    def test_replay_header_line(self, tmp_path, capsys):
        path = tmp_path / "cap.jsonl"
        path.write_text(json.dumps({"seq": 1, "dir": "out", "endpoint": "x"}) + "\n")
        out = self._run_replay(str(path), capsys)
        assert "Replay transcript:" in out
        assert "Records: 1" in out

    def test_replay_uses_latest_when_no_path(self, tmp_path, capsys):
        """Without an explicit path, replay uses the latest capture."""
        path = tmp_path / "cap.jsonl"
        path.write_text(json.dumps({
            "seq": 1, "dir": "out", "endpoint": "sendMessage",
            "request": {"text": "hi"}, "status": 200,
        }) + "\n")
        import astra.cli as cli
        import sys as _sys
        old_argv = _sys.argv
        _sys.argv = ["astra", "mock", "replay"]
        try:
            with patch.object(tg_mock, "_CAPTURE_DIR_DEFAULT", str(tmp_path)):
                cli.cmd_mock()
            out = capsys.readouterr().out
        finally:
            _sys.argv = old_argv
        assert "hi" in out
