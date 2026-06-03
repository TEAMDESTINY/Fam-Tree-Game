"""Daily rewards and gem system commands."""

import random
from datetime import datetime, timezone

from pyrogram import Client as Bot
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.command_registry import reg
from bot.database import Database
from bot.utils import user_mention as util_user_mention
from pyrogram import filters
from bot.client import client
from bot.database import db


# Register commands (must be after router definition)


# Gem types with emoji and rarity weights (lower = rarer)
GEMS = {
    "ruby": {
        "emoji": "🔴",
        "name": "Ruby",
        "weight": 35,
        "fuse_reward": 50_000,
    },
    "emerald": {
        "emoji": "💚",
        "name": "Emerald",
        "weight": 30,
        "fuse_reward": 75_000,
    },
    "sapphire": {
        "emoji": "🔵",
        "name": "Sapphire",
        "weight": 20,
        "fuse_reward": 100_000,
    },
    "amethyst": {
        "emoji": "💜",
        "name": "Amethyst",
        "weight": 10,
        "fuse_reward": 150_000,
    },
    "diamond": {
        "emoji": "💎",
        "name": "Diamond",
        "weight": 5,
        "fuse_reward": 250_000,
    },
}

DAILY_REWARD_AMOUNT = 100_000


def get_random_gem() -> str:
    """Get a random gem type based on weights."""
    gems = list(GEMS.keys())
    weights = [GEMS[g]["weight"] for g in gems]
    return random.choices(gems, weights=weights, k=1)[0]


def format_gem(gem_type: str) -> str:
    """Format gem type with emoji."""
    gem = GEMS.get(gem_type)
    if gem:
        return f"{gem['emoji']} {gem['name']}"
    return gem_type


async def get_target_user(bot: Bot, message: Message):
    """Extract target user from reply or mention."""

    # Check if replying to someone
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        if not target.is_bot:
            return target
    return None


reg("daily", "🎁 Claim daily reward")


@client.on_message(filters.command(["daily"]))
async def daily_command(
    message: Message,
):
    """Claim daily reward (2k coins + random gem)."""
    user = message.from_user

    # Ensure user exists
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Check if can claim
    if not await db.can_claim_daily(user.id):
        # Get current gem
        current_gem = await db.get_user_gem(user.id)
        gem_text = ""
        if current_gem:
            gem_text = f"\n\n💎 Your current gem: {format_gem(current_gem)}\nUse /fuse (reply to someone) to fuse gems!"

        # Calculate time until next reset (midnight UTC)
        now = datetime.now(timezone.utc)
        tomorrow = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if now.hour >= 0:
            from datetime import timedelta

            tomorrow += timedelta(days=1)
        time_left = tomorrow - now
        hours, remainder = divmod(int(time_left.total_seconds()), 3600)
        minutes = remainder // 60

        await message.reply(
            f"⏰ <b>Already Claimed!</b>\n\n"
            f"You've already claimed your daily reward today.\n"
            f"⏳ Next reward in: <b>{hours}h {minutes}m</b>{gem_text}"
        )
        return

    # Check if they have an existing gem that will be overwritten
    old_gem = await db.get_user_gem(user.id)

    # Get random gem
    new_gem = get_random_gem()
    gem_info = GEMS[new_gem]

    # Claim reward
    await db.claim_daily_reward(user.id, new_gem)
    await db.add_balance(user.id, DAILY_REWARD_AMOUNT, "Daily reward")

    # Build response
    response = (
        f"🎁 <b>Daily Reward Claimed!</b>\n\n"
        f"💰 You received <b>${DAILY_REWARD_AMOUNT:,}</b>\n"
        f"💎 You got a <b>{gem_info['emoji']} {gem_info['name']}</b>!\n\n"
    )

    if old_gem and old_gem != new_gem:
        response += "🔄 Your previous gem was replaced with the new gem.\n\n"

    response += (
        "<i>Reply to someone with /fuse to fuse your gems!\n"
        "Both must have the same gem type.</i>"
    )

    await message.reply(response)


reg("gem", "💎 Check your gem")


@client.on_message(filters.command(["gem", "gems"]))
async def gem_command(
    message: Message,
):
    """Check your current gem."""
    user = message.from_user

    # Ensure user exists
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    current_gem = await db.get_user_gem(user.id)

    if not current_gem:
        await message.reply(
            "💎 <b>Your Gems</b>\n\n"
            "You don't have any gem right now.\n"
            "Use /daily to get one!"
        )
        return

    gem_info = GEMS.get(current_gem, {})

    await message.reply(
        f"💎 <b>Your Gems</b>\n\n"
        f"Current gem: <b>{gem_info.get('emoji', '💎')} {gem_info.get('name', current_gem)}</b>\n"
        f"Fuse reward: <b>${gem_info.get('fuse_reward', 0):,}</b>\n\n"
        f"<i>Reply to someone with /fuse to fuse your gems!</i>"
    )


reg("fuse", "✨ Fuse gems with someone")


@client.on_message(filters.command(["fuse"]))
async def fuse_command(
    message: Message,
    bot: Bot,
):
    """Fuse your gem with another player's gem."""
    user = message.from_user

    # Ensure user exists
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Check if user has a gem
    user_gem = await db.get_user_gem(user.id)
    if not user_gem:
        await message.reply(
            "❌ You don't have a gem to fuse!\nUse /daily to get one."
        )
        return

    # Get target user
    target = await get_target_user(bot, message)
    if not target:
        gem_info = GEMS.get(user_gem, {})
        await message.reply(
            f"💎 <b>Gem Fusion</b>\n\n"
            f"Your gem: <b>{gem_info.get('emoji', '💎')} {gem_info.get('name', user_gem)}</b>\n\n"
            f"Reply to someone with /fuse to request a gem fusion!\n"
            f"Both players must have the same gem type."
        )
        return

    if target.id == user.id:
        await message.reply("😅 You can't fuse with yourself!")
        return

    if target.is_bot:
        await message.reply("🤖 You can't fuse gems with a bot!")
        return

    # Ensure target exists in database
    await db.upsert_user(
        user_id=target.id,
        username=target.username,
        first_name=target.first_name,
    )

    # Check if target has the same gem
    target_gem = await db.get_user_gem(target.id)
    if not target_gem:
        await message.reply(
            f"❌ {target.first_name} doesn't have a gem!\n"
            f"They need to use /daily first."
        )
        return

    if target_gem != user_gem:
        user_gem_info = GEMS.get(user_gem, {})
        target_gem_info = GEMS.get(target_gem, {})
        await message.reply(
            f"❌ <b>Gem Mismatch!</b>\n\n"
            f"Your gem: {user_gem_info.get('emoji', '💎')} {user_gem_info.get('name', user_gem)}\n"
            f"{target.first_name}'s gem: {target_gem_info.get('emoji', '💎')} {target_gem_info.get('name', target_gem)}\n\n"
            f"Both players must have the same gem type to fuse!"
        )
        return

    # Create fuse request
    request = await db.create_gem_fuse_request(
        requester_id=user.id,
        target_id=target.id,
        gem_type=user_gem,
        chat_id=message.chat.id,
    )

    gem_info = GEMS.get(user_gem, {})
    user_link = util_user_mention(user)
    target_link = util_user_mention(target)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Fuse {gem_info.get('emoji', '💎')}",
                    callback_data=f"fuse_accept:{request['id']}",
                ),
                InlineKeyboardButton(
                    text="❌ Reject",
                    callback_data=f"fuse_reject:{request['id']}",
                ),
            ]
        ]
    )

    sent = await message.reply(
        f"💎 <b>Gem Fusion Request</b>\n\n"
        f"{user_link} wants to fuse their {gem_info.get('emoji', '💎')} {gem_info.get('name', user_gem)} "
        f"with {target_link}!\n\n"
        f"🎁 Reward: <b>${gem_info.get('fuse_reward', 0):,}</b> each\n\n"
        f"{target_link}, do you accept?",
        reply_markup=keyboard,
    )

    # Update request with message_id
    await db.execute(
        "UPDATE gem_fuse_requests SET message_id = $1 WHERE id = $2",
        sent.id,
        request["id"],
    )


reg("gemlist", "📋 List all gem types")


@client.on_message(filters.command(["gemlist", "geminfo"]))
async def gemlist_command(message: Message):
    """Show all gem types and their rewards."""
    lines = ["💎 <b>Gem Types</b>\n"]

    # Sort by rarity (rarest first)
    sorted_gems = sorted(GEMS.items(), key=lambda x: x[1]["weight"])

    for gem_type, info in sorted_gems:
        rarity = (
            "Common"
            if info["weight"] >= 30
            else "Uncommon"
            if info["weight"] >= 15
            else "Rare"
            if info["weight"] >= 8
            else "Epic"
            if info["weight"] >= 4
            else "Legendary"
        )
        lines.append(
            f"{info['emoji']} <b>{info['name']}</b> ({rarity})\n"
            f"   └ Fuse reward: ${info['fuse_reward']:,}"
        )

    lines.append("\n<i>Get gems from /daily rewards!</i>")

    await message.reply("\n".join(lines))
