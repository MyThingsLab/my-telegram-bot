from __future__ import annotations

from conftest import operator
from mytelegrambot.help_command import help_reply

_WHO = operator()


def test_help_reply_lists_the_available_commands() -> None:
    reply = help_reply("", _WHO)

    assert "/idea" in reply
    assert "/help" in reply


def test_help_reply_ignores_its_arguments() -> None:
    # /start arrives with no args; /help may be typed with trailing text. Both
    # get the same static body regardless of what follows.
    assert help_reply("some trailing text", _WHO) == help_reply("", _WHO)
