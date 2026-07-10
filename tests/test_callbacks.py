from __future__ import annotations

from pathlib import Path

import pytest
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult
from mythings.testers import TesterStore

from conftest import OPERATOR_CHAT, FakeTransport, as_tester, callback_update, operator
from mytelegrambot import idea_command
from mytelegrambot.authz import ChatAuthorizer
from mytelegrambot.idea_command import close_idea, explore_idea, idea_buttons
from mytelegrambot.inbound import handle_batch
from mytelegrambot.router import (
    CallbackAction,
    Reply,
    dispatch_callback,
    encode_action,
    parse_callback,
)


class _AllowAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ALLOW, reason="ok", rule="allow_all")


class _DenyAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY, reason="no", rule="deny_all")


class _AskAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ASK, reason="confirm", rule="ask_all")


def _authorizer(store: TesterStore | None = None) -> ChatAuthorizer:
    return ChatAuthorizer(OPERATOR_CHAT, store=store)


# ---------------------------------------------------------------- encoding


def test_encode_and_parse_roundtrip() -> None:
    data = encode_action("idea", 12, "explore")

    assert data == "idea:12:explore"
    assert parse_callback(data) == CallbackAction(key="idea:explore", number=12)


def test_encode_rejects_data_over_telegrams_64_byte_limit() -> None:
    with pytest.raises(ValueError, match="64 bytes"):
        encode_action("idea", 1, "x" * 64)


def test_parse_callback_rejects_malformed_data() -> None:
    for bad in ("", "idea", "idea:12", "idea:notanumber:explore", "idea:12:explore:extra", ":12:x"):
        assert parse_callback(bad) is None, bad


def test_idea_buttons_carry_both_actions() -> None:
    (row,) = idea_buttons(7)
    labels = [label for label, _data in row]
    datas = [data for _label, data in row]

    assert any("Explore" in label for label in labels)
    assert any("Close" in label for label in labels)
    assert datas == ["idea:7:explore", "idea:7:close"]


def test_dispatch_callback_returns_none_for_an_unregistered_key() -> None:
    assert dispatch_callback("idea:1:vaporize", {}, operator()) is None


def test_dispatch_callback_returns_none_for_malformed_data() -> None:
    routes = {"idea:close": lambda a, p: Reply("never")}
    assert dispatch_callback("garbage", routes, operator()) is None


# ---------------------------------------------------------------- close


def _close(policy, ledger: Ledger, calls: list, number: int = 12) -> Reply:
    return close_idea(
        CallbackAction(key="idea:close", number=number),
        operator(),
        policy=policy,
        ledger=ledger,
        repo="o/r",
        runner=lambda argv: calls.append(argv) or "",
    )


def test_close_idea_closes_the_issue_and_records_it(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    calls: list = []

    reply = _close(_AllowAll(), ledger, calls)

    assert calls == [["issue", "close", "12", "--repo", "o/r"]]
    assert "Closed my-idea#12" in reply.text
    entry = ledger.read(kind="idea_closed")[0]
    assert entry.data["idea_issue"] == 12


def test_close_idea_denied_by_policy_touches_nothing(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    calls: list = []

    reply = _close(_DenyAll(), ledger, calls)

    assert calls == []  # gh never ran
    assert "denied by policy" in reply.text
    assert list(ledger) == []


def test_close_idea_fails_closed_when_policy_would_ask(tmp_path: Path) -> None:
    # The daemon is unattended: an ASK it cannot service must resolve DENY, the
    # same posture ask_human takes on timeout.
    ledger = Ledger(tmp_path / "l.jsonl")
    calls: list = []

    reply = _close(_AskAll(), ledger, calls)

    assert calls == []
    assert "denied by policy" in reply.text


# ---------------------------------------------------------------- explore


def test_explore_idea_is_metered_and_refuses_an_exhausted_tester(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)
    ledger = store.ledger_for(tester)
    ledger.record("myidea", "idea_filed", "success", idea_issue=5)  # it's theirs
    calls = {"n": 0}

    class _Result:
        comment = "a deeper brief"
        posted = True

    def fake_explore(**kwargs: object) -> _Result:
        calls["n"] += 1
        return _Result()

    monkeypatch.setattr(idea_command, "explore", fake_explore)

    def go():
        return explore_idea(
            CallbackAction(key="idea:explore", number=5),
            as_tester(tester),
            store=store,
            github=None,
            policy=_AllowAll(),
            engine=None,
            ledger=ledger,
            repo="o/r",
        )

    first = go()
    assert "a deeper brief" in first.text
    assert first.inline == idea_buttons(5)  # still actionable
    assert store.get(tester.id).engine_used == 1

    second = go()
    assert "full allowance" in second.text
    assert calls["n"] == 1  # the Engine was never reached the second time


def test_explore_idea_flags_when_the_brief_could_not_be_posted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, tester, ledger = _tester_with_ledger(tmp_path, filed=5)

    class _Unposted:
        comment = "a brief"
        posted = False

    monkeypatch.setattr(idea_command, "explore", lambda **k: _Unposted())

    reply = explore_idea(
        CallbackAction(key="idea:explore", number=5),
        as_tester(tester),
        store=store,
        github=None,
        policy=_AllowAll(),
        engine=None,
        ledger=ledger,
        repo="o/r",
    )

    assert "could not be posted" in reply.text


def test_explore_idea_refunds_when_the_engine_call_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)
    ledger = store.ledger_for(tester)
    ledger.record("myidea", "idea_filed", "success", idea_issue=5)

    def boom(**kwargs: object) -> None:
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(idea_command, "explore", boom)

    with pytest.raises(RuntimeError, match="engine exploded"):
        explore_idea(
            CallbackAction(key="idea:explore", number=5),
            as_tester(tester),
            store=store,
            github=None,
            policy=_AllowAll(),
            engine=None,
            ledger=ledger,
            repo="o/r",
        )

    assert store.get(tester.id).engine_used == 0


# ------------------------------------------------- ownership of the subject
#
# callback_data is client-supplied: Telegram delivers whatever a client sends for
# a message it can see. A tester must not be able to hand-craft a button press
# against an idea they never filed -- both actions are GitHub writes.


def _tester_with_ledger(tmp_path: Path, *, filed: int | None) -> tuple:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=5, chat_id=999)
    ledger = store.ledger_for(tester)
    if filed is not None:
        ledger.record("myidea", "idea_filed", "success", idea_issue=filed)
    return store, tester, ledger


def test_a_tester_cannot_close_an_idea_they_did_not_file(tmp_path: Path) -> None:
    store, tester, ledger = _tester_with_ledger(tmp_path, filed=5)
    calls: list = []

    reply = close_idea(
        CallbackAction(key="idea:close", number=999),  # not theirs
        as_tester(tester),
        policy=_AllowAll(),
        ledger=ledger,
        repo="o/r",
        runner=lambda argv: calls.append(argv) or "",
    )

    assert "isn't one of yours" in reply.text
    assert calls == []  # gh never ran


def test_a_tester_can_close_an_idea_they_filed(tmp_path: Path) -> None:
    store, tester, ledger = _tester_with_ledger(tmp_path, filed=5)
    calls: list = []

    reply = close_idea(
        CallbackAction(key="idea:close", number=5),
        as_tester(tester),
        policy=_AllowAll(),
        ledger=ledger,
        repo="o/r",
        runner=lambda argv: calls.append(argv) or "",
    )

    assert "Closed my-idea#5" in reply.text
    assert calls == [["issue", "close", "5", "--repo", "o/r"]]


def test_a_tester_cannot_explore_an_idea_they_did_not_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, tester, ledger = _tester_with_ledger(tmp_path, filed=5)

    def never(**kwargs: object) -> None:
        raise AssertionError("explore must not run")

    monkeypatch.setattr(idea_command, "explore", never)

    reply = explore_idea(
        CallbackAction(key="idea:explore", number=999),
        as_tester(tester),
        store=store,
        github=None,
        policy=_AllowAll(),
        engine=None,
        ledger=ledger,
        repo="o/r",
    )

    assert "isn't one of yours" in reply.text
    # Refused before the reservation, so it costs them nothing.
    assert store.get(tester.id).engine_used == 0


def test_the_operator_may_act_on_any_idea(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")  # empty: the operator files elsewhere too
    calls: list = []

    reply = close_idea(
        CallbackAction(key="idea:close", number=4242),
        operator(),
        policy=_AllowAll(),
        ledger=ledger,
        repo="o/r",
        runner=lambda argv: calls.append(argv) or "",
    )

    assert "Closed my-idea#4242" in reply.text
    assert calls != []


# ---------------------------------------------------------------- daemon routing


def _routes(reply_text: str = "done") -> dict:
    return {"idea:close": lambda action, principal: Reply(f"{reply_text} #{action.number}")}


def test_daemon_routes_a_button_press_to_its_handler(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    result = handle_batch(
        [callback_update(1, message_id=9, data="idea:12:close", query_id="qq")],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={},
        callback_routes=_routes(),
    )

    assert result.callbacks_delivered == 1
    assert transport.sent[0][0] == "done #12"
    assert transport.sent_to == [OPERATOR_CHAT]
    assert transport.answered == ["qq"]  # spinner stopped


def test_a_tester_may_press_their_own_idea_buttons(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    store.register("ada", engine_quota=1, chat_id=999)
    transport = FakeTransport()

    result = handle_batch(
        [callback_update(1, message_id=9, data="idea:12:close", chat_id=999)],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(store),
        routes={},
        callback_routes=_routes(),
    )

    assert result.callbacks_delivered == 1
    assert transport.sent_to == ["999"]


def test_an_ask_decision_is_never_routed_to_a_handler(tmp_path: Path) -> None:
    # "allow"/"deny" answer a blocked `ask` process through the ledger. If a
    # handler ever claimed them, approvals would silently stop resolving.
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()
    trap = {"n": 0}

    def never(action, principal):
        trap["n"] += 1
        return Reply("should not run")

    handle_batch(
        [callback_update(1, message_id=77, data="allow", query_id="qa")],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={},
        callback_routes={"allow": never},
    )

    assert trap["n"] == 0
    assert transport.sent == []
    assert ledger.read(kind="callback")[0].data["message_id"] == 77
    assert transport.answered == ["qa"]


def test_unrouted_button_still_stops_the_spinner(tmp_path: Path) -> None:
    transport = FakeTransport()

    result = handle_batch(
        [callback_update(1, message_id=9, data="idea:12:vaporize", query_id="qz")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={},
        callback_routes=_routes(),
    )

    assert result.dropped == 1
    assert transport.sent == []
    assert transport.answered == ["qz"]


def test_a_failing_callback_handler_answers_and_reports(tmp_path: Path) -> None:
    transport = FakeTransport()

    def boom(action, principal):
        raise RuntimeError("handler bug")

    result = handle_batch(
        [callback_update(1, message_id=9, data="idea:12:close", query_id="qb")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={},
        callback_routes={"idea:close": boom},
    )

    assert result.callbacks_delivered == 1
    assert "Something went wrong" in transport.sent[0][0]
    assert transport.answered == ["qb"]


def test_an_unauthorized_chats_button_press_is_dropped_silently(tmp_path: Path) -> None:
    transport = FakeTransport()

    result = handle_batch(
        [callback_update(1, message_id=9, data="idea:12:close", chat_id="intruder")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={},
        callback_routes=_routes(),
    )

    assert result.dropped == 1
    assert transport.sent == []
    assert transport.answered == []  # not even a spinner ack: it learns nothing


# ---------------------------------------------------------------- the gh boundary


def test_gh_returns_stdout_on_success(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Proc:
        returncode = 0
        stdout = "closed\n"
        stderr = ""

    monkeypatch.setattr(idea_command.subprocess, "run", lambda *a, **k: _Proc())

    assert idea_command._gh(["issue", "close", "1"]) == "closed\n"


def test_gh_raises_with_stderr_on_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Proc:
        returncode = 1
        stdout = ""
        stderr = "could not resolve to an Issue\n"

    monkeypatch.setattr(idea_command.subprocess, "run", lambda *a, **k: _Proc())

    with pytest.raises(RuntimeError, match="could not resolve to an Issue"):
        idea_command._gh(["issue", "close", "999"])
