"""Builder-trace macro expansion sub-pass.

Function-body and record-method-body expansion runs in pass 5.5
(``SemanticAnalyzer._expand_builder_traces``), after class-constants
analysis (pass 5) and before record-method-body analysis (pass 6), so
synthesized records / functions are first-class participants in passes
6 and 7. Top-level expansion runs inside ``_analyze_top_level`` (pass
4 is itself the body-analysis pass for module-level code, so expansion
has to run there). Detects @builder_macro constructor calls, walks the
body linearly, dispatches builder methods to user handlers, and splices
synthesized declarations + a rewritten call site back into the body.
See docs/MACRO_DESIGN.md (Phase 7) for the design.

Phase A (current): the happy path -- ctor, methods, terminal -- without
the full v1 hard-error rules around tracked-symbol misuse, conditionals,
loops, or multiple terminals. Those land in phase B.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from ..parse.nodes import (
    TpyAssign, TpyCall, TpyMethodCall, TpyExprStmt, TpyVarDecl,
    TpyName, TpyStmt, TpyExpr, TpyFunction, TpyRecord, TpyLambda,
    TpyNestedDef,
    TpyStrLiteral, TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral,
    TpyNoneLiteral, TpyUnaryOp,
)
from ..typesys import (
    FieldInfo, NominalType, OwnType, TpyType, VOID, STR, STRVIEW, BOOL, BIGINT,
    FLOAT, FLOAT32,
    INT8, INT16, INT32, INT64, UINT8, UINT16, UINT32, UINT64,
    make_list,
)
from ..diagnostics import SemanticError
from ..symbol_binding import SymbolKind, is_kind, walk_attribute_chain
from ..macro_api import (
    MacroArg, MacroArgs, BuilderContext, TypeInfo, MacroError,
)

if TYPE_CHECKING:
    from .context import SemanticContext
    from .registration import TypeRegistrar


# Synthesized records and functions get a private prefix so they aren't
# treated as user-exported names. Matches the compiler-internal naming
# convention used elsewhere (e.g. __tpy_init, __tpy_main).
_SYNTH_PREFIX = "__tpy_builder_"


# Kind tags for handlers discovered on a @builder_macro class.
_KIND_METHOD = "method"
_KIND_RETURNS = "returns"
_KIND_TERMINAL = "terminal"


@dataclass
class _HandlerSpec:
    fn: Callable
    kind: str
    child_class: type | None = None  # only for kind == _KIND_RETURNS


@dataclass
class _TrackedSymbol:
    """A live tracked symbol bound by a @builder_macro ctor or sub-builder."""
    var_name: str
    state: object
    macro_qname: str
    handlers: dict[str, _HandlerSpec]
    # var_name of the @builder_returns parent that spawned this symbol,
    # or None for top-level (ctor-bound) symbols. Used to cascade
    # close-on-terminal: when the root @builder_terminal fires, every
    # descendant sub-builder is closed too. Sub-builders don't have
    # their own terminal -- they contribute to the root's terminal.
    parent_var: str | None = None


@dataclass
class _PendingReplacement:
    fn_name: str
    args: MacroArgs


def _scan_handlers(macro_cls: type) -> dict[str, _HandlerSpec]:
    """Collect @builder_method / @builder_returns / @builder_terminal
    handlers declared on a builder class.
    """
    out: dict[str, _HandlerSpec] = {}
    for attr in dir(macro_cls):
        if attr.startswith("__") and attr.endswith("__"):
            continue
        fn = getattr(macro_cls, attr, None)
        if not callable(fn):
            continue
        if getattr(fn, "_is_builder_terminal", False):
            out[attr] = _HandlerSpec(fn, _KIND_TERMINAL)
        elif getattr(fn, "_is_builder_returns", False):
            child = getattr(fn, "_builder_child_class", None)
            out[attr] = _HandlerSpec(fn, _KIND_RETURNS, child_class=child)
        elif getattr(fn, "_is_builder_method", False):
            out[attr] = _HandlerSpec(fn, _KIND_METHOD)
    return out


def _macro_arg_from_expr(expr: TpyExpr) -> MacroArg:
    """Wrap an expression as a MacroArg with a best-effort literal type.

    Builder-trace runs before body analysis, so resolved types aren't
    on expressions yet. We synthesize a minimal TypeInfo from the literal
    kind when possible. Macros generally only need MacroArg.expr (for
    literal evaluation); the type field is provided so kwarg_macroarg
    callers see something sensible for the common cases.
    """
    return MacroArg(expr=expr, type=_typeinfo_from_literal(expr))


# Bare-name builtins recognized as ``type=`` references in builder
# macros. Mapping these to their real TpyType lets macros use
# TypeInfo's category helpers (``is_int``, ``is_bigint``, ``is_float``,
# ...) instead of name-string comparisons. User-defined types still
# fall through to a placeholder until full type-name resolution lands
# in builder-trace.
_BUILTIN_TYPE_NAMES: dict[str, TpyType] = {
    "str": STR, "int": BIGINT, "float": FLOAT, "bool": BOOL,
    "Int8": INT8, "Int16": INT16, "Int32": INT32, "Int64": INT64,
    "UInt8": UINT8, "UInt16": UINT16, "UInt32": UINT32, "UInt64": UINT64,
    "Float32": FLOAT32,
}


def _typeinfo_from_literal(expr: TpyExpr) -> TypeInfo:
    if isinstance(expr, TpyBoolLiteral):
        return TypeInfo.from_tpy_type(BOOL)
    if isinstance(expr, TpyIntLiteral):
        return TypeInfo.from_tpy_type(BIGINT)
    if isinstance(expr, TpyFloatLiteral):
        return TypeInfo.from_tpy_type(FLOAT)
    if isinstance(expr, TpyStrLiteral):
        return TypeInfo.from_tpy_type(STRVIEW)
    if isinstance(expr, TpyNoneLiteral):
        return TypeInfo("None", _tpy_type=None)
    if isinstance(expr, TpyName):
        builtin = _BUILTIN_TYPE_NAMES.get(expr.name)
        if builtin is not None:
            return TypeInfo.from_tpy_type(builtin)
        # User-defined types stay as placeholders until full type-name
        # resolution lands in builder-trace.
        return TypeInfo(expr.name, _tpy_type=None)
    return TypeInfo("?", _tpy_type=None)


def _build_macro_args(args: list[TpyExpr], kwargs: dict[str, TpyExpr]) -> MacroArgs:
    return MacroArgs(
        positional=[_macro_arg_from_expr(a) for a in args],
        kwargs={k: _macro_arg_from_expr(v) for k, v in kwargs.items()},
    )


def _extract_binding(stmt: TpyStmt) -> tuple[str | None, TpyExpr | None]:
    """If ``stmt`` binds a single name to an RHS expression, return
    ``(name, rhs)``. Otherwise return ``(None, None)``.

    Handles both first-time declarations (``TpyVarDecl``) and reassignments
    (``TpyAssign`` with a ``TpyName`` target).
    """
    if isinstance(stmt, TpyVarDecl):
        return stmt.name, stmt.init
    if isinstance(stmt, TpyAssign) and isinstance(stmt.target, TpyName):
        return stmt.target.name, stmt.value
    return None, None


class BuilderTraceExpander:
    """Expand a function body containing @builder_macro constructor calls.

    Construct one expander per body. Call ``expand(body)`` to get a
    rewritten body. The expander mutates ``ctx.registry`` and ``module``
    directly via the registrar to register synthesized declarations.

    No-op when the body contains no builder constructors.
    """

    def __init__(
        self,
        ctx: 'SemanticContext',
        registrar: 'TypeRegistrar',
        module: Any,  # TpyModule -- imported lazily to avoid cycle
        function_being_traced: str,
    ) -> None:
        self.ctx = ctx
        self.registrar = registrar
        self.module = module
        self.function_being_traced = function_being_traced
        self._tracked: dict[str, _TrackedSymbol] = {}
        self._pending_replacement: _PendingReplacement | None = None
        # Trace-rule bookkeeping (Phase B).
        self._all_tracked_names: set[str] = set()
        self._open_traces: dict[str, Any] = {}  # name -> ctor loc

    # ------------------------------------------------------------------
    # Body walk
    # ------------------------------------------------------------------

    def expand(self, body: list[TpyStmt] | None) -> list[TpyStmt] | None:
        if body is None:
            return body
        macro_reg = self.ctx.macro_registry
        if macro_reg is None or not macro_reg.has_builder_macros():
            return body
        new_body: list[TpyStmt] = []
        for stmt in body:
            replacement = self._process_stmt(stmt)
            if replacement is _DROP:
                continue
            if replacement is None:
                new_body.append(stmt)
            else:
                new_body.append(replacement)
        # Trace-rule validation (Phase B). Order matters: leaks fire
        # before "trace never closed" so that a symbol used illegally
        # gets the more specific error rather than a generic missing-
        # terminal one.
        if self._all_tracked_names:
            self._validate_no_tracked_leaks(new_body)
            self._check_no_open_traces()
        return new_body

    def _reject_active_reassignment(self, name: str, loc: Any) -> None:
        """Raise if ``name`` is currently bound as an active tracked
        builder symbol. Shared between ctor binding, ``@builder_returns``
        rebinding, and the post-dispatch fall-through reassignment check.
        Closed names are not rejected -- the trace has finished, the
        name is free to take on any other meaning.
        """
        if name in self._tracked:
            raise SemanticError(
                f"builder-trace symbol '{name}' cannot be reassigned",
                loc,
            )

    def _check_no_open_traces(self) -> None:
        if not self._open_traces:
            return
        name, loc = next(iter(self._open_traces.items()))
        raise SemanticError(
            f"builder-trace symbol '{name}' was never closed by a "
            f"@builder_terminal call before the end of "
            f"'{self.function_being_traced}'",
            loc,
        )

    def _validate_no_tracked_leaks(self, body: list[TpyStmt]) -> None:
        tracked = self._all_tracked_names
        for stmt in body:
            self._validate_stmt(stmt, tracked, in_control_flow=False)

    def _validate_stmt(
        self, stmt: TpyStmt, tracked: set[str], *, in_control_flow: bool,
    ) -> None:
        for expr in stmt.exprs():
            if expr is not None:
                self._validate_expr(expr, tracked, stmt,
                                    in_control_flow=in_control_flow)
        for nested in stmt.sub_bodies():
            for inner in nested:
                self._validate_stmt(inner, tracked, in_control_flow=True)
        # TpyNestedDef.sub_bodies() returns []; recurse into its
        # function body explicitly so a tracked symbol captured by a
        # nested def is flagged.
        if isinstance(stmt, TpyNestedDef):
            for inner in stmt.func.body:
                self._validate_stmt(inner, tracked, in_control_flow=True)

    def _validate_expr(
        self, expr: TpyExpr, tracked: set[str], stmt: TpyStmt,
        *, in_control_flow: bool,
    ) -> None:
        if isinstance(expr, TpyName) and expr.name in tracked:
            loc = getattr(expr, "loc", None) or getattr(stmt, "loc", None)
            if in_control_flow:
                raise SemanticError(
                    f"builder-trace symbol '{expr.name}' may not be used "
                    f"inside a control-flow block, lambda, or nested def",
                    loc,
                )
            raise SemanticError(
                f"builder-trace symbol '{expr.name}' may only appear as "
                f"the receiver of a registered @builder_method call",
                loc,
            )
        # TpyLambda.children() returns [] to keep generic walks out of
        # closure bodies; recurse explicitly so a tracked symbol
        # captured by a lambda is flagged. The lambda body is itself
        # an expression.
        if isinstance(expr, TpyLambda):
            self._validate_expr(expr.body, tracked, stmt,
                                in_control_flow=True)
            return
        for child in expr.children():
            self._validate_expr(child, tracked, stmt,
                                in_control_flow=in_control_flow)

    def _process_stmt(self, stmt: TpyStmt) -> Any:
        # x = BuilderClass(...) -- start a new trace.
        # Both TpyAssign (rebinding) and TpyVarDecl (first declaration)
        # can carry the binding RHS, and the ctor itself can appear
        # either as a bare-name call (``ArgumentParser()`` after
        # ``from argparse import ArgumentParser``) or as a qualified
        # method call (``argparse.ArgumentParser()`` after
        # ``import argparse``). Both forms carry resolved_import.
        target_name, rhs = _extract_binding(stmt)
        if target_name is not None and rhs is not None:
            if isinstance(rhs, (TpyCall, TpyMethodCall)):
                builder_cls = self._lookup_builder_macro(rhs)
                if builder_cls is not None:
                    self._handle_ctor(target_name, rhs, builder_cls)
                    return _DROP
            if isinstance(rhs, TpyMethodCall):
                rep = self._handle_method_call(stmt, rhs, lhs=target_name)
                if rep is not _NOT_HANDLED:
                    return rep
            # Reassignment to an actively tracked builder symbol --
            # forbidden because the leak validator wouldn't catch this on
            # its own (the RHS may not reference the tracked name). Once
            # the trace has been closed by a terminal, the name is fair
            # game for any rebinding.
            self._reject_active_reassignment(target_name, getattr(stmt, "loc", None))
        # bare method call: parser.method(...)
        if isinstance(stmt, TpyExprStmt) and isinstance(stmt.expr, TpyMethodCall):
            rep = self._handle_method_call(stmt, stmt.expr, lhs=None)
            if rep is not _NOT_HANDLED:
                return rep
        return None

    # ------------------------------------------------------------------
    # Constructor handling
    # ------------------------------------------------------------------

    def _lookup_builder_macro(self, call: TpyCall | TpyMethodCall) -> type | None:
        """Return the @builder_macro state class targeted by this call,
        or None. Works for both ``Builder()`` (TpyCall after
        ``from mod import Builder``) and ``mod.Builder()`` (TpyMethodCall
        after ``import mod``); both carry ``resolved_import`` (immediate
        import source). The chain walk recovers the registry's canonical
        (ultimate_module, name) key for re-exports through plain modules.
        """
        macro_reg = self.ctx.macro_registry
        if macro_reg is None or call.resolved_import is None:
            return None
        mod_name, obj_name = call.resolved_import
        macro_cls = macro_reg.get_builder_macro(mod_name, obj_name)
        if macro_cls is not None:
            return macro_cls
        result = walk_attribute_chain(
            self.ctx.registry, mod_name, obj_name,
            is_kind(SymbolKind.BUILDER_MACRO))
        if result is None:
            return None
        ult_mod, ult_name, _bd = result
        return macro_reg.get_builder_macro(ult_mod, ult_name)

    def _handle_ctor(
        self, var_name: str, call: TpyCall, macro_cls: type,
    ) -> None:
        from ..macro_loader import validate_builder_macro

        self._reject_active_reassignment(var_name, call.loc)
        qname = f"{call.resolved_import[0]}.{call.resolved_import[1]}"
        args = _build_macro_args(call.args, call.kwargs)
        ctx_obj = BuilderContext(
            expander=self, ctx=self.ctx,
            call_loc=call.loc, function_being_traced=self.function_being_traced,
        )
        state = validate_builder_macro(macro_cls, ctx_obj, args, qname, call.loc)
        handlers = _scan_handlers(macro_cls)
        self._tracked[var_name] = _TrackedSymbol(
            var_name=var_name, state=state, macro_qname=qname,
            handlers=handlers,
        )
        self._all_tracked_names.add(var_name)
        self._open_traces[var_name] = call.loc

    # ------------------------------------------------------------------
    # Method-call handling
    # ------------------------------------------------------------------

    def _handle_method_call(
        self, stmt: TpyStmt, mcall: TpyMethodCall, lhs: str | None,
    ) -> Any:
        if not isinstance(mcall.obj, TpyName):
            return _NOT_HANDLED
        sym = self._tracked.get(mcall.obj.name)
        if sym is None:
            return _NOT_HANDLED
        spec = sym.handlers.get(mcall.method)
        if spec is None:
            raise SemanticError(
                f"no @builder_method handler for '{sym.macro_qname}.{mcall.method}'",
                mcall.loc,
            )

        from ..macro_loader import expand_builder_method

        args = _build_macro_args(mcall.args, mcall.kwargs)
        ctx_obj = BuilderContext(
            expander=self, ctx=self.ctx,
            call_loc=mcall.loc, function_being_traced=self.function_being_traced,
        )
        bound = spec.fn.__get__(sym.state, type(sym.state))
        qname = f"{sym.macro_qname}.{mcall.method}"

        if spec.kind == _KIND_METHOD:
            expand_builder_method(bound, qname, ctx_obj, args, mcall.loc)
            return _DROP

        if spec.kind == _KIND_RETURNS:
            if lhs is None:
                raise SemanticError(
                    f"@builder_returns method '{qname}' must be assigned to a name",
                    mcall.loc,
                )
            # Run the reassignment check before invoking the handler so
            # the diagnostic fires even when the handler would itself
            # crash -- and so the parent's trace state isn't silently
            # overwritten before the user-visible error.
            self._reject_active_reassignment(lhs, mcall.loc)
            child_state = expand_builder_method(bound, qname, ctx_obj, args, mcall.loc)
            child_cls = spec.child_class
            if child_cls is None or not isinstance(child_state, child_cls):
                raise SemanticError(
                    f"@builder_returns({getattr(child_cls, '__name__', '?')}) "
                    f"method '{qname}' returned {type(child_state).__name__}",
                    mcall.loc,
                )
            child_handlers = _scan_handlers(child_cls)
            self._tracked[lhs] = _TrackedSymbol(
                var_name=lhs, state=child_state,
                macro_qname=f"{sym.macro_qname}::{child_cls.__name__}",
                handlers=child_handlers,
                parent_var=sym.var_name,
            )
            self._all_tracked_names.add(lhs)
            self._open_traces[lhs] = mcall.loc
            return _DROP

        # _KIND_TERMINAL: handler should call ctx.replace_call.
        self._pending_replacement = None
        result = expand_builder_method(bound, qname, ctx_obj, args, mcall.loc)
        replacement = self._pending_replacement
        self._pending_replacement = None
        if replacement is None:
            raise SemanticError(
                f"@builder_terminal handler '{qname}' did not call "
                f"ctx.replace_call(); the call site cannot be rewritten",
                mcall.loc,
            )
        # Tracked symbol's trace is finished; remove from active tracking.
        # Sub-builders spawned via @builder_returns from this symbol (or
        # from any of those descendants) are closed alongside the root --
        # they don't have their own terminal and contribute their state
        # to the root's terminal handler. Walk the parent_var chain to
        # find every descendant before mutating the dicts.
        #
        # Cost is O(D * N) where D is chain depth and N is the number of
        # tracked symbols still alive: each iteration extends ``closing``
        # by one depth level. Fine for argparse (max depth 3: parser ->
        # subparsers_action -> sub_builder); a reverse parent->children
        # map would flatten this to O(N) if deeper builder chains land.
        closing = {sym.var_name}
        changed = True
        while changed:
            changed = False
            for name, ts in self._tracked.items():
                if name in closing:
                    continue
                if ts.parent_var in closing:
                    closing.add(name)
                    changed = True
        for name in closing:
            self._tracked.pop(name, None)
            self._open_traces.pop(name, None)
        rewritten = self._build_replacement_call(replacement, mcall.loc)
        if isinstance(stmt, TpyAssign):
            stmt.value = rewritten
            return stmt
        if isinstance(stmt, TpyVarDecl):
            stmt.init = rewritten
            return stmt
        # bare call statement
        new_stmt = TpyExprStmt(expr=rewritten)
        new_stmt.loc = stmt.loc
        return new_stmt

    def _build_replacement_call(
        self, rep: _PendingReplacement, loc: Any,
    ) -> TpyCall:
        positional_exprs = [ma.expr for ma in rep.args.positional]
        kwarg_exprs = {k: ma.expr for k, ma in rep.args.kwargs.items()}
        call = TpyCall(
            func=TpyName(rep.fn_name),
            args=positional_exprs,
            kwargs=kwarg_exprs,
        )
        call.loc = loc
        return call

    # ------------------------------------------------------------------
    # BuilderContext-facing hooks
    # ------------------------------------------------------------------

    def fresh_module_name(self, hint: str) -> str:
        """Allocate a unique module-private name for a synthesized decl.

        Counters live on SemanticContext so they're shared across all
        builder-trace expansions in the same module -- otherwise two
        functions both opening an ArgumentParser trace would each mint
        the same suffix-1 record/function name and collide at codegen.
        """
        counters = self.ctx.builder_trace_fresh_counters
        n = counters.get(hint, 0) + 1
        counters[hint] = n
        return f"{_SYNTH_PREFIX}{hint}_{n}"

    def emit_record(
        self, name: str, fields: list[tuple[str, TpyType]],
        methods: list[TpyFunction], loc: Any,
    ) -> TypeInfo:
        """Caveats: ``validate_record_inheritance`` and
        ``validate_value_type_fields`` run before any builder-trace
        expansion, so synthesized records skip those passes -- inheritance
        and ValueType-conformance constraints on emitted records are
        currently unchecked. The current argparse use case (plain data
        structs) doesn't hit either.

        ``methods`` may include any combination of methods; a default
        positional ``__init__`` is auto-injected unless one is already
        present in the list. This lets builder-trace macros emit
        property forwarders / helper methods alongside the default init
        without having to re-implement the init shape themselves.
        """
        methods = list(methods)
        if not any(m.name == "__init__" for m in methods):
            methods.insert(0, _build_default_init(fields, loc))
        record = _build_record(name, fields, methods, loc)
        # Append to module so codegen sees it; register with the registrar
        # so subsequent sema lookups find it.
        self.module.records.append(record)
        self.registrar.register_record(record)
        # Promote the bare NominalType to qname-bearing form so type
        # equality with the registered RecordInfo holds in compatibility
        # checks (otherwise references via the bare type and via the
        # registered type compare unequal even though both name "name").
        from ..parse.resolve_refs import promote_bare_nominals
        promoted = promote_bare_nominals(NominalType(name), self.ctx.registry)
        return TypeInfo.from_tpy_type(promoted)

    def emit_function(
        self, name: str, params: list[tuple[str, TpyType]],
        return_type: TpyType, body: list[TpyStmt], loc: Any,
    ) -> str:
        """Caveat: ``_normalize_function_info_refs`` runs in pass 2 of
        analyze(); synthesized functions are added in passes 4 / 5.5
        and skip that normalization, so ``FunctionInfo.params`` are
        not wrapped with ``RefType`` for non-value parameter types.
        Masked today because overload resolution unwraps refs on both
        sides; any future check that distinguishes ``ListType`` from
        ``RefType(ListType)`` on registered params would expose this.
        """
        # Returning a freshly constructed reference type requires Own[T] --
        # synthesized parse functions always move ownership to the caller.
        if not return_type.is_value_type() and not isinstance(return_type, OwnType):
            return_type = OwnType(return_type)
        body = list(body)
        # Stmts coming from ast.quote() carry locs anchored to the
        # fragment source, not the user module. Clear them so codegen
        # doesn't try to look up the fragment's line numbers in main.py.
        _strip_fragment_locs(body)
        # Quoted-statement fragments leave TypeRefNodes on annotated
        # decls / generic call_type / etc.; sema's pre-pass already ran
        # by the time we're here, so resolve them now using the module's
        # parser resolver. Mirrors what _finalize_function_refs does for
        # quote_fun-produced functions.
        resolver = getattr(self.ctx, "parser_resolver", None)
        if resolver is not None:
            from ..parse.resolve_refs import _walk_body
            _walk_body(body, None, resolver)
        func = TpyFunction(
            name=name, params=list(params), return_type=return_type,
            body=body,
        )
        func.loc = loc
        self.module.functions.append(func)
        self.registrar.register_function(func)
        return name

    def replace_call(
        self, fn_name: str, args: MacroArgs, loc: Any,
    ) -> None:
        if self._pending_replacement is not None:
            raise SemanticError(
                "ctx.replace_call() called more than once during a single "
                "@builder_terminal expansion",
                loc,
            )
        self._pending_replacement = _PendingReplacement(fn_name=fn_name, args=args)


def _build_record(
    name: str, fields: list[tuple[str, TpyType]],
    methods: list[TpyFunction], loc: Any,
) -> TpyRecord:
    field_infos = [FieldInfo(name=fname, type=ftype) for fname, ftype in fields]
    record = TpyRecord(name=name, fields=field_infos, methods=list(methods))
    record.loc = loc
    return record


def _strip_fragment_locs(stmts: list[TpyStmt]) -> None:
    """Recursively clear ``loc`` on stmts produced by ``ast.quote()``.

    Quoted-fragment stmts carry locs whose line numbers index the
    fragment source, not the user module's source -- if those flow
    through to codegen unchanged, ``emit_inline_comments`` indexes
    out of bounds. Clearing matches the implicit contract for
    ``ast.*``-built stmts (which never set loc in the first place).
    """
    for stmt in stmts:
        stmt.loc = None
        for nested in stmt.sub_bodies():
            _strip_fragment_locs(nested)


def _build_default_init(
    fields: list[tuple[str, TpyType]], loc: Any,
) -> TpyFunction:
    """Build a positional ``__init__`` that assigns each field from a
    same-named parameter.

    Used by ``emit_record`` when the caller doesn't supply methods.
    """
    from ..parse.nodes import TpyAssign, TpyFieldAccess, TpyName
    from ..typesys import OwnType, UnionType

    params: list[tuple[str, TpyType]] = []
    body: list[Any] = []
    for fname, ftype in fields:
        # Union[A, B] non-value fields use the bare type as the param
        # (pointer-variant convention) -- codegen expects the body's
        # implicit assignment to lower through `to_value_variant`,
        # which assumes a pointer-variant input. Wrapping in OwnType
        # here renders the param as `value_variant&&` and breaks that
        # body lowering. Mirrors how user-written ctors for Union-
        # typed fields are spelled (`s: A | B`, not `s: Own[A | B]`).
        if ftype.is_value_type() or isinstance(ftype, UnionType):
            param_type = ftype
        else:
            param_type = OwnType(ftype)
        params.append((fname, param_type))
        body.append(TpyAssign(
            target=TpyFieldAccess(obj=TpyName("self"), field=fname),
            value=TpyName(fname),
        ))
    func = TpyFunction(
        name="__init__", params=params, return_type=VOID, body=body,
        is_method=True, is_macro_generated=True, macro_origin="builder-trace",
    )
    func.loc = loc
    return func


# Sentinel objects for _process_stmt return values.
class _DropMarker:
    pass


class _NotHandledMarker:
    pass


_DROP = _DropMarker()
_NOT_HANDLED = _NotHandledMarker()
