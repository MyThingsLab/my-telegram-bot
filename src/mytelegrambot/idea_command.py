from __future__ import annotations

from myidea.explore import Runner, explore, file_idea
from mythings.engine import Engine
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import Policy

# myidea new's own CLI defaults to the cwd's repo via `gh`'s ambient detection;
# that only works when invoked from inside some repo's checkout. `mytelegrambot
# poll` is meant to run from a cron/systemd timer with an unspecified cwd, so
# this needs an explicit default instead of relying on that implicit one.
DEFAULT_IDEA_REPO = "MyThingsLab/my-idea"

_NO_BODY_FALLBACK = "(filed via Telegram /idea; no additional detail provided)"
_TELEGRAM_MAX_LEN = 4096


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
) -> str:
    # `runner` is an override for tests only: file_idea/explore already default
    # to a real `gh` subprocess when it's omitted, which is the correct
    # production behavior (the same real `gh` the `github` object itself
    # shells out to).
    runner_kwargs = {"runner": runner} if runner is not None else {}

    title, body = _split_title_body(args_text)
    if not title:
        return "Usage: /idea <title>\n(optionally followed by more detail on later lines)"

    created = file_idea(
        title=title,
        body=body or _NO_BODY_FALLBACK,
        github=github,
        policy=policy,
        ledger=ledger,
        **runner_kwargs,
    )
    if created is None:
        return "Idea filing was denied by policy — nothing was created."

    result = explore(
        issue=created.number,
        engine=engine,
        github=github,
        policy=policy,
        ledger=ledger,
        repo=repo,
        **runner_kwargs,
    )
    reply = f"Filed as my-idea#{created.number} — {created.url}\n\n{result.comment}"
    if not result.posted:
        reply += "\n(Note: the brief above could not be posted as a GitHub comment.)"
    return _truncate_for_telegram(reply)
