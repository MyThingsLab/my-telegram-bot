# my-telegram-bot

[![CI](https://github.com/MyThingsLab/my-telegram-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-telegram-bot/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-telegram-bot/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-telegram-bot) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Bridges the [MyThingsLab](../my-things-core) harness to the user over Telegram:
pushes ledger notifications, and turns a `Policy` `ASK` decision into a real
synchronous human confirmation instead of collapsing to `DENY` under an
unattended runner.

## How it works

`notify`/`ask` are deterministic, no Engine call — pure comms/plumbing that
only relays existing `Action`/`Ledger` data, never composes prose. `/idea` is
the one exception (see below).

- **Notify:** reads the shared `Ledger`'s entries since this tool's own last
  `kind=notify` write (incremental window, same pattern as MyReporter/
  MyChangelogger) and pushes them as one Telegram message.
- **Ask:** `TelegramPolicy` wraps any inner `Policy` (typically MyGuard's
  `Guard`). Non-`ASK` decisions pass through untouched. An `ASK` sends a real
  Allow/Deny prompt over Telegram and blocks (bounded by `timeout`) for a
  reply — resolving to the human's answer. **Fail-closed is non-negotiable:**
  on timeout, no reply, or any Telegram API error, it resolves `DENY`, never
  `ALLOW`.
- **Run:** the fleet's one inbound channel, and the single owner of Telegram's
  update queue. `mytelegrambot run` is a long-lived daemon that long-polls
  `getUpdates`, resumes from a ledger-tracked `update_id` cursor, and routes
  each update: text through a small command router, `callback_query` into a
  `kind=callback` ledger entry that a waiting `ask` process picks up. Because
  it is the only `getUpdates` caller, a concurrent `ask` and an inbound command
  can no longer steal each other's updates.

  `/idea <title>` files a `my-idea`-labeled issue and, in the same reply,
  explores it (one Engine call, entirely delegated to MyIdea's own
  `file_idea`/`explore`) so you see the full brief right in Telegram.
  `/status` reports what the bot has done so far, read straight from the
  ledger. `/help` (and `/start`, which Telegram auto-sends on first open) reply
  with a static command list. Everything except `/idea` is deterministic — no
  Engine call, no side effects.
- **Buttons:** every `/idea` reply carries **Explore deeper** and **Close idea**
  buttons, so the thread stays actionable without typing another command.
  `Explore deeper` is metered exactly like `/idea` — a button is not a way around
  a tester's quota — and `Close idea` passes the same `Policy` gate every GitHub
  write in the fleet does. Because Telegram delivers whatever `callback_data` a
  client sends, the *subject* is authorized too: a tester may only act on an idea
  they filed.
- **Testers:** by default only the operator's chat (`TELEGRAM_CHAT_ID`) is
  heard; every other chat is dropped silently. Point `run --testers-db` at a
  `mythings.testers` database to admit registered testers. Each tester gets a
  hard, fail-closed quota of Engine calls (`/idea` reserves before it spends,
  and the refusal is the default), replies go back to their own chat, and their
  activity lands in their own ledger — never the operator's digest. Revoking
  access is one flag: `mytelegrambot testers disable <id>`.
- **Setup:** `mytelegrambot setup` is a one-off admin call that registers the
  command menu (Telegram autocomplete + the ☰ menu button) and shows a
  persistent reply keyboard of tappable `/command` shortcuts. Taps arrive as
  ordinary text, so they route through the normal parser — no callback
  plumbing.

Calls the Telegram Bot API over stdlib `urllib.request` + `json` only — no
`python-telegram-bot` SDK. `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are read
from the environment, never logged, never written to the ledger.

## Usage

```bash
mytelegrambot setup
mytelegrambot notify [--since ISO8601]
mytelegrambot ask --action-kind <kind> --payload-json <json> [--timeout 300]
mytelegrambot run [--repo owner/name] [--engine claude-cli|noop] [--testers-db PATH]
mytelegrambot testers add <handle> --chat-id <id> --quota <n>
mytelegrambot testers disable <id>
```

`run` is a long-lived systemd service on the Pi — see
[`deploy/systemd/`](deploy/systemd/) for the unit file and install steps.
`testers add` prints the tester's token exactly once; only its sha256 is stored.

Primarily consumed as a library (`TelegramPolicy` wrapping another `Policy`)
inside another tool's runtime — the CLI above is for manual/CI-script use.

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../my-things-core -e ../my-guard -e ../my-idea -e ".[dev]"
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
