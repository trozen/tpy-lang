"""Alias-rebind clobber diagnostic.

Rebinding a reference-typed local while a live loan still points at the
object it held silently hands the loan holder the NEW object -- CPython
keeps the old one. Whether it happens on the first rebind or only on a later
one is decided by how many object generations the name's storage can hold at
once, which `storage_generations` answers from the same prescan set the THIR
slot allocation reads.

The hazard itself stays (BUGS.md#resumable-alias-identity and its sync
siblings); this makes it loud at the rebind, with the spellings that avoid it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..parse import (
    TpyAssign, TpyCall, TpyCoerce, TpyExpr, TpyFunction, TpyName,
    TpyNestedDef, TpyNoneLiteral, TpyStmt, TpyVarDecl,
)
from ..typesys import unwrap_readonly, view_family_for_type
from ..value_category import is_rvalue_source
from .context import (
    BorrowKind, ITER_BORROWER, LoanResidency, _borrow_storage_root,
)

if TYPE_CHECKING:
    from .context import SemanticContext


# Names that take a pointer INTO their argument's storage rather than a copy.
# Kept to the two spellings the binding path already registers a PTR loan for.
_PTR_TAKING_CALLS = ("take_ptr", "Ptr")


def storage_generations(ctx: 'SemanticContext', name: str) -> int:
    """How many object generations `name`'s storage can hold live at once.

    `2` when the lowering reserves a function-scoped rebind slot beside the
    block-scoped init storage -- an ordinary sync body's rvalue-rebound local.
    `1` everywhere else: a resumable frame has ONE `frame_slot<T>` field per
    name, module level has no rebind slot at all, and a name with no reserved
    slot has only its init storage. A nested def reads the ENCLOSING body's
    slot set, because that is the set the lowering hands its lambda.
    """
    func = ctx.func.current_function
    if ctx.is_top_level or not isinstance(func, TpyFunction):
        return 1
    # `is_simple_generator` is deliberately NOT consulted: it is a codegen
    # fact that depends on `requires_resumable_frame`, which sema sets DURING
    # the body analysis this check runs in.
    if func.is_async or func.is_generator:
        return 1
    return 2 if name in ctx.func.rebind_slot_names else 1


def loan_bind_root(stmt: TpyStmt) -> tuple[str, str, BorrowKind] | None:
    """`(holder, storage_root, kind)` when `stmt` binds a name to a loan of
    another name's storage, else None. Syntactic and deliberately narrow: the
    shapes `register_binding_borrow` and the `Ptr` arm register a loan for.
    The kind only distinguishes a whole-name ALIAS from a loan INTO the
    object, which is all the message wording needs.
    """
    if isinstance(stmt, TpyVarDecl):
        holder, init = stmt.name, stmt.init
    elif isinstance(stmt, TpyAssign) and isinstance(stmt.target, TpyName):
        holder, init = stmt.target.name, stmt.value
    else:
        return None
    if init is None:
        return None
    inner = init.expr if isinstance(init, TpyCoerce) else init
    root = _borrow_storage_root(init)
    if root is None:
        root = _ptr_call_root(init)
    if root is None or root == holder:
        return None
    kind = (BorrowKind.ALIAS if isinstance(inner, TpyName)
            else BorrowKind.ELEMENT)
    return (holder, root, kind)


def _ptr_call_root(init: TpyExpr) -> str | None:
    """Storage root of a `take_ptr(x)` / `Ptr(x)` init, else None."""
    inner = init.expr if isinstance(init, TpyCoerce) else init
    if (isinstance(inner, TpyCall) and inner.args
            and isinstance(inner.func, TpyName)
            and inner.func.name in _PTR_TAKING_CALLS):
        return _borrow_storage_root(inner.args[0])
    return None


def collect_loop_body_loans(
        stmts: list[TpyStmt]) -> dict[str, dict[str, BorrowKind]]:
    """storage root -> holder -> kind, for every loan the body binds at any
    depth.

    Seeded like `collect_fact_kills`: sema walks a loop body ONCE, so the
    re-execution has to be read off the syntax rather than a fixpoint.
    Keyed by the rebindable NAME, so a field-chain loan (`i = h.inner`)
    is indexed under `h` -- what a rebind statement names.
    """
    loans: dict[str, dict[str, BorrowKind]] = {}
    _collect_loop_body_loans(stmts, loans)
    return loans


def _collect_loop_body_loans(stmts: list[TpyStmt],
                             loans: dict[str, dict[str, BorrowKind]]) -> None:
    for stmt in stmts:
        # A nested def is a separate scope with its own rebind sites, exactly
        # as `collect_fact_kills` treats it.
        if isinstance(stmt, TpyNestedDef):
            continue
        bind = loan_bind_root(stmt)
        if bind is not None:
            holder, root, kind = bind
            loans.setdefault(root.split(".", 1)[0], {})[holder] = kind
        for body in stmt.sub_bodies():
            _collect_loop_body_loans(body, loans)


def check_alias_rebind_clobber(ctx: 'SemanticContext', name: str,
                               stmt: 'TpyVarDecl | TpyAssign') -> None:
    """Warn when rebinding `name` overwrites the storage a live loan reads,
    and record that `name`'s value has moved into its rebind slot.

    MUST run before `retarget_storage_borrows`, which is what drops the
    evidence: after it, a loan on the old generation has been repointed at
    the upstream source or dropped.
    """
    value = stmt.init if isinstance(stmt, TpyVarDecl) else stmt.value
    if value is None:
        return
    inner_value = value.expr if isinstance(value, TpyCoerce) else value
    # `p = None` stores a null handle; the object the loan reads is still
    # there. Probed on both storage models -- CPython-parity either way.
    if isinstance(inner_value, TpyNoneLiteral):
        return
    # Only a rebind that CONSTRUCTS into the storage can clobber it. An
    # lvalue rebind (`s = items[1]`, `g0, g1 = g1, g0`) reseats a pointer at
    # an object that lives elsewhere, so every loan on the old one survives.
    # This holds only because a pointer-classified local is the ONLY shape an
    # lvalue rebind reaches: on an owning frame slot every assignment
    # emplaces in place, and a loan would be clobbered there -- but codegen
    # refuses that combination today (the `res.alias_bind` /
    # `res.local_storage` rejects in `thir/lower/resumable.py`), so no body
    # that would need the owning arm compiles. Widening either reject means
    # revisiting this gate.
    if not is_rvalue_source(ctx, value):
        return
    scope = ctx.func.current_scope
    var_type = scope.lookup(name) if scope else None
    if var_type is None:
        return
    inner = unwrap_readonly(var_type)
    # A value type is copied at the rebind, so nothing aliases its storage;
    # str/bytes are value types with their own pinned-view tier besides.
    if inner.is_value_type() or view_family_for_type(inner) is not None:
        return

    generations = storage_generations(ctx, name)
    bt = ctx.func.borrow_tracker
    # From here on `name`'s value sits in its rebind slot, so loans taken
    # later are the ones the NEXT rebind clobbers.
    if generations == 2:
        bt.mark_slot_resident(name)
    live = stmt.live_names_after
    if not live:
        return
    clobbered: dict[str, BorrowKind] = {}
    prefix = name + "."
    for storage, holders in bt.loans.items():
        if storage != name and not storage.startswith(prefix):
            continue
        for holder, loan in holders.items():
            # The iterator borrow has its own mutating-while-iterating
            # warning, and OPAQUE carries no storage-identity by construction.
            if holder == ITER_BORROWER or loan.kind is BorrowKind.OPAQUE:
                continue
            if holder not in live:
                continue
            if generations == 1 or loan.residency is LoanResidency.SLOT:
                clobbered[holder] = loan.kind
    # A rebind inside a loop re-executes over a loan the body takes at any
    # position, so those holders sit in the slot by the next iteration even
    # when the walk has not registered them yet.
    if generations == 2:
        for body_loans in ctx.func.loop_body_loans:
            for holder, kind in body_loans.get(name, {}).items():
                if holder in live:
                    clobbered.setdefault(holder, kind)
    if not clobbered:
        return

    alias = sorted(clobbered)[0]
    if ctx.is_type_nocopy(var_type):
        fix = (f"both names share its storage and '{name}' cannot be copied; "
               f"bind the new value to a name of its own")
    elif clobbered[alias] is BorrowKind.ALIAS:
        fix = (f"both names share its storage; bind '{alias}' with "
               f"copy({name}), or bind the new value to a name of its own")
    else:
        # The loan points INTO the object, so `copy(name)` is not the
        # spelling that detaches it -- name only the remedy that always is.
        fix = ("both names share its storage; bind the new value to a name "
               "of its own")
    ctx.warning(
        f"'{alias}' will not keep the object it was given -- '{name}' is "
        f"rebound here and {fix}",
        stmt)
