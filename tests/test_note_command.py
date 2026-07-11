from __future__ import annotations

import json
from pathlib import Path

import pytest
from mythings.engine import EngineRequest, EngineResult, NoopEngine
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import ALLOW, Action, Decision, PolicyResult
from mythings.testers import TesterStore

from conftest import all_text, as_tester, operator, replies
from mytelegrambot import note_command
from mytelegrambot.note_command import handle_note, metered_note

_TAGS = {"title": "Caching for the dispatcher", "tags": ["caching", "fleet", "perf"]}


class FakeGh:
    # A scoped-down `gh` covering exactly the calls file_note()/tag() make.
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._filed: dict | None = None

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:2] == ["issue", "create"]:
            self._filed = {
                "number": 9,
                "title": "a thought",
                "body": "the body of the note",
                "url": "https://github.com/o/r/issues/9",
                "labels": [{"name": "my-notes"}],
            }
            return self._filed["url"] + "\n"
        if argv[:2] == ["issue", "edit"]:
            return ""
        if argv[:2] == ["issue", "view"]:
            return json.dumps(self._filed)
        if argv[:2] == ["issue", "comment"]:
            return "https://github.com/o/r/issues/9#issuecomment-1\n"
        if argv[:2] == ["label", "create"]:
            return ""
        raise AssertionError(f"unexpected gh call: {argv}")


class ScriptedEngine:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls = 0

    def run(self, request: EngineRequest) -> EngineResult:
        self.calls += 1
        return EngineResult(text=json.dumps(self.payload))


class AllowAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return ALLOW


class DenyAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY, reason="no", rule="deny")


def _note(fake: FakeGh, *, engine, policy, ledger, args_text="a thought\nthe body"):
    return handle_note(
        args_text,
        github=GitHub(repo="o/r", runner=fake),
        policy=policy,
        engine=engine,
        ledger=ledger,
        repo="o/r",
        runner=fake,
    )


def test_note_confirms_the_capture_before_tagging(tmp_path: Path) -> None:
    # Filing is quick and tagging is the Engine call, so the note is confirmed as
    # safely captured before the wait, not after it.
    fake = FakeGh()
    engine = ScriptedEngine(_TAGS)

    sent = replies(
        _note(fake, engine=engine, policy=AllowAll(), ledger=Ledger(tmp_path / "l.jsonl"))
    )

    assert len(sent) == 2
    ack, tagged = sent
    assert "my-notes#9" in ack.text
    assert "https://github.com/o/r/issues/9" in ack.text
    assert "Caching for the dispatcher" in tagged.text


def test_note_files_and_tags(tmp_path: Path) -> None:
    fake = FakeGh()
    ledger = Ledger(tmp_path / "l.jsonl")
    engine = ScriptedEngine(_TAGS)

    reply = all_text(_note(fake, engine=engine, policy=AllowAll(), ledger=ledger))

    assert "my-notes#9" in reply
    assert "Caching for the dispatcher" in reply
    assert "caching, fleet, perf" in reply
    assert engine.calls == 1  # exactly one Engine call, MyNotes' own

    kinds = [e.kind for e in ledger]
    assert "note_filed" in kinds and "note_tagged" in kinds


def test_note_rejects_an_empty_body_without_filing(tmp_path: Path) -> None:
    fake = FakeGh()

    reply = _note(
        fake,
        engine=NoopEngine(),
        policy=AllowAll(),
        ledger=Ledger(tmp_path / "l.jsonl"),
        args_text="   ",
    )

    assert "Usage: /note" in all_text(reply)
    assert fake.calls == []


def test_note_denied_filing_does_not_tag(tmp_path: Path) -> None:
    fake = FakeGh()
    engine = ScriptedEngine(_TAGS)

    reply = _note(fake, engine=engine, policy=DenyAll(), ledger=Ledger(tmp_path / "l.jsonl"))

    assert "denied by policy" in all_text(reply)
    assert fake.calls == []
    assert engine.calls == 0  # no Engine spend on a refused filing


def test_note_with_noop_engine_still_replies(tmp_path: Path) -> None:
    fake = FakeGh()

    reply = _note(fake, engine=NoopEngine(), policy=AllowAll(), ledger=Ledger(tmp_path / "l.jsonl"))

    # NoopEngine yields no tags and a title falling back to the note's own body --
    # an honest degrade, never a fabricated one.
    text = all_text(reply)
    assert "my-notes#9" in text
    assert "the body of the note" in text


def test_an_oversized_note_reply_is_kept_whole_for_the_transport_to_chunk(
    tmp_path: Path,
) -> None:
    # Chunking is the transport's job now; the handler no longer truncates, which
    # used to throw away the tail of a long title.
    fake = FakeGh()

    reply = all_text(
        _note(
            fake,
            engine=ScriptedEngine({"title": "x" * 5000, "tags": []}),
            policy=AllowAll(),
            ledger=Ledger(tmp_path / "l.jsonl"),
        )
    )

    assert len(reply) > 4096
    assert "truncated" not in reply


def test_metered_note_refuses_an_exhausted_tester(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)
    calls = {"n": 0}

    def fake_handle_note(text: str, **kwargs: object):
        calls["n"] += 1
        from mytelegrambot.router import Reply

        return [Reply("noted")]

    monkeypatch.setattr(note_command, "handle_note", fake_handle_note)

    def go():
        return metered_note(
            "a thought",
            as_tester(tester),
            store=store,
            github=None,
            policy=AllowAll(),
            engine=None,
            ledger=Ledger(tmp_path / "l.jsonl"),
            repo="o/r",
        )

    assert all_text(go()) == "noted"
    assert store.get(tester.id).engine_used == 1

    assert "full allowance" in all_text(go())
    assert calls["n"] == 1  # refused before the Engine was reached


def test_metered_note_refunds_a_failed_call(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store = TesterStore(tmp_path / "t.db")
    tester, _ = store.register("ada", engine_quota=1, chat_id=999)

    def boom(text: str, **kwargs: object):
        raise RuntimeError("engine exploded")

    monkeypatch.setattr(note_command, "handle_note", boom)

    # A generator does nothing until drained, so the refund path only runs when
    # the daemon actually pulls the replies -- which is what _drain does.
    with pytest.raises(RuntimeError, match="engine exploded"):
        replies(
            metered_note(
                "a thought",
                as_tester(tester),
                store=store,
                github=None,
                policy=AllowAll(),
                engine=None,
                ledger=Ledger(tmp_path / "l.jsonl"),
                repo="o/r",
            )
        )

    assert store.get(tester.id).engine_used == 0


def test_the_operator_is_never_metered_for_notes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from mytelegrambot.router import Reply

    store = TesterStore(tmp_path / "t.db")
    monkeypatch.setattr(note_command, "handle_note", lambda text, **k: [Reply("noted")])

    for _ in range(5):
        assert (
            all_text(
                metered_note(
                    "a thought",
                    operator(),
                    store=store,
                    github=None,
                    policy=AllowAll(),
                    engine=None,
                    ledger=Ledger(tmp_path / "l.jsonl"),
                    repo="o/r",
                )
            )
            == "noted"
        )


def test_note_flags_when_the_tags_could_not_be_posted(tmp_path: Path) -> None:
    class AllowFileDenyComment:
        def evaluate(self, action: Action) -> PolicyResult:
            if action.kind == "issue-comment":
                return PolicyResult(Decision.DENY, reason="no", rule="deny_comment")
            return ALLOW

    fake = FakeGh()

    reply = _note(
        fake,
        engine=ScriptedEngine(_TAGS),
        policy=AllowFileDenyComment(),
        ledger=Ledger(tmp_path / "l.jsonl"),
    )

    text = all_text(reply)
    assert "could not be posted" in text
    assert not any(argv[:2] == ["issue", "comment"] for argv in fake.calls)


def test_a_note_that_cannot_be_tagged_is_still_reported_as_captured(tmp_path: Path) -> None:
    # MyNotes skips tagging a note it reads as having an empty body. The filing
    # already succeeded and is the part worth not losing, so this degrades to
    # "filed, but untagged" rather than reporting a title MyNotes never proposed.
    class EmptyBodied(FakeGh):
        def __call__(self, argv: list[str]) -> str:
            out = super().__call__(argv)
            if self._filed is not None:
                self._filed["body"] = ""
            return out

    fake = EmptyBodied()

    sent = replies(
        _note(
            fake,
            engine=ScriptedEngine(_TAGS),
            policy=AllowAll(),
            ledger=Ledger(tmp_path / "l.jsonl"),
        )
    )

    assert "my-notes#9" in sent[0].text
    assert "untagged" in sent[-1].text
