from __future__ import annotations

from mythings.ledger import LedgerEntry


def entry(tool: str, kind: str, outcome: str, detail: str, *, ts: str) -> LedgerEntry:
    return LedgerEntry(tool=tool, kind=kind, outcome=outcome, detail=detail, ts=ts)


class FakeTransport:
    # Mocks only the Telegram HTTP boundary (send_message/poll_decision).
    def __init__(self, *, reply: str | None = "allow") -> None:
        self.reply = reply
        self.sent: list[tuple[str, tuple[str, str] | None]] = []
        self.polled: list[tuple[int, float]] = []
        self._next_id = 1

    def send_message(self, text: str, *, buttons: tuple[str, str] | None = None) -> int:
        self.sent.append((text, buttons))
        message_id = self._next_id
        self._next_id += 1
        return message_id

    def poll_decision(self, message_id: int, *, timeout: float) -> str | None:
        self.polled.append((message_id, timeout))
        return self.reply


class ErrorTransport:
    # Simulates any Telegram API error on send.
    def send_message(self, text: str, *, buttons: tuple[str, str] | None = None) -> int:
        raise RuntimeError("telegram API unreachable")

    def poll_decision(self, message_id: int, *, timeout: float) -> str | None:
        raise AssertionError("should never be reached")
