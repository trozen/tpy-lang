"""Tuple leaf paths: the one vocabulary for addressing the leaves of a
value's type (a nested tuple's members, any other type itself) and for
which of them are references, lent or owned by their declared `Own`
markers. Depends on the type system only, so every sema module can use
it."""

from __future__ import annotations

from typing import Callable, Iterator

from ..typesys import (
    TpyType, TupleType, owned_payload, peel_slot, unwrap_optional_own,
    unwrap_readonly, unwrap_ref_type, unwrap_send_sync,
    without_type_param_bounds,
)

# A leaf's path through nested tuples: `(i,)` top level, `(i, j)` nested.
ElemPath = tuple[int, ...]


def bare_tuple(t: TpyType | None) -> TupleType | None:
    """`t` as a tuple, its wrappers (`peel_slot`) peeled; None for no
    tuple."""
    bare = peel_slot(t) if t is not None else None
    return bare if isinstance(bare, TupleType) else None


def leaves_at(et: TpyType, path: ElemPath
              ) -> Iterator[tuple[ElemPath, TpyType]]:
    """Every leaf of a value of type `et` at `path` with its payload type:
    the members of a nested tuple, any other type itself. The one walk
    over a tuple's leaves (`leaf_paths`, `reference_paths`, the
    conversion's copied leaves)."""
    e = peel_slot(et)
    if isinstance(e, TupleType):
        for i, sub in enumerate(e.element_types):
            yield from leaves_at(sub, path + (i,))
    else:
        # An `Own[T] | None` leaf owns its payload `T` as an `Own[T]` leaf
        # does; only nullability tells them apart.
        payload = owned_payload(e)
        yield path, (peel_slot(payload) if payload is not None else e)


def leaf_paths(slot: TpyType | None,
               prefix: ElemPath = ()) -> frozenset[ElemPath]:
    """Every leaf path of a value of type `slot` -- top-level `(i,)`,
    nested `(i, j)` through nested tuples, value leaves included -- and the
    one path `prefix` of a value that is no tuple."""
    if slot is None:
        return frozenset({prefix})
    return frozenset(p for p, _e in leaves_at(slot, prefix))


def is_reference_leaf(t: TpyType) -> bool:
    """THE test of a leaf an owning slot copies or moves as an object: a
    payload that is no value type -- an open type parameter included,
    whatever its bound (`T`, `T | None`, `T | int32`): that a `ValueType`
    bound makes its copy unobservable is sema's decision, stated in the
    record as a VALUE leaf."""
    return not without_type_param_bounds(peel_slot(t)).is_value_type()


def _paths_at(et: TpyType, path: ElemPath) -> frozenset[ElemPath]:
    """The reference paths of a tuple element of type `et` at `path`: its
    own when it is a reference leaf (`is_reference_leaf`), its members'
    when it is a nested value tuple."""
    return frozenset(p for p, e in leaves_at(et, path)
                     if is_reference_leaf(e))


def reference_paths(slot: TpyType | None,
                    prefix: ElemPath = ()) -> frozenset[ElemPath]:
    """The element paths of tuple `slot` -- top-level `(i,)`, nested
    `(i, j)` through value tuples -- whose leaf is a reference: the
    reference projection of `leaf_paths`. Empty for no tuple."""
    if bare_tuple(slot) is None:
        return frozenset()
    return _paths_at(slot, prefix)


def _declared_borrowed(declared: TpyType, slot: TpyType,
                       path: ElemPath, paths_at: Callable[
                           [TpyType, ElemPath], frozenset[ElemPath]]
                       = _paths_at) -> frozenset[ElemPath]:
    """The paths at `path` a call's declared return element `declared`
    lends: an `Own[...]` marker hands its subtree over, a nested tuple
    answers per member, anything else lends every path of the slot there
    (`paths_at`: its reference paths, or every leaf path)."""
    d = unwrap_readonly(unwrap_send_sync(declared))
    if unwrap_optional_own(d) is not None:
        return frozenset()
    dt, st = bare_tuple(d), bare_tuple(slot)
    if (dt is None or st is None
            or len(dt.element_types) != len(st.element_types)):
        return paths_at(slot, path)
    out: set[ElemPath] = set()
    for i, (de, se) in enumerate(zip(dt.element_types, st.element_types)):
        out |= _declared_borrowed(de, se, path + (i,), paths_at)
    return frozenset(out)


def owned_slot_paths(slot: TpyType | None) -> frozenset[ElemPath]:
    """The element paths (`reference_paths`) of tuple `slot` its `Own`
    markers own, at any depth: every one under `Own[tuple[...]]`, those
    under an `Own[...]` element, none of a plain element. Empty for no
    tuple."""
    bare = bare_tuple(slot)
    if bare is None or slot is None:
        return frozenset()
    return reference_paths(bare) - _declared_borrowed(
        unwrap_ref_type(slot), bare, ())


def owned_leaf_paths(slot: TpyType) -> frozenset[ElemPath]:
    """`owned_slot_paths` over every leaf (`leaf_paths`), value leaves
    included, of a slot that takes ownership: a tuple's `Own`-marked
    leaves, a scalar's one leaf `()`."""
    bare = bare_tuple(slot)
    if bare is None:
        return frozenset({()})
    return leaf_paths(bare) - _declared_borrowed(
        unwrap_ref_type(slot), bare, (), leaf_paths)


def declared_lent_paths(declared: TpyType) -> frozenset[ElemPath]:
    """The reference leaf paths a value of declared type `declared` lends
    (`_declared_borrowed`): none under an `Own` marker, every other
    reference leaf (`is_reference_leaf`)."""
    return _declared_borrowed(declared, declared, ())
