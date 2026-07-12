from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from mythings.ledger import Ledger
from mythings.policy import Action
from mythings.testers import TesterStore

from conftest import OPERATOR_CHAT, FakeTransport, callback_update, message_update
from mytelegrambot import inbound
from mytelegrambot.authz import ChatAuthorizer, Principal
from mytelegrambot.inbound import handle_batch, last_seen_update_id, run_forever
from mytelegrambot.policy import ask_human, await_decision
from mytelegrambot.router import Reply


def _echo(text: str, principal: Principal) -> Reply:
    return Reply(f"{principal.label} said {text}")


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


def test_unregistered_command_is_nudged_toward_help(tmp_path: Path) -> None:
    # It used to get nothing at all, which is indistinguishable from the bot being
    # down. Ordinary chat is still ignored (see the test below); a slash command
    # was typed on purpose.
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    result = handle_batch(
        [message_update(1, "/nosuchcommand")],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
    )

    assert result.commands_routed == 1
    ((text, _inline),) = transport.sent
    assert "/nosuchcommand" in text
    assert "/help" in text


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

    def boom(_text: str, _principal: Principal) -> Reply:
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


# ------------------------------------------------- feedback while a handler works
#
# /idea, /note and /wish each block on a full Engine call -- tens of seconds. The
# daemon is what turns that into something a human can read as "working" rather
# than "dead": a typing indicator held for the wait, and each reply sent the
# moment the handler yields it rather than all at the end.


def test_a_handlers_replies_are_sent_as_it_yields_them(tmp_path: Path) -> None:
    def streaming(text: str, principal: Principal):
        yield Reply("filed as #9")
        yield Reply("here is the brief")

    transport = FakeTransport()

    handle_batch(
        [message_update(1, "/idea a tool")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={"idea": streaming},
    )

    assert [text for text, _inline in transport.sent] == ["filed as #9", "here is the brief"]


def test_the_typing_indicator_is_held_while_a_handler_works(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(inbound, "_TYPING_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(inbound, "_TYPING_REFRESH_SECONDS", 0.01)

    def slow(text: str, principal: Principal):
        time.sleep(0.15)  # stands in for an Engine call
        yield Reply("done")

    transport = FakeTransport()

    handle_batch(
        [message_update(1, "/idea a tool")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={"idea": slow},
    )

    assert ("typing", OPERATOR_CHAT) in transport.actions
    # Refreshed, not sent once: Telegram clears the indicator after ~5s, so a
    # minute-long Engine call would otherwise go quiet halfway through.
    assert transport.actions.count(("typing", OPERATOR_CHAT)) > 1


def test_an_instant_handler_shows_no_typing_indicator(tmp_path: Path) -> None:
    # Every pull is wrapped, including the one that returns StopIteration. Sending
    # the action immediately would leave a phantom "typing…" hanging after the last
    # message, promising more that is never coming.
    transport = FakeTransport()

    handle_batch(
        [message_update(1, "/echo hi")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
    )

    assert transport.sent[0][0] == "operator said hi"
    assert transport.actions == []


def test_a_reply_past_telegrams_limit_is_split_with_the_buttons_on_the_last_chunk(
    tmp_path: Path,
) -> None:
    # This is the bug that mattered: an explored brief longer than 4096 chars used
    # to be truncated, throwing away the tail the human waited a whole Engine call
    # for.
    buttons = ((("Explore deeper", "idea:9:explore"),),)

    def long_reply(text: str, principal: Principal) -> Reply:
        return Reply("\n\n".join("x" * 1000 for _ in range(10)), inline=buttons)

    transport = FakeTransport()

    handle_batch(
        [message_update(1, "/idea a tool")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={"idea": long_reply},
    )

    assert len(transport.sent) > 1
    assert all(len(text) <= 4096 for text, _inline in transport.sent)
    inlines = [inline for _text, inline in transport.sent]
    assert inlines[-1] == buttons  # actionable exactly once, under the final chunk
    assert all(inline is None for inline in inlines[:-1])


def test_a_handler_crash_never_puts_the_exception_in_the_chat(tmp_path: Path) -> None:
    # A traceback is not something the reader can act on, and a tester is not
    # entitled to this tool's internals. The exception goes to the log instead.
    def boom(text: str, principal: Principal) -> Reply:
        raise RuntimeError("gh token 'ghp_secret' rejected")

    transport = FakeTransport()

    result = handle_batch(
        [message_update(1, "/idea a tool")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={"idea": boom},
    )

    ((text, _inline),) = transport.sent
    assert "ghp_secret" not in text
    assert "RuntimeError" not in text
    assert "went wrong" in text
    assert result.commands_routed == 1  # the update is done with, not retried


def test_a_crash_midway_through_a_stream_still_reports_what_already_landed(
    tmp_path: Path,
) -> None:
    # /idea files the issue, yields the number, then explodes in explore(). The
    # human must still be told the idea was captured.
    def half_broken(text: str, principal: Principal):
        yield Reply("filed as #9")
        raise RuntimeError("engine exploded")

    transport = FakeTransport()

    handle_batch(
        [message_update(1, "/idea a tool")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={"idea": half_broken},
    )

    sent = [text for text, _inline in transport.sent]
    assert sent[0] == "filed as #9"
    assert "went wrong" in sent[1]


def test_ordinary_chat_is_still_ignored(tmp_path: Path) -> None:
    # The nudge is for slash commands only. This channel also carries digests and
    # Allow/Deny prompts; answering every stray line would make it unusable.
    transport = FakeTransport()

    result = handle_batch(
        [message_update(1, "hello there")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={"echo": _echo},
    )

    assert result.commands_routed == 0
    assert transport.sent == []


def test_a_failing_typing_indicator_never_costs_the_reply(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The indicator is cosmetic. If sendChatAction is broken, the command it was
    # decorating must still land -- and the failing refresh loop must give up
    # rather than retry into a wall for the length of an Engine call.
    attempts = {"n": 0}

    class _NoChatAction(FakeTransport):
        def send_chat_action(self, action: str, *, chat_id: str | None = None) -> None:
            attempts["n"] += 1
            raise RuntimeError("sendChatAction is down")

    monkeypatch.setattr(inbound, "_TYPING_DELAY_SECONDS", 0.01)
    monkeypatch.setattr(inbound, "_TYPING_REFRESH_SECONDS", 0.01)

    def slow(text: str, principal: Principal):
        time.sleep(0.15)  # long enough that the keep-alive certainly fires
        yield Reply("done anyway")

    transport = _NoChatAction()

    result = handle_batch(
        [message_update(1, "/idea a tool")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={"idea": slow},
    )

    assert attempts["n"] > 0  # the failure path was actually taken
    assert attempts["n"] <= 2  # one per pull: it gave up, it did not spin
    assert result.commands_routed == 1
    assert transport.sent[0][0] == "done anyway"


# --------------------------------------------- the tap has to say something back
#
# "I clicked Allow and nothing happened." The approval had in fact gone through and
# a PR had merged -- but answer_callback_query was called with no text, so Telegram
# silently stopped the spinner and left the Allow/Deny buttons sitting there. What
# the human saw was exactly what they would have seen if the bot were dead.


def test_a_tap_gets_a_visible_answer_and_the_buttons_go_away(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    transport = FakeTransport()

    handle_batch(
        [callback_update(1, message_id=77, data="allow")],
        ledger=ledger,
        transport=transport,
        authorizer=_authorizer(),
        routes={},
    )

    # The decision is still durable -- that is what `ask` is blocking on.
    assert await_decision(ledger, 77, timeout=0) == "allow"
    # And the human is actually told.
    assert transport.answers == [("q1", "Allowed ✓")]
    assert transport.cleared == [77]  # an answered prompt stops looking pending


def test_a_denial_says_so_too(tmp_path: Path) -> None:
    transport = FakeTransport()

    handle_batch(
        [callback_update(1, message_id=77, data="deny")],
        ledger=Ledger(tmp_path / "l.jsonl"),
        transport=transport,
        authorizer=_authorizer(),
        routes={},
    )

    assert transport.answers == [("q1", "Denied ✗")]


def test_the_acks_cannot_drift_from_the_buttons() -> None:
    # The toast is keyed by the callback_data the Allow/Deny buttons carry. If a new
    # decision were added without an ack, this would KeyError inside the daemon.
    from mytelegrambot.inbound import ASK_ACKS
    from mytelegrambot.policy import ASK_DECISIONS

    assert tuple(ASK_ACKS) == ASK_DECISIONS


def test_a_cosmetic_failure_never_loses_the_decision(tmp_path: Path) -> None:
    # The ledger entry is what the waiting `ask` process blocks on. A broken toast
    # or a failed button-strip must never be able to undo an approval the human
    # already gave.
    class _CosmeticsBroken(FakeTransport):
        def answer_callback_query(self, callback_query_id: str, *, text: str = "") -> None:
            raise RuntimeError("telegram flaked")

    ledger = Ledger(tmp_path / "l.jsonl")

    handle_batch(
        [callback_update(1, message_id=77, data="allow")],
        ledger=ledger,
        transport=_CosmeticsBroken(),
        authorizer=_authorizer(),
        routes={},
    )

    assert await_decision(ledger, 77, timeout=0) == "allow"
