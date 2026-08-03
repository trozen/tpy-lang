"""
TurboPython Semantic Analysis Context

Contains the shared state that is passed to all semantic analysis components.
"""

from __future__ import annotations
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass, field, replace
from enum import Enum
from typing import Any, Iterator, Literal, TYPE_CHECKING

from ..macro_loader import MacroRegistry
from ..parse.nodes import SourceLocation
from .value_range import ValueRange

if TYPE_CHECKING:
    from ..parse.type_resolver import TypeResolver

from ..typesys import (
    TpyType, TypeRegistry, ListLiteralInfo, DictLiteralInfo, SetLiteralInfo, ViewVarInfo, TypeParamKind, IntLiteralType,
    INT32, BIGINT, NominalType, ReadonlyType, OwnType, OptionalType, UnionType, TupleType,
    RecursiveAliasInstanceType, recursive_union_alternatives,
    PendingListType, PendingDictType, PendingSetType,
    PendingGenericInstanceType, PendingGenericInstanceInfo,
    ViewTypeFamily, PendingViewType, PendingStrType, VIEW_TYPE_FAMILIES,
    unwrap_readonly, unwrap_ref_type, unwrap_qualifiers,
    is_dyn_protocol, contains_pending_leaf,
)
from ..namespace import Namespace
from ..type_def_registry import int_traits_of
from ..parse import (
    TpyExpr, TpyStmt, TpyRecord, TpyFunction, TpyVarDecl, TpyMethodCall,
    TpyCall, TpyCoerce, TpyName, TpySubscript, TpyFieldAccess, TpyBinOp,
    TpyUnaryOp, TpyIfExpr, TpyTupleLiteral,
    TpyNestedDef, TpyNamedExpr,
)
from ..diagnostics import Diagnostic, DiagnosticLevel, SemanticError, Scope
from ..value_category import call_returns_cpp_ref

# Tuple of all pending container types -- use in isinstance checks so adding
# a new container type requires updating only this one constant.
PENDING_CONTAINER_TYPES = (PendingListType, PendingDictType, PendingSetType)


def addr_taken_roots(expr: TpyExpr) -> list[str]:
    """Return all variable names whose storage is potentially aliased by expr.

    Used to mark params as mutated when their address is taken (directly or
    implicitly). Handles or/and (TpyBinOp ||/&&) and ternary (TpyIfExpr),
    returning all possible roots across branches.
    """
    if isinstance(expr, TpyCoerce):
        return addr_taken_roots(expr.expr)
    if isinstance(expr, TpyName):
        return [expr.name]
    if isinstance(expr, TpySubscript):
        return addr_taken_roots(expr.obj)
    if isinstance(expr, TpyFieldAccess):
        return addr_taken_roots(expr.obj)
    if isinstance(expr, TpyBinOp) and expr.op in ("||", "&&"):
        return addr_taken_roots(expr.left) + addr_taken_roots(expr.right)
    if isinstance(expr, TpyIfExpr):
        return addr_taken_roots(expr.then_expr) + addr_taken_roots(expr.else_expr)
    return []


def tuple_borrow_escape_roots(expr: 'TpyExpr', tuple_bare: 'TupleType',
                              ro_tuple: bool) -> list[tuple[str, bool]]:
    """(root, grants_write) pairs for a borrow-form tuple escaping through a
    yield/return slot. A tuple literal borrows exactly its pointer-repr
    elements' roots (addr_taken_roots has no tuple-literal case; value
    elements are copied into the slot); a readonly slot or element records
    provenance without granting write access.
    """
    inner = expr.expr if isinstance(expr, TpyCoerce) else expr
    if isinstance(inner, TpyTupleLiteral):
        return [
            (root, not ro_tuple and not isinstance(
                tuple_bare.element_types[i], ReadonlyType))
            for i, el in enumerate(inner.elements)
            if i < len(tuple_bare.element_types)
            and TupleType._element_is_pointer_repr(tuple_bare.element_types[i])
            for root in addr_taken_roots(el)
        ]
    return [(root, not ro_tuple) for root in addr_taken_roots(expr)]


def _storage_key(expr: TpyExpr) -> str | None:
    """Extract storage key: 'name' or 'name.field' for single-level field access.

    Used by the borrow tracker as a dict key to identify storage that can be
    borrowed from or mutated.  Supports dotted paths so that ``self.items``
    and ``obj.field`` are tracked separately from ``self`` / ``obj``.
    """
    if isinstance(expr, TpyName):
        return expr.name
    if isinstance(expr, TpyFieldAccess) and isinstance(expr.obj, TpyName):
        return f"{expr.obj.name}.{expr.field}"
    return None


def _storage_root(key: str) -> str:
    """Strip the field suffix off a dotted storage key ('self.items' -> 'self').

    Inverse of `_storage_key`: callers that need to look up the variable name
    a dotted key was built from (param-name table, loop-var dict) use this.
    """
    dot = key.find(".")
    return key if dot == -1 else key[:dot]


def expr_yields_non_null_ptr(expr: TpyExpr, non_null_vars: set[str]) -> bool:
    """Whether evaluating `expr` produces a Ptr value with known non-null provenance.

    Three independent sources, all unified here so consumers (local-init,
    rebind, future return-site / expression-context uses) don't re-enumerate:

    1. Address-taking coercion (`&{e}` -- the `produces_non_null_ptr` flag on
       the Coercion). Covers `p: Ptr[T] = x` and the inheritance / @dynamic
       upcasts. A chain of coercions is walked: any non-address-taking outer
       coercion (e.g. the trivial `Ptr[T] -> Ptr[readonly[T]]`) is transparent
       and provenance is inherited from the inner expression.
    2. Ptr-returning call. Either the call's return type is itself Ptr-typed
       (`take_ptr(x)`, explicit `Ptr(x)` constructor) or the function carries
       the `@value_ptr_coercion` flag (its Ptr param coerces a T arg via `&`).
    3. Read from a name that earlier analysis already marked non-null.
    """
    while isinstance(expr, TpyCoerce):
        if expr.coercion.produces_non_null_ptr:
            return True
        expr = expr.expr
    if isinstance(expr, TpyCall) and expr.args:
        if expr.call_type is not None and expr.call_type.is_pointer():
            return True
        fi = expr.resolved_function_info
        if fi is not None and fi.value_ptr_coercion:
            return True
    if isinstance(expr, TpyName):
        return expr.name in non_null_vars
    return False


def _borrow_storage_root(expr: TpyExpr) -> str | None:
    """Extract the storage key whose storage is borrowed by this expression.

    Handles simple names (``items``), subscript on names or single-level
    field access (``items[i]``, ``self.items[i]``), and field access on
    names (``obj.field``).  Returns None for deeper nesting or rvalues
    (conservative -- no borrow registered).
    """
    if isinstance(expr, TpyCoerce):
        return _borrow_storage_root(expr.expr)
    if isinstance(expr, TpyName):
        return expr.name
    if isinstance(expr, TpySubscript):
        return _storage_key(expr.obj)
    if isinstance(expr, TpyFieldAccess):
        return _storage_key(expr)
    return None


class BorrowKind(Enum):
    """Kind of borrow relationship between a borrower and its storage."""
    ALIAS = "alias"       # whole-container alias (safe through mutations)
    FIELD = "field"       # field-level reference
    ITER = "iter"         # for-loop iterator
    ELEMENT = "element"   # subscript element reference
    PTR = "ptr"           # pointer into storage
    # Possible borrow of unknown shape: the callee's body (and so its
    # return_borrows_from fact) is not analyzed yet, so the bind is assumed
    # to borrow. Gates auto-move of the storage like any borrow, but carries
    # no element/alias semantics -- it must not feed mutation-invalidation
    # warnings or storage-identity (alias) chains.
    OPAQUE = "opaque"


# Restrictiveness order: PTR/ITER/ELEMENT > FIELD/OPAQUE > ALIAS. Used by
# retarget logic to promote a chained borrow to the most-restrictive kind in
# the chain (an ALIAS of an ELEMENT borrower is invalidated by structural
# mutation of the source container) and by FlowFacts borrow merging.
BORROW_KIND_RANK: dict[BorrowKind, int] = {
    BorrowKind.ALIAS: 0,
    BorrowKind.FIELD: 1,
    BorrowKind.OPAQUE: 1,
    BorrowKind.ITER: 2,
    BorrowKind.ELEMENT: 3,
    BorrowKind.PTR: 3,
}


class BorrowTracker:
    """Tracks borrow relationships between variables for mutation safety.

    Manages which variables borrow storage from other variables, enabling
    conflict detection when borrowed storage is mutated.

    Note: string view source tracking (str_source_borrows) and loop var
    mutation tracking (mutated_loop_vars) remain on SemanticContext since
    they reference string deduction and loop optimization state respectively.
    TODO: consider unifying all borrow-like tracking here if a broader
    "deferred type tracking" system emerges.
    """

    __slots__ = ('borrows', 'borrow_kinds')

    def __init__(self) -> None:
        self.borrows: dict[str, set[str]] = {}
        self.borrow_kinds: dict[tuple[str, str], BorrowKind] = {}

    def reset(self) -> None:
        """Clear all borrow state (called between function analyses)."""
        self.borrows.clear()
        self.borrow_kinds.clear()

    def add_borrow(self, storage: str, borrower: str, kind: BorrowKind = BorrowKind.ALIAS) -> None:
        """Record that ``borrower`` borrows from ``storage``."""
        self.borrows.setdefault(storage, set()).add(borrower)
        self.borrow_kinds[(storage, borrower)] = kind

    def remove_borrower(self, borrower: str) -> None:
        """Remove all borrows held by ``borrower`` (e.g. on reassignment)."""
        to_clean: list[str] = []
        for storage, borrowers in self.borrows.items():
            if borrower in borrowers:
                borrowers.discard(borrower)
                self.borrow_kinds.pop((storage, borrower), None)
                if not borrowers:
                    to_clean.append(storage)
        for storage in to_clean:
            del self.borrows[storage]

    def retarget_storage_borrows(self, storage: str) -> None:
        """Reassignment of ``storage``: retarget its borrowers to the upstream source.

        The reassigned variable is generated as a ``T*`` pointer-local in C++,
        so any borrower like ``view = storage`` aliases whatever ``storage``
        currently points into. After the reassignment we want chains to keep
        tracking the original container, not break silently.

        The retargeted borrow kind is the most-restrictive in the chain
        (PTR/ITER/ELEMENT > FIELD > ALIAS): an ALIAS of an ELEMENT borrower
        is still invalidated by structural mutation of the source container.

        Field-path borrows (``storage.*``) refer to the old object's fields
        and are dropped unconditionally -- the variable will be rebound to a
        different object, so those paths are unreachable.

        If ``storage`` itself was not borrowing anything (no upstream chain),
        its borrowers had nowhere to retarget to and are dropped.
        """
        upstream = self.borrow_source(storage)
        upstream_kind = self.borrow_kinds.get((upstream, storage)) if upstream else None

        borrowers = self.borrows.pop(storage, None)
        if borrowers:
            if upstream is not None and upstream_kind is not None:
                for b in borrowers:
                    child_kind = self.borrow_kinds.pop((storage, b), None)
                    if child_kind is None:
                        continue
                    # An OPAQUE borrow has no element/alias shape to promote:
                    # rank-promoting it into the invalidating set would mint
                    # exactly the mutation warnings the kind exists to avoid.
                    if child_kind is BorrowKind.OPAQUE:
                        promoted = BorrowKind.OPAQUE
                    else:
                        promoted = (child_kind
                                    if BORROW_KIND_RANK[child_kind] >= BORROW_KIND_RANK[upstream_kind]
                                    else upstream_kind)
                    existing = self.borrow_kinds.get((upstream, b))
                    if existing is None or BORROW_KIND_RANK[promoted] > BORROW_KIND_RANK[existing]:
                        self.borrows.setdefault(upstream, set()).add(b)
                        self.borrow_kinds[(upstream, b)] = promoted
            else:
                for b in borrowers:
                    self.borrow_kinds.pop((storage, b), None)

        # Field-path borrows (storage.X) are dropped: the variable rebinds to
        # a different object, so dotted-key borrows are unreachable.
        prefix = storage + "."
        to_remove = [k for k in self.borrows if k.startswith(prefix)]
        for k in to_remove:
            for b in self.borrows.pop(k):
                self.borrow_kinds.pop((k, b), None)

    def has_iter_borrow(self, storage_name: str) -> bool:
        """Check if a variable is borrowed by an active for-loop iterator."""
        borrowers = self.borrows.get(storage_name)
        return borrowers is not None and "__for_iter" in borrowers

    def has_element_borrow(self, storage_name: str) -> bool:
        """Check if a variable has element-level or iterator borrows.

        Returns True for borrows that can be invalidated by structural
        mutations (reallocation, insertion, deletion). Returns False for
        whole-container alias borrows which are safe through mutations.
        """
        borrowers = self.borrows.get(storage_name)
        if not borrowers:
            return False
        _INVALIDATING = (BorrowKind.ITER, BorrowKind.ELEMENT, BorrowKind.PTR)
        return any(
            self.borrow_kinds.get((storage_name, b)) in _INVALIDATING
            for b in borrowers
        )

    def has_borrow_of_kinds(self, storage: str, kinds: tuple[BorrowKind, ...]) -> bool:
        """Check if any borrower of storage has one of the given borrow kinds."""
        borrowers = self.borrows.get(storage)
        if not borrowers:
            return False
        return any(
            self.borrow_kinds.get((storage, b)) in kinds
            for b in borrowers
        )

    def has_borrowers_outside(self, storage: str, known_aliases: set[str]) -> bool:
        """Any borrower of ``storage`` (including its dotted field paths)
        not in ``known_aliases``. Bind-based aliases are modeled by
        last-use liveness itself (with dead-alias precision); every other
        borrower -- call-result borrows recorded from return_borrows_from,
        ``__for_iter`` iterator borrows -- is invisible to liveness, so
        moving ``storage`` is unsound while one exists.
        """
        prefix = storage + "."
        for key, borrowers in self.borrows.items():
            if key != storage and not key.startswith(prefix):
                continue
            for b in borrowers:
                # "__for_iter" never expires (loops don't remove it) and is
                # redundant here: an in-body consume of the iterable is kept
                # live by the loop fixpoint in liveness, and a post-loop
                # consume is safe -- gating on it would also block the
                # loop's own consuming-iteration activation.
                if b == "__for_iter":
                    continue
                if b not in known_aliases:
                    return True
        return False

    def effective_storage(self, name: str) -> str:
        """Resolve alias chains to find the underlying storage.

        If ``name`` is an alias of another variable, follows the chain
        (e.g. b -> a -> items) and returns the root storage.  Returns
        ``name`` itself when it is not an alias borrower.
        """
        visited: set[str] = {name}
        current = name
        while True:
            found = None
            for storage, borrowers in self.borrows.items():
                if current in borrowers and self.borrow_kinds.get((storage, current)) is BorrowKind.ALIAS:
                    found = storage
                    break
            if found is None or found in visited:
                return current
            visited.add(found)
            current = found

    def borrow_kind_of(self, name: str) -> 'BorrowKind | None':
        """Return the kind of borrow that 'name' holds, or None if not a borrower."""
        for storage, borrowers in self.borrows.items():
            if name in borrowers:
                return self.borrow_kinds.get((storage, name))
        return None

    def is_deferred_borrow(self, name: str) -> bool:
        """Return True if name ultimately traces back to a deferred ELEMENT borrow.

        A variable is deferred if it is an ELEMENT borrow, or an ALIAS/FIELD borrow
        of a deferred variable (transitively). PTR/ITER borrows are not deferred.
        Termination is guaranteed because borrow chains are acyclic.
        """
        kind = self.borrow_kind_of(name)
        if kind == BorrowKind.ELEMENT:
            return True
        if kind in (BorrowKind.ALIAS, BorrowKind.FIELD):
            source = self.borrow_source(name)
            if source is not None:
                return self.is_deferred_borrow(source)
        return False

    def borrow_source(self, name: str) -> str | None:
        """Return the direct borrow source of name (any borrow kind), or None.

        Unlike effective_storage (ALIAS-only), this finds the container for
        ITER/ELEMENT/FIELD/PTR borrows too -- used to trace loop-var addresses
        back to the source container.
        """
        for storage, borrowers in self.borrows.items():
            if name in borrowers:
                return storage
        return None

    def effective_storage_through_borrows(self, name: str) -> str:
        """Follow ALL borrow chains (ALIAS + ELEMENT + FIELD + PTR) to ultimate storage.

        Unlike effective_storage (ALIAS-only), this traverses the full chain
        so a write through an element ref can be traced back to its source param.
        For example: w ALIAS-borrows v, v ELEMENT-borrows items -> returns items.
        Cycle-safe via visited set.
        """
        visited: set[str] = {name}
        current = name
        while True:
            found = None
            for storage, borrowers in self.borrows.items():
                if current in borrowers:
                    found = storage
                    break
            if found is None or found in visited:
                return current
            visited.add(found)
            current = found

    def freeze(self) -> frozenset[tuple[str, str, BorrowKind]]:
        """Snapshot borrow state as immutable triples for flow analysis."""
        return frozenset(
            (storage, borrower, self.borrow_kinds[(storage, borrower)])
            for storage, borrowers in self.borrows.items()
            for borrower in borrowers
        )

    def restore_from_frozen(self, triples: frozenset[tuple[str, str, BorrowKind]]) -> None:
        """Restore borrow state from frozen triples."""
        self.borrows.clear()
        self.borrow_kinds.clear()
        for storage, borrower, kind in triples:
            self.borrows.setdefault(storage, set()).add(borrower)
            self.borrow_kinds[(storage, borrower)] = kind


def ephemeral_borrow_root(ephemeral_vars: set[str],
                          expr: 'TpyExpr | None') -> str | None:
    """Return the ephemeral-borrow var name `expr` reads from, else None.

    A bare ephemeral name, a walrus handing one out, or a field/subscript
    chain rooted in one (storing `x.field` retains a borrow into the same
    stale slot). A `.clone()` / copy-producing call breaks the borrow, so
    calls are not roots.
    """
    if isinstance(expr, TpyCoerce):
        return ephemeral_borrow_root(ephemeral_vars, expr.expr)
    if isinstance(expr, TpyNamedExpr):
        return ephemeral_borrow_root(ephemeral_vars, expr.value)
    # A ternary reads from whichever arm is taken -- ephemeral if either is.
    if isinstance(expr, TpyIfExpr):
        return (ephemeral_borrow_root(ephemeral_vars, expr.then_expr)
                or ephemeral_borrow_root(ephemeral_vars, expr.else_expr))
    if isinstance(expr, TpyName):
        return expr.name if expr.name in ephemeral_vars else None
    if isinstance(expr, (TpyFieldAccess, TpySubscript)):
        return ephemeral_borrow_root(ephemeral_vars, expr.obj)
    return None


def register_binding_borrow(ctx: 'SemanticContext', name: str,
                            init_expr: TpyExpr) -> None:
    """Register `name` as a borrower of `init_expr`'s storage root (ELEMENT /
    FIELD / ALIAS by init shape). Shared by the VarDecl and walrus binding
    paths.

    A self-assignment (t = t) aliases nothing new; registering it would put
    a self-edge in the borrow graph. For non-simple init shapes (ternary,
    deep chains like `outer.inner[i]`) there is no single root to record, so
    every address-taken root is eagerly marked mutated instead (the binding
    aliases into them, so they must stay `T&`, not `const T&`).

    8a.5: marking the source mutated is DEFERRED until the borrower is
    actually written through for ELEMENT borrows (v = items[i]) and for ALIAS
    borrows (b = y) -- a read-only alias must not force its source mutable. A
    genuine write through the borrower (b.x = 1, or passing b to a mutating
    callee) re-marks the source via mark_param_mutated's full borrow-chain
    follow, so deferral stays sound. FIELD borrows defer only when their root
    traces back to an ELEMENT borrow (checked transitively); PTR/ITER borrows
    and field aliases not rooted at an ELEMENT mark immediately.
    """
    init_unwrapped = (init_expr.expr if isinstance(init_expr, TpyCoerce)
                      else init_expr)
    root = _borrow_storage_root(init_expr)
    if root is None:
        for alias_root in addr_taken_roots(init_expr):
            ctx.mark_param_mutated(alias_root)
        return
    if root == name:
        return
    if isinstance(init_unwrapped, TpySubscript):
        kind = BorrowKind.ELEMENT
    elif isinstance(init_unwrapped, TpyFieldAccess):
        kind = BorrowKind.FIELD
    else:
        kind = BorrowKind.ALIAS
    bt = ctx.func.borrow_tracker
    bt.add_borrow(root, name, kind)
    if not (kind in (BorrowKind.ELEMENT, BorrowKind.ALIAS)
            or bt.is_deferred_borrow(root)):
        ctx.mark_param_mutated(root)


def record_stmt_borrow_binding(ctx: 'SemanticContext', name: str,
                               var_type: 'TpyType | None',
                               init_expr: TpyExpr) -> None:
    """Record a statement-level (var-decl / assign / walrus) borrow binding
    of a non-value local, with its const verdict. Codegen's branch pre-decl
    reads the accumulated fact to pick the pointer (alias) form for
    borrow-only names -- materialized here so the form discriminator is
    defined by sema, not re-derived by a codegen body walk."""
    if var_type is None or unwrap_readonly(var_type).is_value_type():
        return
    inner = init_expr
    while isinstance(inner, TpyCoerce):
        inner = inner.expr
    const = isinstance(ctx.get_expr_type(inner), ReadonlyType)
    if not const and isinstance(inner, TpyMethodCall):
        fi = inner.resolved_function_info
        const = bool(fi is not None and fi.is_readonly
                     and call_returns_cpp_ref(ctx, fi))
    # Operator dispatch mirrors the method-call arm (same rule as
    # _is_const_indirect): a readonly dunder's borrow return binds const,
    # so a branch pre-decl must pick the const pointer form.
    if not const and isinstance(inner, TpyBinOp) and inner.resolved_binop is not None:
        fi = inner.resolved_binop.method
        const = bool(fi.is_readonly and call_returns_cpp_ref(ctx, fi))
    if not const and isinstance(inner, TpyUnaryOp) and inner.resolved_unaryop is not None:
        fi = inner.resolved_unaryop.method
        const = bool(fi.is_readonly and call_returns_cpp_ref(ctx, fi))
    record_borrow_binding(ctx, name, const=const)


def record_borrow_binding(ctx: 'SemanticContext', name: str, *,
                          const: bool) -> None:
    """Record a borrow binding of a non-value local whose const verdict the
    caller already knows.

    For bindings that have no statement-level init expression to inspect -- a
    `with` target borrows `__enter__()`'s result, which is a property of the
    method, not of an expression in the body.
    """
    prev = ctx.func.stmt_borrow_decls.get(name, False)
    ctx.func.stmt_borrow_decls[name] = prev or const


class _ModuleInitSentinel:
    """Sentinel for module-level init context (not a real function, but not None either).

    Truthy so that `if ctx.func.current_function:` passes, but fails
    `isinstance(ctx.func.current_function, TpyFunction)` checks.
    """
    __slots__ = ()
    def __bool__(self) -> bool:
        return True
    def __repr__(self) -> str:
        return "<MODULE_INIT>"


MODULE_INIT_CONTEXT = _ModuleInitSentinel()


def is_body_like_scope(current_function: 'TpyFunction | _ModuleInitSentinel | None') -> bool:
    """True when the current context supports pending-type resolution.

    Real function bodies and ``MODULE_INIT_CONTEXT`` both have a
    ``FunctionTrackingState`` with ``pending_resolutions`` plumbed through
    and ``deduction.resolve_all()`` runs at the end of both (see
    ``analyzer._analyze_top_level``). Class bodies, registration-time
    contexts, and no-context states do not, so empty literals and similar
    late-typed constructs must reject there.
    """
    return isinstance(current_function, TpyFunction) or current_function is MODULE_INIT_CONTEXT


@dataclass
class RecordContext:
    """State for the record currently being analyzed (type params, bounds)."""
    record: TpyRecord | None = None
    type_params: list[str] | None = None
    type_param_kinds: list[TypeParamKind] | None = None
    type_param_bounds: dict[str, TpyType] | None = None


@dataclass(frozen=True, slots=True)
class BindingProvenance:
    """Per-local escape/ownership provenance: one record per local.

    Two merge lattices apply at flow joins (see
    flow_facts.merge_binding_provenance):

      * MUST facts (INTERSECT -- survive only if they hold on every path):
        `safe_to_return` and `param_derived`. Invariant: `param_derived`
        implies `safe_to_return` (safe is the documented superset, so an
        intersecting join that reaches via different safe sources keeps the
        local safe). Consulted by is_dangling_return / is_param_derived_expr.

      * HAZARD facts (UNION -- flagged if they hold on any path), all for
        tuple-typed locals: `owns_fresh_idx` (index of the first element that
        owns fresh non-value storage -- borrowing it across a yield/return
        dangles), `owning_storage` (bound from an owning-tuple call -- a
        borrow-form return would address into the dying local),
        `borrow_into_own_idxs` (plain-borrow elements -- REJECTED at a
        NAME->Own[T] slot) and `copies_into_own_idxs` (owned-by-reference
        elements -- WARNED, the copy-into-owned analog).

    Absent name == default record by construction: callers prune all-default
    records, so a name's membership and its facts stay equivalent under both
    lattices (an absent name contributes the default to every field).
    """

    safe_to_return: bool = False
    param_derived: bool = False
    owns_fresh_idx: int | None = None
    owning_storage: bool = False
    borrow_into_own_idxs: frozenset[int] = frozenset()
    copies_into_own_idxs: frozenset[int] = frozenset()
    # HAZARD (UNION): storage roots a borrow-form tuple local's element
    # pointers alias (terminal roots, pre-expanded at record time). The
    # mark functions trace a yield/return of the bare name through it.
    borrow_source_roots: frozenset[str] = frozenset()


_DEFAULT_PROVENANCE = BindingProvenance()


@dataclass
class FunctionTrackingState:
    """Per-function analysis state.

    Extracted from SemanticContext to keep that class from growing unbounded.
    Reset between functions and saved/restored for nested-def isolation.
    Accessed explicitly by callers via ``ctx.func.<field>``.
    """

    # --- Analysis state (per-function) ---
    current_scope: Scope | None = None
    current_function: TpyFunction | _ModuleInitSentinel | None = None
    current_ns: Namespace | None = None
    loop_depth: int = 0

    # --- try/except control flow (per-function: a nested def must not
    #     inherit the enclosing function's handler context, or its
    #     @error_return calls pass the must-handle check and emit gotos
    #     to labels outside the lambda) ---
    try_except_error_type: str | None = None
    in_except_tier: Literal["return", "throw"] | None = None
    # Whether the current except handler has an 'as e' binding (needed for return-tier re-raise)
    in_except_has_binding: bool = False
    # True when analyzing a finally body
    in_finally: bool = False
    # Stack (one frame per finally body being analyzed) of local names a
    # finally-deferred return in the corresponding try borrowed: `del` of
    # such a name inside the finally would free storage the pending return
    # still reads (CPython keeps the object alive via the stashed reference).
    pending_return_borrows: list[frozenset[str]] = field(default_factory=list)

    # --- List/dict/set literal tracking ---
    variable_to_literal: dict[str, int] = field(default_factory=dict)
    pending_resolutions: list[int] = field(default_factory=list)
    pre_analyzed_method_args: dict[int, list[TpyType]] = field(default_factory=dict)
    variable_to_dict_literal: dict[str, int] = field(default_factory=dict)
    pending_dict_resolutions: list[int] = field(default_factory=list)
    variable_to_set_literal: dict[str, int] = field(default_factory=dict)
    pending_set_resolutions: list[int] = field(default_factory=list)
    # Comprehension/genexpr nodes cache their element/key/value type in a
    # snapshot field (result_elem_type etc.) taken during analysis. When that
    # snapshot is a Pending* container type (a list-literal element), the
    # deferred resolver only updates the element node's type, leaving the
    # snapshot stale -- it must be finalized from the registry after
    # resolve_all. Recorded as (node, attr_name) pairs.
    pending_elem_type_fields: list[tuple[object, str]] = field(default_factory=list)
    # Expression nodes whose cached `expr_types` entry holds a Pending* leaf
    # nested in a composite (`list[Pending]` from a non-array comprehension,
    # `tuple[Pending,...]`). Recorded at set_expr_type so the finalization pass
    # rewrites only these nodes -- never a sweep over the module-wide cache.
    pending_composite_exprs: list[object] = field(default_factory=list)
    # Branch-decl snapshot dicts (the `if_branch_decls` values recorded by
    # this function's branch producers). The snapshots capture binding types
    # BEFORE the deferred container resolution, so resolve_all must finalize
    # each registered map or a Pending* leaf reaches codegen's to_cpp().
    pending_branch_decl_maps: list[dict] = field(default_factory=list)

    # --- Pending generic instance tracking ---
    pending_generic_instances: dict[int, PendingGenericInstanceInfo] = field(default_factory=dict)
    variable_to_generic_instance: dict[str, int] = field(default_factory=dict)

    # --- String local tracking ---
    variable_to_str_var: dict[str, int] = field(default_factory=dict)
    str_source_borrows: dict[str, set[int]] = field(default_factory=dict)
    pending_str_resolutions: list[int] = field(default_factory=list)

    # --- Bytes local tracking ---
    variable_to_bytes_var: dict[str, int] = field(default_factory=dict)
    bytes_source_borrows: dict[str, set[int]] = field(default_factory=dict)
    pending_bytes_resolutions: list[int] = field(default_factory=list)

    # --- Pinned-view alias tracking (StrView/BytesView annotations) ---
    # source_name -> set of pinned-view borrower names. Pending views fall
    # back to the owned type via source_mutated; pinned views can't, so we
    # warn at source reassignment that the view dangles.
    pinned_view_aliases: dict[str, set[str]] = field(default_factory=dict)

    # --- Control flow ---
    super_init_call: TpyMethodCall | None = None
    super_del_call: TpyMethodCall | None = None
    pending_loop_vars: dict[str, tuple[TpyType, TpyStmt, TpyStmt | None]] = field(default_factory=dict)
    loop_vars: set[str] = field(default_factory=set)
    mutated_loop_vars: set[str] = field(default_factory=set)
    consumed_loop_vars: set[str] = field(default_factory=set)
    deferred_loop_copy_warnings: dict[str, list[int]] = field(default_factory=dict)
    loop_var_iterable: dict[str, str] = field(default_factory=dict)
    # True while analyzing the argument of an explicit copy(...) call --
    # copy-divergence warnings (e.g. dict.get(k, default)) are suppressed,
    # the wrap being the acknowledgment spelling.
    in_copy_call_arg: bool = False

    # --- Scope escape tracking ---
    var_scope_depth: dict[str, int] = field(default_factory=dict)
    hoisted_vars: set[str] = field(default_factory=set)
    rvalue_vars: set[str] = field(default_factory=set)
    owned_locals: set[str] = field(default_factory=set)
    # Accumulator: all locals that were ever owned. Survives FlowFacts
    # save/restore (not in FlowFacts). Used to compute the exported
    # movable_locals set at function end.
    ever_owned_locals: set[str] = field(default_factory=set)
    # Statement-level borrow bindings (var-decl / assign / walrus):
    # name -> any binding had a readonly/const source. A name bound ONLY
    # this way (never a fresh rvalue, no non-statement binding) takes the
    # pointer (alias) form at branch pre-decls. Accumulator like
    # ever_owned_locals -- survives FlowFacts restores.
    stmt_borrow_decls: dict[str, bool] = field(default_factory=dict)
    # Names bound by non-statement binding kinds (with-as, for-loop var,
    # match capture): their binding machinery owns the storage form, so
    # they are never pointer-form-eligible at branch pre-decls.
    nonstmt_bound_names: set[str] = field(default_factory=set)
    # Names bound by a construct rather than a statement that are nonetheless
    # borrow bindings: an explicit exception to nonstmt_bound_names so the borrow
    # snapshot keeps them pointer-form-eligible regardless of prescan/full-pass
    # ordering. Two members -- a match capture aliasing an lvalue subject, and a
    # `with` target (which borrows `__enter__()`'s result).
    nonstmt_borrow_bindings: set[str] = field(default_factory=set)
    # Accumulator (survives FlowFacts like ever_owned_locals): locals
    # reassigned from a borrow-producing source, so no longer safely movable
    # even if they were owned earlier. Subtracted from the movable set.
    borrow_reassigned_vars: set[str] = field(default_factory=set)
    move_through_vars: set[str] = field(default_factory=set)

    # --- Prescan / last-use ---
    current_reassigned_vars: set[str] = field(default_factory=set)
    # Locals whose sole binding is a fresh constructor call of their exact static
    # type (never rebound -- field mutation doesn't count). Their dynamic type is
    # provably their static type, so the polymorphic-slicing guard may move them
    # into an owned poly slot (`Box[P]`) without slicing, exactly like a
    # fresh-rvalue ctor at the store site.
    current_fresh_ctor_locals: set[str] = field(default_factory=set)
    # Fresh str/bytes view tuple-unpack targets, candidates for owned-promotion
    # if they later get hoisted out of a branch (a view would then outlive its
    # source __tup temp / branch-local by-ref source). Consumed at the
    # branch-predecl sites. Per-function; reset with the prescan sets.
    tuple_unpack_view_targets: set[str] = field(default_factory=set)
    current_lvalue_reassigned: set[str] = field(default_factory=set)
    current_aug_assigned_vars: set[str] = field(default_factory=set)
    # alias -> source for simple name-init locals (prescan alias_sources).
    # Consulted when invalidating field facts: a mutation through one name
    # of an alias group invalidates facts rooted at every member.
    current_alias_sources: dict[str, str] = field(default_factory=dict)
    # alias -> root for field/subscript-chain-init locals (prescan
    # chain_alias_sources). Together with current_alias_sources these are
    # the borrowers last-use liveness models itself; the auto-move gate
    # only demotes on borrowers OUTSIDE this set.
    current_chain_alias_sources: dict[str, str] = field(default_factory=dict)

    # --- Definite-assignment tracking ---
    definitely_assigned: set[str] = field(default_factory=set)
    init_terminated: bool = False
    narrowed_types: dict[str, TpyType] = field(default_factory=dict)

    # --- Reassignment inference tracking ---
    literal_default_vars: set[str] = field(default_factory=set)
    literal_values: dict[str, list[int]] = field(default_factory=dict)
    unresolved_none_vars: set[str] = field(default_factory=set)
    write_history: dict[str, list[tuple[TpyType, TpyExpr]]] = field(default_factory=dict)
    authoritative_types: dict[str, TpyType] = field(default_factory=dict)
    authoritative_type_lines: dict[str, int] = field(default_factory=dict)
    # Locals whose type was retroactively promoted from the literal-seeded
    # default to a fixed-int target by a typed-slot use (ARG, RETURN, INIT,
    # ASSIGN, SETITEM, FIELD, dict-key). Maps name to the loc of the use
    # that triggered the promotion -- surfaced in later type-mismatch
    # errors when a subsequent use disagrees with the locked type, and
    # consulted by _analyze_assign to refresh stale existing_type when
    # the RHS triggered the promotion of the LHS.
    retro_widened_locs: dict[str, 'SourceLocation | None'] = field(default_factory=dict)

    # --- Global declaration tracking ---
    global_declarations: set[str] = field(default_factory=set)

    # --- Nested def tracking ---
    in_nested_def: bool = False
    nested_def_name: str | None = None
    # Inside a nested-def analysis: 'self' in outer_scope_locals is the
    # enclosing METHOD's receiver (not an ordinary local named self), so
    # assignment sites can reject rebinds with the receiver message.
    outer_self_is_receiver: bool = False
    outer_scope_locals: set[str] = field(default_factory=set)
    current_nonlocal_names: set[str] = field(default_factory=set)
    # Union of nonlocal targets across all nested defs analyzed so far in
    # this function: any later call may invoke such a closure and rebind
    # these names, so check-elision facts for them die at every call site.
    closure_written_names: set[str] = field(default_factory=set)
    nested_def_names: set[str] = field(default_factory=set)
    nested_def_escapes: set[str] = field(default_factory=set)
    nested_def_nodes: dict[str, 'TpyNestedDef'] = field(default_factory=dict)
    # Mutation marks attempted while analyzing a nested-def body (recorded on
    # the NESTED tracking state, which is otherwise discarded on restore).
    # Entries are (name, through_field, structural). _analyze_nested_def
    # replays the captured/nonlocal/self subset into the enclosing state so
    # closure mutations reach the method's const/param-mutation facts.
    nested_mutation_marks: list[tuple[str, bool, bool]] = field(default_factory=list)

    # --- Per-local escape/ownership provenance (see BindingProvenance) ---
    # One record per local; absent name == default. Mutate only via the
    # bp_* helpers below so all-default records stay pruned (the lattices in
    # flow_facts rely on absent == default).
    binding_provenance: dict[str, BindingProvenance] = field(default_factory=dict)
    # Loop vars / next() results bound from a frame-slot-rooted borrow yield
    # (a generator / genexpr / Iterator[T] source, T non-value): the borrow is
    # valid only until the next iteration step (the generator frame slot is
    # overwritten on each __next__()), so retaining it past the step is unsound.
    # These are kept OUT of the safe-to-return provenance and rejected at escape
    # sites (return / store / container insert / closure capture / yield onward).
    # Active only during the consuming loop body (added before, discarded after).
    ephemeral_borrow_vars: set[str] = field(default_factory=set)
    # Borrow-yield rooting checks deferred until `func.generator_locals` is
    # populated: a yielded frame-resident local is a valid borrow root, but the
    # check runs during body analysis, before the frame-local set exists.
    # Entries are (yielded_expr, elem_type, loc).
    pending_yield_root_checks: list[tuple[TpyExpr, TpyType, 'SourceLocation | None']] = field(default_factory=list)
    non_null_ptr_vars: set[str] = field(default_factory=set)
    # Narrowing accumulated during a single statement's expression analysis;
    # flushed into non_null_ptr_vars at the statement boundary. Deferred so
    # sibling accesses within an unspecified-order expression (e.g. `p.x + p.y`,
    # `p.x = p.x + 1`) keep their individual checks -- C++ leaves arithmetic
    # and RHS-vs-LHS sub-expression order unspecified (C++17 only sequences
    # RHS-before-LHS for the whole assignment, not for sub-expressions),
    # so within-statement elision would be unsafe.
    pending_non_null_ptr_vars: set[str] = field(default_factory=set)

    # --- Consumed variable tracking ---
    consumed_vars: set[str] = field(default_factory=set)

    # Owned-erased coroutine locals bound in this body and not read since
    # (any read clears the entry -- every legal read is a consumption or a
    # manual-driving borrow). Survivors warn at body end: a bound coroutine
    # never consumed is destroyed without running.
    unread_coro_locals: dict[str, 'TpyStmt'] = field(default_factory=dict)

    # --- Variable declaration tracking (per-function) ---
    var_decl_by_name: dict[str, 'TpyVarDecl'] = field(default_factory=dict)
    # Resolved type of each name's FIRST declaration, kept even after the
    # declaring block's scope is gone -- a later assignment in an enclosing
    # scope is the same Python local and must still satisfy the one-type rule.
    first_decl_types: dict[str, TpyType] = field(default_factory=dict)

    # --- Integer value range tracking ---
    value_ranges: dict[str, 'ValueRange'] = field(default_factory=dict)

    # --- Borrow tracking ---
    borrow_tracker: BorrowTracker = field(default_factory=BorrowTracker)

    # --- Parameter mutation inference ---
    current_param_names: set[str] = field(default_factory=set)
    current_param_name_to_idx: dict[str, int] = field(default_factory=dict)
    current_mutated_param_names: set[str] = field(default_factory=set)
    # Method type params whose `U: T` bound was used representationally in the
    # body (e.g. `Ptr[U] -> Ptr[T]` coercion). Drained to FunctionInfo at body
    # end; codegen reads it at call sites to decide adapter-wrap for structural
    # conformers.
    current_representational_params: set[str] = field(default_factory=set)
    current_rebound_params: set[str] = field(default_factory=set)
    current_call_edges: list = field(default_factory=list)
    current_self_mutated: bool = False
    current_self_struct_mutated: bool = False
    current_struct_mutated_param_names: set[str] = field(default_factory=set)
    current_returned_param_names: set[str] = field(default_factory=set)
    current_consumed_own_params: set[str] = field(default_factory=set)
    # Params whose address has been observed escaping into a mutable Ptr[T]
    # field via `FIELD = PARAM`. Finalized to FunctionInfo.addr_escapes_params
    # at body-analysis end; consumed by param-signature codegen to suppress
    # the `const T&` default so the `&param -> T*` store type-checks.
    current_addr_escape_param_names: set[str] = field(default_factory=set)
    # Awaited sub-frame FunctionInfos for Send/Sync frame classification
    # (sema/frame_traits.py). A None entry is an await whose operand frame sema
    # cannot classify (Task / structural awaitable) -- forces non-Send.
    current_awaited_subframes: list = field(default_factory=list)

    # --- BindingProvenance accessors ---
    # Reads default-fill from an absent name; writes go through _bp_update,
    # which prunes a record back to absence once every field is default so
    # "absent == default" holds for the flow-merge lattices.

    def _bp_update(self, name: str, **changes: object) -> None:
        cur = self.binding_provenance.get(name, _DEFAULT_PROVENANCE)
        new = replace(cur, **changes)
        if new == _DEFAULT_PROVENANCE:
            self.binding_provenance.pop(name, None)
        else:
            self.binding_provenance[name] = new

    def bp_is_param_derived(self, name: str) -> bool:
        bp = self.binding_provenance.get(name)
        return bp is not None and bp.param_derived

    def bp_is_safe_to_return(self, name: str) -> bool:
        bp = self.binding_provenance.get(name)
        return bp is not None and bp.safe_to_return

    def bp_set_param_derived(self, name: str, value: bool) -> None:
        # param_derived implies safe_to_return (BindingProvenance invariant):
        # raising param also raises safe so the two can't diverge at a setter.
        if value:
            self._bp_update(name, param_derived=True, safe_to_return=True)
        else:
            self._bp_update(name, param_derived=False)

    def bp_set_safe_to_return(self, name: str, value: bool) -> None:
        # Clearing safe must clear param too, or the implication above breaks.
        if value:
            self._bp_update(name, safe_to_return=True)
        else:
            self._bp_update(name, safe_to_return=False, param_derived=False)

    def bp_add_loop_var_provenance(self, name: str) -> None:
        self._bp_update(name, param_derived=True, safe_to_return=True)

    def bp_remove_loop_var_provenance(self, name: str) -> None:
        self._bp_update(name, param_derived=False, safe_to_return=False)

    def bp_owns_fresh_idx(self, name: str) -> int | None:
        bp = self.binding_provenance.get(name)
        return bp.owns_fresh_idx if bp is not None else None

    def bp_is_owning_storage(self, name: str) -> bool:
        bp = self.binding_provenance.get(name)
        return bp is not None and bp.owning_storage

    def bp_borrow_into_own_idxs(self, name: str) -> frozenset[int]:
        bp = self.binding_provenance.get(name)
        return bp.borrow_into_own_idxs if bp is not None else frozenset()

    def bp_copies_into_own_idxs(self, name: str) -> frozenset[int]:
        bp = self.binding_provenance.get(name)
        return bp.copies_into_own_idxs if bp is not None else frozenset()

    def bp_set_tuple_member(
        self,
        name: str,
        *,
        owns_fresh_idx: int | None,
        owning_storage: bool,
        borrow_into_own_idxs: frozenset[int],
        copies_into_own_idxs: frozenset[int],
        borrow_source_roots: frozenset[str],
    ) -> None:
        # All tuple-member hazard fields are (re)derived together per
        # binding -- replacing them atomically preserves the rebind discipline
        # (`t = t` re-installs its own facts) while leaving the return-safety
        # fields, managed separately, untouched.
        self._bp_update(
            name,
            owns_fresh_idx=owns_fresh_idx,
            owning_storage=owning_storage,
            borrow_into_own_idxs=borrow_into_own_idxs,
            copies_into_own_idxs=copies_into_own_idxs,
            borrow_source_roots=borrow_source_roots,
        )

    def bp_borrow_source_roots(self, name: str) -> frozenset[str]:
        bp = self.binding_provenance.get(name)
        return bp.borrow_source_roots if bp is not None else frozenset()


@dataclass
class SemanticContext:
    """Shared state for all semantic analysis components.

    Per-function tracking state is held in ``func`` (FunctionTrackingState).
    Callers access it explicitly as ``ctx.func.<field>`` so the split between
    module-wide state and per-function state is visible at every use site.
    """

    # --- Core ---
    registry: TypeRegistry
    global_scope: Scope
    default_int_type: TpyType = field(default_factory=lambda: INT32)
    macro_registry: MacroRegistry | None = None

    # --- Per-function state ---
    # Accessed explicitly by callers as ``ctx.func.<field>``.
    func: FunctionTrackingState = field(default_factory=FunctionTrackingState)

    # --- Record context ---
    record_ctx: RecordContext = field(default_factory=RecordContext)

    # --- Type cache ---
    expr_types: dict[int, TpyType] = field(default_factory=dict)
    var_types: dict[int, TpyType] = field(default_factory=dict)

    # --- Literal tracking (counters + registries persist across functions) ---
    literal_counter: int = 0
    list_literals: dict[int, ListLiteralInfo] = field(default_factory=dict)
    dict_literals: dict[int, DictLiteralInfo] = field(default_factory=dict)
    set_literals: dict[int, SetLiteralInfo] = field(default_factory=dict)
    pending_generic_counter: int = 0
    str_var_counter: int = 0
    str_vars: dict[int, ViewVarInfo] = field(default_factory=dict)
    bytes_var_counter: int = 0
    bytes_vars: dict[int, ViewVarInfo] = field(default_factory=dict)

    # --- Test annotation facts (persist across functions) ---
    declared_var_types: dict[tuple[int, str], TpyType] = field(default_factory=dict)
    ptr_deref_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    subscript_bounds_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    div_zero_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    cast_safe_facts: dict[tuple[int, str], bool] = field(default_factory=dict)
    # (def line, func name) -> FunctionInfo, for # tpyc: frame_send/frame_sync.
    # Holds the fi, not the answer -- frame traits resolve lazily because
    # awaited sub-frames may belong to bodies analyzed later.
    frame_fact_fns: dict = field(default_factory=dict)

    # --- Import tracking ---
    imports: dict[str, set[tuple[str, str]] | None | str] = field(default_factory=dict)
    bare_module_imports: set[str] = field(default_factory=set)
    imported_names: dict[str, tuple[str, str]] = field(default_factory=dict)

    # --- Cross-module support ---
    module_name: str = "__main__"
    # File-derived module name used by codegen for namespaces. Identical to
    # `module_name` for non-entry modules; for the entry point, codegen uses
    # the file name while `module_name` stays "__main__" for runtime
    # `__name__` semantics. Set alongside `module_name` in `bind_imports`;
    # consumed by `_register_union_wrappers` so the wrapper-index origin
    # matches the comparison codegen does against its own `module_name`.
    cpp_module_name: str = "__main__"
    module_cpp_namespace: str | None = None  # from # tpy: cpp_namespace directive
    # The current module's opaque plugin payload (`FrontendModule.macro_data`,
    # `TpyModule.macro_data`). Stashed per-module in `bind_imports` so a
    # `@call_macro` -- which runs during Pass 7 with no module passed to it --
    # can reach it via `CallMacroContext.module_data`. None for parser-produced
    # modules. (Function macros instead receive it as an explicit argument.)
    macro_data: 'Any' = None
    # Reference to the current module's `CompiledModule.exports`. Set by
    # `Compiler._finalize_declarations` so the registration paths can
    # find pre-populated skeleton RecordInfo / FunctionInfo /
    # ProtocolInfo / enum NominalType objects (created by
    # `_pre_populate_decl_exports`) and mutate them in place rather than
    # allocating new ones. Peer modules' analyzer registries, populated
    # by `bind_imports` via `module_info.{records,functions,...}`,
    # capture references to the same skeletons; in-place mutation lets
    # those peer registries see the freshly-finalized data without a
    # post-hoc resync. None when no pre-populated exports are
    # attached (e.g. ad-hoc analyzer construction in tests / REPL).
    module_decl_exports: 'object | None' = None
    # Reference to the current module's per-module attribute table
    # (aliased from `CompiledModule.module_attributes`). Registration
    # paths install bindings here as they mint/adopt records,
    # functions, protocols, enums, variables, type aliases, and
    # imports. None when no compiler-provided table is attached
    # (ad-hoc analyzer construction in tests / REPL), in which case
    # `install_binding` no-ops.
    module_attributes: 'dict | None' = None
    top_level_decls: dict[str, int] = field(default_factory=dict)
    # Defining modules this module's code references. Populated by
    # sema.reach_analysis after analysis completes; consumed by codegen
    # to drive transitive include emission.
    reached: set[str] = field(default_factory=set)

    # --- Final globals ---
    final_globals: set[str] = field(default_factory=set)
    analyzed_finals: set[str] = field(default_factory=set)

    # --- Builtins ---
    builtin_names: dict[str, TpyType] = field(default_factory=dict)

    # --- Namespace hierarchy (global/builtins persist) ---
    builtins_ns: Namespace | None = None
    macro_ns: Namespace | None = None
    global_ns: Namespace | None = None

    # --- Macro dep modules (populated after macros run) ---
    macro_dep_modules: set[str] = field(default_factory=set)

    # --- Recursive union aliases (self- or mutually-referencing) ---
    recursive_union_names: set[str] = field(default_factory=set)

    # --- Module resolver (parser-owned) ---
    # The `TypeResolver` the parser built for this module.  Wired by
    # `sema.analyzer.analyze()` from `module.resolver`.  Consumed by:
    #   - `_infer_field_type_from_default` to look up same-module
    #     records via the parser's registry (not yet in `ctx.registry`
    #     when the field-default pass runs).
    #   - `register_record`'s macro post-resolve step (resolves
    #     TypeRefNodes in bodies of methods added by class macros,
    #     which join the record after the module-level `resolve_refs`
    #     walk has already finished).
    # No other sema code should touch this; the main parser ->
    # TpyType binding happens via `parse.resolve_refs.resolve_refs`.
    parser_resolver: 'TypeResolver | None' = None

    # --- Control flow (persistent) ---
    in_comprehension: int = 0
    sc_and_walrus: set[str] = field(default_factory=set)
    sc_or_walrus: set[str] = field(default_factory=set)
    is_top_level: bool = False
    # REPL mode: allow @error_return calls at top level (unwrap with panic)
    allow_top_level_error_unwrap: bool = False
    # Re-entry guard for `is_type_nocopy` on a `RecursiveAliasInstanceType`:
    # the wrapper's own self-reference (`list[Tree[T]]` member) would otherwise
    # recurse into Tree[T] forever. Conservative False at the recursion point
    # mirrors `RecursiveAliasInstanceType.is_value_type`'s guard in typesys.
    _evaluating_alias_nocopy: set = field(default_factory=set)
    # Re-entry guard for `is_type_non_copyable`'s record-field walk: a
    # self-/mutually-referential record (`children: list[Node]`) would recurse
    # forever. Conservative False at the recursion point, mirroring the alias
    # guard above. is_type_nocopy needs no such set -- it walks type args only.
    _evaluating_record_noncopyable: set = field(default_factory=set)

    # --- Last-use tracking (shared with codegen, persists across functions) ---
    all_last_uses: set[int] = field(default_factory=set)
    # id(TpyName) of `return <name>` values under a non-suspending finally
    # (liveness.collect_finally_return_candidates; every such return -- the
    # finally can reach the local through aliases/closures, so candidacy is
    # structural, not read-based). Return analysis re-marks eligible
    # reference-type shapes as last-use and stamps
    # TpyReturn.finally_deferred_capture (codegen then materializes the
    # return value after the inline finally chain).
    finally_return_candidates: set[int] = field(default_factory=set)

    # id(FunctionInfo) of this module's bodied functions/methods whose body
    # analysis has not run yet -- their return_borrows_from is still None
    # for ordering reasons, not because they cannot borrow. A call-result
    # bind from one of these registers a conservative OPAQUE borrow; once
    # the body is analyzed the fact becomes a frozenset and the set entry
    # is naturally inert (the None check short-circuits first).
    pending_borrow_fact_fis: set[int] = field(default_factory=set)

    # --- Consuming method tracking ---
    in_consuming_method: bool = False

    # --- Expression type hint ---
    expr_type_hint: TpyType | None = None

    # --- Branch-declared variable tracking ---
    if_branch_decls: dict[int, dict[str, TpyType]] = field(default_factory=dict)

    # --- Extern symbol tracking ---
    extern_symbols: dict[str, str] = field(default_factory=dict)

    # --- Builder-trace fresh-name counters ---
    # hint -> next index. Shared across BuilderTraceExpander instances
    # so two functions that each open an ArgumentParser trace don't
    # both mint __tpy_builder_argparse_args_1, which would collide as
    # a top-level record/function name in the synthesized module.
    builder_trace_fresh_counters: dict[str, int] = field(default_factory=dict)

    # --- Diagnostics ---
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def is_readonly_name(self, name: str) -> bool:
        """Check if a variable has readonly provenance in its declared scope type.

        isinstance narrowing updates narrowed_types (not scope bindings), so the
        scope binding preserves ReadonlyType even when the expr type is narrowed
        to a concrete member. Reassignment updates the scope binding, so a
        non-readonly reassignment correctly clears this.
        """
        if self.func.current_scope is None:
            return False
        declared = self.func.current_scope.lookup(name)
        return declared is not None and isinstance(declared, ReadonlyType)

    def _resolve_loc(self, node: TpyExpr | TpyStmt | TpyRecord | None) -> SourceLocation | None:
        """Resolve source location from a node, with fallback to current function/record."""
        loc = getattr(node, 'loc', None) if node else None
        if loc is None and isinstance(self.func.current_function, TpyFunction):
            loc = self.func.current_function.loc
        if loc is None and self.record_ctx.record is not None:
            loc = self.record_ctx.record.loc
        return loc

    def error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> SemanticError:
        """Create a SemanticError with location from a node."""
        return SemanticError(message, self._resolve_loc(node))

    def reject_resumable_nested_def_escape(
            self, name: str, node: 'TpyExpr | TpyStmt | None') -> None:
        """A nested def in an async def / generator is a member function of
        the resumable frame -- it cannot be returned or handed off as a
        value (the callable would dangle once the frame is gone, and a
        member function has no standalone C++ value form). Call it
        directly. No-op in plain sync functions (lambda form escapes fine).
        """
        func = self.func.current_function
        if not isinstance(func, TpyFunction) or not (func.is_async
                                                     or func.is_generator):
            return
        kind = "an async function" if func.is_async else "a generator"
        raise self.error(
            f"nested function '{name}' defined in {kind} cannot escape or "
            f"be passed as a value (it lives on the coroutine frame); call "
            f"it directly", node)

    def emit_error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> None:
        """Record an error diagnostic without raising (allows continued analysis)."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.ERROR, message, self._resolve_loc(node)))

    def warning(self, message: str, node: TpyExpr | TpyStmt | TpyRecord | None = None) -> None:
        """Record a warning diagnostic (doesn't stop compilation)."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, self._resolve_loc(node)))

    def warning_from_loc(self, message: str, loc: 'SourceLocation | None') -> None:
        """Record a warning diagnostic from a SourceLocation."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, loc))

    def collapse_duplicate_diagnostics(self) -> None:
        """Drop exact repeats, keeping each diagnostic's first occurrence.

        A body analyzed more than once reports its diagnostics once. Bodies are
        cloned wherever one source construct expands into several ordinary ones
        -- `except (A, B):` into one handler per type, `@auto_readonly` into a
        mutable/const method pair -- so each clone re-analyzes the same lines.

        Collapsing happens here, once analysis is over, rather than in the
        recording methods above: during analysis, callers index into
        `diagnostics` (`compatibility.py` records the position of a loop-copy
        warning for `statements.py` to delete once the loop proves to move
        rather than copy). A recorder that skipped a duplicate would shift
        those positions onto unrelated diagnostics.
        """
        seen: set[tuple[object, ...]] = set()
        kept: list[Diagnostic] = []
        for d in self.diagnostics:
            loc = d.loc
            key = ((d.level, d.message, loc.file, loc.line, loc.column) if loc
                   else (d.level, d.message))
            if key in seen:
                continue
            seen.add(key)
            kept.append(d)
        self.diagnostics[:] = kept

    def nocopy_reason(self, typ: TpyType) -> str:
        """Return a human-readable reason why a type is nocopy.

        For explicitly @nocopy types returns "@nocopy type 'X'".
        For implicitly nocopy types (propagated from fields) returns a message
        explaining which field caused it.
        """
        inner = unwrap_readonly(typ)
        if isinstance(inner, OwnType):
            inner = inner.wrapped
        record = self.registry.get_record_for_type(inner)
        if record is None:
            return f"non-copyable type '{typ}'"
        # Walk fields to find the nocopy one (implicitly propagated)
        for f in record.fields:
            if self.is_type_nocopy(f.type):
                return (
                    f"non-copyable type '{typ}' (field '{f.name}' "
                    f"has non-copyable type '{f.type}')"
                )
        # Check type arguments for generic instantiations
        if isinstance(inner, NominalType) and inner.type_args:
            if record is None or not record.has_copy:
                for arg in inner.type_args:
                    if isinstance(arg, TpyType) and self.is_type_nocopy(arg):
                        return (
                            f"non-copyable type '{typ}' (type argument "
                            f"'{arg}' is non-copyable)"
                        )
        # Check parents
        for p in record.parents:
            parent_rec = self.registry.get_record_for_type(p)
            if parent_rec is not None and parent_rec.is_nocopy:
                return (
                    f"non-copyable type '{typ}' (parent '{p}' "
                    f"is non-copyable)"
                )
        # Explicitly decorated with @nocopy (or builtin nocopy)
        return f"@nocopy type '{typ}'"

    def is_type_nocopy(self, typ: TpyType) -> bool:
        """Check if a type is nocopy, recursively unwrapping wrappers and generics.

        For generic instantiations like Box[NocopyType], checks whether any
        type argument is nocopy. Respects __copy__ escape hatch: if the
        containing record defines __copy__, type-arg nocopy is suppressed.
        """
        if isinstance(typ, ReadonlyType):
            return self.is_type_nocopy(typ.wrapped)
        if isinstance(typ, OwnType):
            return self.is_type_nocopy(typ.wrapped)
        if isinstance(typ, OptionalType):
            return self.is_type_nocopy(typ.inner)
        if isinstance(typ, TupleType):
            for e in typ.element_types:
                if isinstance(e, TpyType) and self.is_type_nocopy(e):
                    return True
            return False
        # Generic recursive alias instance: a @nocopy alternative makes the
        # wrapper's std::variant non-copyable. Mirrors the TupleType walk
        # above; uses the unified alternatives accessor. The recursive
        # alternative (e.g. `list[Tree[T]]`) re-enters Tree[T] -- conservative
        # False at the recursion point matches `is_value_type`'s guard.
        if isinstance(typ, RecursiveAliasInstanceType):
            key = (typ.qname, typ.type_args)
            if key in self._evaluating_alias_nocopy:
                return False
            self._evaluating_alias_nocopy.add(key)
            try:
                for m in (recursive_union_alternatives(typ) or ()):
                    if isinstance(m, TpyType) and self.is_type_nocopy(m):
                        return True
                return False
            finally:
                self._evaluating_alias_nocopy.discard(key)
        record = self.registry.get_record_for_type(typ)
        if record is not None and record.is_nocopy:
            return True
        if isinstance(typ, NominalType) and typ.type_args:
            if record is not None and record.has_copy:
                return False
            for arg in typ.type_args:
                if isinstance(arg, TpyType) and self.is_type_nocopy(arg):
                    return True
        return False

    def is_type_non_copyable(self, typ: TpyType) -> bool:
        """Check if a type cannot be copied at C++ level.

        Superset of is_type_nocopy: also counts records with __del__ (which
        deletes copy ops in the generated struct), and walks the parent
        chain for @nocopy or __del__ (which implicitly deletes copy in
        the child). Propagates through record fields and type args.
        Respects __copy__ escape hatch.

        Used to upgrade "copies X into field/container" warnings to errors --
        the generated C++ would otherwise hit a deleted copy ctor.

        Kept separate from is_type_nocopy because the latter feeds into
        analyzer-level `is_nocopy` propagation and user-facing "@nocopy"
        diagnostics, where the narrower "user declared intent" meaning is
        wanted. This helper is strictly about "will C++ reject the copy?".
        """
        if isinstance(typ, ReadonlyType):
            return self.is_type_non_copyable(typ.wrapped)
        if isinstance(typ, OwnType):
            return self.is_type_non_copyable(typ.wrapped)
        if isinstance(typ, OptionalType):
            return self.is_type_non_copyable(typ.inner)
        if isinstance(typ, TupleType):
            for e in typ.element_types:
                if isinstance(e, TpyType) and self.is_type_non_copyable(e):
                    return True
            return False
        # Mirrors the is_type_nocopy branch: a non-copyable alternative
        # propagates through the wrapper's std::variant. Re-entry returns
        # False to match the value/nocopy recursion guards (the recursive
        # alternative re-enters the same instance).
        if isinstance(typ, RecursiveAliasInstanceType):
            key = (typ.qname, typ.type_args)
            if key in self._evaluating_alias_nocopy:
                return False
            self._evaluating_alias_nocopy.add(key)
            try:
                for m in (recursive_union_alternatives(typ) or ()):
                    if isinstance(m, TpyType) and self.is_type_non_copyable(m):
                        return True
                return False
            finally:
                self._evaluating_alias_nocopy.discard(key)
        # Abstract @dynamic protocol bases have pure virtuals and the
        # concrete size depends on the dynamic type, so they have no usable
        # copy/move ctor at the C++ level.
        if is_dyn_protocol(typ):
            return True
        record = self.registry.get_record_for_type(typ)
        if record is None:
            return False
        if record.has_copy:
            return False
        if record.is_nocopy or record.has_del:
            return True
        # Re-entry guard for self-/mutually-referential records: a field or type
        # arg whose type cycles back here (e.g. a tree node `children: list[Node]`)
        # would otherwise recurse forever through the walks below. Conservative
        # False at the recursion point matches the recursive-alias guard above and
        # is_value_type's -- a cycle alone never deletes copy; the answer is
        # decided by the non-cyclic fields/parents the first entry still walks.
        if typ in self._evaluating_record_noncopyable:
            return False
        self._evaluating_record_noncopyable.add(typ)
        try:
            # Inheritance: recurse into each direct parent so each parent's own
            # logic (including its has_copy barrier up its chain) applies
            # independently. For multi-base, any direct parent being non-copyable
            # makes the child non-copyable; for single-parent, parent.has_copy
            # correctly short-circuits the parent's recursion, matching the old
            # `break`-on-has_copy behavior.
            for p in record.parents:
                if self.is_type_non_copyable(p):
                    return True
            if isinstance(typ, NominalType) and typ.type_args:
                for arg in typ.type_args:
                    if isinstance(arg, TpyType) and self.is_type_non_copyable(arg):
                        return True
            for f in record.fields:
                if self.is_type_non_copyable(f.type):
                    return True
            return False
        finally:
            self._evaluating_record_noncopyable.discard(typ)

    def get_expr_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the cached type of an expression, stripping Ref and Own.

        Ref/Own are internal annotations for reference provenance and
        ownership; callers that need bare types (codegen, type resolution,
        narrowing) should not see them.  ReadonlyType is preserved because
        codegen needs it for const emission.  The assignment handler uses
        the analyze_expr return value directly (which preserves Ref/Own)
        for copy-warning detection.
        """
        typ = self.expr_types.get(id(expr))
        if typ is None:
            return None
        typ = unwrap_ref_type(typ)
        if isinstance(typ, OwnType):
            typ = typ.wrapped
        return typ

    def get_raw_expr_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the cached type of an expression WITHOUT stripping qualifiers.

        Used by copy-warning detection to see Ref/Own qualifiers.
        """
        return self.expr_types.get(id(expr))

    def set_expr_type(self, expr: TpyExpr, typ: TpyType) -> None:
        """Cache the type of an expression."""
        self.expr_types[id(expr)] = typ
        # A COMPOSITE carrying a Pending* leaf (a non-array comprehension's
        # `list[Pending]`, a tuple of list literals) is read straight off this
        # cache by the var-decl codegen fallback; record it so resolve_all can
        # finalize just this node rather than sweep the module-wide cache.
        # CROSS-PASS INVARIANT: a BARE Pending* literal is excluded here ONLY
        # because it is a tracked literal whose own cache entry is rewritten to
        # the resolved type by _apply_container_resolution (which runs before
        # _finalize_pending_in_bindings). If a future path ever caches a bare
        # Pending* for a node that does NOT flow through that resolution, this
        # exclusion would silently skip it and reintroduce the codegen crash --
        # the recorded-composite completeness check in
        # _finalize_pending_in_bindings guards the composite half of that.
        # contains_pending_leaf fast-returns on leaves.
        if not isinstance(typ, PENDING_CONTAINER_TYPES) and contains_pending_leaf(typ):
            self.func.pending_composite_exprs.append(expr)

    def record_branch_decls(self, stmt: TpyStmt, mapping: dict) -> dict:
        """Store a branch-decl snapshot for `stmt`, registered for the
        pending-container finalization in resolve_all (see
        pending_branch_decl_maps). Incremental callers keep mutating the
        one registered dict."""
        for typ in mapping.values():
            if typ is not None:
                self._force_branch_decl_lists(typ)
        existing = self.if_branch_decls.get(id(stmt))
        if existing is not None:
            existing.update(mapping)
            return existing
        self.if_branch_decls[id(stmt)] = mapping
        self.func.pending_branch_decl_maps.append(mapping)
        return mapping

    def _force_branch_decl_lists(self, typ: TpyType) -> None:
        """Force pending list literals reaching a branch-decl slot off the
        Array optimization: sibling branches may bind literals of different
        sizes into the one pre-declared slot (the snapshot sees only one
        literal, and a handler/arm bind is not a visible reassignment), so
        only the plain list form is size-safe."""
        if isinstance(typ, PendingListType):
            info = self.list_literals.get(typ.literal_id)
            if info is not None:
                info.is_mutated = True
            # inner_types() does not traverse a Pending's element type, and
            # a nested literal ([[1, 2]] vs [[3]]) has the same shared-slot
            # size hazard one level down.
            self._force_branch_decl_lists(typ.element_type)
        for inner in typ.inner_types():
            self._force_branch_decl_lists(inner)

    def default_int_for_literal(
        self,
        typ: TpyType,
        warn_node: TpyExpr | TpyStmt | None = None,
    ) -> TpyType:
        """Resolve configured default-int, with range-safe fallback for literals."""
        if not isinstance(typ, IntLiteralType):
            return self.default_int_type
        default_tr = int_traits_of(self.default_int_type)
        if (
            default_tr is not None
            and typ.value is not None
            and not (default_tr.min_value <= typ.value <= default_tr.max_value)
        ):
            if warn_node is not None:
                self.warning(
                    f"Integer literal {typ.value} is outside default {self.default_int_type} range; "
                    "inferring int (BigInt).",
                    warn_node,
                )
            return BIGINT
        return self.default_int_type

    def reset_function_tracking(self) -> None:
        """Reset all per-function tracking state."""
        self.func = FunctionTrackingState()

    def save_function_state(self) -> FunctionTrackingState:
        """Snapshot per-function state (for nested def isolation)."""
        return deepcopy(self.func)

    def restore_function_state(self, saved: FunctionTrackingState) -> None:
        """Restore per-function state from a snapshot."""
        self.func = saved

    @contextmanager
    def trial_scope(self) -> Iterator[None]:
        """Snapshot/restore for tentative semantic analysis (e.g. per-candidate
        lambda body trial under Regime C overload resolution).

        Wraps a body of analysis work whose state changes must be rolled back
        unconditionally on exit -- both on success and on exception.

        Snapshotted surfaces:

        - Per-function state (FunctionTrackingState) -- includes call_edges,
          mutated/struct/addr-escape/returned param sets, self-mutation flags,
          borrow tracker, definitely_assigned, narrowed_types, etc. Deep-copied
          since FunctionTrackingState contains nested mutable containers.
        - Module-level type cache (``expr_types``) and the literal/view
          counters and registries (``literal_counter``, ``list_literals``,
          ``dict_literals``, ``set_literals``, ``pending_generic_counter``,
          ``str_var_counter``, ``str_vars``, ``bytes_var_counter``,
          ``bytes_vars``).
        - The diagnostics list -- truncated to its pre-trial length so
          warnings/errors emitted during a rejected trial don't leak into
          the user-visible output.

        Lambda AST mutations (``inferred_param_types``, ``inferred_return_type``,
        ``captured_names``, ``captures_by_value``) are the caller's
        responsibility -- save and restore them around the trial. They are
        not part of SemanticContext state.
        """
        saved_func = self.save_function_state()
        saved_expr_types = dict(self.expr_types)
        saved_literal_counter = self.literal_counter
        saved_list_literals = dict(self.list_literals)
        saved_dict_literals = dict(self.dict_literals)
        saved_set_literals = dict(self.set_literals)
        saved_pending_generic_counter = self.pending_generic_counter
        saved_str_var_counter = self.str_var_counter
        saved_str_vars = dict(self.str_vars)
        saved_bytes_var_counter = self.bytes_var_counter
        saved_bytes_vars = dict(self.bytes_vars)
        saved_diagnostics_len = len(self.diagnostics)
        try:
            yield
        finally:
            self.restore_function_state(saved_func)
            self.expr_types.clear()
            self.expr_types.update(saved_expr_types)
            self.literal_counter = saved_literal_counter
            self.list_literals.clear()
            self.list_literals.update(saved_list_literals)
            self.dict_literals.clear()
            self.dict_literals.update(saved_dict_literals)
            self.set_literals.clear()
            self.set_literals.update(saved_set_literals)
            self.pending_generic_counter = saved_pending_generic_counter
            self.str_var_counter = saved_str_var_counter
            self.str_vars.clear()
            self.str_vars.update(saved_str_vars)
            self.bytes_var_counter = saved_bytes_var_counter
            self.bytes_vars.clear()
            self.bytes_vars.update(saved_bytes_vars)
            del self.diagnostics[saved_diagnostics_len:]

    def mark_loop_var_mutated(self, name: str) -> None:
        """Mark a for-each loop variable as mutated (prevents const-ref binding)."""
        if name in self.func.loop_vars:
            self.func.mutated_loop_vars.add(name)

    def mark_loop_var_consumed(self, name: str) -> None:
        """Mark a for-each loop variable as consumed (copied into owned storage).

        This triggers auto-consuming iteration when the container is at last
        use, so elements are moved instead of copied.
        """
        if name in self.func.loop_vars:
            self.func.consumed_loop_vars.add(name)

    def receiver_self_in_scope(self) -> bool:
        """True when 'self' in the current function scope is the method
        receiver (a closure captures it as the C++ this pointer -- an
        alias), not an ordinary local/param that happens to be named self."""
        f = self.func.current_function
        return (isinstance(f, TpyFunction) and f.is_method
                and not f.is_staticmethod)

    def mark_param_mutated(self, name: str, *, through_field: bool = False) -> None:
        """Mark a function parameter as directly mutated (Phase 1 of mutation inference).

        For 'self': sets current_self_mutated (method self-mutation tracking).
        For regular params: adds to current_mutated_param_names.
        Also traces loop variables back to their source iterables, and traces
        element/field/ptr borrows back to their source storage (8a.5: deferred
        marking for element refs -- when v = items[i] and v.field is written,
        the write propagates back to items).

        ``through_field``: the mutation is a genuine through-reference write
        (field/subscript/slice assignment, del) rather than the speculative
        non-readonly-method-call mark. Only then do we climb a field-path
        borrow root (`o.items` -> `o`) to the owning param: a method call whose
        callee turns out readonly must NOT demote the receiver, so the
        method-call mark stops at the field-path key as it always has.
        """
        if self.func.in_nested_def:
            self.func.nested_mutation_marks.append((name, through_field, False))
        if name == "self":
            self.func.current_self_mutated = True
            return
        if name in self.func.current_param_names and name not in self.func.current_rebound_params:
            self.func.current_mutated_param_names.add(name)
        iterable = self.func.loop_var_iterable.get(name)
        if iterable is not None:
            # Field-path iterables ("c.items") need root extraction for param lookup
            self.mark_param_mutated(_storage_root(iterable), through_field=through_field)
        for src in self.func.bp_borrow_source_roots(name):
            self.mark_param_mutated(src, through_field=through_field)
        # 8a.5: trace through element/field/ptr borrows to source param.
        # When v = items[i] (deferred) and v is later written through,
        # mark the ultimate storage root (e.g. items) as mutated.
        # The recursive call terminates because effective_storage_through_borrows
        # on the ultimate root returns itself (no upstream borrow points to it).
        ultimate = self.func.borrow_tracker.effective_storage_through_borrows(name)
        if ultimate != name:
            self.mark_param_mutated(ultimate, through_field=through_field)
        # A borrow rooted at a field path (`o.items`, registered for `e = o.items[0]`
        # or for an @auto_readonly accessor result `x = o.b.get()`) reaches the
        # owning param through that field; a genuine write through the borrower
        # mutates the param. The leaf-name walk in _root_name_of_expr handles the
        # inline form (`o.items[0].v = 9`); this climbs the alias form to the param.
        if through_field:
            field_root = _storage_root(name)
            if field_root != name:
                self.mark_param_mutated(field_root, through_field=through_field)

    def mark_param_structurally_mutated(self, name: str) -> None:
        """Mark a parameter as structurally mutated (append/insert/clear/del/etc.).

        Structural mutations invalidate element references -- this is separate from
        mark_param_mutated which also fires for element-ref taking (a = items[0]).
        Traces loop variables back to their source iterables transitively.
        """
        if self.func.in_nested_def:
            self.func.nested_mutation_marks.append((name, False, True))
        if name == "self":
            self.func.current_self_struct_mutated = True
            return
        if name in self.func.current_param_names and name not in self.func.current_rebound_params:
            self.func.current_struct_mutated_param_names.add(name)
        iterable = self.func.loop_var_iterable.get(name)
        if iterable is not None:
            self.mark_param_structurally_mutated(_storage_root(iterable))

    def mark_param_returned(self, name: str) -> None:
        """Mark a parameter as contributing to the return value (8b).

        Called when returning a reference derived from param storage, so we
        can record return_borrows_from on FunctionInfo. Mirrors mark_param_mutated
        but writes to current_returned_param_names instead.
        Traces loop variables back to their source iterables transitively.
        """
        if name == "self":
            self.func.current_returned_param_names.add("self")
            return
        if name in self.func.current_param_names and name not in self.func.current_rebound_params:
            self.func.current_returned_param_names.add(name)
        iterable = self.func.loop_var_iterable.get(name)
        if iterable is not None:
            self.mark_param_returned(_storage_root(iterable))
        for src in self.func.bp_borrow_source_roots(name):
            self.mark_param_returned(src)

    def mark_own_param_consumed(self, name: str) -> None:
        """Mark an Own[T] param as consumed (stored, forwarded, or returned)."""
        if name in self.func.current_param_names:
            self.func.current_consumed_own_params.add(name)

    # ------------------------------------------------------------------
    # View-type family generic accessors
    # ------------------------------------------------------------------

    def view_var_map(self, family: ViewTypeFamily) -> dict[str, int]:
        """Variable-name -> var_id mapping for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.func.variable_to_str_var
        return self.func.variable_to_bytes_var

    def view_source_borrows_map(self, family: ViewTypeFamily) -> dict[str, set[int]]:
        """Source-storage -> set of borrowing var_ids for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.func.str_source_borrows
        return self.func.bytes_source_borrows

    def view_pending_resolutions(self, family: ViewTypeFamily) -> list[int]:
        """Pending resolution list for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.func.pending_str_resolutions
        return self.func.pending_bytes_resolutions

    def view_vars(self, family: ViewTypeFamily) -> dict[int, ViewVarInfo]:
        """Var-id -> ViewVarInfo registry for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.str_vars
        return self.bytes_vars

    def next_view_var_id(self, family: ViewTypeFamily) -> int:
        """Allocate and return the next var_id for the given family."""
        if family.pending_type_class is PendingStrType:
            vid = self.str_var_counter
            self.str_var_counter += 1
            return vid
        vid = self.bytes_var_counter
        self.bytes_var_counter += 1
        return vid

    # ------------------------------------------------------------------
    # View-type mutation tracking
    # ------------------------------------------------------------------

    def mark_view_borrowers_mutated(self, storage: str, family: ViewTypeFamily) -> None:
        """Mark pending view-type borrowers of storage as source-mutated.

        Called at mutation sites so that view resolution falls back to
        the owned type when the view's source storage is mutated.
        Also invalidates field-path borrows: mutating ``p`` invalidates
        views borrowed from ``p.name``, ``p.field``, etc.
        """
        source_borrows = self.view_source_borrows_map(family)
        vars_registry = self.view_vars(family)
        keys = [storage]
        prefix = storage + "."
        keys.extend(k for k in source_borrows if k.startswith(prefix))
        for key in keys:
            var_ids = source_borrows.get(key)
            if not var_ids:
                continue
            for var_id in var_ids:
                info = vars_registry.get(var_id)
                if info is not None:
                    info.source_mutated = True

    def mark_all_view_borrowers_mutated(self, storage: str) -> None:
        """Mark borrowers across all view-type families as source-mutated."""
        for family in VIEW_TYPE_FAMILIES:
            self.mark_view_borrowers_mutated(storage, family)

    # ------------------------------------------------------------------
    # Unified container literal lookup
    # ------------------------------------------------------------------

    def get_container_info(self, literal_id: int) -> ListLiteralInfo | DictLiteralInfo | SetLiteralInfo | None:
        """Look up container info across all container types by literal_id."""
        return (self.list_literals.get(literal_id)
                or self.dict_literals.get(literal_id)
                or self.set_literals.get(literal_id))

    def track_container_variable(
        self,
        var_name: str,
        pending_type: 'PendingListType | PendingDictType | PendingSetType',
        decl_line: int | None,
    ) -> None:
        """Register var-name -> literal_id mapping for any pending container type.

        Also sets variable_name and decl_line on the corresponding LiteralInfo.
        Single code path for list/dict/set -- adding a new container type means
        adding one branch here instead of duplicating blocks in statements.py.
        """
        literal_id = pending_type.literal_id
        info: ListLiteralInfo | DictLiteralInfo | SetLiteralInfo
        if isinstance(pending_type, PendingListType):
            self.func.variable_to_literal[var_name] = literal_id
            info = self.list_literals[literal_id]
        elif isinstance(pending_type, PendingDictType):
            self.func.variable_to_dict_literal[var_name] = literal_id
            info = self.dict_literals[literal_id]
        elif isinstance(pending_type, PendingSetType):
            self.func.variable_to_set_literal[var_name] = literal_id
            info = self.set_literals[literal_id]
        else:
            return
        info.variable_name = var_name
        info.decl_line = decl_line
