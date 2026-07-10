from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger

from mytelegrambot.status_command import build_status


def _ledger(tmp_path: Path) -> Ledger:
    return Ledger(tmp_path / "ledger.jsonl")


def test_status_reports_nothing_on_an_empty_ledger(tmp_path: Path) -> None:
    assert "No activity recorded yet" in build_status(_ledger(tmp_path))


def test_status_counts_ideas_asks_and_the_last_poll(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.record("myidea", "idea_filed", "success", detail="filed idea #1: a tool")
    ledger.record("myidea", "idea_filed", "success", detail="filed idea #2: another")
    ledger.record("mytelegrambot", "ask", "allowed", action_kind="bash")
    ledger.record("mytelegrambot", "ask", "denied", action_kind="bash")
    ledger.record("mytelegrambot", "notify", "success", detail="pushed 3 entries (incremental)")
    ledger.record("mytelegrambot", "poll", "success", last_update_id=42)

    status = build_status(ledger)

    assert "Ideas filed via chat: 2" in status
    assert "Approvals asked: 2 (1 allowed · 1 denied · 0 timed out)" in status
    assert "pushed 3 entries" in status
    assert "cursor #42" in status


def test_status_handles_a_ledger_with_no_digest_or_poll_yet(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.record("myidea", "idea_filed", "success", detail="filed idea #1: a tool")

    status = build_status(ledger)

    assert "Ideas filed via chat: 1" in status
    assert "Last digest: none pushed yet" in status
