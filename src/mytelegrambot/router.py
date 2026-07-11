from __future__ import annotations

from collections.abc import Callable, Iterable, Iterator
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
    # they can attach markup. `markdown` asks the transport for Telegram's
    # Markdown parse_mode; it falls back to plain text if Telegram rejects the
    # entities, so setting it can never cost the message.
    text: str
    inline: InlineKeyboard | None = None
    markdown: bool = False


# A command handler takes the raw text after the leading "/word" plus the
# authorized Principal that sent it. A callback handler takes the decoded button
# press. Routes are registered in cli.py; adding one is a dict entry, no change
# to this module.
#
# A handler returns either one Reply or an *iterator* of them, and the daemon
# sends each as it arrives. That is what lets a handler answer before it is
# finished: /idea files the GitHub issue in well under a second but then blocks
# for a whole Engine call, so it yields the issue number immediately and the
# explored brief when it has one, instead of leaving the human staring at a dead
# chat for a minute. A handler still never touches the transport -- yielding is
# the only way it can say "send this now".
#
# The Principal is not decoration: a handler that spends an Engine call has to
# meter it against that tester's quota, and one that reads the ledger has to read
# *their* ledger, not the operator's.
Replies = Reply | Iterable[Reply]
CommandHandler = Callable[[str, Principal], Replies]
CallbackHandler = Callable[["CallbackAction", Principal], Replies]


def as_replies(result: Replies) -> Iterator[Reply]:
    return iter((result,)) if isinstance(result, Reply) else iter(result)


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
    # In a group, Telegram delivers an explicitly addressed command as
    # "/idea@MyBot" -- the same command, and the only form its own autocomplete
    # offers there. Without stripping the suffix it parses as an unknown name.
    name, _, _bot = head[1:].partition("@")
    name = name.lower()
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


def unknown_command(name: str) -> Reply:
    return Reply(f"I don't know /{name}. Send /help to see what I can do.")


def dispatch(
    text: str, routes: dict[str, CommandHandler], principal: Principal
) -> Iterator[Reply] | None:
    # Ordinary chat is still ignored outright: this channel also carries notify
    # digests and Allow/Deny prompts, and answering every stray line would make
    # it unusable. A *slash* command is different -- it was typed deliberately,
    # so a silent drop on a typo is indistinguishable from the bot being down.
    command = parse_command(text)
    if command is None:
        return None
    handler = routes.get(command.name)
    if handler is None:
        return iter((unknown_command(command.name),))
    return as_replies(handler(command.args, principal))


def dispatch_callback(
    data: str, routes: dict[str, CallbackHandler], principal: Principal
) -> Iterator[Reply] | None:
    action = parse_callback(data)
    if action is None:
        return None
    handler = routes.get(action.key)
    if handler is None:
        return None
    return as_replies(handler(action, principal))
