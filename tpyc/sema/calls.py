"""
TurboPython Call Analysis

Function and constructor call analysis.
"""

from __future__ import annotations
from contextlib import contextmanager
from dataclasses import dataclass, replace as dc_replace
from typing import Callable, Iterable, Iterator, Mapping, NamedTuple, NoReturn, TYPE_CHECKING

from ..identity_map import IdentitySet
from ..compilation_context import require_current_compiler

from ..typesys import (
    make_readonly, substitute_cpp_ret, bound_as_spelled,
    declares_no_writes, param_may_be_written,
    default_emittable_at,
    TpyType, NominalType, AliasRef, OwnType, OptionalType, TupleType, param_takes_ownership, strip_template_repr, make_list, PendingListType, PendingViewType, PendingNumType, make_copy_iter, make_own_iter,
    is_polymorphic_class_type, is_dynamic_dispatch_inner, polymorphic_source_inner,
    IntLiteralType, resolve_int_literals,
    LiteralType, LiteralValue, LiteralTag, ListLiteralInfo, FunctionInfo, RecordInfo, TypeParamRef,
    PtrType, is_readonly_ptr, VoidType, is_void_like_type, ParamInfo, ReadonlyType,
    strip_auto_readonly, apply_auto_readonly,
    UNKNOWN_ELEMENT, UnknownElementType, PendingDictType, DictLiteralInfo, PendingSetType, SetLiteralInfo,
    UnionType, VOID, BIGINT, BOOL, STR, INT32, AnyType, ANY, is_protocol_type, unwrap_readonly, unwrap_own, unwrap_optional_own, make_union, ensure_qualified,
    ConcreteCoroType,
    is_any_str_type, container_to_str_template, error_return_matches,
    is_protocol_union, protocol_union_protocols,
    MutationCallEdge,
    PendingGenericInstanceType, PendingGenericInstanceInfo,
    CallableType, is_fn_type, unwrap_ref_type,
    is_integer_type, is_any_int_type,
    is_callable_type, is_float_type, is_any_float_type, is_readonly_span, varargs_is_readonly, unwrap_qualifiers,
    PendingContainerType,
    unwrap_send_sync,
    param_has_mutable_borrow_surface, contains_type_param,
    del_suppresses_default_ctor, owned_tuple_storage_type,
    is_bodyless_binding, recorded_return_borrow_sources)
from ..parse import (
    ResultForm,
    TpyCall, TpyCallLike, TpyMethodCall, TpyFieldAccess, TpyStrLiteral, TpyName, TpyFunction, TpyExpr,
    TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral, TpyNoneLiteral, TpyUnaryOp,
    TpyBinOp, TpyTupleLiteral, TpyTypeParamConstruct, TpyCoerce, TpyLambda,
    TpyDictLiteral, TpySetLiteral, lambda_of,
    TpyVarargPack, TpyStarUnpack, TpyFString, TpyFStringValue,
    TpyGeneratorExpression, TpyStmt, TupleElemCapture,
    TpyArrayLiteral, TpyListRepeat, TpyListComprehension,
    TpyDictComprehension, TpySetComprehension, TpyNamedExpr,
)
from ..prescan import isinstance_narrowing_key
from ..modules import extract_type_params
from ..namespace import BindingKind, NameBinding
from ..coercions import CoercionContext, VALUE_TO_PTR
from ..symbol_binding import (SymbolKind, is_kind, lookup_imported,
                              walk_attribute_chain)
from .arg_binding import (
    ArgBinding, BindReject, CallSource, Candidate, bind, candidate_of,
    committed, adopt_temp_call, positional_param_index, relaid_copy,
    relayout, source_view, temp_call_args,
)
from .call_lends import (
    LendSource, MayLend, borrow_declared_sources,
    call_lend_sources, escaping_borrow_writes, expr_lends_storage,
    proven_lend_roots,
)
from .context import (GenericCallRoute, GenericCtorRoute, OpenCallArgs,
                      PENDING_CONTAINER_TYPES, ProbeSlot)
from ..parse.nodes import DefaultReferent, name_path
from .literal_utils import is_char_literal_init
from ..diagnostics import SemanticError
from ..value_category import (declared_result_is_value, frame_factory_callee,
                              is_iterator_protocol,
                              is_rvalue_source, peel_coerce)
from .own_copy import (KIND_SLOT, contains_reference_type, copy_result_under_write,
                       lost_write_message,
                       slot_message, value_call_copy_message)
from .iter_loans import IterElementSource, iter_element_source, temp_arg_spelling
from .overloads import (
    type_matches_numeric,
    OverloadAmbiguityError,
    OverloadCandidate, OverloadPick, rank_overload_candidates,
    call_resolves_param, call_resolves_params,
    CallRanking, CallShape, call_ranking, pins_const_receiver,
    declared_readonly, shape_of, structural_matches, bound_evidence,
    operand_param_type, typed_source, filter_receiver_overloads,
)
from .list_elem import without_mutable_twins
from .context import CallOperands
from .int_narrowing import proven_cast, same_type_conversion, site_value_range
from .storage_walk import (Ask, MarkRequest, StorageKey, Use, mark_roots,
                           path_key, pointee_root, receiver_leaves,
                           storage_roots)
from .receiver_calls import (call_mutates_receiver,
                             credit_implicit_receiver_call,
                             record_receiver_edges,
                             record_truth_calls)
from .narrowing import truthy_operands
from .match import peel_match_qualifiers
from .scope_tracker import lend_places
from .alias_rebind import owned_slot_paths
from .compatibility import InferredSlot, TupleSink
from .protocols import dynamic_dispatch_type_conforms, handle_payload_may_be
from .dyn_erasure import erased_source
from .type_ops import (ReturnSeed, _fills_none_arm, partial_substitute,
                       seeded_arg_hint)
from .expressions import star_source_element_type
from .slot_hint import SlotHint
from .pending_num import (literal_entries, pending_leaves, receives_pending,
                          strip_int, value_family)
from .type_join import user_type_name
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
    from .pending_num import PendingNums
    from .value_range import ValueRange
    from ..parse.nodes import SourceLocation

from tpyc import modules as builtin_modules
from ..modules import _resolve_concrete_type_name
from .. import qnames
from ..type_def_registry import (
    get_type_def, is_array, is_span, is_varargs, is_list, is_borrowing_view_type,
    is_fixed_int_type, is_bool_type, is_char_type,
    is_str_type, is_big_int_type,
    int_traits_of,
    is_enum_type, enum_info_of, protocol_info_of,
    TypeDef, find_factory_by_simple_name,
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
    return [_enriched_literal(t, a) for t, a in zip(arg_types, args)]


def _enriched_literal(arg_t: TpyType, arg: 'TpyExpr | None') -> TpyType:
    if is_str_type(arg_t) and isinstance(arg, TpyStrLiteral):
        return LiteralType(STR, (LiteralValue(LiteralTag.STR, arg.value),))
    if isinstance(arg_t, IntLiteralType) and arg_t.value is not None:
        return LiteralType(INT32, (LiteralValue(LiteralTag.INT, arg_t.value),))
    if is_bool_type(arg_t) and isinstance(arg, TpyBoolLiteral):
        return LiteralType(BOOL, (LiteralValue(LiteralTag.BOOL, arg.value),))
    return arg_t


def enriched_source(typed: CallSource, src: CallSource,
                    candidates: list[FunctionInfo]) -> CallSource:
    """`_enrich_literal_types` over every operand of a call written with
    `src` whose types are `typed`, keywords included."""
    if not any(isinstance(p.type, LiteralType)
               for c in candidates for p in c.params):
        return typed
    return typed.mapped(lambda at, t: _enriched_literal(t, src.at(at)))


def filled_default(default: TpyExpr, call_loc: 'SourceLocation | None',
                   ctx: 'SemanticContext') -> TpyExpr:
    """A fresh copy of the syntax of a resolved default (`resolve_default`)
    for a call's argument list, at the CALL's loc so an error points at the
    call. A leaf this scope names by the same referent keeps its spelling,
    the read every other spelling of that constant here takes; any other
    leaf -- shadowed here, or not named here at all -- is a stamped read of
    the referent (`TpyFieldAccess.default_fill`), so the value is the one
    the default names where it is written, as in CPython."""
    loc = call_loc if call_loc is not None else default.loc
    if isinstance(default, TpyName):
        ref = default.default_referent
        if ref is None or (ref.module_var is not None
                           and _scope_module_var(ctx, default.name)
                           == ref.module_var):
            return TpyName(default.name, loc=loc)
        if ref.module_var is not None:
            return _stamped_read(ref.module_var[1], loc,
                                 module_var_access=ref.module_var)
        return _stamped_read(default.name, loc,
                             class_constant_owner=ref.class_owner)
    if isinstance(default, TpyFieldAccess):
        stamped = (default.enum_member_of is not None
                   or default.class_constant_owner is not None
                   or default.module_var_access is not None)
        if stamped and not _scope_names_member(ctx, default):
            return _stamped_read(
                default.field, loc,
                module_var_access=default.module_var_access,
                class_constant_owner=default.class_constant_owner,
                enum_member_of=default.enum_member_of)
        return _clone_path(default, loc)
    if isinstance(default, TpyCall):
        return relaid_copy(default, lambda a: filled_default(a, loc, ctx),
                           func=_filled_callee(default.func, loc, ctx),
                           loc=loc)
    if isinstance(default, TpyUnaryOp):
        return dc_replace(default,
                          operand=filled_default(default.operand, loc, ctx),
                          loc=loc)
    return dc_replace(default, loc=loc)


def construct_type_param_defaults(args: list[TpyExpr], filled: list[int],
                                  callee: FunctionInfo,
                                  subst: 'dict[str, TpyType | int]',
                                  ctx: 'SemanticContext') -> None:
    """Rewrite each `T()` default filled at the positions `filled` of the
    `args` of a call to a binding (`emits_cpp_defaults` False) as the
    construction a caller writes for the class `callee` binds `T` to
    (`int32()`), dispatched on that class's binding as a filled constructor
    default is (`DefaultReferent.ctor`). A callee TPy declares takes the `T()` form
    itself (`T{}` at its instantiation); a `T` bound to anything but a plain
    class keeps it too (BUGS.md#native-default-union-container-fill-rejects)."""
    if callee.emits_cpp_defaults:
        return
    for i in filled:
        arg = args[i]
        if not isinstance(arg, TpyTypeParamConstruct):
            continue
        bound = subst.get(arg.param_name)
        if isinstance(bound, PendingViewType):
            # A str / bytes local whose storage is not decided yet binds the
            # class it holds, as inference reads it (`_infer_arg_types`).
            bound = bound.family.owned_type
        if not isinstance(bound, NominalType) or bound.type_args:
            continue
        record = ctx.registry.get_record_for_type(bound)
        if record is None:
            continue
        args[i] = TpyCall(class_callee(record, arg.loc), [], loc=arg.loc)


def class_callee(record: RecordInfo,
                 loc: 'SourceLocation | None') -> TpyName:
    """A callee naming `record` itself, dispatched on its class binding
    (`DefaultReferent.ctor`) under a spelling no scope can bind: a
    construction built for a class already resolved."""
    binding = NameBinding(BindingKind.RECORD, record.name, record_info=record)
    return TpyName(f"<{record.qualified_name()}>",
                   default_referent=DefaultReferent(ctor=binding), loc=loc)


def _filled_callee(func: TpyExpr, loc: 'SourceLocation | None',
                   ctx: 'SemanticContext') -> TpyExpr:
    """The callee of a filled default's constructor call: its name when this
    scope binds it to the same class (`class_of_binding`), as
    `filled_default` keeps a name leaf; else the binding of the class it
    names where the default is written (`DefaultReferent.ctor`), which
    `analyze_call` dispatches on as that scope would its name, under a
    spelling no scope can bind."""
    ref = func.default_referent if isinstance(func, TpyName) else None
    if ref is None or ref.ctor is None:
        return _clone_path(func, loc)
    here = ctx.name_read(func.name)
    there = ctx.class_of_binding(ref.ctor)
    if (here.comp is None and here.binding is not None
            and there is not None
            and ctx.class_of_binding(here.binding) is there):
        return TpyName(func.name, loc=loc)
    spelled = (ref.ctor.record_info.qualified_name()
               if ref.ctor.record_info is not None
               else ".".join(ref.ctor.import_source or (ref.ctor.name,)))
    return TpyName(f"<{spelled}>", default_referent=ref, loc=loc)


def _clone_path(expr: TpyExpr, loc: 'SourceLocation | None') -> TpyExpr:
    """A fresh copy of a dotted name chain, its stamps kept."""
    if isinstance(expr, TpyName):
        return TpyName(expr.name, loc=loc)
    if isinstance(expr, TpyFieldAccess):
        return dc_replace(expr, obj=_clone_path(expr.obj, loc), loc=loc)
    return dc_replace(expr, loc=loc)


def _stamped_read(field: str, loc: 'SourceLocation | None', *,
                  module_var_access: tuple[str, str] | None = None,
                  class_constant_owner: 'RecordInfo | None' = None,
                  enum_member_of: 'TpyType | None' = None) -> TpyFieldAccess:
    """A filled default's read of its referent, spelled by the stamp alone.
    The receiver is a placeholder no scope can bind (not an identifier)."""
    if module_var_access is not None:
        where = module_var_access[0]
    elif class_constant_owner is not None:
        where = class_constant_owner.qualified_name()
    else:
        where = str(enum_member_of)
    return TpyFieldAccess(
        obj=TpyName(f"<{where}>", loc=loc), field=field,
        module_var_access=module_var_access,
        class_constant_owner=class_constant_owner,
        enum_member_of=enum_member_of, default_fill=True, loc=loc)


def _scope_module_var(ctx: 'SemanticContext',
                      name: str) -> tuple[str, str] | None:
    """The module variable the name `name` reads in the scope being
    analyzed (`SemanticContext.name_read`, the resolution `_analyze_name`
    takes), by its defining module and name; None when that is anything
    but a module-level variable (a comprehension variable, a type
    parameter, a local, a parameter, a capture) or nothing."""
    read = ctx.name_read(name)
    if (read.comp is not None or read.binding is None
            or read.binding.kind != BindingKind.VARIABLE
            or read.binding is not ctx.global_ns.lookup_local(name)):
        return None
    imported = lookup_imported(ctx.module_attributes, name,
                               SymbolKind.VARIABLE)
    return imported if imported is not None else (ctx.cpp_module_name, name)


def _scope_names_member(ctx: 'SemanticContext',
                        default: TpyFieldAccess) -> bool:
    """Whether the dotted spelling of a resolved default reads the same
    referent in the scope being analyzed: the same module's variable, the
    same enum's member, the class constant of the same declaring class --
    resolved the way `_analyze_field_access` resolves it."""
    if default.module_var_access is not None:
        return (ctx.module_named_by(default.obj)
                == default.module_var_access[0])
    owner = ctx.type_ref(default.obj)
    if owner is None:
        return False
    if default.enum_member_of is not None:
        return (owner.kind == SymbolKind.ENUM
                and enum_info_of(owner.info)
                is enum_info_of(default.enum_member_of))
    return (owner.kind != SymbolKind.ENUM
            and ctx.registry.find_class_constant_owner(
                owner.info, default.field) is default.class_constant_owner)


def _tuple_call_message(expr: TpyCall) -> str:
    """Why `tuple(...)` is refused: a tuple's length is part of its type, so
    no call can build one from a value whose length is known only at run
    time."""
    if not expr.args:
        return ("tuple() is not supported; write () for an empty tuple")
    src = expr.args[0].name if isinstance(expr.args[0], TpyName) else "xs"
    return (
        f"tuple(...) cannot build a tuple from a sequence: a tuple has a "
        f"fixed number of elements, known at compile time. Keep a list for "
        f"variable-length data, or write the tuple out: ({src}[0], {src}[1])")


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


def _ctor_placement(ctor: FunctionInfo, written: CallSource,
                    name: str) -> list[tuple[int, int | str]] | None:
    """Where construction `written` (`source_view`) of the type a
    diagnostic calls `name` places each argument on `ctor`: (parameter
    index, written address) pairs, a `*args` element at the variadic
    parameter; None when it does not bind."""
    bound = bind(written, ctor.params, name)
    if isinstance(bound, BindReject):
        return None
    return bound.addresses()


def resolve_cpp_template_type_params(
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
        if result_type is not None:
            template = substitute_cpp_ret(template, result_type)
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
    # The argument types as written (`CallSource` of types), and as ranked
    # (a literal as its single-value `Literal`, `enriched_source`).
    typed: CallSource
    enriched: CallSource
    contextual_callable_used: bool = False
    first_contextual_error: SemanticError | None = None
    # The call's binding to the winner, as the ranking placed its
    # arguments (`OverloadPick.binding`).
    binding: ArgBinding | None = None
    # The winner bound to the call as written, with the substitution the
    # ranking instantiated it at: what the commit commits.
    candidate: Candidate | None = None

    @property
    def arg_types(self) -> list[TpyType]:
        """The positional argument types."""
        return list(self.typed.positional)

    @property
    def declared(self) -> FunctionInfo | None:
        """The winner as declared: the generic itself for an instantiated
        generic winner."""
        if self.matched_origin is not None:
            return self.matched_origin[0]
        return self.matched


@dataclass(frozen=True)
class SuppliedFnSlot:
    """An Fn-typed param of a candidate filled by a supplied (non-default) arg.

    Used to identify which candidates are "Fn-bearing-by-supplied-args"
    for regime selection in `_resolve_call_overloads`, and which arg
    expression maps to which Fn param slot for per-candidate trial.
    """
    param_index: int       # index in func.params
    arg_expr: TpyExpr      # the supplied arg expression
    at: int | str          # where it is written: a position, or its keyword


def supplied_fn_slots(binding: ArgBinding, src: CallSource,
                      func: FunctionInfo) -> list[SuppliedFnSlot]:
    """The callable-typed parameters of `func` the call written with `src`
    fills, where its binding places the arguments."""
    return [SuppliedFnSlot(i, src.at(at), at)
            for i, at in binding.addresses()
            if not func.params[i].is_variadic
            and _is_fn_slot_param(func.params[i].type)]


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
            return (isinstance(resolved_type, OptionalType)
                    or (isinstance(resolved_type, UnionType)
                        and resolved_type.has_none_member()))
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


class _RegionNotBuilt:
    """The `**kwargs` region of a call no earlier step built: its commit
    builds it (`CallAnalyzer.kwargs_region_arg`)."""


REGION_NOT_BUILT = _RegionNotBuilt()


class ArgLayout(NamedTuple):
    """A call's argument list laid out against its callee
    (`CallAnalyzer.lay_out_args`), the binding committed to it, and the
    parameters whose default it fills."""
    args: list[TpyExpr]
    binding: ArgBinding
    filled: tuple[int, ...]


def defaults_to_fill(binding: ArgBinding,
                     callee: FunctionInfo) -> tuple[int, ...]:
    """The parameters a call to its resolved `callee` leaves to their
    defaults (`binding`) that need an argument: a slot held by a later
    argument's position, and every slot through the last whose default has
    no C++ spelling (`default_emittable_at`: none of a binding's,
    `emits_cpp_defaults`) -- an emittable default after it needs no
    argument. None while a required parameter is left open: the arity
    check reports it."""
    open_slots = binding.omitted()
    if not open_slots:
        return ()
    params = callee.params
    slots = [(p.type, p.default_expr) for p in params]
    position = binding.positions
    needed = [i for i in open_slots
              if position[i] is not None
              or not default_emittable_at(slots, i, callee.is_method,
                                          callee.emits_cpp_defaults)]
    if not needed:
        return ()
    fill = tuple(i for i in open_slots if i <= needed[-1])
    if any(params[i].default_expr is None for i in fill):
        return ()
    return fill


def validate_generic_defaults(
    binding: ArgBinding,
    func: FunctionInfo,
    type_subst: dict[str, TpyType],
    type_ops: 'TypeOperations',
    error_fn,
    filled: tuple[int, ...] = (),
) -> None:
    """Validate the defaults of the parameters a call leaves to C++
    (`binding`, less the `filled` ones, which the call passes and checks as
    any argument at their slot) against the generic substitution."""
    for i in binding.omitted():
        if i in filled:
            continue
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


def resolve_inferred_type_arg(
    t: "TpyType | int", literal: Callable[[TpyType], TpyType],
) -> "TpyType | int":
    """Resolve literal/pending types in an inferred type arg for codegen,
    an int literal to the type `literal` gives it.

    Inferred values can be ints (integer-kind type params, e.g. N in
    Array[T, N]) -- those pass through unchanged.
    """
    if isinstance(t, int):
        return t
    if isinstance(t, PendingListType):
        return make_list(resolve_int_literals(t.element_type, literal))
    return resolve_int_literals(t, literal)


def validate_type_param_bounds(
    type_subst: dict[str, TpyType],
    bounds: dict[str, TpyType],
    func_name: str,
    satisfies_bound,
    error_fn,
    substitute,
    detail: str = "",
) -> None:
    """Validate that resolved type args satisfy their type parameter bounds
    (`type_param_bound_error`), raising `error_fn` of the message."""
    msg = type_param_bound_error(type_subst, bounds, func_name,
                                 satisfies_bound, substitute, detail)
    if msg is not None:
        raise error_fn(msg)


def type_param_bound_error(
    type_subst: dict[str, TpyType],
    bounds: dict[str, TpyType],
    func_name: str,
    satisfies_bound,
    substitute,
    detail: str = "",
) -> str | None:
    """Why a resolved type arg breaks its type parameter's bound, or None.

    A bound may name sibling type parameters (`[R, T: Proto[R]]`); `substitute`
    resolves them against `type_subst` before the conformance check so `T`'s
    bound is checked as `Proto[<resolved R>]`, not the raw `Proto[R]`.

    `detail` is appended to the message by a call site whose type args are
    INFERRED from the argument slots, where the reason the bound has to hold is
    not obvious from the source.
    """
    for param_name, type_arg in type_subst.items():
        if param_name in bounds:
            bound = substitute(bounds[param_name], type_subst)
            if not satisfies_bound(type_arg, bound):
                return (
                    f"Type argument '{type_arg}' does not satisfy bound '{bound}' "
                    f"for type parameter '{param_name}' of '{func_name}'"
                    f"{detail}"
                    f"{_send_sync_bound_detail(bound, type_arg)}")
    return None


def _send_sync_bound_detail(bound: TpyType, type_arg: TpyType) -> str:
    """Why-not chain appended to a failing `T: Send` / `T: Sync` bound."""
    qname = bound.qualified_name() if isinstance(bound, NominalType) else None
    if qname not in qnames.SEMA_ONLY_MARKER_PROTOCOLS:
        return ""
    send = qname == qnames.SEND
    chain = why_not_send(type_arg) if send else why_not_sync(type_arg)
    return f"\n{render_chain(chain, send)}" if chain is not None else ""


# The marker protocols a composite bound's failure note names, and what each
# asks of the type.
_MARKER_NOUNS = {
    qnames.VALUE_TYPE: "a value type",
    qnames.REFERENCE_TYPE: "a reference type",
}


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


def _is_genexpr_frame(fi: FunctionInfo) -> bool:
    """Whether `fi` is the frame function of a generator expression, whose
    params past the source are captures rather than parameters the user
    wrote."""
    declaration = fi.root.declaration
    return declaration is not None and declaration.is_genexpr


def _edges_reach_param(fi: FunctionInfo, idx: int) -> bool:
    """Whether a call `fi` makes hands on its param `idx` -- as an argument
    or as the receiver -- so that Phase 2 may still find it written where
    the direct facts do not."""
    return any(idx in edge.param_map.values() or edge.receiver_idx == idx
               for edge in fi.call_edges or ())


class _LentVerdict(NamedTuple):
    """What one lent argument of a borrow-declared call makes of its result
    (`CallAnalyzer._lent_operand_verdict`)."""
    lends: bool
    fresh: bool
    read_only: bool
    copies: bool


def _borrowed_container_passed_warning(storage: str, fi: FunctionInfo,
                                       param_idx: int) -> str:
    """The warning for a call that may invalidate references into a
    borrowed container it is handed; spelled once for the immediate and the
    Phase-2 verdict."""
    if _is_genexpr_frame(fi):
        return (f"Borrowed container '{storage}' is mutated by a generator "
                f"expression that captures it (may invalidate references)")
    param_name = fi.params[param_idx].name if param_idx < len(fi.params) else "?"
    return (f"Passing borrowed container '{storage}' to non-readonly parameter "
            f"'{param_name}' (function may invalidate references)")


def _probe_arg_hint(arg: TpyExpr,
                   hint: SlotHint | None) -> SlotHint | None:
    """The hint an overload probe analyzes `arg` under before the winner is
    known. A Callable hint is dropped for a name or a lambda: analyzing
    one against a hint marks the node (`is_function_ref`, the lambda's
    signature), which would outlive the probe and mislead codegen if a
    different overload wins. Lambdas at 2+ Fn-bearing candidates go
    through Regime C's per-candidate trial instead."""
    if (hint is not None
            and is_callable_type(unwrap_send_sync(hint.type))
            and isinstance(arg, (TpyName, TpyLambda))):
        return None
    return hint


def call_shape(call: 'TpyExpr | TpyStmt', args: list[TpyExpr]) -> CallShape:
    """The shape of `call` passing `args` positionally (a subscript's
    dunder call passes its key, and a store's value, so): those and the
    keywords it is written with (`source_view`)."""
    src = (source_view(call) if isinstance(call, (TpyCall, TpyMethodCall))
           else CallSource())
    return CallShape(len(args), tuple(k for k, _ in src.keywords),
                     src.double_star is not None)


def _is_fn_slot_param(ptype: TpyType) -> bool:
    """A parameter that takes a callable argument. `Send`/`Sync` are
    unwrapped because `Send[Callable[...]]` is a callable slot too."""
    return is_callable_type(unwrap_send_sync(unwrap_ref_type(ptype)))


def _held_instance_copy_text(payload: TpyType, call: str) -> str:
    """Why a value-shaped type (a tuple / Optional / union) is refused where
    `call` would hand it back by value: it holds a reference type -- or a
    type parameter that may be one -- which the by-value form copies where
    CPython hands back the object itself."""
    held = ("a reference type"
            if contains_reference_type(payload, open_params_as_values=True)
            else "a type parameter that may be a reference type")
    return f"'{payload}' holds {held}, which {call} would copy; not supported yet"


# Arguments whose type their context gives: analyzed after the arguments
# that narrow an overload set (`CallAnalyzer.probe_overload_args`).
_CONTEXT_TYPED = (TpyLambda, TpyArrayLiteral, TpyTupleLiteral, TpyDictLiteral,
                  TpySetLiteral, TpyListRepeat, TpyListComprehension,
                  TpyDictComprehension, TpySetComprehension)


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
        self.pend: PendingNums
        # Pending borrow checks deferred until Phase 2 resolves mutated_params
        self.pending_borrow_checks: list[tuple[FunctionInfo, int, str, SourceLocation | None]] = []
        # A borrow-declared call whose C++ result is a copy, passed to a
        # mutable-borrow parameter: the copy is observable only if the callee
        # writes the parameter, which Phase 2 settles.
        # Entry: (callee, param index, message, location, the value copied,
        # whose copy the warning declares).
        self.pending_argument_copies: list[tuple[FunctionInfo, int, str, SourceLocation | None, TpyExpr]] = []
        # A method called on a call's COPY result: an error once Phase 2
        # says the method writes its receiver. Entry: (callee, copy call, site).
        self.pending_copy_receiver_calls: list = []
        # Deferred match-arm subject-mutation checks: a method call on the
        # subject root/prefix whose readonly verdict (and thus whether it may
        # reassign the borrowed subject storage) only settles in Phase 2.
        # Entry: (method_call, subject_path_str, match arm).
        self.pending_match_subject_checks: list = []
        # Implicit dunder calls (`for x in g`, `g[k]`, `with g`, ...) on a
        # readonly receiver: whether the dunder may be called on a const
        # receiver is its emitted const-ness, final only once every module's
        # const inference has run. Entry: (callee FunctionInfo, dunder, site).
        self.pending_readonly_receiver_checks: list = []

    def resolve_template_placeholders(
            self, fi: FunctionInfo,
            subst: dict[str, TpyType] | None) -> FunctionInfo:
        """`fi` with its `@cpp_template` type placeholders resolved: `{T}`
        by the receiver's type argument (`subst`), `{cpp}` by its
        (substituted) return type."""
        return resolve_cpp_template_type_params(
            fi, subst or None, result_type=fi.return_type, ctx=self.ctx)

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
            return make_readonly(arg_type)
        return arg_type

    def resolve_pending_borrow_checks(self) -> None:
        """Emit or suppress deferred borrow warnings after Phase 2 propagation."""
        for fi, param_idx, storage, loc in self.pending_borrow_checks:
            # Use structural_mutated_params when available (precise); fall back to mutated_params.
            effective_mp = fi.structural_mutated_params if fi.structural_mutated_params is not None else fi.mutated_params
            if effective_mp is not None and param_idx not in effective_mp:
                continue
            self.ctx.warning_from_loc(
                _borrowed_container_passed_warning(storage, fi, param_idx),
                loc,
            )
        self.pending_borrow_checks.clear()

    def defer_argument_copies(self, call: TpyCall | TpyMethodCall) -> None:
        """An argument that is a borrow-declared call whose C++ result is a
        copy (`ResultForm.COPY`: `bump(max(walk(ps), key=f))`) is
        hoisted into a temporary, so a callee that writes the parameter
        writes the copy where CPython writes the element. Whether it writes
        is final only after Phase 2; a readonly parameter cannot. A frame
        callee reports its own copy (`_warn_frame_held_copies`)."""
        fi = call.resolved_function_info
        # `copy(...)` is the explicit spelling of that copy.
        if (fi is None or fi.is_async or frame_factory_callee(fi)
                or fi.qualified_name == qnames.COPY):
            return
        params = fi.params
        if committed(call):
            slots = call.binding.committed_evidence(call)
        else:
            # A call that commits no binding (`print`, `isinstance`, ...)
            # keeps its written layout.
            by_name = {p.name: i for i, p in enumerate(params)}
            slots = list(enumerate(call.args)) + [
                (by_name.get(k, -1), a) for k, a in (call.kwargs or {}).items()]
        for idx, arg in slots:
            inner = peel_coerce(arg)
            if not (isinstance(inner, TpyCallLike)
                    and inner.result_form is ResultForm.COPY):
                continue
            if not 0 <= idx < len(params):
                continue
            ptype = operand_param_type(params[idx], keep_readonly=True)
            if isinstance(ptype, ReadonlyType):
                continue
            callee = (call.func.name if isinstance(call, TpyCall)
                      and isinstance(call.func, TpyName) else fi.name)
            dest = f"{temp_arg_spelling(fi, idx)} of '{callee}(...)'"
            tuple_slot = unwrap_ref_type(ptype)
            if isinstance(tuple_slot, TupleType):
                # A tuple is a set of scalars: each leaf takes the check its
                # scalar twin's argument does, which the whole tuple's
                # mutable-borrow surface (no open `T` slot) does not answer.
                self.compat.warn_argument_tuple_copies(inner, tuple_slot,
                                                       dest)
                continue
            if not param_has_mutable_borrow_surface(ptype):
                continue
            found = value_call_copy_message(self.ctx, inner, dest)
            if found is not None:
                self.pending_argument_copies.append(
                    (fi, idx, found[2], getattr(inner, 'loc', None),
                     found[1]))

    def defer_copy_receiver_call(self, call: TpyMethodCall) -> None:
        """A method called on a call's COPY result (`max(it, key=f).inc()`)
        writes the copy if it writes its receiver, where CPython writes the
        element; whether it does is final after Phase 2."""
        fi = call.resolved_function_info
        if fi is None or call.is_callable_field:
            return
        lost = copy_result_under_write(call.obj)
        if lost is not None:
            self.pending_copy_receiver_calls.append((fi, lost, call))

    def resolve_pending_copy_receiver_calls(self) -> None:
        pending = self.pending_copy_receiver_calls[:]
        self.pending_copy_receiver_calls.clear()
        for fi, lost, site in pending:
            root = fi.root
            if fi.emitted_const:
                continue
            if root.direct_self_mutated is not None and not root.self_mutated:
                continue
            raise self.ctx.error(lost_write_message(lost), site)

    def resolve_pending_argument_copies(self) -> None:
        """Warn the deferred argument copies whose parameter the callee
        writes (or may: no facts)."""
        pending = sorted(
            self.pending_argument_copies,
            key=lambda e: ((e[3].line, e[3].column) if e[3] is not None
                           else (0, 0)))
        for fi, idx, message, loc, held in pending:
            if param_may_be_written(fi, idx):
                self.ctx.copy_warning(message, loc, held)
        self.pending_argument_copies.clear()

    def resolve_pending_match_subject_checks(self) -> None:
        """Emit the deferred match-arm dangle warning for a non-readonly method
        call on the subject root/prefix (it may reassign the borrowed subject
        storage). Readonly is now settled; warn once per arm. The sound fix
        (reject the mutation) waits for the IR loan model -- see BUGS.md."""
        warned_arms: IdentitySet = IdentitySet()
        for mcall, subj_str, arm in self.pending_match_subject_checks:
            if arm in warned_arms:
                continue
            fi = mcall.resolved_function_info
            if fi is not None and fi.is_readonly is False:
                warned_arms.add(arm)
                self.ctx.warning(
                    f"'{subj_str}' may be mutated by '{mcall.method}()' in this "
                    f"arm while pattern bindings borrow its storage; the "
                    f"bindings dangle (undefined behavior). Copy the bound "
                    f"values before the call", mcall)
        self.pending_match_subject_checks.clear()

    def resolve_pending_readonly_receiver_checks(self) -> None:
        """Reject a queued implicit dunder call on a readonly receiver whose
        callee is emitted non-const: not inferred readonly, or inferred but
        kept non-const (`FunctionInfo.const_withheld`)."""
        pending = self.pending_readonly_receiver_checks[:]
        self.pending_readonly_receiver_checks.clear()
        for callee, method, site in pending:
            if not callee.emitted_const:
                raise self.ctx.error(
                    f"Cannot call non-readonly method '{method}' on readonly reference",
                    site)

    def bind_args(self, call: 'TpyCallLike', params: list[ParamInfo],
                  name: str, src: CallSource | None = None) -> ArgBinding:
        """Bind what `call` was written with (`source_view`, or `src`) to
        `params` of the callee a diagnostic calls `name`; a call whose
        arguments do not bind is an error here."""
        result = bind(src if src is not None else source_view(call),
                      params, name)
        if isinstance(result, BindReject):
            raise self.ctx.error(result.message, call)
        return result

    def commit_args(self, call: 'TpyCallLike', callee: FunctionInfo,
                    binding: ArgBinding,
                    subst: 'dict[str, TpyType | int]', *,
                    analyze: bool, fill: bool = True,
                    kwargs_arg: TpyExpr | None = None,
                    exprs: 'dict[int, TpyExpr] | None' = None,
                    src: CallSource | None = None,
                    receive: 'Callable[[ArgLayout], None] | None' = None,
                    ) -> ArgLayout:
        """Lay `call`'s argument list out from `binding` -- bound to what
        the call was written with (`source_view`, or `src`) -- against its
        resolved `callee` at the type arguments `subst` (`lay_out_args`),
        and store it with the binding committed to it on the call.
        `receive`: the step that receives the laid-out operands in place,
        run on the detached layout before it is stored (`commit_winner`).
        `analyze`: the filled arguments are analyzed and received at their
        slots, for a call site whose written arguments are already.

        At a step that does not commit (`SemanticContext.slot_step_commits`)
        the call keeps what was written and no default is filled: the
        layout returned is a detached one the step receives against, and
        the step that commits lays the call out."""
        commits = self.step_commits(call)
        layout = self.lay_out_args(
            source_view(call) if src is None else src, callee, binding,
            subst, call.loc, fill=fill, filling=commits,
            kwargs_arg=kwargs_arg, exprs=exprs)
        if receive is not None:
            receive(layout)
        if not commits:
            return layout
        relayout(call, layout.args, layout.binding)
        filled = layout.filled
        if analyze and filled:
            params = callee.params
            at = call.binding.at
            received = [call.args[at[i]] for i in filled]
            self.receive_winner_args(None, received, None,
                                     [params[i] for i in filled], callee,
                                     call=call)
            for i, arg in zip(filled, received):
                call.args[at[i]] = arg
        return ArgLayout(call.args, call.binding, filled)

    def lay_out_args(self, src: CallSource, callee: FunctionInfo,
                     binding: ArgBinding,
                     subst: 'dict[str, TpyType | int]',
                     loc: 'SourceLocation | None', *, fill: bool,
                     filling: bool = True,
                     kwargs_arg: TpyExpr | None = None,
                     exprs: 'dict[int, TpyExpr] | None' = None) -> ArgLayout:
        """The argument list `binding` lays out of the call written as `src`
        to its resolved `callee` at the type arguments `subst`: each written
        argument in its parameter's slot (`exprs` overrides one already
        analyzed), the defaults filled (`fill_resolved_defaults`), the
        `*args` elements packed and the `**kwargs` region's TypedDict
        construction (`kwargs_arg`) last -- with the binding committed to
        it. With `fill` off -- a call that renders no C++ call -- the list
        holds the written arguments alone; with `filling` off it leaves out
        the defaults it would fill, and names them in `filled` still."""
        args = binding.layout(src)
        owners = binding.layout_owners()
        for i, e in (exprs or {}).items():
            if binding.positions[i] is not None:
                args[binding.positions[i]] = e
        if fill and filling:
            filled = self.fill_resolved_defaults(args, owners, binding,
                                                 callee, subst, loc)
        else:
            kept = [k for k, a in enumerate(args) if a is not None]
            args = [args[k] for k in kept]
            owners = [owners[k] for k in kept]
            filled = defaults_to_fill(binding, callee) if fill else ()
        va = binding.variadic
        if va is not None:
            # The `*args` elements are one operand, the pack, at the
            # variadic parameter's place.
            elements = [k for k, o in enumerate(owners) if o == va]
            if elements:
                start, end = elements[0], elements[-1] + 1
                assert elements == list(range(start, end))
            else:
                start = end = next((k for k, o in enumerate(owners)
                                    if o is not None and o > va), len(args))
            pack = TpyVarargPack(
                args=args[start:end],
                element_type=binding.vararg_element_type(callee.params),
                loc=loc)
            args[start:end] = [pack]
            owners[start:end] = [va]
        if kwargs_arg is not None:
            args.append(kwargs_arg)
            owners.append(binding.kwargs)
        assert None not in args
        at = [owners.index(i) if i in owners else None
              for i in range(len(binding.slots))]
        return ArgLayout(args, binding.committed(at), filled)

    def fill_resolved_defaults(self, args: 'list[TpyExpr | None]',
                               owners: list[int | None],
                               binding: ArgBinding, callee: FunctionInfo,
                               subst: 'dict[str, TpyType | int]',
                               loc: 'SourceLocation | None') -> tuple[int, ...]:
        """Fill, in the layout `args` (`ArgBinding.layout`, the parameter
        each position belongs to in `owners`), the parameters a call to its
        resolved `callee` leaves to their defaults that need an argument
        (`defaults_to_fill`). Runs once the callee and its type arguments
        are known -- `subst` binds them, at which a filled `T()`
        constructs. Returns the parameters filled."""
        fill = defaults_to_fill(binding, callee)
        if not fill:
            return ()
        position = binding.positions
        filled: list[int] = []
        for i in fill:
            arg = filled_default(callee.params[i].default_expr, loc, self.ctx)
            if position[i] is not None:
                args[position[i]] = arg
                filled.append(position[i])
            else:
                args.append(arg)
                owners.append(i)
                filled.append(len(args) - 1)
        construct_type_param_defaults(args, filled, callee, subst, self.ctx)
        return fill

    def fill_positional_call(self, call: TpyCallLike, callee: FunctionInfo,
                             subst: 'dict[str, TpyType | int]',
                             src: CallSource) -> None:
        """`commit_args` for a call whose arguments, `src`, are analyzed
        and received against its resolved `callee` already, bound to it
        here."""
        binding = self.bind_args(call, callee.params, callee.name, src=src)
        self.commit_args(call, callee, binding, subst, analyze=True, src=src)

    def kwargs_region_arg(self, call: TpyCallLike, callee: FunctionInfo,
                          binding: ArgBinding, name: str) -> TpyExpr | None:
        """The argument a call to `callee` passes its `**kwargs:
        Unpack[TD]` parameter: the `**expr` written, or a construction of
        the TypedDict from the keywords the binding routes to it (field
        defaults honored); None when `callee` has no such parameter. `name`
        is the callee as a diagnostic spells it."""
        if binding.kwargs is None:
            return None
        written = source_view(call)
        td_type = unwrap_ref_type(callee.params[binding.kwargs].type)
        if written.double_star is not None:
            unpack_type = unwrap_ref_type(
                self.expr.analyze_expr(written.double_star))
            self.compat.check_type_compatible(
                unpack_type, td_type,
                f"**kwargs unpacking (expected {td_type})",
                loc=call.loc,
            )
            return written.double_star
        record = self.ctx.registry.get_record_for_type(td_type)
        if record is None:
            raise self.ctx.error(
                f"**kwargs type '{td_type.name}' is not a TypedDict", call)
        return self._build_td_call(record, binding.kwargs_fields(written),
                                   f"{name}()", call)

    def _build_td_call(self, record: RecordInfo, td_kwargs: dict, func_name: str, expr) -> TpyCall:
        """Build a TypedDict constructor call from kwargs, with type-checking."""
        params = [ParamInfo(fld.name, fld.type, default_expr=fld.default_expr,
                            keyword_only=True) for fld in record.fields]
        td_type = NominalType(record.name, _module_qname=record.qualified_name())
        td_call = TpyCall(TpyName(record.name, loc=expr.loc), [], loc=expr.loc)
        binding = self.bind_args(
            expr, params, func_name,
            src=CallSource((), tuple(td_kwargs.items())))
        # The construction spells every field, so no default is left to C++;
        # a step that does not commit fills none (`commit_args`). It is a
        # node of the call's own, laid out whatever the step.
        layout = self.lay_out_args(
            CallSource((), tuple(td_kwargs.items())),
            FunctionInfo(name=record.name, params=params,
                         return_type=td_type, _emits_cpp_defaults=False),
            binding, {}, expr.loc, fill=True,
            filling=self.step_commits(expr))
        relayout(td_call, layout.args, layout.binding)
        for k, i in enumerate(layout.binding.param_order()):
            arg, fld = td_call.args[k], record.fields[i]
            arg_type = self.expr.analyze_at_slot(arg, fld.type,
                                                 CoercionContext.ARG)
            td_call.args[k] = self.compat.coerce_expr(
                arg, arg_type, fld.type, f"argument '{fld.name}'",
                coercion_ctx=CoercionContext.ARG, param=f"'{fld.name}'")
        self.ctx.set_expr_type(td_call, td_type)
        # Built analyzed: the receive meets it through its record.
        self.expr.note_analysis(td_call, td_type, None)
        return td_call

    def _resolve_inferred_type_arg(self, t: "TpyType | int") -> "TpyType | int":
        return resolve_inferred_type_arg(t, self.ctx.default_int_for_literal)

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

    def reject_keywords(self, expr: TpyCall, name: str) -> None:
        """Refuse keywords at a call the compiler analyzes itself rather
        than binding it to a stub (`isinstance`, `copy`, an enum lookup):
        it has no parameter a keyword could name."""
        written = source_view(expr)
        if written.keywords or written.double_star is not None:
            raise self.ctx.error(
                f"{name}() takes no keyword arguments", expr)

    def ctor_signatures(self, record: RecordInfo) -> list[FunctionInfo]:
        """The constructors `record(...)` binds against: its `__init__`
        overloads, own or inherited (`ctor_overloads`), else the one it
        builds from its fields (`RecordInfo.field_ctor`); none for a
        record with neither."""
        ctors = self.ctx.registry.ctor_overloads(record)
        if ctors:
            return ctors
        return [record.field_ctor] if record.field_ctor is not None else []

    def _ctor_view(self, record: RecordInfo, ctor: FunctionInfo,
                   type_subst: 'dict[str, TpyType | int]',
                   ) -> 'tuple[FunctionInfo, dict[str, TpyType | int]]':
        """Constructor `ctor` of `record` at a construction at the record's
        type arguments `type_subst` (`_ctor_at`); a field constructor is
        spelled in the record's own type parameters."""
        if ctor is record.field_ctor:
            subst = dict(type_subst)
            return ((self.type_ops.substitute_method_type_params(ctor, subst)
                     if subst else ctor), subst)
        return self._ctor_at(record, ctor, type_subst)

    def _own_ctor_view(self, record: RecordInfo,
                       ctor: FunctionInfo) -> FunctionInfo:
        """`ctor` spelled in `record`'s own type parameters, which it is
        generic over: the signature a construction's type arguments are
        inferred from."""
        own = {tp: TypeParamRef(tp) for tp in record.type_params}
        view, _ = self._ctor_view(record, ctor, own)
        return dc_replace(view, type_params=list(record.type_params),
                          type_param_bounds=dict(record.type_param_bounds),
                          return_type=self.type_ops.record_pattern(record))

    def _ctor_candidate(self, expr: TpyCall, record: RecordInfo,
                        ctor: FunctionInfo,
                        type_subst: 'dict[str, TpyType | int]',
                        binding: ArgBinding | None = None) -> Candidate:
        """Construction `expr` of `record` bound to its one constructor
        `ctor` at the record's type arguments `type_subst`; `binding`: the
        binding its type arguments were inferred by, whose parameters are
        `ctor`'s."""
        matched, subst = self._ctor_view(record, ctor, type_subst)
        written = source_view(expr)
        if binding is None:
            binding = self.bind_args(expr, matched.params,
                                     f"{record.name}()", src=written)
        self._refuse_varargs_ctor(expr, record, matched)
        return candidate_of(written, binding, matched, ctor, subst,
                            ctor if subst else None)

    def _refuse_varargs_ctor(self, expr: TpyCall, record: RecordInfo,
                             ctor: FunctionInfo) -> None:
        """A construction through an `__init__`'s `*args` parameter has no
        C++ rendering yet (BUGS.md#ctor-varargs-construction-unlowered)."""
        if ctor.has_variadic:
            raise self.ctx.error(
                f"constructing '{record.name}' through its *args parameter "
                f"is not supported yet", expr)

    def commit_construction(self, expr: TpyCall, record: RecordInfo,
                            cand: Candidate, *,
                            scope: OpenCallArgs | None = None,
                            kwargs_arg: 'TpyExpr | None | _RegionNotBuilt'
                            = REGION_NOT_BUILT) -> None:
        """Commit construction `expr` of `record` to its constructor
        candidate `cand` (`commit_winner`): every argument received at the
        constructor's parameter, as the callee declares it there."""
        declared = cand.declared
        own = (self._own_ctor_view(record, declared)
               if record.is_generic() and declared is not None else None)
        self.commit_winner(
            expr, cand, f"{record.name}()", scope=scope, kwargs_arg=kwargs_arg,
            declared_types=([p.type for p in own.params]
                            if own is not None else None))

    def _stamp_construction(
        self,
        expr: TpyCall,
        record: RecordInfo,
        return_type: TpyType,
        cand: Candidate | None,
        type_subst: 'dict[str, TpyType | int] | None' = None,
        commit: bool | None = None,
    ) -> None:
        """What construction `expr` of `record` resolves to: a constructor
        taking the parameters of the `__init__` it binds (`cand`), or none
        for a record without one; and the records a committed construction
        owns -- only where it `commit`s (by default
        `SemanticContext.slot_step_commits`; a construction completed at a
        use says, `complete_construction`). What its arguments pass is
        `_construction_facts`'."""
        if commit is None:
            commit = self.ctx.slot_step_commits()
        # The one funnel every constructor resolution reaches, and the only
        # place that sees a generic record's INFERRED args: `Box(1)` binds
        # `Box[int32]` here and nowhere else, so a program that only ever
        # constructs the record would otherwise leave its bodies' owning-slot
        # copy obligations unanswered (sema/own_copy.py).
        if type_subst and commit:
            self.ctx.record_own_copy_instantiation(record, type_subst,
                                                   is_record=True)
        has_init = (cand is not None and cand.declared is not None
                    and cand.declared is not record.field_ctor)
        expr.resolved_function_info = FunctionInfo(
            name=record.name,
            params=cand.matched.params if has_init else [],
            return_type=return_type,
            is_readonly=False,
            is_constructor=True,
            canonical_fi=cand.declared.root if has_init else None,
        )


    def _ctor_at(self, record: RecordInfo, ctor: FunctionInfo,
                 type_subst: 'dict[str, TpyType | int]',
                 ) -> 'tuple[FunctionInfo, dict[str, TpyType | int]]':
        """`ctor` -- `record`'s own `__init__` or the one it inherits from
        its init owner -- substituted to a construction of `record` at
        `type_subst`, and the substitution from the owner's type parameters
        that does it: a default spelled in the owner's parameters (`U()`)
        fills by that map, since a signature substitution leaves default
        expressions as written."""
        owner = self.ctx.registry.init_owner(record)
        subst = dict(type_subst)
        if owner is not None and owner is not record:
            _, owner_subst = self.protocols.resolve_ancestor_instantiation(
                record, owner)
            subst = self.protocols.ancestor_subst_at(owner_subst, type_subst)
        resolved = (self.type_ops.substitute_method_type_params(ctor, subst)
                    if subst else ctor)
        return resolved, subst

    def _callee_binding(self, expr: TpyCall) -> 'NameBinding | None':
        """What a call's callee name binds: in the scope being analyzed,
        or, for a filled parameter default's constructor, where the
        default is written (`DefaultReferent.ctor`) -- the one by-name
        dispatch of `analyze_call` serves both."""
        ref = expr.func.default_referent
        if ref is not None and ref.ctor is not None:
            return ref.ctor
        ns = self.ctx.func.current_ns
        return ns.lookup(expr.func_name) if ns is not None else None

    def _record_for_local_name(self, name: str,
                               binding: 'NameBinding | None' = None
                               ) -> 'RecordInfo | None':
        """The class a bare callee name denotes, resolved like every other
        type path (`type_ref_path`): its scoped binding (`binding`, when
        the caller looked it up), an import, or a builtin -- never another
        module's class that shares the short name. Outside its own module a
        generic `@builtin_type` class (`Span`, `list`) constructs through
        its type factory, which infers its type arguments."""
        ref = (self.ctx.type_ref_path([name]) if binding is None
               else self.ctx.type_ref_bound_path(binding, [name]))
        if ref is None or ref.kind != SymbolKind.RECORD:
            return None
        rec = ref.info
        if (rec.builtin_type_key and rec.type_params
                and rec.defining_module != self.ctx.module_name):
            return None
        return rec

    def stamp_result_borrow(self, expr: TpyCallLike,
                            result_type: TpyType) -> bool:
        """Decide, once per analysis, whether a call to a binding that
        DECLARES its result's borrow sources (`borrows=` / `element_of=`) binds as a
        borrow of its arguments or is a fresh value, and record it on the
        node (`TpyCallLike.result_form`, `copy_observable`). A method's
        receiver lends as the argument at index -1 does. Every
        reader of the call reads the stamp. Returns whether the result is
        READ-ONLY because what lends it is.

        The result IS a `borrows=` argument, or is handed out by iterating
        an `element_of=` one. It is a borrow exactly when every lent
        argument lends storage that outlives the call
        (`_lent_operand_verdict`); otherwise the C++ reference could dangle
        past the statement and the result is a value. Whether a sink holding
        that value copies something the program still reaches is recorded
        beside it: the sinks warn, the call does not, because a transient
        consumer (`min(a, P(0), key=f).v`) copies nothing. A value-shaped
        result (a number, a `str`, a tuple, an Optional) is a value at every
        call.

        Unless the C++ result is a copy (`ResultForm.COPY`), it is a
        reference into the operands for the statement at least, so a result
        that is not read-only is a mutable use of them."""
        expr.result_form = ResultForm.NOT_DECLARED
        expr.copy_observable = False
        fi = expr.resolved_function_info
        # Only a DECLARATION decides a form: a native method's receiver
        # borrow inferred from its signature keeps its own convention.
        if (fi is None or not is_bodyless_binding(fi)
                or not fi.root.borrow_declared):
            return False
        sources = recorded_return_borrow_sources(fi)
        if not sources:
            return False
        # An open `T` result (a generic body's call) is decided here too: a
        # hedged copy (the instantiation may be a reference type).
        open_result = isinstance(unwrap_qualifiers(result_type), TypeParamRef)
        if declared_result_is_value(fi) and not open_result:
            # A value-shaped result (an Optional / union / tuple) is handed
            # back by value. Holding only value payloads it copies nothing
            # CPython would share. Holding a class instance -- even beside
            # an open `T` -- it certainly copies the instance CPython hands
            # back, and its borrow form is not declared yet. Holding only
            # open `T`s as reference candidates it is the hedged copy a bare
            # open `T` result is: the instantiation may be a reference type.
            payload = unwrap_readonly(unwrap_ref_type(result_type))
            if not contains_reference_type(payload):
                expr.result_form = ResultForm.VALUE
                return False
            if contains_reference_type(payload, open_params_as_values=True):
                raise self.ctx.error(
                    _held_instance_copy_text(payload, expr.call_display),
                    expr)
            open_result = True
        lent = borrow_declared_sources(expr)
        if lent is None:
            return False
        verdicts = [self._lent_operand_verdict(fi, s.idx, s.expr) for s in lent]
        if open_result:
            # A generic body decides before instantiation, and a borrow of an
            # open `T` has no slot yet: the result is a COPY, held in the
            # hedged generic copy contract (TODO.md, "Per-instantiation
            # result form for a generic body").
            expr.result_form = ResultForm.COPY
        elif bool(lent) and all(v.lends for v in verdicts):
            expr.result_form = ResultForm.BORROW
        elif any(v.copies for v in verdicts):
            expr.result_form = ResultForm.COPY
        else:
            expr.result_form = ResultForm.REFERENCE_VALUE
        expr.copy_observable = (expr.result_form.is_fresh
                                and not all(v.fresh for v in verdicts))
        if expr.result_form is ResultForm.COPY:
            # The call itself makes the copy: there is none of a
            # non-copyable element to make.
            payload = unwrap_readonly(unwrap_ref_type(result_type))
            if self.ctx.is_type_non_copyable(payload):
                _, message = slot_message(
                    payload, payload, f"the result of {expr.call_display}",
                    KIND_SLOT, "", non_copyable=True)
                raise self.ctx.error(message, expr)
        read_only = any(v.read_only for v in verdicts)
        if not read_only and expr.result_form is not ResultForm.COPY:
            self._mark_borrow_result_operands_written(fi, lent)
        return expr.result_form is not ResultForm.COPY and read_only

    def _mark_borrow_result_operands_written(
            self, fi: FunctionInfo, lent: list[LendSource]) -> None:
        """A MUTABLE borrow result may be written through, so the call is a
        mutable use of every argument that lends it -- as passing them to an
        open-`T` parameter of a user generic is (`pick(a, b)` makes its
        caller take `P& a, P& b`). The operands stay const only when they are
        declared readonly, which makes the result read-only. Through an
        `element_of=` argument the write lands in an element it hands out,
        so the mark climbs an iterator to the container it walks
        (`it = iter(ps)` marks `ps`)."""
        for src in lent:
            via_element = src.idx in fi.root.element_borrows_from
            names = proven_lend_roots(self.ctx, src.expr)
            if names is None:
                names = [r.name for r in lend_places(self.ctx, src.expr)
                         if not r.held_whole]
            roots = [StorageKey.of_name(n) for n in names]
            # A field-path root (`h.ps` behind `it = iter(h.ps)`) is a
            # write into its owner, as a write through a field is.
            mark_roots(self.ctx, roots, MarkRequest(
                loop_vars=True, through_field=True, via_element=via_element))

    def _lent_operand_verdict(self, fi: FunctionInfo, idx: int,
                              operand: TpyExpr) -> '_LentVerdict':
        """One lent argument of a borrow-declared call. At a `borrows=`
        parameter the argument lends when its form proves it outlives the
        call (`expr_lends_storage`). At an `element_of=` parameter it lends
        only when it is also a CONTAINER (`iter_element_source`): an
        iterator's step is valid only until the next step, and a class whose
        `__iter__` builds an iterator may own what it hands out. A fresh
        owner is a temporary nothing else reaches: not a handle or view into
        other storage, nor an expression that may be an existing object (a
        conditional); for an element source, an owning container. Read-only:
        the argument, or the container whose element is the result, is
        readonly-typed. Copies: an element source that is not a container,
        walked by the callee with an iterator of its own -- the parameter is
        an `Iterable`, where the caller's `Iterator` (`next`) keeps its step
        alive past the call -- so the element comes back by value."""
        rt = self.ctx.get_expr_type(operand)
        lends = expr_lends_storage(self.ctx, operand)
        handle_or_view = rt is not None and (is_iterator_protocol(rt)
                                             or is_borrowing_view_type(rt))
        fresh = (not lends and is_rvalue_source(self.ctx, operand)
                 and not handle_or_view)
        read_only = isinstance(rt, ReadonlyType)
        if idx not in fi.root.element_borrows_from:
            return _LentVerdict(lends, fresh, read_only, copies=False)
        container = (iter_element_source(rt, self.ctx.registry)
                     is IterElementSource.CONTAINER)
        params = fi.root.params
        # The receiver's declared type is the container it is called on.
        declared = (rt if idx == -1
                    else params[idx].type if 0 <= idx < len(params) else None)
        own_iterator = (declared is not None and not is_iterator_protocol(
            unwrap_qualifiers(declared)))
        return _LentVerdict(lends and container, fresh and container,
                            read_only, copies=own_iterator and not container)

    def analyze_call(self, expr: TpyCall) -> TpyType:
        """Analyze a function or constructor call."""
        # Expression callees: callbacks[0](x), get_handler()(x), etc.
        if not isinstance(expr.func, TpyName):
            return self._analyze_expr_callee(expr)

        # Before ANY outward resolution: the namespace chain reaches the
        # module level, so a nested def's name would resolve to the shadowed
        # module function/class until the walk reaches the `def`.
        self.ctx.check_nested_def_shadowed_read(expr.func_name, expr)

        # Type aliases: builtin type aliases (e.g. float64 = float) resolve
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

        # Generic type instantiation (e.g., Container[T, N](), Array[int32, 8]())
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
                # A builtin type instantiation: only the type arguments' range
                # is checked here, not the annotation position rules
                # (BUGS.md#builtin-ctor-type-args-skip-position-rules).
                self.type_ops.check_int_type_args(expr.call_type, expr.loc)
                written = source_view(expr)
                # Derive per-arg hints from __init__ param types when possible.
                # e.g. dict[str, str|int32]([("a","b"), ("c",1)]) -> hint list[tuple[str, str|int32]]
                arg_hints = self._derive_ctor_arg_hints(expr, written)
                typed = written.mapped(
                    lambda at, arg: self.expr.analyze_expr_with_hint(
                        arg, arg_hints[at]))
                picked = (self._validate_generic_constructor(expr, written, typed)
                          if written.addresses or written.double_star is not None
                          else None)
                if isinstance(expr.call_type, PtrType) and expr.args:
                    self._validate_ptr_constructor(expr)
                    # The ranked constructor renders it (the &{0} template).
                    if (expr.resolved_function_info is None
                            and picked is not None
                            and (picked.cpp_template or picked.native_function)):
                        expr.resolved_function_info = picked
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

        # The factory of an imported generic type, whose construction falls
        # through to the generic handling below.
        imported_generic_td: TypeDef | None = None

        # Use namespace for unified lookup - handles shadowing automatically
        binding = self._callee_binding(expr)
        if binding:
            if binding.kind == BindingKind.VARIABLE:
                if expr.subscript_callee is not None:
                    return self._rewrite_subscript_callee(expr)
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
                    if overloads := self._get_module_function_overloads(module_name, func_name):
                        return self._analyze_asyncio_spawn_call(expr, qname, overloads)
                    # falls through when the module registry misses (error path)
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
                    # from-imported asyncio.run / create_task keep the
                    # coroutine-only arg contract (CPython parity).
                    if func_infos[0].qualified_name in (
                            qnames.ASYNCIO_CREATE_TASK, qnames.ASYNCIO_RUN):
                        return self._analyze_asyncio_spawn_call(
                            expr, func_infos[0].qualified_name, func_infos)
                    # Builtin-supplemented functions route through builtin path
                    if func_infos[0].is_builtin_function:
                        if func_infos[0].special_handling:
                            return self._analyze_special_builtin(expr, func_infos)
                        return self._analyze_builtin_function_overloads(expr, func_infos)
                    return self._analyze_user_function_call(expr, func_infos)
                # Check for user module record (registered via _register_user_module_import).
                # Import-aware: get_record by short name collides for same-name
                # records from different modules.
                if record_info := self._record_for_local_name(
                        expr.func_name, binding):
                    return self._analyze_record_constructor(expr, record_info)
                # Check for module function (e.g., math.sqrt)
                if overloads := self._get_module_function_overloads(module_name, func_name):
                    if overloads[0].special_handling:
                        return self._analyze_special_builtin(expr, overloads)
                    if overloads[0].value_ptr_coercion:
                        return self._analyze_user_function_call(expr, overloads)
                    return self._analyze_builtin_function_overloads(expr, overloads)
                # Check for type constructor (e.g., int32 from tpy, int from builtins)
                qname = f"{module_name}.{func_name}"
                if record_info := self.ctx.registry.get_builtin_record(qname):
                    if record_info.get_method_overloads("__init__") and not record_info.type_params:
                        return self._analyze_record_constructor(expr, record_info)
                # Generic types (Array, list) - mark as found and fall through
                imported_generic_td = self.ctx.factory_of_binding(binding)
                if imported_generic_td is None:
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
        if imported_generic_td is None:
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

        # Fallback: Check if it's a record constructor. Route through the
        # full ctor analysis -- this catch-all used to just analyze args and
        # return the type, which let implicit-builtin constructions (the
        # exception classes reach here, not the namespace RECORD path) skip
        # arity/type validation entirely: OSError(an_int64, "msg") emitted
        # silently-narrowing C++.
        record = self._record_for_local_name(expr.func_name)
        if record:
            return self._analyze_record_constructor(expr, record)

        # Fallback: Check if it's a function call
        func_infos = self.ctx.registry.get_function(expr.func_name)
        if func_infos:
            return self._analyze_legacy_function_call(expr, func_infos[0])

        # Generic type constructor without context for type inference
        # Only proceed if we found an imported generic type in namespace
        if (lookup_td := imported_generic_td) is not None:
            record_info = self.ctx.registry.get_builtin_record(lookup_td.qname)
            if not record_info:
                raise self.ctx.error(f"Unknown type '{expr.func_name}'", expr)
            params = ", ".join(record_info.type_params)

            # Check for constructors that can infer type from arguments.
            written = source_view(expr)
            if written.addresses or written.double_star is not None:
                typed = written.mapped(lambda _, arg: unwrap_own(
                    unwrap_ref_type(self.expr.analyze_expr(arg))))
                args = [written.at(at) for at in written.addresses]
                arg_types = [typed.at(at) for at in written.addresses]
                # A call no `__init__` binds is refused for its binding,
                # before an empty-container argument can be blamed instead.
                self._refuse_unbound_ctor(expr, record_info, written, typed)
                # Empty-container args (`set()`, `{}`, `[]` with no type hint)
                # can't drive ctor inference -- their element types are
                # UnknownElementType. Emit the proper user-facing diagnostic
                # now so we don't feed UNKNOWN or unresolved TypeParamRefs
                # into cpp_template substitution (which raises
                # "UnknownElementType should be resolved before codegen" or
                # "Unknown type parameter 'K'").
                for arg, at in zip(args, arg_types):
                    if isinstance(at, PendingListType) and isinstance(at.element_type, UnknownElementType):
                        self._raise_empty_container_from_arg(expr, args, arg_types)
                    if isinstance(at, PendingSetType) and isinstance(at.element_type, UnknownElementType):
                        self._raise_empty_container_from_arg(expr, args, arg_types)
                    if isinstance(at, PendingDictType) and (
                            isinstance(at.key_type, UnknownElementType)
                            or isinstance(at.value_type, UnknownElementType)):
                        self._raise_empty_container_from_arg(expr, args, arg_types)
                fits: list[tuple[FunctionInfo, dict[str, TpyType]]] = []
                for ctor in record_info.get_method_overloads("__init__"):
                    placed = _ctor_placement(ctor, written, expr.func_name)
                    if placed is None:
                        continue
                    inferred_params = self.type_ops.match_generic_constructor(
                        [(operand_param_type(ctor.params[i]),
                          typed.at(at)) for i, at in placed])
                    if inferred_params is not None:
                        fits.append((ctor, inferred_params))
                if fits:
                    ctor, inferred_params = fits[self._pick_generic_ctor(
                        expr, record_info, fits, typed)]
                    # Guard against UNKNOWN_ELEMENT leaking from a
                    # `Pending*` arg (e.g. `list(set())`, `list({})`).
                    # The element is still unresolved; we must not feed
                    # UnknownElementType into the type factory or cpp
                    # template substitution. Fire the same
                    # empty-container diagnostic that resolve_all
                    # would, sourced from the offending Pending* arg.
                    if any(isinstance(v, UnknownElementType)
                           for v in inferred_params.values()):
                        self._raise_empty_container_from_arg(expr, args, arg_types)
                    # Use type_factory to create the result type
                    if (all(p in inferred_params for p in record_info.type_params)
                            and record_info.type_factory):
                        factory_args = []
                        for p in record_info.type_params:
                            t = inferred_params[p]
                            t = resolve_int_literals(t, self.ctx.default_int_for_literal)
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
                            expr.resolved_function_info = resolve_cpp_template_type_params(
                                ctor, clean_params, result_type=result_type, ctx=self.ctx)
                        self._validate_lvalue_params(expr)
                        self._check_ctor_arg_compatibility(expr, ctor, written, typed, inferred_params)
                        return result_type
                    # T not inferred from args; try assignment target hint
                    hint = (self.expr.type_hint_for(expr)
                            if record_info.type_factory else None)
                    if hint is not None:
                        if isinstance(hint, OwnType):
                            hint = hint.wrapped
                        hint = unwrap_readonly(hint)
                        if hint.qualified_name() == lookup_td.qname:
                            expr.call_type = hint
                            if ctor.cpp_template or ctor.native_function:
                                hint_params = extract_type_params(hint)
                                expr.resolved_function_info = resolve_cpp_template_type_params(
                                    ctor, hint_params, result_type=hint, ctx=self.ctx)
                            self._validate_lvalue_params(expr)
                            self._check_ctor_arg_compatibility(expr, ctor, written, typed, inferred_params)
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
                literal_id = self.expr.literal_id_for(expr)
                info = ListLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    element_type=UNKNOWN_ELEMENT,
                    size=0,
                    is_mutated=True,
                )
                self.expr.keep_literal(info)
                result_type = PendingListType(UNKNOWN_ELEMENT, 0, literal_id)
                expr.call_type = result_type
                return result_type
            # dict() with no args -- create empty dict with unknown key/value types.
            if (expr.func_name == "dict"
                    and isinstance(self.ctx.func.current_function, TpyFunction)
                    and record_info.type_factory):
                literal_id = self.expr.literal_id_for(expr)
                info = DictLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    key_type=UNKNOWN_ELEMENT,
                    value_type=UNKNOWN_ELEMENT,
                )
                self.expr.keep_literal(info)
                result_type = PendingDictType(UNKNOWN_ELEMENT, UNKNOWN_ELEMENT, literal_id)
                expr.call_type = result_type
                return result_type
            # set() with no args -- create empty set with unknown element type.
            if (expr.func_name == "set"
                    and isinstance(self.ctx.func.current_function, TpyFunction)
                    and record_info.type_factory):
                literal_id = self.expr.literal_id_for(expr)
                info = SetLiteralInfo(
                    literal_id=literal_id,
                    expr=expr,
                    element_type=UNKNOWN_ELEMENT,
                )
                self.expr.keep_literal(info)
                result_type = PendingSetType(UNKNOWN_ELEMENT, literal_id)
                expr.call_type = result_type
                return result_type
            raise self.ctx.error(
                f"Cannot infer element type for {expr.func_name}(); "
                f"use {expr.func_name}[{params}](), provide a type annotation, or pass an iterable",
                expr
            )

        # Subscript callee fallback: name[expr](args) where name is unknown
        # as a function/type -- try as subscript expression callee
        if expr.subscript_callee is not None:
            return self._rewrite_subscript_callee(expr)
        if expr.func_name == "tuple":
            raise self.ctx.error(_tuple_call_message(expr), expr)
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
    ) -> tuple[TpyExpr, TpyExpr, str | None, NominalType, 'RecordInfo',
               TpyType]:
        """Shared validation for getattr/setattr/delattr/hasattr builtins (D16).

        Validates: no kwargs, arg count, str-typed name, record receiver.
        `expected_args` may be a single count or a tuple of accepted counts.
        Returns (obj_arg, name_arg, literal_name_or_None, actual_type, record,
        obj_type).
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
        return obj_arg, name_arg, literal_name, actual_type, record, obj_type

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
        obj_arg, name_arg, name, actual_type, record, obj_type = self._extract_dyn_builtin_target(
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
                default_type = self.expr.analyze_expr_with_hint(
                    default_arg, ret_type)
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
        obj_arg, name_arg, name, actual_type, record, obj_type = self._extract_dyn_builtin_target(
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
        obj_arg, name_arg, name, actual_type, record, obj_type = self._extract_dyn_builtin_target(
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
        obj_arg, name_arg, name, actual_type, record, obj_type = self._extract_dyn_builtin_target(
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
                    f"(write(str) -> int32, flush() -> None); got '{actual}'",
                    expr)
        for arg in expr.args:
            if isinstance(arg, TpyStarUnpack):
                self._analyze_print_star(arg)
                continue
            # Any int prints the same; its width is decided when the
            # function settles.
            with self.pend.sink(arg), self.pend.list_sink(arg):
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

    def _analyze_print_star(self, arg: TpyStarUnpack) -> None:
        """`print(*xs)`: the source must be a sequence print can borrow and
        walk in place -- the set a `*args` unpack admits. Any other iterable
        refuses: CPython drains it completely before print writes anything,
        and walking it while printing would interleave its side effects
        with the output."""
        with self.pend.sink(arg.expr):
            source = self.expr.analyze_expr(arg.expr)
        # Print only reads the sequence, so an owned source (a fresh
        # `list(...)`) is borrowed for the statement like any other.
        source = unwrap_readonly(unwrap_own(unwrap_ref_type(source)))
        elem = star_source_element_type(source)
        if elem is None:
            got = ("a generator expression"
                   if isinstance(arg.expr, TpyGeneratorExpression)
                   else f"'{self.expr._user_type_name(source)}'")
            raise self.ctx.error(
                f"print() can unpack only a list, Array or Span with '*'; "
                f"got {got}. "
                f"Materialize the values first: print(*list(...))", arg)
        self.ctx.set_expr_type(arg, elem)

    def _get_module_function_overloads(self, module_name: str, func_name: str) -> list[FunctionInfo] | None:
        """Look up function overloads in a module using the unified registry."""
        module_info = self.ctx.registry.get_module(module_name)
        if module_info and func_name in module_info.functions:
            return module_info.functions[func_name]
        return None

    def _analyze_asyncio_spawn_call(self, expr: TpyCall, qname: str,
                                     overloads: 'list[FunctionInfo]') -> TpyType:
        """Analyze `asyncio.run` / `asyncio.create_task` like the ordinary
        module function it is, then enforce the coroutine-only contract."""
        self._reject_async_def_ref_arg(expr, qname)
        if overloads[0].special_handling:
            result = self._analyze_special_builtin(expr, overloads)
        elif overloads[0].is_builtin_function or overloads[0].cpp_template is not None:
            result = self._analyze_builtin_function_overloads(expr, overloads)
        else:
            result = self._analyze_user_function_call(expr, overloads)
        self._require_coroutine_arg(expr, qname)
        return result

    def _reject_coro_handle_borrow(self, arg: TpyExpr, arg_type: 'OwnType',
                                    pname: str) -> None:
        """A bound coroutine handle is single-use and consume-only: the
        create-lend-drop Own-strip (lend an owned rvalue to a borrow param)
        must not apply -- codegen would alias the unique_ptr handle,
        breaking the move-consume discipline."""
        inner = unwrap_readonly(arg_type.wrapped)
        if (isinstance(inner, NominalType)
                and inner.qualified_name() == qnames.CANCELLABLE):
            raise self.ctx.error(
                f"cannot borrow a coroutine handle into argument '{pname}': "
                f"the handle is single-use; consume it instead (await it, "
                f"pass it to asyncio.create_task/run, or move it into an "
                f"Own[Cancellable[T]] param)",
                arg)

    def _note_coro_lend(self, arg: TpyExpr, arg_type: 'OwnType',
                        ptype: TpyType) -> None:
        """A bound coroutine lent to a parameter that may change it (a
        protocol param a generic driver polls, `poll_once(c)`) may come back
        started: record it, so a later move is rejected. A readonly param
        cannot poll it. The committed slot's record."""
        if (self.ctx.slot_step_commits() and isinstance(arg, TpyName)
                and isinstance(unwrap_readonly(arg_type.wrapped),
                               ConcreteCoroType)
                and not isinstance(unwrap_send_sync(ptype), ReadonlyType)):
            self.ctx.assert_slot_effect("a lent coroutine's started record")
            self.ctx.func.started_coro_locals.setdefault(
                arg.name, arg.loc.line if arg.loc else 0)

    def _reject_async_def_ref_arg(self, expr: TpyCall, qname: str) -> None:
        """Friendly pre-check: `run(f)` where f is an async def is a missing
        call, not a coroutine. Without this the user gets a generic
        type-inference failure."""
        if len(expr.args) != 1 or not isinstance(expr.args[0], TpyName):
            return
        overloads = self.ctx.registry.get_function(expr.args[0].name)
        if not any(getattr(fi, "is_async", False) for fi in (overloads or ())):
            return
        short_name = qname.split(".", 1)[1]
        fname = expr.args[0].name
        raise self.ctx.error(
            f"asyncio.{short_name}() expects a coroutine, not the async "
            f"def itself; call it: {short_name}({fname}(...))",
            expr)

    def _require_coroutine_arg(self, expr: TpyCall, qname: str) -> None:
        """CPython parity: `asyncio.run` / `asyncio.create_task` accept
        coroutine objects only -- CPython raises TypeError('a coroutine was
        expected') for Tasks, Futures, and other awaitables even though
        they satisfy the Cancellable[T] param structurally. Compile-time
        analog of `iscoroutine`: the arg's static type must be the
        Cancellable[T] protocol (the async-def call-result shape). Runs
        after arg analysis so bound and forwarded handles are typed.
        """
        if len(expr.args) != 1:
            return  # arity error already reported by normal resolution
        # The coroutine the argument's erasure takes, not the erased owner.
        arg_type = self.ctx.get_expr_type(erased_source(expr.args[0]))
        if arg_type is None:
            return
        inner = unwrap_readonly(unwrap_own(unwrap_ref_type(unwrap_send_sync(arg_type))))
        if (isinstance(inner, NominalType)
                and inner.qualified_name() == qnames.CANCELLABLE):
            # Borrowed-receiver escape: a bound async-METHOD coroutine
            # captures its receiver by reference; spawning it as a task
            # lets the task outlive the receiver's scope. Lifetime is
            # not tracked across the escape.
            if (isinstance(inner, ConcreteCoroType)
                    and inner.frame_owner is not None):
                self.ctx.warning(
                    f"spawned bound method-coroutine borrows its receiver "
                    f"by reference; the task must not outlive the "
                    f"receiver (receiver lifetime is not tracked across "
                    f"this escape)",
                    expr)
            return
        short_name = qname.split(".", 1)[1]
        exc_name = ("TypeError" if qname == qnames.ASYNCIO_CREATE_TASK
                    else "ValueError")
        raise self.ctx.error(
            f"asyncio.{short_name}() expects a coroutine (the result of "
            f"calling an `async def`), got '{arg_type}'. CPython raises "
            f"{exc_name} for non-coroutine awaitables; await the value "
            f"directly instead",
            expr)

    def _analyze_tpy_copy(self, expr: TpyCall) -> TpyType:
        """Analyze a call to tpy.copy() - explicit copy for ownership transfer.

        copy() is truly generic (works with any type T, returns Own[T]).
        This is handled specially because the module system doesn't support
        truly generic functions yet.
        """
        self.reject_keywords(expr, "copy")
        if len(expr.args) != 1:
            raise self.ctx.error("copy() takes exactly 1 argument", expr)
        arg_type = self.expr.analyze_expr(expr.args[0])
        # Unwrap OwnType if already wrapped
        if isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        # A borrow-returning source reads as `Ref[T]`, and the copy of it is
        # an owned `T` -- keeping the marker would describe the result as a
        # borrow, so an inferred slot off it (a list literal element) would
        # spell an array of references. Same rule copy_iter applies per
        # element.
        arg_type = unwrap_ref_type(arg_type)
        # copy() yields owned storage: `copy(t)` on `tuple[Own[A], B]` is an
        # owned `tuple[A, B]`. Leaving either marker on an element would keep
        # describing a borrow, so the sink would lift the value a SECOND time.
        arg_type = owned_tuple_storage_type(arg_type)
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
        # A readonly source is a valid source: copy() reads it and never
        # writes it. The result is a fresh, independently owned value, so the
        # qualifier does not carry -- keeping it would describe the copy as
        # borrowed and reject it at every mutable slot, leaving the copy
        # warnings on readonly payloads with no spelling that silences them.
        # The param keeps the source's qualifier, which is what copy() borrows.
        result_type = unwrap_readonly(arg_type)
        if (isinstance(result_type, PendingViewType) and isinstance(arg, TpyName)
                and self.deduction.binding_withholds_view(arg.name)):
            # A loop variable lends no view: its copy is owned storage, as
            # the local it is bound to (`u = copy(n)`) will be.
            result_type = result_type.family.owned_type
        expr.resolved_function_info = FunctionInfo(
            name="copy",
            params=[ParamInfo("x", arg_type)],
            return_type=OwnType(result_type),
            is_readonly=True,
            is_builtin_function=True,
            qualified_name="tpy.copy",
        )
        return OwnType(result_type)

    def _analyze_tpy_copy_iter(self, expr: TpyCall) -> TpyType:
        """Analyze a call to tpy.copy_iter() - explicit element copy acknowledgment.

        copy_iter(iterable) wraps an iterable and copies each element during
        iteration. Returns CopyIter[T] where T is the element type.
        Suppresses the bulk copy warning on extend(), list(), etc.
        """
        self.reject_keywords(expr, "copy_iter")
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
            _return_borrows_from=frozenset({0}),
        )
        return result_type

    def _analyze_tpy_own_iter(self, expr: TpyCall) -> TpyType:
        """Analyze a call to tpy.own_iter() - consuming iteration.

        own_iter(container) moves the container into an OwnIter that
        iterates with move semantics. Returns OwnIter[T].
        The argument must be at its last use (movable), since own_iter
        takes ownership of the container via std::move.
        """
        self.reject_keywords(expr, "own_iter")
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
            self.compat.check_own_iter_source(arg, expr)
            # The consumption stamp; a later read is the read's own fact.
            moved = self.compat.claim_name_move(arg, OwnType(arg_type),
                                                registers=False)
            if arg not in self.ctx.all_last_uses:
                self.ctx.warning(
                    f"own_iter() consumes '{arg.name}' -- "
                    f"using it afterwards is undefined behavior. "
                    f"Use a regular for-loop if the container is needed later.",
                    expr,
                )
            elif moved:
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
        self.reject_keywords(expr, "try_parse")
        if len(expr.args) != 2:
            raise self.ctx.error(
                "try_parse() takes exactly 2 arguments: try_parse(EnumT, name)",
                expr,
            )
        first_arg = expr.args[0]
        path = name_path(first_arg)
        if path is None or self.ctx.func.current_ns is None:
            raise self.ctx.error(
                "try_parse() first argument must be an enum type name",
                expr,
            )
        ref = self.ctx.type_ref(first_arg)
        if ref is None or ref.kind != SymbolKind.ENUM:
            raise self.ctx.error(
                f"try_parse() first argument must be an enum type, "
                f"got '{'.'.join(path)}'",
                expr,
            )
        enum_type = ref.info
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
        self.reject_keywords(expr, name)
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

        Reached for a bare name that names no class (`_type_operand`
        resolves classes, user and builtin, through the type-path resolver
        first): builtin scalar names (int32, ...), type aliases and `Any`. Resolves `Any` (bare or aliased
        via `from typing import Any as A`) to AnyType when `allow_any=True`;
        otherwise rejects with the isinstance-flavoured "Any is not a
        runtime class" error. typing.cast wants the resolved AnyType so it
        can raise its own cast-flavoured rejection at one site.

        `allow_bare_generic` (isinstance only) resolves an alias of a
        complete class to that class; an alias of a container generic
        (`dict[str, JsonValue]`) is rejected pointing at the bare class,
        which `_type_operand` resolves like any class (CPython's
        `isinstance(x, dict[str, int])` raises "cannot be a parameterized
        generic"). Off for typing.cast.
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

    def _type_operand(
        self, arg: TpyExpr, expr: TpyCall, *, allow_any: bool = False,
        allow_bare_generic: bool = False,
    ) -> TpyType | None:
        """The type an isinstance / cast type operand names: a class
        through the type-path resolver (`C`, `m.Outer.Inner`), else a bare
        builtin or alias name (`_resolve_isinstance_type`). None when the
        operand is no name at all, so the caller reports its own shape
        error."""
        path = name_path(arg)
        if path is None:
            return None
        ref = self.ctx.type_ref(arg)
        # A type form (`Any`) is no runtime class; its name says so below.
        if (ref is not None and ref.kind == SymbolKind.RECORD
                and not ref.info.is_keyword_stub):
            return self.ctx.type_of_ref(ref)
        if isinstance(arg, TpyName):
            return self._resolve_isinstance_type(
                arg.name, expr, allow_any=allow_any,
                allow_bare_generic=allow_bare_generic)
        raise self.ctx.error(
            f"isinstance() second argument must be a type, got '{'.'.join(path)}'",
            expr)

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
                elem_type = self._type_operand(elem, expr, allow_bare_generic=True)
                if elem_type is None:
                    raise self.ctx.error(
                        "isinstance() tuple elements must be type names", expr
                    )
                types.append(elem_type)
            return types
        if isinstance(second_arg, TpyBinOp) and second_arg.op == "|":
            return self._resolve_inline_union_check_types(second_arg, expr)
        single = self._type_operand(second_arg, expr, allow_bare_generic=True)
        if single is not None:
            return [single]
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
            elif (operand_type := self._type_operand(
                    operand, expr, allow_bare_generic=True)) is not None:
                types.append(operand_type)
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
        selects. Exact structural match, or a bare generic class (`dict`,
        `m.Box`) matching that class's member at any type arguments -- by
        the class's identity, as a class pattern selects."""
        if member == check:
            return True
        if not isinstance(check, NominalType) or check.type_args:
            return False
        reg = self.ctx.registry
        record = reg.get_record_for_type(check)
        return record is not None and reg.is_instance_of(member, record)

    def _evaluate_static_isinstance(
        self, var_type: TpyType, check_types: list[TpyType], expr: TpyCall,
    ) -> bool:
        """Fold isinstance(var, check_types) against a concrete var_type.

        Walks the inheritance chain via TypeRegistry.is_subclass_of_or_equal
        (`ancestor_instance`), so `isinstance(child, Parent)` is True when
        Parent is any ancestor, a builtin base included, and a bare generic
        (`dict`) matches its class at any type arguments.
        Downcast checks (`isinstance(parent, Child)`) fold to False with a
        warning -- tpyc uses static dispatch, so any slicing at the boundary
        has already removed the Child fields. A `__deref__` handle (`Rc[Pet]`)
        is tested itself, as in CPython; a check type only the object it holds
        can be folds to False with a warning naming `deref()`. Every offending
        check type warns, so tuple forms like `isinstance(a, (Dog, Cat))`
        surface all mistakes at once.

        Type-parameter subjects do not reach here -- they are intercepted in
        `_analyze_isinstance` and lowered to a per-instantiation compile-time
        trait (`_analyze_isinstance_type_param`), since a generic body is
        analyzed once but instantiated per concrete type.
        """
        reg = self.ctx.registry
        # A container literal not decided yet is the container it spells.
        var_type = bound_as_spelled(var_type, lists=True)
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
            elif handle_payload_may_be(var_type, ct, self.type_ops,
                                       self.protocols, reg):
                subject = expr.args[0]
                spelled = (subject.name if isinstance(subject, TpyName)
                           else "...")
                self.ctx.warning(
                    f"isinstance() tests the '{var_type}' handle, never a "
                    f"'{ct}'; to test the object it holds, use "
                    f"isinstance(p := deref({spelled}), {ct})",
                    expr,
                )
        return result

    def _analyze_isinstance_type_param(
        self, tp: TypeParamRef, check_types: list[TpyType],
        var_name: str | None, expr: TpyCall,
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
        if not (isinstance(bound, NominalType) and bound.is_record
                and self.ctx.registry.get_record_for_type(bound) is not None):
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

    def _validate_polymorphic_subclass_dispatch(
        self,
        inner: 'TpyType',
        check_types: list['TpyType'],
        var_name: str | None,
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
        self.reject_keywords(expr, "isinstance")
        if len(expr.args) != 2:
            raise self.ctx.error(
                f"isinstance() takes exactly 2 arguments, got {len(expr.args)}", expr
            )

        first_arg = expr.args[0]
        # The walrus binds before the check reads its target, so it is
        # analyzed ahead of any narrowing lookup on that target.
        subject_type = self.expr.analyze_expr(first_arg)
        key = isinstance_narrowing_key(first_arg)

        # Check if second arg is a static protocol name
        second_arg = expr.args[1]
        if isinstance(second_arg, TpyName):
            protocol_info = self.ctx.registry.scan_by_short_name(second_arg.name)
            if protocol_info is not None and not protocol_info.is_dynamic:
                if not isinstance(first_arg, TpyName):
                    raise self.ctx.error(
                        "isinstance() with a protocol requires a "
                        "protocol-typed parameter as its first argument",
                        expr,
                    )
                return self._analyze_isinstance_protocol(
                    expr, first_arg, second_arg.name, protocol_info
                )

        check_types = self._resolve_isinstance_check_types(second_arg, expr)

        expr.resolved_function_info = self._isinstance_function_info()
        expr.isinstance_type = (check_types[0] if len(check_types) == 1
                                else make_union(*check_types))

        # If the variable is narrowed to a concrete non-union type (inside an
        # `if isinstance(v, A)` branch, a match arm, or via assignment
        # narrowing), the check is statically answerable. Fold the call site
        # to a constant but keep the full union branch-facts path below so
        # downstream narrowing / codegen extractions still run. A walrus
        # target is rebound by the check itself, so its earlier narrowing
        # says nothing about the value tested.
        narrowed = (self.ctx.func.narrowed_types.get(first_arg.name)
                    if isinstance(first_arg, TpyName) else None)
        static_fold: bool | None = None
        # When the variable was declared as a polymorphic-class source --
        # `Optional[Polymorphic]` (lowered to `T*`) or a bare polymorphic
        # class (lowered to `T&`) -- the runtime object retains its dynamic
        # type and dynamic_cast against a descendant is sound. Skip the
        # static fold so the runtime path lowers isinstance to a dynamic_cast.
        # Even after `is not None` narrowing strips Optional to its inner
        # class type, the C++ representation remains polymorphism-intact.
        # A subject with no name is tested as the value it evaluates to.
        value_type = peel_match_qualifiers(subject_type)[0]
        if isinstance(value_type, AliasRef):
            value_type = (self.ctx.registry.resolve_alias_ref(value_type)
                          or value_type)
        declared_param_type = (
            self.expr.narrowing.declared_type_for_name(key)
            if key is not None else value_type)
        if isinstance(first_arg, TpyNamedExpr):
            expr.isinstance_key_type = declared_param_type
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
        # the resume-case extraction casts through `&__self` there.
        if (isinstance(first_arg, TpyName) and first_arg.name == "self"
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

        effective_type = (self.expr.narrowing.effective_union_type(key)
                          if key is not None else value_type)
        if effective_type is not None:
            effective_type = self._isinstance_unwrap(effective_type)

        if effective_type is None:
            raise self.ctx.error(
                f"isinstance() cannot resolve the type of '{key}'"
                if key is not None
                else "isinstance() cannot resolve the type of its subject",
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
                    inner, check_types, key, expr)

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
            expr.isinstance_var = key
            return BOOL

        if not isinstance(effective_type, UnionType):
            # Generic type-parameter subject: a generic body is analyzed once
            # but instantiated per concrete type, so the answer is decided at
            # C++ instantiation, not here. Lower to a compile-time trait.
            if isinstance(effective_type, TypeParamRef) and check_types:
                return self._analyze_isinstance_type_param(
                    effective_type, check_types, key, expr)
            # Polymorphic source post-narrowing: source was declared as a
            # polymorphic class (`Optional[Inner]` or bare `Inner`); the
            # C++ representation is `const Inner*` or `const Inner&` and
            # dynamic_cast to a subclass is sound. Route to runtime dispatch.
            if polymorphic_source is not None and check_types:
                return self._validate_polymorphic_subclass_dispatch(
                    polymorphic_source, check_types, key, expr)
            # Non-union: compile-time evaluate against the static type,
            # walking the inheritance hierarchy. Reuse the narrowed-path
            # fold if it already ran so we don't warn twice.
            if static_fold is None:
                static_fold = self._evaluate_static_isinstance(
                    effective_type, check_types, expr)
            self._fold_isinstance(expr, static_fold)
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
            # The member the check names wins over its subclasses' members:
            # narrowing to several members is not supported
            # (BUGS.md#isinstance-base-union-subclass-member-false).
            exact = [m for m in hits if m == t]
            if exact:
                hits = exact
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
                self._fold_isinstance(expr, static_fold)
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

        expr.isinstance_var = key
        expr.isinstance_type = (matched[0] if len(matched) == 1
                                else make_union(*matched))
        if static_fold is not None:
            # Folded at the call site, but keep isinstance_var/isinstance_type
            # so the surrounding if/elif still narrows and codegen emits the
            # std::get extraction for the then-block.
            self._fold_isinstance(expr, static_fold)
        return BOOL

    @staticmethod
    def _fold_isinstance(expr: TpyCall, value: bool) -> None:
        """Answer `expr` statically. A bare-name subject reads nothing, so
        the call is the constant; any other subject still evaluates first."""
        if isinstance(expr.args[0], TpyName):
            expr.macro_expansion = TpyBoolLiteral(value=value, loc=expr.loc)
        else:
            expr.isinstance_fold = value

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

        For `class NativeIterable[T](Iterable[T])` and source `Iterable[int32]`,
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
        self.reject_keywords(expr, "Any")
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
        self.reject_keywords(expr, "cast")
        if len(expr.args) != 2:
            raise self.ctx.error(
                f"typing.cast() takes exactly 2 arguments, got {len(expr.args)}",
                expr,
            )

        # allow_any=True resolves bare `Any` and aliased imports (`from
        # typing import Any as A`) to AnyType rather than raising; we reject
        # Any with a cast-flavoured message in one place below.
        target_type = self._type_operand(expr.args[0], expr, allow_any=True)
        if target_type is None:
            raise self.ctx.error(
                "typing.cast() target must be a concrete type, not a union "
                "or other expression",
                expr,
            )
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
        self.reject_keywords(expr, enum_type.name)
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
        # The argument coercion's `Own` arm records this move (and settles
        # it into a copy, with the @nocopy error, if a later binding borrows).
        if self.compat.is_last_use_move_candidate(arg):
            self.compat.check_own_consumption(arg)
            return
        if self._is_nocopy_type(arg_type):
            reason = self.ctx.nocopy_reason(arg_type)

            def text(_note: str) -> str:
                # Asked at settlement, when the read's binding is known.
                if self.compat.read_owns(arg):
                    # Movable owner, but not at last use (used later)
                    return (f"{reason} is used after this point and cannot "
                            f"be moved into '{pname}: Own[{ptype.wrapped}]'. "
                            f"Remove later uses or restructure the code.")
                # Not movable (T& alias, readonly param, etc.)
                return (f"{reason} cannot be copied into "
                        f"'{pname}: Own[{ptype.wrapped}]'. "
                        f"Only the original owner can be moved at its last "
                        f"use.")
            # Held for settlement: the read's record says whether it copies.
            self.ctx.copy_error(text, arg, arg)
        # Non-nocopy implicit copy: warning is emitted by the coercion
        # path in compatibility.py (T -> Own[T] coercion), so no need
        # to duplicate it here.

    def _warn_unnecessary_copy(self, arg: TpyExpr) -> None:
        """Warn when copy(x) is passed to Own[T] param but x is at last use."""
        if not (isinstance(arg, TpyCall) and len(arg.args) == 1
                and arg.resolved_function_info
                and arg.resolved_function_info.qualified_name == "tpy.copy"):
            return
        if self.compat.advise_unneeded_copy(arg) is not None:
            self.compat.check_own_consumption(arg)

    def warn_cond_operand_eager_arg(self, arg: TpyExpr, arg_type: TpyType,
                                    ptype: TpyType) -> None:
        """Warn where a freshly built argument of a conditionally evaluated
        operand is built even when the branch is skipped.

        A reference param binds a named temporary, and inside a logical RHS
        / ternary arm / later chained comparator that temporary is created
        where the operand runs -- unless the value cannot be moved, which
        is what putting it there needs. Then it is built with the enclosing
        statement, ahead of the guard, and CPython would not have built it
        at all. The remedy makes that unconditional build explicit in the
        source instead of hiding it in a skipped branch.
        """
        if not self.ctx.cond_operand_depth:
            return
        # Only a freshly BUILT value runs early; an lvalue argument already
        # exists, and an owning slot takes the fresh value directly with no
        # temporary in between.
        if not isinstance(arg, (TpyCall, TpyMethodCall)):
            return
        if unwrap_optional_own(unwrap_send_sync(ptype)) is not None:
            return
        bare = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(arg_type)))
        if bare.is_value_type() or bare.is_movable():
            return
        self.ctx.warning(
            f"builds {bare} even when the branch is not taken: a value that "
            f"cannot be moved cannot be built inside a conditionally "
            f"evaluated operand; assign it to a local before the expression "
            f"to make the unconditional build explicit", arg)

    def check_own_callback_feeds(
            self, bound: 'list[tuple[int, ParamInfo, TpyExpr, TpyType | None]]',
    ) -> dict[int, TpyType]:
        """An iterable argument whose elements a callable argument receives
        as `Own[X]` is an owning element slot: the callee's declaration
        (type parameters unsubstituted) types the callable slot
        `Fn[[..., T, ...], U]` and the iterable slot `Iterable[T]` with the
        same `T`, and the callable's own parameter there is `Own[X]`. Each such
        argument gets the `Iterable[Own[X]]` parameter's element-copy check.
        `bound` holds each written argument where the call's binding puts
        it: (parameter index, declared parameter, operand, ranked type).
        Returns the effective slots by parameter index, for the lowering
        (`ctx.own_callback_feeds`)."""
        owned_elems: dict[str, OwnType] = {}
        for _, p, _, arg_t in bound:
            slot = unwrap_send_sync(unwrap_ref_type(p.type))
            fn_t = (unwrap_send_sync(unwrap_ref_type(unwrap_readonly(arg_t)))
                    if arg_t is not None else None)
            if not (isinstance(slot, CallableType) and isinstance(fn_t, CallableType)):
                continue
            for slot_pt, fn_pt in zip(slot.param_types, fn_t.param_types):
                if isinstance(slot_pt, TypeParamRef) and isinstance(fn_pt, OwnType):
                    owned_elems[slot_pt.name] = fn_pt
        feeds: dict[int, TpyType] = {}
        if not owned_elems:
            return feeds
        for i, p, arg, arg_t in bound:
            slot = unwrap_send_sync(unwrap_ref_type(p.type))
            if not (isinstance(slot, NominalType)
                    and slot.qualified_name() == qnames.ITERABLE
                    and len(slot.type_args) == 1
                    and isinstance(slot.type_args[0], TypeParamRef)
                    and slot.type_args[0].name in owned_elems
                    and arg_t is not None):
                continue
            owning_slot = slot.with_inner_types((owned_elems[slot.type_args[0].name],))
            feeds[i] = owning_slot
            self.compat.check_type_compatible(
                arg_t, owning_slot, f"argument '{p.name}'", source_expr=arg,
                coercion_ctx=CoercionContext.ARG, param=f"'{p.name}'")
        return feeds

    def check_own_param(self, arg: TpyExpr, arg_type: TpyType,
                        pname: str, ptype: TpyType) -> None:
        """Run Own[T] / Own[T]|None param checks: @nocopy error and unnecessary-copy warning.

        Handles both bare Own[T] and Own[T] | None parameter types.
        Call this for every parameter that might be ownership-taking.
        Non-nocopy implicit copies are warned by the coercion path.

        The ownership verdict is the committed slot's: a receive that does
        not commit (`SemanticContext.slot_step_commits`) checks nothing.
        """
        if not self.ctx.slot_step_commits():
            return
        self.warn_cond_operand_eager_arg(arg, arg_type, ptype)
        # Peel the transparent Send/Sync marker so a Send[Own[T]] destination
        # param gets the same ownership checks as a bare Own[T] param.
        ptype = unwrap_send_sync(ptype)
        if isinstance(arg, TpyName):
            # A by-VALUE slot (a tuple, `Own[value type]`) moves the name
            # while the arguments bind (`q = t; f(q[0].n, take(t))`): the
            # slot copies instead (warned by the checks below). A reference
            # slot (`T&&`) moves inside the callee, after every argument is
            # bound, so it keeps the move.
            bare = unwrap_readonly(ptype)
            own = unwrap_optional_own(bare)
            if (isinstance(bare, TupleType)
                    or (own is not None and own.wrapped.is_value_type())):
                self.compat.move_before_alias_reads(arg)
        passed = peel_coerce(arg)
        own_ptype = unwrap_optional_own(ptype)
        # A parameter owning a leaf is an owning result sink, as a return
        # or a yield is: one check per source.
        if param_takes_ownership(ptype) or owned_slot_paths(ptype):
            self.compat.check_tuple_result(arg, ptype, TupleSink.ARG,
                                           pname=pname)
        bare = unwrap_readonly(ptype)
        # The walk's view only picks the copy's words -- the whole owned
        # tuple's; the record decides whether it copies.
        if (isinstance(arg, TpyName) and isinstance(bare, TupleType)
                and param_takes_ownership(ptype)
                and self.compat._is_owned_var(arg.name)
                # A negative use: the copy path; a move is
                # `check_name_borrow_into_own`'s record.
                and not self.compat.is_last_use_move_candidate(arg)):
            # The `std::tuple<...>&&` param binds only an rvalue, so a
            # movable owned-tuple source NOT at its last use can't move in:
            # a @nocopy tuple is a clean use-after-move error, a copyable one
            # warns and is auto-copied (mirrors the scalar Own[T] arg).
            if self.ctx.is_type_non_copyable(arg_type):
                reason = (self.ctx.nocopy_reason(arg_type)
                          or f"owned tuple '{arg.name}'")
                self.ctx.copy_error(
                    f"{reason} is used after this point and cannot be "
                    f"moved into '{pname}'. Remove later uses or use "
                    f"copy().", arg, arg)
            # A mixed slot's owned element is warned by the
            # element-wise check the argument coercion runs.
            elif bare.is_owned_movable():
                self.ctx.copy_warning(
                    f"copies {bare} into owned storage; use "
                    f"copy() to make this explicit", arg, arg)
        if own_ptype is None:
            return
        self.compat.register_owned_result(arg, own_ptype, TupleSink.ARG)
        if (isinstance(passed, TpyTupleLiteral)
                and isinstance(own_ptype.wrapped, TupleType)):
            # The parameter OWNS the tuple like a field: every member is held
            # by value (a last-use owned one moved in, a borrowed one copied
            # -- both decided by the member check above).
            passed.elem_capture = [TupleElemCapture.VALUE] * len(passed.elements)
            self._warn_unnecessary_copy(arg)
            return
        # Unwrap OwnType from name lookup (implicit owned local) for the check.
        # Explicit OwnType (from function return, not a name) means ownership
        # was already acknowledged -- skip the check.
        check_type = arg_type
        if self.compat.passed_owned_element(own_ptype, arg):
            self._warn_unnecessary_copy(arg)
            return
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
        # Own[T] local passed to Own[T] param -- mark consumption
        if isinstance(arg_type, OwnType):
            self.compat.check_own_consumption(arg)
        self._warn_unnecessary_copy(arg)

    def mark_pending_slot_context(self, arg: TpyExpr, arg_type: TpyType,
                                  ptype: TpyType) -> None:
        """Record the slot context of a pending literal / view value so it
        resolves against the slot's type (a bare `[..]` local passed to a
        list[T] slot must become list, not Array; empty-`[]` element
        inference and dict/set element widening flow the same way). Called
        wherever a pending value meets the slot that decides it: a call's
        receive (`_receive_arg`) and a subscript store's value -- an
        unmarked path leaves the literal defaulting to Array and fails the
        C++ build. The committed slot's record
        (`SemanticContext.slot_step_commits`).
        """
        if not self.ctx.slot_step_commits():
            return
        if isinstance(arg_type, PENDING_CONTAINER_TYPES):
            self.deduction.mark_container_param_context(arg, arg_type, ptype)
        if isinstance(arg_type, PendingViewType):
            self.deduction.mark_view_param_context(arg, ptype, arg_type.family)

    def _derive_ctor_arg_hints(
            self, expr: TpyCall,
            written: CallSource) -> 'dict[int | str, TpyType | None]':
        """Derive per-argument type hints, by written address, from the
        types of the `__init__` parameters construction `written` binds.

        For generic constructors like dict[str, str|int32]([("a","b"), ("c",1)]),
        resolves __init__ param types with the known type params to produce
        concrete hints (e.g. list[tuple[str, str|int32]]).  Falls back to
        call_type when no __init__ overloads are found.
        """
        fallback: dict[int | str, TpyType | None] = {
            at: expr.call_type for at in written.addresses}
        if not expr.call_type or not fallback:
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

        options: list[dict[int | str, TpyType | None]] = []
        for ctor in record_info.get_method_overloads("__init__"):
            placed = _ctor_placement(ctor, written, expr.func_name)
            if placed is None:
                continue
            ctor_hints = dict(fallback)
            for i, at in placed:
                p = ctor.params[i]
                resolved = self.type_ops.substitute_type_params(
                    operand_param_type(p), inferred)
                hint = self._concrete_hint_from_param(resolved)
                if hint is not None:
                    ctor_hints[at] = hint
            options.append(ctor_hints)
        if not options:
            return fallback
        # The arguments are typed before a variant is picked: an argument
        # takes a hint only where every variant it binds gives the same.
        hints = {at: h if all(o[at] == h for o in options) else expr.call_type
                 for at, h in options[0].items()}
        # If the derived hint type doesn't match the arg expression
        # (e.g. ListType hint for a dict literal arg), fall back to call_type
        for at, hint in hints.items():
            if is_list(hint) and isinstance(
                    written.at(at), (TpyDictLiteral, TpySetLiteral)):
                hints[at] = expr.call_type
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

    def _validate_generic_constructor(self, expr: TpyCall, written: CallSource,
                                      typed: CallSource) -> FunctionInfo | None:
        """Validate and resolve generic type constructor calls, written with
        `written` (`source_view`), its arguments typed as `typed`; returns
        the `__init__` variant picked, if any.

        When call_type is set (e.g., list[int](iterable)), checks that the
        arguments conform to the constructor parameters they bind and sets
        resolved_function_info for codegen.
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
            return None
        init_overloads = record_info.get_method_overloads("__init__")
        if not init_overloads:
            self._refuse_unbound_ctor(expr, record_info, written, typed)
            return None
        inferred = extract_type_params(expr.call_type) if expr.call_type else {}
        accepted: list[tuple[FunctionInfo, bool]] = []
        for ctor in init_overloads:
            placed = _ctor_placement(ctor, written, expr.func_name)
            if placed is None:
                continue
            rejected = False
            fully_checked = True
            for i, address in placed:
                at = typed.at(address)
                p = ctor.params[i]
                p_type = unwrap_ref_type(operand_param_type(p))
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
                accepted.append((ctor, fully_checked))
        if accepted:
            index = self._pick_generic_ctor(
                expr, record_info, [(c, inferred) for c, _ in accepted],
                typed)
            ctor, fully_checked = accepted[index]
            if fully_checked and (ctor.cpp_template or ctor.native_function):
                expr.resolved_function_info = resolve_cpp_template_type_params(
                    ctor, inferred, result_type=expr.call_type, ctx=self.ctx)
            return ctor
        self._refuse_unbound_ctor(expr, record_info, written, typed)
        arg_types = list(typed.positional)
        # Arg type compatible with target (e.g. dict[K,V]({...}), list[T](other_list))
        if len(arg_types) == 1 and self.compat.is_type_compatible(arg_types[0], expr.call_type):
            return None
        # No constructor matched -- emit error for single-arg case
        if len(arg_types) == 1:
            raise self.ctx.error(
                f"{expr.func_name}() cannot be constructed from {arg_types[0]}",
                expr
            )
        return None

    def _pick_generic_ctor(
        self, expr: TpyCall, record: RecordInfo,
        fits: 'list[tuple[FunctionInfo, dict[str, TpyType]]]',
        typed: CallSource,
    ) -> int:
        """The index in `fits` -- the `__init__` variants of generic builtin
        `record` that take construction `expr`, its arguments typed as
        `typed`, each with the type arguments it takes them at -- of the one
        the call runs: ranked as every overloaded call is, declaration order
        deciding nothing."""
        if len(fits) == 1:
            return 0
        subs: list[FunctionInfo] = []
        for ctor, subst in fits:
            # A type parameter the arguments leave open stays open.
            full: dict[str, TpyType] = {tp: TypeParamRef(tp)
                                        for tp in record.type_params}
            full.update(subst)
            subs.append(self.type_ops.substitute_method_type_params(ctor, full))
        declared = {id(sub): ctor for sub, (ctor, _) in zip(subs, fits)}
        try:
            pick = rank_overload_candidates(
                [OverloadCandidate(sub, typed) for sub in subs],
                self.ranking())
        except OverloadAmbiguityError as e:
            raise self._ambiguous_overload_error(
                expr, expr.func_name, OverloadAmbiguityError(
                    tuple(declared.get(id(c), c) for c in e.candidates),
                    e.converted))
        if pick.winner is None:
            raise self._ambiguous_overload_error(
                expr, expr.func_name,
                OverloadAmbiguityError(tuple(c for c, _ in fits)))
        return pick.winner

    def _check_ctor_arg_compatibility(
        self,
        expr: TpyCall,
        ctor: FunctionInfo,
        written: CallSource,
        typed: CallSource,
        inferred: dict[str, TpyType],
    ) -> None:
        """Check copy/ownership warnings for constructor args (e.g., Own[T] in Iterable[Own[T]])."""
        for i, at in _ctor_placement(ctor, written, expr.func_name) or ():
            param = ctor.params[i]
            param_type = self.type_ops.substitute_param_type(
                operand_param_type(param), inferred)
            self.compat.check_type_compatible(
                typed.at(at), param_type, f"{expr.func_name}() argument",
                source_expr=written.at(at),
            )

    def _refuse_unbound_ctor(self, expr: TpyCall, record: RecordInfo,
                             written: CallSource, typed: CallSource) -> None:
        """Refuse construction `written` of generic builtin `record` when
        it binds to none of its `__init__` variants, as a user type's
        construction is: the shared no-match error, or, with no `__init__`
        declared, a keyword refused by the binding against no parameter."""
        name = expr.func_name
        inits = record.get_method_overloads("__init__")
        if not inits:
            if written.keywords or written.double_star is not None:
                self.bind_args(expr, [], f"{name}()", written)
            return
        if all(_ctor_placement(o, written, name) is None for o in inits):
            self.raise_no_match(name, inits, typed, written, expr,
                                constructed=record)

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

    @staticmethod
    def committed_operands(expr: 'TpyCallLike') -> CallOperands | None:
        """What committed call `expr` passes, read through its binding
        (`ArgBinding.at`): its callee, and the operand of each parameter
        the layout holds, by parameter index; None when it resolved to no
        callee. A call its step did not commit holds what was written; the
        ones that reach this uncommitted (a callable value's, a generator
        expression's frame creation) are positional and keep no binding."""
        fi = expr.resolved_function_info
        if fi is None:
            return None
        if not committed(expr):
            # Positionals alone: a keyword or an unpacking here would land
            # on a positional index.
            assert not getattr(expr, "kwargs", None) and not getattr(
                expr, "double_star_unpack", None) and not any(
                isinstance(a, TpyStarUnpack) for a in expr.args), (
                "an uncommitted call with keywords or unpacking")
            return CallOperands(fi, None, list(expr.args))
        return CallOperands(fi, None, expr.binding.operands(args=expr.args))

    def _check_borrow_arg_conflicts(
            self, expr: 'TpyExpr', ops: CallOperands | None = None) -> None:
        """Warn when a borrowed container is passed to a non-readonly
        parameter by call `expr`, whose operands are `ops` (by default its
        `committed_operands`; an operator or subscript hands its own).

        A non-pure, non-readonly function receiving a container by mutable
        reference could structurally mutate it, invalidating element borrows.
        Resolves aliases so that passing an alias of a borrowed container warns.
        Also marks str_source_borrows as mutated for string view fallback.

        Uses structural_mutated_params (append/insert/clear/etc.) when available,
        falling back to mutated_params for external/builtin functions.

        During sema, mutated_params holds direct facts only (Phase 1). If the
        param is directly mutated, we emit immediately. If not directly mutated
        but a call the callee makes reaches the param -- as an argument or as
        the receiver of `self.m()` -- (transitive mutation possible), we defer
        the check until Phase 2 resolves the final facts.
        """
        if ops is None:
            ops = self.committed_operands(expr)
        fi = ops.fi if ops is not None else None
        if fi is None or fi.is_pure or declares_no_writes(fi):
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
            if i >= len(ops.args):
                break
            arg = ops.args[i]
            # readonly[T] param -- function promises not to mutate; a
            # parameter the call leaves to its default is passed nothing.
            if arg is None or isinstance(param.type, ReadonlyType):
                continue
            reached = _edges_reach_param(fi, i)
            # A generator expression's frame is analyzed where it is written,
            # so its own writes are known here and its signature need not
            # stand in for them: a capture the body neither writes nor hands
            # to a call keeps the views borrowed out of it.
            if (_is_genexpr_frame(fi) and fi.direct_mutated_params is not None
                    and i not in fi.direct_mutated_params and not reached):
                continue
            self._demote_views_at_mutable_arg(param, arg)
            if (not isinstance(arg, TpyName)
                    or unwrap_optional_own(unwrap_readonly(param.type))
                    is not None):
                # An `Own` parameter takes a copy of a borrowed container
                # (a move never happens while it is borrowed): the callee
                # cannot reach the caller's container.
                continue
            bt = self.ctx.func.borrow_tracker
            storage = bt.effective_storage(arg.name)
            needs_check = bt.has_element_borrow(storage)
            if effective_mp is not None and i not in effective_mp:
                # Direct facts say "not structurally mutated". If a call the
                # callee makes reaches this param and Phase 2 hasn't finalized
                # yet, transitive propagation might still add it -- defer.
                if needs_check and reached and effective_direct is not None:
                    loc = getattr(expr, 'loc', None)
                    self.pending_borrow_checks.append((fi, i, storage, loc))
                if reached:
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
                    _borrowed_container_passed_warning(storage, fi, i), expr)
            self.ctx.mark_all_view_borrowers_mutated(storage)

    def _demote_views_at_mutable_arg(self, param: ParamInfo, arg: TpyExpr) -> None:
        """Demote str/bytes views borrowed out of this argument's storage.

        A view of a record field survives only while the field keeps its
        buffer, and a callee that receives the record through a MUTABLE
        borrow can replace it (`v = one.tag; bump(one)`).

        The gate is the callee's SIGNATURE, not an analysis of its body:
        mutation propagation is per module, so whether a cross-module callee
        writes is not knowable here, and this body's view storage is decided
        before Phase 2 finalizes even the intra-module facts. A parameter with
        a mutable borrow surface that is not `readonly` is therefore treated
        as a write, whatever the callee does; `readonly` is the one escape
        hatch. Asking `const_borrow_params` here would answer differently for
        an imported callee (verdict frozen) than for an intra-module one
        (verdict still None in Phase 1), making the local's C++ form depend on
        where its callee lives.

        The argument's whole dotted path is the key, so `zap(i)` reaches the
        view of `i.name` through the prefix match
        `mark_view_borrowers_mutated` already does, while keying a deeper
        argument by its path rather than by its root leaves a view borrowed
        off a sibling of that path alone.
        """
        if not param_has_mutable_borrow_surface(param.type):
            return
        arg_key = path_key(arg)
        if arg_key is not None:
            self.ctx.mark_all_view_borrowers_mutated(arg_key)

    def _mark_writably_lent_args(self, expr: TpyCall | TpyMethodCall,
                                 fi: FunctionInfo) -> None:
        """An argument the call's result lends writable is a mutable use of
        it at the call, whether or not the result is written: the callee
        returning its parameter through a writable slot
        (`escaping_borrow_writes`, the rule its own return marks the
        parameter mutated by) takes that parameter as a mutable reference.
        Read at the call because the callee's facts may still be pending
        here (a free function's body after the method calling it); an
        assumed source only widens the mark."""
        # A frame-building call's result is the frame: what its body yields
        # is the yield slot's question, settled by the call edges. A
        # constructor's result is a fresh object.
        if (fi.return_type is None or fi.is_async or fi.is_constructor
                or frame_factory_callee(fi)
                or not escaping_borrow_writes(fi.return_type)):
            return
        may = MayLend(self.ctx)
        for src in may.at_call(expr, (), Ask.WRITE) or ():
            if src.idx < 0 or not src.grants_write or src.temp_backed:
                continue
            mark_roots(self.ctx, storage_roots(
                src.expr, Use.POINTEE, self.ctx.get_expr_type, may),
                MarkRequest(loop_vars=True, through_field=True,
                            handle_bindings=False))

    def _record_mutation_call_edges(
            self, expr: 'TpyExpr', ops: CallOperands | None = None) -> None:
        """Record parameter flow through call `expr` for Phase 2 mutation
        propagation, its operands `ops` (by default its
        `committed_operands`; an operator or subscript hands its own, and
        credits its receiver itself)."""
        self.ctx.assert_slot_effect("a mutation call edge")
        if ops is None:
            ops = self.committed_operands(expr)
        fi = ops.fi if ops is not None else None
        if fi is None or fi.is_pure:
            return
        # Without the declaration the unknown-callee leg would conservatively
        # mark every flowing argument mutated; a bodied callee's `is_readonly`
        # is a receiver fact and does not stand for its arguments.
        if declares_no_writes(fi):
            return
        self._mark_writably_lent_args(expr, fi)
        # The mutable clone of an @auto_readonly accessor (dict.values/items,
        # Box.get) hands out a borrow but does not mutate its receiver
        # (`call_mutates_receiver`); an edge here would conservatively flip
        # the caller's self_mutated in Phase 2 (native clones have no analyzed
        # mutation facts). Mutation THROUGH the borrowed result is rooted at
        # the mutation site instead.
        if fi.is_auto_readonly_mutable_clone and not call_mutates_receiver(fi):
            return
        # Receivers whose mutation Phase 2 decides (`receiver_credits`):
        # whatever the receiver reaches past its member paths, borrows and
        # loop bindings (`write_reach`) -- super().method() is the
        # receiver's own call.
        receiver_targets: list[tuple[int, bool, bool]] = []
        receiver_bindings: list[object] = []
        receiver_captured: list[tuple[str, bool, bool]] = []
        if isinstance(expr, TpyMethodCall):
            if expr.super_parent_type is not None:
                idx = self.ctx.self_receiver_index()
                if idx is not None:
                    receiver_targets.append((idx, False, False))
            # A select receiver is each operand it may pick.
            for leaf in receiver_leaves(expr.obj):
                for credit in self.ctx.receiver_credits(leaf):
                    receiver_targets += [t for t in credit.targets
                                         if t not in receiver_targets]
                    receiver_captured += [c for c in credit.captured
                                          if c not in receiver_captured]
                    receiver_bindings += [
                        b for b in credit.bindings
                        if not any(b is r for r in receiver_bindings)]

        param_map: dict[int, int] = {}
        # param_map is single-valued per callee idx, but one callee slot can
        # reach several caller params: a vararg slot receives multiple args,
        # a RE-SEATED argument name borrows more than one root, and one param
        # can be reached both as itself and through a loop binding's element
        # step. Every (caller param, element step) beyond the first spawns a
        # standalone edge (collected here, emitted alongside the main edge),
        # with whether every place reaching it is lent.
        extra_param_edges: dict[tuple[int, int, bool], bool] = {}
        # Per callee slot, whether its argument LENDS the caller's storage
        # through a call or a handle, so only the callee's writes through the
        # slot's elements reach it. The main edge carries the slot's first
        # caller param and its loop bindings together, so it is lent only
        # when every place reaching either is -- an AND over the roots, so
        # the order they are seen in does not matter.
        main_lent: dict[int, bool] = {}
        # Callee slots whose caller param is reached through a loop binding
        # (`fold_edge`'s element step), and the bindings each slot's
        # argument climbs through.
        elem_step: set[int] = set()
        bindings: dict[int, list[object]] = {}

        def credit_arg(i: int, root: str, through_handle: bool) -> None:
            """What a callee writing through slot `i` reaches from argument
            root `root` (`write_reach`, across handles); `through_handle`:
            the argument is a handle a call in argument position builds
            over the root."""
            reach = self.ctx.write_reach(root, handles=True)
            self.ctx.defer_nested_writes(fi, i, reach.captured,
                                         lent=through_handle)
            if reach.bindings:
                slot = bindings.setdefault(i, [])
                slot += [b for b in reach.bindings
                         if not any(b is s for s in slot)]
                # Reached across a handle (`g = items(row); first(g)`): only
                # what the callee writes through the slot's elements reaches
                # the loop binding, as for a param.
                main_lent[i] = main_lent.get(i, True) and (
                    through_handle or reach.bindings_lent)
            for caller_idx, elem, crossed in reach.params:
                is_lent = through_handle or crossed
                main = ((param_map[i], i in elem_step) if i in param_map
                        else None)
                if main is None:
                    param_map[i] = caller_idx
                    if elem:
                        elem_step.add(i)
                if main is None or main == (caller_idx, elem):
                    main_lent[i] = main_lent.get(i, True) and is_lent
                else:
                    key = (i, caller_idx, elem)
                    extra_param_edges[key] = (
                        extra_param_edges.get(key, True) and is_lent)

        for i, callee_param in enumerate(fi.params):
            if i >= len(ops.args):
                break
            if (ops.args[i] is None
                    or isinstance(callee_param.type, ReadonlyType)):
                continue
            # Vararg slot: one credit per element root. A readonly vararg
            # slot is skipped (slot can't mutate elements through *items).
            if callee_param.is_variadic:
                bare_va = unwrap_ref_type(callee_param.type)
                if not is_varargs(bare_va):
                    continue
                if isinstance(bare_va.type_args[0], ReadonlyType):
                    continue
                pack = ops.args[i]
                if not isinstance(pack, TpyVarargPack):
                    continue
                for sub in pack.args:
                    sub_expr = sub.expr if isinstance(sub, TpyStarUnpack) else sub
                    sub_root = pointee_root(sub_expr, self.ctx.get_expr_type,
                                            MayLend(self.ctx))
                    if sub_root is not None:
                        credit_arg(i, sub_root, False)
                continue
            # Skip params with no mutable borrow surface. This catches plain
            # value types and also opts out of Own[T], TypeParamRef, and
            # generic-slot tuples. Tuples and pointer-repr Optionals with
            # non-readonly elements DO need edges so transitive mutation
            # through them propagates.
            sources = call_lend_sources(
                CallOperands(fi, None, list(ops.args)), frozenset((i,)),
                expr_type=self.ctx.get_expr_type)
            # An open-`T` slot (the param, or a tuple param's element) renders
            # a MUTABLE borrow at every reference instantiation, so whatever
            # binds there must stay a mutable lvalue -- a loop binding too.
            # The callee's own mutated_params cannot answer this -- the
            # generic body need not touch the param for its C++ slot to be a
            # mutable borrow -- so it is a direct fact at the call site, not
            # an edge.
            for src in sources:
                if src.binds_open_param:
                    mark_roots(self.ctx, storage_roots(
                        src.expr, Use.POINTEE, self.ctx.get_expr_type,
                        MayLend(self.ctx)),
                        MarkRequest(loop_vars=True, through_field=True))
            sources = [s for s in sources
                       if s.grants_write and not s.binds_open_param]
            if not sources or not param_has_mutable_borrow_surface(
                    callee_param.type):
                continue
            # An argument read off a call lends what that call's result points
            # into (a lazy combinator, a generator factory, a view, a
            # borrowing accessor), so a mutation through the callee's param
            # reaches each root the climb finds. An object the call builds
            # over them (an iterator, a frame, a view) is a HANDLE the callee
            # may advance without writing them (`lent`), as a handle loan is;
            # a C++ reference it returns is the storage itself. An assumed
            # root (a callee whose facts are pending) is kept: the edge only
            # widens the mutated set, which is the safe direction. A root
            # held only whole is not written through, and a handle read out
            # of a holder places nothing: what the callee writes is its
            # referent.
            arg_roots: list[tuple[str, bool]] = []
            for src in sources:
                placed = [p for p in storage_roots(
                    src.expr, Use.POINTEE, self.ctx.get_expr_type,
                    MayLend(self.ctx)).places if not p.held_whole]
                arg_roots += [(p.name, p.through_handle) for p in placed]
            for arg_root, through_handle in dict.fromkeys(arg_roots):
                credit_arg(i, arg_root, through_handle)
        # Edge stores the canonical so Phase 2 reads facts as they evolve.
        callee = fi.root
        assert callee.canonical_fi is None, (
            f"non-collapsed canonical_fi chain on {callee.name}")
        edge = MutationCallEdge(
            callee_fi=callee, param_map=param_map,
            lent=frozenset(i for i, is_lent in main_lent.items() if is_lent),
            elem_step=frozenset(elem_step),
            bindings={i: tuple(b) for i, b in bindings.items()})
        # One edge carries one receiver: every other operand a select
        # receiver may pick gets an edge of its own.
        record_receiver_edges(self.ctx, callee, receiver_targets,
                              tuple(receiver_bindings),
                              tuple(receiver_captured), param_edge=edge)
        if edge.param_map or edge.receiver_idx is not None:
            self.ctx.func.current_call_edges.append(edge)
        if edge.bindings:
            self.ctx.binding_edges.append(edge)
        for (callee_idx, caller_idx, stepped), is_lent in (
                extra_param_edges.items()):
            self.ctx.func.current_call_edges.append(
                MutationCallEdge(callee_fi=callee,
                                 param_map={callee_idx: caller_idx},
                                 lent=(frozenset((callee_idx,)) if is_lent
                                       else frozenset()),
                                 elem_step=(frozenset((callee_idx,))
                                            if stepped else frozenset())))

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
            mark_roots(self.ctx, [StorageKey.of_name(arg.name)],
                       MarkRequest(params=False, loop_vars=True))

    def _check_ctor_type_param_bounds(
        self, node: TpyExpr, callee: str, ctor: FunctionInfo,
        arg_types: list[TpyType],
    ) -> None:
        """A generic type-ctor winner committed without a substitution (its
        argument is itself a type parameter) is picked once for the whole
        generic body, so the argument's own bound must say the pick holds at
        every instantiation (at `int` or `str` the monomorphic call would
        pick `to_fixed_check` / `from_str_check`)."""
        bounds = ctor.type_param_bounds
        if not bounds:
            return
        type_subst: dict[str, TpyType] = {}
        for (_pname, ptype), arg_t in zip(ctor.params, arg_types):
            slot = unwrap_readonly(unwrap_ref_type(ptype))
            if isinstance(slot, TypeParamRef):
                type_subst.setdefault(slot.name, arg_t)
        validate_type_param_bounds(
            type_subst, bounds, f"{callee}()",
            self.protocols.satisfies_bound,
            lambda msg: self.ctx.error(msg, node),
            self.type_ops.substitute_type_params,
            detail=(". The conversion is picked once for the whole generic "
                    "body, so the argument needs a bound satisfying it"),
        )

    def pick_template_ctor(self, init_overloads: list[FunctionInfo],
                           args: 'CallSource | list[TpyType]',
                           built: TpyType | None = None,
                           ) -> OverloadPick:
        """The ranking's pick among the `__init__` overloads of a
        template-constructed type (`int8(x)`) for the argument types
        `args`: ranked as every overloaded call is (`CallRanking`); a
        numeric literal is at home in the numeric type the call builds
        (`built`), so `int8(100)` binds `T = int8`. Raises
        OverloadAmbiguityError."""
        ranking = self.ranking()
        if built is not None and (is_fixed_int_type(built)
                                  or is_any_float_type(built)
                                  or is_big_int_type(built)):
            ranking = ranking.at_home(built)
        return ranking.rank(init_overloads, args)

    def commit_template_ctor(self, node: TpyExpr, callee: str,
                             overloads: list[FunctionInfo], pick: OverloadPick,
                             arg_types: list[TpyType],
                             target: TpyType | None) -> FunctionInfo:
        """The winner of `pick` at the substitution the ranking bound, as a
        named call's winner is committed, so a literal's slot is the type it
        was bound at."""
        assert pick.winner is not None
        winner = overloads[pick.winner]
        if not pick.subst or not winner.type_params:
            self._check_ctor_type_param_bounds(node, callee, winner, arg_types)
            return winner
        fi = self.type_ops.substitute_method_type_params(winner, pick.subst)
        return same_type_conversion(fi, target)

    def narrowing_ctor(self, arg: TpyExpr, source: TpyType, target: TpyType,
                       value_range: 'ValueRange | None') -> FunctionInfo | None:
        """The call an implicit narrowing of `arg` (of type `source`) into
        fixed-width `target` renders as: the constructor `target(arg)` takes,
        picked and resolved as that call's is, or a plain cast where
        `value_range` -- the argument's range at the conversion site --
        proves the value fits. None when `target` has no constructor
        overload for the source."""
        record = self.ctx.registry.get_record_for_type(target)
        if record is None:
            return None
        overloads = record.get_method_overloads("__init__")
        try:
            pick = self.pick_template_ctor(overloads, [source], target)
        except OverloadAmbiguityError:
            return None
        if pick.winner is None or not overloads[pick.winner].cpp_template:
            return None
        ctor = self.commit_template_ctor(arg, str(target), overloads, pick,
                                         [source], target)
        cast = proven_cast(self.ctx, arg, ctor, source, target, value_range)
        if cast is not None:
            return cast
        return resolve_cpp_template_type_params(ctor, result_type=target,
                                                 ctx=self.ctx)

    def _analyze_template_constructor(self, expr: TpyCall, record: RecordInfo,
                                      init_overloads: list[FunctionInfo]) -> TpyType:
        """Analyze a constructor call for types with @cpp_template or @native __init__ overloads.

        Matches args against __init__ overloads using numeric compatibility and
        protocol-aware resolution. Handles special fallbacks for bool(__len__)
        and str(container).
        """
        written = source_view(expr)
        operands = [e for e in written.operands if e is not written.double_star]
        td = get_type_def(record.builtin_type_key)
        if td is not None and td.ctor_arg_truth_test:
            for arg in operands:
                self.expr.mark_truth_test(arg)
        typed = written.mapped(lambda _, arg: unwrap_own(unwrap_ref_type(
            self.expr.analyze_expr(arg))))
        # The operand types in written order; once a variant is picked, in
        # the order of the parameters they bind.
        arg_types = [*typed.positional, *(t for _, t in typed.keywords)]
        if td is not None and td.ctor_arg_truth_test:
            record_truth_calls(self.ctx, [leaf for arg in operands
                                          for leaf in truthy_operands(arg)])

        # Resolve the concrete type (e.g. FLOAT32 singleton). __init__ returns None
        # in Python, so we look up the actual type via the type factory.
        record_type = builtin_modules.get_builtin_type_obj(record.builtin_type_key)
        # An int literal the fixed-int type cannot hold is refused whichever
        # variant would take it.
        if (record_type is not None and is_fixed_int_type(record_type)
                and len(arg_types) == 1
                and isinstance(arg_types[0], IntLiteralType)):
            lit = arg_types[0]
            ret_tr = int_traits_of(record_type)
            if lit.value is not None and not (ret_tr.min_value <= lit.value <= ret_tr.max_value):
                raise self.ctx.error(
                    f"{record_type} overflow: {lit.value} is outside range "
                    f"[{ret_tr.min_value}, {ret_tr.max_value}]",
                    expr,
                )

        try:
            pick = self.pick_template_ctor(init_overloads, typed, record_type)
        except OverloadAmbiguityError as e:
            raise self._ambiguous_overload_error(expr, init_overloads[0].name, e)
        if pick.winner is not None:
            evidence = bound_evidence(init_overloads[pick.winner], typed)
            assert not isinstance(evidence, BindReject)
            arg_types = [t for _, t in evidence]
            ctor = self.commit_template_ctor(
                expr, expr.func_name, init_overloads, pick, arg_types,
                record_type)
            self.fill_positional_call(expr, ctor, {}, written)
            ret = record_type or ctor.return_type
            expr.resolved_function_info = resolve_cpp_template_type_params(ctor, result_type=ret, ctx=self.ctx)
            self._check_cast_safe(expr, ctor, arg_types, ret)
            # Borrowing views (StrView/BytesView/Span/SpanIter) need call_type
            # populated so downstream dangling/provenance checks can see
            # through the constructor. Other builtin constructors leave
            # call_type unset to preserve their existing codegen paths.
            if is_borrowing_view_type(ret):
                expr.call_type = ret
            return ret

        # A fallback converts the one operand of a one-argument variant, so a
        # keyword must name that variant's parameter, which it is bound to.
        keyed = bool(written.keywords) or written.double_star is not None
        pname = "x"
        if keyed:
            lone = next((o.params[0].name for o in init_overloads
                         if len(o.params) == 1 and not isinstance(
                             bound_evidence(o, typed), BindReject)), None)
            if lone is None:
                self.raise_no_match(expr.func_name, init_overloads, typed,
                                    written, expr)
            pname = lone
        # bool(obj) __len__ fallback: types with __len__ but no __bool__
        if record.builtin_type_key == qnames.BOOL and len(arg_types) == 1:
            arg_type = arg_types[0]
            if isinstance(arg_type, NominalType):
                arg_record = self.ctx.registry.get_record_for_type(arg_type)
                mro_definer = self.ctx.registry.mro_method_definer
                if (arg_record and mro_definer(arg_record, "__bool__") is None
                        and mro_definer(arg_record, "__len__") is not None):
                    expr.resolved_function_info = FunctionInfo(
                        name="__len__",
                        params=[ParamInfo(pname, arg_type)],
                        return_type=BOOL,
                        cpp_template="(::tpy::__len__({0}) != 0)",
                        is_readonly=True,
                    )
                    if keyed:
                        self.fill_positional_call(
                            expr, expr.resolved_function_info, {}, written)
                    return BOOL

        # str(container) fallback: containers have runtime to_str helpers.
        # The union arm is gated on every member being renderable -- same
        # all-members policy as the repr() fallback below -- so a non-renderable
        # member yields a sema diagnostic rather than a downstream C++ error.
        if record.builtin_type_key == qnames.STR and len(arg_types) == 1:
            arg0 = unwrap_readonly(arg_types[0])
            tmpl = container_to_str_template(arg0)
            if (tmpl is None and isinstance(arg0, UnionType)
                    and all(_repr_fallback_template(m) is not None for m in arg0.members)):
                tmpl = "::tpy::__str__({0})"
            if tmpl is not None:
                expr.resolved_function_info = FunctionInfo(
                    name="str",
                    params=[ParamInfo(pname, arg_types[0])],
                    return_type=STR,
                    cpp_template=tmpl,
                    is_readonly=True,
                )
                if keyed:
                    self.fill_positional_call(
                        expr, expr.resolved_function_info, {}, written)
                return STR

        # No matching overload found
        type_name = expr.func_name
        if not init_overloads:
            raise self.ctx.error(f"{type_name}() is not callable", expr)
        if keyed:
            self.raise_no_match(type_name, init_overloads, typed, written, expr)
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
        """The value-range elision of an explicit integer conversion
        (`narrowing_proven_safe`), shared with the implicit one."""
        target = target_type or ctor.return_type
        if (len(arg_types) != 1 or not ctor.cpp_template
                or target is None or not is_fixed_int_type(target)
                or not is_fixed_int_type(unwrap_readonly(arg_types[0]))
                or unwrap_readonly(arg_types[0]) == target):
            return
        cast = proven_cast(self.ctx, expr, ctor,
                           unwrap_readonly(arg_types[0]), target,
                           site_value_range(self.ctx, expr.args[0]))
        if cast is not None:
            expr.resolved_function_info = cast

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
            self.type_ops.validate_type(type_arg, allow_type_param_ref=self.ctx.in_generic_scope(), loc=expr.loc, allow_forward_ref=False)

    def _infer_arg_types(
        self, func: FunctionInfo,
        evidence: 'list[tuple[int | None, TpyExpr]]',
        seed: ReturnSeed | None = None, *,
        overloads: list[FunctionInfo] | None = None,
        shape: CallShape | None = None,
        ats: 'list[int | str] | None' = None,
    ) -> list[TpyType | None]:
        with self.ctx.probe_slot_scope(True):
            return self._infer_arg_types_in(func, evidence, seed,
                                            overloads, shape, ats)

    def _infer_arg_types_in(
        self, func: FunctionInfo,
        evidence: 'list[tuple[int | None, TpyExpr]]',
        seed: ReturnSeed | None = None,
        overloads: list[FunctionInfo] | None = None,
        shape: CallShape | None = None,
        ats: 'list[int | str] | None' = None,
    ) -> list[TpyType | None]:
        """Analyze a call's arguments for type param inference, with two
        phases for Fn/Callable params. `evidence` pairs each argument with
        the index of the parameter of `func` it binds (`ArgBinding.evidence`;
        None: no parameter); the types come back in its order. With
        `overloads` -- the call's whole set, called with `shape` -- each
        argument is analyzed as that set's probe argument (`probe_arg`) at
        where it is written (`ats`, `ArgBinding.addresses`).

        When a generic function has Fn/Callable params (e.g. map[T,U](fn: Fn[[T],U], ...)),
        function refs and lambdas can't be analyzed without concrete type hints. We:
        1. Analyze non-Fn args first to get types for partial type param inference.
        2. Substitute inferred params into the Fn type to build concrete hints.
        3. Analyze the Fn args with those hints.

        ``seed`` is an LHS-hint-derived pre-binding of type params (from
        matching the LHS hint against ``func.return_type``). When present, each
        arg is analyzed with the seeded ptype as ``expr_type_hint`` on its
        first pass -- this lets nested generic constructor calls see the outer
        LHS hint before arg-driven inference has any evidence. The seed's
        provenance reaches each argument hint through ``ReturnSeed.slot``.
        """
        seed = seed or ReturnSeed()

        def _analyze_arg(k: int, arg: TpyExpr,
                         hint: SlotHint | None) -> TpyType:
            if overloads is None:
                return self.expr.analyze_call_arg(arg, hint)
            return self.probe_arg(arg, overloads, shape,
                                  ats[k] if ats is not None else k, hint)

        # Quick check: if no Fn/Callable params, analyze all args directly.
        # Only an argument its context types (a lambda, a function named as
        # a value) waits for the Fn hint; any other is evidence like the
        # rest, as Regime C pre-analyzes it.
        fn_positions = {k for k, (slot, arg) in enumerate(evidence)
                        if slot is not None
                        and _is_fn_slot_param(func.params[slot].type)
                        and self._is_contextual_fn_arg(arg)}
        if not fn_positions:
            return [_analyze_arg(k, arg, seeded_arg_hint(func.params, slot, seed))
                    for k, (slot, arg) in enumerate(evidence)]

        # Phase 1: analyze non-Fn args (seeded per-arg hint when available)
        arg_types: list[TpyType | None] = [None] * len(evidence)
        for k, (slot, arg) in enumerate(evidence):
            if k not in fn_positions:
                arg_types[k] = _analyze_arg(
                    k, arg, seeded_arg_hint(func.params, slot, seed))

        # Phase 2: partial inference from known args, then resolve Fn args.
        # Start from the seed so Fn args see seeded type params even before
        # any non-Fn evidence binds them.
        partial_inferred: dict[str, TpyType] = dict(seed.subst)
        none_arms: list[tuple[TpyType, TpyType]] = []
        for (slot, _), arg_type in zip(evidence, arg_types):
            if arg_type is None or slot is None:
                continue
            param = func.params[slot]
            match_ptype = param.type
            if _fills_none_arm(match_ptype, arg_type):
                none_arms.append((match_ptype, arg_type))
                continue
            # A variadic param's declared type is the packed `Span[readonly[E]]`;
            # the corresponding arg_type is a single element (an individual
            # positional or a `*xs` unpack's element). Match against E so the
            # element evidence can bind the type param -- otherwise a Span-vs-
            # element shape mismatch leaves T unbound and a sibling Fn arg
            # can't concretize its hint.
            match_ptype = operand_param_type(param)
            self.type_ops.match_type_with_inference(match_ptype, arg_type, partial_inferred)
        self.type_ops._bind_none_arms(none_arms, partial_inferred)
        # Resolve literal markers (a nested one too: the hint spells the
        # lambda's parameter type) to concrete types for the Fn hint
        literal = self.ctx.default_int_for_literal
        for k, v in partial_inferred.items():
            if isinstance(v, PendingViewType):
                partial_inferred[k] = v.family.owned_type
            elif isinstance(v, TpyType):
                partial_inferred[k] = resolve_int_literals(v, literal)

        # A non-generic callee's callable parameter is its hint as it is.
        if partial_inferred or not func.type_params:
            for k in fn_positions:
                slot, arg = evidence[k]
                ptype = unwrap_ref_type(func.params[slot].type)
                concrete_hint = partial_substitute(ptype, partial_inferred)
                if is_callable_type(concrete_hint):
                    # Only the callee's OWN un-inferred type params block using
                    # this hint to type the lambda; an enclosing-scope type
                    # param (the caller's `T`, e.g. in `list[tuple[T, int32]]`)
                    # is a real in-scope type the lambda params can bind to.
                    callee_unresolved = set(func.type_params) - set(partial_inferred.keys())
                    has_unresolved = any(
                        contains_type_param(p, callee_unresolved)
                        for p in concrete_hint.param_types
                    )
                    if not has_unresolved:
                        arg_types[k] = self.expr.analyze_expr_with_hint(
                            arg, seed.slot(ptype, concrete_hint))

        # Fallback: analyze any remaining unresolved args without hints
        for k, (_, arg) in enumerate(evidence):
            if arg_types[k] is None:
                arg_types[k] = _analyze_arg(k, arg, None)

        return arg_types

    @staticmethod
    def _mark_key_lambda(key_arg: TpyExpr | None) -> None:
        """Give the key lambda -- spelled, or the one a class name at the key
        slot stands for -- const params: min/max call the key on `const T&`.
        Marked once the call resolved, since a class name's lambda only
        exists after its (possibly repeated) contextual analysis."""
        lam = lambda_of(key_arg) if key_arg is not None else None
        if isinstance(lam, TpyLambda):
            lam.readonly_params = True

    def _resolve_call_overloads(
        self,
        expr: TpyCall,
        func_infos: list[FunctionInfo],
        *,
        is_generic_for_pool: Callable[[FunctionInfo], bool],
        explicit_type_args: tuple[TpyType, ...] | None = None,
        strip_ref_own: bool = False,
        resolve_pending_view: bool = False,
        preserve_declaration_order: bool = True,
        src: CallSource | None = None,
    ) -> ResolveResult:
        """Selection-only overload resolution shared by builtin and user paths.

        `src`: the arguments the ranking reads, when not the call's own
        fields. Every candidate takes them where the call's one binding
        places them on its parameters (`bind`); one they do not bind to is
        not applicable.

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

        Raises ``OverloadAmbiguityError`` from the ranking --
        the caller wraps to surface a located diagnostic.
        """
        if src is None:
            src = source_view(expr, peel=False)
        shape = shape_of(src)
        bound = [(f, b) for f in func_infos
                 if (b := shape.binding(f)) is not None]
        # Regime predicate: which candidates have at least one Fn-typed
        # param filled by a supplied (non-default) arg?
        fn_bearing_supplied: list[tuple[FunctionInfo, list[SuppliedFnSlot]]] = []
        for f, b in bound:
            slots = supplied_fn_slots(b, src, f)
            if slots:
                fn_bearing_supplied.append((f, slots))

        # Candidates that differ only in a type parameter's bound (a value
        # and a reference overload of one signature) or a parameter's
        # `readonly` type their arguments alike, so they count as ONE shape
        # in the regime choice; every candidate still competes, and the
        # bound picks among them.
        shapes: list[list[tuple[str, TpyType]]] = []
        for f, _slots in fn_bearing_supplied:
            sig = [(p.name, unwrap_readonly(p.type)) for p in f.params]
            if sig not in shapes:
                shapes.append(sig)

        # Route to Regime C when there are 2+ Fn-bearing-by-supplied
        # candidates; one is typed at its own binding (Regime B), a keyword
        # as its positional twin.
        if len(shapes) >= 2:
            with self.ctx.probe_slot_scope(True):
                return self._with_candidate(expr, self._resolve_regime_c(
                    expr, src, fn_bearing_supplied, func_infos,
                    {id(f): b for f, b in bound},
                    explicit_type_args=explicit_type_args,
                    strip_ref_own=strip_ref_own,
                    resolve_pending_view=resolve_pending_view,
                ))

        # Regime A (0) or B (1): single fn_generic candidate (or none).
        fn_generic = fn_bearing_supplied[0][0] if fn_bearing_supplied else None

        named = [p.type for f in func_infos for p in f.params]
        bindings = {id(f): b for f, b in bound}

        def typed_for(func: FunctionInfo) -> CallSource:
            """The arguments typed for generic candidate `func`, at the
            parameters its binding gives them (`_infer_arg_types`, the one
            argument step of every generic inference)."""
            seed = self.type_ops.seed_subst_from_return_hint(
                func, self.expr.slot_hint_for(expr),
            ).with_explicit(func.type_params, expr.type_args)
            addressed = bindings[id(func)].addresses()
            # An operand the candidate's binding leaves out (a `**kwargs`
            # field) is typed unhinted at no parameter.
            ats = [at for _, at in addressed]
            ats += [at for at in src.addresses if at not in ats]
            slots = [i for i, _ in addressed] + [None] * (len(ats) - len(addressed))
            types = self._infer_arg_types(
                func, [(slot, src.at(at)) for slot, at in zip(slots, ats)],
                seed, overloads=func_infos, shape=shape, ats=ats)
            by_at = dict(zip(ats, self.ranked_arg_types(types, named)))
            out = src.mapped(lambda at, _: by_at[at])
            if strip_ref_own:
                out = out.mapped(lambda _, t: unwrap_own(unwrap_ref_type(t)))
            return out

        if fn_generic is not None:
            # The Fn-bearing candidate types the arguments the ranking
            # reads: a lambda takes its callable parameter as its hint.
            typed = typed_for(fn_generic)
        else:
            _, typed = self.probe_overload_args(expr, src, func_infos)
            if strip_ref_own:
                typed = typed.mapped(lambda _, t: unwrap_own(unwrap_ref_type(t)))

        # Build the unified candidate pool. Its order decides no winner
        # (a tie is ambiguous), only the order an ambiguity names them in.
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
                # Every argument is evidence at the parameter it binds,
                # keywords and keyword-only ones included, typed for it.
                if id(func) not in bindings:
                    continue
                try:
                    ftyped = typed if func is fn_generic else typed_for(func)
                except SemanticError:
                    # Its arguments do not type at its parameters (a lambda
                    # its callable parameter refuses): not applicable.
                    continue
                evidence = bindings[id(func)].evidence(ftyped)
                if isinstance(evidence, BindReject):
                    continue
                type_subst = self.type_ops.infer_type_params_for_function(
                    func, [t for _, t in evidence], self.protocols.satisfies_bound,
                    expected_return_type=self.expr.slot_hint_for(expr),
                    explicit_type_args=explicit_type_args,
                    slots=[slot for slot, _ in evidence],
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

        enriched = enriched_source(typed, src, candidates)
        try:
            # Each candidate ranked at the binding the call made to it.
            pick = rank_overload_candidates(
                [OverloadCandidate(c, enriched, bindings.get(id(
                    generic_originals[id(c)][0] if id(c) in generic_originals
                    else c))) for c in candidates], self.ranking())
        except OverloadAmbiguityError as e:
            # Named as declared: two substitutions may read alike.
            raise OverloadAmbiguityError(
                tuple(generic_originals[id(c)][0] if id(c) in generic_originals
                      else c for c in e.candidates), e.converted) from None
        matched = (candidates[pick.winner] if pick.winner is not None
                   else None)

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

        return self._with_candidate(expr, ResolveResult(
            matched=matched,
            matched_origin=matched_origin,
            typed=typed,
            enriched=enriched,
            contextual_callable_used=contextual,
            binding=pick.binding,
        ))

    @staticmethod
    def _with_candidate(expr: TpyCall, result: ResolveResult
                        ) -> ResolveResult:
        """`result` with its winner's `Candidate`: the binding the ranking
        placed its arguments by (`OverloadPick.binding`), the substitution
        it ranked the winner at."""
        if result.matched is None:
            return result
        declared = result.declared
        binding = result.binding
        assert binding is not None
        origin, subst = (result.matched_origin
                         if result.matched_origin is not None else (None, {}))
        result.candidate = candidate_of(source_view(expr), binding,
                                        result.matched, declared, subst,
                                        origin)
        return result

    def _probe_candidate_args(self, call: TpyExpr, args: list[TpyExpr],
                              func_infos: list[FunctionInfo]) -> list[TpyType]:
        with self.ctx.probe_slot_scope(True):
            types = self._probe_candidate_args_in(call, args, func_infos)
        return self.ranked_arg_types(
            types, [p.type for f in func_infos for p in f.params])

    def _probe_candidate_args_in(self, call: TpyExpr, args: list[TpyExpr],
                                 func_infos: list[FunctionInfo]
                                 ) -> list[TpyType]:
        """The overload probe's pre-analysis of `args`, shared by function
        and method calls.

        With a single candidate whose return matches the LHS hint, the
        arguments take that candidate's hints, so a nested generic call or
        record construction sees them on its FIRST analysis: its type is
        cached then, and the post-selection pass reads the cache back
        (`_generic_candidate`, `_analyze_record_constructor`).
        Under an INFERRED LHS a non-generic candidate does not pick itself:
        its arguments are analyzed exactly as in a fresh binding, and its
        declared parameter types only fill what nothing else types in them
        as a fill-only hint (`SlotHint.fill`), converting nothing.
        """
        # A fill aimed at this call (it is an outer overload's argument) is its
        # LHS too, so its own untyped arguments are filled the same way.
        lhs_hint = self.expr.slot_hint_for(call)
        sole = func_infos[0] if len(func_infos) == 1 else None
        shape = call_shape(call, args)

        def probe(i: int, arg: TpyExpr,
                  hint: 'SlotHint | TpyType | None' = None) -> TpyType:
            # The one candidate's parameter may be a protocol that says
            # nothing about a list's elements (`len`): a list literal
            # passes there with its element still open.
            slot = (positional_param_index(sole.params, i)
                    if sole is not None else None)
            blind = (slot is not None and not sole.params[slot].is_variadic
                     and self.expr._element_blind(sole.params[slot].type))
            with self.pend.list_sink(arg if blind else None):
                return self.probe_arg(arg, func_infos, shape, i, hint)

        if lhs_hint is None:
            return [probe(i, arg) for i, arg in enumerate(args)]

        n = len(args)
        candidate_hints = [
            self.type_ops.candidate_arg_hints(f, n, lhs_hint)
            for f in func_infos
        ]
        # Require a single LHS-matching candidate to avoid cross-candidate
        # hint mixing. With 2+ matching candidates, per-position "first
        # non-None wins" would splice hints across overloads, and an inner
        # generic construction analyzed under one candidate's hint would keep
        # the wrong T: its slot step does not infer it again.
        # ``candidate_arg_hints`` returns None for non-matching
        # candidates, so the count below tracks LHS-matchers specifically (a
        # candidate that matches LHS but has all-None per-arg hints still
        # counts -- otherwise a different LHS-matching candidate could win
        # the ranking and find its winner's hint shape unseeded).
        matching = [(f, h) for f, h in zip(func_infos, candidate_hints)
                    if h is not None]
        if len(matching) != 1:
            return [probe(i, arg) for i, arg in enumerate(args)]
        func, chosen = matching[0]
        if lhs_hint.is_declared or func.type_params:
            return [probe(i, arg, _probe_arg_hint(arg, chosen[i]))
                    for i, arg in enumerate(args)]
        out: list[TpyType] = []
        for i, (arg, hint) in enumerate(zip(args, chosen)):
            with self.ctx.slot_hint_scope(
                    SlotHint.fill(hint.type, arg) if hint is not None else None):
                out.append(probe(i, arg))
        return out

    def _named_function_overloads(
            self, expr: TpyName) -> 'list[FunctionInfo] | None':
        """The overloads a NAME resolves to when it names a function -- a
        local FUNCTION binding, a function imported from a module (a builtin
        like `len` included), or a registry function -- else None (a
        variable, record, module, ...). Falls through to the registry only
        when the namespace lookup misses entirely; an existing non-function
        binding shadows the registry."""
        if self.ctx.func.current_ns:
            binding = self.ctx.func.current_ns.lookup(expr.name)
            if binding:
                if binding.kind == BindingKind.FUNCTION:
                    return binding.func_infos or None
                if (binding.kind == BindingKind.IMPORTED_NAME
                        and binding.import_source):
                    return self._get_module_function_overloads(
                        *binding.import_source)
                return None
        return self.ctx.registry.get_function(expr.name) or None

    def _is_function_binding(self, expr: TpyName) -> bool:
        """True iff ``expr`` names a function (`_named_function_overloads`).
        Used by Regime C to decide which TpyName args need the
        per-candidate dry matcher vs. unhinted pre-analysis."""
        return self._named_function_overloads(expr) is not None

    def _is_contextual_fn_arg(self, arg: TpyExpr) -> bool:
        """True iff ``arg`` is contextual evidence for callable typing:
        a `TpyLambda`, or a `TpyName` that resolves to a function (local
        FUNCTION binding, imported, or registry).
        """
        if isinstance(arg, TpyLambda):
            return True
        if isinstance(arg, TpyName):
            return (self._is_function_binding(arg)
                    or self.expr._names_class(arg))
        return isinstance(arg, TpyFieldAccess) and self.expr._names_class(arg)

    def _resolve_regime_c(
        self,
        expr: TpyCall,
        src: CallSource,
        fn_bearing_supplied: list[tuple[FunctionInfo, list[SuppliedFnSlot]]],
        func_infos: list[FunctionInfo],
        bindings: 'dict[int, ArgBinding]',
        *,
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
        # The operands that are Fn-supplied for at least one candidate --
        # those need per-candidate handling.
        fn_supplied = {s.at for _, slots in fn_bearing_supplied for s in slots}

        baseline: dict[int | str, TpyType] = {}
        saved_unhinted_error: SemanticError | None = None

        # Pre-analyze: operands that don't need contextual typing.
        # An arg needs contextual typing iff it's a TpyLambda or a TpyName
        # resolving to a function. Everything else (variables, literals,
        # arbitrary expressions) is pre-analyzed once, as any overload
        # probe's argument (`probe_arg`) -- over the call's whole set
        # `func_infos`, not the Fn-bearing candidates tried here: whether
        # a number's width picks the variant is the set's fact.
        named = [p.type for f, _ in fn_bearing_supplied for p in f.params]
        shape = shape_of(src)
        for at in src.addresses:
            arg = src.at(at)
            if at in fn_supplied and self._is_contextual_fn_arg(arg):
                continue  # defer to per-candidate
            try:
                baseline[at] = self.ranked_arg_types([self.probe_arg(
                    arg, func_infos, shape, at)], named)[0]
            except SemanticError as e:
                if saved_unhinted_error is None:
                    saved_unhinted_error = e

        if strip_ref_own:
            baseline = {k: unwrap_own(unwrap_ref_type(v))
                        for k, v in baseline.items()}

        # Per-candidate trial: the substituted candidate, its argument
        # types, and its origin, for each candidate that passes the trial.
        candidate_evidences: list[tuple[
            FunctionInfo,                                  # substituted
            CallSource,                                    # argument types
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
                    expr, src, func, fn_slots, bindings[id(func)],
                    baseline=baseline,
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

        # Each candidate carries its own argument types, so the ranking
        # takes them per candidate rather than through `CallRanking.resolve`.
        pick = rank_overload_candidates(
            [OverloadCandidate(substituted, typed,
                               bindings[id(origin[0] if origin else substituted)])
             for substituted, typed, origin in candidate_evidences],
            self.ranking())

        matched: FunctionInfo | None = None
        matched_origin: tuple[FunctionInfo, dict[str, TpyType]] | None = None
        winner_typed: CallSource | None = None
        if pick.winner is not None:
            matched, winner_typed, matched_origin = (
                candidate_evidences[pick.winner])

        # The argument types for downstream diagnostics: the winner's if
        # any candidate matched, else the baseline (with ANY at any
        # contextual operand that was deferred and never filled in).
        display = (winner_typed if winner_typed is not None
                   else src.mapped(lambda at, _: baseline.get(at, ANY)))

        # Stash priority: per-candidate dry error (most informative for
        # the user's call shape) > pre-analysis failure.
        first_contextual_error = saved_dry_error or saved_unhinted_error

        # Literal promotion reads the substituted candidates the strict
        # stage found applicable, or every candidate when it found none.
        pool_for_enrich = ([candidate_evidences[i][0] for i in pick.strict]
                           if pick.strict else [e[0] for e in candidate_evidences])

        return ResolveResult(
            matched=matched,
            matched_origin=matched_origin,
            typed=display,
            enriched=enriched_source(display, src, pool_for_enrich),
            contextual_callable_used=contextual_callable_used,
            first_contextual_error=first_contextual_error,
            binding=pick.binding,
        )

    def _build_regime_c_evidence(
        self,
        expr: TpyCall,
        src: CallSource,
        func: FunctionInfo,
        fn_slots: list[SuppliedFnSlot],
        bound: ArgBinding,
        *,
        baseline: 'dict[int | str, TpyType]',
        explicit_type_args: tuple[TpyType, ...] | None,
        resolve_pending_view: bool,
    ) -> tuple[
        FunctionInfo,                                # substituted candidate
        CallSource,                                  # argument types
        tuple[FunctionInfo, dict[str, TpyType]] | None,  # origin
    ] | None:
        """Build per-candidate evidence for Regime C scoring.

        Returns None to silently reject this candidate (e.g. a baseline
        slot is None because pre-analysis failed, or a Fn slot's
        substituted hint has unresolved param TPRs). May raise
        ``SemanticError`` for ambiguity / generic-rejection on a
        function-ref dry match -- caller stashes and treats as
        candidate rejection. `bound`: the call's binding to `func`.
        """
        # Reject early when this candidate would need a non-Fn-slot
        # arg whose baseline analysis failed (saved_unhinted_error path).
        # After this check every non-Fn-slot operand has a real
        # baseline type; Fn-slot ones are filled below.
        fn_ats = {s.at for s in fn_slots}
        if any(at not in fn_ats and at not in baseline
               for at in src.addresses):
            return None

        # Non-Fn operands from the baseline, Fn ones filled by the per-slot
        # trial below (the placeholder is overwritten before scoring).
        types: dict[int | str, TpyType] = {
            at: (VOID if at in fn_ats else baseline[at])
            for at in src.addresses}

        # Partial inference from already-typed args (non-Fn slots only,
        # so the inference reflects what non-callable args determine),
        # each at the parameter the candidate's binding gives it.
        partial_inferred: dict[str, TpyType] = {}
        if func.is_generic():
            for i, at in bound.addresses():
                ptype = operand_param_type(func.params[i])
                # A parameter naming no type parameter is no evidence.
                if at in fn_ats or not contains_type_param(ptype):
                    continue
                self.type_ops.match_type_with_inference(ptype, types[at],
                                                        partial_inferred)
            # Resolve literal markers -> concrete, PendingView -> owned (mirrors
            # _infer_arg_types' resolution step).
            literal = self.ctx.default_int_for_literal
            for k, v in list(partial_inferred.items()):
                if isinstance(v, PendingViewType):
                    partial_inferred[k] = v.family.owned_type
                elif isinstance(v, TpyType):
                    partial_inferred[k] = resolve_int_literals(v, literal)

        # Per-Fn-slot trial.
        for slot in fn_slots:
            ptype = unwrap_send_sync(unwrap_ref_type(func.params[slot.param_index].type))
            hint = (partial_substitute(ptype, partial_inferred)
                    if func.is_generic() else ptype)
            if not is_callable_type(hint):
                return None
            # V1 limit: hint param types still name the callee's own
            # unresolved type params -- no way to validate the lambda/ref
            # against this slot. An enclosing scope's type param is a real
            # in-scope type, as Regime B takes it.
            unresolved = set(func.type_params) - set(partial_inferred)
            if any(contains_type_param(p, unresolved)
                   for p in hint.param_types):
                return None

            arg = slot.arg_expr
            if isinstance(arg, TpyLambda):
                if len(arg.param_names) != len(hint.param_types):
                    return None
                # An unresolved return TPR after partial inference is pinned
                # by the call's expected type where that pins it, else via a
                # body-analysis trial under ``trial_scope``, which commits
                # no state: the winner's commit analyzes the lambda.
                pinned = self._context_pinned(func, hint)
                slot_type: TpyType = (
                    pinned if pinned is not None
                    else self._lambda_body_dry_run(arg, hint))
            elif (isinstance(arg, (TpyName, TpyFieldAccess))
                    and self.expr._names_class(arg)):
                # A class name stands for `lambda *a: C(*a)`: this
                # candidate fits when that construction types under its
                # hint. The winner builds the name's real expansion when
                # its arguments are analyzed.
                slot_type = self._lambda_body_dry_run(
                    self.expr._class_factory_lambda(
                        arg, len(hint.param_types)),
                    hint, as_class=".".join(name_path(arg) or ()))
            elif isinstance(arg, TpyName) and self._is_function_binding(arg):
                # Pure dry matcher: returns matched data, raises on ambiguity.
                func_infos = self._named_function_overloads(arg)
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
                # If pre-analysis failed (no baseline), reject.
                if slot.at not in baseline:
                    return None
                slot_type = baseline[slot.at]
            types[slot.at] = slot_type

        typed = src.mapped(lambda at, _: types[at])
        # Run full inference on the candidate, each argument's type at the
        # parameter the candidate's binding gives it.
        if func.is_generic():
            evidence = bound.evidence(typed)
            type_subst = self.type_ops.infer_type_params_for_function(
                func, [t for _, t in evidence], self.protocols.satisfies_bound,
                expected_return_type=self.expr.slot_hint_for(expr),
                explicit_type_args=explicit_type_args,
                slots=[slot for slot, _ in evidence],
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

        return substituted, typed, origin

    def _context_pinned(self, func: FunctionInfo,
                        hint: CallableType) -> CallableType | None:
        """Callable slot `hint` of generic `func`, its return type
        parameter pinned by the call's expected type (matching
        `func.return_type` against it): the candidate is instantiated at
        it, as the commit receives the lambda there. `hint` itself when
        its return names no type parameter; None when the expected type
        does not pin it."""
        if not isinstance(hint.return_type, TypeParamRef):
            return hint
        expected = self.expr.type_hint_here()
        if expected is None:
            return None
        trial: dict[str, TpyType] = {}
        if not self.type_ops.match_type_with_inference(func.return_type,
                                                       expected, trial):
            return None
        pinned = trial.get(hint.return_type.name)
        if not isinstance(pinned, TpyType):
            return None
        return dc_replace(hint, return_type=pinned)

    def _lambda_body_dry_run(
        self, lambda_expr: TpyLambda, hint: 'CallableType',
        *, as_class: str | None = None,
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
        saved_body_captures = lambda_expr.body_captures
        saved_captures_by_value = lambda_expr.captures_by_value
        try:
            with self.ctx.trial_scope():
                return self.expr._analyze_lambda_with_fn_hint(
                    lambda_expr, hint, as_class=as_class)
        finally:
            lambda_expr.inferred_param_types = saved_inferred_param_types
            lambda_expr.inferred_return_type = saved_inferred_return_type
            lambda_expr.captured_names = saved_captured_names
            lambda_expr.body_captures = saved_body_captures
            lambda_expr.captures_by_value = saved_captures_by_value

    def _viable_candidates(self, expr: TpyCall,
                           candidates: list[FunctionInfo]) -> list[FunctionInfo]:
        """The candidates the call `expr`, as written, binds to."""
        if len(candidates) == 1:
            return candidates
        shape = shape_of(source_view(expr, peel=False))
        return [f for f in candidates if shape.binding(f) is not None]

    @contextmanager
    def _adaptive_list_args(self, expr: TpyCall, viable: list[FunctionInfo]
                            ) -> Iterator[OpenCallArgs | None]:
        """Analyze call `expr` with its container arguments open
        (`open_call_args`) when the call shape (`_viable_candidates`)
        leaves several candidates to score, or one generic candidate whose
        type parameters are inferred. Yields the scope opened, or None."""
        if not viable or (len(viable) == 1
                          and not _has_type_param_ref_in_params(viable[0])):
            yield None
            return
        written = source_view(expr)
        with self.open_call_args(
                expr, [*written.positional, *(e for _, e in written.keywords)],
                viable[0].name, scored=len(viable) > 1,
                candidates=viable) as scope:
            yield scope

    @contextmanager
    def open_call_args(self, use: TpyExpr | TpyStmt, nodes: list[TpyExpr],
                       name: str, scored: bool,
                       candidates: 'list[FunctionInfo] | None' = None,
                       ) -> Iterator[OpenCallArgs]:
        """Analyze the call `use` with its argument nodes `nodes` open: a
        container argument whose leaves are not decided yet is scored and
        inferred from as it holds them so far (`PendingNums.adaptive_view`)
        and decided by a parameter only once one is chosen -- the generic
        callee's resolved one, or with several `candidates` (`scored`) the
        winner's (`receive_winner_args`). A leaf nothing decided by the end
        of the call is settled then, as an argument to `name` -- at the
        call's committed slot (`SemanticContext.slot_step_commits`): a
        probe's parameter is not the one that decides it."""
        scope = OpenCallArgs(nodes, resolved=not scored, scored=scored,
                             source=source_view(use),
                             candidates=list(candidates or ()))
        self.ctx.open_call_args.append(scope)
        try:
            yield scope
        finally:
            self.ctx.open_call_args.pop()
        if self.ctx.trial_depth or not self.ctx.slot_step_commits():
            return
        open_cells = {c.cid for c in scope.cells if c.settled is None}
        if open_cells:
            self.pend.settle(open_cells, use=use,
                             what=f"an argument to '{name}()'")
            self.pend.resolve_ready()

    def step_commits(self, call: 'TpyExpr | TpyStmt | None') -> bool:
        """Whether the step of `call` -- its commit, its receive -- writes
        what its committed slot owns (`SemanticContext.slot_step_commits`,
        with `call` the innermost expression under analysis)."""
        with self.expr.step_scope(call):
            return self.ctx.slot_step_commits()

    def ranked_arg_types(self, arg_types: list[TpyType],
                         named: 'Iterable[TpyType]') -> list[TpyType]:
        """The types a call's overloads are ranked on: a pending number at
        the type its cells have so far -- the winner's parameter receives
        it still open -- unless it is an open leaf of a container literal
        the parameter types `named` name (the receiver's own part), which
        they take as it is."""
        own = {cid for t in named if isinstance(t, TpyType)
               for leaf in pending_leaves(t) for cid in leaf.cells}

        def ranked(t: TpyType) -> TpyType:
            inner = strip_int(t)
            if not isinstance(inner, PendingNumType) or inner.cells & own:
                return t
            return self.pend.known_type(inner)
        return [ranked(t) for t in arg_types]

    def probe_arg(self, arg: TpyExpr, overloads: list[FunctionInfo],
                  shape: CallShape, at: int | str,
                  hint: 'SlotHint | TpyType | None' = None) -> TpyType:
        """Argument `arg` of a call of `shape` to the overload set
        `overloads` -- the call's whole set -- written at `at` (a position,
        or a keyword's name), analyzed for the ranking. Where its width
        cannot pick the variant (`number_arg_stays_open`) a pending number
        passes the probe open (`ranked_arg_types` ranks it at the type its
        cells have so far) and the winner's parameter receives it; elsewhere
        the use fixes it."""
        stays_open = self.number_arg_stays_open(overloads, shape, at)
        with self.pend.sink(arg if stays_open else None):
            return self.expr.analyze_call_arg(arg, hint)

    def number_arg_stays_open(self, overloads: list[FunctionInfo],
                              shape: CallShape, at: int | str) -> bool:
        """Whether a pending number written at `at` (a position, or a
        keyword's name) of a call of `shape` to `overloads` stays open: its
        width cannot change which variant wins. That holds when every
        variant taking that shape (`admitted_overloads` with nothing known)
        that an integer can be passed to at the parameter the argument binds
        there (`CallRanking.admits`) declares one parameter type
        (`shared_param_type`) a pending number converts into
        (`receives_pending`) -- the rule an argument to one callee follows
        (`ExpressionAnalyzer.analyze_arg_at_param`). The other arguments'
        types filter nothing, so the verdict is a fact about the set and
        the call's shape: never about argument order, spelling, or how far
        a local's cells have been analyzed."""
        ranking = self.ranking()
        takers = [f for f in self.admitted_overloads(overloads, shape, {})
                  if f.has_variadic or (
                      (p := shape.param_type(f, at)) is not None
                      and ranking.admits(IntLiteralType(), p))]
        return receives_pending(self.shared_param_type(takers, at, shape))

    def ranking(self) -> CallRanking:
        """The checkers every overloaded call ranks its set with."""
        return call_ranking(self.ctx, self.protocols, self.type_ops,
                            self.compat)

    def admitted_overloads(self, overloads: list[FunctionInfo],
                           shape: CallShape,
                           known: 'dict[int | str, TpyType]', *,
                           twins_as_one: bool = True,
                           ) -> list[FunctionInfo]:
        """The overloads a call of `shape` binds to (`bind`) that each
        argument known so far (`known`, by where it is written) can be
        passed to at the parameter it binds (`CallRanking.admits`), an
        `@auto_readonly` twin pair as one unless `twins_as_one` is off: a
        FILTER, never a ranking -- a variant only a conversion of a known
        argument reaches stays beside a closer one, and the complete call
        is ranked once every argument is typed."""
        ranking = self.ranking()
        # A pending number is passed at the type its cells have so far.
        types = {at: self.pend.known_type(t) for at, t in known.items()}
        admitted = []
        for f in overloads:
            bound = shape.binding(f)
            if bound is None:
                continue
            if not f.has_variadic and not all(
                    ranking.admits(t, f.params[i].type)
                    for at, t in types.items()
                    if (i := bound.param_at(at)) is not None
                    and not f.params[i].is_kwargs):
                continue
            admitted.append(f)
        return without_mutable_twins(admitted) if twins_as_one else admitted

    def rank_method_call(
            self, name: str, views: list[FunctionInfo],
            arg_types: 'CallSource | list[TpyType]',
            args: 'CallSource | list[TpyExpr | None]',
            loc: 'TpyExpr | TpyStmt', *, readonly_receiver: bool,
            consuming_receiver: bool = False,
    ) -> tuple[Candidate | None, bool]:
        """The variant of an overloaded method a call with `arg_types` (as
        written: positionals and keywords; a list is positionals) runs
        (`views`, substituted at the receiver), ranked over the complete
        call with the receiver's kind as the first component -- the
        explicit `xs.m(a)` and the dunder call a subscript is alike --
        bound to the call (its `Candidate`), and whether the call passes
        the receiver const so C++ runs it (`pins_const_receiver`); None
        when no variant takes the call. A tie at the top is the call's
        ambiguity error at `loc`. `args`: the operands as written, by which
        a literal ranks as itself."""
        typed = typed_source(arg_types)
        src = (args if isinstance(args, CallSource) else CallSource(
            tuple(args) + (None,) * (len(typed.positional) - len(args))))
        enriched = enriched_source(typed, src, views)
        takers = filter_receiver_overloads(views, consuming_receiver)
        try:
            pick = self.ranking().rank(takers, enriched,
                                       is_readonly_receiver=readonly_receiver)
        except OverloadAmbiguityError as e:
            raise self.ctx.error(self.compat.ambiguous_overload_text(
                name, e.candidates), loc)
        if pick.winner is None:
            return None, False
        winner = takers[pick.winner]
        assert pick.binding is not None
        return (candidate_of(src, pick.binding, winner, winner, pick.subst),
                pins_const_receiver(winner, views, readonly_receiver))

    def no_match_hint(self, views: list[FunctionInfo],
                      arg_types: CallSource, args: CallSource) -> str:
        """What a "No matching overload" message adds: when every variant
        the call binds to fails at the same one argument, a literal-seeded
        local, the annotation (or conversion) that types it as the
        parameter there (`PendingNums.annotation_hint`) -- given when the
        variants it applies to declare one type there."""
        ranking = self.ranking()
        shape = shape_of(arg_types)
        cands = [(f, b) for f in views if not f.has_variadic
                 and (b := shape.binding(f)) is not None]
        failing = {tuple(at for at in arg_types.addresses
                         if (i := b.param_at(at)) is not None
                         and not f.params[i].is_kwargs
                         and not ranking.admits(
                             self.pend.known_type(arg_types.at(at)),
                             f.params[i].type))
                   for f, b in cands}
        if len(failing) != 1:
            return ""
        only = failing.pop()
        if len(only) != 1:
            return ""
        at = only[0]
        arg = args.at(at) if (isinstance(at, str) or at < len(args.positional)) else None
        if not isinstance(arg, TpyName):
            return ""
        hints = {h for h in (self.pend.annotation_hint(
                     arg.name, f.params[b.param_at(at)].type)
                     for f, b in cands) if h}
        return hints.pop() if len(hints) == 1 else ""

    def raise_no_match(self, name: str, views: list[FunctionInfo],
                       arg_types: 'CallSource | list[TpyType]',
                       args: 'CallSource | list[TpyExpr | None]',
                       loc: 'TpyExpr | TpyStmt',
                       arg_texts: 'list[str] | None' = None, *,
                       constructed: RecordInfo | None = None) -> NoReturn:
        """The error of an overloaded call no variant of `views` takes. When
        the call's arguments bind to none of them, all for one reason, that
        reason (`bind`'s); else the argument types as the call has them,
        each keyword by its name (`arg_texts` where a positional is named by
        its spelling instead), and what types a literal-seeded local the
        variants refuse (`no_match_hint`). `constructed`: the type the call
        constructs, whose constructor may read keywords as entries."""
        typed = typed_source(arg_types)
        src = (args if isinstance(args, CallSource) else CallSource(
            tuple(args) + (None,) * (len(typed.positional) - len(args)),
            tuple((k, None) for k, _ in typed.keywords)))
        rejects = [bind(typed, f.params, name) for f in views]
        reasons = {r.message for r in rejects if isinstance(r, BindReject)}
        refused = bool(views) and all(isinstance(r, BindReject)
                                      for r in rejects)
        td = (get_type_def(constructed.builtin_type_key)
              if constructed is not None and constructed.builtin_type_key
              else None)
        if (refused and td is not None and td.ctor_keywords_are_entries
                and (typed.keywords or typed.double_star is not None)):
            # CPython builds `{'x': 1}` from `dict(x=1)`: no parameter of
            # the stub is what the keyword names.
            raise self.ctx.error(
                f"keyword arguments to {name}() are not supported; write a "
                f"{name} literal", loc)
        if refused and len(reasons) == 1:
            raise self.ctx.error(reasons.pop(), loc)
        texts = (list(arg_texts) if arg_texts is not None
                 else [self.compat.call_type_text(t) for t in typed.positional])
        texts += [f"{k}={self.compat.call_type_text(t)}"
                  for k, t in typed.keywords]
        head = (f"No matching overload for '{name}' with argument types "
                f"({', '.join(texts)})")
        if refused:
            # Each variant names itself by its parameter types, so the
            # reason it gives is told apart from its siblings'.
            lines = list(dict.fromkeys(
                bind(typed, f.params, self.compat.variant_text(name, f)).message
                for f in views))
            raise self.ctx.error(
                "\n".join([head] + [f"  {line}" for line in lines]), loc)
        hint = self.no_match_hint(views, typed, src)
        raise self.ctx.error(f"{head}{hint}", loc)

    @staticmethod
    def shared_param_type(survivors: list[FunctionInfo],
                          at: int | str, shape: CallShape) -> TpyType | None:
        """The type of the parameter the argument written at `at` (a
        position, or a keyword's name) of a call of `shape` binds, when every
        survivor declares one there and they agree (a variadic survivor, or
        one it binds no parameter of, agrees with nothing); None otherwise
        -- no survivor's hint is borrowed for the others."""
        if not survivors or any(f.has_variadic for f in survivors):
            return None
        types = {shape.param_type(f, at) for f in survivors}
        return types.pop() if len(types) == 1 and None not in types else None

    def probe_overload_args(self, call: TpyExpr, src: CallSource,
                            overloads: list[FunctionInfo],
                            known: 'dict[int | str, TpyType] | None' = None,
                            ) -> tuple[CallSource, CallSource]:
        """The arguments of a call written with `src` to several overloads,
        typed for the ranking, keywords as positionals; `known` holds those
        analyzed already, by where they are written. An argument its context
        types (a lambda, a function named as a value, a container literal or
        comprehension) is analyzed last, at the type of the parameter it
        binds on every variant the other arguments admit
        (`admitted_overloads`, `shared_param_type`) -- unhinted when they
        disagree, never at one survivor's. Returns the arguments' types and
        the types the ranking takes them at (`ranked_arg_types`,
        `ranked_at_slot`), each a source of types."""
        known = dict(known or {})
        named = [p.type for f in overloads for p in f.params]
        ranked = dict(zip(known, self.ranked_arg_types(
            list(known.values()), named)))
        contextual = [at for at in src.addresses if at not in known
                      and self.context_typed(src.at(at))]
        shape = shape_of(src)
        if not known and not any(isinstance(at, int) for at in contextual):
            for i, t in enumerate(self._probe_candidate_args(
                    call, list(src.positional), overloads)):
                known[i] = ranked[i] = t
        # The probe's slot decides types only: the winner's receive is
        # where each argument commits (`receive_winner_args`).
        with self.ctx.probe_slot_scope(True):
            for at in src.addresses:
                if at not in known and at not in contextual:
                    known[at] = ranked[at] = self.ranked_arg_types(
                        [self.probe_arg(src.at(at), overloads, shape, at)],
                        named)[0]
            survivors = (self.admitted_overloads(overloads, shape, known)
                         if contextual else [])
            for at in contextual:
                arg = src.at(at)
                hint = self.shared_param_type(survivors, at, shape)
                # A parameter naming a type parameter is no type to hint.
                if hint is None or contains_type_param(hint):
                    known[at] = ranked[at] = self.expr.analyze_call_arg(arg)
                    continue
                known[at] = self.expr.analyze_arg_at_param(arg, hint, hint)
                ranked[at] = self.ranked_at_slot(known[at], hint)
        return (src.mapped(lambda at, _: known[at]),
                src.mapped(lambda at, _: ranked[at]))

    def context_typed(self, arg: TpyExpr) -> bool:
        """Whether argument `arg`'s type is given by its context: a lambda,
        a function named as a value, a container literal or comprehension."""
        return (isinstance(arg, _CONTEXT_TYPED)
                or self._is_contextual_fn_arg(arg))

    def ranked_at_slot(self, arg_type: TpyType, slot: TpyType) -> TpyType:
        """The type an argument analyzed at its context's slot `slot` is
        ranked at: the slot's own type when the argument converts to it (a
        tuple literal of numbers is that tuple) or is a container literal
        still open, which the parameter decides -- as its coercion will hand
        it over; else its own."""
        slot = unwrap_own(unwrap_ref_type(slot))
        if (isinstance(unwrap_qualifiers(arg_type), PendingContainerType)
                or self.compat.is_type_compatible(arg_type, slot,
                                                  CoercionContext.ARG)):
            return slot
        return arg_type

    def receive_winner_args(self, scope: OpenCallArgs | None,
                            args: list[TpyExpr],
                            arg_types: 'list[TpyType | None] | None',
                            params: 'list[ParamInfo] | list[tuple[str, TpyType]]',
                            declared: FunctionInfo | None, *,
                            call: 'TpyExpr | TpyStmt | None' = None,
                            declared_types: 'list[TpyType] | None' = None,
                            fresh_own: str = "keep",
                            char_literal_init: bool = False,
                            builtin_form: bool = False,
                            own_checks: bool = True,
                            contexts: 'list[str] | None' = None,
                            inferred_slots: 'Mapping[int, InferredSlot] | None' = None,
                            param_indices: 'Sequence[int] | None' = None,
                            ) -> list[TpyType]:
        """The one receive step every winner and commit runs: each argument
        of `args` re-enters through the analysis entry rule at its
        parameter of `params` (closed, it hands back its one analysis; open,
        its slot step decides it there), then takes the per-argument duties
        once -- the empty-list protocol pin, the readonly restore, the
        ownership checks, the pending-literal context -- and is coerced to
        the parameter in place. `arg_types[i]`, when given, is a type the
        call's own preparation decided for the argument (a pending leaf, a
        container receiver's entries), taken instead of a re-entry.

        `scope`, the call's open arguments: a parameter the winner
        `declared` resolves leaves its argument open (the coercion decides),
        any other is a declared slot that decides it. `declared`, the
        callee, also says how it takes every argument: a `@readonly` callee
        makes each parameter readonly (it cannot be handed a write), and a
        `@value_ptr_coercion` one takes a value at a `Ptr` parameter by
        address. `declared_types` are
        the parameters as the callee spells them (a numeric one passes a
        pending number open); `call` the call node, the step whose receive
        this is: at a slot that does not commit it decides types only
        (`SemanticContext.slot_step_commits`), the per-argument duties
        writing nothing.

        The mode flags keep the per-path behavior each caller had before
        the paths shared this step:
        - `fresh_own` "reject_coro" (plain and varargs functions) /
          "note_lend" (generic commit) / "keep" (methods, constructors,
          builtins): a function strips an owned rvalue lent to a borrow
          parameter, and a bound coroutine handle lent there is refused /
          recorded as maybe started -- two rules for one shape, kept apart
          until one is chosen (BUGS.md#coroutine-handle-lend-paths-disagree).
        - `char_literal_init`: a free function's one-char `str` literal at a
          `char` parameter initializes it; methods and constructors still
          refuse it (BUGS.md#char-literal-arg-free-function-only).
        - `builtin_form`: a builtin's argument is taken bare (no Own / Ref)
          and coerced only where its type differs, as the builtin overload
          path always received it. The callee does not say it: a generic
          builtin taken through the generic commit receives in the
          function form.
        - `own_checks` False for a `*args` element: the pack copies it
          whatever its parameter, so it takes no ownership check
          (BUGS.md#vararg-pack-copies-own-element).
        `contexts` names an argument in a diagnostic, by position, where
        the parameter's name is not the user's to see (a builtin
        subscript's key); a None entry keeps the parameter's name.
        `inferred_slots` maps an argument position to the parameter typed by
        a type argument an earlier call inferred (`coerce_expr`'s
        `inferred_slot`). `param_indices[i]`: the parameter of `declared`
        argument `i` binds (its binding's), which says whether the call
        resolves it -- given wherever `scope` scored the call.
        Returns the argument types received."""
        probing = not self.step_commits(call)
        # A callee Phase 2 inferred const may still write its argument
        # (inference reads `self` only), so only a declared one waives
        # the readonly restore.
        readonly = declared is not None and declared_readonly(declared)
        value_ptr = declared is not None and declared.value_ptr_coercion
        if scope is not None and scope.scored and declared is not None:
            scope.resolved = any(call_resolves_params(declared))
        out: list[TpyType] = []
        for i, (arg, param) in enumerate(zip(list(args), params)):
            given = (arg_types[i] if arg_types is not None
                     and i < len(arg_types) else None)
            at_declared = (given is None and scope is not None
                           and scope.scored and declared is not None
                           and not (param_indices is not None
                                    and call_resolves_param(
                                        declared, param_indices[i])))
            saved = scope.receiving if scope is not None else None
            if at_declared:
                scope.release(arg)
                scope.receiving = arg
            try:
                with self.expr.step_scope(call), \
                        self.ctx.probe_slot_scope(probing):
                    out.append(self._receive_arg(
                        i, args, given, param, call=call, declared=declared,
                        declared_types=declared_types, readonly=readonly,
                        fresh_own=fresh_own,
                        char_literal_init=char_literal_init,
                        value_ptr=value_ptr, builtin_form=builtin_form,
                        own_checks=own_checks, contexts=contexts,
                        inferred_slot=(inferred_slots.get(i)
                                       if inferred_slots is not None
                                       else None)))
            finally:
                if at_declared:
                    scope.receiving = saved
        return out

    def commit_winner(
            self, expr: 'TpyCallLike', cand: Candidate, name: str, *,
            stamp: FunctionInfo | None = None,
            scope: OpenCallArgs | None = None,
            fill: bool = True,
            params: 'Mapping[int, ParamInfo | tuple[str, TpyType]] | None' = None,
            declared_types: 'Sequence[TpyType] | None' = None,
            decided: 'Mapping[int, TpyType] | None' = None,
            inferred_slots: 'Mapping[int, InferredSlot] | None' = None,
            fresh_own: str = "keep",
            char_literal_init: bool = False,
            builtin_form: bool = False,
            feeds: 'tuple[list[ParamInfo], CallSource] | None' = None,
            kwargs_arg: 'TpyExpr | None | _RegionNotBuilt' = REGION_NOT_BUILT,
    ) -> ArgLayout:
        """Commit candidate `cand` of call `expr` to `name`, the one commit
        of every ranked call and every one-signature call: the `**kwargs`
        region and the layout of `cand.binding` (`lay_out_args`, its
        defaults filled where the step commits, unless `fill` is off: a
        call whose written arguments stand as its list), each laid-out
        operand received once at its parameter (`_receive_layout`), the
        defaults left to C++ checked at the substitution
        (`validate_generic_defaults`), then the layout stored on the call
        (`commit_args`). `stamp` is what the call resolves to, set before
        the receive reads it.

        The receive flags are the kind's (`receive_winner_args`); `params`,
        `declared_types`, `decided` and `inferred_slots` are by parameter
        index: a parameter received at another type than `cand.matched`
        spells, the parameters as the callee declares them, a type the
        call's preparation decided, a slot an earlier call inferred.
        `feeds`: the generic declaration's params and the ranked argument
        types as written, for `check_own_callback_feeds`. `kwargs_arg`: the
        `**kwargs` region an earlier step of the call built (None: the
        callee takes none), unless `REGION_NOT_BUILT`, built here."""
        matched, binding = cand.matched, cand.binding
        fed: dict[int, TpyType] = {}
        if feeds is not None:
            decl, typed = feeds
            fed = self.check_own_callback_feeds(
                [(i, decl[i], a, t) for (i, a), (_, t)
                 in zip(cand.evidence, binding.evidence(typed))])
        if stamp is not None:
            expr.resolved_function_info = stamp
        if isinstance(kwargs_arg, _RegionNotBuilt):
            kwargs_arg = self.kwargs_region_arg(expr, matched, binding, name)

        def receive(layout: ArgLayout) -> None:
            self._receive_layout(
                expr, cand, layout, scope=scope, params=params,
                declared_types=declared_types, decided=decided,
                inferred_slots=inferred_slots, fresh_own=fresh_own,
                char_literal_init=char_literal_init,
                builtin_form=builtin_form)
            if cand.origin is not None:
                validate_generic_defaults(
                    binding, cand.origin, cand.subst, self.type_ops,
                    lambda msg: self.ctx.error(msg, expr), layout.filled)

        layout = self.commit_args(
            expr, matched, binding,
            {k: self._resolve_inferred_type_arg(v)
             for k, v in cand.subst.items()},
            analyze=False, fill=fill, kwargs_arg=kwargs_arg,
            receive=receive)
        if fed and committed(expr):
            # The lowering reads each by where the layout puts its argument.
            at = expr.binding.at
            self.ctx.own_callback_feeds[expr] = {
                at[i]: slot for i, slot in fed.items() if at[i] is not None}
        return layout

    def _receive_layout(
            self, expr: 'TpyCallLike', cand: Candidate, layout: ArgLayout, *,
            scope: OpenCallArgs | None,
            params: 'Mapping[int, ParamInfo | tuple[str, TpyType]] | None',
            declared_types: 'Sequence[TpyType] | None',
            decided: 'Mapping[int, TpyType] | None',
            inferred_slots: 'Mapping[int, InferredSlot] | None',
            fresh_own: str, char_literal_init: bool,
            builtin_form: bool) -> None:
        """`commit_winner`'s receive: each operand of `layout` at its
        parameter of `cand.matched` (`receive_winner_args`), a `*args`
        pack's elements at its element type (`_analyze_and_pack_varargs`)."""
        matched = cand.matched
        if matched.has_variadic:
            self._analyze_and_pack_varargs(expr, matched, layout)
            return
        for arg in layout.args:
            if isinstance(arg, TpyStarUnpack):
                raise self.ctx.error(
                    f"Cannot use *unpacking: '{matched.name}' does not "
                    f"accept *args", arg)
        order = layout.binding.param_order()
        self.receive_winner_args(
            scope if scope is not None else self.args_scope(layout.args),
            layout.args,
            [decided.get(i) for i in order] if decided else None,
            [params[i] if params is not None and i in params
             else matched.params[i] for i in order],
            cand.declared, call=expr,
            declared_types=([declared_types[i] for i in order]
                            if declared_types is not None else None),
            fresh_own=fresh_own, char_literal_init=char_literal_init,
            builtin_form=builtin_form,
            inferred_slots=({j: inferred_slots[i] for j, i in enumerate(order)
                             if i in inferred_slots}
                            if inferred_slots else None),
            param_indices=order)

    def param_label(self, declared: FunctionInfo | None,
                    pname: str) -> str:
        """Parameter `pname` of callee `declared` as a diagnostic names it:
        `'a'` for a free function, and with its method for a method of any
        class (`parameter 'object' of list.append`, not a class `object`)."""
        owner = (self.ctx.registry.find_record_by_qname(
            declared.owning_type_qname)
            if declared is not None and declared.owning_type_qname else None)
        if owner is None:
            return f"'{pname}'"
        return f"parameter '{pname}' of {owner.name}.{declared.name}"

    def _receive_arg(self, i: int, args: list[TpyExpr],
                     given: TpyType | None,
                     param: 'ParamInfo | tuple[str, TpyType]', *,
                     call: 'TpyExpr | TpyStmt | None',
                     declared: FunctionInfo | None,
                     declared_types: 'list[TpyType] | None', readonly: bool,
                     fresh_own: str, char_literal_init: bool,
                     value_ptr: bool, builtin_form: bool, own_checks: bool,
                     contexts: 'list[str] | None',
                     inferred_slot: 'InferredSlot | None' = None) -> TpyType:
        """`receive_winner_args` for argument `args[i]` at `param`, under
        the receive's slot: its duties write only where it commits."""
        arg = args[i]
        pname, ptype = param
        context = (contexts[i] if contexts is not None
                   and i < len(contexts) and contexts[i] is not None
                   else f"argument '{pname}'")
        vpc = value_ptr and isinstance(ptype, PtrType)
        check_ptype = ptype.pointee if vpc else ptype
        if given is not None:
            arg_type = given
        else:
            decl = (declared_types[i] if declared_types is not None
                    and i < len(declared_types) else ptype)
            arg_type = self.expr.analyze_arg_at_param(arg, check_ptype, decl)
        if builtin_form:
            arg_type = unwrap_own(unwrap_ref_type(arg_type))
        if own_checks:
            self._maybe_coerce_empty_list_to_protocol(arg_type, check_ptype)
        arg_type = self._restore_readonly_arg(arg, arg_type, readonly)
        if (fresh_own != "keep" and isinstance(arg_type, OwnType)
                and not isinstance(check_ptype, OwnType)):
            # A fresh owned rvalue lent to a borrow parameter: codegen
            # materializes a named temporary that outlives the call.
            if fresh_own == "reject_coro":
                self._reject_coro_handle_borrow(arg, arg_type, pname)
            else:
                self._note_coro_lend(arg, arg_type, check_ptype)
            arg_type = arg_type.wrapped
        if not own_checks and isinstance(arg_type, OwnType):
            arg_type = arg_type.wrapped
        if own_checks:
            self.check_own_param(arg, arg_type, pname, check_ptype)
        skip_coerce = ((char_literal_init
                        and is_char_literal_init(check_ptype, arg_type, arg))
                       or (builtin_form and arg_type == check_ptype))
        if not skip_coerce:
            args[i] = self.compat.coerce_expr(
                arg, arg_type, check_ptype, context,
                coercion_ctx=CoercionContext.ARG,
                inferred_slot=inferred_slot,
                param=self.param_label(declared, pname))
        if vpc:
            args[i] = self._address_of_arg(args[i], arg, ptype, pname, call)
        if own_checks:
            self.mark_pending_slot_context(arg, arg_type, ptype)
        return arg_type

    def args_scope(self, args: list[TpyExpr]) -> OpenCallArgs | None:
        """The open scope of the call whose arguments are `args`, if its
        candidates were scored or its type parameters inferred."""
        for scope in reversed(self.ctx.open_call_args):
            if any(n is a for n in scope.nodes for a in args):
                return scope
        return None

    def _address_of_arg(self, source: TpyExpr, arg: TpyExpr,
                        ptype: PtrType, pname: str,
                        call: 'TpyExpr | TpyStmt | None') -> TpyExpr:
        """`@value_ptr_coercion`: the value `source` passed by address at
        `Ptr` parameter `pname`."""
        if not self.compat.is_mutable_lvalue(source):
            raise self.ctx.error(
                f"argument '{pname}' must be a variable, attribute, "
                f"or container element (not a temporary or expression)",
                call if call is not None else arg)
        if not self.ctx.slot_step_commits():
            return source
        self.compat._mark_addr_taken(source)
        inner_type = self.ctx.get_expr_type(source)
        vpc_node = TpyCoerce(
            expr=source,
            actual_type=inner_type,
            expected_type=ptype,
            coercion=VALUE_TO_PTR,
            context_kind=CoercionContext.ARG,
            context_msg=f"argument '{pname}'",
            by_step=True,
            loc=arg.loc,
        )
        self.ctx.set_expr_type(vpc_node, ptype)
        return vpc_node

    def _analyze_builtin_function_overloads(self, expr: TpyCall, overloads: list[FunctionInfo]) -> TpyType:
        """Type-check a call to a builtin function using unified FunctionInfo overloads.

        Uses two-pass overload resolution: prefer exact type matches over coercion matches.
        For generic overloads (with type_params), uses type inference.
        """
        viable = self._viable_candidates(expr, overloads)
        src = source_view(expr, peel=False)
        with self._adaptive_list_args(expr, viable) as scope:
            return self._builtin_function_overloads(expr, overloads, src,
                                                    scope)

    def _builtin_function_overloads(self, expr: TpyCall,
                                    overloads: list[FunctionInfo],
                                    src: CallSource,
                                    scope: OpenCallArgs | None) -> TpyType:
        """`src`: the arguments as written."""
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
                explicit_type_args=explicit,
                strip_ref_own=True,
                resolve_pending_view=True,
                preserve_declaration_order=False,
                src=src,
            )
        except OverloadAmbiguityError as e:
            raise self._ambiguous_overload_error(expr, overloads[0].name, e)

        arg_types = result.arg_types
        matched = result.matched

        if matched is not None:
            cand = result.candidate
            feeds = None
            subst: dict[str, TpyType | int] = {}
            if result.matched_origin is not None:
                overload, type_subst = result.matched_origin
                feeds = (overload.params, result.typed.mapped(
                    lambda _, t: unwrap_own(unwrap_ref_type(t))))
                self.expr.set_step_route(expr, GenericCallRoute(builtin=True))
                expr.inferred_type_args = tuple(
                    self._resolve_inferred_type_arg(type_subst[p])
                    for p in overload.type_params)
                subst = dict(zip(overload.type_params,
                                 expr.inferred_type_args))
            self.commit_winner(expr, cand, expr.func_name, stamp=matched,
                               scope=scope, builtin_form=True, feeds=feeds)
            self._validate_lvalue_params(expr)
            self.commit_facts(expr, matched)
            key = next((i for i, p in enumerate(matched.params)
                        if p.name == "key"), None)
            self._mark_key_lambda(cand.binding.expr(key, src)
                                  if key is not None else None)
            if result.matched_origin is not None:
                expr.representational_subst_params = (
                    self.type_ops.compute_representational_subst_params(
                        overload, expr.inferred_type_args))
                if self.ctx.slot_step_commits():
                    self.ctx.record_own_copy_instantiation(overload.root,
                                                           type_subst)
                ret = self.type_ops.substitute_type_params(
                    overload.return_type, subst)
                if isinstance(overload.return_type, UnionType):
                    orig_count = len(overload.return_type.members)
                    resolved_count = len(ret.members) if isinstance(ret, UnionType) else 1
                    if resolved_count < orig_count:
                        raise self.ctx.error(
                            f"Generic union return type '{overload.return_type}' produces duplicate "
                            f"members with these type arguments (resolves to '{ret}')",
                            expr,
                        )
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

        # abs() fallback: a user type with __abs__ (the native abs overloads
        # only cover numerics). Mirrors CPython's abs() -> x.__abs__() dispatch.
        if expr.func_name == "abs" and len(arg_types) == 1:
            inner = unwrap_readonly(unwrap_ref_type(arg_types[0]))
            if isinstance(inner, NominalType):
                abs_rec = self.ctx.registry.get_record_for_type(inner)
                abs_overloads = abs_rec.get_method_overloads("__abs__") if abs_rec else None
                if abs_overloads:
                    ret = abs_overloads[0].return_type
                    expr.resolved_function_info = FunctionInfo(
                        name="abs",
                        params=[ParamInfo("x", arg_types[0])],
                        return_type=ret,
                        cpp_template="({0}).__abs__()",
                        # Track the resolved __abs__'s readonly status so a
                        # mutating __abs__ reached via abs(x) records the same
                        # mutation-propagation edge as a direct x.__abs__() call.
                        is_readonly=abs_overloads[0].is_readonly,
                        is_builtin_function=True,
                    )
                    return ret

        # No matching overload found - try to give a helpful error.
        # A callable argument the per-candidate trial typed for itself and
        # no candidate took has only a placeholder type here: it is named by
        # its spelling, and a candidate whose slot there takes no callable
        # cannot be what the call meant, so it says nothing about the others.
        typed = result.typed
        placeholder = ({at for at in src.addresses
                        if self._is_contextual_fn_arg(src.at(at))
                        and isinstance(typed.at(at), AnyType)}
                       if result.contextual_callable_used else set())
        arg_texts = [
            (src.positional[i].name if isinstance(src.positional[i], TpyName)
             else "lambda")
            if i in placeholder else self.compat.call_type_text(t)
            for i, t in enumerate(arg_types)]
        arg_type_strs = ", ".join(
            arg_texts + [f"{k}={self.compat.call_type_text(t)}"
                         for k, t in typed.keywords])
        shape = shape_of(src)

        def shape_candidates() -> Iterator[
                tuple[FunctionInfo, list[tuple[int, int | str]]]]:
            """Each generic overload the call binds to, with where it binds
            each argument."""
            for overload in generic:
                bound = shape.binding(overload)
                if bound is None:
                    continue
                addressed = bound.addresses()
                if any(at in placeholder
                       and not _is_fn_slot_param(overload.params[i].type)
                       for i, at in addressed):
                    continue
                yield overload, addressed

        # Check for bound violations on generic overloads (give specific error).
        # Skip type args that resolved to UnknownElementType -- the only signal
        # was an empty container literal with no @type_param_default fallback;
        # "??? does not satisfy <bound>" misleads, and the downstream
        # _analyze_single_function_call retry will surface the cleaner
        # "Cannot infer type arguments" diagnostic. A bound is the reason
        # only when EVERY candidate the arguments fit was refused for one.
        bound_failures: list[tuple[TpyType, TpyType]] = []
        bound_free_fit = False
        for overload, addressed in shape_candidates():
            inferred: dict[str, TpyType] = {}
            if explicit:
                for tp, ta in zip(overload.type_params, explicit):
                    inferred[tp] = ta
            # Only an overload every argument fits in shape is refused FOR its
            # bound; one the arguments do not fit says nothing about them.
            if not all(at in placeholder
                       or self.type_ops.match_type_with_inference(
                           operand_param_type(overload.params[i]),
                           typed.at(at), inferred)
                       for i, at in addressed):
                continue
            exp = self.expr.type_hint_for(expr)
            if exp and not inferred:
                ret = overload.return_type.wrapped if isinstance(overload.return_type, OwnType) else overload.return_type
                exp = exp.wrapped if isinstance(exp, OwnType) else exp
                self.type_ops.match_type_with_inference(ret, exp, inferred)
            failed = [
                (type_arg, overload.type_param_bounds[name])
                for name, type_arg in inferred.items()
                if name in (overload.type_param_bounds or {})
                and not isinstance(type_arg, UnknownElementType)
                and not protocol_checker(type_arg, overload.type_param_bounds[name])]
            bound_failures.extend(failed)
            bound_free_fit = bound_free_fit or not failed
        if bound_failures and not bound_free_fit:
            # The failure the type's own kind does not explain is the honest
            # one: a class with no `__lt__` misses `ComparableRef`'s
            # comparison, not `ComparableValue`'s value half.
            type_arg, bound = next(
                ((t, b) for t, b in bound_failures
                 if (m := self._bound_marker(b)) is None
                 or self.protocols.type_conforms_to_protocol(t, m)),
                bound_failures[0])
            raise self.ctx.error(
                f"Type '{user_type_name(type_arg)}' does not satisfy "
                f"'{bound}' required by '{expr.func_name}'"
                f"{self._marker_bound_note(type_arg, bound, bound_failures)}",
                expr,
            )

        # For generic overloads, check for conflicting type parameter inference
        for overload, addressed in shape_candidates():
            partial: dict[str, TpyType] = {}
            conflict_param = None
            for i, at in addressed:
                if at in placeholder:
                    continue
                pname = overload.params[i].name
                ptype = operand_param_type(overload.params[i])
                arg_t = typed.at(at)
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
                    f"type parameter {tp_name} inferred as "
                    f"{user_type_name(first_t)} and {user_type_name(second_t)}",
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

        self.raise_no_match(expr.func_name, overloads, typed, src, expr,
                            arg_texts)

    def _winner_past_ranking(
            self, expr: TpyCall, name: str, candidates: list[FunctionInfo],
            result: ResolveResult, *, generic_ok: bool,
    ) -> 'FunctionInfo | Candidate | None':
        """The overload of `candidates` call `expr` binds to when the
        ranking `result` named no winner, for the caller to commit -- a
        validated generic pick as its `Candidate`; None when none takes
        it. `generic_ok`: the caller can commit a generic pick.

        A non-generic candidate the structural match takes comes first: the
        pool scored it through the undecided-container arm, and one it
        refused there is refused again by the commit it gets. Then the
        generic candidates a trial commit validates, ranked at the
        substitutions they validated with -- declaration order decides
        nothing, a tie is the call's ambiguity. Last, the one generic
        signature that takes the arguments by shape alone
        (`structural_matches`), whose commit says why it does not take
        them -- the targeted diagnostics such as unsafe_cast's "requires a
        type argument". It is all skipped when the ranking typed a lambda
        or function reference against a candidate's callable parameter:
        matching that synthesized evidence again would mislead, so the
        stashed per-candidate rejection is raised instead. It runs while
        the arguments are still open, so its pick receives them as the
        ranking's winner does."""
        if result.contextual_callable_used:
            if result.first_contextual_error is not None:
                raise result.first_contextual_error
            return None
        try:
            # No `type_ops`: the generic candidates are not ranked here.
            matched = self.ranking()._replace(type_ops=None).resolve(
                candidates, result.enriched)
        except OverloadAmbiguityError as e:
            raise self._ambiguous_overload_error(expr, name, e)
        if matched is not None:
            return matched
        failed: list[SemanticError] = []
        shaped = structural_matches(candidates, result.enriched)
        if generic_ok:
            validated: list[tuple[Candidate, FunctionInfo]] = []
            shaped_ids = {id(f) for f in shaped}
            for func in candidates:
                if not func.is_generic():
                    continue
                found = self._validated(expr, func)
                if isinstance(found, Candidate):
                    validated.append((found, found.matched))
                elif id(func) in shaped_ids:
                    failed.append(found)
            if len(validated) == 1:
                return validated[0][0]
            if validated:
                try:
                    pick = rank_overload_candidates(
                        [OverloadCandidate(sub, result.enriched)
                         for _, sub in validated], self.ranking())
                except OverloadAmbiguityError as e:
                    # Named as declared: two substitutions may read alike.
                    declared = {id(sub): c.origin for c, sub in validated}
                    raise self._ambiguous_overload_error(
                        expr, name, OverloadAmbiguityError(
                            tuple(declared.get(id(c), c)
                                  for c in e.candidates), e.converted))
                if pick.winner is None:
                    raise self._ambiguous_overload_error(
                        expr, name, OverloadAmbiguityError(
                            tuple(c.origin for c, _ in validated)))
                return validated[pick.winner][0]
        if len({tuple(p.type for p in f.params) for f in shaped}) == 1:
            return shaped[0]
        # Every signature the arguments fit by shape refused them for one
        # reason: that reason, whichever is tried first.
        if failed and len(failed) == len(shaped) and len(
                {e.message for e in failed}) == 1:
            raise failed[0]
        return None

    def _validated(self, expr: TpyCall,
                   func: FunctionInfo) -> 'Candidate | SemanticError':
        """Generic candidate `func` of call `expr`, bound and instantiated
        (`_generic_candidate`), else why it does not take the call. Its
        commit is the applicability check: run at an overload probe's slot,
        which decides types only, inside a trial that rolls back what the
        check analyzed. The pick is
        committed as this candidate: a binding and a substitution, which the
        rollback leaves valid; its commit receives the arguments anew."""
        saved = expr.resolved_function_info
        try:
            with self.ctx.trial_scope(), \
                    self.ctx.probe_slot_scope(ProbeSlot.QUERY):
                with self._adaptive_list_args(expr, [func]):
                    cand = self._generic_candidate(expr, func)
                    self._commit_generic_call(expr, cand)
        except SemanticError as e:
            return e
        finally:
            self.expr._peel_step_coercions(expr)
            expr.resolved_function_info = saved
        return cand

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
            # raise and continues, and the user sees "No matching overload"
            # instead of the specific "too many type arguments" error.
            # Mirrors the builtin overload path at calls.py:3815.
            if expr.type_args:
                generic = [f for f in func_infos if f.is_generic()]
                if generic:
                    max_tp = max(len(f.type_params) for f in generic)
                    self._validate_explicit_type_args(expr, max_tp)
            with self._adaptive_list_args(
                    expr, self._viable_candidates(expr, func_infos)) as scope:
                try:
                    result = self._resolve_call_overloads(
                        expr, func_infos,
                        is_generic_for_pool=lambda f: f.is_generic(),
                    )
                except OverloadAmbiguityError as e:
                    raise self._ambiguous_overload_error(expr, expr.func_name, e)
                if result.candidate is not None:
                    # The winner as the ranking bound and instantiated it.
                    return self._analyze_single_function_call(
                        expr, result.declared, result.candidate)

                winner = self._winner_past_ranking(
                    expr, expr.func_name, func_infos, result, generic_ok=True)
                if isinstance(winner, Candidate):
                    return self._analyze_single_function_call(
                        expr, winner.origin, winner)
                if winner is not None:
                    return self._analyze_single_function_call(expr, winner)
            self.raise_no_match(expr.func_name, func_infos, result.typed,
                                source_view(expr), expr)
        return self._analyze_single_function_call(expr, func_infos[0])

    def _marker_bound_note(
            self, type_arg: TpyType, bound: TpyType,
            failures: 'list[tuple[TpyType, TpyType]]' = ()) -> str:
        """Which half of a bound failed, when the bound asks -- itself or
        through a parent -- for a value or a reference type and that is the
        half. Why a stub asks for one is the stub's to document, not this
        message's. When the overloads that refused `type_arg` (`failures`)
        asked for BOTH halves, the type is neither, and saying only one half
        would point at the wrong fix."""
        missing = {
            m.qualified_name() for t, b in failures if t == type_arg
            for m in (self._bound_marker(b),)
            if m is not None
            and not self.protocols.type_conforms_to_protocol(t, m)}
        if len(missing) > 1:
            # A tuple / Optional / union IS a value type; what neither
            # overload admits is the class instance it holds, which the
            # value overload would copy.
            if contains_reference_type(type_arg):
                return ": " + _held_instance_copy_text(type_arg, "this call")
            return f": '{type_arg}' is neither a value type nor a reference type"
        marker = self._bound_marker(bound)
        # A bound that IS the marker already says it.
        if (marker is None or marker is bound
                or self.protocols.type_conforms_to_protocol(type_arg, marker)):
            return ""
        return f": '{type_arg}' is not {_MARKER_NOUNS[marker.qualified_name()]}"

    def _bound_marker(self, bound: TpyType) -> NominalType | None:
        """The value / reference marker protocol `bound` is or inherits,
        else None."""
        if not isinstance(bound, NominalType):
            return None
        if bound.qualified_name() in _MARKER_NOUNS:
            return bound
        info = protocol_info_of(bound)
        for parent in (info.parent_protocols if info is not None else ()):
            found = self._bound_marker(parent)
            if found is not None:
                return found
        return None

    def _ambiguous_overload_error(
        self, expr: TpyCall, func_name: str, err: OverloadAmbiguityError,
    ) -> SemanticError:
        """Build a located diagnostic from an ``OverloadAmbiguityError``.

        Turns the exception-carried tied candidates into an actionable message
        anchored at the call site; callers ``raise`` the returned error.
        """
        return self.ctx.error(
            self.compat.ambiguous_overload_text(func_name, err.candidates),
            expr)

    def _unsafe_cast_diagnostics(self, expr: TpyCall, arg_type: TpyType) -> None:
        """Targeted diagnostics for unsafe_cast when overload resolution fails."""
        if not isinstance(arg_type, PtrType):
            raise self.ctx.error(
                f"unsafe_cast() requires a pointer argument, got {arg_type}", expr)
        hint = self.expr.type_hint_for(expr)
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
            "(e.g., unsafe_cast[uint32](p) or q: Ptr[uint32] = unsafe_cast(p))", expr)

    def _analyze_single_function_call(self, expr: TpyCall, func: FunctionInfo,
                                      cand: Candidate | None = None) -> TpyType:
        """Analyze a call to a single user-defined function `func`, or to
        the winner of its overload set the ranking bound as `cand`."""
        # Handle generic functions
        if func.is_generic():
            with self._adaptive_list_args(expr, [func]):
                if cand is None:
                    cand = self._generic_candidate(expr, func)
                return self._commit_generic_call(expr, cand)

        # Reject type args on non-generic functions
        if expr.type_args or expr.type_args_parse_error:
            raise self.ctx.error(
                f"Function '{expr.func_name}' is not generic and does not accept type arguments",
                expr,
            )

        if cand is None:
            cand = candidate_of(
                source_view(expr),
                self.bind_args(expr, func.params, expr.func_name), func, func)
        self.commit_winner(expr, cand, func.name, stamp=func,
                           fresh_own="reject_coro", char_literal_init=True)
        self.commit_facts(expr, func)
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
        overload is later rejected -- and so does a receive that does not
        commit (`SemanticContext.slot_step_commits`).
        """
        if not (self.ctx.slot_step_commits()
                and isinstance(arg_type, PendingListType)
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
        if (self.pend.seedable(arg_type) is not None
                and value_family(unwrap_readonly(elem_type)) is not None):
            # The coercion to the view seeds the list's element cell
            # (`PendingNums.meets_list`).
            return
        info = self.ctx.list_literal(arg_type.literal_id)
        if info is not None and info.coerced_element_type is None:
            self.ctx.assert_slot_effect("an empty list's protocol element")
            info.coerced_element_type = elem_type

    def _typecheck_and_coerce_arg(
        self, arg: TpyExpr, pname: str, ptype: TpyType,
        func: FunctionInfo | None,
    ) -> TpyExpr:
        """One argument received at parameter `pname` of `func`
        (`receive_winner_args`) for the fixed and keyword-only parameters of
        `_analyze_and_pack_varargs` and for callable-value calls (no
        `func`). Returns the (possibly rewrapped) expression to store back
        into the args list."""
        args = [arg]
        self.receive_winner_args(None, args, None, [(pname, ptype)], func,
                                 fresh_own="reject_coro",
                                 char_literal_init=True)
        return args[0]

    def _analyze_and_pack_varargs(self, expr: TpyCall | TpyMethodCall,
                                  func: FunctionInfo,
                                  layout: ArgLayout) -> None:
        """Receive the arguments of call `expr` to variadic `func`, laid out
        as `layout` (`commit_args`): each fixed and keyword-only one at its
        parameter, each element of the `*args` pack at its element type."""
        va_idx = next(i for i, p in enumerate(func.params) if p.is_variadic)
        va_param = func.params[va_idx]
        args = layout.args
        order = layout.binding.param_order()
        # The commit packed the elements (`lay_out_args`).
        pack_at = layout.binding.at[va_idx]
        pack = args[pack_at]
        assert isinstance(pack, TpyVarargPack)

        # Vararg slot type from Span[T] (mutable *args) or Span[readonly[T]]
        # (readonly *args). Keep the readonly wrapper: it makes a readonly slot
        # accept both mutable and readonly args (adding const is safe) while a
        # mutable slot keeps rejecting readonly args, and it drives codegen's
        # varargs<T> vs varargs<const T> choice via pack.element_type.
        assert is_varargs(unwrap_ref_type(va_param.type))
        span_type = unwrap_ref_type(va_param.type)
        elem_type = span_type.type_args[0]

        # The args: [fixed_positional...] [vararg pack] [keyword-only...]
        vararg_exprs = pack.args

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
        for k in range(pack_at):
            p = func.params[order[k]]
            args[k] = self._typecheck_and_coerce_arg(args[k], p.name, p.type,
                                                     func)

        # Type-check each variadic arg against element type T
        packed: list[int] = []
        for i, arg in enumerate(vararg_exprs):
            if isinstance(arg, TpyStarUnpack):
                # *expr unpacking: validate + analyze the inner container
                # (analyze_call_arg sets the inner expr's type for codegen
                # and raises a clean diagnostic for non-unpackable shapes).
                unpacked_elem = self.expr.analyze_call_arg(
                    arg, elem_type)
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
            packed.append(i)
        elements = [vararg_exprs[i] for i in packed]
        self.receive_winner_args(
            None, elements, None, [(va_param.name, elem_type)] * len(packed),
            func, call=expr, own_checks=False,
            contexts=[f"*args element {i}" for i in packed])
        for i, element in zip(packed, elements):
            vararg_exprs[i] = element

        # Type-check the keyword-only args the call spells
        for k in range(pack_at + 1, len(args)):
            p = func.params[order[k]]
            args[k] = self._typecheck_and_coerce_arg(args[k], p.name, p.type,
                                                     func)
        pack.element_type = elem_type

    def commit_facts(self, site: 'TpyExpr | TpyStmt',
                     func: FunctionInfo | None,
                     ops: CallOperands | None = None, *,
                     receiver: TpyExpr | None = None,
                     receiver_type: TpyType | None = None) -> None:
        """The facts a committed call `site` records once, at the step
        that commits it: the borrow conflicts and mutation edges of its
        operands `ops` (by default its `committed_operands`; an operator
        or subscript dunder answering as its explicit call hands its own),
        with `receiver` the receiver's own call effects
        (`credit_implicit_receiver_call`) for a site that does not credit
        them itself; and the `@error_return` duty of its callee `func`."""
        if self.ctx.slot_step_commits():
            self._check_borrow_arg_conflicts(site, ops)
            self._record_mutation_call_edges(site, ops)
            if receiver is not None and func is not None:
                # The operator node is the call its walkers read; it
                # records no implicit call beside it.
                credit_implicit_receiver_call(
                    self.ctx, receiver, receiver_type, func.root, func.name,
                    site, record=False)
        if func is not None:
            self._check_error_return_handled(site, func)

    def _check_error_return_handled(self, expr: TpyCall | TpyMethodCall, func: FunctionInfo) -> None:
        """Check that calls to @error_return functions are inside matching try/except."""
        if func.error_return_type is None:
            return
        # Inside matching try/except (or except ReturnException catch-all)
        ctx_error_type = self.ctx.func.try_except_error_type
        if error_return_matches(ctx_error_type, func.error_return_type) or ctx_error_type == "*":
            self.ctx.func.try_except_error_handled = True
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

    def _generic_candidate(self, expr: TpyCall,
                           func: FunctionInfo) -> Candidate:
        """The one signature of generic `func` that call `expr` binds,
        bound and instantiated at the type arguments written or inferred
        from its arguments and its slot (`_infer_arg_types`)."""
        # Check for invalid type arguments (e.g., first[123](x) or first[var](x))
        if expr.type_args_parse_error:
            raise self.ctx.error(expr.type_args_parse_error, expr)

        # The omitted defaults wait for the type arguments, at which a
        # filled `T()` constructs; inference reads the written arguments
        # each parameter binds.
        written = source_view(expr)
        binding = self.bind_args(expr, func.params, expr.func_name,
                                 src=written)
        evidence = binding.evidence(written)
        slots = [slot for slot, _ in evidence]

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
                seed = self.type_ops.seed_subst_from_return_hint(
                    func, self.expr.slot_hint_for(expr),
                ).with_explicit(func.type_params, expr.type_args)
                arg_types = self._infer_arg_types(func, evidence, seed)
                type_subst = self.type_ops.infer_type_params_for_function(
                    func, arg_types, self.protocols.satisfies_bound,
                    expected_return_type=self.expr.slot_hint_for(expr),
                    explicit_type_args=expr.type_args, slots=slots,
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
                self.type_ops.substitute_type_params,
            )
        else:
            # Infer from arguments (seeded with LHS hint for nested-call hints).
            # Lets nested generic calls (e.g. `Rc.new(Box(Dog(...)))` with LHS
            # `Rc[Box[Pet]]`) see the seeded ptype as their first-pass hint --
            # the inner call's record-construction LHS-hint logic then picks up
            # T=Pet before arg-driven inference would have settled on T=Dog.
            seed = self.type_ops.seed_subst_from_return_hint(
                func, self.expr.slot_hint_for(expr))
            arg_types = self._infer_arg_types(func, evidence, seed)
            type_subst = self.type_ops.infer_type_params_for_function(
                func, arg_types, self.protocols.satisfies_bound,
                expected_return_type=self.expr.slot_hint_for(expr),
                slots=slots,
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
        return candidate_of(
            written, binding,
            self.type_ops.substitute_method_type_params(func, type_subst),
            func, type_subst, func)

    def _commit_generic_call(self, expr: TpyCall, cand: Candidate) -> TpyType:
        """Commit call `expr` to the generic function candidate `cand`
        instantiates (`commit_winner`)."""
        func = cand.origin
        assert func is not None
        type_subst = cand.subst
        # type_subst keeps literal-marked types for per-arg coercion below;
        # codegen needs them resolved so they don't reach C++ as template args.
        self.expr.set_step_route(expr, GenericCallRoute(builtin=False))
        expr.inferred_type_args = tuple(
            self._resolve_inferred_type_arg(type_subst[p]) for p in func.type_params
        )
        final_subst = dict(zip(func.type_params, expr.inferred_type_args))
        expr.representational_subst_params = (
            self.type_ops.compute_representational_subst_params(
                func, expr.inferred_type_args))

        resolved_func = cand.matched
        # TODO: replace qualified_name check with @compiler_check decorator
        if (func.qualified_name == "tpy.unsafe.unsafe_cast"
                and is_readonly_ptr(resolved_func.return_type)
                and isinstance(hint := self.expr.type_hint_for(expr), PtrType)
                and not hint.is_readonly):
            raise self.ctx.error(
                "unsafe_cast() cannot cast read-only pointer to mutable pointer (use unsafe_const_cast first)", expr
            )

        # A callable parameter's signature spells the argument's own
        # parameters, which are slots and never literals, so it takes the
        # finalized bindings; every other parameter keeps the markers its
        # literal argument coerces against.
        fn_slots = {i: (p.name, self.type_ops.substitute_param_type(
                        p.type, final_subst))
                    for i, p in enumerate(func.params)
                    if _is_fn_slot_param(p.type)}
        self.commit_winner(
            expr, cand, func.name, stamp=resolved_func, params=fn_slots,
            declared_types=[p.type for p in func.params],
            fresh_own="note_lend", char_literal_init=True)

        # Resolve return type
        resolved_return = self.type_ops.substitute_type_params(
            func.return_type, final_subst)

        # Builtin generics (e.g. iter) delegate to concrete C++ that returns
        # std::optional<T>, not T*, despite the template-committed signature.
        if func.is_builtin_function and func.type_params:
            resolved_return = strip_template_repr(resolved_return)

        # Detect duplicate union members after generic substitution:
        # e.g. T | U | str with T=U=int32 would emit std::variant<int, int, str> (ill-formed)
        if isinstance(func.return_type, UnionType):
            orig_count = len(func.return_type.members)
            resolved_count = len(resolved_return.members) if isinstance(resolved_return, UnionType) else 1
            if resolved_count < orig_count:
                raise self.ctx.error(
                    f"Generic union return type '{func.return_type}' produces duplicate members "
                    f"with these type arguments (resolves to '{resolved_return}')",
                    expr,
                )

        if self.ctx.slot_step_commits():
            # After the arguments were received: a candidate the @dispatch
            # fallback refuses leaves no instantiation behind.
            self.ctx.record_own_copy_instantiation(func.root, type_subst)
        self.commit_facts(expr, func)
        return resolved_return

    def _analyze_recursive_union_constructor(
        self, expr: TpyCall, union_type: UnionType,
    ) -> TpyType:
        """Analyze Tree(value) -- wrapping a value in a recursive union type."""
        name = expr.func_name
        self.reject_keywords(expr, name)
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

    def _ctor_group_candidate(
            self, expr: TpyCall, record: RecordInfo,
            overloads: list[FunctionInfo],
            type_subst: 'dict[str, TpyType | int] | None',
            scope: OpenCallArgs) -> Candidate:
        """Construction `expr` of `record` ranked over its `__init__`
        overload group, own or inherited, as a function's overloads are,
        and bound to the winner. `type_subst`: the record's type arguments
        where they are known (an explicit instantiation, a plain record);
        None ranks each constructor generic over the record's type
        parameters, which the winner's substitution then gives.

        A record with its own multi-stub group (the builtin exception
        family) has an init_params copy that mirrors only the FIRST stub; a
        record with no own __init__ emits `using Base::Base` and inherits
        every base ctor. Both construct against the full overload set."""
        if type_subst is None:
            views = [self._own_ctor_view(record, c) for c in overloads]
        else:
            views = [self._ctor_view(record, c, type_subst)[0]
                     for c in overloads]
        written = source_view(expr)
        # Ranked as a function's overloads are: a lambda or function
        # reference takes each candidate's callable parameter as its hint.
        try:
            result = self._resolve_call_overloads(
                expr, views,
                is_generic_for_pool=lambda f: f.is_generic(),
                src=written)
        except OverloadAmbiguityError as e:
            originals = {id(v): c for v, c in zip(views, overloads)}
            raise self._ambiguous_overload_error(
                expr, f"{record.name}.__init__", OverloadAmbiguityError(
                    tuple(originals.get(id(c), c) for c in e.candidates),
                    e.converted))
        cand = result.candidate
        if cand is None and type_subst is not None:
            # The commit takes the ranking's substitution, so a generic
            # candidate the ranking did not pick is no winner here.
            winner = self._winner_past_ranking(
                expr, record.name, views, result, generic_ok=False)
            if winner is not None:
                cand = candidate_of(
                    written, self.bind_args(expr, winner.params,
                                            f"{record.name}()", src=written),
                    winner, winner)
        if cand is None:
            self.raise_no_match(record.name, views, result.typed,
                                written, expr)
        declared = overloads[next(i for i, v in enumerate(views)
                                  if v is cand.declared)]
        subst = cand.subst if type_subst is None else dict(type_subst)
        if type_subst is None:
            matched, _ = self._ctor_view(
                record, declared, {k: self._resolve_inferred_type_arg(v)
                                   for k, v in subst.items()})
        else:
            matched = cand.matched
        return Candidate(matched, declared, cand.binding, cand.evidence,
                         subst, declared if subst else None)

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
        if record.is_typed_dict and source_view(expr).positional:
            raise self.ctx.error(
                f"TypedDict '{record.name}' only accepts keyword arguments", expr)

        written = source_view(expr)
        ctors = self.ctor_signatures(record)

        # The type arguments written: a full instantiation (`Stack[int32]()`,
        # `call_type`) or one with `_` wildcards left to inference
        # (`type_args`).
        explicit = (expr.call_type.type_args
                    if isinstance(expr.call_type, NominalType)
                    and expr.call_type.is_record else ())
        if expr.type_args_parse_error and not explicit:
            # A subscript that spells no type (`E[choose()](7)`).
            raise self.ctx.error(expr.type_args_parse_error, expr)
        spelled = explicit or expr.type_args or ()
        if spelled and len(spelled) != len(record.type_params):
            raise self.ctx.error(
                f"Record '{record.name}' expects {len(record.type_params)} "
                f"type arguments, got {len(spelled)}", expr)
        if record.is_generic() and isinstance(expr.call_type, NominalType):
            if not explicit:
                # No explicit type args -- fall through to inference section
                expr.call_type = None
            else:
                self.type_ops.validate_record_type_args(
                    expr.call_type, record, allow_type_param_ref=True,
                    loc=expr.loc)
                bound_error = self.construction_bound_error(
                    record, record.name,
                    dict(zip(record.type_params, explicit)))
                if bound_error is not None:
                    raise self.ctx.error(bound_error, expr)
        # Type-argument errors (count, bounds) report before argument ones.
        if not ctors and written.operands:
            raise self.ctx.error(self._no_init_message(record), expr)
        if expr.call_type is not None and isinstance(expr.call_type, NominalType) and expr.call_type.is_record:
            # Analyze and type-check constructor arguments with type substitution
            type_subst = self.type_ops.build_type_substitution(expr.call_type)
            cand = self._construct_at(expr, record, ctors, type_subst)
            if cand is not None:
                self._construction_facts(expr, record, cand.declared,
                                         cand.binding)
            self._stamp_construction(expr, record, expr.call_type, cand,
                                     type_subst)
            return expr.call_type
        # Generic record without explicit type args - try type inference
        if record.is_generic():
            # Validate _ wildcard type args if present
            if expr.type_args and None in expr.type_args:
                # Validate non-wildcard entries
                in_generic = self.ctx.in_generic_scope()
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
            return self.infer_generic_construction(expr, record)
        cand = self._construct_at(expr, record, ctors, {})
        # NominalType.name is the canonical declaration name (qname disambiguates
        # cross-module same-name records); an import alias here would make the
        # constructed type's `name` differ from a union member's canonical name
        # and break `==`-based identity. Codegen qualifies via the qname index,
        # not this name, so the alias is not needed for C++ rendering.
        result_type = NominalType(record.name, _module_qname=record.qualified_name())
        if cand is not None:
            self._construction_facts(expr, record, cand.declared, cand.binding)
        self._stamp_construction(expr, record, result_type, cand)
        return result_type

    @staticmethod
    def _no_init_message(record: RecordInfo) -> str:
        """Why `record(args)` cannot take the arguments: it has no
        constructor to bind them to."""
        name = record.name
        # CPython lets a `pass` exception take a message through the
        # inherited Exception(message); a return exception is a plain value
        # that carries only what it declares.
        reason = (f"'{name}' has no __init__: a return-only exception "
                  f"carries only the fields it declares")
        if record.is_return_exception and record.is_native:
            return reason
        if record.is_return_exception and not record.fields:
            return (f"{reason}; add 'message: str' and an __init__ to "
                    f"'{name}' to construct it with one")
        if record.is_native:
            # A binding's class cannot be given an __init__ by the program.
            return f"'{name}' has no __init__"
        return (f"'{name}' has no __init__; use @dataclass or define "
                f"__init__ to accept constructor arguments")

    def _construct_at(self, expr: TpyCall, record: RecordInfo,
                      ctors: list[FunctionInfo],
                      type_subst: 'dict[str, TpyType | int]'
                      ) -> Candidate | None:
        """Construction `expr` of `record` at its known type arguments
        `type_subst`, committed to the constructor of `ctors` it binds
        (ranked over a group); None for a record with no constructor, whose
        zero-argument construction is checked instead."""
        if len(ctors) > 1:
            with self.open_call_args(expr, source_view(expr).operands,
                                     record.name, scored=True) as scope:
                cand = self._ctor_group_candidate(expr, record, ctors,
                                                  type_subst, scope)
                self.commit_construction(expr, record, cand, scope=scope)
            return cand
        if ctors:
            cand = self._ctor_candidate(expr, record, ctors[0], type_subst)
            self.commit_construction(expr, record, cand)
            return cand
        if not record.has_init:
            self._validate_aggregate_zero_arg(record, expr,
                                              type_subst or None)
        return None

    def infer_generic_construction(self, expr: TpyCall,
                                   record: RecordInfo) -> TpyType:
        """The construction `expr` of generic `record` whose type arguments
        its arguments and its slot give: inferred, then completed
        (`complete_construction`), or left pending for its uses to give
        them. Its slot step runs this again (`GenericCtorRoute`), each
        argument met through its record."""
        self.expr.set_step_route(expr, GenericCtorRoute(record))
        wildcard_type_args = (expr.type_args
                              if expr.type_args and None in expr.type_args
                              else None)
        ctors = self.ctor_signatures(record)
        if len(ctors) > 1:
            return self._infer_group_construction(expr, record, ctors)
        # Bound once to its constructor, spelled in the record's own type
        # parameters; laid out once its type arguments are known.
        evidence: list[tuple[int, TpyExpr]] = []
        view: FunctionInfo | None = None
        binding: ArgBinding | None = None
        if ctors:
            view = self._own_ctor_view(record, ctors[0])
            written = source_view(expr)
            binding = self.bind_args(expr, view.params, f"{record.name}()",
                                     src=written)
            self._refuse_varargs_ctor(expr, record, view)
            evidence = binding.evidence(written)
        if record.has_init:
            # Seed type-param bindings from the LHS hint vs the record
            # pattern so nested generic-constructor chains see a
            # contextual hint on their first analysis pass -- symmetric
            # to the function/method-call seeding in
            # `_generic_candidate`. Wildcard explicit type
            # args overlay the seed at their positions; concrete explicit
            # type args take priority over both.
            seed = self.type_ops.seed_subst_from_record_pattern(
                record, self.expr.slot_hint_for(expr),
            ).with_explicit(record.type_params, wildcard_type_args)

            # The arguments the type parameters are inferred from stay
            # open until the resolved parameters decide them.
            with self.open_call_args(expr, [a for _, a in evidence],
                                     record.name, scored=False):
                arg_types = (self._infer_arg_types(view, evidence, seed)
                             if view is not None else [])
                inferred = self.type_ops.infer_type_params_for_record(
                    record, view.params if view is not None else [],
                    [(i, t) for (i, _), t in zip(evidence, arg_types)],
                    expected_type=self.expr.slot_hint_for(expr),
                    explicit_type_args=wildcard_type_args,
                )
                if inferred:
                    for k, v in list(inferred.items()):
                        inferred[k] = self._resolve_inferred_type_arg(v)
                    resolved = self.complete_construction(
                        expr, record, inferred, receive=True, binding=binding)
                    if binding is not None:
                        self._construction_facts(expr, record, ctors[0],
                                                 binding)
                    return resolved
        else:
            # No __init__ -- try contextual inference only
            result_hint = self.expr.slot_hint_for(expr)
            if result_hint is not None or wildcard_type_args is not None:
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
                    record, result_hint
                ).subst
                for tp, val in seeded.items():
                    inferred.setdefault(tp, val)
                if all(tp in inferred for tp in record.type_params):
                    return self.complete_construction(expr, record, inferred,
                                                      receive=False,
                                                      binding=binding)
        pending = self._pending_instance_of(expr)
        if pending is not None:
            # A slot that gives no more than an earlier one did (a probe's
            # step created it); its committing step is the construction's.
            if binding is not None:
                region = self._commit_pending_written(expr, record, view,
                                                      ctors[0], binding)
                if region is not None:
                    pending.kwargs_region = region
            return PendingGenericInstanceType(
                record_name=record.name,
                instance_id=pending.instance_id)
        # Inference failed -- try deferred resolution (Phase 7a)
        if self._can_defer_generic_inference(record, expr):
            return self._create_pending_generic_instance(
                record, expr, view, ctors[0] if ctors else None, binding)
        raise self.ctx.error(
            f"Cannot infer type arguments for '{record.name}'. "
            f"Please specify explicitly: {record.name}[{', '.join(record.type_params)}](...)",
            expr
        )

    def _commit_pending_written(self, expr: TpyCall, record: RecordInfo,
                                view: FunctionInfo, ctor: FunctionInfo,
                                binding: ArgBinding) -> TpyExpr | None:
        """The one analysis of a construction left pending: its fixed
        arguments; at its committing step also its `**kwargs` region (each
        routed keyword at its field, a `**expr` as itself) and its argument
        facts against declared constructor `ctor`. Returns the region,
        which its completion lays out."""
        for _, arg in binding.evidence(source_view(expr)):
            self.expr.analyze_call_arg(arg)
        if not self.ctx.slot_step_commits():
            return None
        region = self.kwargs_region_arg(expr, view, binding,
                                        f"{record.name}()")
        self._construction_facts(expr, record, ctor, binding)
        return region

    def _construction_facts(self, expr: TpyCall, record: RecordInfo,
                            ctor: FunctionInfo | None,
                            binding: ArgBinding) -> None:
        """The facts of construction `expr` its arguments pass declared
        constructor `ctor` (by `binding`), every kind of construction's,
        recorded at the expression where its step commits."""
        if (ctor is None or ctor is record.field_ctor
                or not self.ctx.slot_step_commits()):
            return
        operands = (expr.binding.operands(args=expr.args) if committed(expr)
                    else binding.operands(src=source_view(expr)))
        self.commit_facts(expr, None, CallOperands(ctor, None, operands))

    def _infer_group_construction(self, expr: TpyCall, record: RecordInfo,
                                  ctors: list[FunctionInfo]) -> TpyType:
        """Construction `expr` of generic `record` with an `__init__`
        overload group: ranked over the group, each constructor generic
        over the record's type parameters, and committed to the winner at
        the type arguments it was ranked at."""
        with self.open_call_args(expr, source_view(expr).operands,
                                 record.name, scored=True) as scope:
            cand = self._ctor_group_candidate(expr, record, ctors, None,
                                              scope)
            subst = {tp: self._resolve_inferred_type_arg(cand.subst[tp])
                     for tp in record.type_params if tp in cand.subst}
            if len(subst) != len(record.type_params):
                raise self.ctx.error(
                    f"Cannot infer type arguments for '{record.name}'. "
                    f"Please specify explicitly: {record.name}"
                    f"[{', '.join(record.type_params)}](...)", expr)
            resolved = self._construction_type(expr, record, subst)
            self.commit_construction(expr, record, cand, scope=scope)
        self._construction_facts(expr, record, cand.declared, cand.binding)
        self._stamp_construction(expr, record, resolved, cand, subst)
        return resolved

    def construction_bound_error(self, record: RecordInfo, name: str,
                                 subst: dict[str, TpyType]) -> str | None:
        """Why the type arguments `subst` of a construction of generic
        `record`, spelled `name`, break a bound of its type parameters, or
        None."""
        return type_param_bound_error(
            {p: subst[p] for p in record.type_params},
            record.type_param_bounds, name, self.protocols.satisfies_bound,
            self.type_ops.substitute_type_params)

    def _construction_type(self, expr: TpyCall, record: RecordInfo,
                           subst: dict[str, TpyType]) -> NominalType:
        """The type construction `expr` of generic `record` builds at the
        type arguments `subst`, checked against the bounds and stamped."""
        resolved = NominalType(
            record.name,
            tuple(subst[p] for p in record.type_params),
            _module_qname=record.qualified_name())
        bound_error = self.construction_bound_error(record, resolved.name,
                                                    subst)
        if bound_error is not None:
            raise self.ctx.error(bound_error, expr)
        expr.call_type = resolved
        self.ctx.set_expr_type(expr, resolved)
        return resolved

    def complete_construction(self, expr: TpyCall, record: RecordInfo,
                              subst: dict[str, TpyType], *, receive: bool,
                              commit: bool | None = None,
                              binding: ArgBinding | None = None,
                              kwargs_region: 'TpyExpr | None | _RegionNotBuilt'
                              = REGION_NOT_BUILT,
                              ) -> NominalType:
        """Construction `expr` of generic `record` completed at the type
        arguments `subst`, when its own inference finds them
        (`infer_generic_construction`) or, left pending, when a use gives
        them (`MethodAnalyzer._eagerly_resolve_pending_generic`): its type
        checked against the bounds and stamped, its arguments received at
        the substituted parameters where `receive` says it receives them,
        and its constructor's records written where its step commits
        (`commit`, by default `SemanticContext.slot_step_commits`).
        `binding`: the binding the inference read, when it did;
        `kwargs_region`: the `**kwargs` region built where the construction
        is written (a pending one's; None: it takes none)."""
        resolved = self._construction_type(expr, record, subst)
        if commit is None:
            commit = self.ctx.slot_step_commits()
        ctors = self.ctor_signatures(record)
        cand = (self._ctor_candidate(expr, record, ctors[0], subst, binding)
                if ctors else None)
        if cand is not None and (receive or commit):
            # A pending construction's committing step built its region
            # (`_commit_pending_written`) before any use completes it.
            assert kwargs_region is not None or cand.binding.kwargs is None, (
                f"{record.name}(): pending region not built")
            # Completed at a use, the construction is committed there, at
            # the type arguments the use gave.
            self.commit_construction(expr, record, cand,
                                     kwargs_arg=kwargs_region)
        pending = self._pending_instance_of(expr)
        if pending is not None and commit:
            self._settle_pending_instance(pending, resolved)
        # What the arguments pass is the construction expression's fact,
        # recorded there whatever completes it (`_construction_facts`).
        self._stamp_construction(expr, record, resolved, cand, subst,
                                 commit=commit)
        return resolved


    def _pending_instance_of(self, expr: TpyCall
                             ) -> 'PendingGenericInstanceInfo | None':
        """The pending generic instance construction `expr` created, while
        it is pending."""
        return next((info for info
                     in self.ctx.func.pending_generic_instances.values()
                     if info.expr is expr), None)

    def _settle_pending_instance(self, info: 'PendingGenericInstanceInfo',
                                 resolved: NominalType) -> None:
        """Pending generic instance `info`, completed to `resolved`, is
        pending no more: a local it was bound to takes the type."""
        # Only a local bound to the construction holds the pending type: an
        # inline construction (`Container().set(...)`) must not rebind the
        # class name in scope.
        if info.decl_line is not None:
            if self.ctx.func.current_scope:
                self.ctx.func.current_scope.define(info.variable_name, resolved)
            if self.ctx.func.current_ns:
                self.ctx.func.current_ns.bind_variable(info.variable_name, resolved)
            var_decl = self.ctx.func.var_decl_by_name.get(info.variable_name)
            if var_decl:
                self.ctx.var_types[var_decl] = resolved
            self.ctx.declared_var_types[(info.decl_line, info.variable_name)] = resolved
            self.ctx.func.inferred_generic_locals[info.variable_name] = (
                resolved, info.decl_line)
        del self.ctx.func.pending_generic_instances[info.instance_id]
        self.ctx.func.variable_to_generic_instance.pop(info.variable_name, None)

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
        # Keeps non-function scopes out: a class-level initializer's type
        # must be concrete where it is written.
        if not isinstance(self.ctx.func.current_function, TpyFunction):
            return False
        # A construction with arguments binds them to its constructor
        # (`infer_generic_construction`), which checks their count.
        return bool(self.ctor_signatures(record)) or not expr.args

    def _create_pending_generic_instance(
        self, record: RecordInfo, expr: TpyCall,
        view: FunctionInfo | None, ctor: FunctionInfo | None,
        binding: ArgBinding | None,
    ) -> PendingGenericInstanceType:
        """Create a deferred generic instance for later resolution from
        method calls; `binding` binds the construction to its constructor
        `view` (`_own_ctor_view`; `ctor` as declared), against which its
        arguments are committed once it resolves."""
        # The arguments are analyzed here, where they are written; their
        # types seed the type parameters T is not known for yet.
        evidence = (binding.evidence(source_view(expr))
                    if binding is not None else [])
        region = (self._commit_pending_written(expr, record, view, ctor,
                                               binding)
                  if binding is not None and view is not None
                  and ctor is not None else None)

        instance_id = self.ctx.pending_generic_counter
        self.ctx.pending_generic_counter += 1

        # Seed inferred dict from explicit _ wildcard type args
        inferred: dict[str, TpyType] = {}
        if expr.type_args and None in expr.type_args:
            for tp, arg in zip(record.type_params, expr.type_args):
                if arg is not None:
                    inferred[tp] = arg
        # Seed from constructor args
        if evidence:
            # Try partial inference from available args
            partial = self.type_ops.infer_type_params_for_record(
                record, view.params, [(i, self.ctx.get_expr_type(arg))
                                      for i, arg in evidence],
                expected_type=None,
            )
            if partial:
                for k, v in partial.items():
                    if isinstance(v, TpyType):
                        v = resolve_int_literals(v, self.ctx.default_int_for_literal)
                    inferred[k] = v

        info = PendingGenericInstanceInfo(
            instance_id=instance_id,
            variable_name=expr.func_name,
            record_info=record,
            type_params=list(record.type_params),
            inferred=inferred,
            expr=expr,
            kwargs_region=region,
        )
        self.ctx.func.pending_generic_instances[instance_id] = info
        return PendingGenericInstanceType(record_name=record.name,
                                          instance_id=instance_id)

    def _analyze_legacy_function_call(self, expr: TpyCall, func: FunctionInfo) -> TpyType:
        """Analyze a legacy function call (fallback path): committed as a
        call to a free function is."""
        return self._analyze_single_function_call(expr, func)

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
            **temp_call_args(expr),
            loc=expr.loc,
        )
        ret_type = self.methods.analyze_method_call(synthetic)
        # Cache the synthetic node's own type so downstream consumers keyed on
        # the node (not just the original call) resolve it -- the AST reads it
        # via get_resolved_type's fi fallback, but a raw expr-type lookup needs
        # the cache entry.
        self.ctx.set_expr_type(synthetic, ret_type)
        adopt_temp_call(expr, synthetic, type_args=False)
        expr.dunder_call = synthetic
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
                expr.args[i], f"arg{i + 1}", ptype, None)
        expr.resolved_function_info = FunctionInfo(
            name=label,
            params=[ParamInfo(f"arg{i + 1}", t) for i, t in enumerate(param_types)],
            return_type=return_type,
            is_readonly=False,
            is_callable_value=True,
        )
        self._check_borrow_arg_conflicts(expr)
        for i, ptype in enumerate(param_types):
            if i >= len(expr.args):
                break
            if isinstance(ptype, ReadonlyType):
                continue
            if not param_has_mutable_borrow_surface(ptype):
                continue
            # A bare generic slot is marked too: the template is one C++
            # function for every instantiation, and at a reference-type one
            # the callable takes `T&` and may write through it. The resize
            # mark lands on the root parameter even when only an element is
            # handed over, which over-warns
            # (BUGS.md#callable-arg-structure-effect-depth).
            mark_roots(self.ctx, storage_roots(
                expr.args[i], Use.POINTEE, self.ctx.get_expr_type,
                MayLend(self.ctx)),
                MarkRequest(through_field=True, structural=True,
                            loop_vars=True))
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
        expected_type = self.expr.type_hint_here()
        with self.ctx.slot_hint_scope(None):
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
        ctx = CallMacroContext(self.ctx, loc=loc, expected_type=expected_type)
        qname = f"{module_name}.{func_name}"
        expansion = expand_call_macro(
            macro_fn, ctx, macro_args, macro_kwargs, qname, loc)
        # The expansion holds the argument nodes themselves, evaluated where
        # it places them: they are analyzed there again, as the expansion's,
        # until a macro states how it evaluates its arguments
        # (BUGS.md#call-macro-arg-evaluated-per-field).
        for a in [*args, *kwargs.values()]:
            self.expr.forget(a)
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
            # dotted-module chain in `SemanticContext.module_named_by`.
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
