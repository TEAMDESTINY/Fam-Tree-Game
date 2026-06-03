"""
Shared SVG utilities for creating composite node images.

# XXX: This module provides utilities for:
# XXX: 1. Creating SVG nodes with photo + name label (or initials fallback)
# XXX: 2. Fetching and converting user profile images to base64
# XXX: 3. Exporting PyVis networks to PNG using Playwright
# XXX: 4. Auto-cropping to remove empty background areas
"""

import base64
import hashlib
import io
from typing import Optional

from PIL import Image

# XXX: Material Design color palette for fallback avatars
# XXX: When a user has no profile picture, we show colored initials
# XXX: The color is deterministically chosen based on their name hash
MATERIAL_COLORS = [
    "#F44336",
    "#E91E63",
    "#9C27B0",
    "#673AB7",
    "#3F51B5",
    "#2196F3",
    "#03A9F4",
    "#00BCD4",
    "#009688",
    "#4CAF50",
    "#8BC34A",
    "#FF9800",
    "#FF5722",
    "#795548",
    "#607D8B",
]


def get_deterministic_color(name: str) -> str:
    """
    Generates a consistent nice hex color based on the name string.

    # XXX: Uses MD5 hash to ensure same name always gets same color.
    # XXX: Maps hash to one of the Material Design colors.
    """
    hash_object = hashlib.md5(name.encode())
    index = int(hash_object.hexdigest(), 16) % len(MATERIAL_COLORS)
    return MATERIAL_COLORS[index]


def get_initials(name: str) -> str:
    """
    Extracts up to 2 initials from a name.

    # XXX: "John Doe" -> "JD"
    # XXX: "John" -> "JO" (first 2 chars)
    # XXX: "" -> "??"
    """
    parts = name.strip().split()
    if not parts:
        return "??"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def image_to_base64(image: Image.Image) -> str:
    """
    Convert PIL Image to base64 string.

    # XXX: Saves as PNG format for lossless quality.
    """
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return base64.b64encode(buffer.read()).decode("utf-8")


def create_composite_svg(
    name: str,
    image_b64: Optional[str] = None,
    is_center: bool = False,
    img_size: int = 120,
    text_height: int = 40,
    font_size: int = 16,
) -> str:
    """
    Creates an SVG node with image (or colored initials fallback) and name label.

    # XXX: Structure of the generated SVG:
    # XXX: ┌─────────────────────┐
    # XXX: │  [Red border if     │  ← is_center adds red border
    # XXX: │   is_center]        │
    # XXX: │  ┌───────────────┐  │
    # XXX: │  │               │  │
    # XXX: │  │    Photo      │  │  ← Or colored initials fallback
    # XXX: │  │               │  │
    # XXX: │  └───────────────┘  │
    # XXX: │  ┌───────────────┐  │
    # XXX: │  │    Name       │  │  ← Light blue label bar
    # XXX: │  └───────────────┘  │
    # XXX: └─────────────────────┘

    Args:
        name: Display name for the node
        image_b64: Base64 encoded image data (optional)
        is_center: Whether this is the center/hub node (adds red border)
        img_size: Size of the image area in pixels
        text_height: Height of the text label area
        font_size: Font size for the name label

    Returns:
        Data URI string for the SVG image (data:image/svg+xml;base64,...)
    """
    # XXX: Add padding for center node to make room for border
    padding = 15 if is_center else 0
    total_width = img_size + (padding * 2)
    total_height = img_size + text_height + (padding * 2)

    # XXX: Build content (either real image or initials fallback)
    if image_b64:
        # XXX: Use clipPath for rounded corners on the image
        # XXX: Sanitize name to create valid SVG ID
        safe_name = "".join(c if c.isalnum() else "_" for c in name)
        content_element = f"""
            <defs>
                <clipPath id="clip-{safe_name}">
                    <rect x="{padding}" y="{padding}" width="{img_size}" height="{img_size}" rx="10" ry="10" />
                </clipPath>
            </defs>
            <image href="data:image/png;base64,{image_b64}"
                   x="{padding}" y="{padding}" width="{img_size}" height="{img_size}"
                   clip-path="url(#clip-{safe_name})" preserveAspectRatio="xMidYMid slice" />
        """
    else:
        # XXX: Fallback: colored square with initials
        color = get_deterministic_color(name)
        initials = get_initials(name)
        center_x = padding + (img_size / 2)
        center_y = padding + (img_size / 2)
        initials_font_size = int(img_size * 0.4)

        content_element = f"""
            <rect x="{padding}" y="{padding}" width="{img_size}" height="{img_size}"
                  rx="10" ry="10" fill="{color}" />
            <text x="{center_x}" y="{center_y + initials_font_size * 0.3}"
                  font-family="Arial, sans-serif" font-size="{initials_font_size}" font-weight="bold"
                  fill="white" text-anchor="middle" dominant-baseline="middle">
                  {initials}
            </text>
        """

    # XXX: Build border (red rectangle for center/hub nodes)
    border_rect = ""
    if is_center:
        border_rect = f"""
        <rect x="0" y="0" width="{total_width}" height="{total_height - text_height + 5}"
              rx="15" ry="15" fill="white" stroke="red" stroke-width="5" />
        """

    # XXX: Build bottom label bar (light cyan background)
    text_y_start = img_size + (padding * 2)
    # XXX: Escape special XML characters in name to prevent SVG injection
    escaped_name = (
        name.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    )

    text_element = f"""
        <rect x="0" y="{text_y_start}" width="{total_width}" height="{text_height}"
              rx="5" ry="5" fill="#e0f7fa" fill-opacity="1.0" />
        <text x="{total_width / 2}" y="{text_y_start + text_height * 0.6}"
              font-family="Arial, sans-serif" font-size="{font_size}" font-weight="bold"
              fill="black" text-anchor="middle">{escaped_name}</text>
    """

    # XXX: Assemble final SVG
    svg = f"""
    <svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" height="{total_height}">
        {border_rect}
        {content_element}
        {text_element}
    </svg>
    """

    # XXX: Return as data URI for embedding in PyVis nodes
    return f"data:image/svg+xml;base64,{base64.b64encode(svg.encode('utf-8')).decode('utf-8')}"


async def get_user_image_b64(
    client, user_id: int, size: int = 120
) -> Optional[str]:
    """
    Fetch user's profile image and convert to base64.

    # XXX: Priority order:
    # XXX: 1. Stored base64 in database (profile_pic_b64) - fastest, no download
    # XXX: 2. Download from Telegram using file_id - slower, requires API call
    # XXX: 3. Return None - caller will use initials fallback

    Args:
        client: Bot client with db and download_media methods
        user_id: User ID to fetch image for
        size: Size to resize image to

    Returns:
        Base64 encoded image string or None if not available
    """
    try:
        user = await client.db.get_user(user_id)
        if not user:
            return None

        # XXX: First try stored base64 (faster, no download needed)
        if user.get("profile_pic_b64"):
            try:
                # XXX: Decode, square-crop, and resize the stored image
                img_data = base64.b64decode(user["profile_pic_b64"])
                image = Image.open(io.BytesIO(img_data)).convert("RGBA")

                # XXX: Square crop from center
                w, h = image.size
                min_dim = min(w, h)
                left = (w - min_dim) // 2
                top = (h - min_dim) // 2
                image = image.crop((left, top, left + min_dim, top + min_dim))
                image = image.resize((size, size), Image.Resampling.LANCZOS)

                return image_to_base64(image)
            except Exception:
                pass  # XXX: Fall through to file_id method

        # XXX: Fall back to downloading from Telegram
        if not user.get("profile_pic_file_id"):
            return None

        media = await client.download_media(
            user["profile_pic_file_id"], in_memory=True
        )
        if not media:
            return None

        # XXX: Process the downloaded image
        image = Image.open(media).convert("RGBA")

        # XXX: Square crop from center
        w, h = image.size
        min_dim = min(w, h)
        left = (w - min_dim) // 2
        top = (h - min_dim) // 2
        image = image.crop((left, top, left + min_dim, top + min_dim))
        image = image.resize((size, size), Image.Resampling.LANCZOS)

        return image_to_base64(image)
    except Exception:
        return None


async def export_network_to_png(
    net,
    width: int = 1200,
    height: int = 1200,
    wait_time: int = 3000,
    auto_crop: bool = True,
    padding: int = 40,
) -> Optional[bytes]:
    """
    Export PyVis network to PNG using Playwright.

    # XXX: PyVis generates HTML with embedded JavaScript.
    # XXX: We use Playwright (headless Chromium) to render it and screenshot.
    # XXX: Steps:
    # XXX: 1. Write PyVis HTML to temp file
    # XXX: 2. Open in headless browser
    # XXX: 3. Wait for physics simulation to settle
    # XXX: 4. Screenshot the <canvas> element (avoids white borders)
    # XXX: 5. Auto-crop empty background areas

    Args:
        net: PyVis Network object
        width: Viewport width
        height: Viewport height
        wait_time: Time to wait for physics to settle (ms)
        auto_crop: Whether to crop empty background areas
        padding: Padding to keep around content when cropping

    Returns:
        PNG image bytes or None if failed
    """
    import os
    import tempfile

    from playwright.async_api import async_playwright

    with tempfile.TemporaryDirectory() as tmpdir:
        html_path = os.path.join(tmpdir, "graph.html")
        png_path = os.path.join(tmpdir, "graph.png")

        # XXX: Write PyVis HTML (includes vis.js library and network data)
        net.write_html(html_path)

        # XXX: Render with Playwright headless browser
        async with async_playwright() as p:
            browser = await p.chromium.launch()
            page = await browser.new_page(
                viewport={"width": width, "height": height}
            )

            with open(html_path, "r", encoding="utf-8") as f:
                html_content = f.read()

            # XXX: Load HTML and wait for network requests to complete
            await page.set_content(html_content, wait_until="networkidle")
            # XXX: Additional wait for physics simulation to settle
            await page.wait_for_timeout(wait_time)

            # XXX: Screenshot just the canvas element (no white borders from HTML body)
            canvas = await page.query_selector("canvas")
            if canvas:
                await canvas.screenshot(path=png_path)
            else:
                await page.screenshot(path=png_path, full_page=False)

            await browser.close()

        # XXX: Read PNG and optionally crop
        with open(png_path, "rb") as f:
            png_bytes = f.read()

        if auto_crop:
            png_bytes = _crop_to_content(png_bytes, padding)

        return png_bytes


def _crop_to_content(png_bytes: bytes, padding: int = 40) -> bytes:
    """
    Crop image to content area, removing empty background.

    # XXX: Algorithm:
    # XXX: 1. Detect background color from corner pixel
    # XXX: 2. Scan all pixels to find bounding box of non-background content
    # XXX: 3. Crop to bounding box + padding
    # XXX: This removes unnecessary whitespace/background from edges.

    Args:
        png_bytes: Original PNG image bytes
        padding: Padding to keep around content

    Returns:
        Cropped PNG bytes
    """
    # XXX: Open image
    img = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
    width, height = img.size

    # XXX: Get background color from corner (assuming it's the background)
    bg_color = img.getpixel((0, 0))

    # XXX: Find content bounds by scanning for non-background pixels
    min_x, min_y = width, height
    max_x, max_y = 0, 0

    pixels = img.load()
    for y in range(height):
        for x in range(width):
            pixel = pixels[x, y]
            # XXX: Check if pixel differs significantly from background
            if not _colors_similar(pixel, bg_color, threshold=30):
                min_x = min(min_x, x)
                min_y = min(min_y, y)
                max_x = max(max_x, x)
                max_y = max(max_y, y)

    # XXX: If no content found, return original
    if max_x <= min_x or max_y <= min_y:
        return png_bytes

    # XXX: Add padding
    min_x = max(0, min_x - padding)
    min_y = max(0, min_y - padding)
    max_x = min(width, max_x + padding)
    max_y = min(height, max_y + padding)

    # XXX: Crop
    cropped = img.crop((min_x, min_y, max_x, max_y))

    # XXX: Convert back to bytes
    buffer = io.BytesIO()
    cropped.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.read()


def _colors_similar(c1: tuple, c2: tuple, threshold: int = 30) -> bool:
    """
    Check if two RGBA colors are similar within threshold.

    # XXX: Only compares RGB channels, ignores alpha.
    # XXX: Threshold of 30 allows for minor color variations.
    """
    return all(abs(a - b) <= threshold for a, b in zip(c1[:3], c2[:3]))
