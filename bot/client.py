from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable
from typing import Any

import pyrogram
from pyrogram import Client
from pyrogram.enums import ParseMode

from bot.config import get_config
from bot.watchdog import touch as _watchdog_touch

logger = logging.getLogger(__name__)


class BotClient(Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._config: Any = None

    def setup(self, *, config: Any) -> None:
        self._config = config

    def _wrap(self, func):
        async def wrapped(client: BotClient, event: Any):
            _watchdog_touch()
            sig = inspect.signature(func)
            params = sig.parameters
            has_var_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values()
            )
            kwargs: dict[str, Any] = {}
            if has_var_kwargs or "bot" in params:
                kwargs["bot"] = client
            if has_var_kwargs or "client" in params:
                kwargs["client"] = client
            if has_var_kwargs or "config" in params:
                kwargs["config"] = self._config
            try:
                result = func(event, **kwargs)
                if isinstance(result, Awaitable):
                    await result
            except (pyrogram.ContinuePropagation, pyrogram.StopPropagation):
                raise
            except Exception:
                logger.exception(
                    "Unhandled exception in handler %s", func.__qualname__
                )

        return wrapped

    def on_message(self, filters=None, group: int = 0):
        def decorator(func):
            return super(BotClient, self).on_message(filters, group=group)(
                self._wrap(func)
            )

        return decorator

    def on_callback_query(self, filters=None, group: int = 0):
        def decorator(func):
            return super(BotClient, self).on_callback_query(
                filters, group=group
            )(self._wrap(func))

        return decorator

    def on_message_reaction(self, filters=None, group: int = 0):
        def decorator(func):
            return super(BotClient, self).on_message_reaction(
                filters, group=group
            )(self._wrap(func))

        return decorator


config = get_config()

client = BotClient(
    name="famtree_bot",
    api_id=config.api_id,
    api_hash=config.api_hash,
    bot_token=config.bot_token,
    parse_mode=ParseMode.HTML,
    fetch_replies=True,
)
