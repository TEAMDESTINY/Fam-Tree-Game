"""Alchemy crafting system using LittleAlchemy2 data."""

import json
import logging
import math
import re
from pathlib import Path

from pyrogram import Client as Bot
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

from bot.command_registry import reg
from bot.database import Database
from bot.queue_it import queue_it
from pyrogram import filters
from bot.client import client
from bot.database import db

logger = logging.getLogger(__name__)


# Constants
CRAFT_ITEMS_PER_PAGE = 50
MISSING_COMBOS_PER_PAGE = 20
NORMAL_REWARD = 100_000
MYTHICAL_REWARD = 500_000
EXTRAS_REWARD = 150_000
COMBO_NEW_REWARD = 50_000
DEFAULT_CRAFTS = [
    "air",
    "earth",
    "fire",
    "water",
    "monster",
    "good",
    "evil",
    "immortality",
]
FANON_UNLOCK_AT_50 = ["Fanon:Left", "Fanon:Next", "Fanon:Right"]

# Load data on module import
BASE_DIR = Path(__file__).parent.parent.parent
DB_PATH = BASE_DIR / "assets" / "LittleAlchemy2" / "db.json"
EMOJI_PATH = BASE_DIR / "assets" / "LittleAlchemy2" / "tg_emoji_ids.json"
FANON_DB_PATH = BASE_DIR / "assets" / "LittleAlchemy2" / "fanon_db.json"
FANON_EMOJI_PATH = (
    BASE_DIR / "assets" / "LittleAlchemy2" / "fanon_tg_emoji_ids.json"
)


def _load_items(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        return json.load(f).get("items", {})


def _load_emoji_ids(path: Path) -> dict:
    if not path.exists():
        return {}
    with path.open() as f:
        data = json.load(f)
    return data if isinstance(data, dict) else {}


OFFICIAL_CRAFT_DATA = _load_items(DB_PATH)
FANON_CRAFT_DATA = _load_items(FANON_DB_PATH)
CRAFT_DATA = {**OFFICIAL_CRAFT_DATA, **FANON_CRAFT_DATA}
OFFICIAL_CRAFT_KEYS = set(OFFICIAL_CRAFT_DATA.keys())
EXTRAS_CRAFT_KEYS = {
    key
    for key, value in FANON_CRAFT_DATA.items()
    if key.lower().startswith("fanon:") or value.get("fanon")
}

EMOJI_IDS = {
    **_load_emoji_ids(EMOJI_PATH),
    **_load_emoji_ids(FANON_EMOJI_PATH),
}

TOTAL_ITEMS = len(CRAFT_DATA)
TOTAL_OFFICIAL_ITEMS = len(OFFICIAL_CRAFT_DATA)
TOTAL_EXTRAS_ITEMS = len(EXTRAS_CRAFT_KEYS)


def _strip_fanon_prefix(name: str) -> str:
    return re.sub(r"(?i)^fanon:\s*", "", name).strip()


def _normalize_lookup(name: str) -> str:
    """Normalize text for forgiving item lookup."""
    cleaned = _strip_fanon_prefix(name)
    cleaned = cleaned.replace("-", " ").replace("_", " ")
    cleaned = re.sub(r"[^a-zA-Z0-9\s]", "", cleaned).lower()
    return re.sub(r"\s+", " ", cleaned).strip()


def _build_lookup_map() -> dict[str, list[str]]:
    lookup_map: dict[str, list[str]] = {}
    for key in CRAFT_DATA:
        aliases = {key, _strip_fanon_prefix(key), key.replace("-", " ")}
        for alias in aliases:
            normalized = _normalize_lookup(alias)
            if not normalized:
                continue
            lookup_map.setdefault(normalized, []).append(key)

    for normalized, values in lookup_map.items():
        official = [v for v in values if v in OFFICIAL_CRAFT_KEYS]
        fanon = [v for v in values if v not in OFFICIAL_CRAFT_KEYS]
        ordered = official + fanon  # Prefer official on collisions
        deduped = []
        seen = set()
        for candidate in ordered:
            if candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
        lookup_map[normalized] = deduped
    return lookup_map


LOOKUP_MAP = _build_lookup_map()


def _build_combination_index() -> dict[tuple[str, str], list[str]]:
    index: dict[tuple[str, str], list[str]] = {}

    def resolve_combo_ingredient(raw_name: str) -> str | None:
        normalized = _normalize_lookup(raw_name)
        if not normalized:
            return None
        candidates = LOOKUP_MAP.get(normalized, [])
        return candidates[0] if candidates else None

    for slug, data in CRAFT_DATA.items():
        for combo in data.get("combinations", []):
            if len(combo) != 2:
                continue
            left = resolve_combo_ingredient(combo[0])
            right = resolve_combo_ingredient(combo[1])
            if not left or not right:
                continue
            key = tuple(sorted((left, right)))
            index.setdefault(key, []).append(slug)
    return index


COMBINATION_INDEX = _build_combination_index()


def make_combo_key(item1: str, item2: str) -> str:
    """Create an order-insensitive combo key."""
    a, b = sorted((item1, item2))
    return f"{a}|{b}"


def _build_item_combo_index() -> dict[str, list[tuple[str, str, str]]]:
    """
    Build per-item combo index:
    item_slug -> [(combo_key, left_slug, right_slug), ...]
    """
    index: dict[str, list[tuple[str, str, str]]] = {}
    for item_slug, data in CRAFT_DATA.items():
        seen_keys = set()
        for combo in data.get("combinations", []):
            if len(combo) != 2:
                continue
            left = resolve_item_name(combo[0])
            right = resolve_item_name(combo[1])
            if not left or not right:
                continue
            combo_key = make_combo_key(left, right)
            if combo_key in seen_keys:
                continue
            seen_keys.add(combo_key)
            index.setdefault(item_slug, []).append((combo_key, left, right))
    return index


def resolve_item_name(raw_name: str) -> str | None:
    """Resolve user input to canonical craft key, preferring official items."""
    normalized = _normalize_lookup(raw_name)
    if not normalized:
        return None
    candidates = LOOKUP_MAP.get(normalized, [])
    return candidates[0] if candidates else None


ITEM_COMBO_INDEX = _build_item_combo_index()


def display_name(slug: str) -> str:
    """Convert db key to display format: capitalize each word."""
    if slug.lower().startswith("fanon:"):
        return _strip_fanon_prefix(slug)
    return slug.replace("-", " ").title()


def is_extras(slug: str) -> bool:
    """Return whether a craft belongs to EXTRAS (fanon/community)."""
    return slug in EXTRAS_CRAFT_KEYS or CRAFT_DATA.get(slug, {}).get(
        "fanon", False
    )


def item_labels(slug: str) -> str:
    """Build user-facing label suffixes for item category badges."""
    labels = []
    if CRAFT_DATA.get(slug, {}).get("myths", False):
        labels.append("🌟")
    return f" {' '.join(labels)}" if labels else ""


def item_category(slug: str) -> str:
    """Get category label text for list formatting."""
    categories = []
    info = CRAFT_DATA.get(slug, {})
    if info.get("myths", False):
        categories.append("MYTHICAL")
    if info.get("monster", False):
        categories.append("MONSTER")
    if is_extras(slug):
        categories.append("🧸🛍️")
    if not categories:
        categories.append("NORMAL")
    return ", ".join(categories)


def emoji_tag(slug: str) -> str:
    """Return Telegram custom emoji tag for an item."""
    emoji = CRAFT_DATA.get(slug, {})
    # Get the base emoji from the image URL or use a default
    base_emoji = "🧪"
    emoji_id = EMOJI_IDS.get(slug)
    if emoji_id:
        return f'<tg-emoji emoji-id="{emoji_id}">{base_emoji}</tg-emoji>'
    return base_emoji


def find_combination(item1: str, item2: str) -> list[str]:
    """Find what two items make. Returns a list of all possible result slugs."""
    return COMBINATION_INDEX.get(tuple(sorted((item1, item2))), [])


async def get_user_crafts(db: Database, user_id: int) -> list[str]:
    """Get user's unlocked crafts from database."""
    row = await db.fetchrow(
        "SELECT crafts FROM unlocked_crafts WHERE user_id = $1", user_id
    )
    if row and row["crafts"]:
        crafts = row["crafts"]
        # asyncpg may return JSONB as string, parse it
        if isinstance(crafts, str):
            return json.loads(crafts)
        return crafts
    # If no row exists, create one with defaults
    await db.upsert_user(user_id)
    await db.execute(
        """
        INSERT INTO unlocked_crafts (user_id, crafts, combos)
        VALUES ($1, $2, $3)
        ON CONFLICT (user_id) DO NOTHING
        """,
        user_id,
        json.dumps(DEFAULT_CRAFTS),
        json.dumps([]),
    )
    return list(DEFAULT_CRAFTS)


async def get_user_combo_keys(db: Database, user_id: int) -> set[str]:
    """Get user's discovered combo keys."""
    row = await db.fetchrow(
        "SELECT combos FROM unlocked_crafts WHERE user_id = $1", user_id
    )
    if row and row.get("combos") is not None:
        combos = row["combos"]
        if isinstance(combos, str):
            try:
                combos = json.loads(combos)
            except json.JSONDecodeError:
                combos = []
        return set(combos or [])

    # Ensure row exists for new users.
    await get_user_crafts(db, user_id)
    return set()


async def save_user_combo_keys(
    db: Database, user_id: int, combo_keys: set[str]
):
    """Persist user's combo key history."""
    await db.upsert_user(user_id)
    combos_sorted = sorted(combo_keys)
    await db.execute(
        """
        INSERT INTO unlocked_crafts (user_id, combos, updated_at)
        VALUES ($1, $2, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id) DO UPDATE SET combos = $2, updated_at = CURRENT_TIMESTAMP
        """,
        user_id,
        json.dumps(combos_sorted),
    )


async def unlock_craft(
    db: Database, user_id: int, craft: str
) -> tuple[bool, bool]:
    """Add a craft to user's unlocked list.
    Returns (was_newly_unlocked, time_auto_unlocked).
    """
    await db.upsert_user(user_id)
    current = await get_user_crafts(db, user_id)
    was_new = craft not in current
    if was_new:
        current.append(craft)

    # Auto-unlock special fanon items at 50 unlocked elements
    if len(current) >= 50:
        for special in FANON_UNLOCK_AT_50:
            if special in CRAFT_DATA and special not in current:
                current.append(special)

    # Auto-unlock "time" when user reaches 100 crafts
    time_unlocked = len(current) >= 100 and "time" not in current
    if time_unlocked:
        current.append("time")

    await db.execute(
        """
        INSERT INTO unlocked_crafts (user_id, crafts, updated_at)
        VALUES ($1, $2, CURRENT_TIMESTAMP)
        ON CONFLICT (user_id) DO UPDATE SET crafts = $2, updated_at = CURRENT_TIMESTAMP
        """,
        user_id,
        json.dumps(current),
    )
    return was_new, time_unlocked


reg("craft", "🧪 Alchemy crafting game")
reg("missing_crafts", "🔍 List undiscovered craft elements")
reg("missing_combos", "🔎 List undiscovered combo recipes")
reg("guide_craft", "📖 How to play Alchemy Crafts")


@client.on_message(filters.command(["craft", "c"]))
async def craft_command(message: Message, bot: Bot):
    """Handle /craft command - show unlocked items or craft combination."""
    text = message.text or ""
    raw_args = (
        text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""
    )

    if not raw_args:
        await _show_craft_list(message, db)
        return

    has_separator = "+" in raw_args or "," in raw_args
    tokens = raw_args.split()

    # Single token with no separator → page number or prefix search, never a craft
    if not has_separator and len(tokens) == 1:
        arg = tokens[0]
        if arg.isdigit():
            await _show_craft_list(message, db, page=int(arg))
        else:
            await _show_craft_list(message, db, prefix=arg.lower())
        return

    # Two+ tokens or explicit separator → craft attempt
    await _try_craft(message, db, text, bot)


def _build_craft_list(
    unlocked_slugs: list,
    page: int,
    user_id: int,
    prefix: str | None = None,
) -> tuple[str | None, InlineKeyboardMarkup | None]:
    """Return (text, keyboard) for the crafted-items list or a prefix filter.

    Returns (None, None) when a prefix filter yields zero matches.
    """
    unlocked_sorted = sorted(
        unlocked_slugs, key=lambda x: display_name(x).lower()
    )

    if prefix:
        filtered = [
            s
            for s in unlocked_sorted
            if display_name(s).lower().startswith(prefix)
        ]
        if not filtered:
            return None, None

        items_text = ""
        for i, slug in enumerate(filtered, 1):
            items_text += f"{i}. {display_name(slug)} {emoji_tag(slug)} [{item_category(slug)}]\n\n"

        full_text = (
            f'🧪 <b>Crafted — "{prefix.upper()}"</b>\n\n'
            f"<blockquote expandable>{items_text}</blockquote>"
            f"\n{len(filtered)} match(es) · {len(unlocked_sorted)} total crafted"
            f"\n💡 <i>To craft: <code>/craft item1 + item2</code></i>"
        )
        return full_text, None

    total_pages = max(1, math.ceil(len(unlocked_sorted) / CRAFT_ITEMS_PER_PAGE))
    page = max(1, min(page, total_pages))
    start_idx = (page - 1) * CRAFT_ITEMS_PER_PAGE
    page_items = unlocked_sorted[start_idx : start_idx + CRAFT_ITEMS_PER_PAGE]

    items_text = ""
    for i, slug in enumerate(page_items, start_idx + 1):
        items_text += f"{i}. {display_name(slug)} {emoji_tag(slug)} [{item_category(slug)}]\n\n"

    header = "🧪 <b>Alchemy Crafts</b>\n\n"
    header += f"You have those unlocked (Page {page}/{total_pages} | Displaying {len(page_items)} items):\n"

    extras_unlocked = len(set(unlocked_sorted) & EXTRAS_CRAFT_KEYS)
    footer = (
        f"\nTotal unlocked : {len(unlocked_sorted)}/{TOTAL_ITEMS}"
        f"\nOfficial: {len(set(unlocked_sorted) & OFFICIAL_CRAFT_KEYS)}/{TOTAL_OFFICIAL_ITEMS}"
        f" | EXTRAS: {extras_unlocked}/{TOTAL_EXTRAS_ITEMS}"
    )
    full_text = (
        header + f"<blockquote expandable>{items_text}</blockquote>" + footer
    )

    keyboard = None
    if total_pages > 1:
        keyboard_buttons = []
        if page > 1:
            keyboard_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Prev",
                    callback_data=f"craft_page:{user_id}:{page - 1}",
                )
            )
        if page < total_pages:
            keyboard_buttons.append(
                InlineKeyboardButton(
                    text="Next ➡️",
                    callback_data=f"craft_page:{user_id}:{page + 1}",
                )
            )
        if keyboard_buttons:
            keyboard = InlineKeyboardMarkup(inline_keyboard=[keyboard_buttons])

    return full_text, keyboard


async def _show_craft_list(
    message: Message, db: Database, page: int = 1, prefix: str | None = None
):
    """Reply with the paginated crafted list or a prefix-filtered result."""
    unlocked = await get_user_crafts(db, message.from_user.id)
    full_text, keyboard = _build_craft_list(
        unlocked, page, message.from_user.id, prefix
    )

    if full_text is None:
        note = "\n💡 <i>To craft: <code>/craft item1 + item2</code></i>"
        await message.reply(
            f"✅ No crafted items starting with <b>{prefix.upper()}</b>.{note}"
        )
        return

    if keyboard:
        await message.reply(full_text, reply_markup=keyboard)
    else:
        await message.reply(full_text)


async def _try_craft(message: Message, db: Database, text: str, bot: Bot):
    """Try to craft a combination."""
    # Parse:
    # 1) /craft item1 + item2
    # 2) /craft item1, item2
    # 3) /craft item1 item2   (no separator; split by best word boundary)
    # Remove /craft prefix
    craft_text = text.split(None, 1)[1] if len(text.split(None, 1)) > 1 else ""

    def is_valid_format(craft_text: str) -> bool:
        separators = ["+", ","]

        if not any(sep in craft_text for sep in separators):
            return True

        for sep in separators:
            parts = craft_text.split(sep)
            parts = [part.strip() for part in parts if part.strip()]
            if len(parts) == 2:
                return True
        return False

    if not is_valid_format(craft_text):
        await message.reply(
            "❌ Invalid format!\n\n"
            "Usage: <code>/craft item1 + item2</code> or <code>/craft item1, item2</code> or <code>/craft item1 item2</code>\n"
            "Example: <code>/craft water + fire</code> or <code>/craft water, fire</code> or <code>/craft programming language c</code>\n\n"
            "Use /guide_craft to learn how to play!"
        )
        return

    def parse_items(raw_text: str) -> tuple[str, str] | None:
        # Prefer explicit separators first
        if "+" in raw_text:
            left, right = raw_text.split("+", 1)
            left, right = left.strip(), right.strip()
            return (left, right) if left and right else None
        if "," in raw_text:
            left, right = raw_text.split(",", 1)
            left, right = left.strip(), right.strip()
            return (left, right) if left and right else None

        # Fallback: try splitting words from right-to-left so
        # "/craft programming language c" becomes
        # "programming language" + "c" where possible.
        words = raw_text.split()
        if len(words) < 2:
            return None
        for i in range(len(words) - 1, 0, -1):
            left = " ".join(words[:i]).strip()
            right = " ".join(words[i:]).strip()
            if not left or not right:
                continue
            if resolve_item_name(left) and resolve_item_name(right):
                return left, right
        return None

    parsed_items = parse_items(craft_text)

    if not parsed_items:
        await message.reply(
            '❌ One of two item doesn\'t exist or you did typo (without separator "," or "+" its tricky to detect.'
        )
        return

    item1_raw, item2_raw = parsed_items

    if not item1_raw or not item2_raw:
        await message.reply("❌ Please specify two items to combine!")
        return

    item1 = resolve_item_name(item1_raw)
    item2 = resolve_item_name(item2_raw)

    # Check if items exist in game
    if not item1:
        display = _strip_fanon_prefix(item1_raw).strip() or item1_raw
        await message.reply(
            f"❌ Specified element {display} doesn't exist in game!!!"
        )
        return

    if not item2:
        display = _strip_fanon_prefix(item2_raw).strip() or item2_raw
        await message.reply(
            f"❌ Specified element {display} doesn't exist in game!!!"
        )
        return

    # Check if items are discovered by user
    unlocked = await get_user_crafts(db, message.from_user.id)

    if item1 not in unlocked:
        display = _strip_fanon_prefix(item1_raw).strip() or item1_raw
        emoji = emoji_tag(item1)
        await message.reply(
            f"❌ Specified element {display} {emoji} is not yet discovered!!"
        )
        return

    if item2 not in unlocked:
        display = _strip_fanon_prefix(item2_raw).strip() or item2_raw
        emoji = emoji_tag(item2)
        await message.reply(
            f"❌ Specified element {display} {emoji} is not yet discovered!!"
        )
        return

    # Try to find combination
    results = find_combination(item1, item2)

    if not results:
        # No valid combination
        name1 = display_name(item1)
        name2 = display_name(item2)
        emoji1 = emoji_tag(item1)
        emoji2 = emoji_tag(item2)

        await message.reply(
            f"🧪 <b>You tried experiment:</b>\n\n"
            f"{name1} {emoji1} + {name2} {emoji2}\n\n"
            f"❌ Nothing happened! These elements don't combine."
        )
        return

    combo_key = make_combo_key(item1, item2)
    combo_keys = await get_user_combo_keys(db, message.from_user.id)
    is_new_combo = combo_key not in combo_keys

    # Found combination(s)! Check for undiscovered results
    name1 = display_name(item1)
    name2 = display_name(item2)
    emoji1 = emoji_tag(item1)
    emoji2 = emoji_tag(item2)

    # Find undiscovered results
    new_discoveries = [r for r in results if r not in unlocked]

    combo_bonus = 0
    if is_new_combo:
        combo_keys.add(combo_key)
        await save_user_combo_keys(db, message.from_user.id, combo_keys)
        # Don't stack combo bonus on top of first-time craft discovery reward.
        if not new_discoveries:
            combo_bonus = COMBO_NEW_REWARD
            await db.add_balance(
                message.from_user.id,
                combo_bonus,
                f"Craft combo discovery: {combo_key}",
            )

    if not new_discoveries:
        # User already has all possible results
        result_names = ", ".join(
            display_name(r) + " " + emoji_tag(r) for r in results
        )
        bonus_line = (
            f"\n\n🧩 <b>New Combo Bonus:</b> +${combo_bonus:,}"
            if combo_bonus
            else ""
        )
        await message.reply(
            f"🧪 <b>You tried experiment:</b>\n\n"
            f"{name1} {emoji1} + {name2} {emoji2}\n\n"
            f"😐 You found that you wasted your energy on this...\n"
            f"You already have all results: {result_names}"
            f"{bonus_line}"
        )
        return

    # Auto-craft ALL undiscovered results together
    total_reward = 0
    discovery_messages = []
    milestone_reached = False
    time_unlocked = False

    for result in new_discoveries:
        result_name = display_name(result)
        result_emoji = emoji_tag(result)
        is_mythical = CRAFT_DATA.get(result, {}).get("myths", False)
        is_extra = is_extras(result)

        # Unlock the craft
        was_new, time_auto_unlocked = await unlock_craft(
            db, message.from_user.id, result
        )

        if was_new:
            reward = (
                EXTRAS_REWARD
                if is_extra
                else (MYTHICAL_REWARD if is_mythical else NORMAL_REWARD)
            )
            await db.add_balance(
                message.from_user.id, reward, f"Craft discovery: {result}"
            )
            total_reward += reward

            mythical_label = "🌟 <b>MYTHICAL!</b> " if is_mythical else ""
            extras_label = "🧸🛍️ <b>EXTRAS!</b> " if is_extra else ""
            discovery_messages.append(
                f"{mythical_label}{extras_label}{result_emoji} <b>{result_name}</b> (+${reward:,})"
            )

            if time_auto_unlocked:
                milestone_reached = True
                time_unlocked = True

    # Build discovery message
    discoveries_text = "\n".join(discovery_messages)

    message_text = (
        f"🧪 <b>You tried experiment:</b>\n\n"
        f"{name1} {emoji1} + {name2} {emoji2}\n\n"
        f"🎉 <b>DISCOVERIES!</b>\n{discoveries_text}\n\n"
        f"💰 Total Reward: ${total_reward:,}"
    )
    if combo_bonus:
        message_text += f"\n🧩 <b>New Combo Bonus:</b> +${combo_bonus:,}"

    if milestone_reached:
        message_text += f"\n\n🎊 <b>MILESTONE REACHED!</b>\nYou've unlocked 100 elements!\nSecret element discovered: Time {emoji_tag('time')} ⏰"

    from bot.achievements import (
        check_craft_achievements,
        check_money_achievements,
    )

    await check_craft_achievements(
        db, message.from_user.id, bot=bot, chat_id=message.chat.id
    )
    await check_money_achievements(
        db, message.from_user.id, bot=bot, chat_id=message.chat.id
    )

    await message.reply(message_text)


@client.on_message(filters.command(["missing_crafts"]))
async def missing_crafts_command(
    message: Message,
):
    """Handle /missing_crafts command - show undiscovered crafts list.

    Usage:
      /missing_crafts          — all missing, paginated
      /missing_crafts 2        — page 2
      /missing_crafts g        — all missing whose name starts with 'g'
      /missing_crafts gr       — all missing whose name starts with 'gr'
    """
    raw_parts = (message.text or "").split()
    prefix_filter: str | None = None
    page = 1

    if len(raw_parts) >= 2:
        arg = raw_parts[1]
        if arg.isdigit():
            page = int(arg)
        else:
            prefix_filter = arg.lower()

    unlocked = await get_user_crafts(db, message.from_user.id)
    all_slugs = set(CRAFT_DATA.keys())
    missing = sorted(
        all_slugs - set(unlocked), key=lambda x: display_name(x).lower()
    )

    if not missing:
        await message.reply(
            "🎉 Congratulations! You've discovered all elements!"
        )
        return

    if prefix_filter:
        filtered = [
            s
            for s in missing
            if display_name(s).lower().startswith(prefix_filter)
        ]
        if not filtered:
            await message.reply(
                f"✅ No missing crafts starting with <b>{prefix_filter.upper()}</b>."
            )
            return

        items_text = ""
        for i, slug in enumerate(filtered, 1):
            items_text += f"{i}. {display_name(slug)} {emoji_tag(slug)} [{item_category(slug)}]\n\n"

        full_text = (
            f'🔍 <b>Missing Crafts — "{prefix_filter.upper()}"</b>\n\n'
            f"<blockquote expandable>{items_text}</blockquote>"
            f"\n{len(filtered)} match(es) · {len(missing)} total missing"
        )
        await message.reply(full_text)
        return

    # ── Paginated full list ────────────────────────────────────────────────
    total_pages = max(1, math.ceil(len(missing) / CRAFT_ITEMS_PER_PAGE))
    page = max(1, min(page, total_pages))

    start_idx = (page - 1) * CRAFT_ITEMS_PER_PAGE
    page_items = missing[start_idx : start_idx + CRAFT_ITEMS_PER_PAGE]

    items_text = ""
    for i, slug in enumerate(page_items, start_idx + 1):
        items_text += f"{i}. {display_name(slug)} {emoji_tag(slug)} [{item_category(slug)}]\n\n"

    header = "🔍 <b>Missing Crafts</b>\n\n"
    header += f"You still need to find (Page {page}/{total_pages} | Displaying {len(page_items)} items):\n"
    footer = (
        f"\nTotal missing: {len(missing)} | Found: {len(unlocked)}/{TOTAL_ITEMS}"
        f"\nOfficial total: {TOTAL_OFFICIAL_ITEMS} | EXTRAS total: {TOTAL_EXTRAS_ITEMS}"
    )
    full_text = (
        header + f"<blockquote expandable>{items_text}</blockquote>" + footer
    )

    if total_pages > 1:
        user_id = message.from_user.id
        keyboard_buttons = []
        if page > 1:
            keyboard_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Prev",
                    callback_data=f"missing_craft_page:{user_id}:{page - 1}",
                )
            )
        if page < total_pages:
            keyboard_buttons.append(
                InlineKeyboardButton(
                    text="Next ➡️",
                    callback_data=f"missing_craft_page:{user_id}:{page + 1}",
                )
            )
        if keyboard_buttons:
            await message.reply(
                full_text,
                reply_markup=InlineKeyboardMarkup(
                    inline_keyboard=[keyboard_buttons]
                ),
            )
            return

    await message.reply(full_text)


@client.on_message(filters.command(["missing_combos"]))
async def missing_combos_command(
    message: Message,
):
    """Show missing combo recipes for unlocked crafts."""
    text = message.text or ""
    parts = text.split()
    page = 1
    if len(parts) >= 2 and parts[1].isdigit():
        page = int(parts[1])

    user_id = message.from_user.id
    unlocked = set(await get_user_crafts(db, user_id))
    combo_keys = await get_user_combo_keys(db, user_id)

    all_combo_keys = {
        make_combo_key(left, right) for left, right in COMBINATION_INDEX.keys()
    }
    discovered_global = len(combo_keys & all_combo_keys)
    total_global = len(all_combo_keys)
    missing_global = max(total_global - discovered_global, 0)

    rows: list[tuple[str, int, int, list[tuple[str, str]]]] = []
    for item_slug in sorted(unlocked, key=lambda x: x.lower()):
        combos = ITEM_COMBO_INDEX.get(item_slug, [])
        if not combos:
            continue

        discovered = [
            (left, right) for key, left, right in combos if key in combo_keys
        ]
        total = len(combos)
        missing = total - len(discovered)
        if missing <= 0:
            continue

        rows.append((item_slug, missing, total, discovered))

    if not rows:
        await message.reply(
            f"🎉 <b>No missing combos!</b>\n\n"
            f"Combo Progress: {discovered_global}/{total_global}"
        )
        return

    # Prioritize entries with more missing recipes, then deterministic name order.
    rows.sort(key=lambda x: (-x[1], display_name(x[0]).lower()))

    total_pages = max(1, math.ceil(len(rows) / MISSING_COMBOS_PER_PAGE))
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    start_idx = (page - 1) * MISSING_COMBOS_PER_PAGE
    end_idx = start_idx + MISSING_COMBOS_PER_PAGE
    page_rows = rows[start_idx:end_idx]

    blocks = []
    for i, (item_slug, missing, total, discovered) in enumerate(
        page_rows, start_idx + 1
    ):
        item_name = display_name(item_slug)
        item_emoji = emoji_tag(item_slug)
        lines = [f"{i}. {item_name} {item_emoji} ({len(discovered)}/{total})"]
        for left, right in discovered:
            lines.append(f"   - {display_name(left)} + {display_name(right)}")
        lines.append(f"   - missing x{missing}")
        blocks.append("\n".join(lines))

    formatted_rows = "\n\n".join(blocks)

    response = (
        "🔎 <b>Missing Combos</b>\n\n"
        f"Page {page}/{total_pages} | Showing {len(page_rows)} entries\n"
        f"🧩 Combo Progress: {discovered_global}/{total_global}\n"
        f"❓ Missing globally: {missing_global}\n\n"
        f"<blockquote expandable>{formatted_rows}</blockquote>"
    )
    if total_pages > 1:
        response += "\n\nUse buttons to change page."

    if total_pages > 1:
        keyboard_buttons = []
        if page > 1:
            keyboard_buttons.append(
                InlineKeyboardButton(
                    text="⬅️ Prev",
                    callback_data=f"missing_combo_page:{user_id}:{page - 1}",
                )
            )
        if page < total_pages:
            keyboard_buttons.append(
                InlineKeyboardButton(
                    text="Next ➡️",
                    callback_data=f"missing_combo_page:{user_id}:{page + 1}",
                )
            )
        keyboard = InlineKeyboardMarkup(inline_keyboard=[keyboard_buttons])
        await message.reply(response, reply_markup=keyboard)
        return

    await message.reply(response)


@client.on_callback_query(filters.regex(r"^" + "craft_page:"))
async def craft_page_callback(
    callback,
):
    """Handle pagination for craft list."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        return

    callback_user_id = int(parts[1])
    page = int(parts[2])

    if callback.from_user.id != callback_user_id:
        await callback.answer(
            "❌ This is not your craft list!", show_alert=True
        )
        return

    unlocked = await get_user_crafts(db, callback.from_user.id)
    full_text, keyboard = _build_craft_list(
        unlocked, page, callback.from_user.id
    )

    await queue_it(
        lambda: callback.message.edit_text(full_text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "missing_craft_page:"))
async def missing_craft_page_callback(
    callback,
):
    """Handle pagination for missing crafts list."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        return

    callback_user_id = int(parts[1])
    page = int(parts[2])

    # Only allow the user who sent /missing_crafts to paginate
    if callback.from_user.id != callback_user_id:
        await callback.answer(
            "❌ This is not your missing crafts list!", show_alert=True
        )
        return

    unlocked = await get_user_crafts(db, callback.from_user.id)
    all_slugs = set(CRAFT_DATA.keys())
    unlocked_set = set(unlocked)
    missing = sorted(all_slugs - unlocked_set, key=lambda x: x.lower())

    if not missing:
        await callback.answer(
            "🎉 You've discovered all elements!", show_alert=True
        )
        return

    total_pages = max(1, math.ceil(len(missing) / CRAFT_ITEMS_PER_PAGE))
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    start_idx = (page - 1) * CRAFT_ITEMS_PER_PAGE
    end_idx = start_idx + CRAFT_ITEMS_PER_PAGE
    page_items = missing[start_idx:end_idx]

    # Build the list
    items_text = ""
    for i, slug in enumerate(page_items, start_idx + 1):
        name = display_name(slug)
        emoji = emoji_tag(slug)
        items_text += f"{i}. {name} {emoji} [{item_category(slug)}]\n\n"

    header = "🔍 <b>Missing Crafts</b>\n\n"
    header += f"You still need to find (Page {page}/{total_pages} | Displaying {len(page_items)} items):\n"

    footer = (
        f"\nTotal missing: {len(missing)} | Found: {len(unlocked)}/{TOTAL_ITEMS}"
        f"\nOfficial total: {TOTAL_OFFICIAL_ITEMS} | EXTRAS total: {TOTAL_EXTRAS_ITEMS}"
    )

    full_text = (
        header + f"<blockquote expandable>{items_text}</blockquote>" + footer
    )

    # Build keyboard
    keyboard_buttons = []
    user_id = callback.from_user.id
    if page > 1:
        keyboard_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Prev",
                callback_data=f"missing_craft_page:{user_id}:{page - 1}",
            )
        )
    if page < total_pages:
        keyboard_buttons.append(
            InlineKeyboardButton(
                text="Next ➡️",
                callback_data=f"missing_craft_page:{user_id}:{page + 1}",
            )
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[keyboard_buttons])

    await queue_it(
        lambda: callback.message.edit_text(full_text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "missing_combo_page:"))
async def missing_combo_page_callback(
    callback,
):
    """Handle pagination for missing combos list."""
    parts = callback.data.split(":")
    if len(parts) != 3:
        return

    callback_user_id = int(parts[1])
    page = int(parts[2])

    if callback.from_user.id != callback_user_id:
        await callback.answer(
            "❌ This is not your missing combos list!", show_alert=True
        )
        return

    unlocked = set(await get_user_crafts(db, callback.from_user.id))
    combo_keys = await get_user_combo_keys(db, callback.from_user.id)

    all_combo_keys = {
        make_combo_key(left, right) for left, right in COMBINATION_INDEX.keys()
    }
    discovered_global = len(combo_keys & all_combo_keys)
    total_global = len(all_combo_keys)
    missing_global = max(total_global - discovered_global, 0)

    rows: list[tuple[str, int, int, list[tuple[str, str]]]] = []
    for item_slug in sorted(unlocked, key=lambda x: x.lower()):
        combos = ITEM_COMBO_INDEX.get(item_slug, [])
        if not combos:
            continue
        discovered = [
            (left, right) for key, left, right in combos if key in combo_keys
        ]
        total = len(combos)
        missing = total - len(discovered)
        if missing <= 0:
            continue
        rows.append((item_slug, missing, total, discovered))

    if not rows:
        await callback.answer("🎉 No missing combos!", show_alert=True)
        return

    rows.sort(key=lambda x: (-x[1], display_name(x[0]).lower()))

    total_pages = max(1, math.ceil(len(rows) / MISSING_COMBOS_PER_PAGE))
    if page < 1:
        page = 1
    if page > total_pages:
        page = total_pages

    start_idx = (page - 1) * MISSING_COMBOS_PER_PAGE
    end_idx = start_idx + MISSING_COMBOS_PER_PAGE
    page_rows = rows[start_idx:end_idx]

    blocks = []
    for i, (item_slug, missing, total, discovered) in enumerate(
        page_rows, start_idx + 1
    ):
        item_name = display_name(item_slug)
        item_emoji = emoji_tag(item_slug)
        lines = [f"{i}. {item_name} {item_emoji} ({len(discovered)}/{total})"]
        for left, right in discovered:
            lines.append(f"   - {display_name(left)} + {display_name(right)}")
        lines.append(f"   - missing x{missing}")
        blocks.append("\n".join(lines))

    formatted_rows = "\n\n".join(blocks)
    text = (
        "🔎 <b>Missing Combos</b>\n\n"
        f"Page {page}/{total_pages} | Showing {len(page_rows)} entries\n"
        f"🧩 Combo Progress: {discovered_global}/{total_global}\n"
        f"❓ Missing globally: {missing_global}\n\n"
        f"<blockquote expandable>{formatted_rows}</blockquote>\n\n"
        "Use buttons to change page."
    )

    keyboard_buttons = []
    user_id = callback.from_user.id
    if page > 1:
        keyboard_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Prev",
                callback_data=f"missing_combo_page:{user_id}:{page - 1}",
            )
        )
    if page < total_pages:
        keyboard_buttons.append(
            InlineKeyboardButton(
                text="Next ➡️",
                callback_data=f"missing_combo_page:{user_id}:{page + 1}",
            )
        )

    keyboard = InlineKeyboardMarkup(inline_keyboard=[keyboard_buttons])
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


@client.on_message(filters.command(["guide_craft", "guide_crafts"]))
async def guide_craft_command(message: Message):
    """Show guide for the crafting system."""
    guide_text = (
        "🧪 <b>Alchemy Crafts Guide</b>\n\n"
        "<blockquote expandable>"
        "<b>How to Play:</b>\n"
        "• Use <code>/craft element1 + element2</code>, <code>/craft element1, element2</code>, or <code>/craft element1 element2</code> to combine elements\n"
        f"• Example: <code>/craft water + fire</code> creates Steam {emoji_tag('steam')}!\n"
        f"• You start with 8 elements: Air {emoji_tag('air')}, Earth {emoji_tag('earth')}, Fire {emoji_tag('fire')}, Water {emoji_tag('water')}, Monster {emoji_tag('monster')}, Good {emoji_tag('good')}, Evil {emoji_tag('evil')}, and Immortality {emoji_tag('immortality')}\n"
        f"• Unlock 100 elements to discover the secret element: Time {emoji_tag('time')}!\n"
        "• Use discovered elements to create new ones!\n\n"
        "<b>Rewards:</b>\n"
        f"• Normal discovery: <b>${NORMAL_REWARD:,}</b>\n"
        f"• Mythical discovery: <b>${MYTHICAL_REWARD:,}</b>\n\n"
        f"• EXTRAS discovery: <b>${EXTRAS_REWARD:,}</b>\n\n"
        "<b>Myths & Monsters Edition:</b>\n"
        "• Some elements are from the Myths & Monsters expansion pack\n"
        "• These are marked as 'mythical' and give higher rewards\n"
        f"• You need Good {emoji_tag('good')}, Evil {emoji_tag('evil')}, Immortality {emoji_tag('immortality')}, and Monster {emoji_tag('monster')} to unlock mythical recipes\n\n"
        "<b>EXTRAS Edition:</b>\n"
        "• Community-made fanon elements are marked as 🧸🛍️ EXTRAS\n"
        "• You can craft them like normal elements once discovered\n"
        "• You can search by visible item name (Fanon prefix is hidden)\n\n"
        f"• At 50 unlocked elements, you auto-unlock: {display_name('Fanon:Left')} {emoji_tag('Fanon:Left')}, {display_name('Fanon:Next')} {emoji_tag('Fanon:Next')}, and {display_name('Fanon:Right')} {emoji_tag('Fanon:Right')}\n\n"
        "<b>Viewing Your Collection:</b>\n"
        "• Use <code>/craft</code> to see your unlocked elements\n"
        "• Use <code>/craft 2</code> to see page 2, etc.\n"
        "• Use <code>/missing_crafts</code> to see elements you haven't discovered yet\n"
        "• Use <code>/missing_crafts 2</code> to see page 2 of missing elements\n\n"
        "• Use <code>/missing_combos</code> to see combo recipes you're still missing\n\n"
        "<b>Why 50 elements per page?</b>\n"
        "• Telegram restricts messages to 50 custom emojis per message\n"
        "• Each element uses a custom emoji for proper display\n"
        "• Showing 50 per page ensures all emojis render correctly\n\n"
        "<b>Element Names:</b>\n"
        "• You can use spaces or hyphens: 'apple of discord' = 'apple-of-discord'\n"
        "• Names are case-insensitive: 'Water' = 'water'\n"
        "• Fanon backend prefix is hidden in user-facing names\n\n"
        f"<b>Total Elements:</b> {TOTAL_ITEMS} to discover\n"
        f"<b>Official:</b> {TOTAL_OFFICIAL_ITEMS} | <b>EXTRAS:</b> {TOTAL_EXTRAS_ITEMS}"
        "</blockquote>"
    )

    await message.reply(guide_text)
