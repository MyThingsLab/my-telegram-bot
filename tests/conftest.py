from __future__ import annotations

from mythings.ledger import LedgerEntry
from mythings.testers import Tester

from mytelegrambot.authz import OPERATOR, TESTER, Principal
from mytelegrambot.router import InlineKeyboard, Replies, Reply, as_replies

OPERATOR_CHAT = "chat"


def replies(result: Replies) -> list[Reply]:
    # Handlers stream: /idea acknowledges the filed issue, then sends the brief.
    # Most tests care about one of those, so drain the stream once and index.
    return list(as_replies(result))


def all_text(result: Replies) -> str:
    # For assertions that only ask "did the human end up being told X", without
    # caring which of the streamed messages carried it.
    return "\n".join(reply.text for reply in replies(result))


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
    update_id: int,
    message_id: int,
    data: str,
    *,
    chat_id: str | int = OPERATOR_CHAT,
    query_id: str = "q1",
    message_text: str = "Action: pr-merge\n  number: 12",
) -> dict:
    return {
        "update_id": update_id,
        "callback_query": {
            "id": query_id,
            "data": data,
            "message": {
                "message_id": message_id,
                "chat": {"id": chat_id},
                "text": message_text,
            },
        },
    }


class FakeTransport:
    # Mocks only the Telegram HTTP boundary
    # (send_message/fetch_updates/answer_callback_query).
    def __init__(self, *, updates: list[dict] | None = None) -> None:
        self.sent: list[tuple[str, InlineKeyboard | None]] = []
        self.sent_to: list[str | None] = []
        self.keyboards: list[tuple[tuple[str, ...], ...] | None] = []
        self.commands_set: list[tuple[tuple[str, str], ...]] = []
        self.fetched: list[tuple[int | None, float]] = []
        self.answered: list[str] = []
        self.markdown: list[bool] = []
        self.actions: list[tuple[str, str | None]] = []
        self.cleared: list[int] = []
        self.edits: list[tuple[int, str]] = []
        self.answers: list[tuple[str, str]] = []
        self.alerts: list[bool] = []
        self._updates = updates or []
        self._next_id = 1

    def send_message(
        self,
        text: str,
        *,
        chat_id: str | None = None,
        inline: InlineKeyboard | None = None,
        keyboard: tuple[tuple[str, ...], ...] | None = None,
        markdown: bool = False,
    ) -> int:
        self.sent.append((text, inline))
        self.sent_to.append(chat_id)
        self.keyboards.append(keyboard)
        self.markdown.append(markdown)
        message_id = self._next_id
        self._next_id += 1
        return message_id

    def send_chat_action(self, action: str, *, chat_id: str | None = None) -> None:
        self.actions.append((action, chat_id))

    def clear_inline_keyboard(self, message_id: int, *, chat_id: str | None = None) -> None:
        self.cleared.append(message_id)

    def edit_message_text(self, message_id: int, text: str, *, chat_id: str | None = None) -> None:
        self.edits.append((message_id, text))

    def set_my_commands(self, commands: tuple[tuple[str, str], ...]) -> None:
        self.commands_set.append(commands)

    def answer_callback_query(
        self, callback_query_id: str, *, text: str = "", alert: bool = False
    ) -> None:
        self.answered.append(callback_query_id)
        self.answers.append((callback_query_id, text))
        self.alerts.append(alert)

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
        inline: InlineKeyboard | None = None,
        keyboard: tuple[tuple[str, ...], ...] | None = None,
        markdown: bool = False,
    ) -> int:
        raise RuntimeError("telegram API unreachable")

    def send_chat_action(self, action: str, *, chat_id: str | None = None) -> None:
        raise RuntimeError("telegram API unreachable")

    def clear_inline_keyboard(self, message_id: int, *, chat_id: str | None = None) -> None:
        raise RuntimeError("telegram API unreachable")

    def edit_message_text(self, message_id: int, text: str, *, chat_id: str | None = None) -> None:
        raise RuntimeError("telegram API unreachable")

    def set_my_commands(self, commands: tuple[tuple[str, str], ...]) -> None:
        raise RuntimeError("telegram API unreachable")

    def answer_callback_query(
        self, callback_query_id: str, *, text: str = "", alert: bool = False
    ) -> None:
        raise RuntimeError("telegram API unreachable")

    def fetch_updates(self, *, offset: int | None = None, timeout: float = 0) -> list[dict]:
        raise AssertionError("should never be reached")
