# Deploy: the poller service

`mytelegrambot run` is the fleet's one inbound channel and the **single owner**
of Telegram's bot-wide update queue. It long-polls `getUpdates`, routes text
commands (`/idea`, `/status`, `/help`) and records button taps as `kind=callback`
ledger entries that a waiting `mytelegrambot ask` process reads.

It is a long-running service, not a timer. That is the point: when `poll` was a
once-a-minute oneshot, it and `ask`'s own long-poll drained the same offset queue,
so a poll firing mid-`ask` could swallow the human's tap and time the approval out
to a fail-closed `DENY`. One consumer, one offset, no race.

## Install

```bash
sudo cp deploy/systemd/mytelegrambot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now mytelegrambot.service
```

Migrating from the old timer? Remove it first:

```bash
sudo systemctl disable --now mytelegrambot-poll.timer
sudo rm /etc/systemd/system/mytelegrambot-poll.service /etc/systemd/system/mytelegrambot-poll.timer
```

## Operate

```bash
systemctl status mytelegrambot.service       # is it alive?
journalctl -u mytelegrambot.service -f       # follow its output
sudo systemctl restart mytelegrambot.service # resumes from the ledger cursor
```

A crash-restart (`Restart=always`) resumes from the last committed `update_id`,
so no command is silently skipped.

## Credentials

`TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` come from
`/home/lollinuxpi/.config/mythingslab/telegram.env`, which is **not** in git.
Unlike the old timer, the `EnvironmentFile` is required (no leading `-`): a
daemon that starts without credentials would crash-loop, so it should fail
loudly at start instead of silently no-opping once a minute.

On failure systemd triggers `telegram-alert@mytelegrambot.service`, which DMs you.

## Admitting testers

The unit deliberately ships **without** `--testers-db`, so only the operator's
chat is heard. To let someone else in:

```bash
mytelegrambot testers add ada --chat-id 123456789 --quota 20   # prints a token once
sudo systemctl edit mytelegrambot.service   # append --testers-db .mythings/testers.db to ExecStart
sudo systemctl restart mytelegrambot.service
```

Each tester's `/idea` calls are capped by their quota and refused once it is
spent. `mytelegrambot testers disable <id>` revokes access immediately.
