"""Graphics utility functions.

These utilities generate family tree and friend circle images.

# XXX: Functions that need to fetch profile pictures accept (bot, db).
# The bot client is used to download files while db is used to query user data.

# XXX: Profile pictures are stored in the database as:
# - profile_pic_file_id: Telegram file_id for downloading
# - profile_pic_b64: Base64 encoded image data (fallback)
"""

import base64
import io
from pathlib import Path
from typing import Optional, Tuple

from PIL import Image, ImageDraw, ImageFont

# Asset paths
ASSETS_DIR = Path(__file__).parent.parent.parent / "assets"
FONTS_DIR = ASSETS_DIR / "fonts"
DEFAULT_PFP = ASSETS_DIR / "default_pfp.png"

# Colors
WHITE = (255, 255, 255)
BLACK = (0, 0, 0)
LIGHT_BLUE_BG = (173, 206, 220)  # Light blue background like references
RED_LINE = (205, 92, 92)  # Indian red for connections
PINK_LINE = (255, 105, 180)  # Hot Pink for marriage lines
SIBLING_LINE = (60, 60, 60)  # Dark gray/black for sibling lines
BORDER_GRAY = (180, 180, 180)
BORDER_RED = (180, 80, 80)  # Red border for center user

# Default sizes
PROFILE_SIZE = 70
PROFILE_SIZE_LARGE = 100
NODE_PADDING = 4


def get_font(size: int = 16, bold: bool = False) -> ImageFont.FreeTypeFont:
    """Get a font with the specified size."""
    # Primary: Noto Sans (wide Unicode coverage for math symbols, combining marks, etc.)
    font_path = FONTS_DIR / "NotoSans-Regular.ttf"
    if font_path.exists():
        return ImageFont.truetype(str(font_path), size)

    # Fallback font paths for different systems
    fallback_paths = [
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/TTF/DejaVuSansMNerdFont-Regular.ttf",
        "/usr/share/fonts/liberation/LiberationSans-Regular.ttf",
    ]

    for path in fallback_paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue

    # Last resort - use PIL's default with size parameter if possible
    return ImageFont.load_default(size=size)


def get_emoji_font(size: int = 16) -> ImageFont.FreeTypeFont:
    """Get a font that supports emoji/symbol characters."""
    # Noto Sans covers most emoji as monochrome symbols
    emoji_font_paths = [
        str(FONTS_DIR / "NotoSans-Regular.ttf"),
        "/usr/share/fonts/noto/NotoSans-Regular.ttf",
        "/usr/share/fonts/noto/NotoSansSymbols2-Regular.ttf",
        "/usr/share/fonts/TTF/DejaVuSans.ttf",  # Has some symbol coverage
    ]

    for path in emoji_font_paths:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue

    # Fall back to regular font
    return get_font(size)


def square_crop(image: Image.Image, size: int = PROFILE_SIZE) -> Image.Image:
    """Crop image to a square and resize."""
    # Center crop to square
    w, h = image.size
    min_dim = min(w, h)
    left = (w - min_dim) // 2
    top = (h - min_dim) // 2
    image = image.crop((left, top, left + min_dim, top + min_dim))

    # Resize to target size
    image = image.resize((size, size), Image.Resampling.LANCZOS)

    if image.mode != "RGBA":
        image = image.convert("RGBA")

    return image


def add_square_border(
    image: Image.Image, border_width: int = 2, color: Tuple = BORDER_GRAY
) -> Image.Image:
    """Add a square border around an image."""
    size = image.size[0] + border_width * 2
    bordered = Image.new("RGBA", (size, size), color)
    bordered.paste(image, (border_width, border_width))
    return bordered


async def get_profile_image(
    bot, db, user_id: int, size: int = PROFILE_SIZE
) -> Image.Image:
    """
    Get user's profile picture as a square image.

    # XXX: This function accepts (bot, db) separately.
    # It first tries to load from database (base64), then falls back to downloading
    # via Telegram file_id, then uses the default placeholder.

    Args:
        bot: Kurigram client instance for downloading files
        db: Database instance for querying user data
        user_id: Telegram user ID
        size: Target image size

    Returns:
        PIL Image object, square cropped to the specified size
    """
    user = await db.get_user(user_id)

    profile_image = None

    # Try loading from base64 first (faster, no network)
    if user and user.get("profile_pic_b64"):
        try:
            b64_data = user["profile_pic_b64"]
            image_bytes = base64.b64decode(b64_data)
            profile_image = Image.open(io.BytesIO(image_bytes))
        except Exception:
            pass

    # Fall back to downloading via file_id
    if profile_image is None and user and user.get("profile_pic_file_id"):
        try:
            file_data = await bot.download_media(
                user["profile_pic_file_id"], in_memory=True
            )
            if file_data:
                if hasattr(file_data, "getvalue"):
                    image_bytes = file_data.getvalue()
                elif hasattr(file_data, "read"):
                    image_bytes = file_data.read()
                else:
                    image_bytes = file_data
                profile_image = Image.open(io.BytesIO(image_bytes))
        except Exception:
            pass

    # Fall back to default profile picture
    if profile_image is None:
        if DEFAULT_PFP.exists():
            profile_image = Image.open(DEFAULT_PFP)
        else:
            # Create placeholder silhouette
            profile_image = Image.new("RGB", (size, size), (200, 200, 200))
            draw = ImageDraw.Draw(profile_image)
            draw.ellipse(
                (size * 0.3, size * 0.15, size * 0.7, size * 0.45),
                fill=(150, 150, 150),
            )
            draw.ellipse(
                (size * 0.15, size * 0.5, size * 0.85, size * 1.1),
                fill=(150, 150, 150),
            )

    return square_crop(profile_image, size)


def draw_text_centered(
    draw: ImageDraw.Draw,
    text: str,
    center_x: int,
    y: int,
    font: ImageFont.FreeTypeFont,
    fill: Tuple = BLACK,
    max_width: Optional[int] = None,
) -> int:
    """Draw text centered at the specified x coordinate. Returns text height."""
    if max_width:
        while font.getlength(text) > max_width and len(text) > 3:
            text = text[:-4] + "..."

    bbox = draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    x = center_x - text_width // 2
    draw.text((x, y), text, font=font, fill=fill)

    return text_height


def draw_curved_line(
    draw: ImageDraw.Draw,
    start: Tuple[int, int],
    end: Tuple[int, int],
    color: Tuple = RED_LINE,
    width: int = 2,
):
    """Draw a smooth cubic Bezier curve."""
    x1, y1 = start
    x2, y2 = end

    # Control points for a smooth vertical S-curve
    cx1, cy1 = x1, y1 + (y2 - y1) * 0.45
    cx2, cy2 = x2, y2 - (y2 - y1) * 0.45

    steps = 50  # Increased for smoother curves
    points = []
    for i in range(steps + 1):
        t = i / steps
        # Cubic Bezier formula
        px = (
            (1 - t) ** 3 * x1
            + 3 * (1 - t) ** 2 * t * cx1
            + 3 * (1 - t) * t**2 * cx2
            + t**3 * x2
        )
        py = (
            (1 - t) ** 3 * y1
            + 3 * (1 - t) ** 2 * t * cy1
            + 3 * (1 - t) * t**2 * cy2
            + t**3 * y2
        )
        points.append((px, py))

    # Draw the curve as a series of short lines
    draw.line(points, fill=color, width=width, joint="curve")


def draw_dashed_line(
    draw: ImageDraw.Draw,
    start: Tuple[int, int],
    end: Tuple[int, int],
    color: Tuple = PINK_LINE,
    width: int = 2,
    dash_length: int = 10,
    gap_length: int = 5,
):
    """Draw a dashed line."""
    x1, y1 = start
    x2, y2 = end
    dx = x2 - x1
    dy = y2 - y1
    distance = (dx**2 + dy**2) ** 0.5
    if distance == 0:
        return

    dash_gap_len = dash_length + gap_length
    num_dashes = int(distance / dash_gap_len)

    for i in range(num_dashes):
        start_t = i * dash_gap_len / distance
        end_t = (i * dash_gap_len + dash_length) / distance

        start_x = x1 + start_t * dx
        start_y = y1 + start_t * dy
        end_x = x1 + end_t * dx
        end_y = y1 + end_t * dy

        draw.line([(start_x, start_y), (end_x, end_y)], fill=color, width=width)


def draw_marriage_connector(
    draw: ImageDraw.Draw,
    x1: int,
    y1: int,
    x2: int,
    y2: int,
    color: Tuple = PINK_LINE,
    width: int = 2,
):
    """Draw a short horizontal line between spouses."""
    # Simple horizontal line at the middle height
    mid_y = (y1 + y2) // 2
    draw.line([(x1, mid_y), (x2, mid_y)], fill=color, width=width)


# Legacy circular functions (kept for compatibility)
def circular_crop(image: Image.Image, size: int = PROFILE_SIZE) -> Image.Image:
    """Crop image to a circle with the specified diameter."""
    image = image.resize((size, size), Image.Resampling.LANCZOS)
    mask = Image.new("L", (size, size), 0)
    draw = ImageDraw.Draw(mask)
    draw.ellipse((0, 0, size, size), fill=255)
    output = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    output.paste(image, (0, 0))
    output.putalpha(mask)
    return output


def add_border(
    image: Image.Image, border_width: int = 3, color: Tuple = WHITE
) -> Image.Image:
    """Add a circular border around an image."""
    size = image.size[0] + border_width * 2
    bordered = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(bordered)
    draw.ellipse((0, 0, size - 1, size - 1), fill=color, outline=color)
    bordered.paste(image, (border_width, border_width), image)
    return bordered


def create_gradient_background(
    width: int,
    height: int,
    color1: Tuple = WHITE,
    color2: Tuple = (255, 218, 233),
) -> Image.Image:
    """Create a gradient background image."""
    image = Image.new("RGB", (width, height))
    for y in range(height):
        ratio = y / height
        r = int(color1[0] * (1 - ratio) + color2[0] * ratio)
        g = int(color1[1] * (1 - ratio) + color2[1] * ratio)
        b = int(color1[2] * (1 - ratio) + color2[2] * ratio)
        for x in range(width):
            image.putpixel((x, y), (r, g, b))
    return image
