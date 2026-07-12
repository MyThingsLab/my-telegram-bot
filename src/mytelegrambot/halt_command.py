from __future__ import annotations

import shlex
import subprocess

from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy

from mytelegrambot.authz import Principal
from mytelegrambot.router import Reply

# The fleet's kill switch, reachable from a phone.
#
# It was a marker file you `touch` from a terminal -- so the single most
# safety-critical control in the fleet was unreachable exactly when the unattended,
# billed loop was running and you were away from the machine. That is the wrong way
# round, and it is why this ships in the bot rather than waiting for MyDirector:
# halting needs no Engine call, composes no prose, and makes no judgment.
#
# It is a **CLI hand-off**, the fleet's normal cross-tool relationship: the bot runs
# a configured command and never learns what fleet_dispatch is, where it lives, or
# that a marker file exists. `--halt-cmd` names the base command; this appends the
# verb's flag. No import, no Engine, no Workspace.

_SELF_TOOL = "mytelegrambot"

# fleet_dispatch's own flags. The bot knows these two strings and nothing else
# about the fleet's internals.
_ARM = "--abort"
_CLEAR = "--clear-halt"

_NOT_CONFIGURED = (
    "The kill switch isn't wired up on this bot.\n"
    "Start the daemon with --halt-cmd pointing at fleet_dispatch.py."
)
_NOT_THE_OPERATOR = "Only the operator can halt the fleet."

# Bounded because the daemon is single-threaded: a hung halt command would block
# every other command, and the ask channel with them. Halting is a local file
# touch, so this is generous.
_TIMEOUT = 30.0


class HaltControl:
    def __init__(self, command: str, *, timeout: float = _TIMEOUT) -> None:
        self.command = command
        self.timeout = timeout

    def run(self, flag: str) -> tuple[bool, str]:
        argv = [*shlex.split(self.command), flag]
        try:
            proc = subprocess.run(
                argv, capture_output=True, text=True, timeout=self.timeout, check=False
            )
        except (OSError, subprocess.SubprocessError) as exc:
            return False, f"{type(exc).__name__}"
        output = (proc.stdout or proc.stderr).strip()
        return proc.returncode == 0, output


def _gate(action_kind: str, *, policy: Policy) -> bool:
    # Every write in this fleet passes the Policy seam, and halting is a write. The
    # daemon is unattended, so an ASK it cannot service collapses to DENY -- the
    # same fail-closed posture every other handler here takes.
    #
    # Note the direction this fails in: a policy that cannot decide will *refuse to
    # halt*. That is the uncomfortable one, but it is the honest one -- the
    # alternative is a policy bug that can stop the fleet, and `--abort` on the
    # command line always works regardless.
    decision = policy.evaluate(Action(kind=action_kind, payload={})).under(unattended=True)
    return decision is Decision.ALLOW


def _handle(
    verb: str,
    flag: str,
    action_kind: str,
    principal: Principal,
    *,
    control: HaltControl | None,
    policy: Policy,
    ledger: Ledger,
) -> Reply:
    # Halting the fleet is the operator's alone. A tester who could stop every
    # worker would be a denial-of-service with a chat account.
    if not principal.is_operator:
        return Reply(_NOT_THE_OPERATOR)
    if control is None:
        return Reply(_NOT_CONFIGURED)
    if not _gate(action_kind, policy=policy):
        return Reply(f"{verb.capitalize()} was denied by policy.")

    ok, output = control.run(flag)
    ledger.record(
        _SELF_TOOL,
        "halt" if flag == _ARM else "resume",
        "success" if ok else "failure",
        detail=f"{verb} from chat: {output or 'no output'}",
    )
    if not ok:
        return Reply(f"Couldn't {verb} the fleet — the command failed.\n{output}")
    # Relay the command's own words rather than composing a claim about what
    # happened: this tool does not narrate over what it relays, and a confident
    # "fleet halted" the command never actually said is exactly the hallucination
    # that rule exists to prevent.
    return Reply(output or f"Fleet {verb} requested.")


def handle_halt(
    _args: str,
    principal: Principal,
    *,
    control: HaltControl | None,
    policy: Policy,
    ledger: Ledger,
) -> Reply:
    return _handle(
        "halt", _ARM, "fleet-halt", principal, control=control, policy=policy, ledger=ledger
    )


def handle_resume(
    _args: str,
    principal: Principal,
    *,
    control: HaltControl | None,
    policy: Policy,
    ledger: Ledger,
) -> Reply:
    return _handle(
        "resume", _CLEAR, "fleet-resume", principal, control=control, policy=policy, ledger=ledger
    )
