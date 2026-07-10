from __future__ import annotations

from pathlib import Path

import pytest
from mythings.ledger import Ledger
from mythings.testers import TesterStore

from conftest import OPERATOR_CHAT, FakeTransport, message_update
from mytelegrambot.authz import ChatAuthorizer
from mytelegrambot.inbound import handle_batch
from mytelegrambot.pending import KIND, PendingChats, knocks, pending


def _authorizer(store: TesterStore | None = None) -> ChatAuthorizer:
    return ChatAuthorizer(OPERATOR_CHAT, store=store)


def test_an_unknown_chat_is_recorded_but_still_gets_nothing_back(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    result = handle_batch(
        [message_update(1, "/start", chat_id="555")],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={},
        pending=PendingChats(ledger),
    )

    assert result.dropped == 1
    # Silent to them: no reply, no spinner ack.
    assert transport.sent == []
    assert transport.answered == []
    # Visible to the operator.
    assert [k.chat_id for k in knocks(ledger)] == ["555"]


def test_repeated_knocks_from_one_chat_record_once(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    tracker = PendingChats(ledger)

    for i in range(5):
        handle_batch(
            [message_update(i, "/start", chat_id="555")],
            ledger=ledger,
            transport=FakeTransport(),
            authorizer=_authorizer(),
            routes={},
            pending=tracker,
        )

    assert len(ledger.read(kind=KIND)) == 1


def test_a_restarted_daemon_does_not_re_record_known_knocks(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    PendingChats(ledger).record("555")

    # Fresh process, same ledger: the seen-set is rebuilt from it.
    assert PendingChats(ledger).record("555") is False
    assert len(ledger.read(kind=KIND)) == 1


def test_distinct_knocks_are_capped(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # Someone cycling accounts must not be able to grow the ledger without bound.
    ledger = Ledger(tmp_path / "l.jsonl")
    tracker = PendingChats(ledger, cap=3)

    recorded = [tracker.record(str(chat)) for chat in range(10)]

    assert recorded[:3] == [True, True, True]
    assert not any(recorded[3:])
    assert len(ledger.read(kind=KIND)) == 3
    assert "ignoring further knocks" in capsys.readouterr().out


def test_the_operator_is_never_recorded_as_pending(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    handle_batch(
        [message_update(1, "/help", chat_id=OPERATOR_CHAT)],
        ledger=ledger,
        transport=FakeTransport(),
        authorizer=_authorizer(),
        routes={},
        pending=PendingChats(ledger),
    )

    assert knocks(ledger) == []


def test_a_registered_tester_stops_being_pending(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    PendingChats(ledger).record("999")
    PendingChats(ledger).record("777")

    store = TesterStore(tmp_path / "t.db")
    store.register("ada", engine_quota=5, chat_id=999)

    assert [k.chat_id for k in pending(ledger, store)] == ["777"]


def test_a_disabled_tester_shows_as_pending_again(tmp_path: Path) -> None:
    # Honest: they are not admitted either.
    ledger = Ledger(tmp_path / "l.jsonl")
    PendingChats(ledger).record("999")
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=5, chat_id=999)

    assert pending(ledger, store) == []

    store.set_enabled(tester.id, False)
    assert [k.chat_id for k in pending(ledger, store)] == ["999"]


def test_pending_without_a_store_lists_every_knock(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    PendingChats(ledger).record("999")

    assert [k.chat_id for k in pending(ledger, None)] == ["999"]


def test_a_non_numeric_chat_id_is_never_matched_to_a_tester(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    PendingChats(ledger).record("not-a-number")
    store = TesterStore(tmp_path / "t.db")

    assert [k.chat_id for k in pending(ledger, store)] == ["not-a-number"]
