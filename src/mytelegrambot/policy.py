from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass

from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy, PolicyResult

from mytelegrambot.transport import TelegramTransport, describe

_DEFAULT_TIMEOUT = 300.0
_DEFAULT_INTERVAL = 0.5
_SELF_TOOL = "mytelegrambot"


def format_ask_message(action: Action) -> str:
    lines = [f"Action: {action.kind}"]
    lines += [f"  {k}: {v}" for k, v in sorted(action.payload.items())]
    return "\n".join(lines)


def await_decision(
    ledger: Ledger,
    message_id: int,
    *,
    timeout: float,
    interval: float = _DEFAULT_INTERVAL,
    now: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> str | None:
    # `ask` no longer touches getUpdates. The daemon owns that queue and writes a
    # `kind=callback` entry when the human taps; this waits for the one carrying
    # our own message_id. Two processes rendezvous through the append-only ledger,
    # which is exactly the audit trail we'd want of an approval anyway.
    #
    # Returning None on timeout is what makes the caller fail closed -- unchanged.
    deadline = now() + timeout
    while True:
        for entry in ledger.read(tool=_SELF_TOOL, kind="callback"):
            if entry.data.get("message_id") == message_id:
                decision = entry.data.get("decision")
                return decision if decision in ("allow", "deny") else None
        remaining = deadline - now()
        if remaining <= 0:
            return None
        sleep(min(interval, remaining))


@dataclass(frozen=True)
class AskResult:
    decision: Decision
    outcome: str  # allowed | denied | timeout
    message_id: int | None


def ask_human(
    action: Action,
    *,
    transport: TelegramTransport,
    ledger: Ledger,
    timeout: float = _DEFAULT_TIMEOUT,
) -> AskResult:
    message_id: int | None = None
    reply: str | None = None
    try:
        message_id = transport.send_message(format_ask_message(action), buttons=("Allow", "Deny"))
        reply = await_decision(ledger, message_id, timeout=timeout)
    except Exception as exc:  # any transport/API failure fails closed, never propagates
        print(f"mytelegrambot: transport error during ask, failing closed: {describe(exc)}")
        reply = None

    if reply == "allow":
        decision, outcome = Decision.ALLOW, "allowed"
    elif reply == "deny":
        decision, outcome = Decision.DENY, "denied"
    else:
        decision, outcome = Decision.DENY, "timeout"

    ledger.record(
        tool="mytelegrambot",
        kind="ask",
        outcome=outcome,
        detail=f"{action.kind}: {action.payload}",
        action_kind=action.kind,
        action_payload=action.payload,
        telegram_message_id=message_id,
        decision=decision.value,
    )
    return AskResult(decision, outcome, message_id)


class TelegramPolicy:
    def __init__(
        self,
        inner: Policy,
        *,
        transport: TelegramTransport,
        ledger: Ledger,
        timeout: float = _DEFAULT_TIMEOUT,
    ) -> None:
        self.inner = inner
        self.transport = transport
        self.ledger = ledger
        self.timeout = timeout

    def evaluate(self, action: Action) -> PolicyResult:
        result = self.inner.evaluate(action)
        if result.decision is not Decision.ASK:
            return result
        asked = ask_human(
            action, transport=self.transport, ledger=self.ledger, timeout=self.timeout
        )
        return PolicyResult(
            asked.decision, reason=f"telegram: {asked.outcome}", rule="telegram_ask"
        )
