from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from myguard import Guard
from mythings.engine import ClaudeCLIEngine, Engine, NoopEngine
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import Action, Decision

from mytelegrambot.help_command import help_reply
from mytelegrambot.idea_command import DEFAULT_IDEA_REPO, handle_idea
from mytelegrambot.inbound import poll_once
from mytelegrambot.menu import COMMAND_MENU, REPLY_KEYBOARD, SETUP_GREETING
from mytelegrambot.notifier import notify
from mytelegrambot.policy import ask_human
from mytelegrambot.status_command import build_status
from mytelegrambot.transport import HTTPTelegramTransport

_ENGINES: dict[str, type[Engine]] = {"noop": NoopEngine, "claude-cli": ClaudeCLIEngine}


def _transport() -> HTTPTelegramTransport:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    return HTTPTelegramTransport(token, chat_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="mytelegrambot",
        description="Push ledger notifications and relay Policy ASK decisions to Telegram.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    nfy = sub.add_parser("notify", help="push ledger entries since the last notify")
    nfy.add_argument("--since", help="ISO8601 window start (default: since the last notify)")
    nfy.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))

    ask = sub.add_parser(
        "ask", help="send an Allow/Deny prompt for one action and wait for a reply"
    )
    ask.add_argument("--action-kind", required=True)
    ask.add_argument("--payload-json", default="{}")
    ask.add_argument("--timeout", type=float, default=300.0)
    ask.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))

    poll = sub.add_parser(
        "poll", help="process pending inbound messages (currently: /idea <text>)"
    )
    poll.add_argument(
        "--repo", default=None, help=f"owner/name for filed ideas; default {DEFAULT_IDEA_REPO}"
    )
    poll.add_argument("--engine", choices=sorted(_ENGINES), default="claude-cli")
    poll.add_argument("--get-updates-timeout", type=float, default=1.0)
    poll.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))

    sub.add_parser(
        "setup", help="register the command menu and persistent reply keyboard with Telegram"
    )

    args = parser.parse_args(argv)
    transport = _transport()

    if args.cmd == "setup":
        # One-off admin call: no ledger, no Engine -- just tells Telegram what
        # commands to advertise and shows the persistent shortcut keyboard.
        transport.set_my_commands(COMMAND_MENU)
        transport.send_message(SETUP_GREETING, keyboard=REPLY_KEYBOARD)
        print(f"setup: registered {len(COMMAND_MENU)} commands and the reply keyboard")
        return 0

    ledger = Ledger(args.ledger)

    if args.cmd == "notify":
        result = notify(ledger, transport=transport, since=args.since)
        print(f"{result.outcome}: {result.entries_count} entries")
        return 1 if result.outcome == "failure" else 0

    if args.cmd == "poll":
        repo = args.repo or DEFAULT_IDEA_REPO
        github = GitHub(repo=repo)
        guard = Guard()
        engine = _ENGINES[args.engine]()
        routes = {
            "idea": lambda text: handle_idea(
                text, github=github, policy=guard, engine=engine, ledger=ledger, repo=repo
            ),
            # Deterministic, no-Engine health snapshot read straight from the
            # ledger (ignores any args after /status).
            "status": lambda _text: build_status(ledger),
            # Static, no-Engine meta commands so a human opening the chat can
            # discover what the bot does. Telegram auto-sends /start on first
            # open; both map to the same help body.
            "help": help_reply,
            "start": help_reply,
        }
        poll_result = poll_once(
            ledger=ledger,
            transport=transport,
            routes=routes,
            get_updates_timeout=args.get_updates_timeout,
        )
        print(
            f"{poll_result.outcome}: {poll_result.updates_fetched} update(s), "
            f"{poll_result.commands_routed} routed"
        )
        return 1 if poll_result.outcome == "failure" else 0

    action = Action(kind=args.action_kind, payload=json.loads(args.payload_json))
    result = ask_human(action, transport=transport, ledger=ledger, timeout=args.timeout)
    print(f"{result.outcome}: {result.decision.value}")
    return 0 if result.decision is Decision.ALLOW else 1


if __name__ == "__main__":
    raise SystemExit(main())
