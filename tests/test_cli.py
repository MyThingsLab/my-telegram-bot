from __future__ import annotations

from pathlib import Path

import pytest
from mythings.ledger import Ledger

from conftest import ErrorTransport, FakeTransport, entry
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
