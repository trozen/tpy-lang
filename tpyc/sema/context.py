"""
TurboPython Semantic Analysis Context

Contains the shared state that is passed to all semantic analysis components.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum

from ..typesys import (
    TpyType, TypeRegistry, ListLiteralInfo, DictLiteralInfo, SetLiteralInfo, StrVarInfo, TypeParamKind, IntLiteralType,
    FixedIntType, INT32, BIGINT, NamedType, ReadonlyType, OwnType, OptionalType,
    PendingListType, PendingDictType, PendingSetType,
    PendingGenericInstanceType, PendingGenericInstanceInfo,
    unwrap_readonly,
)
from ..namespace import Namespace
from ..parse import (
    TpyExpr, TpyStmt, TpyRecord, TpyFunction, TpyVarDecl, TpyMethodCall,
    TpyCoerce, TpyName, TpySubscript, TpyFieldAccess, TpyBinOp, TpyIfExpr,
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
    if isinstance(expr, TpySubscript) and isinstance(expr.obj, TpyName):
        return [expr.obj.name]
    if isinstance(expr, TpyFieldAccess) and isinstance(expr.obj, TpyName):
        return [expr.obj.name]
    if isinstance(expr, TpyBinOp) and expr.op in ("||", "&&"):
        return addr_taken_roots(expr.left) + addr_taken_roots(expr.right)
    if isinstance(expr, TpyIfExpr):
        return addr_taken_roots(expr.then_expr) + addr_taken_roots(expr.else_expr)
    return []


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
        """Remove all borrows of ``storage`` (e.g. when storage is reassigned)."""
        borrowers = self.borrows.pop(storage, None)
        if borrowers:
            for b in borrowers:
                self.borrow_kinds.pop((storage, b), None)

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
class SemanticContext:
    """Shared state for all semantic analysis components."""

    # --- Core ---
    registry: TypeRegistry
    global_scope: Scope
    # Default concrete type used for unannotated integer literal deduction.
    default_int_type: TpyType = field(default_factory=lambda: INT32)

    # --- Analysis state ---
    current_scope: Scope | None = None
    current_function: TpyFunction | _ModuleInitSentinel | None = None
    record_ctx: RecordContext = field(default_factory=RecordContext)

    # --- Type cache ---
    expr_types: dict[int, TpyType] = field(default_factory=dict)
    var_types: dict[int, TpyType] = field(default_factory=dict)

    # --- List literal tracking ---
    literal_counter: int = 0
    list_literals: dict[int, ListLiteralInfo] = field(default_factory=dict)
    variable_to_literal: dict[str, int] = field(default_factory=dict)
    pending_resolutions: list[int] = field(default_factory=list)
    pre_analyzed_method_args: dict[int, list[TpyType]] = field(default_factory=dict)
    dict_literals: dict[int, DictLiteralInfo] = field(default_factory=dict)
    variable_to_dict_literal: dict[str, int] = field(default_factory=dict)
    pending_dict_resolutions: list[int] = field(default_factory=list)
    set_literals: dict[int, SetLiteralInfo] = field(default_factory=dict)
    variable_to_set_literal: dict[str, int] = field(default_factory=dict)
    pending_set_resolutions: list[int] = field(default_factory=list)
    var_decl_by_name: dict[str, TpyVarDecl] = field(default_factory=dict)

    # --- Resolved types for variable declarations (for test annotations) ---
    # Keyed by (line, varname). Persists across functions (NOT cleared in
    # reset_function_tracking) so the test framework can query after full compilation.
    declared_var_types: dict[tuple[int, str], TpyType] = field(default_factory=dict)

    # --- Ptr deref facts (for test annotations) ---
    # Records whether each ptr dereference (field access / method call) skips
    # deref_check. Keyed by (line, varname). True = non-null proven.
    ptr_deref_facts: dict[tuple[int, str], bool] = field(default_factory=dict)

    # --- Subscript bounds facts (for test annotations) ---
    # Records whether each subscript access skips bounds checking.
    # Keyed by (line, container_varname). True = bounds-safe proven.
    subscript_bounds_facts: dict[tuple[int, str], bool] = field(default_factory=dict)

    # --- Division non-zero facts (for test annotations) ---
    # Records whether each division/modulo skips zero-check.
    # Keyed by (line, divisor_varname). True = non-zero proven.
    div_zero_facts: dict[tuple[int, str], bool] = field(default_factory=dict)

    # --- Cast safety facts (for test annotations) ---
    # Records whether each int cast skips range checking.
    # Keyed by (line, target_type_name). True = cast proven safe.
    cast_safe_facts: dict[tuple[int, str], bool] = field(default_factory=dict)

    # --- Pending generic instance tracking (Phase 7a) ---
    pending_generic_counter: int = 0
    pending_generic_instances: dict[int, PendingGenericInstanceInfo] = field(default_factory=dict)
    variable_to_generic_instance: dict[str, int] = field(default_factory=dict)

    # --- String local tracking (PendingStrType inference) ---
    str_var_counter: int = 0
    str_vars: dict[int, StrVarInfo] = field(default_factory=dict)
    variable_to_str_var: dict[str, int] = field(default_factory=dict)
    str_source_borrows: dict[str, set[int]] = field(default_factory=dict)  # storage -> str_var_ids
    pending_str_resolutions: list[int] = field(default_factory=list)

    # --- Import tracking ---
    # imports: module_name -> set of (original_name, local_name) tuples (for "from X import Y [as Z]")
    #          module_name -> "*" (for "from X import *")
    imports: dict[str, set[tuple[str, str]] | None | str] = field(default_factory=dict)
    # Modules that had bare `import X` statements (for module.func() routing in codegen)
    bare_module_imports: set[str] = field(default_factory=set)
    # imported_names: name -> (module_name, function_name) for direct function access
    imported_names: dict[str, tuple[str, str]] = field(default_factory=dict)

    # --- Cross-module support ---
    module_name: str = "__main__"
    # Maps local_name -> (source_module, original_name) to support import aliases
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

    # --- Namespace hierarchy ---
    builtins_ns: Namespace | None = None
    global_ns: Namespace | None = None
    current_ns: Namespace | None = None

    # --- Control flow ---
    loop_depth: int = 0
    is_top_level: bool = False
    super_init_call: TpyMethodCall | None = None
    super_del_call: TpyMethodCall | None = None
    loop_vars: set[str] = field(default_factory=set)
    mutated_loop_vars: set[str] = field(default_factory=set)
    loop_var_iterable: dict[str, str] = field(default_factory=dict)  # var_name -> iterable_name
    # Parameter mutation inference (8a)
    current_param_names: set[str] = field(default_factory=set)
    current_param_name_to_idx: dict[str, int] = field(default_factory=dict)
    current_mutated_param_names: set[str] = field(default_factory=set)
    current_rebound_params: set[str] = field(default_factory=set)
    current_call_edges: list = field(default_factory=list)  # list[MutationCallEdge]
    current_self_mutated: bool = False  # True if self is directly mutated in current method
    # Structural mutation inference: param names whose containers are structurally mutated
    # (append/insert/clear/del/etc.) -- excludes element-ref taking and field writes.
    current_struct_mutated_param_names: set[str] = field(default_factory=set)
    # Return borrow inference (8b): param names whose storage the return value borrows from
    current_returned_param_names: set[str] = field(default_factory=set)

    # --- Scope escape tracking ---
    var_scope_depth: dict[str, int] = field(default_factory=dict)
    hoisted_vars: set[str] = field(default_factory=set)
    rvalue_vars: set[str] = field(default_factory=set)
    move_through_vars: set[str] = field(default_factory=set)

    # --- Last-use tracking for auto-move ---
    all_last_uses: set[int] = field(default_factory=set)
    # Prescan reassigned vars for current function (needed by _is_movable_var)
    current_reassigned_vars: set[str] = field(default_factory=set)
    # Reassigned vars with at least one lvalue (non-rvalue) reassignment
    current_lvalue_reassigned: set[str] = field(default_factory=set)
    # Vars targeted by augmented assignment (+=, -=, etc.)
    current_aug_assigned_vars: set[str] = field(default_factory=set)

    # --- Definite-assignment tracking ---
    definitely_assigned: set[str] = field(default_factory=set)
    init_terminated: bool = False
    # Union/Optional type narrowing: var_name -> narrowed member type.
    narrowed_types: dict[str, TpyType] = field(default_factory=dict)

    # --- Reassignment inference tracking ---
    # Vars initialized from int literals without annotation (defaulted to
    # default_int_type) and still eligible for reassignment-based refinement.
    literal_default_vars: set[str] = field(default_factory=set)
    # Tracks values of literal writes for range checks during potential narrowing.
    literal_values: dict[str, list[int]] = field(default_factory=dict)
    # Vars initialized with None without annotation and awaiting concrete type.
    unresolved_none_vars: set[str] = field(default_factory=set)
    # Types from prior writes (for retro-validation when a later annotation appears).
    write_history: dict[str, list[tuple[TpyType, TpyExpr]]] = field(default_factory=dict)
    # Authoritative annotation set by an explicit typed write.
    authoritative_types: dict[str, TpyType] = field(default_factory=dict)
    # Source line for the authoritative explicit annotation.
    authoritative_type_lines: dict[str, int] = field(default_factory=dict)

    # --- Global declaration tracking (per-function `global x` statements) ---
    global_declarations: set[str] = field(default_factory=set)

    # --- Pointer provenance tracking ---
    param_provenance_vars: set[str] = field(default_factory=set)
    non_null_ptr_vars: set[str] = field(default_factory=set)

    # --- Consumed variable tracking (use-after-consume detection) ---
    consumed_vars: set[str] = field(default_factory=set)

    # --- Integer value range tracking (bounds check / div-zero elision) ---
    value_ranges: dict[str, 'ValueRange'] = field(default_factory=dict)

    # --- Borrow tracking ---
    borrow_tracker: BorrowTracker = field(default_factory=BorrowTracker)

    # --- Expression type hint (for context-dependent functions like unsafe_cast) ---
    expr_type_hint: TpyType | None = None

    # --- Branch-declared variable tracking ---
    # Variables first declared inside if-branches that need pre-declaration.
    # Keyed by id(TpyIf), value is {var_name: var_type}.
    if_branch_decls: dict[int, dict[str, TpyType]] = field(default_factory=dict)

    # --- Extern symbol tracking ---
    # Maps extern C/C++ symbol name -> Python function name (for duplicate detection)
    extern_symbols: dict[str, str] = field(default_factory=dict)

    # --- Diagnostics ---
    diagnostics: list[Diagnostic] = field(default_factory=list)

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
        """Reset per-function tracking state between function analyses."""
        self.variable_to_literal.clear()
        self.pending_resolutions.clear()
        self.pre_analyzed_method_args.clear()
        self.variable_to_dict_literal.clear()
        self.pending_dict_resolutions.clear()
        self.variable_to_set_literal.clear()
        self.pending_set_resolutions.clear()
        self.pending_generic_instances.clear()
        self.variable_to_generic_instance.clear()
        self.variable_to_str_var.clear()
        self.str_source_borrows.clear()
        self.pending_str_resolutions.clear()
        self.super_init_call = None
        self.super_del_call = None
        self.loop_vars.clear()
        self.mutated_loop_vars.clear()
        self.loop_var_iterable.clear()
        self.var_scope_depth.clear()
        self.hoisted_vars.clear()
        self.rvalue_vars.clear()
        self.move_through_vars.clear()
        self.current_reassigned_vars.clear()
        self.current_lvalue_reassigned.clear()
        self.current_aug_assigned_vars.clear()
        self.definitely_assigned.clear()
        self.init_terminated = False
        self.narrowed_types.clear()
        self.literal_default_vars.clear()
        self.literal_values.clear()
        self.unresolved_none_vars.clear()
        self.write_history.clear()
        self.authoritative_types.clear()
        self.global_declarations.clear()
        self.param_provenance_vars.clear()
        self.non_null_ptr_vars.clear()
        self.consumed_vars.clear()
        self.borrow_tracker.reset()
        self.value_ranges.clear()
        self.current_param_names.clear()
        self.current_param_name_to_idx.clear()
        self.current_mutated_param_names.clear()
        self.current_rebound_params.clear()
        self.current_call_edges.clear()
        self.current_self_mutated = False
        self.current_struct_mutated_param_names.clear()
        self.current_returned_param_names.clear()

    def mark_loop_var_mutated(self, name: str) -> None:
        """Mark a for-each loop variable as mutated (prevents const-ref binding)."""
        if name in self.loop_vars:
            self.mutated_loop_vars.add(name)

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
            self.mark_param_mutated(iterable)
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
            # Self structural mutation is covered by self_mutated; no separate field needed.
            return
        if name in self.current_param_names and name not in self.current_rebound_params:
            self.current_struct_mutated_param_names.add(name)
        iterable = self.loop_var_iterable.get(name)
        if iterable is not None:
            self.mark_param_structurally_mutated(iterable)

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
            self.mark_param_returned(iterable)

    def mark_str_borrowers_mutated(self, storage: str) -> None:
        """Mark PendingStrType borrowers of storage as source-mutated.

        Called at mutation sites so that string view resolution falls
        back to std::string when the view's source storage is mutated.
        Uses str_source_borrows (separate from the main borrow system,
        since PendingStrType is a value type and not tracked there).
        """
        str_var_ids = self.str_source_borrows.get(storage)
        if not str_var_ids:
            return
        for str_var_id in str_var_ids:
            info = self.str_vars.get(str_var_id)
            if info is not None:
                info.source_mutated = True

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
