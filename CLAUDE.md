# my-telegram-bot — agent instructions

You are developing **my-telegram-bot**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** bridges the harness to the user over Telegram: pushes ledger
  notifications, and turns a `Policy` `ASK` decision into a real synchronous
  human confirmation instead of collapsing to `DENY` under an unattended
  runner.
- **Incremental notify cursor:** the default `notify` window is tracked by a
  **count** (`notified_count` = how many ledger entries the last run covered),
  an index into the append-only ledger — not a timestamp, which at second
  granularity with an exclusive boundary would silently drop entries sharing a
  wall-clock second with the watermark. A digest never includes the tool's own
  `notify` bookkeeping (its `ask` entries do appear — a human was prompted).
  `--since <ts>` is an ad-hoc manual window that ships everything after `ts`
  and deliberately does **not** move the cursor (its record carries no
  `notified_count`), so a manual re-send never consumes the automatic queue.
- **The single Engine call:** none — deterministic. This is a plumbing/comms
  tool, not a judgment tool; it relays existing `Action`/`Ledger` data
  verbatim, it never composes prose that could hallucinate over what it's
  relaying.
- **Invariants / rules:** no `Workspace` — no code edits, no PR ever. This
  tool *is* a `Policy` decorator (`TelegramPolicy`), not a new contract: it
  wraps an inner `Policy`, delegates non-`ASK` decisions untouched, and only
  for `ASK` sends a real Telegram Allow/Deny prompt and blocks (bounded by
  `timeout`) for a reply. **Fail-closed is non-negotiable**: on timeout, no
  reply, or any Telegram API error, resolve `DENY`, never `ALLOW`. A `notify`
  push that hits a transport error is likewise non-fatal — it records no notify
  entry (so the cursor holds and the same digest is retried next run) and
  returns `outcome="failure"` rather than crashing the sole comms channel. Calls the
  Telegram Bot API over stdlib `urllib.request` + `json` only — no
  `python-telegram-bot` SDK. `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` come from
  the environment, never logged, never written to the ledger. The network
  call to the Telegram API is the tool's system boundary (mocked in tests),
  not routed through `Policy` itself.
- **Backlog label:** `my-telegram-bot`.
