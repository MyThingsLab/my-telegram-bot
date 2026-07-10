from __future__ import annotations

import json
from pathlib import Path

from mythings.engine import EngineRequest, EngineResult, NoopEngine
from mythings.github import GitHub
from mythings.ledger import Ledger
from mythings.policy import Action, Decision, PolicyResult

from mytelegrambot.idea_command import handle_idea

# A small, self-contained fake `gh` runner covering exactly the subcommands
# file_idea()/explore() issue (my-idea's own tests use an equivalent FakeGh,
# but it isn't exported from that package, so this is a scoped-down copy of
# the same idea rather than a cross-repo test-fixture import).


class FakeGh:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self.comments: list[str] = []
        self._filed: dict | None = None

    def __call__(self, argv: list[str]) -> str:
        self.calls.append(argv)
        if argv[:2] == ["issue", "create"]:
            self._filed = {
                "number": 9,
                "title": "",
                "body": "",
                "url": "https://github.com/o/r/issues/9",
                "labels": [{"name": "my-idea"}],
            }
            return self._filed["url"] + "\n"
        if argv[:2] == ["issue", "edit"]:
            return ""
        if argv[:2] == ["issue", "list"]:
            return json.dumps([self._filed] if self._filed else [])
        if argv[:2] == ["repo", "list"]:
            return json.dumps([{"name": "my-scraper"}])
        if argv[0] == "api" and "contents/docs/tools" in argv[1]:
            return "my-dashboard.md\n"
        if argv[:2] == ["issue", "comment"]:
            self.comments.append(argv[argv.index("--body") + 1])
            return ""
        raise AssertionError(f"unexpected gh call: {argv}")


class ScriptedEngine:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[EngineRequest] = []

    def run(self, request: EngineRequest) -> EngineResult:
        self.calls.append(request)
        return EngineResult(text=json.dumps(self.payload))


class AllowAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.ALLOW)


class DenyAll:
    def evaluate(self, action: Action) -> PolicyResult:
        return PolicyResult(Decision.DENY)


BRIEF = {
    "restatement": "A new idea captured from Telegram.",
    "overlaps": [],
    "contract_fit": "fits the fleet",
    "risks": [],
    "smallest_slice": "the smallest slice",
    "verdict": "build",
    "fold_into": None,
    "questions": [],
}


def _reply(fake: FakeGh, *, engine, policy, ledger, args_text: str = "a new tool idea"):
    github = GitHub(repo="o/r", runner=fake)
    return handle_idea(
        args_text,
        github=github,
        policy=policy,
        engine=engine,
        ledger=ledger,
        repo="o/r",
        runner=fake,
    )


def _handle(fake: FakeGh, *, engine, policy, ledger, args_text: str = "a new tool idea") -> str:
    return _reply(fake, engine=engine, policy=policy, ledger=ledger, args_text=args_text).text


def test_handle_idea_files_and_explores_in_one_reply(tmp_path: Path) -> None:
    fake = FakeGh()
    ledger = Ledger(tmp_path / "ledger.jsonl")

    reply = _handle(fake, engine=ScriptedEngine(BRIEF), policy=AllowAll(), ledger=ledger)

    assert "Filed as my-idea#9" in reply
    assert "https://github.com/o/r/issues/9" in reply
    assert "A new idea captured from Telegram." in reply
    assert "Verdict:** build" in reply
    assert fake.comments  # the brief was posted back on the issue

    kinds = [e.kind for e in ledger]
    assert kinds == ["idea_filed", "idea_explored"]


def test_handle_idea_splits_first_line_as_title(tmp_path: Path) -> None:
    fake = FakeGh()
    ledger = Ledger(tmp_path / "ledger.jsonl")

    _handle(
        fake,
        engine=ScriptedEngine(BRIEF),
        policy=AllowAll(),
        ledger=ledger,
        args_text="a scraper dashboard\nmore detail on a second line",
    )

    create_call = next(c for c in fake.calls if c[:2] == ["issue", "create"])
    assert create_call[create_call.index("--title") + 1] == "a scraper dashboard"
    assert create_call[create_call.index("--body") + 1] == "more detail on a second line"


def test_handle_idea_uses_fallback_body_when_none_given(tmp_path: Path) -> None:
    fake = FakeGh()
    ledger = Ledger(tmp_path / "ledger.jsonl")

    _handle(
        fake,
        engine=ScriptedEngine(BRIEF),
        policy=AllowAll(),
        ledger=ledger,
        args_text="just a title",
    )

    create_call = next(c for c in fake.calls if c[:2] == ["issue", "create"])
    body = create_call[create_call.index("--body") + 1]
    assert "Telegram" in body


def test_handle_idea_rejects_empty_title_without_filing(tmp_path: Path) -> None:
    fake = FakeGh()
    ledger = Ledger(tmp_path / "ledger.jsonl")

    reply = _handle(
        fake, engine=ScriptedEngine(BRIEF), policy=AllowAll(), ledger=ledger, args_text="   "
    )

    assert "Usage:" in reply
    assert fake.calls == []
    assert list(ledger) == []


def test_handle_idea_denied_filing_returns_short_reply_and_does_not_explore(
    tmp_path: Path,
) -> None:
    fake = FakeGh()
    ledger = Ledger(tmp_path / "ledger.jsonl")

    reply = _handle(fake, engine=ScriptedEngine(BRIEF), policy=DenyAll(), ledger=ledger)

    assert "denied by policy" in reply
    assert fake.calls == []  # file_idea's own policy check short-circuits before any gh call
    assert list(ledger) == []


def test_handle_idea_noop_engine_still_replies_with_deterministic_brief(tmp_path: Path) -> None:
    fake = FakeGh()
    ledger = Ledger(tmp_path / "ledger.jsonl")

    reply = _handle(fake, engine=NoopEngine(), policy=AllowAll(), ledger=ledger)

    assert "Filed as my-idea#9" in reply
    assert "No judgment engine attached" in reply  # honest degrade, never fabricated


def test_handle_idea_flags_when_the_brief_could_not_be_posted(tmp_path: Path) -> None:
    # DenyAll would deny *filing* itself; to exercise the "explore posted=False"
    # branch instead, use a policy that allows issue-create but denies
    # issue-comment.
    class AllowCreateDenyComment:
        def evaluate(self, action: Action) -> PolicyResult:
            if action.kind == "issue-comment":
                return PolicyResult(Decision.DENY)
            return PolicyResult(Decision.ALLOW)

    fake = FakeGh()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    reply = _handle(
        fake, engine=ScriptedEngine(BRIEF), policy=AllowCreateDenyComment(), ledger=ledger
    )

    assert "Filed as my-idea#9" in reply
    assert "could not be posted" in reply
    assert fake.comments == []


def test_truncates_a_reply_longer_than_telegrams_message_limit(tmp_path: Path) -> None:
    fake = FakeGh()
    ledger = Ledger(tmp_path / "ledger.jsonl")
    huge_brief = {**BRIEF, "risks": ["x" * 500 for _ in range(20)]}

    reply = _handle(fake, engine=ScriptedEngine(huge_brief), policy=AllowAll(), ledger=ledger)

    assert len(reply) <= 4096
    assert reply.endswith("…[truncated]")
