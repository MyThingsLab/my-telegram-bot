from __future__ import annotations

import json
from pathlib import Path

from mythings.ledger import Ledger
from mythings.testers import TesterStore

from conftest import as_tester, operator
from mytelegrambot.prs_command import (
    ReadyPR,
    approve_pr,
    handle_prs,
    load_ready_prs,
    prs_buttons,
)
from mytelegrambot.router import CallbackAction

# my-fleet#67: merge from chat. my-fleet writes a small JSON snapshot of what
# it currently considers green and mergeable; this tool only ever reads it --
# it never calls `gh` and never runs `gh pr merge` itself. A tap on "Approve &
# merge" stops at recording an authorized, subject-scoped approval in the
# ledger for my-fleet's merge_ready_prs.py to consume.

_PR = {"repo": "MyThingsLab/my-fleet", "number": 67, "title": "Merge from chat", "url": "https://x/67"}


def _write_snapshot(tmp_path: Path, prs: list[dict]) -> Path:
    path = tmp_path / "ready_prs.json"
    path.write_text(json.dumps(prs))
    return path


def _tester_store(tmp_path: Path):
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=5, chat_id=999)
    return tester


# ---------------------------------------------------------------- load_ready_prs


def test_load_ready_prs_parses_the_snapshot(tmp_path: Path) -> None:
    path = _write_snapshot(tmp_path, [_PR])

    prs = load_ready_prs(path)

    assert prs == [ReadyPR(repo="MyThingsLab/my-fleet", number=67, title="Merge from chat", url="https://x/67")]
    assert prs[0].subject == "MyThingsLab/my-fleet#67"


# ---------------------------------------------------------------- handle_prs


def test_prs_lists_ready_prs_with_a_button_each(tmp_path: Path) -> None:
    path = _write_snapshot(tmp_path, [_PR])

    reply = handle_prs("", operator(), snapshot_path=path)

    assert "MyThingsLab/my-fleet#67" in reply.text
    assert "Merge from chat" in reply.text
    assert reply.inline == prs_buttons(load_ready_prs(path))
    ((label, data),) = reply.inline[0]
    assert "Merge" in label
    assert data == "pr:MyThingsLab/my-fleet#67:approve"


def test_prs_reports_an_empty_queue(tmp_path: Path) -> None:
    path = _write_snapshot(tmp_path, [])

    reply = handle_prs("", operator(), snapshot_path=path)

    assert "No PRs" in reply.text
    assert reply.inline is None


def test_prs_treats_a_missing_snapshot_file_as_an_empty_queue(tmp_path: Path) -> None:
    reply = handle_prs("", operator(), snapshot_path=tmp_path / "does-not-exist.json")

    assert "No PRs" in reply.text


def test_prs_says_so_when_unconfigured(tmp_path: Path) -> None:
    reply = handle_prs("", operator(), snapshot_path=None)

    assert "isn't wired up" in reply.text


def test_prs_flags_a_garbled_snapshot_rather_than_hiding_it(tmp_path: Path) -> None:
    path = tmp_path / "ready_prs.json"
    path.write_text("not json")

    reply = handle_prs("", operator(), snapshot_path=path)

    assert "Couldn't read" in reply.text


def test_a_tester_can_never_see_the_merge_queue(tmp_path: Path) -> None:
    path = _write_snapshot(tmp_path, [_PR])

    reply = handle_prs("", as_tester(_tester_store(tmp_path)), snapshot_path=path)

    assert "Only the operator" in reply.text


# ---------------------------------------------------------------- approve_pr


def _approve(subject: str, principal, *, snapshot_path, ledger: Ledger):
    return approve_pr(
        CallbackAction(key="pr:approve", subject=subject),
        principal,
        snapshot_path=snapshot_path,
        ledger=ledger,
    )


def test_approve_records_an_authorized_subject_scoped_approval(tmp_path: Path) -> None:
    path = _write_snapshot(tmp_path, [_PR])
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = _approve("MyThingsLab/my-fleet#67", operator(), snapshot_path=path, ledger=ledger)

    assert "Recorded" in reply.text
    assert "gh pr merge" not in reply.text.lower()  # never claims to have merged
    entry = ledger.read(kind="pr_approved")[0]
    assert entry.outcome == "success"
    assert entry.data["repo"] == "MyThingsLab/my-fleet"
    assert entry.data["number"] == 67


def test_a_tester_can_never_approve_a_merge(tmp_path: Path) -> None:
    path = _write_snapshot(tmp_path, [_PR])
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = _approve(
        "MyThingsLab/my-fleet#67",
        as_tester(_tester_store(tmp_path)),
        snapshot_path=path,
        ledger=ledger,
    )

    assert "Only the operator" in reply.text
    assert list(ledger) == []


def test_a_pr_no_longer_on_the_ready_list_is_refused_as_stale(tmp_path: Path) -> None:
    # The PR merged, was closed, or fell out of green since /prs was last shown --
    # re-reading the current snapshot is what catches this rather than trusting
    # the encoded repo#number outright.
    path = _write_snapshot(tmp_path, [])
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = _approve("MyThingsLab/my-fleet#67", operator(), snapshot_path=path, ledger=ledger)

    assert "no longer valid" in reply.text
    assert list(ledger) == []


def test_a_forged_pr_never_shown_is_refused_as_stale(tmp_path: Path) -> None:
    path = _write_snapshot(tmp_path, [_PR])
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = _approve("MyThingsLab/some-other-repo#1", operator(), snapshot_path=path, ledger=ledger)

    assert "no longer valid" in reply.text
    assert list(ledger) == []


def test_a_malformed_subject_is_refused_not_crashed(tmp_path: Path) -> None:
    path = _write_snapshot(tmp_path, [_PR])
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = _approve("garbage-no-hash", operator(), snapshot_path=path, ledger=ledger)

    assert "no longer valid" in reply.text
    assert list(ledger) == []


def test_approve_with_no_snapshot_configured_is_refused(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = _approve("MyThingsLab/my-fleet#67", operator(), snapshot_path=None, ledger=ledger)

    assert "no longer valid" in reply.text
    assert list(ledger) == []
