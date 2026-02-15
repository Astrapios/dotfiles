"""Telegram API: send, poll, photos, keyboards."""
import mimetypes
import os
import re
import struct
import time

import requests

from tg_hook import config


def tg_send(text: str, chat_id: str = "", reply_markup: dict | None = None,
            silent: bool = False) -> int:
    """Send a message to Telegram. Returns message_id."""
    chat_id = chat_id or config.CHAT_ID
    text = text.strip()[:config.TG_MAX] or "(empty)"
    payload: dict = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    if silent:
        payload["disable_notification"] = True
    r = requests.post(
        f"https://api.telegram.org/bot{config.BOT}/sendMessage",
        json=payload,
        timeout=30,
    )
    if r.status_code == 400:
        payload_plain: dict = {"chat_id": chat_id, "text": text}
        if reply_markup is not None:
            payload_plain["reply_markup"] = reply_markup
        if silent:
            payload_plain["disable_notification"] = True
        r = requests.post(
            f"https://api.telegram.org/bot{config.BOT}/sendMessage",
            json=payload_plain,
            timeout=30,
        )
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def _send_long_message(header: str, body: str, wid: str = "",
                       reply_markup: dict | None = None, footer: str = "",
                       silent: bool = False):
    """Send a header + body as one or more Telegram messages, chunking if needed.

    Body is wrapped in ``` code blocks. Footer is appended after the closing ```.
    If the total exceeds TG_MAX, body is split across multiple messages at line
    boundaries. reply_markup is attached to the last chunk only so buttons appear
    at the bottom.
    """
    # Escape triple backticks in body to prevent breaking the code block wrapper
    body = body.replace("```", "'''")
    footer_str = f"\n{footer}" if footer else ""
    overhead = len(header) + len("```\n") + len("\n```") + len(footer_str) + 50
    chunk_size = config.TG_MAX - overhead

    if len(body) <= chunk_size:
        msg = f"{header}```\n{body}\n```{footer_str}"
        tg_send(msg, reply_markup=reply_markup, silent=silent)
        config._save_last_msg(wid, msg)
        return

    lines = body.splitlines(keepends=True)
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in lines:
        if current_len + len(line) > chunk_size and current:
            chunks.append("".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += len(line)
    if current:
        chunks.append("".join(current))

    total = len(chunks)
    for i, chunk in enumerate(chunks):
        if i == 0:
            label = f"{header}(1/{total})\n"
        else:
            label = f"(cont. {i+1}/{total})\n"
        is_last = i == total - 1
        suffix = footer_str if is_last else ""
        msg = f"{label}```\n{chunk}\n```{suffix}"
        kb = reply_markup if is_last else None
        tg_send(msg, reply_markup=kb, silent=silent)
    if chunks:
        config._save_last_msg(wid, f"{header}```\n{chunks[0]}\n```")


def _get_image_dimensions(path: str) -> tuple[int, int]:
    """Parse image dimensions from PNG, JPEG, or GIF headers. Returns (width, height) or (0, 0)."""
    try:
        with open(path, "rb") as f:
            header = f.read(32)
            if len(header) < 10:
                return (0, 0)

            # PNG: 8-byte signature, then IHDR chunk with width/height at bytes 16-23
            if header[:8] == b"\x89PNG\r\n\x1a\n":
                if len(header) >= 24:
                    w, h = struct.unpack(">II", header[16:24])
                    return (w, h)

            # GIF: "GIF87a" or "GIF89a", width/height at bytes 6-9 (little-endian)
            if header[:4] == b"GIF8":
                w, h = struct.unpack("<HH", header[6:10])
                return (w, h)

            # JPEG: starts with 0xFFD8, scan for SOF markers
            if header[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    marker_data = f.read(2)
                    if len(marker_data) < 2:
                        break
                    if marker_data[0] != 0xFF:
                        break
                    marker = marker_data[1]
                    # SOF0-SOF3 (0xC0-0xC3) contain dimensions
                    if 0xC0 <= marker <= 0xC3:
                        sof = f.read(7)
                        if len(sof) >= 7:
                            h, w = struct.unpack(">HH", sof[3:7])
                            return (w, h)
                    # Skip non-SOF segments
                    length_data = f.read(2)
                    if len(length_data) < 2:
                        break
                    length = struct.unpack(">H", length_data)[0]
                    f.seek(length - 2, 1)

    except Exception:
        pass
    return (0, 0)


def tg_send_document(path: str, caption: str = "", chat_id: str = "") -> int:
    """Send a file as a document to Telegram. Returns message_id."""
    chat_id = chat_id or config.CHAT_ID
    mime_type = mimetypes.guess_type(path)[0] or "application/octet-stream"
    with open(path, "rb") as f:
        data: dict = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
            data["parse_mode"] = "Markdown"
        r = requests.post(
            f"https://api.telegram.org/bot{config.BOT}/sendDocument",
            data=data,
            files={"document": (os.path.basename(path), f, mime_type)},
            timeout=60,
        )
        if r.status_code == 400 and caption:
            f.seek(0)
            data.pop("parse_mode", None)
            r = requests.post(
                f"https://api.telegram.org/bot{config.BOT}/sendDocument",
                data=data,
                files={"document": (os.path.basename(path), f, mime_type)},
                timeout=60,
            )
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def tg_send_photo(path: str, caption: str = "", chat_id: str = "") -> int:
    """Send a photo to Telegram. Images >1280px auto-route via sendDocument. Returns message_id."""
    w, h = _get_image_dimensions(path)
    if w > 1280 or h > 1280:
        return tg_send_document(path, caption, chat_id)
    chat_id = chat_id or config.CHAT_ID
    mime_type = mimetypes.guess_type(path)[0] or "image/png"
    with open(path, "rb") as f:
        data: dict = {"chat_id": chat_id}
        if caption:
            data["caption"] = caption[:1024]
            data["parse_mode"] = "Markdown"
        r = requests.post(
            f"https://api.telegram.org/bot{config.BOT}/sendPhoto",
            data=data,
            files={"photo": (os.path.basename(path), f, mime_type)},
            timeout=60,
        )
        if r.status_code == 400 and caption:
            f.seek(0)
            data.pop("parse_mode", None)
            r = requests.post(
                f"https://api.telegram.org/bot{config.BOT}/sendPhoto",
                data=data,
                files={"photo": (os.path.basename(path), f, mime_type)},
                timeout=60,
            )
    r.raise_for_status()
    return r.json()["result"]["message_id"]


def _build_reply_keyboard() -> dict:
    """Build a persistent ReplyKeyboardMarkup with common commands."""
    return {"keyboard": [
        [{"text": "/status"}, {"text": "/last"}, {"text": "/saved"}],
        [{"text": "/focus"}, {"text": "/interrupt"}, {"text": "/help"}],
    ], "resize_keyboard": True, "is_persistent": True}


def _build_inline_keyboard(rows: list[list[tuple[str, str]]]) -> dict:
    """Build InlineKeyboardMarkup from rows of (label, callback_data) tuples."""
    return {"inline_keyboard": [
        [{"text": label, "callback_data": data} for label, data in row]
        for row in rows
    ]}


def _answer_callback_query(callback_query_id: str, text: str = ""):
    """POST answerCallbackQuery to dismiss the button loading spinner."""
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.BOT}/answerCallbackQuery",
            json={"callback_query_id": callback_query_id, "text": text},
            timeout=10,
        )
    except Exception:
        pass


def _remove_inline_keyboard(message_id: int, chat_id: str = ""):
    """POST editMessageReplyMarkup with empty keyboard to remove buttons."""
    chat_id = chat_id or config.CHAT_ID
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.BOT}/editMessageReplyMarkup",
            json={"chat_id": chat_id, "message_id": message_id,
                  "reply_markup": {"inline_keyboard": []}},
            timeout=10,
        )
    except Exception:
        pass


def _set_bot_commands():
    """Register bot commands with Telegram so they appear in the / picker."""
    commands = [
        {"command": "status", "description": "List sessions, or show session status with wN"},
        {"command": "help", "description": "Show available commands"},
        {"command": "focus", "description": "Watch completed responses from a session"},
        {"command": "deepfocus", "description": "Stream all session output in real-time"},
        {"command": "unfocus", "description": "Stop real-time monitoring"},
        {"command": "clear", "description": "Reset transient state (prompts, busy, focus)"},
        {"command": "autofocus", "description": "Toggle auto-monitor on message send"},
        {"command": "god", "description": "Auto-accept permissions (god mode)"},
        {"command": "notification", "description": "Control which alerts buzz your phone"},
        {"command": "name", "description": "Name a session (e.g. /name w4 auth)"},
        {"command": "interrupt", "description": "Interrupt current task (Esc)"},
        {"command": "last", "description": "Re-send last message for a session"},
        {"command": "saved", "description": "Review queued messages for busy sessions"},
        {"command": "new", "description": "Start new Claude session"},
        {"command": "stop", "description": "Pause the listener"},
        {"command": "start", "description": "Resume the listener"},
        {"command": "kill", "description": "Exit a Claude session (Ctrl+C)"},
        {"command": "quit", "description": "Shut down the listener"},
    ]
    try:
        requests.post(
            f"https://api.telegram.org/bot{config.BOT}/setMyCommands",
            json={"commands": commands},
            timeout=10,
        )
    except Exception:
        pass


def tg_wait_reply(after_message_id: int, timeout: int = 300) -> str:
    """Poll for a reply after a given message_id. Returns reply text."""
    send_time = int(time.time()) - 5
    offset = 0
    deadline = time.time() + timeout if timeout > 0 else float("inf")
    while time.time() < deadline:
        try:
            r = requests.get(
                f"https://api.telegram.org/bot{config.BOT}/getUpdates",
                params={"timeout": 10, "offset": offset},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
        except Exception:
            time.sleep(2)
            continue

        for upd in data.get("result", []):
            offset = max(offset, upd["update_id"] + 1)
            msg = upd.get("message", {})
            cid = str(msg.get("chat", {}).get("id", ""))
            text = msg.get("text", "")
            msg_date = msg.get("date", 0)

            if cid == str(config.CHAT_ID) and text and msg_date >= send_time:
                return text.strip()

        time.sleep(1)

    return "(no reply - timed out)"


def _poll_updates(offset: int, timeout: int = 1) -> tuple[dict | None, int]:
    """Poll Telegram getUpdates. Returns (response_data, new_offset).
    Returns (None, offset) on error. Lets KeyboardInterrupt propagate."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{config.BOT}/getUpdates",
            params={"timeout": timeout, "offset": offset},
            timeout=timeout + 10,
        )
        r.raise_for_status()
        data = r.json()
    except KeyboardInterrupt:
        raise
    except Exception:
        return None, offset
    for upd in data.get("result", []):
        offset = max(offset, upd["update_id"] + 1)
    return data, offset


def _download_tg_photo(file_id: str, dest: str) -> str | None:
    """Download a Telegram file by file_id to dest path. Returns path or None."""
    try:
        r = requests.get(
            f"https://api.telegram.org/bot{config.BOT}/getFile",
            params={"file_id": file_id},
            timeout=30,
        )
        r.raise_for_status()
        file_path = r.json().get("result", {}).get("file_path", "")
        if not file_path:
            return None
        r2 = requests.get(
            f"https://api.telegram.org/file/bot{config.BOT}/{file_path}",
            timeout=60,
        )
        r2.raise_for_status()
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(r2.content)
        return dest
    except Exception as e:
        config._log("photo", f"Download failed: {e}")
        return None


def _extract_chat_messages(data: dict) -> list[dict]:
    """Extract messages and callback queries from our chat.

    Returns list of dicts with keys:
      - "text": str (message text or caption)
      - "photo": str | None (file_id of largest photo, if present)
      - "callback": dict | None ({"id", "data", "message_id"} for button presses)
    """
    messages = []
    for upd in data.get("result", []):
        cb = upd.get("callback_query")
        if cb:
            cb_chat = str(cb.get("message", {}).get("chat", {}).get("id", ""))
            if cb_chat == str(config.CHAT_ID):
                messages.append({
                    "text": "",
                    "photo": None,
                    "callback": {
                        "id": cb["id"],
                        "data": cb.get("data", ""),
                        "message_id": cb.get("message", {}).get("message_id", 0),
                    },
                })
            continue

        msg = upd.get("message", {})
        cid = str(msg.get("chat", {}).get("id", ""))
        if cid != str(config.CHAT_ID):
            continue
        text = msg.get("text", "")
        caption = msg.get("caption", "")

        reply_msg = msg.get("reply_to_message", {})
        reply_text = reply_msg.get("text", "") or reply_msg.get("caption", "")
        reply_wid = None
        if reply_text:
            wid_m = re.search(r"w(\d+)", reply_text)
            if wid_m:
                reply_wid = wid_m.group(1)

        photos = msg.get("photo")
        if photos:
            best = photos[-1]
            messages.append({
                "text": caption.strip(),
                "photo": best.get("file_id"),
                "callback": None,
                "reply_wid": reply_wid,
            })
        elif text:
            messages.append({"text": text.strip(), "photo": None,
                             "callback": None, "reply_wid": reply_wid})
    return messages
