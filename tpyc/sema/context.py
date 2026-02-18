"""
TurboPython Semantic Analysis Context

Contains the shared state that is passed to all semantic analysis components.
"""

from __future__ import annotations
from dataclasses import dataclass, field

from ..typesys import (
    TpyType, TypeRegistry, ListLiteralInfo, TypeParamKind, IntLiteralType,
    FixedIntType, INT32, BIGINT,
)
from ..namespace import Namespace
from ..parse import TpyExpr, TpyStmt, TpyRecord, TpyFunction, TpyVarDecl, TpyMethodCall
from .diagnostics import Diagnostic, DiagnosticLevel, SemanticError, Scope


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
    var_decl_by_name: dict[str, TpyVarDecl] = field(default_factory=dict)

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
    top_level_decls: dict[str, int] = field(default_factory=dict)

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
    loop_vars: set[str] = field(default_factory=set)

    # --- Scope escape tracking ---
    var_scope_depth: dict[str, int] = field(default_factory=dict)
    hoisted_vars: set[str] = field(default_factory=set)
    rvalue_vars: set[str] = field(default_factory=set)

    # --- Last-use tracking for auto-move ---
    all_last_uses: set[int] = field(default_factory=set)
    # Prescan reassigned vars for current function (needed by _is_movable_var)
    current_reassigned_vars: set[str] = field(default_factory=set)
    # Reassigned vars with at least one lvalue (non-rvalue) reassignment
    current_lvalue_reassigned: set[str] = field(default_factory=set)

    # --- Definite-assignment tracking ---
    definitely_assigned: set[str] = field(default_factory=set)
    init_terminated: bool = False
    # Variables proven non-None at current control-flow point.
    non_none_vars: set[str] = field(default_factory=set)
    # Expression identities (e.g. x.field, x[i]) proven non-None.
    non_none_exprs: set[tuple[str, ...]] = field(default_factory=set)

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

    def warning(self, message: str, node: TpyExpr | TpyStmt | TpyRecord | None = None) -> None:
        """Record a warning diagnostic (doesn't stop compilation)."""
        loc = getattr(node, 'loc', None) if node else None
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, loc))

    def warning_from_loc(self, message: str, loc: 'SourceLocation | None') -> None:
        """Record a warning diagnostic from a SourceLocation."""
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, loc))

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
        self.super_init_call = None
        self.loop_vars.clear()
        self.var_scope_depth.clear()
        self.hoisted_vars.clear()
        self.rvalue_vars.clear()
        self.current_reassigned_vars.clear()
        self.current_lvalue_reassigned.clear()
        self.definitely_assigned.clear()
        self.init_terminated = False
        self.non_none_vars.clear()
        self.non_none_exprs.clear()
        self.literal_default_vars.clear()
        self.literal_values.clear()
        self.unresolved_none_vars.clear()
        self.write_history.clear()
        self.authoritative_types.clear()
        self.global_declarations.clear()
        self.param_provenance_vars.clear()
        self.non_null_ptr_vars.clear()
