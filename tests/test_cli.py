from __future__ import annotations

from pathlib import Path

import pytest
from mythings.engine import NoopEngine
from mythings.ledger import Ledger
from mythings.testers import TesterStore

from conftest import ErrorTransport, FakeTransport, entry, operator
from mytelegrambot import cli
from mytelegrambot.router import CallbackAction, Reply
from mytelegrambot.transport import HTTPTelegramTransport


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger.jsonl"


@pytest.fixture(autouse=True)
def telegram_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")


def _use_transport(monkeypatch: pytest.MonkeyPatch, transport: object) -> None:
    monkeypatch.setattr(cli, "_transport", lambda: transport)


def _tapped(ledger_path: Path, message_id: int, decision: str) -> None:
    Ledger(ledger_path).record(
        tool="mytelegrambot",
        kind="callback",
        outcome="received",
        message_id=message_id,
        decision=decision,
    )


def test_transport_is_wired_from_env_credentials() -> None:
    transport = cli._transport()

    assert isinstance(transport, HTTPTelegramTransport)
    assert transport._token == "tok"
    assert transport._chat_id == "chat"


def test_notify_pushes_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    Ledger(ledger_path).append(
        entry("mytester", "run", "success", "cover pkg:f", ts="2026-07-06T01:00:00Z")
    )
    transport = FakeTransport()
    _use_transport(monkeypatch, transport)

    code = cli.main(["notify", "--ledger", str(ledger_path)])

    assert code == 0
    assert "success: 1 entries" in capsys.readouterr().out
    assert len(transport.sent) == 1


def test_notify_skips_and_exits_zero(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_transport(monkeypatch, FakeTransport())

    code = cli.main(["notify", "--ledger", str(ledger_path)])

    assert code == 0
    assert "skipped: 0 entries" in capsys.readouterr().out


def test_notify_send_failure_exits_nonzero(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    Ledger(ledger_path).append(
        entry("mytester", "run", "success", "cover pkg:f", ts="2026-07-06T01:00:00Z")
    )
    _use_transport(monkeypatch, ErrorTransport())

    code = cli.main(["notify", "--ledger", str(ledger_path)])

    assert code == 1


def test_ask_allow_exits_zero(monkeypatch: pytest.MonkeyPatch, ledger_path: Path) -> None:
    _use_transport(monkeypatch, FakeTransport())
    _tapped(ledger_path, 1, "allow")  # what the daemon writes when the human taps

    code = cli.main(
        [
            "ask",
            "--action-kind",
            "bash",
            "--payload-json",
            '{"command": "rm x"}',
            "--ledger",
            str(ledger_path),
        ]
    )

    assert code == 0


def test_ask_deny_exits_nonzero(monkeypatch: pytest.MonkeyPatch, ledger_path: Path) -> None:
    _use_transport(monkeypatch, FakeTransport())
    _tapped(ledger_path, 1, "deny")

    code = cli.main(["ask", "--action-kind", "bash", "--ledger", str(ledger_path)])

    assert code == 1


def test_ask_timeout_exits_nonzero(monkeypatch: pytest.MonkeyPatch, ledger_path: Path) -> None:
    _use_transport(monkeypatch, FakeTransport())  # no callback ever recorded

    code = cli.main(
        ["ask", "--action-kind", "bash", "--timeout", "0", "--ledger", str(ledger_path)]
    )

    assert code == 1


def _captured_run(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def fake_run_forever(**kwargs: object) -> None:
        captured.update(kwargs)

    monkeypatch.setattr(cli, "run_forever", fake_run_forever)
    return captured


def test_run_wires_the_daemon_with_routes_and_an_authorizer(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    captured = _captured_run(monkeypatch)
    _use_transport(monkeypatch, FakeTransport())

    code = cli.main(["run", "--engine", "noop", "--ledger", str(ledger_path)])

    assert code == 0
    assert set(captured["routes"]) == {"idea", "status", "help", "start"}
    authorizer = captured["authorizer"]
    assert authorizer.authorize("chat").is_operator
    assert authorizer.authorize("999") is None  # no --testers-db: operator only


def test_run_without_testers_db_admits_nobody_else(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path, tmp_path: Path
) -> None:
    # Even a tester who exists in a database is not admitted unless the daemon
    # was explicitly pointed at it.
    TesterStore(tmp_path / "t.db").register("ada", engine_quota=5, chat_id=999)
    captured = _captured_run(monkeypatch)
    _use_transport(monkeypatch, FakeTransport())

    cli.main(["run", "--engine", "noop", "--ledger", str(ledger_path)])

    assert captured["authorizer"].authorize("999") is None


def test_run_with_testers_db_admits_a_registered_tester(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path, tmp_path: Path
) -> None:
    db = tmp_path / "t.db"
    TesterStore(db).register("ada", engine_quota=5, chat_id=999)
    captured = _captured_run(monkeypatch)
    _use_transport(monkeypatch, FakeTransport())

    cli.main(["run", "--engine", "noop", "--ledger", str(ledger_path), "--testers-db", str(db)])

    principal = captured["authorizer"].authorize("999")
    assert principal is not None
    assert principal.tester.handle == "ada"


def test_run_routes_help_start_and_status_deterministically(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    Ledger(ledger_path).record("myidea", "idea_filed", "success", detail="filed idea #1: x")
    captured = _captured_run(monkeypatch)
    _use_transport(monkeypatch, FakeTransport())

    cli.main(["run", "--engine", "noop", "--ledger", str(ledger_path)])

    routes = captured["routes"]
    who = operator()
    assert "/idea" in routes["help"]("", who).text
    assert routes["start"]("", who) == routes["help"]("", who)
    assert "Ideas filed via chat: 1" in routes["status"]("", who).text


def test_run_wires_the_idea_route_through_the_metered_handler(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    seen: dict = {}

    def fake_metered_idea(text: str, principal, *, store, engine, repo, **kwargs) -> Reply:
        seen["text"] = text
        seen["repo"] = repo
        seen["engine"] = engine
        seen["store"] = store
        return Reply("ok reply")

    monkeypatch.setattr(cli, "metered_idea", fake_metered_idea)
    captured = _captured_run(monkeypatch)
    _use_transport(monkeypatch, FakeTransport())

    cli.main(["run", "--engine", "noop", "--ledger", str(ledger_path)])
    reply = captured["routes"]["idea"]("a new tool", operator())

    assert reply.text == "ok reply"
    assert seen["text"] == "a new tool"
    assert seen["repo"] == cli.DEFAULT_IDEA_REPO
    assert isinstance(seen["engine"], NoopEngine)
    assert seen["store"] is None


def test_run_repo_flag_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    seen: dict = {}

    def fake_metered_idea(text: str, principal, *, repo, **kwargs) -> Reply:
        seen["repo"] = repo
        return Reply("ok")

    monkeypatch.setattr(cli, "metered_idea", fake_metered_idea)
    captured = _captured_run(monkeypatch)
    _use_transport(monkeypatch, FakeTransport())

    cli.main(["run", "--engine", "noop", "--repo", "o/r", "--ledger", str(ledger_path)])
    captured["routes"]["idea"]("x", operator())

    assert seen["repo"] == "o/r"


def test_setup_registers_the_command_menu_and_keyboard(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    transport = FakeTransport()
    _use_transport(monkeypatch, transport)

    code = cli.main(["setup"])

    assert code == 0
    # Registered the ☰ menu and pushed one greeting carrying the reply keyboard.
    assert len(transport.commands_set) == 1
    registered = {name for name, _desc in transport.commands_set[0]}
    assert {"idea", "status", "help"} <= registered
    assert transport.keyboards[-1] == (("/idea",), ("/status", "/help"))
    assert "registered" in capsys.readouterr().out


def test_testers_add_prints_the_token_exactly_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db = tmp_path / "t.db"

    code = cli.main(["testers", "--db", str(db), "add", "ada", "--chat-id", "999", "--quota", "5"])

    assert code == 0
    out = capsys.readouterr().out
    assert "registered ada" in out
    token = next(ln.split("token: ")[1] for ln in out.splitlines() if ln.startswith("token:"))
    # Printed, but never stored: the db holds only its sha256.
    assert token.encode() not in db.read_bytes()
    assert TesterStore(db).authenticate(token) is not None


def test_testers_disable_and_enable_toggle_access(tmp_path: Path) -> None:
    db = tmp_path / "t.db"
    tester, _ = TesterStore(db).register("ada", engine_quota=1, chat_id=999)

    cli.main(["testers", "--db", str(db), "disable", str(tester.id)])
    assert TesterStore(db).by_chat_id(999) is None

    cli.main(["testers", "--db", str(db), "enable", str(tester.id)])
    assert TesterStore(db).by_chat_id(999) is not None


def test_testers_needs_no_telegram_credentials(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Registering a tester is pure database admin -- it must not require (or
    # touch) the bot token.
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)

    code = cli.main(
        ["testers", "--db", str(tmp_path / "t.db"), "add", "bob", "--chat-id", "1", "--quota", "1"]
    )

    assert code == 0


def test_run_wires_the_callback_routes_to_the_button_handlers(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    seen: dict = {}

    def fake_explore_idea(action, principal, *, repo, **kwargs) -> Reply:
        seen["explore"] = (action.number, repo)
        return Reply("explored")

    def fake_close_idea(action, principal, *, repo, **kwargs) -> Reply:
        seen["close"] = (action.number, repo)
        return Reply("closed")

    monkeypatch.setattr(cli, "explore_idea", fake_explore_idea)
    monkeypatch.setattr(cli, "close_idea", fake_close_idea)
    captured = _captured_run(monkeypatch)
    _use_transport(monkeypatch, FakeTransport())

    cli.main(["run", "--engine", "noop", "--repo", "o/r", "--ledger", str(ledger_path)])

    callback_routes = captured["callback_routes"]
    assert set(callback_routes) == {"idea:explore", "idea:close"}

    who = operator()
    assert (
        callback_routes["idea:explore"](CallbackAction("idea:explore", 12), who).text == "explored"
    )
    assert callback_routes["idea:close"](CallbackAction("idea:close", 12), who).text == "closed"
    assert seen["explore"] == (12, "o/r")
    assert seen["close"] == (12, "o/r")
