from __future__ import annotations

from dataclasses import dataclass

from mythings.ledger import Ledger, LedgerEntry

from mytelegrambot.transport import TelegramTransport, chunk_for_telegram, describe

_SELF_TOOL = "mytelegrambot"


_SELF_BOOKKEEPING_KINDS = ("notify", "poll", "thread_anchor")


def _notifiable(entries: list[LedgerEntry]) -> list[LedgerEntry]:
    # The comms tool never reports on its own digest pushes, inbound poll
    # bookkeeping, or thread-anchor rendezvous records -- all three are pure
    # noise in a digest. Its `ask` entries (a human was prompted to approve/deny
    # an action) stay in, and so do `myidea` entries filed via /idea -- both are
    # real fleet events worth seeing, exactly like an `ask` is.
    return [e for e in entries if not (e.tool == _SELF_TOOL and e.kind in _SELF_BOOKKEEPING_KINDS)]


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


# Bucketing turns a flat digest -- every ledger entry, in ledger order, no
# matter how routine -- into the three questions an operator actually has: what
# shipped, what needs me, what broke. A raw dump answered none of them; you had
# to read every line to find the one `needs_human` entry buried in forty
# successful dispatches.
_WAITING_OUTCOMES = {"needs_human", "blocked", "halted_critical"}
_WAITING_KINDS = {"spend_alert", "halt"}
# Both spellings are live in the ledger and they mean the same thing. `outcome`
# is a free-form string, so nothing ever forced them to agree: mythings.ledger's
# own checkpoint reader treats `failed` as bad, this module only knew `failure`,
# and every `fleet_dispatch/dispatch/failed` entry therefore landed in "Other" --
# a real dispatch failure, filed under the one heading nobody reads.
_FAILED_OUTCOMES = {"failure", "failed"}
# `usage` entries are one per headless session and say nothing on their own --
# they roll into the cost total instead of appearing as lines.
_COST_ONLY_KIND = "usage"

# What an unattended digest is allowed to interrupt a human for. Everything else
# is still bucketed, still in the ledger, and still shown by an explicit
# `--since` run -- it just never rings a phone on its own.
_ALERT_BUCKETS = ("waiting", "failed")


def _bucket(entry: LedgerEntry) -> str:
    if entry.outcome in _FAILED_OUTCOMES:
        return "failed"
    if entry.outcome in _WAITING_OUTCOMES or entry.kind in _WAITING_KINDS:
        return "waiting"
    if entry.kind == "dispatch" and entry.outcome == "success":
        return "shipped"
    return "other"


def _line(entry: LedgerEntry) -> str:
    return f"  {entry.tool}/{entry.kind}: {entry.detail}"


def format_notify_message(entries: list[LedgerEntry]) -> str:
    buckets: dict[str, list[str]] = {"shipped": [], "waiting": [], "failed": [], "other": []}
    cost_usd = 0.0
    for entry in entries:
        if entry.kind == _COST_ONLY_KIND:
            cost_usd += float(entry.data.get("cost_usd", 0.0))
            continue
        buckets[_bucket(entry)].append(_line(entry))

    sections = []
    if buckets["shipped"]:
        sections.append("🚢 Shipped:\n" + "\n".join(buckets["shipped"]))
    if buckets["waiting"]:
        sections.append("⏳ Waiting on you:\n" + "\n".join(buckets["waiting"]))
    if buckets["failed"]:
        sections.append("❌ Failed:\n" + "\n".join(buckets["failed"]))
    if buckets["other"]:
        sections.append("📋 Other:\n" + "\n".join(buckets["other"]))
    if cost_usd:
        sections.append(f"💰 Cost: ${cost_usd:.2f}")
    return "\n\n".join(sections) if sections else "nothing to report"


def alerting(entries: list[LedgerEntry]) -> list[LedgerEntry]:
    return [e for e in entries if _bucket(e) in _ALERT_BUCKETS]


# Bucketing made the digest readable but did not make it quiet: it still sent
# every entry, every cycle, and the buckets themselves showed why that could
# never work. Measured over the two live ledgers, 246 of 251 and 402 of 467
# entries fell into "Other" -- 301 of them `fleet_cycle/heartbeat/ok`, the
# dead-man's-switch's own liveness records, which exist purely so `heartbeat
# check` can read them back. An operator scrolling past hundreds of those to
# find the five failures stops reading the channel, and then the ASK prompts
# that gate merges go unanswered too. Noise here has a safety cost, not just an
# annoyance cost.
#
# So the automatic digest reports only what a human is expected to act on.
# The rest is not discarded -- it is counted, and one line says how to see it.
def format_alert_message(entries: list[LedgerEntry]) -> str:
    alerts = alerting(entries)
    sections = []
    waiting = [_line(e) for e in alerts if _bucket(e) == "waiting"]
    failed = [_line(e) for e in alerts if _bucket(e) == "failed"]
    if waiting:
        sections.append("⏳ Waiting on you:\n" + "\n".join(waiting))
    if failed:
        sections.append("❌ Failed:\n" + "\n".join(failed))

    routine = len(entries) - len(alerts)
    if routine:
        sections.append(f"🔕 {routine} routine entries not shown (`notify --since <ts>` for all)")
    return "\n\n".join(sections)


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
        # so a manual re-send never consumes the automatic queue. A human who
        # names a window asked for the whole picture, so this path stays a full
        # digest -- it is the escape hatch the alert line points at.
        entries = [e for e in _notifiable(all_entries) if e.ts > since]
        cursor: dict[str, int] = {}
        window = f"since {since}"
        text = format_notify_message(entries)
        sendable = entries
    else:
        entries = _notifiable(all_entries[last_notified_count(ledger) :])
        cursor = {"notified_count": len(all_entries)}
        window = "incremental"
        text = format_alert_message(entries)
        sendable = alerting(entries)

    if not sendable:
        # The cursor advances over the routine entries even though nothing was
        # sent. Holding it would mean every future alert dragged the entire
        # unsent backlog's count along behind it, growing without bound.
        ledger.record(
            tool=_SELF_TOOL,
            kind="notify",
            outcome="skipped",
            detail=f"nothing to alert on ({window}); {len(entries)} routine",
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
        message_ids = [transport.send_message(chunk) for chunk in chunk_for_telegram(text)]
    except Exception as exc:
        # The sole comms channel must not crash on a transient Telegram outage.
        # Record nothing: with no notify entry the cursor stays put, so the same
        # digest is retried on the next run rather than lost.
        print(f"mytelegrambot: notify send failed, will retry next run: {describe(exc)}")
        return NotifyResult("failure", len(sendable), None)
    message_id = message_ids[0]

    ledger.record(
        tool=_SELF_TOOL,
        kind="notify",
        outcome="success",
        detail=f"pushed {len(sendable)} entries ({window})",
        message_id=message_id,
        **cursor,
    )
    return NotifyResult("success", len(sendable), message_id)
