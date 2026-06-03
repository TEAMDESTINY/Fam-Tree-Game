"""Callback query handlers for confirmation buttons."""

import logging
import re
from functools import wraps

from pyrogram import Client as Bot
from pyrogram import filters
from pyrogram.errors import BadRequest, FloodWait
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)

from bot.client import client
from bot.constants import RIPPLE_MULTIPLIER
from bot.database import Database
from bot.input_file import to_input_file
from bot.plugins.family import (
    build_adopt_conflict_message,
    build_makeparent_conflict_message,
    build_sibling_conflict_message,
    send_direct_family_explorer,
    send_full_family_explorer,
    user_display_name,
)
from bot.plugins.gambling import (
    build_chat_message_link,
    format_lottery_participants,
)
from bot.queue_it import queue_it
from bot.utils import format_family_error_message
from bot.database import db

logger = logging.getLogger(__name__)

# Track which games are currently being processed to reject concurrent clicks
_processing_games: set[int] = set()
_processing_callback_actions: set[str] = set()


async def safe_callback_answer(
    callback: CallbackQuery, text: str = None, show_alert: bool = False
):
    """Safely answer a callback, handling timeout errors."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except FloodWait as e:
        # Flood control - show alert with retry time
        retry_after = int(e.value) if hasattr(e, "value") else 5
        await callback.answer(
            f"⏳ Go slow! Wait {retry_after}s. Floodwait!!",
            show_alert=True,
        )
    except BadRequest:
        # Callback query is too old or already answered
        pass


async def safe_callback_edit(
    bot: Bot, callback: CallbackQuery, *args, **kwargs
):
    # Pyrogram's CallbackQuery._parse catches ChannelPrivate and builds a stub
    # Message without `client=`, so Message.edit_text crashes on _client=None.
    # Going through the bot client directly sidesteps that stub.
    msg = callback.message
    if msg is None or msg.chat is None:
        return None
    return await bot.edit_message_text(msg.chat.id, msg.id, *args, **kwargs)


def with_callback_action_lock(handler):
    return handler  # TODO: remove this and open lock decorator again

    @wraps(handler)
    async def wrapped(callback: CallbackQuery, *args, **kwargs):
        if not callback.message:
            return await handler(callback, *args, **kwargs)

        lock_key = (
            f"{callback.message.chat.id}:"
            f"{callback.message.id}:"
            f"{callback.from_user.id}"
        )

        if lock_key in _processing_callback_actions:
            await safe_callback_answer(
                callback,
                "Please wait for old button action to finish 😅",
                show_alert=True,
            )
            return None

        _processing_callback_actions.add(lock_key)

        try:
            return await handler(callback, *args, **kwargs)
        finally:
            _processing_callback_actions.discard(lock_key)

    return wrapped


@client.on_callback_query(
    filters.regex(r"^adopt_(accept|reject|cancel):(\d+)$")
)
@with_callback_action_lock
async def handle_adopt_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle adoption accept/reject/cancel callbacks."""
    match = re.match(r"^adopt_(accept|reject|cancel):(\d+)$", callback.data)
    action = match.group(1)
    request_id = int(match.group(2))

    request = await db.get_pending_request_by_id(request_id)

    if not request:
        await safe_callback_answer(
            callback, "This request has expired.", show_alert=True
        )
        return

    # Only the target can accept/reject, only the requester can cancel
    if action in ("accept", "reject"):
        if callback.from_user.id != request["target_id"]:
            await safe_callback_answer(
                callback, "You can't perform this action!", show_alert=True
            )
            return
    else:  # cancel
        if callback.from_user.id != request["requester_id"]:
            await safe_callback_answer(
                callback, "You can't perform this action!", show_alert=True
            )
            return

    if action == "cancel":
        await db.delete_pending_request(request_id)
        requester = await db.get_user(request["requester_id"])
        target = await db.get_user(request["target_id"])
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ {user_display_name(requester)} cancelled the adoption request to "
                f"{user_display_name(target)}.",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Request cancelled.")
        return

    if action == "reject":
        await db.delete_pending_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot, callback, "❌ Adoption request rejected."
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Adoption rejected.")
        return

    # Accept adoption
    parent_id = request["requester_id"]
    child_id = request["target_id"]

    # Re-validate strict adopt rules in case relationships changed.
    if await db.is_adopt_hierarchy_conflict(parent_id, child_id):
        await db.delete_pending_request(request_id)
        base = await build_adopt_conflict_message(db, parent_id, child_id)
        error_msg = await format_family_error_message(
            db, parent_id, child_id, "adopt"
        )
        await queue_it(
            lambda: safe_callback_edit(bot, callback, f"{base}\n\n{error_msg}"),
            callback.message.chat,
        )
        await safe_callback_answer(
            callback, "Cannot complete adoption.", show_alert=True
        )
        return

    # Check if target is already adopted by this parent
    children = await db.get_children(parent_id)
    if any(c["user_id"] == child_id for c in children):
        await db.delete_pending_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(bot, callback, "❌ Already adopted!"),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Already your child!")
        return

    # Create the relationship
    await db.add_adoption(parent_id, child_id)

    # Add currency rewards
    await db.add_balance(parent_id, 500, "Adopted someone")
    await db.add_balance(child_id, 1000, "Got adopted")

    # Delete the request
    await db.delete_pending_request(request_id)

    parent = await db.get_user(parent_id)
    child = await db.get_user(child_id)

    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"🎉 <b>Adoption Complete!</b>\n\n"
            f"👨‍👧 {user_display_name(parent)} is now {user_display_name(child)}'s parent!\n\n"
            f"💰 {user_display_name(parent)} earned $500\n"
            f"💰 {user_display_name(child)} earned $1,000",
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "You've been adopted! 🎉")

    # Check achievements for parent
    from bot.achievements import (
        check_adoption_achievements,
        check_family_achievements,
    )

    await check_adoption_achievements(db, parent_id, bot, request["chat_id"])
    await check_family_achievements(db, parent_id, bot, request["chat_id"])
    await check_family_achievements(db, child_id, bot, request["chat_id"])


@client.on_callback_query(
    filters.regex(r"^marry_(accept|reject|cancel):(\d+)$")
)
@with_callback_action_lock
async def handle_marry_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle marriage accept/reject/cancel callbacks."""
    # Answer callback early to prevent timeout during long operations
    await safe_callback_answer(callback)

    match = re.match(r"^marry_(accept|reject|cancel):(\d+)$", callback.data)
    action = match.group(1)
    request_id = int(match.group(2))

    request = await db.get_pending_request_by_id(request_id)

    if not request:
        await safe_callback_answer(
            callback, "This request has expired.", show_alert=True
        )
        return

    # Only the target can accept/reject, only the requester can cancel
    if action in ("accept", "reject"):
        if callback.from_user.id != request["target_id"]:
            await safe_callback_answer(
                callback, "You can't perform this action!", show_alert=True
            )
            return
    else:  # cancel
        if callback.from_user.id != request["requester_id"]:
            await safe_callback_answer(
                callback, "You can't perform this action!", show_alert=True
            )
            return

    if action == "cancel":
        await db.delete_pending_request(request_id)
        requester = await db.get_user(request["requester_id"])
        target = await db.get_user(request["target_id"])
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ {user_display_name(requester)} cancelled the marriage proposal to "
                f"{user_display_name(target)}.",
            ),
            callback.message.chat,
        )
        return

    if action == "reject":
        await db.delete_pending_request(request_id)
        requester = await db.get_user(request["requester_id"])
        target = await db.get_user(request["target_id"])
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"💔 {user_display_name(target)} rejected the marriage proposal from "
                f"{user_display_name(requester)}.",
            ),
            callback.message.chat,
        )
        return

    # Accept marriage
    user1_id = request["requester_id"]
    user2_id = request["target_id"]

    # Re-validate before accepting - check if either is already married
    user1_spouses = await db.get_spouses(user1_id)
    user2_spouses = await db.get_spouses(user2_id)

    if user1_spouses:
        await db.delete_pending_request(request_id)
        user1 = await db.get_user(user1_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ Cannot marry - {user_display_name(user1)} is already married!",
            ),
            callback.message.chat,
        )
        return

    if user2_spouses:
        await db.delete_pending_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                "❌ Cannot marry - you are already married! Use /divorce first.",
            ),
            callback.message.chat,
        )
        return

    # Check if already married to each other
    if await db.are_married(user1_id, user2_id):
        await db.delete_pending_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot, callback, "❌ You're already married to each other!"
            ),
            callback.message.chat,
        )
        return

    # Check if this is a remarriage (based on marriage history, not current spouses)
    user1_marriages = await db.get_marriage_count(user1_id)
    user2_marriages = await db.get_marriage_count(user2_id)
    is_remarriage = user1_marriages > 0 or user2_marriages > 0

    # Create the marriage
    await db.add_marriage(user1_id, user2_id)

    # Add currency rewards
    await db.add_balance(user1_id, 2000, "Got married")
    await db.add_balance(user2_id, 2000, "Got married")

    # Delete the request
    await db.delete_pending_request(request_id)

    user1 = await db.get_user(user1_id)
    user2 = await db.get_user(user2_id)

    # Get marriage quote
    quote = await db.get_random_marriage_quote(is_remarriage)

    # Update the message
    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"💒 <b>Just Married!</b>\n\n"
            f"💑 {user_display_name(user1)} and {user_display_name(user2)} are now married!\n\n"
            f'*"{quote}"*\n\n'
            f"💰 Both partners earned $2,000!",
        ),
        callback.message.chat,
    )

    # Check achievements for both users
    from bot.achievements import check_marriage_achievements

    await check_marriage_achievements(db, user1_id, bot, request["chat_id"])
    await check_marriage_achievements(db, user2_id, bot, request["chat_id"])

    # Send marriage notifications
    await send_marriage_notifications(
        bot, db, user1_id, user2_id, user1, user2, quote, request["chat_id"]
    )


async def send_marriage_notifications(
    bot: Bot,
    db: Database,
    user1_id: int,
    user2_id: int,
    user1,
    user2,
    quote: str,
    chat_id: int,
):
    """Send marriage notifications to family and friends."""
    # Get close family for both users
    family1 = await db.get_close_family(user1_id)
    family2 = await db.get_close_family(user2_id)

    # Get friends of both users
    friends1 = await db.get_friends(user1_id)
    friends2 = await db.get_friends(user2_id)

    # Collect all people to notify (excluding the couple themselves)
    notify_ids = set()
    for f in family1["parents"] + family1["children"] + family1["siblings"]:
        notify_ids.add(f["user_id"])
    for f in family2["parents"] + family2["children"] + family2["siblings"]:
        notify_ids.add(f["user_id"])
    for f in friends1:
        notify_ids.add(f["user_id"])
    for f in friends2:
        notify_ids.add(f["user_id"])

    # Remove the couple from notification list
    notify_ids.discard(user1_id)
    notify_ids.discard(user2_id)

    # Try to generate and send marriage card in the original chat first
    try:
        from bot.graphics.marriage_card import render_marriage_card

        card_bytes = await render_marriage_card(
            bot, db, user1_id, user2_id, quote
        )

        if card_bytes:
            # Send marriage card in the chat where proposal was initiated
            try:
                partner1_name = user_display_name(user1)
                partner2_name = user_display_name(user2)
                await bot.send_photo(
                    chat_id,
                    photo=to_input_file(
                        card_bytes, filename="marriage_card.png"
                    ),
                    caption=f'💒 <b>Just Married!</b>\n\n💑 {partner1_name} and {partner2_name} are now married!\n\n<i>"{quote}"</i>',
                )
            except Exception:
                pass

            # Send to both partners via PM
            for uid in [user1_id, user2_id]:
                try:
                    await bot.send_photo(
                        uid,
                        photo=to_input_file(
                            card_bytes, filename="marriage_card.png"
                        ),
                        caption=f'💒 <b>Congratulations on your marriage!</b>\n\n<i>"{quote}"</i>',
                    )
                except Exception:
                    pass

            # Send to family and friends via PM
            for uid in notify_ids:
                try:
                    partner1_name = user_display_name(user1)
                    partner2_name = user_display_name(user2)
                    await bot.send_photo(
                        uid,
                        photo=to_input_file(
                            card_bytes, filename="marriage_card.png"
                        ),
                        caption=f"💑 <b>{partner1_name} and {partner2_name} just got married!</b>",
                    )
                except Exception:
                    pass
    except Exception:
        pass

    # Build family list for group message
    family_names = []
    for f in family1["parents"] + family1["children"] + family1["siblings"]:
        if f["user_id"] in notify_ids:
            family_names.append(user_display_name(f))
    for f in family2["parents"] + family2["children"] + family2["siblings"]:
        if (
            f["user_id"] in notify_ids
            and user_display_name(f) not in family_names
        ):
            family_names.append(user_display_name(f))

    # Send group announcement if there's family
    if family_names:
        try:
            family_list = ", ".join(family_names[:10])  # Limit to 10 names
            if len(family_names) > 10:
                family_list += f" and {len(family_names) - 10} more"

            await bot.send_message(
                chat_id,
                f"💒 <b>Wedding Announcement</b>\n\n"
                f"💑 {user_display_name(user1)} and {user_display_name(user2)} "
                f"just got married!\n\n"
                f"👨‍👩‍👧‍👦 Family invited: {family_list}",
            )
        except Exception:
            pass


@client.on_callback_query(filters.regex(r"^disown:(\d+):(\d+|cancel)$"))
async def handle_disown_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle disown selection callbacks."""
    from bot.utils import db_user_mention

    match = re.match(r"^disown:(\d+):(\d+|cancel)$", callback.data)
    owner_id = int(match.group(1))
    value = match.group(2)

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    if value == "cancel":
        await queue_it(
            lambda: safe_callback_edit(bot, callback, "✅ Disown cancelled."),
            callback.message.chat,
        )
        await safe_callback_answer(callback)
        return

    child_id = int(value)
    parent_id = callback.from_user.id

    # Verify this is their child
    children = await db.get_children(parent_id)
    if not any(c["user_id"] == child_id for c in children):
        await safe_callback_answer(
            callback, "This is not your child!", show_alert=True
        )
        return

    # Remove the relationship
    await db.remove_adoption(parent_id, child_id)

    child = await db.get_user(child_id)
    parent = await db.get_user(parent_id)

    await queue_it(
        lambda: safe_callback_edit(
            bot, callback, f"💔 You have disowned {user_display_name(child)}."
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Disowned.")

    # Send DM notification to the affected child
    try:
        parent_mention = db_user_mention(parent)
        await bot.send_message(
            child_id,
            f"💔 <b>You have been disowned</b>\n\n"
            f"{parent_mention} has disowned you as their child.",
        )
    except Exception:
        pass  # User may have blocked the bot or not started it


@client.on_callback_query(filters.regex(r"^divorce:(\d+):(\d+|cancel)$"))
async def handle_divorce_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle divorce selection callbacks."""

    match = re.match(r"^divorce:(\d+):(\d+|cancel)$", callback.data)
    owner_id = int(match.group(1))
    value = match.group(2)

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    if value == "cancel":
        await queue_it(
            lambda: safe_callback_edit(bot, callback, "✅ Divorce cancelled."),
            callback.message.chat,
        )
        await safe_callback_answer(callback)
        return

    spouse_id = int(value)
    user_id = callback.from_user.id

    # Verify this is their spouse
    spouses = await db.get_spouses(user_id)
    if not any(s["user_id"] == spouse_id for s in spouses):
        await safe_callback_answer(
            callback, "This is not your spouse!", show_alert=True
        )
        return

    # If the couple co-adopted any children, divorce only removes them
    # from the initiator's tree — spouse keeps them. Surface that before
    # confirming so the initiator knows what they're agreeing to.
    user_children = await db.get_children(user_id)
    spouse_children_ids = {
        c["user_id"] for c in await db.get_children(spouse_id)
    }
    shared = [c for c in user_children if c["user_id"] in spouse_children_ids]
    if shared:
        names = ", ".join(user_display_name(c) for c in shared)
        spouse_user = await db.get_user(spouse_id)
        spouse_name = (
            user_display_name(spouse_user) if spouse_user else "your spouse"
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💔 Yes, divorce",
                        callback_data=f"divorce_confirm:{user_id}:{spouse_id}",
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel",
                        callback_data=f"divorce:{user_id}:cancel",
                    ),
                ]
            ]
        )
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"⚠️ <b>Shared children</b>\n\n"
                f"You and {spouse_name} co-adopted: <b>{names}</b>\n"
                f"Divorcing will remove these children from <b>your</b> tree. "
                f"{spouse_name} keeps them.\n\n"
                f"Proceed?",
                reply_markup=keyboard,
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback)
        return

    # No shared children — proceed directly with divorce
    await _execute_divorce(callback, bot, db, user_id, spouse_id)


@client.on_callback_query(filters.regex(r"^divorce_confirm:(\d+):(\d+)$"))
async def handle_divorce_confirm_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle divorce confirmation after shared children warning."""
    match = re.match(r"^divorce_confirm:(\d+):(\d+)$", callback.data)
    owner_id = int(match.group(1))
    spouse_id = int(match.group(2))

    # Only the initiator can interact
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who initiated this can interact!",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id

    # Verify this is still their spouse
    spouses = await db.get_spouses(user_id)
    if not any(s["user_id"] == spouse_id for s in spouses):
        await safe_callback_answer(
            callback, "This is not your spouse!", show_alert=True
        )
        return

    await _execute_divorce(callback, bot, db, user_id, spouse_id)


async def _execute_divorce(
    callback: CallbackQuery,
    bot: Bot,
    db: Database,
    user_id: int,
    spouse_id: int,
):
    """Execute the divorce - initiator gives up shared kids; spouse keeps them."""
    from bot.utils import db_user_mention

    # Get each person's children and determine who adopted them
    user_children = await db.get_children(user_id)
    spouse_children = await db.get_children(spouse_id)

    # Find shared children (both are parents) and personal children
    shared_children = []
    user_only_children = []
    spouse_only_children = []

    for uc in user_children:
        is_shared = any(
            sc["user_id"] == uc["user_id"] for sc in spouse_children
        )
        if is_shared:
            shared_children.append(uc)
        else:
            user_only_children.append(uc)

    for sc in spouse_children:
        if not any(uc["user_id"] == sc["user_id"] for uc in user_children):
            spouse_only_children.append(sc)

    # Remove shared children from the INITIATOR's tree only. The spouse
    # keeps their adoption row, so the child is still parented by them.
    for child in shared_children:
        await db.remove_adoption(user_id, child["user_id"])

    # Remove the marriage
    await db.remove_marriage(user_id, spouse_id)

    spouse = await db.get_user(spouse_id)
    initiator = await db.get_user(user_id)

    text = f"💔 You have divorced {user_display_name(spouse)}."
    if shared_children:
        children_names = ", ".join(
            user_display_name(c) for c in shared_children
        )
        text += (
            f"\n\n👶 Shared child(ren) removed from your tree "
            f"(kept by {user_display_name(spouse)}): {children_names}"
        )
    if user_only_children:
        children_names = ", ".join(
            user_display_name(c) for c in user_only_children
        )
        text += f"\n\n👨‍👧 Your child(ren): {children_names}"

    await queue_it(
        lambda: safe_callback_edit(bot, callback, text), callback.message.chat
    )
    await safe_callback_answer(callback, "Divorced.")

    # Send DM notification to the affected spouse
    try:
        initiator_mention = db_user_mention(initiator)
        dm_text = (
            f"💔 <b>You have been divorced</b>\n\n"
            f"{initiator_mention} has divorced you."
        )
        if shared_children:
            children_names = "\n".join(
                f"• {user_display_name(c)}" for c in shared_children
            )
            dm_text += (
                f"\n\n👶 Shared child(ren) still yours "
                f"({user_display_name(initiator)} gave them up):\n{children_names}"
            )
        if spouse_only_children:
            children_names = "\n".join(
                f"• {user_display_name(c)}" for c in spouse_only_children
            )
            dm_text += f"\n\n👨‍👧 Your child(ren):\n{children_names}"
        await bot.send_message(spouse_id, dm_text)
    except Exception:
        pass  # User may have blocked the bot or not started it


@client.on_callback_query(filters.regex(r"^fam_direct:(\d+):(\d+)$"))
async def handle_direct_family_nav_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle direct family explorer navigation callbacks."""
    match = re.match(r"^fam_direct:(\d+):(\d+)$", callback.data)
    owner_id = int(match.group(1))
    target_id = int(match.group(2))

    # Only the user who sent /family can navigate
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who sent /family can navigate.",
            show_alert=True,
        )
        return

    target = await db.get_user(target_id)
    if not target:
        await safe_callback_answer(callback, "User not found.", show_alert=True)
        return

    await send_direct_family_explorer(
        callback.message,
        db,
        target_id,
        user_display_name(target),
        edit=True,
        owner_id=owner_id,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^fam_nav:(\d+):(\d+)$"))
async def handle_family_nav_callback_legacy(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle legacy fam_nav callbacks for backward compatibility (treat as direct family)."""
    match = re.match(r"^fam_nav:(\d+):(\d+)$", callback.data)
    owner_id = int(match.group(1))
    target_id = int(match.group(2))

    # Only the user who sent /family can navigate
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who sent /family can navigate.",
            show_alert=True,
        )
        return

    target = await db.get_user(target_id)
    if not target:
        await safe_callback_answer(callback, "User not found.", show_alert=True)
        return

    await send_direct_family_explorer(
        callback.message,
        db,
        target_id,
        user_display_name(target),
        edit=True,
        owner_id=owner_id,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^fam_full:(\d+):(\d+)$"))
async def handle_full_family_nav_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle full family explorer navigation callbacks."""
    match = re.match(r"^fam_full:(\d+):(\d+)$", callback.data)
    owner_id = int(match.group(1))
    target_id = int(match.group(2))

    # Only the user who sent /fullfamily can navigate
    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who sent /fullfamily can navigate.",
            show_alert=True,
        )
        return

    target = await db.get_user(target_id)
    if not target:
        await safe_callback_answer(callback, "User not found.", show_alert=True)
        return

    await send_full_family_explorer(
        callback.message,
        db,
        target_id,
        user_display_name(target),
        edit=True,
        owner_id=owner_id,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(
    filters.regex(r"^friend_(accept|reject|cancel):(\d+)$")
)
@with_callback_action_lock
async def handle_friend_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle friend request accept/reject/cancel callbacks."""
    match = re.match(r"^friend_(accept|reject|cancel):(\d+)$", callback.data)
    action = match.group(1)
    requester_id = int(match.group(2))

    target_id = callback.from_user.id

    # Verify request exists
    request = await db.get_friend_request(requester_id, target_id)
    if not request:
        await safe_callback_answer(
            callback, "This request no longer exists.", show_alert=True
        )
        return

    # Only the target can accept/reject, only the requester can cancel
    if action in ("accept", "reject"):
        if requester_id == target_id:
            await safe_callback_answer(
                callback,
                "You can't respond to your own request!",
                show_alert=True,
            )
            return
    else:  # cancel
        if requester_id != target_id:
            await safe_callback_answer(
                callback, "You can't perform this action!", show_alert=True
            )
            return

    requester = await db.get_user(requester_id)

    if action == "cancel":
        await db.delete_friend_request(requester_id, target_id)
        target = await db.get_user(target_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ {user_display_name(requester)} cancelled the friend request to {user_display_name(target)}.",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Request cancelled.")
        return

    if action == "reject":
        await db.delete_friend_request(requester_id, target_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ You rejected the friend request from {user_display_name(requester)}.",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Request rejected.")
        return

    # Accept - create friendship
    await db.add_friendship(requester_id, target_id)
    await db.delete_friend_request(requester_id, target_id)

    # Add currency rewards
    await db.add_balance(requester_id, 3000, "New friendship")
    await db.add_balance(target_id, 3000, "New friendship")

    target = await db.get_user(target_id)

    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"🎉 <b>New Friendship!</b>\n\n"
            f"🤝 {user_display_name(requester)} and {user_display_name(target)} are now friends!\n\n"
            f"💰 Both earned $3,000!",
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "You're now friends! 🎉")

    from bot.achievements import (
        check_friend_achievements,
        check_money_achievements,
    )

    await check_friend_achievements(
        db, requester_id, bot, callback.message.chat.id
    )
    await check_friend_achievements(
        db, target_id, bot, callback.message.chat.id
    )
    await check_money_achievements(
        db, requester_id, bot, callback.message.chat.id
    )
    await check_money_achievements(db, target_id, bot, callback.message.chat.id)


@client.on_callback_query(filters.regex(r"^unfriend_page:(\d+):(\d+)$"))
async def handle_unfriend_page_callback(callback: CallbackQuery, bot: Bot):
    """Handle unfriend list pagination."""
    from bot.plugins.friends import build_unfriend_keyboard, UNFRIEND_PAGE_SIZE

    match = re.match(r"^unfriend_page:(\d+):(\d+)$", callback.data)
    owner_id = int(match.group(1))
    page = int(match.group(2))

    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback, "This menu isn't yours!", show_alert=True
        )
        return

    friends = await db.get_friends(owner_id)
    total = len(friends)
    if not friends:
        await safe_callback_answer(
            callback, "No friends found.", show_alert=True
        )
        return

    keyboard = build_unfriend_keyboard(friends, owner_id, page)
    page_info = f" ({page + 1}/{(total - 1) // UNFRIEND_PAGE_SIZE + 1})"
    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"😢 <b>Select a friend to remove{page_info}:</b>\n"
            "⚠️ (Warning: You will both lose $3,000)",
            reply_markup=keyboard,
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^unfriend:(\d+|cancel)$"))
async def handle_unfriend_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle unfriend selection callbacks."""
    from bot.utils import db_user_mention

    match = re.match(r"^unfriend:(\d+|cancel)$", callback.data)
    value = match.group(1)

    if value == "cancel":
        await queue_it(
            lambda: safe_callback_edit(bot, callback, "✅ Unfriend cancelled."),
            callback.message.chat,
        )
        await safe_callback_answer(callback)
        return

    friend_id = int(value)
    user_id = callback.from_user.id

    # Verify they're friends
    if not await db.are_friends(user_id, friend_id):
        await safe_callback_answer(
            callback, "You're not friends with this person!", show_alert=True
        )
        return

    # Remove friendship
    await db.remove_friendship(user_id, friend_id)

    # Deduct currency
    await db.add_balance(user_id, -3000, "Unfriended someone")
    await db.add_balance(friend_id, -3000, "Got unfriended")

    friend = await db.get_user(friend_id)
    initiator = await db.get_user(user_id)

    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"😢 You are no longer friends with {user_display_name(friend)}.\n"
            f"💸 You lost $3,000.",
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Unfriended.")

    # Send DM notification to the affected friend
    try:
        initiator_mention = db_user_mention(initiator)
        await bot.send_message(
            friend_id,
            f"😢 <b>Friendship ended</b>\n\n"
            f"{initiator_mention} has unfriended you.\n"
            f"💸 You lost $3,000.",
        )
    except Exception:
        pass  # User may have blocked the bot or not started it


@client.on_callback_query(
    filters.regex(r"^siblings_(accept|reject|cancel):(\d+)$")
)
@with_callback_action_lock
async def handle_siblings_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle sibling request accept/reject/cancel callbacks."""
    match = re.match(r"^siblings_(accept|reject|cancel):(\d+)$", callback.data)
    action = match.group(1)
    request_id = int(match.group(2))

    request = await db.get_pending_request_by_id(request_id)

    if not request:
        await safe_callback_answer(
            callback, "This request has expired.", show_alert=True
        )
        return

    # Only the target can accept/reject, only the requester can cancel
    if action in ("accept", "reject"):
        if callback.from_user.id != request["target_id"]:
            await safe_callback_answer(
                callback, "You can't perform this action!", show_alert=True
            )
            return
    else:  # cancel
        if callback.from_user.id != request["requester_id"]:
            await safe_callback_answer(
                callback, "You can't perform this action!", show_alert=True
            )
            return

    requester = await db.get_user(request["requester_id"])
    target = await db.get_user(request["target_id"])

    if action == "cancel":
        await db.delete_pending_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ {user_display_name(requester)} cancelled the sibling request to "
                f"{user_display_name(target)}.",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Request cancelled.")
        return

    if action == "reject":
        await db.delete_pending_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ {user_display_name(target)} rejected the sibling request from "
                f"{user_display_name(requester)}.",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Request rejected.")
        return

    # Accept - create sibling relationship
    # Re-validate before accepting (in case relationships changed)
    requester_id = request["requester_id"]
    target_id = request["target_id"]

    # Check if already siblings
    if await db.are_siblings(requester_id, target_id):
        await db.delete_pending_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                "❌ This sibling request cannot proceed - you are already siblings!",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(
            callback, "Already siblings!", show_alert=True
        )
        return

    # Re-validate sibling rule in case relationships changed.
    if await db.is_sibling_hierarchy_conflict(requester_id, target_id):
        await db.delete_pending_request(request_id)
        base = await build_sibling_conflict_message(db, requester_id, target_id)
        error_msg = await format_family_error_message(
            db, requester_id, target_id, "be siblings with"
        )
        await queue_it(
            lambda: safe_callback_edit(bot, callback, f"{base}\n\n{error_msg}"),
            callback.message.chat,
        )
        await safe_callback_answer(
            callback, "Cannot become siblings.", show_alert=True
        )
        return

    await db.add_sibling(requester_id, target_id)

    # Add currency rewards
    await db.add_balance(request["requester_id"], 1000, "New sibling")
    await db.add_balance(request["target_id"], 1000, "New sibling")

    # Delete the request
    await db.delete_pending_request(request_id)

    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"🎉 <b>New Siblings!</b>\n\n"
            f"👫 {user_display_name(requester)} and {user_display_name(target)} are now siblings!\n\n"
            f"💰 Both earned $1,000!",
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "You're now siblings! 🎉")


@client.on_callback_query(filters.regex(r"^removesibling:(\d+|cancel)$"))
async def handle_removesibling_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle remove sibling selection callbacks."""
    match = re.match(r"^removesibling:(\d+|cancel)$", callback.data)
    value = match.group(1)

    if value == "cancel":
        await queue_it(
            lambda: safe_callback_edit(
                bot, callback, "✅ Sibling removal cancelled."
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback)
        return

    sibling_id = int(value)
    user_id = callback.from_user.id

    # Verify they have a direct sibling relationship
    if not await db.is_direct_sibling(user_id, sibling_id):
        await safe_callback_answer(
            callback,
            "You don't have a removable sibling relationship!",
            show_alert=True,
        )
        return

    # Remove sibling relationship
    await db.remove_sibling(user_id, sibling_id)

    sibling = await db.get_user(sibling_id)
    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"👋 You are no longer siblings with {user_display_name(sibling)}.",
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Sibling removed.")


@client.on_callback_query(filters.regex(r"^makeparent_(accept|reject):(\d+)$"))
@with_callback_action_lock
async def handle_makeparent_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle makeparent request accept/reject callbacks."""
    match = re.match(r"^makeparent_(accept|reject):(\d+)$", callback.data)
    action = match.group(1)
    request_id = int(match.group(2))

    request = await db.get_pending_request_by_id(request_id)

    if not request:
        await safe_callback_answer(
            callback, "This request has expired.", show_alert=True
        )
        return

    # Only the target (prospective parent) can respond
    if callback.from_user.id != request["target_id"]:
        await safe_callback_answer(
            callback, "You can't perform this action!", show_alert=True
        )
        return

    child_id = request["requester_id"]  # The one who asked to be adopted
    parent_id = request["target_id"]  # The prospective parent

    child = await db.get_user(child_id)
    parent = await db.get_user(parent_id)

    if action == "reject":
        await db.delete_pending_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ {user_display_name(parent)} rejected {user_display_name(child)}'s request to be adopted.",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Request rejected.")
        return

    # Accept - create parent-child relationship
    # Re-validate strict makeparent rules.
    if await db.is_adopt_hierarchy_conflict(parent_id, child_id):
        await db.delete_pending_request(request_id)
        base = await build_makeparent_conflict_message(db, child_id, parent_id)
        error_msg = await format_family_error_message(
            db, parent_id, child_id, "be parent of"
        )
        await queue_it(
            lambda: safe_callback_edit(bot, callback, f"{base}\n\n{error_msg}"),
            callback.message.chat,
        )
        await safe_callback_answer(
            callback, "Cannot set parent.", show_alert=True
        )
        return

    # Check if already parent
    children = await db.get_children(parent_id)
    if any(c["user_id"] == child_id for c in children):
        await db.delete_pending_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(bot, callback, "❌ Already adopted!"),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Already your child!")
        return

    await db.add_adoption(parent_id, child_id)

    # Add currency rewards
    await db.add_balance(parent_id, 500, "Adopted someone")
    await db.add_balance(child_id, 1000, "Got adopted")

    # Delete the request
    await db.delete_pending_request(request_id)

    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"🎉 <b>Adoption Complete!</b>\n\n"
            f"👨‍👧 {user_display_name(parent)} has adopted {user_display_name(child)}!\n\n"
            f"💰 {user_display_name(parent)} earned $500\n"
            f"💰 {user_display_name(child)} earned $1,000",
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "You've adopted them! 🎉")


@client.on_callback_query(filters.regex(r"^runaway:(confirm|cancel):(\d+)$"))
@with_callback_action_lock
async def handle_runaway_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle runaway confirmation callbacks."""
    from bot.utils import db_user_mention

    match = re.match(r"^runaway:(confirm|cancel):(\d+)$", callback.data)
    action = match.group(1)
    owner_id = int(match.group(2))

    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who started this can use these buttons!",
            show_alert=True,
        )
        return

    user_id = owner_id

    if action == "cancel":
        await queue_it(
            lambda: safe_callback_edit(
                bot, callback, "❤️ You decided to stay. Good choice!"
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback)
        return

    # Get parents before removing
    parents = await db.get_parents(user_id)

    if not parents:
        await safe_callback_answer(
            callback, "You don't have any parents!", show_alert=True
        )
        return

    # Get child info before removing
    child = await db.get_user(user_id)

    # Remove all parent relationships
    for parent in parents:
        await db.remove_adoption(parent["user_id"], user_id)

    parent_names = ", ".join(user_display_name(p) for p in parents)

    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"🏃 <b>You ran away from home!</b>\n\n"
            f"You are no longer the child of: {parent_names}\n\n"
            f"You're on your own now...",
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "You ran away!")

    # Send DM notifications to all affected parents
    child_mention = db_user_mention(child)
    for parent in parents:
        try:
            await bot.send_message(
                parent["user_id"],
                f"🏃 <b>Your child ran away!</b>\n\n"
                f"{child_mention} has run away from home and is no longer your child.",
            )
        except Exception:
            pass  # User may have blocked the bot or not started it


@client.on_callback_query(filters.regex(r"^" + "setpic:telegram" + r"$"))
async def handle_setpic_telegram_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle setting profile pic from Telegram profile photo."""
    import base64

    from bot.utils import fetch_telegram_profile_photo

    user = callback.from_user

    # Fetch Telegram profile photo
    result = await fetch_telegram_profile_photo(bot, user.id)

    if not result:
        await safe_callback_answer(
            callback, "No Telegram profile photo found!", show_alert=True
        )
        return

    file_id, image_bytes = result
    b64_data = base64.b64encode(image_bytes).decode("utf-8")

    # Save to database
    await db.set_profile_pic(user.id, file_id=file_id, b64=b64_data)

    # Update message with new photo
    try:
        await callback.message.delete()
    except Exception:
        pass

    await bot.send_photo(
        callback.message.chat.id,
        photo=to_input_file(image_bytes, filename="profile.jpg"),
        caption="✅ <b>Your profile picture has been updated from your Telegram profile!</b>",
    )

    await safe_callback_answer(callback, "Profile picture updated! 📸")


@client.on_callback_query(filters.regex(r"^fuse_(accept|reject):(\d+)$"))
@with_callback_action_lock
async def handle_fuse_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle gem fuse accept/reject callbacks."""
    from bot.plugins.daily import GEMS
    from bot.utils import db_user_mention

    match = re.match(r"^fuse_(accept|reject):(\d+)$", callback.data)
    action = match.group(1)
    request_id = int(match.group(2))

    request = await db.get_gem_fuse_request(request_id)

    if not request:
        await safe_callback_answer(
            callback, "This request has expired.", show_alert=True
        )
        return

    # Only the target can respond
    if callback.from_user.id != request["target_id"]:
        await safe_callback_answer(
            callback, "You can't perform this action!", show_alert=True
        )
        return

    requester = await db.get_user(request["requester_id"])
    target = await db.get_user(request["target_id"])
    gem_type = request["gem_type"]
    gem_info = GEMS.get(gem_type, {})

    if action == "reject":
        await db.delete_gem_fuse_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ {user_display_name(target)} rejected the gem fusion with "
                f"{user_display_name(requester)}.",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Fusion rejected!")
        return

    # Accept - verify both still have the gems
    requester_gem = await db.get_user_gem(request["requester_id"])
    target_gem = await db.get_user_gem(request["target_id"])

    if not requester_gem or not target_gem:
        await db.delete_gem_fuse_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                "❌ <b>Fusion Failed!</b>\n\nOne of you no longer has a gem.",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(
            callback, "Fusion failed - missing gem!", show_alert=True
        )
        return

    if requester_gem != target_gem:
        await db.delete_gem_fuse_request(request_id)
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"❌ <b>Fusion Failed!</b>\n\n"
                f"Your gems no longer match.\n"
                f"{user_display_name(requester)}: {GEMS.get(requester_gem, {}).get('emoji', '💎')} {GEMS.get(requester_gem, {}).get('name', requester_gem)}\n"
                f"{user_display_name(target)}: {GEMS.get(target_gem, {}).get('emoji', '💎')} {GEMS.get(target_gem, {}).get('name', target_gem)}",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(
            callback, "Fusion failed - gems don't match!", show_alert=True
        )
        return

    # Success! Clear both gems and give rewards
    reward = gem_info.get("fuse_reward", 5000)

    await db.clear_user_gem(request["requester_id"])
    await db.clear_user_gem(request["target_id"])
    await db.add_balance(
        request["requester_id"], reward, f"Gem fusion ({gem_type})"
    )
    await db.add_balance(
        request["target_id"], reward, f"Gem fusion ({gem_type})"
    )
    await db.delete_gem_fuse_request(request_id)

    requester_mention = db_user_mention(requester)
    target_mention = db_user_mention(target)

    await queue_it(
        lambda: safe_callback_edit(
            bot,
            callback,
            f"✨ <b>Gem Fusion Successful!</b>\n\n"
            f"💎 {requester_mention} & {target_mention} fused their "
            f"{gem_info.get('emoji', '💎')} {gem_info.get('name', gem_type)}s!\n\n"
            f"🎁 Both received <b>${reward:,}</b>!",
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, f"Fusion complete! +${reward:,}")

    # Notify requester via DM if they're not in this chat
    try:
        await bot.send_message(
            request["requester_id"],
            f"✨ <b>Gem Fusion Complete!</b>\n\n"
            f"{target_mention} accepted your fusion request!\n"
            f"You received <b>${reward:,}</b> 💰",
        )
    except Exception:
        pass


@client.on_callback_query(filters.regex(r"^circle:(\d+):(\d+)$"))
async def handle_circle_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle circle level navigation callbacks."""
    match = re.match(r"^circle:(\d+):(\d+)$", callback.data)
    user_id = int(match.group(1))
    level = int(match.group(2))

    # Validate level
    level = max(1, min(5, level))

    await safe_callback_answer(callback, f"Loading level {level}...")

    # Get user name
    user = await db.get_user(user_id)
    user_name = user["first_name"] if user else "Unknown"

    try:
        from pyrogram.types import (
            InlineKeyboardButton,
            InlineKeyboardMarkup,
            InputMediaPhoto,
        )

        from bot.graphics.circle_renderer import render_friend_circle

        image_bytes = await render_friend_circle(bot, db, user_id, depth=level)

        if image_bytes:
            # Create navigation buttons
            buttons = []
            if level > 1:
                buttons.append(
                    InlineKeyboardButton(
                        text="➖ Level",
                        callback_data=f"circle:{user_id}:{level - 1}",
                    )
                )
            if level < 5:
                buttons.append(
                    InlineKeyboardButton(
                        text="➕ Level",
                        callback_data=f"circle:{user_id}:{level + 1}",
                    )
                )

            keyboard = (
                InlineKeyboardMarkup(inline_keyboard=[buttons])
                if buttons
                else None
            )

            level_dots = "●" * level + "○" * (5 - level)

            # Count visible friends at this level
            if level == 1:
                visible_count = len(await db.get_friends(user_id))
            else:
                # For deeper levels, count all friends up to that depth
                visible_ids = {user_id}
                for _d in range(1, level + 1):
                    next_ids = set()
                    for uid in visible_ids:
                        friends = await db.get_friends(uid)
                        for f in friends:
                            next_ids.add(f["user_id"])
                    visible_ids.update(next_ids)
                visible_count = len(visible_ids) - 1  # Exclude center user

            # Build level description
            level_desc = {
                1: "Level 1: Direct friends",
                2: "Level 2: Friends of your friends",
                3: "Level 3: Friends of level 2",
                4: "Level 4: Friends of level 3",
                5: "Level 5: Friends of level 4",
            }
            caption = (
                f"🌐 <b>{user_name}'s Friend Circle</b>\n"
                f"Depth: {level_dots} ({level}/5) | Visible: {visible_count} friends\n"
                f"<i>{level_desc[level]}</i>"
            )

            # Edit the media
            await queue_it(
                lambda: callback.message.edit_media(
                    media=InputMediaPhoto(
                        media=to_input_file(
                            image_bytes, filename="friend_circle.png"
                        ),
                        caption=caption,
                    ),
                    reply_markup=keyboard,
                ),
                callback.message.chat,
            )
        else:
            await queue_it(
                lambda: callback.message.edit_caption(
                    caption=f"🌐 <b>{user_name}'s Friend Circle</b>\n\n"
                    f"No friends at this level."
                ),
                callback.message.chat,
            )
    except Exception as e:
        err_caption = f"❌ Failed to generate circle: {e}"
        await queue_it(
            lambda: callback.message.edit_caption(caption=err_caption),
            callback.message.chat,
        )


# ============ RIPPLE GAMBLING CALLBACKS ============


async def _restart_ripple_game(
    callback: CallbackQuery, bot: Bot, db, bet_amount: int
):
    """Restart ripple game with same bet amount."""
    import random

    from bot.constants import RIPPLE_MAX_BET, RIPPLE_MIN_BET, RIPPLE_MULTIPLIER

    user_id = callback.from_user.id

    # Check balance
    wallet = await db.get_wallet(user_id)
    if wallet["balance"] < bet_amount:
        await safe_callback_answer(
            callback,
            f"❌ Need ${bet_amount:,} to restart! You have ${wallet['balance']:,}",
            show_alert=True,
        )
        return

    # Check bet limits
    if bet_amount < RIPPLE_MIN_BET:
        await safe_callback_answer(
            callback, f"❌ Minimum bet is ${RIPPLE_MIN_BET:,}!", show_alert=True
        )
        return
    if bet_amount > RIPPLE_MAX_BET:
        await safe_callback_answer(
            callback, f"❌ Maximum bet is ${RIPPLE_MAX_BET:,}!", show_alert=True
        )
        return

    # Deduct amount
    await db.add_balance(user_id, -bet_amount, "Ripple bet")

    # Pre-generate snake positions
    snake_positions = []
    for level in range(20):
        snake_pos = random.randint(0, 2)
        snake_positions.append(f"{level}:{snake_pos}")
    snake_data = ",".join(snake_positions)

    # Create new game
    game = await db.fetchrow(
        """
        INSERT INTO ripple_games (user_id, bet_amount, current_prize, chat_id, level, snake_positions)
        VALUES ($1, $2, $2, $3, 0, $4)
        RETURNING *
        """,
        user_id,
        bet_amount,
        callback.message.chat.id,
        snake_data,
    )

    next_prize = int(bet_amount * RIPPLE_MULTIPLIER)
    from bot.plugins.gambling import create_ripple_keyboard

    keyboard = create_ripple_keyboard(game["id"], 0, bet_amount, next_prize)

    try:
        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"🎰 <b>Ripple Game</b>\n\n"
                f"Find 🌻 to increase prize to <b>${next_prize:,}</b>!\n"
                f"Beware of 🐍 - you lose everything!",
                reply_markup=keyboard,
            ),
            callback.message.chat,
        )
    except FloodWait as e:
        retry_after = int(e.value) if hasattr(e, "value") else 5
        await safe_callback_answer(
            callback,
            f"Flood wait during restart. Please wait {retry_after}s.",
            show_alert=True,
        )
        return

    # Update message_id
    await db.execute(
        "UPDATE ripple_games SET message_id = $1 WHERE id = $2",
        callback.message.id,
        game["id"],
    )
    await safe_callback_answer(callback, "🔄 Game restarted!")


@client.on_callback_query(filters.regex(r"^" + "ripple:"))
async def handle_ripple_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle ripple game callbacks."""

    data_parts = callback.data.split(":")

    # Dummy callbacks (old revealed cells)
    if len(data_parts) >= 2 and data_parts[1] == "dummy":
        await safe_callback_answer(callback, "That's old history!")
        return

    # Restart game callback
    if len(data_parts) >= 3 and data_parts[1] == "restart":
        bet_amount = int(data_parts[2])
        await _restart_ripple_game(callback, bot, db, bet_amount)
        return

    if len(data_parts) < 3:
        await safe_callback_answer(callback, "Invalid action")
        return

    game_id = int(data_parts[1])
    action = data_parts[2]

    # Get game
    game = await db.fetchrow(
        "SELECT * FROM ripple_games WHERE id = $1", game_id
    )

    if not game:
        logger.warning(
            "Ripple game not found: game_id=%s user_id=%s chat_id=%s message_id=%s callback=%s",
            game_id,
            callback.from_user.id if callback.from_user else None,
            callback.message.chat.id
            if callback.message and callback.message.chat
            else None,
            callback.message.id if callback.message else None,
            callback.data,
        )
        await safe_callback_answer(callback, "Game not found!", show_alert=True)
        return

    if not game["is_active"]:
        await safe_callback_answer(
            callback, "Game already ended!", show_alert=True
        )
        return

    # Only game owner can play
    if callback.from_user.id != game["user_id"]:
        await safe_callback_answer(
            callback, "This isn't your game!", show_alert=True
        )
        return

    if action == "take":
        # Take winnings - use tracking set to prevent concurrent clicks
        if game_id in _processing_games:
            await safe_callback_answer(
                callback, "Please go slow!", show_alert=True
            )
            return

        _processing_games.add(game_id)
        try:
            prize = game["current_prize"]

            # Build history keyboard to show what happened
            history = game["history"].split(",") if game["history"] else []

            # Parse snake positions to reveal them
            snake_positions = {}
            snake_data = game.get("snake_positions", "")
            if snake_data:
                for entry in snake_data.split(","):
                    if ":" in entry:
                        lvl, pos = entry.split(":")
                        snake_positions[int(lvl)] = int(pos)

            from bot.plugins.gambling import create_ripple_history_keyboard

            keyboard = create_ripple_history_keyboard(
                history,
                won=True,
                bet_amount=game["bet_amount"],
                snake_positions=snake_positions,
                level=game["level"],
            )

            win_text = (
                f"💰 <b>You took ${prize:,}!</b>\n\n"
                f"📊 Reached Level: {game['level']}\n"
                f"Bet: ${game['bet_amount']:,}\n"
                f"Profit: <b>${prize - game['bet_amount']:,}</b> 🎉"
            )

            # Try to edit message first
            try:
                await queue_it(
                    lambda: safe_callback_edit(
                        bot, callback, win_text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )
            except FloodWait as e:
                retry_after = int(e.value) if hasattr(e, "value") else 5
                await safe_callback_answer(
                    callback,
                    f"Flood wait during edit. Please wait at least {retry_after} seconds!",
                    show_alert=True,
                )
                return
            except BadRequest:
                pass

            # Update database after successful edit
            await db.execute(
                "UPDATE ripple_games SET is_active = FALSE WHERE id = $1",
                game_id,
            )

            await db.add_balance(
                callback.from_user.id, prize, "Ripple winnings"
            )

            # Record stats
            profit = prize - game["bet_amount"]
            await db.execute(
                """
                INSERT INTO gambling_stats (user_id, game_type, total_wagered, total_won, biggest_win)
                VALUES ($1, 'ripple', $2, $3, $3)
                ON CONFLICT (user_id, game_type) DO UPDATE SET
                    total_wagered = gambling_stats.total_wagered + $2,
                    total_won = gambling_stats.total_won + $3,
                    biggest_win = GREATEST(gambling_stats.biggest_win, $3)
                """,
                callback.from_user.id,
                game["bet_amount"],
                profit,
            )
            from bot.achievements import (
                check_gambling_achievements,
                check_money_achievements,
            )

            await check_gambling_achievements(
                db,
                callback.from_user.id,
                bot,
                callback.message.chat.id,
            )
            await check_money_achievements(
                db, callback.from_user.id, bot, callback.message.chat.id
            )
        finally:
            _processing_games.discard(game_id)

        await safe_callback_answer(callback, f"You won ${prize:,}!")
        return

    if action == "pick":
        position = int(data_parts[3])

        # Use tracking set to prevent concurrent clicks
        if game_id in _processing_games:
            await safe_callback_answer(
                callback, "Please go slow!", show_alert=True
            )
            return

        _processing_games.add(game_id)
        try:
            # Get pre-determined snake position for current level
            snake_data = game.get("snake_positions", "") or ""
            snake_pos_for_level = 0  # Default to position 0

            # Parse snake positions
            snake_dict = {}
            if snake_data:
                for entry in snake_data.split(","):
                    if ":" in entry:
                        try:
                            lvl, pos = entry.split(":")
                            snake_dict[int(lvl)] = int(pos)
                        except (ValueError, IndexError):
                            pass

            # pre-add new snake positions if we are close to end
            end_level = max(snake_dict.keys()) + 1 if snake_dict else 0
            if (
                end_level - game.get("level", 0) <= 2
                and end_level < 100
                and end_level not in snake_dict
            ):
                import random

                # Generate 20 more levels starting from next_level
                new_snakes = []
                for lvl in range(end_level, end_level + 20):
                    new_snakes.append(f"{lvl}:{random.randint(0, 2)}")
                # Append new snake positions to existing data
                if snake_data:
                    updated_snake = snake_data + "," + ",".join(new_snakes)
                else:
                    updated_snake = ",".join(new_snakes)
                snake_data = updated_snake
                # Save updated snake positions to database
                await db.execute(
                    "UPDATE ripple_games SET snake_positions = $1 WHERE id = $2",
                    updated_snake,
                    game_id,
                )
                # Re-parse
                for entry in snake_data.split(","):
                    if ":" in entry:
                        try:
                            lvl, pos = entry.split(":")
                            snake_dict[int(lvl)] = int(pos)
                        except (ValueError, IndexError):
                            pass

            snake_pos_for_level = snake_dict.get(game["level"], 0)

            # Check if user picked the snake position
            is_sunflower = (snake_pos_for_level == -1) or (
                position != snake_pos_for_level
            )

            # Parse history
            history = game["history"].split(",") if game["history"] else []

            if not is_sunflower:
                # Lost! Hit a snake
                history.append(f"{position}:s")

                # Parse snake positions to reveal them
                snake_positions = {}
                if snake_data:
                    for entry in snake_data.split(","):
                        if ":" in entry:
                            try:
                                lvl, pos = entry.split(":")
                                snake_positions[int(lvl)] = int(pos)
                            except (ValueError, IndexError):
                                pass

                # Build game over display with inline keyboard
                from bot.plugins.gambling import create_ripple_history_keyboard

                keyboard = create_ripple_history_keyboard(
                    history,
                    lost=True,
                    bet_amount=game["bet_amount"],
                    snake_positions=snake_positions,
                    level=game["level"],
                )

                profit_lost = game["current_prize"] - game["bet_amount"]
                loss_text = (
                    f"🐍 <b>You lost!</b>\n\n"
                    f"📊 Reached Level: {game['level']}\n"
                    f"Bet: ${game['bet_amount']:,}\n"
                    f"You lost your ${game['bet_amount']:,} bet"
                    + (
                        f" and ${profit_lost:,} potential profit..."
                        if profit_lost > 0
                        else "..."
                    )
                )

                # Try to edit message first
                try:
                    await queue_it(
                        lambda: safe_callback_edit(
                            bot, callback, loss_text, reply_markup=keyboard
                        ),
                        callback.message.chat,
                    )
                except FloodWait as e:
                    retry_after = int(e.value) if hasattr(e, "value") else 5
                    await safe_callback_answer(
                        callback,
                        f"Flood wait during edit. Please wait at least {retry_after} seconds!",
                        show_alert=True,
                    )
                    return
                except BadRequest:
                    pass

                # Update database after successful edit
                await db.execute(
                    "UPDATE ripple_games SET is_active = FALSE, history = $1 WHERE id = $2",
                    ",".join(history),
                    game_id,
                )

                # Record stats
                await db.execute(
                    """
                    INSERT INTO gambling_stats (user_id, game_type, total_wagered, total_lost, biggest_loss)
                    VALUES ($1, 'ripple', $2, $3, $3)
                    ON CONFLICT (user_id, game_type) DO UPDATE SET
                        total_wagered = gambling_stats.total_wagered + $2,
                        total_lost = gambling_stats.total_lost + $3,
                        biggest_loss = GREATEST(gambling_stats.biggest_loss, $3)
                    """,
                    callback.from_user.id,
                    game["bet_amount"],
                    game["bet_amount"],
                )
            else:
                # Won this round - found sunflower
                history.append(f"{position}:f")
                new_prize = int(game["current_prize"] * RIPPLE_MULTIPLIER)
                new_level = game["level"] + 1

                next_prize = int(new_prize * RIPPLE_MULTIPLIER)

                # Build keyboard
                from bot.plugins.gambling import create_ripple_keyboard

                keyboard = create_ripple_keyboard(
                    game_id, new_level, new_prize, next_prize, history
                )

                # Show level progression message when going above level 10
                level_note = ""
                if new_level > 10:
                    level_note = f"\n\n<i>⚠️ You're now at level {new_level}. Old history rows are being trimmed to keep the display clean.</i>"

                win_text = (
                    f"🎰 <b>Ripple Game</b> (Level {new_level})\n\n"
                    f"🌻 Found sunflower!\n"
                    f"Find another 🌻 to increase prize to <b>${next_prize:,}</b>!"
                    f"{level_note}"
                )

                # Try to edit message first
                try:
                    await queue_it(
                        lambda: safe_callback_edit(
                            bot, callback, win_text, reply_markup=keyboard
                        ),
                        callback.message.chat,
                    )
                except FloodWait as e:
                    retry_after = int(e.value) if hasattr(e, "value") else 5
                    await safe_callback_answer(
                        callback,
                        f"Flood wait during edit. Please wait at least {retry_after} seconds!",
                        show_alert=True,
                    )
                    return
                except BadRequest:
                    pass

                # Update database after successful edit
                await db.execute(
                    "UPDATE ripple_games SET current_prize = $1, level = $2, history = $3 WHERE id = $4",
                    new_prize,
                    new_level,
                    ",".join(history),
                    game_id,
                )
        finally:
            _processing_games.discard(game_id)

        await safe_callback_answer(
            callback,
            "You hit a snake! 🐍"
            if not is_sunflower
            else f"Sunflower! Prize: ${int(game['current_prize'] * RIPPLE_MULTIPLIER):,}",
        )


# ============ LOTTERY CALLBACKS ============


@client.on_callback_query(
    filters.regex(r"^lottery_replace:(confirm|cancel):(\d+)(?::(\d+):(\d+))?$")
)
@with_callback_action_lock
async def handle_lottery_replace_callback(
    callback: CallbackQuery,
):
    """Handle replacing an existing active lottery in chat."""
    match = re.match(
        r"^lottery_replace:(confirm|cancel):(\d+)(?::(\d+):(\d+))?$",
        callback.data,
    )
    action = match.group(1)
    owner_id = int(match.group(2))

    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback,
            "Only the person who started this can choose.",
            show_alert=True,
        )
        return

    if action == "cancel":
        await queue_it(
            lambda: safe_callback_edit(
                bot, callback, "✅ Kept the current lottery."
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback)
        return

    old_lottery_id = int(match.group(3))
    amount = int(match.group(4))
    chat_id = callback.message.chat.id

    existing = await db.fetchrow(
        "SELECT * FROM lotteries WHERE id = $1 AND chat_id = $2 AND is_active = TRUE",
        old_lottery_id,
        chat_id,
    )
    if not existing:
        await safe_callback_answer(
            callback,
            "That lottery is no longer active.",
            show_alert=True,
        )
        return

    other_active = await db.fetchrow(
        "SELECT id FROM lotteries WHERE chat_id = $1 AND is_active = TRUE AND id != $2 LIMIT 1",
        chat_id,
        old_lottery_id,
    )
    if other_active:
        await safe_callback_answer(
            callback,
            "Another active lottery appeared. Try /lottery again.",
            show_alert=True,
        )
        return

    wallet = await db.get_wallet(owner_id)
    if wallet["balance"] < amount:
        await safe_callback_answer(
            callback,
            f"Insufficient balance! You have ${wallet['balance']:,}",
            show_alert=True,
        )
        return

    await db.add_balance(owner_id, -amount, "Lottery entry")
    await db.execute(
        "UPDATE lotteries SET is_active = FALSE WHERE id = $1",
        old_lottery_id,
    )

    lottery = await db.fetchrow(
        """
        INSERT INTO lotteries (host_id, stake_amount, chat_id, participants)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        owner_id,
        amount,
        chat_id,
        [owner_id],
    )

    host_mention = (
        f'<a href="tg://user?id={owner_id}">{callback.from_user.first_name}</a>'
    )
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"🎟️ Join (${amount:,})",
                    callback_data=f"lottery:{lottery['id']}:join",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎲 Draw Winner",
                    callback_data=f"lottery:{lottery['id']}:draw",
                ),
            ],
        ]
    )

    sent = await callback.message.reply(
        f"🎟️ <b>Lottery Started!</b>\n\n"
        f"💰 Stake: <b>${amount:,}</b>\n"
        f"🏆 Winner gets all!\n\n"
        f"👤 Host: {host_mention}\n"
        f"👥 Participants: 1\n"
        f"🧾 Participants:\n• {host_mention}\n"
        f"💵 Prize Pool: <b>${amount:,}</b>\n\n"
        f"<i>Click Join to enter!</i>",
        reply_markup=keyboard,
    )
    await db.execute(
        "UPDATE lotteries SET message_id = $1 WHERE id = $2",
        sent.id,
        lottery["id"],
    )

    new_link = build_chat_message_link(chat_id, sent.id)
    old_link = build_chat_message_link(chat_id, existing["message_id"])
    summary = "✅ <b>Started a new lottery.</b>\n\n"
    if old_link:
        summary += f'🧾 Previous lottery: <a href="{old_link}">Open</a>\n'
    if new_link:
        summary += f'🎟️ New lottery: <a href="{new_link}">Open</a>'

    await queue_it(
        lambda: safe_callback_edit(bot, callback, summary),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "New lottery started.")


@client.on_callback_query(filters.regex(r"^lottery:(\d+):(join|draw)$"))
async def handle_lottery_callback(
    callback: CallbackQuery,
    bot: Bot,
):
    """Handle lottery join/draw callbacks."""
    match = re.match(r"^lottery:(\d+):(join|draw)$", callback.data)
    lottery_id = int(match.group(1))
    action = match.group(2)

    # Get lottery
    lottery = await db.fetchrow(
        "SELECT * FROM lotteries WHERE id = $1", lottery_id
    )

    if not lottery:
        await safe_callback_answer(
            callback, "Lottery not found!", show_alert=True
        )
        return

    if not lottery["is_active"]:
        await safe_callback_answer(
            callback, "Lottery already ended!", show_alert=True
        )
        return

    user = callback.from_user

    if action == "join":
        # Check if already joined
        if user.id in lottery["participants"]:
            await safe_callback_answer(
                callback, "You already joined!", show_alert=True
            )
            return

        # Ensure user exists
        await db.upsert_user(
            user_id=user.id,
            username=user.username,
            first_name=user.first_name,
        )

        # Check balance
        wallet = await db.get_wallet(user.id)
        if wallet["balance"] < lottery["stake_amount"]:
            await safe_callback_answer(
                callback,
                f"Insufficient balance! Need ${lottery['stake_amount']:,}",
                show_alert=True,
            )
            return

        # Deduct and add to participants
        await db.add_balance(user.id, -lottery["stake_amount"], "Lottery entry")

        new_participants = lottery["participants"] + [user.id]
        await db.execute(
            "UPDATE lotteries SET participants = $1 WHERE id = $2",
            new_participants,
            lottery_id,
        )

        # Update message
        prize_pool = lottery["stake_amount"] * len(new_participants)
        host = await db.get_user(lottery["host_id"])
        host_mention = f'<a href="tg://user?id={lottery["host_id"]}">{host["first_name"] if host else "Unknown"}</a>'
        participants_text = await format_lottery_participants(
            db, new_participants
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=f"🎟️ Join (${lottery['stake_amount']:,})",
                        callback_data=f"lottery:{lottery_id}:join",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🎲 Draw Winner",
                        callback_data=f"lottery:{lottery_id}:draw",
                    )
                ],
            ]
        )

        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"🎟️ <b>Lottery!</b>\n\n"
                f"💰 Stake: <b>${lottery['stake_amount']:,}</b>\n"
                f"🏆 Winner gets all!\n\n"
                f"👤 Host: {host_mention}\n"
                f"👥 Participants: {len(new_participants)}\n"
                f"🧾 Participants:\n{participants_text}\n"
                f"💵 Prize Pool: <b>${prize_pool:,}</b>\n\n"
                f"<i>Click Join to enter!</i>",
                reply_markup=keyboard,
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "You joined the lottery! 🎟️")

    elif action == "draw":
        # Only host can draw
        if user.id != lottery["host_id"]:
            await safe_callback_answer(
                callback, "Only the host can draw!", show_alert=True
            )
            return

        if len(lottery["participants"]) < 2:
            await safe_callback_answer(
                callback, "Need at least 2 participants!", show_alert=True
            )
            return

        # Draw winner
        import random

        winner_id = random.choice(lottery["participants"])

        prize_pool = lottery["stake_amount"] * len(lottery["participants"])
        participants_text = await format_lottery_participants(
            db, lottery["participants"]
        )

        # Give winnings
        await db.add_balance(winner_id, prize_pool, "Lottery winnings")

        # Mark as inactive
        await db.execute(
            "UPDATE lotteries SET is_active = FALSE, winner_id = $1 WHERE id = $2",
            winner_id,
            lottery_id,
        )

        from bot.achievements import (
            check_gambling_achievements,
            check_money_achievements,
        )

        await check_gambling_achievements(
            db, winner_id, bot, callback.message.chat.id
        )
        await check_money_achievements(
            db, winner_id, bot, callback.message.chat.id
        )

        # Get winner info
        winner = await db.get_user(winner_id)
        winner_mention = f'<a href="tg://user?id={winner_id}">{winner["first_name"] if winner else "Unknown"}</a>'

        await queue_it(
            lambda: safe_callback_edit(
                bot,
                callback,
                f"🎉 <b>Lottery Winner!</b>\n\n"
                f"🏆 Winner: {winner_mention}\n"
                f"💰 Prize: <b>${prize_pool:,}</b>\n\n"
                f"👥 Participants: {len(lottery['participants'])}\n\n"
                f"🧾 Participants:\n{participants_text}\n\n"
                f"Congratulations! 🎊",
            ),
            callback.message.chat,
        )
        await safe_callback_answer(callback, "Winner drawn!")
