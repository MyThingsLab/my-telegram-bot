from __future__ import annotations

import sys
from pathlib import Path

from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult

from conftest import as_tester, operator
from mytelegrambot.halt_command import HaltControl, handle_halt, handle_resume

# The fleet kill switch, reachable from a phone. It was a marker file you `touch`
# from a terminal -- unreachable exactly when the unattended, billed loop is
# running and you are away from the machine.
#
# It is a CLI hand-off: the bot runs a configured command and never learns what
# fleet_dispatch is, where it lives, or that a marker file exists.


class _AllowAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ALLOW, reason="ok", rule="allow")


class _DenyAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY, reason="no", rule="deny")


class _AskAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ASK, reason="confirm", rule="ask")


def _recording_control(tmp_path: Path, *, exit_code: int = 0, output: str = "HALT armed") -> tuple:
    # A stand-in for `python3 fleet_dispatch.py`, recording the flags it is handed.
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


def test_halt_hands_the_arm_flag_to_the_configured_command(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_halt("", operator(), control=control, policy=_AllowAll(), ledger=ledger)

    assert seen.read_text() == "--abort"
    assert "HALT armed" in reply.text  # the command's own words, not our narration
    assert ledger.read(kind="halt")[0].outcome == "success"


def test_resume_hands_the_clear_flag(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path, output="HALT marker cleared")
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_resume("", operator(), control=control, policy=_AllowAll(), ledger=ledger)

    assert seen.read_text() == "--clear-halt"
    assert "cleared" in reply.text
    assert ledger.read(kind="resume")[0].outcome == "success"


def test_the_reply_relays_the_commands_own_words_and_never_narrates_over_them(
    tmp_path: Path,
) -> None:
    # This tool does not compose prose over what it relays. Claiming "fleet halted"
    # when the command said something else is exactly the hallucination that rule
    # exists to prevent.
    control, _ = _recording_control(tmp_path, output="no HALT marker was set")
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_resume("", operator(), control=control, policy=_AllowAll(), ledger=ledger)

    assert reply.text == "no HALT marker was set"


def test_a_tester_can_never_halt_the_fleet(tmp_path: Path) -> None:
    # A tester who could stop every worker would be a denial-of-service with a chat
    # account. The command must not even run.
    control, seen = _recording_control(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_halt(
        "", as_tester(_tester_store(tmp_path)), control=control, policy=_AllowAll(), ledger=ledger
    )

    assert "Only the operator" in reply.text
    assert not seen.exists()  # the command never ran
    assert list(ledger) == []


def test_a_halt_denied_by_policy_never_runs_the_command(tmp_path: Path) -> None:
    control, seen = _recording_control(tmp_path)
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_halt("", operator(), control=control, policy=_DenyAll(), ledger=ledger)

    assert "denied by policy" in reply.text
    assert not seen.exists()


def test_an_ask_fails_closed_because_the_daemon_is_unattended(tmp_path: Path) -> None:
    # The daemon cannot service an ASK (it would deadlock against itself -- see the
    # ask=None wiring in cli.py), so an ASK collapses to DENY, the same posture
    # every other handler here takes.
    control, seen = _recording_control(tmp_path)

    reply = handle_halt(
        "", operator(), control=control, policy=_AskAll(), ledger=Ledger(tmp_path / "l.jsonl")
    )

    assert "denied by policy" in reply.text
    assert not seen.exists()


def test_an_unconfigured_kill_switch_says_so_rather_than_pretending(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_halt("", operator(), control=None, policy=_AllowAll(), ledger=ledger)

    assert "isn't wired up" in reply.text
    assert list(ledger) == []


def test_a_failing_command_is_reported_as_a_failure_not_a_halt(tmp_path: Path) -> None:
    # The dangerous lie would be telling the operator the fleet is stopped when it
    # is not. A non-zero exit is a failure, and it is recorded as one.
    control, _ = _recording_control(tmp_path, exit_code=1, output="could not write marker")
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_halt("", operator(), control=control, policy=_AllowAll(), ledger=ledger)

    assert "Couldn't halt" in reply.text
    assert "could not write marker" in reply.text
    assert ledger.read(kind="halt")[0].outcome == "failure"


def test_a_missing_command_denies_rather_than_crashing_the_daemon(tmp_path: Path) -> None:
    control = HaltControl("definitely-not-a-real-binary-xyz")
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_halt("", operator(), control=control, policy=_AllowAll(), ledger=ledger)

    assert "Couldn't halt" in reply.text
    assert ledger.read(kind="halt")[0].outcome == "failure"


def test_a_hung_command_cannot_wedge_the_single_threaded_daemon(tmp_path: Path) -> None:
    # The daemon processes updates one at a time, and it is also the fleet's ask
    # channel: a halt command that hangs forever would stall every worker's
    # escalation behind it.
    control = HaltControl(f"{sys.executable} -c 'import time; time.sleep(30)'", timeout=0.2)
    ledger = Ledger(tmp_path / "l.jsonl")

    reply = handle_halt("", operator(), control=control, policy=_AllowAll(), ledger=ledger)

    assert "Couldn't halt" in reply.text
    assert ledger.read(kind="halt")[0].outcome == "failure"
