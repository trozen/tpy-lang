"""
Type dependency cycle detection.

Detects mutual recursion between records and type aliases by building
a directed type-reference graph and finding cycles via DFS. Validates
that every cycle is broken by at least one indirecting container
(Box, list, dict, set, Optional, Ptr, Own).
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .typesys import TpyType, UnionType


# ---- Indirection helpers ------------------------------------------------

def _is_indirecting_type(typ: TpyType) -> bool:
    """Check if a type provides pointer indirection (heap-allocated, incomplete OK)."""
    from .typesys import NominalType
    from .type_def_registry import is_set, is_dict
    type_name = type(typ).__name__
    if type_name in ('OptionalType', 'PtrType'):
        return True
    if is_set(typ) or is_dict(typ):
        return True
    if isinstance(typ, NominalType) and typ.name in ("Box", "list"):
        return True
    return False


def _collect_type_refs(
    typ: TpyType,
    target_names: frozenset[str],
    expanded_aliases: dict[frozenset[str], str] | None = None,
) -> list[tuple[str, bool]]:
    """Walk a type tree and collect references to any of `target_names`.

    Also detects expanded union types that match a known alias (the parser
    expands `Box[Expr]` to `Box[UnionType(Lit, BinOp)]`, so we match the
    expanded UnionType back to the alias by comparing member name sets).

    Returns list of (referenced_name, has_indirection) pairs.
    """
    results: list[tuple[str, bool]] = []
    _walk(typ, target_names, False, results, expanded_aliases)
    return results


def _walk(
    typ: TpyType,
    target_names: frozenset[str],
    inside_indirection: bool,
    out: list[tuple[str, bool]],
    expanded_aliases: dict[frozenset, str] | None = None,
) -> None:
    from .typesys import NominalType, UnionType
    if isinstance(typ, NominalType) and typ.name in target_names and not typ.is_protocol:
        out.append((typ.name, inside_indirection))
        return
    # Detect expanded union alias: Box[UnionType(Lit, BinOp)] -> ref to "Expr"
    # The parser expands type aliases, so Box[Expr] becomes Box[UnionType(...)].
    # Match the expanded UnionType back to its alias by comparing member sets.
    if expanded_aliases and isinstance(typ, UnionType):
        key = frozenset(typ.members)
        alias_name = expanded_aliases.get(key)
        if alias_name is not None:
            out.append((alias_name, inside_indirection))
            return
    new_indirection = inside_indirection or _is_indirecting_type(typ)
    for inner in typ.inner_types():
        _walk(inner, target_names, new_indirection, out, expanded_aliases)


# ---- Graph + cycle detection -------------------------------------------

@dataclass
class _Edge:
    """A directed edge in the type dependency graph."""
    source: str
    target: str
    has_indirection: bool
    field_name: str | None = None


@dataclass
class TypeCycle:
    """A cycle found in the type dependency graph."""
    # Ordered list of type names in the cycle: path[0] -> path[1] -> ... -> path[0]
    path: list[str]
    # Edges parallel to path: edges[i] = path[i] -> path[(i+1) % len]
    edges: list[_Edge]
    # Union alias names participating in this cycle
    alias_names: list[str] = field(default_factory=list)

    def is_fully_unindirected(self) -> bool:
        """True if NO edge has indirection -- infinite size, can't compile."""
        return all(not e.has_indirection for e in self.edges)

    def has_any_indirection(self) -> bool:
        """True if at least one edge has indirection (cycle can be broken)."""
        return any(e.has_indirection for e in self.edges)

    def first_unindirected_edge(self) -> _Edge | None:
        for e in self.edges:
            if not e.has_indirection:
                return e
        return None


def detect_type_cycles(
    record_fields: dict[str, list[tuple[str, TpyType]]],
    union_aliases: dict[str, tuple[TpyType, ...]],
) -> list[TypeCycle]:
    """Detect cycles in the type dependency graph.

    Args:
        record_fields: record_name -> [(field_name, field_type)]
        union_aliases: alias_name -> member types (only non-recursive UnionType aliases)

    Returns:
        List of TypeCycle objects describing each cycle found.
    """
    all_names = frozenset(record_fields.keys()) | frozenset(union_aliases.keys())
    if not all_names:
        return []

    # Build expanded_aliases map: frozenset of member types -> alias name.
    # The parser expands `Box[Expr]` to `Box[UnionType(Lit, BinOp)]`, so we
    # need to match expanded UnionType instances back to their alias.
    expanded_aliases: dict[frozenset, str] = {}
    for alias_name, members in union_aliases.items():
        expanded_aliases[frozenset(members)] = alias_name

    # Build adjacency list
    adj: dict[str, list[_Edge]] = {name: [] for name in all_names}

    # Record edges: record -> types referenced in fields
    for rec_name, fields in record_fields.items():
        for field_name, field_type in fields:
            refs = _collect_type_refs(field_type, all_names, expanded_aliases)
            for ref_name, has_indirection in refs:
                if ref_name != rec_name:
                    adj[rec_name].append(_Edge(
                        source=rec_name, target=ref_name,
                        has_indirection=has_indirection,
                        field_name=field_name,
                    ))

    # Alias edges: alias -> its union member types (by value in the variant)
    for alias_name, members in union_aliases.items():
        for member in members:
            refs = _collect_type_refs(member, all_names)
            for ref_name, _has_indirection in refs:
                if ref_name != alias_name:
                    adj[alias_name].append(_Edge(
                        source=alias_name, target=ref_name,
                        has_indirection=False,
                    ))

    # DFS cycle detection with gray-node back-edge detection
    class _Color(Enum):
        WHITE = 0
        GRAY = 1
        BLACK = 2
    WHITE, GRAY, BLACK = _Color.WHITE, _Color.GRAY, _Color.BLACK
    color: dict[str, _Color] = {name: WHITE for name in all_names}
    # For each node, the edge that brought us there in the DFS tree
    parent_edge: dict[str, _Edge | None] = {name: None for name in all_names}
    cycles: list[TypeCycle] = []
    seen_cycles: set[frozenset[str]] = set()
    alias_name_set = frozenset(union_aliases.keys())

    def dfs(u: str) -> None:
        color[u] = GRAY
        for edge in adj[u]:
            v = edge.target
            if color[v] == GRAY:
                # Back-edge: cycle is v -> ... -> u -> v
                # Reconstruct by tracing parent_edge from u back to v
                path: list[str] = []
                edge_list: list[_Edge] = []
                cur = u
                while cur != v:
                    path.append(cur)
                    pe = parent_edge[cur]
                    assert pe is not None
                    edge_list.append(pe)
                    cur = pe.source
                path.append(v)
                path.reverse()
                edge_list.reverse()
                edge_list.append(edge)  # closing edge: u -> v
                # Deduplicate: multi-edge graphs (e.g. BinOp with two
                # Box[Expr] fields) can produce the same structural cycle
                key = frozenset(path)
                if key in seen_cycles:
                    return
                seen_cycles.add(key)
                cycle = TypeCycle(
                    path=path, edges=edge_list,
                    alias_names=[n for n in path if n in alias_name_set],
                )
                cycles.append(cycle)
            elif color[v] == WHITE:
                parent_edge[v] = edge
                dfs(v)
        color[u] = BLACK

    for name in sorted(all_names):
        if color[name] == WHITE:
            dfs(name)

    return cycles
