from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mythings.ledger import Ledger

from mytelegrambot.authz import ChatAuthorizer
from mytelegrambot.router import CommandHandler, dispatch
from mytelegrambot.transport import (
    TelegramTransport,
    callback_from_update,
    chat_id_of,
    describe,
    text_from_update,
)

_SELF_TOOL = "mytelegrambot"

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


def handle_batch(
    updates: list[dict],
    *,
    ledger: Ledger,
    transport: TelegramTransport,
    authorizer: ChatAuthorizer,
    routes: dict[str, CommandHandler],
) -> BatchResult:
    routed = 0
    callbacks = 0
    dropped = 0

    for update in updates:
        principal = authorizer.authorize(chat_id_of(update))
        if principal is None:
            dropped += 1
            continue

        callback = callback_from_update(update)
        if callback is not None:
            message_id, decision = callback
            if not principal.is_operator:
                # An `ask` prompt is only ever sent to the operator's chat, so a
                # callback from anyone else cannot be an answer to one. Recording
                # it would let a tester resolve the operator's approval.
                dropped += 1
                continue
            ledger.record(
                tool=_SELF_TOOL,
                kind="callback",
                outcome="received",
                detail=f"decision {decision} on message {message_id}",
                message_id=message_id,
                decision=decision,
            )
            callbacks += 1
            continue

        text = text_from_update(update)
        if text is None:
            continue

        try:
            reply = dispatch(text, routes, principal)
        except Exception as exc:  # a handler bug must not kill the daemon
            print(f"mytelegrambot: command handling failed, not retried: {describe(exc)}")
            reply = f"Something went wrong handling that: {exc}"
        if reply is None:
            continue
        routed += 1
        try:
            transport.send_message(reply, chat_id=principal.chat_id)
        except Exception as exc:
            # The command's side effects (e.g. an idea already filed on GitHub)
            # happened inside dispatch() -- a failed reply must not cause this
            # update to be reprocessed, which would refile it. The cursor still
            # advances below.
            print(f"mytelegrambot: reply send failed: {describe(exc)}")

    return BatchResult(len(updates), routed, callbacks, dropped)


def run_forever(
    *,
    ledger: Ledger,
    transport: TelegramTransport,
    authorizer: ChatAuthorizer,
    routes: dict[str, CommandHandler],
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
