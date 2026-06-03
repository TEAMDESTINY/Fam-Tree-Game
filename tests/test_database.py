"""Tests for database operations."""

import pytest


@pytest.mark.asyncio
async def test_upsert_user(db):
    """Test creating and updating users."""
    # Create user
    user = await db.upsert_user(
        user_id=12345,
        username="testuser",
        first_name="Test",
    )

    assert user["user_id"] == 12345
    assert user["username"] == "testuser"
    assert user["first_name"] == "Test"

    # Update user
    updated = await db.upsert_user(
        user_id=12345,
        first_name="Updated Test",
    )

    assert updated["first_name"] == "Updated Test"
    assert updated["username"] == "testuser"  # Should preserve


@pytest.mark.asyncio
async def test_adoption(db, sample_users):
    """Test parent-child relationship operations."""
    parent = sample_users[0]
    child = sample_users[1]

    # Add adoption
    await db.add_adoption(parent["user_id"], child["user_id"])

    # Verify relationship
    children = await db.get_children(parent["user_id"])
    assert len(children) == 1
    assert children[0]["user_id"] == child["user_id"]

    parents = await db.get_parents(child["user_id"])
    assert len(parents) == 1
    assert parents[0]["user_id"] == parent["user_id"]

    # Remove adoption
    await db.remove_adoption(parent["user_id"], child["user_id"])

    children = await db.get_children(parent["user_id"])
    assert len(children) == 0


@pytest.mark.asyncio
async def test_fetchrow_insert_persists_after_connection_close(db):
    """Raw INSERT ... RETURNING via fetchrow should be committed."""
    inserted = await db.fetchrow(
        """
        INSERT INTO users (user_id, username, first_name)
        VALUES ($1, $2, $3)
        RETURNING user_id, username, first_name
        """,
        99887766,
        "fetchrow_user",
        "Fetchrow User",
    )
    assert inserted["user_id"] == 99887766

    found = await db.get_user(99887766)
    assert found is not None
    assert found["username"] == "fetchrow_user"


@pytest.mark.asyncio
async def test_marriage(db, sample_users):
    """Test marriage operations."""
    user1 = sample_users[0]
    user2 = sample_users[1]

    # Add marriage
    await db.add_marriage(user1["user_id"], user2["user_id"])

    # Verify marriage
    assert await db.are_married(user1["user_id"], user2["user_id"])
    assert await db.are_married(
        user2["user_id"], user1["user_id"]
    )  # Order shouldn't matter

    spouses = await db.get_spouses(user1["user_id"])
    assert len(spouses) == 1
    assert spouses[0]["user_id"] == user2["user_id"]

    # Remove marriage
    await db.remove_marriage(user1["user_id"], user2["user_id"])
    assert not await db.are_married(user1["user_id"], user2["user_id"])


@pytest.mark.asyncio
async def test_is_ancestor(db, sample_family):
    """Test ancestor detection."""
    grandparent = sample_family[0]
    parent = sample_family[2]
    grandchild = sample_family[3]
    unrelated = sample_family[4]

    # Grandparent is ancestor of grandchild
    assert await db.is_ancestor(grandparent["user_id"], grandchild["user_id"])

    # Parent is ancestor of grandchild
    assert await db.is_ancestor(parent["user_id"], grandchild["user_id"])

    # Grandchild is NOT ancestor of grandparent
    assert not await db.is_ancestor(
        grandchild["user_id"], grandparent["user_id"]
    )

    # Unrelated user is not ancestor
    assert not await db.is_ancestor(unrelated["user_id"], grandchild["user_id"])


@pytest.mark.asyncio
async def test_siblings(db, sample_users):
    """Test sibling detection."""
    parent = sample_users[0]
    child1 = sample_users[1]
    child2 = sample_users[2]
    unrelated = sample_users[3]

    # Create siblings (same parent)
    await db.add_adoption(parent["user_id"], child1["user_id"])
    await db.add_adoption(parent["user_id"], child2["user_id"])

    # They should be siblings
    assert await db.are_siblings(child1["user_id"], child2["user_id"])

    # Unrelated is not sibling
    assert not await db.are_siblings(child1["user_id"], unrelated["user_id"])


@pytest.mark.asyncio
async def test_friendship(db, sample_users):
    """Test friendship operations."""
    user1 = sample_users[0]
    user2 = sample_users[1]

    # Add friendship
    await db.add_friendship(user1["user_id"], user2["user_id"])

    # Verify friendship
    assert await db.are_friends(user1["user_id"], user2["user_id"])
    assert await db.are_friends(
        user2["user_id"], user1["user_id"]
    )  # Order shouldn't matter

    friends = await db.get_friends(user1["user_id"])
    assert len(friends) == 1
    assert friends[0]["user_id"] == user2["user_id"]

    # Remove friendship
    await db.remove_friendship(user1["user_id"], user2["user_id"])
    assert not await db.are_friends(user1["user_id"], user2["user_id"])


@pytest.mark.asyncio
async def test_friend_suggestions(db, sample_friends):
    """Test friend suggestions (friends of friends)."""
    user1 = sample_friends[0]
    user4 = sample_friends[3]  # Unrelated

    # User4 is not a friend of User1, but User1's friends know each other
    # So User4 should potentially show up as a suggestion if connected

    # Add a connection: User3 is friends with User4
    await db.add_friendship(sample_friends[2]["user_id"], user4["user_id"])

    suggestions = await db.get_friend_suggestions(user1["user_id"])

    # User4 should be suggested (friend of User3, who is friend of User1)
    suggestion_ids = [s["user_id"] for s in suggestions]
    assert user4["user_id"] in suggestion_ids


@pytest.mark.asyncio
async def test_wallet(db, sample_users):
    """Test wallet operations."""
    user = sample_users[0]

    # Get initial wallet
    wallet = await db.get_wallet(user["user_id"])
    assert wallet["balance"] == 0

    # Add balance
    await db.add_balance(user["user_id"], 1000, "Test reward")

    wallet = await db.get_wallet(user["user_id"])
    assert wallet["balance"] == 1000
    assert wallet["total_earned"] == 1000

    # Subtract balance
    await db.add_balance(user["user_id"], -500, "Test deduction")

    wallet = await db.get_wallet(user["user_id"])
    assert wallet["balance"] == 500
    assert wallet["total_earned"] == 1000  # Should not decrease

    # Check transactions
    txns = await db.get_transactions(user["user_id"])
    assert len(txns) == 2


@pytest.mark.asyncio
async def test_friend_rating(db, sample_friends):
    """Test friend rating operations."""
    user1 = sample_friends[0]
    user2 = sample_friends[1]

    # Set rating
    await db.set_friend_rating(user1["user_id"], user2["user_id"], 5)

    # Get ratings given
    ratings = await db.get_ratings_given(user1["user_id"])
    assert len(ratings) == 1
    assert ratings[0]["rating"] == 5

    # Get average rating
    avg = await db.get_average_rating(user2["user_id"])
    assert avg == 5.0

    # Update rating
    await db.set_friend_rating(user1["user_id"], user2["user_id"], 3)

    ratings = await db.get_ratings_given(user1["user_id"])
    assert ratings[0]["rating"] == 3


@pytest.mark.asyncio
async def test_friend_link(db, sample_users):
    """Test friend link operations."""
    user = sample_users[0]

    # Create link
    link1 = await db.get_or_create_friend_link(user["user_id"])
    assert link1 is not None
    assert len(link1) > 10

    # Get same link again
    link2 = await db.get_or_create_friend_link(user["user_id"])
    assert link1 == link2

    # Find user by link
    found = await db.get_user_by_friend_link(link1)
    assert found is not None
    assert found["user_id"] == user["user_id"]


@pytest.mark.asyncio
async def test_pending_request(db, sample_users):
    """Test pending request operations."""
    user1 = sample_users[0]
    user2 = sample_users[1]

    # Create request
    request = await db.create_pending_request(
        request_type="marry",
        requester_id=user1["user_id"],
        target_id=user2["user_id"],
        chat_id=123456,
    )

    assert request is not None
    assert request["request_type"] == "marry"

    # Get request
    found = await db.get_pending_request(
        "marry", user1["user_id"], user2["user_id"]
    )
    assert found is not None

    # Get by ID
    found_by_id = await db.get_pending_request_by_id(request["id"])
    assert found_by_id is not None

    # Delete request
    await db.delete_pending_request(request["id"])

    found = await db.get_pending_request(
        "marry", user1["user_id"], user2["user_id"]
    )
    assert found is None


@pytest.mark.asyncio
async def test_close_family(db, sample_family):
    """Test getting close family members."""
    parent = sample_family[0]

    family = await db.get_close_family(parent["user_id"])

    assert len(family["spouses"]) == 1
    assert len(family["children"]) == 1
    assert len(family["parents"]) == 0
    assert len(family["siblings"]) == 0


@pytest.mark.asyncio
async def test_marriage_quotes(db):
    """Test marriage quote retrieval."""
    # Regular quote
    quote = await db.get_random_marriage_quote(is_remarriage=False)
    assert quote is not None
    assert len(quote) > 0

    # Remarriage quote
    remarriage_quote = await db.get_random_marriage_quote(is_remarriage=True)
    assert remarriage_quote is not None


@pytest.mark.asyncio
async def test_is_descendant(db, sample_family):
    """Test descendant detection (child, grandchild, etc.)."""
    grandparent = sample_family[0]
    parent = sample_family[2]
    grandchild = sample_family[3]
    unrelated = sample_family[4]

    # Grandchild is descendant of grandparent
    assert await db.is_descendant(grandchild["user_id"], grandparent["user_id"])

    # Child is descendant of parent
    assert await db.is_descendant(parent["user_id"], grandparent["user_id"])

    # Grandparent is NOT descendant of grandchild
    assert not await db.is_descendant(
        grandparent["user_id"], grandchild["user_id"]
    )

    # Unrelated user is not descendant
    assert not await db.is_descendant(
        unrelated["user_id"], grandparent["user_id"]
    )


@pytest.mark.asyncio
async def test_get_sibling_path(db, sample_users):
    """Test sibling path finding."""
    parent = sample_users[0]
    child1 = sample_users[1]
    child2 = sample_users[2]
    unrelated = sample_users[3]

    # Create siblings via shared parent
    await db.add_adoption(parent["user_id"], child1["user_id"])
    await db.add_adoption(parent["user_id"], child2["user_id"])

    # Path should exist
    path = await db.get_sibling_path(child1["user_id"], child2["user_id"])
    assert path is not None
    assert child1["user_id"] in path
    assert child2["user_id"] in path

    # No path to unrelated user
    path = await db.get_sibling_path(child1["user_id"], unrelated["user_id"])
    assert path is None


@pytest.mark.asyncio
async def test_get_sibling_path_direct(db, sample_users):
    """Test sibling path via direct sibling relationship."""
    user1 = sample_users[0]
    user2 = sample_users[1]
    unrelated = sample_users[3]

    # Direct sibling
    await db.add_sibling(user1["user_id"], user2["user_id"])

    path = await db.get_sibling_path(user1["user_id"], user2["user_id"])
    assert path is not None
    assert user1["user_id"] in path
    assert user2["user_id"] in path

    # No path to unrelated
    path = await db.get_sibling_path(user1["user_id"], unrelated["user_id"])
    assert path is None


@pytest.mark.asyncio
async def test_are_close_family(db, sample_users):
    """Test close family detection via combined family graph."""
    user1 = sample_users[0]
    user2 = sample_users[1]
    user3 = sample_users[2]
    user4 = sample_users[3]

    # Create: user1 married to user2, user1 adopted user3
    await db.add_marriage(user1["user_id"], user2["user_id"])
    await db.add_adoption(user1["user_id"], user3["user_id"])

    # user2 and user3 are connected via family (spouse's child)
    assert await db.are_close_family(user2["user_id"], user3["user_id"])

    # user4 is not connected
    assert not await db.are_close_family(user1["user_id"], user4["user_id"])


@pytest.mark.asyncio
async def test_get_family_path(db, sample_users):
    """Test family path finding with edge labels."""
    user1 = sample_users[0]
    user2 = sample_users[1]
    user3 = sample_users[2]

    # Create: user1 married to user2, user1 adopted user3
    await db.add_marriage(user1["user_id"], user2["user_id"])
    await db.add_adoption(user1["user_id"], user3["user_id"])

    # Path from user2 to user3 should exist
    path = await db.get_family_path(user2["user_id"], user3["user_id"])
    assert path is not None
    assert len(path) >= 3  # at least user2 -> user1 -> user3

    # Check edge labels exist
    user_ids = [uid for uid, _ in path]
    assert user2["user_id"] in user_ids
    assert user3["user_id"] in user_ids


@pytest.mark.asyncio
async def test_transitive_siblings(db, sample_users):
    """Test that transitive siblings are detected via BFS."""
    user1 = sample_users[0]
    user2 = sample_users[1]
    user3 = sample_users[2]
    user4 = sample_users[3]

    # Create chain: user1 <-> user2 <-> user3
    await db.add_sibling(user1["user_id"], user2["user_id"])
    await db.add_sibling(user2["user_id"], user3["user_id"])

    # user1 and user3 are transitive siblings
    assert await db.are_siblings(user1["user_id"], user3["user_id"])

    # user4 is not connected
    assert not await db.are_siblings(user1["user_id"], user4["user_id"])
