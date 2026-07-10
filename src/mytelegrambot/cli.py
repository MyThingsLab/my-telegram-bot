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
from mythings.testers import TesterStore

from mytelegrambot.authz import ChatAuthorizer, Principal, ledger_for
from mytelegrambot.help_command import help_reply
from mytelegrambot.idea_command import (
    DEFAULT_IDEA_REPO,
    close_idea,
    explore_idea,
    metered_idea,
)
from mytelegrambot.inbound import run_forever
from mytelegrambot.menu import COMMAND_MENU, REPLY_KEYBOARD, SETUP_GREETING
from mytelegrambot.notifier import notify
from mytelegrambot.pending import PendingChats
from mytelegrambot.pending import pending as pending_chats
from mytelegrambot.policy import ask_human
from mytelegrambot.router import CallbackAction, CallbackHandler, CommandHandler, Reply
from mytelegrambot.status_command import build_status
from mytelegrambot.transport import HTTPTelegramTransport

_ENGINES: dict[str, type[Engine]] = {"noop": NoopEngine, "claude-cli": ClaudeCLIEngine}
_DEFAULT_TESTERS_DB = Path(".mythings/testers.db")


def _transport() -> HTTPTelegramTransport:
    token = os.environ["TELEGRAM_BOT_TOKEN"]
    chat_id = os.environ["TELEGRAM_CHAT_ID"]
    return HTTPTelegramTransport(token, chat_id)


def build_routes(
    *,
    ledger: Ledger,
    store: TesterStore | None,
    github: GitHub,
    guard: Guard,
    engine: Engine,
    repo: str,
) -> dict[str, CommandHandler]:
    def idea(text: str, principal: Principal) -> Reply:
        return metered_idea(
            text,
            principal,
            store=store,
            github=github,
            policy=guard,
            engine=engine,
            ledger=ledger_for(principal, main=ledger, store=store),
            repo=repo,
        )

    def status(_text: str, principal: Principal) -> Reply:
        # A tester sees their own activity, not the operator's whole fleet.
        return Reply(build_status(ledger_for(principal, main=ledger, store=store)))

    return {"idea": idea, "status": status, "help": help_reply, "start": help_reply}


def build_callback_routes(
    *,
    ledger: Ledger,
    store: TesterStore | None,
    github: GitHub,
    guard: Guard,
    engine: Engine,
    repo: str,
) -> dict[str, CallbackHandler]:
    def explore(action: CallbackAction, principal: Principal) -> Reply:
        return explore_idea(
            action,
            principal,
            store=store,
            github=github,
            policy=guard,
            engine=engine,
            ledger=ledger_for(principal, main=ledger, store=store),
            repo=repo,
        )

    def close(action: CallbackAction, principal: Principal) -> Reply:
        return close_idea(
            action,
            principal,
            policy=guard,
            ledger=ledger_for(principal, main=ledger, store=store),
            repo=repo,
        )

    return {"idea:explore": explore, "idea:close": close}


def _testers_command(args: argparse.Namespace) -> int:
    if args.testers_cmd == "pending":
        store = TesterStore(args.db) if args.db.exists() else None
        waiting = pending_chats(Ledger(args.ledger), store)
        if not waiting:
            print("no unregistered chats have contacted the bot")
            return 0
        print(f"{len(waiting)} chat(s) waiting to be registered:\n")
        for knock in waiting:
            print(f"  chat {knock.chat_id}  first seen {knock.first_seen}")
            print(
                f"    mytelegrambot testers --db {args.db} add <handle> "
                f"--chat-id {knock.chat_id} --quota 5"
            )
        return 0

    store = TesterStore(args.db)
    if args.testers_cmd == "add":
        tester, token = store.register(args.handle, engine_quota=args.quota, chat_id=args.chat_id)
        print(f"registered {tester.handle} (id {tester.id}) quota={tester.engine_quota}")
        # Shown once and never stored: only its sha256 reaches the database.
        print(f"token: {token}")
        return 0
    enabled = args.testers_cmd == "enable"
    store.set_enabled(args.tester_id, enabled)
    print(f"{'enabled' if enabled else 'disabled'} tester {args.tester_id}")
    return 0


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

    run = sub.add_parser(
        "run", help="run the long-lived poller: the sole owner of Telegram's update queue"
    )
    run.add_argument(
        "--repo", default=None, help=f"owner/name for filed ideas; default {DEFAULT_IDEA_REPO}"
    )
    run.add_argument("--engine", choices=sorted(_ENGINES), default="claude-cli")
    run.add_argument("--long-poll", type=float, default=30.0)
    run.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    run.add_argument(
        "--testers-db",
        type=Path,
        default=None,
        help="admit registered testers from this database (default: operator only)",
    )

    sub.add_parser(
        "setup", help="register the command menu and persistent reply keyboard with Telegram"
    )

    testers = sub.add_parser("testers", help="manage who, besides the operator, may use the bot")
    testers.add_argument("--db", type=Path, default=_DEFAULT_TESTERS_DB)
    testers.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))
    tsub = testers.add_subparsers(dest="testers_cmd", required=True)
    tsub.add_parser("pending", help="list unregistered chats that have contacted the bot")
    add = tsub.add_parser("add", help="register a tester and print their token once")
    add.add_argument("handle")
    add.add_argument("--chat-id", type=int, required=True)
    add.add_argument("--quota", type=int, required=True, help="Engine calls this tester may spend")
    disable = tsub.add_parser("disable", help="revoke a tester's access")
    disable.add_argument("tester_id", type=int)
    enable = tsub.add_parser("enable", help="restore a tester's access")
    enable.add_argument("tester_id", type=int)

    args = parser.parse_args(argv)

    if args.cmd == "testers":
        return _testers_command(args)

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

    if args.cmd == "run":
        repo = args.repo or DEFAULT_IDEA_REPO
        # Without --testers-db the bot stays exactly as single-user as it was:
        # ChatAuthorizer has no store to consult, so every chat but the
        # operator's is dropped. Admitting testers is an explicit act.
        store = TesterStore(args.testers_db) if args.testers_db else None
        authorizer = ChatAuthorizer(os.environ["TELEGRAM_CHAT_ID"], store=store)
        wiring = {
            "ledger": ledger,
            "store": store,
            "github": GitHub(repo=repo),
            "guard": Guard(),
            "engine": _ENGINES[args.engine](),
            "repo": repo,
        }
        print(
            f"mytelegrambot: polling (long_poll={args.long_poll}s, "
            f"testers={'on' if store else 'off'})"
        )
        run_forever(
            ledger=ledger,
            transport=transport,
            authorizer=authorizer,
            routes=build_routes(**wiring),
            callback_routes=build_callback_routes(**wiring),
            # Recorded regardless of --testers-db: you have to see who knocked
            # before you have anyone to put in a database.
            pending=PendingChats(ledger),
            long_poll=args.long_poll,
        )
        return 0

    action = Action(kind=args.action_kind, payload=json.loads(args.payload_json))
    result = ask_human(action, transport=transport, ledger=ledger, timeout=args.timeout)
    print(f"{result.outcome}: {result.decision.value}")
    return 0 if result.decision is Decision.ALLOW else 1


if __name__ == "__main__":
    raise SystemExit(main())
