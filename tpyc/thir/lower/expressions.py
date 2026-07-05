"""Expression gate + lowering: _expr_eligible and _lower_expr with the
call/method/ctor/arg machinery they recurse through (one mutually
recursive family per side).
"""

from __future__ import annotations
import math
from dataclasses import field, replace
from ...parse.nodes import (
    FSTRING_CONV_NONE,
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
    TpyIntLiteral,
    TpyMethodCall,
    TpyName,
    TpyNoneLiteral,
    TpySetLiteral,
    TpySlice,
    TpyStrLiteral,
    TpySubscript,
    TpyUnaryOp,
    TpyVarDecl,
)
from ...typesys import (
    BOOL,
    CHAR,
    FLOAT,
    FloatLiteralType,
    INT32,
    IntLiteralType,
    LiteralType,
    NominalType,
    OptionalType,
    OwnType,
    ParamInfo,
    ReadonlyType,
    TpyType,
    TupleType,
    UnionType,
    ValueForm,
    VoidType,
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
    is_str_type,
    is_str_view_type,
    is_string_type,
)
from ...codegen_cpp.types import resolve_pending_container
from ...codegen_cpp.forms import LocalBinding, classify_local_binding
from ...value_category import is_rvalue_source
from ...codegen_cpp.context import (
    enum_cpp_name,
    escape_cpp_name,
    imported_free_callee_cpp,
)
from ... import qnames
from ..faces import witness as _witness
from ..fallback import expr_kind_tag, note_detail
from ...codegen_cpp.expressions import ExpressionGenerator
from ..nodes import (
    Form,
    PrintForm,
    THIRArgTemp,
    THIRBinOp,
    THIRBytesLiteral,
    THIRCall,
    THIRCharLiteral,
    THIRCoerce,
    THIRContainerLiteral,
    THIRCtorCall,
    THIREnumMember,
    THIREnumWrap,
    THIRExpr,
    THIRFieldAccess,
    THIRForEach,
    THIRFormConvert,
    THIRFString,
    THIRFStringArg,
    THIRIsNone,
    THIRLiteral,
    THIRMethodCall,
    THIRMove,
    THIRName,
    THIRNarrowedRead,
    THIROptionalPtrArg,
    THIRSelf,
    THIRStrLiteral,
    THIRStrSlice,
    THIRSubscript,
    THIRUnaryNot,
    THIRUnionArgLift,
)
from .predicates import (
    _ARITH_OPS,
    _BIGINT_INDEX_NARROW_WRAP,
    _BIGINT_LIT_COERCION,
    _BIGINT_NARROW,
    _COMPARE_OPS,
    _FLOAT32_LIT_COERCION,
    _IS_OPS,
    _LOGICAL_OPS,
    _arg_ptr_union_slot,
    _bigint_index_disposition,
    _binop_operand_casts,
    _bytes_compare_operand,
    _bytes_concat_operand,
    _bytes_name_form,
    _char_compare_operand,
    _coerce_disposition,
    _coerce_wrap,
    _const_exact_field_receiver_ok,
    _const_index,
    _container_pass_through_arg,
    _container_scalar_read,
    _ctor_arg_slot_ok,
    _eligible_char,
    _eligible_enum,
    _eligible_ptr_union,
    _eligible_scalar,
    _eligible_value_union,
    _enum_compare_pair,
    _enum_member_cpp,
    _enum_neg_wrap,
    _enum_prop_wrap,
    _enum_truthy_wrap,
    _f1_record,
    _field_over_subscript_ok,
    _field_receiver_ok,
    _folded_neg_int_literal,
    _is_borrow_form_name,
    _is_bytes_family,
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
    _optional_ptr_arg_slot,
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
    _resolve_pending_view,
    _resolved_bytes_value,
    _resolved_scalar,
    _resolved_str_value,
    _resolved_viewfam_value,
    _runtime_bigint,
    _scalar_pass_through_slot,
    _slice_object_type,
    _str_compare_operand,
    _str_concat_operand,
    _str_name_form,
    _subscript_index_and_tuple,
    _template_init_call_fi,
    _tuple_subscript_value_read,
    _union_binding_divergent,
    _union_compare_pair,
    _unwrap_lit_coerce,
    _value_union_temp_slot,
    _var_decl_type,
)
from .context import (
    _LowerCtx,
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
    that type + the elements. Admitted families mirror the receiver slice:
    `list[scalar|str]` / `Array[scalar|str, N]` / `dict[fixed-int|str,
    scalar|str]`, plus `set[scalar|str]` (decl/len/iteration only -- set has no
    `__getitem__`). Every element/key/value is an eligible scalar or str-slice
    expr; a view-form str source into an owned `std::string` slot copies via the
    per-element `THIRFormConvert` wrap (`std::string(x)`, S5 -- the
    `_wrap_for_owned_slot` mirror), everything else lands bare in the AST's
    brace-init pass-through. The `make_vector`/`make_ordered_*` move arm cannot
    fire: scalars and the str family are value types, never in
    `movable_locals` (sema's ever-owned tracking guards on non-value), so
    `_maybe_move` is inert for every admitted element. A `[0] * n` repeat
    (TpyListRepeat) and a
    nested container element stay on the AST path. The empty-literal-to-Array
    reject is defensive-only: sema errors on both routes to that shape (a bare
    `[]` is un-inferable; an `Array[T, 0]` annotation mismatches the literal),
    so only the empty LIST form (the spelled `std::vector<T>{}` emit) is
    reachable."""
    # A reassigned container local is a POINTER-LOCAL on the AST path (`a = b`
    # rebinds the alias -- `std::vector<T>* a = &__slot_N; ... a = &(b);` -- so a
    # later `a.append` mutates the aliased list, Python's rebinding semantics).
    # The plain value decl this cell emits would silently copy instead; reject
    # (hoisted / move-through conservatively ride along).
    if (stmt.name in prescan.reassigned or stmt.name in prescan.hoisted
            or stmt.name in prescan.move_through):
        return False
    init = stmt.init
    t = _var_decl_type(stmt, analyzer)
    if t is None:
        return False
    if isinstance(init, TpyDictLiteral):
        if not (is_dict(t) and _container_scalar_read(t, analyzer)):
            return False
        elems = list(init.keys) + list(init.values)
    elif isinstance(init, TpySetLiteral):
        args = getattr(t, "type_args", None)
        if not (is_set(t) and bool(args)
                and (_eligible_scalar(args[0])
                     or _owned_str_slot(args[0], analyzer))):
            return False
        elems = list(init.elements)
    elif isinstance(init, TpyArrayLiteral):
        if is_dict(t) or not _container_scalar_read(t, analyzer):
            return False
        if not init.elements and not is_list(t):
            return False
        elems = list(init.elements)
    else:
        return False
    return all(_expr_eligible(e, declared, analyzer) for e in elems)

def _container_subscript_value_read(e: TpyExpr, locals_: dict[str, TpyType],
                                    analyzer) -> bool:
    """A container subscript read `c[i]` off an in-scope container name whose
    element/value is a value scalar or a str-slice value
    (`::tpy::__getitem__(c, i)`, or the bounds-safe
    `c[static_cast<std::size_t>(i)]`). The receiver is a plain name (a non-name or
    narrowed-Optional receiver rides a later cell); the index is any eligible
    value-scalar expr, or -- for an owned-str-keyed dict -- any eligible
    str-slice expr (a literal / name / concat renders bare in the key slot; the
    static-storage pin fires only for view-typed keys, which the receiver gate
    excludes). A `readonly[container]` receiver routes too (byte-identical) --
    sema readonly-wraps only non-value elements, so a scalar element read is never
    `readonly[scalar]`; the result check is a defensive guard confirming the read
    yields a value scalar / str value (redundant with the element check today,
    robust if the container predicate later widens)."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    recv = e.obj
    if not isinstance(recv, TpyName) or recv.name not in locals_:
        return False
    ret = analyzer.get_expr_type(e)
    # locals_ (the declared binding type) rather than get_expr_type: a
    # container-literal local's use sites carry the pre-resolution
    # PendingListType (see _method_call_eligible).
    # A runtime-BigInt index takes gen_index_expr's `.to_fixed_check<int32_t>()`
    # narrow (`_narrow_bigint_index`); only the out-of-int32-range literal
    # disposition rejects.
    return (_container_scalar_read(locals_[recv.name], analyzer)
            and (_resolved_scalar(ret, analyzer)
                 or _resolved_str_value(ret, analyzer) is not None)
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
    name: `::tpy::bytes_getitem(b, i)` -- bytes' `__getitem__(Int32)` is a
    @native free-function dunder, NOT the containers' `::tpy::__getitem__`
    checked template, so the emit dispatches on the bytes receiver -- or the
    bounds-safe `b[static_cast<std::size_t>(i)]` / literal `b[0]` shared with
    the container arm. Receiver and index constraints mirror the str twin
    (`_str_subscript_char_read`): a plain name receiver, an eligible
    value-scalar index (a runtime-BigInt index narrows via
    `_narrow_bigint_index`)."""
    if not isinstance(e, TpySubscript) or e.needs_optional_runtime_check:
        return False
    if e.slice_function_info is not None:
        return False
    recv = e.obj
    if not isinstance(recv, TpyName) or recv.name not in locals_:
        return False
    if _resolved_bytes_value(locals_[recv.name], analyzer) is None:
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
    if not _const_exact_field_receiver_ok(stmt.init, declared, analyzer):
        return None
    if binding is LocalBinding.REF_ALIAS or binding is LocalBinding.POINTER:
        return binding if _f1_record(target_type, analyzer) else None
    # OPTIONAL_TO_PTR: the borrow `T*` points at the optional's inner record.
    inner = target_type.inner if isinstance(target_type, OptionalType) else None
    return binding if _f1_record(inner, analyzer) else None

def _is_record_rvalue_source(init: TpyExpr, declared: dict[str, TpyType],
                             analyzer) -> bool:
    """An F2d rebind-slot source: an rvalue call producing an F1-record (a ctor
    `Inner(...)` or a by-value record-returning call) with eligible scalar args.
    It emits as the bare `Name(args)` the two-slot init / reseat wraps. kwargs /
    star-unpack args take other emit paths and stay on the AST path."""
    if not isinstance(init, TpyCall):
        return False
    if init.kwargs or init.double_star_unpack is not None:
        return False
    if not (_f1_record(analyzer.get_expr_type(init), analyzer)
            and is_rvalue_source(analyzer, init)):
        return False
    # Exact positional arity (mirror _call_eligible): an omitted default is
    # synthesized by the AST arg emit, which the bare THIRCall does not do.
    fi = init.resolved_function_info
    if fi is None or len(init.args) != len(fi.params):
        return False
    # The ctor face shares the shape/registry core with
    # `_record_ctor_call_eligible` (same-name free-fn collision, generic /
    # native / multi-overload / special-form `__init__`, cross-module
    # qualification -- shapes whose AST emit is not the raw `Name(args)`).
    # The by-value record-returning free-call face shares `_call_eligible`'s
    # callee-shape head (linkage, literal-overload mangling, generics,
    # error_return -- shapes whose AST emit is not the bare `name(args)`).
    if fi.is_constructor:
        if not _ctor_shape_ok(init, analyzer):
            return False
    elif not _plain_free_callee_ok(init, analyzer):
        return False
    # Args must be eligible SCALARS or slot-resolved bare float literals
    # (mirror _call_eligible): a non-scalar arg (a record pointer-local /
    # Own[T]) needs the AST's `(*q)` deref or auto-move `std::move(q)`,
    # neither of which the bare THIRCall arg emit reproduces. A union slot
    # hoists a member-valued scalar into a variant temp (mirror
    # _call_eligible's `_member_valued_union_slot` guard), and an
    # `Own[scalar]` SLOT temps a bare-name arg the same way (mirror the
    # `_own_cascade_fires` guard -- `_gen_record_ctor_args` shares the
    # copy+move shape).
    return all((_eligible_scalar(analyzer.get_expr_type(a))
                and not _member_valued_union_slot(a, p.type, analyzer)
                and not _own_cascade_fires(p.type)
                and _expr_eligible(a, declared, analyzer))
               or _float_literal_pass_through_arg(a, p.type, declared, analyzer)
               for a, p in zip(init.args, fi.params))

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
            or _field_over_subscript_ok(target, declared, analyzer)):
        return False
    ftype = analyzer.get_expr_type(target)
    if _eligible_char(ftype):
        if isinstance(stmt.value, TpyStrLiteral):
            return False
    elif not (_eligible_scalar(ftype)
              or _eligible_enum(ftype, analyzer) is not None):
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
              or _field_over_subscript_ok(target, declared, analyzer)):
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

def _binop_eligible(e: TpyBinOp, locals_: dict[str, TpyType], analyzer) -> bool:
    rb = e.resolved_binop
    rt = analyzer.get_expr_type(e)
    if e.op in _ARITH_OPS:
        # Same-width arithmetic: a templated dunder, scalar result. Excludes any
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
    else:
        # in/bitwise take other emit paths, out of the slice -> AST path.
        return False
    return (_expr_eligible(e.left, locals_, analyzer)
            and _expr_eligible(e.right, locals_, analyzer))

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
    if (isinstance(ot, OptionalType)
            and _optional_ptr_borrow_name(e.operand, locals_, analyzer)
            is not None):
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
    """The eligible `len(name)` form: the builtin len over a single in-scope name of a
    builtin container type or a str-slice value (`::tpy::__len__(name)`, Int32 -- the
    runtime overloads cover std::string and std::string_view). The container/str
    restriction is load-bearing, not cosmetic: a container/str is a by-ref/by-value
    param that emits as the bare name, but a record (or `Optional`) with `__len__`
    bound to a pointer-local would need `(*p)` (the AST's is_indirect_name deref) that
    the bare emit misses -- so only list/dict/set/Array/str (never pointer-locals) are
    admitted. A non-name arg (literal, subscript, call) rides a later cell."""
    if not _is_len_native(e):
        return False
    if e.kwargs or e.double_star_unpack is not None or len(e.args) != 1:
        return False
    arg = e.args[0]
    if not (isinstance(arg, TpyName) and arg.name in locals_):
        return False
    t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[arg.name])))
    return (is_list(t) or is_dict(t) or is_set(t) or is_array(t)
            or _resolved_str_value(t, analyzer) is not None
            or _resolved_bytes_value(t, analyzer) is not None  # span/vector overloads
            or is_string_type(t))  # a String local: same std::string overload

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
    if (e.call_type is not None or e.type_args or e.inferred_type_args
            or e.enum_from_value is not None or e.cast_target_type is not None
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
    if (fi.type_params or fi.is_method or fi.is_staticmethod or fi.is_async
            or fi.is_generator or fi.is_property_getter or fi.is_property_setter):
        note_detail("call.callee_kind")
        return None
    if fi.cpp_template:
        # Only a fully-substituted positional-only template expands with no
        # receiver/substitution context (the scalar-ctor cell's rule).
        if _positional_only_template(fi.cpp_template, len(e.args)):
            return ("template", fi.cpp_template)
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
    if icc is not None:
        return ("imported", icc)
    if e.func_name in analyzer.imported_names:
        # An implicitly-imported builtin-module callee: the AST's
        # conditional-qualification arm (inert today, forward-looking for
        # pure-TPy builtins) -> AST path.
        note_detail("call.imported_symbol")
        return None
    return ("plain", "")


def _plain_free_callee_ok(e: TpyCall, analyzer) -> bool:
    """The plain/imported subset of `_free_callee_kind` -- the callee-shape
    head of `_is_record_rvalue_source`'s by-value record-returning call
    face (native/template record returns stay AST there)."""
    kind = _free_callee_kind(e, analyzer)
    return kind is not None and kind[0] in ("plain", "imported")

def _call_eligible(e: TpyCall, locals_: dict[str, TpyType], analyzer,
                   *, stmt_position: bool = False,
                   container_ret_ok: bool = False,
                   temps_ok: bool = False,
                   narrowed: 'set[str] | frozenset[str]' = frozenset()) -> bool:
    if _is_len_call(e, locals_, analyzer):
        return True
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
    # the capture verdict rides `THIRForEach.iterable_lvalue`.
    ret = analyzer.get_expr_type(e)
    if not (_eligible_scalar(ret) or _eligible_char(ret)
            or _eligible_enum(ret, analyzer) is not None
            or _resolved_str_value(ret, analyzer) is not None
            or _resolved_bytes_value(ret, analyzer) is not None
            or (stmt_position and is_void_like_type(ret))
            or (container_ret_ok and _nonvalue_container_ret(ret))):
        return note_detail("call.ret_type")
    # A str-LITERAL arg to a multi-overload callee is pinned to its param's view
    # form (`std::string_view("...")`, _wants_str_literal_pin) -- the bare-literal
    # emit does not reproduce that, so the shape stays on the AST path. The AST
    # pin peels TpyCoerce wrappers, so a coerced literal must be caught too.
    # (A generic callee never pins, but fi.type_params is already rejected above.)
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
        return all(_shared_pass_through_arg(a, p.type, locals_, analyzer)
                   or note_detail("call.native_arg_shape")
                   for a, p in zip(e.args, fi.params))
    return all(_shared_pass_through_arg(a, p.type, locals_, analyzer)
               or (temps_ok and _value_union_temp_arg(a, p.type, locals_,
                                                      narrowed, analyzer))
               or (temps_ok and _record_rvalue_temp_arg(a, p.type, locals_,
                                                        analyzer))
               or (temps_ok and _own_lvalue_arg(a, p.type, locals_,
                                                narrowed, analyzer))
               or _optional_ptr_arg(a, p.type, locals_, analyzer,
                                    temps_ok=temps_ok)
               or _readonly_record_ctor_arg(a, p.type, locals_, analyzer)
               or _union_pass_through_arg(a, p.type, locals_, analyzer)
               or _union_member_lift_arg(a, p.type, locals_, analyzer)
               or _union_coerced_literal_arg(a, p.type, locals_, analyzer)
               or _own_union_ctor_arg(a, p.type, locals_, analyzer)
               or note_detail("call.arg_shape")
               for a, p in zip(e.args, fi.params))

def _shared_pass_through_arg(a: TpyExpr, ptype: 'TpyType | None',
                             locals_: dict[str, TpyType], analyzer) -> bool:
    """The arg rows whose render lives inside gen_call_arg itself --
    independent of the plain loop's pre-arms and of the dcbp/pin kwargs
    the builtins loop does not thread -- shared by the plain AND
    native/template arg loops. Their slot domains are disjoint from the
    plain-loop-only rows (arg-temps / optional-ptr / union / readonly-ctor),
    so hoisting them ahead of those rows never changes admission. A new
    arg row belongs here iff a native/template callee renders it
    identically; otherwise it goes in the plain loop only."""
    return ((_eligible_scalar(analyzer.get_expr_type(a))
             and not _member_valued_union_slot(a, ptype, analyzer)
             and not _own_cascade_fires(ptype)
             and _expr_eligible(a, locals_, analyzer))
            or _own_scalar_rvalue_arg(a, ptype, locals_, analyzer)
            or _own_record_rvalue_arg(a, ptype, locals_, analyzer)
            or _float_literal_pass_through_arg(a, ptype, locals_, analyzer)
            or _int_literal_bigint_arg(a, ptype, locals_, analyzer)
            or _str_pass_through_arg(a, ptype, locals_, analyzer)
            or _bytes_pass_through_arg(a, ptype, locals_, analyzer)
            or _char_pass_through_arg(a, ptype, locals_, analyzer)
            or _container_pass_through_arg(a, ptype, locals_, analyzer)
            or _slice_ctor_pass_through_arg(a, ptype, locals_, analyzer)
            or _enum_pass_through_arg(a, ptype, locals_, analyzer)
            or _record_pass_through_arg(a, ptype, locals_, analyzer))

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
    the ctor call's own eligibility (`Name(args)` emit shape, plain scalar
    ctor args)."""
    if _record_rvalue_temp_slot(a, ptype, analyzer) is None:
        return False
    return _record_ctor_call_eligible(a, locals_, analyzer)

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
        st = unwrap_send_sync(pt) if isinstance(pt, TpyType) else pt
        if (isinstance(st, NominalType) and is_string_type(st)
                and (mut is None or i in mut)):
            return False
        return (_str_pass_through_arg(a, p.type, locals_, analyzer)
                and _witness("ctor.str_arg"))

    return all(_arg_ok(i, a, p)
               for i, (a, p) in enumerate(zip(e.args, fi.params)))

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
    if len(e.args) != len(fi.params):  # no omitted defaults / varargs
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

def _str_pass_through_arg(a: TpyExpr, ptype: TpyType | None,
                          locals_: dict[str, TpyType], analyzer) -> bool:
    """A str-slice arg into a non-Own `str`/`StrView`/`String` param slot. A
    `str`/`StrView` param renders `std::string_view`, and every slice source
    lands in it bare: a param/view local IS a string_view, an owned local
    converts implicitly, a literal is const char[N]. A `String` slot takes
    String values bare and coerced str/StrView sources through the coerce
    arm. An `Own[...]` slot materializes an owned copy the bare emit does not
    reproduce (the gen_call_arg auto-move cascade) -> AST path."""
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
        return ((_resolved_str_value(at, analyzer) is not None
                 or _is_string_owned(at))
                and _expr_eligible(a, locals_, analyzer))
    if not (isinstance(pt, NominalType) and (is_str_type(pt) or is_str_view_type(pt))):
        return False
    if isinstance(a, TpyStrLiteral):
        return True
    return (_resolved_str_value(analyzer.get_expr_type(a), analyzer) is not None
            and _expr_eligible(a, locals_, analyzer))

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

def _method_call_eligible(e: TpyMethodCall, locals_: dict[str, TpyType], analyzer,
                          *, stmt_position: bool = False,
                          temps_ok: bool = False,
                          narrowed: 'set[str] | frozenset[str]' = frozenset()) -> bool:
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
    statement position). Every special-emit marker (static / super /
    module-qualified / typed-dict / nested-ctor / callable-field / macro / fstr /
    deref chain / Optional runtime check) takes a different `_gen_method_call`
    path and is rejected."""
    # Nested enum value lookup `Outer.Kind(v)`: a method-call shape whose
    # receiver is the TYPE name, not a local -- checked before the
    # receiver-name pin.
    if e.is_nested_enum_constructor:
        return _nested_enum_from_value_eligible(e, locals_, analyzer)
    if not isinstance(e.obj, TpyName) or e.obj.name not in locals_:
        return note_detail("method.receiver_shape")
    if not _plain_member_call_markers_ok(e):
        return note_detail("method.marker")
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
    # Mirrors _is_len_call's locals_ lookup.
    if not _container_scalar_read(locals_[e.obj.name], analyzer):
        return _record_method_call_eligible(e, fi, locals_, analyzer,
                                            stmt_position=stmt_position,
                                            temps_ok=temps_ok,
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
            or _resolved_str_value(ret, analyzer) is not None
            or (stmt_position and (ret is None or is_void_like_type(ret)))):
        return note_detail("method.ret_type")
    # A str arg into a non-Own str-family slot passes bare, like a free-call arg
    # (`d.pop(k)` -> `::tpy::dict_pop(d, k)`; builtin-container methods never
    # take the `_wants_str_literal_pin` path -- that pin is the free-call /
    # user-record-method arg paths only). An `Own[str]` slot (`xs.append(s)`)
    # materializes an owned copy / a `std::move(__tmp_N)` temp the bare emit
    # does not reproduce -- `_str_pass_through_arg` rejects Own, so it stays AST.
    return all((_scalar_pass_through_slot(p.type, analyzer)
                and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
                and _expr_eligible(a, locals_, analyzer))
               or _str_pass_through_arg(a, p.type, locals_, analyzer)
               or _container_pass_through_arg(a, p.type, locals_, analyzer)
               or note_detail("method.arg_shape")
               for a, p in zip(e.args, fi.params))

def _record_method_call_eligible(e: TpyMethodCall, fi, locals_: dict[str, TpyType],
                                 analyzer, *, stmt_position: bool,
                                 temps_ok: bool = False,
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
    recv = e.obj  # a TpyName -- checked by the caller
    recv_t = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(locals_[recv.name])))
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
        return note_detail("method.overload_set")
    ret = analyzer.get_expr_type(e)
    if not (_resolved_scalar(ret, analyzer) or _eligible_char(ret)
            or _resolved_str_value(ret, analyzer) is not None
            or (stmt_position and (ret is None or is_void_like_type(ret)))):
        return note_detail("method.ret_type")
    return all((_plain_scalar_slot(p.type, analyzer)
                and _resolved_scalar(analyzer.get_expr_type(a), analyzer)
                and _expr_eligible(a, locals_, analyzer))
               or _float_literal_pass_through_arg(a, p.type, locals_, analyzer)
               or _int_literal_bigint_arg(a, p.type, locals_, analyzer)
               or _str_pass_through_arg(a, p.type, locals_, analyzer)
               or _char_pass_through_arg(a, p.type, locals_, analyzer)
               or _container_pass_through_arg(a, p.type, locals_, analyzer)
               or _record_pass_through_arg(a, p.type, locals_, analyzer)
               or _method_ctor_rvalue_arg(a, p.type, i, overloads[0],
                                          locals_, analyzer)
               or _method_value_union_arg(a, p.type, locals_, analyzer)
               or (temps_ok and _value_union_temp_arg(a, p.type, locals_,
                                                      narrowed, analyzer))
               or note_detail("method.arg_shape")
               for i, (a, p) in enumerate(zip(e.args, fi.params)))

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

def _print_arg_ok(a: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    """One print arg in the no-kwargs common-arg subset: a str/bytes literal,
    an eligible scalar (fixed-int / bool / double), a Char (streamed raw --
    gen_print's direct-output arm; Char has no int_traits, so no int8 cast),
    or a str-slice value (a str/StrView name or str-returning call -- string
    and string_view stream raw, like the AST's is_any_str_type arm)."""
    if isinstance(a, (TpyStrLiteral, TpyBytesLiteral)):
        # A bytes literal prints owned (gen_print threads no target).
        return True
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

def _fstring_arg_wrap(a: TpyExpr, analyzer) -> 'str | None | object':
    """The Python-compatible formatting wrapper for one interpolated f-string
    arg, as a positional `{0}` template (None = pass through bare) -- the
    mirrored subset of `_gen_fstring`'s per-arg table -- or `_FSTRING_INELIGIBLE`
    for any row the slice does not reproduce (user/union `__str__`,
    containers, Char). bool is checked before
    the 8-bit-int row, mirroring the AST order (bool carries 8-bit int traits
    but must format as True/False). An IntLiteral-typed arg (`f"{5}"`) resolves
    through the module default int -- fixed widths format bare like the AST's
    fall-through; a runtime BigInt takes the `.to_string()` row."""
    if isinstance(a, TpyStrLiteral):
        return None  # const char[N] formats directly
    t = analyzer.get_expr_type(a)
    if t is None:
        return _FSTRING_INELIGIBLE
    if _resolved_str_value(t, analyzer) is not None:
        return None  # string/string_view format directly
    if _is_string_owned(t):
        return None  # a concat-result std::string formats directly too
    if is_bool_type(t):
        return "::tpy::bool_to_str({0})"
    # A bare float literal (FloatLiteralType) resolves to float64 in an
    # f-string slot -- there is no Float32-typed context inside one -- so it
    # takes the same row as a concrete double. A concrete Float32 arg casts
    # up first (float_to_str takes double; _gen_fstring's float32 arm).
    if isinstance(t, FloatLiteralType) or is_float_type(t):
        if is_float32_type(t):
            return "::tpy::float_to_str(static_cast<double>({0}))"
        return "::tpy::float_to_str({0})"
    rt = resolve_int_literals(t, analyzer.ctx.default_int_for_literal)
    if is_fixed_int_type(rt):
        tr = int_traits_of(rt)
        if tr is not None and tr.bits == 8:
            return "static_cast<int>({0})"
        return None
    if is_big_int_type(rt):
        # A runtime BigInt (concrete, or an IntLiteral under a BigInt module
        # default) formats via `.to_string()` (_gen_fstring's bigint row).
        return "({0}).to_string()"
    if _eligible_enum(t, analyzer) is not None:
        return "static_cast<int>({0})"
    return _FSTRING_INELIGIBLE

def _fstring_eligible(e: TpyFString, locals_: dict[str, TpyType],
                      analyzer) -> bool:
    """An f-string in the mirrored slice: literal segments plus interpolated
    args that are themselves eligible exprs with a mirrored wrapper row.
    Conversions (`!r`/`!s`) and format specs (they change the placeholder and
    the bool row) stay on the AST path. FStr-macro f-strings never reach this
    gate: they only arise as args to `FStr`-typed params, which the call-arg
    slot pins reject."""
    for part in e.parts:
        if isinstance(part, str):
            continue
        if part.conversion != FSTRING_CONV_NONE or part.format_spec is not None:
            return note_detail("fstring.conv_or_spec")
        if not _expr_eligible(part.expr, locals_, analyzer):
            return False
        if _fstring_arg_wrap(part.expr, analyzer) is _FSTRING_INELIGIBLE:
            return note_detail("fstring.arg_wrap")
    return True

def _expr_eligible(e: TpyExpr, locals_: dict[str, TpyType], analyzer) -> bool:
    if isinstance(e, TpyName):
        # A name outside the local/param set is a module/native/cross-module
        # global: the AST path resolves it to a qualified C++ symbol, which the
        # slice does not yet materialize. Reject -> stays on the AST path.
        # A narrowing-divergent union read (declared union, member-typed read)
        # is a pre-existing AST miscompile -> AST path (see the helper).
        if e.name not in locals_:
            return note_detail("name.global_read")
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
                or _eligible_enum(ft, analyzer) is not None):
            return note_detail("field.result_type")
        return (_field_receiver_ok(e, locals_, analyzer)
                or _optional_checked_field(e, locals_, analyzer)
                or _field_over_subscript_ok(e, locals_, analyzer)
                or _optional_field_over_subscript_ok(e, locals_, analyzer)
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
                or note_detail("subscript.read_shape"))
    if isinstance(e, TpyFString):
        # An owned-str-producing `std::format(...)` / `std::string("...")`
        # expression (STORAGE form) -- composes into the S1 owned-str sinks
        # (decl init, return, print/call arg, compare operand) bare.
        return _fstring_eligible(e, locals_, analyzer)
    if isinstance(e, TpyBinOp):
        return _binop_eligible(e, locals_, analyzer) or note_detail("binop.shape")
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
                or _enum_from_value_eligible(e, locals_, analyzer))
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
    return note_detail(expr_kind_tag(e))  # unopened expression kind

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
        if cond.name in declared and rt is not None and is_bool_type(rt):
            return True
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
                                                  | _IS_OPS):
        return _binop_eligible(cond, declared, analyzer)
    if isinstance(cond, TpyUnaryOp):
        return _unary_not_eligible(cond, declared, analyzer)
    if isinstance(cond, TpyChainedCompare):
        return _chained_compare_eligible(cond, declared, analyzer)
    return note_detail("cond." + expr_kind_tag(cond).removeprefix("expr."))

def _stmt_value_temps_call(e: TpyExpr, ws: _WalkState, analyzer) -> bool:
    """Re-try a DIRECT statement-value call (free or method) with the
    arg-temp rows admitted (`temps_ok`). Only the five flushable statement
    positions call this --
    expr stmt / var-decl init / name assign / scalar field write / return,
    where the AST's single
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

def _subscript_yields_borrow_ptr(sub: TpySubscript, lc: '_LowerCtx') -> bool:
    """Mirror ExpressionGenerator._tuple_subscript_yields_borrow_ptr: `std::get<N>(t)`
    is a bare `T*` (member access `->`) iff element N is a plain non-value BORROW_REF
    pointer-repr slot read from a borrow-form tuple. An owned (`Own`) or value element
    is held by value in the tuple (`std::get` yields a `T&`, `.` access), and a storage
    `auto&&` alias receiver likewise holds its elements by value -- both take `.`."""
    res = _subscript_index_and_tuple(sub, lc.analyzer)
    if res is None:
        return False
    recv_t, idx = res
    et = recv_t.element_types[idx]
    return (et.value_form() is ValueForm.BORROW_REF
            and TupleType._element_is_pointer_repr(et)
            and isinstance(sub.obj, TpyName)
            and sub.obj.name not in lc.storage_tuple_locals)

def _subscript_result_form(sub: TpySubscript, rtype: TpyType, lc: '_LowerCtx') -> Form:
    """The form a tuple subscript result renders as. A value scalar is VALUE; a record
    element is BORROW (a `T*`/`T&`). An Optional element read off a storage-tuple alias
    is STORAGE (`std::optional<T>`, lifted by the consumer via optional_to_ptr); off a
    borrow tuple param it is already `T*` (BORROW)."""
    if not _is_borrow_form_name(rtype):
        return Form.VALUE
    inner = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(rtype)))
    if (isinstance(inner, OptionalType) and isinstance(sub.obj, TpyName)
            and sub.obj.name in lc.storage_tuple_locals):
        return Form.STORAGE
    return Form.BORROW

def _field_is_arrow(e: TpyFieldAccess, lc: '_LowerCtx') -> bool:
    """`recv->field` vs `recv.field`: a plain `T*` pointer-local (F2), a proven
    pointer-repr Optional borrow name (an Optional-ptr param / OPTIONAL_TO_PTR
    local -- both in `lc.pointers`), or the `self` receiver (a `this` pointer)
    renders `->`; a record param / `T&` alias receiver renders `.`. Decided from
    the pointer set lowering tracks plus the method receiver. (An UNPROVEN
    Optional access never reaches this -- it takes the deref_check arm.)

    A record-element tuple subscript receiver (`t[N].field`) renders `->` only when the
    element is a borrow `T*` (`_subscript_yields_borrow_ptr`): a bare-reference element
    off a borrow-form tuple param. An owned element (`std::get` yields `T&`) or a
    storage `auto&&` alias receiver reads `.`."""
    obj = e.obj
    if isinstance(obj, TpySubscript):
        return _subscript_yields_borrow_ptr(obj, lc)
    return (isinstance(obj, TpyName)
            and (obj.name in lc.pointers or obj.name == lc.self_receiver))

def _is_own_param(name: str, lc: '_LowerCtx') -> bool:
    """Whether `name` is an `Own[...]`-declared param of the function being
    lowered (incl. the own-optional shapes) -- the storage-owning binding."""
    for n, t in lc.func.params:
        if n == name:
            return (isinstance(t, TpyType)
                    and unwrap_optional_own(unwrap_readonly(t)) is not None)
    return False

def _lower_expr(e: TpyExpr, lc: '_LowerCtx', *, temp_args: bool = False) -> THIRExpr:
    # `temp_args` admits the arg-temp rows for THIS expression's args only
    # when it is a free call: set by the five flushable statement positions
    # over their direct value, never propagated into subexpressions --
    # mirroring the gate's `_stmt_value_temps_call` scope.
    analyzer = lc.analyzer
    # A container-literal local's use sites keep the pre-resolution pending type
    # on the expr (the AST path unwraps it in TypeResolver.get_resolved_type);
    # THIR nodes must carry fully-resolved types. Same for a str local's
    # PendingStrType (sema's view/owned usage resolution is final pre-lowering).
    rtype = analyzer.get_expr_type(e)
    rtype = resolve_pending_container(rtype, analyzer) or rtype
    rtype = _resolve_pending_view(rtype, analyzer) or rtype
    loc = getattr(e, "loc", None)
    if isinstance(e, TpyName):
        if e.name == lc.self_receiver:
            # The method receiver -> `this`. A borrow (pointer) receiver; only
            # ever reached as a field-access receiver (other `self` positions are
            # gated out), so its form tag is informational.
            _witness("self.this")
            return THIRSelf(result_type=rtype, form=Form.BORROW, loc=loc)
        inr = lc.inline_narrowed.get(e.name)
        if inr is not None:
            # A compound-condition read of the narrowed subject: no alias
            # exists yet, so it lowers to the structural bare-get node.
            member_cpp, is_ptr = inr
            return THIRNarrowedRead(
                result_type=rtype, variant_cpp=e.name, member_cpp=member_cpp,
                is_ptr_variant=is_ptr,
                form=Form.BORROW if _is_borrow_form_name(rtype) else Form.VALUE,
                loc=loc)
        alias = lc.narrow.narrowed.get(e.name)
        if alias is not None:
            # A U3 isinstance-narrowed read renames to the extraction alias
            # (`ctx.narrowed_vars`): a `T&` record alias (BORROW, like a
            # REF_ALIAS local) or a scalar ref (VALUE). rtype is already the
            # narrowed member -- sema retyped the read.
            return THIRName(
                result_type=rtype, name=alias,
                form=Form.BORROW if _is_borrow_form_name(rtype) else Form.VALUE,
                loc=loc)
        # A non-value name (a record param / REF_ALIAS / POINTER local used as a
        # field receiver) is a borrow; scalars are value form. A pointer-repr tuple
        # name is a borrow tuple param (`std::tuple<..., T*>`) UNLESS it is an F3
        # storage-tuple alias local (`auto&& t = ...`, which aliases storage and reads
        # as STORAGE). The tag is informational for the field-access / convert emit,
        # but kept honest so a convert source is never mislabeled. A str-slice name
        # is the exception where the tag is LOAD-BEARING: BORROW (string_view param /
        # view local) drives the owned-sink `std::string(x)` copy, STORAGE (owned
        # local) suppresses it.
        str_t = _resolved_str_value(rtype, analyzer)
        if str_t is not None:
            return THIRName(result_type=str_t, name=e.name,
                            form=_str_name_form(e.name, str_t,
                                                lc.prescan.param_names),
                            loc=loc)
        # A bytes-slice name carries the same load-bearing view/owned form tag
        # as str: BORROW (span param / view local) drives the owned-sink
        # `::tpy::bytes_copy(x)`, STORAGE (owned vector local) suppresses it.
        bytes_t = _resolved_bytes_value(rtype, analyzer)
        if bytes_t is not None:
            return THIRName(result_type=bytes_t, name=e.name,
                            form=_bytes_name_form(e.name, bytes_t,
                                                  lc.prescan.param_names),
                            loc=loc)
        if _is_string_owned(rtype):
            # A String local (a concat-result binding): an owned std::string
            # lvalue, so STORAGE -- the owned-sink copy never fires on it and
            # the tag stays honest ( _is_borrow_form_name would mislabel it).
            return THIRName(result_type=rtype, name=e.name, form=Form.STORAGE,
                            loc=loc)
        if e.name in lc.storage_tuple_locals:
            form = Form.STORAGE
        elif _is_own_param(e.name, lc):
            # An `Own[...]` param owns its storage (a by-value / rvalue-ref
            # slot): STORAGE, not a borrow of someone else's -- keeps the MIL
            # move source and the validator's storage-sink rule honest.
            form = Form.STORAGE
        else:
            form = Form.BORROW if _is_borrow_form_name(rtype) else Form.VALUE
        return THIRName(result_type=rtype, name=e.name, form=form, loc=loc)
    if isinstance(e, TpyFieldAccess):
        if e.enum_member_of is not None:
            # Type-level enum member access: `Color.RED` -> `Color::RED`
            # (gen_expr's BindingKind.ENUM arm, spelled at lowering).
            return THIREnumMember(result_type=rtype,
                                  cpp=_enum_member_cpp(e, analyzer), loc=loc)
        prop = _enum_prop_wrap(e, analyzer)
        if prop is not None:
            # `c.value`: a plain underlying-int value. `c.name`: a
            # static-storage string_view -- BORROW, so owned-str sinks
            # copy it (the S1 view->owned convert), mirroring the AST's
            # `_is_str_view_source` on the StrView-typed read.
            if e.field == "name":
                _witness("enum.name")
                return THIREnumWrap(
                    result_type=rtype, wrap=prop,
                    operand=_lower_expr(e.obj, lc), form=Form.BORROW,
                    loc=loc)
            _witness("enum.value")
            return THIREnumWrap(
                result_type=rtype, wrap=prop, operand=_lower_expr(e.obj, lc),
                loc=loc)
        if e.needs_optional_runtime_check and isinstance(e.obj,
                                                         (TpySubscript, TpyName)):
            # Unproven `Optional[record]` member access -> `deref_check(<T*>).field`.
            # A NAME receiver (an Optional-ptr param / OPTIONAL_TO_PTR local) is
            # already a bare `T*` (pointer_value_expr is the identity for it). A
            # subscript is a `T*` off a borrow tuple, or a `std::optional<T>` off a
            # storage alias lifted to `T*` via optional_to_ptr (the STORAGE-form
            # convert). Mirrors _gen_field_access's runtime-check path.
            sub = _lower_expr(e.obj, lc)
            recv = (THIRFormConvert(result_type=sub.result_type, value=sub,
                                    form=Form.BORROW, loc=loc)
                    if sub.form is Form.STORAGE else sub)
            return THIRFieldAccess(
                result_type=rtype, receiver=recv,
                field_cpp=escape_cpp_name(e.field), deref_check=True, loc=loc)
        # Scalar field read off a borrow receiver (value-form result). A plain
        # non-null `T*` pointer-local receiver renders `recv->field`; the non-value
        # field source for a borrow-local binding is built in _lower_field_source.
        return THIRFieldAccess(
            result_type=rtype,
            receiver=_lower_expr(e.obj, lc),
            field_cpp=escape_cpp_name(e.field),
            is_arrow=_field_is_arrow(e, lc),
            loc=loc,
        )
    if isinstance(e, TpySubscript):
        if e.slice_function_info is not None:
            # Str/bytes slice -> the resolved slice __getitem__'s @cpp_template
            # over a BasicSlice/Slice initializer (or a slice-typed variable
            # index rendered bare). The view result (string_view / span) is
            # BORROW -- an owned decl sink materializes it via the view->owned
            # THIRFormConvert (str: the strview_to_str coerce ->
            # `std::string(...)`; bytes: no coerce at a pending decl, the S6
            # decl-init BORROW wrap -> `::tpy::bytes_copy(...)`); the stepped /
            # slice-var owned result (std::string / std::vector<uint8_t>) is
            # STORAGE, landing bare in every sink. Absent bounds emit
            # std::nullopt.
            rt_view = _resolved_viewfam_value(rtype, analyzer)
            form = (Form.BORROW if rt_view is not None
                    and (is_str_view_type(rt_view) or is_bytes_view_type(rt_view))
                    else Form.STORAGE)
            recv = _lower_expr(e.obj, lc)
            tpl = e.slice_function_info.cpp_template
            if not isinstance(e.index, TpySlice):
                return THIRStrSlice(
                    result_type=rtype, receiver=recv, cpp_template=tpl,
                    index=_lower_expr(e.index, lc), form=form, loc=loc)
            sl = e.index

            def _bound(b: 'TpyExpr | None') -> 'THIRExpr | None':
                # `_gen_slice_bound`: a runtime-BigInt bound appends the
                # `.to_fixed_check<int32_t>()` narrow (non-literal only --
                # the gate rejects literal BigInt bounds, whose AST render
                # is ill-formed).
                if b is None:
                    return None
                lowered = _lower_expr(b, lc)
                if not _runtime_bigint(analyzer.get_expr_type(b), analyzer):
                    return lowered
                _witness("narrow.slice_bound")
                return THIRCoerce(result_type=INT32, expr=lowered,
                                  coercion_name=_BIGINT_NARROW,
                                  wrap=_BIGINT_INDEX_NARROW_WRAP, loc=loc)

            return THIRStrSlice(
                result_type=rtype,
                receiver=recv,
                cpp_template=tpl,
                lower=_bound(sl.lower),
                upper=_bound(sl.upper),
                step=_bound(sl.step),
                stepped=e.is_stepped_slice,
                form=form,
                loc=loc,
            )
        tup = _subscript_index_and_tuple(e, analyzer)
        if tup is not None:
            # Tuple subscript -> `std::get<N>(t)`. Eligibility guaranteed a const index
            # and an eligible-tuple receiver; the shared helper re-derives the
            # normalized index (negatives folded), mirroring _gen_subscript. The
            # normalized offset rides a synthesized `THIRLiteral` (only its value is
            # read, for the `std::get<N>` template arg). `form` records the result
            # shape for the consumer: a value scalar is VALUE, a record element is a
            # borrow (`T*`/`T&`), and an Optional element read off a storage-tuple alias
            # is `std::optional<T>` (STORAGE, lifted to `T*` by the consuming deref_check
            # via optional_to_ptr) -- off a borrow tuple it is already `T*` (BORROW).
            _recv_t, idx = tup
            form = _subscript_result_form(e, rtype, lc)
            return THIRSubscript(
                result_type=rtype,
                receiver=_lower_expr(e.obj, lc),
                index=THIRLiteral(result_type=analyzer.get_expr_type(e.index),
                                  value=idx, loc=loc),
                form=form,
                loc=loc,
            )
        # Container or str subscript -> the checked dunder
        # `::tpy::__getitem__(c, i)` (str's __getitem__ @cpp_template spells the
        # same) or, when sema proved the index in-bounds,
        # `c[static_cast<std::size_t>(i)]` (a literal index needs no cast). The
        # index is a value-scalar expr (a runtime-BigInt one takes the
        # `.to_fixed_check<int32_t>()` narrow, inside the bounds-safe
        # static_cast when both fire) or, for an
        # owned-str-keyed dict, a str-slice expr rendered bare in the key slot.
        # `form` is VALUE for a scalar / Char element; a str element/value read
        # (S5) carries its resolved shape -- BORROW when the read's view var
        # resolved `StrView` (the AST's `_is_str_view_source`, driving the
        # owned-sink `std::string(x)` copy), STORAGE when it resolved owned (the
        # `const std::string&` element lands in owned sinks via the implicit
        # copy ctor, bare on both paths).
        sub_str = _resolved_str_value(rtype, analyzer)
        return THIRSubscript(
            result_type=rtype,
            receiver=_lower_expr(e.obj, lc),
            index=_narrow_bigint_index(_lower_expr(e.index, lc), e.index,
                                       analyzer, loc),
            bounds_safe=e.bounds_safe,
            form=(Form.VALUE if sub_str is None
                  else Form.BORROW if is_str_view_type(sub_str)
                  else Form.STORAGE),
            loc=loc,
        )
    if isinstance(e, (TpyIntLiteral, TpyFloatLiteral, TpyBoolLiteral)):
        return THIRLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyStrLiteral):
        # const char[N] via cpp_string_literal_expr; VALUE form -- implicitly
        # convertible to both string_view and string slots, never wrapped.
        return THIRStrLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyBytesLiteral):
        # Default owned render (bytes_literal_owned / empty vector) -- the
        # target-less positions (print/compare). View-targeted sinks (view
        # decl-init/reassign, bytes/BytesView call args) rewrite the flag at
        # their own lowering sites (_retag_bytes_literal_view / _lower_call_arg).
        return THIRBytesLiteral(result_type=rtype, value=e.value, loc=loc)
    if isinstance(e, TpyFString):
        parts: list[str | THIRFStringArg] = []
        for part in e.parts:
            if isinstance(part, str):
                parts.append(part)
            else:
                wrap = _fstring_arg_wrap(part.expr, analyzer)
                assert wrap is not _FSTRING_INELIGIBLE
                parts.append(THIRFStringArg(expr=_lower_expr(part.expr, lc),
                                            wrap=wrap))
        # An owned std::string result: STORAGE form, so it lands bare in owned
        # sinks (no view->owned wrap), like an owned-str call result.
        return THIRFString(result_type=rtype, parts=tuple(parts),
                           form=Form.STORAGE, loc=loc)
    if isinstance(e, TpyBinOp):
        # and/or lower here too: sema leaves resolved_binop None for &&/||, so
        # the emit takes the bare-operator arm (`(l && r)`), matching the AST's
        # bool-result logical branch. Compare operands lower target-aware: a
        # str literal opposite a Char-typed operand renders as a char literal.
        # A str concat's String result and a bytes concat's owned `bytes`
        # result (`::tpy::bytes_concat`, std::vector<uint8_t> by value) are
        # owned rvalues (STORAGE): they land bare in every owned sink, never
        # wrapped.
        if e.op in _IS_OPS:
            # The None identity test on an Optional-ptr borrow name -- the
            # gate (`_is_none_compare_operand`) pinned the shape to exactly
            # one None literal against such a name, so the structural pick
            # here cannot drift; the node carries only the Optional operand
            # (the AST canonicalizes `None is p` to the same
            # `(p ==|!= nullptr)` render).
            operand = e.right if isinstance(e.left, TpyNoneLiteral) else e.left
            return THIRIsNone(result_type=rtype,
                              operand=_lower_expr(operand, lc),
                              negate=e.op == "is not",
                              form=Form.VALUE, loc=loc)
        if e.op in _COMPARE_OPS:
            left = _lower_char_targeted(e.left, analyzer.get_expr_type(e.right), lc)
            right = _lower_char_targeted(e.right, analyzer.get_expr_type(e.left), lc)
        else:
            # Arithmetic operands render against the resolved dunder's
            # receiver/param types (gen_expr_deref's targets) -- a float
            # literal opposite a Float32 operand takes the `f` suffix.
            lslot, rslot = _rb_operand_slots(e.resolved_binop)
            left = _slot_literal_retype(_lower_expr(e.left, lc), lslot)
            right = _slot_literal_retype(_lower_expr(e.right, lc), rslot)
        bt = _resolved_bytes_value(rtype, analyzer)
        lcast, rcast = _binop_operand_casts(e, analyzer)
        return THIRBinOp(
            result_type=rtype,
            left=left,
            op=e.op,
            right=right,
            resolved=e.resolved_binop,
            divisor_non_zero=e.divisor_non_zero,
            left_cast=lcast,
            right_cast=rcast,
            form=(Form.STORAGE if _is_string_owned(rtype)
                  or (bt is not None and is_bytes_type(bt)) else Form.VALUE),
            loc=loc,
        )
    if isinstance(e, TpyUnaryOp):
        # A negated int literal folds to a plain literal (the AST's
        # _gen_unaryop literal-negation branch renders the negated value
        # directly); otherwise only logical `not` is admitted (bool operand).
        neg = _folded_neg_int_literal(e, analyzer)
        if neg is not None:
            return THIRLiteral(result_type=rtype, value=neg, loc=loc)
        # IntEnum negation: `(-static_cast<U>(p))` (_gen_unaryop's enum arm).
        enum_neg = _enum_neg_wrap(e, analyzer)
        if enum_neg is not None:
            _witness("enum.neg")
            return THIREnumWrap(result_type=rtype, wrap=enum_neg,
                                operand=_lower_expr(e.operand, lc), loc=loc)
        # `not`: an enum operand takes its truthiness wrap under `(!(...))`;
        # bool / Optional-ptr operands lower bare (their truthiness render is
        # their value render).
        return THIRUnaryNot(result_type=rtype,
                            operand=_lower_truthy(e.operand, lc),
                            loc=loc)
    if isinstance(e, TpyChainedCompare):
        # Inline arm of _gen_chained_compare: left-fold the sema pairs with the
        # bare && (resolved None), reproducing `((a < b) && (b < c))`. Each pair
        # is a full TpyBinOp (sema-analyzed), so it lowers like any comparison.
        assert e.pairs is not None
        folded = _lower_expr(e.pairs[0], lc)
        for pair in e.pairs[1:]:
            folded = THIRBinOp(result_type=rtype, left=folded, op="&&",
                               right=_lower_expr(pair, lc), resolved=None,
                               loc=loc)
        return folded
    if isinstance(e, TpyCall):
        if e.enum_from_value is not None:
            # `E(x)` -> `::tpy::EnumUtil<E>::from_value(x)` (gen_expr's
            # enum_from_value arm). A runtime-BigInt arg takes the checked
            # `({0}).to_fixed_check<U>()` wrap over the enum's underlying
            # type (the gate keeps literal-BigInt args out). Rides THIRCall's
            # cpp_template expansion like a scalar type-constructor.
            spelled = enum_cpp_name(e.enum_from_value,
                                    analyzer.ctx.module_name)
            arg = _lower_expr(e.args[0], lc)
            if _runtime_bigint(analyzer.get_expr_type(e.args[0]), analyzer):
                _witness("narrow.enum_arg")
                einfo = enum_info_of(e.enum_from_value)
                assert einfo is not None
                u = einfo.underlying_type
                arg = THIRCoerce(result_type=u, expr=arg,
                                 coercion_name=_BIGINT_NARROW,
                                 wrap="({0})" + f".to_fixed_check<{u.to_cpp()}>()",
                                 loc=loc)
            return THIRCall(
                result_type=rtype, callee=e.func_name,
                args=(arg,),
                cpp_template=(f"::tpy::EnumUtil<{spelled}>"
                              "::from_value({0})"),
                loc=loc)
        fi = e.resolved_function_info
        if fi is not None and fi.is_constructor:
            # A same-module user-record ctor rvalue (the `Own[union]`-slot
            # arg): _gen_call's record-branch tail renders the RAW source
            # name over the (gate-restricted, plain-scalar) args. `fi` is
            # sema's synthetic constructor fi, whose params mirror the
            # resolved __init__'s.
            _witness("ctor.call")
            return THIRCtorCall(
                result_type=rtype, type_cpp=e.func_name,
                args=tuple(_lower_call_arg(a, p.type, lc)
                           for a, p in zip(e.args, fi.params)),
                form=Form.STORAGE, loc=loc)
        if fi is not None and fi.is_method and fi.name == "__init__":
            # A scalar or slice-object type-constructor call (`Int32(x)` /
            # `basic_slice(1, 3)`): the emit is the resolved __init__ overload's
            # @cpp_template expanded over the args with no receiver. Sema
            # already substituted {cpp} / class type params, and the gate
            # admitted only positional-only templates, so the stored template is
            # carried verbatim. A `None` bound in a slice-ctor's value-repr
            # `Int32 | None` slot renders `std::nullopt` (the STORAGE-form None).
            return THIRCall(
                result_type=rtype,
                callee=e.func_name,
                args=tuple(
                    THIRLiteral(result_type=p.type, value=None,
                                form=Form.STORAGE, loc=loc)
                    if isinstance(a, TpyNoneLiteral)
                    else _slot_literal_retype(_lower_expr(a, lc), p.type)
                    for a, p in zip(e.args, fi.params)),
                cpp_template=fi.cpp_template,
                loc=loc,
            )
        # A @native free-function builtin (currently `len` -> `tpy::__len__`) carries
        # its resolved symbol so the emit dispatches on it, not the source name.
        native_name = fi.native_name if _is_len_native(e) else None
        # A str/bytes-slice call result carries its C++ shape: a view-returning
        # call yields a string_view/span (BORROW -- an owned sink copies it), an
        # owned-returning call a string/vector by value (STORAGE -- lands bare).
        view_t = _resolved_str_value(rtype, analyzer)
        if view_t is None:
            view_t = _resolved_bytes_value(rtype, analyzer)
        form = (Form.VALUE if view_t is None
                else Form.BORROW if (is_str_view_type(view_t)
                                     or is_bytes_view_type(view_t))
                else Form.STORAGE)
        # Args lower against their param slots: a str literal in a Char slot
        # renders as a char literal, a bytes literal into a bytes/BytesView
        # slot takes gen_call_arg's static-span pin, a union-slot arg reads
        # the callee's deep-const verdict (`deep_const_borrow_params`, the
        # AST's `is_readonly_target`) for the const-pointee spelling. A `len`
        # call bypasses the arity gate, so fall back to slot-less lowering
        # there.
        params = (fi.params if fi is not None
                  and len(fi.params) == len(e.args) else None)
        dcbp = fi.deep_const_borrow_params if fi is not None else None
        # The callee's emit kind: the SAME classification the gate admitted
        # on (`_free_callee_kind`) -- cross-module spelling on callee_cpp,
        # a C++ @native symbol on native_name (joining the len hardcode),
        # a positional-only @cpp_template on cpp_template.
        callee_cpp = None
        cpp_template = None
        if native_name is None:
            k = _free_callee_kind(e, analyzer)
            if k is not None and k[0] == "imported":
                callee_cpp = k[1]
                _witness("call.imported")
            elif k is not None and k[0] == "native":
                native_name = k[1]
                _witness("call.native_free")
            elif k is not None and k[0] == "template":
                cpp_template = k[1]
                _witness("call.template_free")
        return THIRCall(
            result_type=rtype,
            callee=e.func_name,
            args=tuple(
                _lower_call_arg(a, params[i].type if params else None, lc,
                                temp_args=temp_args,
                                readonly_target=(params is not None
                                                 and dcbp is not None
                                                 and i in dcbp))
                for i, a in enumerate(e.args)),
            native_name=native_name,
            cpp_template=cpp_template,
            callee_cpp=callee_cpp,
            form=form,
            loc=loc,
        )
    if isinstance(e, (TpyArrayLiteral, TpySetLiteral)):
        # A container-literal decl init (the only position eligibility admits
        # it). result_type is the RESOLVED container (list vs Array already
        # decided by sema); the emit dispatches on its family. Elements lower
        # through the per-slot owned-str wrap (S5). A LIST literal's SCALAR
        # elements render target-less on the AST path (bare `{10, 20}` into
        # the vector's brace init) -- but a demoted/annotated ARRAY's and a
        # set's DO thread the element target (probe-verified: `std::array`
        # elements take the Float32 `f` suffix / `::tpy::BigInt(N)` wraps a
        # vector's elements never get), so retype keys on the RESOLVED
        # container kind, not the literal's source shape.
        args = getattr(rtype, "type_args", None)
        slot = args[0] if args else None
        retype = isinstance(e, TpySetLiteral) or is_array(rtype)
        return THIRContainerLiteral(
            result_type=rtype,
            elements=tuple(
                _lower_container_elem(x, slot, lc, retype_scalars=retype)
                for x in e.elements),
            loc=loc,
        )
    if isinstance(e, TpyDictLiteral):
        args = getattr(rtype, "type_args", None)
        kslot = args[0] if args else None
        vslot = args[1] if args and len(args) > 1 else None
        return THIRContainerLiteral(
            result_type=rtype,
            elements=tuple(_lower_container_elem(k, kslot, lc) for k in e.keys),
            values=tuple(_lower_container_elem(v, vslot, lc) for v in e.values),
            loc=loc,
        )
    if isinstance(e, TpyMethodCall):
        if e.is_nested_enum_constructor:
            # `Outer.Kind(v)` -> `::tpy::EnumUtil<Outer::Kind>::from_value(v)`
            # (_gen_method_call's nested-enum arm). Spelled via enum_cpp_name
            # like the top-level E(x) arm -- `Outer::Kind` locally, qualified
            # cross-module.
            _witness("enum.nested_from_value")
            nested_t = analyzer.registry.get_enum(e.nested_type_name)
            spelled = enum_cpp_name(nested_t, analyzer.ctx.module_name)
            return THIRCall(
                result_type=rtype, callee=e.method,
                args=(_lower_expr(e.args[0], lc),),
                cpp_template=(f"::tpy::EnumUtil<{spelled}>"
                              "::from_value({0})"),
                loc=loc)
        fi = e.resolved_function_info
        # The member name mirrors _gen_method_call's resolution: @native rename
        # over the escaped source name (the LiteralType-mangled overload form is
        # gated out). A void method call carries no resolved expr type (None);
        # normalize so the node keeps a non-None result_type.
        member = (fi.native_name if fi.native_name and not fi.native_function
                  else escape_cpp_name(e.method))
        # A str-slice result carries its C++ shape like a THIRCall's (S5): an
        # owned-str method result (`xs.pop()`, std::string by value) is STORAGE
        # and lands bare in owned sinks.
        m_str = _resolved_str_value(rtype, analyzer)
        # Args lower against their param slots like a free call's (the record
        # pointer-local `(*p)` retag); arity was gated exact, so params always
        # pair. A user-record F2 pointer-local receiver renders `->`, as does
        # the method receiver itself (`self.helper()` -> `this->helper()`).
        # `temp_args` admits only the VALUE-union temp row here (the other
        # temp rows are free-call shapes -- a method ctor rvalue INLINES).
        params = (fi.params if fi is not None
                  and len(fi.params) == len(e.args) else None)

        def _method_arg(a: TpyExpr, ptype: 'TpyType | None') -> THIRExpr:
            if temp_args:
                ut = _value_union_temp_slot(a, ptype, lc.analyzer)
                if ut is not None and not (isinstance(a, TpyName)
                                           and (a.name in lc.narrow.narrowed
                                                or a.name in lc.inline_narrowed)):
                    _witness("argtemp.value_union_method")
                    return THIRArgTemp(result_type=ut, cpp_type=ut.to_cpp(),
                                       init=_lower_expr(a, lc), form=Form.VALUE,
                                       loc=getattr(a, "loc", None))
            return _lower_call_arg(a, ptype, lc, method_arg=True)

        if isinstance(e.obj, TpyName) and e.obj.name == lc.self_receiver:
            _witness("call.self_method")
        # An unproven Optional-ptr borrow receiver takes the runtime-check
        # render (`::tpy::deref_check(p).method(args)`); the marker carve-out
        # in the gate admits it only on such a receiver. Mutually exclusive
        # with the indirect (`->`) arm -- the checked deref yields a reference.
        deref_check = e.needs_optional_runtime_check
        return THIRMethodCall(
            result_type=rtype if rtype is not None else VoidType(),
            receiver=_lower_expr(e.obj, lc),
            method_cpp=member,
            args=tuple(
                _method_arg(a, params[i].type if params else None)
                for i, a in enumerate(e.args)),
            native_function_name=fi.native_name if fi.native_function else None,
            cpp_template=fi.cpp_template,
            is_arrow=not deref_check and isinstance(e.obj, TpyName)
                     and (e.obj.name in lc.pointers
                          or e.obj.name == lc.self_receiver),
            deref_check=deref_check,
            form=(Form.VALUE if m_str is None
                  else Form.BORROW if is_str_view_type(m_str) else Form.STORAGE),
            loc=loc,
        )
    if isinstance(e, TpyCoerce):
        inner = _lower_expr(e.expr, lc)
        disp = _coerce_disposition(e)
        if disp == "materialize":
            # The cross-type view->owned copy (`std::string(x)`) IS the S1
            # view->owned form transfer -- one emit chokepoint. The coerce
            # adds only the family-internal type respelling (StrView -> str /
            # String), carried on result_type.
            return THIRFormConvert(result_type=rtype, value=inner,
                                   form=Form.STORAGE, loc=loc)
        if (e.coercion.name in (_FLOAT32_LIT_COERCION, _BIGINT_LIT_COERCION)
                and isinstance(inner, THIRLiteral)):
            # The AST forwards the coerce target into the literal render (the
            # Float32 `f` suffix / the BigInt ctor wraps); mirror by retyping
            # the literal so the emitter picks the wrapped arm. Only a literal
            # source reaches here -- any other literal-typed expr shape is
            # rejected by `_expr_eligible`.
            inner = replace(inner, result_type=rtype)
        # Identity passthrough: the node's form is the wrapped expression's
        # form -- carried honestly (not the VALUE default) so the owned-sink
        # BORROW checks read the real source shape through the coerce (e.g.
        # string_to_str wraps a STORAGE String) -- EXCEPT a view-target coerce
        # (str_to_strview / string_to_strview): its value is a view into the
        # source's buffer whatever the source's form, so it sets BORROW
        # itself (an owned sink downstream must re-copy, like any view).
        vform = (Form.BORROW
                 if rtype is not None
                 and is_str_view_type(unwrap_readonly(unwrap_ref_type(
                     unwrap_send_sync(rtype))))
                 else inner.form)
        return THIRCoerce(
            result_type=rtype,
            expr=inner,
            coercion_name=e.coercion.name,
            wrap=_coerce_wrap(e) if disp == "template" else None,
            form=vform,
            loc=loc,
        )
    raise AssertionError(f"ineligible expr reached lowering: {type(e).__name__}")

def _lower_container_elem(e: TpyExpr, slot: TpyType | None,
                          lc: '_LowerCtx', *,
                          retype_scalars: bool = True) -> THIRExpr:
    """Lower one container-literal element / dict key / dict value into its
    slot. A view-form str source (BORROW -- a string_view param/local, a slice,
    a StrView-returning call) into an owned `std::string` slot copies
    explicitly via the S1 view->owned `THIRFormConvert` (`std::string(x)`) --
    the `_wrap_for_owned_slot`/`_view_source_to_owned` chokepoint at element
    positions. A literal (VALUE, const char[N]) and an owned source (STORAGE --
    an owned local, a String local, a concat/f-string rvalue) land bare, like
    the AST's brace-init pass-through; scalar slots never wrap."""
    # `retype_scalars` mirrors whether the AST threads a scalar element
    # target: dict keys/values and set elements do (target-typed Float32/
    # BigInt literal wraps); list/Array elements do NOT (bare renders).
    el = _lower_expr(e, lc)
    if retype_scalars:
        el = _slot_literal_retype(el, slot)
    st = _resolved_str_value(slot, lc.analyzer) if slot is not None else None
    if st is not None and is_str_type(st) and el.form is Form.BORROW:
        return THIRFormConvert(result_type=st, value=el, form=Form.STORAGE,
                               loc=getattr(e, "loc", None))
    return el

def _lower_call_arg(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx',
                    *, temp_args: bool = False,
                    readonly_target: bool = False,
                    method_arg: bool = False) -> THIRExpr:
    """Lower one call argument against its param slot. A str literal into a
    Char slot renders as a target-typed char literal (gen_expr's char arm,
    via `_lower_char_targeted`); a bytes literal into a bytes/BytesView slot
    takes gen_call_arg's static-storage span pin (`::tpy::bytes_literal(...)`,
    keyed on the RAW ptype exactly like the AST); a None literal / member-typed
    record name into a pointer-variant union slot takes `_gen_union_arg`'s
    inline lift (`_lower_union_arg_lift` -- checked FIRST so a pointer-local
    member name lifts `&((*p))` rather than retagging; `readonly_target`
    threads the callee's `deep_const_borrow_params` verdict for the
    const-pointee spelling); an F2 pointer-local
    record name passed by reference derefs (`take_rec((*p))`, gen_expr_deref's
    indirect-name render -- only records become pointer-locals, so the
    membership test alone keys the retag); every other arg lowers
    position-blind."""
    if isinstance(a, TpyStrLiteral) and _eligible_char(ptype):
        return _lower_char_targeted(a, ptype, lc)
    if isinstance(ptype, TpyType) and (is_bytes_type(ptype)
                                       or is_bytes_view_type(ptype)):
        # Peel coerce wrappers exactly like gen_call_arg's span pin (the pin
        # renders the bare literal; the coercion's own codegen never runs).
        lit = _peel_coerce(a)
        if isinstance(lit, TpyBytesLiteral):
            lowered = _lower_expr(lit, lc)
            return replace(lowered, form=Form.BORROW)
    # The two arg-temp rows, admitted only when the enclosing
    # statement position flushes (`temp_args`; see _lower_expr). The record
    # row mirrors the ref-param cascade arm: the temp declares the SLOT's
    # bare `to_cpp()` (`TempState.create`'s render -- same-nominal only, so
    # no upcast Child spelling arises). The union row mirrors
    # `_gen_union_arg`'s value branch: the temp declares the slot variant
    # (`create_typed` with `types.type_to_cpp`, == `to_cpp()` on the
    # scalar-member slice). A narrowed subject reads its extraction alias
    # while the AST's `already_union` verdict renders it bare -- gate-rejected
    # (`_value_union_temp_arg`); the check here is defense in depth.
    if temp_args:
        rec_pt = _record_rvalue_temp_slot(a, ptype, lc.analyzer)
        if rec_pt is not None:
            _witness("argtemp.record_rvalue")
            return THIRArgTemp(
                result_type=rec_pt, cpp_type=rec_pt.to_cpp(),
                init=_lower_expr(a, lc), form=Form.BORROW,
                loc=getattr(a, "loc", None))
        ut = _value_union_temp_slot(a, ptype, lc.analyzer)
        if ut is not None and not (isinstance(a, TpyName)
                                   and (a.name in lc.narrow.narrowed
                                        or a.name in lc.inline_narrowed)):
            _witness("argtemp.value_union")
            return THIRArgTemp(
                result_type=ut, cpp_type=ut.to_cpp(),
                init=_lower_expr(a, lc), form=Form.VALUE,
                loc=getattr(a, "loc", None))
        # The Own-slot copy+move row: `auto __tmp_N = <arg>;` + the move wrap
        # at the arg position -- or the temp-free `std::move(name)` when the
        # name is movable at its last use (`_maybe_move` fires before the
        # copy arm on the AST path; a scalar / pointer-local / field read is
        # never movable, so it always copies). A pointer-local name derefs in
        # the temp init (`auto __tmp_N = (*p);`), like the plain record-arg
        # retag below.
        ow = _own_lvalue_temp_slot(a, ptype, lc.analyzer)
        if ow is not None and not (isinstance(a, TpyName)
                                   and (a.name in lc.narrow.narrowed
                                        or a.name in lc.inline_narrowed)):
            form = Form.VALUE if _eligible_scalar(ow) else Form.STORAGE
            lowered = _lower_expr(a, lc)
            if isinstance(a, TpyName) and a.name in lc.pointers:
                assert isinstance(lowered, THIRName)
                lowered = replace(lowered, deref=True)
            if _is_move_source(a, lc):
                _witness("move.own_last_use")
                return THIRMove(result_type=ow, value=lowered, form=form,
                                loc=getattr(a, "loc", None))
            _witness("argtemp.own_copy")
            return THIRArgTemp(result_type=ow, init=lowered, move=True,
                               form=form, loc=getattr(a, "loc", None))
    lift = _lower_union_arg_lift(a, ptype, lc, readonly_target=readonly_target)
    if lift is not None:
        return lift
    # The pointer-repr Optional slot faces (must run BEFORE the pointer-local
    # deref retag: an already-pointer name passes BARE into the `T*` slot).
    # A narrowed subject is NOT skipped: its read renames to the extraction
    # alias inside _lower_expr and the 'name' face's `&(...)` wrap mirrors
    # the AST's `&(__u)` render (see _optional_ptr_arg).
    opt_face = _optional_ptr_arg_face(a, ptype, lc.analyzer)
    if opt_face is not None:
        ot = _optional_ptr_arg_slot(ptype, lc.analyzer)
        loc = getattr(a, "loc", None)
        if opt_face == 'none':
            _witness("optptr.none")
            return THIROptionalPtrArg(result_type=ot, form=Form.BORROW, loc=loc)
        if opt_face == 'ctor':
            # The gate admits the ctor face only under temps_ok, so a
            # non-flushable position can never reach here -- a silent
            # fall-through would render the bare (un-addressed) ctor.
            assert temp_args, "optional-ptr ctor face outside a flush position"
            inner = unwrap_readonly(ot.inner)
            _witness("optptr.ctor_rvalue")
            return THIRArgTemp(result_type=inner, cpp_type=inner.to_cpp(),
                               init=_lower_expr(a, lc), addr_of=True,
                               form=Form.BORROW, loc=loc)
        if opt_face == 'lift':
            _witness("optptr.lift")
            return THIROptionalPtrArg(result_type=ot, form=Form.BORROW,
                                      value=_lower_field_source(a, lc),
                                      lift=True, loc=loc)
        elif opt_face == 'pass' or (isinstance(a, TpyName)
                                    and a.name in lc.pointers):
            _witness("optptr.pass")
            return _lower_expr(a, lc)  # already `T*` -- bare, no deref retag
        else:  # 'name': a plain record lvalue takes the address-of
            _witness("optptr.name")
            return THIROptionalPtrArg(result_type=ot, form=Form.BORROW,
                                      value=_lower_expr(a, lc), addr_of=True,
                                      loc=loc)
    if isinstance(a, TpyName) and a.name in lc.pointers:
        lowered = _lower_expr(a, lc)
        assert isinstance(lowered, THIRName)
        return replace(lowered, deref=True)
    # A float literal into a Float32 (or Own[Float32]) slot renders with the
    # `f` suffix, and an int literal into a FREE-call BigInt slot takes the
    # ctor wrap -- gen_call_arg threads the param type into the render. A
    # METHOD arg's int literal stays BARE: gen_call_from_fi's
    # `_convert_to_fixed_int_arg` emits IntLiterals as plain C++ integers
    # (`items.push_back(2)` -- BigInt's implicit int ctor absorbs it).
    lowered = _lower_expr(a, lc)
    if (method_arg and isinstance(lowered, THIRLiteral)
            and isinstance(lowered.value, (int, float))
            and not isinstance(lowered.value, bool)):
        # Both numeric families: a method arg's int literal must not take
        # the BigInt ctor wrap AND its float literal must not take the
        # Float32 `f` suffix -- the method path renders literals target-less.
        return lowered
    return _slot_literal_retype(lowered, ptype)

def _lower_union_arg_lift(a: TpyExpr, ptype: 'TpyType | None', lc: '_LowerCtx',
                          *, readonly_target: bool = False,
                          ) -> 'THIRUnionArgLift | None':
    """The pointer-variant union-slot arg lift, or None when the arg renders
    bare. Mirrors `_gen_union_arg`'s dispatch over the gate-admitted shapes:
    a None literal is the monostate member; a member-typed name lifts
    `pv{&(name)}` with the indirect deref for a pointer-local / `self`
    receiver (`&((*p))` / `&((*this))`, gen_expr_deref's render); an
    already-union name into a DEEP-CONST slot (a `readonly[...]` annotation
    or `readonly_target`, the threaded `deep_const_borrow_params` verdict)
    takes the explicit `ptr_variant_to_const` wrap -- and a deep-const slot
    spells the const-pointee variant throughout. A narrowed
    subject's C++ binding is still the variant (`already_union` via the
    declared type), so the AST falls to the default render -- the bare
    extraction alias, the `is_narrowed` wrap skip -- which the plain
    `_lower_expr` read reproduces; a same-union name into a MUTABLE slot
    renders bare the same way."""
    slot = _arg_ptr_union_slot(ptype, lc.analyzer, readonly_target=readonly_target)
    if slot is None:
        return None
    ut, deep_const = slot
    variant_cpp = (ut.to_cpp_const_ptr_variant() if deep_const
                   else ut.to_cpp_ptr_variant())
    loc = getattr(a, "loc", None)
    if isinstance(a, TpyNoneLiteral):
        _witness("unionlift.none")
        return THIRUnionArgLift(result_type=ut, variant_cpp=variant_cpp,
                                form=Form.BORROW, loc=loc)
    if (not isinstance(a, TpyName) or a.name in lc.narrow.narrowed
            or a.name in lc.inline_narrowed):
        return None
    at = lc.analyzer.get_expr_type(a)
    at = (unwrap_readonly(unwrap_ref_type(unwrap_send_sync(at)))
          if at is not None else None)
    if not any(at == m for m in ut.members if not is_void_like_type(m)):
        if deep_const and at == ut:
            _witness("unionlift.const_wrap")
            return THIRUnionArgLift(result_type=ut, variant_cpp=variant_cpp,
                                    value=_lower_expr(a, lc), const_wrap=True,
                                    form=Form.BORROW, loc=loc)
        return None
    _witness("unionlift.member")
    return THIRUnionArgLift(
        result_type=ut, variant_cpp=variant_cpp,
        value=_lower_expr(a, lc),
        deref=a.name in lc.pointers or a.name == lc.self_receiver,
        form=Form.BORROW, loc=loc)

def _flush_witness(pos: str, value: THIRExpr) -> THIRExpr:
    """Witness a flushable statement position whose lowered value actually
    hoists an arg temp (`__tmp_N` decls land at this statement's flush
    point). Temps only ever sit in the DIRECT args of the position's call
    (temp_args never propagates into subexpressions), possibly behind a
    coerce/form-convert wrapper. Identity on `value` -- instrumentation only."""
    v = value
    while isinstance(v, (THIRCoerce, THIRFormConvert)):
        v = v.expr if isinstance(v, THIRCoerce) else v.value
    if (isinstance(v, (THIRCall, THIRMethodCall))
            and any(isinstance(x, THIRArgTemp) for x in v.args)):
        _witness(pos)
    return value

def _retag_bytes_literal_view(value: THIRExpr, target: 'TpyType | None') -> THIRExpr:
    """Rewrite a bytes literal to its static-storage span render (BORROW) when
    the sink (a view-resolved binding / a BytesView return) is view-typed --
    the AST threads the target into gen_expr's TpyBytesLiteral arm."""
    if isinstance(value, THIRBytesLiteral) and is_bytes_view_type(target):
        return replace(value, form=Form.BORROW)
    return value

def _lower_char_targeted(e: TpyExpr, target: TpyType | None,
                         lc: '_LowerCtx', *, temp_args: bool = False) -> THIRExpr:
    """Lower an expression whose slot may be Char-typed, mirroring gen_expr's
    char-literal arm: a str literal in a Char slot renders as a target-typed
    C++ char literal (`'x'`). Shared by the three positions the AST threads a
    Char target into the render -- comparison operands opposite a Char-typed
    value (`_comparison_targets`' char arm), Char-annotated decl inits, and
    call args into Char param slots. The gates admitted the literal only
    single-char; the other `_comparison_targets` arms (Optional narrowing)
    cannot arise -- Optional operands are gated out of the slice."""
    if isinstance(e, TpyStrLiteral) and _eligible_char(target):
        return THIRCharLiteral(result_type=CHAR, value=e.value,
                               loc=getattr(e, "loc", None))
    return _lower_expr(e, lc, temp_args=temp_args)

def _lower_truthy(e: TpyExpr, lc: '_LowerCtx') -> THIRExpr:
    """Lower a truthiness position (an if/while/assert condition, or a `not`
    operand). An enum-typed operand takes its truthiness wrap (THIREnumWrap;
    the plain-enum arm renders `true` and DROPS the operand, mirroring
    gen_truthy_expr); every other admitted shape's truthiness render equals
    its value render, so it lowers as a plain expression."""
    wrap = _enum_truthy_wrap(lc.analyzer.get_expr_type(e), lc.analyzer)
    if wrap is None:
        return _lower_expr(e, lc)
    loc = getattr(e, "loc", None)
    if wrap == "true":
        _witness("enum.truthy_plain")
        return THIREnumWrap(result_type=BOOL, wrap=wrap, operand=None, loc=loc)
    _witness("enum.truthy_int")
    return THIREnumWrap(result_type=BOOL, wrap=wrap,
                        operand=_lower_expr(e, lc), loc=loc)

def _slot_literal_retype(v: 'THIRExpr | None',
                         slot: 'TpyType | None') -> 'THIRExpr | None':
    """Mirror gen_expr's target threading for target-typed literal renders:
    a float literal against a Float32 slot takes the `f` suffix; an int
    literal against a BigInt slot takes the `::tpy::BigInt(...)` ctor wraps.
    The AST threads the slot type at decl inits/reassigns, returns,
    call/ctor args, field writes, MIL inits, container elements, and
    resolved-binop operands (the gen_expr_deref receiver/param targets) --
    comparison operands do NOT thread it (the compare block renders literal
    operands bare; a fixed-int/double context absorbs them). Applied
    post-lowering: only a float/int THIRLiteral is retyped, every other node
    passes through."""
    if slot is None or not isinstance(slot, TpyType):
        return v
    if not isinstance(v, THIRLiteral):
        return v
    st = unwrap_readonly(unwrap_ref_type(unwrap_send_sync(slot)))
    if isinstance(st, OwnType):
        st = unwrap_readonly(st.wrapped)
    if isinstance(v.value, float) and is_float32_type(st):
        return replace(v, result_type=st)
    if (isinstance(v.value, int) and not isinstance(v.value, bool)
            and is_big_int_type(st)):
        return replace(v, result_type=st)
    return v

def _rb_operand_slots(rb) -> 'tuple[TpyType | None, TpyType | None]':
    """The (left, right) render targets of a resolved ARITHMETIC binop -- the
    receiver/param types gen_expr_deref threads into the operand renders
    (forward: left={self}, right={0}; reverse swapped). Comparison operands
    never take these (the AST compare block renders them target-less)."""
    if rb is None or rb.method is None:
        return (None, None)
    param = rb.method.params[0].type if rb.method.params else None
    recv = rb.receiver_type
    return (param, recv) if rb.is_reverse else (recv, param)

def _lower_field_source(e: TpyFieldAccess, lc: '_LowerCtx') -> THIRFieldAccess:
    """The storage-form field read backing a borrow-local binding or an F3 tuple
    lift: `recv.field` where the field is a record (REF_ALIAS / POINTER), a
    storage-form `optional<T>` (OPTIONAL_TO_PTR), or a storage-form tuple (the F3
    `auto&&` alias decl + the borrow-tuple return source). form=STORAGE -- the bridge
    to borrow form is the `T&` reference bind (REF_ALIAS), the `auto&&` alias, or the
    wrapping THIRFormConvert (`&(...)` for POINTER, `optional_to_ptr` / `tuple_to_pointer`
    for the lifts). The receiver itself may be a pointer-local (a chained borrow), so
    `->` vs `.` is decided the same way as a value read."""
    return THIRFieldAccess(
        result_type=lc.analyzer.get_expr_type(e),
        receiver=_lower_expr(e.obj, lc),
        field_cpp=escape_cpp_name(e.field),
        is_arrow=_field_is_arrow(e, lc),
        form=Form.STORAGE,
        loc=getattr(e, "loc", None),
    )

        # Param names live on `prescan.param_names` (the single copy): a
        # `str`-typed PARAM name is a `std::string_view` in the C++ signature
        # while an owned str LOCAL of the same resolved type is a `std::string`
        # -- the str name-form classifier needs the distinction (see
        # _str_name_form), and the aug-append gate excludes params the same way
        # (see _str_aug_append_ok).


def _is_move_source(value: TpyExpr, lc: _LowerCtx,
                    movable_names: 'set[str] | None' = None) -> bool:
    """Whether a write / return / MIL source moves rather than copies: the last use
    of a movable (owned) name. Mirrors the AST's `_is_last_use_movable(expr,
    movable_names)` (peel `TpyCoerce`; a `TpyName` in the movable set whose node is a
    last use). `movable_names` defaults to the function's `movable_locals` (the
    F2b/F2e write/return case -- only an F2d REBIND_SLOT local is owned there); the
    ctor MIL passes `own_param_names` instead (M3b-move), since no locals exist yet at
    MIL time (the MIL runs before the body) and its movable sources are the Own params."""
    names = lc.movable_locals if movable_names is None else movable_names
    inner = _peel_coerce(value)
    return (isinstance(inner, TpyName)
            and inner.name in names
            and id(inner) in lc.analyzer.ctx.all_last_uses)
