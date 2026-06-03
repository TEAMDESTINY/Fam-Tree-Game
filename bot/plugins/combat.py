"""Combat and crime system - /kill, /rob, /arrest, /heal, /heist commands."""

import random
import re

from pyrogram import Client as Bot
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.command_registry import reg
from bot.constants import action_limit_for_level, level_for_action_limit
from bot.database import Database
from bot.plugins.callbacks import safe_callback_answer
from bot.queue_it import queue_it
from pyrogram import filters
from bot.client import client
from bot.database import db


# Constants
SAME_TARGET_LIMIT = 2  # Can only kill/rob same person twice per day
REVIVE_COST = 5000
REVIVE_FULL_COST = 15000
ROB_MIN_PERCENT = 20  # Minimum % of wallet to steal
ROB_MAX_PERCENT = 40  # Maximum % of wallet to steal
JAIL_DURATION_HOURS = 24  # Jail duration in hours
ARREST_MAX_MINUTES = 4 * 60
POLICE_ARREST_REWARD = 60000
POLICE_COUNTER_ARREST_REWARD = 20000
DOCTOR_HEAL_REWARD = 100000


def get_target_from_message(message: Message):
    """Extract target user from reply or mention."""
    if message.reply_to_message and message.reply_to_message.from_user:
        return message.reply_to_message.from_user

    # Check for mentions in text
    if message.entities:
        for entity in message.entities:
            if entity.type == "mention" and entity.user:
                return entity.user
            if entity.type == "text_mention" and entity.user:
                return entity.user

    return None


async def get_target_from_command(bot: Bot, message: Message, db: Database):
    """Extract target user from reply, mention, @username, or user_id."""
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


async def _resolve_user(bot: Bot, db: Database, arg: str):
    """Resolve a user from username or user_id string."""
    # Try as user_id first
    if arg.isdigit():
        user_id = int(arg)
        user = await db.get_user(user_id)
        if user:
            # Create a minimal User-like object
            from pyrogram.types import User

            return User(
                id=user["user_id"],
                is_bot=False,
                first_name=user["first_name"],
                username=user.get("username"),
            )
        return None

    # Try as username
    try:
        user_obj = await bot.get_users(arg)
        if not user_obj.is_bot:
            return user_obj
    except Exception:
        pass

    # Try database lookup by username
    user = await db.fetchrow("SELECT * FROM users WHERE username = $1", arg)
    if user:
        from pyrogram.types import User

        return User(
            id=user["user_id"],
            is_bot=False,
            first_name=user["first_name"],
            username=user.get("username"),
        )

    return None


def calculate_success_chance(
    attacker_level: int, defender_level: int, base_chance: float
) -> float:
    """Calculate success chance based on job levels."""
    level_diff = attacker_level - defender_level
    # Each level difference gives ±5% chance
    modifier = level_diff * 0.05
    return max(0.1, min(0.9, base_chance + modifier))


def _parse_duration_minutes(text: str | None) -> int | None:
    """Parse flexible durations like '2h 13m', '2h13m', '13m', '2h'."""
    if not text:
        return None

    compact = text.lower().replace(" ", "")
    match = re.fullmatch(r"(?:(\d+)h)?(?:(\d+)m)?", compact)
    if not match:
        return None

    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    total = (hours * 60) + minutes
    if total <= 0:
        return None
    return min(total, ARREST_MAX_MINUTES)


def _format_minutes(total_minutes: int) -> str:
    """Format minutes into human text."""
    if total_minutes < 60:
        return f"{total_minutes} minutes"
    hours = total_minutes // 60
    minutes = total_minutes % 60
    if minutes == 0:
        return f"{hours} hour" if hours == 1 else f"{hours} hours"
    return f"{hours}h {minutes}m"


def _format_remaining_seconds(total_seconds: int) -> str:
    """Format remaining seconds as `Xh Ym`."""
    remaining = max(0, int(total_seconds))
    hours = remaining // 3600
    minutes = (remaining % 3600) // 60
    return f"{hours}h {minutes}m"


reg("kill", "🔪 Attack a player")


@client.on_message(filters.command(["kill"]))
async def kill_command(message: Message, bot: Bot):
    """Attempt to kill another player. Removes one heart on success."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if user is dead
    if await db.is_user_dead(user.id):
        await message.reply(
            "💀 You're dead! Use /revive_me to come back to life."
        )
        return

    # Check if user is jailed
    can_use, jail_reason = await db.can_use_crime_commands(user.id)
    if not can_use:
        await message.reply(f"❌ {jail_reason}")
        return

    target = await get_target_from_command(bot, message, db)
    if not target:
        await message.reply(
            "🔪 <b>Kill Command</b>\n\n"
            "Reply to a user's message, mention them, or use @username/ID to attempt a kill.\n\n"
            "• Success removes 1 heart from target\n"
            "• Target dies at 0 hearts\n"
            "• Daily limit: 5 kills\n"
            "• Same target: 2 times/day max\n\n"
            "Usage: <code>/kill</code> (reply/mention/@username)"
        )
        return

    if target.id == user.id:
        await message.reply("❌ You can't kill yourself!")
        return

    if target.is_bot:
        await message.reply("❌ Can't kill bots!")
        return

    # Ensure target exists
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Check if target is in passive mode
    if await db.is_in_passive_mode(target.id):
        await message.reply(
            f"🛡️ {target.first_name} is in passive mode!\n\n"
            f"They haven't done any crime activity for 5+ days.\n"
            f"You can't attack users in passive mode.\n\n"
            f"<i>Read /guide_passivemode for more info.</i>"
        )
        return

    # Daily kill limit scales with gangster skill level.
    attacker_job_pre = await db.get_job(user.id)
    gangster_level_pre = (
        attacker_job_pre.get("job_level", 1)
        if attacker_job_pre and attacker_job_pre["job_type"] == "gangster"
        else 1
    )
    kill_limit = action_limit_for_level("kill", gangster_level_pre)
    total_kills = await db.get_daily_action_count(user.id, "kill")
    if total_kills >= kill_limit:
        next_level = level_for_action_limit("kill", kill_limit + 1)
        await message.reply(
            f"❌ You've used all {kill_limit} kill attempts today!\n"
            f"💡 Reach gangster Lv.{next_level} to unlock one more attempt."
        )
        return

    # Check if target is already dead
    target_hearts = await db.get_user_hearts(target.id)
    if target_hearts <= 0:
        await message.reply(f"❌ {target.first_name} is already dead!")
        return

    # Get job levels for success calculation
    attacker_job = await db.get_job(user.id)
    defender_job = await db.get_job(target.id)

    attacker_level = 1
    defender_level = 1
    defender_job_type = None

    if attacker_job:
        attacker_level = attacker_job.get("job_level", 1)

    if defender_job and defender_job["job_type"] in ("police", "gangster"):
        defender_level = defender_job.get("job_level", 1)
        defender_job_type = defender_job["job_type"]

    # Base 50% success rate
    success_chance = calculate_success_chance(
        attacker_level, defender_level, 0.5
    )
    success = random.random() < success_chance

    # Record the attempt
    await db.increment_daily_action(user.id, "kill", target.id)

    if success:
        new_hearts = await db.remove_heart(target.id)
        await db.log_crime(user.id, "kill", target.id, True)

        if new_hearts <= 0:
            await message.reply(
                f"💀 <b>KILL!</b>\n\n"
                f"You killed {target.first_name}!\n"
                f"They now have 0 hearts and are dead.\n\n"
                f"<i>They need to /revive_me or get healed by a doctor.</i>"
            )
        else:
            await message.reply(
                f"🔪 <b>Hit!</b>\n\n"
                f"You injured {target.first_name}!\n"
                f"They now have {new_hearts} ❤️ remaining."
            )

        # Notify target
        try:
            await bot.send_message(
                target.id,
                f"🔪 You were attacked by {user.first_name}!\n"
                f"You now have {new_hearts} ❤️"
                + (
                    "\n\n💀 You are dead! Use /revive_me"
                    if new_hearts <= 0
                    else ""
                ),
            )
        except Exception:
            pass
    else:
        await db.log_crime(user.id, "kill", target.id, False)

        # Build failure reason
        fail_reason = ""
        if defender_job_type == "police":
            fail_reason = (
                f"👮 {target.first_name} is a level {defender_level} Police!"
            )
        elif defender_job_type == "gangster":
            fail_reason = (
                f"😎 {target.first_name} is a level {defender_level} Gangster!"
            )
        elif defender_level > attacker_level:
            fail_reason = (
                "🛡️ Target dodged your attack!\n"
                "⚠️ Your level is too low. Work on your job skills to improve!"
            )
        else:
            fail_reason = "🍀 Bad luck - you missed!"

        # Consequences for failing
        consequence_text = ""

        # If failed against police, chance of 1 hour jail
        if defender_job_type == "police" and random.random() < 0.6:
            await db.execute(
                """
                INSERT INTO jail (user_id, jailed_by, reason, jailed_at, release_at)
                VALUES ($1, $2, $3, NOW(), NOW() + INTERVAL '1 hour')
                ON CONFLICT (user_id) DO UPDATE SET
                    jailed_by = $2,
                    reason = $3,
                    jailed_at = NOW(),
                    release_at = NOW() + INTERVAL '1 hour'
                """,
                user.id,
                target.id,
                f"Jailed by {target.first_name} when you tried to kill them",
            )
            await db.add_balance(
                target.id,
                POLICE_COUNTER_ARREST_REWARD,
                f"Counter-arrested {user.first_name} after failed /kill",
            )
            consequence_text = (
                "\n\n🔒 You've been jailed for 1 hour! "
                "(Next time think before attacking a cop!)"
            )

            # Notify the jailed user via DM
            try:
                await bot.send_message(
                    user.id,
                    f"🔒 <b>You have been jailed!</b>\n\n"
                    f"⏱️ Jail time: 1 hour\n"
                    f"📝 Reason: Jailed by {target.first_name} when you tried to kill them\n"
                    f"You cannot use /kill or /rob while jailed.",
                )
            except Exception:
                pass

        # If failed against gangster, chance of losing a heart
        elif defender_job_type == "gangster" and random.random() < 0.6:
            new_hearts = await db.remove_heart(user.id)
            consequence_text = f"\n\n💔 {target.first_name} fought back! You now have {new_hearts} ❤️"
            if new_hearts <= 0:
                consequence_text += "\n\n💀 You are dead!"

        await message.reply(
            f"❌ <b>Miss!</b>\n\n"
            f"Your attack on {target.first_name} failed!\n"
            f"Reason: {fail_reason}\n"
            f"Remaining attempts today: {kill_limit - total_kills - 1}"
            f"{consequence_text}"
        )


reg("rob", "💰 Rob a player's wallet")


@client.on_message(filters.command(["rob"]))
async def rob_command(message: Message, bot: Bot):
    """Rob money from another player's wallet."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if user is dead
    if await db.is_user_dead(user.id):
        await message.reply("💀 You're dead! Use /revive_me first.")
        return

    # Check if user is jailed
    can_use, jail_reason = await db.can_use_crime_commands(user.id)
    if not can_use:
        await message.reply(f"❌ {jail_reason}")
        return

    target = await get_target_from_command(bot, message, db)
    if not target:
        await message.reply(
            "💰 <b>Rob Command</b>\n\n"
            "Reply to a user, mention them, or use @username/ID to rob them.\n\n"
            f"• Steals {ROB_MIN_PERCENT}-{ROB_MAX_PERCENT}% of their <b>wallet</b>\n"
            "• Wallet drained first; bank covers the rest if wallet is short\n"
            f"• Daily limit scales with thief Lv. ({action_limit_for_level('rob', 1)} @ Lv.1, +1 every √levels)\n"
            "• Thief job improves success rate\n\n"
            "Usage: <code>/rob</code> (reply/mention/@username)"
        )
        return

    if target.id == user.id:
        await message.reply("❌ You can't rob yourself!")
        return

    if target.is_bot:
        await message.reply("❌ Can't rob bots!")
        return

    # Ensure target exists
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Check if target is in passive mode
    if await db.is_in_passive_mode(target.id):
        await message.reply(
            f"🛡️ {target.first_name} is in passive mode!\n\n"
            f"They haven't done any crime activity for 5+ days.\n"
            f"You can't rob users in passive mode.\n\n"
            f"<i>Read /guide_passivemode for more info.</i>"
        )
        return

    # Daily rob limit scales with thief skill level.
    attacker_job_pre = await db.get_job(user.id)
    thief_level_pre = (
        attacker_job_pre.get("job_level", 1)
        if attacker_job_pre and attacker_job_pre["job_type"] == "thief"
        else 1
    )
    rob_limit = action_limit_for_level("rob", thief_level_pre)
    total_robs = await db.get_daily_action_count(user.id, "rob")
    if total_robs >= rob_limit:
        next_level = level_for_action_limit("rob", rob_limit + 1)
        await message.reply(
            f"❌ You've used all {rob_limit} rob attempts today!\n"
            f"💡 Reach thief Lv.{next_level} to unlock one more attempt."
        )
        return

    target_robs = await db.get_daily_action_count(user.id, "rob", target.id)
    if target_robs >= SAME_TARGET_LIMIT:
        await message.reply(
            f"❌ You've already robbed {target.first_name} twice today!"
        )
        return

    target_wallet = await db.get_wallet(target.id)
    if target_wallet["balance"] < 100:
        await message.reply(
            f"❌ {target.first_name} doesn't have enough to steal!"
        )
        return

    # Get job levels
    attacker_job = await db.get_job(user.id)
    defender_job = await db.get_job(target.id)

    attacker_level = 1
    defender_level = 1
    attacker_is_thief = False
    defender_is_police = False
    defender_is_gangster = False

    if attacker_job and attacker_job["job_type"] == "thief":
        attacker_level = attacker_job.get("job_level", 1)
        attacker_is_thief = True

    if defender_job and defender_job["job_type"] == "police":
        defender_level = defender_job.get("job_level", 1)
        defender_is_police = True

    if defender_job and defender_job["job_type"] == "gangster":
        defender_level = defender_job.get("job_level", 1)
        defender_is_gangster = True

    # Base 55% success rate for thieves, 40% for others
    base_chance = 0.55 if attacker_is_thief else 0.40
    success_chance = calculate_success_chance(
        attacker_level, defender_level, base_chance
    )
    success = random.random() < success_chance

    # Record the attempt
    await db.increment_daily_action(user.id, "rob", target.id)

    if success:
        # Steal ROB_MIN_PERCENT-ROB_MAX_PERCENT% of wallet only.
        steal_percent = random.uniform(
            ROB_MIN_PERCENT / 100, ROB_MAX_PERCENT / 100
        )
        actual_stolen = int(target_wallet["balance"] * steal_percent)

        if actual_stolen > 0:
            await db.add_balance(
                target.id, -actual_stolen, f"Robbed by {user.first_name}"
            )
            await db.add_balance(
                user.id, actual_stolen, f"Robbed {target.first_name}"
            )
        await db.log_crime(user.id, "rob", target.id, True, actual_stolen)

        await message.reply(
            f"💰 <b>Robbery Success!</b>\n\n"
            f"You stole ${actual_stolen:,} from {target.first_name}!"
        )

        # Notify target
        try:
            await bot.send_message(
                target.id,
                f"🚨 You were robbed by {user.first_name}!\n"
                f"Lost: ${actual_stolen:,} from your wallet",
            )
        except Exception:
            pass
    else:
        await db.log_crime(user.id, "rob", target.id, False)

        # Build failure reason
        fail_reason = ""
        if defender_is_police:
            fail_reason = (
                f"👮 {target.first_name} is a level {defender_level} Police!"
            )
        elif defender_is_gangster:
            fail_reason = (
                f"😎 {target.first_name} is a level {defender_level} Gangster!"
            )
        elif defender_level > attacker_level:
            fail_reason = (
                "🎯 Target was too alert!\n"
                "⚠️ Your level is too low. Work on your job skills to improve!"
            )
        else:
            fail_reason = "🍀 Bad luck this time!"

        # Consequences for failing
        consequence_text = ""

        # If failed against police, chance of 45min jail
        if defender_is_police and random.random() < 0.6:
            await db.execute(
                """
                INSERT INTO jail (user_id, jailed_by, reason, jailed_at, release_at)
                VALUES ($1, $2, $3, NOW(), NOW() + INTERVAL '45 minutes')
                ON CONFLICT (user_id) DO UPDATE SET
                    jailed_by = $2,
                    reason = $3,
                    jailed_at = NOW(),
                    release_at = NOW() + INTERVAL '45 minutes'
                """,
                user.id,
                target.id,
                f"Jailed by {target.first_name} when you tried to rob them",
            )
            await db.add_balance(
                target.id,
                POLICE_COUNTER_ARREST_REWARD,
                f"Counter-arrested {user.first_name} after failed /rob",
            )
            consequence_text = (
                "\n\n🔒 You've been jailed for 45 minutes! "
                "(Next time think before robbing a cop!)"
            )

            # Notify the jailed user via DM
            try:
                await bot.send_message(
                    user.id,
                    f"🔒 <b>You have been jailed!</b>\n\n"
                    f"⏱️ Jail time: 45 minutes\n"
                    f"📝 Reason: Jailed by {target.first_name} when you tried to rob them\n"
                    f"You cannot use /kill or /rob while jailed.",
                )
            except Exception:
                pass

        # If failed against gangster, chance of losing a heart
        elif defender_is_gangster and random.random() < 0.6:
            new_hearts = await db.remove_heart(user.id)
            consequence_text = f"\n\n💔 {target.first_name} fought back! You now have {new_hearts} ❤️"
            if new_hearts <= 0:
                consequence_text += "\n\n💀 You are dead!"

        await message.reply(
            f"❌ <b>Robbery Failed!</b>\n\n"
            f"You couldn't rob {target.first_name}!\n"
            f"Reason: {fail_reason}\n"
            f"Remaining attempts: {rob_limit - total_robs - 1}"
            f"{consequence_text}"
        )


reg("arrest", "👮 Arrest a criminal")


@client.on_message(filters.command(["arrest"]))
async def arrest_command(message: Message, bot: Bot):
    """Arrest a criminal (anyone can use)."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if user is police
    job = await db.get_job(user.id)
    if not job or job["job_type"] != "police":
        await message.reply("❌ Only police officers can arrest criminals!")
        return

    # Check if user is dead
    if await db.is_user_dead(user.id):
        await message.reply("💀 You're dead! Use /revive_me first.")
        return

    # Check if user is jailed
    can_use, jail_reason = await db.can_use_crime_commands(user.id)
    if not can_use:
        await message.reply(f"❌ {jail_reason}")
        return

    # Get user job for arrest limit calculation
    user_level = job.get("job_level", 1)
    is_police = True

    # Daily arrest limit scales with police skill level via shared formula.
    arrest_limit = action_limit_for_level("arrest", user_level)

    # Check daily arrest limit
    total_arrests = await db.get_daily_action_count(user.id, "arrest")
    if total_arrests >= arrest_limit:
        await message.reply(
            f"❌ You've used all {arrest_limit} arrest attempts today!"
            " Level up your police skill to increase the limit!"
        )
        return

    target = await get_target_from_command(bot, message, db)
    if not target:
        await message.reply(
            "👮 <b>Arrest Command</b>\n\n"
            "Reply to a user, mention them, or use @username/ID to arrest them.\n\n"
            "• Only police can arrest criminals\n"
            "• Target must have criminal record\n"
            "• You earn money for successful arrests\n"
            "• Gangsters may fight back!\n"
            "• Max custom jail time: 4 hours\n"
            f"• Daily limit: {arrest_limit} arrests"
            f" (3 base + {user_level} police level)" + "\n\n"
            "Usage: <code>/arrest</code> (reply/mention/@username)\n"
            "<i>Optional:</i> /arrest 2h 13m because reason"
        )
        return

    if target.id == user.id:
        await message.reply("❌ You can't arrest yourself!")
        return

    if target.is_bot:
        await message.reply("❌ Can't arrest bots!")
        return

    # Ensure target exists
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Check if target is already jailed
    if await db.is_user_jailed(target.id):
        # Get jail info to show who arrested them
        jail_info = await db.get_jail_info(target.id)
        jailed_by_id = jail_info["jailed_by"]
        jailed_by_user = await db.get_user(jailed_by_id)
        jailed_by_name = (
            jailed_by_user["first_name"] if jailed_by_user else "Unknown"
        )

        # Calculate remaining jail time
        from datetime import datetime

        release_at = jail_info["release_at"]
        if release_at.tzinfo is not None:
            release_at = release_at.replace(tzinfo=None)
        time_remaining = (release_at - datetime.now()).total_seconds()
        if time_remaining > 0:
            remaining_mins = int(time_remaining // 60)
            if remaining_mins < 60:
                time_text = f"{remaining_mins} minutes"
            elif remaining_mins < 120:
                time_text = (
                    f"{remaining_mins // 60} hour {remaining_mins % 60} minutes"
                )
            else:
                time_text = f"{remaining_mins // 60} hours {remaining_mins % 60} minutes"
        else:
            time_text = "releasing soon"

        # Get jail reason
        reason = jail_info.get("reason", "Unknown")

        await message.reply(
            f"🔒 {target.first_name} is already in jail!\n"
            f"Arrested by: {jailed_by_name}\n"
            f"⏱️ Time remaining: {time_text}\n"
            f"📝 Reason: {reason}\n"
            f"Wait for their sentence to end before arresting again."
        )
        return

    # Check if target has criminal record
    has_record = await db.has_criminal_record(target.id)
    if not has_record:
        await message.reply(
            f"❌ {target.first_name} has no criminal record!\n"
            f"You can only arrest criminals."
        )
        return

    # Check if target has recent unsolved crimes (last 6 hours)
    has_recent = await db.has_recent_unsolved_crimes(target.id, hours=6)
    if not has_recent:
        await message.reply(
            f"🤷 {target.first_name} has done crimes in the past, we all know that... "
            f"but there was no unsolved crime in the last 6 hours.\n"
            f"We can't punish them. Sad."
        )
        return

    # Check if target is gangster - they fight back!
    target_job = await db.get_job(target.id)
    gangster_level = 1

    if target_job and target_job["job_type"] == "gangster":
        gangster_level = target_job.get("job_level", 1)

        # Gangster fights back - 50% base chance modified by levels
        fight_success = calculate_success_chance(
            gangster_level, user_level, 0.5
        )

        if random.random() < fight_success:
            # Gangster wins - user loses a heart
            new_hearts = await db.remove_heart(user.id)
            await message.reply(
                f"⚔️ <b>Gangster Fought Back!</b>\n\n"
                f"The gangster {target.first_name} attacked you!\n"
                f"You now have {new_hearts} ❤️"
                + ("\n\n💀 You died!" if new_hearts <= 0 else "")
            )
            return

    # Parse optional duration/reason from command:
    # /arrest 2h 13m because ...
    # /arrest 2h13m because ...
    # /arrest 13m because ...
    # /arrest 2h because ...
    # /arrest because ...
    duration_minutes = None
    custom_reason = None
    raw_args = ""
    if message.text:
        cmd_parts = message.text.split(maxsplit=1)
        raw_args = cmd_parts[1] if len(cmd_parts) > 1 else ""

    if raw_args:
        # If command used target as first arg (non-reply), strip that token first.
        if not message.reply_to_message:
            tokens = raw_args.split(maxsplit=1)
            first_token = tokens[0]
            first_norm = first_token.lstrip("@").lower()
            target_username = (target.username or "").lower()
            if (
                first_token.startswith("@")
                or first_token.isdigit()
                or (target_username and first_norm == target_username)
            ):
                raw_args = tokens[1] if len(tokens) > 1 else ""

        arg_text = raw_args.strip()
        if arg_text:
            if arg_text.lower().startswith("because "):
                custom_reason = arg_text[8:].strip() or None
            else:
                tokens = arg_text.split()
                candidates = []
                if len(tokens) >= 2:
                    candidates.append((" ".join(tokens[:2]), 2))
                candidates.append((tokens[0], 1))

                for candidate, consumed in candidates:
                    parsed = _parse_duration_minutes(candidate)
                    if parsed is None:
                        continue
                    duration_minutes = parsed
                    reason_rest = " ".join(tokens[consumed:]).strip()
                    if reason_rest.lower().startswith("because "):
                        reason_rest = reason_rest[8:].strip()
                    custom_reason = reason_rest or None
                    break

                if duration_minutes is None and arg_text.lower().startswith(
                    "because"
                ):
                    custom_reason = arg_text[7:].strip() or None

    # If police, show duration selection unless duration/reason is provided.
    # If reason is provided without duration, default to 30m.
    if is_police:
        if duration_minutes is None and custom_reason:
            duration_minutes = 30

        if duration_minutes is not None:
            # Mark all unsolved crimes as solved (punished)
            await db.execute(
                """
                UPDATE crime_log
                SET is_solved = TRUE
                WHERE criminal_id = $1 AND is_solved = FALSE
                """,
                target.id,
            )

            arrester_user = await db.get_user(user.id)
            arrester_name = (
                arrester_user["first_name"]
                if arrester_user
                else user.first_name
            )
            reason_text = custom_reason or f"Arrested by {arrester_name}"

            await db.execute(
                f"""
                INSERT INTO jail (user_id, jailed_by, reason, jailed_at, release_at)
                VALUES ($1, $2, $3, NOW(), NOW() + INTERVAL '{duration_minutes} minutes')
                ON CONFLICT (user_id) DO UPDATE SET
                    jailed_by = $2,
                    reason = $3,
                    jailed_at = NOW(),
                    release_at = NOW() + INTERVAL '{duration_minutes} minutes'
                """,
                target.id,
                user.id,
                reason_text,
            )

            reward = POLICE_ARREST_REWARD
            await db.add_balance(
                user.id, reward, f"Arrested {target.first_name}"
            )
            await db.increment_daily_action(user.id, "arrest", target.id)

            duration_text = _format_minutes(duration_minutes)
            await message.reply(
                f"👮 <b>Arrest Successful!</b>\n\n"
                f"You arrested {target.first_name}!\n"
                f"⏱️ Jail time: {duration_text}\n"
                f"📝 Reason: {reason_text}\n"
                f"💰 Reward: ${reward:,}\n"
                f"🔒 All previous crimes marked as punished."
            )

            try:
                await bot.send_message(
                    target.id,
                    f"🔒 <b>You have been jailed!</b>\n\n"
                    f"⏱️ Jail time: {duration_text}\n"
                    f"📝 Reason: {reason_text}\n"
                    f"You cannot use /kill or /rob while jailed.",
                )
            except Exception:
                pass
            return

        # Show duration selection for police
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="⏱️ 30 minutes",
                        callback_data=f"arrest_duration:{user.id}:{target.id}:30",
                    ),
                    InlineKeyboardButton(
                        text="⏱️ 1 hour",
                        callback_data=f"arrest_duration:{user.id}:{target.id}:60",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="⏱️ 2 hours",
                        callback_data=f"arrest_duration:{user.id}:{target.id}:120",
                    ),
                    InlineKeyboardButton(
                        text="⏱️ 3 hours",
                        callback_data=f"arrest_duration:{user.id}:{target.id}:180",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="⏱️ 4 hours",
                        callback_data=f"arrest_duration:{user.id}:{target.id}:240",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="↩️ Cancel",
                        callback_data=f"arrest_cancel:{user.id}",
                    ),
                ],
            ]
        )

        await message.reply(
            f"👮 <b>Arrest {target.first_name}</b>\n\nSelect jail duration:",
            reply_markup=keyboard,
        )
    else:
        # Non-police: auto-arrest for 30 minutes
        duration_minutes = 30

        # Mark all unsolved crimes as solved (punished)
        await db.execute(
            """
            UPDATE crime_log
            SET is_solved = TRUE
            WHERE criminal_id = $1 AND is_solved = FALSE
            """,
            target.id,
        )

        # Get arrester's current name from database
        arrester_user = await db.get_user(user.id)
        arrester_name = (
            arrester_user["first_name"] if arrester_user else user.first_name
        )

        # Jail the criminal
        await db.execute(
            f"""
            INSERT INTO jail (user_id, jailed_by, reason, jailed_at, release_at)
            VALUES ($1, $2, $3, NOW(), NOW() + INTERVAL '{duration_minutes} minutes')
            ON CONFLICT (user_id) DO UPDATE SET
                jailed_by = $2,
                reason = $3,
                jailed_at = NOW(),
                release_at = NOW() + INTERVAL '{duration_minutes} minutes'
            """,
            target.id,
            user.id,
            f"Arrested by {arrester_name}",
        )

        # Reward
        reward = POLICE_ARREST_REWARD
        await db.add_balance(user.id, reward, f"Arrested {target.first_name}")

        # Record the arrest action
        await db.increment_daily_action(user.id, "arrest", target.id)

        await message.reply(
            f"👮 <b>Arrest Successful!</b>\n\n"
            f"You arrested {target.first_name}!\n"
            f"⏱️ Jail time: 30 minutes\n"
            f"💰 Reward: ${reward:,}\n"
            f"🔒 All previous crimes marked as punished."
        )

        # Notify the jailed user
        try:
            await bot.send_message(
                target.id,
                f"🔒 <b>You have been jailed!</b>\n\n"
                f"⏱️ Jail time: 30 minutes\n"
                f"📝 Reason: Jailed by {user.first_name} when you were arrested\n"
                f"You cannot use /kill or /rob while jailed.",
            )
        except Exception:
            pass


@client.on_callback_query(filters.regex(r"^arrest_duration:(\d+):(\d+):(\d+)$"))
async def handle_arrest_duration_callback(callback: CallbackQuery, bot: Bot):
    """Handle arrest duration selection."""
    match = re.match(r"^arrest_duration:(\d+):(\d+):(\d+)$", callback.data)
    initiator_id = int(match.group(1))
    target_id = int(match.group(2))
    duration_minutes = min(int(match.group(3)), ARREST_MAX_MINUTES)

    # Check if the person clicking is the arresting officer
    if callback.from_user.id != initiator_id:
        await safe_callback_answer(
            callback,
            "Only the arresting officer can select duration!",
            show_alert=True,
        )
        return

    user_id = callback.from_user.id

    # Get target info
    target = await db.get_user(target_id)
    if not target:
        await safe_callback_answer(callback, "User not found!", show_alert=True)
        return

    # Check if already jailed
    if await db.is_user_jailed(target_id):
        # Get jail info to show who arrested them
        jail_info = await db.get_jail_info(target_id)
        jailed_by_id = jail_info["jailed_by"]
        jailed_by_user = await db.get_user(jailed_by_id)
        jailed_by_name = (
            jailed_by_user["first_name"] if jailed_by_user else "Unknown"
        )

        # Calculate remaining jail time
        from datetime import datetime

        release_at = jail_info["release_at"]
        if release_at.tzinfo is not None:
            release_at = release_at.replace(tzinfo=None)
        time_remaining = (release_at - datetime.now()).total_seconds()
        if time_remaining > 0:
            remaining_mins = int(time_remaining // 60)
            if remaining_mins < 60:
                time_text = f"{remaining_mins}m remaining"
            else:
                time_text = (
                    f"{remaining_mins // 60}h {remaining_mins % 60}m remaining"
                )
        else:
            time_text = "releasing soon"

        await safe_callback_answer(
            callback,
            f"Already jailed by {jailed_by_name}! {time_text}",
            show_alert=True,
        )
        return

    # Mark all unsolved crimes as solved (punished)
    await db.execute(
        """
        UPDATE crime_log
        SET is_solved = TRUE
        WHERE criminal_id = $1 AND is_solved = FALSE
        """,
        target_id,
    )

    # Get arrester's current name from database
    arrester_user = await db.get_user(user_id)
    arrester_name = (
        arrester_user["first_name"]
        if arrester_user
        else callback.message.from_user.first_name
    )

    # Jail the criminal
    await db.execute(
        f"""
        INSERT INTO jail (user_id, jailed_by, reason, jailed_at, release_at)
        VALUES ($1, $2, $3, NOW(), NOW() + INTERVAL '{duration_minutes} minutes')
        ON CONFLICT (user_id) DO UPDATE SET
            jailed_by = $2,
            reason = $3,
            jailed_at = NOW(),
            release_at = NOW() + INTERVAL '{duration_minutes} minutes'
        """,
        target_id,
        user_id,
        f"Arrested by {arrester_name}",
    )

    # Reward
    reward = POLICE_ARREST_REWARD
    await db.add_balance(user_id, reward, f"Arrested {target['first_name']}")

    # Record the arrest action
    await db.increment_daily_action(user_id, "arrest", target_id)

    # Format duration text
    duration_text = _format_minutes(duration_minutes)

    await queue_it(
        lambda: callback.message.edit_text(
            f"👮 <b>Arrest Successful!</b>\n\n"
            f"You arrested {target['first_name']}!\n"
            f"⏱️ Jail time: {duration_text}\n"
            f"💰 Reward: ${reward:,}\n"
            f"🔒 All previous crimes marked as punished."
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Arrested!")

    # Notify the jailed user
    try:
        await bot.send_message(
            target_id,
            f"🔒 <b>You have been jailed!</b>\n\n"
            f"⏱️ Jail time: {duration_text}\n"
            f"📝 Reason: Jailed by {callback.message.from_user.first_name} when you were arrested\n"
            f"You cannot use /kill or /rob while jailed.",
        )
    except Exception:
        pass


@client.on_callback_query(filters.regex(r"^" + "arrest_cancel"))
async def handle_arrest_cancel_callback(callback: CallbackQuery):
    """Handle arrest cancellation."""
    # Check if the person clicking is the one who initiated the arrest
    parts = callback.data.split(":")
    if len(parts) > 1:
        initiator_id = int(parts[1])
        if callback.from_user.id != initiator_id:
            await safe_callback_answer(
                callback,
                "Only the arresting officer can cancel!",
                show_alert=True,
            )
            return

    await queue_it(
        lambda: callback.message.edit_text("❌ Arrest cancelled."),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Cancelled.")


reg("heal", "💉 Heal a player (doctor)")


@client.on_message(filters.command(["heal"]))
async def heal_command(message: Message, bot: Bot):
    """Heal another player."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if user is doctor
    job = await db.get_job(user.id)
    if not job or job["job_type"] != "doctor":
        await message.reply("❌ Only doctors can heal other players!")
        return

    # Calculate heal limit using the shared level-scaling formula.
    doctor_level = job.get("job_level", 1)
    heal_limit = action_limit_for_level("heal", doctor_level)

    target = await get_target_from_command(bot, message, db)
    if not target:
        await message.reply(
            "👨‍⚕️ <b>Heal Command</b>\n\n"
            "Reply to a user, mention them, or use @username/ID to heal them.\n\n"
            "• Restores 1 heart\n"
            "• Can revive dead players\n"
            f"• Daily limit: {heal_limit} heals"
            + (
                f" (2 base + {doctor_level} doctor level)"
                if doctor_level > 0
                else ""
            )
            + "\n\n"
            "Usage: <code>/heal</code> (reply/mention/@username)"
        )
        return

    if target.id == user.id:
        await message.reply("❌ You can't heal yourself!")
        return

    if target.is_bot:
        await message.reply("❌ Can't heal bots!")
        return

    # Ensure target exists
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Check daily limit
    heals_today = await db.get_daily_action_count(user.id, "heal")
    if heals_today >= heal_limit:
        await message.reply(f"❌ You've used all {heal_limit} heals today!")
        return

    # Check if target needs healing
    target_hearts = await db.get_user_hearts(target.id)
    if target_hearts >= 3:
        await message.reply(f"❌ {target.first_name} already has full health!")
        return

    # Heal them
    new_hearts = await db.restore_heart(target.id)
    await db.increment_daily_action(user.id, "heal", target.id)

    # Reward healer + doctor XP
    reward = DOCTOR_HEAL_REWARD
    await db.add_balance(user.id, reward, f"Healed {target.first_name}")
    from bot.constants import JOB_XP_PER_WORK

    xp_gain = JOB_XP_PER_WORK
    await db.update_job_xp(user.id, xp_gain)

    was_dead = target_hearts <= 0

    await message.reply(
        f"💉 <b>Healing Complete!</b>\n\n"
        f"{'You revived' if was_dead else 'You healed'} {target.first_name}!\n"
        f"They now have {new_hearts} ❤️\n"
        f"💰 Earned: ${reward:,}\n"
        f"⭐ XP Gained: +{xp_gain}\n"
        f"📊 Heals used today: {heals_today + 1}/{heal_limit}"
    )

    # Notify target
    try:
        await bot.send_message(
            target.id,
            f"💉 You were {'revived' if was_dead else 'healed'} by {user.first_name}!\n"
            f"You now have {new_hearts} ❤️",
        )
    except Exception:
        pass


reg("guide_passivemode", "🛡️ Learn about passive mode")


@client.on_message(filters.command(["guide_passivemode"]))
async def guide_passivemode_command(
    message: Message,
):
    """Explain passive mode to users."""
    text = "🛡️ <b>Passive Mode Guide</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "<b>What is Passive Mode?</b>\n"
    text += "Passive mode protects you from crimes when you're not actively playing.\n\n"
    text += "<b>How does it work?</b>\n"
    text += "• If you haven't committed any crime for <b>5 consecutive days</b>, you enter passive mode\n"
    text += "• While in passive mode, you <b>cannot</b> be:\n"
    text += "  • 🔪 Killed (/kill)\n"
    text += "  • 💰 Robbed (/rob)\n"
    text += "  • 🦹 Heisted (/heist)\n\n"
    text += "<b>How to exit passive mode?</b>\n"
    text += "• Simply commit any crime (/kill, /rob, /heist) and you'll exit passive mode\n"
    text += "• Your 5-day crime-free timer resets\n\n"
    text += "<b>Why passive mode exists?</b>\n"
    text += "• Protects inactive players from being exploited\n"
    text += "• Encourages fair gameplay\n"
    text += "• Gives casual players a safe experience\n\n"
    text += "<i>💡 Tip: If you want to stay protected, just avoid crime commands for 5 days!</i>"

    await message.reply(text)


reg("heist", "🦹 Do a heist (thief)")


@client.on_message(filters.command(["heist"]))
async def heist_command(message: Message, bot: Bot):
    """Attempt a heist on someone's bank account."""
    from bot.constants import (
        HEIST_COOLDOWN_HOURS,
        HEIST_VAULTS_NORMAL,
        HEIST_VAULTS_WITH_SECURITY,
    )

    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if user is thief
    job = await db.get_job(user.id)
    if not job or job["job_type"] != "thief":
        await message.reply("❌ Only thieves can perform heists!")
        return
    thief_level = job.get("job_level", 1)

    if await db.is_user_dead(user.id):
        await message.reply("💀 You're dead! Use /revive_me first.")
        return

    # Check if user is jailed
    can_use, jail_reason = await db.can_use_crime_commands(user.id)
    if not can_use:
        await message.reply(f"❌ {jail_reason}")
        return

    target = await get_target_from_command(bot, message, db)
    if not target:
        heist_limit = action_limit_for_level("heist", thief_level)
        await message.reply(
            "🦹 <b>Heist Command</b>\n\n"
            "Reply to a user, mention them, or use @username/ID to heist their bank account!\n\n"
            f"• Steals 5% of their bank (capped at $10M)\n"
            f"• Victim loses 0.5% (capped at $100M)\n"
            f"• Daily limit: {heist_limit} heists (Thief Lv {thief_level})\n"
            f"• 6-hour cooldown on same target\n"
            f"• Higher thief level = better crack chance\n"
            f"• Mini-game: Choose the correct vault\n\n"
            "Usage: <code>/heist</code> (reply/mention/@username)"
        )
        return

    if target.id == user.id:
        await message.reply("❌ You can't heist yourself!")
        return

    if target.is_bot:
        await message.reply("❌ Can't heist bots!")
        return

    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Check if target is in passive mode
    if await db.is_in_passive_mode(target.id):
        await message.reply(
            f"🛡️ {target.first_name} is in passive mode!\n\n"
            f"They haven't done any crime activity for 5+ days.\n"
            f"You can't heist users in passive mode.\n\n"
            f"<i>Read /guide_passivemode for more info.</i>"
        )
        return

    # Check daily limits
    heist_limit = action_limit_for_level("heist", thief_level)
    total_heists = await db.get_daily_action_count(user.id, "heist")
    if total_heists >= heist_limit:
        await message.reply(
            f"❌ You've used all {heist_limit} heist attempts today!"
        )
        return

    # Check cooldown on target
    if not await db.can_heist_target(user.id, target.id, HEIST_COOLDOWN_HOURS):
        await message.reply(
            f"❌ You can't heist {target.first_name} right now! Wait 6 hours between heists."
        )
        return

    # Check if target has bank money
    target_bank = await db.get_bank_balance(target.id)
    if not target_bank or target_bank["balance"] < 100:
        await message.reply(
            f"❌ {target.first_name} doesn't have enough in their bank!"
        )
        return

    # Check if target has security
    target_security = await db.get_user_security(target.id)
    has_security = target_security and target_security.get("is_active", False)

    # Determine number of vaults
    num_vaults = (
        HEIST_VAULTS_WITH_SECURITY if has_security else HEIST_VAULTS_NORMAL
    )

    # Pre-determine the correct vault and store in DB
    correct_vault = random.randint(1, num_vaults)
    await db.store_heist_vault(user.id, target.id, correct_vault, num_vaults)
    await db.increment_daily_action(user.id, "heist", target.id)

    # Show heist mini-game
    buttons = []
    for i in range(1, num_vaults + 1):
        buttons.append([
            InlineKeyboardButton(
                text=f"🏦 Vault {i}",
                callback_data=f"heist_vault:{target.id}:{i}",
            )
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

    security_text = (
        "\n🔒 <b>Security active!</b> More vaults to choose from."
        if has_security
        else ""
    )

    await message.reply(
        f"🦹 <b>Heist {target.first_name}'s Bank</b>\n\n"
        f"Choose the correct vault to crack!\n"
        f"Vaults: {num_vaults}{security_text}\n\n"
        f"<i>One vault contains the loot, others are empty!</i>",
        reply_markup=keyboard,
    )


@client.on_callback_query(filters.regex(r"^" + "heist_vault:"))
async def handle_heist_vault_callback(callback: CallbackQuery, bot: Bot):
    """Handle heist vault selection."""
    from bot.constants import (
        HEIST_LOSE_CAP,
        HEIST_LOSE_PERCENT,
        HEIST_STEAL_CAP,
        HEIST_STEAL_PERCENT,
    )

    user = callback.from_user

    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid action")
        return

    target_id = int(parts[1])
    chosen_vault = int(parts[2])

    # Get user and target info
    user_job = await db.get_job(user.id)
    thief_level = user_job.get("job_level", 1) if user_job else 1
    target = await db.get_user(target_id)

    if not target:
        await callback.answer("User not found!", show_alert=True)
        return

    # Get target's bank balance
    target_bank = await db.get_bank_balance(target_id)
    if not target_bank or target_bank["balance"] < 100:
        await callback.answer(
            "Target doesn't have enough in bank!", show_alert=True
        )
        return

    # Get stored vault info from DB
    vault_info = await db.get_heist_vault(user.id, target_id)
    if not vault_info:
        await callback.answer(
            "Heist session expired. Use /heist again!", show_alert=True
        )
        return

    correct_vault = vault_info["correct_vault"]
    num_vaults = vault_info["num_vaults"]

    # Delete the stored vault info
    await db.delete_heist_vault(user.id, target_id)

    # Check if target has security (for notification purposes)
    target_security = await db.get_user_security(target_id)
    has_security = target_security and target_security.get("is_active", False)

    # Check if user chose correctly.
    # Higher thief level gets a small bonus chance even on wrong first pick.
    bonus_crack_chance = min(0.35, max(0.0, (thief_level - 1) * 0.02))
    success = chosen_vault == correct_vault or (
        chosen_vault != correct_vault and random.random() < bonus_crack_chance
    )

    if success:
        # Calculate steal amount
        steal_amount = int(target_bank["balance"] * HEIST_STEAL_PERCENT)
        steal_amount = min(steal_amount, HEIST_STEAL_CAP)

        # Calculate victim loss
        victim_loss = int(steal_amount * HEIST_LOSE_PERCENT)
        victim_loss = min(victim_loss, HEIST_LOSE_CAP)

        # Transfer money: victim loses victim_loss, stealer gets steal_amount
        # Bank covers the difference (bank policy)
        await db.execute(
            """
            UPDATE bank_accounts
            SET balance = balance - $1, last_updated = NOW()
            WHERE user_id = $2
            """,
            victim_loss,
            target_id,
        )
        # Log the bank deduction as a transaction
        await db.execute(
            "INSERT INTO transactions (user_id, amount, reason) VALUES ($1, $2, $3)",
            target_id,
            -victim_loss,
            "Heisted by someone",
        )
        await db.add_balance(
            user.id, steal_amount, f"Successful heist on {target['first_name']}"
        )

        # Log heist as crime (for arrest purposes)
        await db.log_crime(user.id, "heist", target_id, True, steal_amount)
        await db.log_heist(user.id, target_id, "win", steal_amount)

        # Break security if active
        if has_security:
            await db.break_security(target_id)
            # Notify target about broken security
            try:
                await bot.send_message(
                    target_id,
                    f"🚨 <b>Security Alert!</b>\n\n"
                    f"Your security system has been breached!\n"
                    f"💰 Stolen: ${steal_amount:,} from your bank\n"
                    f"💸 Because bank is sorry, you only lost: ${victim_loss:,} from your bank\n"
                    f"🔒 You need to buy a new security system from the shop.\n\n"
                    f"<i>You have 6 hours of immunity from further heists.</i>",
                )
            except Exception:
                pass
        else:
            # Notify target
            try:
                await bot.send_message(
                    target_id,
                    f"🚨 <b>Bank Alert!</b>\n\n"
                    f"Your bank was heisted!\n"
                    f"💰 Stolen: ${steal_amount:,} from your bank\n"
                    f"💸 Because bank is sorry, you only lost: ${victim_loss:,} from your bank\n"
                    f"🔒 YOU SHOULD buy a security system from the shop!! And make it hard to heist your bank\n\n"
                    f"<i>You have 6 hours of immunity from further heists.</i>",
                )
            except Exception:
                pass

        await queue_it(
            lambda: callback.message.edit_text(
                f"🦹 <b>Heist Successful!</b>\n\n"
                f"You cracked Vault {chosen_vault} and found ${steal_amount:,}!\n"
                f"💰 Stolen from {target['first_name']}'s bank"
            ),
            callback.message.chat,
        )
        await callback.answer(f"Stole ${steal_amount:,}!")
    else:
        # Fauked heist - give some money to thief
        # Calculate what they would have gotten
        potential_steal = int(target_bank["balance"] * HEIST_STEAL_PERCENT)
        potential_steal = min(potential_steal, HEIST_STEAL_CAP)
        # consolation = int(potential_steal * HEIST_FAIL_VAULT_PERCENT)
        # TODO: remove consaltion as it is just free money lol
        consolation = 100

        # Give consolation from bank, not from target (bank policy)
        await db.add_balance(
            user.id,
            consolation,
            f"Failed heist consolation on {target['first_name']}",
        )

        # Log failed heist as crime too (since they still got money)
        await db.log_crime(user.id, "heist", target_id, True, consolation)
        await db.log_heist(user.id, target_id, "lose", consolation)

        # Get target job for consequences
        target_job = await db.get_job(target_id)
        target_is_gangster = target_job and target_job["job_type"] == "gangster"

        consequence_text = ""

        # If failed against gangster, small chance of losing a heart
        if target_is_gangster and random.random() < 0.1:
            new_hearts = await db.remove_heart(user.id)
            consequence_text = f"\n\n💔 {target['first_name']} caught you! You now have {new_hearts} ❤️"
            if new_hearts <= 0:
                consequence_text += "\n\n💀 You are dead!"

        await queue_it(
            lambda: callback.message.edit_text(
                f"❌ <b>Heist Failed!</b>\n\n"
                f"You chose Vault {chosen_vault}, but it was empty!\n"
                f"The correct vault was Vault {correct_vault}.\n"
                f"You got ${consolation:,} consolation\n"
                f"{consequence_text}"
            ),
            callback.message.chat,
        )
        await callback.answer(
            f"Failed! Got ${consolation:,} consolation.", show_alert=True
        )


reg("gangwar", "⚔️ Start a gang war with another gang")


@client.on_message(filters.command(["gangwar"]))
async def gangwar_command(message: Message, bot: Bot):
    """Start a gang war with another gang."""
    from bot.constants import (
        GANGWAR_DAILY_LIMIT,
        GANGWAR_HEART_LOSS_PERCENT,
        GANGWAR_IMMUNITY_HOURS,
        GANGWAR_REWARD_BASE,
    )

    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if user is in a gang
    user_gang = await db.get_user_gang(user.id)
    if not user_gang:
        await message.reply(
            "❌ You need to be in a gang to start a gang war!\n"
            "Use /join_gang to join one."
        )
        return

    # Check if user is gangster job
    job = await db.get_job(user.id)
    if not job or job["job_type"] != "gangster":
        await message.reply("❌ Only gangsters can start gang wars!")
        return

    if await db.is_user_dead(user.id):
        await message.reply("💀 You're dead! Use /revive_me first.")
        return

    target = await get_target_from_command(bot, message, db)
    if not target:
        # Calculate gangwar limit based on level
        user_level = job.get("job_level", 1)
        gangwar_limit = int(
            GANGWAR_DAILY_LIMIT + max(0, (user_level - 1) * 0.5)
        )
        gangwar_limit = min(gangwar_limit, 10)  # Cap at 10
        await message.reply(
            "⚔️ <b>Gang War</b>\n\n"
            "Reply to a user, mention them, or use @username/ID from another gang to challenge them!\n\n"
            "• Must be in a gang and have gangster job\n"
            f"• Daily limit: {gangwar_limit} challenges (increases with level)\n"
            "• On first win vs same enemy gang today: target gang loses hearts and bank money\n"
            "• Reward scales with target gang bank, capped at $1,000,000\n"
            "• On lose: You lose 1 heart\n"
            "• Immunity: 6 hours\n\n"
            "Usage: <code>/gangwar</code> (reply/mention/@username)"
        )
        return

    if target.id == user.id:
        await message.reply("❌ You can't gangwar yourself!")
        return

    if target.is_bot:
        await message.reply("❌ Can't gangwar bots!")
        return

    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Check if target is in a gang
    target_gang = await db.get_user_gang(target.id)
    if not target_gang:
        await message.reply(f"❌ {target.first_name} is not in a gang!")
        return

    # Check if target gang is same as user gang
    if target_gang["id"] == user_gang["id"]:
        await message.reply(
            "❌ You can't start a gang war with your own gang members!"
        )
        return

    # Target must currently be working as gangster
    target_job = await db.get_job(target.id)
    if not target_job or target_job.get("job_type") != "gangster":
        await message.reply(
            f"❌ {target.first_name} is not gangster at the moment."
        )
        return

    # Check immunity
    if await db.check_gang_immunity(user_gang["id"], target_gang["id"]):
        expiry = await db.get_gang_immunity_expiry(
            user_gang["id"], target_gang["id"]
        )
        time_text = "unknown time"
        if expiry:
            from datetime import datetime

            if expiry.tzinfo is not None:
                expiry = expiry.replace(tzinfo=None)
            remaining = max(0, int((expiry - datetime.now()).total_seconds()))
            hours = remaining // 3600
            minutes = (remaining % 3600) // 60
            time_text = f"{hours}h {minutes}m"
        await message.reply(
            f"❌ Your gang currently has immunity from {target_gang['name']}.\n"
            f"⏱️ Time left: {time_text}\n"
            f"Wait for immunity to expire before attacking this gang again."
        )
        return

    # Check daily limits
    user_level = job.get("job_level", 1)
    # Gangwar limit scales with level: 2 + (level - 1) * 0.5, capped at 10
    # Level 1: 2, Level 3: 3, Level 5: 4, Level 11: 10
    gangwar_limit = int(GANGWAR_DAILY_LIMIT + max(0, (user_level - 1) * 0.5))
    gangwar_limit = min(gangwar_limit, 10)  # Cap at 10
    total_gangwars = await db.get_daily_action_count(user.id, "gangwar")
    if total_gangwars >= gangwar_limit:
        reset_seconds = await db.get_utc_midnight_seconds_remaining()
        reset_text = _format_remaining_seconds(reset_seconds)
        await message.reply(
            f"❌ You've used all {gangwar_limit} gang war attempts today!\n"
            f"⏱️ Reset in: {reset_text} (UTC midnight)"
        )
        return

    # Get user level
    user_level = job.get("job_level", 1)

    # Get target level
    target_level = target_job.get("job_level", 1)

    # Calculate success chance (50% base, modified by level difference)
    level_diff = user_level - target_level
    success_chance = 0.5 + (level_diff * 0.05)
    success_chance = max(0.2, min(0.8, success_chance))

    # Record the attempt
    await db.increment_daily_action(user.id, "gangwar", target.id)

    if random.random() < success_chance:
        pair_already_won_today = await db.had_successful_gang_war_today(
            user_gang["id"], target_gang["id"]
        )
        hearts_lost_count = 0
        target_lost_text = ""
        lost_text = ""
        deducted = 0
        reward = 0

        if not pair_already_won_today:
            # First successful attack for this gang pair today:
            # target gang loses hearts and bank money, and winner gets paid.
            target_gang_members = await db.get_gang_members(target_gang["id"])
            hearts_to_lose = max(
                1,
                int(
                    len(target_gang_members) * GANGWAR_HEART_LOSS_PERCENT / 100
                ),
            )

            # Target (replied user) always loses a heart
            hearts_lost_count = 1
            new_hearts = await db.remove_heart(target.id)
            target_lost_text = f"{target.first_name} now has {new_hearts} ❤️"
            if new_hearts <= 0:
                target_lost_text += "\n💀 They are dead!"

            # Randomly select other members to lose hearts
            other_members = [
                m for m in target_gang_members if m["user_id"] != target.id
            ]
            random.shuffle(other_members)

            additional_lost = []
            for member in other_members[: hearts_to_lose - 1]:
                member_hearts = await db.remove_heart(member["user_id"])
                member_name = (
                    member["first_name"] or member["username"] or "Unknown"
                )
                additional_lost.append(
                    f"{member_name} now has {member_hearts} ❤️"
                )
                hearts_lost_count += 1
                if member_hearts <= 0:
                    additional_lost[-1] += "\n💀 They are dead!"
            lost_text = "\n".join(additional_lost)

            target_gang_bank = int(
                (await db.get_gang_total_bank(target_gang["id"])) or 0
            )
            raw_reward = (target_gang_bank * 3) // 100
            scaled_reward = int(
                raw_reward * (1 + max(0, user_level - 1) * 0.03)
            )
            reward = max(GANGWAR_REWARD_BASE, min(1_000_000, scaled_reward))
            deducted = await db.take_from_gang_bank(target_gang["id"], reward)
            reward = deducted
            if reward > 0:
                await db.add_balance(
                    user.id, reward, f"Won gang war vs {target_gang['name']}"
                )

            # Add immunity (whole target gang immune from attacker gang)
            await db.add_gang_immunity(
                target_gang["id"], user_gang["id"], GANGWAR_IMMUNITY_HOURS
            )

        # Log gang war
        await db.add_gang_war(
            user_gang["id"],
            target_gang["id"],
            user.id,
            target.id,
            "win",
            hearts_lost_count,
            reward,
        )
        await db.log_crime(user.id, "gangwar", target.id, True, reward)

        if pair_already_won_today:
            result_text = (
                f"⚔️ <b>Gang War Victory!</b>\n\n"
                f"You defeated {target.first_name} from {target_gang['name']}!\n"
                f"🛡️ This gang pair already had a successful war today.\n"
                f"No additional hearts or money were taken."
            )
        else:
            result_text = (
                f"⚔️ <b>Gang War Victory!</b>\n\n"
                f"You defeated {target.first_name} from {target_gang['name']}!\n"
                f"💰 Reward: ${reward:,}\n\n"
                f"❤️ Hearts lost from {target_gang['name']}:\n"
                f"• {target_lost_text}"
                + (f"\n• {lost_text}" if lost_text else "")
            )
            if deducted <= 0:
                result_text += "\n\n🏦 Target gang bank had no money to steal."

        await message.reply(result_text)

        # Notify target
        try:
            await bot.send_message(
                target.id,
                f"⚔️ Your gang lost a gang war to {user.first_name}!\n"
                f"You now have {new_hearts} ❤️",
            )
        except Exception:
            pass
    else:
        # User loses - loses 1 heart
        new_hearts = await db.remove_heart(user.id)

        # Log gang war
        await db.add_gang_war(
            user_gang["id"],
            target_gang["id"],
            user.id,
            target.id,
            "lose",
            1,
            0,
        )
        await db.log_crime(user.id, "gangwar", target.id, False, 0)

        await message.reply(
            f"💀 <b>Gang War Defeat!</b>\n\n"
            f"{target.first_name} from {target_gang['name']} defeated you!\n"
            f"You now have {new_hearts} ❤️"
            + ("\n\n💀 You are dead!" if new_hearts <= 0 else "")
        )


reg("revive_me", "💀 Revive yourself")


@client.on_message(filters.command(["revive_me", "revive"]))
async def revive_command(
    message: Message,
):
    """Pay to revive yourself."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    hearts = await db.get_user_hearts(user.id)
    if hearts > 0:
        await message.reply(f"❌ You're not dead! You have {hearts} ❤️")
        return

    wallet = await db.get_wallet(user.id)
    bank = await db.get_bank_balance(user.id)
    bank_balance = bank["balance"] if bank else 0

    if wallet["balance"] < REVIVE_COST:
        await message.reply(
            f"💀 You're dead!\n\n"
            f"Revive cost: ${REVIVE_COST:,}\n"
            f"Your wallet: ${wallet['balance']:,}\n"
            f"Your bank: ${bank_balance:,}\n\n"
            f"💡 Withdraw money from bank with /withdraw, or\n"
            f"💉 Ask a doctor to /heal you for free!"
        )
        return

    # Show confirmation
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"✅ Revive (${REVIVE_COST:,} - 1 ❤️)",
                    callback_data="revive_me_confirm",
                ),
                InlineKeyboardButton(
                    text=f"💖 Full Revive (${REVIVE_FULL_COST:,} - 3 ❤️)",
                    callback_data="revive_me_full_confirm",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="revive_me_cancel",
                ),
            ],
        ]
    )

    await message.reply(
        f"💀 <b>Revive Options</b>\n\n"
        f"Your wallet: ${wallet['balance']:,}\n"
        f"Your bank: ${bank_balance:,}\n\n"
        f"• <b>Standard Revive</b>: ${REVIVE_COST:,} → 1 ❤️\n\n"
        f"• <b>Full Revive</b>: ${REVIVE_FULL_COST:,} → 3 ❤️\n\n"
        f"💡 Withdraw money from bank with /withdraw, or\n"
        f"💉 Ask a doctor to /heal you for free!",
        reply_markup=keyboard,
    )


@client.on_callback_query(filters.regex(r"^" + "revive_me_confirm" + r"$"))
async def revive_me_confirm_callback(
    callback: CallbackQuery,
):
    """Handle revive confirmation."""
    user_id = callback.from_user.id

    # Double-check they're still dead
    hearts = await db.get_user_hearts(user_id)
    if hearts > 0:
        await callback.answer("You're not dead!", show_alert=True)
        return

    wallet = await db.get_wallet(user_id)
    if wallet["balance"] < REVIVE_COST:
        await callback.answer("Not enough money!", show_alert=True)
        return

    # Pay and revive
    await db.add_balance(user_id, -REVIVE_COST, "Self revive")
    await db.set_user_hearts(user_id, 1)

    await queue_it(
        lambda: callback.message.edit_text(
            f"💉 <b>Revived!</b>\n\n"
            f"Paid ${REVIVE_COST:,} to come back to life.\n"
            f"You now have 1 ❤️"
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Revived!")


@client.on_callback_query(filters.regex(r"^" + "revive_me_full_confirm" + r"$"))
async def revive_me_full_confirm_callback(
    callback: CallbackQuery,
):
    """Handle full revive confirmation."""
    user_id = callback.from_user.id

    hearts = await db.get_user_hearts(user_id)
    if hearts > 0:
        await callback.answer("You're not dead!", show_alert=True)
        return

    wallet = await db.get_wallet(user_id)
    if wallet["balance"] < REVIVE_FULL_COST:
        await callback.answer("Not enough money!", show_alert=True)
        return

    await db.add_balance(user_id, -REVIVE_FULL_COST, "Full self revive")
    await db.set_user_hearts(user_id, 3)

    await queue_it(
        lambda: callback.message.edit_text(
            f"💖 <b>Full Revive Complete!</b>\n\n"
            f"Paid ${REVIVE_FULL_COST:,} to come back at full health.\n"
            f"You now have 3 ❤️"
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Fully revived!")


@client.on_callback_query(filters.regex(r"^" + "revive_me_cancel" + r"$"))
async def revive_me_cancel_callback(callback: CallbackQuery):
    """Cancel revive."""
    await queue_it(
        lambda: callback.message.edit_text("❌ Revive cancelled."),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Cancelled.")


reg("hearts", "❤️ Check health status")


@client.on_message(filters.command(["hearts", "health"]))
async def hearts_command(
    message: Message,
):
    """Check your heart status."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    hearts = await db.get_user_hearts(user.id)

    hearts_display = "❤️" * hearts + "🖤" * (3 - hearts)

    text = "💗 <b>Health Status</b>\n\n"
    text += f"Hearts: {hearts_display} ({hearts}/3)\n\n"

    if hearts <= 0:
        text += "💀 <b>You are DEAD!</b>\n"
        text += (
            f"Use /revive_me (${REVIVE_COST:,}) or ask a doctor to /heal you."
        )
    elif hearts == 1:
        text += "⚠️ Critical health! Be careful!"
    elif hearts == 2:
        text += "⚡ Injured. Find a doctor."
    else:
        text += "✅ Full health!"

    await message.reply(text)


reg("release", "🔓 Release jailed player (police)")


@client.on_message(filters.command(["release"]))
async def release_command(message: Message, bot: Bot):
    """Release a jailed player (police only)."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    # Check if user is police
    job = await db.get_job(user.id)
    if not job or job["job_type"] != "police":
        await message.reply("❌ Only police officers can release prisoners!")
        return

    target = await get_target_from_command(bot, message, db)
    if not target:
        await message.reply(
            "🔓 <b>Release Command</b>\n\n"
            "Reply to a user, mention them, or use @username/ID to release them from jail.\n\n"
            "• Can't release yourself\n"
            "• To release another's arrest: need higher level (or more XP if same level)\n"
            "• Jailed players can't use /kill or /rob\n\n"
            "Usage: <code>/release</code> (reply/mention/@username)"
        )
        return

    if target.is_bot:
        await message.reply("❌ Can't release bots!")
        return

    # Ensure target exists
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Check if target is jailed
    if not await db.is_user_jailed(target.id):
        await message.reply(f"❌ {target.first_name} is not jailed!")
        return

    # Get jail info
    jail_info = await db.get_jail_info(target.id)
    jailed_by = jail_info["jailed_by"]

    # Can't release yourself
    if target.id == user.id:
        await message.reply("❌ You can't release yourself from jail!")
        return

    # If releasing another's arrest, need higher level (or more XP if same level)
    if jailed_by != user.id:
        # Get the arresting officer's job level and XP
        arresting_job = await db.get_job(jailed_by)
        arresting_level = 1
        arresting_xp = 0
        if arresting_job:
            arresting_level = arresting_job.get("job_level", 1)
            arresting_xp = arresting_job.get("job_xp", 0)

        police_level = job.get("job_level", 1)
        police_xp = job.get("job_xp", 0)

        # Check if user has higher level, or same level but more XP
        can_release = False
        if police_level > arresting_level:
            can_release = True
        elif police_level == arresting_level and police_xp > arresting_xp:
            can_release = True

        if not can_release:
            # Get arresting officer's name
            arresting_user = await db.fetchrow(
                "SELECT user_id, first_name, username FROM users WHERE user_id = $1",
                jailed_by,
            )
            arresting_name = (
                arresting_user["first_name"] if arresting_user else "Unknown"
            )
            arresting_id = jailed_by

            await message.reply(
                f"❌ You need higher level or more XP to release this prisoner!\n"
                f"Your level: {police_level} (XP: {police_xp})\n"
                f"Arresting officer: {arresting_name} (ID: {arresting_id})\n"
                f"Officer's level: {arresting_level} (XP: {arresting_xp})\n"
                f"Level up or gain more XP to release them."
            )
            return

    # Show confirmation
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Release",
                    callback_data=f"release_confirm:{target.id}:{user.id}",
                ),
                InlineKeyboardButton(
                    text="❌ Cancel",
                    callback_data="release_cancel",
                ),
            ]
        ]
    )

    await message.reply(
        f"🔓 <b>Release Prisoner</b>\n\n"
        f"Are you sure you want to release {target.first_name} from jail?",
        reply_markup=keyboard,
    )


@client.on_callback_query(filters.regex(r"^" + "release_confirm:"))
async def release_confirm_callback(callback: CallbackQuery, bot: Bot):
    """Handle release confirmation."""
    parts = callback.data.split(":")
    target_id = int(parts[1])
    officer_id = int(parts[2]) if len(parts) > 2 else None
    user_id = callback.from_user.id

    # Verify that the officer who clicked is the same who did /release
    if officer_id is not None and user_id != officer_id:
        await callback.answer(
            "Only the officer who initiated the release can confirm!",
            show_alert=True,
        )
        return

    target = await db.get_user(target_id)
    if not target:
        await callback.answer("User not found!", show_alert=True)
        return

    # Release the prisoner
    await db.release_user(target_id)

    await queue_it(
        lambda: callback.message.edit_text(
            f"🔓 Released {target['first_name']} from jail!"
        ),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Released!")

    # Notify the released prisoner
    try:
        await bot.send_message(
            target_id,
            f"🔓 You've been released from jail by {callback.message.from_user.first_name}!\n"
            f"You can now use /kill and /rob again.",
        )
    except Exception:
        pass


@client.on_callback_query(filters.regex(r"^" + "release_cancel" + r"$"))
async def release_cancel_callback(callback: CallbackQuery):
    """Cancel release."""
    await queue_it(
        lambda: callback.message.edit_text("❌ Release cancelled."),
        callback.message.chat,
    )
    await safe_callback_answer(callback, "Cancelled.")


reg("guide_crimes", "📖 Crime & combat guide")


@client.on_message(filters.command(["guide_crimes"]))
async def guide_crimes_command(message: Message):
    """Show detailed crime and combat guide."""
    text = "📖 <b>Crime & Combat Guide</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    text += "<blockquote expandable>"
    text += "<b>❤️ Health System</b>\n"
    text += "• Everyone has 3 hearts max\n"
    text += "• Attacks remove 1 heart\n"
    text += "• At 0 hearts = dead (can't play)\n"
    text += "• Use /hearts to check health\n"
    text += "• Use /revive_me ($5,000) when dead\n\n"

    text += "<b>🔪 Attack Commands:</b>\n\n"

    text += "<b>/kill @user</b> - Attack someone\n"
    text += (
        f"  • Daily limit: {action_limit_for_level('kill', 1)} at gangster Lv.1, "
        f"+1 every √level (see /lvl_for kill N)\n"
    )
    text += f"  • Same target limit: {SAME_TARGET_LIMIT}/day\n"
    text += "  • Success based on level + luck\n"
    text += "  • Higher level = better chance\n\n"

    text += "<b>/rob @user</b> - Steal money\n"
    text += (
        f"  • Daily limit: {action_limit_for_level('rob', 1)} at thief Lv.1, "
        f"+1 every √level (see /lvl_for rob N)\n"
    )
    text += f"  • Steals {ROB_MIN_PERCENT}-{ROB_MAX_PERCENT}% of wallet\n"
    text += "  • Wallet drained first; bank covers the rest if needed\n"
    text += "  • Success based on level + luck\n\n"

    text += "<b>👮 Job-Specific Commands:</b>\n\n"

    text += "<b>/arrest @user</b> (Police only)\n"
    text += "  • Catch criminals with records\n"
    text += "  • Earn bounty reward\n"
    text += "  • Must be Police job\n\n"

    text += "<b>/heal @user</b> (Doctor only)\n"
    text += "  • Restore 1 heart to injured player\n"
    text += "  • Earn $750+ per heal\n"
    text += "  • Daily limit increases with level\n\n"

    text += "<b>/heist</b> (Thief only)\n"
    text += "  • High-risk solo robbery\n"
    text += "  • Big rewards if successful\n"
    text += "  • Daily limit increases with thief level\n"
    text += "  • Chance of failure + injury\n\n"

    text += "<b>💡 Strategy Tips:</b>\n"
    text += "• Keep money in /bank to protect from /rob\n"
    text += "• Level up jobs for better success rates\n"
    text += "• Check /hearts before risky actions\n"
    text += "• Make friends with doctors!\n"
    text += "</blockquote>"

    await message.reply(text)
