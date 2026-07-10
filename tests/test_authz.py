from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger
from mythings.testers import TesterStore

from conftest import as_tester, operator
from mytelegrambot.authz import (
    ChatAuthorizer,
    ledger_for,
    release_engine_call,
    reserve_engine_call,
)

OPERATOR_CHAT = "555"


def test_operator_is_authorized_without_a_store() -> None:
    auth = ChatAuthorizer(OPERATOR_CHAT)

    principal = auth.authorize(OPERATOR_CHAT)
    assert principal is not None
    assert principal.is_operator
    assert principal.label == "operator"


def test_without_a_store_every_other_chat_is_dropped() -> None:
    auth = ChatAuthorizer(OPERATOR_CHAT)

    assert auth.authorize("999") is None
    assert auth.authorize(None) is None


def test_operator_is_authorized_even_when_the_store_is_empty(tmp_path: Path) -> None:
    # A corrupt or empty testers db must never lock the owner out of their bot.
    auth = ChatAuthorizer(OPERATOR_CHAT, store=TesterStore(tmp_path / "t.db"))

    principal = auth.authorize(OPERATOR_CHAT)
    assert principal is not None
    assert principal.is_operator


def test_registered_tester_is_authorized(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=3, chat_id=999)
    auth = ChatAuthorizer(OPERATOR_CHAT, store=store)

    principal = auth.authorize("999")
    assert principal is not None
    assert not principal.is_operator
    assert principal.tester == tester
    assert principal.label == "tester:ada"


def test_disabled_tester_is_dropped(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=3, chat_id=999)
    auth = ChatAuthorizer(OPERATOR_CHAT, store=store)
    store.set_enabled(tester.id, False)

    assert auth.authorize("999") is None


def test_unknown_and_non_numeric_chats_are_dropped(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    auth = ChatAuthorizer(OPERATOR_CHAT, store=store)

    assert auth.authorize("12345") is None
    assert auth.authorize("not-a-chat-id") is None


def test_operator_is_never_metered(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    principal = operator()

    assert all(reserve_engine_call(principal, store) for _ in range(100))
    release_engine_call(principal, store)  # a no-op, must not raise


def test_tester_reservations_are_capped(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=2, chat_id=999)
    principal = as_tester(tester)

    assert reserve_engine_call(principal, store) is True
    assert reserve_engine_call(principal, store) is True
    assert reserve_engine_call(principal, store) is False

    release_engine_call(principal, store)
    assert reserve_engine_call(principal, store) is True


def test_ledger_for_isolates_testers_from_the_operator(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)
    main = Ledger(tmp_path / "main.jsonl")

    assert ledger_for(operator(), main=main, store=store).path == main.path

    tester_ledger = ledger_for(as_tester(tester), main=main, store=store)
    assert tester_ledger.path != main.path

    tester_ledger.record("myidea", "idea_filed", "success")
    assert list(main) == []
