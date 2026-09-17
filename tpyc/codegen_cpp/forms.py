"""Shared binding-form classification for non-value locals.

The C++ binding shape of a single-assignment non-value local -- a `T&` alias of
an lvalue storage source, or a `T*` lifted from a storage-form `Optional[ref]`
via `optional_to_ptr` -- is a pure function of the resolved local type plus the
per-function prescan facts (reassigned / hoisted / move-through). It is decided
in one helper here, so every caller reaches it identically (the same
shared-helper pattern as `resolve_stmt_binding_type`).

The modeled slice is the single-assignment `T&` / `optional_to_ptr` `T*` shape
plus the reseatable `T*` pointer-local (`POINTER`) of a reassigned but
lvalue-sourced plain non-value local. Every other binding shape --
rvalue/rebind-slot locals, tuples, unions, generic slots -- returns `OTHER` and
is the caller's own.
"""

from __future__ import annotations

from enum import Enum, auto

from ..parse.nodes import (TpyExpr, TpyFieldAccess, TpyName, TpyNoneLiteral,
                           TpySubscript)
from ..typesys import (
    OptionalType, OwnType, TpyType, TupleType, UnionType,
    is_ptr_variant_union, unwrap_qualifiers, unwrap_readonly,
)
from ..value_category import call_returns_cpp_ref, is_rvalue_source


def _expr_type(analyzer, expr: TpyExpr) -> TpyType | None:
    """The codegen view of an expr's type: sema type with `ReadonlyType`
    stripped (C++ handles const via signatures). Mirrors
    `CodeGenContext.get_expr_type` so analyzer-pure callers classify identically.
    """
    typ = analyzer.get_expr_type(expr)
    return unwrap_readonly(typ) if typ is not None else None


class LocalBinding(Enum):
    """The C++ binding shape of a single-assignment non-value local.

      * `REF_ALIAS`       -- `T&` / `const T&` aliasing an lvalue storage source.
      * `OPTIONAL_TO_PTR` -- `T*` / `const T*` lifted from a storage-form
                             `Optional[ref]` lvalue via `::tpy::optional_to_ptr`.
      * `POINTER`         -- `T*` / `const T*` pointer-local of a plain non-value
                             lvalue source (`&(...)`), reseatable across
                             reassignments. The reassigned counterpart of
                             REF_ALIAS; only the lvalue-reseat (slot-free) subset.
      * `REBIND_SLOT`     -- `T*` pointer-local of a plain non-value *rvalue*
                             source (a ctor / by-value call): a direct init
                             slot the pointer aims at, and each rvalue reseat
                             writes in place or into a slot of its own
                             (sema's `rebind_storage`). The rvalue
                             counterpart of POINTER.
      * `OPT_PTR_SLOT`    -- `T*` pointer-local of a pointer-repr `Optional[T]`
                             whose init is a None literal (`T* x = nullptr;`) or
                             an rvalue (`T __slot_N = ...; T* x = &__slot_N;`).
                             The Optional sibling of REBIND_SLOT.
      * `STORAGE_TUPLE_ALIAS` -- `auto&& name = <lvalue storage tuple>` aliasing a
                             pointer-repr tuple's storage; single-assignment.
                             Decided by `is_storage_tuple_alias_decl`.
      * `PTR_VARIANT`     -- `::tpy::Union<[const] A*, [const] B*>` pointer-variant
                             local of a non-value union: a bare copy of a
                             borrow-form source, or a `to_[const_]ptr_variant` lift
                             of a value-variant lvalue (a union field); reseatable.
      * `OTHER`           -- any other binding; the caller's existing path owns it
                             (single-assignment rvalue value-locals, tuples,
                             generic slots, value types).
    """
    REF_ALIAS = auto()
    OPTIONAL_TO_PTR = auto()
    POINTER = auto()
    REBIND_SLOT = auto()
    OPT_PTR_SLOT = auto()
    STORAGE_TUPLE_ALIAS = auto()
    PTR_VARIANT = auto()
    OTHER = auto()


def is_plain_nonvalue(t: TpyType) -> bool:
    """Non-value type needing indirection (record, list, dict, set, recursive-
    union wrapper). Unwraps `Own[T]`; excludes pointer-repr Optional and
    ptr-variant Union, which have their own codegen paths.
    """
    check = t.wrapped if isinstance(t, OwnType) else t
    if check.is_value_type():
        return False
    if isinstance(check, OptionalType) and check.uses_pointer_repr():
        return False
    if is_ptr_variant_union(check):
        return False
    return True


def reads_storage_form_optional(analyzer, expr: TpyExpr) -> bool:
    """The analyzer-pure core of `CodeGenContext.is_storage_form_optional_source`
    for field / subscript sources: a read rendering as a storage-form
    `std::optional<T>` lvalue, so a borrow consumer must lift it via
    `optional_to_ptr`. The `TpyName`/`STORAGE_OPTIONAL`-local case stays on the
    ctx method -- it needs the walk-built local-form sets, not just the analyzer.
    """
    if isinstance(expr, TpyFieldAccess):
        vt = _expr_type(analyzer, expr)
        return isinstance(vt, OptionalType) and vt.uses_pointer_repr()
    if isinstance(expr, TpySubscript):
        vt = _expr_type(analyzer, expr)
        if not (isinstance(vt, OptionalType) and vt.uses_pointer_repr()):
            return False
        # A user-record __getitem__ CALL follows the call convention: a
        # pointer-repr Optional return is already borrow-form `T*` unless the
        # accessor returns a C++ reference aliasing a stored optional (the
        # cpp-ref case keeps the storage lift, like a container element).
        if expr.getitem_function_info is not None:
            return call_returns_cpp_ref(analyzer, expr.getitem_function_info)
        # A tuple subscript pre-lifts to `T*` in codegen, so it is not a storage
        # source (mirrors the tuple carve-out in is_storage_form_optional_source).
        obj_type = _expr_type(analyzer, expr.obj)
        return not (obj_type is not None
                    and isinstance(unwrap_qualifiers(obj_type), TupleType))
    return False


def is_storage_tuple_alias_decl(
    target_type: TpyType | None,
    init: TpyExpr | None,
    *,
    name: str,
    reassigned: set[str],
    hoisted: set[str],
    move_through: set[str],
    storage_tuple_locals: set[str] | None = None,
) -> bool:
    """A pointer-repr tuple local bound `auto&& name = <lvalue storage tuple>`,
    aliasing the source's storage (CPython shares the elements); single-assignment
    only. Three lvalue source shapes are admitted -- FieldAccess, Subscript,
    and a Name that is itself a storage-form tuple local. The reassigned
    BORROW_TUPLE fall-over is not admitted here.

    The source-shape test follows `is_storage_form_source`: a field read
    and a container subscript are unconditionally storage sources, while a NAME is
    one only when it already aliases storage. `storage_tuple_locals` supplies that
    membership; passing None admits the two unconditional shapes only, so a caller
    without the walk-state cannot silently admit a borrow-form name.

    Pure, but NOT all its inputs are available up front: `reassigned` / `hoisted` /
    `move_through` are prescan facts, while `storage_tuple_locals` is built
    incrementally during the same lowering walk (a name joins it only once its own
    alias decl has been lowered), which is exactly what makes the Name arm an
    alias-of-an-alias test rather than a type test. Callers add their own receiver /
    element / const checks (THIR lowering gates the receiver and the tuple
    slice, and rejects const sources on the non-field shapes).

    The init must be BARE (no coerce peel): THIR lowering reads `init.obj` directly
    and the eligibility gate likewise admits only a bare access, so admitting a
    coerce-wrapped source here would let the two diverge. A coerce-wrapped tuple
    read is not admitted here."""
    if init is None or target_type is None:
        return False
    if name in reassigned or name in hoisted or name in move_through:
        return False
    if not (isinstance(target_type, TupleType)
            and target_type.has_pointer_repr_element()):
        return False
    if isinstance(init, (TpyFieldAccess, TpySubscript)):
        return True
    return (isinstance(init, TpyName) and storage_tuple_locals is not None
            and init.name in storage_tuple_locals)


def classify_local_binding(
    target_type: TpyType | None,
    init: TpyExpr | None,
    analyzer,
    *,
    name: str,
    reassigned: set[str],
    rvalue_reassigned: set[str],
    hoisted: set[str],
    move_through: set[str],
) -> LocalBinding:
    """Classify a first-declaration non-value local's C++ binding shape.

    Pure: a function of the resolved type, the init expression, and the
    per-function prescan facts. Returns `OTHER` for anything outside the modeled
    slice (hoisted / move-through / rvalue-sourced locals, tuples / unions /
    protocols / generic slots, value types).

    Callers consult this only *after* their own guards -- THIR lowering after
    its eligibility gate -- so the guard-chain shapes need not be re-derived
    here.
    """
    if init is None or target_type is None:
        return LocalBinding.OTHER
    if name in hoisted or name in move_through:
        return LocalBinding.OTHER
    is_reassigned = name in reassigned
    if isinstance(target_type, OptionalType) and target_type.uses_pointer_repr():
        # None-literal and rvalue inits take the slot-hoist pointer-local
        # machinery (reassigned or not). THIR lowering sub-gates the
        # admitted init/reseat shapes.
        if isinstance(init, TpyNoneLiteral) or is_rvalue_source(analyzer, init):
            return LocalBinding.OPT_PTR_SLOT
        # A reassigned optional off an LVALUE init binds the same
        # OPTIONAL_TO_PTR lift as the single-assignment shape; its reseats
        # ride the pointer-local reseat arms (lvalue lift / nullptr /
        # inline-rvalue slot).
        if reads_storage_form_optional(analyzer, init):
            return LocalBinding.OPTIONAL_TO_PTR
        return LocalBinding.OTHER
    if is_plain_nonvalue(target_type):
        if is_rvalue_source(analyzer, init):
            # An rvalue source (a ctor / by-value call). A name reassigned with an
            # rvalue is a rebind-slot pointer-local; a single-assignment
            # rvalue local needs no indirection
            # (a plain value local) and is left to the caller's path.
            return (LocalBinding.REBIND_SLOT if name in rvalue_reassigned
                    else LocalBinding.OTHER)
        # An lvalue source binds a `T&` alias (single-assignment) or a reseatable
        # `T*` pointer-local (reassigned); both lift the lvalue storage to a borrow
        # at the binding site.
        return LocalBinding.POINTER if is_reassigned else LocalBinding.REF_ALIAS
    return LocalBinding.OTHER
