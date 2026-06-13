"""Profile management commands."""

import base64
import logging

from pyrogram import Client as Bot
from pyrogram import filters
from pyrogram.enums import MessageMediaType
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.client import client
from bot.command_registry import reg
from bot.constants import (
    PETS,
    _pet_emoji,
    get_crop_display_emoji,
    get_item_display_name,
    parse_item_and_qty,
    resolve_item_key,
)
from bot.database import Database, db
from bot.input_file import to_input_file
from bot.plugins.family import get_target_user, reply_cannot_target_bot
from bot.queue_it import queue_it
from bot.utils import fetch_telegram_profile_photo, parse_money_amount

logger = logging.getLogger(__name__)

reg("setpic", "🖼️ Set profile picture")


@client.on_message(filters.command(["setpic"]))
async def set_profile_pic(
    message: Message,
    bot: Bot,
):
    """
    Set profile picture by replying to an image, or show current pic with option
    to update from Telegram profile photo.
    """
    user = message.from_user
    logger.info(
        "setpic called by %s (chat=%s, reply=%s)",
        user.id,
        message.chat.id,
        message.reply_to_message_id,
    )

    # Ensure user exists in database
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    reply = message.reply_to_message
    if not reply and message.reply_to_message_id:
        try:
            reply = await bot.get_messages(
                message.chat.id, message.reply_to_message_id
            )
        except Exception:
            pass
    logger.info(
        "setpic reply_to_message: %s, media: %s",
        reply,
        getattr(reply, "media", None),
    )
    if not reply:
        await message.reply(
            "ℹ️ Reply to an image or sticker with /setpic to set your profile picture."
        )
        return

    media_type = reply.media

    if not media_type:
        await message.reply("Replied message is not media message")
        return

    if media_type and media_type not in [
        MessageMediaType.PHOTO,
        MessageMediaType.STICKER,
    ]:
        await message.reply(
            f"❌ Please reply to a photo or sticker to set it as your profile picture. (current you replied to media with type: {media_type}, support for document image or video frame is not added yet)"
        )
        return

    # Check if replying to a photo - set that as profile pic
    # Kurigram's Photo object has a .sizes array; extract the largest Thumbnail
    if reply.photo:
        photo = reply.photo
    elif reply.sticker and reply.sticker.thumbs:
        photo = reply.sticker
    else:
        photo = None
    if photo:
        status_msg = await message.reply("📸 Setting your profile picture...")

        try:
            # Get the largest photo size (last in the list)
            file_id = photo.file_id

            # Download the image
            image_data = await bot.download_media(file_id, in_memory=True)

            if not image_data:
                await queue_it(
                    lambda: status_msg.edit_text(
                        "❌ Failed to download image."
                    ),
                    status_msg.chat,
                )
                return

            # Read all bytes from the download result
            if hasattr(image_data, "getvalue"):
                image_bytes = image_data.getvalue()
            elif hasattr(image_data, "read"):
                image_bytes = image_data.read()
            else:
                image_bytes = image_data

            # Convert to base64
            b64_data = base64.b64encode(image_bytes).decode("utf-8")

            # Save to database (both file_id and base64)
            await db.set_profile_pic(user.id, file_id=file_id, b64=b64_data)

            # Delete status message
            await status_msg.delete()

            # Reply with the image to show confirmation
            await message.reply_photo(
                photo=to_input_file(image_bytes, filename="profile.jpg"),
                caption="✅ <b>Your profile picture has been set to this!</b>",
            )

        except Exception as e:
            err = str(e)
            await queue_it(
                lambda: status_msg.edit_text(
                    f"❌ Failed to set profile picture: {err}"
                ),
                status_msg.chat,
            )
        return

    # No reply to photo - show current pic and offer to use Telegram pfp
    db_user = await db.get_user(user.id)
    has_pic = db_user and db_user.get("profile_pic_b64")

    # Check if they have a Telegram profile photo
    tg_photo = await fetch_telegram_profile_photo(bot, user.id)

    if has_pic:
        # Show current profile pic
        try:
            image_bytes = base64.b64decode(db_user["profile_pic_b64"])

            keyboard = None
            if tg_photo:
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔄 Use current Telegram profile photo",
                                callback_data="setpic:telegram",
                            )
                        ]
                    ]
                )

            await message.reply_photo(
                photo=to_input_file(image_bytes, filename="profile.jpg"),
                caption="📸 <b>Your current profile picture</b>\n\n"
                "Reply to any image with /setpic to change it.",
                reply_markup=keyboard,
            )
        except Exception:
            await message.reply(
                "ℹ️ Reply to an image with /setpic to set your profile picture."
            )
    else:
        # No pic set
        if tg_photo:
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Use my Telegram profile photo",
                            callback_data="setpic:telegram",
                        )
                    ]
                ]
            )
            await message.reply(
                "📸 You haven't set a profile picture yet.\n\n"
                "Reply to an image with /setpic, or use your current Telegram profile photo:",
                reply_markup=keyboard,
            )
        else:
            await message.reply(
                "ℹ️ Reply to an image with /setpic to set your profile picture."
            )


@client.on_message(filters.command(["transactions"]))
async def transactions(
    message: Message,
):
    """View recent transactions."""
    user = message.from_user

    txns = await db.get_transactions(user.id, limit=10)

    if not txns:
        await message.reply("📭 No transactions yet.")
        return

    lines = ["💳 <b>Recent Transactions</b>\n"]
    for txn in txns:
        sign = "+" if txn["amount"] > 0 else ""
        emoji = "💵" if txn["amount"] > 0 else "💸"
        lines.append(f"{emoji} {sign}${txn['amount']:,} - {txn['reason']}")

    await message.reply("\n".join(lines))


@client.on_message(filters.command(["me"]))
async def me_profile(
    message: Message,
):
    """View your complete profile."""
    user = message.from_user

    # Ensure user exists
    await db.upsert_user(user.id, user.username, user.first_name)

    # Get wallet
    wallet = await db.get_wallet(user.id)

    # Get family info
    db_user = await db.get_user(user.id)

    # Get friend count
    friends = await db.get_friends(user.id)
    friend_count = len(friends)

    # Get factory info
    factory = await db.get_factory(user.id)
    factories = [factory] if factory else []
    factory_count = len(factories)

    # Get garden info
    garden = await db.get_garden(user.id)

    # Get inventory counts
    inventory = await db.get_inventory(user.id)
    seed_count = sum(
        i["quantity"] for i in inventory if i["item_type"] == "seed"
    )
    harvest_count = sum(
        i["quantity"] for i in inventory if i["item_type"] == "harvest"
    )

    # Get machine count
    machines = await db.get_user_machines(user.id)
    machine_count = len(machines)

    # Get achievements count
    achievements = await db.get_achievements(user.id)
    achievement_count = len(achievements) if achievements else 0

    # Get pets
    pets = await db.get_pets(user.id)

    # Build profile text with blockquotes
    text = f"<b>{user.first_name or 'User'}'s Profile</b>\n"
    text += "━━━━━━━━━━━━━━━━\n"
    if pets:
        from datetime import datetime

        pet_parts = []
        for pet in pets:
            pet_info = PETS[pet["pet_type"]]
            updated = pet.get("happiness_updated_at")
            if updated:
                elapsed_days = (
                    datetime.utcnow() - updated
                ).total_seconds() / 86400
                happiness = max(0, pet["happiness"] - int(elapsed_days * 40))
            else:
                happiness = pet["happiness"]
            mood = (
                "😞"
                if happiness < 26
                else "😐"
                if happiness < 51
                else "😊"
                if happiness < 76
                else "😄"
            )
            display = (
                pet["pet_name"] if pet.get("pet_name") else pet_info["name"]
            )
            pet_parts.append(
                f"{_pet_emoji(pet['pet_type'])} {display} Lv.{pet['level']} {mood}"
            )
        text += "  ".join(pet_parts) + "\n"
    text += "\n"

    # Basic info in blockquote
    text += "<blockquote><b>📋 Info</b>\n"
    text += f"🆔 ID: <code>{user.id}</code>\n"
    if user.username:
        text += f"📛 Username: @{user.username}\n"
    gender = (
        db_user["gender"].capitalize()
        if db_user and db_user.get("gender")
        else "Not set"
    )
    text += f"⚤ Gender: {gender}</blockquote>\n\n"

    # Wallet in blockquote
    text += "<blockquote><b>💰 Wallet</b>\n"
    text += f"💵 Balance: ${wallet['balance']:,}\n"
    text += f"📈 Total Earned: ${wallet['total_earned']:,}</blockquote>\n\n"

    # Social in blockquote
    text += "<blockquote><b>👥 Social</b>\n"
    text += f"👫 Friends: {friend_count}\n"

    # Family info
    if db_user:
        partner_id = db_user.get("partner_id")
        if partner_id:
            partner = await db.get_user(partner_id)
            partner_name = partner["first_name"] if partner else "Unknown"
            text += f"💕 Married to: {partner_name}\n"

        # Count children (use family_relationships table)
        children = await db.get_children(user.id)
        child_count = len(children)
        if child_count > 0:
            text += f"👶 Children: {child_count}\n"

        # Current job info
        job = await db.get_job(user.id)
        if job:
            text += f"💼 Job: {job['job_type']} (Lv.{job['job_level']}, {job['job_xp']} XP)\n"
    text += "</blockquote>\n\n"

    # Economy in blockquote
    text += "<blockquote><b>🏭 Economy</b>\n"
    text += f"🏭 Factories: {factory_count}\n"
    if factories:
        for f in factories:
            text += f"  • {f['name']} ({f['capacity']} slots, ${f['total_earnings']:,})\n"
    garden_size = f"{garden['size']}×{garden['size']}" if garden else "None"
    text += f"🌻 Garden: {garden_size}\n"
    text += f"⚙️ Machines: {machine_count}\n"
    text += (
        f"🌱 Seeds: {seed_count} | 🌾 Harvest: {harvest_count}</blockquote>\n\n"
    )

    # Achievements in blockquote
    text += "<blockquote><b>🏆 Progress</b>\n"
    text += f"⭐ Achievements: {achievement_count}</blockquote>\n"

    # Commands hint
    text += (
        "\n<i>💡 Use /balance, /transactions, /garden, /factory, /friends</i>"
    )

    await message.reply(text)


reg("gender", "⚤ Set your gender (max 20 chars)")


GENDER_MAX_LEN = 20


@client.on_message(filters.command(["gender"]))
async def gender_command(message: Message):
    """Set your gender — free text, max 20 characters."""
    import html as _html

    user = message.from_user
    parts = (message.text or "").split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await message.reply(
            "❌ Usage: <code>/gender &lt;your gender&gt;</code>\n"
            "Examples: /gender male, /gender female, /gender non-binary\n"
            f"Max {GENDER_MAX_LEN} characters."
        )
        return
    gender = parts[1].strip()
    if len(gender) > GENDER_MAX_LEN:
        await message.reply(
            f"❌ Gender too long ({len(gender)} chars). "
            f"Max {GENDER_MAX_LEN} characters allowed."
        )
        return

    await db.upsert_user(user.id, user.username, user.first_name)
    await db.set_gender(user.id, gender)
    await message.reply(f"✅ Gender set to: <b>{_html.escape(gender)}</b>")


def parse_amount(amount_str: str, max_amount: int = None) -> int | None:
    """
    Parse amount string with special formats:
    - "a" or "all" -> returns max_amount
    - "5+3" -> 5000 (5 * 10^3)
    - "1+5" -> 100000 (1 * 10^5)
    - Regular numbers
    """
    return parse_money_amount(amount_str, max_amount)


async def get_target_user_from_message(
    bot, message: Message, db: Database = None
):
    """Extract target user from message (reply, mention, or user ID).

    Returns a TargetUser. Bot-reply propagates with is_bot=True. Otherwise
    falls back to DB lookup before giving up.
    """
    from bot.plugins.family import TargetUser

    result = await get_target_user(bot, message, db)
    if result.is_bot or result.user:
        return result

    # If not found via bot API, try to resolve from DB directly
    # This handles cases where user changed username but hasn't interacted with bot
    if message.text:
        parts = message.text.split()
        for part in parts[1:]:  # Skip command
            clean = part.lstrip("@")
            if clean.isdigit():
                # Try user ID from DB
                user = await db.get_user(int(clean))
                if user:
                    from pyrogram.types import User

                    return TargetUser(
                        user=User(
                            id=user["user_id"],
                            is_bot=False,
                            first_name=user["first_name"] or "Unknown",
                            username=user["username"],
                        )
                    )
            else:
                # Try username from DB
                user = await db.get_user_by_username(clean)
                if user:
                    from pyrogram.types import User

                    return TargetUser(
                        user=User(
                            id=user["user_id"],
                            is_bot=False,
                            first_name=user["first_name"] or "Unknown",
                            username=user["username"],
                        )
                    )

    return TargetUser(user=None)


@client.on_message(filters.command(["transfer"]))
async def transfer_command(
    message: Message,
    bot: Bot,
):
    """Transfer money to another user. Usage: /transfer <amount> @user"""
    user = message.from_user
    args = message.text.split()[1:] if message.text else []

    if len(args) < 1:
        await message.reply(
            "💸 <b>Transfer Money</b>\n\n"
            "Usage: <code>/transfer &lt;amount&gt;</code> (reply to user)\n"
            "       <code>/transfer &lt;amount&gt; @username</code>\n"
            "       <code>/transfer &lt;amount&gt; user_id</code>\n\n"
            "Amount formats:\n"
            "• <code>1000</code> - transfer $1,000\n"
            "• <code>5+3</code> - transfer $5,000 (5 × 10³)\n"
            "• <code>a</code> or <code>all</code> - transfer entire balance\n\n"
            "<i>Note: You can add any text after the amount, e.g. /transfer 1000 @user for dinner</i>"
        )
        return

    # Ensure user exists
    await db.upsert_user(user.id, user.username, user.first_name)

    # Get wallet balance
    wallet = await db.get_wallet(user.id)
    balance = wallet["balance"]

    # Parse amount
    amount = parse_amount(args[0], balance)
    if amount is None or amount <= 0:
        await message.reply("❌ Invalid amount!")
        return

    if amount > balance:
        await message.reply(f"❌ Insufficient funds! You have ${balance:,}")
        return

    # Get target user - allow extra text after amount
    target, is_bot = await get_target_user_from_message(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if not target:
        await message.reply(
            "❌ Reply to a user, mention @username, or provide user_id to transfer to them."
        )
        return

    if target.id == user.id:
        await message.reply("❌ You can't transfer to yourself!")
        return

    # Ensure target exists
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Honor receiver's transfer-block list
    if await db.is_transfer_blocked(target.id, user.id):
        await message.reply(
            f"🚫 {target.first_name} has blocked transfers from you."
        )
        return

    # Perform transfer
    await db.add_balance(user.id, -amount, f"Transfer to {target.first_name}")
    await db.add_balance(target.id, amount, f"Transfer from {user.first_name}")

    await message.reply(
        f"✅ <b>Transfer Complete!</b>\n\n"
        f"💸 Sent ${amount:,} to {target.first_name}\n"
        f"💰 Your new balance: ${balance - amount:,}"
    )


reg("banktransfer", "🏦 Transfer money to user's bank")


@client.on_message(filters.command(["banktransfer"]))
async def banktransfer_command(
    message: Message,
    bot: Bot,
):
    """Transfer money from your wallet to another user's bank account."""
    user = message.from_user
    args = message.text.split()[1:] if message.text else []

    if len(args) < 1:
        await message.reply(
            "🏦 <b>Bank Transfer</b>\n\n"
            "Transfer money from your wallet to someone's bank account.\n\n"
            "Usage: <code>/banktransfer &lt;amount&gt;</code> (reply to user)\n"
            "       <code>/banktransfer &lt;amount&gt; @username</code>\n"
            "       <code>/banktransfer &lt;amount&gt; user_id</code>\n\n"
            "Amount formats:\n"
            "• <code>1000</code> - transfer $1,000\n"
            "• <code>5+3</code> - transfer $5,000 (5 × 10³)\n"
            "• <code>a</code> or <code>all</code> - transfer entire balance\n\n"
            "<i>Note: You can add any text after the amount, e.g. /banktransfer 1000 @user for rent</i>"
        )
        return

    # Ensure user exists
    await db.upsert_user(user.id, user.username, user.first_name)

    # Get wallet balance
    wallet = await db.get_wallet(user.id)
    balance = wallet["balance"]

    # Parse amount
    amount = parse_amount(args[0], balance)
    if amount is None or amount <= 0:
        await message.reply("❌ Invalid amount!")
        return

    if amount > balance:
        await message.reply(f"❌ Insufficient funds! You have ${balance:,}")
        return

    # Get target user - allow extra text after amount
    target, is_bot = await get_target_user_from_message(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if not target:
        await message.reply(
            "❌ Reply to a user, mention @username, or provide user_id to transfer to them."
        )
        return

    if target.id == user.id:
        await message.reply("❌ You can't transfer to yourself!")
        return

    # Ensure target exists
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Honor receiver's transfer-block list
    if await db.is_transfer_blocked(target.id, user.id):
        await message.reply(
            f"🚫 {target.first_name} has blocked transfers from you."
        )
        return

    # Ensure target has a bank account
    await db.get_bank_account(target.id)

    # Perform transfer: deduct from wallet, add to bank
    await db.add_balance(
        user.id, -amount, f"Bank transfer to {target.first_name}"
    )
    await db.add_bank_balance(target.id, amount)

    await message.reply(
        f"🏦 <b>Bank Transfer Complete!</b>\n\n"
        f"💸 Sent ${amount:,} to {target.first_name}'s bank\n"
        f"💰 Your new balance: ${balance - amount:,}"
    )


reg("block_transfer", "🚫 Block someone from transferring money to you")


@client.on_message(filters.command(["block_transfer"]))
async def block_transfer_command(message: Message, bot: Bot):
    """Block a specific user from sending /transfer or /banktransfer to you."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    target, is_bot = await get_target_user_from_message(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if not target:
        await message.reply(
            "🚫 <b>Block Transfer</b>\n\n"
            "Stop someone from sending you money (wallet or bank).\n\n"
            "Usage: reply to their message, mention @user, or use user_id."
        )
        return
    if target.id == user.id:
        await message.reply("❌ You can't block yourself!")
        return

    target = await db.upsert_user(target.id, target.username, target.first_name)
    added = await db.add_transfer_block(user.id, target.id)
    if added:
        await message.reply(
            f"🚫 Blocked. {target.first_name} can no longer transfer "
            f"money to you. Use /unblock_transfer to undo."
        )
    else:
        await message.reply(
            f"ℹ️ {target.first_name} is already blocked from transferring to you."
        )


reg("unblock_transfer", "🔓 Allow someone to transfer money to you again")


@client.on_message(filters.command(["unblock_transfer"]))
async def unblock_transfer_command(message: Message, bot: Bot):
    """Remove a transfer block for a specific user."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    target, is_bot = await get_target_user_from_message(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if not target:
        await message.reply(
            "🔓 <b>Unblock Transfer</b>\n\n"
            "Re-allow a user to send you money.\n\n"
            "Usage: reply to their message, mention @user, or use user_id."
        )
        return
    if target.id == user.id:
        await message.reply("❌ Self-unblock has no effect.")
        return

    target = await db.upsert_user(target.id, target.username, target.first_name)
    removed = await db.remove_transfer_block(user.id, target.id)
    if removed:
        await message.reply(
            f"🔓 Unblocked. {target.first_name} can now transfer money to you again."
        )
    else:
        await message.reply(
            f"ℹ️ {target.first_name} wasn't blocked from transferring to you."
        )


@client.on_message(filters.command(["gift"]))
async def gift_command(
    message: Message,
    bot: Bot,
):
    """Gift inventory item to another user. Usage: /gift <item> <quantity> @user"""
    user = message.from_user
    args = message.text.split()[1:] if message.text else []

    if len(args) < 1:
        # Show inventory and usage
        inventory = await db.get_inventory(user.id)

        text = "🎁 <b>Gift Items</b>\n\n"
        text += "Usage: <code>/gift &lt;item&gt; [quantity]</code> (reply to user)\n"
        text += (
            "       <code>/gift &lt;item&gt; [quantity] @username</code>\n\n"
        )

        if inventory:
            text += "<b>Your giftable items:</b>\n"
            for item in inventory:
                if item["quantity"] > 0:
                    text += f"  • {item['item_name']} ({item['item_type']}): {item['quantity']}\n"
        else:
            text += "<i>No items in inventory</i>"

        await message.reply(text)
        return

    # Ensure user exists
    await db.upsert_user(user.id, user.username, user.first_name)

    raw_name, quantity = parse_item_and_qty(args)
    item_name = resolve_item_key(raw_name) or raw_name.strip().lower().replace(
        " ", "_"
    )

    if quantity <= 0:
        await message.reply("❌ Invalid quantity!")
        return

    # Get target user
    target, is_bot = await get_target_user_from_message(bot, message)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if not target:
        await message.reply(
            "❌ Reply to a user or mention @username to gift to them."
        )
        return

    if target.id == user.id:
        await message.reply("❌ You can't gift to yourself!")
        return

    # Check inventory for item
    inventory = await db.get_inventory(user.id)
    found_item = None
    for item in inventory:
        if item["item_name"].lower() == item_name:
            found_item = item
            break

    if not found_item:
        await message.reply(f"❌ You don't have any {item_name}!")
        return

    if found_item["quantity"] < quantity:
        await message.reply(
            f"❌ Not enough {item_name}! You have {found_item['quantity']}"
        )
        return

    # Ensure target exists
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Perform gift — lands in recipient's gift_inventory, not regular inventory
    await db.remove_inventory_item(
        user.id, found_item["item_type"], item_name, quantity
    )
    await db.add_gift_inventory_item(
        target.id, found_item["item_type"], item_name, quantity
    )

    item_emoji = get_crop_display_emoji(item_name)
    display = get_item_display_name(item_name)
    await message.reply(
        f"🎁 <b>Gift Sent!</b>\n\n"
        f"Gave {quantity}x {item_emoji} <b>{display}</b> to {target.first_name}\n"
        f"<i>They can use /gift_inv to view and /withdraw_gift to use it.</i>"
    )


reg("gift_inv", "🎁 View items received as gifts [/gift_inventory]")
reg("withdraw_gift", "📤 Withdraw gift items into your regular inventory")


@client.on_message(filters.command(["gift_inv", "gift_inventory"]))
async def gift_inventory_command(message: Message):
    """View items received as gifts."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    gift_items = await db.get_gift_inventory(user.id)

    if not gift_items:
        await message.reply(
            "🎁 <b>Gift Inventory</b>\n\n"
            "<i>No gift items. Items sent to you via /gift appear here.</i>"
        )
        return

    lines = []
    for item in gift_items:
        if item["quantity"] > 0:
            emoji = get_crop_display_emoji(item["item_name"])
            name = get_item_display_name(item["item_name"])
            lines.append(f"  • {emoji} <b>{name}</b>: {item['quantity']}")

    text = (
        "🎁 <b>Gift Inventory</b>\n\n"
        + "\n".join(lines)
        + "\n\n<i>To use these items, withdraw them first:\n"
        "<code>/withdraw_gift &lt;item&gt; &lt;amount&gt;</code></i>"
    )
    await message.reply(text)


@client.on_message(filters.command(["withdraw_gift"]))
async def withdraw_gift_command(message: Message):
    """Withdraw items from gift inventory into regular inventory."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    args = message.text.split()[1:] if message.text else []
    if len(args) < 1:
        await message.reply(
            "Usage: <code>/withdraw_gift &lt;item&gt; [amount]</code>\n"
            "Use /gift_inv to see your gift items."
        )
        return

    raw_name, quantity = parse_item_and_qty(args)
    item_name = resolve_item_key(raw_name) or raw_name.strip().lower().replace(
        " ", "_"
    )

    if quantity <= 0:
        await message.reply("❌ Invalid quantity.")
        return

    # Find the item in gift inventory
    gift_items = await db.get_gift_inventory(user.id)
    found = next(
        (i for i in gift_items if i["item_name"].lower() == item_name), None
    )

    if not found:
        await message.reply(
            f"❌ No <b>{item_name}</b> in your gift inventory.\n"
            "Use /gift_inv to see what you have."
        )
        return

    actual_qty = min(quantity, found["quantity"])

    removed = await db.remove_gift_inventory_item(
        user.id, found["item_type"], item_name, actual_qty
    )
    if not removed:
        await message.reply("❌ Could not withdraw — quantity mismatch.")
        return

    await db.add_inventory_item(
        user.id, found["item_type"], item_name, actual_qty
    )

    emoji = get_crop_display_emoji(item_name)
    display = get_item_display_name(item_name)
    await message.reply(
        f"✅ Withdrew {actual_qty}x {emoji} <b>{display}</b> into your inventory."
    )


reg("bank", "🏦 View wallet and bank balance [/balance /wallet /acc /account]")


@client.on_message(
    filters.command(["bank", "balance", "wallet", "acc", "account"])
)
async def bank_command(
    message: Message,
    bot: Bot,
):
    """View bank account balance. Reply to someone or mention to see theirs."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if viewing another player's bank
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return
    if target and target.id != user.id:
        # Viewing someone else's bank
        target_wallet = await db.get_wallet(target.id)
        target_bank = await db.get_bank_account(target.id)
        target_name = target.first_name or target.username or "Unknown"

        await message.reply(
            f"🏦 <b>{target_name}'s Bank Account</b>\n\n"
            f"<blockquote>"
            f"💵 Wallet: ${target_wallet['balance']:,}\n"
            f"🏦 Bank: ${target_bank['balance']:,}\n"
            f"📊 Total: ${target_wallet['balance'] + target_bank['balance']:,}"
            f"</blockquote>"
        )
        return

    # Viewing own bank
    wallet = await db.get_wallet(user.id)
    bank = await db.get_bank_account(user.id)

    await message.reply(
        f"🏦 <b>Bank Account</b>\n\n"
        f"<blockquote>"
        f"💵 Wallet: ${wallet['balance']:,}\n"
        f"🏦 Bank: ${bank['balance']:,}\n"
        f"📊 Total: ${wallet['balance'] + bank['balance']:,}"
        f"</blockquote>\n\n"
        f"<i>Use /deposit or /withdraw to manage funds</i>"
    )


@client.on_message(filters.command(["deposit"]))
async def deposit_command(
    message: Message,
):
    """Deposit money to bank. Usage: /deposit <amount>"""
    user = message.from_user
    args = message.text.split()[1:] if message.text else []

    await db.upsert_user(user.id, user.username, user.first_name)
    wallet = await db.get_wallet(user.id)

    if len(args) < 1:
        await message.reply(
            "🏦 <b>Deposit to Bank</b>\n\n"
            "Usage: <code>/deposit &lt;amount&gt;</code>\n\n"
            "Amount formats:\n"
            "• <code>1000</code> - deposit $1,000\n"
            "• <code>5+3</code> - deposit $5,000 (5 × 10³)\n"
            "• <code>a</code> or <code>all</code> - deposit entire wallet\n\n"
            f"💵 Current wallet: ${wallet['balance']:,}"
        )
        return

    amount = parse_amount(args[0], wallet["balance"])
    if amount is None or amount <= 0:
        await message.reply("❌ Invalid amount!")
        return

    if amount > wallet["balance"]:
        await message.reply(
            f"❌ Insufficient funds! You have ${wallet['balance']:,}"
        )
        return

    success = await db.deposit_to_bank(user.id, amount)
    if success:
        bank = await db.get_bank_account(user.id)
        await message.reply(
            f"✅ <b>Deposit Successful!</b>\n\n"
            f"🏦 Deposited ${amount:,} to bank\n"
            f"💵 Wallet: ${wallet['balance'] - amount:,}\n"
            f"🏦 Bank: ${bank['balance']:,}"
        )
    else:
        await message.reply("❌ Deposit failed!")


reg("withdraw", "💸 Withdraw money from bank")


@client.on_message(filters.command(["withdraw"]))
async def withdraw_command(
    message: Message,
):
    """Withdraw money from bank. Usage: /withdraw <amount>"""
    user = message.from_user
    args = message.text.split()[1:] if message.text else []

    await db.upsert_user(user.id, user.username, user.first_name)
    bank = await db.get_bank_account(user.id)

    if len(args) < 1:
        await message.reply(
            "🏦 <b>Withdraw from Bank</b>\n\n"
            "Usage: <code>/withdraw &lt;amount&gt;</code>\n\n"
            "Amount formats:\n"
            "• <code>1000</code> - withdraw $1,000\n"
            "• <code>5+3</code> - withdraw $5,000 (5 × 10³)\n"
            "• <code>a</code> or <code>all</code> - withdraw entire bank\n\n"
            f"🏦 Current bank: ${bank['balance']:,}"
        )
        return

    amount = parse_amount(args[0], bank["balance"])
    if amount is None or amount <= 0:
        await message.reply("❌ Invalid amount!")
        return

    if amount > bank["balance"]:
        await message.reply(
            f"❌ Insufficient funds! You have ${bank['balance']:,} in bank"
        )
        return

    success = await db.withdraw_from_bank(user.id, amount)
    if success:
        wallet = await db.get_wallet(user.id)
        await message.reply(
            f"✅ <b>Withdrawal Successful!</b>\n\n"
            f"💵 Withdrew ${amount:,} from bank\n"
            f"💵 Wallet: ${wallet['balance']:,}\n"
            f"🏦 Bank: ${bank['balance'] - amount:,}"
        )
    else:
        await message.reply("❌ Withdrawal failed!")
