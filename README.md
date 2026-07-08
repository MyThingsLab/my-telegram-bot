# my-telegram-bot

[![CI](https://github.com/MyThingsLab/my-telegram-bot/actions/workflows/ci.yml/badge.svg)](https://github.com/MyThingsLab/my-telegram-bot/actions/workflows/ci.yml) [![codecov](https://codecov.io/gh/MyThingsLab/my-telegram-bot/branch/main/graph/badge.svg)](https://codecov.io/gh/MyThingsLab/my-telegram-bot) ![Python](https://img.shields.io/badge/python-3.11%2B-blue) [![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

Bridges the [MyThingsLab](../my-things-core) harness to the user over Telegram:
pushes ledger notifications, and turns a `Policy` `ASK` decision into a real
synchronous human confirmation instead of collapsing to `DENY` under an
unattended runner.

## How it works

Deterministic, no Engine call — this is a comms/plumbing tool, it only relays
existing `Action`/`Ledger` data, never composes prose.

- **Notify:** reads the shared `Ledger`'s entries since this tool's own last
  `kind=notify` write (incremental window, same pattern as MyReporter/
  MyChangelogger) and pushes them as one Telegram message.
- **Ask:** `TelegramPolicy` wraps any inner `Policy` (typically MyGuard's
  `Guard`). Non-`ASK` decisions pass through untouched. An `ASK` sends a real
  Allow/Deny prompt over Telegram and blocks (bounded by `timeout`) for a
  reply — resolving to the human's answer. **Fail-closed is non-negotiable:**
  on timeout, no reply, or any Telegram API error, it resolves `DENY`, never
  `ALLOW`.

Calls the Telegram Bot API over stdlib `urllib.request` + `json` only — no
`python-telegram-bot` SDK. `TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID` are read
from the environment, never logged, never written to the ledger.

## Usage

```bash
mytelegrambot notify [--since ISO8601]
mytelegrambot ask --action-kind <kind> --payload-json <json> [--timeout 300]
```

Primarily consumed as a library (`TelegramPolicy` wrapping another `Policy`)
inside another tool's runtime — the CLI above is for manual/CI-script use.

## Install (development)

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ../my-things-core -e ".[dev]"
pytest
```

## License

MIT — see [`LICENSE`](LICENSE).
