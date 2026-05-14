"""Tests for the TelegramClient chokepoint introduced in PR1 of the
Telegram mock layer (Phase 1D of the radiant-tinkering-sphinx plan).

PR1 is a refactor-only step: all `requests.get`/`requests.post` calls in
`telegram.py` (and one in `listener.py`) now route through
`_default_client.api()` / `_default_client.file_download()`. Behaviour is
identical to v0.28.1 — these tests pin the new abstraction so PR2
(MockTransport) has a stable target.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

import astra
from astra import telegram


class TestTelegramClient:
    def test_api_constructs_telegram_url(self):
        """`api()` should hit https://api.telegram.org/bot<TOKEN>/<endpoint>."""
        client = telegram.TelegramClient()
        mock_transport = MagicMock()
        client.transport = mock_transport
        with patch.object(astra.config, "BOT", "fake-token"):
            client.api("POST", "sendMessage", json={"text": "hi"}, timeout=30)
        # Method routed to transport.post (compat with @patch("requests.post"))
        mock_transport.post.assert_called_once()
        url = mock_transport.post.call_args[0][0]
        assert url == "https://api.telegram.org/botfake-token/sendMessage"

    def test_api_routes_get_to_transport_get(self):
        client = telegram.TelegramClient()
        client.transport = MagicMock()
        with patch.object(astra.config, "BOT", "tok"):
            client.api("GET", "getUpdates", params={"timeout": 1}, timeout=10)
        client.transport.get.assert_called_once()
        client.transport.post.assert_not_called()

    def test_api_routes_post_to_transport_post(self):
        client = telegram.TelegramClient()
        client.transport = MagicMock()
        with patch.object(astra.config, "BOT", "tok"):
            client.api("POST", "sendMessage", json={"text": "x"})
        client.transport.post.assert_called_once()
        client.transport.get.assert_not_called()

    def test_api_other_methods_route_to_transport_request(self):
        """PUT / DELETE / etc. go through transport.request, not get/post."""
        client = telegram.TelegramClient()
        client.transport = MagicMock()
        with patch.object(astra.config, "BOT", "tok"):
            client.api("PUT", "someEndpoint")
        client.transport.request.assert_called_once_with(
            "PUT", "https://api.telegram.org/bottok/someEndpoint")
        client.transport.get.assert_not_called()
        client.transport.post.assert_not_called()

    def test_bot_token_override(self):
        """Explicit bot_token overrides config.BOT (used for DOC_BOT)."""
        client = telegram.TelegramClient()
        client.transport = MagicMock()
        with patch.object(astra.config, "BOT", "main-token"):
            client.api("POST", "sendPhoto", bot_token="doc-token", data={})
        url = client.transport.post.call_args[0][0]
        assert "/botdoc-token/" in url
        assert "main-token" not in url

    def test_file_download_uses_file_host(self):
        """file_download() hits /file/bot<TOKEN>/<path>, not /bot<TOKEN>/<endpoint>."""
        client = telegram.TelegramClient()
        client.transport = MagicMock()
        with patch.object(astra.config, "BOT", "tok"):
            client.file_download("photos/file_1.jpg", timeout=60)
        url = client.transport.get.call_args[0][0]
        assert url == "https://api.telegram.org/file/bottok/photos/file_1.jpg"

    def test_set_transport_swaps(self):
        client = telegram.TelegramClient()
        original = client.transport
        new = MagicMock()
        client.set_transport(new)
        assert client.transport is new
        assert client.transport is not original

    def test_default_transport_is_requests_module(self):
        """By default the transport is the `requests` module itself, so
        existing tests that @patch("requests.get") / @patch("requests.post")
        intercept the calls."""
        import requests
        client = telegram.TelegramClient()
        assert client.transport is requests

    def test_kwargs_pass_through(self):
        """timeout, json, params, etc. forwarded verbatim to transport."""
        client = telegram.TelegramClient()
        client.transport = MagicMock()
        with patch.object(astra.config, "BOT", "tok"):
            client.api("POST", "sendMessage",
                       json={"text": "x"}, timeout=42)
        kwargs = client.transport.post.call_args[1]
        assert kwargs == {"json": {"text": "x"}, "timeout": 42}


class TestDefaultClient:
    def test_default_client_exists(self):
        assert isinstance(telegram._default_client, telegram.TelegramClient)

    @patch("requests.post")
    def test_tg_send_routes_through_default_client(self, mock_post):
        """tg_send() should hit Telegram via the default client's transport.
        Verifies that @patch("requests.post") still catches the call after
        the PR1 refactor (compat with existing test patterns)."""
        mock_post.return_value = MagicMock(
            status_code=200,
            json=lambda: {"result": {"message_id": 42}},
            raise_for_status=lambda: None,
        )
        with patch.object(astra.config, "BOT", "tok"), \
             patch.object(astra.config, "CHAT_ID", "123"):
            mid = telegram.tg_send("hi")
        assert mid == 42
        mock_post.assert_called()
        url = mock_post.call_args[0][0]
        assert url == "https://api.telegram.org/bottok/sendMessage"
