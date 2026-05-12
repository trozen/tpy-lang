"""
TurboPython Semantic Analyzer

Main orchestrator that wires all components together.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import Optional

from ..typesys import (
    TpyType, TypeRegistry, NominalType, UnionType, FinalType, STR, LiteralType, VoidType, VOID,
    NoneType, INT32, ReadonlyType, unwrap_readonly, unwrap_optional_own, OwnType, OptionalType, RecordInfo, FieldInfo,
    FunctionInfo, ParamInfo, is_any_str_type, BIGINT, FLOAT,
    make_ref, unwrap_ref_type, RefType, TypeParamKind, TypeParamRef, TupleType,
    is_integer_type, is_void_like_type,
    _contains_self_reference,
)
from ..namespace import Namespace, NameBinding, BindingKind
from ..parse import TpyModule, TpyRecord, TpyFunction, TpyExpr, TpyStmt, TpyVarDecl, is_docstring, is_super_del_call, is_base_init_call, ParseError
from ..parse.nodes import RecordLinkage
from .registration import build_record_self_type, _vararg_span_type
from ..parse.nodes import (
    TpyStrLiteral, TpyAssign, TpyIf, TpyWhile, TpyForEach, TpyFieldAccess, TpyName, TpyCall,
    TpyMethodCall, TpyExprStmt, TpyRaise, TpyTry, TpyMatch, TpyNestedDef,
)
from .expressions import _collect_body_name_refs

from ..diagnostics import Scope, Diagnostic, SemanticError
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
from ..cycle_detection import detect_type_cycles
from ..parse import SourceLocation, is_parser_keyword
from ..type_def_registry import is_str_type, is_str_view_type, protocol_info_of
from ..parse.resolve_refs import (
    _walk_body, _merged_method_scope, _record_scope,
    promote_bare_nominals,
)
from .macros import _promote_method_signature
from .builder_trace import BuilderTraceExpander
from ..symbol_binding import (
    SymbolKind, install_binding, protocol_kind_for,
    is_macro_kind, is_kind, walk_attribute_chain,
)




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
        elif isinstance(stmt, TpyTry):
            if (_body_has_raise(stmt.try_body, exception_type)
                    or _body_has_raise(stmt.else_body, exception_type)
                    or _body_has_raise(stmt.finally_body, exception_type)):
                return True
            for handler in stmt.handlers:
                if _body_has_raise(handler.body, exception_type):
                    return True
        elif isinstance(stmt, TpyMatch):
            for case in stmt.cases:
                if _body_has_raise(case.body, exception_type):
                    return True
    return False


class SemanticAnalyzer:
    """Semantic analyzer for TurboPython."""

    def __init__(self, default_int_type: TpyType = INT32,
                 shared_modules: 'dict[str, object] | None' = None):
        # Create shared context. `shared_modules`, when provided, gives
        # this analyzer's TypeRegistry the workspace-wide ModuleInfo dict
        # owned by the Compiler -- every analyzer in one compilation
        # reads/writes the same dict, so cross-module qname lookups
        # (`get_record_for_type`, recursive-union detection, builder-trace
        # type rendering) see every peer module's contribution. Per-module
        # short-name bindings (records / functions / protocols /
        # type_aliases / enums) stay strictly per-analyzer; only the
        # `modules` surface is shared. None preserves the legacy
        # per-analyzer behavior (used by REPL, tests, ad-hoc callers).
        self.ctx = SemanticContext(
            registry=TypeRegistry(shared_modules=shared_modules),
            global_scope=Scope(),
            default_int_type=default_int_type,
            builtins_ns=Namespace(),
            macro_ns=None,  # Set below
            global_ns=None,  # Set below
        )
        # Chain: global_ns -> macro_ns -> builtins_ns
        self.ctx.macro_ns = Namespace(parent=self.ctx.builtins_ns)
        self.ctx.global_ns = Namespace(parent=self.ctx.macro_ns)

        # Layer 1: No dependencies on other analyzers
        self.type_ops = TypeOperations(self.ctx)
        self.compat = TypeCompatibility(self.ctx)
        self.iterable = IterableHelper(self.ctx)
        self.deduction = LocalTypeDeduction(self.ctx, self.compat)

        # Layer 2: Depends on type_ops
        self.protocols = ProtocolChecker(self.ctx, self.type_ops)
        self.registrar = TypeRegistrar(self.ctx, self.type_ops, self.protocols)
        self.operators = OperatorResolver(self.ctx, self.type_ops, self.protocols)

        # Wire up compatibility's deferred dependencies
        self.compat.type_ops = self.type_ops
        self.compat.protocols = self.protocols
        self.compat.deduction = self.deduction

        # Narrowing tracker (depends on type_ops, protocols)
        self.narrowing = NarrowingTracker(self.ctx, self.type_ops, self.protocols)

        # Layer 3: Analyzers ordered by dependencies
        # Cycle: expr <-> calls <-> methods. Break by creating calls/methods
        # first, then expr (which takes them), then setting back-references.
        self.calls = CallAnalyzer(
            self.ctx, self.type_ops, self.protocols, self.compat, self.deduction
        )
        self.methods = MethodAnalyzer(
            self.ctx, self.type_ops, self.protocols, self.compat, self.deduction
        )
        self.expr = ExpressionAnalyzer(
            self.ctx, self.type_ops, self.operators, self.protocols, self.compat,
            self.narrowing, self.calls, self.methods,
        )
        # Complete the back-references
        self.calls.expr = self.expr
        self.calls.methods = self.methods
        self.methods.expr = self.expr
        self.methods.calls = self.calls
        self.compat.methods = self.methods

        # stmts and match: one-way deps, passed through constructors
        self.stmts = StatementAnalyzer(
            self.ctx, self.type_ops, self.compat, self.deduction, self.iterable,
            self.protocols, self.narrowing, self.expr,
        )
        self.expr.set_scopes(self.stmts.scopes)

        # Per-function/method pre-scan results (shared with codegen)
        self.function_scan_results: dict[int, ScanResult] = {}
        self.top_level_scan_result: ScanResult | None = None

        # Per-function/method hoisted vars (scope escape phase 2)
        self.function_hoisted_vars: dict[int, set[str]] = {}
        self.top_level_hoisted_vars: set[str] = set()

        # Per-function/method move-through vars (lvalue alias promoted to rvalue)
        self.function_move_through_vars: dict[int, set[str]] = {}
        self.top_level_move_through_vars: set[str] = set()

        # Per-function movable locals (owned, not hoisted/loop/lvalue-reassigned)
        self.function_movable_locals: dict[int, set[str]] = {}

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

        # Populate builtins_ns with Python builtins (always available without import)
        # These are like CPython's builtins module - int, str, list, len, print, etc.
        self._register_python_builtins()

    # Public API compatibility properties
    @property
    def current_scope(self) -> Optional[Scope]:
        return self.ctx.func.current_scope

    @property
    def current_function(self) -> Optional[TpyFunction]:
        return self.ctx.func.current_function

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
        python_builtin_types = ["int", "str", "bool", "float", "bytes", "bytearray",
                                "list", "dict", "set",
                                "slice", "super",
                                "BaseException", "Exception", "StopIteration",
                                "TextIO"]
        for name in python_builtin_types:
            self.ctx.builtins_ns.bind_imported_name(name, "builtins", name)

        # Python builtin functions -- available without import (like CPython).
        # Registered in both builtins_ns (for sema lookup) and imported_names
        # (for codegen to resolve to the lib/tpy/builtins.py module functions).
        python_builtin_functions = ["len", "repr", "hash", "chr", "ord", "abs",
                                    "min", "max", "pow", "round", "divmod",
                                    "next", "print", "range", "enumerate",
                                    "zip", "isinstance", "iter",
                                    "all", "any", "sum", "sorted",
                                    "bin", "hex", "oct", "reversed",
                                    "map", "filter",
                                    "open", "getattr", "setattr", "delattr", "hasattr"]
        for name in python_builtin_functions:
            self.ctx.builtins_ns.bind_imported_name(name, "builtins", name)
            self.ctx.imported_names[name] = ("builtins", name)

    # Sub-phase indices used by the phase-counter assertion. The five
    # public methods below must be called in this exact order; calling
    # them out of order raises AssertionError.  Compiler still drives
    # all five through `analyze()` today; future phases (Phase 3+) will
    # call individual sub-phases workspace-wide.
    _PHASE_NONE = 0
    _PHASE_BIND_IMPORTS = 1
    _PHASE_REGISTER_RECORDS_AND_PROTOCOLS = 2
    _PHASE_REGISTER_SIGNATURES = 3
    _PHASE_ANALYZE_BODIES = 4
    _PHASE_PHASE2_FIXPOINT = 5

    def _advance_phase(self, expected_prev: int, completed: int) -> None:
        """Phase-counter guard. `expected_prev` is the phase that must
        have just completed; `completed` is the phase the caller is
        finishing now. Raises AssertionError on out-of-order calls.
        """
        current = getattr(self, "_completed_phase", self._PHASE_NONE)
        assert current == expected_prev, (
            f"sub-phase ordering violated: expected previous phase "
            f"{expected_prev}, got {current} (trying to complete "
            f"{completed})"
        )
        self._completed_phase = completed

    def analyze(self, module: TpyModule, module_name: str = "__main__") -> None:
        """Analyze a module for semantic correctness.

        Args:
            module: The parsed module AST.
            module_name: Name of this module ("__main__" for entry point).

        Note: User module dependencies should be registered in registry.modules
        before calling this method (via register_module).

        This drives the five public sub-phases in order. Workspace-wide
        callers (Phase 3+) will eventually call the sub-phases directly
        across all modules; for now `analyze()` is a thin wrapper that
        preserves the legacy per-module-with-publishing pipeline.
        """
        self.bind_imports(module, module_name)
        self.register_records_and_protocols(module)
        self.register_signatures(module)
        self.analyze_bodies(module)
        self.run_phase2_fixpoint(module)

    def bind_imports(self, module: TpyModule, module_name: str = "__main__") -> None:
        """Sub-phase 1: set module context, bind imports + bare modules.

        Establishes `ctx.module_name`, `ctx.parser_resolver`, the imports
        dict, star-import registrations, tpy type-alias bindings, and
        bare-module bindings. Macro registries and resolved type refs
        are expected to already be in place (compiler-driven).
        """
        # Set module context
        self.ctx.module_name = module_name
        self.ctx.module_cpp_namespace = getattr(module.directives, 'cpp_namespace', None) if hasattr(module, 'directives') else None
        # Builder-trace expansion (phase 7) needs to splice synthesized
        # records/functions into the module while bodies are being analyzed.
        self._module = module
        # Module resolver -- consumed by `_infer_field_type_from_default`
        # (same-module record lookup) and the macro post-resolve step in
        # `register_record` (body TypeRefNodes on macro-added methods).
        # See `SemanticContext.parser_resolver` docstring.
        self.ctx.parser_resolver = module.resolver

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
                is_star = import_module_name in module.star_imports
                for original_name, local_name in names:
                    is_user_module = import_module_name in module.user_module_imports
                    # Generic IMPORTED_NAME goes in first so the
                    # kind-specific binding installed by
                    # `_register_user_module_import` (bind_variable /
                    # bind_enum / ...) lands on top for callers that
                    # consult the namespace by kind.
                    self.ctx.imported_names[local_name] = (import_module_name, original_name)
                    self.ctx.global_ns.bind_imported_name(local_name, import_module_name, original_name)

                    if is_star and is_user_module:
                        if not self._register_user_module_import(
                                import_module_name, original_name, local_name,
                                from_star_import=True):
                            # Re-resolve a name absent from this source's
                            # exports against any module that does export
                            # it (typical case: `Int32` star-imported via
                            # `from utils import *` where utils itself
                            # re-imports it from tpy). `_bind_star_reexport`
                            # rewrites `imported_names` and the namespace
                            # binding to point at the actual definer.
                            self._bind_star_reexport(original_name, local_name)
                        continue

                    if is_user_module:
                        self._register_user_module_import(import_module_name, original_name, local_name)
                    # Register tpy type aliases from .py stubs (no-op for non-alias names)
                    elif import_module_name == "tpy":
                        self._register_tpy_type_alias(original_name, local_name)
                        # Re-exported module-level variables from tpy/__init__.py
                        # need explicit promotion to VARIABLE bindings (the implicit
                        # path otherwise leaves them as IMPORTED_NAME, which use
                        # sites reject). Records/functions/factories already resolve
                        # through other machinery -- leave them alone here.
                        self._register_implicit_module_variable(
                            import_module_name, original_name, local_name)
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

        # TypeRefNodes were already resolved before `analyze()` was
        # called (by `Compiler._resolve_module_refs` between
        # canonicalization and sema).  Everything downstream can read
        # TpyType uniformly.
        self._advance_phase(self._PHASE_NONE, self._PHASE_BIND_IMPORTS)

    def register_records_and_protocols(self, module: TpyModule) -> None:
        """Sub-phase 2: enums, records (incl. macro application), then
        protocols, inheritance + value-type validation, recursive-union
        detection, and type-alias registration. Everything declaration-
        level except function signatures.
        """
        # Method-linkage validation (stubs allowed/required per record
        # linkage, @native decorator restrictions) runs here so any
        # base-resolution errors from the resolve phase fire first.
        self._validate_record_method_linkage(module)

        # Resolve imported type aliases in AST type annotations.
        # The parser creates NominalType("Shape") for imported aliases since it
        # doesn't know about cross-module aliases at parse time. Substitute them
        # with the resolved types before registration/analysis.
        if self.ctx.registry.imported_type_alias_info:
            self._resolve_imported_aliases(module)

        # First pass: register all enums then records.  Method expansion
        # (self-flag derivation, wrapping, cloning, validation) runs
        # inside `register_record` after `_apply_class_macros` so macro-
        # added methods flow through the same pipeline as regular ones.
        # Enums are registered first so record field-type resolution
        # (inside `register_record`) can substitute parser-level
        # `NominalType("Color")` placeholders with the registered enum
        # NominalType that carries `_module_qname`.  Records never depend
        # on each other at registration time, and enums are self-
        # contained, so this ordering is safe even for nested enums --
        # nested enum names are already dotted ("Message.Kind") when
        # `register_enum` sees them, so the parent record need not
        # exist in the sema registry yet.
        for enum in module.all_enums():
            self.registrar.register_enum(enum)
        for record in module.all_records():
            self.registrar.register_record(record)

        # Populate macro_ns with exports from macro dep modules.
        # Macros have run during register_record, so we know which macro
        # modules were used and can import their dependencies.
        self._populate_macro_deps(module)

        # Promote bare NominalTypes that macros emitted via
        # `types.named(...)` to qname-bearing form now that macro_deps
        # are in ctx.registry.  Macros like `@model` reference types
        # (JsonReader, JsonWriter) that main.py doesn't import
        # explicitly; they land in the registry only after
        # `_populate_macro_deps`, so promotion has to run here rather
        # than inside `register_record`.
        self._promote_macro_generated_types(module)

        # Register protocols (two phases to allow forward references)
        for protocol in module.protocols:
            self.registrar.register_protocol(protocol)
        for protocol in module.protocols:
            self.registrar.validate_protocol_parents(protocol)

        # Re-validate record field types now that protocols are registered.
        # Optional[@dynamic] / container-element / union-protocol checks rely
        # on protocol_info_of, which only resolves once protocols are in the
        # registry -- earlier in register_record they silently pass.
        for record in module.all_records():
            self.registrar.validate_record_field_protocols(record)

        # Validate inheritance relationships (after all records and protocols are registered)
        for record in module.all_records():
            self.registrar.validate_record_inheritance(record)

        # Validate @error_return(E) on methods (ReturnException markers are now set)
        for record in module.all_records():
            self.registrar.validate_method_error_returns(record)

        # Validate ValueType fields in a second pass (all ValueType flags are set now)
        for record in module.all_records():
            self.registrar.validate_value_type_fields(record)

        # Validate default_factory fields conform to Default protocol
        self._validate_factory_defaults(module)

        # Propagate @nocopy from fields to containing records.
        # Done after inheritance validation so parent types are resolved.
        # Definition order handles transitive propagation naturally.
        self._propagate_nocopy(module)

        # Validate that every recursive path in a recursive union alias goes
        # through an indirecting container. Runs here -- after record/protocol
        # registration -- so the field-walk in `validate_recursive_union_paths`
        # can resolve same-module RecordInfo / TypeDef entries for user
        # indirecting records (otherwise we'd false-positive on `class
        # MyBox[T]: _ptr: Ptr[T]; type Bad = Lit | MyBox[Bad]`).
        self._validate_recursive_union_paths(module)

        # Detect mutual recursion cycles and tag recursive union aliases
        self._detect_recursive_unions(module)
        self.ctx.recursive_union_names = module.recursive_union_names

        # The parser eagerly expands same-module union aliases, so
        # RecursiveAlias | None becomes UnionType(NoneType, member1, member2, ...)
        # instead of OptionalType(NominalType("RecursiveAlias")).
        # Now that recursive aliases are identified, fix up those annotations.
        if module.recursive_union_names:
            self._fix_recursive_optional_annotations(module)

        # Transfer type aliases from parser to sema registry, validating members
        for name, (typ, loc) in module.type_aliases.items():
            self._validate_type_alias_members(name, typ, loc)
            self.ctx.registry.register_type_alias(name, typ)
            install_binding(
                self.ctx.module_attributes, name,
                SymbolKind.TYPE_ALIAS, typ,
            )
        self._advance_phase(
            self._PHASE_BIND_IMPORTS,
            self._PHASE_REGISTER_RECORDS_AND_PROTOCOLS,
        )

    def register_signatures(self, module: TpyModule) -> None:
        """Sub-phase 3: register function signatures with @overload
        grouping, normalize FunctionInfo refs (`make_ref` over params /
        returns now that records/protocols/value-type flags are final),
        inject synthetic `__name__` Final[str] for non-private modules.
        """
        # Second pass: register all functions (with @overload grouping)
        self._register_functions_with_overloads(module.functions)

        # Normalize FunctionInfo types: wrap non-value params/returns with Ref[T].
        # Must run after ALL types are registered (records, protocols, value types,
        # AND free functions) so make_ref correctly identifies which types need wrapping.
        self._normalize_function_info_refs()

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

        # Register top-level variable declarations (globals) here, in
        # sub-phase 3, rather than waiting for body sema. The
        # registration only inspects each TpyVarDecl's name + declared
        # type (no init-expression analysis); doing it now lets peer
        # modules' `from X import VAR` resolve at decl-time, which the
        # workspace-wide two-pass sema needs (Phase 5 split).
        self.registrar.register_globals(module.top_level_stmts)
        # Top-level statement analysis also moves into the declaration
        # sub-phases. It populates `global_scope` with names assigned via
        # top-level expressions, including tuple-unpacks
        # (`lo, hi = get_bounds()`) whose types only emerge from
        # analyzing the RHS. Without this, peer modules' `from X import lo`
        # would fail because `lo` would not yet be in
        # `X.exports.variables` when the peer's declarations pass runs.
        # Cross-module function calls in top-level statements resolve
        # against decl-finalized peer ModuleInfos (deps run first in
        # topo order in the declarations pass).
        if module.top_level_stmts:
            self._analyze_top_level(module.top_level_stmts)
        self._advance_phase(
            self._PHASE_REGISTER_RECORDS_AND_PROTOCOLS,
            self._PHASE_REGISTER_SIGNATURES,
        )

    def analyze_bodies(self, module: TpyModule) -> None:
        """Sub-phase 4: analyze class-constant initializers, builder-trace
        expansion, record method bodies, free function bodies. Top-level
        statement analysis moved into `register_signatures` so peer
        modules' decl pass sees globals defined via tuple-unpack
        assignments.
        """
        # Fifth pass: analyze class-constant initializers. Runs after
        # `_analyze_top_level` so module-level Final globals are in scope, and
        # before `_analyze_record_methods` so methods see typed constants.
        for record in module.all_records():
            self._analyze_class_constants(record)

        # Pass 5.5: see `_expand_builder_traces`.
        self._expand_builder_traces(module)

        # Sixth pass: analyze record methods (including nested records)
        for record in module.all_records():
            self._analyze_record_methods(record)

        # Seventh pass: analyze function bodies (skip bodyless @overload stubs and @inline)
        for func in module.functions:
            if func.is_inline and not func.is_stub:
                func.skip_codegen = True
                continue
            # Bodied @overload stubs (mode b) carry their own body that needs
            # analysis just like a regular function; bodyless @overload stubs
            # are handled via their trailing implementation.
            if func.is_overload_stub and func.is_stub:
                continue
            self._analyze_function(func)
        self._advance_phase(
            self._PHASE_REGISTER_SIGNATURES,
            self._PHASE_ANALYZE_BODIES,
        )

    def run_phase2_fixpoint(self, module: TpyModule) -> None:
        """Sub-phase 5: call-graph mutation-fact propagation + readonly
        inference. Borrow-check resolution is deferred to a post-pass
        (`finalize_borrow_checks`) that the Compiler runs after every
        module's propagation has completed so cross-module mutation
        facts are settled before borrow warnings are emitted.
        """
        self._propagate_mutation_facts()
        self._sync_inferred_const(module)
        self._advance_phase(
            self._PHASE_ANALYZE_BODIES,
            self._PHASE_PHASE2_FIXPOINT,
        )

    def finalize_borrow_checks(self) -> None:
        """Emit / suppress deferred borrow warnings using fully-propagated
        cross-module facts. Compiler runs this once per analyzer in a
        third workspace-wide pass after every module has completed
        `run_phase2_fixpoint`, guaranteeing that
        `resolve_pending_borrow_checks` reads finalized
        `mutated_params` / `structural_mutated_params` regardless of
        body-sema iteration order. This is what makes the suite pass
        byte-identical when body sema runs in reverse-topo order
        (Phase 6 acceptance criterion).
        """
        self.calls.resolve_pending_borrow_checks()

    def _normalize_function_info_refs(self) -> None:
        """Apply make_ref to all registered FunctionInfo param/return types.

        Wraps non-value types with RefType so codegen and type inference see
        explicit reference semantics. Called after all types (including value
        type markers) are registered, so make_ref correctly identifies which
        types need wrapping.

        Stamps `originating_module` on every FunctionInfo registered
        in this module's analyzer. Imported peer FIs already carry
        their defining module's stamp, so we skip when set. Opaque
        FIs (builtin / @builtin_decorator stubs) keep
        `originating_module=None` so mutation propagation treats
        them as unowned and skips them.
        """
        current_module = self.ctx.module_name

        def _ref_params(params: list) -> list:
            result = []
            for p in params:
                if isinstance(p, ParamInfo):
                    result.append(dc_replace(p, type=make_ref(p.type)))
                else:
                    # Legacy tuple (name, type) form
                    result.append((p[0], make_ref(p[1])))
            return result

        for overloads in self.ctx.registry.functions.values():
            for fi in overloads:
                if fi.originating_module is None and not fi.is_builtin_function and not fi.builtin_decorator_key:
                    fi.originating_module = current_module
                if isinstance(fi.return_type, RefType):
                    continue
                fi.return_type = make_ref(fi.return_type)
                fi.params = _ref_params(fi.params)
        for rec in self.ctx.registry.records.values():
            for method_list in rec.methods.values():
                for fi in method_list:
                    if fi.originating_module is None and not fi.is_builtin_function and not fi.builtin_decorator_key:
                        # Method's defining module = the record's defining module,
                        # not the current module. Records that flow in via cross-
                        # module registration carry RecordInfo.module set during
                        # their owning module's sema; trust that when present.
                        fi.originating_module = rec.module or current_module
                    if isinstance(fi.return_type, RefType):
                        continue
                    fi.return_type = make_ref(fi.return_type)
                    fi.params = _ref_params(fi.params)

    def _propagate_mutation_facts(self) -> None:
        """Run call-graph mutation-fact propagation, taking advantage of
        the workspace-shared `registry.modules` dict to pull peer
        modules' FunctionInfos in addition to this module's own.

        Each per-analyzer call walks the workspace once. After the
        first call clears `call_edges` on its propagated FIs, the
        gate (`call_edges is not None`) excludes them from later
        analyzers' collections, so subsequent calls only process
        whatever module just finished body sema -- effectively
        incremental rather than re-doing all modules' work.

        The `originating_module` distinction from Phase 6 stays
        (gates body-sema-time fact production); cross-module
        mutating calls propagate facts uniformly through the
        workspace call graph.
        """
        all_fis: list = []
        seen: set[int] = set()

        def _collect_from(reg_or_mod) -> None:
            for overloads in reg_or_mod.functions.values():
                for fi in overloads:
                    if id(fi) in seen:
                        continue
                    if fi.direct_mutated_params is not None and fi.call_edges is not None:
                        seen.add(id(fi))
                        all_fis.append(fi)
            for rec in reg_or_mod.records.values():
                for method_overloads in rec.methods.values():
                    for fi in method_overloads:
                        if id(fi) in seen:
                            continue
                        if fi.direct_mutated_params is not None and fi.call_edges is not None:
                            seen.add(id(fi))
                            all_fis.append(fi)

        _collect_from(self.ctx.registry)
        for mod_info in self.ctx.registry.modules.values():
            if not mod_info.is_builtin:
                _collect_from(mod_info)
        propagate_mutation_facts(all_fis)
        infer_method_const(all_fis)

    def _sync_inferred_const(self, module: TpyModule) -> None:
        """Copy inferred is_readonly=True from FunctionInfo back to TpyFunction nodes.

        infer_method_const() sets FunctionInfo.is_readonly on the registry objects,
        but codegen reads method.is_readonly from the TpyFunction AST nodes.
        This pass syncs the two representations.
        """
        for record in module.all_records():
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
            proto_info = protocol_info_of(proto_type)
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
        proto_info = self.ctx.registry.scan_by_short_name(proto_name)
        if proto_info is None:
            return False
        for sig in proto_info.methods:
            if sig.name == method_name and not sig.is_readonly:
                return True
        for parent in proto_info.parent_protocols:
            if self._proto_hierarchy_has_nonconst(parent.name, method_name, visited):
                return True
        return False

    def _is_type_nocopy(self, typ: TpyType) -> bool:
        """Delegate to canonical is_type_nocopy on context."""
        return self.ctx.is_type_nocopy(typ)

    def _validate_factory_defaults(self, module: TpyModule) -> None:
        """Validate that field(default_factory=X) fields have Default-constructible types."""
        default_proto = NominalType("Default", (), is_protocol=True)
        for record in module.all_records():
            for fld in record.fields:
                if not fld.is_factory_default:
                    continue
                if not self.protocols.type_conforms_to_protocol(fld.type, default_proto):
                    raise SemanticError(
                        f"Field '{fld.name}' in '{record.name}' uses "
                        f"default_factory but type '{fld.type}' is not default-constructible",
                        fld.loc or record.loc,
                    )
                # Validate factory name matches field type
                expr = fld.default_expr
                if isinstance(expr, TpyCall) and isinstance(fld.type, NominalType):
                    if expr.func_name != fld.type.name:
                        raise SemanticError(
                            f"default_factory '{expr.func_name}' does not match "
                            f"field type '{fld.type}'",
                            fld.loc or record.loc,
                        )

    def _propagate_nocopy(self, module: TpyModule) -> None:
        """Propagate nocopy from fields/parents to containing records.

        Processes records in definition order. If any field's type is nocopy,
        the containing record becomes nocopy too. Also checks parent type.
        Skips records that define __copy__ (opt-out escape hatch).
        """
        for record in module.all_records():
            info = self.ctx.registry.get_record(record.name)
            if info is None or info.is_nocopy:
                continue
            # __copy__ opts out of propagation
            if info.has_copy:
                continue
            # Check parents (any nocopy parent propagates to the child)
            if any(self._is_type_nocopy(p) for p in info.parents):
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
            if pname in self.ctx.func.current_consumed_own_params:
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
                if (bound is not None and isinstance(bound, NominalType)
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

        self.ctx.func.current_function = func
        if func.is_generator:
            self.ctx._yield_counter = 0
        # Async def bodies are analyzed normally. The await-expression
        # analyzer (sema/expressions.py:_analyze_await) handles the supported
        # v1 forms (direct call to async def, Task[T], Future[T], structural
        # awaitable) and rejects unsupported shapes with a clear diagnostic.
        # Resolve return type (sets is_protocol for cross-module imports)
        func.return_type = make_ref(self.type_ops.resolve_type(func.return_type))
        scope = Scope(parent=self.ctx.global_scope)
        self.ctx.func.current_scope = scope

        # Resolve and normalize params (@readonly wraps all non-value params).
        # Write back to AST so codegen sees Ref/ReadonlyType (codegen reads func.params directly, not FI).
        local_ns = Namespace(parent=self.ctx.global_ns)
        self.ctx.func.current_ns = local_ns
        resolved_params: list[tuple[str, TpyType]] = []
        for i, (pname, ptype) in enumerate(func.params):
            resolved_ptype = self._normalize_param_type(
                self.type_ops.resolve_type(ptype), func.is_readonly)
            func.params[i] = (pname, make_ref(resolved_ptype))
            resolved_params.append((pname, resolved_ptype))

        # Add *args parameter as Span[readonly[T]] to func.params and resolved_params.
        # Insert before keyword-only params to match FunctionInfo param order.
        if func.vararg_name is not None and func.vararg_type is not None:
            va_type = _vararg_span_type(self.type_ops.resolve_type(func.vararg_type))
            kw_start = func.keyword_only_start
            if kw_start is not None and kw_start < len(func.params):
                func.params.insert(kw_start, (func.vararg_name, make_ref(va_type)))
                resolved_params.insert(kw_start, (func.vararg_name, va_type))
                if func.defaults:
                    func.defaults.insert(kw_start, None)
                # Adjust keyword_only_start since we inserted before it
                func.keyword_only_start = kw_start + 1
            else:
                func.params.append((func.vararg_name, make_ref(va_type)))
                resolved_params.append((func.vararg_name, va_type))
                if func.defaults:
                    func.defaults.append(None)
            # Clear vararg_type to prevent re-processing; keep vararg_name for codegen
            func.vararg_type = None

        # Add **kwargs parameter as TypedDict type to func.params and resolved_params.
        if func.kwarg_name is not None and func.kwarg_type is not None:
            kw_type = self.type_ops.resolve_type(func.kwarg_type)
            func.params.append((func.kwarg_name, make_ref(kw_type)))
            resolved_params.append((func.kwarg_name, kw_type))
            if func.defaults:
                func.defaults.append(None)
            # Clear kwarg_type to prevent re-processing; keep kwarg_name for codegen
            func.kwarg_type = None

        # Track consuming method for ownership propagation through fields
        prev_consuming = self.ctx.in_consuming_method
        self.ctx.in_consuming_method = func.is_consuming

        self._validate_named_defaults(func)

        # Shared core: bind params, prescan, analyze body
        scan = self.stmts._prescan_and_analyze_body(func, resolved_params, scope, local_ns)
        self.deduction.resolve_all()

        # Collect generator/async local variables for struct field generation.
        # Same hoisting policy: every function-level local becomes a struct
        # field. Async coros use the same `func.generator_locals` slot; the
        # field-rewrite path in expressions/statements is shared via the
        # in_generator_body flag (the `in_async_coro_body` flag steers only
        # the return-statement rewrite).
        if func.is_generator or func.is_async:
            param_names = {pname for pname, _ in func.params}
            locals_dict: dict[str, 'TpyType'] = {}
            for name, binding in local_ns.all_bindings().items():
                if name not in param_names and binding.type is not None:
                    locals_dict[name] = binding.type
            for name, (vtype, _, _) in self.ctx.func.pending_loop_vars.items():
                if name not in param_names and vtype is not None:
                    locals_dict[name] = vtype
            func.generator_locals = list(locals_dict.items())

        # Finalize nested def escape analysis
        self._finalize_nested_def_escapes()

        # Store Phase 1 local mutation facts (resolved by Phase 2 propagation).
        # Phase 6 (mutual-imports) ownership gate: only mutate body-sema
        # fields on FunctionInfos that originate in this module. The
        # `direct_mutated_params is None` check covers the legacy case
        # (FI not yet body-analyzed); the strict `originating_module ==
        # self.ctx.module_name` check enforces the doc's ownership rule
        # -- declaration-time `_normalize_function_info_refs` stamps
        # `originating_module` on every non-opaque FI, so by the time a
        # body sema reaches this gate, any reachable FI should carry
        # its defining module's name. A None at this point indicates a
        # missing-origin-stamping bug, not a reason to write through.
        func_overloads = self.ctx.registry.get_function(func.name)
        func_info = func_overloads[-1] if func_overloads else None
        if (func_info is not None
                and func_info.direct_mutated_params is None
                and func_info.originating_module == self.ctx.module_name):
            param_list = [pname for pname, _ in func.params]
            direct = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.func.current_mutated_param_names
            )
            func_info.direct_mutated_params = direct
            func_info.call_edges = list(self.ctx.func.current_call_edges)
            # Set mutated_params to direct facts as initial estimate;
            # Phase 2 propagation will replace with the complete transitive set.
            func_info.mutated_params = direct
            # Structural mutation facts (append/insert/clear/del/etc.)
            direct_struct = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.func.current_struct_mutated_param_names
            )
            func_info.direct_structural_mutated_params = direct_struct
            func_info.structural_mutated_params = direct_struct
            # 8b: Return borrow facts -- which params does the return value borrow from?
            func_info.return_borrows_from = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.func.current_returned_param_names
            )
            func_info.addr_escapes_params = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.func.current_addr_escape_param_names
            )
            # Generator functions: the returned struct stores non-value params
            # as T& references (or &ref lambda captures), and str params as
            # string_view, so the result borrows from those params
            if func.is_generator:
                gen_borrows = frozenset(
                    i for i, (_, ptype) in enumerate(func.params)
                    if not ptype.is_value_type() or is_str_type(ptype) or is_str_view_type(ptype)
                )
                if gen_borrows:
                    func_info.return_borrows_from = func_info.return_borrows_from | gen_borrows

        self._warn_unconsumed_own_params(func)
        self._store_analysis_results(func, scan)

        self.ctx.in_consuming_method = prev_consuming
        self.ctx.func.current_function = None
        self.ctx.func.current_scope = None
        self.ctx.func.current_ns = None

    def _store_analysis_results(self, func: TpyFunction, scan: ScanResult) -> None:
        """Store prescan/liveness results for codegen consumption."""
        self.function_scan_results[id(func)] = scan
        if self.ctx.func.hoisted_vars:
            self.function_hoisted_vars[id(func)] = self.ctx.func.hoisted_vars.copy()
        if self.ctx.func.move_through_vars:
            self.function_move_through_vars[id(func)] = self.ctx.func.move_through_vars.copy()
        # Compute movable locals from ever_owned_locals (survives FlowFacts restores)
        movable = set()
        for name in self.ctx.func.ever_owned_locals:
            if name in self.ctx.func.hoisted_vars:
                continue
            if name in self.ctx.func.current_reassigned_vars and name in self.ctx.func.current_lvalue_reassigned:
                continue
            movable.add(name)
        if movable:
            self.function_movable_locals[id(func)] = movable
        if self.ctx.func.global_declarations:
            self.function_global_decls[id(func)] = self.ctx.func.global_declarations.copy()
        self.if_branch_decls.update(self.ctx.if_branch_decls)

    def _validate_named_defaults(self, func: TpyFunction) -> None:
        """Validate `def f(x: T = NAME)` defaults: NAME must be a Final[T] global.

        The parser accepts any TpyName in default position; binding shape is
        deferred here so module-level Finals (declared after functions in the
        registration order) and imported Finals are visible.
        """
        for default in func.defaults:
            if not isinstance(default, TpyName):
                continue
            name = default.name
            if name in self.ctx.final_globals:
                continue
            imp = self.ctx.user_imported_variables.get(name)
            if imp is not None:
                source_module, original_name = imp
                source_info = self.ctx.registry.get_module(source_module)
                var_info = source_info.variables.get(original_name) if source_info else None
                if var_info is not None and var_info.is_final:
                    continue
            raise self.ctx.error(
                f"Default parameter value '{name}' must be a module-level "
                f"Final[T] constant", default)

    def _finalize_nested_def_escapes(self) -> None:
        """Finalize escape analysis for nested defs after the enclosing function is analyzed."""
        # Outer function's parameter names and types
        outer_params: dict[str, TpyType] = {}
        if self.ctx.func.current_function:
            for pname, ptype in self.ctx.func.current_function.params:
                outer_params[pname] = ptype
        outer_param_names = set(outer_params.keys())
        # Get the enclosing function body for "used after" analysis
        body = self.ctx.func.current_function.body if self.ctx.func.current_function else []
        for name in self.ctx.func.nested_def_escapes:
            node = self.ctx.func.nested_def_nodes.get(name)
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
                cap_type = self.ctx.func.current_scope.lookup(cap_name) if self.ctx.func.current_scope else None
                if cap_type is None:
                    continue
                raw_type = unwrap_readonly(cap_type)
                is_own_param = isinstance(raw_type, OwnType)
                if isinstance(raw_type, OwnType):
                    raw_type = raw_type.wrapped
                check_type = raw_type.inner if isinstance(raw_type, OptionalType) else raw_type
                if cap_name in outer_param_names:
                    # Reject str/StrView parameter captures (string_view dangles)
                    if is_str_type(check_type) or is_str_view_type(check_type):
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
                self._reject_bodied_overloads_with_impl(
                    stubs, method, f"{record.name}.{method.name}")
                self._validate_method_overload_group(method, stubs, record.name)
                self.overload_groups[id(method)] = stubs
        for name, stubs in pending_stubs.items():
            # All stubs must be self-contained: each is @native, @cpp_template,
            # or carries its own body (mode b). Mixed native + bodied groups OK.
            if all(s.has_implementation for s in stubs):
                continue
            raise SemanticError(
                f"@overload stubs for '{record.name}.{name}' have no implementation method",
                stubs[0].loc or record.loc,
            )

    def _validate_method_overload_group(
        self, impl: TpyFunction, stubs: list[TpyFunction], record_name: str,
    ) -> None:
        """Validate exhaustiveness of method overload stubs."""
        self._validate_overload_signatures(
            impl, stubs, f"{record_name}.{impl.name}")

    def _validate_overload_signatures(
        self, impl: TpyFunction, stubs: list[TpyFunction], display_name: str,
    ) -> None:
        """Shared @overload stub validation (functions + methods).

        Rules:
        - Stub arity must be <= impl arity. When shorter, every missing trailing
          impl param must have a default.
        - Stub params must prefix-match impl params by name; stub types must be
          compatible with impl types at each shared position (exact match or
          LiteralType over the impl base for non-unions; subset of members for
          unions).
        - When any stub has fewer params than the impl, the impl may not use
          keyword-only params, *args, or **kwargs.
        - Union-typed impl params: stubs that include the param must only
          reference members of the union; when every stub includes the param,
          all members must be covered.
        """
        impl_defaults = impl.defaults if impl.defaults else []
        has_short_arity = any(len(s.params) < len(impl.params) for s in stubs)
        if has_short_arity:
            if impl.keyword_only_start is not None:
                raise SemanticError(
                    f"@overload implementation '{display_name}' cannot have "
                    f"keyword-only parameters when stubs have different arities",
                    impl.loc,
                )
            if impl.vararg_name is not None:
                raise SemanticError(
                    f"@overload implementation '{display_name}' cannot have "
                    f"*args when stubs have different arities",
                    impl.loc,
                )
            if impl.kwarg_name is not None:
                raise SemanticError(
                    f"@overload implementation '{display_name}' cannot have "
                    f"**kwargs when stubs have different arities",
                    impl.loc,
                )

        for stub in stubs:
            if len(stub.params) > len(impl.params):
                raise SemanticError(
                    f"@overload stub for '{display_name}' has "
                    f"{len(stub.params)} parameter(s), more than the "
                    f"implementation's {len(impl.params)}",
                    stub.loc,
                )
            if len(stub.params) < len(impl.params):
                for i in range(len(stub.params), len(impl.params)):
                    impl_pname = impl.params[i][0]
                    has_default = (i < len(impl_defaults)
                                   and impl_defaults[i] is not None)
                    if not has_default:
                        raise SemanticError(
                            f"@overload stub for '{display_name}' omits "
                            f"parameter '{impl_pname}', but the implementation "
                            f"has no default value for it",
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
            param_stubs = [s for s in stubs if param_idx < len(s.params)]
            if not param_stubs:
                # Every stub skips this param; impl default covers all call sites.
                continue
            if isinstance(resolved, OptionalType):
                inner_covered = False
                none_covered = False
                for stub in param_stubs:
                    stub_ptype = self.type_ops.resolve_type(stub.params[param_idx][1])
                    if stub_ptype == resolved:
                        inner_covered = True
                        none_covered = True
                        continue
                    if stub_ptype == resolved.inner:
                        inner_covered = True
                        continue
                    if is_void_like_type(stub_ptype):
                        # `None` as a type annotation parses to VoidType; a
                        # `None` literal's type is NoneType -- both mean "None".
                        none_covered = True
                        continue
                    if isinstance(stub_ptype, LiteralType):
                        if stub_ptype.base_type == resolved.inner:
                            inner_covered = True
                            continue
                        if stub_ptype.is_int_base() and is_integer_type(resolved.inner):
                            inner_covered = True
                            continue
                    raise SemanticError(
                        f"@overload stub type '{stub_ptype}' for parameter '{pname}' "
                        f"is not compatible with implementation type '{resolved}'",
                        stub.loc,
                    )
                # Exhaustiveness only when every stub includes this param; when
                # some stubs skip it the impl default covers the missing path.
                if len(param_stubs) == len(stubs):
                    missing_parts = []
                    if not inner_covered:
                        missing_parts.append(str(resolved.inner))
                    if not none_covered:
                        missing_parts.append("None")
                    if missing_parts:
                        raise SemanticError(
                            f"@overload stubs for '{display_name}' don't cover "
                            f"all variants of parameter '{pname}': missing "
                            f"{', '.join(missing_parts)}",
                            impl.loc,
                        )
                continue
            if not isinstance(resolved, UnionType):
                for stub in param_stubs:
                    stub_ptype = self.type_ops.resolve_type(stub.params[param_idx][1])
                    if stub_ptype != resolved:
                        # LiteralType is compatible with its base type family
                        if isinstance(stub_ptype, LiteralType):
                            if stub_ptype.base_type == resolved:
                                continue
                            if stub_ptype.is_int_base() and is_integer_type(resolved):
                                continue
                        raise SemanticError(
                            f"@overload stub type '{stub_ptype}' for parameter '{pname}' "
                            f"does not match implementation type '{resolved}'",
                            stub.loc,
                        )
                continue
            covered: set[TpyType] = set()
            for stub in param_stubs:
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
            # Only require full union coverage when every stub includes the param.
            # If some stubs skip it, the impl default handles those call sites.
            if len(param_stubs) == len(stubs):
                missing = [m for m in resolved.members if m not in covered]
                if missing:
                    missing_names = ", ".join(str(m) for m in missing)
                    raise SemanticError(
                        f"@overload stubs for '{display_name}' don't cover all "
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
                # Register minimal FunctionInfo so the key flows through exports.
                # return_type comes from func (already VOID-substituted by
                # _resolve_pending_type_refs); this FunctionInfo is used for
                # decorator-key lookup, not call resolution.
                self.ctx.registry.register_function(FunctionInfo(
                    name=func.name, params=[], return_type=func.return_type,
                    builtin_decorator_key=func.builtin_decorator_key,
                ))
                # Refresh the attribute-table binding to point at the
                # registry's list -- pre-pop installed a placeholder
                # FunctionInfo without builtin_decorator_key, which
                # exports.functions does NOT use here (the registry's
                # special one wins).
                install_binding(
                    self.ctx.module_attributes, func.name,
                    SymbolKind.FUNCTION,
                    self.ctx.registry.get_function(func.name),
                )
                continue
            if func.is_overload_stub:
                pending_stubs.setdefault(func.name, []).append(func)
                continue

            # Non-stub function: check if there are pending stubs for this name
            stubs = pending_stubs.pop(func.name, None)
            if stubs:
                self._reject_bodied_overloads_with_impl(stubs, func, func.name)
                self._register_overload_group(func, stubs)
            else:
                self.registrar.register_function(func)

        # Stubs left without an implementation: each stub must be self-contained
        # (native, cpp_template, or carries its own body).
        for name, stubs in pending_stubs.items():
            if all(s.has_implementation for s in stubs):
                self.registrar.register_overload_group(stubs)
            else:
                raise SemanticError(
                    f"@overload stubs for '{name}' have no implementation function",
                    stubs[0].loc,
                )

    @staticmethod
    def _reject_bodied_overloads_with_impl(
        stubs: list[TpyFunction], impl: TpyFunction, display_name: str,
    ) -> None:
        """Disallow mixing bodied @overload with a trailing implementation.

        A bodied @overload variant is itself the implementation of that
        signature. Pairing it with another trailing impl would make dispatch
        ambiguous; require the author to pick one mode per group.
        """
        for stub in stubs:
            if stub.is_overload_stub and not stub.is_stub:
                raise SemanticError(
                    f"@overload '{display_name}' has a body and cannot be "
                    f"paired with a trailing implementation; either remove "
                    f"the body or remove the trailing implementation",
                    stub.loc or impl.loc,
                )

    def _register_overload_group(
        self, impl: TpyFunction, stubs: list[TpyFunction],
    ) -> None:
        """Validate and register an @overload group.

        - Validates stub parameter types cover all union variants in the implementation
        - Registers each stub as a callable FunctionInfo overload
        - Stores the group mapping for codegen
        """
        self._validate_overload_signatures(impl, stubs, impl.name)

        # Register each stub as a callable overload via a single binding
        self.registrar.register_overload_group(stubs)

        # The implementation is NOT registered in the namespace/registry --
        # callers resolve against stubs only. The body is still analyzed
        # via _analyze_function (which works on the TpyFunction directly).

        # Store the group mapping for codegen
        self.overload_groups[id(impl)] = stubs

    def _validate_recursive_union_paths(self, module: TpyModule) -> None:
        """For every alias tagged as recursive in `resolve_refs`, verify that
        every self-reference goes through an indirecting container. Runs
        post-registration so the field walk in `validate_recursive_union_paths`
        can see same-module RecordInfo / TypeDef entries.
        """
        if not module.recursive_union_names:
            return
        from ..cycle_detection import validate_recursive_union_paths
        for alias_name in module.recursive_union_names:
            entry = module.type_aliases.get(alias_name)
            if entry is None:
                continue
            alias_type, alias_loc = entry
            if not isinstance(alias_type, UnionType):
                continue
            err = validate_recursive_union_paths(alias_name, alias_type.members)
            if err is not None:
                raise SemanticError(err, loc=alias_loc)

    def _detect_recursive_unions(self, module: TpyModule) -> None:
        """Detect type cycles and tag union aliases as recursive.

        Runs after all records are registered but before type alias registration.
        Tags aliases in module.recursive_union_names (codegen emits wrapper structs).
        """
        # Build inputs: record fields and non-recursive union aliases
        record_fields: dict[str, list[tuple[str, TpyType]]] = {}
        for record in module.all_records():
            record_fields[record.name] = [(f.name, f.type) for f in record.fields]

        union_aliases: dict[str, tuple[TpyType, ...]] = {}
        alias_locs: dict[str, object] = {}
        for name, (typ, loc) in module.type_aliases.items():
            if isinstance(typ, UnionType) and name not in module.recursive_union_names:
                union_aliases[name] = typ.members
                alias_locs[name] = loc

        if not union_aliases:
            return

        cycles = detect_type_cycles(record_fields, union_aliases)

        for cycle in cycles:
            # Validate indirection: cycle must have at least one indirected edge
            if cycle.is_fully_unindirected():
                edge = cycle.first_unindirected_edge()
                assert edge is not None
                if edge.field_name:
                    msg = (
                        f"Types form an infinite-size cycle: "
                        f"field '{edge.field_name}' in '{edge.source}' references "
                        f"'{edge.target}' without indirection -- "
                        f"use Box[{edge.target}] or another indirecting container"
                    )
                else:
                    msg = (
                        f"Types form an infinite-size cycle through "
                        f"'{edge.source}' and '{edge.target}' -- "
                        f"use Box or another indirecting container to break the cycle"
                    )
                # At least one node must be an alias (cycles are reachable
                # from alias nodes only), so loc should never be None here.
                loc = alias_locs.get(edge.source) or alias_locs.get(edge.target)
                raise SemanticError(msg, loc)

            # Tag union aliases in this cycle as recursive
            for alias_name in cycle.alias_names:
                module.recursive_union_names.add(alias_name)

    def _validate_record_method_linkage(self, module: TpyModule) -> None:
        """Check per-record linkage rules against each method:
        @native classes require stub bodies; regular classes disallow
        stub bodies (except @overload stubs) and @native("...") decorators.

        Runs after `_resolve_pending_type_refs` so base-resolution
        errors fire first (previously these checks ran at parse time
        and could mask those errors).
        """
        for record in module.all_records():
            for method in record.methods:
                # Use record.loc so the diagnostic points at the class header.
                if record.linkage != RecordLinkage.DEFAULT:
                    if not method.is_stub:
                        raise SemanticError(
                            "Methods on @native classes must have '...' "
                            "body (stub declaration)",
                            loc=record.loc,
                        )
                else:
                    if method.is_stub and not method.is_overload_stub:
                        raise SemanticError(
                            f"Method '{method.name}' cannot have '...' body "
                            f"on a regular class (only allowed on @native classes)",
                            loc=record.loc,
                        )
                    if method.native_name is not None:
                        raise SemanticError(
                            f"@native(\"...\") decorator on method '{method.name}' "
                            f"is only allowed on @native classes",
                            loc=record.loc,
                        )


    def _fix_recursive_optional_annotations(self, module: TpyModule) -> None:
        """Fix annotations where a recursive union alias + None was flattened.

        The parser eagerly expands same-module aliases, so `Expr | None`
        (where `Expr = Lit | BinOp`) becomes UnionType(NoneType, Lit, BinOp).
        This pass converts that back to OptionalType(NominalType("Expr")) by
        matching the non-None members against the alias definitions.

        When the alias's own definition already includes None (e.g.
        `JsonValue = None | bool | ... | list[JsonValue]`), a bare reference
        expands the same way -- but the input is the alias itself, not
        `Alias | None`. The type stays as the full UnionType so downstream
        passes (is-None narrowing, match dispatch with `case None:`) keep
        the NoneType member visible.

        Only covers module-level declarations (function signatures, record
        fields, top-level vars); local annotations are resolved during body
        analysis.
        """
        # Build reverse map: frozenset(alias non-None members) -> (alias name, alias_has_none)
        alias_by_members: dict[frozenset, tuple[str, bool]] = {}
        for name in module.recursive_union_names:
            entry = module.type_aliases.get(name)
            if entry is not None:
                typ = entry[0]
                if isinstance(typ, UnionType):
                    alias_has_none = any(
                        is_void_like_type(m) for m in typ.members
                    )
                    non_none = frozenset(
                        m for m in typ.members
                        if not is_void_like_type(m)
                    )
                    alias_by_members[non_none] = (name, alias_has_none)

        if not alias_by_members:
            return

        def _fix(typ: TpyType) -> TpyType:
            if not isinstance(typ, UnionType):
                return typ.map_inner_types(lambda t: _fix(t))
            non_none = [m for m in typ.members if not is_void_like_type(m)]
            if len(non_none) == len(typ.members):
                return typ.map_inner_types(lambda t: _fix(t))
            entry = alias_by_members.get(frozenset(non_none))
            if entry is None:
                return typ.map_inner_types(lambda t: _fix(t))
            alias_name, alias_has_none = entry
            if alias_has_none:
                return typ
            return OptionalType(NominalType(alias_name))

        def _fix_func(func: TpyFunction) -> None:
            for i, (pname, typ) in enumerate(func.params):
                fixed = _fix(typ)
                if fixed is not typ:
                    func.params[i] = (pname, fixed)
            if func.return_type is not None:
                fixed = _fix(func.return_type)
                if fixed is not func.return_type:
                    func.return_type = fixed

        for func in module.functions:
            _fix_func(func)
        for record in module.all_records():
            for f in record.fields:
                fixed = _fix(f.type)
                if fixed is not f.type:
                    f.type = fixed
            for method in record.methods:
                _fix_func(method)
        if module.top_level_stmts:
            for stmt in module.top_level_stmts:
                if isinstance(stmt, TpyVarDecl) and stmt.type is not None:
                    fixed = _fix(stmt.type)
                    if fixed is not stmt.type:
                        stmt.type = fixed

    def _validate_type_alias_members(
        self, alias_name: str, typ: TpyType, loc: 'SourceLocation | None'
    ) -> None:
        """Validate that all NominalType members in a type alias are registered."""
        # Recursive union aliases have self-referencing NominalType placeholders
        # inside their members -- safety was already validated.
        if alias_name in self.ctx.recursive_union_names:
            return
        members: list[TpyType] = []
        if isinstance(typ, UnionType):
            members = list(typ.members)
        elif isinstance(typ, NominalType):
            members = [typ]
        for m in members:
            # Bare NominalType reference in an alias body -- the registry
            # lookup is the actual check. Exclude resolved types (which have
            # a _module_qname) and protocols.
            if (isinstance(m, NominalType) and not m.is_protocol
                    and not m._module_qname):
                if self.ctx.registry.get_record(m.name) is None:
                    raise SemanticError(
                        f"Type alias '{alias_name}' references unknown type '{m.name}'",
                        loc,
                    )

    @staticmethod
    def _resolve_alias(typ: TpyType, aliases: dict[str, TpyType],
                        _seen: frozenset[str] = frozenset(),
                        _skip: frozenset[str] = frozenset()) -> TpyType:
        """Recursively substitute alias NominalTypes with their resolved types.

        Uses _seen to prevent infinite recursion on self-referencing aliases.
        _skip contains recursive union alias names that must not be expanded
        (their NominalType placeholders are structural).
        """
        # Alias placeholders are bare parser NominalTypes (no _module_qname,
        # no TypeDef entry). Exclude protocols and anything already resolved.
        if (isinstance(typ, NominalType) and not typ.is_protocol
                and not typ._module_qname):
            if typ.name in _seen or typ.name in _skip:
                return typ
            resolved = aliases.get(typ.name)
            if resolved is not None:
                new_seen = _seen | {typ.name}
                return resolved.map_inner_types(
                    lambda t: SemanticAnalyzer._resolve_alias(t, aliases, new_seen, _skip)
                )
        return typ.map_inner_types(
            lambda t: SemanticAnalyzer._resolve_alias(t, aliases, _seen, _skip)
        )

    def _resolve_imported_aliases(self, module: TpyModule) -> None:
        """Substitute imported alias NominalTypes in module AST type annotations."""
        aliases = self.ctx.registry.type_aliases
        skip = frozenset(module.recursive_union_names)
        for func in module.functions:
            self._resolve_func_aliases(func, aliases, skip)
        for record in module.all_records():
            for f in record.fields:
                f.type = self._resolve_alias(f.type, aliases, _skip=skip)
            for method in record.methods:
                self._resolve_func_aliases(method, aliases, skip)
        for stmt in module.top_level_stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.type is not None:
                stmt.type = self._resolve_alias(stmt.type, aliases, _skip=skip)

    @staticmethod
    def _resolve_func_aliases(func: TpyFunction, aliases: dict[str, TpyType],
                               skip: frozenset[str] = frozenset()) -> None:
        """Resolve alias types in a function's signature."""
        if func.return_type is not None:
            func.return_type = SemanticAnalyzer._resolve_alias(func.return_type, aliases, _skip=skip)
        for i, (name, typ) in enumerate(func.params):
            resolved = SemanticAnalyzer._resolve_alias(typ, aliases, _skip=skip)
            if resolved is not typ:
                func.params[i] = (name, resolved)

    def _analyze_class_constants(self, record: TpyRecord) -> None:
        """Analyze each class constant's initializer and validate it is a
        compile-time constant. Earlier constants in declaration order are
        bound into the class-body namespace so later constants can reference
        them (e.g. `B: Final[int] = A + 1`).
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None or not record_info.class_constants:
            return

        # Class-body context: synthetic module-init scope so the const-validity
        # check (`_find_nonconstant_leaf`) accepts Final names via
        # `analyzed_finals`, and so name resolution inherits module-level
        # bindings. Class-constant names get added to scope/ns as analysis
        # progresses (so later constants can forward-ref earlier ones).
        self.ctx.reset_function_tracking()
        self.ctx.func.current_function = MODULE_INIT_CONTEXT
        self.ctx.func.current_scope = Scope(parent=self.ctx.global_scope)
        self.ctx.func.current_ns = Namespace(parent=self.ctx.global_ns)
        self.ctx.is_top_level = True

        # Class-constant names get added to `analyzed_finals` so the
        # const-validity check accepts forward refs. Track adds and roll
        # back at the end -- avoids cloning the full module Finals set.
        added_finals: list[str] = []

        try:
            for cc_name, cc_fld in record_info.class_constants.items():
                if cc_fld.default_expr is None:
                    # @native extern binding: no initializer to analyze.
                    continue
                self.expr.analyze_expr_with_hint(cc_fld.default_expr, cc_fld.type)
                self.stmts.validate_compile_time_constant(
                    cc_fld.default_expr, cc_fld.type,
                    f"class constant '{record.name}.{cc_name}'",
                    cc_fld.loc,
                )
                # Bind so later class constants in the same body can reference it.
                self.ctx.func.current_scope.define(cc_name, cc_fld.type)
                self.ctx.func.current_ns.bind_variable(cc_name, cc_fld.type)
                self.ctx.analyzed_finals.add(cc_name)
                added_finals.append(cc_name)
        finally:
            self.ctx.analyzed_finals.difference_update(added_finals)
            self.ctx.func.current_function = None
            self.ctx.func.current_scope = None
            self.ctx.func.current_ns = None
            self.ctx.is_top_level = False

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
            # Skip bodyless @overload stubs -- their trailing impl is analyzed
            # instead. Bodied @overload stubs (mode b) need body analysis just
            # like regular methods.
            if method.is_overload_stub and method.is_stub:
                continue

            self.ctx.reset_function_tracking()
            self.ctx.func.current_function = method
            if method.is_generator:
                self.ctx._yield_counter = 0
            # Resolve return type (sets is_protocol for cross-module imports)
            method.return_type = make_ref(self.type_ops.resolve_type(method.return_type))
            scope = Scope(parent=self.ctx.global_scope)
            self.ctx.func.current_scope = scope

            local_ns = Namespace(parent=self.ctx.global_ns)
            self.ctx.func.current_ns = local_ns
            if not method.is_staticmethod:
                info = self.ctx.registry.get_record(record.name)
                self_named = build_record_self_type(
                    record,
                    qname=info.qualified_name() if info is not None else None,
                )
                self_type = self._normalize_param_type(self_named, method.is_readonly)
                scope.define("self", self_type)
                self.ctx.func.var_scope_depth["self"] = scope.depth
                self.ctx.func.definitely_assigned.add("self")
                local_ns.bind_variable("self", self_type)

            # Resolve and normalize params (@readonly wraps all non-value params).
            # Write back to AST so codegen sees Ref/ReadonlyType (codegen reads func.params directly, not FI).
            # For auto_readonly_params_resolved methods, the parser clone already applied
            # ReadonlyType to the params that need it -- skip blanket wrapping.
            readonly_ctx = method.is_readonly and not method.auto_readonly_params_resolved
            resolved_params: list[tuple[str, TpyType]] = []
            for i, (pname, ptype) in enumerate(method.params):
                resolved_ptype = self._normalize_param_type(
                    self.type_ops.resolve_type(ptype), readonly_ctx)
                method.params[i] = (pname, make_ref(resolved_ptype))
                resolved_params.append((pname, resolved_ptype))

            # Add *args parameter as Span[readonly[T]] to method.params and resolved_params
            if method.vararg_name is not None and method.vararg_type is not None:
                va_type = _vararg_span_type(self.type_ops.resolve_type(method.vararg_type))
                kw_start = method.keyword_only_start
                if kw_start is not None and kw_start < len(method.params):
                    method.params.insert(kw_start, (method.vararg_name, make_ref(va_type)))
                    resolved_params.insert(kw_start, (method.vararg_name, va_type))
                    if method.defaults:
                        method.defaults.insert(kw_start, None)
                    method.keyword_only_start = kw_start + 1
                else:
                    method.params.append((method.vararg_name, make_ref(va_type)))
                    resolved_params.append((method.vararg_name, va_type))
                    if method.defaults:
                        method.defaults.append(None)
                method.vararg_type = None

            # Add **kwargs parameter as TypedDict type
            if method.kwarg_name is not None and method.kwarg_type is not None:
                kw_type = self.type_ops.resolve_type(method.kwarg_type)
                method.params.append((method.kwarg_name, make_ref(kw_type)))
                resolved_params.append((method.kwarg_name, kw_type))
                if method.defaults:
                    method.defaults.append(None)
                method.kwarg_type = None

            # __next__ must have an explicit non-void return type annotation
            if method.name == "__next__" and isinstance(method.return_type, VoidType):
                raise self._error(
                    "__next__ method must have a return type annotation",
                    method
                )

            # @inline methods: skip body analysis. The body is a template that
            # gets cloned and substituted at each call site (see methods.py
            # _inline_method_call).
            if method.is_inline and not method.is_stub:
                method.skip_codegen = True
                continue

            # Track consuming method for ownership propagation through fields
            prev_consuming = self.ctx.in_consuming_method
            self.ctx.in_consuming_method = method.is_consuming

            self._validate_named_defaults(method)

            # Shared core: bind params, prescan, analyze body
            scan = self.stmts._prescan_and_analyze_body(method, resolved_params, scope, local_ns)

            # Collect generator local variables for struct field generation
            if method.is_generator:
                param_names = {pname for pname, _ in method.params}
                locals_dict: dict[str, 'TpyType'] = {}
                for name, binding in local_ns.all_bindings().items():
                    if name not in param_names and name != "self" and binding.type is not None:
                        locals_dict[name] = binding.type
                for name, (vtype, _, _) in self.ctx.func.pending_loop_vars.items():
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
            if method.name == "__init__" and self.ctx.func.super_init_call is not None:
                # super().__init__() must be the first non-docstring statement
                first_real_stmt = MethodAnalyzer.find_first_non_docstring_stmt(method.body)
                if first_real_stmt is not None:
                    # Check if first real statement contains the super().__init__() call
                    if not MethodAnalyzer.stmt_contains_super_init(first_real_stmt, self.ctx.func.super_init_call):
                        raise self._error(
                            "super().__init__() must be the first statement in __init__",
                            self.ctx.func.super_init_call
                        )

            # Validate __del__ methods
            if method.name == "__del__":
                record_info = self.ctx.registry.get_record(record.name)
                if self.ctx.func.super_del_call is not None:
                    # super().__del__() must be the last non-docstring statement
                    last_real_stmt = MethodAnalyzer.find_last_non_docstring_stmt(method.body)
                    if last_real_stmt is not None and not is_super_del_call(last_real_stmt):
                        raise self._error(
                            "super().__del__() must be the last statement in __del__",
                            self.ctx.func.super_del_call
                        )
                elif record_info and record_info.parents:
                    # No super().__del__() but parent(s) may have __del__. Any parent
                    # with __del__ runs automatically after this destructor.
                    parent_has_del = False
                    for p in record_info.parents:
                        if not hasattr(p, 'name'):
                            continue
                        parent_info = self.ctx.registry.get_record(p.name)
                        if parent_info and parent_info.get_method("__del__") is not None:
                            parent_has_del = True
                            break
                    if parent_has_del:
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
                # Property methods are not in the methods dict (popped during
                # registration) -- look them up through the properties registry
                # so their return_borrows_from facts are recorded.
                if method_fi is None and method.is_property_getter:
                    prop = record_info.properties.get(method.name)
                    if prop is not None:
                        method_fi = prop.getter
                elif method_fi is None and method.is_property_setter:
                    prop = record_info.properties.get(method.property_name)
                    if prop is not None:
                        method_fi = prop.setter
                if (method_fi is not None
                        and method_fi.direct_mutated_params is None
                        and method_fi.originating_module == self.ctx.module_name):
                    param_list = [pname for pname, _ in method.params]
                    direct = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.func.current_mutated_param_names
                    )
                    method_fi.direct_mutated_params = direct
                    method_fi.direct_self_mutated = self.ctx.func.current_self_mutated
                    method_fi.call_edges = list(self.ctx.func.current_call_edges)
                    method_fi.mutated_params = direct
                    # Structural mutation facts (append/insert/clear/del/etc.)
                    direct_struct = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.func.current_struct_mutated_param_names
                    )
                    if self.ctx.func.current_self_struct_mutated:
                        direct_struct = direct_struct | frozenset({-1})
                    method_fi.direct_structural_mutated_params = direct_struct
                    method_fi.structural_mutated_params = direct_struct
                    # 8b: Return borrow facts
                    returned = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.func.current_returned_param_names
                    )
                    if "self" in self.ctx.func.current_returned_param_names:
                        returned = returned | frozenset([-1])
                    method_fi.return_borrows_from = returned
                    method_fi.addr_escapes_params = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.func.current_addr_escape_param_names
                    )

            self._warn_unconsumed_own_params(method)
            self._store_analysis_results(method, scan)

            self.ctx.func.current_scope = None
            self.ctx.func.current_function = None
            self.ctx.func.current_ns = None

        # Multi-base init-call coverage check. Runs after all method bodies
        # are analyzed so expr.unbound_self_parent_type is set on any
        # BaseN.__init__(self, ...) calls in the child __init__.
        record_info_for_init = self.ctx.registry.get_record(record.name)
        if record_info_for_init is not None:
            self.registrar.validate_multi_base_init_calls(record, record_info_for_init)

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
        # Native types manage their own construction in C++
        if record_info.is_native:
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
            if is_base_init_call(stmt):
                continue
            if is_docstring(stmt):
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
        """Collect all fields from a record and its ancestors, walking MRO base-first.

        Child fields override ancestor fields with the same name (last-write-wins
        in dict semantics, consistent with Python attribute shadowing).
        """
        result: dict[str, FieldInfo] = {}
        for anc_rec in self.ctx.registry.iter_ancestor_records(record_info, reverse=True):
            for f in anc_rec.fields:
                result[f.name] = f
        for f in record_info.fields:
            result[f.name] = f
        return result

    def _type_has_del_or_nocopy(self, field_type: TpyType) -> bool:
        """Check if a type (or any ancestor in its MRO) has __del__ or is @nocopy."""
        inner = field_type
        if isinstance(inner, OwnType):
            inner = inner.wrapped
        inner = unwrap_readonly(inner)
        rec = self.ctx.registry.get_record_for_type(inner)
        if rec is None:
            return False
        if rec.is_nocopy or rec.has_del:
            return True
        for anc_rec in self.ctx.registry.iter_ancestor_records(rec):
            if anc_rec.has_del or anc_rec.is_nocopy:
                return True
        return False

    def _analyze_top_level(self, stmts: list[TpyStmt]) -> None:
        """Analyze top-level statements (for generated main()).

        Treats top-level code like a function body so list literals and other
        constructs go through the same analysis path.
        """
        self.ctx.reset_function_tracking()
        self.ctx.func.current_function = MODULE_INIT_CONTEXT
        self.ctx.func.current_scope = Scope(parent=self.ctx.global_scope)
        self.ctx.is_top_level = True

        # Set up namespace - use global_ns for top-level (globals are visible)
        # New local variables will be added to global_ns as they're declared
        self.ctx.func.current_ns = self.ctx.global_ns

        # Top-level builder-trace expansion runs here (not in pass 5.5)
        # because pass 4 is itself the body-analysis pass for module
        # code -- the rewrite has to land before the analyze loop below.
        self._expand_builder_trace_top_level(stmts)

        # Pre-scan for codegen
        self.top_level_scan_result = scan_reassigned_vars(stmts)
        # Last-use analysis for auto-move (shared with codegen)
        self.ctx.all_last_uses |= analyze_last_uses(
            stmts, self.top_level_scan_result.alias_sources)
        self.ctx.func.current_reassigned_vars = self.top_level_scan_result.reassigned.copy()
        self.ctx.func.current_lvalue_reassigned = self.top_level_scan_result.lvalue_reassigned.copy()
        self.ctx.func.current_aug_assigned_vars = self.top_level_scan_result.aug_assigned.copy()

        for stmt in stmts:
            self.stmts.analyze_stmt(stmt)
        self.deduction.resolve_all()

        if self.ctx.func.hoisted_vars:
            self.top_level_hoisted_vars = self.ctx.func.hoisted_vars.copy()
        if self.ctx.func.move_through_vars:
            self.top_level_move_through_vars = self.ctx.func.move_through_vars.copy()
        self.if_branch_decls.update(self.ctx.if_branch_decls)

        self.ctx.func.current_function = None
        self.ctx.func.current_scope = None
        self.ctx.func.current_ns = None
        self.ctx.is_top_level = False

    def get_expr_type(self, expr: TpyExpr) -> Optional[TpyType]:
        """Get the cached type of an expression."""
        return self.ctx.get_expr_type(expr)

    def _register_implicit_module_variable(self, module_name: str,
                                            original_name: str,
                                            local_name: str) -> bool:
        """Promote an IMPORTED_NAME to VARIABLE when the implicit module
        re-exports a module-level constant.

        The parser's special handling for ``tpy`` / ``typing`` / ``builtins``
        skips ``user_module_imports``, so the regular
        ``_register_user_module_import`` path doesn't run on imports from these
        modules. Records / functions / type aliases already have dedicated
        resolution paths in the implicit-module branch; module-level variables
        do not. This helper covers the variable-only case so re-exported
        constants (e.g. ``__version__`` from a ``# tpy: native_module``
        ``tpy/__init__.py``) become first-class VARIABLE bindings instead of
        being rejected at use site as "not a variable".
        """
        module_info = self.ctx.registry.get_module(module_name)
        if module_info is None or module_info.is_builtin:
            return False
        if not module_info.variables or original_name not in module_info.variables:
            return False
        var_info = module_info.variables[original_name]
        self.ctx.global_scope.define(local_name, var_info.type)
        self.ctx.global_ns.bind_variable(local_name, var_info.type)
        self.ctx.user_imported_variables[local_name] = (module_name, original_name)
        return True

    def _register_tpy_type_alias(self, original_name: str, local_name: str) -> None:
        """Register a single tpy type alias from the compiled tpy module_info."""
        # Compile-time-only types (e.g. FStr) take priority
        self.registrar._register_compile_time_type_alias(local_name, "tpy", original_name)

        # AST-level type aliases (e.g. Float64 = float)
        tpy_info = self.ctx.registry.get_module("tpy")
        if not tpy_info or not tpy_info.type_aliases:
            return
        alias_type = tpy_info.type_aliases.get(original_name)
        if alias_type is not None:
            self.ctx.registry.register_type_alias(local_name, alias_type,
                                                  imported_from=("tpy", original_name))
            if local_name != original_name:
                self.ctx.registry.register_type_alias(original_name, alias_type)

    def _register_all_tpy_type_aliases(self) -> None:
        """Register all tpy type aliases (for bare 'import tpy' and star imports)."""
        tpy_info = self.ctx.registry.get_module("tpy")
        if not tpy_info or not tpy_info.type_aliases:
            return
        for name, alias_type in tpy_info.type_aliases.items():
            self.ctx.registry.register_type_alias(name, alias_type,
                                                  imported_from=("tpy", name))

    def _bind_star_reexport(self, original_name: str, local_name: str) -> None:
        """Try to bind a star-imported re-export from a known module.

        When 'from utils import *' brings in a name like Int32 that utils
        imported from tpy, we need to find the original source and bind it
        correctly so the name is usable.
        """
        for mod_name, mod_info in self.ctx.registry.modules.items():
            if mod_info.is_builtin:
                continue
            if mod_info.has_export(original_name):
                self.ctx.imported_names[local_name] = (mod_name, original_name)
                self.ctx.global_ns.bind_imported_name(local_name, mod_name, original_name)
                self._register_user_module_import(mod_name, original_name, local_name)
                return
        # Fallback: tpy type aliases (e.g. Int32, Float64)
        self._register_tpy_type_alias(original_name, local_name)

    def _register_nested_with_alias(self, items, register_fn,
                                    original_name: str, local_name: str) -> None:
        """Register nested children of `original_name` from `items` (a name -> info
        dict) under both the canonical key (`Outer.Inner`) and -- when the import
        is aliased -- the alias-prefixed key (`L.Inner`). Mirrors the pre-register
        pattern used for the flat record above.
        """
        if not items:
            return
        prefix = original_name + "."
        aliased = local_name != original_name
        for canonical, info in items.items():
            if not canonical.startswith(prefix):
                continue
            register_fn(info, canonical)
            if aliased:
                register_fn(info, local_name + canonical[len(original_name):])

    def _register_from_attribute_binding(
        self, src_binding, local_name: str, module_name: str,
        original_name: str,
    ) -> bool:
        """Register an imported name when the source's per-kind dicts
        haven't materialized yet but its attribute-table binding has
        (cycle re-export). Dispatches on `src_binding.kind` to install
        the same registry/global_ns/user_imported_* state the per-kind
        branches in `_register_user_module_import` would, plus the
        attribute-table binding so downstream consumers see it.
        Returns True on success.
        """
        ult_mod = src_binding.defining_module or module_name
        ult_name = src_binding.canonical_name
        kind = src_binding.kind
        info = src_binding.info
        if kind == SymbolKind.FUNCTION:
            func_infos = info if isinstance(info, list) else [info]
            self.ctx.registry.register_function_group(local_name, func_infos)
            if len(func_infos) > 1:
                self.ctx.global_ns.bind(NameBinding(
                    kind=BindingKind.FUNCTION,
                    name=local_name,
                    func_infos=func_infos,
                ))
            self.ctx.user_imported_functions[local_name] = (module_name, original_name)
            install_binding(
                self.ctx.module_attributes, local_name,
                SymbolKind.FUNCTION, func_infos,
                defining_module=ult_mod, canonical_name=ult_name,
            )
            return True
        if kind == SymbolKind.RECORD:
            self.ctx.registry.register_record(info, local_name)
            if local_name != original_name:
                self.ctx.registry.register_record(info, original_name)
            install_binding(
                self.ctx.module_attributes, local_name,
                SymbolKind.RECORD, info,
                defining_module=ult_mod, canonical_name=ult_name,
            )
            return True
        if kind in (SymbolKind.PROTOCOL_STATIC, SymbolKind.PROTOCOL_DYNAMIC):
            self.ctx.registry.register_protocol(info, local_name)
            self.ctx.global_ns.bind_imported_name(local_name, module_name, original_name)
            self.ctx.user_imported_protocols[local_name] = (module_name, original_name)
            install_binding(
                self.ctx.module_attributes, local_name,
                kind, info,
                defining_module=ult_mod, canonical_name=ult_name,
            )
            return True
        if kind == SymbolKind.ENUM:
            self.ctx.registry.register_enum(info, local_name)
            if local_name != original_name:
                self.ctx.registry.register_enum(info, original_name)
            self.ctx.global_ns.bind_enum(info, name=local_name)
            install_binding(
                self.ctx.module_attributes, local_name,
                SymbolKind.ENUM, info,
                defining_module=ult_mod, canonical_name=ult_name,
            )
            return True
        if kind in (SymbolKind.OPAQUE, SymbolKind.PARSER_KEYWORD):
            # Parser keywords / builtin types have no analyzer registry
            # registration (the parser resolves them via TypeDef /
            # decorator schemas) and no `global_ns` counterpart (use
            # sites for these names go through the parser's
            # `_name_index` route, not the namespace lookup). We only
            # propagate the per-module attribute binding so consumers'
            # `from M import X` (explicit or star) and the cycle
            # re-export pre-pop both find the chain entry. The lack of
            # a `global_ns.bind_imported_name` here is intentional --
            # adding it would shadow the parser-keyword fallback path
            # in `_analyze_name` and break the bootstrap that lets
            # `@builtin_decorator` resolve before sema runs.
            install_binding(
                self.ctx.module_attributes, local_name,
                kind, info,
                defining_module=ult_mod, canonical_name=ult_name,
            )
            return True
        return False

    def _flatten_var_chain(self, source_module: str,
                            original_name: str) -> tuple[str, str]:
        """Walk through `ModuleInfo.reexported_variables` to find the
        ultimate defining module for a re-exported variable. Mirrors
        `Compiler._flatten_var_reexport` but works on the analyzer's
        registry (which has `ModuleInfo`, not `CompiledModule`).
        """
        visited: set[tuple[str, str]] = set()
        cur_mod, cur_name = source_module, original_name
        while True:
            key = (cur_mod, cur_name)
            if key in visited:
                return (cur_mod, cur_name)
            visited.add(key)
            mi = self.ctx.registry.get_module(cur_mod)
            if mi is None:
                return (cur_mod, cur_name)
            next_step = mi.reexported_variables.get(cur_name)
            if next_step is None:
                return (cur_mod, cur_name)
            cur_mod, cur_name = next_step

    def _register_user_module_import(self, module_name: str, original_name: str, local_name: str,
                                     from_star_import: bool = False) -> bool:
        """Register an imported item from a user module.

        Looks up the item in the unified registry (user modules are registered
        as ModuleInfo before analysis) and registers it in the appropriate
        namespace (function, record, or protocol).

        Returns True if the name was found and registered, False if not found
        (only possible when from_star_import is True).

        When from_star_import is True, names not found in the module's exports
        are silently skipped (they may be re-imported names visible in the
        source but not in the compiled module's ModuleInfo).
        """
        module_info = self.ctx.registry.get_module(module_name)
        if module_info is None:
            # Module not in registry - may be a macro module (loaded
            # via macro_registry but never registered as a compiled
            # module). Install a macro binding when applicable so
            # downstream re-export chases find the macro.
            macro_chain = self._resolve_macro_chain(module_name, original_name)
            if macro_chain is not None:
                ult_mod, ult_name, kind = macro_chain
                install_binding(
                    self.ctx.module_attributes, local_name,
                    kind, None,
                    defining_module=ult_mod, canonical_name=ult_name,
                )
            return True
        if module_info.is_builtin:
            # Builtin module (no user file shadowing it), skip to let builtin handling work
            return True

        # Check for function (list of overloads in ModuleInfo)
        if module_info.functions and original_name in module_info.functions:
            func_infos = module_info.functions[original_name]
            # Skip callable registration for special_handling builtins
            # (e.g. native_global in tpy.extern). Statement interception in
            # statements.py looks up the IMPORTED_NAME binding's import_source;
            # registering a function group here would clobber that binding.
            # Still record the import so re-export works.
            if func_infos and func_infos[0].special_handling:
                self.ctx.user_imported_functions[local_name] = (module_name, original_name)
                install_binding(
                    self.ctx.module_attributes, local_name,
                    SymbolKind.FUNCTION, func_infos,
                    defining_module=module_name, canonical_name=original_name,
                )
                return True
            self.ctx.registry.register_function_group(local_name, func_infos)
            if len(func_infos) > 1:
                self.ctx.global_ns.bind(NameBinding(
                    kind=BindingKind.FUNCTION,
                    name=local_name,
                    func_infos=func_infos,
                ))
            self.ctx.user_imported_functions[local_name] = (module_name, original_name)
            # Attribution matches `_extract_declaration_exports`'s
            # function re-export logic: defining_module is the ultimate
            # definer (originating_module); canonical_name is the
            # function's name in that module.
            ult_mod = func_infos[0].originating_module if func_infos else None
            ult_name = func_infos[0].name if func_infos else original_name
            install_binding(
                self.ctx.module_attributes, local_name,
                SymbolKind.FUNCTION, func_infos,
                defining_module=ult_mod or module_name,
                canonical_name=ult_name,
            )
            return True

        # Check for record
        if module_info.records and original_name in module_info.records:
            record_info = module_info.records[original_name]
            # Register with local name for lookup (and original name for type resolution)
            self.ctx.registry.register_record(record_info, local_name)
            if local_name != original_name:
                self.ctx.registry.register_record(record_info, original_name)
            # Also register nested types so Outer.Inner resolves in the importing module.
            self._register_nested_with_alias(
                module_info.records, self.ctx.registry.register_record,
                original_name, local_name)
            self._register_nested_with_alias(
                module_info.enums, self.ctx.registry.register_enum,
                original_name, local_name)
            # Attribution matches `imported_record_qualification` /
            # `exports.reexported_records`: defining_module is the
            # *ultimate* definer (record_info.defining_module),
            # canonical_name is the record's name in that module.
            install_binding(
                self.ctx.module_attributes, local_name,
                SymbolKind.RECORD, record_info,
                defining_module=record_info.defining_module,
                canonical_name=record_info.name,
            )
            return True

        # Check for protocol
        if module_info.protocols and original_name in module_info.protocols:
            protocol_info = module_info.protocols[original_name]
            # Register with local name for lookup (supports aliases)
            self.ctx.registry.register_protocol(protocol_info, local_name)
            # Also bind in namespace so it can be resolved as a type
            self.ctx.global_ns.bind_imported_name(local_name, module_name, original_name)
            self.ctx.user_imported_protocols[local_name] = (module_name, original_name)
            install_binding(
                self.ctx.module_attributes, local_name,
                protocol_kind_for(protocol_info.is_dynamic), protocol_info,
                defining_module=module_name, canonical_name=original_name,
            )
            return True

        # Check for type alias
        if module_info.type_aliases and original_name in module_info.type_aliases:
            typ = module_info.type_aliases[original_name]
            self.ctx.registry.register_type_alias(local_name, typ,
                                                  imported_from=(module_name, original_name))
            # Implicitly import member record types so codegen can qualify them
            if isinstance(typ, UnionType) and module_info.records:
                for member in typ.members:
                    if isinstance(member, NominalType) and member.name in module_info.records:
                        if self.ctx.registry.get_record(member.name) is None:
                            rec = module_info.records[member.name]
                            self.ctx.registry.register_record(rec, member.name)
            install_binding(
                self.ctx.module_attributes, local_name,
                SymbolKind.TYPE_ALIAS, typ,
                defining_module=module_name, canonical_name=original_name,
            )
            return True

        # Check for enum
        if module_info.enums and original_name in module_info.enums:
            enum_type = module_info.enums[original_name]
            self.ctx.registry.register_enum(enum_type, local_name)
            # Also register under original name: the parser resolves aliases
            # back to original names for type annotations (NominalType("Color")
            # even when the alias is "C")
            if local_name != original_name:
                self.ctx.registry.register_enum(enum_type, original_name)
            self.ctx.global_ns.bind_enum(enum_type, name=local_name)
            # Attribution matches `imported_enum_qualification` /
            # `exports.reexported_enums`: defining_module is the
            # ultimate declaring module (EnumInfo.module_name),
            # canonical_name is the enum's name in that module.
            from ..type_def_registry import enum_info_of
            einfo = enum_info_of(enum_type)
            ult_mod = einfo.module_name if (einfo and einfo.module_name) else module_name
            install_binding(
                self.ctx.module_attributes, local_name,
                SymbolKind.ENUM, enum_type,
                defining_module=ult_mod, canonical_name=enum_type.name,
            )
            return True

        # Check for variable
        if module_info.variables and original_name in module_info.variables:
            var_info = module_info.variables[original_name]
            self.ctx.global_scope.define(local_name, var_info.type)
            self.ctx.global_ns.bind_variable(local_name, var_info.type)
            self.ctx.user_imported_variables[local_name] = (module_name, original_name)
            # Flatten the re-export chain so the binding records the
            # ultimate definer (matches how `_extract_body_exports`
            # populates `reexported_variables`).
            ult_mod, ult_name = self._flatten_var_chain(
                module_name, original_name)
            install_binding(
                self.ctx.module_attributes, local_name,
                SymbolKind.VARIABLE, var_info.type,
                defining_module=ult_mod, canonical_name=ult_name,
            )
            return True

        # @builtin_type/@builtin_decorator stubs and parser keywords are handled
        # at parse time, not exported by .py files -- silently skip them here.
        if is_parser_keyword(module_name, original_name):
            return True
        if (self.ctx.registry.get_builtin_type_key(original_name) or
                self.ctx.registry.get_builtin_decorator_key(original_name)):
            return True

        # `from pkg import submod` where submod is itself a registered user
        # module: bind it as a MODULE so consumer code can do
        # `submod.X(...)` / `submod.Type` qualified access (mirrors CPython
        # which treats the imported name as the submodule namespace object).
        submodule_qname = f"{module_name}.{original_name}"
        if self.ctx.registry.get_module(submodule_qname) is not None:
            self.ctx.global_ns.bind_module(submodule_qname, alias=local_name)
            return True

        # Macro re-export (Phase 6): the source module may itself be a
        # macro module that registers `original_name`, or it may be a
        # plain module that re-exports a macro. Walk the chain to find
        # the ultimate macro source and install a binding so consumer
        # sites pick up the macro under the right registry key.
        macro_chain = self._resolve_macro_chain(module_name, original_name)
        if macro_chain is not None:
            ult_mod, ult_name, kind = macro_chain
            install_binding(
                self.ctx.module_attributes, local_name,
                kind, None,
                defining_module=ult_mod, canonical_name=ult_name,
            )
            return True

        # Cycle re-export fallback: the per-kind dicts on the source
        # ModuleInfo may not yet hold `original_name` because the source
        # is a cycle peer whose bind_imports hasn't run. Consult the
        # source's pre-populated attribute table, and if it has a
        # re-export binding, dispatch on kind to register the imported
        # name in the analyzer's registry just as the per-kind branches
        # above would.
        if module_info is not None and module_info.module_attributes is not None:
            src_cell = module_info.module_attributes.get(original_name)
            if src_cell is not None:
                if self._register_from_attribute_binding(
                        src_cell.binding, local_name, module_name, original_name):
                    return True

        # Star imports include all public names from the source (matching
        # CPython), but not all of them have entries in ModuleInfo (e.g.
        # re-imported names like Int32 from tpy). Skip those so the caller
        # doesn't bind them as coming from this module.
        if from_star_import:
            return False

        raise self._error(f"'{original_name}' not found in module '{module_name}'")

    def _resolve_macro_chain(
        self, module_name: str, name: str,
    ) -> 'tuple[str, str, SymbolKind] | None':
        """Find the ultimate macro source for `(module_name, name)` by
        first probing the macro registry directly (handles macro
        modules that aren't registered as compiled modules) then
        walking the binding chain.

        Returns (defining_module, canonical_name, SymbolKind), or None
        if no chain reaches a macro. Used by `_register_user_module_import`
        when the per-kind dict branches miss -- macros aren't tracked
        in the per-kind dicts on `ModuleInfo`.
        """
        registry = self.ctx.macro_registry
        if registry is None:
            return None
        for kind, getter in (
            (SymbolKind.CLASS_MACRO, registry.get_macro),
            (SymbolKind.CALL_MACRO, registry.get_call_macro),
            (SymbolKind.BUILDER_MACRO, registry.get_builder_macro),
        ):
            if getter(module_name, name) is not None:
                return (module_name, name, kind)
        result = walk_attribute_chain(
            self.ctx.registry, module_name, name, is_macro_kind)
        if result is None:
            return None
        ult_mod, ult_name, bd = result
        return (ult_mod, ult_name, bd.kind)

    def _expand_builder_traces(self, module: TpyModule) -> None:
        """Pass 5.5: walk every record-method body and free-function body
        in the module, running the builder-trace expander on each.
        Splices synthesized records / functions into ``module`` before
        passes 6/7 iterate, so emitted method bodies get sema-analyzed
        like user-written ones.

        Top-level statements are expanded later, inside
        ``_analyze_top_level`` (pass 4 is itself the body-analysis pass
        for module-level code, so expansion has to run there).

        No-op when no @builder_macro classes are registered.
        """
        if (self.ctx.macro_registry is None
                or not self.ctx.macro_registry.has_builder_macros()):
            return
        # Both loops iterate snapshots: `all_records()` already returns a
        # fresh list, and `list(module.functions)` is explicit because that
        # one is live. Records and functions synthesized during expansion
        # need no further expansion -- they are built from quoted fragments
        # / AST primitives, not user source containing more builder traces.
        for record in module.all_records():
            for method in record.methods:
                self._expand_builder_trace_body(
                    method, f"{record.name}.{method.name}")
        for func in list(module.functions):
            self._expand_builder_trace_body(func, func.name)

    def _expand_builder_trace_body(self, func: TpyFunction, function_qname: str) -> None:
        """Run BuilderTraceExpander on a function/method body in place.

        No-op when the macro registry has no @builder_macro classes or
        the body is empty.
        """
        if not func.body:
            return
        if (self.ctx.macro_registry is None
                or not self.ctx.macro_registry.has_builder_macros()):
            return
        expander = BuilderTraceExpander(
            ctx=self.ctx, registrar=self.registrar,
            module=self._module, function_being_traced=function_qname,
        )
        new_body = expander.expand(func.body)
        if new_body is not None:
            func.body = new_body

    def _expand_builder_trace_top_level(self, stmts: list[TpyStmt]) -> None:
        """Run BuilderTraceExpander on module top-level statements in place.

        Mutates ``stmts`` directly (the caller passed in module.top_level_stmts).
        """
        if not stmts:
            return
        if (self.ctx.macro_registry is None
                or not self.ctx.macro_registry.has_builder_macros()):
            return
        expander = BuilderTraceExpander(
            ctx=self.ctx, registrar=self.registrar,
            module=self._module, function_being_traced="<module>",
        )
        new_stmts = expander.expand(stmts)
        if new_stmts is not None:
            stmts[:] = new_stmts

    def _promote_macro_generated_types(self, module: TpyModule) -> None:
        """Promote bare `NominalType` placeholders on macro-generated
        method signatures / bodies / registered `FunctionInfo` to
        qname-bearing form using the post-macro-deps `ctx.registry`.

        Macros may reference types (JsonReader, JsonWriter, ...) that
        main.py doesn't import explicitly -- those land in the registry
        only after `_populate_macro_deps`, so promotion must run here
        rather than inside `register_record`.
        """
        resolver = self.ctx.parser_resolver
        if resolver is None:
            return
        registry = self.ctx.registry
        for record in module.all_records():
            # AST-level method (TpyFunction) signatures + bodies.
            for method in record.methods:
                _promote_method_signature(method, registry)
                scope = _merged_method_scope(_record_scope(record), method)
                _walk_body(method.body, scope, resolver, promote_registry=registry)
            # Registered FunctionInfo (captured before promotion) --
            # rebuild ParamInfo / return_type in place so overload
            # resolution sees the promoted param types.
            info = registry.get_record(record.name)
            if info is None:
                continue
            for overloads in info.methods.values():
                for i, finfo in enumerate(overloads):
                    new_params = [
                        dc_replace(p, type=promote_bare_nominals(p.type, registry))
                        for p in finfo.params
                    ]
                    new_return = (
                        promote_bare_nominals(finfo.return_type, registry)
                        if finfo.return_type is not None else finfo.return_type
                    )
                    overloads[i] = dc_replace(
                        finfo,
                        params=new_params,
                        return_type=new_return,
                    )
        # Top-level functions: walk bodies so nested-def signatures
        # (resolved at parse time, before F.5 canonicalization) get
        # their bare NominalTypes promoted.
        for func in module.functions:
            _walk_body(func.body, None, resolver, promote_registry=registry)

    def _resolve_builder_macro_chain(
        self, module_name: str, name: str,
    ) -> str | None:
        """Return the defining module of the builder macro reachable via
        the binding chain from `(module_name, name)`, or None if the
        chain doesn't end at a BUILDER_MACRO binding. Used by
        `_populate_macro_deps` to fold a re-exported builder macro's
        MACRO_DEPS into the consumer's macro_ns.
        """
        result = walk_attribute_chain(
            self.ctx.registry, module_name, name,
            is_kind(SymbolKind.BUILDER_MACRO))
        return result[0] if result is not None else None

    def _populate_macro_deps(self, module: TpyModule) -> None:
        """Populate macro_ns with exports from MACRO_DEPS of used macro modules.

        Collects dependency modules from all macro modules referenced by records
        in this module, then binds their public exports (records, functions,
        enums) into macro_ns -- a shadow namespace below global_ns so user
        bindings always take priority.
        """
        macro_reg = self.ctx.macro_registry
        if macro_reg is None:
            return

        # Collect all macro dep modules from records that have macros.
        # Maps dep_module -> name_filter (None = all exports, list = specific names).
        dep_modules: dict[str, list[str] | None] = {}

        def _merge(mod_name: str) -> None:
            for dep, names in macro_reg.get_deps(mod_name).items():
                if dep not in dep_modules:
                    dep_modules[dep] = names
                elif dep_modules[dep] is None or names is None:
                    dep_modules[dep] = None  # None wins (all exports)
                else:
                    dep_modules[dep] = list(set(dep_modules[dep]) | set(names))

        # Class macros: triggered by records carrying pending_macros.
        for record in module.all_records():
            if not record.pending_macros:
                continue
            for qname, _kwargs in record.pending_macros:
                mod_name = qname.rsplit(".", 1)[0] if "." in qname else ""
                _merge(mod_name)

        # Builder-trace macros: expanded later (during body analysis), so
        # there's no equivalent of pending_macros to inspect here. Use the
        # import set as a proxy -- if this module imports a module that
        # has any @builder_macro classes registered, eagerly bring its
        # deps into macro_ns so synthesized code can reference them.
        # Walks re-export chains via ModuleInfo.module_attributes so
        # `from utils import ArgumentParser` (where utils re-exports
        # from argparse) pulls argparse's deps into main's macro_ns.
        builder_macro_modules = macro_reg.builder_macro_modules()
        for imported, names in module.imports.items():
            if imported in builder_macro_modules:
                _merge(imported)
                continue
            if not isinstance(names, set):
                continue
            for original_name, _local in names:
                ult = self._resolve_builder_macro_chain(imported, original_name)
                if ult is not None and ult in builder_macro_modules:
                    _merge(ult)

        if not dep_modules:
            return

        self.ctx.macro_dep_modules = set(dep_modules.keys())

        # Bind exports from each dep module into macro_ns
        for dep_mod_name, name_filter in dep_modules.items():
            module_info = self.ctx.registry.get_module(dep_mod_name)
            if module_info is None:
                continue

            # Bind the module name itself so synthesized code can use
            # the qualified form (e.g. ``sys.argv`` / ``tpy.copy``).
            # Module attribute access then resolves through ModuleInfo
            # without polluting the user's namespace with every export.
            if name_filter is None and dep_mod_name not in self.ctx.macro_ns:
                self.ctx.macro_ns.bind_module(dep_mod_name)

            if module_info.records:
                for name, record_info in module_info.records.items():
                    if name_filter is not None and name not in name_filter:
                        continue
                    if self.ctx.registry.get_record(name) is None:
                        self.ctx.registry.register_record(record_info, name)
                    self.ctx.macro_ns.bind_imported_name(name, dep_mod_name, name)
                    self.ctx.imported_names.setdefault(name, (dep_mod_name, name))

            if module_info.functions:
                for name, func_infos in module_info.functions.items():
                    if name_filter is not None and name not in name_filter:
                        continue
                    is_special = func_infos and func_infos[0].special_handling
                    if not is_special:
                        if self.ctx.registry.get_function(name) is None:
                            self.ctx.registry.register_function_group(name, func_infos)
                        self.ctx.user_imported_functions.setdefault(name, (dep_mod_name, name))
                    self.ctx.macro_ns.bind_imported_name(name, dep_mod_name, name)
                    self.ctx.imported_names.setdefault(name, (dep_mod_name, name))

            if module_info.enums:
                for name, enum_type in module_info.enums.items():
                    if name_filter is not None and name not in name_filter:
                        continue
                    if self.ctx.registry.get_enum(name) is None:
                        self.ctx.registry.register_enum(enum_type, name)
                    self.ctx.macro_ns.bind_enum(enum_type, name=name)
                    self.ctx.imported_names.setdefault(name, (dep_mod_name, name))
