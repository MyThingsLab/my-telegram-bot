from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mytelegrambot.authz import Principal

# A handler takes the raw text after the leading "/word" plus the authorized
# Principal that sent it, and returns the reply to send back. Routes are
# registered in cli.py ("idea", plus the static "help"/"start" meta commands);
# adding another is one more dict entry, no change to this module.
#
# The Principal is not decoration: a handler that spends an Engine call has to
# meter it against that tester's quota, and one that reads the ledger has to read
# *their* ledger, not the operator's.
CommandHandler = Callable[[str, Principal], str]


@dataclass(frozen=True)
class Command:
    name: str
    args: str


def parse_command(text: str) -> Command | None:
    text = text.strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    name = head[1:].lower()
    if not name:
        return None
    return Command(name=name, args=rest.strip())


def dispatch(text: str, routes: dict[str, CommandHandler], principal: Principal) -> str | None:
    # Non-command text and unrecognized commands are silently ignored -- no
    # reply, no ledger noise for ordinary chat/typos in a channel that also
    # carries no-Engine-call plumbing.
    command = parse_command(text)
    if command is None:
        return None
    handler = routes.get(command.name)
    if handler is None:
        return None
    return handler(command.args, principal)
