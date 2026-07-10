from __future__ import annotations

import json
from pathlib import Path

import pytest
from myguide.catalog import Catalog, ToolCard
from myguide.guide import Guide
from mythings.engine import EngineRequest, EngineResult, NoopEngine
from mythings.ledger import Ledger
from mythings.policy import ALLOW, Action, PolicyResult
from mythings.testers import TesterStore

from conftest import as_tester, operator
from mytelegrambot.guide_command import (
    handle_catalog,
    metered_wish,
    trial_buttons,
    trial_tool,
)
from mytelegrambot.router import CallbackAction

_CARDS = (
    ToolCard(
        repo="my-archivist",
        tool="MyArchivist",
        gloss="Tidies a folder of books.",
        reads="the names of files in a folder",
        produces="a list of everything you own",
        never="opens, moves, renames or deletes a file",
    ),
    ToolCard(
        repo="my-scraper",
        tool="MyScraper",
        gloss="Collects pages from the web.",
        reads="a list of addresses",
        produces="the text of each page",
        never="signs in as you",
    ),
)


def _catalog() -> Catalog:
    return Catalog(cards=_CARDS, unexplained=("my-guard",), fleet_repos=frozenset({"my-guard"}))


class AllowAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return ALLOW


class ScriptedEngine:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def run(self, request: EngineRequest) -> EngineResult:
        self.calls += 1
        return EngineResult(text=json.dumps(self.payload))


def _guide(tmp_path: Path, engine=None) -> Guide:
    return Guide(
        catalog=_catalog(),
        ledger=Ledger(tmp_path / "l.jsonl"),
        engine=engine or NoopEngine(),
        policy=AllowAll(),
    )


# ---------------------------------------------------------------- /catalog


def test_catalog_lists_every_card_and_offers_a_trial_button(tmp_path: Path) -> None:
    reply = handle_catalog("", operator(), guide=_guide(tmp_path))

    assert "MyArchivist" in reply.text
    assert "MyScraper" in reply.text
    assert "Not yet explained: my-guard" in reply.text

    assert reply.inline is not None
    datas = [d for row in reply.inline for _label, d in row]
    assert datas == ["guide:my-archivist:trial", "guide:my-scraper:trial"]


def test_catalog_costs_a_tester_nothing(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=5, chat_id=999)

    handle_catalog("", as_tester(tester), guide=_guide(tmp_path))

    assert store.get(tester.id).engine_used == 0


# ---------------------------------------------------------------- /wish


def test_wish_spends_exactly_one_engine_call_and_offers_the_matched_tool(
    tmp_path: Path,
) -> None:
    engine = ScriptedEngine(
        {
            "understood": "tidy my books",
            "matches": [{"tool": "my-archivist", "why": "it tidies books", "confidence": "high"}],
        }
    )
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=5, chat_id=999)

    reply = metered_wish(
        "tidy my books", as_tester(tester), store=store, guide=_guide(tmp_path, engine)
    )

    assert engine.calls == 1
    assert store.get(tester.id).engine_used == 1
    assert "MyArchivist" in reply.text
    datas = [d for row in reply.inline or () for _label, d in row]
    assert "guide:my-archivist:trial" in datas


def test_wish_refuses_an_exhausted_tester_before_the_engine(tmp_path: Path) -> None:
    engine = ScriptedEngine({"understood": "x", "matches": []})
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)
    store.reserve_engine_call(tester.id)  # already spent

    reply = metered_wish("anything", as_tester(tester), store=store, guide=_guide(tmp_path, engine))

    assert "full allowance" in reply.text
    assert engine.calls == 0


def test_wish_refunds_when_the_engine_call_raises(tmp_path: Path) -> None:
    class Boom:
        def run(self, request: EngineRequest) -> EngineResult:
            raise RuntimeError("engine exploded")

    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)

    with pytest.raises(RuntimeError, match="engine exploded"):
        metered_wish("x", as_tester(tester), store=store, guide=_guide(tmp_path, Boom()))

    assert store.get(tester.id).engine_used == 0


def test_wish_without_text_asks_for_some_and_spends_nothing(tmp_path: Path) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)

    reply = metered_wish("   ", as_tester(tester), store=store, guide=_guide(tmp_path))

    assert "Usage: /wish" in reply.text
    assert store.get(tester.id).engine_used == 0


def test_the_operator_is_never_metered_for_wishes(tmp_path: Path) -> None:
    engine = ScriptedEngine({"understood": "x", "matches": []})
    store = TesterStore(tmp_path / "t.db")

    for _ in range(3):
        metered_wish("x", operator(), store=store, guide=_guide(tmp_path, engine))

    assert engine.calls == 3


# ---------------------------------------------------------------- trial button


def test_trial_narrates_a_dry_run_and_writes_nothing(tmp_path: Path) -> None:
    guide = _guide(tmp_path)

    reply = trial_tool(CallbackAction("guide:trial", "my-archivist"), operator(), guide=guide)

    assert "MyArchivist" in reply.text
    assert "dry run" in reply.text.lower()
    # trial_message offers yes/no choices for the --for-real path; this surface
    # doesn't expose it, so they must not render as dead buttons.
    assert reply.inline is None


def test_any_authorized_principal_may_trial_any_tool(tmp_path: Path) -> None:
    # Unlike an idea, a dry run has no owner and costs nothing.
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=5, chat_id=999)

    reply = trial_tool(
        CallbackAction("guide:trial", "my-scraper"), as_tester(tester), guide=_guide(tmp_path)
    )

    assert "MyScraper" in reply.text
    assert store.get(tester.id).engine_used == 0


def test_a_forged_trial_button_for_an_unknown_tool_is_refused(tmp_path: Path) -> None:
    reply = trial_tool(
        CallbackAction("guide:trial", "my-nonexistent"), operator(), guide=_guide(tmp_path)
    )

    assert reply.text == "I don't know that tool."


def test_trial_buttons_drop_choices_that_are_not_catalogued_tools() -> None:
    from myguide.render import Choice, Message

    catalog = _catalog()
    message = Message(
        title="t",
        choices=(Choice("yes", "Do it"), Choice("my-archivist", "Try MyArchivist")),
    )

    keyboard = trial_buttons(catalog, message)

    assert keyboard is not None
    datas = [d for row in keyboard for _label, d in row]
    assert datas == ["guide:my-archivist:trial"]  # "yes" dropped


def test_trial_buttons_are_none_when_nothing_is_offered() -> None:
    from myguide.render import Choice, Message

    message = Message(title="t", choices=(Choice("yes", "Do it"),))

    assert trial_buttons(_catalog(), message) is None


def test_render_truncates_a_reply_longer_than_telegrams_limit(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mytelegrambot import guide_command

    monkeypatch.setattr(guide_command, "_TELEGRAM_MAX_LEN", 60)

    reply = handle_catalog("", operator(), guide=_guide(tmp_path))

    assert len(reply.text) <= 60
    assert "truncated" in reply.text


def test_render_includes_a_message_note(tmp_path: Path) -> None:
    from myguide.render import Message

    from mytelegrambot.guide_command import _render

    reply = _render(_catalog(), Message(title="t", lines=("a",), note="a closing note"))

    assert "a closing note" in reply.text
