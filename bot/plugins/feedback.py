"""Feedback system - sends feedback directly to bot owner via DM."""

import re


from pyrogram import Client as Bot
from pyrogram import filters
from pyrogram.types import Message, ReplyParameters

from bot.client import client
from bot.config import Config
from bot.database import db


def is_admin(config: Config, user_id: int) -> bool:
    return user_id == config.owner_id


# ── Helpers ────────────────────────────────────────────────────────────────


def _msg_link_html(chat_id: int, msg_id: int, label: str = "Message") -> str:
    """Return a clickable t.me/c link for supergroups, bold fallback otherwise."""
    chat_id_str = str(chat_id)
    if chat_id_str.startswith("-100"):
        return (
            f'<a href="https://t.me/c/{chat_id_str[4:]}/{msg_id}">{label}</a>'
        )
    return f"<b>{label}</b>"


def _parse_feedback(text: str) -> dict:
    """Extract routing fields from a #FEEDBACK message (plain text, no HTML tags)."""
    out: dict = {}
    m = re.search(r"User ID: (\d+)", text)
    if m:
        out["user_id"] = int(m.group(1))
    m = re.search(r"Chat ID: (-?\d+)", text)
    if m:
        out["chat_id"] = int(m.group(1))
    m = re.search(r"Msg ID: (\d+)", text)
    if m:
        out["msg_id"] = int(m.group(1))
    m = re.search(r"📢 (?:Feedback|Reply) from ([^(\n]+)", text)
    if m:
        out["user_name"] = m.group(1).strip()
    return out


def _parse_feedback_response(text: str) -> dict:
    """Extract routing fields from a #FEEDBACK_RESPONSE message."""
    out: dict = {}
    m = re.search(r"Admin DM Msg: (\d+)", text)
    if m:
        out["admin_dm_msg_id"] = int(m.group(1))
    m = re.search(r"User ID: (\d+)", text)
    if m:
        out["user_id"] = int(m.group(1))
    return out


# ── /feedback command ───────────────────────────────────────────────────────


@client.on_message(filters.command(["feedback"]))
async def feedback_command(message: Message, bot: Bot, config: Config):
    """Send feedback directly to the bot owner via DM."""
    user = message.from_user

    await db.upsert_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name,
    )

    parts = message.text.split(None, 1) if message.text else []
    if len(parts) < 2:
        await message.reply(
            "ℹ️ Usage: <code>/feedback &lt;your message&gt;</code>\n\n"
            "Example: <code>/feedback Love this bot! Please add more features.</code>"
        )
        return

    feedback_text = parts[1]

    user_link = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
    username_str = f"@{user.username}" if user.username else "no username"
    chat_title = message.chat.title or "DM"
    msg_id = message.id
    msg_link = _msg_link_html(message.chat.id, msg_id)

    feedback_msg = (
        f"#FEEDBACK\n\n"
        f"📢 Feedback from {user_link} ({username_str})\n"
        f"Chat: {chat_title}\n\n"
        f"{msg_link}:\n"
        f"<blockquote expandable>{feedback_text}</blockquote>\n"
        f"<blockquote expandable>"
        f"User ID: {user.id}\n"
        f"Chat ID: {message.chat.id}\n"
        f"Msg ID: {msg_id}"
        f"</blockquote>"
    )

    try:
        await bot.send_message(config.owner_id, feedback_msg)
        await message.reply(
            "✅ Your feedback has been sent to the bot owner. Thank you!"
        )
    except Exception:
        await message.reply(
            "❌ Failed to send feedback. The bot owner may not have started a conversation with the bot yet."
        )


# ── Reply middleware ────────────────────────────────────────────────────────


@client.on_message(filters.reply, group=1000)
async def handle_feedback_reply(message: Message, bot: Bot, config: Config):
    """Intercept replies to #FEEDBACK / #FEEDBACK_RESPONSE bot messages."""
    replied = message.reply_to_message
    if not replied:
        return

    replied_text = (replied.text or replied.caption or "").strip()

    if replied_text.startswith("#FEEDBACK\n"):
        await _admin_responds(message, bot, config, replied_text)
    elif replied_text.startswith("#FEEDBACK_RESPONSE\n"):
        if message.from_user and message.from_user.id == config.owner_id:
            await _admin_responds(message, bot, config, replied_text)
        else:
            await _user_replies(message, bot, config, replied_text)


async def _admin_responds(
    message: Message, bot: Bot, config: Config, feedback_text: str
):
    """Admin replied to a #FEEDBACK → send #FEEDBACK_RESPONSE to user's chat."""
    if message.from_user.id != config.owner_id:
        return

    response_text = (message.text.html if message.text else None) or (
        message.caption.html if message.caption else None
    )
    if not response_text:
        await message.reply("❌ Please reply with a text message.")
        return

    fields = _parse_feedback(feedback_text)
    chat_id = fields.get("chat_id")
    user_id = fields.get("user_id")
    msg_id = fields.get("msg_id")
    user_name = fields.get("user_name", "User")

    if not chat_id or not user_id:
        await message.reply("❌ Could not parse feedback details.")
        return

    user_link = f"<a href='tg://user?id={user_id}'>{user_name}</a>"
    from_link = f"<a href='tg://user?id={message.from_user.id}'>{message.from_user.first_name}</a>"
    admin_username_str = (
        f"@{message.from_user.username}"
        if message.from_user.username
        else "no username"
    )
    admin_dm_msg_id = message.id
    msg_link = _msg_link_html(chat_id, msg_id) if msg_id else "<b>Message</b>"

    out = (
        f"#FEEDBACK_RESPONSE\n\n"
        f"📩 Response to your feedback\n"
        f"From: {from_link} ({admin_username_str})\n"
        f"To: {user_link}\n\n"
        f"↩️ Reply to this message to send a reply back.\n\n"
        f"{msg_link}:\n"
        f"<blockquote expandable>{response_text}</blockquote>\n"
        f"<blockquote expandable>"
        f"User ID: {user_id}\n"
        f"Admin DM Msg: {admin_dm_msg_id}"
        f"</blockquote>"
    )

    try:
        if msg_id:
            await bot.send_message(
                chat_id,
                out,
                reply_parameters=ReplyParameters(message_id=msg_id),
            )
        else:
            await bot.send_message(chat_id, out)

        try:
            await bot.send_reaction(message.chat.id, message.id, "👍")
        except Exception:
            await message.reply("✅ Response sent successfully!")
    except Exception as e:
        err = str(e)
        await message.reply(f"❌ Failed to send response: {err}")


async def _user_replies(
    message: Message, bot: Bot, config: Config, response_text: str
):
    """User replied to a #FEEDBACK_RESPONSE → forward to admin's DM."""
    reply_text = (message.text.html if message.text else None) or (
        message.caption.html if message.caption else None
    )
    if not reply_text:
        return

    fields = _parse_feedback_response(response_text)
    admin_dm_msg_id = fields.get("admin_dm_msg_id")

    user = message.from_user
    if not user:
        return

    user_link = f"<a href='tg://user?id={user.id}'>{user.first_name}</a>"
    username_str = f"@{user.username}" if user.username else "no username"
    chat_title = message.chat.title or "DM"
    msg_link = _msg_link_html(message.chat.id, message.id)

    forward = (
        f"#FEEDBACK_RESPONSE\n\n"
        f"📢 Reply from {user_link} ({username_str})\n"
        f"Chat: {chat_title}\n\n"
        f"{msg_link}:\n"
        f"<blockquote expandable>{reply_text}</blockquote>\n"
        f"<blockquote expandable>"
        f"User ID: {user.id}\n"
        f"Chat ID: {message.chat.id}\n"
        f"Msg ID: {message.id}\n"
        f"Admin DM Msg: {admin_dm_msg_id}"
        f"</blockquote>"
    )

    try:
        if admin_dm_msg_id:
            await bot.send_message(
                config.owner_id,
                forward,
                reply_parameters=ReplyParameters(message_id=admin_dm_msg_id),
            )
        else:
            await bot.send_message(config.owner_id, forward)

        try:
            await bot.send_reaction(message.chat.id, message.id, "👍")
        except Exception:
            await message.reply("✅ Reply sent to admin!")
    except Exception:
        await message.reply("❌ Failed to forward your reply.")


# ── Admin-only management commands ─────────────────────────────────────────


@client.on_message(filters.command(["feedback_chat_lists"]))
async def feedback_chat_lists(message: Message, config: Config):
    """List all feedback destination chats (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("🔒 This command is for admins only.")
        return

    chats = await db.get_feedback_chats()
    if not chats:
        await message.reply("📭 No feedback chats configured.")
        return

    lines = ["📋 <b>Feedback Destination Chats</b>\n"]
    for chat in chats:
        lines.append(f"• {chat['chat_name']} (<code>{chat['chat_id']}</code>)")

    await message.reply("\n".join(lines))


@client.on_message(filters.command(["add_feedback_chat"]))
async def add_feedback_chat(message: Message, bot: Bot, config: Config):
    """Add current or specified chat as feedback destination (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("🔒 This command is for admins only.")
        return

    parts = message.text.split() if message.text else []
    if len(parts) > 1:
        try:
            chat_id = int(parts[1])
            try:
                chat = await bot.get_chat(chat_id)
                chat_name = chat.title or chat.first_name or str(chat_id)
            except Exception:
                chat_name = f"Chat {chat_id}"
        except ValueError:
            await message.reply(
                "❌ Invalid chat ID. Provide a numeric chat ID."
            )
            return
    else:
        chat_id = message.chat.id
        chat_name = (
            message.chat.title or message.chat.first_name or str(chat_id)
        )

    await db.add_feedback_chat(chat_id, chat_name, message.from_user.id)
    await message.reply(
        f"✅ Added <b>{chat_name}</b> as a feedback destination."
    )


@client.on_message(filters.command(["remove_feedback_chat"]))
async def remove_feedback_chat(message: Message, config: Config):
    """Remove a chat from feedback destinations (admin only)."""
    if not is_admin(config, message.from_user.id):
        await message.reply("🔒 This command is for admins only.")
        return

    parts = message.text.split() if message.text else []
    if len(parts) < 2:
        await message.reply("ℹ️ Usage: /remove_feedback_chat <chat_id>")
        return

    try:
        chat_id = int(parts[1])
    except ValueError:
        await message.reply("❌ Invalid chat ID.")
        return

    await db.remove_feedback_chat(chat_id)
    await message.reply("✅ Chat removed from feedback destinations.")
