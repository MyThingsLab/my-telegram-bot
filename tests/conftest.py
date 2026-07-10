from __future__ import annotations

from mythings.ledger import LedgerEntry
from mythings.testers import Tester

from mytelegrambot.authz import OPERATOR, TESTER, Principal

OPERATOR_CHAT = "chat"


def entry(tool: str, kind: str, outcome: str, detail: str, *, ts: str) -> LedgerEntry:
    return LedgerEntry(tool=tool, kind=kind, outcome=outcome, detail=detail, ts=ts)


def operator(chat_id: str = OPERATOR_CHAT) -> Principal:
    return Principal(OPERATOR, chat_id)


def as_tester(tester: Tester, chat_id: str = "999") -> Principal:
    return Principal(TESTER, chat_id, tester=tester)


def message_update(update_id: int, text: str, *, chat_id: str | int = OPERATOR_CHAT) -> dict:
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": chat_id}, "text": text},
    }


def callback_update(
    update_id: int, message_id: int, data: str, *, chat_id: str | int = OPERATOR_CHAT
) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "data": data,
            "message": {"message_id": message_id, "chat": {"id": chat_id}},
        },
    }


class FakeTransport:
    # Mocks only the Telegram HTTP boundary (send_message/fetch_updates).
    def __init__(self, *, updates: list[dict] | None = None) -> None:
        self.sent: list[tuple[str, tuple[str, str] | None]] = []
        self.sent_to: list[str | None] = []
        self.keyboards: list[tuple[tuple[str, ...], ...] | None] = []
        self.commands_set: list[tuple[tuple[str, str], ...]] = []
        self.fetched: list[tuple[int | None, float]] = []
        self._updates = updates or []
        self._next_id = 1

    def send_message(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        buttons: tuple[str, str] | None = None,
        keyboard: tuple[tuple[str, ...], ...] | None = None,
    ) -> int:
        self.sent.append((text, buttons))
        self.sent_to.append(chat_id)
        self.keyboards.append(keyboard)
        message_id = self._next_id
        self._next_id += 1
        return message_id

    def set_my_commands(self, commands: tuple[tuple[str, str], ...]) -> None:
        self.commands_set.append(commands)

    def fetch_updates(self, *, offset: int | None = None, timeout: float = 0) -> list[dict]:
        self.fetched.append((offset, timeout))
        if offset is None:
            return list(self._updates)
        return [u for u in self._updates if u["update_id"] >= offset]


class ErrorTransport:
    # Simulates any Telegram API error on send.
    def send_message(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        buttons: tuple[str, str] | None = None,
        keyboard: tuple[tuple[str, ...], ...] | None = None,
    ) -> int:
        raise RuntimeError("telegram API unreachable")

    def set_my_commands(self, commands: tuple[tuple[str, str], ...]) -> None:
        raise RuntimeError("telegram API unreachable")

    def fetch_updates(self, *, offset: int | None = None, timeout: float = 0) -> list[dict]:
        raise AssertionError("should never be reached")
