import asyncio
import io
import json
import math
import random
import re
from pathlib import Path

import cairosvg
from pyrogram import Client as Bot
from pyrogram.types import FSInputFile, InputSticker
from PIL import Image

# ------------------------------------------------------------
# CONFIG
# ------------------------------------------------------------

OWNER_ID = 866874030
MAX_STICKERS_PER_SET = 200

JSON_FILE = "data/output/fanon_db.json"

SVG_DIR = Path("images_file")
PNG_DIR = Path("images_file_pngs")
PROGRESS_FILE = Path("progress.json")

PNG_DIR.mkdir(exist_ok=True)


# ------------------------------------------------------------
# SAFE FILENAME (CRITICAL FOR CONSISTENCY)
# ------------------------------------------------------------
def safe_filename(name: str) -> str:
    name = re.sub(r'[\\/:*?"<>|]', "_", name)
    name = re.sub(r"\s+", "_", name)
    name = re.sub(r"_+", "_", name)
    return name.strip("_")


# ------------------------------------------------------------
# UNIVERSAL IMAGE CONVERTER
# Handles:
# - real SVG
# - PNG disguised as .svg
# - WebP disguised as .svg
# - JPG disguised as .svg
# ------------------------------------------------------------
def convert_to_png(name: str):
    safe_name = safe_filename(name)

    src_path = SVG_DIR / f"{safe_name}.svg"
    out_path = PNG_DIR / f"{safe_name}.png"

    # XXX: some files failed to convert, so manually did. also one was pdf
    # magick pdf:images_file/Fanon_Sisyphus.svg -resize 512x512 images_file/Fanon_Sisyphus.svg
    # all png are there now so skipping this and returning
    return out_path

    if not src_path.exists():
        print(f"[MISSING FILE] {src_path}")
        return None

    try:
        raw = src_path.read_bytes()

        # --------------------------------------------------------
        # CASE 1: REAL SVG FILE
        # --------------------------------------------------------
        if b"<svg" in raw[:300].lower() or b"<?xml" in raw[:300].lower():
            cairosvg.svg2png(
                bytestring=raw,
                write_to=str(out_path),
                output_width=100,
                output_height=100,
            )
            return out_path

        # --------------------------------------------------------
        # CASE 2: IMAGE FILE (PNG / WEBP / JPG) MISLABELED AS SVG
        # --------------------------------------------------------
        img = Image.open(io.BytesIO(raw)).convert("RGBA")
        img = img.resize((512, 512))
        img.save(out_path, "PNG")

        return out_path

    except Exception as e:
        print(f"[CONVERT FAIL] {name}: {e}")
        return None


# ------------------------------------------------------------
# PROGRESS HELPERS
# ------------------------------------------------------------
def load_progress() -> dict:
    if PROGRESS_FILE.exists():
        with open(PROGRESS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        print(
            f"[RESUME] Found progress: {len(data.get('completed_packs', []))} packs done, "
            f"{len(data.get('mapping', {}))} emojis mapped"
        )
        return data
    return {"completed_packs": [], "mapping": {}}


def save_progress(progress: dict):
    with open(PROGRESS_FILE, "w", encoding="utf-8") as f:
        json.dump(progress, f, indent=2)


# ------------------------------------------------------------
# RETRY HELPER WITH EXPONENTIAL BACKOFF
# ------------------------------------------------------------
async def add_sticker_with_retry(
    bot: Bot,
    user_id: int,
    name: str,
    pack_name: str,
    sticker: InputSticker,
    max_retries: int = 6,
):
    for attempt in range(max_retries):
        try:
            await bot.add_sticker_to_set(
                user_id=user_id,
                name=pack_name,
                sticker=sticker,
            )
            return  # success
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"\n[FAILED] {name} after {max_retries} attempts: {e}")
                raise
            wait = (2**attempt) + random.uniform(0.5, 1.5)
            print(
                f"\n  [RETRY {attempt + 1}/{max_retries - 1}] '{name}': {type(e).__name__} — "
                f"retrying in {wait:.1f}s..."
            )
            await asyncio.sleep(wait)


async def create_pack_with_retry(
    bot: Bot,
    user_id: int,
    pack_name: str,
    pack_title: str,
    first_batch_stickers: list,
    max_retries: int = 6,
):
    for attempt in range(max_retries):
        try:
            await bot.create_new_sticker_set(
                user_id=user_id,
                name=pack_name,
                title=pack_title,
                stickers=first_batch_stickers,
                sticker_type="custom_emoji",
            )
            return  # success
        except Exception as e:
            if attempt == max_retries - 1:
                print(
                    f"\n[FAILED] Create pack '{pack_name}' after {max_retries} attempts: {e}"
                )
                raise
            wait = (2**attempt) + random.uniform(0.5, 1.5)
            print(
                f"\n  [RETRY {attempt + 1}/{max_retries - 1}] Create pack: {type(e).__name__} — "
                f"retrying in {wait:.1f}s..."
            )
            await asyncio.sleep(wait)


# ------------------------------------------------------------
# MAIN BOT FUNCTION
# ------------------------------------------------------------
async def main():
    bot = None
    try:
        # --------------------------------------------------------
        # LOAD FANON DATABASE
        # --------------------------------------------------------
        with open(JSON_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        items = data["items"]

        stickers = []

        # --------------------------------------------------------
        # STEP 1: PREPARE STICKERS
        # --------------------------------------------------------
        print("Preparing stickers...")

        for name, obj in items.items():
            if not obj.get("image_url"):
                continue

            png_path = convert_to_png(name)

            if not png_path:
                continue

            keywords = list(set(name.replace("-", " ").split()))[:10]

            sticker = InputSticker(
                sticker=FSInputFile(png_path),
                format="static",
                emoji_list=["🧪"],
                keywords=keywords,
            )

            stickers.append((name, sticker))

            print(f"[{len(stickers)}] Prepared {name}", end="\r")

        print(f"\nValid stickers: {len(stickers)}")

        # --------------------------------------------------------
        # STEP 2: CALCULATE PACKS
        # --------------------------------------------------------
        total_packs = math.ceil(len(stickers) / MAX_STICKERS_PER_SET)

        print(f"Creating {total_packs} packs")

        # --------------------------------------------------------
        # STEP 3: LOAD PROGRESS & INIT BOT
        # --------------------------------------------------------
        API_ID = int(input("Enter API_ID: ").strip())
        API_HASH = input("Enter API_HASH: ").strip()
        BOT_TOKEN = input("Enter bot token: ").strip()
        bot = Bot(
            "make_fanon_pack_bot",
            api_id=API_ID,
            api_hash=API_HASH,
            bot_token=BOT_TOKEN,
        )
        await bot.start()

        progress = load_progress()
        all_mappings = progress["mapping"]

        # --------------------------------------------------------
        # STEP 4: CREATE / RESUME PACKS
        # --------------------------------------------------------
        for pack_idx in range(total_packs):
            pack_number = pack_idx + 1

            pack_name = f"alchemy_fanon_emojis_{pack_number}_out_of_{total_packs}_by_kcoder_bot"
            pack_title = f"Alchemy Fanon Emojis Part {pack_number}/{total_packs} By @SaudagarAli"

            start = pack_idx * MAX_STICKERS_PER_SET
            end = start + MAX_STICKERS_PER_SET
            pack_stickers = stickers[start:end]

            print(f"\n--- Pack {pack_number}/{total_packs} ---")
            print(f"Pack: {pack_name}")
            print(f"Stickers: {len(pack_stickers)}")

            # Skip already-completed packs
            if pack_name in progress["completed_packs"]:
                print("Already completed — skipping.")
                continue

            # ----------------------------------------------------
            # FIRST 50 = CREATE PACK
            # ----------------------------------------------------
            first_batch = pack_stickers[:50]

            await create_pack_with_retry(
                bot=bot,
                user_id=OWNER_ID,
                pack_name=pack_name,
                pack_title=pack_title,
                first_batch_stickers=[s for _, s in first_batch],
            )

            print("Pack created")

            # Small pause after pack creation
            await asyncio.sleep(1.0)

            # ----------------------------------------------------
            # ADD REMAINING STICKERS (with retry + pacing)
            # ----------------------------------------------------
            failed = []

            for i, (name, sticker) in enumerate(pack_stickers[50:], start=51):
                print(f"[{i}/{len(pack_stickers)}] Adding {name}")

                try:
                    await add_sticker_with_retry(
                        bot=bot,
                        user_id=OWNER_ID,
                        name=name,
                        pack_name=pack_name,
                        sticker=sticker,
                    )
                except Exception:
                    failed.append((name, sticker))

                # Pace requests to avoid triggering Telegram rate limits
                await asyncio.sleep(0.5)

            if failed:
                print(
                    f"\n[WARNING] {len(failed)} stickers failed to add in pack {pack_number}:"
                )
                for name, _ in failed:
                    print(f"  - {name}")

            # ----------------------------------------------------
            # BUILD MAPPING
            # ----------------------------------------------------
            sticker_set = await bot.get_sticker_set(pack_name)

            mapping = {}
            for st, (name, _) in zip(sticker_set.stickers, pack_stickers):
                mapping[name] = st.custom_emoji_id

            all_mappings.update(mapping)

            # Save progress after each completed pack
            progress["completed_packs"].append(pack_name)
            progress["mapping"] = all_mappings
            save_progress(progress)

            print(f"Pack done: https://t.me/addemoji/{pack_name}")

        # --------------------------------------------------------
        # SAVE FINAL MAPPING
        # --------------------------------------------------------
        with open("mapping.json", "w", encoding="utf-8") as f:
            json.dump(all_mappings, f, indent=2)

        # Clean up progress file on full success
        if PROGRESS_FILE.exists():
            PROGRESS_FILE.unlink()
            print("Progress file cleaned up.")

        print("\n=== COMPLETE ===")
        print(f"Total emojis: {len(all_mappings)}")

    finally:
        if bot:
            await bot.stop()


# ------------------------------------------------------------
# ENTRY POINT
# ------------------------------------------------------------
if __name__ == "__main__":
    asyncio.run(main())
