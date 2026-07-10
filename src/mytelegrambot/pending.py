from __future__ import annotations

from dataclasses import dataclass

from mythings.ledger import Ledger
from mythings.testers import TesterStore

# Who knocked, and was not let in.
#
# The daemon drops unauthorized chats silently -- no reply, no ack -- so the
# chat never learns the bot is listening. That is about what *they* observe.
# Recording the knock *locally* reveals nothing to them, and without it a
# prospective tester's first message vanishes: the cursor advances past it and
# Telegram never redelivers, so the operator has no way to learn their chat_id.
#
# Two bounds keep this from becoming a spam sink:
#   - deduplicated by chat_id, so one person hammering /start writes one row
#   - capped at MAX_PENDING distinct chats, so someone cycling accounts cannot
#     grow the ledger without bound. Past the cap, knocks are dropped as before.

_SELF_TOOL = "mytelegrambot"
KIND = "unknown_chat"
MAX_PENDING = 50


@dataclass(frozen=True)
class Knock:
    chat_id: str
    first_seen: str


class PendingChats:
    def __init__(self, ledger: Ledger, *, cap: int = MAX_PENDING) -> None:
        self._ledger = ledger
        self._cap = cap
        self._seen = {k.chat_id for k in knocks(ledger)}
        self._warned = False

    def record(self, chat_id: str) -> bool:
        if chat_id in self._seen:
            return False
        if len(self._seen) >= self._cap:
            if not self._warned:
                print(f"mytelegrambot: {self._cap} unknown chats recorded; ignoring further knocks")
                self._warned = True
            return False
        self._seen.add(chat_id)
        self._ledger.record(
            tool=_SELF_TOOL,
            kind=KIND,
            outcome="dropped",
            detail=f"unregistered chat {chat_id} contacted the bot",
            chat_id=chat_id,
        )
        return True


def knocks(ledger: Ledger) -> list[Knock]:
    seen: dict[str, str] = {}
    for entry in ledger.read(tool=_SELF_TOOL, kind=KIND):
        chat_id = entry.data.get("chat_id")
        if chat_id is not None and chat_id not in seen:
            seen[chat_id] = entry.ts
    return [Knock(chat_id=c, first_seen=ts) for c, ts in seen.items()]


def pending(ledger: Ledger, store: TesterStore | None) -> list[Knock]:
    # A chat stops being pending once it resolves to an *enabled* tester. A
    # disabled one reappears here, which is honest: it is not admitted either.
    if store is None:
        return knocks(ledger)
    return [k for k in knocks(ledger) if _unregistered(store, k.chat_id)]


def _unregistered(store: TesterStore, chat_id: str) -> bool:
    try:
        numeric = int(chat_id)
    except ValueError:
        return True
    return store.by_chat_id(numeric) is None
