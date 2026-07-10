# my-telegram-bot — agent instructions

You are developing **my-telegram-bot**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** bridges the harness to the user over Telegram: pushes ledger
  notifications, turns a `Policy` `ASK` decision into a real synchronous
  human confirmation instead of collapsing to `DENY` under an unattended
  runner, and — the one inbound direction — lets the human capture a
  `my-idea` from chat via `/idea <text>` so no idea gets missed for lack of a
  terminal nearby.
- **Incremental notify cursor:** the default `notify` window is tracked by a
  **count** (`notified_count` = how many ledger entries the last run covered),
  an index into the append-only ledger — not a timestamp, which at second
  granularity with an exclusive boundary would silently drop entries sharing a
  wall-clock second with the watermark. A digest never includes the tool's own
  `notify` bookkeeping (its `ask` entries do appear — a human was prompted).
  `--since <ts>` is an ad-hoc manual window that ships everything after `ts`
  and deliberately does **not** move the cursor (its record carries no
  `notified_count`), so a manual re-send never consumes the automatic queue.
- **The single Engine call:** none *directly* — `notify`/`ask` stay fully
  deterministic, relaying existing `Action`/`Ledger` data verbatim, never
  composing prose that could hallucinate over what it's relaying. The `/idea`
  path handled by `poll` makes exactly one Engine call, but this tool never
  calls the Engine itself: that call is entirely delegated to MyIdea's own
  already-shipped, already-tested `myidea.explore.explore()`, imported as a
  library. The other `poll` commands stay deterministic, no Engine, no side
  effects: `/help` and `/start` echo a fixed command list (`help_command.py`);
  `/status` renders counts read straight from the ledger (`status_command.py`).
  The `setup` CLI subcommand is a one-off admin call (no ledger, no Engine)
  that registers the `setMyCommands` menu and the persistent reply keyboard
  (`menu.py`); the keyboard's labels are literal `/commands` so a tap is just
  ordinary command text — no `callback_query`, so it sidesteps the shared-offset
  race entirely (unlike an inline keyboard, which would not).
- **Dependency-direction exception, deliberate:** every other cross-tool
  relationship in the fleet is a CLI hand-off, not a package dependency (e.g.
  MyPresentation → MyTypster), to keep tool repos decoupled at the code level.
  This tool imports `my-idea` (and, transitively, `my-guard`) directly as
  Python packages instead — a one-off exception because `poll` needs
  `file_idea`'s/`explore`'s structured return values (`Issue`, `ExploreResult`)
  in-process to compose one synchronous Telegram reply, not a fire-and-forget
  hand-off. Do not "fix" this back to a subprocess call.
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
- **`poll` invariants:** `fetch_updates` silently drops any update not
  addressed to this bot's single configured `chat_id` — inbound free text is
  a new attack surface (unlike `poll_decision`, which only ever replies to a
  button on a message only the intended recipient could see), so this keeps
  the tool single-user by construction. The inbound cursor is a
  `kind=poll` ledger entry carrying the last-seen `update_id` (mirrors
  `notify`'s count-based cursor, just keyed by Telegram's id instead of a
  length) and only advances after every fetched update in the batch has been
  routed (or deliberately skipped, e.g. a `callback_query`) — a reply-send
  failure is swallowed so the cursor still advances (a command's side effects,
  like a filed GitHub issue, already happened and must not be repeated).
  **Known, accepted limitations, not silently papered over:** (1) a process
  crash between a command's side effects completing and this run's own
  `kind=poll` ledger write would cause the next poll to reprocess that update
  — GitHub issue creation isn't idempotent, so a crash in that exact window
  could file a duplicate idea; acceptable for a personal, low-frequency,
  single-user tool. (2) `poll`'s `fetch_updates` and `ask`'s `poll_decision`
  both consume the same server-side, bot-token-wide Telegram offset queue
  without coordination — if `poll` runs while an `ask_human()` call is
  mid-wait for a button tap, `poll`'s cursor advance can consume that pending
  callback before `poll_decision` ever sees it, timing the ask out
  (fail-closed `DENY`) even though the human tapped in time. Not fixed here;
  would require unifying both consumption paths under one poller.
- **Backlog label:** `my-telegram-bot`.
