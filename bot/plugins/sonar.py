"""Sonar treasure hunt mini-game."""

import random
from io import BytesIO
from typing import Optional

from pyrogram.types import Message
from PIL import Image, ImageDraw, ImageFont

from bot.command_registry import reg
from bot.constants import (
    SONAR_CHEST_COUNT,
    SONAR_CHEST_REWARD,
    SONAR_GRID_SIZE,
    SONAR_GUESS_COST,
)
from bot.input_file import to_input_file
from bot.database import Database
from pyrogram import filters
from bot.client import client
from bot.database import db


# Register commands

# Grid columns A-J
COLUMNS = "ABCDEFGHIJ"

# Light theme colors (matching the blue grid template)
SONAR_BG_COLOR = "#4CACDC"  # Light blue background
SONAR_CELL_COLOR = "#4CACDC"  # Same as background for empty cells
SONAR_GRID_COLOR = "#2A2A2A"  # Dark grid lines
SONAR_TEXT_COLOR = "#2A2A2A"  # Dark text for headers
SONAR_FOUND_COLOR = "#FFD700"  # Gold for found chests
SONAR_MISSED_COLOR = "#E8E8E8"  # Light gray for missed chests (game over)


def chebyshev_distance(r1: int, c1: int, r2: int, c2: int) -> int:
    """Calculate Chebyshev distance (king's movement in chess)."""
    return max(abs(r1 - r2), abs(c1 - c2))


def get_min_distance(row: int, col: int, chests: list[tuple[int, int]]) -> int:
    """Get minimum distance to any chest."""
    if not chests:
        return -1
    return min(chebyshev_distance(row, col, cr, cc) for cr, cc in chests)


# Simple chest SVG icon as a path (drawn with PIL)
def draw_chest_icon(
    draw: ImageDraw.Draw, x: int, y: int, size: int, color: str
):
    """Draw a simple chest icon at the given position."""
    # Chest body (rectangle with rounded appearance)
    margin = size // 6
    body_top = y + margin
    body_bottom = y + size - margin
    body_left = x + margin
    body_right = x + size - margin

    # Draw chest body
    draw.rectangle(
        [body_left, body_top + size // 6, body_right, body_bottom],
        fill=color,
        outline="#8B4513",  # Brown outline
        width=2,
    )

    # Draw chest lid (arc/dome)
    lid_height = size // 4
    draw.rectangle(
        [body_left, body_top, body_right, body_top + lid_height + 2],
        fill=color,
        outline="#8B4513",
        width=2,
    )

    # Draw lock/clasp in center
    lock_size = size // 8
    lock_x = x + size // 2 - lock_size // 2
    lock_y = body_top + lid_height - lock_size // 2
    draw.rectangle(
        [lock_x, lock_y, lock_x + lock_size, lock_y + lock_size * 2],
        fill="#8B4513",
    )


def generate_sonar_image(
    grid_size: int,
    guesses: dict[tuple[int, int], int],
    found_chests: list[tuple[int, int]],
    remaining_chests: list[tuple[int, int]],
    game_over: bool = False,
) -> bytes:
    """Generate a sonar grid image with light blue theme."""
    cell_size = 50
    padding = 35
    width = padding + cell_size * grid_size + 15
    height = padding + cell_size * grid_size + 15

    # Create image with light blue background
    img = Image.new("RGB", (width, height), SONAR_BG_COLOR)
    draw = ImageDraw.Draw(img)

    # Try to load fonts
    try:
        # Main font for numbers and letters
        font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 16
        )
        small_font = ImageFont.truetype(
            "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 14
        )
    except Exception:
        font = ImageFont.load_default()
        small_font = font

    # Draw column headers (A-J)
    for i in range(grid_size):
        x = padding + i * cell_size + cell_size // 2
        draw.text(
            (x, 12), COLUMNS[i], fill=SONAR_TEXT_COLOR, font=font, anchor="mm"
        )

    # Draw row numbers and grid
    for row in range(grid_size):
        y = padding + row * cell_size

        # Row number (left side)
        draw.text(
            (14, y + cell_size // 2),
            str(row + 1),
            fill=SONAR_TEXT_COLOR,
            font=font,
            anchor="mm",
        )

        for col in range(grid_size):
            x = padding + col * cell_size
            cell_x = x
            cell_y = y

            # Determine cell state
            is_found = (row, col) in found_chests
            is_remaining = game_over and (row, col) in remaining_chests
            is_guessed = (row, col) in guesses

            # Draw cell border (grid lines)
            draw.rectangle(
                [cell_x, cell_y, cell_x + cell_size, cell_y + cell_size],
                outline=SONAR_GRID_COLOR,
                width=1,
            )

            if is_found:
                # Found chest - gold background with chest icon
                draw.rectangle(
                    [
                        cell_x + 1,
                        cell_y + 1,
                        cell_x + cell_size - 1,
                        cell_y + cell_size - 1,
                    ],
                    fill=SONAR_FOUND_COLOR,
                )
                draw_chest_icon(draw, cell_x, cell_y, cell_size, "#DAA520")

            elif is_remaining:
                # Game over - show remaining chests (grayed out)
                draw.rectangle(
                    [
                        cell_x + 1,
                        cell_y + 1,
                        cell_x + cell_size - 1,
                        cell_y + cell_size - 1,
                    ],
                    fill=SONAR_MISSED_COLOR,
                )
                draw_chest_icon(draw, cell_x, cell_y, cell_size, "#A0A0A0")

            elif is_guessed:
                # Guessed cell - show distance number
                dist = guesses[(row, col)]
                if dist == 0:
                    # Found chest (already handled above, but just in case)
                    draw.rectangle(
                        [
                            cell_x + 1,
                            cell_y + 1,
                            cell_x + cell_size - 1,
                            cell_y + cell_size - 1,
                        ],
                        fill=SONAR_FOUND_COLOR,
                    )
                    draw_chest_icon(draw, cell_x, cell_y, cell_size, "#DAA520")
                elif dist < 0:
                    # No chests remaining - empty dot
                    draw.text(
                        (cell_x + cell_size // 2, cell_y + cell_size // 2),
                        "·",
                        fill="#666666",
                        font=font,
                        anchor="mm",
                    )
                else:
                    # Color based on distance (closer = warmer/red, farther = cooler)
                    if dist <= 2:
                        bg_color = "#FF6B6B"  # Red - very close
                        text_col = "#FFFFFF"
                    elif dist <= 4:
                        bg_color = "#FFA500"  # Orange - close
                        text_col = "#FFFFFF"
                    elif dist <= 6:
                        bg_color = "#FFEB3B"  # Yellow - medium
                        text_col = "#000000"
                    else:
                        bg_color = "#90CAF9"  # Light blue - far
                        text_col = "#000000"

                    draw.rectangle(
                        [
                            cell_x + 1,
                            cell_y + 1,
                            cell_x + cell_size - 1,
                            cell_y + cell_size - 1,
                        ],
                        fill=bg_color,
                    )
                    draw.text(
                        (cell_x + cell_size // 2, cell_y + cell_size // 2),
                        str(dist),
                        fill=text_col,
                        font=font,
                        anchor="mm",
                    )
            # Empty cells just show the grid (blue background already)

    # Convert to bytes
    buffer = BytesIO()
    img.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()


def parse_coordinates(coord_str: str) -> Optional[tuple[int, int]]:
    """Parse coordinate string like 'C5' to (row, col) tuple."""
    coord_str = coord_str.strip().upper()
    if len(coord_str) < 2 or len(coord_str) > 3:
        return None

    col_letter = coord_str[0]
    row_str = coord_str[1:]

    if col_letter not in COLUMNS:
        return None

    try:
        row = int(row_str)
        if row < 1 or row > SONAR_GRID_SIZE:
            return None
        return (row - 1, COLUMNS.index(col_letter))
    except ValueError:
        return None


reg("sonar", "🔊 Start Sonar game")


@client.on_message(filters.command(["sonar"]))
async def sonar_command(
    message: Message,
):
    """Start or view a Sonar game."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    chat_id = message.chat.id

    # Check for existing game in this chat
    existing = await db.get_active_sonar_game(chat_id)

    if existing:
        # Show existing game
        text = "🔊 <b>Sonar Game in Progress</b>\n\n"
        text += (
            f"💸 Chests Found: {existing['chests_found']}/{SONAR_CHEST_COUNT}\n"
        )
        text += f"💰 Cost per guess: ${SONAR_GUESS_COST:,}\n"
        text += f"🎁 Reward per chest: ${SONAR_CHEST_REWARD:,}\n\n"
        text += "Use <code>/p A1</code> or <code>/put B3</code> to guess!\n"
        text += "Multiple guesses: <code>/p A1 B2 C3</code>"

        # Generate current grid image
        guesses = existing.get("guesses", {}) or {}
        found = existing.get("found_positions", []) or []

        # Normalize found positions to list of (row, col)
        found_pairs: list[tuple[int, int]] = []
        if isinstance(found, str):
            import json

            try:
                found = json.loads(found)
            except Exception:
                found = []

        if isinstance(found, (list, tuple)) and found:
            if all(isinstance(x, int) for x in found):
                found_pairs = [
                    (int(found[i]), int(found[i + 1]))
                    for i in range(0, len(found), 2)
                ]
            else:
                for p in found:
                    if isinstance(p, str):
                        parts = p.split(",")
                        if len(parts) >= 2:
                            try:
                                found_pairs.append((
                                    int(parts[0]),
                                    int(parts[1]),
                                ))
                            except Exception:
                                continue
                    elif isinstance(p, (list, tuple)) and len(p) >= 2:
                        try:
                            found_pairs.append((int(p[0]), int(p[1])))
                        except Exception:
                            continue

        # Normalize chest positions to list of (row, col) pairs
        chest_positions_raw = existing.get("chest_positions", []) or []
        if isinstance(chest_positions_raw, str):
            import json

            try:
                chest_positions_raw = json.loads(chest_positions_raw)
            except Exception:
                chest_positions_raw = []

        chest_positions_pairs: list[tuple[int, int]] = []
        if (
            isinstance(chest_positions_raw, (list, tuple))
            and chest_positions_raw
        ):
            if all(isinstance(x, int) for x in chest_positions_raw):
                chest_positions_pairs = [
                    (
                        int(chest_positions_raw[i]),
                        int(chest_positions_raw[i + 1]),
                    )
                    for i in range(0, len(chest_positions_raw), 2)
                ]
            else:
                for p in chest_positions_raw:
                    if isinstance(p, (list, tuple)) and len(p) >= 2:
                        chest_positions_pairs.append((int(p[0]), int(p[1])))

        chest_set = set(chest_positions_pairs)
        found_set = set(found_pairs)
        remaining_chests = [c for c in chest_set if c not in found_set]

        # Normalize guesses to {(row,int): dist} and recompute distances
        guesses_dict: dict[tuple[int, int], int] = {}
        if isinstance(guesses, dict):
            for k, v in guesses.items():
                if isinstance(k, str):
                    parts = k.split(",")
                    if len(parts) >= 2:
                        try:
                            rk, ck = int(parts[0]), int(parts[1])
                            guesses_dict[(rk, ck)] = get_min_distance(
                                rk, ck, remaining_chests
                            )
                        except Exception:
                            continue
                elif isinstance(k, (list, tuple)) and len(k) >= 2:
                    try:
                        rk, ck = int(k[0]), int(k[1])
                        guesses_dict[(rk, ck)] = get_min_distance(
                            rk, ck, remaining_chests
                        )
                    except Exception:
                        continue

        img_bytes = generate_sonar_image(
            SONAR_GRID_SIZE,
            guesses_dict,
            found_pairs,
            [],  # Don't reveal remaining chests
        )

        photo = to_input_file(img_bytes, filename="sonar.png")
        await message.reply_photo(photo, caption=text)
        return

    # Check wallet for starting a new game
    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < SONAR_GUESS_COST:
        await message.reply(
            f"❌ You need at least ${SONAR_GUESS_COST:,} to start a Sonar game!"
        )
        return

    # Create new game
    # Generate random chest positions (store as flat list of ints for DB)
    chest_set: set[tuple[int, int]] = set()
    while len(chest_set) < SONAR_CHEST_COUNT:
        r = random.randint(0, SONAR_GRID_SIZE - 1)
        c = random.randint(0, SONAR_GRID_SIZE - 1)
        chest_set.add((r, c))

    positions: list[int] = []
    for r, c in chest_set:
        positions.extend([r, c])

    await db.create_sonar_game(user.id, SONAR_GUESS_COST, positions, chat_id)

    text = "🔊 <b>New Sonar Game Started!</b>\n\n"
    text += f"🎯 Find {SONAR_CHEST_COUNT} hidden chests in a {SONAR_GRID_SIZE}×{SONAR_GRID_SIZE} grid!\n\n"
    text += "📏 Numbers show distance to nearest chest (diagonal = 1 step)\n"
    text += f"💰 Cost per guess: ${SONAR_GUESS_COST:,}\n"
    text += f"🎁 Reward per chest: ${SONAR_CHEST_REWARD:,}\n\n"
    text += "Use <code>/p A1</code> or <code>/put B3</code> to guess!\n"
    text += "Multiple guesses: <code>/p A1 B2 C3</code>"

    # Generate empty grid image
    img_bytes = generate_sonar_image(SONAR_GRID_SIZE, {}, [], [])
    photo = to_input_file(img_bytes, filename="sonar.png")

    await message.reply_photo(photo, caption=text)


reg("p", "📍 Put/guess in Sonar [/put]")


@client.on_message(filters.command(["p", "put"]))
async def put_command(
    message: Message,
):
    """Make a guess in the Sonar game."""
    user = message.from_user
    await db.upsert_user(user.id, user.username, user.first_name)

    chat_id = message.chat.id

    # Get active game
    game = await db.get_active_sonar_game(chat_id)
    if not game:
        await message.reply("❌ No active Sonar game! Use /sonar to start one.")
        return

    # Parse coordinates
    args = message.text.split()[1:] if message.text else []
    if not args:
        await message.reply(
            "❌ Usage: <code>/p A1</code> or <code>/p A1 B2 C3</code>"
        )
        return

    # Parse all coordinates
    coords = []
    for arg in args:
        parsed = parse_coordinates(arg)
        if parsed:
            coords.append(parsed)
        else:
            await message.reply(f"❌ Invalid coordinate: {arg}")
            return

    # Check wallet
    total_cost = SONAR_GUESS_COST * len(coords)
    wallet = await db.get_wallet(user.id)
    if wallet["balance"] < total_cost:
        await message.reply(
            f"❌ Need ${total_cost:,} for {len(coords)} guesses! You have ${wallet['balance']:,}"
        )
        return

    # Process guesses
    guesses = game.get("guesses", {}) or {}
    if isinstance(guesses, str):
        import json

        guesses = json.loads(guesses) if guesses else {}

    found_positions = game.get("found_positions", []) or []
    if isinstance(found_positions, str):
        import json

        found_positions = json.loads(found_positions) if found_positions else []

    chest_positions_raw = game.get("chest_positions", []) or []
    if isinstance(chest_positions_raw, str):
        import json

        chest_positions_raw = (
            json.loads(chest_positions_raw) if chest_positions_raw else []
        )

    # Normalize chest positions to list of (row, col) pairs
    chest_positions_pairs: list[tuple[int, int]] = []
    if isinstance(chest_positions_raw, (list, tuple)) and chest_positions_raw:
        # Flat list of ints: [r1,c1,r2,c2,...]
        if all(isinstance(x, int) for x in chest_positions_raw):
            chest_positions_pairs = [
                (int(chest_positions_raw[i]), int(chest_positions_raw[i + 1]))
                for i in range(0, len(chest_positions_raw), 2)
            ]
        else:
            for p in chest_positions_raw:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    chest_positions_pairs.append((int(p[0]), int(p[1])))

    # Normalize found positions similarly
    found_positions_raw = found_positions or []
    if isinstance(found_positions_raw, str):
        import json

        found_positions_raw = (
            json.loads(found_positions_raw) if found_positions_raw else []
        )

    found_pairs: set[tuple[int, int]] = set()
    if isinstance(found_positions_raw, (list, tuple)) and found_positions_raw:
        if all(isinstance(x, int) for x in found_positions_raw):
            found_pairs = set(
                (int(found_positions_raw[i]), int(found_positions_raw[i + 1]))
                for i in range(0, len(found_positions_raw), 2)
            )
        else:
            for p in found_positions_raw:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    found_pairs.add((int(p[0]), int(p[1])))

    found_set = found_pairs
    chest_set = set(chest_positions_pairs)
    remaining_chests = [c for c in chest_set if c not in found_set]

    # Process each guess
    new_finds = []
    guesses_made = []

    for row, col in coords:
        key = f"{row},{col}"

        # Check if already guessed
        if key in guesses:
            continue

        guesses_made.append((row, col))

        # Calculate distance to nearest remaining chest
        dist = get_min_distance(row, col, remaining_chests)
        guesses[key] = dist

        if dist == 0:
            # Found a chest!
            found_set.add((row, col))
            remaining_chests = [c for c in remaining_chests if c != (row, col)]
            new_finds.append((row, col))

    if not guesses_made:
        await message.reply("❌ All those cells were already guessed!")
        return

    # Deduct cost
    actual_cost = SONAR_GUESS_COST * len(guesses_made)
    await db.add_balance(
        user.id, -actual_cost, f"Sonar: {len(guesses_made)} guesses"
    )

    # Add rewards for found chests
    if new_finds:
        reward = SONAR_CHEST_REWARD * len(new_finds)
        await db.add_balance(
            user.id, reward, f"Sonar: Found {len(new_finds)} chests"
        )

    # Recompute distances for all guesses relative to remaining chests
    # so previous guess numbers update to point to the next-nearest chest
    for k in list(guesses.keys()):
        try:
            r, c = map(int, k.split(","))
            guesses[k] = get_min_distance(r, c, remaining_chests)
        except Exception:
            continue

    # Update game state
    chests_found = len(found_set)
    is_complete = chests_found >= SONAR_CHEST_COUNT

    await db.update_sonar_game(
        game["id"],
        guesses=guesses,
        found_positions=list(found_set),
        chests_found=chests_found,
        completed=is_complete,
    )

    # Generate result text
    text = "🔊 <b>Sonar Results</b>\n\n"

    for row, col in guesses_made:
        coord_str = f"{COLUMNS[col]}{row + 1}"
        dist = guesses[f"{row},{col}"]
        if dist == 0:
            text += f"💸 <b>{coord_str}</b> - CHEST FOUND! (+${SONAR_CHEST_REWARD:,})\n"
        else:
            text += f"📍 <b>{coord_str}</b> - Distance: {dist}\n"

    text += f"\n💰 Cost: ${actual_cost:,}"
    if new_finds:
        text += f" | Earned: ${SONAR_CHEST_REWARD * len(new_finds):,}"

    text += f"\n💸 Chests: {chests_found}/{SONAR_CHEST_COUNT}"

    if is_complete:
        text += "\n\n🎉 <b>GAME COMPLETE!</b> All chests found!"

    # Generate grid image
    guesses_dict = {
        (int(k.split(",")[0]), int(k.split(",")[1])): v
        for k, v in guesses.items()
    }

    img_bytes = generate_sonar_image(
        SONAR_GRID_SIZE,
        guesses_dict,
        list(found_set),
        remaining_chests if is_complete else [],
        game_over=is_complete,
    )

    photo = to_input_file(img_bytes, filename="sonar.png")
    await message.reply_photo(photo, caption=text)


@client.on_message(filters.command(["sonar_end"]))
async def sonar_end_command(
    message: Message,
):
    """End the current Sonar game (reveal all chests)."""
    user = message.from_user
    chat_id = message.chat.id

    game = await db.get_active_sonar_game(chat_id)
    if not game:
        await message.reply("❌ No active Sonar game!")
        return

    # Only game starter can end
    if game["started_by"] != user.id:
        await message.reply("❌ Only the player who started can end the game!")
        return

    # Get game state
    guesses = game.get("guesses", {}) or {}
    if isinstance(guesses, str):
        import json

        guesses = json.loads(guesses) if guesses else {}

    found_positions = game.get("found_positions", []) or []
    if isinstance(found_positions, str):
        import json

        found_positions = json.loads(found_positions) if found_positions else []

    chest_positions_raw = game.get("chest_positions", []) or []
    if isinstance(chest_positions_raw, str):
        import json

        chest_positions_raw = (
            json.loads(chest_positions_raw) if chest_positions_raw else []
        )

    # Normalize chest positions to list of pairs
    chest_positions_pairs: list[tuple[int, int]] = []
    if isinstance(chest_positions_raw, (list, tuple)) and chest_positions_raw:
        if all(isinstance(x, int) for x in chest_positions_raw):
            chest_positions_pairs = [
                (int(chest_positions_raw[i]), int(chest_positions_raw[i + 1]))
                for i in range(0, len(chest_positions_raw), 2)
            ]
        else:
            for p in chest_positions_raw:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    chest_positions_pairs.append((int(p[0]), int(p[1])))

    found_positions_raw = found_positions or []
    if isinstance(found_positions_raw, str):
        import json

        found_positions_raw = (
            json.loads(found_positions_raw) if found_positions_raw else []
        )

    found_pairs: set[tuple[int, int]] = set()
    if isinstance(found_positions_raw, (list, tuple)) and found_positions_raw:
        if all(isinstance(x, int) for x in found_positions_raw):
            found_pairs = set(
                (int(found_positions_raw[i]), int(found_positions_raw[i + 1]))
                for i in range(0, len(found_positions_raw), 2)
            )
        else:
            for p in found_positions_raw:
                if isinstance(p, (list, tuple)) and len(p) >= 2:
                    found_pairs.add((int(p[0]), int(p[1])))

    found_set = found_pairs
    chest_set = set(chest_positions_pairs)
    remaining = [c for c in chest_set if c not in found_set]

    # Generate final image showing all chests
    guesses_dict = {
        (int(k.split(",")[0]), int(k.split(",")[1])): v
        for k, v in guesses.items()
    }

    text = "🔊 <b>Sonar Game Ended</b>\n\n"
    text += f"💸 Chests Found: {len(found_set)}/{SONAR_CHEST_COUNT}\n"
    text += f"❌ Missed: {len(remaining)}\n\n"

    if remaining:
        text += "<b>Remaining chest locations:</b>\n"
        for row, col in remaining:
            text += f"  📍 {COLUMNS[col]}{row + 1}\n"

    img_bytes = generate_sonar_image(
        SONAR_GRID_SIZE,
        guesses_dict,
        list(found_set),
        remaining,
        game_over=True,
    )

    photo = to_input_file(img_bytes, filename="sonar.png")
    await message.reply_photo(photo, caption=text)
