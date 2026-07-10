from __future__ import annotations

from dataclasses import dataclass

from mythings.ledger import Ledger
from mythings.testers import Tester, TesterStore

# Who is allowed to talk to the bot, and what it costs them.
#
# This used to be one line at the HTTP boundary: drop any update whose chat id
# isn't TELEGRAM_CHAT_ID. That made the tool single-user *by construction*, which
# was the right call while the only user was the operator. Admitting testers turns
# it into a real authorization question, so it gets its own module rather than
# hiding in `transport`.
#
# The posture is unchanged for anyone unknown: silently drop. No error reply, no
# ledger entry -- an unauthorized chat learns nothing about the bot, not even that
# it is listening.

OPERATOR = "operator"
TESTER = "tester"


@dataclass(frozen=True)
class Principal:
    kind: str
    chat_id: str
    tester: Tester | None = None

    @property
    def is_operator(self) -> bool:
        return self.kind == OPERATOR

    @property
    def label(self) -> str:
        return OPERATOR if self.is_operator else f"tester:{self.tester.handle}"


class ChatAuthorizer:
    def __init__(self, operator_chat_id: str, *, store: TesterStore | None = None) -> None:
        self._operator_chat_id = str(operator_chat_id)
        self._store = store

    def authorize(self, chat_id: str | None) -> Principal | None:
        if chat_id is None:
            return None
        if str(chat_id) == self._operator_chat_id:
            # The operator is authorized by configuration, never by the database:
            # a corrupt or absent testers db must never lock the owner out of
            # their own bot.
            return Principal(OPERATOR, str(chat_id))
        if self._store is None:
            return None
        try:
            numeric = int(chat_id)
        except ValueError:
            return None
        tester = self._store.by_chat_id(numeric)
        if tester is None:
            # Unknown, or registered-but-disabled: by_chat_id already filters
            # disabled testers, so revoking access is a single flag flip.
            return None
        return Principal(TESTER, str(chat_id), tester=tester)


def reserve_engine_call(principal: Principal, store: TesterStore | None) -> bool:
    # The operator's spend is their own; only testers are metered.
    if principal.is_operator or principal.tester is None or store is None:
        return True
    return store.reserve_engine_call(principal.tester.id)


def release_engine_call(principal: Principal, store: TesterStore | None) -> None:
    if principal.is_operator or principal.tester is None or store is None:
        return
    store.release_engine_call(principal.tester.id)


def ledger_for(principal: Principal, *, main: Ledger, store: TesterStore | None) -> Ledger:
    # A tester's activity lands in their own ledger file, so it never pollutes the
    # operator's digest, `/status`, or the notify cursor. The daemon's own
    # bookkeeping (cursor, callbacks) always goes to `main`.
    if principal.is_operator or principal.tester is None or store is None:
        return main
    return store.ledger_for(principal.tester)
