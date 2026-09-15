from __future__ import annotations

from mythings.ledger import Ledger

from mytelegrambot.authz import Principal
from mytelegrambot.halt_command import HaltControl
from mytelegrambot.router import CallbackAction, InlineKeyboard, Reply, encode_action

# my-fleet#66: give the operator a say over myplanner's recommended build order
# before myorchestrator's `next` acts on it unattended.
#
# /plan is a **CLI hand-off**, reusing HaltControl exactly as spend_command
# does: the bot never imports myplanner, only runs the command `--plan-cmd`
# names and relays its own stdout verbatim -- the same "relay, never compose"
# discipline /halt uses. A plan summarized in this tool's own words could
# hallucinate over a sequence myplanner never actually recommended.
#
# Approve / Reorder / Skip this one only *record* the operator's decision to a
# `kind=plan_decision` ledger entry -- the shape myorchestrator's `next` is
# meant to consult before falling back to its own unattended ranking, mirroring
# how blocker_command's Retry/Skip/Take record intent rather than this tool
# re-driving the fleet itself. An unanswered /plan message writes nothing: no
# tap, no entry, so `next` sees exactly the state it would have seen had /plan
# never been sent -- the same fail-closed shape as an unanswered `ask`
# collapsing to DENY rather than a silent default order being applied.

_SELF_TOOL = "mytelegrambot"

_NOT_CONFIGURED = (
    "The plan command isn't wired up on this bot.\n"
    "Start the daemon with --plan-cmd pointing at myplanner."
)
_NOT_THE_OPERATOR = "Only the operator can act on the fleet's plan."

# Every /plan button acts on "the current plan", not a numbered entity -- unlike
# an idea or a blocker candidate, myplanner's rendered sequence has no id this
# tool can parse out of opaque relayed text.
_PLAN_SUBJECT = "current"

_ACK = {
    "approve": "Approved — recorded for myorchestrator next.",
    "reorder": "Reorder requested — recorded for myorchestrator next. "
    "Choosing the new order itself isn't wired up from a button tap yet.",
    "skip": "Skipping the next item — recorded for myorchestrator next.",
}


def plan_buttons() -> InlineKeyboard:
    return (
        (
            ("✅ Approve", encode_action("plan", _PLAN_SUBJECT, "approve")),
            ("🔀 Reorder", encode_action("plan", _PLAN_SUBJECT, "reorder")),
        ),
        (("⏭ Skip this one", encode_action("plan", _PLAN_SUBJECT, "skip")),),
    )


def handle_plan(_args: str, principal: Principal, *, control: HaltControl | None) -> Reply:
    # Fleet-wide plan control, like /halt: a tester who could reorder or skip the
    # build sequence would be steering the fleet with a chat account.
    if not principal.is_operator:
        return Reply(_NOT_THE_OPERATOR)
    if control is None:
        return Reply(_NOT_CONFIGURED)

    ok, output = control.run()
    if not ok:
        return Reply(f"Couldn't read the plan — the command failed.\n{output}")
    # Relay myplanner's own words rather than composing a summary over them: a
    # confident rendering of a sequence it never actually recommended is exactly
    # the hallucination the no-prose rule exists to prevent.
    text = output or "myplanner has no recommended sequence right now."
    return Reply(text, inline=plan_buttons())


def _handle_decision(decision: str, principal: Principal, *, ledger: Ledger) -> Reply:
    if not principal.is_operator:
        return Reply(_NOT_THE_OPERATOR)
    ledger.record(
        tool=_SELF_TOOL,
        kind="plan_decision",
        outcome=decision,
        detail=f"{decision} recorded for myorchestrator next from chat",
    )
    return Reply(_ACK[decision])


def handle_plan_approve(_action: CallbackAction, principal: Principal, *, ledger: Ledger) -> Reply:
    return _handle_decision("approve", principal, ledger=ledger)


def handle_plan_reorder(_action: CallbackAction, principal: Principal, *, ledger: Ledger) -> Reply:
    return _handle_decision("reorder", principal, ledger=ledger)


def handle_plan_skip(_action: CallbackAction, principal: Principal, *, ledger: Ledger) -> Reply:
    return _handle_decision("skip", principal, ledger=ledger)
