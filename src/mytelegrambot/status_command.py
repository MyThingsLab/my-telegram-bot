from __future__ import annotations

from mythings.ledger import Ledger, LedgerEntry

# Deterministic health snapshot -- reads only this tool's own ledger (the same
# file `poll` writes its cursor to) and counts existing entries. No Engine call,
# no network, no prose composed over what it relays: same "pure plumbing"
# discipline as notify/ask.
_SELF_TOOL = "mytelegrambot"


def _last(entries: list[LedgerEntry], *, tool: str, kind: str) -> LedgerEntry | None:
    matching = [e for e in entries if e.tool == tool and e.kind == kind]
    return matching[-1] if matching else None


def build_status(ledger: Ledger) -> str:
    entries = list(ledger)
    if not entries:
        return "📊 Bot status\n\nNo activity recorded yet."

    ideas_filed = sum(
        1
        for e in entries
        if e.tool == "myidea" and e.kind == "idea_filed" and e.outcome == "success"
    )
    asks = [e for e in entries if e.tool == _SELF_TOOL and e.kind == "ask"]
    allowed = sum(1 for e in asks if e.outcome == "allowed")
    denied = sum(1 for e in asks if e.outcome == "denied")
    timed_out = sum(1 for e in asks if e.outcome == "timeout")

    asks_line = (
        f"Approvals asked: {len(asks)} "
        f"({allowed} allowed · {denied} denied · {timed_out} timed out)"
    )
    lines = [
        "📊 Bot status",
        "",
        f"Ideas filed via chat: {ideas_filed}",
        asks_line,
    ]

    last_notify = _last(entries, tool=_SELF_TOOL, kind="notify")
    if last_notify is not None and last_notify.outcome == "success":
        lines.append(f"Last digest: {last_notify.ts} ({last_notify.detail})")
    else:
        lines.append("Last digest: none pushed yet")

    last_poll = _last(entries, tool=_SELF_TOOL, kind="poll")
    if last_poll is not None:
        cursor = last_poll.data.get("last_update_id", "—")
        lines.append(f"Last poll: {last_poll.ts} (cursor #{cursor})")

    return "\n".join(lines)
