"""Gambling games - Ripple, Rbet, Lottery, and bet statistics."""

import html
import random
from typing import List

from pyrogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Message,
)

_NO_PREVIEW = LinkPreviewOptions(is_disabled=True)

from bot.command_registry import reg
from bot.constants import (
    RBET_MULTIPLIER,
    RBET_SNAKE_CHANCE,
    RIPPLE_MAX_BET,
    RIPPLE_MIN_BET,
    RIPPLE_MULTIPLIER,
    RIPPLE_WARN_MIN_BET,
)
from bot.database import Database
from bot.utils import parse_money_amount
from bot.utils import user_mention as util_user_mention
from pyrogram import filters
from bot.client import client
from bot.database import db


def build_chat_message_link(
    chat_id: int | None, message_id: int | None
) -> str | None:
    """Build a Telegram message link when possible."""
    if not chat_id or not message_id:
        return None
    chat_str = str(chat_id)
    if chat_str.startswith("-100"):
        return f"https://t.me/c/{chat_str[4:]}/{message_id}"
    return None


async def format_lottery_participants(
    db: Database, participant_ids: list[int]
) -> str:
    """Format participant IDs as clickable mentions."""
    mentions: list[str] = []
    for participant_id in participant_ids:
        participant = await db.get_user(participant_id)
        name = participant["first_name"] if participant else "Unknown"
        mentions.append(
            f'<a href="tg://user?id={participant_id}">{html.escape(name)}</a>'
        )
    return (
        "\n".join(f"• {mention}" for mention in mentions)
        if mentions
        else "• None"
    )


# ============ RIPPLE GAME ============


def create_ripple_keyboard(
    game_id: int,
    current_level: int,
    current_prize: int,
    next_prize: int,
    history: List[str] = None,
    game_over: bool = False,
    lost: bool = False,
    revealed_snakes: List[int] = None,
    snake_positions_for_levels: dict = None,
) -> InlineKeyboardMarkup:
    """Create ripple game keyboard."""
    rows = []

    # Show last 10 history rows (most recent at bottom)
    if history:
        for _i, row in enumerate(history[-10:]):
            # row format: "position:result" where result is 's' for snake, 'f' for sunflower
            parts = row.split(":")
            if len(parts) == 2:
                pos, result = int(parts[0]), parts[1]
                row_buttons = []
                for j in range(3):
                    if j == pos:
                        if result == "s":
                            row_buttons.append(
                                InlineKeyboardButton(
                                    text="🐍",
                                    callback_data="ripple:dummy:snake",
                                )
                            )
                        else:
                            row_buttons.append(
                                InlineKeyboardButton(
                                    text="🌻", callback_data="ripple:dummy"
                                )
                            )
                    else:
                        # For game over, reveal snakes
                        if (
                            game_over
                            and revealed_snakes
                            and j in revealed_snakes
                        ):
                            row_buttons.append(
                                InlineKeyboardButton(
                                    text="🐍",
                                    callback_data="ripple:dummy:snake",
                                )
                            )
                        else:
                            # Show space for unclicked non-snake tiles
                            row_buttons.append(
                                InlineKeyboardButton(
                                    text=" ", callback_data="ripple:dummy"
                                )
                            )
                rows.append(row_buttons)

    if not game_over:
        # Active grass row
        grass_row = [
            InlineKeyboardButton(
                text="🌿", callback_data=f"ripple:{game_id}:pick:0"
            ),
            InlineKeyboardButton(
                text="🌿", callback_data=f"ripple:{game_id}:pick:1"
            ),
            InlineKeyboardButton(
                text="🌿", callback_data=f"ripple:{game_id}:pick:2"
            ),
        ]
        rows.append(grass_row)

        # Take button row
        rows.append([
            InlineKeyboardButton(
                text=f"💰 Take: ${current_prize:,}",
                callback_data=f"ripple:{game_id}:take",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


def create_ripple_history_keyboard(
    history: List[str],
    won: bool = False,
    lost: bool = False,
    bet_amount: int = 0,
    snake_positions: dict = None,
    level: int = 0,
) -> InlineKeyboardMarkup:
    """Create a history-only keyboard showing past game moves."""
    rows = []

    # Show last 10 history rows
    # Each history entry corresponds to a level (0-indexed)
    start_level = max(0, len(history) - 10)
    for idx, h in enumerate(history[-10:]):
        actual_level = start_level + idx
        parts = h.split(":")
        if len(parts) == 2:
            pos, result = int(parts[0]), parts[1]
            row_buttons = []
            for j in range(3):
                if j == pos:
                    if result == "s":
                        row_buttons.append(
                            InlineKeyboardButton(
                                text="🐍", callback_data="ripple:dummy:snake"
                            )
                        )
                    else:
                        row_buttons.append(
                            InlineKeyboardButton(
                                text="🌻", callback_data="ripple:dummy"
                            )
                        )
                else:
                    # Show snake position if game ended and we have snake data
                    if snake_positions is not None:
                        snake_pos = snake_positions.get(actual_level, -1)
                        if j == snake_pos:
                            row_buttons.append(
                                InlineKeyboardButton(
                                    text="🐍",
                                    callback_data="ripple:dummy:snake",
                                )
                            )
                        else:
                            row_buttons.append(
                                InlineKeyboardButton(
                                    text=" ", callback_data="ripple:dummy"
                                )
                            )
                    else:
                        # Show space for unclicked tiles
                        row_buttons.append(
                            InlineKeyboardButton(
                                text=" ", callback_data="ripple:dummy"
                            )
                        )
            rows.append(row_buttons)

    # Add result indicator
    if won:
        rows.append([
            InlineKeyboardButton(
                text="💰 TOOK WINNINGS 💰", callback_data="ripple:dummy"
            )
        ])
    elif lost:
        rows.append([
            InlineKeyboardButton(
                text="💀 GAME OVER 💀", callback_data="ripple:dummy"
            )
        ])

    # Add restart button if bet_amount is provided
    if bet_amount > 0:
        rows.append([
            InlineKeyboardButton(
                text="🔄 Restart Game",
                callback_data=f"ripple:restart:{bet_amount}",
            )
        ])

    return InlineKeyboardMarkup(inline_keyboard=rows)


reg("ripple", "🎰 Ripple gambling game")


@client.on_message(filters.command(["ripple"]))
async def ripple_command(
    message: Message,
):
    """Start a ripple gambling game."""
    user = message.from_user

    # Parse amount
    amount = 100  # Default
    if message.text:
        parts = message.text.split()
        if len(parts) > 1:
            parsed_amount = parse_money_amount(parts[1])
            if parsed_amount is None or parsed_amount <= 0:
                await message.reply("❌ Invalid amount!")
                return
            amount = parsed_amount

    # Minimum/maximum bet (from constants)
    if amount < RIPPLE_MIN_BET:
        await message.reply(f"❌ Minimum bet is ${RIPPLE_MIN_BET:,}!")
        return

    if amount > RIPPLE_MAX_BET:
        await message.reply(
            f"❌ Maximum bet is ${RIPPLE_MAX_BET:,}! This is to ensure "
        )
        return

    if amount > RIPPLE_WARN_MIN_BET and "CONFIRM" not in message.text:
        await message.reply(
            f"⚠️ <b>High Bet Warning</b>\n\n"
            f"The maximum bet is ${RIPPLE_MAX_BET:,} to ensure responsible gambling.\n"
            f"You are trying to bet ${amount:,}, which is above the recommended limit.\n\n"
            f"If you want to proceed with this bet, please confirm by adding CONFIRM to your command:\n"
            f"/ripple {amount} CONFIRM"
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
    if wallet["balance"] < amount:
        await message.reply(
            f"❌ Insufficient balance!\n"
            f"You have: ${wallet['balance']:,}\n"
            f"Bet amount: ${amount:,}"
        )
        return

    # Deduct amount
    await db.add_balance(user.id, -amount, "Ripple bet")

    # Pre-generate snake positions for each level (0-19)
    # Every level has exactly one snake position (0, 1, or 2)
    # Format: "level:snake_pos,level:snake_pos,..."
    snake_positions = []
    for level in range(20):
        snake_pos = random.randint(0, 2)  # Always a snake, random position
        snake_positions.append(f"{level}:{snake_pos}")
    snake_data = ",".join(snake_positions)

    # Create game in database
    game = await db.fetchrow(
        """
        INSERT INTO ripple_games (user_id, bet_amount, current_prize, chat_id, level, snake_positions)
        VALUES ($1, $2, $2, $3, 0, $4)
        RETURNING *
        """,
        user.id,
        amount,
        message.chat.id,
        snake_data,
    )

    next_prize = int(amount * RIPPLE_MULTIPLIER)
    keyboard = create_ripple_keyboard(game["id"], 0, amount, next_prize)

    sent = await message.reply(
        f"🎰 <b>Ripple Game</b>\n\n"
        f"Find 🌻 to increase prize to <b>${next_prize:,}</b>!\n"
        f"Beware of 🐍 - you lose everything!",
        reply_markup=keyboard,
    )

    # Update message_id
    await db.execute(
        "UPDATE ripple_games SET message_id = $1 WHERE id = $2",
        sent.id,
        game["id"],
    )


# ============ RBET GAME ============


reg("rbet", "🎲 Quick ripple bet")


@client.on_message(filters.command(["rbet"]))
async def rbet_command(
    message: Message,
):
    """Quick ripple bet - keep betting until you lose or take."""
    user = message.from_user

    # Check for existing game
    existing = await db.fetchrow(
        "SELECT * FROM rbet_games WHERE user_id = $1 AND is_active = TRUE",
        user.id,
    )

    if existing:
        # Continue existing game - roll dice (30% snake from constants)
        if random.random() < RBET_SNAKE_CHANCE:
            # Lost!
            await db.execute(
                "UPDATE rbet_games SET is_active = FALSE WHERE id = $1",
                existing["id"],
            )

            # Record stats
            # Track loss: existing["current_prize"]
            original_bet = existing["bet_amount"]
            await db.execute(
                """
                INSERT INTO gambling_stats (user_id, game_type, total_wagered, total_lost, biggest_loss)
                VALUES ($1, 'rbet', $2, $3, $3)
                ON CONFLICT (user_id, game_type) DO UPDATE SET
                    total_wagered = gambling_stats.total_wagered + $2,
                    total_lost = gambling_stats.total_lost + $3,
                    biggest_loss = GREATEST(gambling_stats.biggest_loss, $3)
                """,
                user.id,
                original_bet,
                original_bet,
            )

            profit_lost = existing["current_prize"] - existing["bet_amount"]
            await message.reply(
                f"🐍 <b>You lost!</b>\n\n"
                f"You lost your ${existing['bet_amount']:,} bet"
                + (
                    f" and ${profit_lost:,} profit..."
                    if profit_lost > 0
                    else "..."
                )
            )
        else:
            # Won this round!
            new_prize = int(existing["current_prize"] * RBET_MULTIPLIER)
            await db.execute(
                "UPDATE rbet_games SET current_prize = $1, level = level + 1, chat_id = $2, message_id = $3 WHERE id = $4",
                new_prize,
                message.chat.id,
                message.id,
                existing["id"],
            )

            next_prize = int(new_prize * RBET_MULTIPLIER)
            await message.reply(
                f"🌻 <b>Hurray!</b> Prize: <b>${new_prize:,}</b>\n\n"
                f"⚠️ <i>Still 20% snake next round — one bad flip wipes it.</i>\n\n"
                f"/rbet → Make it ${next_prize:,}\n"
                f"/rtake → Take prize, don't be greedy ;)"
            )
        return

    # Start new game
    amount = 100  # Default
    if message.text:
        parts = message.text.split()
        if len(parts) > 1:
            parsed_amount = parse_money_amount(parts[1])
            if parsed_amount is None or parsed_amount <= 0:
                await message.reply("❌ Invalid amount!")
                return
            amount = parsed_amount

    if amount < RIPPLE_MIN_BET:
        await message.reply(f"❌ Minimum bet is ${RIPPLE_MIN_BET:,}!")
        return
    if amount > RIPPLE_MAX_BET:
        await message.reply(f"❌ Maximum bet is ${RIPPLE_MAX_BET:,}!")
        return

    # Ensure user exists
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Check balance
    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < amount:
        await message.reply(
            f"❌ Insufficient balance!\nYou have: ${wallet['balance']:,}"
        )
        return

    # Deduct amount
    await db.add_balance(user.id, -amount, "Rbet bet")

    # Create game
    await db.execute(
        """
        INSERT INTO rbet_games (user_id, bet_amount, current_prize, chat_id, message_id)
        VALUES ($1, $2, $2, $3, $4)
        """,
        user.id,
        amount,
        message.chat.id,
        message.id,
    )

    next_prize = int(amount * RBET_MULTIPLIER)
    await message.reply(
        f"🎲 <b>Rbet Started!</b> Prize: <b>${amount:,}</b>\n\n"
        f"⚠️ <i>Each round: 20% snake = you lose everything, 80% sunflower = you push.</i>\n\n"
        f"/rbet → Make it ${next_prize:,}\n"
        f"/rtake → Take prize"
    )


@client.on_message(filters.command(["rtake"]))
async def rtake_command(
    message: Message,
):
    """Take winnings from rbet game."""
    user = message.from_user

    existing = await db.fetchrow(
        "SELECT * FROM rbet_games WHERE user_id = $1 AND is_active = TRUE",
        user.id,
    )

    if not existing:
        await message.reply(
            "❌ You don't have an active rbet game!\nStart one with /rbet [amount]"
        )
        return

    # End game and give prize
    prize = existing["current_prize"]
    await db.execute(
        "UPDATE rbet_games SET is_active = FALSE WHERE id = $1", existing["id"]
    )

    await db.add_balance(user.id, prize, "Rbet winnings")

    # Record stats
    profit = prize - existing["bet_amount"]
    await db.execute(
        """
        INSERT INTO gambling_stats (user_id, game_type, total_wagered, total_won, biggest_win)
        VALUES ($1, 'rbet', $2, $3, $3)
        ON CONFLICT (user_id, game_type) DO UPDATE SET
            total_wagered = gambling_stats.total_wagered + $2,
            total_won = gambling_stats.total_won + $3,
            biggest_win = GREATEST(gambling_stats.biggest_win, $3)
        """,
        user.id,
        existing["bet_amount"],
        profit,
    )
    from bot.achievements import (
        check_gambling_achievements,
        check_money_achievements,
    )

    await check_gambling_achievements(db, user.id)
    await check_money_achievements(db, user.id)

    await message.reply(
        f"💰 <b>You took ${prize:,}!</b>\n\n"
        f"Bet: ${existing['bet_amount']:,}\n"
        f"Profit: <b>${profit:,}</b> 🎉"
    )


# ============ LOTTERY ============


reg("lottery", "🎟️ Start/join lottery")


@client.on_message(filters.command(["lottery"]))
async def lottery_command(
    message: Message,
):
    """Start or join a lottery."""
    user = message.from_user

    # Parse amount
    amount = None
    if message.text:
        parts = message.text.split()
        if len(parts) > 1 and parts[1].isdigit():
            amount = int(parts[1])

    if not amount:
        await message.reply(
            "🎟️ <b>Lottery</b>\n\n"
            "Start a lottery with /lottery [amount]\n"
            "Each player stakes that amount.\n"
            "Winner takes all!\n\n"
            "Example: /lottery 1000"
        )
        return

    if amount < 100:
        await message.reply("❌ Minimum lottery is $100!")
        return

    # Ensure user exists
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Check balance
    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < amount:
        await message.reply(
            f"❌ Insufficient balance!\nYou have: ${wallet['balance']:,}"
        )
        return

    # One active lottery per chat: require explicit replacement confirmation.
    existing = await db.fetchrow(
        """
        SELECT * FROM lotteries
        WHERE chat_id = $1 AND is_active = TRUE
        ORDER BY created_at DESC
        LIMIT 1
        """,
        message.chat.id,
    )
    if existing:
        link = build_chat_message_link(
            existing["chat_id"], existing["message_id"]
        )
        participants_text = await format_lottery_participants(
            db, existing["participants"] or []
        )
        prompt = (
            "⚠️ <b>An active lottery already exists in this chat.</b>\n\n"
            f"💰 Stake: <b>${existing['stake_amount']:,}</b>\n"
            f"👥 Participants: {len(existing['participants'] or [])}\n"
            f"🧾 Participants:\n{participants_text}\n\n"
        )
        if link:
            prompt += (
                f'🔗 Existing lottery: <a href="{link}">Open message</a>\n\n'
            )
        else:
            prompt += "🔗 Existing lottery message link is unavailable.\n\n"
        prompt += f"Do you want to replace it with a new lottery at <b>${amount:,}</b>?"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Start New Lottery",
                        callback_data=(
                            f"lottery_replace:confirm:{user.id}:{existing['id']}:{amount}"
                        ),
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="❌ Keep Current Lottery",
                        callback_data=f"lottery_replace:cancel:{user.id}",
                    ),
                ],
            ]
        )
        await message.reply(prompt, reply_markup=keyboard)
        return

    # Deduct amount
    await db.add_balance(user.id, -amount, "Lottery entry")

    # Create lottery
    lottery = await db.fetchrow(
        """
        INSERT INTO lotteries (host_id, stake_amount, chat_id, participants)
        VALUES ($1, $2, $3, $4)
        RETURNING *
        """,
        user.id,
        amount,
        message.chat.id,
        [user.id],
    )

    user_mention = util_user_mention(user)
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

    sent = await message.reply(
        f"🎟️ <b>Lottery Started!</b>\n\n"
        f"💰 Stake: <b>${amount:,}</b>\n"
        f"🏆 Winner gets all!\n\n"
        f"👤 Host: {user_mention}\n"
        f"👥 Participants: 1\n"
        f"🧾 Participants:\n• {user_mention}\n"
        f"💵 Prize Pool: <b>${amount:,}</b>\n\n"
        f"<i>Click Join to enter!</i>",
        reply_markup=keyboard,
    )

    await db.execute(
        "UPDATE lotteries SET message_id = $1 WHERE id = $2",
        sent.id,
        lottery["id"],
    )


# ============ BETS STATISTICS ============


reg("bets", "📊 Gambling statistics")


@client.on_message(filters.command(["bets", "betstats"]))
async def bets_command(
    message: Message,
):
    """Show gambling statistics."""
    user = message.from_user

    # Ensure user exists
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Get ripple stats
    ripple_stats = await db.fetchrow(
        "SELECT * FROM gambling_stats WHERE user_id = $1 AND game_type = 'ripple'",
        user.id,
    )

    # Get rbet stats
    rbet_stats = await db.fetchrow(
        "SELECT * FROM gambling_stats WHERE user_id = $1 AND game_type = 'rbet'",
        user.id,
    )

    # Get ongoing games
    ongoing_ripple = await db.fetchrow(
        "SELECT * FROM ripple_games WHERE user_id = $1 AND is_active = TRUE",
        user.id,
    )
    ongoing_rbet = await db.fetchrow(
        "SELECT * FROM rbet_games WHERE user_id = $1 AND is_active = TRUE",
        user.id,
    )

    response = "🎰 <b>Gambling Statistics</b>\n\n"

    # Ripple stats
    response += "<b>🌻 Ripple:</b>\n"
    if ripple_stats:
        response += (
            f"   Total wagered: ${ripple_stats['total_wagered']:,}\n"
            f"   Total profit: ${ripple_stats['total_won']:,}\n"
            f"   Total lost: ${ripple_stats['total_lost']:,}\n"
            f"   Biggest win: ${ripple_stats['biggest_win']:,}\n"
            f"   Biggest loss: ${ripple_stats['biggest_loss']:,}\n"
        )
    else:
        response += "   No games played yet\n"

    if ongoing_ripple:
        chat_id = str(ongoing_ripple["chat_id"])
        if chat_id.startswith("-100"):
            chat_id = chat_id[4:]
        response += f'   🎮 Ongoing: <a href="https://t.me/c/{chat_id}/{ongoing_ripple["message_id"]}">Link</a>\n'
    else:
        response += "   🎮 Ongoing: None\n"

    response += "\n"

    # Rbet stats
    response += "<b>🎲 Rbet:</b>\n"
    if rbet_stats:
        response += (
            f"   Total wagered: ${rbet_stats['total_wagered']:,}\n"
            f"   Total profit: ${rbet_stats['total_won']:,}\n"
            f"   Total lost: ${rbet_stats['total_lost']:,}\n"
            f"   Biggest win: ${rbet_stats['biggest_win']:,}\n"
            f"   Biggest loss: ${rbet_stats['biggest_loss']:,}\n"
        )
    else:
        response += "   No games played yet\n"

    if ongoing_rbet:
        chat_id = str(ongoing_rbet["chat_id"])
        if chat_id.startswith("-100"):
            chat_id = chat_id[4:]
        response += f'   🎮 Ongoing: <a href="https://t.me/c/{chat_id}/{ongoing_rbet["message_id"]}">Link</a>\n'
    else:
        response += "   🎮 Ongoing: None\n"

    await message.reply(response, link_preview_options=_NO_PREVIEW)
