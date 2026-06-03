"""Friend network renderer - larger nodes, border gaps.

# XXX: Layout Algorithm Overview
# Uses a force-directed graph layout where:
# 1. Nodes repel each other (prevents overlap)
# 2. Connected nodes attract each other (keeps friends close)
# 3. Light gravity pulls everything toward center
# 4. The center user is fixed at the origin during simulation

# XXX: Level Configuration (TODO)
# Currently only shows direct friends (level 1).
# Future: Add configurable depth:
# - Level 1: Direct friends only (default)
# - Level 2+: Friends of friends (can get messy without proper clustering)

# XXX: Aiogram Migration Notes
# - Function signature changed from (client, user_id) to (bot, db, user_id)
"""

import io
import math
import random
from typing import Dict, Optional

from PIL import Image, ImageDraw

from bot.graphics.utils import (
    BLACK,
    BORDER_RED,
    LIGHT_BLUE_BG,
    RED_LINE,
    draw_text_centered,
    get_font,
    get_profile_image,
    square_crop,
)

# Configuration for "Larger Look"
LARGE_PROFILE_SIZE = 130


class Node:
    """Represents a person in the friend network."""

    def __init__(self, user_id, name):
        self.user_id = user_id
        self.name = name
        self.x = 0
        self.y = 0
        self.vx = 0  # Velocity for force simulation
        self.vy = 0
        self.connections = []
        self.image = None
        self.is_center = False


async def render_friend_circle(
    bot, db, center_user_id: int, depth: int = 1
) -> Optional[bytes]:
    """
    Render friend network visualization.

    Args:
        bot: Kurigram client instance
        db: Database instance
        center_user_id: The user whose network to render (highlighted with red border)
        depth: How many levels of friends to show (1-5)

    Returns:
        PNG image bytes, or None if user has no friends
    """
    # 1. Fetch Data
    center_user = await db.get_user(center_user_id)
    if not center_user:
        return None
    friends = await db.get_friends(center_user_id)
    if not friends:
        return None

    # 2. Build Graph with configurable depth
    nodes: Dict[int, Node] = {}
    levels: Dict[int, int] = {}  # Track which level each user is at

    c_node = Node(center_user_id, (center_user["first_name"] or "You")[:12])
    c_node.is_center = True
    nodes[center_user_id] = c_node
    levels[center_user_id] = 0

    # BFS to get friends up to specified depth
    current_level_ids = {center_user_id}
    for current_depth in range(1, depth + 1):
        next_level_ids = set()
        for uid in current_level_ids:
            user_friends = await db.get_friends(uid)
            for f in user_friends:
                fid = f["user_id"]
                if fid not in nodes:
                    f_node = Node(fid, (f["first_name"] or "Friend")[:12])
                    nodes[fid] = f_node
                    levels[fid] = current_depth
                    next_level_ids.add(fid)

                # Add connection (both ways)
                if uid in nodes and fid in nodes:
                    if nodes[fid] not in nodes[uid].connections:
                        nodes[uid].connections.append(nodes[fid])
                    if nodes[uid] not in nodes[fid].connections:
                        nodes[fid].connections.append(nodes[uid])

        current_level_ids = next_level_ids
        if not next_level_ids:
            break  # No more friends to explore

    node_list = list(nodes.values())
    count = len(node_list)

    # 3. Force Directed Layout (run first, then calculate canvas size)
    # Start with center at origin, spread nodes in a wider initial circle
    for n in node_list:
        if n.is_center:
            n.x, n.y = 0, 0
        else:
            angle = random.random() * math.pi * 2
            # Spread initial positions based on node count
            base_dist = 200 + count * 8
            dist = random.uniform(base_dist * 0.5, base_dist)
            n.x = math.cos(angle) * dist
            n.y = math.sin(angle) * dist

    # Layout parameters - generous spacing
    iterations = 500
    k_repulse = 800000  # Strong repulsion for good spacing
    k_attract = 0.008  # Weak attraction - don't pull too tight
    gravity = 0.001  # Very light gravity - allow spread
    min_dist = LARGE_PROFILE_SIZE + 150  # Generous minimum distance

    for _ in range(iterations):
        for n in node_list:
            if not n.is_center:
                # Very light gravity towards center
                dx_grav = 0 - n.x
                dy_grav = 0 - n.y
                n.vx += dx_grav * gravity
                n.vy += dy_grav * gravity

            for other in node_list:
                if n == other:
                    continue
                dx = n.x - other.x
                dy = n.y - other.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist < 1:
                    dist = 1

                # Strong repulsion - always push apart for nice spacing
                if dist < min_dist * 2:
                    f = k_repulse / (dist * dist)
                    n.vx += (dx / dist) * f
                    n.vy += (dy / dist) * f

            for conn in n.connections:
                dx = conn.x - n.x
                dy = conn.y - n.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0:
                    # Weak attraction - keep connected but not too close
                    ideal_dist = min_dist * 1.8
                    f = (dist - ideal_dist) * k_attract
                    n.vx += (dx / dist) * f
                    n.vy += (dy / dist) * f

        for n in node_list:
            if not n.is_center:
                n.x += n.vx
                n.y += n.vy
                n.vx *= 0.85
                n.vy *= 0.85

    # 4. Calculate bounding box AFTER layout
    TEXT_HEIGHT = 35  # Space for text below
    PADDING = 80  # Canvas padding

    min_x = min(n.x for n in node_list) - LARGE_PROFILE_SIZE // 2 - PADDING
    max_x = max(n.x for n in node_list) + LARGE_PROFILE_SIZE // 2 + PADDING
    min_y = min(n.y for n in node_list) - LARGE_PROFILE_SIZE // 2 - PADDING
    max_y = (
        max(n.y for n in node_list)
        + LARGE_PROFILE_SIZE // 2
        + TEXT_HEIGHT
        + PADDING
    )

    # Shift all nodes so min is at padding
    offset_x = -min_x + PADDING
    offset_y = -min_y + PADDING
    for n in node_list:
        n.x += offset_x
        n.y += offset_y

    # Canvas size based on content
    canvas_w = int(max_x - min_x)
    canvas_h = int(max_y - min_y)

    # 5. Drawing
    canvas = Image.new("RGBA", (canvas_w, canvas_h), LIGHT_BLUE_BG)
    draw = ImageDraw.Draw(canvas)

    for n in node_list:
        n.image = await get_profile_image(
            bot, db, n.user_id, LARGE_PROFILE_SIZE
        )
        n.image = square_crop(n.image, LARGE_PROFILE_SIZE)

    # Lines First - shortened to stop at node bounding box (image + text below)
    drawn_links = set()
    font = get_font(22, bold=True)
    TEXT_HEIGHT = 25  # Approximate text height
    TEXT_GAP = 8  # Gap between image and text

    # Node bounding box: image is centered at (x, y), text is below
    # Top edge: y - PROFILE/2
    # Bottom edge: y + PROFILE/2 + TEXT_GAP + TEXT_HEIGHT
    # Left/Right edges: x ± PROFILE/2

    for n in node_list:
        for conn in n.connections:
            link_id = tuple(sorted((n.user_id, conn.user_id)))
            if link_id not in drawn_links:
                # Calculate direction vector
                dx = conn.x - n.x
                dy = conn.y - n.y
                dist = math.sqrt(dx * dx + dy * dy)
                if dist > 0:
                    # Normalize direction
                    ndx = dx / dist
                    ndy = dy / dist

                    # Calculate margin based on direction (rectangular bounding box)
                    half_w = LARGE_PROFILE_SIZE // 2 + 5
                    half_h_top = LARGE_PROFILE_SIZE // 2 + 5
                    half_h_bottom = (
                        LARGE_PROFILE_SIZE // 2 + TEXT_GAP + TEXT_HEIGHT + 5
                    )

                    # For start node: determine which edge the line exits from
                    if abs(ndx) > abs(ndy):
                        # Exiting from left or right
                        start_margin = half_w / abs(ndx) if ndx != 0 else half_w
                    else:
                        # Exiting from top or bottom
                        if ndy > 0:
                            start_margin = (
                                half_h_bottom / abs(ndy)
                                if ndy != 0
                                else half_h_bottom
                            )
                        else:
                            start_margin = (
                                half_h_top / abs(ndy)
                                if ndy != 0
                                else half_h_top
                            )

                    # For end node: determine which edge the line enters from
                    if abs(ndx) > abs(ndy):
                        end_margin = half_w / abs(ndx) if ndx != 0 else half_w
                    else:
                        if ndy < 0:
                            end_margin = (
                                half_h_bottom / abs(ndy)
                                if ndy != 0
                                else half_h_bottom
                            )
                        else:
                            end_margin = (
                                half_h_top / abs(ndy)
                                if ndy != 0
                                else half_h_top
                            )

                    start_x = n.x + ndx * start_margin
                    start_y = n.y + ndy * start_margin
                    end_x = conn.x - ndx * end_margin
                    end_y = conn.y - ndy * end_margin

                    # Only draw if there's space between nodes
                    if dist > start_margin + end_margin:
                        draw.line(
                            [(start_x, start_y), (end_x, end_y)],
                            fill=RED_LINE,
                            width=2,
                        )
                drawn_links.add(link_id)

    # Avatars & Text
    font = get_font(22, bold=True)  # Readable font

    for n in node_list:
        left = int(n.x - LARGE_PROFILE_SIZE / 2)
        top = int(n.y - LARGE_PROFILE_SIZE / 2)

        if n.is_center:
            gap = 8
            thick = 4
            border_left = left - gap
            border_top = top - gap
            border_right = left + LARGE_PROFILE_SIZE + gap
            border_bottom = top + LARGE_PROFILE_SIZE + gap

            draw.rectangle(
                [border_left, border_top, border_right, border_bottom],
                outline=BORDER_RED,
                width=thick,
            )

        canvas.paste(n.image, (left, top), n.image)

        # Text BELOW image to avoid line intersections
        tx = int(n.x)
        ty = int(top + LARGE_PROFILE_SIZE + 8)  # Below image with gap
        if n.is_center:
            ty += 5  # Extra space for bordered center node
        draw_text_centered(draw, n.name, tx, ty, font, BLACK)

    buffer = io.BytesIO()
    canvas.save(buffer, format="PNG")
    buffer.seek(0)
    return buffer.getvalue()
