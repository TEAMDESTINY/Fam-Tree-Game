"""Shop, cooking, and marketplace - buy machines, cook food, trade with players."""

import html
from collections import Counter, defaultdict

from pyrogram import filters
from pyrogram.errors import BadRequest
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.client import client
from bot.command_registry import reg
from bot.constants import (
    ALL_PLANTABLE,
    FOODS,
    MACHINES,
    format_price,
    get_all_sellable,
    get_crop_display_emoji,
    get_crop_emoji,
    get_ingredient_type,
    get_item_display_name,
    parse_item_and_qty,
    resolve_item_key,
)
from bot.database import Database, db
from bot.queue_it import queue_it

# ============ SHOP ============


reg("shop", "🛒 Browse the shop")


@client.on_message(filters.command(["shop"]))
async def shop_command(
    message: Message,
):
    """View the shop."""
    text = "🛒 <b>Shop</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"
    text += "Select a category to browse:\n"

    buttons = [
        [
            InlineKeyboardButton(text="🌱 Seeds", callback_data="shop:seeds:1"),
            InlineKeyboardButton(
                text="⚙️ Machines", callback_data="shop:machines:1"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🐄 Animal Pens", callback_data="shop:animals:1"
            ),
            InlineKeyboardButton(
                text="🏪 Marketplace", callback_data="shop:market:1"
            ),
        ],
        [
            InlineKeyboardButton(
                text="🛡️ Security", callback_data="shop:security:1"
            ),
        ],
    ]

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await message.reply(text, reply_markup=keyboard)


@client.on_callback_query(filters.regex(r"^" + "shop:"))
async def handle_shop_callback(
    callback: CallbackQuery,
):
    """Handle shop navigation callbacks."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid action")
        return

    category = parts[1]
    page = int(parts[2]) if parts[2].isdigit() else 1

    user_id = callback.from_user.id

    try:
        if category == "seeds":
            text = "🌱 <b>Seeds & Saplings</b>\n\n"
            text += "Click to buy seeds:\n"

            buttons = []
            items = list(ALL_PLANTABLE.items())
            per_page = 10
            start = (page - 1) * per_page
            end = start + per_page
            page_items = items[start:end]

            for name, info in page_items:
                buttons.append([
                    InlineKeyboardButton(
                        text=f"{info['emoji']} {get_item_display_name(name)} - {format_price(info['seed_cost'])}",
                        callback_data=f"shopqty:seed:{name}",
                    )
                ])

            # Pagination
            nav_row = []
            if page > 1:
                nav_row.append(
                    InlineKeyboardButton(
                        text="« Prev", callback_data=f"shop:seeds:{page - 1}"
                    )
                )
            if end < len(items):
                nav_row.append(
                    InlineKeyboardButton(
                        text="Next »", callback_data=f"shop:seeds:{page + 1}"
                    )
                )
            if nav_row:
                buttons.append(nav_row)

            buttons.append([
                InlineKeyboardButton(text="« Back", callback_data="shop:main:1")
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

        elif category == "machines":
            # Get user's owned machines
            owned = await db.get_user_machines(user_id)
            owned_types = {m["machine_type"] for m in owned}

            text = "⚙️ <b>Machines</b>\n\n"
            text += "Buy machines to cook food:\n\n"

            buttons = []
            for machine_name, info in MACHINES.items():
                if machine_name not in owned_types:
                    buttons.append([
                        InlineKeyboardButton(
                            text=f"{info['emoji']} {info['name']} - {format_price(info['cost'])}",
                            callback_data=f"shopbuy:machine:{machine_name}:1",
                        )
                    ])
                else:
                    text += f"✅ {info['emoji']} {info['name']} (owned)\n"

            if not buttons:
                text += "\n<i>You own all machines!</i>"

            buttons.append([
                InlineKeyboardButton(text="« Back", callback_data="shop:main:1")
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

        elif category == "market":
            # Show marketplace listings
            listings, total = await db.get_listings(page=page)

            text = "🏪 <b>Marketplace</b>\n\n"
            text += "<i>Listing IDs (e.g. #1) can be used with /market buy #ID</i>\n\n"

            buttons = []

            if listings:
                for listing in listings:
                    seller_name = (
                        listing["first_name"]
                        or listing["username"]
                        or "Unknown"
                    )
                    emoji = get_crop_emoji(listing["item_name"])
                    listing_id = listing["id"]
                    total_price = listing["quantity"] * listing["price_each"]

                    # Blockquote expandable format
                    text += "<blockquote expandable>"
                    text += f"<b>#{listing_id}</b> - {emoji} {listing['quantity']}x {get_item_display_name(listing['item_name'])}\n"
                    text += f"💰 {format_price(listing['price_each'])}/ea (Total: {format_price(total_price)})\n"
                    text += f"👤 {html.escape(seller_name)}"
                    text += "</blockquote>\n"

                    # Add buy button for each listing
                    buttons.append([
                        InlineKeyboardButton(
                            text=f"Buy #{listing_id} - {emoji} {listing['quantity']}x",
                            callback_data=f"marketbuy:{listing_id}",
                        )
                    ])
            else:
                text += "<i>No listings yet!</i>\n"

            text += "\n<i>Use /market to list your items</i>"

            # Pagination
            total_pages = (total + 9) // 10  # 10 per page for cleaner display
            nav_row = []
            if page > 1:
                nav_row.append(
                    InlineKeyboardButton(
                        text="« Prev", callback_data=f"shop:market:{page - 1}"
                    )
                )
            if page < total_pages:
                nav_row.append(
                    InlineKeyboardButton(
                        text="Next »", callback_data=f"shop:market:{page + 1}"
                    )
                )
            if nav_row:
                buttons.append(nav_row)

            buttons.append([
                InlineKeyboardButton(
                    text="🔄 Refresh", callback_data=f"shop:market:{page}"
                ),
                InlineKeyboardButton(
                    text="« Back", callback_data="shop:main:1"
                ),
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

        elif category == "security":
            from bot.constants import SECURITY_PERCENTAGE

            user_id = callback.from_user.id
            wallet = await db.get_wallet(user_id)

            # Get user's bank balance
            bank = await db.get_bank_balance(user_id)
            bank_balance = bank["balance"] if bank else 0

            # Calculate net worth
            net_worth = wallet["balance"] + bank_balance
            security_cost = max(100, int(net_worth * SECURITY_PERCENTAGE))

            # Check current security status
            user_security = await db.get_user_security(user_id)
            has_security = user_security and user_security.get(
                "is_active", False
            )

            text = "🛡️ <b>Security System</b>\n\n"
            text += "Protect your bank account from heists!\n\n"

            if has_security:
                text += "✅ <b>Status: ACTIVE</b>\n"
                text += "Your bank account is protected.\n\n"
                text += "<i>Security breaks when someone successfully heists you.</i>\n"
                text += "<i>You'll be notified via DM when it breaks.</i>\n"
            else:
                text += "❌ <b>Status: INACTIVE</b>\n\n"
                text += f"Cost: {format_price(security_cost)}\n"
                text += (
                    f"(0.25% of your net worth: {format_price(net_worth)})\n\n"
                )
                text += "Click below to buy:\n"

            buttons = []
            if not has_security:
                buttons.append([
                    InlineKeyboardButton(
                        text=f"🛡️ Buy Security - {format_price(security_cost)}",
                        callback_data=f"shopbuy:security:1:{security_cost}",
                    )
                ])

            buttons.append([
                InlineKeyboardButton(text="« Back", callback_data="shop:main:1")
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

        elif category == "animals":
            from bot.constants import ANIMAL_PENS

            user_pens = await db.get_user_pens(user_id)
            owned = {p["pen_type"]: p for p in user_pens}

            text = "🐄 <b>Animal Pens</b>\n\n"
            buttons = []
            for pen_key, pen in ANIMAL_PENS.items():
                if pen_key in owned:
                    lvl = owned[pen_key]["level"]
                    cap = pen["base_capacity"] + (lvl - 1) * 5
                    text += f"• {pen['emoji']} <b>{pen['name']}</b> — Level {lvl} ({cap} slots)\n"
                    if lvl < 3:
                        upgrade_cost = pen["upgrade_costs"][lvl + 1]
                        buttons.append([
                            InlineKeyboardButton(
                                text=f"{pen['emoji']} {pen['name']} Lv{lvl} → Upgrade (${upgrade_cost:,})",
                                callback_data=f"shop_animals:upgrade:{pen_key}",
                            )
                        ])
                    else:
                        buttons.append([
                            InlineKeyboardButton(
                                text=f"{pen['emoji']} {pen['name']} Lv{lvl} ✅ MAX",
                                callback_data=f"shop_animals:maxed:{pen_key}",
                            )
                        ])
                else:
                    text += f"• {pen['emoji']} <b>{pen['name']}</b> — ${pen['cost']:,}\n"
                    buttons.append([
                        InlineKeyboardButton(
                            text=f"{pen['emoji']} Buy {pen['name']} (${pen['cost']:,})",
                            callback_data=f"shop_animals:buy:{pen_key}",
                        )
                    ])
            buttons.append([
                InlineKeyboardButton(text="« Back", callback_data="shop:main:1")
            ])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

        elif category == "main":
            text = "🛒 <b>Shop</b>\n"
            text += "━━━━━━━━━━━━━━━━\n\n"
            text += "Select a category to browse:\n"

            buttons = [
                [
                    InlineKeyboardButton(
                        text="🌱 Seeds", callback_data="shop:seeds:1"
                    ),
                    InlineKeyboardButton(
                        text="⚙️ Machines", callback_data="shop:machines:1"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🐄 Animal Pens", callback_data="shop:animals:1"
                    ),
                    InlineKeyboardButton(
                        text="🏪 Marketplace", callback_data="shop:market:1"
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text="🛡️ Security", callback_data="shop:security:1"
                    ),
                ],
            ]

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

    except BadRequest:
        await callback.answer()


@client.on_callback_query(filters.regex(r"^shop_animals:"))
async def handle_shop_animals_callback(callback: CallbackQuery):
    """Handle animal pen buy/upgrade from shop."""
    from bot.constants import ANIMAL_PENS

    parts = callback.data.split(":")
    action = parts[1]  # buy | upgrade | maxed
    pen_key = parts[2]
    user_id = callback.from_user.id
    pen_info = ANIMAL_PENS[pen_key]

    if action == "maxed":
        await callback.answer("Already max level!", show_alert=True)
        return

    if action == "buy":
        existing = await db.get_pen_by_type(user_id, pen_key)
        if existing:
            await callback.answer("You already own this pen!", show_alert=True)
            return
        cost = pen_info["cost"]
        wallet = await db.get_wallet(user_id)
        if wallet["balance"] < cost:
            await callback.answer(
                f"Need ${cost:,} coins. You have ${wallet['balance']:,}.",
                show_alert=True,
            )
            return
        await db.add_balance(user_id, -cost, f"Bought {pen_info['name']}")
        await db.buy_animal_pen(user_id, pen_key)
        await callback.answer(
            f"✅ {pen_info['name']} purchased!", show_alert=True
        )

    elif action == "upgrade":
        pen = await db.get_pen_by_type(user_id, pen_key)
        if not pen:
            await callback.answer("You don't own this pen.", show_alert=True)
            return
        lvl = pen["level"]
        if lvl >= 3:
            await callback.answer("Already max level!", show_alert=True)
            return
        cost = pen_info["upgrade_costs"][lvl + 1]
        wallet = await db.get_wallet(user_id)
        if wallet["balance"] < cost:
            await callback.answer(
                f"Need ${cost:,} coins. You have ${wallet['balance']:,}.",
                show_alert=True,
            )
            return
        await db.add_balance(
            user_id, -cost, f"Upgraded {pen_info['name']} to Lv{lvl + 1}"
        )
        await db.upgrade_animal_pen(user_id, pen_key)
        await callback.answer(
            f"✅ Upgraded to Level {lvl + 1}!", show_alert=True
        )

    # Refresh animals shop view
    user_pens = await db.get_user_pens(user_id)
    owned = {p["pen_type"]: p for p in user_pens}
    text = "🐄 <b>Animal Pens</b>\n\n"
    buttons = []
    for pk, pen in ANIMAL_PENS.items():
        if pk in owned:
            lvl = owned[pk]["level"]
            cap = pen["base_capacity"] + (lvl - 1) * 5
            text += f"• {pen['emoji']} <b>{pen['name']}</b> — Level {lvl} ({cap} slots)\n"
            if lvl < 3:
                upgrade_cost = pen["upgrade_costs"][lvl + 1]
                buttons.append([
                    InlineKeyboardButton(
                        text=f"{pen['emoji']} {pen['name']} Lv{lvl} → Upgrade (${upgrade_cost:,})",
                        callback_data=f"shop_animals:upgrade:{pk}",
                    )
                ])
            else:
                buttons.append([
                    InlineKeyboardButton(
                        text=f"{pen['emoji']} {pen['name']} Lv{lvl} ✅ MAX",
                        callback_data=f"shop_animals:maxed:{pk}",
                    )
                ])
        else:
            text += (
                f"• {pen['emoji']} <b>{pen['name']}</b> — ${pen['cost']:,}\n"
            )
            buttons.append([
                InlineKeyboardButton(
                    text=f"{pen['emoji']} Buy {pen['name']} (${pen['cost']:,})",
                    callback_data=f"shop_animals:buy:{pk}",
                )
            ])
    buttons.append([
        InlineKeyboardButton(text="« Back", callback_data="shop:main:1")
    ])
    kb = InlineKeyboardMarkup(inline_keyboard=buttons)
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=kb),
        callback.message.chat,
    )


@client.on_callback_query(filters.regex(r"^" + "shopqty:"))
async def handle_shop_quantity_callback(
    callback: CallbackQuery,
):
    """Handle shop quantity selection for seeds."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid action")
        return

    item_type = parts[1]  # seed
    item_name = parts[2]

    try:
        if item_type == "seed":
            if item_name not in ALL_PLANTABLE:
                await callback.answer("Invalid seed!", show_alert=True)
                return

            info = ALL_PLANTABLE[item_name]
            cost = info["seed_cost"]

            # Get wallet balance to calculate max
            user_id = callback.from_user.id
            wallet = await db.get_wallet(user_id)
            max_affordable = wallet["balance"] // cost if cost > 0 else 0
            max_qty = min(max_affordable, 300)  # Cap at 300

            text = f"🌱 <b>Buy {info['emoji']} {get_item_display_name(item_name)} Seeds</b>\n\n"
            text += f"Price: {format_price(cost)} each\n"
            text += f"💰 Your balance: {format_price(wallet['balance'])}\n"
            text += f"📦 Max you can buy: {max_qty}\n"
            text += "Select quantity:\n"

            # Multipliers: 1, 3, 5, 10, 20, 50, 100, 200, 300, Max
            buttons = [
                [
                    InlineKeyboardButton(
                        text=f"×1 = {format_price(cost)}",
                        callback_data=f"shopbuy:seed:{item_name}:1",
                    ),
                    InlineKeyboardButton(
                        text=f"×3 = {format_price(cost * 3)}",
                        callback_data=f"shopbuy:seed:{item_name}:3",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=f"×5 = {format_price(cost * 5)}",
                        callback_data=f"shopbuy:seed:{item_name}:5",
                    ),
                    InlineKeyboardButton(
                        text=f"×10 = {format_price(cost * 10)}",
                        callback_data=f"shopbuy:seed:{item_name}:10",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=f"×20 = {format_price(cost * 20)}",
                        callback_data=f"shopbuy:seed:{item_name}:20",
                    ),
                    InlineKeyboardButton(
                        text=f"×50 = {format_price(cost * 50)}",
                        callback_data=f"shopbuy:seed:{item_name}:50",
                    ),
                ],
                [
                    InlineKeyboardButton(
                        text=f"×100 = {format_price(cost * 100)}",
                        callback_data=f"shopbuy:seed:{item_name}:100",
                    ),
                    InlineKeyboardButton(
                        text=f"×200 = {format_price(cost * 200)}",
                        callback_data=f"shopbuy:seed:{item_name}:200",
                    ),
                ],
            ]

            # Add max button if affordable
            if max_qty > 0:
                buttons.append([
                    InlineKeyboardButton(
                        text=f"Max ({max_qty}) = {format_price(cost * max_qty)}",
                        callback_data=f"shopbuy:seed:{item_name}:{max_qty}",
                    )
                ])

            buttons.append([
                InlineKeyboardButton(
                    text="« Back",
                    callback_data="shop:seeds:1",
                )
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

    except BadRequest:
        await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "shopbuy:"))
async def handle_shop_buy_callback(
    callback: CallbackQuery,
):
    """Handle shop purchase callbacks with confirmation."""
    parts = callback.data.split(":")
    if len(parts) < 4:
        await callback.answer("Invalid action")
        return

    item_type = parts[1]  # seed, machine
    item_name = parts[2]
    quantity = int(parts[3]) if parts[3].isdigit() else 1
    confirmed = len(parts) > 4 and parts[4] == "confirm"

    user_id = callback.from_user.id

    try:
        if item_type == "seed":
            if item_name not in ALL_PLANTABLE:
                await callback.answer("Invalid seed!", show_alert=True)
                return

            info = ALL_PLANTABLE[item_name]
            cost = info["seed_cost"] * quantity

            if not confirmed:
                # Show confirmation
                text = "🛒 <b>Confirm Purchase</b>\n\n"
                text += f"Buy {quantity}x {info['emoji']} <b>{get_item_display_name(item_name)}</b> seeds?\n"
                text += f"💰 Cost: {format_price(cost)}"

                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Confirm",
                                callback_data=f"shopbuy:seed:{item_name}:{quantity}:confirm",
                            ),
                            InlineKeyboardButton(
                                text="❌ Cancel",
                                callback_data="shop:seeds:1",
                            ),
                        ]
                    ]
                )
                await queue_it(
                    lambda: callback.message.edit_text(
                        text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )
                await callback.answer()
                return

            # Confirmed purchase
            wallet = await db.get_wallet(user_id)
            if wallet["balance"] < cost:
                await callback.answer(
                    f"Need {format_price(cost)}!", show_alert=True
                )
                return

            await db.add_balance(
                user_id, -cost, f"Bought {quantity}x {item_name} seeds"
            )
            await db.add_inventory_item(user_id, "seed", item_name, quantity)

            # Edit message to show purchase result
            text = "✅ <b>Purchase Successful!</b>\n\n"
            text += f"Bought {quantity}x {get_crop_display_emoji(item_name)} <b>{get_item_display_name(item_name)}</b> seeds\n"
            text += f"💰 Cost: {format_price(cost)}"

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Back to Seeds",
                            callback_data="shop:seeds:1",
                        )
                    ]
                ]
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

        elif item_type == "machine":
            if item_name not in MACHINES:
                await callback.answer("Invalid machine!", show_alert=True)
                return

            info = MACHINES[item_name]

            # Check if already owned
            if await db.has_machine(user_id, item_name):
                await callback.answer("You already own this!", show_alert=True)
                return

            if not confirmed:
                # Show confirmation
                text = "🛒 <b>Confirm Purchase</b>\n\n"
                text += f"Buy {info['emoji']} <b>{info['name']}</b>?\n"
                text += f"💰 Cost: {format_price(info['cost'])}\n"
                text += "🍳 Recipes:\n"
                for recipe in info["recipes"]:
                    food = FOODS[recipe["produces"]]
                    ing_str = ", ".join(
                        f"{v}x {get_item_display_name(k)}"
                        for k, v in recipe["ingredients"].items()
                    )
                    text += f"  {food['emoji']} {recipe['name']}: {ing_str}\n"

                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Confirm",
                                callback_data=f"shopbuy:machine:{item_name}:1:confirm",
                            ),
                            InlineKeyboardButton(
                                text="❌ Cancel",
                                callback_data="shop:machines:1",
                            ),
                        ]
                    ]
                )
                await queue_it(
                    lambda: callback.message.edit_text(
                        text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )
                await callback.answer()
                return

            # Confirmed purchase
            wallet = await db.get_wallet(user_id)
            if wallet["balance"] < info["cost"]:
                await callback.answer(
                    f"Need {format_price(info['cost'])}!", show_alert=True
                )
                return

            await db.add_balance(
                user_id, -info["cost"], f"Bought {info['name']}"
            )
            await db.buy_machine(user_id, item_name)

            # Edit message to show purchase result
            text = "✅ <b>Purchase Successful!</b>\n\n"
            text += f"Bought {info['emoji']} <b>{info['name']}</b>\n"
            text += f"💰 Cost: {format_price(info['cost'])}\n"
            text += "🍳 Recipes:\n"
            for recipe in info["recipes"]:
                food = FOODS[recipe["produces"]]
                ing_str = ", ".join(
                    f"{v}x {get_item_display_name(k)}"
                    for k, v in recipe["ingredients"].items()
                )
                text += f"  {food['emoji']} {recipe['name']}: {ing_str}\n"

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Back to Machines",
                            callback_data="shop:machines:1",
                        )
                    ]
                ]
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

        elif item_type == "security":
            # Security system purchase
            cost = int(parts[3]) if len(parts) > 3 else 0

            if not confirmed:
                # Show confirmation
                text = "🛒 <b>Confirm Purchase</b>\n\n"
                text += "Buy 🛡️ Security System?\n"
                text += f"💰 Cost: {format_price(cost)}\n\n"
                text += "<i>Protects your bank account from heists!</i>\n"
                text += "<i>Price is 0.25% of your net worth.</i>"

                keyboard = InlineKeyboardMarkup(
                    inline_keyboard=[
                        [
                            InlineKeyboardButton(
                                text="✅ Confirm",
                                callback_data=f"shopbuy:security:1:{cost}:confirm",
                            ),
                            InlineKeyboardButton(
                                text="❌ Cancel",
                                callback_data="shop:security:1",
                            ),
                        ]
                    ]
                )
                await queue_it(
                    lambda: callback.message.edit_text(
                        text, reply_markup=keyboard
                    ),
                    callback.message.chat,
                )
                await callback.answer()
                return

            # Confirmed purchase
            wallet = await db.get_wallet(user_id)
            if wallet["balance"] < cost:
                await callback.answer(
                    f"Need {format_price(cost)}!", show_alert=True
                )
                return

            # Buy security system
            await db.buy_security(user_id, cost)

            # Edit message to show purchase result
            text = "✅ <b>Purchase Successful!</b>\n\n"
            text += "Bought 🛡️ Security System\n"
            text += f"💰 Cost: {format_price(cost)}\n\n"
            text += "<i>Your bank account is now protected from heists!</i>\n"
            text += "<i>If someone successfully heists you, your security will break and you'll be notified via DM.</i>"

            keyboard = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="« Back to Security",
                            callback_data="shop:security:1",
                        )
                    ]
                ]
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()

    except BadRequest:
        await callback.answer()


# ============ COOKING ============


reg("cook", "👨‍🍳 Cook food")


def _build_food_recipe_index() -> dict:
    index: dict[str, dict] = {}
    for machine_type, info in MACHINES.items():
        for recipe_idx, recipe in enumerate(info["recipes"]):
            index[recipe["produces"]] = {
                "machine_type": machine_type,
                "recipe_idx": recipe_idx,
                "recipe": recipe,
            }
    return index


FOOD_RECIPES = _build_food_recipe_index()


def _format_item_counts(counts: dict[str, int]) -> str:
    parts: list[str] = []
    for item, qty in sorted(counts.items()):
        if qty <= 0:
            continue
        emoji = get_crop_display_emoji(item)
        parts.append(f"{emoji} {get_item_display_name(item)} x{qty}")
    return ", ".join(parts)


def _format_item_bullets(counts: dict[str, int]) -> list[str]:
    lines: list[str] = []
    for item, qty in sorted(counts.items()):
        if qty <= 0:
            continue
        emoji = get_crop_display_emoji(item)
        lines.append(f"• {emoji} {get_item_display_name(item)} x{qty}")
    return lines


def _parse_cook_targets(
    raw_args: str,
) -> tuple[dict[str, int], list[str], list[str]]:
    segments = [
        seg.strip()
        for seg in raw_args.replace("\n", ",").split(",")
        if seg.strip()
    ]
    targets: dict[str, int] = {}
    unknown: list[str] = []
    invalid: list[str] = []
    for segment in segments:
        name, qty = parse_item_and_qty(segment.split())
        if not name:
            invalid.append(segment)
            continue
        item_key = resolve_item_key(name)
        if not item_key or item_key not in FOOD_RECIPES:
            unknown.append(name)
            continue
        if qty < 1:
            invalid.append(segment)
            continue
        targets[item_key] = targets.get(item_key, 0) + qty
    return targets, unknown, invalid


def _build_cook_order(plan: dict[str, int]) -> list[str]:
    order: list[str] = []
    visited: set[str] = set()

    def visit(food_key: str) -> None:
        if food_key in visited:
            return
        visited.add(food_key)
        recipe = FOOD_RECIPES[food_key]["recipe"]
        for ing in recipe["ingredients"]:
            if ing in FOOD_RECIPES and plan.get(ing, 0) > 0:
                visit(ing)
        order.append(food_key)

    for food_key in plan:
        if plan[food_key] > 0:
            visit(food_key)
    return order


def _plan_cook_targets(
    targets: dict[str, int],
    inventory: dict[tuple[str, str], int],
    machines_owned: set[str],
) -> tuple[
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[str, int],
    dict[tuple[str, str], int],
    dict[str, set[str]],
]:
    plan: Counter[str] = Counter()
    prereq_plan: Counter[str] = Counter()
    used_existing_foods: Counter[str] = Counter()
    missing_foods: Counter[str] = Counter()
    missing_base: Counter[tuple[str, str]] = Counter()
    missing_machines: dict[str, set[str]] = defaultdict(set)

    def require_ingredient(item_key: str, qty: int) -> None:
        if qty <= 0:
            return
        item_type = get_ingredient_type(item_key)
        if item_type == "food":
            require_food(item_key, qty)
            return
        have = inventory.get((item_type, item_key), 0)
        used = min(have, qty)
        if used:
            inventory[(item_type, item_key)] = have - used
        remaining = qty - used
        if remaining > 0:
            missing_base[(item_type, item_key)] += remaining

    def require_food(food_key: str, qty: int) -> None:
        if qty <= 0:
            return
        recipe_entry = FOOD_RECIPES.get(food_key)
        if not recipe_entry:
            missing_foods[food_key] += qty
            return
        machine_type = recipe_entry["machine_type"]
        if machine_type not in machines_owned:
            missing_machines[machine_type].add(food_key)
        have = inventory.get(("food", food_key), 0)
        used = min(have, qty)
        if used:
            inventory[("food", food_key)] = have - used
            used_existing_foods[food_key] += used
        remaining = qty - used
        if remaining <= 0:
            return
        prereq_plan[food_key] += remaining
        missing_foods[food_key] += remaining
        ingredients = recipe_entry["recipe"]["ingredients"]
        for ing, needed in ingredients.items():
            require_ingredient(ing, needed * remaining)

    for food_key, qty in targets.items():
        recipe_entry = FOOD_RECIPES.get(food_key)
        if not recipe_entry:
            continue
        machine_type = recipe_entry["machine_type"]
        if machine_type not in machines_owned:
            missing_machines[machine_type].add(food_key)
        ingredients = recipe_entry["recipe"]["ingredients"]
        for ing, needed in ingredients.items():
            require_ingredient(ing, needed * qty)
        plan[food_key] += qty

    full_plan = dict(plan)
    for food_key, qty in prereq_plan.items():
        full_plan[food_key] = full_plan.get(food_key, 0) + qty

    return (
        full_plan,
        dict(prereq_plan),
        dict(used_existing_foods),
        dict(missing_foods),
        dict(missing_base),
        dict(missing_machines),
    )


async def _cook_recipe(
    user_id: int, food_key: str, qty: int
) -> tuple[bool, str | None]:
    recipe_entry = FOOD_RECIPES.get(food_key)
    if not recipe_entry:
        return False, f"Unknown recipe for {get_item_display_name(food_key)}"
    ingredients = recipe_entry["recipe"]["ingredients"]
    consumed: list[tuple[str, str, int]] = []
    for ing, needed in ingredients.items():
        ing_type = get_ingredient_type(ing)
        ok = await db.remove_inventory_item(
            user_id, ing_type, ing, needed * qty
        )
        if not ok:
            for r_ing, r_type, r_amt in consumed:
                await db.add_inventory_item(user_id, r_type, r_ing, r_amt)
            ing_emoji = get_crop_emoji(ing)
            return (
                False,
                f"Cook failed: insufficient {ing_emoji} {get_item_display_name(ing)}",
            )
        consumed.append((ing, ing_type, needed * qty))
    await db.add_inventory_item(user_id, "food", food_key, qty)
    return True, None


async def _handle_direct_cook(
    message, raw_args: str, machines: list[dict]
) -> bool:
    user = message.from_user
    targets, unknown, invalid = _parse_cook_targets(raw_args)
    if unknown or invalid or not targets:
        lines: list[str] = []
        if unknown:
            lines.append(f"❌ Unknown cookable item(s): {', '.join(unknown)}")
        if invalid:
            lines.append(f"❌ Invalid quantity in: {', '.join(invalid)}")
        if not lines:
            lines.append("❌ No valid cook targets found.")
        await message.reply("\n".join(lines))
        return True

    inventory_items = await db.get_inventory(user.id)
    inventory = {
        (item.item_type, item.item_name): item.quantity
        for item in inventory_items
    }
    machines_owned = {machine["machine_type"] for machine in machines}

    (
        full_plan,
        prereq_plan,
        used_existing_foods,
        _missing_foods,
        missing_base,
        missing_machines,
    ) = _plan_cook_targets(targets, inventory, machines_owned)

    missing_harvest: dict[str, int] = {}
    missing_animal: dict[str, int] = {}
    missing_feed: dict[str, int] = {}
    for (item_type, item_key), qty in missing_base.items():
        if item_type == "harvest":
            missing_harvest[item_key] = qty
        elif item_type == "animal_produce":
            missing_animal[item_key] = qty
        elif item_type == "feed":
            missing_feed[item_key] = qty

    if missing_machines or missing_base:
        headline = "❌ Not enough ingredients to cook"
        if missing_machines and not missing_base:
            headline = "❌ Unable to cook"
        lines = [f"{headline} {_format_item_counts(targets)}."]
        lines.append("<blockquote>")
        if missing_machines:
            machine_parts: list[str] = []
            for machine_type, foods in missing_machines.items():
                info = MACHINES[machine_type]
                food_list = ", ".join(
                    get_item_display_name(food) for food in sorted(foods)
                )
                if food_list:
                    machine_parts.append(
                        f"{info['emoji']} {info['name']} (for {food_list})"
                    )
                else:
                    machine_parts.append(f"{info['emoji']} {info['name']}")
            lines.append(f"Missing machines: {', '.join(machine_parts)}")
        if prereq_plan:
            lines.append("Missing prerequisite foods:")
            lines.extend(_format_item_bullets(prereq_plan))
        if missing_harvest:
            lines.append("Need to grow/harvest:")
            lines.extend(_format_item_bullets(missing_harvest))
        if missing_animal:
            lines.append("Need animal produce:")
            lines.extend(_format_item_bullets(missing_animal))
        if missing_feed:
            lines.append("Need feed:")
            lines.extend(_format_item_bullets(missing_feed))
        lines.append("</blockquote>")
        await message.reply("\n".join(lines))
        return True

    cook_order = _build_cook_order(full_plan)
    for food_key in cook_order:
        qty = full_plan.get(food_key, 0)
        if qty <= 0:
            continue
        ok, err = await _cook_recipe(user.id, food_key, qty)
        if not ok:
            await message.reply(f"❌ {err}")
            return True

    lines = [f"✅ Cooked {_format_item_counts(targets)}."]
    details: list[str] = []
    if prereq_plan:
        details.append(
            f"Auto-crafted prerequisites: {_format_item_counts(prereq_plan)}"
        )
    if used_existing_foods:
        details.append(
            f"Used existing prerequisites: {_format_item_counts(used_existing_foods)}"
        )
    if details:
        lines.append("")
        lines.append("<blockquote>")
        lines.extend(details)
        lines.append("</blockquote>")
    await message.reply("\n".join(lines))
    return True


def _build_cook_menu() -> tuple[str, InlineKeyboardMarkup]:
    text = "👨‍🍳 <b>Cooking</b>\n━━━━━━━━━━━━━━━━\n\nHow do you want to browse recipes?\n"
    kb = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📋 All Items", callback_data="cook:view:all"
                ),
                InlineKeyboardButton(
                    text="🔧 By Machine", callback_data="cook:view:machines"
                ),
            ]
        ]
    )
    return text, kb


async def _build_all_recipes_view(user_id: int) -> tuple[str, list]:
    """Flat grid of all cookable recipes across owned machines, 2 per row."""
    machines = await db.get_user_machines(user_id)
    text = "👨‍🍳 <b>Cooking — All Items</b>\n━━━━━━━━━━━━━━━━\n\nSelect what to cook:\n"
    buttons: list = []
    row: list = []
    for machine in machines:
        mt = machine["machine_type"]
        if mt not in MACHINES:
            continue
        for i, recipe in enumerate(MACHINES[mt]["recipes"]):
            food = FOODS[recipe["produces"]]
            row.append(
                InlineKeyboardButton(
                    icon_custom_emoji_id=food.get("custom_emoji_id"),
                    text=f"{food['emoji']} {recipe['name']}"
                    if not food.get("custom_emoji_id")
                    else f"{recipe['name']}",
                    callback_data=f"cook:{mt}:{i}:qty",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)
    buttons.append([
        InlineKeyboardButton(text="« Back", callback_data="cook:menu")
    ])
    return text, buttons


async def _build_machine_list_view(user_id: int) -> tuple[str, list]:
    """List of owned machines, one per row."""
    machines = await db.get_user_machines(user_id)
    text = "👨‍🍳 <b>Cooking — By Machine</b>\n━━━━━━━━━━━━━━━━\n\nSelect a machine:\n"
    buttons: list = []
    for machine in machines:
        mt = machine["machine_type"]
        if mt not in MACHINES:
            continue
        m_info = MACHINES[mt]
        buttons.append([
            InlineKeyboardButton(
                text=f"{m_info['emoji']} {m_info['name']} ({len(m_info['recipes'])} recipes)",
                callback_data=f"cook:{mt}:recipes",
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="« Back", callback_data="cook:menu")
    ])
    return text, buttons


async def _build_cook_qty_picker(
    user_id: int, machine_type: str, recipe_idx: int
):
    """Build the cook quantity-picker text + keyboard for a recipe."""
    info = MACHINES[machine_type]
    recipe = info["recipes"][recipe_idx]
    food = FOODS[recipe["produces"]]
    ingredients = recipe["ingredients"]
    food_html_emoji = get_crop_display_emoji(recipe["produces"])

    text = f"👨‍🍳 <b>{info['name']} → {recipe['name']}</b>\n\n"
    text += "<b>Recipe (per item):</b>\n"

    max_cookable = 999_999
    for ing, needed in ingredients.items():
        ing_type = get_ingredient_type(ing)
        have = await db.get_inventory_item(user_id, ing_type, ing)
        status = "✅" if have >= needed else "❌"
        ing_emoji = get_crop_display_emoji(ing)
        text += (
            f"  {status} {ing_emoji} {needed}x {get_item_display_name(ing)}"
            f" (have: {have})\n"
        )
        max_cookable = min(max_cookable, have // needed if needed > 0 else 0)

    text += (
        f"\n{food_html_emoji} Sells for {format_price(food['sell_price'])} each"
    )
    text += "\n\n<b>How many to cook?</b>"

    buttons: list = []
    row: list = []
    for qty in [1, 5, 10, 50, 100, 500, 1000]:
        if qty <= max_cookable:
            row.append(
                InlineKeyboardButton(
                    text=f"{qty}x",
                    callback_data=f"cook:{machine_type}:{recipe_idx}:{qty}:confirm",
                )
            )
            if len(row) == 2:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)

    if max_cookable > 0 and max_cookable not in [1, 5, 10, 50, 100, 500, 1000]:
        buttons.append([
            InlineKeyboardButton(
                text=f"Max ({max_cookable})x",
                callback_data=f"cook:{machine_type}:{recipe_idx}:{max_cookable}:confirm",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="« Back", callback_data="cook:menu")
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


@client.on_message(filters.command(["cook"]))
async def cook_command(
    message: Message,
):
    """Cook food using your machines."""
    user = message.from_user
    if not user:
        return
    machines = await db.get_user_machines(user.id)
    if not machines:
        await message.reply(
            "❌ You don't have any cooking machines!\n\n"
            "Use /shop to buy machines like:\n"
            "🍿 Popcorn Machine\n🍟 Deep Fryer\n🍔 Grill"
        )
        return

    if message.text:
        parts = message.text.split(maxsplit=1)
        if len(parts) > 1 and parts[1].strip():
            handled = await _handle_direct_cook(
                message, parts[1].strip(), machines
            )
            if handled:
                return

    text, kb = _build_cook_menu()
    await message.reply(text, reply_markup=kb)


@client.on_callback_query(filters.regex(r"^cook:"))
async def handle_cook_callback(
    callback: CallbackQuery,
):
    """Handle cooking callbacks.

    Callback data formats:
      cook:back:0                          → machine list
      cook:{machine}:recipes               → recipe list for machine
      cook:{machine}:{ridx}:qty            → quantity picker for recipe
      cook:{machine}:{ridx}:{qty}:confirm  → execute cook
    """
    parts = callback.data.split(":")
    user_id = callback.from_user.id

    try:
        # ── main menu ─────────────────────────────────────────────────
        if parts[1] == "menu":
            text, kb = _build_cook_menu()
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=kb),
                callback.message.chat,
            )
            await callback.answer()
            return

        # ── view toggle ───────────────────────────────────────────────
        if parts[1] == "view":
            view = parts[2]
            if view == "all":
                text, buttons = await _build_all_recipes_view(user_id)
            else:
                text, buttons = await _build_machine_list_view(user_id)
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()
            return

        machine_type = parts[1]

        if machine_type not in MACHINES:
            await callback.answer("Invalid machine!", show_alert=True)
            return
        if not await db.has_machine(user_id, machine_type):
            await callback.answer(
                "You don't own this machine!", show_alert=True
            )
            return

        info = MACHINES[machine_type]

        # ── recipe list ───────────────────────────────────────────────
        if len(parts) == 3 and parts[2] == "recipes":
            text = f"👨‍🍳 <b>{info['name']}</b>\n"
            text += "━━━━━━━━━━━━━━━━\n\n"
            text += "Select a recipe:\n"
            buttons = []
            for i, recipe in enumerate(info["recipes"]):
                food = FOODS[recipe["produces"]]
                food_html_emoji = get_crop_display_emoji(recipe["produces"])
                text += f"\n{food_html_emoji} <b>{recipe['name']}</b> → {format_price(food['sell_price'])}\n"
                for ing, qty in recipe["ingredients"].items():
                    ing_emoji = get_crop_display_emoji(ing)
                    text += (
                        f"  {ing_emoji} {qty}x {get_item_display_name(ing)}\n"
                    )
                buttons.append([
                    InlineKeyboardButton(
                        icon_custom_emoji_id=food.get("custom_emoji_id"),
                        text=f"{food['emoji']} {recipe['name']}"
                        if not food.get("custom_emoji_id")
                        else f"{recipe['name']}",
                        callback_data=f"cook:{machine_type}:{i}:qty",
                    )
                ])
            buttons.append([
                InlineKeyboardButton(
                    text="« Back", callback_data="cook:view:machines"
                )
            ])
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()
            return

        # ── quantity picker ───────────────────────────────────────────
        if len(parts) == 4 and parts[3] == "qty":
            recipe_idx = int(parts[2])
            text, keyboard = await _build_cook_qty_picker(
                user_id, machine_type, recipe_idx
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()
            return

        # ── execute cook ──────────────────────────────────────────────
        if len(parts) == 5 and parts[4] == "confirm":
            recipe_idx = int(parts[2])
            qty = int(parts[3])
            recipe = info["recipes"][recipe_idx]
            produces = recipe["produces"]
            ingredients = recipe["ingredients"]

            # Verify ingredients
            for ing, needed in ingredients.items():
                total_needed = needed * qty
                ing_type = get_ingredient_type(ing)
                have = await db.get_inventory_item(user_id, ing_type, ing)
                if have < total_needed:
                    ing_emoji = get_crop_emoji(ing)
                    await callback.answer(
                        f"Need {total_needed}x {ing_emoji} {get_item_display_name(ing)}, have {have}!",
                        show_alert=True,
                    )
                    return

            # Consume ingredients — abort and rollback if any removal fails
            consumed: list[tuple[str, str, int]] = []
            for ing, needed in ingredients.items():
                ing_type = get_ingredient_type(ing)
                ok = await db.remove_inventory_item(
                    user_id, ing_type, ing, needed * qty
                )
                if not ok:
                    for r_ing, r_type, r_amt in consumed:
                        await db.add_inventory_item(
                            user_id, r_type, r_ing, r_amt
                        )
                    ing_emoji = get_crop_emoji(ing)
                    await callback.answer(
                        f"Cook failed: insufficient {ing_emoji} {get_item_display_name(ing)}",
                        show_alert=True,
                    )
                    return
                consumed.append((ing, ing_type, needed * qty))

            # Produce food
            await db.add_inventory_item(user_id, "food", produces, qty)

            food_emoji = FOODS[produces]["emoji"]
            await callback.answer(
                f"Cooked {qty}x {food_emoji} {get_item_display_name(produces)}!",
                show_alert=True,
            )

            # Stay on the cook screen — redraw quantity picker with refreshed numbers.
            text, keyboard = await _build_cook_qty_picker(
                user_id, machine_type, recipe_idx
            )
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            return

        await callback.answer("Invalid action")

    except BadRequest:
        await callback.answer()


# ============ MARKETPLACE ============


reg("market", "🏪 Marketplace listings")


@client.on_message(filters.command(["market"]))
async def market_command(
    message: Message,
):
    """Manage your marketplace listings."""
    user = message.from_user
    args = message.text.split()[1:] if message.text else []

    await db.upsert_user(user.id, user.username, user.first_name)

    # If action specified via command (e.g. /market sell apple 10 15)
    if args:
        await _handle_market_action(message, db, args, user)
        return

    # Show interactive marketplace menu
    listings = await db.get_user_listings(user.id)
    inventory = await db.get_inventory(user.id)
    harvest_counts = {}
    food_counts = {}
    for item in inventory:
        if item["item_type"] == "harvest" and item["quantity"] > 0:
            harvest_counts[item["item_name"]] = item["quantity"]
        elif item["item_type"] == "food" and item["quantity"] > 0:
            food_counts[item["item_name"]] = item["quantity"]

    text = "🏪 <b>Marketplace</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    text += "<b>Quick Commands:</b>\n"
    text += (
        "• <code>/market sell &lt;item&gt; &lt;qty&gt; &lt;price&gt;</code>\n"
    )
    text += "• <code>/market cancel &lt;id&gt;</code>\n"
    text += "• Or use buttons below for interactive selling\n\n"

    if listings:
        text += "<b>Your active listings:</b>\n"
        for listing in listings:
            emoji = get_crop_emoji(listing["item_name"])
            text += f"  #{listing['id']} {emoji} {listing['quantity']}x {listing['item_name']} "
            text += f"@ {format_price(listing['price_each'])}/ea\n"
    else:
        text += "<i>No active listings</i>\n"

    # Build sellable items buttons (crops + foods)
    sellable = get_all_sellable()
    sell_buttons = []
    row = []

    # Show harvest items first
    for item_name, qty in sorted(harvest_counts.items()):
        if item_name in sellable:
            info = sellable[item_name]
            row.append(
                InlineKeyboardButton(
                    text=f"{info['emoji']} {get_item_display_name(item_name)} ({qty})",
                    callback_data=f"market:sellqty:{item_name}",
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
                    callback_data=f"market:sellqty:{item_name}",
                )
            )
            if len(row) == 2:
                sell_buttons.append(row)
                row = []

    if row:
        sell_buttons.append(row)

    if sell_buttons:
        sell_buttons.append([
            InlineKeyboardButton(
                text="🏪 View All Listings", callback_data="shop:market:1"
            )
        ])

    keyboard = (
        InlineKeyboardMarkup(inline_keyboard=sell_buttons)
        if sell_buttons
        else None
    )
    await message.reply(text, reply_markup=keyboard)


async def _handle_market_action(
    message: Message, db: Database, args: list, user
):
    """Handle market action from command args."""
    action = args[0].lower()

    if action == "sell":
        if len(args) < 4:
            await message.reply(
                "❌ Usage: <code>/market sell &lt;crop&gt; &lt;qty&gt; &lt;price_each&gt;</code>\n\n"
                "Example: <code>/market sell apple 10 15</code>\n"
                "This lists 10 apples at $15 each"
            )
            return

        crop_name = args[1].lower()
        try:
            qty = int(args[2])
            price_each = int(args[3])
        except ValueError:
            await message.reply("❌ Quantity and price must be numbers!")
            return

        if qty <= 0 or price_each <= 0:
            await message.reply("❌ Quantity and price must be positive!")
            return

        # Check both harvest and food inventory
        have_harvest = await db.get_inventory_item(
            user.id, "harvest", crop_name
        )
        have_food = await db.get_inventory_item(user.id, "food", crop_name)
        total_have = have_harvest + have_food

        if total_have < qty:
            await message.reply(
                f"❌ Not enough {crop_name}!\n"
                f"You have: {total_have}\n"
                f"Trying to list: {qty}"
            )
            return

        # Determine item type and remove from inventory
        item_type = "harvest" if have_harvest > 0 else "food"
        actual_remove = min(
            qty, have_harvest if item_type == "harvest" else have_food
        )
        await db.remove_inventory_item(
            user.id, item_type, crop_name, actual_remove
        )
        remaining = qty - actual_remove
        if remaining > 0:
            other_type = "food" if item_type == "harvest" else "harvest"
            await db.remove_inventory_item(
                user.id, other_type, crop_name, remaining
            )

        # Create listing
        listing = await db.create_listing(
            user.id, item_type, crop_name, qty, price_each
        )

        emoji = get_crop_emoji(crop_name)
        await message.reply(
            f"✅ Listed on marketplace!\n\n"
            f"#{listing['id']} {emoji} {qty}x {crop_name} @ {format_price(price_each)}/ea\n"
            f"Total: {format_price(price_each * qty)}"
        )

    elif action == "buy":
        if len(args) < 2:
            await message.reply(
                "❌ Usage: <code>/market buy &lt;listing_id&gt; [quantity]</code>"
            )
            return

        try:
            listing_id = int(args[1])
            quantity = int(args[2]) if len(args) > 2 else None
        except ValueError:
            await message.reply("❌ Invalid listing ID or quantity!")
            return

        # Get listing
        listing = await db.get_listing(listing_id)
        if not listing:
            await message.reply("❌ Listing not found!")
            return

        if listing["seller_id"] == user.id:
            await message.reply("❌ Can't buy your own listing!")
            return

        # Determine quantity
        qty = quantity if quantity else listing["quantity"]
        qty = min(qty, listing["quantity"])

        if qty <= 0:
            await message.reply("❌ Invalid quantity!")
            return

        # Try to buy
        success, msg = await db.buy_from_listing(listing_id, user.id, qty)

        if success:
            emoji = get_crop_emoji(listing["item_name"])
            await message.reply(f"✅ {emoji} {msg}")
        else:
            await message.reply(f"❌ {msg}")

    elif action == "cancel":
        if len(args) < 2:
            await message.reply(
                "❌ Usage: <code>/market cancel &lt;id&gt;</code>"
            )
            return

        try:
            listing_id = int(args[1])
        except ValueError:
            await message.reply("❌ Invalid listing ID!")
            return

        success = await db.cancel_listing(listing_id, user.id)
        if success:
            await message.reply(
                "✅ Listing cancelled, items returned to inventory."
            )
        else:
            await message.reply(
                "❌ Listing not found or doesn't belong to you!"
            )

    else:
        await message.reply(
            "❌ Unknown action. Use 'sell', 'buy', or 'cancel'."
        )


reg("buy", "🌱 Buy seeds for garden")


@client.on_message(filters.command(["buy"]))
async def buy_command(
    message: Message,
):
    """Buy seeds. Usage: /buy <crop> [qty] or /buy crop1 qty, crop2 qty, ..."""
    user = message.from_user
    raw_text = (
        (message.text or "").split(" ", 1)[1].strip()
        if " " in (message.text or "")
        else ""
    )

    await db.upsert_user(user.id, user.username, user.first_name)

    if not raw_text:
        await message.reply(
            "🌱 <b>Buy Seeds</b>\n\n"
            "Usage: <code>/buy &lt;crop&gt; [quantity]</code>\n\n"
            "Examples:\n"
            "• <code>/buy corn 10</code> — buy 10 corn seeds\n"
            "• <code>/buy tomato 1846, pomegranate 1847, apple 1108</code> — buy multiple at once\n\n"
            "Or use /shop to browse all seeds!\n\n"
            "<blockquote>Want a seed refund? Try /refund_seed</blockquote>"
        )
        return

    # Parse one or more comma-separated entries
    entries = [e.strip() for e in raw_text.split(",") if e.strip()]
    requests: list[tuple[str, int]] = []  # (crop_key, qty)
    for entry in entries:
        tokens = entry.split()
        raw_name, qty = parse_item_and_qty(tokens)
        crop_key = resolve_item_key(
            raw_name
        ) or raw_name.strip().lower().replace(" ", "_")
        if crop_key not in ALL_PLANTABLE:
            await message.reply(f"❌ Unknown crop: <code>{raw_name}</code>")
            return
        requests.append((crop_key, qty))

    # Check total cost upfront
    total_cost = sum(ALL_PLANTABLE[k]["seed_cost"] * q for k, q in requests)
    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < total_cost:
        await message.reply(
            f"❌ Not enough coins!\n"
            f"Total cost: {format_price(total_cost)}\n"
            f"Your balance: {format_price(wallet['balance'])}"
        )
        return

    # Deduct balance once and add all seeds
    await db.add_balance(
        user.id, -total_cost, f"Bought seeds ({len(requests)} types)"
    )
    for crop_key, qty in requests:
        await db.add_inventory_item(user.id, "seed", crop_key, qty)

    if len(requests) == 1:
        crop_key, qty = requests[0]
        emoji = get_crop_display_emoji(crop_key)
        await message.reply(
            f"✅ Bought {qty}x {emoji} <b>{get_item_display_name(crop_key)}</b> seeds!\n"
            f"💰 Cost: {format_price(total_cost)}\n"
            f"Use /plant to sow them in your garden.\n\n"
            f"<blockquote>Want a seed refund? Try /refund_seed</blockquote>"
        )
    else:
        text = f"✅ <b>Bought seeds ({len(requests)} types):</b>\n"
        for crop_key, qty in requests:
            emoji = get_crop_display_emoji(crop_key)
            cost = ALL_PLANTABLE[crop_key]["seed_cost"] * qty
            text += f"  • {emoji} {get_item_display_name(crop_key)}: {qty}x — {format_price(cost)}\n"
        text += f"\n💰 Total: {format_price(total_cost)}\nUse /plant to sow them in your garden.\n\n<blockquote>Want a seed refund? Try /refund_seed</blockquote>"
        await message.reply(text)


@client.on_message(filters.command(["refund_seed"]))
async def refund_seed_command(message: Message):
    """Refund wallet coins for all seeds currently in inventory."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    inventory = await db.get_inventory(user.id)
    seeds = [
        i for i in inventory if i["item_type"] == "seed" and i["quantity"] > 0
    ]

    if not seeds:
        await message.reply("📦 You don't have any seeds to refund!")
        return

    total_refund = 0
    refunded: list[tuple[str, int, int]] = []
    for item in seeds:
        name = item["item_name"]
        qty = item["quantity"]
        if name not in ALL_PLANTABLE:
            continue
        refund = ALL_PLANTABLE[name]["seed_cost"] * qty
        await db.remove_inventory_item(user.id, "seed", name, qty)
        total_refund += refund
        refunded.append((name, qty, refund))

    if not refunded:
        await message.reply("📦 No refundable seeds found!")
        return

    await db.add_balance(user.id, total_refund, "Seed refund")

    text = "💸 <b>Seed Refund Complete!</b>\n\n"
    for name, qty, refund in refunded:
        emoji = get_crop_display_emoji(name)
        text += f"  • {emoji} {get_item_display_name(name)}: {qty}x → +{format_price(refund)}\n"
    text += f"\n💰 Total refunded: {format_price(total_refund)}"
    await message.reply(text)


reg("inventory", "📦 View inventory [/inv]")


@client.on_message(filters.command(["inventory", "inv"]))
async def inventory_command(
    message: Message,
):
    """View your full inventory."""
    user = message.from_user

    inventory = await db.get_inventory(user.id)

    if not inventory:
        await message.reply("📦 Your inventory is empty!")
        return

    text = "📦 <b>Your Inventory</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    # Group by type
    seeds = []
    harvests = []
    foods = []
    produce = []
    feeds = []

    for item in inventory:
        emoji = get_crop_emoji(item["item_name"])
        entry = f"{emoji} {get_item_display_name(item['item_name'])}: {item['quantity']}"

        if item["item_type"] == "seed":
            seeds.append(entry)
        elif item["item_type"] == "harvest":
            harvests.append(entry)
        elif item["item_type"] == "food":
            foods.append(entry)
        elif item["item_type"] == "animal_produce":
            produce.append(entry)
        elif item["item_type"] == "feed":
            feeds.append(entry)

    if seeds:
        text += "<blockquote expandable><b>🌱 Seeds:</b>\n"
        text += "\n".join(seeds) + "\n</blockquote>\n\n"

    if harvests:
        text += "<blockquote expandable><b>🌾 Harvested:</b>\n"
        text += "\n".join(harvests) + "\n</blockquote>\n\n"

    if foods:
        text += "<blockquote expandable><b>🍽️ Cooked Food:</b>\n"
        text += "\n".join(foods) + "\n</blockquote>\n\n"

    if produce:
        text += "<blockquote expandable><b>🐄 Animal Produce:</b>\n"
        text += "\n".join(produce) + "\n</blockquote>\n\n"

    if feeds:
        text += "<blockquote expandable><b>🌾 Animal Feed:</b>\n"
        text += "\n".join(feeds) + "\n</blockquote>"

    # Gift inventory hint
    gift_count = len([
        i for i in await db.get_gift_inventory(user.id) if i["quantity"] > 0
    ])
    if gift_count > 0:
        text += f"\n\n🎁 You have <b>{gift_count}</b> gift item type(s) waiting → /gift_inv"

    await message.reply(text)


reg("machines", "⚙️ View owned machines")


@client.on_message(filters.command(["machines"]))
async def machines_command(
    message: Message,
):
    """View your owned machines."""
    user = message.from_user

    machines = await db.get_user_machines(user.id)

    if not machines:
        await message.reply(
            "⚙️ You don't own any machines!\n\n"
            "Use /shop to buy cooking machines."
        )
        return

    text = "⚙️ <b>Your Machines</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    for machine in machines:
        machine_type = machine["machine_type"]
        if machine_type in MACHINES:
            info = MACHINES[machine_type]
            text += f"{info['emoji']} <b>{info['name']}</b>\n"
            for recipe in info["recipes"]:
                food = FOODS[recipe["produces"]]
                ing_str = ", ".join(
                    f"{v}x {get_item_display_name(k)}"
                    for k, v in recipe["ingredients"].items()
                )
                text += (
                    f"  {food['emoji']} {recipe['name']}: {ing_str}"
                    f" → {format_price(food['sell_price'])}\n"
                )
            text += "\n"

    text += "Use /cook to start cooking!"
    await message.reply(text)


@client.on_callback_query(filters.regex(r"^" + "marketbuy:"))
async def handle_market_buy_callback(
    callback: CallbackQuery,
):
    """Handle marketplace buy with quantity selection."""
    parts = callback.data.split(":")
    if len(parts) < 2:
        await callback.answer("Invalid action")
        return

    listing_id = int(parts[1])
    quantity = int(parts[2]) if len(parts) > 2 else None

    user_id = callback.from_user.id

    # Get listing
    listing = await db.get_listing(listing_id)
    if not listing:
        await callback.answer(
            "❌ Listing no longer available!", show_alert=True
        )
        return

    if listing["seller_id"] == user_id:
        await callback.answer("❌ Can't buy your own listing!", show_alert=True)
        return

    try:
        if quantity is None:
            # Show quantity selection
            emoji = get_crop_emoji(listing["item_name"])
            price = listing["price_each"]
            available = listing["quantity"]

            text = f"🛒 <b>Buy #{listing_id}</b>\n\n"
            text += f"{emoji} <b>{listing['item_name']}</b>\n"
            text += f"Available: {available}\n"
            text += f"Price: {format_price(price)} each\n\n"
            text += "Select quantity:\n"

            # Generate quantity buttons
            buttons = []
            qty_options = [1, 3, 5, 10, 20, 50]
            row = []
            for qty in qty_options:
                if qty <= available:
                    row.append(
                        InlineKeyboardButton(
                            text=f"×{qty} = {format_price(price * qty)}",
                            callback_data=f"marketbuy:{listing_id}:{qty}",
                        )
                    )
                    if len(row) == 2:
                        buttons.append(row)
                        row = []
            if row:
                buttons.append(row)

            # Max button
            if available not in qty_options:
                buttons.append([
                    InlineKeyboardButton(
                        text=f"Max ({available}) = {format_price(price * available)}",
                        callback_data=f"marketbuy:{listing_id}:{available}",
                    )
                ])

            buttons.append([
                InlineKeyboardButton(
                    text="« Back",
                    callback_data="shop:market:1",
                )
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )
            await callback.answer()
        else:
            # Execute the purchase
            qty = min(quantity, listing["quantity"])
            if qty <= 0:
                await callback.answer("❌ Invalid quantity!", show_alert=True)
                return

            success, msg = await db.buy_from_listing(listing_id, user_id, qty)

            if success:
                emoji = get_crop_emoji(listing["item_name"])
                await callback.answer(f"✅ {emoji} {msg}", show_alert=True)
            else:
                await callback.answer(f"❌ {msg}", show_alert=True)

            # Refresh marketplace view
            listings, total = await db.get_listings(page=1)

            text = "🏪 <b>Marketplace</b>\n\n"
            text += "<i>Listing IDs (e.g. #1) can be used with /market buy #ID</i>\n\n"

            buttons = []

            if listings:
                for lst in listings:
                    seller_name = (
                        lst["first_name"] or lst["username"] or "Unknown"
                    )
                    lst_emoji = get_crop_emoji(lst["item_name"])
                    total_price = lst["quantity"] * lst["price_each"]

                    text += "<blockquote expandable>"
                    text += f"<b>#{lst['id']}</b> - {lst_emoji} {lst['quantity']}x {lst['item_name']}\n"
                    text += f"💰 {format_price(lst['price_each'])}/ea (Total: {format_price(total_price)})\n"
                    text += f"👤 {html.escape(seller_name)}"
                    text += "</blockquote>\n"

                    buttons.append([
                        InlineKeyboardButton(
                            text=f"Buy #{lst['id']} - {lst_emoji} {lst['quantity']}x",
                            callback_data=f"marketbuy:{lst['id']}",
                        )
                    ])
            else:
                text += "<i>No listings yet!</i>\n"

            text += "\n<i>Use /market to list your items</i>"

            buttons.append([
                InlineKeyboardButton(
                    text="🔄 Refresh", callback_data="shop:market:1"
                ),
                InlineKeyboardButton(
                    text="« Back", callback_data="shop:main:1"
                ),
            ])

            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await queue_it(
                lambda: callback.message.edit_text(text, reply_markup=keyboard),
                callback.message.chat,
            )

    except BadRequest:
        await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "market:sellqty:"))
async def handle_market_sellqty_callback(
    callback: CallbackQuery,
):
    """Handle marketplace sell quantity selection."""
    parts = callback.data.split(":")
    if len(parts) < 3:
        await callback.answer("Invalid action")
        return

    item_name = parts[2]
    user_id = callback.from_user.id

    sellable = get_all_sellable()
    if item_name not in sellable:
        await callback.answer("Invalid item!", show_alert=True)
        return

    info = sellable[item_name]

    # Check both harvest and food inventory
    have_harvest = await db.get_inventory_item(user_id, "harvest", item_name)
    have_food = await db.get_inventory_item(user_id, "food", item_name)
    have_qty = have_harvest + have_food

    if have_qty <= 0:
        await callback.answer("You don't have this item!", show_alert=True)
        return

    # Show quantity selection first
    text = f"💰 <b>Sell {info['emoji']} {item_name}</b>\n\n"
    text += f"You have: {have_qty}\n"
    text += f"Base price: {format_price(info['sell_price'])}/ea\n"
    text += "Select quantity to sell:\n"

    # Quantity buttons
    buttons = []
    qty_options = [1, 3, 5, 10, 20, 50]
    row = []
    for qty in qty_options:
        if qty <= have_qty:
            row.append(
                InlineKeyboardButton(
                    text=f"×{qty}",
                    callback_data=f"market:sellprice:{item_name}:{qty}",
                )
            )
            if len(row) == 3:
                buttons.append(row)
                row = []
    if row:
        buttons.append(row)

    # Max button (cap at 300)
    max_qty = min(have_qty, 300)
    if max_qty > 0 and max_qty not in qty_options:
        buttons.append([
            InlineKeyboardButton(
                text=f"Max ({max_qty})",
                callback_data=f"market:sellprice:{item_name}:{max_qty}",
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="« Back", callback_data="market:back")
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "market:sellprice:"))
async def handle_market_sellprice_callback(
    callback: CallbackQuery,
):
    """Handle quantity selection, show price options."""
    parts = callback.data.split(":")
    if len(parts) < 5:
        await callback.answer("Invalid action")
        return

    item_name = parts[2]
    qty = int(parts[3])
    user_id = callback.from_user.id

    sellable = get_all_sellable()
    if item_name not in sellable:
        await callback.answer("Invalid item!", show_alert=True)
        return

    info = sellable[item_name]

    # Check inventory again
    have_harvest = await db.get_inventory_item(user_id, "harvest", item_name)
    have_food = await db.get_inventory_item(user_id, "food", item_name)
    have_qty = have_harvest + have_food

    if have_qty < qty:
        await callback.answer("Not enough items!", show_alert=True)
        return

    item_type = "harvest" if have_harvest > 0 else "food"

    text = f"💰 <b>Sell {info['emoji']} {item_name}</b>\n\n"
    text += f"Quantity: {qty}\n"
    text += "Set price per item (in coins):\n"
    text += f"<i>Example: 15 = {format_price(15)} each (Total: {format_price(info['sell_price'] * qty)})</i>"

    # Quick price buttons
    buttons = []
    base_price = info["sell_price"]
    prices = [
        max(1, base_price // 2),
        base_price,
        base_price * 2,
        base_price * 3,
    ]
    row = []
    for price in prices:
        row.append(
            InlineKeyboardButton(
                text=f"{format_price(price)}/ea",
                callback_data=f"market:sellconfirm:{item_name}:{qty}:{price}:{item_type}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="« Back", callback_data=f"market:sellqty:{item_name}"
        )
    ])

    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "market:sellconfirm:"))
async def handle_market_sellconfirm_callback(
    callback: CallbackQuery,
):
    """Handle marketplace sell confirmation."""
    parts = callback.data.split(":")
    if len(parts) < 7:
        await callback.answer("Invalid action")
        return

    item_name = parts[2]
    qty = int(parts[3])
    price_each = int(parts[4])
    item_type = parts[5]
    user_id = callback.from_user.id

    # Check inventory
    have = await db.get_inventory_item(user_id, item_type, item_name)
    if have < qty:
        await callback.answer("Not enough items!", show_alert=True)
        return

    # Remove from inventory
    await db.remove_inventory_item(user_id, item_type, item_name, qty)

    # Create listing
    listing = await db.create_listing(
        user_id, item_type, item_name, qty, price_each
    )

    emoji = get_crop_emoji(item_name)
    text = "✅ <b>Listed on Marketplace!</b>\n\n"
    text += f"#{listing['id']} {emoji} {qty}x {item_name}\n"
    text += f"💰 {format_price(price_each)}/ea (Total: {format_price(price_each * qty)})"

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🏪 View All Listings", callback_data="shop:market:1"
                )
            ],
            [InlineKeyboardButton(text="« Back", callback_data="market:back")],
        ]
    )
    await queue_it(
        lambda: callback.message.edit_text(text, reply_markup=keyboard),
        callback.message.chat,
    )
    await callback.answer()


@client.on_callback_query(filters.regex(r"^" + "market:back" + r"$"))
async def handle_market_back_callback(
    callback: CallbackQuery,
):
    """Go back to market menu."""
    user_id = callback.from_user.id

    listings = await db.get_user_listings(user_id)
    inventory = await db.get_inventory(user_id)
    harvest_counts = {}
    food_counts = {}
    for item in inventory:
        if item["item_type"] == "harvest" and item["quantity"] > 0:
            harvest_counts[item["item_name"]] = item["quantity"]
        elif item["item_type"] == "food" and item["quantity"] > 0:
            food_counts[item["item_name"]] = item["quantity"]

    text = "🏪 <b>Marketplace</b>\n"
    text += "━━━━━━━━━━━━━━━━\n\n"

    text += "<b>Quick Commands:</b>\n"
    text += (
        "• <code>/market sell &lt;item&gt; &lt;qty&gt; &lt;price&gt;</code>\n"
    )
    text += "• <code>/market cancel &lt;id&gt;</code>\n"
    text += "• Or use buttons below for interactive selling\n\n"

    if listings:
        text += "<b>Your active listings:</b>\n"
        for listing in listings:
            emoji = get_crop_emoji(listing["item_name"])
            text += f"  #{listing['id']} {emoji} {listing['quantity']}x {listing['item_name']} "
            text += f"@ {format_price(listing['price_each'])}/ea\n"
    else:
        text += "<i>No active listings</i>\n"

    sellable = get_all_sellable()
    sell_buttons = []
    row = []
    for item_name, qty in sorted(harvest_counts.items()):
        if item_name in sellable:
            info = sellable[item_name]
            row.append(
                InlineKeyboardButton(
                    text=f"{info['emoji']} {item_name} ({qty})",
                    callback_data=f"market:sellqty:{item_name}",
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
                    callback_data=f"market:sellqty:{item_name}",
                )
            )
            if len(row) == 2:
                sell_buttons.append(row)
                row = []
    if row:
        sell_buttons.append(row)

    if sell_buttons:
        sell_buttons.append([
            InlineKeyboardButton(
                text="🏪 View All Listings", callback_data="shop:market:1"
            )
        ])

    keyboard = (
        InlineKeyboardMarkup(inline_keyboard=sell_buttons)
        if sell_buttons
        else None
    )
    try:
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=keyboard),
            callback.message.chat,
        )
    except BadRequest:
        pass
    await callback.answer()
