"""Tests for family tree renderer."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from PIL import Image

# Output directory for rendered test images
OUTPUT_DIR = Path(__file__).parent / "output"
OUTPUT_DIR.mkdir(exist_ok=True)


@pytest.mark.asyncio
async def test_tree_builder_single_user(db, sample_users):
    """Test building a tree with a single user (no relations)."""
    from bot.graphics.tree_renderer import FamilyTreeBuilder

    mock_bot = MagicMock()
    mock_bot.db = db

    builder = FamilyTreeBuilder(mock_bot, db, sample_users[0]["user_id"])
    center = await builder.build()

    assert center is not None
    assert center.user_id == sample_users[0]["user_id"]
    assert len(builder.nodes) == 1


@pytest.mark.asyncio
async def test_tree_builder_with_family(db, sample_family):
    """Test building a tree with family relations."""
    from bot.graphics.tree_renderer import FamilyTreeBuilder

    mock_bot = MagicMock()
    mock_bot.db = db

    # Build tree centered on the child (User3)
    builder = FamilyTreeBuilder(mock_bot, db, sample_family[2]["user_id"])
    center = await builder.build()

    assert center is not None
    assert len(builder.nodes) >= 3  # At least child, parent, and grandchild

    # Check parent connections
    assert len(center.parents) == 2  # Both User1 and User2 are parents

    # Check child connections
    assert len(center.children) == 1  # User4 is child


@pytest.mark.asyncio
async def test_tree_layout(db, sample_family):
    """Test tree layout algorithm."""
    from bot.graphics.tree_renderer import FamilyTreeBuilder, layout_tree

    mock_bot = MagicMock()
    mock_bot.db = db

    builder = FamilyTreeBuilder(mock_bot, db, sample_family[2]["user_id"])
    center = await builder.build()

    width, height = layout_tree(center, builder.nodes)

    assert width >= 400  # Minimum width
    assert height >= 300  # Minimum height

    # All nodes should have positions
    for node in builder.nodes.values():
        assert node.x > 0
        assert node.y > 0


@pytest.mark.asyncio
async def test_render_family_tree_image(db, sample_family):
    """Test rendering a family tree to PNG bytes."""
    from bot.graphics.tree_renderer import render_family_tree

    # Mock bot with db attribute
    mock_bot = MagicMock()
    mock_bot.db = db

    image_bytes = await render_family_tree(
        mock_bot, db, sample_family[2]["user_id"]
    )

    assert image_bytes is not None
    assert len(image_bytes) > 0

    # Verify it's a valid PNG
    img = Image.open(Path(OUTPUT_DIR) / "family_tree.png")
    assert img.width >= 400
    assert img.height >= 300


@pytest.mark.asyncio
async def test_render_full_family_tree_image(db, sample_family):
    """Test rendering a full extended family tree to PNG bytes."""
    from bot.graphics.tree_renderer import render_full_family_tree

    mock_bot = MagicMock()
    mock_bot.db = db

    image_bytes = await render_full_family_tree(
        mock_bot, db, sample_family[0]["user_id"]
    )

    assert image_bytes is not None
    assert len(image_bytes) > 0

    # Verify it's a valid PNG
    img = Image.open(Path(OUTPUT_DIR) / "full_family_tree.png")
    assert img.width >= 400
    assert img.height >= 300


@pytest.mark.asyncio
async def test_circular_crop():
    """Test circular crop utility."""
    from bot.graphics.utils import circular_crop

    # Create a test image
    img = Image.new("RGB", (200, 200), (255, 0, 0))

    cropped = circular_crop(img, 100)

    assert cropped.size == (100, 100)
    assert cropped.mode == "RGBA"

    # Center pixel should be red (opaque)
    center = cropped.getpixel((50, 50))
    assert center[0] == 255  # Red
    assert center[3] == 255  # Opaque

    # Corner pixel should be transparent
    corner = cropped.getpixel((0, 0))
    assert corner[3] == 0  # Transparent


@pytest.mark.asyncio
async def test_add_border():
    """Test adding border to image."""
    from bot.graphics.utils import add_border, circular_crop

    # Create a test circular image
    img = Image.new("RGB", (100, 100), (255, 0, 0))
    circular = circular_crop(img, 80)

    bordered = add_border(circular, border_width=4, color=(255, 255, 255))

    # Should be larger by border width * 2
    assert bordered.size == (88, 88)


@pytest.mark.asyncio
async def test_gradient_background():
    """Test gradient background creation."""
    from bot.graphics.utils import create_gradient_background

    bg = create_gradient_background(400, 300, (255, 255, 255), (255, 200, 200))

    assert bg.size == (400, 300)

    # Top should be closer to white
    top_pixel = bg.getpixel((200, 0))
    assert top_pixel[0] == 255
    assert top_pixel[1] == 255
    assert top_pixel[2] == 255

    # Bottom should be closer to pink
    bottom_pixel = bg.getpixel((200, 299))
    assert bottom_pixel[0] == 255
    assert bottom_pixel[1] == 200
    assert bottom_pixel[2] == 200


@pytest.mark.asyncio
async def test_text_centering():
    """Test centered text drawing."""
    from PIL import Image, ImageDraw

    from bot.graphics.utils import draw_text_centered, get_font

    img = Image.new("RGB", (200, 100), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    font = get_font(14)

    height = draw_text_centered(draw, "Test", 100, 40, font, (0, 0, 0))

    assert height > 0
