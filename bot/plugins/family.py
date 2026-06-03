"""Family relationship commands - adopt, marry, disown, divorce, relations, tree."""

import logging
from typing import NamedTuple, Optional

logger = logging.getLogger(__name__)

from pyrogram import Client as Bot
from pyrogram import filters
from pyrogram.errors import BadRequest
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from bot.command_registry import reg
from bot.constants import (
    COFFIN_VIDEO_FILE_ID,
    FUNERAL_DEFAULT_AMOUNT,
    FUNERAL_EMOJI_IDS,
)
from bot.database import Database
from bot.input_file import to_input_file
from bot.queue_it import queue_it
from bot.client import client
from bot.utils import format_family_error_message, parse_money_amount
from bot.utils import user_mention as util_user_mention
from bot.database import db
from pyrogram.types import CallbackQuery


class TargetUser(NamedTuple):
    """Result of get_target_user. `is_bot` is True iff the user replied to a bot."""

    user: Optional[User]
    is_bot: bool = False


async def reply_cannot_target_bot(message: Message):
    """Generic reply for commands that can't act on a bot target."""
    await message.reply("❌ You can't target a bot! Pick a real user.")


async def get_target_user(
    bot: Bot, message: Message, db: Database = None
) -> TargetUser:
    """
    Extract target user from message (reply, mention, or user ID).

    Returns a TargetUser. If the user replied to a bot, `is_bot=True` and
    `user=None`. Otherwise `is_bot=False` and `user` is the resolved User or
    None if no target was specified.
    """
    # Check if replying to someone
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        if target.is_bot:
            return TargetUser(user=None, is_bot=True)
        return TargetUser(user=target)

    # Check for mentions in entities
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention" and message.text:
                username = message.text[
                    entity.offset : entity.offset + entity.length
                ].lstrip("@")
                target = await _resolve_user(bot, db, username)
                if target:
                    return TargetUser(user=target)
            elif entity.type == "text_mention" and entity.user:
                if not entity.user.is_bot:
                    return TargetUser(user=entity.user)

    # Check for username or user_id in command arguments
    if message.text:
        parts = message.text.split()
        if len(parts) > 1:
            arg = parts[1].lstrip("@")
            target = await _resolve_user(bot, db, arg)
            if target:
                return TargetUser(user=target)

    return TargetUser(user=None)


async def _resolve_user(
    bot: Bot, db: "Database", identifier: str
) -> Optional[User]:
    """Resolve a user by username or user ID, trying bot first then database."""
    # Try as user ID first
    if identifier.isdigit():
        try:
            user_obj = await bot.get_users(int(identifier))
            if not user_obj.is_bot:
                await db.upsert_user(
                    user_id=user_obj.id,
                    username=user_obj.username,
                    first_name=user_obj.first_name,
                )
                return user_obj
        except Exception:
            pass
        # Fallback to database
        user = await db.fetchrow(
            "SELECT user_id, username, first_name FROM users WHERE user_id = $1",
            int(identifier),
        )
        if user:
            return User(
                id=user["user_id"],
                is_bot=False,
                first_name=user["first_name"],
                username=user["username"],
            )
    else:
        # Try as username via bot
        try:
            user_obj = await bot.get_users(f"@{identifier}")
            if not user_obj.is_bot:
                await db.upsert_user(
                    user_id=user_obj.id,
                    username=user_obj.username,
                    first_name=user_obj.first_name,
                )
                return user_obj
        except Exception:
            pass
        # Fallback to database
        user = await db.fetchrow(
            "SELECT user_id, username, first_name FROM users WHERE username = $1",
            identifier,
        )
        if user:
            return User(
                id=user["user_id"],
                is_bot=False,
                first_name=user["first_name"],
                username=user["username"],
            )

    return None


async def ensure_both_users(db: Database, message: Message, target: User):
    """Ensure both users exist in database."""
    user = message.from_user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )
    await db.upsert_user(
        user_id=target.id,
        username=target.username,
        first_name=target.first_name,
    )


def user_display_name(user) -> str:
    """Get display name for a user record or User object."""
    if hasattr(user, "first_name"):
        name = user.first_name
    else:
        name = user.get("first_name") or "Unknown"
    return name


def _safe_button_label(name, prefix: str = "", max_len: int = 10) -> str:
    """Build a non-empty inline-keyboard button label.

    Strips newlines, tabs, and zero-width characters that have caused
    Telegram to reject the markup with REPLY_MARKUP_INVALID. Falls back
    to "?" when the cleaned name is empty so the button is never empty.
    """
    s = "" if name is None else str(name)
    s = s.replace("\n", " ").replace("\r", " ").replace("\t", " ")
    for zw in ("​", "‌", "‍", "⁠", "﻿"):
        s = s.replace(zw, "")
    s = s.strip()
    if not s:
        s = "?"
    return f"{prefix}{s[:max_len]}" if prefix else s[:max_len]


async def _with_family_conflict_details(
    db: Database,
    user1_id: int,
    user2_id: int,
    action: str,
    base_message: str,
) -> str:
    """Append detailed family connection chain when users are connected."""
    if not await db.are_close_family(user1_id, user2_id):
        return base_message
    details = await format_family_error_message(db, user1_id, user2_id, action)
    return f"{base_message}\n\n{details}"


_CAPTION_LIMIT = 1024


async def _send_conflict_message(
    message,
    bot,
    db: Database,
    user1_id: int,
    user2_id: int,
    error_msg: str,
):
    """Send conflict error as photo+caption when connected, plain text otherwise."""
    if not await db.are_close_family(user1_id, user2_id):
        await message.reply(error_msg)
        return

    path = await db.get_family_path(user1_id, user2_id)
    if not path or len(path) < 2:
        await message.reply(error_msg)
        return

    try:
        from bot.graphics.tree_renderer import render_conflict_path

        image_bytes = await render_conflict_path(bot, db, path)
        if image_bytes:
            if len(error_msg) <= _CAPTION_LIMIT:
                await message.reply_photo(
                    to_input_file(image_bytes, "conflict_path.png"),
                    caption=error_msg,
                )
            else:
                # Message too long for a caption — send text first, photo second
                await message.reply(error_msg)
                await message.reply_photo(
                    to_input_file(image_bytes, "conflict_path.png"),
                )
            return
    except Exception:
        pass

    await message.reply(error_msg)


async def build_sibling_conflict_message(
    db: Database, user_id: int, target_id: int
) -> str:
    """Build clear sibling-rule error with generation info."""
    user = await db.get_user(user_id)
    target = await db.get_user(target_id)
    user_name = user.get("first_name") or "You"
    target_name = target.get("first_name") or "Unknown"

    if await db.is_spouse_of(user_id, target_id):
        return f"⚠️ You can't be siblings with {target_name} because you're married."

    if await db.is_ancestor_of(user_id, target_id):
        return f"⚠️ You can't be siblings with {target_name} because they're your descendant."

    if await db.is_ancestor_of(target_id, user_id):
        return f"⚠️ You can't be siblings with {target_name} because they're your ancestor."

    # In-law conflict — one's spouse is the other's sibling, or vice versa.
    for primary_id, other_id in ((user_id, target_id), (target_id, user_id)):
        primary_spouses = await db.get_spouses(primary_id)
        for spouse in primary_spouses:
            spouse_siblings = await db.get_siblings(spouse["user_id"])
            if any(s["user_id"] == other_id for s in spouse_siblings):
                return (
                    f"⚠️ You can't be siblings with {target_name} because "
                    f"they're already a sibling-in-law."
                )
        primary_siblings = await db.get_siblings(primary_id)
        for sibling in primary_siblings:
            sibling_spouses = await db.get_spouses(sibling["user_id"])
            if any(s["user_id"] == other_id for s in sibling_spouses):
                return (
                    f"⚠️ You can't be siblings with {target_name} because "
                    f"they're married to one of your siblings."
                )

    return f"⚠️ You can't be siblings with {target_name} — different generation."


async def build_adopt_conflict_message(
    db: Database, parent_id: int, child_id: int
) -> str:
    """Build clear adopt-rule error with generation info."""
    parent = await db.get_user(parent_id)
    child = await db.get_user(child_id)
    parent_name = parent.get("first_name") or "You"
    child_name = child.get("first_name") or "Unknown"

    if await db.is_ancestor_of(child_id, parent_id):
        return f"⚠️ You can't adopt {child_name} because {child_name} is your ancestor."

    child_parents = await db.get_parents(child_id)
    if child_parents:
        existing_parent_ids = {p["user_id"] for p in child_parents}
        if parent_id in existing_parent_ids:
            return f"⚠️ You're already a parent of {child_name}."
        adopter_spouses = await db.get_spouses(parent_id)
        adopter_spouse_ids = {s["user_id"] for s in adopter_spouses}
        if not (adopter_spouse_ids & existing_parent_ids):
            return (
                f"⚠️ You can't adopt {child_name} because {child_name} already has parent(s).\n"
                "Co-adoption is only allowed when you're married to one of the existing parents."
            )

    if await db.is_spouse_of(parent_id, child_id):
        return f"⚠️ You can't adopt {child_name} because you're married."
    if await db.are_siblings(parent_id, child_id):
        return f"⚠️ You can't adopt {child_name} because you're siblings."

    return f"⚠️ You can't adopt {child_name} due to family conflict."


async def build_makeparent_conflict_message(
    db: Database, child_id: int, parent_id: int
) -> str:
    """Build clear makeparent-rule error with generation info."""
    child = await db.get_user(child_id)
    parent = await db.get_user(parent_id)
    child_name = child.get("first_name") or "You"
    parent_name = parent.get("first_name") or "Unknown"

    if await db.is_ancestor_of(child_id, parent_id):
        return f"⚠️ You can't make {parent_name} your parent because you are their ancestor."

    child_parents = await db.get_parents(child_id)
    if child_parents:
        existing_parent_ids = {p["user_id"] for p in child_parents}
        if parent_id in existing_parent_ids:
            return f"⚠️ {parent_name} is already your parent."
        target_spouses = await db.get_spouses(parent_id)
        target_spouse_ids = {s["user_id"] for s in target_spouses}
        if not (target_spouse_ids & existing_parent_ids):
            return (
                f"⚠️ You can't make {parent_name} your parent because you already have parent(s).\n"
                f"Co-adoption is only allowed when {parent_name} is married to one of your existing parents."
            )

    if await db.is_spouse_of(parent_id, child_id):
        return f"⚠️ You can't make {parent_name} your parent because you're married."
    if await db.are_siblings(parent_id, child_id):
        return f"⚠️ You can't make {parent_name} your parent because you're siblings."

    return f"⚠️ You can't make {parent_name} your parent due to family conflict."


reg("adopt", "👶 Adopt someone as your child")


@client.on_message(filters.command(["adopt"]))
async def adopt_command(
    message: Message,
    bot: Bot,
):
    """Send adoption request - requester becomes parent."""
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await message.reply(
            "❌ You can't adopt me! I'm a bot and can't click confirmation buttons.\n"
            "Please ask another user to send the /adopt command to you instead."
        )
        return
    if not target:
        await message.reply(
            "Reply to a user or use /adopt @username to adopt someone."
        )
        return

    user = message.from_user

    if target.id == user.id:
        await message.reply("😅 You can't adopt yourself!")
        return

    if target.is_bot:
        await message.reply("🤖 You can't adopt a bot!")
        return

    await ensure_both_users(db, message, target)

    # Check if already parent of this user
    children = await db.get_children(user.id)
    if any(c["user_id"] == target.id for c in children):
        await message.reply(
            f"👨‍👧 You're already {target.first_name}'s parent!"
        )
        return

    # Check strict adoption rules
    if await db.is_adopt_hierarchy_conflict(user.id, target.id):
        base = await build_adopt_conflict_message(db, user.id, target.id)
        error_msg = await _with_family_conflict_details(
            db, user.id, target.id, "adopt", base
        )
        await _send_conflict_message(
            message, bot, db, user.id, target.id, error_msg
        )
        return

    # Create pending request
    request = await db.create_pending_request(
        request_type="adopt",
        requester_id=user.id,
        target_id=target.id,
        chat_id=message.chat.id,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Accept",
                    callback_data=f"adopt_accept:{request['id']}",
                ),
                InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=f"adopt_reject:{request['id']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Cancel",
                    callback_data=f"adopt_cancel:{request['id']}",
                ),
            ],
        ]
    )

    # Use HTML mentions for proper notifications
    user_link = util_user_mention(user)
    target_link = util_user_mention(target)

    sent = await message.reply(
        f"👶 <b>Adoption Request</b>\n\n"
        f"{user_link} wants to adopt {target_link}!\n\n"
        f"{target_link}, do you accept?",
        reply_markup=keyboard,
    )

    # Update request with message_id
    await db.execute(
        "UPDATE pending_requests SET message_id = $1 WHERE id = $2",
        sent.id,
        request["id"],
    )


reg("marry", "💒 Send marriage proposal")


@client.on_message(filters.command(["marry"]))
async def marry_command(
    message: Message,
    bot: Bot,
):
    """Send marriage proposal."""
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await message.reply(
            "❌ You can't propose to me! I'm a bot and can't click confirmation buttons.\n"
            "Please ask another user to send the /marry command to you instead."
        )
        return
    if not target:
        await message.reply(
            "Reply to a user or use /marry @username to propose."
        )
        return

    user = message.from_user

    if target.id == user.id:
        await message.reply("😅 You can't marry yourself!")
        return

    if target.is_bot:
        await message.reply("🤖 You can't marry a bot!")
        return

    await ensure_both_users(db, message, target)

    # Check if already married to each other
    if await db.are_married(user.id, target.id):
        await message.reply(
            f"💑 You're already married to {target.first_name}!"
        )
        return

    # Check if user is already married to someone else
    user_spouses = await db.get_spouses(user.id)
    if user_spouses:
        spouse_names = ", ".join(
            s["first_name"] or "Unknown" for s in user_spouses
        )
        await message.reply(
            f"💍 You're already married to {spouse_names}!\n"
            f"Use /divorce first if you want to marry someone else."
        )
        return

    # Check if target is already married to someone else
    target_spouses = await db.get_spouses(target.id)
    if target_spouses:
        await message.reply(
            f"💔 Badluck, {target.first_name} is already committed. "
            f"Good luck with dark magic and making them divorce... 🪄"
        )
        return

    # Check if they're siblings
    if await db.are_siblings(user.id, target.id):
        await message.reply(
            f"⚠️ You can't marry {target.first_name} - you're siblings!"
        )
        return

    # Check if one is ancestor of other
    if await db.is_ancestor(user.id, target.id):
        await message.reply(
            f"⚠️ You can't marry {target.first_name} - they're your descendant!"
        )
        return
    if await db.is_ancestor(target.id, user.id):
        await message.reply(
            f"⚠️ You can't marry {target.first_name} - they're your ancestor!"
        )
        return

    # Create pending request
    request = await db.create_pending_request(
        request_type="marry",
        requester_id=user.id,
        target_id=target.id,
        chat_id=message.chat.id,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💍 Accept",
                    callback_data=f"marry_accept:{request['id']}",
                ),
                InlineKeyboardButton(
                    text="💔 Reject",
                    callback_data=f"marry_reject:{request['id']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Cancel",
                    callback_data=f"marry_cancel:{request['id']}",
                ),
            ],
        ]
    )

    # Use HTML mentions for proper notifications
    user_link = util_user_mention(user)
    target_link = util_user_mention(target)

    sent = await message.reply(
        f"💒 <b>Marriage Proposal</b>\n\n"
        f"💍 {user_link} is proposing to {target_link}!\n\n"
        f"{target_link}, do you accept?",
        reply_markup=keyboard,
    )

    # Update request with message_id
    await db.execute(
        "UPDATE pending_requests SET message_id = $1 WHERE id = $2",
        sent.id,
        request["id"],
    )


reg("disown", "💔 Remove a child")


@client.on_message(filters.command(["disown"]))
async def disown_command(
    message: Message,
):
    """Show menu to disown children."""
    user = message.from_user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    children = await db.get_children(user.id)

    if not children:
        await message.reply("👶 You don't have any children to disown.")
        return

    buttons = []
    for child in children:
        name = child["first_name"] or "Unknown"
        buttons.append([
            InlineKeyboardButton(
                text=name, callback_data=f"disown:{user.id}:{child['user_id']}"
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="❌ Cancel", callback_data=f"disown:{user.id}:cancel"
        )
    ])

    await message.reply(
        "💔 <b>Select a child to disown:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


reg("divorce", "💔 Remove a spouse")


@client.on_message(filters.command(["divorce"]))
async def divorce_command(
    message: Message,
):
    """Show menu to divorce spouses."""
    user = message.from_user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    spouses = await db.get_spouses(user.id)

    if not spouses:
        await message.reply("💑 You don't have any spouses to divorce.")
        return

    if len(spouses) == 1:
        spouse = spouses[0]
        spouse_name = spouse["first_name"] or "Unknown"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💔 Yes, divorce",
                        callback_data=f"divorce:{user.id}:{spouse['user_id']}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data=f"divorce:{user.id}:cancel",
                    ),
                ]
            ]
        )
        await message.reply(
            f"💔 <b>Divorce {spouse_name}?</b>\n\nThis will end your marriage.",
            reply_markup=keyboard,
        )
        return

    buttons = []
    for spouse in spouses:
        name = spouse["first_name"] or "Unknown"
        buttons.append([
            InlineKeyboardButton(
                text=name,
                callback_data=f"divorce:{user.id}:{spouse['user_id']}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="❌ Cancel", callback_data=f"divorce:{user.id}:cancel"
        )
    ])

    await message.reply(
        "💔 <b>Select a spouse to divorce:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


reg("relations", "📋 List close family members")


@client.on_message(filters.command(["relations"]))
async def relations_command(
    message: Message,
    bot: Bot,
):
    """Show text list of close family relations."""
    # Check if targeting another user
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if target:
        await db.upsert_user(
            user_id=target.id,
            username=target.username,
            first_name=target.first_name,
        )
        user_id = target.id
        user_name = target.first_name
    else:
        user = message.from_user
        await db.upsert_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        user_id = user.id
        user_name = user.first_name

    family = await db.get_close_family(user_id)

    lines = [f"📋 <b>{user_name}'s Family</b>\n"]

    if family["spouses"]:
        spouse_names = ", ".join(
            user_display_name(s) for s in family["spouses"]
        )
        lines.append(f"💑 <b>Partner:</b> {spouse_names}")

    if family["parents"]:
        parent_names = ", ".join(
            user_display_name(p) for p in family["parents"]
        )
        lines.append(f"👨‍👩‍👧 <b>Parents:</b> {parent_names}")

    if family["siblings"]:
        sibling_names = ", ".join(
            user_display_name(s) for s in family["siblings"]
        )
        lines.append(f"👫 <b>Siblings:</b> {sibling_names}")

    if family["children"]:
        children_names = ", ".join(
            user_display_name(c) for c in family["children"]
        )
        lines.append(f"👶 <b>Children:</b> {children_names}")

    if len(lines) == 1:
        lines.append("No family relations yet.")
        lines.append("Use /adopt or /marry to build your family! 🌳")

    await message.reply("\n".join(lines))


reg(
    "family",
    "👨‍👩‍👧 Direct family explorer (parents, siblings, children)",
)


@client.on_message(filters.command(["family"]))
async def family_explorer(
    message: Message,
    bot: Bot,
):
    """Direct family explorer showing only blood relations (no spouses/linked)."""
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if target:
        await db.upsert_user(
            user_id=target.id,
            username=target.username,
            first_name=target.first_name,
        )
        user_id = target.id
        user_name = target.first_name
    else:
        user = message.from_user
        await db.upsert_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        user_id = user.id
        user_name = user.first_name

    await send_direct_family_explorer(
        message, db, user_id, user_name, owner_id=message.from_user.id
    )


reg(
    "fullfamily",
    "👨‍👩‍👧‍👦 Full extended family explorer (includes siblings' families)",
)


@client.on_message(filters.command(["fullfamily"]))
async def fullfamily_explorer(
    message: Message,
    bot: Bot,
):
    """Full extended family explorer including siblings' families and in-laws."""
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if target:
        await db.upsert_user(
            user_id=target.id,
            username=target.username,
            first_name=target.first_name,
        )
        user_id = target.id
        user_name = target.first_name
    else:
        user = message.from_user
        await db.upsert_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        user_id = user.id
        user_name = user.first_name

    await send_full_family_explorer(
        message, db, user_id, user_name, owner_id=message.from_user.id
    )


async def send_family_explorer(
    message: Message,
    db: Database,
    user_id: int,
    user_name: str,
    edit: bool = False,
):
    """Send or edit the family explorer message with simplified display."""
    family = await db.get_close_family(user_id)

    lines = [f"👤 <b>{user_name}'s Family</b>\n"]

    # Display family in a simple, easy-to-read format
    if family["spouses"]:
        spouse_list = ", ".join(user_display_name(s) for s in family["spouses"])
        lines.append(f"💑 <b>Spouse:</b> {spouse_list}")

    if family["parents"]:
        parent_list = ", ".join(user_display_name(p) for p in family["parents"])
        lines.append(f"👨‍👩‍👧 <b>Parents:</b> {parent_list}")

    if family["siblings"]:
        sibling_list = ", ".join(
            user_display_name(s) for s in family["siblings"]
        )
        lines.append(f"👫 <b>Siblings:</b> {sibling_list}")

    if family["children"]:
        children_list = ", ".join(
            user_display_name(c) for c in family["children"]
        )
        lines.append(f"👶 <b>Children:</b> {children_list}")

    if len(lines) == 1:
        lines.append("No family relations yet.")
        lines.append("Use /adopt or /marry to start your family! 🌳")

    # Create navigation buttons for interactive exploration
    buttons = []

    # Parents row (navigate up)
    if family["parents"]:
        parent_buttons = []
        for p in family["parents"][:3]:
            parent_buttons.append(
                InlineKeyboardButton(
                    text=f"⬆️ {user_display_name(p)[:10]}",
                    callback_data=f"fam_nav:{p['user_id']}",
                )
            )
        if parent_buttons:
            buttons.append(parent_buttons)

    # Spouses row (navigate sideways)
    if family["spouses"]:
        spouse_buttons = []
        for s in family["spouses"][:3]:
            spouse_buttons.append(
                InlineKeyboardButton(
                    text=f"💑 {user_display_name(s)[:10]}",
                    callback_data=f"fam_nav:{s['user_id']}",
                )
            )
        if spouse_buttons:
            buttons.append(spouse_buttons)

    # Siblings row (wrap into rows of 4 to avoid Telegram limits)
    if family["siblings"]:
        sibling_buttons = []
        for s in family["siblings"]:
            sibling_buttons.append(
                InlineKeyboardButton(
                    text=f"👫 {user_display_name(s)[:10]}",
                    callback_data=f"fam_nav:{s['user_id']}",
                )
            )
        if sibling_buttons:
            # Split into rows of 4
            for i in range(0, len(sibling_buttons), 4):
                buttons.append(sibling_buttons[i : i + 4])

    # Children row (navigate down)
    if family["children"]:
        child_buttons = []
        for c in family["children"][:3]:
            child_buttons.append(
                InlineKeyboardButton(
                    text=f"⬇️ {user_display_name(c)[:10]}",
                    callback_data=f"fam_nav:{c['user_id']}",
                )
            )
        if child_buttons:
            buttons.append(child_buttons)

    keyboard = (
        InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    )
    text = "\n".join(lines)

    if edit:
        await queue_it(
            lambda: message.edit_text(text, reply_markup=keyboard), message.chat
        )
    else:
        await message.reply(text, reply_markup=keyboard)


async def send_direct_family_explorer(
    message: Message,
    db: Database,
    user_id: int,
    user_name: str,
    edit: bool = False,
    owner_id: int = None,
):
    """Send direct family display showing only blood relations + spouses."""
    if owner_id is None:
        owner_id = user_id

    family = await db.get_close_family(user_id)

    # Get spouses of the current user
    spouses = await db.get_spouses(user_id)

    # /family shows direct parents only — those who actually adopted this
    # user via family_relationships. A parent's spouse only appears here
    # if they also co-adopted (their own row in the table).
    all_parents = family["parents"]

    # /family shows direct children only — kids actually adopted by this
    # user via family_relationships. Spouse-only kids are surfaced in
    # /fullfamily as linked children instead.
    all_children = family["children"]

    lines = [f"👤 <b>{user_name}'s Direct Family</b>\n"]

    # Spouse(s)
    if spouses:
        spouse_list = ", ".join(user_display_name(s) for s in spouses)
        lines.append(f"💑 <b>Spouse:</b> {spouse_list}")

    # Direct blood relations (parents) + their spouses as co-parents
    if all_parents:
        parent_list = ", ".join(user_display_name(p) for p in all_parents)
        lines.append(f"👨‍👩‍👧 <b>Parents:</b> {parent_list}")

    if family["siblings"]:
        sibling_list = ", ".join(
            user_display_name(s) for s in family["siblings"]
        )
        lines.append(f"👫 <b>Siblings:</b> {sibling_list}")

    if all_children:
        children_list = ", ".join(user_display_name(c) for c in all_children)
        lines.append(f"👶 <b>Children:</b> {children_list}")

    if len(lines) == 1:
        lines.append("No direct family relations yet.")
        lines.append(
            "Use /adopt to get a child, or /siblings to become siblings with someone! 🌳"
        )

    # Navigation buttons for direct family
    buttons = []
    cb_prefix = f"fam_direct:{owner_id}"

    # Cap each category so the keyboard stays under Telegram's button-count
    # limit (100 total). Worst case here: 8 + 8 + 32 + 48 = 96.
    SPOUSE_CAP = 8
    PARENT_CAP = 8
    SIBLING_CAP = 32
    CHILDREN_CAP = 48

    # Spouse row
    if spouses:
        spouse_row = []
        for spouse in spouses[:SPOUSE_CAP]:
            spouse_row.append(
                InlineKeyboardButton(
                    text=_safe_button_label(
                        user_display_name(spouse), prefix="💑 "
                    ),
                    callback_data=f"{cb_prefix}:{spouse['user_id']}",
                )
            )
        if spouse_row:
            buttons.append(spouse_row)

    # Parents row (including co-parents)
    if all_parents:
        row = []
        for person in all_parents[:PARENT_CAP]:
            row.append(
                InlineKeyboardButton(
                    text=_safe_button_label(
                        user_display_name(person), prefix="⬆️ "
                    ),
                    callback_data=f"{cb_prefix}:{person['user_id']}",
                )
            )
        if row:
            buttons.append(row)

    # Siblings row (wrap into rows of 4)
    if family["siblings"]:
        sibling_buttons = []
        for s in family["siblings"][:SIBLING_CAP]:
            sibling_buttons.append(
                InlineKeyboardButton(
                    text=_safe_button_label(user_display_name(s), prefix="👫 "),
                    callback_data=f"{cb_prefix}:{s['user_id']}",
                )
            )
        if sibling_buttons:
            for i in range(0, len(sibling_buttons), 4):
                buttons.append(sibling_buttons[i : i + 4])

    # Children row (own + spouse's)
    if all_children:
        children_buttons = []
        for c in all_children[:CHILDREN_CAP]:
            children_buttons.append(
                InlineKeyboardButton(
                    text=_safe_button_label(user_display_name(c), prefix="⬇️ "),
                    callback_data=f"{cb_prefix}:{c['user_id']}",
                )
            )
        if children_buttons:
            for i in range(0, len(children_buttons), 4):
                buttons.append(children_buttons[i : i + 4])

    # Empty inline_keyboard=[] is rejected as REPLY_MARKUP_INVALID — only
    # attach a markup when there is at least one button.
    keyboard = (
        InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    )
    text = "\n".join(lines)

    if edit:
        await queue_it(
            lambda: message.edit_text(text, reply_markup=keyboard), message.chat
        )
    else:
        await message.reply(text, reply_markup=keyboard)


async def send_full_family_explorer(
    message: Message,
    db: Database,
    user_id: int,
    user_name: str,
    edit: bool = False,
    owner_id: int = None,
):
    """Send extended family display including siblings' families and in-laws."""
    if owner_id is None:
        owner_id = user_id

    family = await db.get_extended_family(user_id)

    # Surface the user's own children directly, and their spouses'
    # children (whom this user didn't actually adopt) as "linked" so the
    # distinction is visible.
    spouses = await db.get_spouses(user_id)
    direct_child_ids = {c["user_id"] for c in family["children"]}
    linked_children = []
    if spouses:
        for spouse in spouses:
            for sk in await db.get_children(spouse["user_id"]):
                if sk["user_id"] not in direct_child_ids and not any(
                    lc["user_id"] == sk["user_id"] for lc in linked_children
                ):
                    linked_children.append(sk)

    all_children = family["children"] + linked_children

    lines = [f"👤 <b>{user_name}'s Full Family</b>\n"]

    # Direct family
    if family["spouses"]:
        spouse_list = ", ".join(user_display_name(s) for s in family["spouses"])
        lines.append(f"💑 <b>Spouse:</b> {spouse_list}")

    if family["parents"]:
        parent_list = ", ".join(user_display_name(p) for p in family["parents"])
        lines.append(f"👨‍👩‍👧 <b>Parents:</b> {parent_list}")

    if family["siblings"]:
        sibling_list = ", ".join(
            user_display_name(s) for s in family["siblings"]
        )
        lines.append(f"👫 <b>Siblings:</b> {sibling_list}")

    if family["children"]:
        children_list = ", ".join(
            user_display_name(c) for c in family["children"]
        )
        lines.append(f"👶 <b>Children:</b> {children_list}")

    if linked_children:
        linked_list = ", ".join(user_display_name(c) for c in linked_children)
        lines.append(f"👶🔗 <b>Linked Children (spouse's):</b> {linked_list}")

    # Extended family
    if family["extended_siblings"]:
        ext_list = ", ".join(
            user_display_name(s) for s in family["extended_siblings"]
        )
        lines.append(f"👫🔗 <b>Extended Siblings:</b> {ext_list}")

    if family["nieces_nephews"]:
        nn_list = ", ".join(
            user_display_name(n) for n in family["nieces_nephews"]
        )
        lines.append(f"👶🔗 <b>Nieces/Nephews:</b> {nn_list}")

    if family["in_laws"]:
        inlaw_list = ", ".join(user_display_name(i) for i in family["in_laws"])
        lines.append(f"🤝 <b>In-Laws:</b> {inlaw_list}")

    if family["grandchildren"]:
        gc_list = ", ".join(
            user_display_name(g) for g in family["grandchildren"]
        )
        lines.append(f"👶👶 <b>Grandchildren:</b> {gc_list}")

    if len(lines) == 1:
        lines.append("No family relations yet.")
        lines.append("Use /adopt or /marry to start your family! 🌳")

    # Navigation buttons
    buttons = []
    cb_prefix = f"fam_full:{owner_id}"

    # Direct family buttons
    for section, key, emoji in [
        ("parents", "parents", "⬆️"),
        ("spouses", "spouses", "💑"),
        ("siblings", "siblings", "👫"),
    ]:
        if family[key]:
            row = []
            for person in family[key][:4]:
                row.append(
                    InlineKeyboardButton(
                        text=_safe_button_label(
                            user_display_name(person), prefix=f"{emoji} "
                        ),
                        callback_data=f"{cb_prefix}:{person['user_id']}",
                    )
                )
            buttons.append(row)

    # Direct children, then spouse's children marked as linked.
    if family["children"]:
        row = []
        for person in family["children"][:4]:
            row.append(
                InlineKeyboardButton(
                    text=_safe_button_label(
                        user_display_name(person), prefix="⬇️ "
                    ),
                    callback_data=f"{cb_prefix}:{person['user_id']}",
                )
            )
        buttons.append(row)

    if linked_children:
        row = []
        for person in linked_children[:4]:
            row.append(
                InlineKeyboardButton(
                    text=_safe_button_label(
                        user_display_name(person), prefix="🔗 "
                    ),
                    callback_data=f"{cb_prefix}:{person['user_id']}",
                )
            )
        buttons.append(row)

    keyboard = (
        InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    )
    text = "\n".join(lines)

    if edit:
        await queue_it(
            lambda: message.edit_text(text, reply_markup=keyboard), message.chat
        )
    else:
        await message.reply(text, reply_markup=keyboard)


reg("tree", "🌳 View family tree (your direct family)")
reg("fulltree", "🌳🔍 View full extended tree (includes siblings' families)")


@client.on_message(filters.command(["tree"]))
async def tree_command(
    message: Message,
    bot: Bot,
):
    """Show family tree image."""
    await _render_tree(message, bot, db, full=False)


@client.on_message(filters.command(["fulltree"]))
async def fulltree_command(
    message: Message,
    bot: Bot,
):
    """Show full extended family tree including siblings' families."""
    await _render_tree(message, bot, db, full=True)


async def _render_tree(message: Message, bot: Bot, db: Database, full: bool):
    """Render family tree (full=True for extended tree)."""
    # Check if targeting another user
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if target:
        await db.upsert_user(
            user_id=target.id,
            username=target.username,
            first_name=target.first_name,
        )
        user_id = target.id
        user_name = target.first_name
    else:
        user = message.from_user
        await db.upsert_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )
        user_id = user.id
        user_name = user.first_name

    label = "Full extended " if full else ""
    status_msg = await message.reply(f"🔄 Generating {label}family tree...")

    try:
        if full:
            from bot.graphics.tree_renderer import render_full_family_tree

            image_bytes = await render_full_family_tree(bot, db, user_id)
        else:
            from bot.graphics.tree_renderer import render_family_tree

            image_bytes = await render_family_tree(bot, db, user_id)

        if image_bytes:
            tree_label = "Full Extended " if full else ""
            caption = f"🌳 <b>{tree_label}{user_name}'s Family Tree</b>"
            try:
                await message.reply_photo(
                    photo=to_input_file(
                        image_bytes, filename="family_tree.png"
                    ),
                    caption=caption,
                )
            except BadRequest as bad:
                # Wide / tall trees can violate Telegram's photo dimension
                # rules. Fall back to sending the PNG as a document so the
                # user still gets the image.
                if "PHOTO_INVALID_DIMENSIONS" not in str(bad):
                    raise
                await message.reply_document(
                    document=to_input_file(
                        image_bytes, filename="family_tree.png"
                    ),
                    caption=caption,
                )
        else:
            await message.reply(
                f"🌳 <b>{user_name}'s Family Tree</b>\n\n"
                "No family connections yet. Use /adopt or /marry to start building! 👨‍👩‍👧‍👦"
            )

        await status_msg.delete()

    except Exception as e:
        logger.exception("Failed to generate family tree for %s", user_id)
        err_text = str(e)[:200]
        await queue_it(
            lambda: status_msg.edit_text(
                f"❌ Failed to generate tree: {err_text}"
            ),
            status_msg.chat,
        )


reg("siblings", "👫 Ask to be siblings")


@client.on_message(filters.command(["siblings"]))
async def siblings_command(
    message: Message,
    bot: Bot,
):
    """Ask someone to be your sibling."""
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if not target:
        await message.reply(
            "Reply to a user or use /siblings @username to ask them to be your sibling."
        )
        return

    user = message.from_user

    if target.id == user.id:
        await message.reply("😅 You can't be siblings with yourself!")
        return

    if target.is_bot:
        await message.reply("🤖 You can't be siblings with a bot!")
        return

    await ensure_both_users(db, message, target)

    # Check if already siblings
    if await db.are_siblings(user.id, target.id):
        await message.reply(
            f"👫 You're already siblings with {target.first_name}!"
        )
        return

    # Check sibling rule: same generation + parent constraint + not spouse
    if await db.is_sibling_hierarchy_conflict(user.id, target.id):
        base = await build_sibling_conflict_message(db, user.id, target.id)
        error_msg = await _with_family_conflict_details(
            db, user.id, target.id, "be siblings with", base
        )
        await _send_conflict_message(
            message, bot, db, user.id, target.id, error_msg
        )
        return

    # Create pending request
    request = await db.create_pending_request(
        request_type="siblings",
        requester_id=user.id,
        target_id=target.id,
        chat_id=message.chat.id,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Accept",
                    callback_data=f"siblings_accept:{request['id']}",
                ),
                InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=f"siblings_reject:{request['id']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Cancel",
                    callback_data=f"siblings_cancel:{request['id']}",
                ),
            ],
        ]
    )

    # Use HTML mentions for proper notifications
    user_link = util_user_mention(user)
    target_link = util_user_mention(target)

    sent = await message.reply(
        f"👫 <b>Sibling Request</b>\n\n"
        f"{user_link} wants to be siblings with {target_link}!\n\n"
        f"{target_link}, do you accept?",
        reply_markup=keyboard,
    )

    await db.execute(
        "UPDATE pending_requests SET message_id = $1 WHERE id = $2",
        sent.id,
        request["id"],
    )


reg("removesibling", "👋 Remove sibling")


@client.on_message(filters.command(["removesibling"]))
async def removesibling_command(
    message: Message,
    bot: Bot,
):
    """Remove a sibling relationship."""
    user = message.from_user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Check if replying to someone
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if target:
        await db.upsert_user(
            user_id=target.id,
            username=target.username,
            first_name=target.first_name,
        )

        # Check if they are direct siblings (not via shared parents)
        if not await db.is_direct_sibling(user.id, target.id):
            # Check if siblings via shared parents
            if await db.are_siblings(user.id, target.id):
                await message.reply(
                    f"⚠️ {target.first_name} is your sibling's sibling.\n"
                    f"They are connected to you through a shared parent, not directly.\n"
                    f"You can't remove this relationship directly."
                )
            else:
                await message.reply(
                    f"❌ You're not siblings with {target.first_name}!"
                )
            return

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Yes, remove",
                        callback_data=f"removesibling:{target.id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel", callback_data="removesibling:cancel"
                    ),
                ]
            ]
        )

        await message.reply(
            f"👫 Are you sure you want to remove {target.first_name} as your sibling?",
            reply_markup=keyboard,
        )
        return

    # Show list of direct siblings to remove
    siblings = await db.get_siblings(user.id)
    if not siblings:
        await message.reply("😅 You don't have any siblings.")
        return

    # Filter to only direct siblings
    direct_siblings = []
    for sib in siblings:
        if await db.is_direct_sibling(user.id, sib["user_id"]):
            direct_siblings.append(sib)

    if not direct_siblings:
        await message.reply(
            "ℹ️ You don't have any removable sibling relationships.\n"
            "(Siblings via shared parents cannot be removed directly.)"
        )
        return

    buttons = []
    for sib in direct_siblings[:20]:
        name = sib["first_name"] or "Unknown"
        buttons.append([
            InlineKeyboardButton(
                text=f"👫 {name}",
                callback_data=f"removesibling:{sib['user_id']}",
            )
        ])
    buttons.append([
        InlineKeyboardButton(
            text="❌ Cancel", callback_data="removesibling:cancel"
        )
    ])

    await message.reply(
        "👫 <b>Select a sibling to remove:</b>",
        reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons),
    )


reg("makeparent", "👨‍👩‍👧 Ask someone to adopt you [/mp]")


@client.on_message(filters.command(["makeparent"]))
async def makeparent_command(
    message: Message,
    bot: Bot,
):
    """Ask someone to be your parent (adopt you)."""
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if not target:
        await message.reply(
            "ℹ️ Reply to a user or use /makeparent @username to ask them to adopt you."
        )
        return

    user = message.from_user

    if target.id == user.id:
        await message.reply("😅 You can't adopt yourself!")
        return

    if target.is_bot:
        await message.reply("🤖 A bot can't be your parent!")
        return

    await ensure_both_users(db, message, target)

    # Check if target is already your parent
    parents = await db.get_parents(user.id)
    if any(p["user_id"] == target.id for p in parents):
        await message.reply(
            f"👨‍👩‍👧 {target.first_name} is already your parent!"
        )
        return

    # Check strict makeparent rules (target as parent, user as child)
    if await db.is_adopt_hierarchy_conflict(target.id, user.id):
        base = await build_makeparent_conflict_message(db, user.id, target.id)
        error_msg = await _with_family_conflict_details(
            db, target.id, user.id, "be parent of", base
        )
        await _send_conflict_message(
            message, bot, db, target.id, user.id, error_msg
        )
        return

    # Create pending request (reversed - target is parent, user is child)
    request = await db.create_pending_request(
        request_type="makeparent",
        requester_id=user.id,
        target_id=target.id,
        chat_id=message.chat.id,
    )

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Accept",
                    callback_data=f"makeparent_accept:{request['id']}",
                ),
                InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=f"makeparent_reject:{request['id']}",
                ),
            ]
        ]
    )

    # Use HTML mentions for proper notifications
    user_link = util_user_mention(user)
    target_link = util_user_mention(target)

    sent = await message.reply(
        f"👨‍👩‍👧 <b>Adoption Request</b>\n\n"
        f"{user_link} wants {target_link} to be their parent!\n\n"
        f"{target_link}, do you accept?",
        reply_markup=keyboard,
    )

    await db.execute(
        "UPDATE pending_requests SET message_id = $1 WHERE id = $2",
        sent.id,
        request["id"],
    )


reg("runaway", "🏃 Run away from your parents")


@client.on_message(filters.command(["runaway"]))
async def runaway_command(
    message: Message,
):
    """Run away from your parents (remove all parent relationships)."""
    user = message.from_user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    parents = await db.get_parents(user.id)

    if not parents:
        await message.reply("😅 You don't have any parents to run away from.")
        return

    parent_names = ", ".join(p["first_name"] or "Unknown" for p in parents)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏃 Yes, run away!",
                    callback_data=f"runaway:confirm:{user.id}",
                ),
                InlineKeyboardButton(
                    text="🏠 Stay", callback_data=f"runaway:cancel:{user.id}"
                ),
            ]
        ]
    )

    await message.reply(
        f"🏃 <b>Run Away from Home?</b>\n\n"
        f"👨‍👩‍👧 Your parents: {parent_names}\n\n"
        f"Are you sure you want to run away? "
        f"This will remove all parent relationships.",
        reply_markup=keyboard,
    )


# ─────────────────────────── /funeral ───────────────────────────


reg("funeral", "🪦 Hold a funeral for a deleted account")


def _funeral_caption(
    deceased_id: int,
    deceased_name: Optional[str],
    donor_name: str,
    amount: int,
) -> str:
    e1, e2, e3 = FUNERAL_EMOJI_IDS
    if deceased_name:
        target_descr = (
            f"<b>{html_escape(deceased_name)}</b> "
            f"(<code>{deceased_id}</code>) deleted account"
        )
    else:
        target_descr = f"deleted account <code>{deceased_id}</code>"
    return (
        f'<tg-emoji emoji-id="{e1}">🕯️</tg-emoji> '
        f"<b>In Loving Memory</b> "
        f'<tg-emoji emoji-id="{e1}">🕯️</tg-emoji>\n'
        f"━━━━━━━━━━━━━━━━\n\n"
        f'<tg-emoji emoji-id="{e2}">🪦</tg-emoji> A funeral has been held '
        f"for the {target_descr}.\n\n"
        f"💐 Donated by: {donor_name}\n"
        f"💸 Amount: <b>${amount:,}</b>\n\n"
        f'<tg-emoji emoji-id="{e3}">🌹</tg-emoji> '
        f"<i>Rest in peace. The family will not forget.</i>"
    )


async def _resolve_deceased(
    bot: Bot, target_id: int
) -> tuple[bool, Optional[str]]:
    """Returns (is_deleted, telegram_first_name_if_alive).
    - is_deleted True if the Telegram account looks deleted (first_name empty)
      or the API refuses to return it (PEER_ID_INVALID / USER_DELETED).
    - first_name returned only if the account is alive (so we can refuse with
      a meaningful 'this is X, not deleted' message).
    """
    try:
        user_obj = await bot.get_users(target_id)
    except Exception:
        return True, None
    first = getattr(user_obj, "first_name", None)
    if not first:
        return True, None
    return False, first


@client.on_message(filters.command(["funeral"]))
async def funeral_command(message: Message, bot: Bot):
    """Hold a funeral for a deleted Telegram account.

    Usage:
      /funeral 123456789           — default ${default:,} donation
      /funeral 123456789 50000000  — custom amount
      (reply) /funeral             — uses the replied user's id
      (reply) /funeral 5000000     — custom amount, replied user
    """
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    target_id: Optional[int] = None
    amount_arg: Optional[str] = None

    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
        parts = (message.text or "").split()[1:]
        if parts:
            amount_arg = parts[0]
    else:
        parts = (message.text or "").split()[1:]
        if not parts:
            await message.reply(
                "🪦 <b>Funeral</b>\n\n"
                "Hold a funeral for a deleted Telegram account.\n\n"
                "Usage:\n"
                "• <code>/funeral &lt;user_id&gt;</code>\n"
                "• <code>/funeral &lt;user_id&gt; &lt;amount&gt;</code>\n"
                "• Reply to a message from the deleted user with "
                "<code>/funeral</code> "
                "(optional amount after the command)\n\n"
                f"Default donation: ${FUNERAL_DEFAULT_AMOUNT:,}\n"
                "Each user may donate to a given deceased only once."
            )
            return
        if not parts[0].lstrip("@").isdigit():
            await message.reply(
                "❌ Provide a numeric user_id (or reply to their old message)."
            )
            return
        target_id = int(parts[0].lstrip("@"))
        if len(parts) > 1:
            amount_arg = parts[1]

    if target_id == user.id:
        await message.reply("❌ You can't hold a funeral for yourself!")
        return

    amount = FUNERAL_DEFAULT_AMOUNT
    if amount_arg is not None:
        parsed = parse_money_amount(amount_arg)
        if parsed is None or parsed <= 0:
            await message.reply(
                "❌ Invalid amount. Use a positive number "
                "(e.g. <code>5000000</code> or <code>5+6</code>)."
            )
            return
        amount = parsed

    is_deleted, alive_name = await _resolve_deceased(bot, target_id)
    if not is_deleted:
        await message.reply(
            f"❌ <code>{target_id}</code> is "
            f"<b>{html_escape(alive_name or 'a live account')}</b> — "
            f"only deleted accounts can have a funeral."
        )
        return

    # Make sure the deceased exists in our users table so the FK is valid.
    # db.execute is the asyncpg-style wrapper ($N placeholders, positional args).
    await db.execute(
        "INSERT INTO users (user_id) VALUES ($1) ON CONFLICT DO NOTHING",
        target_id,
    )

    if await db.has_donated_to_funeral(user.id, target_id):
        await message.reply(
            "ℹ️ You've already donated to this account's funeral."
        )
        return

    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < amount:
        await message.reply(
            f"❌ Not enough money. Need ${amount:,}, you have ${wallet['balance']:,}."
        )
        return

    # Capture both amount and target on the callback so the confirm step
    # doesn't have to re-parse.
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Confirm",
                    callback_data=f"funeral:cf:{user.id}:{target_id}:{amount}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"funeral:xx:{user.id}",
                ),
            ]
        ]
    )

    e1 = FUNERAL_EMOJI_IDS[0]
    await message.reply(
        f'<tg-emoji emoji-id="{e1}">🕯️</tg-emoji> '
        f"<b>Funeral Confirmation</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Deceased account: <code>{target_id}</code>\n"
        f"💸 Donation: <b>${amount:,}</b>\n"
        f"💰 Your balance: ${wallet['balance']:,}\n\n"
        f"To change the amount, run "
        f"<code>/funeral {target_id} &lt;amount&gt;</code> "
        f"(e.g. <code>/funeral {target_id} 25000000</code>).",
        reply_markup=keyboard,
    )


def html_escape(s: str) -> str:
    import html as _html

    return _html.escape(s or "")


@client.on_callback_query(filters.regex(r"^funeral:xx:"))
async def funeral_cancel_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    initiator_id = (
        int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else None
    )
    if initiator_id is not None and callback.from_user.id != initiator_id:
        await callback.answer("Only the initiator can cancel.", show_alert=True)
        return
    await queue_it(
        lambda: callback.message.edit_text("❌ Funeral cancelled."),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^funeral:cf:"))
async def funeral_confirm_callback(callback: CallbackQuery, bot: Bot):
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer("Invalid funeral payload.", show_alert=True)
        return
    initiator_id = int(parts[2])
    deceased_id = int(parts[3])
    amount = int(parts[4])

    if callback.from_user.id != initiator_id:
        await callback.answer(
            "Only the initiator can confirm this funeral.", show_alert=True
        )
        return

    donor = callback.from_user
    # Re-check balance and dup at confirm time (the wait could be long).
    wallet = await db.get_wallet(donor.id)
    if wallet["balance"] < amount:
        await callback.answer("❌ Not enough money anymore.", show_alert=True)
        return

    if await db.has_donated_to_funeral(donor.id, deceased_id):
        await callback.answer(
            "You've already donated to this funeral.", show_alert=True
        )
        return

    recorded = await db.record_funeral_donation(donor.id, deceased_id, amount)
    if not recorded:
        await callback.answer(
            "You've already donated to this funeral.", show_alert=True
        )
        return

    await db.add_balance(
        donor.id, -amount, f"Funeral donation for {deceased_id}"
    )

    # Pull the historical first_name from our users row (the account is
    # deleted on Telegram's side but may still have a remembered name
    # from before it was wiped).
    deceased_row = await db.get_user(deceased_id)
    deceased_name = (
        deceased_row.first_name
        if deceased_row and getattr(deceased_row, "first_name", None)
        else None
    )
    donor_safe = util_user_mention(donor)
    caption = _funeral_caption(deceased_id, deceased_name, donor_safe, amount)

    funeral_text = f"✅ Funeral held. Donation recorded: <b>${amount:,}</b>"
    await queue_it(
        lambda: callback.message.edit_text(funeral_text),
        callback.message.chat,
    )
    await callback.answer()

    # Send the coffin video with the caption above; fall back to text-only
    # if no file_id is configured yet.
    try:
        if COFFIN_VIDEO_FILE_ID:
            await bot.send_video(
                callback.message.chat.id,
                video=COFFIN_VIDEO_FILE_ID,
                caption=caption,
                show_caption_above_media=True,
            )
        else:
            await bot.send_message(
                callback.message.chat.id,
                caption,
            )
    except Exception:
        logger.exception("Failed to send funeral video/message")
