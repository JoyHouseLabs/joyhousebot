"""Build accessibility snapshot with numeric refs for act() resolution."""

from __future__ import annotations

from typing import Any

# Default max chars for AI snapshot (OpenClaw-aligned)
DEFAULT_AI_SNAPSHOT_MAX_CHARS = 80_000
DEFAULT_AI_SNAPSHOT_EFFICIENT_MAX_CHARS = 10_000


def _cdp_ax_node_to_tree(cdp_nodes: list[dict], node_id: str) -> dict[str, Any] | None:
    """Convert one CDP AX node (by nodeId) to {role, name, value, children}."""
    by_id = {n["nodeId"]: n for n in cdp_nodes}
    node = by_id.get(node_id)
    if not node:
        return None
    role_obj = node.get("role") or {}
    name_obj = node.get("name") or {}
    value_obj = node.get("value") or {}
    role = (role_obj.get("value") or "generic") if isinstance(role_obj, dict) else "generic"
    name = (name_obj.get("value") or "") if isinstance(name_obj, dict) else ""
    value = (value_obj.get("value") or "") if isinstance(value_obj, dict) else ""
    child_ids = node.get("childIds") or []
    children = []
    for cid in child_ids:
        child = _cdp_ax_node_to_tree(cdp_nodes, cid)
        if child:
            children.append(child)
    return {"role": role, "name": name, "value": value, "children": children}


def accessibility_tree_from_cdp_get_full_ax_tree(cdp_response: dict[str, Any]) -> dict[str, Any]:
    """
    Convert CDP Accessibility.getFullAXTree response to a single root node
    {role, name, value, children} for snapshot_from_accessibility().
    """
    nodes = cdp_response.get("nodes") or []
    if not nodes:
        return {"role": "generic", "name": "", "value": "", "children": []}
    parent_ids = set()
    for n in nodes:
        for cid in n.get("childIds") or []:
            parent_ids.add(cid)
    # Root is the node whose nodeId is not any other's childId (or first node)
    root_id = None
    for n in nodes:
        if n["nodeId"] not in parent_ids:
            root_id = n["nodeId"]
            break
    if root_id is None:
        root_id = nodes[0]["nodeId"]
    root = _cdp_ax_node_to_tree(nodes, root_id)
    return root or {"role": "generic", "name": "", "value": "", "children": []}


def _build_tree_with_refs(
    node: dict[str, Any],
    refs: dict[str, dict],
    lines: list[str],
    max_chars: int,
    depth: int,
    indent: str = "",
    role_name_counts: dict[tuple[str, str], int] | None = None,
) -> int:
    """Append one node to lines with [ref=N] and return total chars so far."""
    if role_name_counts is None:
        role_name_counts = {}
    role = (node.get("role") or "generic").strip() or "generic"
    name = (node.get("name") or "").strip()
    value = (node.get("value") or "").strip()
    key = (role, name)
    role_name_counts[key] = role_name_counts.get(key, 0)
    nth = role_name_counts[key]
    role_name_counts[key] += 1
    ref = str(len(refs) + 1)
    refs[ref] = {"role": role, "name": name, "value": value, "nth": nth}
    part = f"{indent}[ref={ref}] {role}"
    if name:
        part += f' "{name}"'
    if value:
        part += f' value="{value}"'
    lines.append(part)
    total = sum(len(l) + 1 for l in lines)
    if total >= max_chars:
        return total
    children = node.get("children") or []
    for child in children:
        total = _build_tree_with_refs(
            child, refs, lines, max_chars, depth + 1, indent + "  ", role_name_counts
        )
        if total >= max_chars:
            lines.append(f"{indent}  ... (truncated)")
            return total
    return total


def snapshot_from_accessibility(
    acc: dict[str, Any],
    *,
    format: str = "ai",
    max_chars: int = DEFAULT_AI_SNAPSHOT_MAX_CHARS,
) -> tuple[str, dict[str, dict]]:
    """
    Build snapshot text and refs map from Playwright accessibility snapshot.
    Returns (snapshot_text, refs_map). refs_map: ref -> {role, name, value}.
    """
    refs: dict[str, dict] = {}
    lines: list[str] = []
    _build_tree_with_refs(acc, refs, lines, max_chars, 0)
    text = "\n".join(lines)
    return text, refs
