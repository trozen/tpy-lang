"""Tuple leaf paths: the one vocabulary for addressing the leaves of a
value's type (a nested tuple's members, any other type itself) and for
which of them are references, lent or owned by their declared `Own`
markers. The walk itself (`leaves_at`, `reference_paths`,
`declared_borrowed`) lives in `tpyc.value_category`, whose call-result
readings use it; this module adds the slot-ownership projections and
re-exports the walk so every sema module has one import for it."""

from __future__ import annotations

from typing import Callable

from ..typesys import TpyType, unwrap_ref_type
from ..value_category import (
    ElemPath, bare_tuple, declared_borrowed, is_reference_leaf, leaves_at,
    paths_at, reference_paths,
)

PathsAt = Callable[[TpyType, ElemPath], frozenset[ElemPath]]

__all__ = [
    "ElemPath", "PathsAt", "bare_tuple", "declared_borrowed", "declared_lent_paths",
    "handed_over_paths", "is_reference_leaf", "leaf_paths", "leaves_at",
    "owned_leaf_paths", "owned_slot_paths", "paths_at", "reference_paths",
    "step_hands_over_lent",
]


def leaf_paths(slot: TpyType | None,
               prefix: ElemPath = ()) -> frozenset[ElemPath]:
    """Every leaf path of a value of type `slot` -- top-level `(i,)`,
    nested `(i, j)` through nested tuples, value leaves included -- and the
    one path `prefix` of a value that is no tuple."""
    if slot is None:
        return frozenset({prefix})
    return frozenset(p for p, _e in leaves_at(slot, prefix))


def owned_slot_paths(slot: TpyType | None) -> frozenset[ElemPath]:
    """The element paths (`reference_paths`) of tuple `slot` its `Own`
    markers own, at any depth: every one under `Own[tuple[...]]`, those
    under an `Own[...]` element, none of a plain element. Empty for no
    tuple."""
    bare = bare_tuple(slot)
    if bare is None or slot is None:
        return frozenset()
    return reference_paths(bare) - declared_borrowed(
        unwrap_ref_type(slot), bare, ())


def owned_leaf_paths(slot: TpyType) -> frozenset[ElemPath]:
    """`owned_slot_paths` over every leaf (`leaf_paths`), value leaves
    included, of a slot that takes ownership: a tuple's `Own`-marked
    leaves, a scalar's one leaf `()`."""
    bare = bare_tuple(slot)
    if bare is None:
        return frozenset({()})
    return leaf_paths(bare) - declared_borrowed(
        unwrap_ref_type(slot), bare, (), leaf_paths)


def declared_lent_paths(declared: TpyType) -> frozenset[ElemPath]:
    """The reference leaf paths a value of declared type `declared` lends
    (`declared_borrowed`): none under an `Own` marker, every other
    reference leaf (`is_reference_leaf`)."""
    return declared_borrowed(declared, declared, ())


def handed_over_paths(declared: TpyType, paths: PathsAt = paths_at,
                      ) -> frozenset[ElemPath]:
    """The reference leaf paths (`paths`, by default `paths_at`) a value
    of declared type `declared` hands over: those its `Own` markers cover
    (`declared_lent_paths`' rest)."""
    return paths(declared, ()) - declared_borrowed(declared, declared, (),
                                                   paths)


def step_hands_over_lent(required: TpyType, declared: TpyType,
                         paths: PathsAt = paths_at) -> bool:
    """A step declared `declared` hands over a reference leaf a step
    requirement `required` lends: the callee may keep a lent leaf for the
    call, while a handed-over one lives only in its consumer's step. A
    handed-over path covers every leaf under it: `Own[T]` at
    `T = tuple[Cell, int32]` hands over the `Cell` at `(0,)`. `paths`
    reads a type's reference leaf paths (`paths_at` by default)."""
    handed = handed_over_paths(declared, paths)
    return any(lent[:len(p)] == p
               for lent in declared_borrowed(required, required, (), paths)
               for p in handed)
