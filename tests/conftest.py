"""Pytest fixtures and configuration."""

import asyncio
import os

import asyncpg
import pytest
import pytest_asyncio

from bot.database import Database

# Test database URL - use a separate test database
TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql://famtree:famtree_dev@localhost:5433/famtree_test",
)


@pytest.fixture(scope="session")
def event_loop():
    """Create an event loop for the test session."""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture
async def db():
    """Create a test database connection."""
    # Create the test database if it doesn't exist
    try:
        conn = await asyncpg.connect(
            TEST_DATABASE_URL.rsplit("/", 1)[0] + "/postgres"
        )
        await conn.execute("CREATE DATABASE famtree_test")
        await conn.close()
    except asyncpg.DuplicateDatabaseError:
        pass
    except Exception:
        pass

    # Create database instance
    database = await Database.create(TEST_DATABASE_URL)

    yield database

    # Clean up after tests
    async with database.connection() as conn:
        await conn.execute("DELETE FROM transactions")
        await conn.execute("DELETE FROM wallets")
        await conn.execute("DELETE FROM friend_ratings")
        await conn.execute("DELETE FROM friend_links")
        await conn.execute("DELETE FROM friend_requests")
        await conn.execute("DELETE FROM friendships")
        await conn.execute("DELETE FROM pending_requests")
        await conn.execute("DELETE FROM marriages")
        await conn.execute("DELETE FROM family_relationships")
        await conn.execute("DELETE FROM feedback_chats")
        await conn.execute("DELETE FROM users")

    await database.close()


@pytest_asyncio.fixture
async def sample_users(db):
    """Create sample users for testing."""
    users = []

    for i in range(1, 6):
        user = await db.upsert_user(
            user_id=1000 + i,
            username=f"testuser{i}",
            first_name=f"Test User {i}",
        )
        users.append(user)

    return users


@pytest_asyncio.fixture
async def sample_family(db, sample_users):
    """Create a sample family structure for testing.

    Structure:
    User1 -- married -- User2
       |
    User3 (child)
       |
    User4 (grandchild)

    User5 is unrelated
    """
    # Create marriage
    await db.add_marriage(
        sample_users[0]["user_id"], sample_users[1]["user_id"]
    )

    # Create parent-child relationships
    await db.add_adoption(
        sample_users[0]["user_id"], sample_users[2]["user_id"]
    )
    await db.add_adoption(
        sample_users[1]["user_id"], sample_users[2]["user_id"]
    )
    await db.add_adoption(
        sample_users[2]["user_id"], sample_users[3]["user_id"]
    )

    return sample_users


@pytest_asyncio.fixture
async def sample_friends(db, sample_users):
    """Create sample friendships for testing."""
    # User1 friends with User2 and User3
    await db.add_friendship(
        sample_users[0]["user_id"], sample_users[1]["user_id"]
    )
    await db.add_friendship(
        sample_users[0]["user_id"], sample_users[2]["user_id"]
    )

    # User2 friends with User3
    await db.add_friendship(
        sample_users[1]["user_id"], sample_users[2]["user_id"]
    )

    return sample_users
