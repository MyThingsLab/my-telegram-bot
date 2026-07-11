from __future__ import annotations

from pathlib import Path

import pytest
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult
from mythings.testers import TesterStore

from conftest import (
    OPERATOR_CHAT,
    FakeTransport,
    all_text,
    as_tester,
    callback_update,
    operator,
    replies,
)
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
    assert parse_callback(data) == CallbackAction(key="idea:explore", subject="12")


def test_encode_rejects_data_over_telegrams_64_byte_limit() -> None:
    with pytest.raises(ValueError, match="64 bytes"):
        encode_action("idea", 1, "x" * 64)


def test_parse_callback_rejects_malformed_data() -> None:
    for bad in ("", "idea", "idea:12", "idea:12:explore:extra", ":12:x", "idea::explore"):
        assert parse_callback(bad) is None, bad


def test_parse_callback_keeps_a_non_numeric_subject() -> None:
    # A trial acts on a tool, not an issue number.
    assert parse_callback("guide:my-archivist:trial") == CallbackAction(
        key="guide:trial", subject="my-archivist"
    )


def test_encode_action_rejects_colons_in_a_segment() -> None:
    with pytest.raises(ValueError, match="colon-free"):
        encode_action("guide", "my:tool", "trial")


def test_as_int_narrows_only_numeric_subjects() -> None:
    assert CallbackAction("idea:close", "12").as_int() == 12
    assert CallbackAction("guide:trial", "my-archivist").as_int() is None


def test_a_forged_non_numeric_idea_button_is_refused_not_crashed(tmp_path: Path) -> None:
    # "idea:abc:close" now parses. The handler must reject it, not raise inside
    # the daemon loop.
    calls: list = []
    reply = close_idea(
        CallbackAction(key="idea:close", subject="abc"),
        operator(),
        policy=_AllowAll(),
        ledger=Ledger(tmp_path / "l.jsonl"),
        repo="o/r",
        runner=lambda argv: calls.append(argv) or "",
    )

    assert "no longer valid" in all_text(reply)
    assert calls == []


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
        CallbackAction(key="idea:close", subject=str(number)),
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
            CallbackAction(key="idea:explore", subject="5"),
            as_tester(tester),
            store=store,
            github=None,
            policy=_AllowAll(),
            engine=None,
            ledger=ledger,
            repo="o/r",
        )

    sent = replies(go())
    assert len(sent) == 2  # "exploring…" first, so the tap is visibly doing something
    ack, first = sent
    assert ack.inline is None
    assert "a deeper brief" in first.text
    assert first.inline == idea_buttons(5)  # still actionable
    assert store.get(tester.id).engine_used == 1

    second = all_text(go())
    assert "full allowance" in second
    assert calls["n"] == 1  # the Engine was never reached the second time


def test_explore_idea_flags_when_the_brief_could_not_be_posted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, tester, ledger = _tester_with_ledger(tmp_path, filed=5)

    class _Unposted:
        comment = "a brief"
        posted = False

    monkeypatch.setattr(idea_command, "explore", lambda **k: _Unposted())

    reply = all_text(
        explore_idea(
            CallbackAction(key="idea:explore", subject="5"),
            as_tester(tester),
            store=store,
            github=None,
            policy=_AllowAll(),
            engine=None,
            ledger=ledger,
            repo="o/r",
        )
    )

    assert "could not be posted" in reply


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
        replies(
            explore_idea(
                CallbackAction(key="idea:explore", subject="5"),
                as_tester(tester),
                store=store,
                github=None,
                policy=_AllowAll(),
                engine=None,
                ledger=ledger,
                repo="o/r",
            )
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
        CallbackAction(key="idea:close", subject="999"),  # not theirs
        as_tester(tester),
        policy=_AllowAll(),
        ledger=ledger,
        repo="o/r",
        runner=lambda argv: calls.append(argv) or "",
    )

    assert "isn't one of yours" in all_text(reply)
    assert calls == []  # gh never ran


def test_a_tester_can_close_an_idea_they_filed(tmp_path: Path) -> None:
    store, tester, ledger = _tester_with_ledger(tmp_path, filed=5)
    calls: list = []

    reply = close_idea(
        CallbackAction(key="idea:close", subject="5"),
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
        CallbackAction(key="idea:explore", subject="999"),
        as_tester(tester),
        store=store,
        github=None,
        policy=_AllowAll(),
        engine=None,
        ledger=ledger,
        repo="o/r",
    )

    assert "isn't one of yours" in all_text(reply)
    # Refused before the reservation, so it costs them nothing.
    assert store.get(tester.id).engine_used == 0


def test_the_operator_may_act_on_any_idea(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")  # empty: the operator files elsewhere too
    calls: list = []

    reply = close_idea(
        CallbackAction(key="idea:close", subject="4242"),
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
    return {"idea:close": lambda action, principal: Reply(f"{reply_text} #{action.subject}")}


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


def test_a_forged_non_numeric_explore_button_is_refused_not_crashed(tmp_path: Path) -> None:
    store, tester, ledger = _tester_with_ledger(tmp_path, filed=5)

    reply = explore_idea(
        CallbackAction(key="idea:explore", subject="abc"),
        as_tester(tester),
        store=store,
        github=None,
        policy=_AllowAll(),
        engine=None,
        ledger=ledger,
        repo="o/r",
    )

    assert "no longer valid" in all_text(reply)
    assert store.get(tester.id).engine_used == 0  # refused before the reservation


def test_a_tap_is_answered_before_the_work_it_triggers(tmp_path: Path) -> None:
    # Telegram spins the button until answerCallbackQuery lands, and the work
    # behind a button is slow (an Engine call, or a `gh` round-trip). Answering
    # after dispatch left the spinner going for the whole thing -- and a handler
    # returning a plain Reply rather than a generator has already *run* by the time
    # dispatch returns, so the answer has to come before dispatch, not just before
    # the send.
    order: list[str] = []

    class _Recording(FakeTransport):
        def answer_callback_query(self, callback_query_id: str, *, text: str = "") -> None:
            order.append("answered")
            super().answer_callback_query(callback_query_id)

    def slow_close(action: CallbackAction, principal) -> Reply:
        order.append("worked")  # stands in for `gh issue close`
        return Reply("closed")

    handle_batch(
        [callback_update(1, message_id=3, data="idea:12:close")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=_Recording(),
        authorizer=_authorizer(),
        routes={},
        callback_routes={"idea:close": slow_close},
    )

    assert order == ["answered", "worked"]
