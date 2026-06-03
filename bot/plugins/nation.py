"""Nation guess game plugin.

Commands:
 - /nation [continent]  start a nation guessing game
 - /guess               guess a nation (also intercepted for single-word messages)

Exports guess_command so outer middleware and /guess from other plugins can call it.
"""

import base64
from typing import Optional

from pyrogram import filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from rapidfuzz import fuzz

from bot.client import client
from bot.command_registry import reg
from bot.database import Database
from bot.graphics.nation_globe import (
    _CONTINENT_ALIASES,
    get_nation_globe_image,
    get_random_nation_name,
)
from bot.input_file import to_input_file
from bot.queue_it import queue_it
from bot.database import db

reg("nation", "🌍 Play nation guessing game")


async def _start_new_game(
    message: Message, db: Database, continent: Optional[str] = None
):
    """Start a new nation game (helper used by command and callback)."""
    # Pick a nation (may return None if continent invalid)
    name = (
        get_random_nation_name(continent)
        if continent
        else get_random_nation_name()
    )
    if name is None:
        await message.reply(
            "❌ Your specified continent is wrong or has a typo.\n\n<blockquote>Valid continents: {}</blockquote>\n".format(
                ", ".join(set(_CONTINENT_ALIASES.values()))
            )
        )
        return

    # Try to render globe image (may fail and return None)
    img_bytes = get_nation_globe_image(name)

    # Store game in database (store photo b64 if available)
    photo_b64 = None
    if img_bytes:
        photo_b64 = base64.b64encode(img_bytes).decode("utf-8")

    try:
        await db.create_nation_game(
            chat_id=message.chat.id, nation_name=name, photo_b64=photo_b64
        )
    except Exception:
        # Best-effort: ignore DB failures
        pass

    # Send photo (use queue_it for group chats)
    caption = "🎮 <b>Guess the nation</b>"

    if img_bytes:
        photo = to_input_file(img_bytes, filename="nation.png")
        await message.reply_photo(photo=photo, caption=caption)
    else:
        await message.reply("Guess the nation")


@client.on_message(filters.command(["nation"]))
async def nation_command(
    message: Message,
):
    """Start a nation guessing game. Optional continent argument.

    If a game is already running, ask user whether to resend the photo or start a new game.
    """
    # Check for existing game
    existing = await db.get_nation_game(message.chat.id)
    if existing:
        reply_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    text="📷 Resend Photo",
                    callback_data=f"nation_resend:{message.chat.id}",
                ),
                InlineKeyboardButton(
                    text="🔄 New Game",
                    callback_data=f"nation_new:{message.chat.id}",
                ),
            ]
        ])
        await message.reply(
            "⚠️ There is an ongoing nation game in this chat!\n\n"
            "Do you want me to resend the photo or start a new game?",
            reply_markup=reply_markup,
        )
        return

    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)

    continent: Optional[str] = None
    if len(parts) > 1:
        continent = parts[1].strip()

    await _start_new_game(message, db, continent)


@client.on_callback_query(filters.regex(r"^" + "nation_resend:"))
async def nation_resend_callback(
    callback: CallbackQuery,
):
    chat_id = int(callback.data.split(":")[1])

    # Verify chat ID matches
    if callback.message.chat.id != chat_id:
        await callback.answer("❌ Wrong chat!", show_alert=True)
        return

    game = await db.get_nation_game(chat_id)
    if not game:
        await callback.answer("❌ Game not found!", show_alert=True)
        return

    # Try to resend stored photo
    photo_b64 = game.get("photo_b64")
    if photo_b64:
        photo_bytes = base64.b64decode(photo_b64)

        await callback.message.reply_photo(
            photo=to_input_file(photo_bytes, filename="nation.png"),
            caption="🎮 <b>Guess the nation</b>",
        )
        await callback.answer("✅ Photo resent!")
        return

    # Fallback: try to render on-demand
    img = get_nation_globe_image(game["nation_name"])
    if img:
        await callback.message.reply_photo(
            photo=to_input_file(img, filename="nation.png"),
            caption="🎮 <b>Guess the nation</b>",
        )
        await callback.answer("✅ Photo resent!")
    else:
        await callback.answer("❌ Could not render photo.", show_alert=True)


@client.on_callback_query(filters.regex(r"^" + "nation_new:"))
async def nation_new_callback(
    callback: CallbackQuery,
):
    chat_id = int(callback.data.split(":")[1])

    if callback.message.chat.id != chat_id:
        await callback.answer("❌ Wrong chat!", show_alert=True)
        return

    # Delete existing game and start a fresh one
    game = await db.get_nation_game(chat_id)
    old_nation_word = game["nation_name"]
    await callback.message.reply(
        f"🔄 Starting a new game! The previous nation was <b>{old_nation_word}</b>."
    )
    await db.delete_nation_game(chat_id)
    await _start_new_game(callback.message, db)
    await callback.answer("✅ New game started!")
    await callback.message.delete()


reg(
    "guess",
    "🎯 Guess the nation (handled automatically if nation game is active)",
)


@client.on_message(filters.command(["guess"]), group=1)
async def _guess_cmd(
    message: Message,
):
    return await guess_command(message, db)


async def guess_command(
    message: Message, db: Database, is_single_word: bool = False
):
    """Handle nation guesses. Can be called directly or via middleware for single-word messages.

    Returns early if there's no active nation game (and is_single_word True).
    """
    # Check for active nation game
    game = await db.get_nation_game(message.chat.id)
    if not game:
        if is_single_word:
            return
        # Let 4-pic /guess handler respond when 4-pic game is active.
        if await db.get_four_pic_game(message.chat.id):
            return
        await message.reply(
            "❌ No active nation game in this chat!\nStart one with /nation"
        )
        return

    # Determine guess text
    text = (message.text or "").strip()
    parts = text.split(maxsplit=1)

    if is_single_word:
        guess = parts[0].strip()  # plain message: whole text is the guess
    elif len(parts) > 1:
        guess = parts[1].strip()  # /guess <country>
    else:
        await message.reply(
            "❌ Please provide a nation to guess!\nUsage: /guess <country>"
        )
        return

    if not guess:
        if is_single_word:
            return
        await message.reply(
            "❌ Please provide a nation to guess!\nUsage: /guess <country>"
        )
        return

    # Normalize
    guess_norm = guess.strip().lower()
    correct = game["nation_name"].strip().lower()

    # Use fuzzy matching only for multi-word or dotted official names
    use_fuzzy = (" " in game["nation_name"]) or ("." in game["nation_name"])
    if guess_norm == correct or (
        use_fuzzy and fuzz.WRatio(guess_norm, correct) >= 80
    ):
        # Correct guess
        try:
            await db.add_balance(
                message.from_user.id, 200_000, "Nation game win"
            )
        except Exception:
            pass

        await message.reply(
            f"✅ Correct! <b>{game['nation_name']}</b> — {message.from_user.mention()} get +$200,000. Use /nation [continent] for new nation game."
        )

        try:
            await db.delete_nation_game(message.chat.id)
        except Exception:
            pass
        return

    # If it's a real nation (generate image to verify), send the "here" photo and tell wrong
    img = get_nation_globe_image(guess, dummy_bytes_check_country_only=True)
    if img is not None and not is_single_word:
        await message.reply(f"{guess} is Wrong! try again.")
        return

    # Not a nation — don't interfere (let other handlers/processes run)
    if is_single_word:
        return
    await message.reply("❌ That doesn't look like a recognized nation.")
