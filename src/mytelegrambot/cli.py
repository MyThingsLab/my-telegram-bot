from __future__ import annotations

import argparse
import json
import os
from collections.abc import Iterator
from pathlib import Path

from myguard import Guard
from myguide.catalog import build_catalog
from myguide.guide import Guide
from mythings.engine import ClaudeCLIEngine, Engine, NoopEngine
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import Action, Decision
from mythings.testers import TesterStore

from mytelegrambot.authz import ChatAuthorizer, Principal, ledger_for
from mytelegrambot.blocker_command import (
    handle_blocker_retry,
    handle_blocker_skip,
    handle_blocker_take,
    send_blocker_alert,
)
from mytelegrambot.guide_command import handle_catalog, metered_wish, trial_tool
from mytelegrambot.halt_command import HaltControl, handle_halt, handle_resume
from mytelegrambot.help_command import help_reply
from mytelegrambot.idea_command import (
    DEFAULT_IDEA_REPO,
    close_idea,
    explore_idea,
    metered_idea,
)
from mytelegrambot.inbound import run_forever
from mytelegrambot.menu import COMMAND_MENU, REPLY_KEYBOARD, SETUP_GREETING
from mytelegrambot.note_command import DEFAULT_NOTE_REPO, metered_note
from mytelegrambot.notifier import notify
from mytelegrambot.pending import PendingChats
from mytelegrambot.pending import pending as pending_chats
from mytelegrambot.policy import ask_human
from mytelegrambot.router import CallbackAction, CallbackHandler, CommandHandler, Reply
from mytelegrambot.spend_command import handle_spend_halt, handle_spend_raise, send_spend_alert
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
    note_repo: str,
    catalog,
    halt: HaltControl | None = None,
) -> dict[str, CommandHandler]:
    def _guide(principal: Principal) -> Guide:
        # One Guide per request so a tester's activity lands in their own ledger.
        return Guide(
            catalog=catalog,
            ledger=ledger_for(principal, main=ledger, store=store),
            engine=engine,
            policy=guard,
        )

    def idea(text: str, principal: Principal) -> Iterator[Reply]:
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

    def note(text: str, principal: Principal) -> Iterator[Reply]:
        return metered_note(
            text,
            principal,
            store=store,
            github=GitHub(repo=note_repo),
            policy=guard,
            engine=engine,
            ledger=ledger_for(principal, main=ledger, store=store),
            repo=note_repo,
        )

    def status(_text: str, principal: Principal) -> Reply:
        # A tester sees their own activity, not the operator's whole fleet.
        return Reply(build_status(ledger_for(principal, main=ledger, store=store)), markdown=True)

    def catalog_cmd(text: str, principal: Principal) -> Reply:
        return handle_catalog(text, principal, guide=_guide(principal))

    def wish(text: str, principal: Principal) -> Iterator[Reply]:
        return metered_wish(text, principal, store=store, guide=_guide(principal))

    def halt_cmd(text: str, principal: Principal) -> Reply:
        # Operator-only, Policy-gated, and never a tester's: see halt_command.
        return handle_halt(text, principal, control=halt, policy=guard, ledger=ledger)

    def resume_cmd(text: str, principal: Principal) -> Reply:
        return handle_resume(text, principal, control=halt, policy=guard, ledger=ledger)

    return {
        "idea": idea,
        "note": note,
        "catalog": catalog_cmd,
        "wish": wish,
        "status": status,
        "halt": halt_cmd,
        "resume": resume_cmd,
        "help": help_reply,
        "start": help_reply,
    }


def build_callback_routes(
    *,
    ledger: Ledger,
    store: TesterStore | None,
    github: GitHub,
    guard: Guard,
    engine: Engine,
    repo: str,
    note_repo: str,
    catalog,
    halt: HaltControl | None = None,
) -> dict[str, CallbackHandler]:
    del note_repo  # notes have no buttons

    def explore(action: CallbackAction, principal: Principal) -> Iterator[Reply]:
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

    def trial(action: CallbackAction, principal: Principal) -> Reply:
        guide = Guide(
            catalog=catalog,
            ledger=ledger_for(principal, main=ledger, store=store),
            engine=engine,
            policy=guard,
        )
        return trial_tool(action, principal, guide=guide)

    def spend_halt(action: CallbackAction, principal: Principal) -> Reply:
        return handle_spend_halt(action, principal, control=halt, policy=guard, ledger=ledger)

    def spend_raise(action: CallbackAction, principal: Principal) -> Reply:
        return handle_spend_raise(action, principal, control=halt, policy=guard, ledger=ledger)

    def blocker_retry(action: CallbackAction, principal: Principal) -> Reply:
        return handle_blocker_retry(action, principal, ledger=ledger)

    def blocker_skip(action: CallbackAction, principal: Principal) -> Reply:
        return handle_blocker_skip(action, principal, ledger=ledger)

    def blocker_take(action: CallbackAction, principal: Principal) -> Reply:
        return handle_blocker_take(action, principal, ledger=ledger)

    return {
        "idea:explore": explore,
        "idea:close": close,
        "guide:trial": trial,
        "spend:halt": spend_halt,
        "spend:raise": spend_raise,
        "blocker:retry": blocker_retry,
        "blocker:skip": blocker_skip,
        "blocker:take": blocker_take,
    }


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

    alert = sub.add_parser(
        "alert-spend",
        help="push a spend-tripwire alert with Halt / Raise-cap buttons and exit",
    )
    alert.add_argument("--spent", type=float, required=True)
    alert.add_argument("--cap", type=float, required=True)
    alert.add_argument("--raise-to", type=float, required=True)
    alert.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))

    blocker = sub.add_parser(
        "escalate-blocker",
        help="push a needs_human blocker with Retry / Skip / I'll take it buttons and exit",
    )
    blocker.add_argument("--candidate", required=True)
    blocker.add_argument("--detail", default="")
    blocker.add_argument("--attempt", type=int, default=0)
    blocker.add_argument("--ledger", type=Path, default=Path(".mythings/ledger.jsonl"))

    run = sub.add_parser(
        "run", help="run the long-lived poller: the sole owner of Telegram's update queue"
    )
    run.add_argument(
        "--repo", default=None, help=f"owner/name for filed ideas; default {DEFAULT_IDEA_REPO}"
    )
    run.add_argument(
        "--note-repo", default=None, help=f"owner/name for filed notes; default {DEFAULT_NOTE_REPO}"
    )
    run.add_argument("--engine", choices=sorted(_ENGINES), default="claude-cli")
    run.add_argument("--long-poll", type=float, default=30.0)
    run.add_argument(
        "--halt-cmd",
        default=None,
        help="base command for the fleet kill switch, e.g. "
        "'python3 /path/to/fleet_dispatch.py'. /halt appends --abort and /resume "
        "--clear-halt. Absent, /halt says so rather than pretending. A CLI hand-off: "
        "the bot never imports the fleet or learns where its marker file lives.",
    )
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

    if args.cmd == "alert-spend":
        send_spend_alert(
            spent=args.spent, cap=args.cap, raise_to=args.raise_to,
            transport=transport, ledger=ledger,
        )
        print(f"spend alert pushed: ${args.spent:.2f} of ${args.cap:.2f}/day")
        return 0

    if args.cmd == "escalate-blocker":
        send_blocker_alert(
            candidate=args.candidate, detail=args.detail, attempt=args.attempt,
            transport=transport, ledger=ledger,
        )
        print(f"blocker alert pushed for {args.candidate}")
        return 0

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
            # ask=None is load-bearing, not a default spelled out for clarity.
            #
            # MyGuard escalates an ASK by shelling out to $MYTHINGS_ASK_CMD -- which
            # is `mytelegrambot ask`, which blocks waiting for the `kind=callback`
            # ledger entry that *this daemon* writes when the human taps. The daemon
            # is single-threaded: it would be sitting inside the handler that
            # triggered the ask, unable to fetch the very update that answers it. It
            # would deadlock against itself for the whole ask timeout and then DENY
            # -- and since this process is also the fleet's ask channel, every
            # worker's escalation would stall behind it.
            #
            # So the daemon never escalates through itself. An ASK it cannot service
            # resolves DENY via the `under(unattended=True)` collapse its handlers
            # already apply, which is exactly the posture an unattended runner is
            # supposed to take.
            "guard": Guard(ask=None),
            "engine": _ENGINES[args.engine](),
            "repo": repo,
            "note_repo": args.note_repo or DEFAULT_NOTE_REPO,
            # Built once: reads the packaged phrasebook and core's fleet manifest,
            # and refuses outright if the phrasebook describes an unshipped tool.
            "catalog": build_catalog(),
        }
        halt = HaltControl(args.halt_cmd) if args.halt_cmd else None
        print(
            f"mytelegrambot: polling (long_poll={args.long_poll}s, "
            f"testers={'on' if store else 'off'})"
        )
        run_forever(
            ledger=ledger,
            transport=transport,
            authorizer=authorizer,
            routes=build_routes(**wiring, halt=halt),
            callback_routes=build_callback_routes(**wiring, halt=halt),
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
