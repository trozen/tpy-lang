"""The storage policy of a reference local's first binding."""

from __future__ import annotations

from typing import NamedTuple

from ..typesys import (TpyType, unwrap_own, unwrap_readonly, unwrap_ref_type,
                       unwrap_send_sync)


class StorageVerdict(NamedTuple):
    """How a reference local's first binding holds its object."""
    # A pointer over storage the first object keeps: a rebind re-points
    # the pointer, so an alias of the old object keeps it.
    pointer: bool = False
    # The binding re-runs in a loop with an alias kept across passes: its
    # storage stands before the loop (the escape hoist).
    hoisted: bool = False


# The site kinds that bind a body local for the first time.
FIRST_BINDING_SITES = frozenset({"decl", "hoist", "unpack", "loop", "walrus",
                                 "with", "frame"})
# The sites the escape hoist does not apply to: a resumable frame's field
# outlives every loop pass, and a hoist row's storage is its predeclaration.
# That predeclaration is not always before the loop: a `try` / `with` one
# inside a loop body dies with each pass
# (BUGS.md#block-optional-slot-alias-dangles).
_OUTLIVES_LOOP_PASSES = frozenset({"hoist", "frame"})


def first_binding_storage(site_kind: str, typ: TpyType | None, *,
                          rebound: bool, hoisted: bool) -> StorageVerdict:
    """The ONE storage policy of a reference local's first binding, from
    two order-free name facts: rebound at all (the prescan's `reassigned`,
    a nested def's merged with its enclosing body's) and kept across loop
    passes (the escape hoist). Which row bound the name -- a declaration,
    an unpack target, a walrus, a for or with target -- and what it bound
    it from do not enter: each rebind's own storage is the per-site
    verdict (`rebind_storage`). A value type copies at every rebind."""
    if site_kind not in FIRST_BINDING_SITES or not isinstance(typ, TpyType):
        return StorageVerdict()
    bare = unwrap_own(unwrap_readonly(unwrap_send_sync(unwrap_ref_type(typ))))
    if bare.is_value_type():
        return StorageVerdict()
    if site_kind in _OUTLIVES_LOOP_PASSES:
        hoisted = False
    return StorageVerdict(pointer=rebound or hoisted, hoisted=hoisted)
