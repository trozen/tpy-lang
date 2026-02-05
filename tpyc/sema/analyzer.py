"""
TurboPython Semantic Analyzer

Main orchestrator that wires all components together.
"""

from __future__ import annotations
from typing import Optional

from ..typesys import TpyType, TypeRegistry, RecordType, STR
from ..namespace import Namespace
from ..parse import TpyModule, TpyRecord, TpyFunction, TpyExpr, TpyStmt

from .diagnostics import Scope, Diagnostic, SemanticError
from .context import SemanticContext
from .type_ops import TypeOperations
from .operators import OperatorResolver
from .compatibility import TypeCompatibility
from .list_literals import ListLiteralTracker
from .protocols import ProtocolChecker
from .registration import TypeRegistrar
from .expressions import ExpressionAnalyzer
from .calls import CallAnalyzer
from .methods import MethodAnalyzer
from .statements import StatementAnalyzer

from tpyc import modules as builtin_modules


class SemanticAnalyzer:
    """Semantic analyzer for TurboPython."""

    def __init__(self):
        # Create shared context
        self.ctx = SemanticContext(
            registry=TypeRegistry(),
            global_scope=Scope(),
            builtins_ns=Namespace(),
            global_ns=None,  # Set below
        )
        self.ctx.global_ns = Namespace(parent=self.ctx.builtins_ns)

        # Layer 1: No dependencies on other analyzers
        self.type_ops = TypeOperations(self.ctx)
        self.operators = OperatorResolver(self.ctx)
        self.compat = TypeCompatibility(self.ctx)
        self.list_tracker = ListLiteralTracker(self.ctx)

        # Layer 2: Depends on type_ops
        self.protocols = ProtocolChecker(self.ctx, self.type_ops)
        self.registrar = TypeRegistrar(self.ctx, self.type_ops, self.protocols)

        # Wire up compatibility's dependencies
        self.compat.type_ops = self.type_ops
        self.compat.protocols = self.protocols

        # Layer 3: Analyzers with circular deps - create first
        self.expr = ExpressionAnalyzer(
            self.ctx, self.type_ops, self.operators, self.protocols, self.compat
        )
        self.calls = CallAnalyzer(
            self.ctx, self.type_ops, self.protocols, self.compat, self.list_tracker
        )
        self.methods = MethodAnalyzer(
            self.ctx, self.type_ops, self.protocols, self.compat
        )
        self.stmts = StatementAnalyzer(
            self.ctx, self.type_ops, self.compat, self.list_tracker, self.protocols
        )

        # Layer 4: Wire circular refs
        self.expr.calls = self.calls
        self.expr.methods = self.methods
        self.calls.expr = self.expr
        self.methods.expr = self.expr
        self.stmts.expr = self.expr

        # Convenience aliases for public API
        self.registry = self.ctx.registry
        self.global_scope = self.ctx.global_scope
        self.diagnostics = self.ctx.diagnostics

    # Public API compatibility properties
    @property
    def current_scope(self) -> Optional[Scope]:
        return self.ctx.current_scope

    @property
    def current_function(self) -> Optional[TpyFunction]:
        return self.ctx.current_function

    @property
    def expr_types(self) -> dict[int, TpyType]:
        return self.ctx.expr_types

    @property
    def var_types(self) -> dict[int, TpyType]:
        return self.ctx.var_types

    @property
    def imports(self) -> dict:
        return self.ctx.imports

    @property
    def imported_names(self) -> dict:
        return self.ctx.imported_names

    @property
    def list_literals(self) -> dict:
        return self.ctx.list_literals

    @property
    def global_ns(self):
        return self.ctx.global_ns

    @property
    def builtins_ns(self):
        return self.ctx.builtins_ns

    def _error(self, message: str, node: TpyExpr | TpyStmt | None = None) -> SemanticError:
        """Create a SemanticError with location from a node."""
        return self.ctx.error(message, node)

    def _warning(self, message: str, node: TpyExpr | TpyStmt | TpyRecord | None = None) -> None:
        """Record a warning diagnostic."""
        self.ctx.warning(message, node)

    def analyze(self, module: TpyModule) -> None:
        """Analyze a module for semantic correctness."""
        # Register all builtin protocols into the unified registry
        for protocol_def in builtin_modules.get_all_protocols():
            info = builtin_modules.protocol_def_to_info(protocol_def)
            self.ctx.registry.register_protocol(info)

        # Register all builtin types as RecordInfo for unified lookup
        self.registrar.register_builtin_types()

        # Register all builtin functions for unified lookup
        self.registrar.register_builtin_functions()

        # Register all builtin modules for unified lookup
        self.registrar.register_builtin_modules()

        # Process imports
        self.ctx.imports = module.imports
        for module_name, names in self.ctx.imports.items():
            if names is not None:
                # "from X import Y" or "from X import Y as Z"
                # Stored as (original_name, local_name) tuples to support aliases
                for original_name, local_name in names:
                    self.ctx.imported_names[local_name] = (module_name, original_name)
                    self.ctx.global_ns.bind_imported_name(local_name, module_name, original_name)
            else:
                # "import X" - register module name
                self.ctx.global_ns.bind_module(module_name)

        # First pass: register all records
        for record in module.records:
            self.registrar.register_record(record)

        # Register protocols (two phases to allow forward references)
        for protocol in module.protocols:
            self.registrar.register_protocol(protocol)
        for protocol in module.protocols:
            self.registrar.validate_protocol_parents(protocol)

        # Validate inheritance relationships (after all records and protocols are registered)
        for record in module.records:
            self.registrar.validate_record_inheritance(record)

        # Second pass: register all functions
        for func in module.functions:
            self.registrar.register_function(func)

        # Third pass: register top-level variable declarations (globals)
        if module.top_level_stmts:
            self.registrar.register_globals(module.top_level_stmts)

        # Register module-level __name__ (user assignments will overwrite at runtime)
        self.ctx.global_scope.define("__name__", STR)
        self.ctx.global_ns.bind_variable("__name__", STR)

        # Fourth pass: analyze top-level statements (globals must be in scope for functions)
        if module.top_level_stmts:
            self._analyze_top_level(module.top_level_stmts)

        # Fifth pass: analyze record methods
        for record in module.records:
            self._analyze_record_methods(record)

        # Sixth pass: analyze function bodies
        for func in module.functions:
            self._analyze_function(func)

    def _analyze_function(self, func: TpyFunction) -> None:
        """Analyze a function body."""
        self.ctx.reset_function_tracking()
        self.ctx.current_function = func
        self.ctx.current_scope = Scope(parent=self.ctx.global_scope)

        # Add parameters to scope
        for pname, ptype in func.params:
            self.ctx.current_scope.define(pname, ptype)

        # Set up local namespace
        local_ns = Namespace(parent=self.ctx.global_ns)
        for pname, ptype in func.params:
            local_ns.bind_variable(pname, ptype)
        self.ctx.current_ns = local_ns

        # Analyze body
        for stmt in func.body:
            self.stmts.analyze_stmt(stmt)

        # Resolve pending list types after analyzing the full function
        self.list_tracker.resolve_pending_list_types()

        self.ctx.current_function = None
        self.ctx.current_scope = None
        self.ctx.current_ns = None

    def _analyze_record_methods(self, record: TpyRecord) -> None:
        """Analyze all methods of a record."""
        # Track current record for super() support
        self.ctx.current_record = record
        # Track type parameters for generic records (allows TypeParamRef in method locals)
        self.ctx.current_record_type_params = record.type_params if record.type_params else None
        self.ctx.current_record_type_param_kinds = record.type_param_kinds if record.type_param_kinds else None
        self.ctx.current_record_type_param_bounds = record.type_param_bounds if record.type_param_bounds else None

        for method in record.methods:
            self.ctx.reset_function_tracking()
            self.ctx.current_function = method
            self.ctx.current_scope = Scope(parent=self.ctx.global_scope)

            # Add 'self' as the record type (skip for static methods)
            if not method.is_staticmethod:
                self.ctx.current_scope.define("self", RecordType(record.name))

            # Add parameters
            for pname, ptype in method.params:
                self.ctx.current_scope.define(pname, ptype)

            # Set up local namespace
            local_ns = Namespace(parent=self.ctx.global_ns)
            if not method.is_staticmethod:
                local_ns.bind_variable("self", RecordType(record.name))
            for pname, ptype in method.params:
                local_ns.bind_variable(pname, ptype)
            self.ctx.current_ns = local_ns

            # Analyze body
            for stmt in method.body:
                self.stmts.analyze_stmt(stmt)

            # Validate super().__init__() position in __init__ methods
            if method.name == "__init__" and self.ctx.super_init_call is not None:
                # super().__init__() must be the first non-docstring statement
                first_real_stmt = MethodAnalyzer.find_first_non_docstring_stmt(method.body)
                if first_real_stmt is not None:
                    # Check if first real statement contains the super().__init__() call
                    if not MethodAnalyzer.stmt_contains_super_init(first_real_stmt, self.ctx.super_init_call):
                        raise self._error(
                            "super().__init__() must be the first statement in __init__",
                            self.ctx.super_init_call
                        )

            # Resolve pending list types after analyzing the full method
            self.list_tracker.resolve_pending_list_types()

            self.ctx.current_scope = None
            self.ctx.current_function = None
            self.ctx.current_ns = None

        self.ctx.current_record = None
        self.ctx.current_record_type_params = None
        self.ctx.current_record_type_param_kinds = None
        self.ctx.current_record_type_param_bounds = None

    def _analyze_top_level(self, stmts: list[TpyStmt]) -> None:
        """Analyze top-level statements (for generated main()).

        Treats top-level code like a function body so list literals and other
        constructs go through the same analysis path.
        """
        self.ctx.reset_function_tracking()
        # Use a sentinel to indicate we're in "module init" context (not None, but not a real function)
        self.ctx.current_function = True  # type: ignore
        self.ctx.current_scope = Scope(parent=self.ctx.global_scope)
        self.ctx.is_top_level = True

        # Set up namespace - use global_ns for top-level (globals are visible)
        # New local variables will be added to global_ns as they're declared
        self.ctx.current_ns = self.ctx.global_ns

        for stmt in stmts:
            self.stmts.analyze_stmt(stmt)

        # Resolve pending list types (same as function analysis)
        self.list_tracker.resolve_pending_list_types()

        self.ctx.current_function = None
        self.ctx.current_scope = None
        self.ctx.current_ns = None
        self.ctx.is_top_level = False

    def get_expr_type(self, expr: TpyExpr) -> Optional[TpyType]:
        """Get the cached type of an expression."""
        return self.ctx.get_expr_type(expr)
