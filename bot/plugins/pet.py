"""Pet system — /pet, /petname commands."""

import math
import random
from datetime import datetime

from pyrogram import filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.client import client
from bot.achievements import check_and_unlock
from bot.command_registry import reg
from bot.constants import (
    FOODS,
    PETS,
    PET_LEVELUP_COST,
    PET_MAX_LEVEL,
    PET_MAX_HAPPINESS,
    _pet_btn_text,
    _pet_custom_emoji_id,
    _pet_emoji,
    format_price,
    get_next_pet_cost,
)
from bot.database import db
from bot.queue_it import queue_it


def _cb(uid: int, *parts) -> str:
    return "pet:" + str(uid) + ":" + ":".join(str(p) for p in parts)


def _effective_happiness(pet: dict) -> int:
    updated = pet.get("happiness_updated_at")
    if not updated:
        return pet["happiness"]
    elapsed_days = (datetime.utcnow() - updated).total_seconds() / 86400
    return max(0, pet["happiness"] - int(elapsed_days * 40))


def _happiness_bar(happiness: int) -> str:
    filled = happiness // 10
    bar = "🟩" * filled + "⬜" * (10 - filled)
    mood = (
        "😞"
        if happiness < 26
        else "😐"
        if happiness < 51
        else "😊"
        if happiness < 76
        else "😄"
    )
    return f"{bar} {happiness}/{PET_MAX_HAPPINESS} {mood}"


def _display_name(pet: dict) -> str:
    """Return custom name if set, otherwise the species name."""
    return (
        pet["pet_name"]
        if pet.get("pet_name")
        else PETS[pet["pet_type"]]["name"]
    )


def _resolve_sound(pet_type: str, sound: str) -> str:
    """Resolve {emoji} placeholder in sound strings to the pet's display emoji."""
    return sound.format(emoji=_pet_emoji(pet_type))


def _pet_header(pet: dict) -> str:
    pet_type = pet["pet_type"]
    info = PETS[pet_type]
    happiness = _effective_happiness(pet)
    name = _display_name(pet)
    species_tag = f" the {info['name']}" if pet.get("pet_name") else ""
    return (
        f"{_pet_emoji(pet_type)} <b>{name}</b>{species_tag} — Level {pet['level']}\n"
        f"Happiness: {_happiness_bar(happiness)}\n"
    )


def _interact_keyboard(
    uid: int, pet: dict, owned_count: int = 1, all_owned: bool = False
) -> InlineKeyboardMarkup:
    pet_type = pet["pet_type"]
    buttons = [
        [
            InlineKeyboardButton(
                text="🍽️ Feed", callback_data=_cb(uid, "feed_menu", pet_type)
            ),
            InlineKeyboardButton(
                text="🍖 Max Feed", callback_data=_cb(uid, "feed_all", pet_type)
            ),
        ],
        [
            InlineKeyboardButton(
                text="🤗 Pet", callback_data=_cb(uid, "pet_it", pet_type)
            ),
            InlineKeyboardButton(
                text="🎮 Play", callback_data=_cb(uid, "play", pet_type)
            ),
        ],
    ]
    if pet["level"] < PET_MAX_LEVEL:
        buttons.append([
            InlineKeyboardButton(
                text=f"⬆️ Level Up ({format_price(PET_LEVELUP_COST)})",
                callback_data=_cb(uid, "levelup", pet_type),
            )
        ])
    if not all_owned:
        next_cost = get_next_pet_cost(owned_count)
        buttons.append([
            InlineKeyboardButton(
                text=f"➕ Get another Pet ({format_price(next_cost)})",
                callback_data=_cb(uid, "buy_menu"),
            )
        ])
    buttons.append([
        InlineKeyboardButton(text="« My Pets", callback_data=_cb(uid, "view"))
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_pet_view(uid: int) -> tuple[str, InlineKeyboardMarkup | None]:
    """Top-level /pet view: picker if multiple pets, interact if one, buy if none."""
    pets = await db.get_pets(uid)

    if not pets:
        return _build_buy_screen(uid, owned_count=0)

    if len(pets) == 1:
        # Skip picker for single-pet owners — go straight to interact view.
        all_owned = set(PETS.keys()) == {pets[0]["pet_type"]}
        return _build_interact_view(
            uid, pets[0], owned_count=1, all_owned=all_owned
        )

    # Picker screen.
    text = "🐾 <b>Your Pets</b>\n\n"
    for p in pets:
        info = PETS[p["pet_type"]]
        happiness = _effective_happiness(p)
        name = _display_name(p)
        text += f"{_pet_emoji(p['pet_type'])} <b>{name}</b>"
        if p.get("pet_name"):
            text += f" the {info['name']}"
        text += f" — Lv.{p['level']}  ❤️ {happiness}\n"

    text += "\nChoose a pet to interact with:"
    buttons = []
    row = []
    for p in pets:
        info = PETS[p["pet_type"]]
        name = _display_name(p)
        pet_type = p["pet_type"]
        btn = InlineKeyboardButton(
            text=_pet_btn_text(pet_type, name),
            callback_data=_cb(uid, "view", pet_type),
            icon_custom_emoji_id=_pet_custom_emoji_id(pet_type),
        )
        row.append(btn)
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)

    buttons.append([
        InlineKeyboardButton(
            text="🍽️ Feed All Pets",
            callback_data=_cb(uid, "feed_all_pets"),
        )
    ])

    owned_types = {p["pet_type"] for p in pets}
    if owned_types != set(PETS.keys()):
        next_cost = get_next_pet_cost(len(pets))
        buttons.append([
            InlineKeyboardButton(
                text=f"➕ Add Pet ({format_price(next_cost)})",
                callback_data=_cb(uid, "buy_menu"),
            )
        ])

    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


def _build_interact_view(
    uid: int, pet: dict, owned_count: int = 1, all_owned: bool = False
) -> tuple[str, InlineKeyboardMarkup]:
    name_hint = (
        ""
        if pet.get("pet_name")
        else "\n<i>💡 Use /petname to give your pet a name!</i>"
    )
    text = f"🐾 <b>Your Pet</b>\n\n{_pet_header(pet)}{name_hint}"
    return text, _interact_keyboard(
        uid, pet, owned_count=owned_count, all_owned=all_owned
    )


def _build_buy_screen(
    uid: int, owned_count: int, owned_types: set | None = None
) -> tuple[str, InlineKeyboardMarkup]:
    owned_types = owned_types or set()
    next_cost = get_next_pet_cost(owned_count)
    warning = ""
    if owned_count >= 1:
        warning = (
            f"\n⚠️ Each additional pet costs <b>10× more</b> — your next one is "
            f"<b>{format_price(next_cost)}</b>. Choose the one you like most right now!\n"
        )
    text = f"🐾 <b>Pets</b>\n{warning}\nChoose your new companion:\n"
    row1, row2 = [], []
    available = [(k, v) for k, v in PETS.items() if k not in owned_types]
    for i, (key, info) in enumerate(available):
        btn = InlineKeyboardButton(
            text=_pet_btn_text(key, f"{info['name']} ({format_price(next_cost)})"),
            callback_data=_cb(uid, "buy", key),
            icon_custom_emoji_id=_pet_custom_emoji_id(key),
        )
        (row1 if i < 3 else row2).append(btn)
    buttons = []
    if row1:
        buttons.append(row1)
    if row2:
        buttons.append(row2)
    if owned_count > 0:
        buttons.append([
            InlineKeyboardButton(
                text="« My Pets", callback_data=_cb(uid, "view")
            )
        ])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── commands ─────────────────────────────────────────────────────────────────

reg("pet", "🐾 View and interact with your pet [/pets]")


@client.on_message(filters.command(["pet", "pets"]))
async def pet_command(message: Message):
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    text, kb = await _build_pet_view(user.id)
    await message.reply(text, reply_markup=kb)


reg("petname", "✏️ Give your pet a name: /petname [type] <name>")


@client.on_message(filters.command(["petname"]))
async def petname_command(message: Message):
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    pets = await db.get_pets(user.id)
    if not pets:
        await message.reply("🐾 You don't have a pet yet! Use /pet to get one.")
        return

    args = (message.text or "").split(maxsplit=2)[1:]  # drop command

    # Resolve pet_type and name from args.
    pet_type: str | None = None
    name: str | None = None

    if len(args) >= 2 and args[0].lower() in PETS:
        # /petname <type> <name>
        pet_type = args[0].lower()
        name = args[1].strip()[:30]
    elif len(args) == 1 and args[0].lower() not in PETS:
        # /petname <name>  — only valid when owning exactly one pet
        if len(pets) > 1:
            types_hint = " | ".join(PETS.keys())
            await message.reply(
                f"✏️ You have multiple pets — specify which one:\n"
                f"<code>/petname &lt;type&gt; &lt;name&gt;</code>\n"
                f"Types: <code>{types_hint}</code>"
            )
            return
        pet_type = pets[0]["pet_type"]
        name = args[0].strip()[:30]
    else:
        owned_types = " | ".join(p["pet_type"] for p in pets)
        current_names = ", ".join(
            f"{_pet_emoji(p['pet_type'])} {p['pet_type']}: <b>{p['pet_name'] or 'unnamed'}</b>"
            for p in pets
        )
        await message.reply(
            f"✏️ Usage: <code>/petname &lt;type&gt; &lt;name&gt;</code>\n"
            f"Your pets — {current_names}\n"
            f"Types: <code>{owned_types}</code>\nMax 30 characters."
        )
        return

    # Check they own this type.
    owned_map = {p["pet_type"]: p for p in pets}
    if pet_type not in owned_map:
        await message.reply(f"🐾 You don't own a {PETS[pet_type]['name']}!")
        return

    await db.set_pet_name(user.id, pet_type, name)
    info = PETS[pet_type]
    await message.reply(
        f"{_pet_emoji(pet_type)} Your {info['name']} is now named <b>{name}</b>!"
    )


# ─── callbacks ────────────────────────────────────────────────────────────────


@client.on_callback_query(filters.regex(r"^pet:"))
async def pet_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    owner_id = int(parts[1])
    action = parts[2]

    if callback.from_user.id != owner_id:
        await callback.answer("That's not your pet! 🐾", show_alert=True)
        return

    uid = owner_id

    if action == "view":
        pet_type = parts[3] if len(parts) > 3 else None
        if pet_type:
            pet = await db.get_pet_by_type(uid, pet_type)
            if not pet:
                await callback.answer("Pet not found.", show_alert=True)
                return
            pets = await db.get_pets(uid)
            owned_types = {p["pet_type"] for p in pets}
            text, kb = _build_interact_view(
                uid,
                pet,
                owned_count=len(pets),
                all_owned=owned_types == set(PETS.keys()),
            )
        else:
            text, kb = await _build_pet_view(uid)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "buy_menu":
        pets = await db.get_pets(uid)
        owned_types = {p["pet_type"] for p in pets}
        text, kb = _build_buy_screen(
            uid, owned_count=len(pets), owned_types=owned_types
        )
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "buy":
        pet_type = parts[3]
        pets = await db.get_pets(uid)
        owned_types = {p["pet_type"] for p in pets}
        if pet_type in owned_types:
            await callback.answer(
                f"You already have a {PETS[pet_type]['name']}!", show_alert=True
            )
            return
        cost = get_next_pet_cost(len(pets))
        info = PETS[pet_type]
        wallet = await db.get_wallet(uid)
        if wallet["balance"] < cost:
            await callback.answer(
                f"Need {format_price(cost)}. You have {format_price(wallet['balance'])}.",
                show_alert=True,
            )
            return
        await db.add_balance(uid, -cost, f"Bought {info['name']} pet")
        await db.buy_pet(uid, pet_type)
        await callback.answer(
            f"🎉 Welcome home, {_pet_emoji(pet_type)} {info['name']}!", show_alert=True
        )
        text, kb = await _build_pet_view(uid)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )

    elif action == "pet_it":
        pet_type = parts[3]
        pet = await db.get_pet_by_type(uid, pet_type)
        if not pet:
            await callback.answer("You don't have that pet!", show_alert=True)
            return
        info = PETS[pet_type]
        sound = _resolve_sound(pet_type, random.choice(info["sounds"]["pet"]))
        await db.update_pet_happiness(uid, pet_type, 5)
        name = _display_name(pet)
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=_cb(uid, "view", pet_type)
                    )
                ]
            ]
        )
        msg_text = (
            f"🐾 <b>Your Pet</b>\n\n{_pet_header(pet)}\n"
            f"{_pet_emoji(pet_type)} <i>{sound}</i>\n\n"
            f"You petted {name}! Happiness +5 💕"
        )
        await queue_it(
            lambda: callback.message.edit_text(msg_text, reply_markup=back_kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "play":
        pet_type = parts[3]
        pet = await db.get_pet_by_type(uid, pet_type)
        if not pet:
            await callback.answer("You don't have that pet!", show_alert=True)
            return
        info = PETS[pet_type]
        sound = _resolve_sound(pet_type, random.choice(info["sounds"]["play"]))
        await db.update_pet_happiness(uid, pet_type, 5)
        name = _display_name(pet)
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=_cb(uid, "view", pet_type)
                    )
                ]
            ]
        )
        msg_text = (
            f"🐾 <b>Your Pet</b>\n\n{_pet_header(pet)}\n"
            f"{_pet_emoji(pet_type)} <i>{sound}</i>\n\n"
            f"You played with {name}! Happiness +5 🎉"
        )
        await queue_it(
            lambda: callback.message.edit_text(msg_text, reply_markup=back_kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "feed_menu":
        pet_type = parts[3]
        pet = await db.get_pet_by_type(uid, pet_type)
        if not pet:
            await callback.answer("You don't have that pet!", show_alert=True)
            return
        happiness = _effective_happiness(pet)
        deficit = PET_MAX_HAPPINESS - happiness
        if deficit <= 0:
            await callback.answer(
                "Your pet is already fully happy! 😄", show_alert=True
            )
            return
        inventory = await db.get_inventory(uid)
        food_items = [
            i
            for i in inventory
            if i["item_type"] == "food"
            and i["quantity"] > 0
            and i["item_name"] in FOODS
        ]
        if not food_items:
            await callback.answer(
                "No food in inventory! Cook something with /cook first.",
                show_alert=True,
            )
            return
        text = f"🍽️ <b>Feed your pet</b>\n\n{_pet_header(pet)}\n"
        text += f"Needs <b>+{deficit} happiness</b> to reach 100%\n"
        text += "<i>Number shown = amount needed (capped by stock):</i>\n"
        buttons = []
        row = []
        for item in food_items[:12]:
            food_info = FOODS.get(item["item_name"], {})
            emoji = food_info.get("emoji", "🍴")
            gain_per = max(5, food_info.get("feed_value", 10) // 5)
            needed = math.ceil(deficit / gain_per)
            to_use = min(needed, item["quantity"])
            btn = InlineKeyboardButton(
                text=f"{emoji} {item['item_name']} ×{to_use}",
                callback_data=_cb(uid, "feed", pet_type, item["item_name"]),
            )
            row.append(btn)
            if len(row) == 2:
                buttons.append(row)
                row = []
        if row:
            buttons.append(row)
        buttons.append([
            InlineKeyboardButton(
                text="« Back", callback_data=_cb(uid, "view", pet_type)
            )
        ])
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "feed":
        pet_type = parts[3]
        food_name = parts[4]
        pet = await db.get_pet_by_type(uid, pet_type)
        if not pet:
            await callback.answer("You don't have that pet!", show_alert=True)
            return
        have = await db.get_inventory_item(uid, "food", food_name)
        if have < 1:
            await callback.answer(
                "You don't have that food anymore!", show_alert=True
            )
            return
        food_info = FOODS.get(food_name, {})
        gain_per = max(5, food_info.get("feed_value", 10) // 5)
        deficit = max(0, PET_MAX_HAPPINESS - _effective_happiness(pet))
        needed = math.ceil(deficit / gain_per) if deficit > 0 else 1
        to_use = min(needed, have)
        total_gain = gain_per * to_use
        await db.remove_inventory_item(uid, "food", food_name, to_use)
        await db.update_pet_happiness(uid, pet_type, total_gain)
        pet = await db.get_pet_by_type(uid, pet_type)
        info = PETS[pet_type]
        sound = _resolve_sound(pet_type, random.choice(info["sounds"]["feed"]))
        emoji = food_info.get("emoji", "🍴")
        name = _display_name(pet)
        qty_str = f"{to_use}× " if to_use > 1 else ""
        back_kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=_cb(uid, "view", pet_type)
                    )
                ]
            ]
        )
        msg_text = (
            f"🐾 <b>Your Pet</b>\n\n{_pet_header(pet)}\n"
            f"{_pet_emoji(pet_type)} <i>{sound}</i>\n\n"
            f"You fed {name} {qty_str}{emoji} {food_name}! Happiness +{total_gain} 🍽️"
        )
        await queue_it(
            lambda: callback.message.edit_text(msg_text, reply_markup=back_kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "levelup":
        pet_type = parts[3]
        pet = await db.get_pet_by_type(uid, pet_type)
        if not pet:
            await callback.answer("You don't have that pet!", show_alert=True)
            return
        if pet["level"] >= PET_MAX_LEVEL:
            await callback.answer("Already at max level!", show_alert=True)
            return
        wallet = await db.get_wallet(uid)
        if wallet["balance"] < PET_LEVELUP_COST:
            await callback.answer(
                f"Need {format_price(PET_LEVELUP_COST)}. You have {format_price(wallet['balance'])}.",
                show_alert=True,
            )
            return
        await db.add_balance(uid, -PET_LEVELUP_COST, "Pet level up")
        new_level = await db.levelup_pet(uid, pet_type)
        if new_level >= 20:
            await check_and_unlock(
                db, uid, "pet_lover", client, callback.message.chat.id
            )
        await callback.answer(
            f"⬆️ Level up! Now Level {new_level}!", show_alert=True
        )
        pet = await db.get_pet_by_type(uid, pet_type)
        pets = await db.get_pets(uid)
        owned_types = {p["pet_type"] for p in pets}
        text, kb = _build_interact_view(
            uid,
            pet,
            owned_count=len(pets),
            all_owned=owned_types == set(PETS.keys()),
        )
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )

    elif action == "feed_all":
        # One-click: feed a single pet to 100% using the best available food
        pet_type = parts[3]
        pet = await db.get_pet_by_type(uid, pet_type)
        if not pet:
            await callback.answer("You don't have that pet!", show_alert=True)
            return
        happiness = _effective_happiness(pet)
        deficit = PET_MAX_HAPPINESS - happiness
        if deficit <= 0:
            await callback.answer(
                "Your pet is already fully happy! 😄", show_alert=True
            )
            return
        inventory = await db.get_inventory(uid)
        food_items = [
            i
            for i in inventory
            if i["item_type"] == "food"
            and i["quantity"] > 0
            and i["item_name"] in FOODS
        ]
        if not food_items:
            await callback.answer(
                "No food in inventory! Cook something with /cook first.",
                show_alert=True,
            )
            return
        # Pick food with highest happiness gain per item
        food_items.sort(
            key=lambda i: max(
                5, FOODS[i["item_name"]].get("feed_value", 10) // 5
            ),
            reverse=True,
        )
        best = food_items[0]
        food_name = best["item_name"]
        food_info = FOODS[food_name]
        gain_per = max(5, food_info.get("feed_value", 10) // 5)
        needed = math.ceil(deficit / gain_per)
        to_use = min(needed, best["quantity"])
        total_gain = gain_per * to_use
        await db.remove_inventory_item(uid, "food", food_name, to_use)
        await db.update_pet_happiness(uid, pet_type, total_gain)
        pet = await db.get_pet_by_type(uid, pet_type)
        pets = await db.get_pets(uid)
        owned_types = {p["pet_type"] for p in pets}
        text, kb = _build_interact_view(
            uid,
            pet,
            owned_count=len(pets),
            all_owned=owned_types == set(PETS.keys()),
        )
        f_emoji = food_info.get("emoji", "🍴")
        text += f"\n🍽️ Fed {to_use}× {f_emoji} {food_name} → +{total_gain} happiness!"
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "feed_all_pets":
        # Feed every owned pet to 100% using best available food per pet
        pets = await db.get_pets(uid)
        if not pets:
            await callback.answer("You don't have any pets!", show_alert=True)
            return
        inventory = await db.get_inventory(uid)
        # Local stock tracker so each pet draws from the same pool
        food_stock: dict[str, int] = {
            i["item_name"]: i["quantity"]
            for i in inventory
            if i["item_type"] == "food"
            and i["quantity"] > 0
            and i["item_name"] in FOODS
        }
        if not food_stock:
            await callback.answer(
                "No food in inventory! Cook something with /cook first.",
                show_alert=True,
            )
            return
        lines: list[str] = []
        for pet in pets:
            pet_type = pet["pet_type"]
            pet_info = PETS[pet_type]
            happiness = _effective_happiness(pet)
            deficit = PET_MAX_HAPPINESS - happiness
            name = _display_name(pet)
            if deficit <= 0:
                lines.append(
                    f"{_pet_emoji(pet_type)} <b>{name}</b>: already at 100% 😄"
                )
                continue
            # Best food still in stock
            best_food = max(
                (f for f in food_stock if food_stock[f] > 0),
                key=lambda f: max(5, FOODS[f].get("feed_value", 10) // 5),
                default=None,
            )
            if not best_food:
                lines.append(
                    f"{_pet_emoji(pet_type)} <b>{name}</b>: no food left 😢"
                )
                continue
            gain_per = max(5, FOODS[best_food].get("feed_value", 10) // 5)
            needed = math.ceil(deficit / gain_per)
            to_use = min(needed, food_stock[best_food])
            total_gain = gain_per * to_use
            await db.remove_inventory_item(uid, "food", best_food, to_use)
            food_stock[best_food] -= to_use
            await db.update_pet_happiness(uid, pet_type, total_gain)
            f_emoji = FOODS[best_food].get("emoji", "🍴")
            lines.append(
                f"{_pet_emoji(pet_type)} <b>{name}</b>: {to_use}× {f_emoji} → +{total_gain} happiness"
            )
        result_text = "🍽️ <b>Feed All Pets</b>\n\n" + "\n".join(lines)
        kb = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« My Pets", callback_data=_cb(uid, "view")
                    )
                ]
            ]
        )
        await queue_it(
            lambda: callback.message.edit_text(result_text, reply_markup=kb),
            callback.message.chat,
        )
        await callback.answer()
