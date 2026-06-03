"""Common utility functions for the bot."""

import base64
import re
from html import escape
from typing import Optional

from pyrogram import Client as Bot
from pyrogram.types import User

from bot.database import Database


def parse_money_amount(amount_str: str, max_amount: int = None) -> int | None:
    """
    Parse flexible money amount text.

    Supports:
    - "a" / "all" -> max_amount
    - "5+3" -> 5000
    - "$100,000" / "100,000" / "$10,0,0,00" -> 100000
    """
    if amount_str is None:
        return None

    normalized = amount_str.strip().lower()
    if normalized in ("a", "all"):
        return max_amount

    normalized = normalized.replace("$", "").replace(",", "")
    if not normalized:
        return None

    plus_match = re.fullmatch(r"(\d+)\+(\d+)", normalized)
    if plus_match:
        base = int(plus_match.group(1))
        zeros = int(plus_match.group(2))
        return base * (10**zeros)

    if normalized.isdigit():
        return int(normalized)
    return None


async def fetch_telegram_profile_photo(
    bot: Bot, user_id: int
) -> Optional[tuple[str, bytes]]:
    """
    Fetch user's current Telegram profile photo.

    Returns:
        Tuple of (file_id, image_bytes) or None if no photo
    """
    try:
        photos = [
            photo async for photo in bot.get_chat_photos(user_id, limit=1)
        ]
        if photos:
            file_id = photos[0].file_id
            image_data = await bot.download_media(file_id, in_memory=True)
            if image_data:
                image_bytes = (
                    image_data.getvalue()
                    if hasattr(image_data, "getvalue")
                    else image_data.read()
                )
                return (file_id, image_bytes)
    except Exception:
        pass
    return None


async def ensure_user(
    bot: Bot, db: Database, user: User, auto_pfp: bool = True
) -> None:
    """
    Ensure user exists in database with profile pic.

    Args:
        bot: Kurigram client instance
        db: Database instance
        user: Telegram User object
        auto_pfp: If True, auto-fetch Telegram profile photo for new users
    """
    # Check if user already exists with a profile pic
    db_user = await db.get_user(user.id)
    is_new = db_user is None
    has_pic = db_user and db_user.get("profile_pic_b64")

    # Upsert user basic info
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Auto-set profile pic for new users or users without one
    if auto_pfp and (is_new or not has_pic):
        result = await fetch_telegram_profile_photo(bot, user.id)
        if result:
            file_id, image_bytes = result
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            await db.set_profile_pic(user.id, file_id=file_id, b64=b64_data)


def mention_html(user_id: int, name: str) -> str:
    """
    Create an HTML mention link for a user.

    Args:
        user_id: Telegram user ID
        name: Display name for the mention

    Returns:
        HTML anchor tag that mentions the user
    """
    safe_name = escape(name)
    return f'<a href="tg://user?id={user_id}">{safe_name}</a>'


def user_mention(user: User) -> str:
    """
    Create an HTML mention for a User object.

    Args:
        user: Telegram User object

    Returns:
        HTML anchor tag that mentions the user
    """
    name = user.first_name or user.username or str(user.id)
    return mention_html(user.id, name)


def db_user_mention(db_user: dict) -> str:
    """
    Create an HTML mention for a database user record.

    Args:
        db_user: Database user record dict

    Returns:
        HTML anchor tag that mentions the user
    """
    name = (
        db_user.get("first_name")
        or db_user.get("username")
        or str(db_user["user_id"])
    )
    return mention_html(db_user["user_id"], name)


async def _format_chain(
    db: Database,
    path: list[tuple[int, str]],
    arrow: str = " ➡️ ",
) -> str:
    """Format a path chain with names and emoji labels."""
    users_in_path = {}
    for uid, _ in path:
        u = await db.get_user(uid)
        users_in_path[uid] = u.get("first_name") or "Unknown"

    parts = []
    for uid, label in path:
        name = users_in_path[uid]
        if label == "start":
            parts.append(name)
        elif label == "👤":
            parts.append(f"🧑‍🍼 {name}")
        else:
            parts.append(f"{label} {name}")
    return arrow.join(parts)


async def format_family_error_message(
    db: Database, user1_id: int, user2_id: int, action: str
) -> str:
    """
    Format a detailed error message showing family path(s) between two users.
    Also includes generation level information for clarity.

    Shows each unique path as a numbered list inside a blockquote.
    """
    family_path = await db.get_family_path(user1_id, user2_id)
    if not family_path:
        user2 = await db.get_user(user2_id)
        name2 = user2.get("first_name") or "Unknown"

        # Add generation level info for better context
        user1_level = await db.get_generation_level(user1_id)
        user2_level = await db.get_generation_level(user2_id)

        if action == "be siblings with":
            return (
                f"⚠️ You can't {action} {name2} - they're connected to you via family!\n"
                f"(You are at generation level {user1_level}, they are at level {user2_level})"
            )
        elif "adopt" in action or action == "parent":
            needed_level = user2_level - 1
            return (
                f"⚠️ You can't adopt {name2}.\n"
                f"You are at level {user1_level}, they are at level {user2_level}.\n"
                f"To adopt them, you need to be at level {needed_level}."
            )
        else:
            return f"⚠️ You can't {action} {name2} - they're connected to you via family!"

    target_name = (await db.get_user(user2_id)).get("first_name") or "Unknown"

    # Collect unique paths (deduplicate by user ID sequence)
    seen: set[tuple[int, ...]] = set()
    unique_paths: list[tuple[list[tuple[int, str]], str]] = []

    # Family path
    family_ids = tuple(uid for uid, _ in family_path)
    if family_ids not in seen:
        seen.add(family_ids)
        unique_paths.append((family_path, " ➡️ "))

    # Also check sibling-only path
    sibling_path = await db.get_sibling_path(user1_id, user2_id)
    if sibling_path:
        sibling_ids = tuple(sibling_path)
        if sibling_ids not in seen:
            seen.add(sibling_ids)
            sp = [
                (uid, "👫" if i > 0 else "start")
                for i, uid in enumerate(sibling_path)
            ]
            unique_paths.append((sp, " ↔ "))

    # Format each unique path with numbered list
    lines = []
    for idx, (path, arrow) in enumerate(unique_paths, 1):
        chain = await _format_chain(db, path, arrow)
        # Get first intermediate person (after start, before end)
        intermediates = path[1:-1]
        if intermediates:
            mid_id, _ = intermediates[0]
            mid_user = await db.get_user(mid_id)
            mid_name = mid_user.get("first_name") or "Unknown"
            lines.append(f"{idx}. {mid_name} ({mid_id}): {chain}")
        else:
            lines.append(f"{idx}. {chain}")

    chain_str = "\n".join(lines)

    return (
        f"⚠️ You can't {action} {target_name} - they're connected to you via family:\n\n"
        f"<blockquote expandable>{chain_str}</blockquote>"
    )


async def format_sibling_error_message(
    db: Database, user1_id: int, user2_id: int, action: str
) -> str:
    """
    Format a detailed error message showing the sibling path between two users.
    Also includes generation level information.

    Shows the chain with names and IDs for each intermediate person,
    so users know exactly who to /removesibling to break the connection.
    """
    path = await db.get_sibling_path(user1_id, user2_id)
    if not path:
        user2 = await db.get_user(user2_id)
        name2 = user2.get("first_name") or "Unknown"

        # Add generation level info
        user1_level = await db.get_generation_level(user1_id)
        user2_level = await db.get_generation_level(user2_id)

        return (
            f"⚠️ You can't {action} {name2} - they're your sibling!\n"
            f"(You are at generation level {user1_level}, they are at level {user2_level})"
        )

    users_in_path = {}
    for uid in path:
        u = await db.get_user(uid)
        users_in_path[uid] = u.get("first_name") or "Unknown"

    chain_parts = [users_in_path[uid] for uid in path]
    full_chain = " ↔ ".join(chain_parts)

    intermediates = path[1:-1]
    target_name = users_in_path[user2_id]

    # Add generation level info
    user1_level = await db.get_generation_level(user1_id)
    user2_level = await db.get_generation_level(user2_id)

    if intermediates:
        lines = []
        for mid_id in intermediates:
            mid_name = users_in_path[mid_id]
            lines.append(f"{mid_name} ({mid_id}): {full_chain}")
        chain_str = "\n".join(lines)
    else:
        chain_str = full_chain

    return (
        f"⚠️ You can't {action} {target_name} - they're connected to you via sibling chain:\n"
        f"(You are at generation level {user1_level}, they are at level {user2_level})\n\n"
        f"{chain_str}"
    )
