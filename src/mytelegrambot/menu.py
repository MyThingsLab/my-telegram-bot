from __future__ import annotations

# One place defining what the bot advertises. COMMAND_MENU feeds Telegram's
# setMyCommands (autocomplete + the ☰ menu); REPLY_KEYBOARD is the persistent
# row of tappable shortcuts. Keeping /start out of both is deliberate: Telegram
# offers it automatically, and surfacing it again is just noise.
COMMAND_MENU: tuple[tuple[str, str], ...] = (
    ("idea", "File a my-idea issue and get an explored brief back"),
    ("status", "Show what the bot has done so far"),
    ("help", "Show what the bot can do"),
)

# Labels are literal "/commands" so a tap sends real command text through the
# ordinary parser -- no callback plumbing, no new attack surface.
REPLY_KEYBOARD: tuple[tuple[str, ...], ...] = (("/idea",), ("/status", "/help"))

SETUP_GREETING = (
    "MyThingsLab bot is ready. Tap a shortcut below or type /help to see everything I can do."
)
