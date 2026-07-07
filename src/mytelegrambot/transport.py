from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Protocol

_API = "https://api.telegram.org/bot{token}/{method}"


class TelegramTransport(Protocol):
    def send_message(self, text: str, *, buttons: tuple[str, str] | None = None) -> int: ...

    def poll_decision(self, message_id: int, *, timeout: float) -> str | None: ...


class HTTPTelegramTransport:
    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._token = bot_token
        self._chat_id = chat_id

    def _call(self, method: str, payload: dict, *, timeout: float = 10) -> dict:
        url = _API.format(token=self._token, method=method)
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url, data=data, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read())

    def send_message(self, text: str, *, buttons: tuple[str, str] | None = None) -> int:
        payload: dict = {"chat_id": self._chat_id, "text": text}
        if buttons is not None:
            allow, deny = buttons
            payload["reply_markup"] = {
                "inline_keyboard": [
                    [
                        {"text": allow, "callback_data": "allow"},
                        {"text": deny, "callback_data": "deny"},
                    ]
                ]
            }
        result = self._call("sendMessage", payload)
        return result["result"]["message_id"]

    def poll_decision(self, message_id: int, *, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        offset: int | None = None
        while time.monotonic() < deadline:
            long_poll = max(1, min(30, int(deadline - time.monotonic())))
            params = {"timeout": long_poll}
            if offset is not None:
                params["offset"] = offset
            try:
                # Telegram holds the connection open for up to long_poll seconds
                # server-side; the client socket timeout must exceed that or we
                # give up before Telegram ever gets to respond.
                result = self._call("getUpdates", params, timeout=long_poll + 10)
            except (urllib.error.URLError, TimeoutError, OSError) as exc:
                print(f"mytelegrambot: getUpdates failed, failing closed: {describe(exc)}")
                return None
            for update in result.get("result", []):
                offset = update["update_id"] + 1
                decision = _decision_from_update(update, message_id)
                if decision is not None:
                    return decision
        return None


def describe(exc: Exception) -> str:
    if isinstance(exc, urllib.error.HTTPError):
        return f"{exc!r} {exc.read().decode('utf-8', 'replace')}"
    return repr(exc)


def _decision_from_update(update: dict, message_id: int) -> str | None:
    callback = update.get("callback_query")
    if not callback:
        return None
    if callback.get("message", {}).get("message_id") != message_id:
        return None
    data = callback.get("data")
    return data if data in ("allow", "deny") else None
