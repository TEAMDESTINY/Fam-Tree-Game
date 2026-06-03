"""Family tree image renderer — curved parent lines, solid marriage lines, sibling lines with border gaps."""

import io
import unicodedata
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

import cairo
import gi
from PIL import Image

from bot.graphics import utils
from bot.graphics.utils import BORDER_RED, LIGHT_BLUE_BG

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402 # isort: skip


@dataclass
class TreeNode:
    """Represents a person in the family tree."""

    user_id: int
    name: str
    x: float = 0
    y: float = 0
    image: Optional[Image.Image] = None
    spouses: List["TreeNode"] = field(default_factory=list)
    children: List["TreeNode"] = field(default_factory=list)
    parents: List["TreeNode"] = field(default_factory=list)
    siblings: List["TreeNode"] = field(default_factory=list)
    depth: int = 0
    placed: bool = False


# --- Layout Configuration ---
NODE_WIDTH = 150
NODE_HEIGHT = 180
H_SPACING = 0
V_SPACING = 130
SPOUSE_GAP = 25
PROFILE_SIZE = 120


class FamilyTreeBuilder:
    """
    Builds the family tree data structure from database relationships.

    Traversal strategy:
      1. _climb_to_root   — walk up the ancestor spine (does not mark visited;
                            lets _sweep_down own that set so it can revisit nodes
                            and correctly build their children).
      2. _sweep_down      — sweep downward from a root, building children at
                            parent.depth + 1. Siblings emerge naturally as all
                            children of a shared parent. Owns self.visited to
                            prevent infinite loops.
      3. _load_spouses    — create/link spouse nodes at the same depth as their partner.
      4. _load_center_siblings_only — catch any siblings of the center user that
                            are stored only in the siblings table (not reachable
                            via shared-parent traversal).
      5. _fix_child_depths  — push any child that ended up at the same depth as
                            its parent one level down.
      6. _fix_spouse_depths — align each spouse to its partner's depth (always
                            moves down, never pulls ancestors up).
      7. _fix_child_depths  — cascade again after spouses may have moved.
    """

    def __init__(self, bot, db, center_user_id: int):
        self.bot = bot
        self.db = db
        self.center_id = center_user_id
        self.nodes: Dict[int, TreeNode] = {}
        self.visited = set()

    async def build(self, max_depth: int = 3) -> Optional[TreeNode]:
        """
        Build the standard tree (grandparents → center → grandchildren).

        Scope:
          - Up to 2 ancestor generations (parents + grandparents)
          - Center's siblings and their direct children
          - Center's own children and grandchildren
          - Spouses' outside children are excluded for sibling nodes at
            center depth; center's own spouses are always fully swept.
        """
        center = await self._create_node(self.center_id, depth=0)
        if not center:
            return None

        # Climb up 2 generations then sweep down from every root.
        await self._climb_to_root(center, 2)
        roots = [n for n in self.nodes.values() if not n.parents]
        for root in roots:
            await self._sweep_down(root, 2)

        # Sweep center down 2 more levels (children + grandchildren).
        await self._sweep_down(center, 2)

        await self._load_spouses()

        # Sweep children of spouses — but only for nodes deeper than center
        # so that siblings' spouses don't pull in unrelated subtrees.
        # Center's own spouses are always swept regardless of this guard.
        await self._sweep_spouses_children(
            max_depth=1, min_node_depth=center.depth + 1
        )

        await self._load_center_siblings_only()

        await self._fix_child_depths()
        await self._fix_spouse_depths()
        await self._fix_child_depths()

        return center

    async def build_full(self, max_depth: int = 3) -> Optional[TreeNode]:
        """Build the extended tree including siblings-of-siblings and their families."""
        center = await self.build(max_depth)
        if not center:
            return None
        await self._build_spouse_ancestors(max_depth)
        await self._build_extended_siblings(max_depth)
        await self._fix_child_depths()
        await self._fix_spouse_depths()
        await self._fix_child_depths()
        return center

    # ── Core traversal ────────────────────────────────────────────────────

    async def _climb_to_root(self, node: TreeNode, remaining: int):
        """
        Walk upward creating ancestor nodes up to `remaining` generations.

        Does NOT use self.visited so that _sweep_down can later visit these
        nodes and correctly build their children.
        """
        if remaining <= 0:
            return
        parents = await self.db.get_parents(node.user_id)
        for p in parents:
            pid = p["user_id"]
            if pid in self.nodes:
                p_node = self.nodes[pid]
                if p_node not in node.parents:
                    node.parents.append(p_node)
                if node not in p_node.children:
                    p_node.children.append(node)
                continue
            p_node = await self._create_node(pid, node.depth - 1)
            if p_node:
                node.parents.append(p_node)
                p_node.children.append(node)
                await self._add_spouses_for_node(p_node)
                await self._climb_to_root(p_node, remaining - 1)

    async def _sweep_down(self, node: TreeNode, remaining: int):
        """
        Recursively build children downward from `node`.

        Owns self.visited to prevent infinite loops. Siblings emerge naturally
        because all children of a shared parent are created at parent.depth + 1.
        """
        if remaining <= 0 or node.user_id in self.visited:
            return
        self.visited.add(node.user_id)

        children = await self.db.get_children(node.user_id)
        for c in children:
            cid = c["user_id"]
            if cid in self.nodes:
                c_node = self.nodes[cid]
                if c_node not in node.children:
                    node.children.append(c_node)
                if node not in c_node.parents:
                    c_node.parents.append(node)
            else:
                c_node = await self._create_node(cid, node.depth + 1)
                if c_node:
                    node.children.append(c_node)
                    c_node.parents.append(node)
                    await self._add_spouses_for_node(c_node)

            if cid in self.nodes:
                await self._sweep_down(self.nodes[cid], remaining - 1)

    # ── Extended build (build_full only) ──────────────────────────────────

    async def _build_spouse_ancestors(self, max_depth: int):
        """Climb ancestors of all in-tree spouses, then sweep down from new roots."""
        spouse_nodes = {
            spouse.user_id: self.nodes[spouse.user_id]
            for node in self.nodes.values()
            for spouse in node.spouses
            if spouse.user_id in self.nodes
        }
        for spouse_node in spouse_nodes.values():
            await self._climb_to_root(spouse_node, max_depth)

        new_roots = [
            n
            for n in self.nodes.values()
            if not n.parents and n.user_id not in self.visited
        ]
        for root in new_roots:
            await self._sweep_down(root, max_depth * 2)

        await self._load_spouses()
        await self._sweep_spouses_children(max_depth=3)
        await self._load_direct_siblings()

    async def _build_extended_siblings(self, max_depth: int):
        """Add siblings-of-siblings and their full subtrees."""
        for sib_node in [n for n in self.nodes.values() if n.siblings]:
            for sibling in sib_node.siblings:
                for os in await self.db.get_siblings(sibling.user_id):
                    osid = os["user_id"]
                    if osid not in self.nodes:
                        os_node = await self._create_node(osid, sibling.depth)
                        if os_node:
                            for pair in [
                                (sibling, os_node),
                                (sib_node, os_node),
                            ]:
                                a, b = pair
                                if b not in a.siblings:
                                    a.siblings.append(b)
                                if a not in b.siblings:
                                    b.siblings.append(a)
                            await self._climb_to_root(os_node, max_depth)
                            await self._sweep_down(os_node, max_depth * 2)
                            await self._load_spouses()
                            await self._sweep_spouses_children(max_depth=3)

        await self._load_direct_siblings()

    # ── Node creation ─────────────────────────────────────────────────────

    async def _create_node(
        self, user_id: int, depth: int
    ) -> Optional[TreeNode]:
        """Return existing node or create a new one for the given user."""
        if user_id in self.nodes:
            return self.nodes[user_id]
        user = await self.db.get_user(user_id)
        if not user:
            return None
        name = user["first_name"] or "Unknown"
        if len(name) > 12:
            name = name[:11] + "…"
        node = TreeNode(user_id=user_id, name=name, depth=depth)
        self.nodes[user_id] = node
        return node

    # ── Relationship loading ───────────────────────────────────────────────

    async def _load_spouses(self):
        """Create and link spouse nodes at the same depth as their partner."""
        to_create = set()
        for uid, node in list(self.nodes.items()):
            for s in await self.db.get_spouses(uid):
                if s["user_id"] not in self.nodes:
                    to_create.add((s["user_id"], node.depth))

        for sid, depth in to_create:
            await self._create_node(sid, depth)

        for uid, node in self.nodes.items():
            for s in await self.db.get_spouses(uid):
                sid = s["user_id"]
                if sid in self.nodes:
                    s_node = self.nodes[sid]
                    if s_node not in node.spouses:
                        node.spouses.append(s_node)

    async def _add_spouses_for_node(self, node: TreeNode):
        """Immediately link (and create if missing) spouses of a node at the same depth."""
        for s in await self.db.get_spouses(node.user_id):
            sid = s["user_id"]
            if sid not in self.nodes:
                s_node = await self._create_node(sid, node.depth)
                if s_node:
                    node.spouses.append(s_node)
                    s_node.spouses.append(node)
            else:
                s_node = self.nodes[sid]
                if s_node not in node.spouses:
                    node.spouses.append(s_node)
                if node not in s_node.spouses:
                    s_node.spouses.append(node)

    async def _sweep_spouses_children(
        self, max_depth: int = 1, min_node_depth: int = None
    ):
        """
        Sweep children of unvisited spouse nodes.

        min_node_depth: when set, only sweep spouses of nodes at or below this
        depth. Center's own spouses are always swept regardless, so that
        children they adopted outside the main traversal are never hidden.
        """
        center_node = self.nodes.get(self.center_id)
        spouse_nodes = []
        seen = set()

        for node in self.nodes.values():
            is_center = node.user_id == self.center_id
            depth_ok = min_node_depth is None or node.depth >= min_node_depth
            if not (is_center or depth_ok):
                continue
            for spouse in node.spouses:
                if (
                    spouse.user_id not in self.visited
                    and spouse.user_id not in seen
                ):
                    spouse_nodes.append(spouse)
                    seen.add(spouse.user_id)

        for spouse in spouse_nodes:
            await self._sweep_down(spouse, max_depth)

    async def _load_direct_siblings(self):
        """
        Load siblings from the siblings table for all nodes.

        For newly added siblings:
          - Sweeps down 2 levels so their children appear.
          - Only links parents already in the tree (never creates new parent
            nodes) so depth is anchored correctly by _fix_child_depths.
          - Loads spouses at the same depth.
        """
        to_create = set()
        for uid, node in list(self.nodes.items()):
            for s in await self.db.get_siblings(uid):
                if s["user_id"] not in self.nodes:
                    to_create.add((s["user_id"], node.depth))

        new_ids = []
        for sid, depth in to_create:
            await self._create_node(sid, depth)
            new_ids.append(sid)

        for sid in new_ids:
            if sid not in self.nodes:
                continue
            node = self.nodes[sid]
            await self._sweep_down(node, 2)

            for p in await self.db.get_parents(sid):
                pid = p["user_id"]
                if pid in self.nodes:
                    p_node = self.nodes[pid]
                    if p_node not in node.parents:
                        node.parents.append(p_node)
                    if node not in p_node.children:
                        p_node.children.append(node)

            for s in await self.db.get_spouses(sid):
                spouse_id = s["user_id"]
                if spouse_id not in self.nodes:
                    await self._create_node(spouse_id, node.depth)
                if spouse_id in self.nodes:
                    spouse_node = self.nodes[spouse_id]
                    if spouse_node not in node.spouses:
                        node.spouses.append(spouse_node)
                    if node not in spouse_node.spouses:
                        spouse_node.spouses.append(node)

        # Bidirectional sibling links for all nodes.
        for uid, node in self.nodes.items():
            for s in await self.db.get_siblings(uid):
                sid = s["user_id"]
                if sid in self.nodes:
                    s_node = self.nodes[sid]
                    if s_node not in node.siblings:
                        node.siblings.append(s_node)
                    if node not in s_node.siblings:
                        s_node.siblings.append(node)

    async def _load_center_siblings_only(self):
        """
        Load siblings of the center user from the siblings table.

        Handles siblings that are not reachable via shared-parent traversal
        (stored only in the siblings table). Sweeps 1 level down so their
        direct children are included. Only links existing parents.
        """
        center_node = self.nodes.get(self.center_id)
        if not center_node:
            return

        center_siblings = await self.db.get_siblings(self.center_id)
        to_create = {
            s["user_id"]
            for s in center_siblings
            if s["user_id"] not in self.nodes
        }

        new_ids = []
        for sid in to_create:
            await self._create_node(sid, center_node.depth)
            new_ids.append(sid)

        for sid in new_ids:
            if sid not in self.nodes:
                continue
            node = self.nodes[sid]
            await self._sweep_down(node, 1)

            for p in await self.db.get_parents(sid):
                pid = p["user_id"]
                if pid in self.nodes:
                    p_node = self.nodes[pid]
                    if p_node not in node.parents:
                        node.parents.append(p_node)
                    if node not in p_node.children:
                        p_node.children.append(node)

            for s in await self.db.get_spouses(sid):
                spouse_id = s["user_id"]
                if spouse_id not in self.nodes:
                    await self._create_node(spouse_id, node.depth)
                if spouse_id in self.nodes:
                    spouse_node = self.nodes[spouse_id]
                    if spouse_node not in node.spouses:
                        node.spouses.append(spouse_node)
                    if node not in spouse_node.spouses:
                        spouse_node.spouses.append(node)

        for s in center_siblings:
            sid = s["user_id"]
            if sid in self.nodes:
                s_node = self.nodes[sid]
                if s_node not in center_node.siblings:
                    center_node.siblings.append(s_node)
                if center_node not in s_node.siblings:
                    s_node.siblings.append(center_node)

    # ── Depth correction ──────────────────────────────────────────────────

    async def _fix_child_depths(self):
        """
        Ensure every child is strictly one level below its parent.

        Loops until stable so multi-hop misplacements cascade correctly.
        Must be called before _fix_spouse_depths (so misplaced children
        are pushed down first) and again after (to cascade any children
        of spouses that moved).
        """
        changed = True
        while changed:
            changed = False
            for node in self.nodes.values():
                for child in node.children:
                    if child.depth <= node.depth:
                        child.depth = node.depth + 1
                        changed = True

    async def _fix_spouse_depths(self):
        """
        Align each spouse to its partner's generation.

        Uses MAX depth across all partners so a spouse is always moved
        down to match the deepest partner — never pulled up, which would
        incorrectly promote ancestors to a younger generation.
        """
        targets: Dict[int, List[int]] = {}
        for node in self.nodes.values():
            for spouse in node.spouses:
                targets.setdefault(spouse.user_id, []).append(node.depth)

        for spouse_id, depths in targets.items():
            if spouse_id in self.nodes:
                self.nodes[spouse_id].depth = max(depths)

    # ── Debug ─────────────────────────────────────────────────────────────

    def print_debug(self, label: str = ""):
        """Print a summary of every node in the tree (for debugging)."""
        print(f"\n=== {label} | {len(self.nodes)} nodes ===")
        for node in self.nodes.values():
            print(
                f"  {node.name}: depth={node.depth}"
                f", parents={[p.name for p in node.parents]}"
                f", spouses={[s.name for s in node.spouses]}"
                f", siblings={[s.name for s in node.siblings]}"
                f", children={[c.name for c in node.children]}"
            )


def layout_tree(
    center: TreeNode, nodes: Dict[int, TreeNode]
) -> Tuple[int, int]:
    """
    Reingold–Tilford-style layout.

    Couples are treated as a single unit. Each unit's allocated horizontal
    slot is the max of its own width and its descendants' combined width.
    Children are centered under their parent unit, eliminating the diamond
    effect that appeared when a row with few units was centered on the canvas.
    """
    depth_groups: Dict[int, List[TreeNode]] = {}
    for node in nodes.values():
        depth_groups.setdefault(node.depth, []).append(node)

    if not depth_groups:
        return 800, 600

    min_depth, max_depth = min(depth_groups.keys()), max(depth_groups.keys())

    TOP_PADDING = PROFILE_SIZE // 2 + 20
    for depth, group in depth_groups.items():
        y = TOP_PADDING + (depth - min_depth) * (NODE_HEIGHT + V_SPACING)
        for node in group:
            node.y = y

    # Build units (couples or singletons) per depth level.
    depth_units: Dict[int, List[List[TreeNode]]] = {}
    node_to_unit: Dict[int, Tuple[int, int]] = {}
    for depth in sorted(depth_groups.keys()):
        group = depth_groups[depth]
        units: List[List[TreeNode]] = []
        processed: set = set()
        for node in group:
            if node.user_id in processed:
                continue
            partners = [s for s in node.spouses if s.depth == depth]
            if partners and partners[0].user_id not in processed:
                spouse = partners[0]
                idx = len(units)
                units.append([node, spouse])
                processed.update({node.user_id, spouse.user_id})
                node_to_unit[node.user_id] = (depth, idx)
                node_to_unit[spouse.user_id] = (depth, idx)
            else:
                idx = len(units)
                units.append([node])
                processed.add(node.user_id)
                node_to_unit[node.user_id] = (depth, idx)
        depth_units[depth] = units

    def unit_width(d: int, i: int) -> int:
        return (
            NODE_WIDTH * 2 + SPOUSE_GAP
            if len(depth_units[d][i]) == 2
            else NODE_WIDTH
        )

    # Map each non-root unit to its primary parent unit (first parent found
    # at depth - 1). Multi-parent children follow one parent for layout;
    # the other still gets a connector line during rendering.
    parent_unit_of: Dict[Tuple[int, int], Optional[Tuple[int, int]]] = {}
    children_of: Dict[Tuple[int, int], List[Tuple[int, int]]] = {}
    for depth in sorted(depth_groups.keys()):
        if depth == min_depth:
            continue
        for idx, unit in enumerate(depth_units[depth]):
            primary: Optional[Tuple[int, int]] = None
            for n in unit:
                for p in n.parents:
                    if p.depth == depth - 1:
                        primary = node_to_unit.get(p.user_id)
                        if primary is not None:
                            break
                if primary is not None:
                    break
            parent_unit_of[(depth, idx)] = primary
            if primary is not None:
                children_of.setdefault(primary, []).append((depth, idx))

    subtree_width: Dict[Tuple[int, int], int] = {}
    unit_offset: Dict[Tuple[int, int], float] = {}
    kids_layout: Dict[Tuple[int, int], List[Tuple[float, float]]] = {}
    kids_offset_in_slot: Dict[Tuple[int, int], float] = {}

    def _layout_kids(
        kids: List[Tuple[int, int]],
    ) -> Tuple[List[Tuple[float, float]], float]:
        """
        Return a (slot_left, slot_width) pair per child plus the total extent.

        Leading and trailing leaf siblings are compressed: their slot_left is
        placed adjacent to the nearest wide sibling's actual unit edge rather
        than after the full subtree slot, so small leaves never drift far from
        their wide neighbour.
        """
        if not kids:
            return [], 0.0

        child_st = [subtree_width[c] for c in kids]
        child_uw = [unit_width(*c) for c in kids]
        is_leaf = [child_st[j] == child_uw[j] for j in range(len(kids))]

        first_wide = next((j for j in range(len(kids)) if not is_leaf[j]), None)
        last_wide = next(
            (j for j in range(len(kids) - 1, -1, -1) if not is_leaf[j]), None
        )

        positions: List[Tuple[float, float]] = [(0.0, 0.0)] * len(kids)

        if first_wide is None:
            cursor = 0.0
            for j in range(len(kids)):
                positions[j] = (cursor, child_uw[j])
                cursor += child_uw[j] + H_SPACING
            return positions, max(0.0, cursor - H_SPACING)

        cursor = 0.0
        for j in range(first_wide, last_wide + 1):
            positions[j] = (cursor, child_st[j])
            cursor += child_st[j] + H_SPACING

        fw_pos = positions[first_wide][0]
        fw_unit_left = fw_pos + unit_offset[kids[first_wide]]
        boundary = fw_unit_left
        for j in range(first_wide - 1, -1, -1):
            unit_left = boundary - H_SPACING - child_uw[j]
            positions[j] = (unit_left, child_uw[j])
            boundary = unit_left

        lw_pos = positions[last_wide][0]
        lw_unit_right = (
            lw_pos + unit_offset[kids[last_wide]] + child_uw[last_wide]
        )
        boundary = lw_unit_right
        for j in range(last_wide + 1, len(kids)):
            unit_left = boundary + H_SPACING
            positions[j] = (unit_left, child_uw[j])
            boundary = unit_left + child_uw[j]

        leftmost = min(p[0] for p in positions)
        rightmost = max(
            p[0]
            + (child_st[j] if first_wide <= j <= last_wide else child_uw[j])
            for j, p in enumerate(positions)
        )
        if leftmost != 0.0:
            positions = [(p[0] - leftmost, p[1]) for p in positions]
            rightmost -= leftmost
        return positions, rightmost

    def compute(d: int, i: int) -> None:
        key = (d, i)
        if key in subtree_width:
            return
        own = unit_width(d, i)
        kids = children_of.get(key, [])

        if not kids:
            subtree_width[key] = own
            unit_offset[key] = 0.0
            kids_layout[key] = []
            kids_offset_in_slot[key] = 0.0
            return

        for c in kids:
            compute(*c)

        # Group children by which spouse adopted them (left-only, shared,
        # right-only), then interleave wide-subtree and leaf children within
        # each group so leaves consume the visual gap between wide siblings.
        parent_unit = depth_units[d][i]
        is_couple = len(parent_unit) == 2
        left_id = parent_unit[0].user_id if is_couple else None
        right_id = parent_unit[1].user_id if is_couple else None

        def _side(c: Tuple[int, int]) -> str:
            if not is_couple:
                return "shared"
            parent_ids = {
                p.user_id for cn in depth_units[c[0]][c[1]] for p in cn.parents
            }
            in_left = left_id in parent_ids
            in_right = right_id in parent_ids
            if in_left and in_right:
                return "shared"
            return "left" if in_left else ("right" if in_right else "shared")

        groups: Dict[str, List[Tuple[int, int]]] = {
            "left": [],
            "shared": [],
            "right": [],
        }
        for c in kids:
            groups[_side(c)].append(c)

        def _interleave(group: List[Tuple[int, int]]) -> List[Tuple[int, int]]:
            wides = [c for c in group if subtree_width[c] > unit_width(*c)]
            leaves = [c for c in group if subtree_width[c] == unit_width(*c)]
            if not wides or not leaves:
                return list(group)
            out: List[Tuple[int, int]] = []
            for n, w in enumerate(wides):
                out.append(w)
                if n < len(wides) - 1 and n < len(leaves):
                    out.append(leaves[n])
            out.extend(leaves[len(wides) - 1 :])
            return out

        kids = (
            _interleave(groups["left"])
            + _interleave(groups["shared"])
            + _interleave(groups["right"])
        )
        children_of[key] = kids

        layout, extent = _layout_kids(kids)
        st = max(own, extent)
        ko = (st - extent) / 2

        child_centers = [
            ko + pos + unit_offset[c] + unit_width(*c) / 2
            for c, (pos, _) in zip(kids, layout)
        ]
        cluster_center = (min(child_centers) + max(child_centers)) / 2
        my_unit_left = max(0.0, min(st - own, cluster_center - own / 2))

        subtree_width[key] = int(st)
        unit_offset[key] = my_unit_left
        kids_layout[key] = layout
        kids_offset_in_slot[key] = ko

    for depth in sorted(depth_groups.keys()):
        for idx in range(len(depth_units[depth])):
            compute(depth, idx)

    SIDE_PADDING = 60

    def place_at_left(unit: List[TreeNode], left: float) -> None:
        if len(unit) == 2:
            unit[0].x = left + NODE_WIDTH / 2
            unit[1].x = left + NODE_WIDTH + SPOUSE_GAP + NODE_WIDTH / 2
        else:
            unit[0].x = left + NODE_WIDTH / 2

    placed: set = set()

    def place_subtree(d: int, i: int, subtree_left: float) -> None:
        key = (d, i)
        place_at_left(depth_units[d][i], subtree_left + unit_offset[key])
        placed.add(key)
        ko = kids_offset_in_slot[key]
        for c, (pos, _) in zip(children_of.get(key, []), kids_layout[key]):
            place_subtree(c[0], c[1], subtree_left + ko + pos)

    top_units = list(range(len(depth_units.get(min_depth, []))))
    total_top = (
        sum(subtree_width[(min_depth, i)] for i in top_units)
        + max(0, len(top_units) - 1) * H_SPACING
    )

    cursor = SIDE_PADDING
    rightmost = SIDE_PADDING
    for i in top_units:
        place_subtree(min_depth, i, cursor)
        cursor += subtree_width[(min_depth, i)] + H_SPACING
        rightmost = cursor - H_SPACING

    # Orphan units (spouses married in from outside with no primary parent
    # unit at depth-1) are appended after the last placed unit.
    for depth in sorted(depth_groups.keys()):
        if depth == min_depth:
            continue
        for idx in range(len(depth_units[depth])):
            if (depth, idx) in placed:
                continue
            place_subtree(depth, idx, rightmost + H_SPACING)
            rightmost += subtree_width[(depth, idx)] + H_SPACING

    canvas_width = int(max(rightmost, total_top + SIDE_PADDING) + SIDE_PADDING)
    num_generations = max_depth - min_depth + 1
    content_height = num_generations * (NODE_HEIGHT + V_SPACING) - V_SPACING
    BOTTOM_PADDING = PROFILE_SIZE // 2 + 50
    total_height = TOP_PADDING + content_height + BOTTOM_PADDING

    return canvas_width, int(total_height)


# ── Cairo rendering helpers ──────────────────────────────────────────────


def _rgb(r, g, b):
    return (r / 255.0, g / 255.0, b / 255.0)


def _draw_text(ctx, text, x, y, font_desc_str, color):
    normalized = unicodedata.normalize("NFC", text)
    layout = PangoCairo.create_layout(ctx)
    layout.set_text(normalized, -1)
    layout.set_font_description(Pango.FontDescription(font_desc_str))
    ctx.save()
    ctx.set_source_rgb(*_rgb(*color))
    ctx.move_to(x, y)
    PangoCairo.update_layout(ctx, layout)
    PangoCairo.show_layout(ctx, layout)
    ctx.restore()
    ink_rect, _ = layout.get_pixel_extents()
    return ink_rect.height


def _draw_curved_line(ctx, x1, y1, x2, y2, color, width=3):
    cy1 = y1 + (y2 - y1) * 0.45
    cy2 = y2 - (y2 - y1) * 0.45
    ctx.save()
    ctx.set_source_rgb(*_rgb(*color))
    ctx.set_line_width(width)
    ctx.move_to(x1, y1)
    ctx.curve_to(x1, cy1, x2, cy2, x2, y2)
    ctx.stroke()
    ctx.restore()


def _draw_line(ctx, x1, y1, x2, y2, color, width=2):
    ctx.save()
    ctx.set_source_rgb(*_rgb(*color))
    ctx.set_line_width(width)
    ctx.move_to(x1, y1)
    ctx.line_to(x2, y2)
    ctx.stroke()
    ctx.restore()


def _pil_to_cairo_surface(pil_img):
    """Convert a PIL RGBA image to a Cairo ARGB32 ImageSurface.

    PIL stores pixels as R-G-B-A; Cairo's ARGB32 on little-endian systems
    expects them in B-G-R-A memory order.
    """
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    raw = pil_img.tobytes("raw", "RGBA")
    data = bytearray(len(raw))
    for i in range(0, len(raw), 4):
        data[i] = raw[i + 2]  # B
        data[i + 1] = raw[i + 1]  # G
        data[i + 2] = raw[i]  # R
        data[i + 3] = raw[i + 3]  # A
    surface = cairo.ImageSurface.create_for_data(
        data, cairo.FORMAT_ARGB32, pil_img.width, pil_img.height
    )
    return surface, data


def _render_tree_content(ctx, builder, center_user_id, width, height):
    """Draw the complete family tree onto an existing Cairo context."""
    TEXT_HEIGHT = 28
    TEXT_GAP = 8

    ctx.set_source_rgb(*_rgb(*LIGHT_BLUE_BG))
    ctx.paint()

    for node in builder.nodes.values():
        node.image = (
            utils.square_crop(node.image, PROFILE_SIZE) if node.image else None
        )
        if node.image:
            node._cairo_surface, node._cairo_data = _pil_to_cairo_surface(
                node.image
            )
        else:
            node._cairo_surface = None
            node._cairo_data = None

    # ── Marriage lines ────────────────────────────────────────────────────
    PINK = (255, 105, 180)
    for node in builder.nodes.values():
        for spouse in node.spouses:
            if node.user_id < spouse.user_id and abs(node.y - spouse.y) < 50:
                if node.x < spouse.x:
                    start_x = node.x + PROFILE_SIZE // 2 + 5
                    end_x = spouse.x - PROFILE_SIZE // 2 - 5
                else:
                    start_x = node.x - PROFILE_SIZE // 2 - 5
                    end_x = spouse.x + PROFILE_SIZE // 2 + 5
                y = node.y
                _draw_line(ctx, start_x, y, end_x, y, PINK, 3)
                mid_x = (start_x + end_x) / 2
                _draw_text(
                    ctx,
                    "💘",
                    int(mid_x - 15),
                    int(y - 15),
                    "Noto Color Emoji 20",
                    (255, 255, 255),
                )

    # ── Sibling lines ─────────────────────────────────────────────────────
    # One horizontal line connects all siblings at the same depth, with gaps
    # where parent-child connector lines cross to avoid visual ambiguity.
    SIBLING_COLOR = (100, 149, 237)

    sibling_nodes_by_depth: dict[int, set] = {}
    drawn_pairs: set[tuple[int, int]] = set()
    for node in builder.nodes.values():
        for sibling in node.siblings:
            if abs(node.depth - sibling.depth) != 0:
                continue
            pair = tuple(sorted([node.user_id, sibling.user_id]))
            if pair not in drawn_pairs:
                drawn_pairs.add(pair)
                sibling_nodes_by_depth.setdefault(node.depth, set()).add(
                    node.user_id
                )
                sibling_nodes_by_depth[node.depth].add(sibling.user_id)

    parent_child_x_at_y: dict[float, set[float]] = {}
    for node in builder.nodes.values():
        if node.parents:
            y = node.y - PROFILE_SIZE // 2 - 5
            parent_child_x_at_y.setdefault(y, set()).add(node.x)
            for p in node.parents:
                parent_child_x_at_y.setdefault(y, set()).add(p.x)

    for depth, uids in sibling_nodes_by_depth.items():
        if len(uids) < 2:
            continue
        sib_nodes = sorted(
            [builder.nodes[uid] for uid in uids if uid in builder.nodes],
            key=lambda n: n.x,
        )
        if len(sib_nodes) < 2:
            continue

        y = sib_nodes[0].y - (PROFILE_SIZE // 2 + 10)
        x_left, x_right = sib_nodes[0].x, sib_nodes[-1].x
        crossing = sorted(
            sx
            for py, px_set in parent_child_x_at_y.items()
            if abs(py - y) < 25
            for sx in px_set
            if x_left < sx < x_right
        )

        if crossing:
            prev = x_left
            for cx in crossing:
                if cx - prev > 5:
                    _draw_line(ctx, prev, y, cx - 3, y, SIBLING_COLOR, 2)
                prev = cx + 3
            if x_right - prev > 5:
                _draw_line(ctx, prev, y, x_right, y, SIBLING_COLOR, 2)
        else:
            _draw_line(ctx, x_left, y, x_right, y, SIBLING_COLOR, 2)

        for n in sib_nodes:
            _draw_line(
                ctx, n.x, y, n.x, n.y - PROFILE_SIZE // 2, SIBLING_COLOR, 2
            )

    # ── Parent-child lines ────────────────────────────────────────────────
    # Colour encodes adoption side within a couple:
    #   A_SIDE  — child belongs to the left partner only
    #   B_SIDE  — child belongs to the right partner only
    #   SHARED  — child has both partners as parents
    #   FALLBACK — single parent with no in-tree spouse
    A_SIDE = (215, 70, 70)
    B_SIDE = (60, 110, 215)
    SHARED = (155, 75, 200)
    FALLBACK = (205, 92, 92)

    for node in builder.nodes.values():
        if not node.parents:
            continue
        end_x = node.x
        end_y = node.y - PROFILE_SIZE // 2 - 5

        if len(node.parents) == 1:
            parent = node.parents[0]
            color = FALLBACK
            spouse_in_tree = next(
                (
                    s
                    for s in parent.spouses
                    if s.user_id in builder.nodes
                    and abs(s.depth - parent.depth) == 0
                ),
                None,
            )
            if spouse_in_tree is not None:
                color = A_SIDE if parent.x <= spouse_in_tree.x else B_SIDE
            start_y = parent.y + PROFILE_SIZE // 2 + TEXT_GAP + TEXT_HEIGHT + 5
            _draw_curved_line(ctx, parent.x, start_y, end_x, end_y, color, 3)
        else:
            p1, p2 = node.parents[0], node.parents[1]
            p1_start_y = p1.y + PROFILE_SIZE // 2 + TEXT_GAP + TEXT_HEIGHT + 5
            p2_start_y = p2.y + PROFILE_SIZE // 2 + TEXT_GAP + TEXT_HEIGHT + 5
            mid_x = (p1.x + p2.x) / 2
            mid_y = p1_start_y + 15
            _draw_line(ctx, p1.x, mid_y, p2.x, mid_y, SHARED, 3)
            _draw_curved_line(ctx, p1.x, p1_start_y, p1.x, mid_y, SHARED, 3)
            _draw_curved_line(ctx, p2.x, p2_start_y, p2.x, mid_y, SHARED, 3)
            _draw_curved_line(ctx, mid_x, mid_y, end_x, end_y, SHARED, 3)

    # ── Profile images and borders ────────────────────────────────────────
    for node in builder.nodes.values():
        img_left = int(node.x - PROFILE_SIZE // 2)
        img_top = int(node.y - PROFILE_SIZE // 2)

        if node.user_id == center_user_id:
            gap, thick = 8, 4
            ctx.save()
            ctx.set_source_rgb(*_rgb(*BORDER_RED))
            ctx.set_line_width(thick)
            ctx.rectangle(
                img_left - gap,
                img_top - gap,
                PROFILE_SIZE + gap * 2,
                PROFILE_SIZE + gap * 2,
            )
            ctx.stroke()
            ctx.restore()

        if node._cairo_surface:
            ctx.save()
            ctx.set_source_surface(node._cairo_surface, img_left, img_top)
            ctx.paint()
            ctx.restore()

        text_y = img_top + PROFILE_SIZE + 8
        if node.user_id == center_user_id:
            text_y += 5
        _draw_text(
            ctx,
            node.name,
            int(node.x - 60),
            int(text_y),
            "Noto Sans 22",
            (0, 0, 0),
        )


async def render_family_tree(bot, db, center_user_id: int) -> Optional[bytes]:
    """
    Render the standard family tree image for a user.

    Returns PNG bytes, or None if the user is not found.
    """
    builder = FamilyTreeBuilder(bot, db, center_user_id)
    center = await builder.build()
    if not center:
        return None

    width, height = layout_tree(center, builder.nodes)
    for node in builder.nodes.values():
        node.image = await utils.get_profile_image(
            bot, db, node.user_id, PROFILE_SIZE
        )

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)
    _render_tree_content(ctx, builder, center_user_id, width, height)

    buffer = io.BytesIO()
    surface.write_to_png(buffer)
    buffer.seek(0)
    return buffer.getvalue()


async def render_conflict_path(
    bot, db, conflict_path: list[tuple[int, str]]
) -> Optional[bytes]:
    """
    Render a family-tree image showing only the nodes on a conflict path.

    Builds TreeNode objects with the correct edges from path edge labels,
    assigns generation depths by traversing those labels, then reuses
    layout_tree + _render_tree_content so all line styles match the normal tree.

    Edge labels:
      "👶" — previous node is parent of current (descending)
      "👤" — previous node is child of current (ascending)
      "💑" — spouses (same depth)
      "👫" — siblings (same depth)
    """
    if not conflict_path or len(conflict_path) < 2:
        return None

    nodes: Dict[int, TreeNode] = {}
    for uid, _ in conflict_path:
        user = await db.get_user(uid)
        name = (user.get("first_name") if user else None) or str(uid)
        nodes[uid] = TreeNode(user_id=uid, name=name[:20])

    depth_map: Dict[int, int] = {conflict_path[0][0]: 0}
    current_depth = 0
    for i in range(1, len(conflict_path)):
        _, label = conflict_path[i]
        if label == "👶":
            current_depth += 1
        elif label == "👤":
            current_depth -= 1
        depth_map[conflict_path[i][0]] = current_depth

    min_d = min(depth_map.values())
    for uid, d in depth_map.items():
        nodes[uid].depth = d - min_d

    for i in range(1, len(conflict_path)):
        prev_id, _ = conflict_path[i - 1]
        curr_id, label = conflict_path[i]
        p, c = nodes[prev_id], nodes[curr_id]

        if label == "👶":
            if c not in p.children:
                p.children.append(c)
            if p not in c.parents:
                c.parents.append(p)
        elif label == "👤":
            if p not in c.children:
                c.children.append(p)
            if c not in p.parents:
                p.parents.append(c)
        elif label == "💑":
            if c not in p.spouses:
                p.spouses.append(c)
            if p not in c.spouses:
                c.spouses.append(p)
        elif label == "👫":
            if c not in p.siblings:
                p.siblings.append(c)
            if p not in c.siblings:
                c.siblings.append(p)

    center_id = conflict_path[0][0]

    class _Builder:
        pass

    builder = _Builder()
    builder.nodes = nodes

    width, height = layout_tree(nodes[center_id], nodes)
    for node in nodes.values():
        node.image = await utils.get_profile_image(
            bot, db, node.user_id, PROFILE_SIZE
        )

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)
    _render_tree_content(ctx, builder, center_id, width, height)

    buffer = io.BytesIO()
    surface.write_to_png(buffer)
    buffer.seek(0)
    return buffer.getvalue()


async def render_full_family_tree(
    bot, db, center_user_id: int
) -> Optional[bytes]:
    """
    Render the extended family tree including siblings-of-siblings.

    Returns PNG bytes, or None if the user is not found.
    """
    builder = FamilyTreeBuilder(bot, db, center_user_id)
    center = await builder.build_full()
    if not center:
        return None

    width, height = layout_tree(center, builder.nodes)
    for node in builder.nodes.values():
        node.image = await utils.get_profile_image(
            bot, db, node.user_id, PROFILE_SIZE
        )

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, width, height)
    ctx = cairo.Context(surface)
    _render_tree_content(ctx, builder, center_user_id, width, height)

    buffer = io.BytesIO()
    surface.write_to_png(buffer)
    buffer.seek(0)
    return buffer.getvalue()
