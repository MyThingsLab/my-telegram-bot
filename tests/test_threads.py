from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger

from mytelegrambot.threads import anchor_for, remember_anchor


def test_anchor_for_an_unseen_subject_is_none(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    assert anchor_for(ledger, "idea:12") is None


def test_remember_then_anchor_for_round_trips(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    remember_anchor(ledger, "idea:12", 100)

    assert anchor_for(ledger, "idea:12") == 100


def test_anchor_for_returns_the_most_recent_of_several(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    remember_anchor(ledger, "idea:12", 100)
    remember_anchor(ledger, "idea:12", 101)
    remember_anchor(ledger, "idea:12", 102)

    assert anchor_for(ledger, "idea:12") == 102


def test_anchor_for_keeps_subjects_separate(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    remember_anchor(ledger, "idea:12", 100)
    remember_anchor(ledger, "spend", 200)

    assert anchor_for(ledger, "idea:12") == 100
    assert anchor_for(ledger, "spend") == 200
