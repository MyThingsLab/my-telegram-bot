from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from mythings.ledger import Ledger, LedgerEntry

from mytelegrambot.transport import TelegramTransport, describe


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def last_notify_ts(ledger: Ledger) -> str | None:
    entries = ledger.read(tool="mytelegrambot", kind="notify")
    if not entries:
        return None
    return max(e.ts for e in entries)


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
    window_start = since if since is not None else last_notify_ts(ledger)
    window_end = _now()
    entries = [
        e for e in ledger if (window_start is None or e.ts > window_start) and e.ts <= window_end
    ]

    if not entries:
        ledger.record(
            tool="mytelegrambot",
            kind="notify",
            outcome="skipped",
            detail=f"nothing new for {window_start or 'all-time'} -> {window_end}",
            window_start=window_start,
            window_end=window_end,
            message_id=None,
        )
        return NotifyResult("skipped", 0, None)

    try:
        message_id = transport.send_message(format_notify_message(entries))
    except Exception as exc:
        # The sole comms channel must not crash on a transient Telegram outage.
        # Record nothing: with no successful notify entry the watermark stays
        # put, so the same digest is retried on the next run rather than lost.
        print(f"mytelegrambot: notify send failed, will retry next run: {describe(exc)}")
        return NotifyResult("failure", len(entries), None)

    ledger.record(
        tool="mytelegrambot",
        kind="notify",
        outcome="success",
        detail=f"pushed digest for {window_start or 'all-time'} -> {window_end}",
        window_start=window_start,
        window_end=window_end,
        message_id=message_id,
    )
    return NotifyResult("success", len(entries), message_id)
