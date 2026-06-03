"""Tests for friend circle renderer."""

import math
from unittest.mock import MagicMock

import pytest


@pytest.mark.asyncio
async def test_circle_positions():
    """Test that friends are positioned in a circle."""
    num_friends = 8
    center_x = 300
    center_y = 300
    radius = 150

    positions = []
    angle_step = 2 * math.pi / num_friends

    for i in range(num_friends):
        angle = i * angle_step - math.pi / 2  # Start from top
        fx = center_x + int(radius * math.cos(angle))
        fy = center_y + int(radius * math.sin(angle))
        positions.append((fx, fy))

    # First friend should be at top (above center)
    assert positions[0][0] == center_x
    assert positions[0][1] < center_y

    # All positions should be roughly on the circle
    for fx, fy in positions:
        dist = math.sqrt((fx - center_x) ** 2 + (fy - center_y) ** 2)
        assert abs(dist - radius) < 1  # Allow for rounding


@pytest.mark.asyncio
async def test_circle_no_friends(db, sample_users):
    """Test circle rendering with no friends."""
    from bot.graphics.circle_renderer import render_friend_circle

    client = MagicMock()
    client.db = db

    # User with no friends
    result = await render_friend_circle(client, sample_users[4]["user_id"])

    assert result is None


@pytest.mark.asyncio
async def test_circle_with_friends(db, sample_friends):
    """Test circle rendering with friends."""
    from bot.graphics.circle_renderer import render_friend_circle

    client = MagicMock()
    client.db = db

    # Mock get_profile_image to avoid needing actual images
    async def mock_get_profile_image(c, user_id, size):
        from PIL import Image

        return Image.new("RGBA", (size, size), (200, 200, 200, 255))

    import bot.graphics.circle_renderer as cr

    original_get_profile = cr.get_profile_image
    cr.get_profile_image = mock_get_profile_image

    try:
        # User1 has friends (User2 and User3)
        result = await render_friend_circle(
            client, sample_friends[0]["user_id"]
        )

        assert result is not None
        assert len(result) > 0  # Should have image data

        # Verify it's valid PNG data
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(result))
        assert img.format == "PNG"
        assert img.size[0] >= 400  # Minimum canvas size
    finally:
        cr.get_profile_image = original_get_profile
