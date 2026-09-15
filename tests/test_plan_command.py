from __future__ import annotations

import sys
from pathlib import Path

from mythings.ledger import Ledger

from conftest import as_tester, operator
from mytelegrambot.halt_command import HaltControl
from mytelegrambot.plan_command import (
    handle_plan,
    handle_plan_approve,
    handle_plan_reorder,
    handle_plan_skip,
    plan_buttons,
)
from mytelegrambot.router import CallbackAction

# my-fleet#66: the operator's say over myplanner's recommended build sequence
# before myorchestrator's `next` acts on it unattended.
#
# /plan is a CLI hand-off, exactly like /halt: the bot never imports myplanner,
# only runs `--plan-cmd` and relays its stdout verbatim.


def _recording_control(tmp_path: Path, *, exit_code: int = 0, output: str = "1. my-idea#12") -> tuple:
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


def test_plan_buttons_carry_approve_reorder_and_skip() -> None:
    data = {d for row in plan_buttons() for _, d in row}
    assert "plan:current:approve" in data
    assert "plan:current:reorder" in data
    assert "plan:current:skip" in data


def test_plan_renders_the_commands_own_words_with_no_flags(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path, output="1. my-idea#12\n2. my-guard#7")

    reply = handle_plan("", operator(), control=control)

    assert seen.read_text() == ""  # /plan hands over no flags, unlike /halt's verbs
    assert reply.text == "1. my-idea#12\n2. my-guard#7"
    assert reply.inline is not None


def test_plan_reply_never_narrates_over_the_commands_own_words(tmp_path: Path) -> None:
    control, _ = _recording_control(tmp_path, output="nothing queued right now")

    reply = handle_plan("", operator(), control=control)

    assert reply.text == "nothing queued right now"


def test_a_tester_can_never_read_the_fleet_plan(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path)

    reply = handle_plan("", as_tester(_tester_store(tmp_path)), control=control)

    assert "Only the operator" in reply.text
    assert not seen.exists()


def test_an_unconfigured_plan_command_says_so_rather_than_pretending() -> None:
    reply = handle_plan("", operator(), control=None)

    assert "isn't wired up" in reply.text


def test_a_failing_plan_command_is_reported_as_a_failure(tmp_path: Path) -> None:
    control, _ = _recording_control(tmp_path, exit_code=1, output="myplanner: no plan file")

    reply = handle_plan("", operator(), control=control)

    assert "Couldn't read the plan" in reply.text
    assert "no plan file" in reply.text


def test_approve_records_the_decision(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_plan_approve(
        CallbackAction(key="plan:approve", subject="current"), operator(), ledger=ledger
    )

    assert "Approved" in reply.text
    entry = ledger.read(kind="plan_decision")[0]
    assert entry.outcome == "approve"


def test_reorder_records_the_decision_without_claiming_a_new_order(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_plan_reorder(
        CallbackAction(key="plan:reorder", subject="current"), operator(), ledger=ledger
    )

    assert "Reorder requested" in reply.text
    assert ledger.read(kind="plan_decision")[0].outcome == "reorder"


def test_skip_records_the_decision_for_the_next_item(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_plan_skip(
        CallbackAction(key="plan:skip", subject="current"), operator(), ledger=ledger
    )

    assert "Skipping the next item" in reply.text
    assert ledger.read(kind="plan_decision")[0].outcome == "skip"


def test_a_tester_can_never_act_on_the_plan(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    tester = as_tester(_tester_store(tmp_path))

    reply = handle_plan_approve(
        CallbackAction(key="plan:approve", subject="current"), tester, ledger=ledger
    )

    assert "Only the operator" in reply.text
    assert list(ledger) == []


def test_an_unanswered_plan_prompt_records_nothing(tmp_path: Path) -> None:
    # No button tap means no ledger entry at all -- myorchestrator's `next` must
    # see exactly the state it would have seen had /plan never been sent, the same
    # fail-closed shape as an unanswered `ask` collapsing to DENY rather than a
    # silent default order.
    control, _ = _recording_control(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")

    handle_plan("", operator(), control=control)

    assert list(ledger) == []
