from __future__ import annotations

import sys
from pathlib import Path

from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult

from conftest import as_tester, operator
from mytelegrambot.halt_command import HaltControl
from mytelegrambot.router import CallbackAction
from mytelegrambot.spend_command import (
    handle_spend_halt,
    handle_spend_raise,
    send_spend_alert,
    spend_alert_buttons,
    spend_alert_text,
)


class _AllowAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ALLOW, reason="ok", rule="allow")


class _DenyAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY, reason="no", rule="deny")


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    def send_message(self, text, *, keyboard=None, inline=None):
        self.sent.append((text, inline))
        return 42


def _recording_control(tmp_path: Path, *, exit_code: int = 0, output: str = "done") -> tuple:
    seen = tmp_path / "flags.txt"
    script = (
        "import sys, pathlib;"
        f"pathlib.Path({str(seen)!r}).write_text(' '.join(sys.argv[1:]));"
        f"print({output!r});"
        f"sys.exit({exit_code})"
    )
    return HaltControl(f"{sys.executable} -c {script!r}"), seen


def _tester_store(tmp_path: Path):
    from mythings.testers import TesterStore

    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=5, chat_id=999)
    return tester


def test_spend_alert_text_reports_the_fraction() -> None:
    text = spend_alert_text(spent=16.0, cap=20.0)
    assert "$16.00" in text
    assert "$20.00" in text
    assert "80%" in text


def test_spend_alert_buttons_encode_the_raised_amount() -> None:
    buttons = spend_alert_buttons(30.0)
    labels = {label for row in buttons for label, _ in row}
    data = {data for row in buttons for _, data in row}
    assert any("Halt" in label for label in labels)
    assert any("30" in label for label in labels)
    assert "spend:now:halt" in data
    assert "spend:30.00:raise" in data


def test_send_spend_alert_pushes_the_message_and_records_the_ledger(tmp_path: Path) -> None:
    transport = FakeTransport()
    ledger = Ledger(tmp_path / "l.jsonl")

    message_id = send_spend_alert(
        spent=16.0, cap=20.0, raise_to=30.0, transport=transport, ledger=ledger
    )

    assert message_id == 42
    assert len(transport.sent) == 1
    entry = ledger.read(kind="spend_alert")[0]
    assert entry.outcome == "success"
    assert entry.data["spent_usd"] == 16.0
    assert entry.data["cap_usd"] == 20.0


def test_spend_halt_hands_the_abort_flag_to_the_configured_command(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path, output="HALT armed")
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_spend_halt(
        CallbackAction(key="spend:halt", subject="now"),
        operator(),
        control=control,
        policy=_AllowAll(),
        ledger=ledger,
    )

    assert seen.read_text() == "--abort"
    assert "HALT armed" in reply.text
    assert ledger.read(kind="halt")[0].outcome == "success"


def test_spend_raise_hands_the_amount_to_the_configured_command(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path, output="daily cap override armed")
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_spend_raise(
        CallbackAction(key="spend:raise", subject="30.00"),
        operator(),
        control=control,
        policy=_AllowAll(),
        ledger=ledger,
    )

    assert seen.read_text() == "--raise-daily-cap 30.00"
    assert "daily cap override armed" in reply.text
    entry = ledger.read(kind="raise_cap")[0]
    assert entry.outcome == "success"
    assert entry.data["cap_usd"] == 30.0


def test_a_tester_can_never_halt_or_raise_the_cap(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")
    tester = as_tester(_tester_store(tmp_path))

    halt_reply = handle_spend_halt(
        CallbackAction(key="spend:halt", subject="now"),
        tester,
        control=control,
        policy=_AllowAll(),
        ledger=ledger,
    )
    raise_reply = handle_spend_raise(
        CallbackAction(key="spend:raise", subject="30.00"),
        tester,
        control=control,
        policy=_AllowAll(),
        ledger=ledger,
    )

    assert "Only the operator" in halt_reply.text
    assert "Only the operator" in raise_reply.text
    assert not seen.exists()


def test_spend_raise_denied_by_policy_never_runs_the_command(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_spend_raise(
        CallbackAction(key="spend:raise", subject="30.00"),
        operator(),
        control=control,
        policy=_DenyAll(),
        ledger=ledger,
    )

    assert "denied by policy" in reply.text
    assert not seen.exists()


def test_a_stale_raise_button_does_not_crash(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_spend_raise(
        CallbackAction(key="spend:raise", subject="not-a-number"),
        operator(),
        control=control,
        policy=_AllowAll(),
        ledger=ledger,
    )

    assert "no longer valid" in reply.text
    assert not seen.exists()


def test_spend_halt_denied_by_policy_never_runs_the_command(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_spend_halt(
        CallbackAction(key="spend:halt", subject="now"),
        operator(),
        control=control,
        policy=_DenyAll(),
        ledger=ledger,
    )

    assert "denied by policy" in reply.text
    assert not seen.exists()


def test_a_failing_halt_command_is_reported_as_a_failure(tmp_path: Path) -> None:
    control, _ = _recording_control(tmp_path, exit_code=1, output="could not write marker")
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_spend_halt(
        CallbackAction(key="spend:halt", subject="now"),
        operator(),
        control=control,
        policy=_AllowAll(),
        ledger=ledger,
    )

    assert "Couldn't halt" in reply.text
    assert "could not write marker" in reply.text
    assert ledger.read(kind="halt")[0].outcome == "failure"


def test_a_failing_raise_command_is_reported_as_a_failure(tmp_path: Path) -> None:
    control, _ = _recording_control(tmp_path, exit_code=1, output="could not write override")
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_spend_raise(
        CallbackAction(key="spend:raise", subject="30.00"),
        operator(),
        control=control,
        policy=_AllowAll(),
        ledger=ledger,
    )

    assert "Couldn't raise the cap" in reply.text
    assert "could not write override" in reply.text
    assert ledger.read(kind="raise_cap")[0].outcome == "failure"


def test_an_unconfigured_control_says_so_rather_than_pretending(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_spend_halt(
        CallbackAction(key="spend:halt", subject="now"),
        operator(),
        control=None,
        policy=_AllowAll(),
        ledger=ledger,
    )

    assert "isn't wired up" in reply.text
    assert list(ledger) == []
