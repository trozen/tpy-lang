"""
TurboPython Semantic Analyzer

Main orchestrator that wires all components together.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import Callable, Optional

from ..typesys import resolve_int_literals
from ..typesys import (
    TpyType, TypeRegistry, NominalType, AliasRef, UnionType, FinalType, STR, LiteralType, VoidType, VOID,
    NoneType, INT32, ReadonlyType, unwrap_readonly, peel_value_readonly, unwrap_optional_own, unwrap_send_sync, OwnType, OptionalType, RecordInfo, FieldInfo,
    RecursiveUnionInfo, RecursiveAliasInstanceType,
    FunctionInfo, ParamInfo, MethodSignature, is_any_str_type, BIGINT, FLOAT,
    make_ref, unwrap_ref_type, RefType, TypeParamKind, TypeParamRef, TupleType,
    TypeAliasInfo,
    is_integer_type, is_void_like_type,
    span_as_const, span_is_readonly, varargs_as_const, varargs_is_readonly,
    _contains_self_reference,
    contains_type_param,
    BaseInitDuty,
    PendingListType, PendingDictType, PendingSetType, PendingViewType,
    PendingNumType,
)
from ..type_def_registry import is_span, is_varargs, is_spanlike_view
from ..identity_map import IdentityMap, IdentitySet
from ..compilation_context import get_current_compiler
from ..namespace import Namespace, NameBinding, BindingKind
from ..parse import TpyModule, TpyRecord, TpyFunction, TpyExpr, TpyStmt, TpyVarDecl, is_super_del_call, is_base_init_call, is_init_trivia, init_leading_run_end, ParseError
from ..parse.nodes import RecordLinkage, OverloadForm
from .registration import (
    receiver_self_type, _vararg_span_type, skipped_base_ctor_error,
    skipped_base_init_warning, skipped_base_inits,
)
from ..parse.nodes import (
    TpyStrLiteral, TpyAssign, TpyIf, TpyWhile, TpyForEach,
    TpyFieldAccess, TpyName, TpyCall, TpyLambda,
    TpyMethodCall, TpyExprStmt, TpyRaise, TpyTry, TpyMatch, TpyNestedDef, TpyCoerce,
    TpyDelVar, SourceLocation, expr_contains_self_method_call,
)
from .expressions import _collect_body_name_refs
from ..interop.sema_validators import (
    validate_export_class_dunders, warn_export_class_return_alias,
    warn_export_class_unexposed_dunders,
)

# Deferred-resolution placeholder types. After `LocalTypeDeduction.resolve_all()`
# every resolution sink syncs the final type into the namespace, so a hoisted
# resumable-frame local must never still carry one of these -- if it does, a
# sink was missed (the dual current_scope/current_ns hand-sync, see TODO).
_PENDING_LOCAL_TYPES = (
    PendingListType, PendingDictType, PendingSetType, PendingViewType,
    PendingNumType,
)

def _find_pending_leaf(typ: 'TpyType') -> 'TpyType | None':
    """Return the first Pending* leaf in a (possibly composite) type, else None.

    A Pending* nested inside a composite (`list[Pending]`, `tuple[Pending,...]`)
    is as fatal to codegen as a bare one, so the check recurses through wrapper
    types via `inner_types()`."""
    if isinstance(typ, _PENDING_LOCAL_TYPES):
        return typ
    for inner in typ.inner_types():
        found = _find_pending_leaf(inner)
        if found is not None:
            return found
    return None


def _assert_no_pending_locals(locals_dict: dict, func_name: str) -> None:
    """Guard the resumable-frame hoist against unresolved Pending* locals.

    Catches a missed resolution sink at sema (naming the variable) rather than
    as an opaque `PendingListType should be resolved before codegen` crash.
    """
    for name, typ in locals_dict.items():
        leaf = _find_pending_leaf(typ)
        if leaf is not None:
            raise AssertionError(
                f"Internal error: resumable-frame local '{name}' in '{func_name}' "
                f"still has unresolved type {type(leaf).__name__} after resolve_all; "
                f"a Pending* resolution sink did not sync current_ns"
            )


from ..diagnostics import Scope, Diagnostic, SemanticError
from .. import qnames
from .context import (
    SemanticContext, RecordContext, DeferredGenericYieldSettle, LoanInfo, OwnSlot,
    MODULE_INIT_CONTEXT, contains_pending_leaf)
from . import own_copy
from .type_ops import TypeOperations
from .operators import OperatorResolver
from .compatibility import TypeCompatibility
from .list_literals import IterableHelper
from .local_deduction import LocalTypeDeduction
from .pending_num import PendingNums
from .protocols import ProtocolChecker
from .registration import TypeRegistrar
from .narrowing import NarrowingTracker
from .expressions import ExpressionAnalyzer
from .calls import CallAnalyzer
from .methods import MethodAnalyzer
from .statements import StatementAnalyzer

from ..prescan import (ScanResult, scan_reassigned_vars, liveness_alias_sources,
                       collect_fact_kills, collect_in_place_writes)
from ..liveness import (analyze_last_uses, closure_pinned_names,
                        collect_finally_return_candidates)
from .alias_rebind import decide_rebind_storage, global_write_facts
from .frame_close import decide_frame_close, stamp_close_materials
from .may_interrupt import stamp_may_interrupt
from .loop_frames import resolve_loop_frame_calls
from ..value_category import is_rvalue_source, wants_move
from .mutation_propagation import propagate_mutation_facts, infer_method_const
from tpyc import modules as builtin_modules
from ..cycle_detection import detect_type_cycles
from ..parse import SourceLocation, is_parser_keyword, walk_body_stmts
from ..type_def_registry import (
    is_str_type, is_str_view_type, enum_info_of,
    protocol_info_of,
)
from ..typesys import unwrap_own, is_protocol_type, is_protocol_union, RefType
from ..parse.resolve_refs import (
    _walk_body, _merged_method_scope, _record_scope,
    promote_bare_nominals,
)
from .macros import _promote_method_signature
from .builder_trace import BuilderTraceExpander
from .function_macros import run_function_macros, run_deferred_sema_macros
from ..symbol_binding import (
    SymbolKind, install_binding, lookup_imported, protocol_kind_for,
    is_macro_kind, is_kind, walk_attribute_chain, resolve_definer,
)


def _is_static_protocol_type(typ: TpyType) -> bool:
    """Sema-side mirror of codegen's `is_static_protocol_param`.

    Types are already alias-resolved at this point, so no codegen-style
    resolve step is needed. Covers a bare static protocol, an Optional of one,
    and a protocol union; excludes @dynamic protocols (which have a concrete
    Adapter backing and so are not the rejected hoisted-local case).
    """
    unwrapped = unwrap_own(unwrap_readonly(unwrap_ref_type(typ)))
    if isinstance(unwrapped, OptionalType):
        inner = unwrapped.inner
        if is_protocol_type(inner):
            info = protocol_info_of(inner)
            return not (info and info.is_dynamic)
        return False
    if isinstance(unwrapped, UnionType):
        return is_protocol_union(unwrapped)
    if is_protocol_type(unwrapped):
        info = protocol_info_of(unwrapped)
        return not (info and info.is_dynamic)
    return False


def _bare_name_source(expr: TpyExpr) -> 'TpyName | None':
    """Peel a sema coercion wrapper to expose a bare name RHS, else None."""
    while isinstance(expr, TpyCoerce):
        expr = expr.expr
    return expr if isinstance(expr, TpyName) else None


def _find_value_capture_lambdas(stmt: TpyStmt) -> list[TpyLambda]:
    """Escaping by-value-capturing lambdas created at this scope in `stmt`.

    Stops at lambda boundaries (a lambda's body is a separate scope), so
    only closures whose captures snapshot *this* function's locals appear.
    """
    found: list[TpyLambda] = []

    def visit(e: TpyExpr) -> None:
        if isinstance(e, TpyLambda):
            if e.captures_by_value:
                found.append(e)
            return  # separate scope
        # TODO: walk through parse.nodes.walk_expr_tree (the shared pruning visitor) instead of an own children() loop.
        for child in e.children():
            visit(child)

    walk_body_stmts([stmt], visit, lambda s: None)
    return found


def _extract_proto_param_forwarding(
    locals_dict: dict[str, TpyType],
    func_node: TpyFunction,
    write_history: dict[str, list[tuple[TpyType, TpyExpr]]],
) -> None:
    """Pull hoisted locals that forward to a bare static-protocol param out of
    the frame layout, recording the local->param link on the function.

    `xs = it` (where `it: Iterable[T]`) is a single-assignment compile-time
    alias of the param: only the captured param carries the deduced template
    arg `T_<pname>`, so the local needs no frame field -- every use lowers to
    the param's capture. Reaching here means the body passed flow analysis, so
    a single write also dominates every read. Reassigned (>1 write) or
    non-param-sourced locals stay in `locals_dict` (and are rejected at frame
    emit if protocol-typed).
    """
    param_types = {pname: ptype for pname, ptype in func_node.params}
    forwarded: dict[str, str] = {}
    for lname in list(locals_dict):
        if not _is_static_protocol_type(locals_dict[lname]):
            continue
        writes = write_history.get(lname, [])
        if len(writes) != 1:
            continue
        src = _bare_name_source(writes[0][1])
        if src is None or src.name not in param_types:
            continue
        if not _is_static_protocol_type(param_types[src.name]):
            continue
        forwarded[lname] = src.name
        del locals_dict[lname]
    if forwarded:
        func_node.forwarded_locals = forwarded


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


class _GenexprTypeSlot:
    """One type stored on a genexpr's function, as the (node, attr) slot the
    ENCLOSING function's pending-type finalization rewrites: a type that comes
    from a container literal of the enclosing function settles only when that
    function ends, after the genexpr's own body is done."""

    def __init__(self, get: 'Callable[[], TpyType]',
                 put: 'Callable[[TpyType], None]') -> None:
        self._get, self._put = get, put

    @property
    def type(self) -> TpyType:
        return self._get()

    @type.setter
    def type(self, resolved: TpyType) -> None:
        self._put(resolved)


def _genexpr_param_slot(func: TpyFunction, fi: 'FunctionInfo | None',
                        index: int) -> _GenexprTypeSlot:
    # The AST param and the registered signature hold the same type and must
    # settle together.
    def put(resolved: TpyType) -> None:
        func.params[index] = (func.params[index][0], resolved)
        if fi is not None:
            fi.params[index].type = unwrap_ref_type(resolved)
    return _GenexprTypeSlot(lambda: func.params[index][1], put)


def _genexpr_yield_slot(func: TpyFunction,
                        fi: 'FunctionInfo | None') -> _GenexprTypeSlot:
    # The return type, on the AST and in the registered signature, follows.
    def put(resolved: TpyType) -> None:
        func.generator_yield_type = resolved
        returned = NominalType("Iterator", (resolved,), is_protocol=True,
                               _module_qname=qnames.ITERATOR)
        func.return_type = make_ref(returned)
        if fi is not None:
            fi.return_type = returned
    return _GenexprTypeSlot(lambda: func.generator_yield_type, put)


def _genexpr_local_slot(func: TpyFunction, name: str) -> _GenexprTypeSlot:
    def put(resolved: TpyType) -> None:
        func.generator_locals = [(n, resolved if n == name else t)
                                 for n, t in func.generator_locals]
    return _GenexprTypeSlot(lambda: dict(func.generator_locals)[name], put)


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

        # Guards the workspace-wide owning-slot discharge against a second
        # run (the Compiler drives it; `finalize_borrow_checks` covers
        # ad-hoc single-analyzer use).
        self._own_copy_discharged = False
        self._own_copy_verdicts: 'own_copy.OwnCopyVerdicts | None' = None

        # Layer 1: No dependencies on other analyzers
        self.type_ops = TypeOperations(self.ctx)
        self.compat = TypeCompatibility(self.ctx)
        self.iterable = IterableHelper(self.ctx)
        self.deduction = LocalTypeDeduction(self.ctx, self.compat)

        # Layer 2: Depends on type_ops
        self.protocols = ProtocolChecker(self.ctx, self.type_ops)
        self.registrar = TypeRegistrar(self.ctx, self.type_ops, self.protocols, self.compat)
        self.operators = OperatorResolver(self.ctx, self.type_ops, self.protocols)

        self.pend = PendingNums(self.ctx, self.compat)
        self.compat.pend = self.pend
        self.deduction.pend = self.pend

        # Wire up compatibility's deferred dependencies
        self.compat.type_ops = self.type_ops
        self.compat.protocols = self.protocols
        self.compat.deduction = self.deduction
        # validate_hashable_container_elem needs Hashable conformance
        self.type_ops.protocols = self.protocols

        # Narrowing tracker (depends on type_ops, protocols)
        self.narrowing = NarrowingTracker(self.ctx, self.type_ops, self.protocols)
        self.narrowing.pend = self.pend

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
        self.expr.pend = self.pend
        self.calls.pend = self.pend
        self.pend.describe_use = self.expr.describe_pending_use
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
        self.stmts.pend = self.pend
        self.expr.set_scopes(self.stmts.scopes)

        # Per-function/method pre-scan results (shared with codegen)
        self.function_scan_results: IdentityMap = IdentityMap()
        # Per function: locals some rvalue rebind of which owns its storage
        # (the alias-rebind pass's OWN verdict) -- the frame layout's
        # pointer-form criterion.
        self.function_own_rebind_names: IdentityMap = IdentityMap()
        # Per function: what each generator-object local borrows
        # (FunctionTrackingState.frame_local_roots), for the frame layout.
        self.function_frame_local_roots: IdentityMap = IdentityMap()
        # Per loop statement: what a pass of it binds (shared with sema).
        self.loop_bindings: IdentityMap = self.ctx.loop_bindings
        # Per function: the frame-holding locals some `del` closes. In a
        # plain function they hold their frame in an optional.
        self.function_closed_frames: IdentityMap = IdentityMap()
        self.top_level_scan_result: ScanResult | None = None

        # Per-function/method hoisted vars (try/finally + branch predecl)
        self.function_hoisted_vars: IdentityMap = IdentityMap()
        self.top_level_hoisted_vars: set[str] = set()

        # Per-function/method move-through vars (lvalue alias promoted to rvalue)
        self.function_move_through_vars: IdentityMap = IdentityMap()
        self.top_level_move_through_vars: set[str] = set()

        # Per-function movable locals (owned, not hoisted/loop/lvalue-reassigned)
        self.function_movable_locals: IdentityMap = IdentityMap()

        # Per-function locals ever bound to a fresh rvalue. A name absent here
        # is borrow-only: its storage must alias the source, never own a copy.
        self.function_ever_owned_locals: IdentityMap = IdentityMap()

        # Per-function statement-level borrow bindings (name -> any-const),
        # excluding names also bound by non-statement kinds (with-as, for,
        # match captures). Drives the branch pre-decl pointer (alias) form.
        self.function_stmt_borrow_decls: IdentityMap = IdentityMap()

        # Per-function `global x` declarations (for codegen)
        self.function_global_decls: IdentityMap = IdentityMap()

        # Branch-declared vars that need pre-declaration before if-statements
        self.if_branch_decls: IdentityMap = IdentityMap()
        # Branch-declared vars each arm of an if declares itself (the if is
        # the whole else body of another if), with the one joined type
        self.arm_branch_decls: IdentityMap = IdentityMap()

        # @overload dispatch groups: implementation func -> list of stub TpyFunctions
        # Used by codegen to emit per-overload specialized C++ functions.
        self.overload_groups: IdentityMap = IdentityMap()

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
    def expr_types(self) -> IdentityMap:
        return self.ctx.expr_types

    @property
    def var_types(self) -> IdentityMap:
        return self.ctx.var_types

    @property
    def imports(self) -> dict:
        return self.ctx.imports

    @property
    def imported_names(self) -> dict:
        return self.ctx.imported_names

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
                                    "next", "print", "input", "range", "enumerate",
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

    def analyze(self, module: TpyModule, module_name: str = "__main__",
                cpp_module_name: str | None = None) -> None:
        """Analyze a module for semantic correctness.

        Args:
            module: The parsed module AST.
            module_name: Name of this module ("__main__" for entry point).
            cpp_module_name: File-derived structural name used by codegen
                for namespace identity. Defaults to `module_name`; callers
                analyzing an entry-point module should pass the file name
                so `union_wrapper_index.origin` matches what codegen
                compares against. See `bind_imports` for details.

        Note: User module dependencies should be registered in registry.modules
        before calling this method (via register_module).

        This drives the five public sub-phases in order. Workspace-wide
        callers (Phase 3+) will eventually call the sub-phases directly
        across all modules; for now `analyze()` is a thin wrapper that
        preserves the legacy per-module-with-publishing pipeline.
        """
        self.bind_imports(module, module_name, cpp_module_name=cpp_module_name)
        self.register_records_and_protocols(module)
        self.register_signatures(module)
        self.analyze_bodies(module)
        self.run_phase2_fixpoint(module)

    def _warn_export_class_return_alias(self, module: TpyModule) -> None:
        warn_export_class_return_alias(self.ctx, module)

    def _validate_export_class_dunders(self, module: TpyModule) -> None:
        validate_export_class_dunders(self.ctx, module)

    def _warn_export_class_unexposed_dunders(self, module: TpyModule) -> None:
        warn_export_class_unexposed_dunders(self.ctx, module)

    def bind_imports(self, module: TpyModule, module_name: str = "__main__",
                     cpp_module_name: str | None = None) -> None:
        """Sub-phase 1: set module context, bind imports + bare modules.

        Establishes `ctx.module_name`, `ctx.parser_resolver`, the imports
        dict, star-import registrations, tpy type-alias bindings, and
        bare-module bindings. Macro registries and resolved type refs
        are expected to already be in place (compiler-driven).

        `module_name` is the runtime/`__name__` flavor ("__main__" for
        the entry point). `cpp_module_name` (when given) is the
        file-derived structural name that codegen uses for namespace
        identity -- needed by `_register_union_wrappers` so the
        wrapper-index origin matches what codegen compares against.
        Defaults to `module_name` for non-entry callers that already
        pass the structural name.
        """
        # Set module context
        self.ctx.module_name = module_name
        self.ctx.cpp_module_name = cpp_module_name or module_name
        self.ctx.module_cpp_namespace = getattr(module.directives, 'cpp_namespace', None) if hasattr(module, 'directives') else None
        # Stash the module's plugin payload so a @call_macro (which runs during
        # Pass 7 with no module handed to it) can read it via ctx.module_data.
        # Set here -- before any body/top-level analysis -- so it is live for
        # every call-macro expansion in the module.
        self.ctx.macro_data = module.macro_data
        # Builder-trace expansion (phase 7) needs to splice synthesized
        # records/functions into the module while bodies are being analyzed.
        self._module = module
        self.ctx.analyze_genexpr_function = self._analyze_genexpr_function
        self.ctx.queue_readonly_receiver_check = (
            self.calls.pending_readonly_receiver_checks.append)
        self._genexpr_enclosing: list = []
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
        self.ctx.user_module_import_lines = module.user_module_imports
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
                            # it (typical case: `int32` star-imported via
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
                # (e.g. import tpy; x: tpy.float64)
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

    def _compute_record_shadow_facts(self, module: TpyModule) -> None:
        """Set `RecordInfo.shadows_local_type` for each record: true when a C++
        member (own or MRO-inherited method/field/property/class-constant) shares
        a name with a same-module record/enum/@dynamic-protocol, so the member
        shadows that type in record scope and local type references inside must
        render fully-qualified. The shadow name is a type's leading C++ component
        (`Outer` for a nested `Outer.Inner`), which is what an unqualified member
        can hijack."""
        registry = self.ctx.registry
        shadow_names: set[str] = set()
        for rec in module.all_records():
            shadow_names.add(rec.name.split(".", 1)[0])
        for en in module.all_enums():
            shadow_names.add(en.name.split(".", 1)[0])
        for proto in module.protocols:
            if proto.is_dynamic:
                shadow_names.add(proto.name.split(".", 1)[0])
        if not shadow_names:
            return
        for record in module.all_records():
            ri = registry.get_record(record.name)
            if ri is None:
                continue
            members = (set(ri.methods) | {f.name for f in ri.fields}
                       | set(ri.properties) | set(ri.class_constants))
            for anc in registry.iter_ancestor_records(ri):
                members |= (set(anc.methods) | {f.name for f in anc.fields}
                            | set(anc.properties) | set(anc.class_constants))
            ri.shadows_local_type = not members.isdisjoint(shadow_names)

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
        # Populate ProtocolInfo.transitive_supertypes (single source of
        # truth for subtype queries) before any record-side closure or
        # subtype-using check reads it.
        for protocol in module.protocols:
            self.registrar.finalize_protocol_closure(protocol)
        for protocol in module.protocols:
            self.registrar.validate_protocol_parents(protocol)

        # Validate inheritance relationships (after all records and protocols
        # are registered). Sets `implemented_protocols` / MRO on each record.
        for record in module.all_records():
            self.registrar.validate_record_inheritance(record)

        # Compute the member-name/type-name shadow fact now that MRO is
        # populated and every local type is registered -- one source both
        # codegen and THIR read (see RecordInfo.shadows_local_type).
        self._compute_record_shadow_facts(module)

        # Re-validate record field types now that protocols are registered AND
        # inheritance is finalized. Optional[@dynamic] / container-element /
        # union-protocol checks rely on protocol_info_of, which only resolves
        # once protocols are in the registry; the Own[Optional[Polymorphic]]
        # check needs implemented_protocols/MRO populated. Runs after
        # inheritance so both prerequisites hold.
        for record in module.all_records():
            self.registrar.validate_record_field_protocols(record)

        # Validate @error_return(E) on methods (ReturnException markers are now set)
        for record in module.all_records():
            self.registrar.validate_method_error_returns(record)

        # Second pass, after all ValueType flags are set: validate ValueType
        # fields, then drop redundant property mutable clones (a structural
        # record.methods edit, not a validation -- it needs the same settled
        # flags, which is why it lives here and not in register_record).
        for record in module.all_records():
            self.registrar.validate_value_type_fields(record)
            self.registrar.prune_value_property_clones(record)

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
        self._register_union_wrappers(module)

        # The parser eagerly expands same-module union aliases, so
        # RecursiveAlias | None becomes UnionType(NoneType, member1, member2, ...)
        # instead of OptionalType(NominalType("RecursiveAlias")).
        # Now that recursive aliases are identified, fix up those annotations.
        if module.recursive_union_names:
            self._fix_recursive_optional_annotations(module)

        # Transfer type aliases from parser to sema registry, validating members
        compiler = get_current_compiler()
        display_names = compiler.union_display_names if compiler is not None else None
        for name, entry in module.type_aliases.items():
            typ, loc, type_params, type_param_kinds = entry
            is_recursive = name in module.recursive_union_names
            self._validate_type_alias_members(name, typ, loc)
            info = TypeAliasInfo(
                body=typ,
                type_params=list(type_params),
                type_param_kinds=list(type_param_kinds),
                loc=loc,
                is_recursive=is_recursive,
            )
            self.ctx.registry.register_type_alias(name, typ, info=info)
            install_binding(
                self.ctx.module_attributes, name,
                SymbolKind.TYPE_ALIAS, typ,
            )
            # Register the defining-module's canonical short name for
            # `UnionType.__str__` so diagnostics print "Shape" instead
            # of "Circle | Rect". First write wins via `setdefault` --
            # the defining module's name takes precedence over any
            # import aliasing in downstream modules. Skip generic aliases:
            # their display name is only meaningful parameterized
            # (`Either[int32]`), and keying on the unsubstituted members
            # would mislabel an unrelated concrete union of the same shape.
            if (display_names is not None and not type_params
                    and isinstance(typ, UnionType)):
                display_names.setdefault(typ.members, name)
        # Convert the parser's AliasRef placeholders for generic recursive
        # aliases (self-refs in bodies + use sites in annotations) into
        # semantic RecursiveAliasInstanceType nodes, now that the alias
        # registry carries each alias's TypeAliasInfo.
        self._finalize_generic_recursive_aliases(module)
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
        bodies = [*module.functions,
                  *(m for r in module.all_records() for m in r.methods)]
        # A borrow return rooted in a module global is certified durable
        # while the body that RESEATS that global may only be analyzed
        # afterwards, so the rebind fact is decided here, over every body at
        # once, before the first return is checked.
        declared_globals, self.ctx.rebound_globals = global_write_facts(bodies)
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
            self.ctx.module_top_level_stmts = module.top_level_stmts
            self._analyze_top_level(module.top_level_stmts, declared_globals)
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

        # Pass 5.5: function-macro expansion, then builder-trace expansion.
        self._expand_function_macros(module)
        self._expand_builder_traces(module)

        # Bodies analyzed below still carry return_borrows_from=None for
        # ordering reasons (caller above callee); seed the pending set so
        # call-result binds from them register a conservative OPAQUE borrow
        # instead of silently assuming "borrows nothing".
        self._seed_pending_borrow_fact_fis(module)
        self.ctx.deferred_generic_yield_settles.clear()

        # Sixth pass: analyze record methods (including nested records)
        for record in module.all_records():
            self._analyze_record_methods(record)

        # Seventh pass: analyze function bodies (skip bodyless @overload stubs and @inline)
        for func in module.functions:
            if func.is_inline and not func.is_stub:
                func.skip_codegen = True
            if self._skip_body_analysis(func):
                continue
            self._analyze_function(func)
        # Drain post-sema deferred function macros now, while this module's
        # expr_types are populated -- they read inferred types Pass 7 just set.
        self._drain_deferred_sema_macros(module)
        # Every callee's return-borrow fact is final now, so the generic
        # yield-slot verdicts can be answered order-independently.
        self._settle_deferred_generic_yields()
        self.stmts.scopes.settle_deferred_escapes()
        decide_frame_close(self.ctx.frame_close_fis)
        self._advance_phase(
            self._PHASE_REGISTER_SIGNATURES,
            self._PHASE_ANALYZE_BODIES,
        )

    def _seed_pending_borrow_fact_fis(self, module: TpyModule) -> None:
        """Collect the FunctionInfos whose bodies the passes below will
        analyze, mirroring their skip conditions and FI lookups -- exactly
        the FIs whose return_borrows_from=None means "not yet", never
        "opaque stub". Entries become inert once finalize fills the fact.
        """
        pending = self.ctx.pending_borrow_fact_fis
        pending.clear()
        for record in module.all_records():
            record_info = self.ctx.registry.get_record(record.name)
            if record_info is None:
                continue
            for method in record.methods:
                if method.is_stub:
                    continue
                method_fi = record_info.get_method(method.name)
                if method_fi is None and method.is_property_getter:
                    prop = record_info.properties.get(method.name)
                    method_fi = prop.getter if prop is not None else None
                elif method_fi is None and method.is_property_setter:
                    prop = record_info.properties.get(method.property_name)
                    method_fi = prop.setter if prop is not None else None
                if method_fi is not None and method_fi.return_borrows_from is None:
                    pending.add(method_fi)
        for func in module.functions:
            if func.is_inline and not func.is_stub:
                continue
            if func.is_overload_stub and func.is_stub:
                continue
            func_overloads = self.ctx.registry.get_function(func.name)
            if func_overloads and func_overloads[-1].return_borrows_from is None:
                pending.add(func_overloads[-1])

    def run_phase2_fixpoint(self, module: TpyModule) -> None:
        """Sub-phase 5: call-graph mutation-fact propagation + readonly
        inference. Borrow-check resolution is deferred to a post-pass
        (`finalize_borrow_checks`) that the Compiler runs after every
        module's propagation has completed so cross-module mutation
        facts are settled before borrow warnings are emitted.
        """
        self._propagate_mutation_facts()
        self._sync_inferred_const(module)
        self._sync_inferred_vararg_readonly(module)
        self._advance_phase(
            self._PHASE_ANALYZE_BODIES,
            self._PHASE_PHASE2_FIXPOINT,
        )

    def discharge_own_copy_verdicts(
        self, seen: 'set | None' = None,
        verdicts: 'own_copy.OwnCopyVerdicts | None' = None,
    ) -> None:
        """Answer every owning-slot copy obligation this module instantiates.

        `Compiler._finalize_workspace` drives this for every module before
        any module's `finalize_borrow_checks`: an obligation recorded in a
        dependency is answered from here, into the compilation's `verdicts`
        table, and the dependency composes its diagnostics from that table
        only once every module has discharged. It passes one workspace-wide
        `seen` set so a generic several modules instantiate at the same args
        is walked once. An analyzer used without a Compiler falls back to its
        own set and table.
        """
        if self._own_copy_discharged:
            return
        self._own_copy_discharged = True
        if seen is None:
            seen = set()
        if verdicts is None:
            verdicts = own_copy.OwnCopyVerdicts()
        self._own_copy_verdicts = verdicts
        for edge in self.ctx.own_copy_roots:
            own_copy.discharge_edge(self.ctx, self.type_ops, edge, seen, verdicts)

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
        self.discharge_own_copy_verdicts()
        self.ctx.apply_own_copy_verdicts(self._own_copy_verdicts)
        self.calls.resolve_pending_borrow_checks()
        self.calls.resolve_pending_argument_copies()
        self.calls.resolve_pending_copy_receiver_calls()
        self.compat.resolve_pending_iter_copy_checks()
        self.calls.resolve_pending_match_subject_checks()
        resolve_loop_frame_calls(self.ctx)
        self.calls.resolve_pending_readonly_receiver_checks()
        self.expr.resolve_pending_lambda_borrow_checks()
        # Last diagnostic-emitting step for this analyzer, so it is where a
        # body analyzed once per clone collapses back to one report.
        self.ctx.collapse_duplicate_diagnostics()

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
        # Materialize per-param const ABI facts AFTER readonly is finalized
        # (infer_method_const sets is_readonly): the union deep-const verdict
        # keys off fi.is_readonly, so it must run last. Imported inside the
        # method to avoid the codegen_cpp <-> sema import cycle.
        from ..codegen_cpp.param_const import populate_const_borrow_params
        for fi in all_fis:
            populate_const_borrow_params(fi)

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
                fi = record_info.get_method(method.name)
                if fi is None or not fi.is_readonly:
                    continue
                # @readonly(False) is an explicit opt-out -- respect it.
                # For @dynamic protocol overrides, the const-ness of the concrete
                # method must match the virtual base declaration. If the protocol
                # declares the method as non-const, don't infer const here --
                # it would produce a different C++ signature and break the override.
                if (method.readonly_opt_out
                        or self._dynamic_proto_requires_nonconst(record_info, method.name)):
                    fi.root.const_withheld = True
                    continue
                method.is_readonly = True

    def _dynamic_proto_requires_nonconst(self, record_info: RecordInfo, method_name: str) -> bool:
        """Return True if method_name must remain non-const due to a @dynamic protocol override.

        A @dynamic protocol generates C++ pure virtual methods that concrete implementations
        must override with matching (non-const) signatures. The method may be declared in a
        non-dynamic ancestor, but still ends up in the dynamic vtable.

        Walks the MRO so a class inheriting a @dynamic protocol transitively (via a
        concrete-class parent) still has its overrides pinned to the protocol's
        const-ness -- otherwise auto-readonly inference would emit a const signature
        that mismatches the virtual slot.
        """
        visited: set[str] = set()
        for proto_type, _ in self.ctx.registry.iter_dynamic_protocols(record_info):
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

    def _sync_inferred_vararg_readonly(self, module: TpyModule) -> None:
        """Flip vararg slots `Span[T]` -> `Span[readonly[T]]` for bodies that
        don't mutate the vararg pack. Parallels `_sync_inferred_const` for
        methods and the per-index `mutated_params` gate that `decide_param_const`
        consumes for non-vararg ref params -- both extend the same body-mutation
        inference to the vararg case so non-mutating callees collapse to
        `varargs<const T>` and accept const arg sources without caller-side
        marking workarounds. Skip rules mirror `_sync_inferred_const`: explicit
        readonly slot, @native, overload stubs, and @dynamic-protocol overrides
        whose virtual slot is non-const.
        """
        for func in module.functions:
            if func.is_overload_stub or func.native_function:
                continue
            if func.vararg_name is None:
                continue
            # For @overload groups, mutation facts land on the implementation's
            # FI (last in overload list); see analyzer.py:1142-1143 where
            # Phase 1 writes them. Plain defs have a single entry.
            overloads = self.ctx.registry.get_function(func.name)
            if not overloads:
                continue
            self._maybe_flip_vararg_readonly(func, overloads[-1], dyn_pin_nonconst=False)

        for record in module.all_records():
            rec_info = self.ctx.registry.get_record(record.name)
            if rec_info is None:
                continue
            for method in record.methods:
                if method.is_overload_stub or method.native_function:
                    continue
                if method.vararg_name is None:
                    continue
                # @auto_readonly methods are cloned into mutable + const pairs;
                # the mutable clone exists precisely to offer the non-const
                # form, so its vararg slot must stay mutable even when the
                # body doesn't mutate the pack. The const clone gets the slot
                # via its own ReadonlyType wrapping at clone time.
                if method.is_auto_readonly_mutable_clone:
                    continue
                method_fi = rec_info.get_method(method.name)
                if method_fi is None:
                    continue
                pin_nonconst = self._dynamic_proto_pins_vararg_nonconst(
                    rec_info, method.name)
                self._maybe_flip_vararg_readonly(method, method_fi,
                                                 dyn_pin_nonconst=pin_nonconst)

    def _maybe_flip_vararg_readonly(
        self, func: TpyFunction, fi: 'FunctionInfo', *, dyn_pin_nonconst: bool,
    ) -> None:
        """Apply the slot flip when body analysis shows the vararg isn't
        mutated. Mutates both `fi.params` and `func.params` so codegen
        (which reads from the AST) and downstream sema (which reads from FI)
        agree on the resolved slot type.
        """
        va_idx = next((i for i, p in enumerate(fi.params) if p.is_variadic), -1)
        if va_idx < 0:
            return
        va_param = fi.params[va_idx]
        bare_va = unwrap_ref_type(va_param.type)
        if not is_varargs(bare_va) or varargs_is_readonly(bare_va):
            return
        # mutated_params=None means no body was analyzed (native, stub,
        # builtin). The signature is the source of truth in those cases --
        # don't infer-flip what the user (or C++ binding) declared.
        if fi.mutated_params is None or va_idx in fi.mutated_params:
            return
        if dyn_pin_nonconst:
            return
        new_span = varargs_as_const(bare_va)
        new_param_type = make_ref(new_span) if isinstance(va_param.type, RefType) else new_span
        fi.params[va_idx] = ParamInfo(
            name=va_param.name,
            type=new_param_type,
            requires_mutable_lvalue=va_param.requires_mutable_lvalue,
            default_expr=va_param.default_expr,
            keyword_only=va_param.keyword_only,
            is_variadic=True,
        )
        # AST mirror: codegen reads from `func.params` directly.
        for i, (pname, _) in enumerate(func.params):
            if pname == func.vararg_name:
                func.params[i] = (pname, new_param_type)
                break

    def _dynamic_proto_pins_vararg_nonconst(
        self, record_info: 'RecordInfo', method_name: str,
    ) -> bool:
        """Parallel to `_dynamic_proto_requires_nonconst`, but for the vararg
        slot: an override of a @dynamic protocol method must keep the vararg
        mutable when the protocol declares it mutable, since the C++ vtable
        slot is monomorphized to `varargs<T>` vs `varargs<const T>`.

        Reachable but unexercised today: `MethodSignature` (typesys.py:4127)
        does not carry `is_variadic`, so protocol methods can't declare a
        `*args` slot; `_proto_hierarchy_has_vararg_nonconst` therefore never
        finds a mutable-vararg sig. Kept as a forward-compat guard so the
        invariant is in place the day protocols gain vararg method support.
        """
        visited: set[str] = set()
        for proto_type, _ in self.ctx.registry.iter_dynamic_protocols(record_info):
            if self._proto_hierarchy_has_vararg_nonconst(
                    proto_type.name, method_name, visited):
                return True
        return False

    def _proto_hierarchy_has_vararg_nonconst(
        self, proto_name: str, method_name: str, visited: set[str],
    ) -> bool:
        if proto_name in visited:
            return False
        visited.add(proto_name)
        proto_info = self.ctx.registry.scan_by_short_name(proto_name)
        if proto_info is None:
            return False
        for sig in proto_info.methods:
            if sig.name != method_name:
                continue
            for _, ptype in sig.params:
                bare = unwrap_ref_type(ptype) if isinstance(ptype, TpyType) else None
                if (bare is not None and is_spanlike_view(bare)
                        and not (span_is_readonly(bare) or varargs_is_readonly(bare))):
                    return True
        for parent in proto_info.parent_protocols:
            if self._proto_hierarchy_has_vararg_nonconst(
                    parent.name, method_name, visited):
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

        Strips ReadonlyType from value types (copies are always safe; the
        same peel a comprehension loop var gets).
        Wraps non-value types with ReadonlyType in @readonly contexts.
        """
        peeled = peel_value_readonly(ptype)
        if peeled is not ptype:
            return peeled
        if is_readonly_ctx and not isinstance(ptype, ReadonlyType):
            if not ptype.is_value_type():
                return ReadonlyType(ptype)
        return ptype

    def _stamp_frame_materials(self, func: TpyFunction, fi: 'FunctionInfo') -> None:
        """Copy Send/Sync frame-classification materials onto the
        FunctionInfo at body-analysis end (sema/frame_traits.py resolves them
        lazily -- awaited sub-frames may belong to later-analyzed bodies).
        Also registers the fi for # tpyc: frame_send/frame_sync validation.
        """
        if not (func.is_generator or func.is_async):
            return
        fi.frame_locals = list(func.generator_locals or [])
        local_names = {name for name, _ in fi.frame_locals}
        fi.frame_loop_var_names = frozenset(
            name for name in self.ctx.func.pending_loop_vars
            if name in local_names
        )
        fi.frame_subframes = list(self.ctx.func.current_awaited_subframes)
        stamp_close_materials(self.ctx, func, fi)
        if func.loc is not None:
            self.ctx.frame_fact_fns[(func.loc.line, func.name)] = fi

    def _own_payload_exempt(self, payload: TpyType) -> bool:
        """An `Own[payload]` slot the never-consumed warning leaves alone."""
        # Value types: copy == move, no semantic difference
        if payload.is_value_type():
            return True
        # Generic T bounded to ValueType: same as value type
        if isinstance(payload, TypeParamRef):
            bound = self.type_ops.get_type_param_bound(payload.name)
            if (bound is not None and isinstance(bound, NominalType)
                    and bound.qualified_name() == "tpy.ValueType"):
                return True
        # @nocopy types are lifetime-significant: taking Own[T] to
        # consume-by-drop (hand it off and let it drop) is a legitimate
        # ownership use, so don't flag it. The drop lands at the CALLER's
        # scope end, not here: an Own[T] param is a `T&&` borrow, so a
        # callee that doesn't relocate the value never destructs it --
        # relocation (store/forward/return) is the "consume" this check
        # models. That timing matches CPython, where passing a value as an
        # argument likewise doesn't drop it; prompt cleanup is `del`/`with`.
        # (These types CAN be borrowed via a plain param; the suppression
        # is about avoiding false positives on the dispose pattern, not
        # about Own being the only way to pass them.)
        return self.ctx.is_type_nocopy(payload)

    def _warn_unconsumed_owned_tuple(self, func: TpyFunction, pname: str,
                                     tuple_type: TupleType) -> None:
        """An owned-element tuple param is an ownership-transfer param (the
        `std::tuple<...>&&` ABI), so it warns when never consumed just like a
        scalar Own[T]. Once an unpack at its last use has moved its elements
        into locals, or a return has moved one element out, each element is
        a scalar of its own: consuming one consumes that element only, and
        each one dropped warns."""
        consumed = self.ctx.func.current_consumed_own_params
        if OwnSlot(pname) in consumed:
            return
        owned = tuple_type.owned_elements()
        targets = {slot.index: local
                   for local, slot in self.ctx.func.own_consume_aliases.items()
                   if slot.param == pname and slot.index is not None}
        if not targets and not any(OwnSlot(pname, i) in consumed
                                   for i, _ in owned):
            # Never taken apart (unpacked into element locals, or an element
            # moved out on its own): the whole tuple is the unit, and a
            # @nocopy element makes consume-by-drop legitimate.
            if any(self.ctx.is_type_nocopy(t) or t.is_value_type() for _, t in owned):
                return
            self.ctx.warning(
                f"owned tuple param '{pname}' is never consumed "
                f"(not moved out by an unpack, stored, forwarded, or returned)",
                func,
            )
            return
        for i, payload in owned:
            if OwnSlot(pname, i) in consumed or self._own_payload_exempt(payload):
                continue
            local = targets.get(i)
            via = (f"unpacked into '{local}', which is not" if local is not None
                   else "not")
            self.ctx.warning(
                f"Own[{payload}] element {i} of tuple param '{pname}' is never "
                f"consumed ({via} stored in a field, forwarded to another "
                f"Own[T], or returned)",
                func,
            )

    def _warn_unconsumed_own_params(self, func: TpyFunction) -> None:
        """Warn when Own[T] params are never consumed (stored, forwarded, or returned)."""
        if func.is_stub:
            return
        # __move__'s source param is the relocating-move source: it is consumed
        # element-wise (drained slot by slot, not stored/forwarded/returned
        # wholesale), so the store/forward/return check would always misfire.
        if func.name == "__move__":
            return
        for pname, ptype in func.params:
            # A capture borrows the enclosing variable: whatever `Own` its
            # type carries was written, and is checked, in the enclosing body.
            if func.is_capture(pname):
                continue
            # Peel the transparent Send/Sync marker so a Send[Own[T]] param is
            # still recognized as an owned param subject to the consume check.
            bare = unwrap_readonly(unwrap_send_sync(ptype))
            # An owned-element tuple param is an ownership-transfer param (the
            # `std::tuple<...>&&` ABI), so it warns when never consumed just
            # like a scalar Own[T] -- unless every owned element is @nocopy
            # (consume-by-drop is legitimate, mirroring the scalar suppression).
            if isinstance(bare, TupleType):
                if func.takes_ownership_of(pname, ptype):
                    self._warn_unconsumed_owned_tuple(func, pname, bare)
                continue
            own = unwrap_optional_own(bare)
            if own is None:
                continue
            if OwnSlot(pname) in self.ctx.func.current_consumed_own_params:
                continue
            # Optional[Own[T]]: None branch has nothing to consume, making
            # flow-sensitive intersection unreliable
            if isinstance(unwrap_readonly(ptype), OptionalType):
                continue
            if self._own_payload_exempt(own.wrapped):
                continue
            self.ctx.warning(
                f"Own[{own.wrapped}] param '{pname}' is never consumed "
                f"(not stored in a field, forwarded to another Own[T], or returned)",
                func,
            )
        # Bound coroutines never read after binding are destroyed without
        # running -- CPython's "coroutine was never awaited" RuntimeWarning,
        # surfaced at compile time. Drained per body; a nested def draining
        # its enclosing body's entries can only miss a warning, never
        # produce a false one.
        for lname, decl in self.ctx.func.unread_coro_locals.items():
            self.ctx.warning(
                f"bound coroutine '{lname}' is never consumed (never "
                f"awaited, passed to asyncio.create_task/run, or moved); "
                f"it will be destroyed without running",
                decl,
            )
        self.ctx.func.unread_coro_locals.clear()

    def _collect_generator_locals(
        self, func: TpyFunction, local_ns: Namespace, *, exclude_self: bool,
    ) -> None:
        """Hoist function-level locals into `func.generator_locals` for
        resumable-frame struct generation (locals living across a yield /
        await suspension become frame fields).

        Excludes params, `self` (methods), and `global`-declared names -- a
        global lives in the module slot, so a frame field would shadow it and
        swallow writes -- plus `frame_exempt` bindings, whose storage the
        enclosing construct already owns. Must run after `resolve_all` so
        resolved (not Pending*) types reach the frame fields. Shared by the
        free-function and method paths so the exclusion policy can't drift
        between them.
        """
        param_names = {pname for pname, _ in func.params}
        global_decls = self.ctx.func.global_declarations

        def keep(name: str) -> bool:
            return (name not in param_names and name not in global_decls
                    and not (exclude_self and name == "self"))

        locals_dict: dict[str, 'TpyType'] = {}
        for name, binding in local_ns.all_bindings().items():
            if keep(name) and binding.type is not None and not binding.frame_exempt:
                # The frame FIELD is the local's slot, so it is typed at the
                # declaration: the binding carries what the body walk last
                # stored, which a rebind to a narrower rvalue (a container
                # literal at an `xs: list[T] | None` local) would otherwise
                # impose on the field.
                decl_type = self.ctx.local_decl_type(name)
                locals_dict[name] = binding.type if decl_type is None else decl_type
        for name, entry in self.ctx.func.pending_loop_vars.items():
            if keep(name) and entry.var_type is not None:
                locals_dict[name] = entry.var_type
        if func.is_genexpr:
            # A loop var over a container literal of the ENCLOSING function
            # settles with that function, like the source param it comes from.
            enclosing = self._genexpr_enclosing[-1]
            for name, typ in locals_dict.items():
                if contains_pending_leaf(typ):
                    enclosing.pending_elem_type_fields.append(
                        (_genexpr_local_slot(func, name), "type"))
        else:
            _assert_no_pending_locals(locals_dict, func.name)
        _extract_proto_param_forwarding(
            locals_dict, func, self.ctx.func.write_history)
        func.generator_locals = list(locals_dict.items())

    def _enqueue_generic_yield_settle(self) -> None:
        """Park a generator with open-`T` yield sources for the module-end
        settle; runs after the post-body drain, once frame locals exist."""
        if self.ctx.func.pending_generic_yield_sources:
            self.ctx.deferred_generic_yield_settles.append(
                DeferredGenericYieldSettle(
                    self.ctx.func.current_function, self.ctx.func,
                    self.ctx.func.current_scope, self.ctx.func.current_ns))

    def _settle_deferred_generic_yields(self) -> None:
        """Decide, once per generator, whether an open-`T` yield slot LENDS.

        A generator lends what it yields when every yield source outlives a
        suspension; otherwise it hands out a value. The slot is one type for
        the whole frame, so the answer is per generator, not per yield -- and
        it is a VERDICT, not a diagnostic: an unrooted source selects the value
        slot (today's spelling) rather than an error, because the same body
        also instantiates at value `T`s, which borrow nothing.

        Runs after every body in the module, not at the end of the generator's
        own body: the walk reads the callees' `return_borrows_from`, which is
        filled when the CALLEE's body finishes, so an earlier verdict would
        depend on the source order of the two definitions. Each generator's
        own function state is re-installed for its walk, together with the
        function node, scope and namespace parked at enqueue -- the rooting
        half asks that function's params, scope and frame locals.
        """
        live = self.ctx.func
        try:
            for fn, state, scope, ns in self.ctx.deferred_generic_yield_settles:
                if not isinstance(fn, TpyFunction):
                    continue
                self.ctx.restore_function_state(state)
                # The body-exit path nulls the function node, the scope and the
                # namespace; the walk asks the state for all three (params,
                # frame locals, and the shadow lookup that keeps a local
                # shadowing a global from reading as the durable global), so
                # put back what was parked at enqueue.
                state.current_function = fn
                state.current_scope = scope
                state.current_ns = ns
                fn.generic_yield_borrows = all(
                    not ephemeral and not self.compat.is_dangling_return(
                        value, gen_yield=True, assume_unknown_calls_safe=False)
                    for value, ephemeral in state.pending_generic_yield_sources)
                state.pending_generic_yield_sources.clear()
        finally:
            self.ctx.restore_function_state(live)
        self.ctx.deferred_generic_yield_settles.clear()

    def _analyze_function(self, func: TpyFunction, *,
                          genexpr_source_loans: tuple[tuple[str, LoanInfo], ...] = ()) -> None:
        """Analyze a function body."""
        # Stub functions (extern imports with ... body) have no body to analyze
        if func.is_stub:
            return

        self.ctx.reset_function_tracking()
        self.ctx.func.genexpr_source_loans = genexpr_source_loans
        own_copy_mark = self.ctx.own_copy_mark()

        self.ctx.func.current_function = func
        self.ctx.func.body_root = (self._genexpr_enclosing[-1].body_root
                                   if func.is_genexpr else func)
        # Async def bodies are analyzed normally. The await-expression
        # analyzer (sema/expressions.py:_analyze_await) handles the supported
        # v1 forms (direct call to async def, Task[T], Future[T], structural
        # awaitable) and rejects unsupported shapes with a clear diagnostic.
        # Resolve return type (sets is_protocol for cross-module imports). A
        # genexpr's function has none yet: its yield type is inferred from the
        # element, at the yield.
        if not (func.is_genexpr and func.return_type is None):
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
        if func.is_genexpr:
            self._hand_up_pending_types()
        self.deduction.resolve_all()
        if func.is_genexpr:
            self._register_genexpr_function(func, resolved_params)

        # Async coros use the same `func.generator_locals` slot as generators;
        # the field-rewrite path in expressions/statements is shared via the
        # in_generator_body flag (the `in_async_coro_body` flag steers only
        # the return-statement rewrite).
        if func.is_generator or func.is_async:
            self._collect_generator_locals(func, local_ns, exclude_self=False)
        if func.is_genexpr and func.generator_locals:
            # A loop var over an enclosing container LITERAL still carries the
            # literal's element type (`tuple[P, IntLiteral]`); the literal's
            # own resolution would give the default int, and a frame field
            # needs the type now.
            func.generator_locals = [
                (n, resolve_int_literals(t, self.ctx.default_int_for_literal))
                for n, t in func.generator_locals]
            loop = func.genexpr_loop
            if loop is not None and loop.elem_type is not None:
                loop.elem_type = resolve_int_literals(
                    loop.elem_type, self.ctx.default_int_for_literal)
        self.compat.drain_deferred_escape_checks()
        self._enqueue_generic_yield_settle()

        # Finalize nested def escape analysis
        self._finalize_nested_def_escapes()
        self._warn_stale_value_captures()

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
            self._stamp_frame_materials(func, func_info)
            stamp_may_interrupt(self.ctx, func, func_info)
            param_list = [pname for pname, _ in func.params]
            direct = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.func.current_mutated_param_names
            )
            func_info.direct_mutated_params = direct
            func_info.call_edges = list(self.ctx.func.current_call_edges)
            func_info.representational_type_params = frozenset(self.ctx.func.current_representational_params)
            (func_info.own_copy_obligations,
             func_info.own_copy_forwards) = self.ctx.own_copy_drain(own_copy_mark)
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
            direct_elem = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.func.current_elem_mutated_param_names
            )
            func_info.direct_elem_mutated_params = direct_elem
            func_info.elem_mutated_params = direct_elem
            # 8b: Return borrow facts -- which params does the return value borrow from?
            func_info.return_borrows_from = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.func.current_returned_param_names
            )
            func_info.addr_escapes_params = frozenset(
                i for i, pname in enumerate(param_list)
                if pname in self.ctx.func.current_addr_escape_param_names
            )
            # Generator and async functions: the returned frame stores
            # non-value params as T& references (or &ref lambda captures), and
            # str/view params as views of the argument's storage, so the
            # result borrows from those params (the same set registration
            # stamped up front).
            if func.is_generator or func.is_async:
                gen_borrows = TypeRegistrar.generator_borrow_param_indices(
                    [p.type for p in func_info.params])
                if gen_borrows:
                    func_info.return_borrows_from = func_info.return_borrows_from | gen_borrows

        self._warn_unconsumed_own_params(func)
        self._store_analysis_results(func, scan)

        self.ctx.in_consuming_method = prev_consuming
        self.ctx.func.current_function = None
        self.ctx.func.current_scope = None
        self.ctx.func.current_ns = None

    def _analyze_genexpr_function(
            self, func: TpyFunction,
            source_loans: tuple[tuple[str, LoanInfo], ...]) -> None:
        """Analyze a generator expression's function at the expression that
        creates it, in the middle of the enclosing body's analysis.

        The enclosing function's state is set aside whole and put back as
        itself -- the inner analysis starts from a fresh state and never sees
        it, so no snapshot is needed. Every enclosing name the body reads is a
        param of `func`, which is why a module-level scope is the right parent.
        The function joins `module.functions` so the later passes (const
        inference, mutation propagation, frame emission) reach it like any
        other generator; pass 7 skips it, since this is its analysis."""
        live = self.ctx.func
        self._genexpr_enclosing.append(live)
        in_comprehension = self.ctx.in_comprehension
        cond_depth = self.ctx.cond_operand_depth
        consuming = self.ctx.in_consuming_method
        top_level = self.ctx.is_top_level
        for_heads = self.ctx.for_head_bodies
        own_copy_from = self.ctx.own_copy_mark()
        self.ctx.in_comprehension = 0
        self.ctx.cond_operand_depth = 0
        # The body is a FUNCTION body even where the expression sits at module
        # level, and no enclosing `for` head is one of its own.
        self.ctx.is_top_level = False
        self.ctx.for_head_bodies = []
        try:
            self._analyze_function(func, genexpr_source_loans=source_loans)
        finally:
            self._genexpr_enclosing.pop()
            self.ctx.func = live
            self.ctx.in_comprehension = in_comprehension
            self.ctx.cond_operand_depth = cond_depth
            self.ctx.in_consuming_method = consuming
            self.ctx.is_top_level = top_level
            self.ctx.for_head_bodies = for_heads
            own_copy_to = self.ctx.own_copy_mark()
            self.ctx.own_copy_inner_spans.append(
                ((own_copy_from[0], own_copy_to[0]),
                 (own_copy_from[1], own_copy_to[1])))
        self._module.functions.append(func)

    def _hand_up_pending_types(self) -> None:
        """A genexpr's function can read a container literal of the ENCLOSING
        function (its source, a capture), whose Array-vs-list verdict settles
        only when that function's body ends. The nodes recorded as holding
        such a type are finalized there, not at the end of this body."""
        enclosing = self._genexpr_enclosing[-1]
        inner = self.ctx.func
        enclosing.pending_composite_exprs.extend(inner.pending_composite_exprs)
        inner.pending_composite_exprs.clear()
        enclosing.pending_container_exprs.extend(inner.pending_container_exprs)
        inner.pending_container_exprs.clear()
        enclosing.pending_elem_type_fields.extend(inner.pending_elem_type_fields)
        inner.pending_elem_type_fields.clear()

    def _register_genexpr_function(
            self, func: TpyFunction,
            resolved_params: 'list[tuple[str, TpyType]]') -> None:
        """Register a genexpr's function once its body has settled the yield
        type -- the signature a `def` generator declares up front."""
        if func.generator_yield_type is None:
            raise self.ctx.error(
                "generator expression has no element to infer its type from", func)

        # The yield type was read off the element while the body was still
        # open; a loop var's view-vs-owned storage settled after that.
        def settled(t: TpyType) -> TpyType:
            verdict = self.ctx.view_storage_verdict(t)
            return verdict if verdict is not None else t.map_inner_types(settled)
        func.generator_yield_type = self.deduction._deep_resolve_pending(
            settled(func.generator_yield_type))
        func.return_type = NominalType(
            "Iterator", (func.generator_yield_type,), is_protocol=True,
            _module_qname=qnames.ITERATOR)
        body_params = func.params
        func.params = list(resolved_params)
        try:
            # Named by a per-module counter, so the declaration is unique and
            # the FunctionInfo can answer that it is a genexpr's frame.
            self.registrar.register_function(func, unique_declaration=True)
        finally:
            func.params = body_params
        func.return_type = make_ref(func.return_type)
        fis = self.ctx.registry.get_function(func.name)
        enclosing = self._genexpr_enclosing[-1]
        fi = fis[-1] if fis else None
        for i, (_, ptype) in enumerate(func.params):
            if contains_pending_leaf(ptype):
                enclosing.pending_elem_type_fields.append(
                    (_genexpr_param_slot(func, fi, i), "type"))
        if contains_pending_leaf(func.generator_yield_type):
            enclosing.pending_elem_type_fields.append(
                (_genexpr_yield_slot(func, fi), "type"))

    def _store_analysis_results(self, func: TpyFunction, scan: ScanResult) -> None:
        """Store prescan/liveness results for codegen consumption."""
        self.function_scan_results[func] = scan
        self.function_own_rebind_names[func] = self.ctx.func.own_rebind_names
        self.function_frame_local_roots[func] = dict(
            self.ctx.func.frame_local_roots)
        deleted: set[str] = set()
        walk_body_stmts(func.body, lambda e: None,
                        lambda s: deleted.update(s.names)
                        if isinstance(s, TpyDelVar) else None)
        closed = deleted & set(self.ctx.func.frame_local_roots)
        if closed:
            self.function_closed_frames[func] = frozenset(closed)
        if self.ctx.func.hoisted_vars:
            self.function_hoisted_vars[func] = self.ctx.func.hoisted_vars.copy()
        if self.ctx.func.move_through_vars:
            self.function_move_through_vars[func] = self.ctx.func.move_through_vars.copy()
        # Compute movable locals from ever_owned_locals (survives FlowFacts restores)
        movable = set()
        for name in self.ctx.func.ever_owned_locals:
            if name in self.ctx.func.hoisted_vars:
                continue
            if name in self.ctx.func.current_reassigned_vars and name in self.ctx.func.current_lvalue_reassigned:
                continue
            # Reassigned to a borrow source (reference-returning call, ternary
            # of lvalues). The prescan's lvalue_reassigned can't see these (no
            # type info, so it classifies them as rvalue), so they reach here
            # via ever_owned; an auto-move at a later last use would steal from
            # the aliased source.
            if name in self.ctx.func.borrow_reassigned_vars:
                continue
            movable.add(name)
        if movable:
            self.function_movable_locals[func] = movable
        if self.ctx.func.ever_owned_locals:
            self.function_ever_owned_locals[func] = self.ctx.func.ever_owned_locals.copy()
        borrow_decls = {
            name: const
            for name, const in self.ctx.func.stmt_borrow_decls.items()
            if name not in self.ctx.func.nonstmt_bound_names
            or name in self.ctx.func.nonstmt_borrow_bindings
        }
        if borrow_decls:
            self.function_stmt_borrow_decls[func] = borrow_decls
        if self.ctx.func.global_declarations:
            self.function_global_decls[func] = self.ctx.func.global_declarations.copy()
        self.if_branch_decls.update(self.ctx.if_branch_decls)
        self.arm_branch_decls.update(self.ctx.arm_branch_decls)
        self.function_hoisted_vars.update(self.ctx.nested_def_hoisted_vars)

    def _validate_named_defaults(self, func: TpyFunction) -> None:
        """Validate `def f(x: T = NAME)` defaults: NAME must be a Final[T] global.

        The parser accepts any TpyName in default position; binding shape is
        deferred here so module-level Finals (declared after functions in the
        registration order) and imported Finals are visible.
        """
        for i, default in enumerate(func.defaults):
            if not isinstance(default, TpyName):
                continue
            name = default.name
            const_type: 'TpyType | None' = None
            if name in self.ctx.final_globals:
                binding = self.ctx.global_ns.lookup(name)
                const_type = binding.type if binding is not None else None
            else:
                imp = lookup_imported(
                    self.ctx.module_attributes, name, SymbolKind.VARIABLE)
                var_info = None
                if imp is not None:
                    source_module, original_name = imp
                    source_info = self.ctx.registry.get_module(source_module)
                    var_info = source_info.variables.get(original_name) if source_info else None
                if var_info is None or not var_info.is_final:
                    raise self.ctx.error(
                        f"Default parameter value '{name}' must be a module-level "
                        f"Final[T] constant", default)
                const_type = var_info.type
            # The binding resolves only here, so this is also where the
            # constant's type meets the parameter's -- the other default
            # shapes are checked at registration.
            ptype = func.params[i][1] if i < len(func.params) else None
            if const_type is not None and ptype is not None and not contains_type_param(ptype):
                self.compat.check_type_compatible(
                    const_type, ptype,
                    f"parameter '{func.params[i][0]}'", default.loc)

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
                if cap_name == "self" and self.ctx.receiver_self_in_scope():
                    # The receiver is captured as `this` -- an alias,
                    # neither copied nor moved, so the copy/move buckets
                    # and their warnings do not apply. The receiver-
                    # lifetime residual is tracked in BUGS.md.
                    continue
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
                            f" '{cap_name}' which would dangle: a str parameter is"
                            f" a borrowed view of the caller's storage. Use String"
                            f" for an owned capture",
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
                    should_move = wants_move(raw_type)
                    if (should_move and self._capture_aliases_other_storage(
                            cap_name, raw_type)):
                        # Moving a local that aliases other storage (`x = a`,
                        # a select alias, a borrow-call result) would steal
                        # that storage: the closure owns a copy.
                        self.ctx.warning(
                            f"Escaping closure '{name}' copies local"
                            f" '{cap_name}', which aliases storage it does"
                            f" not own; use copy() to make the copy explicit",
                            node)
                    elif should_move and cap_name not in names_used_after:
                        # Last use -- move into the closure, no warning
                        node.move_captures.add(cap_name)
                    elif should_move:
                        # Used after the closure -- must copy, warn
                        self.ctx.warning(
                            f"Escaping closure '{name}' copies local"
                            f" '{cap_name}' (used after closure definition,"
                            f" preventing move). Reorder code so the closure"
                            f" is the last use, or use copy() to make the"
                            f" copy explicit",
                            node)

    def _capture_aliases_other_storage(self, name: str,
                                       raw_type: TpyType) -> bool:
        """Whether a captured reference-type local ALIASES another object:
        its single binding is an lvalue (`x = a`, a select with an
        existing-object operand, a borrow-returning call, an element) that
        was not moved through into it. Moving such a local would steal what
        it aliases. A reassigned local is pointer-form, and what its capture
        holds is filed as BUGS.md#escaping-closure-pointer-local-capture;
        other binding kinds keep the owner verdict (BUGS.md entries)."""
        if raw_type.is_value_type():
            return False
        if (name in self.ctx.func.current_reassigned_vars
                or name in self.ctx.func.move_through_vars):
            return False
        decl = self.ctx.func.var_decl_by_name.get(name)
        if decl is None or decl.init is None:
            return False
        return not is_rvalue_source(self.ctx, decl.init)

    @staticmethod
    def _names_used_after(body: list[TpyStmt], nested_node: TpyNestedDef) -> set[str]:
        """Collect names referenced after the nested def -- in the statements
        that follow it, in the lambdas they build, and by every sibling `def`
        those statements can reach.

        A sibling def's body is a separate scope, but not for this question:
        it reads the local while the escaping closure is alive, so moving the
        local into that closure would leave the sibling reading a moved-from
        object. Which enclosing locals a sibling reads is not re-derived here
        -- `_analyze_nested_def` decided it, and `TpyNestedDef.captured_names`
        is the authoritative answer (the names it writes through with
        `nonlocal` are already in it). That verdict is read off the node
        itself, never matched by name: two arms of an `if` can hold two
        different defs called the same thing.

        A sibling written after this def can be reached; one written before
        can as soon as a statement after this def names it, or a sibling
        already reachable captures it. The answer is deliberately
        conservative -- a sibling that is named but never called, and a
        lambda parameter that shadows the local, both count -- because the
        cost of a wrong YES is a copy and the cost of a wrong NO is a read of
        moved-from storage.
        """
        found = False
        after_stmts: list[TpyStmt] = []
        for stmt in body:
            if found:
                after_stmts.append(stmt)
            elif stmt is nested_node:
                found = True
        if not found:
            # Nested def not at top level of body -- conservatively assume all used
            return set(nested_node.captured_names or [])
        names = _collect_body_name_refs(after_stmts, into_lambdas=True)
        siblings: list[TpyNestedDef] = []
        written_after: IdentitySet = IdentitySet()

        def on_stmt(stmt: TpyStmt) -> None:
            if isinstance(stmt, TpyNestedDef) and stmt is not nested_node:
                siblings.append(stmt)

        def on_after_stmt(stmt: TpyStmt) -> None:
            if isinstance(stmt, TpyNestedDef) and stmt is not nested_node:
                written_after.add(stmt)

        walk_body_stmts(body, lambda e: None, on_stmt)
        walk_body_stmts(after_stmts, lambda e: None, on_after_stmt)
        # Fixpoint: a name one sibling captures can be another sibling's.
        merged: IdentitySet = IdentitySet()
        while True:
            newly = [sib for sib in siblings
                     if sib not in merged
                     and (sib in written_after or sib.func.name in names)]
            if not newly:
                return names
            for sib in newly:
                merged.add(sib)
                names.update(sib.captured_names)

    def _warn_stale_value_captures(self) -> None:
        """Warn on an escaping by-value closure whose captured local is
        rebound at a later top-level statement.

        An escaping closure owns its captures by value (a by-reference
        capture would dangle), freezing the value at creation; CPython
        late-binds via a cell and observes later rebindings, so the
        divergence is silent until the captured local is rebound after the
        closure -- warn exactly there. Out of scope: in-place mutation of a
        captured object (an untracked fact) and loop-var rebinding.
        """
        func = self.ctx.func.current_function
        if func is None:
            return
        body = func.body
        # (stmt index, warn-at node, by-value captured names)
        closures: list[tuple[int, TpyExpr | TpyNestedDef, list[str]]] = []
        rebound_per_stmt: list[set[str]] = []
        # Top-level body index of each nested def, by name; a def bound deeper
        # has no position here and is skipped below.
        nd_index: dict[str, int] = {}
        for idx, stmt in enumerate(body):
            rebound_per_stmt.append(collect_fact_kills([stmt]).names)
            if isinstance(stmt, TpyNestedDef):
                nd_index.setdefault(stmt.func.name, idx)
                continue  # separate scope; no this-scope lambdas inside
            for lam in _find_value_capture_lambdas(stmt):
                if lam.captured_names:
                    closures.append((idx, lam, list(lam.captured_names)))
        for name in self.ctx.func.nested_def_escapes:
            node = self.ctx.func.nested_def_nodes.get(name)
            idx = nd_index.get(name)
            if node is None or idx is None:
                continue  # not a top-level escaping def -- position-conservative
            by_value = [n for n in node.captured_names
                        if n not in node.ref_captures
                        and n not in node.move_captures]
            if by_value:
                closures.append((idx, node, by_value))
        if not closures:
            return
        for stmt_idx, node, names in closures:
            rebound_after: set[str] = set()
            for j in range(stmt_idx + 1, len(body)):
                rebound_after |= rebound_per_stmt[j]
            stale = [n for n in names if n in rebound_after]
            if not stale:
                continue
            first = stale[0]
            self.ctx.warning(
                f"Escaping closure captures local '{first}' by value, but"
                f" '{first}' is reassigned after the closure is created; the"
                f" closure keeps the value from capture time (CPython would"
                f" observe the later value). Capture a fresh local that is"
                f" not reassigned (e.g. `snap = {first}`), or use copy() to"
                f" make the snapshot explicit.",
                node)

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
                self._require_overload_form(
                    stubs, f"{record.name}.{method.name}", impl=method)
                self._validate_method_overload_group(method, stubs, record.name)
                self.overload_groups[method] = stubs
        for name, stubs in pending_stubs.items():
            self._require_overload_form(stubs, f"{record.name}.{name}", impl=None)

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
        declaration_counts: dict[str, int] = {}
        for func in functions:
            declaration_counts[func.name] = declaration_counts.get(func.name, 0) + 1

        for func in functions:
            if func.builtin_decorator_key:
                # Register minimal FunctionInfo so the key flows through exports.
                # return_type comes from func (already VOID-substituted by
                # parse.resolve_refs); this FunctionInfo is used for
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
                self._require_overload_form(stubs, func.name, impl=func)
                self._register_overload_group(func, stubs)
            else:
                self.registrar.register_function(func, unique_declaration=declaration_counts[func.name] == 1)

        # A @dispatch set has no trailing implementation to register with;
        # a @overload set left here is missing its implementation.
        for name, stubs in pending_stubs.items():
            self._require_overload_form(stubs, name, impl=None)
            self.registrar.register_overload_group(stubs)

    @staticmethod
    def _require_overload_form(
        stubs: list[TpyFunction], display_name: str, impl: TpyFunction | None,
    ) -> None:
        """Check a same-named group against its decorator's contract.

        The parser already validated each def alone (a @overload stub is
        bodyless, a @dispatch variant is self-contained); the group-level
        half is that the two decorators never mix under one name, that a
        @overload set ends in an implementation, and that a @dispatch set
        does not.
        """
        form = stubs[0].overload_form
        for stub in stubs[1:]:
            if stub.overload_form is not form:
                raise SemanticError(
                    f"'{display_name}' mixes @overload and @dispatch; a name "
                    f"is either a set of @overload stubs with one trailing "
                    f"implementation or a set of @dispatch variants",
                    stub.loc,
                )
        if form is OverloadForm.OVERLOAD and impl is None:
            raise SemanticError(
                f"@overload stubs for '{display_name}' have no implementation; "
                f"add a trailing `def {stubs[0].name}` without @overload, or "
                f"use @dispatch for variants that are their own implementation",
                stubs[0].loc,
            )
        if form is OverloadForm.DISPATCH and impl is not None:
            raise SemanticError(
                f"@dispatch variants of '{display_name}' cannot be followed by "
                f"a trailing implementation: each variant is its own "
                f"implementation; use @overload stubs if one implementation "
                f"serves every signature",
                impl.loc,
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

        # The implementation builds no ParamInfo (see below), but codegen
        # emits ITS defaults into every specialization, so they need the same
        # gate a plain function's params get.
        self.registrar.validate_ast_param_defaults(impl)

        # The impl skips register_function (it is not callable), so its
        # signature-derived generator yield type is never set there. Body
        # analysis of its `yield`s reads func.generator_yield_type; set it here.
        if impl.is_generator and impl.generator_yield_type is None:
            self.registrar._set_generator_yield_type(impl)

        # The implementation is NOT registered in the namespace/registry --
        # callers resolve against stubs only. The body is still analyzed
        # via _analyze_function (which works on the TpyFunction directly).

        # Store the group mapping for codegen
        self.overload_groups[impl] = stubs

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
            alias_type, alias_loc = entry[0], entry[1]
            if not isinstance(alias_type, UnionType):
                continue
            err = validate_recursive_union_paths(
                alias_name, alias_type.members, type_params=entry[2])
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
        for name, entry in module.type_aliases.items():
            typ, loc = entry[0], entry[1]
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

            # Tag union aliases in this cycle as recursive. A *generic* alias
            # reaching this point is recursive transitively (through a record
            # or another alias) rather than via a direct self-ref -- direct
            # generic self-refs are tagged at parse-resolution and excluded
            # from the cycle graph above. v1 supports only direct identity
            # recursion, so reject the transitive case with a clear diagnostic.
            for alias_name in cycle.alias_names:
                entry = module.type_aliases.get(alias_name)
                if entry is not None and entry[2]:
                    others = [n for n in cycle.path if n != alias_name]
                    through = f" (through {', '.join(others)})" if others else ""
                    raise SemanticError(
                        f"alias '{alias_name}' participates in a type "
                        f"cycle{through}; mutual recursion across generic "
                        f"aliases is not supported in v1",
                        alias_locs.get(alias_name),
                    )
                module.recursive_union_names.add(alias_name)

    def _register_union_wrappers(self, module: TpyModule) -> None:
        """Register this module's recursive union aliases in the compiler-wide
        `union_wrapper_index`. The index is keyed by the canonical member tuple
        (and, when None is a direct member, also by the non-None subset) so a
        narrowed-by-None type still resolves to the same wrapper.

        Sema is the authoritative source: each module's pass writes its own
        entries with `setdefault` (idempotent), accumulating across the
        compilation. Downstream lookups go through `UnionType.wrapper_info()`
        / `UnionType.needs_wrapper()`.
        """
        if not module.recursive_union_names:
            return
        compiler = get_current_compiler()
        if compiler is None:
            return
        index = compiler.union_wrapper_index
        # Use the codegen-flavor (file-derived) name -- the entry point's
        # ctx.module_name is "__main__" for runtime semantics, but
        # codegen filters `info.origin == self.module_name` against the
        # file name. They must agree.
        module_name = self.ctx.cpp_module_name
        for alias_name in module.recursive_union_names:
            entry = module.type_aliases.get(alias_name)
            if entry is None:
                continue
            typ = entry[0]
            if not isinstance(typ, UnionType):
                continue
            # Generic recursive aliases are keyed by their on-type alias_info
            # (RecursiveAliasInstanceType), not the member-tuple index -- their
            # members carry unbound TypeParamRefs that must not leak into the
            # concrete-union index.
            if entry[2]:
                continue
            info = RecursiveUnionInfo(
                name=alias_name, full_members=typ.members, origin=module_name,
            )
            index.setdefault(typ.members, info)
            non_none = tuple(m for m in typ.members if not is_void_like_type(m))
            if len(non_none) < len(typ.members):
                index.setdefault(non_none, info)

    def _validate_record_method_linkage(self, module: TpyModule) -> None:
        """Check per-record linkage rules against each method:
        @native classes require stub bodies; regular classes disallow
        stub bodies (except @overload stubs) and @native("...") decorators.

        Runs after `parse.resolve_refs` so base-resolution
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
        This pass converts that back to OptionalType(AliasRef("Expr")) by
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
                # Generic recursive aliases have parameterized use sites
                # (RecursiveAliasInstanceType), never a bare expanded union, so
                # the member-set reverse map does not apply -- and their members
                # carry TypeParamRefs that would mis-key it.
                if entry[2]:
                    continue
                typ = entry[0]  # entry[0] is the body type
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
            return OptionalType(AliasRef(alias_name, module=self.ctx.module_name))

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
        # Exclude generic aliases from the substitution pool: their bodies
        # contain unbound TypeParamRefs that would leak into annotations if
        # _resolve_alias rewrote a bare NominalType(name) placeholder.
        # Generic alias use sites go through the parse-resolution path
        # (`_resolve_generic_alias_use`) which substitutes correctly with
        # the supplied type args.
        aliases = {
            n: info.body
            for n, info in self.ctx.registry.type_aliases.items()
            if not info.type_params
        }
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

    def _alias_lookup_for_finalize(
        self, name: str, defining_module: 'str | None' = None,
    ) -> 'tuple[TypeAliasInfo, str, str] | None':
        """Resolve a referenced alias name to (info, qname, short_name),
        covering both local and imported aliases, or None if not an alias.

        The qname is the defining module + the alias's original name --
        collision-proof across modules that define same-short-named aliases,
        and consistent whether the alias is referenced from its own module or
        an importer (both resolve to the defining module's name).

        A qualified reference (`treelib.Tree[int]`) carries the defining module
        on the AliasRef; that resolves against the defining module's table
        *first*, so it never mis-resolves to a same-short-named local/imported
        alias (`import other; other.Tree` while a local `Tree` exists). Bare
        references pass `defining_module=current`, which falls through to the
        imported-first / local path below.

        Imported aliases are checked next: `from m import Tree` also registers
        a local entry, so a local-first lookup would mis-stamp the importer's
        module name onto the qname (diverging from the defining module's body,
        which the importer inherits via the shared alias_info)."""
        if defining_module is not None and defining_module != self.ctx.module_name:
            mi = self.ctx.registry.modules.get(defining_module)
            qinfo = mi.type_aliases.get(name) if mi is not None else None
            if qinfo is not None:
                return qinfo, f"{defining_module}.{name}", name
        imp = self.ctx.registry.imported_type_alias_info.get(name)
        if imp is not None:
            decl_mod, orig = imp
            mod_info = self.ctx.registry.modules.get(decl_mod)
            iinfo = mod_info.type_aliases.get(orig) if mod_info is not None else None
            if iinfo is None:
                iinfo = self.ctx.registry.get_type_alias_info(name)
            if iinfo is not None:
                return iinfo, f"{decl_mod}.{orig}", orig
        info = self.ctx.registry.get_type_alias_info(name)
        if info is not None:
            return info, f"{self.ctx.module_name}.{name}", name
        return None

    def _finalize_alias_refs(self, typ: TpyType) -> TpyType:
        """Rewrite a generic-recursive `AliasRef` placeholder into the
        semantic `RecursiveAliasInstanceType`, recursing through inner types.

        Non-generic recursive aliases keep their bare `AliasRef` self-ref
        (their use sites stay UnionType + union_wrapper_index)."""
        if isinstance(typ, AliasRef):
            looked = self._alias_lookup_for_finalize(typ.name, typ.module)
            if (looked is not None and looked[0].type_params
                    and looked[0].is_recursive):
                info, qname, _short = looked
                new_args = tuple(
                    self._finalize_alias_refs(a) if isinstance(a, TpyType) else a
                    for a in typ.args
                )
                # Identity AND the C++ render key are the qname: codegen keys
                # `recursive_alias_cpp_names` by qname, so a same-module alias
                # and an imported same-short-named one never collide on
                # rendering. `typ.name` is carried only as the local display
                # short (`_short()` / `__str__` / same-module bare fallback).
                return RecursiveAliasInstanceType(qname, new_args, typ.name, info)
        return typ.map_inner_types(self._finalize_alias_refs)

    def _finalize_generic_recursive_aliases(self, module: TpyModule) -> None:
        """Convert the parser's generic-recursive `AliasRef` placeholders into
        `RecursiveAliasInstanceType` across alias bodies and all annotation
        slots, using the sema-registry alias_info (parse-resolve runs against
        a separate registry and cannot mint the semantic type).

        Single conversion point so use-site instances and recursive children
        share one alias_info source. Runs both for locally-defined generic
        recursive aliases (whose bodies get finalized) and for modules that
        merely *use* an imported one (use sites still need conversion). See
        docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md."""
        generic_rec = {
            n for n in module.recursive_union_names
            if (gi := self.ctx.registry.get_type_alias_info(n)) is not None
            and gi.type_params
        }
        imports_generic_rec = any(
            (lk := self._alias_lookup_for_finalize(n)) is not None
            and lk[0].type_params and lk[0].is_recursive
            for n in self.ctx.registry.imported_type_alias_info
        )
        # The qualified `import m; m.Tree[...]` form binds only the module name,
        # so it leaves no `imported_type_alias_info` entry -- detect it via the
        # imported user modules that define a generic recursive alias, else the
        # pass would skip and the use-site AliasRef would never be finalized.
        qualified_generic_rec = any(
            (mi := self.ctx.registry.modules.get(m)) is not None
            and any((ai := mi.type_aliases.get(n)) is not None and ai.type_params
                    for n in mi.recursive_union_names)
            for m in module.user_module_imports
        )
        if not generic_rec and not imports_generic_rec and not qualified_generic_rec:
            return
        # Alias bodies: the registered TypeAliasInfo (read by alternatives
        # expansion) and the cross-phase tuple (read by codegen + export).
        for n in generic_rec:
            info = self.ctx.registry.get_type_alias_info(n)
            info.body = self._finalize_alias_refs(info.body)
            entry = module.type_aliases.get(n)
            if entry is not None:
                module.type_aliases[n] = (
                    (self._finalize_alias_refs(entry[0]),) + tuple(entry[1:])
                )
        # Use sites in annotations.
        for func in module.functions:
            self._finalize_func_aliases(func)
        for record in module.all_records():
            for f in record.fields:
                f.type = self._finalize_alias_refs(f.type)
            for method in record.methods:
                self._finalize_func_aliases(method)
            # RecordInfo carries registration-time snapshots of the ctor and
            # method signatures, built before this pass, so their alias
            # placeholders are stale and must be finalized here too. (Free
            # functions register after this pass, so their FunctionInfo is
            # already finalized; protocol signatures are finalized below.)
            info = self.ctx.registry.get_record(record.name)
            if info is not None:
                if info.init_params:
                    info.init_params = [
                        (n, self._finalize_alias_refs(t), d)
                        for (n, t, d) in info.init_params
                    ]
                for overloads in info.methods.values():
                    for fi in overloads:
                        if fi.return_type is not None:
                            fi.return_type = self._finalize_alias_refs(fi.return_type)
                        for pi in fi.params:
                            pi.type = self._finalize_alias_refs(pi.type)
        # Protocol method signatures are another registration-time snapshot:
        # the registry ProtocolInfo (shared with the TypeDef payload) is read
        # by conformance + protocol-call dispatch; the AST copy is separate.
        for protocol in module.protocols:
            self._finalize_method_signatures(protocol.methods)
            pinfo = self.ctx.registry.scan_by_short_name(protocol.name)
            if pinfo is not None:
                self._finalize_method_signatures(pinfo.methods)
        if module.top_level_stmts:
            self._finalize_stmts_aliases(module.top_level_stmts)

    def _finalize_method_signatures(self, methods: list[MethodSignature]) -> None:
        for msig in methods:
            for i, (name, typ) in enumerate(msig.params):
                new = self._finalize_alias_refs(typ)
                if new is not typ:
                    msig.params[i] = (name, new)
            if msig.return_type is not None:
                msig.return_type = self._finalize_alias_refs(msig.return_type)

    def _finalize_func_aliases(self, func: TpyFunction) -> None:
        if func.return_type is not None:
            func.return_type = self._finalize_alias_refs(func.return_type)
        for i, (name, typ) in enumerate(func.params):
            new = self._finalize_alias_refs(typ)
            if new is not typ:
                func.params[i] = (name, new)
        if func.vararg_type is not None:
            func.vararg_type = self._finalize_alias_refs(func.vararg_type)
        if func.kwarg_type is not None:
            func.kwarg_type = self._finalize_alias_refs(func.kwarg_type)
        if func.body:
            self._finalize_stmts_aliases(func.body)

    def _finalize_stmts_aliases(self, stmts: 'list') -> None:
        """Convert generic-recursive AliasRef placeholders in local variable
        annotations. Local annotations are resolved at parse-resolution (via
        resolve_refs `_walk_body`), so they carry the same placeholders as
        signatures and must be finalized too."""
        for stmt in stmts:
            if isinstance(stmt, TpyVarDecl) and stmt.type is not None:
                stmt.type = self._finalize_alias_refs(stmt.type)
            if isinstance(stmt, TpyNestedDef):
                self._finalize_func_aliases(stmt.func)
                continue
            for body in stmt.sub_bodies():
                self._finalize_stmts_aliases(body)

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
            # instead. A bodied @dispatch variant needs body analysis just
            # like a regular method.
            if method.is_overload_stub and method.is_stub:
                continue

            self.ctx.reset_function_tracking()
            own_copy_mark = self.ctx.own_copy_mark()
            self.ctx.func.current_function = method
            self.ctx.func.body_root = method
            # Resolve return type (sets is_protocol for cross-module imports)
            method.return_type = make_ref(self.type_ops.resolve_type(method.return_type))
            scope = Scope(parent=self.ctx.global_scope)
            self.ctx.func.current_scope = scope

            local_ns = Namespace(parent=self.ctx.global_ns)
            self.ctx.func.current_ns = local_ns
            if not method.is_staticmethod:
                self_named = receiver_self_type(record, self.ctx.registry)
                self_type = self._normalize_param_type(self_named, method.is_readonly)
                scope.define("self", self_type)
                self.ctx.declare_local("self", scope.depth, block_depth=0)
                self.ctx.func.definitely_assigned.add("self")
                local_ns.bind_variable("self", self_type)
            elif method.is_classmethod:
                # `cls` is a second name for the defining record, not a value:
                # a RECORD binding, so every consumer that already handles a
                # bare class name (constructor calls, class constants,
                # ClassVar writes, static dispatch) resolves it unchanged.
                # On an enum's companion it names the ENUM the same way
                # (`cls.Red`, `cls(1)`, `cls[s]`, `cls.other()`).
                if record.enum_companion_of is not None:
                    local_ns.bind(NameBinding(
                        kind=BindingKind.ENUM, name="cls",
                        enum_type=receiver_self_type(record, self.ctx.registry),
                        is_sema_alias=True))
                elif (cls_info := self.ctx.registry.get_record(record.name)) is not None:
                    local_ns.bind(NameBinding(
                        kind=BindingKind.RECORD, name="cls", record_info=cls_info,
                        is_sema_alias=True))

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

            # Finalize nested def escape analysis (same as _analyze_function)
            self._finalize_nested_def_escapes()
            self._warn_stale_value_captures()

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

            if method.name == "__init__":
                self._require_leading_base_init_calls(method, record)
                self._check_init_field_assignments(method, record)
                self._require_parent_init_call(method, record)

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

            if method.is_generator or method.is_async:
                self._collect_generator_locals(method, local_ns, exclude_self=True)
            self.compat.drain_deferred_escape_checks()
            self._enqueue_generic_yield_settle()

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
                    self._stamp_frame_materials(method, method_fi)
                    stamp_may_interrupt(self.ctx, method, method_fi)
                    param_list = [pname for pname, _ in method.params]
                    direct = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.func.current_mutated_param_names
                    )
                    method_fi.direct_mutated_params = direct
                    method_fi.direct_self_mutated = self.ctx.func.current_self_mutated
                    method_fi.call_edges = list(self.ctx.func.current_call_edges)
                    method_fi.mutated_params = direct
                    method_fi.representational_type_params = frozenset(self.ctx.func.current_representational_params)
                    (method_fi.own_copy_obligations,
                     method_fi.own_copy_forwards) = self.ctx.own_copy_drain(own_copy_mark)
                    # Structural mutation facts (append/insert/clear/del/etc.)
                    direct_struct = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.func.current_struct_mutated_param_names
                    )
                    if self.ctx.func.current_self_struct_mutated:
                        direct_struct = direct_struct | frozenset({-1})
                    method_fi.direct_structural_mutated_params = direct_struct
                    method_fi.structural_mutated_params = direct_struct
                    direct_elem = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.func.current_elem_mutated_param_names
                    )
                    if "self" in self.ctx.func.current_elem_mutated_param_names:
                        direct_elem = direct_elem | frozenset({-1})
                    method_fi.direct_elem_mutated_params = direct_elem
                    method_fi.elem_mutated_params = direct_elem
                    # 8b: Return borrow facts
                    returned = frozenset(
                        i for i, pname in enumerate(param_list)
                        if pname in self.ctx.func.current_returned_param_names
                    )
                    if "self" in self.ctx.func.current_returned_param_names:
                        returned = returned | frozenset([-1])
                    # Generator and async methods: union the frame's param
                    # captures, mirroring the free-function finalize. The
                    # frame's self reference is deliberately not represented
                    # as -1 (it would block readonly inference); see BUGS.md.
                    if method.is_generator or method.is_async:
                        returned = returned | TypeRegistrar.generator_borrow_param_indices(
                            [p.type for p in method_fi.params])
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
        # Parent-initializer calls all sit in the leading run (already
        # enforced), so the field inits start where that run ends.
        init_section_fields: set[str] = set()
        split_idx = len(method.body)
        leading_end = init_leading_run_end(method.body)
        if any(self._base_init_sets_message(record_info, stmt)
               for stmt in method.body[:leading_end]):
            init_section_fields.add(qnames.EXCEPTION_MESSAGE_FIELD)

        for i in range(leading_end, len(method.body)):
            stmt = method.body[i]
            if is_init_trivia(stmt):
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
                if uninit and expr_contains_self_method_call(stmt.value):
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
                if record.type_params and contains_type_param(field_type):
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

    def _base_init_sets_message(self, record_info: RecordInfo,
                                stmt: TpyStmt) -> bool:
        """A leading parent-initializer call that initializes the class's own
        `message` field (`TypeRegistry.base_init_message_arg`)."""
        if not is_base_init_call(stmt):
            return False
        assert isinstance(stmt, TpyExprStmt)
        call = stmt.expr
        assert isinstance(call, TpyMethodCall)
        return self.ctx.registry.base_init_message_arg(record_info, call) is not None

    def _collect_all_fields(self, record_info: RecordInfo) -> dict[str, FieldInfo]:
        """Collect all fields from a record and its ancestors, walking MRO base-first.

        Child fields override ancestor fields with the same name (last-write-wins
        in dict semantics, consistent with Python attribute shadowing).
        """
        result: dict[str, FieldInfo] = {}
        for anc_rec in self.ctx.registry.iter_field_ancestors(record_info, reverse=True):
            for f in anc_rec.fields:
                result[f.name] = f
        for f in record_info.fields:
            result[f.name] = f
        return result

    def _require_leading_base_init_calls(
        self, method: TpyFunction, record: TpyRecord,
    ) -> None:
        """Reject a parent-initializer call (`super().__init__(...)` or
        `Base.__init__(self, ...)`) that the C++ member initializer list
        cannot run with its CPython meaning: one outside the leading run of
        `__init__`, one whose initializer no direct base runs, and a second
        call initializing the same direct base (`base_init_direct_base`, the
        base each call constructs).

        C++ runs base constructors from the member initializer list, ahead of
        every body statement, once per direct base, while CPython runs each
        call in place -- so only a call nothing observable precedes keeps its
        meaning (a docstring or `pass` precedes nothing observable; a
        multi-base `__init__` calls one base after another).
        """
        end = init_leading_run_end(method.body)
        leading: IdentitySet[TpyExpr] = IdentitySet(
            stmt.expr for stmt in method.body[:end]
            if isinstance(stmt, TpyExprStmt) and is_base_init_call(stmt))
        registry = self.ctx.registry
        record_info = registry.get_record(record.name)
        initialized: IdentityMap[RecordInfo, str] = IdentityMap()
        for call, spelling in self.ctx.func.base_init_calls:
            if call not in leading:
                raise self._error(
                    f"{spelling} must be the first statement in __init__", call)
            parent_type = call.super_parent_type or call.unbound_self_parent_type
            target = (registry.get_record_for_type(parent_type)
                      if parent_type is not None else None)
            if target is None or record_info is None:
                continue
            base_type = call.base_init_direct_base
            base = (registry.get_record_for_type(base_type)
                    if base_type is not None else None)
            if base is None:
                raise self._error(
                    self._not_direct_base_message(record_info, target), call)
            first = initialized.get(base)
            if first is not None:
                raise self._error(
                    f"'{base.display_name}' is initialized twice in "
                    f"'{record_info.display_name}.__init__' ('{first}' "
                    f"already initializes it); a base is constructed "
                    f"exactly once", call)
            initialized[base] = spelling

    def _not_direct_base_message(self, record_info: RecordInfo,
                                 target: RecordInfo) -> str:
        """The diagnostic for a parent-initializer call naming `target`, an
        ancestor no direct base of `record_info` constructs with `target`'s
        `__init__`. The hint names the direct base that leads to `target`,
        and `super().__init__(...)` only when it initializes that same base."""
        registry = self.ctx.registry
        via = next((rec for rec in (registry.get_record_for_type(p)
                                    for p in record_info.parents
                                    if isinstance(p, NominalType))
                    if rec is not None
                    and registry.is_subclass_of_record(rec, target)), None)
        head = (f"'{target.display_name}' is not a direct base of "
                f"'{record_info.display_name}'")
        if via is None:
            return head
        via_call = f"'{via.display_name}.__init__(self, ...)'"
        super_base = registry.super_init_direct_base(record_info)
        if (super_base is not None
                and registry.get_record_for_type(super_base) is via):
            return f"{head}; call 'super().__init__(...)' or {via_call}"
        return f"{head}; call {via_call}"

    def _require_parent_init_call(
        self, method: TpyFunction, record: TpyRecord,
    ) -> None:
        """Check a single-base child `__init__` that skips its parent's
        initializer (`super().__init__(...)` / `Base.__init__(self, ...)`,
        mirroring `is_base_init_call`) against what it owes the parent
        (`TypeRegistry.base_init_duty`, the rule
        `validate_multi_base_init_calls` applies per base of a multi-base
        class): skipping a CALL base is a warning, and any skipped base needs
        a C++ default constructor, or the child's constructor could not be
        built.
        """
        record_info = self.ctx.registry.get_record(record.name)
        if record_info is None or record_info.is_native:
            return
        if len(record_info.parents) != 1:
            return
        skipped = skipped_base_inits(self.ctx.registry, record_info, method)
        if not skipped:
            return
        [(parent, duty)] = skipped
        if duty is BaseInitDuty.CALL:
            warn = skipped_base_init_warning(
                self.ctx.registry, record_info, parent, method, single_base=True)
            if warn is not None:
                self.ctx.warning(warn, method)
                return
        msg = skipped_base_ctor_error(
            self.ctx.registry, record.name, parent, method, single_base=True)
        if msg is not None:
            raise self._error(msg, method)

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

    def _analyze_top_level(self, stmts: list[TpyStmt],
                           function_globals: set[str]) -> None:
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
        self.stmts._warn_scalar_type_shadows(None, set(), self.top_level_scan_result)
        # Last-use analysis for auto-move (shared with codegen)
        self.ctx.func.closure_pinned = closure_pinned_names(stmts)
        self.ctx.all_last_uses |= analyze_last_uses(
            stmts, liveness_alias_sources(self.top_level_scan_result),
            pinned=self.ctx.func.closure_pinned)
        self.ctx.finally_return_candidates |= collect_finally_return_candidates(stmts)
        self.ctx.func.current_reassigned_vars = self.top_level_scan_result.reassigned.copy()
        self.ctx.func.current_fresh_ctor_locals = set()
        self.ctx.func.current_lvalue_reassigned = self.top_level_scan_result.lvalue_reassigned.copy()
        self.ctx.func.current_aug_assigned_vars = self.top_level_scan_result.aug_assigned.copy()
        self.ctx.func.current_alias_sources = dict(self.top_level_scan_result.alias_sources)
        self.ctx.func.current_chain_alias_sources = dict(
            self.top_level_scan_result.chain_alias_sources)
        self.ctx.func.in_place_writes = collect_in_place_writes(
            stmts, self.ctx.write_summaries)

        for stmt in stmts:
            # A parser-minted comprehension temp is an init-scope local, not a
            # module slot; everything else at depth 0 declares one.
            self.ctx.current_module_stmt = (
                None if isinstance(stmt, TpyVarDecl) and stmt.module_init_local
                else stmt)
            self.stmts.analyze_stmt(stmt)
        self.ctx.current_module_stmt = None
        self.deduction.resolve_all()
        self.compat.drain_deferred_escape_checks()
        # A global some function rebinds through `global` can change between
        # two module-level statements: foreign storage for the replay.
        decide_rebind_storage(self.ctx, stmts, always_foreign=function_globals)

        if self.ctx.func.hoisted_vars:
            self.top_level_hoisted_vars = self.ctx.func.hoisted_vars.copy()
        if self.ctx.func.move_through_vars:
            self.top_level_move_through_vars = self.ctx.func.move_through_vars.copy()
        self.if_branch_decls.update(self.ctx.if_branch_decls)
        self.arm_branch_decls.update(self.ctx.arm_branch_decls)
        self.function_hoisted_vars.update(self.ctx.nested_def_hoisted_vars)

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
        # Install into the attribute table so use-site checks that read
        # `module_attributes` (e.g. `is_indirect_name`, `pointer_globals`)
        # find non-value imports and emit the right deref / pointer
        # qualification. Without this, a non-value-typed implicit-stdlib
        # variable would lose its `(*x)` indirection at use sites.
        install_binding(
            self.ctx.module_attributes, local_name,
            SymbolKind.VARIABLE, var_info.type,
            defining_module=module_name, canonical_name=original_name,
        )
        return True

    def _register_tpy_type_alias(self, original_name: str, local_name: str) -> None:
        """Register a single tpy type alias from the compiled tpy module_info."""
        # Compile-time-only types (e.g. FStr) take priority
        self.registrar._register_compile_time_type_alias(local_name, "tpy", original_name)

        # AST-level type aliases (e.g. float64 = float)
        tpy_info = self.ctx.registry.get_module("tpy")
        if not tpy_info or not tpy_info.type_aliases:
            return
        alias_info = tpy_info.type_aliases.get(original_name)
        if alias_info is not None:
            self.ctx.registry.register_type_alias(local_name, alias_info.body,
                                                  imported_from=("tpy", original_name),
                                                  info=alias_info)
            if local_name != original_name:
                self.ctx.registry.register_type_alias(original_name, alias_info.body,
                                                      info=alias_info)

    def _register_all_tpy_type_aliases(self) -> None:
        """Register all tpy type aliases (for bare 'import tpy' and star imports)."""
        tpy_info = self.ctx.registry.get_module("tpy")
        if not tpy_info or not tpy_info.type_aliases:
            return
        for name, alias_info in tpy_info.type_aliases.items():
            self.ctx.registry.register_type_alias(name, alias_info.body,
                                                  imported_from=("tpy", name),
                                                  info=alias_info)

    def _bind_star_reexport(self, original_name: str, local_name: str) -> None:
        """Try to bind a star-imported re-export from a known module.

        When 'from utils import *' brings in a name like int32 that utils
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
        # Fallback: tpy type aliases (e.g. int32, float64)
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
        the same registry/global_ns/attribute-table state the per-kind
        branches in `_register_user_module_import` would, so downstream
        consumers see the import. Returns True on success.
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
            # Install with the chain-flattened attribution: defining_module
            # is the *ultimate* definer (record_info.defining_module),
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
            # Install with chain-flattened attribution: defining_module
            # is the *ultimate* definer (protocol_info.module), canonical
            # name is the protocol's name in that module. Mirrors the
            # records / enums / functions paths above; the prior
            # `defining_module=module_name` (immediate import source)
            # caused chain re-exports through `pkg/__init__.py` to
            # qualify protocols via the intermediate package, which
            # broke `RefAdapter` instantiation when the package's own
            # `using` was suppressed for sibling-cycle reasons.
            ult_mod = protocol_info.module if protocol_info.module else module_name
            install_binding(
                self.ctx.module_attributes, local_name,
                protocol_kind_for(protocol_info.is_dynamic), protocol_info,
                defining_module=ult_mod, canonical_name=protocol_info.name,
            )
            return True

        # Check for type alias
        if module_info.type_aliases and original_name in module_info.type_aliases:
            alias_info = module_info.type_aliases[original_name]
            typ = alias_info.body
            self.ctx.registry.register_type_alias(local_name, typ,
                                                  imported_from=(module_name, original_name),
                                                  info=alias_info)
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
            # Install with chain-flattened attribution: defining_module
            # is the ultimate declaring module (EnumInfo.module_name),
            # canonical_name is the enum's name in that module.
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
            # Install with the chain-flattened ultimate defining module
            # so the consumer's `.hpp` renders the definer's qname
            # directly (matters when the immediate source is a
            # native_module facade with no .hpp).
            ult_mod, ult_name = resolve_definer(
                self.ctx.registry, module_name, original_name,
                SymbolKind.VARIABLE)
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
        # re-imported names like int32 from tpy). Skip those so the caller
        # doesn't bind them as coming from this module.
        if from_star_import:
            return False

        line = self.ctx.user_module_import_lines.get(module_name)
        raise self.ctx.error_from_loc(
            f"'{original_name}' not found in module '{module_name}'",
            SourceLocation(line=line) if line else None)

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

    def _expand_function_macros(self, module: TpyModule) -> None:
        """Pass 5.5: run @function_macro decorators on record-method and
        free-function bodies (mirrors `_expand_builder_traces`' walk).

        Iterates unconditionally (each function with no pending macros is a
        cheap no-op) so an unresolved macro decorator errors rather than
        being silently dropped. Methods run first so a macro can't observe
        a module where some free functions are expanded and methods aren't.
        """
        for record in module.all_records():
            for method in record.methods:
                run_function_macros(method, self.ctx, self.ctx.module_name,
                                    module_data=module.macro_data,
                                    record=record)
        for func in list(module.functions):
            run_function_macros(func, self.ctx, self.ctx.module_name,
                                module_data=module.macro_data)

    def _skip_body_analysis(self, func: TpyFunction) -> bool:
        """Functions Pass 7 does not analyze, so their expr_types stay empty:
        bodyless @overload stubs (handled via their trailing implementation)
        and @inline functions (inlined at call sites, not emitted standalone).
        """
        return ((func.is_inline and not func.is_stub)
                or (func.is_overload_stub and func.is_stub)
                or func.is_genexpr)

    def _drain_deferred_sema_macros(self, module: TpyModule) -> None:
        """Post-pass-7: run callbacks a macro deferred via
        ctx.defer_until_sema_complete. They run here -- after every body in
        the module is type-checked -- so they can read inferred expression
        types (absent at pass 5.5 when the macro itself ran). Skip bodies
        passes 6/7 never analyzed (@overload stubs, @inline), so a callback
        never reads empty expr_types.
        """
        for record in module.all_records():
            for method in record.methods:
                if self._skip_body_analysis(method):
                    continue
                run_deferred_sema_macros(method, self.ctx,
                                         self.ctx.module_name,
                                         module_data=module.macro_data,
                                         record=record)
        for func in list(module.functions):
            if self._skip_body_analysis(func):
                continue
            run_deferred_sema_macros(func, self.ctx, self.ctx.module_name,
                                     module_data=module.macro_data)

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
            # A summary of the replaced list would go stale.
            assert func.body not in self.ctx.write_summaries
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
            # A summary of the rewritten list would go stale.
            assert stmts not in self.ctx.write_summaries
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

            # Macro-dep symbols flow into the consumer's per-module
            # attribute table as imports so the re-export emit picks
            # them up (the macro-expanded code references these names,
            # so the consumer's `.hpp` needs to alias them in its
            # namespace just like any other `from M import X`).
            if module_info.records:
                for name, record_info in module_info.records.items():
                    if name_filter is not None and name not in name_filter:
                        continue
                    if self.ctx.registry.get_record(name) is None:
                        self.ctx.registry.register_record(record_info, name)
                    self.ctx.macro_ns.bind_imported_name(name, dep_mod_name, name)
                    self.ctx.imported_names.setdefault(name, (dep_mod_name, name))
                    install_binding(
                        self.ctx.module_attributes, name,
                        SymbolKind.RECORD, record_info,
                        defining_module=(record_info.defining_module or dep_mod_name),
                        canonical_name=record_info.name,
                    )

            if module_info.functions:
                for name, func_infos in module_info.functions.items():
                    if name_filter is not None and name not in name_filter:
                        continue
                    is_special = func_infos and func_infos[0].special_handling
                    if not is_special:
                        if self.ctx.registry.get_function(name) is None:
                            self.ctx.registry.register_function_group(name, func_infos)
                    self.ctx.macro_ns.bind_imported_name(name, dep_mod_name, name)
                    self.ctx.imported_names.setdefault(name, (dep_mod_name, name))
                    ult_mod = (func_infos[0].originating_module
                               if func_infos else None) or dep_mod_name
                    ult_name = func_infos[0].name if func_infos else name
                    install_binding(
                        self.ctx.module_attributes, name,
                        SymbolKind.FUNCTION, func_infos,
                        defining_module=ult_mod, canonical_name=ult_name,
                    )

            if module_info.enums:
                for name, enum_type in module_info.enums.items():
                    if name_filter is not None and name not in name_filter:
                        continue
                    if self.ctx.registry.get_enum(name) is None:
                        self.ctx.registry.register_enum(enum_type, name)
                    self.ctx.macro_ns.bind_enum(enum_type, name=name)
                    self.ctx.imported_names.setdefault(name, (dep_mod_name, name))
                    einfo = enum_info_of(enum_type)
                    ult_mod = (einfo.module_name if einfo and einfo.module_name
                               else dep_mod_name)
                    install_binding(
                        self.ctx.module_attributes, name,
                        SymbolKind.ENUM, enum_type,
                        defining_module=ult_mod, canonical_name=enum_type.name,
                    )
