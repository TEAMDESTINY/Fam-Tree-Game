"""
Friend circle renderer using PyVis for graph visualization.

# XXX: This renderer creates a network graph showing the user's friend circle.
# XXX: Level 1 (default) = Direct friends connected to center, with inter-friend edges.
# XXX: Level 2+ = TODO: Friends of friends (hub-spoke topology).
"""

from typing import Dict, Optional, Set

from pyvis.network import Network

from bot.graphics.svg_utils import (
    create_composite_svg,
    export_network_to_png,
    get_user_image_b64,
)


async def render_friend_circle(
    client,
    center_user_id: int,
    level: int = 1,
) -> Optional[bytes]:
    """
    Render friend circle using PyVis.

    # XXX: Level controls the depth of the friend network shown:
    # XXX: - Level 1: Direct friends only (all connected to center, plus inter-friend edges)
    # XXX: - Level 2+: TODO - Would show friends of friends in a hub-spoke pattern

    Args:
        client: Bot client with db access
        center_user_id: The center user's ID
        level: Depth of friend network (1 = direct friends, 2+ = TODO)

    Returns:
        PNG image bytes or None if failed
    """
    # TODO: Implement level 2+ for friends of friends
    # For level 2+, use hub-and-spoke topology where:
    # - Center connects only to "hubs" (friends with 3+ mutual connections)
    # - Hubs connect to their clustered members
    # This helps manage visual complexity for large friend networks

    # XXX: ======== SECTION 1: FETCH DATA ========
    # XXX: Get the center user and their direct friends from database

    center_user = await client.db.get_user(center_user_id)
    if not center_user:
        return None

    friends = await client.db.get_friends(center_user_id)
    if not friends:
        # XXX: Solo user - just show them alone
        return await _render_solo_user(client, center_user_id, center_user)

    center_name = (center_user.get("first_name") or "You")[:15]

    # XXX: ======== SECTION 2: BUILD FRIEND DATA ========
    # XXX: Create a map of friend IDs to their data and find inter-friend connections

    friend_ids: Set[int] = set()
    friend_data: Dict[int, dict] = {}

    for f in friends:
        fid = f["user_id"]
        friend_ids.add(fid)
        friend_data[fid] = {
            "name": (f.get("first_name") or "Friend")[:15],
            "connections": set(),  # Other friends of center who this friend also knows
        }

    # XXX: Find which of center's friends are also friends with each other
    # XXX: This creates the inter-friend edges in the graph
    for fid in friend_ids:
        f_friends = await client.db.get_friends(fid)
        for ff in f_friends:
            ff_id = ff["user_id"]
            # Only count if both are friends of center
            if ff_id in friend_ids and ff_id != fid:
                friend_data[fid]["connections"].add(ff_id)

    # XXX: ======== SECTION 3: CREATE NETWORK ========
    # XXX: Initialize PyVis network with styling

    net = Network(
        height="850px",
        width="100%",
        bgcolor="#ADD8E6",  # Light blue background
        font_color="black",
        cdn_resources="in_line",  # Embeds all JS/CSS in HTML for standalone rendering
    )

    added_nodes: Set[int] = set()

    # XXX: Helper function to add a node with SVG image
    async def add_node(uid: int, is_center: bool = False):
        if uid in added_nodes:
            return
        added_nodes.add(uid)

        if is_center:
            name = center_name
        else:
            name = friend_data[uid]["name"]

        # XXX: Fetch profile image or use initials fallback
        image_b64 = await get_user_image_b64(client, uid, size=150)

        # XXX: Create composite SVG with image + name label
        svg = create_composite_svg(
            name=name,
            image_b64=image_b64,
            is_center=is_center,
            img_size=150,
            text_height=40,
            font_size=18,
        )

        # XXX: Center node is larger (50) than friend nodes (40)
        size = 50 if is_center else 40
        net.add_node(uid, label=" ", shape="image", image=svg, size=size)

    # XXX: ======== SECTION 4: ADD NODES AND EDGES ========
    # XXX: Level 1: Center connects to ALL friends, plus inter-friend edges

    # Add center node
    await add_node(center_user_id, is_center=True)

    # Add all friend nodes and connect to center
    for fid in friend_ids:
        await add_node(fid, is_center=False)
        # XXX: Edge from center to each friend (medium weight)
        net.add_edge(center_user_id, fid, color="#bd3e3e", width=2)

    # XXX: Add inter-friend edges (friends who know each other)
    # XXX: These are thinner lines to visually distinguish from center edges
    added_edges: Set[tuple] = set()
    for fid, data in friend_data.items():
        for connected_fid in data["connections"]:
            # Avoid duplicate edges (A-B and B-A)
            edge_key = tuple(sorted([fid, connected_fid]))
            if edge_key not in added_edges:
                added_edges.add(edge_key)
                net.add_edge(fid, connected_fid, color="#bd3e3e", width=1)

    # XXX: ======== SECTION 5: CONFIGURE PHYSICS ========
    # XXX: Barnes-Hut algorithm for force-directed layout
    # XXX: These values control how nodes repel/attract each other

    net.barnes_hut(
        gravity=-4000,  # Repulsion between nodes (negative = push apart)
        central_gravity=0.4,  # Pull towards center (keeps graph compact)
        spring_length=90,  # Ideal edge length
        spring_strength=0.04,  # How strongly edges pull nodes together
        damping=0.5,  # Friction (higher = settles faster)
        overlap=1,  # Overlap avoidance
    )

    # XXX: ======== SECTION 6: EXPORT ========
    # XXX: Render to PNG via Playwright headless browser

    return await export_network_to_png(
        net, width=1200, height=1200, wait_time=1000
    )


async def _render_solo_user(client, user_id: int, user) -> Optional[bytes]:
    """
    Render a single user with no friends.

    # XXX: For users with no friends, show just their node centered
    """
    net = Network(
        height="400px",
        width="400px",
        bgcolor="#ADD8E6",
        font_color="black",
        cdn_resources="in_line",
    )

    name = (user.get("first_name") or "You")[:15]
    image_b64 = await get_user_image_b64(client, user_id, size=150)

    svg = create_composite_svg(
        name=name,
        image_b64=image_b64,
        is_center=True,
        img_size=150,
        text_height=40,
        font_size=18,
    )

    net.add_node(user_id, label=" ", shape="image", image=svg, size=50)

    # XXX: Disable physics for single node (no need for force simulation)
    net.set_options('{"physics": {"enabled": false}}')

    return await export_network_to_png(
        net, width=400, height=400, wait_time=500
    )
