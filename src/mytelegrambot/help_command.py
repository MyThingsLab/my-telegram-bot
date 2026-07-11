from __future__ import annotations

from mytelegrambot.authz import Principal
from mytelegrambot.router import Reply

# Static, deterministic replies -- no Engine call, no side effects, no args
# consumed (help ignores whatever follows it). These keep the bot
# self-describing: Telegram sends `/start` automatically the first time a human
# opens the chat, so without a handler a new user is met with silence. `/help`
# and `/start` share one body.
_HELP_TEXT = "\n".join(
    [
        "*MyThingsLab bot* — capture ideas from chat and relay fleet activity.",
        "",
        "*/idea* `<title>`",
        "File a my-idea issue and get an explored brief back here in chat. Put any",
        "extra detail on the lines after the title. Tap the buttons under the reply",
        "to explore it again or close it.",
        "",
        "*/note* `<text>`",
        "Capture a freeform note as a my-notes issue. It comes back tagged and titled.",
        "",
        "*/catalog*",
        "See everything the fleet can do, explained in plain language. Tap a tool to",
        "see exactly what it would do — a dry run.",
        "",
        "*/wish* `<text>`",
        "Say what you want in your own words. I'll point you at the tools that serve",
        "it, or tell you the fleet can't.",
        "",
        "*/status*",
        "Show what the bot has done so far (ideas, digests, approvals).",
        "",
        "*/help*",
        "Show this message.",
        "",
        "/idea, /note and /wish each think for a while — you'll see me typing, and",
        "anything already filed is confirmed before I start.",
        "",
        "I also push fleet notifications to this chat and, when an action needs a",
        "human, send an Allow/Deny prompt. Plain chat (anything with no leading /)",
        "is ignored.",
    ]
)


def help_reply(_args: str, _principal: Principal) -> Reply:
    return Reply(_HELP_TEXT, markdown=True)
