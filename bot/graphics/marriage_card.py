"""Marriage card generator.

# XXX: This module generates wedding announcement cards using a pre-made
# background asset (assets/marriage_card.png). The card includes:
# - Profile pictures of both partners in decorative frames
# - Names with gradient gold text
# - Date and time in UTC format

# XXX: Aiogram Migration Notes
# - Function signature changed from (client, u1, u2, quote) to (bot, db, u1, u2, quote)
# - Uses image data from database instead of file paths
# - Date format: "MONTH DD\nHH:MM AM/PM UTC"
"""

import datetime
import io
import unicodedata
from typing import Optional

from PIL import Image, ImageDraw, ImageFont

from bot.graphics.utils import get_profile_image

# --- Configuration for the Provided Asset ---
ASSET_PATH = "assets/marriage_card.png"
FONT_NAME_PATH = "assets/fonts/CinzelDecorative-Bold.ttf"
FONT_DATE_PATH = "assets/fonts/Montserrat[wght].ttf"

# Colors for gradient text
GOLD_TOP = (88, 56, 18)
GOLD_BOTTOM = (184, 134, 11)
TIME_COLOR = (255, 197, 0)  # #FFC500


# -----------------------------
# Normalization function
#
# useful because Cinzel will not render fancy unicodes so this way we can fix it(else it will render space for unknown unicode)
# -----------------------------
FANCY_MAP = {
    "ℽ": "Y",
    "ℊ": "g",
    "ℋ": "H",
    "ℌ": "H",
    "ℎ": "h",
    "ℏ": "h",
    "ℑ": "I",
    "ℓ": "l",
    "ℕ": "N",
    "ℙ": "P",
    "ℚ": "Q",
    "ℝ": "R",
    "ℤ": "Z",
    "ℭ": "C",
    "ℯ": "e",
}


def normalize_text(text: str, upper_case: bool = False) -> str:
    """Convert fancy Unicode letters (math symbols, combining marks) to plain ASCII."""
    # Step 1: convert fancy letters → ASCII
    result = []
    for c in text:
        if c in FANCY_MAP:
            result.append(FANCY_MAP[c])
            continue
        try:
            name = unicodedata.name(c)
            if "MATHEMATICAL" in name:
                plain_char = name.split()[-1]
                result.append(plain_char)
            else:
                result.append(c)
        except ValueError:
            result.append(c)

    normalized = "".join(result)

    # Step 2: remove combining marks (diacritics, superscripts)
    normalized = "".join(
        c
        for c in unicodedata.normalize("NFD", normalized)
        if not unicodedata.combining(c)
    )

    # Step 3: optional uppercase
    if upper_case:
        normalized = normalized.upper()

    return normalized


def _draw_gradient_text(
    text: str,
    font: ImageFont.FreeTypeFont,
    x: int,
    y: int,
    w: int,
    h: int,
    top_color: tuple,
    bottom_color: tuple,
) -> tuple[Image.Image, Image.Image, tuple[int, int]]:
    """Draw text with vertical gradient color."""
    mask = Image.new("L", (w, h), 0)
    mask_draw = ImageDraw.Draw(mask)

    bbox = mask_draw.textbbox((0, 0), text, font=font)
    text_width = bbox[2] - bbox[0]
    text_height = bbox[3] - bbox[1]

    text_x = (w - text_width) // 2
    text_y = (h - text_height) // 2 - 4

    mask_draw.text((text_x, text_y), text, font=font, fill=255)

    gradient = Image.new("RGB", (w, h))
    g_draw = ImageDraw.Draw(gradient)
    for i in range(h):
        r = int(top_color[0] + (bottom_color[0] - top_color[0]) * (i / h))
        g = int(top_color[1] + (bottom_color[1] - top_color[1]) * (i / h))
        b = int(top_color[2] + (bottom_color[2] - top_color[2]) * (i / h))
        g_draw.line([(0, i), (w, i)], fill=(r, g, b))

    return gradient, mask, (x, y)


async def render_marriage_card(
    bot, db, user1_id: int, user2_id: int, quote: str = None
) -> Optional[bytes]:
    """
    Render a wedding invitation using the provided background asset.

    Args:
        bot: Kurigram client instance
        db: Database instance
        user1_id: First partner's Telegram user ID
        user2_id: Second partner's Telegram user ID
        quote: Optional marriage quote (not currently displayed on card)

    Returns:
        PNG image bytes or None if failed
    """
    try:
        # 1. Load Background
        try:
            bg = Image.open(ASSET_PATH).convert("RGBA")
        except FileNotFoundError:
            # Fallback if file missing
            bg = Image.new("RGBA", (1024, 800), (45, 30, 60))

        # 2. Get user data
        user1 = await db.get_user(user1_id)
        user2 = await db.get_user(user2_id)
        # TODO: Investigate Pillow/font rendering for emoji-only names (e.g. "✨").
        name1 = normalize_text(user1["first_name"] if user1 else "Partner 1")
        name2 = normalize_text(user2["first_name"] if user2 else "Partner 2")

        # 3. Load profile pictures and paste onto card
        # PFP positions from marry_cover_maker.py
        pfps = [
            {"pos": (238, 141), "size": (250, 228)},
            {"pos": (548, 141), "size": (246, 228)},
        ]

        # Get profile images
        img1 = await get_profile_image(
            bot, db, user1_id, size=max(pfps[0]["size"])
        )
        img2 = await get_profile_image(
            bot, db, user2_id, size=max(pfps[1]["size"])
        )

        # Resize and paste
        img1 = img1.resize(pfps[0]["size"], Image.Resampling.LANCZOS)
        img2 = img2.resize(pfps[1]["size"], Image.Resampling.LANCZOS)

        # Make images RGBA for pasting
        if img1.mode != "RGBA":
            img1 = img1.convert("RGBA")
        if img2.mode != "RGBA":
            img2 = img2.convert("RGBA")

        bg.paste(img1, pfps[0]["pos"], img1)
        bg.paste(img2, pfps[1]["pos"], img2)

        # 4. Load fonts
        font_name = ImageFont.truetype(FONT_NAME_PATH, 32)
        font_date = ImageFont.truetype(FONT_DATE_PATH, 20)

        # 5. Draw names with gradient
        names = [
            {"text": name1, "x": 288, "y": 442, "w": 454, "h": 50},
            {"text": name2, "x": 316, "y": 600, "w": 398, "h": 50},
        ]

        for n in names:
            grad_img, mask, pos = _draw_gradient_text(
                n["text"],
                font_name,
                n["x"],
                n["y"],
                n["w"],
                n["h"],
                GOLD_TOP,
                GOLD_BOTTOM,
            )
            bg.paste(grad_img, pos, mask)

        # 6. Draw date/time in UTC format: "MONTH DD\nHH:MM AM/PM UTC"
        now = datetime.datetime.utcnow()
        time_str = f"{now.strftime('%B').upper()} {now.day}\n{now.strftime('%I:%M %p')} UTC"

        d = ImageDraw.Draw(bg)
        d.text(
            (610, 684),
            time_str,
            font=font_date,
            fill=TIME_COLOR,
            spacing=4,
            stroke_width=1,
            stroke_fill=TIME_COLOR,
        )

        # 7. Output
        buffer = io.BytesIO()
        bg.save(buffer, format="PNG")
        buffer.seek(0)
        return buffer.getvalue()

    except Exception as e:
        import logging

        logging.getLogger(__name__).error(
            f"Failed to render marriage card: {e}"
        )
        return None
