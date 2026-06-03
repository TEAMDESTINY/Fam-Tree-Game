import asyncio
from types import SimpleNamespace

import pytest

from bot.plugins.profile import me_profile


class DummyMessage:
    def __init__(self, user):
        self.from_user = user
        self._answers = []

    async def answer(self, text):
        # capture the last answer and return it
        self._answers.append(text)
        return SimpleNamespace(text=text)


class DummyDB:
    def __init__(
        self,
        user_row=None,
        wallet=None,
        factories=None,
        garden=None,
        inventory=None,
        machines=None,
        achievements=None,
        job=None,
        friends_count=0,
    ):
        self._user = user_row or {}
        self._wallet = wallet or {"balance": 0, "total_earned": 0}
        self._factories = factories or []
        self._garden = garden
        self._inventory = inventory or []
        self._machines = machines or []
        self._achievements = achievements or []
        self._job = job
        self._friends_count = friends_count

    async def upsert_user(self, user_id, username, first_name):
        return None

    async def get_wallet(self, user_id):
        return self._wallet

    async def get_user(self, user_id):
        return self._user

    async def get_inventory(self, user_id):
        return self._inventory

    async def get_user_machines(self, user_id):
        return self._machines

    async def get_friends(self, user_id):
        return [{"user_id": idx} for idx in range(self._friends_count)]

    async def get_factory(self, user_id):
        return self._factories[0] if self._factories else None

    async def get_garden(self, user_id):
        return self._garden

    async def get_achievements(self, user_id):
        return self._achievements

    async def get_children(self, user_id):
        return []

    async def get_job(self, user_id):
        return self._job


@pytest.mark.asyncio
async def test_me_profile_shows_job_and_factories():
    user = SimpleNamespace(id=123, username="tester", first_name="Test")
    msg = DummyMessage(user)

    job = {"user_id": 123, "job_type": "miner", "job_level": 2, "job_xp": 150}
    factories = [
        {
            "id": 1,
            "owner_id": 123,
            "name": "Alpha",
            "capacity": 4,
            "total_earnings": 12345,
        }
    ]
    garden = {"id": 1, "owner_id": 123, "size": 5}
    inventory = [
        {"item_type": "seed", "quantity": 10},
        {"item_type": "harvest", "quantity": 3},
    ]
    machines = [{"id": 1}]
    achievements = [{"user_id": 123, "achievement_key": "starter"}]

    db = DummyDB(
        user_row={"gender": "male"},
        wallet={"balance": 500, "total_earned": 1000},
        factories=factories,
        garden=garden,
        inventory=inventory,
        machines=machines,
        achievements=achievements,
        job=job,
        friends_count=2,
    )

    await me_profile(msg, db)

    # Assert that an answer was posted and includes job and factory info
    assert msg._answers, "No reply was sent"
    out = msg._answers[-1]
    assert "Job: miner" in out
    assert "Alpha" in out
    assert "Factories" in out


if __name__ == "__main__":
    asyncio.run(test_me_profile_shows_job_and_factories())
