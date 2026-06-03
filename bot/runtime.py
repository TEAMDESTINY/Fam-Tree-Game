"""Kurigram runtime app — simple middleware + context injection."""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from typing import Any, Optional

from pyrogram import Client, filters
from pyrogram.errors import BadRequest, FloodWait
from pyrogram.handlers import MessageHandler
from pyrogram.types import CallbackQuery, Message


logger = logging.getLogger(__name__)


class SleepModeBotConfigAddon:
    """A class representing a single bot config for sleep mode, containing data used for detecting sleep mode or not"""

    bot_id: int
    bot_username: str
    regex_game_running_patterns: list[str]
    regex_game_not_running_patterns: list[str]
    not_running_check_command: Optional[str]

    def __init__(
        self,
        bot_id: int,
        bot_username: str,
        regex_game_running_patterns: list[str],
        regex_game_not_running_patterns: list[str],
        not_running_check_command: Optional[str],
    ):
        self.bot_id = bot_id
        self.bot_username = bot_username
        self.regex_game_running_patterns = regex_game_running_patterns
        self.regex_game_not_running_patterns = regex_game_not_running_patterns
        self.not_running_check_command = not_running_check_command


class SleepModeConfig:
    sleep_mode_bots_config: list[SleepModeBotConfigAddon] = []
    is_sleep_mode_dict: dict[
        int, bool
    ] = {}  # chat_id: bot username who triggered sleep mode, or False if sleep mode is off
    # a queue for managing self end queue request
    # store dict as chat_id: dict of requests
    # dict of requests is a dict that got  user id as key and command string as value, both associated with request. so we verify its other user not the one who requested end sleep
    end_request_queue: dict[int, dict[int, str]] = {}

    def is_valid_queue_request(
        self, chat_id: int, user_id: int, cmd: str
    ) -> tuple[bool, str]:
        """Check if the request is valid or not, to prevent spam and abuse of the end sleep mode request queue"""

        if chat_id not in self.end_request_queue:
            return False, "No end sleep mode request queue for this chat"
        request_dict = self.end_request_queue[chat_id]

        if user_id in request_dict:
            return (
                False,
                "You have pending request already, please wait for it to be processed",
            )

        if cmd.split("@")[0] in request_dict.values():
            return True, "Valid"
        else:
            return (
                False,
                "wrong cmd"
                + f" (expected {list(request_dict.values())}, got {cmd.split('@')[0]})",
            )

    def clear_queue(self, chat_id: int):
        """Clear the end sleep mode request queue for a specific chat, should be called after processing the end sleep mode request"""
        if chat_id in self.end_request_queue:
            del self.end_request_queue[chat_id]

    def add_queue_request(self, chat_id: int, user_id: int, cmd: str):
        """Add request to queue"""
        if chat_id not in self.end_request_queue:
            self.end_request_queue[chat_id] = {}
        self.end_request_queue[chat_id][user_id] = cmd

    def is_sleep_mode_on(self, chat_id: int) -> bool:
        """Check if sleep mode is on or not"""
        return bool(self.is_sleep_mode_dict.get(chat_id, False))

    def sleep_mode_triggered_by(self, chat_id: int) -> str | bool:
        return self.is_sleep_mode_dict.get(chat_id, False)

    def turn_on_sleep_mode(self, chat_id: int, by_bot_username: str):
        """Turn on sleep mode"""
        self.is_sleep_mode_dict[chat_id] = by_bot_username

    def turn_off_sleep_mode(self, chat_id: int):
        """Turn off sleep mode"""
        self.is_sleep_mode_dict[chat_id] = False

        # clear queue
        self.clear_queue(chat_id)

    def does_this_trigger_sleep_mode(
        self, bot_username: str, text: str
    ) -> bool:
        """Check if this trigger sleep mode for chat"""

        config_for_bot = [
            config
            for config in self.sleep_mode_bots_config
            if config.bot_username == bot_username
        ]
        if not config_for_bot:
            return False
        config_for_bot = config_for_bot[0]

        for pattern in config_for_bot.regex_game_running_patterns:
            if re.search(pattern, text):
                return True

        return False

    def does_this_trigger_end_of_sleep_mode(
        self, bot_username: str, text: str
    ) -> bool:
        """Check if this trigger end sleep for chat"""

        config_for_bot = [
            config
            for config in self.sleep_mode_bots_config
            if config.bot_username == bot_username
        ]
        if not config_for_bot:
            return False
        config_for_bot = config_for_bot[0]

        for pattern in config_for_bot.regex_game_not_running_patterns:
            if re.search(pattern, text):
                return True

        return False

    def give_command_to_check_sleep_mode(
        self, bot_username: str
    ) -> Optional[str]:
        """Give command to check sleep mode for specific bot, if the bot have not_running_check_command in its config, return None"""
        for bot_config in self.sleep_mode_bots_config:
            if bot_config.bot_username == bot_username:
                return bot_config.not_running_check_command
        return None


SLEEP_MODE_CONFIG = SleepModeConfig()

# ---------------------- Add bots for sleep mode checks -----------------------
#

# Codinome
SLEEP_MODE_CONFIG.sleep_mode_bots_config.append(
    SleepModeBotConfigAddon(
        6937708557,
        "@CodinomesBot",
        [
            r"^It('s)? turn of (master|agents?)",
            r"^Time left: \d+ minutes?",
            r"^[\s\w]+\s+(got|attempted)\s+[A-Z]+",
            r"^Match started by",
        ],
        [
            r"^There's no game going on",
            r"TEAM \w+ [^\s]+ WON!\s*🏆",
            r"^Game canceled",
        ],
        "/list",
    ),
)

# Thirty one game bot

SLEEP_MODE_CONFIG.sleep_mode_bots_config.append(
    SleepModeBotConfigAddon(
        402171524,
        "@ThirtyOneBot",
        [
            r"The game is starting!",
            r"These cards are laying on the table:",
            r" skipped their turn.",
            r"Starting new round...",
        ],
        [
            r"The game is finished!",
            r"Not enough players, cancelling game!",
        ],
        None,
    ),
)
# @unobot

SLEEP_MODE_CONFIG.sleep_mode_bots_config.append(
    SleepModeBotConfigAddon(
        118169453,
        "@unobot",
        [
            r"^(First|Next)(\s+[Pp]layer)?: ",
        ],
        [
            r"^Game ended!",
        ],
        None,
    ),
)

# Anya Games, my own bot
SLEEP_MODE_CONFIG.sleep_mode_bots_config.append(
    SleepModeBotConfigAddon(
        8510572053,
        "@AnyaGamesBot",
        [
            r"A game is already running",
            r"Auto-starting game",
            r"Game started by",
            r"^Next(\s+[Pp]layer)?: ",
            r"^UNO.*Next(\s+[Pp]layer)?: ",
        ],
        [
            r"Game ([Ss]topped|[Ee]nded)",
            r"#END_GAME",
            r"No game lobby is open!",
            r"❌ No game or lobby is currently active in this chat.",
            r"wins? by default",
            r"Game Over.*Final Rankings?",
        ],
        "/is_game_running",
    ),
)


class _AppRuntime:
    def __init__(self) -> None:
        self._client: Client | None = None
        self._db: Any = None
        self._config: Any = None
        self._update_max_age_seconds: int = 300
        self._processing_users: set[int] = set()
        self._user_click_timestamps: dict[int, list[float]] = {}
        self._callback_allow = 5
        self._callback_window = 10.0

    def bind(
        self,
        client: Client,
        *,
        db: Any,
        config: Any,
        update_max_age_seconds: int = 300,
    ) -> None:
        self._client = client
        self._db = db
        self._config = config
        self._update_max_age_seconds = update_max_age_seconds
        self._register_middlewares()

    # ── Middleware ────────────────────────────────────────────────────────

    def _register_middlewares(self) -> None:
        assert self._client is not None

        @self._client.on_message(filters.all, group=-1000)
        async def _pre_message(message: Message):
            if await self._guard_blocked_message(message):
                message.stop_propagation()
                return

            if await self.handle_sleep_mode_and_tell_if_i_should_stop(
                message
            ) or self._is_stale(message):
                message.stop_propagation()
                return

            # Upsert chat metadata
            try:
                if message.chat:
                    await self._db.upsert_chat(message.chat)
            except Exception as e:
                logger.exception(f"Chat upsert error: {e}")

            if (
                message.text
                and 1 <= len(message.text.split()) <= 4
                and not message.text.startswith("/")
            ):
                try:
                    from bot.plugins.four_pic import guess_command as guess_4pic
                    from bot.plugins.nation import guess_command as guess_nation

                    four_pic_game = await self._db.get_four_pic_game(
                        message.chat.id
                    )
                    nation_game = await self._db.get_nation_game(
                        message.chat.id
                    )

                    if four_pic_game:
                        await guess_4pic(message, self._db, True)
                    if nation_game:
                        await guess_nation(message, self._db, True)
                except Exception as e:
                    logger.exception(f"Auto guess routing error: {e}")

            message.continue_propagation()

        @self._client.on_callback_query(filters.all, group=-1000)
        async def _pre_callback(callback: CallbackQuery):
            if await self._guard_blocked_callback(callback):
                callback.stop_propagation()
                return
            if self._is_stale(callback):
                callback.stop_propagation()
                return
            if await self._guard_callback_rate_limit(callback):
                callback.stop_propagation()
                return
            callback.continue_propagation()

    # ── Helpers ───────────────────────────────────────────────────────────

    async def handle_sleep_mode_and_tell_if_i_should_stop(
        self, message: Message
    ) -> bool:
        """This method handle if its sleep mode or not and inform user about it.
        Sleep mode is a mode in which bot will not work as it detected other game bot are having a ongoing game in the chat, so it will not interfere and confuse people
        """

        chat_id = message.chat.id if message.chat else None
        user = message.from_user
        is_bot = user and user.is_bot
        text = message.text or message.caption or ""
        cmd = text.split()[0] if text else ""
        is_cmd = cmd.startswith("/")
        _cmd_parts = cmd.split("@")
        _cmd_stem = _cmd_parts[0].lstrip("/")
        _cmd_target = _cmd_parts[1].lower() if len(_cmd_parts) > 1 else None
        _our_username = (
            (self._client.me.username or "").lower()
            if self._client and self._client.me
            else None
        )
        # A command is "for us" only when no @bot is specified, or the @bot matches ours
        is_cmd_to_me = (
            is_cmd
            and _cmd_stem in self.get_registered_commands()
            and (
                _cmd_target is None
                or (_our_username and _cmd_target == _our_username)
            )
        )
        owner_id = getattr(self._config, "owner_id", None)
        is_owner = owner_id is not None and user and user.id == owner_id
        if is_bot:
            username_normalized = "@" + (user.username or "").lstrip("@")
            is_sleep_mode = SLEEP_MODE_CONFIG.is_sleep_mode_on(chat_id)
            trigger_sleep = SLEEP_MODE_CONFIG.does_this_trigger_sleep_mode(
                username_normalized, text
            )
            trigger_end_sleep = (
                SLEEP_MODE_CONFIG.does_this_trigger_end_of_sleep_mode(
                    username_normalized, text
                )
            )

            sleep_mode_triggered_by = SLEEP_MODE_CONFIG.sleep_mode_triggered_by(
                chat_id
            )
            if (
                is_sleep_mode
                and trigger_end_sleep
                and username_normalized == sleep_mode_triggered_by
            ):
                SLEEP_MODE_CONFIG.turn_off_sleep_mode(chat_id)
                await message.reply_text(
                    "DETECTED end of game, sleep mode turned off."
                )
            if not is_sleep_mode and trigger_sleep:
                SLEEP_MODE_CONFIG.turn_on_sleep_mode(
                    chat_id, username_normalized
                )
                await message.reply_text(
                    "DETECTED game start, sleep mode turned on."
                )
            return True  # bot message shouldn't be processed by other handlers

        if is_cmd and not is_cmd_to_me:
            # block command that are not for us
            return True

        cmd_prefix = "stop_sleep"
        is_stop_cmd = text.startswith("/" + cmd_prefix)

        if is_stop_cmd:
            is_valid, reason = SLEEP_MODE_CONFIG.is_valid_queue_request(
                chat_id, user.id, text.split()[0]
            )

            if not is_valid:
                await message.reply_text(
                    "Ah, that's not valud queue request. Reason : " + reason
                )

            else:
                SLEEP_MODE_CONFIG.turn_off_sleep_mode(chat_id)
                await message.reply_text(
                    f"Done, sleep mode turned off.\n\n{user.mention()} verified stop, blame them if this is wrong :P"
                )

            return True

        if is_owner:
            return False

        if SLEEP_MODE_CONFIG.is_sleep_mode_on(chat_id):
            sleep_mode_triggered_by = SLEEP_MODE_CONFIG.sleep_mode_triggered_by(
                chat_id
            )
            queue_cmd = f"/{cmd_prefix}_{user.id}"
            given_cmd = SLEEP_MODE_CONFIG.give_command_to_check_sleep_mode(
                sleep_mode_triggered_by
            )
            if not given_cmd:
                SLEEP_MODE_CONFIG.add_queue_request(chat_id, user.id, queue_cmd)

                if is_cmd_to_me:
                    await message.reply_text(
                        f"Sleep mode is on for {sleep_mode_triggered_by} . If mistaken, ask someone to verify stop sleep with {queue_cmd}"
                    )
            else:
                full_given_cmd = (
                    given_cmd.split("@")[0]
                    + "@"
                    + sleep_mode_triggered_by.strip("@")
                )
                full_given_cmd = "/" + full_given_cmd.strip("/")
                if is_cmd_to_me:
                    await message.reply_text(
                        f"Sleep mode is on for {sleep_mode_triggered_by} . If mistaken, click this command {full_given_cmd} to check for end message"
                    )
            return True

        return False

    async def _guard_blocked_message(self, message: Message) -> bool:
        """Return True if the sender is blocked (and the message handled)."""
        user = message.from_user
        if user is None or user.is_bot:
            return False
        owner_id = getattr(self._config, "owner_id", None)
        if owner_id is not None and user.id == owner_id:
            return False
        try:
            reason = await self._db.is_blocked(user.id)
        except Exception as e:
            logger.exception("Block check failed for user %s: %s", user.id, e)
            return False
        if reason is None:
            return False
        try:
            if not (message.text or message.caption or "").startswith("/"):
                return True  # silently drop blocked user non command message
            ban_text = "🚫 You are banned from using this bot."
            if reason:
                ban_text += f"\nReason: <i>{reason}</i>"
            ban_text += "\n\nYou can ask for unban via /feedback to communicate with owner."
            ban_text += "\n<i>If you want to run your own Fam Tree, wait for a while until this one gets opensourced.</i>"
            await message.reply(ban_text)
        except Exception as e:
            logger.warning("Failed to send block notice to %s: %s", user.id, e)
        return True

    async def _guard_blocked_callback(self, callback: CallbackQuery) -> bool:
        """Return True if the clicker is blocked (and the callback handled)."""
        user = callback.from_user
        if user is None or user.is_bot:
            return False
        owner_id = getattr(self._config, "owner_id", None)
        if owner_id is not None and user.id == owner_id:
            return False
        try:
            reason = await self._db.is_blocked(user.id)
        except Exception as e:
            logger.exception("Block check failed for user %s: %s", user.id, e)
            return False
        if reason is None:
            return False
        try:
            ban_text = "🚫 You are banned from using this bot."
            if reason:
                ban_text += f"\nReason: {reason}"
            ban_text += "\n\nUse /feedback in private to ask for unban."
            await callback.answer(ban_text, show_alert=True)
        except Exception:
            pass
        return True

    def _is_stale(self, event: Any) -> bool:
        date = getattr(event, "date", None)
        if not isinstance(date, datetime):
            return False
        return (time.time() - date.timestamp()) > self._update_max_age_seconds

    def _is_private_chat(self, chat: Any) -> bool:
        return chat is not None and str(
            getattr(chat, "type", "")
        ).lower().endswith("private")

    async def _guard_callback_rate_limit(self, callback: CallbackQuery) -> bool:
        user = callback.from_user
        if user is None or callback.message is None:
            return False
        if self._is_private_chat(callback.message.chat):
            return False

        user_id = user.id
        if user_id in self._processing_users:
            try:
                await callback.answer(
                    "Your old callback is being handled", show_alert=True
                )
            except FloodWait as e:
                try:
                    await callback.answer(
                        f"⏳ Go slow! Wait {getattr(e, 'value', 5)}s.",
                        show_alert=True,
                    )
                except Exception:
                    pass
            except BadRequest:
                pass
            return True

        now = time.time()
        ts_list = self._user_click_timestamps.setdefault(user_id, [])
        ts_list[:] = [t for t in ts_list if t >= now - self._callback_window]
        if len(ts_list) >= self._callback_allow:
            try:
                await callback.answer(
                    "You can click buttons 5 times in 10 seconds",
                    show_alert=True,
                )
            except Exception:
                pass
            return True

        ts_list.append(now)
        return False

    def get_registered_commands(self) -> list[str]:
        """
        Extract all /commands registered in the Pyrogram client dispatcher.

        Safe against:
        - nested filters (&, |)
        - non-iterable base filters
        - duplicate commands
        """

        assert self._client is not None, "Client is not bound"

        def _extract(flt) -> list[str]:
            if not flt:
                return []

            cmds = []

            # direct command filter
            if hasattr(flt, "commands"):
                cmds.extend(flt.commands)

            base = getattr(flt, "base", None)

            # IMPORTANT: base is not always iterable
            if isinstance(base, (list, tuple, set)):
                for sub in base:
                    cmds.extend(_extract(sub))
            elif base is not None and base is not flt:
                cmds.extend(_extract(base))

            return cmds

        found: list[str] = []

        for _, handlers in self._client.dispatcher.groups.items():
            for handler in handlers:
                if isinstance(handler, MessageHandler):
                    found.extend(_extract(handler.filters))

        return sorted(set(found))


runtime_app = _AppRuntime()
