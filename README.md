# my-telegram-bot

[![CI](https://github.com/MyThingsLab/my-telegram-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-telegram-bot/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-telegram-bot/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-telegram-bot) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Bridges the [MyThingsLab](../my-things-core) harness to the user over Telegram:
pushes ledger notifications, and turns a `Policy` `ASK` decision into a real
synchronous human confirmation instead of collapsing to `DENY` under an
unattended runner.

## How it works

`notify`/`ask` are deterministic, no Engine call — pure comms/plumbing that
only relays existing `Action`/`Ledger` data, never composes prose. `poll` is
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
- **Poll:** the fleet's one inbound channel — processes pending Telegram
  messages since the last poll (a ledger-tracked `update_id` cursor) through a
  small command router. `/idea <title>` files a `my-idea`-labeled issue and,
  in the same reply, explores it (one Engine call, entirely delegated to
  MyIdea's own `file_idea`/`explore`) so the human sees the full brief right in
  Telegram. `/status` reports what the bot has done so far, read straight from
  the ledger. `/help` (and `/start`, which Telegram auto-sends on first open)
  reply with a static command list. Everything except `/idea` is deterministic
  — no Engine call, no side effects. Meant to be invoked frequently (e.g. every
  minute) by a Pi-side cron/systemd timer — polling itself is cheap; only an
  actual `/idea` message costs an Engine call.
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
mytelegrambot poll [--repo owner/name] [--engine claude-cli|noop]
```

`poll` is run once a minute by a systemd timer on the Pi — see
[`deploy/systemd/`](deploy/systemd/) for the unit files and install steps.

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
