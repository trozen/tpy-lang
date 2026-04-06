"""
TurboPython Call Analysis

Function and constructor call analysis.
"""

from __future__ import annotations
from dataclasses import replace as dc_replace
from typing import Callable, TYPE_CHECKING

from ..typesys import (
    TpyType, NamedType, OwnType, OptionalType, ListType, PendingListType, PendingViewType, CopyIterType, OwnIterType,
    IntLiteralType, FloatType, Float32Type, BoolType, resolve_int_literals,
    StrType, LiteralType, LiteralValue, CharType, ListLiteralInfo, FunctionInfo, RecordInfo, TypeParamRef,
    PtrType, is_readonly_ptr, VoidType, SpanType, ArrayType, ParamInfo, FixedIntType, BigIntType, ReadonlyType,
    UNKNOWN_ELEMENT, PendingDictType, DictLiteralInfo, PendingSetType, SetLiteralInfo,
    UnionType, EnumType, VOID, BIGINT, BOOL, STR, INT32, is_protocol_type, unwrap_readonly, unwrap_own, unwrap_optional_own,
    is_any_str_type, container_to_str_template, error_return_matches,
    is_protocol_union, protocol_union_protocols,
    StrViewType, STRVIEW, MutationCallEdge,
    PendingGenericInstanceType, PendingGenericInstanceInfo,
    FnType, CallableType, unwrap_ref_type,
)
from ..parse import (
    TpyCall, TpyMethodCall, TpyFieldAccess, TpyStrLiteral, TpyName, TpyFunction, TpyExpr,
    TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyNoneLiteral, TpyUnaryOp,
    TpyTypeParamConstruct, TpyCoerce, TpyLambda,
    TpyDictLiteral, TpySetLiteral,
    TpyVarargPack, TpyStarUnpack,
)
from ..modules import extract_type_params
from ..namespace import BindingKind
from ..coercions import CoercionContext, VALUE_TO_PTR
from .context import PENDING_CONTAINER_TYPES, addr_taken_roots
from .diagnostics import SemanticError
from .overloads import type_matches_numeric, resolve_overload
from ..macro_api import MacroArg, CallMacroContext, TypeInfo
from ..macro_loader import expand_call_macro

if TYPE_CHECKING:
    from .context import SemanticContext
    from .type_ops import TypeOperations
    from .protocols import ProtocolChecker
    from .compatibility import TypeCompatibility
    from .local_deduction import LocalTypeDeduction
    from .expressions import ExpressionAnalyzer
    from .methods import MethodAnalyzer
    from ..parse.nodes import SourceLocation

from tpyc import modules as builtin_modules
from .. import qnames


def _tp_in_record_type(name: str, typ: TpyType) -> bool:
    """Check if type param `name` appears as a direct type arg of a record."""
    if isinstance(typ, NamedType) and typ.is_record:
        for inner in typ.inner_types():
            if isinstance(inner, TypeParamRef) and inner.name == name:
                return True
    for inner in typ.inner_types():
        if _tp_in_record_type(name, inner):
            return True
    return False


def prefer_strview_for_literals(
    type_subst: dict[str, TpyType],
    func: FunctionInfo,
    args: list,
    type_conforms_to_protocol: 'Callable',
    explicit_count: int = 0,
) -> None:
    """Downgrade T=StrType to T=StrViewType when all args at bare-T
    positions are string literals (static lifetime, safe as string_view)."""
    explicit_params = set(func.type_params[:explicit_count])
    for tp, inferred_type in list(type_subst.items()):
        if tp in explicit_params:
            continue
        if not isinstance(inferred_type, StrType):
            continue
        # Skip if T would become a record field (StrView not allowed as field)
        if _tp_in_record_type(tp, func.return_type):
            continue
        if any(_tp_in_record_type(tp, ptype) for _, ptype in func.params):
            continue
        all_literals = True
        any_match = False
        for (pname, ptype), arg in zip(func.params, args):
            ptype_bare = unwrap_ref_type(ptype)
            if isinstance(ptype_bare, TypeParamRef) and ptype_bare.name == tp:
                any_match = True
                if not isinstance(arg, TpyStrLiteral):
                    all_literals = False
                    break
        if any_match and all_literals:
            # Re-check bounds if present
            if tp in func.type_param_bounds:
                if not type_conforms_to_protocol(STRVIEW, func.type_param_bounds[tp]):
                    continue
            type_subst[tp] = STRVIEW


def _enrich_literal_types(
    arg_types: list[TpyType], args: list[TpyExpr],
    candidates: list[FunctionInfo],
) -> list[TpyType]:
    """Create enriched arg types where literal args become single-value LiteralType.

    Returns a new list where literal arguments (str, int, bool) get
    LiteralType(base, (LiteralValue(...),)) instead of their plain type.
    Used only for overload resolution; the original arg_types are used
    for everything else.

    Only enriches when at least one candidate has a LiteralType param,
    to avoid breaking protocol-based matching (e.g. hash(Hashable)).
    Also passes through args that already carry LiteralType (e.g. forwarded
    Literal-annotated parameters).
    """
    has_literal_param = any(
        isinstance(p.type, LiteralType) for c in candidates for p in c.params
    )
    if not has_literal_param:
        return arg_types
    enriched = []
    for arg_t, arg in zip(arg_types, args):
        if isinstance(arg_t, StrType) and isinstance(arg, TpyStrLiteral):
            enriched.append(LiteralType(STR, (LiteralValue("str", arg.value),)))
        elif isinstance(arg_t, IntLiteralType) and arg_t.value is not None:
            enriched.append(LiteralType(INT32, (LiteralValue("int", arg_t.value),)))
        elif isinstance(arg_t, BoolType) and isinstance(arg, TpyBoolLiteral):
            enriched.append(LiteralType(BOOL, (LiteralValue("bool", arg.value),)))
        elif isinstance(arg_t, LiteralType):
            enriched.append(arg_t)
        else:
            enriched.append(arg_t)
    return enriched


def arity_error_msg(name: str, min_args: int, max_args: int, got: int) -> str:
    """Format an arity mismatch error message."""
    if min_args == max_args:
        return f"'{name}' expects {max_args} argument(s), got {got}"
    return f"'{name}' expects {min_args} to {max_args} arguments, got {got}"


def _init_params_min_args(init_params: list) -> int:
    """Compute min args from init_params tuples (name, type, default)."""
    return sum(1 for _, _, default in init_params if default is None)


def resolve_kwargs(
    expr_args: list[TpyExpr],
    expr_kwargs: dict[str, TpyExpr],
    params: list[ParamInfo],
    func_name: str,
    error_fn,
    call_loc: 'SourceLocation | None' = None,
) -> list[TpyExpr]:
    """Resolve keyword arguments into a fully-positional argument list.

    Validates kwarg names, detects duplicate/missing args, and fills gaps
    with default expressions from ParamInfo. Gap-filled defaults get the
    call site's loc so errors point to the call, not the function definition.
    Keyword-only params can only be supplied via kwargs, not positionally.
    Variadic params are excluded from kwargs resolution.
    """
    # Build name -> index map, excluding variadic params
    name_to_index = {p.name: i for i, p in enumerate(params) if not p.is_variadic}

    # Count positional slots (non-keyword-only, non-variadic)
    has_kwonly = any(p.keyword_only for p in params)
    has_variadic = any(p.is_variadic for p in params)
    pos_count = sum(1 for p in params if not p.keyword_only and not p.is_variadic)

    # Check positional overflow into keyword-only slots (variadic absorbs extras)
    if has_kwonly and not has_variadic and len(expr_args) > pos_count:
        raise error_fn(
            f"'{func_name}' takes {pos_count} positional argument(s), got {len(expr_args)}")

    if not expr_kwargs:
        # Even without kwargs, check for missing required keyword-only params
        # and fill in defaults for optional keyword-only params
        for p in params:
            if p.keyword_only and not p.has_default:
                raise error_fn(f"'{func_name}' missing required keyword argument: '{p.name}'")
        if has_kwonly:
            # Append keyword-only defaults to the args list
            result = list(expr_args)
            for p in params:
                if not p.keyword_only:
                    continue
                default = p.default_expr
                if call_loc is not None and default is not None:
                    default = dc_replace(default, loc=call_loc)
                result.append(default)
            return result
        return expr_args

    # Validate all kwarg names exist in params
    for kw_name in expr_kwargs:
        if kw_name not in name_to_index:
            raise error_fn(f"'{func_name}' got unexpected keyword argument '{kw_name}'")

    # Validate no kwarg overlaps with a positional arg (skip keyword-only params)
    for param_name, idx in name_to_index.items():
        if param_name in expr_kwargs and idx < len(expr_args) and not params[idx].keyword_only:
            raise error_fn(f"'{func_name}' got multiple values for argument '{param_name}'")

    # When there's a variadic param, positional args don't map 1:1 to params.
    # Handle separately: raw positional args stay as-is, then append resolved kwonly args.
    if has_variadic:
        result = list(expr_args)
        for p in params:
            if not p.keyword_only:
                continue
            if p.name in expr_kwargs:
                result.append(expr_kwargs[p.name])
            elif p.has_default:
                default = p.default_expr
                if call_loc is not None:
                    default = dc_replace(default, loc=call_loc)
                result.append(default)
            else:
                raise error_fn(f"'{func_name}' missing required keyword argument: '{p.name}'")
        return result

    # Non-variadic: find the rightmost explicitly-provided index
    rightmost = len(expr_args) - 1
    for kw_name in expr_kwargs:
        idx = name_to_index[kw_name]
        if idx > rightmost:
            rightmost = idx

    # Build result list up to rightmost
    result: list[TpyExpr] = []
    for i in range(rightmost + 1):
        p = params[i]
        if i < len(expr_args):
            result.append(expr_args[i])
        elif p.name in expr_kwargs:
            result.append(expr_kwargs[p.name])
        elif p.has_default:
            default = p.default_expr
            if call_loc is not None:
                default = dc_replace(default, loc=call_loc)
            result.append(default)
        else:
            raise error_fn(f"'{func_name}' missing required argument: '{p.name}'")

    # Check for missing required keyword-only params that are beyond rightmost
    for i, p in enumerate(params):
        if i > rightmost and p.keyword_only and not p.has_default and p.name not in expr_kwargs:
            raise error_fn(f"'{func_name}' missing required keyword argument: '{p.name}'")

    return result


def resolve_kwargs_init_params(
    expr_args: list[TpyExpr],
    expr_kwargs: dict[str, TpyExpr],
    init_params: list[tuple[str, 'TpyType', 'TpyExpr | None']],
    func_name: str,
    error_fn,
    call_loc: 'SourceLocation | None' = None,
) -> list[TpyExpr]:
    """Resolve keyword arguments for record constructors using init_params format.

    Adapts init_params tuples to ParamInfo and delegates to resolve_kwargs.
    """
    params = [ParamInfo(name, ptype, default_expr=default) for name, ptype, default in init_params]
    return resolve_kwargs(expr_args, expr_kwargs, params, func_name, error_fn, call_loc=call_loc)


def _resolve_cpp_template_type_params(
    fi: FunctionInfo,
    type_params: dict[str, TpyType] | None = None,
    result_type: TpyType | None = None,
) -> FunctionInfo:
    """Substitute type param placeholders and {cpp} in cpp_template.

    type_params: class-level type params ({T}, {K}, {V}) to substitute.
    result_type: concrete result type for {cpp} substitution.

    Returns a dc_replace'd copy with the resolved template, or the original
    FunctionInfo if no substitution was needed.
    """
    if not fi.cpp_template:
        return fi
    if not type_params and not result_type:
        return fi
    template = fi.cpp_template
    if result_type is not None and "{cpp}" in template:
        template = template.replace("{cpp}", result_type.to_cpp())
    if type_params:
        for name, typ in type_params.items():
            placeholder = f"{{{name}}}"
            if placeholder in template:
                template = template.replace(placeholder, typ.to_cpp_stored())
    if template == fi.cpp_template:
        return fi
    return dc_replace(fi, cpp_template=template)


def _has_type_param_ref(t: TpyType) -> bool:
    """Check if a type contains an unresolved TypeParamRef (e.g. Span[T], Span[readonly[T]])."""
    if isinstance(t, TypeParamRef):
        return True
    return any(_has_type_param_ref(a) for a in t.inner_types())


def _has_type_param_ref_in_params(func: "FunctionInfo") -> bool:
    """Check if a FunctionInfo needs generic type inference.

    True if any parameter type or the return type contains TypeParamRef.
    This covers zero-arg generic functions like unsafe_alloc[T]() -> Ptr[T]
    that infer T from return type context.
    """
    if any(_has_type_param_ref(p.type) for p in func.params):
        return True
    return _has_type_param_ref(func.return_type)


def _partial_substitute(typ: TpyType, subst: dict[str, TpyType]) -> TpyType:
    """Substitute known type params, preserve unknown TypeParamRefs as-is."""
    if isinstance(typ, TypeParamRef):
        return subst.get(typ.name, typ)
    return typ.map_inner_types(lambda t: _partial_substitute(t, subst))


_NUMERIC_TYPES = (FixedIntType, BigIntType, FloatType, Float32Type, IntLiteralType)


def _default_compatible_with_type(default_expr: TpyExpr, resolved_type: TpyType) -> bool:
    """Check if a default expression is compatible with a resolved concrete type."""
    match default_expr:
        case TpyTypeParamConstruct():
            return True
        case TpyNoneLiteral():
            return isinstance(resolved_type, OptionalType)
        case TpyBoolLiteral():
            return isinstance(resolved_type, (BoolType,) + _NUMERIC_TYPES)
        case TpyIntLiteral():
            return isinstance(resolved_type, _NUMERIC_TYPES) or isinstance(resolved_type, BoolType)
        case TpyFloatLiteral():
            return isinstance(resolved_type, (FloatType, Float32Type))
        case TpyStrLiteral():
            return is_any_str_type(resolved_type) or isinstance(resolved_type, CharType)
        case TpyUnaryOp(op="-"):
            return _default_compatible_with_type(default_expr.operand, resolved_type)
        case TpyCall():
            return isinstance(resolved_type, _NUMERIC_TYPES)
        case _:
            # Unknown expression type: allow (parser ensures only const exprs reach here)
            return True


def validate_generic_defaults(
    expr_args: list[TpyExpr],
    func: FunctionInfo,
    type_subst: dict[str, TpyType],
    type_ops: 'TypeOperations',
    error_fn,
) -> None:
    """Validate defaults for params not covered by explicit args after generic substitution."""
    for i in range(len(expr_args), len(func.params)):
        param = func.params[i]
        if not param.has_default:
            continue
        resolved_type = unwrap_ref_type(type_ops.substitute_type_params(param.type, type_subst))
        if isinstance(resolved_type, TypeParamRef):
            continue
        if not _default_compatible_with_type(param.default_expr, resolved_type):
            raise error_fn(
                f"Default value for '{param.name}' is incompatible with "
                f"type '{resolved_type}' (resolved from generic '{func.name}')")


def validate_type_param_bounds(
    type_subst: dict[str, TpyType],
    bounds: dict[str, NamedType],
    func_name: str,
    type_conforms_to_protocol,
    error_fn,
) -> None:
    """Validate that resolved type args satisfy their type parameter bounds."""
    for param_name, type_arg in type_subst.items():
        if param_name in bounds:
            bound = bounds[param_name]
            if not type_conforms_to_protocol(type_arg, bound):
                raise error_fn(
                    f"Type argument '{type_arg}' does not satisfy bound '{bound}' "
                    f"for type parameter '{param_name}' of '{func_name}'")


_REPR_TEMPLATE = "::tpy::__repr__({0})"


def _repr_fallback_template(typ: TpyType) -> str | None:
    """Return a C++ template for repr() on types without Representable,
    or None if the type has no known repr path.

    Covers: bool, fixed ints, float, BigInt, strings, optionals,
    enums, and user records (which always have operator<<).
    """
    if isinstance(typ, BoolType):
        return _REPR_TEMPLATE
    if isinstance(typ, (FixedIntType, BigIntType)):
        return _REPR_TEMPLATE
    if isinstance(typ, (FloatType, Float32Type)):
        return _REPR_TEMPLATE
    if is_any_str_type(typ):
        return _REPR_TEMPLATE
    if isinstance(typ, CharType):
        return _REPR_TEMPLATE
    if isinstance(typ, EnumType):
        return _REPR_TEMPLATE
    if isinstance(typ, OptionalType):
        return _REPR_TEMPLATE
    if isinstance(typ, NamedType) and typ.is_user_record:
        return _REPR_TEMPLATE
    return None


class CallAnalyzer:
    """Function and constructor call analysis."""

    def __init__(
        self,
        ctx: SemanticContext,
        type_ops: TypeOperations,
        protocols: ProtocolChecker,
        compat: TypeCompatibility,
        deduction: LocalTypeDeduction,
    ):
        self.ctx = ctx
        self.type_ops = type_ops
        self.protocols = protocols
        self.compat = compat
        self.deduction = deduction
        # Set via set_cross_deps() to break circular dependency
        self.expr: ExpressionAnalyzer | None = None
        self.methods: MethodAnalyzer | None = None
        # Pending borrow checks deferred until Phase 2 resolves mutated_params
        self.pending_borrow_checks: list[tuple[FunctionInfo, int, str, SourceLocation | None]] = []

    def set_cross_deps(self, expr: ExpressionAnalyzer, methods: MethodAnalyzer | None = None) -> None:
        """Wire circular dependencies (must be called before analyze_call)."""
        self.expr = expr
        self.methods = methods

    def _restore_readonly_arg(self, arg: TpyExpr, arg_type: TpyType,
                              target_is_readonly: bool = False) -> TpyType:
        """Restore ReadonlyType on arg if isinstance narrowing stripped it.

        isinstance narrowing replaces the expr type with the concrete member,
        losing the ReadonlyType wrapper.  The scope binding preserves it.
        Skip when the target function/method is @readonly -- its params are
        implicitly readonly so passing a readonly arg is always safe.
        """
        if (not target_is_readonly
                and isinstance(arg, TpyName)
                and not isinstance(arg_type, ReadonlyType)
                and self.ctx.is_readonly_name(arg.name)):
            return ReadonlyType(arg_type)
        return arg_type

    def resolve_pending_borrow_checks(self) -> None:
        """Emit or suppress deferred borrow warnings after Phase 2 propagation."""
        for fi, param_idx, storage, loc in self.pending_borrow_checks:
            # Use structural_mutated_params when available (precise); fall back to mutated_params.
            effective_mp = fi.structural_mutated_params if fi.structural_mutated_params is not None else fi.mutated_params
            if effective_mp is not None and param_idx not in effective_mp:
                continue
            param_name = fi.params[param_idx].name if param_idx < len(fi.params) else "?"
            self.ctx.warning_from_loc(
                f"Passing borrowed container '{storage}' to non-readonly parameter "
                f"'{param_name}' (function may invalidate references)",
                loc,
            )
        self.pending_borrow_checks.clear()

    def _resolve_call_kwargs(self, expr: TpyCall, func: FunctionInfo) -> None:
        """Resolve keyword arguments on a TpyCall into positional form."""
        if not expr.kwargs and not func.has_keyword_only:
            return
        expr.args = resolve_kwargs(
            expr.args, expr.kwargs, func.params, expr.func_name,
            lambda msg: self.ctx.error(msg, expr),
            call_loc=expr.loc,
        )
        expr.kwargs = {}

    def _resolve_call_kwargs_init(
        self, expr: TpyCall, record: RecordInfo,
    ) -> None:
        """Resolve keyword arguments for record constructor calls."""
        if not expr.kwargs:
            return
        expr.args = resolve_kwargs_init_params(
            expr.args, expr.kwargs, record.init_params, f"{record.name}()",
            lambda msg: self.ctx.error(msg, expr),
            call_loc=expr.loc,
        )
        expr.kwargs = {}

    def _resolve_inferred_type_arg(self, t: TpyType) -> TpyType:
        """Resolve literal types in inferred type args before codegen."""
        return resolve_int_literals(t, self.ctx.default_int_type)

    def _reject_kwargs_for_builtin(self, expr: TpyCall, name: str) -> None:
        """Reject kwargs on overloaded builtin functions."""
        if expr.kwargs:
            raise self.ctx.error(
                f"Keyword arguments not supported for builtin '{name}'", expr)

    def _set_record_constructor_info(
        self,
        expr: TpyCall,
        record: RecordInfo,
        return_type: TpyType,
        type_subst: dict[str, TpyType] | None = None,
    ) -> None:
        """Attach resolved constructor metadata for readonly/effect checks."""
        init_overloads = record.get_method_overloads("__init__")
        if init_overloads:
            ctor = init_overloads[0]
            resolved_ctor = self.type_ops.substitute_method_type_params(ctor, type_subst) if type_subst else ctor
            expr.resolved_function_info = FunctionInfo(
                name=record.name,
                params=resolved_ctor.params,
                return_type=return_type,
                is_readonly=False,
            )
            self._check_borrow_arg_conflicts(expr)
            self._check_loop_var_arg_mutation(expr)
            self._record_mutation_call_edges(expr)
            return

        # Implicit default constructor (no user __init__)
        expr.resolved_function_info = FunctionInfo(
            name=record.name,
            params=[],
            return_type=return_type,
            is_readonly=False,
        )

    def analyze_call(self, expr: TpyCall) -> TpyType:
        """Analyze a function or constructor call."""
        # Expression callees: callbacks[0](x), get_handler()(x), etc.
        if not isinstance(expr.func, TpyName):
            return self._analyze_expr_callee(expr)

        # Handle super() call
        if expr.func_name == "super":
            from .methods import MethodAnalyzer
            return MethodAnalyzer._analyze_super_call_static(self.ctx, expr)

        # Type aliases: builtin type aliases (e.g. Float64 = float) resolve
        # to the underlying type's constructor. Other aliases are not callable.
        if expr.call_type is None:
            alias_type = self.ctx.registry.get_type_alias(expr.func_name)
            if alias_type is not None:
                record = self.ctx.registry.get_record_for_type(alias_type)
                if record and record.builtin_type_key and record.get_method_overloads("__init__"):
                    return self._analyze_record_constructor(expr, record)
                raise self.ctx.error(
                    f"Type alias '{expr.func_name}' is not callable. "
                    f"Use {alias_type} directly, or let the type be inferred from an annotation",
                    expr
                )

        # Generic type instantiation (e.g., Container[T, N](), Array[Int32, 8]())
        # The parser speculatively sets call_type for any imported name, so
        # verify it's actually a type before using it
        if expr.call_type is not None:
            # Check if this is actually a function -- the parser speculatively
            # sets call_type for any imported name, so we need to verify
            is_known_function = self.ctx.registry.get_function(expr.func_name) is not None
            if not is_known_function and self.ctx.current_ns:
                binding = self.ctx.current_ns.lookup(expr.func_name)
                if binding and binding.kind == BindingKind.FUNCTION:
                    is_known_function = True
                elif binding and binding.kind == BindingKind.IMPORTED_NAME and binding.import_source:
                    mod_info = self.ctx.registry.get_module(binding.import_source[0])
                    if mod_info and binding.import_source[1] in mod_info.functions:
                        is_known_function = True
            if is_known_function:
                # Clear speculative call_type; fall through to function
                # handling below where type_args will be used instead
                expr.call_type = None
            if not is_known_function:
                # Check if it's a user-defined record - use _analyze_record_constructor for bound validation
                record = self.ctx.registry.get_record(expr.func_name)
                if record:
                    return self._analyze_record_constructor(expr, record)
                # Subscript callee fallback: Handlers[0](args) was parsed as
                # Handlers[0]() generic type instantiation because the parser
                # treats unknown uppercase names as forward-ref types. If the
                # name isn't a known type/function and we have a subscript
                # callee, rewrite as expression callee.
                if expr.subscript_callee is not None:
                    if not self.ctx.registry.is_known_type(expr.func_name):
                        return self._rewrite_subscript_callee(expr)
                # It's a builtin type instantiation -- validate constructor args
                self._reject_kwargs_for_builtin(expr, expr.func_name)
                # Derive per-arg hints from __init__ param types when possible.
                # e.g. dict[str, str|Int32]([("a","b"), ("c",1)]) -> hint list[tuple[str, str|Int32]]
                arg_hints = self._derive_ctor_arg_hints(expr)
                arg_types = [self.expr.analyze_expr_with_hint(arg, hint)
                             for arg, hint in zip(expr.args, arg_hints)]
                if arg_types:
                    self._validate_generic_constructor(expr, arg_types)
                if isinstance(expr.call_type, PtrType) and expr.args:
                    self._validate_ptr_constructor(expr)
                    # Set resolved constructor for codegen (the &{0} template)
                    lookup = builtin_modules.lookup_generic_type(expr.func_name)
                    if lookup:
                        rec = self.ctx.registry.get_builtin_record(lookup.qualified_name)
                        if rec:
                            for ctor in rec.get_method_overloads("__init__"):
                                if len(ctor.params) == len(expr.args) and (ctor.cpp_template or ctor.native_function):
                                    expr.resolved_function_info = ctor
                                    break
                    self._validate_lvalue_params(expr)
                # Builtin type constructors -- not readonly (constructor calls
                # are not allowed in readonly contexts; see READONLY_DESIGN.md)
                if expr.resolved_function_info is None:
                    expr.resolved_function_info = FunctionInfo(
                        name="__init__",
                        params=[],
                        return_type=expr.call_type,
                        is_readonly=False,
                    )
                return expr.call_type
            # Otherwise fall through to function handling (type_args will be used)

        # Track if we found an imported generic type (allows fallthrough to generic handling)
        # Stores the original name (not alias) for lookup_generic_type
        imported_generic_name: str | None = None
        imported_generic_module: str | None = None

        # Use namespace for unified lookup - handles shadowing automatically
        if self.ctx.current_ns:
            binding = self.ctx.current_ns.lookup(expr.func_name)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    var_type = self.ctx.narrowed_types.get(expr.func_name, binding.type)
                    # Strip Own[T] -- Own is a storage property, not a type distinction
                    if isinstance(var_type, OwnType):
                        var_type = var_type.wrapped
                    if isinstance(var_type, FnType):
                        return self._analyze_fn_type_call(expr, var_type)
                    if isinstance(var_type, CallableType):
                        return self._analyze_callable_type_call(expr, var_type)
                    # Check for record type with __call__ method
                    if isinstance(var_type, NamedType):
                        record = self.ctx.registry.get_record_for_type(var_type)
                        if record and self.ctx.registry.get_method_overloads_with_parents(record, "__call__"):
                            return self._analyze_dunder_call(expr)
                    # Subscript callee fallback: fns[0](args) or fns[T](args)
                    # was parsed as a generic call but fns is a variable.
                    if expr.subscript_callee is not None:
                        return self._rewrite_subscript_callee(expr)
                    raise self.ctx.error(f"'{expr.func_name}' is not callable", expr)
                elif binding.kind == BindingKind.FUNCTION:
                    # Builtin-supplemented functions route through builtin path
                    # (richer diagnostics for unsafe_ptr, unsafe_cast etc.)
                    if binding.func_infos[0].is_builtin_function:
                        if binding.func_infos[0].special_handling:
                            return self._analyze_special_builtin(expr, binding.func_infos)
                        return self._analyze_builtin_function_overloads(expr, binding.func_infos)
                    return self._analyze_user_function_call(expr, binding.func_infos)
                elif binding.kind == BindingKind.RECORD:
                    return self._analyze_record_constructor(expr, binding.record_info)
                elif binding.kind == BindingKind.IMPORTED_NAME:
                    module_name, func_name = binding.import_source
                    # Check for call-site macro before other handling
                    if self.ctx.macro_registry:
                        macro_fn = self.ctx.macro_registry.get_call_macro(module_name, func_name)
                        if macro_fn is not None:
                            return self._expand_call_macro(expr, macro_fn, module_name, func_name)
                    qname = f"{module_name}.{func_name}"
                    # Special handling for functions with custom sema
                    if qname == "tpy.copy":
                        return self._analyze_tpy_copy(expr)
                    if qname == "tpy.copy_iter":
                        return self._analyze_tpy_copy_iter(expr)
                    if qname == "tpy.own_iter":
                        return self._analyze_tpy_own_iter(expr)
                    if qname == "tpy.try_parse":
                        return self._analyze_tpy_try_parse(expr)
                    if qname == "builtins.isinstance":
                        return self._analyze_isinstance(expr)
                    if qname == "builtins.print":
                        for kw_name in expr.kwargs:
                            if kw_name not in ("end", "sep"):
                                raise self.ctx.error(
                                    f"print() does not support keyword argument '{kw_name}'", expr)
                        for kw_name in ("end", "sep"):
                            if kw_name in expr.kwargs:
                                if not isinstance(expr.kwargs[kw_name], TpyStrLiteral):
                                    raise self.ctx.error(
                                        f"print() '{kw_name}' argument must be a string literal", expr)
                                self.expr.analyze_expr(expr.kwargs[kw_name])
                        for arg in expr.args:
                            self.expr.analyze_expr(arg)
                        expr.resolved_function_info = FunctionInfo(
                            name="print",
                            params=[],
                            return_type=VOID,
                            is_readonly=True,
                            is_builtin_function=True,
                            special_handling=True,
                            qualified_name="builtins.print",
                        )
                        return VOID
                    # Check for user module function (registered via _register_user_module_import)
                    if func_infos := self.ctx.registry.get_function(expr.func_name):
                        # Builtin-supplemented functions route through builtin path
                        if func_infos[0].is_builtin_function:
                            if func_infos[0].special_handling:
                                return self._analyze_special_builtin(expr, func_infos)
                            return self._analyze_builtin_function_overloads(expr, func_infos)
                        return self._analyze_user_function_call(expr, func_infos)
                    # Check for user module record (registered via _register_user_module_import)
                    if record_info := self.ctx.registry.get_record(expr.func_name):
                        return self._analyze_record_constructor(expr, record_info)
                    # Check for module function (e.g., math.sqrt)
                    from .registration import TypeRegistrar
                    if overloads := self._get_module_function_overloads(module_name, func_name):
                        if overloads[0].special_handling:
                            return self._analyze_special_builtin(expr, overloads)
                        if overloads[0].value_ptr_coercion:
                            return self._analyze_user_function_call(expr, overloads)
                        return self._analyze_builtin_function_overloads(expr, overloads)
                    # Check for type constructor (e.g., Int32 from tpy, int from builtins)
                    qname = f"{module_name}.{func_name}"
                    if record_info := self.ctx.registry.get_builtin_record(qname):
                        if record_info.get_method_overloads("__init__") and not record_info.type_params:
                            return self._analyze_record_constructor(expr, record_info)
                    # Generic types (Array, list) - mark as found and fall through
                    if builtin_modules.lookup_generic_type_in_module(func_name, module_name):
                        imported_generic_name = func_name
                        imported_generic_module = module_name
                    else:
                        raise SemanticError(f"Unknown function '{func_name}' in module '{module_name}'", expr.loc)
                elif binding.kind == BindingKind.MODULE:
                    raise SemanticError(f"Cannot call module '{expr.func_name}' directly; use module.function()", expr.loc)
                elif binding.kind == BindingKind.ENUM:
                    return self._analyze_enum_from_value(expr, binding.enum_type)
                elif binding.kind == BindingKind.BUILTIN:
                    raise SemanticError(f"'{expr.func_name}' is not callable", expr.loc)

        # Check if it's a tpy type that requires explicit import
        # Only check if we didn't find it in namespace (i.e., not imported)
        # Python builtins (int, str, list) are in builtins_ns and would be found above
        if imported_generic_name is None:
            tpy_qname = f"tpy.{expr.func_name}"
            if record_info := self.ctx.registry.get_builtin_record(tpy_qname):
                if record_info.get_method_overloads("__init__") and not record_info.type_params:
                    raise self.ctx.error(
                        f"'{expr.func_name}' requires: from tpy import {expr.func_name}",
                        expr
                    )
            # Also check generic tpy types (Array, Span)
            if lookup := builtin_modules.lookup_generic_type(expr.func_name):
                if lookup.qualified_name.startswith("tpy."):
                    raise self.ctx.error(
                        f"'{expr.func_name}' requires: from tpy import {expr.func_name}",
                        expr
                    )

        # Fallback: Check if it's a record constructor
        record = self.ctx.registry.get_record(expr.func_name)
        if record:
            # Analyze arguments
            for arg in expr.args:
                self.expr.analyze_expr(arg)
            return NamedType(expr.func_name)

        # Fallback: Check if it's a function call
        func_infos = self.ctx.registry.get_function(expr.func_name)
        if func_infos:
            return self._analyze_legacy_function_call(expr, func_infos[0])

        # Generic type constructor without context for type inference
        # Only proceed if we found an imported generic type in namespace
        if imported_generic_name and imported_generic_module and (
            lookup := builtin_modules.lookup_generic_type_in_module(imported_generic_name, imported_generic_module)
        ):
            record_info = self.ctx.registry.get_builtin_record(lookup.qualified_name)
            if not record_info:
                raise self.ctx.error(f"Unknown type '{expr.func_name}'", expr)
            params = ", ".join(record_info.type_params)

            # Check for constructors that can infer type from arguments
            if expr.args:
                arg_types = [unwrap_own(unwrap_ref_type(self.expr.analyze_expr(arg))) for arg in expr.args]
                init_overloads = record_info.get_method_overloads("__init__")
                if init_overloads:
                    for ctor in init_overloads:
                        if len(ctor.params) != len(arg_types):
                            continue
                        # Try to match and infer type parameters
                        inferred_params = self.type_ops.match_generic_constructor(ctor.params, arg_types)
                        if inferred_params is not None:
                            # Use type_factory to create the result type
                            if (all(p in inferred_params for p in record_info.type_params)
                                    and record_info.type_factory):
                                factory_args = []
                                for p in record_info.type_params:
                                    t = inferred_params[p]
                                    if isinstance(t, IntLiteralType):
                                        t = self.ctx.default_int_for_literal(t)
                                    # Constructors produce owned storage -- strip Ref
                                    t = unwrap_ref_type(t)
                                    factory_args.append(t)
                                result_type = record_info.type_factory(*factory_args)
                                expr.call_type = result_type
                                if isinstance(result_type, PtrType):
                                    self._validate_ptr_constructor(expr)
                                if ctor.cpp_template or ctor.native_function:
                                    # Use extract_type_params on the clean result_type
                                    # (Ref already stripped), not inferred_params which
                                    # may contain val_or_ref wrappers.
                                    clean_params = extract_type_params(result_type)
                                    expr.resolved_function_info = _resolve_cpp_template_type_params(
                                        ctor, clean_params, result_type=result_type)
                                self._validate_lvalue_params(expr)
                                self._check_ctor_arg_compatibility(expr, ctor, arg_types, inferred_params)
                                return result_type
                            # T not inferred from args; try assignment target hint
                            if record_info.type_factory and self.ctx.expr_type_hint is not None:
                                hint = self.ctx.expr_type_hint
                                if isinstance(hint, OwnType):
                                    hint = hint.wrapped
                                hint = unwrap_readonly(hint)
                                if hint.qualified_name() == lookup.qualified_name:
                                    expr.call_type = hint
                                    if ctor.cpp_template or ctor.native_function:
                                        hint_params = extract_type_params(hint)
                                        expr.resolved_function_info = _resolve_cpp_template_type_params(
                                            ctor, hint_params, result_type=hint)
                                    self._validate_lvalue_params(expr)
                                    self._check_ctor_arg_compatibility(expr, ctor, arg_types, inferred_params)
                                    return hint

                # Show specific error when a non-literal type with element info
                # can't match any constructor (e.g., Range passed to list())
                if (len(arg_types) == 1
                        and not isinstance(arg_types[0], PendingListType)
                        and arg_types[0].get_element_type() is not None):
                    raise self.ctx.error(
                        f"{expr.func_name}() cannot be constructed from {arg_types[0]}",
                        expr
                    )
                raise self.ctx.error(
                    f"Cannot infer element type for {expr.func_name}() from these arguments; "
                    f"use {expr.func_name}[{params}]() or provide a type annotation",
                    expr
                )
            # list() with no args and no context hint -- create empty list with
            # unknown element type (same as []) if in function scope.
            if (expr.func_name == "list"
                    and isinstance(self.ctx.current_function, TpyFunction)
                    and record_info.type_factory):
                literal_id = self.ctx.literal_counter
                self.ctx.literal_counter += 1
                info = ListLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    element_type=UNKNOWN_ELEMENT,
                    size=0,
                    is_mutated=True,
                )
                self.ctx.list_literals[literal_id] = info
                self.ctx.pending_resolutions.append(literal_id)
                result_type = PendingListType(UNKNOWN_ELEMENT, 0, literal_id)
                expr.call_type = result_type
                return result_type
            # dict() with no args -- create empty dict with unknown key/value types.
            if (expr.func_name == "dict"
                    and isinstance(self.ctx.current_function, TpyFunction)
                    and record_info.type_factory):
                literal_id = self.ctx.literal_counter
                self.ctx.literal_counter += 1
                info = DictLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    key_type=UNKNOWN_ELEMENT,
                    value_type=UNKNOWN_ELEMENT,
                )
                self.ctx.dict_literals[literal_id] = info
                self.ctx.pending_dict_resolutions.append(literal_id)
                result_type = PendingDictType(UNKNOWN_ELEMENT, UNKNOWN_ELEMENT, literal_id)
                expr.call_type = result_type
                return result_type
            # set() with no args -- create empty set with unknown element type.
            if (expr.func_name == "set"
                    and isinstance(self.ctx.current_function, TpyFunction)
                    and record_info.type_factory):
                literal_id = self.ctx.literal_counter
                self.ctx.literal_counter += 1
                info = SetLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    element_type=UNKNOWN_ELEMENT,
                )
                self.ctx.set_literals[literal_id] = info
                self.ctx.pending_set_resolutions.append(literal_id)
                result_type = PendingSetType(UNKNOWN_ELEMENT, literal_id)
                expr.call_type = result_type
                return result_type
            raise self.ctx.error(
                f"Cannot infer element type for {expr.func_name}(); "
                f"use {expr.func_name}[{params}](), provide a type annotation, or pass an iterable",
                expr
            )

        if self.ctx.in_nested_def and expr.func_name == self.ctx.nested_def_name:
            raise self.ctx.error(
                f"Recursive nested functions are not supported. "
                f"'{expr.func_name}' cannot call itself",
                expr,
            )
        # Subscript callee fallback: name[expr](args) where name is unknown
        # as a function/type -- try as subscript expression callee
        if expr.subscript_callee is not None:
            return self._rewrite_subscript_callee(expr)
        raise self.ctx.error(f"Unknown function or type: '{expr.func_name}'", expr)

    def _analyze_special_builtin(
        self, expr: TpyCall, overloads: list[FunctionInfo],
    ) -> TpyType:
        """Handle builtin functions with special_handling=True."""
        qname = overloads[0].qualified_name
        if qname == "tpy.copy":
            return self._analyze_tpy_copy(expr)
        if qname == "tpy.copy_iter":
            return self._analyze_tpy_copy_iter(expr)
        if qname == "tpy.own_iter":
            return self._analyze_tpy_own_iter(expr)
        if qname == "tpy.try_parse":
            return self._analyze_tpy_try_parse(expr)
        if qname == "builtins.isinstance":
            return self._analyze_isinstance(expr)
        if qname == "builtins.print":
            for kw_name in expr.kwargs:
                if kw_name not in ("end", "sep"):
                    raise self.ctx.error(
                        f"print() does not support keyword argument '{kw_name}'", expr)
            for kw_name in ("end", "sep"):
                if kw_name in expr.kwargs:
                    if not isinstance(expr.kwargs[kw_name], TpyStrLiteral):
                        raise self.ctx.error(
                            f"print() '{kw_name}' argument must be a string literal", expr)
                    self.expr.analyze_expr(expr.kwargs[kw_name])
            for arg in expr.args:
                self.expr.analyze_expr(arg)
            expr.resolved_function_info = FunctionInfo(
                name="print",
                params=[],
                return_type=VOID,
                is_readonly=True,
                is_builtin_function=True,
                special_handling=True,
                qualified_name="builtins.print",
            )
            return VOID
        raise self.ctx.error(f"Unknown special builtin: '{qname}'", expr)

    def _get_module_function_overloads(self, module_name: str, func_name: str) -> list[FunctionInfo] | None:
        """Look up function overloads in a module using the unified registry."""
        module_info = self.ctx.registry.get_module(module_name)
        if module_info and func_name in module_info.functions:
            return module_info.functions[func_name]
        return None

    def _analyze_tpy_copy(self, expr: TpyCall) -> TpyType:
        """Analyze a call to tpy.copy() - explicit copy for ownership transfer.

        copy() is truly generic (works with any type T, returns Own[T]).
        This is handled specially because the module system doesn't support
        truly generic functions yet.
        """
        self._reject_kwargs_for_builtin(expr, "copy")
        if len(expr.args) != 1:
            raise self.ctx.error("copy() takes exactly 1 argument", expr)
        arg_type = self.expr.analyze_expr(expr.args[0])
        # Unwrap OwnType if already wrapped
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        # Use declared union type instead of narrowed member type so copy()
        # preserves the full union (e.g. copy(pet) where pet: Dog | Cat is
        # narrowed to Dog still returns Own[Dog | Cat])
        arg = expr.args[0]
        if isinstance(arg, TpyName) and self.ctx.current_scope:
            binding_type = self.ctx.current_scope.lookup(arg.name)
            if binding_type is not None:
                bt = binding_type.wrapped if isinstance(binding_type, OwnType) else binding_type
                if isinstance(bt, UnionType) and not isinstance(arg_type, UnionType):
                    arg_type = bt
        # @nocopy types cannot be copied
        if self.ctx.is_type_nocopy(arg_type):
            reason = self.ctx.nocopy_reason(arg_type)
            raise self.ctx.error(
                f"Cannot copy {reason}. "
                f"Non-copyable values can only be moved (pass directly at last use).",
                expr,
            )
        expr.resolved_function_info = FunctionInfo(
            name="copy",
            params=[ParamInfo("x", arg_type)],
            return_type=OwnType(arg_type),
            is_readonly=True,
            is_builtin_function=True,
            qualified_name="tpy.copy",
        )
        return OwnType(arg_type)

    def _analyze_tpy_copy_iter(self, expr: TpyCall) -> TpyType:
        """Analyze a call to tpy.copy_iter() - explicit element copy acknowledgment.

        copy_iter(iterable) wraps an iterable and copies each element during
        iteration. Returns CopyIter[T] where T is the element type.
        Suppresses the bulk copy warning on extend(), list(), etc.
        """
        self._reject_kwargs_for_builtin(expr, "copy_iter")
        if len(expr.args) != 1:
            raise self.ctx.error("copy_iter() takes exactly 1 argument", expr)
        arg_type = self.expr.analyze_expr(expr.args[0])
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        elem_type = arg_type.get_iteration_element_type()
        if elem_type is None:
            raise self.ctx.error(
                f"copy_iter() argument must be iterable, got {arg_type}", expr)
        # copy_iter produces owned copies -- strip Ref
        elem_type = unwrap_ref_type(elem_type)
        result_type = CopyIterType(elem_type)
        expr.resolved_function_info = FunctionInfo(
            name="copy_iter",
            params=[ParamInfo("x", arg_type)],
            return_type=result_type,
            is_readonly=True,
            is_builtin_function=True,
            qualified_name="tpy.copy_iter",
            return_borrows_from=frozenset({0}),
        )
        return result_type

    def _analyze_tpy_own_iter(self, expr: TpyCall) -> TpyType:
        """Analyze a call to tpy.own_iter() - consuming iteration.

        own_iter(container) moves the container into an OwnIter that
        iterates with move semantics. Returns OwnIter[T].
        The argument must be at its last use (movable), since own_iter
        takes ownership of the container via std::move.
        """
        self._reject_kwargs_for_builtin(expr, "own_iter")
        if len(expr.args) != 1:
            raise self.ctx.error("own_iter() takes exactly 1 argument", expr)
        arg_type = self.expr.analyze_expr(expr.args[0])
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        if not isinstance(arg_type, ListType):
            raise self.ctx.error(
                f"own_iter() currently only supports list, got {arg_type}", expr)
        # Validate that the argument is at last use -- own_iter moves
        # the container, so using it afterwards is use-after-move.
        arg = expr.args[0]
        if isinstance(arg, TpyName):
            is_last_use = id(arg) in self.ctx.all_last_uses
            is_movable = self.compat._is_owned_var(arg.name)
            if not is_last_use or not is_movable:
                self.ctx.warning(
                    f"own_iter() consumes '{arg.name}' -- "
                    f"using it afterwards is undefined behavior. "
                    f"Use a regular for-loop if the container is needed later.",
                    expr,
                )
            else:
                self.compat.check_own_consumption(arg)
        elem_type = arg_type.get_iteration_element_type()
        assert elem_type is not None
        result_type = OwnIterType(elem_type)
        expr.resolved_function_info = FunctionInfo(
            name="own_iter",
            params=[ParamInfo("x", arg_type)],
            return_type=result_type,
            is_readonly=True,
            is_builtin_function=True,
            qualified_name="tpy.own_iter",
        )
        return result_type

    def _analyze_tpy_try_parse(self, expr: TpyCall) -> TpyType:
        """Analyze try_parse(EnumType, str) -> Optional[EnumType]."""
        self._reject_kwargs_for_builtin(expr, "try_parse")
        if len(expr.args) != 2:
            raise self.ctx.error(
                "try_parse() takes exactly 2 arguments: try_parse(EnumType, name)",
                expr,
            )
        first_arg = expr.args[0]
        if not isinstance(first_arg, TpyName):
            raise self.ctx.error(
                "try_parse() first argument must be an enum type name",
                expr,
            )
        # Resolve the name to an enum type
        if self.ctx.current_ns is None:
            raise self.ctx.error(
                "try_parse() first argument must be an enum type name",
                expr,
            )
        binding = self.ctx.current_ns.lookup(first_arg.name)
        if binding is None or binding.kind != BindingKind.ENUM:
            raise self.ctx.error(
                f"try_parse() first argument must be an enum type, "
                f"got '{first_arg.name}'",
                expr,
            )
        enum_type = binding.enum_type
        # Analyze second arg and check it's a string
        arg_type = self.expr.analyze_expr(expr.args[1])
        if not is_any_str_type(arg_type):
            raise self.ctx.error(
                f"try_parse() second argument must be a string, got '{arg_type}'",
                expr,
            )
        expr.enum_try_parse = enum_type
        expr.resolved_function_info = FunctionInfo(
            name="try_parse",
            params=[],
            return_type=OptionalType(enum_type),
            is_builtin_function=True,
            special_handling=True,
            qualified_name="tpy.try_parse",
        )
        return OptionalType(enum_type)

    def _resolve_isinstance_type(self, name: str, expr: TpyCall) -> TpyType:
        """Resolve a type name used as the second argument to isinstance().

        Handles user-defined records and builtin type names (int, str, bool, float,
        fixed-int types like Int32, etc.).
        """
        # User-defined records
        record = self.ctx.registry.get_record(name)
        if record:
            return NamedType(name)
        # Builtin type names
        from tpyc.modules import _resolve_concrete_type_name
        resolved = _resolve_concrete_type_name(name)
        if resolved is not None:
            return resolved
        # bool is not in _resolve_concrete_type_name -- check directly
        if name == "bool":
            return BOOL
        raise self.ctx.error(f"isinstance() second argument must be a type, got '{name}'", expr)

    def _analyze_isinstance(self, expr: TpyCall) -> TpyType:
        """Analyze isinstance(x, T) for union type narrowing or protocol checks.

        Supports two modes:
        1. Union narrowing: isinstance(x, MemberType) where x has a union type
        2. Protocol check: isinstance(x, Protocol) where x is a protocol-typed
           template parameter -- compiles to if constexpr (Concept<T_x>)

        Sets isinstance_var and isinstance_type on the TpyCall node.
        """
        self._reject_kwargs_for_builtin(expr, "isinstance")
        if len(expr.args) != 2:
            raise self.ctx.error(
                f"isinstance() takes exactly 2 arguments, got {len(expr.args)}", expr
            )

        first_arg = expr.args[0]
        if not isinstance(first_arg, TpyName):
            raise self.ctx.error(
                "isinstance() first argument must be a variable name", expr
            )

        self.expr.analyze_expr(first_arg)

        # Check if second arg is a static protocol name
        second_arg = expr.args[1]
        if isinstance(second_arg, TpyName):
            protocol_info = self.ctx.registry.get_protocol(second_arg.name)
            if protocol_info is not None and not protocol_info.is_dynamic:
                return self._analyze_isinstance_protocol(
                    expr, first_arg, second_arg.name, protocol_info
                )

        effective_type = self.expr.narrowing.effective_union_type(first_arg.name)
        if isinstance(effective_type, OwnType):
            effective_type = effective_type.wrapped

        if not isinstance(effective_type, UnionType):
            raise self.ctx.error(
                f"isinstance() is only supported on union types, "
                f"got '{effective_type}'",
                expr
            )

        # Second arg: resolve as type name (not an expression)
        second_arg = expr.args[1]
        if not isinstance(second_arg, TpyName):
            raise self.ctx.error(
                "isinstance() second argument must be a type name", expr
            )

        resolved_type = self._resolve_isinstance_type(second_arg.name, expr)

        # Check that the resolved type is a member of the union
        if not any(m == resolved_type for m in effective_type.members):
            raise self.ctx.error(
                f"Type '{resolved_type}' is not a member of union '{effective_type}'",
                expr
            )

        expr.isinstance_var = first_arg.name
        expr.isinstance_type = resolved_type
        expr.resolved_function_info = self._isinstance_function_info()
        return BOOL

    def _analyze_isinstance_protocol(
        self, expr: TpyCall, first_arg: TpyName, protocol_name: str,
        protocol_info: 'ProtocolInfo',
    ) -> TpyType:
        """Analyze isinstance(x, Protocol) for compile-time protocol checks.

        Validates that the first argument is a protocol-typed parameter (template
        param in C++). Generates if constexpr (Concept<T_x>) at codegen time.
        """
        var_type = self.ctx.get_expr_type(first_arg)
        if var_type is not None:
            var_type = unwrap_readonly(var_type)
        if var_type is None:
            raise self.ctx.error(
                "isinstance() with a protocol requires a protocol-typed parameter, "
                "but the variable type could not be resolved",
                expr
            )

        # Unwrap Optional[Protocol] -> Protocol (isinstance narrows away None)
        unwrapped_optional = False
        if isinstance(var_type, OptionalType) and is_protocol_type(var_type.inner):
            var_type = var_type.inner
            unwrapped_optional = True

        # Accept both plain protocol params and protocol union params
        if is_protocol_type(var_type):
            # For Optional[P], isinstance can only narrow away None -- checking
            # a different protocol is nonsensical (codegen emits a null guard,
            # not a concept check).
            if unwrapped_optional and var_type.name != protocol_name:
                raise self.ctx.error(
                    f"isinstance() checks protocol '{protocol_name}', "
                    f"but variable is typed as '{var_type} | None'",
                    expr
                )
            type_args = var_type.type_args if isinstance(var_type, NamedType) and var_type.name == protocol_name else None
            protocol_type = NamedType(protocol_name, is_protocol=True, type_args=type_args)
        elif is_protocol_union(var_type):
            # Find the matching member in the union, preserving type_args
            members = protocol_union_protocols(var_type)
            matched = None
            for m in members:
                if isinstance(m, NamedType) and m.name == protocol_name:
                    matched = m
                    break
            if matched is None:
                member_names = ", ".join(m.name for m in members if isinstance(m, NamedType))
                raise self.ctx.error(
                    f"Protocol '{protocol_name}' is not a member of the protocol union "
                    f"({member_names})",
                    expr
                )
            protocol_type = NamedType(
                protocol_name, is_protocol=True, type_args=matched.type_args
            )
        else:
            raise self.ctx.error(
                f"isinstance() with a protocol requires a protocol-typed parameter, "
                f"got '{var_type}'",
                expr
            )

        expr.isinstance_var = first_arg.name
        expr.isinstance_type = protocol_type
        expr.isinstance_is_protocol = True
        expr.resolved_function_info = self._isinstance_function_info()
        return BOOL

    @staticmethod
    def _isinstance_function_info() -> FunctionInfo:
        return FunctionInfo(
            name="isinstance",
            params=[],
            return_type=BOOL,
            is_readonly=True,
            is_builtin_function=True,
            special_handling=True,
            qualified_name="builtins.isinstance",
        )

    def _analyze_enum_from_value(self, expr: TpyCall, enum_type: EnumType) -> TpyType:
        """Analyze enum value lookup: Color(0) -> Color."""
        self._reject_kwargs_for_builtin(expr, enum_type.name)
        if len(expr.args) != 1:
            raise self.ctx.error(
                f"Enum '{enum_type.name}' constructor takes exactly 1 argument, "
                f"got {len(expr.args)}",
                expr
            )
        arg_type = self.expr.analyze_expr(expr.args[0])
        if not isinstance(arg_type, (IntLiteralType, FixedIntType, BigIntType)):
            raise self.ctx.error(
                f"Cannot construct '{enum_type.name}' from '{arg_type}', "
                f"expected an integer type",
                expr
            )
        expr.enum_from_value = enum_type
        return enum_type

    def _is_nocopy_type(self, typ: TpyType) -> bool:
        """Check if a type is @nocopy (move-only, copy deleted).

        Delegates to the canonical is_type_nocopy() which handles wrappers,
        generic type arguments, and the __copy__ escape hatch.
        """
        return self.ctx.is_type_nocopy(typ)

    def _check_own_param_arg(self, arg: TpyExpr, arg_type: TpyType,
                              pname: str, ptype: OwnType) -> None:
        """Check lvalue passed to Own[T] param -- auto-move at last use or error.

        Rvalue expressions (calls, literals, etc.) produce temporaries that
        bind directly to the value param, so no check is needed.
        Unresolved TypeParamRef (generic T) can't be checked at analysis time.
        """
        if not isinstance(arg, TpyName):
            return
        if isinstance(arg_type, TypeParamRef):
            return
        is_last_use_movable = (isinstance(arg, TpyName)
                               and id(arg) in self.ctx.all_last_uses
                               and self.compat._is_owned_var(arg.name))
        if is_last_use_movable:
            self.compat.check_own_consumption(arg)
            return
        if self._is_nocopy_type(arg_type):
            reason = self.ctx.nocopy_reason(arg_type)
            is_movable = (isinstance(arg, TpyName)
                          and self.compat._is_owned_var(arg.name))
            if is_movable:
                # Movable owner, but not at last use (used later)
                raise self.ctx.error(
                    f"{reason} is used after this point "
                    f"and cannot be moved into '{pname}: Own[{ptype.wrapped}]'. "
                    f"Remove later uses or restructure the code.",
                    arg
                )
            # Not movable (T& alias, readonly param, etc.)
            raise self.ctx.error(
                f"{reason} cannot be copied into "
                f"'{pname}: Own[{ptype.wrapped}]'. "
                f"Only the original owner can be moved at its last use.",
                arg
            )
        # Non-nocopy implicit copy: warning is emitted by the coercion
        # path in compatibility.py (T -> Own[T] coercion), so no need
        # to duplicate it here.

    def _warn_unnecessary_copy(self, arg: TpyExpr) -> None:
        """Warn when copy(x) is passed to Own[T] param but x is at last use."""
        if not (isinstance(arg, TpyCall) and len(arg.args) == 1
                and arg.resolved_function_info
                and arg.resolved_function_info.qualified_name == "tpy.copy"):
            return
        inner = arg.args[0]
        if (isinstance(inner, TpyName)
                and id(inner) in self.ctx.all_last_uses
                and self.compat._is_owned_var(inner.name)):
            self.compat.check_own_consumption(arg)
            self.ctx.warning(
                f"unnecessary copy() -- '{inner.name}' is at its last use and would be moved automatically",
                arg,
            )

    def check_own_param(self, arg: TpyExpr, arg_type: TpyType,
                        pname: str, ptype: TpyType) -> None:
        """Run Own[T] / Own[T]|None param checks: @nocopy error and unnecessary-copy warning.

        Handles both bare Own[T] and Own[T] | None parameter types.
        Call this for every parameter that might be ownership-taking.
        Non-nocopy implicit copies are warned by the coercion path.
        """
        own_ptype = unwrap_optional_own(ptype)
        if own_ptype is None:
            return
        # Unwrap OwnType from name lookup (implicit owned local) for the check.
        # Explicit OwnType (from function return, not a name) means ownership
        # was already acknowledged -- skip the check.
        check_type = arg_type
        if isinstance(arg_type, OwnType):
            if isinstance(arg, TpyName):
                check_type = arg_type.wrapped  # implicit Own from local
            else:
                # Explicit Own from function return -- ownership acknowledged
                self.compat.check_own_consumption(arg)
                self._warn_unnecessary_copy(arg)
                return
        if not check_type.is_value_type():
            self._check_own_param_arg(arg, check_type, pname, own_ptype)
        # Own[T] local passed to Own[T] param — mark consumption
        if isinstance(arg_type, OwnType):
            self.compat.check_own_consumption(arg)
        self._warn_unnecessary_copy(arg)

    def _derive_ctor_arg_hints(self, expr: TpyCall) -> list[TpyType | None]:
        """Derive per-argument type hints from __init__ param types.

        For generic constructors like dict[str, str|Int32]([("a","b"), ("c",1)]),
        resolves __init__ param types with the known type params to produce
        concrete hints (e.g. list[tuple[str, str|Int32]]).  Falls back to
        call_type when no __init__ overloads are found.
        """
        fallback = [expr.call_type] * len(expr.args)
        if not expr.call_type or not expr.args:
            return fallback

        lookup = builtin_modules.lookup_generic_type(expr.func_name)
        if lookup is None:
            qname = expr.call_type.qualified_name()
            if qname:
                rec = self.ctx.registry.get_builtin_record(qname)
                if rec and rec.type_params and rec.type_factory:
                    lookup = builtin_modules.GenericTypeLookup(None, qname)
        if lookup is None:
            return fallback
        record_info = self.ctx.registry.get_builtin_record(lookup.qualified_name)
        if not record_info:
            return fallback

        inferred = extract_type_params(expr.call_type)
        if not inferred:
            return fallback

        nargs = len(expr.args)
        hints: list[TpyType | None] = []
        for ctor in record_info.get_method_overloads("__init__"):
            if len(ctor.params) != nargs:
                continue
            for p, arg in zip(ctor.params, expr.args):
                resolved = self.type_ops.substitute_type_params(p.type, inferred)
                hint = self._concrete_hint_from_param(resolved)
                hints.append(hint if hint is not None else expr.call_type)
            break
        if not hints:
            return fallback
        # If the derived hint type doesn't match the arg expression
        # (e.g. ListType hint for a dict literal arg), fall back to call_type
        for i, (hint, arg) in enumerate(zip(hints, expr.args)):
            if isinstance(hint, ListType) and isinstance(arg, (TpyDictLiteral, TpySetLiteral)):
                hints[i] = expr.call_type
        return hints

    @staticmethod
    def _concrete_hint_from_param(param_type: TpyType) -> TpyType | None:
        """Extract a concrete ListType hint from a protocol param type.

        analyze_expr_with_hint dispatches on isinstance(hint, ListType),
        so protocol params like Iterable[Own[tuple[K,V]]] must be converted
        to ListType(tuple[K,V]) for element-level hints to propagate.
        """
        param_type = unwrap_ref_type(param_type)
        if not is_protocol_type(param_type):
            return None
        elem = param_type.get_iteration_element_type()
        if elem is None:
            return None
        # Unwrap Own -- container elements are owned by value
        if isinstance(elem, OwnType):
            elem = elem.wrapped
        return ListType(elem)

    def _validate_generic_constructor(self, expr: TpyCall, arg_types: list[TpyType]) -> None:
        """Validate and resolve generic type constructor calls.

        When call_type is set (e.g., list[int](iterable)), checks that args
        conform to constructor params and sets resolved_function_info for codegen.
        For params with TypeParamRef (e.g. Span[T]), structural compatibility is
        checked (arg must be a container with matching element type) even though
        T itself is unresolved.
        """
        lookup = builtin_modules.lookup_generic_type(expr.func_name)
        if lookup is None:
            # Try looking up via call_type's qualified name (for types from submodules)
            qname = expr.call_type.qualified_name() if expr.call_type else None
            if qname:
                rec = self.ctx.registry.get_builtin_record(qname)
                if rec and rec.type_params and rec.type_factory:
                    lookup = builtin_modules.GenericTypeLookup(None, qname)
        record_info = self.ctx.registry.get_builtin_record(lookup.qualified_name) if lookup else None
        if not record_info:
            return
        init_overloads = record_info.get_method_overloads("__init__")
        if not init_overloads:
            return
        inferred = extract_type_params(expr.call_type) if expr.call_type else {}
        for ctor in init_overloads:
            if len(ctor.params) != len(arg_types):
                continue
            rejected = False
            fully_checked = True
            for p, at in zip(ctor.params, arg_types):
                p_type = unwrap_ref_type(p.type)
                if is_protocol_type(p_type):
                    if not self.protocols.type_extends_any_protocol(at, p_type.name):
                        rejected = True
                        break
                    # Protocol matches structurally, but if it has type params
                    # (e.g. Iterable[T]), verify element type compatibility.
                    # Resolve param type params to get the actual expected
                    # element (e.g. tuple[K,V] for dict, not just V).
                    if _has_type_param_ref(p_type) and expr.call_type and inferred:
                        resolved_param = self.type_ops.substitute_type_params(p_type, inferred)
                        expected_elem = resolved_param.get_iteration_element_type()
                        if isinstance(expected_elem, OwnType):
                            expected_elem = expected_elem.wrapped
                        arg_elem = at.get_iteration_element_type()
                        if expected_elem is not None and arg_elem is not None:
                            if not self.compat.is_type_compatible(arg_elem, expected_elem):
                                rejected = True
                                break
                elif _has_type_param_ref(p_type):
                    # Can't fully resolve T, but reject clearly incompatible
                    # types. For Span[T]: arg must have an element type, and
                    # if T is known from call_type, element types must match.
                    if isinstance(p_type, SpanType):
                        arg_elem = at.get_element_type()
                        if arg_elem is None:
                            rejected = True
                            break
                        if not p_type.is_readonly and isinstance(at, SpanType) and at.is_readonly:
                            rejected = True
                            break
                        expected_elem = expr.call_type.get_element_type() if expr.call_type else None
                        if expected_elem is not None and not type_matches_numeric(arg_elem, expected_elem):
                            rejected = True
                            break
                    fully_checked = False
                elif not type_matches_numeric(at, p_type):
                    rejected = True
                    break
            if not rejected:
                if fully_checked and (ctor.cpp_template or ctor.native_function):
                    expr.resolved_function_info = _resolve_cpp_template_type_params(
                        ctor, inferred, result_type=expr.call_type)
                return
        # Arg type compatible with target (e.g. dict[K,V]({...}), list[T](other_list))
        if len(arg_types) == 1 and self.compat.is_type_compatible(arg_types[0], expr.call_type):
            return
        # No constructor matched -- emit error for single-arg case
        if len(arg_types) == 1:
            raise self.ctx.error(
                f"{expr.func_name}() cannot be constructed from {arg_types[0]}",
                expr
            )

    def _check_ctor_arg_compatibility(
        self,
        expr: TpyCall,
        ctor: FunctionInfo,
        arg_types: list[TpyType],
        inferred: dict[str, TpyType],
    ) -> None:
        """Check copy/ownership warnings for constructor args (e.g., Own[T] in Iterable[Own[T]])."""
        for param, arg_type, arg_expr in zip(ctor.params, arg_types, expr.args):
            param_type = self.type_ops.substitute_type_params(param.type, inferred)
            self.compat.check_type_compatible(
                arg_type, param_type, f"{expr.func_name}() argument", source_expr=arg_expr,
            )

    def _validate_lvalue_params(self, expr: TpyCall) -> None:
        """Validate requires_mutable_lvalue constraints on resolved params."""
        fi = expr.resolved_function_info
        if fi is None:
            return
        for i, param in enumerate(fi.params):
            if i >= len(expr.args):
                break
            if param.requires_mutable_lvalue:
                if not self.compat.is_mutable_lvalue(expr.args[i]):
                    raise self.ctx.error(
                        f"argument '{param.name}' must be a mutable lvalue", expr)
                # Address-taking requires T& -- mark params and loop vars as mutated.
                for name in addr_taken_roots(expr.args[i]):
                    root = self.ctx.borrow_tracker.effective_storage(name)
                    self.ctx.mark_param_mutated(root)
                    self.ctx.mark_loop_var_mutated(root)

    def _check_borrow_arg_conflicts(self, expr: TpyCall | TpyMethodCall) -> None:
        """Warn when a borrowed container is passed to a non-readonly parameter.

        A non-pure, non-readonly function receiving a container by mutable
        reference could structurally mutate it, invalidating element borrows.
        Resolves aliases so that passing an alias of a borrowed container warns.
        Also marks str_source_borrows as mutated for string view fallback.

        Uses structural_mutated_params (append/insert/clear/etc.) when available,
        falling back to mutated_params for external/builtin functions.

        During sema, mutated_params holds direct facts only (Phase 1). If the
        param is directly mutated, we emit immediately. If not directly mutated
        but the callee has call edges (transitive mutation possible), we defer
        the check until Phase 2 resolves the final facts.
        """
        fi = expr.resolved_function_info
        if fi is None or fi.is_pure or fi.is_readonly:
            return
        # Use structural_mutated_params when available (precise); fall back to mutated_params.
        # structural_mutated_params is set for all locally-analyzed functions (None for builtins/imports).
        effective_mp = (fi.structural_mutated_params
                        if fi.structural_mutated_params is not None
                        else fi.mutated_params)
        effective_direct = (fi.direct_structural_mutated_params
                            if fi.direct_structural_mutated_params is not None
                            else fi.direct_mutated_params)
        for i, param in enumerate(fi.params):
            if i >= len(expr.args):
                break
            arg = expr.args[i]
            if not isinstance(arg, TpyName):
                continue
            # readonly[T] param -- function promises not to mutate
            if isinstance(param.type, ReadonlyType):
                continue
            storage = self.ctx.borrow_tracker.effective_storage(arg.name)
            needs_check = self.ctx.borrow_tracker.has_element_borrow(storage)
            if effective_mp is not None and i not in effective_mp:
                # Direct facts say "not structurally mutated". If callee has call edges
                # and Phase 2 hasn't finalized yet, transitive propagation
                # might still add this param -- defer.
                if needs_check and fi.call_edges and effective_direct is not None:
                    loc = getattr(expr, 'loc', None)
                    self.pending_borrow_checks.append((fi, i, storage, loc))
                if fi.call_edges:
                    self.ctx.mark_all_view_borrowers_mutated(storage)
                continue
            if effective_mp is None and effective_direct is None:
                # Callee not yet analyzed (forward call) -- defer to Phase 2
                if needs_check:
                    loc = getattr(expr, 'loc', None)
                    self.pending_borrow_checks.append((fi, i, storage, loc))
                self.ctx.mark_all_view_borrowers_mutated(storage)
                continue
            if needs_check:
                self.ctx.warning(
                    f"Passing borrowed container '{storage}' to non-readonly parameter "
                    f"'{param.name}' (function may invalidate references)",
                    expr,
                )
            self.ctx.mark_all_view_borrowers_mutated(storage)

    def _check_loop_var_arg_mutation(self, expr: TpyCall | TpyMethodCall) -> None:
        """Mark for-each loop variables as mutated when passed to non-readonly params."""
        fi = expr.resolved_function_info
        if fi is None or fi.is_readonly:
            return
        from .statements import _root_name_of_expr
        for i, param in enumerate(fi.params):
            if i >= len(expr.args):
                break
            arg = expr.args[i]
            if isinstance(param.type, ReadonlyType):
                continue
            # If callee is known not to mutate this param, skip
            if fi.mutated_params is not None and i not in fi.mutated_params:
                continue
            if not unwrap_readonly(param.type).is_value_type():
                arg_root = _root_name_of_expr(arg)
                if arg_root is not None:
                    self.ctx.mark_loop_var_mutated(arg_root)

    def _record_mutation_call_edges(self, expr: TpyCall | TpyMethodCall) -> None:
        """Record parameter flow through calls for Phase 2 mutation propagation."""
        fi = expr.resolved_function_info
        if fi is None or fi.is_readonly or fi.is_pure:
            return
        from .statements import _root_name_of_expr
        name_to_idx = self.ctx.current_param_name_to_idx
        rebound = self.ctx.current_rebound_params

        # Detect receivers rooted at self so Phase 2 can propagate self-mutation.
        # Covers: self.method(), self.field.method(), and loop_var.method()
        # where loop_var iterates over a self field.
        receiver_is_self = False
        if isinstance(expr, TpyMethodCall):
            obj = expr.obj
            # Walk through field access chain to find self at the root
            while isinstance(obj, TpyFieldAccess):
                obj = obj.obj
            if isinstance(obj, TpyName):
                if obj.name == "self":
                    receiver_is_self = True
                else:
                    # Loop variable iterating over self.field
                    iterable = self.ctx.loop_var_iterable.get(obj.name)
                    if iterable is not None:
                        root = iterable.split(".")[0] if "." in iterable else iterable
                        if root == "self":
                            receiver_is_self = True

        # Nothing to record if no params flow through and no self-call
        if not name_to_idx and not receiver_is_self:
            return

        param_map: dict[int, int] = {}
        for i, callee_param in enumerate(fi.params):
            if i >= len(expr.args):
                break
            if isinstance(callee_param.type, ReadonlyType):
                continue
            if unwrap_readonly(callee_param.type).is_value_type():
                continue
            arg = expr.args[i]
            arg_root = _root_name_of_expr(arg)
            if arg_root is None:
                continue
            # Resolve alias and element borrow chains to find the original param.
            # 8a.5: effective_storage_through_borrows also follows element/field/ptr
            # borrows so that mutating a call arg that element-borrows from a param
            # correctly traces back to the source param.
            resolved = self.ctx.borrow_tracker.effective_storage_through_borrows(arg_root)
            if resolved in name_to_idx and resolved not in rebound:
                param_map[i] = name_to_idx[resolved]
        if param_map or receiver_is_self:
            self.ctx.current_call_edges.append(
                MutationCallEdge(callee_fi=fi, param_map=param_map,
                                 receiver_is_self=receiver_is_self)
            )

    def _validate_ptr_constructor(self, expr: TpyCall) -> None:
        """Validate pointer constructor arguments (type match, no void args).

        Lvalue checking is handled generically by _validate_lvalue_params.
        """
        assert isinstance(expr.call_type, PtrType)
        pointee = expr.call_type.inner_pointee
        kind = "read-only pointer" if expr.call_type.is_readonly else "pointer"

        if len(expr.args) != 1:
            raise self.ctx.error(f"{kind} constructor takes 0 or 1 argument, got {len(expr.args)}", expr)

        arg = expr.args[0]
        arg_type = self.ctx.get_expr_type(arg)

        if isinstance(pointee, VoidType):
            raise self.ctx.error(f"{kind} to None does not accept arguments", expr)

        # Strip Ref from pointee: Ptr[T] where T was inferred as Ref[Point]
        # should match Point (Ref is provenance, not part of the pointee type).
        bare_pointee = unwrap_ref_type(pointee)
        if arg_type != bare_pointee:
            raise self.ctx.error(
                f"{kind} to {bare_pointee} expects {bare_pointee}, got {arg_type}", expr)
        # Mutable pointer to a for-each loop var prevents const-ref binding
        if not expr.call_type.is_readonly and isinstance(arg, TpyName):
            self.ctx.mark_loop_var_mutated(arg.name)

    def _analyze_template_constructor(self, expr: TpyCall, record: RecordInfo,
                                      init_overloads: list[FunctionInfo]) -> TpyType:
        """Analyze a constructor call for types with @cpp_template or @native __init__ overloads.

        Matches args against __init__ overloads using numeric compatibility and
        protocol-aware resolution. Handles special fallbacks for bool(__len__)
        and str(container).
        """
        self._reject_kwargs_for_builtin(expr, expr.func_name)
        arg_types = [unwrap_own(unwrap_ref_type(self.expr.analyze_expr(arg))) for arg in expr.args]

        # Resolve the concrete type (e.g. Float32Type). __init__ returns None
        # in Python, so we look up the actual type via the type factory.
        record_type = builtin_modules.get_builtin_type_obj(record.builtin_type_key)

        # Find a matching constructor overload
        for ctor in init_overloads:
            if len(ctor.params) != len(arg_types):
                continue
            if all(type_matches_numeric(arg_type, ptype)
                   for (pname, ptype), arg_type in zip(ctor.params, arg_types)):
                ret = record_type or ctor.return_type
                # Reject int literals that are out of range for the target fixed-int type
                if (isinstance(ret, FixedIntType) and len(arg_types) == 1
                        and isinstance(arg_types[0], IntLiteralType)):
                    lit = arg_types[0]
                    if lit.value is not None and not (ret.min_value <= lit.value <= ret.max_value):
                        raise self.ctx.error(
                            f"{ret} overflow: {lit.value} is outside range "
                            f"[{ret.min_value}, {ret.max_value}]",
                            expr,
                        )
                expr.resolved_function_info = _resolve_cpp_template_type_params(ctor, result_type=ret)
                self._check_cast_safe(expr, ctor, arg_types, ret)
                return ret

        # Fallback: try protocol-aware overload resolution (e.g. bool(obj) via Truthy)
        matched = resolve_overload(
            init_overloads, arg_types,
            protocol_checker=self.protocols.type_conforms_to_protocol,
            subclass_checker=self.ctx.registry.is_subclass_of,
        )
        if matched:
            ret = record_type or matched.return_type
            expr.resolved_function_info = _resolve_cpp_template_type_params(matched, result_type=ret)
            self._check_cast_safe(expr, matched, arg_types, ret)
            return ret

        # bool(obj) __len__ fallback: types with __len__ but no __bool__
        if record.builtin_type_key == qnames.BOOL and len(arg_types) == 1:
            arg_type = arg_types[0]
            if isinstance(arg_type, NamedType):
                arg_record = self.ctx.registry.get_record(arg_type.name)
                if arg_record and arg_record.get_method_overloads("__len__"):
                    expr.resolved_function_info = FunctionInfo(
                        name="__len__",
                        params=[ParamInfo("x", arg_type)],
                        return_type=BOOL,
                        cpp_template="(::tpy::__len__({0}) != 0)",
                        is_readonly=True,
                    )
                    return BOOL

        # str(container) fallback: containers have runtime to_str helpers
        if record.builtin_type_key == qnames.STR and len(arg_types) == 1:
            tmpl = container_to_str_template(unwrap_readonly(arg_types[0]))
            if tmpl is not None:
                expr.resolved_function_info = FunctionInfo(
                    name="str",
                    params=[ParamInfo("x", arg_types[0])],
                    return_type=STR,
                    cpp_template=tmpl,
                    is_readonly=True,
                )
                return STR

        # No matching overload found
        type_name = expr.func_name
        if not init_overloads:
            raise self.ctx.error(f"{type_name}() is not callable", expr)
        elif len(arg_types) == 0:
            raise self.ctx.error(f"{type_name}() requires an argument", expr)
        elif len(arg_types) == 1:
            raise self.ctx.error(f"{type_name}() cannot convert {arg_types[0]}", expr)
        else:
            raise self.ctx.error(f"{type_name}() takes at most 1 argument, got {len(arg_types)}", expr)

    def _check_cast_safe(self, expr: TpyCall, ctor: FunctionInfo,
                         arg_types: list[TpyType], target_type: TpyType | None = None) -> None:
        """Mark signed->unsigned casts as safe when value is provably non-negative.

        When the source is a signed int with lo >= 0 (from range tracking) and
        the target unsigned type is wide enough to hold all non-negative values
        of the source type, skip the runtime range check.

        Only applies when the argument is a simple variable name (TpyName);
        field accesses and sub-expressions are conservatively left checked.
        """
        if len(arg_types) != 1 or not ctor.cpp_template:
            return
        if "int_cast_check" not in ctor.cpp_template:
            return
        target = target_type or ctor.return_type
        source = arg_types[0]
        if not isinstance(target, FixedIntType) or not isinstance(source, FixedIntType):
            return
        arg = expr.args[0]
        if not isinstance(arg, TpyName):
            return
        # Safe when: signed -> unsigned, target at least as wide, source proven non-negative
        is_safe = (
            source.signed and not target.signed
            and target.bits >= source.bits
            and (rng := self.ctx.value_ranges.get(arg.name)) is not None
            and rng.is_non_negative()
        )
        if is_safe:
            safe_template = f"static_cast<{target.to_cpp()}>({{0}})"
            expr.resolved_function_info = dc_replace(ctor, cpp_template=safe_template)
        if expr.loc:
            self.ctx.cast_safe_facts[(expr.loc.line, str(target))] = is_safe

    def _validate_explicit_type_args(self, expr: TpyCall, max_type_params: int) -> None:
        """Validate explicit type arguments for a generic call.

        Checks parse errors, count bounds, protocol misuse, unknown types,
        and well-formedness. Used by both user-function and builtin-function
        generic call paths.
        """
        if expr.type_args_parse_error:
            raise self.ctx.error(expr.type_args_parse_error, expr)
        if len(expr.type_args) > max_type_params:
            raise self.ctx.error(
                f"Function '{expr.func_name}' expects {max_type_params} type argument(s), "
                f"got {len(expr.type_args)}",
                expr
            )
        for type_arg in expr.type_args:
            if type_arg is None:  # _ wildcard
                continue
            if is_protocol_type(type_arg):
                raise self.ctx.error(
                    f"Protocol type '{type_arg.name}' cannot be used as a type argument. "
                    f"Protocols are only valid as function and method parameters",
                    expr
                )
            if isinstance(type_arg, NamedType) and type_arg.is_record and not type_arg.type_args:
                if self.ctx.registry.get_record_for_type(type_arg) is None:
                    raise self.ctx.error(f"Unknown type: {type_arg.name}", expr)
            in_generic = bool(
                (isinstance(self.ctx.current_function, TpyFunction) and self.ctx.current_function.type_params)
                or self.ctx.record_ctx.type_params
            )
            self.type_ops.validate_type(type_arg, allow_type_param_ref=in_generic, loc=expr.loc, allow_forward_ref=False)

    def _infer_arg_types(self, expr: TpyCall, func: FunctionInfo) -> list[TpyType]:
        """Analyze args for type param inference, with two-phase for Fn/Callable params.

        When a generic function has Fn/Callable params (e.g. map[T,U](fn: Fn[[T],U], ...)),
        function refs and lambdas can't be analyzed without concrete type hints. We:
        1. Analyze non-Fn args first to get types for partial type param inference.
        2. Substitute inferred params into the Fn type to build concrete hints.
        3. Analyze the Fn args with those hints.
        """
        # Quick check: if no Fn/Callable params, analyze all args directly
        fn_positions: set[int] = set()
        for i, (_, ptype) in enumerate(func.params):
            if isinstance(unwrap_ref_type(ptype), (FnType, CallableType)):
                fn_positions.add(i)
        if not fn_positions:
            return [self.expr.analyze_expr(arg) for arg in expr.args]

        # Phase 1: analyze non-Fn args
        arg_types: list[TpyType | None] = [None] * len(expr.args)
        for i, arg in enumerate(expr.args):
            if i not in fn_positions:
                arg_types[i] = self.expr.analyze_expr(arg)

        # Phase 2: partial inference from known args, then resolve Fn args
        partial_inferred: dict[str, TpyType] = {}
        for (_, ptype), arg_type in zip(func.params, arg_types):
            if arg_type is not None:
                self.type_ops.match_type_with_inference(ptype, arg_type, partial_inferred)
        # Resolve IntLiteralType to concrete int for the Fn hint
        for k, v in partial_inferred.items():
            if isinstance(v, IntLiteralType):
                partial_inferred[k] = self.ctx.default_int_type or INT32
            elif isinstance(v, PendingViewType):
                partial_inferred[k] = v.family.owned_type

        if partial_inferred:
            for i in fn_positions:
                if i >= len(func.params) or i >= len(expr.args):
                    continue
                ptype = unwrap_ref_type(func.params[i].type)
                concrete_hint = _partial_substitute(ptype, partial_inferred)
                if isinstance(concrete_hint, (FnType, CallableType)):
                    has_unresolved = any(
                        _has_type_param_ref(p) for p in concrete_hint.param_types
                    )
                    if not has_unresolved:
                        arg_types[i] = self.expr.analyze_expr_with_hint(
                            expr.args[i], concrete_hint)

        # Fallback: analyze any remaining unresolved args without hints
        for i in range(len(arg_types)):
            if arg_types[i] is None:
                arg_types[i] = self.expr.analyze_expr(expr.args[i])

        return arg_types  # type: ignore[return-value]

    def _flatten_key_kwarg(self, expr: TpyCall, name: str) -> None:
        """Flatten key= kwarg to positional arg for sorted/min/max."""
        if not expr.kwargs:
            return
        if name not in ("sorted", "min", "max"):
            return
        bad = [k for k in expr.kwargs if k != "key"]
        if bad:
            raise self.ctx.error(
                f"'{name}()' does not support keyword argument '{bad[0]}'", expr)
        if "key" in expr.kwargs:
            key_arg = expr.kwargs["key"]
            if isinstance(key_arg, TpyLambda):
                key_arg.readonly_params = True
            expr.args.append(key_arg)
            expr.kwargs = {}

    def _analyze_builtin_function_overloads(self, expr: TpyCall, overloads: list[FunctionInfo]) -> TpyType:
        """Type-check a call to a builtin function using unified FunctionInfo overloads.

        Uses two-pass overload resolution: prefer exact type matches over coercion matches.
        For generic overloads (with type_params), uses type inference.
        """
        self._flatten_key_kwarg(expr, overloads[0].name)
        self._reject_kwargs_for_builtin(expr, overloads[0].name)
        protocol_checker = self.protocols.type_conforms_to_protocol

        # Build unified candidate pool: resolve generics to concrete candidates
        # so they compete with non-generic ones in the same scoring pool.
        non_generic = [o for o in overloads if not _has_type_param_ref_in_params(o)]
        generic = [o for o in overloads if _has_type_param_ref_in_params(o)]

        # For generic overloads with Fn/Callable params, use two-phase arg analysis
        # so function refs and lambdas can be resolved with concrete type hints.
        fn_generic = next((o for o in generic
                           if any(isinstance(unwrap_ref_type(p.type), (FnType, CallableType)) for p in o.params)
                           and len(expr.args) >= o.min_args and len(expr.args) <= o.max_args),
                          None)
        if fn_generic is not None:
            arg_types = self._infer_arg_types(expr, fn_generic)
        else:
            arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
        # Strip Ref/Own from arg types: builtin overloads are defined with bare
        # types, and Ref/Own are semantic annotations not type differences.
        arg_types = [unwrap_own(unwrap_ref_type(t)) for t in arg_types]

        # Validate explicit type args before generic inference
        if expr.type_args_parse_error:
            raise self.ctx.error(expr.type_args_parse_error, expr)
        explicit: tuple[TpyType, ...] | None = None
        if expr.type_args and generic:
            max_tp = max(len(o.type_params) for o in generic)
            self._validate_explicit_type_args(expr, max_tp)
            explicit = expr.type_args

        candidates = list(non_generic)
        generic_originals: dict[int, tuple[FunctionInfo, dict[str, TpyType]]] = {}
        for overload in generic:
            type_subst = self.type_ops.infer_type_params_for_function(
                overload, arg_types, protocol_checker,
                expected_return_type=self.ctx.expr_type_hint,
                explicit_type_args=explicit,
            )
            if type_subst is not None:
                # Resolve PendingViewType to owned type for builtin overloads
                # (codegen can't resolve these via view_vars like user functions)
                for k, v in type_subst.items():
                    if isinstance(v, PendingViewType):
                        type_subst[k] = v.family.owned_type
                n_explicit = len(explicit) if explicit else 0
                if n_explicit < len(overload.type_params):
                    prefer_strview_for_literals(type_subst, overload, expr.args,
                                               protocol_checker, n_explicit)
                resolved = self.type_ops.substitute_method_type_params(overload, type_subst)
                candidates.append(resolved)
                generic_originals[id(resolved)] = (overload, type_subst)

        # Unified resolution: score all candidates (non-generic + resolved generics)
        enriched_types = _enrich_literal_types(arg_types, expr.args, candidates)
        matched = resolve_overload(candidates, enriched_types, protocol_checker,
                                   deref_checker=self.type_ops.get_deref_coercion_target,
                                   default_int_type=self.ctx.default_int_type,
                                   subclass_checker=self.ctx.registry.is_subclass_of)
        if matched is not None:
            expr.resolved_function_info = matched
            self._validate_lvalue_params(expr)
            self._check_error_return_handled(expr, matched)
            self._record_mutation_call_edges(expr)
            for i, (arg, arg_t, (pname, ptype)) in enumerate(zip(expr.args, arg_types, matched.params)):
                self.check_own_param(arg, arg_t, pname, ptype)
                if arg_t != ptype:
                    expr.args[i] = self.compat.coerce_expr(arg, arg_t, ptype,
                                                            f"argument '{pname}'",
                                                            coercion_ctx=CoercionContext.ARG)
            generic_info = generic_originals.get(id(matched))
            if generic_info is not None:
                overload, type_subst = generic_info
                expr.inferred_type_args = tuple(
                    self._resolve_inferred_type_arg(type_subst[p])
                    for p in overload.type_params)
                if isinstance(overload.return_type, UnionType):
                    orig_count = len(overload.return_type.members)
                    resolved_ret = matched.return_type
                    resolved_count = len(resolved_ret.members) if isinstance(resolved_ret, UnionType) else 1
                    if resolved_count < orig_count:
                        raise self.ctx.error(
                            f"Generic union return type '{overload.return_type}' produces duplicate "
                            f"members with these type arguments (resolves to '{resolved_ret}')",
                            expr,
                        )
                ret = matched.return_type
                if (overload.is_builtin_function and overload.type_params
                        and isinstance(ret, OptionalType) and ret.force_pointer_repr):
                    ret = OptionalType(ret.inner)
                return ret
            return matched.return_type

        # repr() fallback for types without Representable protocol but with
        # known C++ __repr__ overloads (containers, primitives, optionals,
        # user records with operator<<)
        if expr.func_name == "repr" and len(arg_types) == 1:
            inner = unwrap_readonly(arg_types[0])
            tmpl = container_to_str_template(inner)
            if tmpl is None:
                tmpl = _repr_fallback_template(inner)
            if tmpl is not None:
                expr.resolved_function_info = FunctionInfo(
                    name="repr",
                    params=[ParamInfo("x", arg_types[0])],
                    return_type=STR,
                    cpp_template=tmpl,
                    is_readonly=True,
                    is_builtin_function=True,
                )
                return STR

        # No matching overload found - try to give a helpful error
        arg_type_strs = ", ".join(str(unwrap_own(t)) for t in arg_types)

        # For generic overloads, check for conflicting type parameter inference
        for overload in generic:
            if len(arg_types) < overload.min_args or len(arg_types) > overload.max_args:
                continue
            partial: dict[str, TpyType] = {}
            conflict_param = None
            for (pname, ptype), arg_t in zip(overload.params, arg_types):
                before = dict(partial)
                if not self.type_ops.match_type_with_inference(ptype, arg_t, partial):
                    # Find which type param conflicted
                    for tp in overload.type_params:
                        if tp in before:
                            expected = before[tp]
                            # Try to extract what this arg would infer
                            trial: dict[str, TpyType] = {}
                            self.type_ops.match_type_with_inference(ptype, arg_t, trial)
                            if tp in trial and trial[tp] != expected:
                                conflict_param = (tp, expected, trial[tp], pname)
                                break
                    break
            if conflict_param:
                tp_name, first_t, second_t, param_name = conflict_param
                raise self.ctx.error(
                    f"No matching overload for {expr.func_name}({arg_type_strs}): "
                    f"type parameter {tp_name} inferred as {first_t} and {second_t}",
                    expr
                )

        # Check for Ptr[readonly[T]] passed at a position where all overloads expect Ptr
        for i, arg_t in enumerate(arg_types):
            if is_readonly_ptr(arg_t):
                all_need_ptr_at_i = all(
                    i < len(o.params) and isinstance(o.params[i].type, PtrType) and not o.params[i].type.is_readonly
                    for o in overloads
                )
                if all_need_ptr_at_i:
                    raise self.ctx.error(
                        f"{expr.func_name}() requires a mutable pointer, got {arg_t}", expr
                    )

        # Check for bound violations on generic overloads (give specific error)
        for overload in generic:
            if not overload.type_param_bounds:
                continue
            inferred: dict[str, TpyType] = {}
            if explicit:
                for tp, ta in zip(overload.type_params, explicit):
                    inferred[tp] = ta
            for (_, ptype), arg_t in zip(overload.params, arg_types):
                self.type_ops.match_type_with_inference(ptype, arg_t, inferred)
            if self.ctx.expr_type_hint and not inferred:
                ret = overload.return_type.wrapped if isinstance(overload.return_type, OwnType) else overload.return_type
                exp = self.ctx.expr_type_hint.wrapped if isinstance(self.ctx.expr_type_hint, OwnType) else self.ctx.expr_type_hint
                self.type_ops.match_type_with_inference(ret, exp, inferred)
            for param_name, type_arg in inferred.items():
                if param_name in overload.type_param_bounds:
                    bound = overload.type_param_bounds[param_name]
                    if not protocol_checker(type_arg, bound):
                        raise self.ctx.error(
                            f"Type '{type_arg}' does not satisfy '{bound}' "
                            f"required by '{expr.func_name}'",
                            expr,
                        )

        raise self.ctx.error(f"No matching overload for {expr.func_name}({arg_type_strs})", expr)

    def _analyze_user_function_call(
        self, expr: TpyCall, func_infos: list[FunctionInfo],
    ) -> TpyType:
        """Analyze a call to a user-defined function (single or @overload group)."""
        if len(func_infos) > 1:
            # @overload group: resolve generic overloads to concrete candidates
            # so they compete with non-generic ones in the same scoring pool.
            # This ensures IntLiteralType preference (default_int) works across
            # generic and non-generic overloads.
            # For generic overloads with Fn/Callable params, use two-phase analysis.
            fn_generic = next((f for f in func_infos
                               if f.is_generic()
                               and any(isinstance(p.type, (FnType, CallableType)) for p in f.params)),
                              None)
            if fn_generic is not None:
                arg_types = self._infer_arg_types(expr, fn_generic)
            else:
                arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]

            # Build candidate pool: non-generic originals + resolved generics
            candidates = []
            generic_originals: dict[int, FunctionInfo] = {}
            for func in func_infos:
                if func.is_generic():
                    type_subst = self.type_ops.infer_type_params_for_function(
                        func, arg_types, self.protocols.type_conforms_to_protocol,
                        expected_return_type=self.ctx.expr_type_hint,
                    )
                    if type_subst is not None:
                        resolved = self.type_ops.substitute_method_type_params(func, type_subst)
                        candidates.append(resolved)
                        generic_originals[id(resolved)] = func
                else:
                    candidates.append(func)

            enriched_types = _enrich_literal_types(arg_types, expr.args, candidates)
            matched = resolve_overload(
                candidates, enriched_types,
                protocol_checker=self.protocols.type_conforms_to_protocol,
                default_int_type=self.ctx.default_int_type,
                subclass_checker=self.ctx.registry.is_subclass_of,
            )
            if matched is not None:
                original = generic_originals.get(id(matched))
                if original is not None:
                    return self._analyze_single_function_call(expr, original)
                return self._analyze_single_function_call(expr, matched)

            # No match in unified pool. Fall back to original resolution
            # (structural matching for generics) to preserve error messages.
            matched = resolve_overload(
                func_infos, enriched_types,
                protocol_checker=self.protocols.type_conforms_to_protocol,
                subclass_checker=self.ctx.registry.is_subclass_of,
            )
            if matched is not None:
                return self._analyze_single_function_call(expr, matched)
            for func in func_infos:
                if func.is_generic():
                    try:
                        return self._analyze_single_function_call(expr, func)
                    except SemanticError:
                        continue
            arg_type_strs = ", ".join(str(unwrap_own(t)) for t in arg_types)
            raise self.ctx.error(
                f"No matching @overload for {expr.func_name}({arg_type_strs})", expr)
        return self._analyze_single_function_call(expr, func_infos[0])

    def _unsafe_cast_diagnostics(self, expr: TpyCall, arg_type: TpyType) -> None:
        """Targeted diagnostics for unsafe_cast when overload resolution fails."""
        if not isinstance(arg_type, PtrType):
            raise self.ctx.error(
                f"unsafe_cast() requires a pointer argument, got {arg_type}", expr)
        hint = self.ctx.expr_type_hint
        if hint is not None:
            raw_hint = hint
            if isinstance(hint, OwnType):
                hint = hint.wrapped
            hint = unwrap_readonly(hint)
            if not isinstance(hint, PtrType):
                raise self.ctx.error(
                    f"unsafe_cast() target must be a pointer type, got {raw_hint}", expr)
            if arg_type.is_readonly and not hint.is_readonly:
                raise self.ctx.error(
                    "unsafe_cast() cannot cast read-only pointer to mutable pointer (use unsafe_const_cast first)", expr)
        raise self.ctx.error(
            "unsafe_cast() requires a type argument or target type annotation "
            "(e.g., unsafe_cast[UInt32](p) or q: Ptr[UInt32] = unsafe_cast(p))", expr)

    def _analyze_single_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a call to a single user-defined function."""
        # Handle generic functions
        if func.is_generic():
            return self._analyze_generic_function_call(expr, func)

        # Reject type args on non-generic functions
        if expr.type_args or expr.type_args_parse_error:
            raise self.ctx.error(
                f"Function '{expr.func_name}' is not generic and does not accept type arguments",
                expr,
            )

        # Resolve kwargs before arity check
        self._resolve_call_kwargs(expr, func)

        expr.resolved_function_info = func
        if len(expr.args) < func.min_args or len(expr.args) > func.max_args:
            raise self.ctx.error(
                arity_error_msg(expr.func_name, func.min_args, func.max_args, len(expr.args)), expr)

        # Pack variadic args if function has *args
        if func.has_variadic:
            self._analyze_and_pack_varargs(expr, func)
        else:
            self._typecheck_call_args(expr, func)

        self._check_borrow_arg_conflicts(expr)
        self._check_loop_var_arg_mutation(expr)
        self._record_mutation_call_edges(expr)
        self._check_error_return_handled(expr, func)
        return func.return_type

    def _typecheck_call_args(self, expr: TpyCall, func: FunctionInfo) -> None:
        """Type-check call arguments against function parameters (non-variadic)."""
        # Reject *unpacking on non-variadic functions
        for arg in expr.args:
            if isinstance(arg, TpyStarUnpack):
                raise self.ctx.error(
                    f"Cannot use *unpacking: '{func.name}' does not accept *args", arg)
        for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
            arg_type = self.expr.analyze_expr_with_hint(arg, ptype)
            arg_type = self._restore_readonly_arg(arg, arg_type, func.is_readonly)

            if (isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType)
                    and not ptype.is_value_type() and not isinstance(arg, TpyName)):
                hint = "Assign to a variable first: x = func(); other_func(x)"
                raise self.ctx.error(
                    f"Cannot pass Own[{arg_type.wrapped}] directly to parameter '{pname}' "
                    f"(object types are passed by reference). {hint}",
                    arg
                )
            if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType):
                arg_type = arg_type.wrapped

            self.check_own_param(arg, arg_type, pname, ptype)

            if not (isinstance(ptype, CharType) and is_any_str_type(arg_type) and
                    isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                coerced_arg = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                       coercion_ctx=CoercionContext.ARG)
                expr.args[i] = coerced_arg

            if isinstance(arg_type, PENDING_CONTAINER_TYPES):
                self.deduction.mark_container_param_context(arg, arg_type, ptype)
            if isinstance(arg_type, PendingViewType):
                self.deduction.mark_view_param_context(arg, ptype, arg_type.family)

    def _analyze_and_pack_varargs(self, expr: TpyCall, func: FunctionInfo) -> None:
        """Analyze call with variadic params: type-check fixed args, pack trailing args."""
        # Find variadic param index and count keyword-only params after it
        va_idx = next(i for i, p in enumerate(func.params) if p.is_variadic)
        va_param = func.params[va_idx]
        n_kwonly = sum(1 for p in func.params if p.keyword_only)

        # Element type T from Span[readonly[T]]
        assert isinstance(unwrap_ref_type(va_param.type), SpanType)
        span_type = unwrap_ref_type(va_param.type)
        elem_type = span_type.inner_element_type

        # Split args: [fixed_positional...] [varargs...] [kwonly_defaults...]
        fixed_args = expr.args[:va_idx]
        kwonly_args = expr.args[len(expr.args) - n_kwonly:] if n_kwonly else []
        vararg_exprs = expr.args[va_idx:len(expr.args) - n_kwonly] if n_kwonly else expr.args[va_idx:]

        # Type-check fixed positional args
        for i, ((pname, ptype), arg) in enumerate(zip(func.params[:va_idx], fixed_args)):
            arg_type = self.expr.analyze_expr_with_hint(arg, ptype)
            arg_type = self._restore_readonly_arg(arg, arg_type, func.is_readonly)
            if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType):
                arg_type = arg_type.wrapped
            self.check_own_param(arg, arg_type, pname, ptype)
            if not (isinstance(ptype, CharType) and is_any_str_type(arg_type) and
                    isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                coerced_arg = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                       coercion_ctx=CoercionContext.ARG)
                fixed_args[i] = coerced_arg

        # Type-check each variadic arg against element type T
        for i, arg in enumerate(vararg_exprs):
            if isinstance(arg, TpyStarUnpack):
                # *expr unpacking: analyze inner expr and check element type compat
                inner_type = self.expr.analyze_expr(arg.expr)
                inner_elem = None
                if isinstance(inner_type, (ListType, SpanType, ArrayType)):
                    inner_elem = inner_type.get_element_type()
                elif isinstance(inner_type, PendingListType):
                    inner_elem = inner_type.element_type
                if inner_elem is None:
                    raise self.ctx.error(
                        f"Cannot unpack type '{inner_type}' into *args", arg)
                continue
            arg_type = self.expr.analyze_expr_with_hint(arg, elem_type)
            arg_type = self._restore_readonly_arg(arg, arg_type, func.is_readonly)
            if isinstance(arg_type, OwnType):
                arg_type = arg_type.wrapped
            coerced_arg = self.compat.coerce_expr(
                arg, arg_type, elem_type, f"*args element {i}",
                coercion_ctx=CoercionContext.ARG)
            vararg_exprs[i] = coerced_arg

        # Type-check keyword-only args (defaults already filled by resolve_kwargs)
        kwonly_params = [p for p in func.params if p.keyword_only]
        for i, (p, arg) in enumerate(zip(kwonly_params, kwonly_args)):
            if arg is not None:
                arg_type = self.expr.analyze_expr_with_hint(arg, p.type)

        # Build pack node and reconstruct args list:
        # [fixed_args..., vararg_pack, kwonly_args...]
        pack = TpyVarargPack(args=vararg_exprs, element_type=elem_type, loc=expr.loc)
        expr.args = fixed_args + [pack] + kwonly_args

    def _check_error_return_handled(self, expr: TpyCall | TpyMethodCall, func: FunctionInfo) -> None:
        """Check that calls to @error_return functions are inside matching try/except."""
        if func.error_return_type is None:
            return
        # Inside matching try/except (or except ReturnException catch-all)
        ctx_error_type = self.ctx.try_except_error_type
        if error_return_matches(ctx_error_type, func.error_return_type) or ctx_error_type == "*":
            return
        # Auto-propagation: caller has matching @error_return(E), not inside a try/except
        # (inside try/except, the goto-based dispatch handles it instead)
        current = self.ctx.current_function
        if (ctx_error_type is None
                and isinstance(current, TpyFunction)
                and error_return_matches(current.error_return, func.error_return_type)):
            return
        # REPL mode: allow error_return calls at top level (codegen panics on error)
        if self.ctx.is_top_level and self.ctx.allow_top_level_error_unwrap:
            return
        # Strip module prefix for user-facing message
        display_name = func.error_return_type
        if "." in display_name:
            display_name = display_name.rsplit(".", 1)[1]
        raise self.ctx.error(
            f"call to '{func.name}' may return '{display_name}' "
            f"which must be handled with try/except",
            expr,
        )

    def _analyze_generic_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a call to a generic function."""
        # Skip re-analysis: when a call appears as an arg to an outer call,
        # analyze_expr is called again. Args are already wrapped in coercions
        # from the first analysis, so re-processing would corrupt them.
        cached = self.ctx.get_expr_type(expr)
        if cached is not None:
            return cached

        # Check for invalid type arguments (e.g., first[123](x) or first[var](x))
        if expr.type_args_parse_error:
            raise self.ctx.error(expr.type_args_parse_error, expr)

        # Resolve kwargs before arity check
        self._resolve_call_kwargs(expr, func)

        # Check argument count first
        if len(expr.args) < func.min_args or len(expr.args) > func.max_args:
            raise self.ctx.error(
                arity_error_msg(expr.func_name, func.min_args, func.max_args, len(expr.args)),
                expr
            )

        # Get type substitution from explicit args or inference
        if expr.type_args:
            self._validate_explicit_type_args(expr, len(func.type_params))

            if len(expr.type_args) == len(func.type_params) and None not in expr.type_args:
                # Full explicit -- existing path
                type_subst = dict(zip(func.type_params, expr.type_args))
            else:
                # Partial explicit -- infer remaining from args + context
                arg_types = self._infer_arg_types(expr, func)
                type_subst = self.type_ops.infer_type_params_for_function(
                    func, arg_types, self.protocols.type_conforms_to_protocol,
                    expected_return_type=self.ctx.expr_type_hint,
                    explicit_type_args=expr.type_args,
                )
                if type_subst is None:
                    raise self.ctx.error(
                        f"Cannot infer remaining type arguments for '{func.name}'",
                        expr
                    )

            validate_type_param_bounds(
                type_subst, func.type_param_bounds, func.name,
                self.protocols.type_conforms_to_protocol,
                lambda msg: self.ctx.error(msg, expr),
            )
        else:
            # Infer from arguments
            arg_types = self._infer_arg_types(expr, func)
            type_subst = self.type_ops.infer_type_params_for_function(
                func, arg_types, self.protocols.type_conforms_to_protocol,
                expected_return_type=self.ctx.expr_type_hint,
            )
            if type_subst is None:
                # TODO: replace qualified_name check with @compiler_check decorator
                if func.qualified_name == "tpy.unsafe.unsafe_cast" and len(arg_types) == 1:
                    self._unsafe_cast_diagnostics(expr, arg_types[0])
                raise self.ctx.error(
                    f"Cannot infer type arguments for '{func.name}'. "
                    f"Specify explicitly: {func.name}[{', '.join(func.type_params)}](...)",
                    expr
                )

        # Prefer StrView for string literal args (skip fully-explicit)
        n_explicit = sum(1 for a in expr.type_args if a is not None) if expr.type_args else 0
        if n_explicit < len(func.type_params):
            prefer_strview_for_literals(type_subst, func, expr.args,
                                        self.protocols.type_conforms_to_protocol,
                                        n_explicit)

        # Store inferred type args for codegen (preserves Ref for val_or_ref<T>)
        expr.inferred_type_args = tuple(type_subst[p] for p in func.type_params)

        # Validate defaults for generic params not covered by explicit args
        self._validate_generic_defaults(expr, func, type_subst)

        # Resolve and check parameters
        resolved_func = self.type_ops.substitute_method_type_params(func, type_subst)

        # TODO: replace qualified_name check with @compiler_check decorator
        if (func.qualified_name == "tpy.unsafe.unsafe_cast"
                and is_readonly_ptr(resolved_func.return_type)
                and isinstance(self.ctx.expr_type_hint, PtrType) and not self.ctx.expr_type_hint.is_readonly):
            raise self.ctx.error(
                "unsafe_cast() cannot cast read-only pointer to mutable pointer (use unsafe_const_cast first)", expr
            )

        expr.resolved_function_info = resolved_func

        # Pack variadic args or type-check normally
        if resolved_func.has_variadic:
            self._analyze_and_pack_varargs(expr, resolved_func)
        else:
            for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
                resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst)

                # @value_ptr_coercion: Ptr[T] params accept T values via address-of coercion.
                vpc_active = func.value_ptr_coercion and isinstance(resolved_ptype, PtrType)
                check_ptype = resolved_ptype.pointee if vpc_active else resolved_ptype

                arg_type = self.expr.analyze_expr_with_hint(arg, check_ptype)
                arg_type = self._restore_readonly_arg(arg, arg_type, func.is_readonly)

                if (isinstance(arg_type, OwnType) and not isinstance(check_ptype, OwnType)
                        and not check_ptype.is_value_type() and not isinstance(arg, TpyName)):
                    hint = "Assign to a variable first: x = func(); other_func(x)"
                    raise self.ctx.error(
                        f"Cannot pass Own[{arg_type.wrapped}] directly to parameter '{pname}' "
                        f"(object types are passed by reference). {hint}",
                        arg
                    )
                if isinstance(arg_type, OwnType) and not isinstance(check_ptype, OwnType):
                    arg_type = arg_type.wrapped

                self.check_own_param(arg, arg_type, pname, check_ptype)

                if not (isinstance(check_ptype, CharType) and is_any_str_type(arg_type) and
                        isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                    coerced_arg = self.compat.coerce_expr(arg, arg_type, check_ptype, f"argument '{pname}'",
                                                           coercion_ctx=CoercionContext.ARG)
                    expr.args[i] = coerced_arg

                if vpc_active:
                    source = expr.args[i]
                    if not self.compat.is_mutable_lvalue(source):
                        raise self.ctx.error(
                            f"argument '{pname}' must be a mutable lvalue", expr)
                    for name in addr_taken_roots(source):
                        root = self.ctx.borrow_tracker.effective_storage(name)
                        self.ctx.mark_param_mutated(root)
                        self.ctx.mark_loop_var_mutated(root)
                    inner_type = self.ctx.get_expr_type(source)
                    vpc_node = TpyCoerce(
                        expr=source,
                        actual_type=inner_type,
                        expected_type=resolved_ptype,
                        coercion=VALUE_TO_PTR,
                        context_kind=CoercionContext.ARG,
                        context_msg=f"argument '{pname}'",
                        loc=arg.loc,
                    )
                    self.ctx.set_expr_type(vpc_node, resolved_ptype)
                    expr.args[i] = vpc_node

                if isinstance(arg_type, PENDING_CONTAINER_TYPES):
                    self.deduction.mark_container_param_context(arg, arg_type, resolved_ptype)
                if isinstance(arg_type, PendingViewType):
                    self.deduction.mark_view_param_context(arg, resolved_ptype, arg_type.family)

        # Resolve return type
        resolved_return = self.type_ops.substitute_type_params(func.return_type, type_subst)

        # Built-in generic functions (e.g. iter) delegate to concrete methods
        # whose C++ returns std::optional<T>, not T*. Strip force_pointer_repr
        # that substitute_type_params sets for unbounded TypeParamRef -> value type.
        if (func.is_builtin_function and func.type_params
                and isinstance(resolved_return, OptionalType) and resolved_return.force_pointer_repr):
            resolved_return = OptionalType(resolved_return.inner)

        # Detect duplicate union members after generic substitution:
        # e.g. T | U | str with T=U=Int32 would emit std::variant<int, int, str> (ill-formed)
        if isinstance(func.return_type, UnionType):
            orig_count = len(func.return_type.members)
            resolved_count = len(resolved_return.members) if isinstance(resolved_return, UnionType) else 1
            if resolved_count < orig_count:
                raise self.ctx.error(
                    f"Generic union return type '{func.return_type}' produces duplicate members "
                    f"with these type arguments (resolves to '{resolved_return}')",
                    expr,
                )

        self._check_borrow_arg_conflicts(expr)
        self._check_loop_var_arg_mutation(expr)
        self._record_mutation_call_edges(expr)
        self._check_error_return_handled(expr, func)
        return resolved_return

    def _validate_generic_defaults(self, expr: TpyCall, func: FunctionInfo,
                                    type_subst: dict[str, TpyType]) -> None:
        """Validate defaults for params not covered by explicit args."""
        validate_generic_defaults(
            expr.args, func, type_subst, self.type_ops,
            lambda msg: self.ctx.error(msg, expr))

    def _analyze_record_constructor(self, expr: TpyCall, record: RecordInfo) -> TpyType:
        """Analyze a call to a record constructor."""
        # Types with overloaded @cpp_template/@native __init__ (e.g. Int32, str, bool)
        if record.builtin_type_key and not record.type_params:
            init_overloads = record.get_method_overloads("__init__")
            if init_overloads:
                return self._analyze_template_constructor(expr, record, init_overloads)

        # Resolve kwargs for record constructors
        if expr.kwargs:
            if record.has_init:
                self._resolve_call_kwargs_init(expr, record)
            else:
                first_kwarg = next(iter(expr.kwargs))
                raise self.ctx.error(
                    f"'{record.name}()' got unexpected keyword argument '{first_kwarg}'", expr)

        # Check if this is a generic record instantiation (e.g., Stack[Int32]())
        if expr.call_type is not None and isinstance(expr.call_type, NamedType) and expr.call_type.is_record:
            # Validate type arguments
            if record.is_generic():
                if not expr.call_type.type_args:
                    # No explicit type args -- fall through to inference section
                    expr.call_type = None
                else:
                    if len(expr.call_type.type_args) != len(record.type_params):
                        raise self.ctx.error(
                            f"Record '{record.name}' expects {len(record.type_params)} type arguments, "
                            f"got {len(expr.call_type.type_args)}",
                            expr
                        )
                    # Validate type parameter bounds
                    for param_name, type_arg in zip(record.type_params, expr.call_type.type_args):
                        if param_name in record.type_param_bounds:
                            bound = record.type_param_bounds[param_name]
                            if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                                raise self.ctx.error(
                                    f"Type argument '{type_arg}' does not satisfy bound '{bound}' "
                                    f"for type parameter '{param_name}' of '{record.name}'",
                                    expr
                                )
        if expr.call_type is not None and isinstance(expr.call_type, NamedType) and expr.call_type.is_record:
            # Analyze and type-check constructor arguments with type substitution
            type_subst = self.type_ops.build_type_substitution(expr.call_type)
            if record.has_init:
                # Type-check __init__ parameters
                min_args = _init_params_min_args(record.init_params)
                max_args = len(record.init_params)
                if len(expr.args) < min_args or len(expr.args) > max_args:
                    raise self.ctx.error(
                        arity_error_msg(f"{record.name}()", min_args, max_args, len(expr.args)),
                        expr
                    )
                for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                    resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst) if type_subst else ptype
                    arg_type = self.expr.analyze_expr_with_hint(arg, resolved_ptype)
                    arg_type = self._restore_readonly_arg(arg, arg_type)
                    self.check_own_param(arg, arg_type, pname, resolved_ptype)
                    expr.args[i] = self.compat.coerce_expr(arg, arg_type, resolved_ptype, f"argument '{pname}'",
                                                           coercion_ctx=CoercionContext.ARG)
            else:
                for arg in expr.args:
                    self.expr.analyze_expr(arg)
            self._set_record_constructor_info(expr, record, expr.call_type, type_subst)
            return expr.call_type
        # Generic record without explicit type args - try type inference
        if record.is_generic():
            # Validate _ wildcard type args if present
            wildcard_type_args: tuple[TpyType | None, ...] | None = None
            if expr.type_args and None in expr.type_args:
                if len(expr.type_args) != len(record.type_params):
                    raise self.ctx.error(
                        f"Record '{record.name}' expects {len(record.type_params)} type arguments, "
                        f"got {len(expr.type_args)}",
                        expr
                    )
                # Validate non-wildcard entries
                in_generic = bool(
                    (isinstance(self.ctx.current_function, TpyFunction) and self.ctx.current_function.type_params)
                    or self.ctx.record_ctx.type_params
                )
                for type_arg in expr.type_args:
                    if type_arg is not None:
                        if is_protocol_type(type_arg):
                            raise self.ctx.error(
                                f"Protocol type '{type_arg.name}' cannot be used as a type argument",
                                expr)
                        if isinstance(type_arg, NamedType) and type_arg.is_record and not type_arg.type_args:
                            if self.ctx.registry.get_record_for_type(type_arg) is None:
                                raise self.ctx.error(f"Unknown type: {type_arg.name}", expr)
                        self.type_ops.validate_type(
                            type_arg, allow_type_param_ref=in_generic,
                            loc=expr.loc, allow_forward_ref=False)
                wildcard_type_args = expr.type_args
            if record.has_init:
                arg_types = [self.expr.analyze_expr(arg) for arg in expr.args]
                inferred = self.type_ops.infer_type_params_for_record(
                    record, arg_types, expected_type=self.ctx.expr_type_hint,
                    explicit_type_args=wildcard_type_args,
                )
                if inferred:
                    # Resolve pending types for codegen.
                    for k, v in list(inferred.items()):
                        if isinstance(v, IntLiteralType):
                            inferred[k] = self.ctx.default_int_for_literal(v)
                        elif isinstance(v, PendingListType):
                            # Resolve PendingListType to ListType
                            elem_type = v.element_type
                            if isinstance(elem_type, IntLiteralType):
                                elem_type = self.ctx.default_int_for_literal(elem_type)
                            inferred[k] = ListType(elem_type)
                    # Validate type parameter bounds
                    for param_name, type_arg in inferred.items():
                        if param_name in record.type_param_bounds:
                            bound = record.type_param_bounds[param_name]
                            if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                                raise self.ctx.error(
                                    f"Inferred type '{type_arg}' does not satisfy bound '{bound}' "
                                    f"for type parameter '{param_name}' of '{record.name}'",
                                    expr
                                )
                    type_args = tuple(inferred[p] for p in record.type_params)
                    # Use expr.func (local name) not record.name (original) for alias support
                    inferred_type = NamedType(expr.func_name, type_args)
                    expr.call_type = inferred_type
                    # Coerce arguments with substitution
                    type_subst = inferred
                    for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                        resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst)
                        at = self._restore_readonly_arg(arg, arg_types[i])
                        self.check_own_param(arg, at, pname, resolved_ptype)
                        expr.args[i] = self.compat.coerce_expr(
                            arg, at, resolved_ptype,
                            f"argument '{pname}'", coercion_ctx=CoercionContext.ARG
                        )
                    self._set_record_constructor_info(expr, record, inferred_type, type_subst)
                    return inferred_type
            else:
                # No __init__ -- try contextual inference only
                if self.ctx.expr_type_hint is not None or wildcard_type_args is not None:
                    inferred: dict[str, TpyType] = {}
                    if wildcard_type_args:
                        for tp, arg in zip(record.type_params, wildcard_type_args):
                            if arg is not None:
                                inferred[tp] = arg
                    exp = self.ctx.expr_type_hint
                    if exp is not None:
                        if isinstance(exp, OwnType):
                            exp = exp.wrapped
                        record_pattern = NamedType(record.name, tuple(
                            TypeParamRef(tp) for tp in record.type_params
                        ))
                        self.type_ops.match_type_with_inference(record_pattern, exp, inferred)
                    if all(tp in inferred for tp in record.type_params):
                        # Validate type parameter bounds
                        for param_name, type_arg in inferred.items():
                            if param_name in record.type_param_bounds:
                                bound = record.type_param_bounds[param_name]
                                if not self.protocols.type_conforms_to_protocol(type_arg, bound):
                                    raise self.ctx.error(
                                        f"Inferred type '{type_arg}' does not satisfy bound '{bound}' "
                                        f"for type parameter '{param_name}' of '{record.name}'",
                                        expr
                                    )
                        type_args = tuple(inferred[p] for p in record.type_params)
                        inferred_type = NamedType(expr.func_name, type_args)
                        expr.call_type = inferred_type
                        self._set_record_constructor_info(expr, record, inferred_type, inferred)
                        for arg in expr.args:
                            self.expr.analyze_expr(arg)
                        return inferred_type
            # Inference failed -- try deferred resolution (Phase 7a)
            if self._can_defer_generic_inference(record, expr):
                return self._create_pending_generic_instance(record, expr)
            raise self.ctx.error(
                f"Cannot infer type arguments for '{record.name}'. "
                f"Please specify explicitly: {record.name}[{', '.join(record.type_params)}](...)",
                expr
            )
        # Non-generic record
        if record.has_init:
            # Type-check __init__ parameters
            min_args = _init_params_min_args(record.init_params)
            max_args = len(record.init_params)
            if len(expr.args) < min_args or len(expr.args) > max_args:
                raise self.ctx.error(
                    arity_error_msg(f"{record.name}()", min_args, max_args, len(expr.args)),
                    expr
                )
            for i, (arg, (pname, ptype, _)) in enumerate(zip(expr.args, record.init_params)):
                arg_type = self.expr.analyze_expr_with_hint(arg, ptype)
                arg_type = self._restore_readonly_arg(arg, arg_type)
                self.check_own_param(arg, arg_type, pname, ptype)
                expr.args[i] = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                        coercion_ctx=CoercionContext.ARG)
        else:
            for arg in expr.args:
                self.expr.analyze_expr(arg)
        # Use expr.func (local name) not record.name (original) for alias support
        result_type = NamedType(expr.func_name)
        self._set_record_constructor_info(expr, record, result_type)
        return result_type

    def _can_defer_generic_inference(self, record: RecordInfo, expr: TpyCall) -> bool:
        """Check whether a generic constructor can use deferred type inference."""
        # Only in function bodies (resolve_all runs there)
        if not isinstance(self.ctx.current_function, TpyFunction):
            return False
        # Not inside a class body (field types must be concrete)
        if self.ctx.record_ctx.record is not None:
            return False
        # Check constructor arity (args must be valid count, ignoring type constraints)
        if record.has_init:
            min_args = _init_params_min_args(record.init_params)
            max_args = len(record.init_params)
            if len(expr.args) < min_args or len(expr.args) > max_args:
                return False
        elif expr.args:
            return False
        return True

    def _create_pending_generic_instance(
        self, record: RecordInfo, expr: TpyCall,
    ) -> PendingGenericInstanceType:
        """Create a deferred generic instance for later resolution from method calls."""
        # Analyze constructor args (we need their types even though we can't
        # type-check against params yet -- T is unknown)
        if record.has_init:
            for arg in expr.args:
                self.expr.analyze_expr(arg)

        instance_id = self.ctx.pending_generic_counter
        self.ctx.pending_generic_counter += 1

        # Seed inferred dict from explicit _ wildcard type args
        inferred: dict[str, TpyType] = {}
        if expr.type_args and None in expr.type_args:
            for tp, arg in zip(record.type_params, expr.type_args):
                if arg is not None:
                    inferred[tp] = arg
        # Seed from constructor args if has_init
        if record.has_init and expr.args:
            arg_types = [self.ctx.get_expr_type(arg) for arg in expr.args]
            # Try partial inference from available args
            partial = self.type_ops.infer_type_params_for_record(
                record, arg_types, expected_type=None,
            )
            if partial:
                for k, v in partial.items():
                    if isinstance(v, IntLiteralType):
                        v = self.ctx.default_int_for_literal(v)
                    inferred[k] = v

        info = PendingGenericInstanceInfo(
            instance_id=instance_id,
            variable_name=expr.func_name,
            record_info=record,
            record_name=expr.func_name,
            type_params=list(record.type_params),
            inferred=inferred,
            expr=expr,
        )
        self.ctx.pending_generic_instances[instance_id] = info
        return PendingGenericInstanceType(record_name=expr.func_name, instance_id=instance_id)

    def _analyze_legacy_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a legacy function call (fallback path)."""
        # Resolve kwargs before arity check
        self._resolve_call_kwargs(expr, func)

        expr.resolved_function_info = func
        if len(expr.args) < func.min_args or len(expr.args) > func.max_args:
            raise self.ctx.error(
                arity_error_msg(expr.func_name, func.min_args, func.max_args, len(expr.args)), expr)

        if func.has_variadic:
            self._analyze_and_pack_varargs(expr, func)
        else:
            self._typecheck_call_args(expr, func)

        self._check_borrow_arg_conflicts(expr)
        self._check_loop_var_arg_mutation(expr)
        self._record_mutation_call_edges(expr)
        return func.return_type

    def _rewrite_subscript_callee(self, expr: TpyCall) -> TpyType:
        """Rewrite fns[0](args) from generic-call form to expression callee.

        The parser parsed fns[0](args) as TpyCall("fns", args, type_args_parse_error=...)
        because fns[0] looks like a generic call. But sema found fns is a variable,
        not a function. Use the pre-parsed subscript_callee to re-analyze.
        """
        assert expr.subscript_callee is not None
        expr.func = expr.subscript_callee
        expr.type_args = ()
        expr.type_args_parse_error = None
        expr.subscript_callee = None
        return self._analyze_expr_callee(expr)

    def _analyze_expr_callee(self, expr: TpyCall) -> TpyType:
        """Analyze a call where the callee is an expression (not a simple name).

        Handles callbacks[0](x), get_handler()(x), obj.field(x) etc.
        """
        callee_type = self.expr.analyze_expr(expr.func)
        if isinstance(callee_type, OwnType):
            callee_type = callee_type.wrapped
        if isinstance(callee_type, FnType):
            return self._analyze_fn_type_call(expr, callee_type)
        if isinstance(callee_type, CallableType):
            return self._analyze_callable_type_call(expr, callee_type)
        if isinstance(callee_type, NamedType):
            record = self.ctx.registry.get_record_for_type(callee_type)
            if record and self.ctx.registry.get_method_overloads_with_parents(record, "__call__"):
                return self._analyze_dunder_call(expr)
        raise self.ctx.error(
            f"Expression is not callable (type '{callee_type}')", expr)

    def _analyze_dunder_call(self, expr: TpyCall) -> TpyType:
        """Analyze obj(args) where obj has a __call__ method.

        Creates a synthetic TpyMethodCall and delegates to method analysis.
        """
        synthetic = TpyMethodCall(
            obj=expr.func,
            method="__call__",
            args=expr.args,
            kwargs=expr.kwargs,
            loc=expr.loc,
        )
        ret_type = self.methods.analyze_method_call(synthetic)
        expr.dunder_call = synthetic
        expr.resolved_function_info = synthetic.resolved_function_info
        return ret_type

    def _analyze_fn_type_call(self, expr: TpyCall, fn_type: FnType) -> TpyType:
        """Analyze a call to a variable of Fn type."""
        return self._analyze_typed_callable_call(expr, fn_type.param_types, fn_type.return_type, "Fn")

    def _analyze_callable_type_call(self, expr: TpyCall, callable_type: CallableType) -> TpyType:
        """Analyze a call to a variable of Callable type."""
        return self._analyze_typed_callable_call(expr, callable_type.param_types, callable_type.return_type, "Callable")

    def _analyze_typed_callable_call(
        self, expr: TpyCall, param_types: tuple[TpyType, ...], return_type: TpyType, kind_name: str,
    ) -> TpyType:
        """Shared logic for calling Fn-typed and Callable-typed variables."""
        if expr.kwargs:
            raise self.ctx.error(
                f"Keyword arguments are not supported for {kind_name}-typed callables "
                f"({kind_name} types have no parameter names)", expr)
        if len(expr.args) != len(param_types):
            raise self.ctx.error(
                f"{kind_name} type expects {len(param_types)} argument(s), "
                f"got {len(expr.args)}",
                expr
            )
        for i, (arg, expected_type) in enumerate(zip(expr.args, param_types)):
            arg_type = self.expr.analyze_expr_with_hint(arg, expected_type)
            if arg_type != expected_type:
                try:
                    self.compat.check_type_compatible(
                        arg_type, expected_type,
                        f"argument {i + 1}", loc=expr.loc, source_expr=arg)
                except SemanticError:
                    raise self.ctx.error(
                        f"Argument {i + 1}: expected '{expected_type}', got '{arg_type}'",
                        expr
                    )
        func_label = expr.func_name if isinstance(expr.func, TpyName) else "<expr>"
        expr.resolved_function_info = FunctionInfo(
            name=func_label,
            params=[ParamInfo(f"__a{i}", t) for i, t in enumerate(param_types)],
            return_type=return_type,
            is_readonly=True,
        )
        return return_type

    # -- Call-site macro expansion --

    def _run_call_macro(
        self,
        args: list[TpyExpr],
        kwargs: dict[str, TpyExpr],
        macro_fn: Callable,
        module_name: str,
        func_name: str,
        loc: object,
    ) -> tuple[TpyExpr, TpyType]:
        """Analyze args, call a @call_macro, analyze expansion. Returns (expansion, type)."""
        # Ensure macro_deps for this call macro's module are populated
        self._ensure_call_macro_deps(module_name)

        macro_args = [
            MacroArg(expr=a, type=TypeInfo.from_tpy_type(self.expr.analyze_expr(a)))
            for a in args
        ]
        macro_kwargs = {
            k: MacroArg(expr=v, type=TypeInfo.from_tpy_type(self.expr.analyze_expr(v)))
            for k, v in kwargs.items()
        }
        ctx = CallMacroContext(self.ctx, loc=loc)
        qname = f"{module_name}.{func_name}"
        expansion = expand_call_macro(
            macro_fn, ctx, macro_args, macro_kwargs, qname, loc)
        return expansion, self.expr.analyze_expr(expansion)

    def _ensure_call_macro_deps(self, module_name: str) -> None:
        """Populate macro_ns with deps from a call macro's module (if not already done)."""
        macro_reg = self.ctx.macro_registry
        if macro_reg is None:
            return
        deps = macro_reg.get_deps(module_name)
        if not deps:
            return
        for dep_mod, name_filter in deps.items():
            if dep_mod in self.ctx.macro_dep_modules:
                continue
            self.ctx.macro_dep_modules.add(dep_mod)
            module_info = self.ctx.registry.get_module(dep_mod)
            if module_info is None:
                continue
            if module_info.records:
                for name, record_info in module_info.records.items():
                    if name_filter is not None and name not in name_filter:
                        continue
                    if self.ctx.registry.get_record(name) is None:
                        self.ctx.registry.register_record(record_info, name)
                    self.ctx.macro_ns.bind_imported_name(name, dep_mod, name)
                    self.ctx.imported_names.setdefault(name, (dep_mod, name))
                    self.ctx.user_imported_records.setdefault(name, (dep_mod, name))
            if module_info.functions:
                for name, func_infos in module_info.functions.items():
                    if name_filter is not None and name not in name_filter:
                        continue
                    is_special = func_infos and func_infos[0].special_handling
                    if not is_special:
                        if self.ctx.registry.get_function(name) is None:
                            self.ctx.registry.register_function_group(name, func_infos)
                        self.ctx.user_imported_functions.setdefault(name, (dep_mod, name))
                    self.ctx.macro_ns.bind_imported_name(name, dep_mod, name)
                    self.ctx.imported_names.setdefault(name, (dep_mod, name))
            if module_info.enums:
                for name, enum_type in module_info.enums.items():
                    if name_filter is not None and name not in name_filter:
                        continue
                    if self.ctx.registry.get_enum(name) is None:
                        self.ctx.registry.register_enum(enum_type, name)
                    self.ctx.macro_ns.bind_enum(enum_type, name=name)
                    self.ctx.imported_names.setdefault(name, (dep_mod, name))
                    self.ctx.user_imported_enums.setdefault(name, (dep_mod, name))

    def _expand_call_macro(
        self, expr: TpyCall, macro_fn: Callable,
        module_name: str, func_name: str,
    ) -> TpyType:
        """Expand a @call_macro on a TpyCall."""
        expansion, typ = self._run_call_macro(
            expr.args, expr.kwargs, macro_fn, module_name, func_name, expr.loc)
        expr.macro_expansion = expansion
        return typ

    def _expand_call_macro_from_method(
        self, expr: TpyMethodCall, macro_fn: Callable,
        module_name: str, func_name: str,
    ) -> TpyType:
        """Expand a @call_macro on a module.func() TpyMethodCall."""
        expansion, typ = self._run_call_macro(
            expr.args, expr.kwargs, macro_fn, module_name, func_name, expr.loc)
        expr.macro_expansion = expansion
        return typ
