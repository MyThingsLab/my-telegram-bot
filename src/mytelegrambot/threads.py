from __future__ import annotations

from mythings.ledger import Ledger

# A private DM chat has no native message threads (Telegram's message_thread_id
# only exists in forum-mode supergroups, which this bot does not require). The
# next-best thing a reply_to_message_id can buy: each new push about the same
# subject replies to the last one, so Telegram's client visually chains them
# instead of the chat being one flat, unbroken scroll -- "one flat chat will
# not survive 30 repos" (fleet-dispatch#28).
#
# The anchor is the ledger itself, exactly the rendezvous fleet_ask.py's ask
# channel already uses: whichever process pushes next reads the last one back.

_SELF_TOOL = "mytelegrambot"
_KIND = "thread_anchor"


def anchor_for(ledger: Ledger, subject: str) -> int | None:
    entries = [
        e for e in ledger.read(tool=_SELF_TOOL, kind=_KIND) if e.data.get("subject") == subject
    ]
    if not entries:
        return None
    return entries[-1].data.get("message_id")


def remember_anchor(ledger: Ledger, subject: str, message_id: int) -> None:
    ledger.record(
        tool=_SELF_TOOL,
        kind=_KIND,
        outcome="success",
        detail=f"anchor for {subject}",
        subject=subject,
        message_id=message_id,
    )
