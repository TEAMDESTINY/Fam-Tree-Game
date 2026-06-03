"""Achievement checking and unlocking system."""

import html
import json
import logging
from pathlib import Path

from pyrogram import Client as Bot

from bot.constants import ACHIEVEMENTS
from bot.database import Database

logger = logging.getLogger(__name__)

BASE_DIR = Path(__file__).parent.parent
OFFICIAL_CRAFT_DB_PATH = BASE_DIR / "assets" / "LittleAlchemy2" / "db.json"
FANON_CRAFT_DB_PATH = BASE_DIR / "assets" / "LittleAlchemy2" / "fanon_db.json"


def _load_craft_items(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        data = json.load(f)
    return data.get("items", {})


OFFICIAL_CRAFT_ITEMS = _load_craft_items(OFFICIAL_CRAFT_DB_PATH)
FANON_CRAFT_ITEMS = _load_craft_items(FANON_CRAFT_DB_PATH)
ALL_CRAFT_ITEMS = {**OFFICIAL_CRAFT_ITEMS, **FANON_CRAFT_ITEMS}
OFFICIAL_CRAFT_KEYS = set(OFFICIAL_CRAFT_ITEMS.keys())
EXTRAS_CRAFT_KEYS = {
    key
    for key, value in FANON_CRAFT_ITEMS.items()
    if key.lower().startswith("fanon:") or value.get("fanon")
}
MYTH_OR_MONSTER_KEYS = {
    key
    for key, value in ALL_CRAFT_ITEMS.items()
    if value.get("myths") or value.get("monster")
}
PROGRAMMING_KEYS = {
    key for key, value in ALL_CRAFT_ITEMS.items() if value.get("programming")
}
GAMING_KEYS = {
    key for key, value in ALL_CRAFT_ITEMS.items() if value.get("gaming")
}
COUNTRY_KEYS = {
    key for key, value in ALL_CRAFT_ITEMS.items() if value.get("country")
}


async def check_and_unlock(
    db: Database,
    user_id: int,
    achievement_key: str,
    bot: Bot = None,
    chat_id: int = None,
) -> bool:
    """
    Check if user qualifies for achievement and unlock it.
    Returns True if newly unlocked.
    Optionally sends notification.
    """
    if achievement_key not in ACHIEVEMENTS:
        return False

    # Check if already has it
    if await db.has_achievement(user_id, achievement_key):
        return False

    # Unlock it
    unlocked = await db.unlock_achievement(user_id, achievement_key)

    if unlocked and bot and chat_id:
        info = ACHIEVEMENTS[achievement_key]
        user = await db.get_user(user_id)
        user_name = (
            html.escape(user["first_name"]) if user else f"User {user_id}"
        )
        try:
            await bot.send_message(
                chat_id,
                f"🏆 <b>Achievement Unlocked!</b>\n\n"
                f"👤 <b>{user_name}</b>\n"
                f"{info['emoji']} <b>{info['name']}</b>\n"
                f"<i>{info['description']}</i>",
            )
        except Exception:
            logger.exception(
                "Failed sending achievement notification: user_id=%s key=%s chat_id=%s",
                user_id,
                achievement_key,
                chat_id,
            )

    return unlocked


async def check_all_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check all achievement categories for a user."""
    await check_marriage_achievements(db, user_id, bot, chat_id)
    await check_adoption_achievements(db, user_id, bot, chat_id)
    await check_family_achievements(db, user_id, bot, chat_id)
    await check_friend_achievements(db, user_id, bot, chat_id)
    await check_money_achievements(db, user_id, bot, chat_id)
    await check_factory_achievements(db, user_id, bot, chat_id)
    await check_garden_achievements(db, user_id, bot, chat_id)
    await check_fishing_achievements(db, user_id, bot=bot, chat_id=chat_id)
    await check_gambling_achievements(db, user_id, bot, chat_id)
    await check_craft_achievements(db, user_id, bot, chat_id)


async def _get_user_crafts(db: Database, user_id: int) -> set[str]:
    """Get user's unlocked craft keys."""
    row = await db.fetchrow(
        "SELECT crafts FROM unlocked_crafts WHERE user_id = $1", user_id
    )
    if not row or not row["crafts"]:
        return set()

    crafts = row["crafts"]
    if isinstance(crafts, str):
        try:
            crafts = json.loads(crafts)
        except json.JSONDecodeError:
            return set()
    return set(crafts)


async def check_craft_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check alchemy craft achievements."""
    unlocked = await _get_user_crafts(db, user_id)
    if not unlocked:
        return

    myth_mon_count = len(unlocked & MYTH_OR_MONSTER_KEYS)
    if MYTH_OR_MONSTER_KEYS and myth_mon_count >= len(MYTH_OR_MONSTER_KEYS):
        await check_and_unlock(
            db, user_id, "craft_myth_mon_master", bot, chat_id
        )

    official_count = len(unlocked & OFFICIAL_CRAFT_KEYS)
    if OFFICIAL_CRAFT_KEYS and official_count >= len(OFFICIAL_CRAFT_KEYS):
        await check_and_unlock(
            db, user_id, "craft_official_master", bot, chat_id
        )

    extras_count = len(unlocked & EXTRAS_CRAFT_KEYS)
    extras_thresholds = [
        (500, "craft_extras_500"),
        (1000, "craft_extras_1000"),
        (1500, "craft_extras_1500"),
        (2000, "craft_extras_2000"),
        (2500, "craft_extras_2500"),
        (3000, "craft_extras_3000"),
    ]
    for threshold, key in extras_thresholds:
        if extras_count >= threshold:
            await check_and_unlock(db, user_id, key, bot, chat_id)
    if EXTRAS_CRAFT_KEYS and extras_count >= len(EXTRAS_CRAFT_KEYS):
        await check_and_unlock(db, user_id, "craft_extras_master", bot, chat_id)

    programming_count = len(unlocked & PROGRAMMING_KEYS)
    if programming_count >= 1:
        await check_and_unlock(db, user_id, "craft_prog_first", bot, chat_id)
    if PROGRAMMING_KEYS and programming_count >= len(PROGRAMMING_KEYS):
        await check_and_unlock(db, user_id, "craft_prog_master", bot, chat_id)

    gaming_count = len(unlocked & GAMING_KEYS)
    if gaming_count >= 1:
        await check_and_unlock(db, user_id, "craft_gaming_first", bot, chat_id)
    if gaming_count >= 50:
        await check_and_unlock(db, user_id, "craft_gaming_50", bot, chat_id)
    if gaming_count >= 100:
        await check_and_unlock(db, user_id, "craft_gaming_100", bot, chat_id)
    if GAMING_KEYS and gaming_count >= len(GAMING_KEYS):
        await check_and_unlock(db, user_id, "craft_gaming_master", bot, chat_id)

    country_count = len(unlocked & COUNTRY_KEYS)
    if country_count >= 1:
        await check_and_unlock(db, user_id, "craft_country_first", bot, chat_id)
    if country_count >= 50:
        await check_and_unlock(db, user_id, "craft_country_50", bot, chat_id)
    if country_count >= 100:
        await check_and_unlock(db, user_id, "craft_country_100", bot, chat_id)
    if country_count >= 150:
        await check_and_unlock(db, user_id, "craft_country_150", bot, chat_id)
    if COUNTRY_KEYS and country_count >= len(COUNTRY_KEYS):
        await check_and_unlock(
            db, user_id, "craft_country_master", bot, chat_id
        )


async def check_marriage_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check marriage-related achievements."""
    # first_marriage - when user gets married (check marriage history, not current spouses)
    marriage_count = await db.fetchval(
        """
        SELECT COUNT(*) FROM marriages
        WHERE user1_id = $1 OR user2_id = $1
        """,
        user_id,
    )
    if marriage_count >= 1:
        await check_and_unlock(db, user_id, "first_marriage", bot, chat_id)


async def check_adoption_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check adoption-related achievements."""
    # first_child - when user adopts someone (check family_relationships where user is parent)
    children = await db.fetchval(
        "SELECT COUNT(*) FROM family_relationships WHERE parent_id = $1",
        user_id,
    )
    if children >= 1:
        await check_and_unlock(db, user_id, "first_child", bot, chat_id)


async def check_family_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check family size achievements."""
    # big_family - 10 family members (parents + children + siblings + spouses)
    family = await db.get_close_family(user_id)
    total_family = (
        len(family.get("parents", []))
        + len(family.get("children", []))
        + len(family.get("siblings", []))
        + len(family.get("spouses", []))
    )
    if total_family >= 10:
        await check_and_unlock(db, user_id, "big_family", bot, chat_id)


async def check_friend_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check friend-related achievements."""
    friend_count = await db.fetchval(
        """
        SELECT COUNT(*) FROM friendships
        WHERE user1_id = $1 OR user2_id = $1
        """,
        user_id,
    )

    if friend_count >= 10:
        await check_and_unlock(db, user_id, "friendly", bot, chat_id)
    if friend_count >= 25:
        await check_and_unlock(db, user_id, "social_butterfly", bot, chat_id)
    if friend_count >= 50:
        await check_and_unlock(db, user_id, "popular", bot, chat_id)
    if friend_count >= 70:
        await check_and_unlock(db, user_id, "influencer", bot, chat_id)
    if friend_count >= 100:
        await check_and_unlock(db, user_id, "crowd_legend", bot, chat_id)


async def check_money_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check money-related achievements."""
    wallet = await db.get_wallet(user_id)

    # first_100k - Earn $100,000 total (total_earned in wallet)
    if wallet["total_earned"] >= 100000:
        await check_and_unlock(db, user_id, "first_100k", bot, chat_id)

    # millionaire - Have $1,000,000 balance (wallet + bank)
    bank = await db.get_bank_balance(user_id)
    bank_balance = bank["balance"] if bank else 0
    total_balance = wallet["balance"] + bank_balance
    if total_balance >= 1000000:
        await check_and_unlock(db, user_id, "millionaire", bot, chat_id)


async def check_factory_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check factory-related achievements."""
    factories = await db.fetch(
        "SELECT * FROM factories WHERE owner_id = $1", user_id
    )

    # factory_owner - Open your first factory
    if factories:
        await check_and_unlock(db, user_id, "factory_owner", bot, chat_id)

    # factory_tycoon - Earn $100,000 from factory (sum of total_earnings across all factories)
    total_earnings = (
        sum(f["total_earnings"] for f in factories) if factories else 0
    )
    if total_earnings >= 100000:
        await check_and_unlock(db, user_id, "factory_tycoon", bot, chat_id)


async def check_garden_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check garden-related achievements."""
    garden = await db.fetchrow(
        "SELECT * FROM gardens WHERE owner_id = $1", user_id
    )

    # master_farmer - Expand garden to 10x10
    if garden and garden["size"] >= 10:
        await check_and_unlock(db, user_id, "master_farmer", bot, chat_id)

    # green_thumb - Harvest 100 crops (cumulative harvested units)
    harvest_count = garden["total_harvests"] if garden else 0
    if harvest_count >= 100:
        await check_and_unlock(db, user_id, "green_thumb", bot, chat_id)


async def check_fishing_achievements(
    db: Database,
    user_id: int,
    caught_fish: str = None,
    bot: Bot = None,
    chat_id: int = None,
):
    """Check fishing-related achievements."""
    stats = await db.get_fishing_stats(user_id)

    # first_catch - Catch your first fish (total_caught >= 1)
    if stats and stats.get("total_caught", 0) >= 1:
        await check_and_unlock(db, user_id, "first_catch", bot, chat_id)

    # legendary_fisher - Catch a Kraken
    if caught_fish and caught_fish.lower() == "kraken":
        await check_and_unlock(db, user_id, "legendary_fisher", bot, chat_id)


async def check_gambling_achievements(
    db: Database, user_id: int, bot: Bot = None, chat_id: int = None
):
    """Check gambling-related achievements."""
    # Check gambling_stats for total won
    stats = await db.fetch(
        "SELECT * FROM gambling_stats WHERE user_id = $1", user_id
    )

    total_won = sum(s["total_won"] for s in stats) if stats else 0

    # high_roller - Win $50,000 from gambling
    if total_won >= 50000:
        await check_and_unlock(db, user_id, "high_roller", bot, chat_id)

    # lucky_winner - Win 10 gambling games (count games where total_won > 0 per game type)
    # Since gambling_stats tracks aggregate per game_type, count wins from ripple_games history
    ripple_wins = await db.fetchval(
        """
        SELECT COUNT(*) FROM ripple_games
        WHERE user_id = $1 AND current_prize > bet_amount
        """,
        user_id,
    )
    rbet_wins = await db.fetchval(
        """
        SELECT COUNT(*) FROM rbet_games
        WHERE user_id = $1 AND current_prize > bet_amount
        """,
        user_id,
    )
    lottery_wins = await db.fetchval(
        """
        SELECT COUNT(*) FROM lotteries
        WHERE $1 = ANY(participants) AND winner_id = $1
        """,
        user_id,
    )
    total_game_wins = (
        (ripple_wins or 0) + (rbet_wins or 0) + (lottery_wins or 0)
    )

    if total_game_wins >= 10:
        await check_and_unlock(db, user_id, "lucky_winner", bot, chat_id)


async def backfill_achievements(db: Database, bot: Bot = None):
    """
    Backfill achievements for all existing users based on their data.
    Call this once to retroactively unlock achievements.
    """
    # Get all users
    users = await db.fetch("SELECT user_id FROM users")

    for user_row in users:
        user_id = user_row["user_id"]

        # Check all achievement types (no notifications for backfill)
        await check_all_achievements(db, user_id, bot=bot)

    return len(users)
