from __future__ import annotations

# Static, deterministic replies -- no Engine call, no side effects, no args
# consumed (help ignores whatever follows it). These keep the bot
# self-describing: Telegram sends `/start` automatically the first time a human
# opens the chat, so without a handler a new user is met with silence. `/help`
# and `/start` share one body.
_HELP_TEXT = "\n".join(
    [
        "MyThingsLab bot — capture ideas from chat and relay fleet activity.",
        "",
        "Commands:",
        "  /idea <title>",
        "      File a my-idea issue and get an explored brief back here in",
        "      chat. Put any extra detail on the lines after the title.",
        "  /status",
        "      Show what the bot has done so far (ideas, digests, approvals).",
        "  /help",
        "      Show this message.",
        "",
        "I also push fleet notifications to this chat and, when an action",
        "needs a human, send an Allow/Deny prompt. Plain chat (anything with",
        "no leading /) is ignored.",
    ]
)


def help_reply(_args: str) -> str:
    return _HELP_TEXT
