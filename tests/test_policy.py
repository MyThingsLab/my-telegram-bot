from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult

from conftest import ErrorTransport, FakeTransport
from mytelegrambot.policy import TelegramPolicy, ask_human

_ACTION = Action(kind="bash", payload={"command": "git push --force origin main"})


class _AlwaysAsk:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ASK, reason="destructive", rule="ask_destructive")


class _FixedDecision:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision

    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(self.decision, reason="fixed", rule="fixed")


def test_ask_human_happy_path_resolves_allow(tmp_path: Path) -> None:
    transport = FakeTransport(reply="allow")
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = ask_human(_ACTION, transport=transport, ledger=ledger, timeout=5)

    assert result.decision is Decision.ALLOW
    assert result.outcome == "allowed"
    assert transport.sent[0][1] == ("Allow", "Deny")

    entry = list(ledger)[0]
    assert entry.kind == "ask"
    assert entry.outcome == "allowed"
    assert entry.data["decision"] == "allow"
    assert entry.data["action_kind"] == "bash"


def test_ask_human_explicit_deny(tmp_path: Path) -> None:
    transport = FakeTransport(reply="deny")
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = ask_human(_ACTION, transport=transport, ledger=ledger, timeout=5)

    assert result.decision is Decision.DENY
    assert result.outcome == "denied"
    assert list(ledger)[0].outcome == "denied"


def test_ask_human_timeout_resolves_deny_not_allow(tmp_path: Path) -> None:
    transport = FakeTransport(reply=None)  # no reply within the timeout
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = ask_human(_ACTION, transport=transport, ledger=ledger, timeout=5)

    assert result.decision is Decision.DENY
    assert result.outcome == "timeout"
    assert list(ledger)[0].outcome == "timeout"


def test_ask_human_transport_error_fails_closed(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")

    result = ask_human(_ACTION, transport=ErrorTransport(), ledger=ledger, timeout=5)

    assert result.decision is Decision.DENY
    assert result.outcome == "timeout"
    assert result.message_id is None


def test_telegram_policy_passes_through_non_ask_decisions_untouched(tmp_path: Path) -> None:
    transport = FakeTransport(reply="deny")  # would deny if ever asked
    ledger = Ledger(tmp_path / "ledger.jsonl")
    policy = TelegramPolicy(
        _FixedDecision(Decision.ALLOW), transport=transport, ledger=ledger, timeout=5
    )

    result = policy.evaluate(_ACTION)

    assert result.decision is Decision.ALLOW
    assert transport.sent == []  # never asked
    assert list(ledger) == []  # no ask entry logged


def test_telegram_policy_relays_ask_to_human(tmp_path: Path) -> None:
    transport = FakeTransport(reply="allow")
    ledger = Ledger(tmp_path / "ledger.jsonl")
    policy = TelegramPolicy(_AlwaysAsk(), transport=transport, ledger=ledger, timeout=5)

    result = policy.evaluate(_ACTION)

    assert result.decision is Decision.ALLOW
    assert len(transport.sent) == 1
    assert list(ledger)[0].kind == "ask"
