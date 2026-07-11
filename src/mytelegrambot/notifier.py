from __future__ import annotations

from dataclasses import dataclass

from mythings.ledger import Ledger, LedgerEntry

from mytelegrambot.transport import TelegramTransport, chunk_for_telegram, describe

_SELF_TOOL = "mytelegrambot"


def _notifiable(entries: list[LedgerEntry]) -> list[LedgerEntry]:
    # The comms tool never reports on its own digest pushes or its own inbound
    # poll bookkeeping -- both are pure noise in a digest. Its `ask` entries (a
    # human was prompted to approve/deny an action) stay in, and so do
    # `myidea` entries filed via /idea -- both are real fleet events worth
    # seeing, exactly like an `ask` is.
    return [e for e in entries if not (e.tool == _SELF_TOOL and e.kind in ("notify", "poll"))]


def last_notified_count(ledger: Ledger) -> int:
    # How many ledger entries the last incremental notify already covered -- an
    # index into the append-only ledger, not a timestamp. Second-granularity
    # timestamps with an exclusive `>` boundary silently drop any entry sharing
    # a wall-clock second with the watermark; a count can't. Only incremental
    # runs carry `notified_count`; ad-hoc `--since` runs deliberately don't, so
    # they never move this cursor.
    counts = [
        e.data["notified_count"]
        for e in ledger.read(tool=_SELF_TOOL, kind="notify")
        if "notified_count" in e.data
    ]
    return max(counts, default=0)


def format_notify_message(entries: list[LedgerEntry]) -> str:
    return "\n".join(f"{e.ts}  {e.tool}/{e.kind}  {e.outcome}: {e.detail}" for e in entries)


@dataclass(frozen=True)
class NotifyResult:
    outcome: str  # success | skipped | failure
    entries_count: int
    message_id: int | None


def notify(
    ledger: Ledger,
    *,
    transport: TelegramTransport,
    since: str | None = None,
) -> NotifyResult:
    all_entries = list(ledger)
    if since is not None:
        # Ad-hoc manual window: send everything after `since`, but leave the
        # incremental cursor untouched (this record carries no notified_count),
        # so a manual re-send never consumes the automatic queue.
        entries = [e for e in _notifiable(all_entries) if e.ts > since]
        cursor: dict[str, int] = {}
        window = f"since {since}"
    else:
        entries = _notifiable(all_entries[last_notified_count(ledger) :])
        cursor = {"notified_count": len(all_entries)}
        window = "incremental"

    if not entries:
        ledger.record(
            tool=_SELF_TOOL,
            kind="notify",
            outcome="skipped",
            detail=f"nothing new ({window})",
            message_id=None,
            **cursor,
        )
        return NotifyResult("skipped", 0, None)

    # A digest is as long as the backlog makes it, and Telegram hard-rejects
    # anything past 4096 chars. Sending it as one message meant a big enough
    # backlog 400'd, held the cursor, and was retried identically forever -- a
    # digest that could never be delivered and only grew. Chunking is what stops
    # that from being a permanent wedge.
    #
    # If a later chunk fails, the cursor still holds and the whole digest is re-sent
    # next run, so the delivered chunks arrive twice. A duplicated notification is
    # strictly better than a dropped one, which is what advancing the cursor here
    # would risk.
    try:
        message_ids = [
            transport.send_message(chunk)
            for chunk in chunk_for_telegram(format_notify_message(entries))
        ]
    except Exception as exc:
        # The sole comms channel must not crash on a transient Telegram outage.
        # Record nothing: with no notify entry the cursor stays put, so the same
        # digest is retried on the next run rather than lost.
        print(f"mytelegrambot: notify send failed, will retry next run: {describe(exc)}")
        return NotifyResult("failure", len(entries), None)
    message_id = message_ids[0]

    ledger.record(
        tool=_SELF_TOOL,
        kind="notify",
        outcome="success",
        detail=f"pushed {len(entries)} entries ({window})",
        message_id=message_id,
        **cursor,
    )
    return NotifyResult("success", len(entries), message_id)
