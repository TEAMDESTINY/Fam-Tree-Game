"""Start and help commands."""

from bot.queue_it import queue_it

from pyrogram import Client as Bot
from pyrogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from bot.database import Database
from pyrogram import filters
from bot.client import client
from bot.database import db


# Help sections with content
HELP_SECTIONS = {
    "main": {
        "title": "🌳 Family Tree Bot",
        "content": """
Build your virtual family tree and friend circles!

Select a category below to learn more:

<b>Why bot is slow and laggy?</b>
If you are using bot in group, it can send messages or edit messages max 20 time within single minute due to telegram limitation. So to not have this issue, bot responses are queued which can make your button click or command run response slow or more slow if multiple people are using bot at same time.

<b>So what to do?</b>
<blockquote>Use bot in its dm if you want speed response when you are gardening, doing ripple or spammy use. Telegram just limit one edit/send per second so in dm there is no need of queueing system so you get response from bot instantly.</blockquote>

Use /guide_bot_slow for quick reference.
""",
        "buttons": [
            ("👨‍👩‍👧‍👦 Family", "help:family"),
            ("🤝 Friends", "help:friends"),
            ("💰 Economy", "help:economy"),
            ("🎰 Gambling", "help:gambling"),
            ("🏭 Factory", "help:factory"),
            ("🌻 Garden", "help:garden"),
            ("👤 Profile", "help:profile"),
            ("🔮 Future", "help:future"),
        ],
    },
    "family": {
        "title": "👨‍👩‍👧‍👦 Family Commands",
        "content": """
<b>📊 View Family:</b>
/tree - View your family tree image
/tree @user - View someone else's tree
/family - Interactive family explorer
/relations - List your close family members

<b>👶 Create Relationships:</b>
/adopt @user - Adopt someone as your child
/marry @user - Send marriage proposal
/siblings @user - Ask to be siblings
/makeparent @user - Ask someone to adopt you

<b>💔 Remove Relationships:</b>
/disown - Remove one of your children
/divorce - Remove a spouse
/removesibling - Remove sibling relationship
/runaway - Leave all your parents

<i>Tip: Reply to someone or mention them!</i>
""",
        "buttons": [("◀️ Back", "help:main")],
    },
    "friends": {
        "title": "🤝 Friend Commands",
        "content": """
<b>📊 View Friends:</b>
/circle - View friend circle image
/friends - Same as /circle
/ratings - See ratings you've given
/suggestions - Get friend suggestions

<b>➕ Add Friends:</b>
/friend @user - Send friend request
/flink - Get shareable friend link
/fsearch &lt;name&gt; - Search for friends

<b>⭐ Rate & Remove:</b>
/rate @user 1-5 - Rate a friend
/unfriend - Remove a friend

<i>Tip: Circle depth can be expanded with +/- buttons!</i>
""",
        "buttons": [("◀️ Back", "help:main")],
    },
    "economy": {
        "title": "💰 Economy Commands",
        "content": """
<b>💵 Wallet:</b>
/balance - Check your current balance
/transactions - View recent transactions

<b>🎁 Daily Rewards:</b>
/daily - Claim daily $2,000 + random gem
/gem - Check your current gem
/gemlist - View all gem types

<b>✨ Gem Fusion:</b>
/fuse @user - Fuse gems with someone
• Both must have the same gem type
• Rarer gems = bigger fusion bonus!

<b>💎 Gem Rarity:</b>
Diamond > Amethyst > Sapphire > Emerald > Ruby
""",
        "buttons": [("◀️ Back", "help:main")],
    },
    "gambling": {
        "title": "🎰 Gambling Games",
        "content": """
<b>🌻 Ripple:</b>
/ripple [amount] - Start ripple game
• Pick 1 of 3 grass patches
• 70% 🌻 Sunflower = 1.5x prize
• 30% 🐍 Snake = lose everything!
• Take prize anytime or keep going

<b>🎲 Rbet (Quick Ripple):</b>
/rbet [amount] - Start/continue rbet
/rtake - Take your winnings
• Faster gameplay, same odds
• Keep typing /rbet to multiply!

<b>🎟️ Lottery:</b>
/lottery [amount] - Start group lottery
• Everyone stakes same amount
• Winner takes all!

<b>📊 Statistics:</b>
/bets - View your gambling stats
""",
        "buttons": [("◀️ Back", "help:main")],
    },
    "factory": {
        "title": "🏭 Factory Commands",
        "content": """
<b>🏭 Your Factory:</b>
/factory - View factory & workers
• Send idle workers to work
• Workers earn $500 per 1-hour shift
• Expand to hire more workers

<b>👷 Worker Management:</b>
/hire @user - Hire someone as worker
/fire @user - Remove a worker
/feedworker @user [food] - Reduce fatigue
/workerstats - View worker profile

<b>⚙️ Workers:</b>
• Workers gain XP from shifts
• Higher level = work at more factories
• Can't work if fatigue ≥ 80%
• Feed them to reduce fatigue!

<i>Tip: Use /block_hire to prevent hiring someone!</i>
""",
        "buttons": [("◀️ Back", "help:main")],
    },
    "garden": {
        "title": "🌻 Garden Commands",
        "content": """
<b>🌻 Your Garden:</b>
/garden - View garden & inventory
/catalog - View all crop prices

<b>🌱 Growing:</b>
/plant &lt;crop&gt; [qty] - Plant seeds
/harvest - Harvest ready crops
/sell &lt;crop&gt; [qty] - Sell for money
/sell all - Sell everything

<b>🛒 Shop & Cooking:</b>
/shop - Buy seeds & machines
/cook - Cook food with machines
/inventory - View your items
/machines - View owned machines

<b>🏪 Marketplace:</b>
/market sell &lt;crop&gt; &lt;qty&gt; &lt;price&gt;
/buy &lt;id&gt; [qty] - Buy from players

<b>🌿 Help Others:</b>
/fertilize @user - Speed up their garden!
""",
        "buttons": [("◀️ Back", "help:main")],
    },
    "profile": {
        "title": "👤 Profile Commands",
        "content": """
<b>🖼️ Profile Picture:</b>
/setpic - Set profile picture
• Reply to an image, OR
• Click button to use your Telegram photo

<b>📝 Feedback:</b>
/feedback &lt;message&gt; - Send feedback to admins

<b>ℹ️ About:</b>
Your profile is global across all groups!
Profile pictures appear in:
• Family tree images
• Friend circle graphs
• Marriage cards
""",
        "buttons": [("◀️ Back", "help:main")],
    },
    "future": {
        "title": "🔮 Future Plans",
        "content": """
<b>🎮 Upcoming Features:</b>

<blockquote expandable>
<b>👮 Jobs System</b>
• Police, Thief, Gangster, Doctor
• Interactive mini-games
• Inter-connected gameplay
</blockquote>

<blockquote expandable>
<b>🎯 Sonar Game</b>
• Find hidden treasures on a grid
• Distance clues show how far
• Diagonal movement allowed!
</blockquote>

<blockquote expandable>
<b>🐟 Fishing Mini-Game</b>
• Catch fish at various spots
• Rare catches = big rewards
• Trade fish at market
</blockquote>

""",
        "buttons": [("◀️ Back", "help:main")],
    },
}


def get_help_keyboard(section: str) -> InlineKeyboardMarkup:
    """Create keyboard for help section."""
    buttons = HELP_SECTIONS.get(section, {}).get("buttons", [])

    # Arrange buttons in rows of 2
    rows = []
    current_row = []
    for text, callback_data in buttons:
        current_row.append(
            InlineKeyboardButton(text=text, callback_data=callback_data)
        )
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)

    return InlineKeyboardMarkup(inline_keyboard=rows)


def get_help_text(section: str) -> str:
    """Get help text for a section."""
    sec = HELP_SECTIONS.get(section, HELP_SECTIONS["main"])
    return f"<b>{sec['title']}</b>\n{sec['content']}"


@client.on_message(filters.command(["start"]))
async def start_private(
    message: Message,
    bot: Bot,
):
    """Handle /start command."""
    user = message.from_user

    # Ensure user exists in database
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    # Check for deep link parameters
    if message.text and len(message.text.split()) > 1:
        param = message.text.split()[1]
        if param.startswith("flink_"):
            link_code = param[6:]  # Remove "flink_" prefix
            link_user = await db.get_user_by_friend_link(link_code)
            if link_user and link_user["user_id"] != user.id:
                # Check if already friends
                if await db.are_friends(user.id, link_user["user_id"]):
                    await message.reply(
                        f"🤝 You're already friends with {link_user['first_name']}!"
                    )
                else:
                    # Add friendship directly
                    await db.add_friendship(user.id, link_user["user_id"])
                    # Add currency rewards
                    await db.add_balance(user.id, 3000, "New friendship")
                    await db.add_balance(
                        link_user["user_id"], 3000, "New friendship"
                    )
                    await message.reply(
                        f"🎉 <b>New Friendship!</b>\n\n"
                        f"🤝 You're now friends with <b>{link_user['first_name']}</b>!\n"
                        f"💰 Both of you earned $3,000!"
                    )
                return
            elif link_user and link_user["user_id"] == user.id:
                await message.reply("😅 That's your own friend link!")
                return

    await message.reply(
        get_help_text("main"), reply_markup=get_help_keyboard("main")
    )


@client.on_message(filters.command(["help"]))
async def help_command(
    message: Message,
):
    """Handle /help command with interactive menu."""
    user = message.from_user

    # Ensure user exists in database
    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    await message.reply(
        get_help_text("main"), reply_markup=get_help_keyboard("main")
    )


@client.on_callback_query(filters.regex(r"^" + "help:"))
async def handle_help_callback(callback: CallbackQuery):
    """Handle help menu navigation."""
    section = callback.data.split(":")[1]

    if section not in HELP_SECTIONS:
        section = "main"

    try:
        await queue_it(
            lambda: callback.message.edit_text(
                get_help_text(section), reply_markup=get_help_keyboard(section)
            ),
            callback.message.chat,
        )
        await callback.answer()
    except Exception:
        await callback.answer("Already on this page")


GUIDE_BOT_SLOW_TEXT = """
<b>Why bot is slow and laggy?</b>
<blockquote expandable>If you are using bot in group, it can send messages or edit messages max 20 time within single minute due to telegram limitation. So to not have this issue, bot responses are queued which can make your button click or command run response slow or more slow if multiple people are using bot at same time.</blockquote>

<b>Callback button limits (group chats)</b>
<blockquote expandable>The bot applies a per-user rate limit for callback buttons in group chats to prevent spam: you can click a button at most 5 times in 10 seconds. If your previous callback is still being processed you will see "Your old callback is being handled". These limits are only applied in group chats; private (DM) interactions are not rate-limited.</blockquote>

<b>So what to do?</b>
<blockquote expandable>Use bot in its dm if you want speed response when you are gardening, doing ripple or spammy use. Telegram just limit one edit/send per second so in dm there is no need of queueing system so you get response from bot instantly.</blockquote>
"""


@client.on_message(filters.command(["guide_bot_slow"]))
async def guide_bot_slow_command(message: Message):
    """Explain queue-based delays in groups and faster DM usage."""
    await message.reply(GUIDE_BOT_SLOW_TEXT)
