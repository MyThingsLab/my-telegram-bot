from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
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
    chunk_for_telegram,
    describe,
    text_from_update,
)

_SELF_TOOL = "mytelegrambot"

# Telegram clears the "typing…" indicator after about five seconds, so a handler
# that blocks on an Engine call for a minute has to keep refreshing it.
_TYPING_REFRESH_SECONDS = 4.0

# How long a handler must block before the wait is worth announcing at all. Below
# this, /help and /status just answer, with no "typing…" flicker in front.
_TYPING_DELAY_SECONDS = 0.4

# What a caller sees when a handler raises. The exception itself goes to the log,
# never into the chat: the reader cannot act on a traceback, and testers are not
# entitled to this tool's internals.
_HANDLER_FAILED = "⚠️ Something went wrong handling that. It has been logged for the operator."

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
    # An explored brief routinely runs past Telegram's 4096-char cap. Splitting it
    # keeps the tail the human actually waited for; the buttons hang under the
    # final chunk, where they read as acting on the whole reply.
    chunks = chunk_for_telegram(reply.text)
    for index, chunk in enumerate(chunks):
        is_last = index == len(chunks) - 1
        try:
            transport.send_message(
                chunk,
                chat_id=principal.chat_id,
                inline=reply.inline if is_last else None,
                markdown=reply.markdown,
            )
        except Exception as exc:
            # The handler's side effects (e.g. an idea already filed on GitHub)
            # happened before we got here -- a failed reply must not cause the
            # update to be reprocessed, which would repeat them. The cursor still
            # advances, and we stop rather than push the remaining chunks into a
            # channel that just rejected one.
            print(f"mytelegrambot: reply send failed: {describe(exc)}")
            return


@contextmanager
def _typing(transport: TelegramTransport, principal: Principal) -> Iterator[None]:
    # Held around every wait for a handler's next reply, so an Engine call reads
    # as the bot thinking rather than as the bot being dead. Cosmetic by
    # construction: the thread is a daemon, every send is best-effort, and the
    # first failure ends the refresh loop instead of retrying into a wall.
    stop = threading.Event()

    def keep_alive() -> None:
        # Wait before the *first* action, not just between refreshes. Every pull
        # is wrapped, including the one that turns out to be StopIteration, so
        # sending immediately would leave a phantom "typing…" hanging after the
        # last message -- promising more that is never coming. A wait too short to
        # notice is a wait not worth announcing.
        if stop.wait(_TYPING_DELAY_SECONDS):
            return
        while True:
            try:
                transport.send_chat_action("typing", chat_id=principal.chat_id)
            except Exception as exc:
                print(f"mytelegrambot: typing indicator failed (cosmetic): {describe(exc)}")
                return
            if stop.wait(_TYPING_REFRESH_SECONDS):
                return

    thread = threading.Thread(target=keep_alive, daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=1.0)


def _drain(replies: Iterator[Reply], *, transport: TelegramTransport, principal: Principal) -> bool:
    # A handler yields as it goes, so each reply is sent the moment it exists
    # rather than at the end. Its body runs *between* our next() calls, which is
    # exactly where the typing indicator belongs -- and where an exception will
    # surface, since a generator does nothing until it is pulled.
    sent_any = False
    while True:
        try:
            with _typing(transport, principal):
                reply = next(replies)
        except StopIteration:
            return sent_any
        except Exception as exc:
            print(f"mytelegrambot: command handling failed, not retried: {describe(exc)}")
            _send(transport, Reply(_HANDLER_FAILED), principal)
            return True
        _send(transport, reply, principal)
        sent_any = True


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

    # Answer the tap *before* doing the work, not after. Telegram spins the button
    # until this lands, and the work behind a button is now slow: "Explore deeper"
    # blocks on a whole Engine call, and "Close idea" on a `gh` round-trip. It must
    # come before dispatch, not merely before _drain -- a handler that returns a
    # plain Reply rather than a generator has already run by the time dispatch
    # returns. The answer is cosmetic and must never undo an action that already
    # happened, which is exactly why it is safe to send first; an unrouted or
    # failed tap still gets one, or the button looks wedged forever.
    transport.answer_callback_query(callback.query_id)

    try:
        replies = dispatch_callback(callback.data, callback_routes, principal)
    except Exception as exc:  # a handler bug must not kill the daemon
        print(f"mytelegrambot: callback handling failed, not retried: {describe(exc)}")
        replies = iter((Reply(_HANDLER_FAILED),))

    if replies is None:
        return False
    return _drain(replies, transport=transport, principal=principal)


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
            replies = dispatch(text, routes, principal)
        except Exception as exc:  # a handler bug must not kill the daemon
            print(f"mytelegrambot: command handling failed, not retried: {describe(exc)}")
            replies = iter((Reply(_HANDLER_FAILED),))
        if replies is None:
            continue
        routed += 1
        _drain(replies, transport=transport, principal=principal)

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
