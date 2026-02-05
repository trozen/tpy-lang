"""
TurboPython Semantic Analysis Context

Contains the shared state that is passed to all semantic analysis components.
"""

from __future__ import annotations
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..typesys import (
    TpyType, TypeRegistry, ListLiteralInfo, TypeParamKind
)
from ..namespace import Namespace
from ..parse import TpyExpr, TpyStmt, TpyRecord, TpyFunction, TpyVarDecl, TpyMethodCall
from .diagnostics import Diagnostic, DiagnosticLevel, SemanticError, Scope

if TYPE_CHECKING:
    from ..compiler import ModuleExports


@dataclass
class SemanticContext:
    """Shared state for all semantic analysis components."""
    registry: TypeRegistry
    global_scope: Scope

    # Current scope during analysis
    current_scope: Scope | None = None
    current_function: TpyFunction | None = None

    # Expression/variable type cache
    expr_types: dict[int, TpyType] = field(default_factory=dict)
    var_types: dict[int, TpyType] = field(default_factory=dict)

    # Current record context (for generic type params)
    current_record: TpyRecord | None = None
    current_record_type_params: list[str] | None = None
    current_record_type_param_kinds: list[TypeParamKind] | None = None
    current_record_type_param_bounds: dict[str, TpyType] | None = None

    # List literal tracking
    literal_counter: int = 0
    list_literals: dict[int, ListLiteralInfo] = field(default_factory=dict)
    variable_to_literal: dict[str, int] = field(default_factory=dict)
    pending_resolutions: list[int] = field(default_factory=list)
    var_decl_by_name: dict[str, TpyVarDecl] = field(default_factory=dict)

    # Import tracking
    # imports: module_name -> set of (original_name, local_name) tuples (for "from X import Y as Z")
    #          module_name -> None (for "import X")
    #          module_name -> "*" (for "from X import *")
    imports: dict[str, set[tuple[str, str]] | None | str] = field(default_factory=dict)
    # imported_names: name -> (module_name, function_name) for direct function access
    imported_names: dict[str, tuple[str, str]] = field(default_factory=dict)

    # Cross-module support
    # available_modules: exports from already-compiled modules (populated before analysis)
    available_modules: dict[str, 'ModuleExports'] = field(default_factory=dict)
    # module_name: name of this module ("__main__" for entry point, "module_name" for imports)
    module_name: str = "__main__"
    # Track imported items from user modules for codegen qualification
    # Maps local_name -> (source_module, original_name) to support import aliases
    user_imported_functions: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_records: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_protocols: dict[str, tuple[str, str]] = field(default_factory=dict)
    user_imported_variables: dict[str, tuple[str, str]] = field(default_factory=dict)
    # Track top-level declarations: name -> line number where declared
    # Used for export filtering and order-aware codegen (imports used before redefinition)
    top_level_decls: dict[str, int] = field(default_factory=dict)

    # Built-in names (reserved for future builtins if needed)
    builtin_names: dict[str, TpyType] = field(default_factory=dict)

    # Unified namespace system
    # builtins_ns: root namespace containing built-in names
    # global_ns: module-level namespace (records, functions, imports, global vars)
    # current_ns: current scope during analysis (local_ns -> global_ns -> builtins_ns)
    builtins_ns: Namespace | None = None
    global_ns: Namespace | None = None
    current_ns: Namespace | None = None

    # Control flow
    loop_depth: int = 0
    is_top_level: bool = False

    # Track super().__init__() calls in current __init__ method for validation
    super_init_call: TpyMethodCall | None = None

    # Diagnostics
    diagnostics: list[Diagnostic] = field(default_factory=list)

    def error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> SemanticError:
        """Create a SemanticError with location from a node."""
        loc = getattr(node, 'loc', None) if node else None
        return SemanticError(message, loc)

    def warning(self, message: str, node: TpyExpr | TpyStmt | TpyRecord | None = None) -> None:
        """Record a warning diagnostic (doesn't stop compilation)."""
        loc = getattr(node, 'loc', None) if node else None
        self.diagnostics.append(Diagnostic(DiagnosticLevel.WARNING, message, loc))

    def get_expr_type(self, expr: TpyExpr) -> TpyType | None:
        """Get the cached type of an expression."""
        return self.expr_types.get(id(expr))

    def set_expr_type(self, expr: TpyExpr, typ: TpyType) -> None:
        """Cache the type of an expression."""
        self.expr_types[id(expr)] = typ

    def reset_function_tracking(self) -> None:
        """Reset per-function tracking state between function analyses."""
        self.variable_to_literal.clear()
        self.pending_resolutions.clear()
        self.super_init_call = None
