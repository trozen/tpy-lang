"""Expression eligibility gates: `_expr_eligible` and the
call/method/ctor/arg + literal/subscript/condition predicates it
recurses through (the gate half of the expression family). The
`_lower_*` arms live in `expressions.py`, which imports the handful
of gate helpers it needs from here -- the dependency is one-way.
"""

from __future__ import annotations
import math
from dataclasses import field
from ...parse.nodes import (
    FSTRING_CONV_NONE,
    FSTRING_CONV_REPR,
    FSTRING_CONV_STR,
    FunctionLinkage,
    TpyArrayLiteral,
    TpyAssert,
    TpyAssign,
    TpyAugAssign,
    TpyBinOp,
    TpyBoolLiteral,
    TpyBytesLiteral,
    TpyCall,
    TpyChainedCompare,
    TpyCoerce,
    TpyDictLiteral,
    TpyExpr,
    TpyFieldAccess,
    TpyFloatLiteral,
    TpyFString,
    TpyIfExpr,
    TpyIntLiteral,
    TpyListRepeat,
    TpyMethodCall,
    TpyName,
    TpyNoneLiteral,
    TpySetLiteral,
    TpySlice,
    TpyStrLiteral,
    TpySubscript,
    TpyTupleLiteral,
    TpyUnaryOp,
    TpyVarDecl,
    TupleElemCapture,
)
from ...typesys import (
    BOOL,
    FLOAT,
    FloatLiteralType,
    IntLiteralType,
    LiteralType,
    NominalType,
    OptionalType,
    OwnType,
    ParamInfo,
    PtrType,
    ReadonlyType,
    TpyType,
    TupleType,
    TypeParamRef,
    UnionType,
    contains_type_param,
    substitute_type_params_simple,
    del_suppresses_default_ctor,
    is_float_type,
    is_void_like_type,
    resolve_int_literals,
    unwrap_optional_own,
    unwrap_readonly,
    unwrap_ref_type,
    unwrap_send_sync,
)
from ...type_def_registry import (
    enum_info_of,
    int_traits_of,
    is_array,
    is_big_int_type,
    is_bool_type,
    is_bytes_type,
    is_bytes_view_type,
    is_dict,
    is_enum_type,
    is_fixed_int_type,
    is_float32_type,
    is_list,
    is_set,
    is_span,
    is_str_type,
    is_str_view_type,
    is_string_type,
)
from ...codegen_cpp.forms import LocalBinding, classify_local_binding
from ...value_category import is_rvalue_source
from ...codegen_cpp.context import (
    escape_cpp_name,
    imported_free_callee_cpp,
    module_qualified_callee_cpp,
    static_method_callee_cpp,
)
from ...compilation_context import get_current_compiler
from ... import qnames
from ..faces import witness as _witness
from ..fallback import expr_kind_tag, note_detail
from .generics import expand_fi_template
from ...codegen_cpp.expressions import ExpressionGenerator
from ..nodes import (
    PrintForm,
    THIRBinOp,
    THIRCall,
    THIRCtorCall,
    THIRFieldAccess,
    THIRForEach,
    THIRFormConvert,
    THIRLiteral,
    THIRMethodCall,
    THIRSelf,
    THIRStrSlice,
)
from .predicates import (
    _ARITH_OPS,
    _BITWISE_OPS,
    _COMPARE_OPS,
    _IS_OPS,
    _LOGICAL_OPS,
    _MEMBERSHIP_OPS,
    _arg_ptr_union_slot,
    _bigint_index_disposition,
    _bytes_compare_operand,
    _bytes_concat_operand,
    _char_compare_operand,
    _coerce_disposition,
    _const_exact_field_receiver_ok,
    _const_index,
    _container_pass_through_arg,
    _container_record_elem,
    _container_scalar_read,
    _container_value_leaf_read,
    _ctor_arg_slot_ok,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_ptr_value,
    _eligible_scalar,
    _eligible_value_union,
    _enum_compare_pair,
    _enum_neg_wrap,
    _enum_prop_wrap,
    _enum_truthy_wrap,
    _f1_record,
    _field_decl_type,
    _field_markers_clean,
    _field_over_subscript_ok,
    _field_receiver_ok,
    _folded_neg_int_literal,
    _is_bytes_family,
    _is_type_param_slot,
    _is_none_compare_operand,
    _is_string_owned,
    _isinstance_narrow_info,
    _member_valued_union_slot,
    _mixed_sign_compare,
    _narrow_bigint_index,
    _narrow_fact_member,
    _narrow_facts_ok,
    _nonvalue_container_ret,
    _operand_type,
    _optional_checked_field,
    _optional_field_over_subscript_ok,
    _optional_ptr_arg_face,
    _opt_view_arg_shim,
    _optional_ptr_borrow,
    _optional_ptr_borrow_name,
    _own_cascade_fires,
    _own_lvalue_temp_slot,
    _owned_str_append_target,
    _owned_str_slot,
    _peel_coerce,
    _plain_member_call_markers_ok,
    _plain_method_fi_ok,
    _plain_own_slot,
    _plain_scalar_slot,
    _positional_only_template,
    _record_rvalue_temp_slot,
    _resolved_bytes_value,
    _resolved_scalar,
    _resolved_str_value,
    _resolved_viewfam_value,
    _generic_root_subst,
    _instantiation_call_fi,
    _is_range_call,
    _range_counter_type,
    _runtime_bigint,
    _scalar_pass_through_slot,
    _slice_object_type,
    _storage_call_container,
    _storage_call_ret,
    _str_compare_operand,
    _str_concat_operand,
    _subscript_container_recv_type,
    _str_field_value_read,
    _template_init_call_fi,
    _tparam_value,
    _tuple_subscript_value_read,
    _type_family_tag,
    _union_binding_divergent,
    _unrouted_binding_read,
    _union_compare_pair,
    _unwrap_lit_coerce,
    _none_value_opt_arg,
    _value_opt_scalar,
    _value_opt_scalar_name,
    _value_opt_str,
    _value_opt_view_name,
    _value_tuple,
    _value_union_temp_slot,
    _var_decl_type,
)
from .context import (
    _Prescan,
    _WalkState,
)

def _ptr_union_source_ok(e: TpyExpr, declared: dict[str, TpyType], analyzer,
                         u: 'UnionType', *, allow_field: bool) -> bool:
    """A source expression for a pointer-variant local decl/reseat or a
    union-field write: a bare name whose binding is the SAME union (a
    borrow-form copy, rendered bare), or -- when `allow_field` -- a
    value-variant field lvalue off an F1-record receiver. At a local decl the
    field source lifts via `to_[const_]ptr_variant` and is gated to
    single-assignment locals (a reseat's const verdict comes from its own
    receiver, a mixed-const reseat chain the slice does not reproduce); at a
    field write it copies storage-to-storage with no lift, so no const
    question arises."""
    if isinstance(e, TpyName):
        bt = declared.get(e.name)
        if bt is None:
            return False
        bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
        return bt == u and _expr_eligible(e, declared, analyzer)
    if allow_field and isinstance(e, TpyFieldAccess):
        # Strict receiver: the local-decl consumer spells the receiver's
        # const verdict (see _const_exact_field_receiver_ok); the field-write
        # consumer is const-blind but shares the arm -- conservative.
        if not _const_exact_field_receiver_ok(e, declared, analyzer):
            return False
        ft = analyzer.get_expr_type(e)
        ft = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
              if ft is not None else None)
        return ft == u
    return False

def _compound_narrow_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, tuple[TpyType, ...], TpyExpr] | None':
    """The U4 compound narrowing condition: an `and` tree (`&&` TpyBinOp --
    the parser folds `a and b` to that) with EXACTLY ONE isinstance-narrow
    leaf (un-folded), every other leaf an eligible bool condition. Leaves
    AFTER the isinstance see the subject retyped to the single concrete
    member (sema narrowed their reads; they render as the inline deref);
    leaves before it see the un-narrowed subject. `or` trees and multiple
    isinstance leaves (facts on several vars) stay AST. Returns
    `(var, union, members, isinstance_leaf)` or None."""
    if not (isinstance(cond, TpyBinOp) and cond.op == "&&"):
        return None
    leaves: list[TpyExpr] = []

    def flat(e: TpyExpr) -> None:
        if isinstance(e, TpyBinOp) and e.op == "&&":
            flat(e.left)
            flat(e.right)
        else:
            leaves.append(e)

    flat(cond)
    hits = [(i, _isinstance_narrow_info(l, declared, analyzer))
            for i, l in enumerate(leaves)]
    hits = [(i, inf) for i, inf in hits if inf is not None]
    if len(hits) != 1:
        return None
    idx, (var, u, members, folded) = hits[0]
    if folded:
        return None
    after = declared
    if len(members) == 1:
        # A single concrete member: later leaves read the subject AS the
        # member (field reads gate like a record param). A tuple check
        # leaves no member type to read as -- later leaves see the union.
        after = dict(declared)
        after[var] = members[0]
    for i, leaf in enumerate(leaves):
        if i != idx and not _condition_eligible(
                leaf, after if i > idx else declared, analyzer):
            return None
    return var, u, members, leaves[idx]

def _narrow_cond_info(
        cond: TpyExpr, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, tuple[TpyType, ...], bool, TpyExpr | None] | None':
    """A narrowing if/while/assert condition, simple or compound:
    `(var, union, members, folded, isin_leaf)`. `isin_leaf` is None for the
    simple form (the whole condition is the isinstance test); a compound
    condition is never sema-folded (folded=False)."""
    info = _isinstance_narrow_info(cond, declared, analyzer)
    if info is not None:
        return (*info, None)
    c = _compound_narrow_info(cond, declared, analyzer)
    if c is None:
        return None
    var, u, members, isin = c
    return var, u, members, False, isin

def _assert_narrow_info(
        stmt: TpyAssert, declared: dict[str, TpyType], analyzer,
) -> 'tuple[str, UnionType, TpyType] | None':
    """The U4 first-narrow assert: `assert isinstance(v, A)` on an
    un-narrowed routed-union subject with a concrete member fact. The AST
    emits the negated holds test + a PERSISTENT extraction alias
    (`_gen_assert` -> `_emit_isinstance_extractions(persistent=True)`), and
    the narrowing holds for the rest of the enclosing scope. Returns
    `(var, union, member)`, or None for every other assert shape (a plain
    assert, a union fact -- which extracts nothing -- or a sema-folded
    condition, the re-assert arm)."""
    info = _narrow_cond_info(stmt.condition, declared, analyzer)
    if info is None:
        return None
    var, u, _members, folded, _isin = info
    if folded or not _narrow_facts_ok(u, stmt.then_type_facts, var):
        return None
    m = _narrow_fact_member(u, stmt.then_type_facts, var)
    if m is None:
        return None
    return var, u, m

def _enum_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """An enum value (name / member access / field read) into a same-enum
    by-value param slot: enums are value scalars for the ownership cascade
    (`own is None`), so both paths render the bare expression. An `Own[enum]`
    slot rejects (`_eligible_enum` does not peel Own)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    et = _eligible_enum(pt, analyzer)
    if et is None:
        return False
    at = _eligible_enum(analyzer.get_expr_type(a), analyzer)
    return at == et and _expr_eligible(a, locals_, analyzer)

def _enum_truthy_operand(e: TpyExpr, locals_: dict[str, TpyType],
                         analyzer) -> bool:
    """An enum-typed truthiness operand (an if/while/assert condition or a
    `not` operand): an eligible NAME / member-access / field-read. A CALL
    operand is rejected even where the bytes would match: the AST drops a
    plain enum's operand render entirely (`if f():` -> `if (true)`, losing
    the call's side effects -- BUGS.md), a miscompile the slice does not
    mirror."""
    if not isinstance(e, (TpyName, TpyFieldAccess)):
        return False
    if _enum_truthy_wrap(analyzer.get_expr_type(e), analyzer) is None:
        return False
    return _expr_eligible(e, locals_, analyzer)

def _enum_from_value_eligible(e: TpyCall, locals_: dict[str, TpyType],
                              analyzer) -> bool:
    """`E(x)` -- sema's BindingKind.ENUM value lookup, rendered
    `::tpy::EnumUtil<E>::from_value(x)`. A NON-literal runtime-BigInt arg
    takes the checked `({0}).to_fixed_check<U>()` wrap over the enum's
    underlying type; a LITERAL arg resolving BigInt (a BigInt module default)
    stays rejected -- the AST wraps the literal's `::tpy::BigInt(...)` render,
    a shape the bare-literal emit does not reproduce."""
    et = e.enum_from_value
    if et is None or _eligible_enum(et, analyzer) is None:
        return False
    if len(e.args) != 1 or e.kwargs:
        return False
    at = analyzer.get_expr_type(e.args[0])
    if not _resolved_scalar(at, analyzer):
        return False
    if (_runtime_bigint(at, analyzer)
            and _const_index(_unwrap_lit_coerce(e.args[0])) is not None):
        return False
    return _expr_eligible(e.args[0], locals_, analyzer)

def _cast_passthrough_eligible(e: TpyCall, locals_: dict[str, TpyType],
                               analyzer) -> bool:
    """`typing.cast(T, x)` on a non-Any source: a compile-time no-op whose
    _gen_call arm renders the bare `gen_expr(args[1])` (the target type is NOT
    threaded into the source). Mirrored by lowering args[1] standalone. An Any
    source takes the `any_cast_or_panic<T>` wrap -- a shape the slice does not
    reproduce -> AST path."""
    if e.cast_target_type is None or e.cast_source_is_any:
        return False
    if len(e.args) != 2 or e.kwargs or e.double_star_unpack is not None:
        return False
    return _expr_eligible(e.args[1], locals_, analyzer)


def _macro_expansion_eligible(e: TpyCall, locals_: dict[str, TpyType],
                              analyzer) -> bool:
    """A `@call_macro` / getattr / hasattr call whose sema-synthesized
    replacement expr `gen_expr(macro_expansion, target)` is what the AST emits
    (the call node itself renders nothing). Any eligible expansion mirrors by
    lowering it in place."""
    if e.macro_expansion is None:
        return False
    return _expr_eligible(e.macro_expansion, locals_, analyzer)


def _nested_enum_from_value_eligible(e: TpyMethodCall,
                                     locals_: dict[str, TpyType],
                                     analyzer) -> bool:
    """`Outer.Kind(v)` -- the nested-enum value lookup (a method-call SHAPE:
    sema marks it is_nested_enum_constructor; _gen_method_call renders
    `::tpy::EnumUtil<Outer::Kind>::from_value(v)`). Arg pins mirror
    `_enum_from_value_eligible`: one positional scalar, no runtime-BigInt
    (the checked `.to_fixed_check` narrow is a deferred row)."""
    if not (e.is_nested_enum_constructor and e.nested_type_name):
        return False
    if e.kwargs or e.double_star_unpack is not None or len(e.args) != 1:
        return False
    et = analyzer.registry.get_enum(e.nested_type_name)
    if et is None or _eligible_enum(et, analyzer) is None:
        return False
    at = analyzer.get_expr_type(e.args[0])
    return (_resolved_scalar(at, analyzer)
            and not _runtime_bigint(at, analyzer)
            and _expr_eligible(e.args[0], locals_, analyzer))

def _container_literal_decl_ok(stmt: TpyVarDecl, declared: dict[str, TpyType],
                               prescan: '_Prescan', analyzer) -> bool:
    """First decl of a container-literal local: `xs = [1, 2]` / `xs: list[T] = []`
    / `d = {k: v}` / `s = {a, b}`. The decl's binding type is sema's RESOLVED
    container (a list literal's vector-vs-array decision -- the PendingListType
    resolution -- is final before lowering), so the emit is a pure function of
    that type + the elements. Element families and the per-slot rules live in
    `_container_lit_elem_ok` (scalars, owned str/bytes, enums,
    Optional[scalar], value tuples, nested list literals, F1 records); the
    `make_vector`/`make_ordered_*` move/nocopy switch is mirrored at lowering
    off the same `movable_locals` + last-use facts the AST reads. A `[0] * n`
    repeat (TpyListRepeat) stays on the AST path. The empty-literal-to-Array
    reject is defensive-only: sema errors on both routes to that shape (a bare
    `[]` is un-inferable; an `Array[T, 0]` annotation mismatches the literal),
    so only the empty LIST form (the spelled `std::vector<T>{}` emit) is
    reachable."""
    # A reassigned container local is a POINTER-LOCAL on the AST path (`a = b`
    # rebinds the alias -- `std::vector<T>* a = &__slot_N; ... a = &(b);` -- so a
    # later `a.append` mutates the aliased list, Python's rebinding semantics).
    # The plain value decl this cell emits would silently copy instead; reject
    # (hoisted / move-through conservatively ride along).
    is_lit = isinstance(stmt.init, (TpyArrayLiteral, TpyDictLiteral,
                                    TpySetLiteral))
    if (stmt.name in prescan.reassigned or stmt.name in prescan.hoisted
            or stmt.name in prescan.move_through):
        return note_detail("container_lit.rebound") if is_lit else False
    t = _var_decl_type(stmt, analyzer)
    if t is None:
        return note_detail("container_lit.decl_type") if is_lit else False
    return _container_literal_ok(stmt.init, t, declared, analyzer, note=True)

def _container_literal_ok(init: TpyExpr, t: TpyType, declared: dict[str, TpyType],
                          analyzer, *, note: bool = False,
                          threaded: bool = True) -> bool:
    """The container-literal eligibility shared by the decl-init gate, the
    storage-container return arm, and the nested-element recursion: the
    literal's family matches the RESOLVED slot `t` and every element/key/value
    is admitted against its slot by `_container_lit_elem_ok` (value scalars,
    owned str/bytes, enums, Optional[scalar], value tuples, F1 records,
    nested list literals). An empty literal is only the LIST form (its
    `std::vector<T>{}` spell is position-independent -- the same render at a
    decl init and a return).

    `threaded` says whether the AST render of THIS literal received a target
    (gen_expr_deref's elem_target threading): True at a decl init / return /
    Array element / dict value, False for a literal nested in a LIST element
    slot (elem_target is None there) -- the empty-literal spell and the
    view->owned element wraps exist only on the threaded side, so the
    un-threaded recursion restricts those shapes.

    `note` (the decl gate only) records the `container_lit.*` sub-classifier
    detail on a family/slot reject, so the fallback tally splits the residue
    by blocking element family; element-EXPR rejects keep the detail
    `_expr_eligible` records."""
    if isinstance(init, TpyDictLiteral):
        args = getattr(t, "type_args", None)
        if not is_dict(t) or not args or len(args) < 2:
            return _note_container_lit_reject(init, t, analyzer) if note else False
        key, val = args[0], args[1]
        # Keys keep the receiver-slice rule (fixed-int / BigInt / owned str);
        # a view-typed key pins its literals to static storage on the AST
        # path, a render this slice does not reproduce.
        if not (is_fixed_int_type(key) or _runtime_bigint(key, analyzer)
                or _owned_str_slot(key, analyzer)):
            if note:
                fam = _container_lit_slot_family(key, analyzer) or "scalar"
                return note_detail(f"container_lit.key.{fam}")
            return False
        # _gen_dict_literal threads k_type/v_type into every render
        # position-independently, so keys and values are always threaded.
        return (all(_expr_eligible(k, declared, analyzer) for k in init.keys)
                and all(_container_lit_elem_ok(v, val, declared, analyzer,
                                               threaded=True, forced=True,
                                               allow_nested=True,
                                               allow_optional=True, note=note)
                        for v in init.values))
    if isinstance(init, TpySetLiteral):
        args = getattr(t, "type_args", None)
        if not (is_set(t) and bool(args)):
            return _note_container_lit_reject(init, t, analyzer) if note else False
        # Set elements: scalars / owned str / enums / value tuples; records,
        # Optional and nested containers stay tagged (hash/emit shapes the
        # slice does not reproduce).
        return all(_container_lit_elem_ok(x, args[0], declared, analyzer,
                                          threaded=True, forced=True, note=note)
                   for x in init.elements)
    if isinstance(init, TpyArrayLiteral):
        if is_dict(t) or is_set(t):
            return _note_container_lit_reject(init, t, analyzer) if note else False
        if not init.elements:
            if not is_list(t):
                # Defensive: sema errors on both routes to an empty Array.
                return note_detail("container_lit.empty_array") if note else False
            if not threaded:
                # An un-threaded empty renders bare `{}` on the AST (no elem
                # target); the spelled THIR emit would diverge.
                return note_detail("container_lit.nested_empty") if note else False
        args = getattr(t, "type_args", None)
        if is_span(t):
            # Span slots keep the scalar-only receiver rule (mirrors
            # _container_scalar_read's span arm).
            return (bool(args) and _eligible_scalar(unwrap_readonly(args[0]))
                    and all(_expr_eligible(x, declared, analyzer)
                            for x in init.elements))
        if not (is_list(t) or is_array(t)) or not args:
            return _note_container_lit_reject(init, t, analyzer) if note else False
        # A demoted Array threads every element target; a list threads only
        # the special slot families (str/bytes/Optional/tuple), which
        # _container_lit_elem_ok derives per-slot from `forced`.
        return all(_container_lit_elem_ok(x, args[0], declared, analyzer,
                                          threaded=threaded, forced=is_array(t),
                                          allow_record=True, allow_nested=True,
                                          allow_optional=True, note=note)
                   for x in init.elements)
    return False

def _container_lit_elem_ok(e: TpyExpr, slot: 'TpyType | None',
                           declared: dict[str, TpyType], analyzer, *,
                           threaded: bool, forced: bool,
                           allow_record: bool = False,
                           allow_nested: bool = False,
                           allow_optional: bool = False,
                           note: bool = False) -> bool:
    """One container-literal element / dict value against its RESOLVED slot.

    `threaded` = the parent literal itself was rendered with a target;
    `forced` = the parent position threads every element target regardless of
    family (a demoted Array's elements, a dict's keys/values) -- a LIST
    threads only the special families (str/bytes/Optional/tuple), so a nested
    list element is threaded only under `forced`. The wrap-relevant arms
    (view->owned copies, the target-typed None/tuple renders) require their
    position to be threaded; the target-free families (scalar, enum, record)
    render identically either way.

    Reject arms record the permanent `container_lit.elem.*` drilldown detail
    (`note`, first-reject-wins) so the fallback tally names the blocking
    element family."""
    if slot is None:
        return note_detail("container_lit.slot_family") if note else False
    fam = _container_lit_slot_family(slot, analyzer)
    if fam is None:  # value scalar / owned str (the original S5 slice)
        if (not threaded and _owned_str_slot(slot, analyzer)
                and not isinstance(e, TpyStrLiteral)):
            # An un-threaded str slot gets no elem target on the AST path, so
            # the S5 view->owned wrap does not fire there; only the
            # form-neutral literal render is byte-identical.
            return note_detail("container_lit.nested_view") if note else False
        return _expr_eligible(e, declared, analyzer)
    su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    if fam == "bytes":
        bt = _resolved_bytes_value(su, analyzer)
        if bt is None or not is_bytes_type(bt):
            return note_detail("container_lit.elem.bytes") if note else False
        if not threaded and not isinstance(e, TpyBytesLiteral):
            # Mirrors the un-threaded str rule: the `::tpy::bytes_copy` wrap
            # fires only at threaded positions (a bytes literal renders its
            # owned form target-free, so it stays admitted).
            return note_detail("container_lit.nested_view") if note else False
        return _expr_eligible(e, declared, analyzer)
    if fam == "enum":
        if (_eligible_enum(analyzer.get_expr_type(e), analyzer) is not None
                and _expr_eligible(e, declared, analyzer)):
            return True
        return note_detail("container_lit.elem.enum") if note else False
    if fam == "optional":
        # Value-repr Optional element slot. Compositional (mirrors
        # `_tuple_literal_element_ok`): a bare `None` renders `std::nullopt`,
        # and any other source routes iff the element EXPRESSION routes -- the
        # inner value's storage form (scalar bare, owned-str view->owned wrap)
        # is a pure function of the slot type that `_lower_container_elem`
        # threads through the Optional inner identically to the AST's implicit
        # `T -> std::optional<T>` conversion. The pointer-repr guard keeps a
        # record/container inner out (`std::variant<T*,...>` -- a different
        # storage-form lift); `threaded` gates the wrap-bearing str inner.
        if not (allow_optional and threaded and isinstance(su, OptionalType)
                and not su.uses_pointer_repr()):
            return note_detail("container_lit.elem.optional") if note else False
        if isinstance(e, TpyNoneLiteral):
            return True  # -> std::nullopt (the STORAGE-form None)
        return (_expr_eligible(e, declared, analyzer)
                or (note_detail("container_lit.elem.optional") if note else False))
    if fam == "tuple":
        vt = _value_tuple(su, analyzer)
        if vt is None or not threaded:
            return note_detail("container_lit.elem.tuple") if note else False
        # Tuple LITERAL elements only: a value-tuple NAME could be an
        # owned-movable tuple param in the AST's movable set
        # (seed_param_locals), which lc.movable_locals deliberately does not
        # mirror -- the make_vector switch would diverge silently.
        return (_tuple_literal_ok(e, vt, declared, analyzer)
                or (note_detail("container_lit.elem.tuple") if note else False))
    if fam == "container":
        if not (allow_nested and (is_list(su) or is_array(su))
                and isinstance(e, TpyArrayLiteral)):
            return note_detail("container_lit.elem.container") if note else False
        return _container_literal_ok(e, su, declared, analyzer, note=note,
                                     threaded=threaded and forced)
    if fam == "record":
        if not (allow_record and _f1_record(su, analyzer)):
            return note_detail("container_lit.elem.record") if note else False
        if isinstance(e, TpyName):
            # A bare record name copies (brace-init), derefs for an F2
            # pointer-local, and moves at a movable local's last use -- all
            # mirrored at lowering off the same facts the AST reads.
            bt = declared.get(e.name)
            if (bt is not None and _f1_record(bt, analyzer)
                    and _expr_eligible(e, declared, analyzer)):
                return True
            return note_detail("container_lit.elem.record") if note else False
        return (_is_record_rvalue_source(e, declared, analyzer)
                or (note_detail("container_lit.elem.record") if note else False))
    return note_detail(f"container_lit.elem.{fam}") if note else False

def _container_lit_slot_family(t: 'TpyType | None', analyzer) -> 'str | None':
    """Family tag for one container-literal element/key/value slot: None for
    the base slice (value scalar / owned str), else the family name. Doubles
    as `_container_lit_elem_ok`'s dispatch key and the permanent
    `container_lit.elem.*` drilldown sub-classifier for the rejected
    families."""
    if t is None:
        return "untyped"
    if _eligible_scalar(t) or _owned_str_slot(t, analyzer):
        return None
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OptionalType):
        return "optional"
    if isinstance(u, UnionType):
        return "union"
    if isinstance(u, TupleType):
        return "tuple"
    if is_list(u) or is_dict(u) or is_set(u) or is_array(u) or is_span(u):
        return "container"
    if is_enum_type(u):
        return "enum"
    if is_str_view_type(u) or is_bytes_view_type(u):
        return "view"
    if is_bytes_type(u):
        return "bytes"
    if isinstance(u, NominalType) and u.is_user_record:
        return "record"
    return "other"

def _note_container_lit_reject(init: TpyExpr, t: TpyType, analyzer) -> bool:
    """Record the family-level `container_lit.*` sub-classifier detail for a
    decl-gate reject (always returns False, like `note_detail`): the DECL/slot
    type is outside the mirrored container families -- an `Own[container]`
    binding, or a union/Optional/protocol target / literal-vs-family mismatch
    (`slot_family`). Per-SLOT rejects are tagged by `_container_lit_elem_ok`."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, OwnType):
        return note_detail("container_lit.own")
    return note_detail("container_lit.slot_family")

def _container_record_elem_subscript(e: TpyExpr, locals_: dict[str, TpyType],
                                     analyzer) -> bool:
    """A container subscript `c[i]` / `d[k]` whose element/value is a plain
    F1-record (`_container_record_elem`): `::tpy::__getitem__(c, k)` yields
    `T&` (or the bounds-safe operator[] lvalue) -- a borrow usable as a
    field-access receiver (`ps[i].x` / `ps[i].x = v`, `.` access -- never
    `->`) or a REF_ALIAS borrow-local source (`p = ps[i]` -> `P& p = ...`).
    The container analog of the tuple `_subscript_record_field_recv`.
    Receivers are the shared subscript set (`_subscript_container_recv_type`:
    an in-scope name or a one-level field off an admitted receiver);
    `Optional`-element containers reject at `_f1_record` (the AST wraps those
    reads differently)."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    if e.slice_function_info is not None or isinstance(e.index, TpySlice):
        return False
    recv_t = _subscript_container_recv_type(e.obj, locals_, analyzer)
    if recv_t is None or not _container_record_elem(recv_t, analyzer):
        return False
    return (_bigint_index_disposition(e.index, analyzer) != "reject"
            and _expr_eligible(e.index, locals_, analyzer))

def _field_over_container_subscript_ok(e: TpyExpr, locals_: dict[str, TpyType],
                                       analyzer) -> bool:
    """A field access off a record-element CONTAINER subscript (`ps[i].field`):
    the receiver `ps[i]` is a plain-record borrow lvalue, so the access renders
    `::tpy::__getitem__(ps, i).field` on both paths (`.` -- `_field_is_arrow`'s
    subscript arm yields `->` only for borrow-`T*` tuple elements). Position-
    neutral like the tuple twin (`_field_over_subscript_ok`): a read (RHS) and
    a scalar-field write target (LHS) render off the same receiver. Markers-
    clean excludes the Optional null-check / property / setattr shapes."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _container_record_elem_subscript(e.obj, locals_, analyzer))

def _value_field_chain_recv_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                               analyzer) -> bool:
    """`recv` is a field-access chain of plain value F1-record fields bottoming
    out at a bare-name F1-record (or proven Optional-ptr borrow) receiver --
    `o.mid`, `self.a.b`. Each link is markers-clean and reads a plain value
    F1-record (no Optional / container / non-F1 intermediate), so it renders
    its own `.`/`->` per its immediate receiver, exactly as the AST's per-link
    `_gen_field_access`. Recursive: `_lower_expr` lowers the terminal field read
    by recursing through the receiver, so admission mirrors that recursion --
    no extra emit."""
    if not isinstance(recv, TpyFieldAccess) or not _field_markers_clean(recv):
        return False
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(recv))))
    if isinstance(ft, OwnType):
        ft = unwrap_readonly(ft.wrapped)
    if not (isinstance(ft, NominalType) and _f1_record(ft, analyzer)):
        return False
    if isinstance(recv.obj, TpyName):
        # Bottom link: `recv` is a field off a bare-name F1 / Optional-ptr
        # borrow receiver -- `_field_receiver_ok`'s admitted set.
        return _field_receiver_ok(recv, locals_, analyzer)
    return _value_field_chain_recv_ok(recv.obj, locals_, analyzer)

def _field_over_field_ok(e: TpyExpr, locals_: dict[str, TpyType],
                         analyzer) -> bool:
    """A value scalar/Char/enum/typeparam/Ptr field read (the terminal type is
    gated by the caller) whose receiver is a value F1-record field chain
    (`o.mid.inner.v`, `self.a.b`). THIR lowers it by recursing `_lower_expr`
    through the receiver, so each link's arrow is decided locally like the AST;
    the chain reads byte-identically. Deeper Optional / container / subscript
    links stay on the AST path (handled by the sibling field-over-subscript
    arms or deferred)."""
    return (isinstance(e, TpyFieldAccess) and _field_markers_clean(e)
            and _value_field_chain_recv_ok(e.obj, locals_, analyzer)
            and _witness("field.chain_recv"))

def _container_subscript_value_read(e: TpyExpr, locals_: dict[str, TpyType],
                                    analyzer) -> bool:
    """A container subscript read `c[i]` off an in-scope container name -- or a
    one-level container FIELD off an admitted receiver name (`self.xs[i]` /
    `h.d[k]` / `p->xs[0]`; the receiver renders as its own THIRFieldAccess
    inside the same subscript emit) -- whose element/value is a value scalar or
    a str-slice value (`::tpy::__getitem__(c, i)`, or the bounds-safe
    `c[static_cast<std::size_t>(i)]`). The index is any eligible value-scalar
    expr, or -- for an owned-str-keyed dict -- any eligible str-slice expr (a
    literal / name / concat renders bare in the key slot; the static-storage
    pin fires only for view-typed keys, which the receiver gate excludes). A
    `readonly[container]` receiver routes too (byte-identical) -- sema
    readonly-wraps only non-value elements, so a scalar element read is never
    `readonly[scalar]`; the result check is a defensive guard confirming the
    read yields a value scalar / str value (redundant with the element check
    today, robust if the container predicate later widens). The receiver type
    comes from `_subscript_container_recv_type` -- the declared binding for a
    name, the DECLARED field type for a field -- so a narrowed Optional/union
    field receiver (the AST's `(*recv.field)` unwrap) rejects at the family
    check; deeper chains (`a.b.c[i]`) and narrowed/checked receivers stay on
    the AST path."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    recv_t = _subscript_container_recv_type(e.obj, locals_, analyzer)
    if recv_t is None:
        return False
    ret = analyzer.get_expr_type(e)
    # A runtime-BigInt index takes gen_index_expr's `.to_fixed_check<int32_t>()`
    # narrow (`_narrow_bigint_index`); only the out-of-int32-range literal
    # disposition rejects. The element family is the compositional value-leaf
    # read set (`_container_value_leaf_read`: scalar / Char / enum / Ptr value,
    # or an owned str/bytes STORAGE lvalue -- all landing bare); the result
    # check re-confirms the same leaf form (redundant today, robust if the
    # container predicate later widens).
    if not _container_value_leaf_read(recv_t, analyzer):
        return False
    return ((_resolved_scalar(ret, analyzer)
             or _eligible_char(ret)
             or _eligible_enum(ret, analyzer) is not None
             or _eligible_ptr_value(ret, analyzer)
             or _resolved_str_value(ret, analyzer) is not None
             or _resolved_bytes_value(ret, analyzer) is not None)
            and _bigint_index_disposition(e.index, analyzer) != "reject"
            and _expr_eligible(e.index, locals_, analyzer))

def _str_subscript_char_read(e: TpyExpr, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A str subscript read `s[i]` -> Char off a str-family receiver:
    `::tpy::__getitem__(s, i)` (str's `__getitem__` @cpp_template spells the
    same checked dunder as the container arm), or the bounds-safe
    `s[static_cast<std::size_t>(i)]` / literal `s[0]`. The receiver shapes are
    the shared slice/iteration set (`_str_slice_receiver_ok`: an in-scope name,
    a str-family field off an F1-record receiver, an eligible str-returning
    call -- the receiver renders bare into the dunder / operator[] either way,
    and `bounds_safe` is a carried node fact); the index is any eligible
    value-scalar expr (a runtime-BigInt index takes the
    `.to_fixed_check<int32_t>()` narrow via `_narrow_bigint_index`). The slice
    form (`s[a:b]`, slice_function_info) has its own gate; bytes has its
    `::tpy::bytes_getitem` twin (`_bytes_subscript_read`, name receivers
    only)."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    if e.slice_function_info is not None:
        return False
    if not _str_slice_receiver_ok(e.obj, locals_, analyzer):
        return False
    return (_eligible_char(analyzer.get_expr_type(e))
            and _bigint_index_disposition(e.index, analyzer) != "reject"
            and _expr_eligible(e.index, locals_, analyzer))

def _bytes_subscript_read(e: TpyExpr, locals_: dict[str, TpyType],
                          analyzer) -> bool:
    """A bytes subscript read `b[i]` -> UInt8 off an in-scope bytes-family
    name -- or a one-level bytes-family field off an F1-record receiver
    (`self.data[i]`, mirroring the str twin's field admission):
    `::tpy::bytes_getitem(b, i)` -- bytes' `__getitem__(Int32)` is a
    @native free-function dunder, NOT the containers' `::tpy::__getitem__`
    checked template, so the emit dispatches on the bytes receiver -- or the
    bounds-safe `b[static_cast<std::size_t>(i)]` / literal `b[0]` shared with
    the container arm. Index constraints mirror the str twin
    (`_str_subscript_char_read`): an eligible value-scalar index (a
    runtime-BigInt index narrows via `_narrow_bigint_index`)."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    if e.slice_function_info is not None:
        return False
    recv = e.obj
    if isinstance(recv, TpyName):
        if (recv.name not in locals_
                or _resolved_bytes_value(locals_[recv.name], analyzer) is None):
            return False
    elif isinstance(recv, TpyFieldAccess):
        if not (_field_receiver_ok(recv, locals_, analyzer)
                and _resolved_bytes_value(analyzer.get_expr_type(recv),
                                          analyzer) is not None):
            return False
    else:
        return False
    return (_eligible_scalar(analyzer.get_expr_type(e))
            and _bigint_index_disposition(e.index, analyzer) != "reject"
            and _expr_eligible(e.index, locals_, analyzer))

def _slice_bound_ok(b: 'TpyExpr | None', locals_: dict[str, TpyType],
                    analyzer) -> bool:
    """A str-slice bound: absent (-> `std::nullopt`), or an eligible fixed-int
    value expr rendered bare into the BasicSlice initializer (the AST's
    `_gen_slice_bound` is a target-less gen_expr_deref, so the expression
    render is position-neutral), or a NON-literal runtime-BigInt expr taking
    the `.to_fixed_check<int32_t>()` narrow. A LITERAL bound resolving BigInt
    (a BigInt module default int) stays rejected: the AST wraps the bare digit
    token (`1.to_fixed_check<...>()`, no `_is_int_constant` exemption here),
    which is ill-formed C++ (one pp-number token) -- see the BUGS.md entry;
    mirroring it would just reproduce the build failure."""
    if b is None:
        return True
    if not _expr_eligible(b, locals_, analyzer):
        return False
    bt = analyzer.get_expr_type(b)
    if bt is None:
        return False
    if _runtime_bigint(bt, analyzer):
        return _const_index(_unwrap_lit_coerce(b)) is None
    bt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
    return is_fixed_int_type(
        resolve_int_literals(bt, analyzer.ctx.default_int_for_literal))

def _str_slice_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                           analyzer) -> bool:
    """A str/bytes-family slice/iteration receiver rendered bare into the
    resolved template's `{self}` slot: an in-scope str/bytes name, a
    str/bytes-family field off an F1-record receiver (`h.name` / `p->name` --
    an lvalue, like a name), or an eligible owned/view-returning call
    (`full(s)[0:2]` -- the temporary lives to the end of the full expression
    on both paths, and sema resolves any BINDING of the resulting view owned,
    so the view->owned copy (`std::string(...)` / `::tpy::bytes_copy(...)`)
    materializes it before the temporary dies -- no view outlives it)."""
    if isinstance(recv, TpyName):
        return (recv.name in locals_
                and _resolved_viewfam_value(locals_[recv.name],
                                            analyzer) is not None)
    if isinstance(recv, TpyFieldAccess):
        return (_field_receiver_ok(recv, locals_, analyzer)
                and _resolved_viewfam_value(analyzer.get_expr_type(recv),
                                            analyzer) is not None)
    if isinstance(recv, TpyCall):
        return (_call_eligible(recv, locals_, analyzer)
                and _resolved_viewfam_value(analyzer.get_expr_type(recv),
                                            analyzer) is not None)
    return False

def _str_slice_read(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """A str/bytes slice off a str/bytes-family receiver -> the resolved slice
    `__getitem__`'s @cpp_template over the slice argument (see `THIRStrSlice`
    for the three index shapes; bytes carries `::tpy::bytes_slice` /
    `::tpy::bytes_stepped_slice` on the same node). Non-stepped `s[a:b]`
    yields a `std::string_view` / `std::span<const uint8_t>` VIEW result
    (BORROW form), consumed at view sinks (view local, view reassign,
    print/compare/len-free positions) or materialized at an owned sink: a str
    owned sink arrives as a sema `strview_to_str` TpyCoerce, lowered via
    `_coerce_disposition` to the view->owned THIRFormConvert
    (`std::string(...)` around the slice); a bytes owned DECL INIT carries no
    coerce (the pending local's owned resolution) and takes the S6 decl-init
    BORROW wrap (`::tpy::bytes_copy(...)`) directly, while a bytes owned
    RETURN arrives as the gate-rejected `bytesview_to_bytes` coerce (the
    deferred cross-type bytes-coercion cell) -> AST. Stepped `s[a:b:c]` and
    a `slice`-typed
    variable index yield the family's OWNED type (STORAGE, bare at every
    sink); a `basic_slice`-typed variable index yields the view. A `{cpp}`
    placeholder would need return-type substitution the emit lacks."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    fi = e.slice_function_info
    if fi is None:
        return False
    if not fi.cpp_template or "{cpp}" in fi.cpp_template:
        return False
    if not _str_slice_receiver_ok(e.obj, locals_, analyzer):
        return False
    rt = _resolved_viewfam_value(analyzer.get_expr_type(e), analyzer)
    if rt is None:
        return False
    if isinstance(e.index, TpySlice):
        sl = e.index
        if e.is_stepped_slice:
            # `::tpy::Slice{lo, hi, step}` -> the owned std::string /
            # std::vector<uint8_t> result.
            if not (is_str_type(rt) or is_bytes_type(rt)):
                return False
            if not _slice_bound_ok(sl.step, locals_, analyzer):
                return False
        else:
            # Defensive: sema sets is_stepped_slice iff the syntax has a step.
            if sl.step is not None or not (is_str_view_type(rt)
                                           or is_bytes_view_type(rt)):
                return False
        return (_slice_bound_ok(sl.lower, locals_, analyzer)
                and _slice_bound_ok(sl.upper, locals_, analyzer))
    # Slice-typed VARIABLE index (`s[sl]`): a bare in-scope slice-object name,
    # rendered bare into the template's `{0}` slot. The view/owned result
    # follows the sema-resolved overload; either way the emit is the bare
    # template expansion, so only the name shape is pinned.
    return (isinstance(e.index, TpyName) and e.index.name in locals_
            and _slice_object_type(locals_[e.index.name]))

def _borrow_local_binding(stmt: TpyVarDecl, target_type: TpyType | None,
                          declared: dict[str, TpyType], prescan: _Prescan,
                          analyzer) -> 'LocalBinding | None':
    """The binding for a non-value local var-decl's *first* declaration, or None
    if it is outside the emit slice. The form decision comes from the shared
    classifier; the slice additionally requires a field-access source off an
    F1-record receiver and an F1-record local (REF_ALIAS / POINTER) / inner
    (OPTIONAL_TO_PTR) type. REF_ALIAS and POINTER are the single-assignment and
    reassigned shapes of the same plain-record lvalue lift; POINTER's reseats are
    gated separately in `_stmt_eligible`."""
    binding = classify_local_binding(
        target_type, stmt.init, analyzer, name=stmt.name,
        reassigned=prescan.reassigned, rvalue_reassigned=prescan.rvalue_reassigned,
        hoisted=prescan.hoisted, move_through=prescan.move_through)
    if binding is LocalBinding.OTHER:
        return None
    if binding is LocalBinding.REBIND_SLOT:
        # F2d: the source is an rvalue F1-record ctor / by-value call (not a field
        # read), so it bypasses the field-receiver check the lvalue bindings need.
        return binding if (_f1_record(target_type, analyzer)
                           and _is_record_rvalue_source(stmt.init, declared, analyzer)) else None
    if isinstance(stmt.init, TpyCall):
        # A borrow-record-returning free call (`p = shared(x)` -> `Pair& p =
        # shared(x);` -- the classifier's lvalue-source verdict). REF_ALIAS
        # only: the reassigned POINTER shape reseats via `&(call)`, a lift
        # the slice does not carry (tagged). Const rides `_f1_is_const`'s
        # raw-sema check (a `readonly[T]` return arrives ReadonlyType-
        # wrapped), the same branch the AST's `_is_const_indirect` applies
        # to a free call. Other call bindings (an OPTIONAL_TO_PTR optional
        # return) fall through untagged so the generic probe's family
        # drilldown names them.
        if binding is LocalBinding.REF_ALIAS and _f1_record(target_type,
                                                            analyzer):
            return binding if _call_eligible(stmt.init, declared, analyzer,
                                             record_ret_ok=True) else None
        if binding is LocalBinding.POINTER and _f1_record(target_type,
                                                          analyzer):
            note_detail("decl.record_call_reassigned")
        return None
    if not _const_exact_field_receiver_ok(stmt.init, declared, analyzer):
        # A record-element container subscript source (`p = ps[i]` ->
        # `P& p = ::tpy::__getitem__(ps, i);`) binds the single-assignment
        # `T&` alias only -- reseats (POINTER) and Optional sources keep the
        # field-receiver pin. The const verdict mirrors the AST's
        # element-borrow propagation (see `_f1_is_const`).
        if (binding is LocalBinding.REF_ALIAS
                and _f1_record(target_type, analyzer)
                and _container_record_elem_subscript(stmt.init, declared,
                                                     analyzer)):
            return binding
        return None
    if binding is LocalBinding.REF_ALIAS or binding is LocalBinding.POINTER:
        return binding if _f1_record(target_type, analyzer) else None
    # OPTIONAL_TO_PTR: the borrow `T*` points at the optional's inner record.
    inner = target_type.inner if isinstance(target_type, OptionalType) else None
    return binding if _f1_record(inner, analyzer) else None

def _is_record_rvalue_source(init: TpyExpr, declared: dict[str, TpyType],
                             analyzer, *, temps_ok: bool = False,
                             narrowed: 'set[str] | frozenset[str]'
                             = frozenset()) -> bool:
    """An F2d rebind-slot source: an rvalue call producing an F1-record (a ctor
    `Inner(...)` or a by-value record-returning call) with eligible scalar args.
    It emits as the bare `Name(args)` the two-slot init / reseat wraps. kwargs /
    star-unpack args take other emit paths and stay on the AST path.

    `temps_ok` (the flushable owned-record decl-init position) additionally
    admits a record-RVALUE arg into a ref-param record slot -- the AST hoists
    `Inner __tmp = make(7);` ahead of the outer `wrap(__tmp)`, mirrored by
    `_lower_call_arg`'s record-temp arm; on the CTOR face the hoist keys on
    the slot's mutation (see `_rec_rvalue_arg_ok`), so a const slot inlines
    the prvalue at any depth while a mutated slot needs the flush. A nested
    MUTATED-slot rvalue (`temps_ok=False` recursion) would need another
    statement flush the single hoist cannot reproduce -> AST."""
    if not isinstance(init, TpyCall):
        return False
    if init.kwargs or init.double_star_unpack is not None:
        return False
    if not (_f1_record(analyzer.get_expr_type(init), analyzer)
            and is_rvalue_source(analyzer, init)):
        return False
    fi = init.resolved_function_info
    if fi is None:
        return False
    # The ctor face shares the shape/registry core with
    # `_record_ctor_call_eligible` (same-name free-fn collision, generic /
    # native / multi-overload / special-form `__init__`, cross-module
    # qualification -- shapes whose AST emit is not the raw `Name(args)`). It
    # owns the arity verdict too (`_ctor_shape_ok`'s `_ctor_arity_ok`): the
    # raw-name form admits omitted trailing defaults, the instantiation form
    # stays exact. The by-value record-returning free-call face shares
    # `_call_eligible`'s callee-shape head (linkage, literal-overload mangling,
    # generics, error_return -- shapes whose AST emit is not the bare
    # `name(args)`); it keeps exact arity here (an omitted free-call default is
    # synthesized by the AST arg emit, a separate frontier).
    if fi.is_constructor:
        if not (_ctor_shape_ok(init, analyzer)
                or _ctor_instantiation_ok(init, analyzer)):
            return False
        # The CTOR arg face keys the record-rvalue temp on the slot's mutation
        # -- `_gen_record_ctor_args` hoists the named temp only for a MUTATED
        # ref slot (flush-position only), while a const slot binds the inline
        # prvalue expansion, temp-free at any depth. Both slot kinds share
        # `_shared_pass_through_arg` (the ctor lowering already emits its rows
        # via `_lower_call_arg`); a mutated slot lowers `T&` (non-const ref),
        # so `mutated` gates off the temp-producing rows a prvalue/temp binds
        # ill-formed (see its docstring), keeping the by-value and lvalue-NAME
        # rows. The mutation-keyed record-rvalue rides `_rec_rvalue_arg_ok`.
        ctor_mut = fi.mutated_params or frozenset()

        def _rec_rvalue_arg_ok(i: int, a: TpyExpr, ptype: 'TpyType | None') -> bool:
            if not _record_rvalue_temp_arg(a, ptype, declared, analyzer):
                return False
            return temps_ok if i in ctor_mut else True

        return all(_shared_pass_through_arg(a, p.type, declared, analyzer,
                                            mutated=i in ctor_mut)
                   or _rec_rvalue_arg_ok(i, a, p.type)
                   for i, (a, p) in enumerate(zip(init.args, fi.params)))
    # The by-value record-returning FREE-call face: the same callee-shape head
    # as `_call_eligible` (linkage / literal-overload / generics / error_return
    # via `_plain_free_callee_ok`) + exact arity, then the SHARED plain-call arg
    # cascade -- so `return make_rec(s, xs, r)` routes the str / container /
    # record / Own-move / optional-ptr / union arg shapes a free call already
    # carries, not the reduced scalar-only subset. Guard: a str-literal into a
    # multi-overload callee pins to the view form (`string_view("...")`), which
    # the bare emit does not reproduce -- mirror `_call_eligible`'s pin.
    if len(init.args) != len(fi.params) or not _plain_free_callee_ok(init, analyzer):
        return False
    if any(isinstance(_peel_coerce(a), TpyStrLiteral) for a in init.args):
        fis = analyzer.registry.get_function(init.func_name)
        if fis is not None and len(fis) > 1:
            return False
    return _plain_call_args_ok(init, declared, analyzer, temps_ok=temps_ok,
                               narrowed=narrowed)

def _is_record_rvalue_method_source(init: TpyExpr, declared: dict[str, TpyType],
                                    analyzer, *, temps_ok: bool = False) -> bool:
    """The method-call sibling of `_is_record_rvalue_source`: a call
    `recv.build(args)` returning an F1-record RVALUE (an `Own[Record]` return,
    not a `T&` borrow) that the owned-record decl stores directly as the bare
    `recv.build(args)` prvalue. Receiver / method / arg admission rides
    `_record_method_call_eligible` (through `_method_call_eligible`) with the
    record return opened by `record_ret_ok`; `is_rvalue_source` keeps a borrow
    `T&`-returning method out (it would need a ref-alias, a separate shape)."""
    if not isinstance(init, TpyMethodCall):
        return False
    if not (_f1_record(analyzer.get_expr_type(init), analyzer)
            and is_rvalue_source(analyzer, init)):
        return False
    return _method_call_eligible(init, declared, analyzer,
                                 record_ret_ok=True, temps_ok=temps_ok)

def _scalar_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                           analyzer, ws: '_WalkState | None' = None) -> bool:
    """A scalar-field write `recv.field = <scalar>`: a value-scalar field off an
    F1-record receiver (`_field_receiver_ok` also rejects the property-setter /
    __setattr__ write target), written with an eligible scalar expression. The
    scalar sibling of `_f2b_optional_field_write_ok` -- it emits as the AST's
    default field-assign path (`recv.field = <value>;`, no borrow<->storage lift). The
    target is a plain field off an F1-record receiver, or a record-element tuple
    subscript (`t[N].field = <scalar>` -> `std::get<N>(t)->field = ...`, the write analog
    of the record-element read); an Optional-element target stays on the AST path (its
    markers reject it). A Char field writes identically (`recv.c = z`); its
    str-literal value guard is defensive -- sema type-errors a literal into a
    Char field, but the target-typed `'x'` render would otherwise diverge.

    `ws` (when passed -- the statement-walk call site) admits a direct
    temp-hoisting call value: the field write is the fifth flushable
    statement position (the AST's single gen_stmt flush point covers every
    assign target shape).

    An Optional-ptr-receiver target is admitted on both faces: proven ->
    `p->field = <value>;` (arrow via _field_receiver_ok), unproven ->
    `::tpy::deref_check(p).field = <value>;` (_optional_checked_field)."""
    target = stmt.target
    if not (_field_receiver_ok(target, declared, analyzer)
            or _optional_checked_field(target, declared, analyzer)
            or _field_over_subscript_ok(target, declared, analyzer)
            or _field_over_container_subscript_ok(target, declared, analyzer)):
        return False
    ftype = analyzer.get_expr_type(target)
    if _eligible_char(ftype):
        if isinstance(stmt.value, TpyStrLiteral):
            return False
    elif not (_eligible_scalar(ftype)
              or _eligible_enum(ftype, analyzer) is not None
              or _is_type_param_slot(ftype)
              or _eligible_ptr_value(ftype, analyzer)):
        # A generic record's `T` field write emits as a plain assign (`field = v`
        # / `field = std::move(v)`) -- the BORROW->STORAGE convert renders the
        # source bare/moved (its TypeParamRef emit arm), byte-identical to the
        # AST's `val_or_ref_t<T>` / `own_param_t<T>` copy/move per instantiation.
        return False
    if _expr_eligible(stmt.value, declared, analyzer):
        return True
    return ws is not None and _stmt_value_temps_call(stmt.value, ws, analyzer)

def _ptr_union_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                              analyzer) -> bool:
    """A union-field write `recv.field = <source>` (F4 U2): a value-variant
    field off an F1-record receiver written from a borrow-form pointer-variant
    name of the same union (lowers to the borrow->storage `THIRFormConvert`,
    `::tpy::to_value_variant<...>` -- the union sibling of the F2b Optional
    write), a `None` literal (a monostate store, `recv.field =
    std::monostate{};`), or a same-union field lvalue (a storage-to-storage
    copy: a field source is not a ptr-variant source on the AST path, so it
    assigns bare with no lift). Member-valued sources (the ctor-arg cascade)
    ride a later cell -- also a pre-existing AST gap at other positions."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    u = _eligible_ptr_union(analyzer.get_expr_type(target), analyzer)
    if u is None:
        return False
    if isinstance(stmt.value, TpyNoneLiteral):
        return True
    return _ptr_union_source_ok(stmt.value, declared, analyzer, u,
                                allow_field=True)

def _nondef_ctor_field(ftype: 'TpyType | None', analyzer) -> bool:
    """The field's record type has a suppressed default ctor (`@nocopy` with
    `__del__`): a DEMOTED init of such a field makes the AST's
    `_reject_nondef_ctor_field_in_body` raise a CodeGenError, so THIR must
    keep the whole ctor on the AST path (routing would silently emit the
    uncompilable default-init instead of the diagnostic). Keyed on the field
    TYPE only -- an inherited field of such a type over-rejects (the AST
    skips non-own fields), which is safe."""
    rec = analyzer.registry.get_record_for_type(ftype)
    return rec is not None and del_suppresses_default_ctor(rec)

def _record_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                           analyzer, ws: '_WalkState',
                           prescan: '_Prescan') -> bool:
    """A plain F1-record field write `recv.field = <source>` off an F1-record
    receiver -- the AST's default field assign, no borrow<->storage lift. Two
    source rows:

      * a **record rvalue** (the `_is_record_rvalue_source` shape -- a ctor /
        by-value record-returning call): a direct copy
        `recv.field = Inner(args);`. The exact source-type == field-type check
        keeps a subclass rvalue (a slicing copy) out.
      * a **record NAME** (a declared borrow param / owned local of a record
        type, incl. `Own[T]` params): the bare copy `recv.field = p;` (plus
        sema's implicit-copy warning, path-independent), or `std::move(p)` at
        a movable name's last use (`_maybe_move`) -- the plain-record STORAGE
        convert arm. Narrowed names (`(*o)` deref renders), pointer-locals
        (`(*p)`), `self`, and coerce-wrapped sources stay on the AST path.

    In a constructor body this gate sees only DEMOTED inits (the MIL hoist
    already ran in `lower_constructor`), which the AST emits via the same
    default assign -- routed, except a field type with a suppressed default
    ctor (see `_nondef_ctor_field`: the AST raises there)."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    ftype = analyzer.get_expr_type(target)
    if not _f1_record(ftype, analyzer):
        return False
    if prescan.is_constructor and _nondef_ctor_field(ftype, analyzer):
        return False
    if _is_record_rvalue_source(stmt.value, declared, analyzer):
        return analyzer.get_expr_type(stmt.value) == ftype
    v = stmt.value
    if not (isinstance(v, TpyName) and v.name in declared
            and v.name not in ws.narrowed and v.name not in ws.pointers
            and not (prescan.has_self and v.name == "self")):
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[v.name])))
    own = unwrap_optional_own(vt)
    if own is not None:
        vt = own.wrapped
    return _f1_record(vt, analyzer)

def _optional_record_field_inner(t: 'TpyType | None', analyzer) -> 'TpyType | None':
    """The inner record type of a pointer-repr `Optional[F1-record]` field slot
    (stored `std::optional<inner>`, inner a non-value record) -- or None. Shared
    by the value-storage optional field-write gate and its lowering. The
    `uses_pointer_repr` guard keeps a VALUE-record inner out: its
    borrow->storage convert has no plain-non-value emit arm."""
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if (isinstance(u, OptionalType) and u.uses_pointer_repr()
            and _f1_record(u.inner, analyzer)):
        return u.inner
    return None

def _optional_record_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                                    pointers: set[str], analyzer, ws: '_WalkState',
                                    prescan: '_Prescan') -> bool:
    """A value-storage `Optional[record]` field write `recv.opt = <record>` off
    an F1-record receiver: the field stores `std::optional<inner>`, and the
    source is a record RVALUE (ctor / by-value call of the inner type -- copied
    bare, exact-type to keep a subclass slice out) or a record NAME (a record
    param / owned local, incl. `Own[T]` params) copied bare (`opt = p;`,
    optional::operator= absorbs the inner lvalue) or moved at a movable name's
    last use (`opt = std::move(p);`). The record-field-write shape at an Optional
    slot; the F2b `T*`->ptr_to_optional lift (a pointer-local source) and the
    `None` store stay their own arms. Narrowed / pointer-local / `self` sources
    take other AST emit paths and stay on the AST path."""
    target = stmt.target
    if not _field_receiver_ok(target, declared, analyzer):
        return False
    inner = _optional_record_field_inner(analyzer.get_expr_type(target), analyzer)
    if inner is None:
        return False
    v = stmt.value
    if _is_record_rvalue_source(v, declared, analyzer):
        return analyzer.get_expr_type(v) == inner
    if not (isinstance(v, TpyName) and v.name in declared
            and v.name not in pointers and v.name not in ws.narrowed
            and not (prescan.has_self and v.name == "self")):
        return False
    vt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(declared[v.name])))
    own = unwrap_optional_own(vt)
    if own is not None:
        vt = own.wrapped
    return _f1_record(vt, analyzer)

def _container_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                              analyzer) -> bool:
    """A container-literal field write `recv.field = [...] / {...}` off an
    F1-record receiver: the AST's default field assign renders
    `gen_expr_deref(value, field_type)` -- the same target-threaded literal
    render a decl init gets (`{e1, e2}` consumed by the vector lvalue, the
    spelled empty list, the `::tpy::ordered_map<K, V>(...)` /
    `ordered_set<T>(...)` constructor forms) with no move wrap (a literal is
    never a movable name). Element admission is the shared
    `_container_literal_ok` slice."""
    if not isinstance(stmt.value, (TpyArrayLiteral, TpyDictLiteral,
                                   TpySetLiteral)):
        return False
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return False
    ftype = unwrap_readonly(unwrap_ref_type(
        unwrap_send_sync(analyzer.get_expr_type(stmt.target))))
    return _container_literal_ok(stmt.value, ftype, declared, analyzer)

def _str_field_write_ok(stmt: TpyAssign, declared: dict[str, TpyType],
                        analyzer) -> bool:
    """A str-family field write `recv.field = <str literal | str name>` off an
    F1-record receiver: the AST's default field assign renders the value BARE
    (`recv.field = s;` / `= "lit";`) -- `std::string::operator=(string_view)`
    absorbs a view source into an owned field, so unlike a decl init there is
    NO view->owned `std::string(...)` construction, and str names are never in
    codegen's movable set (value-typed decl arms don't register), so no move
    wrap either. Sources beyond names/literals (concats, calls, coerces) stay
    on the AST path; `String`-typed fields/sources keep their own emit shapes
    (excluded by `_resolved_str_value`)."""
    if not _field_receiver_ok(stmt.target, declared, analyzer):
        return False
    if _resolved_str_value(analyzer.get_expr_type(stmt.target),
                           analyzer) is None:
        return False
    v = stmt.value
    if isinstance(v, TpyStrLiteral):
        return True
    return (isinstance(v, TpyName) and v.name in declared
            and _resolved_str_value(declared[v.name], analyzer) is not None)

def _scalar_aug_assign_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                          analyzer) -> bool:
    """A scalar augmented assignment `x += y` / `recv.field += y` that is
    byte-identical to `target = (target OP value)` -- the plain
    `_gen_binop_from_result` branch of `_gen_aug_assign_code`, with every
    preprocessing branch of the AST path gated out:

      - an in-place dunder (`resolved_inplace`) mutates the target via a method
        call, not the binop substitution;
      - a missing/non-template `resolved_binop` emits a bare C++ `op=` fallback;
      - `str +=` takes the in-place-append optimization (excluded for free: a
        str target is not an eligible scalar);
      - a class-constant / narrowed-optional target is not a plain eligible-scalar
        lvalue (a record-element tuple-subscript target IS admitted, via
        `_field_over_subscript_ok` -- the target renders identically on both sides
        of the synthetic `target = (target OP value)`).

    The target is a declared scalar local, an F1-record scalar field, or a
    record-element tuple subscript (`t[N].field`); the value
    is an eligible scalar expression. Lowering synthesizes the binop with
    `divisor_non_zero=False` -- the AST aug-assign path never swaps
    `div_check`->`div_floor` (no `TpyBinOp` node carries the flag)."""
    if stmt.resolved_inplace is not None:
        return False
    rb = stmt.resolved_binop
    if rb is None or not getattr(rb.method, "cpp_template", None):
        return False
    target = stmt.target
    if isinstance(target, TpyName):
        if target.name not in declared:
            return False
    elif not (_field_receiver_ok(target, declared, analyzer)
              or _optional_checked_field(target, declared, analyzer)
              or _field_over_subscript_ok(target, declared, analyzer)
              or _field_over_container_subscript_ok(target, declared, analyzer)):
        return False
    # A narrowed-Optional or non-scalar target is rejected here (the AST unwraps
    # the former and never reaches the binop branch for the latter). An
    # Optional-ptr-receiver field target IS admitted (proven -> `p->x`,
    # unproven -> `deref_check(p).x` -- the same render lands on both sides of
    # the synthetic `target = (target OP value)`, exactly as the AST
    # substitutes its target string twice).
    target_type = analyzer.get_expr_type(target)
    if not _eligible_scalar(target_type):
        return False
    # FixedInt += BigInt converts the value via `.to_fixed_check<T>()` before
    # the binop -- mirrored as the synthetic THIRBinOp's right_cast at lowering
    # (the same target-type/value-type pair keys both, so gate and emit agree).
    return _expr_eligible(stmt.value, declared, analyzer)

def _str_aug_append_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                       prescan: _Prescan, analyzer) -> bool:
    """A str in-place append `t += v` -> `t += v;` -- the string branch of
    `_gen_aug_assign_code`'s resolved-binop arm, mirrored condition for
    condition: no in-place dunder (checked first there), a resolved binop, op
    `+`, and an owned-str-family target. The value renders bare via
    `gen_expr(value, target_type)` for every admitted shape (str literal /
    str-family name / owned-str call / nested concat), so it is pinned to the
    concat-operand slice.

    The target must be a declared LOCAL: a str param's aug-assign would need
    the AST's owned-copy prologue -- which `_function_eligible`'s reassigned-
    param reject does NOT cover, because the prescan tracks aug-assign targets
    in `aug_assigned`, not `reassigned`. (The AST path itself emits `a += v` on
    the untouched `std::string_view` param there -- an invalid-C++ miscompile,
    tracked in BUGS.md -- so the reject also avoids reproducing it.)"""
    if stmt.op != "+" or stmt.resolved_inplace is not None:
        return False
    if stmt.resolved_binop is None:
        return False
    target = stmt.target
    if not (isinstance(target, TpyName) and target.name in declared
            and target.name not in prescan.param_names):
        return False
    if not _owned_str_append_target(analyzer.get_expr_type(target), analyzer):
        return False
    vt = analyzer.get_expr_type(stmt.value)
    return (_str_concat_operand(stmt.value, vt, analyzer)
            and _expr_eligible(stmt.value, declared, analyzer))

def _bytes_aug_concat_ok(stmt: TpyAugAssign, declared: dict[str, TpyType],
                         prescan: _Prescan, analyzer) -> bool:
    """A bytes `t += v` -> the concat-and-assign
    `t = ::tpy::bytes_concat(t, v);` -- there is NO in-place append for bytes
    (`_gen_aug_assign_code`'s string branch is str-family-pinned), so the AST
    takes the resolved-binop arm and the lowering's generic
    `target = (target OP value)` desugar reproduces it (the native
    `bytes_concat` emit, unwrapped -- `paren_wrap=False`). Mirrored condition
    for condition with the str twin (`_str_aug_append_ok`): no in-place
    dunder, a resolved binop (here the template-less native dunder), op `+`,
    and an owned-bytes target.

    The target must be a declared LOCAL: an aug-assigned bytes PARAM skips the
    AST's owned-copy prologue (the prescan tracks aug-assign targets in
    `aug_assigned`, not `reassigned`) and emits
    `a = ::tpy::bytes_concat(a, b);` on the untouched span param -- the span
    silently rebinds to the concat's dying temporary vector (a dangling-view
    miscompile, the bytes face of the str aug-assign-param bug in BUGS.md) --
    so the reject avoids reproducing it."""
    if stmt.op != "+" or stmt.resolved_inplace is not None:
        return False
    rb = stmt.resolved_binop
    if rb is None or getattr(rb.method, "cpp_template", None):
        return False
    if not (rb.method.native_function and rb.method.native_name):
        return False
    target = stmt.target
    if not (isinstance(target, TpyName) and target.name in declared
            and target.name not in prescan.param_names):
        return False
    bt = _resolved_bytes_value(analyzer.get_expr_type(target), analyzer)
    if bt is None or not is_bytes_type(bt):
        return False
    vt = analyzer.get_expr_type(stmt.value)
    return (_bytes_concat_operand(stmt.value, vt, analyzer)
            and _expr_eligible(stmt.value, declared, analyzer))

def _subscript_recv_reject(recv: TpyExpr, locals_: dict[str, TpyType],
                           analyzer) -> str:
    """Drilldown suffix for a non-admitted subscript receiver -- shared by the
    read (`subscript.recv.*`) and write (`setitem.recv.*`) gates so the
    fallback tally names WHICH receiver shape blocks (the `_recv_shape_reject`
    pattern: runs only on already-rejected shapes)."""
    if isinstance(recv, TpyName):
        return ("recv.name_absent" if recv.name not in locals_
                else "recv.name_shape")  # pointer-local / narrowed binding
    if isinstance(recv, TpyFieldAccess):
        if not isinstance(recv.obj, TpyName):
            return "recv.field_chain"
        if not _field_receiver_ok(recv, locals_, analyzer):
            return "recv.field_parent"
        if _field_decl_type(recv, locals_, analyzer) is None:
            return "recv.field_decl"
        return "recv.field_family"
    if isinstance(recv, TpySubscript):
        return "recv.subscript"
    if isinstance(recv, (TpyCall, TpyMethodCall)):
        return "recv.call"
    return "recv.other"

def _subscript_elem_reject(t: TpyType, analyzer) -> str:
    """Element-family drilldown under `subscript.elem.*`: WHICH non-admitted
    element (list/Array/Span) or key/value (dict) family blocks a subscript
    read whose container kind is already admitted -- splits the old
    `subscript.elem_family` blanket so the tally ranks the per-family cells.
    Runs only on already-rejected shapes (the `_recv_shape_reject` pattern)."""
    args = getattr(t, "type_args", None)
    if not args:
        return "elem.untyped"
    elem = args[0]
    if is_dict(t):
        key, val = args[0], args[1]
        key_ok = (is_fixed_int_type(key) or _runtime_bigint(key, analyzer)
                  or _owned_str_slot(key, analyzer))
        if key_ok:
            elem = val
        elif _eligible_scalar(val) or _owned_str_slot(val, analyzer):
            return "elem.dict_key"  # value admitted, key family blocks
        else:
            elem = val  # both blocked; name the value family
    if not isinstance(elem, TpyType):
        return "elem.other"
    el = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(elem)))
    if isinstance(el, OptionalType):
        return "elem.optional"
    if isinstance(el, UnionType):
        return "elem.union"
    if isinstance(el, TupleType):
        return "elem.tuple"
    if is_list(el) or is_dict(el) or is_set(el) or is_array(el) or is_span(el):
        return "elem.container"
    if _resolved_bytes_value(el, analyzer) is not None:
        return "elem.bytes"
    if _resolved_str_value(el, analyzer) is not None:
        return "elem.strview"  # a view-typed slot (owned str is admitted)
    if isinstance(el, NominalType) and el.is_record:
        return ("elem.record" if _f1_record(el, analyzer)
                else "elem.record_nonf1")
    return "elem.other"

def _subscript_read_reject(e: TpySubscript, locals_: dict[str, TpyType],
                           analyzer) -> str:
    """Drilldown label for a subscript read no arm admitted -- splits the old
    `subscript.read_shape` bucket by blocking axis (receiver shape / index /
    element family / slice), so the tally ranks the follow-on cells."""
    if e.needs_optional_runtime_check:
        return "subscript.optional_check"
    if e.slice_function_info is not None or isinstance(e.index, TpySlice):
        return "subscript.slice_shape"
    recv_t = _subscript_container_recv_type(e.obj, locals_, analyzer)
    if recv_t is None:
        return "subscript." + _subscript_recv_reject(e.obj, locals_, analyzer)
    if (_bigint_index_disposition(e.index, analyzer) == "reject"
            or not _expr_eligible(e.index, locals_, analyzer)):
        return "subscript.index"
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_t)))
    if isinstance(t, TupleType):
        return "subscript.tuple_shape"  # non-const index / non-value element
    if _resolved_viewfam_value(t, analyzer) is not None:
        return "subscript.viewfam_shape"  # a str/bytes arm's own reject
    if is_list(t) or is_array(t) or is_span(t) or is_dict(t):
        return "subscript." + _subscript_elem_reject(t, analyzer)
    return "subscript.recv_type"

def _setitem_target_ok(sub: TpySubscript, ws: '_WalkState', analyzer) -> bool:
    """The shared write-target half of the subscript-write gates: a
    single-index (non-slice) subscript off a bare in-scope container name --
    or a one-level container FIELD off an admitted receiver name
    (`self.xs[i] = v` / `h.d[k] = v`, the read gate's receiver widening) --
    of an admitted family (`_container_scalar_read`: list/Array/Span[scalar],
    dict[fixed-int|BigInt|str, scalar|str] -- so the written element/value
    slot is a value scalar or an owned str), with an eligible index. A
    TypedDict subscript writes a FIELD on the AST path; a narrowed/pointer
    receiver and slice assignment (`xs[a:b] = ...` -> list_set_slice) stay
    AST. A field receiver types at the DECLARED field type, so a narrowed
    Optional/union field (the AST's `(*recv.field)` unwrap) rejects at the
    family check."""
    if isinstance(sub.index, TpySlice) or sub.slice_function_info is not None:
        return note_detail("setitem.slice")
    if sub.typed_dict_field is not None or sub.needs_optional_runtime_check:
        return note_detail("setitem.receiver")
    recv = sub.obj
    if isinstance(recv, TpyName) and (recv.name in ws.pointers
                                      or recv.name in ws.narrowed):
        return note_detail("setitem.recv.name_shape")
    recv_t = _subscript_container_recv_type(recv, ws.declared, analyzer)
    if recv_t is None:
        return note_detail(
            "setitem." + _subscript_recv_reject(recv, ws.declared, analyzer))
    if not _container_scalar_read(recv_t, analyzer):
        return note_detail("setitem.family")
    if (_bigint_index_disposition(sub.index, analyzer) == "reject"
            or not _expr_eligible(sub.index, ws.declared, analyzer)):
        return note_detail("setitem.index")
    return True

def _container_setitem_ok(stmt: TpyAssign, ws: '_WalkState', analyzer) -> bool:
    """A container subscript write `c[k] = v` -> the checked
    `::tpy::__setitem__(c, k, v);` or (index proven in-bounds) the direct
    `c[static_cast<std::size_t>(k)] = v;`. The value is any eligible scalar /
    str-slice expr rendered against the element slot (literal retype; a
    view-form str source into an owned-str element takes the explicit
    `std::string(v)` copy -- the AST's `_view_source_to_owned` chokepoint),
    or a direct temp-hoisting call (the write is a flushable statement
    position, like a name assign). The AST's other value wraps cannot fire
    here: elements of admitted families are value scalars / owned str, so
    the tuple/Optional/union storage lifts and the last-use move
    (non-value-type locals only) have no admitted source."""
    if not _setitem_target_ok(stmt.target, ws, analyzer):
        return False
    if (_expr_eligible(stmt.value, ws.declared, analyzer)
            or _stmt_value_temps_call(stmt.value, ws, analyzer)):
        return True
    return note_detail("setitem.value")

def _container_aug_setitem_ok(stmt: TpyAugAssign, ws: '_WalkState',
                              analyzer) -> bool:
    """An augmented container subscript write `c[k] OP= v` -> the AST's
    read-modify-write pair `::tpy::__setitem__(c, k, <read> OP v);` with the
    read the CHECKED `::tpy::__getitem__(c, k)` -- the aug arm never takes
    the bounds-safe operator[] even when the node fact is set
    (`_gen_aug_assign_subscript_code` ignores `bounds_safe`). Mirrored
    condition-for-condition with `_scalar_aug_assign_ok`: no in-place
    dunder, a templated resolved binop, an eligible value. The element is a
    resolved scalar (the FixedInt-elem += BigInt value takes the
    `({0}).to_fixed_check<T>()` cast, like the name arm) or an owned str
    (op `+` -- the resolved concat renders `::tpy::str_concat(<read>, v)`,
    pinned to the concat-operand slice like the name append)."""
    if not _setitem_target_ok(stmt.target, ws, analyzer):
        return False
    if stmt.resolved_inplace is not None:
        return note_detail("setitem.aug_inplace")
    rb = stmt.resolved_binop
    if rb is None or not getattr(rb.method, "cpp_template", None):
        return note_detail("setitem.aug_binop")
    et = analyzer.get_expr_type(stmt.target)
    if _owned_str_slot(et, analyzer):
        vt = analyzer.get_expr_type(stmt.value)
        if not (stmt.op == "+" and _str_concat_operand(stmt.value, vt, analyzer)):
            return note_detail("setitem.aug_value")
    elif not _resolved_scalar(et, analyzer):
        return note_detail("setitem.aug_elem")
    if _expr_eligible(stmt.value, ws.declared, analyzer):
        return True
    return note_detail("setitem.aug_value")

def _membership_eligible(e: TpyBinOp, locals_: dict[str, TpyType],
                         analyzer) -> bool:
    """`needle in c` / `needle not in c` over a dict/set container NAME whose
    `__contains__` is a plain @native member (`c.contains(needle)`, the
    `resolved_contains` render of _gen_binop). Restricted to that member shape:
    bytes membership (`__contains__` is a @native FREE function), str `.find()`,
    a TypedDict / tuple-literal / global (indirect-name) receiver, and list's
    `std::ranges::contains` (no `__contains__` member -> resolved_contains None)
    all take other arms. The needle renders bare -- the admitted containers
    carry fixed-int / owned-str keys and scalar set members, never a StrView
    key, so `view_key_target` is None and the AST's `gen_expr(needle, None)` is
    the plain value render -- so the needle is pinned to a value scalar; str /
    bytes needles (view_key_target-threaded) ride a later cell."""
    fi = e.resolved_contains
    if fi is None or e.typed_dict_in_field is not None:
        return False
    if fi.cpp_template or fi.native_function or not fi.native_name:
        return False
    if not (isinstance(e.right, TpyName) and e.right.name in locals_):
        return False
    ct = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[e.right.name])))
    if not (is_dict(ct) or is_set(ct)):
        return False
    lt = _operand_type(e.left, locals_, analyzer)
    return (_resolved_scalar(lt, analyzer)
            and _expr_eligible(e.left, locals_, analyzer)
            and _witness("binop.membership"))

def _binop_eligible(e: TpyBinOp, locals_: dict[str, TpyType], analyzer) -> bool:
    rb = e.resolved_binop
    rt = analyzer.get_expr_type(e)
    if e.op in _ARITH_OPS or e.op in _BITWISE_OPS:
        # Same-width arithmetic OR a fixed-int bitwise op: a templated dunder,
        # scalar result. Bitwise reaches the scalar-result check below (the `+`
        # bytes/str concat arms never fire for it, and a set `&`/`|`/`^` -- a
        # container result -- rejects at `_resolved_scalar`). Excludes any
        # mixed/widening result the slice can't render without a coercion node.
        # _resolved_scalar: two literal-seeded-container element reads (e.g.
        # `ys[0] + ys[2]`) produce an IntLiteral result type; the emit reads only
        # the resolved dunder's template, so the resolved default int is the fact
        # that matters.
        if rb is None or not getattr(rb.method, "cpp_template", None):
            # Bytes concat `a + b`: the resolved `__add__` is a template-less
            # @native free-function dunder (`::tpy::bytes_concat`, an owned
            # `bytes` result) -- the emit's native binop arm, the shape the
            # compare gate already admits for `bytes_eq`. Any other
            # template-less rb takes an unmirrored emit path -> AST. The
            # `bytearray` overload also resolves to `bytes_concat`, but its
            # operand is not a bytes-slice value and rejects below.
            if rb is None or e.op != "+":
                return False
            if not (rb.method.native_function and rb.method.native_name):
                return False
            bt = _resolved_bytes_value(rt, analyzer)
            if bt is None or not is_bytes_type(bt):
                return False
            lt = _operand_type(e.left, locals_, analyzer)
            rt_op = _operand_type(e.right, locals_, analyzer)
            if not (_bytes_concat_operand(e.left, lt, analyzer)
                    and _bytes_concat_operand(e.right, rt_op, analyzer)):
                return False
        elif e.op == "+" and _is_string_owned(rt):
            # str-family concat -> an owned `String` result
            # (`::tpy::str_concat({self}, {0})` via the resolved __add__).
            # Operands are pinned to the bare-rendering str slice (literal /
            # str / StrView / String); a Char operand's overload wraps it in
            # `char_to_str` with the Char value slice's render -> AST path.
            lt = _operand_type(e.left, locals_, analyzer)
            rt_op = _operand_type(e.right, locals_, analyzer)
            if not (_str_concat_operand(e.left, lt, analyzer)
                    and _str_concat_operand(e.right, rt_op, analyzer)):
                return False
        elif not _resolved_scalar(rt, analyzer):
            return False
        # Both operands IntLiteral-typed NON-NAMES (two literal-seeded-container
        # element reads, `ys[0] + ys[2]`): in a fixed-int target context the AST
        # short-circuits to gen_call_from_fi WITHOUT the paren wrap
        # (_gen_binop's literal-operand branch), and the target is
        # position-dependent -- keep the shape on the AST path. A name operand
        # (incl. an IntLiteral-typed loop var) takes the resolved-binop branch
        # THIR mirrors.
        elif (isinstance(analyzer.get_expr_type(e.left), IntLiteralType)
                and not isinstance(e.left, TpyName)
                and isinstance(analyzer.get_expr_type(e.right), IntLiteralType)
                and not isinstance(e.right, TpyName)):
            return False
    elif e.op in _COMPARE_OPS:
        # A scalar comparison -> bool, usable as a value (`x = a < b`) or an
        # `if`/`while` condition. `<`/`==` carry a `{self} OP {0}` template; the
        # derived comparisons (`<= > >= !=`) have rb=None and emit as a bare C++
        # operator. A rb *with* a non-template would emit some other way -> reject.
        if rt is None or not is_bool_type(rt):
            return False
        if rb is not None and not getattr(rb.method, "cpp_template", None):
            # A @native free-function dunder (bytes `==`/`!=` ->
            # `::tpy::bytes_eq`) emits via gen_call_from_fi's native arm,
            # mirrored by the emitter's native binop arm; any other
            # template-less rb takes an unmirrored emit path -> AST.
            if not (rb.method.native_function and rb.method.native_name):
                return False
        lt = _operand_type(e.left, locals_, analyzer)
        rt_op = _operand_type(e.right, locals_, analyzer)
        # Both operands must be value scalars (or both str-slice values, or a
        # Char pair): a record compare also reaches the rb=None bare-operator
        # arm (a user dunder carries no template), but its operands take
        # gen_expr_deref's indirection handling -- `self` renders `(*this)`, a
        # pointer-local `(*p)` -- which the scalar emit does not reproduce.
        # Str/Char names are value types (never pointer-locals), so those pairs
        # are safe; str `<`/`==` templates, the Char rb=None bare `==`/`!=`,
        # and the derived bare operators emit identically on both paths. The
        # str arm is checked before the char arm so a literal-vs-literal
        # compare stays a plain string compare; in the char arm the single-char
        # literal renders as a char literal (`'x'`, lowered via
        # _lower_char_targeted). The name-eligibility check below is no
        # guard here (any in-scope name passes it, whatever its type).
        if not ((_resolved_scalar(lt, analyzer)
                 and _resolved_scalar(rt_op, analyzer))
                or (_str_compare_operand(e.left, lt, analyzer)
                    and _str_compare_operand(e.right, rt_op, analyzer))
                or (_bytes_compare_operand(e.left, lt, analyzer)
                    and _bytes_compare_operand(e.right, rt_op, analyzer))
                or (_char_compare_operand(e.left, lt, analyzer)
                    and _char_compare_operand(e.right, rt_op, analyzer))
                # A T-typed pair (`self.get() < other.get()` under a
                # Comparable/Equatable bound): rb is None (the derived
                # bare-operator emit, same `(l OP r)` shape as scalars);
                # T operands are method-call results or bare T names --
                # never pointer-locals, so no indirection divergence.
                or (_tparam_value(lt) and _tparam_value(rt_op))
                or _union_compare_pair(lt, rt_op)
                or _enum_compare_pair(e, lt, rt_op, analyzer)):
            return False
        if _mixed_sign_compare(lt, rt_op):
            return False
    elif e.op in _LOGICAL_OPS:
        # Bool-result and/or over bool operands emits the bare C++ operator
        # (`(l && r)`, rb is None), identical in value and condition position
        # (gen_truthy_expr reduces to the value render for every admitted bool
        # shape). A non-bool result takes _gen_logical_value's temp+ternary; a
        # non-bool operand under a bool result would need per-operand truthiness
        # reasoning -- both stay on the AST path. The literal_facts chain fold
        # (_try_fold_literal_chain) cannot fire in an eligible function: it needs
        # a LiteralType-typed var, which the param/local gates reject. The
        # isinstance-narrowing propagation to the RHS is inert too (isinstance
        # calls are gated out of the operand set).
        if rt is None or not is_bool_type(rt):
            return False
        lt = _operand_type(e.left, locals_, analyzer)
        rt_op = _operand_type(e.right, locals_, analyzer)
        if not (lt is not None and is_bool_type(lt)
                and rt_op is not None and is_bool_type(rt_op)):
            return False
    elif e.op in _IS_OPS:
        # The None identity test on a pointer-repr Optional borrow name:
        # `(p ==|!= nullptr)`, position-independent (condition, bool value,
        # print/f-string arg). The operand order is canonicalized by the AST
        # (the Optional side renders first), so `None is p` mirrors too; both
        # operand checks live in _is_none_compare_operand. Other identity
        # shapes take other _gen_binop arms -> AST path.
        return _is_none_compare_operand(e, locals_, analyzer) is not None
    elif e.op in _MEMBERSHIP_OPS:
        # `needle in c` over a dict/set name with a plain-native-member
        # `__contains__` -- `(c.contains(needle))`. Self-contained verdict (the
        # needle/container checks live in _membership_eligible), so it returns
        # here rather than falling through to the operand tail.
        return _membership_eligible(e, locals_, analyzer)
    else:
        # An unhandled op family takes another emit path -> AST path.
        return False
    if not (_expr_eligible(e.left, locals_, analyzer)
            and _expr_eligible(e.right, locals_, analyzer)):
        return False
    if e.op in _BITWISE_OPS:
        _witness("binop.bitwise")
    return True

def _binop_operand_suffix(e: TpyBinOp, locals_: dict[str, TpyType],
                          analyzer) -> str:
    """`.tparam` / `.genrec` / `.record` when a rejected binop has a
    type-param- or user-record-typed operand -- sizes the generics
    frontier's binop bucket; delete the split when the bucket empties."""
    fam = ""
    for x in (e.left, e.right):
        t = _operand_type(x, locals_, analyzer)
        if t is None:
            continue
        t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
        if contains_type_param(t):
            return ".tparam"
        if isinstance(t, NominalType) and t.is_user_record and not fam:
            fam = ".genrec" if t.type_args else ".record"
    return fam


def _chained_compare_eligible(e: TpyChainedCompare, locals_: dict[str, TpyType],
                              analyzer) -> bool:
    """The inline arm of `_gen_chained_compare`: every INTERMEDIATE operand is
    side-effect-free (`_is_simple_expr` -- imported, so the trigger cannot drift),
    letting the chain desugar to a left-folded `&&` over the sema-synthesized
    pairs (`((a < b) && (b < c))`); endpoints may be complex (evaluated once).
    A non-simple intermediate takes the GCC statement-expression arm
    (`({ auto&& _cmp1 = ...; ... && ...; })`) -> AST path. Each pair is gated
    exactly like a single comparison (`_binop_eligible`: bool result, template
    dunder or bare operator, mixed-sign exclusion, operand eligibility)."""
    if e.pairs is None:
        return False
    if not all(ExpressionGenerator._is_simple_expr(c) for c in e.comparators[:-1]):
        return False
    return all(_binop_eligible(p, locals_, analyzer) for p in e.pairs)

def _unary_not_eligible(e: TpyUnaryOp, locals_: dict[str, TpyType],
                        analyzer) -> bool:
    """Logical `not` over a bool operand -> `(!(operand))`. A bool operand's
    truthiness render (gen_truthy_expr) is its plain value render, so the emit
    is position-independent. A pointer-repr Optional borrow name's truthiness
    is the bare `T*` (`not p` -> `(!(p))`, the _condition_eligible name arm's
    render under the same wrap); the un-narrowed-read restriction mirrors that
    arm. Other non-bool operands (int / storage-Optional / __bool__ truthiness
    wraps) and the arithmetic unaries (`- + ~`) stay on the AST path."""
    if e.op != "!":
        return False
    ot = analyzer.get_expr_type(e.operand)
    if isinstance(ot, OptionalType) and (
            _optional_ptr_borrow_name(e.operand, locals_, analyzer) is not None
            or _value_opt_scalar_name(e.operand, locals_, analyzer) is not None
            or _value_opt_view_name(e.operand, locals_, analyzer) is not None):
        return True
    # An enum operand's truthiness render slots under the same `(!(...))`
    # wrap: `not c` -> `(!(true))` (plain) / `(!((static_cast<U>(p) != 0)))`
    # (IntEnum) -- gen_truthy_expr's enum arms.
    if _enum_truthy_operand(e.operand, locals_, analyzer):
        return True
    return (ot is not None and is_bool_type(ot)
            and _expr_eligible(e.operand, locals_, analyzer))

def _is_len_native(e: TpyExpr) -> bool:
    """Whether `e` is the builtin `len(...)` call -- it resolves to the `tpy::__len__`
    @native free function. A user function named `len` has a different (or no)
    native_name and is excluded, so the emit dispatch keys on the symbol, not the name."""
    if not (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
            and e.func_name == "len"):
        return False
    fi = e.resolved_function_info
    return fi is not None and fi.native_name == "tpy::__len__"

def _is_len_call(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """The eligible `len(name)` / `len(recv.field)` form: the builtin len over a
    single in-scope name -- or a one-level container/str/bytes field off an
    admitted receiver (`len(self.xs)`; the receiver renders as its own
    THIRFieldAccess inside the same call emit) -- of a builtin container type or
    a str-slice value (`::tpy::__len__(x)`, Int32 -- the runtime overloads cover
    std::string and std::string_view). The container/str restriction is
    load-bearing, not cosmetic: a container/str is a by-ref/by-value binding
    that emits bare, but a record (or `Optional`) with `__len__` bound to a
    pointer-local would need `(*p)` (the AST's is_indirect_name deref) that the
    bare emit misses -- so only list/dict/set/Array/str (never pointer-locals)
    are admitted. A field arg types at the DECLARED field type, so a narrowed
    Optional[container] field (the AST's `(*recv.field)` unwrap) rejects at the
    family check. A non-name arg (literal, subscript, call) rides a later
    cell."""
    if not _is_len_native(e):
        return False
    if e.kwargs or e.double_star_unpack is not None or len(e.args) != 1:
        return False
    arg = e.args[0]
    if isinstance(arg, TpyName) and arg.name in locals_:
        bt = locals_[arg.name]
    elif (isinstance(arg, TpyFieldAccess)
          and _field_receiver_ok(arg, locals_, analyzer)):
        bt = _field_decl_type(arg, locals_, analyzer)
        if bt is None:
            return False
    else:
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(bt)))
    return (is_list(t) or is_dict(t) or is_set(t) or is_array(t) or is_span(t)
            or _resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None  # span/vector overloads
            or is_string_type(t))  # a String local: same std::string overload

def _len_arg_reject(arg: TpyExpr, locals_: dict[str, TpyType],
                    analyzer) -> str:
    """Drilldown label for a builtin `len(...)` call `_is_len_call` rejected --
    splits the reject by ARG shape (the receiver axis), so the tally ranks the
    len widenings. Runs only on already-rejected shapes."""
    if isinstance(arg, TpyName):
        return ("len.name_global" if arg.name not in locals_
                else "len.name_family")  # pointer-local / record __len__
    if isinstance(arg, TpyFieldAccess):
        if not _field_receiver_ok(arg, locals_, analyzer):
            return "len.field_parent"
        ft = _field_decl_type(arg, locals_, analyzer)
        return ("len.field_family" if ft is not None else "len.field_decl")
    if isinstance(arg, TpySubscript):
        return "len.arg_subscript"
    if isinstance(arg, (TpyCall, TpyMethodCall)):
        return "len.arg_call"
    return "len.arg_other"

_SPECIAL_BUILTIN_QNAMES = frozenset({qnames.COPY, qnames.COPY_ITER,
                                     qnames.OWN_ITER, qnames.TRY_PARSE})


def _free_callee_kind(e: TpyCall, analyzer) -> 'tuple[str, str] | None':
    """Classify a bare-name free callee into its emit kind + pre-rendered
    payload -- the ONE routing fact shared by the gate and lowering:
    `("plain", "")` the raw same-module `name(args)`; `("imported", cpp)`
    the cross-module qualified spelling (`imported_free_callee_cpp`, the
    decision shared with the AST emit -- lowering stamps
    `THIRCall.callee_cpp`); `("native", native_name)` gen_call_from_fi's
    `::native(args)` arm (C++ @native imports only -- extern-C spells the
    raw unqualified symbol, a different arm); `("template", tmpl)` the
    positional-only @cpp_template expansion (gen_call_from_fi's template
    arm with no substitution context). None = an emit shape the slice does
    not reproduce. Shared by `_call_eligible` and (through the plain/
    imported wrapper `_plain_free_callee_ok`) `_is_record_rvalue_source`'s
    by-value record-returning call face."""
    if not isinstance(e.func, TpyName):
        note_detail("call.expr_callee")
        return None
    if e.kwargs or e.double_star_unpack is not None:
        note_detail("call.kwargs")
        return None
    if e.call_type is not None:
        note_detail(f"call.special.call_type.{_call_type_fam(e.call_type)}")
        return None
    # Explicit / inferred type args ride only the NATIVE and cpp_template
    # kinds: the AST skips explicit template args for native imports (C++
    # deduction over natural param types) and substitutes them into the
    # template ({T} via expand_fi_template) -- both targ-blind emits the
    # existing arms carry. The plain `f<T>(args)` explicit spelling and the
    # repr-subst adapter spelling are rejected at the tail / here.
    has_targs = bool(e.type_args or e.inferred_type_args)
    if has_targs and getattr(e, "representational_subst_params", None):
        note_detail("call.special.type_args")
        return None
    if (e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert
            or e.subscript_callee is not None):
        note_detail("call.special_form")
        return None
    fi = e.resolved_function_info
    if fi is None:
        note_detail("call.unresolved")
        return None
    # Bespoke AST arms that fire BEFORE the fi dispatch: the four
    # @builtin_function specials (_maybe_gen_special_builtin_call, keyed on
    # qualified_name), the print stream chain (keyed on func_name), and the
    # ord single-char-literal constant fold.
    if fi.qualified_name in _SPECIAL_BUILTIN_QNAMES or e.func_name == "print":
        note_detail("call.builtin_special")
        return None
    if e.func_name == "ord" and len(e.args) == 1:
        a0 = _peel_coerce(e.args[0])
        if isinstance(a0, TpyStrLiteral) and len(a0.value) == 1:
            note_detail("call.builtin_special")
            return None
    # A literal-specialized overload emits a mangled name (`f__lit_N`) the
    # pre-rendered spellings do not reproduce. The error_return guard is
    # defense in depth: sema already forces an @error_return call into a
    # try/except or a propagating caller, both ineligible anyway.
    if fi.error_return_type is not None:
        note_detail("call.error_return")
        return None
    if any(isinstance(p.type, LiteralType) for p in fi.params):
        note_detail("call.literal_overload")
        return None
    if (fi.is_method or fi.is_staticmethod or fi.is_async
            or fi.is_generator or fi.is_property_getter or fi.is_property_setter):
        note_detail("call.callee_kind")
        return None
    if fi.cpp_template:
        # A generic template substitutes its named {T} placeholders exactly
        # like gen_call_from_fi (expand_fi_template); only a
        # fully-substituted positional-only result expands with no
        # receiver/substitution context (the scalar-ctor cell's rule).
        tmpl = (expand_fi_template(fi, e.type_args or e.inferred_type_args)
                if has_targs else fi.cpp_template)
        if _positional_only_template(tmpl, len(e.args)):
            return ("template", tmpl)
        note_detail("call.template_shape")
        return None
    if fi.native_function or fi.native_name:
        # C++ @native imports spell `::native_name` -- gen_call_from_fi's
        # two native arms coincide for a receiver-less call, so only the
        # symbol matters. extern-C / @native_c (raw unqualified symbol), a
        # native_function with no native_name (the bare `fi.name` tail),
        # and a declared cpp_return_type (the AST wraps the call in the
        # narrowing static_cast) stay AST.
        if (fi.native_name and fi.linkage == FunctionLinkage.NATIVE
                and fi.native_cpp_return_type is None):
            return ("native", fi.native_name)
        note_detail("call.native_shape")
        return None
    # Only a DEFAULT-linkage function emits as a bare/qualified `name(args)`.
    # @export(binding="C") uses the raw symbol -- rejected by default so a
    # future linkage is rejected rather than silently mis-emitted.
    if fi.linkage != FunctionLinkage.DEFAULT:
        note_detail("call.linkage")
        return None
    icc = imported_free_callee_cpp(analyzer.ctx.module_attributes, e.func_name)
    if fi.type_params or has_targs:
        # A plain TPy generic callee spells explicit template args
        # (`f<int32_t>(args)`, type_to_cpp_stored per arg) over the plain /
        # imported spelling; the args resolve against the ROOT stub's params
        # with the inferred substitution (the TypeParamRef ref-slot temp
        # rule). Overload groups pick a different fi for the arg loop and
        # literal-mangle the callee -> AST.
        fis = analyzer.registry.get_function(e.func_name)
        if fis is None or len(fis) != 1:
            note_detail("call.callee_kind.generic")
            return None
        root = fis[0]
        if (not root.type_params or not e.inferred_type_args
                or len(e.inferred_type_args) != len(root.type_params)):
            note_detail("call.callee_kind.generic")
            return None
        if icc is None and e.func_name in analyzer.imported_names:
            note_detail("call.imported_symbol")
            return None
        return ("generic", icc or "")
    if icc is not None:
        return ("imported", icc)
    if e.func_name in analyzer.imported_names:
        # An implicitly-imported builtin-module callee: the AST's
        # conditional-qualification arm (inert today, forward-looking for
        # pure-TPy builtins) -> AST path.
        note_detail("call.imported_symbol")
        return None
    return ("plain", "")


def _call_type_fam(t: TpyType) -> str:
    """Coarse `call_type` family for the special-form drilldown detail --
    sizes the generics frontier's instantiation buckets; delete the split
    when the bucket empties."""
    if isinstance(t, PtrType):
        return "ptr"
    if isinstance(t, NominalType):
        if t.is_user_record:
            return "genrec" if t.type_args else "record"
        if t.type_args:
            return "builtin_generic"
    return "other"


def _plain_free_callee_ok(e: TpyCall, analyzer) -> bool:
    """The plain/imported subset of `_free_callee_kind` -- the callee-shape
    head of `_is_record_rvalue_source`'s by-value record-returning call
    face (native/template record returns stay AST there)."""
    kind = _free_callee_kind(e, analyzer)
    return kind is not None and kind[0] in ("plain", "imported")

def _call_eligible(e: TpyCall, locals_: dict[str, TpyType], analyzer,
                   *, stmt_position: bool = False,
                   container_ret_ok: bool = False,
                   storage_ret_ok: bool = False,
                   record_ret_ok: bool = False,
                   temps_ok: bool = False,
                   narrowed: 'set[str] | frozenset[str]' = frozenset()) -> bool:
    if _is_len_call(e, locals_, analyzer):
        return True
    # The generic-type instantiation face rides the storage sinks only (its
    # container result lands bare at the decl-init / return slots).
    if storage_ret_ok and _instantiation_call_eligible(e, locals_, analyzer):
        return True
    if (_is_len_native(e) and len(e.args) == 1 and not e.kwargs
            and e.double_star_unpack is None):
        # A len shape the arm above rejected: classify by arg shape. The
        # native-callee arm below cannot admit any of these (its container /
        # str pass-through rows are name-only), so the detail never
        # misattributes an accepted call.
        note_detail(_len_arg_reject(e.args[0], locals_, analyzer))
    kind = _free_callee_kind(e, analyzer)
    if kind is None:
        return False
    fi = e.resolved_function_info
    # Exact positional arity -- no omitted defaults, no varargs (the AST would
    # synthesize the missing/packed args, which the slice does not).
    if len(e.args) != len(fi.params):
        return note_detail("call.arity_defaults")
    # In value position the result must be an eligible scalar, Char, or a
    # str-slice value (owned str returns by value, StrView by view -- both emit
    # the bare call); as a bare statement the result is discarded, so a `void`
    # (None) return is admitted too. The emit (`callee(args);`) is identical
    # either way. In iterable position (`for x in make_list():`,
    # container_ret_ok) a non-value builtin-container return is admitted too --
    # the capture verdict rides `THIRForEach.iterable_lvalue`. At the storage
    # decl-init / return sinks (storage_ret_ok) container/tuple/union results
    # land bare too, and at the REF_ALIAS decl (record_ret_ok) a borrow
    # F1-record result binds `T& x = f(...);` -- both position-pinned by their
    # gate arms, never open in general value position.
    ret = analyzer.get_expr_type(e)
    if not (_eligible_scalar(ret) or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            or _eligible_ptr_value(ret, analyzer)
            or (stmt_position and is_void_like_type(ret))
            or (container_ret_ok and _nonvalue_container_ret(ret))
            or (storage_ret_ok
                and _storage_call_ret(ret, analyzer) is not None)
            or (record_ret_ok
                and _f1_record(unwrap_readonly(unwrap_ref_type(
                    unwrap_send_sync(ret))) if ret is not None else None,
                    analyzer))):
        return note_detail(_call_ret_reject(e, ret, analyzer))
    # A str-LITERAL arg to a multi-overload callee is pinned to its param's view
    # form (`std::string_view("...")`, _wants_str_literal_pin) -- the bare-literal
    # emit does not reproduce that, so the shape stays on the AST path. The AST
    # pin peels TpyCoerce wrappers, so a coerced literal must be caught too.
    # (A generic callee never pins; the plain kind still rejects fi.type_params.)
    if any(isinstance(_peel_coerce(a), TpyStrLiteral) for a in e.args):
        fis = analyzer.registry.get_function(e.func_name)
        if fis is not None and len(fis) > 1:
            return note_detail("call.strlit_overload_pin")
    # Every argument is an eligible SCALAR (a value type: copied, never moved,
    # so the bare call is byte-identical), a bare numeric literal resolved
    # against its param slot (a float literal into a double slot renders
    # repr(v) bare; an int literal arrives coerce-wrapped and rides the
    # passthrough -- a BigInt slot's `::tpy::BigInt(v)` wrap stays AST), a
    # str-slice value into a str-family
    # param (both spell the borrow `std::string_view` at the boundary, so the
    # bare emit is byte-identical), a bare-name CONTAINER into a non-Own
    # concrete container param (the other pass-through slot shape --
    # `use_list(xs)` emits the bare name on both paths), a bare-name F1-RECORD
    # into a non-Own same-record ref slot (`take_rec(a)` -- bare name whether
    # the slot is `const A&` or `A&`; a pointer-local renders `(*p)`), a
    # union-slot temp-free arg (the same-union pass-through, the
    # pointer-variant member/None inline lift, the union-coerced literal, or
    # the record-ctor rvalue into an `Own[union]` slot), or a slice-object
    # ctor rvalue into a by-value slice slot (`use(s, basic_slice(1, 3))` --
    # the bare template expansion). Any other non-value
    # arg (record rvalue / Own / Span / protocol slot) crosses an ownership or
    # conversion boundary -- an Own param at its last use auto-moves
    # (`f(std::move(p))`), a Span slot converts, a record RVALUE hoists a
    # `__tmp_N` -- which the bare-name THIRCall emit does not reproduce. The
    # union arms are DEEP-CONST-BLIND: a deep-const pointer-variant slot (a
    # `readonly[...]` annotation or the callee's `deep_const_borrow_params`
    # verdict, the AST's `is_readonly_target`) spells const pointees on the
    # member lift and takes the `ptr_variant_to_const` wrap on an
    # already-union arg -- both mirrored at lowering, which re-reads the same
    # fi facts (`_lower_union_arg_lift`), so admission needs no threading.
    # `temps_ok` (set only by the five flushable statement positions -- expr
    # stmt / var-decl init / name assign / scalar field write / return)
    # admits the arg-temp
    # rows: a member-valued scalar into a value-union slot, a record-ctor
    # rvalue into a same-nominal ref slot, and an lvalue into an `Own[T]`
    # slot (the copy+move cascade; a movable name's last use renders the
    # temp-free `std::move(name)` -- lowering picks). Conditions, iterables,
    # and nested calls never set it: a while-condition hoist is the BUGS.md
    # stale-snapshot miscompile, an elif temp breaks the flat `else if`
    # chain. The scalar pass-through arm is slot-checked against the Own
    # cascade (`_own_cascade_fires`): an `Own[scalar]` / `Own[scalar] | None`
    # slot temps a bare name on the AST path, so slot-blind admission would
    # silently render it bare; the rvalue shapes that DO render bare ride
    # the explicit `_own_scalar_rvalue_arg` row.
    if kind[0] in ("native", "template"):
        # The builtins arg loop (gen_template_or_native_call) calls
        # gen_call_arg DIRECTLY -- none of the plain loop's pre-arms
        # (_gen_optional_ptr_arg / _gen_union_arg / protocol / covariant /
        # ref-temp) run, and neither dcbp nor the str-literal overload pin
        # is threaded. Admit only the shared kwarg-independent rows;
        # optional-ptr, union, readonly-ctor, and the arg-temp rows stay
        # AST here.
        for a, p in zip(e.args, fi.params):
            if not (_shared_pass_through_arg(a, p.type, locals_, analyzer)
                    or _own_move_arg(a, p.type, locals_, analyzer)):
                return note_detail(_native_arg_reject(a, p.type, analyzer))
        return True
    if kind[0] == "generic":
        return _generic_plain_args_ok(e, locals_, analyzer, temps_ok=temps_ok)
    return _plain_call_args_ok(e, locals_, analyzer, temps_ok=temps_ok,
                               narrowed=narrowed)

def _plain_call_args_ok(e: TpyCall, locals_: dict[str, TpyType], analyzer,
                        *, temps_ok: bool,
                        narrowed: 'set[str] | frozenset[str]') -> bool:
    """The plain/imported free-callee ARG cascade -- the tail shared by
    `_call_eligible`'s plain branch and `_is_record_rvalue_source`'s by-value
    record-returning free-call face (both spell the bare `name(args)`, so the
    per-arg pass-through/temp decisions are identical). The callee-shape HEAD
    (linkage / literal-overload / generics / arity) and the str-literal
    overload pin stay at each caller -- the pin is per-call and threads
    `fi`/`func_name`, and `_call_eligible` also gates native/template kinds a
    record-return source never reaches. `temps_ok` admits the flush-position
    temp rows (value-union / record-rvalue / Own copy / optional-ptr ctor);
    `narrowed` keeps a narrowed subject off the temp rows (its read renames to
    the extraction alias, which lowering renders bare)."""
    fi = e.resolved_function_info
    return all(_shared_pass_through_arg(a, p.type, locals_, analyzer)
               or (temps_ok and _value_union_temp_arg(a, p.type, locals_,
                                                      narrowed, analyzer))
               or (temps_ok and _record_rvalue_temp_arg(a, p.type, locals_,
                                                        analyzer))
               or _own_move_arg(a, p.type, locals_, analyzer)
               or (temps_ok and _own_lvalue_arg(a, p.type, locals_,
                                                narrowed, analyzer))
               or _optional_ptr_arg(a, p.type, locals_, analyzer,
                                    temps_ok=temps_ok)
               or _readonly_record_ctor_arg(a, p.type, locals_, analyzer)
               or _union_pass_through_arg(a, p.type, locals_, analyzer)
               or _union_member_lift_arg(a, p.type, locals_, analyzer)
               or _union_coerced_literal_arg(a, p.type, locals_, analyzer)
               or _own_union_ctor_arg(a, p.type, locals_, analyzer)
               or _none_value_opt_arg(a, p.type, analyzer) is not None
               or note_detail(
                   "call.arg_shape." + _type_family_tag(p.type, analyzer))
               for a, p in zip(e.args, fi.params))

def _call_ret_reject(e: TpyCall, ret: 'TpyType | None', analyzer) -> str:
    """Drilldown label for a call result the value-position set does not
    admit -- splits call.ret_type by the return's type family so the tally
    ranks which result rung to open next (one bucket routinely hides
    several disjoint frontiers). Records split by value category: a borrow
    (`T&`) return is the REF_ALIAS decl frontier, an rvalue one the
    owned-record arm's residue (position/args/prescan rejects)."""
    if ret is None:
        return "call.ret_type.unresolved"
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
    if isinstance(t, OwnType):
        t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t.wrapped)))
    if is_void_like_type(t):
        return "call.ret_type.void"
    if isinstance(t, OptionalType):
        inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t.inner)))
        return ("call.ret_type.optional_record"
                if _f1_record(inner, analyzer)
                else "call.ret_type.optional_other")
    if isinstance(t, UnionType):
        return ("call.ret_type.union_value"
                if _eligible_value_union(t) is not None
                else "call.ret_type.union_ptr")
    if isinstance(t, TupleType):
        return "call.ret_type.tuple"
    if is_list(t) or is_dict(t) or is_set(t):
        return "call.ret_type.container"
    if isinstance(t, NominalType):
        if not _f1_record(t, analyzer):
            return "call.ret_type.record_other"
        return ("call.ret_type.record_rvalue"
                if is_rvalue_source(analyzer, e)
                else "call.ret_type.record_borrow")
    return "call.ret_type.other"

def _native_arg_reject(a: TpyExpr, ptype: 'TpyType | None', analyzer) -> str:
    """Drilldown label for a native/template callee arg that fails the shared
    pass-through set -- names WHICH plain-loop-only arg row it needs so the
    fallback tally ranks the native_arg_shape mass by shape (optptr / union /
    own / record-rvalue / other) rather than one opaque bucket."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
         if ptype is not None else None)
    if isinstance(t, OptionalType):
        return "call.native_arg.optptr"
    if isinstance(t, UnionType):
        return "call.native_arg.union"
    if isinstance(t, OwnType):
        return "call.native_arg.own"
    if isinstance(a, (TpyCall, TpyMethodCall)):
        return "call.native_arg.call_rvalue"
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if isinstance(at, NominalType):
        # Split F1 vs non-F1: an F1 record name here means a slot mismatch
        # (readonly/Own/unrelated slot); a non-F1 record is blocked on the
        # non-F1-record frontier (the shared elephant with receiver.field_nonf1
        # and sig.receiver_record).
        return ("call.native_arg.record_nonf1"
                if not _f1_record(at, analyzer)
                else "call.native_arg.record_f1_slot")
    return "call.native_arg.other"

def _generic_plain_args_ok(e: TpyCall, locals_: dict[str, TpyType],
                           analyzer, *, temps_ok: bool) -> bool:
    """The plain-generic-callee arg loop (`pick(1, 2)` ->
    `pick<int32_t>(__tmp_1, __tmp_2)`): args resolve against the ROOT
    stub's params with the inferred substitution, mirroring the AST's
    registry-branch loop. A TypeParamRef slot resolves to `param_val_or_
    ref_t<T>` in C++, so a TEMPORARY arg hoists a named temp typed at the
    RESOLVED slot (`int32_t __tmp_1 = 1;` -- TempState.create's
    `to_cpp()` render, flush positions only); an lvalue NAME binds bare.
    The slice: TypeParamRef slots resolved to eligible scalars with
    literal (temp) or scalar-name (bare) args, and concrete slots through
    the shared pass-through rows (the AST's pre-arms -- protocol /
    covariant / optional-ptr / union slots -- have no row here and
    reject). A still-unresolved slot or a repr-subst-marked call never
    reaches this loop (`_free_callee_kind` rejects both)."""
    root, subst = _generic_root_subst(e, analyzer)
    for a, p in zip(e.args, root.params):
        ptype = unwrap_ref_type(p.type) if isinstance(p.type, TpyType) else None
        if ptype is None:
            return note_detail("call.generic_arg_slot")
        resolved = substitute_type_params_simple(ptype, subst)
        if contains_type_param(resolved):
            return note_detail("call.generic_arg_slot")
        if isinstance(ptype, TypeParamRef):
            if not _eligible_scalar(resolved):
                return note_detail("call.generic_arg_slot")
            lit = _peel_coerce(a)
            if isinstance(lit, (TpyIntLiteral, TpyFloatLiteral,
                                TpyBoolLiteral)):
                # A literal (possibly coerce-wrapped) is a temporary: the
                # ref-slot temp rule hoists `<resolved> __tmp_N = <lit>;`
                # -- flush positions only.
                if not temps_ok:
                    return note_detail("call.generic_arg_shape")
                if not _expr_eligible(a, locals_, analyzer):
                    return False
                continue
            if isinstance(a, TpyName):
                # An lvalue name binds the `const T&`/`T&` slot bare.
                if a.name == "self" or a.name not in locals_:
                    return note_detail("call.generic_arg_shape")
                if not (_resolved_scalar(locals_.get(a.name), analyzer)
                        and _expr_eligible(a, locals_, analyzer)):
                    return note_detail("call.generic_arg_shape")
                continue
            return note_detail("call.generic_arg_shape")
        if not (_shared_pass_through_arg(a, resolved, locals_, analyzer)
                or _own_move_arg(a, resolved, locals_, analyzer)):
            return note_detail("call.generic_arg_shape")
    return True

def _shared_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                             locals_: dict[str, TpyType], analyzer,
                             *, mutated: bool = False) -> bool:
    """The arg rows whose render lives inside gen_call_arg itself --
    independent of the plain loop's pre-arms and of the dcbp/pin kwargs
    the builtins loop does not thread -- shared by the plain AND
    native/template arg loops. Their slot domains are disjoint from the
    plain-loop-only rows (arg-temps / optional-ptr / union / readonly-ctor),
    so hoisting them ahead of those rows never changes admission. A new
    arg row belongs here iff a native/template callee renders it
    identically; otherwise it goes in the plain loop only.

    `mutated` marks a MUTATED ctor slot (`T&`, non-const ref): a temp /
    prvalue source binds it ill-formed (the mutated-String-param AST
    miscompile, BUGS.md), so the temp-producing rows gate off -- the
    str->String coerce half, the opt-str shim, the bytes-literal pin, and
    the value-tuple literal. The by-value rows (scalars / float / BigInt
    literal / char / enum / Ptr / slice-rvalue / Own rvalues -- mutation
    is callee-local, the slot stays by value) and the lvalue-NAME rows
    (record / container / owned-String names bind a `T&` legally; sema's
    readonly system rejects a const violation upstream) stay admitted."""
    return ((_eligible_scalar(analyzer.get_expr_type(a))
             and not _member_valued_union_slot(a, ptype, analyzer)
             and not _own_cascade_fires(ptype)
             and _expr_eligible(a, locals_, analyzer))
            or _own_scalar_rvalue_arg(a, ptype, locals_, analyzer)
            or _own_record_rvalue_arg(a, ptype, locals_, analyzer)
            or _float_literal_pass_through_arg(a, ptype, locals_, analyzer)
            or _int_literal_bigint_arg(a, ptype, locals_, analyzer)
            or _str_pass_through_arg(a, ptype, locals_, analyzer,
                                     mutated=mutated)
            or (not mutated
                and _opt_str_shim_arg(a, ptype, locals_, analyzer))
            or (not mutated
                and _bytes_pass_through_arg(a, ptype, locals_, analyzer))
            or _char_pass_through_arg(a, ptype, locals_, analyzer)
            or _container_pass_through_arg(a, ptype, locals_, analyzer)
            or _slice_ctor_pass_through_arg(a, ptype, locals_, analyzer)
            or _enum_pass_through_arg(a, ptype, locals_, analyzer)
            or _ptr_pass_through_arg(a, ptype, locals_, analyzer)
            or _value_tuple_pass_through_arg(a, ptype, locals_, analyzer,
                                             mutated=mutated)
            or _record_pass_through_arg(a, ptype, locals_, analyzer))

def _value_tuple_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                                  locals_: dict[str, TpyType],
                                  analyzer, *, mutated: bool = False) -> bool:
    """A value-tuple arg into a value-tuple slot (`const std::tuple<...>&`):
    a bare in-scope name of the same value-tuple family (an lvalue binding
    the ref slot directly -- bare on both paths; tuples are value types, so
    no move/temp cascade fires) or a tuple literal (the spelled brace-init
    render, target-threaded per element by gen_call_arg -- identical for
    plain and native/template callees, so the row is shared). An
    `Own[tuple]` slot is outside `_value_tuple` (the Own wrapper is not a
    TupleType), so the move cascade never reaches this row. A MUTATED slot
    (an address-escaped tuple param can drop the const) keeps the lvalue
    name and rejects the literal (a prvalue into a non-const ref)."""
    vt = _value_tuple(ptype, analyzer)
    if vt is None:
        return False
    if isinstance(a, TpyName):
        return (a.name in locals_
                and _value_tuple(locals_[a.name], analyzer) is not None)
    return not mutated and _tuple_literal_ok(a, vt, locals_, analyzer)

def _ptr_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                          locals_: dict[str, TpyType], analyzer) -> bool:
    """A `Ptr[T]` value (name / field read) into a Ptr value slot: the
    ownership cascade never fires for the by-value pointer slot (`own is
    None`), so both paths render the bare value. An `Own[...]` slot is not a
    PtrType after the unwraps (auto-move cascade -> AST), a union slot lifts
    (-> AST), and a coerce-wrapped arg (e.g. a const-adding conversion the
    sema spells) falls back via `_expr_eligible`'s coerce dispositions."""
    if not _eligible_ptr_value(ptype if isinstance(ptype, TpyType) else None,
                               analyzer):
        return False
    return (_eligible_ptr_value(analyzer.get_expr_type(a), analyzer)
            and _expr_eligible(a, locals_, analyzer))

def _slice_ctor_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                                 locals_: dict[str, TpyType], analyzer) -> bool:
    """A slice-object ctor rvalue (`basic_slice(1, 3)` / `slice(a, b, c)`) into
    a by-value slice-object param slot: gen_call_arg's ownership cascade never
    fires for the value slot (`own is None`), so both paths render the bare
    template expansion (`use(s, ::tpy::BasicSlice{1, 3})`). `_slice_object_type`
    does not peel Own, so an `Own[...]` slot rejects (the auto-move cascade);
    a union slot (`Int32 | basic_slice`) lifts into the variant -> AST path.
    Slice-typed NAME args stay deferred with the other rvalue-ctor arg shapes."""
    if not _slice_object_type(ptype if isinstance(ptype, TpyType) else None):
        return False
    return (isinstance(a, TpyCall)
            and _slice_ctor_call_eligible(a, locals_, analyzer))

def _union_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bare-name union arg into a non-Own slot of the SAME union type (F4).
    An already-union source skips `_gen_union_arg`'s member-lift arms and
    falls to the default bare-name render (a value union's `const
    std::variant<...>&` binds directly; a pointer variant copies by value) --
    or, for a DEEP-CONST pointer-variant slot (a `readonly[...]` annotation
    or the callee's `deep_const_borrow_params` verdict), the
    `ptr_variant_to_const` wrap, mirrored at lowering keyed on the same
    verdict -- so admission here is readonly-blind. A member-valued arg (a
    scalar name, a float literal, a record rvalue) hoists a temp on the AST
    path -- the arg-temp rows where the position flushes, AST otherwise; the
    temp-free member rows ride `_union_member_lift_arg`. `Own[union]` slots
    auto-move -> AST.
    NB a const-lifted pointer-variant LOCAL into a mutable slot renders the
    bare name on BOTH paths (a pre-existing AST miscompile, BUGS.md) --
    byte-identical, so the shape is not carved out here."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_send_sync(pt))
    if isinstance(pt, OwnType):
        return False
    ut = _eligible_value_union(pt)
    if ut is None:
        ut = _eligible_ptr_union(pt, analyzer)
        if ut is None:
            return False
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    return at == ut and _expr_eligible(a, locals_, analyzer)

def _value_union_temp_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType],
                          narrowed: 'set[str] | frozenset[str]',
                          analyzer) -> bool:
    """Gate arm for the value-union temp row -- the slot/shape verdict plus
    the gate-side arg checks (`_expr_eligible`, the narrowed-name reject)."""
    if _value_union_temp_slot(a, ptype, analyzer) is None:
        return False
    if isinstance(a, TpyName) and a.name in narrowed:
        return False
    return _expr_eligible(a, locals_, analyzer)

def _record_rvalue_temp_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """Gate arm for the record-rvalue temp row -- the slot/shape verdict plus
    the rvalue's own eligibility. A ctor rides `_record_ctor_call_eligible`
    (its str-slot arg loop is a superset of the by-value face's scalar-only
    loop); a by-value record-returning call rides `_is_record_rvalue_source`
    (its callee-shape head + scalar args). The nested arg is scalar-only
    (`temps_ok=False`): a two-level record-rvalue temp would need a second
    statement-level flush the one-arg hoist here cannot reproduce."""
    if _record_rvalue_temp_slot(a, ptype, analyzer) is None:
        return False
    return (_record_ctor_call_eligible(a, locals_, analyzer)
            or _is_record_rvalue_source(a, locals_, analyzer))

def _own_scalar_rvalue_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """An rvalue-shaped eligible scalar into a plain `Own[scalar]` slot: the
    by-value slot binds the rvalue directly (no `_maybe_move` for a non-name,
    no copy temp for a non-simple-lvalue, the value-type `else` tail), so
    both paths render bare -- a coerced int literal (`takes(5)`), a scalar
    ctor / call rvalue, a binop. A bare NAME / field lvalue hoists the
    copy+move temp (`_own_lvalue_arg`); a coerce-WRAPPED lvalue splits on the
    AST's rendered-identity check (`needs_copy`) -- not mirrored, stays AST."""
    w = _plain_own_slot(ptype)
    if w is None or not _eligible_scalar(w):
        return False
    if isinstance(_peel_coerce(a), (TpyName, TpyFieldAccess)):
        return False
    at = analyzer.get_expr_type(a)
    return (_eligible_scalar(at) and _expr_eligible(a, locals_, analyzer)
            and _witness("own.scalar_rvalue"))

def _own_record_rvalue_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A same-module record rvalue CALL (a ctor `A(7)` or a by-value
    record-returning free call) into a plain `Own[record]` SAME-nominal slot:
    an rvalue binds the `T&&` slot directly (gen_call_arg's rvalue-source
    `else` tail -- no temp), so both paths render the bare expansion,
    mirroring the `Own[union]`-slot ctor arm (`_own_union_ctor_arg`). A
    borrow-returning callee is not an rvalue source (the AST copies it
    through a temp) -> AST; the same-nominal check is a slice guard (sema
    rejects an upcast into an Own slot outright)."""
    w = _plain_own_slot(ptype)
    if w is None or not _f1_record(w, analyzer):
        return False
    if not isinstance(a, TpyCall):
        return False
    if analyzer.get_expr_type(a) != w:
        return False
    fi = a.resolved_function_info
    if fi is None:
        return False
    if fi.is_constructor:
        return (_record_ctor_call_eligible(a, locals_, analyzer)
                and _witness("own.record_rvalue"))
    return (_is_record_rvalue_source(a, locals_, analyzer)
            and _witness("own.record_rvalue"))

def _own_move_arg(a: TpyExpr, ptype: TpyType | None,
                  locals_: dict[str, TpyType], analyzer) -> bool:
    """The TEMP-FREE half of the Own-slot cascade: a movable OWN-param name
    at its LAST USE renders `std::move(name)` in ANY position (gen_call_arg's
    `_maybe_move` fires before the copy-temp arm, so no flush is needed) --
    the `heap_take(value)` ctor-MIL shape. Gate-side movability mirrors
    _LowerCtx's param seeding exactly: an `Own[...]`-declared binding of
    NON-VALUE payload (a value payload is never seeded, so its last use
    copies); body-movable locals keep riding the flushable copy+move row
    (`_own_lvalue_arg`), whose lowering picks the move when it applies."""
    if _own_lvalue_temp_slot(a, ptype, analyzer) is None:
        return False
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    own = unwrap_optional_own(unwrap_readonly(unwrap_send_sync(
        locals_[a.name])))
    if own is None or own.wrapped.is_value_type():
        return False
    return id(a) in analyzer.ctx.all_last_uses

def _own_lvalue_arg(a: TpyExpr, ptype: TpyType | None,
                    locals_: dict[str, TpyType],
                    narrowed: 'set[str] | frozenset[str]', analyzer) -> bool:
    """Gate arm for the Own-slot copy+move row -- the slot/shape verdict plus
    the gate-side arg checks. Both outcomes (the `__tmp_N` copy and the
    last-use `std::move(name)`) are expressible, so the gate admits the shape
    wholesale under `temps_ok` and lowering picks; restricting the temp-free
    move to the flushable positions is gate-narrowing only (a move arg in a
    condition stays AST -- deferred)."""
    if _own_lvalue_temp_slot(a, ptype, analyzer) is None:
        return False
    if isinstance(a, TpyName):
        if a.name == "self" or a.name in narrowed or a.name not in locals_:
            return False
    return _expr_eligible(a, locals_, analyzer)

def _readonly_record_ctor_arg(a: TpyExpr, ptype: TpyType | None,
                              locals_: dict[str, TpyType], analyzer) -> bool:
    """A record-ctor rvalue into a readonly-ANNOTATED same-record slot
    (`take_ro(A(7))`, emitted `const A&`): the free-call ref-param temp arm
    keys on `is_ref_param()`, which the `readonly[...]` wrapper defeats, and
    the const ref binds the rvalue directly -- so both paths render the bare
    ctor expansion (no temp). The NAME face (the readonly pass-through)
    stays deferred with the other deep-const rows."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None or not isinstance(pt, ReadonlyType):
        return False
    inner = unwrap_readonly(pt)
    if not (isinstance(inner, NominalType) and _f1_record(inner, analyzer)):
        return False
    if not isinstance(a, TpyCall):
        return False
    if analyzer.get_expr_type(a) != inner:
        return False
    return (_record_ctor_call_eligible(a, locals_, analyzer)
            and _witness("own.readonly_ctor"))

def _optional_ptr_arg(a: TpyExpr, ptype: TpyType | None,
                      locals_: dict[str, TpyType], analyzer,
                      *, temps_ok: bool) -> bool:
    """Gate arm for the pointer-repr Optional slot faces -- the shared face
    verdict plus the gate-side checks per face. The 'name' face admits both
    renders (`&(name)` and the pointer-local bare pass); lowering splits on
    `lc.pointers`. A NARROWED union subject is admitted on the 'name' face
    too: its read renames to the `T&` extraction alias inside `_lower_expr`,
    and the AST's `_gen_optional_ptr_arg` tail wraps the same alias
    (`&(__u)`) -- the render mirrors, so no narrowed reject (the face is
    temp-free and reachable from `_expr_eligible`, where `narrowed` is not
    threaded; a reject here but not there would be exactly the gate/lowering
    drift the shared verdict exists to prevent)."""
    face = _optional_ptr_arg_face(a, ptype, analyzer)
    if face is None:
        return False
    if face == 'none':
        return True
    if face == 'ctor':
        return temps_ok and _record_ctor_call_eligible(a, locals_, analyzer)
    if face == 'lift':
        return _field_receiver_ok(a, locals_, analyzer)
    # 'name' / 'pass'
    if a.name not in locals_:
        return False
    return _expr_eligible(a, locals_, analyzer)

def _union_member_lift_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """The temp-free member rows of `_gen_union_arg`'s pointer-variant branch:
    a `None` literal (`pv{std::monostate{}}`) or a member-typed record NAME
    (`pv{&(name)}` -- never a temp: names are never rvalue sources) into a
    non-Own pointer-variant slot. A deep-const slot (a `readonly[...]`
    annotation or the callee's `deep_const_borrow_params` verdict) takes the
    same lift with the const-pointee variant spelling -- mirrored at
    lowering, so admission is const-blind. A record RVALUE (`take(A(n))`)
    hoists a named temp -> AST. A narrowed subject is admitted here too (its
    `locals_` type is the member): the AST's `already_union` verdict (the C++
    binding is still the variant) renders it as the bare extraction alias,
    which lowering mirrors -- another byte-identical pre-existing AST
    miscompile (an `A&` alias into a variant slot; see BUGS.md's
    union-operand class)."""
    slot = _arg_ptr_union_slot(ptype, analyzer)
    if slot is None:
        return False
    ut, _deep_const = slot
    if isinstance(a, TpyNoneLiteral):
        # Slice guard: sema only types None here when the union has a None
        # member (the monostate slot).
        return any(is_void_like_type(m) for m in ut.members)
    if not isinstance(a, TpyName) or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    return (any(at == m for m in ut.members if not is_void_like_type(m))
            and _expr_eligible(a, locals_, analyzer))

def _union_coerced_literal_arg(a: TpyExpr, ptype: TpyType | None,
                               locals_: dict[str, TpyType], analyzer) -> bool:
    """An int literal into a VALUE-union slot (`take_vu(3)`): sema coerces the
    literal to the union itself, so `_gen_union_arg`'s `already_union` verdict
    falls to the default gen_call_arg render -- the bare literal (the variant
    converting ctor does the work). A bare member-typed literal (`take_vu(2.5)`
    -- a float literal is typed at the member, not the union) hoists the
    `std::variant<...> __tmp_N` temp -> AST path. Only value unions arise: a
    numeric literal cannot coerce to a record union."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None or not isinstance(a, TpyCoerce):
        return False
    ut = _eligible_value_union(unwrap_readonly(unwrap_send_sync(pt)))
    if ut is None:
        return False
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    return at == ut and _expr_eligible(a, locals_, analyzer)

def _own_union_ctor_arg(a: TpyExpr, ptype: TpyType | None,
                        locals_: dict[str, TpyType], analyzer) -> bool:
    """A same-module record-ctor rvalue into an `Own[union]` value-variant slot
    (`take_own(A(7))` -> `take_own(A(7))`): gen_call_arg's Own cascade is inert
    for the shape -- the ctor is an rvalue (no `_maybe_move`, no copy temp) and
    no `to_value_variant` lift fires (a ctor is not a ptr-variant source) -- so
    both paths render the bare ctor expansion. The arg record must be a member
    of the union (slice guard; sema enforces). An Own[union] slot with a NAME
    arg is the auto-move cascade (`std::move(u)`) -> AST path."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_send_sync(pt)
    if isinstance(pt, ReadonlyType):
        return False
    if not isinstance(pt, OwnType):
        return False
    ut = _eligible_ptr_union(pt.wrapped, analyzer)
    if ut is None:
        return False
    if not isinstance(a, TpyCall):
        return False
    rt = analyzer.get_expr_type(a)
    if not any(rt == m for m in ut.members if not is_void_like_type(m)):
        return False
    return (_record_ctor_call_eligible(a, locals_, analyzer)
            and _witness("own.union_ctor"))

def _record_ctor_call_eligible(e: TpyCall, locals_: dict[str, TpyType],
                               analyzer) -> bool:
    """The record-ctor shape core (`_ctor_shape_ok`) plus the scalar/str-slot
    arg loop. Admitted as the `Own[union]`-slot ctor-rvalue arg
    (`_own_union_ctor_arg`), as a method arg into a const same-record slot
    (`_method_ctor_rvalue_arg`), and as the record-rvalue arg-temp init
    (`_record_rvalue_temp_arg`).

    Args are eligible value scalars into PLAIN scalar slots or str-slice
    sources into str-family slots (the free-call pass-through rule): neither
    slot kind triggers the protocol/dynamic/covariant/optional-ptr/union
    arms, so both fall to `gen_call_arg`'s bare render exactly like a free
    call's. A scalar/view slot is never a ref param, and a MUTATED `String`
    slot (lowered `std::string&`, where the `ctor_mutated` rvalue-temp arm
    could fire) rejects; an `Own[...]` slot copy+moves through a temp ->
    AST."""
    if not _ctor_shape_ok(e, analyzer):
        return False
    fi = e.resolved_function_info
    mut = fi.mutated_params

    def _arg_ok(i: int, a: TpyExpr, p: ParamInfo) -> bool:
        pt = unwrap_readonly(unwrap_ref_type(p.type))
        if _eligible_scalar(pt):
            return (_resolved_scalar(analyzer.get_expr_type(a), analyzer)
                    and _expr_eligible(a, locals_, analyzer))
        # A record-rvalue arg into a same-nominal CONST record slot binds the
        # inline prvalue expansion (temp-free, so admissible at any nesting
        # depth); a MUTATED slot needs the named-temp flush that only the
        # statement-position rows admit (`_is_record_rvalue_source`'s ctor
        # face), so it rejects here.
        if (_record_rvalue_temp_slot(a, p.type, analyzer) is not None
                and (mut is None or i not in mut)):
            return ((_record_ctor_call_eligible(a, locals_, analyzer)
                     or _is_record_rvalue_source(a, locals_, analyzer))
                    and _witness("ctor.const_rvalue_arg"))
        st = unwrap_send_sync(pt) if isinstance(pt, TpyType) else pt
        if (isinstance(st, NominalType) and is_string_type(st)
                and (mut is None or i in mut)):
            return False
        return (_str_pass_through_arg(a, p.type, locals_, analyzer)
                and _witness("ctor.str_arg"))

    return all(_arg_ok(i, a, p)
               for i, (a, p) in enumerate(zip(e.args, fi.params)))

def _ctor_arity_ok(e: TpyCall, fi) -> bool:
    """Positional arity for a raw-name record-ctor call. Exact arity is the
    common case; fewer args are admitted when the OMITTED trailing params all
    carry a default. Each default is rendered onto the C++ ctor signature
    (`records.py` emits it via `emit_defaults`), so the call passes only the
    provided args -- byte-identical to the exact-arity `Name(args)` emit. A
    variadic slot has no positional default to fall back on, and more args than
    params is a resolution the raw-name shape never produces -> AST."""
    n = len(e.args)
    params = fi.params
    if n == len(params):
        return True
    if n > len(params):
        return False
    tail = params[n:]
    if not all(p.has_default and not p.is_variadic for p in tail):
        return False
    return _witness("ctor.omit_defaults")

def _ctor_shape_ok(e: TpyCall, analyzer) -> bool:
    """A bare-name SAME-MODULE plain user-record constructor call in the
    `Name(args)` emit shape -- `_gen_call`'s record-branch tail: the RAW source
    name (no escape, no qualification), args through `_gen_record_ctor_args`
    with every special arm structurally unreachable. This is the arg-blind
    shape/registry core shared by `_record_ctor_call_eligible` (which adds
    its plain-scalar-slot arg loop) and `_is_record_rvalue_source`'s ctor
    face (whose arg loop admits the F2d source shapes). Native /
    cpp_template / cross-module / multi-overload / TypedDict ctors take
    other emit shapes -> AST."""
    if not isinstance(e.func, TpyName):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if (e.call_type is not None or e.type_args or e.inferred_type_args
            or e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert
            or e.subscript_callee is not None):
        return False
    # A same-name free function wins _gen_call's registry branch before the
    # record lookup; a name resolved through the import table qualifies.
    if analyzer.registry.get_function(e.func_name):
        return False
    if e.func_name in analyzer.imported_names:
        return False
    # Sema attaches a SYNTHETIC constructor fi (`is_constructor`, named after
    # the record, params = the resolved __init__'s); a builtin type ctor
    # (`Int32(x)`) resolves to the real @cpp_template __init__ instead and
    # rides `_scalar_ctor_call_eligible`.
    fi = e.resolved_function_info
    if fi is None or not fi.is_constructor:
        return False
    if fi.cpp_template or fi.native_function or fi.native_name:
        return False
    if not _ctor_arity_ok(e, fi):
        return False
    ri = analyzer.registry.get_record(e.func_name)
    if ri is None or ri.is_native or ri.builtin_type_key is not None:
        return False
    if ri.type_params:  # generic ctor: substituted/spelled type args -> AST
        return False
    # The short-name collision override: when the sema result type resolves
    # (qname-first) to a DIFFERENT record, the AST trusts it -- reject the
    # ambiguous shape rather than mirror the override.
    rt = analyzer.get_expr_type(e)
    if not (isinstance(rt, NominalType) and rt.is_user_record):
        return False
    if analyzer.registry.get_record_for_type(rt) is not ri:
        return False
    # Cross-module ctors qualify to the declaring module -> AST.
    if analyzer.registry.record_qualification(
            ri, analyzer.ctx.module_name) is not None:
        return False
    # A multi-overload __init__ set: _gen_record_ctor_args reads the record's
    # init_info params, which may disagree with the resolved stub -> AST. The
    # special member forms are read off the REAL __init__ (the synthetic fi
    # carries only params + mutation facts).
    overloads = ri.get_method_overloads("__init__")
    if not overloads:
        # No OWN __init__: the implicit default ctor. Reaching here with a
        # non-None fi means sema attached the synthetic zero-param ctor fi
        # (own-init-less records only -- a record with an INHERITED param-ful
        # __init__ gets no fi and rejected above), so the arity gate pinned
        # the call zero-arg, and a zero-arg call renders the same bare
        # `Name()` -- no arg machinery to disagree with.
        return True
    if len(overloads) != 1:
        return False
    init_fi = overloads[0]
    if (init_fi.cpp_template or init_fi.native_function or init_fi.native_name
            or init_fi.is_consuming or init_fi.error_return_type is not None
            or init_fi.native_cpp_return_type is not None
            or any(isinstance(p.type, LiteralType) for p in init_fi.params)):
        return False
    return True

def _ctor_instantiation_ok(e: TpyCall, analyzer) -> bool:
    """The INSTANTIATION form of a record-ctor call -- spelled
    `Cell[Int32]()` / `Poll[T]()` or inferred `Pair(1, 2)` (`call_type`
    set): `_gen_call`'s call_type-branch tail renders
    `type_to_cpp(call_type)(args)`, mirrored as `THIRCtorCall.type_cpp =
    lc.render_type(call_type)` -- byte-identical by construction, so
    cross-module and generic spellings need no extra gating beyond
    `_f1_record`'s type-arg slice. Shares the arg rows with the raw-name
    face (the scalar / record-rvalue slice coincides across the two AST
    arg loops); the None-literal / array-literal / `T()`-construct
    targeted arms and protocol/union/optional slots are excluded by those
    rows. Native records (native fi arms) and template/native ctor fis
    take other emit arms -> AST."""
    if not isinstance(e.func, TpyName):
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    ct = e.call_type
    if ct is None or not isinstance(ct, NominalType):
        return False
    if (e.enum_from_value is not None or e.cast_target_type is not None
            or e.isinstance_var is not None or e.dunder_call is not None
            or e.macro_expansion is not None or e.compile_time_assert):
        return False
    if e.args and isinstance(e.args[0], TpyListRepeat):
        return False
    fi = e.resolved_function_info
    if fi is None or not fi.is_constructor:
        return False
    if (fi.cpp_template or fi.native_function or fi.native_name
            or fi.error_return_type is not None):
        return False
    if len(e.args) != len(fi.params):
        return False
    if not _f1_record(ct, analyzer):
        return False
    ri = analyzer.registry.get_record_for_type(ct)
    if ri is None:
        return False
    # A NATIVE record's zero-arg instantiation (`UninitStorage[T]()`) renders
    # the same `type_to_cpp(call_type)()` (native_cpp_names spelling) with no
    # arg arms to diverge; an arg-ful native ctor may resolve @native/@
    # cpp_template __init__ overloads with their own emit arms -> AST.
    if ri.is_native and e.args:
        return False
    return True

def _str_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType], analyzer,
                          *, mutated: bool = False) -> bool:
    """A str-slice arg into a non-Own `str`/`StrView`/`String` param slot. A
    `str`/`StrView` param renders `std::string_view`, and every slice source
    lands in it bare: a param/view local IS a string_view, an owned local
    converts implicitly, a literal is const char[N]. A `String` slot takes
    String values bare and coerced str/StrView sources through the coerce
    arm. An `Own[...]` slot materializes an owned copy the bare emit does not
    reproduce (the gen_call_arg auto-move cascade) -> AST path.
    A MUTATED String slot (`std::string&`) keeps only an owned-String NAME
    (an lvalue binding the ref legally): the coerce half materializes a
    `std::string(x)` temp -- the mutated-String-param miscompile. The
    view-slot branch is mutation-blind (`std::string_view` stays by value)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if isinstance(pt, NominalType) and is_string_type(pt):
        # A `String` slot (`const std::string&`): an owned String value binds
        # bare; a str/StrView source arrives as a str_to_string /
        # strview_to_string coerce (identity for a NUL-free literal,
        # `std::string(x)` otherwise) -- rendered by the coerce arm itself.
        at = analyzer.get_expr_type(a)
        if mutated:
            return (isinstance(a, TpyName) and _is_string_owned(at)
                    and _expr_eligible(a, locals_, analyzer))
        return ((_resolved_str_value(at, analyzer) is not None
                 or _is_string_owned(at))
                and _expr_eligible(a, locals_, analyzer))
    if not (isinstance(pt, NominalType) and (is_str_type(pt) or is_str_view_type(pt))):
        return False
    if isinstance(a, TpyStrLiteral):
        return True
    return (_resolved_str_value(analyzer.get_expr_type(a), analyzer) is not None
            and _expr_eligible(a, locals_, analyzer))

def _opt_str_shim_arg(a: TpyExpr, ptype: TpyType | None,
                      locals_: dict[str, TpyType], analyzer) -> bool:
    """A value-repr `Optional[str]` param NAME into another value-repr
    `Optional[str]` slot -- the AST's `_maybe_convert_opt_view_param` same-TPy-
    type ARG split (`s ? std::make_optional(std::string(*s)) : std::nullopt`).
    Fires for the WHOLE optional whether or not sema narrowed the read: gen_expr
    threads the slot type (Optional[str]), so the shim renders on the bare
    binding. `_opt_view_arg_shim` pins the exact source/slot family match the
    AST shim tests (an owned-`str` inner both sides; a `StrView` inner passes
    bare -> rejected here)."""
    if not (isinstance(a, TpyName)
            and a.name in locals_):
        return False
    return _opt_view_arg_shim(
        locals_.get(a.name),
        ptype if isinstance(ptype, TpyType) else None, analyzer)

def _str_owned_slot_arg(a: TpyExpr, ptype: TpyType | None,
                        locals_: dict[str, TpyType],
                        param_names: 'set[str] | frozenset[str]',
                        analyzer) -> bool:
    """A str-slice arg into an `Own[str]` container element slot -- the
    `xs.append(s)` shape `_str_pass_through_arg` rejects (Own is its cutoff). A
    str LITERAL lands bare (const char[N] -> the vector's `std::string` ctor); a
    VIEW-form source materializes an owned copy `std::string(x)` via the S1
    view->owned THIRFormConvert (the same wrap `_lower_container_elem` applies at
    literal-element positions). Two view forms qualify: a `StrView`-resolved
    local, and a `str` PARAM -- resolved `str`, not `StrView`, but the signature
    spells `std::string_view`, so its read is BORROW too. They are told apart
    from an owned `str` local (the STORAGE form that would MISS the copy) by
    `_str_name_form`'s rule, mirrored: a `StrView` resolution OR the name being a
    param. An owned STORAGE source (an owned `str`/`String` local, a
    subscript-owned or call-owned result) rides gen_call_arg's copy+move-temp
    cascade, which the bare/convert emit does not reproduce -- left on the AST
    path."""
    w = _plain_own_slot(ptype)
    if w is None or not is_str_type(w):
        return False
    if isinstance(a, TpyStrLiteral):
        return True
    at = _resolved_str_value(analyzer.get_expr_type(a), analyzer)
    if at is None or not _expr_eligible(a, locals_, analyzer):
        return False
    if is_str_view_type(at):
        return True
    # An owned-`str`-typed source is STORAGE unless it is a str PARAM (BORROW --
    # `std::string_view` in the signature). A reassigned str param is already
    # whole-body-rejected, so the view form is stable at every use here.
    return isinstance(a, TpyName) and a.name in param_names

def _bytes_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bytes-slice arg into a non-Own `bytes`/`BytesView` param slot. The
    param renders `std::span<const uint8_t>`; a param/view local IS a span, an
    owned local (vector) converts implicitly, and a literal takes gen_call_arg's
    static-span pin (`::tpy::bytes_literal(...)`, lowered BORROW). The
    pin keys on the RAW ptype (`is_bytes_type(ptype) or is_bytes_view_type(
    ptype)`), so a wrapped slot (readonly/Own) rejects the literal -- the AST
    renders it owned there, a shape this arm does not thread. An `Own[bytes]`
    slot materializes an owned copy for value args too -> AST path."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    # gen_call_arg peels coerce wrappers before its literal span pin (a literal
    # into a BytesView slot arrives wrapped in the view coercion); mirror it.
    lit = _peel_coerce(a)
    if isinstance(lit, TpyBytesLiteral):
        return is_bytes_type(pt) or is_bytes_view_type(pt)
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    if not (isinstance(pt, NominalType) and (is_bytes_type(pt) or is_bytes_view_type(pt))):
        return False
    return (_resolved_bytes_value(analyzer.get_expr_type(a), analyzer) is not None
            and _expr_eligible(a, locals_, analyzer))

def _char_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                           locals_: dict[str, TpyType], analyzer) -> bool:
    """A Char value into a Char param slot -- both spell `char`, passed bare
    (a value scalar in all but name) -- or a single-char str literal into one
    (the target-typed `'x'` render, gen_expr's char-literal arm; lowered
    param-aware via `_lower_char_targeted`). A multi-char literal never
    renders as a char literal -> AST path (sema rejects it anyway). An
    `Own[Char]` slot is rejected conservatively (the unwrap chain does not
    peel Own)."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if not _eligible_char(pt):
        return False
    if isinstance(a, TpyStrLiteral):
        return len(a.value) == 1
    return (_eligible_char(analyzer.get_expr_type(a))
            and _expr_eligible(a, locals_, analyzer))

def _int_literal_bigint_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """A bare int literal (or folded `-3`) into a BigInt param slot: sema
    leaves it unwrapped (unlike a fixed-int slot's range-checked
    `int_literal_to_fixed_int` coerce), and gen_call_arg threads the slot
    into the literal render (`::tpy::BigInt(10)`) -- mirrored by the
    `_slot_literal_retype` at `_lower_call_arg`'s tail."""
    if not (isinstance(a, TpyIntLiteral)
            or _folded_neg_int_literal(a, analyzer) is not None):
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    return is_big_int_type(pt) and _expr_eligible(a, locals_, analyzer)

def _float_literal_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                                    locals_: dict[str, TpyType],
                                    analyzer) -> bool:
    """A bare float literal (FloatLiteralType -- sema leaves it unwrapped in a
    matching float slot) into a float param slot: a double slot renders
    repr(v) bare on both paths (gen_expr's TpyFloatLiteral double branch ==
    _emit_literal's float arm); a Float32 slot takes the `f` suffix via the
    `_slot_literal_retype` at `_lower_call_arg`'s tail. inf/nan literals
    (`1e400`) are rejected by _expr_eligible's isfinite check."""
    if not isinstance(a, TpyFloatLiteral):
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(pt)))
    return is_float_type(pt) and _expr_eligible(a, locals_, analyzer)

def _record_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                             locals_: dict[str, TpyType], analyzer) -> bool:
    """A bare-name F1-record arg into a non-Own param slot of the SAME record
    or a PARENT of it (`const A&` / `A&`): no gen_call_arg lift fires for
    either pairing (`own is None`, no protocol / Optional / union / covariant
    arm -- an upcast is C++'s implicit derived-to-base reference binding), so
    both paths render the bare name -- or `(*p)` for an F2 pointer-local, the
    gen_expr_deref indirect render `_lower_call_arg` retags. A narrowed
    subject's read arrives with `locals_` retyped to the member record and
    renames to its `T&` extraction alias at lowering (bare on both paths).
    `self` is rejected -- it renders `(*this)`. An `Own[record]` slot
    auto-moves at last use and a readonly-wrapped slot is the deep-const
    frontier -> AST. A record RVALUE (`take_rec(A(7))`) is not a
    name: the AST hoists it into a `__tmp_N` (free calls, typed at the CHILD
    for an upcast) or inlines it (method calls -- the const-slot ctor row
    rides `_method_ctor_rvalue_arg`; the mutated-ref-param shape is the
    miscompile in BUGS.md) -- rvalues stay off this arm."""
    if not isinstance(a, TpyName) or a.name == "self" or a.name not in locals_:
        return False
    at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
    if isinstance(at, OwnType):
        # An Own[record] PARAM binding is by-value storage the arg reads
        # bare, like any record name (the slot below is still non-Own).
        at = unwrap_readonly(at.wrapped)
    if _optional_ptr_borrow(at, analyzer) is not None:
        # A pointer-repr Optional borrow name proven non-None (sema retyped
        # the read; an unproven pass to a record slot is a sema type error):
        # the `(*p)` deref retag at lowering, gen_expr_deref's indirect render.
        at = analyzer.get_expr_type(a)
        at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
              if at is not None else None)
        if isinstance(at, OptionalType):
            return False  # not narrowed -- defensive, sema rejects upstream
    if not (isinstance(at, NominalType) and _f1_record(at, analyzer)):
        return False
    if ptype is None or not isinstance(ptype, TpyType):
        return False
    pt = unwrap_ref_type(unwrap_send_sync(ptype))
    if isinstance(pt, (ReadonlyType, OwnType)):
        return False
    return ((pt == at or analyzer.registry.is_subclass_of(at, pt))
            and _expr_eligible(a, locals_, analyzer))

def _scalar_ctor_call_eligible(e: TpyCall, locals_: dict[str, TpyType],
                               analyzer) -> bool:
    """A builtin scalar type-constructor call -- `Int32(0)` / `UInt32(x)` /
    `Int64(a + b)` / `Float64(1.5)` / `bool(n)` -- resolved by sema to a
    `@cpp_template` `__init__` overload and emitted via `_gen_call`'s
    `fi.cpp_template and not call_type` branch (-> `gen_template_or_native_call`
    -> `gen_call_from_fi`'s template arm). Sema already substituted `{cpp}` /
    class type params into the stored template (`_resolve_cpp_template_type_
    params`), and `_check_cast_safe`'s `static_cast` rewrite lands on the same
    node fact, so the emit is a pure `expand_cpp_template(template, None,
    *args)` -- the gate requires the template be positional-only to keep any
    still-unsubstituted shape on the AST path.

    Admitted: an eligible-scalar result (fixed-int / bool / double) and
    eligible-scalar args in scalar / `Own[scalar]` / method-type-param slots
    (the bare `gen_call_arg` pass-through). str / bytes / BigInt / Float32 /
    Char conversions fail the scalar checks (a `@native(function=True)` ctor
    like Char has no cpp_template at all); `int(...)` produces BigInt; enum
    ctors carry `enum_from_value`; borrowing-view ctors (StrView/Span) set
    `call_type` -- all stay on the AST path."""
    fi = _template_init_call_fi(e)
    if fi is None:
        return False
    if not _eligible_scalar(analyzer.get_expr_type(e)):
        return False
    return all(_ctor_arg_slot_ok(p.type, analyzer)
               and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
               and _expr_eligible(a, locals_, analyzer)
               for a, p in zip(e.args, fi.params))

def _instantiation_call_eligible(e: TpyCall, locals_: dict[str, TpyType],
                                 analyzer) -> bool:
    """A generic-type INSTANTIATION call (`list(it)` / `set(xs)` -- the
    `call_type` branch's resolved-template arm) at a storage sink: the
    sema-substituted positional-only ctor @cpp_template
    (`_instantiation_call_fi`) over per-slot args, result a storage
    container (`_storage_call_ret`'s container families -- the same
    families the decl/return sinks admit). Admitted args: a `range(...)`
    call (the substituted Range-template render, the comprehension
    begin/end iterable's shape) or a bare NON-LAST-USE container name (the
    bare gen_call_arg pass-through; a last use may take the
    consuming-`__iter__` / move renders, so it stays on the AST path --
    conservatively keyed on last-use alone, movability unbound here)."""
    fi = _instantiation_call_fi(e)
    if fi is None:
        return False
    fam = _storage_call_ret(analyzer.get_expr_type(e), analyzer)
    if fam is None or not _storage_call_container(fam):
        return False
    for a in e.args:
        if _is_range_call(a):
            rfi = a.resolved_function_info
            if (len(a.args) not in (1, 2, 3) or rfi is None
                    or not rfi.cpp_template
                    or not _eligible_scalar(_range_counter_type(a, analyzer))):
                return note_detail("call.inst_range_shape")
            if not all(_expr_eligible(ra, locals_, analyzer)
                       for ra in a.args):
                return False
            continue
        # A bare container name renders bare on both paths (no gen_call_arg
        # lift fires for the protocol slot); container locals in an eligible
        # body are single-assignment value slots (a reassigned container
        # local is an AST pointer-local and already rejected its body), and
        # container names are never narrowed or pointer-locals.
        if (not isinstance(a, TpyName) or a.name == "self"
                or a.name not in locals_):
            return note_detail("call.inst_arg_shape")
        at = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[a.name])))
        if not (is_list(at) or is_dict(at) or is_set(at)):
            return note_detail("call.inst_arg_shape")
        if id(a) in analyzer.ctx.all_last_uses:
            return note_detail("call.inst_arg_lastuse")
    return True

def _slice_ctor_call_eligible(e: TpyCall, locals_: dict[str, TpyType],
                              analyzer) -> bool:
    """A slice-object constructor call -- `basic_slice(1, 3)` / `slice(a, b, c)`
    -- the same pure-template-expansion shape as the scalar ctors
    (`::tpy::BasicSlice{{{0}, {1}}}` / `::tpy::Slice{{{0}, {1}, {2}}}`). The
    stub params are `Int32 | None` (value-repr Optional) slots, into which
    gen_call_arg passes every admitted arg bare: an in-range int literal / a
    fixed-int name renders itself, a `None` literal renders `std::nullopt` (the
    value-repr Optional render, THIRLiteral's STORAGE-form None). Coerced
    (widening / BigInt) bound sources fail `_expr_eligible`'s coerce gate and
    stay on the AST path."""
    fi = _template_init_call_fi(e)
    if fi is None:
        return False
    if not _slice_object_type(analyzer.get_expr_type(e)):
        return False
    return all(isinstance(a, TpyNoneLiteral)
               or (_resolved_scalar(analyzer.get_expr_type(a), analyzer)
                   and _expr_eligible(a, locals_, analyzer))
               for a in e.args)

def _method_receiver_type(recv: TpyExpr, locals_: dict[str, TpyType],
                          analyzer) -> 'TpyType | None':
    """The method receiver's binding type. A bare name reads the declared
    binding (`locals_`, the pre-resolution container type -- mirrors
    `_method_call_eligible`'s docstring note); a field-access receiver reads
    its sema-resolved type."""
    if isinstance(recv, TpyName):
        return locals_.get(recv.name)
    return analyzer.get_expr_type(recv)

def _method_field_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                              analyzer) -> bool:
    """A one-level field-access method receiver `x.field.method(...)`: the field
    is a plain value F1-record off an F1-record receiver name (self / a record
    param / REF_ALIAS / F2 pointer-local, or a proven Optional-ptr borrow name
    -- `_field_receiver_ok`'s admitted set). The field's record type routes the
    call to the user-record arm, and the receiver renders bare as its own
    THIRFieldAccess (`this->field.m()` / `p->field.m()`, the `.`/`->` decided by
    that inner node) -- the outer method access is `.` (is_arrow keys on a NAME
    receiver). Container / Optional / non-value field receivers are deferred: an
    Optional field would need the outer `(*obj)` / deref_check unwrap."""
    if not _field_receiver_ok(recv, locals_, analyzer):
        return False
    ft = analyzer.get_expr_type(recv)
    # A scalar-read container field routes the container arm: the receiver
    # renders bare as its own THIRFieldAccess (`this->buf` / `this->m`), exactly
    # as a bare-name container receiver renders `xs` -- the append / pop / update
    # emit inserts that receiver identically. `_method_receiver_type` reads the
    # same resolved field type downstream (no PendingListType round-trip that the
    # name arm dodges via `locals_`). Non-scalar element containers and sets fall
    # through to the record check (and reject there).
    if _container_scalar_read(ft, analyzer):
        return _witness("method.recv.container_field")
    ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ft)))
    if isinstance(ft, OwnType):
        ft = unwrap_readonly(ft.wrapped)
    return (isinstance(ft, NominalType) and _f1_record(ft, analyzer)
            and _witness("method.recv.record_field"))

def _method_nonname_receiver_ok(recv: TpyExpr, locals_: dict[str, TpyType],
                                analyzer) -> bool:
    """A non-name method receiver `<recv>.method(...)`. Two shapes admit:
    a one-level field access (`_method_field_receiver_ok`), and a
    container-element-record subscript `xs[i].m()` -- the subscript is a
    plain-record borrow lvalue (`::tpy::__getitem__(xs, i)`, `.` access,
    the receiver renders as its own THIRSubscript exactly as an `ps[i].field`
    read does). `_method_receiver_type` reads the resolved element record type
    downstream, so the outer call routes the user-record arm. Deeper subscript
    chains and non-record elements reject at `_container_record_elem_subscript`."""
    if isinstance(recv, TpySubscript):
        return (_container_record_elem_subscript(recv, locals_, analyzer)
                and _witness("method.recv.subscript"))
    if isinstance(recv, TpyMethodCall):
        return _method_call_receiver_ok(recv, locals_, analyzer)
    return _method_field_receiver_ok(recv, locals_, analyzer)

def _method_call_receiver_ok(recv: TpyMethodCall, locals_: dict[str, TpyType],
                             analyzer) -> bool:
    """A method-call method receiver `a.b().c()`: the inner call `a.b()` yields
    a plain non-pointer, non-Optional F1-record borrow (`Box.get()` -> `T&`),
    so the outer access renders `.` on both paths -- AST's `use_arrow` stays
    False (the receiver is not a name / pointer / Optional-ptr / own-dyn /
    borrow-`T*` tuple element), and the THIR outer node keeps `is_arrow` False
    (keyed on a NAME receiver). The inner call renders via the shared method
    lowering (`_lower_expr`), byte-identical to the AST's `gen_expr(recv)`.
    A pointer / Optional / non-record inner result reads `->` or the `(*obj)`
    unwrap on the AST path and is deferred."""
    rt = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        analyzer.get_expr_type(recv))))
    if isinstance(rt, OwnType):
        rt = unwrap_readonly(rt.wrapped)
    if not (isinstance(rt, NominalType) and _f1_record(rt, analyzer)):
        return False
    return _witness("method.recv.method")

def _recv_shape_reject(recv: TpyExpr, locals_: dict[str, TpyType],
                       analyzer) -> str:
    """Drilldown label for a non-admitted method receiver -- names *which*
    receiver shape blocks so the fallback tally ranks the follow-on cells
    (the method.receiver_shape total is first-reject-masked: one-level
    value-record fields, the admitted shape, are the rare part; the mass is
    non-F1-record fields and receiver chains)."""
    if isinstance(recv, TpySubscript):
        return "method.recv.subscript"
    if isinstance(recv, TpyCall):
        return "method.recv.call"
    if isinstance(recv, TpyMethodCall):
        return "method.recv.method"
    if isinstance(recv, TpyFieldAccess):
        if not isinstance(recv.obj, TpyName):
            return "method.recv.field_chain"
        # A one-level field whose parent receiver is not itself an admitted
        # F1-record binding (a container elem, a non-slice local, ...).
        if not _field_receiver_ok(recv, locals_, analyzer):
            return "method.recv.field_parent"
        ft = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
            analyzer.get_expr_type(recv))))
        if isinstance(ft, OwnType):
            ft = unwrap_readonly(ft.wrapped)
        if isinstance(ft, OptionalType):
            return "method.recv.field_optional"
        if isinstance(ft, NominalType):
            return "method.recv.field_nonf1"  # non-F1 / native / generic record
        return "method.recv.field_nonrecord"  # container / str / tuple / ...
    return "method.recv.other"

def _marker_reject(e: TpyMethodCall, analyzer) -> str:
    """Drilldown label for a marker-rejected method call -- names WHICH
    special-emit marker fires (each takes a different _gen_method_call arm)
    so the fallback tally ranks the marker mass by arm instead of one
    opaque method.marker bucket. Module-qualified calls sub-split by callee
    kind (they reduce to the free-callee emit family)."""
    if e.kwargs or e.double_star_unpack is not None:
        return "method.marker.kwargs"
    if e.typed_dict_get_field is not None:
        return "method.marker.typed_dict"
    if e.is_nested_constructor or e.is_nested_enum_constructor:
        return "method.marker.nested_ctor"
    if e.is_callable_field:
        return "method.marker.callable_field"
    if e.macro_expansion is not None:
        return "method.marker.macro"
    if e.fstr_expansion is not None:
        return "method.marker.fstr"
    if e.super_parent_type is not None or e.unbound_self_parent_type is not None:
        return "method.marker.super"
    if e.user_module_call is not None or e.builtin_module_call is not None:
        base = ("method.marker.module_static" if e.is_static_call
                else "method.marker.module" if e.user_module_call is not None
                else "method.marker.builtin_module")
        fi = e.resolved_function_info
        if fi is None:
            return base + ".unresolved"
        if fi.is_method and fi.name == "__init__":
            return base + ".ctor"
        if e.type_args or e.inferred_type_args or fi.type_params:
            return base + ".generic"
        if fi.cpp_template:
            return base + ".template"
        if fi.native_function or fi.native_name or fi.is_native_import or fi.is_extern_c:
            return base + ".native"
        return base + ".plain"
    if e.is_static_call:
        fi = e.resolved_function_info
        if fi is None:
            return "method.marker.static.unresolved"
        if e.type_args or e.inferred_type_args or fi.type_params:
            return _static_generic_reject(e, fi, analyzer)
        if fi.cpp_template:
            return "method.marker.static.template"
        if fi.native_function or fi.native_name:
            return "method.marker.static.native"
        return "method.marker.static.plain"
    if e.type_args or e.inferred_type_args:
        return "method.marker.type_args"
    if e.deref_depth or e.deref_narrowed_to is not None:
        return _deref_marker_reject(e, analyzer)
    return "method.marker.other"

def _static_generic_reject(e: TpyMethodCall, fi, analyzer) -> str:
    """Sub-split of a generic static call by what its AST spelling needs:
    the cpp_template expansion (gen_call_from_fi), NO explicit type args
    (the `if not expr.inferred_type_args` arm -- static_method_callee_cpp,
    the same spelling the plain slice already mirrors), or explicit `<T>`
    renders at the class / method level (type_to_cpp respectively
    _render_method_type_arg -- the generics frontier). `_dep` marks type
    args still containing a TypeParamRef (the dependent `template `
    keyword decision rides them)."""
    base = "method.marker.static.generic"
    if fi.cpp_template:
        # Positional-only templates are admitted upstream (_marker_call_kind),
        # so this tag names the residue: a surviving {T}/{cpp} placeholder
        # needing the substitution machinery.
        return base + ".template_typed"
    targs = e.type_args or e.inferred_type_args
    if not targs:
        return base + ".no_targs"
    rec = (analyzer.registry.get_record(e.obj.name)
           if isinstance(e.obj, TpyName) else None)
    n_class = len(rec.type_params) if rec is not None and rec.type_params else 0
    kind = (".both_targs" if targs[:n_class] and targs[n_class:]
            else ".class_targs" if targs[:n_class] else ".method_targs")
    dep = "_dep" if any(contains_type_param(t) for t in targs) else ""
    return base + kind + dep

def _deref_marker_reject(e: TpyMethodCall, analyzer) -> str:
    """Sub-split of a Deref-chain method call by its _gen_method_call arm:
    the narrowed-payload cast (_gen_deref_view_method_call), the Ptr[T]
    receiver arm (`p->m` / `::tpy::deref_check(p).m` -- no `.__deref__()`
    spelling), the builtin arm (cpp_template / native fi through
    gen_method_from_function_info), or the plain member tail
    (`recv.__deref__()...m(args)` over the _args() loop) -- split by
    receiver shape (bare name vs field/chain)."""
    if e.deref_narrowed_to is not None:
        return "method.marker.deref.narrowed"
    fi = e.resolved_function_info
    if fi is None:
        return "method.marker.deref.unresolved"
    if fi.cpp_template is not None or fi.native_function:
        return "method.marker.deref.builtin"
    recv_t = analyzer.get_expr_type(e.obj)
    if recv_t is not None:
        recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(recv_t)))
    if recv_t is not None and recv_t.is_pointer():
        return "method.marker.deref.ptr"
    if not isinstance(e.obj, TpyName):
        return "method.marker.deref.recv_shape"
    return "method.marker.deref.plain"

def _marker_call_kind(e: TpyMethodCall, analyzer) -> 'tuple[str, str] | None':
    """Classify a marker-carrying method call whose emit is RECEIVER-LESS --
    module-qualified (`m.f(x)`) or same-module static (`Rec.m(x)`) -- into
    its THIRCall emit kind + pre-rendered payload, the ONE routing fact
    shared by gate and lowering (the `_free_callee_kind` analog for
    `_gen_method_call`'s marker arms): ("qualified", callee_cpp) the
    `<spelling>(args)` render whose args are `_args()`'s full first-pass
    loop (plain cross-module calls, via `module_qualified_callee_cpp`, and
    plain static methods, via `static_method_callee_cpp` -- probe-verified
    to interpolate the same loop: the Own move cascade fires); ("native",
    symbol) the `::symbol(args)` render for a bare-@native cross-module
    callee -- the same loop but with `inline_template` set (`_is_native_
    stub`), which skips the Own copy-temp, so Own-slot args are rejected by
    the caller; ("template", tmpl) a same-module static `@cpp_template`
    call (`UInt32.trunc(i)`) whose positional-only template expands over
    gen_template_or_native_call's builtins arg loop -- generic statics
    included, since the no-{T} template makes gen_call_from_fi's type-arg
    substitution a no-op (the `_template_init_call_fi` rule). None = an
    emit arm the slice does not reproduce (super / typed-dict / macro /
    deref markers, module statics, `<T>`-spelled generics, ctors,
    extern-C / @native_c raw symbols, `function=True` natives whose args
    render slot-BLIND via gen_expr_deref, non-static cpp_template and
    builtin-module arms)."""
    if e.kwargs or e.double_star_unpack is not None:
        return None
    # Every OTHER special marker takes its own _gen_method_call arm.
    # EXPLICIT type args are rejected here; INFERRED ones flow to the
    # per-branch generic decisions below.
    if (e.super_parent_type is not None
            or e.unbound_self_parent_type is not None
            or e.typed_dict_get_field is not None
            or e.is_nested_constructor or e.is_nested_enum_constructor
            or e.is_callable_field or e.macro_expansion is not None
            or e.fstr_expansion is not None or e.type_args
            or e.deref_depth
            or e.deref_narrowed_to is not None
            or e.needs_optional_runtime_check):
        return None
    fi = e.resolved_function_info
    if fi is None:
        return None
    # A module-qualified record ctor (`m.Rec(...)`) resolves to __init__ --
    # the record-ctor frontier, not this arm.
    if fi.is_method and fi.name == "__init__":
        return None
    # Bespoke sema/emit arms keyed on the resolved function: the four
    # @builtin_function specials, special-handling builtins, and the
    # asyncio spawn pair (sema rewrote the args).
    if (fi.qualified_name in _SPECIAL_BUILTIN_QNAMES or fi.special_handling
            or fi.qualified_name in (qnames.ASYNCIO_RUN,
                                     qnames.ASYNCIO_CREATE_TASK)):
        return None
    if (fi.is_consuming or fi.error_return_type is not None
            or fi.native_cpp_return_type is not None
            or fi.is_async or fi.is_generator
            or fi.is_property_getter or fi.is_property_setter
            or any(isinstance(p.type, LiteralType) for p in fi.params)):
        return None
    if e.is_static_call:
        if e.builtin_module_call is not None:
            return None
        if e.user_module_call is not None:
            # The module-qualified static arm (`m.Cls.m(args)`): only its
            # GENERIC form is mirrored (`::tpyapp::m::Cls<CA>::template
            # m<MA>(args)`, composed at lowering); a template fi expands
            # through the shared substitution; the PLAIN form is a separate
            # pre-existing exclusion (method.marker.module_static.plain).
            if not isinstance(e.obj, TpyFieldAccess):
                return None
            if (not e.inferred_type_args
                    or getattr(e, "representational_subst_params", None)):
                return None
            if fi.cpp_template is not None:
                tmpl = expand_fi_template(fi, e.inferred_type_args)
                if _positional_only_template(tmpl, len(e.args)):
                    return ("template", tmpl)
                return None
            if (fi.native_function
                    or fi.linkage != FunctionLinkage.DEFAULT):
                return None
            return ("generic_module_static", "")
        if not isinstance(e.obj, TpyName):
            return None
        # A @cpp_template static takes the builtins arm (_gen_method_call's
        # native/template block precedes its `Class::m` arm):
        # gen_template_or_native_call expands the template over
        # gen_call_arg(inline_template) args; a GENERIC static template
        # (`Poll.ready[T]`-style) substitutes its {T} placeholders through
        # the shared expand_fi_template first. Positional-only results only.
        if fi.cpp_template is not None:
            tmpl = (expand_fi_template(fi, e.inferred_type_args)
                    if e.inferred_type_args else fi.cpp_template)
            if _positional_only_template(tmpl, len(e.args)):
                return ("template", tmpl)
            return None
        if e.inferred_type_args or fi.type_params:
            # A generic static call spells the class/method targs split
            # (`Cls<CA>::template m<MA>(args)`); the renders need the
            # resolver, so lowering composes the spelling
            # (_lower_generic_static_callee). A NATIVE record's static
            # render is targ-blind (`cpp_class::method(args)`) and rides
            # the plain qualified kind.
            if (not e.inferred_type_args
                    or getattr(e, "representational_subst_params", None)):
                return None
            ri = analyzer.registry.get_record(e.obj.name)
            if ri is not None and ri.is_native:
                cpp_method = (fi.native_name if fi.native_name
                              else escape_cpp_name(e.method))
                return ("qualified", f"{ri.native_name}::{cpp_method}")
            if fi.native_function or fi.native_name:
                return None
            if fi.linkage != FunctionLinkage.DEFAULT:
                return None
            return ("generic_static", "")
        # A native static fi takes the receiver-threaded builtin
        # arm (gen_method_from_function_info) -- not the `Class::m` render.
        if fi.native_function or fi.native_name:
            return None
        if fi.linkage != FunctionLinkage.DEFAULT:
            return None
        compiler = get_current_compiler()
        implicit = (compiler._implicit_stdlib_set() if compiler is not None
                    else set())
        return ("qualified", static_method_callee_cpp(
            analyzer.registry, implicit, analyzer.ctx.module_name,
            e.obj.name, e.method, fi))
    if fi.cpp_template is not None:
        return None
    if e.builtin_module_call is not None:
        return None
    if e.user_module_call is None:
        return None
    if fi.is_native:
        # Bare-@native cross-module callee (`m.sqrt(x)` -> `::std::sqrt(x)`).
        # `function=True` natives take the slot-blind gen_expr_deref arg
        # render instead (_skip_first_pass) -- a different loop, stays AST.
        # Targ-blind like the free-call native arm: the AST never spells
        # explicit template args for a native import.
        if fi.native_function:
            return None
        return ("native", fi.native_name or fi.name)
    if (fi.is_extern_c or fi.is_native_c or fi.native_function
            or fi.native_name):
        return None
    if fi.linkage != FunctionLinkage.DEFAULT:
        return None
    cpp = module_qualified_callee_cpp(
        analyzer.registry, analyzer.ctx.module_attributes,
        analyzer.ctx.module_name, e.user_module_call, e.method, fi)
    if e.inferred_type_args or fi.type_params:
        # A module-qualified generic call spells explicit template args
        # over the SAME qualified callee (`::tpyapp::m::gf<int32_t>(args)`,
        # targs = type_to_cpp(unwrap_ref_type) per inferred arg -- NOT the
        # free-call arm's to_cpp_stored); the args are the same qualified
        # first-pass loop over the RESOLVED (substituted) fi params.
        if (not e.inferred_type_args
                or getattr(e, "representational_subst_params", None)):
            return None
        return ("generic_qualified", cpp)
    return ("qualified", cpp)

def _marker_call_eligible(e: TpyMethodCall, kind: 'tuple[str, str]',
                          locals_: dict[str, TpyType], analyzer,
                          *, stmt_position: bool = False,
                          temps_ok: bool = False,
                          narrowed: 'set[str] | frozenset[str]' = frozenset()) -> bool:
    """Result/arg checks for a `_marker_call_kind`-classified receiver-less
    call. Mirrors `_call_eligible`'s value-position result set and its arg
    rows MINUS the free-loop-only ref-temp hoist (`_record_rvalue_temp_arg`:
    the method-call loop's hoist condition is protocol/TypeParamRef only, so
    a record rvalue into a concrete ref slot renders differently) -- and,
    for the "native" kind, minus the Own rows (`inline_template` skips the
    copy-temp for a non-last-use lvalue). Union lifts are admitted dcbp-BLIND
    on BOTH sides: the method-call loop calls `_gen_union_arg(arg, ptype)`
    without the deep-const verdict, and lowering passes readonly_target=False
    to match."""
    fi = e.resolved_function_info
    if len(e.args) != len(fi.params):
        return note_detail("method.qualcall.arity_defaults")
    ret = analyzer.get_expr_type(e)
    if not (_eligible_scalar(ret) or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            or _eligible_ptr_value(ret, analyzer)
            or (stmt_position and (ret is None or is_void_like_type(ret)))):
        return note_detail(_qualcall_ret_reject(ret, analyzer))
    if kind[0] == "template":
        # The builtins arg loop (gen_template_or_native_call) calls
        # gen_call_arg directly with inline_template set -- none of the
        # plain loop's pre-arms run and the Own copy-temp is skipped, so
        # admit only the shared kwarg-independent rows (the free-call
        # template branch's rule).
        for a, p in zip(e.args, fi.params):
            if not _shared_pass_through_arg(a, p.type, locals_, analyzer):
                return note_detail(_native_arg_reject(a, p.type, analyzer))
        return True
    # The generic-qualified/static kinds ride the SAME qualified first-pass
    # loop (the resolved fi's params are already substituted), so they share
    # the qualified rows including the Own cascade.
    own_ok = kind[0] in ("qualified", "generic_qualified", "generic_static",
                         "generic_module_static")
    return all(
        _shared_pass_through_arg(a, p.type, locals_, analyzer)
        or (temps_ok and _value_union_temp_arg(a, p.type, locals_,
                                               narrowed, analyzer))
        or (own_ok and _own_move_arg(a, p.type, locals_, analyzer))
        or (own_ok and temps_ok and _own_lvalue_arg(a, p.type, locals_,
                                                    narrowed, analyzer))
        or _optional_ptr_arg(a, p.type, locals_, analyzer, temps_ok=temps_ok)
        or _readonly_record_ctor_arg(a, p.type, locals_, analyzer)
        or _union_pass_through_arg(a, p.type, locals_, analyzer)
        or _union_member_lift_arg(a, p.type, locals_, analyzer)
        or _union_coerced_literal_arg(a, p.type, locals_, analyzer)
        or (own_ok and _own_union_ctor_arg(a, p.type, locals_, analyzer))
        or note_detail(_qualcall_arg_reject(a, p.type, analyzer))
        for a, p in zip(e.args, fi.params))

def _qualcall_ret_reject(ret: 'TpyType | None', analyzer) -> str:
    """Drilldown label for a marker-call result outside the value set --
    names the blocking result FAMILY so the qualcall ret mass ranks by the
    frontier it waits on (record / container / optional / union / tuple)."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ret)))
         if ret is not None else None)
    if isinstance(t, OwnType):
        t = unwrap_readonly(t.wrapped)
    if t is None or is_void_like_type(t):
        return "method.qualcall.ret.void_value_pos"
    if isinstance(t, OptionalType):
        return "method.qualcall.ret.optional"
    if isinstance(t, UnionType):
        return "method.qualcall.ret.union"
    if isinstance(t, TupleType):
        return "method.qualcall.ret.tuple"
    if isinstance(t, NominalType):
        if is_list(t) or is_dict(t) or is_set(t) or is_array(t):
            return "method.qualcall.ret.container"
        return ("method.qualcall.ret.record_f1"
                if _f1_record(t, analyzer)
                else "method.qualcall.ret.record")
    return "method.qualcall.ret.other"

def _qualcall_arg_reject(a: TpyExpr, ptype: 'TpyType | None', analyzer) -> str:
    """Drilldown label for a marker-call arg that fails every admitted row --
    names the param-slot family (and the arg's kind for plain slots), like
    `_native_arg_reject` for the native/template loop."""
    t = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ptype)))
         if ptype is not None else None)
    if isinstance(t, OwnType):
        return "method.qualcall.arg.own"
    if isinstance(t, OptionalType):
        return "method.qualcall.arg.optional"
    if isinstance(t, UnionType):
        return "method.qualcall.arg.union"
    if isinstance(a, (TpyCall, TpyMethodCall)):
        return "method.qualcall.arg.call_rvalue"
    at = analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if isinstance(at, NominalType) and at.is_user_record:
        return ("method.qualcall.arg.record_f1"
                if _f1_record(at, analyzer)
                else "method.qualcall.arg.record_nonf1")
    return f"method.qualcall.arg.other.{expr_kind_tag(a)}"

def _ptr_deref_method_call(e: TpyMethodCall, analyzer) -> bool:
    """A single-level Deref method call THROUGH a `Ptr[T]` receiver
    (`cell.release_strong()` on `cell: Ptr[Cell]`): _gen_method_call's
    pointer tail renders `p->m(args)` when sema proved the pointer non-null
    (`e.ptr_non_null`) and `::tpy::deref_check(p).m(args)` otherwise -- the
    existing THIRMethodCall is_arrow / deref_check renders; a pointer
    receiver never spells the `.__deref__()` chain. The ONE discriminator
    shared by gate and lowering; the gate adds receiver-shape and arg/result
    admission on top (the qualified-marker rows: the same `_args()`
    first-pass loop, Own cascade included). Rejected here: any other marker,
    a deeper chain (the pointer arm emits ONE `->` regardless of depth, so a
    `Ptr[Ptr[T]]` render is not reproduced), template/native/renamed fi
    (the builtins arm / `_is_native_stub` arg loop), generics, and non-Ptr
    wrapper receivers (Box/Rc -- the `.__deref__()` spelling)."""
    if e.deref_depth != 1 or e.deref_narrowed_to is not None:
        return False
    if e.kwargs or e.double_star_unpack is not None:
        return False
    if (e.is_static_call or e.super_parent_type is not None
            or e.unbound_self_parent_type is not None
            or e.user_module_call is not None
            or e.builtin_module_call is not None
            or e.typed_dict_get_field is not None
            or e.is_nested_constructor or e.is_nested_enum_constructor
            or e.is_callable_field or e.macro_expansion is not None
            or e.fstr_expansion is not None or e.type_args
            or e.inferred_type_args or e.needs_optional_runtime_check):
        return False
    fi = e.resolved_function_info
    if fi is None or not _plain_method_fi_ok(fi):
        return False
    if (fi.cpp_template is not None or fi.native_function or fi.native_name
            or fi.type_params or fi.linkage != FunctionLinkage.DEFAULT):
        return False
    rt = analyzer.get_expr_type(e.obj)
    return rt is not None and rt.is_pointer()

def _ptr_deref_recv_ok(e: TpyMethodCall, locals_: dict[str, TpyType],
                       analyzer) -> bool:
    """Gate-side receiver admission for `_ptr_deref_method_call`: a bare
    name declared as an eligible `Ptr[T]` value binding (renders raw, like
    the AST's gen_expr -- Ptr locals are never indirect or assign-narrowed),
    or an admitted field read whose value is such a Ptr (`self._cell.m()`
    -- the F1 field-read render; sema's interior unwrap already happened in
    get_expr_type)."""
    if isinstance(e.obj, TpyName):
        return (e.obj.name in locals_
                and _eligible_ptr_value(locals_[e.obj.name], analyzer))
    if isinstance(e.obj, TpyFieldAccess):
        return (_eligible_ptr_value(analyzer.get_expr_type(e.obj), analyzer)
                and _expr_eligible(e.obj, locals_, analyzer))
    return False

def _method_call_eligible(e: TpyMethodCall, locals_: dict[str, TpyType], analyzer,
                          *, stmt_position: bool = False,
                          temps_ok: bool = False,
                          record_ret_ok: bool = False,
                          storage_ret_ok: bool = False,
                          narrowed: 'set[str] | frozenset[str]' = frozenset(),
                          param_names: 'set[str] | frozenset[str]' = frozenset()) -> bool:
    """A method call on a bare-name builtin-container or user-record receiver
    whose emit is the pass-through subset of `_gen_method_call`. Container
    family: `xs.append(v)` -> `xs.push_back(v)` (@native member), `xs.pop()` ->
    `::tpy::pop_back(xs)` (@native free function), `xs.sort()` ->
    `std::stable_sort(...)` (@cpp_template); user-record family: the plain
    member call `a.combine(b)` (see `_record_method_call_eligible`). The
    receiver is an in-scope name; args are value scalars
    into scalar / `Own[scalar]` slots, str-slice values into non-Own str-family
    slots (`d.pop(k)` -- a dict's `readonly[K]` key slot renders the arg bare),
    or pass-through container names into
    non-Own container slots (`d.update(e)` -- see `_container_pass_through_arg`);
    the result is a value scalar or a str-slice value (or void, discarded, in
    statement position). Special-emit markers take different `_gen_method_call`
    paths: the receiver-less qualified/static/template arms are mirrored via
    `_marker_call_kind`, the Ptr-receiver Deref call via
    `_ptr_deref_method_call`; the rest (super / typed-dict / nested-ctor /
    callable-field / macro / fstr / wrapper `.__deref__()` chains / generics)
    are rejected."""
    # Nested enum value lookup `Outer.Kind(v)`: a method-call shape whose
    # receiver is the TYPE name, not a local -- checked before the
    # receiver-name pin.
    if e.is_nested_enum_constructor:
        return _nested_enum_from_value_eligible(e, locals_, analyzer)
    # Markers first: a module-qualified / static / super receiver is a bare name
    # not in `locals_`, so a receiver-shape check ahead of the marker check would
    # misattribute the whole module-call tail to `recv.name_absent`. Filter those
    # here so the receiver-shape drilldown counts only genuinely receiver-blocked
    # calls.
    if not _plain_member_call_markers_ok(e):
        if _ptr_deref_method_call(e, analyzer):
            # Result/arity/args mirror the qualified-marker rows exactly:
            # the same `_args()` first-pass loop interpolates (native stub
            # off, so the Own cascade fires -- own_ok).
            if not _ptr_deref_recv_ok(e, locals_, analyzer):
                # Split the reject: a pointer whose POINTEE is outside the
                # Ptr value family (e.g. a @dynamic-protocol cell -- rc.py's
                # `self._cell`) vs a receiver shape the slice doesn't carry.
                return note_detail(
                    "method.marker.deref.ptr_pointee"
                    if not _eligible_ptr_value(analyzer.get_expr_type(e.obj),
                                               analyzer)
                    else "method.marker.deref.ptr_recv")
            return _marker_call_eligible(e, ("qualified", ""), locals_,
                                         analyzer,
                                         stmt_position=stmt_position,
                                         temps_ok=temps_ok,
                                         narrowed=narrowed)
        kind = _marker_call_kind(e, analyzer)
        if kind is None:
            return note_detail(_marker_reject(e, analyzer))
        return _marker_call_eligible(e, kind, locals_, analyzer,
                                     stmt_position=stmt_position,
                                     temps_ok=temps_ok, narrowed=narrowed)
    if isinstance(e.obj, TpyName):
        if e.obj.name not in locals_:
            return note_detail("method.recv.name_absent")
    elif not _method_nonname_receiver_ok(e.obj, locals_, analyzer):
        return note_detail(_recv_shape_reject(e.obj, locals_, analyzer))
    # The Optional runtime-check marker is mirrored only for a pointer-repr
    # Optional borrow receiver (`::tpy::deref_check(p).method(args)`, the
    # THIRMethodCall deref_check face); the storage-form Optional receivers
    # take deref_optional_check / other arms -> AST path.
    if (e.needs_optional_runtime_check
            and _optional_ptr_borrow_name(e.obj, locals_, analyzer) is None):
        return False
    fi = e.resolved_function_info
    if fi is None or not _plain_method_fi_ok(fi):
        return note_detail("method.fi_kind")
    # Exact positional arity -- no omitted defaults, no varargs.
    if len(e.args) != len(fi.params):
        return note_detail("method.arity_defaults")
    # The declared binding type, not get_expr_type: a container-literal local's
    # use sites carry the pre-resolution PendingListType (the AST path unwraps it
    # in TypeResolver.get_resolved_type); the binding type is post-resolution.
    # Mirrors _is_len_call's locals_ lookup. A field-access receiver resolves to
    # an F1 value record (the container arm is name-only), so it falls through.
    recv_type = _method_receiver_type(e.obj, locals_, analyzer)
    if not (_container_scalar_read(recv_type, analyzer)
            or _container_record_elem(recv_type, analyzer)):
        # A str/StrView value-view receiver dispatches builtin @cpp_template /
        # @native(function=True) methods (`s.startswith(p)`, `s.find(x)`,
        # `s.encode()`) through the SAME general THIRMethodCall arm as a record
        # method -- the receiver renders bare, args pass like a free call's.
        if _resolved_str_value(recv_type, analyzer) is not None:
            return _view_method_call_eligible(e, fi, locals_, analyzer,
                                              stmt_position=stmt_position)
        return _record_method_call_eligible(e, fi, locals_, analyzer,
                                            stmt_position=stmt_position,
                                            temps_ok=temps_ok,
                                            record_ret_ok=record_ret_ok,
                                            storage_ret_ok=storage_ret_ok,
                                            narrowed=narrowed)
    # A `{cpp}` template placeholder substitutes the return type -- not
    # reproduced (the container family otherwise admits @native / @cpp_template
    # emits, its bread and butter).
    if fi.cpp_template is not None and "{cpp}" in fi.cpp_template:
        return note_detail("method.cpp_ret_substitution")
    # A void method's call carries no resolved expr type (None), unlike a void
    # free-function call (NoneType); both are discard-only, statement position.
    # An owned-str result (`xs.pop()` on list[str] -> `::tpy::pop_back(xs)`)
    # emits the same bare call and lands in the S1 str sinks (S5).
    ret = analyzer.get_expr_type(e)
    if not (_resolved_scalar(ret, analyzer)
            or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _eligible_ptr_value(ret, analyzer)
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            # Storage sinks only (the tuple-unpack source; mirrors
            # `_call_eligible`'s storage_ret_ok escape): the container/
            # tuple/union result lands bare in an `auto __tup_N = ...` bind.
            or (storage_ret_ok
                and _storage_call_ret(ret, analyzer) is not None)
            or (stmt_position and (ret is None or is_void_like_type(ret)))):
        return note_detail("method.ret_type")
    # A str arg into a non-Own str-family slot passes bare, like a free-call arg
    # (`d.pop(k)` -> `::tpy::dict_pop(d, k)`; builtin-container methods never
    # take the `_wants_str_literal_pin` path -- that pin is the free-call /
    # user-record-method arg paths only). The `Own[str]` element slot
    # (`xs.append(s)`) admits a literal (bare) and a view source -- a StrView
    # local OR a str param (both BORROW) -- via `_str_owned_slot_arg`, taking the
    # S1 `std::string(x)` copy; an owned STORAGE str local stays AST. A
    # record-element receiver (`recs.append(r)`) feeds its
    # `Own[record]` element slot the shared Own-slot arg cascade: a same-nominal
    # record NAME renders bare (inline_template -- push_back takes the lvalue),
    # a movable Own-param record name moves, a ctor rvalue binds bare.
    return all((_scalar_pass_through_slot(p.type, analyzer)
                and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
                and _expr_eligible(a, locals_, analyzer))
               or _str_pass_through_arg(a, p.type, locals_, analyzer)
               or _str_owned_slot_arg(a, p.type, locals_, param_names, analyzer)
               or _bytes_pass_through_arg(a, p.type, locals_, analyzer)
               or _char_pass_through_arg(a, p.type, locals_, analyzer)
               or _enum_pass_through_arg(a, p.type, locals_, analyzer)
               or _ptr_pass_through_arg(a, p.type, locals_, analyzer)
               or _container_pass_through_arg(a, p.type, locals_, analyzer)
               or _own_record_rvalue_arg(a, p.type, locals_, analyzer)
               or _own_move_arg(a, p.type, locals_, analyzer)
               or _own_lvalue_arg(a, p.type, locals_, narrowed, analyzer)
               or note_detail("method.arg_shape")
               for a, p in zip(e.args, fi.params))

def _record_method_call_eligible(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                                 analyzer, *, stmt_position: bool,
                                 temps_ok: bool = False,
                                 record_ret_ok: bool = False,
                                 storage_ret_ok: bool = False,
                                 narrowed: 'set[str] | frozenset[str]' = frozenset()) -> bool:
    """A plain user-record method call `recv.method(args)` -- the
    `_gen_method_call` user-record arm reduced to its pass-through subset. The
    shared marker / fi / arity rejects already ran in `_method_call_eligible`.

    Receiver: a bare in-scope F1-record name -- a record param, a REF_ALIAS /
    loop-var borrow, an F2 pointer-local (renders `p->method(args)`, the
    indirect-name arm; carried on `THIRMethodCall.is_arrow` at lowering), the
    method receiver itself (`self.helper()` -> `this->helper()`, the same
    indirect arm over the THIRSelf render), an
    isinstance-narrowed subject (`locals_` arrives retyped to the member; reads
    rename to the `T&` extraction alias), or a pointer-repr Optional[F1-record]
    borrow name (an `A | None` param / OPTIONAL_TO_PTR local): proven-non-None
    calls render the indirect arm (`p->method(args)`, is_arrow -- the name is
    in the lowering pointer set), unproven ones the runtime-check arm
    (`::tpy::deref_check(p).method(args)`, the caller's marker carve-out ->
    `THIRMethodCall.deref_check`).

    Method: a single-overload plain instance method, resolvable through the
    MRO (an inherited method emits identically for the admitted arg shapes --
    probe-verified; both AST arg paths reduce to the same gen_call_arg
    renders). Multi-overload sets are rejected wholesale: @auto_readonly
    clones, property pairs, and literal-specialized overloads all land there,
    and the AST arm builds its temp decisions from `overloads[0]` while
    rendering against the RESOLVED overload -- a pairing the slice does not
    reproduce. This also keeps `_wants_str_literal_pin` unreachable (the pin
    fires only at overload_count > 1). @native / @cpp_template facts cannot
    arise on a non-native record's methods but are rejected defensively --
    each takes a different `_gen_method_call` arm.

    Args: the free-call pass-through set minus bytes (a bytes-view result /
    arg form is not threaded through the method node) -- eligible scalars
    into NON-Own scalar slots (see `_plain_scalar_slot`), bare float
    literals, str-slice values, Char values, container names, F1-record
    names (`a.combine(b)`), record-ctor rvalues into CONST same-record
    slots (`a.combine(A(9))`, see `_method_ctor_rvalue_arg`), and the
    VALUE-union rows -- same-union names / coerced literals bare
    (`_method_value_union_arg`) and member-valued scalars through the
    `__tmp_N` variant temp under `temps_ok` (the free-call arg-temp row;
    value variants are const-blind, so the inherited-method first-pass AST
    loop, which omits `is_readonly_target`, renders identically). The
    mutated-ref-param rvalue shape (`a.absorb(A(4))`) is the AST miscompile
    tracked in BUGS.md and stays on the AST path.

    Result: an eligible scalar / Char / str-slice value, or void (None) in
    statement position, mirroring `_call_eligible`'s value-position set."""
    recv = e.obj  # a name or a one-level field access -- checked by the caller
    recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(
        _method_receiver_type(recv, locals_, analyzer))))
    if isinstance(recv_t, OwnType):
        recv_t = unwrap_readonly(recv_t.wrapped)
    # An Optional-ptr borrow receiver dispatches methods on the inner record
    # (proven -> arrow, unproven -> deref_check; both yield the inner).
    opt_recv = _optional_ptr_borrow(recv_t, analyzer)
    if opt_recv is not None:
        recv_t = unwrap_readonly(opt_recv.inner)
    if not (isinstance(recv_t, NominalType) and _f1_record(recv_t, analyzer)):
        # The drill's "which methods block" discriminant: name the receiver
        # family AND the method, so e.g. str methods rank individually.
        return note_detail(f"method.{_recv_family(recv_t, analyzer)}.{e.method}")
    if (fi.cpp_template is not None or fi.native_function or fi.native_name
            or fi.type_params or fi.is_staticmethod or not fi.is_method):
        return note_detail("method.fi_kind")
    ri = analyzer.registry.get_record_for_type(recv_t)
    if ri is None:
        return False
    overloads = analyzer.registry.get_method_overloads_with_parents(ri, e.method)
    if len(overloads) != 1:
        # An @auto_readonly / auto_own[Self] clone pair renders the same
        # plain `recv.method(args)` whichever member sema resolved -- C++
        # dispatches on receiver const-ness (or lvalue-ness), so the call
        # emit is member-blind. The mutable-clone flag is threaded onto its
        # FunctionInfo; an auto_own pair is the one-consuming-member shape
        # (its consuming DEF never routes, but calls on lvalue receivers
        # resolve to the borrowing member and render bare). Genuine stub
        # sets (incl. literal overloads, whose call sites MANGLE the
        # callee name) keep rejecting.
        is_clone_pair = (
            len(overloads) == 2
            and (any(fi2.is_auto_readonly_mutable_clone for fi2 in overloads)
                 or sum(1 for fi2 in overloads if fi2.is_consuming) == 1))
        if not is_clone_pair:
            return note_detail("method.overload_set")
    ret = analyzer.get_expr_type(e)
    # A TypeParamRef result (`self.get() -> T` in a generic body) emits the
    # same bare `recv.method(args)`; the POSITIONS it can compose into gate
    # their own family checks (a T decl/arg rejects there), so admitting it
    # here only opens the T-operand compares and their siblings.
    if not (_resolved_scalar(ret, analyzer) or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _eligible_ptr_value(ret, analyzer)
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            or _tparam_value(ret)
            # An F1-record rvalue return is admitted only at the owned-record
            # decl sink (`Rec r = b.build();`, record_ret_ok): the bare
            # `recv.method(args)` prvalue stored directly, the method sibling of
            # `_is_record_rvalue_source`'s by-value free-call face. `is_rvalue_source`
            # (checked at the decl gate) keeps a `T&` borrow return out.
            or (record_ret_ok and _f1_record(ret, analyzer))
            # Storage sinks only (the tuple-unpack source; the record-method
            # sibling of _call_eligible's storage_ret_ok escape).
            or (storage_ret_ok
                and _storage_call_ret(ret, analyzer) is not None)
            or (stmt_position and (ret is None or is_void_like_type(ret)))):
        return note_detail("method.ret_type")
    return all((_plain_scalar_slot(p.type, analyzer)
                and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
                and _expr_eligible(a, locals_, analyzer))
               or _float_literal_pass_through_arg(a, p.type, locals_, analyzer)
               or _int_literal_bigint_arg(a, p.type, locals_, analyzer)
               or _str_pass_through_arg(a, p.type, locals_, analyzer)
               or _bytes_pass_through_arg(a, p.type, locals_, analyzer)
               or _char_pass_through_arg(a, p.type, locals_, analyzer)
               or _enum_pass_through_arg(a, p.type, locals_, analyzer)
               or _ptr_pass_through_arg(a, p.type, locals_, analyzer)
               or _value_tuple_pass_through_arg(a, p.type, locals_, analyzer)
               or _slice_ctor_pass_through_arg(a, p.type, locals_, analyzer)
               or _own_scalar_rvalue_arg(a, p.type, locals_, analyzer)
               or _own_record_rvalue_arg(a, p.type, locals_, analyzer)
               # The temp-free half of the Own-slot cascade -- a movable
               # Own-param name at last use renders `std::move(name)` in any
               # position (the move half of `_lower_call_arg` runs outside
               # temp_args). The copy half (`_own_lvalue_arg`, `__tmp_N`) needs
               # a flush-position temp the method-arg lowering does not thread
               # yet -> that lvalue shape stays AST.
               or _own_move_arg(a, p.type, locals_, analyzer)
               # Pointer-repr Optional[record] slots, NON-ctor faces only
               # (none / bare-pass / `&(name)` / field lift): temps_ok=False
               # rejects the 'ctor' face, whose `&(__tmp_N)` addr-of temp needs
               # a flush the method-arg lowering does not thread (the same gap
               # that keeps the Own copy half AST).
               or _optional_ptr_arg(a, p.type, locals_, analyzer,
                                    temps_ok=False)
               or _container_pass_through_arg(a, p.type, locals_, analyzer)
               or _record_pass_through_arg(a, p.type, locals_, analyzer)
               or _method_ctor_rvalue_arg(a, p.type, i, overloads[0],
                                          locals_, analyzer)
               or _method_value_union_arg(a, p.type, locals_, analyzer)
               or (temps_ok and _value_union_temp_arg(a, p.type, locals_,
                                                      narrowed, analyzer))
               or note_detail("method.arg_shape")
               for i, (a, p) in enumerate(zip(e.args, fi.params)))

def _view_method_call_eligible(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                               analyzer, *, stmt_position: bool) -> bool:
    """A str/StrView value-view receiver's builtin method call -- the
    `_gen_method_call` builtin-method arm (`native_function or cpp_template`,
    line-3700 block) reduced to its pass-through subset. The shared marker /
    receiver-shape / fi-kind / arity rejects already ran in
    `_method_call_eligible`; the receiver is a bare str-slice name or str field
    (whichever passed that receiver-shape check).

    The AST renders the receiver via `_gen_builtin_method_receiver` (a bare
    `gen_expr` for a plain str name / field) and the call via
    `gen_method_from_function_info` -> `gen_call_from_fi`: a @cpp_template
    expands `{self}`/`{0}`.. positionally, a @native(function=True) prepends
    the receiver (`::sym(recv, args)`). Lowering reaches the SAME general
    THIRMethodCall arm (receiver + args + cpp_template/native_function_name),
    which mirrors those two spellings exactly -- so admission is a gate
    widening only, no new emit.

    Method fi: @cpp_template (positional-only, no `{cpp}` return substitution)
    or @native(function=True). A `{cpp}` placeholder substitutes the return
    type (`str_family` templates carry none, but reject defensively). Owned-str
    results (`s.upper()`) land bare in the str sinks like a container `pop()`;
    a bytes result (`s.encode()`) rides the view/owned form tag.

    Args: the free-call pass-through set (str-slice / scalar / char / bytes /
    enum / ptr into non-Own slots). A str method's args render identically
    under `inline_template=True` (AST) and `_lower_call_arg(method_arg=True)`
    (THIR) for these shapes -- both bare. Own slots / temp-hoisting arg shapes
    stay AST (`_str_pass_through_arg` rejects Own).

    Result: an eligible scalar / char / str-value / bytes-value / enum / ptr,
    or void (None) in statement position."""
    if not (fi.native_function or fi.cpp_template):
        return note_detail("method.view.fi_kind")
    # A `{cpp}` template placeholder substitutes the return-type spelling -- the
    # generics-frontier machinery, not reproduced here.
    if fi.cpp_template is not None and "{cpp}" in fi.cpp_template:
        return note_detail("method.view.cpp_ret_substitution")
    ret = analyzer.get_expr_type(e)
    if not (_resolved_scalar(ret, analyzer)
            or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _eligible_ptr_value(ret, analyzer)
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            or (stmt_position and (ret is None or is_void_like_type(ret)))):
        return note_detail("method.view.ret_type")
    return all((_scalar_pass_through_slot(p.type, analyzer)
                and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
                and _expr_eligible(a, locals_, analyzer))
               or _str_pass_through_arg(a, p.type, locals_, analyzer)
               or _bytes_pass_through_arg(a, p.type, locals_, analyzer)
               or _char_pass_through_arg(a, p.type, locals_, analyzer)
               or _enum_pass_through_arg(a, p.type, locals_, analyzer)
               or _ptr_pass_through_arg(a, p.type, locals_, analyzer)
               or note_detail("method.view.arg_shape")
               for a, p in zip(e.args, fi.params))

def _str_list_method_iterable_ok(e: TpyMethodCall, locals_: dict[str, TpyType],
                                 analyzer) -> bool:
    """A str-view method returning `Own[list[str]]` as a for-each iterable
    (`for w in s.split():`). The result is an rvalue -- the owning
    `auto __obj_N = ::tpy::str_split_whitespace(s);` capture (iterable_lvalue
    False, the same verdict the dict-view branch takes), iterated like any
    list[str] name; the str ELEMENT is checked by the caller's shared elem
    gate. Mirrors the marker / receiver / fi / arity / arg rejects of
    `_method_call_eligible` + `_view_method_call_eligible`, but swaps the
    value-result check for `is_list` (the str-list return the expr gate rejects
    at ret_type). The receiver is a bare str-slice name (the field-receiver and
    non-str-list shapes defer); bytes-receiver splits (`data.split(sep)` ->
    list[bytes]) fail the str-receiver pin and defer with the other
    reference-element rows."""
    if not _plain_member_call_markers_ok(e) or e.needs_optional_runtime_check:
        return False
    if not (isinstance(e.obj, TpyName) and e.obj.name in locals_):
        return False
    if _resolved_str_value(_method_receiver_type(e.obj, locals_, analyzer),
                           analyzer) is None:
        return False
    fi = e.resolved_function_info
    if fi is None or not _plain_method_fi_ok(fi):
        return False
    # The builtin-method arm (@native(function=True) / @cpp_template); a `{cpp}`
    # return substitution is the generics machinery, not mirrored here.
    if not (fi.native_function or fi.cpp_template):
        return False
    if fi.cpp_template is not None and "{cpp}" in fi.cpp_template:
        return False
    if len(e.args) != len(fi.params):
        return False
    # Own-stripped by get_expr_type; the str-list return the value-result expr
    # gate rejects. Only a list return (`split`/`rsplit`/`splitlines`) admits --
    # the caller's elem gate then pins the str element.
    ret = analyzer.get_expr_type(e)
    st = unwrap_readonly(unwrap_send_sync(ret)) if ret is not None else None
    if not is_list(st):
        return False
    return all((_scalar_pass_through_slot(p.type, analyzer)
                and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
                and _expr_eligible(a, locals_, analyzer))
               or _str_pass_through_arg(a, p.type, locals_, analyzer)
               or _bytes_pass_through_arg(a, p.type, locals_, analyzer)
               or _char_pass_through_arg(a, p.type, locals_, analyzer)
               or _enum_pass_through_arg(a, p.type, locals_, analyzer)
               or _ptr_pass_through_arg(a, p.type, locals_, analyzer)
               for a, p in zip(e.args, fi.params))

def _recv_family(t: 'TpyType | None', analyzer) -> str:
    """Coarse receiver-family label for the method-call reject detail."""
    if t is None:
        return "untyped"
    if is_str_type(t):
        return "str"
    if is_bytes_type(t):
        return "bytes"
    if is_list(t):
        return "list"
    if is_dict(t):
        return "dict"
    if is_set(t):
        return "set"
    if is_array(t):
        return "array"
    if isinstance(t, OptionalType):
        return "optional"
    if isinstance(t, UnionType):
        return "union"
    if isinstance(t, TupleType):
        return "tuple"
    if isinstance(t, NominalType):
        return "record"  # non-F1 record (or protocol/native nominal)
    return type(t).__name__.removesuffix("Type").lower()

def _method_value_union_arg(a: TpyExpr, ptype: TpyType | None,
                            locals_: dict[str, TpyType], analyzer) -> bool:
    """The temp-free VALUE-union method-arg rows: a same-union name / a
    union-coerced literal into a value-variant method slot renders bare on
    both paths. Restricted to value unions -- their renders are const-blind
    (no pointee const spelling), so the own-record AST loop (which threads
    `is_readonly_target`) and the inherited-method first-pass loop (which
    omits it) emit identically. Pointer-variant method slots stay AST with
    the other deep-const rows."""
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    if _eligible_value_union(unwrap_readonly(unwrap_send_sync(pt))) is None:
        return False
    return (_union_pass_through_arg(a, ptype, locals_, analyzer)
            or _union_coerced_literal_arg(a, ptype, locals_, analyzer))

def _method_ctor_rvalue_arg(a: TpyExpr, ptype: TpyType | None, idx: int,
                            method_fi, locals_: dict[str, TpyType],
                            analyzer) -> bool:
    """A record-ctor rvalue arg into a CONST same-record method slot
    (`a.combine(A(9))` where `other` is emitted `const A&`): the method-call
    arg loop has no ref-param rvalue-temp arm (unlike the free-fn loop), so
    `gen_call_arg` renders the ctor expansion inline -- the THIRCtorCall
    bytes. Admission requires the callee param be signature-const
    (`const_borrow_params`, the materialized `decide_param_const` verdict,
    the same fact `_param_is_const` reads for body locals): the MUTATED-ref
    shape (`a.absorb(A(4))`) inlines identically on the AST path but that
    render cannot compile (an rvalue never binds `A&`) -- the miscompile
    tracked in BUGS.md ("method-call record rvalue into a mutated ref param
    never temps"), kept gate-rejected rather than mirrored. Same-nominal
    slots only (an rvalue UPCAST is deferred with the other rvalue rows); a
    FREE-fn ctor rvalue hoists a `__tmp_N` even into a const slot (the
    ref-param temp arm) and stays off `_call_eligible`'s arms entirely."""
    if not isinstance(a, TpyCall):
        return False
    pt = ptype if isinstance(ptype, TpyType) else None
    if pt is None:
        return False
    pt = unwrap_ref_type(unwrap_send_sync(pt))
    if isinstance(pt, (ReadonlyType, OwnType)):
        return False
    if not (isinstance(pt, NominalType) and pt.is_user_record):
        return False
    if pt != analyzer.get_expr_type(a):
        return False
    cbp = method_fi.const_borrow_params
    if cbp is None or idx not in cbp:
        return False
    return _record_ctor_call_eligible(a, locals_, analyzer)

def _is_builtin_print(e: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    """`e` is a call to the builtin `print` (not a user/local shadow): the builtin
    is in `imported_names` and `print` is not redefined as a same-module function /
    record or bound as a local. A shadowed `print` conservatively stays on the AST
    path (never a divergence). This is stricter than the AST print path, which
    intercepts `print(...)` unconditionally regardless of a user shadow."""
    if not (isinstance(e, TpyCall) and isinstance(e.func, TpyName)
            and e.func_name == "print"):
        return False
    reg = analyzer.registry
    return ("print" in analyzer.imported_names
            and reg.get_function("print") is None
            and reg.get_record("print") is None
            and "print" not in declared)

def _print_arg_form(t: TpyType) -> PrintForm:
    """The `std::cout <<` wrapper for a print arg's resolved type -- mirrors the
    gen_print per-type dispatch for the eligible subset. bool is checked before
    the 8-bit-int case (a `bool` has an 8-bit int trait but must format as
    `True`/`False`, not `static_cast<int>`)."""
    if is_bool_type(t):
        return PrintForm.BOOL
    if is_float_type(t):
        # print_float takes double; a float32 arg casts up first
        # (gen_print's is_float32_type arm).
        return PrintForm.FLOAT32 if is_float32_type(t) else PrintForm.FLOAT
    # A bytes-slice value (incl. a still-pending bytes local binding -- the
    # view/owned resolution doesn't change the printer) wraps in BytesPrinter
    # (gen_print's is_any_bytes_type arm; bytearray is gated out of the args).
    if _is_bytes_family(t):
        return PrintForm.BYTES
    if is_enum_type(t):
        # @native enums have no emitted operator<< (it would conflict with a
        # user-provided one) -- gen_print routes them through `::tpy::__repr__`;
        # tpy-defined enums stream raw via their emitted operator<<.
        einfo = enum_info_of(t)
        if einfo is not None and einfo.is_native:
            _witness("enum.repr_print")
            return PrintForm.REPR
        return PrintForm.RAW
    tr = int_traits_of(t)
    if tr is not None and tr.bits == 8:
        return PrintForm.INT8
    return PrintForm.RAW

def _print_optval_opt(a: TpyExpr, analyzer) -> 'OptionalType | None':
    """`a` is a print arg whose RESOLVED type is a value-repr `Optional[scalar]`
    or `Optional[str]` -- an UN-narrowed read that gen_print renders via
    `::tpy::print_optional_val(...)` over the whole optional (bare, no deref). A
    narrowed read resolves to the inner scalar (not Optional), so it is excluded
    here and the deref-on-narrow `(*p)` face stays on its own gates. Limited to a
    bare name (param / local) or a plain field read -- the positions gen_print
    lowers via `_gen_expr` (bare optional storage). A container/tuple inner takes
    an explicit Formatter (a separate face) and is excluded: `_value_opt_scalar`/
    `_value_opt_str` only admit scalar / str inners."""
    if not isinstance(a, (TpyName, TpyFieldAccess)):
        return None
    if isinstance(a, TpyFieldAccess) and not _field_markers_clean(a):
        return None
    t = analyzer.get_expr_type(a)
    opt = _value_opt_scalar(t, analyzer)
    return opt if opt is not None else _value_opt_str(t, analyzer)

def _print_optval_form(opt: 'OptionalType') -> 'tuple[PrintForm, str | None]':
    """The `print_optional_val` wrapper for a value-repr Optional print arg,
    mirroring gen_print's value-repr Optional arm: `Optional[bool]` /
    `Optional[float]` take an explicit Formatter + inner-type template
    (`<::tpy::print_bool, T>` / `<::tpy::print_float, T>`, both float widths on
    the float branch), every other inner (int / Char / str) the plain form. The
    inner C++ spelling is the raw `opt.inner.to_cpp()` -- the bool/float inners
    are never a view family, so the AST's `_optional_print_inner_cpp` shim
    (view-storage override) collapses to `inner.to_cpp()` here."""
    inner = opt.inner
    if is_bool_type(inner):
        return PrintForm.OPT_VAL_BOOL, inner.to_cpp()
    if is_float_type(inner):
        return PrintForm.OPT_VAL_FLOAT, inner.to_cpp()
    return PrintForm.OPT_VAL, None

def _wrap_print_form(a: TpyExpr, declared: dict[str, TpyType],
                     analyzer) -> 'PrintForm | None':
    """The kind-keyed printer wrap for a container / value-tuple / F1-record
    NAME print arg, or None outside the slice -- gen_print's per-kind arms:
    `Dict/Set/ListPrinter` (Array shares ListPrinter), `TuplePrinter`, a
    record streaming raw via its emitted operator<<. The ONE routing fact
    shared by the gate and `_lower_print_arg`, so admission and form
    selection cannot drift. NAMES only (call/subscript/field sources ride
    `_expr_eligible`'s tail; the gate excludes pointer-locals); bytearray /
    Span / dict-view / varargs printers stay AST; `self` renders `(*this)`,
    not the bare name -- excluded."""
    if not isinstance(a, TpyName) or a.name == "self":
        return None
    ct = _subscript_container_recv_type(a, declared, analyzer)
    if ct is not None:
        ct = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(ct)))
        if is_dict(ct):
            return PrintForm.DICT
        if is_set(ct):
            return PrintForm.SET
        if is_list(ct) or is_array(ct):
            return PrintForm.LIST
        # A subscriptable non-container binding (tuple/...) falls through.
    t = declared.get(a.name)
    if t is None:
        return None
    if _value_tuple(t, analyzer) is not None:
        return PrintForm.TUPLE
    u = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(t)))
    if isinstance(u, NominalType) and _f1_record(u, analyzer):
        return PrintForm.RAW
    return None

def _print_arg_ok(a: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """One print arg in the no-kwargs common-arg subset: a str/bytes literal,
    an eligible scalar (fixed-int / bool / double), a Char (streamed raw --
    gen_print's direct-output arm; Char has no int_traits, so no int8 cast),
    or a str-slice value (a str/StrView name or str-returning call -- string
    and string_view stream raw, like the AST's is_any_str_type arm)."""
    if isinstance(a, (TpyStrLiteral, TpyBytesLiteral)):
        # A bytes literal prints owned (gen_print threads no target).
        return True
    if _print_optval_opt(a, analyzer) is not None:
        # An UN-narrowed value-repr Optional[scalar/str] read -> the bare
        # `::tpy::print_optional_val(...)` over the whole optional (witnessed at
        # lowering, where the wrapper render actually fires).
        return True
    if _value_opt_scalar_name(a, locals_, analyzer) is not None:
        # A NARROWED value-repr Optional[scalar] param read (declared Optional,
        # rt already the inner scalar) prints its deref-on-narrow `(*p)` -- a
        # separate face; the un-narrowed whole-optional read routed above.
        return note_detail("print.optval")
    if _value_opt_view_name(a, locals_, analyzer) is not None:
        # A value-repr Optional[str]/Optional[bytes] read in print position stays
        # deferred: a NARROWED read would print `(*s)`/`(*b)` (never routed), and
        # an UN-narrowed Optional[bytes] read is excluded from the str-only
        # `_print_optval_opt` route above, so it must not fall to the raw
        # BytesPrinter arm below. (Un-narrowed Optional[str] already routed.)
        return note_detail("print.optstr")
    at = analyzer.get_expr_type(a)
    return ((_resolved_scalar(at, analyzer) or _eligible_char(at)
             # A tpy-defined enum streams via its emitted operator<< (RAW);
             # an @native enum takes `::tpy::__repr__` (PrintForm.REPR).
             or _eligible_enum(at, analyzer) is not None
             or _resolved_str_value(at, analyzer) is not None
             or _resolved_bytes_value(at, analyzer) is not None  # BytesPrinter
             or _is_string_owned(at))  # a concat result / String local: raw <<
            and _expr_eligible(a, locals_, analyzer))

def _print_eligible(e: TpyCall, locals_: dict[str, TpyType], analyzer) -> bool:
    """A `print(<args>)` in the no-kwargs common-arg subset (see
    `_print_arg_ok`). Any `sep=`/`end=`/`file=`/`flush=` kwarg, `**`-unpack,
    f-string, or other non-scalar arg falls back to the AST path (gen_print's
    richer cases). The statement gate layers the comprehension-arg arm on top
    of this (it needs the walk state the expression gates don't carry)."""
    if e.kwargs or e.double_star_unpack is not None:
        return False
    return all(_print_arg_ok(a, locals_, analyzer) for a in e.args)

# Sentinel for an f-string arg type outside the mirrored wrapper rows.
_FSTRING_INELIGIBLE = object()

def _fstring_arg_wrap(a: TpyExpr, analyzer, conv: int,
                      has_spec: bool) -> 'str | None | object':
    """The Python-compatible formatting wrapper for one interpolated f-string
    arg, as a positional `{0}` template (None = pass through bare) -- the
    mirrored subset of `_gen_fstring`'s per-arg table -- or `_FSTRING_INELIGIBLE`
    for any row the slice does not reproduce (user/union `__str__`,
    containers, Any, type params). The mirrored-type row is established first:
    `!r` then overrides it with `repr_of` (the AST chain's conv row precedes
    every type row, and no mirrored type is a container, so `repr_of` fires
    for all of them); `!s` is a no-op outside the user-type row, which is not
    mirrored -- so an unmirrored type stays rejected under any conversion (its
    inner render is not pinned by the slice). A format spec flips the bool row
    to `static_cast<int>` and the float rows to bare (std::format handles the
    spec on double/float directly); the 8-bit-int and enum casts apply
    spec-or-not. bool is checked before the 8-bit-int row, mirroring the AST
    order (bool carries 8-bit int traits but must format as True/False). An
    IntLiteral-typed arg (`f"{5}"`) resolves through the module default int --
    fixed widths format bare like the AST's fall-through; a runtime BigInt
    takes the `.to_string()` row (a spec'd int/BigInt arg is a sema error, so
    the spec never reaches that row)."""
    row: 'str | None | object' = _FSTRING_INELIGIBLE
    if isinstance(a, TpyStrLiteral):
        row = None  # const char[N] formats directly
    else:
        t = analyzer.get_expr_type(a)
        if t is None:
            return _FSTRING_INELIGIBLE
        if (_resolved_str_value(t, analyzer) is not None
                or _is_string_owned(t)):
            row = None  # string/string_view/concat-result format directly
        elif is_bool_type(t):
            row = ("static_cast<int>({0})" if has_spec
                   else "::tpy::bool_to_str({0})")
        elif _eligible_char(t):
            _witness("fstr.char_arg")
            row = None  # char formats directly (no int_traits, so no cast)
        # A bare float literal (FloatLiteralType) resolves to float64 in an
        # f-string slot -- there is no Float32-typed context inside one -- so
        # it takes the same row as a concrete double. A concrete Float32 arg
        # casts up first (float_to_str takes double; _gen_fstring's float32
        # arm) -- unless a spec routes it bare into std::format.
        elif isinstance(t, FloatLiteralType) or is_float_type(t):
            if has_spec:
                row = None
            elif is_float32_type(t):
                row = "::tpy::float_to_str(static_cast<double>({0}))"
            else:
                row = "::tpy::float_to_str({0})"
        elif _eligible_enum(t, analyzer) is not None:
            row = "static_cast<int>({0})"
        else:
            rt = resolve_int_literals(t, analyzer.ctx.default_int_for_literal)
            if is_fixed_int_type(rt):
                tr = int_traits_of(rt)
                row = ("static_cast<int>({0})"
                       if tr is not None and tr.bits == 8 else None)
            elif is_big_int_type(rt):
                # A runtime BigInt (concrete, or an IntLiteral under a BigInt
                # module default) formats via `.to_string()` (the bigint row).
                row = "({0}).to_string()"
    if row is _FSTRING_INELIGIBLE:
        return _FSTRING_INELIGIBLE
    if conv == FSTRING_CONV_REPR:
        _witness("fstr.conv_repr")
        return "::tpy::repr_of({0})"
    if conv == FSTRING_CONV_STR:
        _witness("fstr.conv_str")
    return row

def _fstring_eligible(e: TpyFString, locals_: dict[str, TpyType],
                      analyzer) -> bool:
    """An f-string in the mirrored slice: literal segments plus interpolated
    args that are themselves eligible exprs with a mirrored wrapper row
    (conversion- and spec-aware; see `_fstring_arg_wrap`). FStr-macro
    f-strings never reach this gate: they only arise as args to `FStr`-typed
    params, which the call-arg slot pins reject."""
    for part in e.parts:
        if isinstance(part, str):
            continue
        # !a is a parse error today; the guard keeps a future conversion off
        # the mirrored placeholder table rather than silently mis-rendering.
        if part.conversion not in (FSTRING_CONV_NONE, FSTRING_CONV_STR,
                                   FSTRING_CONV_REPR):
            return note_detail("fstring.conversion")
        # An owned-str FIELD arg formats bare like a str name (the wrap
        # table's str row); the format sink is read-only, so no form seam
        # fires on the STORAGE member read.
        if not (_expr_eligible(part.expr, locals_, analyzer)
                or (_str_field_value_read(part.expr, locals_, analyzer)
                    and _witness("fstr.str_field"))):
            return False
        if _fstring_arg_wrap(part.expr, analyzer, part.conversion,
                             part.format_spec is not None) is _FSTRING_INELIGIBLE:
            return note_detail("fstring.arg_wrap")
    return True

def _expr_eligible(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    if isinstance(e, TpyName):
        # A name outside the local/param set is an unseeded global: a
        # non-value (pointer-slot) / Optional / native / cross-module global
        # whose read the slice does not yet materialize (value-family
        # same-module globals seed into scope at lower_function and never
        # reach this reject). Reject -> stays on the AST path.
        # A narrowing-divergent union read (declared union, member-typed read)
        # is a pre-existing AST miscompile -> AST path (see the helper).
        if e.name not in locals_:
            return note_detail("name.global_read")
        # A widened-ctor-param binding kind with no read arm (value-repr
        # Optional / Own over a non-routed payload) -- see the helper.
        unrouted = _unrouted_binding_read(locals_.get(e.name), analyzer)
        if unrouted is not None:
            return note_detail(unrouted)
        if _value_opt_scalar_name(e, locals_, analyzer) is not None and \
                isinstance(unwrap_readonly(analyzer.get_expr_type(e)),
                           OptionalType):
            # An UN-narrowed value-repr Optional[scalar] read reaching a value
            # position: the AST emits `::tpy::deref_optional_check(p)` (unproven,
            # panics on None) -- a target-driven runtime deref this cell defers.
            # Only the NARROWED read (`(*p)`, rt already the inner scalar) routes
            # here; the None-test / truthiness / bare-pass ride their own gates.
            return note_detail("name.optval_unproven_read")
        if _value_opt_view_name(e, locals_, analyzer) is not None and \
                isinstance(unwrap_readonly(analyzer.get_expr_type(e)),
                           OptionalType):
            # The view twin of the arm above: an UN-narrowed value-repr
            # Optional[str]/Optional[bytes] read in a value position is the AST's
            # `deref_optional_check(s)` -- deferred. Only the NARROWED read
            # (`(*s)`/`(*b)`, rt the inner view) routes past here; the None-test /
            # truthiness / arg-shim ride their own gates.
            return note_detail("name.optstr_unproven_read")
        return not _union_binding_divergent(e, locals_, analyzer)
    if isinstance(e, TpyIntLiteral):
        # Only literals that emit as a bare value in any fixed-int slot. Wider
        # values need a `ull` suffix / `static_cast` that the slice's emitter
        # does not reproduce (see ExpressionGenerator._gen_int_literal_value).
        return -2**31 <= e.value <= 2**31 - 1
    if isinstance(e, TpyFloatLiteral):
        # A finite float literal renders as repr(value) in a double slot, byte
        # for byte (the AST path's _gen_float_literal_value double branch). inf/
        # nan only arise from float(...) calls, never a bare literal, but guard
        # anyway -- repr(inf)/repr(nan) are not valid C++.
        return math.isfinite(e.value)
    if isinstance(e, TpyBoolLiteral):
        return True  # True/False -> true/false; no target-type dependence
    if isinstance(e, TpyStrLiteral):
        # const char[N] via cpp_string_literal_expr -- implicitly convertible to
        # every str-slice slot (string_view AND string), so never wrapped. The
        # positions that reach here are gated by their slot checks (a str-slice
        # decl init / compare operand / call arg / print arg / return value).
        return True
    if isinstance(e, TpyBytesLiteral):
        # Unlike a str literal, the render is TARGET-dependent (owned vector vs
        # static-storage span), so every admitting position threads the owned
        # flag at lowering: decl inits/reassigns and returns key on the resolved
        # binding/return type, call args on the param slot (the gen_call_arg
        # span pin), and the target-less positions (print args, compare
        # operands) render owned -- gen_expr's default arm. Positions outside
        # that set reject the literal on their own type checks (a bytes value
        # is not a scalar / str / container).
        return True
    if isinstance(e, TpyFieldAccess):
        # Type-level enum member access (`Color.RED` -> `Color::RED`) -- the
        # sema-stamped fact, not a field read; the receiver is the enum TYPE
        # name, never a local (sema's namespace lookup already handled any
        # shadowing).
        if e.enum_member_of is not None:
            return _eligible_enum(e.enum_member_of, analyzer) is not None
        # Enum instance property read (`c.value` / `c.name`): the receiver is
        # any eligible enum-valued expr (name, member access, field read).
        if _enum_prop_wrap(e, analyzer) is not None:
            return _expr_eligible(e.obj, locals_, analyzer)
        # A scalar or Char field read off an F1-record receiver (`recv.field`,
        # value form -- the access render is type-independent, and a Char value
        # lands only in positions whose own gates admit it) or off a pointer-repr
        # Optional borrow name (proven -> `p->field` via _field_receiver_ok;
        # unproven -> `::tpy::deref_check(p).field` via _optional_checked_field).
        # The non-value field source for a borrow-local binding is handled in the
        # var-decl branch, not here -- a non-value field read is not a value
        # expression.
        ft = analyzer.get_expr_type(e)
        if not (_eligible_scalar(ft) or _eligible_char(ft)
                or _eligible_enum(ft, analyzer) is not None
                or _is_type_param_slot(ft)
                or _eligible_ptr_value(ft, analyzer)):
            return note_detail("field.result_type")
        return (_field_receiver_ok(e, locals_, analyzer)
                or _optional_checked_field(e, locals_, analyzer)
                or _field_over_subscript_ok(e, locals_, analyzer)
                or _optional_field_over_subscript_ok(e, locals_, analyzer)
                or _field_over_container_subscript_ok(e, locals_, analyzer)
                or _field_over_field_ok(e, locals_, analyzer)
                or note_detail("field.receiver_shape"))
    if isinstance(e, TpySubscript):
        # A value-result tuple subscript read `t[N]` (`std::get<N>(t)`) off an
        # eligible tuple receiver; a container subscript read `c[i]`
        # (`::tpy::__getitem__(c, i)` / bounds-safe operator[]) off a
        # list[scalar] / dict[int, scalar] receiver; a str subscript `s[i]`
        # (-> Char, same emit shapes); a bytes subscript `b[i]` (-> UInt8,
        # `::tpy::bytes_getitem`); or a str/bytes slice (`::tpy::str_slice` /
        # `::tpy::bytes_slice` views, the stepped owned variants).
        return (_tuple_subscript_value_read(e, locals_, analyzer) is not None
                or _container_subscript_value_read(e, locals_, analyzer)
                or _str_subscript_char_read(e, locals_, analyzer)
                or _bytes_subscript_read(e, locals_, analyzer)
                or _str_slice_read(e, locals_, analyzer)
                or note_detail(_subscript_read_reject(e, locals_, analyzer)))
    if isinstance(e, TpyFString):
        # An owned-str-producing `std::format(...)` / `std::string("...")`
        # expression (STORAGE form) -- composes into the S1 owned-str sinks
        # (decl init, return, print/call arg, compare operand) bare.
        return _fstring_eligible(e, locals_, analyzer)
    if isinstance(e, TpyBinOp):
        return (_binop_eligible(e, locals_, analyzer)
                or note_detail(f"binop.shape.{e.op}"
                               f"{_binop_operand_suffix(e, locals_, analyzer)}"))
    if isinstance(e, TpyUnaryOp):
        # A negated int literal (`-3`) folds to a plain literal on both paths
        # (the AST's _gen_unaryop literal-negation branch); a negated FLOAT
        # literal takes the resolved __neg__ template (`-(1.5)`), a render the
        # slice does not reproduce -> AST path.
        if _folded_neg_int_literal(e, analyzer) is not None:
            return True
        # IntEnum negation: `(-static_cast<U>(p))`, a plain underlying-int
        # value composing in any scalar sink.
        if (_enum_neg_wrap(e, analyzer) is not None
                and _expr_eligible(e.operand, locals_, analyzer)):
            return True
        return _unary_not_eligible(e, locals_, analyzer)
    if isinstance(e, TpyChainedCompare):
        return _chained_compare_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCall):
        # A same-module free-function call, a builtin scalar / slice-object
        # type-constructor call (`Int32(x)` / `basic_slice(1, 3)`, emitted via
        # its resolved __init__ @cpp_template), or an enum value lookup
        # (`E(x)` -> `::tpy::EnumUtil<E>::from_value(x)`).
        return (_call_eligible(e, locals_, analyzer)
                or _scalar_ctor_call_eligible(e, locals_, analyzer)
                or _slice_ctor_call_eligible(e, locals_, analyzer)
                or _enum_from_value_eligible(e, locals_, analyzer)
                or _cast_passthrough_eligible(e, locals_, analyzer)
                or _macro_expansion_eligible(e, locals_, analyzer))
    if isinstance(e, TpyMethodCall):
        # A value-scalar-returning container method call (`x = xs.pop()`).
        return _method_call_eligible(e, locals_, analyzer)
    if isinstance(e, TpyCoerce):
        # The literal-into-typed-slot pair, the str-family cross-type
        # coercions (position-disposed: identity passthrough vs the
        # `std::string(x)` materialization -- see _coerce_disposition), and
        # the scalar-cast template family (float widths / fixed-int widening,
        # rendered through the `{0}` wrap). Other coercions (bigint,
        # optional-wrap, Char) take emit paths the slice does not mirror.
        return (_coerce_disposition(e) is not None
                and _expr_eligible(e.expr, locals_, analyzer))
    if isinstance(e, TpyIfExpr):
        # `a if c else b` -> `((cond) ? (then) : (else))` (_gen_if_expr).
        return _if_expr_eligible(e, locals_, analyzer)
    return note_detail(expr_kind_tag(e))  # unopened expression kind

def _if_expr_eligible(e: TpyIfExpr, locals_: dict[str, TpyType],
                      analyzer) -> bool:
    """The value slice of the conditional expression: a scalar / Char / enum /
    str-family result whose arms are themselves eligible exprs and whose
    condition is an admitted truthiness shape (the same set as if/while
    conditions -- for each, gen_truthy_expr's render equals the value render
    or the enum wrap _lower_truthy mirrors). The render is then
    target-independent: _gen_if_expr threads the ternary's OWN resolved type
    into the arms (`branch_target = result_type`, the consumer's target is
    ignored), and the str mixed-arm wrap's target-type skip only fires at
    StrView-typed sinks, which sema rejects for a mixed (temporary-view)
    source. Non-value results (Optional / union / record / container: the
    ptr-lift and per-branch normalization arms) and bytes (target-threaded
    literal renders inside the arms) stay on the AST path."""
    rtype = analyzer.get_expr_type(e)
    if not (_resolved_scalar(rtype, analyzer) or _eligible_char(rtype)
            or _eligible_enum(rtype, analyzer) is not None
            or _resolved_str_value(rtype, analyzer) is not None
            or _is_string_owned(rtype)):
        return note_detail("ifexpr.result_type")
    if not _condition_eligible(e.condition, locals_, analyzer):
        return note_detail("ifexpr.cond")
    return (_expr_eligible(e.then_expr, locals_, analyzer)
            and _expr_eligible(e.else_expr, locals_, analyzer))

def _condition_eligible(cond: TpyExpr, declared: dict[str, TpyType], analyzer) -> bool:
    # An `if`/`while` condition: a bare bool local/param (`if flag:`), a scalar
    # comparison, a bool-result and/or, a bool-operand `not`, or an inline-arm
    # chained comparison. For each admitted shape the truthiness render
    # (gen_truthy_expr) equals the value render, so the emitter reuses
    # _emit_expr for conditions. A comparison/logical BinOp routes through
    # _binop_eligible so it gets the same mixed-sign / bool-operand gates as in
    # value position; an ARITH binop condition (`if a + b:`, int truthiness) is
    # excluded by the op-set check. A bool-literal condition stays on the AST
    # path (it may dead-branch-eliminate).
    # An enum-typed operand takes its truthiness wrap at lowering
    # (_lower_truthy): `if (true)` (plain) / the underlying `!= 0` test
    # (IntEnum) -- gen_truthy_expr's enum arms.
    if _enum_truthy_operand(cond, declared, analyzer):
        return True
    if isinstance(cond, TpyName):
        rt = analyzer.get_expr_type(cond)
        if (_value_opt_scalar_name(cond, declared, analyzer) is not None
                or _value_opt_view_name(cond, declared, analyzer) is not None):
            # A value-repr Optional[scalar] / Optional[view] name's truthiness is
            # `::tpy::is_truthy(p)` on the bare optional (THIROptTruthy),
            # narrowed or not: codegen's `narrowed_vars` is not populated for
            # Optional None-narrowing, so gen_truthy_expr always sees the
            # `std::optional<T>` binding.
            return True
        if cond.name in declared and rt is not None and is_bool_type(rt):
            # A narrowed value-repr Optional[bool] param reads bool here, but
            # the AST renders the narrowed read `(*p)` -- reject like the
            # value-position name arm does.
            return _unrouted_binding_read(declared.get(cond.name),
                                          analyzer) is None
        # A pointer-repr Optional borrow name's truthiness is the bare `T*`
        # (`if (p)`, gen_truthy_expr's pointer render). Only the UN-narrowed
        # read is admitted (rt still Optional): a narrowed record's truthiness
        # takes a different gen_truthy arm -> AST path.
        return (isinstance(rt, OptionalType)
                and _optional_ptr_borrow_name(cond, declared, analyzer)
                is not None)
    if isinstance(cond, TpyFieldAccess):
        # A bool field read (`if self._needs_comma:`): a bool value's
        # truthiness render IS its value render (_truthy_for_rendered's
        # primitive arm), so any admitted field read carries the condition
        # unchanged. Bool only, mirroring the name arm's scope pin: a
        # non-bool scalar field's int-truthiness stays on the AST path.
        rt = analyzer.get_expr_type(cond)
        if rt is None or not is_bool_type(rt):
            return note_detail("cond.field_nonbool")
        return (_expr_eligible(cond, declared, analyzer)
                and _witness("cond.bool_field"))
    if isinstance(cond, TpyBinOp) and cond.op in (_COMPARE_OPS | _LOGICAL_OPS
                                                  | _IS_OPS | _MEMBERSHIP_OPS):
        # A membership condition (`if n in xs:`) is bool-result, and a bool's
        # truthiness render IS its value render, so the value emit carries the
        # condition unchanged.
        return _binop_eligible(cond, declared, analyzer)
    if isinstance(cond, TpyUnaryOp):
        return _unary_not_eligible(cond, declared, analyzer)
    if isinstance(cond, TpyMethodCall):
        # A bool-result method call (`if g.is_open():` / `while r.has_next():`):
        # a bool value's truthiness render IS its value render
        # (_truthy_for_rendered's primitive arm), so the value-position
        # admission carries the condition unchanged. Bool only, mirroring the
        # name/field arms' scope pin: a non-bool result takes a truthiness
        # wrap (str `.empty()`, storage-Optional `is_truthy`) or the
        # int-implicit-conversion render -> AST path.
        rt = analyzer.get_expr_type(cond)
        if rt is None or not is_bool_type(rt):
            return note_detail("cond.method_nonbool")
        return (_method_call_eligible(cond, declared, analyzer)
                and _witness("cond.bool_method"))
    if isinstance(cond, TpyChainedCompare):
        return _chained_compare_eligible(cond, declared, analyzer)
    if isinstance(cond, TpyIfExpr):
        # A bool-result ternary condition (`while d if c else False:`): a bool
        # value's truthiness render IS its value render, so the value emit
        # carries the condition unchanged. Non-bool results (int/str
        # truthiness) stay AST, mirroring the name arm's bool pin.
        rt = analyzer.get_expr_type(cond)
        if rt is None or not is_bool_type(rt):
            return note_detail("cond.if_expr_nonbool")
        return (_if_expr_eligible(cond, declared, analyzer)
                and _witness("ifexpr.cond_pos"))
    return note_detail("cond." + expr_kind_tag(cond).removeprefix("expr."))

def _stmt_value_temps_call(e: TpyExpr, ws: _WalkState, analyzer) -> bool:
    """Re-try a DIRECT statement-value call (free or method) with the
    arg-temp rows admitted (`temps_ok`). Only the flushable statement
    positions call this -- expr stmt / var-decl init / name assign /
    scalar field write / subscript write / return, where the AST's single
    pre-statement flush point places the `__tmp_N` decls; a nested call (a
    binop operand, a print arg, another call's arg) walks `_expr_eligible`
    and never admits temps, mirroring lowering's non-propagating
    `temp_args`."""
    if isinstance(e, TpyMethodCall):
        return _method_call_eligible(e, ws.declared, analyzer, temps_ok=True,
                                     narrowed=ws.narrowed)
    return (isinstance(e, TpyCall)
            and _call_eligible(e, ws.declared, analyzer, temps_ok=True,
                               narrowed=ws.narrowed))


def _tuple_literal_ok(e: TpyExpr, slot: 'TupleType',
                      declared: dict[str, TpyType], analyzer) -> bool:
    """A value-tuple literal into a fully-targeted value-tuple slot -- the
    all-VALUE-elements `_gen_tuple_literal` path (`has_ref_elements` False,
    the spelled `std::tuple<...>{e1, e2}` render): arity matches the slot,
    every element capture is VALUE (a REF/CONST_REF capture takes the borrow
    slot machinery), and every element expr is eligible into its slot. Shared
    by the narrow decl-init / call-arg sinks (scalar / owned-str element
    slots) and the widened return sink (whose slot may carry a nested
    value-tuple or a value-`Optional[scalar]` element)."""
    if not isinstance(e, TpyTupleLiteral):
        return False
    if len(e.elements) != len(slot.element_types):
        return False
    if e.elem_capture and any(c != TupleElemCapture.VALUE
                              for c in e.elem_capture):
        return False
    return all(_tuple_literal_element_ok(x, slot.element_types[i],
                                         declared, analyzer)
               for i, x in enumerate(e.elements))

def _tuple_literal_element_ok(x: TpyExpr, slot_el: TpyType,
                              declared: dict[str, TpyType], analyzer) -> bool:
    """One tuple-literal element against its slot. A nested value-tuple slot
    requires a nested literal source (spelled recursively). A value-`Optional`
    slot admits a bare `None` (`std::nullopt`) or an eligible scalar value
    source. Every other slot (scalar / owned-str) rides `_expr_eligible`, so
    the narrow sinks keep their exact prior behaviour -- these branches only
    fire on the widened return slot's element types."""
    su = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot_el)))
    if isinstance(su, TupleType):
        return (_tuple_literal_ok(x, su, declared, analyzer)
                and _witness("ret.tuple_nested_elem"))
    if isinstance(su, OptionalType) and not su.uses_pointer_repr():
        ok = (isinstance(x, TpyNoneLiteral)
              or _expr_eligible(x, declared, analyzer))
        # An `Optional[str]` inner takes a distinct face: a view source rides the
        # `std::string(view)` wrap `_lower_container_elem` threads through the
        # Optional slot, where the scalar inner lands bare.
        if _resolved_str_value(su.inner, analyzer) is not None:
            return ok and _witness("ret.tuple_opt_str_elem")
        return ok and _witness("ret.tuple_opt_elem")
    return _expr_eligible(x, declared, analyzer)

