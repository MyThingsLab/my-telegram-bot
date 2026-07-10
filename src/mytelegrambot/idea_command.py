from __future__ import annotations

import subprocess

from myidea.explore import Runner, explore, file_idea
from mythings.engine import Engine
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, Policy
from mythings.testers import TesterStore

from mytelegrambot.authz import Principal, release_engine_call, reserve_engine_call
from mytelegrambot.router import CallbackAction, InlineKeyboard, Reply, encode_action

# myidea new's own CLI defaults to the cwd's repo via `gh`'s ambient detection;
# that only works when invoked from inside some repo's checkout. `mytelegrambot
# run` is a daemon with an unspecified cwd, so this needs an explicit default
# instead of relying on that implicit one.
DEFAULT_IDEA_REPO = "MyThingsLab/my-idea"

_NO_BODY_FALLBACK = "(filed via Telegram /idea; no additional detail provided)"
_TELEGRAM_MAX_LEN = 4096

_QUOTA_EXHAUSTED = (
    "You've used your full allowance of explored ideas. Nothing was filed.\n"
    "Ask the operator to raise your quota if you need more."
)

_NOT_YOUR_IDEA = "That idea isn't one of yours."
_STALE_BUTTON = "That button is no longer valid."


def _may_act_on(principal: Principal, ledger: Ledger, number: int) -> bool:
    # callback_data is client-supplied: Telegram will deliver whatever a client
    # sends for a message it can see, so a tester could hand-craft
    # "idea:<any>:close" for an idea they never filed. Both button actions are
    # GitHub writes, so the subject has to be checked, not just the actor.
    #
    # A tester's ledger only ever contains their own ideas (authz.ledger_for
    # hands each one a private file), so it is the ownership record. The operator
    # owns the fleet and is exempt.
    if principal.is_operator:
        return True
    return any(e.data.get("idea_issue") == number for e in ledger.read(kind="idea_filed"))


def _gh(argv: list[str]) -> str:
    # The gh subprocess is this tool's second system boundary (after the Telegram
    # HTTP call), and the one mocked in tests. Mirrors myidea's own private
    # runner rather than importing it across a repo boundary.
    proc = subprocess.run(["gh", *argv], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"gh {' '.join(argv)} failed: {proc.stderr.strip()}")
    return proc.stdout


def idea_buttons(number: int) -> InlineKeyboard:
    # Hung under every /idea reply and under every re-explore, so the thread stays
    # actionable without typing another command.
    return (
        (
            ("🔍 Explore deeper", encode_action("idea", number, "explore")),
            ("✓ Close idea", encode_action("idea", number, "close")),
        ),
    )


def _split_title_body(args_text: str) -> tuple[str, str]:
    title, _, rest = args_text.strip().partition("\n")
    return title.strip(), rest.strip()


def _truncate_for_telegram(text: str) -> str:
    if len(text) <= _TELEGRAM_MAX_LEN:
        return text
    marker = "\n…[truncated]"
    return text[: _TELEGRAM_MAX_LEN - len(marker)] + marker


def handle_idea(
    args_text: str,
    *,
    github: GitHub,
    policy: Policy,
    engine: Engine,
    ledger: Ledger,
    repo: str | None,
    runner: Runner | None = None,
) -> Reply:
    # `runner` is an override for tests only: file_idea/explore already default
    # to a real `gh` subprocess when it's omitted, which is the correct
    # production behavior (the same real `gh` the `github` object itself
    # shells out to).
    runner_kwargs = {"runner": runner} if runner is not None else {}

    title, body = _split_title_body(args_text)
    if not title:
        return Reply("Usage: /idea <title>\n(optionally followed by more detail on later lines)")

    created = file_idea(
        title=title,
        body=body or _NO_BODY_FALLBACK,
        github=github,
        policy=policy,
        ledger=ledger,
        **runner_kwargs,
    )
    if created is None:
        return Reply("Idea filing was denied by policy — nothing was created.")

    result = explore(
        issue=created.number,
        engine=engine,
        github=github,
        policy=policy,
        ledger=ledger,
        repo=repo,
        **runner_kwargs,
    )
    text = f"Filed as my-idea#{created.number} — {created.url}\n\n{result.comment}"
    if not result.posted:
        text += "\n(Note: the brief above could not be posted as a GitHub comment.)"
    return Reply(_truncate_for_telegram(text), inline=idea_buttons(created.number))


def metered_idea(
    args_text: str,
    principal: Principal,
    *,
    store: TesterStore | None,
    github: GitHub,
    policy: Policy,
    engine: Engine,
    ledger: Ledger,
    repo: str | None,
    runner: Runner | None = None,
) -> Reply:
    # /idea and the "Explore deeper" button are the tool's only Engine-spending
    # paths, so they are the only metered ones. The reservation is taken *before*
    # the call and refunded only if the call never happened (an exception), never
    # after a successful spend -- so a crash over-counts against the tester rather
    # than letting an unbilled call through. The operator is never metered.
    #
    # Accepted: a policy-denied filing still consumes one reservation. It errs in
    # the safe direction, and the alternative is sniffing the reply text.
    if not reserve_engine_call(principal, store):
        return Reply(_QUOTA_EXHAUSTED)
    try:
        return handle_idea(
            args_text,
            github=github,
            policy=policy,
            engine=engine,
            ledger=ledger,
            repo=repo,
            runner=runner,
        )
    except Exception:
        release_engine_call(principal, store)
        raise


def explore_idea(
    action: CallbackAction,
    principal: Principal,
    *,
    store: TesterStore | None,
    github: GitHub,
    policy: Policy,
    engine: Engine,
    ledger: Ledger,
    repo: str | None,
    runner: Runner | None = None,
) -> Reply:
    # "Explore deeper": one more Engine call on an existing idea, delegated whole
    # to MyIdea's explore(). Metered exactly like /idea -- a button must not be a
    # way around the quota.
    number = action.as_int()
    if number is None:
        return Reply(_STALE_BUTTON)
    if not _may_act_on(principal, ledger, number):
        return Reply(_NOT_YOUR_IDEA)
    if not reserve_engine_call(principal, store):
        return Reply(_QUOTA_EXHAUSTED)
    runner_kwargs = {"runner": runner} if runner is not None else {}
    try:
        result = explore(
            issue=number,
            engine=engine,
            github=github,
            policy=policy,
            ledger=ledger,
            repo=repo,
            **runner_kwargs,
        )
    except Exception:
        release_engine_call(principal, store)
        raise
    text = f"Re-explored my-idea#{number}\n\n{result.comment}"
    if not result.posted:
        text += "\n(Note: the brief above could not be posted as a GitHub comment.)"
    return Reply(_truncate_for_telegram(text), inline=idea_buttons(number))


def close_idea(
    action: CallbackAction,
    principal: Principal,
    *,
    policy: Policy,
    ledger: Ledger,
    repo: str | None,
    runner: Runner = _gh,
) -> Reply:
    # No Engine call, so no quota -- but it is a GitHub write, and every GitHub
    # write in this fleet passes the Policy seam first, exactly as file_idea does.
    number = action.as_int()
    if number is None:
        return Reply(_STALE_BUTTON)
    if not _may_act_on(principal, ledger, number):
        return Reply(_NOT_YOUR_IDEA)
    gate = Action(kind="issue-close", payload={"issue": number, "repo": repo or ""})
    if policy.evaluate(gate).under(unattended=True) is not Decision.ALLOW:
        return Reply(f"Closing my-idea#{number} was denied by policy.")

    argv = ["issue", "close", str(number)]
    if repo:
        argv += ["--repo", repo]
    runner(argv)

    ledger.record(
        "mytelegrambot",
        "idea_closed",
        "success",
        detail=f"closed idea #{number} from chat",
        idea_issue=number,
    )
    return Reply(f"Closed my-idea#{number}.")
