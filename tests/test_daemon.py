from __future__ import annotations

import threading
from pathlib import Path

from mythings.ledger import Ledger
from mythings.policy import Action
from mythings.testers import TesterStore

from conftest import OPERATOR_CHAT, FakeTransport, callback_update, message_update
from mytelegrambot.authz import ChatAuthorizer, Principal
from mytelegrambot.inbound import handle_batch, last_seen_update_id, run_forever
from mytelegrambot.policy import ask_human, await_decision


def _echo(text: str, principal: Principal) -> str:
    return f"{principal.label} said {text}"


def _authorizer(store: TesterStore | None = None) -> ChatAuthorizer:
    return ChatAuthorizer(OPERATOR_CHAT, store=store)


def test_text_command_is_routed_and_replied_to_its_own_chat(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    result = handle_batch(
        [message_update(1, "/echo hi")],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
    )

    assert result.commands_routed == 1
    assert transport.sent[0][0] == "operator said hi"
    assert transport.sent_to == [OPERATOR_CHAT]


def test_tester_reply_goes_to_the_testers_chat_not_the_operators(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    store.register("ada", engine_quota=1, chat_id=999)
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    handle_batch(
        [message_update(1, "/echo hi", chat_id=999)],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(store),
        routes={"echo": _echo},
    )

    assert transport.sent[0][0] == "tester:ada said hi"
    assert transport.sent_to == ["999"]


def test_unauthorized_chat_is_dropped_silently(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    result = handle_batch(
        [message_update(1, "/echo hi", chat_id="intruder")],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
    )

    assert result.dropped == 1
    assert result.commands_routed == 0
    assert transport.sent == []
    assert list(ledger) == []  # not even a ledger entry: it learns nothing


def test_operator_callback_is_recorded_for_a_waiting_ask(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    result = handle_batch(
        [callback_update(1, message_id=77, data="allow")],
        ledger=ledger,
        transport=FakeTransport(),
        authorizer=_authorizer(),
        routes={},
    )

    assert result.callbacks_delivered == 1
    assert await_decision(ledger, 77, timeout=0) == "allow"


def test_a_testers_callback_can_never_resolve_the_operators_ask(tmp_path: Path) -> None:
    # An ask prompt only ever goes to the operator's chat. A callback from a
    # tester's chat carrying the same message_id must not answer it.
    store = TesterStore(tmp_path / "t.db")
    store.register("mallory", engine_quota=1, chat_id=999)
    ledger = Ledger(tmp_path / "l.jsonl")

    result = handle_batch(
        [callback_update(1, message_id=77, data="allow", chat_id=999)],
        ledger=ledger,
        transport=FakeTransport(),
        authorizer=_authorizer(store),
        routes={},
    )

    assert result.callbacks_delivered == 0
    assert result.dropped == 1
    assert await_decision(ledger, 77, timeout=0) is None


def test_non_text_update_is_ignored_without_a_reply(tmp_path: Path) -> None:
    # e.g. an edited_message, a photo, a sticker: authorized, but nothing to route.
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    result = handle_batch(
        [{"update_id": 1, "message": {"chat": {"id": OPERATOR_CHAT}}}],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
    )

    assert result.commands_routed == 0
    assert result.dropped == 0
    assert transport.sent == []


def test_unregistered_command_gets_no_reply(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    result = handle_batch(
        [message_update(1, "/nosuchcommand")],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
    )

    assert result.commands_routed == 0
    assert transport.sent == []


def test_a_failed_reply_send_still_advances_the_cursor(tmp_path: Path) -> None:
    # The command's side effects already happened inside dispatch(). Reprocessing
    # the update to retry the reply would refile the idea, so the cursor must
    # advance anyway.
    ledger = Ledger(tmp_path / "l.jsonl")

    class _SendFails(FakeTransport):
        def send_message(self, text: str, **kwargs: object) -> int:
            raise RuntimeError("telegram unreachable")

    transport = _SendFails(updates=[message_update(7, "/echo hi")])

    run_forever(
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
        should_continue=lambda: len(transport.fetched) < 1,
    )

    assert last_seen_update_id(ledger) == 7


def test_handler_exception_does_not_kill_the_batch(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    def boom(_text: str, _principal: Principal) -> str:
        raise RuntimeError("handler bug")

    result = handle_batch(
        [message_update(1, "/boom"), message_update(2, "/echo ok")],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={"boom": boom, "echo": _echo},
    )

    assert result.commands_routed == 2
    assert "Something went wrong" in transport.sent[0][0]
    assert transport.sent[1][0] == "operator said ok"


def test_cursor_advances_and_resumes_from_the_ledger(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport(updates=[message_update(5, "/echo hi")])
    calls = {"n": 0}

    def twice() -> bool:
        calls["n"] += 1
        return calls["n"] <= 1

    run_forever(
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
        should_continue=twice,
    )

    assert last_seen_update_id(ledger) == 5

    # A fresh daemon resumes past the committed cursor rather than reprocessing.
    resumed = FakeTransport(updates=[])
    run_forever(
        ledger=ledger,
        transport=resumed,
        authorizer=_authorizer(),
        routes={"echo": _echo},
        should_continue=lambda: len(resumed.fetched) < 1,
    )
    assert resumed.fetched[0][0] == 6


def test_idle_long_poll_writes_no_ledger_entry(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport(updates=[])

    run_forever(
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={},
        should_continue=lambda: len(transport.fetched) < 3,
    )

    assert list(ledger) == []


def test_concurrent_ask_and_inbound_command_do_not_steal_each_others_updates(
    tmp_path: Path,
) -> None:
    # The regression this whole change exists for. Previously `poll`'s
    # fetch_updates and `ask`'s poll_decision drained the same offset queue, so a
    # poll running mid-ask could consume the human's tap and time the ask out to a
    # fail-closed DENY. Now the daemon is the only consumer and hands the decision
    # to `ask` through the ledger, so both complete.
    ledger = Ledger(tmp_path / "l.jsonl")
    ask_transport = FakeTransport()

    # ask_human sends the prompt (message_id 1 from FakeTransport) and blocks.
    results: dict[str, object] = {}

    def run_ask() -> None:
        results["ask"] = ask_human(
            Action(kind="deploy", payload={}),
            transport=ask_transport,
            ledger=ledger,
            timeout=5.0,
        )

    asker = threading.Thread(target=run_ask)
    asker.start()

    # Meanwhile the daemon drains a batch holding BOTH the human's tap and an
    # ordinary inbound command.
    daemon_transport = FakeTransport()
    handle_batch(
        [
            message_update(1, "/echo hello"),
            callback_update(2, message_id=1, data="allow"),
        ],
        ledger=ledger,
        transport=daemon_transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
    )

    asker.join(timeout=10)
    assert not asker.is_alive()

    ask_result = results["ask"]
    assert ask_result.outcome == "allowed"  # type: ignore[union-attr]
    assert daemon_transport.sent[0][0] == "operator said hello"
