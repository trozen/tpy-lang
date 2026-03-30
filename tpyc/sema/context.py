"""
TurboPython Semantic Analysis Context

Contains the shared state that is passed to all semantic analysis components.
"""

from __future__ import annotations
from copy import deepcopy
from dataclasses import dataclass, field, fields as dc_fields
from enum import Enum
from typing import Literal, TYPE_CHECKING

if TYPE_CHECKING:
    from ..macro_loader import MacroRegistry

from ..typesys import (
    TpyType, TypeRegistry, ListLiteralInfo, DictLiteralInfo, SetLiteralInfo, ViewVarInfo, TypeParamKind, IntLiteralType,
    FixedIntType, INT32, BIGINT, NamedType, ReadonlyType, OwnType, OptionalType,
    PendingListType, PendingDictType, PendingSetType,
    PendingGenericInstanceType, PendingGenericInstanceInfo,
    ViewTypeFamily, PendingViewType, PendingStrType, VIEW_TYPE_FAMILIES,
    unwrap_readonly,
)
from ..namespace import Namespace
from ..parse import (
    TpyExpr, TpyStmt, TpyRecord, TpyFunction, TpyVarDecl, TpyMethodCall,
    TpyCoerce, TpyName, TpySubscript, TpyFieldAccess, TpyBinOp, TpyIfExpr,
    TpyNestedDef,
)
from .diagnostics import Diagnostic, DiagnosticLevel, SemanticError, Scope

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

    def remove_storage_borrows(self, storage: str) -> None:
        """Remove all borrows of ``storage`` and any field-path borrows (``storage.*``).

        When a variable is reassigned (``obj = Foo()``), borrows stored under
        dotted keys like ``obj.items`` must also be cleared since the old
        object's fields are no longer reachable through the variable.
        """
        borrowers = self.borrows.pop(storage, None)
        if borrowers:
            for b in borrowers:
                self.borrow_kinds.pop((storage, b), None)
        # Clear field-path borrows: "storage.field"
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


class _ModuleInitSentinel:
    """Sentinel for module-level init context (not a real function, but not None either).

    Truthy so that `if ctx.current_function:` passes, but fails
    `isinstance(ctx.current_function, TpyFunction)` checks.
    """
    __slots__ = ()
    def __bool__(self) -> bool:
        return True
    def __repr__(self) -> str:
        return "<MODULE_INIT>"


MODULE_INIT_CONTEXT = _ModuleInitSentinel()


@dataclass
class RecordContext:
    """State for the record currently being analyzed (type params, bounds)."""
    record: TpyRecord | None = None
    type_params: list[str] | None = None
    type_param_kinds: list[TypeParamKind] | None = None
    type_param_bounds: dict[str, TpyType] | None = None


@dataclass
class FunctionTrackingState:
    """Per-function analysis state that is reset between functions and
    saved/restored for nested def isolation.

    SemanticContext delegates attribute access to an instance of this class
    via __getattr__/__setattr__, so all fields are accessible directly on
    ctx without callsite changes.
    """

    # --- Analysis state (per-function) ---
    current_scope: Scope | None = None
    current_function: TpyFunction | _ModuleInitSentinel | None = None
    current_ns: Namespace | None = None
    loop_depth: int = 0

    # --- List/dict/set literal tracking ---
    variable_to_literal: dict[str, int] = field(default_factory=dict)
    pending_resolutions: list[int] = field(default_factory=list)
    pre_analyzed_method_args: dict[int, list[TpyType]] = field(default_factory=dict)
    variable_to_dict_literal: dict[str, int] = field(default_factory=dict)
    pending_dict_resolutions: list[int] = field(default_factory=list)
    variable_to_set_literal: dict[str, int] = field(default_factory=dict)
    pending_set_resolutions: list[int] = field(default_factory=list)

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

    # --- Control flow ---
    super_init_call: TpyMethodCall | None = None
    super_del_call: TpyMethodCall | None = None
    pending_loop_vars: dict[str, tuple[TpyType, TpyStmt, TpyStmt | None]] = field(default_factory=dict)
    loop_vars: set[str] = field(default_factory=set)
    mutated_loop_vars: set[str] = field(default_factory=set)
    consumed_loop_vars: set[str] = field(default_factory=set)
    deferred_loop_copy_warnings: dict[str, list[int]] = field(default_factory=dict)
    loop_var_iterable: dict[str, str] = field(default_factory=dict)

    # --- Scope escape tracking ---
    var_scope_depth: dict[str, int] = field(default_factory=dict)
    hoisted_vars: set[str] = field(default_factory=set)
    rvalue_vars: set[str] = field(default_factory=set)
    move_through_vars: set[str] = field(default_factory=set)

    # --- Prescan / last-use ---
    current_reassigned_vars: set[str] = field(default_factory=set)
    current_lvalue_reassigned: set[str] = field(default_factory=set)
    current_aug_assigned_vars: set[str] = field(default_factory=set)

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

    # --- Global declaration tracking ---
    global_declarations: set[str] = field(default_factory=set)

    # --- Nested def tracking ---
    in_nested_def: bool = False
    nested_def_name: str | None = None
    outer_scope_locals: set[str] = field(default_factory=set)
    current_nonlocal_names: set[str] = field(default_factory=set)
    nested_def_names: set[str] = field(default_factory=set)
    nested_def_escapes: set[str] = field(default_factory=set)
    nested_def_nodes: dict[str, 'TpyNestedDef'] = field(default_factory=dict)

    # --- Pointer provenance tracking ---
    param_provenance_vars: set[str] = field(default_factory=set)
    non_null_ptr_vars: set[str] = field(default_factory=set)

    # --- Consumed variable tracking ---
    consumed_vars: set[str] = field(default_factory=set)

    # --- Variable declaration tracking (per-function) ---
    var_decl_by_name: dict[str, 'TpyVarDecl'] = field(default_factory=dict)

    # --- Integer value range tracking ---
    value_ranges: dict[str, 'ValueRange'] = field(default_factory=dict)

    # --- Borrow tracking ---
    borrow_tracker: BorrowTracker = field(default_factory=BorrowTracker)

    # --- Parameter mutation inference ---
    current_param_names: set[str] = field(default_factory=set)
    current_param_name_to_idx: dict[str, int] = field(default_factory=dict)
    current_mutated_param_names: set[str] = field(default_factory=set)
    current_rebound_params: set[str] = field(default_factory=set)
    current_call_edges: list = field(default_factory=list)
    current_self_mutated: bool = False
    current_self_struct_mutated: bool = False
    current_struct_mutated_param_names: set[str] = field(default_factory=set)
    current_returned_param_names: set[str] = field(default_factory=set)
    current_consumed_own_params: set[str] = field(default_factory=set)


# Field names on FunctionTrackingState, cached for __getattr__/__setattr__.
_FUNC_STATE_FIELDS: frozenset[str] = frozenset(
    f.name for f in dc_fields(FunctionTrackingState)
)


@dataclass
class SemanticContext:
    """Shared state for all semantic analysis components.

    Per-function state lives in _func (FunctionTrackingState). Attribute access
    is forwarded transparently so callers use ctx.definitely_assigned etc.
    """

    # --- Core ---
    registry: TypeRegistry
    global_scope: Scope
    default_int_type: TpyType = field(default_factory=lambda: INT32)
    macro_registry: MacroRegistry | None = None

    # --- Per-function state (composed, forwarded via __getattr__/__setattr__) ---
    _func: FunctionTrackingState = field(default_factory=FunctionTrackingState)

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

    # --- Import tracking ---
    imports: dict[str, set[tuple[str, str]] | None | str] = field(default_factory=dict)
    bare_module_imports: set[str] = field(default_factory=set)
    imported_names: dict[str, tuple[str, str]] = field(default_factory=dict)

    # --- Cross-module support ---
    module_name: str = "__main__"
    module_cpp_namespace: str | None = None  # from # tpy: cpp_namespace directive
    user_imported_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_records: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_protocols: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_variables: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_type_aliases: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_enums: dict[str, tuple[str, str]] = field(default_factory=dict)
    top_level_decls: dict[str, int] = field(default_factory=dict)

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

    # --- Control flow (persistent) ---
    in_comprehension: int = 0
    sc_and_walrus: set[str] = field(default_factory=set)
    sc_or_walrus: set[str] = field(default_factory=set)
    try_except_error_type: str | None = None
    in_except_tier: Literal["return", "throw"] | None = None
    # Whether the current except handler has an 'as e' binding (needed for return-tier re-raise)
    in_except_has_binding: bool = False
    # True when analyzing a finally body (raise is not allowed there)
    in_finally: bool = False
    is_top_level: bool = False
    # REPL mode: allow @error_return calls at top level (unwrap with panic)
    allow_top_level_error_unwrap: bool = False

    # --- Last-use tracking (shared with codegen, persists across functions) ---
    all_last_uses: set[int] = field(default_factory=set)

    # --- Consuming method tracking ---
    in_consuming_method: bool = False

    # --- Generator yield tracking (persists across functions) ---
    _yield_counter: int = 0
    generator_yield_states: dict[int, int] = field(default_factory=dict)  # id(TpyYield) -> state number

    # --- Expression type hint ---
    expr_type_hint: TpyType | None = None

    # --- Branch-declared variable tracking ---
    if_branch_decls: dict[int, dict[str, TpyType]] = field(default_factory=dict)

    # --- Extern symbol tracking ---
    extern_symbols: dict[str, str] = field(default_factory=dict)

    # --- Diagnostics ---
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def __getattr__(self, name: str) -> object:
        if name in _FUNC_STATE_FIELDS:
            return getattr(self._func, name)
        raise AttributeError(f"'{type(self).__name__}' has no attribute '{name}'")

    def __setattr__(self, name: str, value: object) -> None:
        if name in _FUNC_STATE_FIELDS:
            setattr(self._func, name, value)
        else:
            super().__setattr__(name, value)

    def is_readonly_name(self, name: str) -> bool:
        """Check if a variable has readonly provenance in its declared scope type.

        isinstance narrowing updates narrowed_types (not scope bindings), so the
        scope binding preserves ReadonlyType even when the expr type is narrowed
        to a concrete member. Reassignment updates the scope binding, so a
        non-readonly reassignment correctly clears this.
        """
        if self.current_scope is None:
            return False
        declared = self.current_scope.lookup(name)
        return declared is not None and isinstance(declared, ReadonlyType)

    def error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> SemanticError:
        """Create a SemanticError with location from a node."""
        loc = getattr(node, 'loc', None) if node else None
        return SemanticError(message, loc)

    def emit_error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> None:
        """Record an error diagnostic without raising (allows continued analysis)."""
        loc = getattr(node, 'loc', None) if node else None
        self.diagnostics.append(Diagnostic(DiagnosticLevel.ERROR, message, loc))

    def warning(self, message: str, node: TpyExpr | TpyStmt | TpyRecord | None = None) -> None:
        """Record a warning diagnostic (doesn't stop compilation)."""
        loc = getattr(node, 'loc', None) if node else None
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, loc))

    def warning_from_loc(self, message: str, loc: 'SourceLocation | None') -> None:
        """Record a warning diagnostic from a SourceLocation."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, loc))

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
        if isinstance(inner, NamedType) and inner.type_args:
            if record is None or not record.has_copy:
                for arg in inner.type_args:
                    if isinstance(arg, TpyType) and self.is_type_nocopy(arg):
                        return (
                            f"non-copyable type '{typ}' (type argument "
                            f"'{arg}' is non-copyable)"
                        )
        # Check parent
        if record.parent is not None:
            parent_rec = self.registry.get_record_for_type(record.parent)
            if parent_rec is not None and parent_rec.is_nocopy:
                return (
                    f"non-copyable type '{typ}' (parent '{record.parent}' "
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
        record = self.registry.get_record_for_type(typ)
        if record is not None and record.is_nocopy:
            return True
        if isinstance(typ, NamedType) and typ.type_args:
            if record is not None and record.has_copy:
                return False
            for arg in typ.type_args:
                if isinstance(arg, TpyType) and self.is_type_nocopy(arg):
                    return True
        return False

    def get_expr_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the cached type of an expression."""
        return self.expr_types.get(id(expr))

    def set_expr_type(self, expr: TpyExpr, typ: TpyType) -> None:
        """Cache the type of an expression."""
        self.expr_types[id(expr)] = typ

    def default_int_for_literal(
        self,
        typ: TpyType,
        warn_node: TpyExpr | TpyStmt | None = None,
    ) -> TpyType:
        """Resolve configured default-int, with range-safe fallback for literals."""
        if not isinstance(typ, IntLiteralType):
            return self.default_int_type
        if (
            isinstance(self.default_int_type, FixedIntType)
            and typ.value is not None
            and not (self.default_int_type.min_value <= typ.value <= self.default_int_type.max_value)
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
        self._func = FunctionTrackingState()

    def save_function_state(self) -> FunctionTrackingState:
        """Snapshot per-function state (for nested def isolation)."""
        return deepcopy(self._func)

    def restore_function_state(self, saved: FunctionTrackingState) -> None:
        """Restore per-function state from a snapshot."""
        self._func = saved

    def mark_loop_var_mutated(self, name: str) -> None:
        """Mark a for-each loop variable as mutated (prevents const-ref binding)."""
        if name in self.loop_vars:
            self.mutated_loop_vars.add(name)

    def mark_loop_var_consumed(self, name: str) -> None:
        """Mark a for-each loop variable as consumed (copied into owned storage).

        This triggers auto-consuming iteration when the container is at last
        use, so elements are moved instead of copied.
        """
        if name in self.loop_vars:
            self.consumed_loop_vars.add(name)

    def mark_param_mutated(self, name: str) -> None:
        """Mark a function parameter as directly mutated (Phase 1 of mutation inference).

        For 'self': sets current_self_mutated (method self-mutation tracking).
        For regular params: adds to current_mutated_param_names.
        Also traces loop variables back to their source iterables, and traces
        element/field/ptr borrows back to their source storage (8a.5: deferred
        marking for element refs -- when v = items[i] and v.field is written,
        the write propagates back to items).
        """
        if name == "self":
            self.current_self_mutated = True
            return
        if name in self.current_param_names and name not in self.current_rebound_params:
            self.current_mutated_param_names.add(name)
        iterable = self.loop_var_iterable.get(name)
        if iterable is not None:
            # Field-path iterables ("c.items") need root extraction for param lookup
            root = iterable.split(".")[0] if "." in iterable else iterable
            self.mark_param_mutated(root)
        # 8a.5: trace through element/field/ptr borrows to source param.
        # When v = items[i] (deferred) and v is later written through,
        # mark the ultimate storage root (e.g. items) as mutated.
        # The recursive call terminates because effective_storage_through_borrows
        # on the ultimate root returns itself (no upstream borrow points to it).
        ultimate = self.borrow_tracker.effective_storage_through_borrows(name)
        if ultimate != name:
            self.mark_param_mutated(ultimate)

    def mark_param_structurally_mutated(self, name: str) -> None:
        """Mark a parameter as structurally mutated (append/insert/clear/del/etc.).

        Structural mutations invalidate element references -- this is separate from
        mark_param_mutated which also fires for element-ref taking (a = items[0]).
        Traces loop variables back to their source iterables transitively.
        """
        if name == "self":
            self.current_self_struct_mutated = True
            return
        if name in self.current_param_names and name not in self.current_rebound_params:
            self.current_struct_mutated_param_names.add(name)
        iterable = self.loop_var_iterable.get(name)
        if iterable is not None:
            root = iterable.split(".")[0] if "." in iterable else iterable
            self.mark_param_structurally_mutated(root)

    def mark_param_returned(self, name: str) -> None:
        """Mark a parameter as contributing to the return value (8b).

        Called when returning a reference derived from param storage, so we
        can record return_borrows_from on FunctionInfo. Mirrors mark_param_mutated
        but writes to current_returned_param_names instead.
        Traces loop variables back to their source iterables transitively.
        """
        if name == "self":
            self.current_returned_param_names.add("self")
            return
        if name in self.current_param_names and name not in self.current_rebound_params:
            self.current_returned_param_names.add(name)
        iterable = self.loop_var_iterable.get(name)
        if iterable is not None:
            root = iterable.split(".")[0] if "." in iterable else iterable
            self.mark_param_returned(root)

    def mark_own_param_consumed(self, name: str) -> None:
        """Mark an Own[T] param as consumed (stored, forwarded, or returned)."""
        if name in self.current_param_names:
            self.current_consumed_own_params.add(name)

    # ------------------------------------------------------------------
    # View-type family generic accessors
    # ------------------------------------------------------------------

    def view_var_map(self, family: ViewTypeFamily) -> dict[str, int]:
        """Variable-name -> var_id mapping for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.variable_to_str_var
        return self.variable_to_bytes_var

    def view_source_borrows_map(self, family: ViewTypeFamily) -> dict[str, set[int]]:
        """Source-storage -> set of borrowing var_ids for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.str_source_borrows
        return self.bytes_source_borrows

    def view_pending_resolutions(self, family: ViewTypeFamily) -> list[int]:
        """Pending resolution list for the given family."""
        if family.pending_type_class is PendingStrType:
            return self.pending_str_resolutions
        return self.pending_bytes_resolutions

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
        if isinstance(pending_type, PendingListType):
            self.variable_to_literal[var_name] = literal_id
            info = self.list_literals[literal_id]
        elif isinstance(pending_type, PendingDictType):
            self.variable_to_dict_literal[var_name] = literal_id
            info = self.dict_literals[literal_id]
        elif isinstance(pending_type, PendingSetType):
            self.variable_to_set_literal[var_name] = literal_id
            info = self.set_literals[literal_id]
        else:
            return
        info.variable_name = var_name
        info.decl_line = decl_line
