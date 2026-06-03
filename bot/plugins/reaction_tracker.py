"""
Reaction tracker plugin: capture reaction updates and show /react_history.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from html import escape

from pyrogram import Client as Bot
from pyrogram.enums import ChatMemberStatus, MessageEntityType
from pyrogram.errors import BadRequest, Forbidden
from pyrogram.types import (
    LinkPreviewOptions,
    Message,
    MessageReactionUpdated,
    User,
)

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

from bot.config import Config
from pyrogram import filters
from bot.client import client

logger = logging.getLogger(__name__)


HISTORY_WINDOW_SECONDS = 48 * 60 * 60  # 2 days/ 48 hours
VISIBLE_CHAR_LIMIT = 3800
_BOT_STARTED_AT = datetime.now(UTC)


@dataclass
class ReactionEntry:
    unix_ts: int
    entry_html: str
    visible_char_count: int
    is_unreacted: bool
    is_premium: bool


# user_id -> chat_id -> entries
_reaction_history: dict[int, dict[int, list[ReactionEntry]]] = {}

_VISIBLE_EXCLUDE_RE = re.compile(r"</?a\b[^>]*>")


async def safe_reply(message: Message, text: str, **kwargs) -> None:
    await message.reply(text, **kwargs)


def _visible_len(text: str) -> int:
    return len(_VISIBLE_EXCLUDE_RE.sub("", text))


def _safe_user_mention(user_id: int, display_name: str | None = None) -> str:
    safe_name = escape((display_name or f"user {user_id}").strip())
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def _to_unix(dt: datetime | None) -> int:
    if dt is None:
        return int(datetime.now(UTC).timestamp())
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _format_utc(unix_ts: int) -> str:
    return datetime.fromtimestamp(unix_ts, UTC).strftime(
        "%Y-%m-%d %H:%M:%S UTC"
    )


def _message_link(chat_id: int, message_id: int) -> str:
    chat_text = str(chat_id)
    if chat_text.startswith("-100"):
        link_chat_id = chat_text[4:]
    else:
        link_chat_id = chat_text.lstrip("-")
    return f"https://t.me/c/{link_chat_id}/{message_id}"


def _reaction_key(reaction: object) -> tuple[str, str]:
    reaction_type = getattr(reaction, "type", "")
    if reaction_type == "emoji":
        return ("emoji", getattr(reaction, "emoji", ""))
    if reaction_type == "custom_emoji":
        return ("custom_emoji", getattr(reaction, "custom_emoji_id", ""))
    if reaction_type == "paid":
        return ("paid", "paid")
    return ("unknown", str(reaction))


def _reaction_html(reaction: object) -> tuple[str, bool]:
    reaction_type = getattr(reaction, "type", "")
    if reaction_type == "emoji":
        return escape(getattr(reaction, "emoji", "") or "❓"), False
    if reaction_type == "custom_emoji":
        emoji_id = escape(getattr(reaction, "custom_emoji_id", "") or "")
        return f'<tg-emoji emoji-id="{emoji_id}">🎲</tg-emoji> [🌟]', True
    if reaction_type == "paid":
        return "[PAID REACT]", False
    return "[UNKNOWN REACT]", False


def _prune_user_chat_history(user_id: int, chat_id: int) -> None:
    user_chats = _reaction_history.get(user_id)
    if not user_chats:
        return

    now_ts = int(datetime.now(UTC).timestamp())
    cutoff = now_ts - HISTORY_WINDOW_SECONDS

    entries = user_chats.get(chat_id, [])
    if entries:
        user_chats[chat_id] = [
            item for item in entries if item.unix_ts >= cutoff
        ]
    if not user_chats.get(chat_id):
        user_chats.pop(chat_id, None)
    if not user_chats:
        _reaction_history.pop(user_id, None)


def _append_reaction_entry(
    *,
    user_id: int,
    chat_id: int,
    unix_ts: int,
    action_label: str,
    reaction: object,
    message_url: str,
) -> None:
    reaction_display, is_premium = _reaction_html(reaction)
    time_display = _format_utc(unix_ts)
    entry_html = (
        f'<a href="{message_url}">{action_label}</a> '
        f"{reaction_display} at "
        f"{time_display}"
    )

    user_chats = _reaction_history.setdefault(user_id, {})
    user_chats.setdefault(chat_id, []).append(
        ReactionEntry(
            unix_ts=unix_ts,
            entry_html=entry_html,
            visible_char_count=_visible_len(entry_html),
            is_unreacted=action_label == "UNREACTED",
            is_premium=is_premium,
        )
    )
    _prune_user_chat_history(user_id, chat_id)


def _extract_diff(
    old_reaction: list[object], new_reaction: list[object]
) -> tuple[list[object], list[object]]:
    old_map = {_reaction_key(item): item for item in old_reaction}
    new_map = {_reaction_key(item): item for item in new_reaction}
    common_keys = set(old_map).intersection(new_map)

    removed = [old_map[k] for k in sorted(old_map) if k not in common_keys]
    added = [new_map[k] for k in sorted(new_map) if k not in common_keys]
    return removed, added


def _build_history_chunks(
    target_mention_html: str, entries: list[ReactionEntry]
) -> list[str]:
    total = len(entries)
    removed = sum(1 for item in entries if item.is_unreacted)
    has_premium = any(item.is_premium for item in entries)

    startup_ts = int(_BOT_STARTED_AT.timestamp())
    startup_display = _format_utc(startup_ts)
    startup_line = (
        "NOTE : Bot last restarted at "
        f"{startup_display} "
        "so reaction older than that are not recorded :("
    )

    header_lines = [
        f"{target_mention_html} did {total} reactions update(react unreact) in past 48 hours.",
        "",
        "Note : they removed reactions "
        f"{removed} times, which u cant see as reacted",
    ]
    if has_premium:
        header_lines.append(
            "Note : entries with [🌟] are premium react. If you see 🎲 fallback, "
            "bot cannot render premium emoji due to account or Telegram display limits."
        )
    header_lines.extend(["", startup_line, "", "History :"])

    first_prefix = "\n".join(header_lines) + "\n<blockquote expandable>\n"
    next_prefix = "<blockquote expandable>\n"
    suffix = "\n\n</blockquote>"

    chunks: list[str] = []
    current = first_prefix
    current_visible = _visible_len(first_prefix)
    has_items = False

    sorted_entries = sorted(
        entries, key=lambda item: item.unix_ts, reverse=True
    )
    for idx, item in enumerate(sorted_entries, start=1):
        line = f"{idx}. {item.entry_html}\n"
        line_visible = len(f"{idx}. ") + item.visible_char_count + 1

        if (
            has_items
            and current_visible + line_visible + _visible_len(suffix)
            > VISIBLE_CHAR_LIMIT
        ):
            current += suffix
            chunks.append(current)
            current = next_prefix
            current_visible = _visible_len(next_prefix)
            has_items = False

        current += line
        current_visible += line_visible
        has_items = True

    current += suffix
    chunks.append(current)
    return chunks


async def _mention_for_user(bot: Bot, chat_id: int, user_id: int) -> str:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return _safe_user_mention(
            user_id, getattr(member.user, "full_name", None)
        )
    except (BadRequest, Forbidden):
        return _safe_user_mention(user_id)


async def _is_chat_admin(bot: Bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
    except (BadRequest, Forbidden):
        return False
    return member.status in (
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.CREATOR,
    )


async def _resolve_target_user(
    message: Message, bot: Bot
) -> tuple[int | None, str | None]:
    if message.reply_to_message and message.reply_to_message.from_user:
        reply_user = message.reply_to_message.from_user
        return reply_user.id, _safe_user_mention(
            reply_user.id, getattr(reply_user, "full_name", None)
        )

    text = message.text or ""
    parts = text.split(maxsplit=1)
    args = parts[1].strip() if len(parts) > 1 else ""
    first_arg = args.split(maxsplit=1)[0] if args else ""
    if first_arg.isdigit():
        target_id = int(first_arg)
        return target_id, await _mention_for_user(
            bot, message.chat.id, target_id
        )

    for entity in message.entities or []:
        if entity.type == MessageEntityType.TEXT_MENTION and entity.user:
            user: User = entity.user
            return user.id, _safe_user_mention(
                user.id, getattr(user, "full_name", None)
            )

    return None, None


@client.on_message_reaction()
async def reaction_update_handler(message_reaction: MessageReactionUpdated):
    if not message_reaction.user:
        return

    user_id = message_reaction.user.id
    chat_id = message_reaction.chat.id
    unix_ts = _to_unix(message_reaction.date)
    msg_url = _message_link(chat_id, message_reaction.message_id)

    old_reaction = list(message_reaction.old_reaction or [])
    new_reaction = list(message_reaction.new_reaction or [])
    removed, added = _extract_diff(old_reaction, new_reaction)

    for reaction in removed:
        _append_reaction_entry(
            user_id=user_id,
            chat_id=chat_id,
            unix_ts=unix_ts,
            action_label="UNREACTED",
            reaction=reaction,
            message_url=msg_url,
        )
    for reaction in added:
        _append_reaction_entry(
            user_id=user_id,
            chat_id=chat_id,
            unix_ts=unix_ts,
            action_label="Reacted",
            reaction=reaction,
            message_url=msg_url,
        )


@client.on_message(
    filters.command(["react_history"], prefixes="/.", case_sensitive=False)
)
async def react_history_command(message: Message, bot: Bot, config: Config):
    if not message.from_user:
        return

    invoker_id = message.from_user.id
    if invoker_id != config.owner_id and not await _is_chat_admin(
        bot, message.chat.id, invoker_id
    ):
        await safe_reply(message, "Only owner/admin can use /react_history.")
        return

    target_id, target_mention = await _resolve_target_user(message, bot)
    if target_id is None or target_mention is None:
        await safe_reply(
            message,
            "Reply to a user, or pass a user id, or mention a user(not by username): /react_history &lt;user&gt;.",
        )
        return

    _prune_user_chat_history(target_id, message.chat.id)
    entries = _reaction_history.get(target_id, {}).get(message.chat.id, [])
    if not entries:
        await safe_reply(
            message,
            f"No reaction history found for {target_mention} in past 48 hours.\n\nNOTE: make sure i am admin in chat to receive reaction changes from telegram, no need of special permission for this.",
        )
        return

    chunks = _build_history_chunks(target_mention, entries)
    for idx, chunk in enumerate(chunks):
        if idx == 0:
            await safe_reply(message, chunk, link_preview_options=_NO_PREVIEW)
        else:
            await message.reply(chunk, link_preview_options=_NO_PREVIEW)
