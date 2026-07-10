from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from mytelegrambot.authz import Principal

# An inline button is (label, callback_data); a keyboard is rows of them.
# Telegram caps callback_data at 64 bytes, which `encode_action` respects.
InlineButton = tuple[str, str]
InlineKeyboard = tuple[tuple[InlineButton, ...], ...]

_CALLBACK_DATA_MAX = 64


@dataclass(frozen=True)
class Reply:
    # What a handler hands back: the text to send, plus optionally the buttons to
    # hang under it. Handlers never touch the transport, so this is the only way
    # they can attach markup.
    text: str
    inline: InlineKeyboard | None = None


# A command handler takes the raw text after the leading "/word" plus the
# authorized Principal that sent it. A callback handler takes the decoded button
# press. Routes are registered in cli.py; adding one is a dict entry, no change
# to this module.
#
# The Principal is not decoration: a handler that spends an Engine call has to
# meter it against that tester's quota, and one that reads the ledger has to read
# *their* ledger, not the operator's.
CommandHandler = Callable[[str, Principal], Reply]
CallbackHandler = Callable[["CallbackAction", Principal], Reply]


@dataclass(frozen=True)
class Command:
    name: str
    args: str


@dataclass(frozen=True)
class CallbackAction:
    # "idea:12:explore" -> key "idea:explore", subject "12".
    # "guide:my-archivist:trial" -> key "guide:trial", subject "my-archivist".
    #
    # The key is what routes dispatch on; the subject is what it acts upon.
    # Kept as a string because not every subject is an issue number -- a trial
    # acts on a tool. Handlers that need an int narrow it themselves via
    # `as_int()`, which returns None for a forged or stale button rather than
    # raising inside the daemon loop.
    key: str
    subject: str

    def as_int(self) -> int | None:
        try:
            return int(self.subject)
        except ValueError:
            return None


def parse_command(text: str) -> Command | None:
    text = text.strip()
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    name = head[1:].lower()
    if not name:
        return None
    return Command(name=name, args=rest.strip())


def encode_action(target: str, subject: str | int, verb: str) -> str:
    parts = (target, str(subject), verb)
    if any(":" in part or not part for part in parts):
        raise ValueError(f"callback_data segments must be non-empty and colon-free: {parts}")
    data = ":".join(parts)
    if len(data.encode("utf-8")) > _CALLBACK_DATA_MAX:
        raise ValueError(f"callback_data exceeds Telegram's {_CALLBACK_DATA_MAX} bytes: {data}")
    return data


def parse_callback(data: str) -> CallbackAction | None:
    parts = data.split(":")
    if len(parts) != 3:
        return None
    target, subject, verb = parts
    if not (target and subject and verb):
        return None
    return CallbackAction(key=f"{target}:{verb}", subject=subject)


def dispatch(text: str, routes: dict[str, CommandHandler], principal: Principal) -> Reply | None:
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


def dispatch_callback(
    data: str, routes: dict[str, CallbackHandler], principal: Principal
) -> Reply | None:
    action = parse_callback(data)
    if action is None:
        return None
    handler = routes.get(action.key)
    if handler is None:
        return None
    return handler(action, principal)
