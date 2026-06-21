"""
TurboPython Call Analysis

Function and constructor call analysis.
"""

from __future__ import annotations
import copy
from contextlib import contextmanager
from dataclasses import dataclass, replace as dc_replace
from typing import Callable, Iterator, NoReturn, TYPE_CHECKING

from ..compilation_context import require_current_compiler

from ..typesys import (
    TpyType, NominalType, AliasRef, OwnType, OptionalType, TupleType, own_tuple_target, strip_template_repr, make_list, PendingListType, PendingViewType, make_copy_iter, make_own_iter,
    is_polymorphic_class_type, is_dynamic_dispatch_inner, polymorphic_source_inner,
    deref_dispatch_inner,
    IntLiteralType, resolve_int_literals,
    LiteralType, LiteralValue, LiteralTag, ListLiteralInfo, FunctionInfo, RecordInfo, TypeParamRef,
    PtrType, is_readonly_ptr, VoidType, is_void_like_type, ParamInfo, ReadonlyType,
    strip_auto_readonly, apply_auto_readonly,
    UNKNOWN_ELEMENT, UnknownElementType, PendingDictType, DictLiteralInfo, PendingSetType, SetLiteralInfo,
    UnionType, VOID, BIGINT, BOOL, STR, INT32, AnyType, ANY, is_protocol_type, unwrap_readonly, unwrap_own, unwrap_optional_own, make_union, ensure_qualified,
    is_any_str_type, container_to_str_template, error_return_matches,
    is_protocol_union, protocol_union_protocols,
    MutationCallEdge,
    PendingGenericInstanceType, PendingGenericInstanceInfo,
    CallableType, is_fn_type, unwrap_ref_type,
    is_integer_type, is_any_int_type,
    is_callable_type, is_float_type, is_readonly_span, varargs_is_readonly, unwrap_qualifiers,
    unwrap_send_sync,
    param_has_mutable_borrow_surface, contains_type_param,
    del_suppresses_default_ctor)
from ..parse import (
    TpyCall, TpyMethodCall, TpyFieldAccess, TpyStrLiteral, TpyName, TpyFunction, TpyExpr,
    TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyNoneLiteral, TpyUnaryOp,
    TpyBinOp, TpyTupleLiteral, TpyTypeParamConstruct, TpyCoerce, TpyLambda,
    TpyDictLiteral, TpySetLiteral,
    TpyVarargPack, TpyStarUnpack, TpyFString, TpyFStringValue,
)
from ..modules import extract_type_params
from ..namespace import BindingKind
from ..coercions import CoercionContext, VALUE_TO_PTR
from ..symbol_binding import SymbolKind, is_kind, walk_attribute_chain
from .context import PENDING_CONTAINER_TYPES
from ..diagnostics import SemanticError
from .overloads import (
    type_matches_numeric, type_matches_with_coercion,
    resolve_overload, OverloadAmbiguityError,
    _classify_overload, _score, _expand_arg_types_with_kwargs,
    _scalar_widening_cost,
)
from .statements import _root_name_of_expr, _is_self_call_deferred
from .protocols import dynamic_dispatch_type_conforms
from .type_ops import partial_substitute, post_substitute_hint, seeded_arg_hint
from .send_chain import why_not_send, why_not_sync, render_chain
from ..macro_api import MacroArg, MacroFStringPart, CallMacroContext, TypeInfo, _is_static_str
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
from ..modules import _resolve_concrete_type_name
from .. import qnames
from ..type_def_registry import (
    is_array, is_span, is_varargs, is_list, is_borrowing_view_type,
    is_fixed_int_type, is_bool_type, is_char_type, is_fstr_type,
    is_str_type, is_big_int_type,
    int_traits_of,
    is_enum_type,
    find_factory_by_simple_name, find_factory_in_module,
)


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
        if is_str_type(arg_t) and isinstance(arg, TpyStrLiteral):
            enriched.append(LiteralType(STR, (LiteralValue(LiteralTag.STR, arg.value),)))
        elif isinstance(arg_t, IntLiteralType) and arg_t.value is not None:
            enriched.append(LiteralType(INT32, (LiteralValue(LiteralTag.INT, arg_t.value),)))
        elif is_bool_type(arg_t) and isinstance(arg, TpyBoolLiteral):
            enriched.append(LiteralType(BOOL, (LiteralValue(LiteralTag.BOOL, arg.value),)))
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
        if params[name_to_index[kw_name]].positional_only:
            raise error_fn(
                f"'{func_name}' parameter '{kw_name}' is positional-only "
                f"and cannot be passed by keyword")

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
    keyword_only: bool = False,
) -> list[TpyExpr]:
    """Resolve keyword arguments for record constructors using init_params format.

    Adapts init_params tuples to ParamInfo and delegates to resolve_kwargs.
    keyword_only=True marks all params as keyword-only (used for **kwargs TD packing).
    """
    params = [ParamInfo(name, ptype, default_expr=default, keyword_only=keyword_only)
              for name, ptype, default in init_params]
    return resolve_kwargs(expr_args, expr_kwargs, params, func_name, error_fn, call_loc=call_loc)


def _cross_module_cpp_overlay(
    types: list[TpyType], ctx: 'SemanticContext'
) -> dict[str, str]:
    """Map each cross-module record short-name reachable in `types` to its
    qualified C++ name. Needed because the {T}/{cpp} cpp_template render runs
    at sema, where `native_cpp_names` (codegen's per-module qualification map)
    is still empty -- without this a cross-module element would render as its
    bare short name and fail the C++ build. Qualifies by the record's defining
    module, mirroring codegen's native_cpp_names population (incl. @native
    records' native_name); same-module / builtin records stay bare."""
    from ..codegen_cpp.context import qualified_cpp_name  # local import: avoid sema->codegen_cpp cycle (cf. typesys.py)
    registry = ctx.registry
    current_module = ctx.module_name
    overlay: dict[str, str] = {}
    seen: set[int] = set()

    def walk(t: TpyType) -> None:
        if id(t) in seen:
            return
        seen.add(id(t))
        if isinstance(t, NominalType):
            info = registry.get_record_for_type(t)
            qual = (registry.record_qualification(info, current_module)
                    if info is not None else None)
            if qual is not None:  # cross-module, non-builtin
                if info.is_native:
                    # Match codegen: @native records render to their native_name;
                    # a native record without one stays bare (unregistered).
                    if info.native_name:
                        overlay[t.name] = ensure_qualified(info.native_name)
                else:
                    overlay[t.name] = qualified_cpp_name(*qual)
        for inner in t.inner_types():
            walk(inner)

    for t in types:
        walk(t)
    return overlay


@contextmanager
def _native_cpp_names_overlay(overlay: dict[str, str]) -> Iterator[None]:
    """Temporarily overlay `native_cpp_names` so a sema-time type render
    qualifies cross-module names, then restore. The map is otherwise populated
    per-module at codegen and empty during sema."""
    if not overlay:
        yield
        return
    nmap = require_current_compiler().native_cpp_names
    saved = {k: nmap.get(k) for k in overlay}
    nmap.update(overlay)
    try:
        yield
    finally:
        for k, prev in saved.items():
            if prev is None:
                nmap.pop(k, None)
            else:
                nmap[k] = prev


def _resolve_cpp_template_type_params(
    fi: FunctionInfo,
    type_params: dict[str, TpyType] | None = None,
    result_type: TpyType | None = None,
    ctx: 'SemanticContext | None' = None,
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
    # The substitution renders types to C++ HERE (sema), but
    # NominalType.to_cpp() qualifies a cross-module record only via the
    # native_cpp_names map, which codegen populates per-module and which is
    # empty at sema. Overlay the qualified cross-module names so a transitively
    # reached element (e.g. finditer's Match) doesn't bake its bare short name.
    overlay: dict[str, str] = {}
    if ctx is not None:
        render_types = list(type_params.values()) if type_params else []
        if result_type is not None:
            render_types.append(result_type)
        overlay = _cross_module_cpp_overlay(render_types, ctx)
    with _native_cpp_names_overlay(overlay):
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
    return dc_replace(fi, cpp_template=template, canonical_fi=fi.root)


def _has_type_param_ref_in_params(func: "FunctionInfo") -> bool:
    """Check if a FunctionInfo needs generic type inference.

    True if any parameter type or the return type contains TypeParamRef.
    This covers zero-arg generic functions like unsafe_alloc[T]() -> Ptr[T]
    that infer T from return type context.
    """
    if any(contains_type_param(p.type) for p in func.params):
        return True
    return contains_type_param(func.return_type)


@dataclass
class ResolveResult:
    """Output of overload-resolution selection (`_resolve_call_overloads`).

    Selection only -- callers do post-resolution work (commit-side
    coercion, no-match diagnostics, legacy fallback) using these fields.

    `matched_origin` is `(original_generic, type_subst)` for a generic
    winner, `None` for non-generic winners or no match. Today's user
    path only needs `original_generic`; builtin path needs the full
    `type_subst` for `inferred_type_args`.

    `contextual_callable_used` is True iff any Fn-position arg was typed
    using contextual evidence (a `TpyLambda`, or a `TpyName` resolving
    to a function ref via `_match_function_to_hint_data`). Callers gate
    fallback behavior on this -- structural-match fallbacks must not
    reason over synthesized callable evidence.

    `first_contextual_error` stashes the first per-candidate
    `SemanticError` raised by function-ref ambiguity / generic
    rejection during Regime C trial. Surfaced by the caller when no
    candidate ultimately wins.
    """
    matched: FunctionInfo | None
    matched_origin: tuple[FunctionInfo, dict[str, TpyType]] | None
    arg_types: list[TpyType]
    enriched_types: list[TpyType]
    kwarg_types: dict[str, TpyType] | None
    contextual_callable_used: bool = False
    first_contextual_error: SemanticError | None = None


@dataclass(frozen=True)
class SuppliedFnSlot:
    """An Fn-typed param of a candidate filled by a supplied (non-default) arg.

    Used to identify which candidates are "Fn-bearing-by-supplied-args"
    for regime selection in `_resolve_call_overloads`, and which arg
    expression maps to which Fn param slot for per-candidate trial.
    """
    param_index: int       # index in func.params
    arg_expr: TpyExpr      # the supplied arg expression
    arg_index: int | None  # index in expr.args (None if supplied via kwarg)
    kwarg_name: str | None # kwarg name (None if supplied positionally)


def _is_numeric_or_int_literal(t: TpyType) -> bool:
    """True for any numeric type (int/float concrete families) or IntLiteralType.
    Matches the former _NUMERIC_TYPES tuple (excludes bool -- added separately)."""
    return (is_fixed_int_type(t) or is_big_int_type(t)
            or is_float_type(t) or isinstance(t, IntLiteralType))


def _default_compatible_with_type(default_expr: TpyExpr, resolved_type: TpyType) -> bool:
    """Check if a default expression is compatible with a resolved concrete type."""
    match default_expr:
        case TpyTypeParamConstruct():
            return True
        case TpyNoneLiteral():
            return isinstance(resolved_type, OptionalType)
        case TpyBoolLiteral():
            return is_bool_type(resolved_type) or _is_numeric_or_int_literal(resolved_type)
        case TpyIntLiteral():
            return _is_numeric_or_int_literal(resolved_type) or is_bool_type(resolved_type)
        case TpyFloatLiteral():
            return is_float_type(resolved_type)
        case TpyStrLiteral():
            return is_any_str_type(resolved_type) or is_char_type(resolved_type)
        case TpyUnaryOp(op="-"):
            return _default_compatible_with_type(default_expr.operand, resolved_type)
        case TpyCall():
            return _is_numeric_or_int_literal(resolved_type)
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


def resolve_inferred_type_arg(t: "TpyType | int", default_int_type: TpyType) -> "TpyType | int":
    """Resolve literal/pending types in an inferred type arg for codegen.

    Inferred values can be ints (integer-kind type params, e.g. N in
    Array[T, N]) -- those pass through unchanged.
    """
    if isinstance(t, int):
        return t
    if isinstance(t, PendingListType):
        return make_list(resolve_int_literals(t.element_type, default_int_type))
    return resolve_int_literals(t, default_int_type)


def validate_type_param_bounds(
    type_subst: dict[str, TpyType],
    bounds: dict[str, TpyType],
    func_name: str,
    satisfies_bound,
    error_fn,
) -> None:
    """Validate that resolved type args satisfy their type parameter bounds."""
    for param_name, type_arg in type_subst.items():
        if param_name in bounds:
            bound = bounds[param_name]
            if not satisfies_bound(type_arg, bound):
                raise error_fn(
                    f"Type argument '{type_arg}' does not satisfy bound '{bound}' "
                    f"for type parameter '{param_name}' of '{func_name}'"
                    f"{_send_sync_bound_detail(bound, type_arg)}")


def _send_sync_bound_detail(bound: TpyType, type_arg: TpyType) -> str:
    """Why-not chain appended to a failing `T: Send` / `T: Sync` bound."""
    qname = bound.qualified_name() if isinstance(bound, NominalType) else None
    if qname not in qnames.SEMA_ONLY_MARKER_PROTOCOLS:
        return ""
    send = qname == qnames.SEND
    chain = why_not_send(type_arg) if send else why_not_sync(type_arg)
    return f"\n{render_chain(chain, send)}" if chain is not None else ""


_REPR_TEMPLATE = "::tpy::repr_of({0})"


def _repr_fallback_template(typ: TpyType) -> str | None:
    """Return a C++ template for repr() on types without Representable,
    or None if the type has no known repr path.

    Covers: bool, fixed ints, float, BigInt, strings, optionals,
    enums, user records (which always have operator<<), and unions
    of types that each have their own repr path. Union dispatch lives
    in runtime/cpp/include/tpy/dunder.hpp as a `std::variant` overload.
    """
    if is_bool_type(typ):
        return _REPR_TEMPLATE
    if is_integer_type(typ):
        return _REPR_TEMPLATE
    if is_float_type(typ):
        return _REPR_TEMPLATE
    if is_any_str_type(typ):
        return _REPR_TEMPLATE
    if is_char_type(typ):
        return _REPR_TEMPLATE
    if is_enum_type(typ):
        return _REPR_TEMPLATE
    if isinstance(typ, OptionalType):
        return _REPR_TEMPLATE
    if isinstance(typ, NominalType) and typ.is_user_record:
        return _REPR_TEMPLATE
    if isinstance(typ, UnionType):
        if all(_repr_fallback_template(m) is not None for m in typ.members):
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
        # Set after construction to break circular deps (expr <-> calls <-> methods)
        self.expr: ExpressionAnalyzer
        self.methods: MethodAnalyzer
        # Pending borrow checks deferred until Phase 2 resolves mutated_params
        self.pending_borrow_checks: list[tuple[FunctionInfo, int, str, SourceLocation | None]] = []
        # Deferred match-arm subject-mutation checks: a method call on the
        # subject root/prefix whose readonly verdict (and thus whether it may
        # reassign the borrowed subject storage) only settles in Phase 2.
        # Entry: (method_call, subject_path_str, arm_id).
        self.pending_match_subject_checks: list[tuple[TpyMethodCall, str, int]] = []

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

    def resolve_pending_match_subject_checks(self) -> None:
        """Emit the deferred match-arm dangle warning for a non-readonly method
        call on the subject root/prefix (it may reassign the borrowed subject
        storage). Readonly is now settled; warn once per arm. The sound fix
        (reject the mutation) waits for the IR loan model -- see BUGS.md."""
        warned_arms: set[int] = set()
        for mcall, subj_str, arm_id in self.pending_match_subject_checks:
            if arm_id in warned_arms:
                continue
            fi = mcall.resolved_function_info
            if fi is not None and fi.is_readonly is False:
                warned_arms.add(arm_id)
                self.ctx.warning(
                    f"'{subj_str}' may be mutated by '{mcall.method}()' in this "
                    f"arm while pattern bindings borrow its storage; the "
                    f"bindings dangle (undefined behavior). Copy the bound "
                    f"values before the call", mcall)
        self.pending_match_subject_checks.clear()

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

    def _pack_kwargs_into_typed_dict(self, expr: TpyCall, func: FunctionInfo) -> None:
        """Pack call-site kwargs or **expr into a TypedDict construction for **kwargs param.

        Splits kwargs between regular params and TypedDict fields, builds the TD
        construction, and stores it on expr.kwarg_td_call. The caller appends it
        to expr.args AFTER regular kwargs resolution (so positional ordering is correct).
        """
        kwarg_param = next(p for p in func.params if p.name == func.kwarg_name)
        td_type = unwrap_ref_type(kwarg_param.type)

        if expr.double_star_unpack is not None:
            if expr.kwargs:
                raise self.ctx.error(
                    "Cannot mix keyword arguments with **unpacking", expr)
            unpack_type = self.expr.analyze_expr(expr.double_star_unpack)
            unpack_type = unwrap_ref_type(unpack_type)
            self.compat.check_type_compatible(
                unpack_type, td_type,
                f"**kwargs unpacking (expected {td_type})",
                loc=expr.loc,
            )
            expr.kwarg_td_call = expr.double_star_unpack
            expr.double_star_unpack = None
            return

        record = self.ctx.registry.get_record(td_type.name)
        if record is None:
            raise self.ctx.error(
                f"**kwargs type '{td_type.name}' is not a TypedDict", expr)

        # Split kwargs: regular param kwargs stay for _resolve_call_kwargs,
        # remaining kwargs go to the TD constructor.
        # Unlike direct TypedDict construction, kwargs context honors field defaults
        # (fields with defaults become optional keyword params).
        regular_param_names = {p.name for p in func.params if p.name != func.kwarg_name}
        td_kwargs = {}
        remaining_kwargs = {}
        for k, v in expr.kwargs.items():
            if k in regular_param_names:
                remaining_kwargs[k] = v
            else:
                td_kwargs[k] = v
        td_call = self._build_td_call(record, td_kwargs, f"{func.name}()", expr)
        expr.kwarg_td_call = td_call
        expr.kwargs = remaining_kwargs

    def _pack_kwargs_into_typed_dict_method(self, expr: 'TpyMethodCall', func: FunctionInfo) -> None:
        """Pack call-site kwargs or **expr into a TypedDict for method **kwargs param."""
        kwarg_param = next(p for p in func.params if p.name == func.kwarg_name)
        td_type = unwrap_ref_type(kwarg_param.type)

        if expr.double_star_unpack is not None:
            if expr.kwargs:
                raise self.ctx.error(
                    "Cannot mix keyword arguments with **unpacking", expr)
            unpack_type = self.expr.analyze_expr(expr.double_star_unpack)
            unpack_type = unwrap_ref_type(unpack_type)
            self.compat.check_type_compatible(
                unpack_type, td_type,
                f"**kwargs unpacking (expected {td_type})",
                loc=expr.loc,
            )
            expr.args.append(expr.double_star_unpack)
            expr.double_star_unpack = None
            return

        record = self.ctx.registry.get_record(td_type.name)
        if record is None:
            raise self.ctx.error(f"**kwargs type '{td_type.name}' is not a TypedDict", expr)
        td_call = self._build_td_call(record, dict(expr.kwargs), f"{expr.method}()", expr)
        expr.args.append(td_call)
        expr.kwargs = {}

    def _build_td_call(self, record: RecordInfo, td_kwargs: dict, func_name: str, expr) -> TpyCall:
        """Build a TypedDict constructor call from kwargs, with type-checking."""
        init_params = [
            (fld.name, fld.type, fld.default_expr) for fld in record.fields
        ]
        td_args = resolve_kwargs_init_params(
            [], td_kwargs, init_params, func_name,
            lambda msg: self.ctx.error(msg, expr),
            call_loc=expr.loc, keyword_only=True,
        )
        # Pad trailing fields that have defaults (resolve_kwargs stops at rightmost)
        for fld in record.fields[len(td_args):]:
            if fld.default_expr is not None:
                td_args.append(dc_replace(fld.default_expr, loc=expr.loc))
            else:
                raise self.ctx.error(
                    f"'{func_name}' missing required keyword argument: '{fld.name}'", expr)
        td_call = TpyCall(TpyName(record.name, loc=expr.loc), td_args, loc=expr.loc)
        for i, (arg, fld) in enumerate(zip(td_args, record.fields)):
            arg_type = self.expr.analyze_expr_with_hint(arg, fld.type)
            td_call.args[i] = self.compat.coerce_expr(
                arg, arg_type, fld.type, f"argument '{fld.name}'",
                coercion_ctx=CoercionContext.ARG)
        self.ctx.set_expr_type(td_call, NominalType(record.name, _module_qname=record.qualified_name()))
        return td_call

    def _resolve_inferred_type_arg(self, t: "TpyType | int") -> "TpyType | int":
        return resolve_inferred_type_arg(t, self.ctx.default_int_type)

    def _raise_empty_container_from_arg(self, outer_call: TpyCall, args, arg_types) -> 'NoReturn':
        """Raise a user-facing "cannot infer empty container types" error.

        Called when a generic constructor (e.g. `list(...)`) matched an
        overload whose type params inferred to `UnknownElementType`, which
        means an empty-container arg (`set()`, `dict()`, `{}`) still has
        unresolved element types. The resolve_all pass emits the same
        message when the pending container is seen standalone; this path
        surfaces the message early so we don't feed UNKNOWN_ELEMENT into
        cpp_template substitution and crash.
        """
        for arg, arg_type in zip(args, arg_types):
            kind_word = None
            if isinstance(arg_type, PendingListType):
                kind_word = "list"
            elif isinstance(arg_type, PendingSetType):
                kind_word = "set"
            elif isinstance(arg_type, PendingDictType):
                kind_word = "dict"
            if kind_word is None:
                continue
            raise self.ctx.error(
                f"Cannot infer element type for empty {kind_word} passed to "
                f"{outer_call.func_name}(); add a type annotation on the "
                f"outer variable (e.g., `x: list[T] = {outer_call.func_name}(...)`) "
                f"or on the empty {kind_word} itself",
                arg,
            )
        # Fallback: no pending arg found -- still bail out without crashing.
        raise self.ctx.error(
            f"Cannot infer type parameters for {outer_call.func_name}() from "
            f"arguments; provide an explicit type annotation",
            outer_call,
        )

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
                is_constructor=True,
                # Carry mutation facts so `_check_borrow_arg_conflicts`
                # reads them off `expr.resolved_function_info` -- without
                # this, ctor calls see `mutated_params=None` and emit
                # false-positive "borrowed container" warnings even when
                # the ctor provably doesn't structurally mutate the arg.
                mutated_params=resolved_ctor.mutated_params,
                structural_mutated_params=resolved_ctor.structural_mutated_params,
                canonical_fi=ctor.root,
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
            is_constructor=True,
        )

    def _record_for_local_name(self, name: str) -> 'RecordInfo | None':
        """Resolve a possibly import-aliased local name to its record.

        `get_record(name)` is keyed by short name and collides for two records
        sharing a short name imported from different modules. Override it only
        when the import table proves the short-name lookup landed in a *different*
        module (the genuine collision) -- otherwise the plain lookup is kept, so
        builtins and ordinary single records resolve exactly as before.
        """
        rec = self.ctx.registry.get_record(name)
        imp = self.ctx.imported_names.get(name)
        if imp is not None and rec is not None and rec.defining_module != imp[0]:
            authoritative = self.ctx.registry.find_module_record(*imp)
            if authoritative is not None:
                return authoritative
        return rec

    def analyze_call(self, expr: TpyCall) -> TpyType:
        """Analyze a function or constructor call."""
        # Expression callees: callbacks[0](x), get_handler()(x), etc.
        if not isinstance(expr.func, TpyName):
            return self._analyze_expr_callee(expr)

        # Type aliases: builtin type aliases (e.g. Float64 = float) resolve
        # to the underlying type's constructor. Other aliases are not callable.
        # Exception: recursive union aliases are callable as wrapper constructors.
        if expr.call_type is None:
            alias_type = self.ctx.registry.get_type_alias(expr.func_name)
            if alias_type is not None:
                # Recursive union alias: Tree(value) wraps value in the union
                if expr.func_name in self.ctx.recursive_union_names:
                    return self._analyze_recursive_union_constructor(expr, alias_type)
                record = self.ctx.registry.get_record_for_type(alias_type)
                if record and record.builtin_type_key and record.get_method_overloads("__init__"):
                    return self._analyze_record_constructor(expr, record)
                # `expanded_str()` (not str()) so the suggestion shows the
                # structural form rather than the rejected alias name.
                suggestion = (alias_type.expanded_str()
                              if isinstance(alias_type, UnionType)
                              else str(alias_type))
                raise self.ctx.error(
                    f"Type alias '{expr.func_name}' is not callable. "
                    f"Use {suggestion} directly, or let the type be inferred from an annotation",
                    expr
                )

        # Generic type instantiation (e.g., Container[T, N](), Array[Int32, 8]())
        # The parser speculatively sets call_type for any imported name, so
        # verify it's actually a type before using it
        if expr.call_type is not None:
            # Check if this is actually a function -- the parser speculatively
            # sets call_type for any imported name, so we need to verify
            is_known_function = self.ctx.registry.get_function(expr.func_name) is not None
            if not is_known_function and self.ctx.func.current_ns:
                binding = self.ctx.func.current_ns.lookup(expr.func_name)
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
                record = self._record_for_local_name(expr.func_name)
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
                    td = find_factory_by_simple_name(expr.func_name)
                    if td is not None:
                        rec = self.ctx.registry.get_builtin_record(td.qname)
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
        # Stores the original name (not alias) for factory lookup by simple name
        imported_generic_name: str | None = None
        imported_generic_module: str | None = None

        # Use namespace for unified lookup - handles shadowing automatically
        if self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(expr.func_name)
            if binding:
                if binding.kind == BindingKind.VARIABLE:
                    var_type = self.ctx.func.narrowed_types.get(expr.func_name, binding.type)
                    # Strip Own[T] -- Own is a storage property, not a type distinction
                    if isinstance(var_type, OwnType):
                        var_type = var_type.wrapped
                    # Send/Sync markers add a guarantee, not callability
                    var_type = unwrap_send_sync(var_type)
                    if is_fn_type(var_type):
                        return self._analyze_fn_type_call(expr, var_type)
                    if isinstance(var_type, CallableType):
                        return self._analyze_callable_type_call(expr, var_type)
                    # Check for record type with __call__ method
                    if isinstance(var_type, NominalType):
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
                    # Check for call-site macro before other handling.
                    # Walks the re-export chain via the attribute table so
                    # `from utils import asdict` (where utils re-exports
                    # from dataclasses) resolves to the macro registered
                    # under the ultimate macro module's key.
                    if self.ctx.macro_registry:
                        macro_fn = self.ctx.macro_registry.get_call_macro(module_name, func_name)
                        ult_mod, ult_name = module_name, func_name
                        if macro_fn is None:
                            chain = self._resolve_call_macro_chain(module_name, func_name)
                            if chain is not None:
                                ult_mod, ult_name = chain
                                macro_fn = self.ctx.macro_registry.get_call_macro(ult_mod, ult_name)
                        if macro_fn is not None:
                            return self._expand_call_macro(expr, macro_fn, ult_mod, ult_name)
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
                    if qname in (qnames.ASYNCIO_CREATE_TASK, qnames.ASYNCIO_RUN):
                        self._require_async_def_call_arg(expr, qname)
                        # falls through to normal cpp_template lowering
                    if qname == "builtins.isinstance":
                        return self._analyze_isinstance(expr)
                    if qname == "builtins.super":
                        # Lazy: methods.py imports calls at module load; cycle breaks here.
                        from .methods import MethodAnalyzer
                        return MethodAnalyzer._analyze_super_call_static(self.ctx, expr)
                    if qname == "builtins.print":
                        return self._analyze_print_call(expr)
                    # Check for user module function (registered via _register_user_module_import)
                    if func_infos := self.ctx.registry.get_function(expr.func_name):
                        # Builtin-supplemented functions route through builtin path
                        if func_infos[0].is_builtin_function:
                            if func_infos[0].special_handling:
                                return self._analyze_special_builtin(expr, func_infos)
                            return self._analyze_builtin_function_overloads(expr, func_infos)
                        return self._analyze_user_function_call(expr, func_infos)
                    # Check for user module record (registered via _register_user_module_import).
                    # Import-aware: get_record by short name collides for same-name
                    # records from different modules.
                    if record_info := self._record_for_local_name(expr.func_name):
                        return self._analyze_record_constructor(expr, record_info)
                    # Check for module function (e.g., math.sqrt)
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
                    if find_factory_in_module(func_name, module_name) is not None:
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
            if td := find_factory_by_simple_name(expr.func_name):
                if td.qname.startswith("tpy."):
                    raise self.ctx.error(
                        f"'{expr.func_name}' requires: from tpy import {expr.func_name}",
                        expr
                    )

        # Fallback: Check if it's a record constructor
        record = self._record_for_local_name(expr.func_name)
        if record:
            # Analyze arguments
            for arg in expr.args:
                self.expr.analyze_expr(arg)
            return NominalType(record.name, _module_qname=record.qualified_name())

        # Fallback: Check if it's a function call
        func_infos = self.ctx.registry.get_function(expr.func_name)
        if func_infos:
            return self._analyze_legacy_function_call(expr, func_infos[0])

        # Generic type constructor without context for type inference
        # Only proceed if we found an imported generic type in namespace
        if imported_generic_name and imported_generic_module and (
            lookup_td := find_factory_in_module(imported_generic_name, imported_generic_module)
        ):
            record_info = self.ctx.registry.get_builtin_record(lookup_td.qname)
            if not record_info:
                raise self.ctx.error(f"Unknown type '{expr.func_name}'", expr)
            params = ", ".join(record_info.type_params)

            # Check for constructors that can infer type from arguments
            if expr.args:
                arg_types = [unwrap_own(unwrap_ref_type(self.expr.analyze_expr(arg))) for arg in expr.args]
                # Empty-container args (`set()`, `{}`, `[]` with no type hint)
                # can't drive ctor inference -- their element types are
                # UnknownElementType. Emit the proper user-facing diagnostic
                # now so we don't feed UNKNOWN or unresolved TypeParamRefs
                # into cpp_template substitution (which raises
                # "UnknownElementType should be resolved before codegen" or
                # "Unknown type parameter 'K'").
                for arg, at in zip(expr.args, arg_types):
                    if isinstance(at, PendingListType) and isinstance(at.element_type, UnknownElementType):
                        self._raise_empty_container_from_arg(expr, expr.args, arg_types)
                    if isinstance(at, PendingSetType) and isinstance(at.element_type, UnknownElementType):
                        self._raise_empty_container_from_arg(expr, expr.args, arg_types)
                    if isinstance(at, PendingDictType) and (
                            isinstance(at.key_type, UnknownElementType)
                            or isinstance(at.value_type, UnknownElementType)):
                        self._raise_empty_container_from_arg(expr, expr.args, arg_types)
                init_overloads = record_info.get_method_overloads("__init__")
                if init_overloads:
                    for ctor in init_overloads:
                        if len(ctor.params) != len(arg_types):
                            continue
                        # Try to match and infer type parameters
                        inferred_params = self.type_ops.match_generic_constructor(ctor.params, arg_types)
                        if inferred_params is not None:
                            # Guard against UNKNOWN_ELEMENT leaking from a
                            # `Pending*` arg (e.g. `list(set())`, `list({})`).
                            # The element is still unresolved; we must not feed
                            # UnknownElementType into the type factory or cpp
                            # template substitution. Fire the same
                            # empty-container diagnostic that resolve_all
                            # would, sourced from the offending Pending* arg.
                            if any(isinstance(v, UnknownElementType)
                                   for v in inferred_params.values()):
                                self._raise_empty_container_from_arg(expr, expr.args, arg_types)
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
                                        ctor, clean_params, result_type=result_type, ctx=self.ctx)
                                self._validate_lvalue_params(expr)
                                self._check_ctor_arg_compatibility(expr, ctor, arg_types, inferred_params)
                                return result_type
                            # T not inferred from args; try assignment target hint
                            if record_info.type_factory and self.ctx.expr_type_hint is not None:
                                hint = self.ctx.expr_type_hint
                                if isinstance(hint, OwnType):
                                    hint = hint.wrapped
                                hint = unwrap_readonly(hint)
                                if hint.qualified_name() == lookup_td.qname:
                                    expr.call_type = hint
                                    if ctor.cpp_template or ctor.native_function:
                                        hint_params = extract_type_params(hint)
                                        expr.resolved_function_info = _resolve_cpp_template_type_params(
                                            ctor, hint_params, result_type=hint, ctx=self.ctx)
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
                    and isinstance(self.ctx.func.current_function, TpyFunction)
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
                self.ctx.func.pending_resolutions.append(literal_id)
                result_type = PendingListType(UNKNOWN_ELEMENT, 0, literal_id)
                expr.call_type = result_type
                return result_type
            # dict() with no args -- create empty dict with unknown key/value types.
            if (expr.func_name == "dict"
                    and isinstance(self.ctx.func.current_function, TpyFunction)
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
                self.ctx.func.pending_dict_resolutions.append(literal_id)
                result_type = PendingDictType(UNKNOWN_ELEMENT, UNKNOWN_ELEMENT, literal_id)
                expr.call_type = result_type
                return result_type
            # set() with no args -- create empty set with unknown element type.
            if (expr.func_name == "set"
                    and isinstance(self.ctx.func.current_function, TpyFunction)
                    and record_info.type_factory):
                literal_id = self.ctx.literal_counter
                self.ctx.literal_counter += 1
                info = SetLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    element_type=UNKNOWN_ELEMENT,
                )
                self.ctx.set_literals[literal_id] = info
                self.ctx.func.pending_set_resolutions.append(literal_id)
                result_type = PendingSetType(UNKNOWN_ELEMENT, literal_id)
                expr.call_type = result_type
                return result_type
            raise self.ctx.error(
                f"Cannot infer element type for {expr.func_name}(); "
                f"use {expr.func_name}[{params}](), provide a type annotation, or pass an iterable",
                expr
            )

        if self.ctx.func.in_nested_def and expr.func_name == self.ctx.func.nested_def_name:
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
        if qname == qnames.ASSERT_SEND:
            return self._analyze_send_sync_assertion(expr, send=True)
        if qname == qnames.ASSERT_SYNC:
            return self._analyze_send_sync_assertion(expr, send=False)
        if qname == "builtins.isinstance":
            return self._analyze_isinstance(expr)
        if qname == "typing.cast":
            return self._analyze_typing_cast(expr)
        if qname == "builtins.print":
            return self._analyze_print_call(expr)
        if qname == "builtins.getattr":
            return self._analyze_getattr_builtin(expr)
        if qname == "builtins.setattr":
            return self._analyze_setattr_builtin(expr)
        if qname == "builtins.delattr":
            return self._analyze_delattr_builtin(expr)
        if qname == "builtins.hasattr":
            return self._analyze_hasattr_builtin(expr)
        raise self.ctx.error(f"Unknown special builtin: '{qname}'", expr)

    def _extract_dyn_builtin_target(
        self, expr: TpyCall, builtin: str, expected_args: int | tuple[int, ...],
    ) -> tuple[TpyExpr, TpyExpr, str | None, NominalType, 'RecordInfo']:
        """Shared validation for getattr/setattr/delattr/hasattr builtins (D16).

        Validates: no kwargs, arg count, str-typed name, record receiver.
        `expected_args` may be a single count or a tuple of accepted counts.
        Returns (obj_arg, name_arg, literal_name_or_None, actual_type, record).
        Non-literal names route everything to the dunder (D16 phase 9, Option A);
        callers should skip the declared-member check when literal_name is None.
        """
        if expr.kwargs:
            raise self.ctx.error(f"{builtin}() does not accept keyword arguments", expr)
        accepted = (expected_args,) if isinstance(expected_args, int) else expected_args
        if len(expr.args) not in accepted:
            wanted = " or ".join(str(n) for n in accepted)
            raise self.ctx.error(
                f"{builtin}() expects {wanted} arguments, got {len(expr.args)}", expr)
        obj_arg = expr.args[0]
        name_arg = expr.args[1]
        name_type = self.expr.analyze_expr(name_arg)
        if not is_any_str_type(unwrap_readonly(name_type)):
            raise self.ctx.error(
                f"{builtin}() name argument must be str; got '{name_type}'", expr)
        literal_name = name_arg.value if isinstance(name_arg, TpyStrLiteral) else None
        obj_type = self.expr.analyze_expr(obj_arg)
        actual_type = unwrap_qualifiers(obj_type)
        record = (self.ctx.registry.get_record_for_type(actual_type)
                  if isinstance(actual_type, NominalType) and actual_type.is_record
                  else None)
        if record is None:
            raise self.ctx.error(
                f"{builtin}() requires a record receiver; got '{obj_type}'", expr)
        return obj_arg, name_arg, literal_name, actual_type, record

    def _declared_member_kind(self, record: 'RecordInfo', name: str) -> str | None:
        """Return the kind ('field' / 'property' / 'method' / 'class constant')
        if `name` resolves to a declared member of `record`, else None."""
        if self.protocols.lookup_record_field(record, name) is not None:
            return "field"
        if self.protocols.lookup_record_property(record, name) is not None:
            return "property"
        if self.protocols.lookup_record_method_overloads(record, name)[0]:
            return "method"
        if name in record.class_constants:
            return "class constant"
        return None

    def _reject_declared_member_in_dyn_builtin(
        self, record: 'RecordInfo', name: str, builtin: str, expr: TpyCall,
        op_suffix: str, fixit: str,
    ) -> None:
        """Reject `<builtin>(obj, "<name>", ...)` when name resolves to a
        declared field / property / method / class constant. v1 builtins are
        dynamic-fallback only; these cases are TODO.md:101 territory.

        op_suffix is interpolated into the error (e.g. "" for getattr,
        ", v" for setattr, "" for delattr) so each builtin's wording stays
        consistent. fixit is the suggested replacement (e.g. "obj.{name}").
        """
        kind = self._declared_member_kind(record, name)
        if kind is not None:
            hint = f"; use '{fixit}'" if fixit else ""
            raise self.ctx.error(
                f"{builtin}(obj, \"{name}\"{op_suffix}) for declared {kind} "
                f"is not supported{hint}",
                expr,
            )

    def _analyze_getattr_builtin(self, expr: TpyCall) -> TpyType:
        """`getattr(obj, name[, default])` -- always routed through `__getattr__`
        (Option A: route-all-to-dunder, regardless of literal vs runtime name)."""
        obj_arg, name_arg, name, actual_type, record = self._extract_dyn_builtin_target(
            expr, "getattr", expected_args=(2, 3))
        if name is not None:
            self._reject_declared_member_in_dyn_builtin(
                record, name, "getattr", expr, op_suffix="", fixit=f"obj.{name}")
        ga_overloads, type_subst = self.protocols.lookup_record_method_overloads(
            record, "__getattr__")
        if not ga_overloads:
            target = f"'{name}'" if name is not None else "(runtime name)"
            raise self.ctx.error(
                f"Record '{actual_type.name}' has no field {target} and does not "
                f"define __getattr__",
                expr,
            )
        ga = ga_overloads[0]
        synth = TpyMethodCall(obj=obj_arg, method="__getattr__", args=[name_arg])
        synth.resolved_function_info = ga
        ret_type = ga.return_type
        if type_subst and ret_type is not None:
            ret_type = self.type_ops.substitute_type_params(ret_type, type_subst)
        if len(expr.args) == 3:
            default_arg = expr.args[2]
            if ret_type is not None:
                default_type = self.expr.analyze_expr_with_hint(default_arg, ret_type)
                expr.args[2] = self.compat.coerce_expr(
                    default_arg, default_type, ret_type,
                    "getattr() default argument",
                    coercion_ctx=CoercionContext.ARG,
                )
            else:
                self.expr.analyze_expr(default_arg)
            expr.dyn_getattr_default_call = synth
        else:
            expr.macro_expansion = synth
        return ret_type if ret_type is not None else VOID

    def _analyze_hasattr_builtin(self, expr: TpyCall) -> TpyType:
        """`hasattr(obj, name)`.

        Literal name: static fold if the name is declared (True) or the class
        has no `__getattr__` (False); otherwise runtime try/catch.
        Non-literal name: routes everything to the dunder (Option A); the
        runtime check returns True if the dunder succeeds, False if it raises
        AttributeError. Requires a dyn-readable receiver.
        """
        obj_arg, name_arg, name, actual_type, record = self._extract_dyn_builtin_target(
            expr, "hasattr", expected_args=2)
        if name is not None and self._declared_member_kind(record, name) is not None:
            expr.macro_expansion = TpyBoolLiteral(value=True, loc=expr.loc)
            return BOOL
        ga_overloads, _ = self.protocols.lookup_record_method_overloads(
            record, "__getattr__")
        if not ga_overloads:
            if name is not None:
                expr.macro_expansion = TpyBoolLiteral(value=False, loc=expr.loc)
                return BOOL
            raise self.ctx.error(
                f"Record '{actual_type.name}' has no field (runtime name) and does "
                f"not define __getattr__",
                expr,
            )
        ga = ga_overloads[0]
        synth = TpyMethodCall(obj=obj_arg, method="__getattr__", args=[name_arg])
        synth.resolved_function_info = ga
        expr.dyn_hasattr_call = synth
        return BOOL

    def _analyze_setattr_builtin(self, expr: TpyCall) -> TpyType:
        """`setattr(obj, name, value)` -- routes to `__setattr__` (dynamic-fallback only)."""
        obj_arg, name_arg, name, actual_type, record = self._extract_dyn_builtin_target(
            expr, "setattr", expected_args=3)
        value_arg = expr.args[2]
        if name is not None:
            self._reject_declared_member_in_dyn_builtin(
                record, name, "setattr", expr, op_suffix=", v", fixit=f"obj.{name} = v")
        sa_overloads, _ = self.protocols.lookup_record_method_overloads(record, "__setattr__")
        if not sa_overloads:
            target = f"'{name}'" if name is not None else "(runtime name)"
            raise self.ctx.error(
                f"Record '{actual_type.name}' has no field {target} and does not "
                f"define __setattr__",
                expr,
            )
        # Delegate to method-call analysis so arg coercion (e.g. into-Any) applies.
        synth = TpyMethodCall(
            obj=obj_arg, method="__setattr__", args=[name_arg, value_arg], loc=expr.loc)
        self.expr.analyze_expr(synth)
        expr.macro_expansion = synth
        return VOID

    def _analyze_delattr_builtin(self, expr: TpyCall) -> TpyType:
        """`delattr(obj, name)` -- routes to `__delattr__` (dynamic-fallback only)."""
        obj_arg, name_arg, name, actual_type, record = self._extract_dyn_builtin_target(
            expr, "delattr", expected_args=2)
        if name is not None:
            self._reject_declared_member_in_dyn_builtin(
                record, name, "delattr", expr, op_suffix="", fixit="")
        da_overloads, _ = self.protocols.lookup_record_method_overloads(record, "__delattr__")
        if not da_overloads:
            target = f"'{name}'" if name is not None else "(runtime name)"
            raise self.ctx.error(
                f"Record '{actual_type.name}' has no field {target} and does not "
                f"define __delattr__",
                expr,
            )
        synth = TpyMethodCall(
            obj=obj_arg, method="__delattr__", args=[name_arg], loc=expr.loc)
        self.expr.analyze_expr(synth)
        expr.macro_expansion = synth
        return VOID

    def _analyze_print_call(self, expr: TpyCall) -> TpyType:
        """Validate and type-check builtins.print() with sep=/end=/file=/flush=.

        sep/end accept any string-typed expression. flush must be a bool
        literal. file must satisfy the Writable protocol.
        """
        allowed_kwargs = ("end", "sep", "file", "flush")
        for kw_name in expr.kwargs:
            if kw_name not in allowed_kwargs:
                raise self.ctx.error(
                    f"print() does not support keyword argument '{kw_name}'", expr)
        for kw_name in ("end", "sep"):
            if kw_name in expr.kwargs:
                kw_type = self.expr.analyze_expr(expr.kwargs[kw_name])
                if not is_any_str_type(unwrap_readonly(kw_type)):
                    raise self.ctx.error(
                        f"print() '{kw_name}' argument must be a string; "
                        f"got '{kw_type}'", expr)
        if "flush" in expr.kwargs:
            flush_expr = expr.kwargs["flush"]
            if not isinstance(flush_expr, TpyBoolLiteral):
                raise self.ctx.error(
                    "print() 'flush' argument must be a bool literal "
                    "(True or False)", expr)
            self.expr.analyze_expr(flush_expr)
        if "file" in expr.kwargs:
            file_expr = expr.kwargs["file"]
            file_type = self.expr.analyze_expr(file_expr)
            actual = unwrap_readonly(unwrap_own(file_type))
            writable_proto = NominalType("Writable", (), is_protocol=True)
            if not self.protocols.type_conforms_to_protocol(actual, writable_proto):
                raise self.ctx.error(
                    f"print() 'file' argument must satisfy the Writable protocol "
                    f"(write(str) -> Int32, flush() -> None); got '{actual}'",
                    expr)
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

    def _get_module_function_overloads(self, module_name: str, func_name: str) -> list[FunctionInfo] | None:
        """Look up function overloads in a module using the unified registry."""
        module_info = self.ctx.registry.get_module(module_name)
        if module_info and func_name in module_info.functions:
            return module_info.functions[func_name]
        return None

    def _require_async_def_call_arg(self, expr: TpyCall, qname: str) -> None:
        """v1: `asyncio.run` / `asyncio.create_task` accept only a direct
        call to a known async def. Other Awaitables (Future, Task, custom)
        match the cpp_template's `Awaitable[T]` parameter at sema, but the
        C++ helpers assume a coroutine struct (touch `__cancel_pending` /
        emit a Poll-deducing `decltype` of the awaited value). Reject at
        sema with a clear diagnostic instead of letting the C++ build
        fail with a template error.
        """
        if len(expr.args) != 1:
            return  # arity error will be reported by normal resolution
        if self.expr._resolve_call_to_async_def(expr.args[0]) is not None:
            return
        short_name = qname.split(".", 1)[1]
        raise self.ctx.error(
            f"asyncio.{short_name}() requires a direct call to an async "
            f"def in v1; pass `f(...)` where `f` is `async def f(...) -> T`",
            expr)

    def _analyze_tpy_copy(self, expr: TpyCall) -> TpyType:
        """Analyze a call to tpy.copy() - explicit copy for ownership transfer.

        copy() is truly generic (works with any type T, returns Own[T]).
        This is handled specially because the module system doesn't support
        truly generic functions yet.
        """
        self._reject_kwargs_for_builtin(expr, "copy")
        if len(expr.args) != 1:
            raise self.ctx.error("copy() takes exactly 1 argument", expr)
        prev_in_copy = self.ctx.func.in_copy_call_arg
        self.ctx.func.in_copy_call_arg = True
        try:
            arg_type = self.expr.analyze_expr(expr.args[0])
        finally:
            self.ctx.func.in_copy_call_arg = prev_in_copy
        # Unwrap OwnType if already wrapped
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        # Use declared union type instead of narrowed member type so copy()
        # preserves the full union (e.g. copy(pet) where pet: Dog | Cat is
        # narrowed to Dog still returns Own[Dog | Cat])
        arg = expr.args[0]
        if isinstance(arg, TpyName) and self.ctx.func.current_scope:
            binding_type = self.ctx.func.current_scope.lookup(arg.name)
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
        elem_type = builtin_modules.get_iterable_element_type(arg_type, registry=self.ctx.registry)
        if elem_type is None:
            raise self.ctx.error(
                f"copy_iter() argument must be iterable, got {arg_type}", expr)
        # copy_iter produces owned copies -- strip Ref
        elem_type = unwrap_ref_type(elem_type)
        result_type = make_copy_iter(elem_type)
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
        if not is_list(arg_type):
            raise self.ctx.error(
                f"own_iter() currently only supports list, got {arg_type}", expr)
        # Validate that the argument is at last use -- own_iter moves
        # the container, so using it afterwards is use-after-move.
        arg = expr.args[0]
        if isinstance(arg, TpyName):
            if not self.compat.is_auto_move_use(arg):
                self.ctx.warning(
                    f"own_iter() consumes '{arg.name}' -- "
                    f"using it afterwards is undefined behavior. "
                    f"Use a regular for-loop if the container is needed later.",
                    expr,
                )
            else:
                self.compat.check_own_consumption(arg)
        elem_type = builtin_modules.get_iterable_element_type(arg_type, registry=self.ctx.registry)
        assert elem_type is not None
        result_type = make_own_iter(elem_type)
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
        """Analyze try_parse(EnumT, str) -> Optional[EnumT] for some enum type EnumT."""
        self._reject_kwargs_for_builtin(expr, "try_parse")
        if len(expr.args) != 2:
            raise self.ctx.error(
                "try_parse() takes exactly 2 arguments: try_parse(EnumT, name)",
                expr,
            )
        first_arg = expr.args[0]
        if not isinstance(first_arg, TpyName):
            raise self.ctx.error(
                "try_parse() first argument must be an enum type name",
                expr,
            )
        # Resolve the name to an enum type
        if self.ctx.func.current_ns is None:
            raise self.ctx.error(
                "try_parse() first argument must be an enum type name",
                expr,
            )
        binding = self.ctx.func.current_ns.lookup(first_arg.name)
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
        expr.resolved_function_info = FunctionInfo(
            name="try_parse",
            params=[],
            return_type=OptionalType(enum_type),
            is_builtin_function=True,
            special_handling=True,
            qualified_name="tpy.try_parse",
        )
        return OptionalType(enum_type)

    def _analyze_send_sync_assertion(self, expr: TpyCall, send: bool) -> TpyType:
        """assert_send[T]() / assert_sync[T](): zero-cost compile-time trait
        check. Fails with a why-not chain when T does not hold; elided in
        codegen (see TpyCall.compile_time_assert)."""
        name = "assert_send" if send else "assert_sync"
        trait = "Send" if send else "Sync"
        self._reject_kwargs_for_builtin(expr, name)
        if expr.args:
            raise self.ctx.error(f"{name}() takes no value arguments", expr)
        self._validate_explicit_type_args(expr, 1)
        if len(expr.type_args) != 1 or expr.type_args[0] is None:
            raise self.ctx.error(
                f"{name}[T]() requires exactly one type argument", expr)
        target = expr.type_args[0]
        holds = target.is_send() if send else target.is_sync()
        if not holds:
            chain = why_not_send(target) if send else why_not_sync(target)
            detail = render_chain(chain, send) if chain is not None else f"{target} is not {trait}"
            raise self.ctx.error(f"{name} assertion failed: {detail}", expr)
        expr.compile_time_assert = True
        expr.resolved_function_info = FunctionInfo(
            name=name,
            params=[],
            return_type=VOID,
            is_builtin_function=True,
            special_handling=True,
            qualified_name=qnames.ASSERT_SEND if send else qnames.ASSERT_SYNC,
        )
        return VOID

    def _resolve_isinstance_type(
        self, name: str, expr: TpyCall, *, allow_any: bool = False,
        allow_bare_generic: bool = False,
    ) -> TpyType:
        """Resolve a type name used as the second argument to isinstance()
        (or as the first arg to typing.cast).

        Handles user-defined records and builtin type names (int, str, bool, float,
        fixed-int types like Int32, etc.). Resolves `Any` (bare or aliased
        via `from typing import Any as A`) to AnyType when `allow_any=True`;
        otherwise rejects with the isinstance-flavoured "Any is not a
        runtime class" error. typing.cast wants the resolved AnyType so it
        can raise its own cast-flavoured rejection at one site.

        `allow_bare_generic` (isinstance only) resolves a bare container
        generic -- `dict` / `list` / `set` -- and an alias of one to a
        bare NominalType, so the union-membership path narrows to the matching
        member. A *parameterized* generic or its alias (`dict[str, JsonValue]`)
        is rejected, mirroring CPython (`isinstance(x, dict[str, int])` raises
        "cannot be a parameterized generic"). Off for typing.cast.
        """
        if (name == "Any"
                or self.ctx.imported_names.get(name) == ("typing", "Any")):
            if allow_any:
                return ANY
            raise self.ctx.error(
                "isinstance() second argument cannot be Any -- "
                "Any is not a runtime class",
                expr,
            )
        # User-defined records. Resolve through the import table first: a bare
        # `get_record(name)` is keyed by short name and collides for same-name
        # records imported from different modules, so it would test against the
        # wrong class (and narrow to its qname).
        imp = self.ctx.imported_names.get(name)
        if imp is not None:
            module, original = imp
            record = self.ctx.registry.find_module_record(module, original)
            if record:
                return NominalType(original, _module_qname=record.qualified_name())
        record = self.ctx.registry.get_record(name)
        if record:
            return NominalType(name, _module_qname=record.qualified_name())
        # Builtin type names
        resolved = _resolve_concrete_type_name(name)
        if resolved is not None:
            return resolved
        # Generic type alias used as an isinstance second arg.  A non-
        # recursive generic alias (`type Pair[T] = ...`) has no runtime
        # identity -- the resolver expanded `Pair[T]` use sites to the
        # body, so there's no `Pair` class to test against.  Direct the
        # user to test the body's expanded members instead.  See Codex
        # review point B in docs/GENERIC_RECURSIVE_ALIASES_DESIGN.md.
        alias_info = self.ctx.registry.get_type_alias_info(name)
        if alias_info is not None and alias_info.type_params:
            raise self.ctx.error(
                f"isinstance() does not support generic type alias "
                f"'{name}' -- generic aliases have no runtime identity. "
                f"Test the expanded type's members directly.",
                expr,
            )
        if allow_bare_generic:
            # Bare container generic (`dict`) -- runtime-checkable against the
            # value's variant alternative, exactly what `case dict():` emits.
            if name in self._BARE_RUNTIME_GENERICS:
                return NominalType(name)
            if alias_info is not None and isinstance(alias_info.body, NominalType):
                body = alias_info.body
                # An alias of a container generic, bare or parameterized, has
                # no runtime identity beyond the bare class: `dict[str, V]`
                # CPython rejects at runtime, and bare `dict` is not a complete
                # TPy type. Point at the bare form either way.
                if body.name in self._BARE_RUNTIME_GENERICS:
                    raise self.ctx.error(
                        f"isinstance() does not support the container generic "
                        f"alias '{name}' ('{body}'); use the bare "
                        f"'{body.name}': `isinstance(x, {body.name})`.",
                        expr,
                    )
                # Alias of a complete concrete type (`type Foo = MyRecord`):
                # resolve to the body so the check tests that type.
                if not body.is_protocol:
                    return body
        # A union-alias NAME is not isinstance-able: a `type`-statement alias
        # lowers to no runtime class (and CPython rejects a TypeAliasType at
        # runtime too). Point at the inline-union / tuple spellings, which ARE
        # runtime-valid.
        if alias_info is not None and isinstance(alias_info.body, UnionType):
            raise self.ctx.error(
                f"isinstance() does not accept the union alias '{name}' -- a "
                f"type-alias name is not a runtime class. Use the inline union "
                f"`isinstance(x, {alias_info.body.expanded_str()})` or a tuple "
                f"of the concrete members.",
                expr,
            )
        raise self.ctx.error(f"isinstance() second argument must be a type, got '{name}'", expr)

    def _resolve_isinstance_check_types(
        self, second_arg: TpyExpr, expr: TpyCall,
    ) -> list[TpyType]:
        """Resolve the second arg of isinstance() into a list of check types.

        Accepts a single type name, a tuple of type names, or an inline union:
            isinstance(x, A)       -> [A]
            isinstance(x, (A, B))  -> [A, B]
            isinstance(x, A | B)   -> [A, B]
        """
        if isinstance(second_arg, TpyTupleLiteral):
            if not second_arg.elements:
                raise self.ctx.error(
                    "isinstance() tuple of types cannot be empty", expr
                )
            types: list[TpyType] = []
            for elem in second_arg.elements:
                if not isinstance(elem, TpyName):
                    raise self.ctx.error(
                        "isinstance() tuple elements must be type names", expr
                    )
                types.append(self._resolve_isinstance_type(
                    elem.name, expr, allow_bare_generic=True))
            return types
        if isinstance(second_arg, TpyBinOp) and second_arg.op == "|":
            return self._resolve_inline_union_check_types(second_arg, expr)
        if isinstance(second_arg, TpyName):
            return [self._resolve_isinstance_type(
                second_arg.name, expr, allow_bare_generic=True)]
        raise self.ctx.error(
            "isinstance() second argument must be a type name, an inline "
            "union (`A | B`), or a tuple of type names",
            expr,
        )

    def _flatten_union_operands(self, node: TpyExpr) -> 'list[TpyExpr]':
        """Flatten an inline `A | B | C` operand tree (left-leaning BinOps)."""
        if isinstance(node, TpyBinOp) and node.op == "|":
            return (self._flatten_union_operands(node.left)
                    + self._flatten_union_operands(node.right))
        return [node]

    def _resolve_inline_union_check_types(
        self, node: TpyBinOp, expr: TpyCall,
    ) -> list[TpyType]:
        """Resolve an inline-union isinstance second arg (`A | B`) to its
        member check types -- the runtime-valid analog of the rejected
        type-alias form. `isinstance(x, A | B)` is a `types.UnionType` at
        runtime, which CPython accepts (unlike a PEP 695 alias name). A
        parameterized operand (`A | list[int]`) is rejected at parse time;
        a `None` operand is rejected here (see below)."""
        types: list[TpyType] = []
        for operand in self._flatten_union_operands(node):
            if isinstance(operand, TpyNoneLiteral):
                # CPython accepts `A | None`, but the matched branch narrows to
                # an Optional whose `std::get<optional<A>*>` is not a variant
                # alternative -- the extraction miscompiles (see BUGS.md).
                # Reject cleanly; `x is None` narrows None separately.
                raise self.ctx.error(
                    "isinstance() with `None` in an inline union "
                    "(`A | None`) is not supported yet -- narrow None "
                    "separately with `x is None`.",
                    expr,
                )
            elif isinstance(operand, TpyName):
                types.append(self._resolve_isinstance_type(
                    operand.name, expr, allow_bare_generic=True))
            else:
                # A non-type operand (literal / expression); a parameterized
                # generic (`A | list[int]`) is already rejected at parse time.
                raise self.ctx.error(
                    "isinstance() inline-union members must be type names "
                    "(`A | B`); got a non-type operand.",
                    expr,
                )
        return types

    def _isinstance_unwrap(self, typ: TpyType) -> TpyType:
        """Strip Own/readonly/Ptr layers so isinstance sees the pointee type.

        Ptr is treated identically to a value variable: static dispatch makes
        the static pointee type authoritative, so `Ptr[Parent]` and `Parent`
        behave the same for isinstance folding.
        """
        inner = typ
        if isinstance(inner, OwnType):
            inner = inner.wrapped
        inner = unwrap_readonly(inner)
        if isinstance(inner, PtrType):
            inner = unwrap_readonly(inner.pointee)
        return inner

    def _is_union_alias(self, typ: TpyType) -> bool:
        """True if a placeholder type stands in for a union alias.

        Covers two cases: parser-emitted `AliasRef` self-references inside
        a recursive alias body, and bare NominalType forward-references
        to a same-module alias that resolves to a union/optional shape."""
        if isinstance(typ, AliasRef):
            return True
        if not (isinstance(typ, NominalType) and not typ.is_protocol):
            return False
        alias = self.ctx.registry.get_type_alias(typ.name)
        return isinstance(alias, (UnionType, OptionalType))

    # Builtin container generics that have a bare runtime identity: a value
    # either holds a `dict` alternative or it does not. Matches CPython, where
    # `isinstance(x, dict)` works but `isinstance(x, dict[str, int])` raises.
    _BARE_RUNTIME_GENERICS = frozenset({"dict", "list", "set"})

    def _isinstance_member_match(self, member: TpyType, check: TpyType) -> bool:
        """True if union `member` is the alternative a `isinstance(x, check)`
        selects. Exact structural match, or a bare container generic (`dict`)
        matching that member's container kind regardless of element types."""
        if member == check:
            return True
        return (isinstance(check, NominalType) and not check.type_args
                and check.name in self._BARE_RUNTIME_GENERICS
                and isinstance(member, NominalType)
                and member.name == check.name)

    def _evaluate_static_isinstance(
        self, var_type: TpyType, check_types: list[TpyType], expr: TpyCall,
    ) -> bool:
        """Fold isinstance(var, check_types) against a concrete var_type.

        Walks the user-record inheritance chain via TypeRegistry.is_subclass_of,
        so `isinstance(child, Parent)` is True when Parent is any ancestor.
        Downcast checks (`isinstance(parent, Child)`) fold to False with a
        warning -- tpyc uses static dispatch, so any slicing at the boundary
        has already removed the Child fields. Every offending check type
        warns, so tuple forms like `isinstance(a, (Dog, Cat))` surface all
        mistakes at once.

        Type-parameter subjects do not reach here -- they are intercepted in
        `_analyze_isinstance` and lowered to a per-instantiation compile-time
        trait (`_analyze_isinstance_type_param`), since a generic body is
        analyzed once but instantiated per concrete type.
        """
        reg = self.ctx.registry
        result = False
        for ct in check_types:
            if reg.is_subclass_of_or_equal(var_type, ct):
                result = True
                continue
            if reg.is_subclass_of(ct, var_type):
                self.ctx.warning(
                    f"isinstance() check against descendant type '{ct}' on "
                    f"'{var_type}' folds to False (static dispatch; use "
                    f"@dynamic for runtime type checks)",
                    expr,
                )
        return result

    def _analyze_isinstance_type_param(
        self, tp: TypeParamRef, check_types: list[TpyType],
        var_name: str, expr: TpyCall,
    ) -> TpyType:
        """Lower isinstance(x, C) where x: T is a generic type parameter to a
        per-instantiation compile-time trait (`tpy::isinstance_static<C,
        decltype(x)>`), folded at C++ instantiation.

        Sound only for a NON-polymorphic class bound, where the instantiated
        static type IS the dynamic type. A polymorphic / @dynamic bound (needs
        dynamic_cast), a protocol bound, and an unbounded param (which could
        instantiate to a union / Any / polymorphic shape the static trait
        cannot see) are rejected rather than silently mis-folded; the full
        per-instantiation dispatcher that would lift these is tracked in
        BUGS.md.
        """
        bound = self.type_ops.get_type_param_bound(tp.name)
        if bound is None:
            raise self.ctx.error(
                f"isinstance() on the unbounded type parameter '{tp.name}' is "
                f"not supported: a generic body is compiled once for all "
                f"instantiations, so the check cannot be resolved. Add a "
                f"non-polymorphic class bound (e.g. '{tp.name}: Base').",
                expr,
            )
        if is_dynamic_dispatch_inner(bound, self.ctx.registry):
            raise self.ctx.error(
                f"isinstance() on type parameter '{tp.name}' bounded by the "
                f"polymorphic / @dynamic type '{bound}' is not yet supported; "
                f"use the @dynamic protocol type directly for runtime dispatch.",
                expr,
            )
        if not (isinstance(bound, NominalType) and bound.is_user_record):
            raise self.ctx.error(
                f"isinstance() on type parameter '{tp.name}' bounded by "
                f"'{bound}' is not supported: only non-polymorphic class "
                f"bounds are supported.",
                expr,
            )
        expr.isinstance_var = var_name
        expr.isinstance_type = (check_types[0] if len(check_types) == 1
                                else make_union(*check_types))
        expr.isinstance_type_param = True
        return BOOL

    def _is_non_deref_handle(self, typ: TpyType) -> bool:
        """True if `typ` is a non-owning handle to a payload reachable only via
        `upgrade()` (e.g. Weak[Pet]): it exposes `upgrade()` but no
        reference-returning `__deref__`, so it has no deref view to narrow."""
        record = self.ctx.registry.get_record_for_type(typ)
        if record is None:
            return False
        return (bool(record.get_method_overloads("upgrade"))
                and not record.get_method_overloads("__deref__"))

    def _validate_polymorphic_subclass_dispatch(
        self,
        inner: 'TpyType',
        check_types: list['TpyType'],
        var_name: str,
        expr: TpyCall,
    ) -> TpyType:
        """Validate isinstance() check types against a dynamic-dispatch
        source's inner root and set up runtime dispatch. Shared between the
        `Optional[Polymorphic]`, bare polymorphic, `Ptr[inner]`, direct
        @dynamic protocol, and post-`is None` narrowing branches of
        ``_analyze_isinstance`` so they stay in lockstep.

        When `inner` is a @dynamic protocol, a valid check type either C++-
        inherits the protocol base (`dynamic_cast<Sub*>`) or structurally
        conforms to it -- a structural conformer sits behind the `P*` as an
        `Adapter`/`RefAdapter` wrapper, so codegen narrows via
        `tpy::dyn_adapter_cast` instead. A type that neither inherits nor
        structurally conforms can never match and is rejected with a pointed
        diagnostic rather than silently folding to False.
        """
        non_conformers = [
            ct for ct in check_types
            if not dynamic_dispatch_type_conforms(
                ct, inner, self.protocols, self.ctx.registry)
        ]
        if non_conformers:
            names = ", ".join(f"'{t}'" for t in non_conformers)
            if is_protocol_type(inner):
                raise self.ctx.error(
                    f"isinstance() check type(s) {names} do not conform to the "
                    f"@dynamic protocol '{inner}', so the check can never "
                    f"match; the check type must inherit '{inner}' or "
                    f"structurally implement its methods.",
                    expr,
                )
            raise self.ctx.error(
                f"isinstance() check type(s) {names} are not subclasses of "
                f"'{inner}'",
                expr,
            )
        expr.isinstance_var = var_name
        expr.isinstance_type = (check_types[0] if len(check_types) == 1
                                else make_union(*check_types))
        return BOOL

    def _analyze_isinstance(self, expr: TpyCall) -> TpyType:
        """Analyze isinstance(x, T) for union type narrowing or protocol checks.

        Supports:
        1. Union narrowing: isinstance(x, MemberType) where x has a union type.
           Tuple form isinstance(x, (A, B)) narrows to A | B.
        2. Protocol check: isinstance(x, Protocol) where x is a protocol-typed
           template parameter -- compiles to if constexpr (Concept<T_x>).
        3. Static evaluation: isinstance(x, T) where x has a non-union type
           evaluates at compile time based on the static type of x.

        Sets isinstance_var and isinstance_type on the TpyCall node for the
        union/protocol cases. For the static case, sets macro_expansion to a
        TpyBoolLiteral so codegen emits a constant.
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
            protocol_info = self.ctx.registry.scan_by_short_name(second_arg.name)
            if protocol_info is not None and not protocol_info.is_dynamic:
                return self._analyze_isinstance_protocol(
                    expr, first_arg, second_arg.name, protocol_info
                )

        check_types = self._resolve_isinstance_check_types(second_arg, expr)

        expr.resolved_function_info = self._isinstance_function_info()

        # If the variable is narrowed to a concrete non-union type (inside an
        # `if isinstance(v, A)` branch, a match arm, or via assignment
        # narrowing), the check is statically answerable. Fold the call site
        # to a constant but keep the full union branch-facts path below so
        # downstream narrowing / codegen extractions still run.
        narrowed = self.ctx.func.narrowed_types.get(first_arg.name)
        static_fold: bool | None = None
        # When the variable was declared as a polymorphic-class source --
        # `Optional[Polymorphic]` (lowered to `T*`) or a bare polymorphic
        # class (lowered to `T&`) -- the runtime object retains its dynamic
        # type and dynamic_cast against a descendant is sound. Skip the
        # static fold so the runtime path lowers isinstance to a dynamic_cast.
        # Even after `is not None` narrowing strips Optional to its inner
        # class type, the C++ representation remains polymorphism-intact.
        declared_param_type = self.expr.narrowing.declared_type_for_name(first_arg.name)
        polymorphic_source = polymorphic_source_inner(
            declared_param_type, self.ctx.registry)
        # Reject `isinstance(self, Sub)` in contexts where codegen can't
        # support it correctly yet:
        #   - `__init__` / `__del__`: during construction/destruction the
        #     dynamic type of 'self' is the enclosing class (not the derived
        #     class), so the check would always be False at runtime --
        #     silently diverging from CPython. The transitive case
        #     (isinstance(self) inside a method called from __init__/__del__)
        #     is not detected here; see TODO.md.
        # Generator methods are NOT rejected: they share the resumable frame
        # with `async def` (which already supports isinstance(self, Sub)), and
        # `polymorphic_cast_arg` emits the correct `&__self` cast input there.
        if (first_arg.name == "self"
                and polymorphic_source is not None
                and isinstance(self.ctx.func.current_function, TpyFunction)):
            cur = self.ctx.func.current_function
            if cur.name in ("__init__", "__del__"):
                ctor_or_dtor = cur.name
                raise self.ctx.error(
                    f"isinstance(self, ...) is not supported inside "
                    f"'{ctor_or_dtor}': during construction or destruction "
                    f"the dynamic type of 'self' is the enclosing class, "
                    f"not the derived class being constructed or destroyed, "
                    f"so the check would always be False at runtime. This "
                    f"rule applies to the entire call graph reachable from "
                    f"'{ctor_or_dtor}', not just the body itself: a regular "
                    f"method that does isinstance(self, ...) and is called "
                    f"from here has the same problem (TPy does not "
                    f"currently detect this transitive case). Move the "
                    f"check into a method called after construction "
                    f"completes.",
                    expr,
                )
        if narrowed is not None and polymorphic_source is None:
            narrowed_inner = self._isinstance_unwrap(narrowed)
            if not self._is_union_alias(narrowed_inner) and not isinstance(
                    narrowed_inner, (UnionType, OptionalType)):
                static_fold = self._evaluate_static_isinstance(
                    narrowed_inner, check_types, expr)

        effective_type = self.expr.narrowing.effective_union_type(first_arg.name)
        if effective_type is not None:
            effective_type = self._isinstance_unwrap(effective_type)

        if effective_type is None:
            raise self.ctx.error(
                f"isinstance() cannot resolve the type of '{first_arg.name}'",
                expr,
            )

        if isinstance(effective_type, OptionalType):
            inner = effective_type.inner
            # Optional[dynamic-dispatch inner]: class-based dispatch via
            # dynamic_cast on the `const Inner*` representation, without
            # requiring a prior `is None` narrow. `inner` is a polymorphic
            # class or a direct @dynamic protocol (both carry a vtable); the
            # cast against nullptr (the None case) yields nullptr -> False.
            # _validate_polymorphic_subclass_dispatch picks subclass- vs
            # inheritance-conformer validity per inner kind.
            if (isinstance(inner, NominalType)
                    and is_dynamic_dispatch_inner(inner, self.ctx.registry)
                    and check_types):
                return self._validate_polymorphic_subclass_dispatch(
                    inner, check_types, first_arg.name, expr)

            raise self.ctx.error(
                f"isinstance() is only supported on union types, "
                f"got '{effective_type}'",
                expr,
            )

        # Any narrowing (D15): non-consuming borrow extraction. Inside the
        # true branch, the variable is bound to a `const T&` that aliases
        # the contents of the cell. The outer Any survives. Reject Union /
        # Optional / Any / protocol check types -- v1 supports only
        # concrete-type narrowing.
        if isinstance(effective_type, AnyType):
            for ct in check_types:
                if isinstance(ct, AnyType):
                    raise self.ctx.error(
                        "isinstance() second argument cannot be Any -- "
                        "Any is not a runtime class",
                        expr,
                    )
                if isinstance(ct, (UnionType, OptionalType)):
                    raise self.ctx.error(
                        "isinstance() check type on Any must be a concrete "
                        f"type, got '{ct}'",
                        expr,
                    )
                if is_protocol_type(ct):
                    raise self.ctx.error(
                        "isinstance() against a protocol on Any is not "
                        "supported in v1 -- protocols are structural; "
                        "narrow to a concrete type first",
                        expr,
                    )
            expr.isinstance_var = first_arg.name
            expr.isinstance_type = (check_types[0] if len(check_types) == 1
                                    else make_union(*check_types))
            return BOOL

        if not isinstance(effective_type, UnionType):
            # Generic type-parameter subject: a generic body is analyzed once
            # but instantiated per concrete type, so the answer is decided at
            # C++ instantiation, not here. Lower to a compile-time trait.
            if isinstance(effective_type, TypeParamRef) and check_types:
                return self._analyze_isinstance_type_param(
                    effective_type, check_types, first_arg.name, expr)
            # Polymorphic source post-narrowing: source was declared as a
            # polymorphic class (`Optional[Inner]` or bare `Inner`); the
            # C++ representation is `const Inner*` or `const Inner&` and
            # dynamic_cast to a subclass is sound. Route to runtime dispatch.
            if polymorphic_source is not None and check_types:
                return self._validate_polymorphic_subclass_dispatch(
                    polymorphic_source, check_types, first_arg.name, expr)
            # Owning-wrapper source (Box[Pet]/Rc[Pet]): the dispatch object is
            # the polymorphic payload reached through the wrapper's
            # reference-returning __deref__, not the wrapper itself. Narrow the
            # deref view (so `rc.bark()` resolves against the subclass while
            # `rc.clone()` stays an Rc method) and tag the depth for codegen.
            deref = deref_dispatch_inner(
                effective_type, self.type_ops, self.ctx.registry)
            if deref is not None and check_types:
                inner, depth = deref
                result = self._validate_polymorphic_subclass_dispatch(
                    inner, check_types, first_arg.name, expr)
                expr.isinstance_deref_depth = depth
                return result
            # Non-owning handle (e.g. Weak[Pet]): no deref view -- the payload
            # is reachable only after `upgrade()`. Reject rather than silently
            # folding to False, since the user clearly intends a payload check.
            if check_types and self._is_non_deref_handle(effective_type):
                raise self.ctx.error(
                    f"isinstance() on '{effective_type}' is not supported: it "
                    f"is a non-owning handle with no deref view. Call "
                    f"'.upgrade()' first to obtain an owning handle (or None) "
                    f"and isinstance-check that instead.",
                    expr,
                )
            # Non-union: compile-time evaluate against the static type,
            # walking the inheritance hierarchy. Reuse the narrowed-path
            # fold if it already ran so we don't warn twice.
            if static_fold is None:
                static_fold = self._evaluate_static_isinstance(
                    effective_type, check_types, expr)
            expr.macro_expansion = TpyBoolLiteral(value=static_fold, loc=expr.loc)
            return BOOL

        # Union case: validate that check types select a member. A bare
        # container generic (`dict`) matches the member of that kind; record
        # the matched member so narrowing/codegen use the union's own
        # representation. A bare generic matching two members of the same kind
        # (`list[int] | list[str]`) is unresolvable -- CPython can't tell them
        # apart at runtime either (type erasure) -- so reject rather than guess.
        matched: list[TpyType] = []
        non_members: list[TpyType] = []
        for t in check_types:
            hits = [m for m in effective_type.members
                    if self._isinstance_member_match(m, t)]
            if not hits:
                non_members.append(t)
            elif len(hits) > 1:
                names = ", ".join(f"'{h}'" for h in hits)
                raise self.ctx.error(
                    f"isinstance() against '{t}' is ambiguous: union "
                    f"'{effective_type}' has multiple '{t}' members ({names}) "
                    f"that a bare generic cannot distinguish at runtime.",
                    expr,
                )
            else:
                matched.append(hits[0])
        if non_members:
            # If the narrowed-path fold already answered via hierarchy walk
            # (e.g. `isinstance(x, Puppy)` inside an `if isinstance(x, Dog):`
            # branch on a `Dog | Cat` union), the check types don't have to
            # be declared-union members -- emit the constant and bypass the
            # narrowing setup (we can't narrow a union to a non-member).
            if static_fold is not None:
                expr.macro_expansion = TpyBoolLiteral(value=static_fold, loc=expr.loc)
                return BOOL
            if len(non_members) == 1:
                raise self.ctx.error(
                    f"Type '{non_members[0]}' is not a member of union "
                    f"'{effective_type}'",
                    expr,
                )
            names = ", ".join(f"'{t}'" for t in non_members)
            raise self.ctx.error(
                f"Types {names} are not members of union '{effective_type}'",
                expr,
            )

        expr.isinstance_var = first_arg.name
        expr.isinstance_type = (matched[0] if len(matched) == 1
                                else make_union(*matched))
        if static_fold is not None:
            # Folded at the call site, but keep isinstance_var/isinstance_type
            # so the surrounding if/elif still narrows and codegen emits the
            # std::get extraction for the then-block.
            expr.macro_expansion = TpyBoolLiteral(value=static_fold, loc=expr.loc)
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
            if var_type.name == protocol_name:
                type_args = var_type.type_args
                qname = var_type._module_qname
            else:
                # Cross-protocol narrowing (e.g. Iterable[T] -> NativeIterable):
                # derive the target's type_args from the source via the
                # target's parent_protocols inheritance chain. Without this,
                # codegen emits the C++ concept with the wrong template-arg
                # count (one arg vs. expected `<T_x, ElemT>`).
                type_args = self._derive_narrowed_type_args(var_type, protocol_info)
                if type_args is None and protocol_info.type_params:
                    raise self.ctx.error(
                        f"isinstance() narrowing from '{var_type}' to protocol "
                        f"'{protocol_name}' requires '{protocol_name}' to inherit "
                        f"from '{var_type.name}' with a directly-derivable type "
                        f"argument mapping; otherwise the C++ concept would be "
                        f"emitted with the wrong template-arg count",
                        expr,
                    )
                qname = (f"{protocol_info.module}.{protocol_name}"
                        if protocol_info and protocol_info.module else None)
            protocol_type = NominalType(
                protocol_name, is_protocol=True, type_args=type_args,
                _module_qname=qname,
            )
        elif is_protocol_union(var_type):
            # Find the matching member in the union, preserving type_args
            members = protocol_union_protocols(var_type)
            matched = None
            for m in members:
                if isinstance(m, NominalType) and m.name == protocol_name:
                    matched = m
                    break
            if matched is None:
                member_names = ", ".join(m.name for m in members if isinstance(m, NominalType))
                raise self.ctx.error(
                    f"Protocol '{protocol_name}' is not a member of the protocol union "
                    f"({member_names})",
                    expr
                )
            # Preserve the matched member's qname so isinstance-based
            # union-member narrowing equality-compares correctly.
            protocol_type = NominalType(
                protocol_name, is_protocol=True, type_args=matched.type_args,
                _module_qname=matched._module_qname,
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

    def _derive_narrowed_type_args(
        self, source_type: NominalType, target_info: 'ProtocolInfo',
    ) -> tuple[TpyType, ...] | None:
        """Map a source parent protocol's type_args onto the target's type_params.

        For `class NativeIterable[T](Iterable[T])` and source `Iterable[Int32]`,
        finds the matching parent ref in target_info.parent_protocols, builds a
        target-param -> source-arg substitution from the parent ref's TypeParamRef
        positions, then returns the substituted target type_params.

        Returns None when source has no type args, target has no type params,
        no matching direct parent exists, or the parent ref's args aren't simple
        TypeParamRefs (transitive / re-parameterizing inheritance not handled).
        """
        if not source_type.type_args or not target_info.type_params:
            return None
        matching_parent = next(
            (p for p in target_info.parent_protocols if p.name == source_type.name),
            None,
        )
        if matching_parent is None or len(matching_parent.type_args) != len(source_type.type_args):
            return None
        subst: dict[str, TpyType] = {}
        for parent_arg, source_arg in zip(matching_parent.type_args, source_type.type_args):
            if not isinstance(parent_arg, TypeParamRef) or not isinstance(source_arg, TpyType):
                return None
            subst[parent_arg.name] = source_arg
        try:
            return tuple(subst[tp] for tp in target_info.type_params)
        except KeyError:
            return None

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

    def _analyze_any_construct(self, expr: TpyCall) -> TpyType:
        """Analyze `Any(value)` -- constructor sugar for INTO_ANY coercion.

        Wraps the single arg in the same INTO_ANY coercion that fires for
        annotated targets, then replaces the call with that coerced arg
        via `macro_expansion`. Codegen then emits `make_any(...)`
        directly without going through the constructor path.
        """
        self._reject_kwargs_for_builtin(expr, "Any")
        if len(expr.args) != 1:
            raise self.ctx.error(
                f"Any(...) takes exactly 1 argument, got {len(expr.args)}",
                expr,
            )
        arg = expr.args[0]
        arg_type = self.expr.analyze_expr(arg)
        if isinstance(arg_type, AnyType):
            raise self.ctx.error(
                "Any(x) where x is already Any is redundant -- "
                "use the value directly",
                expr,
            )
        coerced = self.compat.coerce_expr(
            arg, arg_type, ANY,
            "argument to Any(...)",
            coercion_ctx=CoercionContext.INIT,
        )
        expr.macro_expansion = coerced
        self.ctx.set_expr_type(expr, ANY)
        return ANY

    def _analyze_typing_cast(self, expr: TpyCall) -> TpyType:
        """Analyze typing.cast(T, x) -- runtime checked extraction from Any.

        For non-Any sources this is a static-only no-op (matches CPython
        semantics). For Any sources, codegen emits any_cast_or_panic<T>.
        Either way the static result type is T.
        """
        self._reject_kwargs_for_builtin(expr, "cast")
        if len(expr.args) != 2:
            raise self.ctx.error(
                f"typing.cast() takes exactly 2 arguments, got {len(expr.args)}",
                expr,
            )

        type_arg = expr.args[0]
        if not isinstance(type_arg, TpyName):
            raise self.ctx.error(
                "typing.cast() target must be a concrete type, not a union "
                "or other expression",
                expr,
            )
        # _resolve_isinstance_type with allow_any=True resolves bare `Any`
        # and aliased imports (`from typing import Any as A`) to AnyType
        # rather than raising; we reject Any with a cast-flavoured message
        # in one place below.
        target_type = self._resolve_isinstance_type(
            type_arg.name, expr, allow_any=True)
        if isinstance(target_type, AnyType):
            raise self.ctx.error(
                "typing.cast(Any, ...) is meaningless -- pick a concrete type",
                expr,
            )
        if isinstance(target_type, (UnionType, OptionalType)):
            raise self.ctx.error(
                "typing.cast() target must be a concrete type, not a union",
                expr,
            )

        source_type = self.expr.analyze_expr(expr.args[1])

        expr.cast_target_type = target_type
        expr.cast_source_is_any = isinstance(source_type, AnyType)
        expr.resolved_function_info = FunctionInfo(
            name="cast",
            params=[],
            return_type=target_type,
            is_readonly=True,
            is_builtin_function=True,
            special_handling=True,
            qualified_name="typing.cast",
        )
        return target_type

    def _analyze_enum_from_value(self, expr: TpyCall, enum_type: NominalType) -> TpyType:
        """Analyze enum value lookup: Color(0) -> Color."""
        self._reject_kwargs_for_builtin(expr, enum_type.name)
        if len(expr.args) != 1:
            raise self.ctx.error(
                f"Enum '{enum_type.name}' constructor takes exactly 1 argument, "
                f"got {len(expr.args)}",
                expr
            )
        arg_type = self.expr.analyze_expr(expr.args[0])
        if not is_any_int_type(arg_type):
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
        if self.compat.is_auto_move_use(arg):
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
        if self.compat.is_auto_move_use(inner):
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
        # tuple[Own[T_ref], ...] with a literal source: per-element ownership
        # check. Two shapes reach this -- `Own[tuple[T,...]]` (unwrapped to
        # the inner tuple below) and the canonical per-element form
        # `tuple[Own[T], ...]` which users may write directly. Dispatch
        # before unwrap_optional_own because the second shape has no outer
        # Own to unwrap.
        peeled_ptype = unwrap_readonly(ptype)
        if (isinstance(peeled_ptype, TupleType)
                and isinstance(arg, TpyTupleLiteral)
                and any(isinstance(et, OwnType) for et in peeled_ptype.element_types)):
            self._check_own_tuple_literal_arg(arg, peeled_ptype, pname)
            self._warn_unnecessary_copy(arg)
            return
        # A tuple LOCAL passed by NAME into a tuple param with Own slots: the
        # literal element check above never ran, so consult the
        # construction-time plain-borrow-into-Own hazard per Own slot.
        if isinstance(arg, TpyName):
            name_target = own_tuple_target(ptype)
            if (name_target is not None
                    and any(isinstance(et, OwnType)
                            for et in name_target.element_types)):
                self.compat.check_name_borrow_into_own(
                    arg.name, name_target, arg, "pass")
                # The `std::tuple<...>&&` param binds only an rvalue, so a
                # movable owned-tuple source NOT at its last use can't move in:
                # a @nocopy tuple is a clean use-after-move error, a copyable one
                # warns and is auto-copied (mirrors the scalar Own[T] arg). Only
                # the owned-movable, non-readonly tuple param is rendered `&&`;
                # a mixed/borrow or readonly param is const& and binds an lvalue.
                bare = unwrap_readonly(ptype)
                if (isinstance(bare, TupleType) and bare.is_owned_movable()
                        and not isinstance(ptype, ReadonlyType)
                        and self.compat._is_owned_var(arg.name)
                        and not self.compat.is_auto_move_use(arg)):
                    if self.ctx.is_type_non_copyable(arg_type):
                        reason = (self.ctx.nocopy_reason(arg_type)
                                  or f"owned tuple '{arg.name}'")
                        raise self.ctx.error(
                            f"{reason} is used after this point and cannot be "
                            f"moved into '{pname}'. Remove later uses or use "
                            f"copy().", arg)
                    self.ctx.warning(
                        f"copies {bare} into owned storage; use copy() to make "
                        f"this explicit", arg)
        own_ptype = unwrap_optional_own(ptype)
        if own_ptype is None:
            return
        # Own[tuple[T,...]] with a literal source: same per-element check via
        # own_tuple_target's synthesis (lvalue-tuple sources keep today's
        # silent-copy behaviour; the check applies to literal sources only).
        if (isinstance(own_ptype.wrapped, TupleType)
                and isinstance(arg, TpyTupleLiteral)):
            self._check_own_tuple_literal_arg(arg, own_ptype, pname)
            self._warn_unnecessary_copy(arg)
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

    def _check_own_tuple_literal_arg(
        self, literal: 'TpyTupleLiteral',
        ptype: 'OwnType | TupleType', pname: str,
    ) -> None:
        """Per-element Own check for a tuple literal passed to a param with
        per-element Own slots.

        Accepts either form of the param type:
          * `Own[tuple[T, ...]]` -- own_tuple_target synthesizes the
            per-element Own wrap for non-value elements.
          * `tuple[Own[T], T_value, ...]` -- the canonical per-element form
            (also what sema lowers Own[tuple[...]] to internally). Passed
            through unchanged by own_tuple_target.

        Each Own-wrapped element must be at last use, an explicit copy(),
        a fresh rvalue (literal/constructor), or None. Mirrors the
        per-element check the return path applies via own_tuple_target.
        """
        target = own_tuple_target(ptype)
        if target is None:
            return
        for i, et in enumerate(target.element_types):
            if not isinstance(et, OwnType):
                continue
            if i >= len(literal.elements):
                continue
            elem = literal.elements[i]
            self.compat.check_own_lvalue_into_own(
                et, elem, f"argument '{pname}' tuple element {i}",
                action="pass",
            )

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

        lookup_qname: str | None = None
        td = find_factory_by_simple_name(expr.func_name)
        if td is not None:
            lookup_qname = td.qname
        else:
            # Fallback: type may be imported from a submodule without a
            # factory reachable by simple name (e.g. tpy.mem submodule
            # exports). If the record itself carries a type_factory, use
            # its qname.
            qname = expr.call_type.qualified_name()
            if qname:
                rec = self.ctx.registry.get_builtin_record(qname)
                if rec and rec.type_params and rec.type_factory:
                    lookup_qname = qname
        if lookup_qname is None:
            return fallback
        record_info = self.ctx.registry.get_builtin_record(lookup_qname)
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
            if is_list(hint) and isinstance(arg, (TpyDictLiteral, TpySetLiteral)):
                hints[i] = expr.call_type
        return hints

    @staticmethod
    def _concrete_hint_from_param(param_type: TpyType) -> TpyType | None:
        """Extract a concrete ListType hint from a protocol param type.

        analyze_expr_with_hint dispatches on is_list(hint),
        so protocol params like Iterable[Own[tuple[K,V]]] must be converted
        to make_list(tuple[K,V]) for element-level hints to propagate.
        """
        param_type = unwrap_ref_type(param_type)
        if not is_protocol_type(param_type):
            return None
        # Protocol element type is the first type_arg for iterable protocols.
        # No registry needed -- protocol structure is enough.
        qname = param_type.qualified_name()
        if qname not in builtin_modules.ITERABLE_PROTOCOL_QNAMES:
            return None
        if not param_type.type_args:
            return None
        first = param_type.type_args[0]
        elem = first if isinstance(first, TpyType) else None
        if elem is None:
            return None
        # Unwrap Own -- container elements are owned by value
        if isinstance(elem, OwnType):
            elem = elem.wrapped
        return make_list(elem)

    def _validate_generic_constructor(self, expr: TpyCall, arg_types: list[TpyType]) -> None:
        """Validate and resolve generic type constructor calls.

        When call_type is set (e.g., list[int](iterable)), checks that args
        conform to constructor params and sets resolved_function_info for codegen.
        For params with TypeParamRef (e.g. Span[T]), structural compatibility is
        checked (arg must be a container with matching element type) even though
        T itself is unresolved.
        """
        lookup_qname: str | None = None
        td = find_factory_by_simple_name(expr.func_name)
        if td is not None:
            lookup_qname = td.qname
        else:
            # Try looking up via call_type's qualified name (for types from submodules)
            qname = expr.call_type.qualified_name() if expr.call_type else None
            if qname:
                rec = self.ctx.registry.get_builtin_record(qname)
                if rec and rec.type_params and rec.type_factory:
                    lookup_qname = qname
        record_info = self.ctx.registry.get_builtin_record(lookup_qname) if lookup_qname else None
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
                    if contains_type_param(p_type) and expr.call_type and inferred:
                        resolved_param = self.type_ops.substitute_type_params(p_type, inferred)
                        expected_elem = builtin_modules.get_iterable_element_type(resolved_param, registry=self.ctx.registry)
                        if isinstance(expected_elem, OwnType):
                            expected_elem = expected_elem.wrapped
                        arg_elem = builtin_modules.get_iterable_element_type(at, registry=self.ctx.registry)
                        if expected_elem is not None and arg_elem is not None:
                            if not self.compat.is_type_compatible(arg_elem, expected_elem):
                                rejected = True
                                break
                elif contains_type_param(p_type):
                    # Can't fully resolve T, but reject clearly incompatible
                    # types. For Span[T]: arg must have an element type, and
                    # if T is known from call_type, element types must match.
                    if is_span(p_type):
                        arg_elem = at.get_element_type()
                        if arg_elem is None:
                            rejected = True
                            break
                        if not is_readonly_span(p_type) and is_span(at) and is_readonly_span(at):
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
                        ctor, inferred, result_type=expr.call_type, ctx=self.ctx)
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
                        f"argument '{param.name}' must be a variable, attribute, "
                        f"or container element (not a temporary or expression)", expr)
                self.compat._mark_addr_taken(expr.args[i])

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
            bt = self.ctx.func.borrow_tracker
            storage = bt.effective_storage(arg.name)
            needs_check = bt.has_element_borrow(storage)
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
                # No mutation facts available: either a forward call (facts
                # arrive in Phase 2) or a synthetic callable-value callee that
                # has no body and never gains facts. Defer either way -- Phase
                # 2 keeps the warning when facts stay absent (conservative).
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
        for i, param in enumerate(fi.params):
            if i >= len(expr.args):
                break
            arg = expr.args[i]
            if isinstance(param.type, ReadonlyType):
                continue
            # If callee is known not to mutate this param, skip
            if fi.mutated_params is not None and i not in fi.mutated_params:
                continue
            # Vararg slot: Span[T] is itself a value type, so the
            # unwrap-readonly gate below would short-circuit. Each individual
            # arg in the pack is address-taken into a `T*` slot (indirect-mode
            # varargs), so the source must be a non-const lvalue. Mark each
            # arg's root on both axes: `mark_param_mutated` keeps caller
            # params non-const AND traces loop vars back to their iterable
            # (via loop_var_iterable); `mark_loop_var_mutated` keeps the
            # for-loop binding `auto& b` rather than `const auto& b` so `&b`
            # is `T*` not `const T*`. Phase-2 vararg edges cover transitive
            # propagation; this branch handles the local Phase-1 facts the
            # edge mechanism doesn't (loop-var bindings are per-callsite).
            if param.is_variadic and isinstance(arg, TpyVarargPack):
                bare_va = unwrap_ref_type(param.type)
                if is_varargs(bare_va) and not isinstance(bare_va.type_args[0], ReadonlyType):
                    elem_type = bare_va.type_args[0]
                    bare_elem = unwrap_readonly(elem_type)
                    if (not bare_elem.is_value_type()
                            and not isinstance(bare_elem, TypeParamRef)):
                        for sub in arg.args:
                            sub_expr = sub.expr if isinstance(sub, TpyStarUnpack) else sub
                            sub_root = _root_name_of_expr(sub_expr)
                            if sub_root is not None:
                                self.ctx.mark_param_mutated(sub_root)
                                self.ctx.mark_loop_var_mutated(sub_root)
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
        # The mutable clone of an @auto_readonly accessor (dict.values/items,
        # Box.get) hands out a borrow but does not mutate its receiver; an
        # edge here would conservatively flip the caller's self_mutated in
        # Phase 2 (native clones have no analyzed mutation facts). Mutation
        # THROUGH the borrowed result is rooted at the mutation site instead.
        if fi.borrows_receiver_via_auto_readonly:
            return
        name_to_idx = self.ctx.func.current_param_name_to_idx
        rebound = self.ctx.func.current_rebound_params

        # Receivers effectively rooted at self: self.method(), self.field.method(),
        # loop_var.method() over self.field, and super().method() (the synthetic
        # super() proxy resolves to self). Phase 2 propagates self-mutation through
        # these.
        receiver_is_self = False
        if isinstance(expr, TpyMethodCall):
            if expr.super_parent_type is not None:
                receiver_is_self = True
            else:
                obj_root = _root_name_of_expr(expr.obj)
                if obj_root is not None and _is_self_call_deferred(
                        expr.obj, obj_root, self.ctx.func.loop_var_iterable,
                        self.ctx.func.borrow_tracker):
                    receiver_is_self = True

        # Nothing to record if no params flow through and no self-call
        if not name_to_idx and not receiver_is_self:
            return

        param_map: dict[int, int] = {}
        # A vararg slot can receive multiple caller args; param_map is single-
        # valued per callee idx, so additional vararg-arg roots beyond the first
        # spawn standalone edges (collected here, emitted alongside the main edge).
        extra_vararg_edges: list[tuple[int, int]] = []
        for i, callee_param in enumerate(fi.params):
            if i >= len(expr.args):
                break
            if isinstance(callee_param.type, ReadonlyType):
                continue
            # Vararg slot: walk the TpyVarargPack and record one (va_idx ->
            # caller_idx) entry per distinct arg root. A readonly vararg slot
            # is skipped (slot can't mutate elements through *items).
            if callee_param.is_variadic:
                bare_va = unwrap_ref_type(callee_param.type)
                if not is_varargs(bare_va):
                    continue
                if isinstance(bare_va.type_args[0], ReadonlyType):
                    continue
                pack = expr.args[i]
                if not isinstance(pack, TpyVarargPack):
                    continue
                seen_callers: set[int] = set()
                for sub in pack.args:
                    sub_expr = sub.expr if isinstance(sub, TpyStarUnpack) else sub
                    sub_root = _root_name_of_expr(sub_expr)
                    if sub_root is None:
                        continue
                    resolved = self.ctx.func.borrow_tracker.effective_storage_through_borrows(sub_root)
                    if resolved not in name_to_idx or resolved in rebound:
                        continue
                    caller_idx = name_to_idx[resolved]
                    if caller_idx in seen_callers:
                        continue
                    seen_callers.add(caller_idx)
                    if i not in param_map:
                        param_map[i] = caller_idx
                    else:
                        extra_vararg_edges.append((i, caller_idx))
                continue
            # Skip params with no mutable borrow surface. This catches plain
            # value types and also opts out of Own[T], TypeParamRef, and
            # generic-slot tuples. Tuples and pointer-repr Optionals with
            # non-readonly elements DO need edges so transitive mutation
            # through them propagates.
            if not param_has_mutable_borrow_surface(callee_param.type):
                continue
            arg = expr.args[i]
            arg_root = _root_name_of_expr(arg)
            if arg_root is None:
                continue
            # Resolve alias and element borrow chains to find the original param.
            # 8a.5: effective_storage_through_borrows also follows element/field/ptr
            # borrows so that mutating a call arg that element-borrows from a param
            # correctly traces back to the source param.
            resolved = self.ctx.func.borrow_tracker.effective_storage_through_borrows(arg_root)
            if resolved in name_to_idx and resolved not in rebound:
                param_map[i] = name_to_idx[resolved]
        if param_map or receiver_is_self:
            # Edge stores the canonical so Phase 2 reads facts as they evolve.
            callee = fi.root
            assert callee.canonical_fi is None, (
                f"non-collapsed canonical_fi chain on {callee.name}")
            self.ctx.func.current_call_edges.append(
                MutationCallEdge(callee_fi=callee, param_map=param_map,
                                 receiver_is_self=receiver_is_self)
            )
        for callee_idx, caller_idx in extra_vararg_edges:
            callee = fi.root
            self.ctx.func.current_call_edges.append(
                MutationCallEdge(callee_fi=callee,
                                 param_map={callee_idx: caller_idx},
                                 receiver_is_self=False)
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

        # Ptr[None] -- both VoidType (function-return form) and NoneType
        # (type-arg form) pointees lower to void* and share this rule.
        if is_void_like_type(pointee):
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

        # Resolve the concrete type (e.g. FLOAT32 singleton). __init__ returns None
        # in Python, so we look up the actual type via the type factory.
        record_type = builtin_modules.get_builtin_type_obj(record.builtin_type_key)

        # First-match-wins, except when multiple fixed-int overloads accept an
        # IntLiteralType arg -- there, prefer the default_int_type one over the
        # smallest-fitting one (str(IntLiteralType) -> fixed_to_str<int32_t>
        # rather than <int8_t>). Other-shape ctors (BigInt, float, etc.) and
        # single-fixed-int ctors (Int64(20)) keep declaration-order behavior.
        default_int_type = self.ctx.default_int_type
        best_ctor: FunctionInfo | None = None
        best_cost = 0
        for ctor in init_overloads:
            if len(ctor.params) != len(arg_types):
                continue
            if not all(type_matches_numeric(arg_type, ptype)
                       for (pname, ptype), arg_type in zip(ctor.params, arg_types)):
                continue
            # Single pass: detect IntLiteralType -> fixed-int matches and
            # accumulate their widening cost from default_int_type.
            is_fixed_int = False
            cost = 0
            for (_pname, ptype), arg_type in zip(ctor.params, arg_types):
                if isinstance(arg_type, IntLiteralType) and is_fixed_int_type(ptype):
                    is_fixed_int = True
                    cost += _scalar_widening_cost(arg_type, ptype, default_int_type)
            if best_ctor is None:
                best_ctor = ctor
                best_cost = cost
                # Tie-break only fires when the first match was fixed-int;
                # otherwise no later ctor can improve on this one.
                if not is_fixed_int:
                    break
            elif is_fixed_int and cost < best_cost:
                best_ctor = ctor
                best_cost = cost
            if best_cost == 0:
                break
        if best_ctor is not None:
            ctor = best_ctor
            ret = record_type or ctor.return_type
            # Reject int literals that are out of range for the target fixed-int type
            if (is_fixed_int_type(ret) and len(arg_types) == 1
                    and isinstance(arg_types[0], IntLiteralType)):
                lit = arg_types[0]
                ret_tr = int_traits_of(ret)
                if lit.value is not None and not (ret_tr.min_value <= lit.value <= ret_tr.max_value):
                    raise self.ctx.error(
                        f"{ret} overflow: {lit.value} is outside range "
                        f"[{ret_tr.min_value}, {ret_tr.max_value}]",
                        expr,
                    )
            expr.resolved_function_info = _resolve_cpp_template_type_params(ctor, result_type=ret, ctx=self.ctx)
            self._check_cast_safe(expr, ctor, arg_types, ret)
            # Borrowing views (StrView/BytesView/Span/SpanIter) need call_type
            # populated so downstream dangling/provenance checks can see
            # through the constructor. Other builtin constructors leave
            # call_type unset to preserve their existing codegen paths.
            if is_borrowing_view_type(ret):
                expr.call_type = ret
            return ret

        # Fallback: try protocol-aware overload resolution (e.g. bool(obj) via Truthy)
        try:
            matched = resolve_overload(
                init_overloads, arg_types,
                protocol_checker=self.protocols.type_conforms_to_protocol,
                protocol_classifier=self.protocols.classify_protocol_conformance,
                default_int_type=self.ctx.default_int_type,
                subclass_checker=self.ctx.registry.is_subclass_of,
                type_ops=self.type_ops,
            )
        except OverloadAmbiguityError as e:
            raise self._ambiguous_overload_error(expr, init_overloads[0].name, e)
        if matched:
            ret = record_type or matched.return_type
            expr.resolved_function_info = _resolve_cpp_template_type_params(matched, result_type=ret, ctx=self.ctx)
            self._check_cast_safe(expr, matched, arg_types, ret)
            if is_borrowing_view_type(ret):
                expr.call_type = ret
            return ret

        # bool(obj) __len__ fallback: types with __len__ but no __bool__
        if record.builtin_type_key == qnames.BOOL and len(arg_types) == 1:
            arg_type = arg_types[0]
            if isinstance(arg_type, NominalType):
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
        # Build expected arity from overloads
        arities = sorted({len(o.params) for o in init_overloads})
        got = len(arg_types)
        if got == 0:
            raise self.ctx.error(f"{type_name}() requires arguments", expr)
        elif len(arities) == 1 and got != arities[0]:
            expected = arities[0]
            raise self.ctx.error(
                f"{type_name}() takes {expected} argument{'s' if expected != 1 else ''}, got {got}", expr)
        elif got == 1:
            raise self.ctx.error(f"{type_name}() cannot convert {arg_types[0]}", expr)
        elif got in arities:
            arg_str = ", ".join(str(t) for t in arg_types)
            raise self.ctx.error(f"{type_name}() cannot convert ({arg_str})", expr)
        else:
            arity_str = ", ".join(str(a) for a in arities)
            raise self.ctx.error(
                f"{type_name}() takes ({arity_str}) arguments, got {got}", expr)

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
        target_tr = int_traits_of(target)
        source_tr = int_traits_of(source)
        if target_tr is None or source_tr is None:
            return
        arg = expr.args[0]
        if not isinstance(arg, TpyName):
            return
        # Safe when: signed -> unsigned, target at least as wide, source proven non-negative
        is_safe = (
            source_tr.signed and not target_tr.signed
            and target_tr.bits >= source_tr.bits
            and (rng := self.ctx.func.value_ranges.get(arg.name)) is not None
            and rng.is_non_negative()
        )
        if is_safe:
            safe_template = f"static_cast<{target.to_cpp()}>({{0}})"
            expr.resolved_function_info = dc_replace(
                ctor, cpp_template=safe_template, canonical_fi=ctor.root)
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
            if isinstance(type_arg, NominalType) and type_arg.is_record and not type_arg.type_args:
                if self.ctx.registry.get_record_for_type(type_arg) is None:
                    raise self.ctx.error(f"Unknown type: {type_arg.name}", expr)
            in_generic = bool(
                (isinstance(self.ctx.func.current_function, TpyFunction) and self.ctx.func.current_function.type_params)
                or self.ctx.record_ctx.type_params
            )
            self.type_ops.validate_type(type_arg, allow_type_param_ref=in_generic, loc=expr.loc, allow_forward_ref=False)

    def _infer_arg_types(
        self, expr: TpyCall, func: FunctionInfo,
        seed_subst: dict[str, TpyType] | None = None,
    ) -> list[TpyType]:
        """Analyze args for type param inference, with two-phase for Fn/Callable params.

        When a generic function has Fn/Callable params (e.g. map[T,U](fn: Fn[[T],U], ...)),
        function refs and lambdas can't be analyzed without concrete type hints. We:
        1. Analyze non-Fn args first to get types for partial type param inference.
        2. Substitute inferred params into the Fn type to build concrete hints.
        3. Analyze the Fn args with those hints.

        ``seed_subst`` is an LHS-hint-derived pre-binding of type params (from
        matching the LHS hint against ``func.return_type``). When present, each
        arg is analyzed with the seeded ptype as ``expr_type_hint`` on its
        first pass -- this lets nested generic constructor calls see the outer
        LHS hint before arg-driven inference has any evidence.
        """
        def _analyze_arg(idx: int, arg: TpyExpr) -> TpyType:
            hint = seeded_arg_hint(func.params, idx, seed_subst or {})
            return self.expr.analyze_call_arg(arg, hint)

        # Quick check: if no Fn/Callable params, analyze all args directly
        fn_positions: set[int] = set()
        for i, (_, ptype) in enumerate(func.params):
            if is_callable_type(unwrap_ref_type(ptype)):
                fn_positions.add(i)
        if not fn_positions:
            return [_analyze_arg(i, arg) for i, arg in enumerate(expr.args)]

        # Phase 1: analyze non-Fn args (seeded per-arg hint when available)
        arg_types: list[TpyType | None] = [None] * len(expr.args)
        for i, arg in enumerate(expr.args):
            if i not in fn_positions:
                arg_types[i] = _analyze_arg(i, arg)

        # Phase 2: partial inference from known args, then resolve Fn args.
        # Start from the seed so Fn args see seeded type params even before
        # any non-Fn evidence binds them.
        partial_inferred: dict[str, TpyType] = dict(seed_subst) if seed_subst else {}
        for param, arg_type in zip(func.params, arg_types):
            if arg_type is None:
                continue
            match_ptype = param.type
            # A variadic param's declared type is the packed `Span[readonly[E]]`;
            # the corresponding arg_type is a single element (an individual
            # positional or a `*xs` unpack's element). Match against E so the
            # element evidence can bind the type param -- otherwise a Span-vs-
            # element shape mismatch leaves T unbound and a sibling Fn arg
            # can't concretize its hint.
            if param.is_variadic and is_varargs(unwrap_ref_type(match_ptype)):
                match_ptype = unwrap_readonly(unwrap_ref_type(match_ptype).type_args[0])
            self.type_ops.match_type_with_inference(match_ptype, arg_type, partial_inferred)
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
                concrete_hint = partial_substitute(ptype, partial_inferred)
                if is_callable_type(concrete_hint):
                    # Only the callee's OWN un-inferred type params block using
                    # this hint to type the lambda; an enclosing-scope type
                    # param (the caller's `T`, e.g. in `list[tuple[T, Int32]]`)
                    # is a real in-scope type the lambda params can bind to.
                    callee_unresolved = set(func.type_params) - set(partial_inferred.keys())
                    has_unresolved = any(
                        contains_type_param(p, callee_unresolved)
                        for p in concrete_hint.param_types
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

    def _supplied_fn_slots(
        self, expr: TpyCall, func: FunctionInfo,
    ) -> list[SuppliedFnSlot] | None:
        """Walk expr's positional + keyword args against func.params,
        returning the list of Fn-typed slots filled by supplied
        (non-default) arguments.

        Returns ``None`` when the call shape rejects this candidate:
        unknown kwarg, positional/keyword collision, missing required
        param, or too many positional args. An empty list means the
        candidate is viable but has no supplied Fn args.

        Mirrors the validity rules in `_expand_arg_types_with_kwargs`
        but tracks supplied-vs-default per slot, which the type-only
        expansion loses.

        Variadic stubs (`*args` on `@overload`) are parser-blocked, so
        this helper returns ``None`` for any candidate with
        ``has_variadic`` -- callers fall back to the existing
        non-regime-aware flow if needed.
        """
        if func.has_variadic:
            return None

        args = expr.args
        kwargs = expr.kwargs or {}

        if len(args) > len(func.params):
            return None  # too many positional

        # Reject any positional fill of a keyword-only param.
        for i in range(min(len(args), len(func.params))):
            if func.params[i].keyword_only:
                return None

        name_to_index = {p.name: i for i, p in enumerate(func.params)}

        # Validate kwargs against param names + reject collisions.
        rightmost = len(args) - 1
        for kw_name in kwargs:
            idx = name_to_index.get(kw_name)
            if idx is None:
                return None  # unknown kwarg
            if idx < len(args) and not func.params[idx].keyword_only:
                return None  # positional/keyword collision
            if idx > rightmost:
                rightmost = idx

        result: list[SuppliedFnSlot] = []
        for i in range(rightmost + 1):
            p = func.params[i]
            ptype = unwrap_ref_type(p.type)
            if i < len(args):
                if is_callable_type(ptype):
                    result.append(SuppliedFnSlot(
                        param_index=i, arg_expr=args[i],
                        arg_index=i, kwarg_name=None))
            elif p.name in kwargs:
                if is_callable_type(ptype):
                    result.append(SuppliedFnSlot(
                        param_index=i, arg_expr=kwargs[p.name],
                        arg_index=None, kwarg_name=p.name))
            elif p.has_default:
                pass  # default; not supplied
            else:
                return None  # missing required positional

        # Required positional / keyword-only params beyond rightmost
        # must have a default (positional case) or be in kwargs
        # (keyword-only case). Otherwise the candidate isn't viable for
        # this call shape.
        for i, p in enumerate(func.params):
            if i <= rightmost:
                continue
            if p.keyword_only:
                if not p.has_default and p.name not in kwargs:
                    return None
            else:
                if not p.has_default:
                    return None

        return result

    def _resolve_call_overloads(
        self,
        expr: TpyCall,
        func_infos: list[FunctionInfo],
        *,
        is_generic_for_pool: Callable[[FunctionInfo], bool],
        deref_checker: Callable[[TpyType], TpyType | None] | None = None,
        explicit_type_args: tuple[TpyType, ...] | None = None,
        strip_ref_own: bool = False,
        resolve_pending_view: bool = False,
        preserve_declaration_order: bool = True,
    ) -> ResolveResult:
        """Selection-only overload resolution shared by builtin and user paths.

        Picks a regime based on how many candidates have an Fn-typed param
        filled by a *supplied* (non-default) call argument:

        - 0 -> existing flow with ``fn_generic = None`` (no two-phase Fn
          arg analysis).
        - 1 -> existing flow with that single candidate as ``fn_generic``
          (preserves builtin behavior including
          ``_analyze_lambda_with_fn_hint`` body-return TPR inference).
        - 2+ -> per-candidate Fn arg trial (Regime C).

        Per-path knobs (``is_generic_for_pool``, Ref/Own stripping, etc.)
        cover divergences in pool building between user and builtin paths.

        Raises ``OverloadAmbiguityError`` from ``resolve_overload`` --
        the caller wraps to surface a located diagnostic.
        """
        # Regime predicate: which candidates have at least one Fn-typed
        # param filled by a supplied (non-default) arg? Short-circuit when
        # no overload has any callable-typed param at all -- skips the
        # per-candidate _supplied_fn_slots walks for the common
        # non-callable overload set.
        any_fn_typed_param = any(
            is_callable_type(unwrap_ref_type(p.type))
            for f in func_infos for p in f.params
        )
        fn_bearing_supplied: list[tuple[FunctionInfo, list[SuppliedFnSlot]]] = []
        if any_fn_typed_param:
            for f in func_infos:
                slots = self._supplied_fn_slots(expr, f)
                if slots:  # non-None and non-empty
                    fn_bearing_supplied.append((f, slots))

        # Route to Regime C when there are 2+ Fn-bearing-by-supplied
        # candidates, or when any candidate has a kwarg-supplied Fn slot
        # (Regime B's `_infer_arg_types` is positional-only and would
        # leave a kwarg-supplied lambda untyped).
        any_kwarg_fn_slot = any(
            s.kwarg_name is not None
            for _, slots in fn_bearing_supplied
            for s in slots
        )
        if len(fn_bearing_supplied) >= 2 or (
            fn_bearing_supplied and any_kwarg_fn_slot
        ):
            return self._resolve_regime_c(
                expr, fn_bearing_supplied,
                deref_checker=deref_checker,
                explicit_type_args=explicit_type_args,
                strip_ref_own=strip_ref_own,
                resolve_pending_view=resolve_pending_view,
            )

        # Regime A (0) or B (1): single fn_generic candidate (or none).
        fn_generic = fn_bearing_supplied[0][0] if fn_bearing_supplied else None

        if fn_generic is not None:
            # Seed Fn-bearing candidate with the LHS hint so nested-generic args
            # see the seeded ptype on first analysis (matches the non-overloaded
            # path in `_analyze_generic_function_call`). Explicit positional
            # type args override the seed at their positions, mirroring the
            # partial-explicit merge in `_analyze_generic_function_call`'s
            # explicit-type-args branch (search for ``merged_seed = dict(seed_subst)``).
            seed_subst = self.type_ops.seed_subst_from_return_hint(
                fn_generic, self.ctx.expr_type_hint
            )
            if expr.type_args:
                # Partial-explicit allowed: zip's shorter-of-two semantics +
                # the ``ta is not None`` guard handle wildcards and short
                # lists. Validation of over-long lists happens elsewhere.
                merged_seed = dict(seed_subst)
                for tp, ta in zip(fn_generic.type_params, expr.type_args):
                    if ta is not None:
                        merged_seed[tp] = ta
                seed_subst = merged_seed
            arg_types = self._infer_arg_types(expr, fn_generic, seed_subst=seed_subst)
        else:
            arg_types = self._probe_analyze_args(expr, func_infos)

        if strip_ref_own:
            arg_types = [unwrap_own(unwrap_ref_type(t)) for t in arg_types]

        kwarg_types: dict[str, TpyType] | None = None
        if expr.kwargs:
            kwarg_types = {k: self.expr.analyze_expr(v) for k, v in expr.kwargs.items()}

        # Build the unified candidate pool. Pool ordering matters for
        # ambiguity tiebreak in resolve_overload's stable-sort dedupe,
        # so we mirror each path's existing order.
        if preserve_declaration_order:
            iter_funcs = func_infos
        else:
            non_generic = [f for f in func_infos if not is_generic_for_pool(f)]
            generic = [f for f in func_infos if is_generic_for_pool(f)]
            iter_funcs = non_generic + generic

        candidates: list[FunctionInfo] = []
        generic_originals: dict[int, tuple[FunctionInfo, dict[str, TpyType]]] = {}

        for func in iter_funcs:
            if is_generic_for_pool(func):
                type_subst = self.type_ops.infer_type_params_for_function(
                    func, arg_types, self.protocols.satisfies_bound,
                    expected_return_type=self.ctx.expr_type_hint,
                    explicit_type_args=explicit_type_args,
                )
                if type_subst is not None:
                    if resolve_pending_view:
                        for k, v in type_subst.items():
                            if isinstance(v, PendingViewType):
                                type_subst[k] = v.family.owned_type
                    resolved = self.type_ops.substitute_method_type_params(func, type_subst)
                    candidates.append(resolved)
                    generic_originals[id(resolved)] = (func, type_subst)
            else:
                candidates.append(func)

        enriched_types = _enrich_literal_types(arg_types, expr.args, candidates)
        matched = resolve_overload(
            candidates, enriched_types,
            protocol_checker=self.protocols.type_conforms_to_protocol,
            deref_checker=deref_checker,
            default_int_type=self.ctx.default_int_type,
            subclass_checker=self.ctx.registry.is_subclass_of,
            protocol_classifier=self.protocols.classify_protocol_conformance,
            type_ops=self.type_ops,
            kwarg_types=kwarg_types,
        )

        matched_origin = generic_originals.get(id(matched)) if matched is not None else None

        # Regime A/B contextual_callable_used: True when a TpyLambda or
        # TpyName/FUNCTION at a Fn-supplied position participated in
        # arg typing (via _infer_arg_types -> analyze_expr_with_hint or
        # _try_resolve_function_ref). This gates the legacy
        # structural-match fallback at the caller.
        contextual = (
            fn_generic is not None
            and any(self._is_contextual_fn_arg(s.arg_expr)
                    for _, slots in fn_bearing_supplied
                    for s in slots)
        )

        return ResolveResult(
            matched=matched,
            matched_origin=matched_origin,
            arg_types=arg_types,
            enriched_types=enriched_types,
            kwarg_types=kwarg_types,
            contextual_callable_used=contextual,
        )

    def _probe_analyze_args(
        self,
        expr: TpyCall,
        func_infos: list[FunctionInfo],
    ) -> list[TpyType]:
        """Pre-analyze overload-candidate args with an LHS-derived per-arg hint.

        Mirrors the seed logic in ``_analyze_generic_function_call`` so nested
        generic-call/record-ctor args see the seeded ptype on their *first*
        analysis pass -- without seeding here, the inner call's analyze_expr
        caches a hint-naive type that the post-selection retry then can't
        refresh (the cache short-circuit at the top of
        ``_analyze_generic_function_call`` / ``_analyze_record_constructor``
        returns the cached value verbatim).

        For each arg position, picks the per-arg hint from the first candidate
        whose return shape matches the LHS hint (generic: via seed; already-
        substituted: via direct return-type match). Behavior change for
        ambiguous overloads: arg analysis is now biased toward candidates
        whose return matches the LHS -- aligns with the user's intent
        (``r: T = wrap(...)`` should prefer the wrap overload returning
        T-shape).
        """
        lhs_hint = self.ctx.expr_type_hint
        if lhs_hint is None:
            return [self.expr.analyze_call_arg(arg) for arg in expr.args]

        n = len(expr.args)
        candidate_hints = [
            self.type_ops.candidate_arg_hints(f, n, lhs_hint) for f in func_infos
        ]
        # Require a single LHS-matching candidate to avoid cross-candidate
        # hint mixing. With 2+ matching candidates, per-position "first
        # non-None wins" would splice hints across overloads, and an inner
        # generic-ctor/call arg analyzed under one candidate's hint would
        # cache the wrong T -- a type the post-selection cache short-circuit
        # at ``_analyze_record_constructor`` / ``_analyze_generic_function_call``
        # can't refresh. ``candidate_arg_hints`` returns None for non-matching
        # candidates, so the count below tracks LHS-matchers specifically (a
        # candidate that matches LHS but has all-None per-arg hints still
        # counts -- otherwise a different LHS-matching candidate could win
        # resolve_overload and find its winner's hint shape unseeded).
        matching = [c for c in candidate_hints if c is not None]
        chosen: list[TpyType | None] = (
            list(matching[0]) if len(matching) == 1 else [None] * n
        )
        arg_types: list[TpyType] = []
        for i, arg in enumerate(expr.args):
            hint = chosen[i]
            # Drop Callable hints when the arg is a TpyName or TpyLambda:
            # ``analyze_expr_with_hint`` would route through
            # ``_try_resolve_function_ref`` (TpyName) or
            # ``_analyze_lambda_with_fn_hint`` (TpyLambda) and mutate AST
            # state on the arg node (``is_function_ref`` / inferred lambda
            # signature) BEFORE the overload winner is known. The mutation
            # persists past the probe and would mislead codegen if the
            # selected overload differs from the seed source. Lambdas
            # supplied to 2+ Fn-bearing candidates already route through
            # regime C's per-candidate trial, so dropping the hint here
            # doesn't break legitimate body-type inference.
            if (hint is not None
                    and is_callable_type(unwrap_send_sync(hint))
                    and isinstance(arg, (TpyName, TpyLambda))):
                hint = None
            arg_types.append(self.expr.analyze_call_arg(arg, hint))
        return arg_types

    def _is_function_binding(self, expr: TpyName) -> bool:
        """True iff ``expr`` resolves to a function (local FUNCTION binding,
        imported function, or registry function), as opposed to a
        variable, record, module, etc. Used by Regime C to decide which
        TpyName args need the per-candidate dry matcher vs. unhinted
        pre-analysis.

        Falls through to the registry only when the namespace lookup
        misses entirely; an existing non-FUNCTION binding (RECORD, ENUM,
        MODULE, IMPORTED_NAME, BUILTIN) shadows the registry.
        """
        if self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(expr.name)
            if binding:
                return (binding.kind == BindingKind.FUNCTION
                        and bool(binding.func_infos))
        return self.ctx.registry.get_function(expr.name) is not None

    def _is_contextual_fn_arg(self, arg: TpyExpr) -> bool:
        """True iff ``arg`` is contextual evidence for callable typing:
        a `TpyLambda`, or a `TpyName` that resolves to a function (local
        FUNCTION binding, imported, or registry).
        """
        if isinstance(arg, TpyLambda):
            return True
        if isinstance(arg, TpyName):
            return self._is_function_binding(arg)
        return False

    def _resolve_regime_c(
        self,
        expr: TpyCall,
        fn_bearing_supplied: list[tuple[FunctionInfo, list[SuppliedFnSlot]]],
        *,
        deref_checker: Callable[[TpyType], TpyType | None] | None,
        explicit_type_args: tuple[TpyType, ...] | None,
        strip_ref_own: bool,
        resolve_pending_view: bool,
    ) -> ResolveResult:
        """Per-candidate Fn arg typing for 2+ Fn-bearing-by-supplied
        candidates.

        Each candidate's substituted Fn signature drives the synthesized
        arg type for its Fn-typed positions. Lambdas use the candidate's
        hint directly (no body analysis); named function refs use
        `_match_function_to_hint_data`'s concrete callable type so
        return TPRs can be pinned without body analysis.

        V1 limit: a TpyLambda at a slot whose substituted hint return
        type is still a TPR (after partial inference) and isn't pinned
        by `ctx.expr_type_hint` rejects that candidate. Workaround:
        hoist the lambda to a typed local.
        """
        # Identify positional indices and kwarg names that are Fn-supplied
        # for at least one candidate -- those need per-candidate handling.
        fn_pos_idx_supplied: set[int] = set()
        fn_kw_names_supplied: set[str] = set()
        for _, slots in fn_bearing_supplied:
            for s in slots:
                if s.arg_index is not None:
                    fn_pos_idx_supplied.add(s.arg_index)
                else:
                    assert s.kwarg_name is not None
                    fn_kw_names_supplied.add(s.kwarg_name)

        baseline_pos: list[TpyType | None] = [None] * len(expr.args)
        baseline_kw: dict[str, TpyType] = {}
        saved_unhinted_error: SemanticError | None = None

        # Pre-analyze: positions/kwargs that don't need contextual typing.
        # An arg needs contextual typing iff it's a TpyLambda or a TpyName
        # resolving to a function. Everything else (variables, literals,
        # arbitrary expressions) is pre-analyzed once.
        for i, arg in enumerate(expr.args):
            if i in fn_pos_idx_supplied and self._is_contextual_fn_arg(arg):
                continue  # defer to per-candidate
            try:
                baseline_pos[i] = self.expr.analyze_call_arg(arg)
            except SemanticError as e:
                if saved_unhinted_error is None:
                    saved_unhinted_error = e
        if expr.kwargs:
            for k, v in expr.kwargs.items():
                if k in fn_kw_names_supplied and self._is_contextual_fn_arg(v):
                    continue
                try:
                    baseline_kw[k] = self.expr.analyze_expr(v)
                except SemanticError as e:
                    if saved_unhinted_error is None:
                        saved_unhinted_error = e

        if strip_ref_own:
            baseline_pos = [
                unwrap_own(unwrap_ref_type(t)) if t is not None else None
                for t in baseline_pos
            ]
            baseline_kw = {k: unwrap_own(unwrap_ref_type(v)) for k, v in baseline_kw.items()}

        # Per-candidate trial: build (substituted_candidate, arg_types,
        # kwarg_types, original, type_subst) for each candidate that
        # passes the trial.
        candidate_evidences: list[tuple[
            FunctionInfo,                                  # substituted
            list[TpyType],                                 # arg_types
            dict[str, TpyType],                            # kwarg_types
            tuple[FunctionInfo, dict[str, TpyType]] | None,  # origin
        ]] = []
        saved_dry_error: SemanticError | None = None
        # Mark contextual usage if ANY supplied Fn slot has contextual
        # evidence (TpyLambda or TpyName -> function), regardless of
        # whether per-candidate trials succeed. This gates the caller's
        # legacy structural-match fallback -- if we already reasoned over
        # contextual callable typing, the fallback (which would match a
        # synthesized ANY placeholder) must not fire.
        contextual_callable_used = any(
            self._is_contextual_fn_arg(s.arg_expr)
            for _, slots in fn_bearing_supplied
            for s in slots
        )

        for func, fn_slots in fn_bearing_supplied:
            try:
                evidence = self._build_regime_c_evidence(
                    expr, func, fn_slots,
                    baseline_pos=baseline_pos,
                    baseline_kw=baseline_kw,
                    explicit_type_args=explicit_type_args,
                    resolve_pending_view=resolve_pending_view,
                )
            except SemanticError as e:
                if saved_dry_error is None:
                    saved_dry_error = e
                continue
            if evidence is None:
                continue
            candidate_evidences.append(evidence)

        # Score candidates manually (resolve_overload takes a single
        # arg_types; we have per-candidate arg_types here).
        protocol_checker = self.protocols.type_conforms_to_protocol
        classifier = self.protocols.classify_protocol_conformance
        scored: list[tuple[
            tuple[tuple[int, ...], int],
            FunctionInfo,
            list[TpyType],
            dict[str, TpyType],
            tuple[FunctionInfo, dict[str, TpyType]] | None,
        ]] = []
        for substituted, arg_types, kwarg_types, origin in candidate_evidences:
            per_arg = _classify_overload(
                substituted, arg_types,
                protocol_checker, classifier,
                self.ctx.default_int_type, self.type_ops,
                kwarg_types=kwarg_types or None,
            )
            if per_arg is not None:
                scored.append((_score(per_arg), substituted, arg_types, kwarg_types, origin))

        matched: FunctionInfo | None = None
        matched_origin: tuple[FunctionInfo, dict[str, TpyType]] | None = None
        winner_arg_types: list[TpyType] | None = None
        winner_kwarg_types: dict[str, TpyType] | None = None

        if scored:
            scored.sort(key=lambda c: c[0])
            best_score = scored[0][0]
            seen: set[tuple[TpyType, ...]] = set()
            unique_tied: list[tuple[FunctionInfo, list[TpyType], dict[str, TpyType], tuple[FunctionInfo, dict[str, TpyType]] | None]] = []
            for score, substituted, args, kwargs, origin in scored:
                if score != best_score:
                    break
                key = tuple(p.type for p in substituted.params)
                if key not in seen:
                    seen.add(key)
                    unique_tied.append((substituted, args, kwargs, origin))
            if len(unique_tied) > 1:
                raise OverloadAmbiguityError(tuple(u[0] for u in unique_tied))
            matched, winner_arg_types, winner_kwarg_types, matched_origin = unique_tied[0]
        else:
            # Coercion-pass fallback: mirror resolve_overload's pass 2 so
            # `Ptr[T] -> T`, `BigInt -> Int32`, and other registered
            # coercions still apply at non-Fn slots when no candidate
            # passed strict scoring.
            subclass_checker = self.ctx.registry.is_subclass_of
            coercion_scored: list[tuple[
                int, int,
                FunctionInfo,
                list[TpyType],
                dict[str, TpyType],
                tuple[FunctionInfo, dict[str, TpyType]] | None,
            ]] = []
            for substituted, arg_types, kwarg_types, origin in candidate_evidences:
                effective_args = arg_types
                if kwarg_types:
                    expanded = _expand_arg_types_with_kwargs(
                        effective_args, kwarg_types, substituted)
                    if expanded is None:
                        continue
                    effective_args = expanded
                if not (substituted.min_args <= len(effective_args) <= substituted.max_args):
                    continue
                if all(type_matches_with_coercion(arg_t, ptype, protocol_checker,
                                                  deref_checker, subclass_checker)
                       for arg_t, (_, ptype) in zip(effective_args, substituted.params)):
                    numeric_score = sum(1 for arg_t, (_, ptype) in zip(effective_args, substituted.params)
                                        if type_matches_numeric(arg_t, ptype))
                    narrowing = sum(1 for arg_t, (_, ptype) in zip(effective_args, substituted.params)
                                    if is_big_int_type(arg_t) and is_fixed_int_type(unwrap_ref_type(ptype)))
                    coercion_scored.append(
                        (numeric_score, narrowing, substituted, arg_types, kwarg_types, origin))
            if coercion_scored:
                coercion_scored.sort(key=lambda x: (-x[0], x[1]))
                _, _, matched, winner_arg_types, winner_kwarg_types, matched_origin = coercion_scored[0]

        # arg_types for downstream diagnostics: the winner's typed list
        # if any candidate matched, else the baseline (with ANY at any
        # contextual slot that was deferred and never filled in).
        if winner_arg_types is not None:
            display_arg_types = winner_arg_types
        else:
            display_arg_types = [
                baseline_pos[i] if baseline_pos[i] is not None else ANY
                for i in range(len(expr.args))
            ]
        display_kwarg_types = winner_kwarg_types if winner_kwarg_types is not None else baseline_kw

        # Stash priority: per-candidate dry error (most informative for
        # the user's call shape) > pre-analysis failure.
        first_contextual_error = saved_dry_error or saved_unhinted_error

        # Enrichment uses the substituted candidate pool for literal
        # promotion. Build a candidate list from the scored entries (or
        # candidate_evidences if none scored) for enriched_types.
        pool_for_enrich = [s[1] for s in scored] if scored else [e[0] for e in candidate_evidences]
        enriched_types = _enrich_literal_types(display_arg_types, expr.args, pool_for_enrich)

        return ResolveResult(
            matched=matched,
            matched_origin=matched_origin,
            arg_types=display_arg_types,
            enriched_types=enriched_types,
            kwarg_types=display_kwarg_types or None,
            contextual_callable_used=contextual_callable_used,
            first_contextual_error=first_contextual_error,
        )

    def _build_regime_c_evidence(
        self,
        expr: TpyCall,
        func: FunctionInfo,
        fn_slots: list[SuppliedFnSlot],
        *,
        baseline_pos: list[TpyType | None],
        baseline_kw: dict[str, TpyType],
        explicit_type_args: tuple[TpyType, ...] | None,
        resolve_pending_view: bool,
    ) -> tuple[
        FunctionInfo,                                # substituted candidate
        list[TpyType],                               # arg_types
        dict[str, TpyType],                          # kwarg_types
        tuple[FunctionInfo, dict[str, TpyType]] | None,  # origin
    ] | None:
        """Build per-candidate evidence for Regime C scoring.

        Returns None to silently reject this candidate (e.g. a baseline
        slot is None because pre-analysis failed, or a Fn slot's
        substituted hint has unresolved param TPRs). May raise
        ``SemanticError`` for ambiguity / generic-rejection on a
        function-ref dry match -- caller stashes and treats as
        candidate rejection.
        """
        # Reject early when this candidate would need a non-Fn-slot
        # arg whose baseline analysis failed (saved_unhinted_error path).
        # After this check every non-Fn-slot position has a real
        # baseline type; Fn-slot positions are filled below.
        fn_slot_pos_indices = {s.arg_index for s in fn_slots if s.arg_index is not None}
        fn_slot_kw_names = {s.kwarg_name for s in fn_slots if s.kwarg_name is not None}
        for i in range(len(expr.args)):
            if i not in fn_slot_pos_indices and baseline_pos[i] is None:
                return None
        # Same check for kwargs: a non-Fn kwarg whose pre-analysis raised
        # is absent from baseline_kw. Don't let inference run with a
        # silently-missing kwarg type -- reject the candidate.
        if expr.kwargs:
            for kw_name in expr.kwargs:
                if kw_name in fn_slot_kw_names:
                    continue
                if kw_name not in baseline_kw:
                    return None

        # Build arg_types -- non-Fn slots from baseline, Fn slots filled
        # by the per-slot trial below. The placeholder for Fn slots is
        # overwritten unconditionally before scoring.
        arg_types: list[TpyType] = [
            baseline_pos[i] if i not in fn_slot_pos_indices else VOID
            for i in range(len(expr.args))
        ]
        kwarg_types: dict[str, TpyType] = dict(baseline_kw)

        # Partial inference from already-typed args (non-Fn slots only,
        # so the inference reflects what non-callable args determine).
        partial_inferred: dict[str, TpyType] = {}
        if func.is_generic():
            for i, ((_, ptype), arg_type) in enumerate(zip(func.params, arg_types)):
                if i in fn_slot_pos_indices:
                    continue  # Fn slot placeholder; filled below
                self.type_ops.match_type_with_inference(ptype, arg_type, partial_inferred)
            for kw_name, kw_t in kwarg_types.items():
                if kw_name in fn_slot_kw_names:
                    continue  # Fn slot; defer
                idx = next((i for i, p in enumerate(func.params) if p.name == kw_name), None)
                if idx is None:
                    continue
                self.type_ops.match_type_with_inference(func.params[idx].type, kw_t, partial_inferred)
            # Resolve IntLit -> default int, PendingView -> owned (mirrors
            # _infer_arg_types' resolution step).
            for k, v in list(partial_inferred.items()):
                if isinstance(v, IntLiteralType):
                    partial_inferred[k] = self.ctx.default_int_type or INT32
                elif isinstance(v, PendingViewType):
                    partial_inferred[k] = v.family.owned_type

        # Per-Fn-slot trial.
        for slot in fn_slots:
            ptype = unwrap_send_sync(unwrap_ref_type(func.params[slot.param_index].type))
            hint = (partial_substitute(ptype, partial_inferred)
                    if func.is_generic() else ptype)
            if not is_callable_type(hint):
                return None
            if any(contains_type_param(p) for p in hint.param_types):
                # V1 limit: hint param types still have unresolved TPRs.
                # No way to validate the lambda/ref against this slot.
                return None

            arg = slot.arg_expr
            if isinstance(arg, TpyLambda):
                if len(arg.param_names) != len(hint.param_types):
                    return None
                # An unresolved return TPR after partial inference is pinned
                # via a body-analysis trial under ``trial_scope`` -- the
                # winner is re-analyzed by _analyze_single_function_call so
                # the trial commits no state. The context-pinnable shortcut
                # skips the trial when downstream cross-arg inference can
                # pin the TPR from the call-site hint alone.
                needs_body_trial = (
                    isinstance(hint.return_type, TypeParamRef)
                    and not self._return_tpr_pinnable_from_context(func, hint.return_type)
                )
                slot_type: TpyType = (
                    self._lambda_body_dry_run(arg, hint) if needs_body_trial else hint
                )
            elif isinstance(arg, TpyName) and self._is_function_binding(arg):
                # Pure dry matcher: returns matched data, raises on ambiguity.
                if self.ctx.func.current_ns:
                    binding = self.ctx.func.current_ns.lookup(arg.name)
                    if binding and binding.kind == BindingKind.FUNCTION and binding.func_infos:
                        func_infos = binding.func_infos
                    else:
                        func_infos = self.ctx.registry.get_function(arg.name)
                else:
                    func_infos = self.ctx.registry.get_function(arg.name)
                if not func_infos:
                    return None
                matched_data = self.expr._match_function_to_hint_data(
                    func_infos, hint, arg.name, arg)
                if matched_data is None:
                    return None
                # Build the matcher's concrete callable type from the
                # function's actual signature substituted with inferred
                # type args. This can supply a return type the partial
                # hint didn't.
                fi, type_args = matched_data
                slot_type = self.expr.build_concrete_callable(fi, type_args, hint)
            else:
                # Pre-analyzed at baseline (variable / arbitrary expr).
                # If pre-analysis failed (baseline is None), reject.
                if slot.arg_index is not None:
                    if baseline_pos[slot.arg_index] is None:
                        return None
                    slot_type = baseline_pos[slot.arg_index]
                else:
                    if slot.kwarg_name not in baseline_kw:
                        return None
                    slot_type = baseline_kw[slot.kwarg_name]

            if slot.arg_index is not None:
                arg_types[slot.arg_index] = slot_type
            else:
                kwarg_types[slot.kwarg_name] = slot_type

        # Run full inference on the candidate. The inference takes a
        # positional list, so expand kwargs into positional slots first.
        if func.is_generic():
            expanded_args = _expand_arg_types_with_kwargs(
                arg_types, kwarg_types, func
            ) if kwarg_types else arg_types
            if expanded_args is None:
                return None
            type_subst = self.type_ops.infer_type_params_for_function(
                func, expanded_args, self.protocols.satisfies_bound,
                expected_return_type=self.ctx.expr_type_hint,
                explicit_type_args=explicit_type_args,
            )
            if type_subst is None:
                return None
            if resolve_pending_view:
                for k, v in type_subst.items():
                    if isinstance(v, PendingViewType):
                        type_subst[k] = v.family.owned_type
            substituted = self.type_ops.substitute_method_type_params(func, type_subst)
            origin = (func, type_subst)
        else:
            substituted = func
            origin = None

        return substituted, arg_types, kwarg_types, origin

    def _return_tpr_pinnable_from_context(
        self, func: FunctionInfo, return_tpr: TypeParamRef,
    ) -> bool:
        """Heuristic: would `ctx.expr_type_hint` pin `return_tpr` via
        cross-arg inference downstream?

        True if the call site has an expected return type and matching
        ``func.return_type`` against it would resolve ``return_tpr``.
        Used by Regime C to decide whether to keep a candidate whose
        Fn-slot hint has an unresolved return TPR after partial
        inference -- if context can pin it, we don't need lambda body
        analysis.
        """
        hint = self.ctx.expr_type_hint
        if hint is None:
            return False
        trial: dict[str, TpyType] = {}
        if not self.type_ops.match_type_with_inference(func.return_type, hint, trial):
            return False
        return return_tpr.name in trial

    def _lambda_body_dry_run(
        self, lambda_expr: TpyLambda, hint: 'CallableType',
    ) -> 'CallableType':
        """Tentatively analyze ``lambda_expr`` body under ``hint`` to pin
        an unresolved return TPR; roll back all state on exit.

        Returns the resolved CallableType (with the body's inferred type
        substituted for the return TPR). Propagates ``SemanticError`` from
        body analysis -- the caller (``_build_regime_c_evidence``) lets it
        propagate so the regime-driver stashes it into ``saved_dry_error``
        and surfaces the most informative per-candidate error when no
        candidate passes.

        The trial commits no state. The winning candidate's lambda body is
        re-analyzed by the post-Regime-C single-function-call path, which
        sets the lambda AST's ``inferred_*`` fields and registers all body
        sub-expression types and call edges into the live function state.
        """
        # AST-side state is not part of SemanticContext, so save/restore here.
        saved_inferred_param_types = list(lambda_expr.inferred_param_types)
        saved_inferred_return_type = lambda_expr.inferred_return_type
        saved_captured_names = list(lambda_expr.captured_names)
        saved_captures_by_value = lambda_expr.captures_by_value
        try:
            with self.ctx.trial_scope():
                return self.expr._analyze_lambda_with_fn_hint(lambda_expr, hint)
        finally:
            lambda_expr.inferred_param_types = saved_inferred_param_types
            lambda_expr.inferred_return_type = saved_inferred_return_type
            lambda_expr.captured_names = saved_captured_names
            lambda_expr.captures_by_value = saved_captures_by_value

    def _analyze_builtin_function_overloads(self, expr: TpyCall, overloads: list[FunctionInfo]) -> TpyType:
        """Type-check a call to a builtin function using unified FunctionInfo overloads.

        Uses two-pass overload resolution: prefer exact type matches over coercion matches.
        For generic overloads (with type_params), uses type inference.
        """
        self._flatten_key_kwarg(expr, overloads[0].name)
        self._reject_kwargs_for_builtin(expr, overloads[0].name)
        protocol_checker = self.protocols.type_conforms_to_protocol

        # Validate explicit type args before generic inference
        if expr.type_args_parse_error:
            raise self.ctx.error(expr.type_args_parse_error, expr)
        explicit: tuple[TpyType, ...] | None = None
        generic = [o for o in overloads if _has_type_param_ref_in_params(o)]
        if expr.type_args and generic:
            max_tp = max(len(o.type_params) for o in generic)
            self._validate_explicit_type_args(expr, max_tp)
            explicit = expr.type_args

        try:
            result = self._resolve_call_overloads(
                expr, overloads,
                is_generic_for_pool=_has_type_param_ref_in_params,
                deref_checker=self.type_ops.get_deref_coercion_target,
                explicit_type_args=explicit,
                strip_ref_own=True,
                resolve_pending_view=True,
                preserve_declaration_order=False,
            )
        except OverloadAmbiguityError as e:
            raise self._ambiguous_overload_error(expr, overloads[0].name, e)

        arg_types = result.arg_types
        matched = result.matched

        if matched is not None:
            expr.resolved_function_info = matched
            self._validate_lvalue_params(expr)
            self._check_error_return_handled(expr, matched)
            self._record_mutation_call_edges(expr)
            for i, (arg, arg_t, (pname, ptype)) in enumerate(zip(expr.args, arg_types, matched.params)):
                self._maybe_coerce_empty_list_to_protocol(arg_t, ptype)
                self.check_own_param(arg, arg_t, pname, ptype)
                if arg_t != ptype:
                    expr.args[i] = self.compat.coerce_expr(arg, arg_t, ptype,
                                                            f"argument '{pname}'",
                                                            coercion_ctx=CoercionContext.ARG)
            if result.matched_origin is not None:
                overload, type_subst = result.matched_origin
                expr.inferred_type_args = tuple(
                    self._resolve_inferred_type_arg(type_subst[p])
                    for p in overload.type_params)
                expr.representational_subst_params = (
                    self.type_ops.compute_representational_subst_params(
                        overload, expr.inferred_type_args))
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
                if overload.is_builtin_function and overload.type_params:
                    ret = strip_template_repr(ret)
                return ret
            return matched.return_type

        # Surface the most informative per-candidate Regime C error
        # before generic fallbacks. The contextual_callable_used flag
        # ensures we only surface this when synthesized callable
        # evidence was actually used (see Regime C in
        # _resolve_call_overloads).
        if result.contextual_callable_used and result.first_contextual_error is not None:
            raise result.first_contextual_error

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

        # Check for bound violations on generic overloads (give specific error).
        # Skip type args that resolved to UnknownElementType -- the only signal
        # was an empty container literal with no @type_param_default fallback;
        # "??? does not satisfy <bound>" misleads, and the downstream
        # _analyze_single_function_call retry will surface the cleaner
        # "Cannot infer type arguments" diagnostic.
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
                    if isinstance(type_arg, UnknownElementType):
                        continue
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
            # Pre-validate explicit type-arg arity against the max type-param
            # count across generic overloads. Without this, an over-long list
            # like `f[A, B, C]` on an overload set whose max type_params is 2
            # gets every candidate rejected by `infer_type_params_for_function`
            # (len > type_params returns None), the structural-match fallback
            # at line 3893 catches each candidate's `_validate_explicit_type_args`
            # raise and continues, and the user sees "No matching @overload"
            # instead of the specific "too many type arguments" error.
            # Mirrors the builtin overload path at calls.py:3815.
            if expr.type_args:
                generic = [f for f in func_infos if f.is_generic()]
                if generic:
                    max_tp = max(len(f.type_params) for f in generic)
                    self._validate_explicit_type_args(expr, max_tp)
            try:
                result = self._resolve_call_overloads(
                    expr, func_infos,
                    is_generic_for_pool=lambda f: f.is_generic(),
                )
            except OverloadAmbiguityError as e:
                raise self._ambiguous_overload_error(expr, expr.func_name, e)

            arg_types = result.arg_types
            kwarg_types = result.kwarg_types
            enriched_types = result.enriched_types

            if result.matched is not None:
                if result.matched_origin is not None:
                    original, _ = result.matched_origin
                    return self._analyze_single_function_call(expr, original)
                return self._analyze_single_function_call(expr, result.matched)

            # No match in unified pool.
            #
            # The legacy structural-match fallback preserves targeted
            # diagnostics like unsafe_cast's "requires a type argument".
            # It uses raw _structural_match against un-resolved generics
            # and runs _analyze_single_function_call, which would feed
            # contextual evidence (lambdas, function refs) back through
            # full hint-driven analysis. If our pool already used
            # synthesized callable evidence via Regime C, running the
            # fallback would either reintroduce the bug class or produce
            # misleading diagnostics. Skip in that case and surface the
            # stashed per-candidate error instead.
            if not result.contextual_callable_used:
                try:
                    matched = resolve_overload(
                        func_infos, enriched_types,
                        protocol_checker=self.protocols.type_conforms_to_protocol,
                        protocol_classifier=self.protocols.classify_protocol_conformance,
                        default_int_type=self.ctx.default_int_type,
                        subclass_checker=self.ctx.registry.is_subclass_of,
                        kwarg_types=kwarg_types,
                    )
                except OverloadAmbiguityError as e:
                    raise self._ambiguous_overload_error(expr, expr.func_name, e)
                if matched is not None:
                    return self._analyze_single_function_call(expr, matched)
                for func in func_infos:
                    if func.is_generic():
                        try:
                            return self._analyze_single_function_call(expr, func)
                        except SemanticError:
                            continue
            elif result.first_contextual_error is not None:
                # Surface the most informative per-candidate rejection.
                raise result.first_contextual_error
            # Targeted diagnostic: a kwarg name that no overload accepts is
            # the most actionable failure cause; report it instead of a
            # bare arg-types listing that omits the kwarg.
            if kwarg_types:
                accepted_names: set[str] = {p.name for f in func_infos for p in f.params}
                for kw_name in kwarg_types:
                    if kw_name not in accepted_names:
                        raise self.ctx.error(
                            f"'{expr.func_name}' got unexpected keyword argument '{kw_name}'",
                            expr)
            arg_type_strs = ", ".join(str(unwrap_own(t)) for t in arg_types)
            raise self.ctx.error(
                f"No matching @overload for {expr.func_name}({arg_type_strs})", expr)
        return self._analyze_single_function_call(expr, func_infos[0])

    def _ambiguous_overload_error(
        self, expr: TpyCall, func_name: str, err: OverloadAmbiguityError,
    ) -> SemanticError:
        """Build a located diagnostic from an ``OverloadAmbiguityError``.

        Turns the exception-carried tied candidates into an actionable message
        anchored at the call site; callers ``raise`` the returned error.
        """
        sigs = "; ".join(
            f"{c.name}({', '.join(str(p.type) for p in c.params)})"
            for c in err.candidates
        )
        return self.ctx.error(
            f"Ambiguous overload for '{func_name}': "
            f"multiple candidates match equally: {sigs}",
            expr,
        )

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

    def _inline_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Inline an @inline function call: clone body, substitute params, analyze."""
        body = copy.deepcopy(func.inline_body)

        # Validate FStr params receive f-string literals (or plain strings)
        for i, (pi, arg) in enumerate(zip(func.params, expr.args)):
            if is_fstr_type(pi.type) and not isinstance(arg, TpyFString):
                if isinstance(arg, TpyStrLiteral):
                    expr.args[i] = TpyFString(parts=[arg.value], loc=arg.loc)
                else:
                    raise self.ctx.error(
                        f"Parameter '{pi.name}' has type FStr -- only f-string "
                        f"or string literals are accepted", arg)

        # Build param name -> call-site arg map and substitute
        param_map: dict[str, TpyExpr] = {}
        for pi, arg in zip(func.params, expr.args):
            param_map[pi.name] = arg
        # Lazy: methods.py imports calls at module load; cycle breaks here.
        from .methods import MethodAnalyzer
        MethodAnalyzer._substitute_inline_body(body, None, param_map)

        # Store expansion for codegen
        expr.macro_expansion = body

        # Analyze the substituted expression
        self.expr.analyze_expr(body)
        return VOID

    def _analyze_single_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a call to a single user-defined function."""
        # @inline: clone body and substitute at call site
        if func.inline_body is not None:
            return self._inline_function_call(expr, func)

        # Handle generic functions
        if func.is_generic():
            return self._analyze_generic_function_call(expr, func)

        # Reject type args on non-generic functions
        if expr.type_args or expr.type_args_parse_error:
            raise self.ctx.error(
                f"Function '{expr.func_name}' is not generic and does not accept type arguments",
                expr,
            )

        # Pack **kwargs into TypedDict construction before regular kwargs resolution
        if func.kwarg_name:
            self._pack_kwargs_into_typed_dict(expr, func)
        elif expr.double_star_unpack is not None:
            raise self.ctx.error(
                f"'{expr.func_name}' does not accept **kwargs", expr)

        # Resolve regular kwargs before arity check
        self._resolve_call_kwargs(expr, func)

        # Append the TypedDict construction AFTER regular kwargs are resolved to positional
        if expr.kwarg_td_call is not None:
            expr.args.append(expr.kwarg_td_call)
            expr.kwarg_td_call = None

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

    def _maybe_coerce_empty_list_to_protocol(
        self, arg_type: TpyType, ptype: TpyType
    ) -> None:
        """Pin empty list literal's element type from a protocol param target.

        Runs post-overload-resolution. Mirrors the list-literal-vs-list[T]
        coercion in `local_deduction` but for protocol targets like
        `Iterable[T]` / `Sequence[T]`. Without this, the pending-list resolver
        errors "Cannot infer element type" for `f([])` where `f` takes
        `Iterable[T]`. Kept out of `type_conforms_to_protocol` to keep that
        check pure -- writing the coerced element type during overload probes
        would cement the first-probed overload's element type even if that
        overload is later rejected.
        """
        if not (isinstance(arg_type, PendingListType)
                and isinstance(arg_type.element_type, UnknownElementType)):
            return
        inner = unwrap_readonly(unwrap_ref_type(ptype))
        if not (is_protocol_type(inner) and isinstance(inner, NominalType)
                and inner.type_args and len(inner.type_args) == 1):
            return
        elem_type = inner.type_args[0]
        # Skip abstract/unresolved element types: pinning to a TypeParamRef or
        # UnknownElementType (which can happen when generic inference for T
        # fails on an all-empty-iterable call site) would just move the error
        # from the pending-list resolver ("Cannot infer") to codegen
        # ("UnknownElementType should be resolved"). Let the pending-list
        # resolver produce its clearer user-facing error instead.
        if isinstance(elem_type, (TypeParamRef, UnknownElementType)):
            return
        info = self.ctx.list_literals.get(arg_type.literal_id)
        if info is not None and info.coerced_element_type is None:
            info.coerced_element_type = elem_type

    def _typecheck_call_args(self, expr: TpyCall, func: FunctionInfo) -> None:
        """Type-check call arguments against function parameters (non-variadic)."""
        # Reject *unpacking on non-variadic functions
        for arg in expr.args:
            if isinstance(arg, TpyStarUnpack):
                raise self.ctx.error(
                    f"Cannot use *unpacking: '{func.name}' does not accept *args", arg)
        for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
            arg_type = self.expr.analyze_expr_with_hint(arg, ptype)
            self._maybe_coerce_empty_list_to_protocol(arg_type, ptype)
            arg_type = self._restore_readonly_arg(arg, arg_type, func.is_readonly)

            # A fresh Own[...] rvalue lent to a borrow param is safe: codegen
            # materializes a named temp that outlives the call. Strip the Own
            # and let coercion handle the rest (create-lend-drop is intentional;
            # ownership-transfer params declare Own[T] and skip this).
            if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType):
                arg_type = arg_type.wrapped

            self.check_own_param(arg, arg_type, pname, ptype)

            if not (is_char_type(ptype) and is_any_str_type(arg_type) and
                    isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                coerced_arg = self.compat.coerce_expr(arg, arg_type, ptype, f"argument '{pname}'",
                                                       coercion_ctx=CoercionContext.ARG)
                expr.args[i] = coerced_arg

            if isinstance(arg_type, PENDING_CONTAINER_TYPES):
                self.deduction.mark_container_param_context(arg, arg_type, ptype)
            if isinstance(arg_type, PendingViewType):
                self.deduction.mark_view_param_context(arg, ptype, arg_type.family)

    def _typecheck_and_coerce_arg(
        self, arg: TpyExpr, pname: str, ptype: TpyType, func_is_readonly: bool,
    ) -> TpyExpr:
        """Per-arg type check and coercion shared by the fixed and kwonly
        branches of `_analyze_and_pack_varargs` and by callable-value calls.
        Returns the (possibly rewrapped) expression to store back into the
        args list."""
        arg_type = self.expr.analyze_expr_with_hint(arg, ptype)
        arg_type = self._restore_readonly_arg(arg, arg_type, func_is_readonly)
        if isinstance(arg_type, OwnType) and not isinstance(ptype, OwnType):
            arg_type = arg_type.wrapped
        self.check_own_param(arg, arg_type, pname, ptype)
        # Pending literals/views resolve against the param context (a bare
        # `[..]` literal passed to a list[T] slot must become list, not
        # Array) -- same marks the non-variadic path applies.
        if isinstance(arg_type, PENDING_CONTAINER_TYPES):
            self.deduction.mark_container_param_context(arg, arg_type, ptype)
        if isinstance(arg_type, PendingViewType):
            self.deduction.mark_view_param_context(arg, ptype, arg_type.family)
        if (is_char_type(ptype) and is_any_str_type(arg_type)
                and isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
            return arg
        return self.compat.coerce_expr(
            arg, arg_type, ptype, f"argument '{pname}'",
            coercion_ctx=CoercionContext.ARG)

    def _analyze_and_pack_varargs(self, expr: TpyCall | TpyMethodCall, func: FunctionInfo) -> None:
        """Analyze call with variadic params: type-check fixed args, pack trailing args."""
        # Find variadic param index and count keyword-only params after it
        va_idx = next(i for i, p in enumerate(func.params) if p.is_variadic)
        va_param = func.params[va_idx]
        n_kwonly = sum(1 for p in func.params if p.keyword_only)

        # Vararg slot type from Span[T] (mutable *args) or Span[readonly[T]]
        # (readonly *args). Keep the readonly wrapper: it makes a readonly slot
        # accept both mutable and readonly args (adding const is safe) while a
        # mutable slot keeps rejecting readonly args, and it drives codegen's
        # varargs<T> vs varargs<const T> choice via pack.element_type.
        assert is_varargs(unwrap_ref_type(va_param.type))
        span_type = unwrap_ref_type(va_param.type)
        elem_type = span_type.type_args[0]

        # Split args: [fixed_positional...] [varargs...] [kwonly_defaults...]
        fixed_args = expr.args[:va_idx]
        kwonly_args = expr.args[len(expr.args) - n_kwonly:] if n_kwonly else []
        vararg_exprs = expr.args[va_idx:len(expr.args) - n_kwonly] if n_kwonly else expr.args[va_idx:]

        # A `*xs` unpack must be the SOLE entry in the vararg region. Codegen's
        # `_gen_vararg_pack` returns on the first star-unpack, dropping any
        # sibling positional args -- so `f(a, *xs)`, `f(*xs, b)`, `f(*a, *b)`
        # would silently miscompile (wrong arg set). Reject the mix until
        # codegen concatenates leading/trailing positionals with the unpacked
        # span. A lone `f(*xs)` (and `f(fixed, *xs)` where `fixed` fills a
        # non-variadic param) is fine -- those don't share the vararg region.
        if len(vararg_exprs) > 1 and any(
                isinstance(a, TpyStarUnpack) for a in vararg_exprs):
            star = next(a for a in vararg_exprs if isinstance(a, TpyStarUnpack))
            raise self.ctx.error(
                "Cannot mix *unpacking with other arguments in a *args call: "
                "`*iterable` must be the only variadic argument "
                f"(call to '{func.name}')", star)

        # Type-check fixed positional args
        for i, ((pname, ptype), arg) in enumerate(zip(func.params[:va_idx], fixed_args)):
            fixed_args[i] = self._typecheck_and_coerce_arg(arg, pname, ptype, func.is_readonly)

        # Type-check each variadic arg against element type T
        for i, arg in enumerate(vararg_exprs):
            if isinstance(arg, TpyStarUnpack):
                # *expr unpacking: validate + analyze the inner container
                # (analyze_call_arg sets the inner expr's type for codegen
                # and raises a clean diagnostic for non-unpackable shapes).
                unpacked_elem = self.expr.analyze_call_arg(arg, elem_type)
                # The container is forwarded wholesale to `varargs<T>(...)`,
                # which has no per-element coercion. A *mutable* `*args` slot
                # exposes mutable element access (operator[] -> T&), so unpacking
                # a `Span[readonly[T]]` source (std::span<const T>) into it would
                # both fail to construct and alias readonly data into a mutable
                # vararg -- reject it (mirrors the per-arg readonly gate the
                # non-unpack branch below gets via coerce_expr). A *readonly*
                # slot (`*xs: readonly[T]` -> varargs<const T>) is the safe
                # target: the const-span source constructs directly and the
                # body cannot mutate, so allow it. A differing element type is
                # rejected by the coercion check below regardless.
                # Note on forwarding (`def g(*xs): f(*xs)`): a *mutable* vararg
                # param `*xs: T` carries the non-readonly `varargs[T]` type, so it
                # never trips this gate; a *readonly* vararg `*xs: readonly[T]`
                # carries `varargs[readonly[T]]` and is correctly rejected here when
                # forwarded into a mutable slot (and accepted into a readonly
                # one via the slot_is_readonly branch). No provenance exemption
                # is needed -- the source's own span type already distinguishes.
                inner_type = self.ctx.get_expr_type(arg.expr)
                slot_is_readonly = isinstance(elem_type, ReadonlyType)
                if (inner_type is not None
                        and (is_readonly_span(inner_type) or varargs_is_readonly(inner_type))
                        and not slot_is_readonly):
                    raise self.ctx.error(
                        f"Cannot pass readonly[{elem_type}] as mutable "
                        f"{elem_type} when unpacking into *args "
                        f"(call to '{func.name}'); declare the parameter "
                        f"'*{va_param.name}: readonly[...]' to accept it", arg)
                coercion = self.compat.check_type_compatible(
                    unpacked_elem, elem_type, "*args (unpacked element)",
                    loc=getattr(arg, "loc", None),
                    coercion_ctx=CoercionContext.ARG)
                if coercion is not None:
                    raise self.ctx.error(
                        f"Cannot unpack into *args: element type "
                        f"'{unpacked_elem}' needs a conversion to '{elem_type}' "
                        f"that *unpacking cannot apply (call to '{func.name}')",
                        arg)
                continue
            arg_type = self.expr.analyze_expr_with_hint(arg, elem_type)
            arg_type = self._restore_readonly_arg(arg, arg_type, func.is_readonly)
            if isinstance(arg_type, OwnType):
                arg_type = arg_type.wrapped
            coerced_arg = self.compat.coerce_expr(
                arg, arg_type, elem_type, f"*args element {i}",
                coercion_ctx=CoercionContext.ARG)
            vararg_exprs[i] = coerced_arg

        # Type-check keyword-only args (resolve_kwargs has filled all slots)
        kwonly_params = [p for p in func.params if p.keyword_only]
        for i, (p, arg) in enumerate(zip(kwonly_params, kwonly_args)):
            kwonly_args[i] = self._typecheck_and_coerce_arg(arg, p.name, p.type, func.is_readonly)

        # Build pack node and reconstruct args list:
        # [fixed_args..., vararg_pack, kwonly_args...]
        pack = TpyVarargPack(args=vararg_exprs, element_type=elem_type, loc=expr.loc)
        expr.args = fixed_args + [pack] + kwonly_args

    def _check_error_return_handled(self, expr: TpyCall | TpyMethodCall, func: FunctionInfo) -> None:
        """Check that calls to @error_return functions are inside matching try/except."""
        if func.error_return_type is None:
            return
        # Inside matching try/except (or except ReturnException catch-all)
        ctx_error_type = self.ctx.func.try_except_error_type
        if error_return_matches(ctx_error_type, func.error_return_type) or ctx_error_type == "*":
            return
        # Auto-propagation: caller has matching @error_return(E), not inside a try/except
        # (inside try/except, the goto-based dispatch handles it instead)
        current = self.ctx.func.current_function
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

        # Get type substitution from explicit args or inference. The LHS-hint
        # seed is only useful on paths that actually re-analyze args with a
        # contextual hint, so it's computed lazily inside the branches that
        # need it -- skipping it for the fully-explicit type-args path avoids
        # the side-effecting match_type_with_inference walk against any
        # special-cased call (e.g. ``tpy.unsafe.unsafe_cast``) whose return
        # type is not a true predictor of its argument types.
        if expr.type_args:
            self._validate_explicit_type_args(expr, len(func.type_params))

            if len(expr.type_args) == len(func.type_params) and None not in expr.type_args:
                # Full explicit -- existing path; seed unused here.
                type_subst = dict(zip(func.type_params, expr.type_args))
            else:
                # Partial explicit -- infer remaining from args + context.
                # Explicit positional type args override the seed at those
                # positions; merge them in before propagating into per-arg hints.
                seed_subst = self.type_ops.seed_subst_from_return_hint(
                    func, self.ctx.expr_type_hint
                )
                merged_seed = dict(seed_subst)
                for tp, ta in zip(func.type_params, expr.type_args):
                    if ta is not None:
                        merged_seed[tp] = ta
                arg_types = self._infer_arg_types(expr, func, seed_subst=merged_seed)
                type_subst = self.type_ops.infer_type_params_for_function(
                    func, arg_types, self.protocols.satisfies_bound,
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
                self.protocols.satisfies_bound,
                lambda msg: self.ctx.error(msg, expr),
            )
        else:
            # Infer from arguments (seeded with LHS hint for nested-call hints).
            # Lets nested generic calls (e.g. `Rc.new(Box(Dog(...)))` with LHS
            # `Rc[Box[Pet]]`) see the seeded ptype as their first-pass hint --
            # the inner call's record-construction LHS-hint logic then picks up
            # T=Pet before arg-driven inference would have settled on T=Dog.
            seed_subst = self.type_ops.seed_subst_from_return_hint(
                func, self.ctx.expr_type_hint
            )
            arg_types = self._infer_arg_types(expr, func, seed_subst=seed_subst)
            type_subst = self.type_ops.infer_type_params_for_function(
                func, arg_types, self.protocols.satisfies_bound,
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

        # type_subst keeps literal-marked types for per-arg coercion below;
        # codegen needs them resolved so they don't reach C++ as template args.
        expr.inferred_type_args = tuple(
            self._resolve_inferred_type_arg(type_subst[p]) for p in func.type_params
        )
        expr.representational_subst_params = (
            self.type_ops.compute_representational_subst_params(
                func, expr.inferred_type_args))

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
            # `*unpack` is only valid at a variadic param position; a
            # non-variadic generic function must reject it cleanly (mirrors
            # the non-generic guard in _typecheck_call_args). Inference may
            # have element-extracted the unpack as evidence, so this gate is
            # what actually rejects the misuse.
            for arg in expr.args:
                if isinstance(arg, TpyStarUnpack):
                    raise self.ctx.error(
                        f"Cannot use *unpacking: '{func.name}' "
                        f"does not accept *args", arg)
            for i, ((pname, ptype), arg) in enumerate(zip(func.params, expr.args)):
                resolved_ptype = self.type_ops.substitute_type_params(ptype, type_subst)

                # @value_ptr_coercion: Ptr[T] params accept T values via address-of coercion.
                vpc_active = func.value_ptr_coercion and isinstance(resolved_ptype, PtrType)
                check_ptype = resolved_ptype.pointee if vpc_active else resolved_ptype

                arg_type = self.expr.analyze_expr_with_hint(arg, check_ptype)
                self._maybe_coerce_empty_list_to_protocol(arg_type, check_ptype)
                arg_type = self._restore_readonly_arg(arg, arg_type, func.is_readonly)

                # See the non-generic call site: a fresh Own[...] rvalue lent to
                # a borrow param is safe via codegen's named-temp materialization.
                if isinstance(arg_type, OwnType) and not isinstance(check_ptype, OwnType):
                    arg_type = arg_type.wrapped

                self.check_own_param(arg, arg_type, pname, check_ptype)

                if not (is_char_type(check_ptype) and is_any_str_type(arg_type) and
                        isinstance(arg, TpyStrLiteral) and len(arg.value) == 1):
                    coerced_arg = self.compat.coerce_expr(arg, arg_type, check_ptype, f"argument '{pname}'",
                                                           coercion_ctx=CoercionContext.ARG)
                    expr.args[i] = coerced_arg

                if vpc_active:
                    source = expr.args[i]
                    if not self.compat.is_mutable_lvalue(source):
                        raise self.ctx.error(
                            f"argument '{pname}' must be a variable, attribute, "
                            f"or container element (not a temporary or expression)", expr)
                    self.compat._mark_addr_taken(source)
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

        # Builtin generics (e.g. iter) delegate to concrete C++ that returns
        # std::optional<T>, not T*, despite the template-committed signature.
        if func.is_builtin_function and func.type_params:
            resolved_return = strip_template_repr(resolved_return)

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

    def _analyze_recursive_union_constructor(
        self, expr: TpyCall, union_type: UnionType,
    ) -> TpyType:
        """Analyze Tree(value) -- wrapping a value in a recursive union type."""
        name = expr.func_name
        self._reject_kwargs_for_builtin(expr, name)
        if len(expr.args) != 1:
            raise self.ctx.error(
                f"Recursive union constructor '{name}' takes exactly 1 argument, "
                f"got {len(expr.args)}",
                expr,
            )
        arg = expr.args[0]
        arg_type = self.expr.analyze_expr(arg)
        # The argument must be compatible with one of the union members
        # (check_type_compatible raises SemanticError on incompatibility)
        self.compat.check_type_compatible(arg_type, union_type, "argument 'value'", expr)
        expr.resolved_function_info = FunctionInfo(
            name=name,
            params=[("value", union_type)],
            return_type=union_type,
        )
        return union_type

    def _analyze_record_constructor(self, expr: TpyCall, record: RecordInfo) -> TpyType:
        """Analyze a call to a record constructor."""
        # `Any(value)` is constructor sugar for the INTO_ANY coercion.
        # CPython's `typing.Any(x)` raises TypeError, but TPy has a real
        # runtime Any wrapper (`tpy::Any`) and `make_any<T>` factory --
        # treating Any() as inline construction lets users write
        # `[Any(p), Any(q)]` without typed intermediates. The arg flows
        # through the same INTO_ANY path used for annotated targets.
        if record.qualified_name() == "typing.Any":
            return self._analyze_any_construct(expr)
        # Skip re-analysis for already-analyzed synthetic constructor calls
        cached = self.ctx.get_expr_type(expr)
        if cached is not None:
            return cached
        # Normalize expr.call_type to carry `_module_qname` so that the result
        # type flowing back to the caller is TypeDef-resolvable (Post-Phase-D
        # invariant #1). Parser emits bare NominalType("Dog") for `Dog()`.
        if (isinstance(expr.call_type, NominalType)
                and not expr.call_type._module_qname
                and not expr.call_type.is_protocol):
            expr.call_type = NominalType(
                expr.call_type.name, expr.call_type.type_args,
                expr.call_type.is_protocol, record.qualified_name(),
                expr.call_type.is_dynamic_protocol,
            )
        # Inside an @auto_readonly overload, a construction whose explicit type
        # args carry an `auto_readonly[...]` marker (e.g. `Rc[auto_readonly[T]]
        # (...)`) resolves that marker per the overload's polarity -- the
        # mutable half builds `Rc[T]`, the const half `Rc[readonly[T]]` -- so a
        # single shared body produces the matching handle without relying on
        # type-param inference binding to a readonly type (see BUGS.md).
        cur_fn = self.ctx.func.current_function
        polarity = getattr(cur_fn, "auto_readonly_polarity", None)
        if (polarity is not None
                and isinstance(expr.call_type, NominalType)
                and expr.call_type.type_args):
            if polarity == "strip":
                transform = strip_auto_readonly
            else:
                assert polarity == "apply", f"unexpected auto_readonly_polarity {polarity!r}"
                transform = apply_auto_readonly
            new_args = tuple(transform(a) for a in expr.call_type.type_args)
            if new_args != expr.call_type.type_args:
                expr.call_type = NominalType(
                    expr.call_type.name, new_args,
                    expr.call_type.is_protocol, expr.call_type._module_qname,
                    expr.call_type.is_dynamic_protocol,
                )

        # Only cpp_template / @native ctors need the template path (cast/numeric/
        # view lowering); a struct-like @builtin_type ctor takes the normal
        # record path below, which has the full arg-coercion surface.
        if record.builtin_type_key and not record.type_params:
            init_overloads = record.get_method_overloads("__init__")
            if init_overloads and any(
                    o.cpp_template or o.native_function or o.is_native
                    for o in init_overloads):
                return self._analyze_template_constructor(expr, record, init_overloads)

        # TypedDict: keyword-only construction (matches CPython)
        if record.is_typed_dict and expr.args:
            raise self.ctx.error(
                f"TypedDict '{record.name}' only accepts keyword arguments", expr)

        # Resolve kwargs for record constructors
        if expr.kwargs:
            if record.init_params:
                self._resolve_call_kwargs_init(expr, record)
            else:
                first_kwarg = next(iter(expr.kwargs))
                raise self.ctx.error(
                    f"'{record.name}()' got unexpected keyword argument '{first_kwarg}'", expr)

        # Check if this is a generic record instantiation (e.g., Stack[Int32]())
        if expr.call_type is not None and isinstance(expr.call_type, NominalType) and expr.call_type.is_record:
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
        if expr.call_type is not None and isinstance(expr.call_type, NominalType) and expr.call_type.is_record:
            # Analyze and type-check constructor arguments with type substitution
            type_subst = self.type_ops.build_type_substitution(expr.call_type)
            if record.init_params:
                # Type-check constructor arguments (from __init__ or
                # synthesized from fields for native records)
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
            elif expr.args:
                raise self.ctx.error(
                    f"'{record.name}' has no __init__ and cannot be constructed with arguments",
                    expr)
            elif not record.has_init:
                self._validate_aggregate_zero_arg(record, expr, type_subst)
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
                    (isinstance(self.ctx.func.current_function, TpyFunction) and self.ctx.func.current_function.type_params)
                    or self.ctx.record_ctx.type_params
                )
                for type_arg in expr.type_args:
                    if type_arg is not None:
                        if is_protocol_type(type_arg):
                            raise self.ctx.error(
                                f"Protocol type '{type_arg.name}' cannot be used as a type argument",
                                expr)
                        if isinstance(type_arg, NominalType) and type_arg.is_record and not type_arg.type_args:
                            if self.ctx.registry.get_record_for_type(type_arg) is None:
                                raise self.ctx.error(f"Unknown type: {type_arg.name}", expr)
                        self.type_ops.validate_type(
                            type_arg, allow_type_param_ref=in_generic,
                            loc=expr.loc, allow_forward_ref=False)
                wildcard_type_args = expr.type_args
            if record.has_init:
                # Arity check up-front so the diagnostic matches what the
                # function-call path emits (`expects N got M`) instead of
                # the inscrutable 'Cannot infer type arguments' that
                # `infer_type_params_for_record` returns on arity mismatch.
                min_args = _init_params_min_args(record.init_params)
                max_args = len(record.init_params)
                if len(expr.args) < min_args or len(expr.args) > max_args:
                    raise self.ctx.error(
                        arity_error_msg(f"{record.name}()", min_args, max_args, len(expr.args)),
                        expr
                    )

                # Seed type-param bindings from the LHS hint vs the record
                # pattern so nested generic-constructor chains see a
                # contextual hint on their first analysis pass -- symmetric
                # to the function/method-call seeding in
                # `_analyze_generic_function_call`. Wildcard explicit type
                # args overlay the seed at their positions; concrete explicit
                # type args take priority over both.
                seed_subst = self.type_ops.seed_subst_from_record_pattern(
                    record, self.ctx.expr_type_hint
                )
                # Always copy so the loop below can't accidentally mutate the
                # dict that ``seed_subst_from_record_pattern`` returned;
                # mirrors the function-call path at line 4247.
                merged_seed = dict(seed_subst)
                if wildcard_type_args:
                    for tp, ta in zip(record.type_params, wildcard_type_args):
                        if ta is not None:
                            merged_seed[tp] = ta

                arg_types: list[TpyType] = []
                for i, arg in enumerate(expr.args):
                    hint: TpyType | None = None
                    if i < len(record.init_params):
                        _, ptype, _ = record.init_params[i]
                        hint = post_substitute_hint(unwrap_ref_type(ptype), merged_seed)
                    if hint is not None:
                        arg_types.append(self.expr.analyze_expr_with_hint(arg, hint))
                    else:
                        arg_types.append(self.expr.analyze_expr(arg))

                inferred = self.type_ops.infer_type_params_for_record(
                    record, arg_types, expected_type=self.ctx.expr_type_hint,
                    explicit_type_args=wildcard_type_args,
                )
                if inferred:
                    for k, v in list(inferred.items()):
                        inferred[k] = self._resolve_inferred_type_arg(v)
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
                    inferred_type = NominalType(expr.func_name, type_args,
                                                _module_qname=record.qualified_name())
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
                if expr.args:
                    raise self.ctx.error(
                        f"'{record.name}' has no __init__; "
                        f"use @dataclass or define __init__ to accept constructor arguments",
                        expr)
                # No __init__ -- try contextual inference only
                if self.ctx.expr_type_hint is not None or wildcard_type_args is not None:
                    inferred: dict[str, TpyType] = {}
                    if wildcard_type_args:
                        for tp, arg in zip(record.type_params, wildcard_type_args):
                            if arg is not None:
                                inferred[tp] = arg
                    # Route LHS-hint matching through the shared helper so
                    # the no-__init__ branch uses the same qualifier-strip
                    # (Own/Readonly/Ref via unwrap_qualifiers) and useful-
                    # binding filter as the has_init=True seed path.
                    # Explicit wildcard args (already in `inferred`) take
                    # precedence over the LHS-derived seed at their slots.
                    seeded = self.type_ops.seed_subst_from_record_pattern(
                        record, self.ctx.expr_type_hint
                    )
                    for tp, val in seeded.items():
                        inferred.setdefault(tp, val)
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
                        inferred_type = NominalType(expr.func_name, type_args,
                                                    _module_qname=record.qualified_name())
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
        if record.init_params:
            # Type-check constructor arguments (from __init__ or
            # synthesized from fields for native records)
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
        elif expr.args:
            raise self.ctx.error(
                f"'{record.name}' has no __init__; "
                f"use @dataclass or define __init__ to accept constructor arguments",
                expr)
        elif not record.has_init:
            self._validate_aggregate_zero_arg(record, expr, type_subst=None)
        # NominalType.name is the canonical declaration name (qname disambiguates
        # cross-module same-name records); an import alias here would make the
        # constructed type's `name` differ from a union member's canonical name
        # and break `==`-based identity. Codegen qualifies via the qname index,
        # not this name, so the alias is not needed for C++ rendering.
        result_type = NominalType(record.name, _module_qname=record.qualified_name())
        self._set_record_constructor_info(expr, record, result_type)
        return result_type

    def _validate_aggregate_zero_arg(
        self, record: RecordInfo, expr: TpyCall,
        type_subst: dict[str, TpyType] | None,
    ) -> None:
        """Reject zero-arg `Record()` for an aggregate (no __init__) when the
        record's default ctor is suppressed by codegen, or when any own field
        or parent isn't default-constructible.

        Mirrors `protocols.py::_is_default_constructible` so drift here would
        re-introduce the confusing C++ "implicitly deleted" error chain this
        helper exists to prevent.
        """
        if del_suppresses_default_ctor(record):
            raise self.ctx.error(
                f"'{record.name}()' cannot be constructed without arguments: "
                f"'{record.name}' has __del__ and would read indeterminate "
                f"field state after default-initialization "
                f"(provide an __init__ or an in-class field default)",
                expr)
        def check(typ: TpyType, label: str) -> None:
            resolved = self.type_ops.substitute_type_params(typ, type_subst) if type_subst else typ
            if contains_type_param(resolved):
                return
            if not self.protocols._is_default_constructible(resolved):
                raise self.ctx.error(
                    f"'{record.name}()' cannot be constructed without arguments: "
                    f"{label} of type '{resolved}' has no default value",
                    expr)
        for parent in record.parents:
            check(parent, f"parent '{parent}'")
        for fld in record.fields:
            # A field with an explicit constant default never blocks
            # zero-arg construction: codegen emits it as an in-class
            # initializer, so the field type's own default-constructibility
            # is irrelevant.
            if fld.default_expr is not None:
                continue
            check(fld.type, f"field '{fld.name}'")

    def _can_defer_generic_inference(self, record: RecordInfo, expr: TpyCall) -> bool:
        """Check whether a generic constructor can use deferred type inference."""
        # Only in function bodies (resolve_all runs there)
        if not isinstance(self.ctx.func.current_function, TpyFunction):
            return False
        # Not inside a class body (field types must be concrete)
        if self.ctx.record_ctx.record is not None:
            return False
        # Check constructor arity (args must be valid count, ignoring type constraints)
        if record.init_params:
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
        if record.init_params:
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
        # Seed from constructor args
        if record.init_params and expr.args:
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
        self.ctx.func.pending_generic_instances[instance_id] = info
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
        callee_type = unwrap_send_sync(callee_type)
        if is_fn_type(callee_type):
            return self._analyze_fn_type_call(expr, callee_type)
        if isinstance(callee_type, CallableType):
            return self._analyze_callable_type_call(expr, callee_type)
        if isinstance(callee_type, NominalType):
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

    def _analyze_fn_type_call(self, expr: TpyCall, fn_type: CallableType) -> TpyType:
        """Analyze a call to a variable of Fn type."""
        label = expr.func_name if isinstance(expr.func, TpyName) else "<expr>"
        return self.analyze_callable_value_call(expr, fn_type, label, "Fn type")

    def _analyze_callable_type_call(self, expr: TpyCall, callable_type: CallableType) -> TpyType:
        """Analyze a call to a variable of Callable type."""
        label = expr.func_name if isinstance(expr.func, TpyName) else "<expr>"
        return self.analyze_callable_value_call(expr, callable_type, label, "Callable type")

    def analyze_callable_value_call(
        self, expr: TpyCall | TpyMethodCall, callable_type: CallableType,
        label: str, desc: str,
    ) -> TpyType:
        """Shared pipeline for calls through callable VALUES -- Fn/Callable
        typed params, locals, and fields.

        The callee body is unknown to the compiler, so the synthetic
        FunctionInfo is an opaque, potentially-mutating callee: args run the
        same typecheck+coerce pipeline as direct calls (the coercion must be
        applied, not just checked), and reference args bound to non-readonly
        callable params are conservatively marked mutated. A callable value
        has no body the call-graph can reach, so it never gains Phase-2
        mutation facts; the eager marks below stand in for the mutation call
        edges a direct call would record.
        """
        param_types = callable_type.param_types
        return_type = callable_type.return_type
        if expr.kwargs:
            raise self.ctx.error(
                f"Keyword arguments are not supported for {desc} "
                f"(Callable/Fn types have no parameter names)", expr)
        if len(expr.args) != len(param_types):
            raise self.ctx.error(
                f"{desc} expects {len(param_types)} argument(s), "
                f"got {len(expr.args)}",
                expr
            )
        for i, ptype in enumerate(param_types):
            expr.args[i] = self._typecheck_and_coerce_arg(
                expr.args[i], f"arg{i + 1}", ptype, func_is_readonly=False)
        expr.resolved_function_info = FunctionInfo(
            name=label,
            params=[ParamInfo(f"arg{i + 1}", t) for i, t in enumerate(param_types)],
            return_type=return_type,
            is_readonly=False,
        )
        self._check_borrow_arg_conflicts(expr)
        self._check_loop_var_arg_mutation(expr)
        for i, ptype in enumerate(param_types):
            if i >= len(expr.args):
                break
            if isinstance(ptype, ReadonlyType):
                continue
            if not param_has_mutable_borrow_surface(ptype):
                continue
            # Bare generic slots skip only the EAGER mutation mark (mirrors
            # the tuple-slot rule in param_has_mutable_borrow_surface): a
            # value-typed instantiation cannot mutate, and marking would cost
            # const-ness on every generic combinator (`g(f, xs)` forwarding
            # elements into f). The borrow-conflict check above is NOT skipped
            # -- a live element borrow passed to a generic slot still warns.
            if isinstance(unwrap_readonly(unwrap_ref_type(ptype)), TypeParamRef):
                continue
            arg_root = _root_name_of_expr(expr.args[i])
            if arg_root is not None:
                self.ctx.mark_param_mutated(arg_root, through_field=True)
                self.ctx.mark_param_structurally_mutated(arg_root)
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

        # The hint for the whole call expression is the slot type the macro
        # result lowers into (CallMacroContext.expected_type). Capture it, then
        # clear it during arg analysis so it can't leak into arg sub-analysis.
        expected_type = self.ctx.expr_type_hint
        self.ctx.expr_type_hint = None
        try:
            macro_args = []
            for a in args:
                arg_type = self.expr.analyze_expr(a)
                fstring_parts = None
                if isinstance(a, TpyFString):
                    fstring_parts = self._build_fstring_parts(a)
                macro_args.append(MacroArg(
                    expr=a,
                    type=TypeInfo.from_tpy_type(arg_type),
                    _fstring_parts=fstring_parts,
                ))
            macro_kwargs = {}
            for k, v in kwargs.items():
                kwarg_type = self.expr.analyze_expr(v)
                kw_fstring_parts = None
                if isinstance(v, TpyFString):
                    kw_fstring_parts = self._build_fstring_parts(v)
                macro_kwargs[k] = MacroArg(
                    expr=v,
                    type=TypeInfo.from_tpy_type(kwarg_type),
                    _fstring_parts=kw_fstring_parts,
                )
        finally:
            self.ctx.expr_type_hint = expected_type
        ctx = CallMacroContext(self.ctx, loc=loc, expected_type=expected_type)
        qname = f"{module_name}.{func_name}"
        expansion = expand_call_macro(
            macro_fn, ctx, macro_args, macro_kwargs, qname, loc)
        return expansion, self.expr.analyze_expr(expansion)

    def _build_fstring_parts(self, fstr: TpyFString) -> list[MacroFStringPart]:
        """Pre-compute typed parts for an f-string macro argument.

        Each expression in the f-string has already been analyzed by sema,
        so we look up its type from the expression type cache.
        """
        parts: list[MacroFStringPart] = []
        for part in fstr.parts:
            if isinstance(part, TpyFStringValue):
                expr_type = self.ctx.get_expr_type(part.expr)
                if expr_type is None:
                    expr_type = self.expr.analyze_expr(part.expr)
                parts.append(MacroFStringPart(
                    expr=part.expr,
                    type=TypeInfo.from_tpy_type(expr_type),
                    format_spec=part.format_spec,
                    conversion=part.conversion,
                    is_static_str=_is_static_str(part.expr, self.ctx),
                ))
        return parts

    def _ensure_call_macro_deps(self, module_name: str) -> None:
        """Register macro dep modules so qualified calls (mod.func) resolve.

        Binds the module name in macro_ns with BindingKind.MODULE. Individual
        function/record names are NOT injected -- macros should use qualified
        calls (e.g. ast.method_call(ast.name("mod"), "func", args)) to avoid
        leaking names into user code.
        """
        macro_reg = self.ctx.macro_registry
        if macro_reg is None:
            return
        deps = macro_reg.get_deps(module_name)
        if not deps:
            return
        for dep_mod, _ in deps.items():
            if dep_mod in self.ctx.macro_dep_modules:
                continue
            self.ctx.macro_dep_modules.add(dep_mod)
            module_info = self.ctx.registry.get_module(dep_mod)
            if module_info is None:
                continue
            # Bind the module so qualified calls resolve.
            # For flat modules: bind the name directly (log_infra -> log_infra).
            # For dotted modules (e.g. "mylog.infra"): bind the root segment
            # ("mylog") so ast.name("mylog.infra").func() resolves via the
            # dotted-module chain in _try_resolve_dotted_module.
            root = dep_mod.split(".")[0]
            self.ctx.macro_ns.bind_module(dep_mod, alias=root)

    def _resolve_call_macro_chain(
        self, module_name: str, name: str,
    ) -> tuple[str, str] | None:
        """Walk the binding chain to find the ultimate call-macro
        source for `(module_name, name)`. Returns None when no hop
        lands on a CALL_MACRO binding.
        """
        if self.ctx.macro_registry is None:
            return None
        result = walk_attribute_chain(
            self.ctx.registry, module_name, name, is_kind(SymbolKind.CALL_MACRO))
        if result is None:
            return None
        ult_mod, ult_name, _bd = result
        return (ult_mod, ult_name)

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
