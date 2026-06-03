"""Session-wedge watchdog.

Pyrogram's MTProto session can get stuck after a network blip — process
stays alive, asyncio loop stays alive, but updates stop flowing. This
module detects that state and forces a client restart.

Mechanism:
  1. `touch()` is called from `BotClient._wrap` on every handled update.
  2. A background task wakes every `CHECK_INTERVAL` seconds. If the bot
     has been idle for longer than `IDLE_THRESHOLD`, it actively probes
     the session with `get_me` (bounded by `PROBE_TIMEOUT`).
  3. If the probe times out or raises, the session is considered wedged
     and `client.restart()` is called (bounded by `RESTART_TIMEOUT`).
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

logger = logging.getLogger(__name__)

CHECK_INTERVAL = 60.0
IDLE_THRESHOLD = 180.0
PROBE_TIMEOUT = 15.0
RESTART_TIMEOUT = 60.0

_last_update_ts: float = time.monotonic()


def touch() -> None:
    global _last_update_ts
    _last_update_ts = time.monotonic()


async def update_watchdog(client: Any) -> None:
    global _last_update_ts
    _last_update_ts = time.monotonic()

    while True:
        await asyncio.sleep(CHECK_INTERVAL)
        idle_for = time.monotonic() - _last_update_ts
        if idle_for < IDLE_THRESHOLD:
            continue

        try:
            await asyncio.wait_for(client.get_me(), timeout=PROBE_TIMEOUT)
            _last_update_ts = time.monotonic()
            continue
        except Exception as probe_exc:
            logger.error(
                "Watchdog: session looks wedged (idle %.0fs, probe failed: %r); "
                "forcing client.restart()",
                idle_for,
                probe_exc,
            )

        try:
            await asyncio.wait_for(client.restart(), timeout=RESTART_TIMEOUT)
            logger.warning("Watchdog: client.restart() completed")
        except Exception as restart_exc:
            logger.exception(
                "Watchdog: client.restart() failed: %r", restart_exc
            )

        _last_update_ts = time.monotonic()
