"""Command registry for automatic Telegram command registration.

Usage:
    Place `reg("cmd", "desc")` directly above each handler decorator.
    This keeps the registration next to the handler for easy editing.

Example:
    reg("shop", "🛒 Browse the shop")
    @on_command("shop")
    async def shop_command(message: Message, db: Database):
        ...
"""

from pyrogram.types import BotCommand

# Global command registry
_REGISTERED_COMMANDS: list[BotCommand] = []


def reg(command: str, description: str):
    """Register a command for Telegram autocomplete.

    Call this directly above a handler function (not as a decorator).
    """
    _REGISTERED_COMMANDS.append(
        BotCommand(command=command, description=description)
    )


def get_all_commands() -> list[BotCommand]:
    """Get all registered commands, deduplicated by command name."""
    seen: dict[str, BotCommand] = {}
    for cmd in _REGISTERED_COMMANDS:
        seen[cmd.command] = cmd
    return list(seen.values())
