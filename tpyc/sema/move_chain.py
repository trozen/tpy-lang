"""Movability diagnostic chain-walker.

`why_not_movable` explains *why* a type is not movable -- it never re-derives
the boolean answer (`t.is_movable()` stays the single source of truth), it only
attributes an already-False answer to the sub-component that carries it (record
fields/bases, optional/tuple/union members, wrapper payloads), bottoming out at
the non-movable leaf: a `@nomove` record. The cycle-guarded recursion driver
and the indented-tree renderer are shared with the Send/Sync diagnostic in
`trait_chain`; this module supplies only the movability-specific sub-component
enumeration and leaf reason.
"""
from typing import Optional

from tpyc.typesys import (
    TpyType, NominalType, UnionType, TupleType, OptionalType,
    OwnType, ReadonlyType, RefType, substitute_type_params_structural,
)
from .trait_chain import ChainNode, Recurse, build_chain, render_chain as _render_chain


def why_not_movable(t: TpyType) -> Optional[ChainNode]:
    """Return a derivation tree for why `t` is not movable, or None if it is."""
    return build_chain(t, str(t), lambda x: x.is_movable(), _attribute, _leaf_reason)


def render_move_chain(chain: ChainNode) -> str:
    return _render_chain(chain, "movable")


# -- internals --

def _attribute(t: TpyType, recurse: Recurse) -> list[ChainNode]:
    from tpyc.type_def_registry import type_def_of

    if isinstance(t, NominalType):
        # A @nomove record is an inherent leaf -- its non-movability is its own
        # declaration, not attributable to a walkable sub-component.
        td = type_def_of(t)
        rec = td.record if td is not None else None
        if rec is None or rec.move_override is False:
            return []
        subst: dict[str, TpyType] = {}
        if rec.type_params and t.type_args:
            for name, arg in zip(rec.type_params, t.type_args):
                if isinstance(arg, TpyType):
                    subst[name] = arg
        out: list[ChainNode] = []
        for f in rec.fields:
            ft = substitute_type_params_structural(f.type, subst) if subst else f.type
            node = recurse(ft, f"field '{f.name}: {ft}'")
            if node is not None:
                out.append(node)
        for p in rec.parents:
            pt = substitute_type_params_structural(p, subst) if subst else p
            node = recurse(pt, f"base '{pt}'")
            if node is not None:
                out.append(node)
        return out

    if isinstance(t, UnionType):
        return [n for m in t.members
                if (n := recurse(m, str(m))) is not None]

    if isinstance(t, TupleType):
        return [n for i, e in enumerate(t.element_types)
                if (n := recurse(e, f"element {i}: {e}")) is not None]

    if isinstance(t, OptionalType):
        node = recurse(t.inner, str(t.inner))
        return [node] if node is not None else []

    if isinstance(t, (OwnType, ReadonlyType, RefType)):
        node = recurse(t.wrapped, str(t.wrapped))
        return [node] if node is not None else []

    return []


def _leaf_reason(t: TpyType) -> str:
    if isinstance(t, NominalType):
        from tpyc.type_def_registry import type_def_of
        td = type_def_of(t)
        rec = td.record if td is not None else None
        if rec is not None and rec.move_override is False:
            return "marked @nomove"
    return "not movable"
