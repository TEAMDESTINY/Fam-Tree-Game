"""Fishing mini-game system."""

from bot.queue_it import queue_it

import random
from collections import Counter

from pyrogram.errors import BadRequest
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.command_registry import reg
from bot.constants import FISH_TYPES
from bot.database import Database
from pyrogram import filters
from bot.client import client
from bot.database import db


# Daily fishing limit
DAILY_FISH_LIMIT = 10000


# ── Helpers ──────────────────────────────────────────────────────────────


async def safe_callback_answer(
    callback: CallbackQuery, text: str = None, show_alert: bool = False
):
    """Best-effort callback answer (ignore stale callback query errors)."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except BadRequest:
        pass


def get_random_fish() -> tuple[str, dict]:
    """Get a random fish based on rarity weights."""
    total_weight = sum(info["rarity"] for info in FISH_TYPES.values())
    roll = random.uniform(0, total_weight)

    cumulative = 0
    for fish_name, info in FISH_TYPES.items():
        cumulative += info["rarity"]
        if roll <= cumulative:
            return fish_name, info

    # Fallback to first fish
    first = list(FISH_TYPES.items())[0]
    return first[0], first[1]


def format_price(amount: int) -> str:
    """Format price with $ and commas."""
    return f"${amount:,}"


def rarity_label(rarity: int) -> str:
    """Return a rarity label."""
    if rarity == 0.5:
        return "🔮 MYTHICAL!"
    if rarity <= 5:
        return "🌟 LEGENDARY!"
    if rarity <= 10:
        return "⭐ Rare!"
    if rarity <= 25:
        return "Uncommon"
    if rarity <= 40:
        return "Common"
    return "Very Common"


def rarity_label_upper(rarity: int) -> str:
    """Return an uppercase rarity label for bulk results."""
    if rarity == 0.5:
        return "🔮 MYTHICAL"
    if rarity <= 5:
        return "🌟 LEGENDARY"
    if rarity <= 10:
        return "⭐ RARE"
    if rarity <= 25:
        return "Uncommon"
    if rarity <= 40:
        return "Common"
    return "VERY COMMON"


async def check_callback_ownership(callback: CallbackQuery) -> bool:
    """
    Check if the callback belongs to the user who initiated it.
    Returns True if ownership is valid, False otherwise (and answers the callback).
    """
    parts = callback.data.split(":")
    owner_id = int(parts[-1]) if len(parts) > 2 else None
    if owner_id and callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback, "❌ This is not your fishing session!", show_alert=True
        )
        return False
    return True


async def safe_edit_message(message, text, reply_markup=None):
    """Safely edit a message, ignoring errors if the message hasn't changed."""
    try:
        await queue_it(
            lambda: message.edit_text(text, reply_markup=reply_markup),
            message.chat,
        )
    except BadRequest:
        pass


def build_fishing_keyboard(
    user_id: int, single_callback: str = "fish:show", remaining: int = None
) -> InlineKeyboardMarkup:
    """Build the standard fishing session keyboard."""
    # If remaining is provided, cap bulk buttons to remaining
    rows = [
        [
            InlineKeyboardButton(
                text="🎣 Fish", callback_data=f"{single_callback}:{user_id}"
            ),
        ],
    ]

    # x30 button (cap to remaining)
    x30_text = "🎣 Fish x30"
    x30_cb = "fish:30"
    if remaining is not None and remaining < 30:
        x30_text = f"🎣 Fish x{remaining}"
        x30_cb = f"fish:{remaining}"
    elif remaining is not None and remaining == 0:
        x30_text = None  # Skip this button

    # x100 button
    x100_text = "🎣 Fish x100"
    x100_cb = "fish:100"
    if remaining is not None and remaining < 100:
        if remaining < 30:
            x100_text = None  # Already covered by x30
        else:
            x100_text = f"🎣 Fish x{remaining}"
            x100_cb = f"fish:{remaining}"
    elif remaining is not None and remaining == 0:
        x100_text = None

    # x300 button
    x300_text = "🎣 Fish x300"
    x300_cb = "fish:300"
    if remaining is not None and remaining < 300:
        if remaining < 100:
            x300_text = None  # Already covered
        else:
            x300_text = f"🎣 Fish x{remaining}"
            x300_cb = f"fish:{remaining}"
    elif remaining is not None and remaining == 0:
        x300_text = None

    # Build rows with available buttons
    bulk_row1 = []
    if x30_text:
        bulk_row1.append(
            InlineKeyboardButton(
                text=x30_text, callback_data=f"{x30_cb}:{user_id}"
            )
        )
    if x100_text:
        bulk_row1.append(
            InlineKeyboardButton(
                text=x100_text, callback_data=f"{x100_cb}:{user_id}"
            )
        )
    if bulk_row1:
        rows.append(bulk_row1)

    if x300_text:
        rows.append([
            InlineKeyboardButton(
                text=x300_text, callback_data=f"{x300_cb}:{user_id}"
            )
        ])

    # Fish All button (uses all remaining bait, capped by daily limit)
    if remaining is None or remaining > 0:
        rows.append([
            InlineKeyboardButton(
                text="🎣 Fish All", callback_data=f"fish:all:{user_id}"
            ),
        ])

    # Aquarium at the end
    rows.append([
        InlineKeyboardButton(
            text="🐟 Aquarium", callback_data=f"fish:aquarium:{user_id}"
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_fishing_stats_text(stats: dict) -> str:
    """Build the bait + total caught stats line."""
    return f"🪣 Bait left: {stats['bait_count']}\n📈 Total caught: {stats['total_caught']}"


async def get_bait_count(stats: dict) -> int:
    """Get bait count from stats, handling legacy field names."""
    return stats.get("bait", stats.get("bait_count", 0))


async def check_fishing_achievements_for_catches(
    db: Database, user_id: int, catches: dict[str, int]
):
    """Check fishing achievements after new catches."""
    from bot.achievements import check_fishing_achievements

    caught_fish = "kraken" if catches.get("kraken", 0) > 0 else None
    await check_fishing_achievements(db, user_id, caught_fish=caught_fish)


async def _bulk_fish(times: int) -> dict:
    """
    Bulk fish using weighted distribution instead of individual random calls.
    Returns dict of {fish_name: count}.
    Uses numpy-style weighted sampling for speed.
    """
    from bot.constants import FISH_TYPES

    fish_names = list(FISH_TYPES.keys())
    weights = [FISH_TYPES[f]["rarity"] for f in fish_names]
    total_weight = sum(weights)

    # Use weighted distribution - for large batches, use multinomial approximation
    if times > 50:
        # For large batches, use expected values with small random variation
        catches = Counter()
        for fish_name, weight in zip(fish_names, weights):
            expected = times * (weight / total_weight)
            # Add small random variation (±20%)
            actual = max(0, int(expected + random.gauss(0, expected * 0.1)))
            catches[fish_name] = actual

        # Adjust to match exact total
        total_caught = sum(catches.values())
        diff = times - total_caught
        if diff > 0:
            # Add missing to random fish
            for _ in range(diff):
                fish = random.choices(fish_names, weights=weights, k=1)[0]
                catches[fish] += 1
        elif diff < 0:
            # Remove excess from random fish
            for _ in range(abs(diff)):
                available = [f for f in catches if catches[f] > 0]
                if available:
                    fish = random.choice(available)
                    catches[fish] -= 1

        return dict(catches)
    else:
        # For small batches, use individual random calls (more accurate)
        catches = Counter()
        for _ in range(times):
            roll = random.uniform(0, total_weight)
            cumulative = 0
            for fish_name, weight in zip(fish_names, weights):
                cumulative += weight
                if roll <= cumulative:
                    catches[fish_name] += 1
                    break
        return dict(catches)


# ── Commands ─────────────────────────────────────────────────────────────

reg("fish", "🎣 Go fishing")


@client.on_message(filters.command(["fish"]))
async def fish_command(
    message: Message,
):
    """Go fishing! Uses 1 bait."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    stats = await db.get_fishing_stats(user.id)
    daily_caught = await db.get_daily_fish_count(user.id)
    remaining = DAILY_FISH_LIMIT - daily_caught

    text = build_fishing_stats_text(stats)
    if daily_caught >= DAILY_FISH_LIMIT:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        text += f"\n\n⚠️ You've reached the daily limit of {DAILY_FISH_LIMIT:,} fish!\n"
        text += f"🔄 Resets in {h}h {m}m {s}s"
        keyboard = build_fishing_keyboard(
            user.id, single_callback="fish:1", remaining=0
        )
    else:
        if daily_caught > 0:
            text += f"\n📊 Today: {daily_caught:,}/{DAILY_FISH_LIMIT:,} fish"
        keyboard = build_fishing_keyboard(
            user.id, single_callback="fish:1", remaining=remaining
        )

    await message.reply(text, reply_markup=keyboard)


@client.on_callback_query(filters.regex(r"^" + "fish:show:"))
async def fish_show(
    callback: CallbackQuery,
):
    """Show fishing session UI."""
    if not await check_callback_ownership(callback):
        return

    user = callback.from_user
    stats = await db.get_fishing_stats(user.id)
    daily_caught = await db.get_daily_fish_count(user.id)
    remaining = DAILY_FISH_LIMIT - daily_caught

    text = build_fishing_stats_text(stats)
    if daily_caught >= DAILY_FISH_LIMIT:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        text += f"\n\n⚠️ You've reached the daily limit of {DAILY_FISH_LIMIT:,} fish!\n"
        text += f"🔄 Resets in {h}h {m}m {s}s"
        keyboard = build_fishing_keyboard(
            user.id, single_callback="fish:1", remaining=0
        )
    else:
        if daily_caught > 0:
            text += f"\n📊 Today: {daily_caught:,}/{DAILY_FISH_LIMIT:,} fish"
        keyboard = build_fishing_keyboard(
            user.id, single_callback="fish:1", remaining=remaining
        )

    await safe_edit_message(callback.message, text, keyboard)
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "fish:1:"))
async def fish_again_callback(
    callback: CallbackQuery,
):
    """Fish again via callback."""
    if not await check_callback_ownership(callback):
        return

    user = callback.from_user
    stats = await db.get_fishing_stats(user.id)
    daily_caught = await db.get_daily_fish_count(user.id)

    if daily_caught >= DAILY_FISH_LIMIT:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        await safe_callback_answer(
            callback,
            f"Daily limit reached! Resets in {h}h {m}m {s}s",
            show_alert=True,
        )
        return

    if await get_bait_count(stats) <= 0:
        await safe_callback_answer(
            callback, "No bait left! Use /bait to buy more.", show_alert=True
        )
        return

    # Use 1 bait
    await db.use_bait(user.id)
    await db.increment_daily_fish_count(user.id, 1)

    # Get random fish
    fish_name, fish_info = get_random_fish()

    # Add to inventory
    await db.add_fish(user.id, fish_name, 1)
    await db.add_fishing_stat(user.id, "total_caught", 1)
    await check_fishing_achievements_for_catches(db, user.id, {fish_name: 1})

    # Get updated stats
    new_stats = await db.get_fishing_stats(user.id)
    new_daily = daily_caught + 1

    text = f"🎣 <b>You caught a {fish_info['emoji']} {fish_name}!</b>\n\n"
    text += f"📊 Rarity: {rarity_label(fish_info['rarity'])}\n"
    text += f"💰 Value: {format_price(fish_info['sell_price'])}\n"
    text += build_fishing_stats_text(new_stats)
    text += f"\n📊 Today: {new_daily:,}/{DAILY_FISH_LIMIT:,} fish"

    if fish_info["rarity"] <= 5:
        text += "\n\n🎉 <b>WOW! What a catch!</b>"

    remaining = DAILY_FISH_LIMIT - new_daily
    keyboard = build_fishing_keyboard(user.id, remaining=remaining)

    await safe_edit_message(callback.message, text, keyboard)
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "fish:30:"))
async def fish_30_callback(
    callback: CallbackQuery,
):
    """Fish x30 with optimized batch processing."""
    if not await check_callback_ownership(callback):
        return

    user = callback.from_user
    stats = await db.get_fishing_stats(user.id)
    bait_count = await get_bait_count(stats)
    daily_caught = await db.get_daily_fish_count(user.id)
    remaining = DAILY_FISH_LIMIT - daily_caught

    if remaining <= 0:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        await safe_callback_answer(
            callback,
            f"Daily limit reached! Resets in {h}h {m}m {s}s",
            show_alert=True,
        )
        return

    if bait_count <= 0:
        await safe_callback_answer(
            callback, "No bait left! Use /bait to buy more.", show_alert=True
        )
        return

    # Cap to remaining bait and daily limit
    times = min(30, bait_count, remaining)
    catches = await _bulk_fish(times)

    # Update database in batch
    await db.use_bait_bulk(user.id, times)
    for fish_name, count in catches.items():
        await db.add_fish(user.id, fish_name, count)
    await db.add_fishing_stat(user.id, "total_caught", times)
    await db.increment_daily_fish_count(user.id, times)
    await check_fishing_achievements_for_catches(db, user.id, catches)

    # Get updated stats
    new_stats = await db.get_fishing_stats(user.id)
    new_daily = daily_caught + times

    # Build result message
    text = f"🎣 <b>Fished {times} times!</b>\n\n"
    text += "<blockquote><b>Catches:</b>\n"
    total_worth_summary = 0
    for fish_name, count in catches.items():
        info = FISH_TYPES[fish_name]
        sell_price = format_price(info["sell_price"])
        total_value = format_price(info["sell_price"] * count)
        total_worth_summary += info["sell_price"] * count
        text += f"{info['emoji']} {fish_name} x{count} [{rarity_label_upper(info['rarity'])}] [{sell_price}x{count} = {total_value}]\n"
    text += "</blockquote>\n\n"
    text += f"💰 Total worth: {format_price(total_worth_summary)}\n\n"
    text += build_fishing_stats_text(new_stats)
    text += f"\n📊 Today: {new_daily:,}/{DAILY_FISH_LIMIT:,} fish"

    if new_daily >= DAILY_FISH_LIMIT:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        text += f"\n\n⚠️ Daily limit reached! Resets in {h}h {m}m {s}s"

    new_remaining = DAILY_FISH_LIMIT - new_daily
    keyboard = build_fishing_keyboard(
        user.id, single_callback="fish:1", remaining=new_remaining
    )

    await safe_edit_message(callback.message, text, keyboard)
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "fish:100:"))
async def fish_100_callback(
    callback: CallbackQuery,
):
    """Fish x100 with optimized batch processing."""
    if not await check_callback_ownership(callback):
        return

    user = callback.from_user
    stats = await db.get_fishing_stats(user.id)
    bait_count = await get_bait_count(stats)
    daily_caught = await db.get_daily_fish_count(user.id)
    remaining = DAILY_FISH_LIMIT - daily_caught

    if remaining <= 0:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        await safe_callback_answer(
            callback,
            f"Daily limit reached! Resets in {h}h {m}m {s}s",
            show_alert=True,
        )
        return

    if bait_count <= 0:
        await safe_callback_answer(
            callback, "No bait left! Use /bait to buy more.", show_alert=True
        )
        return

    times = min(100, bait_count, remaining)
    catches = await _bulk_fish(times)

    # Update database in batch
    await db.use_bait_bulk(user.id, times)
    for fish_name, count in catches.items():
        await db.add_fish(user.id, fish_name, count)
    await db.add_fishing_stat(user.id, "total_caught", times)
    await db.increment_daily_fish_count(user.id, times)
    await check_fishing_achievements_for_catches(db, user.id, catches)

    # Get updated stats
    new_stats = await db.get_fishing_stats(user.id)
    new_daily = daily_caught + times

    # Build result message
    text = f"🎣 <b>Fished {times} times!</b>\n\n"
    text += "<blockquote><b>Catches:</b>\n"
    total_worth_summary = 0
    for fish_name, count in catches.items():
        info = FISH_TYPES[fish_name]
        sell_price = format_price(info["sell_price"])
        total_value = format_price(info["sell_price"] * count)
        total_worth_summary += info["sell_price"] * count
        text += f"{info['emoji']} {fish_name} x{count} [{rarity_label_upper(info['rarity'])}] [{sell_price}x{count} = {total_value}]\n"
    text += "</blockquote>\n\n"
    text += f"💰 Total worth: {format_price(total_worth_summary)}\n\n"
    text += build_fishing_stats_text(new_stats)
    text += f"\n📊 Today: {new_daily:,}/{DAILY_FISH_LIMIT:,} fish"

    if new_daily >= DAILY_FISH_LIMIT:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        text += f"\n\n⚠️ Daily limit reached! Resets in {h}h {m}m {s}s"

    new_remaining = DAILY_FISH_LIMIT - new_daily
    keyboard = build_fishing_keyboard(
        user.id, single_callback="fish:1", remaining=new_remaining
    )

    await safe_edit_message(callback.message, text, keyboard)
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "fish:300:"))
async def fish_300_callback(
    callback: CallbackQuery,
):
    """Fish x300 with optimized batch processing."""
    if not await check_callback_ownership(callback):
        return

    user = callback.from_user
    stats = await db.get_fishing_stats(user.id)
    bait_count = await get_bait_count(stats)
    daily_caught = await db.get_daily_fish_count(user.id)
    remaining = DAILY_FISH_LIMIT - daily_caught

    if remaining <= 0:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        await safe_callback_answer(
            callback,
            f"Daily limit reached! Resets in {h}h {m}m {s}s",
            show_alert=True,
        )
        return

    if bait_count <= 0:
        await safe_callback_answer(
            callback, "No bait left! Use /bait to buy more.", show_alert=True
        )
        return

    times = min(300, bait_count, remaining)
    catches = await _bulk_fish(times)

    # Update database in batch
    await db.use_bait_bulk(user.id, times)
    for fish_name, count in catches.items():
        await db.add_fish(user.id, fish_name, count)
    await db.add_fishing_stat(user.id, "total_caught", times)
    await db.increment_daily_fish_count(user.id, times)
    await check_fishing_achievements_for_catches(db, user.id, catches)

    # Get updated stats
    new_stats = await db.get_fishing_stats(user.id)
    new_daily = daily_caught + times

    # Build result message
    text = f"🎣 <b>Fished {times} times!</b>\n\n"
    text += "<blockquote><b>Catches:</b>\n"
    total_worth_summary = 0
    for fish_name, count in catches.items():
        info = FISH_TYPES[fish_name]
        sell_price = format_price(info["sell_price"])
        total_value = format_price(info["sell_price"] * count)
        total_worth_summary += info["sell_price"] * count
        text += f"{info['emoji']} {fish_name} x{count} [{rarity_label_upper(info['rarity'])}] [{sell_price}x{count} = {total_value}]\n"
    text += "</blockquote>\n\n"
    text += f"💰 Total worth: {format_price(total_worth_summary)}\n\n"
    text += build_fishing_stats_text(new_stats)
    text += f"\n📊 Today: {new_daily:,}/{DAILY_FISH_LIMIT:,} fish"

    if new_daily >= DAILY_FISH_LIMIT:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        text += f"\n\n⚠️ Daily limit reached! Resets in {h}h {m}m {s}s"

    new_remaining = DAILY_FISH_LIMIT - new_daily
    keyboard = build_fishing_keyboard(
        user.id, single_callback="fish:1", remaining=new_remaining
    )

    await safe_edit_message(callback.message, text, keyboard)
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "fish:all:"))
async def fish_all_callback(
    callback: CallbackQuery,
):
    """Fish using all available bait (capped by daily limit)."""
    if not await check_callback_ownership(callback):
        return

    user = callback.from_user
    stats = await db.get_fishing_stats(user.id)
    bait_count = await get_bait_count(stats)
    daily_caught = await db.get_daily_fish_count(user.id)
    remaining = DAILY_FISH_LIMIT - daily_caught

    if remaining <= 0:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        await safe_callback_answer(
            callback,
            f"Daily limit reached! Resets in {h}h {m}m {s}s",
            show_alert=True,
        )
        return

    if bait_count <= 0:
        await safe_callback_answer(
            callback, "No bait left! Use /bait to buy more.", show_alert=True
        )
        return

    # Use all bait, capped by daily limit
    times = min(bait_count, remaining)
    catches = await _bulk_fish(times)

    # Update database in batch
    await db.use_bait_bulk(user.id, times)
    for fish_name, count in catches.items():
        await db.add_fish(user.id, fish_name, count)
    await db.add_fishing_stat(user.id, "total_caught", times)
    await db.increment_daily_fish_count(user.id, times)
    await check_fishing_achievements_for_catches(db, user.id, catches)

    # Get updated stats
    new_stats = await db.get_fishing_stats(user.id)
    new_daily = daily_caught + times

    # Build result message
    text = f"🎣 <b>Fished {times} times!</b>\n\n"
    text += "<blockquote><b>Catches:</b>\n"
    total_worth_summary = 0
    for fish_name, count in catches.items():
        info = FISH_TYPES[fish_name]
        sell_price = format_price(info["sell_price"])
        total_value = format_price(info["sell_price"] * count)
        total_worth_summary += info["sell_price"] * count
        text += f"{info['emoji']} {fish_name} x{count} [{rarity_label_upper(info['rarity'])}] [{sell_price}x{count} = {total_value}]\n"
    text += "</blockquote>\n\n"
    text += f"💰 Total worth: {format_price(total_worth_summary)}\n\n"
    text += build_fishing_stats_text(new_stats)
    text += f"\n📊 Today: {new_daily:,}/{DAILY_FISH_LIMIT:,} fish"

    if new_daily >= DAILY_FISH_LIMIT:
        secs = await db.get_utc_midnight_seconds_remaining()
        h, m, s = secs // 3600, (secs % 3600) // 60, secs % 60
        text += f"\n\n⚠️ Daily limit reached! Resets in {h}h {m}m {s}s"

    new_remaining = DAILY_FISH_LIMIT - new_daily
    keyboard = build_fishing_keyboard(
        user.id, single_callback="fish:1", remaining=new_remaining
    )

    await safe_edit_message(callback.message, text, keyboard)
    await safe_callback_answer(callback)


reg("bait", "🪱 Buy fishing bait")


@client.on_message(filters.command(["bait"]))
async def bait_command(
    message: Message,
):
    """Buy bait for fishing."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    args = message.text.split()[1:] if message.text else []

    bait_cost = 20  # $20 per bait

    if not args:
        # Show bait shop
        stats = await db.get_fishing_stats(user.id)
        wallet = await db.get_wallet(user.id)

        text = "🪱 <b>Bait Shop</b>\n\n"
        text += f"🪣 Your bait: {stats['bait_count']}\n"
        text += f"💰 Balance: {format_price(wallet['balance'])}\n\n"
        text += f"Price: {format_price(bait_cost)} per bait\n\n"
        text += "Usage: <code>/bait &lt;amount&gt;</code>\n"
        text += "Example: <code>/bait 10</code>"

        # Quick buy buttons
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(text="Buy 5", callback_data="bait:5"),
                    InlineKeyboardButton(
                        text="Buy 10", callback_data="bait:10"
                    ),
                    InlineKeyboardButton(
                        text="Buy 25", callback_data="bait:25"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="Buy 50", callback_data="bait:50"
                    ),
                    InlineKeyboardButton(
                        text="Buy 100", callback_data="bait:100"
                    ),
                ],
            ]
        )

        await message.reply(text, reply_markup=keyboard)
        return

    # Buy specific amount
    try:
        amount = int(args[0])
        if amount <= 0:
            await message.reply("❌ Amount must be positive!")
            return
    except ValueError:
        await message.reply("❌ Invalid amount!")
        return

    total_cost = bait_cost * amount

    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < total_cost:
        await message.reply(
            f"❌ Need {format_price(total_cost)} for {amount} bait!\n"
            f"You have: {format_price(wallet['balance'])}"
        )
        return

    # Process purchase
    await db.add_balance(user.id, -total_cost, f"Bought {amount} bait")
    await db.add_bait(user.id, amount)

    stats = await db.get_fishing_stats(user.id)

    await message.reply(
        f"✅ Bought {amount} bait for {format_price(total_cost)}!\n"
        f"🪣 Total bait: {stats['bait_count']}"
    )


@client.on_callback_query(filters.regex(r"^" + "bait:"))
async def bait_callback(
    callback: CallbackQuery,
):
    """Buy bait via callback."""
    user = callback.from_user

    parts = callback.data.split(":")
    amount = int(parts[1])
    bait_cost = 20
    total_cost = bait_cost * amount

    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < total_cost:
        await safe_callback_answer(
            callback, f"Need {format_price(total_cost)}!", show_alert=True
        )
        return

    # Process purchase
    await db.add_balance(user.id, -total_cost, f"Bought {amount} bait")
    await db.add_bait(user.id, amount)

    stats = await db.get_fishing_stats(user.id)
    new_wallet = await db.get_wallet(user.id)

    text = "🪱 <b>Bait Shop</b>\n\n"
    text += f"✅ Bought {amount} bait for {format_price(total_cost)}!\n\n"
    text += f"🪣 Your bait: {stats['bait_count']}\n"
    text += f"💰 Balance: {format_price(new_wallet['balance'])}\n\n"
    text += f"Price: {format_price(bait_cost)} per bait"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="Buy 5", callback_data="bait:5"),
                InlineKeyboardButton(text="Buy 10", callback_data="bait:10"),
                InlineKeyboardButton(text="Buy 25", callback_data="bait:25"),
            ],
            [
                InlineKeyboardButton(text="Buy 50", callback_data="bait:50"),
                InlineKeyboardButton(text="Buy 100", callback_data="bait:100"),
            ],
            [
                InlineKeyboardButton(
                    text="🎣 Go Fishing", callback_data=f"fish:show:{user.id}"
                ),
            ],
        ]
    )

    try:
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=keyboard),
            callback.message.chat,
        )
    except Exception:
        pass
    await safe_callback_answer(callback, f"✅ Bought {amount} bait!")


reg("aquarium", "🐟 View fish collection")


@client.on_message(filters.command(["aquarium"]))
async def aquarium_command(
    message: Message,
):
    """View your fish collection."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    await _show_aquarium(message, db, user)


async def _show_aquarium(message, db, user, edit=False):
    """Show aquarium with fish selection buttons."""
    inventory = await db.get_fish_inventory(user.id)
    stats = await db.get_fishing_stats(user.id)

    text = f"🐟 <b>{user.first_name}'s Aquarium</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    if not inventory:
        text += "<i>Your aquarium is empty!</i>\n"
        text += "Use /fish to catch some fish!"
    else:
        total_value = 0
        for fish_name, quantity in inventory.items():
            if quantity > 0 and fish_name in FISH_TYPES:
                info = FISH_TYPES[fish_name]
                value = info["sell_price"] * quantity
                total_value += value
                text += f"{info['emoji']} <b>{fish_name}</b>: {quantity} ({format_price(value)})\n"

        text += f"\n💰 Total Value: {format_price(total_value)}"

    text += "\n\n📊 <b>Stats</b>\n"
    text += f"🎣 Total Caught: {stats['total_caught']}\n"
    text += f"🪣 Bait: {stats['bait_count']}"

    # Build keyboard with fish selection buttons
    keyboard = await _build_aquarium_keyboard(user.id, inventory)

    if edit:
        return await queue_it(
            lambda: message.edit_text(text, reply_markup=keyboard), message.chat
        )
    await message.reply(text, reply_markup=keyboard)


async def _build_aquarium_keyboard(user_id, inventory):
    """Build aquarium keyboard with fish selection and sell buttons."""
    buttons = []

    # Add fish selection buttons
    fish_buttons = []
    for fish_name, quantity in sorted(inventory.items()):
        if quantity > 0 and fish_name in FISH_TYPES:
            info = FISH_TYPES[fish_name]
            fish_buttons.append(
                InlineKeyboardButton(
                    text=f"Sell {info['emoji']} {fish_name} ({quantity})",
                    callback_data=f"fish:sellfish:{fish_name}",
                )
            )
            if len(fish_buttons) == 2:
                buttons.append(fish_buttons)
                fish_buttons = []
    if fish_buttons:
        buttons.append(fish_buttons)

    # Sell All and Fish Again buttons
    buttons.append([
        InlineKeyboardButton(
            text="💰 Sell All", callback_data=f"fish:sellall:{user_id}"
        ),
        InlineKeyboardButton(
            text="🎣 Go Fishing", callback_data=f"fish:show:{user_id}"
        ),
    ])

    return InlineKeyboardMarkup(inline_keyboard=buttons)


@client.on_callback_query(filters.regex(r"^" + "fish:aquarium:"))
async def aquarium_callback(
    callback: CallbackQuery,
):
    """View aquarium via callback."""
    if not await check_callback_ownership(callback):
        return

    user = callback.from_user
    await _show_aquarium(callback.message, db, user, edit=True)
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "fish:sellfish:"))
async def sell_fish_select_callback(
    callback: CallbackQuery,
):
    """Show quantity selection for selling specific fish."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await safe_callback_answer(callback, "Invalid action")
        return

    fish_name = parts[2]
    user = callback.from_user

    if fish_name not in FISH_TYPES:
        await safe_callback_answer(callback, "Invalid fish!", show_alert=True)
        return

    info = FISH_TYPES[fish_name]
    inventory = await db.get_fish_inventory(user.id)
    quantity = inventory.get(fish_name, 0)

    if quantity <= 0:
        await safe_callback_answer(
            callback, "You don't have this fish!", show_alert=True
        )
        return

    text = f"💰 <b>Sell {info['emoji']} {fish_name}</b>\n\n"
    text += f"You have: {quantity}\n"
    text += f"Price: {format_price(info['sell_price'])} each\n"
    text += "Select quantity:\n"

    # Quantity buttons
    buttons = []
    qty_options = [1, 3, 5, 10, 20, 50]
    row = []
    for qty in qty_options:
        if qty <= quantity:
            row.append(
                InlineKeyboardButton(
                    text=f"×{qty} = {format_price(info['sell_price'] * qty)}",
                    callback_data=f"fish:sellconfirm:{fish_name}:{qty}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)

    # Max button
    if quantity not in qty_options:
        buttons.append([
            InlineKeyboardButton(
                text=f"Max ({quantity}) = {format_price(info['sell_price'] * quantity)}",
                callback_data=f"fish:sellconfirm:{fish_name}:{quantity}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(
            text="« Back", callback_data=f"fish:aquarium:{user.id}"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "fish:sellconfirm:"))
async def sell_fish_confirm_callback(
    callback: CallbackQuery,
):
    """Confirm selling specific fish quantity."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await safe_callback_answer(callback, "Invalid action")
        return

    fish_name = parts[2]
    quantity = int(parts[3])
    user = callback.from_user

    if fish_name not in FISH_TYPES:
        await safe_callback_answer(callback, "Invalid fish!", show_alert=True)
        return

    info = FISH_TYPES[fish_name]
    inventory = await db.get_fish_inventory(user.id)
    have = inventory.get(fish_name, 0)

    if have < quantity:
        await safe_callback_answer(
            callback, "Not enough fish!", show_alert=True
        )
        return

    # Remove from inventory and add balance
    await db.remove_fish(user.id, fish_name, quantity)
    total_earned = info["sell_price"] * quantity
    await db.add_balance(user.id, total_earned, f"Sold {fish_name}")
    await db.add_fishing_stat(user.id, "total_sold", 1)

    text = f"💰 <b>Sold {quantity}x {info['emoji']} {fish_name}!</b>\n\n"
    text += f"Earned: {format_price(total_earned)}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🐟 Aquarium", callback_data=f"fish:aquarium:{user.id}"
                ),
                InlineKeyboardButton(
                    text="🎣 Go Fishing", callback_data=f"fish:show:{user.id}"
                ),
            ]
        ]
    )
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "fish:sellall:"))
async def sell_all_fish_callback(
    callback: CallbackQuery,
):
    """Sell all fish."""
    if not await check_callback_ownership(callback):
        return

    user = callback.from_user
    inventory = await db.get_fish_inventory(user.id)

    if not inventory:
        await safe_callback_answer(
            callback, "No fish to sell!", show_alert=True
        )
        return

    total_earned = 0
    fish_sold = []

    for fish_name, quantity in inventory.items():
        if quantity > 0 and fish_name in FISH_TYPES:
            info = FISH_TYPES[fish_name]
            price = info["sell_price"] * quantity
            total_earned += price
            fish_sold.append(f"{info['emoji']} {quantity}x {fish_name}")

            # Remove from inventory
            await db.remove_fish(user.id, fish_name, quantity)

    if total_earned > 0:
        await db.add_balance(user.id, total_earned, "Sold fish")
        await db.add_fishing_stat(user.id, "total_sold", len(fish_sold))

        text = f"💰 <b>Sold all fish for {format_price(total_earned)}!</b>\n\n"
        text += "\n".join(fish_sold)

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🎣 Go Fishing",
                        callback_data=f"fish:show:{user.id}",
                    )
                ]
            ]
        )

        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=keyboard),
            callback.message.chat,
        )
        await safe_callback_answer(callback)
    else:
        await safe_callback_answer(
            callback, "No fish to sell!", show_alert=True
        )


reg("sellfish", "💰 Sell fish")


@client.on_message(filters.command(["sellfish"]))
async def sell_fish_command(
    message: Message,
):
    """Sell fish. Usage: /sellfish <name> [amount] or /sellfish all"""
    user = message.from_user
    args = message.text.split()[1:] if message.text else []

    if not args:
        await message.reply(
            "💰 <b>Sell Fish</b>\n\n"
            "Usage:\n"
            "<code>/sellfish all</code> - Sell all fish\n"
            "<code>/sellfish sardine 5</code> - Sell 5 sardines"
        )
        return

    if args[0].lower() == "all":
        # Sell all
        inventory = await db.get_fish_inventory(user.id)

        total_earned = 0
        fish_sold = []

        for fish_name, quantity in inventory.items():
            if quantity > 0 and fish_name in FISH_TYPES:
                info = FISH_TYPES[fish_name]
                price = info["sell_price"] * quantity
                total_earned += price
                fish_sold.append(f"{info['emoji']} {quantity}x {fish_name}")
                await db.remove_fish(user.id, fish_name, quantity)

        if total_earned > 0:
            await db.add_balance(user.id, total_earned, "Sold fish")
            text = f"💰 <b>Sold fish for {format_price(total_earned)}!</b>\n\n"
            text += "\n".join(fish_sold)
            await message.reply(text)
        else:
            await message.reply("❌ No fish to sell!")
        return

    # Sell specific fish
    fish_name = args[0].lower()
    amount = int(args[1]) if len(args) > 1 and args[1].isdigit() else None

    # Find matching fish
    matched_fish = None
    for fn in FISH_TYPES:
        if fn.lower() == fish_name or fn.lower().startswith(fish_name):
            matched_fish = fn
            break

    if not matched_fish:
        await message.reply(f"❌ Unknown fish: {fish_name}")
        return

    # Check inventory
    inventory = await db.get_fish_inventory(user.id)
    owned = inventory.get(matched_fish, 0)

    if owned <= 0:
        await message.reply(f"❌ You don't have any {matched_fish}!")
        return

    to_sell = amount if amount and amount <= owned else owned

    info = FISH_TYPES[matched_fish]
    total_price = info["sell_price"] * to_sell

    await db.remove_fish(user.id, matched_fish, to_sell)
    await db.add_balance(user.id, total_price, f"Sold {matched_fish}")

    await message.reply(
        f"💰 Sold {to_sell}x {info['emoji']} {matched_fish} for {format_price(total_price)}!"
    )
