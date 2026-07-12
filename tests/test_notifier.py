from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger

from conftest import ErrorTransport, FakeTransport, entry
from mytelegrambot.notifier import notify


def test_notify_pushes_new_entries_as_one_message(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytester", "run", "success", "cover pkg:f", ts="2026-07-06T01:00:00Z"))
    ledger.append(entry("myguard", "ask", "success", "destructive push", ts="2026-07-06T02:00:00Z"))
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.outcome == "success"
    assert result.entries_count == 2
    assert len(transport.sent) == 1
    text, buttons = transport.sent[0]
    assert buttons is None
    assert "cover pkg:f" in text
    assert "destructive push" in text

    written = [e for e in ledger if e.kind == "notify"][0]
    assert written.outcome == "success"
    assert written.data["message_id"] == result.message_id


def test_notify_skips_when_nothing_new(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.outcome == "skipped"
    assert result.entries_count == 0
    assert transport.sent == []
    assert list(ledger)[0].outcome == "skipped"


def test_notify_send_failure_is_graceful_and_entries_are_retried(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytester", "run", "success", "cover pkg:f", ts="2026-07-06T01:00:00Z"))

    failed = notify(ledger, transport=ErrorTransport())  # Telegram outage on send

    assert failed.outcome == "failure"
    assert failed.entries_count == 1
    assert failed.message_id is None
    # A failed send records no notify entry, so the watermark must not advance.
    assert [e for e in ledger if e.kind == "notify"] == []

    good = FakeTransport()
    retry = notify(ledger, transport=good)

    assert retry.outcome == "success"
    assert retry.entries_count == 1  # the same entry is re-sent, not lost
    assert "cover pkg:f" in good.sent[0][0]


def test_notify_second_call_is_incremental(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytester", "run", "success", "cover pkg:f", ts="2026-07-06T01:00:00Z"))
    transport = FakeTransport()

    first = notify(ledger, transport=transport)
    assert first.outcome == "success"

    second = notify(ledger, transport=transport)

    assert second.outcome == "skipped"  # nothing new since the first notify
    assert len(transport.sent) == 1  # only the first call actually sent


def test_notify_never_reports_its_own_digest_bookkeeping(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytester", "run", "success", "cover pkg:f", ts="2026-07-06T01:00:00Z"))
    transport = FakeTransport()

    notify(ledger, transport=transport)  # records a mytelegrambot/notify entry
    ledger.append(entry("myguard", "ask", "success", "force push", ts="2026-07-06T03:00:00Z"))
    notify(ledger, transport=transport)

    second_digest = transport.sent[1][0]
    assert "force push" in second_digest  # the real new event is there
    assert "mytelegrambot/notify" not in second_digest  # its own push isn't


def test_notify_never_reports_its_own_poll_bookkeeping(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytester", "run", "success", "cover pkg:f", ts="2026-07-06T01:00:00Z"))
    ledger.append(
        entry("mytelegrambot", "poll", "skipped", "no updates", ts="2026-07-06T02:00:00Z")
    )
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.entries_count == 1
    assert "cover pkg:f" in transport.sent[0][0]
    assert "poll" not in transport.sent[0][0]


def test_notify_includes_idea_entries_filed_via_the_bot(tmp_path: Path) -> None:
    # An idea filed/explored through /idea is a real fleet event, exactly like
    # an `ask` -- it must appear in the digest even though it was recorded by
    # a command this same tool routed.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        entry(
            "myidea",
            "idea_filed",
            "success",
            "filed idea #9: a new tool",
            ts="2026-07-06T01:00:00Z",
        )
    )
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.entries_count == 1
    assert "filed idea #9" in transport.sent[0][0]


def test_notify_includes_its_own_ask_entries(tmp_path: Path) -> None:
    # An `ask` is a real fleet event (a human approved/denied an action) and
    # must appear in the digest, unlike the tool's own notify bookkeeping.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytelegrambot", "ask", "denied", "rm -rf /", ts="2026-07-06T01:00:00Z"))
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.entries_count == 1
    assert "rm -rf /" in transport.sent[0][0]


def test_notify_delivers_entry_whose_timestamp_precedes_the_cursor(tmp_path: Path) -> None:
    # The count-based cursor must deliver a later-appended entry even when its
    # timestamp is older than the previous notify's -- the exact case the old
    # timestamp watermark dropped.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytester", "run", "success", "first", ts="2026-07-06T01:00:00Z"))
    transport = FakeTransport()

    notify(ledger, transport=transport)  # notify record gets a real (later) ts
    # A second worker's entry, backdated to the same second as the first:
    ledger.append(entry("myguard", "ask", "success", "backdated", ts="2026-07-06T01:00:00Z"))
    result = notify(ledger, transport=transport)

    assert result.outcome == "success"
    assert result.entries_count == 1
    assert "backdated" in transport.sent[1][0]


def test_since_query_does_not_consume_the_incremental_queue(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytester", "run", "success", "old", ts="2026-07-06T01:00:00Z"))
    ledger.append(entry("myguard", "ask", "success", "recent", ts="2026-07-06T05:00:00Z"))
    transport = FakeTransport()

    manual = notify(ledger, transport=transport, since="2026-07-06T03:00:00Z")
    assert manual.entries_count == 1  # only "recent" is after the since bound

    incremental = notify(ledger, transport=transport)

    # The manual --since run left the cursor at 0, so both entries still ship.
    assert incremental.outcome == "success"
    assert incremental.entries_count == 2
    assert "old" in transport.sent[1][0]
    assert "recent" in transport.sent[1][0]


def test_a_digest_past_telegrams_limit_is_split_rather_than_rejected(tmp_path: Path) -> None:
    # A digest is as long as the backlog makes it. Sent as one message, a big
    # enough backlog 400'd, held the cursor, and was retried identically forever --
    # a digest that could never be delivered and only grew. Splitting is what stops
    # that from being a permanent wedge.
    ledger = Ledger(tmp_path / "l.jsonl")
    for i in range(60):
        ledger.record(
            "myidea", "idea_filed", "success", detail="x" * 200, ts=f"2026-07-12T00:{i:02d}:00"
        )
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.outcome == "success"
    assert len(transport.sent) > 1
    assert all(len(text) <= 4096 for text, _inline in transport.sent)
    assert result.message_id == 1  # the first message of the digest


def test_a_digest_that_fits_is_still_one_message(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("myidea", "idea_filed", "success", detail="a small thing")
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert len(transport.sent) == 1
    assert result.outcome == "success"


def test_digest_groups_a_shipped_dispatch_under_its_own_heading(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record(
        "fleet_dispatch", "dispatch", "success", detail="my-guard#12: opened PR #7"
    )
    transport = FakeTransport()

    notify(ledger, transport=transport)

    text = transport.sent[0][0]
    assert "🚢 Shipped:" in text
    assert "opened PR #7" in text


def test_digest_groups_a_needs_human_dispatch_under_waiting_on_you(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record(
        "fleet_dispatch", "dispatch", "needs_human", detail="my-guard#3: gave up after 3 attempts"
    )
    transport = FakeTransport()

    notify(ledger, transport=transport)

    text = transport.sent[0][0]
    assert "⏳ Waiting on you:" in text
    assert "gave up after 3 attempts" in text


def test_digest_groups_a_spend_alert_and_halt_under_waiting_on_you(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("fleet_dispatch", "spend_alert", "success", detail="crossed 80% of cap")
    ledger.record("mytelegrambot", "halt", "success", detail="halted from spend alert")
    transport = FakeTransport()

    notify(ledger, transport=transport)

    text = transport.sent[0][0]
    assert "⏳ Waiting on you:" in text
    assert "crossed 80% of cap" in text
    assert "halted from spend alert" in text


def test_digest_groups_a_failure_under_failed(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("fleet_dispatch", "dispatch", "failure", detail="my-guard#9: crashed")
    transport = FakeTransport()

    notify(ledger, transport=transport)

    text = transport.sent[0][0]
    assert "❌ Failed:" in text
    assert "my-guard#9: crashed" in text


def test_digest_rolls_up_usage_entries_into_a_cost_total_not_individual_lines(
    tmp_path: Path,
) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("fleet_dispatch", "usage", "success", detail="session cost", cost_usd=1.25)
    ledger.record("fleet_dispatch", "usage", "success", detail="session cost", cost_usd=2.50)
    transport = FakeTransport()

    notify(ledger, transport=transport)

    text = transport.sent[0][0]
    assert "💰 Cost: $3.75" in text
    assert "session cost" not in text  # rolled up, not listed per-entry


def test_digest_with_only_usage_entries_still_reports_cost(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("fleet_dispatch", "usage", "success", detail="session cost", cost_usd=4.0)
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.outcome == "success"
    assert "💰 Cost: $4.00" in transport.sent[0][0]


def test_digest_puts_an_uncategorized_entry_under_other(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("myidea", "idea_filed", "success", detail="filed idea #9")
    transport = FakeTransport()

    notify(ledger, transport=transport)

    text = transport.sent[0][0]
    assert "📋 Other:" in text
    assert "filed idea #9" in text
