"""Factory mini-game - hire workers, send to work, earn money."""

import html
from datetime import datetime

from pyrogram.errors import BadRequest
from pyrogram import Client as Bot
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.command_registry import reg
from bot.constants import (
    FACTORY_BASE_EARNING,
    FACTORY_EXPANSION_COSTS,
    FACTORY_WORK_DURATION,
    FOODS,
    WORKER_BASE_SALARY,
    WORKER_FATIGUE_PER_SHIFT,
    WORKER_FATIGUE_THRESHOLD,
    WORKER_FEED_COST,
    WORKER_XP_PER_SHIFT,
    format_price,
    get_worker_level,
    get_worker_max_factories,
)
from bot.database import Database
from bot.plugins.callbacks import safe_callback_answer
from bot.queue_it import queue_it
from bot.utils import user_mention
from pyrogram import filters
from bot.client import client
from bot.database import db


def format_time_remaining(seconds: int) -> str:
    """Format seconds into human readable time."""
    if seconds <= 0:
        return "Ready!"
    minutes = seconds // 60
    secs = seconds % 60
    if minutes >= 60:
        hours = minutes // 60
        mins = minutes % 60
        return f"{hours}h {mins}m"
    return f"{minutes}m {secs}s"


async def get_factory_display(
    db: Database, user_id: int, factory: dict, bot: Bot
) -> tuple[str, InlineKeyboardMarkup]:
    """Generate factory display text and keyboard."""
    workers = await db.get_factory_workers(factory["id"])

    # Check for finished workers and update them
    now = datetime.now()
    idle_workers = []
    working_workers = []

    for w in workers:
        if w["is_working"] and w["work_started_at"]:
            elapsed = (
                now - w["work_started_at"].replace(tzinfo=None)
            ).total_seconds()
            if elapsed >= FACTORY_WORK_DURATION:
                # Work is done - end shift
                earnings = FACTORY_BASE_EARNING
                await db.end_worker_shift(
                    w["assignment_id"],
                    WORKER_FATIGUE_PER_SHIFT,
                    factory["id"],
                    earnings,
                )
                await db.update_worker_xp(w["id"], WORKER_XP_PER_SHIFT)
                await db.add_balance(
                    user_id, earnings, f"Factory earnings from {w['name']}"
                )
                # Refresh worker data
                w = dict(w)
                w["is_working"] = False
                idle_workers.append(w)
            else:
                working_workers.append((
                    w,
                    int(FACTORY_WORK_DURATION - elapsed),
                ))
        else:
            idle_workers.append(w)

    # Build display text
    text = f"🏭 <b>{html.escape(factory['name'] or 'My Factory')}</b>\n"
    text += "━━━━━━━━━━━━━━━━\n"
    text += f"💰 Total Earnings: {format_price(factory['total_earnings'])}\n"
    text += f"👥 Workers: {len(workers)}/{factory['capacity']}\n\n"

    # Idle workers section
    if idle_workers:
        text += "<b>😴 Idle Workers:</b>\n"
        for w in idle_workers:
            level = get_worker_level(w["xp"])
            fatigue_bar = "█" * (w["fatigue"] // 10) + "░" * (
                10 - w["fatigue"] // 10
            )
            fatigue_emoji = (
                "🟢"
                if w["fatigue"] < 50
                else "🟡"
                if w["fatigue"] < 80
                else "🔴"
            )
            text += f"  • {html.escape(w['name'])} (Lv.{level})\n"
            text += f"    {fatigue_emoji} Fatigue: [{fatigue_bar}] {w['fatigue']}%\n"

    # Working workers section
    if working_workers:
        text += "\n<b>⚙️ Working:</b>\n"
        for w, remaining in working_workers:
            level = get_worker_level(w["xp"])
            text += f"  • {html.escape(w['name'])} (Lv.{level})\n"
            text += f"    ⏱️ {format_time_remaining(remaining)} remaining\n"

    if not workers:
        text += "\n<i>No workers hired yet!</i>\n"
        text += "Reply to someone with /hire to hire them.\n"

    # Build keyboard
    buttons = []

    # Send idle workers to work (if any can work)
    can_work = [
        w for w in idle_workers if w["fatigue"] < WORKER_FATIGUE_THRESHOLD
    ]
    if can_work:
        buttons.append([
            InlineKeyboardButton(
                text=f"⚙️ Send {len(can_work)} to Work",
                callback_data=f"factory:{factory['id']}:sendall",
            )
        ])

    # Expand factory
    current_cap = factory["capacity"]
    if current_cap < 10:
        expand_cost = FACTORY_EXPANSION_COSTS.get(current_cap + 1, 100000)
        buttons.append([
            InlineKeyboardButton(
                text=f"📐 Expand (+1 slot) - {format_price(expand_cost)}",
                callback_data=f"factory:{factory['id']}:expand",
            )
        ])

    # Refresh button
    buttons.append([
        InlineKeyboardButton(
            text="🔄 Refresh", callback_data=f"factory:{factory['id']}:refresh"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return text, keyboard


@client.on_message(filters.command(["factory"]))
async def factory_command(message: Message, bot: Bot):
    """View your factory."""
    user = message.from_user

    # Ensure user exists
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Get or create factory
    factory = await db.get_or_create_factory(user.id)

    text, keyboard = await get_factory_display(db, user.id, factory, bot)
    from bot.achievements import (
        check_factory_achievements,
        check_money_achievements,
    )

    await check_factory_achievements(db, user.id, bot, message.chat.id)
    await check_money_achievements(db, user.id, bot, message.chat.id)
    await message.reply(text, reply_markup=keyboard)


@client.on_message(filters.command(["hire"]))
async def hire_command(message: Message, bot: Bot):
    """Hire someone as a worker. Use in reply to a message."""
    user = message.from_user

    # Must be reply
    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply(
            "❌ Reply to someone's message to hire them!\n"
            f"💰 Hiring cost: {format_price(WORKER_BASE_SALARY)}"
        )
        return

    target = message.reply_to_message.from_user

    # Can't hire yourself
    if target.id == user.id:
        await message.reply("❌ You can't hire yourself!")
        return

    # Can't hire bots
    if target.is_bot:
        await message.reply("🤖 Bots cannot be hired as workers!")
        return

    # Ensure both users exist
    await db.upsert_user(user.id, user.username, user.first_name)
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Get factory
    factory = await db.get_or_create_factory(user.id)

    # Check capacity
    current_workers = await db.get_factory_workers(factory["id"])
    if len(current_workers) >= factory["capacity"]:
        await message.reply(
            f"❌ Factory is full! ({len(current_workers)}/{factory['capacity']})\n"
            f"Expand your factory to hire more workers."
        )
        return

    # Check if target blocked hiring from this user
    if await db.is_hire_blocked(user.id, target.id):
        await message.reply(
            f"❌ {user_mention(target)} has blocked hire requests from you!"
        )
        return

    # Get or create worker profile for target
    worker = await db.get_or_create_worker(target.id, target.first_name)

    # Check if worker is blocked at factory level
    if await db.is_worker_blocked(factory["id"], worker["id"]):
        await message.reply(
            f"❌ {user_mention(target)} is blocked from your factory!"
        )
        return

    # Check if already hired
    existing = await db.get_worker_assignment(worker["id"], factory["id"])
    if existing:
        await message.reply(
            f"❌ {user_mention(target)} already works at your factory!"
        )
        return

    # Check for existing pending request
    pending = await db.get_hire_request(factory["id"], target.id)
    if pending:
        await message.reply(
            f"⏳ You already have a pending hire request for {user_mention(target)}!"
        )
        return

    # Check worker's factory slot limit
    current_factories = await db.get_worker_factory_count(worker["id"])
    max_factories = get_worker_max_factories(worker["xp"])
    if current_factories >= max_factories:
        await message.reply(
            f"❌ {user_mention(target)} is already working at {current_factories} factories!\n"
            f"They need to level up to work at more places."
        )
        return

    # Check balance
    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < WORKER_BASE_SALARY:
        await message.reply(
            f"❌ Insufficient balance!\n"
            f"You have: {format_price(wallet['balance'])}\n"
            f"Hiring cost: {format_price(WORKER_BASE_SALARY)}"
        )
        return

    # Create hire request (pending confirmation)
    request = await db.create_hire_request(
        factory["id"], target.id, user.id, message.chat.id
    )

    if not request:
        await message.reply("❌ Failed to create hire request. Try again.")
        return

    # Send confirmation request
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Accept",
                    callback_data=f"hire_accept:{request['id']}",
                ),
                InlineKeyboardButton(
                    text="❌ Decline",
                    callback_data=f"hire_decline:{request['id']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🚫 Block this person",
                    callback_data=f"hire_block:{request['id']}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Cancel",
                    callback_data=f"hire_cancel:{request['id']}",
                ),
            ],
        ]
    )

    await message.reply(
        f"👷 <b>Hire Request</b>\n\n"
        f"{user_mention(user)} wants to hire {user_mention(target)} at their factory!\n\n"
        f"💰 Salary: {format_price(WORKER_BASE_SALARY)}\n"
        f"⏱️ Work shifts: 1 hour\n"
        f"💵 Earnings per shift: {format_price(FACTORY_BASE_EARNING)}\n\n"
        f"{user_mention(target)}, do you accept?",
        reply_markup=keyboard,
    )


@client.on_message(filters.command(["fire"]))
async def fire_command(message: Message, bot: Bot):
    """Fire a worker. Use in reply to their message."""
    user = message.from_user

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply("❌ Reply to a worker's message to fire them!")
        return

    target = message.reply_to_message.from_user

    # Get factory
    factory = await db.get_factory(user.id)
    if not factory:
        await message.reply("❌ You don't have a factory!")
        return

    # Get worker
    worker = await db.get_worker(target.id)
    if not worker:
        await message.reply(f"❌ {user_mention(target)} is not a worker!")
        return

    # Check if they work here
    assignment = await db.get_worker_assignment(worker["id"], factory["id"])
    if not assignment:
        await message.reply(
            f"❌ {user_mention(target)} doesn't work at your factory!"
        )
        return

    # Can't fire while working
    if assignment["is_working"]:
        await message.reply(
            f"❌ Can't fire {user_mention(target)} while they're working!\n"
            f"Wait for their shift to end."
        )
        return

    # Fire them
    await db.fire_worker(factory["id"], worker["id"])
    await message.reply(f"✅ Fired {user_mention(target)} from your factory.")


@client.on_message(filters.command(["block_hire"]))
async def block_hire_command(
    message: Message,
):
    """Block someone from being hired at your factory."""
    user = message.from_user

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply(
            "❌ Reply to someone's message to block them from your factory!"
        )
        return

    target = message.reply_to_message.from_user

    # Get factory
    factory = await db.get_factory(user.id)
    if not factory:
        await message.reply("❌ You don't have a factory!")
        return

    # Get or create worker profile
    target = await db.upsert_user(target.id, target.username, target.first_name)
    worker = await db.get_or_create_worker(target.id, target.first_name)

    # Check if already blocked
    if await db.is_worker_blocked(factory["id"], worker["id"]):
        # Unblock
        await db.unblock_worker(factory["id"], worker["id"])
        await message.reply(
            f"✅ Unblocked {user_mention(target)} from your factory."
        )
    else:
        # Block
        await db.block_worker(factory["id"], worker["id"])
        await message.reply(
            f"🚫 Blocked {user_mention(target)} from being hired at your factory."
        )


reg("unblock_hire", "🔓 Unblock someone from hiring you")


@client.on_message(filters.command(["unblock_hire"]))
async def unblock_hire_command(
    message: Message,
):
    """Unblock someone from being able to hire you."""
    user = message.from_user

    if not message.reply_to_message or not message.reply_to_message.from_user:
        await message.reply(
            "❌ Reply to someone's message to unblock them!\n"
            "They will be able to send you hire requests again."
        )
        return

    target = message.reply_to_message.from_user

    # Ensure user exists in database
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Check if they are blocked
    is_blocked = await db.is_hire_blocked(user.id, target.id)
    if not is_blocked:
        await message.reply(
            f"ℹ️ {user_mention(target)} is not blocked from hiring you."
        )
        return

    # Unblock them
    await db.remove_hire_block(user.id, target.id)
    await message.reply(
        f"✅ Unblocked {user_mention(target)}. They can now send you hire requests!"
    )


reg("feedworker", "🍽️ Feed a worker to reduce fatigue [/fw]")


@client.on_message(filters.command(["feedworker", "fw"]))
async def feed_worker_command(message: Message, bot: Bot):
    """Feed a worker to reduce their fatigue."""
    user = message.from_user

    # Get factory
    factory = await db.get_factory(user.id)
    if not factory:
        await message.reply("❌ You don't have a factory!")
        return

    # Get all workers at this factory
    workers = await db.get_factory_workers(factory["id"])

    # Filter workers who have fatigue > 0
    fatigued_workers = [w for w in workers if w["fatigue"] > 0]

    if not fatigued_workers:
        await message.reply("✅ All your workers are at 0 fatigue!")
        return

    # Build keyboard with fatigued workers
    keyboard_rows = [
        [
            InlineKeyboardButton(
                text="🍽️ Feed All",
                callback_data=f"feedworker_all:{user.id}",
            )
        ]
    ]
    for w in fatigued_workers:
        keyboard_rows.append([
            InlineKeyboardButton(
                text=f"{w['name']} ({w['fatigue']}% fatigue)",
                callback_data=f"feedworker_select:{user.id}:{w['user_id']}",
            )
        ])
    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_rows)

    await message.reply(
        "🍽️ <b>Feed Worker</b>\n\nSelect a worker to feed:",
        reply_markup=keyboard,
    )


reg("workerstats", "👷 View worker stats [/ws]")


@client.on_message(filters.command(["workerstats", "ws"]))
async def worker_stats_command(
    message: Message,
):
    """View your worker profile stats."""
    user = message.from_user

    # Check if target specified
    target = user
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user

    worker = await db.get_worker(target.id)
    if not worker:
        if target.id == user.id:
            await message.reply(
                "❌ You're not a worker yet!\n"
                "Someone needs to hire you at their factory first."
            )
        else:
            await message.reply(f"❌ {user_mention(target)} is not a worker!")
        return

    level = get_worker_level(worker["xp"])
    max_factories = get_worker_max_factories(worker["xp"])
    current_factories = await db.get_worker_factory_count(worker["id"])

    # Get next level XP
    from bot.constants import WORKER_LEVEL_XP

    next_level_xp = WORKER_LEVEL_XP.get(level + 1)
    xp_progress = ""
    if next_level_xp:
        progress = worker["xp"] - WORKER_LEVEL_XP[level]
        needed = next_level_xp - WORKER_LEVEL_XP[level]
        xp_progress = f" ({progress}/{needed} to next)"

    text = f"👷 <b>Worker Profile: {html.escape(worker['name'])}</b>\n"
    text += "━━━━━━━━━━━━━━━━\n"
    text += f"⭐ Level: {level}\n"
    text += f"✨ XP: {worker['xp']}{xp_progress}\n"
    text += f"🏭 Factory Slots: {current_factories}/{max_factories}\n"
    text += f"📅 Worker since: {worker['created_at'].strftime('%Y-%m-%d')}"

    await message.reply(text)


@client.on_message(filters.command(["resign_factory", "resign"]))
async def resign_factory_command(
    message: Message,
):
    """View factories you work at and resign from them."""
    user = message.from_user

    worker = await db.get_worker(user.id)
    if not worker:
        await message.reply(
            "❌ You're not a worker yet!\n"
            "Someone needs to hire you at their factory first."
        )
        return

    factories = await db.get_worker_factories(worker["id"])

    if not factories:
        await message.reply("📭 You're not working at any factories.")
        return

    text = "🏭 <b>Your Factory Jobs</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "Click a factory to resign:\n"

    buttons = []
    for f in factories:
        status = "⚙️ Working" if f["is_working"] else "😴 Idle"
        owner_name = f["owner_name"] or "Unknown"
        text += f"• {html.escape(f['name'] or 'Factory')} ({html.escape(owner_name)}) - {status}\n"

        # Allow resignation regardless of working status
        resign_text = "🚪 Resign"
        if f["is_working"]:
            resign_text = "🚪 Resign (forfeit pay)"
        buttons.append([
            InlineKeyboardButton(
                text=f"{resign_text} from {owner_name}'s factory",
                callback_data=f"resign:{f['id']}:{worker['id']}",
            )
        ])

    text += "\n<i>⚠️ Resigning while working forfeits your shift pay (owner keeps earnings).</i>"

    keyboard = (
        InlineKeyboardMarkup(inline_keyboard=buttons) if buttons else None
    )
    await message.reply(text, reply_markup=keyboard)


# ============ HIRE CALLBACKS ============


@client.on_callback_query(filters.regex(r"^" + "hire_accept:"))
async def handle_hire_accept(callback: CallbackQuery, bot: Bot):
    """Handle hire request acceptance."""
    request_id = int(callback.data.split(":")[1])

    request = await db.get_hire_request_by_id(request_id)
    if not request:
        await callback.answer("This hire request has expired.", show_alert=True)
        try:
            await queue_it(
                lambda: callback.message.edit_text(
                    "❌ Hire request expired or cancelled."
                ),
                callback.message.chat,
            )
        except BadRequest:
            pass
        return

    # Only target can accept
    if callback.from_user.id != request["worker_user_id"]:
        await callback.answer(
            "This hire request is not for you!", show_alert=True
        )
        return

    # Get factory and worker
    factory = await db.fetchrow(
        "SELECT * FROM factories WHERE id = $1", request["factory_id"]
    )
    if not factory:
        await callback.answer("Factory no longer exists!", show_alert=True)
        await db.delete_hire_request(request_id)
        return

    worker = await db.get_or_create_worker(
        callback.from_user.id, callback.from_user.first_name
    )

    # Check if already hired
    existing = await db.get_worker_assignment(worker["id"], factory["id"])
    if existing:
        await callback.answer(
            "You already work at this factory!", show_alert=True
        )
        await db.delete_hire_request(request_id)
        return

    # Check requester balance
    wallet = await db.get_wallet(request["requester_id"])
    if wallet["balance"] < WORKER_BASE_SALARY:
        await callback.answer(
            "Factory owner doesn't have enough money!", show_alert=True
        )
        await db.delete_hire_request(request_id)
        try:
            await queue_it(
                lambda: callback.message.edit_text(
                    "❌ Hire cancelled - factory owner doesn't have enough funds."
                ),
                callback.message.chat,
            )
        except BadRequest:
            pass
        return

    # Process hiring
    await db.add_balance(
        request["requester_id"],
        -WORKER_BASE_SALARY,
        f"Hired {callback.from_user.first_name}",
    )
    await db.hire_worker(factory["id"], worker["id"])
    await db.delete_hire_request(request_id)

    # Get requester info
    requester = await db.get_user(request["requester_id"])
    requester_name = requester["first_name"] if requester else "Someone"

    try:
        await queue_it(
            lambda: callback.message.edit_text(
                f"✅ <b>Hire Accepted!</b>\n\n"
                f"👷 {user_mention(callback.from_user)} is now working at {html.escape(requester_name)}'s factory!"
            ),
            callback.message.chat,
        )
    except BadRequest:
        pass

    await callback.answer(
        "You've been hired! Use /workerstats to check your profile."
    )


@client.on_callback_query(filters.regex(r"^" + "hire_decline:"))
async def handle_hire_decline(
    callback: CallbackQuery,
):
    """Handle hire request decline."""
    request_id = int(callback.data.split(":")[1])

    request = await db.get_hire_request_by_id(request_id)
    if not request:
        await callback.answer("This hire request has expired.", show_alert=True)
        try:
            await queue_it(
                lambda: callback.message.edit_text(
                    "❌ Hire request expired or cancelled."
                ),
                callback.message.chat,
            )
        except BadRequest:
            pass
        return

    # Only target can decline
    if callback.from_user.id != request["worker_user_id"]:
        await callback.answer(
            "This hire request is not for you!", show_alert=True
        )
        return

    await db.delete_hire_request(request_id)

    try:
        await queue_it(
            lambda: callback.message.edit_text(
                f"❌ {user_mention(callback.from_user)} declined the hire request."
            ),
            callback.message.chat,
        )
    except BadRequest:
        pass

    await callback.answer("You declined the hire request.")


@client.on_callback_query(filters.regex(r"^" + "hire_cancel:"))
async def handle_hire_cancel(
    callback: CallbackQuery,
):
    """Handle hire request cancellation by requester."""
    request_id = int(callback.data.split(":")[1])

    request = await db.get_hire_request_by_id(request_id)
    if not request:
        await callback.answer("This hire request has expired.", show_alert=True)
        try:
            await queue_it(
                lambda: callback.message.edit_text(
                    "❌ Hire request expired or cancelled."
                ),
                callback.message.chat,
            )
        except BadRequest:
            pass
        return

    # Only requester can cancel
    if callback.from_user.id != request["requester_id"]:
        await callback.answer("You can't perform this action!", show_alert=True)
        return

    await db.delete_hire_request(request_id)

    try:
        await queue_it(
            lambda: callback.message.edit_text(
                f"❌ {user_mention(callback.from_user)} cancelled the hire request."
            ),
            callback.message.chat,
        )
    except BadRequest:
        pass

    await callback.answer("Request cancelled.")


@client.on_callback_query(filters.regex(r"^" + "hire_block:"))
async def handle_hire_block(
    callback: CallbackQuery,
):
    """Handle hire block request."""
    request_id = int(callback.data.split(":")[1])

    request = await db.get_hire_request_by_id(request_id)
    if not request:
        await callback.answer("This hire request has expired.", show_alert=True)
        try:
            await queue_it(
                lambda: callback.message.edit_text(
                    "❌ Hire request expired or cancelled."
                ),
                callback.message.chat,
            )
        except BadRequest:
            pass
        return

    # Only target can block
    if callback.from_user.id != request["worker_user_id"]:
        await callback.answer(
            "This hire request is not for you!", show_alert=True
        )
        return

    # Block the requester from hiring this user
    await db.add_hire_block(callback.from_user.id, request["requester_id"])
    await db.delete_hire_request(request_id)

    requester = await db.get_user(request["requester_id"])
    requester_name = requester["first_name"] if requester else "This person"

    try:
        await queue_it(
            lambda: callback.message.edit_text(
                f"🚫 {user_mention(callback.from_user)} blocked hire requests from {html.escape(requester_name)}."
            ),
            callback.message.chat,
        )
    except BadRequest:
        pass

    await callback.answer(f"You've blocked {requester_name} from hiring you.")


@client.on_callback_query(filters.regex(r"^" + "resign:"))
async def handle_resign_callback(
    callback: CallbackQuery,
):
    """Handle factory resign callback."""
    parts = callback.data.split(":")
    factory_id = int(parts[1])
    worker_id = int(parts[2])

    # Verify it's the right user
    worker = await db.get_worker(callback.from_user.id)
    if not worker or worker["id"] != worker_id:
        await callback.answer(
            "This is not your worker profile!", show_alert=True
        )
        return

    # Try to resign
    result = await db.resign_from_factory(worker_id, factory_id)

    if result["success"]:
        if result.get("earnings_given_to_owner", 0) > 0:
            await callback.answer(
                f"Resigned! You forfeited your shift pay (owner kept ${result['earnings_given_to_owner']:,}).",
                show_alert=True,
            )
        else:
            await callback.answer(
                "You've resigned from this factory!", show_alert=True
            )

        # Refresh the factory list
        factories = await db.get_worker_factories(worker_id)

        if factories:
            text = "🏭 <b>Your Factory Jobs</b>\n"
            text += "━━━━━━━━━━━━━━━━\n\n"
            text += "Click a factory to resign:\n"

            buttons = []
            for f in factories:
                status = "⚙️ Working" if f["is_working"] else "😴 Idle"
                owner_name = f["owner_name"] or "Unknown"
                text += f"• {html.escape(f['name'] or 'Factory')} ({html.escape(owner_name)}) - {status}\n"

                resign_text = "🚪 Resign"
                if f["is_working"]:
                    resign_text = "🚪 Resign (forfeit pay)"
                buttons.append([
                    InlineKeyboardButton(
                        text=f"{resign_text} from {owner_name}'s factory",
                        callback_data=f"resign:{f['id']}:{worker_id}",
                    )
                ])

            text += (
                "\n<i>⚠️ Resigning while working forfeits your shift pay.</i>"
            )

            keyboard = (
                InlineKeyboardMarkup(inline_keyboard=buttons)
                if buttons
                else None
            )
            try:
                await queue_it(
                    lambda: callback.message.edit_text(
                        text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )
            except BadRequest:
                pass
        else:
            try:
                await queue_it(
                    lambda: callback.message.edit_text(
                        "📭 You're not working at any factories now."
                    ),
                    callback.message.chat,
                )
            except BadRequest:
                pass
    else:
        await callback.answer(
            "Failed to resign. Please try again.",
            show_alert=True,
        )


@client.on_callback_query(filters.regex(r"^" + "feedworker_select:"))
async def handle_feedworker_select_callback(callback: CallbackQuery, bot: Bot):
    """Handle worker selection for feeding."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await safe_callback_answer(callback, "Invalid selection")
        return

    initiator_id = int(parts[1])
    worker_user_id = int(parts[2])

    # Check if the person clicking is the factory owner
    if callback.from_user.id != initiator_id:
        await safe_callback_answer(
            callback,
            "Only the factory owner can feed workers!",
            show_alert=True,
        )
        return

    # Get factory
    factory = await db.get_factory(initiator_id)
    if not factory:
        await safe_callback_answer(
            callback, "Factory not found!", show_alert=True
        )
        return

    # Get worker
    worker = await db.get_worker(worker_user_id)
    if not worker:
        await safe_callback_answer(
            callback, "Worker not found!", show_alert=True
        )
        return

    # Get worker assignment
    assignment = await db.get_worker_assignment(worker["id"], factory["id"])
    if not assignment:
        await safe_callback_answer(
            callback, "Worker not found at your factory!", show_alert=True
        )
        return

    if assignment["fatigue"] == 0:
        await safe_callback_answer(
            callback, "This worker has no fatigue!", show_alert=True
        )
        return

    # Get user's wallet
    wallet = await db.get_wallet(initiator_id)

    # Get user's food inventory
    all_inventory_items = await db.get_inventory(initiator_id)
    # Filter for food items only
    inventory_items = [
        item for item in all_inventory_items if item["item_type"] == "food"
    ]

    # Build keyboard with money and available food items
    keyboard_buttons = []

    # Add money option
    keyboard_buttons.append([
        InlineKeyboardButton(
            text=f"💵 Money ({format_price(WORKER_FEED_COST)})",
            callback_data=f"feedworker_feed:{initiator_id}:{worker_user_id}:money",
        )
    ])

    # Add food items from inventory
    for item in inventory_items:
        food_name = item["item_name"]
        qty = item["quantity"]
        if food_name in FOODS and qty > 0:
            food_info = FOODS[food_name]
            keyboard_buttons.append([
                InlineKeyboardButton(
                    text=f"{food_info['emoji']} {food_name} (x{qty}) [-{food_info['feed_value']}%]",
                    callback_data=f"feedworker_feed:{initiator_id}:{worker_user_id}:food:{food_name}",
                )
            ])

    # Add back button
    keyboard_buttons.append([
        InlineKeyboardButton(
            text="↩️ Back",
            callback_data=f"feedworker_back:{initiator_id}",
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    # Show worker info
    text = (
        f"🍽️ <b>Feed {html.escape(worker['name'])}</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"😴 Current fatigue: {assignment['fatigue']}%\n\n"
        f"💰 Your balance: {format_price(wallet['balance'])}\n"
        f"💵 Feed cost: {format_price(WORKER_FEED_COST)} (reduces 25%)\n\n"
        f"Choose payment method:"
    )

    try:
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=keyboard),
            callback.message.chat,
        )
    except BadRequest:
        pass
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "feedworker_feed:"))
async def handle_feedworker_feed_callback(callback: CallbackQuery, bot: Bot):
    """Handle feeding worker with money or food."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await safe_callback_answer(callback, "Invalid action")
        return

    initiator_id = int(parts[1])
    worker_user_id = int(parts[2])
    feed_type = parts[3]  # 'money' or 'food'

    # Check if the person clicking is the factory owner
    if callback.from_user.id != initiator_id:
        await safe_callback_answer(
            callback,
            "Only the factory owner can feed workers!",
            show_alert=True,
        )
        return

    # Get factory
    factory = await db.get_factory(initiator_id)
    if not factory:
        await safe_callback_answer(
            callback, "Factory not found!", show_alert=True
        )
        return

    # Get worker
    worker = await db.get_worker(worker_user_id)
    if not worker:
        await safe_callback_answer(
            callback, "Worker not found!", show_alert=True
        )
        return

    # Get worker assignment
    assignment = await db.get_worker_assignment(worker["id"], factory["id"])
    if not assignment:
        await safe_callback_answer(
            callback, "Worker not found at your factory!", show_alert=True
        )
        return

    if assignment["fatigue"] == 0:
        await safe_callback_answer(
            callback, "This worker has no fatigue!", show_alert=True
        )
        return

    if feed_type == "money":
        # Check balance
        wallet = await db.get_wallet(initiator_id)
        if wallet["balance"] < WORKER_FEED_COST:
            await safe_callback_answer(
                callback,
                f"Insufficient balance! Need {format_price(WORKER_FEED_COST)}",
                show_alert=True,
            )
            return

        # Deduct money and reduce fatigue by 25%
        await db.add_balance(
            initiator_id, -WORKER_FEED_COST, f"Fed {worker['name']}"
        )
        new_fatigue = await db.reduce_worker_fatigue(assignment["id"], 25)

        text = (
            f"🍽️ <b>Fed {html.escape(worker['name'])}!</b>\n\n"
            f"💵 Cost: {format_price(WORKER_FEED_COST)}\n"
            f"😴 Fatigue: {assignment['fatigue']}% → {new_fatigue}%"
        )

    elif feed_type == "food":
        if len(parts) < 5:
            await safe_callback_answer(callback, "Invalid food selection")
            return

        food_name = parts[4]
        if food_name not in FOODS:
            await safe_callback_answer(
                callback, "Invalid food!", show_alert=True
            )
            return

        food_info = FOODS[food_name]

        # Check inventory
        qty = await db.get_inventory_item(initiator_id, "food", food_name)
        if qty <= 0:
            await safe_callback_answer(
                callback,
                f"You don't have any {food_info['emoji']} {food_name}!",
                show_alert=True,
            )
            return

        # Use food
        await db.remove_inventory_item(initiator_id, "food", food_name, 1)
        new_fatigue = await db.reduce_worker_fatigue(
            assignment["id"], food_info["feed_value"]
        )

        text = (
            f"🍽️ <b>Fed {html.escape(worker['name'])}!</b>\n\n"
            f"{food_info['emoji']} Used: 1x {food_name}\n"
            f"😴 Fatigue: {assignment['fatigue']}% → {new_fatigue}%"
        )
    else:
        await safe_callback_answer(
            callback, "Invalid feed type!", show_alert=True
        )
        return

    # Show result with back button
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Back to Workers",
                    callback_data=f"feedworker_back:{initiator_id}",
                )
            ]
        ]
    )

    try:
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=keyboard),
            callback.message.chat,
        )
    except BadRequest:
        pass
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "feedworker_back:"))
async def handle_feedworker_back_callback(callback: CallbackQuery, bot: Bot):
    """Go back to worker selection."""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await safe_callback_answer(callback, "Invalid action")
        return

    initiator_id = int(parts[1])

    # Check if the person clicking is the factory owner
    if callback.from_user.id != initiator_id:
        await safe_callback_answer(
            callback,
            "Only the factory owner can feed workers!",
            show_alert=True,
        )
        return

    # Get factory
    factory = await db.get_factory(initiator_id)
    if not factory:
        await safe_callback_answer(
            callback, "Factory not found!", show_alert=True
        )
        return

    # Get all workers at this factory
    workers = await db.get_factory_workers(factory["id"])

    # Filter workers who have fatigue > 0
    fatigued_workers = [w for w in workers if w["fatigue"] > 0]

    if not fatigued_workers:
        await safe_callback_answer(
            callback, "All your workers are at 0 fatigue!", show_alert=True
        )
        return

    # Build keyboard with fatigued workers
    keyboard_buttons = [
        [
            InlineKeyboardButton(
                text="🍽️ Feed All",
                callback_data=f"feedworker_all:{initiator_id}",
            )
        ]
    ]
    for w in fatigued_workers:
        keyboard_buttons.append([
            InlineKeyboardButton(
                text=f"{w['name']} ({w['fatigue']}% fatigue)",
                callback_data=f"feedworker_select:{initiator_id}:{w['user_id']}",
            )
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=keyboard_buttons)

    text = "🍽️ <b>Feed Worker</b>\n\nSelect a worker to feed:"

    try:
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=keyboard),
            callback.message.chat,
        )
    except BadRequest:
        pass
    await callback.answer()


@client.on_callback_query(filters.regex(r"^feedworker_all:"))
async def handle_feedworker_all_callback(callback: CallbackQuery, bot: Bot):
    """Show payment-method submenu for feeding all fatigued workers at once."""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await safe_callback_answer(callback, "Invalid action")
        return

    initiator_id = int(parts[1])

    if callback.from_user.id != initiator_id:
        await safe_callback_answer(
            callback,
            "Only the factory owner can feed workers!",
            show_alert=True,
        )
        return

    factory = await db.get_factory(initiator_id)
    if not factory:
        await safe_callback_answer(
            callback, "Factory not found!", show_alert=True
        )
        return

    workers = await db.get_factory_workers(factory["id"])
    fatigued = [w for w in workers if w["fatigue"] > 0]

    if not fatigued:
        await safe_callback_answer(
            callback, "All your workers are at 0 fatigue!", show_alert=True
        )
        return

    wallet = await db.get_wallet(initiator_id)
    all_inv = await db.get_inventory(initiator_id)
    food_inv = [
        it
        for it in all_inv
        if it["item_type"] == "food"
        and it["quantity"] > 0
        and it["item_name"] in FOODS
    ]

    n = len(fatigued)
    total_money = WORKER_FEED_COST * n
    affordable = wallet["balance"] // WORKER_FEED_COST

    rows: list[list[InlineKeyboardButton]] = []
    money_label = f"💵 Money ({format_price(total_money)} total)"
    if affordable < n:
        money_label += f" — covers {affordable}/{n}"
    rows.append([
        InlineKeyboardButton(
            text=money_label,
            callback_data=f"feedworker_all_apply:{initiator_id}:money",
        )
    ])
    for item in food_inv:
        food_name = item["item_name"]
        qty = item["quantity"]
        food_info = FOODS[food_name]
        will_feed = min(qty, n)
        rows.append([
            InlineKeyboardButton(
                text=(
                    f"{food_info['emoji']} {food_name} "
                    f"(x{qty}) — feeds {will_feed} × -{food_info['feed_value']}%"
                ),
                callback_data=f"feedworker_all_apply:{initiator_id}:food:{food_name}",
            )
        ])
    rows.append([
        InlineKeyboardButton(
            text="↩️ Back",
            callback_data=f"feedworker_back:{initiator_id}",
        )
    ])

    text = (
        f"🍽️ <b>Feed All — Payment Method</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Fatigued workers: <b>{n}</b>\n"
        f"💰 Balance: {format_price(wallet['balance'])}\n"
        f"💵 Money total: {format_price(total_money)} "
        f"({format_price(WORKER_FEED_COST)} × {n}, each -25%)\n\n"
        f"Most-fatigued workers are fed first when supply is limited.\n"
        f"Choose payment:"
    )

    try:
        await queue_it(
            lambda: callback.message.edit_text(
                text,
                reply_markup=InlineKeyboardMarkup(inline_keyboard=rows),
            ),
            callback.message.chat,
        )
    except BadRequest:
        pass
    await callback.answer()


@client.on_callback_query(filters.regex(r"^feedworker_all_apply:"))
async def handle_feedworker_all_apply_callback(
    callback: CallbackQuery, bot: Bot
):
    """Apply one feed per fatigued worker using the selected payment method."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await safe_callback_answer(callback, "Invalid action")
        return

    initiator_id = int(parts[1])
    feed_type = parts[2]

    if callback.from_user.id != initiator_id:
        await safe_callback_answer(
            callback,
            "Only the factory owner can feed workers!",
            show_alert=True,
        )
        return

    factory = await db.get_factory(initiator_id)
    if not factory:
        await safe_callback_answer(
            callback, "Factory not found!", show_alert=True
        )
        return

    workers = await db.get_factory_workers(factory["id"])
    fatigued = [w for w in workers if w["fatigue"] > 0]

    if not fatigued:
        await safe_callback_answer(
            callback, "All your workers are at 0 fatigue!", show_alert=True
        )
        return

    # Most-fatigued first so limited supply targets the workers who need it most.
    fatigued.sort(key=lambda w: w["fatigue"], reverse=True)

    fed = 0
    skipped = 0

    if feed_type == "money":
        wallet = await db.get_wallet(initiator_id)
        affordable = wallet["balance"] // WORKER_FEED_COST
        for w in fatigued:
            if fed >= affordable:
                skipped += 1
                continue
            await db.add_balance(
                initiator_id, -WORKER_FEED_COST, f"Fed {w['name']}"
            )
            await db.reduce_worker_fatigue(w["assignment_id"], 25)
            fed += 1

        spent = fed * WORKER_FEED_COST
        text = (
            f"🍽️ <b>Fed {fed} worker(s) with money</b>\n\n"
            f"💵 Spent: {format_price(spent)}\n"
            f"😴 Each got -25% fatigue"
        )
        if skipped:
            text += f"\n⚠️ Skipped {skipped} (insufficient balance)"

    elif feed_type == "food":
        if len(parts) < 4:
            await safe_callback_answer(callback, "Invalid food selection")
            return
        food_name = parts[3]
        if food_name not in FOODS:
            await safe_callback_answer(
                callback, "Invalid food!", show_alert=True
            )
            return

        food_info = FOODS[food_name]
        qty = await db.get_inventory_item(initiator_id, "food", food_name)
        if qty <= 0:
            await safe_callback_answer(
                callback,
                f"You have no {food_info['emoji']} {food_name}!",
                show_alert=True,
            )
            return

        for w in fatigued:
            if fed >= qty:
                skipped += 1
                continue
            await db.remove_inventory_item(initiator_id, "food", food_name, 1)
            await db.reduce_worker_fatigue(
                w["assignment_id"], food_info["feed_value"]
            )
            fed += 1

        text = (
            f"🍽️ <b>Fed {fed} worker(s) with "
            f"{food_info['emoji']} {food_name}</b>\n\n"
            f"📦 Used: {fed}x {food_name}\n"
            f"😴 Each got -{food_info['feed_value']}% fatigue"
        )
        if skipped:
            text += f"\n⚠️ Skipped {skipped} (out of food)"
    else:
        await safe_callback_answer(
            callback, "Invalid feed type!", show_alert=True
        )
        return

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="↩️ Back to Workers",
                    callback_data=f"feedworker_back:{initiator_id}",
                )
            ]
        ]
    )

    try:
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=keyboard),
            callback.message.chat,
        )
    except BadRequest:
        pass
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "factory:"))
async def handle_factory_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle factory button callbacks."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid action")
        return

    factory_id = int(parts[1])
    action = parts[2]

    # Get factory
    factory = await db.fetchrow(
        "SELECT * FROM factories WHERE id = $1", factory_id
    )

    if not factory:
        await callback.answer("Factory not found!", show_alert=True)
        return

    # Check ownership
    if factory["owner_id"] != callback.from_user.id:
        await callback.answer("This isn't your factory!", show_alert=True)
        return

    if action == "refresh":
        text, keyboard = await get_factory_display(
            db, callback.from_user.id, factory, bot
        )
        try:
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
        except BadRequest:
            pass
        await callback.answer("Refreshed!")

    elif action == "sendall":
        # Send all idle workers to work
        workers = await db.get_factory_workers(factory_id)
        sent_count = 0

        for w in workers:
            if not w["is_working"] and w["fatigue"] < WORKER_FATIGUE_THRESHOLD:
                await db.start_worker_shift(w["assignment_id"])
                sent_count += 1

        if sent_count > 0:
            text, keyboard = await get_factory_display(
                db, callback.from_user.id, factory, bot
            )
            try:
                await queue_it(
                    lambda: callback.message.edit_text(
                        text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )
            except BadRequest:
                pass
            await callback.answer(f"Sent {sent_count} workers to work!")
        else:
            await callback.answer(
                "No workers available to send!", show_alert=True
            )

    elif action == "expand":
        current_cap = factory["capacity"]
        if current_cap >= 10:
            await callback.answer(
                "Factory is at maximum capacity!", show_alert=True
            )
            return

        expand_cost = FACTORY_EXPANSION_COSTS.get(current_cap + 1, 100000)
        wallet = await db.get_wallet(callback.from_user.id)

        if wallet["balance"] < expand_cost:
            await callback.answer(
                f"Insufficient balance! Need {format_price(expand_cost)}",
                show_alert=True,
            )
            return

        # Expand
        await db.add_balance(
            callback.from_user.id,
            -expand_cost,
            f"Factory expansion to {current_cap + 1} slots",
        )
        await db.expand_factory(factory_id, current_cap + 1)

        # Refresh display
        factory = await db.fetchrow(
            "SELECT * FROM factories WHERE id = $1", factory_id
        )
        text, keyboard = await get_factory_display(
            db, callback.from_user.id, factory, bot
        )
        try:
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
        except BadRequest:
            pass
        await callback.answer(f"Factory expanded to {current_cap + 1} slots!")
