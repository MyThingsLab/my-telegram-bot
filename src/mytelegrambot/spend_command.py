from __future__ import annotations

from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy

from mytelegrambot.authz import Principal
from mytelegrambot.halt_command import HaltControl
from mytelegrambot.router import CallbackAction, InlineKeyboard, Reply, encode_action

# The earlier half of the spend tripwire (fleet-dispatch#41): fleet_dispatch
# already refuses to *launch* once projected spend would cross the daily cap,
# but the operator only learns that by the run refusing. This is the push that
# fires while it is still spending, with Halt and Raise-cap buttons hung under
# it -- Halt reuses the fleet kill switch's own CLI hand-off (halt_command.py),
# Raise cap shells back into `fleet_dispatch.py --raise-daily-cap <amount>`.

_SELF_TOOL = "mytelegrambot"

_NOT_CONFIGURED = (
    "The fleet control command isn't wired up on this bot.\n"
    "Start the daemon with --halt-cmd pointing at fleet_dispatch.py."
)
_NOT_THE_OPERATOR = "Only the operator can act on a spend alert."
_STALE_BUTTON = "That button is no longer valid."


def spend_alert_text(*, spent: float, cap: float) -> str:
    fraction = spent / cap if cap else 1.0
    return (
        f"⚠️ Spend alert: ${spent:.2f} of ${cap:.2f}/day cap "
        f"({fraction:.0%})\nHalt the fleet, or raise today's cap?"
    )


def spend_alert_buttons(raise_to: float) -> InlineKeyboard:
    return (
        (
            ("⏸ Halt fleet", encode_action("spend", "now", "halt")),
            (
                f"\U0001f4b0 Raise cap to ${raise_to:.0f}",
                encode_action("spend", f"{raise_to:.2f}", "raise"),
            ),
        ),
    )


def send_spend_alert(
    *, spent: float, cap: float, raise_to: float, transport, ledger: Ledger
) -> int | None:
    message_id = transport.send_message(
        spend_alert_text(spent=spent, cap=cap),
        inline=spend_alert_buttons(raise_to),
    )
    ledger.record(
        tool=_SELF_TOOL,
        kind="spend_alert",
        outcome="success",
        detail=f"pushed spend alert: ${spent:.2f} of ${cap:.2f}/day",
        message_id=message_id,
        spent_usd=spent,
        cap_usd=cap,
    )
    return message_id


def _gate(action_kind: str, *, policy: Policy) -> bool:
    # Every write in this fleet passes the Policy seam, and both halting and
    # raising the cap are writes. The daemon is unattended, so an ASK it cannot
    # service collapses to DENY -- the same fail-closed posture halt_command
    # takes, for the same reason: a policy bug should never be able to stop or
    # widen the fleet on its own.
    decision = policy.evaluate(Action(kind=action_kind, payload={})).under(unattended=True)
    return decision is Decision.ALLOW


def handle_spend_halt(
    _action: CallbackAction,
    principal: Principal,
    *,
    control: HaltControl | None,
    policy: Policy,
    ledger: Ledger,
) -> Reply:
    if not principal.is_operator:
        return Reply(_NOT_THE_OPERATOR)
    if control is None:
        return Reply(_NOT_CONFIGURED)
    if not _gate("fleet-halt", policy=policy):
        return Reply("Halt was denied by policy.")

    ok, output = control.run("--abort")
    ledger.record(
        tool=_SELF_TOOL,
        kind="halt",
        outcome="success" if ok else "failure",
        detail=f"halt from spend alert: {output or 'no output'}",
    )
    if not ok:
        return Reply(f"Couldn't halt the fleet — the command failed.\n{output}")
    return Reply(output or "Fleet halt requested.")


def handle_spend_raise(
    action: CallbackAction,
    principal: Principal,
    *,
    control: HaltControl | None,
    policy: Policy,
    ledger: Ledger,
) -> Reply:
    if not principal.is_operator:
        return Reply(_NOT_THE_OPERATOR)
    if control is None:
        return Reply(_NOT_CONFIGURED)
    try:
        amount = float(action.subject)
    except ValueError:
        return Reply(_STALE_BUTTON)
    if not _gate("fleet-raise-cap", policy=policy):
        return Reply("Raising the cap was denied by policy.")

    ok, output = control.run("--raise-daily-cap", f"{amount:.2f}")
    ledger.record(
        tool=_SELF_TOOL,
        kind="raise_cap",
        outcome="success" if ok else "failure",
        detail=f"raise cap from spend alert: {output or 'no output'}",
        cap_usd=amount,
    )
    if not ok:
        return Reply(f"Couldn't raise the cap — the command failed.\n{output}")
    return Reply(output or f"Daily cap raised to ${amount:.2f}.")
