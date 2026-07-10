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
  small command router. v0 registers exactly one command: `/idea <title>`
  files a `my-idea`-labeled issue and, in the same reply, explores it (one
  Engine call, entirely delegated to MyIdea's own `file_idea`/`explore`) so
  the human sees the full brief right in Telegram. Meant to be invoked
  frequently (e.g. every minute) by a Pi-side cron/systemd timer — polling
  itself is cheap; only an actual `/idea` message costs an Engine call.

Calls the Telegram Bot API over stdlib `urllib.request` + `json` only — no
`python-telegram-bot` SDK. `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are read
from the environment, never logged, never written to the ledger.

## Usage

```bash
mytelegrambot notify [--since ISO8601]
mytelegrambot ask --action-kind <kind> --payload-json <json> [--timeout 300]
mytelegrambot poll [--repo owner/name] [--engine claude-cli|noop]
```

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
