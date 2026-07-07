from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from mythings.ledger import Ledger
from mythings.policy import Action, Decision

from mytelegrambot.notifier import notify
from mytelegrambot.policy import ask_human
from mytelegrambot.transport import HTTPTelegramTransport


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

    args = parser.parse_args(argv)
    ledger = Ledger(args.ledger)
    transport = _transport()

    if args.cmd == "notify":
        result = notify(ledger, transport=transport, since=args.since)
        print(f"{result.outcome}: {result.entries_count} entries")
        return 1 if result.outcome == "failure" else 0

    action = Action(kind=args.action_kind, payload=json.loads(args.payload_json))
    result = ask_human(action, transport=transport, ledger=ledger, timeout=args.timeout)
    print(f"{result.outcome}: {result.decision.value}")
    return 0 if result.decision is Decision.ALLOW else 1


if __name__ == "__main__":
    raise SystemExit(main())
