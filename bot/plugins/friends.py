"""Friend system commands."""

from bot.queue_it import queue_it

from typing import Optional

from pyrogram.errors import BadRequest
from pyrogram import Client as Bot
from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
    User,
)
from bot.input_file import to_input_file

from bot.command_registry import reg
from bot.database import Database
from bot.utils import user_mention as util_user_mention
from pyrogram import filters
from bot.client import client
from bot.database import db


async def get_target_user(
    bot: Bot, message: Message, db: "Database" = None
) -> Optional[User]:
    """
    Extract target user from message (reply, mention, or user ID).
    Falls back to database if bot.get_chat fails.
    """
    # Check if replying to someone
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user
        if not target.is_bot:
            return target
        return None

    # Check for mentions in entities
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention" and message.text:
                username = message.text[
                    entity.offset : entity.offset + entity.length
                ].lstrip("@")
                target = await _resolve_user(bot, db, username)
                if target:
                    return target
            elif entity.type == "text_mention" and entity.user:
                if not entity.user.is_bot:
                    return entity.user

    # Check for username or user_id in command arguments
    if message.text:
        parts = message.text.split()
        if len(parts) > 1:
            arg = parts[1].lstrip("@")
            target = await _resolve_user(bot, db, arg)
            if target:
                return target

    return None


async def _resolve_user(
    bot: Bot, db: "Database", identifier: str
) -> Optional[User]:
    """Resolve a user by username or user ID, trying bot first then database."""
    if identifier.isdigit():
        try:
            user_obj = await bot.get_users(int(identifier))
            if not user_obj.is_bot:
                return user_obj
        except Exception:
            pass
        if db:
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
        try:
            user_obj = await bot.get_users(f"@{identifier}")
            if not user_obj.is_bot:
                return user_obj
        except Exception:
            pass
        if db:
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


def user_display_name(user) -> str:
    """Get display name for a user record or User object."""
    if hasattr(user, "first_name"):
        return user.first_name
    return user.get("first_name") or "Unknown"


reg("friend", "🤝 Send friend request")


@client.on_message(filters.command(["friend"]))
async def friend_command(
    message: Message,
    bot: Bot,
):
    """Send a friend request."""
    target = await get_target_user(bot, message, db)
    if not target:
        await message.reply(
            "Reply to a user or use /friend @username to send a friend request."
        )
        return

    user = message.from_user

    if target.id == user.id:
        await message.reply("😅 You can't befriend yourself!")
        return

    if target.is_bot:
        await message.reply("🤖 You can't befriend a bot!")
        return

    # Ensure both users exist
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

    # Check if already friends
    if await db.are_friends(user.id, target.id):
        await message.reply(
            f"🤝 You're already friends with {target.first_name}!"
        )
        return

    # Check if request already exists (from us to them) - delete old and create new
    existing = await db.get_friend_request(user.id, target.id)
    if existing:
        await db.delete_friend_request(user.id, target.id)

    # Check if they sent a request to us - auto-accept
    reverse_request = await db.get_friend_request(target.id, user.id)
    if reverse_request:
        # Mutual request - create friendship
        await db.add_friendship(user.id, target.id)
        await db.delete_friend_request(target.id, user.id)

        # Add currency rewards
        await db.add_balance(user.id, 3000, "New friendship")
        await db.add_balance(target.id, 3000, "New friendship")

        await message.reply(
            f"🎉 <b>New Friendship!</b>\n\n"
            f"🤝 You and {target.first_name} are now friends!\n\n"
            f"💰 Both of you earned $3,000!"
        )
        from bot.achievements import (
            check_friend_achievements,
            check_money_achievements,
        )

        await check_friend_achievements(db, user.id, bot, message.chat.id)
        await check_friend_achievements(db, target.id, bot, message.chat.id)
        await check_money_achievements(db, user.id, bot, message.chat.id)
        await check_money_achievements(db, target.id, bot, message.chat.id)
        return

    # Create friend request
    await db.create_friend_request(user.id, target.id)

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Accept", callback_data=f"friend_accept:{user.id}"
                ),
                InlineKeyboardButton(
                    text="❌ Reject", callback_data=f"friend_reject:{user.id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="↩️ Cancel", callback_data=f"friend_cancel:{user.id}"
                ),
            ],
        ]
    )

    # Use HTML mentions for proper notifications
    user_link = util_user_mention(user)
    target_link = util_user_mention(target)

    await message.reply(
        f"🤝 <b>Friend Request</b>\n\n"
        f"{user_link} wants to be friends with {target_link}!\n\n"
        f"{target_link}, do you accept?",
        reply_markup=keyboard,
    )


reg("circle", "🌐 View friend circle")


@client.on_message(filters.command(["circle", "friends"]))
async def circle_command(
    message: Message,
    bot: Bot,
):
    """Show friend circle image with level navigation."""
    # Parse level from arguments
    level = 1  # Default level
    if message.text:
        parts = message.text.split()
        for part in parts[1:]:
            if part.isdigit():
                level = max(1, min(5, int(part)))
                break

    # Check if targeting another user (reply, mention, or ID)
    target = await get_target_user(bot, message, db)
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

    status_msg = await message.reply("🔄 Generating friend circle...")

    try:
        from bot.graphics.circle_renderer import render_friend_circle

        image_bytes = await render_friend_circle(bot, db, user_id, depth=level)

        if image_bytes:
            # Count visible friends at this level
            async def count_visible_friends(target_level):
                """Count visible friends up to a given depth."""
                visible_ids = {user_id}
                for _d in range(1, target_level + 1):
                    next_ids = set()
                    for uid in visible_ids:
                        friends = await db.get_friends(uid)
                        for f in friends:
                            next_ids.add(f["user_id"])
                    visible_ids.update(next_ids)
                return len(visible_ids) - 1  # Exclude center user

            visible_count = await count_visible_friends(level)

            # Check if there's more depth to show
            has_more_depth = False
            if level < 5:
                next_level_count = await count_visible_friends(level + 1)
                has_more_depth = next_level_count > visible_count

            # Create navigation buttons
            buttons = []
            if level > 1:
                buttons.append(
                    InlineKeyboardButton(
                        text="➖ Level",
                        callback_data=f"circle:{user_id}:{level - 1}",
                    )
                )
            if level < 5 and has_more_depth:
                buttons.append(
                    InlineKeyboardButton(
                        text="➕ Level",
                        callback_data=f"circle:{user_id}:{level + 1}",
                    )
                )

            keyboard = None
            if buttons:
                keyboard = InlineKeyboardMarkup(inline_keyboard=[buttons])

            level_dots = "●" * level + "○" * (5 - level)

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

            # Add note if no more depth
            if not has_more_depth and level < 5:
                caption += "\n\n<i>Note: there is no more depth to friend circle network</i>"

            try:
                await message.reply_photo(
                    photo=to_input_file(
                        image_bytes, filename="friend_circle.png"
                    ),
                    caption=caption,
                    reply_markup=keyboard,
                )
            except BadRequest as e:
                # Some generated images may violate Telegram photo dimension rules.
                # Fallback to document to avoid failing the whole command.
                if "PHOTO_INVALID_DIMENSIONS" not in str(e):
                    raise
                await message.reply_document(
                    document=to_input_file(
                        image_bytes, filename="friend_circle.png"
                    ),
                    caption=caption,
                    reply_markup=keyboard,
                )
        else:
            await message.reply(
                f"🌐 <b>{user_name}'s Friend Circle</b>\n\n"
                f"No friends yet. Use /friend @username to add friends! 🤝"
            )

        await status_msg.delete()

    except Exception as e:
        await queue_it(
            lambda: status_msg.edit_text(f"❌ Failed to generate circle: {e}"),
            status_msg.chat,
        )


reg("unfriend", "😢 Remove a friend")


UNFRIEND_PAGE_SIZE = 10


def build_unfriend_keyboard(
    friends: list, user_id: int, page: int
) -> InlineKeyboardMarkup:
    start = page * UNFRIEND_PAGE_SIZE
    page_friends = friends[start : start + UNFRIEND_PAGE_SIZE]
    buttons = [
        [
            InlineKeyboardButton(
                text=f["first_name"] or "Unknown",
                callback_data=f"unfriend:{f['user_id']}",
            )
        ]
        for f in page_friends
    ]
    nav = []
    if page > 0:
        nav.append(
            InlineKeyboardButton(
                text="◀ Prev",
                callback_data=f"unfriend_page:{user_id}:{page - 1}",
            )
        )
    if start + UNFRIEND_PAGE_SIZE < len(friends):
        nav.append(
            InlineKeyboardButton(
                text="Next ▶",
                callback_data=f"unfriend_page:{user_id}:{page + 1}",
            )
        )
    if nav:
        buttons.append(nav)
    buttons.append([
        InlineKeyboardButton(text="❌ Cancel", callback_data="unfriend:cancel")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


@client.on_message(filters.command(["unfriend"]))
async def unfriend_command(
    message: Message,
):
    """Show menu to remove friends."""
    user = message.from_user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    friends = await db.get_friends(user.id)

    if not friends:
        await message.reply("😢 You don't have any friends to remove.")
        return

    total = len(friends)
    keyboard = build_unfriend_keyboard(friends, user.id, 0)
    page_info = (
        f" (1/{(total - 1) // UNFRIEND_PAGE_SIZE + 1})"
        if total > UNFRIEND_PAGE_SIZE
        else ""
    )

    await message.reply(
        f"😢 <b>Select a friend to remove{page_info}:</b>\n"
        "⚠️ (Warning: You will both lose $3,000)",
        reply_markup=keyboard,
    )


@client.on_message(filters.command(["ratings"]))
async def ratings_command(
    message: Message,
):
    """Show ratings the user has given."""
    user = message.from_user

    ratings = await db.get_ratings_given(user.id)

    if not ratings:
        await message.reply(
            "⭐ You haven't rated any friends yet.\n"
            "Use /rate @username 1-5 to rate a friend."
        )
        return

    lines = ["⭐ <b>Your Friend Ratings</b>\n"]
    for r in ratings:
        stars = "⭐" * r["rating"]
        name = r["first_name"] or "Unknown"
        lines.append(f"{name}: {stars}")

    await message.reply("\n".join(lines))


reg("rate", "⭐ Rate a friend (1-5)")


@client.on_message(filters.command(["rate"]))
async def rate_command(
    message: Message,
    bot: Bot,
):
    """Rate a friend (1-5 stars)."""
    user = message.from_user

    # Parse command arguments
    parts = message.text.split() if message.text else []
    if len(parts) < 3:
        await message.reply("Usage: /rate @username 1-5")
        return

    # Parse rating
    try:
        rating = int(parts[-1])
        if rating < 1 or rating > 5:
            raise ValueError()
    except ValueError:
        await message.reply("❌ Rating must be a number between 1 and 5.")
        return

    # Get target user
    target = await get_target_user(bot, message, db)
    if not target:
        # Try parsing username from command
        username = parts[1].lstrip("@")
        try:
            user_obj = await bot.get_users(f"@{username}")
            if not user_obj.is_bot:
                target = user_obj
        except Exception:
            await message.reply("❌ User not found.")
            return

    if not target:
        await message.reply("❌ User not found.")
        return

    if target.id == user.id:
        await message.reply("😅 You can't rate yourself!")
        return

    # Check if they're friends
    if not await db.are_friends(user.id, target.id):
        await message.reply(
            f"🤝 You can only rate friends! Add {target.first_name} as a friend first."
        )
        return

    # Save rating
    await db.set_friend_rating(user.id, target.id, rating)

    stars = "⭐" * rating
    await message.reply(f"✅ Rated {target.first_name}: {stars}")


reg("flink", "🔗 Get friend invite link")


@client.on_message(filters.command(["flink"]))
async def flink_command(
    message: Message,
    bot: Bot,
):
    """Get a shareable friend link."""
    user = message.from_user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    link_code = await db.get_or_create_friend_link(user.id)

    # Get bot username
    me = await bot.get_me()

    link = f"https://t.me/{me.username}?start=flink_{link_code}"

    await message.reply(
        f"🔗 <b>Your Friend Link</b>\n\n"
        f"Share this link to instantly become friends:\n"
        f"{link}\n\n"
        f"Anyone who clicks this link will become your friend! 🤝"
    )


@client.on_message(filters.command(["suggestions"]))
async def suggestions_command(
    message: Message,
):
    """Show friend suggestions (friends of friends)."""
    user = message.from_user
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    suggestions = await db.get_friend_suggestions(user.id, limit=10)

    if not suggestions:
        await message.reply(
            "🤔 No friend suggestions available.\n"
            "Add more friends to get suggestions!"
        )
        return

    lines = ["💡 <b>Friend Suggestions</b>\n", "People your friends know:\n"]

    for s in suggestions:
        name = s["first_name"] or "Unknown"
        mutual = s["mutual_count"]
        username = f"@{s['username']}" if s["username"] else ""
        lines.append(f"👤 {name} {username} ({mutual} mutual)")

    lines.append("\nUse /friend @username to send a request! 🤝")

    await message.reply("\n".join(lines))


@client.on_message(filters.command(["activefriends"]))
async def active_friends_command(
    message: Message,
):
    """Show recently active friends."""
    user = message.from_user

    friends = await db.get_friends(user.id)

    if not friends:
        await message.reply("😢 You don't have any friends yet.")
        return

    # Sort by last_updated
    sorted_friends = sorted(
        friends, key=lambda f: f["last_updated"] or 0, reverse=True
    )[:10]

    lines = ["🟢 <b>Recently Active Friends</b>\n"]

    for f in sorted_friends:
        name = f["first_name"] or "Unknown"
        lines.append(f"👤 {name}")

    await message.reply("\n".join(lines))


@client.on_message(filters.command(["fsearch"]))
async def friend_search_command(
    message: Message,
):
    """Search friends by name."""
    user = message.from_user

    parts = message.text.split() if message.text else []
    if len(parts) < 2:
        await message.reply("Usage: /fsearch &lt;name&gt;")
        return

    query = " ".join(parts[1:]).lower()

    friends = await db.get_friends(user.id)

    if not friends:
        await message.reply("😢 You don't have any friends yet.")
        return

    matches = []
    for f in friends:
        name = (f["first_name"] or "").lower()
        username = (f["username"] or "").lower()
        if query in name or query in username:
            matches.append(f)

    if not matches:
        await message.reply(f"🔍 No friends found matching '{query}'")
        return

    lines = [f"🔍 <b>Friends matching '{query}':</b>\n"]
    for f in matches[:10]:
        name = f["first_name"] or "Unknown"
        username = f"@{f['username']}" if f["username"] else ""
        lines.append(f"👤 {name} {username}")

    await message.reply("\n".join(lines))


@client.on_message(filters.command(["friendcount"]))
async def friend_count_command(
    message: Message,
    bot: Bot,
):
    """Show friend count and average rating."""
    target = await get_target_user(bot, message, db)
    if target:
        user_id = target.id
        user_name = target.first_name
    else:
        user = message.from_user
        user_id = user.id
        user_name = user.first_name

    friends = await db.get_friends(user_id)
    avg_rating = await db.get_average_rating(user_id)

    count = len(friends)
    rating_str = f"{avg_rating:.1f}⭐" if avg_rating else "No ratings"

    await message.reply(
        f"📊 <b>{user_name}'s Friend Stats</b>\n\n"
        f"🤝 Friends: {count}\n"
        f"⭐ Average Rating: {rating_str}"
    )
