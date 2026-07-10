# my-telegram-bot — agent instructions

You are developing **my-telegram-bot**, a MyThingsLab My[X] tool.

**Inherited rules:** obey [`./HARNESS.md`](./HARNESS.md) in full — the vendored
MyThingsLab build-harness rules. Do not restate or override them. Anything not
covered here defers to `HARNESS.md`, then `my-things-core/docs/CONVENTIONS.md`.

## This tool

- **Purpose:** bridges the harness to the user over Telegram: pushes ledger
  notifications, turns a `Policy` `ASK` decision into a real synchronous
  human confirmation instead of collapsing to `DENY` under an unattended
  runner, and — the one inbound direction — lets the operator, plus any tester
  they have explicitly registered, capture a `my-idea` from chat via
  `/idea <text>` so no idea gets missed for lack of a terminal nearby.
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
  composing prose that could hallucinate over what it's relaying. The `/idea`,
  `/note` and `/wish` paths handled by `run` each make exactly one Engine call,
  but this tool never calls the Engine itself: those calls are entirely
  delegated to the owning tool's own already-shipped, already-tested code —
  MyIdea's `myidea.explore.explore()`, MyNotes' `mynotes.tag.tag()` and
  MyGuide's `Guide.wish()` — imported as libraries. Filing (`file_idea`,
  `file_note`) is deterministic and spends nothing; a capture verb composes
  filing + the one Engine call. The other `run` commands stay deterministic, no
  Engine, no side effects: `/catalog` and the `Try <tool>` trial button render
  human-curated cards; `/help` and `/start` echo a fixed command list
  (`help_command.py`);
  `/status` renders counts read straight from the ledger (`status_command.py`).
  The `setup` CLI subcommand is a one-off admin call (no ledger, no Engine)
  that registers the `setMyCommands` menu and the persistent reply keyboard
  (`menu.py`); the keyboard's labels are literal `/commands` so a tap is just
  ordinary command text, routed by the ordinary parser.
- **Dependency-direction exception, and the rule behind it:** every other
  cross-tool relationship in the fleet is a CLI hand-off, not a package
  dependency (e.g. MyPresentation → MyTypster), to keep tool repos decoupled at
  the code level. This tool imports `my-idea`, `my-notes` and `my-guide` (and,
  transitively, `my-guard`) directly as Python packages instead. The rule has
  been restated twice as it met reality — first as "a one-off for `/idea`", then
  as "a capture verb imports its owning tool" when `/note` arrived. `/wish` is a
  *query*, not a capture, and needed the same import, so the honest rule is:

  > **This tool imports any tool whose structured result it must render in one
  > synchronous reply.**

  `Issue`, `ExploreResult`, `TagResult`, `Wish`, `Message` — a subprocess
  hand-off would force parsing stdout or re-reading the issue to recover them,
  exactly the fragility this exception exists to avoid. Do not "fix" these back
  to subprocess calls.

  **Be honest about the cost:** that now covers *every* cross-tool call this
  repo makes, so the fleet's CLI-hand-off rule constrains nothing here. What
  still holds is the direction — those tools never import this one — and the
  discipline that this repo composes their results and never reimplements them.
  A tool whose result this bot does *not* render in a reply (a fire-and-forget
  hand-off) still has no business being a package dependency.
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
- **Buttons are client-supplied input, not trusted state.** `/idea` replies carry
  an inline keyboard (`Explore deeper` / `Close idea`) whose `callback_data`
  encodes `idea:<n>:<verb>` (`router.encode_action`, bounded to Telegram's 64
  bytes). Telegram will deliver *whatever* `callback_data` a client sends for a
  message it can see, so **the subject is authorized, not just the actor**: a
  non-operator may only act on an idea their own ledger records them filing
  (`_may_act_on`). Both verbs are GitHub writes; `Explore deeper` is metered like
  `/idea` (a button must never be a way around the quota) and `Close idea` passes
  the same `Policy` seam `file_idea` does, failing closed on `ASK` because the
  daemon is unattended. Every tap is answered (`answerCallbackQuery`) even when
  unrouted or failed, or the button spins forever; that answer is cosmetic and
  must never undo an action that already happened.
- **One consumer owns the update queue.** `mytelegrambot run` is a long-lived
  daemon (systemd `Type=simple`, `Restart=always`) and its `fetch_updates` is
  the *only* `getUpdates` caller in the tool. `poll_decision` is gone: `ask`
  used to long-poll the same bot-token-wide offset queue, so a concurrent
  `poll` could consume the human's button tap and time the ask out to a
  fail-closed `DENY`. Now the daemon classifies each update — text → the
  command router; `callback_query` → a `kind=callback` ledger entry keyed by
  `message_id` — and `ask_human` (a *separate short-lived process*, since
  other tools shell out to `mytelegrambot ask`) waits on the ledger for the
  entry carrying its own `message_id`. The ledger is the rendezvous: no
  socket, no new dependency, and the race can no longer be expressed. **Do
  not reintroduce a second `getUpdates` caller.** `allow`/`deny` are the only
  `callback_data` values the daemon never routes to a handler — `policy`
  exports `ASK_DECISIONS` so the button and the router cannot disagree about
  what an approval looks like. Handlers return a `router.Reply` (text plus an
  optional inline keyboard) and never touch the transport themselves.
- **Authorization is not the transport's job.** `fetch_updates` returns every
  update Telegram sends; `authz.ChatAuthorizer` decides who may be heard.
  The operator (`TELEGRAM_CHAT_ID`) is authorized *by configuration, never by
  the database*, so a corrupt or absent testers db can never lock the owner
  out. Everyone else must be a registered, enabled row in a
  `mythings.testers.TesterStore`, and only when `run --testers-db` explicitly
  points at one — absent that flag the tool is exactly as single-user as it
  was. An unauthorized chat is dropped **silently**: no reply, no ledger
  entry, so it learns nothing, not even that the bot is listening. A
  `callback_query` from a non-operator chat is dropped too — an `ask` prompt
  only ever goes to the operator, so a tester must never be able to resolve
  one.
- **A knock is recorded, but only locally.** "Silent" is about what *they*
  observe: no reply, no `answerCallbackQuery`. It never meant the operator
  must stay blind. Without a record, a prospective tester's first message is
  unrecoverable — the cursor advances past it and Telegram never redelivers —
  so `pending.PendingChats` writes one `kind=unknown_chat` entry per
  unrecognized chat and `mytelegrambot testers pending` lists them. Two bounds
  keep it from being a spam sink: **deduplicated by `chat_id`** (rebuilt from
  the ledger on restart, so a crash-loop can't re-record), and **capped at
  `MAX_PENDING` distinct chats**, past which knocks are dropped exactly as
  before. Recorded regardless of `--testers-db`: you must see who knocked
  before you have anyone to put in a database.
- **Tester spend is capped, fail-closed.** `/idea`, `/note`, `/wish` and the
  `Explore deeper` button are the Engine-spending paths, so they are the metered
  ones (`metered_idea`, `metered_note`, `metered_wish`, `explore_idea`).
  `/catalog` and the `Try <tool>` trial button are deterministic — a newcomer
  reads the whole fleet and dry-runs any tool for free. A
  tester's reservation is taken from their quota *before* the call and
  refunded only if the call never happened (an exception) — a crash therefore
  over-counts against the tester rather than letting an unbilled call through.
  Refusal is the default. The operator is never metered. Accepted: a
  policy-denied filing still consumes one reservation; it errs in the safe
  direction. A tester's activity is written to their own `ledger_for()` file,
  so it never pollutes the operator's digest, `/status`, or the notify cursor.
- **Cursor invariants:** the inbound cursor is a `kind=poll` ledger entry
  carrying the last-seen `update_id` (mirrors `notify`'s count-based cursor,
  just keyed by Telegram's id instead of a length), committed once per
  non-empty batch; a crash-restart resumes from it. An idle long-poll writes
  **nothing** — the old once-a-minute oneshot recorded a `skipped` entry as
  proof it ran, which in a 30s loop would be pure ledger spam. A reply-send
  failure is swallowed so the cursor still advances (the command's side
  effects, like a filed GitHub issue, already happened and must not be
  repeated). **Known, accepted limitation:** a crash between a command's side
  effects completing and the batch's `kind=poll` write reprocesses that update;
  GitHub issue creation isn't idempotent, so that exact window could file a
  duplicate idea.
- **Backlog label:** `my-telegram-bot`.
