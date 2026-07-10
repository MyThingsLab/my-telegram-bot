from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger

from conftest import FakeTransport, callback_update, message_update
from mytelegrambot.inbound import last_seen_update_id, poll_once


def _ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.jsonl")


def test_last_seen_update_id_is_none_on_an_empty_ledger(tmp_path: Path) -> None:
    assert last_seen_update_id(_ledger(tmp_path)) is None


def test_last_seen_update_id_is_the_max_across_poll_entries(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.record(tool="mytelegrambot", kind="poll", outcome="success", last_update_id=5)
    ledger.record(tool="mytelegrambot", kind="poll", outcome="success", last_update_id=12)
    ledger.record(tool="mytelegrambot", kind="poll", outcome="skipped", detail="no updates")

    assert last_seen_update_id(ledger) == 12


def test_poll_once_routes_a_command_and_sends_the_reply(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    transport = FakeTransport(updates=[message_update(1, "/idea a new tool")])
    routes = {"idea": lambda args: f"filed: {args}"}

    result = poll_once(ledger=ledger, transport=transport, routes=routes)

    assert result.outcome == "success"
    assert result.updates_fetched == 1
    assert result.commands_routed == 1
    assert transport.sent == [("filed: a new tool", None)]

    written = [e for e in ledger if e.kind == "poll"][0]
    assert written.data["last_update_id"] == 1


def test_poll_once_skips_and_leaves_the_cursor_when_no_updates(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    transport = FakeTransport(updates=[])

    result = poll_once(ledger=ledger, transport=transport, routes={})

    assert result.outcome == "skipped"
    written = [e for e in ledger if e.kind == "poll"][0]
    assert "last_update_id" not in written.data


def test_poll_once_second_call_only_fetches_since_the_last_cursor(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    transport = FakeTransport(updates=[message_update(1, "/idea one")])
    poll_once(ledger=ledger, transport=transport, routes={"idea": lambda a: "ok"})

    transport._updates = [message_update(1, "/idea one"), message_update(2, "/idea two")]
    poll_once(ledger=ledger, transport=transport, routes={"idea": lambda a: "ok"})

    # Second fetch is offset by the cursor, so only update_id 2 is new.
    assert transport.fetched[1][0] == 2


def test_poll_once_ignores_plain_chat_with_no_leading_slash(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    transport = FakeTransport(updates=[message_update(1, "just saying hi")])

    result = poll_once(ledger=ledger, transport=transport, routes={"idea": lambda a: "ok"})

    assert result.commands_routed == 0
    assert transport.sent == []
    written = [e for e in ledger if e.kind == "poll"][0]
    assert written.data["last_update_id"] == 1


def test_poll_once_ignores_callback_query_updates_but_still_advances_cursor(
    tmp_path: Path,
) -> None:
    # A callback_query belongs to ask_human's own poll_decision, not this
    # router -- it must never be dispatched, but the cursor still advances
    # past it (a known, accepted race with a concurrent ask_human, documented
    # in CLAUDE.md).
    ledger = _ledger(tmp_path)
    transport = FakeTransport(updates=[callback_update(1, message_id=9, data="allow")])

    result = poll_once(ledger=ledger, transport=transport, routes={"idea": lambda a: "ok"})

    assert result.commands_routed == 0
    assert transport.sent == []
    written = [e for e in ledger if e.kind == "poll"][0]
    assert written.data["last_update_id"] == 1


def test_poll_once_reply_send_failure_does_not_crash_or_lose_the_cursor(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    updates = [message_update(1, "/idea one")]

    class FlakyReply(FakeTransport):
        def send_message(self, text: str, *, buttons=None) -> int:  # type: ignore[override]
            raise RuntimeError("telegram outage")

    transport = FlakyReply(updates=updates)

    result = poll_once(ledger=ledger, transport=transport, routes={"idea": lambda a: "ok"})

    assert result.outcome == "success"
    assert result.commands_routed == 1
    written = [e for e in ledger if e.kind == "poll"][0]
    assert written.data["last_update_id"] == 1


def test_poll_once_handler_exception_does_not_crash_the_poll(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    transport = FakeTransport(updates=[message_update(1, "/idea boom")])

    def _boom(args: str) -> str:
        raise ValueError("boom")

    result = poll_once(ledger=ledger, transport=transport, routes={"idea": _boom})

    assert result.outcome == "success"
    assert result.commands_routed == 1
    assert "went wrong" in transport.sent[0][0]
