"""
TurboPython Semantic Analyzer

Main orchestrator that wires all components together.
"""

from __future__ import annotations
import re
from typing import Optional

from ..typesys import (
    TpyType, TypeRegistry, NamedType, UnionType, FinalType, STR, StrType, StrViewType, VoidType, VOID,
    INT32, ReadonlyType, unwrap_readonly, unwrap_optional_own, OwnType, OptionalType, RecordInfo, FieldInfo,
    FunctionInfo, EnumType, is_any_str_type,
)
from ..namespace import Namespace, NameBinding, BindingKind
from ..parse import TpyModule, TpyRecord, TpyFunction, TpyExpr, TpyStmt, TpyVarDecl, is_super_del_call
from .registration import build_record_self_type
from ..parse.nodes import (
    TpyStrLiteral, TpyAssign, TpyIf, TpyWhile, TpyForEach, TpyFieldAccess, TpyName, TpyCall,
    TpyMethodCall, TpyExprStmt, TpyRaise, TpyTryExcept, TpyMatch, TpyNestedDef,
)
from .expressions import _collect_body_name_refs

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
from .mutation_propagation import propagate_mutation_facts, infer_method_const
from tpyc import modules as builtin_modules
from ..typesys import TypeParamRef, TupleType


def _parse_extends_type_args(
    args_str: str, type_params: dict[str, TpyType],
) -> tuple[TpyType, ...]:
    """Parse extends type arg string into TpyType instances.

    Resolves type parameter names ("T") and concrete type names ("Char")
    via the module system. Handles tuple types ("tuple[K, V]").
    """
    args_str = args_str.strip()
    resolve = builtin_modules._resolve_extends_type_arg
    tuple_match = re.match(r"tuple\[(.+)\]$", args_str)
    if tuple_match:
        inner = tuple_match.group(1)
        parts = [p.strip() for p in inner.split(",")]
        resolved = []
        for p in parts:
            t = resolve(p, type_params)
            resolved.append(t if t is not None else TypeParamRef(p))
        return (TupleType(tuple(resolved)),)
    results = []
    for a in args_str.split(","):
        a = a.strip()
        t = resolve(a, type_params)
        results.append(t if t is not None else TypeParamRef(a))
    return tuple(results)


def _is_stmt_super_init_call(stmt: TpyStmt) -> bool:
    """Check if a statement is a super().__init__() call."""
    if isinstance(stmt, TpyExprStmt):
        expr = stmt.expr
        if isinstance(expr, TpyMethodCall) and expr.method == "__init__":
            return expr.super_parent_type is not None
    return False


def _expr_contains_self_method_call(expr: TpyExpr) -> bool:
    """Check if an expression contains a non-static self.method() call.

    Best-effort recursive walk -- covers common expression shapes.
    """
    if isinstance(expr, TpyMethodCall):
        if (isinstance(expr.obj, TpyName)
                and expr.obj.name == "self"
                and not expr.is_static_call):
            return True
        if _expr_contains_self_method_call(expr.obj):
            return True
        return any(_expr_contains_self_method_call(a) for a in expr.args)
    # Walk sub-expressions generically via common attribute names
    for attr in ("left", "right", "operand", "obj", "expr",
                 "condition", "then_expr", "else_expr", "index"):
        sub = getattr(expr, attr, None)
        if isinstance(sub, TpyExpr):
            if _expr_contains_self_method_call(sub):
                return True
    args = getattr(expr, "args", None)
    if isinstance(args, list):
        return any(_expr_contains_self_method_call(a) for a in args
                   if isinstance(a, TpyExpr))
    return False


def _type_contains_type_param(typ: TpyType) -> bool:
    """Return True if the type is or transitively contains a TypeParamRef."""
    if isinstance(typ, TypeParamRef):
        return True
    if isinstance(typ, TupleType):
        return any(_type_contains_type_param(et) for et in typ.element_types)
    # Cover container types and Optional/Own/Readonly wrappers via their
    # element-accessor methods -- use a best-effort attribute walk.
    for attr in ("pointee", "wrapped", "element", "value_type", "key_type", "inner"):
        sub = getattr(typ, attr, None)
        if isinstance(sub, TpyType) and _type_contains_type_param(sub):
            return True
    elem = getattr(typ, "get_element_type", None)
    if callable(elem):
        et = elem()
        if isinstance(et, TpyType) and _type_contains_type_param(et):
            return True
    return False


def _body_has_raise(stmts: list[TpyStmt], exception_type: str) -> bool:
    """Return True if any path in *stmts* contains ``raise <exception_type>``.

    Called after body analysis, so TpyRaise.exception_type is already
    qualified (e.g. 'builtins.StopIteration').
    """
    for stmt in stmts:
        if isinstance(stmt, TpyRaise) and stmt.exception_type == exception_type:
            return True
        if isinstance(stmt, TpyIf):
            if _body_has_raise(stmt.then_body, exception_type) or _body_has_raise(stmt.else_body, exception_type):
                return True
        elif isinstance(stmt, (TpyWhile, TpyForEach)):
            if _body_has_raise(stmt.body, exception_type) or _body_has_raise(stmt.orelse, exception_type):
                return True
        elif isinstance(stmt, TpyTryExcept):
            if (_body_has_raise(stmt.try_body, exception_type) or _body_has_raise(stmt.except_body, exception_type)
                    or _body_has_raise(stmt.else_body, exception_type)):
                return True
        elif isinstance(stmt, TpyMatch):
            for case in stmt.cases:
                if _body_has_raise(case.body, exception_type):
                    return True
    return False


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
        self.expr.set_cross_deps(self.calls, self.methods, self.stmts.scopes)
        self.calls.set_cross_deps(self.expr, self.methods)
        self.methods.set_cross_deps(self.expr, self.calls)
        self.stmts.set_cross_deps(self.expr)
        self.compat.set_methods(self.methods)

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

        # @overload dispatch groups: implementation func id -> list of stub TpyFunctions
        # Used by codegen to emit per-overload specialized C++ functions.
        self.overload_groups: dict[int, list[TpyFunction]] = {}

        # Convenience aliases for public API
        self.registry = self.ctx.registry
        self.global_scope = self.ctx.global_scope
        self.diagnostics = self.ctx.diagnostics

        # Register all builtins at init time, before user modules are registered
        # User modules registered later (in Compiler._analyze_module) will overwrite
        # builtins with the same name, allowing user code to shadow math/time/sys.
        for protocol_def, module_name in builtin_modules.get_all_protocols():
            info = builtin_modules.protocol_def_to_info(protocol_def, module_name)
            self.ctx.registry.register_protocol(info)
        self.registrar.register_builtin_types()
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
        from ..typesys import TypeParamKind
        for module in builtin_modules.get_all_modules():
            for qname, type_def in module.types.items():
                record_info = self.ctx.registry.get_builtin_record(qname)
                if not record_info or not record_info.extends_protocols:
                    continue
                simple_name = qname.split(".")[-1]

                # Build a TpyType for this builtin (with TypeParamRef args for generics)
                if type_def.type_obj is not None:
                    actual_type = type_def.type_obj
                elif record_info.type_factory and record_info.type_params:
                    args = []
                    for tp, kind in zip(record_info.type_params, record_info.type_param_kinds):
                        if kind == TypeParamKind.INT:
                            args.append(1)  # placeholder int value
                        else:
                            args.append(TypeParamRef(tp))
                    actual_type = record_info.type_factory(*args)
                else:
                    continue

                # Build type_params dict for resolving extends args
                tp_dict: dict[str, TpyType] = {}
                if record_info.type_params:
                    for tp, kind in zip(record_info.type_params, record_info.type_param_kinds):
                        if kind != TypeParamKind.INT:
                            tp_dict[tp] = TypeParamRef(tp)

                for ext_str in record_info.extends_protocols:
                    match = re.match(r"(\w+)(?:\[(.+)\])?", ext_str)
                    if not match:
                        continue
                    proto_name = match.group(1)
                    proto_info = self.ctx.registry.get_protocol(proto_name)
                    if proto_info is None or proto_info.is_marker:
                        continue
                    # Build protocol NamedType with matching type args
                    proto_args = _parse_extends_type_args(match.group(2), tp_dict) if match.group(2) else ()
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
        python_builtin_types = ["int", "str", "bool", "float", "bytes", "bytearray",
                                "list", "dict", "set",
                                "BaseException", "Exception", "StopIteration"]
        for name in python_builtin_types:
            self.ctx.builtins_ns.bind_imported_name(name, "builtins", name)

        # Python builtin functions -- available without import (like CPython).
        # Registered in both builtins_ns (for sema lookup) and imported_names
        # (for codegen to resolve to the lib/tpy/builtins.py module functions).
        python_builtin_functions = ["len", "repr", "hash", "chr", "ord", "abs",
                                    "min", "max", "pow", "round", "divmod",
                                    "next", "print", "range", "enumerate",
                                    "zip", "isinstance", "iter"]
        for name in python_builtin_functions:
            self.ctx.builtins_ns.bind_imported_name(name, "builtins", name)
            self.ctx.imported_names[name] = ("builtins", name)

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
        self.ctx.module_cpp_namespace = getattr(module.directives, 'cpp_namespace', None) if hasattr(module, 'directives') else None

        # Convert parse warnings to diagnostics
        for warning in module.parse_warnings:
            self.ctx.warning_from_loc(warning.message, warning.loc)

        # Process imports
        self.ctx.imports = module.imports
        self.ctx.bare_module_imports = module.bare_module_imports
        if module.tpy_star_import:
            self.registrar.register_tpy_star_import()
            self._register_all_tpy_type_aliases()
        for import_module_name, names in self.ctx.imports.items():
            if isinstance(names, set):
                if module.tpy_star_import and import_module_name == "tpy":
                    continue  # already fully registered above
                # "from X import Y" or "from X import Y as Z"
                # Stored as (original_name, local_name) tuples to support aliases
                for original_name, local_name in names:
                    self.ctx.imported_names[local_name] = (import_module_name, original_name)
                    self.ctx.global_ns.bind_imported_name(local_name, import_module_name, original_name)

                    # For user module imports, also register the items for type checking
                    if import_module_name in module.user_module_imports:
                        self._register_user_module_import(import_module_name, original_name, local_name)
                    # Register tpy type aliases from .py stubs (no-op for non-alias names)
                    elif import_module_name == "tpy":
                        self._register_tpy_type_alias(original_name, local_name)
            elif names is None:
                # "import X" for special modules (tpy, typing, etc.)
                alias = module.module_aliases.get(import_module_name)
                self.ctx.global_ns.bind_module(import_module_name, alias)
                # Register tpy type aliases for qualified annotation resolution
                # (e.g. import tpy; x: tpy.Float64)
                if import_module_name == "tpy":
                    self._register_all_tpy_type_aliases()

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

        # Validate default_factory fields conform to Default protocol
        self._validate_factory_defaults(module)

        # Propagate @nocopy from fields to containing records.
        # Done after inheritance validation so parent types are resolved.
        # Definition order handles transitive propagation naturally.
        self._propagate_nocopy(module)

        # Transfer type aliases from parser to sema registry, validating members
        for name, (typ, loc) in module.type_aliases.items():
            self._validate_type_alias_members(name, typ, loc)
            self.ctx.registry.register_type_alias(name, typ)

        # Second pass: register all functions (with @overload grouping)
        self._register_functions_with_overloads(module.functions)

        # Inject synthetic __name__: Final[str] before registration so it flows
        # through the same path as user-defined Finals (single source of truth).
        # Skip for private submodules (containing "._") that may share their
        # parent's C++ namespace -- they'd cause duplicate __name__ definitions.
        # This applies to both stdlib (tpy._core._types, tpy._builtins._list) and user private
        # submodules (myapp._internal), which is fine since __name__ is rarely
        # needed in submodules compiled to C++.
        module_name = self.ctx.module_name
        is_private_submodule = "._" in module_name
        if not is_private_submodule:
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

        # Sixth pass: analyze function bodies (skip @overload stubs)
        for func in module.functions:
            if not func.is_overload_stub:
                self._analyze_function(func)

        # Phase 2: propagate mutation facts through intra-module call graph,
        # then emit/suppress deferred borrow warnings with resolved facts
        self._propagate_mutation_facts()
        self._sync_inferred_const(module)
        self.calls.resolve_pending_borrow_checks()

    def _propagate_mutation_facts(self) -> None:
        """Collect all module-local FunctionInfos and run call-graph propagation."""
        all_fis: list = []
        for overloads in self.ctx.registry.functions.values():
            for fi in overloads:
                # call_edges is None for imported functions (cleared by their own Phase 2).
                # Only process functions from the current module (call_edges set during Phase 1).
                if fi.direct_mutated_params is not None and fi.call_edges is not None:
                    all_fis.append(fi)
        for rec in self.ctx.registry.records.values():
            for overloads in rec.methods.values():
                for fi in overloads:
                    if fi.direct_mutated_params is not None and fi.call_edges is not None:
                        all_fis.append(fi)
        propagate_mutation_facts(all_fis)
        infer_method_const(all_fis)

    def _sync_inferred_const(self, module: TpyModule) -> None:
        """Copy inferred is_readonly=True from FunctionInfo back to TpyFunction nodes.

        infer_method_const() sets FunctionInfo.is_readonly on the registry objects,
        but codegen reads method.is_readonly from the TpyFunction AST nodes.
        This pass syncs the two representations.
        """
        for record in module.records:
            record_info = self.ctx.registry.get_record(record.name)
            if record_info is None:
                continue
            for method in record.methods:
                if method.is_readonly:
                    continue  # already readonly, no need to sync
                # Phase 1 facts for @overload methods land on overloads[0]'s FI
                # (see comment at get_method() call below). Syncing stub nodes
                # would re-read via get_method() and hit the same FI repeatedly --
                # skip them and let the non-stub TpyFunction node do the sync.
                if method.is_overload_stub:
                    continue
                # @auto_readonly mutable clones are paired with a const clone --
                # keep them mutable so the pair generates both overloads correctly.
                if method.is_auto_readonly_mutable_clone:
                    continue
                # @readonly(False) is an explicit opt-out -- respect it.
                if method.readonly_opt_out:
                    continue
                fi = record_info.get_method(method.name)
                if fi is None or not fi.is_readonly:
                    continue
                # For @dynamic protocol overrides, the const-ness of the concrete
                # method must match the virtual base declaration. If the protocol
                # declares the method as non-const, don't infer const here --
                # it would produce a different C++ signature and break the override.
                if self._dynamic_proto_requires_nonconst(record_info, method.name):
                    continue
                method.is_readonly = True

    def _dynamic_proto_requires_nonconst(self, record_info: RecordInfo, method_name: str) -> bool:
        """Return True if method_name must remain non-const due to a @dynamic protocol override.

        A @dynamic protocol generates C++ pure virtual methods that concrete implementations
        must override with matching (non-const) signatures. The method may be declared in a
        non-dynamic ancestor, but still ends up in the dynamic vtable.
        """
        visited: set[str] = set()
        for proto_type in record_info.implemented_protocols:
            proto_info = self.ctx.registry.get_protocol(proto_type.name)
            # Only @dynamic protocols generate C++ virtual bases
            if proto_info is None or not proto_info.is_dynamic:
                continue
            # Search this dynamic protocol and ALL its ancestors for method_name,
            # regardless of whether ancestors are themselves dynamic.
            if self._proto_hierarchy_has_nonconst(proto_type.name, method_name, visited):
                return True
        return False

    def _proto_hierarchy_has_nonconst(self, proto_name: str, method_name: str, visited: set[str]) -> bool:
        """Search method_name in this protocol and all ancestors (ignoring dynamic flag)."""
        if proto_name in visited:
            return False
        visited.add(proto_name)
        proto_info = self.ctx.registry.get_protocol(proto_name)
        if proto_info is None:
            return False
        for sig in proto_info.methods:
            if sig.name == method_name and not sig.is_readonly:
                return True
        for parent_name in proto_info.parent_protocols:
            if self._proto_hierarchy_has_nonconst(parent_name, method_name, visited):
                return True
        return False

    def _is_type_nocopy(self, typ: TpyType) -> bool:
        """Delegate to canonical is_type_nocopy on context."""
        return self.ctx.is_type_nocopy(typ)

    def _validate_factory_defaults(self, module: TpyModule) -> None:
        """Validate that field(default_factory=X) fields have Default-constructible types."""
        default_proto = NamedType("Default", (), is_protocol=True)
        for record in module.records:
            if not record.is_dataclass:
                continue
            for fld in record.fields:
                if not fld.is_factory_default:
                    continue
                if not self.protocols.type_conforms_to_protocol(fld.type, default_proto):
                    raise SemanticError(
                        f"Field '{fld.name}' in @dataclass '{record.name}' uses "
                        f"default_factory but type '{fld.type}' is not default-constructible",
                        fld.loc or record.loc,
                    )
                # Validate factory name matches field type
                expr = fld.default_expr
                if isinstance(expr, TpyCall) and isinstance(fld.type, NamedType):
                    if expr.func != fld.type.name:
                        raise SemanticError(
                            f"default_factory '{expr.func}' does not match "
                            f"field type '{fld.type}'",
                            fld.loc or record.loc,
                        )

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

    def _warn_unconsumed_own_params(self, func: TpyFunction) -> None:
        """Warn when Own[T] params are never consumed (stored, forwarded, or returned)."""
        if func.is_stub:
            return
        for pname, ptype in func.params:
            own = unwrap_optional_own(unwrap_readonly(ptype))
            if own is None:
                continue
            if pname in self.ctx.current_consumed_own_params:
                continue
            # Optional[Own[T]]: None branch has nothing to consume, making
            # flow-sensitive intersection unreliable
            if isinstance(unwrap_readonly(ptype), OptionalType):
                continue
            # Value types: copy == move, no semantic difference
            if own.wrapped.is_value_type():
                continue
            # Generic T bounded to ValueType: same as value type
            if isinstance(own.wrapped, TypeParamRef):
                bound = self.type_ops.get_type_param_bound(own.wrapped.name)
                if (bound is not None and isinstance(bound, NamedType)
                        and bound.qualified_name() == "tpy.ValueType"):
                    continue
            # @nocopy types: Own is the only way to pass them
            if self.ctx.is_type_nocopy(own.wrapped):
                continue
            self.ctx.warning(
                f"Own[{own.wrapped}] param '{pname}' is never consumed "
                f"(not stored in a field, forwarded to another Own[T], or returned)",
                func,
            )

    def _analyze_function(self, func: TpyFunction) -> None:
        """Analyze a function body."""
        # Stub functions (extern imports with ... body) have no body to analyze
        if func.is_stub:
            return

        self.ctx.reset_function_tracking()

        self.ctx.current_function = func
        if func.is_generator:
            self.ctx._yield_counter = 0
        # Resolve return type (sets is_protocol for cross-module imports)
        func.return_type = self.type_ops.resolve_type(func.return_type)
        scope = Scope(parent=self.ctx.global_scope)
        self.ctx.current_scope = scope

        # Resolve and normalize params (@readonly wraps all non-value params).
        # Write back to AST so codegen sees ReadonlyType.
        local_ns = Namespace(parent=self.ctx.global_ns)
        self.ctx.current_ns = local_ns
        resolved_params: list[tuple[str, TpyType]] = []
        for i, (pname, ptype) in enumerate(func.params):
            resolved_ptype = self._normalize_param_type(
                self.type_ops.resolve_type(ptype), func.is_readonly)
            func.params[i] = (pname, resolved_ptype)
            resolved_params.append((pname, resolved_ptype))

        # Track consuming method for ownership propagation through fields
        prev_consuming = self.ctx.in_consuming_method
        self.ctx.in_consuming_method = func.is_consuming

        # Shared core: bind params, prescan, analyze body
        scan = self.stmts._prescan_and_analyze_body(func, resolved_params, scope, local_ns)
        self.deduction.resolve_all()

        # Collect generator local variables for struct field generation.
        # Include both function-level locals and pending loop vars (for-loop
        # variables and body-declared vars that weren't used after the loop).
        if func.is_generator:
            param_names = {pname for pname, _ in func.params}
            locals_dict: dict[str, 'TpyType'] = {}
            for name, binding in local_ns.all_bindings().items():
                if name not in param_names and binding.type is not None:
                    locals_dict[name] = binding.type
            for name, (vtype, _, _) in self.ctx.pending_loop_vars.items():
                if name not in param_names and vtype is not None:
                    locals_dict[name] = vtype
            func.generator_locals = list(locals_dict.items())

        # Finalize nested def escape analysis
        self._finalize_nested_def_escapes()

        # Store Phase 1 local mutation facts (resolved by Phase 2 propagation)
        func_overloads = self.ctx.registry.get_function(func.name)
        func_info = func_overloads[-1] if func_overloads else None
        if func_info is not None and func_info.direct_mutated_params is None:
            param_list = [pname for pname, _ in func.params]
            direct = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.current_mutated_param_names
            )
            func_info.direct_mutated_params = direct
            func_info.call_edges = list(self.ctx.current_call_edges)
            # Set mutated_params to direct facts as initial estimate;
            # Phase 2 propagation will replace with the complete transitive set.
            func_info.mutated_params = direct
            # Structural mutation facts (append/insert/clear/del/etc.)
            direct_struct = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.current_struct_mutated_param_names
            )
            func_info.direct_structural_mutated_params = direct_struct
            func_info.structural_mutated_params = direct_struct
            # 8b: Return borrow facts -- which params does the return value borrow from?
            func_info.return_borrows_from = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.current_returned_param_names
            )
            # Generator functions: the returned struct stores non-value params
            # as T& references (or &ref lambda captures), and str params as
            # string_view, so the result borrows from those params
            if func.is_generator:
                gen_borrows = frozenset(
                    i for i, (_, ptype) in enumerate(func.params)
                    if not ptype.is_value_type() or isinstance(ptype, (StrType, StrViewType))
                )
                if gen_borrows:
                    func_info.return_borrows_from = func_info.return_borrows_from | gen_borrows

        self._warn_unconsumed_own_params(func)
        self._store_analysis_results(func, scan)

        self.ctx.in_consuming_method = prev_consuming
        self.ctx.current_function = None
        self.ctx.current_scope = None
        self.ctx.current_ns = None

    def _store_analysis_results(self, func: TpyFunction, scan: ScanResult) -> None:
        """Store prescan/liveness results for codegen consumption."""
        self.function_scan_results[id(func)] = scan
        if self.ctx.hoisted_vars:
            self.function_hoisted_vars[id(func)] = self.ctx.hoisted_vars.copy()
        if self.ctx.move_through_vars:
            self.function_move_through_vars[id(func)] = self.ctx.move_through_vars.copy()
        if self.ctx.global_declarations:
            self.function_global_decls[id(func)] = self.ctx.global_declarations.copy()
        self.if_branch_decls.update(self.ctx.if_branch_decls)

    def _finalize_nested_def_escapes(self) -> None:
        """Finalize escape analysis for nested defs after the enclosing function is analyzed."""
        # Outer function's parameter names and types
        outer_params: dict[str, TpyType] = {}
        if self.ctx.current_function:
            for pname, ptype in self.ctx.current_function.params:
                outer_params[pname] = ptype
        outer_param_names = set(outer_params.keys())
        # Get the enclosing function body for "used after" analysis
        body = self.ctx.current_function.body if self.ctx.current_function else []
        for name in self.ctx.nested_def_escapes:
            node = self.ctx.nested_def_nodes.get(name)
            if node is None:
                continue
            node.escapes = True
            # Reject nonlocal in escaping closures
            if node.nonlocal_names:
                nl_list = ", ".join(f"'{n}'" for n in sorted(node.nonlocal_names))
                self.ctx.emit_error(
                    f"nonlocal {nl_list} in escaping closure '{name}' is not supported"
                    f" (the closure is returned or stored; use a class instead)",
                    node)
            # Collect names referenced after the nested def for move analysis
            names_used_after = self._names_used_after(body, node)
            # Per-capture analysis: determine ref vs value vs move capture mode
            for cap_name in node.captured_names:
                cap_type = self.ctx.current_scope.lookup(cap_name) if self.ctx.current_scope else None
                if cap_type is None:
                    continue
                raw_type = unwrap_readonly(cap_type)
                is_own_param = isinstance(raw_type, OwnType)
                if isinstance(raw_type, OwnType):
                    raw_type = raw_type.wrapped
                check_type = raw_type.inner if isinstance(raw_type, OptionalType) else raw_type
                if cap_name in outer_param_names:
                    # Reject str/StrView parameter captures (string_view dangles)
                    if isinstance(check_type, (StrType, StrViewType)):
                        self.ctx.emit_error(
                            f"Escaping closure '{name}' captures str parameter"
                            f" '{cap_name}' which would dangle (string_view into"
                            f" caller's storage). Use String for owned capture",
                            node)
                    elif not raw_type.is_value_type():
                        if is_own_param:
                            # Own[T] param is destroyed on return -- must move
                            node.move_captures.add(cap_name)
                            self.ctx.mark_own_param_consumed(cap_name)
                        else:
                            # Regular const-ref param: caller's object outlives closure
                            node.ref_captures.add(cap_name)
                else:
                    # Local variable: must copy or move (local dies on return).
                    # Only emit std::move for types where move is cheaper than
                    # copy (string, BigInt, etc.) -- for trivial types like
                    # Int32 it's just noise.
                    wants_move = (not raw_type.is_value_type()
                                  or raw_type.is_expensive_copy())
                    if wants_move and cap_name not in names_used_after:
                        # Last use -- move into the closure, no warning
                        node.move_captures.add(cap_name)
                    elif wants_move:
                        # Used after the closure -- must copy, warn
                        self.ctx.warning(
                            f"Escaping closure '{name}' copies local"
                            f" '{cap_name}' (used after closure definition,"
                            f" preventing move). Reorder code so the closure"
                            f" is the last use, or use copy() to make the"
                            f" copy explicit",
                            node)

    @staticmethod
    def _names_used_after(body: list[TpyStmt], nested_node: TpyNestedDef) -> set[str]:
        """Collect names referenced in top-level statements after the nested def."""
        found = False
        after_stmts: list[TpyStmt] = []
        target_name = nested_node.func.name
        for stmt in body:
            if found:
                after_stmts.append(stmt)
            elif isinstance(stmt, TpyNestedDef) and stmt.func.name == target_name:
                found = True
        if not found:
            # Nested def not at top level of body -- conservatively assume all used
            return set(nested_node.captured_names or [])
        return _collect_body_name_refs(after_stmts)

    def _collect_method_overload_groups(self, record: TpyRecord) -> None:
        """Identify and validate @overload groups among a record's methods.

        For each group, stores the mapping from implementation -> stubs
        in self.overload_groups. Also validates exhaustiveness.
        """
        pending_stubs: dict[str, list[TpyFunction]] = {}
        for method in record.methods:
            if method.is_overload_stub:
                pending_stubs.setdefault(method.name, []).append(method)
                continue
            stubs = pending_stubs.pop(method.name, None)
            if stubs:
                self._validate_method_overload_group(method, stubs, record.name)
                self.overload_groups[id(method)] = stubs
        for name, stubs in pending_stubs.items():
            # @native/@cpp_template overload stubs are complete declarations
            if all(s.linkage.name in ("NATIVE", "NATIVE_C") or s.cpp_template for s in stubs):
                continue
            raise SemanticError(
                f"@overload stubs for '{record.name}.{name}' have no implementation method",
                stubs[0].loc or record.loc,
            )

    def _validate_method_overload_group(
        self, impl: TpyFunction, stubs: list[TpyFunction], record_name: str,
    ) -> None:
        """Validate exhaustiveness of method overload stubs."""
        for stub in stubs:
            if len(stub.params) != len(impl.params):
                raise SemanticError(
                    f"@overload stub for '{record_name}.{impl.name}' has "
                    f"{len(stub.params)} parameter(s), but the implementation "
                    f"has {len(impl.params)}",
                    stub.loc,
                )
            for (impl_pname, _), (stub_pname, _) in zip(impl.params, stub.params):
                if impl_pname != stub_pname:
                    raise SemanticError(
                        f"@overload stub parameter '{stub_pname}' does not match "
                        f"implementation parameter '{impl_pname}'",
                        stub.loc,
                    )
        for param_idx, (pname, ptype) in enumerate(impl.params):
            resolved = self.type_ops.resolve_type(ptype)
            if not isinstance(resolved, UnionType):
                for stub in stubs:
                    stub_ptype = self.type_ops.resolve_type(stub.params[param_idx][1])
                    if stub_ptype != resolved:
                        raise SemanticError(
                            f"@overload stub type '{stub_ptype}' for parameter '{pname}' "
                            f"does not match implementation type '{resolved}'",
                            stub.loc,
                        )
                continue
            covered: set[TpyType] = set()
            for stub in stubs:
                stub_ptype = self.type_ops.resolve_type(stub.params[param_idx][1])
                if isinstance(stub_ptype, UnionType):
                    stub_members = set(stub_ptype.members)
                else:
                    stub_members = {stub_ptype}
                extra = [m for m in stub_members if m not in resolved.members]
                if extra:
                    extra_names = ", ".join(str(m) for m in extra)
                    raise SemanticError(
                        f"@overload stub type for parameter '{pname}' includes "
                        f"{extra_names} which is not in the implementation's "
                        f"union type '{resolved}'",
                        stub.loc,
                    )
                covered.update(stub_members)
            missing = [m for m in resolved.members if m not in covered]
            if missing:
                missing_names = ", ".join(str(m) for m in missing)
                raise SemanticError(
                    f"@overload stubs for '{record_name}.{impl.name}' don't cover all "
                    f"variants of parameter '{pname}': missing {missing_names}",
                    impl.loc,
                )

    def _register_functions_with_overloads(self, functions: list[TpyFunction]) -> None:
        """Register functions, grouping @overload stubs with their implementations.

        @overload stubs precede their implementation function (same name).
        Stubs are registered as callable overloads; the implementation is
        registered for body analysis only (not directly callable).
        """
        pending_stubs: dict[str, list[TpyFunction]] = {}

        for func in functions:
            if func.builtin_decorator_key:
                # Register minimal FunctionInfo so the key flows through exports
                self.ctx.registry.register_function(FunctionInfo(
                    name=func.name, params=[], return_type=VOID,
                    builtin_decorator_key=func.builtin_decorator_key,
                ))
                continue
            if func.is_overload_stub:
                pending_stubs.setdefault(func.name, []).append(func)
                continue

            # Non-stub function: check if there are pending stubs for this name
            stubs = pending_stubs.pop(func.name, None)
            if stubs:
                self._register_overload_group(func, stubs)
            else:
                self.registrar.register_function(func)

        # Stubs left without an implementation: @native/@cpp_template overload
        # groups (each stub is a complete declaration) or an error.
        for name, stubs in pending_stubs.items():
            if all(s.linkage.name in ("NATIVE", "NATIVE_C") or s.cpp_template for s in stubs):
                self.registrar.register_overload_group(stubs)
            else:
                raise SemanticError(
                    f"@overload stubs for '{name}' have no implementation function",
                    stubs[0].loc,
                )

    def _register_overload_group(
        self, impl: TpyFunction, stubs: list[TpyFunction],
    ) -> None:
        """Validate and register an @overload group.

        - Validates stub parameter types cover all union variants in the implementation
        - Registers each stub as a callable FunctionInfo overload
        - Stores the group mapping for codegen
        """
        # Validate that each stub has the same number of params as the implementation
        for stub in stubs:
            if len(stub.params) != len(impl.params):
                raise SemanticError(
                    f"@overload stub for '{impl.name}' has {len(stub.params)} parameter(s), "
                    f"but the implementation has {len(impl.params)}",
                    stub.loc,
                )
            for (impl_pname, _), (stub_pname, _) in zip(impl.params, stub.params):
                if impl_pname != stub_pname:
                    raise SemanticError(
                        f"@overload stub parameter '{stub_pname}' does not match "
                        f"implementation parameter '{impl_pname}'",
                        stub.loc,
                    )

        # Check exhaustiveness and subset validity:
        # For each union-typed impl param, stubs must cover all members
        # and stub types must be subsets of the union.
        for param_idx, (pname, ptype) in enumerate(impl.params):
            resolved = self.type_ops.resolve_type(ptype)
            if not isinstance(resolved, UnionType):
                # Non-union impl param: stub must match exactly
                for stub in stubs:
                    stub_ptype = self.type_ops.resolve_type(stub.params[param_idx][1])
                    if stub_ptype != resolved:
                        raise SemanticError(
                            f"@overload stub type '{stub_ptype}' for parameter '{pname}' "
                            f"does not match implementation type '{resolved}'",
                            stub.loc,
                        )
                continue
            # Collect concrete types from stubs at this position
            covered: set[TpyType] = set()
            for stub in stubs:
                stub_ptype = self.type_ops.resolve_type(stub.params[param_idx][1])
                if isinstance(stub_ptype, UnionType):
                    stub_members = set(stub_ptype.members)
                else:
                    stub_members = {stub_ptype}
                # Validate stub types are subsets of impl union
                extra = [m for m in stub_members if m not in resolved.members]
                if extra:
                    extra_names = ", ".join(str(m) for m in extra)
                    raise SemanticError(
                        f"@overload stub type for parameter '{pname}' includes "
                        f"{extra_names} which is not in the implementation's "
                        f"union type '{resolved}'",
                        stub.loc,
                    )
                covered.update(stub_members)
            # Check all union members are covered
            missing = [m for m in resolved.members if m not in covered]
            if missing:
                missing_names = ", ".join(str(m) for m in missing)
                raise SemanticError(
                    f"@overload stubs for '{impl.name}' don't cover all variants "
                    f"of parameter '{pname}': missing {missing_names}",
                    impl.loc,
                )

        # Register each stub as a callable overload via a single binding
        self.registrar.register_overload_group(stubs)

        # The implementation is NOT registered in the namespace/registry --
        # callers resolve against stubs only. The body is still analyzed
        # via _analyze_function (which works on the TpyFunction directly).

        # Store the group mapping for codegen
        self.overload_groups[id(impl)] = stubs

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

        # Collect method overload groups for this record
        self._collect_method_overload_groups(record)

        for method in record.methods:
            # Skip @overload stubs -- their implementation is analyzed instead
            if method.is_overload_stub:
                continue

            self.ctx.reset_function_tracking()
            self.ctx.current_function = method
            if method.is_generator:
                self.ctx._yield_counter = 0
            # Resolve return type (sets is_protocol for cross-module imports)
            method.return_type = self.type_ops.resolve_type(method.return_type)
            scope = Scope(parent=self.ctx.global_scope)
            self.ctx.current_scope = scope

            local_ns = Namespace(parent=self.ctx.global_ns)
            self.ctx.current_ns = local_ns
            if not method.is_staticmethod:
                self_named = build_record_self_type(record)
                self_type = self._normalize_param_type(self_named, method.is_readonly)
                scope.define("self", self_type)
                self.ctx.var_scope_depth["self"] = scope.depth
                self.ctx.definitely_assigned.add("self")
                local_ns.bind_variable("self", self_type)

            # Resolve and normalize params (@readonly wraps all non-value params).
            # Write back to AST so codegen sees ReadonlyType.
            # For auto_readonly_params_resolved methods, the parser clone already applied
            # ReadonlyType to the params that need it -- skip blanket wrapping.
            readonly_ctx = method.is_readonly and not method.auto_readonly_params_resolved
            resolved_params: list[tuple[str, TpyType]] = []
            for i, (pname, ptype) in enumerate(method.params):
                resolved_ptype = self._normalize_param_type(
                    self.type_ops.resolve_type(ptype), readonly_ctx)
                method.params[i] = (pname, resolved_ptype)
                resolved_params.append((pname, resolved_ptype))

            # __next__ must have an explicit non-void return type annotation
            if method.name == "__next__" and isinstance(method.return_type, VoidType):
                raise self._error(
                    "__next__ method must have a return type annotation",
                    method
                )

            # Track consuming method for ownership propagation through fields
            prev_consuming = self.ctx.in_consuming_method
            self.ctx.in_consuming_method = method.is_consuming

            # Shared core: bind params, prescan, analyze body
            scan = self.stmts._prescan_and_analyze_body(method, resolved_params, scope, local_ns)

            # Collect generator local variables for struct field generation
            if method.is_generator:
                param_names = {pname for pname, _ in method.params}
                locals_dict: dict[str, 'TpyType'] = {}
                for name, binding in local_ns.all_bindings().items():
                    if name not in param_names and name != "self" and binding.type is not None:
                        locals_dict[name] = binding.type
                for name, (vtype, _, _) in self.ctx.pending_loop_vars.items():
                    if name not in param_names and name != "self" and vtype is not None:
                        locals_dict[name] = vtype
                method.generator_locals = list(locals_dict.items())

            # Finalize nested def escape analysis (same as _analyze_function)
            self._finalize_nested_def_escapes()

            self.ctx.in_consuming_method = prev_consuming

            # Warn if __next__ has no raise StopIteration (likely infinite loop)
            # Skip for native methods (cpp_template set) -- StopIteration handled in C++
            if (method.name == "__next__" and not method.cpp_template
                    and not _body_has_raise(method.body, "builtins.StopIteration")):
                self.ctx.warning(
                    "__next__() has no 'raise StopIteration' -- "
                    "iterator will loop forever if caller exhausts it",
                    method
                )

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

            # Store Phase 1 local mutation facts on method FunctionInfo.
            # For @overload methods, get_method() returns overloads[0] (the first
            # stub). The implementation's FI is not separately registered, so all
            # Phase 1 facts are stored on stub[0] and Phase 2 / const inference
            # work through it. This is consistent with _sync_inferred_const, which
            # also reads back via get_method() and skips is_overload_stub nodes.
            record_info = self.ctx.registry.get_record(record.name)
            if record_info is not None and not method.is_stub:
                method_fi = record_info.get_method(method.name)
                if method_fi is not None and method_fi.direct_mutated_params is None:
                    param_list = [pname for pname, _ in method.params]
                    direct = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.current_mutated_param_names
                    )
                    method_fi.direct_mutated_params = direct
                    method_fi.direct_self_mutated = self.ctx.current_self_mutated
                    method_fi.call_edges = list(self.ctx.current_call_edges)
                    method_fi.mutated_params = direct
                    # Structural mutation facts (append/insert/clear/del/etc.)
                    direct_struct = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.current_struct_mutated_param_names
                    )
                    method_fi.direct_structural_mutated_params = direct_struct
                    method_fi.structural_mutated_params = direct_struct
                    # 8b: Return borrow facts
                    returned = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.current_returned_param_names
                    )
                    if "self" in self.ctx.current_returned_param_names:
                        returned = returned | frozenset([-1])
                    method_fi.return_borrows_from = returned

            self._warn_unconsumed_own_params(method)
            self._store_analysis_results(method, scan)

            self.ctx.current_scope = None
            self.ctx.current_function = None
            self.ctx.current_ns = None

        self.ctx.record_ctx = RecordContext()

    def _check_init_field_assignments(self, method: TpyFunction, record: TpyRecord) -> None:
        """Enforce the two-section __init__ model.

        The init section is the leading prefix of super().__init__() +
        self.field = expr (own fields only, each at most once). Everything
        after the first statement that breaks this pattern is the body section.

        At the split point:
        - Own fields not initialized + no default ctor -> error.
        - Own fields not initialized + has default ctor -> warning (CPython gap).
        - self.method() call with uninitialized fields in RHS -> warning.

        In the body section (depth > 0 branches):
        - @nocopy field assigned -> error (copy assignment deleted).
        - __del__ field assigned -> error (destructor on default-constructed value).
        - Other fields: silently allowed (e.g. accumulation in a loop body).
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None:
            return
        # Own fields for split-point and missing-field checks
        own_fields: dict[str, FieldInfo] = {f.name: f for f in record_info.fields}
        # All fields (incl. inherited) for branch-body safety checks
        all_fields = self._collect_all_fields(record_info)
        if not own_fields and not all_fields:
            return

        # --- Find split point ---
        init_section_fields: set[str] = set()
        split_idx = len(method.body)

        for i, stmt in enumerate(method.body):
            if _is_stmt_super_init_call(stmt):
                continue
            # Skip docstrings (TpyExprStmt wrapping a string literal)
            if isinstance(stmt, TpyExprStmt) and isinstance(stmt.expr, TpyStrLiteral):
                continue
            if (isinstance(stmt, TpyAssign)
                    and isinstance(stmt.target, TpyFieldAccess)
                    and isinstance(stmt.target.obj, TpyName)
                    and stmt.target.obj.name == "self"
                    and stmt.target.field in all_fields
                    and stmt.target.field not in init_section_fields):
                field_name = stmt.target.field
                # Warn if RHS calls self.method() while fields are still uninitialized
                uninit = set(own_fields.keys()) - init_section_fields
                if uninit and _expr_contains_self_method_call(stmt.value):
                    self._warning(
                        f"instance method called in __init__ before all fields are initialized; "
                        f"the method may access uninitialized fields",
                        stmt
                    )
                init_section_fields.add(field_name)
                continue
            # Not a valid init-section statement: this is the split point
            split_idx = i
            break

        # Collect all fields assigned unconditionally (depth 0) in the full body.
        # Fields in this set are OK even if not in init section: they will be
        # assigned in the constructor body before any branch or method call uses them.
        depth0_assigned: set[str] = {
            stmt.target.field
            for stmt in method.body
            if (isinstance(stmt, TpyAssign)
                and isinstance(stmt.target, TpyFieldAccess)
                and isinstance(stmt.target.obj, TpyName)
                and stmt.target.obj.name == "self")
        }

        # --- Check fields at split point ---
        first_error: SemanticError | None = None

        if own_fields:
            split_node = method.body[split_idx] if split_idx < len(method.body) else None
            for field_name, field_info in own_fields.items():
                if field_name in init_section_fields:
                    continue
                if field_name in depth0_assigned:
                    # Field is assigned unconditionally in the body (just not in the
                    # init section), so it will be initialized before first use.
                    continue
                if field_info.default_value is not None or field_info.default_expr is not None:
                    continue
                field_type = field_info.type
                # In a generic record, fields whose type involves a type parameter
                # cannot be checked here -- C++ handles the constraint at instantiation.
                if record.type_params and _type_contains_type_param(field_type):
                    continue
                if self.protocols._is_default_constructible(field_type):
                    self._warning(
                        f"field '{field_name}' is not initialized before the constructor body; "
                        f"it will be default-constructed in C++ "
                        f"(in CPython, the attribute would not exist)",
                        split_node
                    )
                else:
                    err = self._error(
                        f"field '{field_name}' of type '{str(field_type)}' has no default "
                        f"constructor and is not initialized before the constructor body",
                        split_node
                    )
                    if first_error is None:
                        first_error = err

        # --- Check branch assignments in body (all fields incl. inherited) ---
        def walk_body(stmts: list[TpyStmt], depth: int) -> None:
            nonlocal first_error
            for stmt in stmts:
                if isinstance(stmt, TpyAssign):
                    target = stmt.target
                    if (depth > 0
                            and isinstance(target, TpyFieldAccess)
                            and isinstance(target.obj, TpyName)
                            and target.obj.name == "self"
                            and target.field in all_fields):
                        field_name = target.field
                        field_type = all_fields[field_name].type
                        if self._type_has_del_or_nocopy(field_type):
                            type_name = str(field_type)
                            err = self._error(
                                f"field '{field_name}' of type '{type_name}' assigned inside "
                                f"control flow in __init__; '{type_name}' is move-only or has "
                                f"'__del__', so it must be initialized unconditionally before "
                                f"any branch",
                                stmt
                            )
                            if first_error is None:
                                first_error = err
                elif isinstance(stmt, TpyIf):
                    walk_body(stmt.then_body, depth + 1)
                    walk_body(stmt.else_body, depth + 1)
                elif isinstance(stmt, (TpyWhile, TpyForEach)):
                    walk_body(stmt.body, depth + 1)
                    walk_body(stmt.orelse, depth + 1)

        walk_body(method.body, 0)

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
        self.ctx.current_aug_assigned_vars = self.top_level_scan_result.aug_assigned.copy()

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

    def _register_tpy_type_alias(self, original_name: str, local_name: str) -> None:
        """Register a single tpy type alias from the compiled tpy module_info."""
        tpy_info = self.ctx.registry.get_module("tpy")
        if not tpy_info or not tpy_info.type_aliases:
            return
        alias_type = tpy_info.type_aliases.get(original_name)
        if alias_type is not None:
            self.ctx.registry.register_type_alias(local_name, alias_type)
            self.ctx.user_imported_type_aliases[local_name] = ("tpy", original_name)
            # Also register under original name so _resolve_imported_aliases
            # can substitute NamedType(original_name) nodes in the AST
            if local_name != original_name:
                self.ctx.registry.register_type_alias(original_name, alias_type)

    def _register_all_tpy_type_aliases(self) -> None:
        """Register all tpy type aliases (for bare 'import tpy' and star imports)."""
        tpy_info = self.ctx.registry.get_module("tpy")
        if not tpy_info or not tpy_info.type_aliases:
            return
        for name, alias_type in tpy_info.type_aliases.items():
            self.ctx.registry.register_type_alias(name, alias_type)
            self.ctx.user_imported_type_aliases[name] = ("tpy", name)

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

        # Check for function (list of overloads in ModuleInfo)
        if module_info.functions and original_name in module_info.functions:
            func_infos = module_info.functions[original_name]
            # Skip special_handling builtins merged from the builtin module
            # (e.g. native_c_global in tpy.extern). Statement interception in
            # statements.py looks up the IMPORTED_NAME binding's import_source;
            # registering a function group here would clobber that binding.
            if func_infos and func_infos[0].special_handling:
                return
            self.ctx.registry.register_function_group(local_name, func_infos)
            if len(func_infos) > 1:
                self.ctx.global_ns.bind(NameBinding(
                    kind=BindingKind.FUNCTION,
                    name=local_name,
                    func_infos=func_infos,
                ))
            self.ctx.user_imported_functions[local_name] = (module_name, original_name)
            return

        # Check for record
        if module_info.records and original_name in module_info.records:
            record_info = module_info.records[original_name]
            # Register with local name for lookup (and original name for type resolution)
            self.ctx.registry.register_record(record_info, local_name)
            if local_name != original_name:
                self.ctx.registry.register_record(record_info, original_name)
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

        # @builtin_type/@builtin_decorator stubs and parser keywords are handled
        # at parse time, not exported by .py files -- silently skip them here.
        from ..parse import is_parser_keyword
        if is_parser_keyword(module_name, original_name):
            return
        if (self.ctx.registry.get_builtin_type_key(original_name) or
                self.ctx.registry.get_builtin_decorator_key(original_name)):
            return

        # If this module shadows a builtin, fall back to builtin handling
        # for names the .py file doesn't define (incremental migration support).
        # Only allow names that actually exist in the builtin.
        if module_info.has_builtin_fallback:
            from ..modules import get_module as get_builtin_module
            builtin = get_builtin_module(module_name)
            if builtin and (original_name in builtin.functions or original_name in builtin.protocols):
                return
        raise self._error(f"'{original_name}' not found in module '{module_name}'")
