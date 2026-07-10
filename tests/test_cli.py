from __future__ import annotations

from pathlib import Path

import pytest
from mythings.engine import NoopEngine
from mythings.ledger import Ledger

from conftest import ErrorTransport, FakeTransport, entry, message_update
from mytelegrambot import cli
from mytelegrambot.transport import HTTPTelegramTransport


@pytest.fixture
def ledger_path(tmp_path: Path) -> Path:
    return tmp_path / "ledger.jsonl"


def test_transport_is_wired_from_env_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "tok")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "chat")

    transport = cli._transport()

    assert isinstance(transport, HTTPTelegramTransport)
    assert transport._token == "tok"
    assert transport._chat_id == "chat"


def _use_transport(monkeypatch: pytest.MonkeyPatch, transport: object) -> None:
    monkeypatch.setattr(cli, "_transport", lambda: transport)


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
    _use_transport(monkeypatch, FakeTransport(reply="allow"))

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
    _use_transport(monkeypatch, FakeTransport(reply="deny"))

    code = cli.main(["ask", "--action-kind", "bash", "--ledger", str(ledger_path)])

    assert code == 1


def test_ask_timeout_exits_nonzero(monkeypatch: pytest.MonkeyPatch, ledger_path: Path) -> None:
    _use_transport(monkeypatch, FakeTransport(reply=None))

    code = cli.main(["ask", "--action-kind", "bash", "--ledger", str(ledger_path)])

    assert code == 1


def test_poll_with_no_updates_exits_zero(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _use_transport(monkeypatch, FakeTransport(updates=[]))

    code = cli.main(["poll", "--ledger", str(ledger_path)])

    assert code == 0
    assert "skipped: 0 update(s), 0 routed" in capsys.readouterr().out


def test_poll_routes_an_idea_command_through_the_wired_handler(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_idea(text: str, *, github, policy, engine, ledger, repo) -> str:
        captured["text"] = text
        captured["repo"] = repo
        captured["engine"] = engine
        return "ok reply"

    monkeypatch.setattr(cli, "handle_idea", fake_handle_idea)
    transport = FakeTransport(updates=[message_update(1, "/idea a new tool")])
    _use_transport(monkeypatch, transport)

    code = cli.main(["poll", "--engine", "noop", "--ledger", str(ledger_path)])

    assert code == 0
    assert captured["text"] == "a new tool"
    assert captured["repo"] == cli.DEFAULT_IDEA_REPO
    assert isinstance(captured["engine"], NoopEngine)
    assert transport.sent == [("ok reply", None)]


def test_poll_answers_help_with_the_command_list(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    transport = FakeTransport(updates=[message_update(1, "/help")])
    _use_transport(monkeypatch, transport)

    code = cli.main(["poll", "--engine", "noop", "--ledger", str(ledger_path)])

    assert code == 0
    assert len(transport.sent) == 1
    assert "/idea" in transport.sent[0][0]


def test_poll_answers_start_with_the_same_help_body(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    # Telegram sends /start automatically the first time a human opens the bot.
    transport = FakeTransport(updates=[message_update(1, "/start")])
    _use_transport(monkeypatch, transport)

    code = cli.main(["poll", "--engine", "noop", "--ledger", str(ledger_path)])

    assert code == 0
    assert "/idea" in transport.sent[0][0]


def test_poll_answers_status_from_the_ledger(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    Ledger(ledger_path).record("myidea", "idea_filed", "success", detail="filed idea #1: x")
    transport = FakeTransport(updates=[message_update(1, "/status")])
    _use_transport(monkeypatch, transport)

    code = cli.main(["poll", "--engine", "noop", "--ledger", str(ledger_path)])

    assert code == 0
    assert "Ideas filed via chat: 1" in transport.sent[0][0]


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


def test_poll_repo_flag_overrides_the_default(
    monkeypatch: pytest.MonkeyPatch, ledger_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_handle_idea(text: str, *, github, policy, engine, ledger, repo) -> str:
        captured["repo"] = repo
        return "ok"

    monkeypatch.setattr(cli, "handle_idea", fake_handle_idea)
    _use_transport(monkeypatch, FakeTransport(updates=[message_update(1, "/idea x")]))

    cli.main(["poll", "--engine", "noop", "--repo", "o/r", "--ledger", str(ledger_path)])

    assert captured["repo"] == "o/r"
