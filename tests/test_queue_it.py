"""Tests for bot.queue_it module."""

import asyncio
import time
from unittest.mock import MagicMock

import pytest

from bot.queue_it import (
    ChatQueue,
    _group_queues,
    _get_queue,
    get_queue_status,
    queue_it,
)


class FakeChat:
    """Fake chat object for testing."""

    def __init__(self, chat_id: int, chat_type: str = "private"):
        self.id = chat_id
        self.type = chat_type


@pytest.fixture(autouse=True)
async def clear_queues():
    """Clear all queues before each test."""
    _group_queues.clear()
    yield
    for queue in list(_group_queues.values()):
        if queue._worker_task and not queue._worker_task.done():
            queue._worker_task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await queue._worker_task
    _group_queues.clear()


@pytest.mark.asyncio
async def test_private_chat_executes_immediately():
    result_obj = MagicMock()

    async def my_coro():
        return result_obj

    result = await queue_it(lambda: my_coro(), chat=FakeChat(1, "private"))
    assert result is result_obj


@pytest.mark.asyncio
async def test_none_chat_executes_immediately():
    result_obj = MagicMock()

    async def my_coro():
        return result_obj

    result = await queue_it(lambda: my_coro(), chat=None)
    assert result is result_obj


@pytest.mark.asyncio
async def test_group_chat_queues_coroutine():
    result_obj = MagicMock()
    executed = False

    async def my_coro():
        nonlocal executed
        executed = True
        return result_obj

    chat = FakeChat(100, "group")
    result = await queue_it(lambda: my_coro(), chat=chat)
    assert executed is True
    assert result is result_obj


@pytest.mark.asyncio
async def test_identifier_replace_old():
    coro1_executed = False
    coro2_executed = False

    async def coro1():
        nonlocal coro1_executed
        coro1_executed = True
        return "old"

    async def coro2():
        nonlocal coro2_executed
        coro2_executed = True
        return "new"

    chat = FakeChat(300, "group")
    identifier = "test:123"
    queue = _get_queue(chat.id)
    queue._last_sent_at = time.monotonic()

    task1 = asyncio.create_task(
        queue_it(lambda: coro1(), chat=chat, identifier=identifier)
    )
    await asyncio.sleep(0.05)

    task2 = asyncio.create_task(
        queue_it(
            lambda: coro2(),
            chat=chat,
            identifier=identifier,
            replace_old=True,
        )
    )

    result1 = await task1
    result2 = await task2

    assert result1 is None
    assert result2 == "new"
    assert coro1_executed is False
    assert coro2_executed is True


@pytest.mark.asyncio
async def test_rate_limit_interval():
    chat = FakeChat(400, "group")
    results = []

    async def make_coro(value):
        results.append((value, time.monotonic()))
        return value

    task1 = asyncio.create_task(queue_it(lambda: make_coro(1), chat=chat))
    task2 = asyncio.create_task(queue_it(lambda: make_coro(2), chat=chat))

    r1 = await task1
    r2 = await task2

    assert r1 == 1
    assert r2 == 2
    assert len(results) == 2

    spacing = results[1][1] - results[0][1]
    assert spacing >= 3.5, f"Expected >= 3.5s spacing, got {spacing:.2f}s"


@pytest.mark.asyncio
async def test_get_queue_status():
    chat = FakeChat(500, "group")
    gate = asyncio.Event()

    async def blocked():
        await gate.wait()
        return None

    task = asyncio.create_task(queue_it(lambda: blocked(), chat=chat))
    await asyncio.sleep(0.05)

    status = get_queue_status(500)
    assert status["processing"] is True

    gate.set()
    await task


@pytest.mark.asyncio
async def test_cancel_by_identifier():
    queue = ChatQueue(chat_id=600)
    queue._identifier_versions["cancel_me"] = 1

    removed = queue.cancel_by_identifier("cancel_me")
    assert removed is True
    assert queue._identifier_versions["cancel_me"] == 2

    removed = queue.cancel_by_identifier("not_here")
    assert removed is False
