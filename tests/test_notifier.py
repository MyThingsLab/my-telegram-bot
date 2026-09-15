from __future__ import annotations

from pathlib import Path

from mythings.ledger import Ledger

from conftest import ErrorTransport, FakeTransport, entry
from mytelegrambot.notifier import notify

# The automatic (incremental) path only sends what a human is expected to act
# on; `--since` stays a full digest. Tests that exercise bucket *formatting*
# therefore go through `--since`, and tests of the automatic path use an
# alertable entry -- `needs_human`, `failure`, `spend_alert`, `halt`.
EPOCH = "2000-01-01T00:00:00Z"


def test_notify_pushes_alertable_entries(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        entry("fleet_dispatch", "dispatch", "needs_human", "stuck", ts="2026-07-06T01:00:00Z")
    )
    ledger.append(
        entry("fleet_dispatch", "dispatch", "failure", "crashed", ts="2026-07-06T02:00:00Z")
    )
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.outcome == "success"
    assert result.entries_count == 2
    assert len(transport.sent) == 1
    text, buttons = transport.sent[0]
    assert buttons is None
    assert "stuck" in text
    assert "crashed" in text

    written = [e for e in ledger if e.kind == "notify"][0]
    assert written.outcome == "success"
    assert written.data["message_id"] == result.message_id


def test_notify_stays_silent_when_every_entry_is_routine(tmp_path: Path) -> None:
    # The whole point. 301 `fleet_cycle/heartbeat/ok` entries is what a real
    # ledger looks like, and none of them is a reason to ring a phone.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for i in range(20):
        ledger.append(
            entry("fleet_cycle", "heartbeat", "ok", "build", ts=f"2026-07-06T01:{i:02d}:00Z")
        )
    ledger.append(entry("mytester", "run", "success", "cover pkg:f", ts="2026-07-06T02:00:00Z"))
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.outcome == "skipped"
    assert transport.sent == []


def test_routine_entries_are_counted_alongside_an_alert_not_listed(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    for i in range(5):
        ledger.append(
            entry("fleet_cycle", "heartbeat", "ok", "build", ts=f"2026-07-06T01:{i:02d}:00Z")
        )
    ledger.append(
        entry("fleet_dispatch", "dispatch", "failure", "crashed", ts="2026-07-06T02:00:00Z")
    )
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    text = transport.sent[0][0]
    assert result.entries_count == 1  # counts what was sent, not what was scanned
    assert "crashed" in text
    assert "5 routine entries not shown" in text
    assert "heartbeat" not in text


def test_a_silent_run_still_advances_the_cursor(tmp_path: Path) -> None:
    # If the cursor held whenever nothing was sent, the routine backlog would
    # grow without bound and every future alert would drag its whole count along.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytester", "run", "success", "routine", ts="2026-07-06T01:00:00Z"))
    transport = FakeTransport()

    assert notify(ledger, transport=transport).outcome == "skipped"
    ledger.append(
        entry("fleet_dispatch", "dispatch", "failure", "crashed", ts="2026-07-06T02:00:00Z")
    )
    result = notify(ledger, transport=transport)

    assert result.outcome == "success"
    text = transport.sent[0][0]
    assert "crashed" in text
    assert "routine entries not shown" not in text  # the routine one was consumed


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
    ledger.append(
        entry("fleet_dispatch", "dispatch", "failure", "crashed", ts="2026-07-06T01:00:00Z")
    )

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
    assert "crashed" in good.sent[0][0]


def test_notify_second_call_is_incremental(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(
        entry("fleet_dispatch", "dispatch", "failure", "crashed", ts="2026-07-06T01:00:00Z")
    )
    transport = FakeTransport()

    first = notify(ledger, transport=transport)
    assert first.outcome == "success"

    second = notify(ledger, transport=transport)

    assert second.outcome == "skipped"  # nothing new since the first notify
    assert len(transport.sent) == 1  # only the first call actually sent


def test_notify_never_reports_its_own_digest_bookkeeping(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("myguard", "act", "failure", "first", ts="2026-07-06T01:00:00Z"))
    transport = FakeTransport()

    notify(ledger, transport=transport)  # records a mytelegrambot/notify entry
    ledger.append(entry("myguard", "act", "failure", "force push", ts="2026-07-06T03:00:00Z"))
    notify(ledger, transport=transport)

    second_digest = transport.sent[1][0]
    assert "force push" in second_digest  # the real new event is there
    assert "mytelegrambot/notify" not in second_digest  # its own push isn't
    assert "routine entries not shown" not in second_digest  # nor as a count


def test_notify_never_reports_its_own_poll_bookkeeping(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("myguard", "act", "failure", "crashed", ts="2026-07-06T01:00:00Z"))
    ledger.append(
        entry("mytelegrambot", "poll", "skipped", "no updates", ts="2026-07-06T02:00:00Z")
    )
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.entries_count == 1
    assert "crashed" in transport.sent[0][0]
    assert "poll" not in transport.sent[0][0]
    # Its own bookkeeping is dropped before counting, so it is not a "routine" one.
    assert "routine entries not shown" not in transport.sent[0][0]


def test_notify_includes_its_own_ask_entries_when_they_are_pending(tmp_path: Path) -> None:
    # An `ask` the human never answered resolves to a fail-closed DENY, and the
    # action it was gating did not happen -- that is squarely "waiting on you".
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytelegrambot", "ask", "needs_human", "rm -rf /", ts="2026-07-06T01:00Z"))
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.entries_count == 1
    assert "rm -rf /" in transport.sent[0][0]


def test_notify_delivers_entry_whose_timestamp_precedes_the_cursor(tmp_path: Path) -> None:
    # The count-based cursor must deliver a later-appended entry even when its
    # timestamp is older than the previous notify's -- the exact case the old
    # timestamp watermark dropped.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("myguard", "act", "failure", "first", ts="2026-07-06T01:00:00Z"))
    transport = FakeTransport()

    notify(ledger, transport=transport)  # notify record gets a real (later) ts
    # A second worker's entry, backdated to the same second as the first:
    ledger.append(entry("myguard", "act", "failure", "backdated", ts="2026-07-06T01:00:00Z"))
    result = notify(ledger, transport=transport)

    assert result.outcome == "success"
    assert result.entries_count == 1
    assert "backdated" in transport.sent[1][0]


def test_since_query_does_not_consume_the_incremental_queue(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("myguard", "act", "failure", "old", ts="2026-07-06T01:00:00Z"))
    ledger.append(entry("myguard", "act", "failure", "recent", ts="2026-07-06T05:00:00Z"))
    transport = FakeTransport()

    manual = notify(ledger, transport=transport, since="2026-07-06T03:00:00Z")
    assert manual.entries_count == 1  # only "recent" is after the since bound

    incremental = notify(ledger, transport=transport)

    # The manual --since run left the cursor at 0, so both entries still ship.
    assert incremental.outcome == "success"
    assert incremental.entries_count == 2
    assert "old" in transport.sent[1][0]
    assert "recent" in transport.sent[1][0]


def test_since_is_the_escape_hatch_and_still_shows_routine_entries(tmp_path: Path) -> None:
    # A human who names a window asked for the whole picture. This is the path
    # the alert line's "`notify --since <ts>` for all" actually points at.
    ledger = Ledger(tmp_path / "ledger.jsonl")
    ledger.append(entry("mytester", "run", "success", "cover pkg:f", ts="2026-07-06T01:00:00Z"))
    transport = FakeTransport()

    result = notify(ledger, transport=transport, since=EPOCH)

    assert result.outcome == "success"
    text = transport.sent[0][0]
    assert "📋 Other:" in text
    assert "cover pkg:f" in text


def test_a_digest_past_telegrams_limit_is_split_rather_than_rejected(tmp_path: Path) -> None:
    # A digest is as long as the backlog makes it. Sent as one message, a big
    # enough backlog 400'd, held the cursor, and was retried identically forever --
    # a digest that could never be delivered and only grew. Splitting is what stops
    # that from being a permanent wedge.
    ledger = Ledger(tmp_path / "l.jsonl")
    for i in range(60):
        ledger.record(
            "myidea", "idea_filed", "failure", detail="x" * 200, ts=f"2026-07-12T00:{i:02d}:00"
        )
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.outcome == "success"
    assert len(transport.sent) > 1
    assert all(len(text) <= 4096 for text, _inline in transport.sent)
    assert result.message_id == 1  # the first message of the digest


def test_a_digest_that_fits_is_still_one_message(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("myidea", "idea_filed", "failure", detail="a small thing")
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert len(transport.sent) == 1
    assert result.outcome == "success"


def test_digest_groups_a_shipped_dispatch_under_its_own_heading(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("fleet_dispatch", "dispatch", "success", detail="my-guard#12: opened PR #7")
    transport = FakeTransport()

    notify(ledger, transport=transport, since=EPOCH)

    text = transport.sent[0][0]
    assert "🚢 Shipped:" in text
    assert "opened PR #7" in text


def test_a_shipped_dispatch_alone_does_not_ring_the_phone(tmp_path: Path) -> None:
    # Work landing is the fleet doing its job, not an event needing a human.
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("fleet_dispatch", "dispatch", "success", detail="my-guard#12: opened PR #7")
    transport = FakeTransport()

    assert notify(ledger, transport=transport).outcome == "skipped"
    assert transport.sent == []


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


def test_the_other_spelling_of_failure_also_counts_as_failed(tmp_path: Path) -> None:
    # `outcome` is a free-form string and both spellings are live in the real
    # ledgers. Knowing only "failure" filed every `failed` dispatch under Other,
    # which after this change would mean not sending it at all.
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("fleet_dispatch", "dispatch", "failed", detail="my-guard#9: crashed")
    transport = FakeTransport()

    result = notify(ledger, transport=transport)

    assert result.outcome == "success"
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

    notify(ledger, transport=transport, since=EPOCH)

    text = transport.sent[0][0]
    assert "💰 Cost: $3.75" in text
    assert "session cost" not in text  # rolled up, not listed per-entry


def test_digest_with_only_usage_entries_still_reports_cost(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("fleet_dispatch", "usage", "success", detail="session cost", cost_usd=4.0)
    transport = FakeTransport()

    result = notify(ledger, transport=transport, since=EPOCH)

    assert result.outcome == "success"
    assert "💰 Cost: $4.00" in transport.sent[0][0]


def test_digest_puts_an_uncategorized_entry_under_other(tmp_path: Path) -> None:
    ledger = Ledger(tmp_path / "l.jsonl")
    ledger.record("myidea", "idea_filed", "success", detail="filed idea #9")
    transport = FakeTransport()

    notify(ledger, transport=transport, since=EPOCH)

    text = transport.sent[0][0]
    assert "📋 Other:" in text
    assert "filed idea #9" in text
