"""Rate-limit-safe coroutine queue for non-private chats.

Use `queue_it(lambda: message.reply(...), chat)` so the coroutine is created
only when it is executed by the queue worker.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

from pyrogram.errors import BadRequest, FloodWait

logger = logging.getLogger(__name__)

MIN_INTERVAL_SECONDS = 3.5
MAX_QUEUE_SIZE = 50


@dataclass
class QueuedItem:
    coro_factory: Callable[[], Awaitable[Any]]
    identifier: Optional[str]
    identifier_version: Optional[int]
    future: asyncio.Future[Any]


class ChatQueue:
    def __init__(self, chat_id: int):
        self.chat_id = chat_id
        self._queue: asyncio.Queue[QueuedItem] = asyncio.Queue(
            maxsize=MAX_QUEUE_SIZE
        )
        self._identifier_versions: dict[str, int] = {}
        self._worker_task: Optional[asyncio.Task[Any]] = None
        self._last_sent_at: float = 0.0

    def start(self) -> None:
        if self._worker_task is None or self._worker_task.done():
            self._worker_task = asyncio.create_task(self._worker())

    async def enqueue(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        identifier: Optional[str] = None,
        replace_old: bool = False,
    ) -> Any:
        identifier_version: Optional[int] = None
        if identifier:
            current_version = self._identifier_versions.get(identifier, 0)
            if replace_old or current_version == 0:
                current_version += 1
                self._identifier_versions[identifier] = current_version
            identifier_version = current_version

        future = asyncio.get_running_loop().create_future()
        item = QueuedItem(
            coro_factory=coro_factory,
            identifier=identifier,
            identifier_version=identifier_version,
            future=future,
        )

        await self._queue.put(item)
        self.start()
        return await future

    def cancel_by_identifier(self, identifier: str) -> bool:
        if identifier in self._identifier_versions:
            self._identifier_versions[identifier] += 1
            return True
        return False

    async def _worker(self) -> None:
        IDLE_TIMEOUT_SECONDS = 60.0  # exit worker after 60s not immdiately

        while True:
            try:
                item = await asyncio.wait_for(
                    self._queue.get(), timeout=IDLE_TIMEOUT_SECONDS
                )
            except asyncio.TimeoutError:
                # Queue has been idle for too long, exit worker
                _group_queues.pop(self.chat_id, None)
                break

            if (
                item.identifier
                and item.identifier_version
                != self._identifier_versions.get(item.identifier)
            ):
                if not item.future.done():
                    item.future.set_result(None)
                self._queue.task_done()
                continue

            elapsed = time.monotonic() - self._last_sent_at
            if self._last_sent_at > 0 and elapsed < MIN_INTERVAL_SECONDS:
                await asyncio.sleep(MIN_INTERVAL_SECONDS - elapsed)

            if (
                item.identifier
                and item.identifier_version
                != self._identifier_versions.get(item.identifier)
            ):
                if not item.future.done():
                    item.future.set_result(None)
                self._queue.task_done()
                continue

            requeued = False
            should_clear_identifier = False
            try:
                result = await item.coro_factory()
                if not item.future.done():
                    item.future.set_result(result)
                should_clear_identifier = True
            except FloodWait as e:
                retry_after = float(getattr(e, "value", 5.0))
                logger.warning(
                    "Rate limited in chat %s, retrying after %.1fs",
                    self.chat_id,
                    retry_after,
                )
                await asyncio.sleep(retry_after)
                await self._queue.put(item)
                requeued = True
            except BadRequest as e:
                harmless_errors = (
                    "message is not modified",
                    "MESSAGE_NOT_MODIFIED",
                    "query is too old",
                    "MESSAGE_ID_INVALID",
                )
                if any(err in str(e) for err in harmless_errors):
                    if not item.future.done():
                        item.future.set_result(None)
                else:
                    if not item.future.done():
                        item.future.set_exception(e)
                should_clear_identifier = True
            except Exception as e:
                logger.error(
                    "Queue error in chat %s: %s",
                    self.chat_id,
                    e,
                    exc_info=True,
                )
                if not item.future.done():
                    item.future.set_exception(e)
                should_clear_identifier = True
            finally:
                self._last_sent_at = time.monotonic()
                self._queue.task_done()
                if should_clear_identifier and item.identifier:
                    current_version = self._identifier_versions.get(
                        item.identifier
                    )
                    if current_version == item.identifier_version:
                        self._identifier_versions.pop(item.identifier, None)

            if requeued:
                continue

        if self._queue.empty():
            queue = _group_queues.get(self.chat_id)
            if queue is self:
                _group_queues.pop(self.chat_id, None)


_group_queues: dict[int, ChatQueue] = {}
_legacy_coroutine_warning_logged = False


def _get_queue(chat_id: int) -> ChatQueue:
    queue = _group_queues.get(chat_id)
    if queue is None:
        queue = ChatQueue(chat_id)
        _group_queues[chat_id] = queue
    return queue


async def queue_it(
    coro_factory: Callable[[], Awaitable[Any]] | Awaitable[Any],
    chat=None,
    identifier: Optional[str] = None,
    replace_old: bool = False,
) -> Any:
    factory = _coerce_coro_factory(coro_factory)
    chat_type = str(getattr(chat, "type", "")).lower() if chat else "private"
    is_private = chat is None or chat_type.endswith("private")

    if is_private:
        return await factory()

    chat_id = chat.id if hasattr(chat, "id") else hash(str(chat))
    queue = _get_queue(chat_id)
    return await queue.enqueue(factory, identifier, replace_old)


def _coerce_coro_factory(
    value: Callable[[], Awaitable[Any]] | Awaitable[Any],
) -> Callable[[], Awaitable[Any]]:
    if callable(value):
        return value

    if asyncio.iscoroutine(value):
        global _legacy_coroutine_warning_logged
        if not _legacy_coroutine_warning_logged:
            logger.warning(
                "queue_it received an already-created coroutine; "
                "use queue_it(lambda: ...) for retry-safe behavior."
            )
            _legacy_coroutine_warning_logged = True

        used = False

        def one_shot_factory() -> Awaitable[Any]:
            nonlocal used
            if used:
                raise RuntimeError(
                    "Cannot retry an already-created coroutine. "
                    "Pass a coroutine factory: queue_it(lambda: ...)."
                )
            used = True
            return value

        return one_shot_factory

    raise TypeError(
        "queue_it expects a zero-arg coroutine factory or coroutine object."
    )


def get_queue_status(chat_id: int) -> dict:
    queue = _group_queues.get(chat_id)
    if queue is None:
        return {"queued": 0, "processing": False}

    processing = bool(queue._worker_task and not queue._worker_task.done())
    return {
        "queued": queue._queue.qsize(),
        "processing": processing,
        "last_sent_at": queue._last_sent_at,
    }
