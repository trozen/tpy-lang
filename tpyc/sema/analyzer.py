"""
TurboPython Semantic Analyzer

Main orchestrator that wires all components together.
"""

from __future__ import annotations
from typing import Optional

from ..typesys import (
    TpyType, TypeRegistry, NamedType, UnionType, FinalType, STR, VoidType,
    INT32, ReadonlyType, unwrap_readonly, OwnType, OptionalType, RecordInfo, FieldInfo,
    EnumType,
)
from ..namespace import Namespace
from ..parse import TpyModule, TpyRecord, TpyFunction, TpyExpr, TpyStmt, TpyVarDecl, is_super_del_call
from .registration import build_record_self_type
from ..parse.nodes import (
    TpyStrLiteral, TpyAssign, TpyIf, TpyWhile, TpyForEach, TpyFieldAccess, TpyName,
)

from .diagnostics import Scope, Diagnostic, SemanticError
from .context import SemanticContext, RecordContext, MODULE_INIT_CONTEXT
from .type_ops import TypeOperations
from .operators import OperatorResolver
from .compatibility import TypeCompatibility
from .list_literals import IterableHelper
from .local_deduction import LocalTypeDeduction
from .protocols import ProtocolChecker
from .registration import TypeRegistrar
from .narrowing import NarrowingTracker
from .expressions import ExpressionAnalyzer
from .calls import CallAnalyzer
from .methods import MethodAnalyzer
from .statements import StatementAnalyzer

from ..prescan import ScanResult, scan_reassigned_vars
from ..liveness import analyze_last_uses
from tpyc import modules as builtin_modules


class SemanticAnalyzer:
    """Semantic analyzer for TurboPython."""

    def __init__(self, default_int_type: TpyType = INT32):
        # Create shared context
        self.ctx = SemanticContext(
            registry=TypeRegistry(),
            global_scope=Scope(),
            default_int_type=default_int_type,
            builtins_ns=Namespace(),
            global_ns=None,  # Set below
        )
        self.ctx.global_ns = Namespace(parent=self.ctx.builtins_ns)

        # Layer 1: No dependencies on other analyzers
        self.type_ops = TypeOperations(self.ctx)
        self.operators = OperatorResolver(self.ctx)
        self.compat = TypeCompatibility(self.ctx)
        self.iterable = IterableHelper(self.ctx)
        self.deduction = LocalTypeDeduction(self.ctx, self.compat)

        # Layer 2: Depends on type_ops
        self.protocols = ProtocolChecker(self.ctx, self.type_ops)
        self.registrar = TypeRegistrar(self.ctx, self.type_ops, self.protocols)

        # Wire up compatibility's deferred dependencies
        self.compat.set_deps(self.type_ops, self.protocols)

        # Narrowing tracker (depends on type_ops, protocols)
        self.narrowing = NarrowingTracker(self.ctx, self.type_ops, self.protocols)

        # Layer 3: Analyzers with circular deps - create first
        self.expr = ExpressionAnalyzer(
            self.ctx, self.type_ops, self.operators, self.protocols, self.compat, self.narrowing
        )
        self.calls = CallAnalyzer(
            self.ctx, self.type_ops, self.protocols, self.compat, self.deduction
        )
        self.methods = MethodAnalyzer(
            self.ctx, self.type_ops, self.protocols, self.compat, self.deduction
        )
        self.stmts = StatementAnalyzer(
            self.ctx, self.type_ops, self.compat, self.deduction, self.iterable,
            self.protocols, self.narrowing
        )

        # Layer 4: Wire circular refs via explicit setters
        self.expr.set_cross_deps(self.calls, self.methods)
        self.calls.set_cross_deps(self.expr)
        self.methods.set_cross_deps(self.expr, self.calls)
        self.stmts.set_cross_deps(self.expr)

        # Per-function/method pre-scan results (shared with codegen)
        self.function_scan_results: dict[int, ScanResult] = {}
        self.top_level_scan_result: ScanResult | None = None

        # Per-function/method hoisted vars (scope escape phase 2)
        self.function_hoisted_vars: dict[int, set[str]] = {}
        self.top_level_hoisted_vars: set[str] = set()

        # Per-function/method move-through vars (lvalue alias promoted to rvalue)
        self.function_move_through_vars: dict[int, set[str]] = {}
        self.top_level_move_through_vars: set[str] = set()

        # Per-function `global x` declarations (for codegen)
        self.function_global_decls: dict[int, set[str]] = {}

        # Branch-declared vars that need pre-declaration before if-statements
        self.if_branch_decls: dict[int, dict[str, TpyType]] = {}

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
        self._validate_builtin_extends()

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

    def _validate_builtin_extends(self) -> None:
        """Validate that builtin types conform to their declared extends protocols.

        Uses the same conformance checker as user classes (type_conforms_to_protocol).
        Marker protocols (no methods) are skipped -- the extends declaration is
        the entire conformance for those.
        """
        import re
        from ..typesys import TypeParamRef, TypeParamKind
        for module in builtin_modules.get_all_modules():
            for qname, type_def in module.types.items():
                if not type_def.extends:
                    continue
                simple_name = qname.split(".")[-1]

                # Build a TpyType for this builtin (with TypeParamRef args for generics)
                if type_def.type_obj is not None:
                    actual_type = type_def.type_obj
                elif type_def.type_factory and type_def.type_params:
                    args = []
                    for tp, kind in zip(type_def.type_params, type_def.param_kinds):
                        if kind == TypeParamKind.INT:
                            args.append(1)  # placeholder int value
                        else:
                            args.append(TypeParamRef(tp))
                    actual_type = type_def.type_factory(*args)
                else:
                    continue

                for ext_str in type_def.extends:
                    match = re.match(r"(\w+)(?:\[(.+)\])?", ext_str)
                    if not match:
                        continue
                    proto_name = match.group(1)
                    proto_info = self.ctx.registry.get_protocol(proto_name)
                    if proto_info is None or proto_info.is_marker:
                        continue
                    # Build protocol NamedType with matching type args
                    proto_args = tuple(
                        TypeParamRef(a.strip()) for a in match.group(2).split(",")
                    ) if match.group(2) else ()
                    protocol = NamedType(proto_name, proto_args, is_protocol=True)

                    if not self.protocols.type_conforms_to_protocol(actual_type, protocol):
                        missing = self.protocols.get_missing_protocol_methods(actual_type, protocol)
                        methods_str = ", ".join(missing) if missing else "unknown"
                        raise ValueError(
                            f"Builtin type '{simple_name}' extends '{proto_name}' "
                            f"but does not conform: missing {methods_str}"
                        )

    def _register_python_builtins(self) -> None:
        """Register Python builtins in builtins_ns (always available without import).

        This mirrors CPython's builtins module. Names like int, str, len, print
        are available in every scope without explicit import.
        """
        # Python builtin types (as constructors)
        # These are registered as IMPORTED_NAME from "builtins" module
        # Generic types (list) are also registered - they're handled by generic type
        # inference code but need namespace entry to distinguish from tpy types
        python_builtin_types = ["int", "str", "bool", "float", "list", "dict"]
        for name in python_builtin_types:
            self.ctx.builtins_ns.bind_imported_name(name, "builtins", name)

        # Python builtin functions
        # All go through namespace; special handling is in IMPORTED_NAME handler
        python_builtin_functions = ["len", "chr", "abs", "min", "max", "ord",
                                    "print", "range", "enumerate", "zip",
                                    "isinstance"]
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
        self.ctx.bare_module_imports = module.bare_module_imports
        for import_module_name, names in self.ctx.imports.items():
            if names == "*":
                # "from tpy import *" - register all tpy exports
                self.registrar.register_tpy_star_import()
            elif isinstance(names, set):
                # "from X import Y" or "from X import Y as Z"
                # Stored as (original_name, local_name) tuples to support aliases
                for original_name, local_name in names:
                    self.ctx.imported_names[local_name] = (import_module_name, original_name)
                    self.ctx.global_ns.bind_imported_name(local_name, import_module_name, original_name)

                    # For user module imports, also register the items for type checking
                    if import_module_name in module.user_module_imports:
                        self._register_user_module_import(import_module_name, original_name, local_name)
            elif names is None:
                # "import X" for special modules (tpy, typing, etc.)
                alias = module.module_aliases.get(import_module_name)
                self.ctx.global_ns.bind_module(import_module_name, alias)

        # Bind modules for bare `import X` statements (user modules)
        for mod_name in module.bare_module_imports:
            alias = module.module_aliases.get(mod_name)
            self.ctx.global_ns.bind_module(mod_name, alias)

        # Resolve imported type aliases in AST type annotations.
        # The parser creates NamedType("Shape") for imported aliases since it
        # doesn't know about cross-module aliases at parse time. Substitute them
        # with the resolved types before registration/analysis.
        if self.ctx.user_imported_type_aliases:
            self._resolve_imported_aliases(module)

        # Resolve imported enum types in AST type annotations.
        # Same issue as aliases: parser creates NamedType("Color") for imported
        # enums since it doesn't have cross-module type info at parse time.
        if self.ctx.user_imported_enums:
            self._resolve_imported_enums(module)

        # First pass: register all records and enums
        for record in module.records:
            self.registrar.register_record(record)
        for enum in module.enums:
            self.registrar.register_enum(enum)

        # Register protocols (two phases to allow forward references)
        for protocol in module.protocols:
            self.registrar.register_protocol(protocol)
        for protocol in module.protocols:
            self.registrar.validate_protocol_parents(protocol)

        # Validate inheritance relationships (after all records and protocols are registered)
        for record in module.records:
            self.registrar.validate_record_inheritance(record)

        # Validate ValueType fields in a second pass (all ValueType flags are set now)
        for record in module.records:
            self.registrar.validate_value_type_fields(record)

        # Propagate @nocopy from fields to containing records.
        # Done after inheritance validation so parent types are resolved.
        # Definition order handles transitive propagation naturally.
        self._propagate_nocopy(module)

        # Transfer type aliases from parser to sema registry, validating members
        for name, (typ, loc) in module.type_aliases.items():
            self._validate_type_alias_members(name, typ, loc)
            self.ctx.registry.register_type_alias(name, typ)

        # Second pass: register all functions
        for func in module.functions:
            self.registrar.register_function(func)

        # Inject synthetic __name__: Final[str] before registration so it flows
        # through the same path as user-defined Finals (single source of truth)
        module_name = self.ctx.module_name
        name_decl = TpyVarDecl(
            name="__name__",
            type=FinalType(STR),
            init=TpyStrLiteral(value=module_name),
        )
        if module.top_level_stmts is None:
            module.top_level_stmts = []
        module.top_level_stmts.insert(0, name_decl)

        # Third pass: register top-level variable declarations (globals)
        self.registrar.register_globals(module.top_level_stmts)

        # Fourth pass: analyze top-level statements (globals must be in scope for functions)
        if module.top_level_stmts:
            self._analyze_top_level(module.top_level_stmts)

        # Fifth pass: analyze record methods
        for record in module.records:
            self._analyze_record_methods(record)

        # Sixth pass: analyze function bodies
        for func in module.functions:
            self._analyze_function(func)

    def _is_type_nocopy(self, typ: TpyType) -> bool:
        """Delegate to canonical is_type_nocopy on context."""
        return self.ctx.is_type_nocopy(typ)

    def _propagate_nocopy(self, module: TpyModule) -> None:
        """Propagate nocopy from fields/parents to containing records.

        Processes records in definition order. If any field's type is nocopy,
        the containing record becomes nocopy too. Also checks parent type.
        Skips records that define __copy__ (opt-out escape hatch).
        """
        for record in module.records:
            info = self.ctx.registry.get_record(record.name)
            if info is None or info.is_nocopy:
                continue
            # __copy__ opts out of propagation
            if info.has_copy:
                continue
            # Check parent
            if info.parent is not None and self._is_type_nocopy(info.parent):
                info.is_nocopy = True
                continue
            # Check fields
            for f in info.fields:
                if self._is_type_nocopy(f.type):
                    info.is_nocopy = True
                    break

    def _normalize_param_type(self, ptype: TpyType, is_readonly_ctx: bool) -> TpyType:
        """Normalize a parameter type for readonly context.

        Strips ReadonlyType from value types (copies are always safe).
        Wraps non-value types with ReadonlyType in @readonly contexts.
        """
        if isinstance(ptype, ReadonlyType) and ptype.wrapped.is_value_type():
            return ptype.wrapped
        if is_readonly_ctx and not isinstance(ptype, ReadonlyType):
            if not ptype.is_value_type():
                return ReadonlyType(ptype)
        return ptype

    def _analyze_function(self, func: TpyFunction) -> None:
        """Analyze a function body."""
        # Stub functions (extern imports with ... body) have no body to analyze
        if func.is_stub:
            return

        self.ctx.reset_function_tracking()

        self.ctx.current_function = func
        # Resolve return type (sets is_protocol for cross-module imports)
        func.return_type = self.type_ops.resolve_type(func.return_type)
        self.ctx.current_scope = Scope(parent=self.ctx.global_scope)

        # Add parameters to scope and namespace. ReadonlyType is kept for
        # type-based enforcement; @readonly wraps all non-value params.
        local_ns = Namespace(parent=self.ctx.global_ns)
        for i, (pname, ptype) in enumerate(func.params):
            resolved_ptype = self._normalize_param_type(
                self.type_ops.resolve_type(ptype), func.is_readonly)
            # Propagate resolved types to AST so codegen sees ReadonlyType on
            # @readonly params. Idempotent: _normalize_param_type is a no-op on
            # already-wrapped types.
            func.params[i] = (pname, resolved_ptype)
            self.ctx.current_scope.define(pname, resolved_ptype)
            self.ctx.var_scope_depth[pname] = self.ctx.current_scope.depth
            self.ctx.definitely_assigned.add(pname)
            local_ns.bind_variable(pname, resolved_ptype)
        self.ctx.current_ns = local_ns

        # Pre-scan for reassigned variables (shared with codegen)
        param_names = {pname for pname, _ in func.params}
        scan = scan_reassigned_vars(func.body, pre_declared=param_names)
        # Last-use analysis for auto-move (shared with codegen)
        self.ctx.all_last_uses |= analyze_last_uses(func.body, scan.alias_sources)
        self.ctx.current_reassigned_vars = scan.reassigned.copy()
        self.ctx.current_lvalue_reassigned = scan.lvalue_reassigned.copy()

        # Analyze body
        for stmt in func.body:
            self.stmts.analyze_stmt(stmt)
        self.deduction.resolve_all()

        self.function_scan_results[id(func)] = scan
        if self.ctx.hoisted_vars:
            self.function_hoisted_vars[id(func)] = self.ctx.hoisted_vars.copy()
        if self.ctx.move_through_vars:
            self.function_move_through_vars[id(func)] = self.ctx.move_through_vars.copy()
        if self.ctx.global_declarations:
            self.function_global_decls[id(func)] = self.ctx.global_declarations.copy()
        self.if_branch_decls.update(self.ctx.if_branch_decls)

        self.ctx.current_function = None
        self.ctx.current_scope = None
        self.ctx.current_ns = None

    def _validate_type_alias_members(
        self, alias_name: str, typ: TpyType, loc: 'SourceLocation | None'
    ) -> None:
        """Validate that all NamedType members in a type alias are registered."""
        from ..parse import SourceLocation
        members: list[TpyType] = []
        if isinstance(typ, UnionType):
            members = list(typ.members)
        elif isinstance(typ, NamedType):
            members = [typ]
        for m in members:
            if isinstance(m, NamedType) and not m.is_protocol and not m.is_module_type:
                if self.ctx.registry.get_record(m.name) is None:
                    raise SemanticError(
                        f"Type alias '{alias_name}' references unknown type '{m.name}'",
                        loc,
                    )

    @staticmethod
    def _resolve_alias(typ: TpyType, aliases: dict[str, TpyType]) -> TpyType:
        """Recursively substitute alias NamedTypes with their resolved types."""
        if isinstance(typ, NamedType) and not typ.is_protocol and not typ.is_module_type:
            resolved = aliases.get(typ.name)
            if resolved is not None:
                return resolved
        return typ.map_inner_types(
            lambda t: SemanticAnalyzer._resolve_alias(t, aliases)
        )

    def _resolve_imported_aliases(self, module: TpyModule) -> None:
        """Substitute imported alias NamedTypes in module AST type annotations."""
        from ..parse.nodes import TpyVarDecl
        aliases = self.ctx.registry.type_aliases
        for func in module.functions:
            self._resolve_func_aliases(func, aliases)
        for record in module.records:
            for f in record.fields:
                f.type = self._resolve_alias(f.type, aliases)
            for method in record.methods:
                self._resolve_func_aliases(method, aliases)
        for stmt in module.top_level_stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.type is not None:
                stmt.type = self._resolve_alias(stmt.type, aliases)

    @staticmethod
    def _resolve_func_aliases(func: TpyFunction, aliases: dict[str, TpyType]) -> None:
        """Resolve alias types in a function's signature."""
        if func.return_type is not None:
            func.return_type = SemanticAnalyzer._resolve_alias(func.return_type, aliases)
        for i, (name, typ) in enumerate(func.params):
            resolved = SemanticAnalyzer._resolve_alias(typ, aliases)
            if resolved is not typ:
                func.params[i] = (name, resolved)

    def _resolve_imported_enums(self, module: TpyModule) -> None:
        """Substitute imported enum NamedTypes in module AST type annotations."""
        from ..parse.nodes import TpyVarDecl
        enums = {name: self.ctx.registry.get_enum(name)
                 for name in self.ctx.user_imported_enums}
        for func in module.functions:
            self._resolve_func_enums(func, enums)
        for record in module.records:
            for f in record.fields:
                f.type = self._resolve_enum(f.type, enums)
            for method in record.methods:
                self._resolve_func_enums(method, enums)
        for stmt in module.top_level_stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.type is not None:
                stmt.type = self._resolve_enum(stmt.type, enums)

    @staticmethod
    def _resolve_func_enums(func: TpyFunction, enums: dict[str, EnumType]) -> None:
        """Resolve enum types in a function's signature."""
        if func.return_type is not None:
            func.return_type = SemanticAnalyzer._resolve_enum(func.return_type, enums)
        for i, (name, typ) in enumerate(func.params):
            resolved = SemanticAnalyzer._resolve_enum(typ, enums)
            if resolved is not typ:
                func.params[i] = (name, resolved)

    @staticmethod
    def _resolve_enum(typ: TpyType, enums: dict[str, EnumType]) -> TpyType:
        """Recursively substitute NamedType placeholders with EnumType for imported enums."""
        if isinstance(typ, NamedType) and not typ.is_protocol:
            resolved = enums.get(typ.name)
            if resolved is not None:
                return resolved
        return typ.map_inner_types(
            lambda t: SemanticAnalyzer._resolve_enum(t, enums)
        )

    def _analyze_record_methods(self, record: TpyRecord) -> None:
        """Analyze all methods of a record."""
        # Set up record context for super() support and generic type params
        self.ctx.record_ctx.record = record
        self.ctx.record_ctx.type_params = record.type_params if record.type_params else None
        self.ctx.record_ctx.type_param_kinds = record.type_param_kinds if record.type_param_kinds else None
        self.ctx.record_ctx.type_param_bounds = record.type_param_bounds if record.type_param_bounds else None

        # Reject class defining both __next__ and __next_opt__ (codegen renames __next__ -> __next_opt__)
        method_names = {m.name for m in record.methods}
        if "__next__" in method_names and "__next_opt__" in method_names:
            next_method = next(m for m in record.methods if m.name == "__next__")
            raise self._error(
                "Cannot define both __next__ and __next_opt__ in the same class",
                next_method
            )

        for method in record.methods:
            self.ctx.reset_function_tracking()
    
            self.ctx.current_function = method
            # Resolve return type (sets is_protocol for cross-module imports)
            method.return_type = self.type_ops.resolve_type(method.return_type)
            self.ctx.current_scope = Scope(parent=self.ctx.global_scope)

            # Add self/params to scope and namespace. @readonly wraps non-value types.
            local_ns = Namespace(parent=self.ctx.global_ns)
            if not method.is_staticmethod:
                self_named = build_record_self_type(record)
                self_type = self._normalize_param_type(
                    self_named, method.is_readonly)
                self.ctx.current_scope.define("self", self_type)
                self.ctx.var_scope_depth["self"] = self.ctx.current_scope.depth
                self.ctx.definitely_assigned.add("self")
                local_ns.bind_variable("self", self_type)

            for i, (pname, ptype) in enumerate(method.params):
                resolved_ptype = self._normalize_param_type(
                    self.type_ops.resolve_type(ptype), method.is_readonly)
                # Propagate resolved types to AST so codegen sees ReadonlyType.
                # Idempotent: _normalize_param_type is a no-op on already-wrapped types.
                method.params[i] = (pname, resolved_ptype)
                self.ctx.current_scope.define(pname, resolved_ptype)
                self.ctx.var_scope_depth[pname] = self.ctx.current_scope.depth
                self.ctx.definitely_assigned.add(pname)
                local_ns.bind_variable(pname, resolved_ptype)
            self.ctx.current_ns = local_ns

            # __next__ must have an explicit non-void return type annotation
            if method.name == "__next__" and isinstance(method.return_type, VoidType):
                raise self._error(
                    "__next__ method must have a return type annotation",
                    method
                )

            # Pre-scan for reassigned variables (shared with codegen)
            scan = scan_reassigned_vars(method.body)
            # Last-use analysis for auto-move (shared with codegen)
            self.ctx.all_last_uses |= analyze_last_uses(method.body, scan.alias_sources)
            self.ctx.current_reassigned_vars = scan.reassigned.copy()
            self.ctx.current_lvalue_reassigned = scan.lvalue_reassigned.copy()

            # Analyze body
            for stmt in method.body:
                self.stmts.analyze_stmt(stmt)

            # Check for field assignments inside control flow in __init__
            if method.name == "__init__":
                self._check_init_field_assignments(method, record)

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

            # Validate __del__ methods
            if method.name == "__del__":
                record_info = self.ctx.registry.get_record(record.name)
                if self.ctx.super_del_call is not None:
                    # super().__del__() must be the last non-docstring statement
                    last_real_stmt = MethodAnalyzer.find_last_non_docstring_stmt(method.body)
                    if last_real_stmt is not None and not is_super_del_call(last_real_stmt):
                        raise self._error(
                            "super().__del__() must be the last statement in __del__",
                            self.ctx.super_del_call
                        )
                elif record_info and record_info.parent:
                    # No super().__del__() but there is a parent class - check if parent has __del__
                    parent_info = self.ctx.registry.get_record(record_info.parent.name) if hasattr(record_info.parent, 'name') else None
                    if parent_info and parent_info.get_method("__del__") is not None:
                        self.ctx.warning(
                            "Parent class has __del__() which will be called automatically by C++ "
                            "after this destructor runs. Unlike Python, you do not need "
                            "super().__del__() -- but it also means the parent destructor "
                            "always runs even without an explicit call.",
                            method
                        )

            self.deduction.resolve_all()

            self.function_scan_results[id(method)] = scan
            if self.ctx.hoisted_vars:
                self.function_hoisted_vars[id(method)] = self.ctx.hoisted_vars.copy()
            if self.ctx.move_through_vars:
                self.function_move_through_vars[id(method)] = self.ctx.move_through_vars.copy()
            if self.ctx.global_declarations:
                self.function_global_decls[id(method)] = self.ctx.global_declarations.copy()
            self.if_branch_decls.update(self.ctx.if_branch_decls)

            self.ctx.current_scope = None
            self.ctx.current_function = None
            self.ctx.current_ns = None

        self.ctx.record_ctx = RecordContext()

    def _check_init_field_assignments(self, method: TpyFunction, record: TpyRecord) -> None:
        """Check for field assignments inside control flow in __init__.

        Fields assigned inside branches bypass the C++ member initializer list
        and get default-constructed then reassigned. For nocopy/del types this
        is UB; for others it is fragile.
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None:
            return
        all_fields = self._collect_all_fields(record_info)
        if not all_fields:
            return

        first_error: SemanticError | None = None

        def walk(stmts: list[TpyStmt], depth: int) -> None:
            nonlocal first_error
            for stmt in stmts:
                if isinstance(stmt, TpyAssign):
                    target = stmt.target
                    if (
                        isinstance(target, TpyFieldAccess)
                        and isinstance(target.obj, TpyName)
                        and target.obj.name == "self"
                        and target.field in all_fields
                        and depth > 0
                    ):
                        field_name = target.field
                        field_info = all_fields[field_name]
                        field_type = field_info.type
                        if self._type_has_del_or_nocopy(field_type):
                            type_name = str(field_type)
                            err = self._error(
                                f"field '{field_name}' of type '{type_name}' assigned inside "
                                f"control flow in __init__; '{type_name}' is not safely "
                                f"default-constructible (move-only or has __del__). "
                                f"Use a helper function or @staticmethod to compute the value",
                                stmt
                            )
                            if first_error is None:
                                first_error = err
                        else:
                            self._warning(
                                f"field '{field_name}' assigned inside control flow in "
                                f"__init__; this bypasses the C++ member initializer list. "
                                f"Consider assigning unconditionally before the branch",
                                stmt
                            )
                elif isinstance(stmt, TpyIf):
                    walk(stmt.then_body, depth + 1)
                    walk(stmt.else_body, depth + 1)
                elif isinstance(stmt, (TpyWhile, TpyForEach)):
                    walk(stmt.body, depth + 1)

        walk(method.body, 0)
        if first_error is not None:
            raise first_error

    def _collect_all_fields(self, record_info: RecordInfo) -> dict[str, FieldInfo]:
        """Collect all fields from a record and its ancestors."""
        result: dict[str, FieldInfo] = {}
        parent = record_info.parent
        if parent is not None:
            parent_rec = self.ctx.registry.get_record_for_type(parent)
            if parent_rec is not None:
                result.update(self._collect_all_fields(parent_rec))
        for f in record_info.fields:
            result[f.name] = f
        return result

    def _type_has_del_or_nocopy(self, field_type: TpyType) -> bool:
        """Check if a type (or any ancestor) has __del__ or is @nocopy."""
        inner = field_type
        if isinstance(inner, OwnType):
            inner = inner.wrapped
        inner = unwrap_readonly(inner)
        rec = self.ctx.registry.get_record_for_type(inner)
        if rec is None:
            return False
        if rec.is_nocopy or rec.has_del:
            return True
        parent = rec.parent
        while parent is not None:
            parent_rec = self.ctx.registry.get_record_for_type(parent)
            if parent_rec is None:
                break
            if parent_rec.has_del or parent_rec.is_nocopy:
                return True
            parent = parent_rec.parent
        return False

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

        # Pre-scan for codegen
        self.top_level_scan_result = scan_reassigned_vars(stmts)
        # Last-use analysis for auto-move (shared with codegen)
        self.ctx.all_last_uses |= analyze_last_uses(
            stmts, self.top_level_scan_result.alias_sources)
        self.ctx.current_reassigned_vars = self.top_level_scan_result.reassigned.copy()
        self.ctx.current_lvalue_reassigned = self.top_level_scan_result.lvalue_reassigned.copy()

        for stmt in stmts:
            self.stmts.analyze_stmt(stmt)
        self.deduction.resolve_all()

        if self.ctx.hoisted_vars:
            self.top_level_hoisted_vars = self.ctx.hoisted_vars.copy()
        if self.ctx.move_through_vars:
            self.top_level_move_through_vars = self.ctx.move_through_vars.copy()
        self.if_branch_decls.update(self.ctx.if_branch_decls)

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

        # Check for type alias
        if module_info.type_aliases and original_name in module_info.type_aliases:
            typ = module_info.type_aliases[original_name]
            self.ctx.registry.register_type_alias(local_name, typ)
            self.ctx.user_imported_type_aliases[local_name] = (module_name, original_name)
            # Implicitly import member record types so codegen can qualify them
            if isinstance(typ, UnionType) and module_info.records:
                for member in typ.members:
                    if isinstance(member, NamedType) and member.name in module_info.records:
                        if member.name not in self.ctx.user_imported_records:
                            rec = module_info.records[member.name]
                            self.ctx.registry.register_record(rec, member.name)
                            self.ctx.user_imported_records[member.name] = (module_name, member.name)
            return

        # Check for enum
        if module_info.enums and original_name in module_info.enums:
            enum_type = module_info.enums[original_name]
            self.ctx.registry.register_enum(enum_type, local_name)
            # Also register under original name: the parser resolves aliases
            # back to original names for type annotations (NamedType("Color")
            # even when the alias is "C")
            if local_name != original_name:
                self.ctx.registry.register_enum(enum_type, original_name)
            self.ctx.global_ns.bind_enum(enum_type, name=local_name)
            self.ctx.user_imported_enums[local_name] = (module_name, original_name)
            return

        # Check for variable
        if module_info.variables and original_name in module_info.variables:
            var_info = module_info.variables[original_name]
            self.ctx.global_scope.define(local_name, var_info.type)
            self.ctx.global_ns.bind_variable(local_name, var_info.type)
            self.ctx.user_imported_variables[local_name] = (module_name, original_name)
            return

        raise self._error(f"'{original_name}' not found in module '{module_name}'")
