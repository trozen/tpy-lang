"""
Type dependency cycle detection.

Detects mutual recursion between records and type aliases by building
a directed type-reference graph and finding cycles via DFS. Validates
that every cycle is broken by at least one indirecting container
(Optional, Ptr, plus types whose TypeDef carries is_indirecting=True --
list, dict, set, plus user @native records that opt in).

User TPy records (e.g. tplib.Box, tplib.Rc) provide indirection
structurally: the walker expands a non-indirecting nominal's fields
after type-parameter substitution, so a `_ptr: Ptr[T]` field signals
indirection without the compiler hard-coding the record's name.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

from .typesys import substitute_type_params_structural

if TYPE_CHECKING:
    from .typesys import NominalType, RecordInfo, TpyType, UnionType


# ---- Indirection helpers ------------------------------------------------

def _is_indirecting_type(typ: TpyType) -> bool:
    """True if the type provides pointer indirection that breaks a recursive
    size cycle. Two sources:
    - OptionalType / PtrType structural wrappers (isinstance dispatch).
    - TypeDef.is_indirecting (declared via `@native(..., indirecting=True)`;
      covers list/dict/set + user opt-ins).
    """
    from .typesys import OptionalType, PtrType
    from .type_def_registry import type_def_of
    if isinstance(typ, (OptionalType, PtrType)):
        return True
    td = type_def_of(typ)
    return td is not None and td.is_indirecting


def _collect_type_refs(
    typ: TpyType,
    target_names: frozenset[str],
    expanded_aliases: dict[frozenset[TpyType], str] | None = None,
) -> list[tuple[str, bool]]:
    """Walk a type tree and collect references to any of `target_names`.

    Also detects expanded union types that match a known alias (the parser
    expands `Box[Expr]` to `Box[UnionType(Lit, BinOp)]`, so we match the
    expanded UnionType back to the alias by comparing member name sets).

    Returns list of (referenced_name, has_indirection) pairs.
    """
    results: list[tuple[str, bool]] = []
    _walk(typ, target_names, False, results, expanded_aliases, set())
    return results


def _walk(
    typ: TpyType,
    target_names: frozenset[str],
    inside_indirection: bool,
    out: list[tuple[str, bool]],
    expanded_aliases: dict[frozenset[TpyType], str] | None,
    expanding: set[str],
) -> None:
    from .typesys import NominalType, AliasRef, UnionType
    from .type_def_registry import record_info_of
    # Recursive-alias self-reference: parser-emitted AliasRef placeholder.
    if isinstance(typ, AliasRef) and typ.name in target_names:
        out.append((typ.name, inside_indirection))
        return
    if isinstance(typ, NominalType) and typ.name in target_names and not typ.is_protocol:
        out.append((typ.name, inside_indirection))
        return
    # The parser expands `Box[Expr]` into `Box[UnionType(Lit, BinOp)]`, so a
    # union encountered here may be an already-expanded alias body; match it
    # back by its member set.
    if expanded_aliases and isinstance(typ, UnionType):
        alias_name = expanded_aliases.get(frozenset(typ.members))
        if alias_name is not None:
            out.append((alias_name, inside_indirection))
            return
    if _is_indirecting_type(typ):
        for inner in typ.inner_types():
            _walk(inner, target_names, True, out, expanded_aliases, expanding)
        return
    if isinstance(typ, NominalType) and not typ.is_protocol:
        info = record_info_of(typ)
        if info is not None and info.fields:
            _walk_record_fields(typ, info, target_names, inside_indirection,
                                out, expanded_aliases, expanding)
            return
    # Fallback for parser placeholders without a RecordInfo, primitives, and
    # any structural type not covered above.
    for inner in typ.inner_types():
        _walk(inner, target_names, inside_indirection, out,
              expanded_aliases, expanding)


def _walk_record_fields(
    typ: 'NominalType',
    info: 'RecordInfo',
    target_names: frozenset[str],
    inside_indirection: bool,
    out: list[tuple[str, bool]],
    expanded_aliases: dict[frozenset[TpyType], str] | None,
    expanding: set[str],
) -> None:
    """Walk a non-indirecting record's fields under type-parameter substitution.

    Lets `Box[Foo]` reveal indirection through its `_ptr: Ptr[T]` field
    without hard-coding the record's name. Recursion guard keys on the
    qualified name (falling back to bare name for unregistered placeholders),
    so structurally self-referential records like `class Wrapper[T]: inner:
    Wrapper[T]` terminate.
    """
    guard_key = info.qualified_name() or info.name
    if guard_key in expanding:
        return
    subst: dict[str, TpyType] = {}
    if info.type_params and typ.type_args:
        for param_name, arg in zip(info.type_params, typ.type_args):
            # Array[T, N]'s int slot lands here as a raw int; skip it.
            if hasattr(arg, "map_inner_types"):
                subst[param_name] = arg
    expanding.add(guard_key)
    try:
        for fld in info.fields:
            field_type = (substitute_type_params_structural(fld.type, subst)
                          if subst else fld.type)
            _walk(field_type, target_names, inside_indirection,
                  out, expanded_aliases, expanding)
    finally:
        expanding.discard(guard_key)


# ---- Graph + cycle detection -------------------------------------------

@dataclass
class _Edge:
    """A directed edge in the type dependency graph."""
    source: str
    target: str
    has_indirection: bool
    field_name: str | None = None


def validate_recursive_union_paths(
    alias_name: str, members: tuple[TpyType, ...],
) -> str | None:
    """Validate that all self-references in a recursive union alias go
    through an indirecting container.

    Returns an error message if a non-indirected self-reference exists,
    or None when the alias is well-formed. Indirection sources are the
    same as the wider cycle walker (`_is_indirecting_type` + the
    structural field walk via `_walk` over non-indirecting nominals).

    Called from sema's `_validate_recursive_union_paths` after record /
    protocol registration so the field walk can see same-module RecordInfo /
    TypeDef entries for user indirecting records.
    """
    target = frozenset([alias_name])
    error_msg = (
        f"direct recursion in type alias '{alias_name}' -- "
        f"every recursive path must go through an indirecting "
        f"container (list, dict, set, Optional, Ptr, or a "
        f"record providing pointer indirection)"
    )
    for member in members:
        results: list[tuple[str, bool]] = []
        _walk(member, target, False, results, None, set())
        for _ref_name, has_indirection in results:
            if not has_indirection:
                return error_msg
    return None


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
    expanded_aliases: dict[frozenset[TpyType], str] = {}
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
