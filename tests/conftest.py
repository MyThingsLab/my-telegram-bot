from __future__ import annotations

from mythings.ledger import LedgerEntry


def entry(tool: str, kind: str, outcome: str, detail: str, *, ts: str) -> LedgerEntry:
    return LedgerEntry(tool=tool, kind=kind, outcome=outcome, detail=detail, ts=ts)


def message_update(update_id: int, text: str, *, chat_id: str | int = "chat") -> dict:
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": chat_id}, "text": text},
    }


def callback_update(
    update_id: int, message_id: int, data: str, *, chat_id: str | int = "chat"
) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": chat_id}},
        },
    }


class FakeTransport:
    # Mocks only the Telegram HTTP boundary (send_message/poll_decision/fetch_updates).
    def __init__(
        self, *, reply: str | None = "allow", updates: list[dict] | None = None
    ) -> None:
        self.reply = reply
        self.sent: list[tuple[str, tuple[str, str] | None]] = []
        self.polled: list[tuple[int, float]] = []
        self.fetched: list[tuple[int | None, float]] = []
        self._updates = updates or []
        self._next_id = 1

    def send_message(self, text: str, *, buttons: tuple[str, str] | None = None) -> int:
        self.sent.append((text, buttons))
        message_id = self._next_id
        self._next_id += 1
        return message_id

    def poll_decision(self, message_id: int, *, timeout: float) -> str | None:
        self.polled.append((message_id, timeout))
        return self.reply

    def fetch_updates(self, *, offset: int | None = None, timeout: float = 0) -> list[dict]:
        self.fetched.append((offset, timeout))
        if offset is None:
            return list(self._updates)
        return [u for u in self._updates if u["update_id"] >= offset]


class ErrorTransport:
    # Simulates any Telegram API error on send.
    def send_message(self, text: str, *, buttons: tuple[str, str] | None = None) -> int:
        raise RuntimeError("telegram API unreachable")

    def poll_decision(self, message_id: int, *, timeout: float) -> str | None:
        raise AssertionError("should never be reached")

    def fetch_updates(self, *, offset: int | None = None, timeout: float = 0) -> list[dict]:
        raise AssertionError("should never be reached")
