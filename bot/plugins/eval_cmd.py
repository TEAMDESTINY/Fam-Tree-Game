"""Admin /eval command — intentionally executes arbitrary Python code."""

import asyncio
import html
import io
import textwrap
import traceback

from pyrogram import Client as Bot
from pyrogram import filters
from pyrogram.enums import ChatType
from pyrogram.types import Message

from bot.client import client
from bot.config import Config
from bot.database import db
from bot.plugins.admin import is_admin

_EXEC = getattr(__builtins__, "exec", None) or __import__("builtins").exec


@client.on_message(filters.command(["eval"]) & filters.private)
async def eval_command(message: Message, bot: Bot, config: Config):
    """Execute arbitrary Python in the bot process (admin only, DM only).

    Available variables:
      message / msg  — the /eval Message
      reply / rmsg   — message.reply_to_message
      chat           — message.chat
      user           — message.from_user
      client / bot   — the Pyrogram bot client
      db             — database proxy
      asyncio        — asyncio module
      print          — captured; output shown in reply
    Use `return <expr>` to display a value. Supports await.
    """
    if not is_admin(config, message.from_user.id):
        return

    parts = message.text.split(None, 1) if message.text else []
    if len(parts) < 2:
        await message.reply("Usage: /eval &lt;code&gt;")
        return

    code = parts[1].strip()
    # Strip optional code fences
    if code.startswith("```"):
        code = code.split("\n", 1)[1] if "\n" in code else code[3:]
        if code.endswith("```"):
            code = code[:-3].strip()

    output_buf = io.StringIO()
    local_vars: dict = {
        # message aliases
        "message": message,
        "msg": message,
        # reply-to aliases
        "reply": message.reply_to_message,
        "rmsg": message.reply_to_message,
        # other message fields
        "chat": message.chat,
        "user": message.from_user,
        # bot/client aliases
        "client": bot,
        "bot": bot,
        # utilities
        "db": db,
        "asyncio": asyncio,
        "print": lambda *a, **k: print(*a, file=output_buf, **k),
    }

    # Wrap in async def so `await` works inside the snippet
    indented = textwrap.indent(code, "    ")
    exec_src = f"async def __eval__():\n{indented}\n"

    try:
        _EXEC(compile(exec_src, "<eval>", "exec"), local_vars)
        result = await local_vars["__eval__"]()

        stdout_out = output_buf.getvalue()
        reply_parts = []
        if stdout_out:
            reply_parts.append(
                f"<b>Output:</b>\n<pre>{html.escape(stdout_out.strip())}</pre>"
            )
        if result is not None:
            reply_parts.append(
                f"<b>Return:</b>\n<pre>{html.escape(repr(result))}</pre>"
            )
        reply = "\n".join(reply_parts) or "✅ (no output)"
    except Exception:
        reply = (
            f"❌ <b>Error:</b>\n"
            f"<pre>{html.escape(traceback.format_exc()[-1500:])}</pre>"
        )

    await message.reply(reply[:4096])
