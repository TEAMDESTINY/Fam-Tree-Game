"""Jobs system with interactive mini-games."""

import random
import time
from datetime import datetime
from typing import Optional

from pyrogram.enums import ParseMode
from pyrogram.errors import BadRequest
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.command_registry import reg
from bot.constants import (
    ACTION_LIMIT_BASE,
    ACTION_SKILL_JOB,
    JOB_TYPES,
    action_limit_for_level,
    get_xp_for_level,
    level_for_action_limit,
    xp_for_next_level,
)
from bot.database import Database
from bot.queue_it import queue_it
from bot.plugins.work_games import (
    DoctorPuzzles,
    GangsterPuzzles,
    OPT as _OPT,
    PolicePuzzles,
    ThiefPuzzles,
)
from pyrogram import filters
from bot.client import client
from bot.database import db


# Universal work cooldown: 1 hour (3600 seconds) across ALL jobs
UNIVERSAL_WORK_COOLDOWN = 60  # minutes
POLICE_ARREST_REWARD = 60000
DOCTOR_HEAL_REWARD = 100000

# In-memory cache of question HTML keyed by message_id.
# Allows the result callback to append the verdict below the original question.
# Lost on restart — that's fine since game buttons expire with the cooldown.
_active_game_texts: dict[int, str] = {}


def format_price(amount: int) -> str:
    """Format price with $ and commas."""
    return f"${amount:,}"


def get_cooldown_remaining(
    last_work: Optional[datetime],
    cooldown_minutes: int = UNIVERSAL_WORK_COOLDOWN,
) -> int:
    """Get remaining cooldown in seconds, 0 if ready."""
    if not last_work:
        return 0

    # Handle timezone
    if last_work.tzinfo is not None:
        last_work = last_work.replace(tzinfo=None)

    elapsed = (datetime.now() - last_work).total_seconds()
    remaining = (cooldown_minutes * 60) - elapsed
    return max(0, int(remaining))


async def _get_user_all_job_progress(
    db: Database, user_id: int, current_job: dict | None = None
) -> dict[str, dict]:
    """Return per-job progress including inactive saved skills and active job."""
    rows = await db.fetch(
        """
        SELECT job_type, job_level, job_xp
        FROM job_skills
        WHERE user_id = $1
        """,
        user_id,
    )

    progress: dict[str, dict] = {
        row["job_type"]: {
            "job_level": row["job_level"],
            "job_xp": row["job_xp"],
            "is_active": False,
        }
        for row in rows
    }

    if current_job:
        active_type = current_job["job_type"]
        progress[active_type] = {
            "job_level": current_job.get("job_level", 1),
            "job_xp": current_job.get("job_xp", 0),
            "is_active": True,
        }

    return progress


reg("job", "💼 View or get a job")


@client.on_message(filters.command(["job", "jobs"]))
async def job_command(
    message: Message,
):
    """View or select a job."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Get current job
    current_job = await db.get_job(user.id)

    text = "💼 <b>Jobs System</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    if current_job:
        job_info = JOB_TYPES.get(current_job["job_type"], {})
        job_level = current_job.get("job_level", current_job.get("level", 1))
        job_xp = current_job.get("job_xp", current_job.get("xp", 0))
        min_reward = job_info.get("min_reward", 1_000)
        max_reward = job_info.get("max_reward", 5_000)
        # Calculate XP progress
        current_level_xp = get_xp_for_level(job_level)
        next_level_xp = get_xp_for_level(job_level + 1)
        xp_in_current_level = job_xp - current_level_xp
        xp_needed_for_next = next_level_xp - current_level_xp

        text += f"<b>Current Job:</b> {job_info.get('emoji', '💼')} {current_job['job_type'].title()}\n"
        text += f"📊 Level: {job_level}\n"
        text += f"⭐ XP: {xp_in_current_level}/{xp_needed_for_next}\n"
        text += (
            f"💰 Pay: {format_price(min_reward)} – {format_price(max_reward)}\n"
        )
        text += "\n"

        # Special command earnings
        job_type = current_job["job_type"]
        text += "<b>🎯 Special Command Earnings:</b>\n"
        if job_type == "police":
            text += (
                f"• /arrest - {format_price(POLICE_ARREST_REWARD)} per arrest\n"
            )
        elif job_type == "thief":
            text += "• /heist - High risk, high reward (scales with level)\n"
            text += "• /rob - Steal 5%-15% of target's wallet\n"
        elif job_type == "gangster":
            text += "• /kill - Attack players (success based on level + luck)\n"
        elif job_type == "doctor":
            text += f"• /heal - {format_price(DOCTOR_HEAL_REWARD)} per heal\n"
        text += "\n"

        # Show work cooldown
        cooldown = get_cooldown_remaining(await db.get_work_cooldown(user.id))

        if cooldown > 0:
            mins = cooldown // 60
            secs = cooldown % 60
            text += f"⏰ Work available in: {mins}m {secs}s\n\n"
        else:
            text += "✅ Ready to work! Use /work\n\n"

        text += "Use /work to do your job!\n"
        text += "Use the Quit Job button to change careers.\n"
        text += "<i>💡 Tip: Use /guide_jobs for detailed job info</i>"
    else:
        text += "You don't have a job!\n\n"
        text += "<b>Available Jobs:</b>\n"

        for job_name, info in JOB_TYPES.items():
            emoji = info.get("emoji", "💼")
            lo = format_price(info.get("min_reward", 1_000))
            hi = format_price(info.get("max_reward", 5_000))
            text += f"\n{emoji} <b>{job_name.title()}</b>\n"
            text += f"   💰 {lo} – {hi} per work\n"
            text += f"   ⏱️ {UNIVERSAL_WORK_COOLDOWN}m cooldown\n"

        text += "\nTap a button to apply!\n"
        text += "<i>💡 Use /guide_jobs for detailed info</i>"

    # Build keyboard
    if current_job:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💼 Work", callback_data="job:work"
                    ),
                    InlineKeyboardButton(
                        text="📊 Stats", callback_data="job:stats"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Change Job", callback_data="job:change"
                    ),
                    InlineKeyboardButton(
                        text="🚪 Quit Job", callback_data="job:quit"
                    ),
                ],
            ]
        )
    else:
        buttons = []
        for job_name, info in JOB_TYPES.items():
            buttons.append([
                InlineKeyboardButton(
                    text=f"{info.get('emoji', '💼')} {job_name.title()}",
                    callback_data=f"job:apply:{job_name}",
                )
            ])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    await message.reply(text, reply_markup=keyboard)


@client.on_callback_query(filters.regex(r"^" + "job:apply:"))
async def apply_job_callback(
    callback: CallbackQuery,
):
    """Apply for a job."""
    user = callback.from_user

    parts = callback.data.split(":")
    job_type = parts[2]

    if job_type not in JOB_TYPES:
        await callback.answer("Invalid job!", show_alert=True)
        return

    # Check if already has a job
    current = await db.get_job(user.id)
    if current:
        await callback.answer("Quit your current job first!", show_alert=True)
        return

    # Create job
    await db.join_job(user.id, job_type)

    info = JOB_TYPES.get(job_type, {})
    emoji = info.get("emoji", "💼")
    lo = format_price(info.get("min_reward", 1_000))
    hi = format_price(info.get("max_reward", 5_000))
    cd_minutes = UNIVERSAL_WORK_COOLDOWN

    text = "🎉 <b>Congratulations!</b>\n\n"
    text += f"You are now a {emoji} <b>{job_type.title()}</b>!\n\n"
    text += f"💰 Pay: {lo} – {hi} per work\n"
    text += f"⏱️ Cooldown: {cd_minutes} minutes\n\n"
    text += "Use /work to start earning!"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💼 Work Now", callback_data="job:work")]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "job:change" + r"$"))
async def change_job_callback(
    callback: CallbackQuery,
):
    """Show job selection to change job."""
    user = callback.from_user

    current = await db.get_job(user.id)
    if not current:
        await callback.answer("You don't have a job!", show_alert=True)
        return

    text = "🔄 <b>Change Job</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += f"Current: {current['job_type'].title()}\n"
    text += "<i>Quitting will reset all job progress!</i>\n\n"
    text += "Select a new job:\n"

    buttons = []
    for job_name, info in JOB_TYPES.items():
        if job_name != current["job_type"]:
            buttons.append([
                InlineKeyboardButton(
                    text=f"{info.get('emoji', '💼')} {job_name.title()}",
                    callback_data=f"job:switch:{job_name}",
                )
            ])

    buttons.append([
        InlineKeyboardButton(text="« Cancel", callback_data="job:back")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "job:switch:"))
async def switch_job_callback(
    callback: CallbackQuery,
):
    """Switch to a new job directly."""
    user = callback.from_user

    parts = callback.data.split(":")
    new_job = parts[2]

    if new_job not in JOB_TYPES:
        await callback.answer("Invalid job!", show_alert=True)
        return

    # Quit current job and apply new one
    await db.quit_job(user.id)
    await db.join_job(user.id, new_job)

    # Show the /job main page with new job
    await _show_job_view(callback, db)
    await callback.answer()


async def _show_job_view(callback: CallbackQuery, db: Database):
    """Show the /job main page (used by job command and after job change)."""
    user = callback.from_user
    current_job = await db.get_job(user.id)

    text = "💼 <b>Jobs System</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    if current_job:
        job_info = JOB_TYPES.get(current_job["job_type"], {})
        job_level = current_job.get("job_level", current_job.get("level", 1))
        job_xp = current_job.get("job_xp", current_job.get("xp", 0))
        min_reward = job_info.get("min_reward", 1_000)
        max_reward = job_info.get("max_reward", 5_000)
        # Calculate XP progress
        current_level_xp = get_xp_for_level(job_level)
        next_level_xp = get_xp_for_level(job_level + 1)
        xp_in_current_level = job_xp - current_level_xp
        xp_needed_for_next = next_level_xp - current_level_xp

        text += f"<b>Current Job:</b> {job_info.get('emoji', '💼')} {current_job['job_type'].title()}\n"
        text += f"📊 Level: {job_level}\n"
        text += f"⭐ XP: {xp_in_current_level}/{xp_needed_for_next}\n"
        text += (
            f"💰 Pay: {format_price(min_reward)} – {format_price(max_reward)}\n"
        )
        text += "\n"

        job_type = current_job["job_type"]
        text += "<b>🎯 Special Command Earnings:</b>\n"
        if job_type == "police":
            text += (
                f"• /arrest - {format_price(POLICE_ARREST_REWARD)} per arrest\n"
            )
        elif job_type == "thief":
            text += "• /heist - High risk, high reward (scales with level)\n"
            text += "• /rob - Steal 5%-15% of target's wallet\n"
        elif job_type == "gangster":
            text += "• /kill - Attack players (success based on level + luck)\n"
        elif job_type == "doctor":
            text += f"• /heal - {format_price(DOCTOR_HEAL_REWARD)} per heal\n"

        text += "\n"

        cooldown = get_cooldown_remaining(await db.get_work_cooldown(user.id))

        if cooldown > 0:
            mins = cooldown // 60
            secs = cooldown % 60
            text += f"⏰ Work available in: {mins}m {secs}s\n\n"
        else:
            text += "✅ Ready to work! Use /work\n\n"

        text += "Use /work to do your job!\n"
        text += "Use the Quit Job button to change careers.\n"
        text += "<i>💡 Tip: Use /guide_jobs for detailed job info</i>"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="💼 Work", callback_data="job:work"
                    ),
                    InlineKeyboardButton(
                        text="📊 Stats", callback_data="job:stats"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Change Job", callback_data="job:change"
                    ),
                    InlineKeyboardButton(
                        text="🚪 Quit Job", callback_data="job:quit"
                    ),
                ],
            ]
        )
    else:
        text += "You don't have a job!\n\n"
        text += "<b>Available Jobs:</b>\n"

        for job_name, info in JOB_TYPES.items():
            emoji = info.get("emoji", "💼")
            lo = format_price(info.get("min_reward", 1_000))
            hi = format_price(info.get("max_reward", 5_000))
            text += f"\n{emoji} <b>{job_name.title()}</b>\n"
            text += f"   💰 {lo} – {hi} per work\n"
            text += f"   ⏱️ {UNIVERSAL_WORK_COOLDOWN}m cooldown\n"

        text += "\nTap a button to apply!\n"
        text += "<i>💡 Use /guide_jobs for detailed info</i>"

        buttons = []
        for job_name, info in JOB_TYPES.items():
            buttons.append([
                InlineKeyboardButton(
                    text=f"{info.get('emoji', '💼')} {job_name.title()}",
                    callback_data=f"job:apply:{job_name}",
                )
            ])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    try:
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=keyboard),
            callback.message.chat,
        )
    except BadRequest:
        pass


@client.on_callback_query(filters.regex(r"^" + "job:quit" + r"$"))
async def quit_job_callback(
    callback: CallbackQuery,
):
    """Quit current job."""
    user = callback.from_user

    current = await db.get_job(user.id)
    if not current:
        await callback.answer("You don't have a job!", show_alert=True)
        return

    # Confirm quit
    text = "🚪 <b>Quit Job?</b>\n\n"
    text += f"Are you sure you want to quit as {current['job_type'].title()}?\n"
    text += "You will lose all job progress!"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Yes, Quit", callback_data="job:confirm_quit"
                ),
                InlineKeyboardButton(
                    text="❌ Cancel", callback_data="job:cancel_quit"
                ),
            ]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "job:confirm_quit" + r"$"))
async def confirm_quit_callback(
    callback: CallbackQuery,
):
    """Confirm quitting job."""
    user = callback.from_user

    await db.quit_job(user.id)

    text = "👋 You quit your job.\n\n"
    text += "Use /job to find a new one!"

    await queue_it(
        lambda: callback.message.edit_text(text), callback.message.chat
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "job:cancel_quit" + r"$"))
async def cancel_quit_callback(
    callback: CallbackQuery,
):
    """Cancel quitting."""
    user = callback.from_user

    # Just refresh job view
    current = await db.get_job(user.id)
    if not current:
        await queue_it(
            lambda: callback.message.edit_text(
                "You don't have a job! Use /job"
            ),
            callback.message.chat,
        )
        await callback.answer()
        return

    info = JOB_TYPES.get(current["job_type"], {})

    text = "💼 <b>Your Job</b>\n\n"
    text += f"{info.get('emoji', '💼')} <b>{current['job_type'].title()}</b>\n"
    job_level = current.get("job_level", current.get("level", 1))
    job_xp = current.get("job_xp", current.get("xp", 0))

    # Calculate XP progress
    current_level_xp = get_xp_for_level(job_level)
    next_level_xp = get_xp_for_level(job_level + 1)
    xp_in_current_level = job_xp - current_level_xp
    xp_needed_for_next = next_level_xp - current_level_xp

    text += f"📊 Level: {job_level}\n"
    text += f"⭐ XP: {xp_in_current_level}/{xp_needed_for_next}\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(text="💼 Work", callback_data="job:work"),
                InlineKeyboardButton(
                    text="📊 Stats", callback_data="job:stats"
                ),
            ]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


reg("work", "🛠️ Do your job")


@client.on_message(filters.command(["work"]))
async def work_command(
    message: Message,
):
    """Do your job for money."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Get job
    job = await db.get_job(user.id)
    if not job:
        await message.reply("❌ You don't have a job!\nUse /job to get one.")
        return

    info = JOB_TYPES.get(job["job_type"], {})
    job_type = job["job_type"]

    # Check cooldown first (applies across all jobs)
    cooldown = get_cooldown_remaining(await db.get_work_cooldown(user.id))

    if cooldown > 0:
        mins = cooldown // 60
        secs = cooldown % 60

        # Show job-specific commands while on cooldown
        text = f"⏰ Work available in: {mins}m {secs}s\n\n"
        text += "<b>💡 Job-specific commands:</b>\n"

        if job_type == "police":
            text += "• /arrest - Arrest criminals (earn rewards)\n"
        elif job_type == "thief":
            text += "• /rob - Rob other players' wallets\n"
            text += "• /heist - Do house heists\n"
        elif job_type == "gangster":
            text += "• /kill - Attack other players\n"
            text += "• /gangwar - Challenge other players\n"
        elif job_type == "doctor":
            text += "• /heal - Heal injured/dead players\n"

        await message.reply(text)
        return

    # Start work mini-game based on job type
    if job_type == "police":
        text, keyboard = await create_police_game(db, user.id)
    elif job_type == "thief":
        text, keyboard = await create_thief_game(db, user.id)
    elif job_type == "gangster":
        text, keyboard = await create_gangster_game(db, user.id)
    elif job_type == "doctor":
        text, keyboard = await create_doctor_game(db, user.id)
    else:
        # Generic work (completes immediately, sets its own cooldown)
        text, keyboard = await do_generic_work(db, user.id, job, info)

    # Lock cooldown at game-generation time to prevent double-game
    if job_type in ("police", "thief", "gangster", "doctor"):
        await db.set_work_cooldown(user.id)

    sent = await message.reply(text, reply_markup=keyboard)
    if sent:
        _active_game_texts[sent.id] = text


@client.on_callback_query(filters.regex(r"^" + "job:work" + r"$"))
async def work_callback(
    callback: CallbackQuery,
):
    """Work via callback."""
    user = callback.from_user

    job = await db.get_job(user.id)
    if not job:
        await callback.answer("You don't have a job!", show_alert=True)
        return

    info = JOB_TYPES.get(job["job_type"], {})

    cooldown = get_cooldown_remaining(await db.get_work_cooldown(user.id))
    if cooldown > 0:
        mins = cooldown // 60
        secs = cooldown % 60
        await callback.answer(f"Rest needed: {mins}m {secs}s", show_alert=True)
        return

    job_type = job["job_type"]

    if job_type == "police":
        text, keyboard = await create_police_game(db, user.id)
    elif job_type == "thief":
        text, keyboard = await create_thief_game(db, user.id)
    elif job_type == "gangster":
        text, keyboard = await create_gangster_game(db, user.id)
    elif job_type == "doctor":
        text, keyboard = await create_doctor_game(db, user.id)
    else:
        text, keyboard = await do_generic_work(db, user.id, job, info)

    if job_type in ("police", "thief", "gangster", "doctor"):
        await db.set_work_cooldown(user.id)

    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    _active_game_texts[callback.message.id] = text
    await callback.answer()


def _calc_work_xp(job_level: int, success: bool) -> int:
    """Scale work XP with level: base floor at lv1, percentage of next-level XP beyond that."""
    next_xp = xp_for_next_level(job_level)
    if success:
        base = random.randint(20, 35)
        scaled = int(next_xp * random.uniform(0.04, 0.07))
    else:
        base = random.randint(4, 9)
        scaled = int(next_xp * random.uniform(0.01, 0.025))
    return max(base, scaled)


async def do_generic_work(
    db: Database, user_id: int, job: dict, info: dict
) -> tuple[str, InlineKeyboardMarkup]:
    """Generic work without mini-game."""
    min_reward = info.get("min_reward", 1_000)
    max_reward = info.get("max_reward", 5_000)
    reward = random.randint(min_reward, max_reward)

    xp = _calc_work_xp(job["job_level"], success=True)
    await db.add_balance(user_id, reward, f"Work: {job['job_type']}")
    await db.update_job_xp(user_id, xp)
    await db.set_work_cooldown(user_id)

    # Check for level up
    updated_job = await db.get_job(user_id)
    level_up = updated_job["job_level"] > job["job_level"]

    text = "💼 <b>Work Complete!</b>\n\n"
    text += (
        f"You worked as a {info.get('emoji', '💼')} {job['job_type'].title()}\n"
    )
    text += f"💰 Earned: {format_price(reward)}\n"

    if level_up:
        text += f"\n🎉 <b>LEVEL UP!</b> Now level {updated_job['job_level']}!"

    # Calculate XP progress
    current_level_xp = get_xp_for_level(updated_job["job_level"])
    next_level_xp = get_xp_for_level(updated_job["job_level"] + 1)
    xp_in_current_level = updated_job["job_xp"] - current_level_xp
    xp_needed_for_next = next_level_xp - current_level_xp

    text += f"\n⭐ XP: {xp_in_current_level}/{xp_needed_for_next}"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="« Back to Job", callback_data="job:back"
                )
            ]
        ]
    )

    return text, keyboard


async def create_police_game(
    db: Database, user_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    return PolicePuzzles.generate(user_id, UNIVERSAL_WORK_COOLDOWN * 60)


async def create_thief_game(
    db: Database, user_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    return ThiefPuzzles.generate(user_id, UNIVERSAL_WORK_COOLDOWN * 60)


async def create_gangster_game(
    db: Database, user_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    return GangsterPuzzles.generate(user_id, UNIVERSAL_WORK_COOLDOWN * 60)


async def create_doctor_game(
    db: Database, user_id: int
) -> tuple[str, InlineKeyboardMarkup]:
    return DoctorPuzzles.generate(user_id, UNIVERSAL_WORK_COOLDOWN * 60)


@client.on_callback_query(filters.regex(r"^" + "work:"))
async def work_result_callback(
    callback: CallbackQuery,
):
    """Handle work mini-game result."""
    user = callback.from_user

    # Callback formats (all uniform — last two parts are correct_idx, choice_idx):
    #   work:{type}:{uid}:{expires_at}:{correct_idx}:{choice_idx}
    #   work:doctor:{uid}:{expires_at}:{game_code}:{correct_idx}:{choice_idx}
    parts = callback.data.split(":")
    job_type = parts[1]
    owner_id = int(parts[2])
    expires_at = int(parts[3])

    if callback.from_user.id != owner_id:
        await callback.answer("⛔ This isn't your work shift!", show_alert=True)
        return

    now = int(time.time())
    timed_out = now > expires_at
    time_attack_loss = False

    # Compute result — uniform: parts[-2] = correct_idx, parts[-1] = choice_idx
    if job_type in ("doctor", "police", "thief", "gangster") and len(parts) < 7:
        await callback.answer(
            "⏰ Outdated game format. Start a new shift!", show_alert=True
        )
        return

    if timed_out:
        is_time_attack = len(parts) >= 7 and (
            (
                job_type == "doctor"
                and int(parts[4]) == DoctorPuzzles.GC_TIME_ATTACK
            )
            or (
                job_type == "police"
                and int(parts[4]) == PolicePuzzles.GC_TIMEBOMB
            )
            or (
                job_type == "gangster"
                and int(parts[4]) == GangsterPuzzles.GC_VAULT
            )
            or (job_type == "thief" and int(parts[4]) == ThiefPuzzles.GC_LASER)
        )
        if is_time_attack:
            result = 0
            time_attack_loss = True
        else:
            await callback.answer(
                "⏰ This shift expired. Start a new one!", show_alert=True
            )
            return

    choice_idx = int(parts[-1])
    correct_idx = int(parts[-2])
    if not timed_out:
        result = 1 if choice_idx == correct_idx else 0

    choice_letter = _OPT[choice_idx] if choice_idx < len(_OPT) else "?"
    correct_letter = _OPT[correct_idx] if correct_idx < len(_OPT) else "?"

    job = await db.get_job(user.id)
    if not job:
        await callback.answer("No job found!", show_alert=True)
        return

    info = JOB_TYPES.get(job_type, JOB_TYPES.get(job["job_type"], {}))
    min_reward = info.get("min_reward", 1_000)
    max_reward = info.get("max_reward", 5_000)

    if result == 0:
        reward = min_reward // 4
        xp = _calc_work_xp(job["job_level"], success=False)
    else:
        reward = random.randint(min_reward, max_reward)
        xp = _calc_work_xp(job["job_level"], success=True)

    await db.add_balance(user.id, reward, f"Work: {job_type}")
    await db.update_job_xp(user.id, xp)

    updated_job = await db.get_job(user.id)
    level_up = updated_job["job_level"] > job["job_level"]

    # Build the result line appended below the original question
    if time_attack_loss:
        verdict = f"⏰ <b>Too slow!</b> — Correct was <b>{correct_letter}</b>"
    elif result >= 1:
        verdict = f"✅ <b>Correct!</b> — You chose <b>{choice_letter}</b>"
    else:
        verdict = f"❌ <b>Wrong</b> — You chose <b>{choice_letter}</b>, correct was <b>{correct_letter}</b>"

    suffix = f"\n\n{verdict}\n💰 Earned: {format_price(reward)} · ⭐ +{xp} XP"
    if level_up:
        suffix += f"\n🎉 <b>LEVEL UP!</b> Now level {updated_job['job_level']}!"

    # Get the original question HTML and append result to it
    msg_id = callback.message.id
    question_html = _active_game_texts.pop(msg_id, None)
    if question_html is None:
        # Fallback: try to get HTML from the message entities
        try:
            question_html = callback.message.text.html
        except Exception:
            question_html = str(callback.message.text or "")

    back_keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="« Back to Job", callback_data="job:back"
                )
            ]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(
            question_html + suffix,
            reply_markup=back_keyboard,
            parse_mode=ParseMode.HTML,
        ),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "job:back" + r"$"))
async def job_back_callback(
    callback: CallbackQuery,
):
    """Go back to job view."""
    await _show_job_view(callback, db)
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "job:stats" + r"$"))
async def job_stats_callback(
    callback: CallbackQuery,
):
    """Show detailed job stats."""
    user = callback.from_user

    job = await db.get_job(user.id)

    text = "📊 <b>Job Statistics</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    all_progress = await _get_user_all_job_progress(db, user.id, job)

    for job_name, info in JOB_TYPES.items():
        progress = all_progress.get(
            job_name, {"job_level": 1, "job_xp": 0, "is_active": False}
        )
        job_level = progress["job_level"]
        job_xp = progress["job_xp"]
        is_active = progress["is_active"]

        current_level_xp = get_xp_for_level(job_level)
        next_level_xp = get_xp_for_level(job_level + 1)
        xp_in_current_level = job_xp - current_level_xp
        xp_needed_for_next = next_level_xp - current_level_xp

        text += (
            f"{info.get('emoji', '💼')} <b>{job_name.title()}</b>"
            f"{' ✅ Active' if is_active else ''}\n"
        )
        text += f"• Level: {job_level}\n"
        text += f"• XP: {xp_in_current_level}/{xp_needed_for_next}\n"
        lo = format_price(info.get("min_reward", 1_000))
        hi = format_price(info.get("max_reward", 5_000))
        text += f"• Pay: {lo} – {hi} per work\n"
        text += f"• Work Cooldown: {UNIVERSAL_WORK_COOLDOWN} minutes\n"

        if job_name == "police":
            text += (
                f"• /arrest: {format_price(POLICE_ARREST_REWARD)} per arrest\n"
            )
        elif job_name == "doctor":
            text += f"• /heal: {format_price(DOCTOR_HEAL_REWARD)} per heal\n"
        elif job_name == "thief":
            text += "• /rob and /heist: better results while active thief\n"
        elif job_name == "gangster":
            text += (
                "• /kill and /gangwar: better results while active gangster\n"
                "• /gangwar daily limit increases with level (max 10)\n"
            )
        text += "\n"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="« Back", callback_data="job:back")]
        ]
    )

    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


reg("guide_jobs", "📖 Jobs guide")


@client.on_message(filters.command(["guide_jobs"]))
async def guide_jobs_command(message: Message):
    """Show detailed jobs guide."""
    text = "📖 <b>Jobs Guide</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    text += "<blockquote expandable>"
    text += "<b>🎯 How Jobs Work</b>\n"
    text += "• Use /job to get or switch jobs\n"
    text += "• Use /work to earn money and XP (cooldown: 60 minutes)\n"
    text += "• /job ➜ Stats shows your progress in all jobs\n\n"

    text += "<b>💼 Available Jobs</b>\n\n"
    text += "👮 <b>Police</b>\n"
    text += "• Pay: $20,000 – $30,000 per work\n"
    text += "• /arrest reward: $60,000 per successful arrest\n"
    text += "• Counter-arrest reward: $20,000 when stopping failed /kill or /rob\n\n"

    text += "🦹 <b>Thief</b>\n"
    text += "• Pay: $30,000 – $55,000 per work\n"
    text += "• /rob and /heist are stronger when active thief\n\n"

    text += "🔫 <b>Gangster</b>\n"
    text += "• Pay: $25,000 – $35,000 per work\n"
    text += "• /kill and /gangwar are stronger when active gangster\n"
    text += "• /gangwar daily limit: 2 + (level - 1) × 0.5, capped at 10\n"
    text += "• Gangster fight-back on /kill works only when target is active gangster\n\n"

    text += "👨‍⚕️ <b>Doctor</b>\n"
    text += "• Pay: $60,000 – $80,000 per work\n"
    text += "• /heal reward: $100,000 per successful heal\n\n"

    text += "<b>🧠 Command Rules</b>\n"
    text += "• /kill and /rob can be used by anyone\n"
    text += "• Active matching job gives better performance (thief for /rob, gangster for /kill)\n"
    text += "• /arrest requires active police\n"
    text += "• /heal requires active doctor\n"
    text += "</blockquote>"

    await message.reply(text)


reg("xp_for", "📈 XP needed to reach a job level")


@client.on_message(filters.command(["xp_for"]))
async def xp_for_command(message: Message):
    """Usage: /xp_for <job> <level>

    Anyone can ask, e.g. /xp_for thief 10 → total XP to reach Lv.10.
    All jobs share the same level curve today (y = 2.095x² + 53.37x + 187
    XP between consecutive levels), but we still take the job arg so the
    answer says which job you asked about.
    """
    parts = (message.text or "").split()
    if len(parts) < 3:
        await message.reply(
            "Usage: <code>/xp_for &lt;job&gt; &lt;level&gt;</code>\n"
            f"Jobs: {', '.join(JOB_TYPES.keys())}\n"
            "Example: <code>/xp_for thief 10</code>"
        )
        return
    job_name = parts[1].lower()
    if job_name not in JOB_TYPES:
        await message.reply(
            f"❌ Unknown job '{parts[1]}'. Choose one of: "
            f"{', '.join(JOB_TYPES.keys())}"
        )
        return
    try:
        level = int(parts[2])
    except ValueError:
        await message.reply("❌ Level must be an integer.")
        return
    if level < 1:
        await message.reply("❌ Level must be ≥ 1.")
        return

    cumulative = get_xp_for_level(level)
    to_next = xp_for_next_level(level)
    info = JOB_TYPES[job_name]
    await message.reply(
        f"📈 <b>{info['emoji']} {job_name.title()} — XP table</b>\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"Total XP to reach <b>Lv.{level}</b>: <b>{cumulative:,}</b> XP\n"
        f"XP from Lv.{level} → Lv.{level + 1}: <b>{to_next:,}</b> XP\n\n"
        f"<i>Formula: ΔXP(x) = 2.095·x² + 53.37·x + 187</i>"
    )


reg("lvl_for", "🎯 Level needed to reach an action limit")


@client.on_message(filters.command(["lvl_for"]))
async def lvl_for_command(message: Message):
    """Usage: /lvl_for <action> <limit>

    Tells you which job-skill level reaches a given daily limit for the
    given action. E.g. /lvl_for arrest 7 → 25 (police level).
    """
    parts = (message.text or "").split()
    if len(parts) < 3:
        actions = ", ".join(ACTION_LIMIT_BASE.keys())
        await message.reply(
            "Usage: <code>/lvl_for &lt;action&gt; &lt;limit&gt;</code>\n"
            f"Actions: {actions}\n"
            "Example: <code>/lvl_for arrest 7</code>"
        )
        return
    action = parts[1].lower()
    if action not in ACTION_LIMIT_BASE:
        await message.reply(
            f"❌ Unknown action '{parts[1]}'. Choose one of: "
            f"{', '.join(ACTION_LIMIT_BASE.keys())}"
        )
        return
    try:
        limit = int(parts[2])
    except ValueError:
        await message.reply("❌ Limit must be an integer.")
        return
    base = ACTION_LIMIT_BASE[action]
    if limit <= base:
        await message.reply(
            f"ℹ️ /{action}'s minimum per-day count is "
            f"{action_limit_for_level(action, 1)} at Lv.1 already."
        )
        return
    required_level = level_for_action_limit(action, limit)
    skill_job = ACTION_SKILL_JOB.get(action, "—")
    await message.reply(
        f"🎯 <b>/{action}</b> can be used <b>{limit}×</b>/day "
        f"starting at <b>{skill_job} Lv.{required_level}</b>.\n\n"
        f"<i>Formula: limit = {base} + floor(√level)</i>"
    )
