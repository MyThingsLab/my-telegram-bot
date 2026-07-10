from __future__ import annotations

from mynotes.capture import file_note
from mynotes.tag import Runner, tag
from mythings.engine import Engine
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import Policy
from mythings.testers import TesterStore

from mytelegrambot.authz import Principal, release_engine_call, reserve_engine_call
from mytelegrambot.router import Reply

# /note is /idea's structural twin: file an issue, make exactly one Engine call
# on it, reply with the structured result. The Engine call belongs to MyNotes
# (tag: extract tags + propose a title) and is delegated whole, never composed
# here. Filing and tagging are separate MyNotes calls precisely because filing
# spends nothing.

DEFAULT_NOTE_REPO = "MyThingsLab/my-notes"

_EMPTY_BODY_FALLBACK = "(captured via Telegram /note)"
_TELEGRAM_MAX_LEN = 4096

_QUOTA_EXHAUSTED = (
    "You've used your full allowance of Engine calls. Nothing was filed.\n"
    "Ask the operator to raise your quota if you need more."
)


def _split_title_body(args_text: str) -> tuple[str, str]:
    title, _, rest = args_text.strip().partition("\n")
    return title.strip(), rest.strip()


def _truncate_for_telegram(text: str) -> str:
    if len(text) <= _TELEGRAM_MAX_LEN:
        return text
    marker = "\n…[truncated]"
    return text[: _TELEGRAM_MAX_LEN - len(marker)] + marker


def handle_note(
    args_text: str,
    *,
    github: GitHub,
    policy: Policy,
    engine: Engine,
    ledger: Ledger,
    repo: str | None,
    runner: Runner | None = None,
) -> Reply:
    runner_kwargs = {"runner": runner} if runner is not None else {}

    title, body = _split_title_body(args_text)
    if not title:
        return Reply("Usage: /note <text>\n(optionally continued on later lines)")

    created = file_note(
        title=title,
        body=body or _EMPTY_BODY_FALLBACK,
        github=github,
        policy=policy,
        ledger=ledger,
        **runner_kwargs,
    )
    if created is None:
        return Reply("Filing the note was denied by policy — nothing was created.")

    result = tag(
        issue=created.number,
        engine=engine,
        github=github,
        policy=policy,
        ledger=ledger,
        repo=repo,
        comment=True,
        **runner_kwargs,
    )
    text = f"Noted as my-notes#{created.number} — {created.url}"
    if result.outcome == "success":
        text += f"\n\nTitle: {result.title}"
        if result.tags:
            text += f"\nTags: {', '.join(result.tags)}"
        if not result.posted:
            text += "\n(Note: the tags above could not be posted as a GitHub comment.)"
    return Reply(_truncate_for_telegram(text))


def metered_note(
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
    # Same contract as metered_idea: reserve before spending, refund only when
    # the call never happened, so a crash over-counts rather than letting an
    # unbilled Engine call through. The operator is never metered.
    if not reserve_engine_call(principal, store):
        return Reply(_QUOTA_EXHAUSTED)
    try:
        return handle_note(
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
