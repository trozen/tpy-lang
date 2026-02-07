"""
TurboPython Semantic Analyzer

Main orchestrator that wires all components together.
"""

from __future__ import annotations
from typing import Optional

from ..typesys import TpyType, TypeRegistry, NamedType, STR
from ..namespace import Namespace
from ..parse import TpyModule, TpyRecord, TpyFunction, TpyExpr, TpyStmt

from .diagnostics import Scope, Diagnostic, SemanticError
from .context import SemanticContext, RecordContext, MODULE_INIT_CONTEXT
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

        # Wire up compatibility's deferred dependencies
        self.compat.set_deps(self.type_ops, self.protocols)

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

        # Layer 4: Wire circular refs via explicit setters
        self.expr.set_cross_deps(self.calls, self.methods)
        self.calls.set_cross_deps(self.expr)
        self.methods.set_cross_deps(self.expr, self.calls)
        self.stmts.set_cross_deps(self.expr)

        # Convenience aliases for public API
        self.registry = self.ctx.registry
        self.global_scope = self.ctx.global_scope
        self.diagnostics = self.ctx.diagnostics

        # Register all builtins at init time, before user modules are registered
        # User modules registered later (in Compiler._analyze_module) will overwrite
        # builtins with the same name, allowing user code to shadow math/time/sys.
        for protocol_def in builtin_modules.get_all_protocols():
            info = builtin_modules.protocol_def_to_info(protocol_def)
            self.ctx.registry.register_protocol(info)
        self.registrar.register_builtin_types()
        self.registrar.register_builtin_functions()
        self.registrar.register_builtin_modules()

        # Populate builtins_ns with Python builtins (always available without import)
        # These are like CPython's builtins module - int, str, list, len, print, etc.
        self._register_python_builtins()

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

    def _register_python_builtins(self) -> None:
        """Register Python builtins in builtins_ns (always available without import).

        This mirrors CPython's builtins module. Names like int, str, len, print
        are available in every scope without explicit import.
        """
        # Python builtin types (as constructors)
        # These are registered as IMPORTED_NAME from "builtins" module
        # Generic types (list) are also registered - they're handled by generic type
        # inference code but need namespace entry to distinguish from tpy types
        python_builtin_types = ["int", "str", "bool", "float", "list"]
        for name in python_builtin_types:
            self.ctx.builtins_ns.bind_imported_name(name, "builtins", name)

        # Python builtin functions
        # All go through namespace; special handling is in IMPORTED_NAME handler
        python_builtin_functions = ["len", "chr", "abs", "min", "max", "ord",
                                    "print", "range", "enumerate", "zip"]
        for name in python_builtin_functions:
            self.ctx.builtins_ns.bind_imported_name(name, "builtins", name)

    def analyze(self, module: TpyModule, module_name: str = "__main__") -> None:
        """Analyze a module for semantic correctness.

        Args:
            module: The parsed module AST.
            module_name: Name of this module ("__main__" for entry point).

        Note: User module dependencies should be registered in registry.modules
        before calling this method (via register_module).
        """
        # Set module context
        self.ctx.module_name = module_name

        # Convert parse warnings to diagnostics
        for warning in module.parse_warnings:
            self.ctx.warning_from_loc(warning.message, warning.loc)

        # Process imports
        self.ctx.imports = module.imports
        for import_module_name, names in self.ctx.imports.items():
            if names == "*":
                # "from tpy import *" - register all tpy exports
                self.registrar.register_tpy_star_import()
            elif names is not None:
                # "from X import Y" or "from X import Y as Z"
                # Stored as (original_name, local_name) tuples to support aliases
                for original_name, local_name in names:
                    self.ctx.imported_names[local_name] = (import_module_name, original_name)
                    self.ctx.global_ns.bind_imported_name(local_name, import_module_name, original_name)

                    # For user module imports, also register the items for type checking
                    if import_module_name in module.user_module_imports:
                        self._register_user_module_import(import_module_name, original_name, local_name)
            else:
                # "import X" - register module name
                # Check if there's an alias from "from . import submod"
                alias = module.module_aliases.get(import_module_name)
                self.ctx.global_ns.bind_module(import_module_name, alias)

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

        # Add parameters to scope (resolve types to handle imported protocols)
        for pname, ptype in func.params:
            resolved_ptype = self.type_ops.resolve_type(ptype)
            self.ctx.current_scope.define(pname, resolved_ptype)

        # Set up local namespace
        local_ns = Namespace(parent=self.ctx.global_ns)
        for pname, ptype in func.params:
            resolved_ptype = self.type_ops.resolve_type(ptype)
            local_ns.bind_variable(pname, resolved_ptype)
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
        # Set up record context for super() support and generic type params
        self.ctx.record_ctx.record = record
        self.ctx.record_ctx.type_params = record.type_params if record.type_params else None
        self.ctx.record_ctx.type_param_kinds = record.type_param_kinds if record.type_param_kinds else None
        self.ctx.record_ctx.type_param_bounds = record.type_param_bounds if record.type_param_bounds else None

        for method in record.methods:
            self.ctx.reset_function_tracking()
            self.ctx.current_function = method
            self.ctx.current_scope = Scope(parent=self.ctx.global_scope)

            # Add 'self' as the record type (skip for static methods)
            if not method.is_staticmethod:
                self.ctx.current_scope.define("self", NamedType(record.name))

            # Add parameters
            for pname, ptype in method.params:
                self.ctx.current_scope.define(pname, ptype)

            # Set up local namespace
            local_ns = Namespace(parent=self.ctx.global_ns)
            if not method.is_staticmethod:
                local_ns.bind_variable("self", NamedType(record.name))
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

        self.ctx.record_ctx = RecordContext()

    def _analyze_top_level(self, stmts: list[TpyStmt]) -> None:
        """Analyze top-level statements (for generated main()).

        Treats top-level code like a function body so list literals and other
        constructs go through the same analysis path.
        """
        self.ctx.reset_function_tracking()
        self.ctx.current_function = MODULE_INIT_CONTEXT
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

    def _register_user_module_import(self, module_name: str, original_name: str, local_name: str) -> None:
        """Register an imported item from a user module.

        Looks up the item in the unified registry (user modules are registered
        as ModuleInfo before analysis) and registers it in the appropriate
        namespace (function, record, or protocol).
        """
        module_info = self.ctx.registry.get_module(module_name)
        if module_info is None:
            # Module not in registry - could be builtin without user file, skip
            return
        if module_info.is_builtin:
            # Builtin module (no user file shadowing it), skip to let builtin handling work
            return

        # Check for function (now always list of overloads)
        if module_info.functions and original_name in module_info.functions:
            overloads = module_info.functions[original_name]
            # For user modules, single overload - take first
            func_info = overloads[0]
            # Register with local name for lookup
            self.ctx.registry.register_function(func_info, local_name)
            self.ctx.user_imported_functions[local_name] = (module_name, original_name)
            return

        # Check for record
        if module_info.records and original_name in module_info.records:
            record_info = module_info.records[original_name]
            # Register with local name for lookup
            self.ctx.registry.register_record(record_info, local_name)
            self.ctx.user_imported_records[local_name] = (module_name, original_name)
            return

        # Check for protocol
        if module_info.protocols and original_name in module_info.protocols:
            protocol_info = module_info.protocols[original_name]
            # Register with local name for lookup (supports aliases)
            self.ctx.registry.register_protocol(protocol_info, local_name)
            # Also bind in namespace so it can be resolved as a type
            self.ctx.global_ns.bind_imported_name(local_name, module_name, original_name)
            self.ctx.user_imported_protocols[local_name] = (module_name, original_name)
            return

        # Check for variable
        if module_info.variables and original_name in module_info.variables:
            var_info = module_info.variables[original_name]
            self.ctx.global_scope.define(local_name, var_info.type)
            self.ctx.global_ns.bind_variable(local_name, var_info.type)
            self.ctx.user_imported_variables[local_name] = (module_name, original_name)
            return

        raise self._error(f"'{original_name}' not found in module '{module_name}'")
