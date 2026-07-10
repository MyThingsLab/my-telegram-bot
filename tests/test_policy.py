from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult

from conftest import ErrorTransport, FakeTransport
from mytelegrambot.policy import TelegramPolicy, ask_human, await_decision

_ACTION = Action(kind="bash", payload={"command": "git push --force origin main"})


class _AlwaysAsk:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ASK, reason="destructive", rule="ask_destructive")


class _FixedDecision:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision

    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(self.decision, reason="fixed", rule="fixed")


def _tapped(ledger: Ledger, message_id: int, decision: str) -> None:
    # What the daemon writes when the human taps Allow/Deny. FakeTransport hands
    # out message_id 1 for the first message it sends, which is the ask prompt.
    ledger.record(
        tool="mytelegrambot",
        kind="callback",
        outcome="received",
        message_id=message_id,
        decision=decision,
    )


def test_await_decision_ignores_callbacks_for_other_messages(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _tapped(ledger, 99, "allow")

    assert await_decision(ledger, 1, timeout=0) is None


def test_await_decision_rejects_a_malformed_decision(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _tapped(ledger, 1, "maybe")

    assert await_decision(ledger, 1, timeout=0) is None


def test_await_decision_polls_until_the_callback_arrives(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    clock = {"t": 0.0}

    def now() -> float:
        return clock["t"]

    def sleep(seconds: float) -> None:
        clock["t"] += seconds
        if clock["t"] >= 1.0:
            _tapped(ledger, 1, "allow")

    assert await_decision(ledger, 1, timeout=5, now=now, sleep=sleep) == "allow"


def test_ask_human_happy_path_resolves_allow(tmp_path: Path) -> None:
    transport = FakeTransport()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _tapped(ledger, 1, "allow")

    result = ask_human(_ACTION, transport=transport, ledger=ledger, timeout=5)

    assert result.decision is Decision.ALLOW
    assert result.outcome == "allowed"
    assert transport.sent[0][1] == ("Allow", "Deny")

    ask_entry = ledger.read(kind="ask")[0]
    assert ask_entry.outcome == "allowed"
    assert ask_entry.data["decision"] == "allow"
    assert ask_entry.data["action_kind"] == "bash"


def test_ask_human_explicit_deny(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _tapped(ledger, 1, "deny")

    result = ask_human(_ACTION, transport=FakeTransport(), ledger=ledger, timeout=5)

    assert result.decision is Decision.DENY
    assert result.outcome == "denied"


def test_ask_human_timeout_resolves_deny_not_allow(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")  # no callback ever recorded

    result = ask_human(_ACTION, transport=FakeTransport(), ledger=ledger, timeout=0)

    assert result.decision is Decision.DENY
    assert result.outcome == "timeout"
    assert ledger.read(kind="ask")[0].outcome == "timeout"


def test_ask_human_transport_error_fails_closed(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = ask_human(_ACTION, transport=ErrorTransport(), ledger=ledger, timeout=5)

    assert result.decision is Decision.DENY
    assert result.outcome == "timeout"
    assert result.message_id is None


def test_telegram_policy_passes_through_non_ask_decisions_untouched(tmp_path: Path) -> None:
    transport = FakeTransport()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    policy = TelegramPolicy(
        _FixedDecision(Decision.ALLOW), transport=transport, ledger=ledger, timeout=5
    )

    result = policy.evaluate(_ACTION)

    assert result.decision is Decision.ALLOW
    assert transport.sent == []  # never asked
    assert list(ledger) == []  # no ask entry logged


def test_telegram_policy_relays_ask_to_human(tmp_path: Path) -> None:
    transport = FakeTransport()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    _tapped(ledger, 1, "allow")
    policy = TelegramPolicy(_AlwaysAsk(), transport=transport, ledger=ledger, timeout=5)

    result = policy.evaluate(_ACTION)

    assert result.decision is Decision.ALLOW
    assert len(transport.sent) == 1
    assert ledger.read(kind="ask")[0].kind == "ask"
