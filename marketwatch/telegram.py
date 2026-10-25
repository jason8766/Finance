"""Send messages to the owner's Telegram chat."""

import logging
import re

import httpx

log = logging.getLogger(__name__)

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 4096


def send(token: str, chat_id: str, html: str) -> None:
    for chunk in split(html):
        _send_chunk(token, chat_id, chunk)


def _send_chunk(token: str, chat_id: str, html: str) -> None:
    payload = {
        "chat_id": chat_id,
        "text": html,
        "parse_mode": "HTML",
        "link_preview_options": {"is_disabled": True},
    }
    r = _call(token, "sendMessage", payload)
    if r.status_code == 400 and "parse" in r.text.lower():
        # Malformed markup should never cost us the message: resend as plain text.
        log.warning("Telegram rejected HTML (%s); resending as plain text", r.text[:200])
        payload.pop("parse_mode")
        payload["text"] = strip_tags(html)
        r = _call(token, "sendMessage", payload)
    if r.status_code != 200:
        raise RuntimeError(f"Telegram sendMessage failed: {r.status_code} {r.text[:300]}")


def split(text: str, limit: int = LIMIT) -> list[str]:
    """Split on blank lines (section boundaries), then lines, to stay under Telegram's limit."""
    chunks, current = [], ""
    for block in text.split("\n\n"):
        candidate = f"{current}\n\n{block}" if current else block
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            chunks.append(current)
        current = ""
        for line in block.split("\n"):
            candidate = f"{current}\n{line}" if current else line
            if len(candidate) <= limit:
                current = candidate
            else:
                if current:
                    chunks.append(current)
                current = line[:limit]
    if current:
        chunks.append(current)
    return chunks


def strip_tags(html: str) -> str:
    text = re.sub(r"<[^>]+>", "", html)
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", '"').replace("&amp;", "&")


def get_me(token: str) -> dict:
    r = _call(token, "getMe")
    if r.status_code != 200:
        raise RuntimeError(f"Telegram getMe failed: {r.status_code} {r.text[:300]}")
    return r.json()["result"]


def _call(token: str, method: str, payload: dict | None = None) -> httpx.Response:
    """POST to the Bot API. The token is part of the URL, so errors are re-raised without it."""
    try:
        return httpx.post(API.format(token=token, method=method), json=payload or {}, timeout=20)
    except httpx.HTTPError as e:
        raise RuntimeError(f"Telegram {method} request failed: {type(e).__name__}: {redact(str(e), token)}") from None


def redact(text: str, token: str) -> str:
    return text.replace(token, "<bot-token>") if token else text
