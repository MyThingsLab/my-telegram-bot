from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from mythings.ledger import Ledger

from mytelegrambot.authz import Principal
from mytelegrambot.router import CallbackAction, InlineKeyboard, Reply, encode_action

# my-fleet#67: merge from chat. my-fleet's own merge_ready_prs.py already
# decides what is green and mergeable (`PR.ready`) and blocks on
# `mytelegrambot ask` for each one, one at a time, inside a fleet run's own
# budget window -- but there was no operator-initiated path: nothing let a
# human open Telegram and ask "what's ready right now" without a fleet --ask
# pass already in flight. /prs is that missing direction.
#
# This tool's authority stays fixed at "comms only, fail-closed" (see
# my-fleet/workspace/README.md's kernel table): it never calls `gh` itself and
# never runs `gh pr merge` -- that stays my-fleet's alone, gated by Guard's
# pr-merge Action. my-fleet periodically writes a small JSON snapshot of what
# it currently considers ready; this module only reads that file. A tap on
# "Approve & merge" therefore stops at recording an authorized, subject-scoped
# approval in the ledger -- my-fleet's own merge_ready_prs.py (or a thin entry
# point there) is what consumes that record and performs the actual merge
# through its existing Guard/pr-merge seam.
#
# Snapshot schema (written by my-fleet, read-only here): a JSON array of
# objects, each `{"repo": "owner/name", "number": 67, "title": "...", "url":
# "https://github.com/..."}`.

_SELF_TOOL = "mytelegrambot"

_NOT_THE_OPERATOR = "Only the operator can see or approve the merge queue."
_NOT_CONFIGURED = (
    "The merge queue isn't wired up on this bot.\n"
    "Start the daemon with --prs-snapshot pointing at my-fleet's ready-PR file."
)
_UNREADABLE = "Couldn't read the ready-PR snapshot — check my-fleet's writer."
_EMPTY = "No PRs are currently green and mergeable."
_STALE_BUTTON = "That button is no longer valid — the PR isn't on the current ready list."


@dataclass(frozen=True)
class ReadyPR:
    repo: str
    number: int
    title: str
    url: str

    @property
    def subject(self) -> str:
        return f"{self.repo}#{self.number}"


def load_ready_prs(path: Path) -> list[ReadyPR]:
    raw = json.loads(path.read_text())
    return [
        ReadyPR(repo=e["repo"], number=int(e["number"]), title=e["title"], url=e["url"])
        for e in raw
    ]


def _snapshot(path: Path) -> list[ReadyPR] | None:
    # None means "unreadable" (missing keys, malformed JSON) -- distinct from an
    # empty list ("no file yet, or my-fleet says nothing is ready"). A broken
    # writer must not silently read back as an empty, healthy queue.
    if not path.exists():
        return []
    try:
        return load_ready_prs(path)
    except (OSError, ValueError, KeyError, TypeError):
        return None


def _parse_subject(subject: str) -> tuple[str, int] | None:
    # "MyThingsLab/my-fleet#67" -> ("MyThingsLab/my-fleet", 67). rpartition, not
    # split, because the repo half itself contains a "/".
    repo, sep, number_text = subject.rpartition("#")
    if not sep or not repo:
        return None
    try:
        return repo, int(number_text)
    except ValueError:
        return None


def _pr_line(pr: ReadyPR) -> str:
    return f"• [{pr.subject}]({pr.url}) {pr.title}"


def prs_buttons(prs: list[ReadyPR]) -> InlineKeyboard | None:
    if not prs:
        return None
    # One button per row: labels carry each PR's own subject since Telegram
    # renders buttons below the whole message, not alongside the line they act on.
    return tuple(
        (("✅ Merge " + pr.subject, encode_action("pr", pr.subject, "approve")),) for pr in prs
    )


def handle_prs(_args: str, principal: Principal, *, snapshot_path: Path | None) -> Reply:
    # Operator only: this is an internal fleet-management view (repo names, PR
    # numbers of in-flight org work), the same posture /halt already takes on
    # the fleet kill switch -- and the only principal a merge button can ever
    # be shown to.
    if not principal.is_operator:
        return Reply(_NOT_THE_OPERATOR)
    if snapshot_path is None:
        return Reply(_NOT_CONFIGURED)
    prs = _snapshot(snapshot_path)
    if prs is None:
        return Reply(_UNREADABLE)
    if not prs:
        return Reply(_EMPTY)

    lines = ["*🟢 Ready to merge*", "", *[_pr_line(pr) for pr in prs]]
    return Reply("\n".join(lines), inline=prs_buttons(prs), markdown=True)


def approve_pr(
    action: CallbackAction,
    principal: Principal,
    *,
    snapshot_path: Path | None,
    ledger: Ledger,
) -> Reply:
    # callback_data is client-supplied (see idea_command's close/explore):
    # Telegram delivers whatever a client sends for a message it can see. The
    # actor has to be checked -- only the operator is ever shown this button --
    # and so does the subject: re-reading the *current* snapshot, rather than
    # trusting the encoded repo#number outright, is what refuses a stale button
    # (the PR already merged, or fell out of green) or a forged one naming a PR
    # that was never on any ready list.
    if not principal.is_operator:
        return Reply(_NOT_THE_OPERATOR)
    parsed = _parse_subject(action.subject)
    if parsed is None:
        return Reply(_STALE_BUTTON)
    if snapshot_path is None:
        return Reply(_STALE_BUTTON)
    repo, number = parsed
    prs = _snapshot(snapshot_path) or []
    match = next((pr for pr in prs if pr.repo == repo and pr.number == number), None)
    if match is None:
        return Reply(_STALE_BUTTON)

    # Recording is this tool's whole job here: it never calls `gh pr merge`
    # itself. my-fleet's own merge_ready_prs.py reads this entry and performs
    # the actual merge through Guard's pr-merge gate.
    ledger.record(
        _SELF_TOOL,
        "pr_approved",
        "success",
        detail=f"approved {match.subject} to merge from chat: {match.title}",
        repo=repo,
        number=number,
        title=match.title,
        url=match.url,
    )
    return Reply(
        f"✅ Recorded: {match.subject} approved to merge.\n"
        "my-fleet still performs the merge itself, through Guard's pr-merge gate."
    )
