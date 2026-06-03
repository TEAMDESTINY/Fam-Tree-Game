"""
Family tree renderer using PyVis for hierarchical graph visualization.

# XXX: This renderer creates a hierarchical family tree visualization.
# XXX: Key concepts:
# XXX: - Uses "level" to determine vertical position (generation)
# XXX: - Uses invisible "union nodes" to create bracket-style connections
# XXX: - Marriage edges are pink with heart emoji and act as magnets
# XXX: - Supports multiple parents (adoption scenarios)
"""

from dataclasses import dataclass
from typing import Dict, List, Optional, Set, Tuple

from pyvis.network import Network

from bot.graphics.svg_utils import (
    create_composite_svg,
    export_network_to_png,
    get_user_image_b64,
)


@dataclass
class FamilyMember:
    """
    Represents a family member in the tree.

    # XXX: Each member has a level (generation) that determines vertical position.
    # XXX: Level 0 is normalized later - actual values depend on tree structure.
    """

    user_id: int
    name: str
    level: int = 0
    image_b64: Optional[str] = None
    is_center: bool = False


class FamilyTreeBuilder:
    """
    Builds family tree data from database.

    # XXX: This class traverses the family relationships in the database
    # XXX: and builds a complete tree structure with:
    # XXX: - members: Dict of user_id -> FamilyMember
    # XXX: - unions: List of parent-children groupings for bracket connections
    # XXX: - marriages: List of married pairs for pink heart edges
    """

    def __init__(self, client, center_user_id: int):
        self.client = client
        self.center_id = center_user_id
        self.members: Dict[int, FamilyMember] = {}
        self.unions: List[Dict] = []  # {"parents": [ids], "children": [ids]}
        self.marriages: List[Tuple[int, int]] = []
        self.visited: Set[int] = set()

    async def build(self, max_depth: int = 4) -> Optional[FamilyMember]:
        """
        Build the family tree starting from center user.

        # XXX: Algorithm:
        # XXX: 1. Start from center user at level 0
        # XXX: 2. Build ancestors upward (negative levels)
        # XXX: 3. Build descendants downward (positive levels)
        # XXX: 4. Load spouse relationships
        # XXX: 5. Normalize levels so minimum is 0
        # XXX: 6. Double all levels to leave room for union nodes

        # TODO: Add depth limit configuration for very large trees
        """
        center = await self._create_member(self.center_id, level=0)
        if not center:
            return None

        center.is_center = True

        # XXX: Build ancestors (going up the tree)
        await self._build_ancestors(center, max_depth)

        # XXX: Build descendants (going down the tree)
        await self._build_descendants(center, max_depth)

        # XXX: Load spouse relationships (marriage edges)
        await self._load_marriages()

        # XXX: Normalize levels so minimum = 0, then multiply by 2
        # XXX: The *2 creates space for invisible union nodes between generations
        if self.members:
            min_level = min(m.level for m in self.members.values())
            for m in self.members.values():
                m.level = (m.level - min_level) * 2

            # XXX: Post-process levels to ensure children are always below parents
            # XXX: This handles complex cases like parent's sibling's child marrying their child
            await self._adjust_levels_for_parent_child_relationships()

        # XXX: Build union data for bracket-style connections
        await self._build_unions()

        return center

    async def _adjust_levels_for_parent_child_relationships(self):
        """
        Ensure all children are at least 2 levels below their parents.

        # XXX: This handles edge cases where complex marriages could cause
        # XXX: parent and child to appear at the same level.
        """
        changed = True
        max_iterations = 10  # Prevent infinite loops
        iteration = 0

        while changed and iteration < max_iterations:
            changed = False
            iteration += 1

            # Get all parent-child relationships
            for uid in list(self.members.keys()):
                children = await self.client.db.get_children(uid)
                if uid not in self.members:
                    continue

                parent_level = self.members[uid].level

                for child in children:
                    cid = child["user_id"]
                    if cid in self.members:
                        # Child must be at least 2 levels below parent
                        min_child_level = parent_level + 2
                        if self.members[cid].level < min_child_level:
                            self.members[cid].level = min_child_level
                            changed = True

    async def _create_member(
        self, user_id: int, level: int
    ) -> Optional[FamilyMember]:
        """
        Create a family member node.

        # XXX: Fetches user data and profile image from database.
        # XXX: Caches in self.members to avoid duplicate creation.
        """
        if user_id in self.members:
            return self.members[user_id]

        user = await self.client.db.get_user(user_id)
        if not user:
            return None

        name = user.get("first_name") or "Unknown"
        if len(name) > 15:
            name = name[:14] + "…"

        # XXX: Fetch profile image (from base64 or Telegram file)
        image_b64 = await get_user_image_b64(self.client, user_id, size=120)

        member = FamilyMember(
            user_id=user_id,
            name=name,
            level=level,
            image_b64=image_b64,
        )
        self.members[user_id] = member
        return member

    async def _build_ancestors(self, member: FamilyMember, remaining: int):
        """
        Build ancestors and their other children (siblings).

        # XXX: Recursively goes up the tree, getting parents of each member.
        # XXX: Also builds siblings (other children of the same parents).
        """
        if remaining <= 0:
            return

        parents = await self.client.db.get_parents(member.user_id)
        parent_ids = []

        for p in parents:
            pid = p["user_id"]
            if pid in self.visited:
                if pid in self.members:
                    parent_ids.append(pid)
                continue

            self.visited.add(pid)
            p_member = await self._create_member(pid, member.level - 1)
            if p_member:
                parent_ids.append(pid)
                # XXX: Get siblings (other children of this parent)
                await self._build_siblings(p_member, member.level)
                await self._build_ancestors(p_member, remaining - 1)

        # XXX: Store union info for bracket connections
        if parent_ids:
            children = await self._get_all_children_of_parents(
                parent_ids, member.level
            )
            if children:
                self.unions.append({
                    "parents": parent_ids,
                    "children": children,
                })

    async def _build_siblings(self, parent: FamilyMember, sibling_level: int):
        """
        Get all children of a parent (siblings).

        # XXX: Ensures all siblings are in the tree at the correct level.
        # XXX: Also builds descendants of siblings (nieces/nephews).
        """
        children = await self.client.db.get_children(parent.user_id)
        for c in children:
            cid = c["user_id"]
            if cid not in self.members:
                await self._create_member(cid, sibling_level)
                # XXX: Build descendants of siblings too (limited depth)
                c_member = self.members.get(cid)
                if c_member:
                    await self._build_descendants(c_member, 2)

    async def _build_descendants(self, member: FamilyMember, remaining: int):
        """
        Build descendants of a member.

        # XXX: Recursively goes down the tree, getting children of each member.
        """
        if remaining <= 0:
            return

        children = await self.client.db.get_children(member.user_id)
        child_ids = []

        for c in children:
            cid = c["user_id"]
            if cid in self.members:
                child_ids.append(cid)
                continue

            c_member = await self._create_member(cid, member.level + 1)
            if c_member:
                child_ids.append(cid)
                await self._build_descendants(c_member, remaining - 1)

        # XXX: Store union info for single-parent families too
        if child_ids:
            self.unions.append({
                "parents": [member.user_id],
                "children": child_ids,
            })

    async def _get_all_children_of_parents(
        self, parent_ids: List[int], child_level: int
    ) -> List[int]:
        """
        Get all children of given parents.

        # XXX: Used to find all siblings when building union data.
        """
        children_set = set()
        for pid in parent_ids:
            children = await self.client.db.get_children(pid)
            for c in children:
                cid = c["user_id"]
                if cid in self.members:
                    children_set.add(cid)
        return list(children_set)

    async def _load_marriages(self):
        """
        Load spouse relationships.

        # XXX: Adds spouses to the tree if not already present.
        # XXX: Records marriage pairs for pink heart edges.
        # XXX: Also fetches children of spouses (shared children from marriage).
        """
        for uid in list(self.members.keys()):
            spouses = await self.client.db.get_spouses(uid)
            for s in spouses:
                sid = s["user_id"]
                # XXX: Add spouse to tree at same level if not already present
                if sid not in self.members:
                    member = self.members[uid]
                    spouse_member = await self._create_member(sid, member.level)

                    # XXX: Build spouse's descendants (shared children from marriage)
                    if spouse_member:
                        await self._build_descendants(spouse_member, 3)

                # XXX: Record marriage (avoid duplicates)
                pair = tuple(sorted((uid, sid)))
                if pair not in self.marriages:
                    self.marriages.append(pair)

    async def _build_unions(self):
        """
        Consolidate and deduplicate union data.

        # XXX: Union = a parent group + their children
        # XXX: Used to create bracket-style connections via invisible union nodes.
        """
        # XXX: Deduplicate unions by parent set
        seen_parents = set()
        unique_unions = []

        for union in self.unions:
            parent_key = tuple(sorted(union["parents"]))
            if parent_key not in seen_parents:
                seen_parents.add(parent_key)
                unique_unions.append(union)

        self.unions = unique_unions


async def render_family_tree(
    client,
    center_user_id: int,
) -> Optional[bytes]:
    """
    Render family tree using PyVis with hierarchical layout.

    # XXX: Main entry point for tree rendering.
    # XXX: Creates a hierarchical graph where:
    # XXX: - Vertical position = generation (level)
    # XXX: - Marriage edges = pink with heart emoji
    # XXX: - Parent-child edges = via invisible union nodes for brackets

    Args:
        client: Bot client with db access
        center_user_id: The center user's ID

    Returns:
        PNG image bytes or None if failed
    """
    # XXX: ======== SECTION 1: BUILD TREE DATA ========
    builder = FamilyTreeBuilder(client, center_user_id)
    center = await builder.build(max_depth=4)

    if not center or not builder.members:
        # XXX: Solo user or no family - return single node
        return await _render_solo_user(client, center_user_id)

    # XXX: ======== SECTION 2: CREATE NETWORK ========
    net = Network(
        height="1200px",
        width="100%",
        bgcolor="#ADD8E6",  # Light blue background
        font_color="black",
        cdn_resources="in_line",  # Embeds JS/CSS for standalone rendering
    )

    added_nodes: Set[int] = set()

    # XXX: ======== SECTION 3: ADD MEMBER NODES ========
    # XXX: Each family member becomes a node with SVG image + name
    for uid, member in builder.members.items():
        if uid in added_nodes:
            continue
        added_nodes.add(uid)

        # XXX: Create composite SVG with photo (or initials) + name label
        svg = create_composite_svg(
            name=member.name,
            image_b64=member.image_b64,
            is_center=member.is_center,
            img_size=120,
            text_height=40,
            font_size=16,
        )

        # XXX: level parameter controls vertical position in hierarchical layout
        net.add_node(
            uid,
            label=" ",
            shape="image",
            image=svg,
            size=80,
            level=member.level,
        )

    # XXX: ======== SECTION 4: ADD MARRIAGE EDGES ========
    # XXX: Pink edges with heart emoji between spouses
    # XXX: physics=True + length=1 makes them act as magnets (stay close)
    for uid1, uid2 in builder.marriages:
        if uid1 in added_nodes and uid2 in added_nodes:
            net.add_edge(
                uid1,
                uid2,
                color="#FF69B4",  # Hot pink
                width=3,
                label="💕",
                font={
                    "size": 20,
                    "color": "#E91E63",
                    "align": "middle",
                    "strokeWidth": 0,
                },
                physics=True,
                length=1,  # Acts as magnet - keeps spouses close
            )

    # XXX: ======== SECTION 5: ADD PARENT-CHILD CONNECTIONS ========
    # XXX: Uses invisible "union nodes" for bracket-style connections
    # XXX: Structure: Parent(s) → Union Node → Child(ren)
    # XXX: This creates clean branching rather than direct P→C edges
    union_counter = 0
    for union in builder.unions:
        parents = union["parents"]
        children = union["children"]

        if not children:
            continue

        # XXX: Create invisible union node between generations
        union_id = f"union_{union_counter}"
        union_counter += 1

        # XXX: Union level is between parents and children
        parent_levels = [
            builder.members[p].level for p in parents if p in builder.members
        ]
        if parent_levels:
            union_level = max(parent_levels) + 1
        else:
            continue

        # XXX: Invisible dot node (size=0) that acts as connection hub
        net.add_node(
            union_id,
            shape="dot",
            size=0,
            label=" ",
            level=union_level,
            color="#bd3e3e",
        )

        # XXX: Connect parents to union node
        for pid in parents:
            if pid in added_nodes:
                net.add_edge(pid, union_id, color="#bd3e3e", width=2)

        # XXX: Connect union node to children
        for cid in children:
            if cid in added_nodes:
                net.add_edge(union_id, cid, color="#bd3e3e", width=2)

    # XXX: ======== SECTION 6: CONFIGURE LAYOUT ========
    # XXX: Hierarchical layout with vertical direction (Up-Down)
    # XXX: cubicBezier edges for smooth curved lines
    net.set_options("""
    {
      "layout": {
        "hierarchical": {
          "enabled": true,
          "direction": "UD",
          "sortMethod": "directed",
          "levelSeparation": 260,
          "nodeSpacing": 200,
          "treeSpacing": 220,
          "blockShifting": true,
          "edgeMinimization": true,
          "parentCentralization": false
        }
      },
      "edges": {
        "smooth": { "type": "cubicBezier", "forceDirection": "vertical", "roundness": 0.6 }
      },
      "physics": {
        "hierarchicalRepulsion": { "nodeDistance": 220, "damping": 0.09 },
        "solver": "hierarchicalRepulsion"
      }
    }
    """)

    # XXX: ======== SECTION 7: EXPORT ========
    return await export_network_to_png(
        net, width=2200, height=1600, wait_time=1000
    )


async def _render_solo_user(client, user_id: int) -> Optional[bytes]:
    """
    Render a single user with no family.

    # XXX: For users with no family connections, show just their node centered.
    """
    user = await client.db.get_user(user_id)
    if not user:
        return None

    net = Network(
        height="400px",
        width="400px",
        bgcolor="#ADD8E6",
        font_color="black",
        cdn_resources="in_line",
    )

    name = (user.get("first_name") or "You")[:15]
    image_b64 = await get_user_image_b64(client, user_id, size=120)

    svg = create_composite_svg(
        name=name,
        image_b64=image_b64,
        is_center=True,
        img_size=120,
        text_height=40,
        font_size=16,
    )

    net.add_node(user_id, label=" ", shape="image", image=svg, size=80)

    # XXX: Disable physics for single node
    net.set_options('{"physics": {"enabled": false}}')

    return await export_network_to_png(
        net, width=400, height=400, wait_time=500
    )
