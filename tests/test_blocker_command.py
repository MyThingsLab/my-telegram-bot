from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger

from conftest import as_tester, operator
from mytelegrambot.blocker_command import (
    blocker_alert_buttons,
    blocker_alert_text,
    handle_blocker_retry,
    handle_blocker_skip,
    handle_blocker_take,
    send_blocker_alert,
)
from mytelegrambot.router import CallbackAction


class FakeTransport:
    def __init__(self) -> None:
        self.sent: list[tuple[str, object]] = []

    def send_message(self, text, *, keyboard=None, inline=None):
        self.sent.append((text, inline))
        return 7


def _tester_store(tmp_path: Path):
    from mythings.testers import TesterStore

    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=5, chat_id=999)
    return tester


def test_blocker_alert_text_reports_the_candidate_and_attempt() -> None:
    text = blocker_alert_text(candidate="my-guard#3", detail="gave up", attempt=3)
    assert "my-guard#3" in text
    assert "3 attempt" in text
    assert "gave up" in text


def test_blocker_alert_buttons_encode_the_candidate() -> None:
    buttons = blocker_alert_buttons("my-guard#3")
    data = {d for row in buttons for _, d in row}
    assert "blocker:my-guard#3:retry" in data
    assert "blocker:my-guard#3:skip" in data
    assert "blocker:my-guard#3:take" in data


def test_send_blocker_alert_pushes_and_records(tmp_path: Path) -> None:
    transport = FakeTransport()
    ledger = Ledger(tmp_path / "l.jsonl")

    message_id = send_blocker_alert(
        candidate="my-guard#3", detail="gave up", attempt=3, transport=transport, ledger=ledger
    )

    assert message_id == 7
    assert len(transport.sent) == 1
    entry = ledger.read(kind="blocker_alert")[0]
    assert entry.outcome == "success"
    assert entry.data["candidate"] == "my-guard#3"


def test_retry_records_the_decision(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_blocker_retry(
        CallbackAction(key="blocker:retry", subject="my-guard#3"), operator(), ledger=ledger
    )

    assert "Retry recorded" in reply.text
    entry = ledger.read(kind="blocker_decision")[0]
    assert entry.outcome == "retry"
    assert entry.data["candidate"] == "my-guard#3"


def test_skip_records_the_decision(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_blocker_skip(
        CallbackAction(key="blocker:skip", subject="my-guard#3"), operator(), ledger=ledger
    )

    assert "skipped" in reply.text
    assert ledger.read(kind="blocker_decision")[0].outcome == "skip"


def test_take_records_the_decision(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_blocker_take(
        CallbackAction(key="blocker:take", subject="my-guard#3"), operator(), ledger=ledger
    )

    assert "yours" in reply.text
    assert ledger.read(kind="blocker_decision")[0].outcome == "take"


def test_a_tester_can_never_act_on_a_blocker(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    tester = as_tester(_tester_store(tmp_path))

    reply = handle_blocker_retry(
        CallbackAction(key="blocker:retry", subject="my-guard#3"), tester, ledger=ledger
    )

    assert "Only the operator" in reply.text
    assert list(ledger) == []


def test_a_stale_blocker_button_does_not_crash(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_blocker_retry(
        CallbackAction(key="blocker:retry", subject=""), operator(), ledger=ledger
    )

    assert "no longer valid" in reply.text
    assert list(ledger) == []
