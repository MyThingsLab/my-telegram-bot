from __future__ import annotations

import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Protocol

from mytelegrambot.router import InlineKeyboard

_API = "https://api.telegram.org/bot{token}/{method}"

# Telegram rejects a sendMessage whose text exceeds this with a 400. Anything
# relaying content this tool does not author -- an Engine-composed brief, a
# digest of an unbounded ledger backlog -- can cross it, so callers chunk rather
# than assume.
TELEGRAM_MAX_LEN = 4096


class TelegramTransport(Protocol):
    def send_message(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        inline: InlineKeyboard | None = None,
        keyboard: tuple[tuple[str, ...], ...] | None = None,
        markdown: bool = False,
    ) -> int: ...

    def fetch_updates(self, *, offset: int | None = None, timeout: float = 0) -> list[dict]: ...

    def set_my_commands(self, commands: tuple[tuple[str, str], ...]) -> None: ...

    def answer_callback_query(self, callback_query_id: str, *, text: str = "") -> None: ...

    def send_chat_action(self, action: str, *, chat_id: str | None = None) -> None: ...


def chunk_for_telegram(text: str, *, limit: int = TELEGRAM_MAX_LEN) -> list[str]:
    # An explored brief is the whole payload of /idea, and a digest is the whole
    # payload of notify: truncating either throws away the part the human waited
    # for. Split instead, preferring a paragraph break, then a line break, and
    # only hard-cutting a single oversized line.
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > limit:
        window = remaining[:limit]
        cut = window.rfind("\n\n")
        if cut <= 0:
            cut = window.rfind("\n")
        if cut <= 0:
            cut = limit
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


class HTTPTelegramTransport:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        # The operator's chat: where unaddressed output (notify digests, ask
        # prompts) goes. Inbound updates may now come from other chats too, so
        # this is a default recipient, not a filter -- who may talk to the bot
        # is an authorization question, answered in `authz`.
        self._chat_id = chat_id

    def _call(self, method: str, payload: dict, *, timeout: float = 10) -> dict:
        url = _API.format(token=self._token, method=method)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read())

    def send_message(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        inline: InlineKeyboard | None = None,
        keyboard: tuple[tuple[str, ...], ...] | None = None,
        markdown: bool = False,
    ) -> int:
        payload: dict = {"chat_id": chat_id or self._chat_id, "text": text}
        if markdown:
            payload["parse_mode"] = "Markdown"
        if inline is not None:
            # Per-message inline buttons: taps arrive as `callback_query` updates
            # carrying the button's callback_data. Used by `ask` (Allow/Deny) and
            # by `/idea` replies (Explore deeper / Close).
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [{"text": label, "callback_data": data} for label, data in row]
                    for row in inline
                ]
            }
        elif keyboard is not None:
            # Persistent reply keyboard: rows of shortcut buttons whose taps
            # arrive as ordinary text messages (each label is a "/command"), so
            # they route through the normal command parser. Telegram keeps it
            # shown until replaced.
            payload["reply_markup"] = {
                "keyboard": [[{"text": label} for label in row] for row in keyboard],
                "resize_keyboard": True,
                "is_persistent": True,
            }
        try:
            result = self._call("sendMessage", payload)
        except urllib.error.HTTPError as exc:
            if not markdown or exc.code != 400:
                raise
            # Most text this tool renders as Markdown was composed by an Engine,
            # not by us: a stray `*` or `_` is a malformed entity and Telegram
            # rejects the whole message. The content matters more than the
            # styling, so drop the styling rather than lose the message.
            print("mytelegrambot: markdown rejected, resending unformatted")
            payload.pop("parse_mode")
            result = self._call("sendMessage", payload)
        return result["result"]["message_id"]

    def send_chat_action(self, action: str, *, chat_id: str | None = None) -> None:
        # Drives the "typing…" indicator. Telegram clears it after ~5s or as soon
        # as a message lands, so a caller that blocks longer than that has to
        # refresh it. Purely cosmetic: a failure here must never take down a
        # command that is otherwise working.
        try:
            self._call("sendChatAction", {"chat_id": chat_id or self._chat_id, "action": action})
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"mytelegrambot: sendChatAction failed (cosmetic): {describe(exc)}")

    def set_my_commands(self, commands: tuple[tuple[str, str], ...]) -> None:
        # Registers the bot-wide command list Telegram shows as autocomplete and
        # in the ☰ menu button. One-off admin call, driven by `mytelegrambot setup`.
        self._call(
            "setMyCommands",
            {"commands": [{"command": name, "description": desc} for name, desc in commands]},
        )

    def answer_callback_query(self, callback_query_id: str, *, text: str = "") -> None:
        # Telegram spins a loading indicator on the tapped button until this is
        # called. Best-effort: a failure here is cosmetic (the spinner hangs), and
        # must never undo an action that already happened.
        try:
            self._call(
                "answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text}
            )
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"mytelegrambot: answerCallbackQuery failed (cosmetic): {describe(exc)}")

    def fetch_updates(self, *, offset: int | None = None, timeout: float = 0) -> list[dict]:
        # The *only* getUpdates caller in the tool. `poll_decision` used to be a
        # second one, racing this for the same bot-token-wide offset queue; the
        # daemon now owns the offset outright and routes callbacks by message_id,
        # so that race can no longer be expressed.
        #
        # No chat filtering here: this returns whatever Telegram sends.
        params: dict = {"timeout": timeout}
        if offset is not None:
            params["offset"] = offset
        try:
            # Telegram holds the connection open for up to `timeout` seconds
            # server-side; the client socket timeout must exceed that, or we give
            # up before Telegram ever gets to respond.
            result = self._call("getUpdates", params, timeout=timeout + 10)
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"mytelegrambot: getUpdates failed, will retry: {describe(exc)}")
            return []
        return list(result.get("result", []))


def describe(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"{exc!r} {exc.read().decode('utf-8', 'replace')}"
    return repr(exc)


def chat_id_of(update: dict) -> str | None:
    message = update.get("message") or update.get("callback_query", {}).get("message", {})
    chat_id = message.get("chat", {}).get("id")
    return None if chat_id is None else str(chat_id)


def text_from_update(update: dict) -> str | None:
    # Plain-text messages only -- a `callback_query` update has no top-level
    # "message" key, so it's never mistaken for inbound command text here.
    message = update.get("message")
    if not message or "text" not in message:
        return None
    return str(message["text"])


@dataclass(frozen=True)
class Callback:
    query_id: str
    message_id: int
    data: str


def callback_from_update(update: dict) -> Callback | None:
    # Deliberately does not judge `data`: an Allow/Deny tap and an "Explore
    # deeper" tap are the same kind of update, and deciding which is which is the
    # daemon's routing job, not the transport's parsing job.
    callback = update.get("callback_query")
    if not callback:
        return None
    message_id = callback.get("message", {}).get("message_id")
    query_id = callback.get("id")
    data = callback.get("data")
    if message_id is None or query_id is None or not data:
        return None
    return Callback(query_id=str(query_id), message_id=int(message_id), data=str(data))
