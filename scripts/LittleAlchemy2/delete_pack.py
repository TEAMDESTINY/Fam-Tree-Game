import asyncio

from pyrogram import Client as Bot


async def main():
    API_ID = int(input("Enter API_ID: ").strip())
    API_HASH = input("Enter API_HASH: ").strip()
    BOT_TOKEN = input("Enter bot token: ").strip()
    PACK_NAME = input("Enter sticker pack name (without @): ").strip()
    bot = Bot(
        "delete_pack_bot",
        api_id=API_ID,
        api_hash=API_HASH,
        bot_token=BOT_TOKEN,
    )
    await bot.start()

    try:
        await bot.delete_sticker_set(name=PACK_NAME)
        print(f"Deleted sticker set: {PACK_NAME}")
    except Exception as e:
        print(f"Error: {e}")
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
