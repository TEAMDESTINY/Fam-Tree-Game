"""Admin utility commands."""

import asyncio
import html
import logging
import sys
from typing import Optional

from pyrogram import Client as Bot
from pyrogram import filters
from pyrogram.enums import ChatType
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)

from bot.client import client
from bot.config import Config
from bot.database import db
from bot.queue_it import queue_it
from bot.utils import parse_money_amount

logger = logging.getLogger(__name__)


def is_admin(config: Config, user_id: int) -> bool:
    """Check if user is the bot owner/admin."""
    return user_id == config.owner_id


async def get_target_user(bot: Bot, message: Message) -> Optional[User]:
    """
    Extract target user from message (reply, mention, or user ID).

    # TODO: Text mention handling needs verification
    """
    # Check if replying to someone
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    # Check for command arguments
    parts = message.text.split() if message.text else []
    if len(parts) > 1:
        username = parts[1].lstrip("@")
        if username.isdigit():
            try:
                user_obj = await bot.get_users(int(username))
                if not user_obj.is_bot:
                    return user_obj
            except Exception:
                pass
        else:
            try:
                user_obj = await bot.get_users(f"@{username}")
                if not user_obj.is_bot:
                    return user_obj
            except Exception:
                return None

    return None


@client.on_message(filters.command(["givebalance"]))
async def give_balance_command(message: Message, bot: Bot, config: Config):
    """Give balance to a user (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    parts = message.text.split() if message.text else []
    if len(parts) < 2:
        await message.reply("Usage: /givebalance @username amount")
        return

    amount = parse_money_amount(parts[-1])
    if amount is None:
        await message.reply("❌ Amount must be a number.")
        return

    target = await get_target_user(bot, message)
    if not target:
        await message.reply(
            "❌ No user found!\nReply to a user or use /givebalance @username amount"
        )
        return

    await db.upsert_user(
        target.id, username=target.username, first_name=target.first_name
    )
    await db.add_bank_balance(target.id, amount)

    await message.reply(
        f"🏦 Deposited ${amount:,} into {target.first_name}'s bank"
    )


@client.on_message(filters.command(["dbstats"]))
async def db_stats_command(message: Message, config: Config):
    """Show database statistics (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    async with db.connection() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        families = await conn.fetchval(
            "SELECT COUNT(*) FROM family_relationships"
        )
        marriages = await conn.fetchval("SELECT COUNT(*) FROM marriages")
        friendships = await conn.fetchval("SELECT COUNT(*) FROM friendships")
        pending = await conn.fetchval("SELECT COUNT(*) FROM pending_requests")

    await message.reply(
        f"📊 <b>Database Statistics</b>\n\n"
        f"👤 Users: {users}\n"
        f"👨‍👧 Parent-Child Relations: {families}\n"
        f"💑 Marriages: {marriages}\n"
        f"🤝 Friendships: {friendships}\n"
        f"⏳ Pending Requests: {pending}"
    )


@client.on_message(filters.command(["refresh_achievement"]))
async def refresh_achievement_command(message: Message, config: Config):
    """Re-check and refresh achievements for all users (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    from bot.achievements import check_all_achievements

    status = await message.reply("⏳ Refreshing achievements for all users...")

    users = await db.fetch("SELECT user_id FROM users")
    for row in users:
        await check_all_achievements(db, row["user_id"])

    await queue_it(
        lambda: status.edit_text(
            f"✅ Achievement refresh complete.\nProcessed {len(users)} users."
        ),
        status.chat,
    )


@client.on_message(filters.command(["broadcast"]))
async def broadcast_command(message: Message, bot: Bot, config: Config):
    """Forward a replied message to all users in DM (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    if not message.reply_to_message:
        await message.reply(
            "📢 Reply to a message to broadcast it to all users in DM."
        )
        return

    async with db.connection() as conn:
        users = await conn.fetch("SELECT user_id FROM users")

    from_chat_id = message.chat.id
    msg_id = message.reply_to_message.id
    user_count = len(users)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Confirm",
                    callback_data=f"bcast_confirm:{from_chat_id}:{msg_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="bcast_cancel",
                ),
            ]
        ]
    )
    await message.reply(
        f"📢 <b>Broadcast Confirmation</b>\n\n"
        f"Forward this message to <b>{user_count:,}</b> users in DM?\n"
        f"Rate: 20 per second",
        reply_markup=keyboard,
    )


@client.on_callback_query(filters.regex(r"^bcast_confirm:"))
async def broadcast_confirm_callback(
    callback: CallbackQuery, bot: Bot, config: Config
):
    """Execute DM broadcast after confirmation."""
    if not is_admin(config, callback.from_user.id):
        await callback.answer("⛔ Admins only!", show_alert=True)
        return

    parts = callback.data.split(":")
    from_chat_id = int(parts[1])
    msg_id = int(parts[2])

    async with db.connection() as conn:
        users = await conn.fetch("SELECT user_id FROM users")

    await callback.message.edit_text(
        f"📢 Broadcasting to {len(users):,} users..."
    )
    await callback.answer()

    sent = 0
    failed = 0
    fail_reasons: dict[str, int] = {}
    batch_size = 20

    for i in range(0, len(users), batch_size):
        batch = users[i : i + batch_size]
        for user in batch:
            try:
                await bot.forward_messages(
                    user["user_id"], from_chat_id, msg_id
                )
                sent += 1
            except Exception as e:
                failed += 1
                key = type(e).__name__
                fail_reasons[key] = fail_reasons.get(key, 0) + 1
                logger.warning(
                    "broadcast DM failed uid=%s: %s", user["user_id"], e
                )

        await asyncio.sleep(1)

        completed = i + len(batch)
        if completed % 200 == 0 or completed >= len(users):
            try:
                await callback.message.edit_text(
                    f"📢 Broadcasting... {completed:,}/{len(users):,}\n"
                    f"✅ {sent:,} | ❌ {failed:,}"
                )
            except Exception:
                pass

    fail_text = ""
    if fail_reasons:
        lines = "\n".join(
            f"  {idx}. {name}: {count}"
            for idx, (name, count) in enumerate(fail_reasons.items(), 1)
        )
        fail_text = f"\n\n<b>Failed ({failed}):</b>\n{lines}"

    await callback.message.edit_text(
        f"📢 <b>Broadcast Complete</b>\n\n"
        f"✅ Sent: {sent:,}\n❌ Failed: {failed:,}"
        f"{fail_text}"
    )


@client.on_callback_query(filters.regex(r"^bcast_cancel$"))
async def broadcast_cancel_callback(callback: CallbackQuery, config: Config):
    if not is_admin(config, callback.from_user.id):
        await callback.answer("⛔ Admins only!", show_alert=True)
        return
    await callback.message.edit_text("❌ Broadcast cancelled.")
    await callback.answer()


@client.on_message(filters.command(["groupbroadcast", "gb"]))
async def groupbroadcast_command(message: Message, bot: Bot, config: Config):
    """Forward a replied message to all groups in the chats table (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    if not message.reply_to_message:
        await message.reply(
            "📣 Reply to a message to broadcast it to all groups."
        )
        return

    async with db.connection() as conn:
        chats = await conn.fetch("SELECT chat_id FROM chats")

    from_chat_id = message.chat.id
    msg_id = message.reply_to_message.id
    chat_count = len(chats)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Confirm",
                    callback_data=f"gbcast_confirm:{from_chat_id}:{msg_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="gbcast_cancel",
                ),
            ]
        ]
    )
    await message.reply(
        f"📣 <b>Group Broadcast Confirmation</b>\n\n"
        f"Forward this message to <b>{chat_count:,}</b> groups?\n"
        f"Rate: 20 per second",
        reply_markup=keyboard,
    )


@client.on_callback_query(filters.regex(r"^gbcast_confirm:"))
async def groupbroadcast_confirm_callback(
    callback: CallbackQuery, bot: Bot, config: Config
):
    """Execute group broadcast after confirmation."""
    if not is_admin(config, callback.from_user.id):
        await callback.answer("⛔ Admins only!", show_alert=True)
        return

    parts = callback.data.split(":")
    from_chat_id = int(parts[1])
    msg_id = int(parts[2])

    async with db.connection() as conn:
        chats = await conn.fetch("SELECT chat_id FROM chats")

    await callback.message.edit_text(
        f"📣 Broadcasting to {len(chats):,} groups..."
    )
    await callback.answer()

    sent = 0
    failed = 0
    fail_reasons: dict[str, int] = {}
    batch_size = 20

    for i in range(0, len(chats), batch_size):
        batch = chats[i : i + batch_size]
        for chat in batch:
            try:
                await bot.forward_messages(
                    chat["chat_id"], from_chat_id, msg_id
                )
                sent += 1
            except Exception as e:
                failed += 1
                key = type(e).__name__
                fail_reasons[key] = fail_reasons.get(key, 0) + 1
                logger.warning(
                    "groupbroadcast failed chat_id=%s: %s", chat["chat_id"], e
                )

        await asyncio.sleep(1)

        completed = i + len(batch)
        if completed % 200 == 0 or completed >= len(chats):
            try:
                await callback.message.edit_text(
                    f"📣 Broadcasting... {completed:,}/{len(chats):,}\n"
                    f"✅ {sent:,} | ❌ {failed:,}"
                )
            except Exception:
                pass

    fail_text = ""
    if fail_reasons:
        lines = "\n".join(
            f"  {idx}. {name}: {count}"
            for idx, (name, count) in enumerate(fail_reasons.items(), 1)
        )
        fail_text = f"\n\n<b>Failed ({failed}):</b>\n{lines}"

    await callback.message.edit_text(
        f"📣 <b>Group Broadcast Complete</b>\n\n"
        f"✅ Sent: {sent:,}\n❌ Failed: {failed:,}"
        f"{fail_text}"
    )


@client.on_callback_query(filters.regex(r"^gbcast_cancel$"))
async def groupbroadcast_cancel_callback(
    callback: CallbackQuery, config: Config
):
    if not is_admin(config, callback.from_user.id):
        await callback.answer("⛔ Admins only!", show_alert=True)
        return
    await callback.message.edit_text("❌ Group broadcast cancelled.")
    await callback.answer()


@client.on_message(filters.command(["transferaccount"]))
async def transfer_account_command(message: Message, bot: Bot, config: Config):
    """Transfer user1's data to user2, deleting user2's old data (admin only).

    Usage: /transferaccount @user1 @user2
    This will:
    1. Delete all of user2's data
    2. Update user1's user_id to user2's user_id
    3. User1's data (balance, relationships, etc.) moves to user2's ID
    """
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    parts = message.text.split() if message.text else []
    if len(parts) < 3:
        await message.reply(
            "Usage: /transferaccount @user1 @user2\n\n"
            "⚠️ This will DELETE user2's data and move user1's data to user2's ID!"
        )
        return

    # Parse user1 and user2 from arguments
    user1_id_str = parts[1]
    user2_id_str = parts[2]

    # Remove @ prefix if present
    user1_id_str = user1_id_str.lstrip("@")
    user2_id_str = user2_id_str.lstrip("@")

    # Try to parse as integers
    try:
        user1_id = int(user1_id_str)
    except ValueError:
        await message.reply("❌ User1 must be a valid user ID or @username.")
        return

    try:
        user2_id = int(user2_id_str)
    except ValueError:
        await message.reply("❌ User2 must be a valid user ID or @username.")
        return

    # If not pure integers, resolve username via bot.get_users
    if not user1_id_str.isdigit():
        try:
            u = await bot.get_users(
                f"@{user1_id_str}"
                if not user1_id_str.startswith("@")
                else user1_id_str
            )
            user1_id = u.id
        except Exception:
            await message.reply(f"❌ Could not find user1: {user1_id_str}")
            return

    if not user2_id_str.isdigit():
        try:
            u = await bot.get_users(
                f"@{user2_id_str}"
                if not user2_id_str.startswith("@")
                else user2_id_str
            )
            user2_id = u.id
        except Exception:
            await message.reply(f"❌ Could not find user2: {user2_id_str}")
            return

    if user1_id == user2_id:
        await message.reply("❌ User1 and User2 cannot be the same!")
        return

    user1_name = await _resolve_display_name(bot, user1_id)
    user2_name = await _resolve_display_name(bot, user2_id)

    user1_name_safe = html.escape(user1_name)
    user2_name_safe = html.escape(user2_name)

    default_mask = "11111"
    keyboard = _build_transfer_keyboard(user1_id, user2_id, default_mask)

    await message.reply(
        f"⚠️ <b>ACCOUNT TRANSFER CONFIRMATION</b>\n\n"
        f"📤 <b>From:</b> {user1_name_safe} (ID: {user1_id})\n"
        f"📥 <b>To:</b> {user2_name_safe} (ID: {user2_id})\n\n"
        f"Toggle what to transfer, then press Confirm.\n"
        f"<b>Merge</b> = destination keeps its rows; source rows are added.\n"
        f"<b>Replace</b> = destination's rows are wiped; source rows take over.\n"
        f"Family is replace-only (no merge possible).\n\n"
        f"If <b>all</b> are Yes, the source account is marked transferred "
        f"to the destination and its row stays for history lookups.",
        reply_markup=keyboard,
    )


async def _resolve_display_name(bot: Bot, user_id: int) -> str:
    """Resolve a user's first name via Telegram, falling back to DB."""
    try:
        user_obj = await bot.get_users(user_id)
        if user_obj.first_name:
            return user_obj.first_name
    except Exception:
        pass

    db_user = await db.get_user(user_id)
    if db_user and db_user.first_name:
        return db_user.first_name

    return f"User {user_id}"


def _build_transfer_keyboard(
    user1_id: int, user2_id: int, mask: str
) -> InlineKeyboardMarkup:
    """Inline keyboard for account-transfer toggles + confirm/cancel.

    `mask` is a 5-char string of '0'/'1' in order:
      M=Money, F=Family, R=fRiends, I=Inventory, O=Other.
    Inventory and Money are merge; Family and Other are replace.
    """
    m, f, r, i, o = mask[0], mask[1], mask[2], mask[3], mask[4]
    yes_money = "✅ Yes (Merge)" if m == "1" else "❌ No"
    yes_family = "✅ Yes (Replace)" if f == "1" else "❌ No"
    yes_friends = "✅ Yes (Merge)" if r == "1" else "❌ No"
    yes_inventory = "✅ Yes (Merge)" if i == "1" else "❌ No"
    yes_other = "✅ Yes (Replace)" if o == "1" else "❌ No"

    rows = [
        [
            InlineKeyboardButton(
                text=f"💰 Money: {yes_money}",
                callback_data=f"acctf:tg:M:{user1_id}:{user2_id}:{mask}",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"👨‍👩‍👧 Family: {yes_family}",
                callback_data=f"acctf:tg:F:{user1_id}:{user2_id}:{mask}",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🤝 Friends: {yes_friends}",
                callback_data=f"acctf:tg:R:{user1_id}:{user2_id}:{mask}",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"📦 Inventory: {yes_inventory}",
                callback_data=f"acctf:tg:I:{user1_id}:{user2_id}:{mask}",
            )
        ],
        [
            InlineKeyboardButton(
                text=f"🗂️ Other (factory, jobs, garden…): {yes_other}",
                callback_data=f"acctf:tg:O:{user1_id}:{user2_id}:{mask}",
            )
        ],
        [
            InlineKeyboardButton(
                text="✅ Confirm",
                callback_data=f"acctf:cf:{user1_id}:{user2_id}:{mask}",
            ),
            InlineKeyboardButton(
                text="❌ Cancel",
                callback_data=f"acctf:xx:{user1_id}:{user2_id}",
            ),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@client.on_callback_query(filters.regex(r"^acctf:tg:"))
async def transfer_toggle_callback(callback: CallbackQuery, config: Config):
    """Flip one toggle bit and redraw the keyboard."""
    if not is_admin(config, callback.from_user.id):
        await callback.answer("⛔ Admins only!", show_alert=True)
        return

    parts = callback.data.split(":")
    slot = parts[2]
    user1_id = int(parts[3])
    user2_id = int(parts[4])
    mask = parts[5]

    slot_index = {"M": 0, "F": 1, "R": 2, "I": 3, "O": 4}.get(slot)
    if slot_index is None or len(mask) != 5:
        await callback.answer("Invalid toggle payload.", show_alert=True)
        return

    bits = list(mask)
    bits[slot_index] = "0" if bits[slot_index] == "1" else "1"
    new_mask = "".join(bits)

    keyboard = _build_transfer_keyboard(user1_id, user2_id, new_mask)
    try:
        await callback.message.edit_reply_markup(reply_markup=keyboard)
    except Exception:
        pass
    await callback.answer()


@client.on_callback_query(filters.regex(r"^acctf:cf:"))
async def transfer_confirm_callback(callback: CallbackQuery, config: Config):
    """Execute the transfer with the chosen category mask."""
    if not is_admin(config, callback.from_user.id):
        await callback.answer("⛔ Admins only!", show_alert=True)
        return

    parts = callback.data.split(":")
    user1_id = int(parts[2])
    user2_id = int(parts[3])
    mask = parts[4]

    if user1_id == user2_id:
        await callback.answer(
            "❌ Source and destination IDs must differ.", show_alert=True
        )
        return
    if len(mask) != 5:
        await callback.answer("Invalid mask.", show_alert=True)
        return

    transfer_money = mask[0] == "1"
    transfer_family = mask[1] == "1"
    transfer_friends = mask[2] == "1"
    transfer_inventory = mask[3] == "1"
    transfer_other = mask[4] == "1"
    all_yes = (
        transfer_money
        and transfer_family
        and transfer_friends
        and transfer_inventory
        and transfer_other
    )

    await callback.message.edit_text("🔄 Transferring account data...")
    await callback.answer()

    try:
        await db.transfer_account(
            user1_id,
            user2_id,
            transfer_money=transfer_money,
            transfer_family=transfer_family,
            transfer_friends=transfer_friends,
            transfer_inventory=transfer_inventory,
            transfer_other=transfer_other,
        )

        merge_label = lambda flag: (
            "🔀 merged onto destination" if flag else "🚫 kept on source"
        )
        replace_label = lambda flag: (
            "♻️ replaced destination" if flag else "🚫 kept on source"
        )
        summary_lines = [
            f"💰 Money: {merge_label(transfer_money)}",
            f"👨‍👩‍👧 Family: {replace_label(transfer_family)}",
            f"🤝 Friends: {merge_label(transfer_friends)}",
            f"📦 Inventory: {merge_label(transfer_inventory)}",
            f"🗂️ Other data: {replace_label(transfer_other)}",
        ]
        footer = (
            f"\n📒 Source account ({user1_id}) marked transferred → {user2_id}."
            if all_yes
            else f"\nℹ️ Source account ({user1_id}) stays independent (one or more categories kept)."
        )
        summary = "\n".join(summary_lines)
        await callback.message.edit_text(
            f"✅ <b>Account Transfer Complete</b>\n\n"
            f"📤 From: <code>{user1_id}</code>\n"
            f"📥 To: <code>{user2_id}</code>\n\n"
            f"{summary}"
            f"{footer}"
        )
    except Exception as e:
        logger.exception(
            "Account transfer failed: from_user_id=%s to_user_id=%s",
            user1_id,
            user2_id,
        )
        error_text = html.escape(str(e))[:500]
        await callback.message.edit_text(
            f"❌ <b>Transfer failed.</b>\n\n<code>{error_text}</code>"
        )


@client.on_callback_query(filters.regex(r"^acctf:xx:"))
async def transfer_cancel_callback(callback: CallbackQuery, config: Config):
    """Cancel a pending account transfer."""
    if not is_admin(config, callback.from_user.id):
        await callback.answer("⛔ Admins only!", show_alert=True)
        return
    await callback.message.edit_text("❌ Account transfer cancelled.")
    await callback.answer()


def _parse_target_user_id(message: Message) -> Optional[int]:
    """Resolve a target user id from reply or first command arg.

    Accepts any numeric id; does not require the user to exist in Telegram or
    the bot's database (used by /block which may target unseen users).
    """
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user.id
    parts = message.text.split() if message.text else []
    if len(parts) > 1:
        token = parts[1].lstrip("@")
        if token.isdigit():
            return int(token)
    return None


@client.on_message(filters.command(["block"]))
async def block_user_command(message: Message, config: Config):
    """Block a user from using the bot (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    target_id = _parse_target_user_id(message)
    if target_id is None:
        await message.reply(
            "Usage: <code>/block &lt;user_id&gt; [reason]</code> or reply to a user."
        )
        return

    if target_id == config.owner_id:
        await message.reply("❌ Refusing to block the owner.")
        return

    # Extract reason: everything after the user_id token (or all args if reply-based)
    parts = message.text.split() if message.text else []
    if message.reply_to_message and message.reply_to_message.from_user:
        # /block [reason...] — args start at index 1
        reason = " ".join(parts[1:]) or None
    else:
        # /block <id> [reason...] — args start at index 2
        reason = " ".join(parts[2:]) or None

    added = await db.block_user(target_id, reason=reason)
    reason_line = f"\nReason: <i>{html.escape(reason)}</i>" if reason else ""
    if added:
        await message.reply(
            f"✅ Blocked user <code>{target_id}</code>.{reason_line}"
        )
    else:
        await message.reply(
            f"ℹ️ User <code>{target_id}</code> was already blocked."
        )


@client.on_message(filters.command(["unblock"]))
async def unblock_user_command(message: Message, config: Config):
    """Unblock a user (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    target_id = _parse_target_user_id(message)
    if target_id is None:
        await message.reply(
            "Usage: <code>/unblock &lt;user_id&gt;</code> or reply to a user."
        )
        return

    removed = await db.unblock_user(target_id)
    if removed:
        await message.reply(f"✅ Unblocked user <code>{target_id}</code>.")
    else:
        await message.reply(f"ℹ️ User <code>{target_id}</code> was not blocked.")


@client.on_message(filters.command(["unblock_ft"]))
async def unblock_ft_command(message: Message, config: Config):
    """Remove a user's /ft ban (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    target_id = _parse_target_user_id(message)
    if target_id is None:
        await message.reply(
            "Usage: <code>/unblock_ft &lt;user_id&gt;</code> or reply to a user."
        )
        return

    ban = await db.get_fertilize_ban(target_id)
    if ban is None:
        await message.reply(
            f"ℹ️ User <code>{target_id}</code> has no active /ft ban."
        )
        return

    from datetime import datetime as dt

    await db.set_fertilize_ban(
        target_id, dt.now(), fertilize_count=0, reason="admin_unblock"
    )
    await message.reply(f"✅ /ft ban lifted for user <code>{target_id}</code>.")


@client.on_message(filters.command(["unblock_ft_receive"]))
async def unblock_ft_receive_command(message: Message, config: Config):
    """Remove a user's /ft receive ban (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    target_id = _parse_target_user_id(message)
    if target_id is None:
        await message.reply(
            "Usage: <code>/unblock_ft_receive &lt;user_id&gt;</code> or reply to a user."
        )
        return

    ban = await db.get_fertilize_receive_ban(target_id)
    if ban is None:
        await message.reply(
            f"ℹ️ User <code>{target_id}</code> has no active /ft receive ban."
        )
        return

    from datetime import datetime as dt

    await db.set_fertilize_receive_ban(
        target_id, dt.now(), fertilize_count=0, reason="admin_unblock"
    )
    await message.reply(
        f"✅ /ft receive ban lifted for user <code>{target_id}</code>."
    )


@client.on_message(filters.command(["fileid"]))
async def fileid_command(message: Message, config: Config):
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    replied = message.reply_to_message
    if not replied:
        await message.reply(
            "Reply to a document, video, photo, audio, sticker, "
            "animation, or voice message with /fileid."
        )
        return

    candidates = (
        ("video", getattr(replied, "video", None)),
        ("document", getattr(replied, "document", None)),
        ("photo", getattr(replied, "photo", None)),
        ("animation", getattr(replied, "animation", None)),
        ("audio", getattr(replied, "audio", None)),
        ("voice", getattr(replied, "voice", None)),
        ("sticker", getattr(replied, "sticker", None)),
        ("video_note", getattr(replied, "video_note", None)),
    )
    lines = []
    for label, media in candidates:
        if media is None:
            continue
        fid = getattr(media, "file_id", None)
        fuid = getattr(media, "file_unique_id", None)
        if fid:
            lines.append(
                f"<b>{label}</b>\nfile_id: <code>{html.escape(fid)}</code>"
                + (
                    f"\nfile_unique_id: <code>{html.escape(fuid)}</code>"
                    if fuid
                    else ""
                )
            )
    if not lines:
        await message.reply(
            "❌ The replied message has no media with a file_id."
        )
        return

    await message.reply("\n\n".join(lines))


@client.on_message(filters.command(["resetwork"]))
async def reset_work_cooldown_command(message: Message, config: Config):
    """Reset work cooldown for a specific user or all recent workers (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    args = message.text.split()[1:]

    if not args:
        # Reset everyone who worked in the last hour
        result = await db.fetch(
            "WITH d AS (DELETE FROM work_cooldowns "
            "WHERE last_work_at > NOW() - INTERVAL '1 hour' RETURNING user_id) "
            "SELECT COUNT(*) AS n FROM d"
        )
        count = result[0]["n"] if result else 0
        await message.reply(
            f"✅ Reset work cooldown for <b>{count}</b> user(s) who worked in the last hour."
        )
        return

    # Resolve target user
    target_id: Optional[int] = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target_id = message.reply_to_message.from_user.id
    else:
        raw = args[0].lstrip("@")
        if raw.isdigit():
            target_id = int(raw)
        else:
            try:
                user_obj = await message._client.get_users(f"@{raw}")
                target_id = user_obj.id
            except Exception:
                pass

    if not target_id:
        await message.reply(
            "❌ Could not resolve user. Reply to their message or pass their ID/username."
        )
        return

    await db.execute("DELETE FROM work_cooldowns WHERE user_id = $1", target_id)
    await message.reply(
        f"✅ Work cooldown reset for user <code>{target_id}</code>."
    )


@client.on_message(filters.command(["auto_restart"]))
async def auto_restart_command(message: Message, config: Config):
    """Restart the bot process via sys.exit(0) — Docker restarts the container (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("⛔ This command is for admins only.")
        return

    await message.reply("♻️ Restarting bot...")
    try:
        with open("/tmp/restart", "w") as f:
            f.write(f"{message.chat.id}:{message.id}")
    except Exception:
        pass
    sys.exit(0)


@client.on_message(filters.command(["ftme"]))
async def ftme_command(message: Message, config: Config):
    """Admin only: fully grow own crops (DM only, for testing)."""
    if message.chat.type != ChatType.PRIVATE:
        return
    if not is_admin(config, message.from_user.id):
        return

    user_id = message.from_user.id
    garden = await db.get_garden(user_id)
    if not garden:
        return

    plots = await db.get_garden_plots(garden["id"])
    growing = [p for p in plots if p["crop_type"] and not p["is_ready"]]
    if not growing:
        await message.reply("❌ No growing plants.")
        return

    await db.mark_plots_ready(garden["id"], [p["position"] for p in growing])
    await message.reply(f"✅ Fully grew {len(growing)} plants.")
