from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mythings.ledger import Ledger

from mytelegrambot.authz import ChatAuthorizer, Principal
from mytelegrambot.pending import PendingChats
from mytelegrambot.policy import ASK_DECISIONS
from mytelegrambot.router import (
    CallbackHandler,
    CommandHandler,
    Reply,
    dispatch,
    dispatch_callback,
)
from mytelegrambot.transport import (
    Callback,
    TelegramTransport,
    callback_from_update,
    chat_id_of,
    describe,
    text_from_update,
)

_SELF_TOOL = "mytelegrambot"

# `ask`'s Allow/Deny buttons carry the ASK_DECISIONS callback_data values. They are
# never routed to a handler: they answer a question a *separate* process is
# blocking on, and the ledger is how it hears the answer.

# The daemon is the one consumer of Telegram's bot-wide offset queue. Every
# update it fetches is classified and dispatched here:
#
#   text command   -> the command router; the reply goes back to the *originating*
#                     chat, not the operator's, so testers get their own answers
#   callback_query -> recorded as a `kind=callback` ledger entry keyed by
#                     message_id, which is how a separate `ask` process learns the
#                     human tapped Allow/Deny (see policy.await_decision)
#
# `ask` runs as its own short-lived process -- other tools shell out to
# `mytelegrambot ask` -- so an in-process queue could never reach it. The ledger is
# already the fleet's shared append-only substrate and already stores this tool's
# cursor, so it is the rendezvous. No socket, no new dependency, and the old
# two-consumers-one-offset race is gone by construction.


def last_seen_update_id(ledger: Ledger) -> int | None:
    # Same "index into the append-only ledger" idea as notifier's
    # last_notified_count, keyed by Telegram's own server-assigned update_id.
    ids = [
        e.data["last_update_id"]
        for e in ledger.read(tool=_SELF_TOOL, kind="poll")
        if "last_update_id" in e.data
    ]
    return max(ids, default=None)


@dataclass(frozen=True)
class BatchResult:
    updates_fetched: int
    commands_routed: int
    callbacks_delivered: int
    dropped: int


def _send(transport: TelegramTransport, reply: Reply, principal: Principal) -> None:
    try:
        transport.send_message(reply.text, chat_id=principal.chat_id, inline=reply.inline)
    except Exception as exc:
        # The handler's side effects (e.g. an idea already filed on GitHub)
        # happened before we got here -- a failed reply must not cause the update
        # to be reprocessed, which would repeat them. The cursor still advances.
        print(f"mytelegrambot: reply send failed: {describe(exc)}")


def _handle_ask_decision(callback: Callback, *, ledger: Ledger) -> None:
    ledger.record(
        tool=_SELF_TOOL,
        kind="callback",
        outcome="received",
        detail=f"decision {callback.data} on message {callback.message_id}",
        message_id=callback.message_id,
        decision=callback.data,
    )


def _handle_callback(
    callback: Callback,
    principal: Principal,
    *,
    ledger: Ledger,
    transport: TelegramTransport,
    callback_routes: dict[str, CallbackHandler],
) -> bool:
    if callback.data in ASK_DECISIONS:
        if not principal.is_operator:
            # An `ask` prompt is only ever sent to the operator's chat, so a
            # decision from anyone else cannot be an answer to one. Recording it
            # would let a tester resolve the operator's approval.
            return False
        _handle_ask_decision(callback, ledger=ledger)
        transport.answer_callback_query(callback.query_id)
        return True

    try:
        reply = dispatch_callback(callback.data, callback_routes, principal)
    except Exception as exc:  # a handler bug must not kill the daemon
        print(f"mytelegrambot: callback handling failed, not retried: {describe(exc)}")
        reply = Reply(f"Something went wrong handling that: {exc}")

    # Answer regardless: an unrouted or failed tap must still stop Telegram's
    # spinner, or the button looks wedged forever.
    transport.answer_callback_query(callback.query_id)
    if reply is None:
        return False
    _send(transport, reply, principal)
    return True


def handle_batch(
    updates: list[dict],
    *,
    ledger: Ledger,
    transport: TelegramTransport,
    authorizer: ChatAuthorizer,
    routes: dict[str, CommandHandler],
    callback_routes: dict[str, CallbackHandler] | None = None,
    pending: PendingChats | None = None,
) -> BatchResult:
    callback_routes = callback_routes or {}
    routed = 0
    callbacks = 0
    dropped = 0

    for update in updates:
        chat_id = chat_id_of(update)
        principal = authorizer.authorize(chat_id)
        if principal is None:
            # Still silent to *them* -- no reply, no ack. The knock is recorded
            # locally so the operator can see who is waiting to be registered.
            if pending is not None and chat_id is not None:
                pending.record(chat_id)
            dropped += 1
            continue

        callback = callback_from_update(update)
        if callback is not None:
            if _handle_callback(
                callback,
                principal,
                ledger=ledger,
                transport=transport,
                callback_routes=callback_routes,
            ):
                callbacks += 1
            else:
                dropped += 1
            continue

        text = text_from_update(update)
        if text is None:
            continue

        try:
            reply = dispatch(text, routes, principal)
        except Exception as exc:  # a handler bug must not kill the daemon
            print(f"mytelegrambot: command handling failed, not retried: {describe(exc)}")
            reply = Reply(f"Something went wrong handling that: {exc}")
        if reply is None:
            continue
        routed += 1
        _send(transport, reply, principal)

    return BatchResult(len(updates), routed, callbacks, dropped)


def run_forever(
    *,
    ledger: Ledger,
    transport: TelegramTransport,
    authorizer: ChatAuthorizer,
    routes: dict[str, CommandHandler],
    callback_routes: dict[str, CallbackHandler] | None = None,
    pending: PendingChats | None = None,
    long_poll: float = 30.0,
    should_continue: Callable[[], bool] = lambda: True,
) -> None:
    offset = last_seen_update_id(ledger)
    next_offset = offset + 1 if offset is not None else None

    while should_continue():
        updates = transport.fetch_updates(offset=next_offset, timeout=long_poll)
        if not updates:
            # An idle long-poll is not an event. The old once-a-minute oneshot
            # recorded a "skipped" entry as proof it ran; a daemon looping every
            # 30s would turn that into ledger spam carrying no information.
            continue

        result = handle_batch(
            updates,
            ledger=ledger,
            transport=transport,
            authorizer=authorizer,
            routes=routes,
            callback_routes=callback_routes,
            pending=pending,
        )

        last_update_id = max(u["update_id"] for u in updates)
        next_offset = last_update_id + 1
        ledger.record(
            tool=_SELF_TOOL,
            kind="poll",
            outcome="success",
            detail=(
                f"{result.updates_fetched} update(s), {result.commands_routed} command(s), "
                f"{result.callbacks_delivered} callback(s), {result.dropped} dropped"
            ),
            last_update_id=last_update_id,
            updates_fetched=result.updates_fetched,
            commands_routed=result.commands_routed,
            callbacks_delivered=result.callbacks_delivered,
            dropped=result.dropped,
        )
