import asyncio
from pathlib import Path

from pyrogram import Client as Bot
from pyrogram.types import FSInputFile, InputSticker


async def main():
    print("=== Add Single Sticker to Existing Pack ===\n")

    api_id = int(input("API_ID: ").strip())
    api_hash = input("API_HASH: ").strip()
    bot_token = input("Bot token: ").strip()
    pack_name = input(
        "Pack name (e.g. alchemy_fanon_emojis_1_out_of_14_by_kcoder_bot): "
    ).strip()
    image_path = input("Image path (PNG file): ").strip()
    keywords_raw = input("Keywords (space-separated, max 10): ").strip()
    owner_id = input("Owner user ID [866874030]: ").strip() or "866874030"

    image_path = Path(image_path)
    if not image_path.exists():
        print(f"[ERROR] File not found: {image_path}")
        return

    if image_path.suffix.lower() != ".png":
        print("[WARNING] File is not a .png — Telegram may reject it.")

    keywords = list(set(keywords_raw.split()))[:10]
    owner_id = int(owner_id)

    bot = None
    try:
        bot = Bot(
            "fanon_add_sticker_bot",
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
        )
        await bot.start()

        sticker = InputSticker(
            sticker=FSInputFile(image_path),
            format="static",
            emoji_list=["🧪"],
            keywords=keywords,
        )

        # Snapshot sticker count BEFORE adding
        sticker_set_before = await bot.get_sticker_set(pack_name)
        count_before = len(sticker_set_before.stickers)

        print(f"\nAdding '{image_path.name}' to pack '{pack_name}'...")

        await bot.add_sticker_to_set(
            user_id=owner_id,
            name=pack_name,
            sticker=sticker,
        )

        # Fetch updated pack and find the new sticker by position
        sticker_set_after = await bot.get_sticker_set(pack_name)
        count_after = len(sticker_set_after.stickers)

        if count_after > count_before:
            new_sticker = sticker_set_after.stickers[-1]
            emoji_id = new_sticker.custom_emoji_id
            print("\nSticker added successfully!")
            print(f"Custom Emoji ID: {emoji_id}")
            print(f"Pack sticker count: {count_before} → {count_after}")
            print(f"View pack: https://t.me/addemoji/{pack_name}")
        else:
            print(
                "\n[WARNING] Sticker may not have been added — count did not increase."
            )
            print(
                f"Pack sticker count before: {count_before}, after: {count_after}"
            )

    except Exception as e:
        print(f"[ERROR] {type(e).__name__}: {e}")

    finally:
        if bot:
            await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
