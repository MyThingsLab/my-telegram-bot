from __future__ import annotations

from mytelegrambot.router import Command, dispatch, parse_command


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


def test_dispatch_routes_to_the_matching_handler() -> None:
    routes = {"idea": lambda args: f"got: {args}"}

    assert dispatch("/idea a new tool", routes) == "got: a new tool"


def test_dispatch_returns_none_for_non_command_text() -> None:
    routes = {"idea": lambda args: "should not be called"}

    assert dispatch("hello there", routes) is None


def test_dispatch_returns_none_for_unregistered_command() -> None:
    routes = {"idea": lambda args: "should not be called"}

    assert dispatch("/status", routes) is None
