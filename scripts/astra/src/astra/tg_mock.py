"""Mock transport for Telegram API — capture, replay, REPL plumbing.

PR2 scope: pass-through capture only. Real Telegram traffic is forwarded
through `requests` as usual, but every call is also recorded to a JSONL
file (and an in-memory ring buffer). Tokens and chat IDs are redacted
at write time.

Activated via `ASTRA_MOCK=1` env var (set by `astra listen --mock` in
the CLI) which calls `activate_from_env()`. Direct activation is also
possible via `attach(client, config)` for tests.

PR3 (live signal-file toggle), PR5 (replay / harness migration) extend
this module. PR4 (REPL via Unix socket) is deferred.
"""
from __future__ import annotations

import datetime as _dt
import json
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import requests

from astra import config


# Defaults
_RING_SIZE_DEFAULT = 200
_CAPTURE_DIR_DEFAULT = "/tmp/astra_capture"


@dataclass
class MockConfig:
    """Per-MockTransport configuration."""
    # Where outgoing calls are sent: "real" forwards to actual Telegram;
    # "void" returns a synthetic success.
    sink: str = "real"
    # Where getUpdates / getFile responses come from: "real" forwards;
    # "queue" pops from `pending_updates` (set up by tests).
    source: str = "real"
    # JSONL capture path (None disables file capture; ring still works).
    capture_path: str | None = None
    # Ring buffer size (in-memory).
    ring_size: int = _RING_SIZE_DEFAULT


class FakeResponse:
    """Minimal `requests.Response`-compatible object for synthetic returns."""

    def __init__(self, status_code: int = 200, json_data: Any = None,
                 content: bytes = b""):
        self.status_code = status_code
        self._json = json_data if json_data is not None else {
            "ok": True, "result": {"message_id": 0}}
        self.content = content

    def json(self) -> Any:
        return self._json

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(
                f"{self.status_code} mock error", response=self)


def _extract_endpoint(url: str) -> str:
    """Pull the endpoint name from a Telegram URL.

    Examples:
      - https://api.telegram.org/bot<TOK>/sendMessage   → sendMessage
      - https://api.telegram.org/file/bot<TOK>/photos/x → file:photos/x
    """
    m = re.search(r"/file/bot[^/]+/(.+)$", url)
    if m:
        return f"file:{m.group(1)}"
    m = re.search(r"/bot[^/]+/([^?]+)", url)
    return m.group(1) if m else url


def _redact_url(url: str) -> str:
    """Strip bot tokens from URLs for safe logging."""
    return re.sub(r"/bot[^/]+/", "/bot<REDACTED>/", url)


def _redact_value(value: Any) -> Any:
    """Replace known chat IDs in a value with symbolic names. Leaves other
    values verbatim (per user decision: redact tokens + chat IDs only)."""
    if not value:
        return value
    s = str(value)
    main = str(config.CHAT_ID) if config.CHAT_ID else ""
    doc = str(config.DOC_CHAT_ID) if config.DOC_CHAT_ID else ""
    if main and s == main:
        return "<CHAT_ID>"
    if doc and s == doc:
        return "<DOC_CHAT_ID>"
    return value


def _redact_payload(payload: Any) -> Any:
    """Recursively redact chat IDs in nested dicts/lists; preserve text."""
    if isinstance(payload, dict):
        out: dict = {}
        for k, v in payload.items():
            if k in ("chat_id", "from_chat_id"):
                out[k] = _redact_value(v)
            else:
                out[k] = _redact_payload(v)
        return out
    if isinstance(payload, list):
        return [_redact_payload(x) for x in payload]
    return payload


def _direction_for(endpoint: str) -> str:
    """Classify an endpoint as inbound (TG → astra) or outbound (astra → TG).

    Inbound: getUpdates (poll for user messages), getFile / file: download
    (fetch user-uploaded files), setMyCommands (registration response).
    Everything else is outbound.
    """
    if endpoint in ("getUpdates", "getFile", "setMyCommands"):
        return "in"
    if endpoint.startswith("file:"):
        return "in"
    return "out"


def _summarise_files(files: dict | None) -> list[dict] | None:
    """Summarize multipart file uploads — never inline bytes."""
    if not files:
        return None
    out = []
    for field_name, file_tuple in files.items():
        if isinstance(file_tuple, tuple) and file_tuple:
            name = file_tuple[0]
            mime = file_tuple[2] if len(file_tuple) > 2 else None
        else:
            name = "?"
            mime = None
        out.append({"field": field_name, "name": name, "mime": mime})
    return out


def _build_record(seq: int, method: str, endpoint: str, kwargs: dict,
                  response, elapsed_ms: float | None) -> dict:
    """Build a JSONL record from a captured call. Always redacts."""
    payload: Any = {}
    if "json" in kwargs and kwargs["json"] is not None:
        payload = _redact_payload(kwargs["json"])
    elif "data" in kwargs and kwargs["data"] is not None:
        d = kwargs["data"]
        if isinstance(d, dict):
            payload = _redact_payload(d)
        else:
            payload = {"_raw": str(d)[:500]}
    elif "params" in kwargs and kwargs["params"] is not None:
        payload = _redact_payload(kwargs["params"])

    record: dict = {
        "ts": _dt.datetime.now(_dt.UTC).isoformat(timespec="milliseconds"),
        "seq": seq,
        "dir": _direction_for(endpoint),
        "endpoint": endpoint,
        "method": method,
        "request": payload,
    }
    files = _summarise_files(kwargs.get("files"))
    if files:
        record["files"] = files
    if response is not None:
        try:
            record["status"] = int(getattr(response, "status_code", 0)) or None
        except Exception:
            record["status"] = None
        try:
            body = response.json() if hasattr(response, "json") else None
            record["response"] = _redact_payload(body) if isinstance(body, dict) else body
        except Exception:
            record["response"] = None
    if elapsed_ms is not None:
        record["elapsed_ms"] = round(elapsed_ms, 1)
    return record


class MockTransport:
    """Transport that captures every call and optionally forwards to real Telegram.

    Implements the `requests`-style interface (`.get`, `.post`, `.request`)
    expected by `TelegramClient`. Records go to an in-memory ring buffer
    and (optionally) a JSONL file. Tokens and chat IDs are redacted.

    Default mode (sink=real, source=real, capture_path=...) is pass-through
    capture: real Telegram calls are made, every call is logged. Use this
    for live record/replay (use case 1+3).

    Test mode (sink=void, source=queue) returns synthetic responses without
    hitting the network. Use this for unit tests (use case 2; harness
    migration in PR5).
    """

    def __init__(self, config_: MockConfig | None = None,
                 real_transport=None):
        self.config = config_ if config_ is not None else MockConfig()
        self.real_transport = real_transport if real_transport is not None else requests
        self.ring: list[dict] = []
        # For source="queue" mode (tests / replay)
        self.pending_updates: list[dict] = []
        self._lock = threading.Lock()
        self._seq = 0
        self._capture_fh = None
        if self.config.capture_path:
            self._open_capture()

    # --- requests-style interface ---

    def get(self, url: str, **kwargs):
        return self._handle("GET", url, **kwargs)

    def post(self, url: str, **kwargs):
        return self._handle("POST", url, **kwargs)

    def request(self, method: str, url: str, **kwargs):
        return self._handle(method.upper(), url, **kwargs)

    # --- core dispatch ---

    def _handle(self, method: str, url: str, **kwargs):
        endpoint = _extract_endpoint(url)
        t0 = _dt.datetime.now(_dt.UTC)
        response = self._dispatch(method, endpoint, url, **kwargs)
        elapsed_ms = (_dt.datetime.now(_dt.UTC) - t0).total_seconds() * 1000.0
        try:
            self._record(method, endpoint, kwargs, response, elapsed_ms)
        except Exception:
            # Capture must never break the underlying call
            pass
        return response

    def _dispatch(self, method: str, endpoint: str, url: str, **kwargs):
        """Decide where the response comes from."""
        # getUpdates-from-queue (tests)
        if endpoint == "getUpdates" and self.config.source == "queue":
            return self._fake_get_updates()
        # void sink (tests / dry-run)
        if self.config.sink == "void":
            return FakeResponse()
        # Default: forward to real transport
        return self._forward(method, url, **kwargs)

    def _forward(self, method: str, url: str, **kwargs):
        m = method.upper()
        if m == "GET":
            return self.real_transport.get(url, **kwargs)
        if m == "POST":
            return self.real_transport.post(url, **kwargs)
        return self.real_transport.request(method, url, **kwargs)

    def _fake_get_updates(self):
        """Drain pending_updates into a Telegram-shaped response."""
        with self._lock:
            updates = list(self.pending_updates)
            self.pending_updates.clear()
        return FakeResponse(json_data={"ok": True, "result": updates})

    # --- capture ---

    def _record(self, method: str, endpoint: str, kwargs: dict,
                response, elapsed_ms: float) -> None:
        with self._lock:
            self._seq += 1
            record = _build_record(
                self._seq, method, endpoint, kwargs, response, elapsed_ms)
            self.ring.append(record)
            if len(self.ring) > self.config.ring_size:
                self.ring.pop(0)
            if self._capture_fh:
                try:
                    self._capture_fh.write(json.dumps(record) + "\n")
                    self._capture_fh.flush()
                except OSError:
                    pass

    def _open_capture(self) -> None:
        path = self.config.capture_path
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._capture_fh = open(path, "a")

    def inject_update(self, update: dict) -> None:
        """Queue an update for the next getUpdates call (source='queue')."""
        with self._lock:
            self.pending_updates.append(update)

    def close(self) -> None:
        if self._capture_fh:
            try:
                self._capture_fh.close()
            except OSError:
                pass
            self._capture_fh = None


# --- module-level activation ---


def _default_capture_path() -> str:
    """Auto-generated capture file path under /tmp/astra_capture/."""
    ts = _dt.datetime.now(_dt.UTC).strftime("%Y%m%dT%H%M%S")
    return os.path.join(_CAPTURE_DIR_DEFAULT, f"{ts}.jsonl")


def attach(client, mock_config: MockConfig | None = None) -> MockTransport:
    """Attach a MockTransport to a TelegramClient. Returns the transport.

    The transport's `real_transport` is set to whatever the client was using
    before, so pass-through forwarding continues to work.
    """
    real = client.transport
    transport = MockTransport(mock_config, real_transport=real)
    client.set_transport(transport)
    return transport


def detach(client) -> None:
    """Remove the MockTransport from a TelegramClient, restoring its prior
    real transport. Safe no-op if no mock is attached."""
    current = client.transport
    if isinstance(current, MockTransport):
        current.close()
        client.set_transport(current.real_transport)


def activate_from_env() -> MockTransport | None:
    """If `ASTRA_MOCK=1` is set, attach a pass-through-capture mock to the
    default TelegramClient. Called by `astra listen --mock` and at the
    start of `cmd_listen` if the env var is present."""
    if os.environ.get("ASTRA_MOCK") != "1":
        return None
    # Deferred import to avoid circular at module load.
    from astra import telegram
    if isinstance(telegram._default_client.transport, MockTransport):
        return telegram._default_client.transport
    cfg = MockConfig(
        sink="real",
        source="real",
        capture_path=_default_capture_path(),
    )
    return attach(telegram._default_client, cfg)


def find_latest_capture(directory: str = _CAPTURE_DIR_DEFAULT) -> str | None:
    """Return the most recently modified *.jsonl in the capture dir."""
    if not os.path.isdir(directory):
        return None
    candidates = [
        os.path.join(directory, f) for f in os.listdir(directory)
        if f.endswith(".jsonl")
    ]
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)


def read_records(path: str, limit: int | None = None) -> list[dict]:
    """Read JSONL records from `path`. Returns the last `limit` if set.

    Tolerates malformed lines by skipping them.
    """
    try:
        with open(path) as f:
            lines = f.readlines()
    except OSError:
        return []
    records = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    if limit is not None and limit > 0:
        records = records[-limit:]
    return records
