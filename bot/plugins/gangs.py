"""Gang system - create, join, leave, destroy gangs."""

from bot.queue_it import queue_it

import random
from datetime import datetime, timezone
from html import escape as html_escape

from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.command_registry import reg
from bot.constants import GANG_CREATION_FEE
from bot.database import Database
from bot.plugins.callbacks import safe_callback_answer
from pyrogram import filters
from bot.client import client
from bot.database import db


def format_price(amount: int) -> str:
    """Format price with $ and commas."""
    return f"${amount:,}"


reg("gang", "👥 View your gang info")
reg("gangs", "📋 Browse all gangs and members")


@client.on_message(filters.command(["gang"]))
async def gang_command(
    message: Message,
):
    """View your gang info."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Get user's gang
    user_gang = await db.get_user_gang(user.id)
    if not user_gang:
        await message.reply(
            "👥 <b>Gang System</b>\n\n"
            "You're not in a gang!\n\n"
            "• Use /create_gang &lt;name&gt; to create one ($20,000)\n"
            "• Use /join_gang to join an existing gang\n\n"
            "Usage: <code>/gang</code>"
        )
        return

    # Get gang members
    members = await db.get_gang_members(user_gang["id"])
    is_owner = await db.is_gang_owner(user.id, user_gang["id"])

    text = f"👥 <b>{html_escape(user_gang['name'])}</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += f"👑 Owner: ID {user_gang['owner_id']}\n"
    text += f"👥 Members: {len(members)}\n"
    text += f"📅 Created: {user_gang['created_at'].strftime('%Y-%m-%d')}\n\n"

    text += "<b>Members:</b>\n"
    for i, member in enumerate(members, 1):
        member_name = html_escape(
            member["first_name"] or member["username"] or "Unknown"
        )
        is_owner_marker = (
            " 👑" if member["user_id"] == user_gang["owner_id"] else ""
        )
        text += f"{i}. {member_name}{is_owner_marker}\n"

    text += "\n<b>Commands:</b>\n"
    text += "• /gangwar - Challenge another gang (gangster only)\n"

    if is_owner:
        text += "• /destroy_gang - Destroy your gang (3x confirmation)\n"

    await message.reply(text)


@client.on_message(filters.command(["gangs"]))
async def gangs_command(
    message: Message,
):
    """List all gangs and allow selecting one to view members."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    all_gangs = await db.fetch(
        """
        SELECT g.id, g.name, g.owner_id, g.created_at, COUNT(gm.user_id) as member_count
        FROM gangs g
        LEFT JOIN gang_members gm ON g.id = gm.gang_id
        GROUP BY g.id
        ORDER BY member_count DESC, g.created_at ASC
        """
    )

    if not all_gangs:
        await message.reply(
            "📋 <b>Gangs</b>\n\n"
            "No gangs exist yet.\n"
            "Use /create_gang &lt;name&gt; to create one."
        )
        return

    text = "📋 <b>All Gangs</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "Select a gang to view members:\n\n"

    buttons = []
    for gang in all_gangs:
        gang_name = html_escape(gang["name"])
        member_count = int(gang["member_count"] or 0)
        buttons.append([
            InlineKeyboardButton(
                text=f"{gang_name} ({member_count})",
                callback_data=f"gangs:view:{user.id}:{gang['id']}",
            )
        ])

    await message.reply(
        text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
    )


@client.on_callback_query(filters.regex(r"^" + "gangs:view:"))
async def gangs_view_callback(
    callback: CallbackQuery,
):
    """Show selected gang members."""
    parts = callback.data.split(":")
    if len(parts) != 4:
        await safe_callback_answer(callback)
        return

    owner_id = int(parts[2])
    gang_id = int(parts[3])

    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the user who opened this list can select a gang.",
            show_alert=True,
        )
        return

    gang = await db.get_gang(gang_id)
    if not gang:
        await safe_callback_answer(callback, "Gang not found.", show_alert=True)
        return

    members = await db.get_gang_members(gang_id)

    text = f"👥 <b>{html_escape(gang['name'])}</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += f"👑 Owner: ID {gang['owner_id']}\n"
    text += f"👥 Members: {len(members)}\n"
    text += f"📅 Created: {gang['created_at'].strftime('%Y-%m-%d')}\n\n"
    text += "<b>Member List:</b>\n"

    for i, member in enumerate(members, 1):
        member_name = html_escape(
            member["first_name"] or member["username"] or "Unknown"
        )
        owner_marker = " 👑" if member["user_id"] == gang["owner_id"] else ""
        text += f"{i}. {member_name}{owner_marker}\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Back to all gangs",
                    callback_data=f"gangs:list:{owner_id}",
                )
            ]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "gangs:list:"))
async def gangs_list_callback(
    callback: CallbackQuery,
):
    """Return to all gangs list."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        await safe_callback_answer(callback)
        return

    owner_id = int(parts[2])
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the user who opened this list can use this button.",
            show_alert=True,
        )
        return

    all_gangs = await db.fetch(
        """
        SELECT g.id, g.name, COUNT(gm.user_id) as member_count
        FROM gangs g
        LEFT JOIN gang_members gm ON g.id = gm.gang_id
        GROUP BY g.id
        ORDER BY member_count DESC, g.created_at ASC
        """
    )

    if not all_gangs:
        await queue_it(
            lambda: callback.message.edit_text(
                "📋 <b>Gangs</b>\n\nNo gangs exist yet.",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback)
        return

    text = "📋 <b>All Gangs</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "Select a gang to view members:\n\n"

    buttons = []
    for gang in all_gangs:
        gang_name = html_escape(gang["name"])
        member_count = int(gang["member_count"] or 0)
        buttons.append([
            InlineKeyboardButton(
                text=f"{gang_name} ({member_count})",
                callback_data=f"gangs:view:{owner_id}:{gang['id']}",
            )
        ])

    await queue_it(
        lambda: callback.message.edit_text(
            text, reply_markup=InlineKeyboardMarkup(inline_keyboard=buttons)
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


reg("create_gang", "🏴 Create a new gang")


@client.on_message(filters.command(["create_gang"]))
async def create_gang_command(
    message: Message,
):
    """Create a new gang."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if already in a gang
    user_gang = await db.get_user_gang(user.id)
    if user_gang:
        await message.reply(
            f"❌ You're already in the {html_escape(user_gang['name'])} gang!\n"
            f"Leave it first before creating a new one."
        )
        return

    # Get gang name from command
    text = message.text or ""
    parts = text.split()

    if len(parts) < 2:
        await message.reply(
            "🏴 <b>Create Gang</b>\n\n"
            f"Cost: {format_price(GANG_CREATION_FEE)}\n\n"
            "Usage: <code>/create_gang YourGangName</code>\n"
            "Example: <code>/create_gang Shadow Syndicate</code>"
        )
        return

    gang_name = " ".join(parts[1:])
    gang_name_escaped = html_escape(gang_name)

    # Check if gang name already exists
    existing_gang = await db.get_gang_by_name(gang_name)
    if existing_gang:
        await message.reply(
            f"❌ A gang named '{gang_name_escaped}' already exists!"
        )
        return

    # Check wallet balance
    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < GANG_CREATION_FEE:
        await message.reply(
            f"❌ Not enough money!\n"
            f"Cost: {format_price(GANG_CREATION_FEE)}\n"
            f"Your balance: {format_price(wallet['balance'])}"
        )
        return

    # Show 3-step confirmation
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1/3 - Continue",
                    callback_data=f"create_gang_confirm1:{user.id}:{gang_name}",
                )
            ]
        ]
    )

    await message.reply(
        f"🏴 <b>Create Gang: {gang_name_escaped}</b>\n\n"
        f"This will cost {format_price(GANG_CREATION_FEE)} from your wallet.\n"
        f"No refunds will be given.\n\n"
        f"Step 1/3: Do you want to continue?",
        reply_markup=keyboard,
    )


@client.on_callback_query(filters.regex(r"^" + "create_gang_confirm1:"))
async def create_gang_confirm1_callback(
    callback: CallbackQuery,
):
    """First confirmation step."""
    parts = callback.data.split(":", 2)
    owner_id = int(parts[1])

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    gang_name = parts[2]
    gang_name_escaped = html_escape(gang_name)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="2/3 - Continue",
                    callback_data=f"create_gang_confirm2:{owner_id}:{gang_name}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"create_gang_cancel:{owner_id}",
                ),
            ]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(
            f"🏴 <b>Create Gang: {gang_name_escaped}</b>\n\n"
            f"Step 2/3: Are you absolutely sure?\n"
            f"This action cannot be undone!",
            reply_markup=keyboard,
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "create_gang_confirm2:"))
async def create_gang_confirm2_callback(
    callback: CallbackQuery,
):
    """Second confirmation step."""
    parts = callback.data.split(":", 2)
    owner_id = int(parts[1])

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    gang_name = parts[2]
    gang_name_escaped = html_escape(gang_name)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="3/3 - CREATE",
                    callback_data=f"create_gang_confirm3:{owner_id}:{gang_name}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"create_gang_cancel:{owner_id}",
                ),
            ]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(
            f"🏴 <b>Create Gang: {gang_name_escaped}</b>\n\n"
            f"⚠️ FINAL WARNING ⚠️\n\n"
            f"Step 3/3: This is your last chance to cancel!\n"
            f"Once created, you cannot get your money back!",
            reply_markup=keyboard,
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "create_gang_confirm3:"))
async def create_gang_confirm3_callback(
    callback: CallbackQuery,
):
    """Third and final confirmation step."""
    parts = callback.data.split(":", 2)
    owner_id = int(parts[1])

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    gang_name = parts[2]
    gang_name_escaped = html_escape(gang_name)
    user_id = callback.from_user.id

    # Deduct money
    await db.add_balance(
        user_id, -GANG_CREATION_FEE, f"Gang creation: {gang_name}"
    )

    # Create gang
    gang_id = await db.create_gang(user_id, gang_name)

    await queue_it(
        lambda: callback.message.edit_text(
            f"🎉 <b>Gang Created!</b>\n\n"
            f"Your gang '{gang_name_escaped}' has been created!\n"
            f"💰 Paid: {format_price(GANG_CREATION_FEE)}\n"
            f"ID: {gang_id}\n\n"
            f"Use /gang to view your gang info.\n"
            f"Others can now /join_gang your gang!"
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Gang created!")


@client.on_callback_query(filters.regex(r"^" + "create_gang_cancel:"))
async def create_gang_cancel_callback(callback: CallbackQuery):
    """Cancel gang creation."""
    parts = callback.data.split(":")
    owner_id = int(parts[1])

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    await queue_it(
        lambda: callback.message.edit_text("❌ Gang creation cancelled."),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Cancelled.")


reg("join_gang", "🤝 Join a gang")


@client.on_message(filters.command(["join_gang"]))
async def join_gang_command(
    message: Message,
):
    """Join a gang."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if already in a gang
    user_gang = await db.get_user_gang(user.id)
    if user_gang:
        await message.reply(
            f"❌ You're already in the {html_escape(user_gang['name'])} gang!\n"
            f"Use /leave_gang to leave first."
        )
        return

    # Check if can join (UTC reset check)
    if not await db.can_join_gang(user.id):
        await message.reply(
            "❌ You left a gang today!\n"
            "You can join a new gang after UTC midnight reset."
        )
        return

    # Check if user replied to someone or mentioned someone
    target = None
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
    elif message.entities:
        for entity in message.entities:
            if entity.type == "text_mention" and entity.user:
                target = entity.user
                break

    if target:
        # Try to join the target's gang
        target_gang = await db.get_user_gang(target.id)
        if not target_gang:
            await message.reply(f"❌ {target.first_name} is not in a gang!")
            return

        # Join the gang
        await db.join_gang(target_gang["id"], user.id)

        await message.reply(
            f"🎉 <b>Joined Gang!</b>\n\n"
            f"You joined {html_escape(target_gang['name'])}!\n"
            f"Use /gang to view gang info."
        )
        return

    # Show list of gangs to join
    # Get all gangs with member counts
    all_gangs = await db.fetch(
        """
        SELECT g.*, COUNT(gm.user_id) as member_count
        FROM gangs g
        LEFT JOIN gang_members gm ON g.id = gm.gang_id
        GROUP BY g.id
        ORDER BY member_count DESC
        """
    )

    if not all_gangs:
        await message.reply(
            "❌ No gangs exist yet!\nUse /create_gang &lt;name&gt; to create one."
        )
        return

    text = "🤝 <b>Join a Gang</b>\n\n"
    text += "Select a gang to join:\n\n"

    buttons = []
    for gang in all_gangs:
        gang_name = html_escape(gang["name"])
        member_count = gang["member_count"]
        buttons.append([
            InlineKeyboardButton(
                text=f"{gang_name} ({member_count} members)",
                callback_data=f"join_gang_select:{gang['id']}",
            )
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.reply(text, reply_markup=keyboard)


@client.on_callback_query(filters.regex(r"^" + "join_gang_select:"))
async def join_gang_select_callback(
    callback: CallbackQuery,
):
    """Select a gang to join."""
    gang_id = int(callback.data.split(":")[1])
    user_id = callback.from_user.id

    # Get gang info
    gang = await db.get_gang(gang_id)
    if not gang:
        await callback.answer("Gang not found!", show_alert=True)
        return

    # Join the gang
    await db.join_gang(gang_id, user_id)

    await queue_it(
        lambda: callback.message.edit_text(
            f"🎉 <b>Joined Gang!</b>\n\n"
            f"You joined {html_escape(gang['name'])}!\n"
            f"Use /gang to view gang info."
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, f"Joined {html_escape(gang['name'])}!")


reg("leave_gang", "🚪 Leave your gang")


@client.on_message(filters.command(["leave_gang"]))
async def leave_gang_command(
    message: Message,
):
    """Leave your gang."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if in a gang
    user_gang = await db.get_user_gang(user.id)
    if not user_gang:
        await message.reply("❌ You're not in a gang!")
        return

    # Check if owner
    is_owner = await db.is_gang_owner(user.id, user_gang["id"])
    if is_owner:
        await message.reply(
            "❌ You can't leave your own gang!\n"
            "Use /destroy_gang to disband it instead."
        )
        return

    # Show confirmation
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Leave Gang",
                    callback_data="leave_gang_confirm",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="leave_gang_cancel",
                ),
            ]
        ]
    )

    await message.reply(
        f"🚪 <b>Leave Gang</b>\n\n"
        f"Are you sure you want to leave {html_escape(user_gang['name'])}?\n"
        f"<i>You won't be able to join a new gang until UTC midnight reset.</i>",
        reply_markup=keyboard,
    )


@client.on_callback_query(filters.regex(r"^" + "leave_gang_confirm" + r"$"))
async def leave_gang_confirm_callback(
    callback: CallbackQuery,
):
    """Confirm leaving gang."""
    user_id = callback.from_user.id

    # Leave the gang
    await db.leave_gang(user_id)

    await queue_it(
        lambda: callback.message.edit_text(
            "👋 <b>Left Gang!</b>\n\n"
            "You've left your gang.\n"
            "You can join a new gang after UTC midnight reset."
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Left gang!")


@client.on_callback_query(filters.regex(r"^" + "leave_gang_cancel" + r"$"))
async def leave_gang_cancel_callback(callback: CallbackQuery):
    """Cancel leaving gang."""
    await queue_it(
        lambda: callback.message.edit_text("❌ Cancelled."),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Cancelled.")


reg("destroy_gang", "💀 Destroy your gang")


@client.on_message(filters.command(["destroy_gang"]))
async def destroy_gang_command(
    message: Message,
):
    """Destroy your gang."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if in a gang
    user_gang = await db.get_user_gang(user.id)
    if not user_gang:
        await message.reply("❌ You're not in a gang!")
        return

    # Check if owner
    is_owner = await db.is_gang_owner(user.id, user_gang["id"])
    if not is_owner:
        await message.reply("❌ Only the gang owner can destroy the gang!")
        return

    # Show 3-step confirmation
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="1/3 - Continue",
                    callback_data=f"destroy_gang_confirm1:{user.id}:{user_gang['id']}",
                )
            ]
        ]
    )

    await message.reply(
        f"💀 <b>Destroy Gang: {html_escape(user_gang['name'])}</b>\n\n"
        f"This will permanently destroy your gang!\n"
        f"All members will be removed.\n"
        f"No refunds will be given.\n\n"
        f"Step 1/3: Do you want to continue?",
        reply_markup=keyboard,
    )


@client.on_callback_query(filters.regex(r"^" + "destroy_gang_confirm1:"))
async def destroy_gang_confirm1_callback(
    callback: CallbackQuery,
):
    """First confirmation step."""
    parts = callback.data.split(":")
    owner_id = int(parts[1])
    gang_id = int(parts[2])

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    gang = await db.get_gang(gang_id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="2/3 - Continue",
                    callback_data=f"destroy_gang_confirm2:{owner_id}:{gang_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"destroy_gang_cancel:{owner_id}",
                ),
            ]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(
            f"💀 <b>Destroy Gang: {html_escape(gang['name'])}</b>\n\n"
            f"Step 2/3: Are you absolutely sure?\n"
            f"This action cannot be undone!",
            reply_markup=keyboard,
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "destroy_gang_confirm2:"))
async def destroy_gang_confirm2_callback(
    callback: CallbackQuery,
):
    """Second confirmation step."""
    parts = callback.data.split(":")
    owner_id = int(parts[1])
    gang_id = int(parts[2])

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    gang = await db.get_gang(gang_id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="3/3 - DESTROY",
                    callback_data=f"destroy_gang_confirm3:{owner_id}:{gang_id}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data=f"destroy_gang_cancel:{owner_id}",
                ),
            ]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(
            f"💀 <b>Destroy Gang: {html_escape(gang['name'])}</b>\n\n"
            f"⚠️ FINAL WARNING ⚠️\n\n"
            f"Step 3/3: This is your last chance to cancel!\n"
            f"Once destroyed, your gang will be gone forever!",
            reply_markup=keyboard,
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "destroy_gang_confirm3:"))
async def destroy_gang_confirm3_callback(
    callback: CallbackQuery,
):
    """Third and final confirmation step."""
    parts = callback.data.split(":")
    owner_id = int(parts[1])
    gang_id = int(parts[2])

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    gang = await db.get_gang(gang_id)

    # Destroy the gang
    await db.destroy_gang(gang_id)

    await queue_it(
        lambda: callback.message.edit_text(
            f"💀 <b>Gang Destroyed!</b>\n\n"
            f"The gang '{html_escape(gang['name'])}' has been destroyed.\n"
            f"All members have been removed.\n"
            f"No refund was given."
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Gang destroyed!")


@client.on_callback_query(filters.regex(r"^" + "destroy_gang_cancel:"))
async def destroy_gang_cancel_callback(callback: CallbackQuery):
    """Cancel gang destruction."""
    parts = callback.data.split(":")
    owner_id = int(parts[1])

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    await queue_it(
        lambda: callback.message.edit_text("❌ Gang destruction cancelled."),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Cancelled.")
