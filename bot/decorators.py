"""Decorator to extract target user from message."""

from functools import wraps
from inspect import Parameter, signature
from typing import Optional

from pyrogram.types import Message, User

from bot.database import Database


def target_user(can_be_self: bool = False):
    """Decorator to extract target user from message.

    Checks in order:
    1. Reply to message
    2. Text mention entity (@username)
    3. Mention entity (user mention)
    4. User ID in text

    If not found and can_be_self=True, returns the message author.
    If not found and can_be_self=False, returns None.

    Usage:
        @router.message(Command("tree"))
        @target_user(can_be_self=True)
        async def tree_command(message: Message, db: Database, target_user: User):
            # target_user is either the mentioned/replied user, or self
            ...
    """

    def decorator(func):
        func_signature = signature(func)
        accepts_var_kwargs = any(
            param.kind == Parameter.VAR_KEYWORD
            for param in func_signature.parameters.values()
        )

        @wraps(func)
        async def wrapper(message: Message, *args, **kwargs):
            db = kwargs.get("db")
            if db is None:
                for arg in args:
                    if isinstance(arg, Database):
                        db = arg
                        break

            if db is None:
                raise ValueError(
                    "target_user decorator requires Database dependency"
                )

            bot = kwargs.get("bot") or getattr(message, "_client", None)
            target = await _extract_target(message, db, bot)

            if target is None and can_be_self:
                target = message.from_user

            if target is None and not can_be_self:
                await message.reply(
                    "❌ No user found!\n"
                    "Reply to a user, mention @username, or provide user ID."
                )
                return

            if accepts_var_kwargs:
                filtered_kwargs = dict(kwargs)
            else:
                filtered_kwargs = {
                    key: value
                    for key, value in kwargs.items()
                    if key in func_signature.parameters
                }

            filtered_kwargs["target_user"] = target
            return await func(message, *args, **filtered_kwargs)

        return wrapper

    return decorator


async def _extract_target(
    message: Message, db: Database, bot=None
) -> Optional[User]:
    """Extract target user from message."""
    # 1. Check reply
    if message.reply_to_message and message.reply_to_message.from_user:
        if not message.reply_to_message.from_user.is_bot:
            return message.reply_to_message.from_user

    # 2. Check entities for mentions
    if bot and message.entities:
        for entity in message.entities:
            if entity.type == "mention" and message.text:
                username = message.text[
                    entity.offset : entity.offset + entity.length
                ].lstrip("@")
                try:
                    user_obj = await bot.get_users(f"@{username}")
                    if not user_obj.is_bot:
                        return user_obj
                except Exception:
                    pass
            elif entity.type == "text_mention" and entity.user:
                if not entity.user.is_bot:
                    return entity.user

    # 3. Check for user ID in text
    if bot and message.text:
        parts = message.text.split()
        for part in parts[1:]:  # Skip command
            if part.isdigit():
                try:
                    user_obj = await bot.get_users(int(part))
                    if not user_obj.is_bot:
                        return user_obj
                except Exception:
                    pass

    return None
