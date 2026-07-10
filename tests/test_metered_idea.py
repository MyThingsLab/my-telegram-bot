from __future__ import annotations

from pathlib import Path

import pytest
from mythings.engine import NoopEngine
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.testers import TesterStore

from conftest import as_tester, operator
from mytelegrambot import idea_command
from mytelegrambot.idea_command import metered_idea
from mytelegrambot.router import Reply

# Only the quota gate is under test here; handle_idea itself is covered in
# test_idea_command.py against a fake `gh`. Patching it out keeps this focused on
# the one question that costs money: who got to spend an Engine call.


def _call(monkeypatch: pytest.MonkeyPatch, principal, store, tmp_path: Path, *, boom=False):
    calls = {"n": 0}

    def fake_handle_idea(text: str, **kwargs: object) -> Reply:
        calls["n"] += 1
        if boom:
            raise RuntimeError("engine exploded")
        return Reply("brief")

    monkeypatch.setattr(idea_command, "handle_idea", fake_handle_idea)
    reply = metered_idea(
        "a tool",
        principal,
        store=store,
        github=GitHub(repo="o/r"),
        policy=None,
        engine=NoopEngine(),
        ledger=Ledger(tmp_path / "l.jsonl"),
        repo="o/r",
    )
    return reply.text, calls["n"]


def test_operator_is_never_metered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")

    for _ in range(3):
        reply, calls = _call(monkeypatch, operator(), store, tmp_path)
        assert reply == "brief"
    assert calls == 1  # one call per invocation


def test_tester_spends_a_reservation_per_idea(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=2, chat_id=999)

    _call(monkeypatch, as_tester(tester), store, tmp_path)
    assert store.get(tester.id).engine_used == 1


def test_exhausted_tester_is_refused_before_the_engine_is_called(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)

    first, calls = _call(monkeypatch, as_tester(tester), store, tmp_path)
    assert first == "brief"
    assert calls == 1

    second, calls = _call(monkeypatch, as_tester(tester), store, tmp_path)
    assert "full allowance" in second
    assert calls == 0  # the Engine was never reached: refused, not attempted
    assert store.get(tester.id).engine_used == 1


def test_a_failed_call_refunds_the_reservation(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # The reservation is taken before the call. If the call never happened, the
    # tester must not be billed for it.
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)

    with pytest.raises(RuntimeError, match="engine exploded"):
        _call(monkeypatch, as_tester(tester), store, tmp_path, boom=True)

    assert store.get(tester.id).engine_used == 0


def test_a_disabled_tester_cannot_spend(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=5, chat_id=999)
    store.set_enabled(tester.id, False)

    reply, calls = _call(monkeypatch, as_tester(tester), store, tmp_path)

    assert "full allowance" in reply
    assert calls == 0
