from __future__ import annotations

from mytelegrambot.help_command import help_reply


def test_help_reply_lists_the_available_commands() -> None:
    reply = help_reply("")

    assert "/idea" in reply
    assert "/help" in reply


def test_help_reply_ignores_its_arguments() -> None:
    # /start arrives with no args; /help may be typed with trailing text. Both
    # get the same static body regardless of what follows.
    assert help_reply("some trailing text") == help_reply("")
