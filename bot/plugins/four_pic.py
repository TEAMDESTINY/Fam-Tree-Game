"""4-Pic Word Game - Guess the word from 4 images."""

import asyncio
import base64
import html
import io
import json
import os
import random
from pathlib import Path

import aiohttp
from PIL import Image
from pyrogram import filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.client import client
from bot.command_registry import reg
from bot.database import Database
from bot.input_file import to_input_file
from bot.database import db

# Constants
BASE_REWARD = 100_000
HINT_PENALTY = 5_000
COLLAGE_SIZE = 800
TILE_SIZE = 400

# Load word database
WORD_DB_PATH = (
    Path(__file__).parent.parent.parent / "assets" / "4pic1word" / "words.json"
)
with open(WORD_DB_PATH, "r") as f:
    WORDS = json.load(f)

# Pexels API
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY")
PEXELS_API_URL = "https://api.pexels.com/v1/search"


# ── Helpers ──────────────────────────────────────────────────────────────


async def fetch_pexels_images(query: str) -> list[str]:
    """Fetch 4 images from Pexels API for the given query."""
    if not PEXELS_API_KEY:
        raise ValueError("PEXELS_API_KEY not configured")

    headers = {"Authorization": PEXELS_API_KEY}

    SEARCH_PATTERNS = [
        "things related to {}",
        "objects associated with {}",
        "lifestyle related to {}",
        "scene related to {}",
        "concept of {} in real life",
    ]

    async def get_photos(
        page: int, take: int, obvious_clue: bool = False
    ) -> list[str]:
        async with aiohttp.ClientSession() as session:
            selected_urls = []

            params = {
                "query": random.choice(SEARCH_PATTERNS).format(query)
                if not obvious_clue
                else f"{query} clues",
                "per_page": 20,
                "page": page,
                # "orientation": "square", # XXX: it made many photos to not appear and then very less likely photo appear
            }
            async with session.get(
                PEXELS_API_URL, headers=headers, params=params
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    photos = data.get("photos", [])
                    photos = random.sample(photos, min(take, len(photos)))
                    selected_urls.extend([
                        photo["src"]["original"] for photo in photos
                    ])

                    return selected_urls

        return []

    selected_urls = []

    # get two most obvious clues for the word from page 1
    urls = await get_photos(page=1, take=2, obvious_clue=True)
    selected_urls.extend(urls)

    # get 2 related
    for _ in range(2):
        urls = await get_photos(page=1, take=1)
        selected_urls.extend(urls)

    if len(selected_urls) < 4:
        raise Exception(f"No photos found for query: {query}")

    random.shuffle(selected_urls)
    return selected_urls[:4]


async def download_image(url: str) -> bytes:
    """Download image from URL."""
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as resp:
            if resp.status != 200:
                raise Exception(f"Failed to download image: {url}")
            return await resp.read()


async def create_collage(image_urls: list[str]) -> str:
    """Create 2x2 collage from 4 image URLs and return base64 string."""
    # Download all 4 images
    tasks = [download_image(url) for url in image_urls]
    image_bytes = await asyncio.gather(*tasks)

    # Open and resize images
    images = []
    for img_bytes in image_bytes:
        img = Image.open(io.BytesIO(img_bytes))
        # Convert to RGB if necessary
        if img.mode != "RGB":
            img = img.convert("RGB")
        # Resize to TILE_SIZE x TILE_SIZE
        img = img.resize((TILE_SIZE, TILE_SIZE), Image.Resampling.LANCZOS)
        images.append(img)

    # Create 2x2 collage
    collage = Image.new("RGB", (COLLAGE_SIZE, COLLAGE_SIZE), color="white")

    # Paste images in 2x2 grid
    positions = [
        (0, 0),  # Top-left
        (TILE_SIZE, 0),  # Top-right
        (0, TILE_SIZE),  # Bottom-left
        (TILE_SIZE, TILE_SIZE),  # Bottom-right
    ]

    for img, pos in zip(images, positions):
        collage.paste(img, pos)

    # Convert to base64
    buffer = io.BytesIO()
    collage.save(buffer, format="JPEG", quality=85)
    buffer.seek(0)

    return base64.b64encode(buffer.read()).decode("utf-8")


def get_random_word() -> dict:
    """Get a random word from the word database."""
    return random.choice(WORDS)


def format_word_hint(word: str, revealed_indices: list[int]) -> str:
    """Format word with revealed letters and underscores."""
    result = []
    for i, char in enumerate(word):
        if i in revealed_indices:
            result.append(char)
        else:
            result.append("_")
    return "".join(result)


def calculate_reward(hint_count: int) -> int:
    """Calculate reward based on number of hints used."""
    return max(0, BASE_REWARD - (HINT_PENALTY * hint_count))


async def send_collage_photo(message: Message, photo_b64: str):
    """Send collage photo to chat."""
    photo_bytes = base64.b64decode(photo_b64)
    await message.reply_photo(
        photo=to_input_file(photo_bytes, filename="collage.jpg"),
        caption="🎮 <b>4-Pic Word Game!</b>\n\nGuess the word from these 4 images!\n\nUse /hint to get hints\nUse /guess &lt;word&gt; to submit your guess",
    )


# ── Commands ─────────────────────────────────────────────────────────────


reg("4pic", "🎮 Play 4-pic word guessing game")


@client.on_message(filters.command(["4pic"]))
async def four_pic_command(
    message: Message,
):
    """Start a new 4-pic game or resend existing one."""
    # Check if there's an active game
    existing_game = await db.get_four_pic_game(message.chat.id)

    if existing_game:
        # Ask if user wants to resend photo
        reply_markup = InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    text="📷 Resend Photo",
                    callback_data=f"4pic_resend:{message.chat.id}",
                ),
                InlineKeyboardButton(
                    text="🔄 New Game",
                    callback_data=f"4pic_new:{message.chat.id}",
                ),
            ]
        ])

        await message.reply(
            "⚠️ There is an ongoing 4-pic game in this chat!\n\n"
            "Do you want me to resend the photo or start a new game?",
            reply_markup=reply_markup,
        )
        return

    # Start new game
    await _start_new_game(message, db)


async def _start_new_game(message: Message, db: Database):
    """Start a brand new 4-pic game."""
    # Get random word
    word_data = get_random_word()
    word = word_data["word"]
    category = word_data["category"]
    hint_msg = word_data["hint"]

    try:
        # Fetch images and create collage
        image_urls = await fetch_pexels_images(word)
        photo_b64 = await create_collage(image_urls)

        # Save to database
        await db.create_four_pic_game(
            chat_id=message.chat.id,
            word=word,
            category=category,
            hint_message=hint_msg,
            photo_b64=photo_b64,
        )

        # Send collage
        await send_collage_photo(message, photo_b64)

    except Exception as e:
        await message.reply(f"❌ Failed to start game: {str(e)}")
        return


reg("hint", "💡 Get a hint for the current 4-pic game")


@client.on_message(filters.command(["hint"]))
async def hint_command(
    message: Message,
):
    """Get progressive hints for the current game."""
    # Check for active game
    game = await db.get_four_pic_game(message.chat.id)

    if not game:
        await message.reply(
            "❌ No active 4-pic game in this chat!\nStart one with /4pic"
        )
        return

    # Count hints given
    hint_count = 0
    if game["is_category_hint_given"]:
        hint_count += 1
    if game["is_hint_message_given"]:
        hint_count += 1

    revealed_letters = await db.get_four_pic_revealed_letters(message.chat.id)
    # Ensure it's a list of integers
    if isinstance(revealed_letters, str):
        import json

        revealed_letters = (
            json.loads(revealed_letters) if revealed_letters else []
        )
    hint_count += len(revealed_letters)

    # Limit to 5 total hints
    if hint_count >= 7:
        await message.reply(
            "⚠️ Maximum 7 hints reached! Use /guess to submit your answer."
        )
        return

    # Determine next hint
    if not game["is_category_hint_given"]:
        # Hint #1: Category
        await message.reply(
            f"<blockquote>Hint #1: Word is from category: <code>{html.escape(game['category'])}</code></blockquote>"
        )
        await db.update_four_pic_hint_category(message.chat.id)

    elif not game["is_hint_message_given"]:
        # Hint #2: Hint message
        await message.reply(
            f"<blockquote>Hint #2: Word hint message is: <code>{html.escape(game['hint_message'])}</code></blockquote>"
        )
        await db.update_four_pic_hint_message(message.chat.id)

    else:
        # Hint #3+: Reveal random letter (but cap at 5 total)
        word = game["word"]
        word_len = len(word)

        # Ensure revealed_letters is a list
        if isinstance(revealed_letters, str):
            import json

            revealed_letters = (
                json.loads(revealed_letters) if revealed_letters else []
            )

        # Find unrevealed indices
        unrevealed = [i for i in range(word_len) if i not in revealed_letters]

        if not unrevealed:
            await message.reply("⚠️ All letters have been revealed!")
            return

        # Check if we'd exceed 5 hints
        if hint_count + 1 > 7:
            await message.reply(
                "⚠️ Maximum 7 hints reached! Use /guess to submit your answer."
            )
            return

        # Pick random unrevealed index
        new_index = random.choice(unrevealed)
        await db.add_four_pic_revealed_letter(message.chat.id, new_index)

        # Get updated revealed letters
        revealed_letters = await db.get_four_pic_revealed_letters(
            message.chat.id
        )

        # Format hint
        hint_text = format_word_hint(word, revealed_letters)

        hint_num = hint_count + 1
        await message.reply(
            f"<blockquote>Hint #{hint_num}: Word looks like: <code>{hint_text}</code></blockquote>"
        )


# ── Callback Handlers ────────────────────────────────────────────────────


@client.on_callback_query(filters.regex(r"^" + "4pic_resend:"))
async def resend_photo_callback(
    callback: CallbackQuery,
):
    """Resend the collage photo."""
    chat_id = int(callback.data.split(":")[1])

    # Verify chat ID matches
    if callback.message.chat.id != chat_id:
        await callback.answer("❌ Wrong chat!", show_alert=True)
        return

    game = await db.get_four_pic_game(chat_id)
    if not game:
        await callback.answer("❌ Game not found!", show_alert=True)
        return

    await send_collage_photo(callback.message, game["photo_b64"])
    await callback.answer("✅ Photo resent!")


@client.on_callback_query(filters.regex(r"^" + "4pic_new:"))
async def new_game_callback(
    callback: CallbackQuery,
):
    """Start a new game, replacing the existing one."""
    chat_id = int(callback.data.split(":")[1])

    # Verify chat ID matches
    if callback.message.chat.id != chat_id:
        await callback.answer("❌ Wrong chat!", show_alert=True)
        return

    # Delete existing game
    await db.delete_four_pic_game(chat_id)

    # Start new game
    await _start_new_game(callback.message, db)
    await callback.answer("✅ New game started!")


# ── Message Handler (Guesses) ────────────────────────────────────────────


reg("guess", "🎯 Guess the word in 4-pic game")


@client.on_message(filters.command(["guess"]))
async def _guess_cmd(
    message: Message,
):
    return await guess_command(message, db)


async def guess_command(
    message: Message, db: Database, is_single_word: bool = False
):
    """Handle word guesses from users."""
    # Check for active game
    game = await db.get_four_pic_game(message.chat.id)

    if not game:
        # If this is a single-word (typed) message, the outer middleware will
        # call nation.guess_command after this handler — avoid forwarding to
        # nation here to prevent duplicate checks.
        if is_single_word:
            return
        # Let nation /guess handler respond when nation game is active.
        if await db.get_nation_game(message.chat.id):
            return
        await message.reply(
            "❌ No active 4-pic game in this chat!\nStart one with /4pic"
        )
        return

    # Get the guess from command arguments
    text = message.text.strip()
    parts = text.split(maxsplit=1)

    if len(parts) < 2 and not is_single_word:
        await message.reply(
            "❌ Please provide a word to guess!\nUsage: /guess &lt;word&gt;"
        )
        return

    if is_single_word:
        guess = text.strip().lower()
    else:
        guess = parts[1].strip().lower()
    correct_word = game["word"].lower()

    if guess == correct_word:
        # Correct guess!
        # Calculate reward
        hint_count = 0
        if game["is_category_hint_given"]:
            hint_count += 1
        if game["is_hint_message_given"]:
            hint_count += 1

        revealed_letters = await db.get_four_pic_revealed_letters(
            message.chat.id
        )
        # Ensure it's a list of integers
        if isinstance(revealed_letters, str):
            import json

            revealed_letters = (
                json.loads(revealed_letters) if revealed_letters else []
            )
        hint_count += len(revealed_letters)

        reward = calculate_reward(hint_count)

        # Ensure user exists before touching wallet (FK constraint)
        await db.upsert_user(
            message.from_user.id,
            message.from_user.username,
            message.from_user.first_name,
        )
        await db.add_balance(message.from_user.id, reward, "4-pic game win")

        # Announce winner
        winner_mention = f"<a href='tg://user?id={message.from_user.id}'>{html.escape(message.from_user.first_name)}</a>"
        await message.reply(
            f"🎉 <b>CORRECT!</b>\n\n"
            f"{winner_mention} guessed the word: <b>{html.escape(game['word'].upper())}</b>\n\n"
            f"💰 Reward: <b>${reward:,}</b>\n"
            f"💡 Hints used: {hint_count}"
        )

        # Delete game
        await db.delete_four_pic_game(message.chat.id)
    else:
        if is_single_word:
            return
        # Wrong guess
        await message.reply(
            "❌ <b>Wrong guess for 4pic!</b>\n\nTry again or use /hint for help"
        )
