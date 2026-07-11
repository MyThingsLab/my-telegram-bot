from __future__ import annotations

from conftest import all_text, operator
from mytelegrambot.router import Command, Reply, dispatch, parse_command

_WHO = operator()


def test_parse_command_extracts_name_and_args() -> None:
    assert parse_command("/idea a scraper dashboard") == Command("idea", "a scraper dashboard")


def test_parse_command_lowercases_the_name() -> None:
    assert parse_command("/IDEA hello") == Command("idea", "hello")


def test_parse_command_with_no_args() -> None:
    assert parse_command("/idea") == Command("idea", "")


def test_parse_command_rejects_text_without_leading_slash() -> None:
    assert parse_command("just chatting") is None


def test_parse_command_rejects_bare_slash() -> None:
    assert parse_command("/") is None


def test_parse_command_strips_the_bot_suffix_a_group_chat_appends() -> None:
    # In a group, Telegram's own autocomplete produces "/idea@MyBot" -- the same
    # command. Without stripping the suffix it parsed as an unknown name.
    assert parse_command("/idea@MyThingsLabBot a scraper") == Command("idea", "a scraper")


def test_dispatch_routes_to_the_matching_handler() -> None:
    routes = {"idea": lambda args, who: Reply(f"{who.label} got: {args}")}

    assert all_text(dispatch("/idea a new tool", routes, _WHO)) == "operator got: a new tool"


def test_dispatch_streams_every_reply_a_handler_yields() -> None:
    # /idea acknowledges the filed issue, then sends the brief a whole Engine call
    # later. The daemon sends each as it arrives.
    def two(args: str, who: object):
        yield Reply("filed")
        yield Reply("explored")

    assert [r.text for r in dispatch("/idea x", {"idea": two}, _WHO)] == ["filed", "explored"]


def test_dispatch_returns_none_for_non_command_text() -> None:
    # Ordinary chat stays ignored: this channel also carries digests and Allow/Deny
    # prompts, and answering every stray line would make it unusable.
    routes = {"idea": lambda args, who: Reply("should not be called")}

    assert dispatch("hello there", routes, _WHO) is None


def test_dispatch_nudges_on_an_unrecognized_command() -> None:
    # A slash command was typed deliberately, so silence on a typo is
    # indistinguishable from the bot being down.
    routes = {"idea": lambda args, who: Reply("should not be called")}

    reply = all_text(dispatch("/idae oops", routes, _WHO))

    assert "/idae" in reply
    assert "/help" in reply
