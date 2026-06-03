import asyncio
import json
import math
from pathlib import Path

import cairosvg
from pyrogram import Client as Bot
from pyrogram.types import FSInputFile, InputSticker

OWNER_ID = 866874030
MAX_STICKERS_PER_SET = 200
JSON_FILE = "db.json"
SVG_DIR = Path("svgs")
PNG_DIR = Path("pngs")
PNG_DIR.mkdir(exist_ok=True)


def svg_to_png(name: str):
    svg_path = SVG_DIR / f"{name}.svg"
    png_path = PNG_DIR / f"{name}.png"
    if not png_path.exists():
        cairosvg.svg2png(
            url=str(svg_path),
            write_to=str(png_path),
            output_width=100,
            output_height=100,
        )
    return png_path


async def main():
    API_ID = int(input("Enter API_ID: ").strip())
    API_HASH = input("Enter API_HASH: ").strip()
    BOT_TOKEN = input("Enter bot token: ").strip()
    bot = Bot(
        "make_pack_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
    )
    await bot.start()

    try:
        data = json.load(open(JSON_FILE))
        items = data["items"]
        stickers = []

        # Prepare stickers
        print("Preparing stickers...")
        for i, (name, obj) in enumerate(items.items(), start=1):
            if not obj.get("image_url"):
                continue
            png_path = svg_to_png(name)
            keywords = list(set(name.replace("-", " ").split()))[:10]
            sticker = InputSticker(
                sticker=FSInputFile(png_path),
                format="static",
                emoji_list=["🧪"],
                keywords=keywords,
            )
            stickers.append((name, sticker))
            print(f"[{len(stickers)}/{len(items)}] Prepared {name}", end="\r")

        print(f"\nTotal stickers to upload: {len(stickers)}")

        # Calculate number of packs needed
        total_packs = math.ceil(len(stickers) / MAX_STICKERS_PER_SET)
        print(f"Will create {total_packs} sticker pack(s)")

        all_mappings = {}

        # Create packs
        for pack_idx in range(total_packs):
            pack_number = pack_idx + 1
            pack_name = f"alchemy_emojis_{pack_number}_out_of_{total_packs}_by_kcoder_bot"
            pack_title = f"Alchemy Emojis Part {pack_number}/{total_packs} By @SaudagarAli"

            start_idx = pack_idx * MAX_STICKERS_PER_SET
            end_idx = start_idx + MAX_STICKERS_PER_SET
            pack_stickers = stickers[start_idx:end_idx]

            print(f"\n--- Pack {pack_number}/{total_packs} ---")
            print(f"Creating sticker set: {pack_name}")
            print(f"Stickers in this pack: {len(pack_stickers)}")

            # First batch (up to 50 for creation)
            first_batch = pack_stickers[:50]
            await bot.create_new_sticker_set(
                user_id=OWNER_ID,
                name=pack_name,
                title=pack_title,
                stickers=[s for _, s in first_batch],
                sticker_type="custom_emoji",
            )
            print(
                f"[{len(first_batch)}/{len(pack_stickers)}] Sticker set created!"
            )

            # Add remaining stickers
            for i, (name, sticker) in enumerate(pack_stickers[50:], start=51):
                print(f"[{i}/{len(pack_stickers)}] Adding {name}")
                await bot.add_sticker_to_set(
                    user_id=OWNER_ID,
                    name=pack_name,
                    sticker=sticker,
                )

            # Fetch set to map emoji IDs
            print("Fetching sticker set to build mapping...")
            sticker_set = await bot.get_sticker_set(pack_name)
            mapping = {}
            for st, (name, _) in zip(sticker_set.stickers, pack_stickers):
                mapping[name] = st.custom_emoji_id

            all_mappings.update(mapping)
            print(f"Pack {pack_number} complete! {len(mapping)} emojis mapped.")
            print(f"Pack URL: https://t.me/addemoji/{pack_name}")

        # Save all mappings
        with open("mapping.json", "w") as f:
            json.dump(all_mappings, f, indent=2)

        print("\n=== ALL DONE ===")
        print(f"Total emojis mapped: {len(all_mappings)}")
        print("Mapping saved to mapping.json")

    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
