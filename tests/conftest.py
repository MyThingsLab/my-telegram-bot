from __future__ import annotations

from mythings.ledger import LedgerEntry
from mythings.testers import Tester
from mythings.testing import FakeTransport as FakeTransport

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
        reply_to_message_id: int | None = None,
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
