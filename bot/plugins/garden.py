"""Garden mini-game - plant seeds, grow crops, harvest and sell."""

import asyncio
import html
import random
from datetime import datetime, timedelta

from pyrogram import Client as Bot
from pyrogram import filters
from pyrogram.enums import ChatMembersFilter, ChatType
from pyrogram.errors import BadRequest
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.client import client
from bot.command_registry import reg
from bot.config import Config
from bot.constants import (
    ALL_PLANTABLE,
    CROPS,
    FERTILIZE_BOT_BAN_DAYS,
    FERTILIZE_BOT_REPORT_WINDOW_HOURS,
    FERTILIZE_BOT_THRESHOLD,
    FERTILIZE_BOT_WINDOW_HOURS,
    FERTILIZE_RECEIVE_BOT_BAN_DAYS,
    FERTILIZE_RECEIVE_BOT_THRESHOLD,
    FERTILIZE_RECEIVE_BOT_WINDOW_HOURS,
    FLOWERS,
    FOODS,
    FRUITS,
    GARDEN_EXPANSION_COSTS,
    GARDEN_MAX_SIZE,
    calculate_garden_fertilize_bonus,
    format_price,
    format_time,
    get_all_sellable,
    get_crop_display_emoji,
    get_crop_emoji,
    get_custom_emoji_id,
    get_item_display_name,
    maybe_pay_tomato_soup_commission,
    parse_item_and_qty,
    resolve_item_key,
)
from bot.database import Database, db
from bot.queue_it import queue_it
from bot.utils import mention_html, user_mention

# Per-user harvest lock — serialises /harvest and the harvest button per
# user so spamming the button cannot fire multiple concurrent harvest flows.
# DB-level atomic UPDATE makes double-harvest impossible regardless, but the
# lock prevents wasted work and duplicate "Nothing to harvest" alerts.
_harvest_locks: dict[int, asyncio.Lock] = {}


def _get_harvest_lock(user_id: int) -> asyncio.Lock:
    lock = _harvest_locks.get(user_id)
    if lock is None:
        lock = asyncio.Lock()
        _harvest_locks[user_id] = lock
    return lock


# chat_id → (member_ids, expires_at, is_partial)
# is_partial=True means only admins were visible
_member_cache: dict[int, tuple[frozenset[int], datetime, bool]] = {}
_MEMBER_CACHE_TTL = timedelta(hours=1)

# user_id → pending plant confirmation state (requests, missing seeds, cost)
_pending_plant: dict[int, dict] = {}


async def _get_group_member_ids(
    bot: Bot, chat_id: int
) -> tuple[frozenset[int], bool]:
    """Return (member_ids, is_partial) for a group, using a 1-hour cache.

    is_partial=True when the bot couldn't see all members and fell back to admins only.
    Re-fetches automatically after the cache TTL expires.
    """
    cached = _member_cache.get(chat_id)
    if cached and cached[1] > datetime.now():
        return cached[0], cached[2]

    member_ids: set[int] = set()
    is_partial = False

    try:
        async for member in bot.get_chat_members(chat_id):
            if member.user and not member.user.is_bot:
                member_ids.add(member.user.id)
    except Exception:
        is_partial = True
        try:
            async for member in bot.get_chat_members(
                chat_id, filter=ChatMembersFilter.ADMINISTRATORS
            ):
                if member.user and not member.user.is_bot:
                    member_ids.add(member.user.id)
        except Exception:
            pass

    result = frozenset(member_ids)
    _member_cache[chat_id] = (
        result,
        datetime.now() + _MEMBER_CACHE_TTL,
        is_partial,
    )
    return result, is_partial


async def safe_callback_answer(
    callback: CallbackQuery, text: str = None, show_alert: bool = False
):
    """Best-effort callback answer (ignore stale callback query errors)."""
    try:
        await callback.answer(text, show_alert=show_alert)
    except BadRequest:
        pass


def get_crop_status_emoji(
    planted_at: datetime, grow_time_minutes: int, is_ready: bool
) -> str:
    """Get emoji showing crop growth status."""
    if is_ready:
        return "✅"
    if not planted_at:
        return "⬜"

    elapsed = (
        datetime.now() - planted_at.replace(tzinfo=None)
    ).total_seconds() / 60
    progress = elapsed / grow_time_minutes

    if progress >= 1.0:
        return "✅"
    elif progress >= 0.75:
        return "🌿"
    elif progress >= 0.5:
        return "🌱"
    elif progress >= 0.25:
        return "🌾"
    else:
        return "🫛"


async def update_garden_ready_status(db: Database, garden_id: int):
    """Check and mark ready plots."""
    plots = await db.get_garden_plots(garden_id)
    ready_positions = []

    for plot in plots:
        if plot["crop_type"] and plot["planted_at"] and not plot["is_ready"]:
            crop_info = ALL_PLANTABLE.get(plot["crop_type"])
            if crop_info:
                elapsed = (
                    datetime.now() - plot["planted_at"].replace(tzinfo=None)
                ).total_seconds() / 60
                if elapsed >= crop_info["grow_time"]:
                    ready_positions.append(plot["position"])

    if ready_positions:
        await db.mark_plots_ready(garden_id, ready_positions)


async def get_garden_display(
    db: Database,
    user_id: int,
    garden: dict,
    mode: str = "view",
    selected_crop: str = None,
    multiplier: int = 1,
) -> tuple[str, InlineKeyboardMarkup]:
    """Generate garden display text and keyboard."""
    await update_garden_ready_status(db, garden["id"])
    plots = await db.get_garden_plots(garden["id"])

    # Get inventory counts
    inventory = await db.get_inventory(user_id)
    seed_counts = {}
    harvest_counts = {}
    for item in inventory:
        if item["item_type"] == "seed":
            seed_counts[item["item_name"]] = item["quantity"]
        elif item["item_type"] == "harvest":
            harvest_counts[item["item_name"]] = item["quantity"]

    size = garden["size"]

    # Build garden text
    owner = await db.fetchrow(
        "SELECT first_name FROM users WHERE user_id = $1", user_id
    )
    owner_name = owner["first_name"] if owner else "Unknown"

    text = f"🌻 <b>{html.escape(owner_name)}'s Garden</b> ({size}×{size})\n"
    text += "━━━━━━━━━━━━━━━━\n"

    # Count ready and growing
    ready_count = 0
    growing_count = 0
    empty_count = 0
    growing_crops = {}  # Track growing crops with their time remaining

    for plot in plots:
        if plot["is_ready"]:
            ready_count += 1
        elif plot["crop_type"]:
            growing_count += 1
            # Track time remaining for growing crops
            crop_info = ALL_PLANTABLE.get(plot["crop_type"])
            if crop_info and plot["planted_at"]:
                elapsed = (
                    datetime.now() - plot["planted_at"].replace(tzinfo=None)
                ).total_seconds() / 60
                remaining = max(0, crop_info["grow_time"] - elapsed)
                if plot["crop_type"] not in growing_crops:
                    growing_crops[plot["crop_type"]] = {
                        "count": 0,
                        "min_remaining": remaining,
                    }
                growing_crops[plot["crop_type"]]["count"] += 1
                growing_crops[plot["crop_type"]]["min_remaining"] = min(
                    growing_crops[plot["crop_type"]]["min_remaining"], remaining
                )
        else:
            empty_count += 1

    if ready_count > 0:
        text += f"✅ Ready to harvest: {ready_count}\n"
    if growing_count > 0:
        text += f"🌱 Growing: {growing_count}\n"
    if empty_count > 0:
        text += f"⬜ Empty plots: {empty_count}\n"

    # Show growing crops list in blockquote
    if growing_crops:
        text += "\n<blockquote expandable><b>🌱 Growing:</b>\n"
        for crop_name, info in growing_crops.items():
            emoji = get_crop_display_emoji(crop_name)
            mins = int(info["min_remaining"])
            time_str = (
                f"{mins}m" if mins < 60 else f"{mins // 60}h {mins % 60}m"
            )
            text += f"{emoji} {get_item_display_name(crop_name)} ×{info['count']} - {time_str}\n"
        text += "</blockquote>"

    text += "\n<blockquote expandable><b>📦 Inventory:</b>\n"

    # Show inventory in compact format [seeds, harvested]
    shown_any = False
    for crop_name in ALL_PLANTABLE:
        emoji = get_crop_display_emoji(crop_name)
        seeds = seed_counts.get(crop_name, 0)
        harvested = harvest_counts.get(crop_name, 0)
        if seeds > 0 or harvested > 0:
            text += f"{emoji} [{seeds}, {harvested}] "
            shown_any = True

    if not shown_any:
        text += "<i>Empty</i>"

    text += "\n</blockquote>"
    text += "\n<i>💡 Use /catalog to see prices</i>\n"
    text += "<blockquote>Want a seed refund? Try /refund_seed</blockquote>\n"

    # Build keyboard based on mode
    buttons = []

    if mode == "view":
        # No multiplier row in main view anymore - show after crop selection

        # Action buttons
        buttons.append([
            InlineKeyboardButton(
                text="🛒 Buy",
                callback_data=f"garden:{garden['id']}:buy:{multiplier}",
            ),
            InlineKeyboardButton(
                text="🌱 Plant",
                callback_data=f"garden:{garden['id']}:plant:{multiplier}",
            ),
            InlineKeyboardButton(
                text="💰 Sell",
                callback_data=f"garden:{garden['id']}:sell:{multiplier}",
            ),
        ])

        # Harvest button (if ready crops)
        if ready_count > 0:
            buttons.append([
                InlineKeyboardButton(
                    text=f"🌾 Harvest All ({ready_count})",
                    callback_data=f"garden:{garden['id']}:harvest",
                )
            ])

        # Expand button
        if size < GARDEN_MAX_SIZE:
            expand_cost = GARDEN_EXPANSION_COSTS.get(size + 1, 75000)
            buttons.append([
                InlineKeyboardButton(
                    text=f"📐 Expand to {size + 1}×{size + 1} - {format_price(expand_cost)}",
                    callback_data=f"garden:{garden['id']}:expand",
                )
            ])

        # Refresh
        buttons.append([
            InlineKeyboardButton(
                text="🔄 Refresh",
                callback_data=f"garden:{garden['id']}:refresh:{multiplier}",
            )
        ])

    elif mode == "buy":
        # Show buyable crops - click to select quantity
        text += "\n<b>🛒 Select seed to buy:</b>\n"

        crop_buttons = []
        for crop_name, info in ALL_PLANTABLE.items():
            custom_id = info.get("custom_emoji_id")
            btn_text = (
                crop_name.title()
                if custom_id
                else f"{info['emoji']} {crop_name.title()}"
            )
            crop_buttons.append(
                InlineKeyboardButton(
                    text=btn_text,
                    callback_data=f"garden:{garden['id']}:buyqty:{crop_name}",
                    icon_custom_emoji_id=custom_id,
                )
            )

        # Arrange in rows of 3
        for i in range(0, len(crop_buttons), 3):
            buttons.append(crop_buttons[i : i + 3])

        buttons.append([
            InlineKeyboardButton(
                text="« Back",
                callback_data=f"garden:{garden['id']}:back:{multiplier}",
            )
        ])

    elif mode == "plant":
        # Show plantable seeds from inventory - click to select quantity
        text += "\n<b>🌱 Select seed to plant:</b>\n"

        seed_buttons = []
        for crop_name, qty in seed_counts.items():
            if qty > 0:
                custom_id = get_custom_emoji_id(crop_name)
                emoji = get_crop_emoji(crop_name)
                btn_text = f"({qty})" if custom_id else f"{emoji} ({qty})"
                seed_buttons.append(
                    InlineKeyboardButton(
                        text=btn_text,
                        callback_data=f"garden:{garden['id']}:plantqty:{crop_name}",
                        icon_custom_emoji_id=custom_id,
                    )
                )

        if seed_buttons:
            for i in range(0, len(seed_buttons), 3):
                buttons.append(seed_buttons[i : i + 3])
        else:
            text += "<i>No seeds in inventory!</i>\n"

        buttons.append([
            InlineKeyboardButton(
                text="« Back",
                callback_data=f"garden:{garden['id']}:back:{multiplier}",
            )
        ])

    elif mode == "sell":
        # Show sellable harvests - click to select quantity
        text += "\n<b>💰 Select crop to sell:</b>\n"

        sellable = get_all_sellable()
        sell_buttons = []
        for crop_name, qty in harvest_counts.items():
            if qty > 0 and crop_name in sellable:
                info = sellable[crop_name]
                custom_id = get_custom_emoji_id(crop_name)
                btn_text = (
                    f"({qty})" if custom_id else f"{info['emoji']} ({qty})"
                )
                sell_buttons.append(
                    InlineKeyboardButton(
                        text=btn_text,
                        callback_data=f"garden:{garden['id']}:sellqty:{crop_name}",
                        icon_custom_emoji_id=custom_id,
                    )
                )

        if sell_buttons:
            for i in range(0, len(sell_buttons), 2):
                buttons.append(sell_buttons[i : i + 2])
        else:
            text += "<i>Nothing to sell!</i>\n"

        buttons.append([
            InlineKeyboardButton(
                text="« Back",
                callback_data=f"garden:{garden['id']}:back:{multiplier}",
            )
        ])

    elif mode == "buyqty" and selected_crop:
        # Quantity selection for buying
        info = ALL_PLANTABLE.get(selected_crop)
        if info:
            text += f"\n<b>🛒 Buy {info['emoji']} {selected_crop.title()} seeds:</b>\n"
            text += f"Price: {format_price(info['seed_cost'])} each\n"

            # Multipliers: 1, 3, 5, 10, 20, 50
            buttons.append([
                InlineKeyboardButton(
                    text=f"×1 = {format_price(info['seed_cost'])}",
                    callback_data=f"garden:{garden['id']}:buycrop:{selected_crop}:1",
                ),
                InlineKeyboardButton(
                    text=f"×3 = {format_price(info['seed_cost'] * 3)}",
                    callback_data=f"garden:{garden['id']}:buycrop:{selected_crop}:3",
                ),
            ])
            buttons.append([
                InlineKeyboardButton(
                    text=f"×5 = {format_price(info['seed_cost'] * 5)}",
                    callback_data=f"garden:{garden['id']}:buycrop:{selected_crop}:5",
                ),
                InlineKeyboardButton(
                    text=f"×10 = {format_price(info['seed_cost'] * 10)}",
                    callback_data=f"garden:{garden['id']}:buycrop:{selected_crop}:10",
                ),
            ])
            buttons.append([
                InlineKeyboardButton(
                    text=f"×20 = {format_price(info['seed_cost'] * 20)}",
                    callback_data=f"garden:{garden['id']}:buycrop:{selected_crop}:20",
                ),
                InlineKeyboardButton(
                    text=f"×50 = {format_price(info['seed_cost'] * 50)}",
                    callback_data=f"garden:{garden['id']}:buycrop:{selected_crop}:50",
                ),
            ])
        buttons.append([
            InlineKeyboardButton(
                text="« Back", callback_data=f"garden:{garden['id']}:buy:1"
            ),
        ])

    elif mode == "plantqty" and selected_crop:
        # Quantity selection for planting
        emoji = get_crop_emoji(selected_crop)
        have_seeds = seed_counts.get(selected_crop, 0)
        empty_plots = sum(1 for p in plots if not p["crop_type"])
        max_plant = min(have_seeds, empty_plots)

        text += f"\n<b>🌱 Plant {emoji} {selected_crop}:</b>\n"
        text += f"Seeds: {have_seeds} | Empty plots: {empty_plots}\n"

        # Multipliers: 1, 3, 5, 10, 20, 50, Max
        buttons.append([
            InlineKeyboardButton(
                text="×1",
                callback_data=f"garden:{garden['id']}:plantcrop:{selected_crop}:1",
            ),
            InlineKeyboardButton(
                text="×3",
                callback_data=f"garden:{garden['id']}:plantcrop:{selected_crop}:3",
            ),
            InlineKeyboardButton(
                text="×5",
                callback_data=f"garden:{garden['id']}:plantcrop:{selected_crop}:5",
            ),
        ])
        buttons.append([
            InlineKeyboardButton(
                text="×10",
                callback_data=f"garden:{garden['id']}:plantcrop:{selected_crop}:10",
            ),
            InlineKeyboardButton(
                text="×20",
                callback_data=f"garden:{garden['id']}:plantcrop:{selected_crop}:20",
            ),
            InlineKeyboardButton(
                text="×50",
                callback_data=f"garden:{garden['id']}:plantcrop:{selected_crop}:50",
            ),
        ])
        buttons.append([
            InlineKeyboardButton(
                text=f"Max ({max_plant})",
                callback_data=f"garden:{garden['id']}:plantcrop:{selected_crop}:{max_plant}",
            ),
        ])
        buttons.append([
            InlineKeyboardButton(
                text="« Back", callback_data=f"garden:{garden['id']}:plant:1"
            ),
        ])

    elif mode == "sellqty" and selected_crop:
        # Quantity selection for selling
        sellable = get_all_sellable()
        info = sellable.get(selected_crop)
        have_qty = harvest_counts.get(selected_crop, 0)

        if info:
            text += f"\n<b>💰 Sell {info['emoji']} {selected_crop}:</b>\n"
            text += f"You have: {have_qty} | Price: {format_price(info['sell_price'])} each\n"

            # Multipliers: 1, 3, 5, 10, 20, 50, Max
            buttons.append([
                InlineKeyboardButton(
                    text=f"×1 = {format_price(info['sell_price'])}",
                    callback_data=f"garden:{garden['id']}:sellcrop:{selected_crop}:1",
                ),
                InlineKeyboardButton(
                    text=f"×3 = {format_price(info['sell_price'] * 3)}",
                    callback_data=f"garden:{garden['id']}:sellcrop:{selected_crop}:3",
                ),
            ])
            buttons.append([
                InlineKeyboardButton(
                    text=f"×5 = {format_price(info['sell_price'] * 5)}",
                    callback_data=f"garden:{garden['id']}:sellcrop:{selected_crop}:5",
                ),
                InlineKeyboardButton(
                    text=f"×10 = {format_price(info['sell_price'] * 10)}",
                    callback_data=f"garden:{garden['id']}:sellcrop:{selected_crop}:10",
                ),
            ])
            buttons.append([
                InlineKeyboardButton(
                    text=f"×20 = {format_price(info['sell_price'] * 20)}",
                    callback_data=f"garden:{garden['id']}:sellcrop:{selected_crop}:20",
                ),
                InlineKeyboardButton(
                    text=f"×50 = {format_price(info['sell_price'] * 50)}",
                    callback_data=f"garden:{garden['id']}:sellcrop:{selected_crop}:50",
                ),
            ])
            buttons.append([
                InlineKeyboardButton(
                    text=f"Max ({have_qty}) = {format_price(info['sell_price'] * have_qty)}",
                    callback_data=f"garden:{garden['id']}:sellcrop:{selected_crop}:{have_qty}",
                ),
            ])
        buttons.append([
            InlineKeyboardButton(
                text="« Back", callback_data=f"garden:{garden['id']}:sell:1"
            ),
        ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    return text, keyboard


reg("garden", "🌻 View your garden")


@client.on_message(filters.command(["garden", "g"]))
async def garden_command(
    message: Message,
):
    """View your garden."""
    user = message.from_user

    # Check if viewing someone else's garden
    target = user
    if message.reply_to_message and message.reply_to_message.from_user:
        target = message.reply_to_message.from_user

    # Ensure user exists
    target = await db.upsert_user(target.id, target.username, target.first_name)

    # Get or create garden
    garden = await db.get_or_create_garden(target.id)

    text, keyboard = await get_garden_display(db, target.id, garden)

    # Only show full keyboard to owner
    if target.id != user.id:
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🔄 Refresh",
                        callback_data=f"garden:{garden['id']}:refresh:1",
                    )
                ]
            ]
        )

    await message.reply(text, reply_markup=keyboard)


reg("plant", "🌱 Plant seeds")


@client.on_message(filters.command(["plant"]))
async def plant_command(
    message: Message,
):
    """Plant seeds. Auto-harvests ready crops first to free space."""
    user = message.from_user
    raw_text = (
        (message.text or "").split(" ", 1)[1].strip()
        if " " in (message.text or "")
        else ""
    )

    if not raw_text:
        await message.reply(
            "🌱 <b>Plant Seeds</b>\n\n"
            "Usage: <code>/plant &lt;crop&gt; [amount]</code>\n\n"
            "Examples:\n"
            "  <code>/plant carrot 5</code> — plant 5 carrot seeds\n"
            "  <code>/plant carrot all</code> — plant all carrot seeds\n"
            "  <code>/plant tomato 10, apple 5, cherry 3</code> — plant multiple at once\n\n"
            "Or use /garden and click Plant button.\n\n"
            "<blockquote>Want a seed refund? Try /refund_seed</blockquote>"
        )
        return

    # Parse one or more comma-separated entries: "tomato 10, apple 5"
    entries = [e.strip() for e in raw_text.split(",") if e.strip()]
    requests: list[tuple[str, int]] = []  # (crop_key, qty)
    for entry in entries:
        tokens = entry.split()
        raw_name, qty = parse_item_and_qty(tokens, all_value=999999)
        crop_key = resolve_item_key(
            raw_name
        ) or raw_name.strip().lower().replace(" ", "_")
        if crop_key not in ALL_PLANTABLE:
            await message.reply(f"❌ Unknown crop: <code>{raw_name}</code>")
            return
        requests.append((crop_key, qty))

    garden = await db.get_or_create_garden(user.id)

    # Auto-harvest ready crops first to free up space
    harvested: dict[str, int] = {}
    async with _get_harvest_lock(user.id):
        await update_garden_ready_status(db, garden["id"])
        plots = await db.get_garden_plots(garden["id"])
        ready_positions = [p["position"] for p in plots if p["is_ready"]]
        if ready_positions:
            crop_types = await db.harvest_plots_batch(
                garden["id"], ready_positions
            )
            for crop_type in crop_types or []:
                if crop_type in ALL_PLANTABLE:
                    info = ALL_PLANTABLE[crop_type]
                    yield_amount = random.randint(
                        info["yield_min"], info["yield_max"]
                    )
                    harvested[crop_type] = (
                        harvested.get(crop_type, 0) + yield_amount
                    )
            for crop_type, amount in harvested.items():
                await db.add_inventory_item(
                    user.id, "harvest", crop_type, amount
                )
            if harvested:
                await db.increment_garden_harvests(
                    user.id, sum(harvested.values())
                )
        # Refresh plots after harvest
        plots = await db.get_garden_plots(garden["id"])

    empty_positions = [p["position"] for p in plots if not p["crop_type"]]

    # Check for missing seeds and offer auto-buy if wallet covers the shortfall.
    # Skip entries where qty == 999999 ("all") — those mean "use whatever's in inventory".
    missing: list[tuple[str, int, int]] = []  # (crop_key, shortfall, cost)
    for crop_key, qty in requests:
        if qty >= 999999:
            continue  # "all" = plant what you have, no shortfall
        seeds_have = await db.get_inventory_item(user.id, "seed", crop_key)
        shortfall = qty - seeds_have
        if shortfall > 0:
            cost = ALL_PLANTABLE[crop_key]["seed_cost"] * shortfall
            missing.append((crop_key, shortfall, cost))

    if missing:
        total_buy_cost = sum(c for _, _, c in missing)
        wallet = await db.get_wallet(user.id)
        if wallet["balance"] >= total_buy_cost:
            _pending_plant[user.id] = {
                "requests": requests,
                "missing": missing,
                "total_buy_cost": total_buy_cost,
            }
            confirm_text = ""
            if harvested:
                confirm_text += "🌾 <b>Auto-Harvested:</b>\n"
                for crop, amount in harvested.items():
                    emoji = get_crop_display_emoji(crop)
                    confirm_text += f"  • {emoji} {get_item_display_name(crop)}: +{amount}\n"
                confirm_text += "\n"
            confirm_text += "🛒 <b>Missing seeds:</b>\n"
            for ck, short, cost in missing:
                emoji = get_crop_display_emoji(ck)
                confirm_text += f"  • {emoji} {get_item_display_name(ck)}: {short}x — {format_price(cost)}\n"
            confirm_text += (
                f"\n💰 Auto-buy cost: <b>{format_price(total_buy_cost)}</b>\n"
            )
            confirm_text += (
                f"💵 Your wallet: {format_price(wallet['balance'])}\n\n"
            )
            confirm_text += "Confirm auto-buy and plant?"
            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="✅ Buy & plant all",
                            callback_data=f"plant_confirm:yes:{user.id}",
                        ),
                        InlineKeyboardButton(
                            text="🌱 Plant with existing",
                            callback_data=f"plant_confirm:no:{user.id}",
                        ),
                    ]
                ]
            )
            await message.reply(confirm_text, reply_markup=keyboard)
            return
        short_by = total_buy_cost - wallet["balance"]
        text = "❌ Not enough seeds or money for auto-buy.\n\n"
        if harvested:
            text += "🌾 <b>Auto-Harvested:</b>\n"
            for crop, amount in harvested.items():
                emoji = get_crop_display_emoji(crop)
                text += (
                    f"  • {emoji} {get_item_display_name(crop)}: +{amount}\n"
                )
            text += "\n"
        text += "🛒 <b>Missing seeds:</b>\n"
        for ck, short, cost in missing:
            emoji = get_crop_display_emoji(ck)
            text += f"  • {emoji} {get_item_display_name(ck)}: {short}x — {format_price(cost)}\n"
        text += f"\n💰 Auto-buy cost: <b>{format_price(total_buy_cost)}</b>\n"
        text += f"💵 Your wallet: {format_price(wallet['balance'])}\n"
        text += f"⚠️ Short by: {format_price(short_by)}"
        await message.reply(text)
        return

    # Plant into the now-freed slots
    plant_results: list[tuple[str, int]] = []
    skipped: list[str] = []
    slot = 0

    for crop_key, qty in requests:
        if slot >= len(empty_positions):
            skipped.append(get_item_display_name(crop_key))
            continue
        seeds = await db.get_inventory_item(user.id, "seed", crop_key)
        if seeds <= 0:
            skipped.append(get_item_display_name(crop_key))
            continue
        to_plant = min(qty, len(empty_positions) - slot, seeds)
        if to_plant <= 0:
            skipped.append(get_item_display_name(crop_key))
            continue
        planted = await db.plant_crops_batch(
            garden["id"], empty_positions[slot : slot + to_plant], crop_key
        )
        if planted > 0:
            await db.remove_inventory_item(user.id, "seed", crop_key, planted)
            slot += planted
            plant_results.append((crop_key, planted))

    # Build reply: harvest section (if any) + plant section
    text = ""

    if harvested:
        text += "🌾 <b>Auto-Harvested:</b>\n"
        for crop, amount in harvested.items():
            emoji = get_crop_display_emoji(crop)
            text += f"  • {emoji} {get_item_display_name(crop)}: +{amount}\n"
        text += "\n"

    if plant_results:
        total_planted = sum(q for _, q in plant_results)
        if len(plant_results) == 1:
            crop_key, planted = plant_results[0]
            emoji = get_crop_display_emoji(crop_key)
            grow_time = ALL_PLANTABLE[crop_key]["grow_time"]
            text += (
                f"🌱 Planted {planted}x {emoji} {get_item_display_name(crop_key)}!\n"
                f"⏱️ Ready in: {format_time(grow_time)}"
            )
        else:
            text += f"🌱 <b>Planted {total_planted} seeds across {len(plant_results)} crops:</b>\n"
            for crop_key, planted in plant_results:
                emoji = get_crop_display_emoji(crop_key)
                grow_time = ALL_PLANTABLE[crop_key]["grow_time"]
                text += f"  • {emoji} {get_item_display_name(crop_key)}: {planted}x ({format_time(grow_time)})\n"
        if skipped:
            text += f"\n⚠️ Skipped (no seeds/space): {', '.join(skipped)}"
    elif not harvested:
        text = "❌ Nothing to harvest and no empty plots!"
    else:
        text += (
            "❌ No empty plots even after harvest — garden is fully occupied."
        )

    await message.reply(text)


@client.on_callback_query(filters.regex(r"^plant_confirm:"))
async def handle_plant_confirm_callback(callback: CallbackQuery):
    """Handle auto-buy confirmation for /plant."""
    parts = callback.data.split(":")
    action = parts[1]  # yes | no
    owner_id = int(parts[2])

    if callback.from_user.id != owner_id:
        await safe_callback_answer(
            callback, "This isn't your planting session!", show_alert=True
        )
        return

    pending = _pending_plant.pop(owner_id, None)
    if not pending:
        await safe_callback_answer(
            callback, "Session expired — run /plant again.", show_alert=True
        )
        return

    requests: list[tuple[str, int]] = pending["requests"]
    bought_text = ""

    if action == "yes":
        total_buy_cost: int = pending["total_buy_cost"]
        wallet = await db.get_wallet(owner_id)
        if wallet["balance"] < total_buy_cost:
            await queue_it(
                lambda: callback.message.edit_text(
                    f"❌ Not enough coins!\n"
                    f"Need {format_price(total_buy_cost)}, have {format_price(wallet['balance'])}."
                ),
                callback.message.chat,
            )
            await safe_callback_answer(callback)
            return
        bought_text = "🛒 <b>Auto-bought:</b>\n"
        for crop_key, short, cost in pending["missing"]:
            await db.add_balance(
                owner_id, -cost, f"Auto-bought {short}x {crop_key} seeds"
            )
            await db.add_inventory_item(owner_id, "seed", crop_key, short)
            emoji = get_crop_display_emoji(crop_key)
            bought_text += f"  • {emoji} {get_item_display_name(crop_key)}: {short}x — {format_price(cost)}\n"
        bought_text += "\n"

    # Re-fetch garden for current empty plots (state may have changed)
    garden = await db.get_or_create_garden(owner_id)
    plots = await db.get_garden_plots(garden["id"])
    empty_positions = [p["position"] for p in plots if not p["crop_type"]]

    plant_results: list[tuple[str, int]] = []
    skipped: list[str] = []
    slot = 0
    for crop_key, qty in requests:
        if slot >= len(empty_positions):
            skipped.append(get_item_display_name(crop_key))
            continue
        seeds = await db.get_inventory_item(owner_id, "seed", crop_key)
        if seeds <= 0:
            skipped.append(get_item_display_name(crop_key))
            continue
        to_plant = min(qty, len(empty_positions) - slot, seeds)
        if to_plant <= 0:
            skipped.append(get_item_display_name(crop_key))
            continue
        planted = await db.plant_crops_batch(
            garden["id"], empty_positions[slot : slot + to_plant], crop_key
        )
        if planted > 0:
            await db.remove_inventory_item(owner_id, "seed", crop_key, planted)
            slot += planted
            plant_results.append((crop_key, planted))

    text = bought_text
    if plant_results:
        total_planted = sum(q for _, q in plant_results)
        if len(plant_results) == 1:
            ck, planted = plant_results[0]
            emoji = get_crop_display_emoji(ck)
            grow_time = ALL_PLANTABLE[ck]["grow_time"]
            text += (
                f"🌱 Planted {planted}x {emoji} {get_item_display_name(ck)}!\n"
                f"⏱️ Ready in: {format_time(grow_time)}"
            )
        else:
            text += f"🌱 <b>Planted {total_planted} seeds across {len(plant_results)} crops:</b>\n"
            for ck, planted in plant_results:
                emoji = get_crop_display_emoji(ck)
                grow_time = ALL_PLANTABLE[ck]["grow_time"]
                text += f"  • {emoji} {get_item_display_name(ck)}: {planted}x ({format_time(grow_time)})\n"
        if skipped:
            text += f"\n⚠️ Skipped (no seeds/space): {', '.join(skipped)}"
    else:
        text += "❌ No empty plots — garden is fully occupied."

    await queue_it(
        lambda: callback.message.edit_text(text),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_message(filters.command(["autoharvest", "ah"]))
async def autoharvest_command(
    message: Message,
):
    """Toggle auto-harvest on/off."""
    user = message.from_user
    garden = await db.get_or_create_garden(user.id)
    currently = garden.get("auto_harvest", False)
    new_state = not currently
    await db.set_auto_harvest(user.id, new_state, message.chat.id)
    if new_state:
        await message.reply(
            "🤖 <b>Auto-harvest enabled!</b>\n"
            "I'll harvest your garden automatically when crops are ready and notify you here.\n\n"
            "Use /autoharvest again to disable."
        )
    else:
        await message.reply("❌ Auto-harvest disabled.")


async def auto_harvest_loop(bot, database) -> None:
    """Background task: harvest ready crops for users who opted in."""
    import logging as _log

    _logger = _log.getLogger(__name__)
    CHECK_INTERVAL = 5 * 60  # 5 minutes

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        try:
            gardens = await database.get_auto_harvest_gardens()
        except Exception as e:
            _logger.warning("auto_harvest_loop: failed to fetch gardens: %s", e)
            continue

        for g in gardens:
            owner_id = g["owner_id"]
            garden_id = g["id"]
            chat_id = g["notify_chat_id"]
            try:
                async with _get_harvest_lock(owner_id):
                    await update_garden_ready_status(database, garden_id)
                    plots = await database.get_garden_plots(garden_id)
                    ready_positions = [
                        p["position"] for p in plots if p["is_ready"]
                    ]
                    if not ready_positions:
                        continue
                    crop_types = await database.harvest_plots_batch(
                        garden_id, ready_positions
                    )
                    if not crop_types:
                        continue
                    harvested: dict[str, int] = {}
                    for crop_type in crop_types:
                        if crop_type in ALL_PLANTABLE:
                            info = ALL_PLANTABLE[crop_type]
                            yield_amount = random.randint(
                                info["yield_min"], info["yield_max"]
                            )
                            harvested[crop_type] = (
                                harvested.get(crop_type, 0) + yield_amount
                            )
                    for crop_type, amount in harvested.items():
                        await database.add_inventory_item(
                            owner_id, "harvest", crop_type, amount
                        )
                    await database.increment_garden_harvests(
                        owner_id, sum(harvested.values())
                    )

                    text = "🤖 <b>Auto-Harvest!</b>\n🌾 <b>Harvested:</b>\n"
                    for crop, amount in harvested.items():
                        emoji = get_crop_display_emoji(crop)
                        text += f"  • {emoji} {get_item_display_name(crop)}: +{amount}\n"
                    try:
                        await bot.send_message(chat_id, text)
                    except Exception as send_err:
                        _logger.debug(
                            "auto_harvest: send_message failed for %s: %s",
                            owner_id,
                            send_err,
                        )
            except Exception as e:
                _logger.warning(
                    "auto_harvest_loop: error for garden %s: %s", garden_id, e
                )


reg("harvest", "🌾 Harvest ready crops")


@client.on_message(filters.command(["harvest"]))
async def harvest_command(
    message: Message,
):
    """Harvest all ready crops."""
    user = message.from_user

    garden = await db.get_garden(user.id)
    if not garden:
        await message.reply("❌ You don't have a garden! Use /garden first.")
        return

    async with _get_harvest_lock(user.id):
        await update_garden_ready_status(db, garden["id"])
        plots = await db.get_garden_plots(garden["id"])

        # Find ready plots
        ready_positions = [p["position"] for p in plots if p["is_ready"]]
        if not ready_positions:
            await message.reply("❌ Nothing ready to harvest!")
            return

        # Batch harvest all ready plots
        crop_types = await db.harvest_plots_batch(garden["id"], ready_positions)
        if not crop_types:
            await message.reply("❌ Nothing ready to harvest!")
            return

        # Count crops and add to inventory
        harvested = {}
        for crop_type in crop_types:
            if crop_type in ALL_PLANTABLE:
                info = ALL_PLANTABLE[crop_type]
                yield_amount = random.randint(
                    info["yield_min"], info["yield_max"]
                )
                harvested[crop_type] = (
                    harvested.get(crop_type, 0) + yield_amount
                )

        # Batch add to inventory
        for crop_type, amount in harvested.items():
            await db.add_inventory_item(user.id, "harvest", crop_type, amount)
        await db.increment_garden_harvests(user.id, sum(harvested.values()))

        from bot.achievements import check_garden_achievements

        await check_garden_achievements(db, user.id)

    if harvested:
        text = "🌾 <b>Harvested:</b>\n"
        for crop, amount in harvested.items():
            emoji = get_crop_display_emoji(crop)
            text += f"  • {emoji} {get_item_display_name(crop)}: +{amount}\n"
        await message.reply(text)
    else:
        await message.reply("❌ Nothing ready to harvest!")


reg("sell", "💰 Sell crops")


@client.on_message(filters.command(["sell"]))
async def sell_command(
    message: Message,
):
    """Sell crops and food items interactively."""
    user = message.from_user
    args = message.text.split()[1:] if message.text else []

    await db.upsert_user(user.id, user.username, user.first_name)

    if args:
        # Handle command-line args for backward compatibility
        await _handle_sell_args(message, db, args, user)
        return

    # Show interactive sell menu
    inventory = await db.get_inventory(user.id)
    sellable = get_all_sellable()
    harvest_counts = {}
    food_counts = {}
    animal_counts = {}

    for item in inventory:
        if item["item_type"] == "harvest" and item["quantity"] > 0:
            if item["item_name"] in sellable:
                harvest_counts[item["item_name"]] = item["quantity"]
        elif item["item_type"] == "food" and item["quantity"] > 0:
            if item["item_name"] in sellable:
                food_counts[item["item_name"]] = item["quantity"]
        elif item["item_type"] == "animal_produce" and item["quantity"] > 0:
            if item["item_name"] in sellable:
                animal_counts[item["item_name"]] = item["quantity"]

    text = "💰 <b>Sell Items</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "Select an item to sell:\n"

    sell_buttons = []
    row = []

    # Show harvest items
    for item_name, qty in sorted(harvest_counts.items()):
        if item_name in sellable:
            info = sellable[item_name]
            row.append(
                InlineKeyboardButton(
                    text=f"{info['emoji']} {get_item_display_name(item_name)} ({qty})",
                    callback_data=f"sell:item:{item_name}",
                )
            )
            if len(row) == 2:
                sell_buttons.append(row)
                row = []

    # Show food items
    for item_name, qty in sorted(food_counts.items()):
        if item_name in sellable:
            info = sellable[item_name]
            row.append(
                InlineKeyboardButton(
                    text=f"{info['emoji']} {get_item_display_name(item_name)} ({qty})",
                    callback_data=f"sell:item:{item_name}",
                )
            )
            if len(row) == 2:
                sell_buttons.append(row)
                row = []

    # Show animal produce items
    for item_name, qty in sorted(animal_counts.items()):
        if item_name in sellable:
            info = sellable[item_name]
            row.append(
                InlineKeyboardButton(
                    text=f"{info['emoji']} {get_item_display_name(item_name)} ({qty})",
                    callback_data=f"sell:item:{item_name}",
                )
            )
            if len(row) == 2:
                sell_buttons.append(row)
                row = []

    if row:
        sell_buttons.append(row)

    if not sell_buttons:
        text += "\n<i>Nothing to sell!</i>"
        await message.reply(text)
        return

    # Add sell all button
    sell_buttons.append([
        InlineKeyboardButton(
            text="💰 Sell All", callback_data="sellall:confirm"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=sell_buttons)
    await message.reply(text, reply_markup=keyboard)


async def _handle_sell_args(message: Message, db: Database, args: list, user):
    """Handle /sell command with arguments for backward compatibility."""
    sellable = get_all_sellable()

    if args[0].lower() == "all":
        # Show confirmation for sell all
        inventory = await db.get_inventory(user.id)
        total_value = 0
        items_text = []

        for item in inventory:
            if (
                item["item_type"] in ("harvest", "food", "animal_produce")
                and item["quantity"] > 0
            ):
                name = item["item_name"]
                if name in sellable:
                    info = sellable[name]
                    price = info["sell_price"] * item["quantity"]
                    total_value += price
                    items_text.append(
                        f"  {get_crop_display_emoji(name)} {item['quantity']}x {name} = {format_price(price)}"
                    )

        if total_value == 0:
            await message.reply("❌ Nothing to sell!")
            return

        text = "💰 <b>Confirm Sell All</b>\n\n"
        text += "\n".join(items_text)
        text += f"\n\n<b>Total: {format_price(total_value)}</b>"

        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="✅ Confirm Sell All",
                        callback_data="sellall:confirm",
                    ),
                    InlineKeyboardButton(
                        text="❌ Cancel", callback_data="sell:cancel"
                    ),
                ]
            ]
        )
        await message.reply(text, reply_markup=keyboard)
        return

    raw_name, _qty = parse_item_and_qty(args, all_value=999999)
    has_qty_token = any(p.isdigit() or p.lower() in ("a", "all") for p in args)
    amount = _qty if has_qty_token else None  # None means sell all available
    item_name = resolve_item_key(raw_name) or raw_name.strip().lower().replace(
        " ", "_"
    )

    if item_name not in sellable:
        await message.reply(
            f"❌ Can't sell: {get_item_display_name(item_name)}"
        )
        return

    # Check harvest, food, and animal_produce inventory
    have_harvest = await db.get_inventory_item(user.id, "harvest", item_name)
    have_food = await db.get_inventory_item(user.id, "food", item_name)
    have_animal = await db.get_inventory_item(
        user.id, "animal_produce", item_name
    )
    total_have = have_harvest + have_food + have_animal

    if total_have <= 0:
        display = get_item_display_name(item_name)
        await message.reply(f"❌ You don't have any {display}!")
        return

    to_sell = amount if amount and amount <= total_have else total_have
    info = sellable[item_name]
    total_price = info["sell_price"] * to_sell

    # Sell from harvest first, then food, then animal_produce
    remaining = to_sell
    for itype, have in [
        ("harvest", have_harvest),
        ("food", have_food),
        ("animal_produce", have_animal),
    ]:
        if remaining <= 0:
            break
        take = min(remaining, have)
        if take > 0:
            await db.remove_inventory_item(user.id, itype, item_name, take)
            remaining -= take

    # Add the earned money to wallet
    await db.add_balance(user.id, total_price, f"Sold {item_name}")

    commission = await maybe_pay_tomato_soup_commission(
        db, item_name, total_price
    )

    text = "💰 <b>Sale Complete!</b>\n\n"
    text += f"Sold {to_sell}x {get_crop_display_emoji(item_name)} {get_item_display_name(item_name)}\n"
    text += f"💰 Earned: {format_price(total_price)}"
    if commission:
        text += f"\n\n<i>{get_crop_display_emoji('tomato_soup')} {format_price(commission)} commission paid to the brand owner of {get_item_display_name(item_name)}</i>"
    await message.reply(text)


@client.on_callback_query(filters.regex(r"^" + "sell:item:"))
async def handle_sell_item_callback(
    callback: CallbackQuery,
):
    """Handle sell item selection - show quantity buttons."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await safe_callback_answer(callback, "Invalid action")
        return

    item_name = parts[2]
    user_id = callback.from_user.id

    sellable = get_all_sellable()
    if item_name not in sellable:
        await safe_callback_answer(callback, "Invalid item!", show_alert=True)
        return

    info = sellable[item_name]

    # Check harvest, food, and animal_produce inventory
    have_harvest = await db.get_inventory_item(user_id, "harvest", item_name)
    have_food = await db.get_inventory_item(user_id, "food", item_name)
    have_animal = await db.get_inventory_item(
        user_id, "animal_produce", item_name
    )
    total_have = have_harvest + have_food + have_animal

    if total_have <= 0:
        await safe_callback_answer(
            callback, "You don't have this item!", show_alert=True
        )
        return

    text = f"💰 <b>Sell {get_crop_display_emoji(item_name)} {item_name}</b>\n\n"
    text += f"You have: {total_have}\n"
    text += f"Price: {format_price(info['sell_price'])} each\n"
    text += "Select quantity:\n"

    # Quantity buttons
    buttons = []
    qty_options = [1, 3, 5, 10, 20, 50]
    row = []
    for qty in qty_options:
        if qty <= total_have:
            row.append(
                InlineKeyboardButton(
                    text=f"×{qty} = {format_price(info['sell_price'] * qty)}",
                    callback_data=f"sell:confirm:{item_name}:{qty}",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)

    # Max button (cap at 300)
    max_qty = min(total_have, 300)
    if max_qty > 0 and max_qty not in qty_options:
        buttons.append([
            InlineKeyboardButton(
                text=f"Max ({max_qty}) = {format_price(info['sell_price'] * max_qty)}",
                callback_data=f"sell:confirm:{item_name}:{max_qty}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="« Back", callback_data="sell:back")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "sell:confirm:"))
async def handle_sell_confirm_callback(
    callback: CallbackQuery,
):
    """Handle sell confirmation."""
    parts = callback.data.split(":")
    if len(parts) < 5:
        await safe_callback_answer(callback, "Invalid action")
        return

    item_name = parts[2]
    to_sell = int(parts[3])
    user_id = callback.from_user.id

    sellable = get_all_sellable()
    if item_name not in sellable:
        await safe_callback_answer(callback, "Invalid item!", show_alert=True)
        return

    # Check harvest, food, and animal_produce inventory
    have_harvest = await db.get_inventory_item(user_id, "harvest", item_name)
    have_food = await db.get_inventory_item(user_id, "food", item_name)
    have_animal = await db.get_inventory_item(
        user_id, "animal_produce", item_name
    )
    total_have = have_harvest + have_food + have_animal

    if total_have < to_sell:
        await safe_callback_answer(
            callback, f"Not enough {item_name}!", show_alert=True
        )
        return

    info = sellable[item_name]
    total_price = info["sell_price"] * to_sell

    # Sell from harvest first, then food, then animal_produce
    remaining = to_sell
    for itype, have in [
        ("harvest", have_harvest),
        ("food", have_food),
        ("animal_produce", have_animal),
    ]:
        if remaining <= 0:
            break
        take = min(remaining, have)
        if take > 0:
            await db.remove_inventory_item(user_id, itype, item_name, take)
            remaining -= take

    await db.add_balance(user_id, total_price, f"Sold {item_name}")
    commission = await maybe_pay_tomato_soup_commission(
        db, item_name, total_price
    )

    text = "💰 <b>Sale Complete!</b>\n\n"
    text += f"Sold {to_sell}x {get_crop_display_emoji(item_name)} {get_item_display_name(item_name)}\n"
    text += f"💰 Earned: {format_price(total_price)}"
    if commission:
        text += f"\n\n<i>{get_crop_display_emoji('tomato_soup')} {format_price(commission)} commission paid to the brand owner of {get_item_display_name(item_name)}</i>"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="« Back to Sell", callback_data="sell:back"
                )
            ]
        ]
    )
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "sell:back" + r"$"))
async def handle_sell_back_callback(
    callback: CallbackQuery,
):
    """Go back to sell menu."""
    user_id = callback.from_user.id

    inventory = await db.get_inventory(user_id)
    sellable = get_all_sellable()
    harvest_counts = {}
    food_counts = {}
    animal_counts = {}

    for item in inventory:
        if item["item_type"] == "harvest" and item["quantity"] > 0:
            if item["item_name"] in sellable:
                harvest_counts[item["item_name"]] = item["quantity"]
        elif item["item_type"] == "food" and item["quantity"] > 0:
            if item["item_name"] in sellable:
                food_counts[item["item_name"]] = item["quantity"]
        elif item["item_type"] == "animal_produce" and item["quantity"] > 0:
            if item["item_name"] in sellable:
                animal_counts[item["item_name"]] = item["quantity"]

    text = "💰 <b>Sell Items</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "Select an item to sell:\n"

    sell_buttons = []
    row = []

    for item_name, qty in sorted(harvest_counts.items()):
        if item_name in sellable:
            info = sellable[item_name]
            row.append(
                InlineKeyboardButton(
                    text=f"{info['emoji']} {item_name} ({qty})",
                    callback_data=f"sell:item:{item_name}",
                )
            )
            if len(row) == 2:
                sell_buttons.append(row)
                row = []

    for item_name, qty in sorted(food_counts.items()):
        if item_name in sellable:
            info = sellable[item_name]
            row.append(
                InlineKeyboardButton(
                    text=f"{info['emoji']} {item_name} ({qty})",
                    callback_data=f"sell:item:{item_name}",
                )
            )
            if len(row) == 2:
                sell_buttons.append(row)
                row = []

    for item_name, qty in sorted(animal_counts.items()):
        if item_name in sellable:
            info = sellable[item_name]
            row.append(
                InlineKeyboardButton(
                    text=f"{info['emoji']} {item_name} ({qty})",
                    callback_data=f"sell:item:{item_name}",
                )
            )
            if len(row) == 2:
                sell_buttons.append(row)
                row = []

    if row:
        sell_buttons.append(row)

    if not sell_buttons:
        text += "\n<i>Nothing to sell!</i>"
        await queue_it(
            lambda: callback.message.edit_text(text), callback.message.chat
        )
        await safe_callback_answer(callback)
        return

    sell_buttons.append([
        InlineKeyboardButton(
            text="💰 Sell All", callback_data="sellall:confirm"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=sell_buttons)
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


@client.on_callback_query(filters.regex(r"^" + "sellall:confirm" + r"$"))
async def handle_sell_all_callback(
    callback: CallbackQuery,
):
    """Handle sell all confirmation."""
    user_id = callback.from_user.id

    inventory = await db.get_inventory(user_id)
    sellable = get_all_sellable()
    total_earned = 0
    total_commission = 0
    sold_items = []

    for item in inventory:
        if (
            item["item_type"] in ("harvest", "food", "animal_produce")
            and item["quantity"] > 0
        ):
            name = item["item_name"]
            if name in sellable:
                qty = item["quantity"]
                price = sellable[name]["sell_price"] * qty
                await db.remove_inventory_item(
                    user_id, item["item_type"], name, qty
                )
                total_earned += price
                commission = await maybe_pay_tomato_soup_commission(
                    db, name, price
                )
                total_commission += commission
                sold_items.append(
                    f"{get_crop_display_emoji(name)} {qty}x {get_item_display_name(name)}"
                )

    if total_earned > 0:
        await db.add_balance(user_id, total_earned, "Sold all items")
        text = (
            f"💰 <b>Sold everything for {format_price(total_earned)}!</b>\n\n"
        )
        text += "\n".join(sold_items)
        if total_commission:
            text += f"\n\n<i>{get_crop_display_emoji('tomato_soup')} {format_price(total_commission)} commission paid to the brand owner of {get_item_display_name('tomato_soup')}</i>"
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [InlineKeyboardButton(text="« Back", callback_data="sell:back")]
            ]
        )
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=keyboard),
            callback.message.chat,
        )
    else:
        await queue_it(
            lambda: callback.message.edit_text("❌ Nothing to sell!"),
            callback.message.chat,
        )
    await safe_callback_answer(callback)


reg("fertilize", "🌿 Fertilize someone's garden")


@client.on_message(filters.command(["fertilize", "ft"]))
async def fertilize_command(message: Message, bot: Bot, config: Config):
    """Fertilize someone's garden to speed up their crops."""
    from bot.plugins.family import get_target_user, reply_cannot_target_bot

    user = message.from_user

    # Try to get target from reply, mention, or user ID
    target, is_bot = await get_target_user(bot, message, db)
    if is_bot:
        await reply_cannot_target_bot(message)
        return

    if not target:
        chat = message.chat

        # Only makes sense in a group
        if chat.type == ChatType.PRIVATE:
            await message.reply(
                "🌿 <b>Fertilize</b>\n\n"
                "Run this command in a group to see members' active farms!\n\n"
                "<code>/ft @username</code> or <code>/ft 123456</code> to fertilize directly."
            )
            return

        text = "🌿 <b>Fertilize</b>\n\n"
        text += "Reply to someone, mention @user, or use user ID to fertilize their garden!\n"
        text += "<code>/ft @username</code> or <code>/ft 123456</code>\n\n"

        member_ids, is_partial = await _get_group_member_ids(bot, chat.id)

        if not member_ids:
            text += "<i>Couldn't fetch group members.</i>"
            await message.reply(text)
            return

        # Safe: all values are explicitly int()-cast, no user input
        ids_str = ",".join(str(int(i)) for i in member_ids if i != user.id)

        users_with_gardens = (
            await db.fetch(
                f"""
            SELECT DISTINCT u.user_id, u.username, u.first_name,
                   (SELECT COUNT(*) FROM garden_plots gp
                    WHERE gp.garden_id = g.id
                    AND gp.crop_type IS NOT NULL
                    AND gp.is_ready = FALSE) as growing_count
            FROM users u
            JOIN gardens g ON g.owner_id = u.user_id
            JOIN garden_plots gp ON gp.garden_id = g.id
            WHERE gp.crop_type IS NOT NULL
            AND gp.is_ready = FALSE
            AND u.user_id != $1
            AND u.user_id IN ({ids_str})
            GROUP BY u.user_id, u.username, u.first_name, g.id
            HAVING COUNT(*) > 0
            LIMIT 15
            """,
                user.id,
            )
            if ids_str
            else []
        )

        if users_with_gardens:
            label = (
                "👮 Admins with growing plants (member list hidden):"
                if is_partial
                else "🌱 Group members with growing plants:"
            )
            text += f"<blockquote><b>{label}</b>\n"
            users_with_gardens.sort(
                key=lambda x: x["growing_count"], reverse=True
            )
            for u in users_with_gardens:
                name = u["first_name"] or u["username"] or "Unknown"
                text += f"• {name} — {u['growing_count']} plants\n"
            text += "</blockquote>\n"
            if is_partial:
                text += "<i>⚠️ Only admins visible — bot needs admin rights to see all members.</i>\n"
            text += "\n<i>Reply to their message or mention them!</i>"
        else:
            text += "<i>No group members have growing plants right now.</i>"

        await message.reply(text)
        return

    # Can't fertilize yourself
    if target.id == user.id:
        await message.reply("❌ You can't fertilize your own garden!")
        return

    # Check fertilize ban
    ban_expiry = await db.get_fertilize_ban(user.id)
    if ban_expiry:
        delta = ban_expiry - datetime.now()
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        await message.reply(
            f"🚫 You've been banned from /ft for bot-like behaviour.\n"
            f"⏳ Ban expires in: <b>{hours}h {minutes}m</b>\n\n"
            f"If you think this is a mistake, use /feedback to request an unblock."
        )
        return

    # Check if target can receive fertilizes
    target_ban_expiry = await db.get_fertilize_receive_ban(target.id)
    if target_ban_expiry:
        delta = target_ban_expiry - datetime.now()
        hours = int(delta.total_seconds() // 3600)
        minutes = int((delta.total_seconds() % 3600) // 60)
        await message.reply(
            f"🚫 {user_mention(target)} can't receive /ft right now due to bot-like /ft received from the same user.\n"
            f"⏳ Ban expires in: <b>{hours}h {minutes}m</b>"
        )
        return

    # Check cooldown
    cooldown_remaining = await db.get_fertilize_cooldown_remaining(
        user.id, target.id
    )
    if cooldown_remaining:
        minutes = cooldown_remaining // 60
        seconds = cooldown_remaining % 60
        await message.reply(
            f"❌ You already fertilized recently!\n"
            f"⏳ Try again in: <b>{minutes}m {seconds}s</b>"
        )
        return

    # Get target's garden
    target_garden = await db.get_garden(target.id)
    if not target_garden:
        await message.reply(f"❌ {user_mention(target)} doesn't have a garden!")
        return

    # Refresh target's is_ready flags so the growing-plants check uses truth
    # (otherwise stale flags can let people /ft a garden whose crops finished).
    await update_garden_ready_status(db, target_garden["id"])

    # Get my garden (for bonus calculation)
    my_garden = await db.get_or_create_garden(user.id)
    reward_per_plant = calculate_garden_fertilize_bonus(my_garden["size"])

    # Count growing plants
    plots = await db.get_garden_plots(target_garden["id"])
    growing_plants = [p for p in plots if p["crop_type"] and not p["is_ready"]]

    if not growing_plants:
        await message.reply(
            f"❌ {user_mention(target)}'s garden has no growing plants!"
        )
        return

    # Fully grow every still-growing plot
    await db.mark_plots_ready(
        target_garden["id"], [p["position"] for p in growing_plants]
    )

    # Calculate reward
    reward = reward_per_plant * len(growing_plants)
    await db.add_balance(
        user.id, reward, f"Fertilized {target.first_name}'s garden"
    )

    # Record cooldown
    await db.record_fertilize(user.id, target.id)

    # Auto-ban check: if user hits bot threshold within the detection window
    recent_count = await db.count_recent_fertilizes(
        user.id, FERTILIZE_BOT_WINDOW_HOURS
    )
    if recent_count >= FERTILIZE_BOT_THRESHOLD:
        ban_until = datetime.now() + timedelta(days=FERTILIZE_BOT_BAN_DAYS)
        await db.set_fertilize_ban(
            user.id, ban_until, fertilize_count=recent_count, reason="auto"
        )
        receive_ban_triggered = False
        receive_ban_count = 0
        (
            total_count,
            top_target_id,
            top_target_count,
        ) = await db.get_recent_fertilize_target_stats(
            user.id, FERTILIZE_RECEIVE_BOT_WINDOW_HOURS
        )
        if (
            top_target_id == target.id
            and total_count >= FERTILIZE_RECEIVE_BOT_THRESHOLD
            and top_target_count == total_count
        ):
            receive_ban_triggered = True
            receive_ban_count = total_count
            receive_ban_until = datetime.now() + timedelta(
                days=FERTILIZE_RECEIVE_BOT_BAN_DAYS
            )
            await db.set_fertilize_receive_ban(
                target.id,
                receive_ban_until,
                fertilize_count=receive_ban_count,
                reason="auto",
            )
        try:
            username_str = (
                f"@{user.username}" if user.username else "no username"
            )
            breakdown = await db.get_recent_fertilize_target_breakdown(
                user.id, FERTILIZE_BOT_REPORT_WINDOW_HOURS
            )
            lines = []
            for idx, row in enumerate(breakdown, start=1):
                target_id = row["target_id"]
                target_username = row.get("username")
                target_name = (
                    row.get("first_name") or target_username or str(target_id)
                )
                target_username_str = (
                    f"@{target_username}" if target_username else "no username"
                )
                count = row["cnt"]
                times = "time" if count == 1 else "times"
                lines.append(
                    f"{idx}. Ft'd {mention_html(target_id, target_name)} "
                    f"(ID: <code>{target_id}</code>) "
                    f"(Username: {target_username_str}) : {count} {times}"
                )
            analysis = (
                "\n".join(lines) if lines else "<i>No /ft activity found.</i>"
            )
            owner_text = (
                f"🚫 <b>Triggered ft-ban</b>\n"
                f"User: {user_mention(user)} (ID: <code>{user.id}</code>) "
                f"(Username: {username_str})\n"
                f"Reason: {recent_count} fertilizes in {FERTILIZE_BOT_WINDOW_HOURS}h.\n\n"
                f"Last {FERTILIZE_BOT_REPORT_WINDOW_HOURS}h /ft analysis:\n"
                f"{analysis}"
            )
            await bot.send_message(config.owner_id, owner_text)
        except Exception:
            pass
        response = (
            f"🌿 Fertilized {user_mention(target)}'s garden!\n\n"
            f"🌾 Fully grew {len(growing_plants)} plants!\n"
            f"💰 Earned: {format_price(reward)}\n\n"
            f"🚫 <b>Auto-ban:</b> You've been banned from /ft for {FERTILIZE_BOT_BAN_DAYS} days "
            f"due to suspicious activity ({recent_count} fertilizes in {FERTILIZE_BOT_WINDOW_HOURS}h).\n"
            f"Use /feedback to request an unblock."
        )
        if receive_ban_triggered:
            response += (
                f"\n\n🚫 <b>Auto-ban:</b> {user_mention(target)} can't receive /ft for "
                f"{FERTILIZE_RECEIVE_BOT_BAN_DAYS} days due to bot-like /ft received from the same user "
                f"({receive_ban_count} fertilizes in {FERTILIZE_RECEIVE_BOT_WINDOW_HOURS}h)."
            )
        await message.reply(response)
        return

    response = (
        f"🌿 Fertilized {user_mention(target)}'s garden!\n\n"
        f"🌾 Fully grew {len(growing_plants)} plants!\n"
        f"💰 Earned: {format_price(reward)}"
    )
    await message.reply(response)

    # Notify target with a simple message (no garden UI in DM)
    try:
        await bot.send_message(
            target.id,
            f"🌿 {user_mention(user)} fertilized your garden!\n"
            f"🌾 {len(growing_plants)} plants are fully grown and ready to harvest!",
        )
    except Exception:
        pass


reg("eat", "🍽️ Eat a food item from your inventory [/eat <item> [amount]]")


@client.on_message(filters.command(["eat"]))
async def eat_command(message: Message):
    """Eat food from inventory. Usage: /eat <item> [amount]"""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    args = message.text.split()[1:] if message.text else []

    if not args:
        # Show all eatables (FOODS with feed_value > 0)
        inventory = await db.get_inventory(user.id)
        food_items = [
            i
            for i in inventory
            if i["item_type"] == "food"
            and i["quantity"] > 0
            and i["item_name"] in FOODS
        ]
        if not food_items:
            await message.reply(
                "🍽️ You have no food to eat!\nCook something at a machine first."
            )
            return
        text = "🍽️ <b>Your Food</b>\n\nUse <code>/eat &lt;item&gt; [amount]</code>\n\n"
        for i in food_items:
            name = i["item_name"]
            info = FOODS[name]
            emoji = get_crop_display_emoji(name)
            bonus = info["feed_value"] * 20
            text += (
                f"{emoji} <b>{get_item_display_name(name)}</b> ×{i['quantity']}"
            )
            if info["feed_value"] > 0:
                text += f" — +${bonus:,} coins"
            text += "\n"
        await message.reply(text)
        return

    last = args[-1]
    if len(args) > 1 and last.isdigit():
        raw_name = " ".join(args[:-1])
        amount = int(last)
    else:
        raw_name = " ".join(args)
        amount = 1

    item_name = resolve_item_key(raw_name) or raw_name.strip().lower().replace(
        " ", "_"
    )
    if item_name not in FOODS:
        display = get_item_display_name(item_name)
        await message.reply(f"❌ <b>{display}</b> is not a food item!")
        return

    have = await db.get_inventory_item(user.id, "food", item_name)
    if have <= 0:
        await message.reply(
            f"❌ You don't have any {get_item_display_name(item_name)}!"
        )
        return

    to_eat = min(amount, have)
    await db.remove_inventory_item(user.id, "food", item_name, to_eat)

    info = FOODS[item_name]
    emoji = get_crop_display_emoji(item_name)
    display = get_item_display_name(item_name)
    coin_bonus = info["feed_value"] * 20 * to_eat
    if coin_bonus > 0:
        await db.add_balance(user.id, coin_bonus, f"Ate {to_eat}x {item_name}")
        text = (
            f"{emoji} <b>Nom nom!</b>\n\n"
            f"Ate {to_eat}x <b>{display}</b>\n"
            f"💰 Got +{format_price(coin_bonus)} from the energy boost!"
        )
    else:
        text = f"{emoji} <b>Nom nom!</b>\n\nAte {to_eat}x <b>{display}</b>"

    await message.reply(text)


reg("catalog", "📖 View crop prices")


def _catalog_crop_text() -> str:
    text = "📖 <b>Crop Catalog</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "<blockquote expandable>"
    text += "<b>🥕 Vegetables</b>\n"
    for name, info in CROPS.items():
        emoji = get_crop_display_emoji(name)
        text += f"{emoji} <b>{get_item_display_name(name)}</b> | "
        text += f"Seed: {format_price(info['seed_cost'])} | "
        text += f"Sell: {format_price(info['sell_price'])} | "
        text += f"⏱️{format_time(info['grow_time'])} | "
        text += f"Yield: {info['yield_min']}-{info['yield_max']}\n"
    text += "</blockquote>\n"
    text += "<blockquote expandable>"
    text += "<b>🍎 Fruits</b>\n"
    for name, info in FRUITS.items():
        emoji = get_crop_display_emoji(name)
        text += f"{emoji} <b>{get_item_display_name(name)}</b> | "
        text += f"Seed: {format_price(info['seed_cost'])} | "
        text += f"Sell: {format_price(info['sell_price'])} | "
        text += f"⏱️{format_time(info['grow_time'])} | "
        text += f"Yield: {info['yield_min']}-{info['yield_max']}\n"
    text += "</blockquote>\n"
    text += "<blockquote expandable>"
    text += "<b>🌸 Flowers</b>\n"
    for name, info in FLOWERS.items():
        emoji = get_crop_display_emoji(name)
        text += f"{emoji} <b>{get_item_display_name(name)}</b> | "
        text += f"Seed: {format_price(info['seed_cost'])} | "
        text += f"Sell: {format_price(info['sell_price'])} | "
        text += f"⏱️{format_time(info['grow_time'])} | "
        text += f"Yield: {info['yield_min']}-{info['yield_max']}\n"
    text += "</blockquote>\n"
    text += "\n<i>Use /shop to buy seeds and machines!</i>"
    return text


def _catalog_food_text() -> str:
    text = "🍽️ <b>Food Catalog</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "<blockquote expandable>"
    text += "<b>🍔 Cooked Foods</b>\n"
    for name, info in FOODS.items():
        text += (
            f"{info['emoji']} <b>{get_item_display_name(name)}</b> - "
            f"Sell: {format_price(info['sell_price'])} | "
            f"Feed: -{info['feed_value']}% fatigue\n"
        )
    text += "</blockquote>\n"
    text += "\n<i>Cook ingredients to create food items!</i>"
    return text


@client.on_message(filters.command(["catalog"]))
async def catalog_command(message: Message):
    """Show catalog of crops with button to switch to food catalog."""
    uid = message.from_user.id
    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🍽️ View Food Catalog",
                    callback_data=f"catalog:food:{uid}",
                )
            ]
        ]
    )
    text = _catalog_crop_text()
    await message.reply(text, reply_markup=keyboard)


@client.on_callback_query(filters.regex(r"^catalog:(food|crops):\d+$"))
async def handle_catalog_callback(callback: CallbackQuery):
    _, view, uid_str = callback.data.split(":")
    if callback.from_user.id != int(uid_str):
        await safe_callback_answer(
            callback, "This isn't your catalog!", show_alert=True
        )
        return
    uid = int(uid_str)
    if view == "food":
        text = _catalog_food_text()
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🌾 View Crop Catalog",
                        callback_data=f"catalog:crops:{uid}",
                    )
                ]
            ]
        )
    else:
        text = _catalog_crop_text()
        keyboard = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="🍽️ View Food Catalog",
                        callback_data=f"catalog:food:{uid}",
                    )
                ]
            ]
        )
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await safe_callback_answer(callback)


# ============ GARDEN CALLBACKS ============


@client.on_callback_query(filters.regex(r"^" + "garden:"))
async def handle_garden_callback(
    callback: CallbackQuery,
):
    """Handle garden button callbacks."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await safe_callback_answer(callback, "Invalid action")
        return

    garden_id = int(parts[1])
    action = parts[2]

    # Get garden
    garden = await db.fetchrow("SELECT * FROM gardens WHERE id = $1", garden_id)

    if not garden:
        await safe_callback_answer(
            callback, "Garden not found!", show_alert=True
        )
        return

    # Extract multiplier from action
    multiplier = 1
    if len(parts) >= 4 and parts[3].isdigit():
        multiplier = int(parts[3])

    user_id = callback.from_user.id
    is_owner = garden["owner_id"] == user_id

    try:
        if action == "refresh":
            text, keyboard = await get_garden_display(
                db, garden["owner_id"], garden, multiplier=multiplier
            )
            if not is_owner:
                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="🔄 Refresh",
                                callback_data=f"garden:{garden_id}:refresh:1",
                            )
                        ]
                    ]
                )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await safe_callback_answer(callback, "Refreshed!")

        elif action == "mult" and is_owner:
            text, keyboard = await get_garden_display(
                db, user_id, garden, multiplier=multiplier
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await safe_callback_answer(callback, f"Multiplier: {multiplier}x")

        elif action == "buy" and is_owner:
            text, keyboard = await get_garden_display(
                db, user_id, garden, mode="buy", multiplier=multiplier
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await safe_callback_answer(callback)

        elif action == "plant" and is_owner:
            text, keyboard = await get_garden_display(
                db, user_id, garden, mode="plant", multiplier=multiplier
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await safe_callback_answer(callback)

        elif action == "sell" and is_owner:
            text, keyboard = await get_garden_display(
                db, user_id, garden, mode="sell", multiplier=multiplier
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await safe_callback_answer(callback)

        elif action == "back" and is_owner:
            text, keyboard = await get_garden_display(
                db, user_id, garden, multiplier=multiplier
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await safe_callback_answer(callback)

        elif action == "buyqty" and is_owner:
            crop_name = parts[3] if len(parts) > 3 else None
            if crop_name:
                text, keyboard = await get_garden_display(
                    db, user_id, garden, mode="buyqty", selected_crop=crop_name
                )
                await queue_it(
                    lambda: callback.message.edit_text(
                        text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )
            await safe_callback_answer(callback)

        elif action == "plantqty" and is_owner:
            crop_name = parts[3] if len(parts) > 3 else None
            if crop_name:
                text, keyboard = await get_garden_display(
                    db,
                    user_id,
                    garden,
                    mode="plantqty",
                    selected_crop=crop_name,
                )
                await queue_it(
                    lambda: callback.message.edit_text(
                        text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )
            await safe_callback_answer(callback)

        elif action == "sellqty" and is_owner:
            crop_name = parts[3] if len(parts) > 3 else None
            if crop_name:
                text, keyboard = await get_garden_display(
                    db, user_id, garden, mode="sellqty", selected_crop=crop_name
                )
                await queue_it(
                    lambda: callback.message.edit_text(
                        text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )
            await safe_callback_answer(callback)

        elif action == "harvest" and is_owner:
            async with _get_harvest_lock(user_id):
                await update_garden_ready_status(db, garden_id)
                plots = await db.get_garden_plots(garden_id)

                harvested = {}
                for plot in plots:
                    if plot["is_ready"]:
                        crop_type = await db.harvest_plot(
                            garden_id, plot["position"]
                        )
                        if crop_type and crop_type in ALL_PLANTABLE:
                            info = ALL_PLANTABLE[crop_type]
                            yield_amount = random.randint(
                                info["yield_min"], info["yield_max"]
                            )
                            await db.add_inventory_item(
                                user_id, "harvest", crop_type, yield_amount
                            )
                            harvested[crop_type] = (
                                harvested.get(crop_type, 0) + yield_amount
                            )

                if harvested:
                    await db.increment_garden_harvests(
                        user_id, sum(harvested.values())
                    )
                    summary = ", ".join(
                        f"{get_crop_emoji(k)} +{v}"
                        for k, v in harvested.items()
                    )
                    from bot.achievements import check_garden_achievements

                    await check_garden_achievements(db, user_id)
                    await safe_callback_answer(
                        callback, f"Harvested: {summary}", show_alert=True
                    )
                else:
                    await safe_callback_answer(callback, "Nothing to harvest!")

                text, keyboard = await get_garden_display(db, user_id, garden)
                await queue_it(
                    lambda: callback.message.edit_text(
                        text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )

        elif action == "buycrop" and is_owner:
            crop_name = parts[3]
            mult = int(parts[4]) if len(parts) > 4 else 1

            if crop_name not in ALL_PLANTABLE:
                await safe_callback_answer(
                    callback, "Invalid crop!", show_alert=True
                )
                return

            info = ALL_PLANTABLE[crop_name]
            cost = info["seed_cost"] * mult

            wallet = await db.get_wallet(user_id)
            if wallet["balance"] < cost:
                await safe_callback_answer(
                    callback, f"Need {format_price(cost)}!", show_alert=True
                )
                return

            await db.add_balance(
                user_id, -cost, f"Bought {mult}x {crop_name} seeds"
            )
            await db.add_inventory_item(user_id, "seed", crop_name, mult)

            await safe_callback_answer(
                callback,
                f"Bought {mult}x {info['emoji']} seeds for {format_price(cost)}",
            )

            # Go back to quantity selection for the same crop
            text, keyboard = await get_garden_display(
                db, user_id, garden, mode="buyqty", selected_crop=crop_name
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )

        elif action == "plantcrop" and is_owner:
            crop_name = parts[3]
            mult = int(parts[4]) if len(parts) > 4 else 1

            if crop_name not in ALL_PLANTABLE:
                await safe_callback_answer(
                    callback, "Invalid crop!", show_alert=True
                )
                return

            # Check seeds
            seeds = await db.get_inventory_item(user_id, "seed", crop_name)
            if seeds < mult:
                await safe_callback_answer(
                    callback, f"Only {seeds} seeds!", show_alert=True
                )
                return

            # Find empty plots
            plots = await db.get_garden_plots(garden_id)
            empty_plots = [p for p in plots if not p["crop_type"]]

            if not empty_plots:
                await safe_callback_answer(
                    callback, "No empty plots!", show_alert=True
                )
                return

            # Plant
            to_plant = min(mult, len(empty_plots), seeds)
            planted = 0
            for plot in empty_plots[:to_plant]:
                if await db.plant_crop(garden_id, plot["position"], crop_name):
                    planted += 1

            if planted > 0:
                await db.remove_inventory_item(
                    user_id, "seed", crop_name, planted
                )
                emoji = get_crop_emoji(crop_name)
                await safe_callback_answer(
                    callback, f"Planted {planted}x {emoji}!"
                )

            # Go back to quantity selection for the same crop
            text, keyboard = await get_garden_display(
                db, user_id, garden, mode="plantqty", selected_crop=crop_name
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )

        elif action == "sellcrop" and is_owner:
            crop_name = parts[3]
            mult = int(parts[4]) if len(parts) > 4 else 1

            sellable = get_all_sellable()
            if crop_name not in sellable:
                await safe_callback_answer(
                    callback, "Can't sell that!", show_alert=True
                )
                return

            # Check inventory
            qty = await db.get_inventory_item(user_id, "harvest", crop_name)
            to_sell = min(mult, qty)

            if to_sell <= 0:
                await safe_callback_answer(
                    callback, "Nothing to sell!", show_alert=True
                )
                return

            info = sellable[crop_name]
            total = info["sell_price"] * to_sell

            await db.remove_inventory_item(
                user_id, "harvest", crop_name, to_sell
            )
            await db.add_balance(user_id, total, f"Sold {crop_name}")

            await safe_callback_answer(
                callback,
                f"Sold {to_sell}x {info['emoji']} for {format_price(total)}",
            )

            # Go back to quantity selection for the same crop
            text, keyboard = await get_garden_display(
                db, user_id, garden, mode="sellqty", selected_crop=crop_name
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )

        elif action == "expand" and is_owner:
            current_size = garden["size"]
            if current_size >= GARDEN_MAX_SIZE:
                await safe_callback_answer(
                    callback, "Garden at max size!", show_alert=True
                )
                return

            expand_cost = GARDEN_EXPANSION_COSTS.get(current_size + 1, 75000)
            wallet = await db.get_wallet(user_id)

            if wallet["balance"] < expand_cost:
                await safe_callback_answer(
                    callback,
                    f"Need {format_price(expand_cost)}!",
                    show_alert=True,
                )
                return

            await db.add_balance(user_id, -expand_cost, "Garden expansion")
            await db.expand_garden(garden_id, current_size + 1)
            from bot.achievements import check_garden_achievements

            await check_garden_achievements(db, user_id)

            garden = await db.fetchrow(
                "SELECT * FROM gardens WHERE id = $1", garden_id
            )

            await safe_callback_answer(
                callback,
                f"Garden expanded to {current_size + 1}×{current_size + 1}!",
            )

            text, keyboard = await get_garden_display(db, user_id, garden)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )

        elif not is_owner:
            await safe_callback_answer(
                callback, "This isn't your garden!", show_alert=True
            )

    except BadRequest:
        await safe_callback_answer(callback)


reg("guide_garden", "📖 Garden guide")


@client.on_message(filters.command(["guide_garden"]))
async def guide_garden_command(message: Message):
    """Show detailed garden guide."""
    text = "📖 <b>Garden Guide</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    text += "<blockquote expandable>"
    text += "<b>🌱 Getting Started</b>\n"
    text += "• Use /garden to view your garden\n"
    text += "• Buy seeds from the garden shop\n"
    text += "• Plant seeds in empty plots\n"
    text += "• Wait for crops to grow, then harvest!\n\n"

    text += "<b>🛒 Commands:</b>\n"
    text += "• /garden - View your garden\n"
    text += "• /plant [crop] [qty] - Plant seeds (or 'a' for all)\n"
    text += "• /harvest - Harvest ready crops\n"
    text += "• /sell [crop] [qty] - Sell crops\n"
    text += "• /fertilize @user - Speed up friend's crops\n"
    text += "• /ft @user - Short for fertilize\n"
    text += "• /catalog - View all crop prices\n\n"

    text += "<b>🌾 Crops Available:</b>\n"
    for crop_name, info in list(CROPS.items())[:5]:
        emoji = get_crop_display_emoji(crop_name)
        text += f"  {emoji} {get_item_display_name(crop_name)}: "
        text += f"${info['seed_cost']} → ${info['sell_price']}\n"
    text += "  ...and more! Check /catalog\n\n"

    text += "<b>🍎 Fruits Available:</b>\n"
    for fruit_name, info in list(FRUITS.items())[:3]:
        emoji = get_crop_display_emoji(fruit_name)
        text += f"  {emoji} {get_item_display_name(fruit_name)}: "
        text += f"${info['seed_cost']} → ${info['sell_price']}\n"
    text += "  ...and more! Check /catalog\n\n"

    text += "<b>💡 Tips:</b>\n"
    text += "• Fertilizing fully grows a friend's growing crops\n"
    text += "• Higher value crops take longer to grow\n"
    text += "• Expand your garden for more plots\n"
    text += "• Use multipliers (x1-x50) for bulk actions\n"
    text += "</blockquote>"

    await message.reply(text)
