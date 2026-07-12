from __future__ import annotations

from mythings.ledger import Ledger

from mytelegrambot.authz import Principal
from mytelegrambot.router import CallbackAction, InlineKeyboard, Reply, encode_action

# fleet-dispatch#44: "a stuck worker should ask a human, not just give up."
#
# fleet_dispatch marks a candidate `needs_human` after MAX_ATTEMPTS unresolved
# tries and moves on -- a state nobody was watching. mydirector's `escalate`
# command reads that off fleet_dispatch's ledger and pushes it here, with
# Retry / Skip / I'll take it buttons.
#
# Retry and Skip only *record* the operator's decision today: fleet_dispatch
# already treats needs_human as permanent once written (nothing re-attempts it
# without a code change there), and this tool relays reality rather than
# claiming an action it did not take. I'll take it needs no further backend at
# all -- fleet_dispatch's existing "skip:needs_human forever" behavior already
# is that decision.

_SELF_TOOL = "mytelegrambot"

_NOT_THE_OPERATOR = "Only the operator can act on a blocker escalation."
_STALE_BUTTON = "That button is no longer valid."

_ACK = {
    "retry": "Retry recorded — fleet-dispatch#44's automatic re-dispatch isn't "
    "wired yet, so this only records your decision for now.",
    "skip": "Marked as skipped — the fleet already won't retry it automatically.",
    "take": "Marked as yours — the fleet won't retry it automatically.",
}


def blocker_alert_text(*, candidate: str, detail: str, attempt: int) -> str:
    return (
        f"🧑‍🔧 {candidate} needs a human\n"
        f"Gave up after {attempt} attempt(s): {detail}"
    )


def blocker_alert_buttons(candidate: str) -> InlineKeyboard:
    return (
        (
            ("🔁 Retry", encode_action("blocker", candidate, "retry")),
            ("⏭ Skip", encode_action("blocker", candidate, "skip")),
        ),
        (("🙋 I'll take it", encode_action("blocker", candidate, "take")),),
    )


def send_blocker_alert(
    *, candidate: str, detail: str, attempt: int, transport, ledger: Ledger
) -> int | None:
    message_id = transport.send_message(
        blocker_alert_text(candidate=candidate, detail=detail, attempt=attempt),
        inline=blocker_alert_buttons(candidate),
    )
    ledger.record(
        tool=_SELF_TOOL,
        kind="blocker_alert",
        outcome="success",
        detail=f"pushed blocker alert for {candidate}",
        message_id=message_id,
        candidate=candidate,
    )
    return message_id


def _handle_decision(
    decision: str, action: CallbackAction, principal: Principal, *, ledger: Ledger
) -> Reply:
    if not principal.is_operator:
        return Reply(_NOT_THE_OPERATOR)
    candidate = action.subject
    if not candidate:
        return Reply(_STALE_BUTTON)
    ledger.record(
        tool=_SELF_TOOL,
        kind="blocker_decision",
        outcome=decision,
        detail=f"{decision} recorded for {candidate} from chat",
        candidate=candidate,
    )
    return Reply(_ACK[decision])


def handle_blocker_retry(action: CallbackAction, principal: Principal, *, ledger: Ledger) -> Reply:
    return _handle_decision("retry", action, principal, ledger=ledger)


def handle_blocker_skip(action: CallbackAction, principal: Principal, *, ledger: Ledger) -> Reply:
    return _handle_decision("skip", action, principal, ledger=ledger)


def handle_blocker_take(action: CallbackAction, principal: Principal, *, ledger: Ledger) -> Reply:
    return _handle_decision("take", action, principal, ledger=ledger)
