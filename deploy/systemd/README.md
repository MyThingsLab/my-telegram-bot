# Deploy: `poll` timer

`mytelegrambot poll` is the fleet's one inbound channel — it drains pending
Telegram messages (`/idea`, `/help`, `/start`) and advances a ledger-tracked
`update_id` cursor. It's a short-lived oneshot, meant to be run on a schedule,
not as a daemon. These units run it once a minute on the Pi, following the same
system-unit pattern as `fleet-usage` / `fleet-cycle` (shared venv,
`telegram.env`, `OnFailure` alerting).

## Prerequisites

- `mytelegrambot` installed in the shared venv at
  `/home/lollinuxpi/repos/MyThingsLab/.venv` (`pip install -e .`).
- `/home/lollinuxpi/.config/mythingslab/telegram.env` holding
  `TELEGRAM_BOT_TOKEN=` and `TELEGRAM_CHAT_ID=` (mode `600`).
- `gh` authenticated for the user (the `/idea` path shells out to it).

## Install

```bash
sudo cp deploy/systemd/mytelegrambot-poll.service /etc/systemd/system/
sudo cp deploy/systemd/mytelegrambot-poll.timer   /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mytelegrambot-poll.timer
```

## Verify

```bash
systemctl status mytelegrambot-poll.timer      # next/last fire time
systemctl start  mytelegrambot-poll.service    # run one poll right now
journalctl -u mytelegrambot-poll.service -n 20 # see its output
```

A healthy run logs `success: N update(s), M routed` (or `skipped: 0 update(s)`
when the queue is empty). On failure the unit triggers
`telegram-alert@mytelegrambot-poll.service`, which DMs you the failure.

## Paths that are hard-coded

These units assume the standard Pi layout (`/home/lollinuxpi/repos/MyThingsLab`,
shared `.venv`). Edit `WorkingDirectory`, `Environment=PATH`, and the
`ExecStart`/`EnvironmentFile` paths if yours differ.
