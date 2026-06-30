"""Shared binding-form classification for non-value locals.

The C++ binding shape of a single-assignment non-value local -- a `T&` alias of
an lvalue storage source, or a `T*` lifted from a storage-form `Optional[ref]`
via `optional_to_ptr` -- is a pure function of the resolved local type plus the
per-function prescan facts (reassigned / hoisted / move-through). It is decided
today inside `_gen_var_decl_code`'s indirection cascade; this module lifts that
decision into one helper so the legacy AST codegen path and THIR lowering reach
it identically (the same shared-helper pattern as `resolve_stmt_binding_type`).

The single-assignment slice (`T&` / `optional_to_ptr` `T*`) is rung F1; a
reassigned-but-lvalue-sourced plain non-value local is rung F2's reseatable
`T*` pointer-local (`POINTER`). Every other binding shape -- rvalue/rebind-slot
locals, tuples, unions, generic slots -- returns `OTHER` and stays on the
caller's existing path.
"""

from __future__ import annotations

from enum import Enum, auto

from ..parse.nodes import TpyExpr, TpyFieldAccess, TpySubscript
from ..typesys import (
    OptionalType, OwnType, TpyType, TupleType, UnionType,
    unwrap_qualifiers, unwrap_readonly,
)
from ..value_category import is_rvalue_source


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
                             source (a ctor / by-value call), reseatable via the
                             two-slot `__slot_N` machinery (a direct init slot +
                             an `std::optional<T>` rebind slot). The rvalue
                             counterpart of POINTER.
      * `STORAGE_TUPLE_ALIAS` -- `auto&& name = <lvalue storage tuple>` aliasing a
                             pointer-repr tuple's storage (F3); single-assignment.
                             Used by THIR lowering only (`is_storage_tuple_alias_decl`);
                             the AST path decides this arm inline in `_gen_var_decl_code`.
      * `OTHER`           -- any other binding; the caller's existing path owns it
                             (single-assignment rvalue value-locals, tuples,
                             unions, generic slots, value types).
    """
    REF_ALIAS = auto()
    OPTIONAL_TO_PTR = auto()
    POINTER = auto()
    REBIND_SLOT = auto()
    STORAGE_TUPLE_ALIAS = auto()
    OTHER = auto()


def is_ptr_variant_union(t: TpyType) -> bool:
    """A non-value union lowered to `std::variant<A*, B*>` (pointer variant).

    Pure type query -- `CodeGenContext.is_ptr_variant_union` delegates here so the
    binding classifier and codegen share one definition.
    """
    return (isinstance(t, UnionType) and t.uses_pointer_repr()
            and not t.needs_wrapper())


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
) -> bool:
    """A pointer-repr tuple local bound `auto&& name = <lvalue storage tuple>`,
    aliasing the source's storage (CPython shares the elements); single-assignment
    only. Mirrors `_gen_var_decl_code`'s storage-form-tuple alias arm
    (`statements.py`, the `auto&&` return) for the FieldAccess-source subset -- the
    Subscript / storage-form-Name sources, the const tracking, and the reassigned
    BORROW_TUPLE fall-over ride later F3 cells. A field read off a storage tuple
    field is unconditionally a storage source (the AST's `is_storage_form_source`
    returns True for any FieldAccess), so no ctx walk-state is needed here.

    Pure: a function of the resolved type, the init shape, and the prescan facts;
    callers add their own receiver / element checks (THIR lowering gates the field
    receiver + the F1 tuple slice). The whole-corpus byte-diff is the anti-drift net
    against the AST arm above.

    The init must be a BARE `TpyFieldAccess`: THIR lowering reads `init.obj` directly
    (no coerce peel), and the eligibility gate (`_field_receiver_ok`) likewise admits
    only a bare field access, so admitting a coerce-wrapped source here would let the
    two diverge. A coerce-wrapped tuple-field read stays on the AST path until a later
    F3 cell handles it on both sides together."""
    if init is None or target_type is None:
        return False
    if name in reassigned or name in hoisted or name in move_through:
        return False
    if not (isinstance(target_type, TupleType)
            and target_type.has_pointer_repr_element()):
        return False
    return isinstance(init, TpyFieldAccess)


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

    Callers consult this only *after* their own guards -- the legacy path inside
    `_gen_var_decl_code`'s indirection cascade (the tuple / protocol / generic
    branches have already not fired), THIR lowering after its eligibility gate --
    so the guard-chain shapes need not be re-derived here.

    The AST path treats every non-`REF_ALIAS` result as a pointer-local, so
    splitting `POINTER` out of the old `OTHER` is transparent to it.
    """
    if init is None or target_type is None:
        return LocalBinding.OTHER
    if name in hoisted or name in move_through:
        return LocalBinding.OTHER
    is_reassigned = name in reassigned
    if isinstance(target_type, OptionalType) and target_type.uses_pointer_repr():
        # A reassigned optional pointer-local needs the rebind-slot (`__slot_N`)
        # machinery -- deferred past F2's lvalue-reseat slice.
        if is_reassigned:
            return LocalBinding.OTHER
        if (reads_storage_form_optional(analyzer, init)
                and not is_rvalue_source(analyzer, init)):
            return LocalBinding.OPTIONAL_TO_PTR
        return LocalBinding.OTHER
    if is_plain_nonvalue(target_type):
        if is_rvalue_source(analyzer, init):
            # An rvalue source (a ctor / by-value call). A name reassigned with an
            # rvalue is a rebind-slot pointer-local (the two-slot `__slot_N`
            # machinery); a single-assignment rvalue local needs no indirection
            # (a plain value local) and is left to the caller's path.
            return (LocalBinding.REBIND_SLOT if name in rvalue_reassigned
                    else LocalBinding.OTHER)
        # An lvalue source binds a `T&` alias (single-assignment) or a reseatable
        # `T*` pointer-local (reassigned); both lift the lvalue storage to a borrow
        # at the binding site (the reseat to later lvalues is F2a).
        return LocalBinding.POINTER if is_reassigned else LocalBinding.REF_ALIAS
    return LocalBinding.OTHER
