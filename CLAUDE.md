# my-telegram-bot — agent instructions

You are developing **my-telegram-bot**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `mythings-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** bridges the harness to the user over Telegram: pushes ledger
  notifications, and turns a `Policy` `ASK` decision into a real synchronous
  human confirmation instead of collapsing to `DENY` under an unattended
  runner.
- **The single Engine call:** none — deterministic. This is a plumbing/comms
  tool, not a judgment tool; it relays existing `Action`/`Ledger` data
  verbatim, it never composes prose that could hallucinate over what it's
  relaying.
- **Invariants / rules:** no `Workspace` — no code edits, no PR ever. This
  tool *is* a `Policy` decorator (`TelegramPolicy`), not a new contract: it
  wraps an inner `Policy`, delegates non-`ASK` decisions untouched, and only
  for `ASK` sends a real Telegram Allow/Deny prompt and blocks (bounded by
  `timeout`) for a reply. **Fail-closed is non-negotiable**: on timeout, no
  reply, or any Telegram API error, resolve `DENY`, never `ALLOW`. Calls the
  Telegram Bot API over stdlib `urllib.request` + `json` only — no
  `python-telegram-bot` SDK. `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` come from
  the environment, never logged, never written to the ledger. The network
  call to the Telegram API is the tool's system boundary (mocked in tests),
  not routed through `Policy` itself.
- **Backlog label:** `my-telegram-bot`.
