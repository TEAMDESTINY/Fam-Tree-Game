"""Bot entry point - initializes Kurigram Client and handlers."""

import asyncio
import logging
import os
import sys
from typing import Any

import uvloop
from pyrogram import idle
from pyrogram.types import BotCommand

from bot.client import client
from bot.command_registry import get_all_commands
from bot.config import get_config
from bot.database import Database, db
from bot.runtime import runtime_app as runtime_app
from bot.watchdog import update_watchdog

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s - %(name)s - %(filename)s:%(lineno)d %(levelname)s => %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

UPDATE_MAX_AGE_SECONDS = 5 * 60

BASE_COMMANDS = [
    BotCommand(command="start", description="🚀 Start the bot"),
    BotCommand(command="help", description="❓ Show help message"),
    BotCommand(command="me", description="👤 View your profile"),
    BotCommand(command="feedback", description="📝 Send feedback to admins"),
    BotCommand(
        command="guide_bot_slow",
        description="🚦 Why group bot replies can be slow",
    ),
]


def _setup_logging(log_level: str) -> None:
    root_level = getattr(logging, log_level, logging.INFO)
    root_logger = logging.getLogger()
    root_logger.setLevel(root_level)
    for handler in root_logger.handlers:
        handler.setLevel(root_level)

    kurigram_logger = logging.getLogger("pyrogram")
    kurigram_logger.setLevel(logging.WARNING)


async def _handle_restart_notice(client) -> None:
    """Reply to the /auto_restart command with a success message if /tmp/restart exists."""
    restart_file = "/tmp/restart"
    if not os.path.exists(restart_file):
        return
    try:
        with open(restart_file) as f:
            raw = f.read().strip()
        chat_id_str, msg_id_str = raw.split(":", 1)
        chat_id = int(chat_id_str)
        msg_id = int(msg_id_str)
        await client.send_message(
            chat_id,
            "✅ Bot restarted successfully!",
            reply_to_message_id=msg_id,
        )
    except Exception as e:
        logger.warning("Could not send restart notice: %s", e)
    finally:
        try:
            os.remove(restart_file)
        except Exception:
            pass


async def main():
    """Main entry point."""
    config = get_config()
    _setup_logging(config.log_level)

    logger.info("Connecting to database...")
    try:
        database_client = await Database.create(config.database_url)
    except Exception as e:
        logger.error("Failed to connect to database: %s", e)
        return

    logger.info("Database connected and initialized")
    client.setup(config=config)
    db.set(database_client)
    runtime_app.bind(
        client,
        db=database_client,
        config=config,
        update_max_age_seconds=UPDATE_MAX_AGE_SECONDS,
    )

    # Import plugins (registration happens at import time via decorators)
    from bot.plugins import (  # noqa: F401
        admin,
        animals,
        callbacks,
        combat,
        craft,
        daily,
        eval_cmd,
        factory,
        family,
        feedback,
        fishing,
        four_pic,
        friends,
        gambling,
        gangs,
        garden,
        jobs,
        leaderboard,
        nation,
        pet,
        profile,
        reaction_tracker,
        shop,
        sonar,
        start,
    )

    all_commands = BASE_COMMANDS + get_all_commands()
    seen: dict[str, BotCommand] = {}
    for cmd in all_commands:
        seen[cmd.command] = cmd
    final_commands = list(seen.values())[:100]

    logger.info("Starting bot...")
    watchdog_task: asyncio.Task[Any] | None = None
    try:
        await client.start()
        await _handle_restart_notice(client)
        await client.set_bot_commands(final_commands)
        logger.info("Registered %s bot commands", len(final_commands))
        watchdog_task = asyncio.create_task(
            update_watchdog(client), name="session-watchdog"
        )
        await idle()
    except Exception:
        logger.exception("Bot runtime crashed.")
    finally:
        if watchdog_task is not None:
            watchdog_task.cancel()
            try:
                await watchdog_task
            except (asyncio.CancelledError, Exception):
                pass
        try:
            await client.stop()
        except Exception:
            logger.exception("Failed to stop Kurigram client cleanly.")
        await database_client.close()
        logger.info("[BOT_STOPPED] Bot stopped")


if __name__ == "__main__":
    uvloop.install()
    asyncio.run(main())
