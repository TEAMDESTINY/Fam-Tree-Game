"""Leaderboards and achievements system."""

import html

from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.command_registry import reg
from bot.constants import ACHIEVEMENTS
from bot.database import Database
from bot.queue_it import queue_it
from pyrogram import filters
from bot.client import client
from bot.database import db


# Appended to any leaderboard query that joins `users u`
_NOT_BLOCKED = "AND u.user_id NOT IN (SELECT user_id FROM blocked_users)"


def format_price(amount: int) -> str:
    """Format price with $ and commas."""
    return f"${amount:,}"


def get_rank_emoji(rank: int) -> str:
    """Get emoji for leaderboard rank."""
    if rank == 1:
        return "🥇"
    elif rank == 2:
        return "🥈"
    elif rank == 3:
        return "🥉"
    else:
        return f"#{rank}"


reg("leaderboard", "🏆 View leaderboards [/top /lb]")


@client.on_message(filters.command(["top", "leaderboard", "lb"]))
async def leaderboard_command(
    message: Message,
):
    """Show leaderboard menu."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    text = "🏆 <b>Leaderboards</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "Select a category to view:"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Richest", callback_data=f"top:balance:{user.id}"
                ),
                InlineKeyboardButton(
                    text="📈 Earners", callback_data=f"top:earned:{user.id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🌻 Gardeners", callback_data=f"top:garden:{user.id}"
                ),
                InlineKeyboardButton(
                    text="🏭 Factory", callback_data=f"top:factory:{user.id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎣 Fishers", callback_data=f"top:fishing:{user.id}"
                ),
                InlineKeyboardButton(
                    text="🧪 Crafters", callback_data=f"top:craft:{user.id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💼 Jobs", callback_data=f"top:jobs:{user.id}"
                ),
                InlineKeyboardButton(
                    text="👥 Gangs", callback_data=f"top:gangs:{user.id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🏆 Achievements",
                    callback_data=f"top:achievements:{user.id}",
                ),
                InlineKeyboardButton(
                    text="🪦 Funeral Donors",
                    callback_data=f"top:funeral:{user.id}",
                ),
            ],
        ]
    )

    await message.reply(text, reply_markup=keyboard)


@client.on_callback_query(filters.regex(r"^" + "top:"))
async def leaderboard_callback(
    callback: CallbackQuery,
):
    """Show specific leaderboard."""
    user = callback.from_user

    parts = callback.data.split(":")
    category = parts[1]

    # Check if this leaderboard message belongs to the user
    # If callback has user_id (format: top:category:user_id or top:balance:wallet:user_id), verify ownership
    owner_id = None
    if len(parts) >= 3 and parts[-1].isdigit():
        owner_id = int(parts[-1])
    elif (
        len(parts) >= 4
        and parts[2] in ("wallet", "bank")
        and parts[3].isdigit()
    ):
        owner_id = int(parts[3])

    # Block ownership for all leaderboards - only the person who opened can view
    if owner_id and user.id != owner_id:
        await callback.answer(
            "❌ This leaderboard is not for you! Use /top to open your own.",
            show_alert=True,
        )
        return

    # Handle menu separately
    if category == "menu":
        text = "🏆 <b>Leaderboards</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"
        text += "Select a category to view:"

        owner_id = (
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
        )

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💰 Richest",
                        callback_data=f"top:balance:{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text="📈 Earners",
                        callback_data=f"top:earned:{owner_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🌻 Gardeners",
                        callback_data=f"top:garden:{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text="🏭 Factory",
                        callback_data=f"top:factory:{owner_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🎣 Fishers",
                        callback_data=f"top:fishing:{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text="🧪 Crafters",
                        callback_data=f"top:craft:{owner_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="💼 Jobs", callback_data=f"top:jobs:{owner_id}"
                    ),
                    InlineKeyboardButton(
                        text="👥 Gangs", callback_data=f"top:gangs:{owner_id}"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🏆 Achievements",
                        callback_data=f"top:achievements:{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text="🪦 Funeral Donors",
                        callback_data=f"top:funeral:{owner_id}",
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
        await callback.answer()
        return

    # Initialize leaders and keyboard to handle cases where category doesn't match
    leaders = []
    keyboard = None

    if category == "balance":
        # Support sub-categories: balance (total), balance_wallet, balance_bank
        # Format: top:balance:wallet:user_id or top:balance:user_id
        balance_type = "total"
        owner_id = user.id
        if (
            len(parts) >= 4
            and parts[2] in ("wallet", "bank")
            and parts[3].isdigit()
        ):
            balance_type = parts[2]
            owner_id = int(parts[3])
        elif len(parts) >= 3 and parts[2].isdigit():
            owner_id = int(parts[2])

        if balance_type == "wallet":
            leaders = await db.fetch(
                f"""
                SELECT u.user_id, u.first_name, u.username,
                       w.balance as amount
                FROM wallets w
                JOIN users u ON w.user_id = u.user_id
                WHERE TRUE {_NOT_BLOCKED}
                ORDER BY w.balance DESC
                LIMIT 10
                """
            )
            text = "💰 <b>Richest Players - Wallet</b>\n"
            text += "━━━━━━━━━━━━━━━━\n\n"
            for i, row in enumerate(leaders, 1):
                name = row["first_name"] or row["username"] or "Unknown"
                emoji = get_rank_emoji(i)
                text += f"{emoji} {html.escape(name)}: {format_price(row['amount'])}\n"

        elif balance_type == "bank":
            leaders = await db.fetch(
                f"""
                SELECT u.user_id, u.first_name, u.username,
                       COALESCE(b.balance, 0) as amount
                FROM bank_accounts b
                JOIN users u ON b.user_id = u.user_id
                WHERE TRUE {_NOT_BLOCKED}
                ORDER BY b.balance DESC
                LIMIT 10
                """
            )
            text = "🏦 <b>Richest Players - Bank</b>\n"
            text += "━━━━━━━━━━━━━━━━\n\n"
            for i, row in enumerate(leaders, 1):
                name = row["first_name"] or row["username"] or "Unknown"
                emoji = get_rank_emoji(i)
                text += f"{emoji} {html.escape(name)}: {format_price(row['amount'])}\n"

        else:  # total (default)
            # Richest players (wallet + bank)
            leaders = await db.fetch(
                f"""
                SELECT u.user_id, u.first_name, u.username,
                       w.balance + COALESCE(b.balance, 0) as total
                FROM wallets w
                JOIN users u ON w.user_id = u.user_id
                LEFT JOIN bank_accounts b ON w.user_id = b.user_id
                WHERE TRUE {_NOT_BLOCKED}
                ORDER BY (w.balance + COALESCE(b.balance, 0)) DESC
                LIMIT 10
                """
            )
            text = "💰 <b>Richest Players - Total</b>\n"
            text += "━━━━━━━━━━━━━━━━\n\n"
            for i, row in enumerate(leaders, 1):
                name = row["first_name"] or row["username"] or "Unknown"
                emoji = get_rank_emoji(i)
                total = row["total"]
                text += f"{emoji} {html.escape(name)}: {format_price(total)}\n"

        # Radio button-style keyboard
        def radio_btn(label: str, active: bool, callback: str) -> str:
            return f"{'🔘' if active else '⚪'} {label}"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=radio_btn("Total", balance_type == "total", ""),
                        callback_data=f"top:balance:total:{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text=radio_btn("Wallet", balance_type == "wallet", ""),
                        callback_data=f"top:balance:wallet:{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text=radio_btn("Bank", balance_type == "bank", ""),
                        callback_data=f"top:balance:bank:{owner_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ],
            ]
        )

    elif category == "earned":
        # Most earned
        leaders = await db.fetch(
            f"""
            SELECT u.user_id, u.first_name, u.username, w.total_earned
            FROM wallets w
            JOIN users u ON w.user_id = u.user_id
            WHERE TRUE {_NOT_BLOCKED}
            ORDER BY w.total_earned DESC
            LIMIT 10
            """
        )

        text = "📈 <b>Top Earners (All Time)</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"

        for i, row in enumerate(leaders, 1):
            name = row["first_name"] or row["username"] or "Unknown"
            emoji = get_rank_emoji(i)
            text += f"{emoji} {html.escape(name)}: {format_price(row['total_earned'])}\n"

        owner_id = (
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ]
            ]
        )

    elif category == "garden":
        # Best gardeners (by garden size and harvests)
        leaders = await db.fetch(
            f"""
            SELECT u.user_id, u.first_name, u.username, g.size,
                   COALESCE(SUM(i.quantity), 0) as total_harvest
            FROM gardens g
            JOIN users u ON g.owner_id = u.user_id
            LEFT JOIN inventory i ON i.user_id = u.user_id AND i.item_type = 'harvest'
            WHERE TRUE {_NOT_BLOCKED}
            GROUP BY u.user_id, u.first_name, u.username, g.size
            ORDER BY g.size DESC, total_harvest DESC
            LIMIT 10
            """
        )

        text = "🌻 <b>Top Gardeners</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"

        for i, row in enumerate(leaders, 1):
            name = row["first_name"] or row["username"] or "Unknown"
            emoji = get_rank_emoji(i)
            text += f"{emoji} {html.escape(name)}: {row['size']}×{row['size']} garden, {row['total_harvest']} harvested\n"

        owner_id = (
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ]
            ]
        )

    elif category == "factory":
        # Factory owners by total workers
        leaders = await db.fetch(
            f"""
            SELECT u.user_id, u.first_name, u.username,
                   COUNT(DISTINCT f.id) as factory_count,
                   COUNT(DISTINCT wa.worker_id) as worker_count
            FROM factories f
            JOIN users u ON f.owner_id = u.user_id
            LEFT JOIN worker_assignments wa ON wa.factory_id = f.id
            WHERE TRUE {_NOT_BLOCKED}
            GROUP BY u.user_id, u.first_name, u.username
            ORDER BY worker_count DESC, factory_count DESC
            LIMIT 10
            """
        )

        text = "🏭 <b>Top Factory Owners</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"

        for i, row in enumerate(leaders, 1):
            name = row["first_name"] or row["username"] or "Unknown"
            emoji = get_rank_emoji(i)
            text += f"{emoji} {html.escape(name)}: {row['factory_count']} factories, {row['worker_count']} workers\n"

        owner_id = (
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ]
            ]
        )

    elif category == "fishing":
        # Top fishers
        leaders = await db.fetch(
            f"""
            SELECT u.user_id, u.first_name, u.username,
                   COALESCE(fs.total_caught, 0) as caught
            FROM users u
            LEFT JOIN fishing_stats fs ON fs.user_id = u.user_id
            WHERE fs.total_caught > 0 {_NOT_BLOCKED}
            ORDER BY fs.total_caught DESC
            LIMIT 10
            """
        )

        text = "🎣 <b>Top Fishers</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"

        for i, row in enumerate(leaders, 1):
            name = row["first_name"] or row["username"] or "Unknown"
            emoji = get_rank_emoji(i)
            text += f"{emoji} {html.escape(name)}: {row['caught']} caught\n"

        owner_id = (
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ]
            ]
        )

    elif category == "craft":
        # Top crafters - count number of unlocked crafts
        leaders = await db.fetch(
            f"""
            SELECT u.user_id, u.first_name, u.username,
                   jsonb_array_length(uc.crafts) as craft_count
            FROM unlocked_crafts uc
            JOIN users u ON u.user_id = uc.user_id
            WHERE TRUE {_NOT_BLOCKED}
            ORDER BY craft_count DESC
            LIMIT 10
            """
        )

        text = "🧪 <b>Top Crafters</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"

        for i, row in enumerate(leaders, 1):
            name = row["first_name"] or row["username"] or "Unknown"
            emoji = get_rank_emoji(i)
            text += f"{emoji} {html.escape(name)}: {row['craft_count']} discoveries\n"

        owner_id = (
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ]
            ]
        )

    elif category == "jobs":
        # Support sub-categories: jobs (active), jobs:police, jobs:thief, jobs:gangster, jobs:doctor
        # Format: top:jobs:police:user_id or top:jobs:user_id
        job_filter = "active"
        owner_id = user.id

        if (
            len(parts) >= 4
            and parts[2] in ("police", "thief", "gangster", "doctor")
            and parts[3].isdigit()
        ):
            job_filter = parts[2]
            owner_id = int(parts[3])
        elif len(parts) >= 3 and parts[2] in (
            "police",
            "thief",
            "gangster",
            "doctor",
        ):
            job_filter = parts[2]
        elif len(parts) >= 3 and parts[2].isdigit():
            owner_id = int(parts[2])

        job_emojis = {
            "police": "👮",
            "thief": "🥷",
            "gangster": "🦹",
            "doctor": "👨‍⚕️",
        }

        if job_filter == "active":
            # Show users' current/active job
            leaders = await db.fetch(
                f"""
                SELECT u.user_id, u.first_name, u.username,
                       j.job_type, j.job_level, j.job_xp
                FROM jobs j
                JOIN users u ON j.user_id = u.user_id
                WHERE TRUE {_NOT_BLOCKED}
                ORDER BY j.job_level DESC, j.job_xp DESC
                LIMIT 10
                """
            )
            text = "💼 <b>Active Job Leaders</b>\n"
            text += "━━━━━━━━━━━━━━━━\n\n"

            for i, row in enumerate(leaders, 1):
                name = row["first_name"] or row["username"] or "Unknown"
                emoji = get_rank_emoji(i)
                job_type = row["job_type"]
                job_emoji = job_emojis.get(job_type, "💼")
                text += f"{emoji} {html.escape(name)}: Lvl {row['job_level']} {job_emoji} {job_type.title()}\n"
        else:
            # Show specific job type skill leaderboard (active + inactive).
            leaders = await db.fetch(
                f"""
                WITH job_skill_rows AS (
                    SELECT
                        j.user_id,
                        j.job_level,
                        j.job_xp,
                        TRUE AS is_active
                    FROM jobs j
                    WHERE j.job_type = $1

                    UNION ALL

                    SELECT
                        js.user_id,
                        js.job_level,
                        js.job_xp,
                        FALSE AS is_active
                    FROM job_skills js
                    WHERE js.job_type = $1
                      AND NOT EXISTS (
                        SELECT 1
                        FROM jobs j
                        WHERE j.user_id = js.user_id
                          AND j.job_type = $1
                      )
                )
                SELECT
                    u.user_id,
                    u.first_name,
                    u.username,
                    s.job_level,
                    s.job_xp,
                    s.is_active
                FROM job_skill_rows s
                JOIN users u ON u.user_id = s.user_id
                WHERE TRUE {_NOT_BLOCKED}
                ORDER BY s.job_level DESC, s.job_xp DESC
                LIMIT 10
                """,
                job_filter,
            )

            job_name = job_filter.title()
            job_emoji = job_emojis.get(job_filter, "💼")
            text = f"{job_emoji} <b>Top {job_name} Skills</b>\n"
            text += "━━━━━━━━━━━━━━━━\n\n"

            for i, row in enumerate(leaders, 1):
                name = row["first_name"] or row["username"] or "Unknown"
                emoji = get_rank_emoji(i)

                def get_lvl_bold(row):
                    txt = f"Level {row['job_level']}"
                    return f"<u><b>{txt}</b></u>" if row["is_active"] else txt

                text += f"{emoji} {html.escape(name)}: {get_lvl_bold(row)}\n"

            if not leaders:
                text += "<i>No data yet!</i>\n"
            else:
                text += (
                    "\n<blockquote>"
                    "Note: <u><b>underlined bold level</b></u> means the player is currently active in this job."
                    "</blockquote>\n"
                )

        # Radio button-style keyboard
        def radio_btn(label: str, active: bool) -> str:
            return f"{'🔘' if active else '⚪'} {label}"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text=radio_btn("Active", job_filter == "active"),
                        callback_data=f"top:jobs:active:{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text=radio_btn("Police", job_filter == "police"),
                        callback_data=f"top:jobs:police:{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text=radio_btn("Gangster", job_filter == "gangster"),
                        callback_data=f"top:jobs:gangster:{owner_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=radio_btn("Thief", job_filter == "thief"),
                        callback_data=f"top:jobs:thief:{owner_id}",
                    ),
                    InlineKeyboardButton(
                        text=radio_btn("Doctor", job_filter == "doctor"),
                        callback_data=f"top:jobs:doctor:{owner_id}",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ],
            ]
        )

    elif category == "gangs":
        owner_id = (
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
        )
        leaders = await db.fetch(
            """
            SELECT g.id, g.name,
                   COUNT(gm.user_id) FILTER (
                       WHERE gm.user_id NOT IN (SELECT user_id FROM blocked_users)
                   ) AS member_count
            FROM gangs g
            LEFT JOIN gang_members gm ON g.id = gm.gang_id
            GROUP BY g.id, g.name
            ORDER BY member_count DESC, g.id ASC
            LIMIT 10
            """
        )

        text = "👥 <b>Top Gangs by Members</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"
        for i, row in enumerate(leaders, 1):
            emoji = get_rank_emoji(i)
            text += f"{emoji} {html.escape(row['name'])}: {row['member_count']} members\n"
        if not leaders:
            text += "<i>No gangs yet.</i>\n"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ]
            ]
        )

    elif category == "funeral":
        leaders = await db.fetch(
            f"""
            SELECT u.user_id, u.first_name, u.username,
                   COALESCE(SUM(f.amount), 0) AS total_amount,
                   COUNT(*)                   AS funeral_count
            FROM funerals_history f
            JOIN users u ON f.donor_user_id = u.user_id
            WHERE TRUE {_NOT_BLOCKED}
            GROUP BY u.user_id, u.first_name, u.username
            ORDER BY total_amount DESC, funeral_count DESC
            LIMIT 10
            """
        )

        text = "🪦 <b>Top Funeral Donors</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"

        for i, row in enumerate(leaders, 1):
            name = row["first_name"] or row["username"] or "Unknown"
            emoji = get_rank_emoji(i)
            text += (
                f"{emoji} {html.escape(name)}: "
                f"{format_price(int(row['total_amount']))} "
                f"across {row['funeral_count']} funeral"
                f"{'s' if row['funeral_count'] != 1 else ''}\n"
            )

        owner_id = (
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ]
            ]
        )

    elif category == "achievements":
        # Most achievements
        leaders = await db.fetch(
            f"""
            SELECT u.user_id, u.first_name, u.username,
                   COUNT(*) as achievement_count
            FROM achievements a
            JOIN users u ON a.user_id = u.user_id
            WHERE TRUE {_NOT_BLOCKED}
            GROUP BY u.user_id, u.first_name, u.username
            ORDER BY achievement_count DESC
            LIMIT 10
            """
        )

        text = "🏆 <b>Achievement Leaders</b>\n"
        text += "━━━━━━━━━━━━━━━━\n\n"

        for i, row in enumerate(leaders, 1):
            name = row["first_name"] or row["username"] or "Unknown"
            emoji = get_rank_emoji(i)
            text += f"{emoji} {html.escape(name)}: {row['achievement_count']} achievements\n"

        owner_id = (
            int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
        )
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=f"top:menu:{owner_id}"
                    )
                ]
            ]
        )

    else:
        text = "❌ Unknown category\n"
        keyboard = None

    if leaders is not None and not leaders:
        text += "<i>No data yet!</i>\n"

    if keyboard:
        try:
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
        except Exception:
            pass
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "top:menu"))
async def leaderboard_menu_callback(callback: CallbackQuery):
    """Go back to leaderboard menu."""
    user = callback.from_user

    # Extract owner_id from callback data
    parts = callback.data.split(":")
    owner_id = (
        int(parts[2]) if len(parts) >= 3 and parts[2].isdigit() else user.id
    )

    text = "🏆 <b>Leaderboards</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "Select a category to view:"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="💰 Richest", callback_data=f"top:balance:{owner_id}"
                ),
                InlineKeyboardButton(
                    text="📈 Earners", callback_data=f"top:earned:{owner_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🌻 Gardeners", callback_data=f"top:garden:{owner_id}"
                ),
                InlineKeyboardButton(
                    text="🏭 Factory", callback_data=f"top:factory:{owner_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🎣 Fishers", callback_data=f"top:fishing:{owner_id}"
                ),
                InlineKeyboardButton(
                    text="🧪 Crafters", callback_data=f"top:craft:{owner_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="💼 Jobs", callback_data=f"top:jobs:{owner_id}"
                ),
                InlineKeyboardButton(
                    text="🏆 Achievements",
                    callback_data=f"top:achievements:{owner_id}",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🪦 Funeral Donors",
                    callback_data=f"top:funeral:{owner_id}",
                ),
            ],
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


reg("achievements", "⭐ View achievements")


@client.on_message(filters.command(["achievements", "achieve"]))
async def achievements_command(
    message: Message,
):
    """View your achievements."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Get user's achievements
    user_achievements = await db.fetch(
        "SELECT * FROM achievements WHERE user_id = $1", user.id
    )

    achieved_keys = {a["achievement_key"] for a in user_achievements}

    text = f"🏆 <b>{user.first_name}'s Achievements</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    unlocked = 0
    total = len(ACHIEVEMENTS)

    # Use blockquote expandable for long lists
    text += "<blockquote expandable>"
    for ach_key, info in ACHIEVEMENTS.items():
        if ach_key in achieved_keys:
            text += f"✅ {info['emoji']} <b>{info['name']}</b>\n"
            text += f"   <i>{info['description']}</i>\n"
            unlocked += 1
        else:
            text += f"🔒 {info['emoji']} {info['name']}\n"
            text += f"   <i>{info['description']}</i>\n"
    text += "</blockquote>\n"

    text += f"\n📊 Progress: {unlocked}/{total}"

    await message.reply(text)


@client.on_message(filters.command(["backfill_achievements"]))
async def backfill_achievements_command(
    message: Message,
):
    """Backfill achievements for all users (admin only)."""
    from bot.achievements import backfill_achievements

    await message.reply("⏳ Backfilling achievements for all users...")

    count = await backfill_achievements(db)

    await message.reply(
        f"✅ Processed {count} users and unlocked achievements!"
    )


reg("dashboard", "📊 Your stats dashboard")


@client.on_message(filters.command(["dashboard", "stats"]))
async def dashboard_command(
    message: Message,
):
    """View your complete stats dashboard."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Get all stats
    wallet = await db.get_wallet(user.id)

    # Garden
    garden = await db.fetchrow(
        "SELECT * FROM gardens WHERE owner_id = $1", user.id
    )

    # Factory
    factories = await db.fetch(
        "SELECT * FROM factories WHERE owner_id = $1", user.id
    )

    # Fishing
    fishing = await db.get_fishing_stats(user.id)

    # Job
    job = await db.get_user_job(user.id)

    # Friends
    friends = await db.fetchval(
        """
        SELECT COUNT(*) FROM friendships
        WHERE user1_id = $1 OR user2_id = $1
        """,
        user.id,
    )

    # Achievements
    achievements = await db.fetchval(
        "SELECT COUNT(*) FROM achievements WHERE user_id = $1", user.id
    )

    text = f"📊 <b>{user.first_name}'s Dashboard</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    text += "<b>💰 Economy</b>\n"
    text += f"  Balance: {format_price(wallet['balance'])}\n"
    text += f"  Total Earned: {format_price(wallet['total_earned'])}\n\n"

    text += "<b>🌻 Garden</b>\n"
    if garden:
        text += f"  Size: {garden['size']}×{garden['size']}\n"
    else:
        text += "  <i>No garden</i>\n"
    text += "\n"

    text += "<b>🏭 Factory</b>\n"
    text += f"  Factories: {len(factories)}\n\n"

    text += "<b>🎣 Fishing</b>\n"
    text += f"  Fish Caught: {fishing.get('total_caught', fishing.get('caught', 0))}\n"
    text += f"  Bait: {fishing.get('bait', fishing.get('bait_count', 0))}\n\n"

    text += "<b>💼 Job</b>\n"
    if job:
        job_type = job.get("job_type", "Unknown")
        job_level = job.get("job_level", job.get("level", 1))
        text += f"  {job_type.title()} (Level {job_level})\n"
    else:
        text += "  <i>Unemployed</i>\n"
    text += "\n"

    text += "<b>👥 Social</b>\n"
    text += f"  Friends: {friends or 0}\n\n"

    text += "<b>🏆 Progress</b>\n"
    text += f"  Achievements: {achievements or 0}/{len(ACHIEVEMENTS)}"

    await message.reply(text)
