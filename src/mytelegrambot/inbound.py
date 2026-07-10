from __future__ import annotations

from dataclasses import dataclass

from mythings.ledger import Ledger

from mytelegrambot.router import CommandHandler, dispatch
from mytelegrambot.transport import TelegramTransport, describe, text_from_update

_SELF_TOOL = "mytelegrambot"


def last_seen_update_id(ledger: Ledger) -> int | None:
    # Same "index into the append-only ledger" idea as notifier's
    # last_notified_count, keyed by Telegram's own server-assigned update_id
    # instead of a self-counted length -- there's no precedent anywhere in
    # this fleet for a bespoke local cursor file, and the ledger already gives
    # an audit trail for free.
    ids = [
        e.data["last_update_id"]
        for e in ledger.read(tool=_SELF_TOOL, kind="poll")
        if "last_update_id" in e.data
    ]
    return max(ids, default=None)


@dataclass(frozen=True)
class PollResult:
    # "failure" never actually occurs today: fetch_updates degrades a
    # transport error to an empty batch (same contract as poll_decision)
    # rather than raising, so a Telegram outage surfaces as "skipped" plus a
    # printed warning, not a distinct failure outcome. Kept in the shape for
    # symmetry with NotifyResult / a future transport that can raise.
    outcome: str  # success | skipped | failure
    updates_fetched: int
    commands_routed: int


def poll_once(
    *,
    ledger: Ledger,
    transport: TelegramTransport,
    routes: dict[str, CommandHandler],
    get_updates_timeout: float = 1.0,
) -> PollResult:
    offset = last_seen_update_id(ledger)
    updates = transport.fetch_updates(
        offset=offset + 1 if offset is not None else None, timeout=get_updates_timeout
    )

    if not updates:
        # Nothing to acknowledge -- leave the cursor exactly where it was,
        # same "an empty window doesn't move the watermark" spirit as
        # notify's --since not consuming the incremental queue.
        ledger.record(tool=_SELF_TOOL, kind="poll", outcome="skipped", detail="no updates")
        return PollResult("skipped", 0, 0)

    routed = 0
    for update in updates:
        text = text_from_update(update)
        if text is None:
            # A callback_query update belongs to ask_human's own poll_decision
            # long-poll, never to this router. (Known, accepted limitation:
            # both consumers share one server-side offset queue, so a poll run
            # can advance past a callback the human is mid-tap on -- see
            # CLAUDE.md.)
            continue
        try:
            reply = dispatch(text, routes)
        except Exception as exc:  # a handler bug must not crash the whole poll
            print(f"mytelegrambot: command handling failed, not retried: {describe(exc)}")
            reply = f"Something went wrong handling that: {exc}"
        if reply is None:
            continue
        routed += 1
        try:
            transport.send_message(reply)
        except Exception as exc:
            # The command's own side effects (e.g. an idea already filed on
            # GitHub) already happened inside dispatch() -- a failed reply
            # must not cause this update to be reprocessed, which would refile
            # it. The cursor still advances below.
            print(f"mytelegrambot: poll reply send failed: {describe(exc)}")

    last_update_id = max(u["update_id"] for u in updates)
    ledger.record(
        tool=_SELF_TOOL,
        kind="poll",
        outcome="success",
        detail=f"{len(updates)} update(s), {routed} command(s) routed",
        last_update_id=last_update_id,
        updates_fetched=len(updates),
        commands_routed=routed,
    )
    return PollResult("success", len(updates), routed)
