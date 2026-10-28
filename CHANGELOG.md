# Changelog

## [Unreleased]
### Shipped
- pushed to github.com/MyThingsLab/my-telegram-bot; PR #1 opened, CI green on first push
- live end-to-end ask-flow verified: real Telegram message sent, user tapped Allow, TelegramPolicy.evaluate() resolved decision=allow in ~3.5s via manual-ask-test.yml workflow_dispatch run 28840280628. Confirms the getUpdates client-timeout fix (PR #5) actually closed the loop, not just passed unit tests
### Added/Changed
- implemented TelegramTransport (Protocol) + HTTPTelegramTransport (stdlib urllib.request + json, sendMessage/getUpdates long-poll, no SDK), TelegramPolicy + ask_human (fail-closed ASK relay), notify (incremental ledger push); 10 tests green, ruff clean
### Fixed
- the live ask-flow test resolved deny/timeout in 22s (not a real 300s timeout), meaning ask_human's blanket except swallowed a real transport error silently. Added a describe() helper (reads HTTPError body for the actual Telegram API error message) and print it before failing closed in both ask_human and HTTPTelegramTransport.poll_decision -- observability only, the fail-closed decision itself is unchanged
- root cause of the live test's fast failures: _call() hardcoded urlopen(timeout=10) for every method, but poll_decision asks Telegram to long-poll getUpdates for up to 30s server-side -- the client gave up at 10s before Telegram could ever respond, raising TimeoutError. User confirmed they saw the real message and tapped Allow, but the reply was never read back. Fixed by passing timeout=long_poll+10 for the getUpdates call specifically (sendMessage keeps the default 10s, it's not long-polling)
