from __future__ import annotations

from collections.abc import Iterator

from myguide.catalog import Catalog
from myguide.guide import Guide, GuideError
from myguide.render import Message
from mythings.testers import TesterStore

from mytelegrambot.authz import Principal, release_engine_call, reserve_engine_call
from mytelegrambot.router import CallbackAction, InlineKeyboard, Reply, encode_action

# MyGuide is the fleet's front door for someone who has never opened a terminal,
# and its own CLAUDE.md always intended this surface: "over Telegram a tester
# answers with bounded inline buttons."
#
# The split falls out of MyGuide's own contract:
#   /catalog          -> deterministic, no Engine call, costs a tester nothing
#   /wish <text>      -> MyGuide's single Engine call, so metered like /idea
#   "Try <tool>"      -> trial(), a dry run: no Engine, no writes, no quota
#
# `trial --for-real` (the one write, delegated to MyServer's gated endpoint) is
# deliberately not exposed. It needs MYGUIDE_PLAYGROUND_REPO and a running
# MyServer, and fails closed without them -- there is nothing here a tester could
# do with it but hit a wall.

_QUOTA_EXHAUSTED = (
    "You've used your full allowance of Engine calls.\n"
    "Ask the operator to raise your quota if you need more."
)
_UNKNOWN_TOOL = "I don't know that tool."


def trial_buttons(catalog: Catalog, message: Message) -> InlineKeyboard | None:
    # A Message's `choices` are transport-agnostic by design. Only the ones whose
    # key names a catalogued tool become buttons: `trial_message` also emits
    # yes/no choices for the --for-real path, which this surface does not offer,
    # so they are dropped rather than rendered as buttons that do nothing.
    offered = [c for c in message.choices if c.key in catalog.repos]
    if not offered:
        return None
    buttons = [(c.label, encode_action("guide", c.key, "trial")) for c in offered]
    # Two per row: tool names are long enough that three would wrap badly.
    return tuple(tuple(buttons[i : i + 2]) for i in range(0, len(buttons), 2))


def _render(catalog: Catalog, message: Message) -> Reply:
    # The title is the card's heading; bolding it is what makes a catalog of a
    # dozen tools skimmable on a phone instead of one undifferentiated wall.
    parts = [f"*{message.title}*", "", *message.lines]
    if message.note:
        parts += ["", message.note]
    return Reply(
        "\n".join(parts).strip(),
        inline=trial_buttons(catalog, message),
        markdown=True,
    )


def handle_catalog(_text: str, _principal: Principal, *, guide: Guide) -> Reply:
    # Deterministic: renders cards a human curated in phrasebook.toml. No Engine
    # call, so no metering -- a newcomer can read the whole fleet for free.
    return _render(guide.catalog, guide.show_catalog())


def metered_wish(
    text: str,
    principal: Principal,
    *,
    store: TesterStore | None,
    guide: Guide,
) -> Iterator[Reply]:
    if not text.strip():
        yield Reply("Usage: /wish <what you'd like to do, in your own words>")
        return
    # Same contract as metered_idea: reserve before spending, refund only when
    # the call never happened. The operator is never metered.
    if not reserve_engine_call(principal, store):
        yield Reply(_QUOTA_EXHAUSTED)
        return

    # Nothing is filed here, so there is no receipt to hand over -- but the Engine
    # call still costs a minute, and a wish is the first thing a newcomer ever
    # types. Silence is the worst possible first impression.
    yield Reply("🔮 Thinking about what the fleet can do for that…")

    try:
        _wish, message = guide.wish(text)
    except Exception:
        release_engine_call(principal, store)
        raise
    yield _render(guide.catalog, message)


def trial_tool(action: CallbackAction, _principal: Principal, *, guide: Guide) -> Reply:
    # A dry run reads a phrasebook and writes nothing, so there is no ownership
    # to check (unlike an idea, which belongs to whoever filed it) and nothing to
    # meter. Any authorized principal may trial any catalogued tool.
    try:
        result = guide.trial(action.subject, for_real=False)
    except GuideError:
        # A forged or stale button naming a tool that isn't catalogued.
        return Reply(_UNKNOWN_TOOL)
    return _render(guide.catalog, result.message)
