"""Fake Telegram API for simulation tests.

I/O functions (tg_send, _poll_updates, etc.) are replaced with stateful
fakes.  Pure functions (_extract_chat_messages, _build_inline_keyboard,
_build_reply_keyboard) delegate to the real implementations.

References to real functions are captured at import time so they survive
patching of the module attributes.
"""
import re

from astra import telegram as _real_tg

# Capture real implementations before any patching
_orig_extract_chat_messages = _real_tg._extract_chat_messages
_orig_build_inline_keyboard = _real_tg._build_inline_keyboard
_orig_build_reply_keyboard = _real_tg._build_reply_keyboard
_orig_send_long_message = _real_tg._send_long_message


class FakeTelegram:
    """Stateful replacement for astra.telegram I/O functions."""

    def __init__(self):
        self._next_msg_id: int = 1000
        self.sent_messages: list[dict] = []  # {msg_id, text, reply_markup, silent}
        self._pending_updates: list[dict] = []
        self._next_update_id: int = 1
        self.answered_callbacks: list[str] = []
        self.removed_keyboards: list[int] = []
        self._file_map: dict[str, str] = {}  # file_id -> local path

    # --- I/O fakes ---

    def tg_send(self, text, chat_id="", reply_markup=None, silent=False):
        msg_id = self._next_msg_id
        self._next_msg_id += 1
        self.sent_messages.append({
            "msg_id": msg_id,
            "text": text,
            "reply_markup": reply_markup,
            "silent": silent,
        })
        return msg_id

    def _send_long_message(self, header, body, wid="", reply_markup=None,
                           footer="", silent=False):
        # Delegate to captured real implementation — it calls tg_send internally,
        # which is patched to our fake.
        return _orig_send_long_message(header, body, wid,
                                       reply_markup=reply_markup,
                                       footer=footer, silent=silent)

    def _poll_updates(self, offset, timeout=1):
        if not self._pending_updates:
            return None, offset
        data = {"result": list(self._pending_updates)}
        new_offset = max(u["update_id"] for u in self._pending_updates) + 1
        self._pending_updates.clear()
        return data, new_offset

    def _answer_callback_query(self, callback_query_id, text=""):
        self.answered_callbacks.append(callback_query_id)

    def _remove_inline_keyboard(self, message_id, chat_id=""):
        self.removed_keyboards.append(message_id)

    def _download_tg_file(self, file_id, dest):
        return self._file_map.get(file_id)

    def _set_bot_commands(self):
        pass

    # --- Pure functions (delegate to captured real implementations) ---

    @staticmethod
    def _extract_chat_messages(data):
        return _orig_extract_chat_messages(data)

    @staticmethod
    def _build_inline_keyboard(rows):
        return _orig_build_inline_keyboard(rows)

    @staticmethod
    def _build_reply_keyboard():
        return _orig_build_reply_keyboard()

    # --- Test helpers ---

    def inject_text_message(self, text, chat_id="123"):
        """Queue a text message that will be returned by next _poll_updates."""
        uid = self._next_update_id
        self._next_update_id += 1
        self._pending_updates.append({
            "update_id": uid,
            "message": {
                "message_id": uid,
                "chat": {"id": chat_id},
                "text": text,
            },
        })

    def inject_reply_message(self, text, reply_to_text, chat_id="123"):
        """Queue a text message that is a reply to another message."""
        uid = self._next_update_id
        self._next_update_id += 1
        self._pending_updates.append({
            "update_id": uid,
            "message": {
                "message_id": uid,
                "chat": {"id": chat_id},
                "text": text,
                "reply_to_message": {"text": reply_to_text},
            },
        })

    def inject_callback(self, data, message_id=None, callback_id=None):
        """Queue a callback query (inline keyboard button press)."""
        uid = self._next_update_id
        self._next_update_id += 1
        cb = {
            "id": callback_id or str(uid),
            "data": data,
        }
        if message_id is not None:
            cb["message"] = {"message_id": message_id, "chat": {"id": "123"}}
        self._pending_updates.append({
            "update_id": uid,
            "callback_query": cb,
        })

    def inject_photo(self, file_id="photo_123", caption="", chat_id="123"):
        """Queue a photo message."""
        uid = self._next_update_id
        self._next_update_id += 1
        msg = {
            "message_id": uid,
            "chat": {"id": chat_id},
            "text": caption,
            "photo": [{"file_id": file_id, "width": 100, "height": 100}],
        }
        self._pending_updates.append({
            "update_id": uid,
            "message": msg,
        })

    def register_file(self, file_id, local_path):
        """Pre-register a file_id → path mapping for _download_tg_file."""
        self._file_map[file_id] = local_path

    def find_sent(self, pattern):
        """Return all sent messages whose text matches the regex pattern."""
        pat = re.compile(pattern)
        return [m for m in self.sent_messages if pat.search(m["text"])]

    def last_sent(self):
        """Return the most recently sent message, or None."""
        return self.sent_messages[-1] if self.sent_messages else None

    def clear_sent(self):
        """Clear the sent messages list."""
        self.sent_messages.clear()
