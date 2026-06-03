"""Animal farm — /animals command and inline management UI."""

import datetime as dt
import html

from pyrogram import filters
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.client import client
from bot.command_registry import reg
from bot.constants import (
    ANIMAL_FEEDS,
    ANIMAL_PENS,
    ANIMAL_PRODUCE,
    ANIMALS,
    get_crop_display_emoji,
    get_item_display_name,
)
from bot.database import db
from bot.queue_it import queue_it

# ─── helpers ──────────────────────────────────────────────────────────────────


def _pen_capacity(pen: dict) -> int:
    return (
        ANIMAL_PENS[pen["pen_type"]]["base_capacity"] + (pen["level"] - 1) * 5
    )


def _time_remaining(ready_at) -> str:
    if ready_at is None:
        return "😴 Hungry"
    delta = ready_at - dt.datetime.utcnow()
    if delta.total_seconds() <= 0:
        return "✅ Ready!"
    mins = int(delta.total_seconds() // 60)
    secs = int(delta.total_seconds() % 60)
    return f"⏳ {mins}m {secs}s"


def _cb(uid: int, *parts) -> str:
    return "animals:" + str(uid) + ":" + ":".join(str(p) for p in parts)


async def _build_main_view(
    user_id: int,
) -> tuple[str, InlineKeyboardMarkup | None]:
    pens = await db.get_user_pens(user_id)
    if not pens:
        text = (
            "🐾 <b>Animal Farm</b>\n\n"
            "You don't own any animal pens yet.\n"
            "Buy one from /shop → 🐄 Animal Pens"
        )
        return text, None

    text = "🐾 <b>Animal Farm</b>\n\n"
    buttons: list[list] = []
    for pen in pens:
        pt = pen["pen_type"]
        info = ANIMAL_PENS[pt]
        cap = _pen_capacity(pen)
        count = await db.get_pen_animal_count(pen["id"])
        a_info = ANIMALS[info["animal_type"]]
        text += f"{info['emoji']} <b>{info['name']}</b> — Lv{pen['level']} ({count}/{cap})"
        if a_info["feed_type"]:
            feed_info = ANIMAL_FEEDS[a_info["feed_type"]]
            feed_count = await db.get_inventory_item(
                user_id, "feed", a_info["feed_type"]
            )
            text += f" | {feed_info['emoji']} {feed_info['name']}: {feed_count}"
        text += "\n"
        buttons.append([
            InlineKeyboardButton(
                text=f"{info['emoji']} {info['name']} ({count}/{cap})",
                callback_data=_cb(user_id, "pen", pt),
            )
        ])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


async def _build_pen_view(
    user_id: int, pen_type: str
) -> tuple[str, InlineKeyboardMarkup]:
    pen = await db.get_pen_by_type(user_id, pen_type)
    if not pen:
        return "❌ Pen not found.", InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="« Back", callback_data=_cb(user_id, "main")
                    )
                ]
            ]
        )
    pen_info = ANIMAL_PENS[pen_type]
    cap = _pen_capacity(pen)
    animals = await db.get_pen_animals(pen["id"])

    # Refresh ready flags
    for a in animals:
        await db.check_and_mark_ready(a["id"])
    animals = await db.get_pen_animals(pen["id"])

    animal_type = pen_info["animal_type"]
    a_info = ANIMALS[animal_type]

    text = (
        f"{pen_info['emoji']} <b>{pen_info['name']}</b> — Level {pen['level']} "
        f"({len(animals)}/{cap} slots)\n"
    )
    if a_info["feed_type"]:
        feed_info = ANIMAL_FEEDS[a_info["feed_type"]]
        feed_count = await db.get_inventory_item(
            user_id, "feed", a_info["feed_type"]
        )
        text += f"{feed_info['emoji']} {feed_info['name']}: {feed_count}\n"
    text += "\n"
    buttons: list[list] = []

    hungry_ids = []
    ready_ids = []

    for a in animals:
        if a["is_ready"]:
            status = "✅ Ready!"
            ready_ids.append(a["id"])
        elif a["last_fed_at"] is not None:
            status = _time_remaining(a["ready_at"])
        else:
            status = "😴 Hungry"
            hungry_ids.append(a["id"])
        text += f"  {a_info['emoji']} #{a['id']} — {status}\n"

        row = []
        if a["is_ready"]:
            row.append(
                InlineKeyboardButton(
                    text=f"🧺 Collect #{a['id']}",
                    callback_data=_cb(user_id, "collect", a["id"], pen_type),
                )
            )
        elif a["last_fed_at"] is None:
            label = (
                f"🌾 Feed #{a['id']}"
                if a_info["feed_type"]
                else f"🍯 Start #{a['id']}"
            )
            row.append(
                InlineKeyboardButton(
                    text=label,
                    callback_data=_cb(user_id, "feed", a["id"], pen_type),
                )
            )
        if row:
            buttons.append(row)

    # Bulk buttons
    bulk = []
    if hungry_ids:
        bulk_label = (
            "🌾 Feed All Hungry" if a_info["feed_type"] else "🍯 Start All"
        )
        bulk.append(
            InlineKeyboardButton(
                text=bulk_label,
                callback_data=_cb(user_id, "feed_all", pen_type),
            )
        )
    if ready_ids:
        bulk.append(
            InlineKeyboardButton(
                text="🧺 Collect All",
                callback_data=_cb(user_id, "collect_all", pen_type),
            )
        )
    if bulk:
        buttons.append(bulk)

    # Buy animal
    if len(animals) < cap:
        buttons.append([
            InlineKeyboardButton(
                text=f"🐾 Buy {a_info['emoji']} {a_info['name']} (${a_info['cost']:,})",
                callback_data=_cb(user_id, "buy_animal", pen_type),
            )
        ])

    # Make feed (if applicable)
    if a_info["feed_type"]:
        buttons.append([
            InlineKeyboardButton(
                text="🌾 Make Feed",
                callback_data=_cb(user_id, "make_feed", pen_type),
            )
        ])

    buttons.append([
        InlineKeyboardButton(text="« Back", callback_data=_cb(user_id, "main"))
    ])
    return text, InlineKeyboardMarkup(inline_keyboard=buttons)


# ─── command ──────────────────────────────────────────────────────────────────

reg("animals", "🐄 Manage your animal farm [/farm]")


@client.on_message(filters.command(["animals", "farm"]))
async def animals_command(message: Message):
    """Manage your animal farm."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)
    text, kb = await _build_main_view(user.id)
    await message.reply(text, reply_markup=kb)


# ─── callbacks ────────────────────────────────────────────────────────────────


@client.on_callback_query(filters.regex(r"^animals:"))
async def animals_callback(callback: CallbackQuery):
    parts = callback.data.split(":")
    owner_id = int(parts[1])
    action = parts[2]

    if callback.from_user.id != owner_id:
        await callback.answer("These aren't your animals! 🐾", show_alert=True)
        return

    user_id = owner_id

    if action == "main":
        text, kb = await _build_main_view(user_id)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "pen":
        pen_type = parts[3]
        text, kb = await _build_pen_view(user_id, pen_type)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "buy_animal":
        pen_type = parts[3]
        pen = await db.get_pen_by_type(user_id, pen_type)
        if not pen:
            await callback.answer("Pen not found.", show_alert=True)
            return
        cap = _pen_capacity(pen)
        count = await db.get_pen_animal_count(pen["id"])
        if count >= cap:
            await callback.answer(
                "Pen is full! Upgrade it in /shop.", show_alert=True
            )
            return
        animal_type = ANIMAL_PENS[pen_type]["animal_type"]
        a_info = ANIMALS[animal_type]
        cost = a_info["cost"]
        wallet = await db.get_wallet(user_id)
        if wallet["balance"] < cost:
            await callback.answer(
                f"Need ${cost:,}. You have ${wallet['balance']:,}.",
                show_alert=True,
            )
            return
        await db.add_balance(user_id, -cost, f"Bought {a_info['name']}")
        await db.buy_animal(pen["id"], animal_type)
        await callback.answer(
            f"✅ {a_info['emoji']} {a_info['name']} purchased!",
            show_alert=True,
        )
        text, kb = await _build_pen_view(user_id, pen_type)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )

    elif action == "feed":
        animal_id = int(parts[3])
        pen_type = parts[4]
        pen = await db.get_pen_by_type(user_id, pen_type)
        if not pen:
            await callback.answer("Pen not found.", show_alert=True)
            return
        animals = await db.get_pen_animals(pen["id"])
        animal_row = next((a for a in animals if a["id"] == animal_id), None)
        if not animal_row:
            await callback.answer("Animal not found.", show_alert=True)
            return
        feed_type = ANIMALS[animal_row["animal_type"]]["feed_type"]
        if not feed_type:
            # Bees: no feed needed, just start the production timer
            await db.feed_animal(animal_id)
            await callback.answer("🍯 Started!", show_alert=False)
        else:
            have = await db.get_inventory_item(user_id, "feed", feed_type)
            if have < 1:
                fi = ANIMAL_FEEDS[feed_type]
                ing_str = ", ".join(
                    f"{v}x {k}" for k, v in fi["ingredients"].items()
                )
                await callback.answer(
                    f"No {fi['name']}! Make it from /animals → Make Feed\nNeeds: {ing_str}",
                    show_alert=True,
                )
                return
            await db.remove_inventory_item(user_id, "feed", feed_type, 1)
            await db.feed_animal(animal_id)
            await callback.answer("🌾 Fed!", show_alert=False)
        text, kb = await _build_pen_view(user_id, pen_type)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )

    elif action == "feed_all":
        pen_type = parts[3]
        pen = await db.get_pen_by_type(user_id, pen_type)
        if not pen:
            await callback.answer("Pen not found.", show_alert=True)
            return
        animals = await db.get_pen_animals(pen["id"])
        fed = 0
        for a in animals:
            if a["is_ready"] or a["last_fed_at"] is not None:
                continue
            a_info = ANIMALS[a["animal_type"]]
            if not a_info["feed_type"]:
                await db.feed_animal(a["id"])
                fed += 1
                continue
            have = await db.get_inventory_item(
                user_id, "feed", a_info["feed_type"]
            )
            if have >= 1:
                await db.remove_inventory_item(
                    user_id, "feed", a_info["feed_type"], 1
                )
                await db.feed_animal(a["id"])
                fed += 1
        await callback.answer(f"🌾 Fed {fed} animal(s).", show_alert=fed == 0)
        text, kb = await _build_pen_view(user_id, pen_type)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )

    elif action == "collect":
        animal_id = int(parts[3])
        pen_type = parts[4]
        result = await db.collect_animal(animal_id)
        if not result:
            await callback.answer("Not ready yet!", show_alert=True)
            return
        produce_type, qty = result
        await db.add_inventory_item(
            user_id, "animal_produce", produce_type, qty
        )
        emoji = ANIMAL_PRODUCE[produce_type]["emoji"]
        name = ANIMAL_PRODUCE[produce_type]["name"]
        await callback.answer(f"🧺 Got {qty}x {emoji} {name}!", show_alert=True)
        text, kb = await _build_pen_view(user_id, pen_type)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )

    elif action == "collect_all":
        pen_type = parts[3]
        pen = await db.get_pen_by_type(user_id, pen_type)
        if not pen:
            await callback.answer("Pen not found.", show_alert=True)
            return
        animals = await db.get_pen_animals(pen["id"])
        totals: dict[str, int] = {}
        for a in animals:
            await db.check_and_mark_ready(a["id"])
        animals = await db.get_pen_animals(pen["id"])
        for a in animals:
            if a["is_ready"]:
                result = await db.collect_animal(a["id"])
                if result:
                    p_type, qty = result
                    totals[p_type] = totals.get(p_type, 0) + qty
                    await db.add_inventory_item(
                        user_id, "animal_produce", p_type, qty
                    )
        if totals:
            summary = ", ".join(
                f"{qty}x {ANIMAL_PRODUCE[pt]['emoji']}"
                for pt, qty in totals.items()
            )
            await callback.answer(f"🧺 Collected: {summary}", show_alert=True)
        else:
            await callback.answer("Nothing ready to collect.", show_alert=True)
        text, kb = await _build_pen_view(user_id, pen_type)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )

    elif action == "make_feed":
        pen_type = parts[3]
        animal_type = ANIMAL_PENS[pen_type]["animal_type"]
        feed_type = ANIMALS[animal_type]["feed_type"]
        if not feed_type:
            await callback.answer(
                "These animals don't need feed.", show_alert=True
            )
            return
        feed_info = ANIMAL_FEEDS[feed_type]
        max_batches = 9_999
        lines = []
        for ing, needed in feed_info["ingredients"].items():
            have = await db.get_inventory_item(user_id, "harvest", ing)
            status = "✅" if have >= needed else "❌"
            lines.append(
                f"  {status} {get_crop_display_emoji(ing)} {needed}x "
                f"{get_item_display_name(ing)} (have {have})"
            )
            max_batches = min(max_batches, have // needed if needed else 0)
        text = (
            f"🌾 <b>Make {html.escape(feed_info['name'])}</b>\n\n"
            + "\n".join(lines)
            + "\n\n<b>How many batches?</b>"
        )
        row = []
        for qty in [1, 5, 10, 50]:
            if qty <= max_batches:
                row.append(
                    InlineKeyboardButton(
                        text=f"{qty}x",
                        callback_data=_cb(
                            user_id, "make_feed_confirm", pen_type, qty
                        ),
                    )
                )
        if max_batches > 0 and max_batches not in [1, 5, 10, 50]:
            row.append(
                InlineKeyboardButton(
                    text=f"Max ({max_batches}x)",
                    callback_data=_cb(
                        user_id, "make_feed_confirm", pen_type, max_batches
                    ),
                )
            )
        btns: list[list] = []
        if row:
            btns.append(row)
        btns.append([
            InlineKeyboardButton(
                text="« Back", callback_data=_cb(user_id, "pen", pen_type)
            )
        ])
        kb = InlineKeyboardMarkup(inline_keyboard=btns)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )
        await callback.answer()

    elif action == "make_feed_confirm":
        pen_type = parts[3]
        qty = int(parts[4])
        animal_type = ANIMAL_PENS[pen_type]["animal_type"]
        feed_type = ANIMALS[animal_type]["feed_type"]
        if not feed_type:
            await callback.answer("No feed needed.", show_alert=True)
            return
        feed_info = ANIMAL_FEEDS[feed_type]
        for ing, needed in feed_info["ingredients"].items():
            ok = await db.remove_inventory_item(
                user_id, "harvest", ing, needed * qty
            )
            if not ok:
                await callback.answer(f"Not enough {ing}!", show_alert=True)
                return
        await db.add_inventory_item(user_id, "feed", feed_type, qty)
        await callback.answer(
            f"✅ Made {qty}x {feed_info['emoji']} {feed_info['name']}!",
            show_alert=True,
        )
        text, kb = await _build_pen_view(user_id, pen_type)
        await queue_it(
            lambda: callback.message.edit_text(text, reply_markup=kb),
            callback.message.chat,
        )
